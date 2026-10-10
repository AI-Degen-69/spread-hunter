

# ── Issue #457: honest STOP lifecycle ─────────────────────────────────────────
# A mock-verified stop: no real signals, no real sleeps, no real processes.

import json
import os
import subprocess


class _ScriptedClock:
    """Deterministic clock/sleep: tests advance it, never wait for it."""

    def __init__(self):
        self.now = 1000.0
        self.slept = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.slept += seconds
        self.now += seconds


class _DeadlineClimber:
    """Clock that marches forward in poll steps, forever.

    Used where the process never dies: the loop must terminate by exhausting
    its deadline, not by an iterator running out.
    """

    def __init__(self, ds):
        self.now = 1000.0
        self.step = ds._STOP_POLL_INTERVAL_S

    def monotonic(self):
        value = self.now
        self.now += self.step
        return value


def _stop_fixture(tmp_path, monkeypatch, registry_text):
    """Scratch runtime dir + a registry file; returns the procs_file Path."""
    import dashboard.server as ds

    (tmp_path / "runtime").mkdir(parents=True, exist_ok=True)
    (tmp_path / "run").mkdir(parents=True, exist_ok=True)
    procs_file = tmp_path / "runtime" / "processes.json"
    if registry_text is not None:
        procs_file.write_text(registry_text, encoding="utf-8")
    monkeypatch.setattr(ds, "LIVE_ROOT", tmp_path)
    return procs_file


