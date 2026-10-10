"""The dashboard can be pointed at a store nothing is writing.

`read_shadow_run` answers "which run is writing THIS store" and drops every
other heartbeat. That is right for the stopwatch and wrong for the operator
standing in front of the page: a 4-hour trial writes 1,250 heartbeat files'
worth of history to its own store, the dashboard is aimed at a store whose run
died an hour ago, and every pill agrees the engine is down. Only the operator
knows a healthy run is writing another file.

Two things close the gap without widening what the page counts:

  * `list_shadow_runs` names the recent runs on this machine -- including the
    ones writing a different store -- so the badge can say "not this store, but
    that one is live" instead of leaving the operator to read JSON by hand.
  * `POST /api/system/db` re-points the page at a run's store, so watching a
    stale one is a choice rather than an accident of launch order.

Nothing here makes another store's numbers appear on this page: a run that is
not the active store is named and counted, never read (see
`tests/test_per_run_shadow_heartbeat.py`, which pins the db-scoped rule).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core_brain.order_registry import SCHEMA
from dashboard import server as srv
from dashboard.server import app, set_db_override

def _clock() -> float:
    """The real clock, read at the moment of the call.

    Heartbeat ages here are relative to whatever the server reads, and
    `/api/system/status` uses `time.time()`. A timestamp captured at import
    drifts by however long the rest of the suite takes to run, and the drift
    is not cosmetic: the `ended` verdict ends a run whose process is gone once
    its heartbeat is over 15s old, so a stale-by-25-seconds fixture flips a
    healthy run to ended partway through a full-suite run and passes alone.
    """
    return time.time()


def _make_db(tmp_path: Path, name: str) -> Path:
    import sqlite3

    db = tmp_path / name
    con = sqlite3.connect(str(db))
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    return db


def _wire_runtime(tmp_path, monkeypatch) -> Path:
    """A fake runtime dir wired into the reader's resolver."""
    runtime = tmp_path / "runtime"
    runtime.mkdir(exist_ok=True)
    monkeypatch.setattr(srv, "resolve_runtime_file",
                        lambda name, root=None: runtime / name)
    monkeypatch.setattr(srv, "_is_temp_or_test_db", lambda path: False)
    return runtime


def _hb(runtime: Path, name: str, db: Path, run_id: str, *,
        age: float = 2.0, finished: bool = False, pid: int = 4242) -> None:
    """Write one rehearsal heartbeat file whose heartbeat is `age` seconds old."""
    path = runtime / name
    now = _clock()
    path.write_text(json.dumps({
        "pid": pid,
        "run_id": run_id,
        "started_at": now - 600.0,
        "heartbeat_ts": now - age,
        "interval": 5.0,
        "minutes": 240.0,
        "db_path": str(db),
        "cycle": 353,
        "finished": finished,
    }), encoding="utf-8")
    _touch(path, age)


def _touch(path: Path, age: float) -> None:
    """Backdate the file's mtime to match its heartbeat age."""
    import os

    stamp = _clock() - age
    os.utime(path, (stamp, stamp))


@pytest.fixture
def client():
    yield TestClient(app)


@pytest.fixture(autouse=True)
def _clean_override():
    yield
    set_db_override(None)


# ── list_shadow_runs: every recent run, not just this store's ─────────────

def test_a_run_on_another_store_is_named_and_marked_not_active(tmp_path, monkeypatch):
    # Arrange -- the exact shape that read ENGINE DOWN over a healthy machine:
    # the pointed store's run died, another store's run is mid-rotation.
    runtime = _wire_runtime(tmp_path, monkeypatch)
    here = _make_db(tmp_path, "01_shadow_12-09_00-58.db")
    there = _make_db(tmp_path, "NN_shadow_ladder_333_mine.db")
    _hb(runtime, "shadow_run_shadow-01.json", here, "shadow-01", age=4800.0)
    _hb(runtime, "shadow_run_ladder-live.json", there, "ladder-live", age=2.0)

    # Act
    runs = srv.list_shadow_runs(str(here), now=_clock())

    # Assert -- both are named; only one belongs to this page's store.
    by_id = {r["run_id"]: r for r in runs}
    assert set(by_id) == {"shadow-01", "ladder-live"}
    assert by_id["ladder-live"]["running"] is True
    assert by_id["ladder-live"]["is_active_db"] is False
    assert by_id["ladder-live"]["db_path"] == str(there)
    assert by_id["shadow-01"]["running"] is False
    assert by_id["shadow-01"]["is_active_db"] is True


