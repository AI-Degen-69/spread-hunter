"""Paired-depth experiment metadata and marks for isolated shadow stores.

This module writes only experiment-specific tables inside the caller's shadow
SQLite database. It deliberately does not extend or modify the live registry
schema or production orders database.
"""
from __future__ import annotations

import json
import math
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Callable

from core_brain.order_registry import get_connection
from core_brain.market_lifecycle import resolved_condition_ids


class PairedShadowError(RuntimeError):
    """Paired shadow setup or measurement could not be completed safely."""


def ensure_paired_shadow_tables(db_path: Path | str) -> None:
    """Create experiment-local tables in a shadow database."""
    with closing(get_connection(Path(db_path))) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS shadow_paired_runs (
                run_id TEXT PRIMARY KEY,
                arm TEXT NOT NULL CHECK (arm IN ('control', 'treatment')),
                cutoff_usd REAL,
                trial_axis TEXT NOT NULL DEFAULT 'depth',
                starting_bankroll_usd REAL NOT NULL,
                started_at REAL NOT NULL,
                planned_minutes REAL NOT NULL,
                finished_at REAL,
                status TEXT NOT NULL DEFAULT 'running'
            );
            CREATE TABLE IF NOT EXISTS shadow_paired_snapshots (
                run_id TEXT NOT NULL,
                snapshot_id TEXT NOT NULL,
                observed_at REAL NOT NULL,
                PRIMARY KEY (run_id, snapshot_id)
            );
            CREATE TABLE IF NOT EXISTS shadow_paired_admissions (
                run_id TEXT NOT NULL,
                condition_id TEXT NOT NULL,
                snapshot_id TEXT NOT NULL,
                arm TEXT NOT NULL,
                cutoff_usd REAL,
                event_cluster_id TEXT NOT NULL DEFAULT '',
                event_cluster_source TEXT NOT NULL DEFAULT 'missing',
                event_title TEXT NOT NULL DEFAULT '',
                market_slug TEXT NOT NULL DEFAULT '',
                family_label TEXT NOT NULL DEFAULT '',
                admission_role TEXT NOT NULL DEFAULT '',
                selected_at REAL NOT NULL,
                PRIMARY KEY (run_id, condition_id, snapshot_id)
            );
            CREATE INDEX IF NOT EXISTS idx_shadow_paired_admissions_condition
                ON shadow_paired_admissions (run_id, condition_id, selected_at);
            CREATE TABLE IF NOT EXISTS shadow_paired_market_tokens (
                run_id TEXT NOT NULL,
                condition_id TEXT NOT NULL,
                up_token_id TEXT NOT NULL,
                down_token_id TEXT NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (run_id, condition_id)
            );
            CREATE TABLE IF NOT EXISTS shadow_paired_orders (
                local_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                condition_id TEXT NOT NULL,
                pair_id TEXT,
                arm TEXT NOT NULL,
                cutoff_usd REAL,
                snapshot_id TEXT NOT NULL,
                event_cluster_id TEXT NOT NULL DEFAULT '',
                event_cluster_source TEXT NOT NULL DEFAULT 'missing',
                event_title TEXT NOT NULL DEFAULT '',
                market_slug TEXT NOT NULL DEFAULT '',
                family_label TEXT NOT NULL DEFAULT '',
                admission_role TEXT NOT NULL DEFAULT '',
                admitted_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS shadow_paired_feed_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                ts REAL NOT NULL,
                kind TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS shadow_paired_market_marks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                ts REAL NOT NULL,
                condition_id TEXT NOT NULL,
                event_cluster_id TEXT NOT NULL DEFAULT '',
                unrealized_pnl REAL NOT NULL,
                committed_open_usd REAL NOT NULL,
                valid INTEGER NOT NULL,
                missing_reason TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_shadow_paired_market_marks_latest
                ON shadow_paired_market_marks (run_id, condition_id, ts);
            CREATE TABLE IF NOT EXISTS shadow_paired_equity_marks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                ts REAL NOT NULL,
                equity_usd REAL NOT NULL,
                realized_pnl REAL NOT NULL,
                unrealized_pnl REAL NOT NULL,
                committed_open_usd REAL NOT NULL,
                valid INTEGER NOT NULL,
                missing_conditions_json TEXT NOT NULL DEFAULT '[]'
            );
            CREATE INDEX IF NOT EXISTS idx_shadow_paired_equity_run_ts
                ON shadow_paired_equity_marks (run_id, ts);
            CREATE INDEX IF NOT EXISTS idx_shadow_paired_orders_cluster
                ON shadow_paired_orders (run_id, event_cluster_id);
            """
        )
        conn.commit()
        # Additive migration for stores created before the admission axis:
        # new shadow-only columns, never a rebuild. cutoff_usd nullability is
        # intentionally NOT migrated -- old stores keep NOT NULL and the
        # admission axis (cutoff NULL) runs on new stores only.
        for table, column, ddl in (
            ("shadow_paired_runs", "trial_axis",
             "TEXT NOT NULL DEFAULT 'depth'"),
            ("shadow_paired_admissions", "family_label",
             "TEXT NOT NULL DEFAULT ''"),
            ("shadow_paired_admissions", "admission_role",
             "TEXT NOT NULL DEFAULT ''"),
            ("shadow_paired_orders", "family_label",
             "TEXT NOT NULL DEFAULT ''"),
            ("shadow_paired_orders", "admission_role",
             "TEXT NOT NULL DEFAULT ''"),
        ):
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
        conn.commit()


def record_paired_run_start(
    db_path: Path | str, *, run_id: str, arm: str,
    cutoff_usd: float | None,
    starting_bankroll_usd: float, started_at: float, planned_minutes: float,
    trial_axis: str = "depth",
) -> None:
    """Start a paired run only in an otherwise empty, dedicated shadow store."""
    if arm not in {"control", "treatment"}:
        raise PairedShadowError(f"unknown paired-depth arm {arm!r}")
    if trial_axis not in {"depth", "admission"}:
        raise PairedShadowError(f"unknown trial axis {trial_axis!r}")
    if trial_axis == "admission":
        if cutoff_usd is not None:
            raise PairedShadowError("admission runs carry no dollar cutoff")
        finite = (starting_bankroll_usd, started_at, planned_minutes)
    else:
        finite = (cutoff_usd, starting_bankroll_usd, started_at,
                  planned_minutes)
    if not all(math.isfinite(float(v)) for v in finite):
        raise PairedShadowError("paired run values must be finite")
    if starting_bankroll_usd <= 0 or planned_minutes <= 0:
        raise PairedShadowError("bankroll and duration must be positive")
    if trial_axis == "depth" and not (cutoff_usd is not None and cutoff_usd > 0):
        raise PairedShadowError("bankroll, duration, and cutoff must be positive")
    with closing(get_connection(Path(db_path))) as conn:
        if trial_axis == "admission":
            # Old stores keep `cutoff_usd REAL NOT NULL` by design (no
            # rebuild); an admission row would die there with a raw
            # IntegrityError. Refuse up front, in our own error type.
            info = conn.execute("PRAGMA table_info(shadow_paired_runs)").fetchall()
            notnull = next((bool(r["notnull"]) for r in info
                            if r["name"] == "cutoff_usd"), False)
            if notnull:
                raise PairedShadowError(
                    "admission runs need a store created by the current build; "
                    "this store predates nullable cutoffs")
    with closing(get_connection(Path(db_path))) as conn:
        existing = conn.execute(
            "SELECT 1 FROM shadow_paired_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        rows = conn.execute(
            "SELECT (SELECT COUNT(*) FROM orders) + "
            "(SELECT COUNT(*) FROM fills) + "
            "(SELECT COUNT(*) FROM quotes) + "
            "(SELECT COUNT(*) FROM closes) AS n"
        ).fetchone()
        if existing or int(rows["n"] or 0):
            raise PairedShadowError(
                "paired-depth runs require a fresh, dedicated shadow store and run id"
            )
        conn.execute(
            """INSERT INTO shadow_paired_runs
               (run_id, arm, cutoff_usd, trial_axis, starting_bankroll_usd,
                started_at, planned_minutes, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'running')""",
            (run_id, arm, None if cutoff_usd is None else float(cutoff_usd),
             trial_axis, float(starting_bankroll_usd),
             float(started_at), float(planned_minutes)),
        )
        conn.execute(
            """INSERT INTO shadow_paired_equity_marks
               (run_id, ts, equity_usd, realized_pnl, unrealized_pnl,
                committed_open_usd, valid, missing_conditions_json)
               VALUES (?, ?, ?, 0.0, 0.0, 0.0, 1, '[]')""",
            (run_id, float(started_at), float(starting_bankroll_usd)),
        )
        conn.commit()


def record_paired_snapshot(
    db_path: Path | str, *, run_id: str, snapshot_id: str,
    observed_at: float | None = None,
) -> None:
    if not snapshot_id:
        raise PairedShadowError("paired market visit has no snapshot id")
    with closing(get_connection(Path(db_path))) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO shadow_paired_snapshots "
            "(run_id, snapshot_id, observed_at) VALUES (?, ?, ?)",
            (run_id, snapshot_id, time.time() if observed_at is None else float(observed_at)),
        )
        conn.commit()


def record_paired_market_admission(
    db_path: Path | str, *, run_id: str, condition_id: str,
    snapshot_id: str, arm: str, cutoff_usd: float | None,
    event_cluster_id: str, event_cluster_source: str,
    event_title: str = "", market_slug: str = "",
    family_label: str = "", admission_role: str = "",
    selected_at: float | None = None,
) -> None:
    if not condition_id or not snapshot_id:
        raise PairedShadowError("paired admission requires condition and snapshot ids")
    with closing(get_connection(Path(db_path))) as conn:
        conn.execute(
            """INSERT OR IGNORE INTO shadow_paired_admissions
               (run_id, condition_id, snapshot_id, arm, cutoff_usd,
                event_cluster_id, event_cluster_source, event_title, market_slug,
                family_label, admission_role, selected_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, condition_id, snapshot_id, arm,
             None if cutoff_usd is None else float(cutoff_usd),
             event_cluster_id or "", event_cluster_source or "missing",
             event_title or "", market_slug or "",
             family_label or "", admission_role or "",
             time.time() if selected_at is None else float(selected_at)),
        )
        conn.commit()


