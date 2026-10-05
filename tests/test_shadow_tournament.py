"""Tests for tournament naming conventions, metadata parsing, and heartbeat dash_port."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core_brain.shadow_run import (
    build_tournament_db_path,
    build_tournament_run_id,
    parse_tournament_db_path,
    write_shadow_heartbeat,
)


def test_build_and_parse_tournament_db_path():
    db_path = build_tournament_db_path(
        issue=371,
        index=1,
        arm="aggressive",
        stamp="20261005-031500",
        base_dir="data",
    )
    assert db_path == Path("data/371_tournament_01_aggressive_20261005-031500.db")

    parsed = parse_tournament_db_path(db_path)
    assert parsed is not None
    assert parsed["issue"] == 371
    assert parsed["index"] == 1
    assert parsed["arm"] == "aggressive"
    assert parsed["stamp"] == "20261005-031500"


def test_parse_tournament_db_path_various_paths():
    # String path with backslashes (Windows)
    win_path = r"C:\Users\Tiger\data\371_tournament_02_conservative_20261005-031500.db"
    parsed_win = parse_tournament_db_path(win_path)
    assert parsed_win == {
        "issue": 371,
        "index": 2,
        "arm": "conservative",
        "stamp": "20261005-031500",
    }

    # Relative path without data folder
    rel_path = "371_tournament_99_control_20261005-031500.db"
    parsed_rel = parse_tournament_db_path(rel_path)
    assert parsed_rel == {
        "issue": 371,
        "index": 99,
        "arm": "control",
        "stamp": "20261005-031500",
    }


def test_parse_tournament_db_path_rejects_non_tournament():
    # Ordinary shadow runs must NOT match
    assert parse_tournament_db_path("data/01_shadow_touchpair.db") is None
    assert parse_tournament_db_path("data/NN_shadow_20261004.db") is None
    assert parse_tournament_db_path("data/orders.db") is None
    assert parse_tournament_db_path("data/shadow.db") is None
    assert parse_tournament_db_path("") is None
    assert parse_tournament_db_path(None) is None


def test_build_tournament_run_id_constraints():
    run_id = build_tournament_run_id(
        issue=371,
        index=1,
        arm="aggressive",
        stamp="20261005-031500",
    )
    assert run_id == "shadow-371-t01-aggressive-20261005-031500"
    assert len(run_id) <= 64
    assert run_id.startswith("shadow-")

    # Long arm name is clamped
    long_arm = "very-long-arm-name-that-exceeds-standard-character-limits"
    clamped_id = build_tournament_run_id(
        issue=371,
        index=12,
        arm=long_arm,
        stamp="20261005-031500",
    )
    assert len(clamped_id) <= 64
    assert clamped_id.startswith("shadow-371-t12-very-long-arm-name-that-")


def test_write_shadow_heartbeat_includes_dash_port(tmp_path: Path):
    hb_file = tmp_path / "shadow_run_test.json"
    written = write_shadow_heartbeat(
        db_path=tmp_path / "test.db",
        run_id="shadow-test-01",
        minutes=5.0,
        interval=5.0,
        started_at=1000.0,
        dash_port=8801,
        path=hb_file,
    )
    assert written is not None
    data = json.loads(hb_file.read_text(encoding="utf-8"))
    assert data["dash_port"] == 8801
    assert data["run_id"] == "shadow-test-01"


def test_write_shadow_heartbeat_omits_dash_port_when_none(tmp_path: Path):
    hb_file = tmp_path / "shadow_run_test_none.json"
    written = write_shadow_heartbeat(
        db_path=tmp_path / "test.db",
        run_id="shadow-test-02",
        minutes=5.0,
        interval=5.0,
        started_at=1000.0,
        dash_port=None,
        path=hb_file,
    )
    assert written is not None
    data = json.loads(hb_file.read_text(encoding="utf-8"))
    assert "dash_port" not in data
