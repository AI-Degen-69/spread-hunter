"""Turn the probe's quotable MOMENTS into pairs per day and dollars per day.

    python scripts/family_fill_report.py                     # whole store
    python scripts/family_fill_report.py --hours 6           # last 6 hours
    python scripts/family_fill_report.py --count-adverse     # upper bound
    python scripts/family_fill_report.py --run-id all        # pool every run

Read-only. Opens the store with `mode=ro` and never writes to it.

ONE RUN, AND ONLY THE HOURS IT WATCHED. The store is resumable, so it holds
every probe that ever wrote to it; the newest run is read unless `--run-id`
says otherwise. Within that run, the per-day denominator counts only intervals
the probe was actually sampling -- a missed cycle or a machine asleep is time
no moment could have been found in, and dividing by it reports the strategy as
worse than it is.

`family_probe_report.py` answers "how often does a family LOOK quotable" --
`pair < bot_pos_price_max_pair_cost_usd` and both queues inside the bar, counted on snapshots.
That number cannot be spent. A moment that looks quotable pays nothing unless
the tape then trades THROUGH BOTH of the levels we quoted, in the same window,
before the touch moves off them. This script walks each qualifying moment
forward through the cycles that follow it and asks exactly that.

The output is the number the strategy is decided on:

  * PAIRS/D -- completed pairs per day. Both legs filled inside the horizon.
    This is revenue.
  * SNGL/D -- moments where exactly ONE leg filled: a naked directional
    position nobody decided to take, unwound by crossing the spread on that
    leg. This is cost, and it is why a high moment count is not good news on
    its own.
  * NET $/D -- `pairs x edge x shares` minus the modelled unwind cost of the
    singles. A family can be top of the pass% table and negative here.

MODEL, AND WHERE IT IS WRONG. Fills are simulated, not observed; the probe
records books and tape, not our orders. Three assumptions carry the result:

  1. Drain rate at a level is `vol_at_level / tape_span_min` from the cycle
     covering the interval -- the same long-run average `queue_minutes_at`
     already uses, so this agrees with the queue bar by construction. It is a
     LOWER bound on progress, because a real queue also advances when orders
     ahead cancel, which at these depths is likely the dominant mechanism.
  2. A leg is dead the moment its level stops being the touch. Strict, and
     wrong in one direction: when the market trades DOWN through our bid we
     would in fact be filled, adversely. `--count-adverse` counts those as
     fills and prints the upper bound instead.
  3. One live quote per market. After a moment is simulated, later moments in
     the same market are skipped until that simulation's horizon has elapsed
     (`--cooldown-min`). Without it, six-minute cycles report the same standing
     opportunity ten times and pairs-per-day is inflated by an order of
     magnitude.

So the strict number is a floor and `--count-adverse` is a ceiling. The pair
of them is the answer; either alone is a number to argue with.
"""
from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

DEFAULT_DB = "runtime/family_probe.db"
DEFAULT_QUEUE_BAR = 15.0
DEFAULT_PAIR_BAR = 0.99
# `pairs_exit_window_sec` (900s) is when a naked leg is called stale and
# unwound, so a pair that has not completed by then never completes.
DEFAULT_HORIZON_MIN = 15.0
DEFAULT_SIZE_USD = 15.0
# The probe cycles every ~6 minutes, and it does miss cycles. A cycle that
# arrives long after the previous one attests to its own instant, not to the
# hole in front of it, so an interval this long ends the moment unobserved
# rather than crediting a whole queue's worth of drain nobody watched.
DEFAULT_MAX_GAP_MIN = 12.0
# `1.0 - 0.58` is 0.42000000000000004 in binary floating point, and a level
# that matches ours exactly is the one case this comparison exists to catch.
PRICE_EPS = 1e-6

UP, DOWN = "up", "down"


