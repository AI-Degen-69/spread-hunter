"""Fill quality: which fills become dead legs, and why (#761).

Per-fill attribution joined to the book state at fill time and the terminal
outcome of the pair, calibrated with honest intervals, gated behind a
default-off switch, and judged on inclusive PnL.

READ-ONLY, BY CONSTRUCTION. The input opens through `file:<abs>?mode=ro` with
`uri=True`; `OrderRegistry` is never constructed on an input path (its
constructor creates and migrates tables, which against a production registry
would rewrite it); no transaction is ever opened. A query that fails is
reported as UNREADABLE, never repaired.

The module imports nothing from `core_brain` at load time, which is what
makes that claim auditable in one glance. The single exception is a
function-local reader of the z-table (`statistical_analytics.ci_z`, pure
math with no registry access): the normal multiplier lives in that table
and nowhere else. The queue bucket boundaries are COPIED from `kpi.py`
rather than imported for the same reason.

Units: `fills.venue_ts` / `recorded_ts` and `orders.posted_ts` are
milliseconds; `quotes.ts`, `closes.ts` and `resolutions.resolved_ts` are
seconds. Fill times are normalised to seconds (`fill_ts_sec`) before any
book lookup. A fill with neither timestamp is unreadable, not skipped.

Attribution limit, stated plainly: `closes` carries a `condition_id` but no
`pair_id`, so terminal PnL and outcome labels are CONDITION-level. Pairs that
share a condition split that condition's PnL pro-rata by filled shares and
share its label. Every order in the evidence window carries a `pair_id`, and
`pair_key` (pair_id, else condition_id) is the row's identity.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

#: Load statuses. `NO_DATA` and `UNREADABLE` are the `run_scorer` vocabulary;
#: `INSUFFICIENT` is a bucket status, not a load status.
OK = "OK"
NO_DATA = "NO_DATA"
UNREADABLE = "UNREADABLE"
INSUFFICIENT = "insufficient"

SIGN_RULE = "death rate = dead legs / closed fills; lift = bucket / base"
DEFAULT_MIN_OBS = 30
#: A gate threshold ships only where the rejected legs are more than this
#: many times more likely to die than the base rate (entry-gate-job.html §4).
BREAK_EVEN_LIFT = 8.9

#: Copied verbatim from `kpi.py`. `lo <= q < hi`.
QUEUE_BUCKETS = ((0.0, 1.0), (1.0, 50.0), (50.0, 150.0), (150.0, 400.0),
                 (400.0, math.inf))
QUEUE_UNKNOWN = "unknown"

#: Fixed before the build from the shadow-01 histograms (Task 3). Distance
#: from mid clusters at 2.5c-4c; implied pair cost edges are economic (a pair
#: assembled over $1.00 is a booked loss); time edges are wall-clock.
EDGE_EDGES = (0.0, 0.02, 0.03, 0.04, 0.06, math.inf)
COST_EDGES = (0.0, 0.97, 0.99, 1.00, math.inf)
TTR_EDGES = (0.0, 3600.0, 21600.0, 86400.0, math.inf)
GAP_EDGES = (0.0, 60.0, 600.0, 3600.0, math.inf)

_MS_CUTOFF = 1e11  # above this a timestamp is milliseconds, not seconds


class _BadRow(Exception):
    """A cohort row with non-finite numerics. Reported, never repaired."""


@dataclass(frozen=True)
class StoreLoad:
    """One input file, read once. `status` is OK / NO_DATA / UNREADABLE."""

    label: str
    path: str
    run_id: str | None
    status: str
    detail: str
    cohort: Cohort | None = None


@dataclass(frozen=True)
class Cohort:
    """Raw cohort rows, already filtered to one run. Dicts, never ORM."""

    orders: tuple[dict, ...] = ()
    fills: tuple[dict, ...] = ()
    quotes: tuple[dict, ...] = ()
    closes: tuple[dict, ...] = ()
    resolutions: tuple[dict, ...] = ()
    lifecycles: tuple[dict, ...] = ()


@dataclass(frozen=True)
class FillRow:
    """One fill: what the book looked like, and what the pair became."""

    trade_id: str
    order_id: str
    pair_key: str
    condition_id: str
    token_id: str
    side: str
    price: float
    size: float
    fill_ts_sec: float
    posted_ts_ms: int | None
    mid: float | None
    edge_vs_mid: float | None
    queue_ahead: float | None
    book_size: float | None
    opp_price: float | None
    implied_pair_cost: float | None
    outcome: str  # merged | settled | dead_leg | aged_out_rescue | other | open
    pair_pnl: float
    attr_pnl: float
    t_to_res_sec: float | None
    leg_gap_sec: float

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in (
            "trade_id", "order_id", "pair_key", "condition_id", "token_id",
            "side", "price", "size", "fill_ts_sec", "posted_ts_ms", "mid",
            "edge_vs_mid", "queue_ahead", "book_size", "opp_price",
            "implied_pair_cost", "outcome", "pair_pnl", "attr_pnl",
            "t_to_res_sec", "leg_gap_sec",
        )}


@dataclass(frozen=True)
class BucketSummary:
    """One calibration bucket. A zero denominator yields None, never 0.0."""

    dimension: str
    bucket: str
    n: int
    n_closed: int
    n_dead: int
    death_rate: float | None
    wilson_lo: float | None
    wilson_hi: float | None
    lift: float | None
    status: str  # OK | INSUFFICIENT

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in (
            "dimension", "bucket", "n", "n_closed", "n_dead", "death_rate",
            "wilson_lo", "wilson_hi", "lift", "status",
        )}


@dataclass(frozen=True)
class QualityReport:
    """Per-fill attribution plus cohort summary and calibration."""

    store: StoreLoad
    min_obs: int
    fills: tuple[FillRow, ...] = ()
    buckets: tuple[BucketSummary, ...] = ()
    summary: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "store": {
                "label": self.store.label,
                "path": self.store.path,
                "run_id": self.store.run_id,
                "status": self.store.status,
                "detail": self.store.detail,
            },
            "min_obs": self.min_obs,
            "fills": [r.to_dict() for r in self.fills],
            "buckets": [b.to_dict() for b in self.buckets],
            "summary": self.summary,
        }


def wilson_interval(k: int, n: int, z: float | None = None) -> tuple[float | None, float | None]:
    """Wilson 95% interval for k deaths in n closed fills. No scipy needed.

    The multiplier is read from the one table (`statistical_analytics.ci_z`)
    rather than written down here: a second literal would let a caller read
    a different 95% from the one the table holds (see the guard test
    `test_no_other_core_brain_module_writes_a_normal_multiplier_down`).
    """
    if z is None:
        # Local import, the module's single exception to the no-core_brain
        # rule: ci_z is pure math with no registry access, and the table
        # stays the only place the number lives.
        from core_brain.statistical_analytics import ci_z
        z = ci_z(95)
    if n <= 0:
        return (None, None)
    if z is None:  # unknown level has no multiplier; never invent one
        return (None, None)
    p = k / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / denom
    half = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def _finite(value: object, what: str) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise _BadRow(f"{what}={value!r}") from exc
    if not math.isfinite(number):
        raise _BadRow(f"{what}={value!r}")
    return number


def _to_sec(value: object) -> float | None:
    """Fill timestamps are ms; book timestamps are s. Normalise to seconds."""
    if value is None:
        return None
    number = _finite(value, "ts")
    return number / 1000.0 if number > _MS_CUTOFF else number


def _bucketize(value: float | None, edges: tuple, labels: tuple) -> str:
    if value is None:
        return QUEUE_UNKNOWN
    for lo, hi, label in zip(edges[:-1], edges[1:], labels):
        if lo <= value < hi:
            return label
    return labels[-1]


#: A losing entry (paid over mid) is its own intent-visible bucket (#791).
#: Shares the analysis-side name (`scripts/edge_floor_analysis.NEGATIVE`) so
#: the report, the gate, and the analysis mean the same thing by it. It is
#: blockable through `bot_entry_fill_quality_blocked_edge` like any other
#: edge band. Non-negative boundaries stay exactly as shipped under #787.
NEGATIVE_EDGE_BUCKET = "negative"
_EDGE_LABELS = ("0.00-0.02", "0.02-0.03", "0.03-0.04", "0.04-0.06", "0.06+")
_QUEUE_LABELS = ("0-1", "1-50", "50-150", "150-400", "400+")
_COST_LABELS = ("<0.97", "0.97-0.99", "0.99-1.00", "1.00+")
_TTR_LABELS = ("<1h", "1-6h", "6-24h", "24h+")
_GAP_LABELS = ("<60s", "60-600s", "600-3600s", "3600s+")


def _queue_bucket(value: float | None) -> str:
    if value is None:
        return QUEUE_UNKNOWN
    for (lo, hi), label in zip(QUEUE_BUCKETS, _QUEUE_LABELS):
        if lo <= value < hi:
            return label
    return _QUEUE_LABELS[-1]


def _features(row: FillRow) -> dict[str, str]:
    ttr = "unresolved" if row.t_to_res_sec is None else _bucketize(
        row.t_to_res_sec, TTR_EDGES, _TTR_LABELS)
    return {
        "edge": edge_bucket_for(row.edge_vs_mid),
        "depth": _queue_bucket(row.queue_ahead),
        "cost": _bucketize(row.implied_pair_cost, COST_EDGES, _COST_LABELS),
        "ttres": ttr,
        "gap": _bucketize(row.leg_gap_sec, GAP_EDGES, _GAP_LABELS),
    }


def _calibrate(rows: tuple[FillRow, ...], base_rate: float | None,
               min_obs: int) -> tuple[BucketSummary, ...]:
    groups: dict[tuple[str, str], list[FillRow]] = {}
    for row in rows:
        for dimension, bucket in _features(row).items():
            groups.setdefault((dimension, bucket), []).append(row)
    summaries: list[BucketSummary] = []
    for (dimension, bucket) in sorted(groups):
        members = groups[(dimension, bucket)]
        shut = [r for r in members if r.outcome != "open"]
        dead = sum(1 for r in shut if r.outcome == "dead_leg")
        rate = (dead / len(shut)) if shut else None
        lo, hi = wilson_interval(dead, len(shut))
        lift = (rate / base_rate
                if rate is not None and base_rate else None)
        summaries.append(BucketSummary(
            dimension=dimension, bucket=bucket, n=len(members),
            n_closed=len(shut), n_dead=dead, death_rate=rate,
            wilson_lo=lo, wilson_hi=hi, lift=lift,
            status="OK" if len(members) >= min_obs else INSUFFICIENT,
        ))
    return tuple(summaries)


def edge_bucket_for(value: float | None) -> str:
    """Intent-visible edge dimension, shared by the report and the gate.

    A negative edge is a losing entry, not the best bucket: it returns
    `NEGATIVE_EDGE_BUCKET` instead of falling through to `"0.06+"` (#791).
    `None` stays `"unknown"`; non-negative bands are unchanged (#787).
    """
    if value is not None and value < 0:
        return NEGATIVE_EDGE_BUCKET
    return _bucketize(value, EDGE_EDGES, _EDGE_LABELS)


#: #792: a floor names a price band, so an edge with no book quote behind it
#: has no band to match. The gate refuses it under this one reason string.
UNMEASURABLE_EDGE = f"blocked:edge:{QUEUE_UNKNOWN}"


def evaluate_fill(features: dict[str, str],
                  blocked: set[tuple[str, str]] | frozenset | set[str]
                  ) -> tuple[bool, str]:
    """Pure entry decision: allow unless a feature sits in a blocked bucket.

    `blocked` holds `(dimension, bucket)` pairs (or `"dim:bucket"` strings).
    No I/O, no venue, no randomness -- the shadow A/B replays through this.
    """
    normalized: set[tuple[str, str]] = set()
    for entry in blocked:
        if isinstance(entry, str):
            dim, _, bucket = entry.partition(":")
            normalized.add((dim, bucket))
        else:
            normalized.add((str(entry[0]), str(entry[1])))
    for dimension, bucket in features.items():
        if (dimension, bucket) in normalized:
            return (False, f"blocked:{dimension}:{bucket}")
    return (True, "allowed")


def evaluate_edge(value: float | None,
                  blocked: set[tuple[str, str]] | frozenset | set[str]
                  ) -> tuple[bool, str]:
    """Entry decision for the intent-visible edge floor, fail closed (#792).

    An unmeasurable edge is the case with the least support for an entry, so
    it is refused by this rule rather than passed by omission. It keeps its
    own `unknown` label in the report, where it is counted separately from
    the price bands.
    """
    if value is None:
        return (False, UNMEASURABLE_EDGE)
    return evaluate_fill({"edge": edge_bucket_for(value)}, blocked)


@dataclass(frozen=True)
class GateCandidate:
    """One break-even row: what blocking this bucket costs and saves."""

    dimension: str
    bucket: str
    status: str
    legs_rejected: int
    pairs_rejected: int
    merges_lost_n: int
    merges_lost_pnl: float
    dead_avoided_n: int
    dead_avoided_pnl: float
    net_pnl_delta: float
    lift: float | None
    clears: bool

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in (
            "dimension", "bucket", "status", "legs_rejected",
            "pairs_rejected", "merges_lost_n", "merges_lost_pnl",
            "dead_avoided_n", "dead_avoided_pnl", "net_pnl_delta",
            "lift", "clears",
        )}


def propose_gate(report: QualityReport) -> tuple[list[GateCandidate], str]:
    """One break-even row per OK bucket, plus the ship/no-ship verdict.

    `net_pnl_delta` is minus the blocked fills' attributed PnL: blocking a
    profitable fill forgoes profit, blocking a dead fill avoids its loss.
    A threshold ships only where lift clears BREAK_EVEN_LIFT *and* the net
    is positive; otherwise the verdict redirects per the §9 falsifier.
    """
    candidates: list[GateCandidate] = []
    for bucket in report.buckets:
        if bucket.status != "OK" or bucket.n_closed <= 0:
            continue
        members = [r for r in report.fills
                   if _features(r).get(bucket.dimension) == bucket.bucket]
        shut = [r for r in members if r.outcome != "open"]
        dead = [r for r in shut if r.outcome == "dead_leg"]
        kept = [r for r in shut if r.outcome != "dead_leg"]
        foregone = sum(r.attr_pnl for r in kept)
        avoided = -sum(r.attr_pnl for r in dead)
        net = -(sum(r.attr_pnl for r in members))
        lift = bucket.lift
        candidates.append(GateCandidate(
            dimension=bucket.dimension, bucket=bucket.bucket,
            status=bucket.status, legs_rejected=len(members),
            pairs_rejected=len({r.pair_key for r in members}),
            merges_lost_n=len(kept), merges_lost_pnl=foregone,
            dead_avoided_n=len(dead), dead_avoided_pnl=avoided,
            net_pnl_delta=net, lift=lift,
            clears=(lift is not None and lift >= BREAK_EVEN_LIFT and net > 0),
        ))
    candidates.sort(key=lambda c: c.net_pnl_delta, reverse=True)
    shipped = [c for c in candidates if c.clears]
    if shipped:
        verdict = (f"{len(shipped)} threshold(s) clear {BREAK_EVEN_LIFT}:1 "
                   f"with net>0 -- shippable; the rest stay out.")
    else:
        verdict = (f"no bucket clears {BREAK_EVEN_LIFT}:1 with net>0 -- "
                   "redirect search to sizing/market selection "
                   "(entry-gate-job §9 falsifier). No gate ships.")
    return candidates, verdict


def score_ab(report: QualityReport,
             blocked: set[tuple[str, str]] | frozenset | set[str]) -> dict:
    """Same window, gate off vs on, judged on total realized inclusive PnL.

    A pair whose earliest fill is blocked never opens ($0). A pair with a
    blocked later fill stays single and is scored at its observed inclusive
    outcome -- never silently dropped. Merge-only scoring is rejected by
    construction: both branches sum every method and reason.
    """
    normalized: set[tuple[str, str]] = set()
    for entry in blocked:
        if isinstance(entry, str):
            dim, _, bucket = entry.partition(":")
            normalized.add((dim, bucket))
        else:
            normalized.add((str(entry[0]), str(entry[1])))
    pairs: dict[str, list[FillRow]] = {}
    for row in report.fills:
        pairs.setdefault(row.pair_key, []).append(row)
    methods: set[str] = set()
    reasons: set[str] = set()
    if report.store.cohort is not None:
        for close in report.store.cohort.closes:
            if close.get("method") is not None:
                methods.add(str(close["method"]))
            if close.get("reason") is not None:
                reasons.add(str(close["reason"]))
    off_total = float(report.summary.get("inclusive_pnl", 0.0))
    on_total = 0.0
    excluded: list[str] = []
    single_kept = 0
    for pair_key, fills in pairs.items():
        ordered = sorted(fills, key=lambda r: r.fill_ts_sec)
        hits = [any((dim, feat) in normalized
                    for dim, feat in _features(r).items()) for r in ordered]
        if hits and hits[0]:
            excluded.append(pair_key)
            continue
        if any(hits):
            single_kept += 1
        on_total += sum(r.attr_pnl for r in ordered)
    return {
        "off_total": off_total,
        "on_total": on_total,
        "delta": on_total - off_total,
        "n_pairs": len(pairs),
        "n_excluded": len(excluded),
        "pairs_excluded": sorted(excluded),
        "n_single_kept": single_kept,
        "methods_included": sorted(methods),
        "reasons_included": sorted(reasons),
        "blocked": sorted(f"{d}:{b}" for d, b in normalized),
    }


def render_ab(ab: dict) -> str:
    lines = [
        "  shadow A/B (same window, inclusive realized PnL -- never merge-only):",
        f"  gate off: {ab['off_total']:+.2f} "
        f"gate on: {ab['on_total']:+.2f} "
        f"delta: {ab['delta']:+.2f} (n_pairs={ab['n_pairs']})",
        f"  excluded pairs: {ab['n_excluded']}, "
        f"single-kept pairs: {ab['n_single_kept']}",
        f"  methods scored: {', '.join(ab['methods_included']) or 'none'}",
        f"  reasons scored: {', '.join(ab['reasons_included']) or 'none'}",
    ]
    return "\n".join(lines) + "\n"


def _read_table(conn: sqlite3.Connection, table: str,
                run_id: str | None, has_run: bool) -> list[dict]:
    query = f"SELECT * FROM {table}"
    params: list[object] = []
    if has_run and run_id is not None:
        query += " WHERE run_id = ?"
        params.append(run_id)
    return [dict(row) for row in conn.execute(query, params).fetchall()]


def load_store(
    path: Path | str,
    *,
    run_id: str | None = None,
    label: str = "shadow",
) -> StoreLoad:
    """Read one store's cohort. Never writes, never creates, never repairs."""
    resolved = Path(path)
    if not resolved.is_file():
        return StoreLoad(
            label=label, path=str(resolved), run_id=run_id,
            status=NO_DATA, detail="file not found; nothing to read",
        )

    # `as_uri()` percent-escapes reserved characters. A raw `file:<path>?mode=ro`
    # URI is cut at a `#`, so a store named `a#b.db` opened `a` instead -- and
    # created it, read-write, because the `?mode=ro` then belonged to the
    # truncated path. `?` cannot exist in a Windows filename; `#` can.
    uri = f"{resolved.resolve().as_uri()}?mode=ro"
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    except sqlite3.Error as exc:
        return StoreLoad(
            label=label, path=str(resolved), run_id=run_id,
            status=UNREADABLE, detail=str(exc) or exc.__class__.__name__,
        )
    try:
        conn.row_factory = sqlite3.Row
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if not {"orders", "fills"} <= tables:
            return StoreLoad(
                label=label, path=str(resolved), run_id=run_id,
                status=UNREADABLE,
                detail="no orders/fills cohort tables; nothing was repaired",
            )
        cohort = Cohort(
            orders=tuple(_read_table(conn, "orders", run_id, True)),
            fills=tuple(_read_table(conn, "fills", run_id, True)),
            quotes=tuple(_read_table(conn, "quotes", run_id, False))
            if "quotes" in tables else (),
            closes=tuple(_read_table(conn, "closes", run_id, True))
            if "closes" in tables else (),
            resolutions=tuple(_read_table(conn, "resolutions", run_id, True))
            if "resolutions" in tables else (),
            lifecycles=tuple(_read_table(conn, "single_leg_lifecycle", run_id, False))
            if "single_leg_lifecycle" in tables else (),
        )
        # Validate numerics now: a non-finite price or size makes the cohort
        # unreadable as a whole, reported rather than repaired.
        for order in cohort.orders:
            _finite(order["price"], "orders.price")
            _finite(order["original_size"], "orders.original_size")
        for fill in cohort.fills:
            _finite(fill["size"], "fills.size")
            _finite(fill["price"], "fills.price")
    except (sqlite3.Error, _BadRow) as exc:
        return StoreLoad(
            label=label, path=str(resolved), run_id=run_id,
            status=UNREADABLE, detail=f"{exc}; nothing was repaired",
        )
    finally:
        if conn is not None:
            conn.close()
    return StoreLoad(
        label=label, path=str(resolved), run_id=run_id,
        status=OK, detail=f"{len(cohort.fills)} fills read",
        cohort=cohort,
    )


def _book_index(quotes: tuple[dict, ...]) -> dict[tuple[str, str], tuple[list, list]]:
    """Per (condition, token): sorted timestamps + parallel rows."""
    buckets: dict[tuple[str, str], list] = {}
    for quote in quotes:
        ts = quote.get("ts")
        if ts is None:
            continue
        try:
            ts_num = _finite(ts, "quotes.ts")
        except _BadRow:
            continue
        buckets.setdefault(
            (str(quote.get("condition_id")), str(quote.get("token_id"))), []
        ).append((ts_num, quote))
    return {
        key: ([t for t, _ in sorted(rows)], [r for _, r in sorted(rows)])
        for key, rows in buckets.items()
    }


def _asof(index: dict, condition_id: str, token_id: str,
          ts_sec: float) -> dict | None:
    """Nearest book row at or before the fill. Never a row from the future."""
    entry = index.get((condition_id, token_id))
    if not entry:
        return None
    stamps, rows = entry
    pos = bisect.bisect_right(stamps, ts_sec) - 1
    return rows[pos] if pos >= 0 else None


def _condition_outcomes(closes: tuple[dict, ...]) -> dict[str, tuple[str, float]]:
    """condition_id -> (outcome label, total realized PnL).

    Label precedence is deliberate: a condition that produced both a merge
    and a hard stop counts as a dead_leg producer, because the gate's job is
    to avoid the toxic fills, not to admire the profitable ones beside them.
    """
    by_condition: dict[str, list[dict]] = {}
    for close in closes:
        by_condition.setdefault(str(close.get("condition_id")), []).append(close)
    outcomes: dict[str, tuple[str, float]] = {}
    for condition_id, rows in by_condition.items():
        reasons = {r.get("reason") for r in rows}
        methods = {r.get("method") for r in rows}
        pnl = sum(float(r.get("realized_pnl") or 0.0) for r in rows)
        if reasons & {"lifecycle_hard_stop", "pair_cap_hedge_unreachable"}:
            label = "dead_leg"
        elif "aged_out_rescue" in reasons:
            label = "aged_out_rescue"
        elif "shadow_settlement" in methods:
            label = "settled"
        elif "shadow_merge" in methods:
            label = "merged"
        else:
            label = "other"
        outcomes[condition_id] = (label, pnl)
    return outcomes


def build_report(store: StoreLoad, *, min_obs: int = DEFAULT_MIN_OBS) -> QualityReport:
    """Attribute every fill, then calibrate. Pure: no I/O."""
    if store.status != OK or store.cohort is None:
        return QualityReport(store=store, min_obs=min_obs)
    cohort = store.cohort

    orders = {str(o["id"]): o for o in cohort.orders}
    book = _book_index(cohort.quotes)
    outcomes = _condition_outcomes(cohort.closes)
    resolved = {
        str(r["condition_id"]): _to_sec(r.get("resolved_ts"))
        for r in cohort.resolutions
    }
    # Opposite token per condition, from the quotes ledger's UP/DOWN sides.
    sides: dict[str, dict[str, str]] = {}
    for quote in cohort.quotes:
        sides.setdefault(str(quote.get("condition_id")), {})[
            str(quote.get("token_id"))] = str(quote.get("side"))

    cond_fill_size: dict[str, float] = {}
    annotated: list[dict] = []
    for fill in cohort.fills:
        order = orders.get(str(fill.get("order_uuid")))
        if order is None:
            continue  # cross-run fill for an order outside the filter
        try:
            size = _finite(fill["size"], "fills.size")
            price = _finite(fill["price"], "fills.price")
        except _BadRow:
            continue
        if size <= 0:
            continue
        venue_ts = _to_sec(fill.get("venue_ts"))
        recorded_ts = _to_sec(fill.get("recorded_ts"))
        fill_ts = venue_ts if venue_ts is not None else recorded_ts
        if fill_ts is None:
            continue
        condition_id = str(order["condition_id"])
        token_id = str(order["token_id"])
        cond_fill_size[condition_id] = cond_fill_size.get(condition_id, 0.0) + size
        quote = _asof(book, condition_id, token_id, fill_ts)
        opp_token: str | None = None
        for tok, side in sides.get(condition_id, {}).items():
            if tok != token_id and side != str(quote.get("side") if quote else None):
                opp_token = tok
                break
        if opp_token is None:
            for tok in sides.get(condition_id, {}):
                if tok != token_id:
                    opp_token = tok
                    break
        opp_quote = _asof(book, condition_id, opp_token, fill_ts) \
            if opp_token else None
        try:
            opp_price = _finite(opp_quote["price"], "opp.price") \
                if opp_quote and opp_quote.get("price") is not None else None
        except _BadRow:
            opp_price = None
        annotated.append({
            "fill": fill, "order": order, "size": size, "price": price,
            "fill_ts": fill_ts, "condition_id": condition_id,
            "token_id": token_id, "quote": quote, "opp_price": opp_price,
        })

    pair_fill_ts: dict[str, list[float]] = {}
    for item in annotated:
        order = item["order"]
        key = str(order.get("pair_id") or item["condition_id"])
        pair_fill_ts.setdefault(key, []).append(item["fill_ts"])
    leg_gap = {
        key: (max(ts) - min(ts) if len(ts) > 1 else 0.0)
        for key, ts in pair_fill_ts.items()
    }

    rows: list[FillRow] = []
    for item in annotated:
        order, quote = item["order"], item["quote"]
        key = str(order.get("pair_id") or item["condition_id"])
        label, pnl = outcomes.get(item["condition_id"], ("open", 0.0))
        cond_size = cond_fill_size[item["condition_id"]] or item["size"]
        resolved_ts = resolved.get(item["condition_id"])
        rows.append(FillRow(
            trade_id=str(item["fill"]["trade_id"]),
            order_id=str(order["id"]),
            pair_key=key,
            condition_id=item["condition_id"],
            token_id=item["token_id"],
            side=str(order.get("side")),
            price=item["price"],
            size=item["size"],
            fill_ts_sec=item["fill_ts"],
            posted_ts_ms=order.get("posted_ts"),
            mid=(float(quote["mid"]) if quote and quote.get("mid") is not None else None),
            edge_vs_mid=(float(quote["edge_vs_mid"])
                         if quote and quote.get("edge_vs_mid") is not None else None),
            queue_ahead=(float(quote["queue_ahead"])
                         if quote and quote.get("queue_ahead") is not None else None),
            book_size=(float(quote["size"])
                       if quote and quote.get("size") is not None else None),
            opp_price=item["opp_price"],
            implied_pair_cost=(item["price"] + item["opp_price"]
                               if item["opp_price"] is not None else None),
            outcome=label,
            pair_pnl=pnl,
            attr_pnl=pnl * item["size"] / cond_size,
            t_to_res_sec=(resolved_ts - item["fill_ts"]
                          if resolved_ts is not None else None),
            leg_gap_sec=leg_gap[key],
        ))

    closed = [r for r in rows if r.outcome != "open"]
    dead = [r for r in rows if r.outcome == "dead_leg"]
    by_label: dict[str, int] = {}
    for row in rows:
        by_label[row.outcome] = by_label.get(row.outcome, 0) + 1
    base_rate = (len(dead) / len(closed)) if closed else None
    summary = {
        "n_fills": len(rows),
        "n_closed_fills": len(closed),
        "n_dead": len(dead),
        "base_death_rate": base_rate,
        "inclusive_pnl": sum(r.attr_pnl for r in rows),
        "by_label": by_label,
    }
    buckets = _calibrate(tuple(rows), base_rate, min_obs)
    return QualityReport(store=store, min_obs=min_obs, fills=tuple(rows),
                         buckets=buckets, summary=summary)


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{100.0 * value:.1f}%"


def _bucket_line(bucket: BucketSummary) -> str:
    rate = _pct(bucket.death_rate)
    lo = _pct(bucket.wilson_lo)
    hi = _pct(bucket.wilson_hi)
    lift = "n/a" if bucket.lift is None else f"{bucket.lift:.1f}x"
    flag = "OK" if bucket.status == "OK" else "not-a-finding"
    return (
        f"  {bucket.dimension:<6} {bucket.bucket:<10} n={bucket.n} "
        f"closed={bucket.n_closed} dead={bucket.n_dead} rate={rate} "
        f"[{lo}-{hi}] lift={lift} {flag}"
    )


def render_text(report: QualityReport) -> str:
    lines = [
        "Fill quality report (#761): which fills become dead legs, and why.",
        f"  store: {report.store.label} {report.store.path}",
        f"  status: {report.store.status} ({report.store.detail})",
        f"  min_obs: {report.min_obs} (buckets below this are not-a-finding)",
        f"  {SIGN_RULE}",
        f"  {NO_DATA} / {UNREADABLE} inputs are reported, never repaired.",
    ]
    if report.summary:
        lines.append(
            f"  fills: {report.summary['n_fills']} "
            f"(closed: {report.summary['n_closed_fills']}, "
            f"dead: {report.summary['n_dead']}, "
            f"base death rate: {_pct(report.summary['base_death_rate'])} "
            f"on n={report.summary['n_closed_fills']})"
        )
        lines.append(
            f"  inclusive PnL: {report.summary['inclusive_pnl']:+.2f} "
            f"across {report.summary['n_fills']} fills "
            f"(n={report.summary['n_fills']})"
        )
    if report.buckets:
        lines.append("  calibration: death rate by book state at fill")
        lines.extend(_bucket_line(b) for b in report.buckets)
    return "\n".join(lines) + "\n"


def render_gate(report: QualityReport,
                candidates: list[GateCandidate], verdict: str) -> str:
    merged = [r.attr_pnl for r in report.fills if r.outcome == "merged"]
    dead = [r.attr_pnl for r in report.fills if r.outcome == "dead_leg"]
    merged_avg = sum(merged) / len(merged) if merged else None
    dead_avg = -sum(dead) / len(dead) if dead else None
    lines = ["  break-even table (one row per candidate threshold):"]
    if merged_avg is not None and dead_avg:
        lines.append(
            f"  per-fill economics: merged avg +${merged_avg:.3f} "
            f"(n={len(merged)}), dead avg -${dead_avg:.3f} (n={len(dead)}), "
            f"implied {dead_avg / merged_avg:.1f}:1 "
            f"(reference {BREAK_EVEN_LIFT}:1 per close, §4)")
    for c in candidates:
        lift = "n/a" if c.lift is None else f"{c.lift:.1f}x"
        mark = "SHIPS" if c.clears else "no-ship"
        lines.append(
            f"  {c.dimension:<6} {c.bucket:<10} rejected={c.legs_rejected} "
            f"fills/{c.pairs_rejected} pairs "
            f"merges_lost={c.merges_lost_n} ({c.merges_lost_pnl:+.2f}) "
            f"dead_avoided={c.dead_avoided_n} ({c.dead_avoided_pnl:+.2f}) "
            f"net={c.net_pnl_delta:+.2f} lift={lift} {mark}")
    lines.append(f"  verdict: {verdict}")
    return "\n".join(lines) + "\n"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m core_brain.fill_quality_report",
        description=(
            "Attribute every fill to its book state and terminal outcome."
            " Read-only: the database is opened with mode=ro."
        ),
    )
    parser.add_argument("--shadow-db", required=True, type=Path,
                        help="shadow order registry to read, e.g. data/NN_shadow_<stamp>.db")
    parser.add_argument("--run-id", default=None,
                        help="restrict rows to one run_id")
    parser.add_argument("--min-obs", type=int, default=DEFAULT_MIN_OBS,
                        help=f"observations required before a bucket is a finding"
                             f" (default {DEFAULT_MIN_OBS})")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    parser.add_argument("--propose-gate", action="store_true",
                        help="print the break-even table and ship/no-ship verdict")
    parser.add_argument("--ab", action="store_true",
                        help="score gate off vs on on inclusive realized PnL")
    parser.add_argument("--block", action="append", default=[],
                        metavar="DIM:BUCKET",
                        help="block one calibration bucket in the A/B"
                             " (repeatable, e.g. --block edge:0.02-0.03)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Exit 0 only when the input produced a cohort; else 1 (report prints)."""
    args = _build_parser().parse_args(argv)
    if args.min_obs < 1:
        print("--min-obs must be >= 1")
        return 1

    store = load_store(args.shadow_db, run_id=args.run_id)
    report = build_report(store, min_obs=args.min_obs)
    gate_text = ""
    gate_payload: dict | None = None
    candidates: list[GateCandidate] = []
    if args.propose_gate or args.ab:
        candidates, verdict = propose_gate(report)
        gate_payload = {"candidates": [c.to_dict() for c in candidates],
                        "verdict": verdict}
    if args.propose_gate and gate_payload is not None:
        gate_text = render_gate(report, candidates, gate_payload["verdict"])
    ab_text = ""
    ab_payload: dict | None = None
    if args.ab:
        shipped = [c for c in candidates if c.clears]
        blocked = set(args.block) | {f"{c.dimension}:{c.bucket}" for c in shipped}
        ab_payload = score_ab(report, blocked)
        ab_text = render_ab(ab_payload)

    if args.json:
        payload = report.to_dict()
        if gate_payload is not None:
            payload["gate"] = gate_payload
        if ab_payload is not None:
            payload["ab"] = ab_payload
        print(json.dumps(payload, indent=2, allow_nan=False))
    else:
        print(render_text(report) + gate_text + ab_text, end="")
    return 0 if store.status == OK else 1


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
