"""Unit tests for live/engine/market_feed.py."""
import json
import time
from pathlib import Path

import pytest
from engine.market_feed import (
    GraduatedMarket,
    MarketFeedAbsentError,
    MarketFeedError,
    MarketFeedStaleError,
    get_market_by_cid,
    load_graduated_markets,
)

SAMPLE_ROW = {
    "source": "spread",
    "spread": 0.01,
    "eligible": True,
    "reject_reason": "",
    "volume_24h": 100000.0,
    "days_to_resolve": 5.0,
    "cid": "0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
    "title": "Test Market vs Opponent",
    "slug": "test-market-slug",
    "daily": 0.0,
    "min_size": 5.0,
    "max_spread": 4.5,
    "tick": 0.01,
    "shares": 120,
    "est_income": 10.0,
    "est_capital": 120.0,
    "return_pct_day": 8.33,
    "their_score": 1500.0,
}


def test_load_graduated_markets_real_file():
    """The real feed must load cleanly, whatever the ranker currently holds.

    `run/markets.json` is rewritten by the live ranker every cycle -- row count
    and ordering both move. Asserting a fixed count here made the suite pass at
    the moment it was written and fail minutes later, which is the mutating-data
    trap: snapshot it, or assert only what is invariant. Shape is invariant;
    contents are not.
    """
    markets = load_graduated_markets()
    assert markets, "ranker feed is present but empty"
    for m in markets:
        assert isinstance(m, GraduatedMarket)
        assert m.cid.startswith("0x")
        assert m.min_size > 0
        assert m.tick > 0
        assert m.max_spread > 0


def test_load_graduated_markets_absent(tmp_path):
    """Missing file raises MarketFeedAbsentError."""
    absent = tmp_path / "nonexistent.json"
    with pytest.raises(MarketFeedAbsentError, match="graduated markets feed missing"):
        load_graduated_markets(path=absent)


def test_load_graduated_markets_empty(tmp_path):
    """Empty file raises MarketFeedError."""
    empty = tmp_path / "empty.json"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(MarketFeedError, match="is empty"):
        load_graduated_markets(path=empty)


def test_load_graduated_markets_stale(tmp_path):
    """File older than max_age_sec raises MarketFeedStaleError."""
    stale_file = tmp_path / "stale.json"
    stale_file.write_text(json.dumps([SAMPLE_ROW]), encoding="utf-8")
    # max_age_sec = 0.001 should trigger stale error after tiny sleep
    time.sleep(0.01)
    with pytest.raises(MarketFeedStaleError, match="is stale"):
        load_graduated_markets(path=stale_file, max_age_sec=0.001)


def test_load_graduated_markets_malformed_json(tmp_path):
    """Non-JSON content raises MarketFeedError."""
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(MarketFeedError, match="failed to parse JSON"):
        load_graduated_markets(path=bad_json)


def test_load_graduated_markets_not_a_list(tmp_path):
    """JSON object instead of list raises MarketFeedError."""
    bad_type = tmp_path / "bad_type.json"
    bad_type.write_text(json.dumps({"cid": "0x123"}), encoding="utf-8")
    with pytest.raises(MarketFeedError, match="must contain a JSON list"):
        load_graduated_markets(path=bad_type)


def test_get_market_by_cid():
    """Look up market by full CID and prefix."""
    markets = load_graduated_markets()
    first = markets[0]
    found_full = get_market_by_cid(first.cid)
    assert found_full is not None
    assert found_full.cid == first.cid

    found_prefix = get_market_by_cid(first.cid[:10])
    assert found_prefix is not None
    assert found_prefix.cid == first.cid

    not_found = get_market_by_cid("0x0000000000000000000000000000000000000000")
    assert not_found is None
