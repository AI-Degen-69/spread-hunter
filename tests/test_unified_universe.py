"""The unified market universe: one Gamma scan, movement first, no None rows.

Three properties, each from the 2026-09-08 redesign:

  * Discovery is ONE Gamma /markets scan ordered by 24h volume. Reward state
    is NOT a filter, and the end-date range is NOT a request parameter --
    long-dated markets are fetched and refused auditably by the horizon gate.
    The bounded scan stops one boundary page past the volume floor; --full-scan
    keeps going to exhaustion.
  * `evaluate` measures the tape BEFORE fetching the two books -- a dead
    market costs one request instead of three -- and never returns None: a
    market the funnel discovered is always accounted for as a row.
  * `main` runs the retired /sampling-markets reward scan only behind
    `--legacy-rewards`.
"""
from __future__ import annotations

import json
import sys
import time as _time
from datetime import datetime, timedelta, timezone

import pytest

import scripts.filter_markets as fm
from scripts.filter_markets import (evaluate, expired_at_intake,
                                    gamma_universe, resolve_state)


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeSession:
    """Gamma pages, the /trades tape, and CLOB books, all on one session."""

    def __init__(self, pages, trades=None):
        self.pages = pages            # gamma page payloads, in order
        self.trades = trades or []
        self.gamma_calls = 0
        self.trade_calls = 0
        self.book_calls = 0
        self.gammas = []

    def get(self, url, params=None, timeout=None):
        if "gamma-api" in url:
            self.gamma_calls += 1
            self.gammas.append(params)
            if self.gamma_calls - 1 < len(self.pages):
                return _Resp(self.pages[self.gamma_calls - 1])
            return _Resp([])          # listing exhausted
        if "trades" in url:
            self.trade_calls += 1
            return _Resp(self.trades)
        self.book_calls += 1
        return _Resp({
            "bids": [{"price": "0.49", "size": "5000"}],
            "asks": [{"price": "0.51", "size": "5000"}],
        })


def _gamma_row(cid: str, vol: float, **over) -> dict:
    row = {
        "conditionId": cid,
        "question": f"Market {cid}",
        "slug": f"mkt-{cid}",
        "volume24hr": vol,
        "spread": 0.02,
        "clobTokenIds": json.dumps([f"{cid}-yes", f"{cid}-no"]),
        "enableOrderBook": True,
        "acceptingOrders": True,
        "endDate": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
    }
    row.update(over)
    return row


def _universe_candidate(cid: str) -> dict:
    """A gamma_universe-shaped row that clears identity and horizon."""
    return {
        "condition_id": cid,
        "question": "Will BTC close above 100k?",
        "market_slug": f"mkt-{cid}",
        "category": "Crypto",
        "market_type": "",
        "market_group": "",
        "series_title": "Bitcoin",
        "event_title": "Bitcoin price",
        "tokens": [{"token_id": f"{cid}-yes"}, {"token_id": f"{cid}-no"}],
        "rewards": {"max_spread": 3.5, "min_size": 50},
        "minimum_tick_size": 0.01,
        "end_date_iso": (datetime.now(timezone.utc)
                         + timedelta(days=2)).isoformat(),
        "_order_min": 5,
        "_spread": 0.02,
        "_volume_24h": 250_000.0,
        "closed": False,
        "accepting_orders": True,
    }


# --- discovery ---------------------------------------------------------------


def test_the_discovery_request_carries_no_end_date_and_orders_by_volume():
    s = _FakeSession([[]])
    gamma_universe(s, min_volume_usd=125_000.0)

    params = s.gammas[0]
    assert params["order"] == "volume24hr"
    assert params["ascending"] == "false"
    # The horizon is a scoring gate with its own rejection bucket, not a
    # request parameter: the raw population must include what it removes.
    assert "end_date_min" not in params
    assert "end_date_max" not in params


def test_reward_funded_markets_are_not_filtered_out():
    s = _FakeSession([[
        _gamma_row("a", 900_000.0),
        _gamma_row("b", 800_000.0,
                   clobRewards={"rates": [{"rewards_daily_rate": 42.0}]}),
    ]])
    universe, meta = gamma_universe(s, min_volume_usd=125_000.0)

    assert [m["condition_id"] for m in universe] == ["a", "b"]
    assert "clobRewards" not in " ".join(meta["cheap_rejects"])


def test_the_scan_stops_one_boundary_page_past_the_volume_floor():
    s = _FakeSession([
        [_gamma_row("a", 900_000.0), _gamma_row("b", 500_000.0),
         _gamma_row("z", 1_000.0)],
        # The boundary page: the near-miss tail, all sub-floor, full width.
        [_gamma_row("c", 10_000.0), _gamma_row("d", 20_000.0),
         _gamma_row("e", 30_000.0)],
    ])
    universe, meta = gamma_universe(s, min_volume_usd=125_000.0)

    assert [m["condition_id"] for m in universe] == ["a", "b"]
    assert meta["pages_fetched"] == 2
    assert meta["cheap_rejects"]["sub-floor volume"] == 4
    # The scan stopped on policy, not because the listing ended -- recorded,
    # never silent.
    assert meta["truncated"] is True


def test_exhaustion_at_the_boundary_is_not_truncation():
    s = _FakeSession([
        [_gamma_row("a", 900_000.0), _gamma_row("b", 500_000.0),
         _gamma_row("z", 1_000.0)],
        [_gamma_row("c", 10_000.0)],        # short page: the listing ended
    ])
    universe, meta = gamma_universe(s, min_volume_usd=125_000.0)

    assert [m["condition_id"] for m in universe] == ["a", "b"]
    assert meta["pages_fetched"] == 2
    assert meta["truncated"] is False


