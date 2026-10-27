"""Re-evaluation of a finished tournament run from its own arm databases.

The tool exists because the arm notes and the cockpit matrix carry figures
written from an interim snapshot. These tests pin what the tool must read from
the stores themselves: the per-method PnL split, the pre-registered risk-shape
gates, the sample-power flag, and the leader the ranking picks.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.reevaluate_tournament_run import (  # noqa: E402
    evaluate_run,
    main,
    render_text,
    resolve_plan_path,
)
from tests.test_tournament_cockpit_funnel import SCHEMA  # noqa: E402

#: A winning arm: tight merges, small rescue haircuts. Sharpe ~8, profit factor
#: ~5, drawdown under $1 -- every risk-shape gate clears, the power gate cannot
#: (22 closes against the ~73 a $0.02 effect would need at this sigma).
GATED_RUN_ID = "shadow-test-t10-queue-clear-5m"
GATED_CLOSES = (
    [(1000.0 + i, "shadow_merge", 0.10) for i in range(16)]
    + [(2000.0 + i, "single_buy_exit", -0.05) for i in range(6)]
    + [(3000.0, "shadow_settlement", 0.25), (3001.0, "shadow_settlement", 0.25)]
)

#: The control: the same merges, but every rescue exit is a full-leg loss, so it
#: fails all three risk-shape gates.
CONTROL_RUN_ID = "shadow-test-t01-control"
CONTROL_CLOSES = (
    [(1000.0 + i, "shadow_merge", 0.10) for i in range(15)]
    + [(2000.0 + i, "single_buy_exit", -1.00) for i in range(15)]
)


def _seed_arm(path: Path, run_id: str, closes: list[tuple[float, str, float]]) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    # Two marks an hour apart: the analyzer reads the run window from here.
    conn.execute(
        "INSERT INTO queue_marks (ts, run_id) VALUES (1000.0, ?), (4600.0, ?)",
        (run_id, run_id),
    )
    conn.executemany(
        "INSERT INTO closes (ts, method, realized_pnl, run_id) VALUES (?, ?, ?, ?)",
        [(ts, method, pnl, run_id) for ts, method, pnl in closes],
    )
    conn.commit()
    conn.close()


def _run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "i799-tournament-1m_2026-10-26T00-12"
    run_dir.mkdir()
    for name, run_id, closes in (
        ("control", CONTROL_RUN_ID, CONTROL_CLOSES),
        ("queue-clear-5m", GATED_RUN_ID, GATED_CLOSES),
    ):
        _seed_arm(run_dir / f"{name}.db", run_id, closes)
    plan = {
        "issue": 799,
        "stamp": "2026-10-26T00-12",
        "minutes": 1440.0,
        "arms": [
            {
                "name": "control",
                "db_path": str(run_dir / "control.db"),
                "run_id": CONTROL_RUN_ID,
                "dash_port": 8801,
                "env": {},
            },
            {
                "name": "queue-clear-5m",
                "db_path": str(run_dir / "queue-clear-5m.db"),
                "run_id": GATED_RUN_ID,
                "dash_port": 8802,
                "env": {
                    "BOT_ORDER_GATE_QUEUE_CLEAR_ON": "1",
                    "BOT_ORDER_QUEUE_MAX_CLEAR_MIN": "5",
                },
            },
        ],
    }
    (run_dir / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    return run_dir


def _arm(ev: dict, name: str) -> dict:
    return next(a for a in ev["arms"] if a["name"] == name)


def test_leader_is_the_arm_that_clears_the_risk_shape_gates(tmp_path):
    ev = evaluate_run(_run_dir(tmp_path) / "plan.json")

    gated = _arm(ev, "queue-clear-5m")
    # Every close the analyzer counts is a trade: a shadow settlement is a
    # position that resolved, so it counts here (only plain `settlement` is
    # bookkeeping).
    assert gated["trades"] == 24
    assert gated["merges"] == 16
    assert gated["stop_exits"] == 6
    assert gated["gates"] == {
        "sharpe": True,
        "profit_factor": True,
        "max_drawdown": True,
        "powered": False,
    }
    assert gated["risk_gates_passed"] == 3

    control = _arm(ev, "control")
    assert control["gates"] == {
        "sharpe": False,
        "profit_factor": False,
        "max_drawdown": False,
        "powered": False,
    }
    assert control["risk_gates_passed"] == 0
    assert control["total_pnl"] < 0.0 < gated["total_pnl"]

    assert ev["ranked"][0] == "queue-clear-5m"
    assert ev["leader"] == "queue-clear-5m"


def test_sample_power_is_reported_rather_than_assumed(tmp_path):
    """A 22-hour arm is underpowered; the tool says so instead of ranking on PnL."""
    ev = evaluate_run(_run_dir(tmp_path) / "plan.json")
    gated = _arm(ev, "queue-clear-5m")

    n_required = gated["n_required_002"]
    assert isinstance(n_required, int) and n_required > 0
    assert gated["trades"] < n_required
    assert gated["gates"]["powered"] is False


def test_pnl_is_split_by_close_method(tmp_path):
    """Merges, rescue exits and settlements stay separable -- they are not one number."""
    ev = evaluate_run(_run_dir(tmp_path) / "plan.json")
    by_method = _arm(ev, "queue-clear-5m")["by_method"]

    assert by_method["merges"] == {"n": 16, "pnl": 1.6}
    assert by_method["rescues"] == {"n": 6, "pnl": -0.3}
    assert by_method["settlements"] == {"n": 2, "pnl": 0.5}
    assert by_method["other"] == {"n": 0, "pnl": 0.0}


def test_missing_plan_record_exits_nonzero(tmp_path):
    assert resolve_plan_path(799, tmp_path) is None
    assert main(["--run-dir", str(tmp_path)]) == 1


def test_verdict_path_uses_the_repo_gate_and_reports_no_data(tmp_path, monkeypatch, capsys):
    """--verdict attaches the repo's verdict per arm; without it the seam stays shut."""
    import scripts.reevaluate_tournament_run as tool

    calls = []

    def fake_write(db_path, run_id, mode, out_dir=None):
        calls.append((run_id, mode))
        assert mode == "shadow"
        # The gate reads a scratch copy, never the arm store itself.
        assert "reeval-run-" in str(db_path)
        return {
            "verdict": "NO-GO",
            "gate_rows": [{"gate": tool.PRIMARY_GATE_NAME, "value": -2.5, "passed": False}],
        }

    monkeypatch.setattr(tool, "write_statistics_report", fake_write)
    run_dir = _run_dir(tmp_path)
    assert main(["--run-dir", str(run_dir), "--verdict", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    gated = _arm(payload, "queue-clear-5m")
    assert gated["verdict"] == "NO-GO"
    assert gated["primary_gate_value"] == -2.5
    assert len(calls) == 2

    assert main(["--run-dir", str(run_dir), "--verdict"]) == 0
    assert "Repo gate verdicts" in capsys.readouterr().out

    def boom(*args, **kwargs):
        raise AssertionError("the gate seam must stay shut without --verdict")

    monkeypatch.setattr(tool, "write_statistics_report", boom)
    ev = evaluate_run(run_dir / "plan.json")
    assert _arm(ev, "queue-clear-5m")["verdict"] is None


def test_verdict_reports_no_data_for_a_missing_store(tmp_path, monkeypatch):
    """An arm whose store is gone reports NO_DATA, never a fabricated verdict."""
    import scripts.reevaluate_tournament_run as tool

    def boom(*args, **kwargs):
        raise AssertionError("no store, no gate call")

    monkeypatch.setattr(tool, "write_statistics_report", boom)
    missing = tmp_path / "gone.db"
    repo = tool._repo_verdict(missing, "shadow-test-t99-missing")
    assert repo == {"verdict": "NO_DATA", "primary_gate_value": None}


def test_json_output_and_text_report_agree_on_the_leader(tmp_path, capsys):
    run_dir = _run_dir(tmp_path)
    assert main(["--run-dir", str(run_dir), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["leader"] == "queue-clear-5m"
    assert payload["issue"] == 799

    assert main(["--run-dir", str(run_dir)]) == 0
    text = capsys.readouterr().out
    assert "Prime candidate by the pre-registered rule: queue-clear-5m" in text
    assert "control" in text
    assert "S=Sharpe>=2.70" in text

    # The renderer is the only consumer of the ranking key; a missing one would
    # crash here rather than at the operator's terminal.
    assert render_text(payload)
