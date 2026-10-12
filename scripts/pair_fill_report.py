"""How often both legs of a pair fill, and how big the fills are.

Every measurement so far has answered a question about the BOOK. This one asks
the question about US, and it is the last one left: the spread hunter earns
`1.00 - pair_cost` only when BOTH legs fill as resting bids, and no run in this
project's history has ever recorded that happening.

Two numbers come out, and both are needed to say whether the strategy works.

  * **The double-maker rate.** Of the markets we quoted two-sidedly, in how
    many did both sides fill? A single leg is not a partial success -- it is a
    directional position the strategy exists to avoid, closed by the safety
    path at a loss.
  * **The fill-size distribution.** A pair earns one tick per share, so the
    size of a fill IS the size of the trade -- five shares of a $0.99 pair is
    five cents, whatever the double-maker rate. A high completion rate made of
    five-share fills is not a business.

    There is no gas floor under that. Polymarket merges are gasless: the
    relayer submits them from its own address and pays (#152). An earlier
    version of this report charged $0.01138 per merge -- the median burn of
    twelve on-chain `mergePositions` transactions -- which was the cost to
    whoever sent THOSE transactions, not to us.

Reads a shadow store; writes nothing.
"""
from __future__ import annotations

import argparse
import sqlite3
import statistics
from pathlib import Path
from typing import Optional

def _pct(n: float, d: float) -> str:
    return f"{100.0 * n / d:6.2f}%" if d else "     --"


def quoted_markets(conn: sqlite3.Connection) -> list[tuple]:
    """(market, sides quoted, orders, filled orders) per market."""
    return conn.execute(
        "SELECT market_slug,"
        "  COUNT(DISTINCT side) AS sides,"
        "  COUNT(*) AS orders,"
        "  SUM(CASE WHEN filled > 0 THEN 1 ELSE 0 END) AS filled_orders,"
        "  COUNT(DISTINCT CASE WHEN filled > 0 THEN side END) AS filled_sides"
        " FROM quotes GROUP BY market_slug ORDER BY market_slug"
    ).fetchall()


def fill_sizes(conn: sqlite3.Connection) -> list[float]:
    rows = conn.execute("SELECT size FROM fills WHERE size > 0").fetchall()
    return [float(r[0]) for r in rows]


def merges(conn: sqlite3.Connection) -> list[tuple]:
    """Completed merge closes: the only rows that are the strategy working."""
    try:
        return conn.execute(
            "SELECT market_slug, shares, cost_basis, proceeds, realized_pnl"
            " FROM closes WHERE method LIKE '%merge%'"
        ).fetchall()
    except sqlite3.Error:
        return []


def exits(conn: sqlite3.Connection) -> list[tuple]:
    try:
        return conn.execute(
            "SELECT market_slug, shares, realized_pnl FROM closes"
            " WHERE method LIKE '%exit%'"
        ).fetchall()
    except sqlite3.Error:
        return []


def open_read_only(db_path: Path) -> sqlite3.Connection:
    """Open the store read-only, so a report can never alter what it reports.

    This reads live shadow stores while a rehearsal is still writing them, and
    `data/orders.db` is a legal target too. A reporting tool holding a
    read-write handle on the production registry is one typo away from being
    the thing that corrupts it, and SQLite will happily create an empty
    database if the path is wrong -- which would make a mistyped `--db` look
    like a run that recorded nothing.
    """
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def report(db_path: Path) -> str:
    conn = open_read_only(db_path)
    try:
        markets = quoted_markets(conn)
        sizes = fill_sizes(conn)
        merged = merges(conn)
        exited = exits(conn)
        n_quotes = conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]
        n_fills = conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
    finally:
        conn.close()

    out = [f"pair-fill report -- {db_path}",
           f"{n_quotes} quotes, {n_fills} fills, {len(markets)} markets", ""]

    two_sided = [m for m in markets if m[1] >= 2]
    one_leg = [m for m in two_sided if m[4] == 1]
    both_legs = [m for m in two_sided if m[4] >= 2]

    out.append("THE DOUBLE-MAKER RATE")
    out.append("  a single leg is not half a pair -- it is a directional")
    out.append("  position the safety path closes at a loss")
    out.append(f"  markets quoted two-sidedly : {len(two_sided)}")
    out.append(f"  no leg filled              : "
               f"{len(two_sided) - len(one_leg) - len(both_legs)}")
    out.append(f"  ONE leg filled             : {len(one_leg):<5}"
               f"{_pct(len(one_leg), len(two_sided))}")
    out.append(f"  BOTH legs filled           : {len(both_legs):<5}"
               f"{_pct(len(both_legs), len(two_sided))}")
    out.append("")

    # `fill_sizes` counts every filled leg, including legs whose companion
    # never filled. That is the right denominator for "how big is a fill", but
    # it is NOT a count of pairs -- a lone leg is a directional position, which
    # is the exact thing this report exists to keep separate from a pair. The
    # earnings line is therefore labelled per FILL of the median size, and
    # stated as the tick a pair would earn were that fill completed.
    out.append("FILL SIZE -- a pair earns one tick per share, so size is the trade")
    if sizes:
        srt = sorted(sizes)
        med = statistics.median(srt)
        # Printed at the precision the arithmetic uses. Rounding the display to
        # a whole share while multiplying the unrounded value left the median
        # and the earnings beside it disagreeing, with nothing on the line to
        # say which one had been rounded.
        out.append(f"  n={len(srt)}  min {min(srt):g}  median {med:g}  "
                   f"max {max(srt):g}")
        for edge, label in ((0.001, "0.1c-tick book"), (0.01, "1c-tick book")):
            out.append(f"  {label:<16} median fill would earn "
                       f"${med * edge:>7.4f} if its pair completed")
    else:
        out.append("  no fills recorded")
    out.append("")

    out.append("WHAT ACTUALLY CLOSED")
    out.append(f"  merges : {len(merged)}")
    for slug, shares, basis, proceeds, pnl in merged:
        gross = (proceeds or 0) - (basis or 0)
        # No gas line: the merge is gasless, so gross IS net (#152).
        out.append(f"    {str(slug)[:34]:<36}{shares or 0:>7.0f}sh  "
                   f"${gross:>7.4f}")
    # A single-leg exit never prints: `proceeds` is the best-bid mark at the
    # exit, so labelling it `pnl` reads as a fill that never happened.
    out.append(f"  single-leg exits (mark-to-exit, est.) : {len(exited)}")
    for slug, shares, pnl in exited:
        out.append(f"    {str(slug)[:34]:<36}{shares or 0:>7.0f}sh  "
                   f"mark-to-exit ${pnl or 0:>7.4f}")
    return "\n".join(out)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True, help="shadow store to read")
    args = ap.parse_args(argv)
    print(report(Path(args.db)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
