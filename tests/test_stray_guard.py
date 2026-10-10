"""Tests for core_brain/stray_guard.py — Stray-order guard (Issue #205)."""
from __future__ import annotations

import time
import pytest
from core_brain.order_registry import OrderRecord
from core_brain.stray_guard import (
    StrayClassificationType,
    ClassifiedOrder,
    classify_market_orders,
)


def make_order(
    local_id: str,
    condition_id: str,
    token_id: str,
    price: float,
    size: float = 5.0,
    side: str = "BUY",
    status: str = "open",
    pair_id: str | None = None,
    order_id: str | None = None,
) -> OrderRecord:
    now_ms = int(time.time() * 1000)
    return OrderRecord(
        id=local_id,
        condition_id=condition_id,
        token_id=token_id,
        side=side,
        price=price,
        original_size=size,
        status=status,
        posted_ts=now_ms,
        last_polled_ts=now_ms,
        pair_id=pair_id,
        order_id=order_id or f"venue-{local_id}",
    )


def test_classify_registry_paired():
    """Two orders on same market with same pair_id and different tokens are REGISTRY_PAIRED."""
    cond = "0xcond1"
    tok_up = "tok_up"
    tok_dn = "tok_dn"
    o1 = make_order("o1", cond, tok_up, 0.70, pair_id="pair-abc")
    o2 = make_order("o2", cond, tok_dn, 0.25, pair_id="pair-abc")

    res = classify_market_orders(
        orders=[o1, o2],
        books={},
        max_pair_cost=0.99,
    )

    assert len(res.registry_paired) == 1
    pair_id, legs = res.registry_paired[0]
    assert pair_id == "pair-abc"
    assert {legs[0].id, legs[1].id} == {"o1", "o2"}
    assert len(res.complementary_detached) == 0
    assert len(res.hopeless_strays) == 0


def test_classify_complementary_detached():
    """Two orders on same market with different pair_ids summing <= max_pair_cost are COMPLEMENTARY_DETACHED."""
    cond = "0xcond1"
    tok_up = "tok_up"
    tok_dn = "tok_dn"
    o1 = make_order("o1", cond, tok_up, 0.73, pair_id="pair-1")
    o2 = make_order("o2", cond, tok_dn, 0.23, pair_id="pair-2")

    res = classify_market_orders(
        orders=[o1, o2],
        books={},
        max_pair_cost=0.99,
    )

    assert len(res.registry_paired) == 0
    assert len(res.complementary_detached) == 1
    detached = res.complementary_detached[0]
    assert {detached.leg1.id, detached.leg2.id} == {"o1", "o2"}
    assert detached.combined_cost == pytest.approx(0.96)
    assert len(res.hopeless_strays) == 0


def test_classify_hopeless_stray_when_price_and_ask_exceed_cap():
    """Lone resting order whose price + opposing best ask >= max_pair_cost is a HOPELESS_STRAY."""
    cond = "0xcond1"
    tok_up = "tok_up"
    tok_dn = "tok_dn"
    o1 = make_order("o1", cond, tok_up, 0.75, pair_id="pair-lone")

    # Opposing token ask is 0.26 -> 0.75 + 0.26 = 1.01 >= 0.99
    books = {
        tok_dn: {"asks": [[0.26, 100.0]]}
    }

    res = classify_market_orders(
        orders=[o1],
        books=books,
        max_pair_cost=0.99,
        market_tokens={cond: (tok_up, tok_dn)},
    )

    assert len(res.registry_paired) == 0
    assert len(res.complementary_detached) == 0
    assert len(res.hopeless_strays) == 1
    stray = res.hopeless_strays[0]
    assert stray.order.id == "o1"
    assert "cost_exceeds_cap" in stray.reason


def test_classify_viable_lone_order_not_hopeless():
    """Lone resting order whose price + opposing best ask < max_pair_cost is viable (not hopeless)."""
    cond = "0xcond1"
    tok_up = "tok_up"
    tok_dn = "tok_dn"
    o1 = make_order("o1", cond, tok_up, 0.70, pair_id="pair-lone")

    # Opposing token ask is 0.24 -> 0.70 + 0.24 = 0.94 < 0.99
    books = {
        tok_dn: {"asks": [[0.24, 100.0]]}
    }

    res = classify_market_orders(
        orders=[o1],
        books=books,
        max_pair_cost=0.99,
        market_tokens={cond: (tok_up, tok_dn)},
    )

    assert len(res.registry_paired) == 0
    assert len(res.complementary_detached) == 0
    assert len(res.hopeless_strays) == 0
    assert len(res.viable_strays) == 1


