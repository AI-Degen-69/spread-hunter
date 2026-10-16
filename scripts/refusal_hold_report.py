"""Compare the refusal-hold policies (#622) on one shadow tournament.

Answers a single question: on the same market set, which of `remove` / `keep` /
`drift` should the quoting loop ship? Five metrics per arm, in the shape the
issue asks for:

  * **Churn** -- cancels per posted order grouped by reason, cancel-then-repost
    replacements per market, and the median lifetime of a cancelled order.
  * **Stop-out rate** -- lifecycle stop closes divided by filled legs.
  * **Time-to-fill** -- `orders.posted_ts` to the first fill, CENSORED for orders
    that never filled, with a Kaplan-Meier median so unfilled orders count as
    observations instead of being dropped or read as instantaneous.
  * **Fill rate in trend versus chop**, where the label comes from a shared book
    tape so it cannot depend on any one arm's policy.
  * **Stop slippage** -- `trigger_bid - fresh_bid` and `trigger_bid - proceeds /
    shares`, with field coverage, because both columns are nullable.

Read-only, run-id scoped. Every store is opened `mode=ro`, so a report can never
alter what it reports and a missing file is never created.

Read the output as APPROXIMATELY CONCURRENT SHADOW EVIDENCE, never as a
measurement of the venue. The arms poll independently and therefore do not see
identical books, and the shadow fill model credit a bid only when a trade prints
strictly below it, with no queue simulation. A fill rate out of this report is a
model output.
"""
from __future__ import annotations

import argparse
import sqlite3
import statistics
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Optional

# Run as `python scripts/refusal_hold_report.py`, so the repo root has to be on
# the path before `core_brain` can be imported for the code revision.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Imported after the path insert above, the way the other scripts/ reports do.
from core_brain.close_methods import DASHBOARD_FORCED_EXIT_METHODS
from core_brain.kpi import NON_TRADE_CLOSE_METHODS

MS_PER_SEC = 1000.0

#: Net move / total path length at or above this is a TREND window, below it is
#: CHOP. Fixed here and echoed in the output, because a cutoff chosen after
#: seeing the results is not a cutoff.
DEFAULT_TREND_CUTOFF = 0.5

#: Close methods counted as a lifecycle stop-out -- the orphan cleanup both
#: options lean on. Taken from the canonical taxonomy in `core_brain/close_methods`
#: rather than restated here: #520 made that module the single home for these
#: strings, and a second literal list is exactly the drift it exists to prevent.
#: It covers every exit that sells a leg into the book (`single_buy_exit`,
#: `naked_exit`, `ladder_exit`), plus `aged_out_exit` and `stop_loss_exit`.
#: `single_buy_exit` is what the guarded exit writes on a rehearsal -- safety.md
#: records that the exit path is live money-path code and is deliberately NOT
#: relabelled per store, so a shadow stop-out carries the same method string a
#: live one would. The full method breakdown is printed alongside, so a store
#: using different names shows up instead of silently reporting a zero rate.
STOP_OUT_METHODS = frozenset(DASHBOARD_FORCED_EXIT_METHODS)


