"""Tests for tournament naming conventions, metadata parsing, and heartbeat dash_port."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from core_brain.shadow_run import (
    build_tournament_run_id,
    parse_tournament_db_path,
    write_shadow_heartbeat,
)


def test_parse_legacy_tournament_db_path():
    """Writers no longer emit this shape; the stores already on disk still read."""
    db_path = Path("data/371_tournament_01_aggressive_20261005-031500.db")
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
    assert plan.interval == 1.0
    assert len(plan.arms) == 4
    # Default order (control purged 2026-10-12): conservative, balanced, aggressive, prudent
    arm_names = [a.name for a in plan.arms]
    assert arm_names == ["conservative", "balanced", "aggressive", "prudent"]

    # Check first arm details
    first = plan.arms[0]
    assert first.index == 1
    assert first.dash_port == 8801
    assert first.db_path.name == "conservative.db"
    assert first.db_path.parent.name == f"i371_{plan.stamp}"
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


def test_build_tournament_plan_rejects_a_non_canonical_env():
    from scripts.shadow_tournament import build_tournament_plan

    bad_env_arms = [
        {"name": "arm-one", "env": {"NOT_A_CONFIG_VARIABLE": "123"}},
    ]
    with pytest.raises(ValueError, match=r"only BOT_\* or FILTER_\* overrides are permitted"):
        build_tournament_plan(issue=371, arms=bad_env_arms, check_ports=False)


def test_build_tournament_plan_rejects_a_retired_env_name():
    """The retired `HUNTER_*` names are not arm overrides any more."""
    from scripts.shadow_tournament import build_tournament_plan

    bad_env_arms = [
        {"name": "arm-one", "env": {"HUNTER_DYNAMIC_OFFSET_MULT": "0.5"}},
    ]
    with pytest.raises(ValueError, match=r"only BOT_\* or FILTER_\* overrides are permitted"):
        build_tournament_plan(issue=371, arms=bad_env_arms, check_ports=False)


def test_build_tournament_plan_rejects_a_retired_preset_key():
    """A pre-#732 arm key is refused, not silently left at the shipped default.

    `_preset_to_env` only reads canonical `bot_*` keys, so an arm still spelled
    with the old name used to run with no override at all -- the tournament
    then compared an arm nobody chose.
    """
    from scripts.shadow_tournament import build_tournament_plan

    stale_arms = [
        {"name": "arm-one", "dynamic_offset_enabled": True, "stop_loss_mode": "NONE"},
    ]
    with pytest.raises(ValueError, match=r"retired preset keys"):
        build_tournament_plan(issue=371, arms=stale_arms, check_ports=False)


def test_build_tournament_plan_rejects_live_port_8799():
    from scripts.shadow_tournament import build_tournament_plan

    with pytest.raises(ValueError, match="8799"):
        build_tournament_plan(issue=371, base_port=8799, check_ports=False)


def test_build_tournament_plan_rejects_existing_db(tmp_path: Path):
    from scripts.shadow_tournament import build_tournament_plan

    existing_db = tmp_path / "i371_20261005-032000" / "arm-one.db"
    existing_db.parent.mkdir(parents=True)
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
        "--stamp", "current-setup",
        "--no-port-check",
    ])
    assert rc == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["issue"] == 371
    # `--stamp` names the run; the plan still carries the ISO launch stamp.
    import re as _re
    assert _re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}-\d{2}", data["stamp"]), data["stamp"]
    assert "i371-current-setup_" in data["record_path"].replace("\\", "/")
    assert data["record_path"].replace("\\", "/").endswith("/plan.json")
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
        assert arm.env["BOT_QUOTE_GATE_DYNAMIC_OFFSET_ON"] == ("1" if preset["bot_quote_gate_dynamic_offset_on"] else "0")
        if "bot_quote_spread_offset_multiplier" in preset:
            assert float(arm.env["BOT_QUOTE_SPREAD_OFFSET_MULTIPLIER"]) == preset["bot_quote_spread_offset_multiplier"]
        if "bot_quote_spread_offset_min_cents" in preset:
            assert int(arm.env["BOT_QUOTE_SPREAD_OFFSET_MIN_CENTS"]) == preset["bot_quote_spread_offset_min_cents"]
        if "bot_quote_spread_offset_max_cents" in preset:
            assert int(arm.env["BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS"]) == preset["bot_quote_spread_offset_max_cents"]
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
        assert arm.db_path.name == f"{arm.name}.db"
        assert arm.db_path.parent == plan.record_path.parent


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
    assert "i999-r9_" in data["log_path"].replace("\\", "/")
    assert data["log_path"].endswith("launch.log")


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
    # `--stamp` names the run; the ISO launch stamp is always appended, so the
    # assertion pins the prefix and the extension, never the clock.
    assert "i371-20261005-039999_" in data["results_path"].replace("\\", "/")
    assert data["results_path"].endswith("results.json")
    assert list(tmp_path.glob("*/results.json")) == []
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
    assert not any(k.startswith(("BOT_", "FILTER_")) for k in filter_env), (
        "inherited config variables must be removed"
    )

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
    assert preset["bot_quote_gate_dynamic_offset_on"] is False
    # the static spread offset was purged from the preset
    assert "bot_quote_spread_static_offset_cents" not in preset
    assert preset["bot_order_requote_dead_band_cents"] == 0.060
    # The stop and grace knobs were retired in #619; no preset carries them.
    assert "stop_loss_trigger_usd_cents" not in preset
    assert "single_buy_grace_sec" not in preset

    cfg = apply_tournament_preset(MakerConfig(), "hybrid")
    # reward_offset keeps the shipped class default (0.020); the preset no
    # longer touches it.
    assert cfg.bot_quote_spread_static_offset_cents == 0.020
    assert cfg.bot_order_requote_dead_band_cents == 0.060
    assert cfg.bot_risk_stop_mode == "NONE"
    assert cfg.bot_quote_gate_dynamic_offset_on is False


def test_i608_character_arms_plan(tmp_path: Path):
    """#608: the character arms file builds a valid 8-arm plan.

    #753 deleted `the-gambler` and `the-chaperone`: both existed only to set the
    re-quote holds #387 retired, so removing those settings left them empty.
    """
    from scripts.shadow_tournament import build_tournament_plan

    arms_file = Path(__file__).resolve().parent.parent / "configs" / "tournaments" / "i608-r1_arms.json"
    arms = json.loads(arms_file.read_text(encoding="utf-8"))
    assert len(arms) == 8

    plan = build_tournament_plan(
        issue=608, arms=arms, base_port=8801, base_dir=tmp_path,
        run_number=1, check_ports=False,
    )
    assert len(plan.arms) == 8
    # Unique base names, run-number slugs <base>-1, ports 8801-8808.
    bases = [a["name"] for a in arms]
    assert len(set(bases)) == 8
    assert [a.name for a in plan.arms] == [f"{b}-1" for b in bases]
    assert [a.dash_port for a in plan.arms] == list(range(8801, 8809))

    # One knob per character. #683 purged HUNTER_STOP_LOSS_TRIGGER_USD_CENTS and
    # HUNTER_SINGLE_BUY_GRACE_SEC; the-accountant, the-patient-one,
    # the-graceful and the-strict-parent therefore carry no knobs.
    expected_knobs = {
        "the-accountant": set(),
        "the-patient-one": set(),
        "the-surgeon": {"BOT_QUOTE_SPREAD_OFFSET_MULTIPLIER"},
        "the-daydreamer": {"BOT_QUOTE_SPREAD_OFFSET_MULTIPLIER"},
        "the-regular": {"BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS"},
        "the-metronome": {"BOT_ORDER_REQUOTE_DEAD_BAND_CENTS"},
        "the-graceful": set(),
        "the-strict-parent": set(),
    }
    assert {a["name"] for a in arms} == set(expected_knobs)
    for a in arms:
        assert set(a["env"]) == expected_knobs[a["name"]], a["name"]


def test_worker_environment_strips_parent_config_env(tmp_path, monkeypatch):
    """Shadow worker subprocesses must not inherit parent BOT_*/FILTER_* variables.

    Arms without explicit overrides must start with clean defaults, while arms
    with explicit settings retain them, unrelated parent variables are
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
    monkeypatch.setenv("BOT_QUOTE_PRICE_RISK_WIDEN", "0.03")
    monkeypatch.setenv("BOT_QUOTE_TOURNAMENT_PRESET", "aggressive")
    monkeypatch.setenv("FOO", "bar")
    monkeypatch.setenv("SPREAD_HUNTER_DB", "data/wrong_parent.db")

    plan = st.build_tournament_plan(
        issue=624,
        arms=[
            {"name": "sparse"},
            {"name": "explicit", "env": {"BOT_QUOTE_PRICE_RISK_WIDEN": "0.02"}},
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

    # Parent config vars must be stripped from both
    assert "BOT_QUOTE_TOURNAMENT_PRESET" not in sparse_env
    assert "BOT_QUOTE_TOURNAMENT_PRESET" not in explicit_env
    # A retired name is not config any more: nothing reads it, so the strip list
    # does not know it and passing it on cannot change a run.
    assert sparse_env.get("HUNTER_DUMMY_LEAK") == "should_be_stripped"

    # Sparse arm must have clean defaults (no BOT_QUOTE_PRICE_RISK_WIDEN)
    assert "BOT_QUOTE_PRICE_RISK_WIDEN" not in sparse_env

    # Explicit arm must have its explicit override only
    assert explicit_env.get("BOT_QUOTE_PRICE_RISK_WIDEN") == "0.02"




# --- #622: the refusal-hold arms file ---------------------------------------

I622_ARMS = (Path(__file__).resolve().parent.parent
             / "configs" / "tournaments" / "i622-r1_arms.json")

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
    {"name": "hold-remove", "env": {"BOT_QUOTE_BANK_BASELINE_USD": "100"}},
    {"name": "hold-keep", "env": {"BOT_QUOTE_BANK_BASELINE_USD": "100"}},
    {"name": "hold-drift", "env": {"BOT_QUOTE_BANK_BASELINE_USD": "100"}},
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
    assert shared[0]["BOT_QUOTE_BANK_BASELINE_USD"] == "100"
    assert shared[0]["BOT_RISK_CIRCUIT_MAX_DRAWDOWN_PCT"] == "75.0"

    # The four live stop settings are pinned identically across arms, and the
    # retired stop knob is not carried over from the i492 presets.
    for key in ("BOT_RISK_STOP_MODE", "BOT_RISK_STOP_ADVERSE_DROP_CENTS",
                "BOT_RISK_STOP_MAX_DRAWDOWN_PCT", "BOT_RISK_CIRCUIT_MAX_DRAWDOWN_PCT"):
        assert key in shared[0], f"{key} must be pinned identically across arms"
    assert "BOT_RISK_STOP_TRIGGER_USD_CENTS" not in shared[0], (
        "that stop setting is retired; the arm env is purged of it"
    )


def test_the_i622_arms_file_is_refused_at_plan_time(tmp_path):
    """#684: a retired override is refused, not silently ignored.

    The drift arm differs from its siblings only by `HUNTER_REFUSAL_HOLD_DRIFT`.
    Planning it would run two identical arms and call the result a comparison,
    so the planner names the offending key instead.
    """
    from scripts.shadow_tournament import build_tournament_plan

    arms = json.loads(I622_ARMS.read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match=r"only BOT_\* or FILTER_\* overrides are permitted"):
        build_tournament_plan(issue=622, arms=arms, base_dir=tmp_path,
                              run_number=1, check_ports=False)


def test_no_arm_is_pointed_at_the_production_registry(tmp_path):
    plan = _supported_plan(tmp_path)
    for arm in plan.arms:
        assert Path(arm.db_path).name != "orders.db"
        # The store lives in the run folder, not loose in data/.
        assert Path(arm.db_path).parent == plan.record_path.parent


def test_every_arm_names_a_distinct_store_and_run(tmp_path):
    plan = _supported_plan(tmp_path)
    assert len({str(a.db_path) for a in plan.arms}) == 3
    assert len({a.run_id for a in plan.arms}) == 3


# --- Issue #492: the original #484/#485 ten-arm rerun -----------------------

I492_ARMS = (
    Path(__file__).resolve().parent.parent
    / "configs" / "tournaments" / "i492-r1_arms.json"
)

# The first run's per-leg stop cap was `single_buy_max_loss_usd`, in DOLLARS.
# #573 renamed it to `HUNTER_STOP_LOSS_TRIGGER_USD_CENTS` (same magnitude, x100
# units) and #619 replaced it with the entry-relative ABSOLUTE_DROP trigger,
# which reads `BOT_RISK_STOP_ADVERSE_DROP_CENTS` in dollars again. Re-expressing the original
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
        "BOT_RISK_STOP_MODE": "ABSOLUTE_DROP",
        "BOT_RISK_STOP_ADVERSE_DROP_CENTS": "0.065",
    },
    "queuehug": {
        "BOT_QUOTE_GATE_DYNAMIC_OFFSET_ON": "1",
        "BOT_QUOTE_SPREAD_OFFSET_MULTIPLIER": "0.50",
        "BOT_QUOTE_SPREAD_OFFSET_MIN_CENTS": "2",
        "BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS": "4",
        "BOT_RISK_STOP_MODE": "ABSOLUTE_DROP",
        "BOT_RISK_STOP_ADVERSE_DROP_CENTS": "0.050",
    },
    "deadband-tight": {
        "BOT_QUOTE_GATE_DYNAMIC_OFFSET_ON": "1",
        "BOT_QUOTE_SPREAD_OFFSET_MULTIPLIER": "0.75",
        "BOT_QUOTE_SPREAD_OFFSET_MIN_CENTS": "2",
        "BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS": "5",
        "BOT_ORDER_REQUOTE_DEAD_BAND_CENTS": "0.01",
        "BOT_RISK_STOP_MODE": "ABSOLUTE_DROP",
        "BOT_RISK_STOP_ADVERSE_DROP_CENTS": "0.030",
    },
    "deadband-wide": {
        "BOT_QUOTE_GATE_DYNAMIC_OFFSET_ON": "1",
        "BOT_QUOTE_SPREAD_OFFSET_MULTIPLIER": "0.50",
        "BOT_QUOTE_SPREAD_OFFSET_MIN_CENTS": "2",
        "BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS": "4",
        "BOT_ORDER_REQUOTE_DEAD_BAND_CENTS": "0.06",
        "BOT_RISK_STOP_MODE": "ABSOLUTE_DROP",
        "BOT_RISK_STOP_ADVERSE_DROP_CENTS": "0.050",
    },
    "snapstop": {
        "BOT_QUOTE_GATE_DYNAMIC_OFFSET_ON": "1",
        "BOT_QUOTE_SPREAD_OFFSET_MULTIPLIER": "0.25",
        "BOT_QUOTE_SPREAD_OFFSET_MIN_CENTS": "2",
        "BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS": "3",
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
    # #753: the requote holds #387 retired; no arm may set them again.
    "BOT_ORDER_REQUOTE_HOLD_QUEUE_SHARES",
    "BOT_ORDER_REQUOTE_HOLD_BELOW_TARGET_CENTS",
}

# The SUPERSEDED #487 premise: one "worst catalyst" tweaked per arm, read from
# the #484/#485 forensics. The 2026-10-21 reframe dropped that premise -- those
# forensics described pre-2026-10-20 code, and the knobs it named were retired --
# but the values stay. They are the one job this table still has: keeping a #492
# baseline arm free of a #487-era tweak, so the rerun is still the baseline.
I487_TWEAKS = {
    ("control", "BOT_RISK_STOP_ADVERSE_DROP_CENTS"): "0.060",
    ("conservative", "BOT_RISK_STOP_ADVERSE_DROP_CENTS"): "0.045",
    ("balanced", "BOT_QUOTE_SPREAD_OFFSET_MULTIPLIER"): "0.25",
    ("aggressive", "BOT_RISK_STOP_ADVERSE_DROP_CENTS"): "0.045",
    ("prudent", "BOT_QUOTE_SPREAD_OFFSET_MULTIPLIER"): "0.30",
    ("deadband-tight", "BOT_ORDER_REQUOTE_DEAD_BAND_CENTS"): "0.05",
    ("deadband-wide", "BOT_QUOTE_SPREAD_OFFSET_MULTIPLIER"): "0.25",
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

    The file shipped `BOT_QUOTE_TOURNAMENT_PRESET=control` long after #484's
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
        assert name == f"{arm.name}.db", name
        assert name != "orders.db"
        assert Path(arm.db_path).parent.name.startswith("i492-r1_")


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
    # Anchored to the run root, never to the cwd: a relative path here is what
    # let a launch from scripts/ write the plan record somewhere nobody looks.
    # Plan, results and log share one folder; only the filenames differ.
    run_dir = Path(data["record_path"])
    assert run_dir.is_absolute(), data["record_path"]
    assert run_dir.parent.parent == tmp_path
    assert run_dir.parent.name.startswith("i492-r1_")
    assert run_dir.name == "plan.json"
    for key, filename in (("results_path", "results.json"), ("log_path", "launch.log")):
        reported = Path(data[key])
        assert reported.is_absolute(), (key, data[key])
        assert reported.parent == run_dir.parent, (key, data[key])
        assert reported.name == filename, (key, data[key])
    assert data["arms_source"] == "configs/tournaments/i492-r1_arms.json"


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
                   "env": {"BOT_QUOTE_TOURNAMENT_PRESET": "control"}}],
            base_dir=tmp_path, run_number=1, check_ports=False,
        )


