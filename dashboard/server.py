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
import json
import math
import os
import logging
import secrets
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Generator, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

logger = logging.getLogger(__name__)

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
    legacy_runtime_file,
    resolve_runtime_file,
    runtime_file,
    service_entry,
)

REPO_ROOT = LIVE_ROOT
DEFAULT_PORT = 8799


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
SSE_REPLAY_LINES = 50
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

    Mirrors core_brain.order_manager._find_env_file: the nearest .env walking up
    from core_brain/, stopping at the AGENTS.md boundary. The dashboard never
    loads the whole file -- only LIVE_SWEEP_INTERVAL is read or written -- so
    the signing key and L2 credentials never enter this process.
    """
    curr = LIVE_ROOT / "core_brain"
    for _ in range(4):
        if (curr / ".env").is_file():
            return curr / ".env"
        if (curr / "AGENTS.md").is_file():
            break
        if curr.parent == curr:
            break
        curr = curr.parent
    return None


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
# A run accumulates them without bound -- a requote cycle cancels both legs
# every few seconds -- and after a day they were the bulk of a 14.78 MB
# payload the page asked for every two seconds. Gzipping a body that size
# fails outright ("Response content longer than Content-Length"), the browser
# gets truncated JSON, and the dashboard renders empty.
#
# The page only ever shows them inside one expanded market, behind a "Show N
# cancelled orders" toggle, so a recent slice per market is all it can
# display. Live orders and live pairs are never trimmed.
CANCELLED_ORDERS_PER_MARKET = 5

_CANCELLED_STATUSES = frozenset({"cancelled", "canceled"})


def _order_recency(order: dict) -> int:
    """Newest-first sort key: when the venue last spoke about this order."""
    for field in ("last_polled_ts", "posted_ts"):
        value = order.get(field)
        if isinstance(value, (int, float)):
            return int(value)
    return 0


def _is_cancelled(order: dict) -> bool:
    return str(order.get("status") or "").lower() in _CANCELLED_STATUSES


def _newest_per_market(dead: dict[str, list[dict]], recency) -> list[dict]:
    """The newest `CANCELLED_ORDERS_PER_MARKET` of each market's dead entries."""
    kept: list[dict] = []
    for entries in dead.values():
        entries.sort(key=recency, reverse=True)
        kept.extend(entries[:CANCELLED_ORDERS_PER_MARKET])
    return kept


def _trim_cancelled_orders(state: dict) -> dict:
    """Drop all but the newest `CANCELLED_ORDERS_PER_MARKET` per market.

    Both lists are trimmed, because `pairs` embeds each pair's orders in full
    and so carries a second, larger copy of the same dead history -- 10.6 MB of
    one 14.8 MB payload here. A pair is dead only when every one of its orders
    is; a pair holding anything live is kept whole.

    Returns a shallow copy: the cached snapshot this reads from is shared, so
    trimming in place would mutate what other readers get.
    """
    trimmed = dict(state)

    orders = state.get("orders")
    if isinstance(orders, list):
        live: list[dict] = []
        dead: dict[str, list[dict]] = {}
        for order in orders:
            if _is_cancelled(order):
                dead.setdefault(str(order.get("condition_id") or ""), []).append(order)
            else:
                live.append(order)
        trimmed["orders"] = live + _newest_per_market(dead, _order_recency)
        # What the page would have been sent, so nothing has to infer the cap
        # from a short list.
        trimmed["cancelled_orders_total"] = sum(len(v) for v in dead.values())

    pairs = state.get("pairs")
    if isinstance(pairs, list):
        live_pairs: list[dict] = []
        dead_pairs: dict[str, list[dict]] = {}
        for pair in pairs:
            pair_orders = pair.get("orders") or []
            if pair_orders and all(_is_cancelled(o) for o in pair_orders):
                dead_pairs.setdefault(str(pair.get("condition_id") or ""), []).append(pair)
            else:
                live_pairs.append(pair)

        def pair_recency(pair: dict) -> int:
            return max((_order_recency(o) for o in (pair.get("orders") or [])), default=0)

        kept_dead = _newest_per_market(dead_pairs, pair_recency)
        # Dead pairs are only ever read by orderGroupMarket for metadata fallback,
        # never for order manipulation. Stripping raw nested order and token arrays
        # eliminates 10MB+ of redundant JSON on large run stores.
        lean_dead = []
        for p in kept_dead:
            lean_dead.append({
                "pair_id": p.get("pair_id"),
                "condition_id": p.get("condition_id"),
                "market": p.get("market"),
                "orders": [],
                "tokens": [],
                "max_pair_cost_at_post": p.get("max_pair_cost_at_post"),
                "combined_price": p.get("combined_price"),
                "combined_price_is_paid": p.get("combined_price_is_paid"),
                "hedge_state": p.get("hedge_state"),
            })
        trimmed["pairs"] = live_pairs + lean_dead
        trimmed["cancelled_pairs_total"] = sum(len(v) for v in dead_pairs.values())

    trimmed["cancelled_orders_per_market_cap"] = CANCELLED_ORDERS_PER_MARKET
    return trimmed


