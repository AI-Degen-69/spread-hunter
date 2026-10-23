"""Edge floor analysis (#787): realized money per entry-edge bucket.

The decision this feeds: should the fill-quality gate's edge floor move? The
unit of that decision is the POSITION (one pair / one condition), not the
fill: a floor blocks legs, but money arrives per pair, and a pair whose weakest
leg sits at edge X is the thing a floor at X would have removed.

Read-only, offline, deterministic. The store is opened through
`fill_quality_report.load_store` (mode=ro, never `OrderRegistry`), attribution
and book-state join are `fill_quality_report.build_report`, and the gate-on
comparison is `fill_quality_report.score_ab`. This module adds only what the
report does not carry: position-level PnL per bucket, the crossover, the floor
sweep, and the confound splits.

Two limits are deliberate and stated in the output:

* `closes` carries no pair or fill identity, so a close is attributed to a
  position by `(run_id, condition_id)` -- inferred, never exact.
* A floor exists only as a label list (`BOT_ENTRY_FILL_QUALITY_BLOCKED_EDGE`).
  A negative edge buckets as `"negative"` here, but the live gate labels it
  `"0.06+"` (`edge_bucket_for` has no negative interval), so a label floor
  cannot block it. The sweep reports those positions instead of hiding them.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import statistics
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

# Ensure repo root is importable when run as ``python scripts/edge_floor_analysis.py``.
_LIVE_ROOT = Path(__file__).resolve().parent.parent
if str(_LIVE_ROOT) not in sys.path:
    sys.path.insert(0, str(_LIVE_ROOT))

from core_brain import close_methods
from core_brain.fill_quality_report import (
    DEFAULT_MIN_OBS,
    EDGE_EDGES,
    OK,
    FillRow,
    QualityReport,
    StoreLoad,
    build_report,
    edge_bucket_for,
    load_store,
    wilson_interval,
)
from core_brain.market_meta import UNCATEGORIZED, resolve_market_meta
from core_brain.order_registry import DEFAULT_DB_PATH

NEGATIVE = "negative"
UNKNOWN = "unknown"
REHEARSAL = "rehearsal / not evidence"

#: The boundaries a floor can sit on, ascending.
SWEEP_FLOORS = EDGE_EDGES[1:-1]


@dataclass(frozen=True)
class Position:
    """One pair: both legs, the weakest leg's edge, and what it earned."""

    pair_key: str
    condition_id: str
    edge: float | None
    bucket: str
    pnl: float
    shares: float
    # Sum of the leg prices actually paid: the per-share pair cost when the
    # legs are one-for-one, and only its own leg's price for a naked position.
    paid_pair_price: float
    closed: bool

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in (
            "pair_key", "condition_id", "edge", "bucket", "pnl", "shares",
            "paid_pair_price", "closed",
        )}


@dataclass(frozen=True)
class BucketRow:
    bucket: str
    positions: int
    closed: int
    wins: int
    win_rate: float | None
    wilson_lo: float | None
    wilson_hi: float | None
    mean_pnl: float | None
    median_pnl: float | None
    total_pnl: float
    status: str

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in (
            "bucket", "positions", "closed", "wins", "win_rate", "wilson_lo",
            "wilson_hi", "mean_pnl", "median_pnl", "total_pnl", "status",
        )}


@dataclass(frozen=True)
class SweepRow:
    floor: float
    labels: tuple[str, ...]
    blocked: int
    blocked_pnl: float
    kept: int
    kept_pnl: float
    kept_win_rate: float | None
    quotes_below_floor: int
    negative_below_floor: int

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in (
            "floor", "labels", "blocked", "blocked_pnl", "kept", "kept_pnl",
            "kept_win_rate", "quotes_below_floor", "negative_below_floor",
        )}