def test_a_truncated_scan_marks_every_row_it_returns():
    # Arrange - the bounded stop: one qualifying page, then the boundary page.
    s = _FakeSession([
        [_gamma_row("a", 900_000.0), _gamma_row("b", 500_000.0),
         _gamma_row("z", 1_000.0)],
        [_gamma_row("c", 10_000.0), _gamma_row("d", 20_000.0),
         _gamma_row("e", 30_000.0)],
    ])

    # Act
    universe, meta = gamma_universe(s, min_volume_usd=125_000.0)

    # Assert - the row says the listing behind it was capped, so a reader
    # holding only the row can tell a capped pass from a thin market.
    assert meta["truncated"] is True
    assert [m["fetch_truncated"] for m in universe] == [True, True]


def test_an_exhausted_scan_marks_no_row_as_truncated():
    # Arrange - the listing ran out on its own.
    s = _FakeSession([
        [_gamma_row("a", 900_000.0), _gamma_row("b", 500_000.0)],
        [_gamma_row("c", 10_000.0)],        # short page: the listing ended
    ])

    # Act
    universe, meta = gamma_universe(s, min_volume_usd=125_000.0)

    # Assert
    assert meta["truncated"] is False
    assert [m["fetch_truncated"] for m in universe] == [False, False]


def test_inverted_sort_uses_a_bounded_per_row_fallback():
    pages = [
        [_gamma_row("a", 900_000.0), _gamma_row("low-1", 1_000.0),
         _gamma_row("b", 800_000.0)],
    ]
    for i in range(2, 8):
        pages.append([
            _gamma_row(f"low-{i}", 1_000.0),
            _gamma_row(chr(ord("a") + i), 700_000.0 - i),
            _gamma_row(f"tail-{i}", 500.0),
        ])
    s = _FakeSession(pages)

    universe, meta = gamma_universe(
        s, min_volume_usd=125_000.0, max_pages=20)

    assert meta["ordering_violated"] is True
    assert meta["pages_fetched"] == fm.ORDERING_FALLBACK_PAGES
    assert meta["truncated"] is True
    assert [m["condition_id"] for m in universe] == [
        "a", "b", "c", "d", "e", "f"]
    assert s.gamma_calls == fm.ORDERING_FALLBACK_PAGES


def test_full_scan_keeps_paginating_to_exhaustion():
    s = _FakeSession([
        [_gamma_row("a", 900_000.0), _gamma_row("z", 1_000.0),
         _gamma_row("b", 500_000.0)],
        [_gamma_row("c", 10_000.0), _gamma_row("d", 20_000.0),
         _gamma_row("e", 30_000.0)],
        [],
    ])
    universe, meta = gamma_universe(s, min_volume_usd=125_000.0,
                                    full_scan=True)

    # Two non-empty pages; the third probe returned nothing -- exhaustion,
    # recorded as NOT truncated (contrast the bounded scan's policy stop).
    assert meta["pages_fetched"] == 2
    assert meta["truncated"] is False
    assert meta["ordering_violated"] is True
    assert [m["condition_id"] for m in universe] == ["a", "b"]


def test_cheap_filters_count_and_sample_rejected_rows():
    s = _FakeSession([[
        _gamma_row("a", 900_000.0, enableOrderBook=False),
        _gamma_row("b", 900_000.0, acceptingOrders=False),
        _gamma_row("c", 900_000.0, clobTokenIds="not json"),
        _gamma_row("d", 900_000.0, clobTokenIds=json.dumps(["only-one"])),
        _gamma_row("e", 900_000.0, spread=0.0),
        _gamma_row("f", 900_000.0),                    # survives
        _gamma_row("g", 900_000.0,
                   clobRewards={"rates": [{"rewards_daily_rate": 9}]}),
    ]])
    universe, meta = gamma_universe(s, min_volume_usd=125_000.0)

    assert [m["condition_id"] for m in universe] == ["f", "g"]
    assert meta["cheap_rejects"] == {
        "no order book": 1, "not accepting orders": 1,
        "unparsable clobTokenIds": 1, "not binary": 1,
        "no book spread": 1,
    }
    assert meta["cheap_examples"]["not binary"] == ["Market d"]


# --- evaluate: movement before books, rows not None --------------------------


class _BoomOnBooksSession:
    """Answers the tape; a book fetch means the gate ran too late."""

    def get(self, url, params=None, timeout=None):
        if "trades" in url:
            return _Resp([])
        raise AssertionError("evaluate fetched a book for a flat market")


def test_evaluate_refuses_a_flat_market_before_fetching_books():
    row = evaluate(_BoomOnBooksSession(), 5.0, _universe_candidate("0xflat"),
                   volume_24h=250_000.0, source="spread")

    assert row["eligible"] is False
    assert "no movement" in row["reject_reason"]
    assert row["movement_usd"] == 0.0


class _DeadBookSession:
    def get(self, url, params=None, timeout=None):
        if "trades" in url:
            return _Resp([{"timestamp": _time.time(), "price": 0.5,
                           "size": 4000.0}])
        raise OSError("book endpoint down")


def test_a_failed_book_fetch_returns_a_rejection_row_not_none():
    row = evaluate(_DeadBookSession(), 5.0, _universe_candidate("0xdead"),
                   volume_24h=250_000.0, source="spread")

    assert row is not None
    assert row["eligible"] is False
    assert row["reject_reason"].startswith("YES")
    assert "book fetch failed" in row["reject_reason"]


def test_an_eligible_row_carries_movement_and_book_stats():
    trades = [{"timestamp": _time.time(), "price": 0.5, "size": 4000.0}]
    row = evaluate(_FakeSession([], trades=trades), 5.0,
                   _universe_candidate("0xliq"),
                   volume_24h=250_000.0, source="spread")

    assert row["eligible"] is True
    assert row["source"] == "spread"
    assert row["movement_usd"] == pytest.approx(2000.0)
    assert row["yes_spread"] == 0.02
    assert row["no_spread"] == 0.02
    assert row["yes_depth_usd"] == 2450.0
    assert row["no_depth_usd"] == 2450.0


