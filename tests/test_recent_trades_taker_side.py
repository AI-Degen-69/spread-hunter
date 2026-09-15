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


def _trade(side, price, size, tx):
    return {"transactionHash": tx, "asset": "tok", "timestamp": 1789000000,
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
