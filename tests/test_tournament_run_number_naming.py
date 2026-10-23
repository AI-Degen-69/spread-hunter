"""Tournament artifacts: one folder per run (docs/agents/naming.md, grammar G3).

    configs/tournaments/<key>_arms.json        arm list (input, hand-written)
    data/tournaments/<key>_<stamp>/            everything the run produced
        plan.json
        launch.log
        results.json
        <arm>.db

`<key>` is `i<issue>-r<run>` for a numbered run, else `i<issue>` or
`i<issue>-<name>` from `--stamp`. The stamp is always the ISO launch stamp, in
the folder name: two runs of the same number must not overwrite each other.

Before the folder, one tournament was spread over `configs/`, `runtime/`,
`reports/` and `data/` under three filename grammars -- and the stores sat in
`data/` among `orders.db`. Answering "what did i492-r1 produce" meant four
lookups, and deleting one tournament meant picking its files out by hand.
"""
from __future__ import annotations

from pathlib import Path

from core_brain.shadow_run import (
    build_tournament_db_path_v2,
    build_tournament_log_path,
    build_tournament_record_path,
    build_tournament_results_path,
    build_tournament_run_dir,
    build_tournament_run_id_v2,
    latest_tournament_record,
    parse_tournament_db_path,
    parse_tournament_run_dir_name,
    tournament_key,
)

STAMP = "2026-10-05T03-20"
RUN_DIR = f"i492-r1_{STAMP}"


def test_key_shapes():
    assert tournament_key(492, run_number=1) == "i492-r1"
    assert tournament_key(487, name="current_setup_10arm") == "i487-current-setup-10arm"
    assert tournament_key(371) == "i371"


def test_the_run_folder_carries_the_key_and_the_stamp():
    assert build_tournament_run_dir(492, STAMP, run_number=1, base_dir="data/tournaments") == (
        Path("data/tournaments") / RUN_DIR
    )
    # Same run number, different launch: two folders, no overwrite.
    assert build_tournament_run_dir(492, "2026-10-06T09-00", run_number=1, base_dir="data/tournaments")
    assert (
        build_tournament_run_dir(492, STAMP, run_number=1, base_dir="data/tournaments").name
        != build_tournament_run_dir(
            492, "2026-10-06T09-00", run_number=1, base_dir="data/tournaments"
        ).name
    )


def test_every_generated_file_sits_in_the_run_folder():
    record = build_tournament_record_path(492, STAMP, run_number=1)
    log = build_tournament_log_path(492, STAMP, run_number=1)
    results = build_tournament_results_path(492, STAMP, run_number=1)
    store = build_tournament_db_path_v2(492, "control-1", run_number=1, stamp=STAMP)

    for path in (record, log, results, store):
        assert path.parent.name == RUN_DIR
        assert path.parent.parent.name == "tournaments"
        assert path.parent.parent.parent.name == "data"

    # The folder says which run; the names inside say what each file is.
    assert record.name == "plan.json"
    assert log.name == "launch.log"
    assert results.name == "results.json"
    assert store.name == "control-1.db"


def test_a_named_run_still_carries_the_iso_stamp():
    # `--stamp current_setup_10arm` names the run; it does not replace the
    # timestamp, so the folder sorts and can never collide with a repeat.
    record = build_tournament_record_path(487, STAMP, name="current_setup_10arm")
    assert record.parent.name == f"i487-current-setup-10arm_{STAMP}"
    assert record.name == "plan.json"


def test_v2_run_id_shape():
    run_id = build_tournament_run_id_v2(issue=492, arm="conservative-1", run_number=1)
    assert run_id == "shadow-492-tournament-1-conservative-1"
    assert len(run_id) <= 64
    assert run_id.startswith("shadow-")


def test_parse_a_run_folder_name():
    assert parse_tournament_run_dir_name(RUN_DIR) == {
        "issue": 492,
        "run_number": 1,
        "name": None,
        "stamp": STAMP,
    }
    assert parse_tournament_run_dir_name("i608-r2_2026-10-17T18-48") == {
        "issue": 608,
        "run_number": 2,
        "name": None,
        "stamp": "2026-10-17T18-48",
    }
    assert parse_tournament_run_dir_name("i487-current-setup-10arm_2026-10-01T09-00") == {
        "issue": 487,
        "run_number": None,
        "name": "current-setup-10arm",
        "stamp": "2026-10-01T09-00",
    }
    # Not a run folder: an unnumbered key with no stamp, and the old generations.
    assert parse_tournament_run_dir_name("i492-r1") is None
    assert parse_tournament_run_dir_name("tournament_i608-r2_2026-10-17T18-48.json") is None
    assert parse_tournament_run_dir_name("608_20261017-184837") is None


def test_parse_a_store_inside_a_run_folder():
    parsed = parse_tournament_db_path(f"data/tournaments/{RUN_DIR}/control-1.db")
    assert parsed == {
        "issue": 492,
        "run_number": 1,
        "name": None,
        "arm": "control-1",
        "stamp": STAMP,
        "index": None,
    }


def test_parse_still_rejects_non_tournament():
    assert parse_tournament_db_path("data/orders.db") is None
    assert parse_tournament_db_path("data/NN_shadow_20261004.db") is None
    assert parse_tournament_db_path("") is None
    assert parse_tournament_db_path(None) is None


