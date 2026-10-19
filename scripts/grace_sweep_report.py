"""Every grace value at once, from a single run held at the longest one.

A four-arm sweep of rescue timing (`pairs_exit_window_sec` / `aged_out_rescue_lead_sec`, historically `single_buy_grace_sec`) would split an already thin fill
count four ways and reproduce the confound the #138 sweep could not remove:
arm and hour never separated. This reads one run held at the MAXIMUM grace and
recovers every shorter value from it, because a longer grace CONTAINS the
shorter ones. If the companion leg fills 30s after the first, then grace=45 and
grace=120 both caught it and grace=15 did not — that is a filter on a recorded
distribution, not a second experiment.

Two things decide the answer and this reports both, in SHARES rather than in
legs -- an opening leg of 5 shares against a companion that only fills 1 has 4
shares exposed, and counting the whole leg "rescued" would bury them:

  * **Merged shares.** Per pair, how many opposite-side shares had arrived by
    `open_ts + grace`. `min(open_qty, arrived)` merge; the rest of the opening
    leg stays exposed. A longer grace can only raise this.
  * **The cost of waiting.** The exposed shares are dumped at the bid as it
    stood at the horizon -- a leg held 120s exits at the 120s bid, not the 15s
    one. Reporting merged shares alone would recommend an unbounded grace.

The exit side is repriced from a `book_tape_recorder` store covering the same
window, so the bid at each horizon is measured rather than modelled. The exit
total is published only when EVERY exposed pair has a bid; a partial sum is
withheld, and zero exposed shares is a measured zero. Without a book the cost
column is unmeasured rather than guessed.

The live loop exits on the first poll AFTER the grace expires, so a real exit
lands 0 to one rotation later than the horizon shown and the priced bid is a
best case. A companion that arrives within one rotation of the horizon is
flagged: the poll phase is not recorded, so its split is not resolved here.
"""
from __future__ import annotations

import argparse
import sqlite3
import statistics
from pathlib import Path
from typing import Optional

# The rotation cadence is the real floor under this knob. The live loop exits
# on the first poll AFTER the grace expires, so a grace below the interval is
# indistinguishable from a grace equal to it: a companion that arrives 0.9s
# after its first leg is already there when the 5s poll looks. A `grace=0` row
# would read as "dump instantly" and recommend against a knob the engine
# cannot actually set that low.
POLL_INTERVAL_SEC = 5.0
HORIZONS = (POLL_INTERVAL_SEC, 15.0, 45.0, 120.0)
MERGE_GAIN_PER_SHARE = 0.01      # a pair earns one tick; merges are gasless (#152)


def _ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


MS_PER_SEC = 1000.0


def leg_events(conn: sqlite3.Connection) -> list[dict]:
    """Every fill, keyed by the PAIR the engine assigned it, time in seconds.

    Four joins-worth of care, each of which returned silent nonsense first:

      * `fills.order_uuid` carries the registry's `orders.id`, not its
        `orders.order_id` -- the latter is the venue-side handle
        (`shadow-...`). Joining on `order_id` matches zero rows and the report
        announces there is nothing to sweep.
      * `orders.side` is the ORDER side: every row reads `BUY`. The UP/DOWN
        leg lives on the token, so the side comes from the quotes ledger.
      * `fills.recorded_ts` is in MILLISECONDS while `quotes.ts` and the book
        recorder's `ts` are in seconds. Left alone a 1.4-second wait reads as
        1,400 and no horizon matches.
      * **Pairing is read from `orders.pair_id`, not reconstructed.** Matching
        the next opposite-side fill in the same market looks equivalent and is
        not: a market can carry several pairs at once, and first-in-first-out
        across them married legs the engine never grouped. On this run that
        produced a phantom UP 0.42 + DOWN 0.73 = $1.15 pair -- over the dollar
        the instrument pays, and impossible, because `risk.hard_block` had
        refused exactly that. The engine already recorded which leg belongs to
        which pair; second-guessing it invented a gate failure that never
        happened.
    """
    rows = conn.execute(
        "SELECT o.pair_id AS pair_id, f.recorded_ts / ? AS ts,"
        "       f.size AS size, f.price AS price,"
        "       o.condition_id AS cid, q.side AS side"
        " FROM fills f"
        " JOIN orders o ON o.id = f.order_uuid"
        " LEFT JOIN (SELECT DISTINCT token_id, side FROM quotes) q"
        "   ON q.token_id = o.token_id"
        " WHERE f.size > 0 AND o.pair_id IS NOT NULL"
        " ORDER BY f.recorded_ts",
        (MS_PER_SEC,)
    ).fetchall()
    out = [dict(r) for r in rows]
    bad = sorted({r["pair_id"] for r in out if r["side"] not in ("UP", "DOWN")})
    if bad:
        raise ValueError(
            f"{len(bad)} paired pair(s) carry a fill with no UP/DOWN quote side "
            f"({', '.join(bad)}): the quotes ledger has no row for that token. "
            f"Refusing to guess the leg -- `bid_at` would price it as DOWN and "
            f"`pair_timelines` could count it as the companion."
        )
    return out


