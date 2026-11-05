"""Tournament Analytics & Decision Cockpit Server.

Interactive real-time visualization and statistical analysis cockpit for
Spread Hunter multi-arm shadow tournaments. One page is served: the current
setup (#487). The past-run pages (#484, #492, #608) were purged on 2026-10-21 --
their routes 404 now, and their static snapshots are no longer written. Their
arm records and stores stay: they are launch/compare data, not pages.
Computes risk, expectancy (EV), standard deviation, max drawdown,
Monte Carlo projections, and Live Readiness Scoring.

The Cockpit is a page on the unified dashboard (:8799) with one tab per run:
    http://127.0.0.1:8799/cockpit/        # run tabs: 799, 843, 905, 917

Legacy standalone usage (local debugging only, not the operator flow):
    python -m scripts.tournament_cockpit_server --port 8800
"""
from __future__ import annotations

import argparse
import contextlib
import html
import http.server
import json
import logging
import math
import os
from pathlib import Path
import random
import re
import socket
import socketserver
import sqlite3
import sys
import threading
import time
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core_brain.close_methods import (  # noqa: E402
    MERGE_METHODS,
    RESCUE_METHODS,
    SETTLEMENT,
    SHADOW_MERGE_NOTE,
)
from core_brain.config import load as _load_config  # noqa: E402
from core_brain.shadow_run import (  # noqa: E402
    latest_tournament_record,
    parse_tournament_run_dir_name,
)
from core_brain.sharpe import (
    SHARPE_FORMULA_TEXT,
    risk_free_rate_from_env,
    sharpe_ratio,
)
from core_brain.kpi import kpi_sufficiency_table as _kpi_sufficiency_table  # noqa: E402

I843_ARMS_FILE = ROOT / "configs" / "tournaments" / "i843-pair-test_arms.json"
I799_ARMS_FILE = ROOT / "configs" / "tournaments" / "i799-tournament-test_arms.json"
I905_ARMS_FILE = ROOT / "configs" / "tournaments" / "i905-top5-rematch_arms.json"
I917_ARMS_FILE = ROOT / "configs" / "tournaments" / "i917-multistrat-defense_arms.json"
I923_ARMS_FILE = ROOT / "configs" / "tournaments" / "i923-live-sports-duel_arms.json"
I929_ARMS_FILE = ROOT / "configs" / "tournaments" / "i929-momentum-reversion_arms.json"
I938_ARMS_FILE = ROOT / "configs" / "tournaments" / "i938-tweak-rematch_arms.json"
I940_ARMS_FILE = ROOT / "configs" / "tournaments" / "i940-second-round-tweaks_arms.json"


def _load_arms_from_file(file_path: Path, issue_num: int, start_port: int = 8801) -> list[dict[str, Any]]:
    if not file_path.exists():
        return []
    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    arms = []
    for idx, item in enumerate(raw):
        port = start_port + idx
        name = item["name"]
        db_sub = f"i{issue_num}-pair-test_pair-test/{name}.db" if issue_num == 843 else f"i{issue_num}_tournament_{name}.db"
        arms.append({
            "issue": issue_num,
            "name": name,
            "label": item.get("label", name),
            "port": port,
            "db": f"data/tournaments/{db_sub}",
            "run_id": f"i{issue_num}_{name}",
            "color": item.get("color", "#00e676"),
            "desc": item.get("desc", ""),
            "worst_catalyst": item.get("worst_catalyst", ""),
            "worst_catalyst_desc": item.get("worst_catalyst_desc", ""),
            "hypothesized_tweak": item.get("hypothesized_tweak", ""),
            "tweak_rationale": item.get("tweak_rationale", ""),
            "env": item.get("env", {}),
        })
    return arms


ARMS_CONFIG_843 = _load_arms_from_file(I843_ARMS_FILE, 843, 8801)
ARMS_CONFIG_799 = _load_arms_from_file(I799_ARMS_FILE, 799, 8801)
ARMS_CONFIG_905 = _load_arms_from_file(I905_ARMS_FILE, 905, 8801)
ARMS_CONFIG_917 = _load_arms_from_file(I917_ARMS_FILE, 917, 8801)
ARMS_CONFIG_923 = _load_arms_from_file(I923_ARMS_FILE, 923, 8801)
ARMS_CONFIG_929 = _load_arms_from_file(I929_ARMS_FILE, 929, 8801)
ARMS_CONFIG_938 = _load_arms_from_file(I938_ARMS_FILE, 938, 8801)
ARMS_CONFIG_940 = _load_arms_from_file(I940_ARMS_FILE, 940, 8801)

# Legacy alias kept for older imports. The cockpit now serves #799 by
# default; this constant no longer selects a page.
ARMS_CONFIG = ARMS_CONFIG_799

# Run-level volume-check roles per tournament key. Each tournament has
# different arm names; this maps the logical role to the arm name pattern
# for the one explicitly comparative readout (volume flow between two
# configurations). Nothing here selects a test subject: every arm gets its
# own verdict from its own gates in compute_decision_gates.
DECISION_GATE_ARM_MAP = {
    "843": {
        "volume_arm_pattern": "balanced-queue5",      # Arm whose knob may choke fills
        "baseline_arm_names": ["control"],           # Pure control/baseline
        "balanced_only_arm_names": ["balanced-only"],  # Balanced preset only
    },
    "799": {
        "volume_arm_pattern": "queue-clear-5m",       # Arm whose knob may choke fills
        "baseline_arm_names": ["loose-baseline"],    # Pure control/baseline
        "balanced_only_arm_names": ["balanced-mid"], # Balanced preset only
    },
    "905": {
        "volume_arm_pattern": "queue-clear-5m",       # Arm whose knob may choke fills
        "baseline_arm_names": ["loose-baseline"],    # Pure control/baseline
        "balanced_only_arm_names": ["balanced-mid"], # Balanced preset only
    },
    "917": {
        "volume_arm_pattern": "queue-gated-hybrid",   # Arm whose knob may choke fills
        "baseline_arm_names": ["control-baseline"],  # Pure control/baseline
        "balanced_only_arm_names": ["macro-non-sports"],
    },
    "923": {
        "volume_arm_pattern": "live-sports-only",   # Narrower lane; may choke fills
        "baseline_arm_names": ["all-but-live-sports"],  # Rest of the board
        "balanced_only_arm_names": [],  # No balanced role: falls back to baseline
    },
    "929": {
        "volume_arm_pattern": "live-rider-5c-stop20pct",
        "baseline_arm_names": ["live-rider-2c-stop30pct"],
        "balanced_only_arm_names": [],
    },
    "938": {
        "volume_arm_pattern": "queue-clear-5m",       # Tightened 5m->3m gate; may choke fills
        "baseline_arm_names": ["loose-baseline"],    # Shipped baseline + 6c deadband
        "balanced_only_arm_names": ["balanced-mid"], # Balanced preset + 6c deadband
    },
    "940": {
        "volume_arm_pattern": "queue-clear-5m",       # Tightened 3m->2m gate; may choke fills
        "baseline_arm_names": ["loose-baseline"],    # Shipped baseline + 4c deadband
        "balanced_only_arm_names": ["balanced-mid"], # Balanced preset + 5c ceiling + 6c deadband
    },
}


TOURNAMENTS = {
    "843": {
        "id": "843",
        "title": "Issue #843 Balanced Preset & Queue-5m Gate Rehearsal",
        "subtitle": "24-Hour 4-Arm Multi-Arm Shadow Rehearsal | Control, Balanced-Only, Queue5-Only, Balanced-Queue5",
        "description": "Pre-registered head-to-head empirical rehearsal evaluating whether combining the dynamic balanced quoting preset (2c-4c) with the 5-minute queue-clear gate preserves risk-adjusted edge without volume starvation.",
        "arms": ARMS_CONFIG_843,
        "issue_primary": 843,
        "issue_secondary": 843,
        "nav_label": "#843 Pair Test (4 Arms)",
    },
    "799": {
        "id": "799",
        "title": "Issue #799 Multi-Arm Tournament",
        "subtitle": "10-Arm Live Shadow Evaluation | Strategy Presets and Execution Mechanics",
        "description": "Ten arms evaluating dynamic offset bands, queue-clear gates, requote deadbands, and stop modes under live shadow conditions.",
        "arms": ARMS_CONFIG_799,
        "issue_primary": 799,
        "issue_secondary": 799,
        "nav_label": "#799 Presets (10 Arms)",
    },
    "905": {
        "id": "905",
        "title": "Issue #905 Top-5 Rematch",
        "subtitle": "5-Arm Fresh Shadow Rehearsal | Top-5 Tournament #799 Arms, Empty Stores",
        "description": "Fresh rematch of the top-5 Tournament #799 arms (queue-clear-5m, balanced-mid, loose-baseline, prudent-hybrid, static-floor-2c) from empty stores, re-measuring risk shape on current market conditions.",
        "arms": ARMS_CONFIG_905,
        "issue_primary": 905,
        "issue_secondary": 905,
        "nav_label": "#905 Top-5 Rematch (5 Arms)",
    },
    "917": {
        "id": "917",
        "title": "Issue #917 Multi-Strategy Adverse Selection Defense",
        "subtitle": "6-Arm Live Shadow Tournament | Macro Non-Sports, Taker Snapper, Fast Bailout, Wide Cushion, Queue Hybrid, Control",
        "description": "Pre-registered 6-arm head-to-head shadow tournament evaluating empirical defenses against single-leg adverse selection and stranded orphan resolution losses across live binary markets.",
        "arms": ARMS_CONFIG_917,
        "issue_primary": 917,
        "issue_secondary": 917,
        "nav_label": "#917 Multi-Strat Defense (6 Arms)",
    },
    "923": {
        "id": "923",
        "title": "Issue #923 Live-Sports Duel",
        "subtitle": "2-Arm Live Shadow Duel | Live Sports Only vs All But Live Sports",
        "description": "Pre-registered 2-arm head-to-head shadow duel over the market-regime split: one arm quotes only venue-declared in-play sports/esports events, the other quotes everything except them.",
        "arms": ARMS_CONFIG_923,
        "issue_primary": 923,
        "issue_secondary": 923,
        "nav_label": "#923 Live-Sports Duel (2 Arms)",
    },
    "929": {
        "id": "929",
        "title": "Issue #929 Momentum Reversion Tournament",
        "subtitle": "6-Arm Shadow Tournament | Calibrated 30% Drawdown Stop & Trailing Peak Rider",
        "description": "Pre-registered 6-arm shadow tournament evaluating single-leg momentum riding (1c, 2c deltas), maker entry price ceilings (0.50, 0.60), and a calibrated 30% adverse drawdown stop on live sports flow.",
        "arms": ARMS_CONFIG_929,
        "issue_primary": 929,
        "issue_secondary": 929,
        "nav_label": "#929 Momentum Reversion (6 Arms)",
    },
    "938": {
        "id": "938",
        "title": "Issue #938 Tweak Rematch Tournament",
        "subtitle": "10-Arm Shadow Tournament | All Ten #799 Arms With Concluded Tweaks Applied",
        "description": "Pre-registered 10-arm shadow rematch: every #799 arm rerun with its concluded worst-catalyst tweak applied, testing whether the tweaks fix the orphan-exit bleed of the 2026-11-05 live run.",
        "arms": ARMS_CONFIG_938,
        "issue_primary": 938,
        "issue_secondary": 938,
        "nav_label": "#938 Tweak Rematch (10 Arms)",
    },
    "940": {
        "id": "940",
        "title": "Issue #940 Second-Round Tweak Tournament",
        "subtitle": "10-Arm Shadow Tournament | One-Notch Follow-Up Tweaks Off The #938 Interim Read",
        "description": "Pre-registered 10-arm shadow tournament: every arm rerun with its second-round one-notch tweak derived from the #938 interim shadow read, testing whether the follow-ups fix the orphan-exit bleed the #938 tweaks did not.",
        "arms": ARMS_CONFIG_940,
        "issue_primary": 940,
        "issue_secondary": 940,
        "nav_label": "#940 Second-Round Tweaks (10 Arms)",
    },
}


# The cockpit serves one page: the current setup. Everything past-run is a
# launch/compare record only. These paths are the only ones that render, and the
# current setup is the only tournament key the JSON API will serve.
CURRENT_SETUP_KEY = "799"
PAGE_PATHS: dict[str, str] = {
    "": CURRENT_SETUP_KEY,
    "/index.html": CURRENT_SETUP_KEY,
    "/843": "843",
    "/843.html": "843",
    "/cockpit_843": "843",
    "/799": "799",
    "/799.html": "799",
    "/cockpit_799": "799",
    "/905": "905",
    "/905.html": "905",
    "/cockpit_905": "905",
    "/917": "917",
    "/917.html": "917",
    "/cockpit_917": "917",
    "/923": "923",
    "/923.html": "923",
    "/cockpit_923": "923",
    "/929": "929",
    "/929.html": "929",
    "/cockpit_929": "929",
    "/938": "938",
    "/938.html": "938",
    "/cockpit_938": "938",
    "/940": "940",
    "/940.html": "940",
    "/cockpit_940": "940",
}

TOURNAMENT_ICONS: dict[str, str] = {
    "799": "🧪",
    "843": "🚦",
    "905": "🏆",
    "917": "🛡️",
    "923": "⚡",
    "929": "🏹",
    "938": "🔬",
    "940": "🔭",
}
TOURNAMENT_PILL_KEYS: tuple[str, ...] = ("799", "843", "905", "917", "923", "929", "938", "940")
# The keys the JSON API may serve: the served pages' own tournaments, derived so
# a new page cannot forget to widen this.
SERVED_TOURNAMENT_KEYS = frozenset(PAGE_PATHS.values())


def tourney_key_for_path(path: str) -> Optional[str]:
    """Return the tournament key whose page serves this path, or None for a 404.

    None is deliberate for `/492`, `/484`, `/484.html`, `/608`, `/608.html`, `/487` and `/487.html`:
    the purged past-run pages. Issue #698 asked for that purge; PR #703 ticked
    the acceptance boxes without delivering the route bullet, so it lands here.
    """
    clean = path.split("?")[0].rstrip("/")
    if clean in ("", "/index.html"):
        return resolve_current_setup_key()
    return PAGE_PATHS.get(clean)


def _strip_round_suffix(name: str) -> str:
    """Drop a trailing -<digit> round suffix so plan files match presets."""
    return re.sub(r"-\d+$", "", str(name or ""))


log = logging.getLogger(__name__)


class TournamentRecordError(Exception):
    """An arm record exists but cannot be trusted to describe this run.

    A record that is absent means the run has not started -- a normal state. A
    record that is present and unreadable means the page is about to show the
    static presets while looking exactly like that normal state. The two must not
    be confused, so the second one has a name.
    """


# The last broken-record message already logged. `get_cached_stats` re-collects
# every 3s, so a warning per call would print the same line hundreds of times. A
# successful read clears it, which lets a record that breaks again warn again.
_WARNED_RECORD: Optional[str] = None


def _read_arm_record(path: Path) -> list[dict[str, Any]]:
    """Read one arm record and check its shape; raise TournamentRecordError."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        # JSONDecodeError and UnicodeDecodeError are ValueError subclasses, so
        # truncation, bad bytes and bad JSON all land here.
        raise TournamentRecordError(f"{path}: {exc}") from exc
    if not isinstance(data, dict):
        raise TournamentRecordError(f"{path}: top level is not a JSON object")
    if "arms" not in data:
        raise TournamentRecordError(f"{path}: 'arms' is missing")
    record_arms = data["arms"]
    if not isinstance(record_arms, list):
        raise TournamentRecordError(f"{path}: 'arms' is not a list")
    for i, arm in enumerate(record_arms):
        if not isinstance(arm, dict):
            raise TournamentRecordError(f"{path}: arm {i} is not a JSON object")
        if not isinstance(arm.get("name"), str) or not arm["name"]:
            raise TournamentRecordError(f"{path}: arm {i} has no string 'name'")
        # An ABSENT field is fine -- the merge keeps the static placeholder. A
        # PRESENT-but-unusable one is not: `db_path: null` would be copied into
        # the arm and reach `ROOT / cfg["db"]` in analyze_arm_database, where it
        # raises TypeError and takes the page down with a traceback instead of
        # showing the warning this module exists to show.
        for field in ("db_path", "run_id"):
            if field in arm and (not isinstance(arm[field], str) or not arm[field]):
                raise TournamentRecordError(
                    f"{path}: arm {i} ({arm['name']}) has a non-string '{field}'"
                )
        if "dash_port" in arm:
            port = arm["dash_port"]
            if isinstance(port, bool) or not isinstance(port, int):
                raise TournamentRecordError(
                    f"{path}: arm {i} ({arm['name']}) has a non-integer 'dash_port'"
                )
    return record_arms


# What a run's own record says about the page: the arms it launched, the prose
# from its arms file, and how long it planned to run.
DEFAULT_TOURNAMENT_MINUTES = 720.0

# Colours for plan arms whose arms file names none. Ten entries, so a ten-arm
# run that carries no colour still reads as ten distinct lines on the charts.
_PLAN_ARM_COLORS = (
    "#00e676", "#00b0ff", "#e040fb", "#ffd600", "#ff6d00",
    "#76ff03", "#b388ff", "#ff5252", "#18ffff", "#ffab40",
)


def _tournament_root() -> Path:
    """The run-folder root, read through this module's ROOT so it is patchable."""
    return ROOT / "data" / "tournaments"


HEARTBEAT_MAX_AGE_SEC = 120.0


def _runtime_root() -> Path:
    """The runtime root, read through this module's ROOT so it is patchable."""
    return ROOT / "runtime"


def _is_pid_alive(pid: int | None) -> bool:
    """Check if process with pid is currently running."""
    if not pid or pid <= 0:
        return False
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.OpenProcess(0x1000, False, int(pid))
            if not handle:
                return False
            try:
                exit_code = wintypes.DWORD()
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return exit_code.value == 259
                return False
            finally:
                kernel32.CloseHandle(handle)
        else:
            try:
                os.kill(int(pid), 0)
                return True
            except ProcessLookupError:
                return False
            except PermissionError:
                return True
    except Exception:
        return False


def _extract_issue_from_text(text: str) -> Optional[str]:
    """Extract tournament issue number from run_id or db_path or folder name."""
    if not text:
        return None
    m = re.search(r"(?:shadow[-_]|(?:^|[\\/])i?)(?P<issue>\d+)(?:[-_]|\.db)", str(text))
    if m:
        return m.group("issue")
    return None


