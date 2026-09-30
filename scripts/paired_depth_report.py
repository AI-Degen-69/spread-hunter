"""Read-only paired-depth shadow report; never starts a ranker or shadow run."""
from __future__ import annotations

import argparse
import json
import math
import random
import sqlite3
import statistics
import sys
from pathlib import Path
from urllib.parse import quote


class PairedDepthReportError(RuntimeError):
    """Paired shadow data cannot support a trustworthy report."""


def _read_only(path: Path | str) -> sqlite3.Connection:
    target = Path(path).resolve()
    if not target.is_file():
        raise PairedDepthReportError(f"shadow store does not exist: {target}")
    uri = f"file:{quote(str(target).replace(chr(92), '/'))}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        return conn
    except sqlite3.Error as exc:
        raise PairedDepthReportError(f"cannot open shadow store read-only: {exc}") from exc


def _rows(conn: sqlite3.Connection, query: str, args=()) -> list[dict]:
    return [dict(row) for row in conn.execute(query, args).fetchall()]


def _require_tables(conn: sqlite3.Connection, db_path: Path | str) -> None:
    required = {
        "shadow_paired_runs", "shadow_paired_snapshots", "shadow_paired_admissions",
        "shadow_paired_orders", "shadow_paired_market_tokens", "shadow_paired_feed_events",
        "shadow_paired_market_marks", "shadow_paired_equity_marks", "orders", "fills", "closes",
    }
    found = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    missing = sorted(required - found)
    if missing:
        raise PairedDepthReportError(
            f"{db_path} is missing paired-shadow tables: {', '.join(missing)}"
        )


def _read_arm(path: Path | str, run_id: str) -> dict:
    conn = _read_only(path)
    try:
        _require_tables(conn, path)
        runs = _rows(conn, "SELECT * FROM shadow_paired_runs WHERE run_id = ?", (run_id,))
        if len(runs) != 1:
            raise PairedDepthReportError(f"run {run_id!r} not uniquely present in {path}")
        run = runs[0]
        admissions = _rows(conn,
            "SELECT * FROM shadow_paired_admissions WHERE run_id = ? ORDER BY selected_at",
            (run_id,))
        orders = _rows(conn,
            """SELECT p.*, o.posted_ts
               FROM shadow_paired_orders p
               LEFT JOIN orders o ON o.id = p.local_id
               WHERE p.run_id = ? ORDER BY p.admitted_at, p.local_id""", (run_id,))
        missing_order_count = int(conn.execute(
            """SELECT COUNT(*) FROM orders o LEFT JOIN shadow_paired_orders p
               ON p.local_id=o.id WHERE o.run_id=? AND p.local_id IS NULL""",
            (run_id,),
        ).fetchone()[0])
        snapshots = _rows(conn,
            "SELECT snapshot_id, observed_at FROM shadow_paired_snapshots "
            "WHERE run_id = ? ORDER BY observed_at", (run_id,))
        feed_events = _rows(conn,
            "SELECT ts, kind, detail FROM shadow_paired_feed_events WHERE run_id=? ORDER BY ts",
            (run_id,))
        total_orders = int(conn.execute(
            "SELECT COUNT(*) FROM orders WHERE run_id=?", (run_id,)
        ).fetchone()[0])
        total_fills = int(conn.execute(
            """SELECT COUNT(*) FROM fills f JOIN orders o ON o.id=f.order_uuid
               WHERE o.run_id=?""", (run_id,)
        ).fetchone()[0])
        closes = _rows(conn,
            """SELECT id, ts, condition_id, method, shares, cost_basis,
                      realized_pnl, run_id
               FROM closes WHERE run_id = ? ORDER BY ts, id""", (run_id,))
        fills = _rows(conn,
            """SELECT f.trade_id, f.venue_ts, f.recorded_ts, o.condition_id
               FROM fills f JOIN orders o ON o.id=f.order_uuid
               WHERE o.run_id=? ORDER BY f.recorded_ts, f.trade_id""", (run_id,))
        market_marks = _rows(conn,
            """SELECT id, ts, condition_id, event_cluster_id, unrealized_pnl,
                      committed_open_usd, valid, missing_reason
               FROM shadow_paired_market_marks WHERE run_id=? ORDER BY ts, id""",
            (run_id,))
        equity_marks = _rows(conn,
            """SELECT id, ts, equity_usd, realized_pnl, unrealized_pnl,
                      committed_open_usd, valid, missing_conditions_json
               FROM shadow_paired_equity_marks WHERE run_id=? ORDER BY ts, id""",
            (run_id,))
        return {
            "run": run, "admissions": admissions, "orders": orders,
            "missing_order_count": missing_order_count,
            "total_orders": total_orders, "total_fills": total_fills,
            "snapshots": snapshots,
            "feed_events": feed_events, "closes": closes, "fills": fills,
            "market_marks": market_marks, "equity_marks": equity_marks,
        }
    finally:
        conn.close()


def _event_time(raw: object) -> float | None:
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    # Registry fill timestamps are milliseconds; closes and feed rows are seconds.
    return value / 1000.0 if value > 10_000_000_000 else value


def _latest_market_values(
    marks: list[dict], admissions: list[dict], fills: list[dict],
    closes: list[dict], at: float, max_mark_age_sec: float,
) -> tuple[dict[str, dict[str, float]], list[str]]:
    """Return the latest valid mark for markets with run activity by ``at``."""
    by_condition: dict[str, list[dict]] = {}
    for mark in marks:
        if float(mark["ts"]) <= at:
            by_condition.setdefault(str(mark["condition_id"]), []).append(mark)
    latest: dict[str, dict[str, float]] = {}
    missing: list[str] = []
    admitted = {
        str(row["condition_id"])
        for row in admissions
        if float(row["selected_at"]) <= at
    }
    conditions = {
        str(row["condition_id"])
        for row in fills
        if str(row.get("condition_id") or "") in admitted
        and (_event_time(row.get("venue_ts") or row.get("recorded_ts")) or 0.0) <= at
    }
    conditions.update(
        str(row["condition_id"])
        for row in closes
        if str(row.get("condition_id") or "") in admitted
        and float(row["ts"]) <= at
    )
    for cid in conditions:
        choices = by_condition.get(cid, [])
        if not choices:
            missing.append(cid)
            continue
        mark = max(choices, key=lambda item: (float(item["ts"]), int(item["id"])))
        age = at - float(mark["ts"])
        if age > max_mark_age_sec or not int(mark["valid"]):
            missing.append(cid)
            continue
        latest[cid] = {
            "unrealized_pnl": float(mark["unrealized_pnl"]),
            "committed_open_usd": float(mark["committed_open_usd"]),
        }
    return latest, missing