def pair_timelines(events: list[dict]) -> list[dict]:
    """Per pair: the side that opened the position, how many shares it opened,
    and when the OTHER side's shares arrived.

    A pair merges `min(up_qty, dn_qty)` shares once both sides are present, so
    the grace question is not "did any opposite fill exist" -- it is how much of
    the second side had arrived by `open_ts + grace`. Reported in shares per
    pair rather than per leg: an opening leg of 5 shares against a companion
    that only ever fills 1 leaves 4 shares exposed, and calling the whole leg
    "rescued" because some opposite fill exists would bury them.

    `companion_qty` is `[(ts, cumulative opposite-side shares), ...]` in time
    order, so a horizon lookup walks it once. Empty when the other side never
    filled -- those shares are exposed for good and no grace rescues them.
    """
    by_pair: dict[str, list[dict]] = {}
    for e in events:
        by_pair.setdefault(e["pair_id"], []).append(e)

    out: list[dict] = []
    for pair_id, evs in by_pair.items():
        evs.sort(key=lambda e: e["ts"])
        open_side = evs[0]["side"]
        open_ts = evs[0]["ts"]
        opened = [e for e in evs if e["side"] == open_side]
        other = [e for e in evs if e["side"] != open_side]
        open_qty = sum(e["size"] for e in opened)
        # Size-weighted price of the side that can be left exposed -- that is
        # the leg an exit sells at the bid.
        open_price = (sum(e["size"] * e["price"] for e in opened) / open_qty
                      if open_qty else 0.0)
        # A later add on the opening side starts its OWN grace clock at its own
        # fill time. Tracked cumulatively so a shorter reconstructed horizon
        # only counts the shares whose fill had landed by then -- otherwise a
        # t+110 add reads as opened at t and inflates both columns at grace=5.
        open_cum: list[tuple[float, float]] = []
        running_open = 0.0
        for e in opened:
            running_open += e["size"]
            open_cum.append((e["ts"], running_open))
        cum: list[tuple[float, float]] = []
        running = 0.0
        for e in other:
            running += e["size"]
            cum.append((e["ts"], running))
        out.append({"pair_id": pair_id, "cid": evs[0]["cid"],
                    "open_side": open_side, "open_ts": open_ts,
                    "open_qty": open_qty, "open_price": open_price,
                    "open_qty_ts": open_cum, "companion_qty": cum})
    return out


def opened_by(timeline: dict, deadline_ts: float) -> float:
    """Opening-side shares whose own fill had landed by `deadline_ts`."""
    got = 0.0
    for ts, running in timeline["open_qty_ts"]:
        if ts > deadline_ts:
            break
        got = running
    return got


def companion_by(timeline: dict, deadline_ts: float) -> float:
    """Opposite-side shares that had arrived by `deadline_ts`."""
    got = 0.0
    for ts, running in timeline["companion_qty"]:
        if ts > deadline_ts:
            break
        got = running
    return got


def first_companion_gap(timeline: dict) -> Optional[float]:
    """Seconds from the opening fill to the first opposite share, or None."""
    if not timeline["companion_qty"]:
        return None
    return timeline["companion_qty"][0][0] - timeline["open_ts"]


def bid_at(book: Optional[sqlite3.Connection], cid: str, side: str,
           ts: float) -> Optional[float]:
    """The best bid on `side` at or just after `ts`, from the book recorder.

    "Just after" rather than "nearest": an exit at t+15s is filled by the book
    as it stands then, and a sample taken before the horizon would price the
    exit on information the exit did not have. `ts` here is the horizon lower
    bound -- the live loop exits on the first poll after it, up to one rotation
    later -- so the priced bid is the best case for the exit, not its mean.
    """
    if book is None:
        return None
    col = "best_bid_up" if side == "UP" else "best_bid_down"
    row = book.execute(
        f"SELECT {col} AS bid FROM book_samples"
        f" WHERE condition_id = ? AND ts >= ? AND {col} IS NOT NULL"
        f" ORDER BY ts LIMIT 1", (cid, ts)).fetchone()
    return float(row["bid"]) if row else None