@dataclass(frozen=True)
class Moment:
    """One qualifying entry and what happened to the two legs it quoted."""
    family: str
    condition_id: str
    ts: float
    gate_pass: bool
    pair_cost: float
    shares: float
    spread_up: Optional[float]
    up_filled: bool
    down_filled: bool
    adverse: bool

    @property
    def edge_usd(self) -> float:
        """Dollars booked if both legs filled: `(1.00 - pair) x shares`."""
        return (1.0 - self.pair_cost) * self.shares

    @property
    def is_pair(self) -> bool:
        return self.up_filled and self.down_filled

    @property
    def is_single(self) -> bool:
        return self.up_filled != self.down_filled

    def single_cost_usd(self) -> float:
        """Cost of unwinding one naked leg by crossing its own spread.

        The spread at entry is the best evidence in the store for what the
        exit costs. A moment with no readable spread is charged nothing, which
        understates cost -- flagged here rather than guessed at.
        """
        if not self.is_single or self.spread_up is None:
            return 0.0
        return float(self.spread_up) * self.shares


@dataclass(frozen=True)
class FamilyFill:
    family: str
    moments: int
    pairs: int
    singles: int
    markets: int
    pair_pct: float
    pairs_per_day: float
    singles_per_day: float
    gross_per_day: float
    cost_per_day: float
    gate: str

    @property
    def net_per_day(self) -> float:
        return self.gross_per_day - self.cost_per_day


ALL_RUNS = "all"


def newest_run_id(conn: sqlite3.Connection) -> Optional[str]:
    """The run that wrote the most recent sample."""
    row = conn.execute(
        "SELECT run_id FROM probe_samples ORDER BY ts DESC LIMIT 1").fetchone()
    return None if row is None else str(row[0])


def load_rows(db_path: Path | str, hours: Optional[float],
              run_id: Optional[str] = None) -> list[dict]:
    """Every sample in the window, ordered so each market is a timeline.

    ONE RUN BY DEFAULT. The store is resumable, so it holds every probe that
    ever wrote to it, and consecutive runs are separated by however long the
    machine was idle. Pooling them divides real pairs by hours nobody was
    watching, and reports the strategy as worse than it is. Pass
    `--run-id all` to pool them anyway, or a run id to pick one.

    `--hours` counts back from the newest sample IN THE CHOSEN RUN, not in the
    store. Measured globally, `--run-id <old> --hours 1` walks back one hour
    from a newer run's last sample and returns nothing at all -- an empty
    report that reads as "this run found nothing" rather than "you asked about
    an hour this run was not running".
    """
    uri = f"file:{Path(db_path).as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        if run_id == ALL_RUNS:
            newest = conn.execute(
                "SELECT MAX(ts) FROM probe_samples").fetchone()[0]
            if newest is None:
                return []
            floor = 0.0 if hours is None else float(newest) - hours * 3600.0
            return [dict(row) for row in conn.execute(
                "SELECT * FROM probe_samples WHERE ts >= ? "
                "ORDER BY condition_id, ts", (floor,)).fetchall()]
        chosen = run_id or newest_run_id(conn)
        newest = conn.execute(
            "SELECT MAX(ts) FROM probe_samples WHERE run_id = ?",
            (chosen,)).fetchone()[0]
        if newest is None:
            return []
        floor = 0.0 if hours is None else float(newest) - hours * 3600.0
        return [dict(row) for row in conn.execute(
            "SELECT * FROM probe_samples WHERE ts >= ? AND run_id = ? "
            "ORDER BY condition_id, ts", (floor, chosen)).fetchall()]
    finally:
        conn.close()


def qualifies(row: dict, queue_bar: float, pair_bar: float) -> bool:
    """The entry condition, identical to `family_probe_report._passes`.

    A missing queue reading is not a pass: `queue_minutes_at` returns infinity
    when no volume traded at our level and the probe stores that as NULL, so
    "we never saw a trade at our price" stays the refusal it is. A moment with
    no readable resting size is refused for the same reason -- there is nothing
    to walk a queue against.
    """
    queue, pair = row.get("qmin_worst"), row.get("touch_pair_cost")
    if queue is None or pair is None:
        return False
    if row.get("q_bid_up") is None or row.get("q_ask_up") is None:
        return False
    if row.get("best_bid_up") is None or row.get("best_ask_up") is None:
        return False
    return float(queue) <= queue_bar and float(pair) < pair_bar