@dataclass(frozen=True)
class GroupRow:
    group: str
    n: int
    low_n: int
    low_mean_pnl: float | None
    high_n: int
    high_mean_pnl: float | None
    holds: bool | None

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in (
            "group", "n", "low_n", "low_mean_pnl", "high_n", "high_mean_pnl",
            "holds",
        )}


@dataclass(frozen=True)
class Split:
    name: str
    groups: tuple[GroupRow, ...]
    holds: bool | None

    def to_dict(self) -> dict:
        return {"name": self.name, "holds": self.holds,
                "groups": [g.to_dict() for g in self.groups]}


def _bucket(edge: float | None) -> str:
    if edge is None:
        return UNKNOWN
    return NEGATIVE if edge < 0 else edge_bucket_for(edge)


def _edge_label_order() -> tuple[str, ...]:
    return tuple(edge_bucket_for(e) for e in EDGE_EDGES[:-1])


def position_rows(fills: Sequence[FillRow]) -> tuple[Position, ...]:
    """Group fills into positions; the bucket key is the weakest leg's edge."""
    legs: dict[str, list[FillRow]] = {}
    for row in fills:
        legs.setdefault(row.pair_key, []).append(row)
    positions: list[Position] = []
    for pair_key, group in legs.items():
        edges = [r.edge_vs_mid for r in group if r.edge_vs_mid is not None]
        edge = min(edges) if edges else None
        positions.append(Position(
            pair_key=pair_key,
            condition_id=group[0].condition_id,
            edge=edge,
            bucket=_bucket(edge),
            pnl=sum(r.attr_pnl for r in group),
            shares=sum(r.size for r in group),
            paid_pair_price=sum(r.price for r in group),
            closed=group[0].outcome != "open",
        ))
    return tuple(positions)


def bucket_table(positions: Sequence[Position],
                 min_obs: int = DEFAULT_MIN_OBS) -> tuple[BucketRow, ...]:
    """One row per bucket that holds a position, in edge order."""
    groups: dict[str, list[Position]] = {}
    for position in positions:
        groups.setdefault(position.bucket, []).append(position)
    rows: list[BucketRow] = []
    for bucket in (NEGATIVE, UNKNOWN) + _edge_label_order():
        members = groups.get(bucket)
        if not members:
            continue
        closed = [p for p in members if p.closed]
        pnls = [p.pnl for p in closed]
        wins = sum(1 for value in pnls if value > 0)
        lo, hi = wilson_interval(wins, len(pnls))
        rows.append(BucketRow(
            bucket=bucket,
            positions=len(members),
            closed=len(closed),
            wins=wins,
            win_rate=(wins / len(pnls)) if pnls else None,
            wilson_lo=lo,
            wilson_hi=hi,
            mean_pnl=statistics.fmean(pnls) if pnls else None,
            median_pnl=statistics.median(pnls) if pnls else None,
            total_pnl=sum(pnls),
            status="OK" if len(pnls) >= min_obs else "insufficient",
        ))
    return tuple(rows)


def crossover(buckets: Sequence[BucketRow]) -> float | None:
    """Lowest floor whose buckets at or above it all earn and all clear min_obs."""
    labels = _edge_label_order()
    by_label = {row.bucket: row for row in buckets}
    for floor in SWEEP_FLOORS:
        at_or_above = [by_label[label] for label, lower in zip(labels, EDGE_EDGES)
                       if label in by_label and lower >= floor]
        if not at_or_above:
            continue
        if all(row.status == "OK" and (row.mean_pnl or 0.0) > 0.0
               for row in at_or_above):
            return floor
    return None


def blocked_labels(floor: float) -> tuple[str, ...]:
    """The label list a gate at `floor` can express -- exactly the labels below it."""
    return tuple(edge_bucket_for(e) for e in EDGE_EDGES[:-1] if e < floor)


def _num(value: object) -> float | None:
    """A quote's edge, or None when the store carries junk there."""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number


