"""Acceptance tests for tournament discovery, dashboard port reporting, and run switching."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core_brain.order_registry import SCHEMA
from dashboard import server as srv
from dashboard.server import app, set_db_override


def _clock() -> float:
    return time.time()


def _make_db(tmp_path: Path, name: str) -> Path:
    db = tmp_path / name
    con = sqlite3.connect(str(db))
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    return db


def _wire_runtime(tmp_path: Path, monkeypatch) -> Path:
    runtime = tmp_path / "runtime"
    runtime.mkdir(exist_ok=True)
    monkeypatch.setattr(
        srv, "resolve_runtime_file", lambda name, root=None: runtime / name
    )
    return runtime


def _write_hb(
    runtime: Path,
    name: str,
    db: Path,
    run_id: str,
    *,
    age: float = 2.0,
    finished: bool = False,
    pid: int = 1234,
    dash_port: int | None = None,
) -> None:
    path = runtime / name
    now = _clock()
    payload = {
        "pid": pid,
        "run_id": run_id,
        "started_at": now - 300.0,
        "heartbeat_ts": now - age,
        "interval": 5.0,
        "minutes": 60.0,
        "db_path": str(db),
        "cycle": 10,
        "finished": finished,
    }
    if dash_port is not None:
        payload["dash_port"] = dash_port
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture(autouse=True)
def clean_snapshot_cache(monkeypatch):
    monkeypatch.setattr(srv, "_snapshots", {}, raising=False)
    monkeypatch.setattr(srv, "_snapshot_builders", {}, raising=False)


def test_list_shadow_runs_discovers_tournament_and_dash_port(tmp_path, monkeypatch):
    runtime = _wire_runtime(tmp_path, monkeypatch)

    db1 = _make_db(tmp_path, "371_tournament_02_conservative_20261005-031500.db")
    db2 = _make_db(tmp_path, "371_tournament_01_aggressive_20261005-031500.db")

    # db1 written slightly more recently than db2, but db2 has lower index (01 vs 02)
    _write_hb(runtime, "shadow_run_t02.json", db1, "shadow-371-t02-conservative", age=1.0, dash_port=8802)
    _write_hb(runtime, "shadow_run_t01.json", db2, "shadow-371-t01-aggressive", age=2.0, dash_port=8801)

    runs = srv.list_shadow_runs()
    assert len(runs) >= 2

    # Check tournament cluster ordering: index 01 should precede index 02
    t_runs = [r for r in runs if r.get("tournament") and r["tournament"].get("issue") == 371]
    assert len(t_runs) == 2
    assert t_runs[0]["tournament"]["index"] == 1
    assert t_runs[0]["tournament"]["arm"] == "aggressive"
    assert t_runs[0]["dash_port"] == 8801

    assert t_runs[1]["tournament"]["index"] == 2
    assert t_runs[1]["tournament"]["arm"] == "conservative"
    assert t_runs[1]["dash_port"] == 8802


def test_switch_active_db_and_status_update(tmp_path, monkeypatch):
    _wire_runtime(tmp_path, monkeypatch)
    db1 = _make_db(tmp_path, "store1.db")
    db2 = _make_db(tmp_path, "store2.db")

    set_db_override(db1)
    try:
        client = TestClient(app)
        res = client.get("/api/system/status")
        assert res.status_code == 200
        data = res.json()
        assert Path(data["db_path"]).resolve() == db1.resolve()

        # Switch to store2
        headers = {"X-Control-Token": srv.CONTROL_TOKEN}
        switch_res = client.post("/api/system/db", params={"db": str(db2)}, headers=headers)
        assert switch_res.status_code == 200
        switch_data = switch_res.json()
        assert switch_data["ok"] is True

        # Status now reports store2
        res2 = client.get("/api/system/status")
        assert res2.status_code == 200
        data2 = res2.json()
        assert Path(data2["db_path"]).resolve() == db2.resolve()
    finally:
        set_db_override(None)


def test_finished_shadow_run_retains_per_run_ring(tmp_path, monkeypatch):
    from core_brain.shadow_run import _run_ring_name

    runtime = _wire_runtime(tmp_path, monkeypatch)
    db = _make_db(tmp_path, "finished_run.db")
    run_id = "shadow-finished-123"

    # Write a finished heartbeat
    _write_hb(runtime, "shadow_run_fin.json", db, run_id, age=5.0, finished=True)

    # Write a per-run ring file with canonical name
    ring_file = runtime / _run_ring_name(run_id)
    ring_file.write_text('{"cycle": 1, "action": "test"}\n', encoding="utf-8")

    set_db_override(db)
    try:
        resolved = srv.resolve_ring_path()
        # Even though finished, it must still resolve to the per-run ring!
        assert resolved == ring_file
    finally:
        set_db_override(None)


def test_status_reports_actual_active_port(monkeypatch):
    monkeypatch.setattr(srv, "_ACTIVE_PORT", 8899)
    client = TestClient(app)
    res = client.get("/api/system/status")
    assert res.status_code == 200
    data = res.json()
    assert data["services"]["dash"]["port"] == 8899
