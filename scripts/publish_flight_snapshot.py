"""Flight Snapshot Publisher for Spread Hunter.

Gathers telemetry from all 3 active shadow runs (:8801, :8802, :8803),
generates a lightweight, mobile-optimized HTML dashboard,
and deploys it to Vercel for in-flight monitoring.
"""
from __future__ import annotations

import datetime
import html as html_lib
import json
import math
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("flight_monitor")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEPLOY_DIR = PROJECT_ROOT / "deploy" / "flight_monitor"
HISTORY_FILE = DEPLOY_DIR / "snapshots_history.json"
MAX_SNAPSHOT_AGE_SEC = 3 * 60 * 60


def _load_history() -> list[dict]:
    try:
        history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return history if isinstance(history, list) else []


def _append_history(history: list[dict], snapshot: dict) -> list[dict]:
    history.append(snapshot)
    return history[-50:]


_PUBLIC_RUN_FIELDS = {
    "id", "name", "desc", "port", "running", "status_available",
    "metrics_available", "errors", "elapsed_hours", "target_hours",
    "heartbeat_age_sec", "realized_pnl", "total_with_rebate", "roi_pct",
    "fills", "quotes", "fill_rate_pct", "settlements", "wins", "losses",
    "win_rate_pct", "bankroll", "cost", "active_markets", "avg_edge_cents",
    "median_pair_cost",
}
_PUBLIC_SNAPSHOT_FIELDS = {
    "timestamp", "timestamp_str", "stale_after_sec", "runs", "supervisor_health",
}
_PUBLIC_CHILD_FIELDS = {"running", "exit_code", "restart_failures"}
_PUBLIC_SUPERVISOR_FIELDS = {
    "id", "port", "started", "blocked", "finished", "loop_heartbeat_stale",
    "loop_heartbeat_age_sec", "children",
}


def _prune_public_history(history: list[dict]) -> list[dict]:
    """Allowlist published history fields so local process data cannot leak."""
    sanitized = []
    for snapshot in history:
        if not isinstance(snapshot, dict):
            continue
        clean = {key: value for key, value in snapshot.items() if key in _PUBLIC_SNAPSHOT_FIELDS}
        clean_runs = []
        for run in snapshot.get("runs", []):
            if isinstance(run, dict):
                clean_runs.append({key: value for key, value in run.items() if key in _PUBLIC_RUN_FIELDS})
        clean["runs"] = clean_runs
        if isinstance(snapshot.get("supervisor_health"), list):
            clean_health = []
            for item in snapshot["supervisor_health"]:
                if not isinstance(item, dict):
                    continue
                safe_item = {key: value for key, value in item.items() if key in _PUBLIC_SUPERVISOR_FIELDS}
                children = item.get("children")
                safe_item["children"] = {
                    str(name): {key: value for key, value in child.items() if key in _PUBLIC_CHILD_FIELDS}
                    for name, child in children.items()
                    if isinstance(children, dict) and isinstance(child, dict)
                } if isinstance(children, dict) else {}
                clean_health.append(safe_item)
            clean["supervisor_health"] = clean_health
        sanitized.append(clean)
    return sanitized[-50:]

RUNS_CONFIG = [
    {
        "id": "shadow-01",
        "port": 8801,
        "name": "Shadow-01 (Baseline Pinned)",
        "desc": "Original baseline store (12-09), standard parameters",
        "db": "data/01_shadow_12-09_00-58.db",
        "db_identity": "01_shadow_12-09_00-58.db",
    },
    {
        "id": "shadow-02",
        "port": 8802,
        "name": "Shadow-02 (Exit Regime)",
        "desc": "Active exit-regime model, dynamic thresholding",
        "db": "data/02_shadow_exit-regime_23-09.db",
        "db_identity": "02_shadow_exit-regime_23-09.db",
    },
    {
        "id": "shadow-03",
        "port": 8803,
        "name": "Shadow-03 (Trial $250 Depth)",
        "desc": "Depth-bar trial rehearsal on its own universe feed ($250)",
        "db": "data/03_shadow_24-09_00-04.db",
        "db_identity": "03_shadow_24-09_00-04.db",
    },
]


def fetch_json(url: str, timeout: float = 12.0) -> dict | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "FlightMonitor/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.warning(f"Failed to fetch {url}: {e}")
    return None


def _finite_rounded(value, digits: int) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, digits) if math.isfinite(number) else None