def _rate(volume, span_min) -> float:
    """Shares per minute traded at a level, 0.0 when nothing is measurable."""
    if volume is None or span_min is None or float(span_min) <= 0:
        return 0.0
    return max(0.0, float(volume)) / float(span_min)


def _level_state(row: dict, leg: str, price: float) -> str:
    """Whether our resting level is still the touch on `leg`.

    Returns "intact", "left" (the market bid through us and we are behind the
    queue at a worse level) or "adverse" (the touch fell below our price, so we
    are about to be run over -- a fill, but on the wrong side of the move).
    """
    key = "best_bid_up" if leg == UP else "best_ask_up"
    touch = row.get(key)
    if touch is None:
        return "left"
    touch = float(touch)
    if abs(touch - price) <= PRICE_EPS:
        return "intact"
    # A DOWN leg is quoted as a bid on DOWN, which is the mirror of the UP ask,
    # so "the market moved away from us" is the opposite direction there.
    moved_away = (touch > price + PRICE_EPS if leg == UP
                  else touch < price - PRICE_EPS)
    return "left" if moved_away else "adverse"


def simulate_moment(entry: dict, forward: list[dict], horizon_min: float,
                    size_usd: float, count_adverse: bool,
                    max_gap_min: float = DEFAULT_MAX_GAP_MIN) -> Moment:
    """Walk one qualifying moment forward and say which legs filled.

    `forward` is the same market's later samples, in time order. Each interval
    drains each leg's queue at that cycle's measured rate for as long as the
    level is still the touch; a leg fills once the drain covers the size ahead
    of us plus our own. An interval longer than `max_gap_min` ends the walk
    unobserved: the probe missed cycles, and a rate read at the far end of a
    hole is not evidence about the hole.
    """
    pair_cost = float(entry["touch_pair_cost"])
    shares = size_usd / pair_cost if pair_cost > 0 else 0.0
    prices = {UP: float(entry["best_bid_up"]),
              DOWN: float(entry["best_ask_up"])}
    need = {UP: float(entry["q_bid_up"]) + shares,
            DOWN: float(entry["q_ask_up"]) + shares}
    drained = {UP: 0.0, DOWN: 0.0}
    alive = {UP: True, DOWN: True}
    filled = {UP: False, DOWN: False}
    adverse = False
    previous = float(entry["ts"])
    deadline = previous + horizon_min * 60.0

    for row in forward:
        stamp = float(row["ts"])
        if (stamp - previous) / 60.0 > max_gap_min:
            break
        minutes = max(0.0, (min(stamp, deadline) - previous) / 60.0)
        previous = stamp
        for leg, volume_key in ((UP, "vol_at_bid_up"),
                                (DOWN, "vol_at_ask_up")):
            if filled[leg] or not alive[leg]:
                continue
            state = _level_state(row, leg, prices[leg])
            if state != "intact":
                adverse = adverse or state == "adverse"
                if state == "adverse" and count_adverse:
                    filled[leg] = True
                alive[leg] = False
                continue
            if minutes > 0:
                drained[leg] += _rate(row.get(volume_key),
                                      row.get("tape_span_min")) * minutes
                if drained[leg] >= need[leg]:
                    filled[leg] = True
        done = all(filled[leg] or not alive[leg] for leg in (UP, DOWN))
        if done or stamp >= deadline:
            break

    return Moment(
        family=str(entry["family"]), condition_id=str(entry["condition_id"]),
        ts=float(entry["ts"]), gate_pass=bool(entry["gate_pass"]),
        pair_cost=pair_cost, shares=shares,
        spread_up=(None if entry.get("spread_up") is None
                   else float(entry["spread_up"])),
        up_filled=filled[UP], down_filled=filled[DOWN], adverse=adverse)