def resolve_running_tourney_key() -> Optional[str]:
    """Return the tournament key of any currently running tournament arms, or None.

    Scans runtime heartbeat files and active tournament arm plans to detect
    processes currently executing tournament shadow runs.
    """
    now = time.time()
    runtime_dir = _runtime_root()

    # 1. Direct scan of runtime heartbeat files
    if runtime_dir.is_dir():
        try:
            hb_files = sorted(
                runtime_dir.glob("shadow_run_*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            hb_files = []

        for hb_path in hb_files:
            try:
                with open(hb_path, "r", encoding="utf-8") as f:
                    hb = json.load(f)
                if not isinstance(hb, dict) or hb.get("finished", False):
                    continue
                hb_ts = hb.get("heartbeat_ts")
                if hb_ts is None or (now - float(hb_ts)) > HEARTBEAT_MAX_AGE_SEC:
                    continue
                pid = hb.get("pid")
                if pid and not _is_pid_alive(pid):
                    continue

                run_id = hb.get("run_id", "")
                issue = _extract_issue_from_text(run_id)
                if not issue:
                    db_path = hb.get("db_path", "")
                    issue = _extract_issue_from_text(db_path)
                if issue and issue in SERVED_TOURNAMENT_KEYS:
                    return str(issue)
            except Exception:
                continue

    # 2. Check each tournament's plan in data/tournaments to see if its arms are running
    tourn_dir = _tournament_root()
    if tourn_dir.is_dir():
        candidates: list[tuple[tuple[str, str], Path, Optional[str]]] = []
        try:
            for candidate in tourn_dir.iterdir():
                parsed = parse_tournament_run_dir_name(candidate)
                plan_path = candidate / "plan.json"
                if parsed is not None and plan_path.is_file():
                    folder_issue = str(parsed["issue"]) if parsed.get("issue") is not None else None
                    candidates.append(((parsed["stamp"], candidate.name), plan_path, folder_issue))
        except OSError:
            candidates = []
        candidates.sort(key=lambda c: c[0], reverse=True)

        for _, plan_path, folder_issue in candidates:
            try:
                with open(plan_path, "r", encoding="utf-8") as f:
                    plan = json.load(f)
                if not isinstance(plan, dict):
                    continue
                issue = plan.get("issue") or folder_issue
                if not issue or str(issue) not in SERVED_TOURNAMENT_KEYS:
                    continue
                arms = plan.get("arms", [])
                for arm in arms:
                    run_id = arm.get("run_id")
                    if not run_id:
                        continue
                    hb_path = runtime_dir / f"shadow_run_{run_id}.json"
                    if hb_path.is_file():
                        try:
                            with open(hb_path, "r", encoding="utf-8") as f:
                                hb = json.load(f)
                            if not hb.get("finished", False):
                                hb_ts = hb.get("heartbeat_ts")
                                if hb_ts and (now - float(hb_ts)) <= HEARTBEAT_MAX_AGE_SEC:
                                    pid = hb.get("pid")
                                    if not pid or _is_pid_alive(pid):
                                        return str(issue)
                        except Exception:
                            pass
            except Exception:
                continue
    return None


def resolve_current_setup_key() -> str:
    """Return the tournament key for the current setup.

    Prefers any currently running tournament. If nothing is currently running,
    follows the most recently launched plan record on disk. Falls back to
    CURRENT_SETUP_KEY (799) if no record exists.
    """
    running = resolve_running_tourney_key()
    if running and running in SERVED_TOURNAMENT_KEYS:
        return running
    newest = _newest_plan_record()
    if newest is not None:
        parsed = parse_tournament_run_dir_name(newest.parent)
        if parsed and parsed.get("issue"):
            issue_str = str(parsed["issue"])
            if issue_str in SERVED_TOURNAMENT_KEYS:
                return issue_str
    return CURRENT_SETUP_KEY


def _newest_plan_record() -> Optional[Path]:
    """The most recently launched `plan.json`, whatever issue it belongs to.

    Ranked on the folder's parsed launch stamp: the run an operator just started
    is the one the page must follow, and an issue number says nothing about
    launch order (a #799 run on top of a #608 one is newer, not "lower").
    """
    directory = _tournament_root()
    if not directory.is_dir():
        return None
    best: Optional[tuple[tuple[str, str], Path]] = None
    for candidate in directory.iterdir():
        parsed = parse_tournament_run_dir_name(candidate)
        plan = candidate / "plan.json"
        if parsed is None or not plan.is_file():
            continue
        rank = (parsed["stamp"], candidate.name)
        if best is None or rank > best[0]:
            best = (rank, plan)
    return best[1] if best else None


def _plan_identity(plan_path: Path) -> tuple[Optional[int], Optional[float]]:
    """`(issue, planned minutes)` for a run folder, from its folder or record."""
    parsed = parse_tournament_run_dir_name(plan_path.parent)
    issue = parsed["issue"] if parsed else None
    try:
        with open(plan_path, "r", encoding="utf-8") as f:
            plan = json.load(f)
    except (OSError, ValueError):
        return issue, None
    if not isinstance(plan, dict):
        return issue, None
    if issue is None and isinstance(plan.get("issue"), int):
        issue = plan["issue"]
    minutes = plan.get("minutes")
    if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or minutes <= 0:
        return issue, None
    return issue, float(minutes)


def _plan_arm_source(plan_path: Path) -> dict[str, dict[str, Any]]:
    """The plan's own arms file (`arms_source`), keyed by arm name.

    That file is where a run keeps the prose for its arms: the cockpit's #487
    presets describe a different ten arms, so a newer table has to read its
    hypotheses from the file the launcher actually executed. Best-effort -- a
    missing or broken file leaves names only, which is still true about the run.
    """
    try:
        with open(plan_path, "r", encoding="utf-8") as f:
            plan = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(plan, dict):
        return {}
    source = plan.get("arms_source")
    if not isinstance(source, str) or not source:
        return {}
    path = Path(source)
    if not path.is_absolute():
        path = ROOT / path
    try:
        with open(path, "r", encoding="utf-8") as f:
            spec = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(spec, list):
        return {}
    return {
        entry["name"]: entry
        for entry in spec
        if isinstance(entry, dict) and isinstance(entry.get("name"), str)
    }


def _tweak_text(env: Any) -> str:
    """`key = value` pairs for an arm's env, in the cockpit's own prose form."""
    if not isinstance(env, dict):
        return ""
    lines = []
    for key, value in env.items():
        lines.append(f"{str(key).removeprefix('BOT_').lower()} = {value}")
    return " \u00b7 ".join(lines)


def _note_text(value: Any) -> str:
    """An analyst note as page text: trimmed, and empty unless it is a string.

    `None`, a number and a whitespace-only string all mean "no note". Guessed
    prose would read as an analyst finding the arms file never made.
    """
    if not isinstance(value, str):
        return ""
    return value.strip()


def build_plan_arms(plan_path: Path) -> list[dict[str, Any]]:
    """The arms a plan actually launched, shaped like the static preset rows."""
    try:
        record_arms = _read_arm_record(plan_path)
    except TournamentRecordError:
        return []
    issue, _ = _plan_identity(plan_path)
    source = _plan_arm_source(plan_path)
    arms = []
    for i, record_arm in enumerate(record_arms):
        name = record_arm["name"]
        # A numbered run records `<name>-<n>` while the arms file names the base
        # arm; without the strip this row would lose its label, desc and notes.
        entry = source.get(name) or source.get(_strip_round_suffix(name)) or {}
        arms.append({
            "issue": issue if issue is not None else 0,
            "name": name,
            "label": entry.get("label") or name.replace("-", " ").title(),
            "port": record_arm.get("dash_port", 0),
            "db": record_arm.get("db_path", ""),
            "run_id": record_arm.get("run_id", ""),
            "env": record_arm.get("env", entry.get("env", {})),
            "color": entry.get("color") or _PLAN_ARM_COLORS[i % len(_PLAN_ARM_COLORS)],
            "desc": entry.get("desc") or entry.get("profile") or "",
            "worst_catalyst": _note_text(entry.get("worst_catalyst")),
            "worst_catalyst_desc": _note_text(entry.get("worst_catalyst_desc")),
            "hypothesized_tweak": (
                _note_text(entry.get("hypothesized_tweak"))
                or _tweak_text(entry.get("env") or record_arm.get("env"))
            ),
            "tweak_rationale": (
                _note_text(entry.get("tweak_rationale")) or _note_text(entry.get("thesis"))
            ),
        })
    return arms


def _resolve_plan_for_tourney_key(
    tourney_key: str,
    meta: dict[str, Any],
) -> Optional[Path]:
    """Select the plan record for a given tournament key consistently.

    A newer run of the page's own issue outranks the highest-numbered one:
    run number is not launch order, so a named run started today would
    otherwise merge last week's r1 db/port into today's rows.
    """
    latest_plan = latest_tournament_record(tourney_key, ROOT / "data" / "tournaments")
    if tourney_key in (CURRENT_SETUP_KEY, "843", "799", "905", "917", "923", "929", "938", "940"):
        newest = _newest_plan_record()
        newest_issue = _plan_identity(newest)[0] if newest is not None else None
        if newest is not None and newest_issue == int(meta.get("issue_primary") or 0):
            latest_plan = newest
    return latest_plan


def resolve_tournament_meta(
    tourney_key: str = CURRENT_SETUP_KEY,
) -> tuple[dict[str, Any], float]:
    """The page's branding and planned length, from this key's own newest run.

    The key names one page per issue, and each page reads its own issue's
    record: a #843 run launched into `data/tournaments/` must not repaint the
    #799 page with another experiment's arms under a "#843" header. Planned
    minutes come from that record, so progress is measured against the length
    the run was launched with, not a fixed 12 hours.

    When a run exists for this key, the meta reflects that run's label and
    arm count. Archive keys without a run keep their static meta.
    """
    meta = TOURNAMENTS.get(tourney_key, TOURNAMENTS[CURRENT_SETUP_KEY])
    plan = _resolve_plan_for_tourney_key(tourney_key, meta)
    if plan is not None:
        _, minutes = _plan_identity(plan)
        target_minutes = minutes or DEFAULT_TOURNAMENT_MINUTES
        # Build a dynamic meta reflecting this run's label and arm count.
        run_label = (parse_tournament_run_dir_name(plan.parent) or {}).get("name")
        run_label = (run_label or "run").replace("-", " ").title()
        arms = resolve_tournament_arms(tourney_key)
        arms_label = f"{len(arms)} Arms"
        branding = {key: value for key, value in meta.items() if key != "arms"}
        return {
            **branding,
            "id": str(tourney_key),
            "title": f"Issue #{tourney_key} Tournament \u2014 Live Run",
            "subtitle": f"{target_minutes / 60.0:g}-Hour Multi-Arm Shadow Evaluation | {run_label} | {arms_label}",
            "description": (
                f"Following the tournament record on disk: issue #{tourney_key}, {arms_label.lower()} "
                "launched from its own arms file. Comparative reading happens after the planned run "
                "length; the catalyst column is this run's own arms file, quoted as written."
            ),
            "issue_primary": int(tourney_key),
            "issue_secondary": int(tourney_key),
            "nav_label": f"#{tourney_key} {run_label} ({arms_label})",
        }, target_minutes
    return meta, DEFAULT_TOURNAMENT_MINUTES


def resolve_tournament_arms_with_status(
    tourney_key: str = CURRENT_SETUP_KEY,
) -> tuple[list[dict[str, Any]], Optional[str]]:
    """Resolve this key's arms, and say so when its record cannot be read.

    Returns `(arms, record_error)`. `record_error` is None when there is nothing
    to report: a key with no record (the run has not started), the static archive
    key, and a record that parsed. A broken record falls back to the static
    presets so the page stays usable, and the caller puts the error next to that
    table instead of letting it pass for "not started".
    """
    global _WARNED_RECORD
    meta = TOURNAMENTS.get(tourney_key, TOURNAMENTS[CURRENT_SETUP_KEY])
    default_arms = meta["arms"]
    # Unknown keys fall back to the static table; live keys resolve their plan file.
    if tourney_key not in ("843", "799", "905", "917", "923", "929", "938", "940"):
        return default_arms, None

    # Each page reads its own issue's record: the key in the URL is a page, and
    # a #843 run on disk says nothing about the ten #799 arms this page lists.
    # One resolver for every key: `latest_tournament_record` ranks by parsed
    # (run number, stamp), so a named run (#487's `--stamp current_setup_10arm`)
    # no longer wins or loses on the accident of `c` sorting after a digit.
    latest_plan = _resolve_plan_for_tourney_key(tourney_key, meta)
    if latest_plan is None:
        return default_arms, None
    try:
        record_arms = _read_arm_record(latest_plan)
    except TournamentRecordError as exc:
        message = str(exc)
        if message != _WARNED_RECORD:
            log.warning("Could not read tournament record %s", message)
            _WARNED_RECORD = message
        return default_arms, message
    _WARNED_RECORD = None
    # A run that launched its own arm set (#843's six-arm continuation) is read
    # as itself: merging it over the four static presets would hide two real arms
    # and keep two rows pointing at a different run's stores. A record with NEW
    # arm names (not in the static preset) is a standalone set; a subset of the
    # static preset's arms is a partial run and should merge.
    static_names = {base["name"] for base in default_arms}
    # Normalize record arm names by stripping numbered suffixes for legacy records
    # without arms_source, so "control-1" matches "control" in static preset.
    record_names = {_strip_round_suffix(a["name"]) for a in record_arms}
    if record_names - static_names:
        # Record has arm names the static preset doesn't know -> standalone set
        return build_plan_arms(latest_plan) or default_arms, None
    plan_arms: dict[str, Any] = {}
    for a in record_arms:
        plan_arms[a["name"]] = a
        plan_arms[_strip_round_suffix(a["name"])] = a
    merged = []
    for base in default_arms:
        name = base["name"]
        pa = plan_arms.get(name, plan_arms.get(_strip_round_suffix(name)))
        if pa is not None:
            merged.append({
                **base,
                "db": pa.get("db_path", base["db"]),
                "run_id": pa.get("run_id", base["run_id"]),
                "port": pa.get("dash_port", base["port"]),
                "env": pa.get("env", base.get("env", {})),
            })
        else:
            merged.append(base)
    return merged, None


def resolve_tournament_arms(tourney_key: str = CURRENT_SETUP_KEY) -> list[dict[str, Any]]:
    """Dynamically resolve tournament arms from active plan file or fall back to static presets.

    The arms only: `resolve_tournament_arms_with_status` carries the reason when a
    record exists but could not be read.
    """
    return resolve_tournament_arms_with_status(tourney_key)[0]


def nav_label_for(tourney_key: str) -> str:
    """The switcher pill's text: the run this page serves, named as it launched.

    A static "#843 Pair Test (4 Arms)" over a six-arm continuation run would name
    an experiment the page is not showing.
    """
    meta, _ = resolve_tournament_meta(tourney_key)
    return meta.get("nav_label") or f"#{tourney_key}"


def analyze_arm_database(cfg: dict[str, Any], target_minutes: float = 720.0) -> dict[str, Any]:
    """Extract comprehensive statistical, risk, and time series metrics for one arm."""
    db_path = ROOT / cfg["db"]
    if not db_path.exists():
        return {
            **cfg,
            "status": "missing_db",
            "trades_count": 0,
            "total_pnl": 0.0,
            "score": 0,
            "readiness": "NO_DATA"
        }

    try:
        with contextlib.closing(
            sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
        ) as conn:
            c = conn.cursor()

            # Check time window from queue_marks or quotes
            first_ts = 0.0
            last_ts = 0.0
            ts_row = c.execute(
                "SELECT min(ts), max(ts) FROM queue_marks WHERE run_id = ?",
                (cfg["run_id"],),
            ).fetchone()
            if ts_row and ts_row[0]:
                first_ts = ts_row[0]
                last_ts = ts_row[1] or first_ts
            else:
                q_row = c.execute(
                    "SELECT min(ts), max(ts) FROM quotes WHERE run_id = ?",
                    (cfg["run_id"],),
                ).fetchone()
                if q_row and q_row[0]:
                    first_ts = q_row[0]
                    last_ts = q_row[1] or first_ts

            elapsed_min = (last_ts - first_ts) / 60.0 if first_ts else 0.0
            rem_min = max(0.0, target_minutes - elapsed_min)

            # Fills and Orders
            fills_cnt = c.execute(
                "SELECT count(*) FROM fills WHERE run_id = ?", (cfg["run_id"],)
            ).fetchone()[0]
            orders_cnt = c.execute(
                "SELECT count(DISTINCT order_uuid) FROM fills WHERE run_id = ?", (cfg["run_id"],)
            ).fetchone()[0]

            # Closes (settlement / shadow_merge_note / '' are nontrade, not exits)
            nontrade_exclude = (SETTLEMENT, SHADOW_MERGE_NOTE, "")
            try:
                rows = c.execute(
                    "SELECT ts, method, realized_pnl, cost_basis, proceeds, shares, "
                    "condition_id, tx_hash FROM closes "
                    "WHERE run_id = ? AND method NOT IN ("
                    + ", ".join("?" for _ in nontrade_exclude) + ") "
                    "ORDER BY ts ASC",
                    (cfg["run_id"], *nontrade_exclude),
                ).fetchall()
            except sqlite3.Error:
                base_rows = c.execute(
                    "SELECT ts, method, realized_pnl, cost_basis, proceeds, shares FROM closes "
                    "WHERE run_id = ? AND method NOT IN ("
                    + ", ".join("?" for _ in nontrade_exclude) + ") "
                    "ORDER BY ts ASC",
                    (cfg["run_id"], *nontrade_exclude),
                ).fetchall()
                rows = [(*r, None, None) for r in base_rows]

            try:
                fill_rows = c.execute(
                    "SELECT f.venue_ts, f.recorded_ts, f.size, f.order_uuid, "
                    "o.condition_id, o.pair_id, o.side "
                    "FROM fills f LEFT JOIN orders o ON f.order_uuid = o.id "
                    "WHERE f.run_id = ?",
                    (cfg["run_id"],),
                ).fetchall()
            except sqlite3.Error:
                fill_rows = []

            # Order lifecycle funnel:
            #   quoted  -> every quote row placed (one per leg per requote cycle)
            #   filled  -> distinct orders that got at least one fill (legs, singular)
            #   merged  -> pairs completed via merge/shadow_merge; a merge counts
            #              as TWO singular filled orders (one UP + one DOWN leg)
            #   stop-loss -> single legs closed by the stop-loss family
            #                (single_buy_exit / naked_exit / ladder_exit)
            quoted_cnt = c.execute(
                "SELECT count(*) FROM quotes WHERE run_id = ?", (cfg["run_id"],)
            ).fetchone()[0]
    except sqlite3.Error as exc:
        return {
            **cfg,
            "status": "error",
            "error": str(exc),
            "trades_count": 0,
            "total_pnl": 0.0,
            "score": 0,
            "readiness": "ERROR"
        }

    pnls = [float(r[2] or 0.0) for r in rows]
    n = len(pnls)

    if n == 0:
        return {
            **cfg,
            "status": "no_trades",
            "elapsed_min": elapsed_min,
            "rem_min": rem_min,
            "progress_pct": min(100.0, round((elapsed_min / target_minutes) * 100.0, 1)),
            "fills_cnt": fills_cnt,
            "orders_cnt": orders_cnt,
            "quoted_cnt": quoted_cnt,
            "funnel": {
                "quoted": quoted_cnt,
                "filled": orders_cnt,
                "merged": 0,
                "merged_legs": 0,
                "stop_loss": 0,
            },
            "trades_count": 0,
            "merges_count": 0,
            "exits_count": 0,
            "stop_loss_exits_count": 0,
            "paired_completions_count": 0,
            "total_pnl": 0.0,
            "mean_pnl": 0.0,
            "std_pnl": 0.0,
            "sharpe_ratio": None,
            "sharpe_capital_basis": None,
            "win_rate": 0.0,
            "merge_rate": 0.0,
            "exit_rate": 0.0,
            "profit_factor": 0.0,
            "max_drawdown": 0.0,
            "score": 0,
            "readiness": "EVALUATING",
            "badge_cls": "badge-amber",
            "series": [],
        }

    wins = [p for p in pnls if p > 0.0]
    losses = [p for p in pnls if p < 0.0]
    merge_methods = MERGE_METHODS
    stop_loss_methods = RESCUE_METHODS
    merges = [r for r in rows if r[1] in merge_methods]
    stops = [r for r in rows if r[1] in stop_loss_methods]
    single_exits = n - len(merges)
    paired_completions = len(merges)

    # Per-KPI sample-size sufficiency — the same builder the :8799 dashboard
    # card reads (core_brain.kpi.kpi_sufficiency_table): one row per
    # estimator, each sized from the observations IT uses, at 90/95/99.
    # No arm-vs-arm comparison anywhere: each arm is measured against its
    # own observation count, and every row with >= 1 observation carries its
    # current calculated value so the matrix and cards show live averages
    # rather than hiding underpowered metrics behind a LOCKED label.
    _close_pcts: list[float] = []
    _stop_depth_pcts: list[float] = []
    for r in rows:
        _p = float(r[2] or 0.0)
        _c = float(r[3] or 0.0)
        if _c > 0:
            _pct = 100.0 * _p / _c
            _close_pcts.append(_pct)
            if r[1] in stop_loss_methods:
                _stop_depth_pcts.append(_pct)
    _merge_durations_s, _stop_durations_s = _fill_to_close_durations(rows, fill_rows)
    sufficiency_rows = _kpi_sufficiency_table(
        n_closes=n,
        expectancy_usd=list(pnls),
        return_pcts=list(_close_pcts),
        n_wins=len(wins),
        n_stops=len(stops),
        stop_depth_pcts=_stop_depth_pcts,
        n_merges=len(merges),
        merge_durations_s=_merge_durations_s,
        stop_durations_s=_stop_durations_s,
    )
    _enrich_sufficiency_rows(
        sufficiency_rows,
        expectancy_usd=pnls,
        return_pcts=_close_pcts,
        n_closes=n,
        n_wins=len(wins),
        n_stops=len(stops),
        stop_depth_pcts=_stop_depth_pcts,
        n_merges=len(merges),
        merge_durations_s=_merge_durations_s,
        stop_durations_s=_stop_durations_s,
    )

    tot_pnl = sum(pnls)
    mean_pnl = tot_pnl / n
    var = sum((p - mean_pnl) ** 2 for p in pnls) / (n - 1) if n > 1 else 0.0
    std_pnl = math.sqrt(var)

    win_rate = len(wins) / n
    merge_rate = len(merges) / n
    exit_rate = single_exits / n

    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (99.0 if gross_win > 0 else 1.0)

    # Max Drawdown & PnL Series
    cum = 0.0
    peak = 0.0
    mdd = 0.0
    series = []
    # Sample down to max 60 points for charting smoothness
    step = max(1, len(rows) // 60)
    for i, r in enumerate(rows):
        p = float(r[2] or 0.0)
        cum += p
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > mdd:
            mdd = dd
        if i % step == 0 or i == len(rows) - 1:
            rel_t = (r[0] - first_ts) / 60.0 if first_ts else 0.0
            series.append({
                "t": round(rel_t, 1),
                "cum": round(cum, 4),
                "pnl": round(p, 4)
            })

    # Value at Risk 95% (empirical 5th percentile)
    sorted_pnls = sorted(pnls)
    var_95 = sorted_pnls[max(0, int(0.05 * n))]

    # Sharpe Ratio (time-series, #875): equity from unthinned closes on the
    # starting bankroll, resampled to 5-minute buckets in core_brain/sharpe.py.
    _env_overrides = cfg.get("env", {}) or {}
    _raw_bank = _env_overrides.get("BOT_QUOTE_BANK_BASELINE_USD")
    try:
        _candidate = (
            float(_raw_bank)
            if str(_raw_bank or "").strip()
            else None
        )
    except (TypeError, ValueError):
        _candidate = None
    if _candidate is not None and math.isfinite(_candidate) and _candidate > 0:
        _starting_money = _candidate
        sharpe_capital_basis = "arm_env"
    else:
        _starting_money = _load_config().bot_quote_bank_baseline_usd
        sharpe_capital_basis = "assumed_default"
    _equity_points: list[tuple[float, float]] = []
    _close_ts = [float(r[0] or 0.0) for r in rows]
    if _close_ts:
        _open_t = min(first_ts or _close_ts[0], _close_ts[0])
        _equity_points.append((_open_t, _starting_money))
        _running_pnl = 0.0
        for _ts, _row in zip(_close_ts, rows):
            _running_pnl += float(_row[2] or 0.0)
            _equity_points.append((_ts, _starting_money + _running_pnl))
        _close_t = max(last_ts or _close_ts[-1], _close_ts[-1])
        if _close_t != _equity_points[-1][0]:
            _equity_points.append((_close_t, _starting_money + _running_pnl))
    sharpe_value = (
        sharpe_ratio(_equity_points, risk_free_rate_from_env())
        if _close_ts
        else None
    )

    # Monte Carlo 100-Trade Simulation (500 iterations)
    rng = random.Random(42 + cfg["port"])
    mc_ends = []
    for _ in range(500):
        mc_run = sum(rng.choice(pnls) for _ in range(100))
        mc_ends.append(mc_run)
    mc_ends.sort()
    mc_p5 = mc_ends[int(0.05 * len(mc_ends))]
    mc_p50 = mc_ends[int(0.50 * len(mc_ends))]
    mc_p95 = mc_ends[int(0.95 * len(mc_ends))]
    prob_positive_100 = sum(1 for x in mc_ends if x > 0) / len(mc_ends)

    # Live Readiness Scoring (0 to 100)
    # 1. Total PnL & EV (max 30 pts)
    pnl_pts = min(30.0, max(0.0, (tot_pnl / 15.0) * 30.0)) if tot_pnl > 0 else 0.0
    # 2. Merge Rate (max 25 pts)
    merge_pts = min(25.0, (merge_rate / 0.60) * 25.0)
    # 3. Low Drawdown (max 20 pts)
    dd_pts = max(0.0, 20.0 - (mdd / 1.5))
    # 4. Profit Factor (max 15 pts)
    pf_pts = min(15.0, (profit_factor / 1.5) * 15.0)
    # 5. Statistical Confidence (trades >= 50, max 10 pts)
    conf_pts = min(10.0, (n / 50.0) * 10.0)

    score = int(round(pnl_pts + merge_pts + dd_pts + pf_pts + conf_pts))

    if score >= 75 and tot_pnl > 5.0 and prob_positive_100 >= 0.70:
        readiness = "READY FOR LIVE"
        badge_cls = "badge-green"
    elif score >= 55 and tot_pnl >= 0.0:
        readiness = "NEEDS TUNING"
        badge_cls = "badge-amber"
    else:
        readiness = "DO NOT DEPLOY"
        badge_cls = "badge-red"

    return {
        **cfg,
        "status": "ok",
        "elapsed_min": elapsed_min,
        "rem_min": rem_min,
        "progress_pct": min(100.0, round((elapsed_min / target_minutes) * 100.0, 1)),
        "fills_cnt": fills_cnt,
        "orders_cnt": orders_cnt,
        "quoted_cnt": quoted_cnt,
        "funnel": {
            "quoted": quoted_cnt,
            "filled": orders_cnt,
            "merged": len(merges),
            "merged_legs": len(merges) * 2,
            "stop_loss": len(stops),
        },
        "trades_count": n,
        "sufficiency_rows": sufficiency_rows,
        "merges_count": len(merges),
        "exits_count": single_exits,
        "stop_loss_exits_count": len(stops),
        "paired_completions_count": paired_completions,
        "total_pnl": round(tot_pnl, 4),
        "mean_pnl": round(mean_pnl, 4),
        "std_pnl": round(std_pnl, 4),
        "win_rate": round(win_rate * 100.0, 1),
        "merge_rate": round(merge_rate * 100.0, 1),
        "exit_rate": round(exit_rate * 100.0, 1),
        "profit_factor": round(profit_factor, 2),
        "max_drawdown": round(mdd, 2),
        "max_win": round(max(pnls) if pnls else 0.0, 4),
        "max_loss": round(min(pnls) if pnls else 0.0, 4),
        "avg_win": round(sum(wins) / len(wins), 4) if wins else 0.0,
        "avg_loss": round(sum(losses) / len(losses), 4) if losses else 0.0,
        "var_95": round(var_95, 4),
        "sharpe_ratio": round(sharpe_value, 2) if sharpe_value is not None else None,
        "sharpe_capital_basis": sharpe_capital_basis,
        "mc_p5": round(mc_p5, 2),
        "mc_p50": round(mc_p50, 2),
        "mc_p95": round(mc_p95, 2),
        "prob_positive_100": round(prob_positive_100 * 100.0, 1),
        "score": score,
        "readiness": readiness,
        "badge_cls": badge_cls,
        "series": series,
    }


#: Sharpe Ratio gate levels (#875). These numbers were tuned for the retired
#: per-trade proxy and now apply to the time-series metric — re-check them on
#: future runs; do not retune on history.
SHARPE_TARGET = 2.70
SHARPE_MARGINAL = 2.20


def _fmt_duration_sec(sec: float) -> str:
    """Format fill-to-close duration in seconds into a compact human string."""
    if sec < 60.0:
        if abs(sec - round(sec)) < 0.05:
            return f"{int(round(sec))}s"
        return f"{sec:.1f}s"
    if sec < 3600.0:
        total_s = int(round(sec))
        m, s = divmod(total_s, 60)
        return f"{m}m {s:02d}s"
    total_m = int(round(sec / 60.0))
    h, m = divmod(total_m, 60)
    return f"{h}h {m:02d}m"


def _fill_to_close_durations(
    close_rows: list[tuple[Any, ...]],
    fill_rows: list[tuple[Any, ...]],
) -> tuple[list[float], list[float]]:
    """Compute (merge_durations_s, stop_durations_s) from BUY fills and trade closes.

    Links each close to its opening BUY fill timestamp (first fill of the pair):
    1. Directly by `pair_id` when `closes.tx_hash` carries `pair_id` (or `pair_id:N`).
    2. Otherwise to the earliest unconsumed BUY fill group on the same `condition_id`
       whose opening fill timestamp is at or before `close.ts`.
    """
    if not close_rows or not fill_rows:
        return [], []

    pair_first_ts: dict[str, float] = {}
    pair_cid: dict[str, str] = {}
    pair_shares: dict[str, float] = {}
    for v_ts, r_ts, sz, order_uuid, cid, pid, side in fill_rows:
        if side and str(side).upper() != "BUY":
            continue
        raw_ts = v_ts if v_ts is not None else r_ts
        if raw_ts is None or not cid:
            continue
        try:
            ts_val = float(raw_ts)
        except (TypeError, ValueError):
            continue
        ts_s = ts_val / 1000.0 if ts_val > 1e11 else ts_val
        key = str(pid) if pid else f"order:{order_uuid}"
        if key not in pair_first_ts or ts_s < pair_first_ts[key]:
            pair_first_ts[key] = ts_s
            pair_cid[key] = str(cid)
        pair_shares[key] = pair_shares.get(key, 0.0) + max(0.0, float(sz or 0.0))

    if not pair_first_ts:
        return [], []

    explicit_pid_by_idx: dict[int, str] = {}
    for idx, r in enumerate(close_rows):
        tx_hash = r[7] if len(r) > 7 else None
        if tx_hash:
            pid = str(tx_hash).split(":")[0]
            if pid in pair_first_ts:
                explicit_pid_by_idx[idx] = pid
                sh = max(0.0, float(r[5] or 0.0))
                consumed = 2.0 * sh if r[1] in MERGE_METHODS else sh
                if consumed > 0:
                    pair_shares[pid] = max(0.0, pair_shares.get(pid, 0.0) - consumed)
                else:
                    pair_shares[pid] = 0.0

    cands_by_cid: dict[str, list[list[Any]]] = {}
    for key, f_ts in pair_first_ts.items():
        rem_sh = pair_shares.get(key, 0.0)
        if rem_sh > 1e-6:
            cands_by_cid.setdefault(pair_cid[key], []).append([f_ts, key, rem_sh])
    for cid_list in cands_by_cid.values():
        cid_list.sort(key=lambda item: item[0])

    merge_durs: list[float] = []
    stop_durs: list[float] = []
    for idx, r in enumerate(close_rows):
        c_ts = float(r[0] or 0.0)
        method = r[1]
        sh = max(0.0, float(r[5] or 0.0))
        cid = str(r[6]) if len(r) > 6 and r[6] else ""
        f_ts: float | None = None
        if idx in explicit_pid_by_idx:
            f_ts = pair_first_ts.get(explicit_pid_by_idx[idx])
        elif cid and cid in cands_by_cid:
            for cand in cands_by_cid[cid]:
                if cand[0] <= c_ts and cand[2] > 1e-6:
                    f_ts = float(cand[0])
                    consumed = 2.0 * sh if method in MERGE_METHODS else sh
                    cand[2] = max(0.0, cand[2] - consumed) if consumed > 0 else 0.0
                    break
        if f_ts is not None and c_ts >= f_ts:
            dur = c_ts - f_ts
            if method in MERGE_METHODS:
                merge_durs.append(dur)
            elif method in RESCUE_METHODS:
                stop_durs.append(dur)
    return merge_durs, stop_durs


def _enrich_sufficiency_rows(
    rows: list[dict[str, Any]],
    *,
    expectancy_usd: list[float],
    return_pcts: list[float],
    n_closes: int,
    n_wins: int,
    n_stops: int,
    stop_depth_pcts: list[float],
    n_merges: int,
    merge_durations_s: list[float],
    stop_durations_s: list[float],
) -> None:
    """Attach `value` and `formatted_value` to each KPI row when base_n >= 1."""
    mean_or_none = lambda vals: (sum(vals) / len(vals)) if vals else None
    rate_or_none = lambda num, den: (100.0 * num / den) if den > 0 else None

    val_map: dict[str, tuple[float | None, str | None]] = {}
    exp_val = mean_or_none(expectancy_usd)
    val_map["expectancy_usd"] = (
        round(exp_val, 4) if exp_val is not None else None,
        (f"+${exp_val:.2f}" if exp_val >= 0 else f"-${abs(exp_val):.2f}")
        if exp_val is not None else None,
    )
    ret_val = mean_or_none(return_pcts)
    val_map["avg_return_pct"] = (
        round(ret_val, 2) if ret_val is not None else None,
        f"{ret_val:+.1f}%" if ret_val is not None else None,
    )
    win_val = rate_or_none(n_wins, n_closes)
    val_map["win_rate"] = (
        round(win_val, 2) if win_val is not None else None,
        f"{win_val:.1f}%" if win_val is not None else None,
    )
    stop_rate_val = rate_or_none(n_stops, n_closes)
    val_map["stop_loss_rate"] = (
        round(stop_rate_val, 2) if stop_rate_val is not None else None,
        f"{stop_rate_val:.1f}%" if stop_rate_val is not None else None,
    )
    depth_val = mean_or_none(stop_depth_pcts)
    val_map["avg_stop_loss_depth_pct"] = (
        round(depth_val, 2) if depth_val is not None else None,
        f"{depth_val:+.1f}%" if depth_val is not None else None,
    )
    merge_rate_val = rate_or_none(n_merges, n_closes)
    val_map["merge_rate"] = (
        round(merge_rate_val, 2) if merge_rate_val is not None else None,
        f"{merge_rate_val:.1f}%" if merge_rate_val is not None else None,
    )
    m_dur_val = mean_or_none(merge_durations_s)
    val_map["avg_duration_merge"] = (
        round(m_dur_val, 2) if m_dur_val is not None else None,
        _fmt_duration_sec(m_dur_val) if m_dur_val is not None else None,
    )
    s_dur_val = mean_or_none(stop_durations_s)
    val_map["avg_duration_stop"] = (
        round(s_dur_val, 2) if s_dur_val is not None else None,
        _fmt_duration_sec(s_dur_val) if s_dur_val is not None else None,
    )

    for row in rows:
        key = str(row.get("key", ""))
        if key in val_map:
            raw_v, fmt_v = val_map[key]
            row["value"] = raw_v
            row["formatted_value"] = fmt_v


def _row_formatted_value(row: dict[str, Any] | None) -> str | None:
    """Resolve the display string for a sufficiency row's current value."""
    if not row:
        return None
    fmt = row.get("formatted_value")
    if isinstance(fmt, str) and fmt.strip():
        return fmt.strip()
    base_n = row.get("base_n") or 0
    if row.get("type") == "proportion" and base_n > 0 and row.get("successes") is not None:
        return f"{100.0 * float(row['successes']) / float(base_n):.1f}%"
    return None


def _row_numeric_value(row: dict[str, Any] | None) -> float | None:
    """Extract numeric KPI value for green/yellow/red health classification."""
    if not row:
        return None
    raw = row.get("value")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    base_n = row.get("base_n") or 0
    if row.get("type") == "proportion" and base_n > 0 and row.get("successes") is not None:
        return 100.0 * float(row["successes"]) / float(base_n)
    fmt = _row_formatted_value(row)
    if not fmt:
        return None
    s = fmt.strip().lower()
    # Duration string (e.g. "5m 11s", "1h 02m", "18.9s")
    if s.endswith("s") or s.endswith("m"):
        try:
            total = 0.0
            for part in s.split():
                if part.endswith("h"):
                    total += float(part[:-1]) * 3600.0
                elif part.endswith("m"):
                    total += float(part[:-1]) * 60.0
                elif part.endswith("s"):
                    total += float(part[:-1])
                else:
                    return None
            return total
        except ValueError:
            pass
    cleaned = fmt.replace("$", "").replace("%", "").replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _kpi_value_tone(row: dict[str, Any] | None, is_sufficient: bool = False) -> str:
    """Classify a KPI's current value into green (good), yellow (warn), or red (bad)."""
    if not row:
        return ""
    val = _row_numeric_value(row)
    if val is None:
        if is_sufficient:
            return "kpi-val-good"
        if (row.get("base_n") or 0) > 0:
            return "kpi-val-warn"
        return ""
    key = str(row.get("key", ""))
    if key == "expectancy_usd":
        if val < 0.0:
            return "kpi-val-bad"
        return "kpi-val-good" if val >= 0.15 else "kpi-val-warn"
    if key == "avg_return_pct":
        if val < 0.0:
            return "kpi-val-bad"
        return "kpi-val-good" if val >= 2.0 else "kpi-val-warn"
    if key == "win_rate":
        if val < 50.0:
            return "kpi-val-bad"
        return "kpi-val-good" if val >= 65.0 else "kpi-val-warn"
    if key == "stop_loss_rate":
        if val <= 25.0:
            return "kpi-val-good"
        return "kpi-val-warn" if val <= 40.0 else "kpi-val-bad"
    if key == "avg_stop_loss_depth_pct":
        depth = abs(val)
        if depth <= 8.0:
            return "kpi-val-good"
        return "kpi-val-warn" if depth <= 15.0 else "kpi-val-bad"
    if key == "merge_rate":
        if val < 45.0:
            return "kpi-val-bad"
        return "kpi-val-good" if val >= 60.0 else "kpi-val-warn"
    if key == "avg_duration_merge":
        if val <= 180.0:
            return "kpi-val-good"
        return "kpi-val-warn" if val <= 420.0 else "kpi-val-bad"
    if key == "avg_duration_stop":
        if val <= 180.0:
            return "kpi-val-good"
        return "kpi-val-warn" if val <= 360.0 else "kpi-val-bad"
    if val < 0.0:
        return "kpi-val-bad"
    return "kpi-val-good" if val > 0.0 else "kpi-val-warn"


def _sufficiency_95_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Condense sufficiency rows to their 95% striving level for display.

    One dict per KPI: label, observation base and count, the arm's own
    required N at 95% (None when unmeasured), remaining, progress, and the
    current calculated value with its green/yellow/red health class. Shared by
    the server-side card HTML and the live JS repaint below so first paint and
    poll updates can never disagree."""
    out: list[dict[str, Any]] = []
    for row in rows or []:
        lv95 = next(
            (lv for lv in (row.get("levels") or []) if lv.get("confidence_pct") == 95), {}) or {}
        sufficient = row.get("sufficient_95") is True
        fmt_val = _row_formatted_value(row)
        base_n = row.get("base_n", 0) or 0
        tone = _kpi_value_tone(row, is_sufficient=sufficient)
        if sufficient:
            verdict = fmt_val if fmt_val is not None else "SUFFICIENT"
            badge = tone or "kpi-val-good"
        elif fmt_val is not None:
            verdict, badge = fmt_val, tone or "kpi-val-warn"
        elif lv95.get("required_n") is None:
            verdict, badge = "—", ""
        else:
            verdict, badge = f"n={base_n}", "kpi-val-warn"
        out.append({
            "key": row.get("key", ""),
            "label": row.get("label", row.get("key", "")),
            "base": row.get("base", "closes"),
            "base_n": base_n,
            "required_95": lv95.get("required_n"),
            "remaining_95": lv95.get("remaining_n"),
            "progress_95": lv95.get("progress_pct"),
            "formatted_value": fmt_val,
            "verdict": verdict,
            "badge": badge,
        })
    return out


def _sufficiency_mini_html(rows: list[dict[str, Any]]) -> str:
    """Compact per-KPI sufficiency table for one arm card (@95% striving level)."""
    summaries = _sufficiency_95_rows(rows)
    if not summaries:
        return (
            '<div style="font-size: 11.5px; color: var(--text-muted);">'
            'Per-KPI Sufficiency: unmeasured — arm has not reported yet.</div>'
        )
    body = []
    for s in summaries:
        req = f"{s['required_95']:,}" if s["required_95"] is not None else "—"
        prog = s["progress_95"]
        bar = (
            f'<div style="background: rgba(255,255,255,0.08); border-radius: 4px; height: 6px; min-width: 48px;">'
            f'<div style="background: var(--green); height: 6px; border-radius: 4px; width: {prog}%;"></div></div>'
            if isinstance(prog, (int, float)) else '<span style="color: var(--text-muted);">—</span>'
        )
        val_html = (
            f'<span class="kpi-val {s["badge"]} tabular">{html.escape(str(s["verdict"]))}</span>'
            if s["badge"] else '<span style="color: var(--text-muted);">—</span>'
        )
        body.append(
            f'<tr><td style="padding: 3px 6px 3px 0;">{html.escape(str(s["label"]))}</td>'
            f'<td class="tabular" style="padding: 3px 6px; white-space: nowrap; text-align: right;">{val_html}</td>'
            f'<td class="tabular" style="padding: 3px 6px; text-align: right; color: var(--text-muted);">{req}</td>'
            f'<td style="padding: 3px 6px;">{bar}</td>'
            f'<td class="tabular" style="padding: 3px 0 3px 6px; white-space: nowrap; text-align: right; color: var(--text-muted);">{s["base_n"]} {html.escape(str(s["base"]))}</td></tr>'
        )
    return (
        '<div style="margin: 8px 0 4px; padding-top: 8px; border-top: 1px solid var(--border);">'
        '<div style="font-size: 11px; font-weight: 700; color: var(--text-muted); '
        'text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px;">'
        'Per-KPI Sufficiency @95%</div>'
        '<table style="width: 100%; font-size: 11.5px; border-collapse: collapse;">' + "".join(body) + '</table></div>'
    )


def _dg_kpi_rows(arms: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Union of KPI sufficiency rows across arms, first-seen order.

    Each entry is {key, label}. Arms that never reported contribute no
    rows; a KPI measured by only some arms still gets a matrix row, with
    unmeasured cells for the arms that lack it."""
    seen: dict[str, str] = {}
    for arm in arms or []:
        for row in (arm.get("sufficiency_rows") or []):
            key = str(row.get("key", ""))
            if key and key not in seen:
                seen[key] = str(row.get("label", key))
    return [{"key": k, "label": v} for k, v in seen.items()]


def _dg_level(row: dict[str, Any], confidence: int) -> dict[str, Any]:
    """One confidence level out of a sufficiency row ({} when absent)."""
    for lv in (row.get("levels") or []):
        if lv.get("confidence_pct") == confidence:
            return lv
    return {}


def _dg_cell_html(row: dict[str, Any] | None, confidence: int) -> str:
    """One KPI x arm matrix cell at the selected confidence level."""
    if not row:
        return '<span style="color: var(--text-muted);">unmeasured</span>'
    lv = _dg_level(row, confidence)
    req = lv.get("required_n")
    base_n = row.get("base_n", 0) or 0
    prog = lv.get("progress_pct")
    fmt_val = _row_formatted_value(row)
    is_sufficient = (
        (row.get("sufficient_95") is True and confidence == 95)
        or (confidence != 95 and isinstance(prog, (int, float)) and prog >= 100)
    )
    tone = _kpi_value_tone(row, is_sufficient=is_sufficient)
    if is_sufficient:
        label = fmt_val if fmt_val is not None else "SUFFICIENT"
        val_html = f'<span class="kpi-val {tone or "kpi-val-good"} tabular">{html.escape(str(label))}</span>'
    elif fmt_val is not None:
        val_html = f'<span class="kpi-val {tone or "kpi-val-warn"} tabular">{html.escape(str(fmt_val))}</span>'
    elif req is None:
        return '<span style="color: var(--text-muted);">unmeasured</span>'
    else:
        val_html = f'<span class="kpi-val kpi-val-warn tabular">n={base_n}</span>'
    have_need = (
        f'<span class="tabular">{base_n} / {req:,}</span>' if req is not None
        else f'<span class="tabular">{base_n} / —</span>'
    )
    bar = (
        f'<div style="background: rgba(255,255,255,0.08); border-radius: 4px; height: 6px; margin-top: 3px;">'
        f'<div style="background: var(--green); height: 6px; border-radius: 4px; width: {prog}%;"></div></div>'
        if isinstance(prog, (int, float)) else ''
    )
    return f'{val_html}<div style="font-size: 10.5px; color: var(--text-muted); margin-top: 2px;">{have_need}</div>{bar}'


def _dg_matrix_html(arms: list[dict[str, Any]], confidence: int = 95) -> str:
    """KPI (rows) x arm (columns) sufficiency matrix at one confidence level.

    Server pre-renders the 95% view for first paint; the confidence toggle
    and the live poll re-render from the same `sufficiency_rows` payload."""
    kpi_rows = _dg_kpi_rows(arms)
    if not kpi_rows or not arms:
        return (
            '<div style="font-size: 12px; color: var(--text-muted);">'
            'KPI matrix unmeasured — no arm has reported sufficiency rows yet.</div>'
        )
    rows_by_arm = {
        a.get("name", ""): {r.get("key"): r for r in (a.get("sufficiency_rows") or [])}
        for a in arms
    }
    head = [f"<th style=\"text-align: left;\">KPI x Arm @ {confidence}%</th>"]
    for a in arms:
        head.append(
            f'<th data-dg-matrix-arm="{html.escape(a.get("name", ""))}">'
            f'{html.escape(a.get("label", a.get("name", "")))}</th>'
        )
    body = []
    for kpi in kpi_rows:
        cells = [f'<td style="text-align: left;">{html.escape(kpi["label"])}</td>']
        for a in arms:
            row = rows_by_arm.get(a.get("name", ""), {}).get(kpi["key"])
            cells.append(
                f'<td data-dg-matrix-arm="{html.escape(a.get("name", ""))}" '
                f'data-dg-kpi="{html.escape(kpi["key"])}">'
                f'{_dg_cell_html(row, confidence)}</td>'
            )
        body.append("<tr>" + "".join(cells) + "</tr>")
    return (
        '<table id="dg-matrix-table" style="width: 100%; font-size: 11.5px; '
        'border-collapse: collapse;">' + "<thead><tr>" + "".join(head) + "</tr></thead>"
        + "<tbody>" + "".join(body) + "</tbody></table>"
        + f'<div style="font-size: 11px; color: var(--text-muted); margin-top: 6px;">'
        f'Current value and Have / Need observations at {confidence}% confidence. '
        'Values are colored green (good), yellow (average), or red (bad) by KPI range; '
        'unmeasured means no observations yet.</div>'
    )


def _dg_filter_html(arms: list[dict[str, Any]]) -> str:
    """Arm visibility checkboxes: one or many arms, default all on."""
    boxes = []
    for a in arms or []:
        name = html.escape(a.get("name", ""))
        label = html.escape(a.get("label", a.get("name", "")))
        boxes.append(
            f'<label style="display: inline-flex; align-items: center; gap: 6px; '
            f'font-size: 12px; margin-right: 14px; cursor: pointer;">'
            f'<input type="checkbox" data-dg-arm-toggle="{name}" checked> {label}</label>'
        )
    return "".join(boxes)


def _arm_sufficiency_row(arm: dict[str, Any] | None, key: str) -> dict[str, Any] | None:
    """One sufficiency row off an arm, or None when the arm never ran.

    Hand-built arm dicts (tests, missing DBs) carry no `sufficiency_rows`:
    that reads as unmeasured, never as a fabricated zero."""
    for row in ((arm or {}).get("sufficiency_rows") or []):
        if row.get("key") == key:
            return row
    return None


def _arm_gate_set(arm: dict[str, Any]) -> dict[str, Any]:
    """Four own-sample gates for ONE arm: expectancy sufficiency at 95%,
    Sharpe, profit factor, max drawdown. No cross-arm comparison anywhere:
    every number is the arm's own."""
    n = arm.get("trades_count", 0) or 0

    exp_row = _arm_sufficiency_row(arm, "expectancy_usd")
    exp_95 = {lv.get("confidence_pct"): lv for lv in (exp_row.get("levels") or [])} if exp_row else {}
    exp_95 = exp_95.get(95, {})
    target_n = exp_95.get("required_n")
    remaining_n = exp_95.get("remaining_n")
    sufficient = bool(exp_row) and exp_row.get("sufficient_95") is True
    power_ratio = round((n / target_n) * 100.0, 1) if target_n else 0.0

    if n == 0:
        power = {"status": "NO_DATA", "badge": "badge-amber",
                 "text": "0 Closed Trades — Rehearsal Initializing",
                 "current": 0, "target": None}
    elif sufficient:
        power = {"status": "PASS", "badge": "badge-green",
                 "text": (f"N = {n:,} / {target_n:,} ({power_ratio}%) — "
                           "PnL Expectancy Sufficient at 95% (own-sample sizing)"),
                 "current": n, "target": target_n}
    elif target_n:
        power = {"status": "UNDERPOWERED", "badge": "badge-amber",
                 "text": (f"N = {n:,} / {target_n:,} ({power_ratio}%) — "
                           f"Expectancy Inconclusive at 95% ({remaining_n:,} to go); Evaluating Risk Shape"),
                 "current": n, "target": target_n}
    else:
        power = {"status": "UNDERPOWERED", "badge": "badge-amber",
                 "text": (f"N = {n} — Expectancy Not Yet Measurable "
                           "(need >= 2 closes); Evaluating Risk Shape"),
                 "current": n, "target": None}

    target_sharpe = SHARPE_TARGET
    observed_raw = arm.get("sharpe_ratio")
    observed_sharpe = observed_raw if isinstance(observed_raw, (int, float)) else None
    if n == 0 or observed_sharpe is None:
        sharpe = {"status": "NO_DATA", "badge": "badge-amber",
                  "text": f"Target: \u2265 {target_sharpe:.2f} | Observed: NO_DATA",
                  "current": None, "target": target_sharpe}
    elif observed_sharpe >= target_sharpe:
        sharpe = {"status": "PASS", "badge": "badge-green",
                  "text": (f"Target: \u2265 {target_sharpe:.2f} | Observed: {observed_sharpe:.2f} "
                            f"(+{((observed_sharpe / target_sharpe) - 1) * 100:.1f}%)"),
                  "current": observed_sharpe, "target": target_sharpe}
    elif observed_sharpe >= SHARPE_MARGINAL:
        sharpe = {"status": "MARGINAL", "badge": "badge-amber",
                  "text": (f"Target: \u2265 {target_sharpe:.2f} | Observed: {observed_sharpe:.2f} "
                            "(Within 20%)"),
                  "current": observed_sharpe, "target": target_sharpe}
    else:
        sharpe = {"status": "FAIL", "badge": "badge-red",
                  "text": f"Target: \u2265 {target_sharpe:.2f} | Observed: {observed_sharpe:.2f} (Below threshold)",
                  "current": observed_sharpe, "target": target_sharpe}

    target_pf = 2.50
    observed_pf = arm.get("profit_factor", 0.0) or 0.0
    if n == 0:
        pf = {"status": "NO_DATA", "badge": "badge-amber",
              "text": f"Target: \u2265 {target_pf:.2f} | Observed: NO_DATA",
              "current": None, "target": target_pf}
    elif observed_pf >= target_pf:
        pf = {"status": "PASS", "badge": "badge-green",
              "text": f"Target: \u2265 {target_pf:.2f} | Observed: {observed_pf:.2f}",
              "current": observed_pf, "target": target_pf}
    elif observed_pf >= 1.80:
        pf = {"status": "MARGINAL", "badge": "badge-amber",
              "text": f"Target: \u2265 {target_pf:.2f} | Observed: {observed_pf:.2f}",
              "current": observed_pf, "target": target_pf}
    else:
        pf = {"status": "FAIL", "badge": "badge-red",
              "text": f"Target: \u2265 {target_pf:.2f} | Observed: {observed_pf:.2f}",
              "current": observed_pf, "target": target_pf}

    target_mdd = 4.00
    observed_mdd = arm.get("max_drawdown", 0.0) or 0.0
    if n == 0:
        mdd = {"status": "NO_DATA", "badge": "badge-amber",
               "text": f"Target: < ${target_mdd:.2f} | Observed: NO_DATA",
               "current": None, "target": target_mdd}
    elif observed_mdd < target_mdd:
        mdd = {"status": "PASS", "badge": "badge-green",
               "text": f"Target: < ${target_mdd:.2f} | Observed: ${observed_mdd:.2f} (Protected)",
               "current": observed_mdd, "target": target_mdd}
    elif observed_mdd <= 5.50:
        mdd = {"status": "MARGINAL", "badge": "badge-amber",
               "text": f"Target: < ${target_mdd:.2f} | Observed: ${observed_mdd:.2f}",
               "current": observed_mdd, "target": target_mdd}
    else:
        mdd = {"status": "FAIL", "badge": "badge-red",
               "text": f"Target: < ${target_mdd:.2f} | Observed: ${observed_mdd:.2f} (Excessive Drawdown)",
               "current": observed_mdd, "target": target_mdd}

    return {"power": power, "sharpe": sharpe, "pf": pf, "mdd": mdd,
            "trades_count": n, "expectancy_sufficient": sufficient,
            "expectancy_target_n": target_n, "expectancy_remaining_n": remaining_n}


def _arm_verdict(arm: dict[str, Any], gates: dict[str, Any]) -> tuple[str, str, str]:
    """One arm's verdict from its own gates. Neutral copy: no arm is the
    test subject and none inherits another's rank -- each configuration is
    judged on its own settings."""
    n = gates["trades_count"]
    sharpe = gates["sharpe"]["status"]
    mdd = gates["mdd"]["status"]
    pf = gates["pf"]["status"]
    sufficient = gates["expectancy_sufficient"]
    target_n = gates["expectancy_target_n"]
    remaining_n = gates["expectancy_remaining_n"]
    if n == 0:
        return ("PENDING REHEARSAL DATA", "badge-amber",
                "No completed trades recorded for this arm yet.")
    if sharpe == "FAIL" or mdd == "FAIL":
        return ("REJECTED: CRITICAL RISK GATES FAILED", "badge-red",
                "Observed Sharpe Ratio (< 2.20) or max drawdown (> $5.50) fails this arm's risk limits.")
    if sufficient:
        if sharpe == "PASS" and mdd == "PASS" and pf == "PASS":
            return ("CLEARED: ALL GATES PASS AT 95%", "badge-green",
                    f"PnL expectancy sufficient at 95% (N={n:,}); Sharpe, profit factor and drawdown inside limits.")
        return ("NOT CLEARED: GATED METRIC BELOW STANDARD", "badge-red",
                "Expectancy is sufficient at 95% but the arm fails one or more risk gates.")
    if sharpe == "PASS" and mdd == "PASS":
        if target_n:
            return ("PROVISIONAL: RISK SHAPE HOLDS", "badge-green",
                    f"Sharpe and drawdown hold inside limits; PnL expectancy inconclusive at 95% ({remaining_n:,} closes to go).")
        return ("PROVISIONAL: RISK SHAPE HOLDS", "badge-green",
                "Risk shape holds inside limits; PnL expectancy not yet measurable (need >= 2 closes).")
    return ("INCONCLUSIVE: SAMPLE UNDERPOWERED", "badge-amber",
            f"Sample size (N={n}) is statistically underpowered; risk shape still evaluating.")


def compute_decision_gates(arms_data: list[dict[str, Any]], tourney_key: str = "843") -> dict[str, Any]:
    """Per-arm decision gates plus one run-level volume check.

    Every arm gets its own verdict from its own gates:
    - Gate 1: expectancy sufficiency at 95% (own-sample sizing, required N
      adapts to the arm -- no fixed constant, no arm-vs-arm comparison)
    - Gate 2: Sharpe Ratio (target >= 2.70)
    - Gate 3: Profit Factor (target >= 2.50)
    - Gate 4: Max Drawdown (target < $4.00)
    The run-level volume check compares fill counts between the configured
    roles (it is explicitly a comparison, not a verdict on any arm).
    """
    arm_map = DECISION_GATE_ARM_MAP.get(tourney_key, DECISION_GATE_ARM_MAP["843"])
    arm_verdicts = []
    for arm in arms_data:
        gates = _arm_gate_set(arm)
        verdict, badge, desc = _arm_verdict(arm, gates)
        arm_verdicts.append({
            "name": arm.get("name", ""),
            "label": arm.get("label", arm.get("name", "")),
            "verdict": verdict,
            "badge": badge,
            "desc": desc,
            "trades_count": gates["trades_count"],
            "gates": {"power": gates["power"], "sharpe": gates["sharpe"],
                      "pf": gates["pf"], "mdd": gates["mdd"]},
        })

    # Run-level volume check: does the volume arm's knob choke fills vs the
    # reference role? Informational for the run, never a verdict on an arm.
    # Numbered runs store suffixed names (`all-but-live-sports-1`): strip
    # the round suffix before matching roles, or every numbered run reads
    # NO_DATA even with trades on both arms (#924 review).
    vol_arm = next((a for a in arms_data if arm_map["volume_arm_pattern"] in a.get("name", "")), None)
    ref_arm = next(
        (
            a for a in arms_data
            if _strip_round_suffix(a.get("name", "")) in arm_map["balanced_only_arm_names"]
        ),
        None,
    )
    if ref_arm is None:
        ref_arm = next(
            (
                a for a in arms_data
                if _strip_round_suffix(a.get("name", "")) in arm_map["baseline_arm_names"]
            ),
            None,
        )
    vol_n = vol_arm.get("trades_count", 0) if vol_arm else 0
    ref_n = ref_arm.get("trades_count", 0) if ref_arm else 0
    if vol_arm is None or ref_arm is None or vol_n == 0 or ref_n == 0:
        volume_check = {"status": "NO_DATA", "badge": "badge-amber",
                        "text": "Volume comparison pending trade activity"}
    else:
        drop_pct = ((ref_n - vol_n) / ref_n) * 100.0 if ref_n > 0 else 0.0
        if drop_pct <= 20.0:
            volume_check = {"status": "HEALTHY", "badge": "badge-green",
                            "text": (f"Drop: {drop_pct:+.1f}% -- {vol_arm.get('name')} ({vol_n} trades) vs "
                                    f"{ref_arm.get('name')} ({ref_n} trades) — fills flowing")}
        elif drop_pct <= 40.0:
            volume_check = {"status": "MODERATE", "badge": "badge-amber",
                            "text": (f"Drop: {drop_pct:+.1f}% -- {vol_arm.get('name')} ({vol_n} trades) vs "
                                    f"{ref_arm.get('name')} ({ref_n} trades) — moderate restriction")}
        else:
            volume_check = {"status": "STARVATION", "badge": "badge-red",
                            "text": (f"Drop: {drop_pct:+.1f}% -- {vol_arm.get('name')} ({vol_n} trades) vs "
                                    f"{ref_arm.get('name')} ({ref_n} trades) — volume starvation detected")}

    return {
        "tourney_key": tourney_key,
        "total_trades": sum(a.get("trades_count", 0) for a in arms_data),
        "arm_verdicts": arm_verdicts,
        "volume_check": volume_check,
    }



def collect_all_stats(tourney_key: str = CURRENT_SETUP_KEY) -> dict[str, Any]:
    """Analyze all tournament arms for the selected tournament and compile overall metrics."""
    meta, target_minutes = resolve_tournament_meta(tourney_key)
    arms_config, record_error = resolve_tournament_arms_with_status(tourney_key)
    arms_data = [analyze_arm_database(cfg, target_minutes) for cfg in arms_config]
    
    # Sort by Total PnL descending for leaderboard
    sorted_arms = sorted(arms_data, key=lambda x: x.get("total_pnl", -999.0), reverse=True)
    
    total_trades = sum(a.get("trades_count", 0) for a in arms_data)
    total_merges = sum(a.get("merges_count", 0) for a in arms_data)
    total_fills = sum(a.get("fills_cnt", 0) for a in arms_data)
    total_quoted = sum(
        int((a.get("funnel") or {}).get("quoted", 0) or 0) for a in arms_data
    )
    total_filled_orders = sum(
        int((a.get("funnel") or {}).get("filled", 0) or 0) for a in arms_data
    )
    total_merged_legs = sum(
        int((a.get("funnel") or {}).get("merged_legs", 0) or 0) for a in arms_data
    )
    total_stop_loss_exits = sum(
        int((a.get("funnel") or {}).get("stop_loss", 0) or 0) for a in arms_data
    )

    # Aggregate strip (frontend-only, from the sums above): summed realized
    # PnL, closes-weighted win/merge %, best-minus-worst spread. NO_DATA
    # honesty when no arm DB has reported yet -- never a fabricated zero.
    ok_arms = [a for a in arms_data if a.get("status") == "ok"]
    agg_has_data = bool(ok_arms)
    total_closes = sum(int(a.get("trades_count", 0) or 0) for a in ok_arms)
    agg_realized_pnl = round(sum(float(a.get("total_pnl", 0.0) or 0.0) for a in ok_arms), 2)
    agg_win_rate = (
        round(sum(float(a.get("win_rate", 0.0) or 0.0) * int(a.get("trades_count", 0) or 0) for a in ok_arms) / total_closes, 1)
        if total_closes else 0.0
    )
    agg_merge_rate = (
        round(sum(int(a.get("merges_count", 0) or 0) for a in ok_arms) / total_closes * 100.0, 1)
        if total_closes else 0.0
    )
    _pnls = [float(a.get("total_pnl", 0.0) or 0.0) for a in ok_arms]
    agg_spread = round(max(_pnls) - min(_pnls), 2) if _pnls else 0.0
    
    # Global tournament progress
    max_elapsed = max((a.get("elapsed_min", 0.0) for a in arms_data), default=0.0)
    progress_pct = min(100.0, round((max_elapsed / target_minutes) * 100.0, 1))
    time_rem_min = max(0.0, target_minutes - max_elapsed)
    
    # Leaders
    leader_1 = None
    leader_2 = None
    # Top two by PnL.
    if sorted_arms:
        leader_1 = sorted_arms[0]
        leader_2 = sorted_arms[1] if len(sorted_arms) > 1 else sorted_arms[0]
    _tag = meta.get("issue_primary", tourney_key)
    l1_title = f"#{_tag} Top Performer"
    l2_title = f"#{_tag} Runner-Up"

    top_arm = sorted_arms[0] if sorted_arms else {}

    return {
        "tourney_key": tourney_key,
        "current_setup_key": resolve_current_setup_key(),
        "tourney_meta": meta,
        # None unless a record exists and could not be read: the page shows this
        # so a broken record never passes for "the run has not started".
        "record_error": record_error,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_trades": total_trades,
        "total_merges": total_merges,
        "total_fills": total_fills,
        "total_quoted": total_quoted,
        "total_filled_orders": total_filled_orders,
        "total_merged_legs": total_merged_legs,
        "total_stop_loss_exits": total_stop_loss_exits,
        "agg_has_data": agg_has_data,
        "agg_realized_pnl": agg_realized_pnl,
        "agg_win_rate": agg_win_rate,
        "agg_merge_rate": agg_merge_rate,
        "agg_spread": agg_spread,
        "max_elapsed_min": round(max_elapsed, 1),
        "elapsed_hours": round(max_elapsed / 60.0, 2),
        "target_hours": round(target_minutes / 60.0, 2),
        "rem_hours": round(time_rem_min / 60.0, 2),
        "progress_pct": progress_pct,
        "is_completed": max_elapsed >= target_minutes - 1.0,
        "l1_title": l1_title,
        "leader_484": leader_1["name"] if leader_1 else "N/A",
        "leader_484_label": leader_1.get("label", "N/A") if leader_1 else "N/A",
        "leader_484_port": leader_1.get("port", 0) if leader_1 else 0,
        "leader_484_pnl": leader_1.get("total_pnl", 0.0) if leader_1 else 0.0,
        "l2_title": l2_title,
        "leader_485": leader_2["name"] if leader_2 else "N/A",
        "leader_485_label": leader_2.get("label", "N/A") if leader_2 else "N/A",
        "leader_485_port": leader_2.get("port", 0) if leader_2 else 0,
        "leader_485_pnl": leader_2.get("total_pnl", 0.0) if leader_2 else 0.0,
        "top_score": top_arm.get("score", 0),
        "top_readiness": top_arm.get("readiness", "PENDING"),
        "arms": sorted_arms,
        "decision_gates": compute_decision_gates(sorted_arms, tourney_key),
    }


def generate_cockpit_html(initial_data: dict[str, Any]) -> str:
    """Generate the full responsive, interactive HTML dashboard."""
    tourney_key = str(initial_data.get("tourney_key") or CURRENT_SETUP_KEY)
    decision_gates = initial_data.get("decision_gates") or compute_decision_gates(
        initial_data.get("arms", []), tourney_key
    )
    
    # Generate Per-Arm Cards for Decision Gates Tab. Every arm is judged on
    # its own settings: no arm is the test subject, none inherits another's
    # rank, so all cards share one neutral border and carry their own verdict.
    arm_verdict_by_name = {
        v.get("name", ""): v for v in (decision_gates.get("arm_verdicts") or [])
    }
    arm_cards = []
    for a in initial_data.get("arms", []):
        verdict = arm_verdict_by_name.get(a.get("name", ""), {})
        pnl = a.get("total_pnl", 0.0)
        pnl_str = ("+$" if pnl >= 0 else "-$") + f"{abs(pnl):.2f}"
        pnl_cls = "positive" if pnl >= 0 else "negative"
        trades_cnt = a.get("trades_count", 0)
        sharpe = a.get("sharpe_ratio")
        sharpe_txt = f"{sharpe:.2f}" if isinstance(sharpe, (int, float)) else "NO_DATA"
        mdd = a.get("max_drawdown", 0.0)
        merge_pct = a.get("merge_rate", 0.0)
        stops = a.get("stop_loss_exits_count", 0)
        status = a.get("status", "pending")
        quoted = int((a.get("funnel") or {}).get("quoted", 0) or a.get("quoted_cnt", 0) or 0)
        is_quoting = quoted > 0
        if status == "ok":
            status_label = "ACTIVE"
            status_badge_cls = "badge-green"
        elif status == "no_trades" and is_quoting:
            status_label = f"QUOTING ({quoted})"
            status_badge_cls = "badge-green"
        else:
            status_label = status.upper()
            status_badge_cls = "badge-amber"
        verdict_badge = verdict.get("badge", "badge-amber")
        verdict_text = verdict.get("verdict", "PENDING REHEARSAL DATA")
        verdict_desc = verdict.get("desc", "No completed trades recorded for this arm yet.")
        arm_cards.append(f"""
        <div class="m-card" data-dg-arm-card="{html.escape(a.get('name', ''))}" style="border: 1px solid var(--border);">
          <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 8px;">
            <div>
              <div style="font-weight: 700; font-size: 14px; color: var(--text-primary);">{html.escape(a.get('label', a.get('name', '')))}</div>
              <div style="font-size: 11px; color: var(--text-muted); font-family: monospace;">{html.escape(a.get('name', ''))} | Port {a.get('port', 0)}</div>
            </div>
            <span class="badge {status_badge_cls}">{html.escape(status_label)}</span>
          </div>
          <div style="margin-bottom: 4px;"><span class="badge {verdict_badge}" style="font-size: 10px; padding: 3px 8px;">{html.escape(verdict_text)}</span></div>
          <div style="font-size: 11.5px; color: var(--text-muted); line-height: 1.4; margin-bottom: 4px;">{html.escape(verdict_desc)}</div>
          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin: 12px 0; padding: 8px 0; border-top: 1px solid var(--border); border-bottom: 1px solid var(--border); font-size: 12px;">
            <div>PnL: <strong class="tabular {pnl_cls}">{pnl_str}</strong></div>
            <div>Quoted: <strong class="tabular">{quoted:,}</strong></div>
            <div>Trades: <strong class="tabular">{trades_cnt:,}</strong></div>
            <div title="{SHARPE_FORMULA_TEXT}">Sharpe Ratio: <strong class="tabular">{sharpe_txt}</strong></div>
            <div>Max DD: <strong class="tabular">${mdd:.2f}</strong></div>
            <div>Merge Rate: <strong class="tabular">{merge_pct:.1f}%</strong></div>
          </div>
          {_sufficiency_mini_html(a.get('sufficiency_rows') or [])}
          <div style="font-size: 11.5px; color: var(--text-muted); line-height: 1.4;">
            {html.escape(a.get('desc', ''))}
          </div>
        </div>
        """)
    arm_cards_html = "\n".join(arm_cards)
    # Notes come from an editable arms file, so neither a `</script>` nor a `<b>`
    # in one may end this block early; `<` is the only character that can.
    json_embedded = json.dumps(initial_data).replace("<", "\\u003c")
    # A record that exists but cannot be read must not look like "the run has not
    # started". The container is always emitted -- hidden when there is nothing to
    # say -- so the 5s /api/stats poll can show or hide it without a page reload.
    record_error = initial_data.get("record_error")
    record_warning_style = "display: block;" if record_error else "display: none;"
    record_warning_text = ""
    if record_error:
        record_warning_text = (
            "⚠️ Tournament record failed to parse: "
            f"{html.escape(str(record_error))}. "
            "The table shows default arms, not live data."
        )

    # Pre-render Live Readiness Decision Matrix rows for static snapshot & initial paint
    matrix_rationale = {
        "queue-clear-5m": "👑 <strong>Top Efficiency & Tail Risk Champion.</strong> Stale-book filter cuts max DD by 73% ($2.60) with Sharpe 4.67 and positive tail return (MC P5 +$12.90). Core prerequisite for live execution.",
        "balanced-mid": "🌟 <strong>Top Realized PnL Champion.</strong> Shipped dynamic 0.50x (2–4¢) spread tracking generated highest net profit (+$23.39) on 161 trades. Selected as the live pricing baseline.",
        "loose-baseline": "📊 <strong>Control Reference.</strong> Solid volume and $22.84 PnL, but unconstrained queues and missing stop loss leave orphan legs open to directional drift.",
        "static-floor-2c": "⚠️ <strong>Provisional.</strong> High volume ($17.99 PnL) but flat 2¢ floor without volatility tracking suffered -$2.28 spike loss during market blowout.",
        "tight-touch": "⚠️ <strong>Qualified Maker.</strong> Tight 0.25x fills fast on narrow books, but suffers 53% quote starvation on wide spreads. Positive EV but lower reach.",
        "prudent-hybrid": "⚠️ <strong>Provisional.</strong> Strong trade count and $19.24 PnL, but lifted 6¢ clamp suffered tournament-high drawdown ($11.29) during volatility cluster.",
        "low-churn-deadband": "🚫 <strong>Disqualified.</strong> 10¢ deadband is overly sticky; holds stale quotes into adverse drifts, bleeding $10.77 drawdown and -$3.63 MC P5.",
        "hair-trigger-2c": "🚫 <strong>Disqualified.</strong> 2¢ stop triggered 80 stop exits (57.5% exit rate!), liquidating complementary legs on normal tick flutter and gutting win rate to 42.4%.",
        "wide-shield": "🚫 <strong>Disqualified.</strong> Conservative 8¢ stop lets toxic orphan legs bleed deeply (-$0.37 avg loss) with negative tail expectancy (MC P5 -$6.76).",
        "patient-stop-9c": "⛔ <strong>REJECTED (Score < 75).</strong> 9¢ stop leash allowed catastrophic single-trade bleed (-$2.10), tanking Sharpe to 0.52 and MC P5 to -$9.56.",
    }
    ranked_arms_matrix = sorted(
        initial_data.get("arms", []),
        key=lambda x: (x.get("score") or 0, x.get("total_pnl") or 0.0),
        reverse=True,
    )
    dm_rows = []
    for idx, arm in enumerate(ranked_arms_matrix):
        pnl = arm.get("total_pnl", 0.0)
        pnl_cls = "positive" if pnl >= 0 else "negative"
        pnl_prefix = "+$" if pnl >= 0 else "-$"
        mcp5 = arm.get("mc_p5", 0.0)
        mcp5_cls = "positive" if mcp5 >= 0 else "negative"
        mcp5_prefix = "+$" if mcp5 >= 0 else "-$"
        name = arm.get("name", "")
        rationale = matrix_rationale.get(name, html.escape(arm.get("desc", "")))
        sharpe_cell = arm.get("sharpe_ratio")
        sharpe_txt = f"{sharpe_cell:.2f}" if isinstance(sharpe_cell, (int, float)) else "NO_DATA"
        dm_rows.append(
            f'<tr>'
            f'<td><strong>#{idx+1}</strong> <span style="color: {arm.get("color", "#fff")}">●</span> <strong>{html.escape(arm.get("label") or name)}</strong>'
            f'<div style="font-size: 10px; color: var(--text-muted); line-height: 1.2; margin-top: 2px;">{html.escape(arm.get("desc", ""))}</div></td>'
            f'<td><span class="badge {arm.get("badge_cls", "badge-amber")}">{arm.get("readiness", "EVALUATING")}</span></td>'
            f'<td class="tabular"><strong>{arm.get("score", 0)}</strong> / 100</td>'
            f'<td class="tabular {pnl_cls}"><strong>{pnl_prefix}{abs(pnl):.2f}</strong></td>'
            f'<td class="tabular">{arm.get("mean_pnl", 0.0):.3f}</td>'
            f'<td class="tabular" title="{html.escape(SHARPE_FORMULA_TEXT)}">{sharpe_txt}</td>'
            f'<td class="tabular">{(arm.get("profit_factor") or 0.0):.2f}</td>'
            f'<td class="tabular">{(arm.get("win_rate") or 0.0):.1f}%</td>'
            f'<td class="tabular">{(arm.get("merge_rate") or 0.0):.1f}%</td>'
            f'<td class="tabular negative">${(arm.get("max_drawdown") or 0.0):.2f}</td>'
            f'<td class="tabular {mcp5_cls}">{mcp5_prefix}{abs(mcp5):.2f}</td>'
            f'<td style="max-width: 320px; font-size: 11.5px; line-height: 1.35;">{rationale}</td>'
            f'</tr>'
        )
    dm_tbody_content = "\n".join(dm_rows)
    current_setup = str(initial_data.get("current_setup_key") or resolve_current_setup_key())
    # One pill per served tournament, each naming the run its own page shows.
    nav_pills = []
    pill_keys = list(TOURNAMENT_PILL_KEYS)
    for k in TOURNAMENTS:
        if k not in pill_keys:
            pill_keys.append(k)

    for key in pill_keys:
        icon = TOURNAMENT_ICONS.get(key, "🎯")
        badge = (
            '<span class="badge badge-green" style="font-size: 9px; padding: 1px 5px;" '
            f'title="The live rehearsal this cockpit follows (tournament #{current_setup})">'
            "CURRENT SETUP</span>"
            if key == current_setup else ""
        )
        active = "active" if tourney_key == key else ""
        nav_pills.append(
            f'<a href="/{key}" class="tourney-pill {active}" data-tourney="{key}">'
            f"<span>{icon}</span>"
            f"<span>{html.escape(nav_label_for(key))}</span>{badge}</a>"
        )
    nav_pills_html = "\n  ".join(nav_pills)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Spread Hunter — Tournament Analytics & Decision Cockpit</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  :root {{
    --bg-base: #0a0d14;
    --bg-card: #121722;
    --bg-card-hover: #171e2c;
    --bg-subtle: #1c2436;
    --border: #232c3f;
    --border-bright: #364461;
    --text-primary: #f0f4fc;
    --text-muted: #8b9bb4;
    --green: #00e676;
    --green-glow: rgba(0, 230, 118, 0.18);
    --red: #ff5252;
    --red-glow: rgba(255, 82, 82, 0.18);
    --blue: #00b0ff;
    --amber: #ffd600;
    --purple: #b388ff;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: var(--bg-base);
    color: var(--text-primary);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    font-size: 13.5px;
    line-height: 1.5;
    padding: 20px;
    min-height: 100vh;
  }}
  .tabular {{ font-variant-numeric: tabular-nums; font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }}
  
  /* Tournament Navigation Switcher */
  .tourney-nav {{
    display: flex;
    align-items: center;
    gap: 8px;
    background: #0f141f;
    border: 1px solid var(--border);
    padding: 6px 12px;
    border-radius: 8px;
    margin-bottom: 16px;
  }}
  .tourney-nav-label {{
    color: var(--text-muted);
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.6px;
    margin-right: 6px;
  }}
  .tourney-pill {{
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 5px 12px;
    border-radius: 6px;
    font-size: 12px;
    font-weight: 600;
    text-decoration: none;
    color: var(--text-muted);
    border: 1px solid transparent;
    transition: all 0.2s;
  }}
  .tourney-pill:hover {{
    color: var(--text-primary);
    background: var(--bg-card);
    border-color: var(--border);
  }}
  .tourney-pill.active {{
    background: rgba(0, 230, 118, 0.12);
    color: var(--green);
    border-color: rgba(0, 230, 118, 0.4);
  }}

  /* Top Bar */
  header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 16px 24px;
    margin-bottom: 20px;
    box-shadow: 0 4px 20px rgba(0,0,0,0.3);
  }}
  .header-titles h1 {{
    font-size: 20px;
    font-weight: 700;
    letter-spacing: 0.3px;
    display: flex;
    align-items: center;
    gap: 10px;
  }}
  .pulse-dot {{
    width: 10px; height: 10px; border-radius: 50%;
    background: var(--green);
    box-shadow: 0 0 12px var(--green);
    animation: pulse 1.8s infinite;
  }}
  @keyframes pulse {{
    0% {{ transform: scale(0.9); opacity: 0.7; }}
    50% {{ transform: scale(1.3); opacity: 1; }}
    100% {{ transform: scale(0.9); opacity: 0.7; }}
  }}
  .header-titles p {{
    color: var(--text-muted);
    font-size: 12px;
    margin-top: 3px;
  }}
  .header-controls {{
    display: flex;
    align-items: center;
    gap: 14px;
  }}
  .progress-card {{
    background: var(--bg-subtle);
    padding: 8px 16px;
    border-radius: 8px;
    border: 1px solid var(--border);
    text-align: right;
  }}
  .progress-bar-bg {{
    width: 180px; height: 6px; background: #232c3f;
    border-radius: 3px; overflow: hidden; margin-top: 5px;
  }}
  .progress-bar-fill {{
    height: 100%; background: linear-gradient(90deg, #00b0ff, #00e676);
    border-radius: 3px; transition: width 0.4s ease;
  }}
  .btn {{
    background: var(--bg-subtle);
    color: var(--text-primary);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 8px 14px;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.2s;
  }}
  .btn:hover {{ background: var(--border); border-color: var(--text-muted); }}

  /* Metric Highlight Cards */
  .metrics-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
    gap: 14px;
    margin-bottom: 20px;
  }}
  .m-card {{
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px;
    transition: border-color 0.2s;
  }}
  .m-card:hover {{ border-color: var(--border-bright); }}
  .m-label {{ color: var(--text-muted); font-size: 11.5px; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600; }}
  .m-val {{ font-size: 24px; font-weight: 700; margin-top: 4px; }}
  .m-sub {{ font-size: 11.5px; color: var(--text-muted); margin-top: 4px; }}

  /* Tabs */
  .tabs {{
    display: flex;
    gap: 8px;
    border-bottom: 1px solid var(--border);
    margin-bottom: 20px;
    padding-bottom: 4px;
  }}
  .tab-btn {{
    background: transparent;
    border: none;
    color: var(--text-muted);
    font-size: 13.5px;
    font-weight: 600;
    padding: 8px 16px;
    border-radius: 6px;
    cursor: pointer;
    transition: all 0.2s;
  }}
  .tab-btn:hover {{ color: var(--text-primary); background: var(--bg-card); }}
  .tab-btn.active {{ color: var(--text-primary); background: var(--bg-subtle); border: 1px solid var(--border); }}

  /* Panels */
  .tab-pane {{ display: none; }}
  .tab-pane.active {{ display: block; }}

  /* Cards and Charts Layout */
  .charts-grid {{
    display: grid;
    grid-template-columns: 2fr 1fr;
    gap: 16px;
    margin-bottom: 20px;
  }}
  .chart-box {{
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 18px;
  }}
  .chart-header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 14px;
  }}
  .chart-title {{ font-size: 14px; font-weight: 700; }}
  .chart-legend {{ font-size: 11.5px; color: var(--text-muted); }}

  /* Table styling */
  .table-box {{
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 10px;
    overflow: hidden;
    margin-bottom: 20px;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    text-align: left;
  }}
  th {{
    background: var(--bg-subtle);
    color: var(--text-muted);
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    padding: 12px 14px;
    border-bottom: 1px solid var(--border);
  }}
  td {{
    padding: 12px 14px;
    border-bottom: 1px solid var(--border);
    font-size: 12.5px;
  }}
  tr:hover td {{ background: var(--bg-card-hover); }}
  .badge {{
    display: inline-block;
    padding: 3px 8px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
  }}
  .badge-green {{ background: var(--green-glow); color: var(--green); border: 1px solid rgba(0,230,118,0.3); }}
  .badge-amber {{ background: rgba(255,214,0,0.15); color: var(--amber); border: 1px solid rgba(255,214,0,0.3); }}
  .badge-red {{ background: var(--red-glow); color: var(--red); border: 1px solid rgba(255,82,82,0.3); }}

  .kpi-val {{
    display: inline-block;
    font-size: 12.5px;
    font-weight: 700;
    line-height: 1.25;
    letter-spacing: 0.15px;
    text-transform: none;
    white-space: nowrap;
  }}
  .kpi-val-good {{ color: var(--green); }}
  .kpi-val-warn {{ color: #ffd54f; }}
  .kpi-val-bad  {{ color: var(--red); }}

  .positive {{ color: var(--green); }}
  .negative {{ color: var(--red); }}

  /* Decision Callout */
  .decision-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
    margin-top: 16px;
  }}
  .decision-box {{
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 18px;
  }}
  .decision-box h3 {{
    font-size: 15px; margin-bottom: 10px; display: flex; align-items: center; gap: 8px;
  }}
  .decision-box ul {{
    padding-left: 20px; color: var(--text-muted); font-size: 12.5px; line-height: 1.7;
  }}
  .decision-box strong {{ color: var(--text-primary); }}
