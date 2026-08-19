"""live/engine/kpi.py - Live KPI report mirroring strategy/kpi.py:124-410.

Computes exact matching metrics from live/run/live.db across 3 levels:
1. Run level: maker fill rate, uptime, spread capture, markout drift, PnL by method, ROI.
2. Market level: per-market drill-down with 4-horizon markouts (5m/1h/6h/15m), quotes vs mid, skips, settlement.
3. Mechanics level: order latency, reconcile lag, venue errors by code, 3-way divergences.
Plus multi-run isolation (run selector) and float_marks time series.
"""

from __future__ import annotations

import json
import statistics
import time
from pathlib import Path
from typing import Any, Optional

from engine.order_registry import OrderRegistry, DEFAULT_DB_PATH
from engine.config import load as load_cfg

_CFG = load_cfg()
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Shown when neither the ranker feed nor its fallbacks name a category. A named
# bucket groups honestly; a blank cell reads as missing data.
UNCATEGORIZED = "Uncategorized"


def _resolve_market_meta(cid: str, closes: list[dict], quotes: list[dict]) -> dict[str, Any]:
    """Resolve human-readable title, slug, and Polymarket link for a condition_id from disk."""
    out = {
        "condition_id": cid,
        "title": None,
        "slug": None,
        "url": None,
        "category": None,
        "days_to_resolve": None,
        "min_size": None,
        "volume_24h": None,
        "source": None,
    }
    if not cid:
        out["category"] = UNCATEGORIZED
        return out
    
    # Try reading run/markets.json from repo root (written by ranker)
    try:
        feed_path = REPO_ROOT / "run" / "markets.json"
        if feed_path.exists():
            feed = json.loads(feed_path.read_text(encoding="utf-8"))
            for row in feed if isinstance(feed, list) else []:
                if (row.get("cid") or "").lower() == cid.lower():
                    out.update({
                        "title": row.get("title") or row.get("event_title"),
                        "slug": row.get("slug"),
                        # The live feed ships category="" on most rows, so the
                        # series and the group are the labels that actually
                        # survive. An empty cell teaches the reader nothing.
                        "category": (
                            (row.get("category") or "").strip()
                            or (row.get("series_title") or "").strip()
                            or (row.get("market_group") or "").strip()
                            or None
                        ),
                        "days_to_resolve": row.get("days_to_resolve"),
                        "min_size": row.get("min_size"),
                        "volume_24h": row.get("volume_24h"),
                        "source": row.get("source"),
                    })
                    break
    except Exception:
        pass

    # Fallback to closes or quotes market_slug
    if not out["slug"]:
        for c in closes:
            if c.get("condition_id") == cid and c.get("market_slug"):
                out["slug"] = c["market_slug"]
                break
    if not out["slug"]:
        for q in quotes:
            if q.get("condition_id") == cid and q.get("market_slug"):
                out["slug"] = q["market_slug"]
                break

    if not out["title"] and out["slug"]:
        out["title"] = out["slug"].replace("-", " ").title()
    elif not out["title"]:
        out["title"] = f"Market {cid[:10]}...{cid[-6:]}" if len(cid) > 16 else cid

    if out["slug"]:
        out["url"] = f"https://polymarket.com/market/{out['slug']}"
    if not out["category"]:
        out["category"] = UNCATEGORIZED
    return out


