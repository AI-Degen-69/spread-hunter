"""Resume tournament shadow rehearsal arms with custom max_markets.

Usage:
    python -m scripts.resume_tournament_arms --tourney 799 --max-markets 3
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core_brain.shadow_run import latest_tournament_record  # noqa: E402
from scripts.reconcile_tournament_stack import clear_instance_lock, sweep_stale_orders  # noqa: E402

log = logging.getLogger("tournament_resume")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def main() -> int:
    parser = argparse.ArgumentParser(description="Resume Tournament Arms")
    parser.add_argument("--tourney", default="799", help="Tournament issue ID (default: 799)")
    parser.add_argument("--max-markets", type=int, default=3, help="Max markets to quote simultaneously (default: 3)")
    parser.add_argument("--minutes", type=float, default=1440.0, help="Run duration in minutes (default: 1440.0)")
    args = parser.parse_args()

    plan_path = latest_tournament_record(args.tourney, ROOT / "data" / "tournaments")
    if plan_path is None or not plan_path.is_file():
        log.error("No tournament plan found for tourney #%s", args.tourney)
        return 1

    with open(plan_path, "r", encoding="utf-8") as f:
        plan = json.load(f)

    arms = plan.get("arms", [])
    if not arms:
        log.error("No arms found in plan %s", plan_path)
        return 1

    run_dir = plan_path.parent
    logs_dir = run_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    log.info("Resuming tournament %s with %d arms (max_markets=%d)...", run_dir.name, len(arms), args.max_markets)

    python_exe = sys.executable
    procs: list[tuple[subprocess.Popen, str, Any]] = []

    def _cleanup():
        log.info("Terminating resumed arm processes...")
        for p, label, lf in procs:
            if p.poll() is None:
                try:
                    p.terminate()
                except Exception:
                    pass
        deadline = time.time() + 3.0
        for p, label, lf in procs:
            remaining = max(0.1, deadline - time.time())
            try:
                p.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                try:
                    p.kill()
                except Exception:
                    pass
            if lf and not lf.closed:
                try:
                    lf.close()
                except Exception:
                    pass

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, lambda _s, _f: _cleanup())
        except (ValueError, AttributeError):
            pass

    try:
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
            sweep_stale_orders(full_db)
            clear_instance_lock(full_db)
            log_file = logs_dir / f"arm-{idx:02d}-{name}.log"
            lf = open(log_file, "a", encoding="utf-8")

            lf.write(f"\n===== RESUME {time.strftime('%Y-%m-%d %H:%M:%S')} max_markets={args.max_markets} =====\n")
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
                str(args.minutes),
                "--interval",
                "1.0",
                "--dash-port",
                str(dash_port),
                "--max-markets",
                str(args.max_markets),
            ]

            env = {**clean_env, **arm_env, "SPREAD_HUNTER_DB": str(full_db)}

            p = subprocess.Popen(cmd, env=env, stdout=lf, stderr=subprocess.STDOUT)
            procs.append((p, name, lf))
            log.info("Spawned arm #%02d [%s] (PID %d) -> max_markets=%d, db=%s", idx, name, p.pid, args.max_markets, full_db.name)

        log.info("All 10 arms resumed! Monitoring child processes. Press Ctrl+C to stop.")
        while True:
            alive = sum(1 for p, _, _ in procs if p.poll() is None)
            if alive == 0:
                log.info("All resumed arms have completed.")
                break
            time.sleep(2.0)

    except KeyboardInterrupt:
        log.info("KeyboardInterrupt received.")
    finally:
        _cleanup()

    return 0


if __name__ == "__main__":
    sys.exit(main())