</style>
</head>
<body>

<!-- Tournament Navigation Switcher: pick which tournament's cockpit to view.
     Both #799 (the current live setup) and the #843 pair-test rehearsal are
     served; the active pill follows the page being viewed. -->
<div class="tourney-nav">
  <span class="tourney-nav-label">Tournament Evaluation:</span>
  {nav_pills_html}
</div>

<header>
  <div class="header-titles">
    <h1><span class="pulse-dot"></span> {initial_data.get('tourney_meta', {}).get('title', 'Spread Hunter — Tournament Cockpit')}</h1>
    <p>{initial_data.get('tourney_meta', {}).get('subtitle', '12-Hour Multi-Arm Live Shadow Evaluation')}</p>
    <p style="color: var(--text-muted); font-size: 12px; line-height: 1.35; margin-top: 4px; max-width: 820px;">{initial_data.get('tourney_meta', {}).get('description', '')}</p>
  </div>
  <div class="header-controls">
    <div class="progress-card">
      <div style="font-size: 11px; color: var(--text-muted); display: flex; justify-content: space-between;">
        <span>Progress: <strong id="progress-text" class="tabular">{initial_data.get('elapsed_hours', 0.0):.1f}h / {initial_data.get('target_hours', 12.0):.1f}h</strong></span>
        <span id="rem-text" class="tabular">~{initial_data.get('rem_hours', 12.0):.1f}h rem</span>
      </div>
      <div class="progress-bar-bg">
        <div id="progress-bar" class="progress-bar-fill" style="width: {initial_data.get('progress_pct', 0.0):.1f}%;"></div>
      </div>
    </div>
    <div style="display: flex; flex-direction: column; align-items: flex-end; gap: 4px;">
      <button class="btn" id="btn-refresh" onclick="fetchLiveStats()">↻ Refresh Data</button>
      <span id="refresh-status" style="font-size: 11px; color: var(--text-muted);">Loaded</span>
    </div>
  </div>
