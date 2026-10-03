"""Release a dead loop's `instance_lock` row, or refuse.

Covers the helper behind shadow resume: a killed loop never runs the lock's
`finally` cleanup, so its `fleet` row survives and the next loop dies with
`InstanceInUse`. The helper deletes the row only for the verified-dead holder.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time

import pytest

from scripts.release_instance_lock import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_REFUSED,
    read_lock,
    release_if_holder,
)


def _seed(db_path, holder="15548:70f39f42", age_ms=20_828) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS instance_lock "
            "(role TEXT PRIMARY KEY, holder TEXT NOT NULL, acquired_ts INTEGER NOT NULL)"
        )
        conn.execute(
            "INSERT OR REPLACE INTO instance_lock (role, holder, acquired_ts) "
            "VALUES (?, ?, ?)",
            ("fleet", holder, int(time.time() * 1000) - age_ms),
        )
        conn.commit()


def test_empty_store_is_a_noop_success(tmp_path):
    db = tmp_path / "shadow.db"
    db.touch()
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE instance_lock "
            "(role TEXT PRIMARY KEY, holder TEXT NOT NULL, acquired_ts INTEGER NOT NULL)"
        )
    released, row = release_if_holder(db, "fleet", 15548)
    assert released is False
    assert row is None


def test_matching_dead_holder_is_released(tmp_path):
    db = tmp_path / "shadow.db"
    _seed(db)
    released, row = release_if_holder(db, "fleet", 15548)
    assert released is True
    assert row is not None
    assert row["holder"] == "15548:70f39f42"
    assert read_lock(db, "fleet") is None


def test_mismatched_holder_is_refused_and_row_survives(tmp_path):
    db = tmp_path / "shadow.db"
    _seed(db)
    released, row = release_if_holder(db, "fleet", 99999)
    assert released is False
    kept = read_lock(db, "fleet")
    assert kept is not None
    assert kept["holder"] == "15548:70f39f42"


def test_read_lock_reports_age(tmp_path):
    db = tmp_path / "shadow.db"
    _seed(db, age_ms=20_828)
    row = read_lock(db, "fleet")
    assert row is not None
    assert row["age_ms"] == pytest.approx(20_828, abs=5_000)


def test_production_registry_is_refused(tmp_path):
    db = tmp_path / "orders.db"
    _seed(db)
    with pytest.raises(SystemExit) as exc:
        release_if_holder(db, "fleet", 15548)
    assert exc.value.code == EXIT_REFUSED
    with sqlite3.connect(db) as conn:
        kept = conn.execute(
            "SELECT holder FROM instance_lock WHERE role = 'fleet'").fetchone()
    assert kept[0] == "15548:70f39f42"


def test_mismatched_holder_cli_refuses_but_library_reports(tmp_path):
    db = tmp_path / "shadow.db"
    _seed(db)
    released, row = release_if_holder(db, "fleet", 99999)
    assert released is False
    assert row is not None


def test_missing_file_is_an_error(tmp_path):
    with pytest.raises(SystemExit) as exc:
        release_if_holder(tmp_path / "nope.db", "fleet", 15548)
    assert exc.value.code == EXIT_ERROR


def _cli(db_path, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "scripts.release_instance_lock",
         "--db", str(db_path), *args],
        capture_output=True, text=True,
    )


def test_cli_releases_and_prints_json(tmp_path):
    db = tmp_path / "shadow.db"
    _seed(db)
    proc = _cli(db, "--role", "fleet", "--holder-pid", "15548")
    assert proc.returncode == EXIT_OK
    assert json.loads(proc.stdout)["released"] is True


def test_cli_show_reports_row_without_touching(tmp_path):
    db = tmp_path / "shadow.db"
    _seed(db)
    proc = _cli(db, "--role", "fleet")
    assert proc.returncode == EXIT_OK
    assert json.loads(proc.stdout)["holder"] == "15548:70f39f42"
    assert read_lock(db, "fleet") is not None


def test_cli_refuses_mismatch_with_exit_two(tmp_path):
    db = tmp_path / "shadow.db"
    _seed(db)
    proc = _cli(db, "--role", "fleet", "--holder-pid", "99999")
    assert proc.returncode == EXIT_REFUSED
    assert json.loads(proc.stdout)["released"] is False
