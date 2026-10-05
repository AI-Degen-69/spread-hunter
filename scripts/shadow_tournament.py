"""Multi-arm tournament launcher orchestrator.

Launches isolated shadow runs for multiple parameter arms concurrently,
each with its own scratch SQLite database, unique index, designated dashboard port,
and optional dedicated dashboard monitor server.

Usage:
    python scripts/shadow_tournament.py --issue 371 --minutes 5 --dashboards
    python scripts/shadow_tournament.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import signal
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from core_brain.config import TOURNAMENT_PRESETS
from core_brain.shadow_guard import assert_not_production_registry
from core_brain.shadow_run import (
    build_tournament_db_path,
    build_tournament_run_id,
)

log = logging.getLogger("shadow_tournament")

ARM_NAME_PATTERN = re.compile(r"^[a-z0-9-]{1,24}$")


@dataclass
class ArmPlan:
    index: int
    name: str
    db_path: Path
    run_id: str
    dash_port: int
    env: dict[str, str] = field(default_factory=dict)
    shadow_argv: list[str] = field(default_factory=list)
    dash_argv: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "name": self.name,
            "db_path": str(self.db_path),
            "run_id": self.run_id,
            "dash_port": self.dash_port,
            "env": self.env,
            "shadow_argv": self.shadow_argv,
            "dash_argv": self.dash_argv,
        }


@dataclass
class TournamentPlan:
    issue: int
    stamp: str
    base_port: int
    minutes: float
    interval: float
    dashboards: bool
    record_path: Path
    arms: list[ArmPlan] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue": self.issue,
            "stamp": self.stamp,
            "base_port": self.base_port,
            "minutes": self.minutes,
            "interval": self.interval,
            "dashboards": self.dashboards,
            "record_path": str(self.record_path),
            "arms": [a.to_dict() for a in self.arms],
        }


def is_port_available(port: int, host: str = "127.0.0.1") -> bool:
    """Check if a TCP port is currently available on the local interface."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def _preset_to_env(preset: dict[str, Any]) -> dict[str, str]:
    """Convert a tournament preset dict into HUNTER_* environment overrides."""
    env: dict[str, str] = {}
    if "dynamic_offset_enabled" in preset:
        env["HUNTER_DYNAMIC_OFFSET"] = "1" if preset["dynamic_offset_enabled"] else "0"
    if "dynamic_offset_multiplier" in preset:
        env["HUNTER_DYNAMIC_OFFSET_MULT"] = str(preset["dynamic_offset_multiplier"])
    if "dynamic_offset_min_cents" in preset:
        env["HUNTER_DYNAMIC_OFFSET_MIN_CENTS"] = str(int(preset["dynamic_offset_min_cents"]))
    if "dynamic_offset_max_cents" in preset:
        env["HUNTER_DYNAMIC_OFFSET_MAX_CENTS"] = str(int(preset["dynamic_offset_max_cents"]))
    if "reward_offset" in preset:
        env["HUNTER_REWARD_OFFSET"] = str(preset["reward_offset"])
    return env