def test_explicit_preset_selector_not_overwritten_by_name_default(tmp_path):
    from scripts.shadow_tournament import build_tournament_plan

    plan = build_tournament_plan(
        issue=492,
        arms=[{"name": "conservative",
               "env": {"BOT_QUOTE_TOURNAMENT_PRESET": "aggressive"}}],
        base_dir=tmp_path, run_number=1, check_ports=False,
    )
    assert plan.arms[0].env["BOT_QUOTE_TOURNAMENT_PRESET"] == "aggressive"


def test_a_retired_preset_selector_is_refused_at_plan_time(tmp_path):
    """The retired `HUNTER_TOURNAMENT_PRESET` is not a name the planner knows."""
    from scripts.shadow_tournament import build_tournament_plan

    with pytest.raises(ValueError, match=r"only BOT_\* or FILTER_\* overrides are permitted"):
        build_tournament_plan(
            issue=492,
            arms=[{"name": "conservative",
                   "env": {"HUNTER_TOURNAMENT_PRESET": "aggressive"}}],
            base_dir=tmp_path, run_number=1, check_ports=False,
        )


def test_a_supported_alias_still_plans(tmp_path):
    from scripts.shadow_tournament import build_tournament_plan

    plan = build_tournament_plan(
        issue=684,
        arms=[{
            "name": "arm-one",
            "env": {
                "BOT_RISK_STOP_ADVERSE_DROP_CENTS": "0.05",
                "BOT_QUOTE_PRICE_RISK_WIDEN": "0.02",
            },
        }],
        base_dir=tmp_path,
        run_number=1,
        check_ports=False,
    )
    env = plan.arms[0].env
    assert env["BOT_RISK_STOP_ADVERSE_DROP_CENTS"] == "0.05"
    assert env["BOT_QUOTE_PRICE_RISK_WIDEN"] == "0.02"


