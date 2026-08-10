"""The market scorer's gates, run offline against a stub CLOB session (#15).

The scorer in the selection script (`evaluate` in `scripts/rank_markets.py`)
receives its HTTP session across the seam -- exactly as the universe fetchers
(`gamma_volume`, `gamma_spread_universe`) already do -- instead of opening
connections inside the scoring function. This file is the second half of that
contract: the gates run against a stub session that serves canned books and
refuses every other request, so a regression that reaches for a real
connection (or for a URL the test did not intend) fails loudly instead of
touching the network.

The identity / volume / horizon gates are also exercised through `evaluate`
itself here, because they are part of the same per-market decision the scorer
makes and the pure helpers alone cannot show the whole funnel.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import rank_markets                                      # noqa: E402
from scripts.rank_markets import (                                    # noqa: E402
    MAX_BOOK_SPREAD, MAX_DAYS_TO_RESOLVE, MIN_TOP3_DEPTH_USD,
    MIN_VOLUME_24H, evaluate,
)


class _StubBookResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _StubClobSession:
    """A CLOB session that serves canned books and refuses every other request.

    The refusal is the offline guarantee: the scorer's only allowed fetch is
    one book GET per token, and anything else is a test bug that must fail
    loudly rather than leak a real request.
    """

    def __init__(self, books):
        self.books = dict(books)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        tok = (params or {}).get("token_id")
        if url == "https://clob.polymarket.com/book" and tok in self.books:
            return _StubBookResponse(self.books[tok])
        raise AssertionError(f"unexpected request: url={url} params={params}")


def _good_book():
    """A two-sided book that clears every scorer gate.

    Top-3 bid depth $1,070 > the $1,000 bar; spread 2c <= 6c; mid 0.50 inside
    [0.05, 0.95]. The deep 0.46/0.54 levels sit OUTSIDE the 3.5c reward
    window, so they fund the depth gate without adding competitor score --
    the thin-touch shape the depth gate exists to admit.
    """
    return {
        "bids": [{"price": "0.49", "size": "60"},
                 {"price": "0.48", "size": "60"},
                 {"price": "0.46", "size": "2200"}],
        "asks": [{"price": "0.51", "size": "60"},
                 {"price": "0.52", "size": "60"},
                 {"price": "0.54", "size": "2200"}],
    }


def _spec(cid="c1", volume=500_000.0, end_days=7,
          question="Will Bitcoin close above $110k by Friday?",
          rewards=None, tokens=("tok-yes", "tok-no")):
    """A candidate that clears identity and, with the default book, every
    gate. `end_days` is relative to now so the horizon arithmetic never
    depends on the wall clock."""

    def _iso(days):
        return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()

    return {
        "condition_id": cid,
        "question": question,
        "market_slug": "btc-110k",
        "category": "crypto",
        "market_type": "binary",
        "market_group": "",
        "series_title": "",
        "event_title": "",
        "tokens": [{"token_id": t} for t in tokens],
        "rewards": rewards if rewards is not None
                   else {"max_spread": 3.5, "min_size": 50},
        "minimum_tick_size": 0.01,
        "end_date_iso": _iso(end_days),
    }


def _session():
    return _StubClobSession({"tok-yes": _good_book(), "tok-no": _good_book()})


# --- the seam (criterion 1) ------------------------------------------------

def test_the_scorer_uses_the_passed_session_and_opens_no_connection(monkeypatch):
    """The scorer must not create a session of its own, even if a future edit
    adds a stray `requests.get`. The monkeypatch turns any such reach into a
    hard failure; the stub proves the only requests are one book GET per
    token, in order."""
    def _no_connections(*args, **kwargs):
        raise AssertionError("the scorer opened its own connection")

    # `rank_markets.requests` IS the shared requests module, so this patches
    # module attributes process-wide -- the only way to catch a stray
    # `requests.get` inside the scorer. Safe under pytest's sequential run,
    # and monkeypatch restores both attributes at teardown.
    monkeypatch.setattr(rank_markets.requests, "Session", _no_connections)
    monkeypatch.setattr(rank_markets.requests, "get", _no_connections)

    s = _session()
    r = evaluate(s, rate=50.0, m=_spec(), volume_24h=500_000.0)
    assert r is not None and r["eligible"]
    urls = [url for url, _ in s.calls]
    toks = [params["token_id"] for _, params in s.calls]
    assert urls == ["https://clob.polymarket.com/book"] * 2
    assert toks == ["tok-yes", "tok-no"]


# --- gates that return None (no verdict) -----------------------------------

def test_a_failed_book_fetch_drops_the_market_without_a_verdict():
    s = _StubClobSession({})          # refuses every request
    assert evaluate(s, rate=50.0, m=_spec()) is None


def test_a_malformed_book_row_drops_the_market_instead_of_crashing():
    """The old parse sat outside the fetch try, so one garbage level aborted
    the whole ranking run (the exception took down every ThreadPool worker).
    parse_book skips the row and counts it; the scorer fails closed on the
    count -- a skipped competitor under-counts `theirs` and inflates our
    income share, the dangerous direction for a funding decision."""
    bad = {"bids": [{"price": "0.49", "size": "60"},
                    {"price": "garbage", "size": "60"},
                    {"price": "0.46", "size": "2200"}],
           "asks": [{"price": "0.51", "size": "60"},
                     {"price": "0.52", "size": "60"},
                     {"price": "0.54", "size": "2200"}]}
    s = _StubClobSession({"tok-yes": bad, "tok-no": _good_book()})
    assert evaluate(s, rate=50.0, m=_spec(), volume_24h=500_000.0) is None


def test_a_structural_book_failure_drops_the_market():
    """A payload that is not a dict is a fetch-shaped failure: parse_book
    raises ValueError and the scorer treats the book as unreadable."""
    s = _StubClobSession({"tok-yes": ["not", "a", "dict"],
                          "tok-no": _good_book()})
    assert evaluate(s, rate=50.0, m=_spec(), volume_24h=500_000.0) is None


def test_a_one_sided_book_is_dropped():
    s = _StubClobSession({
        "tok-yes": {"bids": [{"price": "0.49", "size": "60"}], "asks": []},
        "tok-no": _good_book(),
    })
    assert evaluate(s, rate=50.0, m=_spec()) is None


def test_a_near_settled_mid_is_dropped():
    """Outside [0.05, 0.95] the book is one-sided in practice and the
    position is mostly a bet on a near-settled outcome."""
    settled = {"bids": [{"price": "0.97", "size": "1000"}],
               "asks": [{"price": "0.99", "size": "1000"}]}
    s = _StubClobSession({"tok-yes": settled, "tok-no": settled})
    assert evaluate(s, rate=50.0, m=_spec()) is None


def test_a_market_without_two_tokens_is_dropped_without_fetching():
    s = _StubClobSession({})
    assert evaluate(s, rate=50.0, m=_spec(tokens=("tok-yes",))) is None
    assert s.calls == []


def test_a_reward_window_narrower_than_our_offset_is_dropped_without_fetching():
    """max_spread 1% -> v = 0.01 < OFFSET 0.02: there is nowhere inside the
    window to rest a quote, so there is no point reading the book."""
    s = _StubClobSession({})
    r = evaluate(s, rate=50.0,
                 m=_spec(rewards={"max_spread": 1.0, "min_size": 50}))
    assert r is None
    assert s.calls == []


# --- gates that reject with a reason (a verdict) ----------------------------

def test_the_identity_gate_rejects_without_fetching():
    s = _StubClobSession({})
    r = evaluate(s, rate=50.0, m=_spec(question="Who wins Game 1 of the BO5?"))
    assert r is not None and not r["eligible"]
    assert r["reject_reason"] == "blocked dynamic/submarket keyword"
    assert s.calls == [], "the identity gate must refuse before any fetch"


def test_the_depth_gate_rejects_a_thin_book_and_reports_its_reading():
    thin = {"bids": [{"price": "0.49", "size": "20"},
                     {"price": "0.48", "size": "20"},
                     {"price": "0.47", "size": "20"}],
            "asks": [{"price": "0.51", "size": "20"},
                     {"price": "0.52", "size": "20"},
                     {"price": "0.53", "size": "20"}]}
    s = _StubClobSession({"tok-yes": thin, "tok-no": thin})
    r = evaluate(s, rate=50.0, m=_spec())
    assert r is not None and not r["eligible"]
    assert "depth" in r["reject_reason"]
    assert f"{MIN_TOP3_DEPTH_USD:,.2f}" in r["reject_reason"]
    # The reason embeds the measured reading -- the near-miss tracker parses
    # it, so the format itself is part of the contract.
    measured = 0.49 * 20 + 0.48 * 20 + 0.47 * 20
    assert f"${measured:,.2f}" in r["reject_reason"]
    # All levels sit under the 50-share minimum, so nothing rests inside the
    # reward window: the competition reading is zero, not a guess.
    assert r["their_score"] == 0.0


def test_the_spread_gate_rejects_a_wide_book():
    wide = {"bids": [{"price": "0.40", "size": "3000"}],
            "asks": [{"price": "0.60", "size": "3000"}]}
    s = _StubClobSession({"tok-yes": wide, "tok-no": wide})
    r = evaluate(s, rate=50.0, m=_spec())
    assert r is not None and not r["eligible"]
    assert "spread" in r["reject_reason"]
    assert f"{MAX_BOOK_SPREAD:.2f}" in r["reject_reason"]


def test_the_volume_gate_rejects_a_thin_market():
    s = _session()
    r = evaluate(s, rate=50.0, m=_spec(), volume_24h=MIN_VOLUME_24H - 1)
    assert r is not None and not r["eligible"]
    assert "volume" in r["reject_reason"]


def test_the_horizon_gate_rejects_a_long_dated_market():
    s = _session()
    r = evaluate(s, rate=50.0, m=_spec(end_days=MAX_DAYS_TO_RESOLVE + 5),
                 volume_24h=500_000.0)
    assert r is not None and not r["eligible"]
    assert "horizon" in r["reject_reason"]


def test_the_reward_payout_floor_rejects_a_small_pot():
    """Below $1.50/day the venue pays exactly zero, whatever the yield looks
    like -- the reason the 2026-07-30 universe held 16 dead markets."""
    s = _session()
    r = evaluate(s, rate=5.0, m=_spec(), volume_24h=500_000.0)
    assert r is not None and not r["eligible"]
    assert "under payout floor" in r["reject_reason"]


def test_the_payout_floor_is_a_reward_rule_and_not_applied_to_spread():
    """A spread market is paid by whoever lifts the offer, so there is no
    distribution to be under -- the same small pot that the reward floor
    refuses must be admitted on the spread source."""
    s = _session()
    reward = evaluate(s, rate=5.0, m=_spec(), source="rewards",
                      volume_24h=500_000.0)
    spread = evaluate(s, rate=5.0, m=_spec(), source="spread",
                      volume_24h=500_000.0)
    assert reward is not None and not reward["eligible"]
    assert "under payout floor" in reward["reject_reason"]
    assert spread is not None and spread["eligible"]


# --- the admission path ------------------------------------------------------

def test_a_healthy_market_scores_income_and_capital_off_the_live_book():
    s = _session()
    r = evaluate(s, rate=50.0, m=_spec(), volume_24h=500_000.0)
    assert r is not None
    assert r["eligible"] is True
    assert r["reject_reason"] == ""
    assert r["source"] == "rewards"
    assert r["shares"] == 120

    # The economics, recomputed from the same formula:
    # ours  = our quote score at 120 shares, 2c from mid
    # theirs = competitor score, both sides, levels 1c and 2c from mid
    ours = ((0.035 - 0.02) / 0.035) ** 2 * 120.0
    sc_1c = ((0.035 - 0.01) / 0.035) ** 2 * 60.0
    sc_2c = ((0.035 - 0.02) / 0.035) ** 2 * 60.0
    theirs = 2.0 * (sc_1c + sc_2c)
    income = 50.0 * ours / (ours + theirs)
    # The scorer rounds income to 3 decimals before persisting it.
    assert r["est_income"] == round(income, 3)
    # capital = 120 shares x ($0.50 YES + $0.50 NO)
    assert r["est_capital"] == pytest.approx(120.0 * 1.00, rel=1e-6)