@app.get("/api/state")
def get_state():
    """Return JSON state snapshot for the live execution dashboard."""
    from core_brain.registry_state import summarize_state
    db_path = resolve_db_path(_ACTIVE_DB_OVERRIDE)
    payload = _cached_snapshot(
        ("state", str(db_path)),
        lambda: _trim_cancelled_orders(summarize_state(db_path)),
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
    # ordered by arm index.
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
            return (is_running, c_age, t["issue"], t["stamp"], t.get("index", 0))
        return (is_running, r.get("heartbeat_age_sec", 0.0), 0, "", 0)

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


def _start_stack_commands(sweep_interval_sec: float | None) -> list[list[str]]:
    """Argv (minus the interpreter) for the three START processes.

    Single source for `start_bot` and the preflight preview: a flag change
    here moves both, so the preview cannot drift from what START launches.
    The guardrail watchdog (`scripts.global_stop_loss`) is deliberately
    absent -- the dashboard never starts it.
    """
    query = ["-m", "core_brain.order_manager", "poll", "--interval", "0.5"]
    if sweep_interval_sec is not None:
        query += ["--sweep-interval", f"{sweep_interval_sec:g}"]
    return [
        ["-m", "scripts.filter_loop"],
        query,
        ["-m", "core_brain.trader_loop", "--live",
         "--no-reconcile", "--no-sweep", "--interval", "5"],
    ]


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
                f"so running them from here would be invisible. Restart the "
                f"dashboard without --db / LIVE_DB_PATH first."
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

    filter_started_at = _service_started_at(filter_pid, filter_info, filter_running)
    query_started_at = _service_started_at(query_pid, query_info, query_running)
    decide_started_at = _service_started_at(decide_pid, decide_info, decide_running)

    configured_sweep_interval = resolve_sweep_interval()
    running_sweep_interval = query_info.get("sweep_interval_sec") if query_running else None

    dash_running = True
    dash_pid = os.getpid()
    dash_started_at = _process_start_time(dash_pid)

    bot_running = bool(filter_running or query_running or decide_running)

    db_identity = resolve_db_identity(resolve_db_path(_ACTIVE_DB_OVERRIDE))

    _bot_state = (
        "UNKNOWN" if registry_unreadable
        else ("RUNNING" if bot_running else "STOPPED")
    )

    return {
        "services": {
            "filter": {
                "name": "Market Filter",
                "running": filter_running,
                "pid": filter_pid if filter_running else None,
                "started_at": filter_started_at,
                "uptime_sec": _uptime_sec(filter_started_at),
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
        "shadow_run": _recent_shadow_run(db_identity["path"]),
        # The OTHER rehearsals on this machine, including live ones writing a
        # store this page is not pointed at. Named, never read into the numbers
        # above: without them a dead run on a stale store reads as "the engine
        # is down" while a healthy trial writes to a file nobody is watching.
        "shadow_runs": _recent_shadow_runs(db_identity["path"]),
        "starting_capital": get_starting_capital(),
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


def start_bot() -> dict:
    """Launch background Screener and Reconcile loop."""
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
                f"Restart the dashboard without --db / LIVE_DB_PATH to start "
                f"the stack."
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

        # POSIX only: a new session makes each launched service a process-group
        # leader, so STOP can signal the whole tree (ranker/watcher children
        # included) instead of just the root.
        popen_group = {"start_new_session": True} if sys.platform != "win32" else {}

        stack_cmds = _start_stack_commands(resolve_sweep_interval())

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
        # _CFG.bankroll_usd (a hardcoded config constant) as its baseline,
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
        procs_file.write_text(json.dumps(new_procs, indent=2), encoding="utf-8")

        reused = [n for n, alive in
                  (("filter", filter_alive), ("query", query_alive), ("decide", decide_alive))
                  if alive]
        return {
            "ok": True,
            "message": "Bot stack started",
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
    deadline = time.monotonic() + (
        _STOP_POLITE_WAIT_S + _STOP_FORCE_WAIT_S + 2 * _STOP_TASKKILL_TIMEOUT_S)
    outcomes: dict[str, dict[str, str]] = {}
    alive = [(n, p, s) for (n, p, s) in targets if _is_pid_alive(p, s)]
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
        elif pid in forced_pids:
            # It only came down after the force pass: report that honestly.
            outcomes[name] = {"outcome": "forced", "detail": errors.get(pid, "")}
        else:
            outcomes[name] = {"outcome": "stopped", "detail": errors.get(pid, "")}
    return outcomes


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


@app.post("/api/system/start")
def api_system_start(request: Request):
    """Start background bot stack."""
    _authorize_control(request)
    return JSONResponse(start_bot())


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


@app.post("/api/system/db")
def api_system_switch_db(request: Request, db: str | None = None):
    """Point the page at another store (read-only; changes no machine state)."""
    _authorize_control(request)
    return JSONResponse(switch_active_db(db))


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
        subprocess.Popen(relaunch_argv(), cwd=str(LIVE_ROOT))
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
    from core_brain.order_manager import cancel_all
    try:
        cancel_all(live=True)
        return JSONResponse({"ok": True, "message": "All open orders cancelled on the venue."})
    except SystemExit as e:
        return JSONResponse(
            {"ok": False, "message": f"Venue rejected cancel-all: {e}"},
            status_code=502,
        )
    except Exception as e:
        return JSONResponse(
            {"ok": False, "message": f"Cancel-all failed: {e}"},
            status_code=500,
        )


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
    from core_brain.order_manager import cancel_all, account_sweep

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
    try:
        cancel_all(live=True)
        steps.append("venue: all open orders cancelled")
    except (Exception, SystemExit) as e:
        steps.append(f"venue: cancel-all skipped ({e})")

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


@app.get("/api/parameters")
def get_parameters(registry=None):
    """Return active strategy settings, trigger thresholds, and action descriptions.

    Consolidates safety limits from core_brain.config:MakerConfig (max_pair_cost,
    max_naked_usd, min_quote_shares, max_order_usd, max_total_usd) and the sweep
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
            reg = OrderRegistry()
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
    naked_usd = dynamic["max_naked_usd"]
    order_usd = dynamic["max_order_usd"]
    total_usd = dynamic["max_total_usd"]
    basis_label = "account value" if portfolio_usd is not None else "bankroll"

    sweep = resolve_sweep_interval()
    params = [
        {
            "name": "max_pair_cost",
            "value": f"${cfg.max_pair_cost:.2f}",
            "trigger": f"Combined UP + DOWN maker buy cost reaches or exceeds ${cfg.max_pair_cost:.2f}",
            "action": "Refuses to quote pair to ensure guaranteed positive spread profit on merge",
        },
        {
            "name": "max_naked_usd",
            "value": f"${naked_usd:.2f} ({cfg.naked_risk_pct*100:.0f}% of {basis_label})",
            "trigger": f"One leg fills while the opposing leg is unfilled, creating unhedged exposure > ${naked_usd:.2f}",
            "action": "Stops quoting new orders on that market; prepares emergency exit / merge",
        },
        {
            "name": "max_order_usd",
            "value": f"${order_usd:.2f} ({cfg.order_risk_pct*100:.0f}% of {basis_label})",
            "trigger": f"Order sizing calculation generates a single order > ${order_usd:.2f}",
            "action": "Clamps size to prevent accidental capital overcommitment",
        },
        {
            "name": "max_total_usd",
            "value": f"${total_usd:.2f} ({cfg.bankroll_ceiling_pct*100:.0f}% {basis_label} ceiling)",
            "trigger": f"Sum of all open notional across fleet reaches ${total_usd:.2f}",
            "action": "Refuses all new quotes across all markets until existing orders settle or cancel",
        },
        {
            "name": "min_quote_shares",
            "value": f"{cfg.min_quote_shares} shares",
            "trigger": "Calculated order size falls below Polymarket venue minimum",
            "action": "Refuses single-sided quote or scales up to {} shares if budget permits".format(cfg.min_quote_shares),
        },
        {
            "name": "sweep_interval",
            "value": f"{sweep:.0f}s" if sweep is not None else "every tick",
            "trigger": "{} elapsed since last wallet balance query".format(f"{sweep:.0f}s" if sweep is not None else "every poll cycle"),
            "action": "Fetches fresh on-chain USDC balance and updates float marks",
        },
    ]
    return JSONResponse({"parameters": params})


@app.get("/api/active-markets")
def get_active_markets():
    """Return graduated and currently quoted markets with order depth and PnL.

    Splits /api/state's market data into active-only (not resolved) for
    the Market Inspection table's ACTIVE MARKETS view.
    """
    from core_brain.registry_state import summarize_state
    state = summarize_state(resolve_db_path(_ACTIVE_DB_OVERRIDE))
    # Filter to markets with active orders or fills
    active = [p for p in state.get("pairs", []) if p.get("status") in ("RESTING", "NAKED", "BALANCED")]
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
        html.replace(CONTROL_TOKEN_PLACEHOLDER, CONTROL_TOKEN),
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


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
        html.replace(CONTROL_TOKEN_PLACEHOLDER, CONTROL_TOKEN),
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
