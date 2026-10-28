"""Tournament Dashboard Hub.

Monitors active tournament arms from data/tournaments/ and ensures
each arm has a dedicated dashboard server running on its assigned port
(e.g., ports 8801-8810). When a user clicks "Open Dash" in the Cockpit,
the server responds immediately.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core_brain.shadow_run import latest_tournament_record  # noqa: E402

log = logging.getLogger("dashboard_hub")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def is_port_listening(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.25):
            return True
    except (OSError, socket.timeout):
        return False


def get_latest_plan(tourney_id: str = "799", tourn_dir: Path | None = None) -> dict | None:
    """Newest plan record for this issue, resolved by the shared ranker.

    The ranking (highest run number, then newest stamp) lives in
    `core_brain.shadow_run.latest_tournament_record` so this hub and the cockpit
    cannot drift into disagreeing about which file is "the plan".
    """
    directory = tourn_dir if tourn_dir is not None else ROOT / "data" / "tournaments"
    latest = latest_tournament_record(tourney_id, directory)
    if latest is None:
        return None
    try:
        with open(latest, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        log.warning("Could not read tournament plan %s: %s", latest, exc)
        return None


def run_hub(tourney_id: str = "799", interval_sec: float = 3.0) -> None:
    procs: dict[int, subprocess.Popen] = {}
    python_exe = sys.executable

    log.info("Starting Tournament Dashboard Hub for tourney #%s...", tourney_id)

    try:
        while True:
            plan = get_latest_plan(tourney_id)
            if not plan:
                time.sleep(interval_sec)
                continue

            arms = plan.get("arms", [])
            for arm in arms:
                port = arm.get("dash_port")
                db_path = arm.get("db_path")
                name = arm.get("name", f"arm-{port}")

                if not port or not db_path:
                    continue

                full_db = ROOT / db_path if not Path(db_path).is_absolute() else Path(db_path)
                if not full_db.exists():
                    continue

                # Check if port is already serving
                if is_port_listening(port):
                    continue

                # Port is not listening; check if process crashed or needs starting
                existing_p = procs.get(port)
                if existing_p and existing_p.poll() is None:
                    # Still starting up or binding
                    continue

                log.info("Launching Dashboard for arm [%s] on port %d (db: %s)", name, port, db_path)
                cmd = [
                    python_exe,
                    "-m",
                    "dashboard.server",
                    "--port",
                    str(port),
                    "--db",
                    str(full_db),
                ]
                # Arm dashboards read their own --db; inheriting the parent's
                # config overrides would show every arm the launcher's settings.
                env = {k: v for k, v in os.environ.items()
                       if not k.startswith(("BOT_", "FILTER_"))}
                env["PORT"] = str(port)
                env["LIVE_DB_PATH"] = str(full_db)

                p = subprocess.Popen(
                    cmd,
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                procs[port] = p
                log.info("Spawned dashboard PID %d for arm [%s] on port %d", p.pid, name, port)

            time.sleep(interval_sec)
    except KeyboardInterrupt:
        log.info("Terminating dashboard servers...")
        for p in procs.values():
            if p.poll() is None:
                p.terminate()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tournament Dashboard Hub")
    parser.add_argument("--tourney", default="799", help="Tournament issue ID (default: 799)")
    args = parser.parse_args()
    run_hub(args.tourney)