class _ZeroScoreSession:
    def get(self, url, params=None, timeout=None):
        if "trades" in url:
            return _Resp([{"timestamp": _time.time(), "price": 0.5,
                           "size": 4000.0}])
        return _Resp({
            "bids": [{"price": "0.425", "size": "5000"}],
            "asks": [{"price": "0.475", "size": "5000"}],
        })


def test_a_zero_ours_score_is_retained_as_a_rejection_row():
    market = _universe_candidate("0xzero")
    market["rewards"]["max_spread"] = 2.1
    market["minimum_tick_size"] = 0.001

    row = evaluate(_ZeroScoreSession(), 5.0, market,
                   volume_24h=250_000.0, source="spread", max_spread=0.06)

    assert row["eligible"] is False
    assert row["reject_reason"] == (
        "cannot score here without overbidding the book")
    assert row["cid"] == "0xzero"
    assert row["movement_usd"] == pytest.approx(2000.0)


def test_a_decided_mid_buckets_as_one_gate():
    assert (fm._cause("YES: decided mid 0.88 outside [0.15, 0.85]")
            == "YES decided mid")
    assert (fm._cause("NO: decided mid 0.11 outside [0.15, 0.85]")
            == "NO decided mid")


# --- the legacy flag seam ----------------------------------------------------


class _Stop(RuntimeError):
    pass


class _PagingSession:
    """Two Gamma pages (the second short), one funded legacy row, live books."""

    def __init__(self):
        self.gamma_calls = 0

    def get(self, url, params=None, timeout=None):
        if "gamma-api" in url:
            self.gamma_calls += 1
            if self.gamma_calls == 1:
                return _Resp([_gamma_row("a", 900_000.0),
                              _gamma_row("b", 500_000.0)])
            return _Resp([_gamma_row("c", 10_000.0)])   # short page: the end
        if "sampling" in url:
            return _Resp({"data": [{
                "condition_id": "0xleg",
                # A real end date: the legacy top-250 cut refuses unexpired-unknown
                # rows, and this candidate must reach the scoring pool.
                "end_date_iso": (datetime.now(timezone.utc)
                                 + timedelta(days=2)).isoformat(),
                "rewards": {"rates": [{"rewards_daily_rate": 5.0}]},
                "accepting_orders": True,
                "closed": False,
            }]})
        if "trades" in url:
            return _Resp([{"timestamp": _time.time(), "price": 0.5,
                           "size": 4000.0}])
        return _Resp({"bids": [{"price": "0.48", "size": "5000"}],
                      "asks": [{"price": "0.52", "size": "5000"}]})


def test_the_legacy_reward_scan_runs_only_behind_the_flag(monkeypatch,
                                                          tmp_path):
    # main() scores the universe, then --legacy-rewards scores the legacy
    # pool in a SECOND score_pool call. Record every call and let main run
    # to completion with RUN redirected, so the snapshot's legacy accounting
    # can be asserted too.
    monkeypatch.setattr(fm, "RUN", tmp_path)
    calls: list[list[str]] = []

    def spy(jobs, **kw):
        calls.append(sorted({j[3] for j in jobs}))
        return []

    monkeypatch.setattr(fm, "score_pool", spy)
    monkeypatch.setattr(fm.requests, "Session", _PagingSession)

    monkeypatch.setattr(sys, "argv", ["filter_markets.py"])
    fm.main()
    assert calls == [["spread"]], (
        "the retired reward scan ran without --legacy-rewards")
    snap = json.loads(
        (tmp_path / "pipeline.json").read_text(encoding="utf-8"))
    assert snap["counts"]["funded"] == 0, (
        "the reward pool must read as empty when the legacy path never ran")

    calls.clear()
    (tmp_path / "pipeline.json").unlink()
    monkeypatch.setattr(sys, "argv", ["filter_markets.py", "--legacy-rewards"])
    fm.main()
    assert len(calls) == 2
    assert calls[0] == ["spread"]
    assert calls[1] == ["rewards"], (
        "the legacy pool must be scored as rewards, with its payout floor")
    snap = json.loads(
        (tmp_path / "pipeline.json").read_text(encoding="utf-8"))
    assert snap["counts"]["funded"] == 1


@pytest.mark.parametrize(
    "failure",
    [fm.requests.Timeout("timed out"),
     fm.requests.ConnectionError("disconnected"),
     ValueError("invalid json")],
    ids=["timeout", "connection", "json"],
)
def test_unavailable_legacy_sampling_returns_an_empty_result(failure,
                                                              capsys):
    class BrokenResponse:
        def json(self):
            raise failure

    class BrokenSession:
        def get(self, url, params=None, timeout=None):
            if isinstance(failure, ValueError):
                return BrokenResponse()
            raise failure

    result = fm._legacy_reward_candidates(BrokenSession())

    assert result == ([], [], [], {})
    assert "continuing without legacy markets" in capsys.readouterr().err


def test_legacy_sampling_failure_preserves_unified_output(monkeypatch,
                                                           tmp_path):
    class UnavailableLegacySession(_PagingSession):
        def get(self, url, params=None, timeout=None):
            if "sampling" in url:
                raise fm.requests.Timeout("timed out")
            return super().get(url, params=params, timeout=timeout)

    unified_row = {
        "source": "spread", "eligible": False,
        "reject_reason": "cannot score here without overbidding the book",
        "volume_24h": 900_000.0, "cid": "a", "title": "Market a",
        "slug": "mkt-a",
    }

    def score(jobs, **kwargs):
        return [unified_row] if jobs and jobs[0][3] == "spread" else []

    monkeypatch.setattr(fm, "RUN", tmp_path)
    monkeypatch.setattr(fm, "score_pool", score)
    monkeypatch.setattr(fm.requests, "Session", UnavailableLegacySession)
    monkeypatch.setattr(sys, "argv", ["filter_markets.py", "--legacy-rewards"])

    fm.main()

    rows = json.loads(
        (tmp_path / "market_universe.json").read_text(encoding="utf-8"))["rows"]
    assert rows == [unified_row]
    pipeline = json.loads(
        (tmp_path / "pipeline.json").read_text(encoding="utf-8"))
    assert pipeline["counts"]["scored"] == 1
    assert pipeline["counts"]["funded"] == 0


