"""Read a book/tape store and print the two curves it was recorded for.

`scripts.book_tape_recorder` writes; this reads. Kept apart so the recorder can
run for an hour with no reason to change, and the questions asked of its store
can change as often as they need to.

Two sections, matching the two questions in issue #145:

  * **Reachability.** Tape volume by ticks below mid, and the cumulative share
    a resting bid at each distance could ever be reached by. The cumulative
    column is the direct measurement of `spread_capture_frac`, which the market
    filter asserts is 0.25 and has never checked.
  * **The touch pair.** The distribution of `best_bid(UP) + best_bid(DOWN)`,
    against the `max_pair_cost` the risk gate refuses with `>=`.

Bootstrap rows are excluded from reachability and counted separately. See
`book_tape_recorder.write_tape` for why they exist and why they are kept.
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Optional

MAX_PAIR_COST = 0.99            # core_brain/config.py:751, compared with >=
MAX_COMPLETABLE = 1.00          # core_brain/config.py:764, likewise


def _pct(numerator: float, denominator: float) -> str:
    return f"{100.0 * numerator / denominator:6.2f}%" if denominator else "     --"


def reachability(conn: sqlite3.Connection) -> list[tuple]:
    """(ticks_from_mid, volume, n_prints) for live tape only, nearest first."""
    return conn.execute(
        "SELECT ticks_from_mid, SUM(volume), COUNT(*) FROM tape_buckets"
        " WHERE is_bootstrap = 0 AND ticks_from_mid IS NOT NULL"
        " GROUP BY ticks_from_mid ORDER BY ticks_from_mid"
    ).fetchall()


def touch_pairs(conn: sqlite3.Connection) -> list[tuple]:
    """(touch_pair_cost, n) over every readable book sample."""
    return conn.execute(
        "SELECT touch_pair_cost, COUNT(*) FROM book_samples"
        " WHERE touch_pair_cost IS NOT NULL"
        " GROUP BY touch_pair_cost ORDER BY touch_pair_cost"
    ).fetchall()


def mid_sums(conn: sqlite3.Connection) -> list[tuple]:
    return conn.execute(
        "SELECT mid_sum, COUNT(*) FROM book_samples WHERE mid_sum IS NOT NULL"
        " GROUP BY mid_sum ORDER BY mid_sum"
    ).fetchall()


def spreads(conn: sqlite3.Connection) -> list[tuple]:
    """(market, n, mean spread per leg, mean touch size per leg).

    Restricted to samples where BOTH sides of BOTH books are readable. A
    one-sided book has no spread to average, and averaging over the rows that
    do have one, under a count that includes the rows that do not, reports a
    spread for a market that was half dark.
    """
    return conn.execute(
        "SELECT market_slug, COUNT(*),"
        " ROUND(AVG(best_ask_up - best_bid_up), 4),"
        " ROUND(AVG(best_ask_down - best_bid_down), 4),"
        " ROUND(AVG(touch_size_up), 0), ROUND(AVG(touch_size_down), 0)"
        " FROM book_samples"
        " WHERE best_bid_up IS NOT NULL AND best_ask_up IS NOT NULL"
        "   AND best_bid_down IS NOT NULL AND best_ask_down IS NOT NULL"
        " GROUP BY market_slug ORDER BY COUNT(*) DESC"
    ).fetchall()


def report(db_path: Path) -> str:
    conn = sqlite3.connect(str(db_path))
    try:
        samples = conn.execute("SELECT COUNT(*) FROM book_samples").fetchone()[0]
        boot, live = conn.execute(
            "SELECT SUM(is_bootstrap), SUM(1 - is_bootstrap) FROM tape_buckets"
        ).fetchone()
        rows = reachability(conn)
        pairs = touch_pairs(conn)
        sums = mid_sums(conn)
        books = spreads(conn)
    finally:
        conn.close()

    out = [f"book/tape report -- {db_path}",
           f"{samples} book samples; {live or 0} live tape rows, "
           f"{boot or 0} bootstrap rows excluded", ""]

    out.append("MARKETS SEEN")
    out.append(f"  {'market':<34}{'n':>6}{'sprd UP':>9}{'sprd DN':>9}"
               f"{'touch UP':>11}{'touch DN':>11}")
    for slug, n, su, sd, tu, td in books:
        out.append(f"  {slug[:34]:<34}{n:>6}{su or 0.0:>9.4f}{sd or 0.0:>9.4f}"
                   f"{tu or 0:>11.0f}{td or 0:>11.0f}")
    out.append("")

    out.append("REACHABILITY -- tape volume by ticks below mid")
    out.append("  a resting BUY at k ticks is reached by prints at ticks >= k")
    total = sum(r[1] for r in rows) or 0.0
    below = sum(r[1] for r in rows if r[0] >= 0) or 0.0
    # The tail is built from the deepest tick upward, so `tail[k]` is the
    # volume at `ticks_from_mid >= k` -- everything that would have traded
    # through a bid resting at k. Accumulating the other way, from mid down,
    # would report the prints that went to better-priced bids instead.
    tail: dict[int, float] = {}
    running = 0.0
    for ticks, vol, _ in sorted(rows, key=lambda r: -r[0]):
        if ticks < 0:
            continue
        running += vol
        tail[ticks] = running
    out.append(f"  {'ticks':>7}{'volume':>14}{'prints':>8}{'share':>9}{'cum>=':>9}")
    for ticks, vol, n in rows:
        cum = (_pct(tail[ticks], total) if ticks >= 0 else "       --")
        out.append(f"  {ticks:>7}{vol:>14.1f}{n:>8}{_pct(vol, total):>9}{cum:>9}")
    out.append("")
    out.append(f"  all tape:                 {total:14.1f}")
    out.append(f"  at or below mid (ticks>=0):{below:13.1f}  {_pct(below, total)}")
    for k in (0, 1, 2, 3, 4, 5):
        reach = sum(r[1] for r in rows if r[0] >= k)
        out.append(f"  reachable by a bid {k} tick(s) under mid: "
                   f"{_pct(reach, total)}   ({reach:.1f})")
    out.append("  `spread_capture_frac` asserts 0.25 of volume is captured; the")
    out.append("  figures above are an UPPER BOUND on that, before queue.")
    out.append("")

    out.append("THE TOUCH PAIR -- best_bid(UP) + best_bid(DOWN)")
    ptotal = sum(p[1] for p in pairs) or 0
    for cost, n in pairs:
        verdict = ("REFUSED by bot_pos_price_max_pair_cost_usd" if cost >= MAX_PAIR_COST
                   else "tradeable")
        out.append(f"  ${cost:.4f}  n={n:<6} {_pct(n, ptotal)}  {verdict}")
    refused = sum(n for c, n in pairs if c >= MAX_PAIR_COST)
    out.append(f"  refused by the ${MAX_PAIR_COST:.2f} cap: "
               f"{_pct(refused, ptotal)} of samples")
    out.append("")

    out.append("MID SUM -- mid(UP) + mid(DOWN)")
    stotal = sum(s[1] for s in sums) or 0
    for val, n in sums:
        out.append(f"  {val:.4f}  n={n:<6} {_pct(n, stotal)}")
    return "\n".join(out)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True, help="book/tape store to read")
    args = ap.parse_args(argv)
    print(report(Path(args.db)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
