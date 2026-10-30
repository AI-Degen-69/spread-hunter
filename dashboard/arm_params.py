"""Arm-specific effective configuration for the Strategy Parameters panel (#866).

Dashboard-only, read-only. Turns a tournament plan + arm into the effective
engine configs by running the real loaders in a short-lived non-trading child
process with a worker-style environment.

The child does exactly what a shadow worker's entrypoint does before loading
(`declare_rehearsal()`, then `load()`), then prints JSON and exits. It never
trades, never loads `.env`, and never touches the dashboard process environment
-- which matters because the dashboard server is multi-threaded and both
loaders read process-wide `os.environ`.

Engine imports stay inside functions (the same pattern `get_parameters` uses)
so importing this module can never break dashboard startup.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import math
import os
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, NamedTuple, Optional

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent

#: How long the parent waits for one child evaluation before giving up.
CHILD_TIMEOUT_SEC = 30.0

#: Error codes returned in `ArmParamsError.code`; surfaced as `arm_error`.
NO_PLAN = "NO_PLAN"
MALFORMED_PLAN = "MALFORMED_PLAN"
UNKNOWN_ARM = "UNKNOWN_ARM"
AMBIGUOUS_ARM = "AMBIGUOUS_ARM"
EVAL_FAILED = "EVAL_FAILED"

#: What the child reports when a plan override has no reader: the shipped
#: defaults still apply because the child (like a worker) never loads `.env`.
SHIPPED_DEFAULTS_BASIS = (
    "plan overrides applied to shipped defaults and presets; "
    "operator .env is not loaded"
)

_ROUND_SUFFIX_RE = re.compile(r"-\d+$")


class ArmParamsError(Exception):
    """A resolution/evaluation failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ArmEvaluation(NamedTuple):
    """One arm's effective configs as rebuilt MakerConfig objects."""

    core: Any
    scoring: Optional[Any]
    scoring_error: Optional[str]
    basis: str = SHIPPED_DEFAULTS_BASIS


def _strip_round_suffix(name: str) -> str:
    return _ROUND_SUFFIX_RE.sub("", str(name or ""))


def default_tournament_root() -> Path:
    return REPO_ROOT / "data" / "tournaments"


