"""CLI report generator and automated invalidation engine for 10-tier bankroll sensitivity analysis."""
import os
import sys
import json
import sqlite3
from pathlib import Path
from strategy.stats import calc_confidence_intervals

ROOT = Path(__file__).resolve().parent.parent

def evaluate_bankroll_tier(bankroll: int, workdir: Path) -> dict:
    """Read tier DB, evaluate Student's t CIs, Sharpe/Sortino, and automated invalidation rules."""
    db_path = workdir / "fleet.db"
    status_path = workdir / "status.json"
    returns = []
    db_error = None

    if db_path.exists():
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            tables = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "closes" in tables:
                rows = conn.execute("SELECT realized_pnl FROM closes WHERE realized_pnl IS NOT NULL").fetchall()
                returns = [float(r[0]) for r in rows if r[0] is not None]
            elif "settled" in tables:
                rows = conn.execute("SELECT pnl_usd FROM settled WHERE pnl_usd IS NOT NULL").fetchall()
                returns = [float(r[0]) for r in rows if r[0] is not None]
            else:
                returns = []
            conn.close()
        except Exception as e:
            db_error = str(e)
            returns = []

    tier_status = "INITIALIZED"
    if status_path.exists():
        try:
            sdata = json.loads(status_path.read_text())
            tier_status = sdata.get("status", tier_status)
        except Exception:
            pass

    stats = calc_confidence_intervals(returns)
    sample_count = stats["count"]
    win_rate = (len([r for r in returns if r > 0]) / sample_count * 100.0) if sample_count > 0 else 0.0

    invalidation_reasons = []
    if db_error:
        invalidation_reasons.append(f"Database error: {db_error}")

    if sample_count >= 30:
        if stats["ci_95"]["upper"] < 0:
            invalidation_reasons.append("95% CI upper bound < 0%")
        if win_rate < 35.0:
            invalidation_reasons.append("Win rate < 35%")

    cum_sum = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for r in returns:
        cum_sum += r
        if cum_sum > peak:
            peak = cum_sum
        dd = (peak - cum_sum) / bankroll * 100.0
        if dd > max_drawdown:
            max_drawdown = dd

    if max_drawdown > 15.0:
        invalidation_reasons.append("Max drawdown > 15%")

    is_invalid = len(invalidation_reasons) > 0

    return {
        "bankroll": bankroll,
        "status": tier_status if not is_invalid else "INVALID",
        "sample_count": sample_count,
        "win_rate": win_rate,
        "max_drawdown": max_drawdown,
        "mean_return": stats["mean"],
        "std_dev": stats["std"],
        "sharpe": stats["sharpe"],
        "sortino": stats["sortino"],
        "ci_95": stats["ci_95"],
        "ci_98": stats["ci_98"],
        "is_invalid": is_invalid,
        "invalidation_reasons": invalidation_reasons,
    }

def generate_summary_report(run_dir: Path = ROOT / "run") -> list[dict]:
    """Generate summary evaluation report across all 10 bankroll tiers."""
    reports = []
    for amount in range(100, 1001, 100):
        workdir = run_dir / f"bankroll_{amount}"
        reports.append(evaluate_bankroll_tier(amount, workdir))
    return reports

def print_cli_report(reports: list[dict]):
    """Format and print ASCII summary table to console."""
    print("=" * 100)
    print(" 10-TIER BANKROLL SENSITIVITY EXPERIMENT REPORT")
    print("=" * 100)
    header = f"{'Bankroll':<10} | {'Samples':<8} | {'Win %':<7} | {'Mean ($)':<9} | {'95% CI ($)':<20} | {'Sortino':<8} | {'Status':<12}"
    print(header)
    print("-" * 100)
    for r in reports:
        ci_str = f"[{r['ci_95']['lower']:.2f}, {r['ci_95']['upper']:.2f}]"
        status_str = "INVALID" if r["is_invalid"] else ("VALID" if r["sample_count"] >= 30 else "COLLECTING")
        row = f"${r['bankroll']:<9} | {r['sample_count']:<8} | {r['win_rate']:<6.1f}% | {r['mean_return']:<9.2f} | {ci_str:<20} | {r['sortino']:<8.2f} | {status_str:<12}"
        print(row)
    print("=" * 100)

def main():
    json_mode = "--json" in sys.argv
    reports = generate_summary_report()
    if json_mode:
        print(json.dumps(reports, indent=2))
    else:
        print_cli_report(reports)

if __name__ == "__main__":
    main()
