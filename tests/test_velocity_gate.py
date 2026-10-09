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


def test_tape_movement_and_range_normalizes_outcome_prices():
    """Complementary outcome prices share one frame, so a flat tape reads flat."""
    # YES trade at 0.35 and NO trade at 0.65 are economically identical in price (both imply 0.35 YES / 0.65 NO)
    session = _TapeSession([
        {"timestamp": NOW - 60, "price": 0.35, "size": 100.0, "outcome": "Yes", "outcomeIndex": 0},
        {"timestamp": NOW - 120, "price": 0.65, "size": 50.0, "outcome": "No", "outcomeIndex": 1},
    ])

    stats = tape_movement_and_range(session, "0xmarket", window_sec=WINDOW, now_ts=NOW)

    assert stats["trade_count"] == 2
    # Un-normalized range would be (0.65 - 0.35) = 30.0c.
    # Normalized range must be 0.00c!
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


def test_normal_sports_swing_passes_the_widened_range_bar():
    # #416 — a 6c range over a live 30m tape is ordinary play, not flat.
    stats = {"movement_usd": 275.0, "trade_count": 12,
             "last_trade_sec_ago": 45.0, "range_cents": 6.0}
    rejected, reason = velocity_gate_reject(
        stats, min_trades=0, max_last_trade_sec=None, min_range_cents=1.0,
        enabled=True)
    assert not rejected
    assert reason == ""


def test_sub_cent_drift_is_still_refused_at_the_widened_bar():
    # The widening is not a removal: half-a-cent drift is still flat.
    stats = {"movement_usd": 275.0, "trade_count": 12,
             "last_trade_sec_ago": 45.0, "range_cents": 0.5}
    rejected, reason = velocity_gate_reject(
        stats, min_trades=0, max_last_trade_sec=None, min_range_cents=1.0,
        enabled=True)
    assert rejected
    assert "flat range" in reason


def test_evaluate_admits_a_normal_swing_at_production_bars():
    # End to end with the production velocity bars, read from the module
    # rather than a literal: a tape swinging 1.5c on real notional is admitted
    # at the shipped 1.0c bar. The band matters -- a 1.5c range sits ABOVE the
    # shipped bar and BELOW the former 2.0c one, so this test fails if the
    # default ever climbs back to 2.0c, which a 6c tape would have hidden.
    import time as _time

    from scripts import filter_markets as fm

    t_now = _time.time()
    session = _TapeSession(
        [_trade(t_now - 60 - 60 * i, 0.52 + 0.0025 * (i % 7), 50.0)
         for i in range(10)]
    )
    m = {
        "condition_id": "0xswing",
        "question": "Will BTC reach 100k?",
        "tokens": [{"token_id": "1"}, {"token_id": "2"}],
        "rewards": {"max_spread": 3.5, "min_size": 50},
        "closed": False,
        "acceptingOrders": True,
    }
    row = evaluate(
        session, rate=10.0, m=m, source="spread",
        min_trades=0, max_last_trade_sec=None,
        min_range_cents=fm.MIN_RANGE_CENTS,
        velocity_gate_enabled=True,
    )
    assert "flat range" not in row.get("reject_reason", "")


def test_shipped_production_bars_are_the_widened_ones():
    # #416 pins the widened defaults: $200/30m movement (was $500) and
    # 1.0c range (was 2.0c), gate still enabled. Fails if anyone moves the
    # bars without updating the documented rationale.
    from scripts import filter_markets as fm

    assert fm.MIN_MOVEMENT_USD == 200.0
    assert fm.MIN_RANGE_CENTS == 1.0
    assert fm.VELOCITY_GATE_ENABLED is True


def test_evaluate_integrates_velocity_gate_rejection():
    import time as _time
    t_now = _time.time()
    # Session provides 2 trades at identical price (flat range: 0.0) with $1000 volume (> $200 bar)
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


def test_is_sports_or_esports():
    from scoring.selector import is_sports_or_esports

    # Explicit sports_market_type
    assert is_sports_or_esports(sports_market_type="moneyline")
    # Series title / league regex
    assert is_sports_or_esports(series_title="ATP Wimbledon 2026")
    assert is_sports_or_esports(title="Chiefs vs 49ers", slug="chiefs-49ers-nfl")
    assert is_sports_or_esports(category="esports", title="T1 vs Gen.G")
    assert is_sports_or_esports(title="NAVI vs FaZe - CS2 Major")
    assert is_sports_or_esports(category="gaming")
    assert is_sports_or_esports(category="sports")
    # Non-sports
    assert not is_sports_or_esports(title="Will Fed cut rates in May?", category="economics")
    assert not is_sports_or_esports(title="Bitcoin above $100k by end of year?", slug="btc-100k")
    assert not is_sports_or_esports(title="US Presidential Election Winner 2028", category="politics")


def test_volatility_config_defaults_and_env(monkeypatch):
    import scoring.config as sc

    cfg = sc.MakerConfig()
    assert cfg.select_min_range_cents == 2.0
    assert cfg.select_volatility_window_sec == 7200.0

    monkeypatch.setenv("HUNTER_VOLATILITY_WINDOW_SEC", "3600.0")
    monkeypatch.setenv("HUNTER_MIN_RANGE_CENTS", "3.5")
    loaded = sc.load()
    assert loaded.select_volatility_window_sec == 3600.0
    assert loaded.select_min_range_cents == 3.5

