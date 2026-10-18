"""Tests for tournament naming conventions, metadata parsing, and heartbeat dash_port."""
from __future__ import annotations

import json
import os
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
    assert len(plan.arms) == 4
    # Default order (control purged 2026-10-12): conservative, balanced, aggressive, prudent
    arm_names = [a.name for a in plan.arms]
    assert arm_names == ["conservative", "balanced", "aggressive", "prudent"]

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
    with pytest.raises(ValueError, match=r"only BOT_\* \(or legacy HUNTER_\*\) overrides are permitted"):
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
    assert len(data["arms"]) == 4


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
        assert arm.env["BOT_QUOTE_TOURNAMENT_PRESET"] == arm.name


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
        assert arm.env["BOT_QUOTE_GATE_DYNAMIC_OFFSET_ON"] == ("1" if preset["dynamic_offset_enabled"] else "0")
        if "dynamic_offset_multiplier" in preset:
            assert float(arm.env["BOT_QUOTE_SPREAD_OFFSET_MULTIPLIER"]) == preset["dynamic_offset_multiplier"]
        if "dynamic_offset_min_cents" in preset:
            assert int(arm.env["BOT_QUOTE_SPREAD_OFFSET_MIN_CENTS"]) == preset["dynamic_offset_min_cents"]
        if "dynamic_offset_max_cents" in preset:
            assert int(arm.env["BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS"]) == preset["dynamic_offset_max_cents"]
    by_name = {a.name: a for a in plan.arms}
    assert float(by_name["prudent"].env["BOT_QUOTE_SPREAD_OFFSET_MULTIPLIER"]) == 0.60
    assert int(by_name["prudent"].env["BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS"]) == 4
    # The legacy capped control arm was purged; every surviving arm runs
    # dynamic offset ON (the loose system baseline).
    for name in ("conservative", "balanced", "aggressive", "prudent"):
        assert by_name[name].env.get("BOT_QUOTE_GATE_DYNAMIC_OFFSET_ON") == "1"


def test_default_arm_ports_and_paths(tmp_path: Path):
    from scripts.shadow_tournament import build_tournament_plan

    plan = build_tournament_plan(
        issue=371,
        base_port=8801,
        base_dir=tmp_path,
        stamp="20261005-032000",
        check_ports=False,
    )
    assert [a.dash_port for a in plan.arms] == [8801, 8802, 8803, 8804]
    db_names = [a.db_path.name for a in plan.arms]
    assert len(set(db_names)) == 4
    run_ids = [a.run_id for a in plan.arms]
    assert len(set(run_ids)) == 4
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


def test_read_arm_results_path_with_space(tmp_path: Path):
    from scripts.shadow_tournament import read_arm_results

    db = tmp_path / "dir with space" / "arm.db"
    db.parent.mkdir(parents=True)
    _seed_arm_db(db, "run-A",
                 fills=[{"order_uuid": "o1"}],
                 closes=[{"method": "shadow_merge", "realized_pnl": 0.05}])
    res = read_arm_results(db, "run-A")
    assert res["status"] == "ok"
    assert res["fill_events"] == 1


def test_write_tournament_results_rejects_length_mismatch(tmp_path: Path):
    from scripts.shadow_tournament import build_tournament_plan, write_tournament_results

    plan = build_tournament_plan(
        issue=371,
        base_port=8801,
        base_dir=tmp_path,
        stamp="20261005-032000",
        check_ports=False,
    )
    plan.results_path = tmp_path / "mismatch_results.json"
    with pytest.raises(ValueError, match="shorter"):
        write_tournament_results(plan, [0, 0])


def test_tournament_exit_code_prefers_workers_but_flags_report_failure():
    from scripts.shadow_tournament import _tournament_exit_code

    assert _tournament_exit_code([0, 0, 0, 0, 0], True) == 0
    assert _tournament_exit_code([0, 1, 0, 0, 0], True) == 1
    assert _tournament_exit_code([0, 0, 0, 0, 0], False) == 1
    assert _tournament_exit_code([0, 2, 0, 0, 0], False) == 2
    assert _tournament_exit_code([], True) == 0


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
    out = write_tournament_results(plan, [0, 1, 0, 0])
    data = json.loads(Path(out).read_text(encoding="utf-8"))
    assert len(data["arms"]) == 4
    assert [a["exit_code"] for a in data["arms"]] == [0, 1, 0, 0]
    assert "stat gate decides" in data["note"]


