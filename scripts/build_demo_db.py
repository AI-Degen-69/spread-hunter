#!/usr/bin/env python3
"""Build a bogus demo registry that exercises every dashboard scenario.

Writes a fresh SQLite registry through the same ``OrderRegistry`` writers the
live trader loop uses, so the dashboard's read path (``summarize_state``, the
KPI report, the analytics surface) sees rows shaped exactly like production
ones -- just with invented numbers. Every order scenario the Market
Inspection table can render gets a row, every trade scenario the closed
trades view can render gets a close, and the quote / fill / markout /
account-mark series are long enough for the analytics metrics to compute
real values instead of "unmeasured".

Usage::

    python scripts/build_demo_db.py                       # -> data/demo_orders.db
    python scripts/build_demo_db.py --out other.db --force
    python -m dashboard.server --db data/demo_orders.db   # load it

Safety: refuses to write the production registry (``DEFAULT_DB_PATH``) and
refuses to overwrite an existing output file without ``--force``. The built
database is tagged ``run_id=demo-bogus`` so a stray demo row is greppable.

One scenario (the ghost fill) is written with a raw SQLite insert and
``PRAGMA foreign_keys = OFF``, deliberately: the registry enforces
``fills.order_uuid -> orders.id``, so a fill with no order behind it cannot
go through the normal writer. That is exactly the corrupted-or-imported
store the dashboard must survive, so the demo must contain one.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core_brain.order_registry import (  # noqa: E402
    DEFAULT_DB_PATH,
    CloseRecord,
    DivergenceEventRecord,
    FillRecord,
    HedgeCensusRecord,
    LifecycleStateRecord,
    MarketEventRecord,
    MarkoutRecord,
    OrderRecord,
    OrderRegistry,
    QuoteRecord,
    ResolutionRecord,
    VenueErrorRecord,
    init_db,
)

DEMO_RUN_ID = "demo-bogus"
DEFAULT_OUT = ROOT / "data" / "demo_orders.db"


class DemoRefused(RuntimeError):
    """The builder was pointed somewhere it must not write."""


# --------------------------------------------------------------------------
# Time helpers. The registry mixes units: orders and fills carry integer
# milliseconds, quotes / closes / markouts / account marks carry float
# seconds. Every scenario below goes through these two helpers so a row is
# never written in the wrong unit.
# --------------------------------------------------------------------------

def _ms(seconds: int) -> int:
    """Seconds since epoch -> integer milliseconds, the orders/fills unit."""
    return seconds * 1000


def _market(slug: str, idx: int) -> dict[str, str]:
    """Deterministic fake venue ids for a demo market.

    The condition id is a 64-hex-digit string; the token ids are the
    23-digit integers the venue uses. Nothing here resolves on the venue --
    these are display-only identifiers.
    """
    return {
        "slug": slug,
        "condition_id": f"0x{idx:064x}",
        "up_token": f"{90000000000000000000000 + idx * 2 + 1}",
        "dn_token": f"{90000000000000000000000 + idx * 2 + 2}",
    }


# --------------------------------------------------------------------------
# Order lifecycle helpers. These mirror the trader loop's real sequence:
# create pending -> attach venue id -> (fill | cancel) -> final status.
# --------------------------------------------------------------------------

def _post(
    reg: OrderRegistry,
    *,
    local_id: str,
    venue_id: str | None,
    market: dict[str, str],
    leg: str,
    price: float,
    size: float,
    posted_ts: int,
    polled_ts: int,
    pair_id: str | None = None,
    bot_pos_price_max_pair_cost_usd: float | None = None,
) -> None:
    """Create an order and attach its venue id, as the trader loop does.

    ``venue_id=None`` leaves the order in ``pending`` -- the
    never-acknowledged case.
    """
    token_id = market["up_token"] if leg == "UP" else market["dn_token"]
    reg.create_order(
        OrderRecord(
            id=local_id,
            condition_id=market["condition_id"],
            token_id=token_id,
            side="BUY",
            price=price,
            original_size=size,
            status="pending",
            posted_ts=_ms(posted_ts),
            last_polled_ts=_ms(polled_ts),
            pair_id=pair_id,
            max_pair_cost_at_post=bot_pos_price_max_pair_cost_usd,
        )
    )
    if venue_id is not None:
        reg.attach_venue_order_id(
            local_id, venue_id, status="open", last_polled_ts=_ms(polled_ts)
        )


def _quote(
    reg: OrderRegistry,
    *,
    market: dict[str, str],
    leg: str,
    price: float,
    size: float,
    ts: int,
    local_id: str,
    venue_id: str | None,
    queue_ahead: float | None,
    mid: float,
    t_remaining: float,
    latency_ms: float,
) -> None:
    """Log a quote intent with the telemetry the queue metrics read.

    `side` is the LEG label ('UP'/'DOWN'), the same value production
    writes: the Active Markets tab normalizes `quotes.side` into legs and
    a 'BUY' row is dropped, leaving YES PRICE / NO PRICE / EDGE empty.

    Logged with ``filled=0``; when a fill follows, ``record_fill``
    attributes it to this quote automatically, exactly like production.
    """
    token_id = market["up_token"] if leg == "UP" else market["dn_token"]
    reg.log_quote(
        QuoteRecord(
            ts=float(ts),
            market_slug=market["slug"],
            condition_id=market["condition_id"],
            token_id=token_id,
            side=leg,
            price=price,
            size=size,
            queue_ahead=queue_ahead,
            mid=mid,
            edge_vs_mid=round(mid - price, 4),
            t_remaining=t_remaining,
            filled=0.0,
            order_id=venue_id,
            local_id=local_id,
            latency_ms=latency_ms,
        )
    )


def _fill(
    reg: OrderRegistry,
    *,
    local_id: str,
    trade_id: str,
    size: float,
    price: float,
    venue_ts: int,
    status: str = "filled",
) -> None:
    """Record a fill and move the order to its terminal/partial status."""
    reg.record_fill(
        FillRecord(
            trade_id=trade_id,
            order_uuid=local_id,
            size=size,
            price=price,
            venue_ts=_ms(venue_ts),
            recorded_ts=_ms(venue_ts),
        )
    )
    reg.update_order_status(local_id, status, _ms(venue_ts))


# --------------------------------------------------------------------------
# Scenarios. One function per scenario so an operator can add a case
# without touching the others. Each returns a one-line description printed
# in the build report.
# --------------------------------------------------------------------------

def scenario_resting_pair(reg: OrderRegistry, t: int, out: Path) -> str:
    """Two resting orders on one market -- the RESTING hedge state."""
    m = _market("btc-150k-oct", 1)
    _post(reg, local_id="loc-1-up", venue_id="0xvenue0001up", market=m, leg="UP",
          price=0.46, size=20, posted_ts=t - 600, polled_ts=t - 45,
          pair_id="pair-1", bot_pos_price_max_pair_cost_usd=0.98)
    _quote(reg, market=m, leg="UP", price=0.46, size=20, ts=t - 600,
           local_id="loc-1-up", venue_id="0xvenue0001up", queue_ahead=1200.0,
           mid=0.48, t_remaining=5400.0, latency_ms=62.0)
    _post(reg, local_id="loc-1-dn", venue_id="0xvenue0001dn", market=m, leg="DOWN",
          price=0.49, size=20, posted_ts=t - 598, polled_ts=t - 45,
          pair_id="pair-1", bot_pos_price_max_pair_cost_usd=0.98)
    _quote(reg, market=m, leg="DOWN", price=0.49, size=20, ts=t - 598,
           local_id="loc-1-dn", venue_id="0xvenue0001dn", queue_ahead=3400.0,
           mid=0.50, t_remaining=5400.0, latency_ms=58.0)
    return "resting pair ........ btc-150k-oct: 2 open orders (RESTING)"


def scenario_naked_single_leg(reg: OrderRegistry, t: int, out: Path) -> str:
    """One filled leg with no other side -- the NAKED hedge state, plus a
    rescue close that exercises the adverse-drift audit pair."""
    m = _market("eth-flip-nov", 2)
    _post(reg, local_id="loc-2-up", venue_id="0xvenue0002up", market=m, leg="UP",
          price=0.44, size=15, posted_ts=t - 1500, polled_ts=t - 900,
          pair_id="pair-2", bot_pos_price_max_pair_cost_usd=0.97)
    _quote(reg, market=m, leg="UP", price=0.44, size=15, ts=t - 1500,
           local_id="loc-2-up", venue_id="0xvenue0002up", queue_ahead=80.0,
           mid=0.465, t_remaining=7200.0, latency_ms=41.0)
    _fill(reg, local_id="loc-2-up", trade_id="trade-2-up", size=15,
          price=0.435, venue_ts=t - 900)
    reg.save_lifecycle_state(LifecycleStateRecord(
        pair_id="pair-2", condition_id=m["condition_id"], state="naked",
        reason="single_buy_filled_second_leg_declined",
        updated_ts_ms=_ms(t - 880),
        evidence_json='{"up_filled": true, "dn_declined": true, '
                      '"naked_usd": 6.53}',
    ))
    # Rescue exit: the adverse-drift route on the single-buy exit path,
    # with the hard-stop audit pair. `single_buy_exit` is the recorded
    # method that puts the close in the rescue bucket.
    reg.log_close(CloseRecord(
        ts=float(t - 600), condition_id=m["condition_id"],
        market_slug=m["slug"], method="single_buy_exit", gas=0.02, shares=15,
        up_price=0.29, dn_price=0.71, cost_basis=6.53, proceeds=4.35,
        fee=0.45, realized_pnl=-1.80, reason="adverse_drift",
        trigger_bid=0.31, fresh_bid=0.29,
        tx_hash="0xdeadbeef2",
    ))
    reg.log_markout(MarkoutRecord(
        ts=float(t - 900), condition_id=m["condition_id"],
        market_slug=m["slug"], side="BUY", token_id=m["up_token"],
        fill_price=0.435, size=15, ref_mid=0.465, ref_mid_source="clean",
        mid_h0=0.458, mid_h1=0.451, mid_h2=0.443, mid_h3=0.437, done=1,
    ))
    return "naked single leg ... eth-flip-nov: filled UP, no other side + rescue close (NAKED)"


def scenario_balanced_pair(reg: OrderRegistry, t: int, out: Path) -> str:
    """Both legs filled, not yet merged -- the BALANCED hedge state."""
    m = _market("fed-cut-dec", 3)
    _post(reg, local_id="loc-3-up", venue_id="0xvenue0003up", market=m, leg="UP",
          price=0.46, size=10, posted_ts=t - 2400, polled_ts=t - 2100,
          pair_id="pair-3", bot_pos_price_max_pair_cost_usd=0.98)
    _quote(reg, market=m, leg="UP", price=0.46, size=10, ts=t - 2400,
           local_id="loc-3-up", venue_id="0xvenue0003up", queue_ahead=540.0,
           mid=0.47, t_remaining=10800.0, latency_ms=88.0)
    _fill(reg, local_id="loc-3-up", trade_id="trade-3-up", size=10,
          price=0.458, venue_ts=t - 2100)
    _post(reg, local_id="loc-3-dn", venue_id="0xvenue0003dn", market=m, leg="DOWN",
          price=0.51, size=10, posted_ts=t - 2395, polled_ts=t - 2050,
          pair_id="pair-3", bot_pos_price_max_pair_cost_usd=0.98)
    _quote(reg, market=m, leg="DOWN", price=0.51, size=10, ts=t - 2395,
           local_id="loc-3-dn", venue_id="0xvenue0003dn", queue_ahead=2100.0,
           mid=0.51, t_remaining=10800.0, latency_ms=91.0)
    _fill(reg, local_id="loc-3-dn", trade_id="trade-3-dn", size=10,
          price=0.508, venue_ts=t - 2050)
    return "balanced pair ...... fed-cut-dec: both legs filled, awaiting merge (BALANCED)"


def scenario_merged_pair(reg: OrderRegistry, t: int, out: Path) -> str:
    """Both legs filled and merged -- orders display as 'merged'."""
    m = _market("spacex-starship", 4)
    _post(reg, local_id="loc-4a-up", venue_id="0xvenue0004aup", market=m, leg="UP",
          price=0.45, size=12, posted_ts=t - 7200, polled_ts=t - 7000,
          pair_id="pair-4a", bot_pos_price_max_pair_cost_usd=0.98)
    _quote(reg, market=m, leg="UP", price=0.45, size=12, ts=t - 7200,
           local_id="loc-4a-up", venue_id="0xvenue0004aup", queue_ahead=310.0,
           mid=0.49, t_remaining=21600.0, latency_ms=55.0)
    _fill(reg, local_id="loc-4a-up", trade_id="trade-4a-up", size=12,
          price=0.448, venue_ts=t - 7000)
    _post(reg, local_id="loc-4a-dn", venue_id="0xvenue0004adn", market=m, leg="DOWN",
          price=0.52, size=12, posted_ts=t - 7190, polled_ts=t - 6950,
          pair_id="pair-4a", bot_pos_price_max_pair_cost_usd=0.98)
    _quote(reg, market=m, leg="DOWN", price=0.52, size=12, ts=t - 7190,
           local_id="loc-4a-dn", venue_id="0xvenue0004adn", queue_ahead=880.0,
           mid=0.51, t_remaining=21600.0, latency_ms=74.0)
    _fill(reg, local_id="loc-4a-dn", trade_id="trade-4a-dn", size=12,
          price=0.519, venue_ts=t - 6950)
    reg.log_close(CloseRecord(
        ts=float(t - 6000), condition_id=m["condition_id"],
        market_slug=m["slug"], method="merge", gas=0.01, shares=12,
        up_price=0.448, dn_price=0.519, cost_basis=11.60, proceeds=12.00,
        fee=0.0, realized_pnl=0.40, up_cost_removed=5.38, dn_cost_removed=6.23,
        tx_hash="0xdeadbeef4a",
    ))
    # A second merged pair in the same market: two data points for the
    # fill-path metrics instead of one.
    _post(reg, local_id="loc-4b-up", venue_id="0xvenue0004bup", market=m, leg="UP",
          price=0.43, size=8, posted_ts=t - 6800, polled_ts=t - 6600,
          pair_id="pair-4b", bot_pos_price_max_pair_cost_usd=0.96)
    _quote(reg, market=m, leg="UP", price=0.43, size=8, ts=t - 6800,
           local_id="loc-4b-up", venue_id="0xvenue0004bup", queue_ahead=150.0,
           mid=0.47, t_remaining=21600.0, latency_ms=49.0)
    _fill(reg, local_id="loc-4b-up", trade_id="trade-4b-up", size=8,
          price=0.428, venue_ts=t - 6600)
    _post(reg, local_id="loc-4b-dn", venue_id="0xvenue0004bdn", market=m, leg="DOWN",
          price=0.50, size=8, posted_ts=t - 6790, polled_ts=t - 6550,
          pair_id="pair-4b", bot_pos_price_max_pair_cost_usd=0.96)
    _quote(reg, market=m, leg="DOWN", price=0.50, size=8, ts=t - 6790,
           local_id="loc-4b-dn", venue_id="0xvenue0004bdn", queue_ahead=640.0,
           mid=0.53, t_remaining=21600.0, latency_ms=63.0)
    _fill(reg, local_id="loc-4b-dn", trade_id="trade-4b-dn", size=8,
          price=0.498, venue_ts=t - 6550)
    reg.log_close(CloseRecord(
        ts=float(t - 5500), condition_id=m["condition_id"],
        market_slug=m["slug"], method="merge", gas=0.01, shares=8,
        up_price=0.428, dn_price=0.498, cost_basis=7.41, proceeds=8.00,
        fee=0.0, realized_pnl=0.59, up_cost_removed=3.42, dn_cost_removed=3.98,
        tx_hash="0xdeadbeef4b",
    ))
    return "merged pair ........ spacex-starship: 2 filled pairs + merge closes (display 'merged')"


def scenario_cancelled_order(reg: OrderRegistry, t: int, out: Path) -> str:
    """A cancelled order that defended the pair-cost gate, with the cancel
    reason and queue position recorded."""
    m = _market("nba-finals-2027", 5)
    _post(reg, local_id="loc-5-up", venue_id="0xvenue0005up", market=m, leg="UP",
          price=0.55, size=20, posted_ts=t - 1800, polled_ts=t - 300,
          pair_id="pair-5", bot_pos_price_max_pair_cost_usd=0.98)
    _quote(reg, market=m, leg="UP", price=0.55, size=20, ts=t - 1800,
           local_id="loc-5-up", venue_id="0xvenue0005up", queue_ahead=15638.0,
           mid=0.52, t_remaining=43200.0, latency_ms=120.0)
    reg.update_order_status("loc-5-up", "cancelled", _ms(t - 300),
                            cancel_reason="pair_cost_regate",
                            cancel_queue_ahead=8734.5)
    return "cancelled order .... nba-finals-2027: reason + queue-ahead recorded"


def scenario_partial_fill(reg: OrderRegistry, t: int, out: Path) -> str:
    """One leg partially filled -- size_remaining > 0 on a working order."""
    m = _market("oscar-best-picture", 6)
    _post(reg, local_id="loc-6-up", venue_id="0xvenue0006up", market=m, leg="UP",
          price=0.22, size=20, posted_ts=t - 1200, polled_ts=t - 900,
          pair_id="pair-6", bot_pos_price_max_pair_cost_usd=0.75)
    _quote(reg, market=m, leg="UP", price=0.22, size=20, ts=t - 1200,
           local_id="loc-6-up", venue_id="0xvenue0006up", queue_ahead=420.0,
           mid=0.24, t_remaining=259200.0, latency_ms=310.0)
    _fill(reg, local_id="loc-6-up", trade_id="trade-6-up", size=6,
          price=0.22, venue_ts=t - 900, status="partial")
    return "partial fill ....... oscar-best-picture: 6 of 20 shares matched (PARTIAL)"


def scenario_pending_order(reg: OrderRegistry, t: int, out: Path) -> str:
    """An order created but never acknowledged -- venue id never attached."""
    m = _market("gdp-q3-print", 7)
    _post(reg, local_id="loc-7-up", venue_id=None, market=m, leg="UP",
          price=0.61, size=10, posted_ts=t - 20, polled_ts=t - 20,
          pair_id="pair-7", bot_pos_price_max_pair_cost_usd=0.97)
    _quote(reg, market=m, leg="UP", price=0.61, size=10, ts=t - 20,
           local_id="loc-7-up", venue_id=None, queue_ahead=None,
           mid=0.635, t_remaining=900.0, latency_ms=None)
    return "pending order ...... gdp-q3-print: created, venue id never attached"


def scenario_ghost_fill(reg: OrderRegistry, t: int, out: Path) -> str:
    """A fill with no order behind it, and a quote referencing an order that
    never existed -- plus one order marked 'unattributed'.

    The registry enforces fills.order_uuid -> orders.id, so the ghost fill
    is written with a raw insert and the pragma off: that is exactly the
    corrupted-or-imported store the dashboard must render rather than crash
    on. The dangling quote goes through the normal writer (quotes have no
    order FK).
    """
    m = _market("election-pa", 8)
    with sqlite3.connect(str(out)) as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute(
            "INSERT INTO fills (trade_id, order_uuid, size, price, venue_ts,"
            " recorded_ts, run_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("trade-ghost", "loc-never-existed", 5.0, 0.48,
             _ms(t - 2400), _ms(t - 2400), DEMO_RUN_ID),
        )
        conn.commit()
    reg.log_quote(QuoteRecord(
        ts=float(t - 2400), market_slug=m["slug"],
        condition_id=m["condition_id"], token_id=m["up_token"], side="UP",
        price=0.48, size=5, queue_ahead=980.0, mid=0.50, edge_vs_mid=0.02,
        t_remaining=3600.0, filled=0.0, order_id="0xvenue-never-existed",
        local_id="loc-never-existed", latency_ms=70.0,
    ))
    # An order the venue never acknowledged, marked unattributed by the
    # reconcile pass.
    _post(reg, local_id="loc-8-ghost", venue_id=None, market=m, leg="UP",
          price=0.49, size=10, posted_ts=t - 2400, polled_ts=t - 2300,
          pair_id="pair-8", bot_pos_price_max_pair_cost_usd=0.97)
    reg.update_order_status("loc-8-ghost", "unattributed", _ms(t - 2300))
    return "ghost fill ......... election-pa: fill with no order + dangling quote + unattributed order"


def scenario_stale_order(reg: OrderRegistry, t: int, out: Path) -> str:
    """A pair whose orders carry old poll ages -- the per-order age column
    reads minutes. (The global stale banner tracks the freshest poll across
    ALL orders, so a mixed demo database cannot show it; the aged rows here
    are what an operator sees per order.)"""
    m = _market("rain-in-tel-aviv", 9)
    _post(reg, local_id="loc-9-up", venue_id="0xvenue0009up", market=m, leg="UP",
          price=0.30, size=25, posted_ts=t - 3600, polled_ts=t - 180,
          pair_id="pair-9", bot_pos_price_max_pair_cost_usd=0.90)
    _quote(reg, market=m, leg="UP", price=0.30, size=25, ts=t - 3600,
           local_id="loc-9-up", venue_id="0xvenue0009up", queue_ahead=220.0,
           mid=0.30, t_remaining=172800.0, latency_ms=95.0)
    _post(reg, local_id="loc-9-dn", venue_id="0xvenue0009dn", market=m, leg="DOWN",
          price=0.58, size=25, posted_ts=t - 3590, polled_ts=t - 180,
          pair_id="pair-9", bot_pos_price_max_pair_cost_usd=0.90)
    _quote(reg, market=m, leg="DOWN", price=0.58, size=25, ts=t - 3590,
           local_id="loc-9-dn", venue_id="0xvenue0009dn", queue_ahead=1750.0,
           mid=0.68, t_remaining=172800.0, latency_ms=110.0)
    return "stale pair ......... rain-in-tel-aviv: last poll 3 min ago (stale banner)"


def scenario_settled_market(reg: OrderRegistry, t: int, out: Path) -> str:
    """A resolved market with a booked settlement close."""
    m = _market("superbowl-lix-winner", 10)
    _post(reg, local_id="loc-10-up", venue_id="0xvenue0010up", market=m, leg="UP",
          price=0.55, size=10, posted_ts=t - 86400, polled_ts=t - 86000,
          pair_id="pair-10", bot_pos_price_max_pair_cost_usd=0.99)
    _quote(reg, market=m, leg="UP", price=0.55, size=10, ts=t - 86400,
           local_id="loc-10-up", venue_id="0xvenue0010up", queue_ahead=4300.0,
           mid=0.53, t_remaining=259200.0, latency_ms=150.0)
    _fill(reg, local_id="loc-10-up", trade_id="trade-10-up", size=10,
          price=0.548, venue_ts=t - 86000)
    _post(reg, local_id="loc-10-dn", venue_id="0xvenue0010dn", market=m, leg="DOWN",
          price=0.44, size=10, posted_ts=t - 86390, polled_ts=t - 85900,
          pair_id="pair-10", bot_pos_price_max_pair_cost_usd=0.99)
    _quote(reg, market=m, leg="DOWN", price=0.44, size=10, ts=t - 86390,
           local_id="loc-10-dn", venue_id="0xvenue0010dn", queue_ahead=670.0,
           mid=0.46, t_remaining=259200.0, latency_ms=140.0)
    _fill(reg, local_id="loc-10-dn", trade_id="trade-10-dn", size=10,
          price=0.438, venue_ts=t - 85900)
    reg.log_resolution(ResolutionRecord(
        condition_id=m["condition_id"], winning_token="DOWN",
        winning_token_id=m["dn_token"], resolved_ts=float(t - 43200),
    ))
    reg.log_close(CloseRecord(
        ts=float(t - 40000), condition_id=m["condition_id"],
        market_slug=m["slug"], method="sell", gas=0.02, shares=10,
        up_price=0.02, dn_price=1.00, cost_basis=9.86, proceeds=4.38,
        fee=0.0, realized_pnl=-5.48, forgone_vs_settlement=-4.62,
        tx_hash="0xdeadbeef10",
    ))
    return "settled market ..... superbowl-lix-winner: resolution + settlement close"


def scenario_refused_lifecycle(reg: OrderRegistry, t: int, out: Path) -> str:
    """A pair the dashboard cannot classify -- the REFUSED hedge state.

    REFUSED is not driven by the single-leg lifecycle record: the readout
    fires when one pair_id spans more than two token ids, which is what a
    contaminated pair looks like from a reduced view. The lifecycle record
    below is the real telemetry the saver writes; the third token is what
    makes the readout render REFUSED with its reason.
    """
    m = _market("strike-friday", 11)
    # The single buy that filled.
    _post(reg, local_id="loc-11-up", venue_id="0xvenue0011up", market=m, leg="UP",
          price=0.38, size=10, posted_ts=t - 5400, polled_ts=t - 5000,
          pair_id="pair-11", bot_pos_price_max_pair_cost_usd=0.97)
    _quote(reg, market=m, leg="UP", price=0.38, size=10, ts=t - 5400,
           local_id="loc-11-up", venue_id="0xvenue0011up", queue_ahead=2600.0,
           mid=0.39, t_remaining=86400.0, latency_ms=180.0)
    _fill(reg, local_id="loc-11-up", trade_id="trade-11-up", size=10,
          price=0.378, venue_ts=t - 5000)
    # The second leg, resting.
    _post(reg, local_id="loc-11-dn", venue_id="0xvenue0011dn", market=m, leg="DOWN",
          price=0.57, size=10, posted_ts=t - 5390, polled_ts=t - 4990,
          pair_id="pair-11", bot_pos_price_max_pair_cost_usd=0.97)
    _quote(reg, market=m, leg="DOWN", price=0.57, size=10, ts=t - 5390,
           local_id="loc-11-dn", venue_id="0xvenue0011dn", queue_ahead=1900.0,
           mid=0.60, t_remaining=86400.0, latency_ms=175.0)
    # The stray token: a third token id under the same pair_id, so the
    # pair spans three tokens and the hedge state reads REFUSED.
    stray = _market("strike-friday-stray", 31)
    _post(reg, local_id="loc-11-extra", venue_id="0xvenue0011x", market=stray,
          leg="UP", price=0.20, size=5, posted_ts=t - 5380, polled_ts=t - 4980,
          pair_id="pair-11", bot_pos_price_max_pair_cost_usd=0.97)
    reg.save_lifecycle_state(LifecycleStateRecord(
        pair_id="pair-11", condition_id=m["condition_id"], state="refused",
        reason="single_buy_refused_second_leg",
        updated_ts_ms=_ms(t - 4800),
        evidence_json='{"up_filled": true, "dn_refused": true, '
                      '"reason": "pair_cost_above_cap"}',
    ))
    return "refused pair ....... strike-friday: 3 token ids under one pair (REFUSED)"


def scenario_aged_cancelled(reg: OrderRegistry, t: int, out: Path) -> str:
    """An old cancelled pair -- history rows the closed view can page past."""
    m = _market("old-market-june", 12)
    _post(reg, local_id="loc-12-up", venue_id="0xvenue0012up", market=m, leg="UP",
          price=0.50, size=10, posted_ts=t - 432000, polled_ts=t - 431500,
          pair_id="pair-12", bot_pos_price_max_pair_cost_usd=0.98)
    _quote(reg, market=m, leg="UP", price=0.50, size=10, ts=t - 432000,
           local_id="loc-12-up", venue_id="0xvenue0012up", queue_ahead=5100.0,
           mid=0.49, t_remaining=604800.0, latency_ms=210.0)
    reg.update_order_status("loc-12-up", "cancelled", _ms(t - 431500),
                            cancel_reason="market_aged_out",
                            cancel_queue_ahead=0.0)
    _post(reg, local_id="loc-12-dn", venue_id="0xvenue0012dn", market=m, leg="DOWN",
          price=0.49, size=10, posted_ts=t - 431990, polled_ts=t - 431500,
          pair_id="pair-12", bot_pos_price_max_pair_cost_usd=0.98)
    _quote(reg, market=m, leg="DOWN", price=0.49, size=10, ts=t - 431990,
           local_id="loc-12-dn", venue_id="0xvenue0012dn", queue_ahead=4600.0,
           mid=0.50, t_remaining=604800.0, latency_ms=205.0)
    reg.update_order_status("loc-12-dn", "cancelled", _ms(t - 431500),
                            cancel_reason="market_aged_out",
                            cancel_queue_ahead=0.0)
    return "aged cancelled ..... old-market-june: 5-day-old cancelled pair (history)"


def scenario_pair_completion_wait(reg: OrderRegistry, t: int, out: Path) -> str:
    """One leg filled with the other leg still quoted -- waiting for the
    other side.

    The pair-completion state the Orders tab renders with the filled
    leg's counterpart annotation and the resting leg's break-even room
    line (the most the missing side may still cost under the pair cap).
    """
    m = _market("waiting-for-other-side", 26)
    # The leg that filled.
    _post(reg, local_id="loc-w-up", venue_id="0xvenue0026up", market=m, leg="UP",
          price=0.42, size=10, posted_ts=t - 2700, polled_ts=t - 2400,
          pair_id="pair-wait", bot_pos_price_max_pair_cost_usd=0.98)
    _quote(reg, market=m, leg="UP", price=0.42, size=10, ts=t - 2700,
           local_id="loc-w-up", venue_id="0xvenue0026up", queue_ahead=340.0,
           mid=0.44, t_remaining=7200.0, latency_ms=67.0)
    _fill(reg, local_id="loc-w-up", trade_id="trade-w-up", size=10,
          price=0.418, venue_ts=t - 2400)
    # The leg still quoted, waiting for the filled side to complete the pair.
    _post(reg, local_id="loc-w-dn", venue_id="0xvenue0026dn", market=m, leg="DOWN",
          price=0.55, size=10, posted_ts=t - 2350, polled_ts=t - 60,
          pair_id="pair-wait", bot_pos_price_max_pair_cost_usd=0.98)
    _quote(reg, market=m, leg="DOWN", price=0.55, size=10, ts=t - 2350,
           local_id="loc-w-dn", venue_id="0xvenue0026dn", queue_ahead=1100.0,
           mid=0.55, t_remaining=7200.0, latency_ms=71.0)
    return "pair-completion wait .. waiting-for-other-side: UP filled, DOWN quoted (hedge pending)"


def scenario_one_leg_quoted(reg: OrderRegistry, t: int, out: Path) -> str:
    """A single resting order with no partner leg -- one quoted, one missing.

    The Orders tab renders it UNPAIRED / Missing Leg - No Position: the
    resting leg shows its quote and break-even room line, the other side
    reads '[Awaiting ... Quote]'.
    """
    m = _market("half-quoted-market", 27)
    _post(reg, local_id="loc-h-up", venue_id="0xvenue0027up", market=m, leg="UP",
          price=0.36, size=15, posted_ts=t - 900, polled_ts=t - 30,
          pair_id="pair-half", bot_pos_price_max_pair_cost_usd=0.97)
    _quote(reg, market=m, leg="UP", price=0.36, size=15, ts=t - 900,
           local_id="loc-h-up", venue_id="0xvenue0027up", queue_ahead=760.0,
           mid=0.38, t_remaining=10800.0, latency_ms=59.0)
    return "one leg quoted ...... half-quoted-market: UP resting, no DOWN order (missing leg)"


def scenario_quote_history(reg: OrderRegistry, t: int, out: Path) -> str:
    """Extra quote rows with the spread of queue telemetry the queue
    metrics aggregate: NULL queue_ahead (unmeasured), a deep queue, a
    shallow one, cancelled rows, and varied latency."""
    m = _market("quote-history-mix", 13)
    rows = [
        # (leg, price, size, age_sec, queue_ahead, mid, t_remaining, latency_ms, cancelled)
        ("UP", 0.46, 15, 3000, None, 0.48, 7200.0, 65.0, False),
        ("UP", 0.46, 15, 2800, 15638.0, 0.48, 7200.0, 68.0, False),
        ("DOWN", 0.51, 15, 2790, 1724.0, 0.52, 7200.0, 71.0, False),
        ("UP", 0.20, 30, 5400, 95.0, 0.21, 86400.0, 420.0, False),
        ("DOWN", 0.77, 30, 5300, 31100.0, 0.79, 86400.0, 910.0, True),
        ("UP", 0.65, 10, 600, 45.0, 0.66, 3600.0, 38.0, False),
        ("DOWN", 0.33, 10, 590, 880.0, 0.34, 3600.0, 44.0, True),
        ("UP", 0.12, 40, 9000, 520.0, 0.13, 172800.0, 505.0, False),
        ("DOWN", 0.86, 40, 8900, 24000.0, 0.87, 172800.0, 870.0, False),
    ]
    for i, (leg, price, size, age, qa, mid, t_rem, lat, cancelled) in enumerate(rows):
        ts = t - age
        lid = f"loc-13-q{i}"
        vid = f"0xvenue0013q{i}"
        _quote(reg, market=m, leg=leg, price=price, size=size, ts=ts,
               local_id=lid, venue_id=vid, queue_ahead=qa, mid=mid,
               t_remaining=t_rem, latency_ms=lat)
        if cancelled:
            _post(reg, local_id=lid, venue_id=vid, market=m, leg=leg,
                  price=price, size=size, posted_ts=ts, polled_ts=ts)
            reg.update_order_status(lid, "cancelled", _ms(ts + 60),
                                    cancel_reason="book_moved",
                                    cancel_queue_ahead=qa or 0.0)
    return f"quote history ...... quote-history-mix: {len(rows)} rows, queue telemetry spread"


def scenario_markouts(reg: OrderRegistry, t: int, out: Path) -> str:
    """Markout trajectories the adverse-selection metrics read: adverse,
    favorable, and flat, one still pending, one contaminated reference.

    One exit is recorded for the same market (#516), timed so the earliest
    trajectories fall INSIDE a holding window. The excursion readout measures
    post-fill marks between a fill and its close, so a market with no close can
    only ever report that every position is still open -- true, and useless for
    looking at the card."""
    m = _market("markout-mix", 14)
    rows = [
        # (fill_price, ref_mid, (h0, h1, h2, h3), done, source)
        (0.50, 0.50, (0.495, 0.491, 0.487, 0.482), 1, "clean"),    # adverse
        (0.50, 0.50, (0.505, 0.511, 0.516, 0.521), 1, "clean"),    # favorable
        (0.50, 0.50, (0.501, 0.499, 0.500, 0.498), 1, "clean"),    # flat
        (0.46, 0.46, (0.452, 0.448, 0.443, 0.440), 1, "contaminated"),
        (0.62, 0.62, (0.618, 0.624, 0.630, 0.637), 1, "clean"),
        (0.35, 0.35, (0.348, 0.344, 0.340, 0.336), 1, "clean"),
        (0.72, 0.72, (0.715, 0.719, 0.724, None), 0, "clean"),     # pending
        (0.28, 0.28, (0.283, 0.287, 0.291, 0.296), 1, "clean"),
        (0.55, 0.55, (0.547, 0.542, 0.538, 0.534), 1, "clean"),
        (0.41, 0.41, (0.414, 0.418, 0.422, 0.427), 1, "clean"),
        (0.58, 0.58, (0.574, 0.569, 0.565, 0.561), 1, "contaminated"),
        (0.66, 0.66, (0.657, 0.655, 0.654, 0.653), 1, "clean"),
    ]
    for i, (fill_price, ref_mid, horizon, done, source) in enumerate(rows):
        ts = t - 12000 + i * 900
        reg.log_markout(MarkoutRecord(
            ts=float(ts), condition_id=m["condition_id"],
            market_slug=m["slug"], side="BUY", token_id=m["up_token"],
            fill_price=fill_price, size=10, ref_mid=ref_mid,
            ref_mid_source=source,
            mid_h0=horizon[0], mid_h1=horizon[1], mid_h2=horizon[2],
            mid_h3=horizon[3], done=done,
        ))
    # The exit that closes the first four positions: it sits 400s after the fourth trajectory's
    # fill, so trajectories 0-3 have their 5m mark inside the window (trajectory 0 also has its
    # 15m one) and the eight later fills are still open. A single_buy_exit, because a leg held
    # past a mark and then sold back through the book is exactly the rescue this card is for.
    reg.log_close(CloseRecord(
        ts=float(t - 8900), condition_id=m["condition_id"], market_slug=m["slug"],
        method="single_buy_exit", gas=0.0, shares=10, up_price=0.47,
        cost_basis=5.00, proceeds=4.70, fee=0.0, realized_pnl=-0.30,
        trigger_bid=0.48, fresh_bid=0.475, reason="adverse_drift",
        tx_hash="0xdemo516exit",
    ))
    return (f"markouts ........... markout-mix: {len(rows)} trajectories "
            "(adverse/favorable/flat/pending) + one holding-window exit")


def scenario_account_curve(reg: OrderRegistry, t: int, out: Path) -> str:
    """An equity curve with a drawdown and recovery and asymmetric
    returns, so the capital-risk metrics have a real series: mostly small
    wins with a few large losses (negative skew), one deep drawdown that
    recovers to a new high, one shallower drawdown still open."""
    steps = (
        [2.4, 1.8, 2.9, 1.2, 3.1, 2.2, 1.7, 2.8, 1.4, 2.6]
        + [-3.0, -4.2, -5.1, -2.3]          # drawdown ~ -14.6
        + [2.1, 3.4, 4.0, 5.2, 3.1]         # recovery to a new high
        + [2.3, 1.9, -6.4, 1.6, 2.8, 2.2]   # the big loss -> negative skew
        + [-1.8, -1.2, 1.4, 2.6]            # shallow drawdown, recovering
        + [2.0, 1.5, 2.9, 1.8, 2.4, 1.1, 2.7, 1.9, 2.3, 1.6]
        + [2.5, 1.7, 2.2, 1.3, 2.8, 2.0]
    )
    value = 100.0
    for i, step in enumerate(steps):
        value += step
        ts = t - (len(steps) - i) * 1200.0  # one mark every 20 minutes
        reg.log_account_mark(
            {
                "collateral_usd": round(value * 0.55, 2),
                "positions_value_usd": round(value * 0.45, 2),
                "account_value_usd": round(value, 2),
                "pnl_usd": round(value - 100.0, 2),
                "pnl_pct": round(value - 100.0, 2),
                "pnl_closed_usd": round(value - 100.0, 2),
                "pnl_series_usd": round(value - 100.0, 2),
                "unrealized_usd": round(value * 0.03, 2),
                "committed_usd": round(value * 0.10, 2),
                "open_positions_count": 3 + (i % 2),
                "closed_positions_count": i + 1,
            },
            ts=ts,
        )
    return f"account curve ...... {len(steps)} marks: drawdown+recovery, negative skew"


def scenario_operational_telemetry(reg: OrderRegistry, t: int, out: Path) -> str:
    """The remaining operational tables: float marks, hedge census, market
    events, venue errors, divergence events."""
    m = _market("ops-telemetry", 15)
    for i in range(6):
        reg.log_float_mark(
            unrealized_usd=round(4.2 - i * 0.4, 2),
            committed_open_usd=round(18.5 - i * 1.1, 2),
            naked_usd=round(2.0 if i == 2 else 0.0, 2),
            ts=float(t - 3600 + i * 600),
        )
    # One census row survives per condition_id (it is the table's primary
    # key), so each census row gets its own market.
    for i, (up_ask, dn_ask) in enumerate(
        [(0.46, 0.51), (0.44, 0.53), (0.48, 0.50), (0.45, 0.52),
         (0.47, 0.50), (0.43, 0.54)]
    ):
        cm = _market(f"census-{i}", 30 + i)
        reg.log_hedge_census(HedgeCensusRecord(
            condition_id=cm["condition_id"], market_slug=cm["slug"],
            up_ask=up_ask, down_ask=dn_ask,
            pair_cost_at_touch=round(up_ask + dn_ask, 4),
            fillable_sub_one=round(1.0 - up_ask - dn_ask, 4),
            observed_ts=float(t - 3600 + i * 600),
        ))
    events = [
        ("BLOCKED", "pair_cost_above_cap", "PAIR_COST", "UP", 0.58, 20),
        ("quote_skipped", "queue_ahead_unmeasurable", "QUEUE", "DOWN", 0.71, 15),
        ("lifecycle_stop", "naked_position_refused", "LIFECYCLE", None, None, None),
        ("BLOCKED", "stale_book", "STALE", "UP", 0.49, 10),
        ("fill", None, "FILL", "UP", 0.44, 15),
    ]
    for kind, reason, code, side, price, size in events:
        reg.log_market_event(MarketEventRecord(
            ts=float(t - 1800), condition_id=m["condition_id"],
            market_slug=m["slug"], kind=kind, reason=reason,
            reason_code=code, side=side, price=price, size=size,
        ))
    for i, (code, msg) in enumerate(
        [("RATE_LIMIT", "too many requests"),
         ("INSUFFICIENT_BALANCE", "not enough collateral for 20 shares"),
         ("INVALID_ORDER", "min tick size violated")]
    ):
        reg.log_venue_error(VenueErrorRecord(
            ts=float(t - 1200 + i * 300), condition_id=m["condition_id"],
            side="UP", price=0.47, size=20, error_code=code,
            raw_error_msg=msg,
        ))
    for i in range(2):
        reg.log_divergence_event(DivergenceEventRecord(
            ts=float(t - 900 + i * 400), condition_id=m["condition_id"],
            pair_id=f"pair-ops-{i}", registry_diff=0.5, venue_diff=0.4,
            chain_diff=0.1, divergence_msg="registry drift vs venue read",
        ))
    return ("operational telemetry ... float marks, hedge census, market "
            "events, venue errors, divergences")


def scenario_trade_history(reg: OrderRegistry, t: int, out: Path) -> str:
    """Extra closes across history markets so the closed-trades table and
    the fill-path / friction metrics have a real sample: merges with varied
    pair costs, rescue sells with and without the audit pair, and one
    zero-fee rescue (taker cost reads unmeasured -- the #524 case)."""
    history = [
        # (idx, slug, method, shares, cost_basis, proceeds, fee, realized, reason, trigger, fresh, forgone)
        (16, "old-merge-a", "merge", 10, 9.55, 10.00, 0.0, 0.45, None, None, None, None),
        (17, "old-merge-b", "merge", 8, 7.92, 8.00, 0.0, 0.08, None, None, None, None),
        (18, "old-rescue-a", "single_buy_exit", 10, 4.40, 4.85, 0.30, 0.15, "grace_expired", 0.50, 0.485, None),
        (19, "old-rescue-b", "single_buy_exit", 12, 5.76, 4.20, 0.36, -1.20, "adverse_drift", 0.36, 0.35, -0.60),
        (20, "old-rescue-c", "naked_exit", 6, 2.88, 2.70, 0.0, -0.18, "adverse_drift", None, None, -0.30),
        (21, "old-merge-c", "merge", 15, 14.10, 15.00, 0.0, 0.90, None, None, None, None),
        (22, "old-rescue-d", "single_buy_exit", 20, 9.20, 10.40, 0.60, 0.60, "grace_expired", 0.48, 0.52, None),
        (23, "old-merge-d", "merge", 6, 5.94, 6.00, 0.0, 0.06, None, None, None, None),
        (24, "old-rescue-e", "single_buy_exit", 10, 4.60, 3.10, 0.30, -1.20, "adverse_drift", 0.29, 0.31, -0.50),
        (25, "old-merge-e", "merge", 12, 11.28, 12.00, 0.0, 0.72, None, None, None, None),
    ]
    for i, (idx, slug, method, shares, cost, proceeds, fee, pnl, reason,
            trigger, fresh, forgone) in enumerate(history):
        m = _market(slug, idx)
        reg.log_close(CloseRecord(
            ts=float(t - 10000 + i * 900), condition_id=m["condition_id"],
            market_slug=slug, method=method, gas=0.01, shares=shares,
            cost_basis=cost, proceeds=proceeds, fee=fee, realized_pnl=pnl,
            reason=reason, trigger_bid=trigger, fresh_bid=fresh,
            forgone_vs_settlement=forgone,
            tx_hash=f"0xdeadbeef{idx:02x}",
        ))
    return f"trade history ...... {len(history)} closes: merges, rescues (with/without audit pair, zero-fee)"


SCENARIOS = (
    scenario_resting_pair,
    scenario_naked_single_leg,
    scenario_balanced_pair,
    scenario_merged_pair,
    scenario_cancelled_order,
    scenario_partial_fill,
    scenario_pending_order,
    scenario_ghost_fill,
    scenario_stale_order,
    scenario_settled_market,
    scenario_refused_lifecycle,
    scenario_aged_cancelled,
    scenario_pair_completion_wait,
    scenario_one_leg_quoted,
    scenario_quote_history,
    scenario_markouts,
    scenario_account_curve,
    scenario_operational_telemetry,
    scenario_trade_history,
)


# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------

def build(out_path: Path, *, force: bool = False) -> dict[str, int]:
    """Write the demo registry and return per-table row counts.

    Raises ``DemoRefused`` for any path or state that must not be written.
    """
    resolved = out_path.resolve()
    if resolved == DEFAULT_DB_PATH.resolve():
        raise DemoRefused(
            f"refusing to write the production registry {DEFAULT_DB_PATH}"
        )
    if out_path.exists() and not force:
        raise DemoRefused(
            f"{out_path} already exists; pass --force to overwrite"
        )
    if out_path.exists():
        out_path.unlink()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    init_db(out_path)
    reg = OrderRegistry(out_path, run_id=DEMO_RUN_ID)
    t = int(time.time())
    for scenario in SCENARIOS:
        scenario(reg, t, out_path)

    counts: dict[str, int] = {}
    with sqlite3.connect(str(out_path)) as conn:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
                " AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        ]
        for table in tables:
            counts[table] = int(
                conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            )
    return counts


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    parser = argparse.ArgumentParser(
        description="Build a bogus demo registry for dashboard testing."
    )
    parser.add_argument(
        "--out", default=str(DEFAULT_OUT),
        help=f"output path (default: {DEFAULT_OUT})",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="overwrite an existing output file",
    )
    args = parser.parse_args(argv)

    out = Path(args.out)
    try:
        counts = build(out, force=args.force)
    except DemoRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2

    print(f"demo registry written: {out.resolve()}  (run_id={DEMO_RUN_ID})")
    print()
    print("scenarios:")
    for scenario in SCENARIOS:
        print(f"  {scenario.__doc__.splitlines()[0].strip()}")
    print()
    print("rows per table:")
    for table, count in counts.items():
        print(f"  {table:24s} {count}")
    print()
    print("load it in the dashboard:")
    print(f"  python -m dashboard.server --db {out.resolve()}")
    print("  http://127.0.0.1:8799")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
