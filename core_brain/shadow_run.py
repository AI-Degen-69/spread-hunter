"""Shadow run: the whole loop, against the live book, spending nothing.

`python -m core_brain.shadow_run --minutes 5 --db data/NN_shadow_<stamp>.db`

What this is for: watching the machine work. Every other way to see the full
loop -- screener through quoting through fills through the merge path -- costs
real money, because `--no-live` on `core_brain.order_manager` prints one
subcommand's intent and exits before any of it happens.

What it changes, and it is only three things:

1. **The client cannot sign.** `core_brain.shadow_guard.shadow_client` builds a
   CLOB client with no private key and no API credentials, wrapped in a
   deny-by-default proxy. Submission is not disabled by a flag; there is
   nothing loaded to sign with.
2. **The store is not the production registry.** Every run requires an explicit
   per-run `--db` path, and `data/orders.db` is refused outright.
3. **The run stops on a wall clock.** Driven from the injected `sleep_fn`, so
   `core_brain/trader_loop.py` needs no edit.

Everything else is the live path unchanged: the same `MakerConfig`, the same
`MAX_ORDER_USD` and `MAX_TOTAL_USD`, the same gates, the same
`decide_quotes`. A rehearsal run under a relaxed configuration would be a
rehearsal of something we do not ship.

Unauthenticated reads: books, prices and market metadata on the CLOB are
public endpoints. If one starts requiring authentication, that is a finding
to report, never a reason to load credentials into a shadow run.

The numbers a shadow run produces are rehearsal, not results.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import sqlite3
import sys
import uuid
import time
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from core_brain import rehearsal
from core_brain.code_revision import read_code_revision, revision_label
from core_brain.order_registry import get_connection

log = logging.getLogger("shadow_run")

# Deprecated compatibility alias; the CLI still requires an explicit per-run path.
DEFAULT_SHADOW_DB = Path("data/shadow.db")

# A shadow run is not part of the supervised stack -- it does not appear in
# `runtime/processes.json`, so `dashboard.server.get_system_status` cannot see
# it the way it sees the filter, query and decide services. This heartbeat is
# how the rehearsal publishes its own liveness: written when the run starts,
# refreshed once per rotation, and marked finished when the time box expires so
# a clean end is distinguishable from a crash (a crash simply stops refreshing,
# and the reader calls a stale heartbeat ended).
SHADOW_HEARTBEAT_NAME = "shadow_run.json"
#: Per-run heartbeat name: `shadow_run_<run_id>.json`. Two rehearsals writing
#: at once (a menu stack and a hand-launched rehearsal, say) must not fight
#: over one liveness slot -- the last writer used to win, so a dashboard pointed
#: at either store flickered to "not running" between the two refreshes. Same
#: fix the per-run cycle ring got (`shadow-<run_id>.jsonl`).
SHADOW_HEARTBEAT_PREFIX = "shadow_run_"


def _run_heartbeat_name(run_id: str) -> str:
    """File name for a run-scoped heartbeat: `shadow_run_<run_id>.json`.

    Sanitized exactly like `_run_ring_name`: the id is not ours to trust (it
    can come from `SH_RUN_ID` verbatim), so anything outside
    `[A-Za-z0-9_.-]` becomes a dash, leading/trailing dots and dashes go, and
    the result is capped. Ids already carrying the `shadow-` prefix are not
    prefixed twice.
    """
    token = re.sub(r"[^A-Za-z0-9_.-]", "-", run_id).strip("-.")[:64] or "unnamed"
    if token.startswith("shadow-"):
        return f"{SHADOW_HEARTBEAT_PREFIX}{token}.json"
    return f"{SHADOW_HEARTBEAT_PREFIX}shadow-{token}.json"


def shadow_heartbeat_path(root=None, run_id: str = "") -> Path:
    """Where a shadow run publishes its liveness (writer path).

    Per run id when one is given (`shadow_run_<run_id>.json`), so concurrent
    rehearsals never overwrite each other's dashboard state; the legacy shared
    name is kept only as a read fallback for heartbeats written by older code
    (readers: `dashboard/server.py`).
    """
    from core_brain.runtime_paths import runtime_file
    if run_id:
        return runtime_file(_run_heartbeat_name(run_id), root=root)
    return runtime_file(SHADOW_HEARTBEAT_NAME, root=root)


TOURNAMENT_DB_PATTERN = re.compile(
    r"^(?:.*[\\/])?(?P<issue>\d+)_tournament_(?P<index>\d+)_(?P<arm>[a-z0-9-]+)_(?P<stamp>\d{8}-\d{6})\.db$"
)


def parse_tournament_db_path(path: str | Path | None) -> dict | None:
    """Parse a tournament scratch DB filename into its constituent parts.

    Returns {'issue': int, 'index': int, 'arm': str, 'stamp': str} if matched,
    otherwise None. Specifically rejects non-tournament names and NN_shadow_*.db.
    """
    if not path:
        return None
    raw = str(path).strip()
    m = TOURNAMENT_DB_PATTERN.match(raw)
    if not m:
        return None
    try:
        return {
            "issue": int(m.group("issue")),
            "index": int(m.group("index")),
            "arm": m.group("arm"),
            "stamp": m.group("stamp"),
        }
    except (ValueError, TypeError):
        return None


def build_tournament_db_path(
    issue: int | str,
    index: int,
    arm: str,
    stamp: str,
    base_dir: str | Path = "data",
) -> Path:
    """Generate canonical tournament DB path data/<issue>_tournament_<idx>_<arm>_<stamp>.db."""
    clean_arm = re.sub(r"[^a-z0-9-]", "-", str(arm).lower()).strip("-")[:24] or "arm"
    idx_str = f"{int(index):02d}"
    filename = f"{issue}_tournament_{idx_str}_{clean_arm}_{stamp}.db"
    return Path(base_dir) / filename


def build_tournament_run_id(
    issue: int | str,
    index: int,
    arm: str,
    stamp: str,
) -> str:
    """Build unique run id: shadow-<issue>-t<idx>-<arm>-<stamp> (<= 64 chars, valid tokens)."""
    clean_arm = re.sub(r"[^a-z0-9-]", "-", str(arm).lower()).strip("-")[:24] or "arm"
    idx_str = f"{int(index):02d}"
    token = f"shadow-{issue}-t{idx_str}-{clean_arm}-{stamp}"
    return re.sub(r"[^A-Za-z0-9_.-]", "-", token).strip("-.")[:64]


def _process_start_time(pid: int) -> float | None:
    """Return an OS process creation time for PID-reuse-safe supervision."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class FileTime(ctypes.Structure):
                _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.GetProcessTimes.argtypes = [
                wintypes.HANDLE, ctypes.POINTER(FileTime), ctypes.POINTER(FileTime),
                ctypes.POINTER(FileTime), ctypes.POINTER(FileTime),
            ]
            kernel32.GetProcessTimes.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return None
            try:
                created = FileTime()
                exited = FileTime()
                kernel = FileTime()
                user = FileTime()
                if not kernel32.GetProcessTimes(
                    handle, ctypes.byref(created), ctypes.byref(exited),
                    ctypes.byref(kernel), ctypes.byref(user),
                ):
                    return None
                ticks = (created.high << 32) | created.low
                return ticks / 1e7 - 11644473600.0 if ticks else None
            finally:
                kernel32.CloseHandle(handle)

        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        fields = stat[stat.rindex(")") + 2:].split()
        start_ticks = float(fields[19])
        hz = os.sysconf("SC_CLK_TCK")
        with open("/proc/stat", encoding="utf-8") as stream:
            boot_time = next(float(line.split()[1]) for line in stream if line.startswith("btime "))
        return boot_time + start_ticks / hz
    except (OSError, ValueError, StopIteration, IndexError, AttributeError):
        return None


