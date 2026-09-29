from __future__ import annotations

import sqlite3
from pathlib import Path

from core_brain.order_registry import CloseRecord, OrderRegistry
from core_brain.statistics_observer import observe


def _seed_db(tmp_path: Path) -> Path:
    db = tmp_path / "observed.db"
    reg = OrderRegistry(db)
    for i in range(2):
        reg.log_close(CloseRecord(i, "c", "m", "shadow_merge", 1, .95, 1, .05, "run-a"))
    return db


def test_observer_samples_own_store_and_finalizes_once(tmp_path: Path, monkeypatch):
    db = _seed_db(tmp_path)
    before = db.read_bytes()
    finalized = []
    monkeypatch.setattr("core_brain.statistics_observer.write_statistics_report", lambda *a, **k: finalized.append(1))
    out = observe(db, "run-a", "shadow", tmp_path / "data", ticks=2, interval=0)
    assert out.count == 2
    assert len(finalized) == 1
    assert db.read_bytes() == before
    with sqlite3.connect(out.stats_path) as con:
        assert con.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 2


def test_observer_stops_on_stop_file(tmp_path: Path, monkeypatch):
    db = _seed_db(tmp_path)

    existing_stop = tmp_path / "existing.stop"
    existing_stop.touch()
    finalized = []
    monkeypatch.setattr("core_brain.statistics_observer.write_statistics_report", lambda *a, **k: finalized.append(1))
    before = db.read_bytes()
    out = observe(db, "run-a", "shadow", tmp_path / "existing-data", ticks=10, interval=0, stop_file=existing_stop)
    assert out.count == 0
    assert len(finalized) == 1
    assert db.read_bytes() == before

    midrun_stop = tmp_path / "midrun.stop"
    midrun_finalized = []
    monkeypatch.setattr("core_brain.statistics_observer.write_statistics_report", lambda *a, **k: midrun_finalized.append(1))
    calls = 0

    def create_stop(_seconds: float):
        nonlocal calls
        calls += 1
        midrun_stop.touch()

    monkeypatch.setattr("core_brain.statistics_observer.time.sleep", create_stop)
    midrun = observe(db, "run-a", "shadow", tmp_path / "midrun-data", ticks=10, interval=1, stop_file=midrun_stop)
    assert midrun.count < 10
    assert calls == 1
    assert len(midrun_finalized) == 1


def test_observer_negative_max_hours_runs_past_the_first_snapshot(tmp_path: Path, monkeypatch):
    # Arrange — a negative cap means "run until stopped", mirroring
    # shadow_run's negative-minutes contract.
    db = _seed_db(tmp_path)
    monkeypatch.setattr("core_brain.statistics_observer.write_statistics_report", lambda *a, **k: None)

    # Act
    out = observe(db, "run-a", "shadow", tmp_path / "data", ticks=2, interval=0, max_hours=-1)

    # Assert — no hourly break after the first snapshot.
    assert out.count == 2