def floor_sweep(positions: Sequence[Position],
                quotes: Sequence[dict],
                floors: Sequence[float] = SWEEP_FLOORS) -> tuple[SweepRow, ...]:
    """What each floor keeps and drops, in pairs and in money."""
    posted = [e for e in (_num(q.get("edge_vs_mid")) for q in quotes)
              if e is not None]
    rows: list[SweepRow] = []
    for floor in floors:
        labels = blocked_labels(floor)
        blocked = [p for p in positions if p.bucket in labels]
        kept = [p for p in positions if p.bucket not in labels]
        kept_closed = [p for p in kept if p.closed]
        kept_pnls = [p.pnl for p in kept_closed]
        below = [e for e in posted if e < floor]
        rows.append(SweepRow(
            floor=floor,
            labels=labels,
            blocked=len(blocked),
            blocked_pnl=sum(p.pnl for p in blocked if p.closed),
            kept=len(kept),
            kept_pnl=sum(kept_pnls),
            kept_win_rate=((sum(1 for v in kept_pnls if v > 0) / len(kept_pnls))
                           if kept_pnls else None),
            quotes_below_floor=len(below),
            negative_below_floor=sum(1 for e in below if e < 0),
        ))
    return tuple(rows)


def _split_group(name: str, members: Sequence[Position],
                 edge_cut: float | None) -> GroupRow:
    """One band, split at the same global edge cut as every other band."""
    closed = [p for p in members if p.closed and p.edge is not None]
    if not closed or edge_cut is None:
        return GroupRow(name, len(members), 0, None, 0, None, None)
    low = [p.pnl for p in closed if p.edge <= edge_cut]
    high = [p.pnl for p in closed if p.edge > edge_cut]
    low_mean = statistics.fmean(low) if low else None
    high_mean = statistics.fmean(high) if high else None
    holds = (high_mean > low_mean
             if low_mean is not None and high_mean is not None else None)
    return GroupRow(name, len(members), len(low), low_mean, len(high), high_mean,
                    holds)


def _split(name: str, parts: Sequence[tuple[str, Sequence[Position]]],
           edge_cut: float | None) -> Split:
    groups = tuple(_split_group(label, members, edge_cut)
                   for label, members in parts)
    verdicts = [g.holds for g in groups]
    if any(v is False for v in verdicts):
        holds: bool | None = False       # a band reversed: the effect is not safe
    elif any(v is None for v in verdicts):
        holds = None                     # a band too thin to vouch either way
    else:
        holds = True
    return Split(name=name, groups=groups, holds=holds)


def confound_splits(
    positions: Sequence[Position],
    categories: dict[str, str],
) -> tuple[Split, ...]:
    """Does the high-edge half still win inside each size / cost / category band?"""
    edges = [p.edge for p in positions if p.edge is not None]
    edge_cut = statistics.median(edges) if edges else None
    splits: list[Split] = []

    for name, key in (("shares", lambda p: p.shares),
                      ("pair_cost", lambda p: p.paid_pair_price)):
        cut = statistics.median([key(p) for p in positions]) if positions else 0.0
        splits.append(_split(name, (
            (f"<= {cut:.2f}", [p for p in positions if key(p) <= cut]),
            (f"> {cut:.2f}", [p for p in positions if key(p) > cut]),
        ), edge_cut))

    by_category: dict[str, list[Position]] = {}
    for position in positions:
        by_category.setdefault(
            categories.get(position.condition_id) or UNCATEGORIZED,
            []).append(position)
    splits.append(_split("category", tuple(sorted(by_category.items())),
                         edge_cut))
    return tuple(splits)


