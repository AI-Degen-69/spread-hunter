"""End-to-end unit tests for redesigned market filtering pipeline (#435).

This suite validates the end-to-end execution of the market filtering funnel
offline without reaching the live Polymarket venue or writing to production stores:
1. Strict fail-fast evaluation order (cheap metadata -> tape -> books).
2. Boundary and edge conditions: price band [0.15, 0.85] and tight 0.02 spread ceiling.
3. Venue-declared live sports / eSports priority (volatility exemption and lower volume bar).
4. Zero-reward market independence and downstream decide order manager safety.
5. Snapshot accounting and persistence consistency.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from unittest.mock import patch

import pytest

import scripts.filter_markets as fm
from scripts.filter_markets import _cause


class _Resp:
    """Mock HTTP response returning static JSON."""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeSession:
    """Offline requests session that logs URLs and returns configurable responses."""

    def __init__(self, trades: Optional[list] = None,
                 books_by_token: Optional[dict[str, dict]] = None,
                 default_book: Optional[dict] = None):
        self.trades = trades or []
        self.books_by_token = books_by_token or {}
        self.default_book = default_book or {
            "bids": [{"price": "0.49", "size": "5000"}],
            "asks": [{"price": "0.51", "size": "5000"}],
        }
        self.requests: list[tuple[str, Optional[dict]]] = []

    def get(self, url: str, params: Optional[dict] = None, timeout: Optional[float] = None):
        self.requests.append((url, params))
        if "trades" in url:
            return _Resp(self.trades)
        if "book" in url:
            tok = (params or {}).get("token_id")
            book = self.books_by_token.get(tok, self.default_book)
            return _Resp(book)
        return _Resp({})


def _candidate(cid: str = "0xtest", **overrides) -> dict:
    """Generate a valid candidate market dictionary clearing initial checks."""
    row = {
        "condition_id": cid,
        "question": "Will Ethereum reach $10,000 by year end?",
        "market_slug": f"mkt-{cid}",
        "category": "Crypto",
        "market_type": "",
        "market_group": "",
        "series_title": "Ethereum",
        "event_title": "Ethereum price",
        "tokens": [{"token_id": f"{cid}-yes"}, {"token_id": f"{cid}-no"}],
        "rewards": {"max_spread": 3.5, "min_size": 50, "rate": 50.0},
        "minimum_tick_size": 0.01,
        "end_date_iso": (datetime.now(timezone.utc) + timedelta(days=5)).isoformat(),
        "_order_min": 5,
        "_spread": 0.02,
        "_volume_24h": 250_000.0,
        "closed": False,
        "accepting_orders": True,
    }
    row.update(overrides)
    return row


def _book_payload(bids: list[tuple[float, float]], asks: list[tuple[float, float]]) -> dict:
    """Convert bid and ask (price, size) lists to Polymarket API book JSON format."""
    return {
        "bids": [{"price": f"{p:.4f}", "size": f"{s:.2f}"} for p, s in bids],
        "asks": [{"price": f"{p:.4f}", "size": f"{s:.2f}"} for p, s in asks],
    }


def _books(yes_bid: float, yes_ask: float, depth_usd: float = 2400.0,
           no_bid: Optional[float] = None, no_ask: Optional[float] = None) -> tuple[dict, dict]:
    """Build paired YES and NO book payloads with specified prices and depth."""
    yes_size = depth_usd / max(yes_bid, 0.01)
    yes_book = _book_payload([(yes_bid, yes_size)], [(yes_ask, 5000.0)])

    nb = (1.0 - yes_ask) if no_bid is None else no_bid
    na = (1.0 - yes_bid) if no_ask is None else no_ask
    no_size = depth_usd / max(nb, 0.01)
    no_book = _book_payload([(nb, no_size)], [(na, 5000.0)])
    return yes_book, no_book


def _active_tape(now_ts: float, count: int = 60, total_usd: float = 12_000.0,
                 price_swing_cents: float = 3.0) -> list[dict]:
    """Generate trade history representing an active tape with sufficient movement and range."""
    trades = []
    size_per_trade = (total_usd / max(count, 1)) / 0.50
    for i in range(count):
        # Evenly distribute trades over last 15 minutes (within 30m window)
        t_time = now_ts - (15 * 60) * (i / max(count, 1))
        # Alternate prices to establish range
        swing = (price_swing_cents / 100.0) if i % 2 == 0 else 0.0
        trades.append({
            "timestamp": t_time,
            "price": 0.50 + swing,
            "size": size_per_trade,
        })
    return trades


def _flat_tape(now_ts: float) -> list[dict]:
    """Generate dead/flat trade history with insufficient dollar movement."""
    return [{
        "timestamp": now_ts - 100.0,
        "price": 0.50,
        "size": 1.0,  # $0.50 volume << $5,000 threshold
    }]


# ==============================================================================
# Task 1: Baseline Market & Price / Spread Edge Tests
# ==============================================================================

def test_baseline_market_clears_all_screening_gates():
    now_ts = time.time()
    now_iso = datetime.fromtimestamp(now_ts, timezone.utc).isoformat()
    cid = "0xbaseline"
    cand = _candidate(cid, end_date_iso=(datetime.fromtimestamp(now_ts, timezone.utc) + timedelta(days=5)).isoformat())

    yes_book, no_book = _books(0.49, 0.51, depth_usd=2400.0)
    session = _FakeSession(
        trades=_active_tape(now_ts),
        books_by_token={f"{cid}-yes": yes_book, f"{cid}-no": no_book},
    )

    result = fm.evaluate(
        session, rate=50.0, m=cand, volume_24h=cand["_volume_24h"],
        source="spread", velocity_gate_enabled=True, now_iso=now_iso,
    )

    assert result["eligible"] is True
    assert result.get("reject_reason") in (None, "")
    assert result["source"] == "spread"


@pytest.mark.parametrize("yes_mid, yes_bid, yes_ask", [
    (0.10, 0.09, 0.11),
    (0.90, 0.89, 0.91),
])
def test_price_edge_rejects_out_of_band_mids(yes_mid, yes_bid, yes_ask):
    now_ts = time.time()
    now_iso = datetime.fromtimestamp(now_ts, timezone.utc).isoformat()
    cid = f"0xmid_{int(yes_mid * 100)}"
    cand = _candidate(cid)

    yes_book, no_book = _books(yes_bid, yes_ask, depth_usd=2400.0)
    session = _FakeSession(
        trades=_active_tape(now_ts),
        books_by_token={f"{cid}-yes": yes_book, f"{cid}-no": no_book},
    )

    result = fm.evaluate(
        session, rate=50.0, m=cand, volume_24h=cand["_volume_24h"],
        source="spread", velocity_gate_enabled=True, now_iso=now_iso,
    )

    assert result["eligible"] is False
    expected_reason = f"YES: decided mid {yes_mid:.2f} outside [0.15, 0.85]"
    assert result["reject_reason"] == expected_reason
    assert _cause(result["reject_reason"]) == "YES decided mid"


def test_price_edge_precedes_spread_and_depth_failures():
    """A market with an out-of-band mid, wide spread, and thin depth fails at the price gate first."""
    now_ts = time.time()
    now_iso = datetime.fromtimestamp(now_ts, timezone.utc).isoformat()
    cid = "0xcombo_price"
    cand = _candidate(cid)

    # Mid 0.10, wide spread (0.05), thin depth ($100 << $500)
    yes_book = _book_payload([(0.075, 100.0)], [(0.125, 5000.0)])
    no_book = _book_payload([(0.875, 100.0)], [(0.925, 5000.0)])
    session = _FakeSession(
        trades=_active_tape(now_ts),
        books_by_token={f"{cid}-yes": yes_book, f"{cid}-no": no_book},
    )

    result = fm.evaluate(
        session, rate=50.0, m=cand, volume_24h=cand["_volume_24h"],
        source="spread", max_spread=0.02, min_depth_usd=500.0,
        velocity_gate_enabled=True, now_iso=now_iso,
    )

    assert result["eligible"] is False
    assert result["reject_reason"] == "YES: decided mid 0.10 outside [0.15, 0.85]"
    assert _cause(result["reject_reason"]) == "YES decided mid"


def test_price_boundary_exactly_at_band_is_rejected():
    """Strict inequality: mid of exactly 0.15 is rejected by `not 0.15 < mid < 0.85`."""
    now_ts = time.time()
    now_iso = datetime.fromtimestamp(now_ts, timezone.utc).isoformat()
    cid = "0xbound_15"
    cand = _candidate(cid)

    # 0.125 + 0.175 = 0.30 exactly in float; mid is exactly 0.1500
    yes_book, no_book = _books(0.125, 0.175, depth_usd=2400.0)
    session = _FakeSession(
        trades=_active_tape(now_ts),
        books_by_token={f"{cid}-yes": yes_book, f"{cid}-no": no_book},
    )

    result = fm.evaluate(
        session, rate=50.0, m=cand, volume_24h=cand["_volume_24h"],
        source="spread", velocity_gate_enabled=True, now_iso=now_iso,
    )

    assert result["eligible"] is False
    assert result["reject_reason"] == "YES: decided mid 0.15 outside [0.15, 0.85]"
    assert _cause(result["reject_reason"]) == "YES decided mid"


def test_spread_edge_rejects_spread_exceeding_ceiling():
    now_ts = time.time()
    now_iso = datetime.fromtimestamp(now_ts, timezone.utc).isoformat()
    cid = "0xwidespread"
    cand = _candidate(cid)

    # Spread 0.03 (0.515 - 0.485 = 0.03) > ceiling 0.02
    yes_book, no_book = _books(0.485, 0.515, depth_usd=2400.0)
    session = _FakeSession(
        trades=_active_tape(now_ts),
        books_by_token={f"{cid}-yes": yes_book, f"{cid}-no": no_book},
    )

    result = fm.evaluate(
        session, rate=50.0, m=cand, volume_24h=cand["_volume_24h"],
        source="spread", max_spread=0.02, velocity_gate_enabled=True, now_iso=now_iso,
    )

    assert result["eligible"] is False
    assert result["reject_reason"] == "YES: spread 0.0300 > 0.0200"
    assert _cause(result["reject_reason"]) == "YES spread"


def test_spread_edge_admits_spread_within_ceiling():
    now_ts = time.time()
    now_iso = datetime.fromtimestamp(now_ts, timezone.utc).isoformat()
    cid = "0xtightspread"
    cand = _candidate(cid)

    # Spread 0.01 (0.505 - 0.495 = 0.01) <= ceiling 0.02
    yes_book, no_book = _books(0.495, 0.505, depth_usd=2400.0)
    session = _FakeSession(
        trades=_active_tape(now_ts),
        books_by_token={f"{cid}-yes": yes_book, f"{cid}-no": no_book},
    )

    result = fm.evaluate(
        session, rate=50.0, m=cand, volume_24h=cand["_volume_24h"],
        source="spread", max_spread=0.02, velocity_gate_enabled=True, now_iso=now_iso,
    )

    assert result["eligible"] is True


def test_spread_edge_precedes_thin_depth_failure():
    """When both spread is wide and depth is thin, the spread gate rejects first."""
    now_ts = time.time()
    now_iso = datetime.fromtimestamp(now_ts, timezone.utc).isoformat()
    cid = "0xspread_vs_depth"
    cand = _candidate(cid)

    # Spread 0.04 > 0.02, Depth $100 <= $500
    yes_book, no_book = _books(0.48, 0.52, depth_usd=100.0)
    session = _FakeSession(
        trades=_active_tape(now_ts),
        books_by_token={f"{cid}-yes": yes_book, f"{cid}-no": no_book},
    )

    result = fm.evaluate(
        session, rate=50.0, m=cand, volume_24h=cand["_volume_24h"],
        source="spread", max_spread=0.02, min_depth_usd=500.0,
        velocity_gate_enabled=True, now_iso=now_iso,
    )

    assert result["eligible"] is False
    assert result["reject_reason"] == "YES: spread 0.0400 > 0.0200"
    assert _cause(result["reject_reason"]) == "YES spread"
