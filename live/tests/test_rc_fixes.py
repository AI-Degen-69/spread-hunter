"""Regression tests for the three live-tree fixes.

RC-1 — an exit is RECORDED. `exit_naked_leg` used to sell at the venue and
return, writing nothing to the registry. The next cycle's auto pass saw the
same naked pair, sold it again, and the repeat-sell loop ran until the venue
positions check finally diverged. The exit now writes a `naked_exit` close
(which leg, cost basis, proceeds, realized PnL), inventory math subtracts it,
and the auto pass skips fills that predate a close on the condition.

RC-2 — the pair-cost cap fires on the LIGHT side. R4 exempts the light side
from the exposure arms because buying it REDUCES exposure -- but the pair-cost
arm is not an exposure bound, and a light-side fill that assembles the pair
at/over `max_pair_cost` is a booked loss on an instrument that pays $1.00.

RC-3 — no lone HEAVY-side leg. `_require_two_sided` refused lone intents only
when inventory was flat; with unbalanced inventory a lone intent on the heavy
side deepened the imbalance and was allowed through. It is now refused too.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from engine import live_pairs as lp
from engine import risk
from engine.config import MakerConfig
from engine.order_registry import (
    CloseRecord, FillRecord, OrderRecord, OrderRegistry, QuoteRecord,
)
from engine.quotes import Inventory, decide_quotes
from engine.live_pairs import auto_manage_pairs

MAX_PAIR_COST = 0.995
TOK_UP = "tok-up"
TOK_DN = "tok-dn"
COND = "0xcond-rc"
NOW_S = 1_900.0
FILL_TS_MS = 1_000_000


@pytest.fixture
def registry(tmp_path: Path) -> OrderRegistry:
    return OrderRegistry(db_path=tmp_path / "live.db")


class FakeClient:
    """Records every venue call. No network. Mirrors tests/test_auto_pairs.py."""

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
    registry.log_quote(QuoteRecord(
        ts=now / 1000.0, condition_id=cond, token_id=TOK_UP, side="UP",
        price=fill_price, size=filled_size,
    ))
    registry.log_quote(QuoteRecord(
        ts=now / 1000.0, condition_id=cond, token_id=TOK_DN, side="DOWN",
        price=0.38, size=filled_size,
    ))
    return pair_id


# ---------------------------------------------------------------------------
# RC-1 — the exit is recorded, so a sold pair can never be re-exited
# ---------------------------------------------------------------------------

def test_exit_writes_a_naked_exit_close(registry: OrderRegistry):
    """After a real exit the registry holds a close that names the sold leg.

    Without this row the pair reads as still-held next cycle and the auto pass
    sells it again -- the repeat-sell loop observed in production.
    """
    pair_id = _one_sided_pair(registry, filled_size=10.0, fill_price=0.60)
    client = FakeClient(best_ask=0.40)

    result = lp.exit_naked_leg(client, registry, pair_id,
                               max_pair_cost=MAX_PAIR_COST, live=True)
    assert result["action"] == "exited"
    assert result["side"] == "UP"          # the heavy leg is the one sold

    closes = registry.get_all_closes()
    assert len(closes) == 1
    c = closes[0]
    assert c["method"] == "naked_exit"
    assert c["condition_id"] == COND
    assert c["shares"] == pytest.approx(10.0)
    # UP leg sold: up_price carries the sale, dn_price stays None.
    assert c["up_price"] is not None
    assert c["dn_price"] is None
    # Cost basis is 10 @ 0.60; proceeds are the worst accepted price.
    assert c["cost_basis"] == pytest.approx(6.0)
    assert c["up_cost_removed"] == pytest.approx(6.0)
    assert c["dn_cost_removed"] == pytest.approx(0.0)


def test_exit_refuses_when_unpriced_heavy_shares_exist(registry: OrderRegistry):
    """A venue fill the registry has not priced defers the exit, not sells.

    The close's cost basis extrapolates the registry's average heavy price
    onto every sold share, and the sold size already counts venue-reported
    heavy matched. Selling while the venue reports shares with no registry
    price would record a fabricated average -- the same refusal complete_pair
    makes in this state. Deferring lets reconcile price the fill first.
    """
    now = 1_000_000
    heavy = OrderRecord(
        id=str(uuid.uuid4()), order_id="venue-heavy-unpriced",
        condition_id=COND, token_id=TOK_UP, side="BUY", price=0.60,
        original_size=10.0, status="partial",
        posted_ts=now, last_polled_ts=now, pair_id="pair-1",
        max_pair_cost_at_post=MAX_PAIR_COST,
    )
    registry.create_order(heavy)
    registry.record_fill(FillRecord(
        trade_id="trade-unpriced-h", order_uuid=heavy.id, size=5.0,
        price=0.60, venue_ts=FILL_TS_MS,
    ))
    light = OrderRecord(
        id=str(uuid.uuid4()), order_id="venue-light-unpriced",
        condition_id=COND, token_id=TOK_DN, side="BUY", price=0.38,
        original_size=10.0, status="open",
        posted_ts=now, last_polled_ts=now, pair_id="pair-1",
        max_pair_cost_at_post=MAX_PAIR_COST,
    )
    registry.create_order(light)
    registry.log_quote(QuoteRecord(
        ts=now / 1000.0, condition_id=COND, token_id=TOK_UP, side="UP",
        price=0.60, size=10.0,
    ))
    registry.log_quote(QuoteRecord(
        ts=now / 1000.0, condition_id=COND, token_id=TOK_DN, side="DOWN",
        price=0.38, size=10.0,
    ))

    # The venue reports the working 5 as already matched: 5 unpriced shares.
    client = FakeClient(best_ask=0.40,
                        venue_matched={"venue-heavy-unpriced": 10.0})

    with pytest.raises(lp.PairExitRefused, match="more shares"):
        lp.exit_naked_leg(client, registry, "pair-1",
                          max_pair_cost=MAX_PAIR_COST, live=True)

    # The defer is a ledger no-op: nothing sold, no close written.
    assert client.orders == []
    assert registry.get_all_closes() == []


def test_exit_close_is_subtracted_from_inventory(registry: OrderRegistry):
    """inventory_from_registry must see the sold leg leave the position.

    The dashboard's UP/DN SHARES and PAIR COST columns read the registry; a
    close that exists but is ignored would keep showing the phantom position.
    """
    from engine.order_registry import inventory_from_registry

    _one_sided_pair(registry, filled_size=10.0, fill_price=0.60)
    lp.exit_naked_leg(FakeClient(best_ask=0.40), registry, "pair-1",
                      max_pair_cost=MAX_PAIR_COST, live=True)

    inv = inventory_from_registry(COND, TOK_UP, TOK_DN, db_path=registry.db_path)
    assert inv.up_shares == pytest.approx(0.0)
    assert inv.down_shares == pytest.approx(0.0)
    assert inv.up_cost == pytest.approx(0.0)


def test_auto_pass_does_not_re_exit_after_the_close(registry: OrderRegistry):
    """The regression test for the repeat-sell loop itself.

    Cycle 1 exits and records the close. Cycle 2 must find nothing to do: the
    fill is older than the condition's close, so the pair is skipped instead
    of sold again.
    """
    pair_id = _one_sided_pair(registry, filled_size=10.0, fill_price=0.60)
    client = FakeClient(best_ask=0.40)

    out1 = auto_manage_pairs(client, registry, _cfg(), now=NOW_S)
    assert [r["action"] for r in out1] == ["exited"]
    sells_after_first = sum(c.startswith("sell:") for c in client.calls)
    assert sells_after_first == 1

    # Second cycle: same registry, no new fills. The close covers the fill.
    out2 = auto_manage_pairs(client, registry, _cfg(), now=NOW_S + 5.0)
    assert out2 == []
    sells_total = sum(c.startswith("sell:") for c in client.calls)
    assert sells_total == 1


def test_a_fill_after_the_close_re_arms_the_rule(registry: OrderRegistry):
    """A close covers only fills that predate it.

    If the fleet re-quotes the same condition and a NEW fill lands after the
    exit, that new exposure must be managed like any other -- the old
    condition-level skip would have frozen the rule for the condition forever.
    """
    _one_sided_pair(registry, filled_size=10.0, fill_price=0.60, pair_id="pair-1")
    lp.exit_naked_leg(FakeClient(best_ask=0.40), registry, "pair-1",
                      max_pair_cost=MAX_PAIR_COST, live=True)

    # A second pair on the SAME condition, filled AFTER the close. The close
    # row's ts is time.time() (real now); the new fill's venue_ts must sit
    # after it for the skip logic to treat it as new exposure.
    import time as _time
    after_close_ms = int(_time.time() * 1000) + 60_000
    _one_sided_pair(registry, filled_size=10.0, fill_price=0.60, pair_id="pair-2",
                    venue_ts=after_close_ms)

    client = FakeClient(best_ask=0.40)
    out = auto_manage_pairs(client, registry, _cfg(),
                            now=(after_close_ms / 1000.0) + 60.0)
    assert [r["action"] for r in out] == ["exited"]


def test_exit_refuses_when_the_sold_leg_side_is_unresolvable(
    registry: OrderRegistry,
):
    """A sell that cannot be recorded must not be sent.

    The closes table encodes the sold leg via up_price/dn_price -- no token
    column -- so a token with no quote-side entry would produce an exit the
    registry could never learn about. Fail closed before any venue write.
    """
    pair_id = _one_sided_pair(registry)
    # Remove the quotes ledger rows: simulate a pair quoted before side
    # logging existed, or by a path that never logged quotes.
    with registry._conn() as conn:
        conn.execute("DELETE FROM quotes")
        conn.commit()
    client = FakeClient(best_ask=0.40)

    with pytest.raises(lp.PairExitRefused, match="UP or DOWN leg"):
        lp.exit_naked_leg(client, registry, pair_id,
                          max_pair_cost=MAX_PAIR_COST, live=True)
    assert not any(c.startswith("cancel:") for c in client.calls)
    assert not any(c.startswith("sell:") for c in client.calls)


# ---------------------------------------------------------------------------
# RC-2 — the pair-cost cap fires on the LIGHT side
# ---------------------------------------------------------------------------

def _healthy_book(bid, ask, depth=9999.0):
    return {"best_bid": bid, "best_ask": ask,
            "bids": {bid: depth}, "asks": {ask: depth},
            "token_id": "tok-x", "tick_size": "0.01"}


def test_hard_block_pair_cost_fires_on_the_light_side():
    """Quoting the light side at a price that assembles the pair at/over the
    cap must be blocked -- R4's light-side exemption is for exposure arms only.

    Production failure: light-side UP quoted at 0.92 against a held DOWN leg
    at 0.20 avg -> pair $1.12, a booked loss, and the cap never fired because
    R4 returned before the pair-cost arm ran.
    """
    cfg = MakerConfig(enable_hard_blocks=True, max_pair_cost=0.995,
                      max_naked_usd=100.0, enforce_price_band=False)
    inv = Inventory(up_shares=0.0, down_shares=6.0,
                    up_cost=0.0, down_cost=6.0 * 0.20)

    up_book = _healthy_book(0.88, 0.92)
    down_book = _healthy_book(0.19, 0.21)

    why = risk.hard_block(cfg, inv, "UP", price=0.92, own_book=up_book,
                          hedge_book=down_book)
    assert why is not None
    assert "cap" in why
    assert "1.12" in why.replace(" ", "").replace("$", "") or "1.120" in why


def test_hard_block_light_side_under_the_cap_is_allowed():
    """The same light-side quote one tick under the cap still rests.

    R4's exemption survives for exposure: buying the light side REDUCES
    exposure, and the pair-cost arm only fires when the pair is a booked loss.
    """
    cfg = MakerConfig(enable_hard_blocks=True, max_pair_cost=0.995,
                      max_naked_usd=100.0, enforce_price_band=False)
    inv = Inventory(up_shares=0.0, down_shares=6.0,
                    up_cost=0.0, down_cost=6.0 * 0.20)

    up_book = _healthy_book(0.75, 0.79)
    down_book = _healthy_book(0.19, 0.21)

    why = risk.hard_block(cfg, inv, "UP", price=0.79, own_book=up_book,
                          hedge_book=down_book)
    assert why is None


def test_hard_block_heavy_side_pair_cost_still_fires():
    """The heavy side keeps its existing pair-cost bound (and its reason)."""
    cfg = MakerConfig(enable_hard_blocks=True, max_pair_cost=0.995,
                      max_naked_usd=100.0, enforce_price_band=False)
    inv = Inventory(up_shares=6.0, down_shares=2.0,
                    up_cost=6.0 * 0.80, down_cost=2.0 * 0.20)

    up_book = _healthy_book(0.78, 0.82)
    down_book = _healthy_book(0.18, 0.22)

    # Heavy side is UP (6 > 2). Quoting UP at 0.82 against DOWN avg 0.20
    # assembles 1.02 >= 0.995.
    why = risk.hard_block(cfg, inv, "UP", price=0.82, own_book=up_book,
                          hedge_book=down_book)
    assert why is not None
    assert "cap" in why


# ---------------------------------------------------------------------------
# RC-3 — a lone HEAVY-side leg is refused
# ---------------------------------------------------------------------------

def test_flat_inventory_refuses_a_lone_leg():
    """Unchanged behavior: flat book + lone intent = no quote (existing rule)."""
    from engine.quotes import _require_two_sided

    cfg = MakerConfig(require_two_sided_when_flat=True)
    flat = Inventory(up_shares=0, down_shares=0, up_cost=0.0, down_cost=0.0)
    up = {"best_bid": 0.11, "best_ask": 0.12,
          "bids": {0.11: 9999.0}, "asks": {0.12: 9999.0}}
    down = {"best_bid": 0.87, "best_ask": 0.88,
            "bids": {0.87: 9999.0}, "asks": {0.88: 9999.0}}

    intents, why = decide_quotes(cfg, up, down, flat, 1e9, None)
    assert intents == []
    assert "lone resting leg" in why


def test_unbalanced_lone_heavy_side_is_refused():
    """A lone intent on the side we already over-hold deepens the imbalance.

    Only the light side may rest alone (it reduces exposure). The heavy side
    alone is a directional bet on the wrong direction -- refused exactly like
    the flat case.

    The realistic route here is the RC-2 interaction: UP-heavy inventory, and
    the DOWN (light) quote is blocked by the pair-cost cap because
    down_quote + avg(UP) >= max_pair_cost. Only UP (heavy) remains quotable,
    and a lone UP intent would deepen the position -- refused.
    """
    cfg = MakerConfig(require_two_sided_when_flat=True,
                      objective="rewards", quote_shares=120,
                      min_quote_shares=5, max_pair_cost=0.995,
                      enable_emergency_hedge=False, max_naked_usd=0.0)
    # UP-heavy at a 0.60 average: 40 UP held, none DOWN.
    naked = Inventory(up_shares=40, down_shares=0,
                      up_cost=24.0, down_cost=0.0)
    # Healthy two-sided books (so the heavy side's hedge check passes), but
    # the DOWN quote at ~0.41 assembles 0.41 + 0.60 = 1.01 >= 0.995.
    up = {"best_bid": 0.55, "best_ask": 0.56,
          "bids": {0.55: 9999.0}, "asks": {0.56: 9999.0}}
    down = {"best_bid": 0.43, "best_ask": 0.44,
            "bids": {0.43: 9999.0}, "asks": {0.44: 9999.0}}

    intents, why = decide_quotes(cfg, up, down, naked, 1e9, None)
    assert intents == []
    assert "deepens the imbalance" in why


def test_unbalanced_lone_light_side_is_allowed():
    """The light side alone still rests: it flattens the position."""
    cfg = MakerConfig(require_two_sided_when_flat=True)
    naked = Inventory(up_shares=40, down_shares=0,
                      up_cost=6.0, down_cost=0.0)
    up = {"best_bid": 0.11, "best_ask": 0.12,
          "bids": {0.11: 9999.0}, "asks": {0.12: 9999.0}}
    down = {"best_bid": 0.87, "best_ask": 0.88,
            "bids": {0.87: 9999.0}, "asks": {0.88: 9999.0}}

    intents, why = decide_quotes(cfg, up, down, naked, 1e9, None)
    assert [i.side for i in intents] == ["DOWN"]
    assert why == ""
