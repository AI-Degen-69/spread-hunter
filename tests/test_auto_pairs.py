"""The U35 auto pass: live_loop converts in-window one-sided fills.

`auto_manage_pairs` runs once per poll cycle after reconcile. It discovers
pairs with fills from the ledger, gates them on the U35 window and the
close table, and routes each to complete (under the cap) or exit (at/over
the cap or no ask), mirroring the paper run's sweep. These tests drive the pass
with a real tmp registry and a fake venue client -- no network.
"""
import uuid
from pathlib import Path
import pytest

from core_brain.config import MakerConfig
from core_brain.order_registry import (
    OrderRegistry, OrderRecord, FillRecord, CloseRecord, QuoteRecord,
)
from core_brain import single_buy_saver as lp
from core_brain.market_resolution import MarketEndState
from core_brain.single_buy_saver import (
    auto_manage_pairs, manage_single_leg_positions,
)


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


def test_out_of_window_remains_under_lifecycle_management(registry):
    _one_sided_pair(registry, fill_price=0.60, venue_ts=1_000_000)
    client = FakeClient(best_bid=0.58)
    results = auto_manage_pairs(
        client, registry, _cfg(enable_aged_out_rescue=False), now=3_000.0,
    )
    assert results[0]["action"] == "patient_wait"
    assert not any(call.startswith(("buy:", "sell:", "cancel:"))
                   for call in client.calls)


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
# Ordinary single-leg exposure waits for its maker hedge
# ---------------------------------------------------------------------------

def test_in_window_under_cap_waits_instead_of_taker_completing(registry):
    _one_sided_pair(registry, fill_price=0.50)
    client = FakeClient(best_ask=0.40)
    results = auto_manage_pairs(client, registry, _cfg(), now=NOW_S)
    assert len(results) == 1
    assert results[0]["action"] == "patient_wait"
    assert results[0]["pair_id"] == "pair-1"
    assert not any(c.startswith(("buy:", "sell:", "cancel:"))
                   for c in client.calls)


def test_at_cap_does_not_trigger_an_automatic_exit(registry):
    _one_sided_pair(registry, fill_price=0.60)
    client = FakeClient(best_ask=0.395)
    results = auto_manage_pairs(client, registry, _cfg(), now=NOW_S)
    assert len(results) == 1
    assert results[0]["action"] == "escalated_wait"
    assert not any(c.startswith(("buy:", "sell:", "cancel:"))
                   for c in client.calls)


def test_no_ask_still_waits_for_lifecycle_action(registry):
    _one_sided_pair(registry, fill_price=0.60)
    client = FakeClient(best_ask=None)
    results = auto_manage_pairs(client, registry, _cfg(), now=NOW_S)
    assert len(results) == 1
    assert results[0]["action"] == "escalated_wait"
    assert not any(c.startswith(("buy:", "sell:", "cancel:"))
                   for c in client.calls)


def test_dry_run_sends_nothing(registry):
    _one_sided_pair(registry, fill_price=0.50)
    client = FakeClient(best_ask=0.40)
    results = auto_manage_pairs(client, registry, _cfg(), live=False, now=NOW_S)
    assert results[0]["action"] == "patient_wait"
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
    assert actions.get("pair-good") == "patient_wait"
    assert actions.get("pair-bad") == "error"


def test_resolved_pairs_are_skipped_before_any_venue_read(registry):
    """#402 T4: a resolved condition is never completed, exited, or read."""
    from core_brain.order_registry import ResolutionRecord
    _one_sided_pair(registry, fill_price=0.60)
    registry.log_resolution(ResolutionRecord(
        condition_id=COND, winning_token="Up", resolved_ts=NOW_S,
        run_id="t4-test", winning_token_id=TOK_UP))
    client = FakeClient()
    out = auto_manage_pairs(client, registry, _cfg(), now=NOW_S,
                            resolved_cids={COND})
    assert out == []
    assert client.calls == []
    # The naked leg is preserved untouched for the settlement path.
    resting = [o for o in registry.get_active_orders() if o.status == "open"]
    assert len(resting) == 1


def test_unresolved_pairs_still_read_the_venue(registry):
    """Control: without a resolution the same pair costs venue reads."""
    _one_sided_pair(registry, fill_price=0.60)
    client = FakeClient()
    auto_manage_pairs(client, registry, _cfg(), now=NOW_S)
    assert any(c.startswith("book:") for c in client.calls)


