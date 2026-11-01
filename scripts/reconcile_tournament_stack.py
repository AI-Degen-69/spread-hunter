"""Tournament Stack Reconciler & Self-Healing Alignment.

Idempotent desired-state reconciler for Spread Hunter tournaments:
1. Optionally sweeps orders for a market the operator names (--sweep-slug) from
   the arm databases. Nothing is deleted without that flag.
2. Ensures the Market Filter loop is running and publishing fresh snapshots.
3. Ensures Tournament Cockpit (:8800) and Tournament Hub (:8798) are alive.
4. Checks all 10 arm dashboards (:8801-:8810), restarting any that are down or
   running stale code in memory so they reflect the latest code on disk.
5. Checks all 10 shadow run arms, restarting any that are down or running stale
   code with the target max-markets (default: 3) and 24-hour time box.
6. Verifies green telemetry across all 10 arms and outputs an actionable summary.

Usage:
    python -m scripts.reconcile_tournament_stack [--tourney 799] [--max-markets 3] [--minutes 1440]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Sequence
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core_brain.code_revision import code_predates_tree, decision_code_mtime
from core_brain.shadow_run import latest_tournament_record

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("reconcile_tournament")


def _detached_kwargs() -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "cwd": str(ROOT),
    }
    if sys.platform == "win32":
        # CREATE_NO_WINDOW (0x08000000) guarantees no visible console window or Windows Terminal tab is popped up.
        # CREATE_NEW_PROCESS_GROUP (0x00000200) allows independent process lifecycle.
        # Do NOT use DETACHED_PROCESS (0x00000008) because Windows Terminal intercepts it and creates visible windows.
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        kwargs["creationflags"] = flags
    else:
        kwargs["start_new_session"] = True
    return kwargs


def is_port_listening(port: int, host: str = "127.0.0.1", timeout: float = 0.3) -> bool:
    """Check if a TCP port is currently accepting connections."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def _is_pid_alive(pid: int) -> bool:
    """Check if process with pid is currently running."""
    if not pid or pid <= 0:
        return False
    try:
        if sys.platform == "win32":
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                                 capture_output=True, text=True, check=False, timeout=30).stdout
            return str(pid) in out
        else:
            os.kill(pid, 0)
            return True
    except subprocess.TimeoutExpired:
        log.warning("tasklist timed out for PID %s; treating as unknown", pid)
        return False
    except Exception:
        return False


def _pid_commandline(pid: int) -> str:
    """Best-effort command line for PID, '' when unknown. Never raises."""
    try:
        if sys.platform == "win32":
            out = subprocess.run(
                ["wmic", "process", "where", f"ProcessId={pid}", "get", "CommandLine", "/value"],
                capture_output=True, text=True, check=False, timeout=15,
            ).stdout or ""
            return out.strip()
        else:
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                return fh.read().replace(b"\x00", b" ").decode("utf-8", "replace")
    except Exception:
        return ""


def kill_pid(pid: int) -> bool:
    """Terminate a process by PID."""
    if not pid or pid <= 0:
        return False
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, text=True, check=False, timeout=30)
        else:
            os.kill(pid, 9)
        time.sleep(0.3)
        return True
    except Exception as exc:
        log.warning("Could not kill PID %d: %s", pid, exc)
        return False


def kill_process_on_port(port: int) -> bool:
    """Find and kill whichever process is listening on a given port."""
    if sys.platform != "win32":
        return False
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "tcp"],
                             capture_output=True, text=True, check=False, timeout=30).stdout
        for line in out.splitlines():
            line = line.strip()
            if f":{port} " in line and "LISTENING" in line:
                parts = line.split()
                pid = int(parts[-1])
                log.info("Killing process on port %d (PID %d)...", port, pid)
                kill_pid(pid)
                time.sleep(0.5)
                return True
    except subprocess.TimeoutExpired:
        log.warning("netstat timed out looking for port %d; leaving it alone", port)
    except Exception as exc:
        log.warning("Error finding process on port %d: %s", port, exc)
    return False


