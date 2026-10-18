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
import sqlite3
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core_brain.config import TOURNAMENT_PRESETS
from core_brain.kpi import MERGE_METHODS, NON_TRADE_CLOSE_METHODS
from core_brain.shadow_guard import assert_not_production_registry
from core_brain.shadow_run import (
    build_tournament_db_path,
    build_tournament_db_path_v2,
    build_tournament_log_path,
    build_tournament_log_path_v2,
    build_tournament_run_id,
    build_tournament_run_id_v2,
)

# The Market Filter (`scripts.filter_loop`) refreshes the shared market feed
# `runtime/markets.json` that every arm reads. Arms re-read that file each cycle
# (`dynamic_markets_fn`), so a live filter keeps a long run trading fresh
# markets instead of a universe that settles out from under it -- and, because
# `load_graduated_markets` refuses a feed older than the staleness window, the
# filter is also what stops every arm from refusing the feed mid-run. It writes
# the SHARED feed on purpose: the arms must compare over the same universe.
#
# The cycle length is the env var `SH_FILTER_INTERVAL_SEC`, not a CLI flag --
# `filter_loop` takes no `--interval`. Set it in the child's environment so the
# shipped 600s default and any operator override in .env are both honoured.
FILTER_LOOP_ARGV = [sys.executable, "-m", "scripts.filter_loop"]
FILTER_INTERVAL_SEC = 600

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
    results_path: Path
    log_path: Optional[Path] = None
    markets_path: Optional[str] = None
    arms: list[ArmPlan] = field(default_factory=list)
    run_number: Optional[int] = None
    arms_source: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue": self.issue,
            "stamp": self.stamp,
            "run_number": self.run_number,
            "base_port": self.base_port,
            "minutes": self.minutes,
            "interval": self.interval,
            "dashboards": self.dashboards,
            "record_path": str(self.record_path),
            "results_path": str(self.results_path),
            "log_path": str(self.log_path) if self.log_path is not None else None,
            "markets_path": self.markets_path,
            "arms_source": self.arms_source,
            "arms": [a.to_dict() for a in self.arms],
        }


RESULTS_NOTE = (
    "Shadow results are modeled. Do not choose a production winner from this file. "
    "The stat gate decides. Arms read books at slightly different moments, so the "
    "market list is shared but the books are not an identical snapshot."
)