def test_dry_run_logs_to_stderr_and_keeps_stdout_pure_json(tmp_path: Path):
    """The launcher's own startup lines must reach stderr (basicConfig).

    Regression: main() never configured logging, so all launcher log.info
    calls were silently dropped. Subprocess-based so pytest's own log
    handlers cannot mask the absence. stdout must stay pure plan JSON.
    """
    import subprocess
    import sys

    from scripts.shadow_tournament import ROOT

    proc = subprocess.run(
        [
            sys.executable, "-m", "scripts.shadow_tournament",
            "--dry-run", "--issue", "999", "--run-number", "9",
            "--base-dir", str(tmp_path), "--no-port-check",
        ],
        capture_output=True, text=True, cwd=str(ROOT), timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "Tournament dry-run" in proc.stderr
    data = json.loads(proc.stdout)
    assert len(data["arms"]) == 4
    assert data["log_path"].endswith("i999_tournament-9.log")


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


class _FakeProc:
    """Stand-in for a subprocess.Popen child: records calls, never spawns."""

    def __init__(self, label, running=True):
        self.label = label
        self.pid = 4242
        self._running = running
        self.terminated = False
        self.killed = False
        self.waited = False

    def poll(self):
        # None while running (terminate is warranted); a code once it has
        # exited, matching a real child the launcher stops at shutdown.
        return None if self._running else 0

    def terminate(self):
        self.terminated = True
        self._running = False

    def kill(self):
        self.killed = True
        self._running = False

    def wait(self, timeout=None):
        self.waited = True
        return 0


def test_launch_starts_and_stops_the_market_filter(tmp_path, monkeypatch):
    """A tournament run must start the Market Filter and tear it down on exit.

    Arms re-read runtime/markets.json each cycle, so the filter is what keeps a
    long run trading fresh markets and stops the feed-staleness refusal from
    killing every arm mid-run. The filter is a real subprocess in production;
    here it is faked so the test spawns nothing and touches no shared feed.
    """
    import scripts.shadow_tournament as st

    spawned: list[tuple[list[str], dict | None, _FakeProc]] = []

    def fake_popen(argv, env=None, **kwargs):
        label = "market-filter" if "filter_loop" in " ".join(argv) else "child"
        proc = _FakeProc(label)
        spawned.append((list(argv), env.copy() if env is not None else None, proc))
        return proc

    monkeypatch.setattr(st.subprocess, "Popen", fake_popen)
    monkeypatch.setenv("HUNTER_DUMMY_LEAK", "should_be_stripped")

    plan = st.build_tournament_plan(
        issue=371,
        arms=[{"name": "solo", **st.TOURNAMENT_PRESETS["balanced"]}],
        base_dir=tmp_path,
        stamp="20261010-120000",
        check_ports=False,
    )
    plan.dashboards = False  # only the filter + one arm, no dashboard server
    rc = st.launch_tournament(plan)
    assert rc == 0

    filter_calls = [(a, e, p) for a, e, p in spawned if "filter_loop" in " ".join(a)]
    assert len(filter_calls) == 1, "Market Filter must start exactly once per run"
    argv, filter_env, proc = filter_calls[0]
    # No --interval flag: filter_loop reads its cycle from SH_FILTER_INTERVAL_SEC.
    assert argv == [st.sys.executable, "-m", "scripts.filter_loop"]
    assert filter_env is not None
    assert filter_env.get("SH_FILTER_INTERVAL_SEC") == str(st.FILTER_INTERVAL_SEC)
    assert not any(k.startswith("HUNTER_") for k in filter_env), "inherited HUNTER_* must be removed"

    # It is still running at shutdown and must be stopped, never left behind.
    assert proc.terminated or proc.killed, "filter must be stopped on run exit"

    # Now verify explicit interval override in environment is honored by setdefault
    spawned.clear()
    monkeypatch.setenv("SH_FILTER_INTERVAL_SEC", "120")
    rc_override = st.launch_tournament(plan)
    assert rc_override == 0
    override_filter_calls = [(a, e, p) for a, e, p in spawned if "filter_loop" in " ".join(a)]
    assert len(override_filter_calls) == 1
    assert override_filter_calls[0][1].get("SH_FILTER_INTERVAL_SEC") == "120"


def test_hybrid_preset_configuration():
    from core_brain.config import MakerConfig, apply_tournament_preset, TOURNAMENT_PRESETS

    assert "hybrid" in TOURNAMENT_PRESETS
    preset = TOURNAMENT_PRESETS["hybrid"]
    assert preset["dynamic_offset_enabled"] is False
    # reward_offset was purged from the preset (HUNTER_REWARD_OFFSET retired).
    assert "reward_offset" not in preset
    assert preset["requote_dead_band"] == 0.060
    # The stop and grace knobs were retired in #619; no preset carries them.
    assert "stop_loss_trigger_usd_cents" not in preset
    assert "single_buy_grace_sec" not in preset

    cfg = apply_tournament_preset(MakerConfig(), "hybrid")
    # reward_offset keeps the shipped class default (0.020); the preset no
    # longer touches it.
    assert cfg.reward_offset == 0.020
    assert cfg.requote_dead_band == 0.060
    assert cfg.stop_loss_mode == "NONE"
    assert cfg.dynamic_offset_enabled is False


def test_i608_character_arms_plan(tmp_path: Path):
    """#608: the 10-character arms file builds a valid 10-arm plan."""
    from scripts.shadow_tournament import build_tournament_plan

    arms_file = Path(__file__).resolve().parent.parent / "configs" / "tournaments" / "i608_tournament-1_arms.json"
    arms = json.loads(arms_file.read_text(encoding="utf-8"))
    assert len(arms) == 10

    plan = build_tournament_plan(
        issue=608, arms=arms, base_port=8801, base_dir=tmp_path,
        run_number=1, check_ports=False,
    )
    assert len(plan.arms) == 10
    # Unique base names, run-number slugs <base>-1, ports 8801-8810.
    bases = [a["name"] for a in arms]
    assert len(set(bases)) == 10
    assert [a.name for a in plan.arms] == [f"{b}-1" for b in bases]
    assert [a.dash_port for a in plan.arms] == list(range(8801, 8811))

    # One knob per character; the-gambler and the-strict-parent define two.
    # #683 purged HUNTER_STOP_LOSS_TRIGGER_USD_CENTS and
    # HUNTER_SINGLE_BUY_GRACE_SEC; the-accountant, the-patient-one,
    # the-graceful and the-strict-parent therefore carry no knobs.
    expected_knobs = {
        "the-accountant": set(),
        "the-patient-one": set(),
        "the-surgeon": {"HUNTER_DYNAMIC_OFFSET_MULT"},
        "the-daydreamer": {"HUNTER_DYNAMIC_OFFSET_MULT"},
        "the-regular": {"HUNTER_DYNAMIC_OFFSET_MAX_CENTS"},
        "the-metronome": {"HUNTER_REQUOTE_DEAD_BAND"},
        "the-gambler": {"HUNTER_REQUOTE_HOLD_BELOW", "HUNTER_REQUOTE_HOLD_QUEUE"},
        "the-chaperone": {"HUNTER_REQUOTE_HOLD_QUEUE"},
        "the-graceful": set(),
        "the-strict-parent": set(),
    }
    assert {a["name"] for a in arms} == set(expected_knobs)
    for a in arms:
        assert set(a["env"]) == expected_knobs[a["name"]], a["name"]


def test_worker_environment_strips_parent_hunter_env(tmp_path, monkeypatch):
    """Shadow worker subprocesses must not inherit parent HUNTER_* variables.

    Arms without explicit overrides must start with clean defaults, while arms
    with explicit settings retain them, non-HUNTER parent variables are
    preserved, and SPREAD_HUNTER_DB points to the arm-specific database.
    """
    import scripts.shadow_tournament as st

    spawned: list[tuple[list[str], dict | None, _FakeProc]] = []

    def fake_popen(argv, env=None, **kwargs):
        label = "market-filter" if "filter_loop" in " ".join(argv) else "worker"
        proc = _FakeProc(label)
        spawned.append((list(argv), env.copy() if env is not None else None, proc))
        return proc

    monkeypatch.setattr(st.subprocess, "Popen", fake_popen)
    # Avoid mutating global signal handlers in pytest process
    monkeypatch.setattr(st.signal, "signal", lambda sig, handler: None)

    # Set parent ambient environment variables
    monkeypatch.setenv("HUNTER_DUMMY_LEAK", "should_be_stripped")
    monkeypatch.setenv("HUNTER_PRICE_RISK_WIDEN", "0.03")
    monkeypatch.setenv("HUNTER_TOURNAMENT_PRESET", "aggressive")
    monkeypatch.setenv("FOO", "bar")
    monkeypatch.setenv("SPREAD_HUNTER_DB", "data/wrong_parent.db")

    plan = st.build_tournament_plan(
        issue=624,
        arms=[
            {"name": "sparse"},
            {"name": "explicit", "env": {"HUNTER_PRICE_RISK_WIDEN": "0.02"}},
        ],
        base_dir=tmp_path,
        stamp="20261016-120000",
        check_ports=False,
    )
    plan.dashboards = False

    rc = st.launch_tournament(plan)
    assert rc == 0

    worker_calls = [
        (a, e, p) for a, e, p in spawned if "core_brain.shadow_run" in " ".join(a)
    ]
    assert len(worker_calls) == 2, "Both arms must spawn shadow workers"

    # Match workers to arms via --db path
    sparse_arm = plan.arms[0]
    explicit_arm = plan.arms[1]

    sparse_call = next(c for c in worker_calls if str(sparse_arm.db_path) in c[0])
    explicit_call = next(c for c in worker_calls if str(explicit_arm.db_path) in c[0])

    sparse_env = sparse_call[1]
    explicit_env = explicit_call[1]
    assert sparse_env is not None
    assert explicit_env is not None

    # Parent non-HUNTER vars and correct db paths must be present in both
    assert sparse_env.get("FOO") == "bar"
    assert explicit_env.get("FOO") == "bar"
    assert sparse_env.get("SPREAD_HUNTER_DB") == str(sparse_arm.db_path)
    assert explicit_env.get("SPREAD_HUNTER_DB") == str(explicit_arm.db_path)

    # Un-set parent HUNTER_* vars must be stripped from both
    assert "HUNTER_DUMMY_LEAK" not in sparse_env
    assert "HUNTER_DUMMY_LEAK" not in explicit_env
    assert "HUNTER_TOURNAMENT_PRESET" not in sparse_env
    assert "HUNTER_TOURNAMENT_PRESET" not in explicit_env

    # Sparse arm must have clean defaults (no HUNTER_PRICE_RISK_WIDEN)
    assert "HUNTER_PRICE_RISK_WIDEN" not in sparse_env

    # Explicit arm must have its explicit override only
    assert explicit_env.get("HUNTER_PRICE_RISK_WIDEN") == "0.02"




# --- #622: the refusal-hold arms file ---------------------------------------

I622_ARMS = (Path(__file__).resolve().parent.parent
             / "configs" / "tournaments" / "i622_tournament-1_arms.json")

#: The only keys an arm may differ on. Everything else must be identical, or the
#: comparison measures two changes at once. After the #683 purge of
#: HUNTER_REFUSAL_HOLD_POLICY, the only remaining divergence is the drift
#: tolerance carried by the drift arm.
I622_VARYING = ("HUNTER_REFUSAL_HOLD_DRIFT",)

#: A three-arm definition carrying only live overrides, used by the plan-shape
#: tests below. The i622 file itself can no longer be planned: it still carries
#: HUNTER_REFUSAL_HOLD_DRIFT, which #684 refuses at plan time (see
#: test_the_i622_arms_file_is_refused_at_plan_time).
SUPPORTED_ARMS = [
    {"name": "hold-remove", "env": {"HUNTER_BANKROLL": "100"}},
    {"name": "hold-keep", "env": {"HUNTER_BANKROLL": "100"}},
    {"name": "hold-drift", "env": {"HUNTER_BANKROLL": "100"}},
]


def _supported_plan(tmp_path):
    from scripts.shadow_tournament import build_tournament_plan

    return build_tournament_plan(issue=684, arms=SUPPORTED_ARMS, base_dir=tmp_path,
                                 run_number=1, check_ports=False)


def test_the_i622_arms_file_varies_only_the_refusal_drift():
    """#622/#683: the refusal-hold arms file shares everything but drift.

    #683 purged HUNTER_REFUSAL_HOLD_POLICY from every arm; the three arms now
    share every setting, with the drift arm carrying its 0.02 tolerance. The file
    is read directly because #684 refuses to plan it while that key is present.
    """
    arms = json.loads(I622_ARMS.read_text(encoding="utf-8"))
    assert len(arms) == 3, "A/B/C is three arms"

    drift = [a for a in arms if "HUNTER_REFUSAL_HOLD_DRIFT" in a["env"]]
    assert len(drift) == 1, "exactly one drift arm carries the tolerance"
    assert drift[0]["env"]["HUNTER_REFUSAL_HOLD_DRIFT"] == "0.02"

    shared = [{k: v for k, v in a["env"].items() if k not in I622_VARYING}
              for a in arms]
    assert shared[0] == shared[1] == shared[2], (
        "the arms must share everything except the refusal-drift knob; anything "
        "else and the result measures two changes at once"
    )
    assert shared[0]["HUNTER_BANKROLL"] == "100"
    assert shared[0]["HUNTER_EMERGENCY_DRAWDOWN_PCT"] == "75.0"

    # The four live stop settings are pinned identically across arms, and the
    # retired stop knob is not carried over from the i492 presets.
    for key in ("HUNTER_STOP_LOSS_MODE", "HUNTER_DROP_CENTS",
                "HUNTER_DRAWDOWN_PCT", "HUNTER_EMERGENCY_DRAWDOWN_PCT"):
        assert key in shared[0], f"{key} must be pinned identically across arms"
    assert "HUNTER_STOP_LOSS_TRIGGER_USD_CENTS" not in shared[0], (
        "that stop setting is retired; the arm env is purged of it"
    )


def test_no_arm_is_pointed_at_the_production_registry(tmp_path):
    for arm in _supported_plan(tmp_path).arms:
        assert Path(arm.db_path).name != "orders.db"
        assert "tournament" in Path(arm.db_path).name


def test_every_arm_names_a_distinct_store_and_run(tmp_path):
    plan = _supported_plan(tmp_path)
    assert len({str(a.db_path) for a in plan.arms}) == 3
    assert len({a.run_id for a in plan.arms}) == 3


# --- Issue #492: the original #484/#485 ten-arm rerun -----------------------

I492_ARMS = (
    Path(__file__).resolve().parent.parent
    / "configs" / "tournaments" / "i492_tournament-1_arms.json"
)

# The first run's per-leg stop cap was `single_buy_max_loss_usd`, in DOLLARS.
# #573 renamed it to `HUNTER_STOP_LOSS_TRIGGER_USD_CENTS` (same magnitude, x100
# units) and #619 replaced it with the entry-relative ABSOLUTE_DROP trigger,
# which reads `HUNTER_DROP_CENTS` in dollars again. Re-expressing the original
# 4.5c cap as ABSOLUTE_DROP + 0.045 keeps the dimension the arm was measuring;
# dropping it would silently disable every arm's stop, changing more than the
# two script fixes this rerun exists to isolate.
#
# #619 also retired `HUNTER_SINGLE_BUY_GRACE_SEC` with no like-for-like
# replacement, so the grace dimension is gone from all ten arms.
# `docs/runs/2026-10-18-492-tournament-rerun.md` records that loss (snapstop now
# differs from aggressive only by its 3.0c stop).
I492_EXPECTED_ENV: dict[str, dict[str, str]] = {
    "control": {
        "BOT_QUOTE_GATE_DYNAMIC_OFFSET_ON": "0",
        "BOT_RISK_STOP_MODE": "ABSOLUTE_DROP",
        "BOT_RISK_STOP_ADVERSE_DROP_CENTS": "0.045",
    },
    "conservative": {
        "BOT_QUOTE_GATE_DYNAMIC_OFFSET_ON": "1",
        "BOT_QUOTE_SPREAD_OFFSET_MULTIPLIER": "0.75",
        "BOT_QUOTE_SPREAD_OFFSET_MIN_CENTS": "2",
        "BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS": "5",
        "BOT_RISK_STOP_MODE": "ABSOLUTE_DROP",
        "BOT_RISK_STOP_ADVERSE_DROP_CENTS": "0.030",
        "BOT_QUOTE_TOURNAMENT_PRESET": "conservative",
    },
    "balanced": {
        "BOT_QUOTE_GATE_DYNAMIC_OFFSET_ON": "1",
        "BOT_QUOTE_SPREAD_OFFSET_MULTIPLIER": "0.5",
        "BOT_QUOTE_SPREAD_OFFSET_MIN_CENTS": "2",
        "BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS": "4",
        "BOT_RISK_STOP_MODE": "ABSOLUTE_DROP",
        "BOT_RISK_STOP_ADVERSE_DROP_CENTS": "0.050",
        "BOT_QUOTE_TOURNAMENT_PRESET": "balanced",
    },
    "aggressive": {
        "BOT_QUOTE_GATE_DYNAMIC_OFFSET_ON": "1",
        "BOT_QUOTE_SPREAD_OFFSET_MULTIPLIER": "0.25",
        "BOT_QUOTE_SPREAD_OFFSET_MIN_CENTS": "2",
        "BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS": "3",
        "BOT_RISK_STOP_MODE": "ABSOLUTE_DROP",
        "BOT_RISK_STOP_ADVERSE_DROP_CENTS": "0.065",
        "BOT_QUOTE_TOURNAMENT_PRESET": "aggressive",
    },
    "prudent": {
        "BOT_QUOTE_GATE_DYNAMIC_OFFSET_ON": "1",
        "BOT_QUOTE_SPREAD_OFFSET_MULTIPLIER": "0.6",
        "BOT_QUOTE_SPREAD_OFFSET_MIN_CENTS": "2",
        "BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS": "4",
        "BOT_RISK_STOP_MODE": "ABSOLUTE_DROP",
        "BOT_RISK_STOP_ADVERSE_DROP_CENTS": "0.038",
        "BOT_QUOTE_TOURNAMENT_PRESET": "prudent",
    },
    "stickhold": {
        "BOT_QUOTE_GATE_DYNAMIC_OFFSET_ON": "1",
        "BOT_QUOTE_SPREAD_OFFSET_MULTIPLIER": "0.25",
        "BOT_QUOTE_SPREAD_OFFSET_MIN_CENTS": "2",
        "BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS": "3",
        "BOT_ORDER_REQUOTE_HOLD_BELOW_TARGET_CENTS": "0",
        "BOT_ORDER_REQUOTE_HOLD_QUEUE_SHARES": "0",
        "BOT_RISK_STOP_MODE": "ABSOLUTE_DROP",
        "BOT_RISK_STOP_ADVERSE_DROP_CENTS": "0.065",
    },
    "queuehug": {
        "BOT_QUOTE_GATE_DYNAMIC_OFFSET_ON": "1",
        "BOT_QUOTE_SPREAD_OFFSET_MULTIPLIER": "0.50",
        "BOT_QUOTE_SPREAD_OFFSET_MIN_CENTS": "2",
        "BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS": "4",
        "BOT_ORDER_REQUOTE_HOLD_BELOW_TARGET_CENTS": "1.0",
        "BOT_ORDER_REQUOTE_HOLD_QUEUE_SHARES": "800",
        "BOT_RISK_STOP_MODE": "ABSOLUTE_DROP",
        "BOT_RISK_STOP_ADVERSE_DROP_CENTS": "0.050",
    },
    "deadband-tight": {
        "BOT_QUOTE_GATE_DYNAMIC_OFFSET_ON": "1",
        "BOT_QUOTE_SPREAD_OFFSET_MULTIPLIER": "0.75",
        "BOT_QUOTE_SPREAD_OFFSET_MIN_CENTS": "2",
        "BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS": "5",
        "BOT_ORDER_REQUOTE_DEAD_BAND_CENTS": "0.01",
        "BOT_ORDER_REQUOTE_HOLD_QUEUE_SHARES": "500",
        "BOT_ORDER_REQUOTE_HOLD_BELOW_TARGET_CENTS": "0.08",
        "BOT_RISK_STOP_MODE": "ABSOLUTE_DROP",
        "BOT_RISK_STOP_ADVERSE_DROP_CENTS": "0.030",
    },
    "deadband-wide": {
        "BOT_QUOTE_GATE_DYNAMIC_OFFSET_ON": "1",
        "BOT_QUOTE_SPREAD_OFFSET_MULTIPLIER": "0.50",
        "BOT_QUOTE_SPREAD_OFFSET_MIN_CENTS": "2",
        "BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS": "4",
        "BOT_ORDER_REQUOTE_DEAD_BAND_CENTS": "0.06",
        "BOT_ORDER_REQUOTE_HOLD_QUEUE_SHARES": "500",
        "BOT_ORDER_REQUOTE_HOLD_BELOW_TARGET_CENTS": "0.08",
        "BOT_RISK_STOP_MODE": "ABSOLUTE_DROP",
        "BOT_RISK_STOP_ADVERSE_DROP_CENTS": "0.050",
    },
    "snapstop": {
        "BOT_QUOTE_GATE_DYNAMIC_OFFSET_ON": "1",
        "BOT_QUOTE_SPREAD_OFFSET_MULTIPLIER": "0.25",
        "BOT_QUOTE_SPREAD_OFFSET_MIN_CENTS": "2",
        "BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS": "3",
        "BOT_ORDER_REQUOTE_HOLD_QUEUE_SHARES": "500",
        "BOT_ORDER_REQUOTE_HOLD_BELOW_TARGET_CENTS": "0.08",
        "BOT_RISK_STOP_MODE": "ABSOLUTE_DROP",
        "BOT_RISK_STOP_ADVERSE_DROP_CENTS": "0.030",
    },
}

# Knobs `core_brain.config` names and ignores with a warning (#619, #622) or
# dropped outright. A baseline that carries them measures the shipped defaults,
# not the value written in the file.
I492_RETIRED_KNOBS = {
    "HUNTER_STOP_LOSS_TRIGGER_USD_CENTS",
    "HUNTER_STOP_LOSS_TRIGGER_PCT",
    "HUNTER_SINGLE_BUY_GRACE_SEC",
    "HUNTER_SINGLE_BUY_MAX_LOSS_USD",
    "HUNTER_SINGLE_BUY_MAX_LOSS_PCT",
    "HUNTER_REWARD_OFFSET",
    "HUNTER_REFUSAL_HOLD_POLICY",
    "HUNTER_REFUSAL_HOLD_DRIFT",
}

# #487 hypothesises one changed catalyst per arm. Each value below IS the tweak,
# so its presence here would mean the rerun is no longer the baseline #487 is
# compared against.
I487_TWEAKS = {
    ("control", "HUNTER_DROP_CENTS"): "0.060",
    ("conservative", "HUNTER_DROP_CENTS"): "0.045",
    ("balanced", "HUNTER_DYNAMIC_OFFSET_MULT"): "0.25",
    ("aggressive", "HUNTER_DROP_CENTS"): "0.045",
    ("prudent", "HUNTER_DYNAMIC_OFFSET_MULT"): "0.30",
    ("stickhold", "HUNTER_REQUOTE_HOLD_QUEUE"): "500",
    ("queuehug", "HUNTER_REQUOTE_HOLD_BELOW"): "0.08",
    ("deadband-tight", "HUNTER_REQUOTE_DEAD_BAND"): "0.05",
    ("deadband-wide", "HUNTER_DYNAMIC_OFFSET_MULT"): "0.25",
    ("snapstop", "HUNTER_SINGLE_BUY_GRACE_SEC"): "45",
}


def _i492_arms() -> list[dict]:
    return json.loads(I492_ARMS.read_text(encoding="utf-8"))


def test_i492_arms_restore_the_first_run_dimensions():
    arms = _i492_arms()
    assert [a["name"] for a in arms] == list(I492_EXPECTED_ENV)
    for arm in arms:
        assert arm["env"] == I492_EXPECTED_ENV[arm["name"]], arm["name"]


def test_i492_arms_carry_no_retired_knob():
    for arm in _i492_arms():
        offenders = I492_RETIRED_KNOBS & set(arm["env"])
        assert not offenders, f"{arm['name']} carries retired knobs {sorted(offenders)}"


def test_i492_arms_carry_no_487_catalyst_tweak():
    env_by_arm = {a["name"]: a["env"] for a in _i492_arms()}
    for (arm, knob), tweak in I487_TWEAKS.items():
        assert env_by_arm[arm].get(knob) != tweak, (
            f"{arm} carries the #487 tweak {knob}={tweak}"
        )



def test_every_i492_arm_loads_its_own_config():
    """A plan the children cannot load is not a tournament.

    The file shipped `HUNTER_TOURNAMENT_PRESET=control` long after #484's
    control preset was purged. `config.from_env` raises on an unknown preset, so
    that arm would have died at startup while the other nine kept posting --
    the exact failure the plan now refuses up front.
    """
    from core_brain.config import load

    for arm in _i492_arms():
        for key, value in arm["env"].items():
            os.environ[key] = value
        try:
            cfg = load()
            assert cfg.bot_risk_stop_mode == "ABSOLUTE_DROP", arm["name"]
            assert cfg.bot_risk_stop_adverse_drop_cents == pytest.approx(
                float(arm["env"]["BOT_RISK_STOP_ADVERSE_DROP_CENTS"])), arm["name"]
            if "BOT_QUOTE_SPREAD_OFFSET_MULTIPLIER" in arm["env"]:
                assert cfg.bot_quote_spread_offset_multiplier == pytest.approx(
                    float(arm["env"]["BOT_QUOTE_SPREAD_OFFSET_MULTIPLIER"])), arm["name"]
        finally:
            for key in arm["env"]:
                os.environ.pop(key, None)


def test_i492_plan_names_ten_fresh_stores_and_ports(tmp_path):
    from scripts.shadow_tournament import build_tournament_plan

    arms = _i492_arms()
    plan = build_tournament_plan(issue=492, arms=arms, base_dir=tmp_path,
                                 run_number=1, check_ports=False)
    assert [a.name for a in plan.arms] == [f"{a['name']}-1" for a in arms]
    assert [a.dash_port for a in plan.arms] == list(range(8801, 8811))
    assert len({str(a.db_path) for a in plan.arms}) == 10
    assert len({a.run_id for a in plan.arms}) == 10
    for arm in plan.arms:
        name = Path(arm.db_path).name
        assert name.startswith("i492_tournament-1_"), name
        assert name != "orders.db"


def test_run_numbered_dry_run_reads_the_conventional_arms_file(tmp_path, capsys):
    """The issue's acceptance command must carry the ten arms, not the default four."""
    from scripts.shadow_tournament import main

    rc = main([
        "--issue", "492", "--minutes", "720", "--run-number", "1",
        "--dry-run", "--base-dir", str(tmp_path),
    ])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["run_number"] == 1
    assert [a["name"] for a in data["arms"]] == [
        f"{name}-1" for name in I492_EXPECTED_ENV
    ]
    assert [a["dash_port"] for a in data["arms"]] == list(range(8801, 8811))
    assert data["record_path"].replace("\\", "/") == (
        "runtime/tournaments/i492_tournament-1.json")
    assert data["results_path"].replace("\\", "/") == (
        "reports/tournaments/i492_tournament-1_results.json")
    assert data["log_path"].replace("\\", "/") == (
        "runtime/tournaments/i492_tournament-1.log")
    assert data["arms_source"] == "configs/tournaments/i492_tournament-1_arms.json"


def test_explicit_arms_file_beats_the_convention(tmp_path, capsys):
    from scripts.shadow_tournament import main

    arms_file = tmp_path / "explicit_arms.json"
    arms_file.write_text(json.dumps(SUPPORTED_ARMS), encoding="utf-8")
    rc = main([
        "--issue", "492", "--run-number", "1", "--dry-run",
        "--base-dir", str(tmp_path), "--arms-file", str(arms_file),
    ])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert [a["name"] for a in data["arms"]] == [
        "hold-remove-1", "hold-keep-1", "hold-drift-1",
    ], "an explicit arms file wins over the i492 convention"


def test_unknown_preset_selector_is_refused_at_plan_time(tmp_path):
    from scripts.shadow_tournament import build_tournament_plan

    with pytest.raises(ValueError, match="unknown tournament preset"):
        build_tournament_plan(
            issue=492,
            arms=[{"name": "control",
                   "env": {"BOT_QUOTE_TOURNAMENT_PRESET": "control"}}],
            base_dir=tmp_path, run_number=1, check_ports=False,
        )
    with pytest.raises(ValueError, match="unknown tournament preset"):
        build_tournament_plan(
            issue=492,
            arms=[{"name": "control",
                   "env": {"HUNTER_TOURNAMENT_PRESET": "control"}}],
            base_dir=tmp_path, run_number=1, check_ports=False,
        )


def test_explicit_legacy_preset_selector_not_overwritten_by_name_default(tmp_path):
    from scripts.shadow_tournament import build_tournament_plan

    plan = build_tournament_plan(
        issue=492,
        arms=[{"name": "conservative",
               "env": {"HUNTER_TOURNAMENT_PRESET": "aggressive"}}],
        base_dir=tmp_path, run_number=1, check_ports=False,
    )
    assert plan.arms[0].env["HUNTER_TOURNAMENT_PRESET"] == "aggressive"
    assert "BOT_QUOTE_TOURNAMENT_PRESET" not in plan.arms[0].env


# --- Issue #684: retired legacy env names are refused at plan time ----------
#
# `core_brain.config` warns and ignores these keys (or, for the purged reward
# offset, never reads them). An arm that carries one measures the shipped
# default, so the run compares two identical arms while the file says otherwise.
# The planner refuses them before anything starts.

#: The canonical replacement for each retired key, as substrings the message must
#: contain. Keys with no replacement must say so instead of inventing one.
RETIRED_GUIDANCE = {
    "HUNTER_STOP_LOSS_TRIGGER_USD_CENTS": (
        "BOT_RISK_STOP_MODE", "ABSOLUTE_DROP", "BOT_RISK_STOP_ADVERSE_DROP_CENTS"),
    "HUNTER_STOP_LOSS_TRIGGER_PCT": (
        "BOT_RISK_STOP_MODE", "PERCENTAGE_DROP", "BOT_RISK_STOP_MAX_DRAWDOWN_PCT"),
    "HUNTER_SINGLE_BUY_MAX_LOSS_USD": ("BOT_RISK_STOP_ADVERSE_DROP_CENTS",),
    "HUNTER_SINGLE_BUY_MAX_LOSS_PCT": ("BOT_RISK_STOP_MAX_DRAWDOWN_PCT",),
    "HUNTER_SINGLE_BUY_GRACE_SEC": ("BOT_RISK_AGED_OUT_RESCUE_LEAD_SEC",),
    "HUNTER_REFUSAL_HOLD_POLICY": ("no replacement",),
    "HUNTER_REFUSAL_HOLD_DRIFT": ("no replacement",),
    "HUNTER_REWARD_OFFSET": ("no environment replacement",),
}


def _plan_one(env, tmp_path, name="arm-one"):
    from scripts.shadow_tournament import build_tournament_plan

    return build_tournament_plan(issue=684, arms=[{"name": name, "env": env}],
                                 base_dir=tmp_path, run_number=1, check_ports=False)


def test_retired_env_inventory_matches_the_i492_inventory():
    from scripts.shadow_tournament import RETIRED_ENV_OVERRIDES

    assert set(RETIRED_GUIDANCE) == I492_RETIRED_KNOBS
    assert set(RETIRED_ENV_OVERRIDES) == I492_RETIRED_KNOBS, (
        "the planner's refusal list and the config-loader inventory must not drift"
    )
    assert all(g.strip() for g in RETIRED_ENV_OVERRIDES.values()), (
        "every refused key needs actionable guidance"
    )


@pytest.mark.parametrize("key", sorted(I492_RETIRED_KNOBS))
@pytest.mark.parametrize("value", ["", " ", "0", "0.05"])
def test_a_retired_env_key_is_refused_at_plan_time(key, value, tmp_path):
    with pytest.raises(ValueError) as exc:
        _plan_one({key: value}, tmp_path)
    message = str(exc.value)
    assert "arm-one" in message, "the refusal must name the offending arm"
    assert key in message, "the refusal must quote the retired key"
    assert "retired" in message.lower()


def test_a_retired_key_is_refused_even_with_its_replacement_present(tmp_path):
    # A correct replacement does not redeem the dead key: leaving both in place
    # hides which one actually took effect.
    with pytest.raises(ValueError, match="HUNTER_STOP_LOSS_TRIGGER_USD_CENTS"):
        _plan_one({
            "BOT_RISK_STOP_MODE": "ABSOLUTE_DROP",
            "BOT_RISK_STOP_ADVERSE_DROP_CENTS": "0.045",
            "HUNTER_STOP_LOSS_TRIGGER_USD_CENTS": "4.5",
        }, tmp_path)


def test_a_retired_key_in_a_later_arm_is_refused(tmp_path):
    from scripts.shadow_tournament import build_tournament_plan

    arms = [
        {"name": "clean", "env": {"HUNTER_BANKROLL": "100"}},
        {"name": "dead", "env": {"HUNTER_REFUSAL_HOLD_DRIFT": "0.02"}},
    ]
    with pytest.raises(ValueError, match="'dead'"):
        build_tournament_plan(issue=684, arms=arms, base_dir=tmp_path,
                              run_number=1, check_ports=False)


def test_a_supported_legacy_alias_still_plans(tmp_path):
    # The purge is per key, not per prefix: these HUNTER_* names still load and
    # must keep working inside an arm.
    plan = _plan_one({"HUNTER_REQUOTE_HOLD_QUEUE": "500",
                      "HUNTER_DROP_CENTS": "0.05",
                      "HUNTER_PRICE_RISK_WIDEN": "0.02"}, tmp_path)
    env = plan.arms[0].env
    assert env["HUNTER_REQUOTE_HOLD_QUEUE"] == "500"
    assert env["HUNTER_DROP_CENTS"] == "0.05"
    assert env["HUNTER_PRICE_RISK_WIDEN"] == "0.02"


def test_the_refusal_names_the_replacement_or_says_there_is_none(tmp_path):
    assert set(RETIRED_GUIDANCE) == I492_RETIRED_KNOBS
    for key, expected in RETIRED_GUIDANCE.items():
        with pytest.raises(ValueError) as exc:
            _plan_one({key: "1"}, tmp_path)
        message = str(exc.value)
        for fragment in expected:
            assert fragment in message, f"{key} guidance must mention {fragment!r}"

    # The reward offset has no env knob at all; never advertise one as a fix.
    with pytest.raises(ValueError) as exc:
        _plan_one({"HUNTER_REWARD_OFFSET": "0.005"}, tmp_path)
    assert "BOT_QUOTE_SPREAD_STATIC_OFFSET_CENTS" not in str(exc.value)


def test_the_i622_arms_file_is_refused_at_plan_time(tmp_path):
    from scripts.shadow_tournament import build_tournament_plan

    arms = json.loads(I622_ARMS.read_text(encoding="utf-8"))
    with pytest.raises(ValueError) as exc:
        build_tournament_plan(issue=622, arms=arms, base_dir=tmp_path,
                              run_number=1, check_ports=False)
    message = str(exc.value)
    assert "'hold-drift'" in message, "the third arm is the offender"
    assert "HUNTER_REFUSAL_HOLD_DRIFT" in message


def test_a_retired_arm_is_refused_before_anything_launches(tmp_path, monkeypatch,
                                                          capsys):
    import scripts.shadow_tournament as st

    def _explode(*_args, **_kwargs):
        raise AssertionError("nothing may launch when the plan is refused")

    monkeypatch.setattr(st, "launch_tournament", _explode)
    monkeypatch.setattr(st.subprocess, "Popen", _explode)

    arms_file = tmp_path / "retired_arms.json"
    arms_file.write_text(json.dumps([
        {"name": "dead", "env": {"HUNTER_REFUSAL_HOLD_DRIFT": "0.02"}},
    ]), encoding="utf-8")

    with pytest.raises(ValueError, match="HUNTER_REFUSAL_HOLD_DRIFT"):
        st.main(["--issue", "684", "--run-number", "1",
                 "--base-dir", str(tmp_path), "--arms-file", str(arms_file)])

    assert capsys.readouterr().out.strip() == "", "no plan JSON may be printed"