def list_runs(reg: OrderRegistry) -> list[dict[str, Any]]:
    """List all distinct run_ids with metadata summary in reverse chronological order."""
    with reg._conn() as conn:
        tables = {"orders", "quotes", "fills", "closes", "market_events", "markouts", "float_marks", "venue_errors", "divergence_events"}
        existing_tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        
        runs_map: dict[str, dict[str, Any]] = {}

        for tbl in (tables & existing_tables):
            # Check if run_id column exists
            cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({tbl})").fetchall()}
            if "run_id" not in cols:
                continue
            
            ts_col = "ts" if "ts" in cols else ("venue_ts" if "venue_ts" in cols else ("posted_ts" if "posted_ts" in cols else None))
            query = f"SELECT run_id, COUNT(*) as cnt" + (f", MIN({ts_col}) as min_ts, MAX({ts_col}) as max_ts" if ts_col else "") + f" FROM {tbl} WHERE run_id IS NOT NULL AND run_id != '' GROUP BY run_id"
            for r in conn.execute(query).fetchall():
                r_id = r["run_id"]
                slot = runs_map.setdefault(r_id, {
                    "run_id": r_id,
                    "first_ts": None,
                    "last_ts": None,
                    "orders_count": 0,
                    "quotes_count": 0,
                    "fills_count": 0,
                    "closes_count": 0,
                    "realized_pnl": 0.0,
                })
                if ts_col and r["min_ts"] is not None:
                    min_t = float(r["min_ts"]) / (1000.0 if ts_col in ("venue_ts", "posted_ts") and float(r["min_ts"]) > 1e11 else 1.0)
                    slot["first_ts"] = min(slot["first_ts"], min_t) if slot["first_ts"] is not None else min_t
                if ts_col and r["max_ts"] is not None:
                    max_t = float(r["max_ts"]) / (1000.0 if ts_col in ("venue_ts", "posted_ts") and float(r["max_ts"]) > 1e11 else 1.0)
                    slot["last_ts"] = max(slot["last_ts"], max_t) if slot["last_ts"] is not None else max_t

                if tbl == "orders":
                    slot["orders_count"] += int(r["cnt"])
                elif tbl == "quotes":
                    slot["quotes_count"] += int(r["cnt"])
                elif tbl == "fills":
                    slot["fills_count"] += int(r["cnt"])
                elif tbl == "closes":
                    slot["closes_count"] += int(r["cnt"])

        # Add closes realized_pnl
        if "closes" in existing_tables:
            for r in conn.execute("SELECT run_id, SUM(COALESCE(realized_pnl, 0.0)) as pnl FROM closes WHERE run_id IS NOT NULL GROUP BY run_id").fetchall():
                if r["run_id"] in runs_map:
                    runs_map[r["run_id"]]["realized_pnl"] = float(r["pnl"] or 0.0)

        runs_list = list(runs_map.values())
        runs_list.sort(key=lambda x: (x["last_ts"] or 0.0), reverse=True)
        return runs_list


