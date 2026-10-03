"""`/api/scan-state` during a shadow rehearsal.

A shadow run has no live poll loop, so it writes no `live_poll_heartbeat.json`
and its per-cycle events go to `runtime/shadow-<run_id>.jsonl`, not the live
`cycle_events.jsonl` the screener appends to. Before this, the dashboard read
the live ring (no rehearsal events) and the absent heartbeat, so the scan pill
showed STALLED for a healthy rehearsal.

Now `resolve_ring_path()` follows the rehearsal's ring when the page is on a
shadow store, and `get_scan_state()` falls back to the shadow heartbeat.
"""
from __future__ import annotations

import datetime
import json
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from core_brain.order_registry import SCHEMA
from dashboard import server as srv
from dashboard.server import (
    app,
    set_db_override,
    set_heartbeat_override,
    set_ring_override,
)


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def temp_db(tmp_path):
    db_file = tmp_path / "01_shadow.db"
    con = sqlite3.connect(str(db_file))
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    return db_file


@pytest.fixture
def client(temp_db):
    set_db_override(temp_db)
    yield TestClient(app)
    set_db_override(None)


def _fresh_quoting_ring(tmp_path):
    ring = tmp_path / "shadow-01.jsonl"
    ring.write_text(
        json.dumps({
            "ts": _now_iso(), "service": "decide", "cycle": 42,
            "phase": "quoting", "action": "decide", "market_slug": "atp-x",
            "reason": "", "latency_ms": 0.0, "pid": 1, "extra": {},
        }) + "\n",
        encoding="utf-8",
    )
    return ring


def test_scan_state_falls_back_to_the_shadow_heartbeat(client, tmp_path, monkeypatch):
    ring = _fresh_quoting_ring(tmp_path)
    missing_hb = tmp_path / "no_such_heartbeat.json"
    monkeypatch.setattr(
        srv, "read_shadow_run",
        lambda *a, **k: {"running": True, "heartbeat_age_sec": 4.0, "run_id": "shadow-01"},
    )
    set_ring_override(ring)
    set_heartbeat_override(missing_hb)
    try:
        res = client.get("/api/scan-state")
    finally:
        set_ring_override(None)
        set_heartbeat_override(None)

    assert res.status_code == 200
    data = res.json()
    # Fresh quoting event + a live shadow heartbeat -> SCANNING, not STALLED.
    assert data["scan_state"] == "SCANNING"
    assert data["seconds_since_heartbeat"] is not None
    assert data["seconds_since_heartbeat"] == pytest.approx(4.0, abs=2.0)


def test_scan_state_prefers_shadow_over_a_stale_live_heartbeat(client, tmp_path, monkeypatch):
    # A live_poll_heartbeat.json left by an earlier live run is stale but not
    # absent, so `hb_ts` is set. Without preferring the matched shadow run the
    # pill would flip to STALLED after 90s while the rehearsal is healthy.
    ring = _fresh_quoting_ring(tmp_path)
    stale_hb = tmp_path / "heartbeat.json"
    stale_hb.write_text(
        json.dumps([{"ts": int((time.time() - 3600) * 1000), "cycle": 1, "errors": 0}]),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        srv, "read_shadow_run",
        lambda *a, **k: {"running": True, "heartbeat_age_sec": 3.0, "run_id": "shadow-01"},
    )
    set_ring_override(ring)
    set_heartbeat_override(stale_hb)
    try:
        res = client.get("/api/scan-state")
    finally:
        set_ring_override(None)
        set_heartbeat_override(None)

    data = res.json()
    assert data["scan_state"] == "SCANNING"
    assert data["seconds_since_heartbeat"] == pytest.approx(3.0, abs=2.0)


def test_scan_state_keeps_the_live_heartbeat_when_no_shadow_matches(client, tmp_path, monkeypatch):
    # No rehearsal for this store -> the live heartbeat still drives the pill.
    ring = _fresh_quoting_ring(tmp_path)
    fresh_hb = tmp_path / "heartbeat.json"
    fresh_hb.write_text(
        json.dumps([{"ts": int(time.time() * 1000), "cycle": 9, "errors": 0}]),
        encoding="utf-8",
    )
    monkeypatch.setattr(srv, "read_shadow_run", lambda *a, **k: None)
    set_ring_override(ring)
    set_heartbeat_override(fresh_hb)
    try:
        res = client.get("/api/scan-state")
    finally:
        set_ring_override(None)
        set_heartbeat_override(None)

    data = res.json()
    assert data["scan_state"] == "SCANNING"
    assert data["seconds_since_heartbeat"] is not None