def _ro(path: Path) -> sqlite3.Connection:
    """Read-only connection. A `?` or `#` in the path must be escaped first."""
    conn = sqlite3.connect(
        f"file:{urllib.parse.quote(str(path))}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _has(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return column in _columns(conn, table)


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> Any:
    row = conn.execute(sql, params).fetchone()
    return None if row is None else row[0]


# --- churn ------------------------------------------------------------------

def churn(conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    """Cancels per posted order by reason, replacements, median cancelled life."""
    posted = _scalar(
        conn, "SELECT COUNT(*) FROM orders WHERE run_id = ?", (run_id,)) or 0
    reasons: dict[str, int] = {}
    for row in conn.execute(
        "SELECT COALESCE(cancel_reason, '<unrecorded>') AS r, COUNT(*) AS n "
        "FROM orders WHERE run_id = ? AND status = 'cancelled' GROUP BY r",
        (run_id,),
    ):
        reasons[row["r"]] = row["n"]
    cancelled = sum(reasons.values())

    # Replacements: a cancel with a LATER post on the same token. Counted per
    # market so one hot market cannot dominate the figure.
    per_market: dict[tuple[str, str], list[tuple[int, str]]] = {}
    for row in conn.execute(
        "SELECT condition_id, token_id, posted_ts, status FROM orders "
        "WHERE run_id = ? ORDER BY posted_ts ASC", (run_id,),
    ):
        key = (row["condition_id"] or "", row["token_id"] or "")
        per_market.setdefault(key, []).append((row["posted_ts"], row["status"]))
    replacements = 0
    markets_with_replacement = 0
    for _key, seq in per_market.items():
        count = 0
        for idx, (_ts, status) in enumerate(seq):
            if status != "cancelled":
                continue
            if any(later != "cancelled" for _lts, later in seq[idx + 1:]):
                count += 1
        replacements += count
        if count:
            markets_with_replacement += 1

    # Median cancelled-order life, from the #622 T4 column. Stores written
    # before it exist report NULL here, and the coverage count says so.
    lives: list[float] = []
    if _has(conn, "orders", "cancelled_ts"):
        unmeasured = 0
        for row in conn.execute(
            "SELECT posted_ts, cancelled_ts FROM orders "
            "WHERE run_id = ? AND status = 'cancelled'", (run_id,),
        ):
            if row["cancelled_ts"] is None:
                unmeasured += 1
                continue
            lives.append((row["cancelled_ts"] - row["posted_ts"]) / MS_PER_SEC)
    else:
        # A store written before #622 T4 has no cancel clock at all. Its
        # cancels are real and countable; they simply carry no lifetime. Saying
        # "0 measured / 0 unmeasured" would read as "no cancels", which is a
        # different and false claim.
        unmeasured = cancelled

    return {
        "posted": posted,
        "cancelled": cancelled,
        "cancel_rate": (cancelled / posted) if posted else None,
        "reasons": reasons,
        "cancels_per_reason_per_posted": {
            k: (v / posted) if posted else None for k, v in reasons.items()
        },
        "replacements": replacements,
        "markets_with_replacement": markets_with_replacement,
        "median_cancelled_life_sec": statistics.median(lives) if lives else None,
        "lives_measured": len(lives),
        "lives_unmeasured": unmeasured,
    }


# --- stop-outs ---------------------------------------------------------------

def stop_outs(conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    """Lifecycle stop closes divided by filled legs, plus the method breakdown."""
    filled_legs = _scalar(
        conn, "SELECT COUNT(DISTINCT order_uuid) FROM fills WHERE run_id = ?",
        (run_id,)) or 0
    methods: dict[str, int] = {}
    for row in conn.execute(
        "SELECT COALESCE(method, '<none>') AS m, COUNT(*) AS n FROM closes "
        "WHERE run_id = ? GROUP BY m", (run_id,),
    ):
        methods[row["m"]] = row["n"]
    stops = sum(n for m, n in methods.items()
                if m in STOP_OUT_METHODS and m not in NON_TRADE_CLOSE_METHODS)
    return {
        "filled_legs": filled_legs,
        "stop_closes": stops,
        "stop_out_rate": (stops / filled_legs) if filled_legs else None,
        "methods": methods,
    }


# --- time to fill ------------------------------------------------------------

def _km_median(durations: list[float], events: list[bool]) -> Optional[float]:
    """Kaplan-Meier median. `events[i]` False means the observation is censored.

    Dropping censored orders would report the fill speed of the orders that
    happened to fill, which is the opposite of the question -- an arm that fills
    nothing would look fastest. Counting them as instantaneous would be worse.
    Returns None when survival never reaches 0.5 inside the window, which is a
    real answer: "not reached yet", not "no data".
    """
    count = len(durations)
    if count == 0:
        return None
    order = sorted(range(count), key=lambda i: durations[i])
    at_risk = count
    survival = 1.0
    idx = 0
    while idx < count:
        moment = durations[order[idx]]
        died = 0
        end = idx
        while end < count and durations[order[end]] == moment:
            if events[order[end]]:
                died += 1
            end += 1
        if at_risk > 0 and died:
            survival *= (at_risk - died) / at_risk
            if survival <= 0.5:
                return moment
        at_risk -= (end - idx)
        idx = end
    return None


def time_to_fill(conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    """Posted to first fill, censoring orders that never filled."""
    first_fill: dict[str, float] = {}
    fetch = conn.execute(
        "SELECT order_uuid, size, price, venue_ts, recorded_ts FROM fills "
        "WHERE run_id = ?", (run_id,))
    for row in fetch:
        stamp = row["venue_ts"] if row["venue_ts"] is not None else row["recorded_ts"]
        if stamp is None:
            continue
        key = row["order_uuid"]
        if key not in first_fill or stamp < first_fill[key]:
            first_fill[key] = float(stamp)

    has_cancel_ts = _has(conn, "orders", "cancelled_ts")
    select_cancel = ", cancelled_ts" if has_cancel_ts else ""
    durations: list[float] = []
    events: list[bool] = []
    censor_secs: list[float] = []
    horizon = 0.0
    rows = []
    for row in conn.execute(
        f"SELECT id, posted_ts, status{select_cancel} FROM orders "
        "WHERE run_id = ?", (run_id,),
    ):
        rows.append(row)
        horizon = max(horizon, float(row["posted_ts"]))
    for row in rows:
        posted = float(row["posted_ts"])
        filled_at = first_fill.get(row["id"])
        if filled_at is not None and filled_at >= posted:
            durations.append((filled_at - posted) / MS_PER_SEC)
            events.append(True)
            continue
        # Censored: observed until it was cancelled, or -- when the store
        # predates the #622 T4 cancel clock -- until the last posted order in
        # the run. Never counted as a zero-second fill.
        until = horizon
        if has_cancel_ts and row["cancelled_ts"] is not None:
            until = float(row["cancelled_ts"])
        elapsed = max(0.0, (until - posted) / MS_PER_SEC)
        durations.append(elapsed)
        censor_secs.append(elapsed)
        events.append(False)

    censored = sum(1 for e in events if not e)
    return {
        "observations": len(durations),
        "filled": len(durations) - censored,
        "censored": censored,
        "km_median_sec": _km_median(durations, events),
        "median_filled_only_sec": statistics.median(
            [d for d, e in zip(durations, events) if e]) if censored < len(durations) else None,
        # Where the censored orders stopped being observed, and how long the
        # store gave them. These are what make the censoring rule checkable
        # rather than merely asserted: a run whose longest censor sits at the
        # store horizon had no cancel clock to read.
        "max_censor_sec": max(censor_secs) if censor_secs else None,
        "store_horizon_sec": horizon / MS_PER_SEC,
    }


# --- trend versus chop -------------------------------------------------------

def market_labels(book: sqlite3.Connection, cutoff: float,
                  run_id: Optional[str] = None) -> dict[str, dict[str, Any]]:
    """Label each market on the shared tape: net move / total path length.

    One tape serves every arm, so a market cannot be "trend for keep" and "chop
    for drift" -- the label is a property of the window, not of the policy.
    """
    where = " WHERE run_id = ?" if run_id else ""
    params: tuple = (run_id,) if run_id else ()
    series: dict[str, list[float]] = {}
    for row in book.execute(
        "SELECT condition_id, ts, mid_up, mid_down FROM book_samples"
        f"{where} ORDER BY condition_id, ts", params,
    ):
        mid = row["mid_up"] if row["mid_up"] is not None else row["mid_down"]
        if mid is None:
            continue
        series.setdefault(row["condition_id"] or "", []).append(float(mid))

    labels: dict[str, dict[str, Any]] = {}
    for cid, mids in series.items():
        if len(mids) < 2:
            labels[cid] = {"label": "chop", "ratio": None, "samples": len(mids)}
            continue
        net = abs(mids[-1] - mids[0])
        path = sum(abs(b - a) for a, b in zip(mids, mids[1:]))
        if path <= 0:
            # A perfectly flat tape is the definition of not trending.
            labels[cid] = {"label": "chop", "ratio": 0.0, "samples": len(mids)}
            continue
        ratio = net / path
        labels[cid] = {
            "label": "trend" if ratio >= cutoff else "chop",
            "ratio": ratio,
            "samples": len(mids),
        }
    return labels


def fill_rate_by_label(conn: sqlite3.Connection, run_id: str,
                       labels: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Posted orders and filled orders per trend/chop label."""
    filled_ids = {
        row["order_uuid"] for row in conn.execute(
            "SELECT DISTINCT order_uuid FROM fills WHERE run_id = ?", (run_id,))
    }
    buckets: dict[str, dict[str, int]] = {
        "trend": {"posted": 0, "filled": 0},
        "chop": {"posted": 0, "filled": 0},
        "unlabelled": {"posted": 0, "filled": 0},
    }
    for row in conn.execute(
        "SELECT id, condition_id FROM orders WHERE run_id = ?", (run_id,),
    ):
        entry = labels.get(row["condition_id"] or "")
        label = entry["label"] if entry else "unlabelled"
        buckets[label]["posted"] += 1
        if row["id"] in filled_ids:
            buckets[label]["filled"] += 1
    for stats in buckets.values():
        stats["fill_rate"] = (
            stats["filled"] / stats["posted"] if stats["posted"] else None)
    return buckets


# --- stop slippage -----------------------------------------------------------

def stop_slippage(conn: sqlite3.Connection, run_id: str) -> dict[str, Any]:
    """`trigger_bid - fresh_bid`, and `trigger_bid - proceeds/shares`.

    Both bid columns are nullable, so coverage is reported next to the values:
    a slippage figure computed over two closes out of forty is not a result.
    """
    trigger_gap: list[float] = []
    realized_gap: list[float] = []
    with_both = 0
    with_trigger = 0
    with_proceeds = 0
    total = 0
    extras: list[str] = []
    if _has(conn, "closes", "reason"):
        extras.append("reason")
    select_extra = (", " + ", ".join(extras)) if extras else ""
    for row in conn.execute(
        f"SELECT trigger_bid, fresh_bid, proceeds, shares{select_extra} "
        "FROM closes WHERE run_id = ?", (run_id,),
    ):
        total += 1
        trigger = row["trigger_bid"]
        fresh = row["fresh_bid"]
        if trigger is not None:
            with_trigger += 1
        if fresh is not None:
            with_both += 1
        if trigger is not None and fresh is not None:
            trigger_gap.append(float(trigger) - float(fresh))
        proceeds, shares = row["proceeds"], row["shares"]
        if trigger is not None and proceeds is not None and shares:
            with_proceeds += 1
            realized_gap.append(float(trigger) - (float(proceeds) / float(shares)))

    def _summary(values: list[float]) -> dict[str, Any]:
        return {
            "n": len(values),
            "median": statistics.median(values) if values else None,
            "worst": max(values) if values else None,
            "best": min(values) if values else None,
        }

    return {
        "closes": total,
        "with_trigger_bid": with_trigger,
        "with_fresh_bid": with_both,
        "with_proceeds_and_shares": with_proceeds,
        "trigger_minus_fresh": _summary(trigger_gap),
        "trigger_minus_realized": _summary(realized_gap),
    }


# --- run checks --------------------------------------------------------------

def run_checks(conn: sqlite3.Connection, run_id: str,
               revision: Optional[str] = None) -> dict[str, Any]:
    """Store-observed window and cycles, refusal-visit counts, code revision."""
    first = _scalar(conn, "SELECT MIN(posted_ts) FROM orders WHERE run_id = ?",
                    (run_id,))
    last = _scalar(conn, "SELECT MAX(posted_ts) FROM orders WHERE run_id = ?",
                   (run_id,))
    cycles = None
    if _columns(conn, "cycle_intent"):
        cycles = _scalar(
            conn, "SELECT COUNT(DISTINCT cycle) FROM cycle_intent WHERE run_id = ?",
            (run_id,))
    visits: dict[str, int] = {}
    if _columns(conn, "market_events"):
        for row in conn.execute(
            "SELECT COALESCE(reason_code, '<none>') AS c, COUNT(*) AS n "
            "FROM market_events WHERE run_id = ? AND kind = 'refusal_visit' "
            "GROUP BY c", (run_id,),
        ):
            visits[row["c"]] = row["n"]
    return {
        "window": (
            f"{(first / MS_PER_SEC):.0f}..{(last / MS_PER_SEC):.0f}"
            if first is not None and last is not None else "unknown"
        ),
        "window_sec": ((last - first) / MS_PER_SEC
                       if first is not None and last is not None else None),
        "cycles": cycles,
        "refusal_visits": visits,
        "refusal_visits_total": sum(visits.values()),
        # The revision THIS report ran under. An arm's own heartbeat is not read
        # here -- the tournament owns that layout -- so this must not be quoted
        # as proof of what the arm ran.
        "report_revision": revision or "unknown",
    }


# --- one arm -----------------------------------------------------------------

def arm(store: Path, run_id: str, book: Optional[sqlite3.Connection] = None,
        cutoff: float = DEFAULT_TREND_CUTOFF,
        revision: Optional[str] = None) -> dict[str, Any]:
    """Every metric for one arm, or an honest `unavailable` marker."""
    if not store.exists():
        return {"status": "unavailable", "error": f"no such store: {store}"}
    try:
        conn = _ro(store)
    except sqlite3.Error as exc:
        return {"status": "unavailable", "error": str(exc)}
    try:
        try:
            result: dict[str, Any] = {
                "status": "ok",
                "store": str(store),
                "run_id": run_id,
                "churn": churn(conn, run_id),
                "stop_outs": stop_outs(conn, run_id),
                "time_to_fill": time_to_fill(conn, run_id),
                "stop_slippage": stop_slippage(conn, run_id),
                "run": run_checks(conn, run_id, revision),
            }
        except sqlite3.Error as exc:
            return {"status": "unavailable", "error": str(exc)}
        labels = market_labels(book, cutoff) if book is not None else {}
        result["labels_available"] = bool(labels)
        result["fill_rate_by_label"] = fill_rate_by_label(conn, run_id, labels)
        result["label_counts"] = {
            "trend": sum(1 for v in labels.values() if v["label"] == "trend"),
            "chop": sum(1 for v in labels.values() if v["label"] == "chop"),
        }
        return result
    finally:
        conn.close()


# --- rendering ---------------------------------------------------------------

def _pct(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _num(value: Optional[float], digits: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _money(value: Optional[float]) -> str:
    return "n/a" if value is None else f"${value:.4f}"


def render(arms: dict[str, dict[str, Any]], cutoff: float,
           book_used: bool) -> str:
    out: list[str] = []
    out.append("# #622 refusal-hold comparison")
    out.append("")
    out.append(
        "APPROXIMATELY CONCURRENT SHADOW EVIDENCE. The arms poll independently "
        "and do\nnot see identical books, the shadow fill model credits a bid "
        "only when a trade\nprints STRICTLY BELOW it, and queue position is not "
        "simulated. These are model\noutputs, never the venue's fill "
        "probability.")
    out.append("")
    out.append(f"Trend/chop cutoff (net move / total path length): "
               f"{cutoff:g} -- at or above is TREND.")
    if not book_used:
        out.append("No book tape given: trend/chop fill rates are NOT measured "
                   "here.")
    out.append("")

    names = list(arms.keys())
    header = f"| {'metric':<44} | " + " | ".join(
        f"{name:>14}" for name in names) + " |"
    out.append(header)
    out.append("|" + "-" * 46 + "|" + "|".join("-" * 16 for _ in names) + "|")

    def line(label: str, values: list[str]) -> None:
        out.append(f"| {label:<44} | " + " | ".join(
            f"{v:>14}" for v in values) + " |")

    def get(path: list[str], default=None):
        vals = []
        for name in names:
            node: Any = arms[name]
            for key in path:
                node = node.get(key) if isinstance(node, dict) else None
                if node is None:
                    break
            vals.append(default if node is None else node)
        return vals

    line("status", [str(arms[n].get("status")) for n in names])
    line("posted orders", [str(get(["churn", "posted"], 0)[i]) for i in range(len(names))])
    line("cancels", [str(get(["churn", "cancelled"], 0)[i]) for i in range(len(names))])
    line("cancel rate (cancels / posted)",
         [_pct(v) for v in get(["churn", "cancel_rate"])])
    line("replacements (cancel then repost, same token)",
         [str(v) for v in get(["churn", "replacements"], 0)])
    line("markets with a replacement",
         [str(v) for v in get(["churn", "markets_with_replacement"], 0)])
    line("median cancelled life (s)",
         [_num(v) for v in get(["churn", "median_cancelled_life_sec"])])
    line("  cancelled lives measured / unmeasured",
         [f"{get(['churn', 'lives_measured'], 0)[i]} / "
          f"{get(['churn', 'lives_unmeasured'], 0)[i]}" for i in range(len(names))])
    out.append("")
    line("filled legs", [str(v) for v in get(["stop_outs", "filled_legs"], 0)])
    line("lifecycle stop closes",
         [str(v) for v in get(["stop_outs", "stop_closes"], 0)])
    line("STOP-OUT RATE (stops / filled legs)",
         [_pct(v) for v in get(["stop_outs", "stop_out_rate"])])
    out.append("")
    line("fill observations", [str(v) for v in get(["time_to_fill", "observations"], 0)])
    line("filled / censored",
         [f"{get(['time_to_fill', 'filled'], 0)[i]} / "
          f"{get(['time_to_fill', 'censored'], 0)[i]}" for i in range(len(names))])
    line("TIME-TO-FILL km median (s, censored included)",
         [_num(v) for v in get(["time_to_fill", "km_median_sec"])])
    line("  (filled-only median, for contrast)",
         [_num(v) for v in get(["time_to_fill", "median_filled_only_sec"])])
    out.append("")
    if book_used:
        for label in ("trend", "chop", "unlabelled"):
            line(f"fill rate -- {label}",
                 [_pct(((get(["fill_rate_by_label", label, "fill_rate"])[i])))
                  for i in range(len(names))])
            line(f"  {label}: posted / filled",
                 [f"{get(['fill_rate_by_label', label, 'posted'], 0)[i]} / "
                  f"{get(['fill_rate_by_label', label, 'filled'], 0)[i]}"
                  for i in range(len(names))])
        line("markets labelled trend / chop",
             [f"{get(['label_counts', 'trend'], 0)[i]} / "
              f"{get(['label_counts', 'chop'], 0)[i]}" for i in range(len(names))])
        out.append("")
    line("stop slippage n (trigger-fresh)",
         [str(get(["stop_slippage", "trigger_minus_fresh", "n"], 0)[i])
          for i in range(len(names))])
    line("STOP SLIPPAGE median (trigger_bid - fresh_bid)",
         [_money(v) for v in get(["stop_slippage", "trigger_minus_fresh", "median"])])
    line("  worst",
         [_money(v) for v in get(["stop_slippage", "trigger_minus_fresh", "worst"])])
    line("stop slippage n (trigger-realized)",
         [str(get(["stop_slippage", "trigger_minus_realized", "n"], 0)[i])
          for i in range(len(names))])
    line("STOP SLIPPAGE median (trigger_bid - proceeds/shares)",
         [_money(v) for v in get(["stop_slippage", "trigger_minus_realized", "median"])])
    line("closes with trigger_bid / fresh_bid",
         [f"{get(['stop_slippage', 'with_trigger_bid'], 0)[i]} / "
          f"{get(['stop_slippage', 'with_fresh_bid'], 0)[i]}"
          for i in range(len(names))])
    line("closes (total)",
         [str(v) for v in get(["stop_slippage", "closes"], 0)])
    out.append("")
    line("REFUSAL VISITS (rate denominator)",
         [str(v) for v in get(["run", "refusal_visits_total"], 0)])
    line("store window (s)", [_num(v, 0) for v in get(["run", "window_sec"])])
    line("completed cycles", [str(v) for v in get(["run", "cycles"], 0)])
    line("report-run code revision",
         [str(v) for v in get(["run", "report_revision"])])
    out.append("")

    out.append("Cancel reasons per arm (count, and per posted order):")
    for name in names:
        reasons = arms[name].get("churn", {}).get("reasons") or {}
        rates = arms[name].get("churn", {}).get("cancels_per_reason_per_posted") or {}
        if not reasons:
            out.append(f"  {name}: none")
            continue
        parts = ", ".join(
            f"{reason} {reasons[reason]} ({_pct(rates.get(reason))})"
            for reason in sorted(reasons))
        out.append(f"  {name}: {parts}")
    out.append("")

    out.append("Refusal-visit outcomes per arm "
               "(refused_hold / refused_grace_expired / refused_drift / ...):")
    for name in names:
        visits = arms[name].get("run", {}).get("refusal_visits") or {}
        out.append(f"  {name}: " + (", ".join(
            f"{code} {count}" for code, count in sorted(visits.items()))
            or "none recorded"))

    out.append("")
    out.append("Close methods per arm (so a differently-named stop shows up "
               "rather than reading as a zero stop-out rate):")
    for name in names:
        methods = arms[name].get("stop_outs", {}).get("methods") or {}
        out.append(f"  {name}: " + (", ".join(
            f"{method} {count}" for method, count in sorted(methods.items()))
            or "none"))

    for name in names:
        if arms[name].get("status") != "ok":
            out.append("")
            out.append(f"!! {name}: {arms[name].get('error')}")
    return "\n".join(out)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--arm", nargs=3, action="append", required=True,
        metavar=("NAME", "DB", "RUN_ID"),
        help="one arm: its label, its shadow store, its run id. Repeat per arm.")
    ap.add_argument("--book-db", default=None,
                    help="book_tape_recorder store, shared by every arm. "
                         "Without it trend/chop fill rates are not measured.")
    ap.add_argument("--book-run-id", default=None,
                    help="run id inside the book store; omit to read all rows")
    ap.add_argument("--trend-cutoff", type=float, default=DEFAULT_TREND_CUTOFF,
                    help=f"net move / path length for TREND (default "
                         f"{DEFAULT_TREND_CUTOFF:g})")
    ap.add_argument("--revision", default=None,
                    help="code revision label to record, e.g. from `git describe`")
    args = ap.parse_args(argv)

    revision = args.revision
    if revision is None:
        # `unknown` is a real answer (no git, or a checkout with no history), so
        # it is reported rather than raised -- but a FAILURE to import says so on
        # stderr instead of quietly masquerading as "no git", which is how this
        # read `unknown` on a machine where the revision was perfectly readable.
        try:
            from core_brain.code_revision import read_code_revision
            revision = str(read_code_revision().get("label") or "unknown")
        except ImportError as exc:
            print(f"!! could not read the code revision: {exc}", file=sys.stderr)
            revision = "unknown"

    book = None
    book_path = Path(args.book_db) if args.book_db else None
    if book_path is not None:
        if not book_path.exists():
            print(f"!! book store not found: {book_path} -- the trend/chop "
                  "columns will not be measured")
            book_path = None
        else:
            book = _ro(book_path)
    try:
        results = {
            name: arm(Path(db), run_id, book, args.trend_cutoff, revision)
            for name, db, run_id in args.arm
        }
        print(render(results, args.trend_cutoff, book is not None))
    finally:
        if book is not None:
            book.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
