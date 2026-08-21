"""The U35 auto pass: live_loop converts in-window one-sided fills.

`auto_manage_pairs` runs once per poll cycle after reconcile. It discovers
pairs with fills from the ledger, gates them on the U35 window and the
close table, and routes each to complete (under the cap) or exit (at/over
the cap or no ask), mirroring the sim's sweep. These tests drive the pass
with a real tmp registry and a fake venue client -- no network.
"""
import uuid
from pathlib import Path
import pytest

from engine.config import MakerConfig
from engine.order_registry import (
    OrderRegistry, OrderRecord, FillRecord, CloseRecord, QuoteRecord,
)
from engine import live_pairs as lp
from engine.live_pairs import auto_manage_pairs


MAX_PAIR_COST = 0.995
TOK_UP = "tok-up"
TOK_DN = "tok-dn"
COND = "0xcond-u35"
NOW_S = 1_900.0        # 1900s -> venue_ts 1_000_000ms is exactly in the 900s window
FILL_TS_MS = 1_000_000


@pytest.fixture
def registry(tmp_path: Path) -> OrderRegistry:
    return OrderRegistry(db_path=tmp_path / "live.db")


def _cfg(**kw) -> MakerConfig:
    base = dict(enable_pairs_rule=True, pairs_exit_window_sec=900.0,
                max_pair_cost=MAX_PAIR_COST)
    base.update(kw)
    return MakerConfig(**base)


def _one_sided_pair(registry: OrderRegistry, filled_size: float = 10.0,
                    fill_price: float = 0.60, pair_id: str = "pair-1",
                    cond: str = COND, venue_ts: int = FILL_TS_MS) -> str:
    """A heavy UP leg fully filled, a light DOWN leg still resting."""
    now = 1_000_000

    heavy = OrderRecord(
        id=str(uuid.uuid4()), order_id=f"venue-heavy-{pair_id}",
        condition_id=cond, token_id=TOK_UP, side="BUY", price=fill_price,
        original_size=filled_size, status="filled",
        posted_ts=now, last_polled_ts=now, pair_id=pair_id,
        max_pair_cost_at_post=MAX_PAIR_COST,
    )
    registry.create_order(heavy)
    registry.record_fill(FillRecord(
        trade_id=f"trade-{pair_id}-h", order_uuid=heavy.id, size=filled_size,
        price=fill_price, venue_ts=venue_ts,
    ))

    light = OrderRecord(
        id=str(uuid.uuid4()), order_id=f"venue-light-{pair_id}",
        condition_id=cond, token_id=TOK_DN, side="BUY", price=0.38,
        original_size=filled_size, status="open",
        posted_ts=now, last_polled_ts=now, pair_id=pair_id,
        max_pair_cost_at_post=MAX_PAIR_COST,
    )
    registry.create_order(light)

    # Quotes ledger carries the UP/DOWN label the exit needs to encode the
    # sold leg in the closes table (which has no token column).
    registry.log_quote(QuoteRecord(
        ts=now / 1000.0, condition_id=cond, token_id=TOK_UP, side="UP",
        price=fill_price, size=filled_size,
    ))
    registry.log_quote(QuoteRecord(
        ts=now / 1000.0, condition_id=cond, token_id=TOK_DN, side="DOWN",
        price=0.38, size=filled_size,
    ))
    return pair_id


class FakeClient:
    """Records every venue call. No network."""

    def __init__(self, best_ask=0.40, best_bid=0.55, cancel_ok=True,
                 bid_depth=100.0, ask_depth=100.0, bid_levels=None,
                 venue_matched=None, get_order_ok=True, tick_size="0.01"):
        self.best_ask = best_ask
        self.best_bid = best_bid
        self.cancel_ok = cancel_ok
        self.bid_depth = bid_depth
        self.ask_depth = ask_depth
        self.bid_levels = bid_levels
        self.tick_size = tick_size
        self.venue_matched = dict(venue_matched or {})
        self.get_order_ok = get_order_ok
        self.calls: list[str] = []
        self.orders: list[dict] = []

    def get_order_book(self, token_id):
        self.calls.append(f"book:{token_id}")
        asks = ([] if self.best_ask is None
                else [{"price": str(self.best_ask), "size": str(self.ask_depth)}])
        bids = (self.bid_levels if self.bid_levels is not None
                else [{"price": str(self.best_bid), "size": str(self.bid_depth)}])
        return {"asset_id": token_id, "bids": bids, "asks": asks,
                "tick_size": self.tick_size}

    def get_order(self, order_id):
        self.calls.append(f"get_order:{order_id}")
        if not self.get_order_ok:
            raise RuntimeError("venue order read failed")
        return {"orderID": order_id,
                "size_matched": self.venue_matched.get(order_id, 0.0)}

    def cancel_order(self, payload):
        self.calls.append(f"cancel:{getattr(payload, 'orderID', payload)}")
        if not self.cancel_ok:
            raise RuntimeError("venue refused the cancel")
        return {"canceled": ["venue-light"]}

    def create_and_post_market_order(self, order_args, options=None,
                                     order_type="FOK", defer_exec=False):
        verb = "sell" if order_args.side == "SELL" else "buy"
        self.calls.append(
            f"{verb}:{order_args.token_id}:{order_args.amount}:{order_args.side}"
        )
        self.orders.append({
            "side": order_args.side, "token_id": order_args.token_id,
            "amount": order_args.amount,
            "price": getattr(order_args, "price", None),
        })
        return {"success": True, "orderID": f"venue-{verb}"}


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------

