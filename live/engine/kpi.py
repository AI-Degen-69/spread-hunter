"""live/engine/kpi.py - Live KPI report mirroring strategy/kpi.py:124-410.

Computes exact matching metrics from live/run/live.db across 3 levels:
1. Run level: maker fill rate, uptime, spread capture, markout drift, PnL by method, ROI.
2. Market level: per-market drill-down with 4-horizon markouts (5m/1h/6h/15m), quotes vs mid, skips, settlement.
3. Mechanics level: order latency, reconcile lag, venue errors by code, 3-way divergences.
Plus multi-run isolation (run selector) and float_marks time series.
"""

from __future__ import annotations

import json
import math
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


def _wilson_ci(successes: int, n: int, z: float = 1.96) -> Optional[dict[str, float]]:
    """Wilson score interval for a binomial proportion (the win rate).

    The normal-approximation interval misbehaves at n=1 or all-wins/losses;
    Wilson stays inside [0, 1] there. z=1.96 is the 95% two-sided bound.
    """
    if n <= 0:
        return None
    p = successes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denom
    half = (z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n)) / denom
    return {
        "lower": max(0.0, center - half),
        "upper": min(1.0, center + half),
    }


def compute_trade_analytics(
    closes: list[dict],
    starting_capital: float,
    equity_series: list[dict],
    float_marks: list[dict],
) -> dict[str, Any]:
    """Per-trade outcome statistics and risk factors for the Level 1 tiles.

    A "trade" is one closed position (a `closes` row). `realized_pnl` is the
    absolute net gain/loss on it -- the $1.50 in "committed $5, returned
    $6.50" -- and `cost_basis` is the $5. Return % is pnl / cost_basis, so the
    same position also reads +30%.

    Every field is NULL when it cannot be measured, never a fabricated zero:
    a one-close run has no stdev, Sharpe, or CI; a no-close run has no drawdown.
    """
    n = len(closes)
    pnl_distribution: list[dict[str, Any]] = []
    wins: list[float] = []
    losses: list[float] = []
    return_pcts: list[float] = []

    for c in closes:
        pnl = float(c.get("realized_pnl") or 0.0)
        cost = c.get("cost_basis")
        cost_f = float(cost) if cost is not None else None
        return_pct = (100.0 * pnl / cost_f) if (cost_f is not None and cost_f > 0) else None
        if return_pct is not None:
            return_pcts.append(return_pct)
        if pnl > 0:
            wins.append(pnl)
        else:
            losses.append(pnl)
        pnl_distribution.append({
            "ts": c.get("ts"),
            "condition_id": c.get("condition_id"),
            "market_slug": c.get("market_slug"),
            "method": c.get("method"),
            "realized_pnl": pnl,
            "cost_basis": cost_f,
            "return_pct": return_pct,
        })
    pnl_distribution.sort(key=lambda r: (r["ts"] if r["ts"] is not None else 0.0))

    n_wins = len(wins)
    n_losses = len(losses)
    win_rate = (n_wins / n) if n else None
    win_rate_ci95 = _wilson_ci(n_wins, n) if n else None

    expectancy_usd = statistics.mean(wins + losses) if n else None
    mean_return_pct = statistics.mean(return_pcts) if return_pcts else None
    stdev_return_pct = statistics.stdev(return_pcts) if len(return_pcts) > 1 else None

    ci90_lower_pct: Optional[float] = None
    ci95_return_pct: Optional[dict[str, float]] = None
    if mean_return_pct is not None and stdev_return_pct is not None and len(return_pcts) > 1:
        se = stdev_return_pct / math.sqrt(len(return_pcts))
        # One-sided 90% lower bound (the "is expectancy positive?" gate the
        # simulation uses), plus a 95% two-sided band for context.
        ci90_lower_pct = mean_return_pct - 1.645 * se
        ci95_return_pct = {
            "lower": mean_return_pct - 1.96 * se,
            "upper": mean_return_pct + 1.96 * se,
        }

    avg_win_usd = statistics.mean(wins) if wins else None
    avg_loss_usd = statistics.mean(losses) if losses else None
    # Classic reward:risk = average win / average loss (magnitude). NULL when
    # there is no loss to measure against (an all-win run has no downside yet).
    risk_reward_ratio = (
        avg_win_usd / abs(avg_loss_usd)
        if avg_win_usd is not None and avg_loss_usd is not None and avg_loss_usd != 0.0
        else None
    )
    gross_wins = sum(wins)
    gross_losses = abs(sum(losses))
    profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else None

    # Per-trade Sharpe/Sortino on the return distribution (no annualisation:
    # trades are not daily observations, and annualising a 3-trade sample would
    # manufacture a number the sample never earned).
    sharpe_ratio: Optional[float] = None
    sortino_ratio: Optional[float] = None
    if len(return_pcts) > 1 and stdev_return_pct and stdev_return_pct > 0:
        sharpe_ratio = mean_return_pct / stdev_return_pct
        downside = [r for r in return_pcts if r < 0]
        downside_dev = math.sqrt(sum(r * r for r in downside) / len(downside)) if downside else 0.0
        if downside_dev > 0:
            sortino_ratio = mean_return_pct / downside_dev

    # Max drawdown from the run-level equity curve (realised closes stacked on
    # the bankroll, open float folded in at its marks).
    max_drawdown_usd: Optional[float] = None
    max_drawdown_pct: Optional[float] = None
    if equity_series:
        peak = starting_capital
        max_dd_usd = 0.0
        max_dd_pct = 0.0
        for pt in equity_series:
            v = float(pt.get("v") or 0.0)
            if v > peak:
                peak = v
            dd = peak - v
            if dd > max_dd_usd:
                max_dd_usd = dd
                max_dd_pct = (100.0 * dd / peak) if peak > 0 else 0.0
        max_drawdown_usd = max_dd_usd
        max_drawdown_pct = max_dd_pct

    # Inventory risk: the largest naked (one-sided) dollar exposure ever marked.
    naked_vals = [float(fm.get("naked_usd") or 0.0) for fm in float_marks]
    max_naked_exposure_usd = max(naked_vals) if naked_vals else None

    return {
        "n_closes": n,
        "wins": n_wins,
        "losses": n_losses,
        "win_rate": win_rate,
        "win_rate_ci95": win_rate_ci95,
        "expectancy_usd": expectancy_usd,
        "mean_return_pct": mean_return_pct,
        "stdev_return_pct": stdev_return_pct,
        "ci90_lower_pct": ci90_lower_pct,
        "ci95_return_pct": ci95_return_pct,
        "avg_win_usd": avg_win_usd,
        "avg_loss_usd": avg_loss_usd,
        "risk_reward_ratio": risk_reward_ratio,
        "profit_factor": profit_factor,
        "sharpe_ratio": sharpe_ratio,
        "sortino_ratio": sortino_ratio,
        "max_drawdown_usd": max_drawdown_usd,
        "max_drawdown_pct": max_drawdown_pct,
        "max_naked_exposure_usd": max_naked_exposure_usd,
        "pnl_distribution": pnl_distribution,
    }


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