def simulate(rows: list[dict], queue_bar: float, pair_bar: float,
             horizon_min: float, cooldown_min: float, size_usd: float,
             count_adverse: bool,
             max_gap_min: float = DEFAULT_MAX_GAP_MIN) -> list[Moment]:
    """Every qualifying moment in the store, one live quote per market."""
    by_market: dict[str, list[dict]] = {}
    for row in rows:
        by_market.setdefault(str(row["condition_id"]), []).append(row)
    out: list[Moment] = []
    for samples in by_market.values():
        samples.sort(key=lambda r: float(r["ts"]))
        busy_until = float("-inf")
        for index, entry in enumerate(samples):
            ts = float(entry["ts"])
            if ts < busy_until or not qualifies(entry, queue_bar, pair_bar):
                continue
            out.append(simulate_moment(
                entry, samples[index + 1:], horizon_min, size_usd,
                count_adverse, max_gap_min))
            busy_until = ts + cooldown_min * 60.0
    out.sort(key=lambda m: m.ts)
    return out


def _gate_reasons(rows: list[dict]) -> dict[str, str]:
    """The refusal each family most often recorded, or "admitted"."""
    tally: dict[str, dict[str, int]] = {}
    for row in rows:
        if row.get("gate_pass"):
            continue
        reason = str(row.get("gate_reason") or "refused")
        bucket = tally.setdefault(str(row["family"]), {})
        bucket[reason] = bucket.get(reason, 0) + 1
    return {family: max(reasons, key=lambda k: reasons[k])
            for family, reasons in tally.items()}


def summarise(moments: list[Moment], rows: list[dict],
              span_days: float) -> list[FamilyFill]:
    """One line per family, sorted by the dollars it would have made."""
    gates = _gate_reasons(rows)
    grouped: dict[str, list[Moment]] = {}
    for moment in moments:
        grouped.setdefault(moment.family, []).append(moment)
    days = max(span_days, 1e-9)
    out = []
    for family, group in grouped.items():
        pairs = [m for m in group if m.is_pair]
        singles = [m for m in group if m.is_single]
        gross = sum(m.edge_usd for m in pairs)
        cost = sum(m.single_cost_usd() for m in singles)
        out.append(FamilyFill(
            family=family, moments=len(group), pairs=len(pairs),
            singles=len(singles),
            markets=len({m.condition_id for m in group}),
            pair_pct=100.0 * len(pairs) / len(group),
            pairs_per_day=len(pairs) / days,
            singles_per_day=len(singles) / days,
            gross_per_day=gross / days, cost_per_day=cost / days,
            gate=gates.get(family, "admitted")))
    out.sort(key=lambda s: (-s.net_per_day, -s.pairs_per_day))
    return out


def _rate_basis(rows: list[dict]) -> str:
    """Over what window these samples measured the drain rate at our level.

    Rows written before the probe bounded its tape window carry NULL here, and
    their rate is the market's LIFETIME average -- a different unit from a
    recent rate, not a different value of the same one. Pooling the two would
    average a month against an hour, so the header says plainly when a run
    carries the old basis.

    Only rows that carry a rate are read. A market sampled with no book at all
    writes NULL for the window AND for the span, because there was nothing to
    measure -- it is not an old measurement, and counting it as one makes a
    fresh store report itself as MIXED.
    """
    rated = [r for r in rows if r.get("tape_span_min") is not None]
    windows = {r.get("tape_window_min") for r in rated}
    if not windows:
        return "no sample carries a measurable tape window"
    bounded = sorted(w for w in windows if w is not None)
    if not bounded:
        return ("the venue's whole tape history (LEGACY -- this rate is a "
                "lifetime average and predicts drains that do not happen)")
    if None in windows:
        return (f"MIXED: {', '.join(f'{w:.0f}min' for w in bounded)} and "
                f"legacy lifetime rows -- do not compare these to each other")
    return ", ".join(f"last {w:.0f}min of tape" for w in bounded)


