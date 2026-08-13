"""Spread Hunter design, real fleet data.

Isolated dashboard: a visual migration of the `spread-hunter` mockup
(dark Swiss-brutalist UI) onto this fleet's actual numbers. Does not import
from or modify `server/fleet_dash.py` or `server/dashboard.py` beyond
calling their existing read-only functions -- both keep running unmodified
on their own ports. Every figure on this page traces back to
`strategy.stats` / `strategy.config` / `run/*.json`; nothing here is mock
copy carried over from the design source.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from starlette.middleware.gzip import GZipMiddleware

from server import fleet_dash
from strategy import stats
from strategy.config import load as load_config
from strategy.store import float_history, reason_code

ROOT = Path(__file__).resolve().parent.parent
CFG = load_config()

app = FastAPI(title="Hunter fleet -- spread hunter design")
# Both pages are large inline HTML blobs; gzip is the cheapest transfer win.
app.add_middleware(GZipMiddleware, minimum_size=1000)

# The dashboard endpoints re-read the fleet DB on every request; under the
# live writer's lock traffic a full `stats.snapshot()` read can take 9-12s,
# which makes the page look dead on every load. This is a monitoring view and
# the fleet's own pulse is written roughly every 10s, so an 8-second-stale
# snapshot is indistinguishable from live. Cache the two expensive payloads
# per process instead of re-reading the DB per request.
_DASH_TTL = 8.0
_DASH_CACHE: dict = {}
_DASH_LOCK = threading.Lock()
# Per-key in-flight marker for single-flight loading: a threading.Event that
# the owning request sets when it finishes (success or failure). A request
# that observes a miss while another request is loading waits on this instead
# of re-running the expensive fleet read -- without it, N requests arriving
# right after expiry each run `fleet_dash.fleet()` (~2s) under the same lock
# traffic they were caching to avoid.
_DASH_LOADING: dict = {}


def _cached(key: str, loader):
    while True:
        now = time.time()
        with _DASH_LOCK:
            hit = _DASH_CACHE.get(key)
            if hit and now - hit[0] < _DASH_TTL:
                return hit[1]
            other = _DASH_LOADING.get(key)
            if other is None:
                # CHECK AND INSTALL in one critical section: two racers must
                # not both observe a miss and both install a marker, or the
                # duplicate fleet read this exists to prevent comes back.
                ev = threading.Event()
                _DASH_LOADING[key] = ev
                break
        # Another request owns this load. Wait OUTSIDE the lock -- the loader
        # needs the lock to publish its result, so waiting while holding it
        # deadlocks. Then loop to re-check the cache, or to take over the load
        # if the owner failed and cleared its marker.
        other.wait()
    try:
        value = loader()
    except BaseException:
        with _DASH_LOCK:
            _DASH_LOADING.pop(key, None)
            ev.set()
        raise
    with _DASH_LOCK:
        _DASH_CACHE[key] = (time.time(), value)
        _DASH_LOADING.pop(key, None)
        ev.set()
    return value


def _cache_ts(key: str):
    """When the cached value for `key` was loaded, or None if never loaded.
    The dashboard's "Data as of" tile reads this so it reports the data's
    freshness, not the response time -- the fleet/pipeline values inside can
    be up to _DASH_TTL older than the response that carries them."""
    with _DASH_LOCK:
        hit = _DASH_CACHE.get(key)
        return hit[0] if hit else None


def _warm_cache() -> None:
    """Prime the dashboard cache shortly after startup so the first page
    load after a restart is not the slow one. Best-effort: a missing DB or
    state file just means the first real request pays the cold cost."""
    try:
        time.sleep(2.0)
        _cached("fleet", fleet_dash.fleet)
        _cached("pipeline", fleet_dash.pipeline)
    except Exception:
        pass


threading.Thread(target=_warm_cache, daemon=True).start()

# A small fixed cycle so an open-ended set of category tags (derived from
# market-slug prefixes, not a hardcoded sport enum) still gets a stable,
# legible color per tag instead of every bar being the same gray.
_CAT_PALETTE = ["#EF4444", "#3B82F6", "#9CA3AF", "#F59E0B", "#4B5563", "#10B981"]


def _cat_color(i: int) -> str:
    return _CAT_PALETTE[i % len(_CAT_PALETTE)]


def _market_status(r: dict) -> str:
    """Derive the reported posture of one fleet market row.

    Mixed paired + one-sided exposure is reported explicitly -- a "Paired
    (holding)" read would hide the naked inventory that can actually lose
    money at resolution.
    """
    err = r.get("err") or r.get("why")
    paired = r.get("paired", 0)
    naked = r.get("naked_sh", 0)
    has_quotes = len(r.get("quotes") or []) > 0

    if err:
        return err
    if paired > 0 and naked > 0:
        return "Paired + one side filled (15m window)"
    if paired > 0:
        return "Paired (holding)"
    if naked > 0:
        return "One side filled (15m window)"
    if has_quotes:
        return "Orders resting"
    return r.get("close_why") or r.get("merge_why") or "Inactive"


def _category(slug: str) -> str:
    parts = (slug or "?").split("-")
    tag = parts[0].lower()
    
    if tag in ["mlb", "atp", "wta", "nfl", "nba", "nhl", "soccer", "fifa", "ufc", "boxing", "f1", "tennis", "golf"]:
        return "Sports"
    elif tag in ["lol", "cs2", "csgo", "dota", "dota2", "val", "valorant", "esports"]:
        return "E-Sports"
    elif tag in ["pol", "politics", "election", "pres", "senate", "gop", "dem"]:
        return "Politics"
    elif tag in ["crypto", "btc", "eth", "sol", "defi", "nft"]:
        return "Crypto"
    elif tag in ["pop", "culture", "oscars", "grammys", "movie", "boxoffice"]:
        return "Pop Culture"
    elif tag in ["biz", "econ", "finance", "fed"]:
        return "Business"
    else:
        return tag.title()


@app.get("/api/summary")
def api_summary() -> dict:
    real = stats.realized()
    go = stats.go_live_readiness()
    mk = stats.markout_stats()
    fl = _cached("fleet", fleet_dash.fleet)
    open_rows = fl["markets"]

    unrealized_total = sum(r["unrealized_pnl"] for r in open_rows)
    committed_open = sum(r["committed"] for r in open_rows)
    active_positions = sum(
        1 for r in open_rows if (r["paired"] or r["naked_sh"]))
    # Fleet-wide naked exposure: the USD in the unhedged leg, valued at
    # average cost per market (fleet_dash's `naked_cost`). The $120 cap is
    # strategy/config.py's max_naked_usd -- the binding per-market dollar
    # budget the quoting layer enforces. Shown as a utilization bar.
    naked_usd = sum(r.get("naked_cost") or 0.0 for r in open_rows)
    cost = real["cost"] or 0.0
    realized_pct = (100.0 * real["realized"] / cost) if cost else None

    # Maker rebates: real money earned on matched volume, paid separately
    # from the liquidity-reward pot. The other running dashboard
    # (server/fleet_dash.py) folds this into its headline "Total
    # Liquidation P&L"; this page keeps it as its own line so a reader
    # comparing the two isn't left wondering where the gap went.
    rebate = stats.maker_rebate()
    rebate_usd = rebate.get("earned", 0.0) or 0.0
    total_liquidation_usd = real["realized"] + rebate_usd + unrealized_total

    cat_counts = go["category_counts"]
    grouped = {}
    for tag, count in cat_counts.items():
        c = _category(tag)
        grouped[c] = grouped.get(c, 0) + count

    total_cat = sum(grouped.values()) or 1
    categories = [
        {"name": k.upper(), "n": v, "pct": 100.0 * v / total_cat,
         "color": _cat_color(i)}
        for i, (k, v) in enumerate(
            sorted(grouped.items(), key=lambda kv: -kv[1]))
    ]

    try:
        pipe = _cached("pipeline", fleet_dash.pipeline)
        fleet_alive = pipe.get("fleet_alive")
        counts = (pipe.get("snapshot") or {}).get("counts") or {}
    except Exception:
        fleet_alive = None
        counts = {}

    return {
        "now": time.time(),
        # When the fleet payload was actually loaded, so "Data as of" shows
        # data freshness rather than response time (coderabbit). Falls back
        # to `now` client-side when the cache has never filled.
        "fleet_ts": _cache_ts("fleet"),
        "fleet_alive": fleet_alive,
        # The pairs-rule EV + the pending exit-card ladder (Sessions 44-51)
        # -- the fleet payload already computes it; surface it so the
        # verdict panel can render the exit-card progress tile.
        "pairs_ev": (fl.get("totals") or {}).get("pairs_ev"),
        "status": go["status"],
        "n_settled": go["n_settled"],
        "signal_min_settled": go["signal_min_settled"],
        "go_live_min_settled": go["go_live_min_settled"],
        "mean_return_pct": go["mean_return_pct"],
        "stdev_return_pct": go["stdev_return_pct"],
        "ci90_lower_pct": go["ci90_lower_pct"],
        "calendar_days": go["calendar_days"],
        "go_live_min_calendar_days": go["go_live_min_calendar_days"],
        "max_category_share": go["max_category_share"],
        "go_live_max_category_share": go["go_live_max_category_share"],
        "categories": categories,
        "markout_n_eff": go["markout_n_eff"],
        "markout_min_sample": CFG.markout_min_sample,
        "markout_mean_per_share": go["markout_mean_per_share"],
        "markout_spread_usd": mk.get("spread", 0.0),
        "markout_drift_usd": mk.get("total", 0.0),
        "realized_usd": real["realized"],
        "realized_pct": realized_pct,
        "realized_cost": cost,
        "wins": real["wins"], "losses": real["losses"],
        "closes": real["closes"], "closed_pnl": real["closed_pnl"],
        "unrealized_usd": unrealized_total,
        "committed_open_usd": committed_open,
        "active_positions": active_positions,
        "rebate_usd": rebate_usd,
        "rebate_shares": rebate.get("shares", 0.0),
        "rebate_fills": rebate.get("fills", 0),
        "rebate_cps": rebate.get("per_share_cents"),
        "rebate_err": rebate.get("err"),
        "total_liquidation_usd": total_liquidation_usd,
        "bankroll_usd": CFG.bankroll_usd,
        "max_committed_usd": CFG.max_committed_usd,
        "naked_usd": naked_usd,
        "max_naked_usd": CFG.max_naked_usd,
        # The per-sweep open-position marks (fleet-side float_marks, pruned to
        # 90 days, downsampled here to <=1 pt/min, capped). The Total equity
        # widget time-merges these with the settled closes so the curve
        # reflects the float that was actually open at each point.
        "float_history": float_history(),
        "scanned": counts.get("attempted"),
        "scored": counts.get("scored"),
        "eligible": counts.get("eligible"),
        "picked": counts.get("picked"),
    }


@app.get("/api/bankroll_matrix")
def api_bankroll_matrix() -> dict:
    from scripts.bankroll_stats_report import generate_summary_report
    reports = generate_summary_report()
    return {
        "now": time.time(),
        "tiers": reports,
    }



@app.get("/api/markets")
def api_markets() -> dict:
    rows = _cached("fleet", fleet_dash.fleet)["markets"]
    now = time.time()
    out = []
    for r in rows:
        status = _market_status(r)
        # Phase-4 table: the refusal code is the operator's stable gate code
        # (strategy/store.py reason_code) derived from the live err/why prose;
        # events are the market's real persisted lifecycle telemetry; ts is the
        # row's telemetry anchor so the page can show a truthful age badge.
        refusal = reason_code(r.get("err") or r.get("why"))
        age = r.get("age")
        out.append({
            "id": r["slug"] or r["title"],
            "market": r["slug"] or r["title"],
            "category": _category(r["slug"]),
            "committed": r["committed"],
            "resting": len(r.get("quotes") or []),
            "quotes": r.get("quotes") or [],
            "unrealized": r["unrealized_pnl"],
            "unrealized_pct": (r["unrealized_pnl"] / r["committed"] * 100.0) if r.get("committed") else None,
            "age": r.get("age"),
            # Realized P&L already booked on THIS market from voluntary
            # closes (merges/sells/naked exits) so far -- independent of
            # whether the market as a whole has resolved. A market can be
            # ERROR/BLOCKED/still-QUOTING and still show a nonzero figure
            # here, because some of its shares were already closed for
            # real money while the rest of the position is still open.
            "realized": r.get("closed_pnl", 0.0),
            "realized_pct": None, # We don't have cost basis for partial closes easily available yet
            "closes": r.get("closes", 0),
            "fills": r["fills"],
            "status": status,
            "gate": r.get("gate", "NORMAL"),
            "markout": r.get("markout"),
            # Order-depth / mid inputs, same fields the 8800 dashboard's
            # `ladder()` visualization reads (server/fleet_dash.py).
            "mid_up": r.get("mid_up"),
            "up_bid": r.get("up_bid"),
            "up_ask": r.get("up_ask"),
            "our_up": r.get("our_up"),
            "our_dn_as_up": r.get("our_dn_as_up"),
            "max_spread": r.get("max_spread"),
            # Phase-4 raw inputs the table classifies on (paired/naked/err),
            # plus the refusal code and the persisted lifecycle events.
            "paired": r.get("paired", 0.0),
            "naked_sh": r.get("naked_sh", 0.0),
            "err": r.get("err", ""),
            "why": r.get("why", ""),
            "close_why": r.get("close_why", ""),
            "merge_why": r.get("merge_why", ""),
            "code": refusal,
            "events": r.get("events") or [],
            "ts": (now - age) if age is not None else None,
        })
    out.sort(key=lambda r: -abs(r["unrealized"]))
    return {"markets": out, "now": now}


@app.get("/api/settled")
def api_settled() -> dict:
    rows = stats.settled_positions()
    out = [{
        "market": r["market_slug"] or r["condition_id"],
        "category": _category(r["market_slug"] or r["condition_id"]),
        "ts": r["ts"],
        "pnl": r["realized_pnl"],
        "pnl_pct": r["pnl_pct"],
        "method": r["method"],
        "win": (r["realized_pnl"] or 0.0) > 0,
        "avg_cost": r["avg_cost"],
        "shares": r.get("shares", 0.0),
        "cost_basis": r.get("cost_basis", 0.0),
    } for r in rows]
    return {"settled": out, "total_closes": len(out)}


@app.get("/api/funnel")
def api_funnel() -> dict:
    try:
        pipe = _cached("pipeline", fleet_dash.pipeline)
    except Exception:
        pipe = {}
    snap = pipe.get("snapshot") or {}
    counts = snap.get("counts") or {}
    rejections = snap.get("rejections") or []
    go = stats.go_live_readiness()
    return {
        "scanned": counts.get("attempted", 0),
        "scored": counts.get("scored", 0),
        "eligible": counts.get("eligible", 0),
        "picked": counts.get("picked", 0),
        "settled": go["n_settled"],
        "rejections": [{
            "cause": r.get("cause", "?"),
            "n": r.get("n", 0),
            "would_fund": r.get("would_fund", 0),
        } for r in rejections],
        "snapshot_age": pipe.get("snapshot_age"),
    }


from server.spread_dash_html import (  # noqa: E402
    LANDING_HTML, DASHBOARD_HTML, _CAPITAL_JS)


@app.get("/capital.js")
def capital_js():
    """The capital-since-inception widget, shared verbatim by the landing
    page and the dashboard -- the one piece of JS the two pages hold in
    common. Served as a static script so the pages stay self-contained
    copies (matching the repo's per-page duplication of fmtUsd/fmtPct)."""
    # Static, stable widget shared verbatim by both pages -- a short public
    # cache beats re-fetching on every visit, and the pages themselves stay
    # no-cache so their live numbers are never stale.
    return HTMLResponse(content=_CAPITAL_JS, media_type="text/javascript",
                        headers={"Cache-Control": "public, max-age=3600"})


@app.get("/", response_class=HTMLResponse)
def landing():
    return HTMLResponse(content=LANDING_HTML, headers={
        "Cache-Control": "no-cache, no-store, must-revalidate"})


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return HTMLResponse(content=DASHBOARD_HTML, headers={
        "Cache-Control": "no-cache, no-store, must-revalidate"})
