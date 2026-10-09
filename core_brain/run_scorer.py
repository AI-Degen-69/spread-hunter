"""Run Scorer: Authenticity and Reliability scoring for shadow rehearsals and live trading.

Scores runs across core action dimensions to establish statistical confidence:
1. Orders placed: Quoting mechanics and liquidity posting.
2. Positions going: Order fills and active leg exposure.
3. Stop loss exits: Risk management, hard stops, and single-leg unwinds.
4. Positions merged: Spread-hunter arbitrage completion ($1.00 pair redemption).

Provides:
- `score_run(db_path, ...)`: Computes observed actions vs thresholds, authenticity score,
  and confidence tier.
- Finish-line evaluation: Decides whether a run has gathered sufficient data to conclude.
- CLI: `python -m core_brain.run_scorer --db <path> [--json]`
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass(frozen=True)
class ActionThresholds:
    """Target observation thresholds required to achieve statistical confidence."""
    orders: int = 50
    positions_going: int = 20
    stop_loss_exits: int = 5
    positions_merged: int = 15
    target_trades: Optional[int] = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ActionThresholds:
        return cls(
            orders=int(data.get("orders", 50)),
            positions_going=int(data.get("positions_going", 20)),
            stop_loss_exits=int(data.get("stop_loss_exits", 5)),
            positions_merged=int(data.get("positions_merged", 15)),
            target_trades=int(data["target_trades"]) if data.get("target_trades") is not None else None,
        )


@dataclass
class ActionMetric:
    """Status of an individual action dimension."""
    name: str
    observed: int
    target: int
    ratio: float
    met: bool


@dataclass
class RunReliabilityScore:
    """Complete authenticity and reliability assessment of a run."""
    db_path: str
    run_id: Optional[str]
    authenticity_score_pct: float
    confidence_tier: str
    is_reliable: bool
    finish_line_reached: bool
    total_closed_trades: int
    total_fills: int
    actions: dict[str, ActionMetric]
    bottlenecks: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "db_path": self.db_path,
            "run_id": self.run_id,
            "authenticity_score_pct": self.authenticity_score_pct,
            "confidence_tier": self.confidence_tier,
            "is_reliable": self.is_reliable,
            "finish_line_reached": self.finish_line_reached,
            "total_closed_trades": self.total_closed_trades,
            "total_fills": self.total_fills,
            "actions": {k: asdict(v) for k, v in self.actions.items()},
            "bottlenecks": self.bottlenecks,
        }


def _get_table_names(conn: sqlite3.Connection) -> set[str]:
    cur = conn.cursor()
    return {
        row[0]
        for row in cur.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        ).fetchall()
    }


def score_run(
    db_path: Path | str,
    run_id: Optional[str] = None,
    thresholds: Optional[ActionThresholds] = None,
) -> RunReliabilityScore:
    """Calculate the authenticity score and action threshold fulfillment for a database."""
    thresh = thresholds or ActionThresholds()
    path = Path(db_path)

    empty_actions = {
        "orders": ActionMetric("orders", 0, thresh.orders, 0.0, False),
        "positions_going": ActionMetric("positions_going", 0, thresh.positions_going, 0.0, False),
        "stop_loss_exits": ActionMetric("stop_loss_exits", 0, thresh.stop_loss_exits, 0.0, False),
        "positions_merged": ActionMetric("positions_merged", 0, thresh.positions_merged, 0.0, False),
    }

    if not path.is_file():
        return RunReliabilityScore(
            db_path=str(path),
            run_id=run_id,
            authenticity_score_pct=0.0,
            confidence_tier="NO_DATA",
            is_reliable=False,
            finish_line_reached=False,
            total_closed_trades=0,
            total_fills=0,
            actions=empty_actions,
            bottlenecks=list(empty_actions.keys()),
        )

    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
    except Exception:
        return RunReliabilityScore(
            db_path=str(path),
            run_id=run_id,
            authenticity_score_pct=0.0,
            confidence_tier="UNREADABLE",
            is_reliable=False,
            finish_line_reached=False,
            total_closed_trades=0,
            total_fills=0,
            actions=empty_actions,
            bottlenecks=list(empty_actions.keys()),
        )

    try:
        tables = _get_table_names(conn)
        cur = conn.cursor()

        # 1. Orders placed
        orders_count = 0
        if "orders" in tables:
            q = "SELECT count(*) FROM orders"
            params: list[Any] = []
            if run_id:
                q += " WHERE run_id = ?"
                params.append(run_id)
            orders_count = cur.execute(q, params).fetchone()[0]

        # 2. Positions going (fills)
        fills_count = 0
        if "fills" in tables:
            q = "SELECT count(*) FROM fills"
            params = []
            if run_id:
                q += " WHERE run_id = ?"
                params.append(run_id)
            fills_count = cur.execute(q, params).fetchone()[0]

        # 3. Stop loss exits & 4. Merges & Total closed trades
        stop_loss_count = 0
        merged_count = 0
        total_closed_trades = 0

        if "closes" in tables:
            q = "SELECT count(*) FROM closes"
            params = []
            if run_id:
                q += " WHERE run_id = ?"
                params.append(run_id)
            total_closed_trades = cur.execute(q, params).fetchone()[0]

            # Inspect closes columns to handle older schema without 'reason'
            closes_cols = {col[1] for col in cur.execute("PRAGMA table_info(closes)").fetchall()}
            if "reason" in closes_cols:
                q_stop = (
                    "SELECT count(*) FROM closes WHERE (reason IN ('lifecycle_hard_stop', "
                    "'aged_out_rescue') OR method = 'single_buy_exit')"
                )
            else:
                q_stop = "SELECT count(*) FROM closes WHERE method = 'single_buy_exit'"

            params_stop = []
            if run_id:
                q_stop += " AND run_id = ?"
                params_stop.append(run_id)
            stop_loss_count = cur.execute(q_stop, params_stop).fetchone()[0]

            # Merges: merge or shadow_merge
            q_merge = "SELECT count(*) FROM closes WHERE method IN ('merge', 'shadow_merge')"
            params_merge = []
            if run_id:
                q_merge += " AND run_id = ?"
                params_merge.append(run_id)
            merged_count = cur.execute(q_merge, params_merge).fetchone()[0]

    except sqlite3.Error:
        return RunReliabilityScore(
            db_path=str(path),
            run_id=run_id,
            authenticity_score_pct=0.0,
            confidence_tier="UNREADABLE",
            is_reliable=False,
            finish_line_reached=False,
            total_closed_trades=0,
            total_fills=0,
            actions=empty_actions,
            bottlenecks=list(empty_actions.keys()),
        )
    finally:
        conn.close()

    # Calculate ratios and action metrics
    def _metric(name: str, obs: int, target: int) -> ActionMetric:
        target_clamped = max(1, target)
        ratio = min(1.0, obs / target_clamped)
        return ActionMetric(
            name=name,
            observed=obs,
            target=target,
            ratio=round(ratio, 4),
            met=bool(obs >= target),
        )

    actions = {
        "orders": _metric("orders", orders_count, thresh.orders),
        "positions_going": _metric("positions_going", fills_count, thresh.positions_going),
        "stop_loss_exits": _metric("stop_loss_exits", stop_loss_count, thresh.stop_loss_exits),
        "positions_merged": _metric("positions_merged", merged_count, thresh.positions_merged),
    }

    # Authenticity score: average of the 4 dimension ratios
    ratios = [m.ratio for m in actions.values()]
    avg_ratio = sum(ratios) / len(ratios) if ratios else 0.0
    score_pct = round(avg_ratio * 100.0, 1)

    bottlenecks = [k for k, m in actions.items() if not m.met]
    is_reliable = (len(bottlenecks) == 0)

    # Check target trades finish line if specified
    trades_target_met = True
    if thresh.target_trades is not None and thresh.target_trades > 0:
        trades_target_met = (total_closed_trades >= thresh.target_trades)
        if not trades_target_met:
            bottlenecks.append(f"target_trades ({total_closed_trades}/{thresh.target_trades})")

    finish_line_reached = is_reliable and trades_target_met

    # Confidence tier
    if is_reliable and trades_target_met:
        confidence_tier = "DATA_SATURATED"
    elif score_pct >= 80.0 and all(m.observed > 0 for m in actions.values()):
        confidence_tier = "AUTHENTIC"
    elif score_pct >= 50.0:
        confidence_tier = "PROVISIONAL"
    else:
        confidence_tier = "INSUFFICIENT"

    return RunReliabilityScore(
        db_path=str(path),
        run_id=run_id,
        authenticity_score_pct=score_pct,
        confidence_tier=confidence_tier,
        is_reliable=is_reliable,
        finish_line_reached=finish_line_reached,
        total_closed_trades=total_closed_trades,
        total_fills=fills_count,
        actions=actions,
        bottlenecks=bottlenecks,
    )


def render_scoreboard(score: RunReliabilityScore) -> str:
    """Format an ASCII scoreboard presentation for the operator."""
    lines: list[str] = [
        "=" * 70,
        "  RUN AUTHENTICITY & RELIABILITY SCOREBOARD",
        "=" * 70,
        f"  Database:        {Path(score.db_path).name}",
        f"  Run ID:          {score.run_id or 'all'}",
        f"  Authenticity:    {score.authenticity_score_pct:.1f}% [{score.confidence_tier}]",
        f"  Reliability:     {'RELIABLE (Thresholds Met)' if score.is_reliable else 'UNSATISFIED'}",
        f"  Finish Line:     {'REACHED (Conclusive Evidence)' if score.finish_line_reached else 'IN PROGRESS'}",
        "-" * 70,
        f"  {'Action Dimension':<24} {'Observed':>9} {'Target':>8}   {'Progress':<14} {'Status':>6}",
        "-" * 70,
    ]

    labels = {
        "orders": "Orders Placed",
        "positions_going": "Positions Going (Fills)",
        "stop_loss_exits": "Stop Loss Exits",
        "positions_merged": "Positions Merged",
    }

    bar_len = 10
    for key in ("orders", "positions_going", "stop_loss_exits", "positions_merged"):
        m = score.actions.get(key)
        if not m:
            continue
        filled = int(round(m.ratio * bar_len))
        bar = "#" * filled + "-" * (bar_len - filled)
        pct_str = f"{int(m.ratio * 100)}%"
        status_str = "OK" if m.met else "PENDING"
        label = labels.get(key, m.name)
        lines.append(f"  {label:<24} {m.observed:>9} {m.target:>8}   [{bar}] {pct_str:>4} {status_str:>6}")

    lines.extend([
        "-" * 70,
        f"  Total Closed Trades: {score.total_closed_trades} | Total Fills: {score.total_fills}",
    ])

    if score.bottlenecks:
        lines.append(f"  Action Bottlenecks:  {', '.join(score.bottlenecks)}")
    else:
        lines.append("  Action Bottlenecks:  None (All observation thresholds satisfied)")

    lines.append("=" * 70)
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Score a shadow or live trading database for authenticity and action reliability."
    )
    parser.add_argument("--db", required=True, help="Path to SQLite registry database.")
    parser.add_argument("--run-id", default=None, help="Filter scoring to specific run_id.")
    parser.add_argument("--orders", type=int, default=50, help="Order quotes threshold (default: 50).")
    parser.add_argument("--fills", type=int, default=20, help="Positions going / fills threshold (default: 20).")
    parser.add_argument("--stops", type=int, default=5, help="Stop loss exits threshold (default: 5).")
    parser.add_argument("--merges", type=int, default=15, help="Positions merged threshold (default: 15).")
    parser.add_argument("--target-trades", type=int, default=None, help="Optional total trades finish line.")
    parser.add_argument("--json", action="store_true", dest="json_out", help="Emit JSON instead of text.")

    args = parser.parse_args(argv)

    thresh = ActionThresholds(
        orders=args.orders,
        positions_going=args.fills,
        stop_loss_exits=args.stops,
        positions_merged=args.merges,
        target_trades=args.target_trades,
    )

    score = score_run(args.db, run_id=args.run_id, thresholds=thresh)

    if args.json_out:
        print(json.dumps(score.to_dict(), indent=2))
    else:
        print(render_scoreboard(score))

    return 0 if score.is_reliable else 1


if __name__ == "__main__":
    sys.exit(main())
