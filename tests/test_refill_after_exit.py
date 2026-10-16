"""Refill-after-exit netting (issue #326): the fix and its guard cases.

`load_pair` sizes exits off fills only; prior `single_buy_exit` closes net
solely on the venue side. Re-posting exited shares under the SAME pair_id
used to make the next exit size fills-only naked against a netted venue
position, so `_check_positions` refused as a would-be oversell -- and the
refilled leg stranded, every rotation the same way.

The exit now nets prior single-leg exits on the condition (capped by the
observed venue gap), so a refill exits exactly the remainder with no
oversell. These tests pin the fixed behavior plus the guard cases that
still refuse: genuine and partially-explained divergence. The two routing
cases drive the unified lifecycle (`manage_single_leg_positions`), which is
the pass the live loop actually runs.

The fake venue mirrors `tests/test_single_buy_saver.py` (same response
shapes, same quote-ledger side resolution). No network in any test.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from core_brain.config import MakerConfig
from core_brain.market_resolution import MarketEndState
from core_brain.order_registry import (
    OrderRegistry, OrderRecord, FillRecord, QuoteRecord,
)
from core_brain import single_buy_saver as lp

MAX_PAIR_COST = 0.995
TOK_UP = "tok-up"
TOK_DN = "tok-dn"
COND = "0xcond-refill"
PAIR = "pair-refill"
NOW = 2_000_000


@pytest.fixture
def registry(tmp_path):
    return OrderRegistry(db_path=tmp_path / "refill.db")


class FakeVenue:
    """Records venue calls. No network."""

    def __init__(self, best_ask=0.40, best_bid=0.55, depth=100.0):
        self.best_ask = best_ask
        self.best_bid = best_bid
        self.depth = depth
        self.calls: list[str] = []

    def get_order_book(self, token_id):
        return {
            "asset_id": token_id, "tick_size": "0.01",
            "asks": [{"price": str(self.best_ask), "size": str(self.depth)}],
            "bids": [{"price": str(self.best_bid), "size": str(self.depth)}],
        }

    def get_order(self, order_id):
        return {"orderID": order_id, "size_matched": 0.0}

    def cancel_order(self, payload):
        self.calls.append(f"cancel:{getattr(payload, 'orderID', payload)}")
        return {"canceled": True}

    def create_and_post_market_order(self, order_args, options=None,
                                     order_type="FOK", defer_exec=False):
        verb = "sell" if order_args.side == "SELL" else "buy"
        self.calls.append(f"{verb}:{order_args.token_id}:{order_args.amount}")
        return {"success": True, "orderID": f"venue-{verb}"}


def _resting_up(registry: OrderRegistry, size: float, price: float,
                when: int, tag: str) -> OrderRecord:
    order = OrderRecord(
        id=str(uuid.uuid4()), order_id=f"venue-up-{tag}", condition_id=COND,
        token_id=TOK_UP, side="BUY", price=price, original_size=size,
        status="open", posted_ts=when, last_polled_ts=when, pair_id=PAIR,
        max_pair_cost_at_post=MAX_PAIR_COST,
    )
    registry.create_order(order)
    return order


def _fill(registry: OrderRegistry, order: OrderRecord, size: float,
          price: float, when: int) -> None:
    registry.record_fill(FillRecord(
        trade_id=f"trade-{order.id}", order_uuid=order.id, size=size,
        price=price, venue_ts=when,
    ))
    registry.update_order_status(order.id, status="filled",
                                 last_polled_ts=when)


def _ladder_market(registry: OrderRegistry, when: int) -> None:
    """One DOWN rung resting plus the quotes ledger both legs need."""
    down = OrderRecord(
        id=str(uuid.uuid4()), order_id="venue-dn", condition_id=COND,
        token_id=TOK_DN, side="BUY", price=0.38, original_size=10.0,
        status="open", posted_ts=when, last_polled_ts=when, pair_id=PAIR,
        max_pair_cost_at_post=MAX_PAIR_COST,
    )
    registry.create_order(down)
    registry.log_quote(QuoteRecord(
        ts=when / 1000.0, condition_id=COND, token_id=TOK_UP, side="UP",
        price=0.60, size=10.0,
    ))
    registry.log_quote(QuoteRecord(
        ts=when / 1000.0, condition_id=COND, token_id=TOK_DN, side="DOWN",
        price=0.38, size=10.0,
    ))


def _cfg() -> MakerConfig:
    """The manager's own knobs: both routes on, a 900s window and lead."""
    return MakerConfig(
        enable_pairs_rule=True, enable_aged_out_rescue=True,
        pairs_exit_window_sec=900.0, aged_out_rescue_lead_sec=900.0,
        max_pair_cost=MAX_PAIR_COST, max_order_usd=100.0,
    )


