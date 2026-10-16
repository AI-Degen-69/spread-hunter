"""TDD tests for the entry-relative stop in the poll path (single_buy_saver).

#619 replaced a static $0.15 bid stop -- which liquidated every 0.10-0.14 entry
the moment it filled -- with a trigger derived from the held leg's own average
fill:

1. `NONE` (the shipped default): the universal emergency floor alone protects
   the naked leg, at `entry * (1 - emergency_drawdown_pct/100)` and never below
   $0.01.
2. `ABSOLUTE_DROP` / `PERCENTAGE_DROP`: one optional dynamic mode at a time.
3. The active trigger is the HIGHER of the mode's trigger and the floor, and the
   poll sells the whole naked leg when the held bid reaches it.
4. Elapsed time alone never exits a leg: patience is `pairs_exit_window_sec`,
   not the retired grace knob.
5. A live stop exit is an explicit fill-or-kill SELL.
"""
import uuid
from pathlib import Path
import pytest

from core_brain.config import MakerConfig
from core_brain.order_registry import (
    OrderRegistry, OrderRecord, FillRecord, QuoteRecord,
)
from core_brain.single_buy_saver import MIN_ORDER_SHARES, auto_manage_pairs


MAX_PAIR_COST = 0.995
TOK_UP = "tok-up"
TOK_DN = "tok-dn"
COND = "0xcond-dual-sl"
FILL_TS_MS = 1_000_000_000  # 1,000,000.000 s

# A call that states no order type is distinguishable from one that states FOK,
# because the stub's default is deliberately NOT the SDK's own "FOK" default.
UNSET_ORDER_TYPE = "<unset>"


@pytest.fixture
def registry(tmp_path: Path) -> OrderRegistry:
    return OrderRegistry(db_path=tmp_path / "live.db")


def _cfg(**kw) -> MakerConfig:
    base = dict(
        enable_pairs_rule=True,
        pairs_exit_window_sec=900.0,
        max_pair_cost=MAX_PAIR_COST,
    )
    base.update(kw)
    return MakerConfig(**base)


def _one_sided_pair(registry: OrderRegistry, filled_size: float = 5.0,
                    fill_price: float = 0.60, pair_id: str = "pair-sl",
                    cond: str = COND, venue_ts: int = FILL_TS_MS) -> str:
    heavy = OrderRecord(
        id=str(uuid.uuid4()), order_id=f"venue-heavy-{pair_id}",
        condition_id=cond, token_id=TOK_UP, side="BUY", price=fill_price,
        original_size=filled_size, status="filled",
        posted_ts=venue_ts / 1000.0, last_polled_ts=venue_ts / 1000.0, pair_id=pair_id,
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
        posted_ts=venue_ts / 1000.0, last_polled_ts=venue_ts / 1000.0, pair_id=pair_id,
        max_pair_cost_at_post=MAX_PAIR_COST,
    )
    registry.create_order(light)

    registry.log_quote(QuoteRecord(
        ts=venue_ts / 1000.0, condition_id=cond, token_id=TOK_UP, side="UP",
        price=fill_price, size=filled_size,
    ))
    registry.log_quote(QuoteRecord(
        ts=venue_ts / 1000.0, condition_id=cond, token_id=TOK_DN, side="DOWN",
        price=0.38, size=filled_size,
    ))
    return pair_id