def data_quality(store: StoreLoad, positions: Sequence[Position]) -> dict:
    """Counts that keep every excluded observation visible."""
    cohort = store.cohort
    if cohort is None:
        return {}
    orders = {str(o.get("id")) for o in cohort.orders}
    filled_conditions = {p.condition_id for p in positions}
    bookkeeping = sum(
        1 for c in cohort.closes
        if str(c.get("method") or "") in close_methods.BOOKKEEPING_METHODS)
    return {
        "orphan_fills": sum(1 for f in cohort.fills
                            if str(f.get("order_uuid")) not in orders),
        "open_positions": sum(1 for p in positions if not p.closed),
        "negative_positions": sum(1 for p in positions if p.bucket == NEGATIVE),
        "unknown_edge_positions": sum(1 for p in positions if p.bucket == UNKNOWN),
        "unmatched_closes": sum(
            1 for c in cohort.closes
            if str(c.get("condition_id")) not in filled_conditions),
        "bookkeeping_closes_excluded": bookkeeping,
    }


def _load(path: Path | str, run_id: str | None,
          min_obs: int) -> tuple[StoreLoad, QualityReport]:
    """Load, drop bookkeeping closes, then attribute. Never edits the module."""
    raw = load_store(path, run_id=run_id)
    if raw.status != OK or raw.cohort is None:
        return raw, build_report(raw, min_obs=min_obs)
    kept = tuple(
        c for c in raw.cohort.closes
        if str(c.get("method") or "") not in close_methods.BOOKKEEPING_METHODS)
    filtered = (raw if len(kept) == len(raw.cohort.closes)
                else dataclasses.replace(
                    raw, cohort=dataclasses.replace(raw.cohort, closes=kept)))
    return raw, build_report(filtered, min_obs=min_obs)


def analyze(path: Path | str, run_id: str | None = None,
            min_obs: int = DEFAULT_MIN_OBS) -> dict:
    """The whole analysis as one JSON-safe dict; never writes to the store."""
    store, report = _load(path, run_id, min_obs)
    quotes = list(store.cohort.quotes) if store.cohort is not None else []
    # Older stores carry no run_id on quotes; filter only when the column is
    # actually there, or every quote would be dropped as "another run".
    if run_id is not None and quotes and "run_id" in quotes[0]:
        quotes = [q for q in quotes if str(q.get("run_id")) == run_id]
    positions = position_rows(report.fills)
    closes = list(store.cohort.closes) if store.cohort else []
    categories = {
        cid: resolve_market_meta(cid, closes, quotes)["category"]
        for cid in {p.condition_id for p in positions}
    }
    buckets = bucket_table(positions, min_obs)
    return {
        "store": {"path": str(path), "run_id": run_id, "status": store.status,
                  "detail": store.detail},
        "rehearsal": Path(path).resolve() != Path(DEFAULT_DB_PATH).resolve(),
        "min_obs": min_obs,
        "positions": [p.to_dict() for p in positions],
        "buckets": [b.to_dict() for b in buckets],
        "crossover": crossover(buckets),
        "sweep": [s.to_dict() for s in floor_sweep(positions, quotes)],
        "confounds": [s.to_dict()
                      for s in confound_splits(positions, categories)],
        "data_quality": data_quality(store, positions),
        "fills": {"n": len(report.fills),
                  "closed": sum(1 for p in positions if p.closed),
                  "inclusive_pnl": report.summary.get("inclusive_pnl")},
    }


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{100.0 * value:.1f}%"


def _money(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.2f}"