def _open_market_state(end_ts: float) -> MarketEndState:
    """An open, accepting market with a stated end -- no venue, no network."""
    return MarketEndState(
        condition_id=COND, closed=False, end_ts=end_ts, end_date_iso=None,
        end_date_passed=False, accepting_orders=True, resolved=False,
        unreachable=False,
    )


@pytest.fixture
def saver_clock(monkeypatch):
    """A mutable clock on the `time` `single_buy_saver` reads.

    A close stamps its own `ts` from the wall clock, and the pass skips any
    pair whose last fill is older than the condition's newest close. Real time
    (ms since 1970) would put that close decades after the fills this test
    uses, so the second pass would read the pair as already closed. The test
    owns the clock and advances it between passes instead.
    """
    now = {"t": 0.0}
    monkeypatch.setattr(lp, "time", SimpleNamespace(time=lambda: now["t"]))
    return now


def _manager_pass(registry: OrderRegistry, venue, *, now_s: float,
                  end_ts: float) -> dict:
    """One lifecycle pass, with the venue position the divergence gate needs
    (UP 10 held, DOWN nothing) and an open market inside the lead."""
    results = lp.manage_single_leg_positions(
        venue, registry, _cfg(), live=True, now=now_s,
        venue_positions={TOK_UP: 10.0, TOK_DN: 0.0},
        market_state_fn=lambda cid: _open_market_state(end_ts),
    )
    assert len(results) == 1
    return results[0]


def test_refill_after_exit_sells_the_netted_remainder(registry):
    """Exit 10, refill 10 under the same stamp: the second exit sells 10."""
    _ladder_market(registry, NOW)
    venue = FakeVenue(best_ask=0.40, best_bid=0.55)

    first = _resting_up(registry, 10.0, 0.60, NOW, "r1")
    _fill(registry, first, 10.0, 0.60, NOW)
    result = lp.exit_single_buy(venue, registry, PAIR,
                                max_pair_cost=MAX_PAIR_COST, live=True,
                                venue_positions={TOK_UP: 10.0, TOK_DN: 0.0})
    assert result["action"] == "exited"
    assert result["size"] == pytest.approx(10.0)

    # The venue sold those 10: it now holds 10 of the 20 fills on record.
    second = _resting_up(registry, 10.0, 0.60, NOW + 1, "r2")
    _fill(registry, second, 10.0, 0.60, NOW + 1)

    result = lp.exit_single_buy(venue, registry, PAIR,
                                max_pair_cost=MAX_PAIR_COST, live=True,
                                venue_positions={TOK_UP: 10.0, TOK_DN: 0.0})
    assert result["action"] == "exited"
    assert result["size"] == pytest.approx(10.0)

    sold = sum(float(c.split(":")[2])
               for c in venue.calls if c.startswith("sell:"))
    assert sold == pytest.approx(20.0), "sold exactly the 20 filled, no more"
    closes = [c for c in registry.get_all_closes()
              if c.get("method") == "single_buy_exit"]
    assert len(closes) == 2, "every filled share ends with a close"


def test_genuine_divergence_with_no_prior_exits_still_refuses(registry):
    """A short wallet with nothing exited on record is unexplained: refuse."""
    _ladder_market(registry, NOW)
    venue = FakeVenue(best_ask=0.40, best_bid=0.55)

    first = _resting_up(registry, 10.0, 0.60, NOW, "r1")
    _fill(registry, first, 10.0, 0.60, NOW)
    with pytest.raises(lp.PairExitRefused, match="diverge"):
        lp.exit_single_buy(venue, registry, PAIR,
                           max_pair_cost=MAX_PAIR_COST, live=True,
                           venue_positions={TOK_UP: 5.0, TOK_DN: 0.0})
    assert not any(c.startswith("sell:") for c in venue.calls)


