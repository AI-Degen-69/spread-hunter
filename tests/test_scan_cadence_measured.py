"""The scan watchdog ages against the cycle time it MEASURES, not the configured one.

`--interval` is the sleep between rotations, not the length of a rotation. A
shadow rehearsal configured at `interval=5.0` whose rotations really take ~160s
(public CLOB order-book reads over a thin universe) was judged against
`max(120, 3 x 5.0) = 120s`, so a perfectly healthy loop crossed the line every
single cycle and the header pill sat on red DOWN most of the time.

The heartbeat now carries its own rotation count, which makes the observed
cadence a division: `(heartbeat_ts - started_at) / cycle`. The watchdog ramps
off that, and falls back to `interval` for a heartbeat written before the field
existed.
"""
from __future__ import annotations

import json
import time

import pytest

from dashboard import server as dash
from core_brain.shadow_run import write_shadow_heartbeat


@pytest.fixture()
def shadow_heartbeat(tmp_path, monkeypatch):
    """Write a legacy shared-name heartbeat for a store this page is 'reading'
    (the per-run names resolve to this same tmp runtime and are empty)."""
    db = tmp_path / "99_shadow_test.db"
    db.write_bytes(b"")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    hb = runtime / "shadow_run.json"

    def _resolve(name, root=None):
        return hb if name == "shadow_run.json" else runtime / name

    monkeypatch.setattr(dash, "resolve_runtime_file", _resolve)

    def _write(**fields):
        payload = {
            "pid": 0,
            "run_id": "shadow-test",
            "db_path": str(db),
            "minutes": 60.0,
            "interval": 5.0,
            "finished": False,
        }
        payload.update(fields)
        hb.write_text(json.dumps(payload), encoding="utf-8")
        return str(db)

    return _write


def test_the_measured_cadence_comes_from_the_rotation_count(shadow_heartbeat):
    # Arrange -- 10 rotations over 1600s: a real cadence of 160s per rotation,
    # 32x the configured 5.0s interval.
    now = 1_000_000.0
    db = shadow_heartbeat(started_at=now - 1600.0, heartbeat_ts=now, cycle=10)

    # Act
    run = dash.read_shadow_run(db, now=now)

    # Assert
    assert run is not None
    assert run["cadence_sec"] == pytest.approx(160.0)


def test_a_healthy_slow_loop_is_not_called_stalled(shadow_heartbeat):
    # Arrange -- the exact shape that showed DOWN: rotations of ~160s, and a
    # heartbeat 130s old. That is mid-rotation, not a dead loop.
    now = 1_000_000.0
    db = shadow_heartbeat(started_at=now - 1730.0, heartbeat_ts=now - 130.0, cycle=10)

    # Act
    run = dash.read_shadow_run(db, now=now)

    # Assert -- 3 x 160s = 480s of grace, so 130s is still running.
    assert run["running"] is True, (
        f"a 130s-old heartbeat on a {run['cadence_sec']:.0f}s cadence read as ended"
    )


def test_a_loop_that_really_died_still_goes_stale(shadow_heartbeat):
    # Arrange -- same 160s cadence, but silent for well past three rotations.
    now = 1_000_000.0
    db = shadow_heartbeat(started_at=now - 2200.0, heartbeat_ts=now - 600.0, cycle=10)

    # Act / Assert -- widening the ramp must not disable the watchdog.
    assert dash.read_shadow_run(db, now=now)["ended"] is True


def test_a_heartbeat_without_a_cycle_count_falls_back_to_the_interval(shadow_heartbeat):
    # A file written by the previous build has no `cycle`; the old behaviour
    # (ramp off `interval`) must survive, not crash and not read as 0s.
    now = 1_000_000.0
    db = shadow_heartbeat(started_at=now - 600.0, heartbeat_ts=now)

    run = dash.read_shadow_run(db, now=now)

    assert run["cadence_sec"] == pytest.approx(5.0)


def test_the_cadence_never_falls_below_the_configured_interval(shadow_heartbeat):
    # A burst of fast rotations early in a run must not shrink the ramp below
    # what the operator configured -- that would manufacture false alarms.
    now = 1_000_000.0
    db = shadow_heartbeat(started_at=now - 2.0, heartbeat_ts=now, cycle=10)

    assert dash.read_shadow_run(db, now=now)["cadence_sec"] == pytest.approx(5.0)


def test_the_heartbeat_file_records_the_rotation_count(tmp_path):
    # The reader can only divide by a number the writer actually wrote.
    target = tmp_path / "shadow_run.json"
    write_shadow_heartbeat(db_path=tmp_path / "x.db", run_id="r", minutes=1.0,
                           interval=5.0, started_at=1.0, cycle=7, path=target)

    assert json.loads(target.read_text(encoding="utf-8"))["cycle"] == 7


def test_the_scan_state_endpoint_publishes_the_cadence_it_judged_against(
        shadow_heartbeat, monkeypatch):
    # The page draws its own pill ramp; it cannot do that from a threshold the
    # server keeps to itself.
    now = time.time()
    db = shadow_heartbeat(started_at=now - 1600.0, heartbeat_ts=now - 30.0, cycle=10)
    monkeypatch.setattr(dash, "resolve_db_path", lambda *a, **k: db)
    monkeypatch.setattr(dash, "_read_cycle_intent_rows", lambda *a, **k: [])
    monkeypatch.setattr(dash, "resolve_ring_path",
                        lambda: dash.resolve_runtime_file("no-such-ring.jsonl"))

    payload = json.loads(dash.get_scan_state().body)

    # 1570s of run over 10 rotations: 157s each, 31x the configured 5.0s.
    assert payload["cadence_sec"] == pytest.approx(157.0, abs=1.0)
    assert payload["scan_state"] != "STALLED"