@pytest.mark.parametrize("name", [
    "BOT_ORDER_REQUOTE_HOLD_QUEUE_SHARES",
    "BOT_ORDER_REQUOTE_HOLD_BELOW_TARGET_CENTS",
])
def test_an_arm_environment_setting_a_retired_hold_is_refused(tmp_path, name):
    """#753: the holds #387 retired cannot be set through an arm's env block.

    Nothing reads the variable, so the arm would quietly run the shipped
    default and the tournament would compare a setting nobody chose.
    """
    from scripts.shadow_tournament import build_tournament_plan

    with pytest.raises(ValueError, match=r"retired by #387"):
        build_tournament_plan(
            issue=753,
            arms=[{"name": "arm-one", "env": {name: "500"}}],
            base_dir=tmp_path,
            run_number=1,
            check_ports=False,
        )


@pytest.mark.parametrize("field", [
    "bot_order_requote_hold_queue_shares",
    "bot_order_requote_hold_below_target_cents",
])
def test_an_arm_preset_setting_a_retired_hold_is_refused(field):
    from scripts.shadow_tournament import _preset_to_env

    with pytest.raises(ValueError, match=r"retired by #387"):
        _preset_to_env({"name": "arm-one", field: 500.0})


def test_default_plan_interval_matches_max_rotation_ceiling(tmp_path: Path):
    from core_brain.config import MAX_ROTATION_INTERVAL_SEC
    from scripts.shadow_tournament import build_tournament_plan

    plan = build_tournament_plan(
        base_dir=tmp_path,
        stamp="20261019-120000",
        check_ports=False,
    )
    assert plan.interval == MAX_ROTATION_INTERVAL_SEC == 1.0
    for arm in plan.arms:
        idx = arm.shadow_argv.index("--interval")
        assert arm.shadow_argv[idx + 1] == "1.0"