def record_paired_feed_event(
    db_path: Path | str, *, run_id: str, kind: str, detail: str,
    ts: float | None = None,
) -> None:
    with closing(get_connection(Path(db_path))) as conn:
        conn.execute(
            "INSERT INTO shadow_paired_feed_events (run_id, ts, kind, detail) "
            "VALUES (?, ?, ?, ?)",
            (run_id, time.time() if ts is None else float(ts), kind, detail[:500]),
        )
        conn.commit()


def _cluster_from_row(row: dict) -> tuple[str, str]:
    event_id = str(row.get("event_id") or "").strip()
    event_slug = str(row.get("event_slug") or "").strip()
    if event_id:
        return f"gamma-event:{event_id}", "gamma_event_id"
    if event_slug:
        return f"gamma-event-slug:{event_slug}", "gamma_event_slug"
    return "", "missing"


def _open_exposure_on_cluster(conn, *, run_id: str, event_cluster_id: str,
                               exclude_condition_id: str) -> str:
    """A held condition in the same event, or "" when the cluster is free.

    Exposure is read from the registry, not from attribution-row counts: a
    two-leg quote writes two attribution rows before either leg fills, and a
    cancelled order leaves its row behind, so row counts both over- and
    under-read. Open means a resting or filled-but-unclosed order for another
    condition of the event; any close for the condition retires it (exit,
    merge or settlement holds nothing). The candidate's own condition never
    blocks itself. Missing registry tables mean nothing trackable: free.
    """
    if not _table_exists(conn, "orders") or not _table_exists(conn, "closes"):
        return ""
    row = conn.execute(
        """SELECT DISTINCT o.condition_id FROM orders o
           WHERE o.run_id = ?
           AND o.status IN ('open', 'partial', 'pending', 'filled')
           AND o.condition_id != ?
           AND o.condition_id IN (
               SELECT condition_id FROM shadow_paired_orders
               WHERE run_id = ? AND event_cluster_id = ?)
           AND NOT EXISTS (
               SELECT 1 FROM closes c
               WHERE c.run_id = o.run_id AND c.condition_id = o.condition_id)
           LIMIT 1""",
        (run_id, exclude_condition_id, run_id, event_cluster_id),
    ).fetchone()
    return str(row["condition_id"]) if row else ""