</header>

<div id="record-warning" class="badge-red" style="{record_warning_style} width: 100%; box-sizing: border-box; padding: 10px 14px; border-radius: 8px; font-size: 12.5px; line-height: 1.5; margin: 0 0 18px;">{record_warning_text}</div>

<!-- Top Headline Metric Cards -->
<div class="metrics-grid">
  <div class="m-card">
    <div class="m-label" id="m-l1-title">{initial_data.get('l1_title', 'Top Performer')}</div>
    <div class="m-val tabular positive" id="m-leader-484">{initial_data.get('leader_484_label', initial_data.get('leader_484', 'N/A'))}</div>
    <div class="m-sub" id="m-leader-484-pnl">PnL: {('+$' if initial_data.get('leader_484_pnl', 0.0) >= 0 else '-$') + f"{abs(initial_data.get('leader_484_pnl', 0.0)):.2f}"} | Port {initial_data.get('leader_484_port', 0)}</div>
  </div>
  <div class="m-card">
    <div class="m-label" id="m-l2-title">{initial_data.get('l2_title', 'Runner-Up')}</div>
    <div class="m-val tabular positive" id="m-leader-485">{initial_data.get('leader_485_label', initial_data.get('leader_485', 'N/A'))}</div>
    <div class="m-sub" id="m-leader-485-pnl">PnL: {('+$' if initial_data.get('leader_485_pnl', 0.0) >= 0 else '-$') + f"{abs(initial_data.get('leader_485_pnl', 0.0)):.2f}"} | Port {initial_data.get('leader_485_port', 0)}</div>
  </div>
  <div class="m-card">
    <div class="m-label">Total Pair Merges</div>
    <div class="m-val tabular" id="m-merges">{initial_data.get('total_merges', 0):,}</div>
    <div class="m-sub" id="m-merges-sub">Across all {len(initial_data.get('arms') or [])} tournament arms</div>
  </div>
  <div class="m-card">
    <div class="m-label">Total Quoted Orders</div>
    <div class="m-val tabular" id="m-fills">{initial_data.get('total_quoted', 0):,}</div>
    <div class="m-sub">Funnel entry: every quote placed across all arms</div>
  </div>
  <div class="m-card">
    <div class="m-label">Top Live Candidate Score</div>
    <div class="m-val tabular positive" id="m-top-score">{initial_data.get('top_score', 0)} / 100</div>
    <div class="m-sub">Readiness: {initial_data.get('top_readiness', 'EVALUATING')}</div>
  </div>
