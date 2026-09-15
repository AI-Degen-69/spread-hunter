"""Count a run's cancels by reason, so churn and defence are separable.

`plan_orders` records why every order was cancelled and how many shares were
resting ahead of it at the time. This reads that back. Without it the record is
just two more columns nobody looks at, and the question the record exists to
answer -- "how much of our cancelling is throwing away queue position for
nothing?" -- stays unanswered.

Read-only. Opens the registry, counts, prints.
"""
from __future__ import annotations

import sqlite3
import statistics
import urllib.parse
from pathlib import Path
from typing import Any, Optional

# The reasons `plan_orders` writes, in the order a reader wants to see them:
# the one that might be waste first, the ones that are doing their job after.
REASON_ORDER = ("price_moved", "regate_pair_cost", "not_quoted",
                "market_dropped")

REASON_BLURB = {
    "price_moved": "the desired price left the tolerance band",
    "regate_pair_cost": "holding would have carried the pair over max_pair_cost",
    "not_quoted": "we stopped quoting that token this cycle",
    "market_dropped": "the market left the active universe",
}


def summarize(db_path: Path | str) -> dict[str, Any]:
    """Cancels grouped by reason, with the queue each group gave up."""
    path = Path(db_path)
    if not path.exists():
        return {"db_path": str(path), "readable": False, "reasons": {},
                "cancelled": 0, "unattributed": 0}

    try:
        # Escape before interpolating: a filename holding `?` or `#` would
        # otherwise be read as the start of the URI's query or fragment, and
        # the open would fail on a file that is perfectly fine.
        con = sqlite3.connect(
            f"file:{urllib.parse.quote(str(path))}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        cols = {r["name"] for r in con.execute("PRAGMA table_info(orders)")}
        # BOTH columns, not just one: a partially migrated store would pass a
        # check on `cancel_reason` alone and then fail selecting the other.
        if not {"cancel_reason", "cancel_queue_ahead"} <= cols:
            # A store written before attribution existed. Its cancels are real
            # and countable; they simply carry no reason. Reporting that as
            # "unreadable" would send the operator looking for a broken file.
            total = con.execute(
                "SELECT COUNT(*) FROM orders WHERE status = 'cancelled'"
            ).fetchone()[0]
            con.close()
            return {"db_path": str(path), "readable": True, "reasons": {},
                    "cancelled": total, "unattributed": total}
        rows = con.execute(
            "SELECT cancel_reason, cancel_queue_ahead FROM orders "
            "WHERE status = 'cancelled'"
        ).fetchall()
        con.close()
    except sqlite3.Error:
        # A registry we cannot read is not a registry with no cancels.
        return {"db_path": str(path), "readable": False, "reasons": {},
                "cancelled": 0, "unattributed": 0}

    reasons: dict[str, dict[str, Any]] = {}
    unattributed = 0
    for row in rows:
        reason = row["cancel_reason"]
        if not reason:
            # Cancelled before attribution existed, or by a path that does not
            # record one. Counted separately -- folding it into any named
            # reason would invent evidence.
            unattributed += 1
            continue
        slot = reasons.setdefault(str(reason), {"count": 0, "queues": []})
        slot["count"] += 1
        if row["cancel_queue_ahead"] is not None:
            slot["queues"].append(float(row["cancel_queue_ahead"]))

    for slot in reasons.values():
        queues = slot.pop("queues")
        slot["measured_queues"] = len(queues)
        slot["median_queue_ahead"] = statistics.median(queues) if queues else None
        slot["min_queue_ahead"] = min(queues) if queues else None

    return {
        "db_path": str(path),
        "readable": True,
        "cancelled": len(rows),
        "unattributed": unattributed,
        "reasons": reasons,
    }


def _hold_state() -> str:
    """Whether the two re-quote holds are actually on, read from the config.

    Printed rather than assumed: a hardcoded "currently off" would keep saying
    so after someone turned it on, which is the reading that matters most.

    BOTH holds are named. They keep a price move for different reasons -- the
    queue hold for an order near the front of its level, the direction hold for
    an order the book is falling onto -- and reporting only one of them tells
    an operator the other is not there.
    """
    try:
        from core_brain.config import load

        cfg = load()
        shares = float(getattr(cfg, "requote_hold_queue_shares", 0.0) or 0.0)
        below = float(getattr(cfg, "requote_hold_below_target", 0.0) or 0.0)
    except Exception:
        return "state unread"
    queue = f"on at {shares:g} shares" if shares > 0 else "off"
    drop = f"on within {below:g}" if below > 0 else "off"
    return f"queue {queue}, direction {drop}"


def format_report(summary: dict[str, Any]) -> str:
    """The block an operator reads."""
    if not summary.get("readable"):
        return (f"cancels in {summary.get('db_path')}: UNREAD\n"
                f"  The registry could not be opened. This is not zero cancels.")

    lines = [f"cancels in {summary['db_path']}: {summary['cancelled']}"]
    reasons = summary["reasons"]
    ordered = [r for r in REASON_ORDER if r in reasons]
    ordered += sorted(r for r in reasons if r not in REASON_ORDER)

    for reason in ordered:
        slot = reasons[reason]
        median = slot["median_queue_ahead"]
        smallest = slot["min_queue_ahead"]
        queue = ("queue unmeasured" if median is None
                 else f"median {median:,.0f} ahead, closest {smallest:,.0f}")
        lines.append(f"  {reason:18s} {slot['count']:4d}  ({queue})")
        blurb = REASON_BLURB.get(reason)
        if blurb:
            lines.append(f"  {'':18s}       {blurb}")

    if summary["unattributed"]:
        lines.append(f"  {'unattributed':18s} {summary['unattributed']:4d}  "
                     f"(cancelled by a path that records no reason)")

    price_moved = reasons.get("price_moved", {}).get("count", 0)
    if price_moved and summary["cancelled"]:
        share = 100.0 * price_moved / summary["cancelled"]
        lines.append("")
        lines.append(f"  {share:.0f}% of cancels were price moves. Those are the "
                     f"only ones the re-quote holds can keep")
        lines.append(f"  (requote_hold_queue_shares / requote_hold_below_target "
                     f"in core_brain.config: {_hold_state()});")
        lines.append(f"  the rest are gates doing their job.")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    from core_brain.order_registry import DEFAULT_DB_PATH

    parser = argparse.ArgumentParser(
        description="Count a run's cancels by reason (read-only).")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH),
                        help="registry or shadow store to read")
    args = parser.parse_args(argv)

    summary = summarize(args.db)
    print(format_report(summary))
    return 0 if summary["readable"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