def _table_exists(conn, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,)).fetchone() is not None


def record_paired_market_selection(
    db_path: Path | str, *, run_id: str, arm: str, cutoff_usd: float | None,
    spec: dict, observed_at: float | None = None,
) -> None:
    """Persist selected feed metadata before resolving the market over the network."""
    if str(spec.get("trial_axis") or "") == "admission":
        _record_admission_selection(
            db_path, run_id=run_id, arm=arm, spec=spec,
            observed_at=observed_at)
        return
    snapshot_id = str(spec.get("paired_depth_snapshot_id") or "")
    cid = str(spec.get("cid") or spec.get("condition_id") or "")
    row_arm = str(spec.get("paired_depth_arm") or "")
    try:
        row_cutoff = float(spec.get("paired_depth_cutoff_usd"))
    except (TypeError, ValueError):
        row_cutoff = math.nan
    if row_arm != arm or not math.isfinite(row_cutoff) or row_cutoff != float(cutoff_usd):
        detail = f"market={cid or '?'} arm={row_arm or '?'} cutoff={row_cutoff}"
        record_paired_feed_event(
            db_path, run_id=run_id, kind="invalid_feed_metadata",
            detail=detail, ts=observed_at)
        raise PairedShadowError(f"paired feed metadata mismatch: {detail}")
    if not snapshot_id:
        record_paired_feed_event(
            db_path, run_id=run_id, kind="missing_snapshot",
            detail=f"market={cid or '?'}", ts=observed_at)
        raise PairedShadowError(f"paired feed has no snapshot for {cid or 'market'}")
    if not cid:
        record_paired_feed_event(
            db_path, run_id=run_id, kind="missing_condition_id",
            detail="paired market row has no condition id", ts=observed_at)
        raise PairedShadowError("paired market row has no condition id")
    now = time.time() if observed_at is None else float(observed_at)
    cluster_id, source = _cluster_from_row(spec)
    if not cluster_id:
        record_paired_feed_event(
            db_path, run_id=run_id, kind="missing_event_cluster",
            detail=f"market={cid}", ts=now)
        raise PairedShadowError(
            f"paired market {cid} has no stable Gamma event id or slug")
    record_paired_snapshot(db_path, run_id=run_id, snapshot_id=snapshot_id,
                           observed_at=now)
    record_paired_market_admission(
        db_path, run_id=run_id, condition_id=cid, snapshot_id=snapshot_id,
        arm=arm, cutoff_usd=cutoff_usd, event_cluster_id=cluster_id,
        event_cluster_source=source, event_title=str(spec.get("event_title") or ""),
        market_slug=str(spec.get("slug") or ""), selected_at=now,
    )


