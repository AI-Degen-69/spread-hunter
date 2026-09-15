from __future__ import annotations

import datetime as _datetime
import re
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
    }