# --- the universe file -------------------------------------------------------


def test_the_universe_file_records_rejections_and_discovery(tmp_path,
                                                            monkeypatch):
    monkeypatch.setattr(fm, "RUN", tmp_path)
    rows = [
        {"eligible": False,
         "reject_reason": "no movement: $0 traded in last 30m under $200 "
                          "(flat)",
         "cid": "0xr", "title": "Rejected", "slug": "rej",
         "movement_usd": 0.0, "volume_24h": 300000.0, "source": "spread"},
        {"eligible": True, "cid": "0xe", "title": "Winner", "slug": "win",
         "est_income": 1.0, "est_capital": 50.0, "return_pct_day": 2.0,
         "volume_24h": 300000.0, "source": "spread"},
    ]
    meta = {"pages_fetched": 3, "truncated": True,
            "cheap_rejects": {"not binary": 4}}

    fm._write_universe_file(rows, meta)

    snap = json.loads(
        (tmp_path / "market_universe.json").read_text(encoding="utf-8"))
    assert snap["discovery"]["truncated"] is True
    assert snap["discovery"]["cheap_rejects"]["not binary"] == 4
    assert [r["cid"] for r in snap["rows"]] == ["0xr", "0xe"]


# --- venue taxonomy extraction (issue #295) -----------------------------------


def test_null_category_with_series_title_keeps_the_series():
    s = _FakeSession([[
        _gamma_row("lol", 900_000.0, category=None, categorySlug=None,
                   events=[{"title": "LoL event",
                            "series": [{"title": "League of Legends"}]}]),
    ]])
    universe, _ = gamma_universe(s, min_volume_usd=125_000.0)

    assert [m["condition_id"] for m in universe] == ["lol"]
    assert universe[0]["category"] == ""
    assert universe[0]["venue_category"] == ""
    assert universe[0]["series_title"] == "League of Legends"


def test_market_category_slug_survives_when_category_is_null():
    s = _FakeSession([[
        _gamma_row("cry", 900_000.0, category=None, categorySlug="Crypto"),
    ]])
    universe, _ = gamma_universe(s, min_volume_usd=125_000.0)

    assert universe[0]["category"] == "Crypto"


def test_event_category_and_tags_carried_verbatim():
    s = _FakeSession([[
        _gamma_row("ev", 900_000.0, category=None, categorySlug=None,
                   tags=[{"label": "Politics", "slug": "politics"}],
                   events=[{"category": "Politics",
                            "tags": [{"label": "Elections",
                                      "slug": "elections"}],
                            "series": []}]),
    ]])
    universe, _ = gamma_universe(s, min_volume_usd=125_000.0)

    # The gate keeps reading market-level `category` (still blank here);
    # display reads `venue_category`.
    assert universe[0]["category"] == ""
    assert universe[0]["venue_category"] == "Politics"
    assert universe[0]["tags"] == ["Politics", "Elections"]


def test_event_category_never_reaches_the_identity_gate():
    """Issue #295 review: enriching the gate input would silently admit or
    reject markets, so the gate verdict for an event-category-only market
    must equal the verdict with no category at all."""
    from scoring.selector import identity_allowed

    s = _FakeSession([[
        _gamma_row("ev2", 900_000.0, category=None, categorySlug=None,
                   question="Team A vs Team B",
                   events=[{"category": "Politics", "series": []}]),
    ]])
    universe, _ = gamma_universe(s, min_volume_usd=125_000.0)
    m = universe[0]
    assert m["venue_category"] == "Politics"

    gated = identity_allowed(
        m.get("question"), m.get("market_slug"), m.get("category"),
        m.get("market_type"), m.get("market_group"),
        m.get("series_title"), m.get("event_title"))
    blank = identity_allowed(
        "Team A vs Team B", m.get("market_slug"), "", "", "", "", "")
    assert gated == blank
    assert gated[0] is False


@pytest.mark.parametrize("bad_events", [
    "nope",
    [{"series": {"title": "Dictionary series"}}],
    [{"series": "string series"}],
    [{"series": None}],
])
def test_malformed_event_shapes_never_raise(bad_events):
    s = _FakeSession([[
        _gamma_row("bad", 900_000.0, category=None, categorySlug=None,
                   events=bad_events),
    ]])
    universe, _ = gamma_universe(s, min_volume_usd=125_000.0)

    assert [m["condition_id"] for m in universe] == ["bad"]
    assert universe[0]["category"] == ""
    assert universe[0]["series_title"] == ""


def test_eligible_row_persists_tags_and_market_type():
    m = _universe_candidate("0xtags")
    m["tags"] = ["Politics", "Elections"]
    m["market_type"] = "binary"
    m["venue_category"] = "Politics"
    trades = [{"timestamp": _time.time(), "price": 0.5, "size": 4000.0}]
    row = evaluate(_FakeSession([], trades=trades), 5.0, m,
                   volume_24h=250_000.0, source="spread")

    assert row["eligible"] is True
    assert row["tags"] == ["Politics", "Elections"]
    assert row["venue_category"] == "Politics"
    assert row["market_type"] == "binary"


def test_eligible_row_keeps_a_raw_market_type_key():
    """Legacy-shaped callers carry `marketType`; the row keeps it rather
    than blanking a label the venue published."""
    m = _universe_candidate("0xraw")
    del m["market_type"]
    m["marketType"] = "binary"
    trades = [{"timestamp": _time.time(), "price": 0.5, "size": 4000.0}]
    row = evaluate(_FakeSession([], trades=trades), 5.0, m,
                   volume_24h=250_000.0, source="spread")

    assert row["market_type"] == "binary"