class FakeClient:
    """A one-level book plus a recording venue stub (the LIVE branch).

    Deliberately without `_sell_into_bids`, so `exit_single_buy` takes its live
    path. `create_and_post_market_order`'s `order_type` default is a sentinel
    rather than the SDK's own `"FOK"`: a caller that lets the library pick the
    execution instruction must be distinguishable from one that states it.
    """

    def __init__(self, best_ask=0.42, best_bid=0.58, bid_size=100.0,
                 cancel_ok=True):
        self.best_ask = best_ask
        self.best_bid = best_bid
        self.bid_size = bid_size
        self.cancel_ok = cancel_ok
        self.calls: list[str] = []
        self.order_types: list[str] = []
        self.sells: list = []

    def get_order_book(self, token_id):
        self.calls.append(f"book:{token_id}")
        asks = ([{"price": str(self.best_ask), "size": str(self.bid_size)}]
                if self.best_ask is not None else [])
        bids = ([{"price": str(self.best_bid), "size": str(self.bid_size)}]
                if self.best_bid is not None else [])
        return {"asset_id": token_id, "bids": bids, "asks": asks,
                "tick_size": "0.01"}

    def get_order(self, order_id):
        return {"orderID": order_id, "size_matched": 0.0}

    def cancel_order(self, payload):
        return {"canceled": ["venue-light"]}

    def create_and_post_market_order(self, order_args, options=None,
                                     order_type=UNSET_ORDER_TYPE,
                                     defer_exec=False):
        verb = "sell" if order_args.side == "SELL" else "buy"
        self.calls.append(f"{verb}:{order_args.token_id}:{order_args.amount}")
        self.order_types.append(order_type)
        if verb == "sell":
            self.sells.append(order_args)
        return {"success": True, "orderID": f"venue-{verb}"}

    @property
    def sell_calls(self) -> list[str]:
        return [c for c in self.calls if c.startswith("sell:")]


def _stop_exit(result: dict) -> bool:
    return result.get("route") == "hard_stop"


# --- NONE: the emergency floor alone ---------------------------------------

@pytest.mark.parametrize(
    ("best_bid", "expect_stop"),
    [(0.031, False), (0.03, True), (0.029, True)],
)
def test_none_mode_cuts_a_ten_cent_entry_at_its_own_floor(
    registry, best_bid, expect_stop,
):
    """Entry 0.10 at the 75% default floor is 0.025, rounded UP to 0.03.

    The retired rule sold on fill at any bid <= 0.15, and the strategy quotes
    down to 0.10 -- so every low-range entry was a booked loss by construction.
    """
    _one_sided_pair(registry, fill_price=0.10)
    now_s = (FILL_TS_MS / 1000.0) + 5.0
    client = FakeClient(best_ask=0.12, best_bid=best_bid)

    results = auto_manage_pairs(client, registry, _cfg(), now=now_s)

    assert len(results) == 1
    assert _stop_exit(results[0]) is expect_stop
    if expect_stop:
        assert client.sell_calls == [f"sell:{TOK_UP}:5.0"]
        assert results[0]["lifecycle_state"] == "HARD_STOP"
        assert results[0]["reason"] == (
            f"held bid {best_bid:.4f} is at or below the emergency "
            "trigger 0.0300"
        )
    else:
        assert client.sell_calls == []
        assert results[0]["action"] == "escalated_wait"


@pytest.mark.parametrize(
    ("fill_price", "best_bid"), [(0.10, 0.10), (0.12, 0.14), (0.14, 0.15)]
)
def test_low_range_entries_are_not_liquidated_on_fill(
    registry, fill_price, best_bid,
):
    """The original bug: a 0.10-0.14 fill under the old static $0.15 stop."""
    _one_sided_pair(registry, fill_price=fill_price)
    now_s = (FILL_TS_MS / 1000.0) + 5.0
    client = FakeClient(best_ask=0.16, best_bid=best_bid)

    results = auto_manage_pairs(client, registry, _cfg(), now=now_s)

    assert not _stop_exit(results[0])
    assert client.sell_calls == []
    assert registry.get_all_closes() == []


def test_the_floor_is_never_below_one_cent(registry):
    """A $0.02 entry at a 100% emergency drawdown still cuts at $0.01."""
    _one_sided_pair(registry, fill_price=0.02)
    now_s = (FILL_TS_MS / 1000.0) + 5.0
    client = FakeClient(best_ask=0.03, best_bid=0.01)

    results = auto_manage_pairs(
        client, registry, _cfg(emergency_drawdown_pct=100.0), now=now_s,
    )

    assert _stop_exit(results[0])
    assert results[0]["reason"].endswith("emergency trigger 0.0100")


# --- the two dynamic modes, and the higher-of rule -------------------------