def _record_admission_selection(
    db_path: Path | str, *, run_id: str, arm: str,
    spec: dict, observed_at: float | None = None,
) -> None:
    """Admission-axis selection: no dollar cutoff, family attribution, guard.

    Depth callers never reach here (their specs carry no `trial_axis`), so
    the depth contract is untouched.
    """
    snapshot_id = str(spec.get("snapshot_id") or "")
    cid = str(spec.get("cid") or spec.get("condition_id") or "")
    row_arm = str(spec.get("trial_arm") or spec.get("arm") or "")
    if row_arm != arm:
        detail = f"market={cid or '?'} arm={row_arm or '?'}"
        record_paired_feed_event(
            db_path, run_id=run_id, kind="invalid_feed_metadata",
            detail=detail, ts=observed_at)
        raise PairedShadowError(f"paired feed metadata mismatch: {detail}")
    if not snapshot_id:
        record_paired_feed_event(
            db_path, run_id=run_id, kind="missing_snapshot",
            detail=f"market={cid or '?'}", ts=observed_at)
        raise PairedShadowError(f"paired feed has no snapshot for {cid or 'market'}")
    if not cid:
        record_paired_feed_event(
            db_path, run_id=run_id, kind="missing_condition_id",
            detail="paired market row has no condition id", ts=observed_at)
        raise PairedShadowError("paired market row has no condition id")
    now = time.time() if observed_at is None else float(observed_at)
    cluster_id = str(spec.get("event_cluster_id") or "")
    source = "bundle"
    if not cluster_id:
        cluster_id, source = _cluster_from_row(spec)
    if not cluster_id:
        record_paired_feed_event(
            db_path, run_id=run_id, kind="missing_event_cluster",
            detail=f"market={cid}", ts=now)
        raise PairedShadowError(
            f"paired market {cid} has no stable Gamma event id or slug")
    role = str(spec.get("admission_role") or "")
    if arm == "treatment" and role == "fallback":
        with closing(get_connection(Path(db_path))) as conn:
            held = _open_exposure_on_cluster(
                conn, run_id=run_id, event_cluster_id=cluster_id,
                exclude_condition_id=cid)
        if held:
            detail = (f"market={cid} held={held} cluster={cluster_id}: "
                      f"one fallback holding per event")
            record_paired_feed_event(
                db_path, run_id=run_id, kind="fallback_guard_skip",
                detail=detail, ts=now)
            raise PairedShadowError(f"admission fallback guard: {detail}")
    record_paired_snapshot(db_path, run_id=run_id, snapshot_id=snapshot_id,
                           observed_at=now)
    record_paired_market_admission(
        db_path, run_id=run_id, condition_id=cid, snapshot_id=snapshot_id,
        arm=arm, cutoff_usd=None, event_cluster_id=cluster_id,
        event_cluster_source=source, event_title=str(spec.get("event_title") or ""),
        market_slug=str(spec.get("slug") or ""),
        family_label=str(spec.get("family") or ""),
        admission_role=role, selected_at=now,
    )


def record_paired_market_tokens(
    db_path: Path | str, *, run_id: str, condition_id: str,
    up_token_id: str, down_token_id: str, observed_at: float | None = None,
) -> None:
    """Persist the resolved UP/DOWN token map; reject any identity change."""
    now = time.time() if observed_at is None else float(observed_at)
    if not condition_id or not up_token_id or not down_token_id or up_token_id == down_token_id:
        record_paired_feed_event(
            db_path, run_id=run_id, kind="missing_market_tokens",
            detail=f"market={condition_id or '?'}", ts=now)
        raise PairedShadowError(
            f"paired market {condition_id or '?'} has invalid UP/DOWN token mapping")
    with closing(get_connection(Path(db_path))) as conn:
        existing = conn.execute(
            """SELECT up_token_id, down_token_id FROM shadow_paired_market_tokens
               WHERE run_id = ? AND condition_id = ?""",
            (run_id, condition_id),
        ).fetchone()
        changed_tokens = bool(
            existing and (
                str(existing["up_token_id"]) != str(up_token_id)
                or str(existing["down_token_id"]) != str(down_token_id)
            )
        )
    if changed_tokens:
        record_paired_feed_event(
            db_path, run_id=run_id, kind="changed_market_tokens",
            detail=f"market={condition_id}", ts=now)
        raise PairedShadowError(
            f"paired market {condition_id} changed its UP/DOWN token mapping")
    with closing(get_connection(Path(db_path))) as conn:
        conn.execute(
            """INSERT INTO shadow_paired_market_tokens
               (run_id, condition_id, up_token_id, down_token_id, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(run_id, condition_id) DO UPDATE SET
                   up_token_id=excluded.up_token_id,
                   down_token_id=excluded.down_token_id,
                   updated_at=excluded.updated_at""",
            (run_id, condition_id, str(up_token_id), str(down_token_id), now),
        )
        conn.commit()