def report(db_path: Path | str | None = None, run_id: Optional[str] = None) -> dict[str, Any]:
    """Generate live KPI dictionary mirroring strategy/kpi.py report() structure with live enhancements."""
    reg = OrderRegistry(db_path if db_path is not None else DEFAULT_DB_PATH)
    
    all_quotes = reg.get_all_quotes()
    all_fills = reg.get_all_fills()
    all_closes = reg.get_all_closes()
    all_market_events = reg.get_all_market_events()
    all_markouts = reg.get_all_markouts()
    census_rows = reg.get_all_hedge_census()
    all_venue_errs = reg.get_all_venue_errors()
    all_divergences = reg.get_all_divergence_events()
    all_float_marks = reg.get_all_float_marks()
    resolutions = reg.get_all_resolutions()

    runs = list_runs(reg)
    
    # Active run resolution
    active_run_id: Optional[str] = None
    if run_id == "all":
        active_run_id = "all"
    elif run_id:
        active_run_id = run_id
    elif runs:
        # Default to the most recent run
        active_run_id = runs[0]["run_id"]
    else:
        active_run_id = None

    # Filter by run_id unless 'all' or None
    if active_run_id and active_run_id != "all":
        quotes = [q for q in all_quotes if q.get("run_id") == active_run_id]
        fills = [f for f in all_fills if f.get("run_id") == active_run_id]
        closes = [c for c in all_closes if c.get("run_id") == active_run_id]
        market_events = [e for e in all_market_events if e.get("run_id") == active_run_id]
        markouts = [m for m in all_markouts if m.get("run_id") == active_run_id]
        venue_errs = [v for v in all_venue_errs if v.get("run_id") == active_run_id]
        divergences = [d for d in all_divergences if d.get("run_id") == active_run_id]
        float_marks = [fm for fm in all_float_marks if fm.get("run_id") == active_run_id]
    else:
        quotes = all_quotes
        fills = all_fills
        closes = all_closes
        market_events = all_market_events
        markouts = all_markouts
        venue_errs = all_venue_errs
        divergences = all_divergences
        float_marks = all_float_marks

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
    # No quote telemetry means capture was never measured. Reporting 0 here would
    # assert "we captured nothing" next to a +$0.30 realised PnL tile -- the same
    # class of instrumentation lie the research log keeps catching. Stay NULL.
    spread_capture = sum(cap_list) if cap_list else None

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

    # Map token_id to quote side if available
    token_side_map: dict[str, str] = {}
    for q in quotes:
        if q.get("token_id") and q.get("side"):
            token_side_map[q["token_id"]] = str(q["side"]).upper()

    # Group fills & market metrics per market
    all_cids = {
        cid for cid in (
            [q.get("condition_id") for q in quotes] +
            [f.get("condition_id") for f in fills] +
            [c.get("condition_id") for c in closes] +
            [m.get("condition_id") for m in markouts] +
            [e.get("condition_id") for e in market_events] +
            [v.get("condition_id") for v in venue_errs]
        ) if cid
    }

    by_mkt: dict[str, dict[str, Any]] = {}
    for cid in all_cids:
        meta = _resolve_market_meta(cid, closes, quotes)
        m_quotes = [q for q in quotes if q.get("condition_id") == cid]
        m_fills = [f for f in fills if f.get("condition_id") == cid]
        m_closes = [c for c in closes if c.get("condition_id") == cid]
        m_markouts = [m for m in markouts if m.get("condition_id") == cid]
        m_events = [e for e in market_events if e.get("condition_id") == cid]
        m_errs = [v for v in venue_errs if v.get("condition_id") == cid]

        # Group fills by token / side
        tok_list = sorted(list({f["token_id"] for f in m_fills if f.get("token_id")}))
        tok_a = tok_list[0] if len(tok_list) > 0 else None

        up_fills = [
            f for f in m_fills
            if token_side_map.get(f.get("token_id")) in ("UP", "YES", "LONG")
            or (f.get("token_id") == tok_a and token_side_map.get(f.get("token_id")) not in ("DOWN", "NO", "DN", "SHORT"))
            or str(f.get("side")).upper() in ("UP", "YES")
        ]
        dn_fills = [f for f in m_fills if f not in up_fills]

        up_sh = sum(float(f.get("size") or 0.0) for f in up_fills)
        dn_sh = sum(float(f.get("size") or 0.0) for f in dn_fills)
        up_cost = sum(float(f.get("size") or 0.0) * float(f.get("price") or 0.0) for f in up_fills)
        dn_cost = sum(float(f.get("size") or 0.0) * float(f.get("price") or 0.0) for f in dn_fills)
        m_pnl = sum(float(c.get("realized_pnl") or 0.0) for c in m_closes)

        # Markout horizons for this market
        formatted_markouts = []
        for mo in m_markouts:
            fp = float(mo.get("fill_price") or 0.0)
            formatted_markouts.append({
                "ts": mo.get("ts"),
                "side": mo.get("side"),
                "size": float(mo.get("size") or 0.0),
                "fill_price": fp,
                "ref_mid": mo.get("ref_mid"),
                "ref_mid_source": mo.get("ref_mid_source"),
                "mid_h0": mo.get("mid_h0"),  # 5m (300s)
                "mid_h1": mo.get("mid_h1"),  # 1h (3600s)
                "mid_h2": mo.get("mid_h2"),  # 6h (21600s)
                "mid_h3": mo.get("mid_h3"),  # 15m (900s)
                "drift_h0": (float(mo["mid_h0"]) - fp) if mo.get("mid_h0") is not None else None,
                "drift_h1": (float(mo["mid_h1"]) - fp) if mo.get("mid_h1") is not None else None,
                "drift_h2": (float(mo["mid_h2"]) - fp) if mo.get("mid_h2") is not None else None,
                "drift_h3": (float(mo["mid_h3"]) - fp) if mo.get("mid_h3") is not None else None,
                "done": mo.get("done", 0),
            })

        by_mkt[cid] = {
            **meta,
            "up_sh": up_sh,
            "dn_sh": dn_sh,
            "up_cost": up_cost,
            "dn_cost": dn_cost,
            "total_sh": up_sh + dn_sh,
            "total_cost": up_cost + dn_cost,
            "fills_count": len(m_fills),
            "quotes_count": len(m_quotes),
            "pair_cost": ((up_cost / up_sh) + (dn_cost / dn_sh)) if (up_sh > 0 and dn_sh > 0) else None,
            "balance": (min(up_sh, dn_sh) / max(up_sh, dn_sh)) if max(up_sh, dn_sh) > 0 else None,
            "realized_pnl": m_pnl,
            "quotes": m_quotes,
            "fills": m_fills,
            "markouts": formatted_markouts,
            "skip_events": m_events,
            "venue_errors": m_errs,
            "settlements": m_closes,
        }

    balances = [m["balance"] for m in by_mkt.values() if m["balance"] is not None]
    pairs = [m["pair_cost"] for m in by_mkt.values() if m["pair_cost"] is not None]

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

    skip_reasons: dict[str, list[dict[str, Any]]] = {}
    for e in market_events:
        if e.get("kind") in ("BLOCKED", "DECISION") or e.get("reason_code") != "INTENT_GENERATED":
            code = e.get("reason_code") or "OTHER"
            skip_reasons.setdefault(code, []).append({
                "title": e.get("market_slug") or e.get("condition_id") or "market",
                "reason": e.get("reason") or code,
                "ts": e.get("ts"),
            })
    
    top_skips = sorted([(code, len(items)) for code, items in skip_reasons.items()], key=lambda kv: -kv[1])[:6]

    # Funnel view (RAW -> FILTERS -> FINAL -> GRADUATED)
    funnel_filters = [
        {"cause": code, "n": len(items), "examples": items[:5]}
        for code, items in sorted(skip_reasons.items(), key=lambda kv: -len(kv[1]))
    ]
    funnel = {
        "raw_count": max(len(census_rows), len(all_cids)),
        "filters": funnel_filters,
        "final_count": len({q["condition_id"] for q in quotes if q.get("condition_id")}),
        "graduated": [
            {"condition_id": cid, "slug": m["slug"], "title": m["title"], "fills": m["fills_count"], "pnl": m["realized_pnl"]}
            for cid, m in by_mkt.items() if m["fills_count"] > 0 or m["quotes_count"] > 0
        ],
    }

    # Adverse selection from size-weighted markouts
    markout_drifts = []
    for m in markouts:
        sz = float(m.get("size") or 1.0)
        # longest matured horizon (prefer h2=6h, h1=1h, h3=15m, h0=5m)
        m_later = None
        for col in ("mid_h2", "mid_h1", "mid_h3", "mid_h0"):
            if m.get(col) is not None:
                m_later = float(m[col])
                break
        if m_later is not None:
            fp = float(m.get("fill_price") or 0.0)
            drift = m_later - fp
            markout_drifts.append(drift * sz)

    # NULL, not 0.0: zero markout samples means undrifted-unknown, not undrifted.
    adverse_selection = (sum(markout_drifts) / filled_sh) if (filled_sh > 0 and markout_drifts) else None

    # Hedge Census
    census = {
        "markets_observed": len(census_rows),
        "fillable": sum(1 for r in census_rows if r.get("fillable_sub_one")),
        "fillable_rate": (sum(1 for r in census_rows if r.get("fillable_sub_one")) / len(census_rows)) if census_rows else None,
        "median_pair_at_touch": statistics.median([float(r["pair_cost_at_touch"]) for r in census_rows if r.get("pair_cost_at_touch") is not None]) if census_rows else None,
    }

    # Mechanics: 4 Live-Specific Metrics
    latencies = [float(q["latency_ms"]) for q in quotes if q.get("latency_ms") is not None]
    order_latency_ms = {
        "median": statistics.median(latencies) if latencies else None,
        "max": max(latencies) if latencies else None,
        "count": len(latencies),
        "samples": latencies[:50],
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
        "samples": reconcile_lags[:50],
    }

    venue_rejects = {
        "total": len(venue_errs),
        "by_code": {},
        "events": [
            {
                "ts": ve.get("ts"),
                "condition_id": ve.get("condition_id"),
                "code": ve.get("error_code") or "ERROR",
                "message": ve.get("raw_error_msg") or "",
                "side": ve.get("side"),
                "price": ve.get("price"),
                "size": ve.get("size"),
            }
            for ve in venue_errs[:20]
        ],
    }
    for ve in venue_errs:
        c = ve.get("error_code") or "ERROR"
        venue_rejects["by_code"][c] = venue_rejects["by_code"].get(c, 0) + 1

    three_way_divergences = {
        "total": len(divergences),
        "events": divergences[:20],
    }

    # ------------------------------------------------------------------
    # Portfolio overview: the whole run in one reading, not the first market.
    # Mirrors the simulation's capitalSeries widget (server/spread_dash_html.py:175):
    # realised closes stacked on the starting bankroll, with the open float
    # folded in at the timestamps it was actually marked.
    # ------------------------------------------------------------------
    starting_capital = _CFG.bankroll_usd
    sorted_closes = sorted(
        [c for c in closes if c.get("ts") is not None], key=lambda c: float(c["ts"])
    )
    sorted_marks = sorted(
        [fm for fm in float_marks if fm.get("ts") is not None], key=lambda fm: float(fm["ts"])
    )
    # NULL, not 0.0: nothing in the engine calls log_float_mark today, so an
    # empty float_marks table means the open float was never measured. Printing
    # a measured $0.00 next to a resting naked leg is the same instrumentation
    # lie that spread_capture and adverse_selection above already refuse to tell.
    latest_mark = sorted_marks[-1] if sorted_marks else None
    # A mark recorded before the newest close describes a book that no longer
    # exists: its float has since been realised and is already in realized_pnl.
    # Counting both would bill the same money twice on the headline tile.
    latest_close_ts = float(sorted_closes[-1]["ts"]) if sorted_closes else None
    mark_is_current = (
        latest_mark is not None
        and (latest_close_ts is None or float(latest_mark["ts"]) >= latest_close_ts)
    )
    unrealized_usd = float(latest_mark.get("unrealized_usd") or 0.0) if mark_is_current else None
    open_committed_usd = (
        float(latest_mark.get("committed_open_usd") or 0.0) if mark_is_current else None
    )

    equity_series: list[dict[str, Any]] = []
    running_equity = starting_capital
    running_float = 0.0
    mark_idx = 0

    def _fold_marks_through(t: float) -> None:
        """Push a point for every mark recorded at or before t."""
        nonlocal mark_idx, running_float
        while mark_idx < len(sorted_marks) and float(sorted_marks[mark_idx]["ts"]) <= t:
            running_float = float(sorted_marks[mark_idx].get("unrealized_usd") or 0.0)
            equity_series.append({
                "ts": float(sorted_marks[mark_idx]["ts"]),
                "v": running_equity + running_float,
                "type": "mark",
                "unrealized_usd": running_float,
            })
            mark_idx += 1

    for c in sorted_closes:
        c_ts = float(c["ts"])
        _fold_marks_through(c_ts)
        running_equity += float(c.get("realized_pnl") or 0.0)
        # The float measured before this close described positions this close has
        # now realised -- realized_pnl already holds that money. Carrying it past
        # the close would count the same dollars twice on the curve, the way the
        # portfolio tile would have before the stale-mark guard above.
        running_float = 0.0
        equity_series.append({
            "ts": c_ts,
            "v": running_equity,
            "type": "close",
            "pnl": float(c.get("realized_pnl") or 0.0),
            "market": c.get("market_slug") or c.get("condition_id"),
        })
    # Marks recorded after the last close: the curve keeps stepping on float alone.
    _fold_marks_through(float("inf"))

    # An unmeasured float contributes nothing to the total, and the page says so
    # rather than folding an unknown into a number labelled "Total Value".
    total_pnl = realized_pnl + (unrealized_usd or 0.0)
    # Markets that only ever appear in a refusal or a venue error were never
    # traded. Counting them would report "2 markets" for a run that quoted one.
    traded_markets = sum(
        1 for m in by_mkt.values()
        if m["fills_count"] > 0 or m["quotes_count"] > 0 or m["settlements"]
    )
    portfolio = {
        "starting_capital": starting_capital,
        "realized_pnl": realized_pnl,
        "unrealized_usd": unrealized_usd,
        "unrealized_measured": mark_is_current,
        "total_pnl": total_pnl,
        "total_value": starting_capital + total_pnl,
        # NULL, not 0.0: a zero bankroll makes the percentage undefined, and a
        # printed 0.00% would read as "flat" rather than "unmeasurable".
        "pnl_pct": (100.0 * total_pnl / starting_capital) if starting_capital else None,
        "markets_count": traded_markets,
        "closes_count": len(closes),
        "open_committed_usd": open_committed_usd,
    }

    # Float marks formatted series for time chart
    float_marks_formatted = [
        {
            "ts": float(fm["ts"]),
            "unrealized_usd": float(fm.get("unrealized_usd") or 0.0),
            "committed_open_usd": float(fm.get("committed_open_usd") or 0.0),
            "naked_usd": float(fm.get("naked_usd") or 0.0),
            "run_id": fm.get("run_id"),
        }
        for fm in float_marks
    ]

    return {
        # Multi-run metadata
        "runs": runs,
        "active_run_id": active_run_id,

        # Portfolio overview (run-level, all markets)
        "portfolio": portfolio,
        "equity_series": equity_series,

        # Pace
        "markets_quoted": len({q["condition_id"] for q in quotes if q.get("condition_id")}),
        "markets_filled": len({f["condition_id"] for f in fills if f.get("condition_id")}),
        "markets_settled": len(closes),
        "fills": len(fills),
        "quotes": len(quotes),
        "days": days,
        "fills_per_day": (len(fills) / days) if days > 0.01 else None,
        "notional_per_day": (cost / days) if days > 0.01 else None,

        # Maker metrics (Level 1)
        "fill_rate": ((filled_sh - crossed_sh) / posted_sh) if posted_sh else None,
        "posted_shares": posted_sh,
        "filled_shares": filled_sh,
        "crossed_shares": crossed_sh,
        "taker_fees_paid": sum(float(c.get("fee") or 0.0) for c in closes),
        "median_seconds_to_fill": statistics.median(waits) if waits else None,
        "median_queue_ahead": statistics.median(queues) if queues else None,

        # Edge & Spread capture
        "spread_capture": spread_capture,
        "spread_capture_per_share": (spread_capture / filled_sh) if (spread_capture is not None and filled_sh) else None,
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
        "markout_samples": len(markout_drifts),
        "rebate_est": None,  # Explicit NULL: graduated spread markets carry $0.00 maker rewards; rebate accrual disabled
        "rebate_est_note": "NULL: graduated spread markets carry $0.00 maker rewards; income derives strictly from merge spread capture",
        "total_with_rebate": realized_pnl,

        # Bankroll & Census
        "equity": _CFG.bankroll_usd + realized_pnl,
        "bankroll": _CFG.bankroll_usd,
        "census": census,
        "settlements": closes[:60],

        # Level 2: Market drilldown & Funnel
        "by_market": by_mkt,
        "funnel": funnel,

        # Level 3: Mechanics Diagnostics
        "order_latency_ms": order_latency_ms,
        "reconcile_lag_ms": reconcile_lag_ms,
        "venue_rejects": venue_rejects,
        "three_way_divergences": three_way_divergences,

        # Req 4: Exposure time series
        "float_marks": float_marks_formatted,
    }
