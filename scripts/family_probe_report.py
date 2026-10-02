"""Read what `family_probe.py` measured and say which families are quotable.

    python scripts/family_probe_report.py                  # whole store
    python scripts/family_probe_report.py --hours 6        # last 6 hours only
    python scripts/family_probe_report.py --by-hour        # coverage by hour

Read-only. Opens the store with `mode=ro` and never writes to it.

The columns answer the three questions the snapshot could not:

  * BOOK% -- share of samples where the market had a two-sided book at all.
    A family at 0% is listed and unquotable, which is a different answer from
    a family that is absent, and the two look identical unless both columns
    are present.
  * SEEN / MKTS -- how many samples the family produced, and how many distinct
    markets they came from. A family with 200 samples from 3 markets is three
    markets watched all day, not a family that reliably has members open. Both
    numbers are needed and neither substitutes for the other.
  * Q_MED / Q_P90 -- median and 90th-percentile minutes to clear the queue on
    the worse of the two legs. The median is the typical case; the p90 is what
    a run is actually exposed to, and a family whose median is 2 minutes and
    whose p90 is 90 is not a 2-minute family.
  * PASS% -- share of samples where BOTH legs clear inside `--queue-bar` AND
    the touch pair costs under `max_pair_cost`. That conjunction is the
    strategy's entry condition; either half alone has already misled this
    project once.

GATE tells you why the bot cannot trade the family today, taken from the most
common refusal reason its samples recorded. A family that is quotable and
blocked is a config decision; a family that is quotable and merely below the
volume bar is a different one.

HOURS is coverage, not quality: the count of distinct clock hours in which the
family had at least one member open. A family present in 3 hours out of 24 is
not a day-long opportunity however good its queues look, and that distinction
is the entire reason this run exists.
"""
from __future__ import annotations

import argparse
import math
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

DEFAULT_DB = "runtime/family_probe.db"
DEFAULT_QUEUE_BAR = 15.0
DEFAULT_PAIR_BAR = 0.99


@dataclass(frozen=True)
class FamilyStat:
    family: str
    samples: int
    markets: int
    hours: int
    book_pct: float
    spread_med: Optional[float]
    pair_med: Optional[float]
    queue_med: Optional[float]
    queue_p90: Optional[float]
    pass_pct: float
    gate: str


def _percentile(values: list[float], fraction: float) -> Optional[float]:
    """The value at `fraction` through a sorted sample, nearest-rank.

    `statistics.quantiles` needs at least two points and interpolates; a family
    with one sample is a real and common case here, and reporting nothing for
    it would hide exactly the thin families this run is looking for.
    """
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[min(index, len(ordered) - 1)]


def load_rows(db_path: Path | str, hours: Optional[float]) -> list[dict]:
    uri = f"file:{Path(db_path).as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        newest = conn.execute("SELECT MAX(ts) FROM probe_samples").fetchone()[0]
        if newest is None:
            return []
        floor = 0.0 if hours is None else float(newest) - hours * 3600.0
        # No bootstrap filter. The queue is measured against the span of the
        # venue window each cycle read, so a market's first sample is as valid
        # as its tenth; dropping it would discard every family whose members
        # live shorter than two cycles, which is the population under test.
        cursor = conn.execute(
            "SELECT * FROM probe_samples WHERE ts >= ?", (floor,))
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def summarise(rows: list[dict], queue_bar: float,
              pair_bar: float) -> list[FamilyStat]:
    """One line per family, sorted by how often it clears the entry condition."""
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(str(row["family"]), []).append(row)
    out = []
    for family, samples in grouped.items():
        queues = [float(r["qmin_worst"]) for r in samples
                  if r["qmin_worst"] is not None]
        spreads = [float(r["spread_up"]) for r in samples
                   if r["spread_up"] is not None]
        pairs = [float(r["touch_pair_cost"]) for r in samples
                 if r["touch_pair_cost"] is not None]
        passing = sum(1 for r in samples if _passes(r, queue_bar, pair_bar))
        reasons: dict[str, int] = {}
        for row in samples:
            if not row["gate_pass"]:
                reason = str(row["gate_reason"] or "refused")
                reasons[reason] = reasons.get(reason, 0) + 1
        gate = ("admitted" if not reasons
                else max(reasons, key=lambda k: reasons[k]))
        out.append(FamilyStat(
            family=family, samples=len(samples),
            markets=len({r["condition_id"] for r in samples}),
            hours=len({time.localtime(float(r["ts"])).tm_hour
                       for r in samples}),
            book_pct=100.0 * sum(1 for r in samples
                                 if r.get("book_ok")) / len(samples),
            spread_med=_percentile(spreads, 0.5),
            pair_med=_percentile(pairs, 0.5),
            queue_med=_percentile(queues, 0.5),
            queue_p90=_percentile(queues, 0.9),
            pass_pct=100.0 * passing / len(samples),
            gate=gate))
    out.sort(key=lambda s: (-s.pass_pct, -s.hours, -s.samples))
    return out