def test_non_string_venue_fields_read_as_blank():
    s = _FakeSession([[
        _gamma_row("weird", 900_000.0, category=123, categorySlug=None,
                   events=[{"category": ["Politics"],
                            "series": [{"title": 456}],
                            "title": {"text": "x"}}]),
    ]])
    universe, _ = gamma_universe(s, min_volume_usd=125_000.0)

    assert universe[0]["category"] == ""
    assert universe[0]["venue_category"] == ""
    assert universe[0]["series_title"] == ""
    assert universe[0]["event_title"] == ""


# --- resolve state: a started market is not a resolved one (#312) ----------------


def _live_sports_market(cid: str, end_iso: str) -> dict:
    """A main-line sports market past kickoff but still open on the venue."""
    m = _universe_candidate(cid)
    m["question"] = "Eagles vs. Bears"
    m["market_slug"] = f"nfl-phi-chi-{cid}"
    m["category"] = "Sports"
    m["market_type"] = ""
    m["market_group"] = ""
    m["series_title"] = "NFL 2026"
    m["event_title"] = "Eagles vs. Bears"
    m["end_date_iso"] = end_iso
    m["closed"] = False
    m["accepting_orders"] = True
    return m


_KICKOFF_PAST = "2026-09-29T00:15:00Z"
_TRADES = [{"timestamp": _time.time(), "price": 0.5, "size": 4000.0}]


def test_a_live_market_past_its_end_date_is_not_refused_as_resolved():
    # Arrange â€” main line, past the venue kickoff timestamp, still open.
    m = _live_sports_market("0xlive", _KICKOFF_PAST)

    # Act
    verdict, reason, end_iso = resolve_state(
        m.get("closed"), m.get("accepting_orders"), m.get("end_date_iso"))

    # Assert â€” live, and the reason says so with the timestamp.
    assert verdict is False
    assert _KICKOFF_PAST in reason
    assert end_iso == _KICKOFF_PAST


def test_a_closed_market_is_refused_as_resolved():
    # Arrange
    m = _live_sports_market("0xdone", _KICKOFF_PAST)
    m["closed"] = True

    # Act
    verdict, reason, _ = resolve_state(
        m.get("closed"), m.get("accepting_orders"), m.get("end_date_iso"))

    # Assert
    assert verdict is True
    assert "resolved" in reason


def test_an_unreadable_market_is_refused_not_assumed_live():
    # Arrange â€” venue gave us nothing to read; fail closed.
    # Act
    verdict, reason, _ = resolve_state(None, None, _KICKOFF_PAST)

    # Assert
    assert verdict is True
    assert "unreadable" in reason


def test_a_market_no_longer_accepting_orders_is_refused_as_resolved():
    # Arrange â€” closed flag off but the venue stopped taking orders.
    m = _live_sports_market("0xlocked", _KICKOFF_PAST)
    m["accepting_orders"] = False

    # Act
    verdict, reason, _ = resolve_state(
        m.get("closed"), m.get("accepting_orders"), m.get("end_date_iso"))

    # Assert -- the signal that fired is named, and it is not the closed one.
    assert verdict is True
    assert "stopped accepting orders" in reason
    assert "closed" not in reason
    assert _KICKOFF_PAST in reason


def test_a_closed_market_names_the_signal_that_closed_it():
    # Arrange - the venue's closed flag, with the date it was judged on.
    # Act
    verdict, reason, _ = resolve_state(True, True, _KICKOFF_PAST)

    # Assert - the signal and the date, still in the horizon bucket.
    assert verdict is True
    assert "market closed on the venue" in reason
    assert _KICKOFF_PAST in reason
    assert fm._cause(reason) == "horizon"


def test_a_malformed_resolution_signal_is_unreadable_not_live():
    # Arrange - venue strings, not booleans; "false" is truthy in Python.
    # Act
    verdict, reason, _ = resolve_state("false", "true", _KICKOFF_PAST)

    # Assert - fail closed, never assume live.
    assert verdict is True
    assert "unreadable" in reason


def test_an_absent_closed_flag_reaches_the_gate_as_absent():
    # Arrange - Gamma omitted `closed` on this row entirely.
    s = _FakeSession([[_gamma_row("gap", 500_000.0)]])
    universe, _ = gamma_universe(s, min_volume_usd=125_000.0)

    # Assert - the row keeps the gap instead of filling it with False, so the
    # gate refuses it instead of reading a live market.
    assert universe[0]["closed"] is None
    verdict, reason, _ = resolve_state(
        universe[0]["closed"], universe[0]["accepting_orders"],
        universe[0]["end_date_iso"])
    assert verdict is True
    assert "unreadable" in reason


def test_tradable_admits_a_live_market_past_kickoff_to_the_horizon_arm():
    # Arrange
    m = _live_sports_market("0xadm", _KICKOFF_PAST)

    # Act
    ok, reason = fm.tradable(
        250_000.0, fm.days_to_resolve(_KICKOFF_PAST), m["question"],
        m["market_slug"], m["category"], m["market_type"], m["market_group"],
        m["series_title"], m["event_title"],
        state=fm.resolve_state(m.get("closed"), m.get("accepting_orders"),
                               m.get("end_date_iso")))

    # Assert â€” not a horizon refusal; the distance arm still applies.
    assert ok is True
    assert reason == ""


def test_tradable_refuses_a_resolved_market_with_a_resolved_verdict():
    # Arrange
    m = _live_sports_market("0xres", _KICKOFF_PAST)
    m["closed"] = True

    # Act
    ok, reason = fm.tradable(
        250_000.0, fm.days_to_resolve(_KICKOFF_PAST), m["question"],
        m["market_slug"], m["category"], m["market_type"], m["market_group"],
        m["series_title"], m["event_title"],
        state=fm.resolve_state(m.get("closed"), m.get("accepting_orders"),
                               m.get("end_date_iso")))

    # Assert
    assert ok is False
    assert reason == ("resolved: market closed on the venue "
                      f"(endDate {_KICKOFF_PAST})")
    assert fm._cause(reason) == "horizon"


