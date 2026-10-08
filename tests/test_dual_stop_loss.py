"""TDD tests for Dual-Trigger Stop-Loss in single_buy_saver.

The Dual-Trigger Stop-Loss rules:
1. Grace Window (default 45s): A fresh single fill rests its maker quote instead of panic-dumping.
2. Adverse Drift Stop-Loss: If the heavy leg's price drops by >10% (or >$0.045) from fill price,
   it exits immediately regardless of remaining grace time.
3. Grace Expiry Stop-Loss: If grace period (45s) passes without a maker fill, it exits to prevent holding to settlement.
4. Active Taker Completion: If opposing ask drops so fill_cost + ask < max_pair_cost, it completes immediately.
"""
import uuid
from pathlib import Path
import pytest

from core_brain.config import MakerConfig
from core_brain.order_registry import (
    OrderRegistry, OrderRecord, FillRecord, QuoteRecord,
)
from core_brain.single_buy_saver import auto_manage_pairs


MAX_PAIR_COST = 0.995
TOK_UP = "tok-up"
TOK_DN = "tok-dn"
COND = "0xcond-dual-sl"
FILL_TS_MS = 1_000_000_000  # 1,000,000.000 s


@pytest.fixture
def registry(tmp_path: Path) -> OrderRegistry:
    return OrderRegistry(db_path=tmp_path / "live.db")


def _cfg(**kw) -> MakerConfig:
    base = dict(
        enable_pairs_rule=True,
        pairs_exit_window_sec=900.0,
        max_pair_cost=MAX_PAIR_COST,
        single_buy_grace_sec=45.0,
        single_buy_max_loss_pct=0.10,
        single_buy_max_loss_usd=0.045,
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
    def __init__(self, best_ask=0.42, best_bid=0.58, cancel_ok=True):
        self.best_ask = best_ask
        self.best_bid = best_bid
        self.cancel_ok = cancel_ok
        self.calls: list[str] = []

    def get_order_book(self, token_id):
        self.calls.append(f"book:{token_id}")
        asks = [{"price": str(self.best_ask), "size": "100"}] if self.best_ask is not None else []
        bids = [{"price": str(self.best_bid), "size": "100"}] if self.best_bid is not None else []
        return {"asset_id": token_id, "bids": bids, "asks": asks, "tick_size": "0.01"}

    def get_order(self, order_id):
        return {"orderID": order_id, "size_matched": 0.0}

    def cancel_order(self, payload):
        return {"canceled": ["venue-light"]}

    def create_and_post_market_order(self, order_args, options=None,
                                     order_type="FOK", defer_exec=False):
        verb = "sell" if order_args.side == "SELL" else "buy"
        self.calls.append(f"{verb}:{order_args.token_id}:{order_args.amount}")
        return {"success": True, "orderID": f"venue-{verb}"}


def test_fresh_fill_within_grace_period_holds_maker_bid(registry):
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


def test_grace_expiry_does_not_force_a_single_leg_exit(registry):
    """Elapsed time alone no longer exits a held leg."""
    _one_sided_pair(registry, fill_price=0.60)
    now_s = (FILL_TS_MS / 1000.0) + 50.0
    client = FakeClient(best_ask=0.42, best_bid=0.58)
    results = auto_manage_pairs(client, registry, _cfg(), now=now_s)

    assert len(results) == 1
    assert results[0]["action"] == "patient_wait"
    assert not any(c.startswith(("buy:", "sell:")) for c in client.calls)


def test_grace_expiry_does_not_write_a_close(registry):
    """Waiting past the old grace period leaves no close record."""
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