</div>

<!-- Tabs Navigation -->
<div class="tabs">
  <button class="tab-btn active" onclick="switchTab('overview', this)">🏆 Leaderboard &amp; Overview</button>
  <button class="tab-btn" onclick="switchTab('decision-gates', this)">🚦 Decision Gates &amp; Rehearsal Status</button>
  {'<button class="tab-btn" onclick="switchTab(\'hybrid-config\', this)">⚙️ HYBRID Configuration Blueprint</button>' if tourney_key not in ("905", "917", "923", "929", "938", "940") else ''}
  <button class="tab-btn" onclick="switchTab('pnl-chart', this)">📈 Cumulative PnL Curves</button>
  <button class="tab-btn" onclick="switchTab('risk-stats', this)">⚖️ Risk &amp; Volatility (EV, &sigma;, MDD)</button>
  <button class="tab-btn" onclick="switchTab('execution', this)">🎯 Merge vs Fallout Quality</button>
  <button class="tab-btn" onclick="switchTab('monte-carlo', this)">🎲 Monte Carlo Projections</button>
  <button class="tab-btn" onclick="switchTab('recommendations', this)">🚀 Live Readiness Decision Matrix</button>
</div>

<!-- TAB 1: OVERVIEW & LEADERBOARD -->
<div id="tab-overview" class="tab-pane active">
  <div class="table-box" style="margin-bottom: 16px;">
    <div style="display: flex; gap: 18px; flex-wrap: wrap; padding: 12px 16px; font-size: 13px;">
      <span>Aggregate realized PnL: <strong class="tabular" id="agg-pnl">{('+$' if initial_data.get('agg_realized_pnl', 0.0) >= 0 else '-$') + f"{abs(initial_data.get('agg_realized_pnl', 0.0)):.2f}" if initial_data.get('agg_has_data') else 'NO_DATA'}</strong></span>
      <span>Closes-weighted win: <strong class="tabular" id="agg-win">{f"{initial_data.get('agg_win_rate', 0.0):.1f}%" if initial_data.get('agg_has_data') else 'NO_DATA'}</strong></span>
      <span>Closes-weighted merge: <strong class="tabular" id="agg-merge">{f"{initial_data.get('agg_merge_rate', 0.0):.1f}%" if initial_data.get('agg_has_data') else 'NO_DATA'}</strong></span>
      <span>Best-minus-worst: <strong class="tabular" id="agg-spread">{('$' + f"{initial_data.get('agg_spread', 0.0):.2f}") if initial_data.get('agg_has_data') else 'NO_DATA'}</strong></span>
    </div>
  </div>
  <div id="arms-empty-state" style="display: none; margin-bottom: 16px; padding: 16px; border: 1px dashed #2a3550; border-radius: 10px; background: #0e1420; text-align: center; font-size: 13px; font-weight: 600; color: #8b9bb4;"></div>
  <div class="table-box">
    <table>
      <thead>
        <tr>
          <th>Rank &amp; Arm</th>
          <th title="{SHARPE_FORMULA_TEXT}">Sharpe Ratio</th>
          <th>Profit Factor</th>
          <th>VaR95</th>
          <th>MC P5</th>
          <th>Filled Orders</th>
          <th>Stop-Loss Exits</th>
          <th>Merge %</th>
          <th>Total PnL</th>
          <th>EV / Trade</th>
          <th>Std Dev (&sigma;)</th>
          <th>Max DD</th>
          <th>Worst Catalyst &amp; Hypothesized Tweak</th>
          <th>Live Score</th>
          <th>Readiness Verdict</th>
          <th>Action</th>
        </tr>
      </thead>
      <tbody id="leaderboard-tbody">
        <!-- Rendered dynamically -->
      </tbody>
    </table>
  </div>

  <div class="charts-grid">
    <div class="chart-box">
      <div class="chart-header">
        <div class="chart-title">Realized PnL vs Max Drawdown (Risk-Return Frontier)</div>
        <div class="chart-legend">Higher and further left is superior</div>
      </div>
      <div style="height: 280px;">
        <canvas id="scatterChart"></canvas>
      </div>
    </div>
    <div class="chart-box">
      <div class="chart-header">
        <div class="chart-title" id="radar-title">Multi-Factor Radar: Leader vs Control</div>
        <div class="chart-legend">Balanced profile comparison</div>
      </div>
      <div style="height: 280px;">
        <canvas id="radarChart"></canvas>
      </div>
    </div>
  </div>
</div>

<!-- TAB: DECISION GATES & REHEARSAL STATUS -->
<div id="tab-decision-gates" class="tab-pane">
  <!-- The tournament switcher lives in the top nav bar; the rehearsal pane
       follows whichever tournament the page is serving. -->

  <!-- Run-level bar: totals plus the volume check. The volume check is
       informational for the run -- it is never a verdict on any arm. -->
  <div style="padding: 16px 20px; border-radius: 10px; margin-bottom: 20px; border: 1px solid var(--border); background: var(--bg-card); display: flex; flex-direction: column; gap: 8px;">
    <div style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px;">
      <div style="font-size: 12px; color: var(--text-muted);">
        Run total: <strong class="tabular" id="dg-run-trades" style="color: var(--text-primary);">{decision_gates['total_trades']:,}</strong> closed trades — each arm below is judged on its own settings.
      </div>
      <div style="display: flex; align-items: center; gap: 8px;">
        <span style="font-size: 12px; color: var(--text-muted);">Volume check:</span>
        <span class="badge {decision_gates['volume_check']['badge']}" id="dg-volume-badge">{decision_gates['volume_check']['status']}</span>
      </div>
    </div>
    <div id="dg-volume-text" style="font-size: 12.5px; color: var(--text-muted); line-height: 1.5;">
      {decision_gates['volume_check']['text']}
    </div>
  </div>

  <!-- Sufficiency matrix: KPIs (rows) x arms (columns) at 90/95/99 -->
  <div style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px; margin-bottom: 10px;">
    <div style="font-size: 12px; font-weight: 700; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px;">Sufficiency Matrix — KPIs x Arms</div>
    <div id="dg-conf-toggle" style="display: flex; gap: 6px; align-items: center;">
      <span style="font-size: 11px; color: var(--text-muted);">Confidence:</span>
      <button type="button" class="dg-conf-btn" data-dg-conf="90" style="font-size: 11px; padding: 3px 10px; border-radius: 6px; border: 1px solid var(--border); background: transparent; color: var(--text-muted); cursor: pointer;">90%</button>
      <button type="button" class="dg-conf-btn" data-dg-conf="95" style="font-size: 11px; padding: 3px 10px; border-radius: 6px; border: 1px solid var(--green); background: var(--green-glow); color: var(--text-primary); cursor: pointer;">95%</button>
      <button type="button" class="dg-conf-btn" data-dg-conf="99" style="font-size: 11px; padding: 3px 10px; border-radius: 6px; border: 1px solid var(--border); background: transparent; color: var(--text-muted); cursor: pointer;">99%</button>
    </div>
  </div>
  <div id="dg-arm-filter" style="margin-bottom: 12px; font-size: 12px;">
    <span style="font-size: 11px; color: var(--text-muted); margin-right: 8px;">Show arms:</span>{_dg_filter_html(initial_data.get('arms', []))}
  </div>
  <div id="dg-matrix-container" class="table-box" style="margin-bottom: 24px;">{_dg_matrix_html(initial_data.get('arms', []), 95)}</div>

    <!-- Retired single-arm gate cards (Gate 2+) removed: every arm now
         carries its own gates; the matrix above and the arm cards below
         are the decision-gates surface. -->
  <!-- Per-Arm Verdict Cards: each arm judged on its own gates -->
  <div style="font-size: 12px; font-weight: 700; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 10px;">Per-Arm Verdicts ({len(initial_data.get('arms', []))} Arms — Each Judged On Its Own Gates)</div>
  <div id="dg-arm-cards-container" style="display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 14px; margin-bottom: 24px;">
    {arm_cards_html}
  </div>
</div>

<!-- TAB: HYBRID CONFIGURATION BLUEPRINT -->
<div id="tab-hybrid-config" class="tab-pane" style="{'display: none !important;' if tourney_key in ('905', '917', '923', '929', '938', '940') else ''}">
  <div style="background: linear-gradient(135deg, rgba(0, 230, 118, 0.12), rgba(0, 176, 255, 0.12)); border: 1px solid rgba(0, 230, 118, 0.4); border-radius: 12px; padding: 22px; margin-bottom: 20px;">
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; flex-wrap: wrap; gap: 10px;">
      <div>
        <span class="badge badge-green" style="font-size: 12px; padding: 4px 10px; margin-bottom: 6px;">🌟 BENCHMARK SYNTHESIS</span>
        <h2 style="font-size: 18px; font-weight: 700; color: #f0f4fc;">Production HYBRID Configuration Blueprint</h2>
        <p style="color: var(--text-muted); font-size: 12.5px; margin-top: 4px; max-width: 760px;">
          Static benchmark synthesis of the multi-arm champion. Tournament #799 ran 10 arms on ports 8801&ndash;8810 over 24 hours;
          289 trades, 210 merges and 74 stop exits are attributed to the #799 tournament review, not re-derived here.
        </p>
      </div>
      <div style="text-align: right; max-width: 340px;">
        <div style="font-size: 11px; color: var(--text-muted); text-transform: uppercase; font-weight: 600;">Status in Codebase</div>
        <div class="tabular" style="color: var(--green); font-size: 14px; font-weight: 700;">core_brain.config.TOURNAMENT_PRESETS["balanced"] + explicit overrides</div>
        <div style="font-size: 11px; color: var(--text-muted); margin-top: 4px;">No single shipped preset holds this configuration.</div>
      </div>
    </div>
  </div>

  <div class="table-box" style="margin-bottom: 24px;">
    <table>
      <thead>
        <tr>
          <th>Parameter</th>
          <th>Config Value</th>
          <th>CLI / Env Key</th>
          <th>Set By</th>
          <th>Live Function &amp; Mathematical Rationale</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td><strong>Volatility Pricing Preset</strong></td>
          <td class="tabular positive" style="font-size: 14px;"><strong>Dynamic 0.50x, 2-4¢</strong></td>
          <td><code>BOT_QUOTE_TOURNAMENT_PRESET=balanced</code></td>
          <td><span class="badge badge-green">#799 balanced-mid — the leader's only env key</span></td>
          <td><strong>Measured Recipe:</strong> The leader arm ran the shipped baseline with exactly one override, the <code>balanced</code> preset: book-spread tracking at 0.50x inside a 2¢ floor and a 4¢ ceiling. Reproducing that arm's pricing takes this one variable.</td>
        </tr>
        <tr>
          <td><strong>Spread Offset Ceiling</strong></td>
          <td class="tabular positive" style="font-size: 14px;"><strong>4.0¢</strong></td>
          <td><code>BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS</code> — preset default, no line needed</td>
          <td><span class="badge badge-green">balanced preset</span></td>
          <td><strong>Reach Anchor:</strong> The ceiling is 4¢ because that is what the preset ships; the leader sets no override here, so no <code>BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS</code> line belongs in the deployment file. An earlier version of this tab lifted it to 6¢ — a value no #799 arm ran.</td>
        </tr>
        <tr>
          <td><strong>Requote Deadband</strong></td>
          <td class="tabular positive" style="font-size: 14px;"><strong>0.060 (6.0¢)</strong></td>
          <td><code>BOT_ORDER_REQUOTE_DEAD_BAND_USD=0.06</code></td>
          <td><span class="badge badge-amber">not tested at 6¢ in #799</span></td>
          <td><strong>Queue priority defender.</strong> The deadband is the price move the bot ignores before it re-quotes: wide enough that 1¢ jitter cannot demote the order, tight enough that the quote is not resting on a stale price. 6¢ sits between <code>static-floor-2c</code> (3¢ at the time) and <code>low-churn-deadband</code> (10¢), and carries the #485 <code>deadband-wide</code> lesson. No #799 arm ran a 6¢ deadband, so this value is reasoned rather than measured.</td>
        </tr>
        <tr>
          <td><strong>Volatility Pricing Offset</strong></td>
          <td class="tabular positive" style="font-size: 14px;"><strong>Dynamic 0.50x, 2-4¢ clamp</strong></td>
          <td><code>BOT_QUOTE_TOURNAMENT_PRESET=balanced</code></td>
          <td><span class="badge badge-green">#799 balanced-mid</span></td>
          <td><strong>Volatility tracker.</strong> offset = round(0.50 × the recent range in cents), held between 2 and 4. The <code>balanced</code> preset ships exactly this band (0.50x, 2¢ floor, 4¢ ceiling), so no offset-ceiling line is needed — the leader ran with no override beyond the preset. Stale or missing range data falls back to the static base offset.</td>
        </tr>
        <tr>
          <td><strong>Single-Buy Loss Stop</strong></td>
          <td class="tabular positive" style="font-size: 14px;"><strong>$0.045 (4.5¢)</strong></td>
          <td><code>BOT_RISK_STOP_MODE=ABSOLUTE_DROP</code><br><code>BOT_RISK_STOP_ADVERSE_DROP_USD=0.045</code></td>
          <td><span class="badge badge-amber">between #799 hair-trigger-2c and patient-stop-9c</span></td>
          <td><strong>A trigger, not a loss cap.</strong> In <code>ABSOLUTE_DROP</code> mode the trigger is the entry price minus 0.045, <strong>rounded up to a whole cent</strong>; the emergency floor can set a higher trigger. A 2¢ stop fired on 44.1% of legs in the #799 review (<code>hair-trigger-2c</code>), while 9¢ and 8¢ (<code>patient-stop-9c</code>, <code>wide-shield</code>) let the tail bleed. 4.5¢ was not tested in #799.</td>
        </tr>
        <tr>
          <td><strong>Queue-Clear Gate</strong></td>
          <td class="tabular positive" style="font-size: 14px;"><strong>3.0 minutes</strong></td>
          <td><code>BOT_ORDER_GATE_QUEUE_CLEAR_ON=1</code><br><code>BOT_ORDER_QUEUE_MAX_CLEAR_MIN=3.0</code></td>
          <td><span class="badge badge-amber">tightened from #799 queue-clear-5m</span></td>
          <td><strong>Illiquid-book filter.</strong> Estimated clear time = size ahead ÷ the recent traded rate &mdash; an upper-bound estimate, not a fill deadline. A book with no recent flow makes the estimate infinite, so the entry is refused rather than left resting behind a queue that never clears. #799 tested 5 minutes, not 3.</td>
        </tr>
        <tr>
          <td><strong>Aged-Out Rescue Lead</strong></td>
          <td class="tabular positive" style="font-size: 14px;"><strong>45.0s</strong></td>
          <td><code>BOT_RISK_AGED_OUT_RESCUE_LEAD_SEC=45.0</code></td>
          <td><span class="badge badge-amber">not varied in #799</span></td>
          <td><strong>Settlement-squeeze defence.</strong> How long before resolution the bot unwinds a stranded orphan leg, so the position is liquidated rather than settled directionally. Every #799 arm inherited the shipped 900s, so 45.0s is a new operating point &mdash; which is why the failure card below carries its estimate caveat.</td>
        </tr>
        <tr>
          <td><strong>Absolute Pair Cap</strong></td>
          <td class="tabular" style="font-size: 14px; color: var(--amber);"><strong>$0.990</strong></td>
          <td><code>BOT_POS_PRICE_MAX_PAIR_COST_USD</code> (shipped default, leave unset)</td>
          <td><span class="badge badge-amber">shared safety default</span></td>
          <td><strong>Core safety law, and a gross margin only.</strong> The bot refuses a two-leg quote sum above $0.99, which leaves at least a $0.01 gross spread before fees and gas against the $1.00 the pair merges into. $0.990 is already the value the engine ships, so the block below names it as a comment and leaves the variable unset: setting it explicitly raises outside a rehearsal.</td>
        </tr>
      </tbody>
    </table>
  </div>

  <div class="decision-grid" style="margin-bottom: 24px;">
    <div class="decision-box" style="border-left: 4px solid var(--green);">
      <h3 style="color: var(--green);">📊 Leader Arm — Live Standing in #799 (Interim)</h3>
      <p style="color: var(--text-muted); font-size: 11.5px; margin: 0 0 10px 0; line-height: 1.45;">
        Recomputed from <code>balanced-mid</code>, the arm this blueprint is built from, on the same 5s poll as the rest of this page.
        Not a forecast, and not a verdict: the run is <strong id="hb-progress" class="tabular">—</strong> complete, and this run's
        <code>results.json</code> carries the note “Shadow results are modeled… The stat gate decides.” The standing row below says whether that arm is still ahead.
      </p>
      <ul style="list-style-type: none; padding-left: 0; line-height: 1.8;">
        <li>🎯 <strong>Observed Win Rate:</strong> <span id="hb-win" class="tabular positive">—</span> <span style="color: var(--text-muted);">vs league <span id="hb-aggwin" class="tabular">—</span></span></li>
        <li>🔗 <strong>Merge-to-Close:</strong> <span id="hb-merge" class="tabular positive">—</span> <span style="color: var(--text-muted);">vs league <span id="hb-aggmerge" class="tabular">—</span></span></li>
        <li>⚡ <strong>Sharpe Ratio:</strong> <span id="hb-sharpe" class="tabular positive">—</span> <span style="color: var(--text-muted);">league best <span id="hb-bestsharpe" class="tabular">—</span> (<span id="hb-bestsharpearm">—</span>)</span></li>
        <li>💰 <strong>Realized EV / Trade:</strong> <span id="hb-ev" class="tabular positive">—</span> per closed position</li>
        <li>⚖️ <strong>Profit Factor:</strong> <span id="hb-pf" class="tabular positive">—</span></li>
        <li>🎲 <strong>Monte Carlo P5 (100-Trade Draw):</strong> <span id="hb-mcp5" class="tabular positive">—</span></li>
        <li>📉 <strong>Max Drawdown:</strong> <span id="hb-mdd" class="tabular negative">—</span></li>
        <li>🏁 <strong>Standing Among the Arms:</strong> <span id="hb-rank" class="tabular">—</span>, at <span id="hb-pnl" class="tabular positive">—</span> realized — leader <span id="hb-leader">—</span> (<span id="hb-leaderpnl" class="tabular positive">—</span>)</li>
      </ul>
    </div>

    <div class="decision-box" style="border-left: 4px solid var(--red);">
      <h3 style="color: var(--red);">⚠️ Stress Scenarios &amp; Failure Mode Defenses</h3>
      <ul style="list-style-type: none; padding-left: 0; line-height: 1.8;">
        <li>💥 <strong>1. Flash Directional Breakout:</strong> Leg 1 fills immediately prior to a sharp >15¢ event before Leg 2 posts.
          <div style="font-size: 11px; color: var(--text-muted); margin-left: 14px;"><strong>Defense:</strong> the 4.5¢ absolute-drop stop cuts the orphan at -$0.045 instead of letting it run. Observed on this arm: <span id="hb-stops2" class="tabular">—</span> stop-loss exits.</div>
        </li>
        <li>🧊 <strong>2. Liquidity Vacuum / Stalled Book:</strong> Taker volume halts right after Leg 1 fill, leaving second leg stranded.
          <div style="font-size: 11px; color: var(--text-muted); margin-left: 14px;">            <strong>Defense:</strong> the 5m queue-clear filter avoids stagnant books; the 45s rescue unwinds the leg into settlement.</div>
        </li>
        <li>🏓 <strong>3. High-Frequency Ping-Pong Jitter:</strong> Micro-tick flutter triggers repeated cancellations.
          <div style="font-size: 11px; color: var(--text-muted); margin-left: 14px;"><strong>Defense:</strong> 6.0¢ deadband suppresses cancel/re-quote order churn, maintaining book priority.</div>
        </li>
      </ul>
    </div>
  </div>

  <div class="decision-grid" style="margin-bottom: 24px;">
    <div class="decision-box">
      <h3 style="color: var(--blue);">💻 Ready-to-Run PowerShell &amp; CLI Commands</h3>
      <p style="color: var(--text-muted); font-size: 12px; margin-bottom: 10px;">A rehearsal reads these from the shell environment, not from <code>.env</code>. Set all seven, then run one command:</p>
      <pre style="background: #090c12; border: 1px solid var(--border); border-radius: 8px; padding: 12px; font-family: monospace; font-size: 12px; color: #a5d6a7; overflow-x: auto;">
