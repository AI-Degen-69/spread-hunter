"""Tournament hub: one page that lists every running tournament arm and links to its dashboard.

Read-only by design. It reads the launcher's plan record
(`runtime/tournaments/<issue>_<stamp>.json`) and each arm's shadow heartbeat
(`runtime/shadow_run_<run_id>.json`) to show live status, then links straight to
the arm's own dashboard port. It never reads or writes `data/orders.db`, never
places an order, and exposes no control endpoints.

Usage:
    python -m scripts.tournament_hub --port 8798

Defaults to the newest tournament record under `runtime/tournaments/`.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import socket
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core_brain.close_methods import SHADOW_MERGE_NOTE  # noqa: E402
from dashboard.server import read_shadow_run  # noqa: E402

TOURNAMENT_DIR = ROOT / "runtime" / "tournaments"


def _read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def find_latest_record() -> Optional[Path]:
    """Newest tournament plan record, by mtime."""
    if not TOURNAMENT_DIR.is_dir():
        return None
    records = sorted(
        (p for p in TOURNAMENT_DIR.glob("*.json") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return records[0] if records else None


def _resolve_path(raw: str, base: Path) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else (base / path)


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.35) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def _ports_reachable(ports: list[int], timeout: float = 0.15) -> dict[int, bool]:
    if not ports:
        return {}
    results: dict[int, bool] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(16, max(len(ports), 1))) as ex:
        futures = {ex.submit(_port_open, p, "127.0.0.1", timeout): p for p in set(ports)}
        for f in concurrent.futures.as_completed(futures):
            p = futures[f]
            try:
                results[p] = bool(f.result())
            except Exception:
                results[p] = False
    return results


def _arm_counts(db_path: Path, run_id: str) -> dict[str, Any]:
    """Read-only fill/close counts for one arm. Never creates the store."""
    try:
        conn = sqlite3.connect(f"{Path(db_path).resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return {"error": str(exc)}
    try:
        fills = conn.execute(
            "SELECT count(*) FROM fills WHERE run_id = ?", (run_id,)
        ).fetchone()[0]
        orders = conn.execute(
            "SELECT count(DISTINCT order_uuid) FROM fills WHERE run_id = ?", (run_id,)
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT method, realized_pnl FROM closes WHERE run_id = ?", (run_id,)
        ).fetchall()
    except sqlite3.Error as exc:
        return {"error": str(exc)}
    finally:
        conn.close()

    nontrade = {"", None, "unknown", SHADOW_MERGE_NOTE}
    traded = [r for r in rows if (r[0] or "unknown") not in nontrade]
    merges = [r for r in traded if (r[0] or "") == "shadow_merge"]
    return {
        "fill_events": fills,
        "filled_orders": orders,
        "close_events": len(traded),
        "merges": len(merges),
        "single_leg_exits": len(traded) - len(merges),
        "realized_pnl_usd": sum(float(r[1] or 0.0) for r in traded),
    }


def build_arm_view(plan: dict, arm: dict, reachable_map: Optional[dict[int, bool]] = None) -> dict[str, Any]:
    """One arm's status: preset, stop, port reachability, liveness, counts."""
    env = arm.get("env") or {}
    preset = (
        env.get("BOT_QUOTE_TOURNAMENT_PRESET")
        or arm.get("name")
    )
    db_path = _resolve_path(arm["db_path"], ROOT)
    run_id = arm.get("run_id", "")

    heartbeat = read_shadow_run(str(db_path)) or {}
    now = time.time()
    started = float(heartbeat.get("started_at") or 0.0)
    minutes = float(plan.get("minutes") or 0.0)
    elapsed_min = (now - started) / 60.0 if started else 0.0
    remaining_min = max(0.0, minutes - elapsed_min)
    beat_age = heartbeat.get("heartbeat_age_sec")

    dash_port = arm.get("dash_port")
    if reachable_map is not None and dash_port is not None:
        dashboard_reachable = reachable_map.get(int(dash_port), False)
    elif dash_port is not None:
        dashboard_reachable = _port_open(int(dash_port))
    else:
        dashboard_reachable = False

    view: dict[str, Any] = {
        "index": arm.get("index"),
        "name": arm.get("name"),
        "preset": preset,
        "run_id": run_id,
        "dash_port": dash_port,
        "dashboard_url": f"http://127.0.0.1:{dash_port}/",
        "dashboard_reachable": dashboard_reachable,
        "db_path": str(db_path),
        "db_exists": db_path.exists(),
        "stop_loss_usd_cents": env.get("BOT_RISK_STOP_ADVERSE_DROP_CENTS"),
        "dynamic_offset": env.get("BOT_QUOTE_GATE_DYNAMIC_OFFSET_ON"),
        "offset_min_cents": env.get("BOT_QUOTE_SPREAD_OFFSET_MIN_CENTS"),
        "offset_max_cents": env.get("BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS"),
        "offset_mult": env.get("BOT_QUOTE_SPREAD_OFFSET_MULTIPLIER"),
        "cycle": heartbeat.get("cycle"),
        "finished": bool(heartbeat.get("finished")),
        "shutdown_reason": heartbeat.get("shutdown_reason"),
        "elapsed_min": round(elapsed_min, 1),
        "remaining_min": round(remaining_min, 1),
        "heartbeat_age_sec": round(float(beat_age), 1) if beat_age is not None else None,
        # Catalyst verdicts ride the plan arm when the run has reported them;
        # empty until then (never hardcoded).
        "worst_catalyst": arm.get("worst_catalyst") or "",
        "worst_catalyst_desc": arm.get("worst_catalyst_desc") or "",
        "hypothesized_tweak": arm.get("hypothesized_tweak") or "",
        "tweak_rationale": arm.get("tweak_rationale") or "",
    }

    view["alive"] = bool(heartbeat.get("running"))
    view.update(_arm_counts(db_path, run_id) if db_path.exists() else {})
    return view


