"""Append-only cycle telemetry stream and intent logger for the live engine.

Writes a compact NDJSON ring to `live/runtime/cycle_events.jsonl` (max 500 lines with atomic
rotation) and logs cycle decisions to `cycle_intent` in `orders.db`.

Designed for zero latency impact on the core loop:
- Fire-and-forget: never raises an exception out of `emit()`.
- O_APPEND append: one os.write per event, atomic at EOF across processes.
- Atomic rotation via tempfile + os.fsync + os.replace owned by the engine process.
"""
from __future__ import annotations

import atexit
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

# ring path -> (size in bytes we last left the file at, line count at that size).
# A count of None means "unknown, go read the file". Re-opening the ring for
# read costs ~20ms per call on Windows once the file has just been modified
# (real-time AV rescans it), so the rotation check must not read on every
# append. Our own appends grow the file by a known number of bytes and exactly
# one line, so the count stays exact for as long as nothing else writes; a size
# that does not match invalidates the entry and the next check reads for real.
_RING_LINES: dict[str, tuple[int, Optional[int]]] = {}
_RING_LINES_MAX_ENTRIES = 32

LIVE_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RING_PATH = LIVE_ROOT / "runtime" / "cycle_events.jsonl"

# One registry, one path. This module used to hardcode data/orders.db while every
# other caller resolved data/orders.db through order_registry, so cycle_intent
# rows landed in a database the dashboard never read.
from core_brain.order_registry import DEFAULT_DB_PATH  # noqa: E402
from core_brain.runtime_paths import resolve_runtime_file  # noqa: E402

MAX_LINES = 500
KEEP_LINES = 400

CYCLE_INTENT_KEEP_ROWS = 200
_INTENT_RETRY_BACKOFF_SEC = 0.05

