"""Unit tests for live_exec decide CLI verb."""
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from engine.live_exec import decide, _evaluate_single_market_quote
from engine.market_feed import GraduatedMarket
from engine.markets import LiveMarket


@pytest.fixture
def mock_pinned_market():
    return LiveMarket(
        condition_id="0x6a571e1b83c8238df6cf49e89ff815d98b522d01db7af1568a514f3d37ec8ce0",
        market_slug="atp-faria-walton-2026-08-18",
        up_token="10439858151242",
        down_token="69795149601155",
        start_ts=1000.0,
        end_ts=9999999.0,
        tick_size=0.01,
        neg_risk=False,
    )


@pytest.fixture
def mock_books():
    up = {
        "best_bid": 0.52, "best_ask": 0.53,
        "bids": {0.52: 1000.0}, "asks": {0.53: 1000.0}
    }
    dn = {
        "best_bid": 0.47, "best_ask": 0.48,
        "bids": {0.47: 1000.0}, "asks": {0.48: 1000.0}
    }
    return up, dn


@pytest.fixture
def frozen_feed(tmp_path, monkeypatch):
    """Two graduated markets, frozen.

    `run/markets.json` is rewritten by the live ranker every cycle: row count
    and ordering both move under the suite. Pointing these tests at the real
    file made them pass when written and fail minutes later. The feed's own
    parser still runs -- only the bytes are pinned.
    """
    from engine import market_feed

    row = {
        "source": "spread", "spread": 0.01, "eligible": True, "reject_reason": "",
        "volume_24h": 150000.0, "days_to_resolve": 6.63,
        "cid": "0x6a571e1b83c8238df6cf49e89ff815d98b522d01db7af1568a514f3d37ec8ce0",
        "title": "Cincinnati Open: Jaime Faria vs Adam Walton",
        "slug": "atp-faria-walton-2026-08-18", "daily": 0.0, "min_size": 5.0,
        "max_spread": 4.5, "tick": 0.01, "shares": 120, "est_income": 2.6,
        "est_capital": 120.0, "return_pct_day": 2.2, "their_score": 11180.9,
    }
    second = dict(row,
                  cid="0xde5eeb0860270c58e265904d31e772698a9764ca0dec088bdef92e7259bf275a",
                  slug="mlb-det-pit-2026-08-18",
                  title="Detroit Tigers vs. Pittsburgh Pirates")
    feed = tmp_path / "markets.json"
    feed.write_text(json.dumps([row, second]), encoding="utf-8")
    monkeypatch.setattr(market_feed, "DEFAULT_MARKETS_PATH", feed)
    return feed


def test_decide_single_market_with_mocked_network(
    mock_pinned_market, mock_books, tmp_path, frozen_feed
):
    up_book, down_book = mock_books
    with patch("engine.markets.fetch_pinned_market", return_value=mock_pinned_market), \
         patch("engine.markets.full_book", side_effect=[up_book, down_book]):

        results = decide(target="0", db_path=tmp_path / "live.db")
        assert len(results) == 1
        res = results[0]
        assert res["cid"] == mock_pinned_market.condition_id
        assert len(res["intents"]) == 2
        assert not res["why"]


def test_decide_all_graduated_markets_mocked(
    mock_pinned_market, mock_books, tmp_path, frozen_feed
):
    up_book, down_book = mock_books
    with patch("engine.markets.fetch_pinned_market", return_value=mock_pinned_market), \
         patch("engine.markets.full_book", return_value=up_book):

        results = decide(all_graduated=True, db_path=tmp_path / "live.db")
        assert len(results) == 2
        for res in results:
            assert "cid" in res