def test_tournament_main_dry_run_default_interval(tmp_path: Path, capsys):
    from scripts.shadow_tournament import main

    exit_code = main(["--dry-run", "--base-dir", str(tmp_path), "--no-port-check"])
    assert exit_code == 0
    captured = capsys.readouterr().out
    data = json.loads(captured)
    assert data["interval"] == 1.0
    for arm in data["arms"]:
        idx = arm["shadow_argv"].index("--interval")
        assert arm["shadow_argv"][idx + 1] == "1.0"


def test_worker_argument_interval_passes_silently_to_shadow_run(tmp_path: Path, caplog):
    import logging
    from core_brain.shadow_run import _parse_args
    from scripts.shadow_tournament import build_tournament_plan

    plan = build_tournament_plan(
        base_dir=tmp_path,
        stamp="20261019-120000",
        check_ports=False,
    )
    first_arm = plan.arms[0]
    idx = first_arm.shadow_argv.index("--interval")
    interval_val = first_arm.shadow_argv[idx + 1]

    with caplog.at_level(logging.WARNING, logger="core_brain.config"):
        parsed = _parse_args(["--db", "data/x_shadow_test.db", "--interval", interval_val])
    assert parsed.interval == 1.0
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_explicit_interval_retention_and_clamping(tmp_path: Path, caplog):
    import logging
    from core_brain.shadow_run import _parse_args
    from scripts.shadow_tournament import build_tournament_plan

    # Explicit 0.25 (below ceiling): plan retains it, parsed returns 0.25, no warnings
    plan_fast = build_tournament_plan(
        base_dir=tmp_path,
        interval=0.25,
        stamp="20261019-120001",
        check_ports=False,
    )
    assert plan_fast.interval == 0.25
    idx_fast = plan_fast.arms[0].shadow_argv.index("--interval")
    assert plan_fast.arms[0].shadow_argv[idx_fast + 1] == "0.25"

    with caplog.at_level(logging.WARNING, logger="core_brain.config"):
        parsed_fast = _parse_args(["--db", "data/x_shadow_test.db", "--interval", "0.25"])
    assert parsed_fast.interval == 0.25
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    # Explicit 5.0 (above ceiling): plan retains 5.0, worker parser clamps to 1.0 with exactly one warning
    caplog.clear()
    plan_slow = build_tournament_plan(
        base_dir=tmp_path,
        interval=5.0,
        stamp="20261019-120002",
        check_ports=False,
    )
    assert plan_slow.interval == 5.0
    idx_slow = plan_slow.arms[0].shadow_argv.index("--interval")
    assert plan_slow.arms[0].shadow_argv[idx_slow + 1] == "5.0"

    with caplog.at_level(logging.WARNING, logger="core_brain.config"):
        parsed_slow = _parse_args(["--db", "data/x_shadow_test.db", "--interval", "5.0"])
    assert parsed_slow.interval == 1.0
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "5.0" in warnings[0].message and "1.0" in warnings[0].message


