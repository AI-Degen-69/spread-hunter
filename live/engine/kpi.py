"""live/engine/kpi.py - Live KPI report mirroring strategy/kpi.py:124-410.

Computes exact matching metrics from live/run/live.db:
- True maker fill rate (excluding taker crossed shares)
- Quote uptime and top skip reasons
- Spread capture and adverse selection markouts
- Realized PnL by method (merge vs exit)
- Plus 4 live operational metrics: latency, reconcile lag, venue errors, divergence count.
"""

from __future__ import annotations

import statistics
import time
from pathlib import Path
from typing import Any, Optional

from engine.order_registry import OrderRegistry, DEFAULT_DB_PATH
from engine.config import load as load_cfg

_CFG = load_cfg()


def report(db_path: Path | str | None = None, run_id: Optional[str] = None) -> dict[str, Any]:
    """Generate live KPI dictionary mirroring strategy/kpi.py report() structure."""
    reg = OrderRegistry(db_path if db_path is not None else DEFAULT_DB_PATH)
    
    quotes = reg.get_all_quotes()
    fills = reg.get_all_fills()
    closes = reg.get_all_closes()
    market_events = reg.get_all_market_events()
    markouts = reg.get_all_markouts()
    census_rows = reg.get_all_hedge_census()
    venue_errs = reg.get_all_venue_errors()
    divergences = reg.get_all_divergence_events()
    resolutions = reg.get_all_resolutions()

    if run_id:
        quotes = [q for q in quotes if q.get("run_id") == run_id]
        fills = [f for f in fills if f.get("run_id") == run_id]
        closes = [c for c in closes if c.get("run_id") == run_id]
        market_events = [e for e in market_events if e.get("run_id") == run_id]
        markouts = [m for m in markouts if m.get("run_id") == run_id]
        venue_errs = [v for v in venue_errs if v.get("run_id") == run_id]
        divergences = [d for d in divergences if d.get("run_id") == run_id]

    posted_sh = sum(float(q.get("size") or 0.0) for q in quotes)
    filled_sh = sum(float(f.get("size") or 0.0) for f in fills)
    # Taker crossed fills
    crossed_sh = sum(float(f.get("size") or 0.0) for f in fills if f.get("side") == "SELL")
    cost = sum(float(f.get("size") or 0.0) * float(f.get("price") or 0.0) for f in fills)

    # Spread capture vs mid at quote/post time
    cap_list = []
    edges = []
    for q in quotes:
        if q.get("edge_vs_mid") is not None and q.get("filled", 0) > 0:
            e = float(q["edge_vs_mid"])
            sh = float(q["filled"])
            cap_list.append(e * sh)
            edges.append(e)
    spread_capture = sum(cap_list)

    # Seconds to fill
    waits = []
    quote_map = {q["local_id"]: q for q in quotes if q.get("local_id")}
    for f in fills:
        q = quote_map.get(f.get("order_uuid"))
        if q and f.get("venue_ts") and q.get("ts"):
            # venue_ts is ms, q.ts is sec
            sec_to_fill = max(0.0, (float(f["venue_ts"]) / 1000.0) - float(q["ts"]))
            waits.append(sec_to_fill)

    queues = [float(q["queue_ahead"]) for q in quotes if q.get("queue_ahead") is not None]

    # Group fills per market
    by_mkt: dict[str, dict] = {}
    for f in fills:
        cid = f.get("condition_id") or "unknown"
        m = by_mkt.setdefault(cid, {
            "slug": f.get("condition_id", "unknown")[:16],
            "up_sh": 0.0, "dn_sh": 0.0,
            "up_cost": 0.0, "dn_cost": 0.0, "fills": 0,
            "fee": 0.0, "crossed_sh": 0.0,
        })
        side = str(f.get("side", "UP")).upper()
        sz = float(f.get("size") or 0.0)
        p = float(f.get("price") or 0.0)
        if side == "UP":
            m["up_sh"] += sz
            m["up_cost"] += sz * p
        else:
            m["dn_sh"] += sz
            m["dn_cost"] += sz * p
        m["fills"] += 1

    balances = []
    pairs = []
    for cid, m in by_mkt.items():
        hi = max(m["up_sh"], m["dn_sh"])
        if hi > 0:
            balances.append(min(m["up_sh"], m["dn_sh"]) / hi)
        if m["up_sh"] > 0 and m["dn_sh"] > 0:
            pairs.append((m["up_cost"] / m["up_sh"]) + (m["dn_cost"] / m["dn_sh"]))

    # Realized PnL from closes
    realized_from_closes = sum(float(c.get("realized_pnl") or 0.0) for c in closes)
    realized_pnl = realized_from_closes
    wins = [float(c["realized_pnl"]) for c in closes if float(c.get("realized_pnl") or 0.0) > 0]
    losses = [float(c["realized_pnl"]) for c in closes if float(c.get("realized_pnl") or 0.0) <= 0]

    # Time span
    all_ts = [float(q["ts"]) for q in quotes if q.get("ts")] + [float(f.get("venue_ts", 0))/1000 for f in fills if f.get("venue_ts")]
    days = ((max(all_ts) - min(all_ts)) / 86400.0) if len(all_ts) > 1 else 0.0

    # Fill rate by queue ahead buckets
    q_buckets = [(0, 1), (1, 50), (50, 150), (150, 400), (400, 1e12)]
    fill_by_queue = []
    for lo, hi in q_buckets:
        b = [q for q in quotes if lo <= (float(q.get("queue_ahead") or 0.0)) < hi]
        posted_b = sum(float(q.get("size") or 0.0) for q in b)
        filled_b = sum(float(q.get("filled") or 0.0) for q in b)
        if b:
            fill_by_queue.append({
                "label": f"{lo:.0f}-{hi:.0f}" if hi < 1e11 else f"{lo:.0f}+",
                "quotes": len(b),
                "posted": posted_b,
                "filled": filled_b,
                "fill_rate": (filled_b / posted_b) if posted_b else None,
            })

    # Partial vs full quotes
    partial = [q for q in quotes if 0 < (float(q.get("filled") or 0.0)) < (float(q.get("size") or 0.0)) - 1e-9]
    fully = [q for q in quotes if (float(q.get("filled") or 0.0)) >= (float(q.get("size") or 0.0)) - 1e-9 and float(q.get("filled") or 0.0) > 0]
    unfilled_of_partials = sum(float(q.get("size") or 0.0) - float(q.get("filled") or 0.0) for q in partial)

    # Quote uptime from market_events
    dec_quoting = sum(1 for e in market_events if e.get("kind") == "QUOTING")
    dec_total = len(market_events)
    quote_uptime = (dec_quoting / dec_total) if dec_total > 0 else None

    skip_reasons = {}
    for e in market_events:
        if e.get("kind") in ("BLOCKED", "DECISION") or e.get("reason_code") != "INTENT_GENERATED":
            code = e.get("reason_code") or "OTHER"
            skip_reasons[code] = skip_reasons.get(code, 0) + 1
    top_skips = sorted(skip_reasons.items(), key=lambda kv: -kv[1])[:6]

    # Adverse selection from size-weighted markouts
    # Drift = (mid_later - fill_price)
    markout_drifts = []
    for m in markouts:
        sz = float(m.get("size") or 1.0)
        # longest matured horizon
        m_later = None
        for col in ("mid_h2", "mid_h1", "mid_h3", "mid_h0"):
            if m.get(col) is not None:
                m_later = float(m[col])
                break
        if m_later is not None:
            fp = float(m.get("fill_price") or 0.0)
            drift = m_later - fp
            markout_drifts.append(drift * sz)

    adverse_selection = (sum(markout_drifts) / filled_sh) if (filled_sh > 0 and markout_drifts) else 0.0

    # Hedge Census
    census = {
        "markets_observed": len(census_rows),
        "fillable": sum(1 for r in census_rows if r.get("fillable_sub_one")),
        "fillable_rate": (sum(1 for r in census_rows if r.get("fillable_sub_one")) / len(census_rows)) if census_rows else None,
        "median_pair_at_touch": statistics.median([float(r["pair_cost_at_touch"]) for r in census_rows if r.get("pair_cost_at_touch") is not None]) if census_rows else None,
    }

    # 4 Live-Specific Metrics
    latencies = [float(q["latency_ms"]) for q in quotes if q.get("latency_ms") is not None]
    order_latency_ms = {
        "median": statistics.median(latencies) if latencies else None,
        "max": max(latencies) if latencies else None,
        "count": len(latencies),
    }

    reconcile_lags = []
    for f in fills:
        if f.get("venue_ts") and f.get("recorded_ts"):
            lag = max(0.0, float(f["recorded_ts"]) - float(f["venue_ts"]))
            reconcile_lags.append(lag)
    reconcile_lag_ms = {
        "median": statistics.median(reconcile_lags) if reconcile_lags else None,
        "max": max(reconcile_lags) if reconcile_lags else None,
        "count": len(reconcile_lags),
    }

    venue_rejects = {
        "total": len(venue_errs),
        "by_code": {},
    }
    for ve in venue_errs:
        c = ve.get("error_code") or "ERROR"
        venue_rejects["by_code"][c] = venue_rejects["by_code"].get(c, 0) + 1

    three_way_divergences = {
        "total": len(divergences),
        "events": divergences[:10],
    }

    return {
        # Pace
        "markets_quoted": len({q["condition_id"] for q in quotes if q.get("condition_id")}),
        "markets_filled": len(by_mkt),
        "markets_settled": len(closes),
        "fills": len(fills),
        "quotes": len(quotes),
        "days": days,
        "fills_per_day": (len(fills) / days) if days > 0.01 else None,
        "notional_per_day": (cost / days) if days > 0.01 else None,

        # Maker metrics
        "fill_rate": ((filled_sh - crossed_sh) / posted_sh) if posted_sh else None,
        "posted_shares": posted_sh,
        "filled_shares": filled_sh,
        "crossed_shares": crossed_sh,
        "taker_fees_paid": sum(float(c.get("fee") or 0.0) for c in closes),
        "median_seconds_to_fill": statistics.median(waits) if waits else None,
        "median_queue_ahead": statistics.median(queues) if queues else None,

        # Edge & Spread capture
        "spread_capture": spread_capture,
        "spread_capture_per_share": (spread_capture / filled_sh) if filled_sh else None,
        "avg_edge_cents": (statistics.mean(edges) * 100) if edges else None,
        "cost": cost,

        # Maker diagnostics
        "fill_by_queue": fill_by_queue,
        "partial_quotes": len(partial),
        "full_quotes": len(fully),
        "partial_fill_shares_missing": unfilled_of_partials,
        "quote_uptime": quote_uptime,
        "top_skip_reasons": [{"reason": r, "cycles": n} for r, n in top_skips],
        "pair_cost_distribution": sorted(pairs),

        # Inventory discipline
        "median_balance": statistics.median(balances) if balances else None,
        "median_pair_cost": statistics.median(pairs) if pairs else None,
        "pairs_under_1": (100.0 * sum(1 for p in pairs if p < 1.0) / len(pairs)) if pairs else None,

        # Outcome
        "realized_pnl": realized_pnl,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / len(closes)) if closes else None,
        "avg_win": statistics.mean(wins) if wins else 0.0,
        "avg_loss": statistics.mean(losses) if losses else 0.0,
        "roi_on_cost": (realized_pnl / cost) if cost else None,

        # Adverse selection & rebate
        "adverse_selection": adverse_selection,
        "rebate_est": None,  # Explicit NULL: graduated spread markets carry $0.00 maker rewards; rebate accrual disabled
        "rebate_est_note": "NULL: graduated spread markets carry $0.00 maker rewards; income derives strictly from merge spread capture",
        "total_with_rebate": realized_pnl,

        # Bankroll & Census
        "equity": _CFG.bankroll_usd + realized_pnl,
        "bankroll": _CFG.bankroll_usd,
        "census": census,
        "settlements": closes[:60],

        # 4 Live-Specific Operational Metrics
        "order_latency_ms": order_latency_ms,
        "reconcile_lag_ms": reconcile_lag_ms,
        "venue_rejects": venue_rejects,
        "three_way_divergences": three_way_divergences,
    }