# The cycle_intent table tracks quoting intents and their per-visit outcomes:
# `intent_count` is decided intents for that visit; `submitted` and `cancelled`
# are filled by the later submit or market_error event for the exact visit keyed
# by (market_slug, cycle, run_id).
# `submitted` counts order legs placed by the quote loop during that single visit
# (assigned, not incremented). Dry-run mode and order reuse yield 0. Placements
# outside the quote loop (rescue completion BUYs, order_manager quote/probe)
# are excluded. The table retains only the newest CYCLE_INTENT_KEEP_ROWS (200) rows,
# so SUM(submitted) represents a rolling window metric, not daily cumulative volume.
_CREATE_CYCLE_INTENT = """
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

# The cycle_intent connection, kept open between events. Connecting costs a
# few milliseconds, and emit() runs on the engine's hot path once per market
# visit, so rebuilding the handle per event was pure overhead. One entry: the
# engine writes a single registry for its whole life, while tests walk through
# temporary files, so a cache of one never grows and never strands a handle.
_DB_LOCK = threading.RLock()
_DB_CACHE: dict[str, sqlite3.Connection] = {}


def close_intent_connections() -> None:
    """Close the cached cycle_intent handle.

    The engine never needs this -- the handle lives as long as the process --
    but a test that wants a fresh connection, or a caller shutting down before
    the interpreter exits, does.
    """
    with _DB_LOCK:
        for key in list(_DB_CACHE):
            conn = _DB_CACHE.pop(key)
            try:
                conn.close()
            except Exception:
                pass


atexit.register(close_intent_connections)


def _intent_connection(path: Path) -> sqlite3.Connection:
    """The open connection for `path`, building it on first use.

    `check_same_thread=False` plus `_DB_LOCK` rather than a per-thread handle:
    the engine emits from more than one thread, and serialising the writes is
    what SQLite wants anyway. Callers must hold `_DB_LOCK`.
    """
    key = str(path)
    existing = _DB_CACHE.get(key)
    if existing is not None:
        return existing
    close_intent_connections()
    conn = sqlite3.connect(key, timeout=1.0, check_same_thread=False)
    conn.execute(_CREATE_CYCLE_INTENT)
    conn.commit()
    _DB_CACHE[key] = conn
    return conn


def _with_intent_connection(path: Path, work):
    """Run `work(conn)` on the cached connection, rebuilding it once on error.

    A cached handle that has gone bad would otherwise turn one failure into
    every future one, so a `sqlite3.Error` drops it and retries exactly once;
    a second failure belongs to the caller's warning. That covers the errors
    SQLite actually reports: a closed handle, a lock it could not take, a
    statement it refused.

    It does NOT cover the database file being replaced underneath a live
    handle, and nothing here should be read as promising otherwise. On POSIX
    the open handle stays bound to the old inode, the write "succeeds" into a
    file nobody reads, and no exception is raised for a retry to catch --
    SQLite documents unlinking or renaming a database in use as unsafe. The
    supported way to swap the store is to call `close_intent_connections()`
    first and replace it while nothing holds it open; `test_replacing_the_
    store_needs_the_handle_closed_first` pins that procedure. Nothing in this
    repo replaces `data/orders.db` under a running engine today.
    """
    with _DB_LOCK:
        try:
            return work(_intent_connection(path))
        except sqlite3.Error:
            close_intent_connections()
            # Retrying a locked database in the same instant just fails again,
            # so the pause is what makes this a retry rather than a formality.
            # It stays short on purpose: emit() sits on the trading loop, and
            # this module's contract is no latency impact, so a write that
            # stays contended is dropped with a warning instead of stalling a
            # cycle. Worst case here is the two 1s busy timeouts plus this.
            time.sleep(_INTENT_RETRY_BACKOFF_SEC)
            return work(_intent_connection(path))


def _resolve_run_id() -> str:
    """The current run id, resolved the same way for decide and submit events."""
    try:
        from core_brain.order_registry import get_run_id
        return get_run_id()
    except Exception:
        return "live"


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
    """Fire-and-forget INSERT into cycle_intent table, pruning older than 200 rows.

    Records the initial decide event with submitted=0 and cancelled=0. These fields
    are later assigned by _update_cycle_intent when the visit finishes quoting.
    """
    p = Path(db_path) if db_path else DEFAULT_DB_PATH
    if not p.parent.exists():
        p.parent.mkdir(parents=True, exist_ok=True)

    r_id = run_id or _resolve_run_id()

    now_ts = time.time()

    def insert(conn: sqlite3.Connection) -> None:
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
        # Pruned on every insert, not in batches: the retention window is what
        # the dashboard reads, so the table must never be seen over it.
        conn.execute(
            """
            DELETE FROM cycle_intent
            WHERE id NOT IN (
                SELECT id FROM cycle_intent ORDER BY id DESC LIMIT ?
            )
            """,
            (CYCLE_INTENT_KEEP_ROWS,),
        )
        conn.commit()

    try:
        _with_intent_connection(p, insert)
    except Exception as exc:
        # Non-blocking / fire-and-forget
        print(f"WARNING: cycle_intent insert failed: {exc}", file=sys.stderr)


def _update_cycle_intent(
    market_slug: str,
    cycle: int,
    run_id: str,
    submitted: int = 0,
    cancelled: int = 0,
    db_path: Path | None = None,
) -> None:
    """Fire-and-forget UPDATE of the cycle_intent row for one market visit.

    Matching on market_slug alone would let a later decide event for the same
    market (before this submit arrives) capture this submit's outcome. cycle +
    run_id + market_slug identify the exact visit the decide event inserted.

    `submitted` records orders placed by the quote loop for this visit (assigned,
    not incremented), preserving partial placements on error. Placements outside
    the quote loop (rescue completion BUYs, manual orders) are not reflected here.
    """
    p = Path(db_path) if db_path else DEFAULT_DB_PATH
    if not p.exists():
        print(f"WARNING: cycle_intent update skipped, db missing: {p}", file=sys.stderr)
        return

    def update(conn: sqlite3.Connection) -> None:
        cur = conn.execute(
            """
            UPDATE cycle_intent SET submitted = ?, cancelled = ?
            WHERE id = (
                SELECT id FROM cycle_intent
                WHERE market_slug = ? AND cycle = ? AND run_id = ?
                ORDER BY id DESC LIMIT 1
            )
            """,
            (submitted, cancelled, market_slug, cycle, run_id),
        )
        if cur.rowcount == 0:
            print(
                f"WARNING: cycle_intent update matched 0 rows: market_slug={market_slug} "
                f"cycle={cycle} run_id={run_id}",
                file=sys.stderr,
            )
        conn.commit()

    try:
        _with_intent_connection(p, update)
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
    payload = line.encode("utf-8")
    with _APPEND_LOCK:
        fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT)
        try:
            # fstat on the fd we already hold: no second open, so no AV rescan.
            # Compare sizes rather than adding len(payload): os.open has no
            # O_BINARY here, so on Windows each "\n" reaches disk as "\r\n" and
            # the file grows by more bytes than we handed to os.write.
            size_before = os.fstat(fd).st_size
            os.write(fd, payload)
            size_after = os.fstat(fd).st_size
        finally:
            os.close(fd)
        key = str(path)
        previous = _RING_LINES.get(key)
        if previous is not None and previous[1] is not None \
                and previous[0] == size_before:
            _RING_LINES[key] = (size_after, previous[1] + 1)
        else:
            if len(_RING_LINES) >= _RING_LINES_MAX_ENTRIES:
                _RING_LINES.clear()
            _RING_LINES[key] = (size_after, None)


def _rotate_ring_file(ring_path: Path) -> None:
    """Atomic rotation of the ring file keeping the last KEEP_LINES.

    A concurrent writer (fleet/screener) can append between our read and the
    replace. A fully atomic rotation would need a cross-process lock shared
    with those decoupled writers (they must not import core_brain.*), so instead
    we re-stat after reading and skip this rotation when the file grew. The
    residual window (append after the re-stat, before os.replace) can drop at
    most one telemetry line in a rare race; the next emit re-checks.
    """
    key = str(ring_path)
    try:
        if not ring_path.exists():
            _RING_LINES.pop(key, None)
            return
        size_before = ring_path.stat().st_size
        cached = _RING_LINES.get(key)
        if cached is not None and cached[1] is not None \
                and cached[0] == size_before and cached[1] <= MAX_LINES:
            # We know the count and it is under the limit, so there is nothing
            # to rotate and no reason to pay for a read of the whole file.
            return
        with open(ring_path, "r", encoding="utf-8", errors="replace") as rf:
            lines = rf.readlines()
        if len(lines) <= MAX_LINES:
            _RING_LINES[key] = (size_before, len(lines))
            return
        if ring_path.stat().st_size != size_before:
            # A concurrent append landed during our read; don't drop it.
            _RING_LINES.pop(key, None)
            return
        kept = lines[-KEEP_LINES:]
        tmp_path = ring_path.with_name(f"{ring_path.name}.tmp.{uuid.uuid4()}")
        with open(tmp_path, "w", encoding="utf-8") as tf:
            tf.writelines(kept)
            tf.flush()
            os.fsync(tf.fileno())
        os.replace(tmp_path, ring_path)
        _RING_LINES[key] = (ring_path.stat().st_size, len(kept))
    except Exception as exc:
        _RING_LINES.pop(key, None)
        print(f"WARNING: cycle_stream rotation failed: {exc}", file=sys.stderr)


def emit(
    cycle: int,
    phase: str,
    action: str,
    *,
    service: str = "query",
    market_slug: str = "",
    reason: str = "",
    latency_ms: float = 0.0,
    extra: dict | None = None,
    ring_path: Path | None = None,
    db_path: Path | None = None,
    can_rotate: bool | None = None,
    run_id: str | None = None,
) -> None:
    """Append one NDJSON event to the ring file and log cycle intent if relevant.

    Never raises into caller.

    `run_id` scopes the cycle_intent rows to the session that emitted them.
    A shadow session must pass its registry's `shadow-...` id here: the
    fallback is the process-wide resolver, which returns the LIVE lock-file
    id for 12 hours -- all 200 of a rehearsal's intent rows once landed on
    the live session (`run-5eb297de8751`) that way.
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

        # Rotation check (default True for query service)
        should_rotate = (service in ("query", "engine")) if can_rotate is None else can_rotate
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
                run_id=run_id,
            )
        elif phase == "quoting" and action in ("submit", "market_error"):
            # submit carries the outcome of a successful decide; market_error on
            # the submit/cancel path carries the partial counts that must not be
            # left at zero. Either way update the same row the decide inserted.
            _update_cycle_intent(
                market_slug=market_slug,
                cycle=cycle,
                run_id=run_id or _resolve_run_id(),
                submitted=int((extra or {}).get("submitted", 0)),
                cancelled=int((extra or {}).get("cancelled", 0)),
                db_path=db_path,
            )
    except Exception as exc:
        print(f"WARNING: cycle_stream emit failed: {exc}", file=sys.stderr)


