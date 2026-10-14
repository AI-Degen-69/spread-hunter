"""Shared fixed shadow-run fixture for #541 golden tests (Station III, T1).

One deterministic run: mixed-method closes (losses first so the equity curve opens
below starting capital), fixed-timestamp fills/quotes, one close with unmeasured PnL,
one zero-cost close, account marks that dip below starting capital.
"""
from __future__ import annotations

from pathlib import Path

from core_brain.order_registry import (
    CloseRecord,
    FillRecord,
    OrderRecord,
    OrderRegistry,
    QuoteRecord,
)

RUN_ID_541 = "shadow-541-golden"
START_CAPITAL_541 = 100.0
BASE_TS_541 = 1_700_000_000.0

# (method, realized_pnl, cost_basis) — row 5 is unmeasured, row 6 is zero-cost.
CLOSES_541: list[tuple[str, float | None, float]] = [
    ("merge", -1.20, 5.00),
    ("single_buy_exit", -0.60, 4.00),
    ("merge", 2.10, 5.00),
    ("naked_exit", 0.90, 3.00),
    ("shadow_merge", 1.50, 5.00),
    ("merge", None, 5.00),
    ("ladder_exit", 0.40, 0.00),
    ("merge", 3.20, 5.00),
]


def build_fixed_run(db: Path) -> Path:
    """Seed the fixed run into a fresh SQLite file at `db`. Returns `db`."""
    reg = OrderRegistry(db_path=db)
    for i in range(4):
        reg.log_quote(QuoteRecord(
            ts=BASE_TS_541 + i * 10.0, condition_id="0xabc", token_id="tok-up",
            side="UP", price=0.48, size=2.0, market_slug="fake-market",
            run_id=RUN_ID_541,
        ))
    for i, (method, pnl, cost) in enumerate(CLOSES_541):
        oid = f"o541-{i}"
        reg.create_order(OrderRecord(
            id=oid, condition_id="0xabc", token_id="tok-up", side="BUY",
            price=0.48, original_size=2, status="filled",
            posted_ts=int(BASE_TS_541) + i * 100,
            last_polled_ts=int(BASE_TS_541) + i * 100 + 50, run_id=RUN_ID_541,
        ))
        reg.record_fill(FillRecord(
            trade_id=f"t541-{i}", order_uuid=oid, size=1.0, price=0.48,
            venue_ts=int(BASE_TS_541) + i * 100 + 60,
            recorded_ts=int(BASE_TS_541) + i * 100 + 61, run_id=RUN_ID_541,
        ))
        proceeds = None if pnl is None else cost + pnl
        reg.log_close(CloseRecord(
            ts=BASE_TS_541 + 1000.0 + i * 100.0, condition_id="0xabc",
            market_slug="fake-market", method=method, shares=2.0,
            cost_basis=float(cost),
            proceeds=None if proceeds is None else float(proceeds),
            realized_pnl=None if pnl is None else float(pnl),
            run_id=RUN_ID_541,
        ))
    reg.log_float_mark(-0.75, 1.0, 0.0, ts=BASE_TS_541 + 500.0, run_id=RUN_ID_541)
    reg.log_float_mark(0.0, 0.0, 0.0, ts=BASE_TS_541 + 9000.0, run_id=RUN_ID_541)
    for j, v in enumerate([100.0, 98.40, 97.50, 101.30, 103.10]):
        reg.log_account_mark(
            {"account_value_usd": v, "pnl_usd": v - START_CAPITAL_541},
            ts=BASE_TS_541 + 50.0 + j * 2000.0, run_id=RUN_ID_541,
        )
    return db
