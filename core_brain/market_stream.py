"""Live market stream (#648): one public socket, every book in memory.

A quote visit used to pay two blocking HTTP `GET /book` calls. This module
keeps the same books from the venue's public market channel instead, so a
read is a dict lookup:

    wss://ws-subscriptions-clob.polymarket.com/ws/market
    {"assets_ids": [...], "type": "market"}

Frames (raw CLOB wire shape, snake_case):
    {"event_type": "book", "asset_id": ..., "bids": [{"price","size"}], "asks": [...]}
    {"event_type": "price_change", "price_changes": [{"asset_id", "price",
     "size", "side": "BUY"|"SELL", "best_bid": ..., "best_ask": ...}, ...]}

Fold rules: a `book` frame **replaces** both sides; a `price_change` entry
writes one level (`size == 0` removes it); any other `event_type` is ignored.

Fail closed: an unready book raises `BookNotReady` — never a stale or empty
book, never an HTTP fallback, never a wait. A closed socket drops every book,
because a dead feed must not keep serving prices.
"""
from __future__ import annotations

import json
import logging
import threading
import time

from core_brain import markets

log = logging.getLogger("market_stream")

MARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
SERVICE = "market_stream"

PING_TEXT = "PING"
PING_INTERVAL_SEC = 10.0
LIVE_WINDOW_SEC = 30.0
SNAPSHOT_TIMEOUT_SEC = 10.0
SNAPSHOT_RESEND_LIMIT = 1
RECONNECT_MIN_SEC = 1.0
RECONNECT_MAX_SEC = 30.0
TICK_SUMMARY_SEC = 10.0
READ_LATENCY_BUDGET_SEC = 0.001

_UNPARSEABLE = (TypeError, ValueError, KeyError, AttributeError)


class BookNotReady(RuntimeError):
    """The token has no live book: hold the resting orders instead of quoting."""


class MarketBookCache:
    """Thread-safe books, keyed by token id, in `parse_book` shape."""

    def __init__(self, time_fn=time.time):
        self._time = time_fn
        self._lock = threading.RLock()
        self._books: dict[str, dict] = {}
        # Counters read as deltas by the tick summary. One lock, one writer
        # thread; a torn read of an int is not a thing in CPython and a
        # one-window slip in a telemetry count costs nothing.
        self.frames = 0
        self.level_gap = 0

    def apply_frame(self, msg: dict | list) -> list[str]:
        """Fold one venue frame (dict or list) in; return the tokens it moved."""
        items = msg if isinstance(msg, list) else [msg]
        changed: list[str] = []
        with self._lock:
            for item in items:
                if not isinstance(item, dict):
                    continue
                kind = item.get("event_type")
                if kind == "book":
                    touched = self._apply_book(item)
                elif kind == "price_change":
                    touched = self._apply_changes(item)
                else:
                    continue  # last_trade_price, tick_size_change, ...
                self.frames += 1
                for token in touched:
                    if token not in changed:
                        changed.append(token)
        return changed

    def _apply_book(self, item: dict) -> list[str]:
        token = item.get("asset_id")
        if token is None:
            return []
        token = str(token)
        try:
            self._books[token] = markets.parse_book(
                {"bids": item.get("bids"), "asks": item.get("asks")}, token)
        except ValueError:
            # A snapshot whose side is not a level list cannot be trusted at
            # all: drop the token rather than keep half a book.
            self._books.pop(token, None)
        return [token]

    def _apply_changes(self, item: dict) -> list[str]:
        out: list[str] = []
        for row in item.get("price_changes") or []:
            token = self._apply_one_change(row)
            if token is not None and token not in out:
                out.append(token)
        return out

    def _apply_one_change(self, row) -> str | None:
        if not isinstance(row, dict):
            return None
        token = row.get("asset_id")
        side = str(row.get("side") or "").upper()
        if token is None or side not in ("BUY", "SELL"):
            return None
        token = str(token)
        book = self._books.get(token)
        if book is None:
            return None  # a delta with no snapshot to fold it into
        try:
            price = round(float(row["price"]), 4)
            size = float(row["size"])
        except _UNPARSEABLE:
            # A level we cannot read leaves this book silently diverged from
            # the venue: drop the token and let the next snapshot refill it.
            del self._books[token]
            return token
        ladder = book["bids"] if side == "BUY" else book["asks"]
        if size > 0:
            ladder[price] = size
        else:
            ladder.pop(price, None)
        book["best_bid"] = max(book["bids"]) if book["bids"] else None
        book["best_ask"] = min(book["asks"]) if book["asks"] else None
        self._count_gap(row, book)
        return token

    def _count_gap(self, row: dict, book: dict) -> None:
        """Count, never correct: a per-entry best_bid/best_ask that disagrees
        with the book we rebuilt. These per-entry fields' exact semantics are
        unverified, and acting on a wrong comparison would mean a resubscribe
        storm on a live money path — so this is evidence, not a trigger.
        """
        if book["best_bid"] is None or book["best_ask"] is None:
            return
        try:
            bid = row.get("best_bid")
            ask = row.get("best_ask")
            if bid is None or ask is None:
                return
            if (round(float(bid), 4) != round(book["best_bid"], 4)
                    or round(float(ask), 4) != round(book["best_ask"], 4)):
                self.level_gap += 1
        except _UNPARSEABLE:
            return

    def book(self, token_id: str) -> dict:
        """The token's latest book, or `BookNotReady`. Never waits."""
        with self._lock:
            book = self._books.get(str(token_id))
            if book is None:
                raise BookNotReady(f"no live book for token {token_id}")
            return {
                "token_id": book["token_id"],
                "bids": dict(book["bids"]),
                "asks": dict(book["asks"]),
                "best_bid": book["best_bid"],
                "best_ask": book["best_ask"],
                "malformed": book["malformed"],
            }

    def drop(self, token_ids) -> None:
        with self._lock:
            for token in token_ids:
                self._books.pop(str(token), None)

    def clear_all(self) -> None:
        """A closed socket drops every book: a dead feed must not serve prices."""
        with self._lock:
            self._books.clear()

    def ready_tokens(self) -> list[str]:
        with self._lock:
            return list(self._books)


