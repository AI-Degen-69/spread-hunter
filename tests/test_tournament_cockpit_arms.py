"""The Tournament Cockpit's #487 arm table and its plan-record resolver.

Two things an operator reads on `http://127.0.0.1:8800/487` depend on these:
the ten arms with the hypothesis each one tests, and the db/run_id/port the run
actually wrote. The resolver must also keep serving #492 and #608 from their own
record forms -- that path is a frozen contract, so the last test pins it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import tournament_cockpit_server as cockpit  # noqa: E402

I487_ARMS_SPEC = ROOT / "configs" / "tournaments" / "i487_current_setup_10arm_arms.json"


def _spec_arm_names() -> list[str]:
    return [a["name"] for a in json.loads(I487_ARMS_SPEC.read_text(encoding="utf-8"))]


def _record(tmp_path: Path, filename: str, arms: list[dict]) -> Path:
    """Drop a plan record into the temporary tournaments directory."""
    tourn_dir = tmp_path / "runtime" / "tournaments"
    tourn_dir.mkdir(parents=True, exist_ok=True)
    path = tourn_dir / filename
    path.write_text(json.dumps({"arms": arms}), encoding="utf-8")
    return path


def test_i487_arm_table_names_the_ten_spec_arms_with_their_hypotheses():
    arms = cockpit.ARMS_CONFIG_487
    assert [a["name"] for a in arms] == _spec_arm_names()
    assert len(arms) == 10
    assert [a["port"] for a in arms] == list(range(8801, 8811))
    for arm in arms:
        assert arm["hypothesized_tweak"], arm["name"]
        assert arm["tweak_rationale"], arm["name"]
        # No forensic run exists, so a catalyst score here would be invented.
        assert arm["worst_catalyst"] == "", arm["name"]
        assert arm["worst_catalyst_desc"] == "", arm["name"]


def test_i487_each_arm_tweak_names_the_one_knob_the_spec_file_sets():
    """The cockpit prose and the executable env live in two files.

    Names alone are not enough: a value edited in one file and not the other
    would misreport what the arm actually ran, which is the whole promise the
    table makes to the operator.
    """
    spec = {
        a["name"]: a.get("env", {})
        for a in json.loads(I487_ARMS_SPEC.read_text(encoding="utf-8"))
    }
    arms = {a["name"]: a for a in cockpit.ARMS_CONFIG_487}
    assert spec["control"] == {}, "the control arm's entire point is an empty env"
    for name, env in spec.items():
        if not env:
            continue
        assert len(env) == 1, f"{name} moves {len(env)} settings, not one"
        (key, value), = env.items()
        assert key.startswith("BOT_"), (name, key)
        assert arms[name]["hypothesized_tweak"] == f"{key.removeprefix('BOT_').lower()} = {value}", name


def test_i487_resolves_the_named_plan_record(tmp_path, monkeypatch):
    monkeypatch.setattr(cockpit, "ROOT", tmp_path)
    _record(tmp_path, "487_current_setup_10arm.json", [
        {
            "name": "control",
            "db_path": "data/real_control.db",
            "run_id": "shadow-real-control",
            "dash_port": 9901,
        },
        {
            "name": "price-risk-030",
            "db_path": "data/real_price_risk.db",
            "run_id": "shadow-real-price-risk",
            "dash_port": 9908,
        },
    ])
    arms = {a["name"]: a for a in cockpit.resolve_tournament_arms("487")}
    assert list(arms) == _spec_arm_names()
    # db/run_id/port come from the record for the arms it names
    assert arms["control"]["db"] == "data/real_control.db"
    assert arms["control"]["run_id"] == "shadow-real-control"
    assert arms["control"]["port"] == 9901
    assert arms["price-risk-030"]["db"] == "data/real_price_risk.db"
    assert arms["price-risk-030"]["port"] == 9908
    # an arm the record omits keeps its static placeholder AND its prose
    assert arms["widen-on"]["db"] == (
        "data/487_tournament_07_widen-on_current_setup_10arm.db")
    assert arms["widen-on"]["hypothesized_tweak"] == "quote_gate_widen_on = 1"


def test_i487_prefers_the_named_record_over_a_leftover_timestamped_one(tmp_path, monkeypatch):
    """Both record shapes can sit in the folder; the named one is this run.

    A timestamped `487_<stamp>.json` could only be the superseded catalyst round,
    so the resolver must read db/run_id/port from the named record. That choice is
    a sort, so it gets a test rather than a comment.
    """
    monkeypatch.setattr(cockpit, "ROOT", tmp_path)
    _record(tmp_path, "487_20261018-140000.json", [{
        "name": "control",
        "db_path": "data/stale_control.db",
        "run_id": "shadow-stale-control",
        "dash_port": 9999,
    }])
    _record(tmp_path, "487_current_setup_10arm.json", [{
        "name": "control",
        "db_path": "data/named_control.db",
        "run_id": "shadow-named-control",
        "dash_port": 8801,
    }])
    arms = {a["name"]: a for a in cockpit.resolve_tournament_arms("487")}
    assert arms["control"]["db"] == "data/named_control.db"
    assert arms["control"]["run_id"] == "shadow-named-control"
    assert arms["control"]["port"] == 8801


def test_i487_without_a_record_falls_back_to_the_static_table(tmp_path, monkeypatch):
    monkeypatch.setattr(cockpit, "ROOT", tmp_path)
    assert cockpit.resolve_tournament_arms("487") == cockpit.ARMS_CONFIG_487


def test_492_and_608_still_resolve_from_their_run_numbered_records(tmp_path, monkeypatch):
    monkeypatch.setattr(cockpit, "ROOT", tmp_path)
    _record(tmp_path, "i492_tournament-2.json", [{
        "name": "control-1",
        "db_path": "data/real492.db",
        "run_id": "shadow-real-492",
        "dash_port": 8901,
    }])
    _record(tmp_path, "i608_tournament-1.json", [{
        # The real #608 record names its arms WITHOUT the round suffix the
        # cockpit's static table carries; the resolver strips it to match.
        "name": "the-accountant",
        "db_path": "data/real608.db",
        "run_id": "shadow-real-608",
        "dash_port": 8902,
    }])
    # A #487 named record sitting beside them must not bleed into either key.
    _record(tmp_path, "487_current_setup_10arm.json", [])

    c492 = {a["name"]: a for a in cockpit.resolve_tournament_arms("492")}
    assert len(c492) == len(cockpit.ARMS_CONFIG_492)
    assert c492["control-1"]["db"] == "data/real492.db"
    assert c492["control-1"]["run_id"] == "shadow-real-492"
    assert c492["control-1"]["port"] == 8901

    c608 = {a["name"]: a for a in cockpit.resolve_tournament_arms("608")}
    assert len(c608) == len(cockpit.ARMS_CONFIG_608)
    assert c608["the-accountant-1"]["db"] == "data/real608.db"
    assert c608["the-accountant-1"]["run_id"] == "shadow-real-608"
    assert c608["the-accountant-1"]["port"] == 8902