def test_scan_state_stays_stalled_with_no_heartbeat_and_no_shadow(client, tmp_path, monkeypatch):
    ring = _fresh_quoting_ring(tmp_path)
    missing_hb = tmp_path / "no_such_heartbeat.json"
    monkeypatch.setattr(srv, "read_shadow_run", lambda *a, **k: None)
    set_ring_override(ring)
    set_heartbeat_override(missing_hb)
    try:
        res = client.get("/api/scan-state")
    finally:
        set_ring_override(None)
        set_heartbeat_override(None)

    assert res.json()["scan_state"] == "STALLED"


def test_scan_state_ignores_an_ended_shadow_run(client, tmp_path, monkeypatch):
    ring = _fresh_quoting_ring(tmp_path)
    missing_hb = tmp_path / "no_such_heartbeat.json"
    monkeypatch.setattr(
        srv, "read_shadow_run",
        lambda *a, **k: {"running": False, "ended": True, "end_reason": "heartbeat_stale", "heartbeat_age_sec": 900.0, "run_id": "shadow-01"},
    )
    set_ring_override(ring)
    set_heartbeat_override(missing_hb)
    try:
        res = client.get("/api/scan-state")
    finally:
        set_ring_override(None)
        set_heartbeat_override(None)

    data = res.json()
    assert data["scan_state"] == "STALLED"
    assert data["stall_reason"] == "heartbeat_stale"


def test_scan_state_shadow_fresh_provenance(client, tmp_path, monkeypatch):
    ring = _fresh_quoting_ring(tmp_path)
    missing_hb = tmp_path / "no_such_heartbeat.json"
    monkeypatch.setattr(
        srv, "read_shadow_run",
        lambda *a, **k: {
            "running": True,
            "ended": False,
            "heartbeat_age_sec": 2.0,
            "run_id": "shadow-01",
            "heartbeat_file": "runtime/shadow_run_shadow-01.json",
            "db_path": "/path/to/01_shadow.db",
            "pid": 1234,
        },
    )
    set_ring_override(ring)
    set_heartbeat_override(missing_hb)
    try:
        res = client.get("/api/scan-state")
    finally:
        set_ring_override(None)
        set_heartbeat_override(None)

    data = res.json()
    assert data["scan_state"] == "SCANNING"
    assert data["stall_reason"] is None
    src = data["heartbeat_source"]
    assert src["kind"] == "shadow_run"
    assert src["run_id"] == "shadow-01"
    assert src["file"] == "runtime/shadow_run_shadow-01.json"
    assert src["db_path"] == "/path/to/01_shadow.db"
    assert src["pid"] == 1234


def test_scan_state_shadow_finished_gives_stalled_and_reason(client, tmp_path, monkeypatch):
    ring = _fresh_quoting_ring(tmp_path)
    missing_hb = tmp_path / "no_such_heartbeat.json"
    monkeypatch.setattr(
        srv, "read_shadow_run",
        lambda *a, **k: {
            "running": False,
            "ended": True,
            "end_reason": "finished",
            "heartbeat_age_sec": 2.0,
            "run_id": "shadow-01",
            "heartbeat_file": "runtime/shadow_run_shadow-01.json",
        },
    )
    set_ring_override(ring)
    set_heartbeat_override(missing_hb)
    try:
        res = client.get("/api/scan-state")
    finally:
        set_ring_override(None)
        set_heartbeat_override(None)

    data = res.json()
    assert data["scan_state"] == "STALLED"
    assert data["stall_reason"] == "finished"


def test_scan_state_shadow_process_gone_gives_stalled_and_reason(client, tmp_path, monkeypatch):
    ring = _fresh_quoting_ring(tmp_path)
    missing_hb = tmp_path / "no_such_heartbeat.json"
    monkeypatch.setattr(
        srv, "read_shadow_run",
        lambda *a, **k: {
            "running": False,
            "ended": True,
            "end_reason": "process_gone",
            "heartbeat_age_sec": 20.0,
            "run_id": "shadow-01",
            "heartbeat_file": "runtime/shadow_run_shadow-01.json",
            "pid": 99999,
        },
    )
    set_ring_override(ring)
    set_heartbeat_override(missing_hb)
    try:
        res = client.get("/api/scan-state")
    finally:
        set_ring_override(None)
        set_heartbeat_override(None)

    data = res.json()
    assert data["scan_state"] == "STALLED"
    assert data["stall_reason"] == "process_gone"


def test_scan_state_no_heartbeat_gives_stalled_and_reason(client, tmp_path, monkeypatch):
    ring = _fresh_quoting_ring(tmp_path)
    missing_hb = tmp_path / "no_such_heartbeat.json"
    monkeypatch.setattr(srv, "read_shadow_run", lambda *a, **k: None)
    set_ring_override(ring)
    set_heartbeat_override(missing_hb)
    try:
        res = client.get("/api/scan-state")
    finally:
        set_ring_override(None)
        set_heartbeat_override(None)

    data = res.json()
    assert data["scan_state"] == "STALLED"
    assert data["stall_reason"] == "no_heartbeat"
    assert data["heartbeat_source"]["kind"] == "none"


