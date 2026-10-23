"""The suite must not write into the operator's data/, runtime/ or reports/.

`tests/conftest.py::block_real_state_writes` is autouse and fails a test that
creates, changes or removes a file under those three directories. This module
proves the guard can see a write, so it is not a guard that silently stopped
guarding -- the same reason `test_docs_layout.py` keeps a sentinel.

The snapshot probes use the documented marker opt-out and restore real state
in finally blocks. The rejection probe is unmarked and uses a temporary repo
root to exercise the guard without touching the operator's state.
"""
from __future__ import annotations

import pathlib

import pytest

import tests.conftest as root_conftest
from tests.conftest import REPO_ROOT, _real_state_snapshot

PROBE = "runtime/real-state-guard-probe.tmp"


def test_an_unmarked_write_is_rejected(tmp_path, monkeypatch, request):
    with monkeypatch.context() as patch:
        patch.setattr(root_conftest, "REPO_ROOT", tmp_path)
        guard = root_conftest.block_real_state_writes.__wrapped__(request)
        next(guard)
        probe = tmp_path / PROBE
        probe.parent.mkdir(parents=True)
        probe.write_text("probe", encoding="utf-8")

        with pytest.raises(AssertionError, match=r"\+ runtime/real-state-guard-probe\.tmp"):
            next(guard)


@pytest.mark.writes_real_state
def test_a_write_under_runtime_is_visible_to_the_guard():
    before = _real_state_snapshot()
    probe = REPO_ROOT / PROBE
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text("probe", encoding="utf-8")
    try:
        after = _real_state_snapshot()
        assert PROBE in set(after) - set(before)
    finally:
        probe.unlink()


@pytest.mark.writes_real_state
def test_a_change_to_the_tracked_demo_fixture_is_visible_to_the_guard():
    """The failure that motivated the guard: `M data/demo_orders.db` after a run."""
    fixture = REPO_ROOT / "data" / "demo_orders.db"
    if not fixture.is_file():
        pytest.skip("tracked demo fixture is absent")
    original = fixture.read_bytes()
    before = _real_state_snapshot()
    try:
        fixture.write_bytes(original + b"\x00")
        after = _real_state_snapshot()
        assert after["data/demo_orders.db"] != before["data/demo_orders.db"]
    finally:
        fixture.write_bytes(original)


def test_the_snapshot_reads_the_real_state_dirs():
    state = _real_state_snapshot()
    assert isinstance(state, dict)
    # The demo fixture is tracked, so it is always there to be seen.
    if pathlib.Path(REPO_ROOT / "data" / "demo_orders.db").is_file():
        assert "data/demo_orders.db" in state