def _equity_curve(
    marks: list[dict], start: float, end: float, bankroll: float,
    max_mark_age_sec: float,
) -> tuple[float | None, list[str]]:
    """Compute drawdown only from a valid, sufficiently fresh equity series."""
    ordered = sorted(marks, key=lambda row: (float(row["ts"]), int(row["id"])))
    before = [row for row in ordered if float(row["ts"]) <= start]
    through_end = [row for row in ordered if float(row["ts"]) <= end]
    issues: list[str] = []
    if not before:
        return None, ["no equity mark at or before the paired window start"]
    if not through_end:
        return None, ["no equity mark at or before the paired window end"]
    baseline = before[-1]
    terminal = through_end[-1]
    if start - float(baseline["ts"]) > max_mark_age_sec:
        issues.append("equity mark at paired window start is stale")
    if end - float(terminal["ts"]) > max_mark_age_sec:
        issues.append("equity mark at paired window end is stale")
    selected = [baseline] + [
        row for row in ordered
        if start < float(row["ts"]) <= end
    ]
    selected.sort(key=lambda row: (float(row["ts"]), int(row["id"])))
    rows = selected
    if any(not int(row["valid"]) for row in rows):
        issues.append("one or more equity marks contain missing position coverage")
    if any(
        float(later["ts"]) - float(earlier["ts"]) > max_mark_age_sec
        for earlier, later in zip(rows, rows[1:])
    ):
        issues.append("equity mark series has a gap larger than the allowed mark age")
    if bankroll <= 0:
        return None, issues + ["starting bankroll is not positive"]
    peak = float(rows[0]["equity_usd"])
    drawdown = 0.0
    for row in rows:
        value = float(row["equity_usd"])
        peak = max(peak, value)
        drawdown = max(drawdown, peak - value)
    return drawdown * 100.0 / bankroll, issues


def _condition_clusters(
    arm: dict, *, start: float, end: float,
    start_market_values: dict[str, dict[str, float]],
) -> tuple[dict[str, str], list[str]]:
    """Map only event families exposed during the analysis window."""
    exposed: set[str] = set()
    for row in arm["admissions"]:
        if start <= float(row["selected_at"]) <= end:
            exposed.add(str(row["condition_id"]))
    for row in arm["orders"]:
        posted = _event_time(row.get("posted_ts"))
        if posted is not None and start <= posted <= end:
            exposed.add(str(row["condition_id"]))
    for row in arm["fills"]:
        filled = _event_time(row.get("venue_ts") or row.get("recorded_ts"))
        if filled is not None and start <= filled <= end:
            exposed.add(str(row["condition_id"]))
    for row in arm["closes"]:
        if start <= float(row["ts"]) <= end:
            exposed.add(str(row["condition_id"]))
    for cid, mark in start_market_values.items():
        if mark["committed_open_usd"] > 1e-8:
            exposed.add(cid)
    for mark in arm["market_marks"]:
        if not int(mark["valid"]):
            exposed.add(str(mark["condition_id"]))

    by_condition: dict[str, set[str]] = {}
    for row in arm["admissions"]:
        cid = str(row["condition_id"])
        if cid not in exposed or float(row["selected_at"]) > end:
            continue
        cluster = str(row.get("event_cluster_id") or "")
        if cluster:
            by_condition.setdefault(cid, set()).add(cluster)
    for row in arm["orders"]:
        cid = str(row["condition_id"])
        cluster = str(row.get("event_cluster_id") or "")
        if cid in exposed and cluster:
            by_condition.setdefault(cid, set()).add(cluster)

    issues: list[str] = []
    mapping: dict[str, str] = {}
    for cid in sorted(exposed):
        clusters = by_condition.get(cid, set())
        if not clusters:
            issues.append(f"event-family attribution missing for {cid}")
        elif len(clusters) != 1:
            issues.append(f"condition {cid} changed event-family identity")
        else:
            mapping[cid] = next(iter(clusters))
    return mapping, issues


def _bootstrap_sum_ci(
    values: list[float], replicates: int, seed: int,
) -> tuple[float, float] | None:
    """Cluster bootstrap CI for total normalized policy uplift (a sum, not mean)."""
    if len(values) < 2 or replicates < 1:
        return None
    rng = random.Random(seed)
    count = len(values)
    totals = [
        sum(values[rng.randrange(count)] for _ in range(count))
        for _ in range(replicates)
    ]
    totals.sort()
    lo = max(0, math.floor(0.025 * (replicates - 1)))
    hi = min(replicates - 1, math.ceil(0.975 * (replicates - 1)))
    return totals[lo], totals[hi]


def _regularized_gamma_p(a: float, x: float) -> float:
    """Regularized lower incomplete gamma P(a, x) by its power series.

    The series converges for x < a + 1. Every call here sits at the chi-square
    25th percentile, where x = χ²/2 < df/2 = a < a + 1 always holds, so no
    continued-fraction branch is needed. Evaluated in log space (`lgamma`):
    the direct Γ(a) factorial form overflows the float range around
    df ≈ 200, and a cluster count that large must not crash the report.
    """
    coef = 1.0 / a
    total = 0.0
    for n in range(300):
        if n > 0:
            coef *= x / (a + n)
        total += coef
    log_prefactor = -x + a * math.log(x) - math.lgamma(a)
    return math.exp(log_prefactor) * total


def _chi2_ppf(degrees_freedom: int, probability: float) -> float:
    """The chi-square inverse CDF, by bisection on the exact series CDF.

    The pilot preregistration's σ selection rule prices estimation uncertainty
    into the power calculation through this quantile, so it is computed
    exactly rather than approximated: the normal (Wilson-Hilferty) shortcut
    is off by ~0.07% at the degrees of freedom this report actually sees,
    which is small but pointless to accept when the exact series is twenty
    lines and needs no dependency.
    """
    if degrees_freedom < 1:
        raise PairedDepthReportError("chi-square degrees of freedom must be positive")
    a = degrees_freedom / 2.0
    lo, hi = 1e-12, float(degrees_freedom) + 1.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if _regularized_gamma_p(a, mid / 2.0) < probability:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def _paired_sigma_upper_bound(sigma: float, cluster_count: int) -> float:
    """The 75% upper confidence bound on a paired sigma estimated from clusters.

    For s² estimated from k observations, (k−1)s²/σ² ~ χ²(k−1), so the upper
    bound is s·sqrt((k−1)/χ²_0.25(k−1)). This is the value the pilot
    preregistration registers when the cluster count falls below the point
    estimate's precision bar: it overstates sigma rather than understating
    it, which can only demand more clusters, never fewer.
    """
    if cluster_count < 2:
        raise PairedDepthReportError(
            "a sigma upper bound needs at least two clusters")
    degrees_freedom = cluster_count - 1
    return sigma * math.sqrt(
        degrees_freedom / _chi2_ppf(degrees_freedom, 0.25))


