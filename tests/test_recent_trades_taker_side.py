"""The tape credits a resting BUY only from trades a taker SOLD into.

A resting BUY at $0.42 is filled when somebody SELLS at $0.42. A taker who
BUYS at $0.42 lifts an ask, or mints against the complement leg -- either way
our bid is still sitting there untouched. `recent_trades` used to return the
whole tape at a price with no regard for which side the taker took, and
`shadow_fills.credit_fills` then spent that volume on our queue. On a live
sample of 500 trades the endpoint returned 84% BUY, so the shadow fill model
was being handed roughly six times the volume that could ever reach it, and
every rehearsal fill rate and time-to-fill built on it read fast.

`core_brain/live_fill_engine.py` is unaffected and stays that way: live, a
fill exists only when the venue says so.
"""
from __future__ import annotations

import pytest

from core_brain import markets


class _FakeResponse:
    def __init__(self, rows):
        self._rows = rows

    def raise_for_status(self):
        return None

    def json(self):
        return self._rows


@pytest.fixture
def tape(monkeypatch):
    """Serve a fixed trade list to `recent_trades` with no network."""
    def _serve(rows):
        monkeypatch.setattr(
            markets._SESSION, "get",
            lambda *a, **k: _FakeResponse(rows), raising=False)
    return _serve


def _trade(side, price, size, tx, ts=1789000000, asset="tok"):
    return {"transactionHash": tx, "asset": asset, "timestamp": ts,
            "price": price, "size": size, "side": side}


def test_taker_buys_are_not_credited_to_a_resting_bid(tape):
    # Arrange -- the same price level, one taker SELL and one taker BUY.
    tape([_trade("SELL", 0.42, 7.0, "0xsell"),
          _trade("BUY", 0.42, 93.0, "0xbuy")])

    # Act
    out = markets.recent_trades("0xcond", set())

    # Assert -- only the seller's size can reach our bid.
    assert out == {"tok": {0.42: 7.0}}


def test_full_tape_is_available_when_the_caller_asks_for_it(tape):
    # Arrange -- a research recorder wants every print, not just the sells.
    tape([_trade("SELL", 0.42, 7.0, "0xsell"),
          _trade("BUY", 0.42, 93.0, "0xbuy")])

    # Act
    out = markets.recent_trades("0xcond", set(), taker_side=None)

    # Assert
    assert out == {"tok": {0.42: 100.0}}


def test_a_trade_with_no_readable_side_is_skipped(tape):
    """Under-counting is the conservative direction for a fill model.

    If the endpoint ever drops the field the rehearsal must report no fills,
    not invent them.
    """
    # Arrange
    row = _trade("SELL", 0.42, 7.0, "0xsell")
    row.pop("side")
    tape([row])

    # Act
    out = markets.recent_trades("0xcond", set())

    # Assert
    assert out == {}


def test_side_matching_ignores_case_and_padding(tape):
    # Arrange
    tape([_trade(" sell ", 0.42, 7.0, "0xsell")])

    # Act
    out = markets.recent_trades("0xcond", set())

    # Assert
    assert out == {"tok": {0.42: 7.0}}


def test_the_book_tape_recorder_still_reads_the_whole_tape():
    """`reachable_fraction` divides by ALL tape, above-mid prints included.

    Handing the recorder a SELL-only tape would silently shrink that
    denominator and inflate every reachability number it has ever recorded.
    The fill model is the only caller that wants the filtered view.
    """
    import inspect

    from scripts import book_tape_recorder

    src = inspect.getsource(book_tape_recorder.main)
    assert "taker_side=None" in src


# --- #401: the 0.26 DOWN miss. Taker-view prints arrive BUY-labelled
# (mint flow) and the single page overflows on busy markets, so the reader
# must page the tape and read maker-perspective fills too.


