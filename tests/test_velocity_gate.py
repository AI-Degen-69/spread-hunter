"""Tests for real-time trade velocity and volatility range gate (#370)."""
from __future__ import annotations

import pytest

from scripts.filter_markets import (
    tape_movement_and_range,
    velocity_gate_reject,
    evaluate,
)

NOW = 1_788_000_000.0
WINDOW = 1800.0  # 30m


class _TapeSession:
    """A session that answers the trades endpoint with a canned payload."""

    def __init__(self, payload, boom: bool = False):
        self.payload = payload
        self.boom = boom
        self.calls = 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        if self.boom:
            raise OSError("tape unreachable")
        payload = self.payload

        class _Resp:
            def json(self_inner):
                return payload

        return _Resp()


def _trade(ts: float, price: float, size: float) -> dict:
    return {"timestamp": ts, "price": price, "size": size}


def test_tape_movement_and_range_counts_trades_and_calculates_range():
    # 3 trades within window (prices 0.50, 0.53, 0.51), 1 outside window
    session = _TapeSession([
        _trade(NOW - 60, 0.50, 100.0),
        _trade(NOW - 120, 0.53, 50.0),
        _trade(NOW - 300, 0.51, 80.0),
        _trade(NOW - 4000, 0.40, 500.0),  # outside 30m window
    ])

    stats = tape_movement_and_range(session, "0xmarket", window_sec=WINDOW, now_ts=NOW)

    assert stats["trade_count"] == 3
    assert stats["last_trade_sec_ago"] == 60.0
    # Range: 0.53 - 0.50 = 0.03 -> 3.0 cents
    assert stats["range_cents"] == 3.0
    # Notional: 0.50*100 + 0.53*50 + 0.51*80 = 50 + 26.5 + 40.8 = 117.3
    assert stats["movement_usd"] == 117.3


def test_tape_movement_and_range_handles_flat_tape():
    session = _TapeSession([
        _trade(NOW - 60, 0.50, 100.0),
        _trade(NOW - 120, 0.50, 50.0),
    ])

    stats = tape_movement_and_range(session, "0xmarket", window_sec=WINDOW, now_ts=NOW)

    assert stats["trade_count"] == 2
    assert stats["range_cents"] == 0.0


def test_tape_movement_and_range_handles_unreachable_tape():
    session = _TapeSession([], boom=True)
    stats = tape_movement_and_range(session, "0xmarket", window_sec=WINDOW, now_ts=NOW)

    assert stats["movement_usd"] is None
    assert stats["trade_count"] == 0
    assert stats["last_trade_sec_ago"] is None
    assert stats["range_cents"] is None


def test_velocity_gate_reject_when_disabled():
    stats = {"movement_usd": 100.0, "trade_count": 1, "last_trade_sec_ago": 600.0, "range_cents": 0.0}
    rejected, reason = velocity_gate_reject(stats, min_trades=8, max_last_trade_sec=300.0, min_range_cents=0.01, enabled=False)
    assert not rejected
    assert reason == ""


def test_velocity_gate_reject_low_trade_count():
    stats = {"movement_usd": 100.0, "trade_count": 3, "last_trade_sec_ago": 60.0, "range_cents": 2.0}
    rejected, reason = velocity_gate_reject(stats, min_trades=8, max_last_trade_sec=300.0, min_range_cents=0.01, enabled=True)
    assert rejected
    assert "low velocity: 3 trades in last 30m < 8" in reason


def test_velocity_gate_reject_stale_tape():
    stats = {"movement_usd": 100.0, "trade_count": 10, "last_trade_sec_ago": 400.0, "range_cents": 2.0}
    rejected, reason = velocity_gate_reject(stats, min_trades=8, max_last_trade_sec=300.0, min_range_cents=0.01, enabled=True)
    assert rejected
    assert "stale tape: last trade 400s ago > 300s" in reason


def test_velocity_gate_reject_flat_range():
    stats = {"movement_usd": 100.0, "trade_count": 10, "last_trade_sec_ago": 60.0, "range_cents": 0.0}
    rejected, reason = velocity_gate_reject(stats, min_trades=8, max_last_trade_sec=300.0, min_range_cents=0.01, enabled=True)
    assert rejected
    assert "flat range: price range 0.00c in last 30m < 0.01c" in reason


def test_velocity_gate_passes_active_oscillating_tape():
    stats = {"movement_usd": 100.0, "trade_count": 10, "last_trade_sec_ago": 60.0, "range_cents": 2.5}
    rejected, reason = velocity_gate_reject(stats, min_trades=8, max_last_trade_sec=300.0, min_range_cents=0.01, enabled=True)
    assert not rejected
    assert reason == ""


def test_velocity_gate_fail_open_on_unmeasured_tape():
    stats = {"movement_usd": None, "trade_count": 0, "last_trade_sec_ago": None, "range_cents": None}
    rejected, reason = velocity_gate_reject(stats, min_trades=8, max_last_trade_sec=300.0, min_range_cents=0.01, enabled=True)
    assert not rejected
    assert reason == ""


def test_evaluate_integrates_velocity_gate_rejection():
    import time as _time
    t_now = _time.time()
    # Session provides 2 trades at identical price (flat range: 0.0) with $1000 volume (> $500 bar)
    session = _TapeSession([
        _trade(t_now - 60, 0.50, 1000.0),
        _trade(t_now - 120, 0.50, 1000.0),
    ])
    m = {
        "condition_id": "0xflat",
        "question": "Will BTC reach 100k?",
        "tokens": [{"token_id": "1"}, {"token_id": "2"}],
        "rewards": {"max_spread": 3.5, "min_size": 50},
        "closed": False,
        "acceptingOrders": True,
    }

    # Enabled gate rejects on flat range
    row_rej = evaluate(
        session, rate=10.0, m=m, source="spread",
        min_trades=2, max_last_trade_sec=300.0, min_range_cents=0.01,
        velocity_gate_enabled=True,
    )
    assert not row_rej["eligible"]
    assert "flat range" in row_rej["reject_reason"]
    assert row_rej["range_cents"] == 0.0

    # Disabled gate does not reject on flat range
    row_pass = evaluate(
        session, rate=10.0, m=m, source="spread",
        min_trades=2, max_last_trade_sec=300.0, min_range_cents=0.01,
        velocity_gate_enabled=False,
    )
    # Book fetch fails because mock doesn't answer CLOB book, but reject_reason won't be flat range
    assert "flat range" not in row_pass.get("reject_reason", "")