def _passes(row: dict, queue_bar: float, pair_bar: float) -> bool:
    """Both legs clear inside the bar AND the touch pair is under the cap.

    A missing queue reading is NOT a pass. `queue_minutes_at` returns infinity
    when no volume traded at our level, the probe stores that as NULL, and
    "we never saw a trade at our price" is the strongest form of a refusal.
    """
    queue = row.get("qmin_worst")
    pair = row.get("touch_pair_cost")
    if queue is None or pair is None:
        return False
    return float(queue) <= queue_bar and float(pair) < pair_bar


def hour_coverage(rows: list[dict]) -> dict[str, dict[int, int]]:
    """Per family, how many markets it had open in each clock hour."""
    out: dict[str, dict[int, int]] = {}
    for row in rows:
        hour = time.localtime(float(row["ts"])).tm_hour
        bucket = out.setdefault(str(row["family"]), {})
        bucket[hour] = bucket.get(hour, 0) + 1
    return out


def _fmt(value: Optional[float], width: int, digits: int = 1) -> str:
    return " " * (width - 1) + "-" if value is None else f"{value:>{width}.{digits}f}"


def report(db_path: Path, hours: Optional[float], queue_bar: float,
           pair_bar: float, top: int, by_hour: bool) -> int:
    rows = load_rows(db_path, hours)
    if not rows:
        print(f"no samples in {db_path} for the requested window")
        return 1
    stats = summarise(rows, queue_bar, pair_bar)
    span_h = (max(float(r["ts"]) for r in rows)
              - min(float(r["ts"]) for r in rows)) / 3600.0
    print(f"{len(rows)} samples over {span_h:.1f}h, {len(stats)} families")
    print(f"pass = both legs clear <= {queue_bar:.0f}min "
          f"AND touch pair < {pair_bar:.2f}\n")
    header = (f"{'pass%':>6} {'book%':>6} {'seen':>5} {'mkts':>5} {'hrs':>4} "
              f"{'sprd':>6} {'pair':>6} {'q_med':>7} {'q_p90':>7}  "
              f"{'gate':<34} family")
    print(header)
    print("-" * len(header))
    for stat in stats[:top]:
        spread = ("     -" if stat.spread_med is None
                  else f"{stat.spread_med * 100:>5.1f}c")
        print(f"{stat.pass_pct:>6.1f} {stat.book_pct:>6.1f} "
              f"{stat.samples:>5} {stat.markets:>5} "
              f"{stat.hours:>4} {spread} {_fmt(stat.pair_med, 6, 3)} "
              f"{_fmt(stat.queue_med, 7)} {_fmt(stat.queue_p90, 7)}  "
              f"{stat.gate[:34]:<34} {stat.family[:44]}")
    if by_hour:
        print("\n=== samples per clock hour ===")
        coverage = hour_coverage(rows)
        for stat in stats[:top]:
            buckets = coverage.get(stat.family, {})
            line = "".join("#" if buckets.get(h) else "."
                           for h in range(24))
            print(f"  {line}  {stat.family[:50]}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--hours", type=float, default=None)
    parser.add_argument("--queue-bar", type=float, default=DEFAULT_QUEUE_BAR)
    parser.add_argument("--pair-bar", type=float, default=DEFAULT_PAIR_BAR)
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--by-hour", action="store_true")
    args = parser.parse_args(argv)
    path = Path(args.db)
    if not path.exists():
        print(f"no store at {path} -- run scripts/family_probe.py first")
        return 1
    return report(path, args.hours, args.queue_bar, args.pair_bar,
                  args.top, args.by_hour)


if __name__ == "__main__":
    raise SystemExit(main())
