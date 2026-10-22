"""Fill-model calibration: the shadow fill rate beside the venue's own (#474).

One question, two numbers: how far is the shadow fill model from what the
venue actually did? This module answers it per price bucket and per queue
bucket, twice over -- share fill fraction and order any-fill rate -- with a
signed gap of `shadow - live`.

READ-ONLY, BY CONSTRUCTION. Both inputs open through `file:<abs>?mode=ro` with
`uri=True`; `OrderRegistry` is never constructed on an input path (its
constructor creates and migrates tables, which against `data/orders.db` would
rewrite the production registry); no transaction is ever opened and there is
not one INSERT/UPDATE/DELETE/CREATE/ALTER/DROP statement below. A query that
fails is reported as `UNREADABLE`, never repaired.

The module imports nothing from `core_brain`, which is what makes that claim
auditable in one glance. The `kpi.py` queue boundaries are COPIED rather than
imported for the same reason: importing `kpi` would drag a report builder into
a tool whose only job is to read two files.

The shadow fill engine's credit pass is the model under measurement and is
untouched by this file. That pass is explicit about itself: one print below a
resting bid credits EVERY order resting above it, so the model can over-fill as
easily as under-fill. The gap is therefore signed both ways and the footer names
the artefact instead of claiming the model is pessimistic.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

#: Load statuses. `NO_DATA` and `UNREADABLE` are the `run_scorer` vocabulary;
#: `INSUFFICIENT` is a row status, not a load status.
OK = "OK"
NO_DATA = "NO_DATA"
UNREADABLE = "UNREADABLE"
INSUFFICIENT = "insufficient"

SIGN_RULE = "positive gap = shadow fills more than live"

PRICE_BUCKET_WIDTH = 0.10
#: Copied verbatim from `kpi.py:1723`. `lo <= q < hi`.
QUEUE_BUCKETS = ((0.0, 1.0), (1.0, 50.0), (50.0, 150.0), (150.0, 400.0), (400.0, math.inf))
#: A missing queue reading is its own bucket, never `0`. `kpi.py:1726` folds
#: NULL into the 0-1 bucket; this report does not, because a quote with no queue
#: value did not sit at the best queue. The queue BOUNDARIES stay identical.
QUEUE_UNKNOWN = "unknown"

DEFAULT_MIN_ORDERS = 10
FILL_EPSILON = 1e-9


@dataclass(frozen=True)
class OrderSample:
    """One resting maker bid: the unit of the cohort, and of both rates."""

    local_id: str
    price: float
    posted_shares: float
    queue_ahead: float | None
    status: str
    #: Summed from `fills.size` and capped at `posted_shares`.
    filled_shares: float
    overfilled: bool

    @property
    def has_fill(self) -> bool:
        return self.filled_shares > FILL_EPSILON


@dataclass(frozen=True)
class StoreLoad:
    """One input file, read once. `status` is OK / NO_DATA / UNREADABLE."""

    label: str
    path: str
    run_id: str | None
    status: str
    detail: str
    samples: tuple[OrderSample, ...] = field(default=())


@dataclass(frozen=True)
class BucketSummary:
    """One bucket on one side. A zero denominator yields `None`, never `0.0`."""

    bucket: str
    orders: int
    posted_shares: float
    filled_shares: float
    filled_orders: int
    status_counts: dict[str, int]
    share_fill_rate: float | None
    order_fill_rate: float | None


@dataclass(frozen=True)
class CalibrationRow:
    """One line of the report: the same bucket measured on both sides."""

    dimension: str          # "overall" | "price" | "queue"
    bucket: str
    shadow: BucketSummary
    live: BucketSummary
    share_gap: float | None  # shadow - live; None when not measurable
    order_gap: float | None
    status: str             # OK | INSUFFICIENT


@dataclass(frozen=True)
class CalibrationReport:
    shadow: StoreLoad
    live: StoreLoad
    min_orders: int
    overall: CalibrationRow
    price_rows: tuple[CalibrationRow, ...]
    queue_rows: tuple[CalibrationRow, ...]
    sign_rule: str = SIGN_RULE

    def to_dict(self) -> dict:
        return {
            "sign_rule": self.sign_rule,
            "min_orders": self.min_orders,
            "shadow": _store_to_dict(self.shadow),
            "live": _store_to_dict(self.live),
            "overall": _row_to_dict(self.overall),
            "price": [_row_to_dict(row) for row in self.price_rows],
            "queue": [_row_to_dict(row) for row in self.queue_rows],
        }


# One grouped query per database, never one per order. `MIN(id)` is the first
# logged quote row for that order (`quotes.id` is AUTOINCREMENT), and
# `quotes.filled` is deliberately NOT selected: attribution writes the whole
# order fill onto every duplicate quote row, so summing it double-counts.
# `fills.size` is the only numerator.
_SAMPLE_QUERY = """
SELECT o.id AS local_id, o.price, o.original_size, o.status,
       COALESCE(f.filled, 0.0) AS filled,
       q.queue_ahead