def test_tradable_refuses_an_unreadable_market():
    # Arrange
    m = _live_sports_market("0xunk", _KICKOFF_PAST)
    m["closed"] = None
    m["accepting_orders"] = None

    # Act
    ok, reason = fm.tradable(
        250_000.0, fm.days_to_resolve(_KICKOFF_PAST), m["question"],
        m["market_slug"], m["category"], m["market_type"], m["market_group"],
        m["series_title"], m["event_title"],
        state=fm.resolve_state(m.get("closed"), m.get("accepting_orders"),
                               m.get("end_date_iso")))

    # Assert â€” fail closed.
    assert ok is False
    assert "unreadable" in reason
    assert fm._cause(reason) == "horizon"


def test_tradable_reads_a_plain_three_tuple_state():
    # Arrange â€” callers may pass only (verdict, reason); end_iso rides along.
    m = _live_sports_market("0xplain", _KICKOFF_PAST)

    # Act
    ok, _ = fm.tradable(
        250_000.0, fm.days_to_resolve(_KICKOFF_PAST), m["question"],
        m["market_slug"], m["category"], m["market_type"], m["market_group"],
        m["series_title"], m["event_title"],
        state=(False, "open", _KICKOFF_PAST))

    # Assert
    assert ok is True


def test_tradable_refuses_a_past_market_with_no_end_date_to_audit():
    # Arrange - the negative cannot be explained, so the refusal stands.
    # Act
    ok, reason = fm.tradable(
        250_000.0, -1.0, "Eagles vs. Bears", "nfl-phi-chi", "Sports", "",
        "", "NFL 2026", "Eagles vs. Bears", state=(False, "open"))

    # Assert
    assert ok is False
    assert "endDate unknown" in reason
    assert fm._cause(reason) == "horizon"


def test_a_far_market_keeps_its_distance_refusal():
    # Arrange â€” two days out reads live but too far, whatever the venue adds.
    m = _live_sports_market(
        "0xfar",
        (datetime.now(timezone.utc) + timedelta(days=40)).isoformat())

    # Act
    ok, reason = fm.tradable(
        250_000.0, fm.days_to_resolve(m["end_date_iso"]), m["question"],
        m["market_slug"], m["category"], m["market_type"], m["market_group"],
        m["series_title"], m["event_title"],
        state=fm.resolve_state(m.get("closed"), m.get("accepting_orders"),
                               m.get("end_date_iso")))

    # Assert
    assert ok is False
    assert reason.startswith("horizon 40")
    assert fm._cause(reason) == "horizon"


def test_a_submarket_refusal_names_the_label_that_refused_it():
    # Arrange - the venue field is the line value, so the reason must carry it.
    # Act
    ok, reason = fm.identity_reason_with_value(
        "carries a submarket group label", "Spread -2.5")

    # Assert
    assert ok is False
    assert "Spread -2.5" in reason
    assert reason.startswith("carries a submarket group label")
    assert fm._cause(reason) == "carries a submarket group label"


@pytest.mark.parametrize("label", [
    "Brazil", "Argentina", "England", "San Marino",
    "Donald Trump", "Democratic Party", "Elise Stefanik",
    "New York Knicks", "Chicago White Sox",
    "December 31, 2027", "June 30, 2027", "October 31",
    "2027-12-31", "2026-10-03",
])
def test_a_bare_name_or_date_group_label_is_not_a_submarket(label):
    # Arrange - Oct 2026: the venue groups main lines under country,
    # candidate, team, and date labels. None of those names a fragment.
    # Act
    ok, reason = fm.identity_allowed(
        "Will something happen by December?", "will-something-happen",
        "", "", label, "", "")

    # Assert - admitted; the liquidity/depth/spread gates decide downstream.
    assert ok is True
    assert reason == ""


@pytest.mark.parametrize("label", [
    "Spread -3.5", "Spread -21.5", "Belarus (-2.5)", "Burkina Faso (-5.5)",
    "Game 1", "Map 2", "Round 3",
    "Alabama Total Rushing Yards: O/U 125.5",
    "Memphis Total Rushing Yards: O/U 150.5",
    "74,000", "<76,000", "65-89", "90-114",
    "\u2191 88,000", "\u2193 2,100", "\u2193 80,000",
    "Over 2.5", "Under 2.5", "Over 125.5",
])
def test_a_line_shaped_group_label_stays_refused(label):
    # Arrange - real fragments measured on the Oct 2026 rank. Game/Map/Round
    # labels trip the blocked-keyword arm first (it reads the group field
    # too); both refusals keep the market out, which is what is asserted.
    # Act
    ok, reason = fm.identity_allowed(
        "Will something happen by December?", "will-something-happen",
        "", "", label, "", "")

    # Assert
    assert ok is False
    assert reason in ("carries a submarket group label",
                      "blocked dynamic/submarket keyword")


def test_a_country_labeled_candidate_reaches_the_downstream_gates():
    # Arrange - end-to-end through evaluate: the label alone must not refuse.
    m = _universe_candidate("0xcountry")
    m["question"] = "Will Brazil win the World Cup?"
    m["market_slug"] = "will-brazil-win-the-world-cup"
    m["category"] = "Sports"
    m["market_group"] = "Brazil"
    trades = [{"timestamp": _time.time(), "price": 0.5, "size": 4000.0}]

    # Act
    row = evaluate(_FakeSession([], trades=trades), 5.0, m,
                   volume_24h=250_000.0, source="spread")

    # Assert
    assert row["eligible"] is True


