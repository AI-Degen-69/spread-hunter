"""Re-evaluate a finished tournament run from its own arm databases.

The cockpit reads its arms through ``resolve_tournament_arms`` and falls back to
static presets when a record is missing, and the arm notes in
``configs/tournaments/*_arms.json`` were written from an interim snapshot. This
tool answers a narrower question with the stores themselves: for one run
directory, what did each arm actually close, and does it clear the
pre-registered decision gates?

Read-only. It opens every arm database with ``mode=ro`` and never writes.

Usage:
    python -m scripts.reevaluate_tournament_run --issue 799
    python -m scripts.reevaluate_tournament_run --run-dir data/tournaments/i799-... --json
    python -m scripts.reevaluate_tournament_run --run-dir <run dir> --verdict

Exit codes: 0 when the run was read and evaluated, 1 when it could not be
(no plan record, no arms, unreadable store).
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core_brain.close_methods import (  # noqa: E402
    MERGE_METHODS,
    RESCUE_METHODS,
    SETTLEMENT_METHODS,
)
from core_brain.kpi import (  # noqa: E402
    Z_ALPHA_90_ONE_SIDED,
    Z_BETA_80_POWER,
    required_sample_size,
)
from core_brain.shadow_run import latest_tournament_record  # noqa: E402
from core_brain.statistics_report import write_statistics_report  # noqa: E402
from scripts.tournament_cockpit_server import analyze_arm_database  # noqa: E402

#: The effect size the pre-registered rehearsals commit to resolving:
#: $0.02 of per-trade PnL, per docs/runs/2026-10-26-843-balanced-queue5-pair.md.
DELTA_USD = 0.02

#: Pre-registered risk-shape gates (same thresholds the cockpit's decision
#: gates evaluate), asserted per arm rather than assumed for the run.
GATE_SHARPE_MIN = 2.70
GATE_PROFIT_FACTOR_MIN = 2.50
GATE_MAX_DRAWDOWN_USD = 4.00

#: The primary gate row's name in ``write_statistics_report``'s ``gate_rows``.
PRIMARY_GATE_NAME = "Primary \u2014 total PnL (inclusive)"


def resolve_plan_path(issue: int | str, run_dir: str | Path | None = None) -> Path | None:
    """The plan record to evaluate: an explicit run directory or the newest run."""
    if run_dir is not None:
        candidate = Path(run_dir)
        plan = candidate / "plan.json"
        return plan if plan.is_file() else None
    return latest_tournament_record(issue)


def _pnl_by_method(db_path: Path, run_id: str) -> dict[str, Any]:
    """Measured closes and PnL split by close method for one arm's run.

    The split matters more than the total for a migration read: a merged pair is
    the strategy working, a rescue exit is the strategy paying to undo a naked
    leg, and a settlement is neither.
    """
    out: dict[str, Any] = {"merges": [], "rescues": [], "settlements": [], "other": []}
    try:
        with contextlib.closing(
            sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
        ) as conn:
            rows = conn.execute(
                "SELECT method, realized_pnl FROM closes WHERE run_id = ? "
                "AND method IS NOT NULL AND method != ''",
                (run_id,),
            ).fetchall()
    except sqlite3.Error as exc:
        return {**out, "error": str(exc)}

    slots = {
        "merges": MERGE_METHODS,
        "rescues": RESCUE_METHODS,
        "settlements": SETTLEMENT_METHODS,
    }
    for method, pnl in rows:
        bucket = next((k for k, names in slots.items() if method in names), "other")
        out[bucket].append(float(pnl or 0.0))
    return {
        k: {"n": len(v), "pnl": round(sum(v), 4)} if k != "error" else v
        for k, v in out.items()
    }


def _repo_verdict(db_path: Path, run_id: str) -> dict[str, Any]:
    """The repo's own gate verdict for one arm, computed on a scratch copy.

    ``write_statistics_report`` opens its store through ``OrderRegistry``
    (read-write DDL) and drops a markdown report beside it, so it never runs
    against the arm store itself: the store's pages are copied to a temp dir
    with the SQLite backup API — a file copy would miss uncheckpointed WAL
    frames — the report lands in the same temp dir, and both vanish with it.
    The verdict and gate rows do not depend on where the copy lives, so what
    comes back is exactly what the repo's gate says about this arm.
    """
    if not db_path.is_file():
        return {"verdict": "NO_DATA", "primary_gate_value": None}

    saved_env = {
        k: os.environ.get(k)
        for k in ("BOT_EVAL_STAT_GATE_THRESHOLD_PCT", "BOT_EVAL_STAT_GATE_TARGET_CLOSES")
    }
    scratch: Path | None = None
    try:
        # Enforce exact tournament audit gates: 1.0% threshold, 60 target closes
        os.environ["BOT_EVAL_STAT_GATE_THRESHOLD_PCT"] = "1.0"
        os.environ["BOT_EVAL_STAT_GATE_TARGET_CLOSES"] = "60"
        with tempfile.TemporaryDirectory(prefix="reeval-run-") as tmp:
            scratch = Path(tmp) / db_path.name
            try:
                with contextlib.closing(
                    sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
                ) as src, contextlib.closing(sqlite3.connect(scratch)) as dst:
                    src.backup(dst)
                rep = write_statistics_report(scratch, run_id, "shadow", out_dir=tmp)
            finally:
                if scratch and scratch.exists():
                    scratch.unlink(missing_ok=True)
    except Exception as exc:
        return {"verdict": "ERROR", "primary_gate_value": None, "error": str(exc)}
    finally:
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    primary = next(
        (r for r in rep.get("gate_rows", []) if r.get("gate") == PRIMARY_GATE_NAME),
        None,
    )
    return {
        "verdict": rep.get("verdict"),
        "primary_gate_value": (primary or {}).get("value"),
    }


def evaluate_run(plan_path: Path, with_verdict: bool = False) -> dict[str, Any]:
    """Per-arm metrics, gate verdicts and the leader for one run's plan record."""
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    minutes = float(plan.get("minutes") or 720.0)
    run_dir = Path(plan_path).parent

    unreadable: list[dict[str, Any]] = []
    arms: list[dict[str, Any]] = []
    for arm in plan.get("arms") or []:
        db_path = Path(arm.get("db_path") or "")
        cfg = {
            "name": arm.get("name", ""),
            "db": str(db_path),
            "run_id": arm.get("run_id", ""),
            "port": arm.get("dash_port", 0),
            "env": arm.get("env", {}),
        }
        metrics = analyze_arm_database(cfg, target_minutes=minutes)
        if metrics.get("status") in {"missing_db", "error"}:
            unreadable.append({
                "name": cfg["name"],
                "status": metrics["status"],
                "error": metrics.get("error", "missing or unreadable arm database"),
            })
            continue
        split = _pnl_by_method(db_path, cfg["run_id"]) if db_path.is_file() else {}
        sigma = float(metrics.get("std_pnl") or 0.0)
        n_required = required_sample_size(sigma, DELTA_USD, Z_ALPHA_90_ONE_SIDED, Z_BETA_80_POWER)
        trades = int(metrics.get("trades_count") or 0)
        risk_gates = {
            "sharpe": round(float(metrics.get("sharpe_proxy") or 0.0), 2) >= GATE_SHARPE_MIN,
            "profit_factor": round(float(metrics.get("profit_factor") or 0.0), 2)
            >= GATE_PROFIT_FACTOR_MIN,
            "max_drawdown": float(metrics.get("max_drawdown") or 0.0) < GATE_MAX_DRAWDOWN_USD,
        }
        gates = {
            **risk_gates,
            "powered": bool(n_required) and trades >= int(n_required),
        }
        arms.append(
            {
                "name": cfg["name"],
                "run_id": cfg["run_id"],
                "db_path": str(db_path),
                "env": cfg["env"],
                "status": metrics.get("status"),
                "elapsed_min": round(float(metrics.get("elapsed_min") or 0.0), 1),
                "progress_pct": metrics.get("progress_pct"),
                "trades": trades,
                "merges": metrics.get("merges_count", 0),
                "stop_exits": metrics.get("stop_loss_exits_count", 0),
                "quotes": metrics.get("quoted_cnt", 0),
                "fills": metrics.get("fills_cnt", 0),
                "merge_rate_pct": metrics.get("merge_rate", 0.0),
                "total_pnl": metrics.get("total_pnl", 0.0),
                "mean_pnl": metrics.get("mean_pnl", 0.0),
                "sigma": round(sigma, 4),
                "sharpe_proxy": metrics.get("sharpe_proxy", 0.0),
                "profit_factor": metrics.get("profit_factor", 0.0),
                "max_drawdown": metrics.get("max_drawdown", 0.0),
                "var_95": metrics.get("var_95"),
                "mc_p5": metrics.get("mc_p5"),
                "mc_p50": metrics.get("mc_p50"),
                "prob_positive_100_pct": metrics.get("prob_positive_100"),
                "score": metrics.get("score"),
                "readiness": metrics.get("readiness"),
                "by_method": split,
                "n_required_002": n_required,
                "verdict": None,
                "primary_gate_value": None,
                "gates": gates,
                "risk_gates_passed": sum(1 for v in risk_gates.values() if v),
            }
        )

    if with_verdict:
        for arm_record, arm in zip(arms, plan.get("arms") or []):
            repo = _repo_verdict(
                Path(arm.get("db_path") or ""), arm_record["run_id"]
            )
            arm_record["verdict"] = repo["verdict"]
            arm_record["primary_gate_value"] = repo["primary_gate_value"]
            if "error" in repo:
                arm_record["verdict_error"] = repo["error"]

    has_go = any(a.get("verdict") == "GO" for a in arms)
    any_powered = any(a["gates"].get("powered") for a in arms)

    if has_go:
        # Candidate ranking on risk shape is used only if no arm earns GO.
        # When an arm earns GO, base ranking and recommendation on that verdict.
        ranked = sorted(
            arms,
            key=lambda a: (
                1 if a.get("verdict") == "GO" else 0,
                float(a.get("primary_gate_value") or 0.0),
                a["total_pnl"],
            ),
            reverse=True,
        )
        caveat = "Modelled shadow results. Leader approved by statistical GO gate."
    else:
        # Ranked on risk shape, per the pre-registered rule: at a 24-hour sample no
        # arm clears the power gate (or no arm earns GO), so nominal PnL cannot separate
        # them and the ordering is the risk-shape gates first, then Sharpe, then realised PnL.
        ranked = sorted(
            arms,
            key=lambda a: (a["risk_gates_passed"], a["sharpe_proxy"], a["total_pnl"]),
            reverse=True,
        )
        if any_powered:
            caveat = (
                "Modelled shadow results. No arm earned a statistical GO verdict; "
                "ranked by pre-registered risk shape."
            )
        else:
            caveat = (
                "Modelled shadow results. No arm clears the pre-registered sample power "
                "gate, so nominal PnL is not a winner signal here: read risk shape only."
            )

    return {
        "issue": plan.get("issue"),
        "stamp": plan.get("stamp"),
        "minutes_planned": minutes,
        "run_dir": str(run_dir),
        "arms": arms,
        "unreadable": unreadable,
        "ranked": [a["name"] for a in ranked],
        "leader": ranked[0]["name"] if ranked else None,
        "caveat": caveat,
    }


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def render_text(ev: dict[str, Any]) -> str:
    """Human-readable report: measured shape per arm plus the gate verdicts."""
    lines = [
        f"Run: issue #{ev['issue']} stamp {ev['stamp']} "
        f"({ev['minutes_planned']:.0f} min planned)",
        f"Dir: {ev['run_dir']}",
        "",
        f"{'arm':<20}{'hrs':>6}{'trades':>8}{'merges':>8}{'stops':>7}"
        f"{'merge%':>8}{'pnl':>9}{'sharpe':>8}{'PF':>7}{'MDD':>7}{'Nreq':>15}  gates",
    ]
    for a in ev["arms"]:
        gates = a["gates"]
        marks = (
            ("S" if gates["sharpe"] else "s")
            + ("P" if gates["profit_factor"] else "p")
            + ("D" if gates["max_drawdown"] else "d")
            + ("N" if gates["powered"] else "n")
        )
        n_req = (
            str(a["n_required_002"])
            if a.get("n_required_002") is not None
            else "not computable"
        )
        lines.append(
            f"{a['name']:<20}{a['elapsed_min'] / 60.0:>6.1f}{a['trades']:>8}{a['merges']:>8}"
            f"{a['stop_exits']:>7}{_fmt(a['merge_rate_pct']):>8}{_fmt(a['total_pnl']):>9}"
            f"{_fmt(a['sharpe_proxy']):>8}{_fmt(a['profit_factor']):>7}"
            f"{_fmt(a['max_drawdown']):>7}{n_req:>15}  {marks}"
        )
    has_go = any(a.get("verdict") == "GO" for a in ev["arms"])
    ranking_desc = "statistical GO" if has_go else "gate count, then Sharpe, then PnL"
    leader_desc = (
        "Approved arm by statistical GO"
        if has_go
        else "Prime candidate by the pre-registered rule"
    )
    lines += [
        "",
        "gates: S=Sharpe>=2.70  P=profit factor>=2.50  D=max drawdown<$4.00  "
        "N=trades>=N required for $0.02 (upper case = pass)",
        "",
        f"Ranked ({ranking_desc}): {', '.join(ev['ranked'])}",
        f"{leader_desc}: {ev['leader']}",
        "",
        ev["caveat"],
    ]
    if any(a.get("verdict") for a in ev["arms"]):
        lines += ["", "Repo gate verdicts (90% CI lower bound on inclusive mean return):"]
        for a in ev["arms"]:
            value = a.get("primary_gate_value")
            shown = f"{value:.3f}%" if isinstance(value, (int, float)) else "n/a"
            lines.append(f"  {a['name']:<20}{a['verdict']} ({shown})")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Re-derive a tournament run's per-arm metrics from its arm databases (read-only)."
    )
    parser.add_argument("--issue", default=799, help="Tournament issue number (default: 799)")
    parser.add_argument("--run-dir", default=None, help="Explicit run directory to evaluate")
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument(
        "--verdict",
        action="store_true",
        help="Attach the repo's own gate verdict per arm (runs on scratch copies; slower).",
    )
    args = parser.parse_args(argv)

    plan_path = resolve_plan_path(args.issue, args.run_dir)
    if plan_path is None:
        print(f"No tournament plan record found for issue {args.issue}", file=sys.stderr)
        return 1

    ev = evaluate_run(plan_path, with_verdict=args.verdict)
    if ev.get("unreadable"):
        for bad in ev["unreadable"]:
            detail = f": {bad['error']}" if bad.get("error") else ""
            print(f"Unreadable arm {bad['name']} ({bad['status']}){detail}", file=sys.stderr)
        return 1

    if not ev["arms"]:
        print(f"Plan {plan_path} lists no arms", file=sys.stderr)
        return 1

    if args.json_output:
        print(json.dumps(ev, indent=2, default=str))
    else:
        print(render_text(ev))
    return 0


if __name__ == "__main__":
    sys.exit(main())