def analyze_paired_depth(
    *, control_db: Path | str, treatment_db: Path | str,
    control_run_id: str, treatment_run_id: str,
    bootstrap_replicates: int = 20_000,
    max_mark_age_sec: float = 600.0,
    pilot_paired_sigma_usd_per_cluster: float | None = None,
) -> dict:
    """Analyze two finished paired shadows without inventing a power prior."""
    if control_run_id == treatment_run_id:
        raise PairedDepthReportError("control and treatment must use distinct run ids")
    if bootstrap_replicates < 1 or max_mark_age_sec <= 0:
        raise PairedDepthReportError("bootstrap replicates and max mark age must be positive")
    if (pilot_paired_sigma_usd_per_cluster is not None
            and (not math.isfinite(pilot_paired_sigma_usd_per_cluster)
                 or pilot_paired_sigma_usd_per_cluster <= 0)):
        raise PairedDepthReportError("pilot paired sigma must be finite and positive")

    control = _read_arm(control_db, control_run_id)
    treatment = _read_arm(treatment_db, treatment_run_id)
    limitations: list[str] = []
    for label, arm, expected, expected_cutoff in (
        ("control", control, "control", 500.0),
        ("treatment", treatment, "treatment", 250.0),
    ):
        run = arm["run"]
        if run["arm"] != expected:
            limitations.append(f"{label} store run is tagged as {run['arm']!r}")
        if abs(float(run["cutoff_usd"]) - expected_cutoff) > 1e-9:
            limitations.append(f"{label} cutoff is not the registered ${expected_cutoff:g}")
        if run["status"] != "finished" or run["finished_at"] is None:
            limitations.append(f"{label} run is not marked finished")
        if arm["missing_order_count"]:
            limitations.append(
                f"{label} has {arm['missing_order_count']} order(s) without paired attribution")
        if arm["total_orders"] and not arm["total_fills"]:
            limitations.append(f"{label} has orders but no fills; outcomes are unmeasured")
        if any(event["kind"] in {
            "missing_snapshot", "invalid_feed_metadata", "empty_feed",
            "missing_market_tokens", "missing_condition_id", "missing_event_cluster",
            "changed_market_tokens", "market_resolution_failed",
        } for event in arm["feed_events"]):
            limitations.append(f"{label} encountered a paired feed/market coverage gap")

    crun, trun = control["run"], treatment["run"]

    def _finished(run: dict) -> float:
        """End of a run's window, or its start when it never finished.

        A shadow loop that crashes or is killed never reaches
        `record_paired_run_finish`, so its row keeps `finished_at = NULL`.
        Collapsing that window to zero length keeps the report on its
        documented path -- a list of limitations and an inconclusive status --
        instead of a TypeError out of `float(None)`, which the CLI's error
        handler does not catch.
        """
        raw = run["finished_at"]
        return float(raw) if raw is not None else float(run["started_at"])

    starts = [float(crun["started_at"]), float(trun["started_at"])]
    ends = [_finished(crun), _finished(trun)]
    common_start, common_end = max(starts), min(ends)
    elapsed_hours = max(0.0, (common_end - common_start) / 3600.0)
    if common_end <= common_start:
        limitations.append("control and treatment have no positive overlapping run window")
    bankrolls = [float(crun["starting_bankroll_usd"]),
                 float(trun["starting_bankroll_usd"])]
    if max(bankrolls) - min(bankrolls) > 0.01:
        limitations.append("starting bankrolls are not equal within one cent")
    equal_bankroll = sum(bankrolls) / 2.0
    if equal_bankroll <= 0:
        limitations.append("starting bankroll is not positive")
    for label, run in (("control", crun), ("treatment", trun)):
        actual = _finished(run) - float(run["started_at"])
        planned = float(run["planned_minutes"]) * 60.0
        if actual + max_mark_age_sec < planned:
            limitations.append(f"{label} run ended before its planned duration")

    # Each arm records the snapshots IT observed (one row per snapshot, from
    # its per-visit admission writes). Any snapshot seen by exactly one arm
    # means the two loops did not rank on the same feed at the same time --
    # the entire causal claim of the pairing -- so every such id, wherever it
    # fell in either arm's wall clock, is a mismatch. This is deliberately
    # stricter than intersecting on the common window: a snapshot observed
    # only before the second arm started still means the first arm traded a
    # selection the other never saw.
    def snapshot_set(arm: dict) -> set[str]:
        return {str(row["snapshot_id"]) for row in arm["snapshots"]}

    control_snapshots = snapshot_set(control)
    treatment_snapshots = snapshot_set(treatment)
    snapshot_mismatch = sorted(control_snapshots ^ treatment_snapshots)
    if snapshot_mismatch:
        limitations.append("control and treatment did not observe the same feed snapshots")
    if not control_snapshots and not treatment_snapshots:
        limitations.append("no paired feed snapshot was recorded in either arm")

    cstart_values, cmiss_start = _latest_market_values(
        control["market_marks"], control["admissions"], control["fills"],
        control["closes"], common_start, max_mark_age_sec)
    tstart_values, tmiss_start = _latest_market_values(
        treatment["market_marks"], treatment["admissions"], treatment["fills"],
        treatment["closes"], common_start, max_mark_age_sec)
    cend_values, cmiss_end = _latest_market_values(
        control["market_marks"], control["admissions"], control["fills"],
        control["closes"], common_end, max_mark_age_sec)
    tend_values, tmiss_end = _latest_market_values(
        treatment["market_marks"], treatment["admissions"], treatment["fills"],
        treatment["closes"], common_end, max_mark_age_sec)
    for label, missing, phase in (
        ("control", cmiss_start, "window start"),
        ("treatment", tmiss_start, "window start"),
        ("control", cmiss_end, "window end"),
        ("treatment", tmiss_end, "window end"),
    ):
        if missing:
            limitations.append(
                f"{label} market marks missing at {phase}: {', '.join(missing)}")

    cmap, ccluster_issues = _condition_clusters(
        control, start=common_start, end=common_end,
        start_market_values=cstart_values)
    tmap, tcluster_issues = _condition_clusters(
        treatment, start=common_start, end=common_end,
        start_market_values=tstart_values)
    limitations.extend(f"control: {issue}" for issue in ccluster_issues)
    limitations.extend(f"treatment: {issue}" for issue in tcluster_issues)

    def market_values_by_cluster(values: dict[str, dict[str, float]],
                                 mapping: dict[str, str], key: str) -> dict[str, float]:
        out: dict[str, float] = {}
        for cid, row in values.items():
            cluster = mapping.get(cid)
            if cluster:
                out[cluster] = out.get(cluster, 0.0) + float(row[key])
        return out

    def close_maps(arm: dict, condition_clusters: dict[str, str], label: str):
        sums: dict[str, float] = {}
        exit_loss = 0.0
        exit_notional = 0.0
        for close in arm["closes"]:
            ts = float(close["ts"])
            if not common_start <= ts <= common_end:
                continue
            cid = str(close.get("condition_id") or "")
            cluster = condition_clusters.get(cid)
            if not cluster:
                limitations.append(f"{label} close {close['id']} lacks event-family attribution")
                continue
            pnl = close.get("realized_pnl")
            if pnl is None or not math.isfinite(float(pnl)):
                limitations.append(f"{label} close {close['id']} has no finite realized PnL")
                continue
            sums[cluster] = sums.get(cluster, 0.0) + float(pnl)
            if close.get("method") in {"single_buy_exit", "naked_exit"}:
                basis = close.get("cost_basis")
                if basis is None or not math.isfinite(float(basis)) or float(basis) <= 0:
                    limitations.append(
                        f"{label} single-buy exit {close['id']} lacks fill notional")
                else:
                    exit_loss += max(0.0, -float(pnl))
                    exit_notional += float(basis)
        return sums, exit_loss, exit_notional

    cclose, cexit_loss, cexit_notional = close_maps(control, cmap, "control")
    tclose, texit_loss, texit_notional = close_maps(treatment, tmap, "treatment")
    cfloat_start = market_values_by_cluster(cstart_values, cmap, "unrealized_pnl")
    tfloat_start = market_values_by_cluster(tstart_values, tmap, "unrealized_pnl")
    cfloat_end = market_values_by_cluster(cend_values, cmap, "unrealized_pnl")
    tfloat_end = market_values_by_cluster(tend_values, tmap, "unrealized_pnl")
    clusters = sorted(set(cmap.values()) | set(tmap.values()))
    scale = (
        (100.0 / equal_bankroll) * (100.0 / elapsed_hours)
        if elapsed_hours > 0 and equal_bankroll > 0 else 0.0
    )
    c_by_cluster = {
        cluster: (cclose.get(cluster, 0.0) + cfloat_end.get(cluster, 0.0)
                  - cfloat_start.get(cluster, 0.0)) * scale
        for cluster in clusters
    }
    t_by_cluster = {
        cluster: (tclose.get(cluster, 0.0) + tfloat_end.get(cluster, 0.0)
                  - tfloat_start.get(cluster, 0.0)) * scale
        for cluster in clusters
    }
    paired_differences = [t_by_cluster[c] - c_by_cluster[c] for c in clusters]
    uplift = sum(paired_differences) if clusters else None
    interval = _bootstrap_sum_ci(
        paired_differences, bootstrap_replicates,
        seed=sum(map(ord, control_run_id + treatment_run_id)),
    )
    if interval is None:
        limitations.append("fewer than two event-family clusters; paired bootstrap interval unavailable")

    target_delta = 1.0
    required_clusters = None
    pilot_sigma = pilot_paired_sigma_usd_per_cluster
    sigma_upper_bound = None
    if pilot_sigma is None:
        limitations.append(
            "no independent paired pilot variance supplied; sample adequacy is unproven")
    else:
        # The pilot doc's selection rule is computed here, not by hand: with
        # 20-29 clusters the registered sigma must be the conservative bound,
        # and leaving that to manual arithmetic is how a power calculation
        # inherits an optimistic variance.
        if len(paired_differences) >= 2:
            sigma_upper_bound = _paired_sigma_upper_bound(
                pilot_sigma, len(paired_differences))
        required_clusters = max(2, math.ceil(((1.96 + 0.842) * pilot_sigma / target_delta) ** 2))
        if len(clusters) < required_clusters:
            limitations.append(
                f"{len(clusters)} event-family clusters below the pilot-powered target "
                f"of {required_clusters}")

    cdd, cdd_issues = _equity_curve(
        control["equity_marks"], common_start, common_end,
        float(crun["starting_bankroll_usd"]), max_mark_age_sec)
    tdd, tdd_issues = _equity_curve(
        treatment["equity_marks"], common_start, common_end,
        float(trun["starting_bankroll_usd"]), max_mark_age_sec)
    limitations.extend(f"control drawdown: {item}" for item in cdd_issues)
    limitations.extend(f"treatment drawdown: {item}" for item in tdd_issues)

    cexit_rate = cexit_loss / cexit_notional if cexit_notional > 0 else None
    texit_rate = texit_loss / texit_notional if texit_notional > 0 else None
    if cexit_rate is None or texit_rate is None:
        limitations.append(
            "single-buy exit loss per fill notional is unmeasured in one or both arms")
    dd_increase = tdd - cdd if tdd is not None and cdd is not None else None
    checks = {
        "practical_uplift": uplift is not None and uplift >= 1.0,
        "paired_95pct_interval_above_zero": interval is not None and interval[0] > 0.0,
        "drawdown_increase_at_most_1_per_100": dd_increase is not None and dd_increase <= 1.0,
        "single_buy_exit_loss_rate_not_worse": (
            cexit_rate is not None and texit_rate is not None and texit_rate <= cexit_rate
        ),
    }
    measured = not limitations
    decision = "inconclusive"
    if measured and all(checks.values()):
        decision = "adopt_treatment"
    elif measured:
        decision = "retain_control"

    c_raw_pnl = sum(cclose.values()) + sum(cfloat_end.values()) - sum(cfloat_start.values())
    t_raw_pnl = sum(tclose.values()) + sum(tfloat_end.values()) - sum(tfloat_start.values())
    c_terminal_committed = sum(x["committed_open_usd"] for x in cend_values.values())
    t_terminal_committed = sum(x["committed_open_usd"] for x in tend_values.values())
    return {
        "format": "spread_hunter.paired-depth-report.v1",
        "measurement_status": "measured" if measured else "inconclusive",
        "decision": decision,
        "arms": {
            "control": {"run_id": control_run_id, "database": str(control_db),
                        "cutoff_usd": crun["cutoff_usd"],
                        "starting_bankroll_usd": crun["starting_bankroll_usd"],
                        "started_at": crun["started_at"], "finished_at": crun["finished_at"]},
            "treatment": {"run_id": treatment_run_id, "database": str(treatment_db),
                          "cutoff_usd": trun["cutoff_usd"],
                          "starting_bankroll_usd": trun["starting_bankroll_usd"],
                          "started_at": trun["started_at"], "finished_at": trun["finished_at"]},
        },
        "window": {"start": common_start, "end": common_end,
                   "elapsed_hours": elapsed_hours,
                   "max_mark_age_sec": max_mark_age_sec},
        "coverage": {
            "control_orders": control["total_orders"],
            "treatment_orders": treatment["total_orders"],
            "control_fills": control["total_fills"],
            "treatment_fills": treatment["total_fills"],
            "control_snapshots": sorted(control_snapshots),
            "treatment_snapshots": sorted(treatment_snapshots),
            "common_snapshots": sorted(control_snapshots & treatment_snapshots),
            "unmatched_snapshots": snapshot_mismatch,
            "snapshot_ids_match": not snapshot_mismatch,
            "control_orders_missing_attribution": control["missing_order_count"],
            "treatment_orders_missing_attribution": treatment["missing_order_count"],
            "control_missing_positions_at_start": cmiss_start,
            "treatment_missing_positions_at_start": tmiss_start,
            "control_missing_positions_at_end": cmiss_end,
            "treatment_missing_positions_at_end": tmiss_end,
            "control_feed_events": control["feed_events"],
            "treatment_feed_events": treatment["feed_events"],
        },
        "clusters": {
            "count": len(clusters),
            "ids": clusters,
            "paired_differences": paired_differences,
            "control_contribution_per_cluster": c_by_cluster,
            "treatment_contribution_per_cluster": t_by_cluster,
            "paired_standard_deviation": (
                statistics.stdev(paired_differences) if len(paired_differences) >= 2 else None
            ),
        },
        "power_analysis": {
            "status": "prior_supplied" if pilot_sigma is not None else "prior_missing",
            "pilot_paired_sigma_usd_per_cluster": pilot_sigma,
            # The 75% upper bound for a sigma estimated from THIS report's
            # cluster count. On a pilot report this is the number the pilot
            # preregistration registers when k falls in [20, 30); on the
            # final report it is a reference only -- the registered value is
            # the frozen scalar above.
            "paired_sigma_75pct_upper_bound": sigma_upper_bound,
            "target_uplift_usd_per_100_bankroll_per_100h": target_delta,
            "target_power": 0.80,
            "two_sided_alpha": 0.05,
            "estimated_clusters_required": required_clusters,
            "observed_clusters": len(clusters),
        },
        "metrics": {
            "control_net_pnl_usd_in_common_window": c_raw_pnl,
            "treatment_net_pnl_usd_in_common_window": t_raw_pnl,
            "uplift_usd_per_100_bankroll_per_100h": uplift,
            "paired_95pct_cluster_bootstrap_interval": list(interval) if interval else None,
            "control_max_drawdown_usd_per_100_bankroll": cdd,
            "treatment_max_drawdown_usd_per_100_bankroll": tdd,
            "drawdown_increase_usd_per_100_bankroll": dd_increase,
            "control_terminal_committed_open_usd": c_terminal_committed,
            "treatment_terminal_committed_open_usd": t_terminal_committed,
        },
        "single_buy_exit_loss_rate": {
            "control": cexit_rate, "treatment": texit_rate,
            "control_loss_usd": cexit_loss, "treatment_loss_usd": texit_loss,
            "control_fill_notional_usd": cexit_notional,
            "treatment_fill_notional_usd": texit_notional,
        },
        "registered_checks": checks,
        "limitations": sorted(set(limitations)),
        "interpretation": (
            "An inconclusive report is not evidence for either depth cutoff. "
            "A verdict requires complete attribution/mark coverage, independent "
            "paired variance for power, and all registered risk guards."
        ),
    }