def write_shadow_heartbeat(
    *,
    db_path,
    run_id: str,
    minutes: float,
    interval: float,
    started_at: float,
    finished: bool = False,
    cycle: int = 0,
    dash_port: Optional[int] = None,
    code_revision: Optional[dict] = None,
    shutdown_reason: str = "",
    path: Optional[Path] = None,
) -> Optional[Path]:
    """Publish (or refresh) the rehearsal's heartbeat file.

    Best-effort by design: a rehearsal must never die because the dashboard's
    convenience file could not be written, so every failure degrades to a debug
    line and None.

    `code_revision` is the code this process loaded, read ONCE at start and
    passed in unchanged on every write. The dashboard reads it to tell a run
    holding today's tree from one still deciding with yesterday's -- the
    comparison a person used to have to make by lining process start times up
    against `git log` (`core_brain/code_revision.py`). It is passed rather than
    re-read here for that reason: a mid-run edit must not rewrite what a running
    rehearsal claims to have loaded.
    """
    target = Path(path) if path is not None else shadow_heartbeat_path(run_id=run_id)
    payload = {
        "pid": os.getpid(),
        "process_started_at": _process_start_time(os.getpid()),
        "run_id": run_id,
        "started_at": float(started_at),
        "minutes": float(minutes),
        "interval": float(interval),
        "db_path": str(Path(db_path).resolve()),
        "heartbeat_ts": time.time(),
        # Rotations completed so far. The dashboard divides the elapsed run by
        # this to get the cadence the loop REALLY runs at: `interval` is only
        # the sleep between rotations, and a rotation that spends two minutes
        # on public order-book reads makes the configured 5s a fiction to age
        # the heartbeat against.
        "cycle": int(cycle),
        "finished": bool(finished),
    }
    # Why the run ended: once / deadline / interrupt / lock_lost (#402).
    # Set on the finished write only; a crash leaves the field absent, which
    # keeps "crashed" distinguishable from "ended".
    if shutdown_reason:
        payload["shutdown_reason"] = str(shutdown_reason)
    if dash_port is not None:
        payload["dash_port"] = int(dash_port)
    if code_revision is not None:
        payload["code_revision"] = dict(code_revision)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(target)
    except OSError as e:
        log.debug("shadow heartbeat write failed: %s", e)
        return None
    return target


def shadow_cfg():
    """The rehearsal's config: whatever `load()` reads, grace included.

    This used to force `single_buy_grace_sec=0.0` unconditionally. That made
    the exit path unmeasurable on the only surface it is safe to measure on --
    every stranded leg was dumped on the first pass after its fill (0.5s, in
    `data/15_shadow_touchpair.db`), the operator's `HUNTER_SINGLE_BUY_GRACE_SEC`
    was discarded on the way in, and so the knob could never be swept.

    `load()` already defaults the field to 0.0, so an operator who sets nothing
    still gets the old baseline; only an explicit setting now reaches
    `_route_pair`.
    """
    from core_brain.config import load
    return load()


def shadow_run_id() -> str:
    """A fresh run_id for one rehearsal, never the live session's.

    `order_registry._resolve_run_id` reuses `runtime/.current_run_id` for 12
    hours so the fleet, the dashboard and the exec process tag one LIVE session
    identically. A rehearsal is not that session and must not borrow its name.
    Two shadow runs 3.85 hours apart both wrote as `run-2809a7161de1`: the
    baseline and the re-run landed in one bucket, and telling them apart meant
    spotting a gap in `posted_ts` by eye. A rehearsal that cannot be selected on
    its own has no numbers worth comparing.

    The `shadow-` prefix is deliberate and load-bearing. Live ids are `run-`,
    so a rehearsal can never be read as a live session in the dashboard's run
    selector -- which matters most for exactly the number a rehearsal produces
    most often, a zero fill count.

    `SH_RUN_ID` still wins, the one case where sharing an id is the point: an
    operator continuing a single rehearsal across a restart.
    """
    override = os.environ.get("SH_RUN_ID")
    if override:
        return override
    return f"shadow-{uuid.uuid4().hex[:12]}"


class _Deadline(KeyboardInterrupt):
    """Raised from the shadow `sleep_fn` when the time box expires.

    Subclasses KeyboardInterrupt deliberately. `core_brain/trader_loop.py`
    wraps its sleep call in `except KeyboardInterrupt: break` and nothing else,
    and that break is the loop's designed clean exit -- it returns the last
    rotation's results. A plain Exception would propagate out of `run` uncaught
    and lose them; a bare BaseException subclass would not be caught by that
    handler at all and would escape the same way.
    """


class _FinishLine(KeyboardInterrupt):
    """Raised from the shadow loop when the target action quota / finish line is reached."""


