"""Live mark cache (#427): shared, read-only venue mids for held tokens.

One dashboard-server-owned feed replaces per-browser polling for live prices.
The worker holds no credentials and imports no order client — it only reads
the public market channel, the same protocol as the `order_manager` probe:

    wss://ws-subscriptions-clob.polymarket.com/ws/market
    {"assets_ids": [...], "type": "market"}

Frames carry `asset_id` plus `bids` / `asks` level lists
(`{"price", "size"}`), either as one dict or a list of them — keyed on
`asset_id` alone, never on message shape.

Mid rule mirrors `_cycle_mids` in `trader_loop.py` (both sides, finite,
bid < ask) plus the binary 0–1 range: anything else drops the entry instead
of keeping a stale mid. All timestamps are epoch seconds (float), the same
unit KPI quote rows use, so the browser can compare mark age against quote
age directly.
"""
from __future__ import annotations

import json
import logging
import math
import os
import random
import threading
import time

log = logging.getLogger("live_marks")

MARK_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

SOURCE_ENV_VAR = "HUNTER_LIVE_MARKS_SOURCE"
SOURCE_VENUE = "venue"
SOURCE_OFF = "off"
SOURCE_SIM = "sim"

WANTED_TTL_SEC = 120.0
WANTED_SETTLE_SEC = 1.0
RECONNECT_MIN_SEC = 1.0
RECONNECT_MAX_SEC = 30.0
SIM_TICK_SEC = 0.25
SIM_STEP = 0.005


def live_marks_source() -> str:
    """The mark source: `venue` (default), `off`, or `sim`."""
    raw = os.environ.get(SOURCE_ENV_VAR, SOURCE_VENUE).strip().lower()
    if raw not in (SOURCE_VENUE, SOURCE_OFF, SOURCE_SIM):
        raise ValueError(
            f"{SOURCE_ENV_VAR} must be venue, off, or sim (got {raw!r})")
    return raw


def _levels(items) -> tuple[dict[float, float], bool]:
    """Price → size map from a level list (zero sizes kept as removals).

    Returns the map plus a corrupt flag: a level with a non-finite price or
    size means the frame cannot be trusted, and the token's book must go
    rather than keep a mid built from garbage.
    """
    out: dict[float, float] = {}
    corrupt = False
    for lvl in items or []:
        try:
            price = float(lvl.get("price"))
            size = float(lvl.get("size", 0.0))
        except (TypeError, ValueError, AttributeError):
            corrupt = True
            continue
        if not (math.isfinite(price) and math.isfinite(size)):
            corrupt = True
            continue
        out[price] = size
    return out, corrupt


