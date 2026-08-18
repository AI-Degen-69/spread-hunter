"""Report when a simulation module that live/ forked has moved at the root.

`live/engine/config.py` and `live/engine/markets.py` are copies, not imports.
That is deliberate: live/ must be able to tune its own parameters without
perturbing a simulation sample, and AGENTS.md says changing strategy parameters
invalidates the current run.

So divergence is allowed. Divergence nobody noticed is the hazard -- a bug fixed
in `strategy/markets.py` that never reaches the copy the real-money path uses
will not announce itself, and a live order is the wrong place to discover it.

This test fails only when the ROOT copy has changed since the fork was recorded.
It never compares the two files to each other; the live copy is free to differ.
Read the printed diff, decide whether the live copy wants the same change, apply
it or not, then re-record:

    python live/scripts/refork.py
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

LIVE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = LIVE_ROOT.parent
MANIFEST = LIVE_ROOT / "FORKED_FROM.json"


def _normalised_sha256(path: Path) -> str:
    """Hash with CRLF folded to LF.

    This repo is worked on Windows with git's autocrlf in play. Hashing raw
    bytes makes the checkout's line endings part of the answer, which fails the
    test for a reason that has nothing to do with the code.
    """
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _forks() -> dict[str, dict]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["forks"]


def test_manifest_lists_every_forked_module():
    """Guard the manifest: a fork added to live/engine/ but not recorded is unmonitored.

    Everything in live/engine/ that also exists at strategy/<name> is a fork by
    construction -- the live-only modules (live_exec, live_pairs,
    order_registry) have no root twin.
    """
    recorded = set(_forks())
    on_disk = {
        f"live/engine/{p.name}"
        for p in (LIVE_ROOT / "engine").glob("*.py")
        if p.name != "__init__.py" and (REPO_ROOT / "strategy" / p.name).is_file()
    }
    assert on_disk == recorded, (
        f"unrecorded forks: {sorted(on_disk - recorded)}; "
        f"stale manifest entries: {sorted(recorded - on_disk)}"
    )


@pytest.mark.parametrize("live_rel", sorted(_forks()))
def test_root_source_has_not_moved_since_the_fork(live_rel):
    entry = _forks()[live_rel]
    source = REPO_ROOT / entry["forked_from"]
    assert source.is_file(), f"{entry['forked_from']} is gone; update {MANIFEST.name}"

    actual = _normalised_sha256(source)
    if actual == entry["source_sha256"]:
        return

    # Diff the recorded commit against the WORKING TREE, not against HEAD.
    # The change that trips this test is usually still uncommitted -- someone
    # edited strategy/markets.py a minute ago -- and `<commit>..HEAD` renders
    # empty for exactly that case, which is the case the reader most needs to see.
    diff = subprocess.run(
        ["git", "diff", entry["at_commit"], "--", entry["forked_from"]],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
    ).stdout or "(diff unavailable -- recorded commit not in this history)"

    pytest.fail(
        f"{entry['forked_from']} changed since {live_rel} was forked from it at "
        f"{entry['at_commit'][:8]}.\n"
        f"Decide whether {live_rel} wants the same change, then re-record with "
        f"`python live/scripts/refork.py`.\n\n{diff[:4000]}"
    )
