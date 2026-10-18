"""User Account WebSocket stream for real-time fills and order updates.

Subscribes to Polymarket's authenticated WebSocket user channel
(wss://ws-subscriptions-clob.polymarket.com/ws/user) to stream execution trades
and order status changes in real time.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import threading
import time
from collections.abc import Callable, Iterable
from typing import Any

log = logging.getLogger("user_stream")

USER_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/user"
PING_TEXT = "PING"
PING_INTERVAL_SEC = 10.0
PONG_MISS_LIMIT = 3
RECONNECT_MIN_SEC = 1.0
RECONNECT_MAX_SEC = 30.0
FINAL_TRADE_STATUSES = frozenset({"CONFIRMED", "FAILED"})
SERVICE = "user_stream"

_PARSE_ERRORS = (ValueError, TypeError, KeyError, AttributeError)

try:
    from dotenv import find_dotenv, load_dotenv

    _env_file = find_dotenv(usecwd=True)
    if _env_file:
        load_dotenv(_env_file)
except ImportError:
    pass
except OSError as e:
    log.warning("user_stream: failed to read .env file at %s: %s", _env_file, e)




class UserStreamCredentialsMissing(RuntimeError):

    """Raised when required L2 API credentials are missing from the environment."""


def get_user_stream_creds():
    """Retrieve API credentials and wallet label for user stream authentication.

    Returns:
        (creds, wallet_label) where creds is ApiCreds and wallet_label is str.
    """
    from core_brain import venue

    creds = venue.api_creds_from_env()
    if creds is None:
        missing = [
            var for var in ("POLY_API_KEY", "POLY_API_SECRET", "POLY_API_PASSPHRASE")
            if not os.environ.get(var)
        ]
        raise UserStreamCredentialsMissing(
            f"User stream requires credentials in environment. Missing: {', '.join(missing)}"
        )
    wallet = os.environ.get("POLY_FUNDER") or "unknown"
    return creds, wallet


@dataclasses.dataclass(frozen=True)
class MakerOrderFill:
    order_id: str
    price: float
    size: float
    side: str


@dataclasses.dataclass(frozen=True)
class UserTradeEvent:
    trade_id: str
    order_id: str
    condition_id: str
    token_id: str
    side: str
    price: float
    size: float
    status: str
    fills: tuple[MakerOrderFill, ...] = ()


@dataclasses.dataclass(frozen=True)
class UserOrderEvent:
    order_id: str
    condition_id: str
    token_id: str
    side: str
    price: float
    size: float
    status: str


def parse_frame(text: str) -> tuple[list[UserTradeEvent | UserOrderEvent], int, int]:
    """Parse one raw WebSocket message text into structured events.

    Pure, network-free parser.

    Returns:
        (events, pongs, skipped_count)
    """
    if not text:
        return [], 0, 0

    stripped = text.strip()
    if stripped == PING_TEXT or stripped == "PONG":
        return [], 1 if stripped == "PONG" else 0, 0

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        log.warning("user_stream: unparseable non-JSON frame: %s", text[:80])
        return [], 0, 1

    items = data if isinstance(data, list) else [data]
    events: list[UserTradeEvent | UserOrderEvent] = []
    pongs = 0
    skipped = 0

    for item in items:
        if not isinstance(item, dict):
            skipped += 1
            continue

        event_type = item.get("event_type")
        if event_type == "pong" or item.get("type") == "pong":
            pongs += 1
            continue

        if event_type == "trade":
            try:
                trade_id = str(item.get("id") or item.get("trade_id") or "")
                order_id = str(item.get("taker_order_id") or item.get("order_id") or "")
                condition_id = str(item.get("market") or item.get("condition_id") or "")
                token_id = str(item.get("asset_id") or item.get("token_id") or "")
                side = str(item.get("side") or "").upper()
                price = float(item["price"])
                size = float(item.get("size") or item.get("amount") or 0.0)
                status = str(item.get("status") or "").upper()

                maker_orders_raw = item.get("maker_orders") or []
                fills = []
                for m in maker_orders_raw:
                    if isinstance(m, dict):
                        m_id = str(m.get("order_id") or m.get("id") or "")
                        m_price = float(m["price"])
                        m_size = float(m.get("size") or m.get("amount") or 0.0)
                        m_side = str(m.get("side") or "").upper()
                        fills.append(MakerOrderFill(order_id=m_id, price=m_price, size=m_size, side=m_side))

                events.append(UserTradeEvent(
                    trade_id=trade_id,
                    order_id=order_id,
                    condition_id=condition_id,
                    token_id=token_id,
                    side=side,
                    price=price,
                    size=size,
                    status=status,
                    fills=tuple(fills),
                ))
            except _PARSE_ERRORS as e:
                log.warning("user_stream: skipped malformed trade item: %s (%s)", item, e)
                skipped += 1

        elif event_type == "order":
            try:
                order_id = str(item.get("id") or item.get("order_id") or "")
                condition_id = str(item.get("market") or item.get("condition_id") or "")
                token_id = str(item.get("asset_id") or item.get("token_id") or "")
                side = str(item.get("side") or "").upper()
                price = float(item["price"])
                size = float(item.get("size") or item.get("original_size") or 0.0)
                status = str(item.get("status") or "").upper()

                events.append(UserOrderEvent(
                    order_id=order_id,
                    condition_id=condition_id,
                    token_id=token_id,
                    side=side,
                    price=price,
                    size=size,
                    status=status,
                ))
            except _PARSE_ERRORS as e:
                log.warning("user_stream: skipped malformed order item: %s (%s)", item, e)
                skipped += 1

        else:
            # Unknown event type (e.g. initial connection echo, market info)
            skipped += 1

    return events, pongs, skipped


class UserStreamClient:
    """Thread-safe WebSocket client and callback registry for user stream events.

    Callbacks run on the socket receiver thread and must execute quickly.
    Stream notices are execution notifications and should be reconciled against
    settled ledger states where strict accounting is required.
    """

    def __init__(self, *, emit_fn: Callable[..., None] | None = None, time_fn: Callable[[], float] = time.time):
        self._emit_fn = emit_fn
        self._time = time_fn
        self._lock = threading.RLock()
        self._send_lock = threading.Lock()
        self._trade_cbs: list[Callable[[UserTradeEvent], None]] = []
        self._order_cbs: list[Callable[[UserOrderEvent], None]] = []
        self._state_cbs: list[Callable[[str, str], None]] = []

        self._markets: set[str] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._heartbeat: threading.Thread | None = None
        self._app: Any = None
        self._connected = False
        self._auth_ok = False
        self._alive_at = 0.0
        self._pongs_missed = 0
        self._backoff = RECONNECT_MIN_SEC
        self._close_code: int | None = None
        self._close_reason: str = ""
        self._creds: Any = None
        self._wallet: str = "unknown"

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def auth_ok(self) -> bool:
        return self._auth_ok

    def on_trade(self, callback: Callable[[UserTradeEvent], None]) -> Callable[[], None]:
        """Register a callback for incoming UserTradeEvent notices. Returns unsubscribe function."""
        with self._lock:
            self._trade_cbs.append(callback)

        def unsubscribe():
            with self._lock:
                if callback in self._trade_cbs:
                    self._trade_cbs.remove(callback)

        return unsubscribe

    def on_order(self, callback: Callable[[UserOrderEvent], None]) -> Callable[[], None]:
        """Register a callback for incoming UserOrderEvent notices. Returns unsubscribe function."""
        with self._lock:
            self._order_cbs.append(callback)

        def unsubscribe():
            with self._lock:
                if callback in self._order_cbs:
                    self._order_cbs.remove(callback)

        return unsubscribe

    def on_state(self, callback: Callable[[str, str], None]) -> Callable[[], None]:
        """Register a callback for connection lifecycle state changes. Returns unsubscribe function."""
        with self._lock:
            self._state_cbs.append(callback)

        def unsubscribe():
            with self._lock:
                if callback in self._state_cbs:
                    self._state_cbs.remove(callback)

        return unsubscribe

    def start(self, condition_ids: Iterable[str] | None = None) -> None:
        """Validate credentials and launch the stream worker and heartbeat daemon threads."""
        with self._lock:
            if self._thread is not None:
                return
            self._creds, self._wallet = get_user_stream_creds()
            if condition_ids:
                self._markets = {str(cid) for cid in condition_ids}
            else:
                self._markets.clear()

            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, name="user-stream-worker", daemon=True
            )
            self._heartbeat = threading.Thread(
                target=self._heartbeat_loop, name="user-stream-heartbeat", daemon=True
            )
            self._thread.start()
            self._heartbeat.start()

    def stop(self) -> None:
        """Signal clean stop, terminate socket, and join threads."""
        self._stop.set()
        with self._lock:
            app, self._app = self._app, None
        if app is not None:
            self._close(app)

        for attr in ("_thread", "_heartbeat"):
            with self._lock:
                thread = getattr(self, attr)
                setattr(self, attr, None)
            if thread is not None and thread.is_alive() and thread is not threading.current_thread():
                thread.join(timeout=5.0)

        self._connected = False
        self._auth_ok = False
        self._dispatch_state("STOPPED", "clean stop")
        self._emit("user_ws_stopped", reason="clean stop")

    def _run(self) -> None:
        """Worker loop: dial WebSocket, authenticate, stream, and reconnect with exponential backoff."""
        try:
            import websocket
        except ImportError as e:
            log.error("user stream disabled: missing 'websocket-client' package: %s", e)
            self._emit("user_ws_error", reason=f"missing websocket-client: {e}")
            return

        while not self._stop.is_set():
            app = None
            try:
                app = websocket.WebSocketApp(
                    USER_WS_URL,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_close=self._on_close,
                    on_error=self._on_error,
                )
                with self._lock:
                    self._app = app
                app.run_forever(ping_interval=0)
            except Exception as e:  # noqa: BLE001
                log.warning("user stream socket error: %s: %s", type(e).__name__, e)
                self._emit("user_ws_error", reason=f"socket error: {e}")
            finally:
                with self._lock:
                    if self._app is app:
                        self._app = None
                self._connected = False
                self._auth_ok = False

            if self._stop.is_set():
                break

            delay = self._next_backoff()
            self._dispatch_state("RECONNECTING", f"backoff {delay}s")
            self._emit("user_ws_reconnecting", reason="reconnect_backoff",
                       extra={"delay_sec": delay, "reason": self._close_reason, "close_code": self._close_code})
            self._stop.wait(delay)

    def _next_backoff(self) -> float:
        delay = self._backoff
        self._backoff = min(delay * 2.0, RECONNECT_MAX_SEC)
        return delay

    def _on_open(self, app: Any) -> None:
        self._connected = True
        self._alive_at = self._time()
        self._pongs_missed = 0
        self._auth_ok = False

        self._dispatch_state("CONNECTED", f"wallet: {self._wallet}")
        self._emit("user_ws_connected", reason="connected", extra={"url": USER_WS_URL, "wallet": self._wallet})

        auth_frame: dict[str, Any] = {
            "auth": {
                "apiKey": self._creds.api_key,
                "secret": self._creds.api_secret,
                "passphrase": self._creds.api_passphrase,
            },
            "type": "user",
        }
        if self._markets:
            auth_frame["markets"] = sorted(self._markets)

        self._send_frame(app, auth_frame)
        self._emit("user_ws_auth_sent", reason="auth_sent", extra={"markets_count": len(self._markets)})

    def _on_message(self, app: Any, message: str) -> None:
        self._alive_at = self._time()
        self._pongs_missed = 0

        if not self._auth_ok:
            self._auth_ok = True
            self._backoff = RECONNECT_MIN_SEC
            self._dispatch_state("AUTH_OK", f"wallet: {self._wallet}")
            self._emit("user_ws_auth_ok", reason=f"auth_ok wallet: {self._wallet}", extra={"wallet": self._wallet})

        events, _, _ = parse_frame(message)

        for event in events:
            self._dispatch_event(event)

            if isinstance(event, UserTradeEvent):
                self._emit(
                    "user_ws_trade",
                    reason=f"trade {event.trade_id} {event.status}",
                    extra={
                        "trade_id": event.trade_id,
                        "order_id": event.order_id,
                        "status": event.status,
                        "price": event.price,
                        "size": event.size,
                        "token_id": event.token_id,
                        "condition_id": event.condition_id,
                        "fills_count": len(event.fills),
                    },
                )
            elif isinstance(event, UserOrderEvent):
                self._emit(
                    "user_ws_order",
                    reason=f"order {event.order_id} {event.status}",
                    extra={
                        "order_id": event.order_id,
                        "status": event.status,
                        "price": event.price,
                        "size": event.size,
                        "token_id": event.token_id,
                        "condition_id": event.condition_id,
                    },
                )

    def _on_close(self, app: Any, code: int | None = None, reason: str = "") -> None:
        self._close_code = code
        self._close_reason = str(reason or "")
        self._connected = False
        self._auth_ok = False
        self._dispatch_state("DISCONNECTED", self._close_reason)
        self._emit("user_ws_disconnected", reason=self._close_reason,
                   extra={"close_code": code, "reason": self._close_reason})

    def _on_error(self, app: Any, error: Any) -> None:
        log.warning("user_stream: socket error: %s", error)
        self._emit("user_ws_error", reason=str(error), extra={"error": str(error)})

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(PING_INTERVAL_SEC):
            try:
                self._heartbeat_tick()
            except Exception as e:  # noqa: BLE001
                log.warning("user_stream: heartbeat tick error: %s", e)

    def _heartbeat_tick(self) -> None:
        with self._lock:
            app = self._app
            connected = self._connected
        if app is None or not connected:
            return

        self._pongs_missed += 1
        if self._pongs_missed > PONG_MISS_LIMIT:
            log.warning("user_stream: missed %d PONGs, closing unresponsive socket", self._pongs_missed)
            self._emit("user_ws_error", reason="pong_timeout", extra={"pongs_missed": self._pongs_missed})
            self._close(app)
            return

        self._send_text(app, PING_TEXT)

    def _send_text(self, app: Any, text: str) -> None:
        try:
            with self._send_lock:
                app.send(text)
        except Exception as e:  # noqa: BLE001
            log.warning("user_stream: send text failed: %s", e)

    def _send_frame(self, app: Any, frame: dict[str, Any]) -> None:
        try:
            raw = json.dumps(frame)
            with self._send_lock:
                app.send(raw)
        except Exception as e:  # noqa: BLE001
            log.warning("user_stream: send frame failed: %s", e)

    def _close(self, app: Any) -> None:
        try:
            app.close()
        except Exception as e:  # noqa: BLE001
            log.warning("user_stream: app.close() failed: %s", e)

    def _dispatch_event(self, event: UserTradeEvent | UserOrderEvent) -> None:
        if isinstance(event, UserTradeEvent):
            with self._lock:
                targets = list(self._trade_cbs)
        elif isinstance(event, UserOrderEvent):
            with self._lock:
                targets = list(self._order_cbs)
        else:
            return

        for cb in targets:
            try:
                cb(event)
            except Exception:
                log.exception("user_stream: error in %s callback", type(event).__name__)

    def _dispatch_state(self, state: str, details: str = "") -> None:
        with self._lock:
            targets = list(self._state_cbs)

        for cb in targets:
            try:
                cb(state, details)
            except Exception:
                log.exception("user_stream: error in state callback")

    def _emit(self, action: str, *, reason: str = "", extra: dict[str, Any] | None = None) -> None:
        """Emit one cycle event record via injected emit_fn or cycle_stream.emit."""
        emit = self._emit_fn
        if emit is None:
            try:
                from core_brain import cycle_stream
                emit = cycle_stream.emit
            except Exception:  # noqa: BLE001
                return

        try:
            emit(
                0,
                "stream",
                action,
                service=SERVICE,
                reason=reason,
                extra=extra or {},
                can_rotate=False,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("user_stream: telemetry emit failed: %s", e)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="User Account WebSocket stream listener probe")
    parser.add_argument("--listen", action="store_true", help="Start user stream and listen for events")
    parser.add_argument(
        "--condition-id",
        dest="condition_ids",
        action="append",
        help="Filter by condition ID (can be specified multiple times)",
    )
    args = parser.parse_args(argv)

    if args.listen:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        client = UserStreamClient()
        client.on_trade(
            lambda t: print(
                f"[TRADE] {t.trade_id} {t.side} {t.size}@{t.price} status={t.status} fills={len(t.fills)}"
            )
        )
        client.on_order(
            lambda o: print(
                f"[ORDER] {o.order_id} {o.side} {o.size}@{o.price} status={o.status}"
            )
        )
        client.on_state(lambda s, d: print(f"[STATE] {s}: {d}"))
        print("Starting UserStreamClient (Ctrl+C to stop)...")
        try:
            client.start(condition_ids=args.condition_ids)
        except UserStreamCredentialsMissing as e:
            sys.stderr.write(
                f"[ERROR] {e}\n"
                "To run the user stream listener, ensure POLY_API_KEY, POLY_API_SECRET, and POLY_API_PASSPHRASE are set in your environment or .env file.\n"
            )
            return 1
        try:
            while True:
                time.sleep(1.0)
        except KeyboardInterrupt:
            print("\nStopping UserStreamClient...")
            client.stop()
            print("Stopped.")
        return 0
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())