def collect_run_telemetry(cfg: dict) -> dict:
    port = cfg["port"]
    run_id = cfg["id"]
    base_url = f"http://127.0.0.1:{port}"

    sys_data = fetch_json(f"{base_url}/api/system/status")
    kpi_data = fetch_json(f"{base_url}/api/kpi")
    status_available = isinstance(sys_data, dict) and "shadow_run" in sys_data
    metrics_available = (
        isinstance(kpi_data, dict)
        and not kpi_data.get("error")
        and kpi_data.get("realized_pnl") is not None
        and kpi_data.get("total_with_rebate") is not None
    )
    errors = []
    if not status_available:
        errors.append("Dashboard status API unavailable or invalid")
    if not metrics_available:
        errors.append("Dashboard KPI API unavailable or invalid")

    # Do not accept a healthy response from a different dashboard/database;
    # an occupied port must never make one run impersonate another.
    reported_db = None
    db_matches = False
    run_matches = False
    if status_available:
        reported_db = sys_data.get("db_path")
        expected_db = (PROJECT_ROOT / cfg["db"]).resolve()
        try:
            db_matches = (
                isinstance(reported_db, str)
                and Path(reported_db).resolve() == expected_db
            )
        except (OSError, TypeError, ValueError):
            db_matches = False
        run_info = sys_data.get("shadow_run")
        run_matches = isinstance(run_info, dict) and run_info.get("run_id") == run_id
        running_value = run_info.get("running") if isinstance(run_info, dict) else None
        if running_value is None:
            status_available = False
            errors.append("Shadow run heartbeat is unavailable")
        elif not isinstance(running_value, bool):
            status_available = False
            errors.append("Shadow run status is invalid")
        if not db_matches or not run_matches:
            status_available = False
            metrics_available = False
            errors.append("Dashboard is connected to a different run/store")

    # Metrics from a port cannot be attributed unless the status endpoint
    # verifies that port's database and run. An orphaned/random dashboard must
    # never contribute plausible-looking PnL to the published snapshot.
    if not status_available:
        metrics_available = False

    # Invalid status/KPI payloads are represented as unknown, never as a clean
    # stopped run or zero-valued performance.

    # Do not include local process/session records in the public deployment.
    # They can contain PIDs, local paths, and command lines; the dashboard API
    # heartbeat is the authoritative run status needed by this read-only page.
    sh = sys_data.get("shadow_run") if status_available else None
    sh = sh if isinstance(sh, dict) else {}
    is_running = bool(sh.get("running", False)) if status_available else None
    try:
        elapsed_sec = float(sh.get("elapsed_sec")) if status_available else None
        minutes_cfg = float(sh.get("minutes")) if status_available else None
    except (TypeError, ValueError):
        elapsed_sec = minutes_cfg = None
    if status_available and (
        elapsed_sec is None or minutes_cfg is None
        or not math.isfinite(elapsed_sec) or not math.isfinite(minutes_cfg)
    ):
        status_available = False
        is_running = None
        elapsed_sec = minutes_cfg = None
        errors.append("Shadow run timing data is invalid")

    realized_pnl = None
    total_with_rebate = None
    roi = None
    fills = None
    quotes = None
    fill_rate = None
    wins = None
    losses = None
    win_rate = None
    bankroll = None
    cost = None
    settlements_count = None
    active_markets = None
    avg_edge = None
    median_pair_cost = None

    if metrics_available:
        try:
            realized_pnl = float(kpi_data["realized_pnl"])
            total_with_rebate = float(kpi_data["total_with_rebate"])
            roi = float(kpi_data.get("roi_on_cost") or 0.0)
            fills = int(kpi_data.get("fills") or 0)
            quotes = int(kpi_data.get("quotes") or 0)
            fill_rate = float(kpi_data.get("fill_rate") or 0.0)
            wins = int(kpi_data.get("wins") or 0)
            losses = int(kpi_data.get("losses") or 0)
            win_rate = float(kpi_data.get("win_rate") or 0.0)
            bankroll = float(kpi_data.get("bankroll") or 0.0)
            cost = float(kpi_data.get("cost") or 0.0)
            settlements_count = len(kpi_data.get("settlements") or [])
            active_markets = int(kpi_data.get("active_quoting_markets") or 0)
            avg_edge = float(kpi_data.get("avg_edge_cents") or 0.0)
            median_pair_cost = float(kpi_data.get("median_pair_cost") or 0.0)
            metrics_available = all(math.isfinite(value) for value in (
                realized_pnl, total_with_rebate, roi, fill_rate,
                bankroll, cost,
            )) and all(
                value is None or math.isfinite(value)
                for value in (win_rate, avg_edge, median_pair_cost)
            )
        except (TypeError, ValueError, KeyError):
            metrics_available = False
            errors.append("Dashboard KPI payload has invalid metric values")

        if not metrics_available:
            realized_pnl = total_with_rebate = roi = None
            fills = quotes = fill_rate = wins = losses = win_rate = None
            bankroll = cost = settlements_count = active_markets = None
            avg_edge = median_pair_cost = None

    return {
        "id": run_id,
        "name": cfg["name"],
        "desc": cfg["desc"],
        "port": port,
        "running": is_running,
        "status_available": status_available,
        "metrics_available": metrics_available,
        "errors": errors,
        "elapsed_hours": round(elapsed_sec / 3600.0, 2) if elapsed_sec is not None and math.isfinite(elapsed_sec) else None,
        "target_hours": round(minutes_cfg / 60.0, 1) if minutes_cfg is not None and math.isfinite(minutes_cfg) else None,
        "heartbeat_age_sec": _finite_rounded(sh.get("heartbeat_age_sec"), 1)
        if status_available else None,
        "realized_pnl": round(realized_pnl, 2) if metrics_available else None,
        "total_with_rebate": round(total_with_rebate, 2) if metrics_available else None,
        "roi_pct": round(roi * 100.0, 2) if metrics_available else None,
        "fills": fills if metrics_available else None,
        "quotes": quotes if metrics_available else None,
        "fill_rate_pct": round(fill_rate * 100.0, 3) if metrics_available else None,
        "settlements": settlements_count if metrics_available else None,
        "wins": wins if metrics_available else None,
        "losses": losses if metrics_available else None,
        "win_rate_pct": round(win_rate * 100.0, 1) if metrics_available else None,
        "bankroll": round(bankroll, 2) if metrics_available else None,
        "cost": round(cost, 2) if metrics_available else None,
        "active_markets": active_markets if metrics_available else None,
        "avg_edge_cents": round(avg_edge, 2) if metrics_available else None,
        "median_pair_cost": round(median_pair_cost, 4) if metrics_available else None,
    }