def test_cockpit_hybrid_blueprint_tab_purged():
    from scripts.tournament_cockpit_server import generate_cockpit_html

    html = generate_cockpit_html({"arms": [], "tourney_key": "492"})
    start_marker = "<!-- TAB: HYBRID CONFIGURATION BLUEPRINT -->"
    end_marker = "<!-- TAB 2: PNL CURVES -->"
    start_idx = html.find(start_marker)
    end_idx = html.find(end_marker)
    assert start_idx != -1 and end_idx != -1
    blueprint = html[start_idx:end_idx]

    # #799: the pane is built from the leader arm's own launch env. That arm
    # (`balanced-mid` in configs/tournaments/i799-tournament-test_arms.json)
    # overrode exactly one variable -- the `balanced` preset -- so the pricing
    # half of the spec is one line, not a preset plus a stack of overrides. The
    # rest of the block is the deployment layer; the pane labels each of those as
    # an override, because #799 varies one knob per arm and tested none of them.
    canonical_keys = [
        "BOT_QUOTE_TOURNAMENT_PRESET=balanced",
        "BOT_ORDER_REQUOTE_DEAD_BAND_CENTS=0.06",
        "BOT_RISK_STOP_MODE=ABSOLUTE_DROP",
        "BOT_RISK_STOP_ADVERSE_DROP_CENTS=0.045",
        "BOT_ORDER_GATE_QUEUE_CLEAR_ON=1",
        "BOT_ORDER_QUEUE_MAX_CLEAR_MIN=5",
        "BOT_RISK_AGED_OUT_RESCUE_LEAD_SEC=45.0",
        "BOT_POS_PRICE_MAX_PAIR_COST_USD",
    ]
    for key in canonical_keys:
        assert key in blueprint, f"Expected canonical key {key!r} in blueprint"

    # The leader's recipe is `balanced` on its own. `prudent` with the clamp
    # lifted to 6c is the arm that finished behind it, on a value no arm ran, so
    # none of that spec's keys may survive in the block.
    assert "BOT_QUOTE_TOURNAMENT_PRESET=prudent" not in blueprint
    assert "BOT_QUOTE_GATE_DYNAMIC_OFFSET_ON=1" not in blueprint
    assert "BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS=6" not in blueprint
    assert 'TOURNAMENT_PRESETS["balanced"]' in blueprint
    assert "the leader's only env key" in blueprint
    # The ceiling is still documented -- as the preset's own 4c, not an override.
    assert "BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS" in blueprint
    assert "4.0¢" in blueprint

    # Units: the deadband is a dollar fraction and `_bounded_float` floors that
    # field at 1.0, while the offset ceiling is a cents count. Swapping the two
    # is the difference between 6c of protection and a value the engine bounds.
    assert "BOT_ORDER_REQUOTE_DEAD_BAND_CENTS=6" not in blueprint
    assert "BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS=0.06" not in blueprint

    # #753: the retired requote holds are not presented as current settings.
    assert "REQUOTE_HOLD" not in blueprint
    assert "Queue Hold" not in blueprint
    assert "Hold Deviation" not in blueprint

    # The queue window is the one deployment knob #799 actually varied, so it
    # carries the arm's own value (5) rather than the unmeasured 3.0 minutes that
    # stood in the previous revision -- and it is the arm that leads the field on
    # Sharpe, profit factor and drawdown.
    assert "BOT_ORDER_QUEUE_MAX_CLEAR_MIN=3.0" not in blueprint
    assert 'badge-green">#799 queue-clear-5m' in blueprint

    # Every value #799 did not vary is labeled as an unmeasured override, so a
    # deployment choice cannot read as a tournament result. Three rows are
    # override-labeled: the deadband, the stop and the rescue lead.
    assert blueprint.count('badge-amber">Override') == 3
    assert 'badge-green">#799 balanced-mid' in blueprint

    # Interval 1.0 vs 5.0
    assert "--interval 1.0" in blueprint
    assert "--interval 5.0" not in blueprint

    # #799: every performance figure is read from the live arm on the page's own
    # poll. `results.json` says the shadow numbers are modeled and the stat gate
    # decides, so the pane may report what the arm measured and must not forecast
    # past it -- a baked-in projection is wrong the moment the run moves.
    for bound_id in (
        "hb-quotes", "hb-trades", "hb-arms", "hb-progress", "hb-win", "hb-aggwin",
        "hb-merge", "hb-aggmerge", "hb-sharpe", "hb-bestsharpe", "hb-ev", "hb-pf",
        "hb-mcp5", "hb-mdd", "hb-rank", "hb-pnl", "hb-leader", "hb-stops",
        "hb-stoprate", "hb-qcsharpe",
    ):
        assert f'id="{bound_id}"' in blueprint, f"Expected live-bound {bound_id!r} in blueprint"
        # Present in the markup *and* written by renderAll, or it stays a dash.
        assert html.count(bound_id) >= 2, f"{bound_id!r} is never written by the render pass"
    assert "hbArm = hbArms.find" in html
    # The standing card follows the arm the blueprint is built from.
    assert "'balanced-mid'" in html
    assert "'prudent-hybrid'" not in html
    assert "stat gate decides" in blueprint

    # The claims #799 contradicts: a forecast that outran the run, a rank the
    # leaderboard above it disagreed with, and a stop rate no arm measured.
    assert "Performance Forecast" not in blueprint
    assert "Projected Win Rate" not in blueprint
    assert "&gt; 10.5" not in blueprint
    assert "289 executed trades" not in blueprint
    assert "9.52" not in blueprint
    assert "44.1%" not in blueprint
    assert "-$2.28" not in blueprint

    # Purged / dead knobs & misleading labels absent
    assert "HUNTER_" not in blueprint
    assert "max_pair_cost_cap" not in blueprint
    assert "0.08 (8%)" not in blueprint

    # Pair cap comment-only guard in .env block
    for line in blueprint.splitlines():
        if "BOT_POS_PRICE_MAX_PAIR_COST_USD=" in line:
            stripped = line.strip()
            assert stripped.startswith("#"), f"Pair cap must be comment-only in .env snippet: {line}"

    # Discredited #484 primary winner claims absent from full cockpit HTML
    assert "Primary Winner" not in html
    assert "control (Issue #484)" not in html


