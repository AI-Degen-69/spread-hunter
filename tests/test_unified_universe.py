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
from scripts.filter_markets import evaluate, gamma_universe


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
            "bids": [{"price": "0.48", "size": "5000"}],
            "asks": [{"price": "0.52", "size": "5000"}],
        })


def _gamma_row(cid: str, vol: float, **over) -> dict:
    row = {
        "conditionId": cid,
        "question": f"Market {cid}",
        "slug": f"mkt-{cid}",
        "volume24hr": vol,
        "spread": 0.04,
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
        "_spread": 0.04,
        "_volume_24h": 250_000.0,
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
    assert row["yes_spread"] == 0.04
    assert row["no_spread"] == 0.04
    assert row["yes_depth_usd"] == 2400.0
    assert row["no_depth_usd"] == 2400.0


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
                   volume_24h=250_000.0, source="spread")

    assert row["eligible"] is False
    assert row["reject_reason"] == (
        "cannot score here without overbidding the book")
    assert row["cid"] == "0xzero"
    assert row["movement_usd"] == pytest.approx(2000.0)


def test_a_decided_mid_buckets_as_one_gate():
    assert (fm._cause("YES: decided mid 0.85 outside [0.20, 0.80]")
            == "YES decided mid")
    assert (fm._cause("NO: decided mid 0.11 outside [0.20, 0.80]")
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
         "reject_reason": "no movement: $0 traded in last 30m under $500 "
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

    assert universe[0]["category"] == "Politics"
    assert universe[0]["tags"] == ["Politics", "Elections"]


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
    trades = [{"timestamp": _time.time(), "price": 0.5, "size": 4000.0}]
    row = evaluate(_FakeSession([], trades=trades), 5.0, m,
                   volume_24h=250_000.0, source="spread")

    assert row["eligible"] is True
    assert row["tags"] == ["Politics", "Elections"]
    assert row["market_type"] == "binary"