@pytest.fixture
def paged_tape(monkeypatch):
    """Serve rows by page index and record every request.

    Walk 1 (settlement baseline) sends no takerOnly key; walk 2 (maker
    fills) sends takerOnly=False. Page index = offset // limit, so tests
    drive multi-page walks with small limits and single-row pages.
    """
    calls = []

    def _serve(pages):
        def _get(url, params=None, **kw):
            params = dict(params or {})
            calls.append(params)
            lim = int(params.get("limit", 500)) or 500
            idx = int(params.get("offset", 0)) // lim
            to = params.get("takerOnly", "default")
            rows = pages.get((idx, to), pages.get(idx, []))
            if isinstance(rows, Exception):
                raise rows
            return _FakeResponse(rows)

        monkeypatch.setattr(markets._SESSION, "get", _get, raising=False)

    _serve.calls = calls
    return _serve


def test_buy_row_does_not_suppress_matching_sell(paged_tape):
    # Arrange -- same trade identity, BUY row first (mint leg), SELL after.
    paged_tape({0: [_trade("BUY", 0.26, 9.0, "0xmint"),
                    _trade("SELL", 0.26, 9.0, "0xmint")]})

    # Act
    out = markets.recent_trades("0xcond", set())

    # Assert -- the SELL still counts.
    assert out == {"tok": {0.26: 9.0}}


def test_missing_side_row_does_not_suppress_repaired_sell(paged_tape):
    # Arrange -- unreadable side first, repaired SELL with same identity.
    broken = _trade("SELL", 0.26, 9.0, "0xfix")
    broken.pop("side")
    paged_tape({0: [broken, _trade("SELL", 0.26, 9.0, "0xfix")]})

    # Act
    out = markets.recent_trades("0xcond", set())

    # Assert
    assert out == {"tok": {0.26: 9.0}}


def test_second_page_is_read_when_first_page_has_no_overlap(paged_tape):
    # Arrange -- seen holds an unrelated trade; both pages are fresh.
    paged_tape({0: [_trade("SELL", 0.26, 1.0, "0xelsewhere")]})
    seen = set()
    markets.recent_trades("0xcond", seen)  # primes seen, key format agnostic
    paged_tape({0: [_trade("SELL", 0.26, 5.0, "0xp0")],
                1: [_trade("SELL", 0.26, 7.0, "0xp1")]})
    paged_tape.calls.clear()

    # Act (limit=1: single-row pages read as full, so the walk continues).
    out = markets.recent_trades("0xcond", seen, limit=1)

    # Assert -- both pages count, the walk ends on the empty third page.
    assert out == {"tok": {0.26: 12.0}}
    walk1 = [p.get("offset", 0) for p in paged_tape.calls if "takerOnly" not in p]
    assert walk1 == [0, 1, 2]


def test_pagination_stops_at_first_all_seen_page(paged_tape):
    # Arrange -- page 0 mixes fresh with seen, page 1 is all seen.
    old = _trade("SELL", 0.26, 3.0, "0xold")
    paged_tape({0: [old]})
    seen = set()
    markets.recent_trades("0xcond", seen)  # primes seen with old's key
    paged_tape({0: [_trade("SELL", 0.26, 5.0, "0xnew"), dict(old)],
                1: [dict(old)],
                2: [_trade("SELL", 0.26, 9.0, "0xfar")]})
    paged_tape.calls.clear()

    # Act (limit=2: page 1 is short AND all seen, so the walk stops).
    out = markets.recent_trades("0xcond", seen, limit=2)

    # Assert -- page 2 never requested; the all-seen page adds nothing.
    walk1 = [p.get("offset", 0) for p in paged_tape.calls if "takerOnly" not in p]
    assert walk1 == [0, 2]
    assert out == {"tok": {0.26: 5.0}}


def test_pagination_respects_the_page_bound(paged_tape):
    # Arrange -- ten fresh pages; the reader must stop at its bound.
    bound = getattr(markets, "TRADE_MAX_PAGES", 4)
    pages = {i: [_trade("SELL", 0.26, 1.0, f"0xpg{i}")] for i in range(10)}
    paged_tape(pages)

    # Act (limit=1: every stub page reads as full, only the bound stops).
    out = markets.recent_trades("0xcond", set(), limit=1)

    # Assert
    walk1 = [p.get("offset", 0) for p in paged_tape.calls if "takerOnly" not in p]
    assert walk1 == list(range(bound))
    assert out == {"tok": {0.26: float(bound)}}


