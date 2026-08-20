"""Append-only cycle telemetry stream and intent logger for the live engine.

Writes a compact NDJSON ring to `live/run/cycle_events.jsonl` (max 500 lines with atomic
rotation) and logs cycle decisions to `cycle_intent` in `live.db`.

Designed for zero latency impact on the core loop:
- Fire-and-forget: never raises an exception out of `emit()`.
- O_APPEND append: one os.write per event, atomic at EOF across processes.
- Atomic rotation via tempfile + os.fsync + os.replace owned by the engine process.
"""
from __future__ import annotations

import datetime
import json
import os
import sqlite3
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

# Serializes appends within one process. Cross-process atomicity comes from
# O_APPEND (a single os.write lands at EOF as one syscall), not from this lock.
_APPEND_LOCK = threading.Lock()

LIVE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RING_PATH = LIVE_ROOT / "run" / "cycle_events.jsonl"
DEFAULT_DB_PATH = LIVE_ROOT / "run" / "live.db"

MAX_LINES = 500
KEEP_LINES = 400


def _write_cycle_intent(
    cycle: int,
    market_slug: str,
    condition_id: Optional[str] = None,
    intent_count: int = 0,
    top_skip_reason: Optional[str] = None,
    top_pass_reason: Optional[str] = None,
    latency_ms: float = 0.0,
    db_path: Path | None = None,
    run_id: Optional[str] = None,
) -> None:
    """Fire-and-forget INSERT into cycle_intent table, pruning older than 200 rows."""
    p = Path(db_path) if db_path else DEFAULT_DB_PATH
    if not p.parent.exists():
        p.parent.mkdir(parents=True, exist_ok=True)

    r_id = run_id
    if r_id is None:
        try:
            from engine.order_registry import get_run_id
            r_id = get_run_id()
        except Exception:
            r_id = "live"

    now_ts = time.time()
    try:
        with sqlite3.connect(str(p), timeout=1.0) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cycle_intent (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    cycle INTEGER NOT NULL,
                    market_slug TEXT NOT NULL,
                    condition_id TEXT,
                    intent_count INTEGER NOT NULL DEFAULT 0,
                    submitted INTEGER NOT NULL DEFAULT 0,
                    cancelled INTEGER NOT NULL DEFAULT 0,
                    top_skip_reason TEXT,
                    top_pass_reason TEXT,
                    latency_ms REAL,
                    run_id TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                INSERT INTO cycle_intent (
                    ts, cycle, market_slug, condition_id, intent_count,
                    submitted, cancelled, top_skip_reason, top_pass_reason,
                    latency_ms, run_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now_ts,
                    cycle,
                    market_slug,
                    condition_id,
                    intent_count,
                    0,  # submitted/cancelled are filled by the later submit event
                    0,
                    top_skip_reason,
                    top_pass_reason,
                    latency_ms,
                    r_id,
                ),
            )
            conn.execute(
                """
                DELETE FROM cycle_intent
                WHERE id NOT IN (
                    SELECT id FROM cycle_intent ORDER BY id DESC LIMIT 200
                )
                """
            )
            conn.commit()
    except Exception as exc:
        # Non-blocking / fire-and-forget
        print(f"WARNING: cycle_intent insert failed: {exc}", file=sys.stderr)


def _update_cycle_intent(
    market_slug: str,
    submitted: int = 0,
    cancelled: int = 0,
    db_path: Path | None = None,
) -> None:
    """Fire-and-forget UPDATE of the newest cycle_intent row for a market.

    The submit event of the same market visit carries the counts; writing them
    onto the decide row keeps one row per market visit (the schema's
    submitted/cancelled columns are the outcome, not a second decision).
    """
    p = Path(db_path) if db_path else DEFAULT_DB_PATH
    if not p.exists():
        return
    try:
        with sqlite3.connect(str(p), timeout=1.0) as conn:
            conn.execute(
                """
                UPDATE cycle_intent SET submitted = ?, cancelled = ?
                WHERE id = (
                    SELECT id FROM cycle_intent
                    WHERE market_slug = ? ORDER BY id DESC LIMIT 1
                )
                """,
                (submitted, cancelled, market_slug),
            )
            conn.commit()
    except Exception as exc:
        # Non-blocking / fire-and-forget
        print(f"WARNING: cycle_intent update failed: {exc}", file=sys.stderr)