def test_adopt_detached_legs_idempotent(tmp_path):
    """Adopting detached complementary legs welds them under one pair_id in registry."""
    from core_brain.order_registry import OrderRegistry
    from core_brain.single_buy_saver import load_pair
    from core_brain.stray_guard import adopt_detached_legs

    db_path = tmp_path / "orders.db"
    reg = OrderRegistry(db_path=db_path)

    cond = "0xcond_adopt"
    tok_up = "tok_up"
    tok_dn = "tok_dn"
    o1 = make_order("o1", cond, tok_up, 0.70, size=5.0, pair_id="pair-stray-1")
    o2 = make_order("o2", cond, tok_dn, 0.25, size=5.0, pair_id="pair-stray-2")
    reg.create_order(o1)
    reg.create_order(o2)

    active = reg.get_active_orders()
    res = classify_market_orders(active, books={}, max_pair_cost=0.99)
    assert len(res.complementary_detached) == 1

    # Adopt
    adopted = adopt_detached_legs(reg, res.complementary_detached)
    assert len(adopted) == 1
    shared_pid = adopted[0]
    assert shared_pid in ("pair-stray-1", "pair-stray-2")

    # Verify both orders in registry now share the same pair_id
    o1_fresh = reg.get_order("o1")
    o2_fresh = reg.get_order("o2")
    assert o1_fresh is not None and o2_fresh is not None
    assert o1_fresh.pair_id == shared_pid
    assert o2_fresh.pair_id == shared_pid

    # Verify single_buy_saver.load_pair sees both legs
    pair = load_pair(reg, shared_pid)
    assert len(pair["legs"]) == 2
    assert pair["condition_id"] == cond

    # Re-classification must be idempotent (now registry_paired, 0 detached)
    active_after = reg.get_active_orders()
    res_after = classify_market_orders(active_after, books={}, max_pair_cost=0.99)
    assert len(res_after.registry_paired) == 1
    assert len(res_after.complementary_detached) == 0

    # Re-adopting does nothing
    adopted_again = adopt_detached_legs(reg, res_after.complementary_detached)
    assert len(adopted_again) == 0


class MockCancelClient:
    def __init__(self):
        self.cancelled_ids = []

    def cancel_order(self, payload_or_id):
        target = getattr(payload_or_id, "orderID", None) or payload_or_id
        self.cancelled_ids.append(str(target))
        return {"success": True, "canceled": [str(target)]}


def test_cancel_hopeless_orders_dry_run_and_live(tmp_path):
    """Hopeless orders are reported in dry-run, and cancelled on venue and registry in live mode."""
    from core_brain.order_registry import OrderRegistry
    from core_brain.stray_guard import cancel_hopeless_orders

    db_path = tmp_path / "orders.db"
    reg = OrderRegistry(db_path=db_path)

    cond = "0xcond_hopeless"
    tok_up = "tok_up"
    tok_dn = "tok_dn"
    o1 = make_order("o1", cond, tok_up, 0.78, order_id="v-o1", pair_id="pair-lone")
    reg.create_order(o1)

    books = {tok_dn: {"asks": [[0.25, 100.0]]}}  # 0.78 + 0.25 = 1.03 >= 0.99
    res = classify_market_orders(
        reg.get_active_orders(),
        books=books,
        max_pair_cost=0.99,
        market_tokens={cond: (tok_up, tok_dn)},
    )
    assert len(res.hopeless_strays) == 1

    mock_client = MockCancelClient()

    # 1. Dry run: live=False
    actions_dry = cancel_hopeless_orders(mock_client, reg, res.hopeless_strays, live=False)
    assert len(actions_dry) == 1
    assert actions_dry[0]["action"] == "would_cancel"
    assert len(mock_client.cancelled_ids) == 0
    assert reg.get_order("o1").status == "open"

    # 2. Live run: live=True
    actions_live = cancel_hopeless_orders(mock_client, reg, res.hopeless_strays, live=True)
    assert len(actions_live) == 1
    assert actions_live[0]["action"] == "cancelled"
    assert mock_client.cancelled_ids == ["v-o1"]

    o1_fresh = reg.get_order("o1")
    assert o1_fresh.status == "cancelled"
    assert "hopeless_stray" in (o1_fresh.cancel_reason or "")


