import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strategy.supervisor import CHILDREN, next_restart_delay  # noqa: E402


def test_watcher_is_a_supervised_child():
    """watch_universe.py must run under the supervisor like every other child.

    It was started detached on 2026-08-08 so the evening-slate log would
    accrue unattended; a detached process dies with the console and is not
    restarted. As a CHILDREN entry it inherits the supervisor's restart
    backoff and survives crashes and reboots with the rest of the stack.
    """
    assert "watch" in CHILDREN
    cmd = CHILDREN["watch"]
    assert cmd[-1] == "scripts.watch_universe"
    # It must not run with `--once`: one sample and exit would defeat the
    # supervisor. The script's own default interval (300s) is what we want.
    assert "--once" not in cmd


def test_supervisor_owns_exactly_the_expected_stack():
    """One supervisor, one of each process -- the single-writer invariant."""
    assert set(CHILDREN) == {"fleet", "dash", "scan", "rerank", "watch"}


def test_first_crash_restarts_promptly():
    assert next_restart_delay(1) == pytest.approx(5.0)


def test_repeat_crashes_back_off():
    assert next_restart_delay(3) > next_restart_delay(1)


def test_backoff_is_capped():
    assert next_restart_delay(99) == pytest.approx(60.0)


def test_a_recovered_child_starts_from_the_bottom_again():
    # The caller resets the counter to 0 once a child survives; the delay in
    # that state must not exceed the first-crash delay.
    assert next_restart_delay(0) <= next_restart_delay(1)
