"""Seed a live registry DB with a full Level 1 fixture for the dashboard.

The dashboard smoke test needs every Level 1 widget exercised, not just the
account card. This writes one coherent run with:

* 8 closes (5 wins, 3 losses) so win rate, expectancy, the P&L histogram,
  Sharpe/Sortino, reward:risk, profit factor, and drawdown all have real data;
* 4 orders + 4 fills + 4 matured markouts so the adverse-selection bell curve
  has a size-weighted mean drift and a non-zero sample count;
* 3 float marks so the exposure chart and inventory-risk tile have data.

Run it as `python live/scripts/seed_preview_fixture.py <db-path>` and then
point the dashboard at that path with `--db`. The script is importable too, so
the fixture is reproducible from tests via `seed(OrderRegistry(db_path))`.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

# live/, one level up from live/scripts/. `python live/scripts/<file>.py` puts
# live/scripts/ on sys.path, not live/, so add it the way live_dash.py does.
LIVE_ROOT = Path(__file__).resolve().parent.parent
if str(LIVE_ROOT) not in sys.path:
    sys.path.insert(0, str(LIVE_ROOT))

from engine.order_registry import (  # noqa: E402
    CloseRecord,
    FillRecord,
    MarkoutRecord,
    OrderRecord,
    OrderRegistry,
)

RUN_ID = "run-preview-seed"


def seed(reg: OrderRegistry, now: float | None = None) -> str:
    """Seed one coherent run into `reg` and return its run_id."""
    now = now if now is not None else time.time()
    t0 = now - 86400

    # --- 8 closes: 5 wins, 3 losses -------------------------------------
    # One trade is a -100% full loss on a small cost basis, so the equal-weighted
    # mean return is negative while the dollar expectancy stays positive -- the
    # case the distribution tile's dollar headline exists to disambiguate.
    closes = [
        (0,    "mlb-chc-chw-merge",  "merge", 5.0, 5.00, 6.50,  1.50),
        (120,  "nfl-gb-chi-merge",  "merge", 5.0, 4.70, 5.00,  0.30),
        (240,  "nba-lal-den-exit",  "sell",  5.0, 4.00, 3.00, -1.00),
        (360,  "mlb-chw-chc-merge", "merge", 5.0, 3.10, 0.00, -3.10),
        (480,  "ufc-jones-smith",   "merge", 5.0, 4.50, 6.50,  2.00),
        (600,  "soccer-ars-mci",    "sell",  5.0, 4.00, 3.40, -0.60),
        (720,  "nhl-nyr-bos",       "merge", 5.0, 4.90, 5.90,  1.00),
        (840,  "mlb-nyy-bos",       "merge", 5.0, 4.90, 5.00,  0.10),
    ]
    for off, slug, method, shares, cost, proceeds, pnl in closes:
        reg.log_close(CloseRecord(
            ts=t0 + off, condition_id=f"0x{slug}", market_slug=slug,
            method=method, shares=shares, cost_basis=cost,
            proceeds=proceeds, realized_pnl=pnl, run_id=RUN_ID,
        ))

    # --- 4 orders + fills + matured markouts ----------------------------
    # Fills give the adverse-selection denominator; markouts give the numerator.
    # Drift = mid_later - fill_price, so three of four go against us (net
    # adverse), which is the realistic maker outcome.
    markouts = [
        # (order_id, slug, token_id, fill_price, mid_h2)
        ("o1", "mlb-chc-chw-merge", "tok-up", 0.62, 0.60),
        ("o2", "nfl-gb-chi-merge",  "tok-dn", 0.38, 0.37),
        ("o3", "ufc-jones-smith",   "tok-up", 0.55, 0.53),
        ("o4", "nhl-nyr-bos",       "tok-dn", 0.45, 0.46),
    ]
    for i, (order_id, slug, token_id, fill_price, mid_h2) in enumerate(markouts):
        reg.create_order(OrderRecord(
            id=order_id, condition_id=f"0x{slug}", token_id=token_id,
            side="BUY", price=fill_price, original_size=5.0,
            status="filled", posted_ts=int(t0 + i * 120),
            last_polled_ts=int(t0 + i * 120 + 5), run_id=RUN_ID,
        ))
        reg.record_fill(FillRecord(
            trade_id=f"t{i + 1}", order_uuid=order_id, size=5.0,
            price=fill_price, venue_ts=int((t0 + i * 120) * 1000),
            recorded_ts=int((t0 + i * 120 + 2) * 1000), run_id=RUN_ID,
        ))
        reg.log_markout(MarkoutRecord(
            ts=t0 + i * 120, condition_id=f"0x{slug}", market_slug=slug,
            side="BUY", token_id=token_id, fill_price=fill_price, size=5.0,
            ref_mid=fill_price, ref_mid_source="sampled",
            mid_h2=mid_h2, done=1, run_id=RUN_ID,
        ))

    # --- 3 float marks: naked exposure peaks at 3.20 --------------------
    reg.log_float_mark(unrealized_usd=1.25, committed_open_usd=9.60,
                       naked_usd=0.00, ts=t0 + 100, run_id=RUN_ID)
    reg.log_float_mark(unrealized_usd=-0.40, committed_open_usd=9.60,
                       naked_usd=3.20, ts=t0 + 420, run_id=RUN_ID)
    reg.log_float_mark(unrealized_usd=0.65, committed_open_usd=9.60,
                       naked_usd=1.00, ts=t0 + 900, run_id=RUN_ID)

    return RUN_ID


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db_path", help="Registry DB to seed (created if missing)")
    args = parser.parse_args()

    db_path = Path(args.db_path)
    if db_path.exists():
        db_path.unlink()
    reg = OrderRegistry(db_path)
    run_id = seed(reg)
    print(f"seeded {run_id!r} into {db_path.resolve()}")


if __name__ == "__main__":
    main()