def test_repeat_poll_with_same_seen_returns_zero(paged_tape):
    # Arrange
    paged_tape({0: [_trade("SELL", 0.26, 9.0, "0xonce")]})
    seen = set()
    assert markets.recent_trades("0xcond", seen) == {"tok": {0.26: 9.0}}

    # Act -- same tape, same seen: nothing new.
    assert markets.recent_trades("0xcond", seen) == {}


def test_maker_buy_absent_from_taker_view_counts(paged_tape):
    # Arrange -- taker walk shows a BUY (skipped); maker walk shows a
    # resting-bid fill at our price that the taker walk never carried.
    maker_fill = _trade("BUY", 0.26, 30.0, "0xmaker")
    paged_tape({0: [_trade("BUY", 0.26, 1140.0, "0xtaker")],
                (0, False): [maker_fill]})

    # Act
    out = markets.recent_trades("0xcond", set())

    # Assert
    assert out == {"tok": {0.26: 30.0}}
    assert any(p.get("takerOnly") is False for p in paged_tape.calls)


def test_maker_sell_absent_from_taker_view_does_not_count(paged_tape):
    # Arrange -- a maker ask-lift is not queue drain; under-counting stays
    # the conservative direction.
    paged_tape({0: [], (0, False): [_trade("SELL", 0.26, 55.0, "0xask")]})

    # Act
    out = markets.recent_trades("0xcond", set())

    # Assert
    assert out == {}


def test_taker_sell_present_in_both_views_counts_once(paged_tape):
    # Arrange -- same SELL print in both walks must not double-count.
    row = _trade("SELL", 0.26, 7.0, "0xboth")
    paged_tape({0: [row], (0, False): [dict(row)]})

    # Act
    out = markets.recent_trades("0xcond", set())

    # Assert
    assert out == {"tok": {0.26: 7.0}}


def test_failed_page_marks_nothing_and_recovers_next_poll(paged_tape):
    # Arrange -- page 1 fails: this poll credits nothing and marks nothing,
    # so the next healthy poll counts every row exactly once.
    import requests

    boom = requests.RequestException("tape down")
    paged_tape({0: [_trade("SELL", 0.26, 5.0, "0xp0")], 1: boom})
    seen = set()

    # Act
    assert markets.recent_trades("0xcond", seen, limit=1) == {}
    assert seen == set()
    paged_tape({0: [_trade("SELL", 0.26, 5.0, "0xp0")],
                1: [_trade("SELL", 0.26, 7.0, "0xp1")]})

    # Assert
    assert markets.recent_trades("0xcond", seen, limit=1) == {"tok": {0.26: 12.0}}


def test_walk2_request_error_keeps_walk1_volume(paged_tape):
    # Arrange -- the maker walk fails like a network error: taker volume stays.
    import requests

    paged_tape({0: [_trade("SELL", 0.26, 7.0, "0xt")],
                (0, False): requests.RequestException("maker down")})

    # Act
    out = markets.recent_trades("0xcond", set())

    # Assert
    assert out == {"tok": {0.26: 7.0}}


def test_walk2_implementation_error_propagates(paged_tape):
    # Arrange -- a bug is loud, never a silent partial attribution.
    paged_tape({0: [_trade("SELL", 0.26, 7.0, "0xt")],
                (0, False): RuntimeError("bug")})

    # Act / Assert
    with pytest.raises(RuntimeError):
        markets.recent_trades("0xcond", set())


def test_taker_buy_mode_never_opens_the_maker_walk(paged_tape):
    # Arrange -- a taker-BUY request must read taker BUYs, not maker fills.
    paged_tape({0: [_trade("BUY", 0.26, 93.0, "0xtaker")],
                (0, False): [_trade("BUY", 0.26, 30.0, "0xmaker")]})

    # Act
    out = markets.recent_trades("0xcond", set(), taker_side="BUY")

    # Assert
    assert out == {"tok": {0.26: 93.0}}
    assert not any(p.get("takerOnly") is False for p in paged_tape.calls)
