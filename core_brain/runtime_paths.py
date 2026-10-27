"""Where a runtime state file actually lives, during and after the run/ rename.

The state directory was renamed `run/` -> `runtime/`, three files inside it were
renamed with it, and the process keys inside the registry were renamed too. None
of that state is in git: it is on the operator's disk, written by processes that
may still be running when the new code starts.

The hazard is not cosmetic. `dashboard.server.start_bot` refuses to start a
second stack only when `get_system_status()` reports RUNNING, and that status is
read from the process registry. A registry the new code cannot find reads as
STOPPED, so START launches a second `core_brain.trader_loop --live` next to the
one already resting real bids -- two traders on one orders.db.

So readers resolve through `resolve_runtime_file`, which prefers the new path and
falls back to the pre-rename one while only that one exists. Writers keep writing
the new path unconditionally: the fallback switches itself off the moment the new
file appears, and it never resurrects the old one.

Nothing here moves, copies or deletes a file. Moving live state out from under a
running process is how you lose an append-only log on Windows; the old copy is
read and otherwise left alone.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional

LIVE_ROOT = Path(__file__).resolve().parent.parent

#: Where every runtime state file is written from now on.
RUNTIME_DIR = LIVE_ROOT / "runtime"

#: The pre-rename directory. Read-only as far as this repo is concerned.
LEGACY_RUNTIME_DIR = LIVE_ROOT / "run"

#: current name -> pre-rename name, for the files that were renamed with the
#: directory. Anything absent from this map kept its name across the move.
LEGACY_FILE_NAMES = {
    "processes.json": "live_procs.json",
    "global_stop_loss_heartbeat.json": "guardrail_watch_heartbeat.json",
    "global_stop_loss_alerts.log": "guardrail_alerts.log",
}

#: current process key -> pre-rename key, for entries inside the registry.
LEGACY_SERVICE_KEYS = {
    "filter": "screener",
    "query": "engine",
    "decide": "fleet",
}

#: The live trader's recorded engine profile (#856). A new file: it kept no
#: pre-rename name, so it stays out of LEGACY_FILE_NAMES.
TRADER_PROFILE_FILE = "trader_profile.json"

#: The operator-staged profile for the next START (#856 IIIB). Written by
#: POST /api/profile/stage, consumed once by start_bot. New file: no legacy
#: entry.
PENDING_PROFILE_FILE = "pending_profile.json"


def _runtime_dir(
    root: Path | str | None,
    runtime_dir: Path | str | None,
) -> Path:
    if runtime_dir is not None:
        return Path(runtime_dir)
    if root is not None:
        return Path(root) / RUNTIME_DIR.name
    return RUNTIME_DIR


def _legacy_dir(
    root: Path | str | None,
    legacy_dir: Path | str | None,
) -> Path:
    if legacy_dir is not None:
        return Path(legacy_dir)
    if root is not None:
        return Path(root) / LEGACY_RUNTIME_DIR.name
    return LEGACY_RUNTIME_DIR


def legacy_runtime_file(
    name: str,
    *,
    root: Path | str | None = None,
    legacy_dir: Path | str | None = None,
) -> Path:
    """The pre-rename path for runtime state file `name`.

    Does not check whether it exists -- `resolve_runtime_file` does that.

    Callers that keep their own repo root (most modules here do, and the tests
    redirect it) pass `root=`; the two directory arguments are for tests that
    want to place them independently.
    """
    return _legacy_dir(root, legacy_dir) / LEGACY_FILE_NAMES.get(name, name)


def resolve_runtime_file(
    name: str,
    *,
    root: Path | str | None = None,
    runtime_dir: Path | str | None = None,
    legacy_dir: Path | str | None = None,
) -> Path:
    """The path a READER should open for runtime state file `name`.

    Returns the `runtime/` path when it exists, the pre-rename `run/` path when
    only that one does, and the `runtime/` path otherwise -- so a caller that
    ends up writing through this path always creates the new file.
    """
    current = _runtime_dir(root, runtime_dir) / name
    if current.exists():
        return current
    legacy = legacy_runtime_file(name, root=root, legacy_dir=legacy_dir)
    if legacy.exists():
        return legacy
    return current


def runtime_file(
    name: str,
    *,
    root: Path | str | None = None,
    runtime_dir: Path | str | None = None,
) -> Path:
    """The path a WRITER should use for runtime state file `name`.

    Always under `runtime/`. Never falls back: a write to the old path would
    keep the stale copy alive and the fallback armed forever.
    """
    return _runtime_dir(root, runtime_dir) / name


def service_entry(saved_procs: Optional[Mapping[str, Any]], key: str) -> dict:
    """The recorded process entry for `key`, accepting its pre-rename name.

    A registry written before the rename records `screener`/`engine`/`fleet`.
    Returns `{}` when neither name is present or the entry is not a mapping,
    so callers can `.get("pid")` on the result unconditionally.
    """
    if not isinstance(saved_procs, Mapping):
        return {}
    for candidate in (key, LEGACY_SERVICE_KEYS.get(key)):
        if candidate is None:
            continue
        entry = saved_procs.get(candidate)
        if isinstance(entry, Mapping):
            return dict(entry)
    return {}
