from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import flight_supervisor as supervisor
from scripts import publish_flight_snapshot as publisher


def _cfg(db_name: str = "01_shadow_test.db") -> dict:
    return {
        "id": "shadow-test",
        "port": 8801,
        "name": "Test run",
        "desc": "Test",
        "db": f"data/{db_name}",
        "db_identity": db_name,
    }


def test_unavailable_run_telemetry_is_not_reported_as_zero(monkeypatch):
    monkeypatch.setattr(publisher, "fetch_json", lambda *_args, **_kwargs: None)

    telemetry = publisher.collect_run_telemetry(_cfg())

    assert telemetry["status_available"] is False
    assert telemetry["metrics_available"] is False
    assert telemetry["running"] is None
    assert telemetry["realized_pnl"] is None
    assert telemetry["total_with_rebate"] is None
    assert telemetry["errors"] == [
        "Dashboard status API unavailable or invalid",
        "Dashboard KPI API unavailable or invalid",
    ]


def test_dashboard_on_wrong_run_is_not_used_as_status_or_kpi(monkeypatch, tmp_path):
    monkeypatch.setattr(publisher, "PROJECT_ROOT", tmp_path)
    (tmp_path / "data").mkdir()
    expected_db = tmp_path / "data" / "01_shadow_test.db"
    wrong_db = tmp_path / "data" / "02_shadow_other.db"

    def fetch(url, **_kwargs):
        if url.endswith("/api/system/status"):
            return {
                "db_path": str(wrong_db),
                "shadow_run": {"run_id": "shadow-other", "running": True},
            }
        return {"realized_pnl": 50.0, "total_with_rebate": 50.0}

    monkeypatch.setattr(publisher, "fetch_json", fetch)

    telemetry = publisher.collect_run_telemetry(_cfg())

    assert telemetry["status_available"] is False
    assert telemetry["metrics_available"] is False
    assert telemetry["running"] is None
    assert telemetry["total_with_rebate"] is None
    assert "different run/store" in telemetry["errors"][-1]


def test_public_telemetry_does_not_include_local_session_details(monkeypatch, tmp_path):
    monkeypatch.setattr(publisher, "PROJECT_ROOT", tmp_path)
    (tmp_path / "data").mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "shadow-session-shadow-test.json").write_text(
        '{"pid": 1234, "command": "private local command"}', encoding="utf-8"
    )
    db = tmp_path / "data" / "01_shadow_test.db"

    def fetch(url, **_kwargs):
        if url.endswith("/api/system/status"):
            return {
                "db_path": str(db.resolve()),
                "shadow_run": {
                    "run_id": "shadow-test", "running": True,
                    "elapsed_sec": 20, "minutes": 60,
                    "heartbeat_age_sec": 1.5,
                },
            }
        return {
            "realized_pnl": 1.0, "total_with_rebate": 1.0,
            "roi_on_cost": 0.01, "fills": 2, "quotes": 3,
            "fill_rate": 0.5, "wins": 1, "losses": 0,
            "win_rate": 1.0, "bankroll": 100, "cost": 50,
            "settlements": [], "active_quoting_markets": 1,
            "avg_edge_cents": 2, "median_pair_cost": 0.98,
        }

    monkeypatch.setattr(publisher, "fetch_json", fetch)

    telemetry = publisher.collect_run_telemetry(_cfg())

    assert telemetry["status_available"] is True
    assert telemetry["metrics_available"] is True
    assert "session" not in telemetry
    assert "private local command" not in repr(telemetry)


def test_snapshot_html_marks_old_data_stale_and_unknown_metrics_as_unavailable():
    snapshot = {
        "timestamp": 1,
        "timestamp_str": "old snapshot",
        "runs": [{
            **_cfg(),
            "running": None,
            "status_available": False,
            "metrics_available": False,
            "errors": ["Dashboard unavailable"],
            "elapsed_hours": None,
            "target_hours": None,
            "heartbeat_age_sec": None,
            "realized_pnl": None,
            "total_with_rebate": None,
            "roi_pct": None,
            "fills": None,
            "quotes": None,
            "fill_rate_pct": None,
            "settlements": None,
            "wins": None,
            "losses": None,
            "win_rate_pct": None,
            "bankroll": None,
            "cost": None,
            "active_markets": None,
            "avg_edge_cents": None,
            "median_pair_cost": None,
        }],
    }

    page = publisher.generate_html(snapshot, [snapshot])

    assert "STALE SNAPSHOT" in page
    assert "STATUS DATA INCOMPLETE" in page
    assert "Telemetry unavailable" in page
    assert ">N/A<" in page
    assert "$+0.00" not in page
    assert "staleAfterMs = 10800000" in page
    assert "SNAPSHOT STALE" in page