def test_cockpit_live_readiness_decision_matrix_updated_from_i799():
    from scripts.tournament_cockpit_server import generate_cockpit_html

    sample_arms = [
        {
            "name": "balanced-mid",
            "label": "Balanced Mid",
            "color": "#e040fb",
            "desc": "balanced preset: dynamic 0.50x, 2-4c",
            "total_pnl": 23.39,
            "mean_pnl": 0.145,
            "sharpe_proxy": 1.70,
            "profit_factor": 1.84,
            "win_rate": 60.2,
            "merge_rate": 59.0,
            "max_drawdown": 9.64,
            "mc_p5": 2.17,
            "score": 93,
            "readiness": "READY FOR LIVE",
            "badge_cls": "badge-green",
        },
        {
            "name": "queue-clear-5m",
            "label": "Queue Clear 5m",
            "color": "#18ffff",
            "desc": "queue-clear gate on, 5-minute max clear",
            "total_pnl": 17.39,
            "mean_pnl": 0.202,
            "sharpe_proxy": 4.67,
            "profit_factor": 3.23,
            "win_rate": 66.3,
            "merge_rate": 64.0,
            "max_drawdown": 2.60,
            "mc_p5": 12.90,
            "score": 98,
            "readiness": "READY FOR LIVE",
            "badge_cls": "badge-green",
        },
        {
            "name": "hair-trigger-2c",
            "label": "Hair Trigger 2c",
            "color": "#ff4081",
            "desc": "2c absolute stop on a naked leg",
            "total_pnl": 10.31,
            "mean_pnl": 0.074,
            "sharpe_proxy": 1.40,
            "profit_factor": 1.44,
            "win_rate": 42.4,
            "merge_rate": 41.0,
            "max_drawdown": 7.49,
            "mc_p5": -0.35,
            "score": 77,
            "readiness": "READY FOR LIVE",
            "badge_cls": "badge-green",
        },
        {
            "name": "patient-stop-9c",
            "label": "Patient Stop 9c",
            "color": "#b388ff",
            "desc": "9c absolute stop on a naked leg",
            "total_pnl": 6.43,
            "mean_pnl": 0.051,
            "sharpe_proxy": 0.52,
            "profit_factor": 1.20,
            "win_rate": 51.2,
            "merge_rate": 49.6,
            "max_drawdown": 11.09,
            "mc_p5": -9.56,
            "score": 68,
            "readiness": "NEEDS TUNING",
            "badge_cls": "badge-amber",
        },
    ]
    html = generate_cockpit_html({"arms": sample_arms, "tourney_key": "799"})
    start_marker = "<!-- TAB 6: RECOMMENDATIONS -->"
    end_marker = "<script>"
    start_idx = html.find(start_marker)
    end_idx = html.find(end_marker)
    assert start_idx != -1 and end_idx != -1
    rec_tab = html[start_idx:end_idx]

    # Stale #485 evaluation text must be completely purged
    assert "deadband-wide (Issue #485)" not in rec_tab
    assert "queuehug" not in rec_tab
    assert "-$14.96" not in rec_tab
    assert "deadband-tight" not in rec_tab
    assert "-$17.40" not in rec_tab
    assert "Avoid: <code>balanced</code>" not in rec_tab

    # Updated #799 Live Readiness Decision Matrix must be present
    assert "Live Readiness Decision Matrix" in rec_tab
    assert "decision-matrix-tbody" in rec_tab
    assert "queue-clear-5m" in rec_tab
    assert "balanced-mid" in rec_tab
    assert "READY FOR LIVE" in rec_tab
    assert "hair-trigger" in rec_tab.lower()
    assert "patient-stop" in rec_tab.lower()
    assert "score" in rec_tab.lower()