def test_a_live_run_sorts_ahead_of_a_dead_one(tmp_path, monkeypatch):
    runtime = _wire_runtime(tmp_path, monkeypatch)
    db = _make_db(tmp_path, "01_shadow.db")
    _hb(runtime, "shadow_run_aaa-dead.json", db, "aaa-dead", age=3600.0)
    _hb(runtime, "shadow_run_zzz-live.json", db, "zzz-live", age=1.0)

    runs = srv.list_shadow_runs(str(db), now=_clock())

    # Alphabetical file order would show the dead one first; the operator needs
    # the running one at the top of the switcher.
    assert [r["run_id"] for r in runs][:2] == ["zzz-live", "aaa-dead"]


def test_a_run_that_stopped_writing_hours_ago_is_not_offered(tmp_path, monkeypatch):
    # 1,250 heartbeat files accumulate on a busy machine. A run that has not
    # written for six hours is dead (the stale verdict lands at 120s), so the
    # switcher must not fill with them.
    runtime = _wire_runtime(tmp_path, monkeypatch)
    db = _make_db(tmp_path, "01_shadow.db")
    _hb(runtime, "shadow_run_ancient.json", db, "ancient", age=60 * 60 * 8.0)

    assert srv.list_shadow_runs(str(db), now=_clock()) == []


def test_a_heartbeat_with_no_database_is_still_listed(tmp_path, monkeypatch):
    # A malformed file is information, not a reason for the listing to fail.
    runtime = _wire_runtime(tmp_path, monkeypatch)
    db = _make_db(tmp_path, "01_shadow.db")
    now = _clock()
    (runtime / "shadow_run_orphan.json").write_text(json.dumps({
        "pid": 1, "run_id": "orphan", "started_at": now - 10.0,
        "heartbeat_ts": now - 2.0, "interval": 5.0, "cycle": 2,
    }), encoding="utf-8")
    _touch(runtime / "shadow_run_orphan.json", 2.0)

    runs = srv.list_shadow_runs(str(db), now=_clock())

    assert [r["run_id"] for r in runs] == ["orphan"]
    assert runs[0]["is_active_db"] is False
    assert runs[0]["db_path"] is None


def test_a_malformed_heartbeat_file_is_skipped(tmp_path, monkeypatch):
    runtime = _wire_runtime(tmp_path, monkeypatch)
    db = _make_db(tmp_path, "01_shadow.db")
    (runtime / "shadow_run_broken.json").write_text("{not json", encoding="utf-8")
    _hb(runtime, "shadow_run_ok.json", db, "ok", age=2.0)

    runs = srv.list_shadow_runs(str(db), now=_clock())

    assert [r["run_id"] for r in runs] == ["ok"]


# ── /api/system/status carries the listing ────────────────────────────────

def test_status_carries_the_run_list(client, tmp_path, monkeypatch):
    runtime = _wire_runtime(tmp_path, monkeypatch)
    here = _make_db(tmp_path, "01_shadow.db")
    there = _make_db(tmp_path, "NN_shadow_ladder_333_mine.db")
    _hb(runtime, "shadow_run_shadow-01.json", here, "shadow-01", age=4800.0)
    _hb(runtime, "shadow_run_ladder-live.json", there, "ladder-live", age=2.0)
    set_db_override(here)

    payload = client.get("/api/system/status").json()

    runs = payload.get("shadow_runs")
    assert isinstance(runs, list), "the status endpoint must name the other runs"
    assert {r["run_id"] for r in runs} == {"shadow-01", "ladder-live"}


# ── POST /api/system/db: the run switcher ─────────────────────────────────

def _token() -> dict:
    import dashboard.server as dash_mod

    return {"X-Control-Token": dash_mod.CONTROL_TOKEN}


def test_switching_re_points_the_page_at_the_live_store(client, tmp_path, monkeypatch):
    runtime = _wire_runtime(tmp_path, monkeypatch)
    stale = _make_db(tmp_path, "01_shadow_12-09_00-58.db")
    live = _make_db(tmp_path, "NN_shadow_ladder_333_mine.db")
    _hb(runtime, "shadow_run_shadow-01.json", stale, "shadow-01", age=4800.0)
    _hb(runtime, "shadow_run_ladder-live.json", live, "ladder-live", age=2.0)
    set_db_override(stale)

    res = client.post("/api/system/db", params={"db": str(live)}, headers=_token())

    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert Path(body["status"]["db_path"]).resolve() == live.resolve()
    # And the stopwatch follows: the page is now watching a run that is alive.
    assert body["status"]["shadow_run"]["run_id"] == "ladder-live"
    assert body["status"]["shadow_run"]["running"] is True


