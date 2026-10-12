"""A live merge close must price its legs from THIS run's fills, not history.

`_resolve_merge_unit_costs` averaged fills over every round that ever
touched the condition (`WHERE o.condition_id = ?`, no run scope). The
production registry is shared across runs, so re-trading a condition blends
a fully-closed prior round's fills into this merge's unit costs -- the live
version of the suspicion `test_shadow_merge_run_scope` closed for rehearsal.

Scoping is safe here because adoption re-stamps: reconcile writes adopted
fills with the adopting run's id (`order_registry.py`, the `FillRecord(`
calls with `run_id=registry._run_id()`), so the merging run always owns
what it merges. Fills with NULL run_id (rows predating the column) stay
included -- excluding legacy inventory would be worse than blending it.
"""

from __future__ import annotations

import time

from core_brain.order_registry import (
    FillRecord,
    OrderRecord,
    OrderRegistry,
    QuoteRecord,
    init_db,
)

COND = "0xmergerunscope"
DN_TOKEN = "1234567890123456789012345678901234567890"
UP_TOKEN = "9876543210987654321098765432109876543210"


def _seed_run(reg: OrderRegistry, run: str | None, up_price: float,
              dn_price: float, shares=6.0, tag=""):
    now = int(time.time() * 1000)
    reg.create_order(OrderRecord(
        id=f"o-dn-{tag}", condition_id=COND, token_id=DN_TOKEN, side="BUY",
        price=dn_price, original_size=shares, status="filled",
        posted_ts=now, last_polled_ts=now, run_id=run))
    reg.record_fill(FillRecord(trade_id=f"f-dn-{tag}",
                               order_uuid=f"o-dn-{tag}",
                               size=shares, price=dn_price, run_id=run))
    reg.create_order(OrderRecord(
        id=f"o-up-{tag}", condition_id=COND, token_id=UP_TOKEN, side="BUY",
        price=up_price, original_size=shares, status="filled",
        posted_ts=now, last_polled_ts=now, run_id=run))
    reg.record_fill(FillRecord(trade_id=f"f-up-{tag}",
                               order_uuid=f"o-up-{tag}",
                               size=shares, price=up_price, run_id=run))


def _seed_quotes(reg: OrderRegistry):
    reg.log_quote(QuoteRecord(
        ts=time.time(), condition_id=COND, token_id=DN_TOKEN, side="DOWN",
        price=0.5, size=6.0, run_id="run-B"))
    reg.log_quote(QuoteRecord(
        ts=time.time(), condition_id=COND, token_id=UP_TOKEN, side="UP",
        price=0.5, size=6.0, run_id="run-B"))


def _unit_costs(reg: OrderRegistry, run_id=None):
    from core_brain.order_manager import _resolve_merge_unit_costs
    with reg._conn() as conn:
        return _resolve_merge_unit_costs(conn, COND, run_id=run_id)


class TestLiveMergeRunScope:
    def test_prior_round_fills_do_not_blend(self, tmp_path):
        db = tmp_path / "live.db"
        init_db(db)
        reg = OrderRegistry(db_path=db)
        _seed_run(reg, "run-A", up_price=0.30, dn_price=0.60, tag="a")
        _seed_run(reg, "run-B", up_price=0.50, dn_price=0.50, tag="b")
        _seed_quotes(reg)
        up, dn = _unit_costs(reg, run_id="run-B")
        assert round(up, 4) == round(0.50, 4)
        assert round(dn, 4) == round(0.50, 4)

    def test_unscoped_call_keeps_old_behavior(self, tmp_path):
        # No run given (tests, one-off scripts): history average, as before.
        db = tmp_path / "live.db"
        init_db(db)
        reg = OrderRegistry(db_path=db)
        _seed_run(reg, "run-A", up_price=0.30, dn_price=0.60, tag="a")
        _seed_run(reg, "run-B", up_price=0.50, dn_price=0.50, tag="b")
        _seed_quotes(reg)
        up, dn = _unit_costs(reg)
        assert round(up, 4) == round(0.40, 4)
        assert round(dn, 4) == round(0.55, 4)

    def test_restart_orphaned_fills_fall_back_to_history(self, tmp_path):
        # Restart mid-position: the new run holds no fills, the defunct run
        # holds them all. Mirroring kpi.report, history wins over invented
        # 0.49 defaults -- said out loud, not silently.
        db = tmp_path / "live.db"
        init_db(db)
        reg = OrderRegistry(db_path=db)
        _seed_run(reg, "run-A", up_price=0.30, dn_price=0.60, tag="a")
        _seed_quotes(reg)
        up, dn = _unit_costs(reg, run_id="run-B")
        assert round(up, 4) == round(0.30, 4)
        assert round(dn, 4) == round(0.60, 4)

    def test_legacy_fills_without_run_id_still_count(self, tmp_path):
        # run_id arrived by migration (ALTER TABLE fills ADD COLUMN), so
        # production holds pre-column rows with NULL. Model them directly:
        # the record API always stamps a run id and cannot produce NULL.
        db = tmp_path / "live.db"
        init_db(db)
        reg = OrderRegistry(db_path=db)
        _seed_run(reg, None, up_price=0.252, dn_price=0.618, tag="legacy")
        with reg._conn() as conn:
            conn.execute("UPDATE fills SET run_id = NULL")
            conn.commit()
        _seed_quotes(reg)
        up, dn = _unit_costs(reg, run_id="run-B")
        assert round(up, 4) == round(0.252, 4)
        assert round(dn, 4) == round(0.618, 4)