class MockExitClient:
    def __init__(self):
        self.market_orders = []

    def get_order_book(self, token_id):
        return {"bids": [{"price": "0.58", "size": "100.0"}], "asks": []}

    def get_order(self, order_id):
        return {"id": order_id, "status": "filled", "size_matched": 5.0}

    def create_and_post_market_order(self, payload):
        self.market_orders.append(payload)
        return {"status": "matched", "takingAmount": "5.0", "size": "5.0"}



def test_remediate_stray_positions_dry_run_and_live(tmp_path):
    """Unhedged stray positions without a pair are assigned a pair_id and exited."""
    from core_brain.order_registry import OrderRegistry, FillRecord, QuoteRecord
    from core_brain.stray_guard import remediate_stray_positions

    db_path = tmp_path / "orders.db"
    reg = OrderRegistry(db_path=db_path)

    cond = "0xcond_pos"
    tok_up = "tok_up"
    now_s = time.time()
    now_ms = int(now_s * 1000)

    # Insert a filled stray order with no pair_id
    o1 = make_order("o_stray", cond, tok_up, 0.60, size=5.0, status="filled", pair_id=None)
    reg.create_order(o1)
    reg.record_fill(FillRecord(
        trade_id="tr_stray",
        order_uuid="o_stray",
        size=5.0,
        price=0.60,
        venue_ts=now_ms,
        recorded_ts=now_ms,
    ))
    reg.log_quote(QuoteRecord(
        ts=now_s,
        condition_id=cond,
        token_id=tok_up,
        side="UP",
        price=0.60,
        size=5.0,
        local_id="o_stray",
    ))

    client = MockExitClient()

    # 1. Dry run
    res_dry = remediate_stray_positions(client, reg, [o1], live=False)
    assert len(res_dry) == 1
    assert res_dry[0]["action"] == "would_exit"
    assert res_dry[0]["size"] == 5.0
    assert len(client.market_orders) == 0
    assert reg.get_order("o_stray").pair_id is None

    # 2. Live run
    res_live = remediate_stray_positions(client, reg, [o1], live=True)
    assert len(res_live) == 1
    assert "error" not in res_live[0], res_live[0].get("error")
    assert res_live[0]["action"] == "exited"
    assert len(client.market_orders) == 1

    # Check that close was recorded
    closes = reg.get_all_closes()
    assert len(closes) == 1
    assert closes[0]["condition_id"] == cond
    assert closes[0]["shares"] == 5.0


def test_reconcile_integration(tmp_path):
    """Reconcile pass runs stray guard step, adopting detached legs."""
    from core_brain.order_registry import OrderRegistry, reconcile_orders

    db_path = tmp_path / "orders.db"
    reg = OrderRegistry(db_path=db_path)

    cond = "0xcond_reconcile"
    tok_up = "tok_up"
    tok_dn = "tok_dn"

    o_up = make_order("o_up", cond, tok_up, 0.48, pair_id=None, order_id="v_up")
    o_dn = make_order("o_dn", cond, tok_dn, 0.48, pair_id=None, order_id="v_dn")
    reg.create_order(o_up)
    reg.create_order(o_dn)

    class MockReconcileClient:
        creds = object()
        def get_open_orders(self):
            return [
                {"id": "v_up", "asset_id": tok_up, "price": 0.48, "size": 10.0, "side": "BUY"},
                {"id": "v_dn", "asset_id": tok_dn, "price": 0.48, "size": 10.0, "side": "BUY"},
            ]
        def get_trades(self, **kwargs):
            return []

    client = MockReconcileClient()
    summary = reconcile_orders(client, reg)
    assert summary.strays_adopted == 1
    assert any("ADOPT_STRAY" in t for t in summary.transitions)

    # Verify both orders now share a pair_id
    up_fresh = reg.get_order("o_up")
    dn_fresh = reg.get_order("o_dn")
    assert up_fresh.pair_id is not None
    assert up_fresh.pair_id == dn_fresh.pair_id


