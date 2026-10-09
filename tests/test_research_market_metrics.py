"""Tests for scripts.research_market_metrics."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.research_market_metrics import (
    active_markets,
    build_report,
    fetch_window_trades,
    format_table,
    main,
    measure_market_depth,
    measure_market_volume,
    measure_outcome_depth,
    nearest_rank,
    notional_in_window,
    parse_binary_tokens,
    sample_markets,
    summarize_metric,
)


class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_parse_binary_tokens():
    # Standard string clobTokenIds
    m1 = {
        "clobTokenIds": '["tok_yes", "tok_no"]',
        "outcomes": '["Yes", "No"]',
    }
    assert parse_binary_tokens(m1) == ("tok_yes", "tok_no")

    # Inverted outcomes
    m2 = {
        "clobTokenIds": ["tok_1", "tok_2"],
        "outcomes": ["No", "Yes"],
    }
    assert parse_binary_tokens(m2) == ("tok_2", "tok_1")

    # Up/Down outcomes
    m3 = {
        "clobTokenIds": ["tok_down", "tok_up"],
        "outcomes": ["Down", "Up"],
    }
    assert parse_binary_tokens(m3) == ("tok_up", "tok_down")

    # Invalid: non-binary
    assert parse_binary_tokens({"clobTokenIds": ["t1"]}) is None
    assert parse_binary_tokens({"clobTokenIds": ["t1", "t2", "t3"]}) is None

    # Invalid: bad json or types
    assert parse_binary_tokens({"clobTokenIds": "invalid json"}) is None
    assert parse_binary_tokens({"clobTokenIds": [True, False]}) is None
    assert parse_binary_tokens({"clobTokenIds": ["", "t2"]}) is None
    assert parse_binary_tokens(None) is None


def test_sample_markets():
    pool = [
        {"conditionId": f"c_{i}", "clobTokenIds": [f"y_{i}", f"n_{i}"]}
        for i in range(10)
    ]
    # Add an unusable market
    pool.append({"conditionId": "c_bad", "clobTokenIds": ["only_one"]})

    sampled = sample_markets(pool, size=5, seed=42)
    assert len(sampled) == 5
    assert all(m["conditionId"] != "c_bad" for m in sampled)

    # Determinism with seed
    sampled2 = sample_markets(pool, size=5, seed=42)
    assert [m["conditionId"] for m in sampled] == [m["conditionId"] for m in sampled2]

    # Pool smaller than requested size
    sampled_small = sample_markets(pool[:3], size=10, seed=42)
    assert len(sampled_small) == 3


def test_measure_market_volume():
    assert measure_market_volume({"volume24hr": "1234.5"}) == 1234.5
    assert measure_market_volume({"volume_24h": 5000}) == 5000.0
    assert measure_market_volume({"volume24h": 42.1}) == 42.1
    assert measure_market_volume({"volume": "100"}) == 100.0
    assert measure_market_volume({"volume24hr": "nan"}) is None
    assert measure_market_volume({"volume24hr": -5}) is None
    assert measure_market_volume({}) is None
    assert measure_market_volume(None) is None


def test_measure_outcome_and_market_depth():
    fake_session = MagicMock()

    def _get(url, params=None, timeout=None):
        tok = params.get("token_id")
        if tok == "tok_yes":
            book = {
                "bids": [{"price": "0.50", "size": "100"}, {"price": "0.48", "size": "200"}],
                "asks": [{"price": "0.52", "size": "50"}],
            }
        else:
            book = {
                "bids": [{"price": "0.48", "size": "50"}, {"price": "0.46", "size": "100"}],
                "asks": [{"price": "0.51", "size": "50"}],
            }
        return _FakeResponse(book)

    fake_session.get.side_effect = _get

    d_yes = measure_outcome_depth("https://clob.polymarket.com", "tok_yes", session=fake_session)
    # top bids: 0.50*100 = 50, 0.48*200 = 96 => total = 146.0
    assert d_yes == pytest.approx(146.0)

    d_no = measure_outcome_depth("https://clob.polymarket.com", "tok_no", session=fake_session)
    # top bids: 0.48*50 = 24, 0.46*100 = 46 => total = 70.0
    assert d_no == pytest.approx(70.0)

    # Bottleneck is min(146.0, 70.0) = 70.0
    bottleneck = measure_market_depth("https://clob.polymarket.com", "tok_yes", "tok_no", session=fake_session)
    assert bottleneck == pytest.approx(70.0)


def test_fetch_window_trades_and_notional():
    fake_session = MagicMock()
    now = 10000.0
    cutoff = now - 1800.0  # 8200.0

    page_1 = [
        {"transactionHash": "tx1", "asset": "tok_y", "timestamp": 9500.0, "price": 0.5, "size": 100},
        {"transactionHash": "tx2", "asset": "tok_y", "timestamp": 9000.0, "price": 0.5, "size": 200},
    ]
    page_2 = [
        {"transactionHash": "tx3", "asset": "tok_y", "timestamp": 8100.0, "price": 0.5, "size": 300},
    ]

    def _get(url, params=None, timeout=None):
        offset = params.get("offset", 0)
        if offset == 0:
            return _FakeResponse(page_1)
        return _FakeResponse(page_2)

    fake_session.get.side_effect = _get

    trades, window_complete = fetch_window_trades(
        session=fake_session,
        condition_id="c_test",
        now=now,
        window_seconds=1800,
        page_limit=2,
    )
    assert len(trades) == 3
    assert window_complete is True

    # Notional in window: tx1 (50) + tx2 (100) = 150. tx3 is at 8100 < 8200, so excluded
    notional = notional_in_window(trades, cutoff=cutoff)
    assert notional == pytest.approx(150.0)

    # Test deduplication
    duplicate_trades = list(trades) + [page_1[0]]
    dedup_notional = notional_in_window(duplicate_trades, cutoff=cutoff)
    assert dedup_notional == pytest.approx(150.0)


def test_fetch_window_trades_incomplete_flag():
    fake_session = MagicMock()
    now = 10000.0
    # Every page has full limit and timestamps within window
    fake_session.get.return_value = _FakeResponse([
        {"transactionHash": f"tx_{i}", "asset": "tok", "timestamp": 9900.0, "price": 1, "size": 1}
        for i in range(2)
    ])

    trades, window_complete = fetch_window_trades(
        session=fake_session,
        condition_id="c_test",
        now=now,
        window_seconds=1800,
        max_pages=2,
        page_limit=2,
    )
    assert len(trades) == 4
    assert window_complete is False


def test_fetch_window_trades_non_list_payload_raises():
    fake_session = MagicMock()
    fake_session.get.return_value = _FakeResponse({"error": "bad request"})
    with pytest.raises(ValueError, match="not a list"):
        fetch_window_trades(
            session=fake_session,
            condition_id="c_test",
            now=10000.0,
        )


def test_active_markets_pagination():
    fake_session = MagicMock()
    page1 = [{"id": "m1"}, {"id": "m2"}]
    page2 = [{"id": "m3"}]

    def _get(url, params=None, timeout=None):
        if params.get("offset") == 0:
            return _FakeResponse(page1)
        return _FakeResponse(page2)

    fake_session.get.side_effect = _get

    markets = active_markets(session=fake_session, limit_per_page=2, max_pages=5)
    assert len(markets) == 3
    assert [m["id"] for m in markets] == ["m1", "m2", "m3"]

    # Test unreadable payload
    fake_session.get.side_effect = None
    fake_session.get.return_value = _FakeResponse("not a list or valid dict")
    with pytest.raises(ValueError, match="not a list of markets"):
        active_markets(session=fake_session)


def test_nearest_rank_and_summarize_metric():
    # Test nearest rank
    values = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
    # k = ceil(0.25 * 10) = 3 -> idx 2 -> 30.0
    assert nearest_rank(values, 0.25) == 30.0
    # k = ceil(0.50 * 10) = 5 -> idx 4 -> 50.0
    assert nearest_rank(values, 0.50) == 50.0
    # k = ceil(0.75 * 10) = 8 -> idx 7 -> 80.0
    assert nearest_rank(values, 0.75) == 80.0

    # Edge cases
    assert nearest_rank([42.0], 0.25) == 42.0
    with pytest.raises(ValueError):
        nearest_rank([], 0.5)

    # Empty summary
    empty_summary = summarize_metric([])
    assert empty_summary["count"] == 0
    assert empty_summary["median"] is None
    assert empty_summary["missing"] == 0

    # Summary with None values
    mixed_summary = summarize_metric([10.0, None, 20.0, 30.0, None])
    assert mixed_summary["count"] == 3
    assert mixed_summary["missing"] == 2
    assert mixed_summary["min"] == 10.0
    assert mixed_summary["median"] == 20.0
    assert mixed_summary["max"] == 30.0
    assert mixed_summary["mean"] == 20.0


def test_format_table_and_build_report():
    summaries = {
        "volume_24h": {"count": 10, "min": 100.0, "p25": 200.0, "median": 500.0, "mean": 600.0, "p75": 800.0, "max": 1500.0},
        "recent_traded_notional_30m": {"count": 10, "min": 10.0, "p25": 20.0, "median": 50.0, "mean": 60.0, "p75": 80.0, "max": 150.0},
        "top3_bid_depth": {"count": 10, "min": 50.0, "p25": 100.0, "median": 250.0, "mean": 300.0, "p75": 400.0, "max": 800.0},
    }
    table_str = format_table(
        summaries=summaries,
        sample_size=10,
        pool_size=100,
        seed=1234,
        error_count=1,
        incomplete_windows=2,
    )
    assert "24h Volume" in table_str
    assert "30m Traded Notional" in table_str
    assert "Top-3 Bid Depth" in table_str
    assert "Sampling seed: 1234" in table_str
    assert "1 market sampling errors" in table_str
    assert "2 markets reached the" in table_str

    report = build_report(
        summaries=summaries,
        market_records=[{"slug": "test-slug"}],
        seed=1234,
        sample_size=10,
        pool_size=100,
        error_count=1,
        incomplete_windows=2,
    )
    assert report["parameters"]["seed"] == 1234
    assert len(report["markets"]) == 1
    # Check JSON serializable
    json_bytes = json.dumps(report)
    assert len(json_bytes) > 0


def test_main_cli_sample_size_validation():
    # Invalid sample size < 1
    ret = main(["--sample-size", "0"])
    assert ret == 2

    # Invalid sample size > 100
    ret = main(["--sample-size", "101"])
    assert ret == 2


def test_main_cli_full_flow(tmp_path):
    report_file = tmp_path / "custom_report.json"

    fake_markets = [
        {
            "conditionId": "c_1",
            "slug": "m-1",
            "clobTokenIds": ["y1", "n1"],
            "volume24hr": "5000",
        }
    ]

    with patch("scripts.research_market_metrics.active_markets", return_value=fake_markets), \
         patch("scripts.research_market_metrics.measure_market_depth", return_value=123.45), \
         patch("scripts.research_market_metrics.fetch_window_trades", return_value=([], True)):
        ret = main(["--sample-size", "1", "--seed", "99", "--output", str(report_file)])
        assert ret == 0
        assert report_file.exists()

        content = json.loads(report_file.read_text(encoding="utf-8"))
        assert content["parameters"]["sample_size"] == 1
        assert content["parameters"]["seed"] == 99
        assert content["markets"][0]["slug"] == "m-1"


def test_main_cli_venue_failure():
    with patch("scripts.research_market_metrics.active_markets", side_effect=RuntimeError("connection refused")):
        ret = main(["--sample-size", "10"])
        assert ret == 1


def test_main_cli_empty_sample_pool():
    with patch("scripts.research_market_metrics.active_markets", return_value=[]):
        ret = main(["--sample-size", "10"])
        assert ret == 1


def test_main_cli_save_flag(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.research_market_metrics.LIVE_ROOT", tmp_path)
    fake_markets = [
        {
            "conditionId": "c_1",
            "slug": "m-1",
            "clobTokenIds": ["y1", "n1"],
            "volume24hr": "5000",
        }
    ]
    with patch("scripts.research_market_metrics.active_markets", return_value=fake_markets), \
         patch("scripts.research_market_metrics.measure_market_depth", return_value=123.45), \
         patch("scripts.research_market_metrics.fetch_window_trades", return_value=([], True)):
        ret = main(["--sample-size", "1", "--save"])
        assert ret == 0
        reports = list((tmp_path / "reports").glob("market_metrics_statistics_report_*.json"))
        assert len(reports) == 1

