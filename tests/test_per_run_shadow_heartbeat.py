"""Concurrent rehearsals get their own heartbeat files.

Two shadow runs writing at once (a menu stack and a hand-launched rehearsal)
used to share one `runtime/shadow_run.json`. The last writer won, and the
dashboard's stopwatch surfacing keys on `db_path` -- so a page pointed at
either store flickered to "not running" whenever the OTHER run refreshed the
shared heartbeat. The same fix the per-run cycle ring got
(`runtime/shadow-<run_id>.jsonl`, tests/test_per_run_shadow_ring.py) applied
to the liveness file: each run writes `runtime/shadow_run_<run_id>.json`, and
the reader scans the per-run names with the legacy shared file kept as a
fallback for heartbeats written by older code.
"""
from __future__ import annotations

import json
from pathlib import Path

import core_brain.runtime_paths as runtime_paths
from core_brain.shadow_run import write_shadow_heartbeat
from dashboard import server as srv

STARTED_AT = 1_788_000_000.0


def _wire_runtime(tmp_path, monkeypatch) -> Path:
    """A fake runtime dir wired into both the writer's and reader's resolvers."""
    runtime = tmp_path / "runtime"
    runtime.mkdir(exist_ok=True)
    monkeypatch.setattr(runtime_paths, "runtime_file",
                        lambda name, root=None: runtime / name)
    monkeypatch.setattr(srv, "resolve_runtime_file",
                        lambda name, root=None: runtime / name)
    return runtime


def _hb(runtime: Path, name: str, db: Path, run_id: str) -> None:
    (runtime / name).write_text(json.dumps({
        "pid": 4242,
        "run_id": run_id,
        "started_at": STARTED_AT,
        "minutes": 60.0,
        "interval": 5.0,
        "db_path": str(db),
        "heartbeat_ts": STARTED_AT + 60.0,
        "finished": False,
    }), encoding="utf-8")


def test_writer_names_the_heartbeat_file_for_the_run(tmp_path, monkeypatch):
    # Arrange / Act
    runtime = _wire_runtime(tmp_path, monkeypatch)
    db = tmp_path / "01_shadow_12-09_00-58.db"
    written = write_shadow_heartbeat(db_path=db, run_id="shadow-01",
                                     minutes=60.0, interval=5.0,
                                     started_at=STARTED_AT)
    payload = json.loads(written.read_text(encoding="utf-8"))

    # Assert
    assert written == runtime / "shadow_run_shadow-01.json"
    assert payload["run_id"] == "shadow-01"
    assert payload["db_path"] == str(db.resolve())


def test_writing_does_not_touch_the_legacy_shared_file(tmp_path, monkeypatch):
    # Two runs at once must not resurrect the single-slot collision.
    runtime = _wire_runtime(tmp_path, monkeypatch)

    write_shadow_heartbeat(db_path=tmp_path / "x.db", run_id="shadow-03",
                           minutes=60.0, interval=5.0, started_at=STARTED_AT)

    assert not (runtime / "shadow_run.json").exists()


def test_a_hostile_run_id_cannot_climb_out_of_runtime(tmp_path, monkeypatch):
    # The id is not ours to trust: it can carry separators from SH_RUN_ID.
    runtime = _wire_runtime(tmp_path, monkeypatch)

    written = write_shadow_heartbeat(db_path=tmp_path / "x.db", run_id="../evil",
                                     minutes=1.0, interval=5.0,
                                     started_at=STARTED_AT)

    assert written.parent == runtime
    assert written.name.startswith("shadow_run_")
    assert ".." not in written.name


def test_concurrent_heartbeats_do_not_blind_each_other(tmp_path, monkeypatch):
    # Arrange -- two rehearsals writing at once, one file each. This is the
    # exact shape that used to flicker: whichever run refreshed the single
    # shared file last blinded the dashboard reading the other store.
    runtime = _wire_runtime(tmp_path, monkeypatch)
    db_a = tmp_path / "01_shadow_a.db"
    db_b = tmp_path / "03_shadow_b.db"
    _hb(runtime, "shadow_run_shadow-01.json", db_a, "shadow-01")
    _hb(runtime, "shadow_run_shadow-03.json", db_b, "shadow-03")
    now = STARTED_AT + 62.0

    # Act
    a = srv.read_shadow_run(str(db_a), now=now)
    b = srv.read_shadow_run(str(db_b), now=now)

    # Assert -- each page sees ITS run, both running.
    assert a is not None and a["run_id"] == "shadow-01" and a["running"] is True
    assert b is not None and b["run_id"] == "shadow-03" and b["running"] is True


def test_a_legacy_shared_heartbeat_still_surfaces(tmp_path, monkeypatch):
    # A heartbeat written by older code (single shared shadow_run.json) must
    # not vanish from the dashboard after this change: read fallback.
    runtime = _wire_runtime(tmp_path, monkeypatch)
    db = tmp_path / "01_shadow_old.db"
    _hb(runtime, "shadow_run.json", db, "shadow-01")

    run = srv.read_shadow_run(str(db), now=STARTED_AT + 62.0)

    assert run is not None and run["running"] is True


def test_a_heartbeat_for_another_store_is_still_not_surfaced(tmp_path, monkeypatch):
    # The per-run scan widens what is read, not what is shown: a mismatched
    # db_path stays invisible to the page.
    runtime = _wire_runtime(tmp_path, monkeypatch)
    _hb(runtime, "shadow_run_shadow-03.json", tmp_path / "03_shadow_b.db", "shadow-03")

    assert srv.read_shadow_run(str(tmp_path / "01_shadow_a.db")) is None