def read_arm_results(db_path: Path, run_id: str) -> dict[str, Any]:
    """Read per-arm fill/close counts, read-only, filtered by run_id.

    Opens the store with `mode=ro` so a report can never alter what it
    reports (and a missing file is never created). Counting rules follow
    `kpi._execution_funnel`: one filled order is one distinct `order_uuid`,
    and sentinel close methods are not trades.
    """
    try:
        conn = sqlite3.connect(f"{Path(db_path).resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return {"status": "unavailable", "error": str(exc)}
    try:
        try:
            fill_events = conn.execute(
                "SELECT count(*) FROM fills WHERE run_id = ?", (run_id,)
            ).fetchone()[0]
            filled_orders = conn.execute(
                "SELECT count(DISTINCT order_uuid) FROM fills WHERE run_id = ?",
                (run_id,),
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT method, realized_pnl FROM closes WHERE run_id = ?", (run_id,)
            ).fetchall()
        except sqlite3.Error as exc:
            return {"status": "unavailable", "error": str(exc)}
        traded = [r for r in rows if (r[0] or "unknown") not in NON_TRADE_CLOSE_METHODS]
        merged = [r for r in traded if (r[0] or "unknown") in MERGE_METHODS]
        pnl = sum(float(r[1] or 0.0) for r in traded)
        return {
            "fill_events": fill_events,
            "filled_orders": filled_orders,
            "close_events": len(traded),
            "merges": len(merged),
            "single_leg_exits": len(traded) - len(merged),
            "realized_pnl_usd": pnl,
            "status": "no_fills" if fill_events == 0 else "ok",
        }
    finally:
        conn.close()


def write_tournament_results(plan: TournamentPlan, exit_codes: list[int]) -> Path:
    """Write one comparison file for every arm (including failed ones)."""
    out_path = Path(plan.results_path)
    if not out_path.is_absolute():
        out_path = ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    arms_data = []
    for arm, code in zip(plan.arms, exit_codes, strict=True):
        result = read_arm_results(arm.db_path, arm.run_id)
        arms_data.append({
            "name": arm.name,
            "db_path": str(arm.db_path),
            "run_id": arm.run_id,
            "dash_port": arm.dash_port,
            "exit_code": code,
            **result,
        })
    payload = {
        "issue": plan.issue,
        "stamp": plan.stamp,
        "minutes": plan.minutes,
        "markets_path": plan.markets_path,
        "note": RESULTS_NOTE,
        "arms": arms_data,
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Tournament results: {out_path}")
    for entry in arms_data:
        if entry["status"] == "unavailable":
            print(f"  {entry['name']:<12} unavailable ({entry.get('error', '?')})"
                  f" exit={entry['exit_code']}")
        else:
            print(f"  {entry['name']:<12} fills={entry['fill_events']}"
                  f" orders={entry['filled_orders']} closes={entry['close_events']}"
                  f" merges={entry['merges']} exits={entry['single_leg_exits']}"
                  f" pnl={entry['realized_pnl_usd']:+.4f} exit={entry['exit_code']}")
    return out_path


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
    """Convert a tournament preset dict into BOT_* environment overrides.

    Canonical `BOT_*` names win over deprecated `HUNTER_*` aliases
    (see `.env.example` and `core_brain.config._env_get`). The launcher emits
    canonical names only; legacy aliases still load through `from_env`.
    """
    env: dict[str, str] = {}
    if "dynamic_offset_enabled" in preset:
        env["BOT_QUOTE_GATE_DYNAMIC_OFFSET_ON"] = "1" if preset["dynamic_offset_enabled"] else "0"
    if "dynamic_offset_multiplier" in preset:
        env["BOT_QUOTE_SPREAD_OFFSET_MULTIPLIER"] = str(preset["dynamic_offset_multiplier"])
    if "dynamic_offset_min_cents" in preset:
        env["BOT_QUOTE_SPREAD_OFFSET_MIN_CENTS"] = str(int(preset["dynamic_offset_min_cents"]))
    if "dynamic_offset_max_cents" in preset:
        env["BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS"] = str(int(preset["dynamic_offset_max_cents"]))
    if "stop_loss_mode" in preset:
        env["BOT_RISK_STOP_MODE"] = str(preset["stop_loss_mode"])
    if "drop_cents" in preset:
        env["BOT_RISK_STOP_ADVERSE_DROP_CENTS"] = str(preset["drop_cents"])
    # Loose-baseline knobs. These let an arm quote the raw dynamic
    # offset with no upper clamp and no WIDENED snap-back, without touching
    # the shipped defaults or any other arm.
    if "max_spread_from_mid" in preset:
        env["BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID"] = str(int(round(float(preset["max_spread_from_mid"]) * 100.0)))
    if "apply_widen_gate" in preset:
        env["BOT_QUOTE_GATE_WIDEN_ON"] = "1" if preset["apply_widen_gate"] else "0"
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
    run_number: Optional[int] = None,
) -> TournamentPlan:
    """Build a validated, isolated tournament execution plan.

    run_number selects the stamp-less naming scheme (Issue #492): arm slugs
    become <base>-<run>, DBs become data/i<issue>_tournament-<run>_<slug>.db,
    and the plan/results files drop the stamp. None keeps the legacy scheme
    byte-identical.
    """
    if stamp is None:
        stamp = time.strftime("%Y%m%d-%H%M%S")

    # If arms not provided, default to TOURNAMENT_PRESETS in balanced order.
    # The legacy "control" arm was purged 2026-10-12 (broken capped logic); the
    # system baseline IS the loose MakerConfig default, so the default order now
    # leads with the loosest diverging preset and no longer includes a control.
    if arms is None:
        default_order = ["conservative", "balanced", "aggressive", "prudent"]
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

        # Run-number scheme: the slug carries the run (<base>-<run>); preset
        # and env lookups below keep using the BASE name.
        slug = f"{name}-{int(run_number)}" if run_number is not None else name

        # Environment variable overrides
        arm_env = _preset_to_env(arm_def)
        custom_env = arm_def.get("env")
        if custom_env:
            for k, v in custom_env.items():
                if not (k.startswith("BOT_") or k.startswith("HUNTER_")):
                    raise ValueError(
                        f"Invalid environment override {k!r}: only BOT_* (or legacy HUNTER_*) overrides are permitted"
                    )
                arm_env[k] = str(v)
        # Full-profile selector: applies the whole preset (including the
        # shared queue-hold settings _preset_to_env does not copy). Only
        # for names the config knows; custom arms keep their explicit env.
        if (
            name in TOURNAMENT_PRESETS
            and "BOT_QUOTE_TOURNAMENT_PRESET" not in arm_env
            and "HUNTER_TOURNAMENT_PRESET" not in arm_env
        ):
            arm_env["BOT_QUOTE_TOURNAMENT_PRESET"] = name
        # Fail at plan time, not in the child. An unknown selector kills the arm
        # at config load (config.from_env), i.e. after the other arms have
        # already started and the run is half a tournament. The i492 arms file
        # carried `HUNTER_TOURNAMENT_PRESET=control` from #484's purged preset
        # and would have lost that arm exactly that way.
        preset_sel = (
            arm_env.get("BOT_QUOTE_TOURNAMENT_PRESET")
            or arm_env.get("HUNTER_TOURNAMENT_PRESET")
            or ""
        ).strip().lower()
        if preset_sel and preset_sel not in TOURNAMENT_PRESETS:
            raise ValueError(
                f"Arm {name!r} selects unknown tournament preset "
                f"{preset_sel!r}; valid presets: {list(TOURNAMENT_PRESETS)}"
            )

        # Port assignment & validation
        dash_port = base_port + (idx - 1)
        if dash_port == 8799:
            raise ValueError(
                "Port 8799 is reserved for live execution and cannot be used in tournament"
            )
        if check_ports and not is_port_available(dash_port):
            raise ValueError(f"Port {dash_port} is already in use or unavailable")

        # Database path & safety
        if run_number is not None:
            db_path = build_tournament_db_path_v2(
                issue=issue,
                arm=slug,
                run_number=int(run_number),
                base_dir=base_dir_path,
            )
        else:
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

        if run_number is not None:
            run_id = build_tournament_run_id_v2(
                issue=issue,
                arm=slug,
                run_number=int(run_number),
            )
        else:
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
                name=slug,
                db_path=db_path,
                run_id=run_id,
                dash_port=dash_port,
                env=arm_env,
                shadow_argv=shadow_argv,
                dash_argv=dash_argv,
            )
        )

    if run_number is not None:
        record_path = (
            Path("runtime") / "tournaments" / f"i{issue}_tournament-{int(run_number)}.json"
        )
        results_path = (
            Path("reports") / "tournaments" / f"i{issue}_tournament-{int(run_number)}_results.json"
        )
        log_path = build_tournament_log_path_v2(issue=issue, run_number=int(run_number))
    else:
        record_path = Path("runtime") / "tournaments" / f"{issue}_{stamp}.json"
        results_path = Path("reports") / "tournaments" / f"{issue}_{stamp}_results.json"
        log_path = build_tournament_log_path(issue=issue, stamp=stamp)
    return TournamentPlan(
        issue=issue,
        stamp=stamp,
        run_number=(int(run_number) if run_number is not None else None),
        base_port=base_port,
        minutes=minutes,
        interval=interval,
        dashboards=dashboards,
        record_path=record_path,
        results_path=results_path,
        log_path=log_path,
        markets_path=str(markets_path) if markets_path is not None else None,
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
        # 0. Start the Market Filter so the shared feed stays fresh for the whole
        #    run. It refreshes runtime/markets.json, which every arm re-reads
        #    each cycle; without it a multi-hour run trades a universe that
        #    settles out and then trips the feed-staleness refusal entirely.
        #    It must be up before the arms start, so a long run never begins on
        #    a feed already older than the staleness window.
        filter_proc = None
        try:
            filter_env = {
                k: v for k, v in os.environ.items()
                if not (k.startswith("HUNTER_") or k.startswith("BOT_"))
            }
            filter_env.setdefault("SH_FILTER_INTERVAL_SEC", str(FILTER_INTERVAL_SEC))
            filter_proc = subprocess.Popen(
                FILTER_LOOP_ARGV, env=filter_env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            procs.append((filter_proc, "market-filter"))
            log.info("Started Market Filter (scripts.filter_loop, PID %d, "
                     "%ds cycle) refreshing the shared feed",
                     filter_proc.pid, FILTER_INTERVAL_SEC)
        except Exception:
            log.exception("Market Filter failed to start; arms will run on the "
                          "current runtime/markets.json until it goes stale")

        # 1. Spawn dashboard servers if requested
        if plan.dashboards:
            for arm in plan.arms:
                # Dashboard does not get arm env overrides
                dash_env = {
                    k: v for k, v in os.environ.items()
                    if not (k.startswith("HUNTER_") or k.startswith("BOT_"))
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
            # Strip inherited HUNTER_* and BOT_* variables like Market Filter and dashboard
            clean_env = {
                k: v for k, v in os.environ.items()
                if not (k.startswith("HUNTER_") or k.startswith("BOT_"))
            }
            shadow_env = {**clean_env, **arm.env, "SPREAD_HUNTER_DB": str(arm.db_path)}
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
        results_ok = True
        try:
            write_tournament_results(plan, exit_codes)
        except Exception:
            log.exception("Tournament results step failed")
            results_ok = False
        return _tournament_exit_code(exit_codes, results_ok)

    finally:
        _cleanup()


def _tournament_exit_code(exit_codes: list[int], results_ok: bool) -> int:
    """Worker codes win; a failed report step turns an all-green run non-zero."""
    code = max(exit_codes) if exit_codes else 0
    if not results_ok and code == 0:
        return 1
    return code


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
    parser.add_argument("--run-number", type=int, default=None, help="Run-number naming scheme: slugs become <base>-<run>, DBs i<issue>_tournament-<run>_<slug>.db, no stamps (default: legacy scheme)")
    parser.add_argument("--base-dir", type=str, default="data", help="Target database directory (default: data)")
    parser.add_argument("--log-file", type=str, default=None, help="Override detached-launch stdout redirect (default: canonical runtime/tournaments/i<issue>_tournament-<run>.log)")
    parser.add_argument("--no-port-check", action="store_true", help="Skip TCP socket availability check")

    args = parser.parse_args(argv)

    # The launcher's own startup lines previously went nowhere: this module
    # never configured logging, so log.info calls were silently dropped and
    # detached-launch logs contained only child output. Configure once here
    # (stderr only -- stdout stays pure plan JSON on --dry-run).
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    arms = None
    arms_source = None
    if args.arms_file:
        arms = json.loads(Path(args.arms_file).read_text(encoding="utf-8"))
        arms_source = str(args.arms_file)
        log.info("Tournament arms: %s (%d arms)", args.arms_file, len(arms))
    elif args.run_number is not None:
        # Run-number convention (#492): a numbered tournament reads its arm
        # definitions from configs/tournaments/i<issue>_tournament-<run>_arms.json
        # when that file exists, so the documented command
        # (`--issue 492 --run-number 1 --dry-run`) carries the arm list without
        # an extra flag. An explicit --arms-file always wins, and no file means
        # the shipped preset defaults.
        conventional = (
            ROOT / "configs" / "tournaments"
            / f"i{args.issue}_tournament-{int(args.run_number)}_arms.json"
        )
        if conventional.is_file():
            arms = json.loads(conventional.read_text(encoding="utf-8"))
            arms_source = conventional.relative_to(ROOT).as_posix()
            log.info(
                "Tournament arms: %s (%d arms)",
                arms_source, len(arms),
            )

    plan = build_tournament_plan(
        issue=args.issue,
        arms=arms,
        base_port=args.base_port,
        minutes=args.minutes,
        interval=args.interval,
        dashboards=args.dashboards,
        run_number=args.run_number,
        max_markets=args.max_markets,
        markets_path=args.markets_path,
        stamp=args.stamp,
        base_dir=args.base_dir,
        check_ports=(args.dashboards and not args.no_port_check) if not args.dry_run else False,
    )

    plan.arms_source = arms_source
    if args.log_file:
        plan.log_path = Path(args.log_file)

    if args.dry_run:
        log.info(
            "Tournament dry-run: issue=%s run_number=%s arms=%d log=%s (nothing started)",
            plan.issue, plan.run_number, len(plan.arms), plan.log_path,
        )
        print(json.dumps(plan.to_dict(), indent=2))
        return 0

    if plan.log_path is not None:
        log.info("Detached-launch redirect target: %s", plan.log_path)
    return launch_tournament(plan)


if __name__ == "__main__":
    sys.exit(main())