def sweep(store: Path, book_store: Optional[Path]) -> str:
    conn = _ro(store)
    book = _ro(book_store) if book_store else None
    try:
        events = leg_events(conn)
        pairs = pair_timelines(events)
        opened_sh = sum(p["open_qty"] for p in pairs)
        out = [f"grace sweep -- {store}",
               f"{len(events)} fills across {len(pairs)} pairs, "
               f"{opened_sh:.0f} shares opened a position",
               ""]
        if not pairs:
            out.append("no pairs recorded; nothing to sweep")
            return "\n".join(out)

        gaps = sorted(g for g in (first_companion_gap(p) for p in pairs)
                      if g is not None)
        out.append("COMPANION WAIT -- when the first opposite share arrived")
        if gaps:
            out.append(f"  pairs with any companion : {len(gaps)}/{len(pairs)}")
            out.append(f"  first-share wait seconds : min {gaps[0]:.1f}  "
                       f"median {statistics.median(gaps):.1f}  max {gaps[-1]:.1f}")
        else:
            out.append(f"  pairs with any companion : 0/{len(pairs)} -- "
                       "no opposite share ever filled")
        out.append("")

        out.append("EVERY GRACE VALUE, FROM THE ONE RUN")
        out.append("  merged  = opposite shares in within the horizon -- these pair and merge")
        out.append("  exposed = opened shares still uncovered -- dumped at the bid")
        out.append(f"  the {POLL_INTERVAL_SEC:.0f}s row is the floor: the loop cannot exit "
                   "before its own poll, and every")
        out.append(f"  real exit lands 0-{POLL_INTERVAL_SEC:.0f}s LATER than the horizon "
                   "shown -- the exit $ is a best case")
        out.append(f"  {'grace':>7}{'merged sh':>11}{'exposed sh':>12}"
                   f"{'merge $':>10}{'exit $':>13}{'net $':>13}")
        ambiguous = 0
        for h in HORIZONS:
            merged_sh = exposed_sh = exit_usd = 0.0
            exposed_pairs = priced_pairs = 0
            for p in pairs:
                opened_h = opened_by(p, p["open_ts"] + h)
                got = companion_by(p, p["open_ts"] + h)
                merged = min(opened_h, got)
                exposed = opened_h - merged
                merged_sh += merged
                exposed_sh += exposed
                gap = first_companion_gap(p)
                if gap is not None and h < gap <= h + POLL_INTERVAL_SEC:
                    ambiguous += 1
                if exposed <= 0:
                    continue
                exposed_pairs += 1
                bid = bid_at(book, p["cid"], p["open_side"], p["open_ts"] + h)
                if bid is not None:
                    exit_usd += (bid - p["open_price"]) * exposed
                    priced_pairs += 1
            merge_usd = merged_sh * MERGE_GAIN_PER_SHARE
            if exposed_pairs == 0:
                # A measured zero: nothing was left to sell.
                exit_cell, net_cell = f"{0.0:>13.4f}", f"{merge_usd:>13.4f}"
            elif priced_pairs == exposed_pairs:
                exit_cell = f"{exit_usd:>13.4f}"
                net_cell = f"{merge_usd + exit_usd:>13.4f}"
            else:
                # Partial pricing is not a total. Withhold both.
                exit_cell = f"{f'{priced_pairs}of{exposed_pairs}priced':>13}"
                net_cell = f"{'unmeasured':>13}"
            out.append(f"  {h:>7.0f}{merged_sh:>11.0f}{exposed_sh:>12.0f}"
                       f"{merge_usd:>10.4f}{exit_cell}{net_cell}")
        if ambiguous:
            out.append("")
            out.append(f"  ~ {ambiguous} pair-horizon case(s) had a companion arrive "
                       "within one poll of the horizon;")
            out.append("    the poll phase is not recorded, so their merged/exposed "
                       "split could go either way live.")
        if book is None:
            out.append("")
            out.append("  exit column unmeasured: pass --book-db with a "
                       "book_tape_recorder store covering the same window.")
        return "\n".join(out)
    finally:
        conn.close()
        if book is not None:
            book.close()


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True, help="shadow store held at max grace")
    ap.add_argument("--book-db", default=None,
                    help="book_tape_recorder store covering the same window")
    args = ap.parse_args(argv)
    print(sweep(Path(args.db),
                Path(args.book_db) if args.book_db else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