def test_a_matchup_with_a_country_label_still_needs_its_series_word():
    # Arrange - scope guard: this fix narrows only the group-label veto, not
    # the primary rule. A head-to-head with no league word stays refused.
    # Act
    ok, reason = fm.identity_allowed(
        "Croatia vs England", "croatia-vs-england", "", "", "Croatia",
        "", "")

    # Assert
    assert ok is False
    assert reason == "not a primary Moneyline/Outright or Macro/Politics market"


@pytest.mark.parametrize("title,slug,series,group", [
    ("Alabama vs Mississippi State", "alabama-vs-mississippi-state", "College Football", "Alabama"),
    ("Alabama vs Mississippi State", "alabama-vs-mississippi-state", "College Football", "Mississippi State"),
    ("Croatia vs England", "croatia-vs-england", "FIFA World Cup", "Croatia"),
    ("Lakers vs Celtics", "lakers-vs-celtics", "NBA 2026", "Lakers"),
])
def test_a_sports_matchup_with_a_bare_name_group_is_admitted(title, slug, series, group):
    # Arrange / Act - sports league confirmed and non-fragment group label (team/country)
    ok, reason = fm.identity_allowed(
        title, slug, "Sports", "", group, series, "")

    # Assert - admitted to downstream liquidity gates
    assert ok is True
    assert reason == ""


@pytest.mark.parametrize("title,slug,series,group", [
    ("Colts vs. Commanders: O/U 46.5", "nfl-ind-was-2026-10-04-total-46pt5", "NFL 2026", "O/U 46.5"),
    ("Patriots vs. Bills: O/U 50.5", "nfl-ne-buf-2026-10-04-total-50pt5", "NFL 2026", "O/U 50.5"),
    ("Netherlands vs. Serbia: O/U 3.5", "unl-nld-ser-2026-10-04-total-3pt5", "Soccer", "O/U 3.5"),
    ("Packers vs. Buccaneers", "nfl-gb-tb-2026-10-04", "NFL 2026", "Spread -3.5"),
    ("Cardinals vs. Giants", "nfl-ari-nyg-2026-10-04", "NFL 2026", "Spread -2.5"),
    ("Chiefs vs. Raiders: Total 48.5", "nfl-kc-lv-2026-10-04-total-48pt5", "NFL 2026", ""),
])
def test_a_matchup_with_a_fragment_title_or_group_stays_refused(title, slug, series, group):
    # Arrange / Act - audited shapes from docs/issues/355-matchup-refusal-audit.md
    ok, reason = fm.identity_allowed(
        title, slug, "Sports", "", group, series, "")

    # Assert - stays refused with exact refusal string
    assert ok is False
    assert reason == "not a primary Moneyline/Outright or Macro/Politics market"


def test_a_sports_matchup_with_bare_team_group_reaches_downstream_gates():
    # Arrange - end-to-end through evaluate
    m = _universe_candidate("0xmatchup_cfb")
    m["question"] = "Alabama vs Mississippi State"
    m["market_slug"] = "alabama-vs-mississippi-state"
    m["category"] = "Sports"
    m["series_title"] = "College Football"
    m["market_group"] = "Alabama"
    trades = [{"timestamp": _time.time(), "price": 0.5, "size": 4000.0}]

    # Act
    row = evaluate(_FakeSession([], trades=trades), 5.0, m,
                   volume_24h=250_000.0, source="spread")

    # Assert
    assert row["eligible"] is True


def test_a_refusal_without_a_value_is_left_alone():
    # Arrange / Act / Assert - unknown vocabulary passes through untouched.
    ok, reason = fm.identity_reason_with_value("not a primary Moneyline", "")

    assert ok is False
    assert reason == "not a primary Moneyline"


def test_an_admissible_market_stays_admissible():
    # Arrange / Act
    ok, reason = fm.identity_reason_with_value("", "Spread -2.5")

    # Assert
    assert ok is True
    assert reason == ""


def test_evaluate_reports_the_group_value_on_a_submarket_refusal():
    # Arrange - a real submarket: title carries the group label the venue gave.
    m = _universe_candidate("0xsub")
    m["question"] = "Spread: Eagles (-3.5)"
    m["market_slug"] = "nfl-phi-chi-spread-away-3pt5"
    m["market_group"] = "Spread -3.5"

    # Act
    row = evaluate(_FakeSession([], trades=_TRADES), 5.0, m,
                   volume_24h=250_000.0, source="spread")

    # Assert
    assert row["eligible"] is False
    assert "Spread -3.5" in row["reject_reason"]
    assert fm._cause(row["reject_reason"]) == "carries a submarket group label"


def test_a_capped_pass_stamps_the_condition_on_its_rejection_rows():
    # Arrange - the candidate came from a capped scan; the row must say so.
    m = _universe_candidate("0xcapped")
    m["market_group"] = "Spread -3.5"
    m["fetch_truncated"] = True

    # Act
    row = evaluate(_FakeSession([]), 5.0, m, volume_24h=250_000.0,
                   source="spread")

    # Assert
    assert row["eligible"] is False
    assert row["fetch_truncated"] is True


def test_a_complete_pass_does_not_stamp_its_rows():
    # Arrange - the scan reached exhaustion, so the row carries no cap.
    m = _universe_candidate("0xwhole")
    m["market_group"] = "Spread -3.5"
    m["fetch_truncated"] = False

    # Act
    row = evaluate(_FakeSession([]), 5.0, m, volume_24h=250_000.0,
                   source="spread")

    # Assert
    assert row["eligible"] is False
    assert row["fetch_truncated"] is False


def test_an_eligible_live_market_past_kickoff_reaches_the_books():
    # Arrange â€” end-to-end through evaluate: venue open, past endDate, tape alive.
    m = _live_sports_market("0xe2e", _KICKOFF_PAST)

    # Act
    row = evaluate(_FakeSession([], trades=_TRADES), 5.0, m,
                   volume_24h=250_000.0, source="spread")

    # Assert
    assert row["eligible"] is True
    assert row["reject_reason"] == ""


