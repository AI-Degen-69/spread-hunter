from __future__ import annotations

import datetime as _datetime
import math
import re
import sqlite3
import statistics
from pathlib import Path
from typing import Any

from core_brain.config import MakerConfig, load as load_cfg
from core_brain.kpi import report as kpi_report
from core_brain.order_registry import OrderRegistry
from core_brain.runtime_paths import LIVE_ROOT
from statistical_validation_run.artifacts import (
    build_gate_rows,
    build_sensitivity,
    render_report_md,
    resolve_verdict,
)

_VALID_MODES = {"shadow", "live"}


# Where a report goes when the caller does not say. Anchored to the repo, not
# to the cwd: `Path("reports")` resolved against whatever directory the process
# happened to start in, so the report only landed correctly because every menu
# launch passes `-WorkingDirectory $ProjectPath`. Started from a scheduled
# task, from `scripts/`, or from a test harness, the run's only human-readable
# artifact went into a `reports/` folder nobody looks in -- the same bug as the
# statistics store that spilled into the repo root, by the same mechanism: a
# writer deciding its destination from ambient state.
DEFAULT_REPORT_DIR = LIVE_ROOT / "reports"


def _queue_multiple(queue_ahead: float | None, order_size: float | None) -> float | None:
    """Queue depth relative to order size, owned by this report module.

    Same contract as the rehearsal fill model's queue-multiple helper, deliberately
    duplicated rather than imported: the live/shadow import boundary
    (`test_no_live_module_imports_the_shadow_model` in `tests/test_shadow_run.py`)
    forbids any live-side module -- and this reporter serves live reports too --
    from importing rehearsal fill logic, whose inferred-fill semantics must never
    meet the live path. (The name of that module is spelled out nowhere in this
    file on purpose: the boundary guard scans source text.) A multiple of N means
    the tape must trade N times the order size at the exact order price before
    first fill.
    """
    if queue_ahead is None or order_size is None:
        return None
    try:
        queue = float(queue_ahead)
        size = float(order_size)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(queue) or not math.isfinite(size):
        return None
    if queue < 0.0 or size <= 0.0:
        return None
    return queue / size


SINGLE_CYCLE_LINE = (
    "Only one decision cycle observed; resting orders received no settlement pass "
    "after posting, so zero fills is expected and is not evidence of a fill-model defect."
)


def _queue_depth_section(registry: OrderRegistry, run_id: str) -> tuple[str, dict[str, Any]]:
    """Queue-multiple stats plus the single-cycle warning for one shadow run.

    `queue_ahead` and `size` come off the quote row; when the quote carries no
    size, fall back to the order's `original_size` joined by `local_id`
    (`orders.id`). `cycle_intent` is filtered by run -- shadow stores are
    per-run, but a shared store must not mix another run's cycles in.
    Rendered unconditionally: a zero-fill run has no closes, so gating this on
    the sample-size gate would hide exactly the signal that explains it.
    """
    quotes = [row for row in registry.get_all_quotes() if row.get("run_id") == run_id]
    orders = [row for row in registry.get_all_orders() if row.get("run_id") == run_id]
    size_by_id = {
        str(o.get("id")): o.get("original_size")
        for o in orders if o.get("id") is not None
    }
    measured: list[float] = []
    for q in quotes:
        size = q.get("size")
        if size is None:
            local = q.get("local_id")
            size = size_by_id.get(str(local)) if local is not None else None
        multiple = _queue_multiple(q.get("queue_ahead"), size)
        if multiple is not None:
            measured.append(multiple)
    try:
        with registry._conn() as conn:
            cycle_rows = conn.execute(
                "SELECT DISTINCT cycle FROM cycle_intent WHERE run_id = ?",
                (run_id,),
            ).fetchall()
        n_cycles: int | None = len(cycle_rows)
    except sqlite3.Error:
        # Telemetry read, not report logic: an unreadable cycle table omits
        # the warning rather than breaking the report. Narrowly sqlite3 so
        # programming errors surface instead of reading as missing cycles.
        n_cycles = None
    median = statistics.median(measured) if measured else None
    maximum = max(measured) if measured else None
    lines = [
        "## Queue depth (shadow)",
        "",
        f"- **Measured quotes**: `{len(measured)}` (unmeasured: `{len(quotes) - len(measured)}`)",
        f"- **Median queue multiple**: `{median:.1f}x`" if median is not None else "- **Median queue multiple**: `n/a`",
        f"- **Max queue multiple**: `{maximum:.1f}x`" if maximum is not None else "- **Max queue multiple**: `n/a`",
    ]
    if n_cycles == 1:
        lines += ["", SINGLE_CYCLE_LINE]
    stats = {
        "measured_quotes": len(measured),
        "unmeasured_quotes": len(quotes) - len(measured),
        "median_queue_multiple": median,
        "max_queue_multiple": maximum,
        "distinct_cycles": n_cycles,
    }
    return "\n".join(lines) + "\n", stats


