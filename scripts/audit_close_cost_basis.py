"""Audit every booked rescue close against the cash its fills actually spent (#798).

Standalone, read-only script in the `audit_matchup_refusals.py` convention:
argparse flags, per-condition print lines, non-zero exit on findings. It never
writes, repairs, or re-reports a past run -- it reads one run database and
prints what the ledger itself says today.

The invariant, per condition:

    sum(closes.cost_basis for rescue-method closes) <= sum(BUY fills notional)

Rescue means exactly `core_brain.close_methods.RESCUE_METHODS`
(single_buy_exit, naked_exit, ladder_exit) -- the same set the saver and the
registry already use. Merge closes (shadow_merge), settlements
(shadow_settlement) and every other method stay out of BOTH sides of the
comparison: merges and settlements resolve positions the fills paid for, so
counting them as spend would manufacture a violation out of a healthy book.

The join is the whole issue: `fills` references `orders.id` (the registry's
own row id), NOT `orders.order_id` (the venue id). A join on the venue id
yields zero totals everywhere and a falsely clean report. The script prints
the spent figure on every condition line so a zero is visible, and refuses to
report a clean pass when the fills table is non-empty but the join matched
nothing (exit 2).

Exit codes:
    0  clean -- no condition exceeds its fills' spend
    1  at least one violation
    2  join suspicion -- fills exist but the orders join matched none of them
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core_brain.close_methods import RESCUE_METHODS

# Float dust from summing fills, mirroring single_buy_saver's
# POSITION_DIVERGENCE_TOLERANCE scale: a real violation is cents, not 1e-12.
TOLERANCE = 1e-6

# Per-condition totals, rounded to the ledger's own precision.
_ROUND = 6


def _round(x: Any) -> float:
    return round(float(x or 0.0), _ROUND)


def audit_db(db_path: str | Path) -> dict[str, Any]:
    """Read one run database and return the audit result.

    The connection is opened read-only (``mode=ro``) so the audit can never
    create -wal/-shm files beside a run database it must not touch.

    Returns a dict with ``rows`` (one per audited condition), ``violations``
    (subset of rows), ``join_suspicious`` (bool) and ``totals``.
    """
    con = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    try:
        return _audit_connection(con)
    finally:
        con.close()


def _audit_connection(con: sqlite3.Connection) -> dict[str, Any]:
    con.row_factory = sqlite3.Row

    # Join fills to orders on the registry's row id. The venue id
    # (orders.order_id) is NOT the join key: fills.order_uuid stores orders.id.
    fills = con.execute(
        """
        SELECT LOWER(o.condition_id) AS cid,
               SUM(f.size * f.price)  AS spent,
               COUNT(*)               AS fill_rows
        FROM fills f
        JOIN orders o ON o.id = f.order_uuid
        WHERE o.side = 'BUY'
        GROUP BY LOWER(o.condition_id)
        """
    ).fetchall()

    # Join-suspicion guard: a fills table with rows that matched no order row
    # means the join key is wrong (the orders.order_id trap) and every clean
    # result below would be a false clean.
    total_fills = con.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
    joined_fills = con.execute(
        "SELECT COUNT(*) FROM fills f JOIN orders o ON o.id = f.order_uuid"
    ).fetchone()[0]
    join_suspicious = total_fills > 0 and joined_fills == 0

    closes = con.execute(
        f"""
        SELECT LOWER(condition_id) AS cid,
               SUM(COALESCE(cost_basis, 0.0)) AS rescue_cost,
               COUNT(*)                       AS close_rows
        FROM closes
        WHERE method IN ({",".join("?" for _ in RESCUE_METHODS)})
        GROUP BY LOWER(condition_id)
        """,
        tuple(RESCUE_METHODS),
    ).fetchall()

    slugs = con.execute(
        """
        SELECT LOWER(condition_id) AS cid, MIN(market_slug) AS slug
        FROM quotes GROUP BY LOWER(condition_id)
        """
    ).fetchall()

    spent_by_cid = {r["cid"]: (_round(r["spent"]), r["fill_rows"]) for r in fills}
    closes_by_cid = {r["cid"]: (_round(r["rescue_cost"]), r["close_rows"]) for r in closes}
    slug_by_cid = {r["cid"]: r["slug"] for r in slugs}

    rows: list[dict[str, Any]] = []
    for cid in sorted(set(spent_by_cid) | set(closes_by_cid)):
        spent, fill_rows = spent_by_cid.get(cid, (0.0, 0))
        rescue_cost, close_rows = closes_by_cid.get(cid, (0.0, 0))
        excess = _round(rescue_cost - spent)
        rows.append(
            {
                "condition_id": cid,
                "market_slug": slug_by_cid.get(cid),
                "spent": spent,
                "fill_rows": fill_rows,
                "rescue_cost_basis": rescue_cost,
                "close_rows": close_rows,
                "excess": excess,
                "violation": excess > TOLERANCE,
            }
        )

    violations = [r for r in rows if r["violation"]]
    return {
        "rows": rows,
        "violations": violations,
        "join_suspicious": join_suspicious,
        "totals": {
            "conditions": len(rows),
            "total_fills": total_fills,
            "joined_fills": joined_fills,
            "rescue_closes": sum(r["close_rows"] for r in rows),
        },
    }


def format_line(row: dict[str, Any]) -> str:
    """One per-condition line. Spent is always printed so a zero is visible."""
    label = row["market_slug"] or row["condition_id"]
    status = "VIOLATION" if row["violation"] else "OK       "
    return (
        f"{status}  {label:<42} spent=${row['spent']:.2f}  "
        f"rescue_cost_basis=${row['rescue_cost_basis']:.2f}  "
        f"excess={'+' if row['excess'] > 0 else ''}{row['excess']:.2f}"
    )


def run(db_path: str | Path, out=print) -> int:
    """Print the report for one database and return its exit code."""
    result = audit_db(db_path)
    out("=" * 80)
    out(f"CLOSE-COST-BASIS AUDIT: {db_path}")
    out("=" * 80)
    out(
        f"fills join: orders.id = fills.order_uuid   "
        f"({result['totals']['joined_fills']}/{result['totals']['total_fills']} fills joined)"
    )
    for row in result["rows"]:
        out(format_line(row))
    if result["join_suspicious"]:
        out(
            "JOIN SUSPICION: fills exist but NONE joined to orders on "
            "orders.id = fills.order_uuid. Every total above is zero and a "
            "clean report here would be false. Do not trust an OK result "
            "until the join matches (the wrong key is orders.order_id)."
        )
        return 2
    out(
        f"{len(result['violations'])} violation(s) across "
        f"{result['totals']['conditions']} condition(s); "
        f"{result['totals']['rescue_closes']} rescue close(s) audited"
    )
    return 1 if result["violations"] else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit rescue-close cost basis against fills spend (#798). Read-only."
    )
    parser.add_argument("--db", required=True, help="Path to the run database (opened read-only)")
    args = parser.parse_args()
    return run(args.db)


if __name__ == "__main__":
    sys.exit(main())