def test_stores_from_before_the_folder_still_parse():
    """Files already on disk keep parsing; the writers no longer produce them."""
    legacy = parse_tournament_db_path("data/484_tournament_01_control_20261011-120000.db")
    assert legacy is not None
    assert legacy["issue"] == 484
    assert legacy["arm"] == "control"
    assert legacy["index"] == 1

    current = parse_tournament_db_path("data/484_tournament_01_control_2026-10-11T12-00.db")
    assert current is not None
    assert current["stamp"] == "2026-10-11T12-00"

    flat = parse_tournament_db_path("data/tournament_i492-r1_control-1.db")
    assert flat is not None
    assert flat["issue"] == 492
    assert flat["run_number"] == 1


def test_latest_run_folder_ranks_on_fields_not_on_the_name(tmp_path: Path):
    """Highest run wins, then newest stamp -- never string order.

    The old ranked-by-string readers picked a named record last only because
    `c` sorts after a digit, and would have picked run 10 before run 9.
    """
    for folder in (
        "i608-r1_2026-10-17T18-48",
        "i608-r9_2026-10-18T09-00",
        "i608-r10_2026-10-19T09-00",
        "i608-current-setup_2026-10-20T09-00",
        "i608-r10_2026-10-19T08-00",
        "i609-r99_2026-10-21T09-00",  # another issue
        "i608-r11_2026-10-21T09-00",  # a folder with no plan is not a plan
    ):
        run_dir = tmp_path / folder
        run_dir.mkdir()
        if folder != "i608-r11_2026-10-21T09-00":
            (run_dir / "plan.json").write_text("{}", encoding="utf-8")

    latest = latest_tournament_record(608, tmp_path)
    assert latest is not None
    assert latest == tmp_path / "i608-r10_2026-10-19T09-00" / "plan.json"


def test_latest_run_ignores_a_longer_issue_number(tmp_path: Path):
    """`i60*` also matches `i608`, so the parsed id has to decide.

    Without the check the higher run number of the *other* issue wins, and the
    cockpit reads a plan that belongs to a different tournament.
    """
    for folder in ("i608-r9_2026-10-19T09-00", "i60-r1_2026-10-01T09-00"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "plan.json").write_text("{}", encoding="utf-8")

    latest = latest_tournament_record(60, tmp_path)
    assert latest is not None
    assert latest.parent.name == "i60-r1_2026-10-01T09-00"


def test_build_plan_puts_everything_in_one_folder(tmp_path: Path):
    from scripts.shadow_tournament import build_tournament_plan

    plan = build_tournament_plan(
        issue=492,
        arms=[{"name": "baseline"}, {"name": "conservative"}],
        base_port=8801,
        base_dir=tmp_path,
        check_ports=False,
        run_number=1,
    )
    run_dir = tmp_path / f"i492-r1_{plan.stamp}"
    assert plan.run_number == 1
    assert [a.name for a in plan.arms] == ["baseline-1", "conservative-1"]
    assert plan.record_path == run_dir / "plan.json"
    assert plan.results_path == run_dir / "results.json"
    assert plan.log_path == run_dir / "launch.log"
    assert plan.arms[0].db_path == run_dir / "baseline-1.db"
    assert plan.arms[1].db_path == run_dir / "conservative-1.db"
    assert plan.arms[0].run_id == "shadow-492-tournament-1-baseline-1"
    assert plan.to_dict()["log_path"] == str(plan.log_path)
    assert plan.record_path.is_absolute()
    # Base name still drives the preset selector, not the suffixed slug.
    assert plan.arms[0].env["BOT_QUOTE_TOURNAMENT_PRESET"] == "baseline"


def test_build_plan_run_number_two(tmp_path: Path):
    from scripts.shadow_tournament import build_tournament_plan

    plan = build_tournament_plan(
        issue=492,
        arms=[{"name": "snapstop"}],
        base_port=8801,
        base_dir=tmp_path,
        check_ports=False,
        run_number=2,
    )
    assert plan.arms[0].name == "snapstop-2"
    assert plan.arms[0].db_path.name == "snapstop-2.db"
    assert plan.record_path.parent.name == f"i492-r2_{plan.stamp}"


def test_an_unnumbered_run_also_drops_the_arm_index(tmp_path: Path):
    """No run number: the folder carries the identity, so the store is `<arm>.db`.

    The old indexed shape (`371_tournament_01_conservative_<stamp>.db`) existed
    only so the dashboard's clustering parser could read the arm index out of
    the filename. The folder and the plan record carry it now.
    """
    from scripts.shadow_tournament import build_tournament_plan

    plan = build_tournament_plan(
        issue=371,
        base_port=8801,
        base_dir=tmp_path,
        stamp=STAMP,
        check_ports=False,
    )
    assert plan.run_number is None
    assert [a.name for a in plan.arms][:2] == ["conservative", "balanced"]
    assert plan.arms[0].db_path == tmp_path / f"i371_{STAMP}" / "conservative.db"
    assert plan.record_path == tmp_path / f"i371_{STAMP}" / "plan.json"
    assert plan.results_path == tmp_path / f"i371_{STAMP}" / "results.json"


def test_build_plan_named_run(tmp_path: Path):
    from scripts.shadow_tournament import build_tournament_plan

    plan = build_tournament_plan(
        issue=487,
        base_port=8801,
        base_dir=tmp_path,
        stamp=STAMP,
        run_name="current_setup_10arm",
        check_ports=False,
    )
    assert plan.record_path.parent.name == f"i487-current-setup-10arm_{STAMP}"
    assert plan.results_path.parent == plan.record_path.parent