ADMISSION_HORIZON_SEC = {"h0": 300.0, "h1": 3600.0, "h2": 21600.0,
                         "h3": 900.0}
ADMISSION_MIN_CLUSTERS = 30
ADMISSION_PARITY_FLOOR = -0.01


def _admission_markouts(conn: sqlite3.Connection, run_id: str) -> list[dict]:
    return _rows(conn,
        """SELECT m.ts, m.condition_id, m.token_id, m.fill_price, m.size,
                  m.refs_json
           FROM markouts m WHERE m.run_id = ?
           ORDER BY m.ts, m.id""", (run_id,))


def _admission_fills(conn: sqlite3.Connection, run_id: str) -> list[dict]:
    return _rows(conn,
        """SELECT f.trade_id, f.price, f.size, f.venue_ts, f.recorded_ts,
                  o.condition_id, o.token_id
           FROM fills f JOIN orders o ON o.id = f.order_uuid
           WHERE o.run_id = ? ORDER BY f.recorded_ts, f.trade_id""",
        (run_id,))


def _admission_fill_notional(conn: sqlite3.Connection, run_id: str) -> float:
    row = conn.execute(
        """SELECT SUM(f.price * f.size) FROM fills f
           JOIN orders o ON o.id = f.order_uuid WHERE o.run_id = ?""",
        (run_id,),
    ).fetchone()
    return float(row[0] or 0.0)