def fill_extra(fill: Any, order: Any, meta: dict | None = None) -> dict:
    """Shape one venue-confirmed fill into a `fill_recorded` event payload.

    Pure: size and price always come from the fill, identity from the order.
    `outcome` (UP/DOWN) and `market_title` ride in only when the caller already
    knows them via `meta` -- unknown stays unknown, never guessed.
    """
    extra: dict[str, Any] = {
        "condition_id": getattr(order, "condition_id", None),
        "token_id": getattr(order, "token_id", None),
        "order_id": getattr(order, "order_id", None) or getattr(order, "id", None),
        "trade_id": getattr(fill, "trade_id", None),
        "pair_id": getattr(order, "pair_id", None),
        "side": str(getattr(order, "side", "") or "").upper() or None,
        "size": float(getattr(fill, "size", 0.0) or 0.0),
        "price": float(getattr(fill, "price", 0.0) or 0.0),
    }
    if isinstance(meta, dict):
        outcome = meta.get("outcome")
        if outcome in ("UP", "DOWN"):
            extra["outcome"] = outcome
        title = meta.get("title")
        if title:
            extra["market_title"] = title
    return {k: v for k, v in extra.items() if v is not None}


def make_fill_observer(emit_fn: Any, *, service: str, phase: str,
                       meta_lookup: Any = None) -> Any:
    """Build the `on_fill_recorded` callback for `reconcile_orders`.

    `meta_lookup` maps a condition id to a market-meta dict (callers pass
    `resolve_market_meta`, which reads local files only). A failing lookup
    still emits with empty name fields; a failing emitter never raises into
    the reconcile pass.
    """
    def _on_fill_recorded(fill: Any, order: Any) -> None:
        try:
            meta = None
            if meta_lookup is not None:
                try:
                    meta = meta_lookup(getattr(order, "condition_id", "") or "")
                except Exception:
                    meta = None
            slug = ""
            if isinstance(meta, dict):
                slug = meta.get("slug") or ""
            emit_fn(
                service=service,
                cycle=0,
                phase=phase,
                action="fill_recorded",
                market_slug=slug,
                extra=fill_extra(fill, order, meta if isinstance(meta, dict) else None),
            )
        except Exception as exc:
            print(f"WARNING: fill observer failed: {exc}", file=sys.stderr)

    return _on_fill_recorded

