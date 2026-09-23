"""Shadow-run stopwatch beside the SHADOW pill (#32).

A shadow run is started from its own terminal and never lands in
`runtime/processes.json`, so the dashboard used to have no way of knowing
whether the rehearsal it displays started thirty seconds ago or ended twenty
minutes ago. The run now publishes its own heartbeat and the status payload
surfaces it -- but only for the store the page is actually reading.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core_brain.shadow_run import write_shadow_heartbeat
from dashboard import server as srv

STARTED_AT = 1_788_000_000.0


def _test_cfg():
    from core_brain.config import MakerConfig
    return MakerConfig()

_STATIC = Path(__file__).resolve().parent.parent / "dashboard" / "static"


def _heartbeat(tmp_path, monkeypatch, **overrides):
    """Write a heartbeat and point the reader at it."""
    target = tmp_path / "shadow_run.json"
    payload = {
        "pid": 4242,
        "run_id": "shadow-abc123",
        "started_at": STARTED_AT,
        "minutes": 30.0,
        "interval": 5.0,
        "db_path": str(tmp_path / "shadow.db"),
        "heartbeat_ts": STARTED_AT + 60.0,
        "finished": False,
    }
    payload.update(overrides)
    target.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(srv, "resolve_runtime_file", lambda *a, **k: target)
    return payload


def test_heartbeat_is_written_with_the_fields_the_dashboard_needs(tmp_path):
    # Arrange
    db = tmp_path / "shadow.db"
    target = tmp_path / "runtime" / "shadow_run.json"

    # Act
    written = write_shadow_heartbeat(db_path=db, run_id="shadow-abc123",
                                     minutes=30.0, interval=5.0,
                                     started_at=STARTED_AT, path=target)
    payload = json.loads(target.read_text(encoding="utf-8"))

    # Assert
    assert written == target
    assert payload["run_id"] == "shadow-abc123"
    assert payload["started_at"] == STARTED_AT
    assert payload["minutes"] == 30.0
    assert payload["db_path"] == str(db.resolve())
    assert payload["finished"] is False
    # The interval travels with the heartbeat: dashboard/server.py sizes its
    # stale window off it, so a writer that stopped publishing it would silently
    # move every run to the 30s floor.
    assert payload["interval"] == 5.0
    assert payload["heartbeat_ts"] > 0


def test_heartbeat_refresh_moves_the_timestamp_forward(tmp_path, monkeypatch):
    # Arrange — a clock that advances a rotation between the two writes, so
    # "refreshed" is asserted strictly rather than by wall-clock luck.
    from core_brain import shadow_run as sr

    ticks = iter([STARTED_AT + 5.0, STARTED_AT + 10.0])
    monkeypatch.setattr(sr.time, "time", lambda: next(ticks))
    target = tmp_path / "shadow_run.json"
    kwargs = dict(db_path=tmp_path / "shadow.db", run_id="shadow-abc123",
                  minutes=30.0, interval=5.0, started_at=STARTED_AT, path=target)
    write_shadow_heartbeat(**kwargs)
    first = json.loads(target.read_text(encoding="utf-8"))["heartbeat_ts"]

    # Act
    write_shadow_heartbeat(**kwargs)
    second = json.loads(target.read_text(encoding="utf-8"))["heartbeat_ts"]

    # Assert
    assert second > first


def test_a_fresh_heartbeat_reads_as_a_running_rehearsal(tmp_path, monkeypatch):
    # Arrange
    payload = _heartbeat(tmp_path, monkeypatch)

    # Act
    run = srv.read_shadow_run(payload["db_path"], now=STARTED_AT + 62.0)

    # Assert
    assert run is not None
    assert run["running"] is True
    assert run["ended"] is False
    assert run["elapsed_sec"] == pytest.approx(62.0)


def test_a_stale_heartbeat_reads_as_ended_and_stops_the_clock(tmp_path, monkeypatch):
    # Arrange — nobody refreshed it for far more than a few rotations.
    payload = _heartbeat(tmp_path, monkeypatch)

    # Act
    run = srv.read_shadow_run(payload["db_path"], now=STARTED_AT + 600.0)

    # Assert — frozen at the last heartbeat, not still counting.
    assert run["ended"] is True
    assert run["running"] is False
    assert run["elapsed_sec"] == pytest.approx(60.0)


def test_a_finished_heartbeat_reads_as_ended_immediately(tmp_path, monkeypatch):
    # Arrange
    payload = _heartbeat(tmp_path, monkeypatch, finished=True)

    # Act
    run = srv.read_shadow_run(payload["db_path"], now=STARTED_AT + 61.0)

    # Assert
    assert run["finished"] is True
    assert run["ended"] is True


def test_a_heartbeat_for_another_store_is_not_surfaced(tmp_path, monkeypatch):
    # Arrange
    _heartbeat(tmp_path, monkeypatch)

    # Act — the page is reading a different database.
    run = srv.read_shadow_run(str(tmp_path / "other.db"), now=STARTED_AT + 62.0)

    # Assert
    assert run is None


def test_no_heartbeat_file_is_not_an_error(tmp_path, monkeypatch):
    # Arrange
    monkeypatch.setattr(srv, "resolve_runtime_file",
                        lambda *a, **k: tmp_path / "missing.json")

    # Act / Assert
    assert srv.read_shadow_run(str(tmp_path / "shadow.db")) is None


def test_header_has_a_slot_for_the_shadow_stopwatch():
    # Arrange / Act
    html = (_STATIC / "index.html").read_text(encoding="utf-8")
    app_js = (_STATIC / "app.js").read_text(encoding="utf-8")

    # Assert
    assert 'id="shadow-run-clock"' in html
    assert "renderShadowClock" in app_js


def test_run_shadow_publishes_and_refreshes_its_heartbeat(tmp_path, monkeypatch):
    """The rehearsal itself writes the heartbeat, once per rotation."""
    # Arrange — a run whose loop rotates twice and then returns.
    from core_brain import shadow_run as sr
    from core_brain import trader_loop

    target = tmp_path / "runtime" / "shadow_run.json"
    monkeypatch.setattr(sr, "shadow_heartbeat_path", lambda root=None, run_id="": target)

    # A clock that steps one rotation per read, so a run that stopped
    # refreshing the heartbeat fails this test instead of passing on ties.
    clock = {"t": STARTED_AT}

    def tick():
        clock["t"] += 5.0
        return clock["t"]

    monkeypatch.setattr(sr.time, "time", tick)

    writes: list[dict] = []

    def fake_loop_run(seam, **kwargs):
        sleep_fn = kwargs["sleep_fn"]
        for _ in range(2):
            sleep_fn(0.0)
            writes.append(json.loads(target.read_text(encoding="utf-8")))
        return []

    monkeypatch.setattr(trader_loop, "run", fake_loop_run)
    monkeypatch.setattr(sr, "build_shadow_seam", lambda **kw: type("Seam", (), {})())

    # Act
    sr.run_shadow(minutes=0.0, db_path=tmp_path / "shadow.db",
                  markets_fn=lambda: [], client_fn=lambda: None,
                  decide_fn=lambda *a, **k: [], fetch_books=lambda *a, **k: {},
                  cfg=_test_cfg(), run_id="shadow-abc123", sleep_fn=lambda s: None)
    final = json.loads(target.read_text(encoding="utf-8"))

    # Assert — refreshed on every rotation, then marked finished on clean end.
    assert len(writes) == 2
    assert writes[1]["heartbeat_ts"] > writes[0]["heartbeat_ts"]
    assert writes[0]["finished"] is False
    assert final["finished"] is True
