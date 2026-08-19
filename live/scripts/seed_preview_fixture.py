"""Seed a live registry DB with a full Level 1 fixture for the dashboard.

The dashboard smoke test should exercise every widget, not just the account
card. This writes one coherent run with:

* 8 closes (5 wins, 3 losses) so win rate, expectancy, the P&L histogram,
  Sharpe/Sortino, reward:risk, profit factor, and drawdown all have real data;
* 2 balanced pairs (UP + DOWN legs, both filled) + 4 fills + 4 matured
  markouts, so maker fill rate, wait-to-fill, queue, spread capture, and the
  adverse-selection bell curve all have a numerator and a denominator -- and no
  naked-leg alarm fires, because every leg is hedged;
* 8 market_events (6 quoting, 2 blocked) so quote uptime and skips render;
* 3 float marks plus one synthetic venue sweep (account_marks) so the exposure
  chart, account value, collateral/positions basis, unrealized, and committed
  tiles all render.

The account_marks row is clearly labelled `source = "fixture"`: it is synthetic
venue data for a preview, never a claim the real venue said it.

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
    MarketEventRecord,
    MarkoutRecord,
    OrderRecord,
    OrderRegistry,
    QuoteRecord,
)

RUN_ID = "run-preview-seed"


def seed(reg: OrderRegistry, now: float | None = None) -> str:
    """Seed one coherent, fully-populated run into `reg` and return its run_id."""
    now = now if now is not None else time.time()
    # Closes and float marks live on a day-long history so the equity curve and
    # exposure chart have a shape; the live order book, quotes, fills, markouts,
    # and the venue sweep are anchored to `now` so nothing reads as stale.
    t0 = now - 86400
    now_ms = int(now * 1000)

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

    # --- 2 balanced pairs: UP + DOWN legs, both filled -------------------
    # Each pair's two legs match exactly, so the dashboard classifies them as
    # BALANCED (a normal hedged state), never NAKED.
    # Drift = mid_h2 - fill_price: three of four go against us (net adverse).
    pairs = [
        # (pair_id, slug, up_token, up_px, up_mid2, dn_token, dn_px, dn_mid2)
        ("pair-1", "mlb-chc-chw", "tok-up-a", 0.62, 0.60, "tok-dn-a", 0.38, 0.37),
        ("pair-2", "ufc-jones-smith", "tok-up-b", 0.55, 0.53, "tok-dn-b", 0.45, 0.46),
    ]
    order_index = 0
    for pair_id, slug, up_tok, up_px, up_mid2, dn_tok, dn_px, dn_mid2 in pairs:
        for tok, px, mid2, leg in ((up_tok, up_px, up_mid2, "UP"),
                                   (dn_tok, dn_px, dn_mid2, "DOWN")):
            order_index += 1
            order_id = f"o{order_index}"
            reg.create_order(OrderRecord(
                id=order_id, condition_id=f"0x{slug}", token_id=tok,
                side="BUY", price=px, original_size=5.0, status="filled",
                posted_ts=now_ms - 30000, last_polled_ts=now_ms - 2000,
                pair_id=pair_id, run_id=RUN_ID,
            ))
            reg.record_fill(FillRecord(
                trade_id=f"t{order_index}", order_uuid=order_id, size=5.0,
                price=px, venue_ts=now_ms - 27000, recorded_ts=now_ms - 25000,
                run_id=RUN_ID,
            ))
            # A quote with the fill's order id as local_id, posted 3s before the
            # fill, so wait-to-fill, queue, and spread capture all resolve.
            reg.log_quote(QuoteRecord(
                ts=now - 30, condition_id=f"0x{slug}", token_id=tok,
                side="BUY", price=px, size=5.0, market_slug=slug,
                queue_ahead=float(order_index), mid=px - 0.01,
                edge_vs_mid=0.01, t_remaining=600.0, filled=5.0,
                local_id=order_id, latency_ms=45.0, run_id=RUN_ID,
            ))
            reg.log_markout(MarkoutRecord(
                ts=now - 30, condition_id=f"0x{slug}", market_slug=slug,
                side="BUY", token_id=tok, fill_price=px, size=5.0,
                ref_mid=px, ref_mid_source="sampled",
                mid_h2=mid2, done=1, run_id=RUN_ID,
            ))

    # --- 8 market events: 6 quoting, 2 blocked ---------------------------
    # quote_uptime = 6/8 = 75%; the two blocked events feed the skips tile.
    for i in range(6):
        reg.log_market_event(MarketEventRecord(
            ts=now - 120 + i * 15, condition_id=f"0x{slug}", kind="QUOTING",
            market_slug=slug, reason="posted pair", reason_code="INTENT_GENERATED",
            run_id=RUN_ID,
        ))
    reg.log_market_event(MarketEventRecord(
        ts=now - 90, condition_id="0xblocked-wide", kind="BLOCKED",
        market_slug="blocked-wide", reason="spread too wide",
        reason_code="SPREAD_WIDE", run_id=RUN_ID,
    ))
    reg.log_market_event(MarketEventRecord(
        ts=now - 60, condition_id="0xblocked-band", kind="BLOCKED",
        market_slug="blocked-band", reason="outside price band",
        reason_code="PRICE_BAND", run_id=RUN_ID,
    ))

    # --- 3 float marks: naked exposure peaks at 3.20 --------------------
    reg.log_float_mark(unrealized_usd=1.25, committed_open_usd=9.60,
                       naked_usd=0.00, ts=t0 + 100, run_id=RUN_ID)
    reg.log_float_mark(unrealized_usd=-0.40, committed_open_usd=9.60,
                       naked_usd=3.20, ts=t0 + 420, run_id=RUN_ID)
    reg.log_float_mark(unrealized_usd=0.65, committed_open_usd=9.60,
                       naked_usd=1.00, ts=t0 + 900, run_id=RUN_ID)

    # --- 1 synthetic venue sweep -----------------------------------------
    # Clearly labelled `source = "fixture"`: this stands in for what the real
    # account-sweep would write after reading Polymarket's /value, /positions,
    # and /closed-positions endpoints.
    reg.log_account_mark({
        "collateral_usd": 96.38,
        "positions_value_usd": 4.70,
        "account_value_usd": 101.08,
        "pnl_usd": 0.20,
        "pnl_pct": 0.20,
        "pnl_closed_usd": 0.20,
        "pnl_series_usd": 0.20,
        "unrealized_usd": 0.65,
        "committed_usd": 9.60,
        "open_positions_count": 2,
        "closed_positions_count": 8,
        "source": "fixture",
    }, ts=now, run_id=RUN_ID)

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
