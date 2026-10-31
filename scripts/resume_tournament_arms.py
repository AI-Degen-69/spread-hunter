"""Resume tournament shadow rehearsal arms with custom max_markets.

Usage:
    python -m scripts.resume_tournament_arms --tourney 799 --max-markets 3
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import IO, Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core_brain.shadow_run import latest_tournament_record
from scripts.reconcile_tournament_stack import clear_instance_lock

FILTER_LOOP_ARGV = [sys.executable, "-m", "scripts.filter_loop"]
FILTER_INTERVAL_SEC = 600

log = logging.getLogger("tournament_resume")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def resume_tournament(
    plan_path: Path,
    max_markets: int = 3,
    minutes: float = 1440.0,
    poll_interval: float = 2.0,
) -> int:
    if not plan_path.is_file():
        log.error("Tournament plan file not found: %s", plan_path)
        return 1

    with open(plan_path, encoding="utf-8") as f:
        plan = json.load(f)

    arms = plan.get("arms", [])
    if not arms:
        log.error("No arms found in plan %s", plan_path)
        return 1

    run_dir = plan_path.parent
    logs_dir = run_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    log.info("Resuming tournament %s with %d arms (max_markets=%d)...", run_dir.name, len(arms), max_markets)

    python_exe = sys.executable
    procs: list[tuple[subprocess.Popen, str, IO[Any] | None]] = []

    def _cleanup():
        log.info("Terminating resumed arm and filter processes...")
        for p, _label, _lf in procs:
            if p.poll() is None:
                try:
                    p.terminate()
                except (OSError, RuntimeError):
                    pass
        deadline = time.time() + 3.0
        for p, _label, _lf in procs:
            remaining = max(0.1, deadline - time.time())
            try:
                p.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                try:
                    p.kill()
                except (OSError, RuntimeError):
                    pass
            if _lf and not _lf.closed:
                try:
                    _lf.close()
                except (OSError, RuntimeError):
                    pass

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, lambda _s, _f: _cleanup())
        except (ValueError, AttributeError):
            pass

    try:
        clean_env = {
            k: v for k, v in os.environ.items()
            if not k.startswith(("FILTER_", "BOT_"))
        }

        extra_popen_kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            extra_popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

        # 0. Start Market Filter to keep shared feed runtime/markets.json fresh
        filter_log_file = logs_dir / "market-filter.log"
        filter_lf = open(filter_log_file, "a", encoding="utf-8")  # noqa: SIM115
        try:
            filter_env = dict(clean_env)
            filter_env.setdefault("SH_FILTER_INTERVAL_SEC", str(FILTER_INTERVAL_SEC))
            filter_proc = subprocess.Popen(
                FILTER_LOOP_ARGV, env=filter_env,
                stdin=subprocess.DEVNULL,
                stdout=filter_lf, stderr=subprocess.STDOUT,
                **extra_popen_kwargs,
            )
            procs.append((filter_proc, "market-filter", filter_lf))
            log.info("Started Market Filter (scripts.filter_loop, PID %d, %ds cycle)",
                     filter_proc.pid, FILTER_INTERVAL_SEC)
        except Exception:
            log.exception("Market Filter failed to start; arms will run on current runtime/markets.json")
            if not filter_lf.closed:
                filter_lf.close()

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
            clear_instance_lock(full_db)
            log_file = logs_dir / f"arm-{idx:02d}-{name}.log"
            lf = open(log_file, "a", encoding="utf-8")  # noqa: SIM115

            lf.write(f"\n===== RESUME {time.strftime('%Y-%m-%d %H:%M:%S')} max_markets={max_markets} =====\n")
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
                "--dash-port",
                str(dash_port),
                "--max-markets",
                str(max_markets),
            ]

            env = {**clean_env, **arm_env, "SPREAD_HUNTER_DB": str(full_db)}

            p = subprocess.Popen(cmd, env=env, stdout=lf, stderr=subprocess.STDOUT, **extra_popen_kwargs)
            procs.append((p, name, lf))
            log.info("Spawned arm #%02d [%s] (PID %d) -> max_markets=%d, db=%s", idx, name, p.pid, max_markets, full_db.name)

        active_arms_count = sum(1 for _, label, _ in procs if label != "market-filter")
        log.info("All %d arms resumed! Monitoring child processes. Press Ctrl+C to stop.", active_arms_count)
        while True:
            alive = sum(1 for p, label, _ in procs if label != "market-filter" and p.poll() is None)
            if alive == 0:
                log.info("All resumed arms have completed.")
                break
            time.sleep(poll_interval)

    except KeyboardInterrupt:
        log.info("KeyboardInterrupt received.")
    finally:
        _cleanup()

    return 0


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

    return resume_tournament(plan_path=plan_path, max_markets=args.max_markets, minutes=args.minutes)


if __name__ == "__main__":
    sys.exit(main())