def build_tournament_plan(
    issue: int = 371,
    arms: Optional[list[dict[str, Any]]] = None,
    base_port: int = 8801,
    minutes: float = 5.0,
    interval: float = 5.0,
    dashboards: bool = False,
    max_markets: Optional[int] = None,
    markets_path: Optional[str | Path] = None,
    stamp: Optional[str] = None,
    base_dir: str | Path = "data",
    check_ports: bool = True,
    python_executable: str = sys.executable,
) -> TournamentPlan:
    """Build a validated, isolated tournament execution plan."""
    if stamp is None:
        stamp = time.strftime("%Y%m%d-%H%M%S")

    # If arms not provided, default to TOURNAMENT_PRESETS in balanced order
    if arms is None:
        default_order = ["control", "conservative", "balanced", "aggressive"]
        arms = []
        for name in default_order:
            if name in TOURNAMENT_PRESETS:
                arms.append({"name": name, **TOURNAMENT_PRESETS[name]})

    if not arms:
        raise ValueError("At least one arm must be specified for a tournament")

    seen_names: set[str] = set()
    arm_plans: list[ArmPlan] = []
    base_dir_path = Path(base_dir)

    for idx, arm_def in enumerate(arms, start=1):
        name = arm_def.get("name")
        if not name or not isinstance(name, str) or not ARM_NAME_PATTERN.match(name):
            raise ValueError(
                f"Invalid arm name {name!r}: must match ^[a-z0-9-]{{1,24}}$"
            )
        if name in seen_names:
            raise ValueError(f"Duplicate arm name: {name}")
        seen_names.add(name)

        # Environment variable overrides
        arm_env = _preset_to_env(arm_def)
        custom_env = arm_def.get("env")
        if custom_env:
            for k, v in custom_env.items():
                if not k.startswith("HUNTER_"):
                    raise ValueError(
                        f"Invalid environment override {k!r}: only HUNTER_* overrides are permitted"
                    )
                arm_env[k] = str(v)

        # Port assignment & validation
        dash_port = base_port + (idx - 1)
        if dash_port == 8799:
            raise ValueError(
                "Port 8799 is reserved for live execution and cannot be used in tournament"
            )
        if check_ports and not is_port_available(dash_port):
            raise ValueError(f"Port {dash_port} is already in use or unavailable")

        # Database path & safety
        db_path = build_tournament_db_path(
            issue=issue,
            index=idx,
            arm=name,
            stamp=stamp,
            base_dir=base_dir_path,
        )
        assert_not_production_registry(db_path)
        if db_path.exists():
            raise ValueError(f"Tournament database already exists: {db_path}")

        run_id = build_tournament_run_id(
            issue=issue,
            index=idx,
            arm=name,
            stamp=stamp,
        )

        shadow_argv = [
            python_executable,
            "-m",
            "core_brain.shadow_run",
            "--db",
            str(db_path),
            "--run-id",
            run_id,
            "--minutes",
            str(minutes),
            "--interval",
            str(interval),
            "--dash-port",
            str(dash_port),
        ]
        if max_markets is not None:
            shadow_argv.extend(["--max-markets", str(max_markets)])
        if markets_path is not None:
            shadow_argv.extend(["--markets-path", str(markets_path)])

        dash_argv = [
            python_executable,
            "-m",
            "dashboard.server",
            "--port",
            str(dash_port),
            "--db",
            str(db_path),
        ]

        arm_plans.append(
            ArmPlan(
                index=idx,
                name=name,
                db_path=db_path,
                run_id=run_id,
                dash_port=dash_port,
                env=arm_env,
                shadow_argv=shadow_argv,
                dash_argv=dash_argv,
            )
        )

    record_path = Path("runtime") / "tournaments" / f"{issue}_{stamp}.json"
    return TournamentPlan(
        issue=issue,
        stamp=stamp,
        base_port=base_port,
        minutes=minutes,
        interval=interval,
        dashboards=dashboards,
        record_path=record_path,
        arms=arm_plans,
    )