def test_run_stray_guard_end_to_end(tmp_path):
    """Unified run_stray_guard handles adoption, cancellation, and position remediation in one pass."""
    from core_brain.order_registry import OrderRegistry, FillRecord, QuoteRecord
    from core_brain.stray_guard import run_stray_guard

    db_path = tmp_path / "orders.db"
    reg = OrderRegistry(db_path=db_path)

    now_s = time.time()
    now_ms = int(now_s * 1000)

    # 1. Detached pair (UP + DOWN <= 0.99)
    cond1 = "0xcond1"
    tok1_up = "tok1_up"
    tok1_dn = "tok1_dn"
    o1 = make_order("o1", cond1, tok1_up, 0.45, order_id="v-o1", pair_id=None)
    o2 = make_order("o2", cond1, tok1_dn, 0.45, order_id="v-o2", pair_id=None)
    reg.create_order(o1)
    reg.create_order(o2)

    # 2. Hopeless stray order (UP @ 0.78, DOWN ask @ 0.25 -> 1.03 >= 0.99)
    cond2 = "0xcond2"
    tok2_up = "tok2_up"
    tok2_dn = "tok2_dn"
    o3 = make_order("o3", cond2, tok2_up, 0.78, order_id="v-o3", pair_id=None)
    reg.create_order(o3)
    reg.log_quote(QuoteRecord(
        ts=now_s,
        condition_id=cond2,
        token_id=tok2_up,
        side="UP",
        price=0.78,
        size=100.0,
        local_id="o3",
    ))
    reg.log_quote(QuoteRecord(
        ts=now_s,
        condition_id=cond2,
        token_id=tok2_dn,
        side="DOWN",
        price=0.25,
        size=100.0,
    ))

    # 3. Unpaired filled stray position (UP @ 0.60, filled 5.0)
    cond3 = "0xcond3"
    tok3_up = "tok3_up"
    o4 = make_order("o4", cond3, tok3_up, 0.60, size=5.0, status="filled", pair_id=None)
    reg.create_order(o4)
    reg.record_fill(FillRecord(
        trade_id="tr_stray_e2e",
        order_uuid="o4",
        size=5.0,
        price=0.60,
        venue_ts=now_ms,
        recorded_ts=now_ms,
    ))
    reg.log_quote(QuoteRecord(
        ts=now_s,
        condition_id=cond3,
        token_id=tok3_up,
        side="UP",
        price=0.60,
        size=5.0,
        local_id="o4",
    ))

    class MockE2EClient:
        def __init__(self):
            self.cancelled_ids = []
            self.market_orders = []

        def get_order_book(self, token_id):
            if token_id == tok2_dn:
                return {"asks": [{"price": "0.25", "size": "100.0"}], "bids": []}
            if token_id == tok3_up:
                return {"bids": [{"price": "0.58", "size": "100.0"}], "asks": []}
            return {"bids": [], "asks": []}

        def cancel_order(self, payload_or_id):
            target = getattr(payload_or_id, "orderID", None) or payload_or_id
            self.cancelled_ids.append(str(target))
            return {"success": True, "canceled": [str(target)]}

        def get_order(self, order_id):
            return {"id": order_id, "status": "filled", "size_matched": 5.0}

        def create_and_post_market_order(self, payload):
            self.market_orders.append(payload)
            return {"status": "matched", "takingAmount": "5.0", "size": "5.0"}

    client = MockE2EClient()

    res = run_stray_guard(client, reg, live=True)

    # Detached pair adopted
    assert len(res["adopted_pairs"]) == 1
    shared_pid = res["adopted_pairs"][0]
    assert reg.get_order("o1").pair_id == shared_pid
    assert reg.get_order("o2").pair_id == shared_pid

    # Hopeless order cancelled
    assert len(res["cancelled_orders"]) == 1
    assert res["cancelled_orders"][0]["action"] == "cancelled"
    assert "0.25" in res["cancelled_orders"][0]["reason"]
    assert "v-o3" in client.cancelled_ids
    assert reg.get_order("o3").status == "cancelled"

    # Unpaired filled position remediated
    assert len(res["remediated_positions"]) == 1
    assert res["remediated_positions"][0]["action"] == "exited"
    assert len(client.market_orders) == 1