def _refs_excess(refs_json: object, horizon: str) -> float | None:
    """Excess markout for one markout row at one horizon, or None.

    Never substitutes zero or the raw number: a missing ref or peer is a
    missing observation, and the verdict treats it as one.
    """
    try:
        refs = json.loads(refs_json) if isinstance(refs_json, str) else {}
    except (TypeError, ValueError):
        return None
    if not isinstance(refs, dict):
        return None
    cell = refs.get(horizon)
    if not isinstance(cell, dict):
        return None
    try:
        ref = float(cell.get("ref"))
        peer = float(cell.get("peer"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(ref) or not math.isfinite(peer):
        return None
    from core_brain.markout import excess_markout
    return excess_markout(ref, peer)


def _match_markout(markouts: list[dict], *, condition_id: str,
                   token_id: str, price: float, size: float,
                   fill_ts: float) -> dict | None:
    """The markout sample for one fill: same condition, token, price, size.

    Several fills of one condition share those keys, so the closest sample
    time wins. Deterministic; no invented observations.
    """
    best = None
    best_gap = None
    for row in markouts:
        if str(row.get("condition_id") or "") != condition_id:
            continue
        if str(row.get("token_id") or "") != token_id:
            continue
        try:
            row_price = float(row.get("fill_price"))
            row_size = float(row.get("size"))
        except (TypeError, ValueError):
            continue
        # Same store round-trips identical floats, but fills and markouts are
        # written by different writers: compare with tolerance, never ==.
        if not (math.isclose(row_price, price, rel_tol=1e-9, abs_tol=1e-12)
                and math.isclose(row_size, size, rel_tol=1e-9, abs_tol=1e-12)):
            continue
        try:
            gap = abs(float(row.get("ts") or 0.0) - fill_ts)
        except (TypeError, ValueError):
            continue
        if best is None or gap < best_gap:
            best, best_gap = row, gap
    return best


def _bootstrap_mean_ci(
    values: list[float], replicates: int, seed: int,
) -> tuple[float, float] | None:
    """Percentile CI of a mean under cluster resampling."""
    if len(values) < 1 or replicates < 1:
        return None
    if len(values) == 1:
        return values[0], values[0]
    rng = random.Random(seed)
    count = len(values)
    means = [
        sum(values[rng.randrange(count)] for _ in range(count)) / count
        for _ in range(replicates)
    ]
    means.sort()
    lo = max(0, math.floor(0.025 * (replicates - 1)))
    hi = min(replicates - 1, math.ceil(0.975 * (replicates - 1)))
    return means[lo], means[hi]


def analyze_paired_admission(
    *, control_db: Path | str, treatment_db: Path | str,
    control_run_id: str, treatment_run_id: str,
    bootstrap_replicates: int = 20_000,
    max_mark_age_sec: float = 600.0,
) -> dict:
    """Adopt / reject / inconclusive for the D12 admission trial.

    Read-only: both stores open through `_read_only`. The verdict reads only
    the four pre-registered h3 bars; every horizon is still reported for
    inspection. No PnL uplift, no variance prior anywhere in this verdict.
    """
    if control_run_id == treatment_run_id:
        raise PairedDepthReportError("control and treatment must use distinct run ids")
    if bootstrap_replicates < 1 or max_mark_age_sec <= 0:
        raise PairedDepthReportError("bootstrap replicates and max mark age must be positive")

    control_conn = _read_only(control_db)
    treatment_conn = _read_only(treatment_db)
    try:
        control = _read_arm(control_db, control_run_id)
        treatment = _read_arm(treatment_db, treatment_run_id)
        c_markouts = _admission_markouts(control_conn, control_run_id)
        t_markouts = _admission_markouts(treatment_conn, treatment_run_id)
        c_fills = _admission_fills(control_conn, control_run_id)
        t_fills = _admission_fills(treatment_conn, treatment_run_id)
        c_notional = _admission_fill_notional(control_conn, control_run_id)
        t_notional = _admission_fill_notional(treatment_conn, treatment_run_id)
    finally:
        control_conn.close()
        treatment_conn.close()

    limitations: list[str] = []
    for label, arm in (("control", control), ("treatment", treatment)):
        if str(arm["run"].get("trial_axis") or "depth") != "admission":
            limitations.append(f"{label} run is not an admission-axis run")
        if arm["run"]["arm"] != label:
            limitations.append(f"{label} store run is tagged as {arm['run']['arm']!r}")
        if arm["run"]["status"] != "finished" or arm["run"]["finished_at"] is None:
            limitations.append(f"{label} run is not marked finished")
        if arm["missing_order_count"]:
            limitations.append(
                f"{label} has {arm['missing_order_count']} order(s) without paired attribution")
        if arm["total_orders"] and not arm["total_fills"]:
            limitations.append(f"{label} has orders but no fills; outcomes are unmeasured")
        if any(event["kind"] in {
            "missing_snapshot", "invalid_feed_metadata", "empty_feed",
            "missing_market_tokens", "missing_condition_id", "missing_event_cluster",
            "changed_market_tokens", "market_resolution_failed",
        } for event in arm["feed_events"]):
            limitations.append(f"{label} encountered a paired feed/market coverage gap")

    crun, trun = control["run"], treatment["run"]
    starts = [float(crun["started_at"]), float(trun["started_at"])]
    ends = [float(crun["finished_at"]) if crun["finished_at"] is not None else float(crun["started_at"]),
            float(trun["finished_at"]) if trun["finished_at"] is not None else float(trun["started_at"])]
    common_start, common_end = max(starts), min(ends)
    if common_end <= common_start:
        limitations.append("control and treatment have no positive overlapping run window")
    bankrolls = [float(crun["starting_bankroll_usd"]),
                 float(trun["starting_bankroll_usd"])]
    if max(bankrolls) - min(bankrolls) > 0.01:
        limitations.append("starting bankrolls are not equal within one cent")
    equal_bankroll = sum(bankrolls) / 2.0
    if equal_bankroll <= 0:
        limitations.append("starting bankroll is not positive")
    for label, run in (("control", crun), ("treatment", trun)):
        actual = (float(run["finished_at"]) - float(run["started_at"])
                  if run["finished_at"] is not None else 0.0)
        planned = float(run["planned_minutes"]) * 60.0
        if actual + max_mark_age_sec < planned:
            limitations.append(f"{label} run ended before its planned duration")

    def snapshot_set(arm: dict) -> set[str]:
        return {str(row["snapshot_id"]) for row in arm["snapshots"]}

    control_snapshots = snapshot_set(control)
    treatment_snapshots = snapshot_set(treatment)
    snapshot_mismatch = sorted(control_snapshots ^ treatment_snapshots)
    if snapshot_mismatch:
        limitations.append("control and treatment did not observe the same feed snapshots")
    if not control_snapshots and not treatment_snapshots:
        limitations.append("no paired feed snapshot was recorded in either arm")

    cmap, ccluster_issues = _condition_clusters(
        control, start=common_start, end=common_end, start_market_values={})
    tmap, tcluster_issues = _condition_clusters(
        treatment, start=common_start, end=common_end, start_market_values={})
    limitations.extend(f"control: {issue}" for issue in ccluster_issues)
    limitations.extend(f"treatment: {issue}" for issue in tcluster_issues)

    def arm_horizons(fills: list[dict], markouts: list[dict],
                     mapping: dict[str, str]) -> dict:
        """Per-fill excess markouts joined to event clusters, per horizon."""
        by_horizon: dict[str, dict[str, list[float]]] = {
            h: {} for h in ADMISSION_HORIZON_SEC}
        due_missing: dict[str, list[str]] = {h: [] for h in ADMISSION_HORIZON_SEC}
        due_count = 0
        not_due_count = 0
        for fill in fills:
            cid = str(fill.get("condition_id") or "")
            cluster = mapping.get(cid)
            if not cluster:
                continue
            fill_ts = _event_time(fill.get("venue_ts") or fill.get("recorded_ts")) or 0.0
            try:
                price = float(fill.get("price"))
                size = float(fill.get("size"))
            except (TypeError, ValueError):
                continue
            if size <= 0:
                continue
            sample = _match_markout(
                markouts, condition_id=cid,
                token_id=str(fill.get("token_id") or ""),
                price=price, size=size, fill_ts=fill_ts)
            for horizon, window in ADMISSION_HORIZON_SEC.items():
                due = fill_ts + window <= common_end
                if not due:
                    if horizon == "h3":
                        not_due_count += 1
                    continue
                if horizon == "h3":
                    due_count += 1
                value = _refs_excess(sample.get("refs_json") if sample else None,
                                     horizon) if sample else None
                if value is None:
                    if horizon == "h3":
                        due_missing["h3"].append(str(fill.get("trade_id") or cid))
                    continue
                by_horizon[horizon].setdefault(cluster, []).append((value, size))
        return {"by_horizon": by_horizon, "due_missing": due_missing,
                "due_count": due_count, "not_due_count": not_due_count}

    carm = arm_horizons(c_fills, c_markouts, cmap)
    tarm = arm_horizons(t_fills, t_markouts, tmap)

    def cluster_means(per_cluster: dict[str, list[tuple[float, float]]]) -> dict[str, float]:
        return {cluster: sum(v * s for v, s in pairs) / sum(s for _, s in pairs)
                for cluster, pairs in per_cluster.items() if pairs}

    horizons: dict[str, dict] = {}
    seed_base = sum(map(ord, control_run_id + treatment_run_id))
    for horizon in ADMISSION_HORIZON_SEC:
        cmeans = cluster_means(carm["by_horizon"][horizon])
        tmeans = cluster_means(tarm["by_horizon"][horizon])
        horizons[horizon] = {
            "control": {
                "mean": (sum(cmeans.values()) / len(cmeans)) if cmeans else None,
                "interval": _bootstrap_mean_ci(sorted(cmeans.values()),
                                               bootstrap_replicates, seed_base),
                "clusters": len(cmeans),
            },
            "treatment": {
                "mean": (sum(tmeans.values()) / len(tmeans)) if tmeans else None,
                "interval": _bootstrap_mean_ci(sorted(tmeans.values()),
                                               bootstrap_replicates, seed_base + 1),
                "clusters": len(tmeans),
            },
        }

    h3c = cluster_means(carm["by_horizon"]["h3"])
    h3t = cluster_means(tarm["by_horizon"]["h3"])
    for label, means in (("control", h3c), ("treatment", h3t)):
        if len(means) < ADMISSION_MIN_CLUSTERS:
            limitations.append(
                f"{label} has {len(means)} event clusters with a usable h3 mark; "
                f"floor is {ADMISSION_MIN_CLUSTERS}")
    for label, missing in (("control", carm["due_missing"]["h3"]),
                           ("treatment", tarm["due_missing"]["h3"])):
        if missing:
            limitations.append(
                f"{label} has {len(missing)} due fill(s) without an h3 excess markout")

    # Joint union bootstrap for the treatment-minus-control h3 difference.
    union = sorted(set(h3c) | set(h3t))
    diff = ((sum(h3t.values()) / len(h3t)) - (sum(h3c.values()) / len(h3c))
            if h3c and h3t else None)
    interval = None
    if union and h3c and h3t:
        rng = random.Random(seed_base)
        replicates = []
        for _ in range(bootstrap_replicates):
            drawn = [union[rng.randrange(len(union))] for _ in range(len(union))]
            tc = [h3t[c] for c in drawn if c in h3t]
            cc = [h3c[c] for c in drawn if c in h3c]
            if tc and cc:
                replicates.append(sum(tc) / len(tc) - sum(cc) / len(cc))
        if replicates:
            replicates.sort()
            lo = max(0, math.floor(0.025 * (len(replicates) - 1)))
            hi = min(len(replicates) - 1, math.ceil(0.975 * (len(replicates) - 1)))
            interval = (replicates[lo], replicates[hi])
    if diff is not None and interval is None:
        limitations.append("paired h3 interval unavailable: no usable joint resample")

    cdd, cdd_issues = _equity_curve(
        control["equity_marks"], common_start, common_end,
        float(crun["starting_bankroll_usd"]), max_mark_age_sec)
    tdd, tdd_issues = _equity_curve(
        treatment["equity_marks"], common_start, common_end,
        float(trun["starting_bankroll_usd"]), max_mark_age_sec)
    for label, issues in (("control", cdd_issues), ("treatment", tdd_issues)):
        limitations.extend(f"{label} drawdown: {item}" for item in issues)
    dd_increase = tdd - cdd if tdd is not None and cdd is not None else None
    if dd_increase is None:
        limitations.append("drawdown is unmeasured in one or both arms")

    def exit_loss_rate(arm: dict, label: str, notional: float) -> float | None:
        loss = 0.0
        for close in arm["closes"]:
            if not common_start <= float(close["ts"]) <= common_end:
                continue
            if close.get("method") in {"single_buy_exit", "naked_exit"}:
                pnl = close.get("realized_pnl")
                if pnl is None or not math.isfinite(float(pnl)):
                    limitations.append(
                        f"{label} close {close['id']} has no finite realized PnL")
                    continue
                loss += max(0.0, -float(pnl))
        if notional <= 0:
            limitations.append(f"{label} has zero fill notional")
            return None
        return loss / notional

    cexit_rate = exit_loss_rate(control, "control", c_notional)
    texit_rate = exit_loss_rate(treatment, "treatment", t_notional)

    bars = {
        "markout_parity": diff is not None and diff >= ADMISSION_PARITY_FLOOR,
        "interval_above_floor": interval is not None and interval[0] > ADMISSION_PARITY_FLOOR,
        "drawdown_bounded": dd_increase is not None and dd_increase <= 1.0,
        "exit_loss_rate": (cexit_rate is not None and texit_rate is not None
                           and texit_rate <= cexit_rate),
    }
    measured = not limitations
    decision = "inconclusive"
    if measured and all(bars.values()):
        decision = "adopt"
    elif measured:
        decision = "reject"
    return {
        "format": "spread_hunter.paired-admission-report.v1",
        "measurement_status": "measured" if measured else "inconclusive",
        "decision": decision,
        "trial_axis": "admission",
        "arms": {
            "control": {"run_id": control_run_id, "database": str(control_db),
                        "starting_bankroll_usd": crun["starting_bankroll_usd"],
                        "started_at": crun["started_at"],
                        "finished_at": crun["finished_at"]},
            "treatment": {"run_id": treatment_run_id, "database": str(treatment_db),
                          "starting_bankroll_usd": trun["starting_bankroll_usd"],
                          "started_at": trun["started_at"],
                          "finished_at": trun["finished_at"]},
        },
        "window": {"start": common_start, "end": common_end,
                   "max_mark_age_sec": max_mark_age_sec},
        "coverage": {
            "control_clusters_usable_h3": len(h3c),
            "treatment_clusters_usable_h3": len(h3t),
            "control_due_fills": carm["due_count"],
            "treatment_due_fills": tarm["due_count"],
            "control_fills_not_yet_due": carm["not_due_count"],
            "treatment_fills_not_yet_due": tarm["not_due_count"],
            "control_due_fills_missing_h3": carm["due_missing"]["h3"],
            "treatment_due_fills_missing_h3": tarm["due_missing"]["h3"],
            "control_snapshots": sorted(control_snapshots),
            "treatment_snapshots": sorted(treatment_snapshots),
            "unmatched_snapshots": snapshot_mismatch,
            "snapshot_ids_match": not snapshot_mismatch,
        },
        "horizons": horizons,
        "h3_difference": {
            "treatment_minus_control": diff,
            "paired_95pct_interval": list(interval) if interval else None,
        },
        "drawdown": {
            "control_per_100_bankroll": cdd,
            "treatment_per_100_bankroll": tdd,
            "increase_per_100_bankroll": dd_increase,
        },
        "single_buy_exit_loss_rate": {
            "control": cexit_rate, "treatment": texit_rate,
            "control_fill_notional_usd": c_notional,
            "treatment_fill_notional_usd": t_notional,
        },
        "registered_bars": bars,
        "limitations": sorted(set(limitations)),
        "interpretation": (
            "An inconclusive report is not evidence for admission. A verdict "
            "reads only the four pre-registered h3 bars; PnL uplift never "
            "enters it, and inconclusive never justifies changing the gate."
        ),
    }


def _detect_trial_axis(control_db: Path | str, treatment_db: Path | str,
                       control_run_id: str, treatment_run_id: str) -> str:
    """Read trial_axis from both runs; admission only on unanimous verdict."""
    axes = set()
    for path, run_id in ((control_db, control_run_id),
                         (treatment_db, treatment_run_id)):
        conn = _read_only(path)
        try:
            rows = _rows(conn, "SELECT trial_axis FROM shadow_paired_runs WHERE run_id = ?",
                         (run_id,))
        except sqlite3.Error as exc:
            # A store from before the axis existed has no trial_axis column:
            # that schema signal means depth. Any other read failure is
            # corruption, and guessing an axis would analyze it wrong.
            if "no such column" not in str(exc).lower():
                raise PairedDepthReportError(
                    f"cannot determine trial axis: {exc}") from exc
            rows = [{"trial_axis": "depth"}]
        finally:
            conn.close()
        axes.add(str((rows[0].get("trial_axis") if rows else "depth") or "depth"))
    return "admission" if axes == {"admission"} else "depth"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only paired-depth shadow analyzer; does not start a run.")
    parser.add_argument("--control-db", required=True)
    parser.add_argument("--treatment-db", required=True)
    parser.add_argument("--control-run-id", required=True)
    parser.add_argument("--treatment-run-id", required=True)
    parser.add_argument("--bootstrap-replicates", type=int, default=20_000)
    parser.add_argument("--max-mark-age-sec", type=float, default=600.0)
    parser.add_argument(
        "--pilot-paired-sigma-usd-per-cluster", type=float, default=None,
        help="independent paired-pilot SD in normalized cluster uplift units; omitted means power is unproven")
    parser.add_argument("--output", default=None,
                        help="optional JSON report path; input stores remain read-only")
    parser.add_argument("--trial-axis", choices=("auto", "depth", "admission"),
                        default="auto",
                        help="which verdict to compute; auto reads trial_axis from both runs")
    args = parser.parse_args(argv)
    axis = args.trial_axis
    if axis == "auto":
        axis = _detect_trial_axis(args.control_db, args.treatment_db,
                                  args.control_run_id, args.treatment_run_id)
    if axis == "admission":
        report = analyze_paired_admission(
            control_db=args.control_db, treatment_db=args.treatment_db,
            control_run_id=args.control_run_id,
            treatment_run_id=args.treatment_run_id,
            bootstrap_replicates=args.bootstrap_replicates,
            max_mark_age_sec=args.max_mark_age_sec,
        )
    else:
        report = analyze_paired_depth(
            control_db=args.control_db, treatment_db=args.treatment_db,
            control_run_id=args.control_run_id,
            treatment_run_id=args.treatment_run_id,
            bootstrap_replicates=args.bootstrap_replicates,
            max_mark_age_sec=args.max_mark_age_sec,
            pilot_paired_sigma_usd_per_cluster=args.pilot_paired_sigma_usd_per_cluster,
        )
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text + "\n", encoding="utf-8")
        print(f"paired-depth report written: {target}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PairedDepthReportError as exc:
        print(f"paired-depth report unavailable: {exc}", file=sys.stderr)
        sys.exit(2)