def _supervisor_degraded(item: dict) -> bool:
    if item.get("blocked") or item.get("loop_heartbeat_stale") is True:
        return True
    if item.get("started") is not True:
        return False
    child_health = item.get("children")
    child_health = child_health if isinstance(child_health, dict) else {}
    required_children = {"dash", "loop", "observer"}
    if item.get("id") != "shadow-02":
        required_children.add("screener")
    return any(not (child_health.get(name) or {}).get("running") for name in required_children)


def generate_html(snapshot: dict, history: list[dict]) -> str:
    history = _prune_public_history(list(history))
    now_str = snapshot["timestamp_str"]
    latest_age_sec = max(0.0, time.time() - float(snapshot.get("timestamp") or 0.0))
    stale_after_sec = float(snapshot.get("stale_after_sec") or MAX_SNAPSHOT_AGE_SEC)
    snapshot_stale = latest_age_sec > stale_after_sec
    supervisor_health = snapshot.get("supervisor_health") or []
    runs = snapshot["runs"]
    any_supervisor_problem = any(_supervisor_degraded(item) for item in supervisor_health)
    all_running = bool(runs) and all(
        r.get("status_available") and r.get("running") is True for r in runs
    )
    status_known = all(r.get("status_available") for r in runs)
    active_count = sum(1 for r in runs if r.get("running") is True)
    status_badge_class = (
        "bg-rose-500/20 text-rose-300 border-rose-500/50"
        if any_supervisor_problem else (
            "bg-emerald-500/20 text-emerald-400 border-emerald-500/40"
            if all_running else "bg-amber-500/20 text-amber-400 border-amber-500/40"
        )
    )
    if any_supervisor_problem:
        status_badge_text = "SUPERVISOR DEGRADED"
    elif all_running:
        status_badge_text = f"ALL {len(runs)} RUNS ACTIVE"
    elif not status_known:
        status_badge_text = "STATUS DATA INCOMPLETE"
    else:
        status_badge_text = f"{active_count}/{len(runs)} RUNS ACTIVE"

    # Never aggregate partial data or render an API failure as a genuine $0.00.
    all_metrics_available = bool(runs) and all(r.get("metrics_available") for r in runs)
    total_pnl = sum(float(r["total_with_rebate"] or 0.0) for r in runs) if all_metrics_available else None
    total_fills = sum(int(r["fills"] or 0) for r in runs) if all_metrics_available else None
    total_settlements = sum(int(r["settlements"] or 0) for r in runs) if all_metrics_available else None
    pnl_class = "text-emerald-400" if total_pnl is not None and total_pnl >= 0 else "text-slate-300"
    total_pnl_text = f"${total_pnl:+.2f}" if total_pnl is not None else "N/A"
    total_fills_text = str(total_fills) if total_fills is not None else "N/A"
    total_settlements_text = str(total_settlements) if total_settlements is not None else "N/A"
    snapshot_epoch_ms = int(float(snapshot.get("timestamp") or 0) * 1000)
    snapshot_age_minutes = int(latest_age_sec // 60)
    stale_banner_text = (
        f"STALE SNAPSHOT — latest data is about {snapshot_age_minutes} minutes old. "
        "Do not assume any run is still active."
    )
    if not snapshot_stale and any_supervisor_problem:
        stale_banner_text = "SUPERVISOR DEGRADED — a required run component is down or stale."
    warning_visible = snapshot_stale or any_supervisor_problem
    supervisor_rows = []
    for item in supervisor_health:
        label = html_lib.escape(str(item.get("id", "run")))
        child_health = item.get("children") or {}
        required_children = {"dash", "loop", "observer"}
        if item.get("id") != "shadow-02":
            required_children.add("screener")
        if item.get("started") and any(
            child.get("running") is False
            for name, child in child_health.items()
            if name in required_children
        ):
            degraded = True
        else:
            degraded = False
        degraded = degraded or item.get("loop_heartbeat_stale") is True
        state = "BLOCKED" if item.get("blocked") else (
            "FINISHED" if item.get("finished") else (
                "DEGRADED" if degraded else ("SUPERVISING" if item.get("started") else "STARTING")
            )
        )
        children = " • ".join(
            f"{html_lib.escape(str(name))}: {'up' if child.get('running') else 'down'}"
            + (f" (restarts {int(child.get('restart_failures') or 0)})"
               if child.get("restart_failures") else "")
            + (f" (exit {int(child['exit_code'])})"
               if not child.get("running") and child.get("exit_code") is not None else "")
            for name, child in child_health.items()
        )
        hb_age = item.get("loop_heartbeat_age_sec")
        heartbeat = f"{float(hb_age):.0f}s old" if hb_age is not None else "not seen yet"
        supervisor_rows.append(
            f'<div class="border-t border-slate-800 py-3 text-sm">'
            f'<div class="font-semibold text-slate-100">{label} • {state}</div>'
            f'<div class="mt-1 text-xs text-slate-400">{children}</div>'
            f'<div class="mt-1 text-xs text-slate-500">Loop heartbeat: {heartbeat}</div>'
            f'</div>'
        )
    supervisor_html = "".join(supervisor_rows) or (
        '<p class="text-xs text-amber-300">Supervisor health unavailable in this snapshot.</p>'
    )

    run_cards_html = ""
    for r in runs:
        metrics_available = bool(r.get("metrics_available"))
        status_available = bool(r.get("status_available"))
        r_pnl = float(r.get("total_with_rebate") or 0.0) if metrics_available else 0.0
        r_pnl_color = "text-emerald-400" if r_pnl >= 0 else "text-rose-400"
        r_pnl_text = f"${r_pnl:+.2f}" if metrics_available else "N/A"
        roi_text = f"ROI: {float(r['roi_pct']):+.2f}%" if metrics_available else "ROI: N/A"
        r_status = r.get("running")
        r_status_color = "bg-emerald-500" if r_status is True else (
            "bg-rose-500" if r_status is False and status_available else "bg-amber-500"
        )
        r_status_text = "RUNNING" if r_status is True else (
            "STOPPED" if r_status is False and status_available else "UNKNOWN"
        )
        if r.get("elapsed_hours") is not None and r.get("target_hours"):
            progress_pct = min(100.0, round((r["elapsed_hours"] / max(0.1, r["target_hours"])) * 100.0, 1))
            elapsed_text = f"{r['elapsed_hours']}h / {r['target_hours']}h ({progress_pct}%)"
        else:
            progress_pct = 0.0
            elapsed_text = "N/A — status unavailable"
        settlements_text = str(r.get("settlements")) if metrics_available else "N/A"
        win_rate_text = (
            f"{r['win_rate_pct']}% ({r['wins']}W / {r['losses']}L)"
            if metrics_available else "N/A"
        )
        fills_quotes_text = f"{r['fills']} / {r['quotes']}" if metrics_available else "N/A"
        fill_rate_text = f"{r['fill_rate_pct']}%" if metrics_available else "N/A"
        active_markets_text = str(r.get("active_markets")) if metrics_available else "N/A"
        avg_edge_text = f"{r['avg_edge_cents']}¢" if metrics_available else "N/A"
        median_pair_cost_text = f"${r['median_pair_cost']}" if metrics_available else "N/A"
        heartbeat_text = f"{r['heartbeat_age_sec']}s ago" if r.get("heartbeat_age_sec") is not None else "N/A"
        error_text = html_lib.escape("; ".join(r.get("errors") or []))

        run_cards_html += f"""
        <div class="bg-slate-800/80 border border-slate-700/80 rounded-2xl p-6 shadow-xl relative overflow-hidden backdrop-blur-sm">
          <div class="flex items-center justify-between mb-4">
            <div>
              <div class="flex items-center gap-2">
                <span class="w-2.5 h-2.5 rounded-full {r_status_color} animate-pulse"></span>
                <span class="font-mono text-xs uppercase tracking-wider text-slate-400">{r["id"]} • Port {r["port"]}</span>
              </div>
              <h3 class="text-xl font-bold text-white mt-1">{r["name"]}</h3>
              <p class="text-xs text-slate-400 mt-0.5">{r["desc"]}</p>
            </div>
            <div class="text-right">
              <span class="px-2.5 py-1 rounded-full text-xs font-semibold uppercase tracking-wider {r_status_color}/20 text-slate-200 border {('border-emerald-500/40' if r_status is True else ('border-rose-500/40' if r_status is False and status_available else 'border-amber-500/40'))}">
                {r_status_text}
              </span>
            </div>
          </div>

          <div class="grid grid-cols-2 gap-4 my-5 bg-slate-900/60 p-4 rounded-xl border border-slate-800">
            <div>
              <div class="text-xs text-slate-400">Total Net PnL</div>
              <div class="text-2xl font-mono font-bold {r_pnl_color}">{r_pnl_text}</div>
              <div class="text-[11px] text-slate-500">{roi_text}</div>
            </div>
            <div>
              <div class="text-xs text-slate-400">Settled Pairs</div>
              <div class="text-2xl font-mono font-bold text-white">{settlements_text}</div>
              <div class="text-[11px] text-slate-500">Win Rate: {win_rate_text}</div>
            </div>
          </div>

          <div class="space-y-2.5 text-sm">
            <div class="flex justify-between items-center text-xs">
              <span class="text-slate-400">Elapsed / Timebox:</span>
              <span class="font-mono text-slate-200">{elapsed_text}</span>
            </div>
            <div class="w-full bg-slate-700/50 rounded-full h-1.5 overflow-hidden">
              <div class="bg-indigo-500 h-1.5 rounded-full transition-all duration-500" style="width: {progress_pct}%"></div>
            </div>

            <div class="pt-2 grid grid-cols-3 gap-2 text-center text-xs">
              <div class="bg-slate-900/40 p-2 rounded-lg">
                <div class="text-slate-500">Fills / Quotes</div>
                <div class="font-mono font-medium text-slate-300 mt-0.5">{fills_quotes_text}</div>
              </div>
              <div class="bg-slate-900/40 p-2 rounded-lg">
                <div class="text-slate-500">Fill Rate</div>
                <div class="font-mono font-medium text-slate-300 mt-0.5">{fill_rate_text}</div>
              </div>
              <div class="bg-slate-900/40 p-2 rounded-lg">
                <div class="text-slate-500">Active Mkts</div>
                <div class="font-mono font-medium text-slate-300 mt-0.5">{active_markets_text}</div>
              </div>
            </div>

            <div class="flex justify-between items-center text-[11px] text-slate-500 pt-1">
              <span>Avg Edge: <span class="font-mono text-slate-400">{avg_edge_text}</span></span>
              <span>Median Pair Cost: <span class="font-mono text-slate-400">{median_pair_cost_text}</span></span>
              <span>Heartbeat: <span class="font-mono text-slate-400">{heartbeat_text}</span></span>
            </div>
          </div>
          {f'<p class="mt-3 rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-xs text-rose-300">Telemetry unavailable: {error_text}</p>' if error_text else ''}
        </div>
        """

    # Timeline rows
    history_rows = ""
    for item in reversed(history[-10:]):
        ts = item.get("timestamp_str", "")
        h_runs = item.get("runs", [])
        history_metrics_available = bool(h_runs) and all(
            h.get("metrics_available") and h.get("total_with_rebate") is not None
            for h in h_runs
        )
        pnl_sum = sum(float(h["total_with_rebate"]) for h in h_runs) if history_metrics_available else None
        p_color = "text-emerald-400" if pnl_sum is not None and pnl_sum >= 0 else "text-slate-400"
        pnl_text = f"${pnl_sum:+.2f}" if pnl_sum is not None else "N/A"
        settlements_text = (
            f"{sum(int(h['settlements'] or 0) for h in h_runs)} pairs"
            if history_metrics_available else "N/A"
        )
        active_count = sum(1 for h in h_runs if h.get("running") is True)

        history_rows += f"""
        <tr class="border-b border-slate-800 text-xs hover:bg-slate-800/40 transition">
          <td class="py-2.5 font-mono text-slate-300">{ts}</td>
          <td class="py-2.5 text-slate-400">{active_count}/3 Active</td>
          <td class="py-2.5 font-mono {p_color} font-medium">{pnl_text}</td>
          <td class="py-2.5 text-right font-mono text-slate-400">{settlements_text}</td>
        </tr>
        """

    html = f"""<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
  <meta http-equiv="Pragma" content="no-cache">
  <meta http-equiv="Expires" content="0">
  <meta http-equiv="refresh" content="300">
  <title>Spread Hunter — Flight Monitor</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <style>
    body {{ font-family: 'Inter', sans-serif; }}
    code, pre, .font-mono {{ font-family: 'JetBrains Mono', monospace; }}
  </style>
</head>
<body class="bg-slate-950 text-slate-100 min-h-screen antialiased selection:bg-indigo-500 selection:text-white">
  <div class="max-w-6xl mx-auto px-4 py-8 sm:px-6 lg:px-8">
    <div id="stale-warning" role="alert" class="{'mb-5' if warning_visible else 'hidden mb-5'} rounded-xl border border-rose-500/60 bg-rose-950 px-4 py-3 text-sm font-semibold text-rose-200">
      {html_lib.escape(stale_banner_text)}
    </div>
    
    <!-- Top Header -->
    <header class="flex flex-col md:flex-row md:items-center md:justify-between pb-8 border-b border-slate-800 gap-4">
      <div>
        <div class="flex items-center gap-2">
          <span class="text-2xl">✈️</span>
          <h1 class="text-2xl sm:text-3xl font-extrabold tracking-tight text-white">Spread Hunter Flight Monitor</h1>
          <span id="snapshot-status-badge" class="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium border {status_badge_class}">
            {status_badge_text}
          </span>
        </div>
        <p class="text-sm text-slate-400 mt-1">Read-only shadow-run checkup. Snapshot freshness is monitored on this page.</p>
      </div>

      <div class="flex items-center gap-3">
        <div class="bg-slate-900 border border-slate-800 rounded-xl px-4 py-2 text-right">
          <div class="text-[11px] uppercase tracking-wider text-slate-500">Last Snapshot</div>
          <div class="font-mono text-sm font-semibold text-slate-200">{now_str}</div>
        </div>
        <button onclick="window.location.reload()" class="bg-indigo-600 hover:bg-indigo-500 text-white px-3 py-2 rounded-xl text-xs font-medium transition shadow-lg shadow-indigo-600/20">
          ↻ Refresh
        </button>
      </div>
    </header>

    <!-- Global Key Metric Summary -->
    <section class="grid grid-cols-2 sm:grid-cols-4 gap-4 my-8">
      <div class="bg-slate-900/80 border border-slate-800 p-5 rounded-2xl">
        <div class="text-xs text-slate-400 uppercase tracking-wider">Combined Net PnL</div>
        <div class="text-2xl sm:text-3xl font-mono font-bold mt-1 {pnl_class}">{total_pnl_text}</div>

        <div class="text-xs text-slate-500 mt-1">Across {len(runs)} configured shadow runs</div>
      </div>
      <div class="bg-slate-900/80 border border-slate-800 p-5 rounded-2xl">
        <div class="text-xs text-slate-400 uppercase tracking-wider">Total Settlements</div>
        <div class="text-2xl sm:text-3xl font-mono font-bold mt-1 text-white">{total_settlements_text}</div>
        <div class="text-xs text-slate-500 mt-1">Fully merged pairs</div>
      </div>
      <div class="bg-slate-900/80 border border-slate-800 p-5 rounded-2xl">
        <div class="text-xs text-slate-400 uppercase tracking-wider">Total Fills</div>
        <div class="text-2xl sm:text-3xl font-mono font-bold mt-1 text-white">{total_fills_text}</div>
        <div class="text-xs text-slate-500 mt-1">Legs filled across runs</div>
      </div>
      <div class="bg-slate-900/80 border border-slate-800 p-5 rounded-2xl">
        <div class="text-xs text-slate-400 uppercase tracking-wider">Data Health</div>
        <div class="text-2xl sm:text-3xl font-mono font-bold mt-1 text-indigo-400">{sum(1 for r in runs if r.get('status_available') and r.get('metrics_available'))}/{len(runs)} OK</div>
        <div class="text-xs text-slate-500 mt-1">Status + KPI endpoints reachable</div>
      </div>
    </section>

    <!-- Supervisor health -->
    <section class="bg-slate-900/60 border border-slate-800 rounded-2xl p-5 mb-8">
      <h2 class="text-lg font-bold text-slate-200 mb-3">This System — Supervisor Health</h2>
      {supervisor_html}
    </section>

    <!-- 3 Active Runs -->
    <section class="space-y-4 mb-10">
      <div class="flex items-center justify-between">
        <h2 class="text-lg font-bold text-slate-200">Active Rehearsal Instances</h2>
        <span class="text-xs text-slate-500 font-mono">Expected update every 2h • stale warning after 3h</span>
      </div>
      <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
        {run_cards_html}
      </div>
    </section>

    <!-- Side-by-Side Comparison Table -->
    <section class="bg-slate-900/60 border border-slate-800 rounded-2xl p-6 mb-10">
      <h3 class="text-base font-bold text-white mb-4">Direct Run Comparison</h3>
      <div class="overflow-x-auto">
        <table class="w-full text-left text-xs">
          <thead>
            <tr class="border-b border-slate-800 text-slate-400 uppercase">
              <th class="pb-3 font-semibold">Instance</th>
              <th class="pb-3 font-semibold">Status</th>
              <th class="pb-3 font-semibold">Elapsed</th>
              <th class="pb-3 font-semibold">Net PnL</th>
              <th class="pb-3 font-semibold">Pairs</th>
              <th class="pb-3 font-semibold">Win Rate</th>
              <th class="pb-3 font-semibold">Fill Rate</th>
              <th class="pb-3 font-semibold">Bankroll</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-slate-800/60">
            {"".join(f'''
            <tr class="hover:bg-slate-800/30">
              <td class="py-3 font-medium text-slate-200">{r["name"]}</td>
              <td class="py-3"><span class="px-2 py-0.5 rounded text-[10px] font-semibold {'bg-emerald-500/20 text-emerald-400' if r.get('running') is True else ('bg-rose-500/20 text-rose-400' if r.get('status_available') else 'bg-amber-500/20 text-amber-300')}">{'ACTIVE' if r.get('running') is True else ('STOPPED' if r.get('status_available') else 'UNKNOWN')}</span></td>
              <td class="py-3 font-mono text-slate-300">{f'{r["elapsed_hours"]}h / {r["target_hours"]}h' if r.get('elapsed_hours') is not None else 'N/A'}</td>
              <td class="py-3 font-mono font-bold">{f'${float(r["total_with_rebate"] or 0.0):+.2f}' if r.get('metrics_available') else 'N/A'}</td>
              <td class="py-3 font-mono text-slate-200">{r.get("settlements") if r.get('metrics_available') else 'N/A'}</td>
              <td class="py-3 font-mono text-slate-200">{f'{r["win_rate_pct"]}%' if r.get('metrics_available') else 'N/A'}</td>
              <td class="py-3 font-mono text-slate-200">{f'{r["fill_rate_pct"]}%' if r.get('metrics_available') else 'N/A'}</td>
              <td class="py-3 font-mono text-slate-400">{f'${float(r["bankroll"] or 0.0):.2f}' if r.get('metrics_available') else 'N/A'}</td>
            </tr>''' for r in runs)}
          </tbody>
        </table>
      </div>
    </section>

    <!-- History Log -->
    <section class="bg-slate-900/60 border border-slate-800 rounded-2xl p-6 mb-8">
      <h3 class="text-base font-bold text-white mb-2">Snapshot History Timeline</h3>
      <p class="text-xs text-slate-400 mb-4">Historical record of updates during the flight</p>
      <div class="overflow-x-auto">
        <table class="w-full text-left">
          <thead>
            <tr class="border-b border-slate-800 text-xs text-slate-500 uppercase">
              <th class="pb-2">Timestamp (Local)</th>
              <th class="pb-2">State</th>
              <th class="pb-2">Combined PnL</th>
              <th class="pb-2 text-right">Settled Pairs</th>
            </tr>
          </thead>
          <tbody>
            {history_rows if history_rows else '<tr><td colspan="4" class="py-4 text-center text-xs text-slate-500">First snapshot registered. Next history entries will appear here.</td></tr>'}
          </tbody>
        </table>
      </div>
    </section>

    <!-- Footer -->
    <footer class="pt-6 border-t border-slate-800/80 text-center text-xs text-slate-500 flex flex-col sm:flex-row justify-between items-center gap-2">
      <div>Spread Hunter Execution Engine • Read-only shadow telemetry</div>
      <div>Page auto-refreshes every 5 minutes. Snapshot timestamp: {now_str}</div>
    </footer>

  </div>
  <script>
    const snapshotTimeMs = {snapshot_epoch_ms};
    const staleAfterMs = {int(stale_after_sec * 1000)};
    const supervisorDegraded = {str(any_supervisor_problem).lower()};
    function updateSnapshotFreshness() {{
      const ageMs = Date.now() - snapshotTimeMs;
      const warning = document.getElementById('stale-warning');
      if (ageMs > staleAfterMs || supervisorDegraded) {{
        warning.classList.remove('hidden');
        const badge = document.getElementById('snapshot-status-badge');
        if (ageMs > staleAfterMs) {{
          warning.textContent = `STALE SNAPSHOT — latest data is about ${{Math.floor(ageMs / 60000)}} minutes old. Do not assume any run is still active.`;
          badge.textContent = 'SNAPSHOT STALE';
        }} else {{
          warning.textContent = 'SUPERVISOR DEGRADED — a required run component is down or stale.';
          badge.textContent = 'SUPERVISOR DEGRADED';
        }}
        badge.className = 'inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium border bg-rose-500/20 text-rose-300 border-rose-500/50';
      }}
    }}
    updateSnapshotFreshness();
    window.setInterval(updateSnapshotFreshness, 60000);
    window.setInterval(() => window.location.reload(), 300000);
  </script>
</body>
</html>"""
    return html


def take_snapshot_and_render(
    *, supervisor_health: list[dict] | None = None,
) -> dict:
    DEPLOY_DIR.mkdir(parents=True, exist_ok=True)

    now = datetime.datetime.now()
    now_str = now.strftime("%d/%m/%Y %H:%M:%S")

    runs_data = []
    for cfg in RUNS_CONFIG:
        logger.info("Gathering telemetry for %s (:%s)...", cfg["id"], cfg["port"])
        runs_data.append(collect_run_telemetry(cfg))

    snapshot = {
        "timestamp": now.timestamp(),
        "timestamp_str": now_str,
        "runs": runs_data,
    }

    snapshot["stale_after_sec"] = MAX_SNAPSHOT_AGE_SEC
    snapshot["supervisor_health"] = supervisor_health or []

    # Retain only a compact public history and strip legacy local session data.
    history = _prune_public_history(_append_history(_load_history(), snapshot))
    _atomic_write(HISTORY_FILE, json.dumps(history, indent=2))

    # Render HTML
    html_content = generate_html(snapshot, history)
    index_path = DEPLOY_DIR / "index.html"
    _atomic_write(index_path, html_content)

    logger.info(f"Generated {index_path} ({len(html_content)} bytes)")
    return snapshot


def _atomic_write(path: Path, content: str) -> None:
    """Replace a generated artifact atomically so interruption cannot truncate it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temp_path = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def deploy_to_vercel() -> str:
    """Deploy the rendered monitor; treat every ambiguous result as a failure."""
    logger.info("Deploying to Vercel via CLI...")
    vercel_bin = shutil.which("vercel") or shutil.which("vercel.cmd")
    if not vercel_bin:
        raise FileNotFoundError("Vercel CLI is not installed or not on PATH")
    cmd = [vercel_bin, "deploy", str(DEPLOY_DIR), "--prod", "--yes"]
    is_windows_script = os.name == "nt" and Path(vercel_bin).suffix.lower() in {".cmd", ".bat"}
    if is_windows_script:
        cmd = ["cmd.exe", "/d", "/s", "/c", subprocess.list2cmdline(cmd)]
    res = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=120,
        shell=is_windows_script,
        cwd=str(PROJECT_ROOT),
        check=False,
    )
    output = (res.stdout + "\n" + res.stderr).strip()
    logger.info("Vercel output:\n%s", output)
    if res.returncode != 0:
        raise RuntimeError(f"Vercel CLI exited {res.returncode}: {output[-2000:]}")
    for word in output.split():
        candidate = word.strip("<>()[]{}.,;'")
        if candidate.startswith("https://") and "vercel.app" in candidate:
            return candidate
    raise RuntimeError("Vercel CLI succeeded but did not report a deployment URL")


def main():
    logger.info("Taking snapshot...")
    snapshot = take_snapshot_and_render()
    logger.info("Collected: %d runs.", len(snapshot["runs"]))
    url = deploy_to_vercel()
    print("\n==========================================")
    print(f"[FLIGHT MONITOR LIVE] {url}")
    print("==========================================\n")


if __name__ == "__main__":
    main()