def _append_line(path: Path, line: str) -> None:
    """Append one NDJSON line atomically.

    os.write on an O_APPEND fd appends at EOF in a single syscall, so writers
    in other processes (fleet, screener) can interleave whole lines but never
    corrupt or lose one. A plain open("a") does seek-then-write, which on
    Windows loses a line whenever two writers hit the same end offset -- the
    concurrent-append test caught that race.
    """
    with _APPEND_LOCK:
        fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)


def _rotate_ring_file(ring_path: Path) -> None:
    """Atomic rotation of the ring file keeping the last KEEP_LINES."""
    try:
        if not ring_path.exists():
            return
        with open(ring_path, "r", encoding="utf-8", errors="replace") as rf:
            lines = rf.readlines()
        if len(lines) > MAX_LINES:
            kept = lines[-KEEP_LINES:]
            tmp_path = ring_path.with_name(f"{ring_path.name}.tmp.{uuid.uuid4()}")
            with open(tmp_path, "w", encoding="utf-8") as tf:
                tf.writelines(kept)
                tf.flush()
                os.fsync(tf.fileno())
            os.replace(tmp_path, ring_path)
    except Exception as exc:
        print(f"WARNING: cycle_stream rotation failed: {exc}", file=sys.stderr)


def emit(
    cycle: int,
    phase: str,
    action: str,
    *,
    service: str = "engine",
    market_slug: str = "",
    reason: str = "",
    latency_ms: float = 0.0,
    extra: dict | None = None,
    ring_path: Path | None = None,
    db_path: Path | None = None,
    can_rotate: bool | None = None,
) -> None:
    """Append one NDJSON event to the ring file and log cycle intent if relevant.

    Never raises into caller.
    """
    try:
        target_ring = Path(ring_path) if ring_path else DEFAULT_RING_PATH
        target_ring.parent.mkdir(parents=True, exist_ok=True)

        now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        record: dict[str, Any] = {
            "ts": now_iso,
            "service": service,
            "cycle": cycle,
            "phase": phase,
            "action": action,
            "market_slug": market_slug,
            "reason": reason,
            "latency_ms": round(float(latency_ms), 2) if latency_ms else 0.0,
            "pid": os.getpid(),
            "extra": extra or {},
        }

        _append_line(target_ring, json.dumps(record) + "\n")

        # Rotation check (default True for engine service)
        should_rotate = (service == "engine") if can_rotate is None else can_rotate
        if should_rotate:
            _rotate_ring_file(target_ring)

        # Database intent recording: the decide event INSERTs the row (with the
        # skip/pass rationale), the submit event of the same visit UPDATEs the
        # submitted/cancelled counts onto it. Anything else never touches SQL.
        if phase == "quoting" and action == "decide":
            _write_cycle_intent(
                cycle=cycle,
                market_slug=market_slug,
                condition_id=(extra or {}).get("condition_id"),
                intent_count=int((extra or {}).get("intent_count", 0)),
                top_skip_reason=(extra or {}).get("top_skip_reason")
                                or (reason if not (extra or {}).get("intent_count") else None),
                top_pass_reason=(extra or {}).get("top_pass_reason")
                                or (reason if (extra or {}).get("intent_count") else None),
                latency_ms=latency_ms,
                db_path=db_path,
            )
        elif phase == "quoting" and action == "submit":
            _update_cycle_intent(
                market_slug=market_slug,
                submitted=int((extra or {}).get("submitted", 0)),
                cancelled=int((extra or {}).get("cancelled", 0)),
                db_path=db_path,
            )
    except Exception as exc:
        print(f"WARNING: cycle_stream emit failed: {exc}", file=sys.stderr)


def read_ring(ring_path: Path | None = None, tail: int = 100) -> list[dict]:
    """Read the last `tail` parsed JSON events from the ring file."""
    p = Path(ring_path) if ring_path else DEFAULT_RING_PATH
    if not p.exists():
        return []
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        tail_lines = lines[-tail:] if tail > 0 else lines
        events = []
        for line in tail_lines:
            line_str = line.strip()
            if not line_str:
                continue
            try:
                events.append(json.loads(line_str))
            except Exception:
                continue
        return events
    except Exception as exc:
        print(f"WARNING: cycle_stream read_ring failed: {exc}", file=sys.stderr)
        return []
