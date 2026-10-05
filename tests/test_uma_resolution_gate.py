"""Tests for the global UMA oracle resolution gate.

Markets in UMA proposed/disputed/resolved state must be rejected across:
1. `scripts.filter_markets` (ranker / screener)
2. `core_brain.markets` (live market discovery)
3. `core_brain.pair_scanner` (pair scanner)
4. `core_brain.market_resolution` (resolution sweeper marks resolved=True)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
import pytest

from core_brain.markets import _parse_market_row
from core_brain.pair_scanner import parse_candidate
from core_brain.market_resolution import (
    parse_end_state,
    parse_uma_resolution_status,
    extract_uma_resolution_status,
)
from scripts.filter_markets import resolve_state, evaluate, gamma_universe


def test_filter_markets_resolve_state_flags_uma_proposed():
    resolved, reason, _ = resolve_state(
        closed=False,
        accepting_orders=True,
        end_iso="2026-10-12T09:00:00Z",
        uma_status="proposed",
    )
    assert resolved is True
    assert "uma resolution proposed" in reason.lower()


def test_filter_markets_resolve_state_flags_uma_statuses_list():
    resolved, reason, _ = resolve_state(
        closed=False,
        accepting_orders=True,
        end_iso="2026-10-12T09:00:00Z",
        uma_statuses=["proposed", "proposed"],
    )
    assert resolved is True
    assert "uma resolution" in reason.lower()


def test_filter_markets_resolve_state_passes_clean_uma_status():
    resolved, reason, _ = resolve_state(
        closed=False,
        accepting_orders=True,
        end_iso="2026-10-12T09:00:00Z",
        uma_status="",
        uma_statuses=[],
    )
    assert resolved is False
    assert "open" in reason.lower()


def test_filter_markets_evaluate_rejects_uma_proposed_without_session():
    m = {
        "condition_id": "0x123",
        "question": "Will BTC close above 100k?",
        "market_slug": "btc-above-100k",
        "category": "Crypto",
        "market_type": "",
        "market_group": "",
        "series_title": "Bitcoin",
        "event_title": "Bitcoin price",
        "tokens": [{"token_id": "111"}, {"token_id": "222"}],
        "rewards": {"max_spread": 3.5, "min_size": 50},
        "minimum_tick_size": 0.01,
        "end_date_iso": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
        "_order_min": 5,
        "_spread": 0.04,
        "_volume_24h": 250_000.0,
        "uma_resolution_status": "proposed",
        "closed": False,
        "accepting_orders": True,
    }
    # session=None ensures evaluate returns immediately at UMA gate without network calls
    res = evaluate(None, rate=10.0, m=m, volume_24h=250_000.0)
    assert res["eligible"] is False
    assert "uma resolution proposed" in res["reject_reason"].lower()


class _MockGammaSession:
    def __init__(self, rows: list[dict]):
        self._rows = rows
        self._calls = 0

    def get(self, url: str, params: Any = None, timeout: Any = None):
        class _Resp:
            def __init__(self, data):
                self._data = data

            def json(self):
                return self._data

        self._calls += 1
        data = self._rows if self._calls == 1 else []
        return _Resp(data)


def test_filter_markets_gamma_universe_rejects_uma_proposed():
    mock_market = {
        "conditionId": "0x123",
        "question": "Will BTC close above 100k?",
        "slug": "btc-above-100k",
        "volume24hr": 50000.0,
        "enableOrderBook": True,
        "acceptingOrders": True,
        "umaResolutionStatus": "proposed",
        "clobTokenIds": '["111", "222"]',
        "spread": 0.02,
        "endDate": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
        "orderMinSize": 5,
        "orderPriceMinTickSize": 0.01,
    }
    session = _MockGammaSession([mock_market])
    universe, meta = gamma_universe(session, min_volume_usd=1000.0)
    assert len(universe) == 0
    assert meta["cheap_rejects"].get("uma resolution proposed") == 1


@pytest.mark.parametrize(
    "status_val,statuses_val",
    [
        ("proposed", None),
        ("disputed", None),
        ("resolved", None),
        ("  PROPOSED  ", None),
        (None, ["proposed"]),
        (None, '["disputed"]'),
        (None, ["  RESOLVED  "]),
    ],
)
def test_parsers_reject_uma_status_matrix(status_val, statuses_val):
    # 1. core_brain.markets
    row_markets = {
        "conditionId": "0x123",
        "slug": "atp-match",
        "clobTokenIds": '["111", "222"]',
        "eventStartTime": "2026-10-05T14:00:00Z",
        "endDate": "2026-10-12T09:00:00Z",
        "orderPriceMinTickSize": 0.01,
        "negRisk": False,
        "umaResolutionStatus": status_val,
        "umaResolutionStatuses": statuses_val,
    }
    assert _parse_market_row(row_markets) is None

    # 2. core_brain.pair_scanner
    row_scanner = {
        "conditionId": "0x123",
        "slug": "atp-match",
        "clobTokenIds": ["111", "222"],
        "volume24hr": 64000.0,
        "question": "Player A vs Player B",
        "umaResolutionStatus": status_val,
        "umaResolutionStatuses": statuses_val,
    }
    assert parse_candidate(row_scanner) is None

    # 3. core_brain.market_resolution
    row_res = {
        "conditionId": "0x123",
        "closed": False,
        "acceptingOrders": True,
        "endDate": "2026-10-12T09:00:00Z",
        "umaResolutionStatus": status_val,
        "umaResolutionStatuses": statuses_val,
    }
    end_state = parse_end_state(row_res, now_ts=1791200000.0)
    assert end_state is not None
    assert end_state.resolved is True


def test_core_brain_markets_accepts_clean_uma():
    m = {
        "conditionId": "0x123",
        "slug": "atp-active-match",
        "clobTokenIds": '["111", "222"]',
        "eventStartTime": "2026-10-05T14:00:00Z",
        "endDate": "2026-10-12T09:00:00Z",
        "orderPriceMinTickSize": 0.01,
        "negRisk": False,
        "umaResolutionStatus": "",
        "umaResolutionStatuses": "[]",
    }
    parsed = _parse_market_row(m)
    assert parsed is not None
    assert parsed.condition_id == "0x123"
