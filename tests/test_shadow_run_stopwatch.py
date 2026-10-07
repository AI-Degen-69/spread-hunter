"""Shadow-run stopwatch beside the SHADOW pill (#32).

A shadow run is started from its own terminal and never lands in
`runtime/processes.json`, so the dashboard used to have no way of knowing
whether the rehearsal it displays started thirty seconds ago or ended twenty
minutes ago. The run now publishes its own heartbeat and the status payload
surfaces it -- but only for the store the page is actually reading.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from core_brain.shadow_run import write_shadow_heartbeat
from dashboard import server as srv

STARTED_AT = 1_788_000_000.0


def _test_cfg():
    from core_brain.config import MakerConfig
    return MakerConfig()

_STATIC = Path(__file__).resolve().parent.parent / "dashboard" / "static"
_CLOCK_HARNESS = Path(__file__).resolve().parent / "js" / "filter_uptime_harness.cjs"


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


# ── The code stamp (#403) ───────────────────────────────────────────────────
#
# A rehearsal outlives the code it started with, and it used to say nothing
# about it. Three runs sat side by side on 2026-10-07, two of them deciding with
# the previous morning's ranker and no queue gate, every one of them reading
# identically on the page and in the log.


def _revision(**overrides) -> dict:
    record = {
        "commit": "80d03f1",
        "dirty": True,
        "label": "80d03f1+dirty",
        "code_mtime": STARTED_AT - 60.0,
        "captured_at": STARTED_AT,
    }
    record.update(overrides)
    return record


def test_a_heartbeat_can_name_the_code_the_process_loaded(tmp_path):
    target = tmp_path / "runtime" / "shadow_run.json"

    write_shadow_heartbeat(db_path=tmp_path / "shadow.db", run_id="shadow-05",
                           minutes=1440.0, interval=5.0, started_at=STARTED_AT,
                           code_revision=_revision(), path=target)

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["code_revision"]["label"] == "80d03f1+dirty"


def test_a_heartbeat_without_a_revision_carries_no_stamp(tmp_path):
    # Backward compatible: a caller that has no revision to publish writes
    # exactly what it wrote before, so every older reader keeps working.
    target = tmp_path / "runtime" / "shadow_run.json"

    write_shadow_heartbeat(db_path=tmp_path / "shadow.db", run_id="shadow-05",
                           minutes=1440.0, interval=5.0, started_at=STARTED_AT,
                           path=target)

    assert "code_revision" not in json.loads(target.read_text(encoding="utf-8"))


def test_run_shadow_publishes_the_revision_on_every_write(tmp_path, monkeypatch):
    """The stamp is on the first, per-rotation and final heartbeat alike."""
    from core_brain import shadow_run as sr
    from core_brain import trader_loop

    target = tmp_path / "runtime" / "shadow_run.json"
    monkeypatch.setattr(sr, "shadow_heartbeat_path", lambda root=None, run_id="": target)
    monkeypatch.setattr(trader_loop, "run", lambda seam, **kw: (kw["sleep_fn"](0.0), [])[1])
    monkeypatch.setattr(sr, "build_shadow_seam", lambda **kw: type("Seam", (), {})())

    sr.run_shadow(minutes=0.0, db_path=tmp_path / "shadow.db",
                  markets_fn=lambda: [], client_fn=lambda: None,
                  decide_fn=lambda *a, **k: [], fetch_books=lambda *a, **k: {},
                  cfg=_test_cfg(), run_id="shadow-05", sleep_fn=lambda s: None,
                  code_revision=_revision())

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["finished"] is True
    assert payload["code_revision"]["label"] == "80d03f1+dirty"


def test_main_reads_the_revision_once_and_names_it_on_the_banner(
        tmp_path, monkeypatch, caplog):
    """Read once, at start: a mid-run edit must not rewrite what the run claims.

    Re-reading per heartbeat would let an edit land in the record of a process
    that never loaded it -- the exact silence this stamp exists to break.
    """
    import logging

    from core_brain import shadow_run as sr

    reads: list[int] = []
    monkeypatch.setattr(sr, "read_code_revision",
                        lambda *a, **k: (reads.append(1), _revision())[1])
    target = tmp_path / "runtime" / "shadow_run.json"
    monkeypatch.setattr(sr, "shadow_heartbeat_path", lambda root=None, run_id="": target)

    with caplog.at_level(logging.INFO, logger="shadow_run"):
        sr.main(
            ["--minutes", "0", "--db", str(tmp_path / "shadow.db"),
             "--run-id", "shadow-05"],
            markets_fn=lambda max_markets=None: [],
            client_fn=lambda: object(),
            decide_fn=lambda cfg, up, dn, inv, t_rem, wf: ([], "declined"),
            fetch_books=lambda clob_host, token: {},
        )

    assert len(reads) == 1
    assert "code=80d03f1+dirty" in caplog.text
    assert json.loads(target.read_text(encoding="utf-8"))["code_revision"]["label"] \
        == "80d03f1+dirty"


def test_the_dashboard_surfaces_the_recorded_revision(tmp_path, monkeypatch):
    payload = _heartbeat(tmp_path, monkeypatch, code_revision=_revision())
    # The tree's newest decision file is the one this run recorded having read:
    # nothing has changed under it.
    monkeypatch.setattr(srv.code_revision, "decision_code_mtime",
                        lambda root=None: STARTED_AT - 60.0)

    run = srv.read_shadow_run(payload["db_path"], now=STARTED_AT + 62.0)

    assert run["code_revision"]["label"] == "80d03f1+dirty"
    assert run["code_stale"] is False


def test_the_dashboard_flags_a_run_whose_code_the_tree_moved_past(
        tmp_path, monkeypatch):
    payload = _heartbeat(tmp_path, monkeypatch, code_revision=_revision())
    # The tree changed after this run read its code -- today's ranker edit, or
    # the queue-gate merge landing under a running rehearsal.
    monkeypatch.setattr(srv.code_revision, "decision_code_mtime",
                        lambda root=None: STARTED_AT + 7200.0)

    run = srv.read_shadow_run(payload["db_path"], now=STARTED_AT + 62.0)

    assert run["code_stale"] is True
    assert run["running"] is True, "a stale rehearsal is still a running one"


def test_a_heartbeat_written_before_the_stamp_existed_is_still_judged(
        tmp_path, monkeypatch):
    """The runs already on the machine when this ships.

    No recorded revision, but a process start time older than the tree's newest
    decision file is proof enough: a process cannot hold code newer than the
    process. This is what makes the two stale rehearsals from 2026-10-07
    visible without restarting them.
    """
    payload = _heartbeat(tmp_path, monkeypatch,
                         process_started_at=STARTED_AT + 3.0)
    monkeypatch.setattr(srv.code_revision, "decision_code_mtime",
                        lambda root=None: STARTED_AT + 7200.0)

    run = srv.read_shadow_run(payload["db_path"], now=STARTED_AT + 62.0)

    assert run["code_revision"] is None
    assert run["code_stale"] is True


def test_a_heartbeat_with_no_evidence_is_not_flagged(tmp_path, monkeypatch):
    # No clock on the tree at all (or no start time): an unknown is not a
    # verdict, and a warning nobody can act on is worse than silence.
    payload = _heartbeat(tmp_path, monkeypatch)
    monkeypatch.setattr(srv.code_revision, "decision_code_mtime", lambda root=None: None)

    run = srv.read_shadow_run(payload["db_path"], now=STARTED_AT + 62.0)

    assert run["code_stale"] is False


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed on this host")
def test_the_pill_shows_the_code_and_marks_it_when_the_tree_moved_on():
    payloads = [
        # 1. holding what is on disk
        {"shadow_run": {"running": True, "run_id": "shadow-05", "minutes": 1440,
                         "elapsed_sec": 104, "code_revision": _revision(),
                         "code_stale": False}},
        # 2. the tree moved past it: the call to action
        {"shadow_run": {"running": True, "run_id": "shadow-04-prudent", "minutes": 1440,
                         "elapsed_sec": 104, "code_revision": _revision(label="1f2e3d4"),
                         "code_stale": True}},
        # 3. a run recorded nothing: no stamp, no noise
        {"shadow_run": {"running": True, "run_id": "shadow-old", "minutes": 1440,
                         "elapsed_sec": 104}},
    ]

    result = subprocess.run(
        [shutil.which("node"), str(_CLOCK_HARNESS), json.dumps(payloads)],
        capture_output=True, text=True, check=True, encoding="utf-8",
    )
    out = json.loads(result.stdout)

    assert out["shadow_texts"][0] == "· 01:44 · code 80d03f1+dirty"
    assert out["shadow_texts"][1] == "· 01:44 · code 1f2e3d4 (older than this tree)"
    assert out["shadow_texts"][2] == "· 01:44"
    assert "no longer here" in out["shadow_titles"][1]
    assert "no decision code has changed" in out["shadow_titles"][0]
    # One sentence, not two run together: "...time box 1440 min It loaded..."
    # was the first draft and read as a typo.
    assert "1440 min. It loaded" in out["shadow_titles"][1]
    # And with no stamp, the title is byte-for-byte what it was before.
    assert out["shadow_titles"][2].endswith("time box 1440 min")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed on this host")
def test_dashboard_labels_unlimited_shadow_runs_without_a_time_box():
    payloads = [{"shadow_run": {
        "running": True,
        "run_id": "shadow-unlimited",
        "minutes": -1,
        "elapsed_sec": 3600,
    }}]

    result = subprocess.run(
        [shutil.which("node"), str(_CLOCK_HARNESS), json.dumps(payloads)],
        capture_output=True, text=True, check=True, encoding="utf-8",
    )

    assert json.loads(result.stdout)["shadow_titles"] == [
        "Shadow rehearsal shadow-unlimited running, no time box (runs until stopped)"
    ]
