"""Single-market live execution monitor (:8799).

Watched during ONE supervised live cycle or unattended operation.
Lifts proven UI components from the paper-run dashboards:
- Level 1: Run-level Strategy KPI tile grid, tooltips, and bell curves from `server/spread_dash_html.py:1525-1567`
- Level 2: Selection funnel (RAW -> FILTERS -> FINAL -> GRADUATED) & refusal cards from `server/fleet_dash.py:1106-1180`
- Level 2: Market drill-down (quotes vs mid, 4 markout horizons, skip events, settlements) from `server/spread_dash.py:598`
- Level 3: Mechanics & system health (latency, reconcile lag, venue errors, 3-way divergences) from `server/spread_dash_html.py:1572`
- Req 4: Exposure over time (unrealized, committed, naked USD) from `server/spread_dash_html.py:1505-1509`
- Req 5: Run selector with multi-run isolation from `server/spread_dash.py:181`

Telemetry only: reads SQLite orders, fills, and reconcile_lock directly
from `data/orders.db` via read-only URI mode:
`sqlite3.connect('file:<path>?mode=ro', uri=True)`.

Zero venue network calls. Zero credentials needed.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import os
import logging
import re
import secrets
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Generator, Optional

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

logger = logging.getLogger(__name__)


class _NoScoringValue(str):
    """Formats as the marker text regardless of the card's format spec (#709)."""

    def __format__(self, spec: str) -> str:
        return str(self)

    def __mul__(self, other):
        return self

    __rmul__ = __mul__