def _read_plan_json(plan_path: Path) -> dict[str, Any]:
    try:
        with open(plan_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        raise ArmParamsError(MALFORMED_PLAN, f"{plan_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ArmParamsError(MALFORMED_PLAN, f"{plan_path}: top level is not an object")
    return data


def read_record_arms(plan_path: Path) -> list[dict[str, Any]]:
    """Validated arms from a plan record; raises MALFORMED_PLAN when untrusted."""
    data = _read_plan_json(plan_path)
    if "arms" not in data:
        raise ArmParamsError(MALFORMED_PLAN, f"{plan_path}: 'arms' is missing")
    record_arms = data["arms"]
    if not isinstance(record_arms, list):
        raise ArmParamsError(MALFORMED_PLAN, f"{plan_path}: 'arms' is not a list")
    for i, arm in enumerate(record_arms):
        if not isinstance(arm, dict):
            raise ArmParamsError(MALFORMED_PLAN, f"{plan_path}: arm {i} is not an object")
        if not isinstance(arm.get("name"), str) or not arm["name"]:
            raise ArmParamsError(MALFORMED_PLAN, f"{plan_path}: arm {i} has no string 'name'")
        env = arm.get("env", {})
        if not isinstance(env, dict) or any(
            not isinstance(k, str) or not isinstance(v, str) for k, v in env.items()
        ):
            raise ArmParamsError(
                MALFORMED_PLAN, f"{plan_path}: arm {i} ({arm.get('name')}) has a non-string env map"
            )
    return record_arms


def _ranked_plans(tourn_root: Path) -> list[tuple[tuple[int, str], Path]]:
    from core_brain.shadow_run import parse_tournament_run_dir_name

    ranked: list[tuple[tuple[int, str], Path]] = []
    if not tourn_root.is_dir():
        return ranked
    for candidate in tourn_root.iterdir():
        parsed = parse_tournament_run_dir_name(candidate)
        plan = candidate / "plan.json"
        if parsed is None or not plan.is_file():
            continue
        ranked.append(((parsed["run_number"] or 0, parsed["stamp"]), plan))
    ranked.sort(key=lambda item: item[0])
    return ranked


def list_plan_catalog(tourn_root: Path | None = None) -> list[dict[str, Any]]:
    """Newest plan per issue with its arm names; broken plans carry record_error."""
    root = Path(tourn_root) if tourn_root is not None else default_tournament_root()
    from core_brain.shadow_run import parse_tournament_run_dir_name

    newest: dict[int, tuple[tuple[int, str], Path]] = {}
    if root.is_dir():
        for candidate in root.iterdir():
            parsed = parse_tournament_run_dir_name(candidate)
            plan = candidate / "plan.json"
            if parsed is None or not plan.is_file():
                continue
            rank = (parsed["run_number"] or 0, parsed["stamp"])
            prev = newest.get(parsed["issue"])
            if prev is None or rank > prev[0]:
                newest[parsed["issue"]] = (rank, plan)
    catalog = []
    for issue in sorted(newest):
        _, plan = newest[issue]
        try:
            arms = [a["name"] for a in read_record_arms(plan)]
            record_error = None
        except ArmParamsError as exc:
            arms = []
            record_error = f"{exc.code}: {exc}"
        catalog.append({
            "plan_key": plan.parent.name,
            "issue": issue,
            "run_number": _plan_run_number(plan),
            "arms": arms,
            "record_error": record_error,
        })
    return catalog


def _plan_run_number(plan_path: Path) -> Optional[int]:
    from core_brain.shadow_run import parse_tournament_run_dir_name

    parsed = parse_tournament_run_dir_name(plan_path.parent)
    return parsed["run_number"] if parsed else None


def resolve_plan_path(*, plan: str | None = None, tourney: int | str | None = None,
                      tourn_root: Path | None = None) -> Path:
    """Resolve which plan record to read; raises NO_PLAN when nothing qualifies."""
    root = Path(tourn_root) if tourn_root is not None else default_tournament_root()
    if plan:
        # Confine explicit plans to the tournament runs folder: a plan key is a
        # folder name, never a path. Anything escaping the root is refused.
        candidate = (root / plan / "plan.json").resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            raise ArmParamsError(NO_PLAN, f"no plan record at {plan!r}") from None
        if candidate.is_file():
            return candidate
        raise ArmParamsError(NO_PLAN, f"no plan record at {plan!r}")
    if tourney is not None:
        try:
            wanted = int(str(tourney).strip())
        except (TypeError, ValueError):
            raise ArmParamsError(NO_PLAN, f"no tournament record for issue {tourney!r}") from None
        from core_brain.shadow_run import latest_tournament_record

        found = latest_tournament_record(wanted, root)
        if found is not None:
            return found
        raise ArmParamsError(NO_PLAN, f"no tournament record for issue {tourney}")
    ranked = _ranked_plans(root)
    if not ranked:
        raise ArmParamsError(NO_PLAN, "no launched tournament plan found")
    return ranked[-1][1]


def resolve_arm(plan_path: Path, arm_name: str) -> dict[str, Any]:
    """Resolve one arm by exact name, then unique round-suffix match."""
    arms = read_record_arms(plan_path)
    for arm in arms:
        if arm["name"] == arm_name:
            return arm
    stripped = [_strip_round_suffix(a["name"]) for a in arms]
    matches = [arm for arm, base in zip(arms, stripped) if base == arm_name]
    if len(matches) == 1:
        return matches[0]
    if matches:
        names = ", ".join(a["name"] for a in matches)
        raise ArmParamsError(AMBIGUOUS_ARM, f"{arm_name!r} matches several arms: {names}")
    raise ArmParamsError(UNKNOWN_ARM, f"arm {arm_name!r} not in {plan_path.parent.name}")


def build_child_env(arm: dict[str, Any]) -> dict[str, str]:
    """Worker-style env: stripped parent env + arm overrides + arm store.

    Mirrors `launch_tournament` in scripts/shadow_tournament.py: inherited
    `BOT_*`/`FILTER_*` are dropped so the arm's own map decides, then the
    arm's database path is pinned.
    """
    clean = {k: v for k, v in os.environ.items()
             if not (k.startswith("BOT_") or k.startswith("FILTER_"))}
    env = {**clean, **arm.get("env", {})}
    db_path = arm.get("db_path")
    if isinstance(db_path, str) and db_path:
        env["SPREAD_HUNTER_DB"] = db_path
    return env


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, bool | int | str) or value is None:
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"non-finite float {value!r} is not serializable")
        return value
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _dump_configs(core: Any, scoring: Any | None) -> dict[str, Any]:
    core_dict = {k: _to_jsonable(v) for k, v in dataclasses.asdict(core).items()}
    # `dataclasses.asdict` skips properties; the picking bar is read through
    # `filter_common_spread_max_usd` everywhere (dashboard, ranker), so ship it.
    for prop in ("filter_common_spread_max_usd",):
        if hasattr(core, prop):
            core_dict[prop] = _to_jsonable(getattr(core, prop))
    scoring_dict = None
    if scoring is not None:
        scoring_dict = {k: _to_jsonable(v) for k, v in dataclasses.asdict(scoring).items()}
        for prop in ("filter_common_spread_max_usd",):
            if hasattr(scoring, prop):
                scoring_dict[prop] = _to_jsonable(getattr(scoring, prop))
    return {"core": core_dict, "scoring": scoring_dict}