def sweep_stale_orders(db_path: Path, slugs: Sequence[str]) -> int:
    """Purge orders for the market slugs the operator named, plus their fills and closes.

    Opt-in on purpose: this deletes rows from a live arm store, so the only
    caller that supplies slugs is the operator's --sweep-slug flag. No market
    is hardcoded here; a delisted market is a decision made at run time.
    """
    if not slugs or not db_path.exists():
        return 0
    removed = 0
    try:
        con = sqlite3.connect(str(db_path), timeout=5.0)
        cur = con.cursor()
        
        tables = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "orders" not in tables:
            con.close()
            return 0

        cols = {r[1] for r in cur.execute("PRAGMA table_info(orders)").fetchall()}
        fills_cols = {r[1] for r in cur.execute("PRAGMA table_info(fills)").fetchall()} if "fills" in tables else set()
        for pat in slugs:
            cur.execute("SELECT id, order_id FROM orders WHERE condition_id LIKE ? OR order_id LIKE ?", (pat, pat))
            rows = cur.fetchall()
            if not rows:
                continue
            ids = [r[0] for r in rows]
            order_ids = [r[1] for r in rows if r[1]]
            cur.execute("DELETE FROM orders WHERE condition_id LIKE ? OR order_id LIKE ?", (pat, pat))
            if "fills" in tables and ids:
                if "order_uuid" in fills_cols:
                    cur.execute("DELETE FROM fills WHERE order_uuid IN ({})".format(",".join("?" for _ in ids)), ids)
                elif "order_id" in fills_cols and order_ids:
                    cur.execute("DELETE FROM fills WHERE order_id IN ({})".format(",".join("?" for _ in order_ids)), order_ids)
            if "closes" in tables:
                # Production closes has no order_id; match by market identity.
                cur.execute("DELETE FROM closes WHERE condition_id LIKE ? OR market_slug LIKE ?", (pat, pat))
            removed += len(rows)

        if removed > 0:
            con.commit()
            try:
                cur.execute("VACUUM")
            except Exception:
                pass
            log.info("Cleaned %d stale order(s) from %s", removed, db_path.name)
        con.close()
    except Exception as exc:
        log.warning("Could not sweep stale orders in %s: %s", db_path.name, exc)
    return removed


def clear_instance_lock(db_path: Path) -> None:
    """Release any leftover instance_lock so a restarted arm can acquire immediately."""
    if not db_path.exists():
        return
    try:
        con = sqlite3.connect(str(db_path), timeout=5.0)
        con.execute("DELETE FROM instance_lock")
        con.commit()
        con.close()
    except Exception:
        pass


def ensure_filter_loop() -> bool:
    """Ensure the Market Filter background loop is actively screening and publishing."""
    pipeline_file = ROOT / "runtime" / "pipeline.json"
    now = time.time()
    if pipeline_file.exists():
        age = now - pipeline_file.stat().st_mtime
        if age < 180.0:
            log.info("Market Filter loop is fresh (snapshot age: %.1fs).", age)
            return True

    log.info("Starting fresh Market Filter loop (scripts.filter_loop)...")
    python_exe = sys.executable
    log_dir = ROOT / "runtime" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    lf = open(log_dir / "filter_loop.log", "a", encoding="utf-8")
    
    cmd = [python_exe, "-m", "scripts.filter_loop"]
    subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT, **_detached_kwargs())
    time.sleep(1.5)
    return True


def ensure_dashboard(port: int = 8799, log_dir: Optional[Path] = None) -> bool:
    """Ensure the Unified Dashboard is listening on :8799, serving Cockpit and all arms."""
    if is_port_listening(port):
        log.info("Unified Dashboard is listening on :%d (Cockpit live at http://127.0.0.1:%d/cockpit/)", port, port)
        return True
    log.info("Starting Unified Dashboard on port %d...", port)
    python_exe = sys.executable
    target_log_dir = log_dir if log_dir is not None else (ROOT / "runtime" / "logs")
    target_log_dir.mkdir(parents=True, exist_ok=True)
    lf = open(target_log_dir / "dashboard.log", "a", encoding="utf-8")
    cmd = [python_exe, "-m", "dashboard.server", "--port", str(port)]
    subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT, **_detached_kwargs())
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if is_port_listening(port):
            log.info("Unified Dashboard started successfully on port %d", port)
            return True
        time.sleep(0.3)
    log.warning("Unified Dashboard process launched but port %d not yet accepting connections", port)
    return False