def _disclaimer(mode: str) -> str:
    if mode == "shadow":
        return "Rehearsal, not results. This shadow report is observational and no money was spent."
    return "Observational, read-only, live caveats: this report reads live data without trading."


def write_statistics_report(
    db_path: Path | str,
    run_id: str,
    mode: str,
    out_dir: Path | str | None = None,
) -> dict[str, Any]:
    if mode not in _VALID_MODES:
        raise ValueError(f"mode must be one of: {', '.join(sorted(_VALID_MODES))}")

    db = Path(db_path)
    cfg: MakerConfig = load_cfg()
    registry = OrderRegistry(db)
    closes = [row for row in registry.get_all_closes() if row.get("run_id") == run_id]
    fills = [row for row in registry.get_all_fills() if row.get("run_id") == run_id]
    quotes = [row for row in registry.get_all_quotes() if row.get("run_id") == run_id]
    kpi = kpi_report(db, run_id=run_id)
    gate_rows = build_gate_rows(
        closes=closes,
        kpi=kpi,
        cfg=cfg,
        # The sample-size gate must bite even for ad-hoc reports: shadow-01
        # showed a profitable 38-close run still failing it, and a report
        # that says "reported" instead of "FAIL" would hide that.
        target_closes=cfg.stat_gate_target_closes,
        matured_markouts=None,
        min_markouts=None,
    )
    # A thin sample must control the verdict, not just the gate row: a
    # profitable 38-close run below target is INCONCLUSIVE, never GO.
    n_closes = len(closes)
    underpowered = n_closes < cfg.stat_gate_target_closes
    stat = resolve_verdict(
        gate_rows=gate_rows,
        kpi=kpi,
        underpowered=underpowered,
        underpowered_reasons=(
            [f"closes {n_closes} < {cfg.stat_gate_target_closes}"]
            if underpowered else []
        ),
        threshold_pct=cfg.stat_gate_threshold_pct,
    )

    timestamp = _datetime.datetime.now().strftime("%d-%m_%H-%M")
    destination = Path(out_dir) if out_dir is not None else DEFAULT_REPORT_DIR
    destination.mkdir(parents=True, exist_ok=True)
    safe_run_id = re.sub(r"[^A-Za-z0-9.-]+", "_", run_id).strip("._") or "run"
    report_path = destination / f"{timestamp}_{mode}_{safe_run_id}_statistics_report.md"
    text = render_report_md(
        run_id=run_id,
        db_path=db,
        artifact_dir=destination,
        kpi=kpi,
        cfg=cfg,
        gate_rows=gate_rows,
        verdict=stat["verdict"],
        verdict_reason=stat["verdict_reason"],
        run_result={"status": stat["verdict"], "reason": stat["verdict_reason"]},
        closes=closes,
        target_closes=cfg.stat_gate_target_closes,
        min_markouts=None,
        sensitivity=build_sensitivity(closes, threshold_pct=cfg.stat_gate_threshold_pct),
    )
    text = text.replace(
        "> Rehearsal numbers, not results. The shadow store has no signer; no "
        "money was spent and no position was opened.",
        f"> {_disclaimer(mode)}",
    )
    text = text.replace("Rehearsal, not results", "rehearsal, not results")
    text = text.replace("Observational, read-only, live caveats", "observational, read-only, live caveats")
    queue_stats: dict[str, Any] = {}
    if mode == "shadow":
        queue_section, queue_stats = _queue_depth_section(registry, run_id)
        text = text.rstrip("\n") + "\n\n" + queue_section
    report_path.write_text(text, encoding="utf-8")
    return {
        "db_path": str(db),
        "run_id": run_id,
        "mode": mode,
        "report_path": str(report_path),
        "verdict": stat["verdict"],
        "gate_rows": gate_rows,
        "fills": len(fills),
        "quotes": len(quotes),
        **queue_stats,
    }
