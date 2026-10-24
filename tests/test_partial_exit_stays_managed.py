"""Partial exits stay managed until the remainder is gone (#646).

The old close-timestamp skip hid any pair whose latest fill predated its
latest close, so a partial exit's remainder never saw another pass. Now the
skip also requires the net leftover inventory gone; a remainder falls through
and the exit sells only what the venue still agrees is held (#326 netting).
No network in any test.
"""

from __future__ import annotations

import pytest

from core_brain import single_buy_saver as lp
from core_brain.market_resolution import MarketEndState
from core_brain.order_registry import OrderRegistry
from tests.test_hard_stop_reset import (
    COND,
    NOW,
    PAIR,
    TOK_DN,
    TOK_UP,
    ProvingVenue,
    _balances,
    _cfg,
    _seed_pair,
)


@pytest.fixture
def registry(tmp_path):
    return OrderRegistry(db_path=tmp_path / "live.db")


def _route(registry, venue, **kw):
    results = lp.manage_single_leg_positions(
        venue, registry, _cfg(), live=True, now=NOW / 1000.0 + 10.0,
        market_state_fn=lambda cid: MarketEndState(
            condition_id=COND, closed=False, end_ts=NOW / 1000.0 + 600.0,
            end_date_iso=None, end_date_passed=False, accepting_orders=True,
            resolved=False, unreachable=False),
        **kw)
    assert len(results) == 1
    return results[0]


class PartialThenFullVenue(ProvingVenue):
    """First sale fills 4 of 10; every later sale fills what was asked."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._sales = 0

    def create_and_post_market_order(self, order_args, options=None,
                                     order_type="FOK", defer_exec=False):
        verb = "sell" if order_args.side == "SELL" else "buy"
        self.calls.append(f"{verb}:{order_args.token_id}:{order_args.amount}")
        self._sales += 1
        if self._sales == 1:
            return {"success": True, "size": 4.0, "price": "0.14",
                    "orderID": "v-partial"}
        return {"success": True, "orderID": "venue-%s" % verb}


def _sells(venue):
    return [float(c.split(":")[2]) for c in venue.calls
            if c.startswith("sell:")]


def test_a_partial_stop_leaves_the_row_and_sells_only_the_remainder(
        registry, monkeypatch):
    """#646: 4 of 10 sold stays HARD_STOP; the next pass sells just 6."""
    _balances(monkeypatch, [])
    _seed_pair(registry)
    venue = PartialThenFullVenue()

    first = _route(registry, venue, funder="0xfunder",
                   venue_positions={TOK_UP: 10.0, TOK_DN: 0.0})
    assert first["action"] == "exited"
    assert first["size"] == pytest.approx(4.0)
    assert first["reset_status"] == "RESIDUAL"
    assert registry.get_lifecycle_state(PAIR).state == "HARD_STOP"
    # #796: the remainder stays managed AND fresh entries stay blocked.
    first_mark = registry.requalification_marks()[COND]
    assert first_mark.status == "awaiting"
    assert "hard_stop" in first_mark.reason

    # The venue sold those 4: 6 remain, and the close covers the old fill --
    # the exact shape the old skip hid. The pass must still sell the 6.
    second = _route(registry, venue, funder="0xfunder",
                    venue_positions={TOK_UP: 6.0, TOK_DN: 0.0})
    assert second["action"] == "exited"
    assert second["size"] == pytest.approx(6.0)
    assert second["reset_status"] == "RESET"
    assert _sells(venue) == [pytest.approx(10.0), pytest.approx(6.0)]
    assert registry.get_lifecycle_state(PAIR) is None


def test_route_to_merge_leaves_the_row_unchanged(registry, monkeypatch):
    """#646: a pair that completes mid-exit is routed, never reset."""
    _balances(monkeypatch, [])
    _seed_pair(registry)
    venue = ProvingVenue()

    def _completed(order_id):
        if str(order_id) == "venue-dn":
            return {"orderID": order_id, "size_matched": 10.0}
        return {"orderID": order_id, "size_matched": 0.0}

    venue.get_order = _completed

    routed = _route(registry, venue, funder="0xfunder",
                    venue_positions={TOK_UP: 10.0, TOK_DN: 0.0})

    assert routed["action"] == "route_to_merge"
    assert "reset_status" not in routed
    assert registry.get_lifecycle_state(PAIR).state == "HARD_STOP"
    assert registry.requalification_marks() == {}


def test_a_late_fill_after_cancel_blocks_the_reset(registry, monkeypatch):
    """#646: a fill the reconcile adopts mid-reset keeps the row managed."""
    _balances(monkeypatch, [])
    _seed_pair(registry)
    venue = ProvingVenue()
    venue.trades = [{
        "id": "late-1", "taker_order_id": "venue-dn",
        "size": 10.0, "price": 0.38, "match_time": NOW // 1000 + 60,
    }]

    routed = _route(registry, venue, funder="0xfunder",
                    venue_positions={TOK_UP: 10.0, TOK_DN: 0.0})

    assert routed["action"] == "exited"
    assert routed["reset_status"] == "LATE_FILL"
    assert registry.get_lifecycle_state(PAIR).state == "HARD_STOP"
    # #796: a late fill keeps the row managed but still blocks fresh entries.
    mark = registry.requalification_marks()[COND]
    assert mark.status == "awaiting"
    assert "hard_stop" in mark.reason