# --- Champion environment (Windows PowerShell 7) ---
$env:BOT_QUOTE_TOURNAMENT_PRESET = "balanced"
$env:BOT_ORDER_REQUOTE_DEAD_BAND_USD = "0.06"
$env:BOT_RISK_STOP_MODE = "ABSOLUTE_DROP"
$env:BOT_RISK_STOP_ADVERSE_DROP_USD = "0.045"
$env:BOT_ORDER_GATE_QUEUE_CLEAR_ON = "1"
$env:BOT_ORDER_QUEUE_MAX_CLEAR_MIN = "3.0"
$env:BOT_RISK_AGED_OUT_RESCUE_LEAD_SEC = "45.0"

# The pair cap stays at its shipped $0.990 default - clear any inherited value:
Remove-Item Env:BOT_POS_PRICE_MAX_PAIR_COST_USD -ErrorAction SilentlyContinue

# 1. Interactive menu - the balanced preset, 60-minute rehearsal (needs PowerShell 7):
.\\scripts\\spread-hunter-menu.ps1 shadow-run -Preset balanced -Minutes 60

# 2. Or run the same rehearsal directly:
python -m core_brain.shadow_run --minutes 60 --interval 1 --db data/champion_balanced_shadow_01.db --run-id shadow-champion-balanced-01 --starting-bankroll-usd 100

# 3. Read the result: a direct --db rehearsal writes a standalone store that
#    the cockpit cannot resolve (it serves data/tournaments run records), so
#    open the store in the execution dashboard:
python -m dashboard.server --db data/champion_balanced_shadow_01.db
      </pre>
    </div>

    <div class="decision-box">
      <h3 style="color: var(--green);">🛡️ Live Production Environment Variables (.env)</h3>
      <p style="color: var(--text-muted); font-size: 12px; margin-bottom: 10px;">The same seven values for the live trader, which does read <code>.env</code>:</p>
      <pre style="background: #090c12; border: 1px solid var(--border); border-radius: 8px; padding: 12px; font-family: monospace; font-size: 12px; color: #81d4fa; overflow-x: auto;">
# --- Spread Hunter HYBRID Champion Config ---
BOT_QUOTE_TOURNAMENT_PRESET=balanced
BOT_ORDER_REQUOTE_DEAD_BAND_USD=0.06
BOT_RISK_STOP_MODE=ABSOLUTE_DROP
BOT_RISK_STOP_ADVERSE_DROP_USD=0.045
BOT_ORDER_GATE_QUEUE_CLEAR_ON=1
BOT_ORDER_QUEUE_MAX_CLEAR_MIN=3.0
BOT_RISK_AGED_OUT_RESCUE_LEAD_SEC=45.0
# Pair cap: $0.990 shipped default. Leave the pair-cap variable unset.
      </pre>
    </div>
  </div>

  <div class="decision-grid" style="margin-bottom: 24px;">
    <div class="decision-box">
      <h3 style="color: var(--blue);">📈 Performance Forecast</h3>
      <p style="color: var(--text-muted); font-size: 12px; margin-bottom: 10px;">
        Projected targets for this combination. Tournament #799 never ran it, so these are projections, not measured results.
      </p>
      <ul>
        <li><strong>Projected Win Rate: <span class="tabular positive">80% - 84%</span></strong><br>Share of closes with positive realized P&amp;L.</li>
        <li><strong>Merge-to-Close Ratio: <span class="tabular positive">82% - 86%</span></strong><br>Pairs merged &divide; every close.</li>
        <li><strong>Expected Sharpe Ratio: <span class="tabular positive">to be measured</span></strong><br>Annualised time-series Sharpe on portfolio equity (5-minute buckets, 365-day year). Old proxy gates (&ge; 2.70) are under review for the new metric.</li>
        <li><strong>Expected EV per Trade: <span class="tabular positive">+$0.38 - +$0.46</span></strong><br>Mean realized P&amp;L of one close.</li>
        <li><strong>Target Profit Factor: <span class="tabular positive">&gt; 8.0</span></strong><br>Gross wins &divide; gross losses.</li>
        <li><strong>Projected 100-Trade Monte Carlo 5th Percentile: <span class="tabular positive">&gt; +$30.00</span></strong><br>5th percentile of 500 resamples of 100 trades from the realized distribution.</li>
      </ul>
    </div>

    <div class="decision-box">
      <h3 style="color: var(--red);">🛡️ Failure Modes &amp; Stress Scenarios</h3>
      <p style="color: var(--text-muted); font-size: 12px; margin-bottom: 10px;">
        The three ways this configuration loses money, and what already stands in the way.
      </p>
      <ul>
        <li><strong>Flash Directional Breakout</strong> &mdash; leg 1 fills just before a sharp move of more than 15¢, and leg 2 never fills.<br><em>Mitigation:</em> the 4.5¢ stop triggers early, targeting about -$0.045 to -$0.05 per share. A gap or a thin book can make the real loss larger than the trigger.</li>
        <li><strong>Liquidity Vacuum / Book Desertion</strong> &mdash; taker activity freezes after the leg 1 fill.<br><em>Mitigation:</em> the 3-minute queue-clear filter avoids the slowest books, and the 45s aged-out rescue unwinds the position before the settlement freeze. Queue-clear is an estimate and promises no fill.</li>
        <li><strong>High-Frequency Ping-Pong Jitter</strong> &mdash; fast bid-ask swings inside the spread.<br><em>Mitigation:</em> the 6.0¢ deadband suppresses cancel and re-quote churn, so the order keeps its queue priority.</li>
      </ul>
    </div>
  </div>
</div>

<!-- TAB 2: PNL CURVES -->
<div id="tab-pnl-chart" class="tab-pane">
  <div class="chart-box" style="margin-bottom: 20px;">
    <div class="chart-header">
      <div class="chart-title">Cumulative PnL Trajectory Across {initial_data.get('target_hours', 12.0):g} Hours (Minutes 0 to {initial_data.get('target_hours', 12.0) * 60:g})</div>
      <div class="chart-legend">Click legend items to toggle arms</div>
    </div>
    <div style="height: 480px;">
      <canvas id="pnlLineChart"></canvas>
    </div>
  </div>
</div>

<!-- TAB 3: RISK & VOLATILITY -->
<div id="tab-risk-stats" class="tab-pane">
  <div class="charts-grid">
    <div class="chart-box">
      <div class="chart-header">
        <div class="chart-title">Expected Return (EV) vs Volatility (&sigma;) Per Trade</div>
        <div class="chart-legend">Positive expectancy with narrow dispersion wins</div>
      </div>
      <div style="height: 320px;">
        <canvas id="evBarChart"></canvas>
      </div>
    </div>
    <div class="chart-box">
      <div class="chart-header">
        <div class="chart-title">Profit Factor Comparison</div>
        <div class="chart-legend">Gross Gains / Gross Losses (Target &gt; 1.50)</div>
      </div>
      <div style="height: 320px;">
        <canvas id="pfBarChart"></canvas>
      </div>
    </div>
  </div>
</div>

<!-- TAB 4: EXECUTION & MERGES -->
<div id="tab-execution" class="tab-pane">
  <div class="chart-box" style="margin-bottom: 20px;">
    <div class="chart-header">
      <div class="chart-title">Execution Composition: Successful Merges vs Single-Leg Exits</div>
      <div class="chart-legend">Single-leg exits incur haircuts; higher merge proportion protects edge</div>
    </div>
    <div style="height: 360px;">
      <canvas id="mergeStackedBarChart"></canvas>
    </div>
  </div>
</div>

<!-- TAB 5: MONTE CARLO -->
<div id="tab-monte-carlo" class="tab-pane">
  <div class="chart-box" style="margin-bottom: 20px;">
    <div class="chart-header">
      <div class="chart-title">100-Trade Monte Carlo Projections: 5th Percentile, Median, and 95th Percentile</div>
      <div class="chart-legend">Empirical resampling (500 simulations) of realized trade distribution</div>
    </div>
    <div style="height: 360px;">
      <canvas id="mcBarChart"></canvas>
    </div>
  </div>
</div>