class LiveMarkCache:
    """Thread-safe in-memory mids, keyed by token_id."""

    def __init__(self, time_fn=time.time):
        self._time = time_fn
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._marks: dict[str, dict] = {}
        self._books: dict[str, dict[str, dict[float, float]]] = {}
        self._seq = 0
        self._reset_generation = 0
        self._wanted: dict[str, float] = {}
        self._seed_mids: dict[str, float] = {}

    @property
    def reset_generation(self) -> int:
        with self._lock:
            return self._reset_generation

    def _bump_locked(self) -> None:
        self._seq += 1
        self._cond.notify_all()

    def update_wanted(self, token_ids: set[str], now: float | None = None) -> bool:
        """Refresh the wanted set; True when membership changed."""
        at = self._time() if now is None else now
        with self._lock:
            before = set(self._wanted)
            for tok in token_ids:
                self._wanted[str(tok)] = at
            changed = set(self._wanted) != before
            return changed

    def expire_wanted(self, now: float | None = None,
                      ttl: float = WANTED_TTL_SEC) -> bool:
        """Drop tokens no payload mentioned inside the TTL; True if any left."""
        at = self._time() if now is None else now
        with self._lock:
            stale = [t for t, seen in self._wanted.items() if at - seen > ttl]
            for t in stale:
                del self._wanted[t]
                self._marks.pop(t, None)
                self._books.pop(t, None)
            if stale:
                self._bump_locked()
            return bool(stale)

    def wanted(self) -> set[str]:
        with self._lock:
            return set(self._wanted)

    def seed_mid(self, token_id: str, mid: float) -> None:
        """Newest KPI quote mid per token — the `sim` source walks from here."""
        try:
            mid = float(mid)
        except (TypeError, ValueError):
            return
        if math.isfinite(mid) and 0.0 <= mid <= 1.0:
            with self._lock:
                self._seed_mids[str(token_id)] = mid

    def _recompute_locked(self, token_id: str) -> bool:
        """Recompute one mid from its level maps; True when output changed."""
        book = self._books.get(token_id, {})
        bids = {p: s for p, s in book.get("bids", {}).items() if s > 0}
        asks = {p: s for p, s in book.get("asks", {}).items() if s > 0}
        book["bids"], book["asks"] = bids, asks
        before = self._marks.get(token_id)
        if not bids or not asks:
            self._marks.pop(token_id, None)
            return before is not None
        bid, ask = max(bids), min(asks)
        if not (math.isfinite(bid) and math.isfinite(ask)
                and 0.0 <= bid and ask <= 1.0 and bid < ask):
            self._marks.pop(token_id, None)
            return before is not None
        mid = (bid + ask) / 2.0
        if before is not None and before["mid"] == mid:
            return False
        self._marks[token_id] = {
            "token_id": token_id, "bid": bid, "ask": ask, "mid": mid,
            "ts": self._time(), "seq": self._seq + 1,
        }
        return True

    def apply_message(self, msg: dict | list) -> list[str]:
        """Fold one venue frame (dict or list) into the cache.

        Returns the token ids whose mark changed. Tokens outside the wanted
        set are ignored; the wanted set itself is untouched.
        """
        items = msg if isinstance(msg, list) else [msg]
        changed: list[str] = []
        with self._lock:
            want = set(self._wanted)
            for item in items:
                if not isinstance(item, dict):
                    continue
                token_id = item.get("asset_id")
                if token_id is None or str(token_id) not in want:
                    continue
                token_id = str(token_id)
                book = self._books.setdefault(
                    token_id, {"bids": {}, "asks": {}})
                for side in ("bids", "asks"):
                    if side in item:
                        levels, corrupt = _levels(item.get(side))
                        if corrupt:
                            # Untrustworthy frame: drop the whole book rather
                            # than keep a mid built from garbage.
                            book["bids"] = {}
                            book["asks"] = {}
                            break
                        for price, size in levels.items():
                            if size > 0:
                                book[side][price] = size
                            else:
                                book[side].pop(price, None)
                if self._recompute_locked(token_id):
                    if token_id in self._marks:
                        self._marks[token_id]["seq"] = self._seq + 1
                    changed.append(token_id)
            if changed:
                self._bump_locked()
        return changed

    def apply_sim_tick(self, token_id: str, mid: float,
                       now: float | None = None) -> bool:
        """Move one sim mid (no network); True when the mark changed."""
        try:
            mid = float(mid)
        except (TypeError, ValueError):
            return False
        if not (math.isfinite(mid) and 0.0 <= mid <= 1.0):
            return False
        at = self._time() if now is None else now
        with self._lock:
            before = self._marks.get(token_id)
            if before is not None and before["mid"] == mid:
                return False
            self._marks[str(token_id)] = {
                "token_id": str(token_id), "bid": mid, "ask": mid, "mid": mid,
                "ts": at, "seq": self._seq + 1,
            }
            self._bump_locked()
            return True

    def disconnect(self) -> None:
        """Clear all entries; readers must stop showing old prices as live."""
        with self._lock:
            self._marks.clear()
            self._books.clear()
            self._reset_generation += 1
            self._bump_locked()

    def snapshot(self) -> tuple[int, list[dict]]:
        """Current (global seq, mark list). Readers track the seq per stream."""
        with self._lock:
            return self._seq, [dict(m) for m in self._marks.values()]

    def wait(self, timeout: float) -> None:
        """Sleep until a mark changes or the timeout passes (stream pacing)."""
        with self._cond:
            self._cond.wait(timeout=timeout)

    def subscribe_payload(self) -> str:
        """The venue subscribe message for the current wanted set."""
        with self._lock:
            ids = sorted(self._wanted)
        return json.dumps({"assets_ids": ids, "type": "market"})