def test_supervisor_degraded_status_is_visible_even_when_snapshot_is_fresh():
    snapshot = {
        "timestamp": supervisor.time.time(),
        "timestamp_str": "fresh snapshot",
        "runs": [],
        "supervisor_health": [{
            "id": "shadow-01",
            "started": True,
            "blocked": False,
            "finished": False,
            "loop_heartbeat_stale": True,
            "loop_heartbeat_age_sec": 999,
            "children": {
                "dash": {"running": True},
                "loop": {"running": True, "restart_failures": 1},
                "observer": {"running": False, "restart_failures": 1, "exit_code": 1},
                "screener": {"running": True},
            },
        }],
    }

    page = publisher.generate_html(snapshot, [])

    assert "SUPERVISOR DEGRADED" in page
    assert "DEGRADED" in page
    assert "observer: down (restarts 1) (exit 1)" in page


def test_public_history_allowlist_removes_unrecognized_local_fields():
    history = [{
        "timestamp": 1,
        "timestamp_str": "snapshot",
        "host_path": "C:/private/store.db",
        "runs": [{"id": "shadow-01", "running": True, "private_command": "secret"}],
        "supervisor_health": [{
            "id": "shadow-01", "children": {
                "loop": {"running": True, "pid": 1234, "command": "private"},
            },
            "local_path": "C:/private",
        }],
    }]

    sanitized = publisher._prune_public_history(history)

    assert "host_path" not in sanitized[0]
    assert "private_command" not in sanitized[0]["runs"][0]
    assert "local_path" not in sanitized[0]["supervisor_health"][0]
    assert "pid" not in sanitized[0]["supervisor_health"][0]["children"]["loop"]
    assert "private" not in repr(sanitized)


def test_deploy_failure_is_reported_instead_of_returning_success(monkeypatch):
    class Result:
        returncode = 1
        stdout = "deployment failed"
        stderr = "bad credentials"

    monkeypatch.setattr(publisher.shutil, "which", lambda _name: "vercel")
    monkeypatch.setattr(publisher.subprocess, "run", lambda *_args, **_kwargs: Result())

    with pytest.raises(RuntimeError, match="exited 1"):
        publisher.deploy_to_vercel()


def test_deploy_without_a_reported_url_is_not_treated_as_success(monkeypatch):
    class Result:
        returncode = 0
        stdout = "Build complete"
        stderr = ""

    monkeypatch.setattr(publisher.shutil, "which", lambda _name: "vercel")
    monkeypatch.setattr(publisher.subprocess, "run", lambda *_args, **_kwargs: Result())

    with pytest.raises(RuntimeError, match="did not report a deployment URL"):
        publisher.deploy_to_vercel()


def test_supervisor_lock_prevents_duplicate_owners(tmp_path):
    path = tmp_path / "supervisor.lock"

    first = supervisor.acquire_instance_lock(path)
    second = supervisor.acquire_instance_lock(path)
    assert first is not None
    assert second is None

    first.close()
    third = supervisor.acquire_instance_lock(path)
    assert third is not None
    third.close()


def test_failed_publish_uses_fresh_telemetry_on_retry(monkeypatch, caplog):
    snapshots = iter([
        {"timestamp": 123, "runs": []},
        {"timestamp": 456, "runs": []},
    ])
    calls = iter([RuntimeError("temporary Vercel outage"), "https://monitor.vercel.app"])
    monkeypatch.setattr(supervisor, "take_snapshot_and_render", lambda: next(snapshots))

    def deploy():
        result = next(calls)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(supervisor, "deploy_to_vercel", deploy)
    state = {"publish_failures": 0}

    retry_at = supervisor.publish_cycle(state, now=1000.0)

    assert state["publish_failures"] == 1
    assert 1000.0 < retry_at <= 1000.0 + supervisor.PUBLISH_RETRY_MAX_SEC
    assert "publish failed" in caplog.text

    next_publish = supervisor.publish_cycle(state, now=retry_at)

    assert next_publish == retry_at + supervisor.SNAPSHOT_INTERVAL_SEC
    assert state["publish_failures"] == 0