class _NoScoringConfig:
    """Placeholder the params panel uses when `scoring.config.load()` failed (#709).

    Every attribute reads as the marker, so the screener cards show the failure
    instead of engine values silently substituted from `core_brain.config`.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def __getattr__(self, name: str) -> _NoScoringValue:
        return _NoScoringValue("⚠ scoring config unavailable — see dashboard log")


# live/, one level up from live/dash/. Everything this page reads lives under it.
LIVE_ROOT = Path(__file__).resolve().parent.parent
# Launching this file by path (`python live/dash/live_dash.py`) puts live/dash/ on
# sys.path, not live/, so `import core_brain.kpi` fails at request time with a 500 that
# the live suite never sees -- it runs with live/ as the working directory.
if str(LIVE_ROOT) not in sys.path:
    sys.path.insert(0, str(LIVE_ROOT))

# Runtime state moved run/ -> runtime/ and some of its files were renamed. The
# page must still see a stack that was started before that move: a registry it
# cannot find reads as STOPPED, and START would then launch a second live
# Trader beside the running one. resolve_runtime_file falls back to the old
# path while only the old file exists. See core_brain/runtime_paths.py.
from core_brain import code_revision  # noqa: E402
from core_brain.runtime_paths import (  # noqa: E402
    PENDING_PROFILE_FILE,
    TRADER_PROFILE_FILE,
    legacy_runtime_file,
    resolve_runtime_file,
    runtime_file,
    service_entry,
)

REPO_ROOT = LIVE_ROOT
DEFAULT_PORT = 8799

#: Which code THIS dashboard process loaded. `read_code_revision` shells out to
#: git twice, and /api/system/status is polled by every open dashboard, so it is
#: read once at start -- the same contract the rehearsal heartbeats follow. A
#: per-request read would also let an edit rewrite what the page claims to hold.
_DASH_CODE = code_revision.read_code_revision()


def resolve_port(explicit: int | None = None) -> int:
    """Which port to bind: the flag, then `PORT`, then the operator's default.

    8799 is a real address, not a placeholder: the operator's live stack serves
    the control surface there, START button and all. Anything else that wants to
    run this app -- a preview harness, a second copy for research -- has to be
    able to take another port without being handed a flag, or it collides with
    the live one.

    A `PORT` that is not a usable port raises rather than falling through to the
    default, because falling through would land the process on 8799 beside the
    live stack, which is the one outcome this exists to prevent.
    """
    if explicit is not None:
        return explicit
    raw = os.environ.get("PORT")
    if raw is None:
        return DEFAULT_PORT
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError(f"PORT={raw!r} is not a port number") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"PORT={raw!r} is outside 1-65535")
    return port


# The port actually bound. main() overwrites it when --port is given; the status
# payload must report where the page really is, not where it usually is.
_ACTIVE_PORT = resolve_port(None)
POLL_INTERVAL_MS = 2000

CYCLE_RING_NAME = "cycle_events.jsonl"
# The client keeps 200 rows per family, so replaying 50 threw away three quarters
# of the history it could show -- and with a shadow run emitting ~1,800 events,
# every fill and alert fell outside that window. Replay what the UI can hold.
SSE_REPLAY_LINES = 200
SSE_POLL_SEC = 0.5
SSE_KEEPALIVE_SEC = 15.0

# #427: server-owned live marks. One read-only venue feed (or sim walk, or
# off) serves every browser over the existing cycle stream as `event: mark`
# frames. The cache is module-global so the generator and the KPI route share
# it; the worker thread starts only in the CLI path, never at import and
# never inside a request or a generator.
_LIVE_MARKS_CACHE = None
_LIVE_MARKS_WORKER = None


def _live_marks_cache():
    """The shared mark cache, created lazily. No network, no thread."""
    global _LIVE_MARKS_CACHE
    if _LIVE_MARKS_CACHE is None:
        from core_brain.live_marks import LiveMarkCache
        _LIVE_MARKS_CACHE = LiveMarkCache()
    return _LIVE_MARKS_CACHE


def start_live_marks(source=None):
    """Start the single feed worker (CLI startup path only). Idempotent."""
    global _LIVE_MARKS_WORKER
    if _LIVE_MARKS_WORKER is not None:
        return _LIVE_MARKS_WORKER
    from core_brain.live_marks import (
        LiveMarkWorker,
        live_marks_source,
    )
    src = source or live_marks_source()
    if src == "off":
        return None
    _LIVE_MARKS_WORKER = LiveMarkWorker(_live_marks_cache(), source=src)
    _LIVE_MARKS_WORKER.start()
    return _LIVE_MARKS_WORKER


def stop_live_marks():
    """Stop the feed worker (tests, shutdown). The cache keeps its data."""
    global _LIVE_MARKS_WORKER
    worker, _LIVE_MARKS_WORKER = _LIVE_MARKS_WORKER, None
    if worker is not None:
        worker.stop()


def sync_live_marks() -> bool:
    """Push a drifted wanted set to the venue socket (no-op unless running)."""
    worker = _LIVE_MARKS_WORKER
    if worker is None:
        return False
    try:
        return bool(worker.sync_subscription())
    except Exception:
        logger.debug("live-marks resubscribe skipped", exc_info=True)
        return False


def _mark_frame(seq, snapshot, reset, marks):
    """One `event: mark` SSE frame for the live-marks contract (#427)."""
    return ("event: mark\ndata: "
            + json.dumps({"seq": seq, "snapshot": snapshot,
                          "reset": reset, "marks": marks}) + "\n\n")
# How long a shutdown waits for open connections before dropping them.
SHUTDOWN_GRACE_SEC = 5
SCAN_STALL_THRESHOLD_SEC = 90.0

_ACTIVE_RING_OVERRIDE: Path | None = None
_ACTIVE_HEARTBEAT_OVERRIDE: Path | None = None
_ACTIVE_GUARDRAIL_HB_OVERRIDE: Path | None = None


def set_ring_override(path: Path | str | None) -> None:
    """Point the cycle-stream/scan-state endpoints at a specific ring file (tests)."""
    global _ACTIVE_RING_OVERRIDE
    _ACTIVE_RING_OVERRIDE = Path(path) if path else None


def set_heartbeat_override(path: Path | str | None) -> None:
    """Point scan-state at a specific heartbeat file (tests)."""
    global _ACTIVE_HEARTBEAT_OVERRIDE
    _ACTIVE_HEARTBEAT_OVERRIDE = Path(path) if path else None


def set_guardrail_heartbeat_override(path: Path | str | None) -> None:
    """Point the guardrail-health endpoint at a specific heartbeat file (tests)."""
    global _ACTIVE_GUARDRAIL_HB_OVERRIDE
    _ACTIVE_GUARDRAIL_HB_OVERRIDE = Path(path) if path else None


def resolve_ring_path() -> Path:
    if _ACTIVE_RING_OVERRIDE is not None:
        return _ACTIVE_RING_OVERRIDE
    shadow_ring = _resolve_shadow_ring_path()
    if shadow_ring is not None:
        return shadow_ring
    return resolve_runtime_file(CYCLE_RING_NAME, root=LIVE_ROOT)


def resolve_heartbeat_path() -> Path:
    if _ACTIVE_HEARTBEAT_OVERRIDE is not None:
        return _ACTIVE_HEARTBEAT_OVERRIDE
    return resolve_runtime_file("live_poll_heartbeat.json", root=LIVE_ROOT)


def resolve_guardrail_heartbeat_path() -> Path:
    """The watcher's self-report file (live/scripts/global_stop_loss.py).

    Falls back to the pre-rename guardrail_watch_heartbeat.json so a watcher
    started before the rename does not read as a dead safety monitor.
    """
    if _ACTIVE_GUARDRAIL_HB_OVERRIDE is not None:
        return _ACTIVE_GUARDRAIL_HB_OVERRIDE
    return resolve_runtime_file("global_stop_loss_heartbeat.json", root=LIVE_ROOT)


def resolve_db_path(custom_path: str | Path | None = None) -> Path:
    """Find the live registry SQLite database path."""
    if custom_path:
        return Path(custom_path)
    env_path = os.environ.get("LIVE_DB_PATH")
    if env_path:
        return Path(env_path)
    from core_brain.order_registry import DEFAULT_DB_PATH
    return DEFAULT_DB_PATH


def resolve_db_identity(db_path: Path | str) -> dict:
    """Which registry the page is reading: the live one, or something else.

    Three answers, never two. LIVE is the production registry
    (`core_brain.order_registry.DEFAULT_DB_PATH`); SHADOW is any other store the
    page was pointed at with `--db` or `LIVE_DB_PATH`; UNKNOWN is a path the
    platform refuses to resolve.

    UNKNOWN reports `is_production=False`, and that direction is the whole
    point. START is gated on this flag, so a path we cannot resolve refuses a
    live stack rather than launching one on a guess. The mirror-image guard in
    `core_brain/shadow_guard.py:assert_not_production_registry` fails closed the
    other way -- it refuses a shadow *write* it cannot prove is safe -- because
    there the dangerous outcome is the opposite one.
    """
    from core_brain.order_registry import DEFAULT_DB_PATH

    raw = Path(db_path)
    try:
        same = raw.resolve() == DEFAULT_DB_PATH.resolve()
    except (OSError, ValueError, RuntimeError):
        return {"path": str(raw), "mode": "UNKNOWN", "is_production": False}
    return {
        "path": str(raw),
        "mode": "LIVE" if same else "SHADOW",
        "is_production": same,
    }


# The Market Filter's default re-rank cadence when SH_FILTER_INTERVAL_SEC is
# unset -- the same default `scripts/filter_loop.py` applies.
DEFAULT_SCAN_INTERVAL_SEC = 600.0


def resolve_scan_interval() -> float:
    """Configured Market Filter cadence in seconds.

    `scripts/filter_loop.py` re-ranks the universe every SH_FILTER_INTERVAL_SEC
    (default 600). The page ages the pipeline snapshot against it, so an
    operator who sets a 60s cadence must not have a 20-minute-old snapshot
    still reading LIVE against a 600 baked into app.js. Absent, invalid, or
    non-positive falls back to the default rather than refusing: the cadence is
    a display threshold, not a trading parameter.
    """
    raw = (os.environ.get("SH_FILTER_INTERVAL_SEC") or "").strip()
    if not raw:
        return DEFAULT_SCAN_INTERVAL_SEC
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_SCAN_INTERVAL_SEC
    if not math.isfinite(value) or value <= 0:
        return DEFAULT_SCAN_INTERVAL_SEC
    return value


def resolve_sweep_interval() -> float | None:
    """Configured account-sweep cadence in seconds, or None for every tick.

    Read from LIVE_SWEEP_INTERVAL so the operator can throttle the venue
    reads the account card depends on without editing code. An absent,
    invalid, or non-positive value falls back to the every-tick default.
    """
    raw = (os.environ.get("LIVE_SWEEP_INTERVAL") or "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return value


def _env_file() -> Path | None:
    """The .env file core_brain.order_manager loads, found without importing it.

    Resolves through the same core_brain.env_file seam the CLI uses: the
    nearest .env walking up from core_brain/, stopping at the AGENTS.md
    boundary. The dashboard never loads the whole file -- only
    LIVE_SWEEP_INTERVAL is read or written -- so the signing key and L2
    credentials never enter this process.
    """
    from core_brain.env_file import find_env_file

    return find_env_file(LIVE_ROOT / "core_brain")


def read_sweep_interval_from_env_file(env_path: Path) -> float | None:
    """Parse LIVE_SWEEP_INTERVAL from a .env file, or None.

    Reads the file as text and looks only at that key, so no credential line
    is ever materialised anywhere it could be logged.
    """
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() != "LIVE_SWEEP_INTERVAL":
            continue
        raw = value.strip()
        if not raw:
            return None
        try:
            parsed = float(raw)
        except ValueError:
            return None
        return parsed if parsed > 0 else None
    return None


def write_sweep_interval_to_env_file(env_path: Path, value: float | None) -> bool:
    """Set or remove LIVE_SWEEP_INTERVAL in .env without disturbing the rest.

    Atomic temp-file + fsync + os.replace, carrying over the file's permission
    bits, because the file may hold POLY_PRIVATE_KEY and must never be
    truncated in place.
    """
    try:
        text = env_path.read_text(encoding="utf-8")
        try:
            mode = os.stat(env_path).st_mode
        except OSError:
            mode = None
    except OSError:
        return False

    lines = [
        ln for ln in text.splitlines()
        if ln.split("=", 1)[0].strip() != "LIVE_SWEEP_INTERVAL"
    ]
    if value is not None:
        lines += [
            "",
            "# Account-sweep cadence for the live dashboard (seconds).",
            f"LIVE_SWEEP_INTERVAL={value:g}",
        ]
    new_text = "\n".join(lines)
    if new_text and not new_text.endswith("\n"):
        new_text += "\n"

    tmp = env_path.with_name(f".env.tmp.{secrets.token_hex(4)}")
    try:
        with open(tmp, "w", encoding="utf-8", opener=lambda path, flags: os.open(path, flags, 0o600)) as fh:
            fh.write(new_text)
            fh.flush()
            os.fsync(fh.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, env_path)
        return True
    except OSError:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return False


def _bootstrap_sweep_interval() -> None:
    """Seed LIVE_SWEEP_INTERVAL from .env once, without loading credentials.

    An explicit environment variable wins; only when it is absent do we read
    the single key back from the file the engine loads. Everything else in
    .env -- the signing key, L2 credentials -- stays on disk.
    """
    if "LIVE_SWEEP_INTERVAL" in os.environ:
        return
    env_file = _env_file()
    if env_file is None:
        return
    saved = read_sweep_interval_from_env_file(env_file)
    if saved is not None:
        os.environ["LIVE_SWEEP_INTERVAL"] = str(saved)


_bootstrap_sweep_interval()


app = FastAPI(title="Spread Hunter Monitor")
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Mount static files (CSS/JS) for the extracted frontend.
# HTML is still templated by index() to inject the per-process control token.
_STATIC_DIR = Path(__file__).resolve().parent / "static"
if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

# Every /api/system/* route changes machine state: start spawns the live
# execution loop that signs real venue requests, reset-db deletes the registry,
# restart-dash ends this process. Loopback binding is not a defence -- a page
# open in the operator's browser can submit a cross-origin form POST to
# 127.0.0.1:8799 with no CORS preflight, and the side effect lands even though
# the attacker cannot read the reply.
#
# The token is generated per process and handed to the page that this process
# serves, so there is nothing for the operator to configure and no state to
# leak between runs. A simple form POST cannot set a custom header, which is
# what makes the header requirement a complete CSRF defence on its own; the
# Origin check is the second lock.
CONTROL_TOKEN = secrets.token_urlsafe(32)
CONTROL_TOKEN_PLACEHOLDER = "__LIVE_DASH_CONTROL_TOKEN__"


def _authorize_control(request: Request) -> None:
    """Reject cross-origin or untokened attempts to change machine state."""
    origin = request.headers.get("origin")
    if origin is not None:
        allowed = {f"http://{request.url.netloc}", f"https://{request.url.netloc}"}
        if origin not in allowed:
            raise HTTPException(status_code=403, detail="cross-origin control request refused")
    if request.headers.get("x-control-token") != CONTROL_TOKEN:
        raise HTTPException(status_code=403, detail="missing or stale control token")

_ACTIVE_DB_OVERRIDE: Path | None = None


def set_db_override(path: Path | str | None) -> None:
    global _ACTIVE_DB_OVERRIDE
    _ACTIVE_DB_OVERRIDE = Path(path) if path else None


def demo_db_path() -> Path:
    """The bogus registry `scripts/build_demo_db.py` writes.

    A function rather than a constant so tests can point it at a store they
    built; the dashboard itself always reads the repo's own demo file.
    """
    return LIVE_ROOT / "data" / "demo_orders.db"


def _same_registry(a: Path | str, b: Path | str) -> bool:
    """Do two paths name the same store file?"""
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return str(a) == str(b)


# How long a built snapshot is served to every caller before it is rebuilt.
# The page polls on a 2s timer, so 2.5s avoids re-triggering background builds
# on every single cycle of a single tab.
SNAPSHOT_TTL_SEC: float = 2.5
SNAPSHOT_ENDED_SHADOW_TTL_SEC: float = 60.0


def _snapshot_ttl_for_key(key: tuple) -> float:
    """Return appropriate snapshot TTL: static/ended shadow stores cache longer.

    A heartbeat that cannot be read must not be fatal here -- the caller is on the
    hot path for every snapshot build -- but silently dropping to the live TTL is
    exactly what made a large ended store rebuild on every poll, so the fallback is
    logged rather than swallowed.
    """
    if len(key) >= 2 and isinstance(key[1], str):
        try:
            shadow = read_shadow_run(key[1])
        except (OSError, ValueError) as exc:
            logger.warning("shadow heartbeat unreadable for %s: %s", key[1], exc)
            shadow = None
        if shadow and shadow.get("ended"):
            return SNAPSHOT_ENDED_SHADOW_TTL_SEC
    return SNAPSHOT_TTL_SEC

_snapshots: dict[tuple, tuple[float, Any]] = {}
_snapshot_builders: dict[tuple, threading.Lock] = {}
_snapshot_registry_lock = threading.Lock()


def _cached_snapshot(key: tuple, build, ttl: float | None = None):
    """Build `key`'s snapshot at most once per TTL, however many ask for it.

    Every registry read in the process serialises on one lock in
    `core_brain.order_registry`, and a full `/api/kpi` plus `/api/state` pass
    over a run-sized store costs more than a second of it. Without this, two
    open browser tabs ask for more work per second than the lock can deliver,
    the request threadpool fills with readers waiting on each other, and the
    dashboard stops answering anything at all -- which the page reports as lost
    contact with the engine.

    Only the very first caller waits for a build. Once a snapshot exists it is
    served immediately, stale or not, and one background thread refreshes it --
    a build costs several seconds of the registry lock, which is longer than
    the TTL, so making readers wait for a fresh one meant a page load sat for
    40 seconds behind requests an earlier load had abandoned. Concurrent first
    callers share one build rather than starting their own.

    What is cached is the payload, never a `Response` object. A response
    carries per-request state -- the gzip middleware rewrites its headers as it
    sends -- so handing one instance to two requests corrupts both
    ("Response content longer than Content-Length").

    `ttl` defaults per call rather than as an argument default: the snapshot
    tests shorten SNAPSHOT_TTL_SEC at runtime, and binding it at import would
    freeze the value and silently un-test every one of them. The default is
    resolved per key, so an ended shadow store caches far longer than a live
    one while an explicit `ttl` still wins for the tests that pass one.
    """
    ttl = _snapshot_ttl_for_key(key) if ttl is None else ttl
    hit = _snapshots.get(key)
    if hit is not None and (time.monotonic() - hit[0]) < ttl:
        return hit[1]

    with _snapshot_registry_lock:
        builder = _snapshot_builders.setdefault(key, threading.Lock())

    if hit is not None:
        if builder.acquire(blocking=False):
            try:
                threading.Thread(target=_refresh_snapshot, args=(key, build, builder),
                                 daemon=True).start()
            except RuntimeError:
                # The thread never started, so nothing will release the lock:
                # hold it and this key freezes on one snapshot forever, which
                # is the silent kind of stale this whole cache exists to avoid.
                builder.release()
                raise
        return hit[1]

    with builder:
        # Re-check: whoever held the builder lock has just refreshed this key.
        hit = _snapshots.get(key)
        if hit is not None and (time.monotonic() - hit[0]) < ttl:
            return hit[1]
        value = build()
        _snapshots[key] = (time.monotonic(), value)
        return value


def _refresh_snapshot(key: tuple, build, builder: threading.Lock) -> None:
    """Rebuild `key` off the request path, keeping the old value on failure."""
    try:
        # Build first, THEN read the clock: `(time.monotonic(), build())`
        # evaluates left to right, so a build slower than SNAPSHOT_TTL_SEC
        # landed in the cache already expired and the endpoint rebuilt
        # continuously without ever serving anything fresh.
        value = build()
        _snapshots[key] = (time.monotonic(), value)
    except Exception:
        # The previous snapshot stays as it was: serving the last good numbers
        # beats serving none, and the next reader retries the refresh.
        logger.exception("snapshot refresh failed for %s", key)
    finally:
        builder.release()


# How many cancelled orders per market survive into `/api/state`.
#
# Dead-order shaping lives in core_brain.registry_state (trim_cancelled_orders),
# the read side of the registry: the server renders that shape, never
# recomputes it.
@app.get("/api/state")
def get_state():
    """Return JSON state snapshot for the live execution dashboard."""
    from core_brain.registry_state import summarize_state, trim_cancelled_orders
    db_path = resolve_db_path(_ACTIVE_DB_OVERRIDE)
    payload = _cached_snapshot(
        ("state", str(db_path)),
        lambda: trim_cancelled_orders(summarize_state(db_path)),
    )
    return JSONResponse(payload)


# How far a process's real creation time may sit from the time we recorded for
# it. The parent writes started_at immediately after Popen, so a genuine match
# is sub-second; this is slack for clock granularity, not for a different
# process. A recycled PID landing inside this window is not a case worth
# engineering around -- a PID that came back within a minute is still the same
# generation of work.
PID_START_TOLERANCE_S: float = 60.0

# Issue #457: the stop budget. One shared deadline keeps the whole stop inside
# the 30s ops-lock takeover window, so another control request can never observe
# a half-stopped stack as fully stopped. 10 + 5 + 2×5 = 25s.
_STOP_POLITE_WAIT_S: float = 10.0
_STOP_FORCE_WAIT_S: float = 5.0
_STOP_TASKKILL_TIMEOUT_S: float = 5.0
_STOP_POLL_INTERVAL_S: float = 0.25


def _win_process_times(pid: int) -> tuple[float | None, float | None] | None:
    """(created, exited) as Unix timestamps for a Windows PID.

    `exited` is None while the process is still running. It is not always zero
    for a dead one: a process whose parent still holds an open handle stays
    queryable after exit, and only this field distinguishes it from a live one.

    Returns None when the process cannot be opened at all.
    """
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    # argtypes and restype are declared, not left to ctypes' defaults. A HANDLE
    # is pointer-sized, and the default `c_int` restype truncates it on 64-bit
    # Windows -- so a handle above 2**31 would come back as a different value,
    # be passed to GetProcessTimes as garbage, and then be closed as garbage.
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.GetProcessTimes.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    k32.GetProcessTimes.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL

    handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return None
    try:
        creation = wintypes.FILETIME()
        exited = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        if not k32.GetProcessTimes(
                handle, ctypes.byref(creation), ctypes.byref(exited),
                ctypes.byref(kernel), ctypes.byref(user)):
            return None

        def _unix(ft) -> float | None:
            # FILETIME counts 100ns intervals since 1601-01-01; the Unix epoch
            # is 11644473600 seconds later. Zero means "not set".
            ticks = (ft.dwHighDateTime << 32) | ft.dwLowDateTime
            return None if ticks == 0 else ticks / 1e7 - 11644473600.0

        return _unix(creation), _unix(exited)
    finally:
        k32.CloseHandle(handle)


def _process_start_time(pid: int) -> float | None:
    """Unix timestamp the process was created, or None if it cannot be read."""
    try:
        if sys.platform == "win32":
            times = _win_process_times(int(pid))
            return None if times is None else times[0]
        # Linux: field 22 of /proc/<pid>/stat is starttime in clock ticks since
        # boot. The comm field can contain spaces and parentheses, so the split
        # starts after the last ')'.
        stat = Path(f"/proc/{int(pid)}/stat").read_text()
        fields = stat[stat.rindex(")") + 2:].split()
        starttime_ticks = float(fields[19])
        hz = os.sysconf("SC_CLK_TCK")
        with open("/proc/stat", encoding="utf-8") as fh:
            btime = next(float(line.split()[1])
                         for line in fh if line.startswith("btime "))
        return btime + starttime_ticks / hz
    except Exception:
        return None


def _is_pid_alive(pid: int | None, started_at: float | None = None) -> bool:
    """Is the process we recorded still running?

    A bare PID check is not enough. The OS recycles PIDs, and on this project it
    did: the bot exited, Windows handed 13052 to msedge, and the dashboard then
    reported the bot stack RUNNING forever -- which permanently refused every
    `Fresh DB` reset and would have refused a legitimate start. When we know
    when the process was supposed to have started, the creation time must agree.

    If the creation time cannot be read (unsupported platform, denied access),
    this falls back to the bare PID check rather than declaring the process
    dead: a false "stopped" would let a second bot stack launch alongside a
    live one, which AGENTS.md forbids outright.
    """
    if not pid or pid <= 0:
        return False
    created: float | None = None
    try:
        if sys.platform == "win32":
            times = _win_process_times(int(pid))
            if times is None:
                return False
            created, exited = times
            if exited is not None:
                # Queryable but finished -- a handle is still open somewhere.
                return False
        else:
            try:
                os.kill(int(pid), 0)
            except PermissionError:
                # EPERM means the process exists but belongs to another user.
                # Letting the bare `except` below turn that into False is the
                # false "stopped" this function exists to avoid -- it would let
                # a second bot stack launch beside a live one.
                pass
    except Exception:
        return False

    if started_at is None:
        return True
    if created is None:
        created = _process_start_time(int(pid))
    if created is None:
        return True
    return abs(created - float(started_at)) <= PID_START_TOLERANCE_S


# A heartbeat older than this many rotations means nobody is refreshing it:
# the rehearsal ended or died. A stopwatch that keeps ticking for a dead
# process is worse than no stopwatch.
# Sized at 120s (above SCAN_STALL_THRESHOLD_SEC 90s) so multi-market CLOB
# orderbook queries over the public API (which take ~40-55s per rotation)
# do not trip a false "ended" / STALLED alert mid-rotation.
SHADOW_HEARTBEAT_STALE_ROTATIONS = 3.0
SHADOW_HEARTBEAT_MIN_STALE_S = 120.0


def _measured_cadence(cycle: Any, started_at: float, heartbeat_ts: float,
                      interval: float) -> float:
    """How long a rotation really takes, from the rotations the loop has run.

    `interval` is the configured SLEEP between rotations, not their length. A
    rehearsal at `--interval 5` whose rotations spend ~160s on public
    order-book reads was aged against 5s, so a healthy loop read STALLED every
    cycle. Dividing the elapsed run by the rotation count gives the real
    cadence; the configured interval stays the floor, so an early burst of
    fast rotations cannot shrink the ramp into false alarms, and a heartbeat
    written before the `cycle` field existed keeps the old behaviour.
    """
    try:
        rotations = int(cycle)
    except (TypeError, ValueError):
        return interval
    if rotations <= 0:
        return interval
    elapsed = heartbeat_ts - started_at
    if elapsed <= 0:
        return interval
    return max(interval, elapsed / rotations)


def _format_heartbeat_rel_path(path: Path | None) -> str | None:
    """Format a heartbeat path relative to LIVE_ROOT when possible, else runtime/name or name."""
    if path is None:
        return None
    try:
        return path.resolve().relative_to(LIVE_ROOT.resolve()).as_posix()
    except Exception:
        if path.parent.name in ("runtime", "run"):
            return f"{path.parent.name}/{path.name}"
        return path.name


def read_shadow_run(active_db_path: str | None, now: float | None = None) -> dict | None:
    """The shadow rehearsal writing THIS store, or None.

    A shadow run is not in `runtime/processes.json`, so it publishes its own
    liveness in a per-run heartbeat, `runtime/shadow_run_<run_id>.json` (see
    `core_brain.shadow_run.write_shadow_heartbeat`). Concurrent rehearsals
    each write their own file -- the old single shared `shadow_run.json` made
    the last writer win, so a dashboard pointed at either store flickered to
    "not running" whenever the other run refreshed. The legacy shared name is
    still read as a fallback for heartbeats written by older code. Only a
    heartbeat whose `db_path` matches the store this page is reading is
    surfaced: a stopwatch for a run writing somewhere else would be
    describing numbers that are not on the screen.

    Evaluates all matching candidates, preferring running runs (running=True)
    over ended ones, then the freshest heartbeat.
    """
    now = time.time() if now is None else now
    matched: list[dict] = []
    for path in _shadow_heartbeat_candidates():
        try:
            if now - path.stat().st_mtime > SHADOW_RUN_LIST_WINDOW_S:
                continue
        except OSError:
            continue
        run = _read_shadow_heartbeat_file(path, active_db_path, now)
        if run is not None:
            matched.append(run)
    if not matched:
        return None
    matched.sort(key=lambda r: (0 if r.get("running") else 1, float(r.get("heartbeat_age_sec", 0.0))))
    winner = matched[0]
    # Does this run still hold the code on disk? The verdict is what makes a
    # rehearsal that outlived its code visible: on 2026-10-07 two of three runs
    # were deciding with the previous morning's ranker and no queue gate, and
    # the page could not say so. Read the tree clock ONCE for the whole call --
    # it is a walk over core_brain/ and scoring/, and `read_shadow_run` is also
    # reached outside the 10s status cache. The winning payload carries
    # `code_revision`, `process_started_at` and `started_at`, which is exactly
    # the evidence `code_predates_tree` reads, so no second lookup is needed.
    winner["code_stale"] = code_revision.code_predates_tree(
        winner, clock=code_revision.decision_code_mtime())
    try:
        from core_brain.run_scorer import score_run
        winner["reliability"] = score_run(
            winner.get("db_path") or active_db_path,
            run_id=winner.get("run_id"),
        ).to_dict()
    except Exception:
        winner["reliability"] = None
    return winner


def read_other_live_shadow_runs(active_db_path: str | None, now: float | None = None) -> list[dict]:
    """Running rehearsals writing a store OTHER than the active one.

    Surfaces only identity: `run_id`, `db_path`, `heartbeat_file`. No ages,
    cycle counts, elapsed time, or cadence from another store are returned.
    """
    now = time.time() if now is None else now
    other_runs: list[dict] = []
    seen_runs: set[str] = set()

    for path in _shadow_heartbeat_candidates():
        try:
            if now - path.stat().st_mtime > SHADOW_RUN_LIST_WINDOW_S:
                continue
        except OSError:
            continue
        run = _read_shadow_heartbeat_file(path, active_db_path, now, match_db=False)
        if run is None or not run.get("running"):
            continue
        run_db = run.get("db_path")
        if _is_temp_or_test_db(run_db):
            continue
        if _same_path(run_db, active_db_path):
            continue
        run_id = str(run.get("run_id") or path.stem)
        if run_id in seen_runs:
            continue
        seen_runs.add(run_id)
        other_runs.append({
            "run_id": run.get("run_id"),
            "db_path": run.get("db_path"),
            "heartbeat_file": run.get("heartbeat_file"),
        })
    return other_runs


def _shadow_heartbeat_candidates() -> list[Path]:
    """Every heartbeat file that might hold the run for the active store.

    The per-run names are scanned, then the legacy shared name. Older files
    missing from the listing (or an unreadable directory) degrade to the
    legacy fallback rather than an error.
    """
    candidates: list[Path] = []
    try:
        runtime_dir = Path(resolve_runtime_file("shadow_run.json", root=LIVE_ROOT)).parent
        candidates.extend(sorted(runtime_dir.glob("shadow_run_*.json")))
    except OSError:
        pass
    candidates.append(resolve_runtime_file("shadow_run.json", root=LIVE_ROOT))
    return candidates


def _same_path(a: Any, b: Any) -> bool:
    """Whether two paths name the same file, without raising on junk input."""
    if not a or not b:
        return False
    try:
        return Path(a).resolve() == Path(b).resolve()
    except (OSError, ValueError, TypeError, RuntimeError):
        return False


def _is_temp_or_test_db(path: Any) -> bool:
    """Whether `path` is in a temporary or pytest directory.

    Temporary database files created under system Temp or pytest directories
    are transient test artifacts and are out of scope for the operations dashboard.
    """
    if not path:
        return False
    try:
        p = Path(path).resolve()
        temp_dir = Path(tempfile.gettempdir()).resolve()
        runtime_dir = Path(resolve_runtime_file("shadow_run.json", root=LIVE_ROOT)).parent.resolve()
        if temp_dir in runtime_dir.parents or runtime_dir == temp_dir:
            # Running under a test fixture with runtime redirected to temp.
            # Allow databases located within the fixture's sandboxed root,
            # but reject foreign temp or pytest paths outside it.
            fixture_root = runtime_dir.parent
            if fixture_root in p.parents or p.parent == fixture_root:
                return False
        if temp_dir in p.parents or p == temp_dir:
            return True
    except (OSError, ValueError, TypeError, RuntimeError):
        pass
    parts = str(path).replace("\\", "/").lower().split("/")
    return any(
        part in ("temp", "tmp", "pytest") or part.startswith("pytest-")
        for part in parts[:-1]
    )


def _read_shadow_heartbeat_file(
    path: Path, active_db_path: str | None, now: float | None,
    match_db: bool = True,
) -> dict | None:
    """One heartbeat file as a stopwatch payload, or None on any mismatch.

    `match_db` is the db-scoped rule the page runs on: a heartbeat naming
    another store belongs to another run and must not put its stopwatch here.
    The listing in `list_shadow_runs` passes `match_db=False` to read the same
    files without the filter, which is how it can name a run writing a store
    this page is not pointed at.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None

    heartbeat_db = raw.get("db_path")
    if match_db and (not heartbeat_db or not active_db_path):
        return None
    if match_db and not _same_path(heartbeat_db, active_db_path):
        return None

    now = time.time() if now is None else now
    try:
        started_at = float(raw.get("started_at"))
        heartbeat_ts = float(raw.get("heartbeat_ts"))
        interval = float(raw.get("interval") or 5.0)
    except (TypeError, ValueError):
        return None

    heartbeat_age = max(0.0, now - heartbeat_ts)
    cadence = _measured_cadence(raw.get("cycle"), started_at, heartbeat_ts, interval)
    stale_after = max(SHADOW_HEARTBEAT_MIN_STALE_S,
                      SHADOW_HEARTBEAT_STALE_ROTATIONS * cadence)
    finished = bool(raw.get("finished"))
    pid = raw.get("pid")
    started_at_proc = raw.get("process_started_at")
    if started_at_proc is None:
        started_at_proc = raw.get("started_at")
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        # A malformed pid in the heartbeat file must not crash the reader.
        pid_int = 0
    # Only a positive integer identifies a process; anything else (0, negative,
    # bool, junk) is "unknown" and must not declare a live run dead.
    pid_alive = _is_pid_alive(pid_int, started_at_proc) if isinstance(pid, int) and not isinstance(pid, bool) and pid_int > 0 else None

    # Reasons evaluated in strict order:
    # 1. finished cleanly
    # 2. process gone (15s grace)
    # 3. heartbeat stale
    if finished:
        end_reason = "finished"
    elif pid_alive is False and heartbeat_age > 15.0:
        end_reason = "process_gone"
    elif heartbeat_age > stale_after:
        end_reason = "heartbeat_stale"
    else:
        end_reason = None

    ended = end_reason is not None

    from core_brain.shadow_run import parse_tournament_db_path

    resolved_db = None
    if heartbeat_db:
        try:
            resolved_db = str(Path(heartbeat_db).resolve())
        except Exception:
            resolved_db = str(heartbeat_db)

    tournament = parse_tournament_db_path(resolved_db or heartbeat_db)

    return {
        "run_id": raw.get("run_id"),
        # Which store this run writes. The switcher needs it to re-point the
        # page, and the badge needs it to tell "not this store" from "no store".
        "db_path": resolved_db,
        "pid": raw.get("pid"),
        "started_at": started_at,
        "minutes": raw.get("minutes"),
        "interval": interval,
        # What a rotation actually costs, which is what the watchdog ramps off.
        "cadence_sec": cadence,
        "heartbeat_age_sec": heartbeat_age,
        # Elapsed at the last heartbeat, not at `now`: once a run ends the
        # stopwatch must stop where it stopped.
        "elapsed_sec": max(0.0, (heartbeat_ts if ended else now) - started_at),
        "running": not ended,
        "ended": ended,
        "finished": finished,
        "heartbeat_file": _format_heartbeat_rel_path(path),
        "pid_alive": pid_alive,
        "end_reason": end_reason,
        "dash_port": raw.get("dash_port"),
        "tournament": tournament,
        # The code this process loaded, as it recorded it at start (None for a
        # heartbeat written before the field existed). Passed through raw here:
        # the staleness verdict is computed once in `read_shadow_run` against a
        # single tree clock, not per heartbeat file.
        "code_revision": raw.get("code_revision") if isinstance(
            raw.get("code_revision"), dict) else None,
        # When this process started, which is what bounds the code of a
        # heartbeat that carries no recorded revision.
        "process_started_at": raw.get("process_started_at"),
        # Where this run's books came from (#659 N2), exactly as the run
        # recorded it. None for a heartbeat written before the field existed:
        # an old heartbeat says nothing about its source, and "0 stream" would
        # be a claim about a run that never made one.
        "book_source_mix": raw.get("book_source_mix") if isinstance(
            raw.get("book_source_mix"), dict) else None,
        # The engine configuration this run loaded (#856), exactly as the run
        # recorded it. None for a heartbeat written before the field existed:
        # an old heartbeat says nothing about its profile, and a missing name
        # is "unknown", never a guess from this process's environment.
        "profile": raw.get("profile") if isinstance(
            raw.get("profile"), dict) and isinstance(
            raw.get("profile").get("name"), str) else None,
    }


#: How long a heartbeat file is worth reading at all.
#:
#: These are never cleaned up: one file per rehearsal, kept forever, and a
#: machine that has been running trials for weeks holds well over a thousand.
#: A run that has not written for six hours is long dead -- the `ended` verdict
#: above lands at 120s -- so the listing skips those files on mtime and never
#: pays to parse them.
SHADOW_RUN_LIST_WINDOW_S = 6 * 3600.0

#: How many runs the switcher offers. Enough to cover a machine with a few
#: rehearsals in flight; the ordering puts the live ones first.
SHADOW_RUN_LIST_LIMIT = 12


#: Sort sentinel for runs with no dashboard port (or no parsed arm index):
#: they sink below ported arms inside their group instead of tying at 0.
_NO_DASH_PORT: float = float("inf")


def list_shadow_runs(active_db_path: str | None = None,
                     now: float | None = None,
                     limit: int = SHADOW_RUN_LIST_LIMIT) -> list[dict]:
    """Every recent rehearsal on this machine, live ones first.

    `read_shadow_run` answers "which run is writing THIS store" and drops the
    rest. That is right for the stopwatch and wrong for the operator question
    the page cannot otherwise answer: *is anything running at all?* A run that
    has not written in hours cannot be, and a run on another store is named
    here and never read into this page's numbers.
    """
    now = time.time() if now is None else now
    runs: list[dict] = []
    for path in _shadow_heartbeat_candidates():
        try:
            if now - path.stat().st_mtime > SHADOW_RUN_LIST_WINDOW_S:
                continue
        except OSError:
            continue
        run = _read_shadow_heartbeat_file(path, active_db_path, now, match_db=False)
        if run is None:
            continue
        if _is_temp_or_test_db(run.get("db_path")):
            continue
        run["is_active_db"] = _same_path(run.get("db_path"), active_db_path)
        run["source_file"] = path.name
        runs.append(run)

    # Live first, then freshest: the switcher exists to answer "what is running",
    # and alphabetical file order answers it worst. Within each group (running vs ended),
    # tournament arms sharing the same (issue, stamp) cluster stay grouped together
    # ordered by dashboard port -- the cockpit table's order, and the only
    # per-arm sequence the run-number scheme records (its parsed index is None,
    # which would tie every arm and silently fall back to filename order).
    cluster_ages: dict[tuple, float] = {}
    for r in runs:
        t = r.get("tournament")
        if t and "issue" in t and "stamp" in t:
            key = (t["issue"], t["stamp"])
            cluster_ages[key] = min(cluster_ages.get(key, float("inf")), r["heartbeat_age_sec"])

    def _sort_key(r: dict):
        is_running = 0 if r.get("running") else 1
        t = r.get("tournament")
        if t and "issue" in t and "stamp" in t:
            c_age = cluster_ages.get((t["issue"], t["stamp"]), r["heartbeat_age_sec"])
            port = r.get("dash_port")
            port_key = port if isinstance(port, int) and not isinstance(port, bool) else _NO_DASH_PORT
            idx = t.get("index")
            idx_key = idx if isinstance(idx, int) and not isinstance(idx, bool) else _NO_DASH_PORT
            return (is_running, c_age, t["issue"], t["stamp"] or "",
                    port_key, idx_key, str(t.get("arm") or ""))
        return (is_running, r.get("heartbeat_age_sec", 0.0), 0, "",
                _NO_DASH_PORT, _NO_DASH_PORT, "")

    runs.sort(key=_sort_key)
    return runs[:limit]


#: How stale a run listing may be. The scan stats every heartbeat file in the
#: runtime directory, which is well over a thousand on a machine that has been
#: running trials for weeks -- too much to repeat on the 2s status poll, and an
#: operator reading a run list ten seconds old is not misled by it.
SHADOW_RUN_LIST_TTL_S = 10.0


def _recent_shadow_runs(active_db_path: str | None) -> list[dict]:
    """`list_shadow_runs` on the shared snapshot cache, on its own longer TTL."""
    return _cached_snapshot(
        ("shadow-runs", active_db_path),
        lambda: list_shadow_runs(active_db_path),
        ttl=SHADOW_RUN_LIST_TTL_S,
    )


def _recent_shadow_run(active_db_path: str | None) -> dict | None:
    """`read_shadow_run` on the shared snapshot cache, on its own longer TTL."""
    return _cached_snapshot(
        ("shadow-run", active_db_path),
        lambda: read_shadow_run(active_db_path),
        ttl=SHADOW_RUN_LIST_TTL_S,
    )


def _recent_other_live_shadow_runs(active_db_path: str | None) -> list[dict]:
    """`read_other_live_shadow_runs` on the shared snapshot cache."""
    return _cached_snapshot(
        ("other-live-shadow-runs", active_db_path),
        lambda: read_other_live_shadow_runs(active_db_path),
        ttl=SHADOW_RUN_LIST_TTL_S,
    )


def _resolve_shadow_ring_path() -> Path | None:
    """The cycle ring a shadow rehearsal wrote, or None.

    A shadow run sends its per-cycle events to `runtime/shadow-<run_id>.jsonl`
    (`core_brain.shadow_run._run_ring_name`), not to the live `cycle_events.jsonl`
    the screener appends to. When the page is pointed at a shadow store and
    `shadow_run.json` reports a rehearsal for THAT store (RUNNING, FINISHED, or ENDED),
    the ring readers (`/api/scan-state`, `/api/cycle-stream`, `/api/pairs-activity`)
    should follow the rehearsal's ring -- otherwise they read a live ring that
    the rehearsal never touches and the panels look dead.

    Returns None on any doubt: no matching run, no run id, ring not yet on disk,
    or a store this rehearsal is not the one writing.
    """
    try:
        shadow = _recent_shadow_run(str(resolve_db_path(_ACTIVE_DB_OVERRIDE)))
    except Exception:
        return None
    if not shadow:
        return None
    run_id = shadow.get("run_id")
    if not run_id:
        return None
    try:
        from core_brain.shadow_run import _run_ring_name
        ring = resolve_runtime_file(_run_ring_name(str(run_id)), root=LIVE_ROOT)
    except Exception:
        return None
    return ring if ring.exists() else None


def _service_started_at(pid: int | None, info: dict, running: bool) -> float | None:
    """Unix start time for a running service, or None.

    Prefers the OS-reported creation time -- the same source `_is_pid_alive`
    trusts to defeat PID recycling -- and falls back to the time recorded in
    `processes.json` when the OS will not answer. A stopped service has no
    uptime: reporting one would be a lie the STOPPED pill contradicts.
    """
    if not running or not pid:
        return None
    created = _process_start_time(int(pid))
    if created is not None:
        return created
    recorded = info.get("started_at")
    try:
        return float(recorded) if recorded is not None else None
    except (TypeError, ValueError):
        return None


def _uptime_sec(started_at: float | None, now: float | None = None) -> float | None:
    """Seconds a service has been up, or None when it is not running.

    Sent alongside `started_at` so the page can tick a stopwatch against its
    own clock without inheriting any skew between the browser and this host.
    """
    if started_at is None:
        return None
    elapsed = (time.time() if now is None else now) - float(started_at)
    return max(0.0, elapsed)


def _start_stack_commands(sweep_interval_sec: float | None,
                          deadline_ts: float | None = None) -> list[list[str]]:
    """Argv (minus the interpreter) for the three START processes.

    Single source for `start_bot` and the preflight preview: a flag change
    here moves both, so the preview cannot drift from what START launches.
    The guardrail watchdog (`scripts.global_stop_loss`) is supervised by
    `order_manager poll --watch-guardrails`, never as a standalone process.
    """
    query = ["-m", "core_brain.order_manager", "poll", "--interval", "0.5",
             "--watch-guardrails"]
    if sweep_interval_sec is not None:
        query += ["--sweep-interval", f"{sweep_interval_sec:g}"]
    decide = ["-m", "core_brain.trader_loop", "--live",
              "--no-reconcile", "--no-sweep", "--interval", "5"]
    if deadline_ts is not None:
        decide += ["--deadline-ts", str(float(deadline_ts))]
    return [
        ["-m", "scripts.filter_loop"],
        query,
        decide,
    ]


def _atomic_write_json(path, payload: dict) -> None:
    """Write JSON atomically via sibling tmp file + os.replace."""
    import tempfile as _tf

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = _tf.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with open(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2))
            handle.flush()
            try:
                handle.fileno()
                import os as _os
                _os.fsync(handle.fileno())
            except Exception:
                pass
        import os as _os2
        _os2.replace(tmp, path)
    finally:
        try:
            Path(tmp).unlink(missing_ok=True)
        except Exception:
            pass


def _get_next_shadow_seq(tag: str = "") -> str:
    """Next 2-digit shadow run id (01..99), mirroring Get-NextShadowSeq.

    Scans `data/*.db` for `NN_shadow_` (excluding `_shadow_prudent` unless
    `tag == "prudent"`), takes max + 1, wraps past 99 back to 1.
    """
    import re as _re

    data_dir = LIVE_ROOT / "data"
    seqs: list[int] = []
    try:
        names = [p.name for p in data_dir.glob("*.db")]
    except OSError:
        names = []
    for name in names:
        base = name[:-3] if name.endswith(".db") else name
        if tag == "prudent":
            m = _re.match(r"^(\d{1,2})_shadow_prudent", base)
        else:
            if _re.match(r"^(\d{1,2})_shadow_prudent", base):
                continue
            m = _re.match(r"^(\d{1,2})_shadow_", base)
        if m:
            try:
                seqs.append(int(m.group(1)))
            except ValueError:
                continue
    nxt = (max(seqs) + 1) if seqs else 1
    if nxt > 99:
        nxt = 1
    return f"{nxt:02d}"


def _slugify_profile_name(name: str | None) -> str:
    """Filesystem-safe profile slug for shadow db names ("", never "prudent*")."""
    slug = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")[:24]
    if slug.startswith("prudent"):
        slug = ("run-" + slug)[:24]
    return slug.strip("-")


def _staged_shadow_profile() -> tuple[dict | None, str]:
    """Matched staged profile (option, slug) for the next fresh shadow, or (None, "")."""
    pending = _read_pending_profile()
    if not pending:
        return None, ""
    try:
        options = _profile_options()
        match = next((o for o in options["presets"] + options["arms"]
                      if o["kind"] == pending.get("kind")
                      and o["name"] == pending.get("name")), None)
    except Exception:
        match = None
    if match is None:
        return None, ""
    return match, _slugify_profile_name(pending.get("name"))


def _fresh_shadow_naming() -> dict:
    """Next fresh shadow store naming: NN_shadow_<stamp>[_<slug>].db + run id.

    The name is bumped while its file already exists: two STARTs inside the
    same minute (or a retry right after a launch) otherwise share one store,
    and the second run dies on the first run's fleet lock with nothing but a
    cycle-0 heartbeat to show for it.
    """
    match, slug = _staged_shadow_profile()
    stamp = time.strftime("%d-%m_%H-%M", time.localtime())
    seq = _get_next_shadow_seq()
    try:
        seq_int = int(seq)
    except (TypeError, ValueError):
        seq_int = 1
    db_name = ""
    run_id = ""
    for _ in range(100):
        seq = f"{seq_int:02d}" if seq_int <= 99 else str(seq_int)
        db_name = f"{seq}_shadow_{stamp}" + (f"_{slug}" if slug else "") + ".db"
        run_id = f"shadow-{seq}" + (f"-{slug}" if slug else "")
        try:
            if not (LIVE_ROOT / "data" / db_name).exists():
                break
        except OSError:
            break
        seq_int = seq_int + 1 if seq_int < 99 else 1
    return {"seq": seq, "slug": slug, "profile": match,
            "db_name": db_name, "run_id": run_id}


def _ensure_filter_for_shadow() -> str | None:
    """Gap-fill the Market Filter so a fresh rehearsal has a markets file.

    Read-only ranking; shared with the live stack exactly like `start_bot`'s
    gap-fill. Returns a note when it launched the filter, else None. Never
    raises: a filter problem must not block the rehearsal itself. Skips when
    the process registry is unreadable (fail closed: cannot rule out a
    running filter) or a filter is already observed.
    """
    import subprocess

    try:
        status = get_system_status()
        fsvc = (status.get("services") or {}).get("filter", {})
        if fsvc.get("running") or fsvc.get("observed_running"):
            return None
        saved, _err = _read_saved_procs()
        if saved is None:
            return None
        proc = subprocess.Popen(
            [sys.executable, "-m", "scripts.filter_loop"],
            cwd=str(REPO_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **({"start_new_session": True} if sys.platform != "win32" else {}),
        )
        entry = {"pid": proc.pid, "started_at": time.time()}
        merged = dict(saved)
        merged["filter"] = entry
        legacy = {"screener"}
        for key in legacy:
            merged.pop(key, None)
        _atomic_write_json(runtime_file("processes.json", root=LIVE_ROOT), merged)
        return "Market Filter started for this run."
    except Exception:
        return None


def _shadow_fleet_lock_info(db_path: Path) -> dict | None:
    """A fresh-held fleet lock on a shadow target, or None.

    A second rehearsal against the same store dies at startup on the first
    run's lock with nothing but a cycle-0 heartbeat to show for it -- and the
    child used to log to DEVNULL, so the page just flipped back to START.
    This probe mirrors the registry's own rule (`INSTANCE_LOCK_STALE_MS`): a
    lock younger than that refuses, whatever the holder's PID looks like now.
    Best-effort and read-only; any error returns None (fail open) and the
    per-run log carries the verdict instead.
    """
    try:
        if not db_path.exists() or db_path.stat().st_size == 0:
            return None
        from core_brain.order_registry import INSTANCE_LOCK_STALE_MS
        import sqlite3
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
        try:
            row = con.execute(
                "SELECT holder, acquired_ts FROM instance_lock WHERE role = 'fleet'"
            ).fetchone()
        finally:
            con.close()
        if not row:
            return None
        holder, acquired_ts = row[0], row[1]
        try:
            age_ms = int(time.time() * 1000) - int(acquired_ts)
        except (TypeError, ValueError):
            return None
        age_ms = max(0, age_ms)
        if age_ms >= INSTANCE_LOCK_STALE_MS:
            return None
        return {"holder": holder, "age_ms": age_ms,
                "stale_ms": int(INSTANCE_LOCK_STALE_MS)}
    except Exception:
        return None


def _clear_shadow_fleet_lock(target_db: Path, pid: int) -> None:
    """Drop our own fleet lock row after a confirmed shadow stop.

    A SIGKILLed rehearsal never runs its lock release, so the row stays fresh
    for a full STALE window and the next START against that store dies on it.
    Only the row our killed PID wrote is removed (holder is `pid:hex`); a row
    adopted by another run meanwhile is left alone. Best-effort.
    """
    try:
        if resolve_db_identity(target_db)["is_production"]:
            return
        import sqlite3
        con = sqlite3.connect(str(target_db), timeout=5.0)
        try:
            con.execute(
                "DELETE FROM instance_lock WHERE role = 'fleet' AND holder LIKE ?",
                (f"{int(pid)}:%",),
            )
            con.commit()
        finally:
            con.close()
    except Exception:
        pass


def _invalidate_snapshots_for_db(*db_strs: str) -> None:
    """Drop cached snapshots keyed to a store that just changed state.

    Without this the next poll (10s TTL on shadow-run keys, longer for ended
    stores) keeps serving the pre-start record and the master toggle flickers
    back to START right after a successful launch.
    """
    wants = {s for s in db_strs if s}
    if not wants:
        return
    try:
        for key in list(_snapshots.keys()):
            try:
                if any(isinstance(part, str) and part in wants for part in key):
                    _snapshots.pop(key, None)
            except Exception:
                continue
    except Exception:
        pass


SERVICE_NAMES = ("filter", "query", "decide")
_SERVICE_CWDS = {"filter": "repo", "query": "live", "decide": "live"}


def _service_cwd(name: str) -> str:
    """Working directory a service spawns under, matching `start_bot`."""
    return str(REPO_ROOT if _SERVICE_CWDS[name] == "repo" else LIVE_ROOT)


def _acquire_ops_lock():
    """Take the interprocess start/stop lock; return (fd, None) or (None, error)."""
    import time as _time

    lock_file = runtime_file(".bot_start.lock", root=LIVE_ROOT)
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.write(fd, f"{os.getpid()}\n".encode())
        return fd, None
    except FileExistsError:
        pass
    except Exception as e:
        return None, f"Failed to acquire startup lock: {e}"
    try:
        if not lock_file.exists():
            try:
                fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.write(fd, f"{os.getpid()}\n".encode())
                return fd, None
            except Exception:
                return None, "Failed to acquire startup lock after retry; another start may be running."
        lock_age = _time.time() - lock_file.stat().st_mtime
        if lock_age > 30:
            try:
                lock_file.unlink()
            except OSError:
                pass
            try:
                fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.write(fd, f"{os.getpid()}\n".encode())
                return fd, None
            except FileExistsError:
                return None, "Failed to acquire startup lock after removing stale lock; another start won the race."
        return None, "Another start/stop request is in progress; refusing concurrent operation."
    except Exception:
        return None, "Failed to acquire startup lock; another start may be running."


def _release_ops_lock(lock_fd) -> None:
    """Release a lock taken by `_acquire_ops_lock`; no-op when None."""
    if lock_fd is None:
        return
    lock_file = runtime_file(".bot_start.lock", root=LIVE_ROOT)
    try:
        os.close(lock_fd)
    except Exception:
        pass
    try:
        lock_file.unlink()
    except Exception:
        pass


def _refuse_unless_live_writable() -> dict | None:
    """The gates every stack-changing op shares: production DB, readable registry."""
    db_identity = resolve_db_identity(resolve_db_path(_ACTIVE_DB_OVERRIDE))
    if not db_identity["is_production"]:
        from core_brain.order_registry import DEFAULT_DB_PATH
        return {
            "ok": False,
            "message": (
                f"This dashboard is reading {db_identity['path']} "
                f"({db_identity['mode']}), not the production registry "
                f"{DEFAULT_DB_PATH}. Service controls act on the live stack, "
                f"so running them from here would be invisible. Use the live "
                f"dashboard at http://127.0.0.1:8799/ (restart without --db / "
                f"LIVE_DB_PATH)."
            ),
            "status": get_system_status(),
        }
    current = get_system_status()
    if current.get("bot_state") == "UNKNOWN":
        return {
            "ok": False,
            "message": (
                f"Cannot read the process file at {current.get('registry_path')}; "
                "refusing until it is readable, because a second live stack "
                "cannot be ruled out."
            ),
            "status": current,
        }
    return None


def _read_saved_procs() -> tuple[dict | None, str | None]:
    """The recorded stack, or (None, error) when it cannot be read."""
    procs_file = resolve_runtime_file("processes.json", root=LIVE_ROOT)
    if not procs_file.exists():
        return {}, None
    try:
        loaded = json.loads(procs_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, (
            f"Cannot read the process file at {procs_file}; refusing to change "
            "the stack until it is readable."
        )
    if not isinstance(loaded, dict):
        return None, (
            f"Cannot read the process file at {procs_file}; refusing to change "
            "the stack until it is readable."
        )
    return loaded, None


def start_service(name: str) -> dict:
    """Launch one stack service (filter, query, or decide) on its own.

    Each dashboard toggle drives exactly one service: starting Decide no
    longer drags Filter and Query up with it, and starting Filter never
    rests a bid. The master START RUN (`start_bot`) still launches the
    whole stack at once.
    """
    import subprocess

    if name not in SERVICE_NAMES:
        return {"ok": False, "message": f"Unknown service {name!r}; expected one of {', '.join(SERVICE_NAMES)}.",
                "status": get_system_status()}
    refusal = _refuse_unless_live_writable()
    if refusal is not None:
        return refusal

    lock_fd, lock_err = _acquire_ops_lock()
    if lock_fd is None:
        return {"ok": False, "message": lock_err, "status": get_system_status()}
    proc = None
    try:
        current = get_system_status()
        if current.get("services", {}).get(name, {}).get("running"):
            return {"ok": False, "message": f"Service {name} is already running; refusing a duplicate.",
                    "status": current}

        saved_procs, read_err = _read_saved_procs()
        if saved_procs is None:
            return {"ok": False, "message": read_err, "status": get_system_status()}

        from core_brain.order_registry import get_run_id
        child_env = {**os.environ, "SH_RUN_ID": get_run_id()}
        sweep = resolve_sweep_interval()
        idx = SERVICE_NAMES.index(name)
        extra = {"sweep_interval_sec": sweep} if name == "query" else {}
        proc = subprocess.Popen(
            [sys.executable, *_start_stack_commands(sweep)[idx]],
            cwd=_service_cwd(name),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=child_env,
        )
        saved_procs[name] = {"pid": proc.pid, "started_at": time.time(), **extra}
        procs_file = runtime_file("processes.json", root=LIVE_ROOT)
        procs_file.parent.mkdir(parents=True, exist_ok=True)
        procs_file.write_text(json.dumps(saved_procs, indent=2), encoding="utf-8")
        _capture_starting_capital()
        return {"ok": True, "message": f"Service {name} started", "status": get_system_status()}
    except Exception as e:
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        return {"ok": False, "message": f"Failed to start service {name}: {e}",
                "status": get_system_status()}
    finally:
        _release_ops_lock(lock_fd)


def stop_service(name: str) -> dict:
    """Terminate one stack service, leaving the others and the registry alone."""
    import subprocess

    if name not in SERVICE_NAMES:
        return {"ok": False, "message": f"Unknown service {name!r}; expected one of {', '.join(SERVICE_NAMES)}.",
                "status": get_system_status()}

    lock_fd, lock_err = _acquire_ops_lock()
    if lock_fd is None:
        return {"ok": False, "message": lock_err, "status": get_system_status()}
    try:
        saved_procs, read_err = _read_saved_procs()
        if saved_procs is None:
            return {"ok": False, "message": read_err, "status": get_system_status()}

        info = service_entry(saved_procs, name)
        pid = info.get("pid")
        alive = bool(pid and _is_pid_alive(pid, info.get("started_at")))
        if alive:
            try:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
                else:
                    os.kill(int(pid), 15)
            except Exception:
                pass
        saved_procs.pop(name, None)
        # A legacy key (screener/engine/fleet) may hold this service's
        # record when the stack predates the rename; drop it too so the
        # next status poll does not resurrect the entry.
        for legacy in ({"filter": ("screener",), "query": ("engine",), "decide": ("fleet",)}[name]):
            saved_procs.pop(legacy, None)
        procs_file = runtime_file("processes.json", root=LIVE_ROOT)
        procs_file.parent.mkdir(parents=True, exist_ok=True)
        procs_file.write_text(json.dumps(saved_procs, indent=2), encoding="utf-8")
        if alive:
            return {"ok": True, "message": f"Service {name} stopped", "status": get_system_status()}
        return {"ok": True, "message": f"Service {name} was not running", "status": get_system_status()}
    finally:
        _release_ops_lock(lock_fd)


def _runtime_file_age_sec(name: str) -> float | None:
    """Seconds since a runtime file last changed, or None when it is unreadable."""
    try:
        return max(0.0, time.time() - resolve_runtime_file(name, root=LIVE_ROOT).stat().st_mtime)
    except OSError:
        return None


def _read_trader_profile(active_db_path: str | None, decide_pid=None) -> dict | None:
    """The live trader's recorded profile (#856), or None.

    Trusted only when the recording process is alive, registered as the
    `decide` entry in the process registry, and writing the active store --
    otherwise a stale file would label the wrong process. Any failure reads
    as missing, never as a wrong name.
    """
    try:
        raw = json.loads(resolve_runtime_file(
            TRADER_PROFILE_FILE, root=LIVE_ROOT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    profile = raw.get("profile")
    if not isinstance(profile, dict) or not isinstance(profile.get("name"), str):
        return None
    try:
        pid_int = int(raw.get("pid"))
    except (TypeError, ValueError):
        return None
    if isinstance(raw.get("pid"), bool) or pid_int <= 0:
        return None
    if not _is_pid_alive(pid_int, raw.get("started_at")):
        return None
    try:
        if decide_pid is None or int(decide_pid) != pid_int:
            return None
    except (TypeError, ValueError):
        return None
    if not _same_path(raw.get("db_path"), active_db_path):
        return None
    preset = profile.get("preset")
    try:
        queue_min = profile.get("queue_max_clear_min")
        queue_min = float(queue_min) if queue_min is not None else None
    except (TypeError, ValueError):
        queue_min = None
    return {
        "name": profile["name"],
        "preset": preset if isinstance(preset, str) else None,
        "queue_clear_on": bool(profile.get("queue_clear_on")),
        "queue_max_clear_min": queue_min,
        "source": "live",
        "running": True,
    }


def _tournament_arms_configs() -> list[dict]:
    """Arm definitions from configs/tournaments/*_arms.json (#856 IIIB).

    Each arm carries the env block that reproduces it, so staging an arm is
    replaying recorded settings, not inventing new ones. Unreadable files are
    skipped: a broken arms file must not hide the healthy ones.
    """
    arms: list[dict] = []
    try:
        cfg_dir = REPO_ROOT / "configs" / "tournaments"
        paths = sorted(cfg_dir.glob("*_arms.json"))
    except OSError:
        return []
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, list):
            continue
        for entry in data:
            if not isinstance(entry, dict) or not entry.get("name"):
                continue
            raw_env = entry.get("env") or {}
            env = {str(k): str(v) for k, v in raw_env.items()
                   if isinstance(k, str)} if isinstance(raw_env, dict) else {}
            arms.append({
                "kind": "arm",
                "name": str(entry["name"]),
                "label": str(entry.get("label") or entry["name"]),
                "profile": str(entry.get("profile") or ""),
                "env": env,
                "source": path.name,
            })
    return arms


def _arm_stores(now: float | None = None) -> dict[str, str]:
    """Freshest on-disk heartbeat store per tournament arm (#856 IIIB).

    One scan shared by the options listing and the view lookup, so N arms do
    not pay N directory walks. Temporary/test stores are never offered.
    """
    from core_brain.shadow_run import parse_tournament_db_path
    now = time.time() if now is None else now
    best: dict[str, tuple[float, str]] = {}
    for path in _shadow_heartbeat_candidates():
        try:
            if now - path.stat().st_mtime > SHADOW_RUN_LIST_WINDOW_S:
                continue
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(raw, dict):
            continue
        tournament = parse_tournament_db_path(raw.get("db_path"))
        if not isinstance(tournament, dict) or not tournament.get("arm"):
            continue
        try:
            ts = float(raw.get("heartbeat_ts", 0.0))
        except (TypeError, ValueError):
            continue
        db_path = raw.get("db_path")
        if not db_path or _is_temp_or_test_db(db_path):
            continue
        try:
            if not Path(db_path).is_file():
                continue
        except (OSError, ValueError, TypeError):
            continue
        arm = str(tournament["arm"])
        if ts > best.get(arm, (-1.0, ""))[0]:
            best[arm] = (ts, str(db_path))
    return {arm: db for arm, (_, db) in best.items()}


def _profile_options() -> dict:
    """Every loadable profile: code presets plus tournament arms (#856 IIIB)."""
    from core_brain.config import TOURNAMENT_PRESETS
    presets = [{
        "kind": "preset",
        "name": name,
        "label": name,
        "detail": str(preset.get("description", "")),
        "env": {"BOT_QUOTE_TOURNAMENT_PRESET": name},
    } for name, preset in TOURNAMENT_PRESETS.items()]
    stores = _arm_stores()
    arms = []
    for arm in _tournament_arms_configs():
        arm["has_store"] = arm["name"] in stores
        arms.append(arm)
    return {"presets": presets, "arms": arms}


def _resolve_arm_store(arm_name: str | None, now: float | None = None) -> str | None:
    """The freshest heartbeat store recorded for a tournament arm (#856 IIIB).

    View-only navigation evidence: the page re-points at the store the arm is
    actually writing. A name with no heartbeat has no store to show.
    """
    if not arm_name:
        return None
    return _arm_stores(now=now).get(str(arm_name))


def _read_pending_profile() -> dict | None:
    """The staged profile for the next START, or None (#856 IIIB)."""
    try:
        raw = json.loads(resolve_runtime_file(
            PENDING_PROFILE_FILE, root=LIVE_ROOT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    if not isinstance(raw.get("kind"), str) or not isinstance(raw.get("name"), str):
        return None
    return {
        "kind": raw["kind"],
        "name": raw["name"],
        "staged_at": raw.get("staged_at"),
    }


def _resolve_active_profile(shadow_run: dict | None) -> dict | None:
    """The engine-recorded profile for the selected store (#856), or None.

    Only an engine record counts: the dashboard never derives the profile
    from its own environment, whose variables the launcher does not set.
    A heartbeat without a well-formed `profile` (older code, malformed
    record) reports as missing, never as a wrong name.
    """
    try:
        if not isinstance(shadow_run, dict):
            return None
        profile = shadow_run.get("profile")
        if not isinstance(profile, dict) or not isinstance(profile.get("name"), str):
            return None
        preset = profile.get("preset")
        try:
            queue_min = profile.get("queue_max_clear_min")
            queue_min = float(queue_min) if queue_min is not None else None
        except (TypeError, ValueError):
            queue_min = None
        tournament = shadow_run.get("tournament")
        arm = tournament.get("arm") if isinstance(tournament, dict) else None
        resolved = {
            "name": profile["name"],
            "preset": preset if isinstance(preset, str) else None,
            "queue_clear_on": bool(profile.get("queue_clear_on")),
            "queue_max_clear_min": queue_min,
            "source": "shadow",
            "running": bool(shadow_run.get("running")),
        }
        if isinstance(arm, str) and arm:
            resolved["tournament_arm"] = arm
        return resolved
    except (ValueError, OSError):
        return None


def _watchdog_summary() -> dict:
    """Lightweight watchdog state for status payloads; never raises."""
    try:
        health = _guardrail_health()
    except Exception:
        return {"running": False, "age_s": None, "alerts_total": 0}
    if not isinstance(health, dict):
        return {"running": False, "age_s": None, "alerts_total": 0}
    return {
        "running": bool(health.get("running")),
        "age_s": health.get("age_s"),
        "alerts_total": health.get("alerts_total", 0),
        "pid": health.get("pid"),
    }


def get_system_status() -> dict:
    """Return live running status for 3 sub-services (Market Filter, Query Polymarket, Decide & Execute) and Telemetry."""
    procs_file = resolve_runtime_file("processes.json", root=LIVE_ROOT)
    saved_procs: dict[str, Any] = {}
    # A registry we cannot read is NOT an empty registry. Reporting STOPPED for
    # a truncated or half-written processes.json is how START gets permission to
    # launch a second live Trader beside the running one, so this path fails
    # closed: bot_state becomes UNKNOWN and every control that guards on
    # "not RUNNING" refuses until the file is readable again.
    registry_unreadable = False
    if procs_file.exists():
        try:
            loaded = json.loads(procs_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            registry_unreadable = True
        else:
            if isinstance(loaded, dict):
                saved_procs = loaded
            else:
                registry_unreadable = True

    # service_entry accepts the pre-rename keys (screener/engine/fleet) so a
    # stack started before the rename is still reported as RUNNING. Without
    # that, start_bot's "already running" guard would wave through a second
    # live Trader.
    filter_info = service_entry(saved_procs, "filter")
    filter_pid = filter_info.get("pid")
    filter_running = _is_pid_alive(filter_pid, filter_info.get("started_at"))

    query_info = service_entry(saved_procs, "query")
    query_pid = query_info.get("pid")
    query_running = _is_pid_alive(query_pid, query_info.get("started_at"))

    decide_info = service_entry(saved_procs, "decide")
    decide_pid = decide_info.get("pid")
    decide_running = _is_pid_alive(decide_pid, decide_info.get("started_at"))

    # A Market Filter started outside the process registry is still a running
    # filter: the tournament launcher spawns `scripts.filter_loop` directly, so
    # the registry has no entry for it and the page painted OFF while it
    # re-ranked the universe every cycle. `running` stays registry-only -- it is
    # what refuses a second start, so it must keep failing closed -- and this
    # second flag carries the observable truth for the display. The filter's own
    # output is the evidence: a snapshot inside two cycles means it is working.
    filter_observed_age = _runtime_file_age_sec("pipeline.json")
    filter_observed_running = bool(
        not filter_running
        and filter_observed_age is not None
        and filter_observed_age <= resolve_scan_interval() * 2
    )

    filter_started_at = _service_started_at(filter_pid, filter_info, filter_running)
    query_started_at = _service_started_at(query_pid, query_info, query_running)
    decide_started_at = _service_started_at(decide_pid, decide_info, decide_running)

    configured_sweep_interval = resolve_sweep_interval()
    running_sweep_interval = query_info.get("sweep_interval_sec") if query_running else None

    dash_running = True
    dash_pid = os.getpid()
    dash_started_at = _process_start_time(dash_pid)
    # One walk of core_brain/ + scoring/ per request: it is the whole staleness
    # verdict, and the status endpoint is polled continuously.
    tree_mtime = code_revision.decision_code_mtime()
    dash_code_stale = bool(
        dash_started_at is not None and tree_mtime is not None and dash_started_at < tree_mtime
    )

    bot_running = bool(filter_running or query_running or decide_running)

    db_identity = resolve_db_identity(resolve_db_path(_ACTIVE_DB_OVERRIDE))

    _bot_state = (
        "UNKNOWN" if registry_unreadable
        else ("RUNNING" if bot_running else "STOPPED")
    )

    # The selected store's rehearsal, read once for the stopwatch and the
    # PROFILE badge alike. The profile below comes only from this record --
    # never from this process's environment (#856).
    selected_shadow_run = _recent_shadow_run(db_identity["path"])
    try:
        active_profile = _resolve_active_profile(selected_shadow_run)
        if active_profile is None:
            # No shadow record for this store: fall back to the live
            # trader's record. Shadow keeps priority when both exist.
            active_profile = _read_trader_profile(db_identity["path"], decide_pid)
    except (ValueError, OSError):
        active_profile = None

    return {
        "services": {
            "filter": {
                "name": "Market Filter",
                "running": filter_running,
                "pid": filter_pid if filter_running else None,
                "started_at": filter_started_at,
                "uptime_sec": _uptime_sec(filter_started_at),
                # Display-only: the filter is demonstrably working without a
                # registry entry. Never read this for start/stop decisions.
                "observed_running": filter_observed_running,
                "observed_age_sec": filter_observed_age,
            },
            "query": {
                "name": "Query Polymarket",
                "running": query_running,
                "pid": query_pid if query_running else None,
                "started_at": query_started_at,
                "uptime_sec": _uptime_sec(query_started_at),
                "sweep_interval_sec": configured_sweep_interval,
                "running_sweep_interval_sec": running_sweep_interval,
            },
            "decide": {
                "name": "Decide & Execute",
                "running": decide_running,
                "pid": decide_pid if decide_running else None,
                "started_at": decide_started_at,
                "uptime_sec": _uptime_sec(decide_started_at),
            },
            "dash": {
                "name": "Telemetry (dash)",
                "running": dash_running,
                "pid": dash_pid,
                "started_at": dash_started_at,
                "uptime_sec": _uptime_sec(dash_started_at),
                "port": _ACTIVE_PORT,
            },
        },
        "bot_state": _bot_state,
        # How often the Market Filter re-ranks, so the page ages its snapshot
        # against the configured cadence instead of a constant.
        "scan_interval_sec": resolve_scan_interval(),
        "registry_path": str(procs_file),
        "registry_unreadable": registry_unreadable,
        # Which store these numbers came from. The page renders identically
        # against the production registry and against a shadow rehearsal, so
        # the mode has to travel with the data rather than live in the operator's
        # memory of how they launched the server.
        "db_path": db_identity["path"],
        "db_mode": db_identity["mode"],
        "db_is_production": db_identity["is_production"],
        # The rehearsal writing this store, when there is one. None otherwise:
        # the header shows no stopwatch rather than another run's clock.
        "shadow_run": selected_shadow_run,
        # The active engine profile for the PROFILE badge (#856): the shadow
        # record when the selected store has one, else null (T3 adds the
        # live fallback). Never derived from this process's environment.
        "active_profile": active_profile,
        # The staged profile for the next START, if any (#856 IIIB).
        "pending_profile": _read_pending_profile(),
        # The OTHER rehearsals on this machine, including live ones writing a
        # store this page is not pointed at. Named, never read into the numbers
        # above: without them a dead run on a stale store reads as "the engine
        # is down" while a healthy trial writes to a file nobody is watching.
        "shadow_runs": _recent_shadow_runs(db_identity["path"]),
        "starting_capital": get_starting_capital(),
        "run": saved_procs.get("run") if isinstance(saved_procs, dict) else None,
        "watchdog": _watchdog_summary(),
        "repo_revision": _DASH_CODE,
        "dash_revision": {
            "commit": _DASH_CODE.get("commit"),
            "dirty": _DASH_CODE.get("dirty"),
            "started_at": dash_started_at,
            "code_mtime": tree_mtime,
            "code_stale": dash_code_stale,
        },
        "timestamp": time.time(),
    }


def _save_starting_account_value(val: float) -> None:
    """Safely persist starting_account_value into processes.json if not already set."""
    procs_file = runtime_file("processes.json", root=LIVE_ROOT)
    saved = {}
    if procs_file.exists():
        try:
            saved = json.loads(procs_file.read_text(encoding="utf-8"))
        except Exception:
            saved = {}
    if not isinstance(saved, dict):
        saved = {}
    if "starting_account_value" not in saved or saved.get("starting_account_value") is None:
        saved["starting_account_value"] = val
        procs_file.parent.mkdir(parents=True, exist_ok=True)
        procs_file.write_text(json.dumps(saved, indent=2), encoding="utf-8")


def _capture_starting_capital() -> float | None:
    """Snapshot the real account equity from the venue -- LIVE stores only.

    Returns the venue-reported account_value_usd, or None if the venue is
    unreachable. Updates processes.json with starting_account_value if not set.
    Never raises -- a failed balance read at start time must not block the
    bot or dashboard from launching.

    A SHADOW store is refused outright (#422): the venue read writes an account
    mark into the rehearsal's isolated database and reports real capital, which
    contradicts the fixed starting bankroll a rehearsal runs under. UNKNOWN
    fails closed the same way -- no sweep we cannot prove belongs to the live
    page. `start_bot` and the reset flow call this for the live stack, so their
    behaviour is unchanged.
    """
    db_path = resolve_db_path(_ACTIVE_DB_OVERRIDE)
    if resolve_db_identity(db_path)["mode"] != "LIVE":
        return None
    try:
        from core_brain.order_manager import account_sweep
        result = account_sweep(quiet=True, db_path=str(db_path))
        if isinstance(result, dict) and result.get("account_value_usd") is not None:
            val = float(result["account_value_usd"])
            _save_starting_account_value(val)
            return val
    except (Exception, SystemExit):
        # account_sweep exits with SystemExit when POLY_FUNDER is unset (a
        # missing funder must not block the bot from launching) and raises
        # RuntimeError from the test socket guard / venue failures otherwise.
        # Both are a failed balance read: skip the snapshot, keep starting.
        pass
    return None


def get_starting_capital() -> float | None:
    """Read the starting capital snapshot from processes.json.

    Returns the account_value_usd captured at bot-start time, or None if
    no snapshot exists (bot never started, or venue was unreachable).
    """
    procs_file = resolve_runtime_file("processes.json", root=LIVE_ROOT)
    if not procs_file.exists():
        return None
    try:
        saved = json.loads(procs_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Same rule as get_system_status: an unreadable registry is not an
        # empty one. There is no number to report, and the caller renders the
        # config bankroll label instead.
        return None
    if not isinstance(saved, dict):
        return None
    val = saved.get("starting_account_value")
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def start_bot(preflight_id: str | None = None,
            minutes: float | None = None) -> dict:
    """Launch background Screener and Reconcile loop.

    `minutes` sets the horizon: the Decide command carries `--deadline-ts`
    and the run record lands in `processes.json`. `None` keeps the legacy
    unbounded stack (service starts, older callers, existing tests).
    """
    import subprocess

    # The stack always writes the production registry, whatever this page is
    # reading. Started from a shadow view, real maker bids would rest behind a
    # page that cannot show a single one of them -- no orders, no fills, no
    # exposure bar -- which reads as "nothing happened". Refuse before the
    # start lock, so a refusal leaves nothing for a later live start to clear.
    db_identity = resolve_db_identity(resolve_db_path(_ACTIVE_DB_OVERRIDE))
    if not db_identity["is_production"]:
        from core_brain.order_registry import DEFAULT_DB_PATH
        return {
            "ok": False,
            "message": (
                f"This dashboard is reading {db_identity['path']} "
                f"({db_identity['mode']}), not the production registry "
                f"{DEFAULT_DB_PATH}. START launches the live stack against the "
                f"production registry, so its orders would be invisible here. "
                f"Use the live dashboard at http://127.0.0.1:8799/ (restart "
                f"without --db / LIVE_DB_PATH) to start the stack."
            ),
            "status": get_system_status(),
        }

    # One instance at a time (AGENTS.md): two stacks on one database sum their
    # independent inventories into silently invalid data. The page disables the
    # button while RUNNING, but a double click in the poll gap, a reload, or a
    # direct POST all bypass button state -- and live_procs.json only remembers
    # the newest PIDs, so stop_bot could never reach the first pair.
    # The ops lock is shared with start_service/stop_service/stop_bot: without
    # it a master STOP can unlink processes.json while a per-service START
    # holds it and then rewrites a dead PID back to life.
    lock_fd, lock_err = _acquire_ops_lock()
    if lock_fd is None:
        return {"ok": False, "message": lock_err, "status": get_system_status()}

    launched_procs = []
    try:
        # Re-check status now that we hold the lock.
        current = get_system_status()
        # Anything but a confirmed STOPPED refuses. UNKNOWN means the process
        # registry could not be read, and a start on that state is exactly the
        # duplicate-stack case this guard exists to prevent.
        svcs = current.get("services", {})
        filter_alive = svcs.get("filter", {}).get("running", False)
        query_alive = svcs.get("query", {}).get("running", False)
        decide_alive = svcs.get("decide", {}).get("running", False)

        # Issue #457: a partial stack is fillable, not a refusal. Refuse only a
        # COMPLETE duplicate stack (all three alive), or a RUNNING verdict with
        # no per-service state to reason from. Otherwise launch the gap.
        if filter_alive and query_alive and decide_alive:
            return {"ok": False, "message": "Bot stack is already running; refusing to start a duplicate instance.", "status": current}
        if current.get("bot_state") == "RUNNING" and not (filter_alive or query_alive or decide_alive):
            return {"ok": False, "message": "Bot stack is already running; refusing to start a duplicate instance.", "status": current}
        if current.get("bot_state") == "UNKNOWN":
            return {"ok": False, "message": f"Cannot read the process file at {current.get('registry_path')}; refusing to start until it is readable, because a second live stack cannot be ruled out.", "status": current}

        procs_file = runtime_file("processes.json", root=LIVE_ROOT)
        procs_file.parent.mkdir(parents=True, exist_ok=True)

        # Load the registry BEFORE any alive check: the `else` branches below
        # read saved_procs to keep a running service's entry, and reading it
        # after those branches is the NameError that made every partial-stack
        # START crash instead of filling the gap.
        saved_procs, read_err = _read_saved_procs()
        if saved_procs is None:
            return {"ok": False, "message": read_err, "status": current}

        # Derive a stable run_id so fleet/exec/dash share one session id.
        # Without this, each process generates its own UUID at import time
        # and fills/orders are tagged to inconsistent run_ids, which makes
        # the dashboard default run selector show a misleading zeros grid.
        from core_brain.order_registry import get_run_id
        child_env = {**os.environ, "SH_RUN_ID": get_run_id()}

        # A staged profile from POST /api/profile/stage (#856 IIIB): the file
        # carries identity only (kind + name); the env is re-resolved from
        # the current options, so a stale or hand-edited file can never
        # smuggle SH_RUN_ID, CLOB_HOST, or rehearsal-only keys into the
        # stack. This process's own environment stays untouched, so the
        # badge keeps reading engine records.
        pending_path = None
        staged_profile_name = None
        try:
            pending_path = resolve_runtime_file(PENDING_PROFILE_FILE, root=LIVE_ROOT)
            if pending_path.exists():
                pending = json.loads(pending_path.read_text(encoding="utf-8"))
                if isinstance(pending, dict):
                    options = _profile_options()
                    match = next((o for o in options["presets"] + options["arms"]
                                  if o["kind"] == pending.get("kind")
                                  and o["name"] == pending.get("name")), None)
                    if match is not None:
                        child_env.update(match["env"])
                        child_env["SH_RUN_ID"] = get_run_id()
                        staged_profile_name = match["name"]
                    else:
                        logger.warning("staged profile %r no longer matches a known "
                                     "profile, starting unstaged",
                                     {k: pending.get(k) for k in ("kind", "name")})
        except (OSError, ValueError) as e:
            logger.warning("staged profile unreadable, starting unstaged: %s", e)
            pending_path, staged_profile_name = None, None

        # POSIX only: a new session makes each launched service a process-group
        # leader, so STOP can signal the whole tree (ranker/watcher children
        # included) instead of just the root.
        popen_group = {"start_new_session": True} if sys.platform != "win32" else {}

        deadline_ts: float | None = None
        run_minutes: float | None = None
        if minutes is not None:
            try:
                run_minutes = float(minutes)
            except (TypeError, ValueError):
                run_minutes = None
        if run_minutes is not None and run_minutes > 0:
            deadline_ts = time.time() + run_minutes * 60.0
        stack_cmds = _start_stack_commands(resolve_sweep_interval(), deadline_ts)

        # Launch Market Filter (filter_loop) if not running
        if not filter_alive:
            p_scr = subprocess.Popen(
                [sys.executable, *stack_cmds[0]],
                cwd=str(REPO_ROOT),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=child_env,
                **popen_group,
            )
            launched_procs.append(p_scr)
            filter_entry = {"pid": p_scr.pid, "started_at": time.time()}
        else:
            filter_entry = service_entry(saved_procs, "filter")

        # Launch Query Polymarket loop if not running
        if not query_alive:
            sweep_interval = resolve_sweep_interval()
            p_eng = subprocess.Popen(
                [sys.executable, *stack_cmds[1]],
                cwd=str(LIVE_ROOT),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=child_env,
                **popen_group,
            )
            launched_procs.append(p_eng)
            query_entry = {"pid": p_eng.pid, "started_at": time.time(),
                           "sweep_interval_sec": sweep_interval}
        else:
            query_entry = service_entry(saved_procs, "query")

        # Launch Decide & Execute loop if not running
        if not decide_alive:
            p_fleet = subprocess.Popen(
                [sys.executable, *stack_cmds[2]],
                cwd=str(LIVE_ROOT),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=child_env,
                **popen_group,
            )
            launched_procs.append(p_fleet)
            decide_entry = {"pid": p_fleet.pid, "started_at": time.time()}
        else:
            decide_entry = service_entry(saved_procs, "decide")

        # Preserve starting_account_value: a partial START must not re-snapshot
        # capital the stack already captured at its real start.
        existing_starting_value = None
        if procs_file.exists():
            try:
                existing_data = json.loads(procs_file.read_text(encoding="utf-8"))
                existing_starting_value = existing_data.get("starting_account_value")
            except Exception:
                pass
        partial = bool(launched_procs) and any(
            (filter_alive, query_alive, decide_alive))

        new_procs = {
            "filter": filter_entry,
            "query": query_entry,
            "decide": decide_entry,
        }
        # Capture starting capital: a real snapshot of account equity at the
        # moment the bot is toggled ON. The kpi.py report uses
        # _CFG.bot_quote_bank_baseline_usd (a hardcoded config constant) as its baseline,
        # but the comment at kpi.py:889 explicitly says that's "a paper-run
        # constant that nobody deposited." This snapshot is the real number.
        # It may be None if the venue is unreachable at start time; the
        # dashboard shows a "estimated baseline" label in that case.
        if not partial:
            starting_account_value = _capture_starting_capital()
            if starting_account_value is not None:
                new_procs["starting_account_value"] = starting_account_value
            elif existing_starting_value is not None:
                # Preserve previously captured value if current capture fails
                new_procs["starting_account_value"] = existing_starting_value
        elif existing_starting_value is not None:
            new_procs["starting_account_value"] = existing_starting_value
        run_record: dict | None = None
        if run_minutes is not None and run_minutes > 0:
            started_at = time.time()
            stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime(started_at))
            run_id = f"live-{stamp}-{int(run_minutes)}m"
            run_record = {
                "run_id": run_id,
                "minutes": run_minutes,
                "started_at": started_at,
                "deadline_at": started_at + run_minutes * 60.0,
                "expired": False,
            }
            if preflight_id:
                run_record["preflight_id"] = preflight_id
            new_procs["run"] = run_record
        else:
            existing_run = None
            try:
                if procs_file.exists():
                    existing_run = json.loads(procs_file.read_text(encoding="utf-8")).get("run")
            except Exception:
                existing_run = None
            if isinstance(existing_run, dict):
                new_procs["run"] = existing_run
        _atomic_write_json(procs_file, new_procs)

        reused = [n for n, alive in
                  (("filter", filter_alive), ("query", query_alive),
                   ("decide", decide_alive))
                  if alive]
        staged_msg = ""
        if staged_profile_name and pending_path is not None:
            try:
                pending_path.unlink(missing_ok=True)
            except OSError:
                pass
            staged_msg = f" with staged profile {staged_profile_name}"
        return {
            "ok": True,
            "message": f"Bot stack started{staged_msg}",
            "launched": [n for n, alive in
                         (("filter", not filter_alive), ("query", not query_alive),
                          ("decide", not decide_alive)) if alive],
            "reused": reused,
            "status": get_system_status(),
        }
    except Exception as e:
        # Cleanup: terminate only the children THIS call launched, through the
        # same honest stop helper STOP uses. A reused service was already
        # running before START and must survive a failed fill; stopping it
        # would turn a gap-fill failure into an outage.
        launched_targets = [
            (name, proc.pid, None)
            for name, proc in zip(
                [n for n, alive in
                 (("filter", not filter_alive), ("query", not query_alive),
                  ("decide", not decide_alive)) if alive],
                launched_procs,
            )
        ]
        _stop_services(launched_targets, subprocess)
        return {
            "ok": False,
            "message": f"Failed to start bot stack: {e}",
            "status": get_system_status(),
        }
    finally:
        _release_ops_lock(lock_fd)


def stop_bot() -> dict:
    """Terminate background Filter, Query, and Decide loops.

    Reads through resolve_runtime_file, so a stack recorded in the pre-rename
    run/live_procs.json is still reachable. The loop below walks whatever keys
    the file holds, which covers the old screener/engine/fleet names too.

    Holds the ops lock: without it a concurrent per-service start can write a
    fresh PID into processes.json between our kill loop and the unlink, and
    that entry dies with the file while its process keeps running.
    """
    import subprocess
    lock_fd, lock_err = _acquire_ops_lock()
    if lock_fd is None:
        return {"ok": False, "message": lock_err, "status": get_system_status()}
    try:
        return _stop_bot_locked(subprocess)
    finally:
        _release_ops_lock(lock_fd)


def _stop_bot_locked(subprocess) -> dict:
    """The stop itself; caller must hold the ops lock.

    Issue #457: STOP is now honest. Ask politely (SIGTERM / taskkill /T), wait a
    bounded grace period, escalate to force (SIGKILL / taskkill /F /T), verify
    each service is actually down, and report one outcome per service. The
    registry is REWRITTEN rather than deleted, so `starting_account_value` and
    any survivor's entry survive the stop and a retry can reach them.

    Known acceptance gaps (deliberate, not full coverage):
    - Whole-tree stop is complete only on Linux/macOS for services launched by
      this master START (they own a new process group). Legacy records and
      service-card launches are single processes, so their children can
      survive as orphans.
    - On Windows the down-check confirms only the root process; descendant
      death relies on taskkill /T and its return code.
    - A zombie owned by another parent can still look alive; STOP then reports
      `still_running`, which is the safe, honest answer.
    - Stopping processes does not cancel resting venue orders.
    """
    procs_file = resolve_runtime_file("processes.json", root=LIVE_ROOT)
    if procs_file.exists():
        try:
            saved_procs = json.loads(procs_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved_procs = None
        # A registry we cannot read is not an empty one. Treating it as {} kills
        # nothing, deletes the file, and reports "stopped" -- after which the
        # reset path archives the database while the original processes are
        # still writing to it. Keep the file and fail.
        if not isinstance(saved_procs, dict):
            return {
                "ok": False,
                "message": (
                    f"Cannot read the process file at {procs_file}; refusing to "
                    "report the stack stopped. No process was killed and the file "
                    "was left in place. Fix or remove it, then stop again."
                ),
                "status": get_system_status(),
            }

        # Every dict entry that holds a pid, whatever its key: pre-rename
        # screener/engine/fleet registries stop under the current name.
        targets = []
        for key, info in saved_procs.items():
            if not isinstance(info, dict):
                continue
            pid = info.get("pid")
            if not pid:
                continue
            targets.append((_legacy_canonical(key), pid, info.get("started_at")))

        outcomes = _stop_services(targets, subprocess)

        survivors = [n for n, o in outcomes.items() if o["outcome"] == "still_running"]
        if not survivors:
            message = "Stack stopped"
            if any(o["outcome"] == "forced" for o in outcomes.values()):
                forced = next(n for n, o in outcomes.items() if o["outcome"] == "forced")
                message = f"Stack stopped ({forced} force-killed)"
        else:
            first = survivors[0]
            pid = next(
                (p for n, p, _ in targets if n == first), None)
            message = (
                f"STOP incomplete: {first} still running"
                + (f" (PID {pid})" if pid else "")
                + ". Registry kept for retry."
            )

        # Rewrite, never unlink: survivors and non-service metadata (capital,
        # reset_at) must survive the stop.
        rewritten = {
            k: v for k, v in saved_procs.items()
            if not (isinstance(v, dict) and v.get("pid") and k not in survivors)
        }
        try:
            procs_file.write_text(json.dumps(rewritten, indent=2), encoding="utf-8")
        except OSError:
            logger.warning("Could not rewrite %s after STOP", procs_file)

        return {
            "ok": not survivors,
            "message": message,
            "services": outcomes,
            "status": get_system_status(),
        }

    return {
        "ok": True,
        "message": "Stack already stopped",
        "status": get_system_status(),
    }


def _legacy_canonical(key: str) -> str:
    """Map a pre-rename registry key to its current service name."""
    return {"screener": "filter", "engine": "query", "fleet": "decide"}.get(key, key)


def _stop_services(targets: list, subprocess) -> dict:
    """Stop a list of (name, pid, started_at) under one shared deadline.

    Returns {name: {"outcome": ..., "detail": ...}} with outcome in
    not_running | stopped | forced | still_running. The clock and sleep are
    called through the `time` module so tests can script them.
    """
    outcomes: dict[str, dict[str, str]] = {}
    initially_alive = {p for (n, p, s) in targets if _is_pid_alive(p, s)}
    alive = [(n, p, s) for (n, p, s) in targets if p in initially_alive]
    if not alive:
        for name, _, _ in targets:
            outcomes[name] = {"outcome": "not_running", "detail": ""}
        return outcomes

    is_windows = sys.platform == "win32"
    errors: dict[str, str] = {}
    forced_pids: set[int] = set()

    def _signal(pid: int, sig: int) -> None:
        """Polite or forced signal, group-aware on POSIX, taskkill on Windows."""
        if sig == 9:
            forced_pids.add(pid)
        if is_windows:
            argv = (["taskkill", "/F", "/T"] if sig == 9 else ["taskkill", "/T"]) + ["/PID", str(pid)]
            try:
                res = subprocess.run(
                    argv, capture_output=True, timeout=_STOP_TASKKILL_TIMEOUT_S)
                if res.returncode != 0:
                    errors[pid] = f"taskkill exited {res.returncode}"
            except (subprocess.TimeoutExpired, OSError) as exc:
                errors[pid] = str(exc)
            return
        group = None
        try:
            group = os.getpgid(pid) if os.getpgid(pid) == pid else None
        except OSError:
            group = None
        try:
            if group is not None:
                os.killpg(group, sig)
            else:
                os.kill(pid, sig)
        except OSError as exc:
            errors[pid] = str(exc)

    def _reap(pid: int) -> None:
        # WNOHANG only exists on POSIX; Windows has no waitpid to reap.
        if is_windows or not hasattr(os, "WNOHANG"):
            return
        try:
            os.waitpid(pid, os.WNOHANG)
        except (ChildProcessError, OSError):
            pass

    def _down(pid: int) -> bool:
        if is_windows:
            return not _is_pid_alive(pid, None)
        try:
            os.killpg(os.getpgid(pid), 0)
            return False
        except ProcessLookupError:
            return True
        except OSError:
            return not _is_pid_alive(pid, None)

    # Polite pass.
    for _, pid, _ in alive:
        _signal(pid, 15)
    polite_deadline = time.monotonic() + _STOP_POLITE_WAIT_S
    while alive and time.monotonic() < polite_deadline:
        for _, pid, _ in alive:
            _reap(pid)
        alive = [(n, p, s) for (n, p, s) in alive if not _down(p)]
        if alive:
            time.sleep(_STOP_POLL_INTERVAL_S)

    # Force pass for whoever is left.
    for _, pid, _ in alive:
        _signal(pid, 9)
    force_deadline = time.monotonic() + _STOP_FORCE_WAIT_S
    while alive and time.monotonic() < force_deadline:
        for _, pid, _ in alive:
            _reap(pid)
        alive = [(n, p, s) for (n, p, s) in alive if not _down(p)]
        if alive:
            time.sleep(_STOP_POLL_INTERVAL_S)

    still = {p for _, p, _ in alive}
    for name, pid, _ in targets:
        if pid in still:
            outcomes[name] = {"outcome": "still_running", "detail": errors.get(pid, "")}
        elif pid not in initially_alive:
            outcomes[name] = {"outcome": "not_running", "detail": errors.get(pid, "")}
        elif pid in forced_pids:
            # It only came down after the force pass: report that honestly.
            outcomes[name] = {"outcome": "forced", "detail": errors.get(pid, "")}
        else:
            outcomes[name] = {"outcome": "stopped", "detail": errors.get(pid, "")}
    return outcomes


def start_shadow_run(
    custom_db: str | None = None,
    custom_run_id: str | None = None,
    minutes: float = 10.0,
    ensure_filter: bool = False,
    env_extra: dict | None = None,
) -> dict:
    """Launch background shadow rehearsal without signer."""
    import subprocess

    target_db = resolve_db_path(custom_db or _ACTIVE_DB_OVERRIDE)
    db_identity = resolve_db_identity(target_db)
    if db_identity["is_production"]:
        return {
            "ok": False,
            "message": "Refusing shadow start: cannot run shadow rehearsal against production database data/orders.db.",
            "status": get_system_status(),
        }

    # Check if a rehearsal is already running for this database
    shadow = read_shadow_run(str(target_db))
    if shadow and shadow.get("running") and not shadow.get("ended"):
        pid = shadow.get("pid")
        if pid and _is_pid_alive(pid, shadow.get("process_started_at")):
            return {
                "ok": False,
                "message": f"Shadow rehearsal {shadow.get('run_id')} (PID {pid}) is already running on {target_db}.",
                "status": get_system_status(),
            }

    # A fresh-held fleet lock means the previous run never released it (a
    # killed rehearsal skips its lock release). Spawning anyway dies at
    # startup on InstanceInUse with only a cycle-0 heartbeat behind -- the
    # "button flips back to START" report. Refuse with the wait instead.
    lock = _shadow_fleet_lock_info(target_db)
    if lock is not None:
        held_s = lock["age_ms"] / 1000.0
        clears_in_s = max(0.0, lock["stale_ms"] / 1000.0 - held_s)
        return {
            "ok": False,
            "message": (
                f"Refusing shadow start on {target_db.name}: the previous run "
                f"({lock['holder']}) still holds the fleet lock "
                f"(held {held_s:.0f}s; clears automatically in "
                f"~{clears_in_s:.0f}s). Wait a minute and start again."
            ),
            "status": get_system_status(),
        }

    # Run ID resolution
    if custom_run_id and str(custom_run_id).strip():
        run_id = str(custom_run_id).strip()
    elif shadow and shadow.get("run_id"):
        run_id = str(shadow.get("run_id"))
    else:
        stem = target_db.stem.replace("_", "-")
        run_id = stem if stem and stem != "orders" else f"shadow-{int(time.time())}"

    filter_note: str | None = None
    if ensure_filter:
        filter_note = _ensure_filter_for_shadow()

    popen_group = {"start_new_session": True} if sys.platform != "win32" else {}
    cmd = [
        sys.executable,
        "-m",
        "core_brain.shadow_run",
        "--minutes",
        str(minutes),
        "--db",
        str(target_db),
        "--run-id",
        str(run_id),
    ]
    child_env = None
    if env_extra:
        child_env = {**os.environ, **env_extra}

    # The child used to log to DEVNULL, so a startup death (stale import,
    # venue outage, lock refusal) was invisible and the page just flipped back
    # to START. Keep one small log per run instead.
    safe_run_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(run_id))
    log_path = runtime_file(f"shadow_run_{safe_run_id}.out.log", root=LIVE_ROOT)
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_fh = open(log_path, "ab")
    except OSError as e:
        return {
            "ok": False,
            "message": f"Failed to start shadow rehearsal: cannot open log {log_path}: {e}",
            "status": get_system_status(),
        }
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(REPO_ROOT),
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            **({"env": child_env} if child_env is not None else {}),
            **popen_group,
        )
    except Exception as e:
        try:
            log_fh.close()
        except Exception:
            pass
        return {
            "ok": False,
            "message": f"Failed to start shadow rehearsal: {e}",
            "status": get_system_status(),
        }
    try:
        log_fh.close()
    except Exception:
        pass

    # The pre-start polls may have cached this store's old (ended) record;
    # drop it so the next poll reads the new run instead of the dead one.
    try:
        _invalidate_snapshots_for_db(str(target_db), str(Path(target_db).resolve()))
    except Exception:
        pass
    status = get_system_status()
    if not status.get("shadow_run") or not status["shadow_run"].get("running"):
        status["shadow_run"] = {
            "running": True,
            "ended": False,
            "pid": proc.pid,
            "run_id": run_id,
            "db_path": str(target_db),
            "minutes": minutes,
            "elapsed_sec": 0,
        }

    message = f"Shadow rehearsal started ({run_id}) on {target_db.name}."
    if filter_note:
        message += f" {filter_note}"
    return {
        "ok": True,
        "message": message,
        "pid": proc.pid,
        "run_id": run_id,
        "status": status,
    }


def stop_shadow_run(custom_db: str | None = None) -> dict:
    """Stop running background shadow rehearsal."""
    import subprocess

    target_db = resolve_db_path(custom_db or _ACTIVE_DB_OVERRIDE)
    shadow = read_shadow_run(str(target_db))
    if not shadow or not shadow.get("running") or shadow.get("ended"):
        return {
            "ok": True,
            "message": "No active shadow rehearsal running for this store.",
            "status": get_system_status(),
        }

    pid = shadow.get("pid")
    if not pid or not _is_pid_alive(pid, shadow.get("process_started_at")):
        return {
            "ok": True,
            "message": "Shadow rehearsal is already stopped.",
            "status": get_system_status(),
        }

    outcomes = _stop_services([("shadow", pid, shadow.get("process_started_at"))], subprocess)
    outcome = outcomes.get("shadow", {}).get("outcome", "stopped")
    if outcome in ("stopped", "forced"):
        # A killed rehearsal never releases its fleet lock; without this the
        # next START against the same store dies on it for a full STALE
        # window. Only our own row is removed (holder is `pid:hex`).
        _clear_shadow_fleet_lock(target_db, pid)
        try:
            _invalidate_snapshots_for_db(str(target_db), str(Path(target_db).resolve()))
        except Exception:
            pass
    status = get_system_status()
    if status.get("shadow_run"):
        status["shadow_run"]["running"] = False
        status["shadow_run"]["ended"] = True
    return {
        "ok": outcome in ("stopped", "forced", "not_running"),
        "message": f"Shadow rehearsal (PID {pid}) {outcome}.",
        "outcome": outcome,
        "status": status,
    }


def set_sweep_interval(raw: str | None) -> dict:
    """Apply and persist the account-sweep cadence.

    `raw` is None or empty to clear (revert to every tick), otherwise a
    positive number of seconds. Persists to the .env live_exec loads and
    updates this process's environment so the status payload reflects it
    immediately. A running engine keeps its launch-time cadence until the
    bot stack is restarted.
    """
    value: float | None = None
    if raw is not None and str(raw).strip() != "":
        try:
            value = float(str(raw).strip())
        except ValueError:
            return {"ok": False, "message": "sweep interval must be a number of seconds"}
        if value <= 0:
            return {"ok": False, "message": "sweep interval must be positive seconds"}

    env_file = _env_file()
    if env_file is None:
        return {"ok": False, "message": "no .env found; sweep interval was not persisted"}

    if not write_sweep_interval_to_env_file(env_file, value):
        return {"ok": False, "message": f"failed to write {env_file}"}

    if value is None:
        os.environ.pop("LIVE_SWEEP_INTERVAL", None)
    else:
        os.environ["LIVE_SWEEP_INTERVAL"] = str(value)

    return {
        "ok": True,
        "message": "sweep: every tick" if value is None else f"sweep interval set to {value:g}s",
        "sweep_interval_sec": value,
        "status": get_system_status(),
    }


def reset_database(custom_path: str | Path | None = None) -> dict:
    """Safely archive the existing orders.db and initialize a fresh, clean database."""
    from core_brain.order_registry import OrderRegistry
    import shutil
    import datetime

    target_db = resolve_db_path(custom_path or _ACTIVE_DB_OVERRIDE)
    archived_name = None

    # Unlinking the registry under a live writer loses every subsequent write:
    # the engine and screener keep their handles on the old inode while the page
    # reads a new empty file, so the run's telemetry splits across two files and
    # the dashboard reads empty for a bot that is still trading.
    bot_state = get_system_status()["bot_state"]
    if bot_state != "STOPPED":
        # UNKNOWN gets the same refusal as RUNNING: an unreadable process file
        # cannot rule out a live writer, and resetting under one loses every
        # write it makes afterwards.
        return {
            "ok": False,
            "message": (
                "Refusing to reset while the bot stack is running. Stop the bot first."
                if bot_state == "RUNNING"
                else "Refusing to reset while the bot stack state is unknown: the "
                     "process file cannot be read, so a running stack cannot be "
                     "ruled out. Fix the process file, then stop the bot."
            ),
            "archived_to": None,
            "db_path": str(target_db),
        }

    # This function archives-then-deletes whatever it is pointed at. Launched
    # with --db against an archived cycle for a post-mortem, an unguarded reset
    # would destroy the very record the operator opened the page to read -- and
    # nest a new archive/ inside the archive directory on the way out.
    if any(part.lower() == "archive" for part in target_db.resolve().parts):
        return {
            "ok": False,
            "message": (
                f"Refusing to reset {target_db.name}: it is an archived run, "
                "opened for reading. Archives are history and are never reset."
            ),
            "archived_to": None,
            "db_path": str(target_db),
        }

    if target_db.exists() and target_db.stat().st_size > 0:
        archive_dir = target_db.parent / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        ts_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_path = archive_dir / f"live_{ts_str}.db"
        shutil.copy2(target_db, archive_path)
        archived_name = archive_path.name
        try:
            target_db.unlink()
        except Exception:
            pass
        for extra in (f"{target_db}-wal", f"{target_db}-shm"):
            try:
                Path(extra).unlink(missing_ok=True)
            except Exception:
                pass

    # Initialize fresh database with all tables and schema
    OrderRegistry(target_db)

    return {
        "ok": True,
        "message": f"Created fresh database at {target_db.name}" + (f" (archived previous to {archived_name})" if archived_name else ""),
        "archived_to": archived_name,
        "db_path": str(target_db),
    }


@app.get("/api/system/status")
def api_system_status():
    """Return process states for 3 sub-services."""
    return JSONResponse(get_system_status())


@app.post("/api/system/preflight")
async def api_system_preflight(request: Request):
    """Run one sequential preflight check; issue single-use approval when all pass."""
    _authorize_control(request)
    refusal = _refuse_unless_live_writable()
    if refusal is not None:
        return JSONResponse(refusal)
    from dashboard import preflight as _pf

    body: dict = {}
    try:
        if request.headers.get("content-type", "").startswith("application/json"):
            body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    step = body.get("step")
    preflight_id = body.get("preflight_id")
    confirm = body.get("confirm", False)
    if step not in _pf.PREFLIGHT_STEPS:
        return JSONResponse({
            "ok": False,
            "message": f"Unknown preflight step {step!r}.",
            "status": get_system_status(),
        })
    pid = preflight_id
    if step == _pf.PREFLIGHT_STEPS[0] and not pid:
        pid = _pf.new_session()
    sess = _pf.get_session(pid)
    if sess is None:
        return JSONResponse({
            "ok": False,
            "message": "Unknown or expired preflight_id; restart preflight from step 1.",
            "status": get_system_status(),
        })
    runners = {
        "credentials": lambda: _pf.check_credentials(),
        "screener": lambda: _pf.check_screener(),
        "stream": lambda: _pf.check_stream(),
        "reach_probe": lambda: _pf.check_reach_probe(confirm=confirm is True),
    }
    try:
        result = runners[step]()
    except Exception as exc:
        result = {"ok": False, "summary": f"{step} crashed: {type(exc).__name__}",
                  "details": {"error": f"{type(exc).__name__}: {exc}"}}
    if not isinstance(result, dict) or "ok" not in result:
        result = {"ok": False, "summary": f"{step} returned no verdict", "details": {}}
    accepted, error = _pf.submit_step(pid, step, bool(result.get("ok")),
                                      str(result.get("summary", "")),
                                      dict(result.get("details") or {}))
    if not accepted:
        return JSONResponse({
            "ok": False,
            "message": error or "Preflight step refused.",
            "preflight_id": pid,
            "status": get_system_status(),
        })
    approved = _pf.try_approve(pid)
    sess2 = _pf.get_session(pid) or sess
    nxt: str | None = None
    for s in _pf.PREFLIGHT_STEPS:
        entry = (sess2.get("steps") or {}).get(s)
        if not (isinstance(entry, dict) and entry.get("ok") is True):
            nxt = s
            break
    return JSONResponse({
        "ok": bool(result.get("ok")),
        "step": step,
        "summary": result.get("summary", ""),
        "details": result.get("details", {}),
        "preflight_id": pid,
        "next_step": nxt,
        "approved": bool(approved),
        "expires_in_sec": int(_pf.PREFLIGHT_TTL_SEC),
    })


@app.post("/api/system/start")
async def api_system_start(request: Request):
    """Start background bot stack (gated: valid single-use preflight_id)."""
    _authorize_control(request)
    body: dict = {}
    try:
        if request.headers.get("content-type", "").startswith("application/json"):
            body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    preflight_id = body.get("preflight_id")
    minutes = body.get("minutes", 1440)
    try:
        minutes_f = float(minutes)
    except (TypeError, ValueError):
        return JSONResponse({
            "ok": False,
            "message": "Invalid minutes; choose 60, 360, or 1440.",
            "status": get_system_status(),
        })
    if minutes_f not in (60.0, 360.0, 1440.0):
        return JSONResponse({
            "ok": False,
            "message": "Invalid minutes; choose 60, 360, or 1440.",
            "status": get_system_status(),
        })
    if not preflight_id or not isinstance(preflight_id, str):
        return JSONResponse({
            "ok": False,
            "message": ("Live start needs a fresh preflight approval: open the "
                        "dashboard Start Run launcher, pass all 4 checks, then start."),
            "status": get_system_status(),
        })
    from dashboard import preflight as _pf

    if not _pf.consume_approval(preflight_id):
        return JSONResponse({
            "ok": False,
            "message": "Invalid, expired, or already-used preflight_id; re-run preflight.",
            "status": get_system_status(),
        })
    return JSONResponse(start_bot(preflight_id=preflight_id, minutes=minutes_f))


@app.post("/api/system/stop")
def api_system_stop(request: Request):
    """Stop background bot stack."""
    _authorize_control(request)
    return JSONResponse(stop_bot())


def switch_active_db(path: str | None) -> dict:
    """Re-point this page at another registry: the run switcher.

    Which store the dashboard reads is otherwise fixed at launch by `--db` or
    `LIVE_DB_PATH`, so an operator whose page was aimed at a store whose run
    has since died has no way to watch the run that is actually going except
    restarting the server. This only changes what the page reads; the stack,
    its processes and the production registry are untouched, and the existing
    START refusals still apply to whatever store is now active.
    """
    if not path:
        return {"ok": False,
                "message": "No database path given.",
                "status": get_system_status()}
    if _is_temp_or_test_db(path):
        return {
            "ok": False,
            "message": (
                f"Refused database at {path}: temporary or test directories "
                "are out of scope for the operations dashboard."
            ),
            "status": get_system_status(),
        }
    target = Path(path)
    if not target.is_file():
        return {
            "ok": False,
            "message": (
                f"No registry at {path}. Switch to a store a run is actually "
                f"writing, or restart the dashboard with --db {path}."
            ),
            "status": get_system_status(),
        }
    set_db_override(target)
    return {"ok": True,
            "message": f"Now reading {target}.",
            "status": get_system_status()}


@app.get("/api/profile/options")
def api_profile_options():
    """Every loadable profile: code presets plus tournament arms (#856 IIIB).

    Read-only listing. Presets come from TOURNAMENT_PRESETS, arms from
    configs/tournaments/*_arms.json with the env block that reproduces them.
    """
    return JSONResponse(_profile_options())


@app.post("/api/profile/view")
def api_profile_view(request: Request, name: str | None = None):
    """Point the page at an arm's store: view-only navigation (#856 IIIB).

    The same read-only re-point as the run switcher; the stack, its
    processes and the production registry are untouched.
    """
    _authorize_control(request)
    store = _resolve_arm_store(name)
    if not store:
        return JSONResponse({
            "ok": False,
            "message": f"No store on disk for arm {name!r}: nothing is writing it.",
            "status": get_system_status(),
        })
    return JSONResponse(switch_active_db(store))


@app.post("/api/profile/stage")
def api_profile_stage(request: Request, kind: str | None = None, name: str | None = None):
    """Stop the stack and stage a profile for the next START (#856 IIIB).

    Staging writes runtime/pending_profile.json; it never starts anything.
    The operator's next START press consumes it (child env overlay) and the
    staged run then records itself in trader_profile.json like any other.
    """
    _authorize_control(request)
    # Staging stops the live stack, so it shares the production gate with
    # every other stack-changing op: from a shadow-view page START would be
    # refused afterwards, leaving resting orders unmanaged.
    refusal = _refuse_unless_live_writable()
    if refusal is not None:
        return JSONResponse(refusal)
    options = _profile_options()
    match = next((o for o in options["presets"] + options["arms"]
                  if o["kind"] == kind and o["name"] == name), None)
    if match is None:
        return JSONResponse({
            "ok": False,
            "message": f"Unknown profile {kind}:{name}.",
            "status": get_system_status(),
        })
    stopped = stop_bot()
    if not stopped.get("ok"):
        return JSONResponse({
            "ok": False,
            "message": ("Refusing to stage "
                          f"{match['label']}: stop failed ({stopped.get('message')})."),
            "status": get_system_status(),
        })
    # Identity only: start_bot re-resolves the env from the current
    # options, so this file can never inject overrides into the stack.
    record = {
        "kind": kind,
        "name": name,
        "staged_at": time.time(),
    }
    try:
        target = runtime_file(PENDING_PROFILE_FILE, root=LIVE_ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(record), encoding="utf-8")
        tmp.replace(target)
    except OSError as e:
        return JSONResponse({
            "ok": False,
            "message": f"Stop succeeded but staging failed: {e}.",
            "status": get_system_status(),
        })
    return JSONResponse({
        "ok": True,
        "message": (f"Stack stopped; {match['label']} staged. "
                      "Press START to run it."),
        "status": get_system_status(),
    })


@app.post("/api/profile/clear")
def api_profile_clear(request: Request):
    """Drop the staged profile: the next START uses the current env (#856 IIIB)."""
    _authorize_control(request)
    try:
        resolve_runtime_file(
            PENDING_PROFILE_FILE, root=LIVE_ROOT).unlink(missing_ok=True)
    except OSError:
        pass
    return JSONResponse({"ok": True, "status": get_system_status()})


@app.post("/api/system/db")
def api_system_switch_db(request: Request, db: str | None = None):
    """Point the page at another store (read-only; changes no machine state)."""
    _authorize_control(request)
    return JSONResponse(switch_active_db(db))


@app.post("/api/system/demo")
def api_system_demo(request: Request):
    """Toggle this page between the demo registry and the live store.

    The demo registry is the bogus store `scripts/build_demo_db.py` writes,
    built so every dashboard table and metric card can be eyeballed without
    a live run. Loading it is the same read-only re-point the run switcher
    performs: the stack, its processes and the production registry are
    untouched, and the START refusals that gate on a non-production store
    still apply -- which is the point. A button that loads bogus data sits
    next to the controls that spend money, so the page reading the demo
    must not be able to start the live stack.

    Pressing it again (the button reads BACK TO LIVE while the demo is
    loaded) returns the page to its launch store. When the page was
    launched ON the demo itself, the env var that still points at it is
    cleared too, so the fallback is the production registry and the button
    can never trap itself on bogus data.
    """
    _authorize_control(request)
    demo = demo_db_path()
    if _same_registry(resolve_db_path(_ACTIVE_DB_OVERRIDE), demo):
        set_db_override(None)
        env_path = os.environ.get("LIVE_DB_PATH")
        if env_path and _same_registry(Path(env_path), demo):
            del os.environ["LIVE_DB_PATH"]
        live = resolve_db_path(None)
        return JSONResponse({
            "ok": True,
            "demo": False,
            "message": f"Back to the live store: {live}.",
            "status": get_system_status(),
        })
    if not demo.is_file():
        return JSONResponse({
            "ok": False,
            "message": (
                f"No demo registry at {demo}. Build one first: "
                "python scripts/build_demo_db.py"
            ),
            "status": get_system_status(),
        })
    set_db_override(demo)
    return JSONResponse({
        "ok": True,
        "demo": True,
        "message": (
            f"Reading the demo registry: {demo}. Orders, fills and PnL on "
            "this page are bogus, and START is disabled while they are loaded."
        ),
        "status": get_system_status(),
    })


@app.post("/api/system/service/start")
def api_system_service_start(request: Request, service: str | None = None):
    """Start one stack service (filter, query, or decide) on its own."""
    _authorize_control(request)
    return JSONResponse(start_service(service or ""))


@app.post("/api/system/service/stop")
def api_system_service_stop(request: Request, service: str | None = None):
    """Stop one stack service, leaving the others running."""
    _authorize_control(request)
    return JSONResponse(stop_service(service or ""))


# ---------------------------------------------------------------------------
# Tournament Hub & Cockpit, served in-process (no ports, no processes)
# ---------------------------------------------------------------------------
# The hub and cockpit used to be standalone servers the dashboard
# reverse-proxied from :8798/:8800. They are pages now: the dashboard imports
# their data builders and renders the same documents itself, so nothing is
# spawned, nothing listens on those ports for the dashboard's sake, and the
# side-rail links are plain same-tab navigation. Both modules stay runnable
# standalone (`python -m scripts.tournament_hub|cockpit_server --port ...`)
# for the menu and the reconcile loop, which manage those processes.
def _load_cockpit():
    """Tournament cockpit module, imported lazily (like the hub).

    The cockpit is a large research module; a failure importing it must not
    keep the live control page from starting. Handlers pay the import on
    first use instead of at dashboard boot.
    """
    from scripts import tournament_cockpit_server as cockpit_mod
    return cockpit_mod

_INPROC_SERVICES = ("hub", "cockpit")

_SHARED_RAIL_CSS = """<style>
.sh-rail{position:fixed;left:0;top:0;bottom:0;width:56px;background:#0b0f19;\
border-right:1px solid #232b3a;display:flex;flex-direction:column;\
align-items:center;padding:10px 0;gap:6px;z-index:9999}
.sh-rail a{display:flex;flex-direction:column;align-items:center;gap:3px;\
width:44px;padding:7px 0 5px;border-radius:9px;color:#8b94a3;\
text-decoration:none;font:600 8.5px/1.2 system-ui,-apple-system,"Segoe UI",sans-serif}
.sh-rail a svg{width:17px;height:17px}
.sh-rail a:hover{color:#e6ebf2;background:#16202f}
.sh-rail a.on{color:#6ea8fe;background:#14273f}
body{padding-left:56px !important}
</style>"""

# Same destinations as the dashboard's own side rail (prototype.js
# RAIL_LINKS): the icons are the same 16x16 path data, so the rail reads as
# one navigation everywhere.
_SHARED_RAIL_LINKS = (
    ("/", "Dashboard", "Dash",
     "M2.4 2.4h4.4v4.4H2.4z M9.2 2.4h4.4v4.4H9.2z M2.4 9.2h4.4v4.4H2.4z M9.2 9.2h4.4v4.4H9.2z"),
    ("/hub/", "Tournament Hub", "Hub",
     "M2 4h12v9H2z M5 2h6 M5 15h6"),
    ("/cockpit/", "Tournament Cockpit", "Cockpit",
     "M2 13.5h12 M3 11l3-6 4 4 4-7"),
    ("/explainer", "Strategy Explainer", "Guide",
     "M2 3h5a2 2 0 0 1 2 2v8a2 2 0 0 0-2-2H2z M14 3H9a2 2 0 0 0-2 2v8a2 2 0 0 1 2-2h5z"),
)


def _shared_rail(active: str) -> str:
    """Fixed left rail shared by the folded hub/cockpit pages.

    `active` is "hub" or "cockpit" and only highlights the current page.
    Scoped `sh-` classes and inline SVG keep it free of the host pages'
    styles, so the same snippet works in both documents.
    """
    items = []
    for href, title, label, icon in _SHARED_RAIL_LINKS:
        on = " on" if ((href == "/hub/" and active == "hub")
                         or (href == "/cockpit/" and active == "cockpit")
                         or (href == "/" and active == "dashboard")) else ""
        items.append(
            f'<a href="{href}" title="{title}" class="sh-link{on}">'
            f'<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" '
            f'stroke-width="1.4"><path d="{icon}"/></svg>'
            f"<span>{label}</span></a>")
    return '<nav class="sh-rail" aria-label="Site">' + "".join(items) + "</nav>"


def _with_shared_rail(html_text: str, active: str) -> str:
    """Inject the shared rail into a folded page's document."""
    marker = "<body>"
    if marker not in html_text:
        return html_text
    return html_text.replace(marker, marker + _SHARED_RAIL_CSS + _shared_rail(active), 1)


def _probe_service_status(name: str) -> dict:
    """Tournament pages are served in-process: always running, no port."""
    if name not in _INPROC_SERVICES:
        return {"error": f"Unknown service: {name}"}
    return {
        "name": name,
        "state": "running",
        "port": None,
        "url": f"/{name}/",
        "data_available": True,
        "detail": "Served in-process by the dashboard; no separate backend.",
    }


@app.get("/api/services/hub/status")
@app.get("/api/services/cockpit/status")
@app.get("/api/services/{name}/status")
def api_service_status(name: str = "hub"):
    """Tournament pages are part of this dashboard: always running."""
    if name not in _INPROC_SERVICES:
        return JSONResponse({"error": f"Unknown service: {name}"}, status_code=404)
    return JSONResponse(_probe_service_status(name))


@app.get("/api/system/shadow/next")
def api_system_shadow_next():
    """Preview the next fresh shadow store (read-only, no state change)."""
    naming = _fresh_shadow_naming()
    seq, db_name = naming["seq"], naming["db_name"]
    markets_info: dict = {"exists": False, "age_sec": None}
    try:
        from core_brain.runtime_paths import runtime_file as _rf
        mp = _rf("markets.json", root=LIVE_ROOT)
    except Exception:
        mp = LIVE_ROOT / "runtime" / "markets.json"
    try:
        if mp.exists():
            markets_info = {"exists": True,
                            "age_sec": max(0.0, time.time() - mp.stat().st_mtime)}
    except OSError:
        pass
    return JSONResponse({
        "ok": True,
        "seq": seq,
        "db_name": db_name,
        "db_path": str((LIVE_ROOT / "data" / db_name).resolve()),
        "profile_slug": naming["slug"] or None,
        "markets": markets_info,
    })


@app.post("/api/system/shadow/start")
async def api_system_shadow_start(
    request: Request,
    db: str | None = None,
    run_id: str | None = None,
    minutes: float = 10.0,
):
    """Launch background shadow rehearsal without signer."""
    _authorize_control(request)
    body = {}
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            body = await request.json()
        except Exception:
            body = {}
    target_db = body.get("db") or db
    target_run_id = body.get("run_id") or run_id
    target_minutes = body.get("minutes") if body.get("minutes") is not None else minutes
    profile_env: dict | None = None
    took_fresh = False
    if body.get("fresh") is True and not target_db:
        took_fresh = True
        naming = _fresh_shadow_naming()
        target_db = str((LIVE_ROOT / "data" / naming["db_name"]).resolve())
        if not target_run_id:
            target_run_id = naming["run_id"]
        if naming["profile"] is not None:
            profile_env = dict(naming["profile"].get("env") or {})
        try:
            from core_brain.order_registry import OrderRegistry as _Reg
            _Reg(db_path=target_db)
        except Exception:
            pass
        try:
            set_db_override(Path(target_db))
        except Exception:
            pass
    result = start_shadow_run(target_db, target_run_id, float(target_minutes),
                              ensure_filter=took_fresh,
                              env_extra=profile_env)
    if took_fresh and result.get("ok") is True:
        try:
            pending_file = resolve_runtime_file(PENDING_PROFILE_FILE, root=LIVE_ROOT)
            pending_file.unlink(missing_ok=True)
        except Exception:
            pass
    return JSONResponse(result)


@app.post("/api/system/shadow/stop")
async def api_system_shadow_stop(request: Request, db: str | None = None):
    """Stop running background shadow rehearsal."""
    _authorize_control(request)
    body = {}
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            body = await request.json()
        except Exception:
            body = {}
    target_db = body.get("db") or db
    return JSONResponse(stop_shadow_run(target_db))


@app.post("/api/system/sweep-interval")
def api_system_set_sweep_interval(request: Request, seconds: str | None = None):
    """Set or clear the account-sweep cadence and persist it to .env."""
    _authorize_control(request)
    return JSONResponse(set_sweep_interval(seconds))


@app.post("/api/system/reset-db")
def api_system_reset_db(request: Request):
    """Archive current database and initialize a fresh clean orders.db."""
    _authorize_control(request)
    return JSONResponse(reset_database())


@app.post("/api/system/venue-sync")
def api_system_venue_sync(request: Request):
    """Trigger a one-time venue reconciliation.
    Reads the account from Polymarket and backfills closes/float_marks.
    Read-only at the venue; no exposure is opened or increased."""
    _authorize_control(request)
    from core_brain.order_manager import venue_sync
    db_path = resolve_db_path(_ACTIVE_DB_OVERRIDE)
    return JSONResponse(venue_sync(db_path=db_path, quiet=False))


@app.post("/api/system/sync")
def api_system_sync(request: Request):
    """Full dashboard ↔ venue sync — fixes the '0 open/0 positions on site but stale on dashboard' drift.

    1. Reconcile local orders/fills against venue open orders + trades (marks stale 'open' rows as
       'cancelled'/'filled' when the venue no longer lists them). 2. Run venue_sync (account value,
       closes, float_marks — now correctly writes a 0/0/0 mark when the venue holds 0 positions).

    Both steps are read-only at the venue: no quotes, no cancels, no exposure change. A venue
    outage on step 1 does not block step 2; errors are returned per-step so the UI can show
    'orders stale, account fresh' rather than a silent 500.
    """
    _authorize_control(request)
    from core_brain.order_registry import OrderRegistry
    from core_brain.order_manager import venue_sync

    db_path = resolve_db_path(_ACTIVE_DB_OVERRIDE)
    result: dict = {"ok": True, "steps": {}}

    # Step 1: order/fill reconcile (venue open_orders + trades → local orders/fills)
    try:
        from core_brain.venue import client as venue_client
        from core_brain.order_registry import reconcile_orders
        import os

        c = venue_client()
        reg = OrderRegistry(db_path=db_path)
        funder = os.environ.get("POLY_FUNDER")
        summary = reconcile_orders(c, reg, maker_address=funder)
        result["steps"]["reconcile"] = {
            "ok": True,
            "open_orders_count": summary.open_orders_count,
            "trades_polled": summary.trades_polled,
            "fills_recorded": summary.fills_recorded,
            "orders_filled": summary.orders_filled,
            "orders_cancelled": summary.orders_cancelled,
            "transitions": summary.transitions[:20],
        }
    except (Exception, SystemExit) as exc:
        result["steps"]["reconcile"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        result["ok"] = False

    # Step 2: venue account/positions sync (always attempted, even if reconcile failed)
    try:
        vs = venue_sync(db_path=db_path, quiet=True)
        result["steps"]["venue_sync"] = {"ok": True, **vs}
    except SystemExit as exc:
        result["steps"]["venue_sync"] = {"ok": False, "error": str(exc)}
        result["ok"] = False
    except Exception as exc:
        result["steps"]["venue_sync"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        result["ok"] = False

    # Current state snapshot so the UI can immediately show the before/after without a second poll.
    try:
        from core_brain.registry_state import summarize_state
        st = summarize_state(db_path)
        # venue_open_orders is the live venue count from step 1 when available, else local.
        local_open = len([o for o in (st.get("orders") or []) if str(o.get("status") or "").lower() in ("open", "pending", "partial")])
        result["state"] = {
            "local_open_orders": local_open,
            "venue_open_orders": result["steps"].get("reconcile", {}).get("open_orders_count"),
            "fills": len(st.get("fills") or []),
            "local_positions_hint": st.get("pairs", []),
        }
    except Exception:
        pass

    # Fresh kpi + status for immediate re-render (avoid a second round-trip)
    try:
        from core_brain.kpi import report as kpi_report
        kpi = kpi_report(db_path=db_path)
        result["kpi"] = {"run_profitability": kpi.get("run_profitability"), "portfolio": kpi.get("portfolio")}
    except Exception:
        pass

    status_code = 200 if result["ok"] else 207  # 207 = multi-status: one step failed
    return JSONResponse(result, status_code=status_code)


def relaunch_argv() -> list[str]:
    """Build the command that starts a replacement dashboard process.

    The script path must be absolute. `sys.argv[0]` is whatever the operator
    typed, and .claude/launch.json types it relative ("live/dash/live_dash.py");
    replaying that under cwd=LIVE_ROOT would look for live/live/dash/live_dash.py,
    so the replacement would die on startup -- after the current instance has
    already called os._exit(0), leaving no dashboard at all.

    Everything after argv[0] is carried through, so --port and --db survive a
    restart and the page comes back on the same port against the same database.
    """
    return [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]]


@app.post("/api/system/restart-dash")
def api_system_restart_dash(request: Request):
    """Restart only the dashboard web server process without touching engine/screener workers."""
    _authorize_control(request)
    import threading
    import subprocess

    def _do_restart():
        time.sleep(0.8)
        # Launch detached replacement dashboard process.
        kwargs = {"cwd": str(LIVE_ROOT), "stdin": subprocess.DEVNULL}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(relaunch_argv(), **kwargs)
        # Exit current instance to immediately release port 8799. Engine and
        # screener are separate processes and keep running; they are only
        # orphaned, never signalled.
        os._exit(0)

    threading.Thread(target=_do_restart, daemon=True).start()
    return JSONResponse({"ok": True, "message": "Dashboard server restarting..."})


@app.post("/api/system/cancel-all")
def api_system_cancel_all(request: Request):
    """Cancel all open orders on the venue.

    A closing command (pre-approved per AGENTS.md safety rails), but the
    endpoint still requires the same CSRF defence as every other machine-state
    POST: control token + origin check. Without it, a cross-origin form POST
    to 127.0.0.1:8799 can cancel every resting order on a live bot.
    """
    _authorize_control(request)
    # Direct seam adapter: the Order Manager verb keeps the CLI rendering
    # (stdout print, SystemExit); the server renders the same refusal as JSON.
    from core_brain.order_execution import cancel_all_orders
    from core_brain.order_manager import client as build_venue_client
    # The seam maps every venue failure to a refusal, so there is nothing to
    # catch here: a raise would mean the seam itself broke.
    result = cancel_all_orders(client_fn=build_venue_client, live=True)
    if not result.ok:
        return JSONResponse(
            {"ok": False, "message": f"Venue rejected cancel-all: {result.refusal}"},
            status_code=502,
        )
    return JSONResponse({"ok": True, "message": "All open orders cancelled on the venue."})


@app.post("/api/system/reset")
def api_system_reset(request: Request):
    """Full reset: halt bot, cancel venue orders, wipe DB, snapshot wallet.

    This is the "clean run" button. It does, in order:
    1. Stop the bot stack (screener, engine, fleet, guardrail)
    2. Cancel all open orders on the venue (so nothing is resting)
    3. Archive + wipe orders.db (fresh registry: 0 PnL, 0 fills, 0 closes)
    4. Clear run state files (cycle_events, heartbeats, live_procs)
    5. Snapshot the live Polymarket wallet as starting capital
    6. Venue-sync open positions into the fresh DB (so the dashboard shows
       real positions immediately, not an empty page that fabricates zeros)

    Configuration (.env, config.py) is never touched. The Polymarket account
    is the source of truth: open positions are pulled in, open orders are
    cancelled, and the account value at reset time becomes the starting capital.
    """
    _authorize_control(request)
    from core_brain.order_execution import cancel_all_orders
    from core_brain.order_manager import account_sweep, client as build_venue_client

    steps = []

    # 1. Halt the bot. A stop that could not confirm the stack is down aborts
    #    the whole reset: cancelling orders and archiving the database under a
    #    live writer loses every write it makes afterwards.
    stop_result = stop_bot()
    steps.append(f"bot: {stop_result['message']}")
    if not stop_result.get("ok"):
        return JSONResponse(
            {
                "ok": False,
                "message": stop_result["message"],
                "steps": steps,
                "starting_capital": None,
                "status": stop_result.get("status"),
            },
            status_code=409,
        )

    # 2. Cancel all open orders on the venue (best-effort; if credentials
    #    are missing or the venue is down, the reset still proceeds)
    reset_cancel = cancel_all_orders(client_fn=build_venue_client, live=True)
    if reset_cancel.ok:
        steps.append("venue: all open orders cancelled")
    else:
        steps.append(f"venue: cancel-all skipped ({reset_cancel.refusal})")

    # 3. Archive + wipe the database
    reset_result = reset_database()
    if not reset_result.get("ok"):
        return JSONResponse(reset_result, status_code=409)
    steps.append(f"db: {reset_result['message']}")

    # 4. Clear runtime state files (ring buffer, heartbeats, processes, screener universe & pipeline).
    #    Both the current runtime/ name and the pre-rename run/ name, or
    #    "clean run ready" would leave state the fallback reader still finds.
    for fname in ["cycle_events.jsonl", "live_poll_heartbeat.json",
                  "global_stop_loss_heartbeat.json", "live_orders.json",
                  "processes.json", "markets.json", "pipeline.json",
                  "rerank.log"]:
        for target in (runtime_file(fname, root=LIVE_ROOT),
                       legacy_runtime_file(fname, root=LIVE_ROOT)):
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
    steps.append("screener: universe and pipeline files cleared")
    steps.append("run: state files cleared")

    # 5. Snapshot the live Polymarket wallet as starting capital.
    #    account_sweep reads collateral + positions + P&L from the venue
    #    and writes an account_mark + float_mark into the fresh DB.
    #    The account_value_usd at this moment becomes the baseline.
    starting_capital = None
    try:
        sweep_result = account_sweep(quiet=True,
                                     db_path=str(resolve_db_path(_ACTIVE_DB_OVERRIDE)))
        if isinstance(sweep_result, dict):
            starting_capital = sweep_result.get("account_value_usd")
            if starting_capital is not None:
                starting_capital = float(starting_capital)
                steps.append(f"wallet: starting capital = ${starting_capital:,.2f}")
            else:
                steps.append("wallet: venue returned null account value")
    except (Exception, SystemExit) as e:
        steps.append(f"wallet: snapshot failed ({e})")

    # Write starting capital to processes.json so get_starting_capital() can
    # read it on the next poll — even if the bot hasn't been started yet.
    procs_file = runtime_file("processes.json", root=LIVE_ROOT)
    procs_file.parent.mkdir(parents=True, exist_ok=True)
    procs_data = {}
    if starting_capital is not None:
        procs_data["starting_account_value"] = starting_capital
    procs_data["reset_at"] = time.time()
    procs_file.write_text(json.dumps(procs_data), encoding="utf-8")

    return JSONResponse({
        "ok": True,
        "message": "Reset complete. Clean run ready.",
        "steps": steps,
        "starting_capital": starting_capital,
        "status": get_system_status(),
    })


def _read_only_error_payload(exc: Exception) -> dict[str, str]:
    """Describe a read-only report failure with one stable JSON contract."""
    return {"error": str(exc), "error_type": type(exc).__name__}


def _read_only_error_response(exc: Exception) -> JSONResponse:
    """Return the shared 500 response used by read-only report endpoints."""
    return JSONResponse(_read_only_error_payload(exc), status_code=500)


MAX_QUOTES_PER_MARKET_KPI: int = 10


def _kpi_quote_leg(q: dict) -> str | None:
    """The leg a KPI quote belongs to, matching the page's normalizeLeg()."""
    v = str(q.get("side") or "").strip().upper()
    if v == "UP":
        return "UP"
    if v in ("DN", "DOWN"):
        return "DN"
    return None


def _trim_kpi_quotes(kpi: Any, max_quotes_per_market: int = MAX_QUOTES_PER_MARKET_KPI) -> Any:
    """Trim historical quotes per market in by_market to avoid multi-megabyte payloads.

    The frontend only reads recent quotes per leg to determine current quotes,
    mids, and token-to-leg mappings. Unbounded historical quote logs can reach 10MB+
    and stall browsers.

    The newest quote of each leg is kept even when it is older than the cap, because
    latestLegQuotes() rebuilds the UP/DN mapping from whatever survives: dropping a
    leg's newest quote would report that leg as unquoted while the store still holds
    it. The remaining slots go to the newest quotes overall.
    """
    if not isinstance(kpi, dict):
        return kpi
    by_mkt = kpi.get("by_market")
    if not isinstance(by_mkt, dict):
        return kpi
    trimmed = dict(kpi)
    new_by_mkt = {}
    for cid, m in by_mkt.items():
        if isinstance(m, dict):
            quotes = m.get("quotes")
            if isinstance(quotes, list) and len(quotes) > max_quotes_per_market:
                m_copy = dict(m)
                newest_first = sorted(quotes, key=lambda q: float(q.get("ts") or 0.0),
                                      reverse=True)
                kept: list[dict] = []
                seen_legs: set[str] = set()
                # Pass 1: one newest quote per leg, so neither leg can be starved.
                for q in newest_first:
                    leg = _kpi_quote_leg(q)
                    if leg is not None and leg not in seen_legs:
                        seen_legs.add(leg)
                        kept.append(q)
                # Pass 2: fill whatever is left by timestamp.
                if len(kept) < max_quotes_per_market:
                    chosen = {id(q) for q in kept}
                    for q in newest_first:
                        if len(kept) >= max_quotes_per_market:
                            break
                        if id(q) not in chosen:
                            kept.append(q)
                            chosen.add(id(q))
                m_copy["quotes"] = kept[:max_quotes_per_market]
                new_by_mkt[cid] = m_copy
                continue
        new_by_mkt[cid] = m
    trimmed["by_market"] = new_by_mkt
    return trimmed


@app.get("/api/kpi")
def get_kpi(run_id: str | None = None):
    """Return live KPI report mirroring strategy/kpi.py with Level 1/2/3 diagnostics."""
    from core_brain.kpi import report as generate_kpi_report
    db_path = resolve_db_path(_ACTIVE_DB_OVERRIDE)

    def build() -> tuple[Any, int]:
        try:
            raw = generate_kpi_report(db_path=db_path, run_id=run_id)
            return _trim_kpi_quotes(raw), 200
        except Exception as e:
            return _read_only_error_payload(e), 500

    payload, status = _cached_snapshot(("kpi", str(db_path), run_id), build)
    if status == 200 and isinstance(payload, dict):
        # Feed the live-marks wanted set from the payload already served:
        # no rebuild, no feed read, no order-book pulls -- the served dict
        # is the only input. Best-effort; a mark failure must never fail KPI.
        try:
            from core_brain.live_marks import update_wanted_from_kpi
            if update_wanted_from_kpi(_live_marks_cache(), payload):
                sync_live_marks()
        except Exception:
            logger.debug("live-marks wanted update skipped", exc_info=True)
    return JSONResponse(payload, status_code=status)


@app.get("/api/run-profitability")
def get_run_profitability(run_id: str | None = None):
    """Quick-answer endpoint: was this run bottom-line profitable?

    Returns the `run_profitability` slice of the KPI report alone so the
    dashboard can poll it independently of the full report. Same filtering
    rules as /api/kpi: `run_id` overrides the active run, omitted picks the
    most recent run, `?run_id=all` aggregates.
    """
    from core_brain.kpi import report as generate_kpi_report
    db_path = resolve_db_path(_ACTIVE_DB_OVERRIDE)
    try:
        data = generate_kpi_report(db_path=db_path, run_id=run_id)
        rp = data.get("run_profitability")
        if rp is None:
            return _read_only_error_response(
                RuntimeError("run_profitability unavailable")
            )
        # venue_open_orders must come from venue reconciliation, not local SQLite.
        # The profitability endpoint is read-only and must not invent a venue
        # count from stale rows; omit the field when venue reconciliation has
        # not run (call /api/system/sync for that).
        return JSONResponse(rp)
    except Exception as e:
        return _read_only_error_response(e)


@app.post("/api/account/sweep")
@app.get("/api/account/sweep")
def trigger_account_sweep():
    """Trigger an on-demand account sweep against the venue and update starting capital if unset."""
    import os
    if not os.environ.get("POLY_FUNDER"):
        return JSONResponse({"ok": False, "error": "POLY_FUNDER is not configured in environment"}, status_code=400)
    from core_brain.order_manager import account_sweep
    db_path = str(resolve_db_path(_ACTIVE_DB_OVERRIDE))
    try:
        res = account_sweep(quiet=True, db_path=db_path)
        if isinstance(res, dict) and res.get("account_value_usd") is not None:
            val = float(res["account_value_usd"])
            _save_starting_account_value(val)
            return JSONResponse({"ok": True, "sweep": res, "starting_capital": val})
        return JSONResponse({"ok": False, "message": "Sweep did not return account value", "sweep": res})
    except (Exception, SystemExit) as e:
        return JSONResponse({"ok": False, "error": str(e) or "account_sweep exited unexpectedly"}, status_code=500)


def compute_scan_state(
    last_event_ts: Optional[float],
    hb_ts: Optional[float],
    now: float,
    active_phases: set[str],
    stall_threshold: float = SCAN_STALL_THRESHOLD_SEC,
    *,
    ended: bool = False,
) -> tuple[str, Optional[float]]:
    """Classify the fleet as SCANNING, IDLE, or STALLED.

    STALLED  -- the engine heartbeat has not advanced within `stall_threshold`
                (or is absent entirely, or ended): a real alarm, not an empty table.
    SCANNING -- heartbeat fresh AND some service did active-phase work
                (scanning/filtering/quoting/settling) in the recent window.
    IDLE     -- heartbeat fresh but no active-phase work in the window.
    """
    age = None
    if hb_ts is not None:
        age = max(0.0, now - hb_ts)
    if ended or hb_ts is None or (age is not None and age > stall_threshold):
        return "STALLED", age
    if active_phases & {"scanning", "filtering", "quoting", "settling"}:
        return "SCANNING", age
    return "IDLE", age


def _parse_event_ts(ts: Any) -> Optional[float]:
    """Parse an ISO-8601 ring timestamp to a Unix timestamp, or None."""
    if not ts:
        return None
    try:
        dt = datetime.datetime.strptime(str(ts), "%Y-%m-%dT%H:%M:%SZ")
        return dt.replace(tzinfo=datetime.timezone.utc).timestamp()
    except (ValueError, TypeError):
        return None


def _parse_replay_ts(ts: Any) -> Optional[float]:
    """Parse an ISO-8601 string or numeric timestamp to a Unix float timestamp, or None."""
    if not ts:
        return None
    if isinstance(ts, (int, float)):
        return float(ts)
    s = str(ts).strip()
    try:
        dt = datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
        return dt.replace(tzinfo=datetime.timezone.utc).timestamp()
    except (ValueError, TypeError):
        pass
    try:
        clean = s.replace("Z", "+00:00") if s.endswith("Z") else s
        dt = datetime.datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        pass
    return None


_CONSOLE_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "WARN", "ERROR", "CRITICAL"})


def _split_console_level(text: str) -> tuple[Optional[str], str]:
    """(level, message) for a log line, consuming a level only when it is one.

    Any non-empty text that does not open with a level word comes back as the
    level-less message, unchanged. Reading the first word as a level regardless
    deleted it from the message: `Starting scan for 40 markets` became
    `scan for 40 markets`, one word short and with nothing to show for it.
    """
    m_br = re.match(r"^\[([A-Za-z]+)\]\s*(.*)$", text)
    if m_br and m_br.group(1).upper() in _CONSOLE_LEVELS:
        lvl, msg = m_br.group(1).upper(), m_br.group(2)
    else:
        m_word = re.match(r"^([A-Za-z]+):?\s+(.*)$", text)
        if not (m_word and m_word.group(1).upper() in _CONSOLE_LEVELS):
            return None, text.strip()
        lvl, msg = m_word.group(1).upper(), m_word.group(2)
    return ("WARNING" if lvl == "WARN" else lvl), msg.strip()


def _parse_console_line(
    line: str,
    file_mtime: Optional[float] = None,
    last_known_ts: Optional[float] = None,
) -> tuple[Optional[str], Optional[str], str, Optional[float]]:
    """Parse a console text log line into (ts_iso, level, clean_message, ts_unix).

    Recognizes:
    - Filter Loop header: ===== YYYY-MM-DD HH:MM:SS =====
    - Full datetime: YYYY-MM-DD HH:MM:SS [LEVEL] message
    - Shadow/trader time-only: HH:MM:SS LEVEL message (inferred from file_mtime)
    - Bracketed level: [LEVEL] message
    - Python Traceback / FAILED lines
    """
    clean = line.strip()
    if not clean:
        return None, None, "", last_known_ts

    # 1. Filter loop header: ===== 2026-10-27 18:00:00 =====
    m_hdr = re.match(r"^={3,}\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?)\s*={3,}$", clean)
    if m_hdr:
        try:
            dt = datetime.datetime.fromisoformat(m_hdr.group(1)).astimezone(datetime.timezone.utc)
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ"), "INFO", clean, dt.timestamp()
        except Exception:
            pass

    # 2. Time-only line: HH:MM:SS LEVEL message or [HH:MM:SS] LEVEL message
    m_time = re.match(r"^\[?(\d{2}:\d{2}:\d{2})\]?\s+(?:\[([A-Za-z]+)\]|([A-Za-z]+))\s+(.*)$", clean)
    if m_time:
        t_str, lvl1, lvl2, msg = m_time.groups()
        lvl = (lvl1 or lvl2).upper()
        if lvl in {"DEBUG", "INFO", "WARNING", "WARN", "ERROR", "CRITICAL"}:
            if lvl == "WARN":
                lvl = "WARNING"
            mtime = file_mtime or time.time()
            m_dt = datetime.datetime.fromtimestamp(mtime)
            try:
                h, mn, s = [int(x) for x in t_str.split(":")]
                dt = datetime.datetime(m_dt.year, m_dt.month, m_dt.day, h, mn, s)
                if dt.timestamp() > mtime + 60:
                    dt -= datetime.timedelta(days=1)
                dt_utc = dt.astimezone(datetime.timezone.utc)
                return dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), lvl, msg.strip(), dt_utc.timestamp()
            except Exception:
                pass

    # 3. Full datetime: 2026-10-27 18:00:00 [LEVEL] msg, ...Z LEVEL msg, or a
    #    timestamp followed by prose with no level at all.
    m_full = re.match(r"^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?Z?)\s*(.*)$", clean)
    if m_full:
        ds, rest = m_full.groups()
        try:
            dt = datetime.datetime.fromisoformat(ds.replace("Z", "+00:00").replace(",", ".")).astimezone(datetime.timezone.utc)
        except (ValueError, TypeError):
            dt = None
        if dt is not None:
            lvl, msg = _split_console_level(rest.strip())
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ"), lvl, msg or clean, dt.timestamp()

    # 4. Bracketed level without timestamp: [LEVEL] message
    m_lvl = re.match(r"^\[([A-Za-z]+)\]\s*(.*)$", clean)
    if m_lvl:
        lvl = m_lvl.group(1).upper()
        if lvl in {"DEBUG", "INFO", "WARNING", "WARN", "ERROR", "CRITICAL"}:
            if lvl == "WARN":
                lvl = "WARNING"
            ts_iso = (
                datetime.datetime.fromtimestamp(last_known_ts, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                if last_known_ts is not None
                else None
            )
            return ts_iso, lvl, m_lvl.group(2).strip(), last_known_ts

    # 5. Traceback or error keyword lines
    if clean.startswith("Traceback") or clean.startswith("FAILED:") or clean.startswith("ERROR:"):
        ts_iso = (
            datetime.datetime.fromtimestamp(last_known_ts, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if last_known_ts is not None
            else None
        )
        return ts_iso, "ERROR", clean, last_known_ts

    # 6. Default fallback line
    ts_iso = (
        datetime.datetime.fromtimestamp(last_known_ts, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if last_known_ts is not None
        else None
    )
    return ts_iso, None, clean, last_known_ts


def _last_per_service(events: list[dict]) -> dict[str, tuple[str, Optional[float]]]:
    """Latest (phase, unix ts) per service from ring events."""
    out: dict[str, tuple[str, Optional[float]]] = {}
    for ev in events:
        svc = str(ev.get("service") or "query")
        ts = _parse_event_ts(ev.get("ts"))
        if svc not in out or (
            ts is not None and (out[svc][1] is None or ts > out[svc][1])
        ):
            out[svc] = (str(ev.get("phase") or ""), ts)
    return out


def _read_engine_heartbeat() -> dict[str, Any]:
    """Read live/runtime/live_poll_heartbeat.json, returning {} when absent/invalid."""
    try:
        data = json.loads(resolve_heartbeat_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if isinstance(data, list) and data and isinstance(data[-1], dict):
        return data[-1]
    return {}


def _read_cycle_ring(
    tail: int = 400,
    path: Path | None = None,
) -> tuple[list[dict], dict[str, str] | None]:
    """Read a cycle ring and retain a structured error for read-only panels."""
    try:
        from core_brain.cycle_stream import read_ring
        return read_ring(path or resolve_ring_path(), tail=tail, strict=True), None
    except Exception as exc:
        return [], {
            "source": "cycle_ring",
            "error": str(exc),
            "error_type": type(exc).__name__,
        }


def _read_guardrail_heartbeat() -> dict[str, Any]:
    """Read the guardrail watcher's self-report, {} when absent/invalid."""
    try:
        data = json.loads(resolve_guardrail_heartbeat_path().read_text(
            encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if isinstance(data, list) and data and isinstance(data[-1], dict):
        return data[-1]
    return {}


def _guardrail_health() -> dict[str, Any]:
    """Watcher liveness (heartbeat) + alert totals (ring), one payload.

    Liveness: the watcher writes global_stop_loss_heartbeat.json every check
    (~5s). A heartbeat older than STALE_THRESHOLD_SEC means the watcher is
    down or dead -- the silent failure this endpoint exists to surface.
    Alerts: counted from `guardrail_alert` ring events, so the total survives
    watcher restarts (the heartbeat itself is per-process).
    """
    hb = _read_guardrail_heartbeat()
    ts = hb.get("ts")
    age_s = None
    if isinstance(ts, str):
        try:
            dt = datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
            age_s = (datetime.datetime.now(datetime.timezone.utc) - dt.replace(
                tzinfo=datetime.timezone.utc)).total_seconds()
        except ValueError:
            age_s = None

    ring_events, telemetry_error = _read_cycle_ring()
    ring_alerts = [
        ev for ev in ring_events
        if ev.get("action") == "guardrail_alert"
    ]
    ring_alerts.sort(key=lambda a: str(a.get("ts") or ""), reverse=True)
    newest = ring_alerts[0] if ring_alerts else {}

    STALE_THRESHOLD_SEC = 30.0
    return {
        "pid": hb.get("pid"),
        "started_at": hb.get("started_at"),
        "last_ts": ts,
        "cycle": hb.get("cycle"),
        "running": bool(hb) and age_s is not None and age_s <= STALE_THRESHOLD_SEC,
        "age_s": age_s,
        "alerts_total": len(ring_alerts),
        "last_alert_ts": newest.get("ts"),
        "last_alert_kind": newest.get("reason"),
        "telemetry_error": telemetry_error,
    }


def _read_cycle_intent_rows(db_path: Path | str, limit: int = 200) -> list[dict]:
    """Last `limit` cycle_intent rows in read-only mode; [] when unavailable."""
    path = Path(db_path)
    if not path.exists():
        return []
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    con = None
    try:
        con = sqlite3.connect(uri, uri=True, timeout=2.0)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT ts, cycle, market_slug, top_skip_reason, top_pass_reason, "
            "intent_count, submitted, cancelled FROM cycle_intent "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass


@app.get("/api/trial-readiness")
def get_trial_readiness():
    """Whether the near-miss evidence licenses a controlled gate trial.

    The ranker writes one line per rank to `runtime/near_misses.jsonl` and
    `runtime/volume_near_misses.jsonl`. Nothing read them back, so the evidence
    accumulated and was discarded: the screener could show that markets were
    refused, never that they were refused consistently enough to change a bar
    on purpose. Read-only, and a missing log reads as "no evidence yet" rather
    than as an error.
    """
    from core_brain.trial_readiness import readiness

    return readiness(
        resolve_runtime_file("near_misses.jsonl", root=LIVE_ROOT),
        resolve_runtime_file("volume_near_misses.jsonl", root=LIVE_ROOT),
    )


@app.get("/api/reliability")
@app.get("/api/shadow/reliability")
def api_shadow_reliability(db: str | None = None, run_id: str | None = None):
    """Score the active or requested database for authenticity and action reliability."""
    from core_brain.run_scorer import score_run
    target_db = resolve_db_path(db if db is not None else _ACTIVE_DB_OVERRIDE)
    if db is not None:
        try:
            import tempfile
            resolved_p = target_db.resolve()
            temp_root = Path(tempfile.gettempdir()).resolve()
            allowed = [
                LIVE_ROOT.resolve(),
                (LIVE_ROOT / "data").resolve(),
                (LIVE_ROOT / "runtime").resolve(),
                temp_root,
            ]
            if not any(resolved_p == root or root in resolved_p.parents for root in allowed):
                return JSONResponse({"error": "Forbidden database path"}, status_code=403)
        except Exception:
            return JSONResponse({"error": "Invalid database path"}, status_code=400)

    try:
        score = score_run(target_db, run_id=run_id)
        return JSONResponse(score.to_dict())
    except Exception as e:
        return JSONResponse({"error": f"Failed to score run: {e}"}, status_code=400)


@app.get("/api/scan-state")
def get_scan_state():
    """SCANNING / IDLE / STALLED plus per-cycle skip/pass rationale (read-only)."""
    now = time.time()
    events, telemetry_error = _read_cycle_ring()

    active_db = str(resolve_db_path(_ACTIVE_DB_OVERRIDE))
    try:
        shadow = _recent_shadow_run(active_db)
    except Exception:
        shadow = None

    stall_reason: str | None = None
    heartbeat_source: dict[str, Any]
    shadow_ended = False

    if shadow is not None:
        # A shadow rehearsal was registered for this active DB: its heartbeat is authoritative.
        # Even if the rehearsal is ended or stalled, its age is the shadow run's age,
        # never a stale engine heartbeat from an unrelated live run. Use the shadow's
        # own stale threshold (SHADOW_HEARTBEAT_MIN_STALE_S), not the 90s engine one,
        # or a healthy slow rotation would flip to STALLED between 90s and 120s.
        hb_ts = now - float(shadow.get("heartbeat_age_sec") or 0.0)
        cadence = float(shadow.get("cadence_sec") or shadow.get("interval") or 5.0)
        stale_threshold = max(SHADOW_HEARTBEAT_MIN_STALE_S,
                              SHADOW_HEARTBEAT_STALE_ROTATIONS * cadence)
        shadow_ended = bool(shadow.get("ended"))
        heartbeat_source = {
            "kind": "shadow_run",
            "file": shadow.get("heartbeat_file"),
            "run_id": shadow.get("run_id"),
            "db_path": shadow.get("db_path"),
            "pid": shadow.get("pid"),
        }
    else:
        hb = _read_engine_heartbeat()
        hb_path = resolve_heartbeat_path()
        hb_file = _format_heartbeat_rel_path(hb_path)
        cadence = None
        stale_threshold = SCAN_STALL_THRESHOLD_SEC
        if hb and hb.get("ts"):
            hb_ts = (hb.get("ts") or 0) / 1000.0
            heartbeat_source = {
                "kind": "live_engine",
                "file": hb_file,
                "run_id": None,
                "db_path": None,
                "pid": hb.get("pid"),
            }
        else:
            hb_ts = None
            if hb_path.exists():
                heartbeat_source = {
                    "kind": "live_engine",
                    "file": hb_file,
                    "run_id": None,
                    "db_path": None,
                    "pid": None,
                }
            else:
                heartbeat_source = {
                    "kind": "none",
                    "file": None,
                    "run_id": None,
                    "db_path": None,
                    "pid": None,
                }

    window = now - 60.0
    active_phases: set[str] = set()
    last_event_ts: Optional[float] = None
    last_scan_ts: Optional[float] = None
    for ev in events:
        ts = _parse_event_ts(ev.get("ts"))
        if ts is None:
            continue
        if last_event_ts is None or ts > last_event_ts:
            last_event_ts = ts
        if ts >= window:
            active_phases.add(str(ev.get("phase") or ""))
        if str(ev.get("service") or "") in ("filter", "screener") and (
            last_scan_ts is None or ts > last_scan_ts
        ):
            last_scan_ts = ts

    # The screener always appends to the live ring, never to a rehearsal's
    # per-run ring. When `events` above came from the shadow ring it carries no
    # filter events, so read the screener timestamp straight from the live ring.
    if last_scan_ts is None and _ACTIVE_RING_OVERRIDE is None and telemetry_error is None:
        live_events, live_error = _read_cycle_ring(
            path=resolve_runtime_file(CYCLE_RING_NAME, root=LIVE_ROOT)
        )
        if live_error is not None:
            telemetry_error = live_error
        for ev in live_events:
            if str(ev.get("service") or "") not in ("filter", "screener"):
                continue
            ts = _parse_event_ts(ev.get("ts"))
            if ts is not None and (last_scan_ts is None or ts > last_scan_ts):
                last_scan_ts = ts

    state, hb_age = compute_scan_state(
        last_event_ts, hb_ts, now, active_phases,
        stall_threshold=stale_threshold,
        ended=shadow_ended,
    )

    if state == "STALLED":
        if shadow is not None:
            stall_reason = shadow.get("end_reason") or "heartbeat_stale"
        else:
            if hb and hb.get("ts"):
                stall_reason = "heartbeat_stale"
            else:
                hb_path = resolve_heartbeat_path()
                if hb_path.exists():
                    stall_reason = "heartbeat_unreadable"
                else:
                    stall_reason = "no_heartbeat"

    try:
        other_live_runs = _recent_other_live_shadow_runs(active_db)
    except Exception:
        other_live_runs = []

    rows = _read_cycle_intent_rows(resolve_db_path(_ACTIVE_DB_OVERRIDE))
    skip_counts: dict[str, int] = {}
    pass_counts: dict[str, int] = {}
    for r in rows:
        sk = r.get("top_skip_reason")
        pk = r.get("top_pass_reason")
        if sk:
            skip_counts[sk] = skip_counts.get(sk, 0) + 1
        if pk:
            pass_counts[pk] = pass_counts.get(pk, 0) + 1

    return JSONResponse({
        "scan_state": state,
        # The cadence the verdict above was judged against, so the page can
        # draw its pill ramp from the same number instead of a second guess.
        "cadence_sec": round(cadence, 1) if cadence is not None else None,
        "stale_threshold_sec": round(stale_threshold, 1),
        "seconds_since_heartbeat": round(hb_age, 1) if hb_age is not None else None,
        "seconds_since_scan": (
            round(max(0.0, now - last_scan_ts), 1) if last_scan_ts is not None else None
        ),
        "last_scan_ts": last_scan_ts,
        "heartbeat_source": heartbeat_source,
        "stall_reason": stall_reason,
        "other_live_runs": other_live_runs,
        "services": {
            svc: {"phase": phase, "last_ts": ts}
            for svc, (phase, ts) in _last_per_service(events).items()
        },
        "decisions_logged": len(rows),
        "skip_reasons": sorted(
            [{"reason": k, "count": v} for k, v in skip_counts.items()],
            key=lambda x: -x["count"],
        ),
        "pass_reasons": sorted(
            [{"reason": k, "count": v} for k, v in pass_counts.items()],
            key=lambda x: -x["count"],
        ),
        "telemetry_error": telemetry_error,
    })


def _ring_file_key(st: Any) -> tuple:
    """Identity that changes when the engine rotates the ring via os.replace.

    st_ino distinguishes the replaced inode on POSIX; on Windows st_ino is 0,
    so fall back to creation time (st_ctime_ns), which os.replace changes.
    """
    if getattr(st, "st_ino", 0):
        return (st.st_dev, st.st_ino)
    return (st.st_dev, st.st_ctime_ns)


def _ring_replay_and_offset(fh, tail: int, stat_fn=os.fstat):
    """(replay lines, follow offset, file identity) for ONE open ring handle.

    All three answers come from the same handle. Reopening the path between
    them straddles a rotation: the replay would be the new ring's tail while
    the follow loop resumed at the old ring's end offset.

    The offset is taken BEFORE the tail read, not after. `tail_lines_fh` reads
    up to the EOF it saw when it started, so a later offset would sit past any
    line appended while the tail was being read -- that line would be in
    neither the replay nor the follow range, and silently lost. Taking it first
    makes the two ranges overlap: the worst case is one line delivered twice,
    which for a telemetry stream beats one delivered never.
    """
    from core_brain.cycle_stream import tail_lines_fh

    offset = fh.seek(0, os.SEEK_END)
    file_key = _ring_file_key(stat_fn(fh.fileno()))
    return tail_lines_fh(fh, tail), offset, file_key


def _cycle_stream_sse(
    ring_path: Path,
    tail: int = SSE_REPLAY_LINES,
    poll_sec: float = SSE_POLL_SEC,
    live_marks=None,
) -> Generator[str, None, None]:
    """Yield SSE frames for the cycle-telemetry ring: replay tail, then follow appends.

    The engine rotates the ring past 500 lines by atomically replacing the file.
    Replacement is detected by file identity (inode on POSIX, creation time on
    Windows) rather than size alone, so a replacement larger than the current
    read offset is still seen. On rotation we emit an ``event: rotate`` frame
    and re-sync from the new file's start.

    After the ring replay each connection gets one ``event: mark`` snapshot
    frame from the shared live-marks cache (#427), then deltas as the feed
    moves and a reset frame after every venue disconnect. ``live_marks`` is
    an injectable cache (tests); ``None`` uses the module singleton. No
    venue work happens here -- the worker thread feeds the cache, the
    generator only reads it.
    """
    last_keepalive = time.time()
    marks_cache = live_marks if live_marks is not None else _live_marks_cache()

    def _wait(sec: float) -> None:
        # Condition-wait so a mark wakes the stream early; a timeout so the
        # ring poll and the keepalive still tick with no feed. Falls back to
        # a plain sleep for foreign cache doubles without a wait method.
        try:
            marks_cache.wait(timeout=sec)
        except (AttributeError, TypeError):
            time.sleep(sec)

    def _frame(line: str) -> str:
        return f"data: {line.strip()}\n\n"

    offset = 0
    file_key = None
    if ring_path.exists():
        try:
            # Seek to the tail rather than reading every line to keep the
            # last few: the ring grows for the life of a run (33.8 MB on a
            # one-day rehearsal) and every page load opens this stream.
            with open(ring_path, "rb") as fh:
                replay, offset, file_key = _ring_replay_and_offset(fh, tail)
            for line in replay:
                if line.strip():
                    yield _frame(line)
        except OSError:
            pass

    try:
        seq, marks = marks_cache.snapshot()
        last_reset = marks_cache.reset_generation
    except AttributeError:
        seq, marks = 0, []
        last_reset = 0
    yield _mark_frame(seq, True, False, marks)
    last_seq = seq

    while True:
        try:
            if not ring_path.exists():
                _wait(poll_sec)
                continue
            st = ring_path.stat()
            key = _ring_file_key(st)
            size = st.st_size
            if file_key is not None and (key != file_key or size < offset):
                offset = 0
                yield "event: rotate\ndata: {}\n\n"
            file_key = key
            if size > offset:
                with open(ring_path, "r", encoding="utf-8", errors="replace") as fh:
                    fh.seek(offset)
                    for line in fh:
                        if line.strip():
                            yield _frame(line)
                    offset = fh.tell()
            try:
                seq, marks = marks_cache.snapshot()
                gen = marks_cache.reset_generation
            except AttributeError:
                seq, marks, gen = last_seq, [], last_reset
            if gen != last_reset:
                last_reset = gen
                last_seq = seq
                yield _mark_frame(seq, False, True, marks)
            elif seq != last_seq:
                # Delta carries only marks that moved since this stream's
                # seq; removals ride the 30s browser max-age instead of a
                # tombstone here, the same bound poll renders already use.
                try:
                    delta = marks_cache.marks_since(last_seq)
                except AttributeError:
                    delta = marks
                last_seq = seq
                yield _mark_frame(seq, False, False, delta)
            if time.time() - last_keepalive >= SSE_KEEPALIVE_SEC:
                yield ": keepalive\n\n"
                last_keepalive = time.time()
            _wait(poll_sec)
        except OSError:
            _wait(poll_sec)


PAIRS_ACTION_PREFIX = "pairs_"


@app.get("/api/pairs-activity")
def pairs_activity():
    """Aggregate U35 auto-pairs activity from the cycle ring.

    Counts every pairs_* action (completed/exited/would_complete/would_exit/
    hold/balanced/error) overall and per latest cycle, plus each pair's most
    recent action with its timestamp. Read-only; the ring is the source.
    """
    events, telemetry_error = _read_cycle_ring()
    totals: dict[str, int] = {}
    per_cycle: dict[int, dict[str, int]] = {}
    per_pair: dict[str, dict] = {}
    for ev in events:
        a = str(ev.get("action") or "")
        if not a.startswith(PAIRS_ACTION_PREFIX):
            continue
        action = a[len(PAIRS_ACTION_PREFIX):]
        totals[action] = totals.get(action, 0) + 1
        cycle = ev.get("cycle")
        if cycle is not None:
            pc = per_cycle.setdefault(int(cycle), {})
            pc[action] = pc.get(action, 0) + 1
        pid = (ev.get("extra") or {}).get("pair_id")
        if pid:
            per_pair[str(pid)] = {
                "action": action,
                "ts": ev.get("ts"),
                "cycle": ev.get("cycle"),
            }
    last_cycle = max(per_cycle) if per_cycle else None
    return {
        "totals": totals,
        "last_cycle": last_cycle,
        "last_cycle_counts": per_cycle.get(last_cycle, {})
        if last_cycle is not None else {},
        "per_pair": [
            {"pair_id": pid, **info}
            for pid, info in sorted(per_pair.items())
        ],
        "telemetry_error": telemetry_error,
    }


@app.get("/api/guardrail-alerts")
def guardrail_alerts():
    """Active guardrail violations as a visible banner payload.

    Reads the cycle ring for `guardrail_alert` events (emitted by
    live/scripts/global_stop_loss.py on a repeated pair exit or an over-cap
    pair) and returns them newest-first. The dashboard renders the most
    recent one as a red banner so a violation is visible, not just a log
    line. Read-only; the ring is the source.
    """
    events, telemetry_error = _read_cycle_ring()
    alerts = []
    for ev in events:
        if ev.get("action") != "guardrail_alert":
            continue
        alerts.append({
            "ts": ev.get("ts"),
            "cycle": ev.get("cycle"),
            "kind": ev.get("reason"),
            "subject": (ev.get("extra") or {}).get("subject"),
            "detail": (ev.get("extra") or {}).get("detail"),
        })
    alerts.sort(key=lambda a: str(a["ts"] or ""), reverse=True)
    return {"alerts": alerts, "telemetry_error": telemetry_error}


@app.get("/api/guardrail-health")
def guardrail_health():
    """Watcher health: running pid, restart time, alert count.

    Reads the watcher's self-report heartbeat (global_stop_loss_heartbeat.json,
    written every check) for liveness and the ring for the cumulative alert
    count, so a dead watcher is visible on the dashboard instead of failing
    silently. Read-only; both files are sources of truth.
    """
    return _guardrail_health()


@app.get("/api/cycle-stream")
def cycle_stream_events():
    """Server-Sent-Events tail of live/runtime/cycle_events.jsonl."""
    return StreamingResponse(
        _cycle_stream_sse(resolve_ring_path()),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


_RUN_ID_SAFE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def _validate_run_id(run_id: str | None) -> str | None:
    if not run_id:
        return None
    if not _RUN_ID_SAFE_RE.match(run_id) or ".." in run_id or "/" in run_id or "\\" in run_id:
        raise HTTPException(status_code=400, detail="Invalid run_id format")
    return run_id


def _resolve_log_families(run_id: str | None = None) -> dict[str, dict]:
    """Resolve ring paths, text logs, and availability per log family.

    Returns dict mapping family name ('live', 'filter', 'shadow') to metadata.
    Callers never supply raw file paths.
    """
    rt_dir = runtime_file("").resolve()

    # Live family
    live_ring = _ACTIVE_RING_OVERRIDE if _ACTIVE_RING_OVERRIDE is not None else resolve_runtime_file(CYCLE_RING_NAME, root=LIVE_ROOT)
    live_avail = live_ring.exists()
    families = {
        "live": {
            "available": live_avail,
            "reason": None if live_avail else "Cycle ring not found on disk",
            "labels": [live_ring.name],
            "run_id": None,
            "ring_path": live_ring,
            "ring_filter": lambda ev: "filter" not in str(ev.get("service") or "").lower() and "screener" not in str(ev.get("service") or "").lower(),
            "text_logs": [],
        }
    }

    # Filter family
    filter_ring = live_ring
    filter_text_logs: list[Path] = []
    rerank = resolve_runtime_file("rerank.log", root=LIVE_ROOT)
    if rerank.resolve().parent == rt_dir:
        filter_text_logs.append(rerank)
    try:
        for ext in [".out.log", ".err.log"]:
            matches = list(rt_dir.glob(f"screener-*{ext}")) + list(rt_dir.glob(f"resume_screener-*{ext}"))
            if matches:
                newest = max(matches, key=lambda p: p.stat().st_mtime)
                if newest not in filter_text_logs and newest.resolve().parent == rt_dir:
                    filter_text_logs.append(newest)
    except OSError:
        pass

    filter_labels = [f"{filter_ring.name} (service=filter)"] + [p.name for p in filter_text_logs]
    filter_avail = filter_ring.exists() or any(p.exists() for p in filter_text_logs)
    families["filter"] = {
        "available": filter_avail,
        "reason": None if filter_avail else "No filter events or rerank.log yet",
        "labels": filter_labels,
        "run_id": None,
        "ring_path": filter_ring,
        "ring_filter": lambda ev: "filter" in str(ev.get("service") or "").lower() or "screener" in str(ev.get("service") or "").lower(),
        "text_logs": filter_text_logs,
    }

    # Shadow family
    shadow_ring = None
    shadow_run_id = None
    shadow_avail = False
    shadow_reason = None
    shadow_text_logs: list[Path] = []

    if run_id:
        _validate_run_id(run_id)
        from core_brain.shadow_run import _run_ring_name
        ring_name = _run_ring_name(run_id)
        candidate = resolve_runtime_file(ring_name, root=LIVE_ROOT)
        known_ids = {r.get("run_id") for r in list_shadow_runs() if r.get("run_id")}
        if run_id not in known_ids and not candidate.exists():
            raise HTTPException(status_code=400, detail=f"Unlisted run_id: {run_id}")
        if candidate.resolve().parent == rt_dir:
            shadow_ring = candidate
            shadow_run_id = run_id
            shadow_avail = candidate.exists()
            if not shadow_avail:
                shadow_reason = f"Shadow rehearsal ring not found on disk"
        else:
            shadow_run_id = run_id
            shadow_avail = False
            shadow_reason = f"Shadow rehearsal ring not found on disk"
    else:
        candidate = _resolve_shadow_ring_path()
        if candidate is not None:
            shadow_ring = candidate
            shadow_avail = candidate.exists()
            try:
                active_db = str(resolve_db_path(_ACTIVE_DB_OVERRIDE))
                shadow_info = _recent_shadow_run(active_db)
                shadow_run_id = shadow_info.get("run_id") if shadow_info else None
            except Exception:
                shadow_run_id = None
            if not shadow_avail:
                shadow_reason = "Shadow rehearsal ring not found on disk"
        else:
            shadow_avail = False
            shadow_reason = "No active shadow rehearsal"

    if shadow_run_id and _RUN_ID_SAFE_RE.match(shadow_run_id):
        for ext in [".out.log", ".err.log"]:
            fresh = runtime_file(f"shadow_run-{shadow_run_id}{ext}")
            resume = runtime_file(f"shadow_resume-{shadow_run_id}{ext}")
            chosen = None
            if fresh.exists() and resume.exists():
                chosen = fresh if fresh.stat().st_mtime >= resume.stat().st_mtime else resume
            elif fresh.exists():
                chosen = fresh
            elif resume.exists():
                chosen = resume
            if chosen and chosen.resolve().parent == rt_dir:
                shadow_text_logs.append(chosen)

    shadow_labels = ([shadow_ring.name] if shadow_ring else []) + [p.name for p in shadow_text_logs]
    families["shadow"] = {
        "available": shadow_avail,
        "reason": shadow_reason,
        "labels": shadow_labels,
        "run_id": shadow_run_id,
        "ring_path": shadow_ring,
        "ring_filter": None,
        "text_logs": shadow_text_logs,
    }

    return families


def _log_stream_sse(
    families: list[str],
    run_id: str | None = None,
    tail: int = SSE_REPLAY_LINES,
    poll_sec: float = SSE_POLL_SEC,
) -> Generator[str, None, None]:
    """Yield SSE frames for per-process log streams without mark frames.

    Events:
    - ``event: source``: hello per family with available state, labels, reason, run_id.
    - ``event: log``: {family, source, event} or {family, source, text, file}.
    - ``event: rotate``: {family, source}.
    """
    from core_brain.cycle_stream import tail_lines_fh

    last_keepalive = time.time()
    families_map = _resolve_log_families(run_id=run_id)

    # 1. Emit source hello frame per requested family
    for fam in families:
        fam_info = families_map.get(fam)
        if fam_info:
            hello = {
                "family": fam,
                "labels": fam_info.get("labels", []),
                "available": fam_info.get("available", False),
                "reason": fam_info.get("reason"),
                "run_id": fam_info.get("run_id"),
            }
            yield f"event: source\ndata: {json.dumps(hello)}\n\n"

    # 2. Setup followers & collect replay tail
    followers: list[dict] = []
    replay_records: list[dict] = []

    for fam_idx, fam in enumerate(families):
        fam_info = families_map.get(fam, {})
        ring_path = fam_info.get("ring_path")
        r_filter = fam_info.get("ring_filter")
        if ring_path and ring_path.exists():
            matched_evs = []
            try:
                with open(ring_path, "rb") as fh:
                    if r_filter is not None:
                        raw_lines = tail_lines_fh(fh, max(500, tail * 5))
                        offset = fh.seek(0, os.SEEK_END)
                        file_key = _ring_file_key(os.fstat(fh.fileno()))
                        for raw in raw_lines:
                            line_str = (raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)).strip()
                            if not line_str:
                                continue
                            try:
                                ev = json.loads(line_str)
                            except Exception:
                                continue
                            if r_filter(ev):
                                matched_evs.append(ev)
                        matched_evs = matched_evs[-tail:]
                    else:
                        replay, offset, file_key = _ring_replay_and_offset(fh, tail)
                        for line in replay:
                            clean = (line.decode("utf-8", errors="replace") if isinstance(line, bytes) else str(line)).strip()
                            if not clean:
                                continue
                            try:
                                ev = json.loads(clean)
                            except Exception:
                                continue
                            matched_evs.append(ev)

                ring_mtime = ring_path.stat().st_mtime if ring_path.exists() else None
                ring_items = []
                for ev in matched_evs:
                    ts_val = _parse_replay_ts(ev.get("ts"))
                    ring_items.append({"ev": ev, "sort_ts": ts_val})

                # Bidirectional timestamp fill within this ring stream
                cur_ts = None
                for it in ring_items:
                    if it["sort_ts"] is not None:
                        cur_ts = it["sort_ts"]
                    elif cur_ts is not None:
                        it["sort_ts"] = cur_ts
                cur_ts = None
                for it in reversed(ring_items):
                    if it["sort_ts"] is not None:
                        cur_ts = it["sort_ts"]
                    elif cur_ts is not None:
                        it["sort_ts"] = cur_ts
                for it in ring_items:
                    if it["sort_ts"] is None:
                        it["sort_ts"] = ring_mtime or 0.0

                for line_idx, it in enumerate(ring_items):
                    payload = {"family": fam, "source": "ring", "event": it["ev"]}
                    replay_records.append({
                        "sort_ts": it["sort_ts"],
                        "family_idx": fam_idx,
                        "source_idx": 0,
                        "line_idx": line_idx,
                        "payload": payload,
                    })

                followers.append({
                    "family": fam,
                    "source": "ring",
                    "path": ring_path,
                    "offset": offset,
                    "file_key": file_key,
                    "filter_fn": r_filter,
                    "label": ring_path.name,
                })
            except OSError:
                followers.append({
                    "family": fam,
                    "source": "ring",
                    "path": ring_path,
                    "offset": 0,
                    "file_key": None,
                    "filter_fn": r_filter,
                    "label": ring_path.name,
                })
        elif ring_path:
            followers.append({
                "family": fam,
                "source": "ring",
                "path": ring_path,
                "offset": 0,
                "file_key": None,
                "filter_fn": r_filter,
                "label": ring_path.name,
            })

        for src_idx, t_path in enumerate(fam_info.get("text_logs", []), start=1):
            if t_path.exists():
                text_lines = []
                try:
                    with open(t_path, "rb") as fh:
                        raw_lines = tail_lines_fh(fh, tail)
                        offset = fh.seek(0, os.SEEK_END)
                        file_key = _ring_file_key(os.fstat(fh.fileno()))
                        for raw in raw_lines:
                            line_str = (raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)).strip()
                            if line_str:
                                text_lines.append(line_str[:1000])

                    t_mtime = t_path.stat().st_mtime if t_path.exists() else None
                    text_items = []
                    last_known_ts = None
                    for line_str in text_lines:
                        ts_iso, lvl, msg, ts_u = _parse_console_line(line_str, file_mtime=t_mtime, last_known_ts=last_known_ts)
                        if ts_u is not None:
                            last_known_ts = ts_u
                        text_items.append({
                            "text": line_str,
                            "ts_iso": ts_iso,
                            "level": lvl,
                            "message": msg[:1000],
                            "sort_ts": ts_u,
                        })

                    # Bidirectional timestamp fill within this text stream
                    cur_ts = None
                    for it in text_items:
                        if it["sort_ts"] is not None:
                            cur_ts = it["sort_ts"]
                        elif cur_ts is not None:
                            it["sort_ts"] = cur_ts
                            if not it["ts_iso"]:
                                it["ts_iso"] = datetime.datetime.fromtimestamp(cur_ts, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    cur_ts = None
                    for it in reversed(text_items):
                        if it["sort_ts"] is not None:
                            cur_ts = it["sort_ts"]
                        elif cur_ts is not None:
                            it["sort_ts"] = cur_ts
                            if not it["ts_iso"]:
                                it["ts_iso"] = datetime.datetime.fromtimestamp(cur_ts, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    for it in text_items:
                        if it["sort_ts"] is None:
                            it["sort_ts"] = t_mtime or 0.0
                            if not it["ts_iso"] and t_mtime:
                                it["ts_iso"] = datetime.datetime.fromtimestamp(t_mtime, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

                    for line_idx, it in enumerate(text_items):
                        payload = {
                            "family": fam,
                            "source": "text",
                            "file": t_path.name,
                            "text": it["text"],
                            "ts": it["ts_iso"],
                            "level": it["level"],
                            "message": it["message"],
                        }
                        replay_records.append({
                            "sort_ts": it["sort_ts"],
                            "family_idx": fam_idx,
                            "source_idx": src_idx,
                            "line_idx": line_idx,
                            "payload": payload,
                        })

                    followers.append({
                        "family": fam,
                        "source": "text",
                        "path": t_path,
                        "offset": offset,
                        "file_key": file_key,
                        "filter_fn": None,
                        "label": t_path.name,
                        "last_known_ts": last_known_ts,
                    })
                except OSError:
                    followers.append({
                        "family": fam,
                        "source": "text",
                        "path": t_path,
                        "offset": 0,
                        "file_key": None,
                        "filter_fn": None,
                        "label": t_path.name,
                    })
            else:
                followers.append({
                    "family": fam,
                    "source": "text",
                    "path": t_path,
                    "offset": 0,
                    "file_key": None,
                    "filter_fn": None,
                    "label": t_path.name,
                })

    # Sort historical replay chronologically with stable tie-breaking:
    replay_records.sort(key=lambda r: (r["sort_ts"], r["family_idx"], r["source_idx"], r["line_idx"]))
    for r in replay_records:
        yield f"event: log\ndata: {json.dumps(r['payload'])}\n\n"

    # 3. Follow loop
    rt_dir = runtime_file("").resolve()
    while True:
        if "filter" in families:
            try:
                for ext in [".out.log", ".err.log"]:
                    matches = list(rt_dir.glob(f"screener-*{ext}")) + list(rt_dir.glob(f"resume_screener-*{ext}"))
                    if matches:
                        newest = max(matches, key=lambda p: p.stat().st_mtime)
                        if newest.resolve().parent == rt_dir and not any(f.get("path") == newest for f in followers):
                            followers.append({
                                "family": "filter",
                                "source": "text",
                                "path": newest,
                                "offset": 0,
                                "file_key": None,
                                "filter_fn": None,
                                "label": newest.name,
                            })
            except OSError:
                pass

        for f in followers:
            path = f["path"]
            if not path.exists():
                continue
            try:
                st = path.stat()
                key = _ring_file_key(st)
                size = st.st_size
                if f["file_key"] is not None and (key != f["file_key"] or size < f["offset"]):
                    f["offset"] = 0
                    yield f"event: rotate\ndata: {json.dumps({'family': f['family'], 'source': f['source']})}\n\n"
                f["file_key"] = key
                if size > f["offset"]:
                    with open(path, "r", encoding="utf-8", errors="replace") as fh:
                        fh.seek(f["offset"])
                        lines_read = 0
                        while True:
                            line = fh.readline()
                            if not line:
                                break
                            f["offset"] = fh.tell()
                            lines_read += 1
                            clean = line.strip()
                            if not clean:
                                if lines_read >= 500:
                                    break
                                continue
                            if f["source"] == "ring":
                                try:
                                    ev = json.loads(clean)
                                except Exception:
                                    ev = None
                                if ev is not None:
                                    if f["filter_fn"] is None or f["filter_fn"](ev):
                                        yield f"event: log\ndata: {json.dumps({'family': f['family'], 'source': 'ring', 'event': ev})}\n\n"
                            else:
                                t_mtime = None
                                try:
                                    t_mtime = path.stat().st_mtime
                                except OSError:
                                    pass
                                ts_iso, lvl, msg, ts_u = _parse_console_line(clean, file_mtime=t_mtime, last_known_ts=f.get("last_known_ts"))
                                if ts_u is not None:
                                    f["last_known_ts"] = ts_u
                                payload = {
                                    "family": f["family"],
                                    "source": "text",
                                    "file": f["label"],
                                    "text": clean[:1000],
                                    "ts": ts_iso,
                                    "level": lvl,
                                    "message": msg[:1000],
                                }
                                yield f"event: log\ndata: {json.dumps(payload)}\n\n"
                            if lines_read >= 500:
                                break
            except OSError:
                pass

        if time.time() - last_keepalive >= SSE_KEEPALIVE_SEC:
            yield ": keepalive\n\n"
            last_keepalive = time.time()

        time.sleep(poll_sec)


@app.get("/api/log-stream")
def log_stream_events(
    family: list[str] = Query(default=None),
    run_id: str | None = Query(default=None),
):
    """Server-Sent-Events multi-family log and telemetry stream."""
    valid_fams = {"live", "filter", "shadow"}
    selected_fams: list[str] = []
    if family:
        for f in family:
            for part in f.split(","):
                part = part.strip().lower()
                if not part:
                    continue
                if part == "all":
                    for name in ["live", "filter", "shadow"]:
                        if name not in selected_fams:
                            selected_fams.append(name)
                elif part in valid_fams:
                    if part not in selected_fams:
                        selected_fams.append(part)
                else:
                    raise HTTPException(status_code=400, detail=f"Unknown family: {part}")
    if not selected_fams:
        selected_fams = ["live", "filter", "shadow"]

    sanitized_run_id = None
    if run_id:
        sanitized_run_id = _validate_run_id(run_id)
        from core_brain.shadow_run import _run_ring_name
        ring_name = _run_ring_name(sanitized_run_id)
        candidate = resolve_runtime_file(ring_name, root=LIVE_ROOT)
        known_ids = {r.get("run_id") for r in list_shadow_runs() if r.get("run_id")}
        if sanitized_run_id not in known_ids and not candidate.exists():
            raise HTTPException(status_code=400, detail=f"Unlisted run_id: {sanitized_run_id}")

    return StreamingResponse(
        _log_stream_sse(families=selected_fams, run_id=sanitized_run_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/parameters")
def get_parameters(registry=None):
    """Return active strategy settings, trigger thresholds, and action descriptions.

    Consolidates safety limits from core_brain.config:MakerConfig (bot_pos_price_max_pair_cost_usd,
    bot_risk_exposure_max_naked_usd, bot_quote_size_min_shares, bot_risk_exposure_max_order_usd,
    bot_risk_exposure_max_total_usd) and the sweep
    interval from the dashboard's own config. One config object, not two.
    """
    from core_brain.config import load as load_cfg, derive_dynamic_caps
    # Display-only endpoint: never places an order. `for_display=True` keeps an
    # inherited rehearsal-only trial knob from 500-ing this panel (see
    # core_brain.config.load).
    cfg = load_cfg(for_display=True)
    portfolio_usd = None
    try:
        reg = registry
        if reg is None:
            from core_brain.order_registry import OrderRegistry
            # Build from the dashboard's active store, not DEFAULT_DB_PATH: a
            # panel that reads data/orders.db ignores --db / set_db_override and
            # shows the production wallet's caps against a demo or shadow DB.
            reg = OrderRegistry(db_path=resolve_db_path(_ACTIVE_DB_OVERRIDE))
        am = reg.get_latest_account_mark()
        if am and am.get("account_value_usd") is not None and float(am["account_value_usd"]) > 0:
            portfolio_usd = float(am["account_value_usd"])
        # Run-scoped lookup returns None after a restart until the new run writes
        # its first sweep. Fall back to the most recent global mark so the
        # Strategy Parameters tile and the Total Portfolio Value tile never
        # diverge — both should read the same venue account value.
        if portfolio_usd is None:
            try:
                all_marks = reg.get_all_account_marks()
                if all_marks:
                    # get_all_account_marks is ordered ASC by ts; last is newest
                    for m in reversed(all_marks):
                        v = m.get("account_value_usd")
                        if v is not None and float(v) > 0:
                            portfolio_usd = float(v)
                            break
            except BaseException:
                pass
    except BaseException as exc:
        logger.debug("get_parameters: using bankroll fallback (%s)", exc)

    dynamic = derive_dynamic_caps(cfg, portfolio_usd)
    naked_usd = dynamic["bot_risk_exposure_max_naked_usd"]
    order_usd = dynamic["bot_risk_exposure_max_order_usd"]
    total_usd = dynamic["bot_risk_exposure_max_total_usd"]
    basis_label = "account value" if portfolio_usd is not None else "bankroll"

    sweep = resolve_sweep_interval()

    from scoring.config import load as load_scoring
    scoring_error = None
    try:
        scfg = load_scoring()
    except (TypeError, ValueError) as exc:
        scoring_error = f"{type(exc).__name__}: {exc}"
        logger.warning(
            "get_parameters: scoring config load failed; screener cards show a "
            "placeholder instead of engine values (%s)",
            scoring_error,
        )
        # Every scfg read renders the marker, so no engine value is ever
        # presented under the screener scope (#709). The __format__ override
        # lets all 19 f-string read sites keep their format specs.
        scfg = _NoScoringConfig(scoring_error)

    params = [
        # =====================================================================
        # SCOPE 1: MARKET FILTERING (MARKET SCREENER)
        # =====================================================================
        # Subgroup 1: Active / In-Play Markets (Live Sports & Esports)
        {
            "name": "filter_live_vol_min_24h_usd",
            "key": "filter_live_vol_min_24h_usd",
            "display_name": "Live Event 24h Volume Floor",
            "scope": "market_screener",
            "scope_label": "Scope 1: Market Screener",
            "subgroup": "active_live",
            "subgroup_label": "Active / In-Play Markets (Live Sports & Esports)",
            "value": f"${cfg.filter_live_vol_min_24h_usd:,.2f}",
            "raw_value": cfg.filter_live_vol_min_24h_usd,
            "default": "$35,000.00",
            "env_var": "FILTER_LIVE_VOL_MIN_24H_USD",
            "category": "Live Screening",
            "badge": "hard-limit",
            "trigger": f"In-play event (/events?live=true) 24h volume < ${cfg.filter_live_vol_min_24h_usd:,.2f}",
            "action": "Filters out illiquid live matches to protect against dead-flat or untradable tape",
        },
        {
            "name": "filter_live_vel_lookback_sec",
            "key": "filter_live_vel_lookback_sec",
            "display_name": "Movement Lookback Window",
            "scope": "market_screener",
            "scope_label": "Scope 1: Market Screener",
            "subgroup": "active_live",
            "subgroup_label": "Active / In-Play Markets (Live Sports & Esports)",
            "value": f"{scfg.filter_live_vel_lookback_sec:.0f}s",
            "raw_value": scfg.filter_live_vel_lookback_sec,
            "default": "1800s",
            "env_var": "FILTER_LIVE_VEL_LOOKBACK_SEC",
            "category": "Velocity Filter",
            "badge": "cadence",
            "trigger": "Lookback window for recent trade tape movement",
            "action": "Evaluates recent trading velocity across 30-minute window before admitting live candidates",
        },
        {
            "name": "filter_live_vel_min_usd",
            "key": "filter_live_vel_min_usd",
            "display_name": "Min Traded Movement Notional",
            "scope": "market_screener",
            "scope_label": "Scope 1: Market Screener",
            "subgroup": "active_live",
            "subgroup_label": "Active / In-Play Markets (Live Sports & Esports)",
            "value": f"${scfg.filter_live_vel_min_usd:,.2f}",
            "raw_value": scfg.filter_live_vel_min_usd,
            "default": "$200.00",
            "env_var": "FILTER_LIVE_VEL_MIN_USD",
            "category": "Velocity Filter",
            "badge": "hard-limit",
            "trigger": f"Traded volume inside movement window < ${scfg.filter_live_vel_min_usd:,.2f}",
            "action": "Disqualifies dead markets that traded previously but have halted now",
        },
        {
            "name": "filter_live_activity_min_ratio",
            "key": "filter_live_activity_min_ratio",
            "display_name": "Min Movement Activity Ratio",
            "scope": "market_screener",
            "scope_label": "Scope 1: Market Screener",
            "subgroup": "active_live",
            "subgroup_label": "Active / In-Play Markets (Live Sports & Esports)",
            "value": f"{scfg.filter_live_activity_min_ratio*100:.1f}%",
            "raw_value": scfg.filter_live_activity_min_ratio,
            "default": "0.0% (off)",
            "env_var": "FILTER_LIVE_ACTIVITY_MIN_RATIO",
            "category": "Velocity Filter",
            "badge": "dynamic",
            "trigger": f"Observed activity share relative to market size < {scfg.filter_live_activity_min_ratio*100:.1f}%",
            "action": "Demands minimum active trading share before admitting candidate",
        },
        {
            "name": "filter_live_move_min_range_cents",
            "key": "filter_live_move_min_range_cents",
            "display_name": "Min Price Range Volatility",
            "scope": "market_screener",
            "scope_label": "Scope 1: Market Screener",
            "subgroup": "active_live",
            "subgroup_label": "Active / In-Play Markets (Live Sports & Esports)",
            "value": f"{scfg.filter_live_move_min_range_cents:.1f}¢",
            "raw_value": scfg.filter_live_move_min_range_cents,
            "default": "2.0¢",
            "env_var": "FILTER_LIVE_MOVE_MIN_RANGE_CENTS",
            "category": "Volatility Filter",
            "badge": "hard-limit",
            "trigger": f"Observed price range within window < {scfg.filter_live_move_min_range_cents:.1f}¢",
            "action": "Rejects static, pinned markets lacking two-sided price discovery",
        },
        {
            "name": "filter_live_move_lookback_sec",
            "key": "filter_live_move_lookback_sec",
            "display_name": "Volatility Evaluation Horizon",
            "scope": "market_screener",
            "scope_label": "Scope 1: Market Screener",
            "subgroup": "active_live",
            "subgroup_label": "Active / In-Play Markets (Live Sports & Esports)",
            "value": f"{scfg.filter_live_move_lookback_sec:.0f}s",
            "raw_value": scfg.filter_live_move_lookback_sec,
            "default": "7200s",
            "env_var": "FILTER_LIVE_MOVE_LOOKBACK_SEC",
            "category": "Volatility Filter",
            "badge": "cadence",
            "trigger": "Volatility measurement lookback window",
            "action": "Samples high-to-low price dispersion across 2-hour window",
        },
        {
            "name": "filter_live_gate_velocity_on",
            "key": "filter_live_gate_velocity_on",
            "display_name": "Velocity Gate Active",
            "scope": "market_screener",
            "scope_label": "Scope 1: Market Screener",
            "subgroup": "active_live",
            "subgroup_label": "Active / In-Play Markets (Live Sports & Esports)",
            "value": f"{scfg.filter_live_gate_velocity_on}",
            "raw_value": scfg.filter_live_gate_velocity_on,
            "default": "True",
            "env_var": "FILTER_LIVE_GATE_VELOCITY_ON",
            "category": "Gate Switch",
            "badge": "safety",
            "trigger": "Velocity gate toggle",
            "action": "Enforces strict volume and price-range checks on candidates",
        },
        # Subgroup 2: Standard / Pre-Match & Long-Dated Markets
        {
            "name": "filter_std_vol_min_24h_usd",
            "key": "filter_std_vol_min_24h_usd",
            "display_name": "Standard 24h Volume Floor",
            "scope": "market_screener",
            "scope_label": "Scope 1: Market Screener",
            "subgroup": "standard",
            "subgroup_label": "Standard / Pre-Match & Long-Dated Markets",
            "value": f"${cfg.filter_std_vol_min_24h_usd:,.2f}",
            "raw_value": cfg.filter_std_vol_min_24h_usd,
            "default": "$125,000.00",
            "env_var": "FILTER_STD_VOL_MIN_24H_USD",
            "category": "Universe Screening",
            "badge": "hard-limit",
            "trigger": f"Standard/pre-match 24h volume < ${cfg.filter_std_vol_min_24h_usd:,.2f}",
            "action": "Guarantees sufficient trading depth across the full market lifecycle",
        },
        {
            "name": "filter_common_vol_min_top3_depth_usd",
            "key": "filter_common_vol_min_top3_depth_usd",
            "display_name": "Top-3 Level Depth Floor",
            "scope": "market_screener",
            "scope_label": "Scope 1: Market Screener",
            "subgroup": "standard",
            "subgroup_label": "Standard / Pre-Match & Long-Dated Markets",
            "value": f"${cfg.filter_common_vol_min_top3_depth_usd:,.2f}",
            "raw_value": cfg.filter_common_vol_min_top3_depth_usd,
            "default": "$500.00",
            "env_var": "",
            "category": "Liquidity Floor",
            "badge": "hard-limit",
            "trigger": f"Summed top-3 bid depth on YES or NO <= ${cfg.filter_common_vol_min_top3_depth_usd:,.2f}",
            "action": "Refuses thin books to guarantee immediate exit liquidity upon fill",
        },
        {
            "name": "filter_std_time_max_days_to_resolve",
            "key": "filter_std_time_max_days_to_resolve",
            "display_name": "Max Resolution Horizon",
            "scope": "market_screener",
            "scope_label": "Scope 1: Market Screener",
            "subgroup": "standard",
            "subgroup_label": "Standard / Pre-Match & Long-Dated Markets",
            "value": f"{cfg.filter_std_time_max_days_to_resolve:.0f} days",
            "raw_value": cfg.filter_std_time_max_days_to_resolve,
            "default": "30 days",
            "env_var": "",
            "category": "Capital Horizon",
            "badge": "cadence",
            "trigger": f"Market scheduled resolution > {cfg.filter_std_time_max_days_to_resolve:.0f} days",
            "action": "Excludes long-dated capital lockup contracts; prioritizes near-term turnover",
        },
        {
            "name": "filter_common_spread_max_cents",
            "key": "filter_common_spread_max_cents",
            "display_name": "Picking Spread Cap (Derived)",
            "scope": "market_screener",
            "scope_label": "Scope 1: Market Screener",
            "subgroup": "standard",
            "subgroup_label": "Standard / Pre-Match & Long-Dated Markets",
            "value": f"{cfg.filter_common_spread_max_cents*100:.2f}¢ ({cfg.filter_common_spread_max_cents:.4f})",
            "raw_value": cfg.filter_common_spread_max_cents,
            "default": "2.05¢",
            "env_var": "",
            "category": "Spread Gate",
            "badge": "dynamic",
            "trigger": f"Candidate book spread > {cfg.filter_common_spread_max_cents*100:.2f}¢ (41/120 of bot_quote_book_max_spread_cents)",
            "action": "Derived picking cap prevents offering books wider than entry threshold",
        },
        {
            "name": "filter_std_queue_max_clear_min",
            "key": "filter_std_queue_max_clear_min",
            "display_name": "Maker Queue Bar",
            "scope": "market_screener",
            "scope_label": "Scope 1: Market Screener",
            "subgroup": "standard",
            "subgroup_label": "Standard / Pre-Match & Long-Dated Markets",
            "value": f"{scfg.filter_std_queue_max_clear_min:.1f}m",
            "raw_value": scfg.filter_std_queue_max_clear_min,
            "default": "15.0m",
            "env_var": "",
            "category": "Queue Realism",
            "badge": "cadence",
            "trigger": "Calculated queue clear time at target bid > 15m",
            "action": "Screens out congested queues that cannot drain within pairs action window",
        },
        {
            "name": "filter_std_gate_queue_max_on",
            "key": "filter_std_gate_queue_max_on",
            "display_name": "Queue Bar Enforcement",
            "scope": "market_screener",
            "scope_label": "Scope 1: Market Screener",
            "subgroup": "standard",
            "subgroup_label": "Standard / Pre-Match & Long-Dated Markets",
            "value": f"{scfg.filter_std_gate_queue_max_on} (Telemetry Only)",
            "raw_value": scfg.filter_std_gate_queue_max_on,
            "default": "False",
            "env_var": "",
            "category": "Telemetry",
            "badge": "safety",
            "trigger": "Queue bar enforcement switch",
            "action": "Records queue telemetry without rejecting markets during trial evaluation",
        },

        # =====================================================================
        # SCOPE 2: BOT ENGINE (SHARED EQUALLY BY LIVE TRADING & SHADOW PAPER MODE)
        # =====================================================================
        # Subgroup 1: Quoting Phase (Entry & Book Health)
        {
            "name": "bot_quote_bank_baseline_usd",
            "key": "bot_quote_bank_baseline_usd",
            "display_name": "Baseline Trading Bankroll",
            "scope": "bot_engine",
            "scope_label": "Scope 2: Bot Engine (Shared Live & Shadow)",
            "subgroup": "quoting_phase",
            "subgroup_label": "Quoting Phase: Sizing & Capital",
            "value": f"${cfg.bot_quote_bank_baseline_usd:.2f}",
            "raw_value": cfg.bot_quote_bank_baseline_usd,
            "default": "$100.00",
            "env_var": "BOT_QUOTE_BANK_BASELINE_USD",
            "category": "Capital Baseline",
            "badge": "dynamic",
            "trigger": "Account capital baseline for relative sizing calculations",
            "action": "Scales dynamic risk caps proportionally with live account value",
        },
        {
            "name": "bot_risk_exposure_max_naked_pct",
            "key": "bot_risk_exposure_max_naked_pct",
            "display_name": "Directional Risk Allocation",
            "scope": "bot_engine",
            "scope_label": "Scope 2: Bot Engine (Shared Live & Shadow)",
            "subgroup": "quoting_phase",
            "subgroup_label": "Quoting Phase: Sizing & Capital",
            "value": f"{cfg.bot_risk_exposure_max_naked_pct*100:.1f}% of {basis_label}",
            "raw_value": cfg.bot_risk_exposure_max_naked_pct,
            "default": "6.0%",
            "env_var": "",
            "category": "Inventory Risk",
            "badge": "dynamic",
            "trigger": "Unhedged exposure fraction of portfolio",
            "action": "Allocates 6% risk budget to cap maximum single-leg unhedged exposure",
        },
        {
            "name": "bot_risk_exposure_max_order_pct",
            "key": "bot_risk_exposure_max_order_pct",
            "display_name": "Single Order Capital Cap",
            "scope": "bot_engine",
            "scope_label": "Scope 2: Bot Engine (Shared Live & Shadow)",
            "subgroup": "quoting_phase",
            "subgroup_label": "Quoting Phase: Sizing & Capital",
            "value": f"{cfg.bot_risk_exposure_max_order_pct*100:.1f}% of {basis_label}",
            "raw_value": cfg.bot_risk_exposure_max_order_pct,
            "default": "25.0%",
            "env_var": "",
            "category": "Order Sizing",
            "badge": "dynamic",
            "trigger": "Single order calculation fraction of portfolio",
            "action": "Enforces 25% single-order ceiling to prevent accidental capital overcommitment",
        },
        {
            "name": "bot_risk_exposure_bankroll_ceiling_pct",
            "key": "bot_risk_exposure_bankroll_ceiling_pct",
            "display_name": "Global Fleet Deployment Ceiling",
            "scope": "bot_engine",
            "scope_label": "Scope 2: Bot Engine (Shared Live & Shadow)",
            "subgroup": "quoting_phase",
            "subgroup_label": "Quoting Phase: Sizing & Capital",
            "value": f"{cfg.bot_risk_exposure_bankroll_ceiling_pct*100:.1f}% of {basis_label}",
            "raw_value": cfg.bot_risk_exposure_bankroll_ceiling_pct,
            "default": "90.0%",
            "env_var": "",
            "category": "Global Fleet Cap",
            "badge": "dynamic",
            "trigger": "Total open notional across fleet fraction of portfolio",
            "action": "Halts all new quotes across all markets when 90% capital is committed",
        },
        {
            "name": "bot_risk_exposure_max_naked_usd",
            "key": "bot_risk_exposure_max_naked_usd",
            "display_name": "Single-Leg Unwind Cap",
            "scope": "bot_engine",
            "scope_label": "Scope 2: Bot Engine (Shared Live & Shadow)",
            "subgroup": "quoting_phase",
            "subgroup_label": "Quoting Phase: Sizing & Capital",
            "value": f"${naked_usd:.2f} ({cfg.bot_risk_exposure_max_naked_pct*100:.0f}% of {basis_label})",
            "raw_value": naked_usd,
            "default": "$6.00",
            "env_var": "",
            "category": "Inventory Risk",
            "badge": "dynamic",
            "trigger": f"One leg fills while opposing leg is unfilled, creating unhedged exposure > ${naked_usd:.2f}",
            "action": "Stops quoting new orders on that market; prepares emergency exit / merge",
        },
        {
            "name": "bot_risk_exposure_max_order_usd",
            "key": "bot_risk_exposure_max_order_usd",
            "display_name": "Max Capital Per Order",
            "scope": "bot_engine",
            "scope_label": "Scope 2: Bot Engine (Shared Live & Shadow)",
            "subgroup": "quoting_phase",
            "subgroup_label": "Quoting Phase: Sizing & Capital",
            "value": f"${order_usd:.2f} ({cfg.bot_risk_exposure_max_order_pct*100:.0f}% of {basis_label})",
            "raw_value": order_usd,
            "default": "$25.00",
            "env_var": "",
            "category": "Order Sizing",
            "badge": "dynamic",
            "trigger": f"Order sizing calculation generates a single order > ${order_usd:.2f}",
            "action": "Clamps size to prevent accidental capital overcommitment",
        },
        {
            "name": "bot_risk_exposure_max_total_usd",
            "key": "bot_risk_exposure_max_total_usd",
            "display_name": "Bankroll Deployment Ceiling",
            "scope": "bot_engine",
            "scope_label": "Scope 2: Bot Engine (Shared Live & Shadow)",
            "subgroup": "quoting_phase",
            "subgroup_label": "Quoting Phase: Sizing & Capital",
            "value": f"${total_usd:.2f} ({cfg.bot_risk_exposure_bankroll_ceiling_pct*100:.0f}% {basis_label} ceiling)",
            "raw_value": total_usd,
            "default": "$90.00",
            "env_var": "",
            "category": "Global Fleet Cap",
            "badge": "dynamic",
            "trigger": f"Sum of all open notional across fleet reaches ${total_usd:.2f}",
            "action": "Refuses all new quotes across all markets until existing orders settle or cancel",
        },
        {
            "name": "bot_quote_alloc_couple_pct",
            "key": "bot_quote_alloc_couple_pct",
            "display_name": "Pair Coupling Fraction",
            "scope": "bot_engine",
            "scope_label": "Scope 2: Bot Engine (Shared Live & Shadow)",
            "subgroup": "quoting_phase",
            "subgroup_label": "Quoting Phase: Sizing & Capital",
            "value": f"{cfg.bot_quote_alloc_couple_pct*100:.1f}%",
            "raw_value": cfg.bot_quote_alloc_couple_pct,
            "default": "1.0%",
            "env_var": "",
            "category": "Order Sizing",
            "badge": "dynamic",
            "trigger": "Coupled order allocation fraction",
            "action": "Limits coupled notional commitment per round-trip opportunity",
        },
        {
            "name": "bot_quote_alloc_couple_min_usd",
            "key": "bot_quote_alloc_couple_min_usd",
            "display_name": "Min Coupled Capital Floor",
            "scope": "bot_engine",
            "scope_label": "Scope 2: Bot Engine (Shared Live & Shadow)",
            "subgroup": "quoting_phase",
            "subgroup_label": "Quoting Phase: Sizing & Capital",
            "value": f"${cfg.bot_quote_alloc_couple_min_usd:.2f}",
            "raw_value": cfg.bot_quote_alloc_couple_min_usd,
            "default": "$10.00",
            "env_var": "",
            "category": "Order Sizing",
            "badge": "hard-limit",
            "trigger": f"Available coupled allocation < ${cfg.bot_quote_alloc_couple_min_usd:.2f}",
            "action": "Enforces minimum viable order notional for new position entries",
        },

        # Probability & Pricing Bands
        {
            "name": "bot_quote_price_band_min_cents",
            "key": "bot_quote_price_band_min_cents",
            "display_name": "Pricing Band Lower Bound",
            "scope": "bot_engine",
            "scope_label": "Scope 2: Bot Engine (Shared Live & Shadow)",
            "subgroup": "quoting_phase",
            "subgroup_label": "Quoting Phase: Probability & Pricing Bands",
            "value": f"${cfg.bot_quote_price_band_min_cents:.2f}",
            "raw_value": cfg.bot_quote_price_band_min_cents,
            "default": "$0.10",
            "env_var": "",
            "category": "Pricing Band",
            "badge": "hard-limit",
            "trigger": f"Candidate quote price < ${cfg.bot_quote_price_band_min_cents:.2f}",
            "action": "Refuses extreme longshot bids where adverse selection risk spikes",
        },
        {
            "name": "bot_quote_price_band_max_cents",
            "key": "bot_quote_price_band_max_cents",
            "display_name": "Pricing Band Upper Bound",
            "scope": "bot_engine",
            "scope_label": "Scope 2: Bot Engine (Shared Live & Shadow)",
            "subgroup": "quoting_phase",
            "subgroup_label": "Quoting Phase: Probability & Pricing Bands",
            "value": f"${cfg.bot_quote_price_band_max_cents:.2f}",
            "raw_value": cfg.bot_quote_price_band_max_cents,
            "default": "$0.90",
            "env_var": "",
            "category": "Pricing Band",
            "badge": "hard-limit",
            "trigger": f"Candidate quote price > ${cfg.bot_quote_price_band_max_cents:.2f}",
            "action": "Refuses extreme favourite bids where spread capture margin is negligible",
        },
        {
            "name": "bot_quote_gate_price_band_on",
            "key": "bot_quote_gate_price_band_on",
            "display_name": "Enforce Pricing Band",
            "scope": "bot_engine",
            "scope_label": "Scope 2: Bot Engine (Shared Live & Shadow)",
            "subgroup": "quoting_phase",
            "subgroup_label": "Quoting Phase: Probability & Pricing Bands",
            "value": f"{cfg.bot_quote_gate_price_band_on}",
            "raw_value": cfg.bot_quote_gate_price_band_on,
            "default": "True",
            "env_var": "",
            "category": "Pricing Band",
            "badge": "safety",
            "trigger": "Price band filter switch",
            "action": "Restricts all quotes strictly within $0.10 ≤ P ≤ $0.90 implied odds",
        },

        # Dynamic Offsets & Grid
        {
            "name": "bot_quote_gate_dynamic_offset_on",
            "key": "bot_quote_gate_dynamic_offset_on",
            "display_name": "Dynamic Offset Active",
            "scope": "bot_engine",
            "scope_label": "Scope 2: Bot Engine (Shared Live & Shadow)",
            "subgroup": "quoting_phase",
            "subgroup_label": "Quoting Phase: Dynamic Offsets & Grid",
            "value": f"{cfg.bot_quote_gate_dynamic_offset_on}",
            "raw_value": cfg.bot_quote_gate_dynamic_offset_on,
            "default": "True",
            "env_var": "BOT_QUOTE_GATE_DYNAMIC_OFFSET_ON",
            "category": "Quote Pricing",
            "badge": "safety",
            "trigger": "Spread-proportional dynamic offset switch",
            "action": "Scales resting bid distance dynamically with order book width",
        },
        {
            "name": "bot_quote_spread_offset_multiplier",
            "key": "bot_quote_spread_offset_multiplier",
            "display_name": "Half-Spread Offset Multiplier",
            "scope": "bot_engine",
            "scope_label": "Scope 2: Bot Engine (Shared Live & Shadow)",
            "subgroup": "quoting_phase",
            "subgroup_label": "Quoting Phase: Dynamic Offsets & Grid",
            "value": f"{cfg.bot_quote_spread_offset_multiplier:.2f} (50% of spread)",
            "raw_value": cfg.bot_quote_spread_offset_multiplier,
            "default": "0.50",
            "env_var": "BOT_QUOTE_SPREAD_OFFSET_MULTIPLIER",
            "category": "Quote Pricing",
            "badge": "dynamic",
            "trigger": "Inside spread offset calculation",
            "action": "Positions maker bids at 50% of the inside spread away from mid",
        },
        {
            "name": "bot_quote_spread_offset_min_cents",
            "key": "bot_quote_spread_offset_min_cents",
            "display_name": "Min Offset Floor",
            "scope": "bot_engine",
            "scope_label": "Scope 2: Bot Engine (Shared Live & Shadow)",
            "subgroup": "quoting_phase",
            "subgroup_label": "Quoting Phase: Dynamic Offsets & Grid",
            "value": f"{cfg.bot_quote_spread_offset_min_cents}¢",
            "raw_value": cfg.bot_quote_spread_offset_min_cents,
            "default": "1¢",
            "env_var": "BOT_QUOTE_SPREAD_OFFSET_MIN_CENTS",
            "category": "Quote Pricing",
            "badge": "hard-limit",
            "trigger": "Calculated offset < 1¢",
            "action": "Enforces minimum 1 tick distance below touch",
        },
        {
            "name": "bot_quote_spread_offset_max_cents",
            "key": "bot_quote_spread_offset_max_cents",
            "display_name": "Max Offset Ceiling",
            "scope": "bot_engine",
            "scope_label": "Scope 2: Bot Engine (Shared Live & Shadow)",
            "subgroup": "quoting_phase",
            "subgroup_label": "Quoting Phase: Dynamic Offsets & Grid",
            "value": f"{cfg.bot_quote_spread_offset_max_cents}¢",
            "raw_value": cfg.bot_quote_spread_offset_max_cents,
            "default": "20¢",
            "env_var": "BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS",
            "category": "Quote Pricing",
            "badge": "hard-limit",
            "trigger": "Calculated offset > 20¢",
            "action": "Caps offset at 20¢ to ensure quotes remain fillable",
        },
        {
            "name": "bot_quote_tick_size",
            "key": "bot_quote_tick_size",
            "display_name": "Price Grid / Tick Size",
            "scope": "bot_engine",
            "scope_label": "Scope 2: Bot Engine (Shared Live & Shadow)",
            "subgroup": "quoting_phase",
            "subgroup_label": "Quoting Phase: Dynamic Offsets & Grid",
            "value": f"${cfg.bot_quote_tick_size:.2f}",
            "raw_value": cfg.bot_quote_tick_size,
            "default": "$0.01",
            "env_var": "",
            "category": "Venue Protocol",
            "badge": "hard-limit",
            "trigger": "Venue price discretization",
            "action": "Enforces 1¢ price discretization across all resting quotes",
        },

        # Book Quality & Order Floor
        {
            "name": "bot_quote_size_min_shares",
            "key": "bot_quote_size_min_shares",
            "display_name": "Minimum Order Size",
            "scope": "bot_engine",
            "scope_label": "Scope 2: Bot Engine (Shared Live & Shadow)",
            "subgroup": "quoting_phase",
            "subgroup_label": "Quoting Phase: Book Quality & Order Floor",
            "value": f"{cfg.bot_quote_size_min_shares} shares",
            "raw_value": cfg.bot_quote_size_min_shares,
            "default": "5 shares",
            "env_var": "",
            "category": "Venue Protocol",
            "badge": "cadence",
            "trigger": "Calculated order size falls below Polymarket venue protocol floor",
            "action": f"Refuses single-sided quote or scales up to {cfg.bot_quote_size_min_shares} shares if budget permits",
        },
        {
            "name": "bot_quote_book_max_spread_cents",
            "key": "bot_quote_book_max_spread_cents",
            "display_name": "Max Book Spread Gate",
            "scope": "bot_engine",
            "scope_label": "Scope 2: Bot Engine (Shared Live & Shadow)",
            "subgroup": "quoting_phase",
            "subgroup_label": "Quoting Phase: Book Quality & Order Floor",
            "value": f"${cfg.bot_quote_book_max_spread_cents:.2f}",
            "raw_value": cfg.bot_quote_book_max_spread_cents,
            "default": "$0.06",
            "env_var": "",
            "category": "Book Health",
            "badge": "hard-limit",
            "trigger": f"Inside two-sided book spread > ${cfg.bot_quote_book_max_spread_cents:.2f}",
            "action": "Refuses to quote wide books where exit hedging is illiquid",
        },
        {
            "name": "bot_quote_book_max_spread_from_mid",
            "key": "bot_quote_book_max_spread_from_mid",
            "display_name": "Max Resting Spread From Mid",
            "scope": "bot_engine",
            "scope_label": "Scope 2: Bot Engine (Shared Live & Shadow)",
            "subgroup": "quoting_phase",
            "subgroup_label": "Quoting Phase: Book Quality & Order Floor",
            "value": f"${cfg.bot_quote_book_max_spread_from_mid:.2f}",
            "raw_value": cfg.bot_quote_book_max_spread_from_mid,
            "default": "$0.15",
            "env_var": "BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID",
            "category": "Book Health",
            "badge": "hard-limit",
            "trigger": f"Distance from mid to resting bid > ${cfg.bot_quote_book_max_spread_from_mid:.2f}",
            "action": "Resting-offset ceiling preventing quotes too deep to fill",
        },
        {
            "name": "bot_quote_require_two_sided_when_flat_on",
            "key": "bot_quote_require_two_sided_when_flat_on",
            "display_name": "Require Two-Sided Flat Entry",
            "scope": "bot_engine",
            "scope_label": "Scope 2: Bot Engine (Shared Live & Shadow)",
            "subgroup": "quoting_phase",
            "subgroup_label": "Quoting Phase: Book Quality & Order Floor",
            "value": f"{cfg.bot_quote_require_two_sided_when_flat_on}",
            "raw_value": cfg.bot_quote_require_two_sided_when_flat_on,
            "default": "True",
            "env_var": "",
            "category": "Book Health",
            "badge": "safety",
            "trigger": "One-sided market order book while inventory is flat",
            "action": "Refuses initial entry unless both UP and DOWN order books have active bids",
        },

        # ---------------------------------------------------------------------
        # Subgroup 2: Open Positions Management Phase (Inventory & Merge)
        # ---------------------------------------------------------------------
        # Spread Capture & Parity
        {
            "name": "bot_pos_price_max_pair_cost_usd",
            "key": "bot_pos_price_max_pair_cost_usd",
            "display_name": "Pair Cost Entry Ceiling",
            "scope": "bot_engine",
            "scope_label": "Scope 2: Bot Engine (Shared Live & Shadow)",
            "subgroup": "open_positions",
            "subgroup_label": "Open Positions: Spread Capture & Parity",
            "value": f"${cfg.bot_pos_price_max_pair_cost_usd:.2f}",
            "raw_value": cfg.bot_pos_price_max_pair_cost_usd,
            "default": "$0.99",
            "env_var": "BOT_POS_PRICE_MAX_PAIR_COST_USD",
            "category": "Pricing Safeguard",
            "badge": "hard-limit",
            "trigger": f"Combined UP + DOWN maker buy cost exceeds ${cfg.bot_pos_price_max_pair_cost_usd:.2f}",
            "action": "Refuses to quote pair to ensure guaranteed positive spread profit on merge ($1.00 - cost)",
        },
        {
            "name": "bot_pos_price_max_complete_usd",
            "key": "bot_pos_price_max_complete_usd",
            "display_name": "Completable Pair Cost Ceiling",
            "scope": "bot_engine",
            "scope_label": "Scope 2: Bot Engine (Shared Live & Shadow)",
            "subgroup": "open_positions",
            "subgroup_label": "Open Positions: Spread Capture & Parity",
            "value": f"${cfg.bot_pos_price_max_complete_usd:.2f}",
            "raw_value": cfg.bot_pos_price_max_complete_usd,
            "default": "$1.00",
            "env_var": "BOT_POS_PRICE_MAX_COMPLETE_USD",
            "category": "Pricing Safeguard",
            "badge": "hard-limit",
            "trigger": f"Resting bid + opposite best ask >= ${cfg.bot_pos_price_max_complete_usd:.2f}",
            "action": "Guarantees any single fill can immediately be hedged at market ask without loss",
        },
        {
            "name": "bot_pos_gate_complete_cost_on",
            "key": "bot_pos_gate_complete_cost_on",
            "display_name": "Enforce Completable Cost",
            "scope": "bot_engine",
            "scope_label": "Scope 2: Bot Engine (Shared Live & Shadow)",
            "subgroup": "open_positions",
            "subgroup_label": "Open Positions: Spread Capture & Parity",
            "value": f"{cfg.bot_pos_gate_complete_cost_on}",
            "raw_value": cfg.bot_pos_gate_complete_cost_on,
            "default": "True",
            "env_var": "",
            "category": "Pricing Safeguard",
            "badge": "safety",
            "trigger": "Completable cost gate switch",
            "action": "Strictly gates maker bids on instantaneous taker hedge viability",
        },

        # Inventory Skew
        {
            "name": "bot_pos_gate_strict_pairs_on",
            "key": "bot_pos_gate_strict_pairs_on",
            "display_name": "Strict Paired Inventory",
            "scope": "bot_engine",
            "scope_label": "Scope 2: Bot Engine (Shared Live & Shadow)",
            "subgroup": "open_positions",
            "subgroup_label": "Open Positions: Inventory Skew",
            "value": f"{cfg.bot_pos_gate_strict_pairs_on}",
            "raw_value": cfg.bot_pos_gate_strict_pairs_on,
            "default": "True",
            "env_var": "",
            "category": "Inventory Management",
            "badge": "safety",
            "trigger": "Inventory imbalance detected between UP and DOWN legs",
            "action": "Blocks quoting heavy leg; sizes light leg strictly to balance inventory",
        },
        {
            "name": "bot_pos_skew_target_balance_ratio",
            "key": "bot_pos_skew_target_balance_ratio",
            "display_name": "Target Inventory Balance",
            "scope": "bot_engine",
            "scope_label": "Scope 2: Bot Engine (Shared Live & Shadow)",
            "subgroup": "open_positions",
            "subgroup_label": "Open Positions: Inventory Skew",
            "value": f"{cfg.bot_pos_skew_target_balance_ratio*100:.0f}%",
            "raw_value": cfg.bot_pos_skew_target_balance_ratio,
            "default": "92%",
            "env_var": "",
            "category": "Inventory Management",
            "badge": "dynamic",
            "trigger": f"Inventory ratio (min_leg / max_leg) < {cfg.bot_pos_skew_target_balance_ratio*100:.0f}%",
            "action": "Activates skew spring to rebalance positions toward 1.0 parity",
        },
        {
            "name": "bot_pos_skew_max_offset_cents",
            "key": "bot_pos_skew_max_offset_cents",
            "display_name": "Max Skew Offset",
            "scope": "bot_engine",
            "scope_label": "Scope 2: Bot Engine (Shared Live & Shadow)",
            "subgroup": "open_positions",
            "subgroup_label": "Open Positions: Inventory Skew",
            "value": f"${cfg.bot_pos_skew_max_offset_cents:.3f}",
            "raw_value": cfg.bot_pos_skew_max_offset_cents,
            "default": "$0.015",
            "env_var": "",
            "category": "Inventory Management",
            "badge": "dynamic",
            "trigger": "Inventory skew adjustment reaches 1.5¢ limit",
            "action": "Caps aggressive pricing on light side to prevent adverse fills",
        },
        {
            "name": "bot_pos_skew_min_reward_offset_cents",
            "key": "bot_pos_skew_min_reward_offset_cents",
            "display_name": "Min Quote Offset Edge",
            "scope": "bot_engine",
            "scope_label": "Scope 2: Bot Engine (Shared Live & Shadow)",
            "subgroup": "open_positions",
            "subgroup_label": "Open Positions: Inventory Skew",
            "value": f"${cfg.bot_pos_skew_min_reward_offset_cents:.3f}",
            "raw_value": cfg.bot_pos_skew_min_reward_offset_cents,
            "default": "$0.005",
            "env_var": "",
            "category": "Inventory Management",
            "badge": "hard-limit",
            "trigger": "Calculated quote offset edge < 0.5¢",
            "action": "Enforces minimum 0.5¢ resting margin from mid",
        },

        # Capital Efficiency
        {
            "name": "bot_pos_merge_max_loss_per_share_usd",
            "key": "bot_pos_merge_max_loss_per_share_usd",
            "display_name": "Merge Max Loss Per Share",
            "scope": "bot_engine",
            "scope_label": "Scope 2: Bot Engine (Shared Live & Shadow)",
            "subgroup": "open_positions",
            "subgroup_label": "Open Positions: Capital Efficiency",
            "value": f"${cfg.bot_pos_merge_max_loss_per_share_usd:.2f}",
            "raw_value": cfg.bot_pos_merge_max_loss_per_share_usd,
            "default": "$0.01",
            "env_var": "",
            "category": "Merge Parity",
            "badge": "hard-limit",
            "trigger": "Merge assembly loss per share exceeds 1¢",
            "action": "Prevents merging at a loss; holds pair until parity conditions met",
        },
        {
            "name": "bot_pos_merge_velocity_hold_days",
            "key": "bot_pos_merge_velocity_hold_days",
            "display_name": "Max Inventory Hold Days",
            "scope": "bot_engine",
            "scope_label": "Scope 2: Bot Engine (Shared Live & Shadow)",
            "subgroup": "open_positions",
            "subgroup_label": "Open Positions: Capital Efficiency",
            "value": f"{cfg.bot_pos_merge_velocity_hold_days:.0f} days",
            "raw_value": cfg.bot_pos_merge_velocity_hold_days,
            "default": "30 days",
            "env_var": "",
            "category": "Merge Parity",
            "badge": "cadence",
            "trigger": "Open pair unmerged duration exceeds 30 days",
            "action": "Triggers capital recycling / redemption sweep on aged inventory",
        },

        # ---------------------------------------------------------------------
        # Subgroup 3: Protection, Stops, Requotes & Order Lifecycle Phase
        # ---------------------------------------------------------------------
        # Order Management & Requoting
        {
            "name": "bot_order_requote_interval_sec",
            "key": "bot_order_requote_interval_sec",
            "display_name": "Requote Min Interval",
            "scope": "bot_engine",
            "scope_label": "Scope 2: Bot Engine (Shared Live & Shadow)",
            "subgroup": "protection_lifecycle",
            "subgroup_label": "Protection & Lifecycle: Order Management & Requoting",
            "value": f"{cfg.bot_order_requote_interval_sec:.1f}s",
            "raw_value": cfg.bot_order_requote_interval_sec,
            "default": "2.0s",
            "env_var": "",
            "category": "Requote Cadence",
            "badge": "cadence",
            "trigger": "Time elapsed since last order submission < 2.0s",
            "action": "Throttles amendments to prevent venue rate limit exhaustion and preserve queue",
        },
        {
            "name": "bot_order_requote_dead_band_cents",
            "key": "bot_order_requote_dead_band_cents",
            "display_name": "Requote Dead Band",
            "scope": "bot_engine",
            "scope_label": "Scope 2: Bot Engine (Shared Live & Shadow)",
            "subgroup": "protection_lifecycle",
            "subgroup_label": "Protection & Lifecycle: Order Management & Requoting",
            "value": f"${cfg.bot_order_requote_dead_band_cents:.2f}",
            "raw_value": cfg.bot_order_requote_dead_band_cents,
            "default": "$0.03",
            "env_var": "BOT_ORDER_REQUOTE_DEAD_BAND_CENTS",
            "category": "Requote Policy",
            "badge": "hard-limit",
            "trigger": f"Target price shifts by less than ${cfg.bot_order_requote_dead_band_cents:.2f} from resting order",
            "action": "Suppresses cancel-replace churn; retains order book queue priority",
        },
        {
            "name": "bot_order_poll_interval_sec",
            "key": "bot_order_poll_interval_sec",
            "display_name": "Trader Loop Poll Cadence",
            "scope": "bot_engine",
            "scope_label": "Scope 2: Bot Engine (Shared Live & Shadow)",
            "subgroup": "protection_lifecycle",
            "subgroup_label": "Protection & Lifecycle: Order Management & Requoting",
            "value": f"{cfg.bot_order_poll_interval_sec:.1f}s",
            "raw_value": cfg.bot_order_poll_interval_sec,
            "default": "1.0s",
            "env_var": "",
            "category": "Execution Cadence",
            "badge": "cadence",
            "trigger": "Fleet execution loop tick",
            "action": "Rotates active markets, polls order status, and reconciles float every 1.0s",
        },
        {
            "name": "sweep_interval",
            "key": "sweep_interval",
            "display_name": "Account Sync Cadence",
            "scope": "bot_engine",
            "scope_label": "Scope 2: Bot Engine (Shared Live & Shadow)",
            "subgroup": "protection_lifecycle",
            "subgroup_label": "Protection & Lifecycle: Order Management & Requoting",
            "value": f"{sweep:.0f}s" if sweep is not None else "every tick",
            "raw_value": sweep,
            "default": "every tick",
            "env_var": "LIVE_SWEEP_INTERVAL",
            "category": "Reconciliation",
            "badge": "cadence",
            "trigger": "{} elapsed since last wallet balance query".format(f"{sweep:.0f}s" if sweep is not None else "every poll cycle"),
            "action": "Fetches fresh on-chain USDC balance and updates float marks",
        },

        # Single-Buy Rescue & Aging
        {
            "name": "bot_risk_gate_pairs_rule_on",
            "key": "bot_risk_gate_pairs_rule_on",
            "display_name": "Pairs-Only Rule Active",
            "scope": "bot_engine",
            "scope_label": "Scope 2: Bot Engine (Shared Live & Shadow)",
            "subgroup": "protection_lifecycle",
            "subgroup_label": "Protection & Lifecycle: Single-Buy Rescue & Aging",
            "value": f"{cfg.bot_risk_gate_pairs_rule_on}",
            "raw_value": cfg.bot_risk_gate_pairs_rule_on,
            "default": "True",
            "env_var": "BOT_RISK_GATE_PAIRS_RULE_ON",
            "category": "Single-Buy Safety",
            "badge": "safety",
            "trigger": "Single-buy (one leg filled) event occurs",
            "action": "Dates a 15-minute action window off the last fill; an older naked leg is left alone, not force-exited",
        },
        {
            "name": "bot_risk_pairs_exit_window_sec",
            "key": "bot_risk_pairs_exit_window_sec",
            "display_name": "Pairs Action Window",
            "scope": "bot_engine",
            "scope_label": "Scope 2: Bot Engine (Shared Live & Shadow)",
            "subgroup": "protection_lifecycle",
            "subgroup_label": "Protection & Lifecycle: Single-Buy Rescue & Aging",
            "value": f"{cfg.bot_risk_pairs_exit_window_sec:.0f}s",
            "raw_value": cfg.bot_risk_pairs_exit_window_sec,
            "default": "900s",
            "env_var": "",
            "category": "Single-Buy Safety",
            "badge": "cadence",
            "trigger": "15 minutes elapsed since last fill timestamp without opposite leg fill",
            "action": "Halts active quoting on market; passes control to single-buy rescue handler",
        },
        {
            "name": "bot_risk_gate_aged_out_rescue_on",
            "key": "bot_risk_gate_aged_out_rescue_on",
            "display_name": "Aged-Out Rescue Active",
            "scope": "bot_engine",
            "scope_label": "Scope 2: Bot Engine (Shared Live & Shadow)",
            "subgroup": "protection_lifecycle",
            "subgroup_label": "Protection & Lifecycle: Single-Buy Rescue & Aging",
            "value": f"{cfg.bot_risk_gate_aged_out_rescue_on}",
            "raw_value": cfg.bot_risk_gate_aged_out_rescue_on,
            "default": "True",
            "env_var": "",
            "category": "Single-Buy Safety",
            "badge": "safety",
            "trigger": "Unhedged inventory exceeds aging window",
            "action": "Activates automated unhedged inventory liquidator / rescue routine",
        },
        {
            "name": "bot_risk_aged_out_rescue_lead_sec",
            "key": "bot_risk_aged_out_rescue_lead_sec",
            "display_name": "Resolution Rescue Lead Time",
            "scope": "bot_engine",
            "scope_label": "Scope 2: Bot Engine (Shared Live & Shadow)",
            "subgroup": "protection_lifecycle",
            "subgroup_label": "Protection & Lifecycle: Single-Buy Rescue & Aging",
            "value": f"{cfg.bot_risk_aged_out_rescue_lead_sec:.0f}s",
            "raw_value": cfg.bot_risk_aged_out_rescue_lead_sec,
            "default": "900s",
            "env_var": "BOT_RISK_AGED_OUT_RESCUE_LEAD_SEC",
            "category": "Single-Buy Safety",
            "badge": "cadence",
            "trigger": "Lead time before market resolution for unhedged rescue",
            "action": "Forces inventory closure prior to market expiration",
        },

        # Stop-Loss & Emergency Defense
        {
            "name": "bot_risk_stop_mode",
            "key": "bot_risk_stop_mode",
            "display_name": "Stop-Loss Defensive Mode",
            "scope": "bot_engine",
            "scope_label": "Scope 2: Bot Engine (Shared Live & Shadow)",
            "subgroup": "protection_lifecycle",
            "subgroup_label": "Protection & Lifecycle: Stop-Loss & Emergency Defense",
            "value": f"{cfg.bot_risk_stop_mode}",
            "raw_value": cfg.bot_risk_stop_mode,
            "default": "NONE",
            "env_var": "BOT_RISK_STOP_MODE",
            "category": "Stop-Loss System",
            "badge": "safety",
            "trigger": f"Configured stop loss trigger mode ('{cfg.bot_risk_stop_mode}')",
            "action": "Defines defensive liquidation rule (NONE, ABSOLUTE_DROP, or PERCENTAGE_DROP)",
        },
        {
            "name": "bot_risk_stop_adverse_drop_cents",
            "key": "bot_risk_stop_adverse_drop_cents",
            "display_name": "Adverse Drop Cents Stop",
            "scope": "bot_engine",
            "scope_label": "Scope 2: Bot Engine (Shared Live & Shadow)",
            "subgroup": "protection_lifecycle",
            "subgroup_label": "Protection & Lifecycle: Stop-Loss & Emergency Defense",
            "value": f"${cfg.bot_risk_stop_adverse_drop_cents:.2f}",
            "raw_value": cfg.bot_risk_stop_adverse_drop_cents,
            "default": "$0.05",
            "env_var": "BOT_RISK_STOP_ADVERSE_DROP_CENTS",
            "category": "Stop-Loss System",
            "badge": "hard-limit",
            "trigger": f"Mid price drops ${cfg.bot_risk_stop_adverse_drop_cents:.2f} below position acquisition cost",
            "action": "Triggers defensive market exit to truncate tail risk on naked leg",
        },
        {
            "name": "bot_risk_stop_max_drawdown_pct",
            "key": "bot_risk_stop_max_drawdown_pct",
            "display_name": "Position Drawdown Stop",
            "scope": "bot_engine",
            "scope_label": "Scope 2: Bot Engine (Shared Live & Shadow)",
            "subgroup": "protection_lifecycle",
            "subgroup_label": "Protection & Lifecycle: Stop-Loss & Emergency Defense",
            "value": f"{cfg.bot_risk_stop_max_drawdown_pct:.1f}%",
            "raw_value": cfg.bot_risk_stop_max_drawdown_pct,
            "default": "10.0%",
            "env_var": "BOT_RISK_STOP_MAX_DRAWDOWN_PCT",
            "category": "Stop-Loss System",
            "badge": "dynamic",
            "trigger": f"Unrealized loss on market position exceeds {cfg.bot_risk_stop_max_drawdown_pct:.1f}%",
            "action": "Halts quoting and initiates orderly position liquidation",
        },
        {
            "name": "bot_risk_circuit_max_drawdown_pct",
            "key": "bot_risk_circuit_max_drawdown_pct",
            "display_name": "Emergency Circuit Breaker",
            "scope": "bot_engine",
            "scope_label": "Scope 2: Bot Engine (Shared Live & Shadow)",
            "subgroup": "protection_lifecycle",
            "subgroup_label": "Protection & Lifecycle: Stop-Loss & Emergency Defense",
            "value": f"{cfg.bot_risk_circuit_max_drawdown_pct:.1f}%",
            "raw_value": cfg.bot_risk_circuit_max_drawdown_pct,
            "default": "75.0%",
            "env_var": "BOT_RISK_CIRCUIT_MAX_DRAWDOWN_PCT",
            "category": "Circuit Breaker",
            "badge": "hard-limit",
            "trigger": f"Catastrophic market price collapse exceeding {cfg.bot_risk_circuit_max_drawdown_pct:.1f}%",
            "action": "Immediate emergency circuit breaker; liquidates open risk unconditionally",
        },
        {
            "name": "bot_risk_hedge_emergency_pct",
            "key": "bot_risk_hedge_emergency_pct",
            "display_name": "Emergency Taker Cross Trigger",
            "scope": "bot_engine",
            "scope_label": "Scope 2: Bot Engine (Shared Live & Shadow)",
            "subgroup": "protection_lifecycle",
            "subgroup_label": "Protection & Lifecycle: Stop-Loss & Emergency Defense",
            "value": f"{cfg.bot_risk_hedge_emergency_pct*100:.0f}% of bot_risk_exposure_max_naked_usd",
            "raw_value": cfg.bot_risk_hedge_emergency_pct,
            "default": "80%",
            "env_var": "",
            "category": "Emergency Defense",
            "badge": "dynamic",
            "trigger": f"Exposure reaches {cfg.bot_risk_hedge_emergency_pct*100:.0f}% of bot_risk_exposure_max_naked_usd",
            "action": "Permits light side to cross spread and take hedge at market before cap freezes",
        },
        {
            "name": "bot_risk_gate_emergency_hedge_on",
            "key": "bot_risk_gate_emergency_hedge_on",
            "display_name": "Emergency Hedge Active",
            "scope": "bot_engine",
            "scope_label": "Scope 2: Bot Engine (Shared Live & Shadow)",
            "subgroup": "protection_lifecycle",
            "subgroup_label": "Protection & Lifecycle: Stop-Loss & Emergency Defense",
            "value": f"{cfg.bot_risk_gate_emergency_hedge_on}",
            "raw_value": cfg.bot_risk_gate_emergency_hedge_on,
            "default": "True",
            "env_var": "",
            "category": "Emergency Defense",
            "badge": "safety",
            "trigger": "Emergency taker hedge authorization switch",
            "action": "Authorizes spread-crossing defensive hedge orders under severe deficit",
        },
    ]
    if scoring_error is not None:
        params.insert(0, {
            "name": "scoring_config_load_failed",
            "key": "scoring_config_load_failed",
            "display_name": "⚠ Scoring Config Failed To Load",
            "scope": "market_screener",
            "scope_label": "Scope 1: Market Screener",
            "subgroup": "active_live",
            "subgroup_label": "Active / In-Play Markets (Live Sports & Esports)",
            "value": f"⚠ {scoring_error}",
            "raw_value": None,
            "default": "",
            "env_var": "",
            "category": "Config Load Failure",
            "badge": "safety",
            "trigger": "scoring.config.load() raised",
            "action": "Scope 1 shows a placeholder, not engine values. Fix the named variable and restart the dashboard.",
        })
    return JSONResponse({"parameters": params})


@app.get("/api/active-markets")
def get_active_markets():
    """Return graduated and currently quoted markets with order depth and PnL.

    Splits /api/state's market data into active-only (not resolved) for
    the Market Inspection table's ACTIVE MARKETS view.
    """
    from core_brain.registry_state import summarize_state
    state = summarize_state(resolve_db_path(_ACTIVE_DB_OVERRIDE))
    # Filter to markets with active orders or fills. Pairs carry
    # `hedge_state` (RESTING/NAKED/BALANCED/...), never `status`: filtering
    # on the latter matched nothing and the tab always read 0 beside open
    # orders.
    active = [p for p in state.get("pairs", []) if p.get("hedge_state") in ("RESTING", "NAKED", "BALANCED")]
    return JSONResponse({"markets": active})


@app.get("/api/closed-markets")
def get_closed_markets():
    """Return historical closed/settled markets and booked PnL.

    Reads from the KPI report's by_market dict, filtering to markets that
    have closes (realized PnL booked).
    """
    from core_brain.kpi import report as generate_kpi_report
    db_path = resolve_db_path(_ACTIVE_DB_OVERRIDE)
    try:
        data = generate_kpi_report(db_path=db_path)
        closed = [
            {**m, "realized_pnl": m.get("realized_pnl", 0.0)}
            for m in data.get("by_market", {}).values()
            if m.get("settlements")
        ]
        return JSONResponse({"markets": closed})
    except Exception as e:
        return _read_only_error_response(e)


# PAGE_HTML: backward-compat shim for tests that reference the constant.
# The actual HTML now lives in dash/static/index.html. Tests that assert on
# specific HTML strings should read from the static file directly.
_PAGE_HTML_FILE = Path(__file__).resolve().parent / "static" / "index.html"

def _load_page_html() -> str:
    """Read the static HTML file, or return empty string if missing."""
    try:
        return _PAGE_HTML_FILE.read_text(encoding="utf-8")
    except OSError:
        return ""

# Kept as a module-level constant for backward compat with tests that import
# PAGE_HTML. Reads the file once at import time; index() reads fresh per
# request so file edits are picked up without restart.
PAGE_HTML = _load_page_html()

_STATIC_ASSET_RE = re.compile(r"/static/([A-Za-z0-9_.\-]+)(?:\?v=[A-Za-z0-9]+)?")


def _static_fingerprint(name: str, static_dir: Path) -> str | None:
    """Short content hash for a static asset, or None when unreadable."""
    try:
        digest = hashlib.sha1((static_dir / name).read_bytes()).hexdigest()
    except OSError:
        return None
    return digest[:12]


def _fingerprint_static_refs(html: str, static_dir: Path = _STATIC_DIR) -> str:
    """Rewrite /static/ refs to content-hash ?v= so browsers refetch on change.

    A manual ?v= number is replaced; an unversioned ref gains one. The
    version tracks the bytes, so no author ever has to remember a bump.
    Refs to missing files stay untouched.
    """
    def _replace(match: re.Match[str]) -> str:
        fingerprint = _static_fingerprint(match.group(1), static_dir)
        if fingerprint is None:
            return match.group(0)
        return f"/static/{match.group(1)}?v={fingerprint}"
    return _STATIC_ASSET_RE.sub(_replace, html)


@app.get("/prototype", response_class=HTMLResponse)
def prototype_page():
    """The sidebar-pages layout under review (#95 frame, #140 content).

    It serves the same document as `/`, control token and all. `prototype.js`
    reads the path and, only here, moves the live panels into five sidebar
    pages -- so the layout is judged against real data with no second copy of
    the markup to keep in sync.

    It stays on its own path rather than replacing `/`: this dashboard is the
    control surface for a loop that places real orders, and the operator keeps
    the surface they know until the layout is signed off. Swapping it in is a
    one-line change to `LAYOUT_PATHS` in `prototype.js`.
    """
    html = _load_page_html()
    if not html:
        raise HTTPException(status_code=404, detail="dashboard page not built")
    return HTMLResponse(
        _fingerprint_static_refs(
            html.replace(CONTROL_TOKEN_PLACEHOLDER, CONTROL_TOKEN)),
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )



@app.get("/explainer", response_class=HTMLResponse)
def explainer_page() -> HTMLResponse:
    """The Strategy Explainer page on its canonical clean path.

    Serves `static/strategy_explainer.html`. Read-only documentation of strategy,
    timing, parameters, and risk mechanics. `/static/strategy_explainer.html`
    reaches the same document if the static mount is preferred.
    """
    path = _STATIC_DIR / "strategy_explainer.html"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="strategy explainer page not found")
    return HTMLResponse(
        path.read_text(encoding="utf-8"),
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_alias():
    """Canonical dashboard path; same single-page app as `/`."""
    return index()


def _open_dash_retired_page() -> str:
    """Per-arm dashboards were retired: arms are heartbeat-only, read via :8799."""
    return (
        "<h1>Per-arm dashboards are retired</h1>"
        "<p>Arms run heartbeat-only now; there is nothing listening on per-arm ports.</p>"
        '<p><a href="/">Back to the dashboard</a> — pick an arm’s store in the '
        "run switcher to read what it is writing.</p>"
    )


@app.get("/cockpit/api/stats")
def cockpit_stats(tourney: str | None = None):
    """Tournament stats JSON, built in-process by the cockpit’s own builder."""
    cockpit_mod = _load_cockpit()
    key = tourney if tourney in cockpit_mod.SERVED_TOURNAMENT_KEYS else cockpit_mod.CURRENT_SETUP_KEY
    return JSONResponse(cockpit_mod.get_cached_stats(key))


@app.get("/cockpit", response_class=HTMLResponse)
@app.get("/cockpit/{subpath:path}")
def cockpit_page(subpath: str = ""):
    """Tournament cockpit as a dashboard page: the same document, in-process data.

    No port, no process: the HTML is generated here by the cockpit module’s
    own builder, with its API calls and self-links rewritten to stay on :8799.
    """
    if subpath == "open-dash":
        return HTMLResponse(_open_dash_retired_page())
    cockpit_mod = _load_cockpit()
    key = cockpit_mod.tourney_key_for_path("/" + subpath)
    if key is None:
        raise HTTPException(status_code=404, detail="Unknown cockpit page")
    html_text = cockpit_mod.generate_cockpit_html(cockpit_mod.get_cached_stats(key))
    html_text = html_text.replace("fetch('/api/stats", "fetch('/cockpit/api/stats")
    html_text = html_text.replace("http://127.0.0.1:8800", "/cockpit/")
    # Standalone-root links the template emits for :8800 would 404 on :8799.
    html_text = html_text.replace('href="/open-dash', 'href="/cockpit/open-dash')
    html_text = html_text.replace('href="/799"', 'href="/cockpit/799"')
    return HTMLResponse(_with_shared_rail(html_text, "cockpit"))


@app.get("/hub/api/state")
def hub_state():
    """Tournament hub state JSON, built in-process."""
    from scripts import tournament_hub as hub_mod
    return JSONResponse(hub_mod.build_hub_state())


@app.get("/hub/api/health")
def hub_health():
    return JSONResponse({"ok": True, "service": "hub"})


@app.get("/hub", response_class=HTMLResponse)
def hub_page():
    """Tournament hub as a dashboard page: the same document, in-process data."""
    from scripts import tournament_hub as hub_mod
    html_text = hub_mod.PAGE_HTML
    html_text = html_text.replace("http://127.0.0.1:8800", "/cockpit/")
    html_text = html_text.replace(" (Port 8800)", "")
    return HTMLResponse(_with_shared_rail(html_text, "hub"))


@app.get("/", response_class=HTMLResponse)
def index():
    """Serve the live operations dashboard.

    The HTML lives in dash/static/index.html. The per-process control token
    is injected at serve time via string replacement — the same pattern as
    the old inline PAGE_HTML, just reading from a file instead of a constant.
    CSS and JS are served cacheable via FastAPI StaticFiles at /static/.
    """
    html = _load_page_html()
    return HTMLResponse(
        _fingerprint_static_refs(
            html.replace(CONTROL_TOKEN_PLACEHOLDER, CONTROL_TOKEN)),
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )



def main():
    import uvicorn

    parser = argparse.ArgumentParser(description="Spread Hunter Execution Monitor")
    parser.add_argument("--port", type=int, default=None,
                        help="Port to bind (default: $PORT, else 8799)")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host interface (default: 127.0.0.1)")
    parser.add_argument("--db", type=str, default=None, help="Path to orders.db SQLite file")
    parser.add_argument("--reload", action="store_true",
                        help="Auto-reload on code changes (dev convenience; reload is off in production)")
    args = parser.parse_args()

    if args.db:
        set_db_override(args.db)
        os.environ["LIVE_DB_PATH"] = str(Path(args.db))

    port = resolve_port(args.port)

    global _ACTIVE_PORT
    _ACTIVE_PORT = port
    os.environ["PORT"] = str(port)

    print(f"Starting Live Execution Dashboard on http://{args.host}:{port}")
    # Best-effort initial snapshot so the dashboard opens with fresh live balance
    try:
        _capture_starting_capital()
    except Exception:
        pass
    # Server-owned live-marks feed next to it: read-only venue WS (or sim),
    # one thread for all browsers. Best-effort; the stream degrades to ring
    # telemetry alone when the feed is off or fails to start.
    try:
        start_live_marks()
    except Exception:
        logger.debug("live-marks feed did not start", exc_info=True)
    app_target = "dashboard.server:app" if args.reload else app
    uvicorn.run(app_target, host=args.host, port=port,
                reload=args.reload,
                # Watch only the dashboard's own code. Without this, a reload
                # watcher rooted at the project restarts the monitor when
                # core_brain or scripts change -- the observer must never be
                # restarted by the thing it is observing.
                reload_dirs=["dashboard"] if args.reload else None,
                # The cycle-telemetry SSE stream never ends on its own, so a
                # graceful shutdown that waits for open connections waits
                # forever: a reload or a restart leaves the port bound by a
                # server that prints "Waiting for connections to close" and
                # never comes back. Cut the stream off instead.
                timeout_graceful_shutdown=SHUTDOWN_GRACE_SEC)


if __name__ == "__main__":
    main()