def ensure_cockpit_and_hub(port: int = 8800, hub_port: int = 8798) -> None:
    """Hub and Cockpit are served in-process by the unified :8799 dashboard."""
    ensure_dashboard(8799)


def reconcile_arms(
    arms: List[Dict[str, Any]],
    logs_dir: Path,
    max_markets: Optional[int] = None,
    minutes: float = 1440.0,
    tree_mtime: float = 0.0,
) -> None:
    """Check each arm's shadow_run process; restart any that are dead or holding stale code."""
    python_exe = sys.executable
    clean_env = {
        k: v for k, v in os.environ.items()
        if not (k.startswith("FILTER_") or k.startswith("BOT_"))
    }

    for arm in arms:
        idx = arm.get("index", 1)
        name = arm.get("name", f"arm-{idx}")
        db_path = arm.get("db_path")
        run_id = arm.get("run_id")
        dash_port = arm.get("dash_port")
        arm_env = arm.get("env", {})

        if not db_path or not run_id:
            continue

        full_db = Path(db_path) if Path(db_path).is_absolute() else (ROOT / db_path)
        hb_file = ROOT / "runtime" / f"shadow_run_{run_id}.json"

        needs_restart = False
        old_pid = None

        if hb_file.exists():
            try:
                hb = json.loads(hb_file.read_text(encoding="utf-8"))
                old_pid = hb.get("pid")
                is_running = (not hb.get("finished", False)) and _is_pid_alive(old_pid)
                is_stale = code_predates_tree(hb, clock=tree_mtime)

                if is_running and not is_stale:
                    cmdline = _pid_commandline(old_pid) if old_pid else ""
                    needs_restart = False
                    if cmdline:
                        tokens = cmdline.split()
                        running_cap = None
                        if "--max-markets" in tokens:
                            pos = tokens.index("--max-markets") + 1
                            running_cap = tokens[pos] if pos < len(tokens) else None
                        wanted_cap = str(max_markets) if max_markets else None
                        needs_restart = running_cap != wanted_cap
                else:
                    needs_restart = True
            except Exception:
                needs_restart = True
        else:
            needs_restart = True

        if needs_restart:
            if old_pid:
                cmdline = _pid_commandline(old_pid)
                if cmdline and run_id not in cmdline and "shadow_run" not in cmdline:
                    log.warning("PID %d is not arm %s (%s); leaving it running", old_pid, run_id, cmdline[:120])
                else:
                    log.info("Stopping stale/dead arm #%02d [%s] (PID %d)...", idx, name, old_pid)
                    kill_pid(old_pid)

            clear_instance_lock(full_db)

            log_file = logs_dir / f"arm-{idx:02d}-{name}.log"
            lf = open(log_file, "a", encoding="utf-8")
            mk_desc = f"max_markets={max_markets}" if max_markets else "uncapped"
            lf.write(f"\n===== RECONCILE {time.strftime('%Y-%m-%d %H:%M:%S')} {mk_desc} (tree sync) =====\n")
            lf.flush()

            cmd = [
                python_exe,
                "-m",
                "core_brain.shadow_run",
                "--db",
                str(full_db),
                "--run-id",
                run_id,
                "--minutes",
                str(minutes),
                "--interval",
                "1.0",
            ]
            if max_markets and max_markets > 0:
                cmd.extend(["--max-markets", str(max_markets)])
            env = {**clean_env, **arm_env, "SPREAD_HUNTER_DB": str(full_db)}
            p = subprocess.Popen(cmd, env=env, stdout=lf, stderr=subprocess.STDOUT, **_detached_kwargs())
            log.info("Launched fresh arm #%02d [%s] (PID %d) -> %s, 24h box", idx, name, p.pid, mk_desc)
            time.sleep(0.3)