def launch_tournament(plan: TournamentPlan) -> int:
    """Execute tournament runs and optional dashboard servers."""
    plan.record_path.parent.mkdir(parents=True, exist_ok=True)
    plan.record_path.write_text(
        json.dumps(plan.to_dict(), indent=2), encoding="utf-8"
    )
    log.info("Tournament plan saved to %s", plan.record_path)

    procs: list[tuple[subprocess.Popen, str]] = []

    def _cleanup():
        log.info("Terminating tournament child processes...")
        for p, label in procs:
            if p.poll() is None:
                try:
                    p.terminate()
                except Exception:
                    pass
        # Wait up to 3 seconds for graceful shutdown
        deadline = time.time() + 3.0
        for p, label in procs:
            remaining = max(0.1, deadline - time.time())
            try:
                p.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                try:
                    p.kill()
                except Exception:
                    pass

    # Setup signal traps
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, lambda _s, _f: _cleanup())
        except (ValueError, AttributeError):
            pass

    try:
        # 1. Spawn dashboard servers if requested
        if plan.dashboards:
            for arm in plan.arms:
                # Dashboard does not get arm HUNTER_* env overrides
                dash_env = {
                    k: v for k, v in os.environ.items()
                    if not k.startswith("HUNTER_")
                }
                dash_env["PORT"] = str(arm.dash_port)
                dash_env["LIVE_DB_PATH"] = str(arm.db_path)
                p = subprocess.Popen(arm.dash_argv, env=dash_env)
                procs.append((p, f"dashboard-{arm.dash_port}"))
                log.info(
                    "Started dashboard server for arm #%02d [%s] on port %d (PID %d)",
                    arm.index, arm.name, arm.dash_port, p.pid
                )

        # 2. Spawn shadow rehearsal runs
        shadow_procs: list[subprocess.Popen] = []
        for arm in plan.arms:
            shadow_env = {**os.environ, **arm.env, "SPREAD_HUNTER_DB": str(arm.db_path)}
            p = subprocess.Popen(arm.shadow_argv, env=shadow_env)
            procs.append((p, f"shadow-{arm.name}"))
            shadow_procs.append(p)
            log.info(
                "Started shadow rehearsal for arm #%02d [%s] store=%s (PID %d)",
                arm.index, arm.name, arm.db_path.name, p.pid
            )

        log.info(
            "Tournament in progress (%d arms, timebox: %.1fm). Press Ctrl+C to abort.",
            len(plan.arms), plan.minutes
        )

        # Wait for all shadow runs to finish
        exit_codes = [p.wait() for p in shadow_procs]
        log.info("All shadow runs finished with codes: %s", exit_codes)
        return max(exit_codes) if exit_codes else 0

    finally:
        _cleanup()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Spread Hunter Multi-Arm Shadow Tournament Launcher"
    )
    parser.add_argument("--issue", type=int, default=371, help="Issue number (default: 371)")
    parser.add_argument("--arms-file", type=str, default=None, help="JSON file with custom arms list")
    parser.add_argument("--minutes", type=float, default=5.0, help="Time box per arm in minutes (default: 5.0)")
    parser.add_argument("--interval", type=float, default=5.0, help="Cycle rotation interval in seconds (default: 5.0)")
    parser.add_argument("--base-port", type=int, default=8801, help="Starting dashboard port (default: 8801)")
    parser.add_argument("--dashboards", action="store_true", help="Launch isolated dashboard monitors")
    parser.add_argument("--max-markets", type=int, default=None, help="Cap number of rotated markets")
    parser.add_argument("--markets-path", type=str, default=None, help="Path to trial universe feed")
    parser.add_argument("--dry-run", action="store_true", help="Print tournament plan JSON and exit")
    parser.add_argument("--stamp", type=str, default=None, help="Explicit timestamp identifier override")
    parser.add_argument("--base-dir", type=str, default="data", help="Target database directory (default: data)")
    parser.add_argument("--no-port-check", action="store_true", help="Skip TCP socket availability check")

    args = parser.parse_args(argv)

    arms = None
    if args.arms_file:
        arms = json.loads(Path(args.arms_file).read_text(encoding="utf-8"))

    plan = build_tournament_plan(
        issue=args.issue,
        arms=arms,
        base_port=args.base_port,
        minutes=args.minutes,
        interval=args.interval,
        dashboards=args.dashboards,
        max_markets=args.max_markets,
        markets_path=args.markets_path,
        stamp=args.stamp,
        base_dir=args.base_dir,
        check_ports=not args.no_port_check,
    )

    if args.dry_run:
        print(json.dumps(plan.to_dict(), indent=2))
        return 0

    return launch_tournament(plan)


if __name__ == "__main__":
    sys.exit(main())