def _span_days(rows: list[dict],
               max_gap_min: float = DEFAULT_MAX_GAP_MIN) -> float:
    """Days the probe was WATCHING, which is not the clock between the ends.

    Every per-day figure here is a count divided by this. A hole in the
    sampling -- a missed cycle, a machine asleep between runs -- is time no
    moment could have been found in, and counting it as denominator reports
    the strategy as worse than it is. Holes longer than `max_gap_min` are
    dropped, the same cut `simulate_moment` makes when it refuses to credit
    drain across a gap nobody watched.
    """
    stamps = sorted({float(r["ts"]) for r in rows})
    if len(stamps) < 2:
        return 0.0
    cap = max_gap_min * 60.0
    watched = sum(b - a for a, b in zip(stamps, stamps[1:]) if b - a <= cap)
    return watched / 86400.0


def report(db_path: Path, hours: Optional[float], queue_bar: float,
           pair_bar: float, horizon_min: float, cooldown_min: float,
           size_usd: float, count_adverse: bool, max_gap_min: float,
           run_id: Optional[str], top: int) -> int:
    rows = load_rows(db_path, hours, run_id)
    if not rows:
        print(f"no samples in {db_path} for the requested window")
        return 1
    days = _span_days(rows, max_gap_min)
    moments = simulate(rows, queue_bar, pair_bar, horizon_min, cooldown_min,
                       size_usd, count_adverse, max_gap_min)
    if not moments:
        print(f"{len(rows)} samples over {days * 24:.1f}h, "
              f"0 moments clear queue <= {queue_bar:.0f}min "
              f"AND pair < {pair_bar:.2f}")
        # A store on the legacy basis reports zero moments for a reason -- the
        # basis is the finding, so it has to survive the early return.
        print(f"rate basis: {_rate_basis(rows)}")
        return 1
    stats = summarise(moments, rows, days)
    mode = ("upper bound (adverse moves count as fills)" if count_adverse
            else "strict (a level that stops being the touch is dead)")
    runs = sorted({str(r["run_id"]) for r in rows})
    label = runs[0] if len(runs) == 1 else f"{len(runs)} runs pooled"
    print(f"{len(rows)} samples, {days * 24:.1f}h watched, "
          f"{len(moments)} quotable moments, {len(stats)} families "
          f"[{label}]")
    print(f"rate basis: {_rate_basis(rows)}")
    print(f"entry: queue <= {queue_bar:.0f}min AND pair < {pair_bar:.2f}; "
          f"${size_usd:.0f}/pair, {horizon_min:.0f}min horizon")
    print(f"fills: {mode}\n")
    header = (f"{'pairs/d':>8} {'sngl/d':>7} {'pair%':>6} {'moments':>8} "
              f"{'mkts':>5} {'gross$/d':>9} {'cost$/d':>8} {'net$/d':>8}  "
              f"{'gate':<30} family")
    print(header)
    print("-" * len(header))
    for stat in stats[:top]:
        print(f"{stat.pairs_per_day:>8.2f} {stat.singles_per_day:>7.2f} "
              f"{stat.pair_pct:>6.1f} {stat.moments:>8} {stat.markets:>5} "
              f"{stat.gross_per_day:>9.2f} {stat.cost_per_day:>8.2f} "
              f"{stat.net_per_day:>8.2f}  {stat.gate[:30]:<30} "
              f"{stat.family[:44]}")
    _print_totals(moments, stats, days, count_adverse)
    return 0