def test_shared_manager_waits_for_organic_fill_without_completing(registry):
    _one_sided_pair(registry, fill_price=0.60)
    client = FakeClient(best_ask=0.30, best_bid=0.58)

    results = manage_single_leg_positions(
        client, registry, _cfg(), now=NOW_S,
    )

    assert results[0]["action"] == "patient_wait"
    # Ownership: PATIENT_WAIT is the Trader's to persist, not the poll's.
    # The poll reports the wait but writes no lifecycle row for it.
    assert registry.get_lifecycle_state("pair-1") is None
    assert not any(call.startswith(("buy:", "sell:", "cancel:"))
                   for call in client.calls)


def test_shared_manager_accepts_a_pair_row_with_only_its_filled_token(registry):
    pair_id = "pair-one-token"
    order = OrderRecord(
        id=str(uuid.uuid4()), order_id="venue-up-only",
        condition_id=COND, token_id=TOK_UP, side="BUY", price=0.60,
        original_size=5.0, status="filled", posted_ts=FILL_TS_MS,
        last_polled_ts=FILL_TS_MS, pair_id=pair_id,
        max_pair_cost_at_post=MAX_PAIR_COST,
    )
    registry.create_order(order)
    registry.record_fill(FillRecord(
        trade_id="trade-up-only", order_uuid=order.id, size=5.0,
        price=0.60, venue_ts=FILL_TS_MS,
    ))
    registry.log_quote(QuoteRecord(
        ts=NOW_S, condition_id=COND, token_id=TOK_UP, side="UP",
        price=0.60, size=5.0,
    ))
    registry.log_quote(QuoteRecord(
        ts=NOW_S, condition_id=COND, token_id=TOK_DN, side="DOWN",
        price=0.38, size=5.0,
    ))
    client = FakeClient(best_bid=0.58)

    results = manage_single_leg_positions(
        client, registry, _cfg(), now=NOW_S,
    )

    assert results[0]["action"] == "patient_wait"
    # PATIENT_WAIT is Trader-owned; the poll reports but does not persist it.
    assert registry.get_lifecycle_state(pair_id) is None
    assert not any(call.startswith(("buy:", "sell:", "cancel:"))
                   for call in client.calls)



def test_poll_preserves_a_trader_persisted_escalation_row(registry):
    """Ownership: the poll must never overwrite a Trader-owned escalation row.

    The Trader persists ESCALATED_HEDGE using its per-market offset; the poll
    computes with a different (coarser) offset. If the poll re-persisted the
    row it could downgrade or overwrite the Trader's decision. It reports the
    action but writes nothing for a decision it does not own.
    """
    from core_brain.order_registry import LifecycleStateRecord
    from core_brain.single_leg_lifecycle import LegState

    _one_sided_pair(registry, fill_price=0.60)
    registry.save_lifecycle_state(LifecycleStateRecord(
        pair_id="pair-1", condition_id=COND,
        state=LegState.ESCALATED_HEDGE.value,
        reason="trader escalated", updated_ts_ms=int(NOW_S * 1000),
        evidence_json="{}",
    ))
    client = FakeClient(best_ask=0.30, best_bid=0.50)

    results = manage_single_leg_positions(
        client, registry, _cfg(), now=NOW_S,
    )

    assert results[0]["action"] == "escalated_wait"
    # The Trader-owned row is untouched by the poll pass.
    assert registry.get_lifecycle_state("pair-1").state == "ESCALATED_HEDGE"
    assert not any(call.startswith(("buy:", "sell:", "cancel:"))
                   for call in client.calls)

    # Second phase reuses the same pair rows with a collapsed bid to
    # exercise the poll-owned hard-stop path -- no re-seeding, the
    # venue order_ids are UNIQUE per registry.
    client = FakeClient(best_ask=0.30, best_bid=0.15)

    results = manage_single_leg_positions(
        client, registry, _cfg(), now=NOW_S,
        venue_positions={TOK_UP: 10.0},
    )

    assert results[0]["action"] == "exited"
    assert registry.get_lifecycle_state("pair-1").state == "HARD_STOP"
    assert any(call.startswith("sell:") for call in client.calls)
    assert not any(call.startswith("buy:") for call in client.calls)


def test_shared_manager_completes_only_when_settlement_fallback_is_due(registry):
    _one_sided_pair(registry, fill_price=0.60)
    client = FakeClient(best_ask=0.30)
    now_s = NOW_S + 2_000.0
    market_state = MarketEndState(
        condition_id=COND, closed=False, end_ts=now_s + 600.0,
        end_date_iso=None, end_date_passed=False, accepting_orders=True,
    )

    results = manage_single_leg_positions(
        client, registry, _cfg(), now=now_s,
        market_state_fn=lambda condition_id: market_state,
    )

    assert results[0]["route"] == "completed"
    assert results[0]["action"] == "aged_out_rescue"
    assert results[0]["lifecycle_stage"] == "settlement_fallback"
    assert any(call.startswith("buy:") for call in client.calls)
    assert not any(call.startswith("sell:") for call in client.calls)
