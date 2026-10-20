"""Fallback tests for the #659 WS-first book provider (TDD RED)."""
from __future__ import annotations

import pytest

from core_brain.market_stream import BookNotReady


STREAM_BOOK = {
    "token_id": "tok",
    "bids": {0.40: 100.0},
    "asks": {0.42: 100.0},
    "best_bid": 0.40,
    "best_ask": 0.42,
    "malformed": 0,
}

REST_BOOK = {
    "token_id": "tok",
    "bids": {0.39: 50.0},
    "asks": {0.43: 50.0},
    "best_bid": 0.39,
    "best_ask": 0.43,
    "malformed": 0,
}


class FakeStream:
    """Duck-typed stand-in for MarketStream."""

    def __init__(self, *, connected=True, books=None):
        self.connected = connected
        self.books = dict(books or {})
        self.fetch_calls: list[str] = []
        self.stopped = False

    def fetch_books(self, clob_host, token_id):
        self.fetch_calls.append(str(token_id))
        if not self.connected:
            raise BookNotReady(f"market stream is not connected ({token_id})")
        try:
            return self.books[str(token_id)]
        except KeyError:
            raise BookNotReady(f"no live book for token {token_id}")

    def ready_tokens(self):
        return list(self.books)

    def ensure_market(self, condition_id, slug, up_token, down_token):
        self.ensured = (condition_id, slug, up_token, down_token)

    def retain_markets(self, condition_ids):
        self.retained = list(condition_ids)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


def _rest(book=REST_BOOK, calls=None):
    def _reader(clob_host, token_id):
        (calls if calls is not None else []).__iadd__([(clob_host, token_id)])
        return dict(book)
    return _reader


def test_ready_stream_serves_without_rest():
    from core_brain.book_provider import BookProvider

    calls: list = []
    provider = BookProvider(
        stream=FakeStream(connected=True, books={"tok": STREAM_BOOK}),
        rest_reader=_rest(calls=calls),
    )
    assert provider.get_book("tok") == STREAM_BOOK
    assert calls == []


def test_disconnected_stream_falls_back_to_rest():
    from core_brain.book_provider import BookProvider

    calls: list = []
    provider = BookProvider(
        stream=FakeStream(connected=False), rest_reader=_rest(calls=calls)
    )
    assert provider.get_book("tok") == REST_BOOK
    assert [t for _, t in calls] == ["tok"]


def test_stale_stream_falls_back_to_rest():
    """A connected stream with no book for the token is not-ready: REST."""
    from core_brain.book_provider import BookProvider

    calls: list = []
    provider = BookProvider(
        stream=FakeStream(connected=True, books={}),
        rest_reader=_rest(calls=calls),
    )
    assert provider.get_book("tok") == REST_BOOK
    assert [t for _, t in calls] == ["tok"]


def test_silent_stream_with_cached_book_falls_back_to_rest():
    """Connected but silent (book present yet unreadable): stale → REST."""
    from core_brain.book_provider import BookProvider

    class SilentStream(FakeStream):
        def fetch_books(self, clob_host, token_id):
            self.fetch_calls.append(str(token_id))
            raise BookNotReady(f"market stream went silent ({token_id})")

    reasons: list = []
    stream = SilentStream(connected=True, books={"tok": STREAM_BOOK})
    provider = BookProvider(
        stream=stream,
        rest_reader=_rest(),
        emit_fn=lambda *a, **k: reasons.append(k.get("reason")),
    )
    assert provider.get_book("tok") == REST_BOOK
    assert reasons == ["stale"]


def test_no_stream_reads_rest_directly():
    from core_brain.book_provider import BookProvider

    calls: list = []
    provider = BookProvider(stream=None, rest_reader=_rest(calls=calls))
    assert provider.get_book("tok") == REST_BOOK
    assert [t for _, t in calls] == ["tok"]


def test_rest_failure_raises_never_empty_book():
    from core_brain.book_provider import BookProvider

    def _boom(clob_host, token_id):
        raise ConnectionError("venue down")

    provider = BookProvider(stream=FakeStream(connected=False), rest_reader=_boom)
    with pytest.raises(ConnectionError):
        provider.get_book("tok")


def test_get_fresh_book_never_touches_cache():
    from core_brain.book_provider import BookProvider

    stream = FakeStream(connected=True, books={"tok": STREAM_BOOK})
    calls: list = []
    provider = BookProvider(stream=stream, rest_reader=_rest(calls=calls))
    assert provider.get_fresh_book("tok") == REST_BOOK
    assert stream.fetch_calls == []
    assert [t for _, t in calls] == ["tok"]


def test_both_paths_return_same_shape():
    from core_brain.book_provider import BookProvider

    provider = BookProvider(
        stream=FakeStream(connected=True, books={"tok": STREAM_BOOK}),
        rest_reader=_rest(),
    )
    stream_book = provider.get_book("tok")
    fresh_book = provider.get_fresh_book("tok")
    assert set(stream_book) == set(fresh_book) == {
        "token_id", "bids", "asks", "best_bid", "best_ask", "malformed",
    }


def test_fetch_books_adapter_uses_seam_signature():
    from core_brain.book_provider import BookProvider

    provider = BookProvider(
        stream=FakeStream(connected=True, books={"tok": STREAM_BOOK}),
        rest_reader=_rest(),
    )
    assert provider.fetch_books("https://clob.polymarket.com", "tok") == STREAM_BOOK


@pytest.mark.parametrize(
    "book",
    [
        # crossed
        {"token_id": "t", "bids": {0.50: 1.0}, "asks": {0.48: 1.0},
         "best_bid": 0.50, "best_ask": 0.48, "malformed": 0},
        # one-sided
        {"token_id": "t", "bids": {}, "asks": {0.48: 1.0},
         "best_bid": None, "best_ask": 0.48, "malformed": 0},
        # out of range
        {"token_id": "t", "bids": {1.20: 1.0}, "asks": {1.30: 1.0},
         "best_bid": 1.20, "best_ask": 1.30, "malformed": 0},
    ],
)
def test_get_mid_none_for_invalid_books(book):
    from core_brain.book_provider import BookProvider

    provider = BookProvider(
        stream=FakeStream(connected=True, books={"t": book}),
        rest_reader=_rest(),
    )
    assert provider.get_mid("t") is None


def test_get_mid_for_valid_book():
    from core_brain.book_provider import BookProvider

    provider = BookProvider(
        stream=FakeStream(connected=True, books={"tok": STREAM_BOOK}),
        rest_reader=_rest(),
    )
    assert provider.get_mid("tok") == pytest.approx(0.41)


def test_fallback_telemetry_is_rate_limited():
    from core_brain.book_provider import BookProvider

    emitted: list = []
    provider = BookProvider(
        stream=FakeStream(connected=False),
        rest_reader=_rest(),
        emit_fn=lambda *a, **k: emitted.append((a, k)),
    )
    provider.get_book("tok")
    provider.get_book("tok")
    assert len(emitted) == 1


def test_only_owning_provider_stops_stream():
    from core_brain.book_provider import BookProvider

    borrowed = FakeStream()
    BookProvider(stream=borrowed, rest_reader=_rest(), owns_stream=False).stop()
    assert borrowed.stopped is False

    owned = FakeStream()
    BookProvider(stream=owned, rest_reader=_rest(), owns_stream=True).stop()
    assert owned.stopped is True