_LIFECYCLE_ALLOW = {
    "pair_id", "condition_id", "token_id", "size", "requested_size",
    "fill_price", "min_price", "ask", "pair_cost", "notional",
    "order_id", "route", "lifecycle_state", "settlement_reason",
}


def lifecycle_extra(result) -> dict:
    """Shape one lifecycle result into the enriched `lifecycle_*` payload.

    Allow-listed copy only: `side` becomes `outcome` (UP/DOWN), `venue_side`
    is derived (SELL for `exited`, BUY for `completed`), and `response` never
    leaves this function. Pure and never raises into the caller.
    """
    if not isinstance(result, dict):
        return {}
    extra: dict = {}
    for key in sorted(_LIFECYCLE_ALLOW):
        if key in result and result[key] is not None:
            extra[key] = result[key]
    side = result.get("side")
    if side in ("UP", "DOWN"):
        extra["outcome"] = side
    action = result.get("action")
    if action == "exited":
        extra["venue_side"] = "SELL"
    elif action == "completed":
        extra["venue_side"] = "BUY"
    return extra


def relayer_extra(
    *,
    condition_id: str,
    size=None,
    relayer_state: str = "",
    transaction_hash=None,
    transaction_id=None,
) -> dict:
    """Shape one relayer outcome into a `merge_*`/`redeem_*` payload.

    `size` rides only when the caller measured it; hash and id stay separate
    so no sentence ever presents a relayer id as on-chain proof. Pure.
    """
    extra: dict = {"condition_id": condition_id}
    if size is not None:
        extra["size"] = float(size)
    if relayer_state:
        extra["relayer_state"] = relayer_state
    if transaction_hash:
        extra["transaction_hash"] = transaction_hash
    if transaction_id and transaction_id != transaction_hash:
        extra["transaction_id"] = transaction_id
    return extra