# --- Issue #487: the current-setup ten-arm tournament ------------------------

I487_ARMS = (
    Path(__file__).resolve().parent.parent
    / "configs" / "tournaments" / "i487-current-setup-10arm_arms.json"
)

# The plan's Decision 3 table, arm for arm. The control runs today's
# configuration untouched (empty env); every other arm sets exactly one
# canonical `BOT_*` variable. The names and values are pinned here rather than
# derived, because the whole point of the run is that these ten differences --
# and only these ten -- are measured.
I487_ARM_ENV: dict[str, dict[str, str]] = {
    "control": {},
    "offset-mult-025": {"BOT_QUOTE_SPREAD_OFFSET_MULTIPLIER": "0.25"},
    "offset-min-2c": {"BOT_QUOTE_SPREAD_OFFSET_MIN_CENTS": "2"},
    "offset-max-3c": {"BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS": "3"},
    "offset-static": {"BOT_QUOTE_GATE_DYNAMIC_OFFSET_ON": "0"},
    "deadband-6c": {"BOT_ORDER_REQUOTE_DEAD_BAND_CENTS": "0.06"},
    "widen-on": {"BOT_QUOTE_GATE_WIDEN_ON": "1"},
    "price-risk-030": {"BOT_QUOTE_PRICE_RISK_WIDEN": "0.030"},
    "stop-adverse-5c": {"BOT_RISK_STOP_MODE": "ABSOLUTE_DROP"},
    "book-window-4c": {"BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID": "4"},
}