def render_text(result: dict) -> str:
    """The human-readable report, and the exact body of `edge_floor.md`."""
    lines = [
        "Edge floor analysis (#787): realized money by entry edge.",
        (
            f"  store: {result['store']['path']} "
            f"({result['store']['status']}: {result['store']['detail']})"
        ),
        (
            f"  run: {result['store']['run_id'] or 'all runs'}  "
            f"min_obs: {result['min_obs']}"
        ),
    ]
    if result["rehearsal"]:
        lines.append(f"  {REHEARSAL}: this store is not the live registry.")
    if result["store"]["status"] != OK:
        return "\n".join(lines) + "\n"
    fills = result["fills"]
    lines.append(
        f"  fills: {fills['n']}  positions: {len(result['positions'])} "
        f"closed: {fills['closed']}  inclusive PnL: {_money(fills['inclusive_pnl'])}")
    quality = result["data_quality"]
    lines.append(
        "  data quality: "
        + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in quality.items()))
    lines.append("  buckets (position = one pair, edge = weakest leg, "
                 "win = PnL > 0):")
    for row in result["buckets"]:
        lo, hi = row["wilson_lo"], row["wilson_hi"]
        span = (f"[{100 * lo:.0f}-{100 * hi:.0f}]"
                if lo is not None and hi is not None else "[n/a]")
        lines.append(
            f"    {row['bucket']:<10} pos={row['positions']} "
            f"closed={row['closed']} wins={row['wins']} "
            f"win_rate={_pct(row['win_rate'])} {span} "
            f"mean={_money(row['mean_pnl'])} "
            f"median={_money(row['median_pnl'])} "
            f"total={_money(row['total_pnl'])} {row['status']}")
    cross = result["crossover"]
    lines.append(
        "  crossover: " + ("none -- no floor has every bucket at or above it "
                           f"at min_obs={result['min_obs']} and earning"
                           if cross is None else f"{cross:.2f}"))
    lines.append("  floor sweep (labels = what BOT_ENTRY_FILL_QUALITY_BLOCKED_EDGE"
                 " can block):")
    for row in result["sweep"]:
        lines.append(
            f"    {row['floor']:.2f} labels={list(row['labels'])} "
            f"blocked={row['blocked']} pos ({_money(row['blocked_pnl'])}) "
            f"kept={row['kept']} pos ({_money(row['kept_pnl'])}) "
            f"kept win rate {_pct(row['kept_win_rate'])} "
            f"quotes below floor={row['quotes_below_floor']} "
            f"(negative, unblockable={row['negative_below_floor']})")
    lines.append("  confounds (does the high-edge half still win inside each band?):")
    for split in result["confounds"]:
        verdict = {True: "holds", False: "fails", None: "inconclusive"}
        lines.append(f"    {split['name']}: {verdict[split['holds']]}")
        for group in split["groups"]:
            lines.append(
                f"      {group['group']:<12} n={group['n']} "
                f"low-edge mean={_money(group['low_mean_pnl'])} (n={group['low_n']}) "
                f"high-edge mean={_money(group['high_mean_pnl'])} "
                f"(n={group['high_n']}) -> {verdict[group['holds']]}")
    return "\n".join(lines) + "\n"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/edge_floor_analysis.py",
        description="Realized money per entry-edge bucket, read-only (#787).",
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH,
                        help="order registry to read (default: the live registry)")
    parser.add_argument("--run-id", default=None,
                        help="restrict rows to one run_id")
    parser.add_argument("--min-obs", type=int, default=DEFAULT_MIN_OBS,
                        help=f"closed positions before a bucket is a finding"
                             f" (default {DEFAULT_MIN_OBS})")
    parser.add_argument("--out", type=Path, default=None,
                        help="output folder (default reports/edge_floor_<stamp>)")
    parser.add_argument("--json", action="store_true",
                        help="print the report as JSON and write nothing")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    result = analyze(args.db, args.run_id, args.min_obs)
    text = render_text(result)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["store"]["status"] == OK else 1
    if result["store"]["status"] != OK:
        # A store that opened nothing gets a printed reason, not a report
        # folder: an empty table on disk reads like a finding of zero.
        sys.stdout.write(text)
        return 1
    out = args.out or Path("reports") / (
        "edge_floor_" + time.strftime("%d-%m_%H-%M", time.gmtime()))
    out.mkdir(parents=True, exist_ok=True)
    (out / "edge_floor.json").write_text(
        json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    (out / "edge_floor.md").write_text(
        "```\n" + text + "```\n", encoding="utf-8")
    sys.stdout.write(text)
    sys.stdout.write(f"  wrote: {out / 'edge_floor.json'} and edge_floor.md\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