def test_cli_stray_guard(tmp_path, monkeypatch, capsys):
    """CLI stray-guard command runs non-destructively in --no-live and prints summary."""
    from core_brain.order_registry import OrderRegistry
    from core_brain.order_manager import stray_guard_cmd

    db_path = tmp_path / "orders.db"
    reg = OrderRegistry(db_path=db_path)

    cond = "0xcond_cli"
    tok_up = "tok_up"
    tok_dn = "tok_dn"
    o1 = make_order("o1", cond, tok_up, 0.47, pair_id=None)
    o2 = make_order("o2", cond, tok_dn, 0.47, pair_id=None)
    reg.create_order(o1)
    reg.create_order(o2)

    class MockCliClient:
        def get_order_book(self, token_id):
            return {"bids": [], "asks": []}

    monkeypatch.setattr("core_brain.order_manager.client", lambda: MockCliClient())

    stray_guard_cmd(live=False, db_path=str(db_path))

    captured = capsys.readouterr()
    assert "=== STRAY ORDER GUARD [DRY-RUN (--no-live)] ===" in captured.out
    assert "Adopted pairs (1):" in captured.out
    # Non-destructive: DB not mutated in dry run
    assert reg.get_order("o1").pair_id is None
    assert reg.get_order("o2").pair_id is None


def test_hopeless_partial_order_cancelled_and_remediated(tmp_path):
    """A hopeless partial order has its venue remainder cancelled and its fills remediated."""
    from core_brain.order_registry import OrderRegistry, FillRecord, QuoteRecord
    from core_brain.stray_guard import run_stray_guard

    db_path = tmp_path / "orders.db"
    reg = OrderRegistry(db_path=db_path)

    now_s = time.time()
    now_ms = int(now_s * 1000)

    cond = "0xcond_partial"
    tok_up = "tok_up"
    tok_dn = "tok_dn"

    # Order has size 10.0, but only 4.0 is filled; status is 'partial'
    o_part = make_order("o_part", cond, tok_up, 0.75, size=10.0, status="partial", pair_id="pair-part", order_id="v-part")
    reg.create_order(o_part)
    reg.record_fill(FillRecord(
        trade_id="tr_part",
        order_uuid="o_part",
        size=4.0,
        price=0.75,
        venue_ts=now_ms,
        recorded_ts=now_ms,
    ))
    reg.log_quote(QuoteRecord(
        ts=now_s,
        condition_id=cond,
        token_id=tok_up,
        side="UP",
        price=0.75,
        size=10.0,
        local_id="o_part",
    ))
    reg.log_quote(QuoteRecord(
        ts=now_s,
        condition_id=cond,
        token_id=tok_dn,
        side="DOWN",
        price=0.30,
        size=50.0,
    ))

    class MockPartialClient:
        def __init__(self):
            self.cancelled_ids = []
            self.market_orders = []

        def get_order_book(self, token_id):
            if token_id == tok_dn:
                return {"asks": [{"price": "0.30", "size": "50.0"}], "bids": []}
            if token_id == tok_up:
                return {"bids": [{"price": "0.70", "size": "50.0"}], "asks": []}
            return {"bids": [], "asks": []}

        def cancel_order(self, payload_or_id):
            target = getattr(payload_or_id, "orderID", None) or payload_or_id
            self.cancelled_ids.append(str(target))
            return {"success": True, "canceled": [str(target)]}

        def get_order(self, order_id):
            return {"id": order_id, "status": "partial", "size_matched": 4.0}

        def create_and_post_market_order(self, payload):
            self.market_orders.append(payload)
            return {"status": "matched", "takingAmount": "4.0", "size": "4.0"}

    client = MockPartialClient()

    res = run_stray_guard(client, reg, live=True)

    # 1. Hopeless partial order cancelled on venue
    assert len(res["cancelled_orders"]) == 1
    assert "v-part" in client.cancelled_ids
    assert reg.get_order("o_part").status == "cancelled"

    # 2. Position remediated (fills sold/exited)
    assert len(res["remediated_positions"]) == 1
    assert res["remediated_positions"][0]["action"] == "exited"
    assert len(client.market_orders) == 1