FROM orders o
JOIN (SELECT local_id, MIN(id) AS first_id FROM quotes
      WHERE local_id IS NOT NULL GROUP BY local_id) qf ON qf.local_id = o.id
JOIN quotes q ON q.id = qf.first_id
LEFT JOIN (SELECT order_uuid, SUM(size) AS filled FROM fills
           GROUP BY order_uuid) f ON f.order_uuid = o.id
WHERE o.side = 'BUY' AND o.original_size > 0 AND o.price > 0 AND o.price < 1
"""

_RUN_FILTER = "  AND o.run_id = ?\n"

_ORDER_BY = "ORDER BY o.id\n"


def _sample_from_row(row: sqlite3.Row) -> OrderSample:
    posted = float(row["original_size"])
    raw_filled = float(row["filled"] or 0.0)
    overfilled = raw_filled > posted + FILL_EPSILON
    filled = min(raw_filled, posted) if raw_filled > 0.0 else 0.0
    queue = row["queue_ahead"]
    return OrderSample(
        local_id=str(row["local_id"]),
        price=float(row["price"]),
        posted_shares=posted,
        queue_ahead=None if queue is None else float(queue),
        status=str(row["status"]),
        filled_shares=filled,
        overfilled=overfilled,
    )


def load_store(
    path: Path | str,
    *,
    run_id: str | None = None,
    label: str = "shadow",
) -> StoreLoad:
    """Read one store's cohort. Never writes, never creates, never repairs.

    `run_id` filters ORDERS only. A fill carrying a different run id than its
    order still counts for that order: the order owns its fills.
    """
    resolved = Path(path)
    if not resolved.is_file():
        return StoreLoad(
            label=label, path=str(resolved), run_id=run_id,
            status=NO_DATA, detail="file not found; nothing to read",
        )

    uri = f"file:{resolved.resolve().as_posix()}?mode=ro"
    query = _SAMPLE_QUERY
    params: list[object] = []
    if run_id:
        query += _RUN_FILTER
        params.append(run_id)
    query += _ORDER_BY

    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    except sqlite3.Error as exc:
        return StoreLoad(
            label=label, path=str(resolved), run_id=run_id,
            status=UNREADABLE, detail=str(exc) or exc.__class__.__name__,
        )
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, params).fetchall()
    except sqlite3.Error as exc:
        return StoreLoad(
            label=label, path=str(resolved), run_id=run_id,
            status=UNREADABLE, detail=str(exc) or exc.__class__.__name__,
        )
    finally:
        conn.close()

    samples = tuple(_sample_from_row(row) for row in rows)
    if not samples:
        return StoreLoad(
            label=label, path=str(resolved), run_id=run_id,
            status=NO_DATA, detail="no cohort orders (resting BUY with a quote row)",
            samples=samples,
        )
    return StoreLoad(
        label=label, path=str(resolved), run_id=run_id,
        status=OK, detail="", samples=samples,
    )


# --- Buckets ------------------------------------------------------------------

def _price_label(index: int) -> str:
    lo = index * PRICE_BUCKET_WIDTH
    hi = (index + 1) * PRICE_BUCKET_WIDTH
    return f"{lo:.2f}-{hi:.2f}"


PRICE_BUCKET_LABELS = tuple(_price_label(i) for i in range(10))


def price_bucket(price: float) -> str:
    """`lo <= price < hi` in 0.10 bands; the top band closes at 1.00.

    `round(..., 9)` before the floor: `0.3 * 10` is 2.9999999999999996 in
    binary, and a bare `int()` would file a 0.30 bid under 0.20-0.30.
    """
    scaled = round(float(price) * 10.0, 9)
    index = max(0, min(math.floor(scaled), 9))
    return _price_label(index)


def _queue_label(lo: float, hi: float) -> str:
    return f"{lo:.0f}-{hi:.0f}" if math.isfinite(hi) else f"{lo:.0f}+"


def queue_bucket(queue_ahead: float | None) -> str:
    """The `kpi.py` boundaries, with NULL kept apart as `unknown`.

    A value the boundaries cannot place (negative, NaN) is also `unknown`: the
    alternative is folding a non-reading into `0-1`, which would invent a fill
    rate at the best queue.
    """
    if queue_ahead is None:
        return QUEUE_UNKNOWN
    value = float(queue_ahead)
    for lo, hi in QUEUE_BUCKETS:
        if lo <= value < hi:
            return _queue_label(lo, hi)
    return QUEUE_UNKNOWN


_QUEUE_RANK = {_queue_label(lo, hi): n for n, (lo, hi) in enumerate(QUEUE_BUCKETS)}
_QUEUE_RANK[QUEUE_UNKNOWN] = len(_QUEUE_RANK)


def _bucket_rank(label: str) -> tuple[int, float, str]:
    """Canonical display order: price bands ascending, then queue, unknown last."""
    if label in _QUEUE_RANK:
        return (1, float(_QUEUE_RANK[label]), "")
    head = label.split("-", 1)[0]
    try:
        return (0, float(head), "")
    except ValueError:
        return (2, 0.0, label)


# --- Math ---------------------------------------------------------------------

def summarize(samples: Sequence[OrderSample], bucket: str) -> BucketSummary:
    """One bucket on one side. Every zero denominator yields `None`, not `0.0`."""
    rows = list(samples)
    orders = len(rows)
    posted = sum(s.posted_shares for s in rows)
    filled = sum(s.filled_shares for s in rows)
    filled_orders = sum(1 for s in rows if s.has_fill)
    status_counts: dict[str, int] = {}
    for s in rows:
        status_counts[s.status] = status_counts.get(s.status, 0) + 1
    return BucketSummary(
        bucket=bucket,
        orders=orders,
        posted_shares=posted,
        filled_shares=filled,
        filled_orders=filled_orders,
        status_counts=status_counts,
        share_fill_rate=(filled / posted) if posted > 0.0 else None,
        order_fill_rate=(filled_orders / orders) if orders > 0 else None,
    )


def bucket_summaries(
    samples: Sequence[OrderSample],
    key: Callable[[OrderSample], str],
) -> dict[str, BucketSummary]:
    """Non-empty buckets only, in canonical order."""
    grouped: dict[str, list[OrderSample]] = {}
    for sample in samples:
        grouped.setdefault(key(sample), []).append(sample)
    ordered = sorted(grouped, key=_bucket_rank)
    return {label: summarize(grouped[label], label) for label in ordered}


def _gap(shadow_rate: float | None, live_rate: float | None, *, min_orders: int,
         shadow_orders: int, live_orders: int) -> float | None:
    """`shadow - live`, or `None` when either side is too thin to measure."""
    if shadow_orders < min_orders or live_orders < min_orders:
        return None
    if shadow_rate is None or live_rate is None:
        return None
    return shadow_rate - live_rate


def _row(dimension: str, bucket: str, shadow: BucketSummary, live: BucketSummary,
         *, min_orders: int) -> CalibrationRow:
    share_gap = _gap(shadow.share_fill_rate, live.share_fill_rate,
                     min_orders=min_orders, shadow_orders=shadow.orders,
                     live_orders=live.orders)
    order_gap = _gap(shadow.order_fill_rate, live.order_fill_rate,
                     min_orders=min_orders, shadow_orders=shadow.orders,
                     live_orders=live.orders)
    return CalibrationRow(
        dimension=dimension,
        bucket=bucket,
        shadow=shadow,
        live=live,
        share_gap=share_gap,
        order_gap=order_gap,
        status=OK if share_gap is not None and order_gap is not None else INSUFFICIENT,
    )


def _compare_buckets(
    shadow_by: dict[str, BucketSummary],
    live_by: dict[str, BucketSummary],
    *,
    dimension: str,
    min_orders: int,
) -> list[CalibrationRow]:
    labels = sorted(set(shadow_by) | set(live_by), key=_bucket_rank)
    return [
        _row(
            dimension,
            label,
            shadow_by.get(label) or summarize([], label),
            live_by.get(label) or summarize([], label),
            min_orders=min_orders,
        )
        for label in labels
    ]


def compare(
    shadow: Sequence[OrderSample],
    live: Sequence[OrderSample],
    *,
    min_orders: int = DEFAULT_MIN_ORDERS,
) -> tuple[CalibrationRow, list[CalibrationRow], list[CalibrationRow]]:
    """Overall row, price rows, queue rows. Pure: no database, no I/O."""
    overall = _row("overall", "all", summarize(shadow, "all"), summarize(live, "all"),
                   min_orders=min_orders)
    price_rows = _compare_buckets(
        bucket_summaries(shadow, lambda s: price_bucket(s.price)),
        bucket_summaries(live, lambda s: price_bucket(s.price)),
        dimension="price", min_orders=min_orders,
    )
    queue_rows = _compare_buckets(
        bucket_summaries(shadow, lambda s: queue_bucket(s.queue_ahead)),
        bucket_summaries(live, lambda s: queue_bucket(s.queue_ahead)),
        dimension="queue", min_orders=min_orders,
    )
    return overall, price_rows, queue_rows


def build_report(shadow: StoreLoad, live: StoreLoad, *,
                 min_orders: int = DEFAULT_MIN_ORDERS) -> CalibrationReport:
    overall, price_rows, queue_rows = compare(
        shadow.samples, live.samples, min_orders=min_orders
    )
    return CalibrationReport(
        shadow=shadow,
        live=live,
        min_orders=min_orders,
        overall=overall,
        price_rows=tuple(price_rows),
        queue_rows=tuple(queue_rows),
    )


# --- Rendering ----------------------------------------------------------------

DASH = "--"


def _pct(rate: float | None) -> str:
    return DASH if rate is None else f"{rate * 100.0:6.1f}%"


def _signed(gap: float | None) -> str:
    return DASH if gap is None else f"{gap * 100.0:+6.1f}pp"


def _store_to_dict(load: StoreLoad) -> dict:
    return {
        "label": load.label,
        "path": load.path,
        "run_id": load.run_id,
        "status": load.status,
        "detail": load.detail,
        "orders": len(load.samples),
        "overfilled": [s.local_id for s in load.samples if s.overfilled],
    }


def _bucket_to_dict(summary: BucketSummary) -> dict:
    return {
        "orders": summary.orders,
        "posted_shares": summary.posted_shares,
        "filled_shares": summary.filled_shares,
        "filled_orders": summary.filled_orders,
        "status_counts": dict(summary.status_counts),
        "share_fill_rate": summary.share_fill_rate,
        "order_fill_rate": summary.order_fill_rate,
    }


def _row_to_dict(row: CalibrationRow) -> dict:
    return {
        "dimension": row.dimension,
        "bucket": row.bucket,
        "status": row.status,
        "share_gap": row.share_gap,
        "order_gap": row.order_gap,
        "shadow": _bucket_to_dict(row.shadow),
        "live": _bucket_to_dict(row.live),
    }


def render_text(report: CalibrationReport) -> str:
    """Plain text. Every unmeasurable cell is `--`, never a fabricated number."""
    lines: list[str] = []
    lines.append("Fill calibration: simulated (shadow) vs venue (live)")
    lines.append("")
    for load in (report.shadow, report.live):
        lines.append(f"  {load.label:<6} {load.status:<10} n={len(load.samples):<5} {load.path}")
        if load.run_id:
            lines.append(f"         run_id={load.run_id}")
        if load.detail:
            lines.append(f"         {load.detail}")
        flagged = [s.local_id for s in load.samples if s.overfilled]
        if flagged:
            lines.append(f"         overfilled (capped at posted): {', '.join(flagged)}")
    lines.append("")
    lines.append(f"  minimum orders per side for a gap: {report.min_orders}")
    lines.append("")

    header = (
        f"  {'bucket':<10} {'side':<7} {'n':>4} {'posted':>9} {'filled':>9} "
        f"{'share%':>7} {'order%':>7}"
    )
    lines.append("  overall")
    lines.append(header)
    for side, summary in (("shadow", report.overall.shadow), ("live", report.overall.live)):
        lines.append(_summary_line(side, summary))
    lines.append(
        f"  {'gap':<10} {'':<7} {'':>4} {'':>9} {'':>9} "
        f"{_signed(report.overall.share_gap):>7} {_signed(report.overall.order_gap):>7}"
        f"   {report.overall.status}"
    )

    for title, rows in (("by price", report.price_rows), ("by queue ahead", report.queue_rows)):
        lines.append("")
        lines.append(f"  {title}")
        if not rows:
            lines.append("    (no bucket has orders on either side)")
            continue
        lines.append(header)
        for row in rows:
            lines.append(f"    {row.bucket}")
            lines.append(_summary_line("shadow", row.shadow, indent="    "))
            lines.append(_summary_line("live", row.live, indent="    "))
            lines.append(
                f"    {'gap':<10} {'':<7} {'':>4} {'':>9} {'':>9} "
                f"{_signed(row.share_gap):>7} {_signed(row.order_gap):>7}   {row.status}"
            )
    lines.append("")
    lines.append(f"  {report.sign_rule}")
    lines.append(f"  {NO_DATA} / {UNREADABLE} inputs are reported, never repaired.")
    lines.append("  A positive gap is not automatically model error: shadow_credit_fills")
    lines.append("  credits every order resting above a print, so it can over-fill too.")
    return "\n".join(lines) + "\n"


def _summary_line(side: str, summary: BucketSummary, indent: str = "") -> str:
    return (
        f"{indent}  {summary.bucket:<10} {side:<7} {summary.orders:>4} "
        f"{summary.posted_shares:>9.1f} {summary.filled_shares:>9.1f} "
        f"{_pct(summary.share_fill_rate):>7} {_pct(summary.order_fill_rate):>7}"
    )


# --- CLI ----------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m core_brain.fill_calibration",
        description=(
            "Compare the simulated (shadow) fill rate with the venue's live fill "
            "rate. Read-only: both databases are opened with mode=ro."
        ),
    )
    parser.add_argument("--shadow-db", required=True, type=Path,
                        help="shadow order registry to read, e.g. data/NN_shadow_<stamp>.db")
    parser.add_argument("--live-db", required=True, type=Path,
                        help="live order registry to read, e.g. data/orders.db")
    parser.add_argument("--shadow-run-id", default=None,
                        help="restrict shadow orders to one run_id")
    parser.add_argument("--live-run-id", default=None,
                        help="restrict live orders to one run_id")
    parser.add_argument("--min-orders", type=int, default=DEFAULT_MIN_ORDERS,
                        help=f"orders required on each side before a gap is shown "
                             f"(default {DEFAULT_MIN_ORDERS})")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Exit 0 only when BOTH sides produced a cohort; else 1 (report still prints).

    A missing or unreadable input is not a crash -- the readable side is still
    shown -- but it is not a comparison either, so the exit code says so and a
    caller can gate on it.
    """
    args = _build_parser().parse_args(argv)
    if args.min_orders < 1:
        print("--min-orders must be >= 1")
        return 1

    shadow = load_store(args.shadow_db, run_id=args.shadow_run_id, label="shadow")
    live = load_store(args.live_db, run_id=args.live_run_id, label="live")
    report = build_report(shadow, live, min_orders=args.min_orders)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(render_text(report))
    return 0 if (shadow.status == OK and live.status == OK) else 1


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