def _funnel_from_pipeline(
    by_mkt: dict[str, dict[str, Any]],
    pipeline_path: Path | str | None = None,
    markets_path: Path | str | None = None,
) -> Optional[dict[str, Any]]:
    """Build the Level 2 funnel from the screener's own snapshot.

    `run/pipeline.json` is the same file the sim scan (server/fleet_dash.py)
    renders, so sourcing the funnel from it makes the live Level 2 lanes
    compare 1:1 with the sim scan: identical gate names ("volume", "YES:
    top-3 bid depth", "horizon", ...) and identical counts. GRADUATED is the
    ranker's run/markets.json picks annotated with this run's live fills and
    realized PnL, so the lane reads live before any quote exists.

    Returns None when the ranker hasn't written a snapshot yet, so the caller
    falls back to runtime market-event telemetry.
    """
    pp = Path(pipeline_path) if pipeline_path is not None else (REPO_ROOT / "run" / "pipeline.json")
    if not pp.is_file():
        return None
    try:
        snap = json.loads(pp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(snap, dict):
        return None

    counts = snap.get("counts") or {}
    raw_count = int(counts.get("funded") or 0) + int(counts.get("spread_universe") or 0)

    filters = [
        {
            "cause": r.get("cause") or "other",
            "n": int(r.get("n") or 0),
            "examples": [
                {"title": e.get("title") or "", "reason": e.get("reason") or ""}
                for e in (r.get("examples") or [])
            ],
        }
        for r in (snap.get("rejections") or [])
        if isinstance(r, dict)
    ]

    graduated: list[dict[str, Any]] = []
    mp = Path(markets_path) if markets_path is not None else (REPO_ROOT / "run" / "markets.json")
    specs: list = []
    if mp.is_file():
        try:
            specs = json.loads(mp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            specs = []
    if not isinstance(specs, list):
        specs = []
    for s in specs:
        if not isinstance(s, dict):
            continue
        # Skip markets the ranker recorded as already resolved. The gamma query
        # already excludes closed markets, but a market can resolve between the
        # rank pass and the dashboard read; days_to_resolve < 0 means expired.
        dtr = s.get("days_to_resolve")
        if isinstance(dtr, (int, float)) and dtr < 0:
            continue
        cid = s.get("cid") or s.get("condition_id") or ""
        m = by_mkt.get(cid, {})
        graduated.append({
            "condition_id": cid,
            "slug": s.get("slug") or "",
            "title": s.get("title") or s.get("question") or "",
            "fills": m.get("fills_count", 0),
            "pnl": m.get("realized_pnl", 0.0),
        })

    # Snapshot metadata so the dashboard can show where the lanes came
    # from and how fresh they are.
    snap_ts = snap.get("ts")
    snapshot_age = (time.time() - snap_ts) if snap_ts else None

    return {
        "raw_count": raw_count,
        "filters": filters,
        "final_count": int(counts.get("eligible") or 0),
        "graduated": graduated,
        "source": "screener",
        "snapshot_age": snapshot_age,
        "census": snap.get("census") or "",
        "gates": snap.get("gates") or "",
    }


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
    all_orders = reg.get_all_orders()
    # Deliberately NOT filtered by run_id below. An account balance belongs to
    # the wallet, not to a run: slicing it per run would report the account as
    # empty for any run that happened not to sweep.
    all_account_marks = reg.get_all_account_marks()
    resolutions = reg.get_all_resolutions()

    runs = list_runs(reg)

    # Active run resolution
    active_run_id: Optional[str] = None
    if run_id == "all":
        active_run_id = "all"
    elif run_id:
        active_run_id = run_id
    elif runs:
        # Default to the most recent run.
        # BUT: a process restart orphans fills under a defunct run_id while the
        # new process's run_id has orders + quotes and zero fills. Picking the
        # most-recent run there would render the dashboard empty even though the
        # venue saw real fills minutes ago. Fall through to "all" when the
        # chosen run has no fills but any earlier run does -- the operator
        # would rather see 30s of stale fills than a clean zeros grid.
        chosen = runs[0]
        chosen_has_fills = chosen.get("fills_count", 0) > 0
        any_earlier_has_fills = any(
            r.get("fills_count", 0) > 0 for r in runs[1:]
        )
        if chosen_has_fills or not any_earlier_has_fills:
            active_run_id = chosen["run_id"]
        else:
            active_run_id = "all"
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
        orders = [o for o in all_orders if o.get("run_id") == active_run_id]
    else:
        quotes = all_quotes
        fills = all_fills
        closes = all_closes
        market_events = all_market_events
        markouts = all_markouts
        venue_errs = all_venue_errs
        divergences = all_divergences
        float_marks = all_float_marks
        orders = all_orders

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

    # Drop markets that have already resolved.
    #
    # A market leaves "MARKETS IN RUN" once the venue reports it settled. The
    # account sweep writes those as `closes` rows with method='venue_sync'
    # (distinct from local merge/sell closes, which are still-open trades the
    # operator wants to see). A negative `days_to_resolve` from run/markets.json
    # is the same fact from the ranker's side. Both are durable, venue-free
    # signals already in the registry.
    #
    # Markets that were merely blocked, quoted-then-cancelled, or never opened
    # here are KEPT: they were touched by the bot this run and belong in the
    # drill-down. `days_to_resolve` of None is kept (unknown rank freshness
    # must never silently drop a market).
    resolved_cids = {
        c.get("condition_id") for c in closes
        if c.get("condition_id") and c.get("method") == "venue_sync"
    }
    by_mkt = {
        cid: m for cid, m in by_mkt.items()
        if cid not in resolved_cids
        and not (m.get("days_to_resolve") is not None and m.get("days_to_resolve") < 0)
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

    # Funnel view (RAW -> FILTERS -> FINAL -> GRADUATED). Prefer the screener's
    # own snapshot so the gate names and counts compare 1:1 with the sim scan;
    # fall back to runtime market-event telemetry when the ranker hasn't written
    # a snapshot, or when serving a non-production db (a test/smoke db), where
    # the repo's snapshot would misrepresent that db's own telemetry.
    if db_path is None or Path(db_path).resolve() == DEFAULT_DB_PATH.resolve():
        pipeline_funnel = _funnel_from_pipeline(by_mkt)
    else:
        pipeline_funnel = None
    if pipeline_funnel is not None:
        funnel = pipeline_funnel
    else:
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
            "source": "runtime",
            "snapshot_age": None,
            "census": "",
            "gates": "",
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
    # ------------------------------------------------------------------
    # The account, as the venue reports it. `starting_capital` above is a
    # simulation constant that nobody deposited; these fields are the balance
    # and P&L the account holder sees on Polymarket, written by
    # `engine.live_exec account-sweep` and read here from SQLite.
    # ------------------------------------------------------------------
    sorted_account_marks = sorted(
        [am for am in all_account_marks if am.get("ts") is not None],
        key=lambda am: float(am["ts"]),
    )
    # Prefer the newest sweep that actually obtained an account value. A sweep
    # whose collateral read failed records NULL, and letting that NULL win would
    # blank the headline while a good reading from minutes earlier sits in the
    # table. The whole mark is taken from one row -- never assembled field by
    # field across rows -- and `ts` reports how old that row is, so the page can
    # say the reading is stale rather than pretend it is current.
    latest_account = next(
        (am for am in reversed(sorted_account_marks)
         if am.get("account_value_usd") is not None),
        sorted_account_marks[-1] if sorted_account_marks else None,
    )

    def _am(field: str) -> Optional[float]:
        """A field of the newest account mark, preserving NULL.

        `float(x or 0.0)` would turn an unmeasured field into a measured zero,
        which is the exact defect this whole change exists to remove.
        """
        if latest_account is None:
            return None
        v = latest_account.get(field)
        return None if v is None else float(v)

    account = {
        "measured": latest_account is not None,
        "ts": float(latest_account["ts"]) if latest_account else None,
        "source": (latest_account.get("source") if latest_account else None),
        "collateral_usd": _am("collateral_usd"),
        "positions_value_usd": _am("positions_value_usd"),
        "account_value_usd": _am("account_value_usd"),
        "pnl_usd": _am("pnl_usd"),
        "pnl_pct": _am("pnl_pct"),
        "pnl_closed_usd": _am("pnl_closed_usd"),
        "pnl_series_usd": _am("pnl_series_usd"),
        "unrealized_usd": _am("unrealized_usd"),
        "committed_usd": _am("committed_usd"),
        "open_positions_count": (
            None if latest_account is None or latest_account.get("open_positions_count") is None
            else int(latest_account["open_positions_count"])
        ),
        "closed_positions_count": (
            None if latest_account is None or latest_account.get("closed_positions_count") is None
            else int(latest_account["closed_positions_count"])
        ),
    }
    # No reconciliation against `starting_capital + total_pnl` is offered here.
    # That figure is the config bankroll -- a number nobody deposited -- so a
    # "gap" against it would restate the fabrication this change removed, in a
    # footnote. The registry records no deposits, so it has nothing real to
    # reconcile the venue's balance against.

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
        "account": account,
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

    # Per-trade win rate/expectancy/risk factors (Level 1 tiles). Computed from
    # the same closes the outcome block sums, plus the equity curve and marks
    # already assembled above.
    trade_analytics = compute_trade_analytics(
        closes=closes,
        starting_capital=starting_capital,
        equity_series=equity_series,
        float_marks=float_marks,
    )

    return {
        # Multi-run metadata
        "runs": runs,
        "active_run_id": active_run_id,

        # Portfolio overview (run-level, all markets)
        "portfolio": portfolio,
        "equity_series": equity_series,
        # The true account-value curve: one point per sweep, venue-sourced.
        # Marks that failed to reach the venue carry a NULL value and are
        # dropped here rather than plotted as a crash to zero.
        "account_series": [
            {"ts": float(am["ts"]), "v": float(am["account_value_usd"]),
             "pnl": (None if am.get("pnl_usd") is None else float(am["pnl_usd"]))}
            for am in sorted_account_marks
            if am.get("account_value_usd") is not None
        ],

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

        # Win rate + expectancy + risk-adjusted factors (Level 1)
        "trade_analytics": trade_analytics,

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