def record_paired_market_visit(
    db_path: Path | str, *, run_id: str, arm: str, cutoff_usd: float,
    spec: dict, up_token_id: str, down_token_id: str,
    observed_at: float | None = None,
) -> None:
    """Durably record the exact feed snapshot, family, and outcome token map."""
    record_paired_market_selection(
        db_path, run_id=run_id, arm=arm, cutoff_usd=cutoff_usd,
        spec=spec, observed_at=observed_at)
    cid = str(spec.get("cid") or spec.get("condition_id") or "")
    record_paired_market_tokens(
        db_path, run_id=run_id, condition_id=cid,
        up_token_id=up_token_id, down_token_id=down_token_id,
        observed_at=observed_at)


def _latest_paired_admission(conn, *, run_id: str, condition_id: str):
    row = conn.execute(
        """SELECT arm, cutoff_usd, snapshot_id, event_cluster_id,
                  event_cluster_source, event_title, market_slug,
                  family_label, admission_role, selected_at
           FROM shadow_paired_admissions
           WHERE run_id = ? AND condition_id = ?
           ORDER BY selected_at DESC, rowid DESC LIMIT 1""",
        (run_id, condition_id),
    ).fetchone()
    if row is None:
        raise PairedShadowError(
            f"no paired admission for {condition_id}; refusing unattributed order"
        )
    if not str(row["snapshot_id"] or ""):
        raise PairedShadowError(
            f"no feed snapshot for {condition_id}; refusing paired order"
        )
    if not str(row["event_cluster_id"] or ""):
        raise PairedShadowError(
            f"no stable event-family id for {condition_id}; refusing order"
        )
    return row


def validate_paired_market_admission(
    db_path: Path | str, *, run_id: str, condition_id: str,
) -> None:
    """Refuse a paired order before it creates any registry rows."""
    with closing(get_connection(Path(db_path))) as conn:
        _latest_paired_admission(conn, run_id=run_id, condition_id=condition_id)