def update_wanted_from_kpi(cache: LiveMarkCache, payload: dict,
                           now: float | None = None) -> bool:
    """Refresh the wanted set from a served `/api/kpi` payload.

    Held, unfinished markets only (positive shares, not resolved): quote and
    fill token ids are collected, and quote mids seed the `sim` source. No
    report is built and no feed is read — the payload the server already
    served is the only input. Returns True when membership or expiry changed
    anything.
    """
    at = time.time() if now is None else now
    tokens: set[str] = set()
    for market in (payload or {}).get("by_market", {}).values():
        if not isinstance(market, dict):
            continue
        if market.get("resolved"):
            continue
        try:
            shares = float(market.get("up_sh", 0) or 0) + float(
                market.get("dn_sh", 0) or 0)
        except (TypeError, ValueError):
            continue
        if shares <= 0:
            continue
        for row in list(market.get("quotes", []) or []) + list(
                market.get("fills", []) or []):
            if not isinstance(row, dict):
                continue
            tok = row.get("token_id")
            if tok is None:
                continue
            tokens.add(str(tok))
            if "price" in row:
                cache.seed_mid(str(tok), row.get("price"))
    changed = cache.update_wanted(tokens, now=at)
    expired = cache.expire_wanted(now=at)
    return changed or expired


class LiveMarkWorker:
    """Owns one daemon feed (venue socket or sim walk) for a cache."""

    def __init__(self, cache: LiveMarkCache, source: str = SOURCE_VENUE,
                 time_fn=time.time):
        self._cache = cache
        self._source = source
        self._time = time_fn
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._app = None
        self._sent_ids: frozenset | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="live-marks", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=5.0)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        if self._source == SOURCE_SIM:
            self._run_sim()
        elif self._source == SOURCE_VENUE:
            self._run_venue()

    def _run_sim(self) -> None:
        """Random-walk the seeds several times a second; no network."""
        while not self._stop.is_set():
            for tok in self._cache.wanted():
                seed = self._cache._seed_mids.get(tok, 0.5)
                cur = self._cache._marks.get(tok, {}).get("mid", seed)
                step = random.uniform(-SIM_STEP, SIM_STEP)
                nxt = min(0.99, max(0.01, round(cur + step, 4)))
                self._cache.apply_sim_tick(tok, nxt)
            self._stop.wait(SIM_TICK_SEC)

    def _run_venue(self) -> None:
        """Read-only socket with backoff; never imports order clients."""
        import websocket  # local import: no venue work at module import time

        backoff = RECONNECT_MIN_SEC
        while not self._stop.is_set():
            app = None
            try:
                app = websocket.WebSocketApp(
                    MARK_WS_URL,
                    on_open=lambda ws: self._send_subscribe(ws),
                    on_message=lambda ws, msg: self._on_frame(msg),
                    on_close=lambda ws, *a: None,
                )
                self._app = app
                app.run_forever(ping_interval=30, ping_timeout=10)
            except Exception as e:
                log.warning("live-marks venue feed failed: %s: %s",
                            type(e).__name__, e)
            finally:
                if self._app is app:
                    self._app = None
            if self._stop.is_set():
                break
            self._cache.disconnect()
            self._stop.wait(backoff)
            backoff = min(backoff * 2.0, RECONNECT_MAX_SEC)

    def _on_frame(self, message: str) -> None:
        try:
            self._cache.apply_message(json.loads(message))
        except (ValueError, TypeError):
            pass

    def _send_subscribe(self, app) -> bool:
        """Send the current wanted set; True when it left the socket."""
        try:
            app.send(self._cache.subscribe_payload())
        except Exception:
            return False
        self._sent_ids = frozenset(self._cache.wanted())
        return True

    def sync_subscription(self) -> bool:
        """Push a drifted wanted set over the open socket; True when sent.

        Thread-safe nudge for the serving path: holdings change on KPI
        polls while the socket stays up, so without this a newly opened
        leg would wait for the next venue blip before its marks arrive.
        A no-op unless the worker is connected and the set actually moved.
        """
        app = self._app
        if app is None:
            return False
        if self._sent_ids is not None and set(self._sent_ids) == self._cache.wanted():
            return False
        return self._send_subscribe(app)
