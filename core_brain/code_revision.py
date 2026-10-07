"""Which code a running process is holding, and whether the tree has moved on.

A rehearsal outlives the code it started with, and nothing used to say so. On
2026-10-07 three rehearsals ran on one machine: `shadow-05` held that morning's
work, while `shadow-01-prudent` (started the night before) and
`shadow-04-prudent` were still deciding with the old ranker, without the
hold-through rule, and with no queue-clear gate at all. All three looked
identical on the dashboard and in their logs. The only way to tell them apart
was to line process start times up against `git log` by hand -- which is exactly
the kind of arithmetic an operator reading numbers at 11pm should not be doing.

So a run records the code it loaded, and this module answers two questions:

1. **What is this process holding?** `read_code_revision`, called once at
   process start: the short commit, whether the working tree was dirty (this
   checkout runs rehearsals from a working tree, so a bare sha would name code
   that did not run), and the mtime of the newest file under `REVISION_DIRS`.
2. **Has that code changed since?** `code_predates_tree`, asked by a reader
   that is looking at the run's heartbeat now.

The clock is the verdict, not the commit sha. A rehearsal started from a dirty
tree whose edits are then committed unchanged is holding exactly the code on
disk, so it must not be called stale -- and its recorded clock says so, because
committing does not touch file mtimes. Conversely a `git checkout`, a pull, or a
one-line edit moves the clock past the run's recorded one, and the run is
running older code whether or not anyone committed anything.

The verdict is deliberately evidence-only. A heartbeat that names a revision
answers for itself; one written before this field existed is bounded by when its
process started (a process cannot hold code newer than the process); one with
neither is never called stale. Silence about a run the machine cannot judge is
the same as today's behaviour, and better than a warning nobody can act on.
"""
from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path
from typing import Any, Mapping, Optional

log = logging.getLogger("code_revision")

#: The directories whose contents decide what a rehearsal quotes. `dashboard/`
#: is outside on purpose: the loop does not import it, so a dashboard edit does
#: not make a running rehearsal's numbers describe old code. `scripts/` is
#: outside for the same reason -- the loop imports `core_brain` and `scoring`,
#: and the stack's other processes are their own runs with their own heartbeats.
REVISION_DIRS = ("core_brain", "scoring")

#: A git that cannot answer (not installed, not a checkout, a hung subprocess)
#: must not hang or crash a rehearsal's startup. Git's own answer arrives in
#: milliseconds on a checkout this size.
GIT_TIMEOUT_SEC = 5.0

#: Files written while a checkout lands share a second with a process started
#: immediately after it. A clock inside this window of the recorded one is
#: treated as the same revision rather than as a stale run.
STALE_GRACE_SEC = 1.0


def repo_root() -> Path:
    """The checkout this package lives in.

    Resolved from `__file__` rather than the working directory: the menu, the
    dashboard and a hand-started rehearsal all import this module from the same
    place and must agree about which tree they are describing.
    """
    return Path(__file__).resolve().parent.parent


def decision_code_mtime(root: Path | str | None = None) -> Optional[float]:
    """When the newest decision-affecting file was last written, or None.

    None is "no clock here" (an empty or unusual tree), never zero: a zero would
    read as "everything is newer than this process" and invent staleness.
    """
    base_root = Path(root) if root is not None else repo_root()
    newest: Optional[float] = None
    for name in REVISION_DIRS:
        base = base_root / name
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if newest is None or mtime > newest:
                newest = mtime
    return newest


def _git_output(args: list[str], root: Path) -> Optional[str]:
    """`git <args>` in `root`, or None when git cannot answer at all."""
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(root), capture_output=True, text=True,
            timeout=GIT_TIMEOUT_SEC, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        log.debug("git %s failed in %s: %s", " ".join(args), root, e)
        return None
    if proc.returncode != 0:
        log.debug("git %s exited %d in %s", " ".join(args), proc.returncode, root)
        return None
    return proc.stdout


def revision_label(revision: Mapping[str, Any] | None) -> str:
    """The revision as one word for a log line or a badge: `80d03f1+dirty`.

    `unknown` is a real answer: a checkout with no git, or a heartbeat written
    before this was recorded. It is not a claim that the code is current.
    """
    if not isinstance(revision, Mapping):
        return "unknown"
    commit = revision.get("commit")
    if not commit:
        return "unknown"
    return f"{commit}+dirty" if revision.get("dirty") else str(commit)


def read_code_revision(root: Path | str | None = None,
                       now: float | None = None) -> dict[str, Any]:
    """The code THIS process is holding, captured once at start.

    Read it once and carry it: re-reading per heartbeat would let a mid-run edit
    rewrite what the run claims to have loaded, which is the silence this exists
    to fix.
    """
    base_root = Path(root) if root is not None else repo_root()
    commit_raw = _git_output(["rev-parse", "--short", "HEAD"], base_root)
    commit = commit_raw.strip() if commit_raw and commit_raw.strip() else None
    status = _git_output(["status", "--porcelain"], base_root)
    # None means "could not tell", which must never read as "clean".
    dirty: Optional[bool] = None if status is None else bool(status.strip())
    revision: dict[str, Any] = {
        "commit": commit,
        "dirty": dirty,
        "label": revision_label({"commit": commit, "dirty": dirty}),
        "code_mtime": decision_code_mtime(root=base_root),
        "captured_at": float(time.time() if now is None else now),
    }
    return revision


def _as_float(value: Any) -> Optional[float]:
    """A float, or None for anything that is not a usable number."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _code_as_of(heartbeat: Mapping[str, Any] | None) -> Optional[float]:
    """The oldest moment this run's code can possibly be newer than.

    Its own recorded clock first (that is the file mtimes it read), then the
    moment its process started, then the moment the run started. A heartbeat
    written before `code_revision` existed has only the last two, and they are
    enough: the code a process loaded cannot postdate the process.
    """
    if not isinstance(heartbeat, Mapping):
        return None
    revision = heartbeat.get("code_revision")
    if isinstance(revision, Mapping):
        for key in ("code_mtime", "captured_at"):
            stamp = _as_float(revision.get(key))
            if stamp is not None:
                return stamp
    for key in ("process_started_at", "started_at"):
        stamp = _as_float(heartbeat.get(key))
        if stamp is not None:
            return stamp
    return None


def code_predates_tree(heartbeat: Mapping[str, Any] | None,
                       root: Path | str | None = None,
                       *, clock: float | None = None) -> bool:
    """Whether `core_brain/` or `scoring/` changed after this run started.

    True is the actionable verdict: the numbers on the page describe code that
    is no longer the code on disk, so two runs compared against each other may
    be describing two different strategies. False covers both "this run holds
    what is on disk" and "there is not enough evidence to say" -- the caller
    that wants the difference reads `revision_label` for it.

    `clock` is for a reader that has already read the tree once and is asking
    about several runs: the walk is the only expensive part here, so the
    dashboard reads it a single time per request instead of once per heartbeat
    file it parses.
    """
    tree_clock = decision_code_mtime(root=root) if clock is None else clock
    if tree_clock is None:
        return False
    as_of = _code_as_of(heartbeat)
    if as_of is None:
        return False
    return tree_clock > as_of + STALE_GRACE_SEC
