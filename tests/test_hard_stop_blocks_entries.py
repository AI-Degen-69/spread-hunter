"""#796: a hard stop blocks fresh entries for the rest of the run.

After a `lifecycle_hard_stop` close the condition keeps its lifecycle row
(residual shares stay managed) but fresh entry quotes must not reach it
again until a newer qualifying publication re-qualifies it. Managed pairs
on the same condition keep hedging/merging, and the block lives in the
registry so it survives a restart.
"""

from __future__ import annotations

import time

from core_brain.order_registry import OrderRegistry
from tests.test_hard_stop_reset import (
    COND,
    NOW,
    PAIR,
    TOK_DN,
    TOK_UP,
    ProvingVenue,
    _balances,
    _cfg,
    _seed_pair,
)
from core_brain import single_buy_saver as lp
from core_brain.market_resolution import MarketEndState


def _route(registry: OrderRegistry, venue, **kw):
    results = lp.manage_single_leg_positions(
        venue, registry, _cfg(), live=True, now=NOW / 1000.0 + 10.0,
        venue_positions={TOK_UP: 10.0, TOK_DN: 0.0},
        market_state_fn=lambda cid: MarketEndState(
            condition_id=COND, closed=False, end_ts=NOW / 1000.0 + 600.0,
            end_date_iso=None, end_date_passed=False, accepting_orders=True,
            resolved=False, unreachable=False),
        **kw)
    assert len(results) == 1
    return results[0]


def test_hard_stop_with_residual_blocks_fresh_entries_but_keeps_row(
        tmp_path, monkeypatch):
    """#796: RESIDUAL keeps the row AND leaves an awaiting block."""
    from pathlib import Path

    db = tmp_path / "live.db"
    registry = OrderRegistry(db_path=db)
    _balances(monkeypatch, [{"asset": TOK_UP, "size": 5.0}])
    _seed_pair(registry)

    routed = _route(registry, ProvingVenue(), funder="0xfunder")

    assert routed["action"] == "exited"
    assert routed["reset_status"] == "RESIDUAL"
    # The row survives so the remainder stays managed ...
    assert registry.get_lifecycle_state(PAIR).state == "HARD_STOP"
    # ... but fresh entries are blocked for the rest of the run.
    marks = registry.requalification_marks()
    assert COND.lower() in marks
    assert marks[COND.lower()].status == "awaiting"
    assert "hard_stop" in marks[COND.lower()].reason

    # The block survives a process restart inside the run (registry, not memory).
    reopened = OrderRegistry(db_path=db)
    assert COND.lower() in reopened.requalification_marks()


def test_trader_final_gate_blocks_fresh_but_keeps_hedge(tmp_path):
    """#796: entry gate drops fresh submits, keeps lifecycle-context hedges."""
    import uuid

    from core_brain.order_registry import LifecycleStateRecord
    from core_brain.trader_loop import _enforce_entry_gate

    registry = OrderRegistry(db_path=tmp_path / "t.db")
    # Simulate the post-stop state: row current + awaiting hard-stop block.
    registry.save_lifecycle_state(LifecycleStateRecord(
        pair_id="pair-1", condition_id=COND, state="HARD_STOP",
        reason="seed", updated_ts_ms=1_000, evidence_json="{}"))
    now_ms = int(time.time() * 1000)
    registry.mark_hard_stop_block(COND, "hard_stop_exited", now_ms)

    class _Intent:
        def __init__(self, pair_id=None):
            self.pair_id = pair_id

    class _Seam:
        def __init__(self, reg):
            self.registry = reg

    seam = _Seam(registry)
    fresh = _Intent()
    hedge = _Intent(pair_id="pair-1")

    kept, blocked = _enforce_entry_gate(seam, COND, [fresh], None)
    assert kept == []
    assert blocked is True

    kept, blocked = _enforce_entry_gate(seam, COND, [hedge, fresh], "pair-1")
    assert kept == [hedge]
    assert blocked is True

    # A newer qualifying publication still clears the block (existing gate).
    gen = registry.requalification_marks()[COND.lower()].generation
    assert registry.clear_requalification(COND, gen) is True
    kept, blocked = _enforce_entry_gate(seam, COND, [fresh], None)
    assert kept == [fresh]
    assert blocked is False
