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


def test_build_tournament_plan_defaults(tmp_path: Path):
    from scripts.shadow_tournament import build_tournament_plan

    plan = build_tournament_plan(
        issue=371,
        base_port=8801,
        base_dir=tmp_path,
        stamp="20261005-032000",
        check_ports=False,
    )
    assert plan.issue == 371
    assert plan.stamp == "20261005-032000"
    assert len(plan.arms) == 5
    # Default order: control, conservative, balanced, aggressive, prudent
    arm_names = [a.name for a in plan.arms]
    assert arm_names == ["control", "conservative", "balanced", "aggressive", "prudent"]

    # Check first arm details
    first = plan.arms[0]
    assert first.index == 1
    assert first.dash_port == 8801
    assert first.db_path.name.startswith("371_tournament_01_")
    assert first.run_id.startswith("shadow-371-t01-")
    assert "--db" in first.shadow_argv
    assert str(first.db_path) in first.shadow_argv
    assert "--run-id" in first.shadow_argv
    assert first.run_id in first.shadow_argv


def test_build_tournament_plan_rejects_invalid_arm_name():
    from scripts.shadow_tournament import build_tournament_plan

    invalid_arms = [{"name": "Bad_Arm_Name!"}]
    with pytest.raises(ValueError, match="Invalid arm name"):
        build_tournament_plan(issue=371, arms=invalid_arms, check_ports=False)


def test_build_tournament_plan_rejects_duplicate_arm_names():
    from scripts.shadow_tournament import build_tournament_plan

    dup_arms = [
        {"name": "arm-alpha"},
        {"name": "arm-alpha"},
    ]
    with pytest.raises(ValueError, match="Duplicate arm name"):
        build_tournament_plan(issue=371, arms=dup_arms, check_ports=False)


def test_build_tournament_plan_rejects_non_hunter_env():
    from scripts.shadow_tournament import build_tournament_plan

    bad_env_arms = [
        {"name": "arm-one", "env": {"NOT_HUNTER": "123"}},
    ]
    with pytest.raises(ValueError, match="only HUNTER_.*overrides are permitted"):
        build_tournament_plan(issue=371, arms=bad_env_arms, check_ports=False)


def test_build_tournament_plan_rejects_live_port_8799():
    from scripts.shadow_tournament import build_tournament_plan

    with pytest.raises(ValueError, match="8799"):
        build_tournament_plan(issue=371, base_port=8799, check_ports=False)


def test_build_tournament_plan_rejects_existing_db(tmp_path: Path):
    from scripts.shadow_tournament import build_tournament_plan

    existing_db = tmp_path / "371_tournament_01_arm-one_20261005-032000.db"
    existing_db.write_text("existing content", encoding="utf-8")

    arms = [{"name": "arm-one"}]
    with pytest.raises(ValueError, match="already exists"):
        build_tournament_plan(
            issue=371,
            arms=arms,
            base_dir=tmp_path,
            stamp="20261005-032000",
            check_ports=False,
        )


def test_build_tournament_plan_rejects_occupied_port(monkeypatch):
    from scripts.shadow_tournament import build_tournament_plan

    def fake_is_port_available(port, host="127.0.0.1"):
        return False

    monkeypatch.setattr("scripts.shadow_tournament.is_port_available", fake_is_port_available)

    with pytest.raises(ValueError, match="in use or unavailable"):
        build_tournament_plan(issue=371, base_port=8801, check_ports=True)


def test_dry_run_cli_output(capsys, tmp_path: Path):
    from scripts.shadow_tournament import main

    rc = main([
        "--dry-run",
        "--issue", "371",
        "--base-dir", str(tmp_path),
        "--stamp", "20261005-032000",
        "--no-port-check",
    ])
    assert rc == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["issue"] == 371
    assert data["stamp"] == "20261005-032000"
    assert len(data["arms"]) == 5