def test_partially_explained_divergence_still_refuses(registry):
    """Priors explain 10 of a 15-share gap: the unexplained 5 still refuse."""
    _ladder_market(registry, NOW)
    venue = FakeVenue(best_ask=0.40, best_bid=0.55)

    first = _resting_up(registry, 10.0, 0.60, NOW, "r1")
    _fill(registry, first, 10.0, 0.60, NOW)
    lp.exit_single_buy(venue, registry, PAIR,
                       max_pair_cost=MAX_PAIR_COST, live=True,
                       venue_positions={TOK_UP: 10.0, TOK_DN: 0.0})

    second = _resting_up(registry, 10.0, 0.60, NOW + 1, "r2")
    _fill(registry, second, 10.0, 0.60, NOW + 1)
    # Truncated view: 5 more shares missing than the one exit accounts for.
    with pytest.raises(lp.PairExitRefused, match="diverge"):
        lp.exit_single_buy(venue, registry, PAIR,
                           max_pair_cost=MAX_PAIR_COST, live=True,
                           venue_positions={TOK_UP: 5.0, TOK_DN: 0.0})
    sells = [c for c in venue.calls if c.startswith("sell:")]
    assert len(sells) == 1


def test_manager_exits_the_refill_on_second_pass(registry, saver_clock):
    """Both passes route an old fill, and the refill sells exactly its 10.

    Pass 1 (3900s) exits the 1000s-old fill; the refill lands a second later
    under the same pair_id. Pass 2 (5000s) must sell just the refill: the
    registry believes 20 shares, the venue agrees to 10, and the one recorded
    exit explains the gap (#326). 20 shares sold across two closes, never 30.
    """
    # Timestamps are ms throughout; the manager's own routes own these fills
    # (an old fill, an open market inside the 900s lead, a bid of 0.57 that is
    # nowhere near the 0.15 stop line).
    _ladder_market(registry, 1_000_000)
    venue = FakeVenue(best_ask=0.40, best_bid=0.57)

    first = _resting_up(registry, 10.0, 0.60, 1_000_000, "r1")
    _fill(registry, first, 10.0, 0.60, 1_000_000)

    saver_clock["t"] = 3_900.0
    pass1 = _manager_pass(registry, venue, now_s=3_900.0, end_ts=4_500.0)
    assert pass1["action"] == "aged_out_rescue"
    assert pass1["route"] == "exited"

    second = _resting_up(registry, 10.0, 0.60, 3_901_000, "r2")
    _fill(registry, second, 10.0, 0.60, 3_901_000)

    saver_clock["t"] = 5_000.0
    pass2 = _manager_pass(registry, venue, now_s=5_000.0, end_ts=5_600.0)
    assert pass2["action"] == "aged_out_rescue"
    assert pass2["route"] == "exited"
    assert pass2["size"] == pytest.approx(10.0)

    sold = [float(c.split(":")[2])
            for c in venue.calls if c.startswith("sell:")]
    assert sold == [pytest.approx(10.0), pytest.approx(10.0)]
    assert sum(sold) == pytest.approx(20.0), "sold exactly the 20 filled, no more"
    closes = [c for c in registry.get_all_closes()
              if c.get("method") == "single_buy_exit"]
    assert len(closes) == 2, "every filled share ends with a close"


def test_fresh_fill_waits_patiently(registry):
    """A 1s-old fill is inside the window: the pass waits and sends nothing."""
    _ladder_market(registry, NOW)
    # Bid 0.57 keeps the leg out of escalation (drawdown 0.03 against a 0.04
    # threshold), so the pass has no route to take here.
    venue = FakeVenue(best_ask=0.40, best_bid=0.57)

    first = _resting_up(registry, 10.0, 0.60, NOW, "r1")
    _fill(registry, first, 10.0, 0.60, NOW)

    results = lp.manage_single_leg_positions(
        venue, registry, _cfg(), live=True, now=NOW / 1000.0 + 1.0,
        venue_positions={TOK_UP: 10.0, TOK_DN: 0.0},
    )

    assert len(results) == 1
    assert results[0]["action"] == "patient_wait"
    assert not any(c.startswith(("buy:", "sell:", "cancel:"))
                   for c in venue.calls)