def find_all_records() -> list[Path]:
    """Every tournament plan record, newest first by mtime.

    The hub merges them all: a second batch (new issue, new ports) must appear
    beside the first instead of replacing it. Ordered newest-first so the
    newest batch sorts to the top of the merged view.
    """
    if not TOURNAMENT_DIR.is_dir():
        return []
    records = []
    for p in TOURNAMENT_DIR.glob("*.json"):
        if not p.is_file():
            continue
        # Only real plan records: a dict carrying "arms". The arms-definition
        # file (a bare list) lives in the same directory and must be skipped.
        plan = _read_json(p)
        if isinstance(plan, dict) and isinstance(plan.get("arms"), list):
            records.append(p)
    return sorted(records, key=lambda p: p.stat().st_mtime, reverse=True)


def build_hub_state(record_path: Optional[Path] = None) -> dict[str, Any]:
    """Arms from one record, or every record merged when none is pinned.

    With an explicit `record_path` the behaviour is unchanged (single batch).
    With none, all tournament records are merged into one arm list so the hub
    is a single page over every running batch.
    """
    if record_path is not None:
        records = [record_path]
        merged = False
    else:
        records = find_all_records()
        merged = True
    records = [r for r in records if r.exists()]
    if not records:
        return {"available": False, "reason": "no tournament record found"}

    # Pre-probe all distinct dashboard ports concurrently to avoid sequential socket delays
    ports_to_check: list[int] = []
    for record in records:
        plan = _read_json(record)
        if isinstance(plan, dict):
            for arm in plan.get("arms", []):
                if isinstance(arm, dict) and arm.get("dash_port") is not None:
                    try:
                        ports_to_check.append(int(arm["dash_port"]))
                    except (ValueError, TypeError):
                        pass
    reachable_map = _ports_reachable(ports_to_check)

    arms: list[dict[str, Any]] = []
    seen_arms: set[tuple[Any, Any]] = set()
    for record in records:
        plan = _read_json(record)
        if not isinstance(plan, dict):
            continue
        for arm in plan.get("arms", []):
            if not isinstance(arm, dict):
                continue
            # Deduplicate by issue and arm name (run identity), so separate batches
            # sharing ports don't clobber each other while superseded runs of the
            # same arm in the same issue keep the newest sighting.
            arm_key = (plan.get("issue"), arm.get("name"))
            if arm_key in seen_arms:
                continue
            view = build_arm_view(plan, arm, reachable_map=reachable_map)
            view["issue"] = plan.get("issue")
            view["stamp"] = plan.get("stamp")
            seen_arms.add(arm_key)
            arms.append(view)

    return {
        "available": True,
        "merged": merged,
        "record_path": str(records[0]),
        "record_paths": [str(r) for r in records],
        "issue": _read_json(records[0]).get("issue") if records else None,
        "stamp": _read_json(records[0]).get("stamp") if records else None,
        "minutes": _read_json(records[0]).get("minutes") if records else None,
        "interval": _read_json(records[0]).get("interval") if records else None,
        "results_path": str(_resolve_path(
            _read_json(records[0])["results_path"], ROOT))
        if (_read_json(records[0]) or {}).get("results_path") else None,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "arms": arms,
    }


