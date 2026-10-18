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

import threading
import time

from core_brain import markets

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
