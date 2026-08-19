"""A recycled PID must not read as a running bot.

Found on the Owner's machine on 2026-08-19. `live/run/live_procs.json` recorded
supervisor and engine at PID 13052 from a run that had already exited. Windows
handed 13052 to msedge, and because the liveness check only asked "does a
process with this PID exist", the dashboard reported the bot stack RUNNING
indefinitely. Consequences, in order of severity:

1. `stop_bot` runs `taskkill /F /T` on the recorded PIDs -- pressing Stop Bot
   would have force-killed the Owner's browser and its child processes.
2. Every `Fresh DB` reset was refused, permanently.
3. `start_bot` refuses while RUNNING, so the stack could never be started.
4. Two tests in test_live_dash.py failed on any machine holding a stale file.

The recorded `started_at` is the fix: a recycled PID belongs to a process
created at a different time.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from dash.live_dash import (
    PID_START_TOLERANCE_S,
    _is_pid_alive,
    _process_start_time,
)


def test_start_time_of_this_process_is_readable():
    """If this fails the guard degrades to the old PID-only behaviour."""
    ts = _process_start_time(os.getpid())
    assert ts is not None
    assert 0 < ts <= time.time() + 1


def test_live_pid_with_matching_start_time_is_alive():
    me = os.getpid()
    started = _process_start_time(me)
    assert _is_pid_alive(me, started) is True


def test_recycled_pid_is_not_alive():
    """Same PID, a start time that predates it: a different process."""
    me = os.getpid()
    stale = _process_start_time(me) - (PID_START_TOLERANCE_S + 3600)
    assert _is_pid_alive(me, stale) is False


def test_clock_slack_still_counts_as_alive():
    me = os.getpid()
    near = _process_start_time(me) - (PID_START_TOLERANCE_S / 2)
    assert _is_pid_alive(me, near) is True


def test_missing_start_time_falls_back_to_pid_check():
    """A false 'stopped' would let a second bot stack launch beside a live one,
    which AGENTS.md forbids. Unknown start time must not read as dead."""
    assert _is_pid_alive(os.getpid(), None) is True


def test_dead_pid_is_not_alive():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    # Popen still holds the handle, so the PID stays queryable after exit.
    # Only the exit time separates it from a live process.
    assert _is_pid_alive(proc.pid, time.time()) is False
    assert _is_pid_alive(proc.pid) is False


def test_zero_and_none_are_not_alive():
    assert _is_pid_alive(None) is False
    assert _is_pid_alive(0) is False
    assert _is_pid_alive(-1) is False


def test_system_status_ignores_a_stale_procs_file(tmp_path, monkeypatch):
    """The exact failure: a real, live PID recorded with an old started_at."""
    import json

    import dash.live_dash as dash_mod

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    stale = time.time() - 86400
    (run_dir / "live_procs.json").write_text(json.dumps({
        "supervisor": {"pid": os.getpid(), "started_at": stale},
        "screener": {"pid": os.getpid(), "started_at": stale},
        "engine": {"pid": os.getpid(), "started_at": stale},
    }), encoding="utf-8")

    monkeypatch.setattr(dash_mod, "LIVE_ROOT", tmp_path)
    status = dash_mod.get_system_status()
    assert status["supervisor"]["running"] is False
    assert status["bot_state"] == "STOPPED"


def test_system_status_reports_a_genuinely_running_process(tmp_path, monkeypatch):
    import json

    import dash.live_dash as dash_mod

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "live_procs.json").write_text(json.dumps({
        "supervisor": {"pid": os.getpid(),
                       "started_at": _process_start_time(os.getpid())},
    }), encoding="utf-8")

    monkeypatch.setattr(dash_mod, "LIVE_ROOT", tmp_path)
    status = dash_mod.get_system_status()
    assert status["supervisor"]["running"] is True
    assert status["bot_state"] == "RUNNING"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows handle types")
def test_open_process_handle_is_not_truncated():
    """ctypes defaults restype to c_int, which truncates a pointer-sized HANDLE
    on 64-bit Windows: a handle above 2**31 would be passed on as a different
    value and then closed as garbage. argtypes/restype must be declared."""
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    handle = k32.OpenProcess(0x1000, False, os.getpid())
    try:
        assert handle is not None
        assert int(handle) > 0        # never negative from truncation
    finally:
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        k32.CloseHandle(handle)

    # And the real helper reads a sane creation time through the same path.
    assert _process_start_time(os.getpid()) is not None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal semantics")
def test_permission_error_means_the_process_exists(monkeypatch):
    """EPERM from os.kill means the process is alive and owned by someone else.
    Reading it as 'stopped' would let a second bot stack start beside a live
    one, which AGENTS.md forbids."""
    import dash.live_dash as dash_mod

    def denied(pid, sig):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(dash_mod.os, "kill", denied)
    assert dash_mod._is_pid_alive(4321) is True