def test_resolved_tokens_are_never_requested(tmp_path):
    """#402 T4: the guard omits resolved-condition tokens from book reads."""
    from core_brain.order_registry import OrderRegistry, ResolutionRecord
    from core_brain.stray_guard import run_stray_guard

    reg = OrderRegistry(db_path=tmp_path / "orders.db")
    dead, live = "0xdead", "0xlive"
    reg.create_order(make_order("o-dead", dead, "tok-dead", 0.45,
                                order_id="v-dead"))
    reg.create_order(make_order("o-live", live, "tok-live", 0.45,
                                order_id="v-live"))
    reg.log_resolution(ResolutionRecord(
        condition_id=dead, winning_token="Up", resolved_ts=time.time(),
        run_id="t4-test", winning_token_id="tok-dead"))

    requested: list[str] = []

    class _Client:
        def get_order_book(self, token_id):
            requested.append(token_id)
            return {"bids": [], "asks": []}

        def get_order(self, order_id):
            return {"id": order_id, "status": "open", "size_matched": 0.0}

        def cancel_order(self, payload_or_id):
            return {"success": True, "canceled": []}

        def create_and_post_market_order(self, payload):
            return {"status": "matched", "takingAmount": "0.0", "size": "0.0"}

    run_stray_guard(_Client(), reg, live=False, resolved_cids={dead})

    assert "tok-dead" not in requested
    assert "tok-live" in requested


def test_resolved_tokens_skipped_when_caller_passes_no_set(tmp_path):
    """#402 review: with no explicit set the guard reads the store itself, so
    reconcile/CLI callers that never heard of `resolved_cids` still stay off
    dead books."""
    from core_brain.order_registry import OrderRegistry, ResolutionRecord
    from core_brain.stray_guard import run_stray_guard

    reg = OrderRegistry(db_path=tmp_path / "orders.db")
    dead, live = "0xdead2", "0xlive2"
    reg.create_order(make_order("o-dead2", dead, "tok-dead2", 0.45,
                                order_id="v-dead2"))
    reg.create_order(make_order("o-live2", live, "tok-live2", 0.45,
                                order_id="v-live2"))
    reg.log_resolution(ResolutionRecord(
        condition_id=dead, winning_token="Up", resolved_ts=time.time(),
        run_id="review-test", winning_token_id="tok-dead2"))

    requested: list[str] = []

    class _Client:
        def get_order_book(self, token_id):
            requested.append(token_id)
            return {"bids": [], "asks": []}

        def get_order(self, order_id):
            return {"id": order_id, "status": "open", "size_matched": 0.0}

        def cancel_order(self, payload_or_id):
            return {"success": True, "canceled": []}

        def create_and_post_market_order(self, payload):
            return {"status": "matched", "takingAmount": "0.0", "size": "0.0"}

    run_stray_guard(_Client(), reg, live=False)

    assert "tok-dead2" not in requested
    assert "tok-live2" in requested


def test_failing_book_backs_off_without_an_explicit_tracker(tmp_path):
    """#402 review: the backoff must be live on the real reconcile path, which
    passes no tracker -- a failing token is requested once, not every pass."""
    from core_brain.order_registry import OrderRegistry
    from core_brain.stray_guard import run_stray_guard

    reg = OrderRegistry(db_path=tmp_path / "orders.db")
    reg.create_order(make_order("o-dead3", "0xdead3", "tok-dead3", 0.45,
                                order_id="v-dead3"))

    requested: list[str] = []

    class _Client:
        def get_order_book(self, token_id):
            requested.append(token_id)
            raise RuntimeError("404 no book")

        def get_order(self, order_id):
            return {"id": order_id, "status": "open", "size_matched": 0.0}

        def cancel_order(self, payload_or_id):
            return {"success": True, "canceled": []}

        def create_and_post_market_order(self, payload):
            return {"status": "matched", "takingAmount": "0.0", "size": "0.0"}

    client = _Client()
    run_stray_guard(client, reg, live=False)
    run_stray_guard(client, reg, live=False)

    assert requested == ["tok-dead3"], (
        "the second pass must be gated by the default per-registry backoff")
    assert reg.get_all_resolutions() == []


