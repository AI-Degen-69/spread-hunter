"""One-off: give the first live cycle's two fills their real venue timestamps.

Those rows were written by a backfill 47 minutes after the trade, and the
backfill stamped `venue_ts` with its own clock. Two metrics die on that: reconcile
lag is `recorded_ts - venue_ts`, identically zero when both are the same reading,
and every markout horizon is measured *from the fill*, so a wrong anchor turns
adverse selection into a rigorous-looking number that means nothing.

The correct values come from the venue's own `match_time` on the two trades:

    trade 66550a86-196e-4370-bfc4-b6bb1f9adb8f  match_time 1787105572  (UP  @ 0.62)
    trade 60f0becf-2490-4df5-87fe-d2bbc55adffd  match_time 1787105664  (DOWN @ 0.32)

read back from `get_trades()` on 2026-08-19 and confirmed against the CLOB's
`maker_orders` entries for our two order ids. This script exists so that
provenance is written down somewhere a future reader can check, rather than
living as two literal UPDATEs inside the schema path, where it would re-run
against every database ever created and explain itself to nobody.

Idempotent: it only moves a row whose `venue_ts` still equals the backfill clock.

    python live/scripts/repair_stage45_fill_times.py [--db live/run/live.db] [--apply]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

BACKFILL_CLOCK_MS = 1787108327493

REPAIRS = {
    "66550a86-196e-4370-bfc4-b6bb1f9adb8f_0xafb3ca08": 1787105572000,
    "60f0becf-2490-4df5-87fe-d2bbc55adffd_0xe5ed9874": 1787105664000,
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(Path(__file__).resolve().parents[1] / "run" / "live.db"))
    ap.add_argument("--apply", action="store_true",
                    help="write the change; without it the script only reports")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    changed = 0
    for trade_id, venue_ts in REPAIRS.items():
        row = conn.execute(
            "SELECT trade_id, venue_ts, recorded_ts FROM fills WHERE trade_id = ?",
            (trade_id,),
        ).fetchone()
        if row is None:
            print(f"absent   {trade_id}")
            continue
        if row["venue_ts"] == venue_ts:
            print(f"already  {trade_id}  venue_ts={venue_ts}")
            continue
        if row["venue_ts"] != BACKFILL_CLOCK_MS:
            print(f"SKIP     {trade_id}  venue_ts={row['venue_ts']} is neither the "
                  f"backfill clock nor the repaired value -- refusing to overwrite")
            continue
        print(f"repair   {trade_id}  {row['venue_ts']} -> {venue_ts} "
              f"(lag becomes {BACKFILL_CLOCK_MS - venue_ts} ms)")
        if args.apply:
            conn.execute(
                "UPDATE fills SET venue_ts = ?, recorded_ts = ? WHERE trade_id = ?",
                (venue_ts, BACKFILL_CLOCK_MS, trade_id),
            )
            changed += 1
    if args.apply:
        conn.commit()
        print(f"committed {changed} row(s)")
    else:
        print("dry run -- pass --apply to write")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