# The five arms whose value a #484 preset ships, and the preset key each one
# reads. The assertion compares against `TOURNAMENT_PRESETS` itself, so a later
# preset edit that moves a value breaks this test instead of letting the arm
# drift away from the table it claims to be sourced from.
#
# Arms 7-10 are absent on purpose: no preset ships a *different* value for those
# four knobs, so "fetched live from the presets" cannot apply to them. Their
# honesty note lives in the arm's description in the cockpit config.
I487_PRESET_SOURCED: dict[str, str] = {
    "offset-mult-025": "bot_quote_spread_offset_multiplier",
    "offset-min-2c": "bot_quote_spread_offset_min_cents",
    "offset-max-3c": "bot_quote_spread_offset_max_cents",
    "offset-static": "bot_quote_gate_dynamic_offset_on",
    "deadband-6c": "bot_order_requote_dead_band_cents",
}


def _i487_arms() -> list[dict]:
    return json.loads(I487_ARMS.read_text(encoding="utf-8"))


def _i487_same_setting(env_token: str, preset_value: object) -> bool:
    """A preset value and its `BOT_*` env spelling, compared as one setting.

    Bools travel as "1"/"0"; numbers compare as numbers, so the file's "500"
    and the preset's `500.0` are the same setting.
    """
    if isinstance(preset_value, bool):
        return env_token == ("1" if preset_value else "0")
    return float(env_token) == float(preset_value)


def test_i487_arms_are_one_knob_variations_of_the_control():
    from core_brain.config import TOURNAMENT_PRESETS
    from scripts.shadow_tournament import ARM_NAME_PATTERN

    arms = _i487_arms()
    assert [a["name"] for a in arms] == list(I487_ARM_ENV)
    assert arms[0]["env"] == {}, "the control arm must run today's setup untouched"
    for arm in arms:
        assert arm["env"] == I487_ARM_ENV[arm["name"]], arm["name"]

    names = [a["name"] for a in arms]
    assert len(set(names)) == 10
    # `build_tournament_plan` appends BOT_QUOTE_TOURNAMENT_PRESET=<arm name>
    # when the name is a preset key, turning a one-knob arm into a six-knob
    # one. The spec must stay disjoint from the preset names.
    collisions = sorted(set(names) & set(TOURNAMENT_PRESETS))
    assert not collisions, f"arm names collide with presets: {collisions}"
    for name in names:
        assert ARM_NAME_PATTERN.match(name), name


def test_i487_preset_sourced_values_come_from_the_live_preset_table():
    from core_brain.config import TOURNAMENT_PRESETS

    arms = {a["name"]: a for a in _i487_arms()}
    for arm_name, preset_key in I487_PRESET_SOURCED.items():
        env = arms[arm_name]["env"]
        assert len(env) == 1, f"{arm_name} is not a one-knob arm: {sorted(env)}"
        value = next(iter(env.values()))
        shipped = {
            preset[preset_key]
            for preset in TOURNAMENT_PRESETS.values()
            if preset_key in preset
        }
        assert shipped, f"no preset ships {preset_key}"
        assert any(_i487_same_setting(value, candidate) for candidate in shipped), (
            f"{arm_name} states {value!r}, which no preset ships for {preset_key}"
        )


def test_i487_plan_names_ten_fresh_stores_and_ports(tmp_path):
    from scripts.shadow_tournament import build_tournament_plan

    arms = _i487_arms()
    plan = build_tournament_plan(
        issue=487, arms=arms, base_dir=tmp_path, check_ports=False
    )
    assert [a.name for a in plan.arms] == list(I487_ARM_ENV)
    assert [a.dash_port for a in plan.arms] == list(range(8801, 8811))
    assert len({str(a.db_path) for a in plan.arms}) == 10
    assert len({a.run_id for a in plan.arms}) == 10
    for arm in plan.arms:
        assert Path(arm.db_path).name == f"{arm.name}.db"
        assert Path(arm.db_path).name != "orders.db"
        assert Path(arm.db_path).parent == plan.record_path.parent

