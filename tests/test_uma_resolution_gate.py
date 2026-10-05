"""Tests for the global UMA oracle resolution gate.

Markets in UMA proposed/disputed/resolved state must be rejected across:
1. `scripts.filter_markets` (ranker / screener)
2. `core_brain.markets` (live market discovery)
3. `core_brain.pair_scanner` (pair scanner)
4. `core_brain.market_resolution` (resolution sweeper marks resolved=True)
"""
from __future__ import annotations

import json
import pytest

from core_brain.markets import _parse_market_row
from core_brain.pair_scanner import parse_candidate
from core_brain.market_resolution import parse_end_state
from scripts.filter_markets import resolve_state, evaluate


def test_filter_markets_resolve_state_flags_uma_proposed():
    resolved, reason, end = resolve_state(
        closed=False,
        accepting_orders=True,
        end_iso="2026-10-12T09:00:00Z",
        uma_status="proposed",
    )
    assert resolved is True
    assert "uma resolution proposed" in reason.lower()


def test_filter_markets_resolve_state_flags_uma_statuses_list():
    resolved, reason, end = resolve_state(
        closed=False,
        accepting_orders=True,
        end_iso="2026-10-12T09:00:00Z",
        uma_statuses=["proposed", "proposed"],
    )
    assert resolved is True
    assert "uma resolution" in reason.lower()


def test_filter_markets_resolve_state_passes_clean_uma_status():
    resolved, reason, end = resolve_state(
        closed=False,
        accepting_orders=True,
        end_iso="2026-10-12T09:00:00Z",
        uma_status="",
        uma_statuses=[],
    )
    assert resolved is False
    assert "open" in reason.lower()


def test_core_brain_markets_rejects_uma_proposed():
    m = {
        "conditionId": "0x123",
        "slug": "atp-prizmic-jones",
        "clobTokenIds": json.dumps(["111", "222"]),
        "eventStartTime": "2026-10-05T14:00:00Z",
        "endDate": "2026-10-12T09:00:00Z",
        "orderPriceMinTickSize": 0.01,
        "negRisk": False,
        "umaResolutionStatus": "proposed",
    }
    assert _parse_market_row(m) is None


def test_core_brain_markets_accepts_clean_uma():
    m = {
        "conditionId": "0x123",
        "slug": "atp-active-match",
        "clobTokenIds": json.dumps(["111", "222"]),
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


def test_pair_scanner_rejects_uma_proposed():
    row = {
        "conditionId": "0x123",
        "slug": "atp-prizmic-jones",
        "clobTokenIds": ["111", "222"],
        "volume24hr": 64000.0,
        "question": "Prizmic vs Jones",
        "umaResolutionStatus": "proposed",
    }
    assert parse_candidate(row) is None


def test_market_resolution_parse_end_state_marks_uma_proposed_resolved():
    row = {
        "conditionId": "0x123",
        "closed": False,
        "acceptingOrders": True,
        "endDate": "2026-10-12T09:00:00Z",
        "umaResolutionStatus": "proposed",
    }
    end_state = parse_end_state(row, now_ts=1791200000.0)
    assert end_state is not None
    assert end_state.resolved is True