def verify_and_report(arms: List[Dict[str, Any]], tourney_id: str) -> bool:
    """Check all shadow arms and the shared Market Filter state, then report."""
    log.info("\n" + "=" * 80)
    log.info("TOURNAMENT #%s STACK ALIGNMENT VERDICT", tourney_id)
    log.info("=" * 80)

    # Per-arm dashboards are retired, so no :8801-:8810 server answers /api/kpi
    # anymore. The Market Filter publishes one shared snapshot for the whole
    # stack; its age is every arm row's filter state.
    stack_filter_state = "UNKNOWN"
    try:
        age = time.time() - (ROOT / "runtime" / "pipeline.json").stat().st_mtime
        stack_filter_state = "RUNNING" if age <= 1200 else "DEGRADED"
    except OSError:
        pass

    all_green = True
    rows = []

    for arm in arms:
        idx = arm.get("index", 1)
        name = arm.get("name", f"arm-{idx}")
        port = arm.get("dash_port")
        db_path = arm.get("db_path")
        run_id = arm.get("run_id")
        full_db = Path(db_path) if Path(db_path).is_absolute() else (ROOT / db_path)

        orders_cnt, filled_cnt = 0, 0
        if full_db.exists():
            try:
                con = sqlite3.connect(str(full_db), timeout=2.0)
                cur = con.cursor()
                orders_cnt = cur.execute("SELECT count(*) FROM orders").fetchone()[0]
                # The registry writes lowercase statuses ('filled', 'open', ...).
                filled_cnt = cur.execute("SELECT count(*) FROM orders WHERE status='filled'").fetchone()[0]
                con.close()
            except Exception:
                pass

        engine_ok = False
        code_stale = True
        market_filter_state = "UNKNOWN"

        hb_file = ROOT / "runtime" / f"shadow_run_{run_id}.json"
        if hb_file.exists():
            try:
                hb = json.loads(hb_file.read_text(encoding="utf-8"))
                pid = hb.get("pid", 0)
                if not hb.get("finished", False) and _is_pid_alive(pid):
                    engine_ok = True
                code_stale = code_predates_tree(hb)
            except Exception:
                pass

        market_filter_state = stack_filter_state

        if not engine_ok or code_stale or market_filter_state != "RUNNING":
            all_green = False

        status_str = "RUNNING (OK)" if engine_ok and not code_stale else "STALE / DOWN"
        rows.append((idx, name, status_str, market_filter_state, orders_cnt, filled_cnt))

    header = f"{'#':<3} {'Arm Name':<24} {'Engine Status':<16} {'Filter':<10} {'Orders':<8} {'Filled':<6}"
    log.info(header)
    log.info("-" * len(header))
    for idx, name, status_str, filter_st, orders_cnt, filled_cnt in rows:
        log.info(f"{idx:<3} {name:<24} {status_str:<16} {filter_st:<10} {orders_cnt:<8} {filled_cnt:<6}")

    log.info("-" * len(header))
    log.info("Tournament Cockpit: http://127.0.0.1:8799/cockpit/")
    log.info("Tournament Hub:     http://127.0.0.1:8799/hub/")

    if all_green:
        log.info("[OK] ALL %d ARMS ARE SYNCHRONIZED, RUNNING FRESH CODE, AND MARKET FILTER IS ACTIVE!", len(arms))
    else:
        log.warning("[WARN] Some arms or services are still settling. Rerun reconcile if needed.")

    return all_green