# --- intake expiry gate (#357) ------------------------------------------------


def test_expired_at_intake_refuses_past_end_date_for_non_sports():
    now = "2026-10-04T12:00:00Z"
    past = "2026-10-03T20:00:00Z"  # 16 hours ago
    expired, reason = expired_at_intake(past, start_iso=None, category="Crypto", now_iso=now)
    assert expired is True
    assert reason == "horizon passed (expired 16.0h ago)"
    assert fm._cause(reason) == "horizon"


def test_expired_at_intake_admits_future_or_missing_end_date():
    now = "2026-10-04T12:00:00Z"
    future = "2026-10-05T12:00:00Z"
    # Future
    expired, reason = expired_at_intake(future, category="Crypto", now_iso=now)
    assert expired is False
    assert reason == ""
    # Missing / None
    expired, reason = expired_at_intake(None, category="Crypto", now_iso=now)
    assert expired is False
    assert reason == ""
    # Malformed / non-string
    expired, reason = expired_at_intake("not-a-date", category="Crypto", now_iso=now)
    assert expired is False
    assert reason == ""
    expired, reason = expired_at_intake(12345, category="Crypto", now_iso=now)
    assert expired is False
    assert reason == ""


def test_expired_at_intake_preserves_sports_kickoff_exception():
    now = "2026-10-04T12:00:00Z"
    past = "2026-10-04T10:00:00Z"  # 2 hours ago
    # Category Sports without start_iso
    expired, reason = expired_at_intake(past, start_iso=None, category="Sports", now_iso=now)
    assert expired is False
    assert reason == ""
    # start_iso present
    expired, reason = expired_at_intake(past, start_iso="2026-10-04T10:00:00Z", category="Other", now_iso=now)
    assert expired is False
    assert reason == ""


class _ExplodingSessionForExpired:
    """A book or tape fetch means the early expiry gate ran too late (#357)."""

    def __init__(self):
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        raise AssertionError(f"evaluate fetched URL ({url}) for an expired market")


def test_evaluate_refuses_expired_market_before_fetching_tape_or_books():
    now_dt = datetime.now(timezone.utc)
    past_iso = (now_dt - timedelta(hours=16)).isoformat()
    m = _universe_candidate("0xexpired")
    m["end_date_iso"] = past_iso
    m["category"] = "Crypto"

    session = _ExplodingSessionForExpired()
    row = evaluate(session, 5.0, m, volume_24h=250_000.0, source="spread")

    assert session.calls == []
    assert row["eligible"] is False
    assert "horizon passed" in row["reject_reason"]
    assert "expired" in row["reject_reason"]
    assert fm._cause(row["reject_reason"]) == "horizon"


def test_evaluate_missing_end_date_falls_through_to_tradable():
    m = _universe_candidate("0xnoend")
    m["end_date_iso"] = None

    row = evaluate(_FakeSession([], trades=_TRADES), 5.0, m, volume_24h=250_000.0, source="spread")

    assert row["eligible"] is False
    assert row["reject_reason"] == "horizon unknown"
    assert fm._cause(row["reject_reason"]) == "horizon"


def test_screening_spread_gate_boundary():
    assert fm.MAX_BOOK_SPREAD == 0.0205

    class _CustomBookSession(_FakeSession):
        def __init__(self, bids, asks):
            super().__init__([], trades=_TRADES)
            self._bids = bids
            self._asks = asks

        def get(self, url, params=None, timeout=None):
            if "trades" in url:
                return _Resp(self.trades)
            return _Resp({"bids": self._bids, "asks": self._asks})

    # Books with 2c spread: admitted
    m = _universe_candidate("0xok")
    session_ok = _CustomBookSession([{"price": "0.49", "size": "5000"}], [{"price": "0.51", "size": "5000"}])
    row = evaluate(session_ok, 5.0, m, volume_24h=250_000.0, source="spread")
    assert row["eligible"] is True

    # Books with 3c spread: rejected by spread gate
    session_wide = _CustomBookSession([{"price": "0.485", "size": "5000"}], [{"price": "0.515", "size": "5000"}])
    row_wide = evaluate(session_wide, 5.0, m, volume_24h=250_000.0, source="spread")
    assert row_wide["eligible"] is False
    assert "spread 0.0300 > 0.0205" in row_wide["reject_reason"]


def test_screening_mid_price_band_boundary():
    class _MidBookSession(_FakeSession):
        def __init__(self, yes_mid, no_mid):
            super().__init__([], trades=_TRADES)
            self._yes_mid = yes_mid
            self._no_mid = no_mid

        def get(self, url, params=None, timeout=None):
            if "trades" in url:
                return _Resp(self.trades)
            mid = self._yes_mid if params and "yes" in str(params.get("token_id", "")) else self._no_mid
            return _Resp({
                "bids": [{"price": f"{mid - 0.01:.4f}", "size": "5000"}],
                "asks": [{"price": f"{mid + 0.01:.4f}", "size": "5000"}],
            })

    m = _universe_candidate("0xmid")

    # Mids 0.17 and 0.83 (inside [0.15, 0.85]): admitted
    session_in = _MidBookSession(0.17, 0.83)
    row_in = evaluate(session_in, 5.0, m, volume_24h=250_000.0, source="spread")
    assert row_in["eligible"] is True

    # Mids 0.14 and 0.86 (outside [0.15, 0.85]): rejected as decided mid
    session_out = _MidBookSession(0.14, 0.86)
    row_out = evaluate(session_out, 5.0, m, volume_24h=250_000.0, source="spread")
    assert row_out["eligible"] is False
    assert "decided mid" in row_out["reject_reason"]
    assert "outside [0.15, 0.85]" in row_out["reject_reason"]

