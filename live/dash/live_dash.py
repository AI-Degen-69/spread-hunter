"""Single-market live execution monitor (:8799).

Watched during ONE supervised live cycle or unattended operation.
Lifts proven UI components from the simulation dashboards:
- Level 1: Run-level Strategy KPI tile grid, tooltips, and bell curves from `server/spread_dash_html.py:1525-1567`
- Level 2: Selection funnel (RAW -> FILTERS -> FINAL -> GRADUATED) & refusal cards from `server/fleet_dash.py:1106-1180`
- Level 2: Market drill-down (quotes vs mid, 4 markout horizons, skip events, settlements) from `server/spread_dash.py:598`
- Level 3: Mechanics & system health (latency, reconcile lag, venue errors, 3-way divergences) from `server/spread_dash_html.py:1572`
- Req 4: Exposure over time (unrealized, committed, naked USD) from `server/spread_dash_html.py:1505-1509`
- Req 5: Run selector with multi-run isolation from `server/spread_dash.py:181`

Telemetry only: reads SQLite orders, fills, and reconcile_lock directly
from `live/run/live.db` via read-only URI mode:
`sqlite3.connect('file:<path>?mode=ro', uri=True)`.

Zero venue network calls. Zero credentials needed.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.middleware.gzip import GZipMiddleware

# live/, one level up from live/dash/. Everything this page reads lives under it.
LIVE_ROOT = Path(__file__).resolve().parent.parent
# Launching this file by path (`python live/dash/live_dash.py`) puts live/dash/ on
# sys.path, not live/, so `import engine.kpi` fails at request time with a 500 that
# the live suite never sees -- it runs with live/ as the working directory.
if str(LIVE_ROOT) not in sys.path:
    sys.path.insert(0, str(LIVE_ROOT))
# The ranker writes run/markets.json at the repo root; live/ reads it as data,
# never as code -- the tree boundary is about imports, not about files on disk.
REPO_ROOT = LIVE_ROOT.parent
DEFAULT_PORT = 8799
POLL_INTERVAL_MS = 2000
STALE_THRESHOLD_SEC = 30.0


def resolve_db_path(custom_path: str | Path | None = None) -> Path:
    """Find the live registry SQLite database path."""
    if custom_path:
        return Path(custom_path)
    env_path = os.environ.get("LIVE_DB_PATH")
    if env_path:
        return Path(env_path)
    return LIVE_ROOT / "run" / "live.db"


def _market_identity(condition_id: str, closes_by_cid: dict) -> dict:
    """Who is this market, in words a human recognises."""
    out = {"condition_id": condition_id, "title": None, "slug": None,
           "url": None, "days_to_resolve": None, "min_size": None,
           "volume_24h": None, "source": None}
    if not condition_id:
        return out
    try:
        feed = json.loads((REPO_ROOT / "run" / "markets.json").read_text(encoding="utf-8"))
    except Exception:
        feed = []
    for row in feed if isinstance(feed, list) else []:
        if (row.get("cid") or "").lower() == condition_id.lower():
            out.update({
                "title": row.get("title") or row.get("event_title"),
                "slug": row.get("slug"),
                "days_to_resolve": row.get("days_to_resolve"),
                "min_size": row.get("min_size"),
                "volume_24h": row.get("volume_24h"),
                "source": row.get("source"),
            })
            break
    if not out["slug"]:
        closed = closes_by_cid.get(condition_id) or {}
        out["slug"] = closed.get("market_slug")
    if not out["title"] and out["slug"]:
        out["title"] = out["slug"].replace("-", " ").title()
    elif not out["title"]:
        out["title"] = f"Market {condition_id[:10]}...{condition_id[-6:]}" if len(condition_id) > 16 else condition_id
    if out["slug"]:
        out["url"] = f"https://polymarket.com/market/{out['slug']}"
    return out


def query_db_state(db_path: Path | str) -> dict[str, Any]:
    """Read the live registry database in read-only URI mode and summarize state."""
    path = Path(db_path)
    now_ts = time.time()
    now_ms = int(now_ts * 1000)

    empty_payload = {
        "empty": True,
        "db_path": str(path),
        "server_time_ms": now_ms,
        "message": "Database not initialized or no live orders found.",
        "pairs": [],
        "orders": [],
        "fills": [],
        "capital": {
            "resting_committed": 0.0,
            "filled_committed": 0.0,
            "total_committed": 0.0,
        },
        "last_polled_ts": None,
        "seconds_since_poll": None,
        "stale": False,
        "idle": True,
        "at_stake": False,
        "reconcile_lock": {
            "held": False,
            "holder": None,
            "acquired_ts": None,
            "age_sec": None,
        },
    }

    if not path.exists():
        empty_payload["message"] = f"Database file not found at {path}"
        return empty_payload

    # Strictly read-only connection
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True, timeout=2.0)
        con.row_factory = sqlite3.Row
    except Exception as e:
        empty_payload["message"] = f"Failed to connect in read-only mode: {e}"
        return empty_payload

    try:
        cur = con.cursor()
        tables = {
            row[0]
            for row in cur.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            ).fetchall()
        }

        if "orders" not in tables:
            con.close()
            empty_payload["message"] = "Orders table does not exist in database."
            return empty_payload

        # Query orders summary
        if "order_summary" in tables:
            orders_rows = cur.execute("""
                SELECT id, order_id, condition_id, token_id, side, price, original_size,
                       status, posted_ts, last_polled_ts, pair_id, max_pair_cost_at_post,
                       size_matched
                FROM order_summary
                ORDER BY posted_ts DESC
            """).fetchall()
        else:
            orders_rows = cur.execute("""
                SELECT o.id, o.order_id, o.condition_id, o.token_id, o.side, o.price, o.original_size,
                       o.status, o.posted_ts, o.last_polled_ts, o.pair_id, o.max_pair_cost_at_post,
                       COALESCE(SUM(f.size), 0.0) AS size_matched
                FROM orders o
                LEFT JOIN fills f ON f.order_uuid = o.id
                GROUP BY o.id
                ORDER BY o.posted_ts DESC
            """).fetchall()

        # Settlement closes
        closes_by_cid: dict[str, dict[str, Any]] = {}
        if "closes" in tables:
            for c_row in cur.execute("""
                SELECT condition_id, method, market_slug, SUM(shares) AS shares,
                       SUM(COALESCE(realized_pnl, 0.0)) AS pnl,
                       MAX(ts) AS last_ts
                FROM closes
                WHERE condition_id IS NOT NULL
                GROUP BY condition_id, method
            """).fetchall():
                slot = closes_by_cid.setdefault(
                    c_row["condition_id"],
                    {"shares": 0.0, "pnl": 0.0, "last_ts": 0.0, "methods": [],
                     "market_slug": c_row["market_slug"]},
                )
                slot["shares"] += float(c_row["shares"] or 0.0)
                slot["pnl"] += float(c_row["pnl"] or 0.0)
                slot["last_ts"] = max(slot["last_ts"], float(c_row["last_ts"] or 0.0))
                slot["methods"].append(c_row["method"] or "?")

        # Query fills
        fills_rows = []
        if "fills" in tables:
            fills_rows = cur.execute("""
                SELECT f.trade_id, f.order_uuid, f.size, f.price, f.venue_ts,
                       o.side, o.pair_id, o.token_id
                FROM fills f
                LEFT JOIN orders o ON o.id = f.order_uuid
                ORDER BY f.venue_ts DESC
            """).fetchall()

        # Check reconcile lock
        rec_lock_info = {
            "held": False,
            "holder": None,
            "acquired_ts": None,
            "age_sec": None,
        }
        if "reconcile_lock" in tables:
            lock_row = cur.execute("SELECT holder, acquired_ts FROM reconcile_lock WHERE id = 1").fetchone()
            if lock_row and lock_row["acquired_ts"] is not None:
                acq = float(lock_row["acquired_ts"])
                # If acquired within 5 minutes, consider it active
                if now_ms - acq < 300000:
                    rec_lock_info["held"] = True
                    rec_lock_info["holder"] = lock_row["holder"]
                    rec_lock_info["acquired_ts"] = acq
                    rec_lock_info["age_sec"] = max(0.0, round((now_ms - acq) / 1000.0, 1))

        con.close()
    except Exception as e:
        empty_payload["message"] = f"Error reading database: {e}"
        return empty_payload

    if not orders_rows:
        empty_payload["reconcile_lock"] = rec_lock_info
        return empty_payload

    max_poll_ms = 0
    resting_committed = 0.0
    filled_committed = 0.0

    orders_list = []
    for r in orders_rows:
        o = dict(r)
        lp = o.get("last_polled_ts") or 0
        if lp > max_poll_ms:
            max_poll_ms = lp

        o["age_sec"] = max(0.0, round((now_ms - o["posted_ts"]) / 1000.0, 1))
        o["size_remaining"] = max(0.0, float(o["original_size"]) - float(o["size_matched"]))
        o["is_unattributed"] = (o.get("status") == "unattributed")

        # Committed math
        st = o["status"]
        sz_rem = o["size_remaining"]
        px = float(o["price"])
        sz_mat = float(o["size_matched"])

        if st in ("open", "pending", "partial"):
            resting_committed += sz_rem * px
        if sz_mat > 0:
            # Fallback only. The order's limit price is what we asked to pay; a
            # maker fill often lands better, so the fills table overrides this
            # below. $0.625 asked vs $0.620 paid is a 0.5c lie per share.
            filled_committed += sz_mat * px

        orders_list.append(o)

    # Poll staleness
    seconds_since_poll = round((now_ms - max_poll_ms) / 1000.0, 1) if max_poll_ms > 0 else None
    stale = (seconds_since_poll is not None and seconds_since_poll > STALE_THRESHOLD_SEC)

    # Idle check
    has_resting = any(o["status"] in ("open", "pending", "partial") for o in orders_list)
    fills_list = []
    for f in fills_rows:
        f_dict = dict(f)
        f_dict["age_sec"] = max(0.0, round((now_ms - f_dict["venue_ts"]) / 1000.0, 1))
        try:
            dt = datetime.datetime.fromtimestamp(f_dict["venue_ts"] / 1000.0)
            f_dict["venue_time_str"] = dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            f_dict["venue_time_str"] = str(f_dict["venue_ts"])
        fills_list.append(f_dict)

    # Price the filled leg at what it actually cost, not at what we bid.
    # A backfilled order can carry a NULL avg_fill_price, so rebuild it from the
    # trades themselves rather than falling back to the limit price.
    _fill_agg: dict[str, list[float]] = {}
    for f in fills_list:
        sz = float(f.get("size") or 0.0)
        agg = _fill_agg.setdefault(f["order_uuid"], [0.0, 0.0])
        agg[0] += sz
        agg[1] += sz * float(f.get("price") or 0.0)
    avg_fill_by_order = {oid: (v[1] / v[0]) for oid, v in _fill_agg.items() if v[0] > 0}

    fills_cost = sum(float(f.get("size") or 0.0) * float(f.get("price") or 0.0) for f in fills_list)
    if fills_cost > 0:
        filled_committed = fills_cost
    total_committed = resting_committed + filled_committed

    # Group into pairs
    pairs_map: dict[str, dict[str, Any]] = {}
    for o in orders_list:
        pid = o["pair_id"] or f"unpaired_{o['id']}"
        if pid not in pairs_map:
            pairs_map[pid] = {
                "pair_id": pid,
                "condition_id": o["condition_id"],
                "max_pair_cost_at_post": o["max_pair_cost_at_post"],
                "orders": [],
            }
        pairs_map[pid]["orders"].append(o)

    pairs_list = []
    for pdata in pairs_map.values():
        legs = pdata["orders"]
        _open_by_token: dict[str, float] = {}
        for leg in sorted(legs, key=lambda x: (x["side"] != "BUY", x["posted_ts"])):
            _open_by_token.setdefault(
                leg["token_id"],
                float(
                    leg.get("avg_fill_price")
                    or avg_fill_by_order.get(leg["id"])
                    or leg["price"]
                ),
            )
        combined_price = sum(_open_by_token.values())
        pdata["combined_price"] = round(combined_price, 4)
        pdata["combined_price_is_paid"] = any(
            leg.get("avg_fill_price") or avg_fill_by_order.get(leg["id"]) for leg in legs
        )

        order_ids_in_pair = {leg["id"] for leg in legs}
        pair_fills = [f for f in fills_list if f["order_uuid"] in order_ids_in_pair]
        pair_fills.sort(key=lambda x: x["venue_ts"])

        tokens: dict[str, dict[str, Any]] = {}
        for o in legs:
            tok = tokens.setdefault(o["token_id"], {
                "token_id": o["token_id"],
                "net_matched": 0.0,
                "notional": 0.0,
                "orders": [],
            })
            matched = float(o["size_matched"])
            signed = -matched if o["side"] == "SELL" else matched
            tok["net_matched"] += signed
            tok["notional"] += signed * float(o["price"])
            tok["orders"].append(o)

        for t in tokens.values():
            t["net_matched"] = round(t["net_matched"], 6)
            t["avg_price"] = (
                abs(t["notional"] / t["net_matched"]) if abs(t["net_matched"]) > 1e-9
                else (float(t["orders"][0]["price"]) if t["orders"] else 0.0)
            )

        pdata["tokens"] = list(tokens.values())

        def _naked_from(tok: dict[str, Any], shares: float,
                        _toks: dict[str, dict[str, Any]] = tokens) -> dict[str, Any]:
            """Build the naked payload for `shares` unhedged on `tok`."""
            tok_order_ids = {o["id"] for o in tok["orders"]}
            tok_fills = [f for f in pair_fills if f["order_uuid"] in tok_order_ids]
            since = (tok_fills[0]["venue_ts"] if tok_fills
                     else min(o["posted_ts"] for o in tok["orders"]))
            price = tok["avg_price"]
            nets = [t["net_matched"] for t in _toks.values()]
            return {
                "unhedged_shares": round(abs(shares), 4),
                "unhedged_side": "LONG" if shares > 0 else "SHORT",
                "unhedged_token_id": tok["token_id"],
                "unhedged_price": round(price, 4),
                "unhedged_dollars": round(abs(shares) * price, 2),
                "long_leg_matched": round(max(nets), 4),
                "short_leg_matched": round(min(nets), 4),
                "naked_since_ts": since,
                "seconds_naked": max(0.0, round((now_ms - since) / 1000.0, 1)),
            }

        naked_info = None
        if len(tokens) > 2:
            hedge_state = "REFUSED"
            pdata["refused_reason"] = (
                f"pair spans {len(tokens)} token ids; a pair is two legs. "
                f"Position cannot be classified from a reduced view."
            )
        else:
            nets = [t["net_matched"] for t in tokens.values()]
            if all(abs(n) <= 1e-6 for n in nets):
                working = {"open", "pending", "partial"}
                hedge_state = ("RESTING"
                               if any(leg["status"] in working for leg in legs)
                               else "CLOSED")
            elif len(tokens) == 2:
                a, b = list(tokens.values())
                diff = round(a["net_matched"] - b["net_matched"], 6)
                if abs(diff) <= 1e-6:
                    held = min(a["net_matched"], b["net_matched"])
                    closed = closes_by_cid.get(pdata.get("condition_id") or "")
                    if closed and closed["shares"] + 1e-6 >= held > 0:
                        hedge_state = "SETTLED"
                        pdata["settlement"] = {
                            "shares": closed["shares"],
                            "pnl": closed["pnl"],
                            "methods": sorted(set(closed["methods"])),
                            "ts": closed["last_ts"],
                        }
                    else:
                        hedge_state = "BALANCED"
                else:
                    hedge_state = "NAKED"
                    heavy = a if a["net_matched"] > b["net_matched"] else b
                    naked_info = _naked_from(heavy, abs(diff))
            else:
                only = next(iter(tokens.values()))
                hedge_state = "NAKED"
                naked_info = _naked_from(only, only["net_matched"])

        pdata["hedge_state"] = hedge_state
        pdata["naked_info"] = naked_info
        pdata["market"] = _market_identity(pdata.get("condition_id"), closes_by_cid)
        pairs_list.append(pdata)

    has_naked = any(p["hedge_state"] == "NAKED" for p in pairs_list)
    has_balanced = any(p["hedge_state"] == "BALANCED" for p in pairs_list)
    idle = not has_resting and not has_naked and not has_balanced
    at_stake = has_resting or has_naked

    return {
        "empty": False,
        "db_path": str(path),
        "server_time_ms": now_ms,
        "pairs": pairs_list,
        "orders": orders_list,
        "fills": fills_list,
        "capital": {
            "resting_committed": round(resting_committed, 2),
            "filled_committed": round(filled_committed, 2),
            "total_committed": round(total_committed, 2),
        },
        "last_polled_ts": max_poll_ms if max_poll_ms > 0 else None,
        "seconds_since_poll": seconds_since_poll,
        "stale": stale,
        "idle": idle,
        "at_stake": at_stake,
        "reconcile_lock": rec_lock_info,
    }


app = FastAPI(title="Spread Hunter Live Monitor")
app.add_middleware(GZipMiddleware, minimum_size=1000)

_ACTIVE_DB_OVERRIDE: Path | None = None


def set_db_override(path: Path | str | None) -> None:
    global _ACTIVE_DB_OVERRIDE
    _ACTIVE_DB_OVERRIDE = Path(path) if path else None


@app.get("/api/state")
def get_state():
    """Return JSON state snapshot for the live execution dashboard."""
    return JSONResponse(query_db_state(resolve_db_path(_ACTIVE_DB_OVERRIDE)))


@app.get("/api/kpi")
def get_kpi(run_id: str | None = None):
    """Return live KPI report mirroring strategy/kpi.py with Level 1/2/3 diagnostics."""
    from engine.kpi import report as generate_kpi_report
    db_path = resolve_db_path(_ACTIVE_DB_OVERRIDE)
    try:
        data = generate_kpi_report(db_path=db_path, run_id=run_id)
        return JSONResponse(data)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Spread Hunter — Live Cycle Monitor</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Big+Shoulders+Display:wght@700;800&family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg-base: #080c14;
      --bg-surface: #0f172a;
      --bg-surface-raised: #1e293b;
      --bg-card: rgba(15, 23, 42, 0.65);
      --border-subtle: rgba(255, 255, 255, 0.08);
      --border-strong: rgba(255, 255, 255, 0.16);
      --text-primary: #f8fafc;
      --text-secondary: #94a3b8;
      --text-muted: #64748b;
      --signal: #10b981;
      --loss: #ef4444;
      --open: #38bdf8;
      --warn: #f59e0b;
      --gold: #fbbf24;
      
      --red-alert: #ef4444;
      --red-bg: rgba(127, 29, 29, 0.45);
      --red-border: #dc2626;
      --red-glow: 0 0 40px rgba(239, 68, 68, 0.35);

      --green-ok: #10b981;
      --green-bg: rgba(6, 78, 59, 0.35);
      --green-border: #059669;
      --green-glow: 0 0 30px rgba(16, 185, 129, 0.25);

      --blue-rest: #38bdf8;
      --blue-bg: rgba(12, 74, 110, 0.35);
      --blue-border: #0284c7;

      --amber-warn: #f59e0b;
      --amber-bg: rgba(120, 53, 15, 0.35);
      --amber-border: #d97706;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      background: linear-gradient(180deg, #080c14 0%, #0b111e 100%) fixed;
      color: var(--text-primary);
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      min-height: 100vh;
      padding: 20px;
      line-height: 1.5;
    }

    .font-display { font-family: 'Big Shoulders Display', Impact, sans-serif; letter-spacing: 0.02em; font-weight: 800; }
    .mono { font-family: 'JetBrains Mono', ui-monospace, monospace; font-variant-numeric: tabular-nums; }

    .container {
      max-width: 1380px;
      margin: 0 auto;
      display: flex;
      flex-direction: column;
      gap: 18px;
    }

    /* Top Bar */
    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 14px 20px;
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
      border-radius: 12px;
      backdrop-filter: blur(8px);
    }

    .brand { display: flex; align-items: center; gap: 12px; }
    .brand-icon { font-size: 24px; }
    .brand-title { font-size: 18px; font-weight: 800; letter-spacing: -0.02em; }
    .brand-sub { font-size: 11px; color: var(--text-secondary); font-family: 'JetBrains Mono', monospace; }

    .top-meta {
      display: flex;
      align-items: center;
      gap: 12px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
    }

    .run-select-wrap {
      display: flex;
      align-items: center;
      gap: 6px;
      background: var(--bg-surface-raised);
      border: 1px solid var(--border-subtle);
      padding: 4px 8px;
      border-radius: 6px;
    }
    .run-select-wrap select {
      background: transparent;
      color: var(--text-primary);
      border: none;
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
      font-weight: 600;
      outline: none;
      cursor: pointer;
    }

    .pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 4px 10px;
      border-radius: 9999px;
      font-weight: 600;
      font-size: 11px;
    }
    .pill-fresh { background: var(--green-bg); color: #34d399; border: 1px solid var(--green-border); }
    .pill-stale { background: var(--red-bg); color: #fca5a5; border: 1px solid var(--red-border); animation: pulse 1.5s infinite; }
    .pill-neutral { background: var(--bg-surface-raised); color: var(--text-secondary); border: 1px solid var(--border-subtle); }

    /* Market Strip */
    .market-strip {
      display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px 18px;
      padding: 12px 18px; border-radius: 10px;
      border: 1px solid var(--border-subtle);
      background: var(--bg-surface);
    }
    .market-strip .mk-title { font-size: 16px; font-weight: 700; }
    .market-strip a.mk-link { color: #38bdf8; text-decoration: none; }
    .market-strip a.mk-link:hover { text-decoration: underline; }
    .market-strip .mk-facts {
      display: flex; flex-wrap: wrap; gap: 6px 16px;
      font-size: 12px; color: var(--text-muted);
      font-family: 'JetBrains Mono', monospace;
    }
    .market-strip .mk-facts b { color: var(--text-secondary); font-weight: 600; }

    /* Stale Warning Banner */
    .stale-banner {
      display: none;
      background: linear-gradient(90deg, #991b1b, #7f1d1d);
      border: 2px solid var(--red-alert);
      color: #fee2e2;
      padding: 12px 18px;
      border-radius: 10px;
      font-weight: 700;
      font-size: 14px;
      box-shadow: var(--red-glow);
      animation: pulse 1.5s infinite;
      align-items: center;
      gap: 12px;
    }

    @keyframes pulse { 0%, 100% { opacity: 1; transform: scale(1); } 50% { opacity: 0.9; transform: scale(0.995); } }
    @keyframes flashBg { 0%, 100% { background-color: rgba(127, 29, 29, 0.7); } 50% { background-color: rgba(185, 28, 28, 0.95); } }

    /* HERO SECTION: HEDGE STATE */
    .hero-card {
      border-radius: 14px;
      padding: 24px 28px;
      background: var(--bg-surface);
      border: 2px solid var(--border-strong);
      transition: all 0.3s ease;
    }
    .hero-card.state-naked {
      background: linear-gradient(145deg, rgba(80, 10, 10, 0.95), rgba(30, 5, 5, 0.95));
      border: 3px solid var(--red-alert);
      box-shadow: var(--red-glow);
      animation: flashBg 2s infinite ease-in-out;
    }
    .hero-card.state-balanced {
      background: linear-gradient(145deg, rgba(6, 60, 45, 0.85), rgba(6, 30, 25, 0.85));
      border: 2px solid var(--green-ok);
      box-shadow: var(--green-glow);
    }
    .hero-card.state-resting {
      background: linear-gradient(145deg, rgba(15, 30, 60, 0.85), rgba(10, 20, 40, 0.85));
      border: 2px solid var(--blue-border);
    }
    .hero-card.state-closed { background: var(--bg-surface); border: 1px solid var(--border-subtle); }
    .hero-card.state-empty { background: var(--bg-surface); border: 1px dashed var(--border-strong); text-align: center; padding: 36px 20px; }

    .hero-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px; }
    .hero-badge {
      display: inline-flex; align-items: center; gap: 8px;
      font-size: 13px; font-weight: 800; letter-spacing: 0.08em; text-transform: uppercase;
      padding: 5px 12px; border-radius: 6px; font-family: 'JetBrains Mono', monospace;
    }
    .hero-badge.naked { background: #ef4444; color: #fff; }
    .hero-badge.balanced { background: #10b981; color: #fff; }
    .hero-badge.resting { background: #0284c7; color: #fff; }
    .hero-badge.closed { background: #475569; color: #f1f5f9; }

    .hero-headline { font-size: 28px; font-weight: 900; letter-spacing: -0.02em; margin-top: 6px; }
    .hero-desc { font-size: 14px; color: var(--text-secondary); margin-top: 4px; }

    .naked-metrics {
      display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 18px;
      padding: 16px; background: rgba(0, 0, 0, 0.4); border-radius: 10px; border: 1px solid rgba(239, 68, 68, 0.4);
    }
    .metric-block { display: flex; flex-direction: column; gap: 4px; }
    .metric-label { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: #fca5a5; font-family: 'JetBrains Mono', monospace; }
    .metric-val-huge { font-size: 32px; font-weight: 900; color: #fff; font-family: 'JetBrains Mono', monospace; }
    .timer-val { color: #fef08a; }

    /* SECTION CONTAINERS & GRIDS */
    .section-title {
      font-size: 13px; font-weight: 800; letter-spacing: 0.12em; text-transform: uppercase;
      color: var(--gold); display: flex; align-items: center; gap: 8px; margin-bottom: 10px;
    }
    .section-title span.badge {
      font-size: 10px; padding: 2px 6px; border-radius: 4px; background: rgba(251, 191, 36, 0.15);
      border: 1px solid rgba(251, 191, 36, 0.3); color: var(--gold);
    }

    .panel {
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
      border-radius: 12px;
      padding: 18px;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }

    /* KPI Tiles Grid (Lifted from server/spread_dash_html.py:1525) */
    .kpi-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 14px;
    }
    .kpi-tile {
      background: rgba(15, 23, 42, 0.6);
      border: 1px solid var(--border-subtle);
      border-radius: 10px;
      padding: 14px 16px;
      display: flex;
      flex-direction: column;
      gap: 8px;
      transition: border-color 0.15s ease, background-color 0.15s ease;
    }
    .kpi-tile:hover {
      border-color: rgba(255, 255, 255, 0.2);
      background: rgba(20, 30, 50, 0.7);
    }
    .kpi-header {
      font-family: 'JetBrains Mono', monospace;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--text-secondary);
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .kpi-val {
      font-family: 'JetBrains Mono', monospace;
      font-size: 22px;
      font-weight: 800;
      line-height: 1.1;
    }
    .kpi-sub {
      font-family: 'JetBrains Mono', monospace;
      font-size: 11px;
      color: var(--text-muted);
      line-height: 1.4;
    }

    /* TOOLTIP PATTERN (Lifted from server/spread_dash_html.py:99-114) */
    .tip-wrap { position: relative; display: inline-flex; vertical-align: middle; margin-left: 4px; }
    .tip-ico {
      width: 14px; height: 14px; border: 1px solid rgba(148, 163, 184, 0.5); color: #94a3b8;
      background: transparent; border-radius: 9999px; font-size: 9px; font-weight: 700;
      display: inline-flex; align-items: center; justify-content: center; cursor: help; padding: 0;
    }
    .tip-ico:hover, .tip-wrap:focus-within .tip-ico { border-color: var(--gold); color: var(--gold); }
    .tip-pop {
      position: absolute; bottom: calc(100% + 8px); left: 50%; transform: translateX(-50%) translateY(4px);
      width: 290px; max-width: calc(100vw - 32px); padding: 10px 12px; background: #090d16;
      border: 1px solid rgba(255, 255, 255, 0.2); box-shadow: 0 16px 36px rgba(0, 0, 0, 0.95);
      opacity: 0; visibility: hidden; pointer-events: none; transition: opacity 0.15s ease, transform 0.15s ease, visibility 0.15s;
      z-index: 9999; text-align: left; font-family: 'JetBrains Mono', monospace;
    }
    .tip-pop .tip-k { display: block; font-size: 10px; letter-spacing: 0.14em; text-transform: uppercase; color: var(--gold); font-weight: 700; margin-bottom: 4px; }
    .tip-pop .tip-t { display: block; font-size: 11px; line-height: 1.5; color: #94a3b8; font-weight: 400; }
    .tip-pop .tip-g { display: block; margin-top: 6px; padding-top: 6px; border-top: 1px solid rgba(255, 255, 255, 0.1); font-size: 10px; line-height: 1.5; color: #d1d5db; }
    .tip-wrap:hover .tip-pop, .tip-wrap:focus-within .tip-pop { opacity: 1; visibility: visible; transform: translateX(-50%) translateY(0); }

    /* FUNNEL VIEW (Lifted from server/fleet_dash.py:1106-1180) */
    .funnel-board {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 12px;
    }
    @media (max-width: 900px) { .funnel-board { grid-template-columns: 1fr; } }
    .funnel-lane {
      background: rgba(15, 23, 42, 0.5);
      border: 1px solid var(--border-subtle);
      border-radius: 10px;
      padding: 12px;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }
    .funnel-hdr {
      display: flex;
      justify-content: space-between;
      align-items: center;
      border-bottom: 1px solid var(--border-subtle);
      padding-bottom: 8px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
    }
    .funnel-badge {
      padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 800;
    }
    .gate-card {
      background: rgba(8, 12, 20, 0.6);
      border: 1px solid var(--border-subtle);
      border-radius: 6px;
      padding: 10px;
      display: flex;
      flex-direction: column;
      gap: 6px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 11px;
    }
    .gate-card .gate-code { font-weight: 700; color: #f59e0b; }
    .gate-card .gate-ex { color: var(--text-muted); font-size: 10px; line-height: 1.4; }

    /* TABLES */
    .table-container { overflow-x: auto; }
    table { width: 100%; border-collapse: collapse; font-size: 12px; font-family: 'JetBrains Mono', monospace; }
    th { text-align: left; padding: 10px 12px; color: var(--text-muted); font-weight: 600; font-size: 11px; text-transform: uppercase; border-bottom: 1px solid var(--border-subtle); }
    td { padding: 10px 12px; border-bottom: 1px solid rgba(255, 255, 255, 0.04); color: var(--text-primary); }
    tr:hover td { background: rgba(255, 255, 255, 0.02); }
    tr.clickable-row { cursor: pointer; }
    tr.clickable-row:hover td { background: rgba(56, 189, 248, 0.08); }

    .status-tag { display: inline-block; padding: 2px 7px; border-radius: 4px; font-weight: 700; font-size: 10px; text-transform: uppercase; }
    .status-tag.open { background: var(--blue-bg); color: var(--blue-rest); border: 1px solid var(--blue-border); }
    .status-tag.filled { background: var(--green-bg); color: #34d399; border: 1px solid var(--green-border); }
    .status-tag.partial { background: var(--amber-bg); color: #fcd34d; border: 1px solid var(--amber-border); }
    .status-tag.cancelled { background: var(--bg-surface-raised); color: var(--text-muted); }
    .status-tag.unattributed { background: #ea580c; color: #ffffff; border: 1px solid #f97316; animation: pulse 1.5s infinite; }

    /* MECHANICS PANEL (Level 3 - Visually Distinct) */
    .mechanics-box {
      border: 1px solid rgba(245, 158, 11, 0.3);
      background: rgba(20, 15, 5, 0.35);
      border-radius: 12px;
      padding: 18px;
    }
    .mech-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 14px;
    }
    .mech-card {
      background: rgba(15, 23, 42, 0.7);
      border: 1px solid rgba(255, 255, 255, 0.07);
      border-radius: 8px;
      padding: 12px 14px;
      display: flex;
      flex-direction: column;
      gap: 6px;
      font-family: 'JetBrains Mono', monospace;
    }

    /* MODAL */
    .modal-backdrop {
      position: fixed; inset: 0; background: rgba(0, 0, 0, 0.85); backdrop-filter: blur(6px);
      z-index: 10000; display: flex; align-items: center; justify-content: center; padding: 20px;
    }
    .modal-box {
      background: #090d16; border: 1px solid rgba(255, 255, 255, 0.2); border-radius: 12px;
      width: 100%; max-width: 960px; max-height: 90vh; overflow-y: auto; padding: 24px;
      box-shadow: 0 25px 60px rgba(0, 0, 0, 0.95); display: flex; flex-direction: column; gap: 16px;
    }
    .modal-hdr { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border-subtle); padding-bottom: 12px; }
    .modal-close { background: transparent; border: 1px solid var(--border-subtle); color: var(--text-secondary); width: 28px; height: 28px; border-radius: 6px; font-weight: 700; cursor: pointer; }
    .modal-close:hover { border-color: #ef4444; color: #ef4444; }

    .grid-2col { display: grid; grid-template-columns: 2fr 1fr; gap: 18px; }
    @media (max-width: 900px) { .grid-2col { grid-template-columns: 1fr; } }
    .grid-bottom { display: grid; grid-template-columns: 3fr 2fr; gap: 18px; }
    @media (max-width: 900px) { .grid-bottom { grid-template-columns: 1fr; } }

    .stat-list { display: flex; flex-direction: column; gap: 10px; }
    .stat-row { display: flex; justify-content: space-between; align-items: center; padding: 8px 12px; background: var(--bg-surface-raised); border-radius: 6px; border: 1px solid var(--border-subtle); font-family: 'JetBrains Mono', monospace; font-size: 12px; }
    .lock-box { padding: 12px; border-radius: 6px; background: var(--bg-surface-raised); border: 1px solid var(--border-subtle); font-size: 12px; font-family: 'JetBrains Mono', monospace; }
    .lock-idle { border-left: 4px solid var(--green-ok); }
    .lock-active { border-left: 4px solid var(--amber-warn); }
    .empty-state-text { color: var(--text-muted); font-style: italic; padding: 18px 0; text-align: center; }
  </style>
</head>
<body>
  <div class="container">
    <!-- Header -->
    <header>
      <div class="brand">
        <span class="brand-icon">🎯</span>
        <div>
          <div class="brand-title">SPREAD HUNTER // LIVE MONITOR</div>
          <div class="brand-sub" id="db-path-display">DB: run/live.db</div>
        </div>
      </div>
      <div class="top-meta">
        <div class="run-select-wrap">
          <label for="run-selector" style="color:var(--text-secondary);font-size:11px;font-weight:700;">RUN:</label>
          <select id="run-selector">
            <option value="">Latest Run</option>
          </select>
        </div>
        <span id="poll-pill" class="pill pill-neutral">CONNECTING...</span>
        <span id="port-pill" class="pill pill-neutral"></span>
        <span id="clock-display">--:--:--</span>
      </div>
    </header>

    <!-- Market Strip -->
    <div id="market-strip"></div>

    <!-- Stale Warning Banner -->
    <div id="stale-alert-banner" class="stale-banner">
      <span>⚠️</span>
      <span id="stale-alert-text">STALE TELEMETRY: Poll loop has not updated the database in >30 seconds.</span>
    </div>

    <!-- HERO SECTION: HEDGE STATE -->
    <div id="hero-container">
      <div class="hero-card state-empty">
        <div class="hero-headline">Loading live telemetry...</div>
        <div class="hero-desc">Connecting to local database reader.</div>
      </div>
    </div>

    <!-- LEVEL 1: RUN-LEVEL STRATEGY METRICS (Lifted from server/spread_dash_html.py:1525-1567) -->
    <section class="panel">
      <div class="section-title">
        <span>Level 1 &mdash; Strategy Performance</span>
        <span class="badge">Run Evaluation</span>
      </div>
      <div class="kpi-grid" id="sec-run-kpis">
        <!-- Rendered dynamically -->
      </div>
    </section>

    <!-- REQ 4: EXPOSURE OVER TIME (Lifted from server/spread_dash_html.py:1505-1509) -->
    <section class="panel">
      <div class="section-title">
        <span>Portfolio Exposure Over Time</span>
        <span class="badge">Float Marks</span>
      </div>
      <div id="sec-exposure">
        <!-- Exposure Chart SVG -->
      </div>
    </section>

    <!-- LEVEL 2: MARKET-LEVEL DRILL-DOWN & FUNNEL (Lifted from server/fleet_dash.py:1106-1180) -->
    <section class="panel">
      <div class="section-title">
        <span>Level 2 &mdash; Market Selection & Refusals Funnel</span>
        <span class="badge">Market Choice</span>
      </div>
      <div class="funnel-board" id="sec-funnel">
        <!-- 4 Lanes: RAW -> FILTERS -> FINAL -> GRADUATED -->
      </div>

      <div style="margin-top:14px;">
        <div style="font-family:'JetBrains Mono',monospace;font-size:12px;font-weight:700;color:var(--text-secondary);margin-bottom:8px;">
          MARKETS IN RUN &mdash; CLICK TO DRILL DOWN
        </div>
        <div class="table-container">
          <table>
            <thead>
              <tr>
                <th>Market</th>
                <th>Volume (24h)</th>
                <th>Horizon</th>
                <th>Fills / Quotes</th>
                <th>Up / Dn Shares</th>
                <th>Pair Cost</th>
                <th>Balance</th>
                <th>Realized PnL</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody id="markets-tbody">
              <tr><td colspan="9" class="empty-state-text">No market telemetry available</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </section>

    <!-- LEVEL 3: MECHANICS & SYSTEM HEALTH (Lifted from server/spread_dash_html.py:1572-1621) -->
    <section class="mechanics-box">
      <div class="section-title" style="color:#f59e0b;">
        <span>Level 3 &mdash; Mechanics & System Health</span>
        <span class="badge" style="background:rgba(245,158,11,0.15);border-color:rgba(245,158,11,0.3);color:#f59e0b;">Machinery Diagnostics</span>
      </div>
      <div class="mech-grid" id="sec-mechanics">
        <!-- Latency, Reconcile Lag, Venue Rejects, Divergence events -->
      </div>
      <div id="venue-rejects-detail" style="margin-top:14px;display:none;">
        <div style="font-family:'JetBrains Mono',monospace;font-size:11px;font-weight:700;color:#fca5a5;margin-bottom:6px;">RECENT VENUE REJECTS / ERRORS</div>
        <div class="table-container">
          <table style="font-size:11px;">
            <thead>
              <tr><th>Time</th><th>Code</th><th>Side</th><th>Price</th><th>Size</th><th>Raw Error Message</th></tr>
            </thead>
            <tbody id="venue-rejects-tbody"></tbody>
          </table>
        </div>
      </div>
    </section>

    <!-- ACTIVE ORDERS + CAPITAL GRID -->
    <div class="grid-2col">
      <!-- Order Matrix -->
      <div class="panel">
        <div class="section-title" style="color:var(--text-secondary);">
          <span>Active Pair Orders</span>
          <span id="order-count-badge" class="pill pill-neutral">0 orders</span>
        </div>
        <div class="table-container">
          <table>
            <thead>
              <tr>
                <th>Side / Leg</th>
                <th>Price</th>
                <th>Size</th>
                <th>Matched</th>
                <th>Fill %</th>
                <th>Status</th>
                <th>Age</th>
              </tr>
            </thead>
            <tbody id="orders-tbody">
              <tr>
                <td colspan="7" class="empty-state-text">No orders loaded</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Capital & Pair Pricing -->
      <div class="panel">
        <div class="section-title" style="color:var(--text-secondary);">
          <span>Capital & Pair Cost</span>
        </div>
        <div class="stat-list">
          <div class="stat-row">
            <span style="color:var(--text-secondary);">Total Capital Committed</span>
            <span id="stat-total-committed" style="font-weight:700;">$0.00</span>
          </div>
          <div class="stat-row">
            <span style="color:var(--text-secondary);">Resting Notional</span>
            <span id="stat-resting-committed" style="font-weight:700;">$0.00</span>
          </div>
          <div class="stat-row">
            <span style="color:var(--text-secondary);">Filled Notional</span>
            <span id="stat-filled-committed" style="font-weight:700;">$0.00</span>
          </div>
          <div class="stat-row">
            <span style="color:var(--text-secondary);">Combined Pair Price</span>
            <span id="stat-pair-price" style="font-weight:700;">--</span>
          </div>
          <div class="stat-row">
            <span style="color:var(--text-secondary);">Max Pair Cost Limit</span>
            <span id="stat-max-pair-cost" style="font-weight:700;">--</span>
          </div>
        </div>
      </div>
    </div>

    <!-- BOTTOM GRID: FILLS TIMELINE + FRESHNESS & LOCK -->
    <div class="grid-bottom">
      <!-- Fills Timeline -->
      <div class="panel">
        <div class="section-title" style="color:var(--text-secondary);">
          <span>Fills Timeline (Newest First)</span>
          <span id="fill-count-badge" class="pill pill-neutral">0 fills</span>
        </div>
        <div class="table-container">
          <table>
            <thead>
              <tr>
                <th>Time (Local)</th>
                <th>Trade ID</th>
                <th>Side</th>
                <th>Price</th>
                <th>Size</th>
                <th>Notional</th>
              </tr>
            </thead>
            <tbody id="fills-tbody">
              <tr>
                <td colspan="6" class="empty-state-text">No fills recorded yet</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Telemetry & Reconcile Lock -->
      <div class="panel">
        <div class="section-title" style="color:var(--text-secondary);">
          <span>Telemetry & Lock State</span>
        </div>
        <div class="stat-list">
          <div class="stat-row">
            <span style="color:var(--text-secondary);">Last Venue Poll</span>
            <span id="telemetry-last-poll" style="font-weight:700;">--</span>
          </div>
          <div class="stat-row">
            <span style="color:var(--text-secondary);">Poll Status</span>
            <span id="telemetry-poll-status" style="font-weight:700;">Waiting</span>
          </div>
          <div id="lock-container" class="lock-box lock-idle">
            <strong>Reconcile Lock:</strong> <span id="lock-status-text">Idle (no pass in flight)</span>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- MARKET DRILL-DOWN MODAL -->
  <div id="modal-drilldown" class="modal-backdrop" style="display:none;">
    <div class="modal-box">
      <div class="modal-hdr">
        <div>
          <div id="modal-mkt-title" style="font-size:18px;font-weight:800;color:#f8fafc;">Market Details</div>
          <div id="modal-mkt-sub" style="font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--text-secondary);"></div>
        </div>
        <button type="button" class="modal-close" onclick="closeDrilldownModal()">✕</button>
      </div>
      <div id="modal-drilldown-body" style="display:flex;flex-direction:column;gap:16px;">
        <!-- Filled dynamically -->
      </div>
    </div>
  </div>

  <!-- DISTRIBUTION ZOOM MODAL (Lifted from server/spread_dash_html.py:1255) -->
  <div id="modal-dist" class="modal-backdrop" style="display:none;">
    <div class="modal-box" style="max-width:700px;">
      <div class="modal-hdr">
        <div id="modal-dist-title" style="font-size:16px;font-weight:800;">Adverse Selection Distribution</div>
        <button type="button" class="modal-close" onclick="closeDistModal()">✕</button>
      </div>
      <div id="modal-dist-body" style="padding:10px 0;"></div>
    </div>
  </div>

  <script>
    let lastState = null;
    let lastKpi = null;
    let localNakedSinceMs = null;
    let localLastPollMs = null;
    let selectedRunId = "";

    function esc(v) {
      return String(v == null ? '' : v)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
    const KNOWN_STATUSES = ['pending', 'open', 'partial', 'filled', 'cancelled', 'unattributed'];

    function formatTime(ms) {
      if (!ms) return '--';
      const d = new Date(ms);
      return d.toLocaleTimeString();
    }

    function formatDuration(sec) {
      if (sec === null || sec === undefined || isNaN(sec)) return '--';
      const s = Math.floor(sec);
      const m = Math.floor(s / 60);
      const remS = s % 60;
      if (m > 0) return `${m}m ${remS}s`;
      return `${s}s`;
    }

    function tip(label, body, formula) {
      return `
        <span class="tip-wrap" tabindex="0">
          <span class="tip-ico" aria-label="Info">i</span>
          <span class="tip-pop">
            <span class="tip-k">${esc(label)}</span>
            <span class="tip-t">${body}</span>
            ${formula ? `<span class="tip-g"><strong>Formula:</strong> ${formula}</span>` : ''}
          </span>
        </span>
      `;
    }

    // SVG Bell Curve (Lifted from server/spread_dash_html.py:1217)
    function bellCurveSvg(opts) {
      const {min, max, mean, stdev, zero, color, w, h} = opts;
      const W = w || 180, H = h || 50, pad = 8;
      const x = v => pad + ((v - min) / Math.max(0.001, max - min)) * (W - pad * 2);
      const sd = stdev && stdev > 0 ? stdev : Math.max(0.1, (max - min) / 6);
      const bell = [];
      for (let i = 0; i < 40; i++) {
        const v = min + (i / 39) * (max - min);
        const z = (v - mean) / sd;
        bell.push([v, Math.exp(-0.5 * z * z)]);
      }
      const maxY = Math.max(...bell.map(p => p[1])) || 1;
      const yS = y => (H - 10) - (y / maxY) * (H - 20);
      const path = bell.map((p, i) => `${i === 0 ? "M" : "L"} ${x(p[0]).toFixed(1)} ${yS(p[1]).toFixed(1)}`).join(" ");
      const area = `${path} L ${x(bell[bell.length - 1][0]).toFixed(1)} ${H - 10} L ${x(bell[0][0]).toFixed(1)} ${H - 10} Z`;
      let zeroLine = "";
      if (zero !== undefined && zero >= min && zero <= max) {
        zeroLine = `<line x1="${x(zero).toFixed(1)}" x2="${x(zero).toFixed(1)}" y1="4" y2="${H - 6}" stroke="#EF4444" stroke-width="1.2" stroke-dasharray="3 2"/>`;
      }
      return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:${H}px;display:block;">
        <path d="${area}" fill="${color}" fill-opacity="0.15"/>
        <path d="${path}" fill="none" stroke="${color}" stroke-width="1.5"/>
        ${zeroLine}
        <circle cx="${x(mean).toFixed(1)}" cy="${yS(Math.exp(0)).toFixed(1)}" r="3" fill="#111827" stroke="#F9FAFB" stroke-width="1.5"/>
      </svg>`;
    }

    function renderMarketStrip(state) {
      const el = document.getElementById('market-strip');
      const pair = (state.pairs || [])[0];
      const m = pair && pair.market;
      if (!m || !m.condition_id) { el.innerHTML = ''; return; }
      const facts = [];
      if (m.volume_24h) facts.push(`<span><b>24h volume</b> $${Number(m.volume_24h).toLocaleString(undefined, {maximumFractionDigits: 0})}</span>`);
      if (m.days_to_resolve != null) facts.push(`<span><b>resolves in</b> ${Number(m.days_to_resolve).toFixed(1)}d</span>`);
      if (m.min_size != null) facts.push(`<span><b>venue min</b> ${Number(m.min_size).toFixed(0)}sh</span>`);
      if (m.source) facts.push(`<span><b>ranked as</b> ${esc(m.source)}</span>`);
      facts.push(`<span><b>pair</b> ${esc(pair.pair_id || '')}</span>`);
      facts.push(`<span><b>condition</b> ${esc(m.condition_id.slice(0, 10))}...${esc(m.condition_id.slice(-6))}</span>`);
      const title = m.title || m.slug || 'Unnamed market';
      const titleHtml = m.url
        ? `<a class="mk-link" href="${esc(m.url)}" target="_blank" rel="noopener">${esc(title)} ↗</a>`
        : esc(title);
      el.innerHTML = `
        <div class="market-strip">
          <div class="mk-title">${titleHtml}</div>
          <div class="mk-facts">${facts.join('')}</div>
        </div>
      `;
    }

    function renderHero(state) {
      const hero = document.getElementById('hero-container');
      if (state.empty || !state.pairs || state.pairs.length === 0) {
        hero.innerHTML = `
          <div class="hero-card state-empty">
            <div class="hero-badge resting">📡 AWAITING LIVE ORDERS</div>
            <div class="hero-headline">No active orders in registry</div>
            <div class="hero-desc">${esc(state.message || "Orders posted via live_exec quote will appear here automatically.")}</div>
          </div>
        `;
        localNakedSinceMs = null;
        return;
      }

      const pair = state.pairs[0];
      const hedgeState = pair.hedge_state;

      if (hedgeState === 'NAKED' && pair.naked_info) {
        const info = pair.naked_info;
        localNakedSinceMs = info.naked_since_ts;
        const unhedgedUsd = info.unhedged_dollars.toFixed(2);
        const unhedgedShares = info.unhedged_shares.toFixed(2);
        const side = info.unhedged_side || 'LONG';
        const secNaked = Math.floor(info.seconds_naked || 0);

        hero.innerHTML = `
          <div class="hero-card state-naked">
            <div class="hero-header">
              <div>
                <div class="hero-badge naked">🚨 CRITICAL: NAKED LEG DETECTED</div>
                <div class="hero-headline">UNHEDGED POSITION OPEN</div>
                <div class="hero-desc">One leg has filled while the opposite leg remains unmatched. Immediate attention required!</div>
              </div>
            </div>
            <div class="naked-metrics">
              <div class="metric-block">
                <div class="metric-label">Unhedged Dollar Risk</div>
                <div class="metric-val-huge">$${unhedgedUsd} <span style="font-size:18px;font-weight:600;">(${unhedgedShares} sh @ ${side})</span></div>
              </div>
              <div class="metric-block">
                <div class="metric-label">Time Naked (Counting Up)</div>
                <div class="metric-val-huge timer-val" id="live-naked-timer">${formatDuration(secNaked)}</div>
              </div>
            </div>
          </div>
        `;
      } else if (hedgeState === 'SETTLED') {
        localNakedSinceMs = null;
        const st = pair.settlement || {};
        const pnl = Number(st.pnl || 0);
        const sign = pnl >= 0 ? '+' : '-';
        hero.innerHTML = `
          <div class="hero-card state-balanced">
            <div class="hero-header">
              <div>
                <div class="hero-badge balanced">✅ POSITION CLOSED</div>
                <div class="hero-headline">${sign}$${Math.abs(pnl).toFixed(2)} REALISED</div>
                <div class="hero-desc">${esc((st.methods || ['closed']).join(', '))} &mdash; ${Number(st.shares || 0).toFixed(2)} pairs settled. The shares are gone and the money is back; nothing is at risk on this market.</div>
              </div>
            </div>
          </div>
        `;
      } else if (hedgeState === 'BALANCED') {
        localNakedSinceMs = null;
        const totalFills = pair.orders.reduce((acc, o) => acc + (o.size_matched || 0), 0);
        hero.innerHTML = `
          <div class="hero-card state-balanced">
            <div class="hero-header">
              <div>
                <div class="hero-badge balanced">🛡️ INVENTORY BALANCED</div>
                <div class="hero-headline">PERFECTLY HEDGED PAIR</div>
                <div class="hero-desc">Both legs filled in equal size (${(totalFills / 2).toFixed(2)} shares matched). Inventory neutral &mdash; holding to resolution.</div>
              </div>
            </div>
          </div>
        `;
      } else if (hedgeState === 'RESTING') {
        localNakedSinceMs = null;
        hero.innerHTML = `
          <div class="hero-card state-resting">
            <div class="hero-header">
              <div>
                <div class="hero-badge resting">⏳ RESTING BIDS ON CLOB</div>
                <div class="hero-headline">WAITING FOR TAKER FILLS</div>
                <div class="hero-desc">Orders placed and resting on both sides. No fills executed yet.</div>
              </div>
            </div>
          </div>
        `;
      } else if (hedgeState === 'REFUSED') {
        localNakedSinceMs = null;
        hero.innerHTML = `
          <div class="hero-card state-naked">
            <div class="hero-header">
              <div>
                <div class="hero-badge naked">❓ CANNOT CLASSIFY POSITION</div>
                <div class="hero-headline">TREAT AS UNHEDGED</div>
                <div class="hero-desc">${esc(pair.refused_reason || 'Pair shape not recognised.')} Check the position on the venue before acting.</div>
              </div>
            </div>
          </div>
        `;
      } else {
        localNakedSinceMs = null;
        hero.innerHTML = `
          <div class="hero-card state-closed">
            <div class="hero-header">
              <div>
                <div class="hero-badge closed">⏹️ CYCLE CLOSED / CANCELLED</div>
                <div class="hero-headline">NO ACTIVE EXPOSURE</div>
                <div class="hero-desc">Orders in this pair are cancelled or fully closed.</div>
              </div>
            </div>
          </div>
        `;
      }
    }

    // LEVEL 1: Render Strategy KPIs (Lifted from server/spread_dash_html.py:1525)
    function renderRunKpis(kpi) {
      const el = document.getElementById('sec-run-kpis');
      if (!kpi || kpi.error) {
        el.innerHTML = '<div class="empty-state-text">No KPI data loaded</div>';
        return;
      }

      const fillRateVal = kpi.fill_rate !== null ? (kpi.fill_rate * 100).toFixed(1) + '%' : '--';
      const uptimeVal = kpi.quote_uptime !== null ? (kpi.quote_uptime * 100).toFixed(1) + '%' : '--';
      const waitVal = kpi.median_seconds_to_fill !== null ? kpi.median_seconds_to_fill.toFixed(1) + 's' : '--';
      const queueVal = kpi.median_queue_ahead !== null ? kpi.median_queue_ahead.toFixed(0) + ' sh' : '--';
      const capVal = kpi.spread_capture_per_share !== null ? (kpi.spread_capture_per_share * 100).toFixed(2) + '¢' : '--';
      const advVal = kpi.adverse_selection !== null ? (kpi.adverse_selection * 100).toFixed(2) + '¢' : '--';
      const pnlVal = kpi.realized_pnl !== null ? (kpi.realized_pnl >= 0 ? '+' : '') + '$' + kpi.realized_pnl.toFixed(2) : '--';
      const roiVal = kpi.roi_on_cost !== null ? (kpi.roi_on_cost * 100).toFixed(1) + '%' : '--';
      const topSkip = (kpi.top_skip_reasons && kpi.top_skip_reasons[0]) ? `${kpi.top_skip_reasons[0].reason} (${kpi.top_skip_reasons[0].cycles})` : 'None';

      const tiles = [
        {
          label: 'Maker Fill Rate',
          val: fillRateVal,
          color: kpi.fill_rate && kpi.fill_rate > 0.5 ? '#10b981' : '#f59e0b',
          sub: `${kpi.filled_shares || 0} filled / ${kpi.posted_shares || 0} posted sh`,
          tipBody: 'Proportion of resting maker bids filled by takers, excluding taker crossing shares.',
          tipFormula: '(filled_shares &minus; crossed_shares) / posted_shares',
        },
        {
          label: 'Quote Uptime & Skips',
          val: uptimeVal,
          color: kpi.quote_uptime && kpi.quote_uptime > 0.8 ? '#10b981' : '#94a3b8',
          sub: `Top Skip: ${esc(topSkip)}`,
          tipBody: 'Fraction of evaluation cycles where quotes were actively posted vs skipped.',
          tipFormula: 'cycles_quoting / total_decision_cycles',
        },
        {
          label: 'Wait to Fill & Queue',
          val: waitVal,
          color: '#38bdf8',
          sub: `Median Queue Ahead: ${queueVal} &middot; n=${kpi.fills || 0}`,
          tipBody: 'Median elapsed seconds from quote placement to venue match timestamp.',
          tipFormula: 'median(venue_ts - quote_posted_ts)',
        },
        {
          label: 'Spread Capture / Share',
          val: capVal,
          color: '#10b981',
          sub: `Total: ${kpi.spread_capture !== null && kpi.spread_capture !== undefined ? '$' + kpi.spread_capture.toFixed(2) : '--'} &middot; avg edge ${kpi.avg_edge_cents !== null && kpi.avg_edge_cents !== undefined ? kpi.avg_edge_cents.toFixed(1) + '¢' : '--'}`,
          tipBody: 'Earned edge vs reference mid-price at time of quote placement per share filled.',
          tipFormula: '&Sigma;(edge_vs_mid &middot; filled_size) / filled_shares',
        },
        {
          label: 'Adverse Selection',
          val: advVal,
          color: kpi.adverse_selection === null || kpi.adverse_selection === undefined ? '#94a3b8' : (kpi.adverse_selection <= 0 ? '#10b981' : '#ef4444'),
          sub: `n=${kpi.markout_samples || 0} markout samples <button onclick="openDistModal('adv')" style="background:none;border:none;color:#38bdf8;cursor:pointer;text-decoration:underline;font:inherit;">chart &nearr;</button>`,
          chart: bellCurveSvg({min: -5, max: 5, mean: (kpi.adverse_selection || 0) * 100, stdev: 1.5, zero: 0, color: (kpi.adverse_selection || 0) <= 0 ? '#10b981' : '#ef4444', w: 180, h: 42}),
          tipBody: 'Size-weighted post-trade drift against us across 4 horizons (5m, 1h, 6h, 15m).',
          tipFormula: '&Sigma;(size &middot; (mid_later &minus; fill_price)) / total_filled',
        },
        {
          label: 'Realized PnL & ROI',
          val: pnlVal,
          color: (kpi.realized_pnl || 0) >= 0 ? '#10b981' : '#ef4444',
          sub: `ROI: ${roiVal} &middot; ${kpi.wins || 0}W / ${kpi.losses || 0}L closes`,
          tipBody: 'Realized trading profit from settled merges and early exits on cost basis.',
          tipFormula: 'realized_pnl / capital_cost',
        },
      ];

      el.innerHTML = tiles.map(t => `
        <div class="kpi-tile">
          <div class="kpi-header">
            <span>${esc(t.label)}</span>
            ${tip(t.label, t.tipBody, t.tipFormula)}
          </div>
          <div class="kpi-val" style="color:${t.color};">${esc(t.val)}</div>
          ${t.chart || ''}
          <div class="kpi-sub">${t.sub}</div>
        </div>
      `).join('');
    }

    // REQ 4: Exposure Over Time Chart (Lifted from server/spread_dash_html.py:1505)
    function renderExposureChart(kpi) {
      const el = document.getElementById('sec-exposure');
      const marks = (kpi && kpi.float_marks) ? kpi.float_marks : [];
      if (!marks || marks.length === 0) {
        el.innerHTML = '<div class="empty-state-text">No float marks recorded yet in this run</div>';
        return;
      }

      const W = 900, H = 160, padL = 50, padR = 20, padT = 16, padB = 24;
      const sorted = marks.slice().sort((a, b) => a.ts - b.ts);
      const t0 = sorted[0].ts;
      const t1 = sorted[sorted.length - 1].ts === t0 ? t0 + 60 : sorted[sorted.length - 1].ts;
      const span = Math.max(1, t1 - t0);

      const maxVal = Math.max(10, ...sorted.map(m => Math.max(m.committed_open_usd, m.naked_usd, m.unrealized_usd)));
      const x = t => padL + ((t - t0) / span) * (W - padL - padR);
      const y = v => (H - padB) - (v / maxVal) * (H - padB - padT);

      const pathCommitted = sorted.map((m, i) => `${i === 0 ? 'M' : 'L'} ${x(m.ts).toFixed(1)} ${y(m.committed_open_usd).toFixed(1)}`).join(' ');
      const pathNaked = sorted.map((m, i) => `${i === 0 ? 'M' : 'L'} ${x(m.ts).toFixed(1)} ${y(m.naked_usd).toFixed(1)}`).join(' ');
      const pathUnrealized = sorted.map((m, i) => `${i === 0 ? 'M' : 'L'} ${x(m.ts).toFixed(1)} ${y(m.unrealized_usd).toFixed(1)}`).join(' ');

      const latest = sorted[sorted.length - 1];

      el.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;font-family:'JetBrains Mono',monospace;font-size:12px;">
          <div style="display:flex;gap:16px;">
            <span style="color:#38bdf8;">● Committed: $${latest.committed_open_usd.toFixed(2)}</span>
            <span style="color:#ef4444;">● Naked Risk: $${latest.naked_usd.toFixed(2)}</span>
            <span style="color:#10b981;">● Unrealised PnL: $${latest.unrealized_usd.toFixed(2)}</span>
          </div>
          <span style="color:var(--text-muted);font-size:11px;">${sorted.length} marks recorded</span>
        </div>
        <div style="overflow-x:auto;">
          <svg viewBox="0 0 ${W} ${H}" style="width:100%;height:${H}px;background:rgba(8,12,20,0.6);border:1px solid var(--border-subtle);border-radius:8px;">
            <!-- Grid lines -->
            <line x1="${padL}" x2="${W - padR}" y1="${y(maxVal * 0.5)}" y2="${y(maxVal * 0.5)}" stroke="rgba(255,255,255,0.05)" stroke-dasharray="3 3"/>
            <line x1="${padL}" x2="${W - padR}" y1="${H - padB}" y2="${H - padB}" stroke="rgba(255,255,255,0.1)"/>
            
            <!-- Series Lines -->
            <path d="${pathCommitted}" fill="none" stroke="#38bdf8" stroke-width="2"/>
            <path d="${pathNaked}" fill="none" stroke="#ef4444" stroke-width="2"/>
            <path d="${pathUnrealized}" fill="none" stroke="#10b981" stroke-width="1.5" stroke-dasharray="4 2"/>

            <!-- Y Axis labels -->
            <text x="${padL - 6}" y="${y(maxVal)}" text-anchor="end" fill="#64748b" font-size="10" font-family="JetBrains Mono">$${maxVal.toFixed(0)}</text>
            <text x="${padL - 6}" y="${H - padB}" text-anchor="end" fill="#64748b" font-size="10" font-family="JetBrains Mono">$0</text>
          </svg>
        </div>
      `;
    }

    // LEVEL 2: Funnel & Market Drill-down (Lifted from server/fleet_dash.py:1106-1180)
    function renderFunnel(kpi) {
      const el = document.getElementById('sec-funnel');
      const funnel = (kpi && kpi.funnel) || {};
      const filters = funnel.filters || [];
      const graduated = funnel.graduated || [];

      const rawCount = funnel.raw_count || 0;
      const filterCount = filters.reduce((acc, f) => acc + (f.n || 0), 0);
      const finalCount = funnel.final_count || graduated.length;
      const gradCount = graduated.length;

      el.innerHTML = `
        <!-- Lane 1: RAW -->
        <div class="funnel-lane">
          <div class="funnel-hdr">
            <span>① RAW CANDIDATES</span>
            <span class="funnel-badge" style="background:#38bdf820;color:#38bdf8;">${rawCount}</span>
          </div>
          <div style="font-size:11px;color:var(--text-muted);font-family:'JetBrains Mono',monospace;">
            Observed in candidate scan & census.
          </div>
        </div>

        <!-- Lane 2: FILTERS -->
        <div class="funnel-lane">
          <div class="funnel-hdr">
            <span>② REFUSALS / GATES</span>
            <span class="funnel-badge" style="background:#f59e0b20;color:#f59e0b;">${filterCount}</span>
          </div>
          <div style="display:flex;flex-direction:column;gap:6px;max-height:180px;overflow-y:auto;">
            ${filters.length ? filters.map(g => `
              <div class="gate-card">
                <div style="display:flex;justify-content:space-between;">
                  <span class="gate-code">${esc(g.cause)}</span>
                  <span style="color:var(--text-secondary);font-weight:700;">${g.n}</span>
                </div>
                ${(g.examples || []).slice(0, 2).map(e => `
                  <div class="gate-ex truncate" title="${esc(e.reason)}">&bull; ${esc(e.title)}: ${esc(e.reason)}</div>
                `).join('')}
              </div>
            `).join('') : '<div class="empty-state-text" style="padding:10px 0;">No refusals logged</div>'}
          </div>
        </div>

        <!-- Lane 3: FINAL -->
        <div class="funnel-lane">
          <div class="funnel-hdr">
            <span>③ FINAL ELIGIBLE</span>
            <span class="funnel-badge" style="background:#a855f720;color:#c084fc;">${finalCount}</span>
          </div>
          <div style="font-size:11px;color:var(--text-muted);font-family:'JetBrains Mono',monospace;">
            Passed spread, inventory, and risk gates.
          </div>
        </div>

        <!-- Lane 4: GRADUATED -->
        <div class="funnel-lane">
          <div class="funnel-hdr">
            <span>④ GRADUATED / LIVE</span>
            <span class="funnel-badge" style="background:#10b98120;color:#10b981;">${gradCount}</span>
          </div>
          <div style="display:flex;flex-direction:column;gap:6px;max-height:180px;overflow-y:auto;">
            ${graduated.length ? graduated.map(g => `
              <div class="gate-card" style="border-left:3px solid #10b981;">
                <div style="font-weight:700;color:var(--text-primary);">${esc(g.title || g.slug || g.condition_id)}</div>
                <div style="display:flex;justify-content:space-between;color:var(--text-secondary);font-size:10px;">
                  <span>${g.fills || 0} fills</span>
                  <span style="color:${(g.pnl || 0) >= 0 ? '#10b981' : '#ef4444'};">${(g.pnl || 0) >= 0 ? '+' : ''}$${(g.pnl || 0).toFixed(2)}</span>
                </div>
              </div>
            `).join('') : '<div class="empty-state-text" style="padding:10px 0;">No active markets</div>'}
          </div>
        </div>
      `;
    }

    function renderMarketsTable(kpi) {
      const tbody = document.getElementById('markets-tbody');
      const byMkt = (kpi && kpi.by_market) ? kpi.by_market : {};
      const cids = Object.keys(byMkt);

      if (cids.length === 0) {
        tbody.innerHTML = '<tr><td colspan="9" class="empty-state-text">No market telemetry in selected run</td></tr>';
        return;
      }

      tbody.innerHTML = cids.map(cid => {
        const m = byMkt[cid];
        const title = m.title || m.slug || cid.slice(0, 16);
        const pnl = m.realized_pnl || 0;
        const pnlColor = pnl >= 0 ? '#10b981' : '#ef4444';
        const pairCostStr = m.pair_cost !== null ? `$${m.pair_cost.toFixed(3)}` : '--';
        const balanceStr = m.balance !== null ? `${(m.balance * 100).toFixed(0)}%` : '--';
        const volStr = m.volume_24h ? `$${(m.volume_24h / 1000).toFixed(0)}K` : '--';
        const daysStr = m.days_to_resolve !== null ? `${m.days_to_resolve.toFixed(1)}d` : '--';

        return `
          <tr class="clickable-row" onclick="openDrilldownModal('${esc(cid)}')">
            <td>
              <strong>${esc(title)}</strong>
              <div style="font-size:10px;color:var(--text-muted);">${esc(cid.slice(0, 10))}...${esc(cid.slice(-6))}</div>
            </td>
            <td>${volStr}</td>
            <td>${daysStr}</td>
            <td>${m.fills_count} / ${m.quotes_count}</td>
            <td>${m.up_sh.toFixed(1)} UP / ${m.dn_sh.toFixed(1)} DN</td>
            <td><strong>${pairCostStr}</strong></td>
            <td>${balanceStr}</td>
            <td style="color:${pnlColor};font-weight:700;">${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}</td>
            <td><button style="background:transparent;border:1px solid var(--border-subtle);color:#38bdf8;padding:2px 8px;border-radius:4px;cursor:pointer;font-family:inherit;font-size:11px;">Drill Down &rarr;</button></td>
          </tr>
        `;
      }).join('');
    }

    // LEVEL 3: Mechanics Panel (Lifted from server/spread_dash_html.py:1572)
    function renderMechanics(kpi, state) {
      const el = document.getElementById('sec-mechanics');
      const lat = (kpi && kpi.order_latency_ms) || {};
      const rec = (kpi && kpi.reconcile_lag_ms) || {};
      const rej = (kpi && kpi.venue_rejects) || { total: 0, by_code: {}, events: [] };
      const divs = (kpi && kpi.three_way_divergences) || { total: 0, events: [] };
      const lock = (state && state.reconcile_lock) || {};

      // A backfilled fill can be recorded 44 minutes after the venue timestamp.
      // Printing that as "2663493.0ms" is unreadable, so scale the unit.
      const durMs = (v) => {
        if (v === null || v === undefined) return '--';
        if (v < 1000) return `${v.toFixed(1)}ms`;
        if (v < 60000) return `${(v / 1000).toFixed(1)}s`;
        if (v < 3600000) return `${(v / 60000).toFixed(1)}m`;
        return `${(v / 3600000).toFixed(1)}h`;
      };
      const latMed = durMs(lat.median);
      const latMax = durMs(lat.max);
      const recMed = durMs(rec.median);
      const recMax = durMs(rec.max);

      el.innerHTML = `
        <div class="mech-card">
          <div style="font-size:11px;color:var(--text-secondary);font-weight:700;">ORDER POST LATENCY</div>
          <div style="font-size:20px;font-weight:800;color:#f8fafc;">${latMed}</div>
          <div style="font-size:11px;color:var(--text-muted);">Max: ${latMax} &middot; n=${lat.count || 0}</div>
        </div>

        <div class="mech-card">
          <div style="font-size:11px;color:var(--text-secondary);font-weight:700;">RECONCILE LAG</div>
          <div style="font-size:20px;font-weight:800;color:${(rec.median || 0) > 1000 ? '#f59e0b' : '#f8fafc'};">${recMed}</div>
          <div style="font-size:11px;color:var(--text-muted);">Max: ${recMax} &middot; n=${rec.count || 0}</div>
        </div>

        <div class="mech-card">
          <div style="font-size:11px;color:var(--text-secondary);font-weight:700;">VENUE REJECTS / ERRORS</div>
          <div style="font-size:20px;font-weight:800;color:${rej.total > 0 ? '#ef4444' : '#10b981'};">${rej.total}</div>
          <div style="font-size:11px;color:var(--text-muted);">${Object.keys(rej.by_code || {}).map(c => `${c}: ${rej.by_code[c]}`).join(', ') || '0 errors'}</div>
        </div>

        <div class="mech-card">
          <div style="font-size:11px;color:var(--text-secondary);font-weight:700;">3-WAY DIVERGENCES</div>
          <div style="font-size:20px;font-weight:800;color:${divs.total > 0 ? '#ef4444' : '#10b981'};">${divs.total}</div>
          <div style="font-size:11px;color:var(--text-muted);">${divs.total === 0 ? 'Clean (Registry=Venue=Chain)' : 'Investigate incidents'}</div>
        </div>
      `;

      // Detail table for venue rejects if any
      const rejTbody = document.getElementById('venue-rejects-tbody');
      const rejBox = document.getElementById('venue-rejects-detail');
      if (rej.events && rej.events.length > 0) {
        rejBox.style.display = 'block';
        rejTbody.innerHTML = rej.events.map(e => `
          <tr>
            <td>${formatTime(e.ts * 1000)}</td>
            <td style="color:#ef4444;font-weight:700;">${esc(e.code)}</td>
            <td>${esc(e.side || '--')}</td>
            <td>${e.price ? '$' + e.price.toFixed(3) : '--'}</td>
            <td>${e.size ? e.size.toFixed(1) : '--'}</td>
            <td style="color:#fca5a5;">${esc(e.message)}</td>
          </tr>
        `).join('');
      } else {
        rejBox.style.display = 'none';
      }
    }

    function openDrilldownModal(cid) {
      const byMkt = (lastKpi && lastKpi.by_market) ? lastKpi.by_market : {};
      const m = byMkt[cid];
      if (!m) return;

      document.getElementById('modal-mkt-title').textContent = m.title || m.slug || cid;
      document.getElementById('modal-mkt-sub').textContent = `Condition ID: ${cid} | ${m.url ? m.url : ''}`;

      const body = document.getElementById('modal-drilldown-body');
      
      // Quotes section
      const quotesHtml = (m.quotes || []).map(q => `
        <tr>
          <td>${formatTime(q.ts * 1000)}</td>
          <td><strong>${esc(q.side)}</strong></td>
          <td>$${parseFloat(q.price).toFixed(3)}</td>
          <td>${parseFloat(q.size).toFixed(1)}</td>
          <td>${q.mid ? '$' + parseFloat(q.mid).toFixed(3) : '--'}</td>
          <td>${q.edge_vs_mid ? (parseFloat(q.edge_vs_mid) * 100).toFixed(2) + '¢' : '--'}</td>
          <td>${q.queue_ahead != null ? parseFloat(q.queue_ahead).toFixed(0) : '--'}</td>
          <td>${q.filled ? parseFloat(q.filled).toFixed(1) : '0.0'}</td>
          <td>${q.latency_ms ? parseFloat(q.latency_ms).toFixed(1) + 'ms' : '--'}</td>
        </tr>
      `).join('') || '<tr><td colspan="9" class="empty-state-text">No quotes logged</td></tr>';

      // 4-Horizon Markouts section
      const markoutsHtml = (m.markouts || []).map(mo => `
        <tr>
          <td>${formatTime(mo.ts * 1000)}</td>
          <td><strong>${esc(mo.side)}</strong></td>
          <td>$${mo.fill_price.toFixed(3)}</td>
          <td>${mo.size.toFixed(1)}</td>
          <td>${mo.ref_mid ? '$' + mo.ref_mid.toFixed(3) : '--'}</td>
          <td>${mo.drift_h0 !== null && mo.drift_h0 !== undefined ? (mo.drift_h0 * 100).toFixed(2) + '¢' : '--'}</td>
          <td>${mo.drift_h3 !== null && mo.drift_h3 !== undefined ? (mo.drift_h3 * 100).toFixed(2) + '¢' : '--'}</td>
          <td>${mo.drift_h1 !== null && mo.drift_h1 !== undefined ? (mo.drift_h1 * 100).toFixed(2) + '¢' : '--'}</td>
          <td>${mo.drift_h2 !== null && mo.drift_h2 !== undefined ? (mo.drift_h2 * 100).toFixed(2) + '¢' : '--'}</td>
        </tr>
      `).join('') || '<tr><td colspan="9" class="empty-state-text">No markout records for this market</td></tr>';

      // Skips section
      const skipsHtml = (m.skip_events || []).map(e => `
        <tr>
          <td>${formatTime(e.ts * 1000)}</td>
          <td style="color:#f59e0b;font-weight:700;">${esc(e.reason_code)}</td>
          <td>${esc(e.kind)}</td>
          <td>${esc(e.reason || '')}</td>
        </tr>
      `).join('') || '<tr><td colspan="4" class="empty-state-text">No skip events logged</td></tr>';

      body.innerHTML = `
        <div class="panel" style="padding:12px;">
          <div style="font-family:'JetBrains Mono',monospace;font-size:12px;font-weight:700;color:var(--text-secondary);margin-bottom:6px;">QUOTES PLACEMENT VS MID</div>
          <div class="table-container">
            <table>
              <thead><tr><th>Time</th><th>Side</th><th>Price</th><th>Size</th><th>Mid</th><th>Edge</th><th>Queue</th><th>Filled</th><th>Latency</th></tr></thead>
              <tbody>${quotesHtml}</tbody>
            </table>
          </div>
        </div>

        <div class="panel" style="padding:12px;">
          <div style="font-family:'JetBrains Mono',monospace;font-size:12px;font-weight:700;color:var(--text-secondary);margin-bottom:6px;">FILLS WITH 4 MARKOUT HORIZONS</div>
          <div class="table-container">
            <table>
              <thead><tr><th>Time</th><th>Side</th><th>Fill Px</th><th>Size</th><th>Ref Mid</th><th>h0 (5m)</th><th>h3 (15m)</th><th>h1 (1h)</th><th>h2 (6h)</th></tr></thead>
              <tbody>${markoutsHtml}</tbody>
            </table>
          </div>
        </div>

        <div class="panel" style="padding:12px;">
          <div style="font-family:'JetBrains Mono',monospace;font-size:12px;font-weight:700;color:var(--text-secondary);margin-bottom:6px;">MARKET EVENT & SKIP TIMELINE</div>
          <div class="table-container">
            <table>
              <thead><tr><th>Time</th><th>Reason Code</th><th>Kind</th><th>Detail</th></tr></thead>
              <tbody>${skipsHtml}</tbody>
            </table>
          </div>
        </div>
      `;

      document.getElementById('modal-drilldown').style.display = 'flex';
    }

    function closeDrilldownModal() {
      document.getElementById('modal-drilldown').style.display = 'none';
    }

    function openDistModal(type) {
      const modal = document.getElementById('modal-dist');
      const body = document.getElementById('modal-dist-body');
      const title = document.getElementById('modal-dist-title');

      const adv = lastKpi ? (lastKpi.adverse_selection || 0) * 100 : 0;
      title.textContent = "Adverse Selection Markout Distribution";
      body.innerHTML = `
        <div style="padding:10px;">
          <div style="font-family:'JetBrains Mono',monospace;font-size:13px;color:var(--text-secondary);margin-bottom:12px;">
            Size-weighted post-trade drift &Delta; = mid_later &minus; fill_price (in cents). Negative = adverse selection against maker.
          </div>
          ${bellCurveSvg({min: -8, max: 8, mean: adv, stdev: 2.0, zero: 0, color: adv <= 0 ? '#10b981' : '#ef4444', w: 600, h: 160})}
          <div style="display:flex;justify-content:space-between;margin-top:12px;font-family:'JetBrains Mono',monospace;font-size:12px;">
            <span>Sample Mean: <strong style="color:${adv <= 0 ? '#10b981' : '#ef4444'};">${adv.toFixed(2)}¢</strong></span>
            <span>Horizon: 6h/1h/15m/5m</span>
            <span>Samples: ${lastKpi ? lastKpi.markout_samples || 0 : 0}</span>
          </div>
        </div>
      `;
      modal.style.display = 'flex';
    }

    function closeDistModal() {
      document.getElementById('modal-dist').style.display = 'none';
    }

    function renderOrders(state) {
      const tbody = document.getElementById('orders-tbody');
      const badge = document.getElementById('order-count-badge');
      const orders = state.orders || [];

      badge.textContent = `${orders.length} order${orders.length === 1 ? '' : 's'}`;

      if (orders.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="empty-state-text">No orders recorded in database</td></tr>';
        return;
      }

      tbody.innerHTML = orders.map(o => {
        const size = parseFloat(o.original_size).toFixed(2);
        const matched = parseFloat(o.size_matched).toFixed(2);
        const pct = o.original_size > 0 ? Math.min(100, Math.round((o.size_matched / o.original_size) * 100)) : 0;
        const price = parseFloat(o.price).toFixed(3);
        const age = formatDuration(o.age_sec);
        const isUnattr = o.status === 'unattributed';
        const tagClass = KNOWN_STATUSES.includes(o.status) ? o.status : 'open';

        return `
          <tr>
            <td>
              <strong>${esc(o.side || 'BUY')}</strong>
              <div style="font-size:10px;color:var(--text-muted);">${esc((o.token_id || '').slice(0, 10))}...</div>
            </td>
            <td>$${price}</td>
            <td>${size}</td>
            <td><strong>${matched}</strong></td>
            <td>${pct}%</td>
            <td>
              <span class="status-tag ${tagClass}">
                ${isUnattr ? '⚠️ UNATTRIBUTED' : esc(o.status)}
              </span>
            </td>
            <td>${age}</td>
          </tr>
        `;
      }).join('');
    }

    function renderCapital(state) {
      const cap = state.capital || {};
      document.getElementById('stat-total-committed').textContent = `$${(cap.total_committed || 0).toFixed(2)}`;
      document.getElementById('stat-resting-committed').textContent = `$${(cap.resting_committed || 0).toFixed(2)}`;
      document.getElementById('stat-filled-committed').textContent = `$${(cap.filled_committed || 0).toFixed(2)}`;

      const pairs = state.pairs || [];
      if (pairs.length > 0) {
        const p = pairs[0];
        document.getElementById('stat-pair-price').textContent = p.combined_price ? `$${p.combined_price.toFixed(3)}` : '--';
        document.getElementById('stat-max-pair-cost').textContent = p.max_pair_cost_at_post ? `$${p.max_pair_cost_at_post.toFixed(3)}` : '< $1.00';
      } else {
        document.getElementById('stat-pair-price').textContent = '--';
        document.getElementById('stat-max-pair-cost').textContent = '--';
      }
    }

    function renderFills(state) {
      const tbody = document.getElementById('fills-tbody');
      const badge = document.getElementById('fill-count-badge');
      const fills = state.fills || [];

      badge.textContent = `${fills.length} fill${fills.length === 1 ? '' : 's'}`;

      if (fills.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="empty-state-text">No fills recorded yet</td></tr>';
        return;
      }

      tbody.innerHTML = fills.map(f => {
        const notional = (f.size * f.price).toFixed(2);
        return `
          <tr>
            <td>
              <div>${esc(f.venue_time_str || formatTime(f.venue_ts))}</div>
              <div style="font-size:10px;color:var(--text-muted);">${formatDuration(f.age_sec)} ago</div>
            </td>
            <td><span style="font-size:11px;color:var(--text-secondary);">${esc((f.trade_id || '').slice(0, 12))}</span></td>
            <td><strong>${esc(f.side || 'BUY')}</strong></td>
            <td>$${parseFloat(f.price).toFixed(3)}</td>
            <td>${parseFloat(f.size).toFixed(2)}</td>
            <td><strong>$${notional}</strong></td>
          </tr>
        `;
      }).join('');
    }

    function renderFreshnessAndLock(state) {
      const pollPill = document.getElementById('poll-pill');
      const staleBanner = document.getElementById('stale-alert-banner');
      const staleText = document.getElementById('stale-alert-text');
      const lastPollEl = document.getElementById('telemetry-last-poll');
      const pollStatusEl = document.getElementById('telemetry-poll-status');

      localLastPollMs = state.last_polled_ts;

      if (state.empty || !state.last_polled_ts) {
        pollPill.className = 'pill pill-neutral';
        pollPill.textContent = 'NO POLL DATA';
        staleBanner.style.display = 'none';
        lastPollEl.textContent = 'Never';
        pollStatusEl.textContent = 'Idle';
      } else {
        const secSince = state.seconds_since_poll || 0;
        lastPollEl.textContent = `${formatTime(state.last_polled_ts)} (${formatDuration(secSince)} ago)`;

        if (state.stale && state.idle) {
          pollPill.className = 'pill pill-neutral';
          pollPill.textContent = 'IDLE — NO CYCLE RUNNING';
          staleBanner.style.display = 'none';
          pollStatusEl.textContent = `Idle (last poll ${formatDuration(secSince)} ago)`;
        } else if (state.stale) {
          pollPill.className = 'pill pill-stale';
          pollPill.textContent = `STALE (${Math.round(secSince)}s)`;
          staleBanner.style.display = 'flex';
          staleText.textContent = `⚠️ STALE TELEMETRY: money is at stake and the poll loop has not run in ${Math.round(secSince)}s (>30s limit). Check the supervisor or the poll script.`;
          pollStatusEl.textContent = 'STALE (>30s)';
          pollStatusEl.style.color = 'var(--red-alert)';
        } else {
          pollPill.className = 'pill pill-fresh';
          pollPill.textContent = `POLL OK (${Math.round(secSince)}s)`;
          staleBanner.style.display = 'none';
          pollStatusEl.textContent = 'Healthy';
          pollStatusEl.style.color = 'var(--green-ok)';
        }
      }

      // Reconcile lock
      const lock = state.reconcile_lock || {};
      const lockBox = document.getElementById('lock-container');
      const lockStatus = document.getElementById('lock-status-text');

      if (lock.held) {
        lockBox.className = 'lock-box lock-active';
        lockStatus.innerHTML = `<span style="color:#f59e0b;font-weight:700;">HELD</span> by <code>${esc(lock.holder)}</code> (acquired ${formatDuration(lock.age_sec)} ago)`;
      } else {
        lockBox.className = 'lock-box lock-idle';
        lockStatus.textContent = 'Idle (no reconcile pass in flight)';
      }
    }

    function renderRunsSelector(kpi) {
      const sel = document.getElementById('run-selector');
      const runs = (kpi && kpi.runs) || [];
      const cur = selectedRunId || (kpi && kpi.active_run_id) || "";

      // Only rebuild if count changed or empty
      if (sel.options.length <= 1 && runs.length > 0) {
        sel.innerHTML = runs.map(r => {
          const pnlStr = (r.realized_pnl >= 0 ? '+' : '') + '$' + r.realized_pnl.toFixed(2);
          const timeStr = r.last_ts ? new Date(r.last_ts * 1000).toLocaleTimeString() : '';
          return `<option value="${esc(r.run_id)}">${esc(r.run_id)} (${pnlStr}, ${r.fills_count}f, ${timeStr})</option>`;
        }).join('') + '<option value="all">All Runs (Pooled)</option>';
        if (cur) sel.value = cur;
      }
    }

    document.getElementById('run-selector').addEventListener('change', (e) => {
      selectedRunId = e.target.value;
      pollState();
    });

    document.getElementById('port-pill').textContent =
      ':' + (window.location.port || (window.location.protocol === 'https:' ? '443' : '80'));

    async function pollState() {
      try {
        const [stateRes, kpiRes] = await Promise.all([
          fetch('/api/state'),
          fetch(`/api/kpi${selectedRunId ? '?run_id=' + encodeURIComponent(selectedRunId) : ''}`)
        ]);

        if (!stateRes.ok) throw new Error(`HTTP ${stateRes.status}`);
        const state = await stateRes.json();
        lastState = state;

        let kpi = {};
        if (kpiRes.ok) {
          kpi = await kpiRes.json();
          lastKpi = kpi;
        }

        document.getElementById('db-path-display').textContent = `DB: ${state.db_path || 'run/live.db'}`;
        renderRunsSelector(kpi);
        renderMarketStrip(state);
        renderHero(state);
        renderRunKpis(kpi);
        renderExposureChart(kpi);
        renderFunnel(kpi);
        renderMarketsTable(kpi);
        renderMechanics(kpi, state);
        renderOrders(state);
        renderCapital(state);
        renderFills(state);
        renderFreshnessAndLock(state);
      } catch (err) {
        const pollPill = document.getElementById('poll-pill');
        pollPill.className = 'pill pill-stale';
        pollPill.textContent = 'OFFLINE';
      }
    }

    // Local 1-second interval ticker for smooth counting up of timers and clock
    setInterval(() => {
      document.getElementById('clock-display').textContent = new Date().toLocaleTimeString();

      if (localNakedSinceMs) {
        const timerEl = document.getElementById('live-naked-timer');
        if (timerEl) {
          const sec = Math.max(0, Math.floor((Date.now() - localNakedSinceMs) / 1000));
          timerEl.textContent = formatDuration(sec);
        }
      }

      if (localLastPollMs && lastState && !lastState.empty) {
        const secSince = Math.max(0, (Date.now() - localLastPollMs) / 1000);
        if (secSince > 30 && !lastState.stale) {
          lastState.stale = true;
          renderFreshnessAndLock(lastState);
        }
      }
    }, 1000);

    // Initial poll and recurring loop
    pollState();
    setInterval(pollState, 2000);
  </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def index():
    """Serve the live execution monitor."""
    return HTMLResponse(PAGE_HTML)


def main():
    import uvicorn

    parser = argparse.ArgumentParser(description="Spread Hunter Live Execution Monitor")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port to bind (default: 8799)")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host interface (default: 127.0.0.1)")
    parser.add_argument("--db", type=str, default=None, help="Path to live.db SQLite file")
    args = parser.parse_args()

    if args.db:
        set_db_override(args.db)

    print(f"Starting Live Execution Dashboard on http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