def reconcile(tourney_id: str = "799", max_markets: Optional[int] = None, minutes: float = 1440.0,
              sweep_slugs: Sequence[str] = (), base_dir: Optional[Path | str] = None) -> int:
    """Full idempotent alignment pass for the specified tournament."""
    clean_id = str(tourney_id).lstrip("i#")
    log.info("Starting tournament #%s reconciliation pass...", clean_id)
    tournaments_dir = Path(base_dir) if base_dir else (ROOT / "data" / "tournaments")
    plan_path = latest_tournament_record(clean_id, tournaments_dir)
    if plan_path is None or not plan_path.is_file():
        log.info("No active tournament plan found on disk for tourney #%s; initializing fresh plan...", clean_id)
        from scripts.shadow_tournament import build_tournament_plan
        arms_file = ROOT / "configs" / "tournaments" / f"i{clean_id}-tournament-test_arms.json"
        if not arms_file.is_file():
            candidates = list((ROOT / "configs" / "tournaments").glob(f"i{clean_id}-*arms.json"))
            if not candidates:
                candidates = list((ROOT / "configs" / "tournaments").glob(f"i{clean_id}_*arms.json"))
            if not candidates:
                pattern = re.compile(rf"(^|[^0-9]){re.escape(clean_id)}([^0-9]|$)")
                candidates = [f for f in (ROOT / "configs" / "tournaments").glob("*arms.json") if pattern.search(f.name)]
            if candidates:
                arms_file = candidates[0]

        if not arms_file.is_file():
            log.error("No tournament arms configuration file found for issue #%s in configs/tournaments/", clean_id)
            return 1

        arms_data = json.loads(arms_file.read_text(encoding="utf-8"))
        plan = build_tournament_plan(
            issue=int(clean_id) if clean_id.isdigit() else 799,
            arms=arms_data,
            base_port=8801,
            minutes=minutes,
            interval=1.0,
            dashboards=True,
            max_markets=max_markets,
            run_name="tournament-1m",
            base_dir=str(tournaments_dir),
            check_ports=False,
        )
        plan.record_path.parent.mkdir(parents=True, exist_ok=True)
        plan.record_path.write_text(json.dumps(plan.to_dict(), indent=2), encoding="utf-8")
        plan_path = plan.record_path
        log.info("Initialized fresh tournament plan at %s", plan_path)

    with open(plan_path, "r", encoding="utf-8") as f:
        plan = json.load(f)

    arms = plan.get("arms", [])
    if not arms:
        log.error("No arms found in tournament plan %s", plan_path)
        return 1

    run_dir = plan_path.parent
    logs_dir = run_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    # 1. Optional sweep of markets the operator named
    if sweep_slugs:
        log.info("1. Sweeping %d named market(s) across %d tournament databases...", len(sweep_slugs), len(arms))
        total_swept = 0
        for arm in arms:
            db_path = arm.get("db_path")
            if db_path:
                full_db = Path(db_path) if Path(db_path).is_absolute() else (ROOT / db_path)
                total_swept += sweep_stale_orders(full_db, sweep_slugs)
        log.info("Stale order sweep complete (%d total orders purged).", total_swept)
    else:
        log.info("1. No --sweep-slug given: arm databases left untouched.")

    # 2. Ensure filter loop is running
    log.info("2. Ensuring Market Filter loop is running...")
    ensure_filter_loop()

    # 3. Ensure Cockpit and Hub
    log.info("3. Cockpit/Hub are :8799 pages (standalone :8800/:8798 retired)...")
    ensure_cockpit_and_hub()

    # 4. Check tree mtime
    tree_mtime = decision_code_mtime() or time.time()

    # 4. Reconcile Shadow Arms
    mk_desc = f"max_markets={max_markets}" if max_markets else "uncapped"
    log.info("4. Reconciling %d shadow run arms (%s, 24h box)...", len(arms), mk_desc)
    reconcile_arms(arms, logs_dir, max_markets=max_markets, minutes=minutes, tree_mtime=tree_mtime)

    # Give services time to initialize and write heartbeats
    log.info("5. Waiting 5 seconds for heartbeats to settle...")
    time.sleep(5.0)

    # 7. Verify and output report
    success = verify_and_report(arms, tourney_id)
    return 0 if success else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Reconcile and Align Tournament Stack")
    parser.add_argument("--tourney", default="799", help="Tournament issue ID (default: 799)")
    parser.add_argument("--max-markets", type=int, default=None, help="Max markets per arm (default: uncapped)")
    parser.add_argument("--minutes", type=float, default=1440.0, help="Run duration in minutes (default: 1440.0)")
    parser.add_argument("--sweep-slug", action="append", default=[], metavar="SLUG",
                        help="Purge orders for this market slug from every arm database (repeatable; default: none)")
    args = parser.parse_args()

    return reconcile(tourney_id=args.tourney, max_markets=args.max_markets,
                     minutes=args.minutes, sweep_slugs=args.sweep_slug)


if __name__ == "__main__":
    sys.exit(main())
