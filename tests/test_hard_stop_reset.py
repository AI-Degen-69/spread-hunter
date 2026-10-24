"""Stop exits route through the shared guarded reset (#646).

The old block wrote `DUAL_RESTING` off an assumed-flat read (`venue_positions
is None` fell back to `requested - sold`, and the other leg read 0.0 through
a nonexistent attribute). Now only the proof resets: ledger and venue both
flat, or the row stays HARD_STOP under its manager. No network in any test.
"""

from __future__ import annotations

import json
import uuid

import pytest

from core_brain import single_buy_saver as lp
from core_brain.config import MakerConfig
from core_brain.market_resolution import MarketEndState
from core_brain.order_registry import (
    FillRecord,
    OrderRecord,
    OrderRegistry,
    QuoteRecord,
)

MAX_PAIR_COST = 0.995
TOK_UP = "tok-up"
TOK_DN = "tok-dn"
COND = "0xcond-stop-reset"
PAIR = "pair-stop-reset"
NOW = 2_000_000


@pytest.fixture
def registry(tmp_path):
    return OrderRegistry(db_path=tmp_path / "live.db")


class ProvingVenue:
    """Decision/execution books plus a venue that can prove flat.

    `open_orders` starts with both legs resting; every cancel removes its
    order, so a post-cancel read shows an empty book. Trades stay empty and
    balances come from the Data API double the test stages.
    """

    def __init__(self, decision_bid=0.15, execution_bid=0.14, depth=100.0):
        self.decision_bid = decision_bid
        self.execution_bid = execution_bid
        self.depth = depth
        self.creds = object()
        self.open_orders = [{"id": "venue-up"}, {"id": "venue-dn"}]
        self.trades: list[dict] = []
        self.calls: list[str] = []

    def _book(self, bid):
        bids = ([] if bid is None
                else [{"price": str(bid), "size": str(self.depth)}])
        return {"asset_id": "tok", "tick_size": "0.01",
                "asks": [{"price": "0.40", "size": str(self.depth)}],
                "bids": bids}

    def get_order_book(self, token_id):
        if token_id == TOK_DN:
            return self._book(None)
        cancelled = any(c.startswith("cancel:") for c in self.calls)
        return self._book(self.execution_bid if cancelled else self.decision_bid)

    def get_order(self, order_id):
        return {"orderID": order_id, "size_matched": 0.0}

    def get_open_orders(self):
        return list(self.open_orders)

    def get_trades(self, params=None):
        return list(self.trades)

    def cancel_order(self, payload):
        oid = str(getattr(payload, "orderID", payload))
        self.calls.append(f"cancel:{oid}")
        self.open_orders = [o for o in self.open_orders if o["id"] != oid]
        return {"canceled": True}

    def create_and_post_market_order(self, order_args, options=None,
                                     order_type="FOK", defer_exec=False):
        verb = "sell" if order_args.side == "SELL" else "buy"
        self.calls.append(f"{verb}:{order_args.token_id}:{order_args.amount}")
        return {"success": True, "orderID": f"venue-{verb}"}


class _Resp:
    def __init__(self, payload):
        self._data = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _balances(monkeypatch, payload):
    import urllib.request

    def _fake(req, timeout=None):
        return _Resp(payload)

    monkeypatch.setattr(urllib.request, "urlopen", _fake)


def _seed_pair(registry: OrderRegistry) -> None:
    down = OrderRecord(
        id=str(uuid.uuid4()), order_id="venue-dn", condition_id=COND,
        token_id=TOK_DN, side="BUY", price=0.38, original_size=10.0,
        status="open", posted_ts=NOW, last_polled_ts=NOW, pair_id=PAIR,
        max_pair_cost_at_post=MAX_PAIR_COST,
    )
    registry.create_order(down)
    up = OrderRecord(
        id=str(uuid.uuid4()), order_id="venue-up", condition_id=COND,
        token_id=TOK_UP, side="BUY", price=0.60, original_size=10.0,
        status="open", posted_ts=NOW, last_polled_ts=NOW, pair_id=PAIR,
        max_pair_cost_at_post=MAX_PAIR_COST,
    )
    registry.create_order(up)
    registry.record_fill(FillRecord(
        trade_id=f"trade-{up.id}", order_uuid=up.id, size=10.0,
        price=0.60, venue_ts=NOW))
    registry.update_order_status(up.id, status="filled", last_polled_ts=NOW)
    for tok, side, price in ((TOK_UP, "UP", 0.60), (TOK_DN, "DOWN", 0.38)):
        registry.log_quote(QuoteRecord(
            ts=NOW / 1000.0, condition_id=COND, token_id=tok, side=side,
            price=price, size=10.0))


def _cfg():
    return MakerConfig(
        bot_risk_gate_pairs_rule_on=True, bot_risk_gate_aged_out_rescue_on=True,
        bot_risk_pairs_exit_window_sec=900.0, bot_risk_aged_out_rescue_lead_sec=900.0,
        bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST, bot_risk_exposure_max_order_usd=100.0)


def _route(registry, venue, **kw):
    results = lp.manage_single_leg_positions(
        venue, registry, _cfg(), live=True, now=NOW / 1000.0 + 10.0,
        venue_positions={TOK_UP: 10.0, TOK_DN: 0.0},
        market_state_fn=lambda cid: MarketEndState(
            condition_id=COND, closed=False, end_ts=NOW / 1000.0 + 600.0,
            end_date_iso=None, end_date_passed=False, accepting_orders=True,
            resolved=False, unreachable=False),
        **kw)
    assert len(results) == 1
    return results[0]


def test_a_full_exit_with_a_fresh_zero_read_resets(registry, monkeypatch):
    """#646: flat ledger plus flat venue clears the row and marks awaiting."""
    _balances(monkeypatch, [])
    _seed_pair(registry)

    routed = _route(registry, ProvingVenue(), funder="0xfunder")

    assert routed["action"] == "exited"
    assert routed["reset_status"] == "RESET"
    assert routed["lifecycle_state"] is None
    assert registry.get_lifecycle_state(PAIR) is None
    assert registry.requalification_marks()[COND].status == "awaiting"


def test_a_full_exit_with_an_unreadable_balance_does_not_reset(
        registry, monkeypatch):
    """#646: the old fallback assumed flat here; the proof refuses instead.

    #796: the row stays managed AND fresh entries stay blocked -- the
    unreadable venue cannot prove flat, but the exit still happened.
    """
    import urllib.request

    def _boom(req, timeout=None):
        raise ConnectionError("Data API offline")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    _seed_pair(registry)

    routed = _route(registry, ProvingVenue(), funder="0xfunder")

    assert routed["action"] == "exited"
    assert routed["reset_status"] == "UNAVAILABLE"
    assert registry.get_lifecycle_state(PAIR).state == "HARD_STOP"
    mark = registry.requalification_marks()[COND]
    assert mark.status == "awaiting"
    assert "hard_stop" in mark.reason


def test_an_exited_result_with_a_nonzero_balance_does_not_reset(
        registry, monkeypatch):
    """#646: shares the ledger forgot still count, even after a full sale."""
    _balances(monkeypatch, [{"asset": TOK_UP, "size": 5.0}])
    _seed_pair(registry)

    routed = _route(registry, ProvingVenue(), funder="0xfunder")

    assert routed["action"] == "exited"
    assert routed["reset_status"] == "RESIDUAL"
    assert registry.get_lifecycle_state(PAIR).state == "HARD_STOP"
    # #796: residual keeps the row managed but still blocks fresh entries.
    mark = registry.requalification_marks()[COND]
    assert mark.status == "awaiting"
    assert "hard_stop" in mark.reason