def _print_totals(moments: list[Moment], stats: list[FamilyFill],
                  days: float, count_adverse: bool) -> None:
    """The whole-run line, then the split the config decision turns on."""
    print("\n=== whole run ===")
    print(f"  pairs/day {sum(s.pairs_per_day for s in stats):.2f}   "
          f"singles/day {sum(s.singles_per_day for s in stats):.2f}   "
          f"net $/day {sum(s.net_per_day for s in stats):.2f}")
    adverse = sum(1 for m in moments if m.adverse)
    tail = ("counted as fills here" if count_adverse
            else "re-run with --count-adverse to count them as fills")
    print(f"  {adverse} of {len(moments)} moments saw the touch move through "
          f"a quoted level; {tail}")
    print("\n=== today's gate ===")
    days = max(days, 1e-9)
    for label, chosen in (("admitted", True), ("refused", False)):
        group = [m for m in moments if m.gate_pass is chosen]
        if not group:
            continue
        pairs = [m for m in group if m.is_pair]
        singles = [m for m in group if m.is_single]
        net = (sum(m.edge_usd for m in pairs)
               - sum(m.single_cost_usd() for m in singles)) / days
        print(f"  {label:<9} {len(group):>5} moments  "
              f"{len(pairs) / days:>6.2f} pairs/day  {net:>8.2f} net $/day")


def _invalid_argument(args) -> Optional[str]:
    """The first parameter that cannot describe a simulation, or None.

    A negative `--size-usd` buys negative shares: it LOWERS the queue depth a
    moment has to clear and then books the pair at an inverted price, so the
    run reports more fills and more profit the more nonsense it is given. A
    `--pair-bar` above 1.0 admits pairs that cost more than the dollar they
    merge into -- the one thing this whole strategy exists to refuse.
    """
    for name, value in (("--size-usd", args.size_usd),
                        ("--horizon-min", args.horizon_min),
                        ("--queue-bar", args.queue_bar),
                        ("--top", args.top)):
        if value is not None and value <= 0:
            return f"{name} must be greater than 0 (got {value})"
    for name, value in (("--hours", args.hours),
                        ("--cooldown-min", args.cooldown_min),
                        ("--max-gap-min", args.max_gap_min)):
        if value is not None and value < 0:
            return f"{name} cannot be negative (got {value})"
    if not 0.0 < args.pair_bar <= 1.0:
        return (f"--pair-bar must be in (0, 1]: a pair costing more than "
                f"$1.00 cannot profit on merge (got {args.pair_bar})")
    return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--hours", type=float, default=None)
    parser.add_argument("--queue-bar", type=float, default=DEFAULT_QUEUE_BAR)
    parser.add_argument("--pair-bar", type=float, default=DEFAULT_PAIR_BAR)
    parser.add_argument("--horizon-min", type=float,
                        default=DEFAULT_HORIZON_MIN)
    parser.add_argument("--cooldown-min", type=float, default=None,
                        help="minutes before the same market may be quoted "
                             "again (default: the horizon)")
    parser.add_argument("--size-usd", type=float, default=DEFAULT_SIZE_USD)
    parser.add_argument("--max-gap-min", type=float,
                        default=DEFAULT_MAX_GAP_MIN,
                        help="a longer hole between cycles ends the moment "
                             "unobserved instead of crediting the gap")
    parser.add_argument("--count-adverse", action="store_true",
                        help="count a level the market traded through as a "
                             "fill: the upper bound, not the estimate")
    parser.add_argument("--run-id", default=None,
                        help=f"which probe run to read (default: the newest; "
                             f"'{ALL_RUNS}' pools every run in the store)")
    parser.add_argument("--top", type=int, default=30)
    args = parser.parse_args(argv)
    invalid = _invalid_argument(args)
    if invalid:
        print(invalid)
        return 2
    path = Path(args.db)
    if not path.exists():
        print(f"no store at {path} -- run scripts/family_probe.py first")
        return 1
    cooldown = (args.horizon_min if args.cooldown_min is None
                else args.cooldown_min)
    return report(path, args.hours, args.queue_bar, args.pair_bar,
                  args.horizon_min, cooldown, args.size_usd,
                  args.count_adverse, args.max_gap_min, args.run_id,
                  args.top)


if __name__ == "__main__":
    raise SystemExit(main())