def test_default_arms_set_preset_selector(tmp_path: Path):
    from scripts.shadow_tournament import build_tournament_plan

    plan = build_tournament_plan(
        issue=371,
        base_port=8801,
        base_dir=tmp_path,
        stamp="20261005-032000",
        check_ports=False,
    )
    for arm in plan.arms:
        assert arm.env["HUNTER_TOURNAMENT_PRESET"] == arm.name


def test_default_arm_offsets_agree_with_presets(tmp_path: Path):
    from core_brain.config import TOURNAMENT_PRESETS
    from scripts.shadow_tournament import build_tournament_plan

    plan = build_tournament_plan(
        issue=371,
        base_port=8801,
        base_dir=tmp_path,
        stamp="20261005-032000",
        check_ports=False,
    )
    for arm in plan.arms:
        preset = TOURNAMENT_PRESETS[arm.name]
        assert arm.env["HUNTER_DYNAMIC_OFFSET"] == ("1" if preset["dynamic_offset_enabled"] else "0")
        if "dynamic_offset_multiplier" in preset:
            assert float(arm.env["HUNTER_DYNAMIC_OFFSET_MULT"]) == preset["dynamic_offset_multiplier"]
        if "dynamic_offset_min_cents" in preset:
            assert int(arm.env["HUNTER_DYNAMIC_OFFSET_MIN_CENTS"]) == preset["dynamic_offset_min_cents"]
        if "dynamic_offset_max_cents" in preset:
            assert int(arm.env["HUNTER_DYNAMIC_OFFSET_MAX_CENTS"]) == preset["dynamic_offset_max_cents"]
    by_name = {a.name: a for a in plan.arms}
    assert float(by_name["prudent"].env["HUNTER_DYNAMIC_OFFSET_MULT"]) == 0.60
    assert int(by_name["prudent"].env["HUNTER_DYNAMIC_OFFSET_MAX_CENTS"]) == 4
    assert by_name["control"].env["HUNTER_DYNAMIC_OFFSET"] == "0"


def test_default_arm_ports_and_paths(tmp_path: Path):
    from scripts.shadow_tournament import build_tournament_plan

    plan = build_tournament_plan(
        issue=371,
        base_port=8801,
        base_dir=tmp_path,
        stamp="20261005-032000",
        check_ports=False,
    )
    assert [a.dash_port for a in plan.arms] == [8801, 8802, 8803, 8804, 8805]
    db_names = [a.db_path.name for a in plan.arms]
    assert len(set(db_names)) == 5
    run_ids = [a.run_id for a in plan.arms]
    assert len(set(run_ids)) == 5
    for arm in plan.arms:
        assert f"_tournament_{arm.index:02d}_{arm.name}_" in arm.db_path.name


def _seed_arm_db(path, run_id, fills, closes):
    import sqlite3

    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE fills (trade_id TEXT PRIMARY KEY, order_uuid TEXT,"
            " size REAL, price REAL, run_id TEXT)"
        )
        conn.execute(
            "CREATE TABLE closes (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " ts REAL, method TEXT, realized_pnl REAL, run_id TEXT)"
        )
        for i, f in enumerate(fills):
            conn.execute(
                "INSERT INTO fills (trade_id, order_uuid, size, price, run_id)"
                " VALUES (?,?,?,?,?)",
                (f"t-{run_id}-{i}", f["order_uuid"], f.get("size", 1.0),
                 f.get("price", 0.5), f.get("run_id", run_id)),
            )
        for c in closes:
            conn.execute(
                "INSERT INTO closes (ts, method, realized_pnl, run_id)"
                " VALUES (?,?,?,?)",
                (1.0, c["method"], c.get("realized_pnl", 0.0), c.get("run_id", run_id)),
            )
        conn.commit()
    finally:
        conn.close()