def test_missing_loop_heartbeat_is_recovered_after_startup_grace(monkeypatch, tmp_path):
    monkeypatch.setattr(supervisor, "RUNTIME_DIR", tmp_path)
    runner = supervisor.InstanceRunner({"id": "shadow-test", "port": 8801}, object())
    runner.started_at["loop"] = 0.0
    proc = type("Proc", (), {"pid": 4321})()
    now = [supervisor.LOOP_HEARTBEAT_STARTUP_GRACE_SEC - 1]
    monkeypatch.setattr(supervisor.time, "monotonic", lambda: now[0])

    assert runner._loop_heartbeat_stale(proc) is False

    now[0] += 2
    assert runner._loop_heartbeat_stale(proc) is True


def test_loop_with_a_fresh_matching_heartbeat_is_not_restarted(monkeypatch, tmp_path):
    monkeypatch.setattr(supervisor, "RUNTIME_DIR", tmp_path)
    runner = supervisor.InstanceRunner({"id": "shadow-test", "port": 8801}, object())
    runner.started_at["loop"] = 0.0
    monkeypatch.setattr(supervisor.time, "monotonic", lambda: 200.0)
    (tmp_path / "shadow_run_shadow-test.json").write_text(
        '{"pid": 4321, "run_id": "shadow-test", "heartbeat_ts": 999, '
        '"started_at": 900, "interval": 5, "cycle": 20, "finished": false}',
        encoding="utf-8",
    )
    proc = type("Proc", (), {"pid": 4321})()
    monkeypatch.setattr(supervisor.time, "time", lambda: 1000.0)

    assert runner._loop_heartbeat_stale(proc) is False