PAGE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tournament Hub</title>
<style>
  :root {
    /* Standalone page: these colors must guarantee contrast on their own,
       because the hub may be opened on a light or dark host. The theme vars
       are only a fallback hint — the card/body backgrounds are set explicitly
       below so pale text never lands on a pale background. */
    --fg: #e9edf3;
    --muted: #9aa4b2;
    --accent: #6ea8fe;
    --border: #2b3240;
    --card: #161b24;
    --ok: #4ec9a5;
    --warn: #e0a458;
    --bad: #e06c75;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 16px;
    background: #0d1117; color: var(--fg);
    font: 14px/1.45 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  }
  h1 { font-size: 18px; margin: 0 0 4px; letter-spacing: .2px; }
  .sub { color: var(--muted); font-size: 12px; margin-bottom: 16px; }
  .sub code { color: var(--fg); }
  .grid { display: grid; gap: 12px; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); }
  .card {
    background: var(--card); border: 1px solid var(--border); border-radius: 10px;
    padding: 12px 14px; display: flex; flex-direction: column; gap: 8px;
  }
  .top { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
  .name { font-weight: 650; font-size: 15px; text-transform: capitalize; color: var(--fg); }
  .pill {
    font-size: 11px; padding: 2px 8px; border-radius: 999px;
    border: 1px solid var(--border); color: var(--muted); white-space: nowrap;
  }
  .pill.live { color: var(--ok); border-color: color-mix(in srgb, var(--ok) 45%, transparent); }
  .pill.dead { color: var(--bad); border-color: color-mix(in srgb, var(--bad) 45%, transparent); }
  .pill.done { color: var(--warn); border-color: color-mix(in srgb, var(--warn) 45%, transparent); }
  .rows { display: grid; grid-template-columns: auto 1fr; gap: 2px 10px; font-size: 12.5px; }
  .rows dt { color: var(--muted); }
  .rows dd { margin: 0; text-align: right; font-variant-numeric: tabular-nums; color: var(--fg); }
  .open {
    display: block; text-align: center; text-decoration: none; padding: 7px 10px;
    border-radius: 8px; border: 1px solid var(--border); color: var(--fg); font-size: 13px;
    background: #1d2531;
  }
  .open:hover { border-color: var(--accent); color: var(--accent); }
  .open.disabled { opacity: .5; pointer-events: none; }
  .stop { color: var(--accent); font-weight: 600; }
  .empty { color: var(--muted); padding: 24px; text-align: center; }
</style>
</head>
<body>
<h1>🏆 Tournament Hub</h1>
<div class="sub" id="sub">loading…</div>
<div style="margin-bottom: 16px;">
  <a href="http://127.0.0.1:8800" target="_blank" style="display: inline-flex; align-items: center; gap: 8px; background: #162438; border: 1px solid #2b568e; color: #6ea8fe; padding: 8px 16px; border-radius: 8px; text-decoration: none; font-weight: 600; font-size: 13px;">
    🚀 Open Advanced Analytics &amp; Decision Cockpit (Port 8800)
  </a>
</div>
<div id="arms" class="grid"><div class="empty">loading…</div></div>

<script>
function fmt(n, digits = 2) {
  if (n === null || n === undefined) return "—";
  return Number(n).toFixed(digits);
}

function statusPill(a) {
  if (a.finished) return '<span class="pill done">finished</span>';
  if (a.alive) return '<span class="pill live">● live</span>';
  return '<span class="pill dead">stopped</span>';
}

function esc(s) {
  if (s === null || s === undefined) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function rows(a) {
  const off = a.dynamic_offset === "1"
    ? `${esc(a.offset_min_cents ?? "?")}–${esc(a.offset_max_cents ?? "?")}¢ (×${esc(a.offset_mult ?? "?")})`
    : "static";
  return `
    <dt>stop</dt><dd class="stop">$${esc(a.stop_loss_usd ?? "?")}</dd>
    <dt>offset</dt><dd>${off}</dd>
    <dt>run id</dt><dd title="${esc(a.run_id ?? "")}">${esc(a.run_id ?? "—")}</dd>
    <dt>cycle</dt><dd>${esc(a.cycle ?? "—")}</dd>
    <dt>left</dt><dd>${fmt(a.remaining_min, 0)} min</dd>
    <dt>fills</dt><dd>${a.fill_events ?? 0} / ${a.filled_orders ?? 0} ord</dd>
    <dt>closes</dt><dd>${a.close_events ?? 0} (⇄${a.merges ?? 0} ✕${a.single_leg_exits ?? 0})</dd>
    <dt>pnl</dt><dd>${a.realized_pnl_usd === undefined ? "—" : "$" + fmt(a.realized_pnl_usd)}</dd>
    ${a.worst_catalyst ? `<dt>catalyst</dt><dd title="${esc(a.worst_catalyst_desc || "")}">🚨 ${esc(a.worst_catalyst)}</dd>` : ""}
    ${a.hypothesized_tweak ? `<dt>tweak</dt><dd title="${esc(a.tweak_rationale || "")}">💡 ${esc(a.hypothesized_tweak)}</dd>` : ""}`;
}

function render(state) {
  const wrap = document.getElementById("arms");
  if (!state.available) {
    wrap.innerHTML = `<div class="empty">no tournament record found</div>`;
    document.getElementById("sub").textContent = state.reason || "";
    return;
  }
  const batches = state.merged
    ? `${state.arms.length} arms across ${new Set(state.arms.map(a => a.issue)).size} batches`
    : `issue <code>#${state.issue}</code> · stamp <code>${state.stamp}</code>`;
  document.getElementById("sub").innerHTML =
    `${batches} · ${state.minutes} min · refreshed ${state.generated_at}`;

  // Group by issue (batch) first so both batches stay visually together, then
  // by plan index within each batch (all arms, any count, in plan order).
  const arms = [...state.arms].sort((a, b) => {
    if (a.issue !== b.issue) return (a.issue ?? 0) - (b.issue ?? 0);
    return (a.index ?? 0) - (b.index ?? 0);
  });
  wrap.innerHTML = arms.map(a => `
    <div class="card">
      <div class="top">
        <span class="name">#${esc(a.issue)} · ${esc(a.name)}</span>
        ${statusPill(a)}
      </div>
      <dl class="rows">${rows(a)}</dl>
      <a class="open ${a.dashboard_reachable ? "" : "disabled"}"
         href="${esc(a.dashboard_url)}" target="_blank" rel="noopener">
        ${a.dashboard_reachable ? "Open dashboard :" + esc(a.dash_port) : "dashboard offline :" + esc(a.dash_port)}
      </a>
    </div>`).join("");
}

async function tick() {
  try {
    const r = await fetch("/hub/api/state", { cache: "no-store" });
    render(await r.json());
  } catch (e) {
    document.getElementById("arms").innerHTML =
      `<div class="empty">hub unreachable</div>`;
  }
}
tick();
setInterval(tick, 5000);
</script>
</body>
</html>
"""


def build_app():
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, JSONResponse

    hub = FastAPI(title="spread-hunter tournament hub")

    @hub.get("/", response_class=HTMLResponse)
    def index():
        return HTMLResponse(
            PAGE_HTML,
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    @hub.get("/hub/api/state")
    def state(record: str | None = None):
        return JSONResponse(build_hub_state(Path(record) if record else None))

    @hub.get("/hub/api/health")
    def health():
        return JSONResponse({"ok": True, "service": "hub"})

    return hub


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Spread Hunter Tournament Hub")
    parser.add_argument("--port", type=int, default=8798, help="Port (default: 8798)")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host (default: 127.0.0.1)")
    parser.add_argument("--record", type=str, default=None, help="Explicit tournament record JSON")
    args = parser.parse_args(argv)

    state = build_hub_state(Path(args.record) if args.record else None)
    if not state.get("available"):
        print(f"warning: {state.get('reason')} — the page will stay empty until a run exists")

    import uvicorn

    print(f"Tournament hub on http://{args.host}:{args.port}")
    uvicorn.run(build_app(), host=args.host, port=args.port,
                timeout_graceful_shutdown=3)
    return 0


if __name__ == "__main__":
    sys.exit(main())
