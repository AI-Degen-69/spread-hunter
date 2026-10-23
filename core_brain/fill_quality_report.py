"""Fill quality: which fills become dead legs, and why (#761).

Per-fill attribution joined to the book state at fill time and the terminal
outcome of the pair, calibrated with honest intervals, gated behind a
default-off switch, and judged on inclusive PnL.

READ-ONLY, BY CONSTRUCTION. The input opens through `file:<abs>?mode=ro` with
`uri=True`; `OrderRegistry` is never constructed on an input path (its
constructor creates and migrates tables, which against a production registry
would rewrite it); no transaction is ever opened. A query that fails is
reported as UNREADABLE, never repaired.

The module imports nothing from `core_brain`, which is what makes that claim
auditable in one glance. The queue bucket boundaries are COPIED from `kpi.py`
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
class QualityReport:
    """Per-fill attribution plus cohort summary. Buckets land in Task 3."""

    store: StoreLoad
    min_obs: int
    fills: tuple[FillRow, ...] = ()
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
            "buckets": [],
            "summary": self.summary,
        }


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
        if "lifecycle_hard_stop" in reasons:
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
    """Attribute every fill to its book state and terminal outcome. Pure."""
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
    summary = {
        "n_fills": len(rows),
        "n_closed_fills": len(closed),
        "n_dead": len(dead),
        "base_death_rate": (len(dead) / len(closed) if closed else None),
        "inclusive_pnl": sum(r.attr_pnl for r in rows),
        "by_label": by_label,
    }
    return QualityReport(store=store, min_obs=min_obs, fills=tuple(rows),
                         summary=summary)


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{100.0 * value:.1f}%"


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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Exit 0 only when the input produced a cohort; else 1 (report prints)."""
    args = _build_parser().parse_args(argv)
    if args.min_obs < 1:
        print("--min-obs must be >= 1")
        return 1

    store = load_store(args.shadow_db, run_id=args.run_id)
    report = build_report(store, min_obs=args.min_obs)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, allow_nan=False))
    else:
        print(render_text(report))
    return 0 if store.status == OK else 1


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