class MarketStream:
    """One long-lived socket feeding a `MarketBookCache`; reads never block.

    Two daemon threads: the worker owns the socket and its reconnect loop, the
    heartbeat keeps the venue's `PING` cadence and hangs up on a feed that has
    gone silent. `stop()` is the only way to end both.
    """

    def __init__(self, *, cache=None, emit_fn=None, time_fn=time.time,
                 ping_sec=PING_INTERVAL_SEC, tick_sec=TICK_SUMMARY_SEC):
        self._cache = cache if cache is not None else MarketBookCache(
            time_fn=time_fn)
        self._emit_fn = emit_fn
        self._time = time_fn
        self._ping_sec = ping_sec
        self._tick_sec = tick_sec
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._markets: dict[str, tuple[str, str, str]] = {}
        self._want: set[str] = set()
        self._pending: set[str] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._heartbeat: threading.Thread | None = None
        self._app = None
        self._connected = False
        self._alive_at = 0.0
        self._close_code = None
        self._close_reason = ""
        self._backoff = RECONNECT_MIN_SEC
        self._pending_since = 0.0
        self._resends = 0

    # ── universe ───────────────────────────────────────────────────────────

    def ensure_market(self, condition_id, slug, up_token, down_token) -> None:
        """Register one market's two outcome tokens as wanted on this feed."""
        cid, slug = str(condition_id), str(slug)
        up, down = str(up_token), str(down_token)
        with self._lock:
            if self._markets.get(cid) == (slug, up, down):
                return
            self._markets[cid] = (slug, up, down)
            self._want.update((up, down))

    # ── lifecycle ──────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="market-stream", daemon=True)
        self._heartbeat = threading.Thread(
            target=self._heartbeat_loop, name="market-stream-heartbeat",
            daemon=True)
        self._thread.start()
        self._heartbeat.start()

    def stop(self) -> None:
        self._stop.set()
        app, self._app = self._app, None
        if app is not None:
            self._close(app)
        for attr in ("_thread", "_heartbeat"):
            thread = getattr(self, attr)
            setattr(self, attr, None)
            if (thread is not None and thread.is_alive()
                    and thread is not threading.current_thread()):
                thread.join(timeout=5.0)
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    def _run(self) -> None:
        """Dial, subscribe, and redial on a capped backoff until stopped."""
        try:
            import websocket  # local: no venue work at module import time
        except ImportError as e:
            # Loud, not silent: without the declared dependency this module
            # would serve an empty feed and hold every order for no reason.
            log.error(
                "market stream disabled: missing 'websocket-client' package "
                "(%s). Install requirements so books stop needing HTTP.", e)
            return

        while not self._stop.is_set():
            app = None
            try:
                app = websocket.WebSocketApp(
                    MARKET_WS_URL,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_close=self._on_close,
                    on_error=self._on_error,
                )
                with self._lock:
                    self._app = app
                # The venue's contract is an application-level text PING, so the
                # library's own protocol ping stays off: one heartbeat, not two.
                app.run_forever(ping_interval=0)
            except Exception as e:
                log.warning("market stream socket failed: %s: %s",
                            type(e).__name__, e)
            finally:
                with self._lock:
                    if self._app is app:
                        self._app = None
                self._connected = False
                self._cache.clear_all()
            if self._stop.is_set():
                break
            self._stop.wait(self._next_backoff())

    def _next_backoff(self) -> float:
        """The delay before this redial; a successful open resets the ladder."""
        delay = self._backoff
        self._backoff = min(delay * 2.0, RECONNECT_MAX_SEC)
        return delay

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self._ping_sec):
            try:
                self._heartbeat_tick()
            except Exception as e:
                # Telemetry and venue hiccups must never kill the heartbeat.
                log.warning("market stream heartbeat failed: %s: %s",
                            type(e).__name__, e)

    def _heartbeat_tick(self) -> None:
        """Keep the PING cadence, or hang up on a feed that has gone quiet.

        Nothing inbound inside the window means the socket is a zombie we are
        still reading prices from: closing it deliberately is what makes the
        reconnect path run.
        """
        app = self._app
        if app is None:
            return
        if self._time() - self._alive_at > LIVE_WINDOW_SEC:
            self._close(app)
            return
        self._send_text(app, PING_TEXT)

    # ── socket callbacks ───────────────────────────────────────────────────

    def _on_open(self, app) -> None:
        self._connected = True
        self._alive_at = self._time()
        self._backoff = RECONNECT_MIN_SEC
        with self._lock:
            tokens = sorted(self._want)
        self._pending = set(tokens)
        self._pending_since = self._time()
        self._resends = 0
        self._send_frame(app, {"assets_ids": tokens, "type": "market"})

    def _on_message(self, app, message) -> None:
        self._alive_at = self._time()  # any inbound frame is liveness
        if isinstance(message, (bytes, bytearray)):
            message = message.decode("utf-8", "replace")
        text = str(message).strip()
        if not text or text.upper() == "PONG":
            return
        try:
            payload = json.loads(text)
        except ValueError:
            log.debug("market stream: unparseable frame skipped")
            return
        self._cache.apply_frame(payload)

    def _on_close(self, app, code=None, reason="") -> None:
        self._close_code = code
        self._close_reason = str(reason or "")
        self._connected = False
        self._cache.clear_all()

    def _on_error(self, app, error) -> None:
        log.warning("market stream socket error: %s", error)

    # ── sending ────────────────────────────────────────────────────────────

    def _send_text(self, app, text: str) -> bool:
        """One send lock: the worker, the heartbeat and callers all send here."""
        with self._send_lock:
            try:
                app.send(text)
            except Exception as e:
                log.debug("market stream send failed: %s", e)
                return False
            return True

    def _send_frame(self, app, payload: dict) -> bool:
        return self._send_text(app, json.dumps(payload))

    def _close(self, app) -> None:
        try:
            app.close()
        except Exception as e:
            log.debug("market stream close failed: %s", e)
