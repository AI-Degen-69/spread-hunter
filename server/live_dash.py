"""Single-market live execution monitor (:8799).

Watched during ONE supervised live cycle. Its primary job is to make an
unhedged (NAKED) leg impossible to miss from across the room.

Telemetry only: reads SQLite orders, fills, and reconcile_lock directly
from `live/run/live.db` (or `run/live.db`) via read-only URI mode:
`sqlite3.connect('file:<path>?mode=ro', uri=True)`.

Zero venue network calls. Zero credentials needed.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.middleware.gzip import GZipMiddleware

ROOT = Path(__file__).resolve().parent.parent
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
    # Prefer run/live.db at root if present, else live/run/live.db
    p_run = ROOT / "run" / "live.db"
    if p_run.exists():
        return p_run
    p_live = ROOT / "live" / "run" / "live.db"
    if p_live.exists():
        return p_live
    return p_run


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

        # Query reconcile_lock
        lock_row = None
        if "reconcile_lock" in tables:
            lock_row = cur.execute(
                "SELECT id, holder, acquired_ts FROM reconcile_lock WHERE id = 1"
            ).fetchone()

        con.close()
    except Exception as e:
        try:
            con.close()
        except Exception:
            pass
        empty_payload["message"] = f"Query error: {e}"
        return empty_payload

    rec_lock_info = {
        "held": False,
        "holder": None,
        "acquired_ts": None,
        "age_sec": None,
    }
    if lock_row:
        acq_ts = lock_row["acquired_ts"]
        rec_lock_info = {
            "held": True,
            "holder": lock_row["holder"],
            "acquired_ts": acq_ts,
            "age_sec": max(0.0, round((now_ms - acq_ts) / 1000.0, 1)),
        }

    empty_payload["reconcile_lock"] = rec_lock_info

    if not orders_rows:
        empty_payload["message"] = "No live orders recorded yet in registry."
        return empty_payload

    # Process orders
    orders_list = []
    max_poll_ms = 0
    total_committed = 0.0
    resting_committed = 0.0
    filled_committed = 0.0

    for r in orders_rows:
        o = dict(r)
        o["is_unattributed"] = (o["status"] == "unattributed")
        o["age_sec"] = max(0.0, round((now_ms - o["posted_ts"]) / 1000.0, 1))
        o["poll_age_sec"] = (
            max(0.0, round((now_ms - o["last_polled_ts"]) / 1000.0, 1))
            if o["last_polled_ts"]
            else None
        )
        if o["last_polled_ts"] and o["last_polled_ts"] > max_poll_ms:
            max_poll_ms = o["last_polled_ts"]

        size = float(o["original_size"])
        matched = float(o["size_matched"])
        price = float(o["price"])
        status = o["status"]

        if o["side"] == "SELL":
            # An unwind returns collateral rather than committing it.
            # Adding it here made `exit` read as growing the position.
            pass
        elif status not in ("cancelled",):
            total_committed += (size * price)
            remaining = max(0.0, size - matched)
            resting_committed += (remaining * price)
            filled_committed += (matched * price)
        else:
            total_committed += (matched * price)
            filled_committed += (matched * price)

        orders_list.append(o)

    # Process fills
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
    for pid, pdata in pairs_map.items():
        legs = pdata["orders"]
        # One price per token, not per order: after `exit` or `complete` a
        # pair holds three orders on two tokens, and summing all three
        # reports a pair cost that was never paid.
        # The opening BUY on each token is what the pair cost. A later SELL
        # from `exit` prices an unwind, not the position, and the row order
        # the query happens to return must not decide which one is read.
        _open_by_token: dict[str, float] = {}
        for _l in sorted(legs, key=lambda x: (x["side"] != "BUY", x["posted_ts"])):
            _open_by_token.setdefault(_l["token_id"], float(_l["price"]))
        combined_price = sum(_open_by_token.values())
        pdata["combined_price"] = round(combined_price, 4)

        order_ids_in_pair = {l["id"] for l in legs}
        pair_fills = [f for f in fills_list if f["order_uuid"] in order_ids_in_pair]
        pair_fills.sort(key=lambda x: x["venue_ts"])

        # Classify by TOKEN, not by order. A pair is two tokens; each token can
        # carry any number of orders, because `exit` adds a SELL on a token
        # already in the pair and `complete` adds a crossing BUY on the other.
        # Counting orders instead made a three-order pair -- the normal shape
        # during exit and complete -- fall through to a calm RESTING with no naked
        # warning, which is the one state this page exists never to show.
        # live/strategy/live_pairs.py:347-375 groups the same way.
        tokens: dict[str, dict[str, Any]] = {}
        for o in legs:
            tok = tokens.setdefault(o["token_id"], {
                "token_id": o["token_id"],
                "net_matched": 0.0,
                "notional": 0.0,
                "orders": [],
            })
            matched = float(o["size_matched"])
            # SELL reduces the position on its token. Summing it as an increase
            # inverts the answer on exactly the pairs `exit` has already acted on.
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
            # live_pairs.load_pair refuses this outright rather than reducing to
            # the largest two. A dashboard must not be more confident than the
            # engine it watches, so it refuses too -- loudly, not silently.
            hedge_state = "REFUSED"
            pdata["refused_reason"] = (
                f"pair spans {len(tokens)} token ids; a pair is two legs. "
                f"Position cannot be classified from a reduced view."
            )
        else:
            nets = [t["net_matched"] for t in tokens.values()]
            if all(abs(n) <= 1e-6 for n in nets):
                # No net position on either token. RESTING while any order
                # still works the book; CLOSED once nothing is live, which
                # covers a pair flattened by `exit` as well as a cancelled one.
                working = {"open", "pending", "partial"}
                hedge_state = ("RESTING"
                               if any(l["status"] in working for l in legs)
                               else "CLOSED")
            elif len(tokens) == 2:
                a, b = list(tokens.values())
                diff = round(a["net_matched"] - b["net_matched"], 6)
                if abs(diff) <= 1e-6:
                    hedge_state = "BALANCED"
                else:
                    hedge_state = "NAKED"
                    heavy = a if a["net_matched"] > b["net_matched"] else b
                    naked_info = _naked_from(heavy, abs(diff))
            else:
                # One token holding a net position is unhedged by definition.
                only = next(iter(tokens.values()))
                hedge_state = "NAKED"
                naked_info = _naked_from(only, only["net_matched"])

        pdata["hedge_state"] = hedge_state
        pdata["naked_info"] = naked_info
        pairs_list.append(pdata)

    seconds_since_poll = (
        max(0.0, round((now_ms - max_poll_ms) / 1000.0, 1))
        if max_poll_ms > 0
        else None
    )
    stale = (seconds_since_poll is None) or (seconds_since_poll > STALE_THRESHOLD_SEC)

    rec_lock_info = {
        "held": False,
        "holder": None,
        "acquired_ts": None,
        "age_sec": None,
    }
    if lock_row:
        acq_ts = lock_row["acquired_ts"]
        rec_lock_info = {
            "held": True,
            "holder": lock_row["holder"],
            "acquired_ts": acq_ts,
            "age_sec": max(0.0, round((now_ms - acq_ts) / 1000.0, 1)),
        }

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
        "reconcile_lock": rec_lock_info,
    }


app = FastAPI(title="Spread Hunter Live Monitor")
app.add_middleware(GZipMiddleware, minimum_size=1000)

_ACTIVE_DB_OVERRIDE: Path | None = None


def set_db_override(path: Path | str | None) -> None:
    global _ACTIVE_DB_OVERRIDE
    _ACTIVE_DB_OVERRIDE = Path(path) if path else None


@app.get("/api/state")
def get_state(db: str | None = Query(default=None)):
    """Return JSON state snapshot for the live execution dashboard."""
    target_path = resolve_db_path(db or _ACTIVE_DB_OVERRIDE)
    return JSONResponse(query_db_state(target_path))


PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Spread Hunter — Live Cycle Monitor</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&family=JetBrains+Mono:wght@400;500;700;800&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg-base: #090d16;
      --bg-surface: #0f172a;
      --bg-surface-raised: #1e293b;
      --bg-card: rgba(15, 23, 42, 0.75);
      --border-subtle: rgba(255, 255, 255, 0.08);
      --border-strong: rgba(255, 255, 255, 0.16);
      --text-primary: #f8fafc;
      --text-secondary: #94a3b8;
      --text-muted: #64748b;
      
      --red-alert: #ef4444;
      --red-bg: rgba(127, 29, 29, 0.45);
      --red-border: #dc2626;
      --red-glow: 0 0 45px rgba(239, 68, 68, 0.35);

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

    * {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }

    body {
      background-color: var(--bg-base);
      color: var(--text-primary);
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      min-height: 100vh;
      padding: 24px;
      line-height: 1.5;
    }

    .container {
      max-width: 1280px;
      margin: 0 auto;
      display: flex;
      flex-direction: column;
      gap: 20px;
    }

    /* Top Bar */
    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 16px 20px;
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
      border-radius: 12px;
      backdrop-filter: blur(8px);
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
    }

    .brand-icon {
      font-size: 24px;
    }

    .brand-title {
      font-size: 18px;
      font-weight: 800;
      letter-spacing: -0.02em;
      color: var(--text-primary);
    }

    .brand-sub {
      font-size: 12px;
      color: var(--text-secondary);
      font-family: 'JetBrains Mono', monospace;
    }

    .top-meta {
      display: flex;
      align-items: center;
      gap: 16px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
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

    .pill-fresh {
      background: var(--green-bg);
      color: #34d399;
      border: 1px solid var(--green-border);
    }

    .pill-stale {
      background: var(--red-bg);
      color: #fca5a5;
      border: 1px solid var(--red-border);
      animation: pulse 1.5s infinite;
    }

    .pill-neutral {
      background: var(--bg-surface-raised);
      color: var(--text-secondary);
      border: 1px solid var(--border-subtle);
    }

    /* Stale Warning Banner */
    .stale-banner {
      display: none;
      background: linear-gradient(90deg, #991b1b, #7f1d1d);
      border: 2px solid var(--red-alert);
      color: #fee2e2;
      padding: 14px 20px;
      border-radius: 10px;
      font-weight: 700;
      font-size: 15px;
      box-shadow: var(--red-glow);
      animation: pulse 1.5s infinite;
      align-items: center;
      gap: 12px;
    }

    @keyframes pulse {
      0%, 100% { opacity: 1; transform: scale(1); }
      50% { opacity: 0.88; transform: scale(0.995); }
    }

    @keyframes flashBg {
      0%, 100% { background-color: rgba(127, 29, 29, 0.7); }
      50% { background-color: rgba(185, 28, 28, 0.95); }
    }

    /* SECTION 1: HEDGE STATE (THE HERO CARD) */
    .hero-card {
      border-radius: 16px;
      padding: 28px 32px;
      background: var(--bg-surface);
      border: 2px solid var(--border-strong);
      transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
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

    .hero-card.state-closed {
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
    }

    .hero-card.state-empty {
      background: var(--bg-surface);
      border: 1px dashed var(--border-strong);
      text-align: center;
      padding: 48px 24px;
    }

    .hero-header {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      margin-bottom: 20px;
    }

    .hero-badge {
      display: inline-flex;
      align-items: center;
      gap: 10px;
      font-size: 14px;
      font-weight: 800;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      padding: 6px 14px;
      border-radius: 8px;
      font-family: 'JetBrains Mono', monospace;
    }

    .hero-badge.naked {
      background: #ef4444;
      color: #ffffff;
    }

    .hero-badge.balanced {
      background: #10b981;
      color: #ffffff;
    }

    .hero-badge.resting {
      background: #0284c7;
      color: #ffffff;
    }

    .hero-badge.closed {
      background: #475569;
      color: #f1f5f9;
    }

    .hero-headline {
      font-size: 32px;
      font-weight: 900;
      letter-spacing: -0.03em;
      margin-top: 10px;
      line-height: 1.2;
    }

    .hero-desc {
      font-size: 15px;
      color: var(--text-secondary);
      margin-top: 6px;
    }

    /* Naked-specific big indicators */
    .naked-metrics {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 20px;
      margin-top: 24px;
      padding: 20px;
      background: rgba(0, 0, 0, 0.4);
      border-radius: 12px;
      border: 1px solid rgba(239, 68, 68, 0.4);
    }

    .metric-block {
      display: flex;
      flex-direction: column;
      gap: 4px;
    }

    .metric-label {
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: #fca5a5;
      font-family: 'JetBrains Mono', monospace;
    }

    .metric-val-huge {
      font-size: 36px;
      font-weight: 900;
      color: #ffffff;
      font-family: 'JetBrains Mono', monospace;
      letter-spacing: -0.02em;
    }

    .timer-val {
      color: #fef08a;
    }

    /* SECTION 2 & 3: GRID (Orders + Capital) */
    .grid-2col {
      display: grid;
      grid-template-columns: 2fr 1fr;
      gap: 20px;
    }

    .panel {
      background: var(--bg-surface);
      border: 1px solid var(--border-subtle);
      border-radius: 12px;
      padding: 20px;
      display: flex;
      flex-direction: column;
      gap: 14px;
    }

    .panel-title {
      font-size: 14px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--text-secondary);
      display: flex;
      justify-content: space-between;
      align-items: center;
    }

    /* Tables */
    .table-container {
      overflow-x: auto;
    }

    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
      font-family: 'JetBrains Mono', monospace;
    }

    th {
      text-align: left;
      padding: 10px 12px;
      color: var(--text-muted);
      font-weight: 600;
      font-size: 11px;
      text-transform: uppercase;
      border-bottom: 1px solid var(--border-subtle);
    }

    td {
      padding: 12px;
      border-bottom: 1px solid rgba(255, 255, 255, 0.04);
      color: var(--text-primary);
    }

    tr:hover td {
      background: rgba(255, 255, 255, 0.02);
    }

    .status-tag {
      display: inline-block;
      padding: 3px 8px;
      border-radius: 4px;
      font-weight: 700;
      font-size: 11px;
      text-transform: uppercase;
    }

    .status-tag.open { background: var(--blue-bg); color: var(--blue-rest); border: 1px solid var(--blue-border); }
    .status-tag.filled { background: var(--green-bg); color: #34d399; border: 1px solid var(--green-border); }
    .status-tag.partial { background: var(--amber-bg); color: #fcd34d; border: 1px solid var(--amber-border); }
    .status-tag.cancelled { background: var(--bg-surface-raised); color: var(--text-muted); }
    .status-tag.unattributed {
      background: #ea580c;
      color: #ffffff;
      border: 1px solid #f97316;
      animation: pulse 1.5s infinite;
    }

    /* Capital Stats */
    .stat-list {
      display: flex;
      flex-direction: column;
      gap: 12px;
    }

    .stat-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 10px 12px;
      background: var(--bg-surface-raised);
      border-radius: 8px;
      border: 1px solid var(--border-subtle);
    }

    .stat-label {
      font-size: 12px;
      color: var(--text-secondary);
    }

    .stat-val {
      font-family: 'JetBrains Mono', monospace;
      font-weight: 700;
      font-size: 15px;
    }

    /* Bottom Grid: Fills & Telemetry */
    .grid-bottom {
      display: grid;
      grid-template-columns: 3fr 2fr;
      gap: 20px;
    }

    .lock-box {
      padding: 14px;
      border-radius: 8px;
      background: var(--bg-surface-raised);
      border: 1px solid var(--border-subtle);
      font-size: 12px;
      font-family: 'JetBrains Mono', monospace;
    }

    .lock-idle {
      border-left: 4px solid var(--green-ok);
    }

    .lock-active {
      border-left: 4px solid var(--amber-warn);
    }

    .empty-state-text {
      color: var(--text-muted);
      font-style: italic;
      padding: 20px 0;
      text-align: center;
    }
  </style>
</head>
<body>
  <div class="container">
    <!-- Header -->
    <header>
      <div class="brand">
        <span class="brand-icon">🎯</span>
        <div>
          <div class="brand-title">SPREAD HUNTER // LIVE CYCLE</div>
          <div class="brand-sub" id="db-path-display">DB: run/live.db</div>
        </div>
      </div>
      <div class="top-meta">
        <span id="poll-pill" class="pill pill-neutral">CONNECTING...</span>
        <span id="port-pill" class="pill pill-neutral"></span>
        <span id="clock-display">--:--:--</span>
      </div>
    </header>

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

    <!-- MAIN GRID: ORDERS + CAPITAL -->
    <div class="grid-2col">
      <!-- Order Matrix -->
      <div class="panel">
        <div class="panel-title">
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
        <div class="panel-title">
          <span>Capital & Pair Cost</span>
        </div>
        <div class="stat-list">
          <div class="stat-row">
            <span class="stat-label">Total Capital Committed</span>
            <span class="stat-val" id="stat-total-committed">$0.00</span>
          </div>
          <div class="stat-row">
            <span class="stat-label">Resting Notional</span>
            <span class="stat-val" id="stat-resting-committed">$0.00</span>
          </div>
          <div class="stat-row">
            <span class="stat-label">Filled Notional</span>
            <span class="stat-val" id="stat-filled-committed">$0.00</span>
          </div>
          <div class="stat-row">
            <span class="stat-label">Combined Pair Price</span>
            <span class="stat-val" id="stat-pair-price">--</span>
          </div>
          <div class="stat-row">
            <span class="stat-label">Max Pair Cost Limit</span>
            <span class="stat-val" id="stat-max-pair-cost">--</span>
          </div>
        </div>
      </div>
    </div>

    <!-- BOTTOM GRID: FILLS TIMELINE + FRESHNESS & LOCK -->
    <div class="grid-bottom">
      <!-- Fills Timeline -->
      <div class="panel">
        <div class="panel-title">
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
        <div class="panel-title">
          <span>Telemetry & Lock State</span>
        </div>
        <div class="stat-list">
          <div class="stat-row">
            <span class="stat-label">Last Venue Poll</span>
            <span class="stat-val" id="telemetry-last-poll">--</span>
          </div>
          <div class="stat-row">
            <span class="stat-label">Poll Status</span>
            <span class="stat-val" id="telemetry-poll-status">Waiting</span>
          </div>
          <div id="lock-container" class="lock-box lock-idle">
            <strong>Reconcile Lock:</strong> <span id="lock-status-text">Idle (no pass in flight)</span>
          </div>
        </div>
      </div>
    </div>
  </div>

  <script>
    let lastState = null;
    let localNakedSinceMs = null;
    let localLastPollMs = null;

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
      if (m > 0) {
        return `${m}m ${remS}s`;
      }
      return `${s}s`;
    }

    function renderHero(state) {
      const hero = document.getElementById('hero-container');
      if (state.empty || !state.pairs || state.pairs.length === 0) {
        hero.innerHTML = `
          <div class="hero-card state-empty">
            <div class="hero-badge resting">📡 AWAITING LIVE ORDERS</div>
            <div class="hero-headline">No active orders in registry</div>
            <div class="hero-desc">${state.message || "Orders posted via live_exec quote will appear here automatically."}</div>
          </div>
        `;
        localNakedSinceMs = null;
        return;
      }

      // We focus on the primary pair (first pair)
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
      } else if (hedgeState === 'BALANCED') {
        localNakedSinceMs = null;
        const totalFills = pair.orders.reduce((acc, o) => acc + (o.size_matched || 0), 0);
        hero.innerHTML = `
          <div class="hero-card state-balanced">
            <div class="hero-header">
              <div>
                <div class="hero-badge balanced">🛡️ INVENTORY BALANCED</div>
                <div class="hero-headline">PERFECTLY HEDGED PAIR</div>
                <div class="hero-desc">Both legs filled in equal size (${(totalFills / 2).toFixed(2)} shares matched). Inventory neutral — holding to resolution.</div>
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
                <div class="hero-desc">${pair.refused_reason || 'Pair shape not recognised.'} Check the position on the venue before acting.</div>
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
        const tagClass = isUnattr ? 'unattributed' : (o.status || 'open');

        return `
          <tr>
            <td>
              <strong>${o.side || 'BUY'}</strong>
              <div style="font-size:10px;color:var(--text-muted);">${(o.token_id || '').slice(0, 10)}...</div>
            </td>
            <td>$${price}</td>
            <td>${size}</td>
            <td><strong>${matched}</strong></td>
            <td>${pct}%</td>
            <td>
              <span class="status-tag ${tagClass}">
                ${isUnattr ? '⚠️ UNATTRIBUTED' : o.status}
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
              <div>${f.venue_time_str || formatTime(f.venue_ts)}</div>
              <div style="font-size:10px;color:var(--text-muted);">${formatDuration(f.age_sec)} ago</div>
            </td>
            <td><span style="font-size:11px;color:var(--text-secondary);">${(f.trade_id || '').slice(0, 12)}</span></td>
            <td><strong>${f.side || 'BUY'}</strong></td>
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

        if (state.stale) {
          pollPill.className = 'pill pill-stale';
          pollPill.textContent = `STALE (${Math.round(secSince)}s)`;
          staleBanner.style.display = 'flex';
          staleText.textContent = `⚠️ STALE TELEMETRY: Poll loop has not run in ${Math.round(secSince)}s (>30s limit)! Check supervisor or poll script.`;
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
        lockStatus.innerHTML = `<span style="color:#f59e0b;font-weight:700;">HELD</span> by <code>${lock.holder}</code> (acquired ${formatDuration(lock.age_sec)} ago)`;
      } else {
        lockBox.className = 'lock-box lock-idle';
        lockStatus.textContent = 'Idle (no reconcile pass in flight)';
      }
    }

    // Show the port actually serving this page. Hardcoding the default
    // made two dashboards on different ports indistinguishable.
    document.getElementById('port-pill').textContent =
      ':' + (window.location.port || (window.location.protocol === 'https:' ? '443' : '80'));

    async function pollState() {
      try {
        const res = await fetch('/api/state');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const state = await res.json();
        lastState = state;

        document.getElementById('db-path-display').textContent = `DB: ${state.db_path || 'run/live.db'}`;
        renderHero(state);
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
      // Clock
      document.getElementById('clock-display').textContent = new Date().toLocaleTimeString();

      // Naked timer tick
      if (localNakedSinceMs) {
        const timerEl = document.getElementById('live-naked-timer');
        if (timerEl) {
          const sec = Math.max(0, Math.floor((Date.now() - localNakedSinceMs) / 1000));
          timerEl.textContent = formatDuration(sec);
        }
      }

      // Stale counter tick
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
    """Serve the single-page live execution monitor."""
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