def test_percentage_mode_cuts_at_the_drawdown_not_the_floor(registry):
    """Entry 0.60 - 10% = 0.54, well above the 0.15 floor."""
    _one_sided_pair(registry, fill_price=0.60)
    now_s = (FILL_TS_MS / 1000.0) + 5.0
    client = FakeClient(best_ask=0.56, best_bid=0.54)

    results = auto_manage_pairs(
        client, registry,
        _cfg(stop_loss_mode="PERCENTAGE_DROP", drawdown_pct=10.0),
        now=now_s,
    )

    assert _stop_exit(results[0])
    assert results[0]["reason"].startswith(
        "held bid 0.5400 is at or below the strategy trigger 0.5400")
    assert client.sell_calls == [f"sell:{TOK_UP}:5.0"]


def test_percentage_mode_does_not_cut_above_its_trigger(registry):
    _one_sided_pair(registry, fill_price=0.60)
    now_s = (FILL_TS_MS / 1000.0) + 5.0
    client = FakeClient(best_ask=0.58, best_bid=0.55)

    results = auto_manage_pairs(
        client, registry,
        _cfg(stop_loss_mode="PERCENTAGE_DROP", drawdown_pct=10.0),
        now=now_s,
    )

    assert not _stop_exit(results[0])
    assert client.sell_calls == []


def test_absolute_mode_cuts_a_fixed_cent_drop(registry):
    """Entry 0.50 - $0.05 = 0.45, above the 0.125 floor."""
    _one_sided_pair(registry, fill_price=0.50)
    now_s = (FILL_TS_MS / 1000.0) + 5.0
    client = FakeClient(best_ask=0.47, best_bid=0.45)

    results = auto_manage_pairs(
        client, registry,
        _cfg(stop_loss_mode="ABSOLUTE_DROP", drop_cents=0.05),
        now=now_s,
    )

    assert _stop_exit(results[0])
    assert "strategy trigger 0.4500" in results[0]["reason"]


def test_the_floor_wins_when_the_mode_trigger_is_below_it(registry):
    """Entry 0.50 - $0.90 is negative: the emergency floor is the live rule."""
    _one_sided_pair(registry, fill_price=0.50)
    now_s = (FILL_TS_MS / 1000.0) + 5.0
    client = FakeClient(best_ask=0.14, best_bid=0.125)

    results = auto_manage_pairs(
        client, registry,
        _cfg(stop_loss_mode="ABSOLUTE_DROP", drop_cents=0.90),
        now=now_s,
    )

    assert _stop_exit(results[0])
    assert "emergency trigger 0.1300" in results[0]["reason"]
    assert "strategy" not in results[0]["reason"]


def test_the_floor_does_not_cut_below_its_own_level(registry):
    """With the floor as the live rule, one tick above it must not sell."""
    _one_sided_pair(registry, fill_price=0.50)
    now_s = (FILL_TS_MS / 1000.0) + 5.0
    client = FakeClient(best_ask=0.15, best_bid=0.14)

    results = auto_manage_pairs(
        client, registry,
        _cfg(stop_loss_mode="ABSOLUTE_DROP", drop_cents=0.90),
        now=now_s,
    )

    assert not _stop_exit(results[0])
    assert client.sell_calls == []


# --- the exit itself ------------------------------------------------------

def test_a_live_stop_exit_is_an_explicit_fill_or_kill_sell(registry):
    """The execution instruction is stated, not inherited from the SDK."""
    _one_sided_pair(registry, fill_price=0.60)
    now_s = (FILL_TS_MS / 1000.0) + 5.0
    client = FakeClient(best_ask=0.56, best_bid=0.54)

    results = auto_manage_pairs(
        client, registry,
        _cfg(stop_loss_mode="PERCENTAGE_DROP", drawdown_pct=10.0),
        now=now_s,
    )

    assert _stop_exit(results[0])
    assert client.order_types == ["FOK"], (
        "the live stop must send FOK itself rather than rely on the library's "
        "default order type"
    )