def test_disabled_rule_does_nothing(registry):
    _one_sided_pair(registry, fill_price=0.60)
    assert auto_manage_pairs(FakeClient(), registry, _cfg(enable_pairs_rule=False),
                             now=NOW_S) == []


def test_no_fills_returns_empty(registry):
    assert auto_manage_pairs(FakeClient(), registry, _cfg(), now=NOW_S) == []


def test_out_of_window_is_left_alone(registry):
    _one_sided_pair(registry, fill_price=0.60, venue_ts=1_000_000)
    # now = 3000s -> fill age 2000s > 900s window: left alone.
    assert auto_manage_pairs(FakeClient(), registry, _cfg(),
                             now=3_000.0) == []


def test_closed_condition_is_skipped(registry):
    pid = _one_sided_pair(registry, fill_price=0.60)
    registry.log_close(CloseRecord(
        ts=1_000_000, condition_id=COND, method="merge", shares=10.0,
        cost_basis=5.0, proceeds=5.0, realized_pnl=0.0, run_id="run-a"))
    assert auto_manage_pairs(FakeClient(), registry, _cfg(),
                             now=NOW_S) == []


def test_balanced_pair_is_skipped(registry):
    # Both legs filled equally -> naked == 0 -> the pass skips it.
    pid = _one_sided_pair(registry, fill_price=0.50)
    orders = registry.get_orders_by_pair(pid)
    light = [o for o in orders if o.token_id == TOK_DN][0]
    registry.record_fill(FillRecord(
        trade_id="trade-pair-1-l", order_uuid=light.id, size=10.0,
        price=0.38, venue_ts=FILL_TS_MS))
    assert auto_manage_pairs(FakeClient(), registry, _cfg(),
                             now=NOW_S) == []


# ---------------------------------------------------------------------------
# Routing: complete under the cap, exit at/over it
# ---------------------------------------------------------------------------

def test_in_window_under_cap_completes(registry):
    _one_sided_pair(registry, fill_price=0.50)  # 0.50 fill + 0.40 ask = 0.90
    client = FakeClient(best_ask=0.40)
    results = auto_manage_pairs(client, registry, _cfg(), now=NOW_S)
    assert len(results) == 1
    assert results[0]["action"] == "completed"
    assert results[0]["pair_id"] == "pair-1"
    assert any(c.startswith("buy:") for c in client.calls)


def test_at_cap_exits(registry):
    # 0.60 fill + 0.395 ask == 0.995 == cap: the exit owns the case.
    _one_sided_pair(registry, fill_price=0.60)
    client = FakeClient(best_ask=0.395)
    results = auto_manage_pairs(client, registry, _cfg(), now=NOW_S)
    assert len(results) == 1
    assert results[0]["action"] == "exited"
    assert any(c.startswith("sell:") for c in client.calls)


def test_no_ask_exits(registry):
    _one_sided_pair(registry, fill_price=0.60)
    client = FakeClient(best_ask=None)
    results = auto_manage_pairs(client, registry, _cfg(), now=NOW_S)
    assert len(results) == 1
    assert results[0]["action"] == "exited"


def test_dry_run_sends_nothing(registry):
    _one_sided_pair(registry, fill_price=0.50)
    client = FakeClient(best_ask=0.40)
    results = auto_manage_pairs(client, registry, _cfg(), live=False, now=NOW_S)
    assert results[0]["action"] == "would_complete"
    assert not any(c.startswith(("buy:", "sell:", "cancel:")) for c in client.calls)


def test_positions_read_failure_fails_closed(registry, monkeypatch):
    _one_sided_pair(registry, fill_price=0.50)

    def boom(funder):
        raise RuntimeError("data api down")

    monkeypatch.setattr(lp, "fetch_positions", boom)
    client = FakeClient(best_ask=0.40)
    results = auto_manage_pairs(client, registry, _cfg(),
                                live=True, funder="0xfunder", now=NOW_S)
    assert results == [{
        "pair_id": None, "action": "error",
        "error": "positions read failed: RuntimeError: data api down",
    }]
    assert client.calls == []  # nothing sent, nothing read


def test_one_bad_pair_does_not_stop_others(registry):
    # A healthy pair completes...
    _one_sided_pair(registry, fill_price=0.50, pair_id="pair-good")
    # ...while a 3-token "pair" refuses in load_pair.
    now = 1_000_000
    for tok, oid in (("tok-x", "vx"), ("tok-y", "vy"), ("tok-z", "vz")):
        registry.create_order(OrderRecord(
            id=str(uuid.uuid4()), order_id=oid, condition_id="0xcond-3leg",
            token_id=tok, side="BUY", price=0.5, original_size=10.0,
            status="filled", posted_ts=now, last_polled_ts=now,
            pair_id="pair-bad", max_pair_cost_at_post=MAX_PAIR_COST,
        ))
    bad_heavy = registry.get_orders_by_pair("pair-bad")[0]
    registry.record_fill(FillRecord(
        trade_id="trade-bad-h", order_uuid=bad_heavy.id, size=10.0,
        price=0.5, venue_ts=FILL_TS_MS))

    client = FakeClient(best_ask=0.40)
    results = auto_manage_pairs(client, registry, _cfg(), now=NOW_S)
    actions = {r["pair_id"]: r["action"] for r in results}
    assert actions.get("pair-good") == "completed"
    assert actions.get("pair-bad") == "error"