def test_dead_book_backoff_blocks_then_permits_retry():
    """#402 T4: per-token backoff, driven by a fake clock."""
    from core_brain.market_lifecycle import DeadBookBackoff

    now = [1000.0]
    bo = DeadBookBackoff(base_sec=10.0, cap_sec=60.0,
                         now_fn=lambda: now[0])
    assert bo.allow("tok-a") is True
    bo.note_failure("tok-a")
    assert bo.allow("tok-a") is False
    now[0] += 9.0
    assert bo.allow("tok-a") is False
    now[0] += 2.0  # past the 10s window: retry permitted
    assert bo.allow("tok-a") is True
    bo.note_success("tok-a")
    bo.note_failure("tok-a")  # waits restart after success
    now[0] += 5.0
    assert bo.allow("tok-a") is False
    now[0] += 6.0
    assert bo.allow("tok-a") is True
    # Backoff never records anything: no invented resolutions.
    assert not hasattr(bo, "registry")


def test_failed_book_read_retries_after_backoff_without_resolving(tmp_path):
    """#402 T4: a first failed read gates, a later read succeeds, and no
    resolution is invented in between."""
    from core_brain.order_registry import OrderRegistry
    from core_brain.market_lifecycle import DeadBookBackoff
    from core_brain.stray_guard import run_stray_guard

    reg = OrderRegistry(db_path=tmp_path / "orders.db")
    reg.create_order(make_order("o-flap", "0xflap", "tok-flap", 0.45,
                                order_id="v-flap"))

    now = [1000.0]
    bo = DeadBookBackoff(base_sec=10.0, cap_sec=60.0,
                         now_fn=lambda: now[0])
    state = {"fail": True}
    requested: list[str] = []

    class _Client:
        def get_order_book(self, token_id):
            requested.append(token_id)
            if state["fail"]:
                raise RuntimeError("404 no book")
            return {"bids": [{"price": "0.45", "size": "10.0"}], "asks": []}

        def get_order(self, order_id):
            return {"id": order_id, "status": "open", "size_matched": 0.0}

        def cancel_order(self, payload_or_id):
            return {"success": True, "canceled": []}

        def create_and_post_market_order(self, payload):
            return {"status": "matched", "takingAmount": "0.0", "size": "0.0"}

    client = _Client()
    run_stray_guard(client, reg, live=False, book_tracker=bo)
    assert requested == ["tok-flap"]
    run_stray_guard(client, reg, live=False, book_tracker=bo)
    assert requested == ["tok-flap"], "backoff must gate the immediate retry"
    now[0] += 11.0
    state["fail"] = False
    run_stray_guard(client, reg, live=False, book_tracker=bo)
    assert requested == ["tok-flap", "tok-flap"]
    assert reg.get_all_resolutions() == []






def test_classify_market_orders_protected_orders_never_hopeless():
    from core_brain.stray_guard import classify_market_orders

    cond = "0xcond_protect"
    tok_up = "tok_up"
    tok_dn = "tok_dn"
    # Order would be hopeless because price 0.50 + ask 0.50 >= 0.99
    o1 = make_order("o1", cond, tok_dn, 0.50, pair_id=None)
    books = {tok_up: {"asks": [{"price": 0.50, "size": 10.0}]}}

    # When NOT protected -> hopeless
    res_unprotected = classify_market_orders([o1], books=books, max_pair_cost=0.99, market_tokens={cond: (tok_up, tok_dn)})
    assert len(res_unprotected.hopeless_strays) == 1

    # When protected -> in protected list, never hopeless
    res_protected = classify_market_orders(
        [o1], books=books, max_pair_cost=0.99,
        market_tokens={cond: (tok_up, tok_dn)},
        protected_order_ids={"o1"}
    )
    assert len(res_protected.hopeless_strays) == 0
    assert len(res_protected.protected) == 1
    assert res_protected.protected[0].id == "o1"
