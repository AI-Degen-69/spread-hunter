"""The book/tape fetchers moved out of the deleted strategy/main.py (issue
#14). These tests pin the new homes: importable from the market-data module,
and the sweep module re-exports the very same functions -- so the existing
monkeypatch seams (`strategy.sweep.full_book` / `recent_trades`) still control
what the engine fetches.

The parse half of the fetch seam lives here too: `parse_book` is the ONE
place venue /book rows become typed levels. Its contract -- skip and count
row garbage, raise on a structural failure -- is what keeps a malformed
payload from crashing the ranker or masquerading as a network failure in the
sweep's book gate.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strategy import markets                                     # noqa: E402


def test_fetchers_import_from_the_market_data_module():
    from strategy.markets import full_book, recent_trades

    assert callable(full_book)
    assert callable(recent_trades)


class _StubResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class _StubBookSession:
    """A CLOB session that serves canned /book payloads per token and refuses
    anything else -- the offline guarantee for the fetch tests."""

    def __init__(self, books_by_token):
        self.books = dict(books_by_token)

    def get(self, url, params=None, timeout=None):
        tok = (params or {}).get("token_id")
        if url.endswith("/book") and tok in self.books:
            return _StubResponse(self.books[tok])
        raise AssertionError(f"unexpected request: url={url} params={params}")


class _StubTape:
    """A data-api session that serves one canned /trades payload."""

    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        return _StubResponse(self._payload)


# --- parse_book: the parse half of the fetch seam -------------------------

def test_parse_book_returns_the_canonical_shape():
    raw = {"bids": [{"price": "0.49", "size": "60"},
                    {"price": "0.48", "size": "60"}],
           "asks": [{"price": "0.51", "size": "60"}]}
    book = markets.parse_book(raw, "tok-1")
    assert book["token_id"] == "tok-1"
    assert book["bids"] == {0.49: 60.0, 0.48: 60.0}
    assert book["asks"] == {0.51: 60.0}
    assert book["best_bid"] == 0.49
    assert book["best_ask"] == 0.51
    assert book["malformed"] == 0


def test_parse_book_skips_and_counts_unparseable_rows():
    """Row garbage is tolerated and counted -- the same tolerance the
    selector's depth gate applies to its inputs. One bad level must never
    take down a caller."""
    raw = {"bids": [{"price": "0.49", "size": "60"},
                    {"price": "garbage", "size": "60"},
                    {"price": "0.47"},          # no size key
                    "not-a-row"],
           "asks": [{"price": None, "size": "60"},
                    {"price": "0.51", "size": "60"}]}
    book = markets.parse_book(raw, "tok-1")
    assert book["bids"] == {0.49: 60.0}
    assert book["asks"] == {0.51: 60.0}
    assert book["malformed"] == 4


def test_parse_book_raises_on_a_structural_failure():
    """A payload that is not a dict, or a side that is not a list, is a
    fetch-shaped failure -- callers treat it like an unreadable book."""
    with pytest.raises(ValueError):
        markets.parse_book([], "tok")            # not a dict
    with pytest.raises(ValueError):
        markets.parse_book({"bids": "nope", "asks": []}, "tok")


# --- market slug sanitizing: venue data must never reach the DB/HTML raw --

def test_sanitize_slug_keeps_normal_slugs():
    assert markets._sanitize_slug("nfl-cardinals-vs-49ers-2026-09-29") == \
        "nfl-cardinals-vs-49ers-2026-09-29"
    assert markets._sanitize_slug("btc-up-or-down-2026") == \
        "btc-up-or-down-2026"


def test_sanitize_slug_strips_html_and_quotes():
    """PR #22 review: a hostile venue-supplied slug used to pass through to
    dashboard HTML attributes/links and the fleet DB. Only URL-safe
    characters survive the venue-data boundary."""
    assert markets._sanitize_slug('<img src=x onerror=alert(1)>') == "imgsrcxonerroralert1"
    assert markets._sanitize_slug('x\" onmouseover=\"alert(1)') == "xonmouseoveralert1"
    assert markets._sanitize_slug("x');alert(1);//") == "xalert1"
    assert markets._sanitize_slug("") == ""


def test_parse_market_sanitizes_the_slug(monkeypatch):
    """The venue-data entry point applies the sanitizer before the slug can
    be persisted by any caller."""
    raw = {
        "conditionId": "cond-1",
        "clobTokenIds": '["tok-a", "tok-b"]',
        "eventStartTime": "2026-09-29T16:00:00Z",
        "endDate": "2026-09-29T18:00:00Z",
        "slug": 'x" onmouseover="alert(1)',
    }
    m = markets._parse_market(raw)
    assert m is not None
    assert m.market_slug == "xonmouseoveralert1"


def test_parse_book_handles_an_empty_book():
    book = markets.parse_book({"bids": [], "asks": []}, "tok")
    assert book["bids"] == {}
    assert book["asks"] == {}
    assert book["best_bid"] is None and book["best_ask"] is None
    assert book["malformed"] == 0


# --- full_book: bad levels no longer masquerade as a network failure ------

def test_full_book_skips_bad_levels_instead_of_raising(monkeypatch):
    """REGRESSION: the old parse sat outside any try, so one garbage level
    raised out of full_book and the sweep's book gate caught it as though the
    venue were unreachable -- cancelling quotes and stamping BOOK_FAILED on a
    healthy host."""
    raw = {"bids": [{"price": "0.49", "size": "60"},
                    {"price": "junk", "size": "5"}],
           "asks": [{"price": "0.51", "size": "60"}]}
    monkeypatch.setattr(markets, "_SESSION", _StubBookSession({"tok": raw}))
    book = markets.full_book("https://clob.example", "tok")
    assert book["bids"] == {0.49: 60.0}
    assert book["malformed"] == 1
    assert book["best_bid"] == 0.49


# --- recent_trades: a bad row must not silently kill the market's sweep ---

def test_recent_trades_skips_bad_rows_without_dropping_the_tape(monkeypatch):
    """REGRESSION: the parse sat outside the fetch try, so one unparseable
    price raised out of the loop and the sweep's "exceptions propagate"
    contract turned it into a market that vanished from every sweep with no
    status, no err and no event."""
    rows = [
        {"transactionHash": "0x1", "asset": "tok", "timestamp": 1,
         "price": "0.50", "size": "10"},
        {"transactionHash": "0x2", "asset": "tok", "timestamp": 2,
         "price": "NaN!", "size": "5"},
        "junk-row",
        {"transactionHash": "0x3", "asset": "tok", "timestamp": 3,
         "price": "0.51", "size": "7"},
    ]
    tape = _StubTape(rows)
    monkeypatch.setattr(markets, "_SESSION", tape)
    seen = set()
    out = markets.recent_trades("cond-1", seen)
    assert out == {"tok": {0.5: 10.0, 0.51: 7.0}}
    # The malformed DICT row still consumed its dedup key, so a repeat of the
    # same bad trade is not re-parsed; the junk ROW was never a trade.
    assert len(seen) == 3


def test_recent_trades_returns_empty_on_a_non_list_response(monkeypatch):
    """A dict-shaped /trades response used to crash iterating its keys; now
    it reads as no tape, which the caller already knows how to fall back from."""
    tape = _StubTape({"data": []})
    monkeypatch.setattr(markets, "_SESSION", tape)
    assert markets.recent_trades("cond-1", set()) == {}
    assert tape.calls


def test_sweep_re_exports_the_same_functions():
    """Identity, not a copy: a monkeypatch on strategy.sweep.full_book must be
    the one the sweep actually calls, and it must be markets' function."""
    from strategy import sweep
    from strategy.markets import full_book, recent_trades

    assert sweep.full_book is full_book
    assert sweep.recent_trades is recent_trades