def make_deadline_sleep(
    deadline_ts: float,
    clock: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> Callable[[float], None]:
    """A `sleep_fn` for `trader_loop.run` that ends the run at `deadline_ts`.

    Raises `_Deadline` once the clock is at or past the deadline. Otherwise
    sleeps, clamped to the time actually remaining, so a long rotation interval
    cannot overshoot a short time box.

    `clock` and `sleep` are injected so the time box is testable without
    spending the wall-clock time it measures.
    """
    def sleep_fn(seconds: float) -> None:
        remaining = deadline_ts - clock()
        if remaining <= 0:
            raise _Deadline()
        sleep(max(0.0, min(seconds, remaining)))

    return sleep_fn


@dataclass
class ShadowIntent:
    """A decided intent, stamped with the market it belongs to.

    `QuoteIntent` has no condition_id (it travels beside the market object in
    the live loop); a recorded intent outlives that pairing, so the recorder
    attaches it.
    """
    condition_id: str
    side: str
    token_id: str
    price: float
    size: int
    mid: float = 0.0
    edge_vs_mid: float = 0.0
    reason: str = ""
    crossed: bool = False

    @classmethod
    def from_quote(cls, qi: Any, condition_id: str) -> "ShadowIntent":
        return cls(
            condition_id=condition_id,
            side=qi.side,
            token_id=qi.token_id,
            price=qi.price,
            size=qi.size,
            mid=getattr(qi, "mid", 0.0),
            edge_vs_mid=getattr(qi, "edge_vs_mid", 0.0),
            reason=getattr(qi, "reason", ""),
            crossed=bool(getattr(qi, "crossed", False)),
        )


@dataclass
class ShadowResult:
    """What a shadow session produced. Rehearsal numbers, not results."""
    results: list = field(default_factory=list)
    intents: list = field(default_factory=list)
    skipped_stages: tuple = ()


def _run_ring_name(run_id: str) -> str:
    """File name for a run-scoped ring: `shadow-<token>.jsonl`.

    The id is not ours to trust. `_resolve_run_id` takes `SH_RUN_ID` from the
    environment verbatim (order_registry.py:61-74), so anything the operator
    exports lands here -- including a separator, which would put the ring
    outside `runtime/`, or a dot pair, which would climb out of it. Every
    character outside `[A-Za-z0-9_.-]` becomes a dash, leading and trailing
    dots and dashes go, and the result is capped.

    Ids already carrying the `shadow-` prefix are not prefixed twice:
    `shadow_run_id()` returns `shadow-<hex>`, which would otherwise name the
    file `shadow-shadow-<hex>.jsonl`.
    """
    token = re.sub(r"[^A-Za-z0-9_.-]", "-", run_id).strip("-.")[:64] or "unnamed"
    if token.startswith("shadow-"):
        return f"{token}.jsonl"
    return f"shadow-{token}.jsonl"


def _make_logging_emit(
    db_path,
    inventory_lookup: Optional[Callable[[str], Any]] = None,
    run_id: Optional[str] = None,
) -> Callable[..., None]:
    """`emit`, plus one INFO line per market visit.

    A shadow run exists to be watched, and before this the only thing it wrote
    to the terminal was the banner and, ten minutes later, the summary. Every
    decision went to the ring file and the store, where reading it means
    querying SQLite beside a running loop.

    Only the `quoting/decide` event logs: that is the one event per market visit
    that carries both the intent count and the rationale. Logging every phase
    would bury the decisions in rotation bookkeeping. Telemetry is unchanged --
    the wrapper calls `emit` first and adds to it, never in place of it.

    `inventory_lookup` resolves the decide event's `condition_id` to the
    `Inventory` the seam computed for that market. It has to be a lookup
    rather than shares carried on the event itself: `trader_loop.py` emits
    `quoting/decide` with only `intent_count` and `condition_id` in `extra`
    (see `evaluate_market_quote`), and that module does not change for a
    rehearsal feature. `build_shadow_seam` is the caller that has a position
    to offer -- it passes a lookup backed by the same cache
    `settling_inventory_fn` fills immediately before each decision. The
    lookup must stay total: a `condition_id` it has never seen (no lookup
    wired, or a market this seam never settled) logs the line without a
    position rather than raising or printing zeros nothing measured.

    A rehearsal names its own ring. With a `run_id`, every event goes to
    `runtime/shadow-<run_id>.jsonl` and rotation is switched off for that
    file: a 45-minute session emits ~2000 lines against the live ring's
    500-line cap, and the whole point of the rehearsal record is that its
    first minute is still on disk when the last one lands. The per-run file
    has exactly one writer, so it needs none of the live ring's rotation --
    there, many writers share one small file, which is why rotation exists.

    Without a `run_id` nothing changes: events fall through to the default
    ring with default rotation, exactly as before this existed.

    `emit` is imported per call, not at wiring time -- the same convention
    `_default_fetch_books` states below -- so what the wrapper calls is
    whatever `cycle_stream.emit` names when the event happens.
    """
    from core_brain.cycle_stream import LIVE_ROOT

    ring_path = (LIVE_ROOT / "runtime" / _run_ring_name(run_id)) if run_id else None

    def emit_fn(cycle, phase, action, **kw) -> None:
        from core_brain.cycle_stream import emit as _emit_cycle_event

        if ring_path is not None:
            # Assigned, not defaulted: a caller that passed its own ring_path
            # here would put the rehearsal back in someone else's file, and
            # can_rotate=True would reintroduce the eviction this exists to
            # stop. With a run_id, the run-scoped ring is not negotiable.
            kw["ring_path"] = ring_path
            kw["can_rotate"] = False
        _emit_cycle_event(cycle, phase, action, db_path=db_path,
                          run_id=run_id, **kw)

        if phase == "quoting" and action == "discard":
            extra = kw.get("extra") or {}
            reason = (kw.get("reason") or "").strip()
            # Only UMA-gate discards carry uma_status / uma_resolution_*
            # reasons; a future non-UMA discard falls through to the decide
            # telemetry below instead of being mislabeled.
            if extra.get("uma_status") is None and not reason.startswith(
                    "uma_resolution_"):
                pass
            else:
                status_value = extra.get("uma_status") or "?"
                try:
                    cancelled = int(extra.get("cancelled", 0) or 0)
                except (TypeError, ValueError):
                    cancelled = "?"
                log.info(
                    "cycle=%s %s UMA_DISCARD status=%s reason=%s cancelled=%s",
                    cycle,
                    kw.get("market_slug") or extra.get("condition_id") or "?",
                    status_value, reason, cancelled,
                )
                return

        if phase != "quoting" or action != "decide":
            return

        extra = kw.get("extra") or {}
        # The telemetry call above protects itself from a malformed event and
        # returns; repeating the conversion here unprotected would let a
        # non-numeric count end the rehearsal after telemetry had already been
        # handled. An unknown count is worth printing, not worth a crash.
        try:
            count = int(extra.get("intent_count", 0) or 0)
        except (TypeError, ValueError):
            count = "?"
        reason = (kw.get("reason") or "").strip()

        shares = ""
        inv = inventory_lookup(extra.get("condition_id")) if inventory_lookup else None
        if inv is not None:
            shares = f" up={inv.up_shares:g} down={inv.down_shares:g}"

        log.info(
            "cycle=%s %s intents=%s%s%s",
            cycle,
            kw.get("market_slug") or extra.get("condition_id") or "?",
            count,
            shares,
            f" -- {reason}" if reason else "",
        )

    # Introspectable for tests and callers: which ring this session writes,
    # or None when events fall through to the default ring unchanged.
    emit_fn.ring_path = ring_path

    return emit_fn


def build_shadow_seam(
    *,
    db_path,
    client_fn: Optional[Callable[[], Any]] = None,
    intents_sink: Optional[list] = None,
    decide_fn: Optional[Callable] = None,
    fetch_market: Optional[Callable] = None,
    fetch_books: Optional[Callable] = None,
    traded_fn: Optional[Callable[[str, set], dict]] = None,
    flow_fn: Optional[Callable[[str, float], Any]] = None,
    fetch_uma_status: Optional[Callable[[str], Any]] = None,
    record_market_event: Optional[Callable[[Any], None]] = None,
    cfg=None,
    registry=None,
    run_id: Optional[str] = None,
    paired_depth_arm: Optional[str] = None,
    paired_depth_cutoff_usd: Optional[float] = None,
    paired_admission_arm: Optional[str] = None,
    markets_fn_empty_is_routine: bool = False,
):
    """The seam a shadow run rotates over: live reads, recorded writes.

    Identical to the live seam except at exactly two ports:

    - `client` comes from `shadow_guard.shadow_client` (no signer, denying
      proxy) unless `client_fn` injects one -- and an injected client is
      wrapped in the same proxy, so the seam never holds a raw client.
    - `submit_fn`/`cancel_fn` are recorders against the shadow store rather
      than the venue. Submit stamps each decided intent with its condition id
      into `intents_sink` AND rests it as an order row (with a quote row and a
      modelled queue position) through `shadow_exec.record_submit`; cancel
      marks the named rows `cancelled` and returns how many it handled, which
      in a rehearsal is always all of them.

    One port this seam will NOT choose for a caller: `flow_fn`, the queue-clear
    gate's tape reader (#393). It stays `None` -- the gate inert, exactly as
    `VenueSeam` documents -- unless a caller wires the live reader
    (`_default_flow_fn`). That is deliberately not the `traded_fn` treatment, and
    the reason is whose read it is: a caller that substituted its other sources
    must not be handed a live one by surprise.
    `scripts/ladder_shadow_rehearsal.py` replays a RECORDED tape for both books
    and fills, states that its only network is the resolution read, and rotates
    at `--interval 0.01` -- defaulting this to the live tape would put public
    reads per market per rotation into a recorded experiment and measure today's
    tape against a previous session's book.

    Left unset, that inertness is NAMED rather than silent (see the warning
    below) -- a rehearsal that cannot exercise the gate must not look like one
    that did and found nothing.

    `reconcile_fn` is a no-op on purpose -- it reconciles against venue
    positions a shadow run does not have. Callers must report it as
    deliberately skipped (`ShadowResult.skipped_stages`), never as a stage
    that silently passed.

    `sweep_fn` is a no-op ONLY at this layer: this function has no `cfg`
    strong enough to build the pairs pass against (`cfg` here may be `None`,
    resolved later), so it wires `noop_sweep` and leaves the real port to the
    caller. `run_shadow` is that caller -- it overwrites `seam.sweep_fn` with
    `shadow_sweep`, which runs `single_buy_saver.auto_manage_pairs` against
    this same store every rotation. A test or script that calls
    `build_shadow_seam` directly, bypassing `run_shadow`, gets the no-op and
    must account for that itself.

    Without an injected `fetch_books` the session rehearses decisions against
    empty books and says so in the log; the entrypoint wires the real book
    source. Degrade, do not stop.
    """
    from core_brain.order_registry import OrderRegistry, init_db
    from core_brain.quotes import decide_quotes
    from core_brain.shadow_exec import (
        ensure_shadow_tables, record_submit, settle_market,
    )
    from core_brain.shadow_guard import (
        assert_not_production_registry,
        shadow_client,
    )
    from core_brain.trader_loop import VenueSeam, _make_inventory_fn, _make_open_orders_fn

    if not db_path:
        raise ValueError("shadow runs require an explicit per-run db_path")
    assert_not_production_registry(db_path)
    ensure_shadow_tables(db_path)
    if paired_depth_arm is not None:
        if paired_depth_arm not in {"control", "treatment"}:
            raise ValueError("paired-depth arm must be control or treatment")
        if paired_depth_cutoff_usd is None or paired_depth_cutoff_usd <= 0:
            raise ValueError("paired-depth run requires its positive depth cutoff")
        from core_brain.paired_shadow import ensure_paired_shadow_tables
        ensure_paired_shadow_tables(db_path)
    if paired_admission_arm is not None:
        if paired_depth_arm is not None:
            raise ValueError("paired depth and admission arms are mutually exclusive")
        if paired_admission_arm not in {"control", "treatment"}:
            raise ValueError("paired-admission arm must be control or treatment")
        # Admission varies identity, not a dollar bar: no cutoff exists.
        from core_brain.paired_shadow import ensure_paired_shadow_tables
        ensure_paired_shadow_tables(db_path)

    if intents_sink is None:
        intents_sink = []
    if registry is None:
        init_db(db_path)
        registry = OrderRegistry(db_path=Path(db_path), run_id=run_id)

    from core_brain.shadow_guard import ReadOnlyVenue

    client = client_fn() if client_fn else shadow_client()
    # An injected client is an unwrapped object by definition -- tests hand in
    # plain stubs. Whatever arrives leaves through the deny-by-default proxy,
    # so no wiring path can put a raw signing-capable client on the seam.
    if not isinstance(client, ReadOnlyVenue):
        client = ReadOnlyVenue(client)
    decide = decide_fn or decide_quotes

    empty_book = {"bids": {}, "asks": {}}

    def shadow_fetch_books(clob_host, token_id):
        if fetch_books is None:
            log.warning(
                "no book source wired: deciding %s... against an empty book",
                str(token_id)[:12])
            return dict(empty_book)
        return fetch_books(clob_host, token_id)

    # One resolved host for every read this seam makes. Read once here rather
    # than at each call site: `record_submit` reads the book to model queue
    # position, and a hard-coded default there would have that one read talk to
    # a different venue than everything else when CLOB_HOST is set.
    clob_host = os.environ.get("CLOB_HOST", "https://clob.polymarket.com")

    if flow_fn is None:
        # Named, not silent. `python -m core_brain.shadow_run` wires the live
        # reader in `main()`, so this fires for a caller that built its own
        # seam -- a recorded-tape trial, a unit test -- where the gate is inert.
        # A rehearsal that cannot measure a queue and one that measured every
        # queue and refused nothing must not read the same afterwards.
        log.warning(
            "queue-clear gate inert in this rehearsal: no flow port wired, so "
            "no market's queue is measured. Pass flow_fn to measure it.")

    resolved_traded_fn = traded_fn or _default_traded_fn()
    seen_by_market: dict[str, set] = {}
    base_inventory_fn = _make_inventory_fn(registry, Path(db_path))

    # What the log line reads its `up=`/`down=` counts from. Keyed by
    # condition_id, refreshed every time a market is settled below -- the
    # decide event that follows always carries the condition_id, never the
    # shares themselves (`trader_loop.py` is off limits), so the log line
    # closes over this same dict rather than being handed a position.
    last_inventory_by_market: dict[str, Any] = {}

    def settling_inventory_fn(market):
        """Settle this market, then report what it now holds.

        Order matters: a decision taken against a pre-settle inventory is a
        decision taken against a position the market has already left.
        """
        seen = seen_by_market.setdefault(market.condition_id, set())
        try:
            # `shadow_fetch_books` is the same source the rest of this seam
            # decides against, so an injected book drives the telemetry too.
            # Recording is opt-in at this one argument: without it a rehearsal
            # behaves exactly as before.
            settle_market(registry, market, db_path=db_path,
                          traded_fn=resolved_traded_fn, seen=seen,
                          book_fn=shadow_fetch_books, clob_host=clob_host)
        except (sqlite3.Error, OSError, ValueError) as e:
            # Degrade, do not stop: an unsettled visit decides against a stale
            # inventory, which the next visit corrects. Raising here would take
            # the market out of the rotation entirely.
            log.warning("settle failed for %s: %s", market.condition_id[:12], e)
        inv = base_inventory_fn(market)
        last_inventory_by_market[market.condition_id] = inv
        return inv

    if paired_depth_arm is not None:
        registry.paired_context = {
            "db_path": Path(db_path), "run_id": run_id,
            "arm": paired_depth_arm,
            "cutoff_usd": float(paired_depth_cutoff_usd),
        }
    elif paired_admission_arm is not None:
        registry.paired_context = {
            "db_path": Path(db_path), "run_id": run_id,
            "arm": paired_admission_arm,
            "cutoff_usd": None,
            "trial_axis": "admission",
        }

    def shadow_submit(client, reg, market, intents, cfg_in) -> int:
        for qi in intents:
            intents_sink.append(ShadowIntent.from_quote(qi, market.condition_id))
        return record_submit(client, reg, market, intents, cfg_in,
                             db_path=db_path, book_fn=shadow_fetch_books,
                             clob_host=clob_host)

    def record_cancel(_client, reg, orders) -> int:
        """Mark each named row cancelled and report how many were handled.

        Mirrors `core_brain/trader_loop.py:_cancel_orders` minus the venue
        call: in a rehearsal there is no venue to reject a cancel, so a cancel
        always succeeds and the count returned always equals the number of
        orders handed over.

        Returning 0 instead -- which this did -- is not a harmless stub. The
        caller (`trader_loop._visit_one`) reads a short count as a possibly
        failed cancel and asks `_still_resting`; the shadow seam wires no
        `resting_order_ids_fn`, so that check takes its "unverifiable means
        unsafe" branch, reports every order as still resting, and raises. The
        market goes ERROR before `submit_fn` is ever reached, on every
        re-quote, and the order it wanted to replace stays `open` -- still
        collecting simulated fills at a price the live loop would have
        cancelled. Both cancel ports (`_visit_one` and
        `_cancel_dropped_markets`) call this same seam function with the same
        dict shape, so both are served here.

        An order whose row cannot be found still counts: a row that is no
        longer active was already filled or cancelled, so nothing rests, which
        is the outcome the caller is asking about. A row that IS found and
        cannot be written does not count -- the store still says it rests, and
        the caller must be allowed to refuse the replacement.
        """
        now_ms = int(time.time() * 1000)
        cancelled = 0
        for o in orders:
            v_id = o.get("order_id") or o.get("id")
            row_id = o.get("id")
            match = None
            for row in reg.get_active_orders():
                if row.order_id == v_id or (row_id and row.id == row_id):
                    match = row
                    break
            if match is None:
                cancelled += 1
                continue
            try:
                # The rehearsal is where the cancel evidence is gathered, so
                # the reason and queue position have to survive here too --
                # `plan_orders` attaches both to the order dict it hands over.
                reg.update_order_status(
                    match.id, status="cancelled", last_polled_ts=now_ms,
                    cancel_reason=o.get("cancel_reason"),
                    cancel_queue_ahead=o.get("cancel_queue_ahead"))
            except (KeyError, sqlite3.Error) as e:
                log.warning("shadow cancel of %s failed: %s", match.id, e)
                continue
            cancelled += 1
        return cancelled

    def noop_reconcile(*_a) -> None:
        return None

    def noop_sweep() -> None:
        return None

    # UMA re-check gate (#408): same cached-reader shape as live when the
    # caller wires it; absent ports = old behavior (the visit check skips
    # itself). record_cancel stays the cancel adapter. record_market_event
    # calls log_market_event() on the shadow registry (BLOCKED, reason +
    # status in details); absent = skip the write. This seam will NOT choose
    # the live reader for itself -- same as flow_fn: a caller that
    # substituted its other sources must not be handed a live Gamma read by
    # surprise. `run_shadow` passes both straight through; the entrypoint
    # (`main`) is what wires the real one.
    def shadow_record_market_event(record) -> None:
        if record_market_event is not None:
            record_market_event(record)
            return
        try:
            log_fn = getattr(registry, "log_market_event", None)
            if callable(log_fn):
                log_fn(record)
        except (sqlite3.Error, OSError) as e:
            log.warning("shadow uma market_events write failed: %s: %s",
                        type(e).__name__, e)

    return VenueSeam(
        client=client,
        registry=registry,
        base_cfg=cfg,
        clob_host=clob_host,
        fetch_market=fetch_market or _default_fetch_market(),
        fetch_books=shadow_fetch_books,
        decide=decide,
        submit_fn=shadow_submit,
        cancel_fn=record_cancel,
        reconcile_fn=noop_reconcile,
        sweep_fn=noop_sweep,
        inventory_fn=settling_inventory_fn,
        open_orders_fn=_make_open_orders_fn(registry),
        emit_fn=_make_logging_emit(
            db_path, inventory_lookup=last_inventory_by_market.get,
            run_id=getattr(registry, "run_id", None)),
        markets_fn_empty_is_routine=markets_fn_empty_is_routine,
        flow_fn=flow_fn,
        fetch_uma_status=fetch_uma_status,
        record_market_event=shadow_record_market_event,
    )


def run_shadow(
    *,
    minutes: float,
    db_path,
    markets_fn: Optional[Callable[..., list]] = None,
    client_fn: Optional[Callable[[], Any]] = None,
    decide_fn: Optional[Callable] = None,
    fetch_books: Optional[Callable] = None,
    flow_fn: Optional[Callable[[str, float], Any]] = None,
    fetch_uma_status: Optional[Callable[[str], Any]] = None,
    record_market_event: Optional[Callable[[Any], None]] = None,
    interval: float = 5.0,
    funder: Optional[str] = None,
    run_id: Optional[str] = None,
    sleep_fn: Optional[Callable[[float], None]] = None,
    cfg: Optional[MakerConfig] = None,
    markets_path: Optional[str] = None,
    paired_depth_arm: Optional[str] = None,
    paired_depth_cutoff_usd: Optional[float] = None,
    paired_admission_arm: Optional[str] = None,
    starting_bankroll_usd: Optional[float] = None,
    market_state_fn: Optional[Callable] = None,
    markets_fn_empty_is_routine: bool = False,
    dash_port: Optional[int] = None,
    code_revision: Optional[dict] = None,
    finish_line_trades: Optional[int] = None,
    finish_line_merges: Optional[int] = None,
    finish_line_reliable: bool = False,
) -> ShadowResult:
    """One shadow session: rotate until `minutes` elapse, record, spend nothing.

    Negative minutes means run until stopped; zero still means one immediate-
    deadline rotation. The guard runs before anything is constructed, so even a wrong `db_path`
    fails before a table exists. Config loads through the same
    `core_brain.config.load()` the live loop uses -- same gates, same caps --
    but the bankroll is always the fixed rehearsal value, never the live
    wallet: an explicit `starting_bankroll_usd` pins it, otherwise the config
    default (or its env override) stands.

    `fetch_books` and `flow_fn` are the two LIVE reads the seam will not choose
    for itself: `run_shadow` passes both straight through, and the entrypoint is
    what wires the real ones (`main`). A caller that supplies neither gets the
    degraded behaviour each port already names -- empty books, no queue
    measurement -- rather than a live read it did not ask for.

    `code_revision` travels the same way as `flow_fn`: the entrypoint reads it
    once (`core_brain.code_revision.read_code_revision`) and this passes it into
    every heartbeat write. A caller that supplies none publishes a heartbeat
    without it, and a reader then falls back to the process start time -- still
    enough to see that the run predates a later edit, just without the label.
    """
    from dataclasses import replace as dc_replace

    from core_brain.shadow_guard import assert_not_production_registry
    from core_brain.trader_loop import _fleet_state
    from core_brain.trader_loop import run as loop_run

    # Between argv and any database handle.
    if not db_path:
        raise ValueError("shadow runs require an explicit per-run db_path")
    assert_not_production_registry(db_path)

    # A rehearsal names itself, before a single row is written. Without this
    # the registry hands back whatever `runtime/.current_run_id` holds, which
    # is the LIVE session's id for 12 hours after it was published -- so two
    # rehearsals hours apart both wrote as `run-2809a7161de1` and the baseline
    # could only be separated from the re-run by spotting a gap in `posted_ts`.
    #
    # Carried on the REGISTRY, never installed process-wide. `set_run_id` would
    # have leaked in both directions: a second session starting while this one
    # is alive re-tags this one's later rows, and the id survives the return, so
    # a live order created afterwards in the same process is stamped
    # `shadow-...`. See `shadow_run_id` and `OrderRegistry._run_id`.
    run_id = run_id if run_id is not None else shadow_run_id()
    started_at = time.time()

    if paired_depth_arm is not None and paired_admission_arm is not None:
        raise ValueError("paired depth and admission arms are mutually exclusive")
    if paired_depth_arm is not None and (markets_path is None or not markets_path):
        raise ValueError("paired-depth run requires --markets-path")
    if paired_depth_arm is not None and starting_bankroll_usd is None:
        raise ValueError("paired-depth run requires an explicit equal starting bankroll")
    if paired_admission_arm is not None and (markets_path is None or not markets_path):
        raise ValueError("paired-admission run requires --markets-path")
    if paired_admission_arm is not None and starting_bankroll_usd is None:
        raise ValueError("paired-admission run requires an explicit equal starting bankroll")

    cfg_supplied = cfg is not None
    if cfg is None:
        cfg = shadow_cfg()
        # Every rehearsal starts from the same fixed bankroll: the config
        # default (or its env override). The live wallet is never read here.
        # `funder` stays keyword-compatible but no longer funds the bankroll.

    bankroll_source = "config"
    if starting_bankroll_usd is not None:
        if not math.isfinite(starting_bankroll_usd) or starting_bankroll_usd <= 0:
            raise ValueError(
                "starting bankroll must be finite and positive, "
                f"got: {starting_bankroll_usd}")
        cfg = dc_replace(cfg, bankroll_usd=float(starting_bankroll_usd))
        bankroll_source = "explicit override"
    elif cfg_supplied:
        # A caller that built its own cfg never went through `config.load()`,
        # so the bankroll environment did not set this value. Reporting
        # "environment" would misstate the one thing this line exists to say.
        bankroll_source = "caller-supplied config"
    elif (os.environ.get("SPREAD_HUNTER_BANKROLL") or "").strip() or \
            (os.environ.get("HUNTER_BANKROLL") or "").strip():
        bankroll_source = "environment"
    log.info("starting bankroll $%.2f (%s)", cfg.bankroll_usd, bankroll_source)

    # One line that retires "what did this rehearsal actually run under?".
    # The two offset knobs are env-overridable per run, and recovering what a
    # finished rehearsal had used cost a full verification pass over the
    # quotes ledger. Logged at INFO so the banner and any captured log carry
    # it without anyone having to ask.
    log.info(
        "effective config: reward_offset=%.4f price_risk_widen=%.4f "
        "min_reward_offset=%.4f max_completable_pair_cost=%.4f",
        cfg.reward_offset, cfg.price_risk_widen,
        cfg.min_reward_offset, cfg.max_completable_pair_cost,
    )

    resolved_markets_fn = markets_fn or _default_markets_fn()
    markets = resolved_markets_fn()
    markets_holder = [markets]

    def dynamic_markets_fn():
        current = resolved_markets_fn()
        markets_holder[0] = current
        if (paired_depth_arm is not None or paired_admission_arm is not None) and not current:
            from core_brain.paired_shadow import record_paired_feed_event
            record_paired_feed_event(
                db_path, run_id=run_id, kind="empty_feed",
                detail="paired feed refresh returned no selected markets")
        return current

    paired_meta = None
    paired_axis = "admission" if paired_admission_arm is not None else "depth"
    paired_arm = paired_admission_arm if paired_admission_arm is not None else paired_depth_arm
    if paired_arm is not None:
        from core_brain.market_feed import load_graduated_markets
        initial_rows = load_graduated_markets(path=markets_path, paired_arm=paired_arm)
        if not initial_rows:
            raise ValueError(f"paired-{paired_axis} feed arm is empty; refusing an unmeasurable run")
        snapshots = {gm.snapshot_id if paired_axis == "admission" else gm.paired_depth_snapshot_id
                     for gm in initial_rows}
        if len(snapshots) != 1:
            raise ValueError(f"paired-{paired_axis} initial feed must have one common snapshot")
        paired_meta = {"snapshot_id": next(iter(snapshots)), "cutoff_usd": None}
        if paired_axis == "depth":
            cutoffs = {gm.paired_depth_cutoff_usd for gm in initial_rows}
            if len(cutoffs) != 1:
                raise ValueError("paired-depth initial feed must have one common snapshot and cutoff")
            paired_meta["cutoff_usd"] = next(iter(cutoffs))
        if paired_axis == "depth":
            if paired_depth_cutoff_usd is not None and paired_meta["cutoff_usd"] != paired_depth_cutoff_usd:
                raise ValueError("paired-depth CLI cutoff does not match the feed")
        if any(not (gm.event_id or gm.event_slug) for gm in initial_rows):
            raise ValueError(f"paired-{paired_axis} feed contains a row with no stable Gamma event id or slug")
        if paired_axis == "depth":
            expected_cutoff = 500.0 if paired_depth_arm == "control" else 250.0
            if paired_meta["cutoff_usd"] != expected_cutoff:
                raise ValueError(
                    f"{paired_depth_arm} feed cutoff must be ${expected_cutoff:g}, "
                    f"got ${paired_meta['cutoff_usd']:g}")
            paired_depth_cutoff_usd = paired_meta["cutoff_usd"]

    intents_sink: list = []
    seam = build_shadow_seam(
        db_path=db_path,
        client_fn=client_fn,
        intents_sink=intents_sink,
        decide_fn=decide_fn,
        fetch_market=_lookup_fetch_market(lambda: markets_holder[0]),
        fetch_books=fetch_books,
        flow_fn=flow_fn,
        fetch_uma_status=fetch_uma_status,
        record_market_event=record_market_event,
        cfg=cfg,
        run_id=run_id,
        paired_depth_arm=paired_depth_arm,
        paired_depth_cutoff_usd=paired_depth_cutoff_usd,
        paired_admission_arm=paired_admission_arm,
        markets_fn_empty_is_routine=markets_fn_empty_is_routine,
    )
    if paired_depth_arm is not None or paired_admission_arm is not None:
        from core_brain.paired_shadow import (
            record_paired_equity_mark, record_paired_run_start,
        )
        bankroll = float(starting_bankroll_usd)
        if paired_admission_arm is not None:
            record_paired_run_start(
                db_path, run_id=run_id, arm=paired_admission_arm,
                cutoff_usd=None,
                starting_bankroll_usd=bankroll, started_at=started_at,
                planned_minutes=minutes, trial_axis="admission")
        else:
            record_paired_run_start(
                db_path, run_id=run_id, arm=paired_depth_arm,
                cutoff_usd=float(paired_depth_cutoff_usd),
                starting_bankroll_usd=bankroll, started_at=started_at,
                planned_minutes=minutes)
        # The per-run mark writer is wired only in shadow mode. Every inventory
        # read and public book read happens against this isolated store.
        seam.paired_mark_fn = lambda ts=None: record_paired_equity_mark(
            seam.registry, db_path, run_id=run_id,
            starting_bankroll_usd=bankroll, book_fn=seam.fetch_books,
            clob_host=seam.clob_host, ts=ts)
        initial_mark = seam.paired_mark_fn(ts=started_at)
        if not initial_mark["valid"]:
            log.warning("paired opening equity mark incomplete: %s",
                        ", ".join(initial_mark["missing_conditions"]))
    # Fleet aggregates recomputed per cycle off the SHADOW store, so the gates
    # see the same shape of numbers they would live.
    seam.fleet_state_fn = lambda r: _fleet_state(r, cfg)

    def shadow_sweep() -> None:
        """Run the production lifecycle policy against the isolated shadow store."""
        from core_brain.shadow_exec import (
            ShadowExecutionClient, record_shadow_merges, shadow_positions,
        )
        from core_brain.single_buy_saver import (
            SINGLE_LEG_QUIET_ACTIONS, manage_single_leg_positions,
        )
        from core_brain.market_lifecycle import resolved_condition_ids

        # Resolved conditions are skipped before books or simulated orders.
        cycle_resolved = resolved_condition_ids(seam.registry)
        exec_client = ShadowExecutionClient(
            seam.registry, db_path, book_fn=seam.fetch_books,
            clob_host=seam.clob_host,
            window_sec=getattr(cfg, "pairs_exit_window_sec", 900.0))
        state_fn = getattr(shadow_sweep, "_market_state_fn", None)
        try:
            for pr in manage_single_leg_positions(
                exec_client, seam.registry, cfg,
                venue_positions=shadow_positions(seam.registry, db_path),
                market_state_fn=state_fn,
                state_cache=getattr(shadow_sweep, "_state_cache", None),
                resolved_cids=cycle_resolved,
            ):
                action = pr.get("action", "?")
                pair_id = pr.get("pair_id") or "?"
                if action == "error":
                    log.warning("lifecycle %s error: %s", pair_id,
                                pr.get("error"))
                elif action not in SINGLE_LEG_QUIET_ACTIONS:
                    log.info("lifecycle %s %s", pair_id, action)
            for pair_id in record_shadow_merges(seam.registry, db_path):
                log.info("merged %s at $1.00 a share", pair_id)
        except (sqlite3.Error, OSError, ValueError) as e:
            log.warning("shadow lifecycle pass failed: %s", e)

        # Mature any adverse-selection horizon that has come due. Placed after
        # the pairs pass and before the resolution read so a fill booked this
        # rotation is already in the store when the next rotation samples it.
        sampled = sample_shadow_markouts(seam.registry, clob_host=seam.clob_host)
        if sampled:
            log.info("markouts: %d horizon(s) sampled", sampled)

        paired_mark = getattr(seam, "paired_mark_fn", None)
        if paired_mark is not None:
            try:
                mark = paired_mark()
                if not mark["valid"]:
                    log.warning("paired equity mark incomplete: %s",
                                ", ".join(mark["missing_conditions"]))
            except Exception as e:  # noqa: BLE001 - retain rehearsal, report missing mark
                log.warning("paired equity mark failed: %s: %s", type(e).__name__, e)

        # Confirm externally-ended markets and record the terminal marker.
        # Runs after the pairs pass so a market this sweep resolves is not
        # acted on by auto_manage_pairs in the same rotation. The gamma read
        # is the only thing in this sweep that can reach a network failure;
        # it is the last stage on purpose so a slow endpoint never blocks
        # the merge / exit decisions above.
        try:
            resolve_fn = getattr(shadow_sweep, "_resolve_fn", None)
            if resolve_fn is not None:
                resolve_fn()
        except Exception as e:  # noqa: BLE001 - degrade, do not stop
            log.warning("shadow resolution pass failed: %s", e)
        if paired_mark is not None:
            try:
                mark = paired_mark()
                if not mark["valid"]:
                    log.warning("paired post-resolution equity mark incomplete: %s",
                                ", ".join(mark["missing_conditions"]))
            except Exception as e:  # noqa: BLE001 - retain rehearsal, report missing mark
                log.warning("paired post-resolution mark failed: %s: %s",
                            type(e).__name__, e)

    # Snapshot-level coverage must be complete before a paired run can be
    # reported as a decision. A max-market cap or market lookup failure must
    # never make an unvisited selection disappear from the audit.
    paired_coverage = getattr(seam, "paired_coverage_fn", None)
    if paired_coverage is not None:
        paired_coverage()

    # Close the write-side gap: markets that ended on the venue but whose
    # resting rows + quotes_count keep the dashboard reading QUOTING. The
    # gamma read is public (no signer), so the shadow session can run it;
    # a failure degrades and retries next rotation, never marking a market
    # resolved on a guess.
    def resolve_markets_fn():
        from core_brain.market_resolution import (
            DEFAULT_GAMMA_HOST, sweep_market_resolutions,
        )
        host = os.environ.get("GAMMA_HOST", DEFAULT_GAMMA_HOST)
        # Shadow models the wallet, so settlement PnL belongs here: a
        # validation run must book the redemption, not leave it as unrealised
        # CAPEX. Live loops never set this -- on-chain redemption is theirs.
        # Suspect markets the stale feed retains are confirmed too (#402).
        for r in sweep_market_resolutions(
            seam.registry, db_path, markets=markets_holder[0],
            gamma_host=host, run_id=run_id, book_settlement=True,
            extra_candidates=set(shadow_suspects.get("cids", frozenset())),
        ):
            if r.action in ("resolved_recorded", "partial_stranded"):
                settle = f" settled {r.settled_shares:g}sh pnl=${r.settled_pnl:.2f}" \
                    if r.settled_pnl else ""
                log.info("resolved %s (%s): %s cancelled=%d partial=%d%s%s",
                         r.condition_id[:12], r.reason, r.action,
                         r.cancelled_rows, r.partial_rows,
                         f" winner={r.winning_token}" if r.winning_token else "",
                         settle)
            elif r.action == "still_open":
                log.debug("not resolved %s: %s", r.condition_id[:12], r.reason)
            elif r.action == "unreachable":
                log.debug("resolve read failed %s: %s", r.condition_id[:12], r.reason)

    shadow_sweep._resolve_fn = resolve_markets_fn  # type: ignore[attr-defined]
    # Suspect markets named by the Trader's visits, read by the sweep above
    # (#402). Replaced (never mutated) every rotation, so the sweep's read
    # needs no lock.
    shadow_suspects: dict = {}  # type: ignore[assignment]
    shutdown_box: dict = {}
    # The aged-out pass (#311) reads each leg's market state through this seam.
    # Left None it uses the public gamma read -- an injected fn keeps a test or
    # a rehearsal off the network, the same shape as `_resolve_fn` above.
    shadow_sweep._market_state_fn = market_state_fn  # type: ignore[attr-defined]
    # One market read per condition per TTL for the whole session, the same
    # reuse the live loop does: a rotation is seconds apart and every one of
    # those reads is synchronous against a deadline that moves in hours.
    from core_brain.single_buy_saver import AgedOutMarketStateCache
    shadow_sweep._state_cache = AgedOutMarketStateCache()  # type: ignore[attr-defined]
    seam.sweep_fn = shadow_sweep

    deadline_ts = started_at + max(0.0, minutes * 60.0)
    if sleep_fn is not None:
        resolved_sleep_fn = sleep_fn
    elif minutes < 0:
        resolved_sleep_fn = time.sleep
    else:
        resolved_sleep_fn = make_deadline_sleep(deadline_ts)

    heartbeat_kwargs = dict(db_path=db_path, run_id=run_id, minutes=minutes,
                            interval=interval, started_at=started_at,
                            dash_port=dash_port, code_revision=code_revision)
    write_shadow_heartbeat(**heartbeat_kwargs, cycle=0)

    rotations = 0

    def beating_sleep(seconds: float) -> None:
        """Refresh the heartbeat once per rotation, then sleep as before."""
        nonlocal rotations
        rotations += 1
        write_shadow_heartbeat(**heartbeat_kwargs, cycle=rotations)
        if finish_line_trades is not None or finish_line_merges is not None or finish_line_reliable:
            from core_brain.run_scorer import score_run
            s = score_run(db_path, run_id=run_id)
            if finish_line_reliable and s.is_reliable:
                log.warning("SHADOW RUN finish line reached: all reliability action thresholds met (%s)", s.confidence_tier)
                raise _FinishLine()
            if finish_line_trades is not None and s.total_closed_trades >= finish_line_trades:
                log.warning("SHADOW RUN finish line reached: target closed trades reached (%d/%d)", s.total_closed_trades, finish_line_trades)
                raise _FinishLine()
            if finish_line_merges is not None and s.actions["positions_merged"].observed >= finish_line_merges:
                log.warning("SHADOW RUN finish line reached: target merges reached (%d/%d)", s.actions["positions_merged"].observed, finish_line_merges)
                raise _FinishLine()
        resolved_sleep_fn(seconds)

    results = loop_run(
        seam,
        interval=interval,
        once=False,
        live=True,  # safe: submit/cancel are recorders, the client cannot sign
        markets=markets,
        markets_fn=dynamic_markets_fn,
        sleep_fn=beating_sleep,
        suspects_box=shadow_suspects,
        shutdown_box=shutdown_box,
    )
    # Only a clean end is marked finished. A crash leaves the heartbeat
    # unrefreshed, which the reader calls ended once it goes stale -- so the
    # two endings stay distinguishable.
    write_shadow_heartbeat(**heartbeat_kwargs, cycle=rotations, finished=True,
                           shutdown_reason=str(shutdown_box.get("reason", "")))
    if paired_depth_arm is not None:
        # Snapshot-level coverage must be audited before the run is marked
        # finished: a max-market visit cap, a market lookup failure, or an
        # early deadline must never make an unvisited selection disappear
        # from the report. Only this function holds the run's full selected
        # feed set (markets_holder), so only here can "selected but never
        # visited" be computed honestly.
        from core_brain.paired_shadow import record_paired_feed_event
        from core_brain.trader_loop import _cid
        try:
            selected_ids = {_cid(row) for row in (markets_holder[0] or [])}
            with closing(get_connection(Path(db_path))) as conn:
                visited_ids = {
                    str(row["condition_id"])
                    for row in conn.execute(
                        "SELECT DISTINCT condition_id FROM shadow_paired_admissions "
                        "WHERE run_id = ?", (run_id,))
                }
            unvisited = sorted(cid for cid in selected_ids - visited_ids if cid)
            if unvisited:
                record_paired_feed_event(
                    db_path, run_id=run_id, kind="unvisited_selected_markets",
                    detail=f"{len(unvisited)} selected market(s) not visited: "
                           f"{','.join(unvisited[:10])}")
        except (sqlite3.Error, OSError) as e:
            log.warning("paired coverage audit failed: %s", e)
        from core_brain.paired_shadow import record_paired_run_finish
        seam.paired_mark_fn(ts=time.time())
        record_paired_run_finish(db_path, run_id=run_id)
    return ShadowResult(
        results=results,
        intents=list(intents_sink),
        # Deliberately skipped, and reported as such -- reconcile compares
        # against venue positions a shadow run does not have. Never "passed
        # silently". `sweep` no longer belongs here: `shadow_sweep` above
        # wires it to run the single-buy pairs pass every rotation, and
        # listing a stage that ran as "skipped" is the same lie in the other
        # direction -- reporting something that happened as something that
        # did not.
        skipped_stages=("reconcile",),
    )


def sample_shadow_markouts(
    registry,
    clob_host: str = "https://clob.polymarket.com",
) -> int:
    """Mature whatever adverse-selection horizons have come due, once.

    `core_brain.markout.MarkoutWorker` is a daemon thread started only by
    `core_brain.order_manager` on the live poll path, so a rehearsal has no
    sampler at all: rows would be opened on fill and then sit at NULL forever.
    This is the rehearsal's equivalent, called from the per-rotation sweep
    rather than from a thread -- the sweep already runs on the session's own
    cadence, and one more thread in a rehearsal buys nothing.

    Cheap when idle: `get_pending_markouts` is a single indexed read that
    returns nothing until a horizon is actually due, and the run's fills are
    counted in single digits.

    Reads only -- the public book and the public tape, no signer and no
    credentials, the same endpoints this session already decides against.

    Returns the number of horizons filled. Never raises: telemetry that fails
    must not end a rehearsal, so every error degrades to 0 and a warning.
    """
    from core_brain import markout as markout_mod

    try:
        return markout_mod.sample_pending_markouts(registry, clob_host=clob_host)
    except Exception as e:  # noqa: BLE001 - degrade, never stop the sweep
        log.warning("shadow markout sampling failed: %s", e)
        return 0


def _default_markets_fn() -> Callable[..., list]:
    """The graduated market list, exactly as the live loop resolves it."""
    from core_brain.trader_loop import _market_specs
    return _market_specs


def _default_fetch_books() -> Callable[[str, str], dict]:
    """The live loop's real book source: `GET {clob_host}/book`.

    Public endpoint, no key and no API credentials, and it does not travel
    through the CLOB client the deny-by-default proxy guards -- so wiring it
    here spends nothing and loads nothing that could sign. Without it the
    session decides every market against an empty book, which is a rehearsal of
    a venue that does not exist.

    `full_book` is imported per call so the entrypoint reads the module
    attribute at call time rather than binding it at wiring time.
    """
    def fetch(clob_host: str, token_id: str) -> dict:
        from core_brain.markets import full_book
        return full_book(clob_host, token_id)

    return fetch


def _default_traded_fn() -> Callable[[str, set], dict]:
    """The live tape reader. Public endpoint, no credentials, no signer."""
    def traded(condition_id: str, seen: set) -> dict:
        from core_brain.markets import recent_trades
        return recent_trades(condition_id, seen)

    return traded


def _default_flow_fn() -> Callable[[str, float], Any]:
    """The queue-clear gate's tape reader: taker SELL flow, public endpoint.

    Same safety class as `_default_traded_fn` above, and for the same reason:
    the trades endpoint needs no key and no API credentials, and this read does
    not travel through the CLOB client the deny-by-default proxy guards -- so a
    rehearsal that measures a queue spends nothing and loads nothing it could
    sign with.

    Lazy at the gate: `trader_loop._admit_placements` calls this only for a new
    PASSIVE placement that has shares resting ahead of it at its own price, so
    a rehearsal over a clear book pays no round-trip at all. An unreachable
    tape is not a rehearsal failure -- `markets.recent_sell_flow` returns
    `unavailable` and the gate fails OPEN, naming the skip instead of refusing
    a placement on a read that never happened.

    The same reader `trader_loop.main` wires on the live seam, for the same
    reason a rehearsal exists at all: the loop that is rehearsed has to be the
    loop that ships. If those two ever name different readers, the rehearsal is
    measuring a gate nobody runs.
    """
    def flow(condition_id: str, window_sec: float):
        from core_brain.markets import recent_sell_flow
        return recent_sell_flow(condition_id, window_sec)

    return flow


def _default_uma_status_fn() -> Callable[[str], Any]:
    """The UMA re-check gate's Gamma reader: public endpoint, no key (#408).

    Same safety class as `_default_flow_fn` above: Gamma needs no key and no
    API credentials, and this read does not travel through the CLOB client
    the deny-by-default proxy guards. Lazy per visit through the 30s TTL
    cache, flagged pinned, unreachable uncached -- and unreachable fails
    OPEN, warning and continuing the visit rather than cancelling.

    The same cached-reader shape `trader_loop.main` wires on the live seam:
    the loop that is rehearsed has to be the loop that ships.
    """
    from core_brain.trader_loop import make_uma_status_reader
    return make_uma_status_reader(
        gamma_host=os.environ.get(
            "GAMMA_HOST", "https://gamma-api.polymarket.com"))


def _default_fetch_market() -> Callable[[str], Any]:
    """The live loop's real market resolver (network)."""
    from core_brain.trader_loop import _fetch_market
    return _fetch_market


def _lookup_fetch_market(markets_source: Any) -> Callable[[str], Any]:
    """Resolve a cid first against the session's own market objects.

    A session handed fully-formed market objects (tests, or a caller that
    resolved markets itself) must not re-fetch them. Anything not in the
    session's list or lacking tradeable tokens falls through to the live
    loop's real resolver.
    """
    from core_brain.trader_loop import _cid, _fetch_market

    def fetch(cid: str):
        mkts = markets_source() if callable(markets_source) else markets_source
        by_cid = {_cid(m): m for m in (mkts or [])}
        m = by_cid.get(cid)
        if m is not None:
            up = getattr(m, "up_token", None) or (m.get("up_token") if isinstance(m, dict) else None)
            dn = getattr(m, "down_token", None) or (m.get("down_token") if isinstance(m, dict) else None)
            if up and dn:
                return m
        return _fetch_market(cid)

    return fetch


# --- entrypoint ---------------------------------------------------------------


def _parse_args(argv: Optional[list[str]] = None):
    ap = argparse.ArgumentParser(
        description="SHADOW fleet: the full loop against the live book, "
                    "spending nothing. No signer is loaded.")
    ap.add_argument("--minutes", type=float, default=5.0,
                    help="time box in minutes (default: 5.0; negative runs until stopped)")
    ap.add_argument("--interval", type=float, default=5.0,
                    help="rotation cadence in seconds (default: 5.0)")
    ap.add_argument("--db", required=True,
                    help="required per-run shadow store path; data/orders.db is refused")
    ap.add_argument("--run-id", default=None,
                    help="run id stamped on every row (e.g. shadow-01); matches the "
                         "statistics observer's --run-id so records line up across "
                         "the db, stats store and report. Defaults to shadow_run_id()")
    ap.add_argument("--max-markets", type=int, default=None,
                    help="cap the number of markets rotated (default: all)")
    ap.add_argument("--markets-path", default=None,
                    help="read the universe from this feed file instead of the "
                         "default runtime/markets.json (a trial run's own "
                         "feed, e.g. runtime/trials/shadow-03/markets.json). "
                         "An injected markets_fn still wins.")
    ap.add_argument("--paired-depth-arm", choices=("control", "treatment"),
                    default=None,
                    help="read this arm from --markets-path as an atomic paired-depth bundle")
    ap.add_argument("--paired-admission-arm", choices=("control", "treatment"),
                    default=None,
                    help="read this arm from --markets-path as an atomic paired-admission bundle")
    ap.add_argument("--paired-depth-cutoff-usd", type=float, default=None,
                    help="expected cutoff in this paired bundle (must match its arm metadata)")
    ap.add_argument("--starting-bankroll-usd", "--paired-starting-bankroll-usd",
                    type=float, default=None,
                    help="fixed starting bankroll for this rehearsal "
                         "(default: config bankroll ($100) or SPREAD_HUNTER_BANKROLL; "
                         "paired arms default to $100)")
    ap.add_argument("--funder", default=None,
                    help="kept for compatibility; does not set the shadow bankroll")
    ap.add_argument("--dash-port", type=int, default=None,
                    help="metadata port of the dashboard monitoring this shadow run")
    ap.add_argument("--finish-line-trades", "--target-trades", type=int, default=None,
                    help="stop rehearsal early when total closed trades reaches this count")
    ap.add_argument("--finish-line-merges", "--target-merges", type=int, default=None,
                    help="stop rehearsal early when total merged pairs reaches this count")
    ap.add_argument("--finish-line-reliable", "--target-reliable", action="store_true", default=False,
                    help="stop rehearsal early as soon as all reliability action thresholds are satisfied")
    return ap.parse_args(argv)


def main(
    argv: Optional[list[str]] = None,
    *,
    markets_fn: Optional[Callable[..., list]] = None,
    client_fn: Optional[Callable[[], Any]] = None,
    decide_fn: Optional[Callable] = None,
    fetch_books: Optional[Callable] = None,
    flow_fn: Optional[Callable[[str, float], Any]] = None,
    fetch_uma_status: Optional[Callable[[str], Any]] = None,
) -> int:
    """The shadow entrypoint: argparse, banner, time box, clean exit code."""
    # Declared before anything reads config. This process builds a
    # credential-free client behind a deny-by-default proxy, so it cannot
    # place an order whatever sits in `.env` -- which is what licenses the
    # rehearsal-only trial knobs to apply here and nowhere else.
    rehearsal.declare_rehearsal()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stderr)],
    )

    a = _parse_args(argv)

    # The code THIS process is holding, read once. It goes on the banner and
    # into every heartbeat write, so a run that has outlived its code says so
    # instead of looking like the three identical rehearsals on 2026-10-07 --
    # two of them deciding with the previous morning's ranker, none of them
    # saying a word about it.
    code_revision_record = read_code_revision()

    if a.dash_port is not None and not (1 <= a.dash_port <= 65535):
        raise SystemExit("--dash-port must be between 1 and 65535")

    db = Path(a.db)
    # Feed precedence: an injected markets_fn wins, then --markets-path, then
    # the pathless default -- so a trial run reads only its own feed while
    # every existing caller resolves exactly as before.
    from core_brain.trader_loop import _market_specs
    if a.paired_depth_arm is not None and a.markets_path is None:
        raise SystemExit("--paired-depth-arm requires --markets-path")
    if a.paired_admission_arm is not None and a.markets_path is None:
        raise SystemExit("--paired-admission-arm requires --markets-path")
    if a.paired_depth_arm is not None and a.paired_admission_arm is not None:
        raise SystemExit("paired depth and admission arms are mutually exclusive")
    if (a.paired_admission_arm is not None
            and a.paired_depth_cutoff_usd is not None):
        raise SystemExit("paired-admission runs carry no dollar cutoff")
    if markets_fn is not None:
        resolved_markets_fn = markets_fn
    elif a.markets_path is not None:
        trial_feed = a.markets_path
        paired_arm = (a.paired_depth_arm if a.paired_depth_arm is not None
                      else a.paired_admission_arm)
        if paired_arm is None:
            resolved_markets_fn = (lambda cap=None: _market_specs(
                cap if cap is not None else a.max_markets, path=trial_feed))
        else:
            resolved_markets_fn = (lambda cap=None: _market_specs(
                cap if cap is not None else a.max_markets, path=trial_feed,
                paired_arm=paired_arm))
    else:
        resolved_markets_fn = (lambda max_markets=None:
                               _default_markets_fn()(a.max_markets))
    log.warning(
        "SHADOW RUN starting: mode=shadow minutes=%s interval=%ss store=%s "
        "max_markets=%s markets_path=%s code=%s -- NO SIGNER LOADED: this process cannot place, cancel "
        "or merge anything. Numbers below are rehearsal, not results.",
        a.minutes, a.interval, db, a.max_markets,
        a.markets_path if a.markets_path is not None else "default",
        revision_label(code_revision_record))

    if a.starting_bankroll_usd is not None:
        starting_bankroll = a.starting_bankroll_usd
    elif a.paired_depth_arm is not None or a.paired_admission_arm is not None:
        from core_brain.config import MakerConfig
        starting_bankroll = MakerConfig.__dataclass_fields__["bankroll_usd"].default
    else:
        starting_bankroll = None

    result = run_shadow(
        minutes=a.minutes,
        db_path=db,
        markets_fn=resolved_markets_fn,
        client_fn=client_fn,
        decide_fn=decide_fn,
        fetch_books=fetch_books or _default_fetch_books(),
        # The queue-clear gate's tape reader, wired here with the other live
        # sources -- this is the rehearsal an operator launches, so it is the
        # one that must actually exercise the gate. It reads the public trades
        # endpoint only, never the CLOB client the proxy guards, and the gate
        # calls it lazily: a market with a clear front pays nothing.
        flow_fn=flow_fn or _default_flow_fn(),
        # The UMA re-check gate's Gamma reader, wired here with the other
        # live sources -- this is the rehearsal an operator launches, so it
        # is the one that must actually exercise the gate. Public Gamma only,
        # no signer, lazy per visit through the 30s TTL cache.
        fetch_uma_status=fetch_uma_status or _default_uma_status_fn(),
        interval=a.interval,
        funder=a.funder,
        run_id=a.run_id,
        markets_path=a.markets_path,
        paired_depth_arm=a.paired_depth_arm,
        paired_depth_cutoff_usd=a.paired_depth_cutoff_usd,
        paired_admission_arm=a.paired_admission_arm,
        starting_bankroll_usd=starting_bankroll,
        dash_port=a.dash_port,
        code_revision=code_revision_record,
        finish_line_trades=a.finish_line_trades,
        finish_line_merges=a.finish_line_merges,
        finish_line_reliable=a.finish_line_reliable,
    )

    quoted = sum(1 for r in result.results if r.status == "QUOTED")
    declined = sum(1 for r in result.results if r.status == "DECLINED")
    errors = sum(1 for r in result.results if r.status == "ERROR")
    log.warning(
        "SHADOW RUN finished: rotations_returned=%d quoted=%d declined=%d "
        "errors=%d intents_recorded=%d skipped=%s code=%s",
        len(result.results), quoted, declined, errors,
        len(result.intents), ",".join(result.skipped_stages),
        revision_label(code_revision_record))

    # The queue-clear gate (#393) reports on the run's own line, not only in the
    # per-rotation log: this is the rehearsal that the bar gets picked from, and
    # "measured" and "could not be measured" are the two readings that must not
    # be confused. A skip means the tape was unreachable or page-bounded, so the
    # run learned nothing about that market's queue -- not that it was clear.
    reported = [r for r in result.results if getattr(r, "queue_why", "")]
    unmeasurable = [r for r in reported
                    if str(r.queue_why).startswith("queue gate skipped:")]
    log.warning(
        "SHADOW RUN queue gate: reported=%d measured=%d unmeasurable=%d",
        len(reported), len(reported) - len(unmeasurable), len(unmeasurable))
    return 0 if result.results else 1


if __name__ == "__main__":
    sys.exit(main())