def test_read_arm_results_filters_by_run_id(tmp_path: Path):
    from scripts.shadow_tournament import read_arm_results

    db = tmp_path / "arm.db"
    _seed_arm_db(db, "run-A",
                 fills=[{"order_uuid": "o1"}, {"order_uuid": "o2", "run_id": "run-B"}],
                 closes=[{"method": "shadow_merge", "realized_pnl": 0.05}])
    res = read_arm_results(db, "run-A")
    assert res["status"] == "ok"
    assert res["fill_events"] == 1
    assert res["filled_orders"] == 1


def test_read_arm_results_partial_fills_count_once(tmp_path: Path):
    from scripts.shadow_tournament import read_arm_results

    db = tmp_path / "arm.db"
    _seed_arm_db(db, "run-A",
                 fills=[{"order_uuid": "o1"}, {"order_uuid": "o1"}],
                 closes=[])
    res = read_arm_results(db, "run-A")
    assert res["status"] == "ok"
    assert res["fill_events"] == 2
    assert res["filled_orders"] == 1


def test_read_arm_results_close_split(tmp_path: Path):
    from scripts.shadow_tournament import read_arm_results

    db = tmp_path / "arm.db"
    _seed_arm_db(db, "run-A",
                 fills=[{"order_uuid": "o1"}],
                 closes=[{"method": "shadow_merge", "realized_pnl": 0.05},
                         {"method": "single_buy_exit", "realized_pnl": -0.02}])
    res = read_arm_results(db, "run-A")
    assert res["merges"] == 1
    assert res["single_leg_exits"] == 1
    assert res["close_events"] == 2
    assert res["realized_pnl_usd"] == pytest.approx(0.03)


def test_read_arm_results_excludes_sentinel_closes(tmp_path: Path):
    from scripts.shadow_tournament import read_arm_results

    db = tmp_path / "arm.db"
    _seed_arm_db(db, "run-A",
                 fills=[{"order_uuid": "o1"}],
                 closes=[{"method": "shadow_merge", "realized_pnl": 0.05},
                         {"method": "venue_sync", "realized_pnl": 99.0}])
    res = read_arm_results(db, "run-A")
    assert res["close_events"] == 1
    assert res["realized_pnl_usd"] == pytest.approx(0.05)


def test_read_arm_results_no_fills(tmp_path: Path):
    from scripts.shadow_tournament import read_arm_results

    db = tmp_path / "arm.db"
    _seed_arm_db(db, "run-A", fills=[], closes=[])
    res = read_arm_results(db, "run-A")
    assert res["status"] == "no_fills"
    assert res["fill_events"] == 0
    assert res["filled_orders"] == 0
    assert res["close_events"] == 0


def test_read_arm_results_missing_db(tmp_path: Path):
    from scripts.shadow_tournament import read_arm_results

    missing = tmp_path / "nope.db"
    res = read_arm_results(missing, "run-A")
    assert res["status"] == "unavailable"
    assert res["error"]
    assert "fill_events" not in res
    assert not missing.exists()


def test_write_tournament_results_file(tmp_path: Path):
    from scripts.shadow_tournament import build_tournament_plan, write_tournament_results

    plan = build_tournament_plan(
        issue=371,
        base_port=8801,
        base_dir=tmp_path,
        stamp="20261005-032000",
        check_ports=False,
    )
    plan.results_path = tmp_path / "371_20261005-032000_results.json"
    out = write_tournament_results(plan, [0, 1, 0, 0, 0])
    data = json.loads(Path(out).read_text(encoding="utf-8"))
    assert len(data["arms"]) == 5
    assert [a["exit_code"] for a in data["arms"]] == [0, 1, 0, 0, 0]
    assert "stat gate decides" in data["note"]


def test_dry_run_shows_results_path_and_writes_nothing(tmp_path: Path, capsys):
    from scripts.shadow_tournament import ROOT, main

    rc = main([
        "--dry-run",
        "--issue", "371",
        "--base-dir", str(tmp_path),
        "--stamp", "20261005-039999",
        "--no-port-check",
    ])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["results_path"].endswith("371_20261005-039999_results.json")
    assert list(tmp_path.glob("*results.json")) == []
    assert not (ROOT / data["results_path"]).exists()