def test_switching_without_the_control_token_is_refused(client, tmp_path):
    live = _make_db(tmp_path, "NN_shadow_ladder_333_mine.db")
    set_db_override(_make_db(tmp_path, "01_shadow.db"))

    res = client.post("/api/system/db", params={"db": str(live)})

    assert res.status_code == 403


def test_switching_to_a_file_that_is_not_there_is_refused(client, tmp_path):
    set_db_override(_make_db(tmp_path, "01_shadow.db"))

    res = client.post("/api/system/db", params={"db": str(tmp_path / "nope.db")},
                      headers=_token())

    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert "nope.db" in body["message"]
    # The refusal must not have moved the page.
    assert Path(body["status"]["db_path"]).name == "01_shadow.db"


# ── Temp / test DB exclusion: keep transient artifacts out of scope ───────

def test_is_temp_or_test_db_detection():
    import tempfile
    temp_dir = tempfile.gettempdir()
    assert srv._is_temp_or_test_db(f"{temp_dir}\\test.db") is True
    assert srv._is_temp_or_test_db(f"{temp_dir}/pytest-123/shadow.db") is True
    assert srv._is_temp_or_test_db("C:\\Users\\Tiger\\AppData\\Local\\Temp\\pytest-of-Tiger\\pytest-2504\\test0\\shadow.db") is True
    assert srv._is_temp_or_test_db("/tmp/shadow.db") is True
    assert srv._is_temp_or_test_db("C:\\Users\\Tiger\\Agents\\Projects\\spread-hunter\\data\\orders.db") is False
    assert srv._is_temp_or_test_db("data/NN_shadow_123.db") is False
    assert srv._is_temp_or_test_db(None) is False


def test_temp_and_pytest_dbs_are_excluded_from_shadow_run_list(tmp_path, monkeypatch):
    import tempfile
    runtime = tmp_path / "runtime"
    runtime.mkdir(exist_ok=True)
    monkeypatch.setattr(srv, "resolve_runtime_file",
                        lambda name, root=None: runtime / name)
    # Testing server's real _is_temp_or_test_db, NOT mocked
    normal_db = srv.LIVE_ROOT / "data" / "01_shadow_real.db"
    temp_db = Path(tempfile.gettempdir()) / "pytest-999" / "shadow.db"

    _hb(runtime, "shadow_run_temp.json", temp_db, "temp-run", age=2.0)
    _hb(runtime, "shadow_run_normal.json", normal_db, "normal-run", age=2.0)

    runs = srv.list_shadow_runs(now=_clock())
    run_ids = [r["run_id"] for r in runs]
    assert "normal-run" in run_ids
    assert "temp-run" not in run_ids


def test_temp_and_pytest_dbs_are_excluded_from_other_live_runs(tmp_path, monkeypatch):
    import tempfile
    runtime = tmp_path / "runtime"
    runtime.mkdir(exist_ok=True)
    monkeypatch.setattr(srv, "resolve_runtime_file",
                        lambda name, root=None: runtime / name)
    # Testing server's real _is_temp_or_test_db, NOT mocked
    normal_db = srv.LIVE_ROOT / "data" / "01_shadow_real.db"
    temp_db = Path(tempfile.gettempdir()) / "pytest-999" / "shadow.db"

    _hb(runtime, "shadow_run_temp.json", temp_db, "temp-run", age=2.0)
    _hb(runtime, "shadow_run_normal.json", normal_db, "normal-run", age=2.0)

    other = srv.read_other_live_shadow_runs(active_db_path="data/orders.db", now=_clock())
    run_ids = [r["run_id"] for r in other]
    assert "normal-run" in run_ids
    assert "temp-run" not in run_ids


def test_switching_to_temp_db_is_refused(client):
    import tempfile
    temp_db = str(Path(tempfile.gettempdir()) / "test.db")
    res = client.post("/api/system/db", params={"db": temp_db}, headers=_token())
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert "temporary or test directories are out of scope" in body["message"]