def record_paired_order_attribution(
    db_path: Path | str, *, run_id: str, local_id: str,
    condition_id: str, pair_id: str | None,
) -> None:
    """Copy the latest explicit market admission onto one simulated order."""
    with closing(get_connection(Path(db_path))) as conn:
        row = _latest_paired_admission(conn, run_id=run_id,
                                       condition_id=condition_id)
        conn.execute(
            """INSERT INTO shadow_paired_orders
               (local_id, run_id, condition_id, pair_id, arm, cutoff_usd,
                snapshot_id, event_cluster_id, event_cluster_source, event_title,
                market_slug, family_label, admission_role, admitted_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (local_id, run_id, condition_id, pair_id, row["arm"], row["cutoff_usd"],
             row["snapshot_id"], row["event_cluster_id"],
             row["event_cluster_source"], row["event_title"], row["market_slug"],
             row["family_label"] or "", row["admission_role"] or "",
             row["selected_at"]),
        )
        conn.commit()


def remove_paired_order_attribution(
    db_path: Path | str, *, run_id: str, local_id: str,
) -> None:
    """Compensate a paired side-table write if its registry order cannot be created."""
    with closing(get_connection(Path(db_path))) as conn:
        conn.execute(
            "DELETE FROM shadow_paired_orders WHERE run_id = ? AND local_id = ?",
            (run_id, local_id),
        )
        conn.commit()


def validate_paired_pair_attribution(
    db_path: Path | str, *, run_id: str, pair_id: str,
) -> None:
    """Refuse a taker completion unless its original pair has feed attribution."""
    with closing(get_connection(Path(db_path))) as conn:
        row = conn.execute(
            """SELECT 1 FROM shadow_paired_orders
               WHERE run_id = ? AND pair_id = ? AND event_cluster_id != ''
               LIMIT 1""",
            (run_id, pair_id),
        ).fetchone()
        if row is None:
            raise PairedShadowError(
                f"pair {pair_id} has no stable paired feed attribution"
            )


def copy_paired_order_attribution(
    db_path: Path | str, *, run_id: str, pair_id: str, local_id: str,
    condition_id: str,
) -> None:
    """Copy the original pair's feed attribution to its taker completion order."""
    with closing(get_connection(Path(db_path))) as conn:
        row = conn.execute(
            """SELECT arm, cutoff_usd, snapshot_id, event_cluster_id,
                      event_cluster_source, event_title, market_slug,
                      family_label, admission_role, admitted_at
               FROM shadow_paired_orders WHERE run_id = ? AND pair_id = ?
               ORDER BY admitted_at ASC LIMIT 1""",
            (run_id, pair_id),
        ).fetchone()
        if row is None:
            raise PairedShadowError(
                f"pair {pair_id} has no paired order attribution to copy"
            )
        conn.execute(
            """INSERT OR IGNORE INTO shadow_paired_orders
               (local_id, run_id, condition_id, pair_id, arm, cutoff_usd,
                snapshot_id, event_cluster_id, event_cluster_source, event_title,
                market_slug, family_label, admission_role, admitted_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (local_id, run_id, condition_id, pair_id, row["arm"], row["cutoff_usd"],
             row["snapshot_id"], row["event_cluster_id"],
             row["event_cluster_source"], row["event_title"], row["market_slug"],
             row["family_label"] or "", row["admission_role"] or "",
             row["admitted_at"]),
        )
        conn.commit()


def _bid_liquidation_value(book: dict, shares: float) -> float | None:
    """Walk available bids for a full-size liquidation; unknown depth is missing."""
    side = (book or {}).get("bids") or {}
    if isinstance(side, dict):
        levels = [(float(price), float(size)) for price, size in side.items()]
    else:
        levels = []
        for level in side:
            try:
                levels.append((float(level["price"]), float(level["size"])))
            except (KeyError, TypeError, ValueError):
                continue
    levels = sorted(
        ((p, s) for p, s in levels
         if math.isfinite(p) and math.isfinite(s) and p > 0 and s > 0),
        reverse=True,
    )
    remaining = shares
    proceeds = 0.0
    for price, size in levels:
        take = min(remaining, size)
        proceeds += take * price
        remaining -= take
        if remaining <= 1e-8:
            return proceeds
    return None


def _paired_inventory(
    db_path: Path | str, *, run_id: str, condition_id: str,
    up_token_id: str, down_token_id: str,
) -> tuple[dict[str, float] | None, str]:
    """Replay one paired run's fills and closes into net outcome positions.

    `inventory_from_registry` is intentionally shared with normal registry
    consumers, but its legacy run scope is condition-wide. The paired report
    needs run-owned positions and must also retire `shadow_settlement` closes,
    so replay this isolated run ledger with explicit attribution instead.
    """
    token_side = {str(up_token_id): "UP", str(down_token_id): "DOWN"}
    if not all(token_side) or len(token_side) != 2:
        return None, "invalid UP/DOWN token map"
    with closing(get_connection(Path(db_path))) as conn:
        fills = conn.execute(
            """SELECT f.trade_id, f.venue_ts, f.recorded_ts, f.size, f.price,
                      o.token_id, o.side
               FROM fills f JOIN orders o ON o.id = f.order_uuid
               WHERE o.run_id = ? AND o.condition_id = ?
               ORDER BY f.venue_ts, f.recorded_ts, f.trade_id""",
            (run_id, condition_id),
        ).fetchall()
        closes = conn.execute(
            """SELECT id, ts, method, shares, up_price, dn_price,
                      cost_basis, proceeds, realized_pnl,
                      up_cost_removed, dn_cost_removed
               FROM closes WHERE run_id = ? AND condition_id = ?
               ORDER BY ts, id""",
            (run_id, condition_id),
        ).fetchall()

    events: list[tuple[float, int, str, object]] = []
    for row in fills:
        raw_ts = row["venue_ts"] or row["recorded_ts"]
        if raw_ts is None:
            return None, f"fill {row['trade_id']} has no timestamp"
        fill_ts = float(raw_ts)
        if fill_ts > 10_000_000_000:
            fill_ts /= 1000.0
        events.append((fill_ts, 0, str(row["trade_id"]), row))
    for row in closes:
        events.append((float(row["ts"]), 1, str(row["id"]), row))
    events.sort(key=lambda item: item[:3])

    balance = {"UP": [0.0, 0.0], "DOWN": [0.0, 0.0]}
    eps = 1e-7
    settled = False
    for _, kind, _key, row in events:
        if kind == 0:
            side = token_side.get(str(row["token_id"] or ""))
            order_side = str(row["side"] or "").upper()
            try:
                shares = float(row["size"])
                price = float(row["price"])
            except (TypeError, ValueError):
                return None, f"fill {row['trade_id']} has invalid size or price"
            if (side is None or order_side not in {"BUY", "SELL"}
                    or not math.isfinite(shares) or not math.isfinite(price)
                    or shares <= 0 or price <= 0):
                return None, f"fill {row['trade_id']} has invalid token, side, size, or price"
            if order_side != "BUY":
                return None, f"fill {row['trade_id']} is a sell without replayable cost removal"
            balance[side][0] += shares
            balance[side][1] += shares * price
            if balance[side][0] < -eps or balance[side][1] < -eps:
                return None, f"fill {row['trade_id']} exceeds replayed inventory"
            continue

        method = str(row["method"] or "")
        if method == "shadow_settlement":
            if settled:
                return None, f"close {row['id']} duplicates a shadow settlement"
            try:
                close_shares = float(row["shares"])
                close_cost = float(row["cost_basis"])
                close_pnl = float(row["realized_pnl"])
            except (TypeError, ValueError):
                return None, f"close {row['id']} has incomplete settlement accounting"
            expected_shares = balance["UP"][0] + balance["DOWN"][0]
            expected_cost = balance["UP"][1] + balance["DOWN"][1]
            if (not all(math.isfinite(v) for v in
                        (close_shares, close_cost, close_pnl))
                    or abs(close_shares - expected_shares) > 1e-5
                    or abs(close_cost - expected_cost) > max(1e-5, expected_cost * 1e-5)):
                return None, f"close {row['id']} settlement does not reconcile to replayed inventory"
            balance = {"UP": [0.0, 0.0], "DOWN": [0.0, 0.0]}
            settled = True
            continue
        if settled:
            return None, f"close {row['id']} follows a terminal settlement"
        try:
            shares = float(row["shares"])
        except (TypeError, ValueError):
            return None, f"close {row['id']} has invalid shares"
        if not math.isfinite(shares) or shares <= 0:
            return None, f"close {row['id']} has invalid shares"
        if method in {"shadow_merge", "merge"}:
            legs = ("UP", "DOWN")
        elif method in {"single_buy_exit", "naked_exit", "ladder_exit"}:
            if row["up_price"] is not None and row["dn_price"] is None:
                legs = ("UP",)
            elif row["dn_price"] is not None and row["up_price"] is None:
                legs = ("DOWN",)
            else:
                return None, f"close {row['id']} does not identify one exited leg"
        else:
            return None, f"unsupported close method {method!r}"
        for leg in legs:
            removed_raw = row["up_cost_removed"] if leg == "UP" else row["dn_cost_removed"]
            if removed_raw is None:
                return None, f"close {row['id']} has no {leg} cost removed"
            removed_cost = float(removed_raw)
            if (not math.isfinite(removed_cost) or removed_cost < -eps
                    or balance[leg][0] + eps < shares
                    or balance[leg][1] + eps < removed_cost):
                return None, f"close {row['id']} exceeds replayed {leg} inventory"
            balance[leg][0] = max(0.0, balance[leg][0] - shares)
            balance[leg][1] = max(0.0, balance[leg][1] - removed_cost)

    return {
        "up_shares": balance["UP"][0], "up_cost": balance["UP"][1],
        "down_shares": balance["DOWN"][0], "down_cost": balance["DOWN"][1],
    }, ""


def record_paired_equity_mark(
    registry, db_path: Path | str, *, run_id: str,
    starting_bankroll_usd: float, book_fn: Callable[[str, str], dict],
    clob_host: str, ts: float | None = None,
) -> dict:
    """Mark every run-owned position to full liquidation value, then total equity."""
    now = time.time() if ts is None else float(ts)
    with closing(get_connection(Path(db_path))) as conn:
        admissions = conn.execute(
            """SELECT a.condition_id, a.event_cluster_id,
                      t.up_token_id, t.down_token_id
               FROM shadow_paired_admissions a
               LEFT JOIN shadow_paired_market_tokens t
                 ON t.run_id = a.run_id AND t.condition_id = a.condition_id
               JOIN (
                   SELECT condition_id, MAX(rowid) AS admission_rowid
                   FROM shadow_paired_admissions WHERE run_id = ?
                   GROUP BY condition_id
               ) latest ON latest.condition_id = a.condition_id
                      AND latest.admission_rowid = a.rowid
               JOIN (
                   SELECT DISTINCT o.condition_id
                   FROM orders o JOIN fills f ON f.order_uuid = o.id
                   WHERE o.run_id = ?
               ) exposed ON exposed.condition_id = a.condition_id
               WHERE a.run_id = ?""", (run_id, run_id, run_id)
        ).fetchall()
        realized = conn.execute(
            "SELECT COALESCE(SUM(realized_pnl), 0) AS pnl FROM closes WHERE run_id = ?",
            (run_id,),
        ).fetchone()["pnl"]
        missing_orders = conn.execute(
            """SELECT COUNT(*) AS n FROM orders o
               LEFT JOIN shadow_paired_orders p ON p.local_id = o.id
               WHERE o.run_id = ? AND p.local_id IS NULL""", (run_id,)
        ).fetchone()["n"]

    missing: list[str] = []
    total_unrealized = 0.0
    total_committed = 0.0
    market_marks: list[tuple[str, str, float, float, int, str]] = []
    # Resolved conditions are never book-read (#402): their value is booked
    # at settlement, not liquidation, so no book here could price them.
    resolved = resolved_condition_ids(registry)
    for row in admissions:
        cid = str(row["condition_id"])
        cluster_id = str(row["event_cluster_id"] or "")
        up_token = str(row["up_token_id"] or "")
        down_token = str(row["down_token_id"] or "")
        if not up_token or not down_token or up_token == down_token:
            # No position can mean a never-filled market. Check fills before
            # calling this a data gap; any unlabelled holding is unmeasurable.
            with closing(get_connection(Path(db_path))) as conn:
                has_fills = conn.execute(
                    """SELECT 1 FROM fills f JOIN orders o ON o.id=f.order_uuid
                       WHERE o.run_id=? AND o.condition_id=? LIMIT 1""",
                    (run_id, cid),
                ).fetchone()
            if has_fills:
                missing.append(cid)
                market_marks.append((cid, cluster_id, 0.0, 0.0, 0,
                                     "missing UP/DOWN token mapping"))
                continue
            market_marks.append((cid, cluster_id, 0.0, 0.0, 1, ""))
            continue
        inventory, inventory_error = _paired_inventory(
            db_path, run_id=run_id, condition_id=cid,
            up_token_id=up_token, down_token_id=down_token)
        if inventory is None:
            missing.append(cid)
            market_marks.append((cid, cluster_id, 0.0, 0.0, 0,
                                 inventory_error))
            continue
        held = ((up_token, inventory["up_shares"]),
                (down_token, inventory["down_shares"]))
        proceeds = 0.0
        position_missing = ""
        if cid.lower() in resolved:
            # No book read: the market is over and its liquidation value is
            # not on a book. The settlement path owns the value; this mark
            # says so plainly instead of fabricating a total loss.
            market_marks.append((cid, cluster_id, 0.0, 0.0, 0,
                                 "resolved; valued at settlement, not on book"))
            missing.append(cid)
            continue
        for token, shares in held:
            if shares <= 1e-8:
                continue
            try:
                book = book_fn(clob_host, token)
                value = _bid_liquidation_value(book, shares)
            except Exception as exc:  # telemetry cannot terminate a rehearsal
                value = None
                position_missing = f"book read failed: {type(exc).__name__}"
            if value is None:
                position_missing = position_missing or f"insufficient bid depth:{token}"
                break
            proceeds += value
        cost = inventory["up_cost"] + inventory["down_cost"]
        valid = not position_missing
        pnl = proceeds - cost if valid else 0.0
        committed = cost if valid else 0.0
        market_marks.append((cid, cluster_id, pnl, committed,
                             1 if valid else 0, position_missing))
        if valid:
            total_unrealized += pnl
            total_committed += committed
        else:
            missing.append(cid)

    with closing(get_connection(Path(db_path))) as conn:
        conn.executemany(
            """INSERT INTO shadow_paired_market_marks
               (run_id, ts, condition_id, event_cluster_id, unrealized_pnl,
                committed_open_usd, valid, missing_reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [(run_id, now, *row) for row in market_marks],
        )
        if missing_orders:
            missing.append(f"{int(missing_orders)} order(s) lack paired attribution")
        valid = not missing
        equity = float(starting_bankroll_usd) + float(realized or 0.0) + total_unrealized
        conn.execute(
            """INSERT INTO shadow_paired_equity_marks
               (run_id, ts, equity_usd, realized_pnl, unrealized_pnl,
                committed_open_usd, valid, missing_conditions_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, now, equity, float(realized or 0.0), total_unrealized,
             total_committed, 1 if valid else 0, json.dumps(missing)),
        )
        conn.commit()
    return {"ts": now, "equity_usd": equity, "realized_pnl": float(realized or 0.0),
            "unrealized_pnl": total_unrealized,
            "committed_open_usd": total_committed,
            "valid": not missing,
            "missing_conditions": missing}


def record_paired_run_finish(
    db_path: Path | str, *, run_id: str, finished_at: float | None = None,
) -> None:
    with closing(get_connection(Path(db_path))) as conn:
        changed = conn.execute(
            """UPDATE shadow_paired_runs SET finished_at = ?, status = 'finished'
               WHERE run_id = ? AND status = 'running'""",
            (time.time() if finished_at is None else float(finished_at), run_id),
        ).rowcount
        if changed != 1:
            raise PairedShadowError(f"paired run {run_id!r} was not marked running")
        conn.commit()
