"""Statistical validation harness: live pipeline execution with zero financial risk.

`python -m statistical_validation_run --target-closes 50 --max-hours 12.0`

Runs the live strategy against live CLOB books via a read-only client proxy into
an isolated SQLite database, stopping when either the target number of closes is
reached or the maximum time limit expires.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import logging
import os
import pathlib
import shutil
import sqlite3
import sys
import time
from dataclasses import replace as dc_replace
from pathlib import Path
from typing import Any, Callable, Optional

from core_brain.config import MakerConfig, load as load_cfg
from core_brain.runtime_paths import resolve_runtime_file
from core_brain.shadow_guard import assert_not_production_registry
from core_brain.shadow_run import (
    _Deadline,
    _default_fetch_books,
    _default_markets_fn,
    run_shadow,
    shadow_run_id,
    ShadowResult,
)
from core_brain.venue import MAX_ORDER_USD, MAX_TOTAL_USD
from statistical_validation_run.artifacts import (
    build_gate_rows,
    resolve_verdict,
    write_artifacts,
)

log = logging.getLogger("statistical_validation_run")


def count_closes(db_path: Path | str, run_id: Optional[str] = None) -> int:
    """Return count of closed trades in the given database, filtered by run_id if provided.

    Returns 0 if the database file does not exist, the closes table is missing,
    the run_id column is missing when run_id is supplied, or the query encounters an error.
    """
    path = Path(db_path)
    if not path.is_file():
        return 0
    try:
        con = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    except sqlite3.Error:
        try:
            con = sqlite3.connect(str(path))
        except sqlite3.Error:
            return 0

    try:
        with con:
            cur = con.cursor()
            table_check = cur.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='closes'"
            ).fetchone()
            if not table_check:
                return 0
            if run_id:
                cols = {row[1] for row in cur.execute("PRAGMA table_info(closes)").fetchall()}
                if "run_id" not in cols:
                    return 0
                row = cur.execute(
                    "SELECT COUNT(*) FROM closes WHERE run_id = ?", (run_id,)
                ).fetchone()
            else:
                row = cur.execute("SELECT COUNT(*) FROM closes").fetchone()
            return int(row[0]) if row and row[0] is not None else 0
    except (sqlite3.Error, OSError):
        return 0
    finally:
        try:
            con.close()
        except Exception:
            pass


def count_matured_markouts(db_path: Path | str, run_id: Optional[str] = None) -> int:
    """Return count of clean, matured markout observations in the given database.

    A markout is matured if ref_mid is not null, ref_mid_source != 'contaminated',
    and at least one horizon mid (mid_h0, mid_h1, mid_h2, or mid_h3) has been measured.
    """
    path = Path(db_path)
    if not path.is_file():
        return 0
    try:
        con = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    except sqlite3.Error:
        try:
            con = sqlite3.connect(str(path))
        except sqlite3.Error:
            return 0

    try:
        with con:
            cur = con.cursor()
            table_check = cur.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='markouts'"
            ).fetchone()
            if not table_check:
                return 0
            cols = {row[1] for row in cur.execute("PRAGMA table_info(markouts)").fetchall()}
            if run_id and "run_id" not in cols:
                return 0

            horizon_cols = [f"mid_h{i}" for i in range(4) if f"mid_h{i}" in cols]
            if not horizon_cols:
                return 0
            horizon_clause = " OR ".join(f"{col} IS NOT NULL" for col in horizon_cols)

            query = f"""
                SELECT COUNT(*) FROM markouts
                WHERE ref_mid IS NOT NULL
                  AND (ref_mid_source IS NULL OR ref_mid_source != 'contaminated')
                  AND ({horizon_clause})
            """
            params: list[Any] = []
            if run_id:
                query += " AND run_id = ?"
                params.append(run_id)

            row = cur.execute(query, params).fetchone()
            return int(row[0]) if row and row[0] is not None else 0
    except (sqlite3.Error, OSError):
        return 0
    finally:
        try:
            con.close()
        except Exception:
            pass


def make_hybrid_deadline_sleep(
    deadline_ts: float,
    count_closes_fn: Callable[[], int],
    target_closes: Optional[int] = None,
    count_markouts_fn: Optional[Callable[[], int]] = None,
    min_markouts: Optional[int] = None,
    start_ts: Optional[float] = None,
    min_seconds: float = 0.0,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> Callable[[float], None]:
    """A sleep_fn for trader_loop.run that stops on target events or wall clock deadline.

    Raises `_Deadline` when:
    1. The minimum duration has elapsed (elapsed >= min_seconds), target closes is reached
       (if configured), AND minimum matured markouts is reached (if configured).
    2. OR the clock reaches or exceeds deadline_ts.
    """
    _start = start_ts if start_ts is not None else clock()

    def sleep_fn(seconds: float) -> None:
        now = clock()
        elapsed = now - _start

        closes_met = True
        if target_closes is not None and target_closes > 0:
            closes_met = (count_closes_fn() >= target_closes)

        markouts_met = True
        if min_markouts is not None and min_markouts > 0:
            if count_markouts_fn is not None:
                markouts_met = (count_markouts_fn() >= min_markouts)
            else:
                markouts_met = False

        duration_met = (elapsed >= min_seconds)

        if duration_met and closes_met and markouts_met and (target_closes is not None or min_markouts is not None):
            raise _Deadline()

        remaining = deadline_ts - now
        if remaining <= 0:
            raise _Deadline()
        sleep(max(0.0, min(seconds, remaining)))

    return sleep_fn


def snapshot_stat_validation_env(
    artifact_dir: Path,
    cfg: MakerConfig,
    *,
    root: Optional[Path] = None,
) -> None:
    """Create artifact directory and snapshot pipeline inputs and effective config.

    File names carry the `_snapshot` suffix (Issue #54): the bundle a run emits
    is `config_snapshot.json`, `pipeline_snapshot.json` and
    `markets_snapshot.json`, so a reader can tell a run's frozen inputs from
    the live files still being written under `runtime/`.
    """
    artifact_dir.mkdir(parents=True, exist_ok=True)

    for fname, snap_name in (
        ("pipeline.json", "pipeline_snapshot.json"),
        ("markets.json", "markets_snapshot.json"),
    ):
        src = resolve_runtime_file(fname, root=root)
        if src.is_file():
            try:
                shutil.copy2(src, artifact_dir / snap_name)
            except OSError as e:
                log.warning("snapshot: failed to copy %s: %s", fname, e)
        else:
            log.warning("snapshot: source file %s missing, skipping", fname)

    cfg_path = artifact_dir / "config_snapshot.json"
    try:
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(dataclasses.asdict(cfg), f, indent=2)
    except (OSError, TypeError) as e:
        log.warning("snapshot: failed to write config_snapshot.json: %s", e)

    log.info(
        "STAT HARNESS config: max_order_usd=%.2f max_total_usd=%.2f objective=%s bankroll_usd=%.2f",
        MAX_ORDER_USD,
        MAX_TOTAL_USD,
        getattr(cfg, "objective", "balanced"),
        getattr(cfg, "bankroll_usd", 0.0),
    )


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Statistical Validation Harness: run live pipeline with no-money ReadOnlyVenue."
    )
    ap.add_argument(
        "--target-closes",
        type=int,
        default=None,
        help="target number of completed closes before stopping "
             "(default: stat_gate_target_closes from config, currently 60)",
    )
    ap.add_argument(
        "--min-hours",
        type=float,
        default=0.0,
        help="minimum execution time in hours before event stop can trigger (default: 0.0)",
    )
    ap.add_argument(
        "--max-hours",
        type=float,
        default=1.0,
        help="maximum execution time in hours (default: 1.0)",
    )
    ap.add_argument(
        "--min-markouts",
        type=int,
        default=25,
        help="minimum matured markout samples required for fleet posture (default: 25)",
    )
    ap.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="rotation cadence in seconds (default: 5.0)",
    )
    ap.add_argument(
        "--db",
        type=str,
        default=None,
        help="isolated shadow database path (default: data/shadow_stat_<ts>_<run_id>.db)",
    )
    ap.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="explicit run id shared with the launcher's observer/watcher "
             "(default: mint a fresh shadow-<hex> id)",
    )
    ap.add_argument(
        "--report",
        type=str,
        default=None,
        help="artifact directory (default: reports/stat_<ts>_<run_id>/)",
    )
    ap.add_argument(
        "--max-markets",
        type=int,
        default=None,
        help="cap the number of markets rotated (default: all)",
    )
    ap.add_argument(
        "--funder",
        default=None,
        help="funder address for live balance read (default: POLY_FUNDER)",
    )
    return ap.parse_args(argv)


def main(
    argv: Optional[list[str]] = None,
    *,
    markets_fn: Optional[Callable[..., list]] = None,
    client_fn: Optional[Callable[[], Any]] = None,
    decide_fn: Optional[Callable] = None,
    fetch_books: Optional[Callable] = None,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
    root: Optional[Path] = None,
) -> int:
    """The statistical validation harness entrypoint."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stderr)],
    )

    a = _parse_args(argv)

    # The sample-size gate needs a close count to bite on. Default to the
    # config target (60): a rehearsal that never collects enough closes must
    # run to its --max-hours ceiling and be reported underpowered, never
    # "PASS with adequate sample" at n=16. --target-closes 0 opts out.
    cfg = dc_replace(load_cfg(), single_buy_grace_sec=0.0)
    if a.target_closes is None:
        a.target_closes = cfg.stat_gate_target_closes

    run_id = a.run_id or shadow_run_id()
    ts_str = datetime.datetime.now().strftime("%d-%m_%H-%M")
    db_path = Path(a.db) if a.db else Path(f"data/shadow_stat_{ts_str}_{run_id}.db")

    # Fail fast if production registry is passed
    assert_not_production_registry(db_path)

    log.warning(
        "SHADOW RUN starting: mode=stat_validation target_closes=%s min_hours=%s max_hours=%s min_markouts=%s interval=%ss store=%s run_id=%s "
        "-- NO SIGNER LOADED: this process cannot place, cancel or merge anything. Numbers below are rehearsal, not results.",
        a.target_closes,
        a.min_hours,
        a.max_hours,
        a.min_markouts,
        a.interval,
        db_path,
        run_id,
    )

    artifact_dir = (
        Path(a.report)
        if a.report
        else Path(f"reports/stat_{ts_str}_{run_id}")
    )

    maker = a.funder or os.environ.get("POLY_FUNDER")
    if maker:
        try:
            from core_brain.account import fetch_live_balance
            live_bal = fetch_live_balance(maker)
            if live_bal is not None and live_bal > 0:
                cfg = dc_replace(cfg, bankroll_usd=live_bal)
        except Exception as e:  # noqa: BLE001 - degrade, do not stop
            log.warning("live balance read failed, using config bankroll: %s", e)

    snapshot_stat_validation_env(artifact_dir, cfg, root=root)

    start_now = clock()
    deadline_ts = start_now + max(0.0, float(a.max_hours) * 3600.0)
    min_seconds = max(0.0, float(a.min_hours) * 3600.0)

    hybrid_sleep = make_hybrid_deadline_sleep(
        deadline_ts=deadline_ts,
        count_closes_fn=lambda: count_closes(db_path, run_id),
        target_closes=a.target_closes,
        count_markouts_fn=lambda: count_matured_markouts(db_path, run_id),
        min_markouts=a.min_markouts,
        start_ts=start_now,
        min_seconds=min_seconds,
        clock=clock,
        sleep=sleep,
    )

    # Validate the feed before entering the long-running shadow loop. The
    # launcher seeds this file automatically; direct CLI users get a concise
    # remediation instead of a nested trader traceback.
    if markets_fn is None:
        from core_brain.market_feed import MarketFeedError, load_graduated_markets
        try:
            load_graduated_markets()
        except MarketFeedError as exc:
            raise RuntimeError(
                "graduated markets feed is unavailable; run "
                "scripts/rank_markets.py first (or use the overnight launcher)"
            ) from exc

    result: ShadowResult = run_shadow(
        minutes=float(a.max_hours) * 60.0,
        db_path=db_path,
        run_id=run_id,
        sleep_fn=hybrid_sleep,
        interval=a.interval,
        funder=a.funder,
        cfg=cfg,
        markets_fn=(
            markets_fn
            or (lambda max_markets=None: _default_markets_fn()(a.max_markets))
        ),
        client_fn=client_fn,
        decide_fn=decide_fn,
        fetch_books=fetch_books or _default_fetch_books(),
    )

    quoted = sum(1 for r in result.results if getattr(r, "status", None) == "QUOTED")
    declined = sum(1 for r in result.results if getattr(r, "status", None) == "DECLINED")
    errors = sum(1 for r in result.results if getattr(r, "status", None) == "ERROR")
    log.warning(
        "SHADOW RUN finished: rotations_returned=%d quoted=%d declined=%d "
        "errors=%d intents_recorded=%d skipped=%s",
        len(result.results),
        quoted,
        declined,
        errors,
        len(result.intents),
        ",".join(result.skipped_stages),
    )

    total_closes = count_closes(db_path, run_id)
    matured_markouts = count_matured_markouts(db_path, run_id)

    is_underpowered = False
    underpowered_reasons = []
    if a.target_closes is not None and total_closes < a.target_closes:
        is_underpowered = True
        underpowered_reasons.append(f"closes {total_closes} < {a.target_closes}")
    if a.min_markouts is not None and matured_markouts < a.min_markouts:
        is_underpowered = True
        underpowered_reasons.append(f"markouts {matured_markouts} < {a.min_markouts}")

    if is_underpowered:
        verdict = "INCONCLUSIVE"
        verdict_reason = f"underpowered: n < N_min or markouts < {a.min_markouts} ({', '.join(underpowered_reasons)})"
        log.warning(
            "VERDICT: %s (%s) -- sample is insufficient to validate spread capture.",
            verdict,
            verdict_reason,
        )
    else:
        verdict = "POWERED"
        verdict_reason = f"sample satisfied: closes={total_closes}, markouts={matured_markouts}"
        log.info("VERDICT: %s (%s)", verdict, verdict_reason)

    run_result = {
        "run_id": run_id,
        "db_path": str(db_path),
        "total_closes": total_closes,
        "matured_markouts": matured_markouts,
        "target_closes": a.target_closes,
        "min_markouts": a.min_markouts,
        "min_hours": a.min_hours,
        "max_hours": a.max_hours,
        "status": verdict,
        "reason": verdict_reason,
    }
    result_path = artifact_dir / "run_result.json"
    try:
        result_path.write_text(json.dumps(run_result, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning("failed to write run_result.json: %s", e)

    # ── Issue #54: the full KPI artifact bundle ────────────────────────────
    # The harness must prove the gate inclusive of ALL losses, so the report
    # is generated from the shadow store through the same kpi.report path the
    # live dashboard reads, scoped to this run. The verdict is INCONCLUSIVE
    # whenever the sample is underpowered; GO/NO-GO only with adequate sample.
    artifacts_failed = False
    try:
        from core_brain.kpi import report as kpi_report
        kpi = kpi_report(db_path, run_id=run_id)
        from core_brain.order_registry import OrderRegistry
        reg = OrderRegistry(db_path)
        run_closes = [
            c for c in reg.get_all_closes() if c.get("run_id") == run_id
        ]
        gate_rows = build_gate_rows(
            closes=run_closes,
            kpi=kpi,
            cfg=cfg,
            target_closes=a.target_closes,
            matured_markouts=matured_markouts,
            min_markouts=a.min_markouts,
        )
        stat = resolve_verdict(
            gate_rows=gate_rows,
            kpi=kpi,
            underpowered=is_underpowered,
            underpowered_reasons=underpowered_reasons,
            threshold_pct=cfg.stat_gate_threshold_pct,
        )
        write_artifacts(
            artifact_dir,
            db_path=db_path,
            run_id=run_id,
            cfg=cfg,
            kpi=kpi,
            verdict=stat["verdict"],
            verdict_reason=stat["verdict_reason"],
            gate_rows=gate_rows,
            run_result=run_result,
            target_closes=a.target_closes,
            min_markouts=a.min_markouts,
        )
        log.warning(
            "STAT VERDICT: %s (%s) -- memo at %s/report.md",
            stat["verdict"],
            stat["verdict_reason"],
            artifact_dir,
        )
    except Exception as e:  # noqa: BLE001 - the run itself already finished
        artifacts_failed = True
        log.warning(
            "artifact emission failed (run finished, results remain in %s): %s",
            db_path, e,
        )

    # The overnight launcher keys off the exit code; a run whose artifact
    # bundle failed to emit must not look successful to the harness.
    if artifacts_failed:
        return 2
    return 0 if result.results else 1