def test_stop_graceful_group_exit_reports_stopped(tmp_path, monkeypatch):
    """POSIX group leader: SIGTERM to the group, no escalation, ok=True."""
    import dashboard.server as ds

    procs_file = _stop_fixture(
        tmp_path, monkeypatch,
        '{"decide": {"pid": 5001, "started_at": 1.0}, "starting_account_value": 100.0}')
    monkeypatch.setattr(ds.sys, "platform", "linux")
    monkeypatch.setattr(ds, "_is_pid_alive", lambda pid, started_at=None: True)

    calls = []

    def killpg(pgid, sig):
        calls.append(("killpg", pgid, sig))
        if sig == 0:
            raise ProcessLookupError  # group already gone on the first poll

    monkeypatch.setattr(ds.os, "killpg", killpg, raising=False)
    monkeypatch.setattr(ds.os, "getpgid", lambda pid: 5001, raising=False)
    monkeypatch.setattr(ds.os, "kill", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(ds.os, "waitpid", lambda *a, **k: (0, 0), raising=False)

    result = ds.stop_bot()

    assert result["ok"] is True
    assert result["services"]["decide"]["outcome"] == "stopped"
    # Polite term went to the GROUP, not the bare process.
    assert ("killpg", 5001, 15) in calls
    assert not any(c[2] == 9 for c in calls)  # no SIGKILL
    # Registry rewritten, not deleted: capital kept, no service entry.
    assert procs_file.exists()
    kept = json.loads(procs_file.read_text(encoding="utf-8"))
    assert kept["starting_account_value"] == 100.0
    assert "decide" not in kept


def test_stop_escalates_to_sigkill_and_reaps(tmp_path, monkeypatch):
    """A survivor of the polite wait gets SIGKILL to the group + a reap."""
    import dashboard.server as ds

    _stop_fixture(
        tmp_path, monkeypatch,
        '{"decide": {"pid": 5001, "started_at": 1.0}, "starting_account_value": 100.0}')
    monkeypatch.setattr(ds.sys, "platform", "linux")
    monkeypatch.setattr(ds, "_is_pid_alive", lambda pid, started_at=None: True)

    clock = _ScriptedClock()
    monkeypatch.setattr(ds.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(ds.time, "sleep", clock.sleep)

    calls = []
    reaped = []

    def killpg(pgid, sig):
        calls.append((pgid, sig))
        if sig == 0:
            # Alive until the polite deadline passes, then the group is gone.
            if clock.now > 1000.0 + ds._STOP_POLITE_WAIT_S:
                raise ProcessLookupError
            # Advance the clock so the loop reaches the deadline.
            clock.now += ds._STOP_POLL_INTERVAL_S

    def waitpid(pid, opts):
        reaped.append((pid, opts))
        raise ChildProcessError  # not our direct child: the expected outcome

    monkeypatch.setattr(ds.os, "killpg", killpg, raising=False)
    monkeypatch.setattr(ds.os, "getpgid", lambda pid: 5001, raising=False)
    monkeypatch.setattr(ds.os, "kill", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(ds.os, "waitpid", waitpid, raising=False)

    result = ds.stop_bot()

    assert result["ok"] is True
    assert result["services"]["decide"]["outcome"] == "forced"
    assert (5001, 9) in calls  # SIGKILL to the group
    # Reap is a POSIX-only step (os.WNOHANG); on a Windows host it is a no-op,
    # so only assert the reap happened where the platform has waitpid.
    if hasattr(os, "WNOHANG"):
        assert reaped  # ChildProcessError is tolerated by the helper


def test_stop_reports_still_running_and_keeps_the_registry(tmp_path, monkeypatch):
    """A process that survives the force-kill: honest failure, registry kept."""
    import dashboard.server as ds

    procs_file = _stop_fixture(
        tmp_path, monkeypatch,
        '{"decide": {"pid": 5001, "started_at": 1.0}, "starting_account_value": 100.0}')
    monkeypatch.setattr(ds.sys, "platform", "linux")
    monkeypatch.setattr(ds, "_is_pid_alive", lambda pid, started_at=None: True)

    monkeypatch.setattr(ds.time, "sleep", lambda s: None)
    # The group never empties; the clock advances past each deadline so the
    # force stage exits promptly instead of looping forever.
    monkeypatch.setattr(ds.time, "monotonic", _DeadlineClimber(ds).monotonic)
    monkeypatch.setattr(ds.os, "killpg", lambda pgid, sig: None, raising=False)
    monkeypatch.setattr(ds.os, "getpgid", lambda pid: 5001, raising=False)
    monkeypatch.setattr(ds.os, "kill", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(ds.os, "waitpid", lambda *a, **k: (0, 0), raising=False)

    result = ds.stop_bot()

    assert result["ok"] is False
    assert result["services"]["decide"]["outcome"] == "still_running"
    assert "decide" in result["message"] and "5001" in result["message"]
    # The registry is KEPT so a retry can reach the survivor.
    assert procs_file.exists()
    kept = json.loads(procs_file.read_text(encoding="utf-8"))
    assert kept["decide"]["pid"] == 5001
    assert kept["starting_account_value"] == 100.0


def test_stop_signal_error_lands_in_detail_without_raising(tmp_path, monkeypatch):
    """A PermissionError on SIGTERM is reported in detail, never raised."""
    import dashboard.server as ds

    _stop_fixture(tmp_path, monkeypatch,
                  '{"decide": {"pid": 5001, "started_at": 1.0}}')
    monkeypatch.setattr(ds.sys, "platform", "linux")
    monkeypatch.setattr(ds, "_is_pid_alive", lambda pid, started_at=None: True)
    monkeypatch.setattr(ds.time, "sleep", lambda s: None)

    def killpg(pgid, sig):
        if sig == 15:
            raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(ds.os, "killpg", killpg, raising=False)
    monkeypatch.setattr(ds.os, "getpgid", lambda pid: 5001, raising=False)
    monkeypatch.setattr(ds.os, "kill", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(ds.os, "waitpid", lambda *a, **k: (0, 0), raising=False)

    result = ds.stop_bot()  # must not raise

    assert "not permitted" in result["services"]["decide"]["detail"].lower()


def test_stop_legacy_fleet_entry_reports_under_decide(tmp_path, monkeypatch):
    """Pre-rename screener/engine/fleet keys stop under current names."""
    import dashboard.server as ds

    _stop_fixture(tmp_path, monkeypatch, '{"fleet": {"pid": 5002}}')
    monkeypatch.setattr(ds.sys, "platform", "linux")
    monkeypatch.setattr(ds, "_is_pid_alive", lambda pid, started_at=None: True)
    monkeypatch.setattr(ds.time, "sleep", lambda s: None)

    calls = []

    def kill(pid, sig):
        calls.append(("kill", pid, sig))
        if sig == 0:
            raise ProcessLookupError

    monkeypatch.setattr(ds.os, "kill", kill, raising=False)
    # Not a group leader (pgid 1): single-process signal path, and the
    # down-check's killpg(1, 0) reports the group empty once the pid is gone.
    monkeypatch.setattr(ds.os, "getpgid", lambda pid: 1, raising=False)
    monkeypatch.setattr(
        ds.os, "killpg",
        lambda pgid, sig: (_ for _ in ()).throw(ProcessLookupError), raising=False)
    monkeypatch.setattr(ds.os, "waitpid", lambda *a, **k: (0, 0), raising=False)

    result = ds.stop_bot()

    assert result["ok"] is True
    assert result["services"]["decide"]["outcome"] in ("stopped", "forced")
    assert ("kill", 5002, 15) in calls


def test_stop_windows_polite_then_forced_taskkill(tmp_path, monkeypatch):
    """Windows: taskkill /T then /F /T, return code captured in detail."""
    import dashboard.server as ds

    _stop_fixture(tmp_path, monkeypatch,
                  '{"decide": {"pid": 5001, "started_at": 1.0}}')
    monkeypatch.setattr(ds.sys, "platform", "win32")
    monkeypatch.setattr(ds.time, "sleep", lambda s: None)
    monkeypatch.setattr(ds.time, "monotonic", _DeadlineClimber(ds).monotonic)

    alive = {"v": True}

    def run_taskkill(argv, **kwargs):
        if "/F" in argv:
            alive["v"] = False
            return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.CompletedProcess(argv, 128, "", "")  # polite fails

    monkeypatch.setattr(subprocess, "run", run_taskkill)
    monkeypatch.setattr(
        ds, "_is_pid_alive", lambda pid, started_at=None: alive["v"])

    result = ds.stop_bot()

    assert result["ok"] is True
    assert result["services"]["decide"]["outcome"] == "forced"
    assert "128" in result["services"]["decide"]["detail"]


def test_stop_windows_timeout_lands_in_detail_without_raising(tmp_path, monkeypatch):
    """A taskkill timeout is captured in detail, never raised."""
    import dashboard.server as ds

    _stop_fixture(tmp_path, monkeypatch,
                  '{"decide": {"pid": 5001, "started_at": 1.0}}')
    monkeypatch.setattr(ds.sys, "platform", "win32")
    monkeypatch.setattr(ds, "_is_pid_alive", lambda pid, started_at=None: True)
    monkeypatch.setattr(ds.time, "sleep", lambda s: None)

    def run_raise(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))

    monkeypatch.setattr(subprocess, "run", run_raise)

    result = ds.stop_bot()  # must not raise

    assert "timed out" in result["services"]["decide"]["detail"].lower()


def test_stop_nothing_running_is_success_and_keeps_capital(tmp_path, monkeypatch):
    """No live PIDs: ok=True, 'already stopped', capital kept."""
    import dashboard.server as ds

    procs_file = _stop_fixture(
        tmp_path, monkeypatch, '{"starting_account_value": 100.0}')
    monkeypatch.setattr(ds, "_is_pid_alive", lambda pid, started_at=None: False)

    result = ds.stop_bot()

    assert result["ok"] is True
    assert result["services"] == {}  # no live pid, nothing signalled
    assert procs_file.exists()
    assert json.loads(procs_file.read_text(encoding="utf-8"))["starting_account_value"] == 100.0


def test_stop_missing_registry_is_success(tmp_path, monkeypatch):
    """No registry file at all: ok=True, nothing crashed."""
    import dashboard.server as ds

    _stop_fixture(tmp_path, monkeypatch, None)

    result = ds.stop_bot()

    assert result["ok"] is True
    assert "already stopped" in result["message"].lower()


def test_stop_budget_stays_below_the_ops_lock_window():
    """The whole stop must finish before the 30s ops-lock takeover window."""
    import dashboard.server as ds

    total = (ds._STOP_POLITE_WAIT_S + ds._STOP_FORCE_WAIT_S
             + 2 * ds._STOP_TASKKILL_TIMEOUT_S)
    assert total < 30.0


def test_reset_aborts_when_stop_cannot_confirm_down(tmp_path, monkeypatch):
    """RESET must not cancel orders under a stack that is still up."""
    from fastapi.testclient import TestClient

    import core_brain.order_manager as om
    import dashboard.server as ds

    _stop_fixture(tmp_path, monkeypatch,
                  '{"decide": {"pid": 5001, "started_at": 1.0}, "starting_account_value": 100.0}')
    monkeypatch.setattr(ds, "_is_pid_alive", lambda pid, started_at=None: True)
    monkeypatch.setattr(ds.sys, "platform", "linux")
    monkeypatch.setattr(ds.os, "killpg", lambda pgid, sig: None, raising=False)
    monkeypatch.setattr(ds.os, "getpgid", lambda pid: 5001, raising=False)
    monkeypatch.setattr(ds.os, "kill", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(ds.os, "waitpid", lambda *a, **k: (0, 0), raising=False)
    monkeypatch.setattr(ds.time, "sleep", lambda s: None)
    monkeypatch.setattr(ds.time, "monotonic", _DeadlineClimber(ds).monotonic)

    def cancel_fails(*a, **k):
        raise AssertionError("RESET must not cancel under a live stack")

    monkeypatch.setattr(om, "cancel_all", cancel_fails)

    client = TestClient(ds.app)
    res = client.post("/api/system/reset", headers={"X-Control-Token": ds.CONTROL_TOKEN})
    assert res.status_code == 409