def child_main() -> int:
    """Child entry point: load with the inherited worker-style env, print JSON."""
    from core_brain.rehearsal import declare_rehearsal

    declare_rehearsal()
    try:
        from core_brain.config import load as load_core

        core = load_core(for_display=True)
    except Exception as exc:  # noqa: BLE001 -- reported, not raised
        print(json.dumps({"error": f"core config failed: {type(exc).__name__}: {exc}"}))
        return 1
    scoring = None
    scoring_error: str | None = None
    try:
        from scoring.config import load as load_scoring

        scoring = load_scoring()
    except Exception as exc:  # noqa: BLE001 -- one loader must not hide the other
        scoring_error = f"{type(exc).__name__}: {exc}"
        log.warning("arm_params child: scoring config load failed (%s)", scoring_error)
    try:
        payload = _dump_configs(core, scoring)
    except ValueError as exc:
        print(json.dumps({"error": f"serialize failed: {exc}"}))
        return 1
    payload["scoring_error"] = scoring_error
    payload["basis"] = SHIPPED_DEFAULTS_BASIS
    print(json.dumps(payload))
    return 0


def run_child_and_load(env: dict[str, str], timeout: float = CHILD_TIMEOUT_SEC) -> dict[str, Any]:
    """Run the loader child and return its payload; raises EVAL_FAILED."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "dashboard.arm_params"],
            env=env, cwd=str(REPO_ROOT), capture_output=True, text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ArmParamsError(EVAL_FAILED, f"arm evaluation did not finish: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stdout or "").strip()[-500:] or (proc.stderr or "").strip()[-500:]
        raise ArmParamsError(EVAL_FAILED, f"arm evaluation failed: {detail}")
    try:
        payload = json.loads(proc.stdout)
    except ValueError as exc:
        raise ArmParamsError(EVAL_FAILED, f"arm evaluation returned invalid JSON: {exc}") from exc
    if not isinstance(payload, dict) or "core" not in payload or isinstance(payload.get("error"), str):
        raise ArmParamsError(EVAL_FAILED, f"arm evaluation error: {payload.get('error', 'bad payload')}")
    return payload


def rebuild_config(payload: dict[str, Any], cls: Any) -> Any:
    """Rebuild a MakerConfig from child JSON using declared fields only."""
    fields = {f.name: f for f in dataclasses.fields(cls)}
    kwargs: dict[str, Any] = {}
    for key, value in payload.items():
        field = fields.get(key)
        if field is None:
            continue
        default = field.default
        if isinstance(value, list) and isinstance(default, tuple):
            value = tuple(value)
        kwargs[key] = value
    return cls(**kwargs)


_lock = threading.Lock()
_memo: dict[tuple[str, float, str], dict[str, Any]] = {}


def clear_memo() -> None:
    with _lock:
        _memo.clear()


def evaluate_arm(plan_path: Path | str, arm_name: str,
                 timeout: float = CHILD_TIMEOUT_SEC) -> ArmEvaluation:
    """Evaluate one arm's effective configs, memoizing the child payload."""
    plan_path = Path(plan_path)
    arm = resolve_arm(plan_path, arm_name)
    try:
        mtime = plan_path.stat().st_mtime
    except OSError as exc:
        raise ArmParamsError(MALFORMED_PLAN, f"{plan_path}: {exc}") from exc
    key = (str(plan_path), mtime, arm["name"])
    with _lock:
        payload = _memo.get(key)
    if payload is None:
        payload = run_child_and_load(build_child_env(arm), timeout=timeout)
        with _lock:
            _memo[key] = payload
    from core_brain.config import MakerConfig as CoreMakerConfig
    from scoring.config import MakerConfig as ScoringMakerConfig

    core = rebuild_config(payload["core"], CoreMakerConfig)
    scoring = None
    if isinstance(payload.get("scoring"), dict):
        scoring = rebuild_config(payload["scoring"], ScoringMakerConfig)
    scoring_error = payload.get("scoring_error")
    return ArmEvaluation(core=core, scoring=scoring, scoring_error=scoring_error)


if __name__ == "__main__":
    raise SystemExit(child_main())
