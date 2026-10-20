"""Unified real-time book provider (#659): stream first, REST fallback.

Every trading process reads books through this one seam. The venue's public
market socket (via `MarketStream`) serves the book from memory; when the
stream cannot — not connected, gone silent, or no book for the token — the
read falls back to the REST `GET /book` reader instead of raising an
unhandled error.

Fail-closed: `BookNotReady` never escapes this module. If REST also fails,
the REST reader's own exceptions propagate (callers already treat those as
fetch failures) — never an empty or stale book. REST results are never
written into the stream cache: two writers would create competing state.

Fill accounting is untouched: the REST trade tape stays the only fill
evidence. Execution-time reads (hard-stop exit polls, order completion)
use `get_fresh_book`, which is always REST and never touches the cache.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from typing import Any, Callable

from core_brain import markets
from core_brain.market_stream import BookNotReady

log = logging.getLogger("book_provider")

DEFAULT_CLOB_HOST = "https://clob.polymarket.com"
SERVICE = "book_provider"

BOOK_SOURCE_STREAM = "stream"
BOOK_SOURCE_REST = "rest"

REASON_NOT_CONNECTED = "not_connected"
REASON_STALE = "stale"
REASON_NOT_READY = "not_ready"

TELEMETRY_WINDOW_SEC = 60.0


def _side_to_levels(side: Any, *, reverse: bool) -> list[dict]:
    """Price-keyed `{price: size}` map → sorted `[{"price", "size"}]` levels."""
    if not isinstance(side, dict):
        return []
    rows = []
    for price, size in side.items():
        try:
            rows.append({"price": float(price), "size": float(size)})
        except (TypeError, ValueError):
            continue
    rows.sort(key=lambda r: r["price"], reverse=reverse)
    return rows


class BookProvider:
    """Stream-first, REST-fallback book reads with a seam-shaped adapter."""

    def __init__(
        self,
        *,
        stream: Any | None = None,
        rest_reader: Callable[[str, str], dict] | None = None,
        clob_host: str = DEFAULT_CLOB_HOST,
        emit_fn: Callable[..., None] | None = None,
        owns_stream: bool = False,
        time_fn: Callable[[], float] = time.time,
        telemetry_window_sec: float = TELEMETRY_WINDOW_SEC,
        stats_box: dict | None = None,
    ) -> None:
        self._stream = stream
        self._rest_reader = rest_reader if rest_reader is not None else markets.full_book
        self._clob_host = clob_host
        self._emit_fn = emit_fn
        self._owns_stream = owns_stream
        self._stats_box = stats_box
        self._time = time_fn
        self._telemetry_window_sec = telemetry_window_sec
        self._lock = threading.Lock()
        self._last_emit_at = 0.0
        self._suppressed = 0

    # ── reads ──────────────────────────────────────────────────────────

    def get_book(self, token_id: str) -> dict:
        """The token's book: stream first, REST when the stream cannot serve."""
        return self._read(str(token_id), self._clob_host)

    def fetch_books(self, clob_host: str, token_id: str) -> dict:
        """The `VenueSeam.fetch_books(clob_host, token_id)` adapter.

        Same two arguments as the HTTP read it replaces, so existing seams
        accept the provider unchanged. The stream ignores the host (the book
        is already in memory); the REST fallback uses the passed host.
        """
        return self._read(str(token_id), str(clob_host))

    def _read(self, token_id: str, rest_host: str) -> dict:
        """One stream-first read; `rest_host` serves the fallback only."""
        t0 = time.perf_counter()
        stream = self._stream
        source = BOOK_SOURCE_REST
        if stream is not None:
            try:
                book = stream.fetch_books(self._clob_host, token_id)
                source = BOOK_SOURCE_STREAM
            except BookNotReady as e:
                reason = self._fallback_reason(stream, token_id)
                self._note_fallback(token_id, reason, str(e))
                book = self._rest_reader(rest_host, token_id)
        else:
            book = self._rest_reader(rest_host, token_id)
        self._note_stats(token_id, source, (time.perf_counter() - t0) * 1000.0)
        return book

    def fetch_fresh_books(self, clob_host: str, token_id: str) -> dict:
        """REST-only read with the seam signature, for execution paths.

        Same arguments as `fetch_books` so execution call sites accept it
        unchanged; never touches the stream cache.
        """
        return self._rest_reader(str(clob_host), str(token_id))

    def get_fresh_book(self, token_id: str) -> dict:
        """Always-REST read for execution paths. Never touches the cache."""
        return self._rest_reader(self._clob_host, str(token_id))

    def get_levels_book(self, token_id: str) -> dict:
        """Stream-first book in SDK levels shape, for `get_order_book` callers.

        Defense readers (`single_buy_saver`, `stray_guard`) consume books
        through `client.get_order_book(token_id)`, which returns levels
        lists (`[{"price", "size"}]`) per side — not the price-keyed dicts
        of `parse_book`. This adapts the unified read into that shape so
        those call sites need no parsing change.
        """
        book = self.get_book(token_id)
        return {
            "token_id": book.get("token_id", str(token_id)),
            "bids": _side_to_levels(book.get("bids"), reverse=True),
            "asks": _side_to_levels(book.get("asks"), reverse=False),
        }

    def get_mid(self, token_id: str) -> float | None:
        """Mid price under the `live_marks` validity rule, else None.

        Requires both sides, finite best prices, `bid < ask`, and the binary
        0–1 range. Anything else returns None — never zero, never stale.
        """
        try:
            book = self.get_book(token_id)
        except Exception:
            return None
        bids = book.get("bids") or {}
        asks = book.get("asks") or {}
        if not bids or not asks:
            return None
        try:
            bid = max(bids)
            ask = min(asks)
            bid_f, ask_f = float(bid), float(ask)
        except (TypeError, ValueError):
            return None
        if not (math.isfinite(bid_f) and math.isfinite(ask_f)):
            return None
        if not (0.0 <= bid_f and ask_f <= 1.0 and bid_f < ask_f):
            return None
        return (bid_f + ask_f) / 2.0

    # ── fallback classification + telemetry ────────────────────────────

    def _fallback_reason(self, stream: Any, token_id: str) -> str:
        """Why the stream could not serve: connected/stale/not-ready."""
        try:
            connected = stream.connected
        except AttributeError:
            connected = True
        if not connected:
            return REASON_NOT_CONNECTED
        try:
            ready = stream.ready_tokens()
        except AttributeError:
            return REASON_NOT_READY
        try:
            if str(token_id) in set(ready):
                return REASON_STALE
        except TypeError:
            return REASON_NOT_READY
        return REASON_NOT_READY

    def _note_fallback(self, token_id: str, reason: str, detail: str) -> None:
        """Rate-limited fallback event on the `market_stream` emit hook.

        At most one event per window: the guardian thread and the main loop
        share the provider, and one event per read would churn the ring.
        Never raises — telemetry must not break a money path.
        """
        emit = self._emit_fn
        if emit is None:
            return
        try:
            with self._lock:
                now = self._time()
                if now - self._last_emit_at < self._telemetry_window_sec:
                    self._suppressed += 1
                    return
                self._last_emit_at = now
                suppressed = self._suppressed
                self._suppressed = 0
            emit(0, "stream", "book_provider_fallback", service=SERVICE,
                 market_slug="", reason=reason,
                 extra={"token_id": token_id, "reason": reason,
                        "suppressed": suppressed, "detail": detail[:120]})
        except Exception as e:
            log.warning("book provider telemetry failed: %s: %s",
                        type(e).__name__, e)

    def _note_stats(self, token_id: str, source: str, read_ms: float) -> None:
        """Record the last read for the decide row (`book_stats_fn`).

        One dict, overwritten per read: the dashboard shows where the book
        came from without a per-read ring event churning the log.
        """
        box = self._stats_box
        if box is not None:
            box.clear()
            box.update({
                "book_source": source,
                "book_read_ms": read_ms,
                "token_id": token_id,
            })

    # ── lifecycle passthrough ──────────────────────────────────────────

    def ensure_market(self, condition_id: str, slug: str,
                      up_token: str, down_token: str) -> None:
        if self._stream is not None:
            self._stream.ensure_market(condition_id, slug, up_token, down_token)

    def retain_markets(self, condition_ids) -> None:
        if self._stream is not None:
            self._stream.retain_markets(condition_ids)

    def start(self) -> None:
        if self._stream is not None:
            self._stream.start()

    def stop(self) -> None:
        """Stop the stream only when this provider owns it (joins threads)."""
        if self._stream is not None and self._owns_stream:
            self._stream.stop()
