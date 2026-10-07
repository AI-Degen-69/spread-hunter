"""Which code a rehearsal is holding, so an old one cannot pass for current.

The failure this covers is silence, not a crash. Three rehearsals ran on one
machine on 2026-10-07; two had started before that morning's commits and were
still deciding with the old ranker, the old hold-through rule and no
queue-clear gate at all, while the third held everything. Nothing in the
heartbeat, the logs or the dashboard said so -- the only way to know was to
compare process start times against `git log` by hand.

So a rehearsal records the code it loaded (commit, dirty marker, and the mtime
of the newest decision file it read), and the reader asks whether that code is
still the code on disk. The verdict is deliberately evidence-only: a heartbeat
that names a revision answers for itself, an older heartbeat is bounded by when
its process started, and a heartbeat with neither is never called stale.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from core_brain import code_revision

#: A fixed clock so every test states its own ordering explicitly.
RUN_START = 1_788_000_000.0


def _tree(tmp_path: Path, files: dict[str, float]) -> Path:
    """A checkout-shaped directory: `files` maps a relative path to its mtime."""
    for rel, mtime in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# code\n", encoding="utf-8")
        os.utime(path, (mtime, mtime))
    return tmp_path


def _git(monkeypatch, *, commit: str | None = "80d03f1", porcelain: str = "") -> list[tuple]:
    """Wire `_git_output` to a canned repository, and record what was asked.

    `commit=None` is a dead git: not installed, not a checkout, a hung
    subprocess. Every call answers None, which is what the reader has to
    degrade on -- `porcelain` is only meaningful for a git that responds.
    """
    calls: list[tuple] = []

    def fake(args, root):
        calls.append(tuple(args))
        if commit is None:
            return None
        if args[0] == "rev-parse":
            return commit + "\n"
        if args[0] == "status":
            return porcelain
        return None

    monkeypatch.setattr(code_revision, "_git_output", fake)
    return calls


# ── The record ──────────────────────────────────────────────────────────────


def test_the_label_is_the_commit_and_a_dirty_tree_says_so(tmp_path, monkeypatch):
    _git(monkeypatch, commit="80d03f1", porcelain=" M core_brain/shadow_run.py\n")
    tree = _tree(tmp_path, {"core_brain/shadow_run.py": RUN_START - 60.0})

    rev = code_revision.read_code_revision(root=tree)

    assert rev["commit"] == "80d03f1"
    assert rev["dirty"] is True
    # The marker is load-bearing: this checkout runs rehearsals from a working
    # tree, so a bare commit sha would name code that is not what ran.
    assert rev["label"] == "80d03f1+dirty"
    assert rev["code_mtime"] == pytest.approx(RUN_START - 60.0)


def test_a_clean_tree_carries_no_dirty_marker(tmp_path, monkeypatch):
    _git(monkeypatch, commit="80d03f1", porcelain="")

    rev = code_revision.read_code_revision(
        root=_tree(tmp_path, {"core_brain/risk.py": RUN_START - 60.0}))

    assert rev["dirty"] is False
    assert rev["label"] == "80d03f1"


def test_a_git_that_cannot_answer_is_unknown_rather_than_clean(tmp_path, monkeypatch):
    # No git on PATH, not a checkout, a hung subprocess: the read degrades, and
    # it must never claim the tree is clean on no evidence.
    _git(monkeypatch, commit=None)

    rev = code_revision.read_code_revision(
        root=_tree(tmp_path, {"core_brain/risk.py": RUN_START - 60.0}))

    assert rev["commit"] is None
    assert rev["dirty"] is None
    assert rev["label"] == "unknown"
    # The clock is still recorded: it is what the staleness verdict runs on.
    assert rev["code_mtime"] == pytest.approx(RUN_START - 60.0)


def test_the_code_clock_is_the_newest_decision_file(tmp_path):
    # `dashboard/` is outside the walk on purpose -- the loop does not import
    # it -- and a pycache or a stray non-Python file is not code.
    tree = _tree(tmp_path, {
        "core_brain/risk.py": RUN_START - 300.0,
        "scoring/selector.py": RUN_START - 120.0,
        "dashboard/server.py": RUN_START + 60.0,
        "core_brain/__pycache__/risk.cpython-312.pyc": RUN_START + 90.0,
        "core_brain/notes.txt": RUN_START + 120.0,
    })

    clock = code_revision.decision_code_mtime(root=tree)

    assert clock == pytest.approx(RUN_START - 120.0)
    assert {d for d in code_revision.REVISION_DIRS} == {"core_brain", "scoring"}


def test_a_tree_with_no_decision_files_has_no_clock(tmp_path):
    # Unknown, not zero: a zero clock would read as "everything is newer".
    assert code_revision.decision_code_mtime(root=tmp_path) is None


def test_revision_label_of_nothing_is_unknown():
    assert code_revision.revision_label(None) == "unknown"
    assert code_revision.revision_label({}) == "unknown"
    # A record from a future/partial writer without a label still names its commit.
    assert code_revision.revision_label({"commit": "abc1234"}) == "abc1234"


# ── The verdict ─────────────────────────────────────────────────────────────


def _heartbeat(**extra) -> dict:
    payload = {"started_at": RUN_START, "heartbeat_ts": RUN_START + 30.0,
               "run_id": "shadow-01"}
    payload.update(extra)
    return payload


def test_a_tree_edit_after_the_run_started_makes_it_predate_the_tree(tmp_path):
    tree = _tree(tmp_path, {"core_brain/risk.py": RUN_START + 600.0})
    hb = _heartbeat(code_revision={
        "label": "80d03f1", "commit": "80d03f1", "dirty": False,
        "code_mtime": RUN_START - 60.0, "captured_at": RUN_START})

    assert code_revision.code_predates_tree(hb, root=tree) is True


def test_a_run_holding_the_tree_on_disk_does_not_predate_it(tmp_path):
    tree = _tree(tmp_path, {"core_brain/risk.py": RUN_START - 60.0})
    hb = _heartbeat(code_revision={
        "label": "80d03f1", "commit": "80d03f1", "dirty": False,
        "code_mtime": RUN_START - 60.0, "captured_at": RUN_START})

    assert code_revision.code_predates_tree(hb, root=tree) is False


def test_committing_the_edits_a_run_started_with_does_not_stale_it(tmp_path, monkeypatch):
    # The scenario that would make a naive dirty-flag comparison lie: a
    # rehearsal starts on a dirty tree, the edits are committed unchanged, and
    # the tree is now clean at a NEW sha with the SAME file mtimes. The run is
    # still holding exactly this code, so it must not be called stale.
    _git(monkeypatch, commit="deadbee", porcelain="")
    tree = _tree(tmp_path, {"core_brain/risk.py": RUN_START - 60.0})
    hb = _heartbeat(code_revision={
        "label": "80d03f1+dirty", "commit": "80d03f1", "dirty": True,
        "code_mtime": RUN_START - 60.0, "captured_at": RUN_START})

    assert code_revision.code_predates_tree(hb, root=tree) is False


def test_a_legacy_heartbeat_falls_back_to_when_its_process_started(tmp_path):
    # A heartbeat written before the field existed still answers the question:
    # a process cannot hold code newer than the process. This is what makes the
    # rehearsals already running legible on the dashboard.
    tree = _tree(tmp_path, {"core_brain/risk.py": RUN_START + 3600.0})
    hb = {"started_at": RUN_START, "process_started_at": RUN_START + 2.0,
          "heartbeat_ts": RUN_START + 30.0, "run_id": "shadow-04-prudent"}

    assert code_revision.code_predates_tree(hb, root=tree) is True


def test_a_legacy_heartbeat_started_after_the_tree_is_current(tmp_path):
    tree = _tree(tmp_path, {"core_brain/risk.py": RUN_START - 3600.0})
    hb = {"started_at": RUN_START, "process_started_at": RUN_START,
          "heartbeat_ts": RUN_START + 30.0, "run_id": "shadow-05"}

    assert code_revision.code_predates_tree(hb, root=tree) is False


def test_a_recorded_revision_without_a_clock_falls_back_to_its_capture(tmp_path):
    tree = _tree(tmp_path, {"core_brain/risk.py": RUN_START + 600.0})
    hb = _heartbeat(code_revision={"label": "80d03f1", "commit": "80d03f1",
                                   "dirty": False, "code_mtime": None,
                                   "captured_at": RUN_START - 5.0})

    assert code_revision.code_predates_tree(hb, root=tree) is True


def test_no_evidence_is_never_reported_stale(tmp_path):
    # An unknown is not a verdict. A heartbeat with no clock and no revision --
    # or no heartbeat at all -- must not put a warning on a page.
    tree = _tree(tmp_path, {"core_brain/risk.py": RUN_START + 600.0})

    assert code_revision.code_predates_tree(None, root=tree) is False
    assert code_revision.code_predates_tree({}, root=tree) is False
    assert code_revision.code_predates_tree({"run_id": "shadow-01"}, root=tree) is False


def test_a_touch_in_the_same_second_is_not_a_stale_verdict(tmp_path):
    # Files written as a checkout lands share a second with a process started
    # right after; the grace keeps a fresh run from being flagged on noise.
    tree = _tree(tmp_path, {"core_brain/risk.py": RUN_START + 0.5})
    hb = _heartbeat(code_revision={"label": "80d03f1", "commit": "80d03f1",
                                   "dirty": False, "code_mtime": RUN_START,
                                   "captured_at": RUN_START})

    assert code_revision.code_predates_tree(hb, root=tree) is False


def test_a_reader_can_hand_over_a_clock_it_already_read(tmp_path, monkeypatch):
    # The dashboard asks about several heartbeat files per request and walks the
    # tree once, not once per file.
    def explode(root=None):
        raise AssertionError("the passed clock must be used, not re-read")

    monkeypatch.setattr(code_revision, "decision_code_mtime", explode)
    hb = _heartbeat(code_revision={"label": "80d03f1", "commit": "80d03f1",
                                   "dirty": False, "code_mtime": RUN_START,
                                   "captured_at": RUN_START})

    assert code_revision.code_predates_tree(hb, clock=RUN_START + 600.0) is True
    assert code_revision.code_predates_tree(hb, clock=RUN_START) is False


def test_the_default_root_is_this_checkout():
    # The readers (dashboard, loop) resolve against the package they imported,
    # so the default must be a directory that really holds core_brain/.
    root = code_revision.repo_root()

    assert (root / "core_brain").is_dir()
    assert (root / "scoring").is_dir()
    assert code_revision.decision_code_mtime() is not None