<!-- TAB 6: RECOMMENDATIONS -->
<div id="tab-recommendations" class="tab-pane">
  <div style="background: linear-gradient(135deg, rgba(0, 230, 118, 0.12), rgba(0, 176, 255, 0.12)); border: 1px solid rgba(0, 230, 118, 0.4); border-radius: 12px; padding: 22px; margin-bottom: 20px;">
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; flex-wrap: wrap; gap: 10px;">
      <div>
        <span class="badge badge-green" style="font-size: 12px; padding: 4px 10px; margin-bottom: 6px;">🚀 #799 PRODUCTION READINESS VERDICT</span>
        <h2 style="font-size: 18px; font-weight: 700; color: #f0f4fc;">Live Readiness Decision Matrix — #799 Tournament Evaluation</h2>
        <p style="color: var(--text-muted); font-size: 12.5px; margin-top: 4px;">
          Multi-factor readiness audit across 10 tournament arms. Production gate requires: <strong>Score &ge; 75</strong>, <strong>Realized PnL &gt; $5.00</strong>, and <strong>Monte Carlo Prob(PnL &gt; 0) &ge; 70%</strong>.
        </p>
      </div>
      <div style="text-align: right;">
        <div style="font-size: 11px; color: var(--text-muted); text-transform: uppercase; font-weight: 600;">Scoring Model Framework</div>
        <div class="tabular" style="color: var(--green); font-size: 14px; font-weight: 700;">PnL (30) &bull; Merge% (25) &bull; Low DD (20) &bull; PF (15) &bull; Sample (10)</div>
        <div style="font-size: 11px; color: var(--text-muted); margin-top: 4px;">100-point composite score penalizing drawdown, fee churn, and tail bleed.</div>
      </div>
    </div>
  </div>

  <div class="table-box" style="margin-bottom: 24px;">
    <div style="padding: 12px 16px; background: var(--bg-subtle); border-bottom: 1px solid var(--border); font-size: 13px; font-weight: 600; color: #f0f4fc;">
      📊 Cross-Arm Live Readiness Decision Matrix
    </div>
    <table>
      <thead>
        <tr>
          <th>Rank &amp; Strategy Arm</th>
          <th>Readiness Verdict</th>
          <th>Live Score</th>
          <th>Total PnL</th>
          <th>EV / Trade</th>
          <th title="{SHARPE_FORMULA_TEXT}">Sharpe Ratio</th>
          <th>Profit Factor</th>
          <th>Win Rate</th>
          <th>Merge Rate</th>
          <th>Max DD</th>
          <th>MC P5</th>
          <th>Observed Failure Mode &amp; Deployment Decision</th>
        </tr>
      </thead>
      <tbody id="decision-matrix-tbody">
{dm_tbody_content}
      </tbody>
    </table>
  </div>

  <div class="decision-grid" style="margin-bottom: 24px;">
    <div class="decision-box" style="border-left: 4px solid var(--green);">
      <h3 style="color: var(--green);">🌟 Recommended Production Configuration (#799 Champion Synthesis)</h3>
      <p style="color: var(--text-muted); font-size: 12px; margin-bottom: 12px; line-height: 1.45;">
        Synthesizes the measured winners of the #799 tournament run into the target live deployment stack:
      </p>
      <ul>
        <li><strong>Volatility Pricing Base: <code>balanced-mid</code> (Preset: <code>balanced</code>)</strong> — Book-spread tracking at dynamic 0.50x (2–4¢ floor/ceiling) captured the highest realized PnL (+<strong>$23.39</strong> across 161 trades) without quote starvation.</li>
        <li><strong>Queue-Clear Gate: <code>queue-clear-5m</code> (Mandatory Guard)</strong> — Slashes max drawdown by <strong>73%</strong> (to $2.60) while delivering tournament-high Sharpe (<strong>4.67</strong>), Profit Factor (<strong>3.23</strong>), and positive tail expectancy (MC P5 +$12.90).</li>
        <li><strong>Disciplined Deadband: <code>BOT_ORDER_REQUOTE_DEAD_BAND_USD=0.06</code> (6.0¢)</strong> — Prevents fee churn and queue rank loss without the toxic stickiness of 10¢.</li>
        <li><strong>Orphan Loss Stop: <code>BOT_RISK_STOP_MODE=ABSOLUTE_DROP</code> &bull; <code>0.045</code> (4.5¢)</strong> — Protects single legs without the 57% stop churn of 2¢ hair-triggers or the -$2.10 deep tail bleed of 9¢ leashes.</li>
        <li><strong>Aged-Out Rescue Lead: <code>BOT_RISK_AGED_OUT_RESCUE_LEAD_SEC=45.0</code></strong> — Orderly emergency single-leg unwind into resolution.</li>
      </ul>
    </div>

    <div class="decision-box" style="border-left: 4px solid var(--red);">
      <h3 style="color: var(--red);">⚠️ Disqualified Configurations &amp; Measured Failure Modes (#799 Evidence)</h3>
      <p style="color: var(--text-muted); font-size: 12px; margin-bottom: 12px; line-height: 1.45;">
        Configurations empirically rejected based on measured tournament breakdown:
      </p>
      <ul>
        <li><strong>Avoid: <code>patient-stop-9c</code> (FAILED THRESHOLD &mdash; Score 68 / 100)</strong> — 9.0¢ leash allowed catastrophic single-trade bleed (-$2.10 worst loss) and negative tail expectancy (MC P5 -$9.56, lowest Sharpe 0.52).</li>
        <li><strong>Avoid: <code>hair-trigger-2c</code> (Score 77)</strong> — Premature stop triggers dumped 80 trades (57.5% exit rate) on 1-tick normal noise, wrecking win rate to 42.4%.</li>
        <li><strong>Avoid: <code>low-churn-deadband</code> (Score 80)</strong> — 10.0¢ deadband proved overly sticky; held stale orders into adverse market drifts, suffering $10.77 drawdown.</li>
        <li><strong>Avoid: <code>static-floor-2c</code> (Score 95, but tail risk)</strong> — Static 2¢ floor without volatility widening suffered -$2.28 single loss spike during market blowout.</li>
        <li><strong>Avoid: <code>wide-shield</code> (Score 75)</strong> — 8.0¢ stop let toxic orphan legs bleed deeply (-$0.37 avg loss per stop) with negative MC P5 (-$6.76).</li>
      </ul>
    </div>
  </div>

  <div class="decision-grid" style="margin-bottom: 24px;">
    <div class="decision-box" style="border-left: 4px solid var(--blue);">
      <h3 style="color: var(--blue);">🛡️ Operational Deployment Policy &amp; Circuit Breakers</h3>
      <p style="color: var(--text-muted); font-size: 12px; margin-bottom: 10px;">Enforced risk controls and automated kill switches for live execution:</p>
      <ul>
        <li><strong>Portfolio Daily Drawdown Limit: $15.00</strong> — Immediate trading halt and cancel-all if 24h rolling realized drawdown touches $15.00.</li>
        <li><strong>Dynamic Exposure Caps (<code>core_brain/config.py</code>)</strong>:
          Max single order exposure: <strong>25%</strong> of bankroll ($25 at $100 baseline);
          Max naked single-leg exposure: <strong>6%</strong> ($6.00);
          Total bankroll ceiling: <strong>90%</strong> ($90.00).
        </li>
        <li><strong>Immutable Pair Cap: <code>BOT_POS_PRICE_MAX_PAIR_COST_USD=0.99</code></strong> — Shipped invariant guaranteeing minimum $0.01 margin before $1.00 settlement payout.</li>
      </ul>
    </div>

    <div class="decision-box" style="border-left: 4px solid var(--amber);">
      <h3 style="color: var(--amber);">📋 Live Production Go / No-Go Checklist</h3>
      <p style="color: var(--text-muted); font-size: 12px; margin-bottom: 10px;">Stat gate validation requirements before switching from shadow to live trading:</p>
      <ul>
        <li>&#9989; <strong>Composite Score &ge; 75</strong> — Multi-factor readiness rating passing EV, merge rate, drawdown, and PF thresholds.</li>
        <li>&#9989; <strong>Statistical Sample &ge; 50 Closed Trades</strong> — Adequate empirical sample depth before flipping live venue execution.</li>
        <li>&#9989; <strong>Monte Carlo Positive Outcome &ge; 70%</strong> — 500-iteration resampled trade sequences confirming positive expectancy.</li>
        <li>&#9989; <strong>Merge Efficiency &ge; 55%</strong> — Verification that two-sided fills dominate single-leg rescue exits.</li>
      </ul>
    </div>
  </div>
</div>

<script>
let state = {json_embedded};
let charts = {{}};

function switchTab(name, btn) {{
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
  const activeBtn = btn || (typeof event !== 'undefined' ? event.target : null);
  if (activeBtn) activeBtn.classList.add('active');
  const target = document.getElementById('tab-' + name);
  if (target) target.classList.add('active');
}}

// Analyst annotations arrive from a tournament's arms file, so every value here
// is text on the page -- never markup.
function escHtml(value) {{
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}}

function annotationText(value) {{
  return value == null ? '' : String(value).trim();
}}

// `N/A` means one thing here: no data. A blank description or rationale drops
// its line instead of leaving an empty one, so "empty" reads the same everywhere.
function annotationCellHtml(arm) {{
  const source = arm || {{}};
  const catalyst = annotationText(source.worst_catalyst);
  const catalystDesc = annotationText(source.worst_catalyst_desc);
  const tweak = annotationText(source.hypothesized_tweak);
  const rationale = annotationText(source.tweak_rationale);
  return `
        <div style="font-weight: 600; color: #ffab91; font-size: 11px; font-family: monospace;">🚨 ${{escHtml(catalyst || 'N/A')}}</div>
        ${{catalystDesc ? `<div style="font-size: 10px; color: var(--text-muted); line-height: 1.2; margin-top: 2px;">${{escHtml(catalystDesc)}}</div>` : ''}}
        <div style="margin-top: 4px; padding-top: 3px; border-top: 1px dashed rgba(255,255,255,0.1);">
          <span style="font-weight: 600; color: #69f0ae; font-size: 11px; font-family: monospace;">💡 Tweak: ${{escHtml(tweak || 'N/A')}}</span>
          ${{rationale ? `<div style="font-size: 10px; color: #b0bec5; line-height: 1.2; margin-top: 1px;">${{escHtml(rationale)}}</div>` : ''}}
        </div>
      `;
}}

function updateCurrentSetupBadge(currentKey) {{
  document.querySelectorAll('.tourney-pill').forEach(pill => {{
    const isCurrent = pill.getAttribute('data-tourney') === String(currentKey);
    const existingBadge = pill.querySelector('.badge-green');
    if (isCurrent && !existingBadge) {{
      const span = document.createElement('span');
      span.className = 'badge badge-green';
      span.style.fontSize = '9px';
      span.style.padding = '1px 5px';
      span.title = 'The live rehearsal this cockpit follows (tournament #' + currentKey + ')';
      span.textContent = 'CURRENT SETUP';
      pill.appendChild(span);
    }} else if (!isCurrent && existingBadge && existingBadge.textContent.includes('CURRENT SETUP')) {{
      existingBadge.remove();
    }}
  }});
}}

function renderAll() {{
  updateDecisionGatesUI();
  // The warning must survive a refresh, and appear if a record stops parsing
  // mid-run: /api/stats carries `record_error` on every poll.
  const recWarn = document.getElementById('record-warning');
  if (recWarn) {{
    if (state.record_error) {{
      recWarn.innerText = '⚠️ Tournament record failed to parse: ' + state.record_error + '. The table shows default arms, not live data.';
      recWarn.style.display = 'block';
    }} else {{
      recWarn.style.display = 'none';
    }}
  }}
  // Header metrics
  const elapsedH = (state.elapsed_hours != null ? Number(state.elapsed_hours) : 0).toFixed(1);
  const remH = (state.rem_hours != null ? Number(state.rem_hours) : 12.0).toFixed(1);
  const targetH = (state.target_hours != null ? Number(state.target_hours) : 12.0).toFixed(1);
  const pct = Math.min(100, Math.max(0, state.progress_pct || 0)).toFixed(1);
  document.getElementById('progress-text').innerText = elapsedH + 'h / ' + targetH + 'h';
  document.getElementById('rem-text').innerText = '~' + remH + 'h rem';
  document.getElementById('progress-bar').style.width = pct + '%';
  document.getElementById('m-leader-484').innerText = state.leader_484_label || state.leader_484;
  document.getElementById('m-leader-484-pnl').innerText = 'PnL: ' + (state.leader_484_pnl >= 0 ? '+$' : '-$') + Math.abs(state.leader_484_pnl).toFixed(2);
  document.getElementById('m-leader-485').innerText = state.leader_485_label || state.leader_485;
  document.getElementById('m-leader-485-pnl').innerText = 'PnL: ' + (state.leader_485_pnl >= 0 ? '+$' : '-$') + Math.abs(state.leader_485_pnl).toFixed(2);
  document.getElementById('m-merges').innerText = state.total_merges.toLocaleString();
  document.getElementById('m-fills').innerText = (state.total_quoted || 0).toLocaleString();
  const aggPnl = document.getElementById('agg-pnl');
  if (aggPnl) aggPnl.innerText = state.agg_has_data
    ? ((state.agg_realized_pnl >= 0 ? '+$' : '-$') + Math.abs(state.agg_realized_pnl).toFixed(2)) : 'NO_DATA';
  const aggWin = document.getElementById('agg-win');
  if (aggWin) aggWin.innerText = state.agg_has_data ? Number(state.agg_win_rate).toFixed(1) + '%' : 'NO_DATA';
  const aggMerge = document.getElementById('agg-merge');
  if (aggMerge) aggMerge.innerText = state.agg_has_data ? Number(state.agg_merge_rate).toFixed(1) + '%' : 'NO_DATA';
  const aggSpread = document.getElementById('agg-spread');
  if (aggSpread) aggSpread.innerText = state.agg_has_data ? '$' + Number(state.agg_spread).toFixed(2) : 'NO_DATA';

  // HYBRID blueprint tab: the leader arm's standing, read from the same poll as
  // the rest of the page. The blueprint is a specification and may be static, but
  // its *numbers* must come from the run they cite -- that is what made the
  // previous hardcoded forecast wrong the moment the run moved past it.
  const hbSet = (id, text) => {{
    const el = document.getElementById(id);
    if (el) el.innerText = text;
  }};
  const hbArms = state.arms || [];
  const hbArm = hbArms.find(a => a.name === 'balanced-mid');
  if (hbArm) {{
    const hbPct = (v) => (v == null ? '\u2014' : Number(v).toFixed(1) + '%');
    const hbUsd = (v) => ((v >= 0 ? '+$' : '-$') + Math.abs(Number(v) || 0).toFixed(2));
    hbSet('hb-quotes', (state.total_quoted || 0).toLocaleString());
    hbSet('hb-trades', (state.total_trades || 0).toLocaleString());
    hbSet('hb-arms', String(hbArms.length));
    hbSet('hb-progress', Number(state.progress_pct || 0).toFixed(1) + '%');
    hbSet('hb-win', hbPct(hbArm.win_rate));
    hbSet('hb-aggwin', hbPct(state.agg_win_rate));
    hbSet('hb-merge', hbPct(hbArm.merge_rate));
    hbSet('hb-aggmerge', hbPct(state.agg_merge_rate));
    hbSet('hb-sharpe', hbArm.sharpe_ratio != null ? Number(hbArm.sharpe_ratio).toFixed(2) : 'NO_DATA');
    const hbBest = [...hbArms].sort((a, b) => (b.sharpe_ratio ?? -Infinity) - (a.sharpe_ratio ?? -Infinity))[0];
    if (hbBest) {{
      hbSet('hb-bestsharpe', hbBest.sharpe_ratio != null ? Number(hbBest.sharpe_ratio).toFixed(2) : 'NO_DATA');
      hbSet('hb-bestsharpearm', hbBest.label || hbBest.name);
    }}
    // The queue-gate row cites the arm that ran it. Binding its Sharpe keeps that
    // row honest if the arm stops leading the field.
    const hbQc = hbArms.find(a => a.name === 'queue-clear-5m');
    if (hbQc) hbSet('hb-qcsharpe', hbQc.sharpe_ratio != null ? Number(hbQc.sharpe_ratio).toFixed(2) : 'NO_DATA');
    // 3 decimals: the leaderboard's EV / TRADE column is the value to match.
    hbSet('hb-ev', (hbArm.mean_pnl >= 0 ? '+$' : '-$') + Math.abs(Number(hbArm.mean_pnl) || 0).toFixed(3));
    hbSet('hb-pf', Number(hbArm.profit_factor || 0).toFixed(2));
    hbSet('hb-mcp5', hbUsd(hbArm.mc_p5));
    hbSet('hb-mdd', '$' + Number(hbArm.max_drawdown || 0).toFixed(2));
    const hbRanked = [...hbArms].sort((a, b) => (b.total_pnl || 0) - (a.total_pnl || 0));
    hbSet('hb-rank', '#' + (hbRanked.findIndex(a => a.name === hbArm.name) + 1) + ' of ' + hbRanked.length);
    hbSet('hb-pnl', hbUsd(hbArm.total_pnl));
    if (hbRanked[0]) {{
      hbSet('hb-leader', hbRanked[0].label || hbRanked[0].name);
      hbSet('hb-leaderpnl', hbUsd(hbRanked[0].total_pnl));
    }}
    const hbStops = Number(hbArm.stop_loss_exits_count || 0);
    const hbTrades = Number(hbArm.trades_count || 0);
    hbSet('hb-stops', String(hbStops));
    hbSet('hb-stops2', String(hbStops));
    hbSet('hb-stoprate', hbTrades > 0 ? (100.0 * hbStops / hbTrades).toFixed(1) : '0.0');
  }}

  // Live Readiness Decision Matrix Table
  const dmTbody = document.getElementById('decision-matrix-tbody');
  if (dmTbody && state.arms && state.arms.length) {{
    dmTbody.innerHTML = '';
    const matrixRationale = {{
      'queue-clear-5m': '👑 <strong>Top Efficiency & Tail Risk Champion.</strong> Stale-book filter cuts max DD by 73% ($2.60) with Sharpe 4.67 and positive tail return (MC P5 +$12.90). Core prerequisite for live execution.',
      'balanced-mid': '🌟 <strong>Top Realized PnL Champion.</strong> Shipped dynamic 0.50x (2–4¢) spread tracking generated highest net profit (+$23.39) on 161 trades. Selected as the live pricing baseline.',
      'loose-baseline': '📊 <strong>Control Reference.</strong> Solid volume and $22.84 PnL, but unconstrained queues and missing stop loss leave orphan legs open to directional drift.',
      'static-floor-2c': '⚠️ <strong>Provisional.</strong> High volume ($17.99 PnL) but flat 2¢ floor without volatility tracking suffered -$2.28 spike loss during market blowout.',
      'tight-touch': '⚠️ <strong>Qualified Maker.</strong> Tight 0.25x fills fast on narrow books, but suffers 53% quote starvation on wide spreads. Positive EV but lower reach.',
      "prudent-hybrid": '⚠️ <strong>Provisional.</strong> Strong trade count and $19.24 PnL, but lifted 6¢ clamp suffered tournament-high drawdown ($11.29) during volatility cluster.',
      'low-churn-deadband': '🚫 <strong>Disqualified.</strong> 10¢ deadband is overly sticky; holds stale quotes into adverse drifts, bleeding $10.77 drawdown and -$3.63 MC P5.',
      'hair-trigger-2c': '🚫 <strong>Disqualified.</strong> 2¢ stop triggered 80 stop exits (57.5% exit rate!), liquidating complementary legs on normal tick flutter and gutting win rate to 42.4%.',
      'wide-shield': '🚫 <strong>Disqualified.</strong> Conservative 8¢ stop lets toxic orphan legs bleed deeply (-$0.37 avg loss) with negative tail expectancy (MC P5 -$6.76).',
      'patient-stop-9c': '⛔ <strong>REJECTED (Score < 75).</strong> 9¢ stop leash allowed catastrophic single-trade bleed (-$2.10), tanking Sharpe to 0.52 and MC P5 to -$9.56.'
    }};
    const rankedForMatrix = [...state.arms].sort((a, b) => (b.score || 0) - (a.score || 0) || (b.total_pnl || 0) - (a.total_pnl || 0));
    rankedForMatrix.forEach((arm, idx) => {{
      const pnl = arm.total_pnl || 0;
      const pnlClass = pnl >= 0 ? 'positive' : 'negative';
      const pnlPrefix = pnl >= 0 ? '+$' : '-$';
      const mcp5 = arm.mc_p5 || 0;
      const mcp5Class = mcp5 >= 0 ? 'positive' : 'negative';
      const mcp5Prefix = mcp5 >= 0 ? '+$' : '-$';
      const rationale = matrixRationale[arm.name] || (arm.desc || '');
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><strong>#${{idx+1}}</strong> <span style="color: ${{arm.color}}">●</span> <strong>${{arm.label || arm.name}}</strong>
          <div style="font-size: 10px; color: var(--text-muted); line-height: 1.2; margin-top: 2px;">${{arm.desc || ''}}</div>
        </td>
        <td><span class="badge ${{arm.badge_cls || 'badge-amber'}}">${{arm.readiness || 'EVALUATING'}}</span></td>
        <td class="tabular"><strong>${{arm.score || 0}}</strong> / 100</td>
        <td class="tabular ${{pnlClass}}"><strong>${{pnlPrefix}}${{Math.abs(pnl).toFixed(2)}}</strong></td>
        <td class="tabular">${{(arm.mean_pnl || 0).toFixed(3)}}</td>
        <td class="tabular">${{(arm.sharpe_ratio != null ? arm.sharpe_ratio.toFixed(2) : 'NO_DATA')}}</td>
        <td class="tabular">${{(arm.profit_factor ?? 0).toFixed(2)}}</td>
        <td class="tabular">${{(arm.win_rate ?? 0).toFixed(1)}}%</td>
        <td class="tabular">${{(arm.merge_rate ?? 0).toFixed(1)}}%</td>
        <td class="tabular negative">$${{(arm.max_drawdown || 0).toFixed(2)}}</td>
        <td class="tabular ${{mcp5Class}}">${{mcp5Prefix}}${{Math.abs(mcp5).toFixed(2)}}</td>
        <td style="max-width: 320px; font-size: 11.5px; line-height: 1.35;">${{rationale}}</td>
      `;
      dmTbody.appendChild(tr);
    }});
  }}

  // Table
  const tbody = document.getElementById('leaderboard-tbody');
  tbody.innerHTML = '';
  state.arms.forEach((arm, idx) => {{
    const tr = document.createElement('tr');
    const pnlClass = arm.total_pnl >= 0 ? 'positive' : 'negative';
    const pnlPrefix = arm.total_pnl >= 0 ? '+$' : '-$';
    const funnel = arm.funnel || {{}};
    tr.innerHTML = `
      <td><strong>#${{idx+1}}</strong> <span style="color: ${{arm.color}}">●</span> <strong>${{arm.label}}</strong>
        <div style="font-size: 10px; color: var(--text-muted); line-height: 1.2; margin-top: 2px; max-width: 260px;">${{arm.desc || ''}}</div>
      </td>
      <td class="tabular">${{(arm.sharpe_ratio != null ? arm.sharpe_ratio.toFixed(2) : 'NO_DATA')}}</td>
      <td class="tabular">${{(arm.profit_factor ?? 0).toFixed(2)}}</td>
      <td class="tabular">${{(arm.var_95 ?? 0).toFixed(4)}}</td>
      <td class="tabular">${{(arm.mc_p5 ?? 0).toFixed(2)}}</td>
      <td class="tabular">${{funnel.filled || arm.orders_cnt || 0}}</td>
      <td class="tabular">${{arm.stop_loss_exits_count || 0}}</td>
      <td class="tabular">${{arm.merge_rate || 0}}%</td>
      <td class="tabular ${{pnlClass}}"><strong>${{pnlPrefix}}${{Math.abs(arm.total_pnl || 0).toFixed(2)}}</strong></td>
      <td class="tabular">${{(arm.mean_pnl || 0).toFixed(3)}}</td>
      <td class="tabular">${{(arm.std_pnl || 0).toFixed(3)}}</td>
      <td class="tabular negative">$$${{(arm.max_drawdown || 0).toFixed(2)}}</td>
      <td style="max-width: 290px; text-align: left;">
        ${{annotationCellHtml(arm)}}
      </td>
      <td class="tabular"><strong>${{arm.score || 0}}</strong></td>
      <td><span class="badge ${{arm.badge_cls}}">${{arm.readiness}}</span></td>
      <td><a href="/open-dash?port=${{arm.port}}&db=${{encodeURIComponent(arm.db || '')}}" target="_blank" class="btn" style="padding: 3px 8px; font-size: 11px;">Open Dash</a></td>
    `;
    tbody.appendChild(tr);
  }});

  renderCharts();
}}

function renderCharts() {{
  // Zero arms is a valid poll, not a crash -- and it happens for real: the arms
  // file is read once at import and yields [] when it is missing or does not
  // parse (`_load_arms_from_file`), which every resolver then passes through
  // untouched. Every chart below reads fields off a `state.arms[0]`-derived
  // pair (the radar's `label: ctrl.label` was the one that threw), so an empty
  // list used to die mid-render and leave the operator behind a cleared
  // leaderboard with a JS error instead of an explanation (#873). State the empty case and stop.
  const armList = Array.isArray(state.arms) ? state.arms : [];
  const emptyState = document.getElementById('arms-empty-state');
  if (emptyState) {{
    emptyState.textContent = armList.length ? '' : 'No arms in this tournament';
    emptyState.style.display = armList.length ? 'none' : 'block';
  }}
  if (!armList.length) {{
    // A chart drawn from the previous, non-empty poll must not outlive it.
    Object.keys(charts).forEach((key) => {{
      if (charts[key]) {{ charts[key].destroy(); charts[key] = null; }}
    }});
    return;
  }}

  // PnL Line Chart
  const lineCtx = document.getElementById('pnlLineChart');
  if (lineCtx) {{
    if (charts.line) charts.line.destroy();
    const datasets = state.arms.map(arm => ({{
      label: arm.label,
      borderColor: arm.color,
      backgroundColor: arm.color,
      borderWidth: arm.name === 'control' || arm.name === 'deadband-wide' ? 3 : 1.5,
      data: (arm.series || []).map(pt => ({{ x: pt.t, y: pt.cum }})),
      tension: 0.1,
      pointRadius: 0
    }}));
    charts.line = new Chart(lineCtx, {{
      type: 'line',
      data: {{ datasets }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        scales: {{
          x: {{ type: 'linear', title: {{ display: true, text: 'Elapsed Minutes (0 to {initial_data.get('target_hours', 12.0) * 60:g})', color: '#8b9bb4' }}, grid: {{ color: '#1c2436' }} }},
          y: {{ title: {{ display: true, text: 'Cumulative Realized PnL ($)', color: '#8b9bb4' }}, grid: {{ color: '#1c2436' }} }}
        }},
        plugins: {{
          legend: {{ labels: {{ color: '#f0f4fc', font: {{ size: 11 }} }} }}
        }}
      }}
    }});
  }}

  // Scatter Chart (Risk-Return Frontier)
  const scatterCtx = document.getElementById('scatterChart');
  if (scatterCtx) {{
    if (charts.scatter) charts.scatter.destroy();
    charts.scatter = new Chart(scatterCtx, {{
      type: 'scatter',
      data: {{
        datasets: state.arms.map(arm => ({{
          label: arm.label,
          backgroundColor: arm.color,
          data: [{{ x: arm.max_drawdown, y: arm.total_pnl }}],
          pointRadius: 8
        }}))
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        scales: {{
          x: {{ title: {{ display: true, text: 'Max Drawdown ($)', color: '#8b9bb4' }}, grid: {{ color: '#1c2436' }} }},
          y: {{ title: {{ display: true, text: 'Total Realized PnL ($)', color: '#8b9bb4' }}, grid: {{ color: '#1c2436' }} }}
        }},
        plugins: {{ legend: {{ display: false }} }}
      }}
    }});
  }}

  // Radar Chart
  const radarCtx = document.getElementById('radarChart');
  if (radarCtx) {{
    if (charts.radar) charts.radar.destroy();
    const ctrl = state.arms.find(a => /^(control|.*baseline)/.test(a.name)) || state.arms[0];
    // The leaderboard is sorted by PnL, so `arms[0]` is the arm to read against
    // the control. The old pair was pinned to #487's `control` and
    // `deadband-wide` names, which no other run has.
    const rival = state.arms.find(a => a.name !== (ctrl && ctrl.name)) || ctrl;
    const radarTitle = document.getElementById('radar-title');
    if (radarTitle && ctrl && rival) {{
      radarTitle.innerText = 'Multi-Factor Radar: ' + rival.label + ' vs ' + ctrl.label;
    }}
    charts.radar = new Chart(radarCtx, {{
      type: 'radar',
      data: {{
        labels: ['PnL (Norm)', 'Win Rate %', 'Merge Rate %', 'Low Drawdown', 'Profit Factor', 'EV/Trade'],
        datasets: [
          {{
            label: ctrl.label,
            data: [
              Math.max(0, ctrl.total_pnl * 4),
              ctrl.win_rate,
              ctrl.merge_rate,
              Math.max(0, 100 - ctrl.max_drawdown * 8),
              Math.min(100, ctrl.profit_factor * 40),
              Math.max(0, ctrl.mean_pnl * 300)
            ],
            borderColor: ctrl.color,
            backgroundColor: 'rgba(0, 230, 118, 0.2)'
          }},
          {{
            label: rival.label,
            data: [
              Math.max(0, rival.total_pnl * 4),
              rival.win_rate,
              rival.merge_rate,
              Math.max(0, 100 - rival.max_drawdown * 8),
              Math.min(100, rival.profit_factor * 40),
              Math.max(0, rival.mean_pnl * 300)
            ],
            borderColor: rival.color,
            backgroundColor: 'rgba(118, 255, 3, 0.2)'
          }}
        ]
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        scales: {{ r: {{ grid: {{ color: '#232c3f' }}, angleLines: {{ color: '#232c3f' }}, pointLabels: {{ color: '#8b9bb4' }} }} }},
        plugins: {{ legend: {{ labels: {{ color: '#f0f4fc' }} }} }}
      }}
    }});
  }}

  // EV Bar Chart
  const evCtx = document.getElementById('evBarChart');
  if (evCtx) {{
    if (charts.ev) charts.ev.destroy();
    charts.ev = new Chart(evCtx, {{
      type: 'bar',
      data: {{
        labels: state.arms.map(a => a.label),
        datasets: [
          {{
            label: 'EV per Trade ($)',
            data: state.arms.map(a => a.mean_pnl),
            backgroundColor: state.arms.map(a => a.mean_pnl >= 0 ? '#00e676' : '#ff5252')
          }},
          {{
            label: 'Std Dev (Risk Dispersion)',
            data: state.arms.map(a => a.std_pnl),
            backgroundColor: '#00b0ff'
          }}
        ]
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        scales: {{
          x: {{ grid: {{ color: '#1c2436' }} }},
          y: {{ grid: {{ color: '#1c2436' }} }}
        }},
        plugins: {{ legend: {{ labels: {{ color: '#f0f4fc' }} }} }}
      }}
    }});
  }}

  // Profit Factor Chart
  const pfCtx = document.getElementById('pfBarChart');
  if (pfCtx) {{
    if (charts.pf) charts.pf.destroy();
    charts.pf = new Chart(pfCtx, {{
      type: 'bar',
      data: {{
        labels: state.arms.map(a => a.label),
        datasets: [{{
          label: 'Profit Factor',
          data: state.arms.map(a => a.profit_factor),
          backgroundColor: state.arms.map(a => a.profit_factor >= 1.5 ? '#00e676' : (a.profit_factor >= 1.0 ? '#ffd600' : '#ff5252'))
        }}]
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        scales: {{
          x: {{ grid: {{ color: '#1c2436' }} }},
          y: {{ grid: {{ color: '#1c2436' }} }}
        }},
        plugins: {{ legend: {{ display: false }} }}
      }}
    }});
  }}

  // Merges vs Exits Stacked Bar Chart
  const mergeCtx = document.getElementById('mergeStackedBarChart');
  if (mergeCtx) {{
    if (charts.merge) charts.merge.destroy();
    charts.merge = new Chart(mergeCtx, {{
      type: 'bar',
      data: {{
        labels: state.arms.map(a => a.label),
        datasets: [
          {{
            label: 'Paired Merges ($1.00 Payout)',
            data: state.arms.map(a => a.merges_count),
            backgroundColor: '#00e676'
          }},
          {{
            label: 'Single-Leg Stopped Exits',
            data: state.arms.map(a => a.exits_count),
            backgroundColor: '#ff5252'
          }}
        ]
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        scales: {{
          x: {{ stacked: true, grid: {{ color: '#1c2436' }} }},
          y: {{ stacked: true, grid: {{ color: '#1c2436' }} }}
        }},
        plugins: {{ legend: {{ labels: {{ color: '#f0f4fc' }} }} }}
      }}
    }});
  }}

  // Monte Carlo Bar Chart
  const mcCtx = document.getElementById('mcBarChart');
  if (mcCtx) {{
    if (charts.mc) charts.mc.destroy();
    charts.mc = new Chart(mcCtx, {{
      type: 'bar',
      data: {{
        labels: state.arms.map(a => a.label),
        datasets: [
          {{ label: 'Worst Case (5th %ile)', data: state.arms.map(a => a.mc_p5), backgroundColor: '#ff5252' }},
          {{ label: 'Median Expectation (50th %ile)', data: state.arms.map(a => a.mc_p50), backgroundColor: '#ffd600' }},
          {{ label: 'Best Case (95th %ile)', data: state.arms.map(a => a.mc_p95), backgroundColor: '#00e676' }}
        ]
      }},
      options: {{
        responsive: true,
        maintainAspectRatio: false,
        scales: {{
          x: {{ grid: {{ color: '#1c2436' }} }},
          y: {{ title: {{ display: true, text: 'Projected 100-Trade PnL ($)', color: '#8b9bb4' }}, grid: {{ color: '#1c2436' }} }}
        }},
        plugins: {{ legend: {{ labels: {{ color: '#f0f4fc' }} }} }}
      }}
    }});
  }}
}}

let lastUpdateTime = new Date().toLocaleTimeString();

function fetchLiveStats() {{
  const statusEl = document.getElementById('refresh-status');
  const tourneyKey = (state && state.tourney_key) ? state.tourney_key : '799';
  fetch('/api/stats?tourney=' + tourneyKey)
    .then(r => {{
      if (!r.ok) {{
        throw new Error('HTTP ' + r.status);
      }}
      return r.json();
    }})
    .then(data => {{
      state = data;
      lastUpdateTime = new Date().toLocaleTimeString();
      if (statusEl) {{
        statusEl.textContent = 'Updated ' + lastUpdateTime;
        statusEl.style.color = 'var(--text-muted)';
      }}
      if (data && data.current_setup_key) {{
        updateCurrentSetupBadge(data.current_setup_key);
      }}
      renderAll();
    }})
    .catch(err => {{
      console.log('Stats refresh offline or error:', err);
      if (statusEl) {{
        statusEl.textContent = '⚠️ Update failed (' + (lastUpdateTime ? 'last ' + lastUpdateTime : 'offline') + ')';
        statusEl.style.color = 'var(--amber)';
      }}
    }});
}}

function rowFormattedValue(r) {{
  if (!r) return null;
  if (typeof r.formatted_value === 'string' && r.formatted_value.trim()) {{
    return r.formatted_value.trim();
  }}
  const baseN = r.base_n || 0;
  if (r.type === 'proportion' && baseN > 0 && r.successes != null) {{
    return (100.0 * Number(r.successes) / Number(baseN)).toFixed(1) + '%';
  }}
  return null;
}}

function rowNumericValue(r) {{
  if (!r) return null;
  if (typeof r.value === 'number' && !isNaN(r.value)) return r.value;
  const baseN = r.base_n || 0;
  if (r.type === 'proportion' && baseN > 0 && r.successes != null) {{
    return 100.0 * Number(r.successes) / Number(baseN);
  }}
  const fmt = rowFormattedValue(r);
  if (!fmt) return null;
  const s = fmt.trim().toLowerCase();
  if (s.endsWith('s') || s.endsWith('m')) {{
    let total = 0.0;
    const parts = s.split(/\\s+/);
    let ok = true;
    for (const p of parts) {{
      if (p.endsWith('h')) total += parseFloat(p.slice(0, -1)) * 3600.0;
      else if (p.endsWith('m')) total += parseFloat(p.slice(0, -1)) * 60.0;
      else if (p.endsWith('s')) total += parseFloat(p.slice(0, -1));
      else ok = false;
    }}
    if (ok && !isNaN(total)) return total;
  }}
  const cleaned = fmt.replace(/[$%,]/g, '').trim();
  const num = parseFloat(cleaned);
  return isNaN(num) ? null : num;
}}

function kpiValueTone(r, isSufficient) {{
  if (!r) return '';
  const val = rowNumericValue(r);
  if (val == null) {{
    if (isSufficient) return 'kpi-val-good';
    if ((r.base_n || 0) > 0) return 'kpi-val-warn';
    return '';
  }}
  const key = String(r.key || '');
  if (key === 'expectancy_usd') {{
    if (val < 0.0) return 'kpi-val-bad';
    return val >= 0.15 ? 'kpi-val-good' : 'kpi-val-warn';
  }}
  if (key === 'avg_return_pct') {{
    if (val < 0.0) return 'kpi-val-bad';
    return val >= 2.0 ? 'kpi-val-good' : 'kpi-val-warn';
  }}
  if (key === 'win_rate') {{
    if (val < 50.0) return 'kpi-val-bad';
    return val >= 65.0 ? 'kpi-val-good' : 'kpi-val-warn';
  }}
  if (key === 'stop_loss_rate') {{
    if (val <= 25.0) return 'kpi-val-good';
    return val <= 40.0 ? 'kpi-val-warn' : 'kpi-val-bad';
  }}
  if (key === 'avg_stop_loss_depth_pct') {{
    const depth = Math.abs(val);
    if (depth <= 8.0) return 'kpi-val-good';
    return depth <= 15.0 ? 'kpi-val-warn' : 'kpi-val-bad';
  }}
  if (key === 'merge_rate') {{
    if (val < 45.0) return 'kpi-val-bad';
    return val >= 60.0 ? 'kpi-val-good' : 'kpi-val-warn';
  }}
  if (key === 'avg_duration_merge') {{
    if (val <= 180.0) return 'kpi-val-good';
    return val <= 420.0 ? 'kpi-val-warn' : 'kpi-val-bad';
  }}
  if (key === 'avg_duration_stop') {{
    if (val <= 180.0) return 'kpi-val-good';
    return val <= 360.0 ? 'kpi-val-warn' : 'kpi-val-bad';
  }}
  if (val < 0.0) return 'kpi-val-bad';
  return val > 0.0 ? 'kpi-val-good' : 'kpi-val-warn';
}}

function suffMiniHtml(rows) {{
  if (!rows || !rows.length) {{
    return '<div style="font-size: 11.5px; color: var(--text-muted);">' +
      'Per-KPI Sufficiency: unmeasured — arm has not reported yet.</div>';
  }}
  let body = '';
  rows.forEach(r => {{
    const lv95 = ((r.levels || []).filter(lv => lv.confidence_pct === 95)[0]) || {{}};
    const req = (lv95.required_n != null ? Number(lv95.required_n).toLocaleString() : '—');
    const prog = (typeof lv95.progress_pct === 'number' ? lv95.progress_pct : null);
    const fmtVal = rowFormattedValue(r);
    const baseN = r.base_n || 0;
    const isSuff = r.sufficient_95 === true;
    const tone = kpiValueTone(r, isSuff);
    let verdict, badge;
    if (isSuff) {{ verdict = fmtVal != null ? fmtVal : 'SUFFICIENT'; badge = tone || 'kpi-val-good'; }}
    else if (fmtVal != null) {{ verdict = fmtVal; badge = tone || 'kpi-val-warn'; }}
    else if (lv95.required_n == null) {{ verdict = '—'; badge = ''; }}
    else {{ verdict = 'n=' + baseN; badge = 'kpi-val-warn'; }}
    const bar = (prog !== null)
      ? '<div style="background: rgba(255,255,255,0.08); border-radius: 4px; height: 6px; min-width: 48px;">' +
        '<div style="background: var(--green); height: 6px; border-radius: 4px; width: ' + prog + '%;"></div></div>'
      : '<span style="color: var(--text-muted);">—</span>';
    const valHtml = badge
      ? '<span class="kpi-val ' + badge + ' tabular">' + escHtml(verdict) + '</span>'
      : '<span style="color: var(--text-muted);">—</span>';
    body += '<tr><td style="padding: 3px 6px 3px 0;">' + escHtml(r.label || r.key) + '</td>' +
      '<td class="tabular" style="padding: 3px 6px; white-space: nowrap; text-align: right;">' + valHtml + '</td>' +
      '<td class="tabular" style="padding: 3px 6px; text-align: right; color: var(--text-muted);">' + req + '</td>' +
      '<td style="padding: 3px 6px;">' + bar + '</td>' +
      '<td class="tabular" style="padding: 3px 0 3px 6px; white-space: nowrap; text-align: right; color: var(--text-muted);">' + baseN + ' ' + escHtml(r.base || 'closes') + '</td></tr>';
  }});
  return '<div style="margin: 8px 0 4px; padding-top: 8px; border-top: 1px solid var(--border);">' +
    '<div style="font-size: 11px; font-weight: 700; color: var(--text-muted); ' +
    'text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px;">' +
    'Per-KPI Sufficiency @95%</div>' +
    '<table style="width: 100%; font-size: 11.5px; border-collapse: collapse;">' + body + '</table></div>';
}}

// Decision-gates view state: the selected confidence level (90/95/99,
// striving for 95) and the arms hidden by the operator's filter. This is a
// view choice, not poll data, so it survives the 5-second repaints.
var dgConfidence = 95;
var dgHiddenArms = {{}};
var dgControlsWired = false;

function dgVisibleArms(arms) {{
  return (arms || []).filter(a => !dgHiddenArms[a.name]);
}}

function dgVerdictFor(name) {{
  const vs = (state.decision_gates && state.decision_gates.arm_verdicts) || [];
  for (const v of vs) {{
    if (v.name === name) return v;
  }}
  return {{}};
}}

function dgCellHtml(row, confidence) {{
  if (!row) return '<span style="color: var(--text-muted);">unmeasured</span>';
  let lv = null;
  for (const c of (row.levels || [])) {{
    if (c.confidence_pct === confidence) lv = c;
  }}
  lv = lv || {{}};
  const req = lv.required_n;
  const baseN = row.base_n || 0;
  const prog = lv.progress_pct;
  const fmtVal = rowFormattedValue(row);
  if (req == null && fmtVal == null) return '<span style="color: var(--text-muted);">unmeasured</span>';
  let sufficient = row.sufficient_95 === true && confidence === 95;
  if (confidence !== 95 && typeof prog === 'number' && prog >= 100) sufficient = true;
  const label = sufficient ? (fmtVal != null ? fmtVal : 'SUFFICIENT') : (fmtVal != null ? fmtVal : ('n=' + baseN));
  const tone = kpiValueTone(row, sufficient) || (sufficient ? 'kpi-val-good' : 'kpi-val-warn');
  const valHtml = '<span class="kpi-val ' + tone + ' tabular">' + escHtml(label) + '</span>';
  const bar = (typeof prog === 'number')
    ? '<div style="background: rgba(255,255,255,0.08); border-radius: 4px; height: 6px; margin-top: 3px;">' +
      '<div style="background: var(--green); height: 6px; border-radius: 4px; width: ' + prog + '%;"></div></div>'
    : '';
  const reqTxt = (req != null ? Number(req).toLocaleString() : '—');
  return valHtml + '<div style="font-size: 10.5px; color: var(--text-muted); margin-top: 2px;"><span class="tabular">' +
    baseN + ' / ' + reqTxt + '</span></div>' + bar;
}}

function dgMatrixHtml(arms, confidence) {{
  const list = dgVisibleArms(arms);
  if (!list.length) {{
    return '<div style="font-size: 12px; color: var(--text-muted);">' +
      'All arms filtered out — tick at least one arm above.</div>';
  }}
  const keys = [];
  const labels = {{}};
  list.forEach(a => {{
    (a.sufficiency_rows || []).forEach(r => {{
      if (r && r.key && !(r.key in labels)) {{ keys.push(r.key); labels[r.key] = r.label || r.key; }}
    }});
  }});
  if (!keys.length) {{
    return '<div style="font-size: 12px; color: var(--text-muted);">' +
      'KPI matrix unmeasured — no arm has reported sufficiency rows yet.</div>';
  }}
  const byArm = {{}};
  list.forEach(a => {{
    const m = {{}};
    (a.sufficiency_rows || []).forEach(r => {{ if (r && r.key) m[r.key] = r; }});
    byArm[a.name] = m;
  }});
  let head = '<th style="text-align: left;">KPI x Arm @ ' + confidence + '%</th>';
  list.forEach(a => {{
    head += '<th>' + escHtml(a.label || a.name) + '</th>';
  }});
  let body = '';
  keys.forEach(k => {{
    let cells = '<td style="text-align: left;">' + escHtml(labels[k]) + '</td>';
    list.forEach(a => {{
      cells += '<td>' + dgCellHtml(byArm[a.name][k] || null, confidence) + '</td>';
    }});
    body += '<tr>' + cells + '</tr>';
  }});
  return '<table id="dg-matrix-table" style="width: 100%; font-size: 11.5px; border-collapse: collapse;">' +
    '<thead><tr>' + head + '</tr></thead><tbody>' + body + '</tbody></table>' +
    '<div style="font-size: 11px; color: var(--text-muted); margin-top: 6px;">' +
    'Current value and Have / Need observations at ' + confidence + '% confidence. ' +
    'Values are colored green (good), yellow (average), or red (bad) by KPI range; ' +
    'unmeasured means no observations yet.</div>';
}}

function renderDgMatrix() {{
  const el = document.getElementById('dg-matrix-container');
  if (!el) return;
  el.innerHTML = dgMatrixHtml(state.arms, dgConfidence);
}}

function dgWireControls() {{
  if (dgControlsWired) return;
  dgControlsWired = true;
  const toggle = document.getElementById('dg-conf-toggle');
  if (toggle) {{
    toggle.addEventListener('click', ev => {{
      const btn = ev.target && ev.target.closest ? ev.target.closest('[data-dg-conf]') : null;
      if (!btn) return;
      dgConfidence = parseInt(btn.getAttribute('data-dg-conf'), 10) || 95;
      const btns = toggle.querySelectorAll('[data-dg-conf]');
      btns.forEach(b => {{
        const on = b.getAttribute('data-dg-conf') === String(dgConfidence);
        b.style.border = on ? '1px solid var(--green)' : '1px solid var(--border)';
        b.style.background = on ? 'var(--green-glow)' : 'transparent';
        b.style.color = on ? 'var(--text-primary)' : 'var(--text-muted)';
      }});
      renderDgMatrix();
    }});
  }}
  const filter = document.getElementById('dg-arm-filter');
  if (filter) {{
    filter.addEventListener('change', ev => {{
      const box = ev.target && ev.target.closest ? ev.target.closest('[data-dg-arm-toggle]') : null;
      if (!box) return;
      const name = box.getAttribute('data-dg-arm-toggle');
      if (box.checked) {{ delete dgHiddenArms[name]; }}
      else {{ dgHiddenArms[name] = true; }}
      renderDgArmCards(state.arms);
      renderDgMatrix();
    }});
  }}
}}

function renderDgArmCards(arms) {{
  const container = document.getElementById('dg-arm-cards-container');
  if (!container) return;
  let html = '';
  dgVisibleArms(arms).forEach(a => {{
    const v = dgVerdictFor(a.name);
    const pnl = a.total_pnl || 0.0;
    const pnlStr = (pnl >= 0 ? '+$' : '-$') + Math.abs(pnl).toFixed(2);
    const pnlCls = pnl >= 0 ? 'positive' : 'negative';
    const quoted = (a.funnel && a.funnel.quoted) || a.quoted_cnt || 0;
    const isQuoting = quoted > 0;
    let statusLabel = (a.status || 'pending').toUpperCase();
    let statusCls = a.status === 'ok' ? 'badge-green' : 'badge-amber';
    if (a.status === 'ok') {{
      statusLabel = 'ACTIVE';
      statusCls = 'badge-green';
    }} else if (a.status === 'no_trades' && isQuoting) {{
      statusLabel = 'QUOTING (' + quoted + ')';
      statusCls = 'badge-green';
    }}
    html += '<div class="m-card" data-dg-arm-card="' + escHtml(a.name || '') + '" style="border: 1px solid var(--border);">' +
      '<div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 8px;">' +
        '<div>' +
          '<div style="font-weight: 700; font-size: 14px; color: var(--text-primary);">' + escHtml(a.label || a.name) + '</div>' +
          '<div style="font-size: 11px; color: var(--text-muted); font-family: monospace;">' + escHtml(a.name) + ' | Port ' + (a.port || 0) + '</div>' +
        '</div>' +
        '<span class="badge ' + statusCls + '">' + escHtml(statusLabel) + '</span>' +
      '</div>' +
      '<div style="margin-bottom: 4px;"><span class="badge ' + (v.badge || 'badge-amber') + '" style="font-size: 10px; padding: 3px 8px;">' + escHtml(v.verdict || 'PENDING REHEARSAL DATA') + '</span></div>' +
      '<div style="font-size: 11.5px; color: var(--text-muted); line-height: 1.4; margin-bottom: 4px;">' + escHtml(v.desc || 'No completed trades recorded for this arm yet.') + '</div>' +
      '<div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin: 12px 0; padding: 8px 0; border-top: 1px solid var(--border); border-bottom: 1px solid var(--border); font-size: 12px;">' +
        '<div>PnL: <strong class="tabular ' + pnlCls + '">' + pnlStr + '</strong></div>' +
        '<div>Quoted: <strong class="tabular">' + quoted.toLocaleString() + '</strong></div>' +
        '<div>Trades: <strong class="tabular">' + (a.trades_count || 0).toLocaleString() + '</strong></div>' +
        '<div title="{html.escape(SHARPE_FORMULA_TEXT)}">Sharpe Ratio: <strong class="tabular">' + (a.sharpe_ratio != null ? a.sharpe_ratio.toFixed(2) : 'NO_DATA') + '</strong></div>' +
        '<div>Max DD: <strong class="tabular">$' + (a.max_drawdown || 0).toFixed(2) + '</strong></div>' +
        '<div>Merge Rate: <strong class="tabular">' + (a.merge_rate || 0).toFixed(1) + '%</strong></div>' +
      '</div>' +
      suffMiniHtml(a.sufficiency_rows || []) +
      '<div style="font-size: 11.5px; color: var(--text-muted); line-height: 1.4;">' + escHtml(a.desc || '') + '</div>' +
    '</div>';
  }});
  container.innerHTML = html;
}}

function updateDecisionGatesUI() {{
  if (!state || !state.decision_gates) return;
  dgWireControls();
  if (state.arms) {{
    renderDgArmCards(state.arms);
    renderDgMatrix();
  }}
  const dg = state.decision_gates;
  const runTrades = document.getElementById('dg-run-trades');
  if (runTrades) runTrades.textContent = (dg.total_trades || 0).toLocaleString();
  const vol = dg.volume_check || {{}};
  const volBadge = document.getElementById('dg-volume-badge');
  if (volBadge) {{
    volBadge.className = 'badge ' + (vol.badge || 'badge-amber');
    volBadge.textContent = vol.status || 'NO_DATA';
  }}
  const volText = document.getElementById('dg-volume-text');
  if (volText) volText.textContent = vol.text || '';
}}
// Polling interval
setInterval(fetchLiveStats, 5000);
</script>
</body>
</html>
"""


_STATS_CACHE: dict[str, dict[str, Any]] = {}
_STATS_CACHE_TIME: dict[str, float] = {}
_STATS_CACHE_LOCK = threading.Lock()
_STATS_CACHE_TTL_SEC: float = 3.0


def get_cached_stats(tourney_key: str = CURRENT_SETUP_KEY) -> dict[str, Any]:
    global _STATS_CACHE, _STATS_CACHE_TIME
    with _STATS_CACHE_LOCK:
        now = time.time()
        last_time = _STATS_CACHE_TIME.get(tourney_key, 0.0)
        cached = _STATS_CACHE.get(tourney_key)
        if not cached or (now - last_time) >= _STATS_CACHE_TTL_SEC:
            _STATS_CACHE[tourney_key] = collect_all_stats(tourney_key)
            _STATS_CACHE_TIME[tourney_key] = now
        return _STATS_CACHE[tourney_key]



def open_dash_target(query: str) -> tuple[int, Path] | None:
    """Parse `/open-dash`'s query into (arm port, db file), or None when out of bounds.

    A GET that kills a listening process must not be aimable at whatever the
    caller likes -- any page the operator has open can navigate to it. Only an
    arm port is accepted, and only a store inside `data/tournaments`.
    """
    import urllib.parse
    params = urllib.parse.parse_qs(query, keep_blank_values=True)
    try:
        port = int(params.get("port", ["0"])[0])
    except ValueError:
        return None
    if not 8801 <= port <= 8810:
        return None
    db_path = params.get("db", [""])[0]
    db_file = (ROOT / db_path if db_path else ROOT).resolve()
    if (ROOT / "data" / "tournaments").resolve() not in db_file.parents:
        return None
    return port, db_file


def _ensure_arm_dashboard(port: int, db_path: str) -> None:
    from scripts.reconcile_tournament_stack import (
        is_port_listening, kill_process_on_port, _detached_kwargs, ROOT
    )
    from core_brain.code_revision import decision_code_mtime
    import urllib.error
    import urllib.request
    import subprocess
    import sys
    import time
    from pathlib import Path

    tree_mtime = decision_code_mtime() or time.time()
    needs_restart = False
    full_db = Path(db_path) if Path(db_path).is_absolute() else (ROOT / db_path)

    if not is_port_listening(port):
        needs_restart = True
    else:
        try:
            url = f"http://127.0.0.1:{port}/api/system/status"
            req = urllib.request.Request(url, headers={"User-Agent": "Cockpit-AutoEnsure"})
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                dash_started = data.get("services", {}).get("dash", {}).get("started_at", 0)
                if dash_started and dash_started < tree_mtime:
                    needs_restart = True
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, ConnectionRefusedError) or "refused" in str(exc.reason).lower():
                needs_restart = True
            else:
                print(f"[cockpit] dash :{port} status timeout; leaving it running ({exc})")
        except Exception as exc:
            print(f"[cockpit] dash :{port} status check failed; leaving it running ({exc})")

    if needs_restart:
        kill_process_on_port(port)
        log_dir = ROOT / "runtime" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        lf = open(log_dir / f"dash_{port}.log", "a", encoding="utf-8")
        cmd = [sys.executable, "-m", "dashboard.server", "--port", str(port), "--db", str(full_db)]
        subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT, **_detached_kwargs())
        for _ in range(15):
            if is_port_listening(port):
                break
            time.sleep(0.1)

class CockpitHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        # Mute normal HTTP log spam
        pass

    def do_GET(self) -> None:
        clean_path = self.path.split("?")[0].rstrip("/")
        query = self.path.split("?")[1] if "?" in self.path else ""

        # Route 1: the current setup page (/ and its aliases) -- the only page.
        # Ten arms are listed with their hypothesis; every metric reads NO_DATA
        # until the 720-minute run writes its record. The purged past-run paths
        # (/492, /484, /608) are absent from PAGE_PATHS, so they fall to the 404.
        page_key = tourney_key_for_path(self.path)
        if page_key is not None:
            stats = get_cached_stats(page_key)
            html = generate_cockpit_html(stats)
            data = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        # Route 2: JSON API (/api/stats?tourney=<key>) -- the page's Refresh
        # button and the menu's service probe (`probe_path` in dashboard/server.py).
        # A retired key serves the page's own tournament instead of resurrecting
        # a purged one.
        elif clean_path == "/open-dash":
            target = open_dash_target(query)
            if target is None:
                self.send_error(400, "open-dash needs port=8801..8810 and a db= inside data/tournaments")
                return
            port, db_file = target
            _ensure_arm_dashboard(port, str(db_file))
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{port}")
            self.end_headers()
        elif clean_path == "/api/stats":
            tourney = CURRENT_SETUP_KEY
            for param in query.split("&"):
                if param.startswith("tourney="):
                    tourney = param.split("=")[1].strip()
            if tourney not in SERVED_TOURNAMENT_KEYS:
                tourney = CURRENT_SETUP_KEY
            stats = get_cached_stats(tourney)
            data = json.dumps(stats).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_response(404)
            self.end_headers()


def run_cockpit_server(port: int = 8800) -> None:
    reports_dir = ROOT / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    # Export the standalone static copy of the one page there is: the current
    # setup. The past-run snapshots (#484, #492, #608) are no longer written --
    # those pages are purged, and their stale files are deleted on the way in.
    stats_current = collect_all_stats(CURRENT_SETUP_KEY)
    static_current = generate_cockpit_html(stats_current)
    out_file_main = reports_dir / "tournament_cockpit.html"
    out_file_main.write_text(static_current, encoding="utf-8")
    print(f"Static Cockpit (#487 current setup) generated at: {out_file_main}")

    # The purged pages leave stale snapshots behind on every machine that ran
    # the old server; drop them so nothing links to a page that no longer exists.
    for stale in (
        "tournament_cockpit_484.html",
        "tournament_cockpit_492.html",
        "tournament_cockpit_487.html",
        "tournament_cockpit_608.html",
    ):
        with contextlib.suppress(OSError):
            (reports_dir / stale).unlink()

    class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    server = ThreadedServer(("127.0.0.1", port), CockpitHandler)
    print(f"Tournament Cockpit server running at http://127.0.0.1:{port} (page: / and /487 [#487 current setup]; the purged past-run pages /484, /492 and /608 return 404)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Cockpit server...")
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Tournament Cockpit Server")
    parser.add_argument("--port", type=int, default=8800, help="Port to serve on (default: 8800)")
    args = parser.parse_args()
    run_cockpit_server(args.port)


if __name__ == "__main__":
    main()