# Where a tail read starts, counted back from the end of the ring. A cycle
# event is a few hundred bytes, so this covers a 100-line tail many times over
# and the loop below widens it on the rare line that is longer.
RING_TAIL_CHUNK_BYTES = 128 * 1024


def tail_lines_fh(fh, tail: int) -> list[str]:
    """`tail_lines`, against a handle the caller already opened.

    The SSE stream has to answer two questions about the SAME file state: what
    the last lines are, and what byte offset to follow from. Reopening the path
    between them straddles a rotation -- the replay would come from the new
    ring while the follow loop resumed at the old ring's end offset, skipping
    everything before it. One handle answers both.

    The handle is left open and its position is not guaranteed; callers that
    still need it should seek.
    """
    fh.seek(0, os.SEEK_END)
    size = fh.tell()
    if size == 0:
        return []
    chunk = min(size, RING_TAIL_CHUNK_BYTES)
    while True:
        fh.seek(size - chunk)
        raw = fh.read(chunk)
        lines = raw.splitlines()
        # The first line is a fragment unless the read reached the start
        # of the file.
        if chunk < size:
            lines = lines[1:]
        if len(lines) >= tail or chunk >= size:
            break
        chunk = min(size, chunk * 4)
    return [line.decode("utf-8", errors="replace") for line in lines[-tail:]]


def tail_lines(path: Path, tail: int) -> list[str]:
    """The last `tail` lines, read from the end rather than from the start.

    The ring grows for the life of a run -- 33.8 MB and 108,317 lines on a
    one-day rehearsal -- and three dashboard endpoints call `read_ring` on
    every 2s poll. Loading every line to keep the last hundred meant ~100 MB of
    reads per poll: polls ran 5-11s, the page aborted them at its 5s timeout,
    and the STALE banner flickered on and off.
    """
    with open(path, "rb") as fh:
        return tail_lines_fh(fh, tail)


def read_ring(
    ring_path: Path | None = None,
    tail: int = 100,
    *,
    strict: bool = False,
) -> list[dict]:
    """Read the last `tail` parsed JSON events from the ring file.

    `emit()` always writes `runtime/`, but a reader with no explicit path
    resolves the pre-rename `run/cycle_events.jsonl` while only that one exists
    -- otherwise the guardrail watcher reads an empty ring right after the rename
    and misses a repeat-exit alert.

    A non-positive `tail` still means every event. When ``strict`` is true,
    file-read failures are raised so diagnostic callers can distinguish an
    unavailable ring from a genuinely empty one. Malformed individual JSON
    lines remain ignored in either mode.
    """

    p = Path(ring_path) if ring_path else resolve_runtime_file(
        DEFAULT_RING_PATH.name, root=LIVE_ROOT)
    if not p.exists():
        return []
    try:
        if tail > 0:
            lines = tail_lines(p, tail)
        else:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        events = []
        for line in lines:
            line_str = line.strip()
            if not line_str:
                continue
            try:
                events.append(json.loads(line_str))
            except Exception:
                continue
        return events
    except Exception as exc:
        if strict:
            raise
        print(f"WARNING: cycle_stream read_ring failed: {exc}", file=sys.stderr)
        return []