def test_scan_state_heartbeat_unreadable_gives_stalled_and_reason(client, tmp_path, monkeypatch):
    ring = _fresh_quoting_ring(tmp_path)
    bad_hb = tmp_path / "malformed_heartbeat.json"
    bad_hb.write_text("invalid json content", encoding="utf-8")
    monkeypatch.setattr(srv, "read_shadow_run", lambda *a, **k: None)
    set_ring_override(ring)
    set_heartbeat_override(bad_hb)
    try:
        res = client.get("/api/scan-state")
    finally:
        set_ring_override(None)
        set_heartbeat_override(None)

    data = res.json()
    assert data["scan_state"] == "STALLED"
    assert data["stall_reason"] == "heartbeat_unreadable"


def test_scan_state_live_engine_fallback_provenance(client, tmp_path, monkeypatch):
    ring = _fresh_quoting_ring(tmp_path)
    fresh_hb = tmp_path / "live_poll_heartbeat.json"
    fresh_hb.write_text(
        json.dumps([{"ts": int(time.time() * 1000), "cycle": 9, "errors": 0, "pid": 4321}]),
        encoding="utf-8",
    )
    monkeypatch.setattr(srv, "read_shadow_run", lambda *a, **k: None)
    set_ring_override(ring)
    set_heartbeat_override(fresh_hb)
    try:
        res = client.get("/api/scan-state")
    finally:
        set_ring_override(None)
        set_heartbeat_override(None)

    data = res.json()
    assert data["scan_state"] == "SCANNING"
    src = data["heartbeat_source"]
    assert src["kind"] == "live_engine"
    assert src["db_path"] is None
    assert src["pid"] == 4321


def test_scan_state_ticket_scenario_dead_here_live_elsewhere(client, tmp_path, monkeypatch):
    # Dead run on displayed store, live run on another store
    ring = _fresh_quoting_ring(tmp_path)
    missing_hb = tmp_path / "no_such_heartbeat.json"
    monkeypatch.setattr(
        srv, "read_shadow_run",
        lambda *a, **k: {
            "running": False,
            "ended": True,
            "end_reason": "process_gone",
            "heartbeat_age_sec": 4800.0,
            "run_id": "shadow-01",
            "heartbeat_file": "runtime/shadow_run_shadow-01.json",
        },
    )
    monkeypatch.setattr(
        srv, "read_other_live_shadow_runs",
        lambda *a, **k: [{
            "run_id": "ladder-live",
            "db_path": "/path/to/NN_shadow_ladder.db",
            "heartbeat_file": "runtime/shadow_run_ladder-live.json",
        }],
    )
    set_ring_override(ring)
    set_heartbeat_override(missing_hb)
    try:
        res = client.get("/api/scan-state")
    finally:
        set_ring_override(None)
        set_heartbeat_override(None)

    data = res.json()
    assert data["scan_state"] == "STALLED"
    assert data["stall_reason"] == "process_gone"
    assert data["seconds_since_heartbeat"] == pytest.approx(4800.0, abs=2.0)
    assert len(data["other_live_runs"]) == 1
    assert data["other_live_runs"][0]["run_id"] == "ladder-live"


# --- ring resolution --------------------------------------------------------


def test_resolve_shadow_ring_points_at_the_run_scoped_file(tmp_path, monkeypatch):
    ring = tmp_path / "shadow-01.jsonl"
    ring.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        srv, "read_shadow_run",
        lambda *a, **k: {"running": True, "run_id": "shadow-01"},
    )
    monkeypatch.setattr(srv, "resolve_runtime_file", lambda name, root=None: tmp_path / name)

    assert srv._resolve_shadow_ring_path() == ring


def test_resolve_shadow_ring_is_none_when_the_ring_is_not_on_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(
        srv, "read_shadow_run",
        lambda *a, **k: {"running": True, "run_id": "shadow-01"},
    )
    monkeypatch.setattr(srv, "resolve_runtime_file", lambda name, root=None: tmp_path / name)

    assert srv._resolve_shadow_ring_path() is None


def test_resolve_shadow_ring_is_none_without_a_running_rehearsal(monkeypatch):
    monkeypatch.setattr(srv, "read_shadow_run", lambda *a, **k: None)

    assert srv._resolve_shadow_ring_path() is None


def test_resolve_ring_path_prefers_an_explicit_override(tmp_path, monkeypatch):
    override = tmp_path / "explicit.jsonl"
    override.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        srv, "read_shadow_run",
        lambda *a, **k: {"running": True, "run_id": "shadow-01"},
    )
    set_ring_override(override)
    try:
        assert srv.resolve_ring_path() == override
    finally:
        set_ring_override(None)