def test_preflight_rejects_unverifiable_dashboard_port(monkeypatch, tmp_path):
    runner = supervisor.InstanceRunner(
        {"id": "shadow-test", "port": 8801, "db": "data/test.db"}, object()
    )
    monkeypatch.setattr(supervisor, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(supervisor, "fetch_json", lambda *_a, **_k: None)

    class Connection:
        closed = False

        def close(self):
            self.closed = True

    connection = Connection()
    monkeypatch.setattr(supervisor.socket, "create_connection", lambda *_a, **_k: connection)

    with pytest.raises(RuntimeError, match="status is unavailable"):
        runner._preflight()
    assert connection.closed
    assert runner.preflight_done is False


def test_preflight_adopts_only_matching_live_shadow_dashboard(monkeypatch, tmp_path):
    from dashboard import server as dashboard

    runner = supervisor.InstanceRunner(
        {"id": "shadow-test", "port": 8801, "db": "data/test.db"}, object()
    )
    monkeypatch.setattr(supervisor, "RUNTIME_DIR", tmp_path)
    db_path = (supervisor.PROJECT_ROOT / "data/test.db").resolve()
    (tmp_path / "shadow_run_shadow-test.json").write_text(
        json.dumps({
            "pid": 1234, "process_started_at": 900, "run_id": "shadow-test",
            "started_at": 905, "db_path": str(db_path), "heartbeat_ts": 999,
            "minutes": 60, "interval": 5, "cycle": 10, "finished": False,
        }),
        encoding="utf-8",
    )

    class Connection:
        def close(self):
            pass

    monkeypatch.setattr(supervisor.socket, "create_connection", lambda *_a, **_k: Connection())
    monkeypatch.setattr(dashboard, "_is_pid_alive", lambda _pid, _started: True)
    monkeypatch.setattr(dashboard, "_process_start_time", lambda _pid: 900.0)
    monkeypatch.setattr(supervisor, "fetch_json", lambda *_a, **_k: {
        "db_path": str(db_path),
        "db_is_production": False,
        "services": {"dash": {"pid": 5678, "started_at": 900}},
        "shadow_run": {"run_id": "shadow-test", "pid": 1234, "running": True},
    })

    runner._preflight()

    assert runner.preflight_done is True
    assert runner.attached_existing is True
    assert runner.procs["dash"].external is True
    assert runner.procs["loop"].external is True


def test_preflight_adopts_loop_when_dashboard_is_down(monkeypatch, tmp_path):
    from dashboard import server as dashboard

    runner = supervisor.InstanceRunner(
        {"id": "shadow-test", "port": 8801, "db": "data/test.db"}, object()
    )
    monkeypatch.setattr(supervisor, "RUNTIME_DIR", tmp_path)
    db_path = (supervisor.PROJECT_ROOT / "data/test.db").resolve()
    (tmp_path / "shadow_run_shadow-test.json").write_text(json.dumps({
        "pid": 1234, "process_started_at": 900, "run_id": "shadow-test",
        "started_at": 905, "db_path": str(db_path), "heartbeat_ts": 999,
        "minutes": 60, "interval": 5, "cycle": 10, "finished": False,
    }), encoding="utf-8")

    def no_dashboard(*_args, **_kwargs):
        raise OSError(10061, "connection refused")

    monkeypatch.setattr(supervisor.socket, "create_connection", no_dashboard)
    monkeypatch.setattr(dashboard, "_is_pid_alive", lambda _pid, _started=None: True)
    monkeypatch.setattr(dashboard, "_process_start_time", lambda _pid: 900.0)

    runner._preflight()

    assert runner.procs["loop"].external is True
    assert runner.attached_existing is True
    assert runner.procs["dash"] is None


def test_preflight_rejects_a_dashboard_that_reports_another_loop_pid(monkeypatch, tmp_path):
    from dashboard import server as dashboard

    runner = supervisor.InstanceRunner(
        {"id": "shadow-test", "port": 8801, "db": "data/test.db"}, object()
    )
    monkeypatch.setattr(supervisor, "RUNTIME_DIR", tmp_path)
    db_path = (supervisor.PROJECT_ROOT / "data/test.db").resolve()
    (tmp_path / "shadow_run_shadow-test.json").write_text(json.dumps({
        "pid": 1234, "process_started_at": 900, "run_id": "shadow-test",
        "started_at": 905, "db_path": str(db_path), "heartbeat_ts": 999,
        "minutes": 60, "interval": 5, "cycle": 10, "finished": False,
    }), encoding="utf-8")

    class Connection:
        def close(self):
            pass

    monkeypatch.setattr(supervisor.socket, "create_connection", lambda *_a, **_k: Connection())
    monkeypatch.setattr(dashboard, "_is_pid_alive", lambda _pid, _started=None: True)
    monkeypatch.setattr(dashboard, "_process_start_time", lambda _pid: 900.0)
    monkeypatch.setattr(supervisor, "fetch_json", lambda *_a, **_k: {
        "db_path": str(db_path),
        "db_is_production": False,
        "services": {"dash": {"pid": 5678, "started_at": 900}},
        "shadow_run": {"run_id": "shadow-test", "pid": 9999, "running": True},
    })

    with pytest.raises(RuntimeError, match="active but unverifiable"):
        runner._preflight()


def test_adopt_session_children_requires_matching_store_and_process_identity(monkeypatch, tmp_path):
    from dashboard import server as dashboard

    runner = supervisor.InstanceRunner(
        {"id": "shadow-test", "port": 8801, "db": "data/test.db"}, object()
    )
    monkeypatch.setattr(supervisor, "RUNTIME_DIR", tmp_path)
    db_path = (supervisor.PROJECT_ROOT / "data/test.db").resolve()
    starts = {1234: 900.0, 1235: 905.0, 1236: 910.0}
    start_ticks = lambda value: int((value + 62_135_596_800.0) * 10_000_000)
    (tmp_path / "shadow-session-shadow-test.json").write_text(json.dumps({
        "run_id": "shadow-test",
        "shadow_db": str(db_path),
        "loop": {"pid": 1234, "started_ticks": start_ticks(starts[1234])},
        "screener": {"pid": 1235, "started_ticks": start_ticks(starts[1235])},
        "observer": {"pid": 1236, "started_ticks": start_ticks(starts[1236])},
    }), encoding="utf-8")
    existing_loop = supervisor.ObservedProcess(1234, starts[1234])
    runner.procs["loop"] = existing_loop
    monkeypatch.setattr(dashboard, "_process_start_time", lambda pid: starts.get(pid))
    monkeypatch.setattr(dashboard, "_is_pid_alive", lambda _pid, _started=None: True)

    runner._adopt_session_children()

    assert runner.procs["loop"] is existing_loop
    assert runner.procs["screener"].pid == 1235
    assert runner.procs["observer"].pid == 1236
    assert runner.procs["screener"].external is True


def test_adopt_session_children_rejects_a_different_database(monkeypatch, tmp_path):
    runner = supervisor.InstanceRunner(
        {"id": "shadow-test", "port": 8801, "db": "data/test.db"}, object()
    )
    monkeypatch.setattr(supervisor, "RUNTIME_DIR", tmp_path)
    (tmp_path / "shadow-session-shadow-test.json").write_text(json.dumps({
        "run_id": "shadow-test",
        "shadow_db": str(supervisor.PROJECT_ROOT / "data/other.db"),
    }), encoding="utf-8")

    with pytest.raises(RuntimeError, match="store does not match"):
        runner._adopt_session_children()


def test_preflight_refuses_active_run_without_matching_heartbeat(monkeypatch, tmp_path):
    from dashboard import server as dashboard

    runner = supervisor.InstanceRunner(
        {"id": "shadow-test", "port": 8801, "db": "data/test.db"}, object()
    )
    monkeypatch.setattr(supervisor, "RUNTIME_DIR", tmp_path)

    class Connection:
        def close(self):
            pass

    monkeypatch.setattr(supervisor.socket, "create_connection", lambda *_a, **_k: Connection())
    monkeypatch.setattr(dashboard, "_is_pid_alive", lambda _pid, _started: True)
    db_path = (supervisor.PROJECT_ROOT / "data/test.db").resolve()
    monkeypatch.setattr(supervisor, "fetch_json", lambda *_a, **_k: {
        "db_path": str(db_path),
        "db_is_production": False,
        "services": {"dash": {"pid": 5678, "started_at": 900}},
        "shadow_run": {"run_id": "shadow-test", "running": True},
    })

    with pytest.raises(RuntimeError, match="active but unverifiable"):
        runner._preflight()
    assert runner.preflight_done is False


def test_stale_adopted_loop_is_not_killed_or_duplicated(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(supervisor, "RUNTIME_DIR", tmp_path)
    runner = supervisor.InstanceRunner({"id": "shadow-test", "port": 8801}, object())
    runner.started_at["loop"] = 0.0
    proc = type("Proc", (), {
        "pid": 4321,
        "external": True,
        "poll": lambda self: None,
        "terminate": lambda self: pytest.fail("external loop must not be killed"),
    })()
    runner.procs["loop"] = proc
    runner._loop_heartbeat_stale = lambda _proc: True
    monkeypatch.setattr(supervisor.time, "monotonic", lambda: 500.0)

    runner._ensure_child("loop", ["-m", "core_brain.shadow_run"])

    assert runner.procs["loop"] is proc
    assert "leaving external process untouched" in caplog.text
    assert runner.failures.get("loop", 0) == 0


def test_monitor_health_snapshot_excludes_pids_and_local_paths():
    runner = supervisor.InstanceRunner({"id": "shadow-test", "port": 8801, "db": "C:/private/test.db"}, object())
    child = type("Proc", (), {"pid": 9876, "poll": lambda self: None})()
    runner.procs["loop"] = child
    runner.started_at["loop"] = 0.0
    runner.started_wall["loop"] = 900.0
    runner.preflight_done = True
    runner._loop_heartbeat = lambda _proc: {
        "heartbeat_ts": 999.0, "started_at": 900.0,
    }

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(supervisor.time, "time", lambda: 1000.0)
    try:
        public = runner.monitor_snapshot()
    finally:
        monkeypatch.undo()

    assert public["children"]["loop"]["running"] is True
    assert public["loop_heartbeat_age_sec"] == 1.0
    assert "pid" not in repr(public).lower()
    assert "private" not in repr(public)


def test_history_sanitizer_removes_local_session_payloads():
    history = [{"runs": [{"id": "shadow-01", "session": {"pid": 4321, "command": "local"}}]}]

    sanitized = publisher._prune_public_history(history)

    assert "session" not in sanitized[0]["runs"][0]


def test_process_start_time_helper_is_readable():
    import os

    from core_brain.shadow_run import _process_start_time

    started = _process_start_time(os.getpid())

    assert started is not None
    assert started <= supervisor.time.time() + 1


def test_supervisor_window_defaults_to_about_four_days():
    args = supervisor.parse_args([])

    assert args.duration_hours == pytest.approx(100.08)


def test_supervisor_rejects_unbounded_or_invalid_windows():
    for value in ("0", "-1", "nan", "inf"):
        with pytest.raises(SystemExit) as exc:
            supervisor.parse_args(["--duration-hours", value])
        assert exc.value.code == 2


def test_system_awake_guard_uses_and_releases_windows_execution_state():
    calls = []

    class Api:
        def SetThreadExecutionState(self, flags):
            calls.append(flags)
            return 1

    guard = supervisor.SystemAwakeGuard(windows=True, api=Api())
    guard.start()
    assert guard.active is True
    guard.stop()

    assert calls == [
        supervisor.SystemAwakeGuard.ES_CONTINUOUS | supervisor.SystemAwakeGuard.ES_SYSTEM_REQUIRED,
        supervisor.SystemAwakeGuard.ES_CONTINUOUS,
    ]
    assert guard.active is False


def test_supervisor_degraded_does_not_flag_startup_before_children_exist():
    assert publisher._supervisor_degraded({
        "started": False,
        "blocked": False,
        "finished": False,
        "children": {},
    }) is False

    assert publisher._supervisor_degraded({
        "started": True,
        "blocked": False,
        "finished": False,
        "children": {"dash": {"running": True}, "loop": {"running": False}},
    }) is True
