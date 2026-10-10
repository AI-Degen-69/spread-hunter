

# ── Issue #457: partial START ─────────────────────────────────────────────────
# START fills only the missing services; rollback stops only what it launched.

import json
import subprocess


class _RecordingPopen:
    """Fake Popen that records argv/kwargs and hands out scripted pids."""

    spawned: list = []

    def __init__(self, args, **kwargs):
        type(self).spawned.append((list(args), kwargs))
        self.pid = 6001 + len(type(self).spawned)
        if getattr(type(self), "fail_on", None) == len(type(self).spawned):
            raise OSError("boom: launch failed")

    def terminate(self):
        pass

    def wait(self, timeout=None):
        pass

    def kill(self):
        pass


def _start_fixture(tmp_path, monkeypatch, registry_text):
    """Scratch roots + registry + a fake Popen; returns (procs_file, spawns)."""
    import dashboard.server as ds

    (tmp_path / "runtime").mkdir(parents=True, exist_ok=True)
    procs_file = tmp_path / "runtime" / "processes.json"
    if registry_text is not None:
        procs_file.write_text(registry_text, encoding="utf-8")
    monkeypatch.setattr(ds, "LIVE_ROOT", tmp_path)
    monkeypatch.setattr(ds, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(ds, "resolve_sweep_interval", lambda: None)
    monkeypatch.setattr(ds, "_capture_starting_capital", lambda: 100.0)
    _RecordingPopen.spawned = []
    _RecordingPopen.fail_on = None
    monkeypatch.setattr(subprocess, "Popen", _RecordingPopen)
    return procs_file


def test_partial_start_fills_only_the_missing_services(tmp_path, monkeypatch):
    """A partial stack: no NameError, only the gaps are launched."""
    import dashboard.server as ds

    procs_file = _start_fixture(
        tmp_path, monkeypatch,
        '{"query": {"pid": 4242, "started_at": 1.0}, "starting_account_value": 100.0}')
    # query is alive; filter and decide are not.
    monkeypatch.setattr(ds, "_is_pid_alive",
                        lambda pid, started_at=None: pid == 4242)

    result = ds.start_bot()

    assert result["ok"] is True, result["message"]
    assert sorted(result["launched"]) == ["decide", "filter"]
    assert result["reused"] == ["query"]
    modules = [" ".join(argv) for argv, _ in _RecordingPopen.spawned]
    assert any("scripts.filter_loop" in m for m in modules)
    assert any("core_brain.trader_loop" in m for m in modules)
    assert not any("core_brain.order_manager" in m for m in modules)
    kept = json.loads(procs_file.read_text(encoding="utf-8"))
    assert kept["query"]["pid"] == 4242          # reused entry untouched
    assert kept["starting_account_value"] == 100.0  # capital preserved


def test_partial_start_uses_a_new_process_group(tmp_path, monkeypatch):
    """POSIX: each launched service owns a group, so STOP can kill the tree."""
    import dashboard.server as ds

    _start_fixture(tmp_path, monkeypatch, "{}")
    monkeypatch.setattr(ds, "_is_pid_alive", lambda pid, started_at=None: False)
    monkeypatch.setattr(ds.sys, "platform", "linux")

    ds.start_bot()

    assert _RecordingPopen.spawned
    for _, kwargs in _RecordingPopen.spawned:
        assert kwargs.get("start_new_session") is True


def test_full_stack_refuses_and_launches_nothing(tmp_path, monkeypatch):
    """All three alive: refuse, no process spawned."""
    import dashboard.server as ds

    _start_fixture(tmp_path, monkeypatch,
                   '{"filter": {"pid": 1}, "query": {"pid": 2}, "decide": {"pid": 3}}')
    monkeypatch.setattr(ds, "_is_pid_alive", lambda pid, started_at=None: True)

    result = ds.start_bot()

    assert result["ok"] is False
    assert "already running" in result["message"].lower()
    assert _RecordingPopen.spawned == []


def test_start_rollback_stops_only_what_it_launched(tmp_path, monkeypatch):
    """A failed third launch kills the first two, keeps the reused entry."""
    import dashboard.server as ds

    procs_file = _start_fixture(
        tmp_path, monkeypatch,
        '{"query": {"pid": 4242, "started_at": 1.0}, "starting_account_value": 100.0}')
    monkeypatch.setattr(ds, "_is_pid_alive",
                        lambda pid, started_at=None: pid == 4242)
    _RecordingPopen.fail_on = 2  # second launch (decide) fails

    stopped = []
    monkeypatch.setattr(
        ds, "_stop_services",
        lambda targets, sp: stopped.extend(t[1] for t in targets) or {})

    result = ds.start_bot()

    assert result["ok"] is False
    # Only the service THIS call launched is stopped; the reused pid is not.
    assert 4242 not in stopped
    assert stopped  # the launched one was rolled back
    if procs_file.exists():
        kept = json.loads(procs_file.read_text(encoding="utf-8"))
        assert kept["query"]["pid"] == 4242