class ShadowStopClient(FakeClient):
    """The rehearsal client: presence of `_sell_into_bids` selects its branch."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.reasons: list = []

    def _sell_into_bids(self, *args, **kwargs):
        """Marker only -- never called; presence selects the shadow branch."""

    def create_and_post_market_order(self, order_args, *, reason=None):
        """Signature copied from `core_brain.shadow_exec`: reason and nothing
        else, so an explicit order type reaching the rehearsal is a TypeError
        rather than a silently ignored kwarg."""
        self.reasons.append(reason)
        super().create_and_post_market_order(order_args)
        return {"success": True, "orderID": "venue-shadow-sell",
                "price": self.best_bid, "size": order_args.amount}


def test_the_shadow_branch_keeps_its_own_exit_call(registry):
    """The rehearsal passes its exit reason and no explicit order type."""
    _one_sided_pair(registry, fill_price=0.60)
    now_s = (FILL_TS_MS / 1000.0) + 5.0
    client = ShadowStopClient(best_ask=0.56, best_bid=0.54)

    results = auto_manage_pairs(
        client, registry,
        _cfg(stop_loss_mode="PERCENTAGE_DROP", drawdown_pct=10.0),
        now=now_s,
    )

    assert _stop_exit(results[0])
    assert client.reasons == ["lifecycle_hard_stop"]
    assert client.order_types == [UNSET_ORDER_TYPE]


def test_a_stop_exit_books_its_own_close_with_the_decision_bid(registry):
    _one_sided_pair(registry, fill_price=0.60)
    now_s = (FILL_TS_MS / 1000.0) + 5.0
    client = FakeClient(best_ask=0.56, best_bid=0.54)

    auto_manage_pairs(
        client, registry,
        _cfg(stop_loss_mode="PERCENTAGE_DROP", drawdown_pct=10.0),
        now=now_s,
    )

    closes = [c for c in registry.get_all_closes()
              if c.get("method") == "single_buy_exit"]
    assert len(closes) == 1
    assert closes[0]["reason"] == "lifecycle_hard_stop"
    assert closes[0]["trigger_bid"] == pytest.approx(0.54)
    assert closes[0]["fresh_bid"] == pytest.approx(0.54)


def test_a_stop_sells_the_whole_naked_leg_and_never_buys(registry):
    """Sized by the registry, capped by depth at or above the accepted floor."""
    _one_sided_pair(registry, filled_size=7.0, fill_price=0.60)
    now_s = (FILL_TS_MS / 1000.0) + 5.0
    client = FakeClient(best_ask=0.56, best_bid=0.54, bid_size=25.0)

    results = auto_manage_pairs(
        client, registry,
        _cfg(stop_loss_mode="PERCENTAGE_DROP", drawdown_pct=10.0),
        now=now_s,
    )

    assert _stop_exit(results[0])
    assert client.sells[0].amount == pytest.approx(7.0)
    assert client.sells[0].side == "SELL"
    assert not any(c.startswith("buy:") for c in client.calls)


def test_a_book_too_thin_to_hold_the_leg_refuses_the_stop(registry):
    """Depth below the venue minimum is a refusal, never a partial exit."""
    _one_sided_pair(registry, filled_size=5.0, fill_price=0.60)
    now_s = (FILL_TS_MS / 1000.0) + 5.0
    client = FakeClient(best_ask=0.56, best_bid=0.54,
                        bid_size=MIN_ORDER_SHARES / 2.0)

    results = auto_manage_pairs(
        client, registry,
        _cfg(stop_loss_mode="PERCENTAGE_DROP", drawdown_pct=10.0),
        now=now_s,
    )

    assert client.sell_calls == []
    assert registry.get_all_closes() == []
    assert results[0]["action"] == "error"
    assert "venue minimum" in results[0]["error"]


# --- patience, drift, and the profitable ask, all under NONE --------------

def test_fresh_fill_within_the_window_holds_maker_bid(registry):
    """A stable single leg waits for its resting hedge without a taker buy."""
    _one_sided_pair(registry, fill_price=0.60)
    # now is 10s after fill
    now_s = (FILL_TS_MS / 1000.0) + 10.0
    client = FakeClient(best_ask=0.42, best_bid=0.58)
    results = auto_manage_pairs(client, registry, _cfg(), now=now_s)

    assert len(results) == 1
    assert results[0]["action"] == "patient_wait"
    assert not any(c.startswith("sell:") for c in client.calls)
    assert not any(c.startswith("buy:") for c in client.calls)


def test_adverse_drift_escalates_the_resting_hedge(registry):
    """A falling held-leg bid escalates the opposite maker quote, not a sell."""
    _one_sided_pair(registry, fill_price=0.60)
    now_s = (FILL_TS_MS / 1000.0) + 5.0
    # best_bid collapsed from 0.60 to 0.52 (loss of 0.08 > 0.045 max loss)
    client = FakeClient(best_ask=0.45, best_bid=0.52)
    results = auto_manage_pairs(client, registry, _cfg(), now=now_s)

    assert len(results) == 1
    assert results[0]["action"] == "escalated_wait"
    assert results[0]["lifecycle_state"] == "ESCALATED_HEDGE"
    assert not any(c.startswith(("buy:", "sell:")) for c in client.calls)


def test_escalated_hedge_reports_without_an_exit(registry):
    """The poll reports escalation while leaving the held leg untouched.

    Ownership: ESCALATED_HEDGE is Trader-persisted; the poll reports the
    decision in the result dict but writes no lifecycle row for it.
    """
    _one_sided_pair(registry, fill_price=0.60)
    now_s = (FILL_TS_MS / 1000.0) + 5.0
    client = FakeClient(best_ask=0.45, best_bid=0.52)
    results = auto_manage_pairs(client, registry, _cfg(), now=now_s)
    assert results[0]["action"] == "escalated_wait"
    assert results[0]["lifecycle_state"] == "ESCALATED_HEDGE"
    assert registry.get_lifecycle_state("pair-sl") is None
    assert registry.get_all_closes() == []


def test_elapsed_time_does_not_force_a_single_leg_exit(registry):
    """Elapsed time alone no longer exits a held leg."""
    _one_sided_pair(registry, fill_price=0.60)
    now_s = (FILL_TS_MS / 1000.0) + 50.0
    client = FakeClient(best_ask=0.42, best_bid=0.58)
    results = auto_manage_pairs(client, registry, _cfg(), now=now_s)

    assert len(results) == 1
    assert results[0]["action"] == "patient_wait"
    assert not any(c.startswith(("buy:", "sell:")) for c in client.calls)


def test_elapsed_time_does_not_write_a_close(registry):
    """Waiting past the old retired grace period leaves no close record."""
    _one_sided_pair(registry, fill_price=0.60)
    now_s = (FILL_TS_MS / 1000.0) + 50.0
    client = FakeClient(best_ask=0.42, best_bid=0.58)
    results = auto_manage_pairs(client, registry, _cfg(), now=now_s)
    assert results[0]["action"] == "patient_wait"
    assert registry.get_all_closes() == []


def test_profitable_opposite_ask_does_not_trigger_taker_completion(registry):
    """A profitable crossing still waits for an organic maker fill."""
    _one_sided_pair(registry, fill_price=0.60)
    now_s = (FILL_TS_MS / 1000.0) + 15.0
    client = FakeClient(best_ask=0.38, best_bid=0.58)
    results = auto_manage_pairs(client, registry, _cfg(), now=now_s)

    assert len(results) == 1
    assert results[0]["action"] == "patient_wait"
    assert not any(c.startswith(("buy:", "sell:")) for c in client.calls)


def test_patient_wait_takes_precedence_over_a_profitable_ask(registry):
    """The lifecycle no longer chooses between completion, drift, and grace."""
    _one_sided_pair(registry, fill_price=0.60, pair_id="pair-complete")
    now_s = (FILL_TS_MS / 1000.0) + 15.0
    client = FakeClient(best_ask=0.38, best_bid=0.58)
    results = auto_manage_pairs(client, registry, _cfg(), now=now_s)
    assert results[0]["action"] == "patient_wait"
    assert registry.get_all_closes() == []
    assert not any(c.startswith("buy:") for c in client.calls)


def test_oversized_completable_leg_still_waits_without_attempting_a_buy(registry):
    """The old immediate-completion path no longer attempts oversized orders."""
    # 100 shares at 0.38 = $38.00 > $25 cap
    _one_sided_pair(registry, filled_size=100.0, fill_price=0.60)
    now_s = (FILL_TS_MS / 1000.0) + 15.0
    client = FakeClient(best_ask=0.38, best_bid=0.58)
    # max_order_usd is $25.0
    cfg = _cfg(max_order_usd=25.0)
    results = auto_manage_pairs(client, registry, cfg, now=now_s)

    assert len(results) == 1
    assert results[0]["action"] == "patient_wait"
    assert not any(c.startswith(("buy:", "sell:")) for c in client.calls)
