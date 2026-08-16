"""Unit tests for strategy/order_registry.py.

Stage 2 Invariant Tests:
1. Local UUID primary key written before order_id is attached; order_id is unique and nullable.
2. size_matched is strictly derived from SUM(fills.size), no size_matched column in orders, FK enforced.
3. Writes are atomic and fail closed: failed operations raise and roll back cleanly.
4. Replayed fill sequence produces the correct row transitions.
5. Mid-sequence restart produces the same end state as an uninterrupted run (from disk).
6. Trade landing in the boundary second is counted exactly once.
7. Duplicate trade_id across two polls does not double-count.
8. Orphan venue order is adopted, and an unmatchable one is recorded as unattributed.
9. 429/5xx produces exponential backoff sequence capped at 60s without crashing.
10. Absence from get_open_orders without trade evidence does not mark a row filled.
"""

import contextlib
import sqlite3
import uuid
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from strategy.order_registry import (
    OrderRegistry,
    OrderRecord,
    FillRecord,
    get_connection,
    init_db,
    DEFAULT_ORPHAN_MATCH_WINDOW_MS,
    SIZE_EPS,
    reconcile_orders,
    ReconcileSummary,
    compute_backoff_delay,
)


class MockClobClient:
    """Mock CLOB client providing get_open_orders and get_trades without network."""

    def __init__(self, open_orders=None, trades=None):
        self._open_orders = list(open_orders or [])
        self._trades = list(trades or [])
        self.get_trades_calls = []
        self.get_open_orders_calls = []

    def get_open_orders(self, params=None, only_first_page=False, next_cursor=None):
        self.get_open_orders_calls.append(params)
        return list(self._open_orders)

    def get_trades(self, params=None, only_first_page=False, next_cursor=None):
        self.get_trades_calls.append(params)
        return list(self._trades)


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test_live.db"
    return db_path


@pytest.fixture
def registry(temp_db: Path) -> OrderRegistry:
    return OrderRegistry(db_path=temp_db)


def test_invariant_1_local_uuid_before_order_id(registry: OrderRegistry, temp_db: Path):
    """Invariant 1: Local UUID primary key written before venue order_id is attached."""
    local_id = str(uuid.uuid4())
    now_ms = 1723840000000

    order = OrderRecord(
        id=local_id,
        order_id=None,
        condition_id="0xcond123",
        token_id="0xtok456",
        side="BUY",
        price=0.48,
        original_size=100.0,
        status="pending",
        posted_ts=now_ms,
        last_polled_ts=now_ms,
        pair_id="pair_abc_1",
        max_pair_cost_at_post=0.96,
    )

    registry.create_order(order)

    fetched = registry.get_order(local_id)
    assert fetched is not None
    assert fetched.id == local_id
    assert fetched.order_id is None
    assert fetched.status == "pending"
    assert fetched.original_size == 100.0
    assert fetched.price == 0.48

    venue_order_id = "0xvenue_order_999"
    registry.attach_venue_order_id(local_id, venue_order_id, status="open")

    updated = registry.get_order(local_id)
    assert updated is not None
    assert updated.order_id == venue_order_id
    assert updated.status == "open"

    by_venue = registry.get_order_by_venue_id(venue_order_id)
    assert by_venue is not None
    assert by_venue.id == local_id

    dup_id = str(uuid.uuid4())
    dup_order = OrderRecord(
        id=dup_id,
        order_id=venue_order_id,
        condition_id="0xcond123",
        token_id="0xtok456",
        side="BUY",
        price=0.48,
        original_size=50.0,
        status="open",
        posted_ts=now_ms,
        last_polled_ts=now_ms,
    )
    with pytest.raises(sqlite3.IntegrityError):
        registry.create_order(dup_order)


def test_invariant_2_size_matched_derived_from_fills(registry: OrderRegistry, temp_db: Path):
    """Invariant 2: size_matched is derived SUM(fills.size), no size_matched column, FK enforced."""
    local_id = str(uuid.uuid4())
    now_ms = 1723840000000

    order = OrderRecord(
        id=local_id,
        order_id="0xvenue_order_inv2",
        condition_id="0xcond123",
        token_id="0xtok456",
        side="BUY",
        price=0.50,
        original_size=100.0,
        status="open",
        posted_ts=now_ms,
        last_polled_ts=now_ms,
    )
    registry.create_order(order)

    with contextlib.closing(get_connection(temp_db)) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(orders)").fetchall()}
        assert "size_matched" not in cols, "orders table must NOT contain a size_matched column"

    assert registry.get_size_matched(local_id) == 0.0

    fill1 = FillRecord(
        trade_id="0xtrade_1",
        order_uuid=local_id,
        size=40.0,
        price=0.50,
        venue_ts=now_ms + 1000,
    )
    registry.record_fill(fill1)
    assert registry.get_size_matched(local_id) == 40.0

    fill2 = FillRecord(
        trade_id="0xtrade_2",
        order_uuid=local_id,
        size=35.5,
        price=0.50,
        venue_ts=now_ms + 2000,
    )
    registry.record_fill(fill2)
    assert registry.get_size_matched(local_id) == 75.5

    assert registry.record_fill(fill1) is False
    assert registry.get_size_matched(local_id) == 75.5, "duplicate must not double-count"
    assert registry.record_fill(fill2) is False
    assert registry.get_size_matched(local_id) == 75.5

    invalid_fill = FillRecord(
        trade_id="0xtrade_orphan_fk",
        order_uuid="00000000-0000-0000-0000-000000000000",
        size=10.0,
        price=0.50,
        venue_ts=now_ms + 3000,
    )
    with pytest.raises(sqlite3.IntegrityError):
        registry.record_fill(invalid_fill)


def test_invariant_3_atomic_write_fails_closed(registry: OrderRegistry, temp_db: Path):
    """Invariant 3: Writes are atomic and fail closed (raises error, no partial state)."""
    now_ms = 1723840000000

    order_id_1 = str(uuid.uuid4())
    order1 = OrderRecord(
        id=order_id_1,
        order_id="0xvenue_ok",
        condition_id="0xcond1",
        token_id="0xtok1",
        side="BUY",
        price=0.40,
        original_size=50.0,
        status="open",
        posted_ts=now_ms,
        last_polled_ts=now_ms,
    )
    registry.create_order(order1)

    order_id_2 = str(uuid.uuid4())
    bad_fill = FillRecord(
        trade_id="0xtrade_fail",
        order_uuid="non_existent_uuid",
        size=10.0,
        price=0.40,
        venue_ts=now_ms,
    )

    with pytest.raises(sqlite3.IntegrityError):
        with contextlib.closing(get_connection(temp_db)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO orders (id, condition_id, token_id, side, price, original_size, status, posted_ts, last_polled_ts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (order_id_2, "0xcond2", "0xtok2", "BUY", 0.40, 50.0, "open", now_ms, now_ms),
            )
            conn.execute(
                "INSERT INTO fills (trade_id, order_uuid, size, price, venue_ts) VALUES (?, ?, ?, ?, ?)",
                (bad_fill.trade_id, bad_fill.order_uuid, bad_fill.size, bad_fill.price, bad_fill.venue_ts),
            )
            conn.commit()

    assert registry.get_order(order_id_2) is None


def test_attach_venue_order_id_fails_closed_on_missing_row(registry: OrderRegistry):
    """A zero-row UPDATE must raise, never commit silently."""
    with pytest.raises(KeyError):
        registry.attach_venue_order_id(
            "00000000-0000-0000-0000-000000000000",
            "0xvenue_order_no_local_row",
            status="open",
        )


def test_status_check_constraint_rejects_unknown_status(registry: OrderRegistry):
    """A typo'd status must be rejected at write time, not silently stored."""
    now_ms = 1723840000000
    bad = OrderRecord(
        id=str(uuid.uuid4()),
        order_id=None,
        condition_id="0xcond_bad",
        token_id="0xtok_bad",
        side="BUY",
        price=0.40,
        original_size=25.0,
        status="fillled",
        posted_ts=now_ms,
        last_polled_ts=now_ms,
    )
    with pytest.raises(sqlite3.IntegrityError):
        registry.create_order(bad)

    bad_side = OrderRecord(
        id=str(uuid.uuid4()),
        order_id=None,
        condition_id="0xcond_bad2",
        token_id="0xtok_bad2",
        side="buy",
        price=0.40,
        original_size=25.0,
        status="pending",
        posted_ts=now_ms,
        last_polled_ts=now_ms,
    )
    with pytest.raises(sqlite3.IntegrityError):
        registry.create_order(bad_side)


def test_size_matched_float_accumulation_needs_epsilon(registry: OrderRegistry):
    """Forty partial fills of 2.5 sum to 100.0 only within SIZE_EPS."""
    local_id = str(uuid.uuid4())
    now_ms = 1723840000000
    registry.create_order(
        OrderRecord(
            id=local_id,
            order_id="0xvenue_order_eps",
            condition_id="0xcond_eps",
            token_id="0xtok_eps",
            side="BUY",
            price=0.50,
            original_size=100.0,
            status="open",
            posted_ts=now_ms,
            last_polled_ts=now_ms,
        )
    )

    for i in range(40):
        registry.record_fill(
            FillRecord(
                trade_id=f"0xtrade_eps_{i}",
                order_uuid=local_id,
                size=2.5,
                price=0.50,
                venue_ts=now_ms + i,
            )
        )

    matched = registry.get_size_matched(local_id)
    assert abs(matched - 100.0) <= SIZE_EPS
    assert matched >= 100.0 - SIZE_EPS


def test_replayed_fill_sequence_transitions(registry: OrderRegistry):
    """Test 2: Replayed fill sequence produces open -> partial -> filled."""
    local_id = str(uuid.uuid4())
    venue_order_id = "0xvenue_replay_1"
    now_ms = 1723840000000

    order = OrderRecord(
        id=local_id,
        order_id=venue_order_id,
        condition_id="0xcond_rep",
        token_id="0xtok_rep",
        side="BUY",
        price=0.50,
        original_size=10.0,
        status="open",
        posted_ts=now_ms,
        last_polled_ts=now_ms,
    )
    registry.create_order(order)

    # Poll 1: Still resting on venue, no fills -> remains open
    client = MockClobClient(
        open_orders=[{"id": venue_order_id, "size": 10.0, "price": 0.50}],
        trades=[],
    )
    summary1 = reconcile_orders(client, registry, current_ts_ms=now_ms + 5000)
    assert registry.get_order(local_id).status == "open"
    assert registry.get_size_matched(local_id) == 0.0

    # Poll 2: Partial fill of 4 shares -> transitions to partial
    client = MockClobClient(
        open_orders=[{"id": venue_order_id, "size": 6.0, "price": 0.50}],
        trades=[{"id": "tr_1", "order_id": venue_order_id, "size": 4.0, "price": 0.50, "timestamp": now_ms + 6000}],
    )
    summary2 = reconcile_orders(client, registry, current_ts_ms=now_ms + 10000)
    assert registry.get_order(local_id).status == "partial"
    assert registry.get_size_matched(local_id) == 4.0

    # Poll 3: Remaining 6 shares fill -> no longer resting, transitions to filled
    client = MockClobClient(
        open_orders=[],
        trades=[
            {"id": "tr_1", "order_id": venue_order_id, "size": 4.0, "price": 0.50, "timestamp": now_ms + 6000},
            {"id": "tr_2", "order_id": venue_order_id, "size": 6.0, "price": 0.50, "timestamp": now_ms + 12000},
        ],
    )
    summary3 = reconcile_orders(client, registry, current_ts_ms=now_ms + 15000)
    assert registry.get_order(local_id).status == "filled"
    assert registry.get_size_matched(local_id) == 10.0
    assert summary3.orders_filled == 1


def test_restart_equivalence(temp_db: Path):
    """Test 3: Mid-sequence restart produces the same end state as uninterrupted run.

    Reconstructs registry object from disk to ensure persistence across process restarts.
    """
    now_ms = 1723840000000
    local_id = str(uuid.uuid4())
    venue_order_id = "0xvenue_restart"

    # Process 1: Post order and register partial fill
    reg1 = OrderRegistry(db_path=temp_db)
    reg1.create_order(
        OrderRecord(
            id=local_id,
            order_id=venue_order_id,
            condition_id="0xcond_res",
            token_id="0xtok_res",
            side="BUY",
            price=0.45,
            original_size=20.0,
            status="open",
            posted_ts=now_ms,
            last_polled_ts=now_ms,
        )
    )
    client1 = MockClobClient(
        open_orders=[{"id": venue_order_id, "size": 15.0, "price": 0.45}],
        trades=[{"id": "tr_res_1", "order_id": venue_order_id, "size": 5.0, "price": 0.45, "timestamp": now_ms + 2000}],
    )
    reconcile_orders(client1, reg1, current_ts_ms=now_ms + 5000)
    assert reg1.get_order(local_id).status == "partial"
    assert reg1.get_size_matched(local_id) == 5.0

    # Simulate process termination & drop in-memory reference
    del reg1

    # Process 2: Restart against same SQLite file on disk
    reg2 = OrderRegistry(db_path=temp_db)
    loaded_order = reg2.get_order(local_id)
    assert loaded_order is not None
    assert loaded_order.status == "partial"
    assert reg2.get_size_matched(local_id) == 5.0

    # Continue execution: final fill arrives
    client2 = MockClobClient(
        open_orders=[],
        trades=[
            {"id": "tr_res_1", "order_id": venue_order_id, "size": 5.0, "price": 0.45, "timestamp": now_ms + 2000},
            {"id": "tr_res_2", "order_id": venue_order_id, "size": 15.0, "price": 0.45, "timestamp": now_ms + 8000},
        ],
    )
    reconcile_orders(client2, reg2, current_ts_ms=now_ms + 10000)
    final_order = reg2.get_order(local_id)
    assert final_order.status == "filled"
    assert reg2.get_size_matched(local_id) == 20.0


def test_boundary_second_counted_once(registry: OrderRegistry):
    """Test 4: Trade landing in boundary second is counted exactly once."""
    local_id = str(uuid.uuid4())
    venue_order_id = "0xvenue_boundary"
    now_ms = 1723840000000

    registry.create_order(
        OrderRecord(
            id=local_id,
            order_id=venue_order_id,
            condition_id="0xcond_b",
            token_id="0xtok_b",
            side="BUY",
            price=0.50,
            original_size=10.0,
            status="open",
            posted_ts=now_ms,
            last_polled_ts=now_ms,
        )
    )

    # Trade exactly on boundary second (now_ms - 60s overlap boundary)
    trade_boundary = {
        "id": "tr_bound_1",
        "order_id": venue_order_id,
        "size": 5.0,
        "price": 0.50,
        "timestamp": now_ms,
    }
    client = MockClobClient(open_orders=[{"id": venue_order_id}], trades=[trade_boundary])

    # Poll 1
    s1 = reconcile_orders(client, registry, current_ts_ms=now_ms + 5000)
    assert s1.fills_recorded == 1
    assert registry.get_size_matched(local_id) == 5.0

    # Poll 2 (60s overlap re-presents trade_boundary)
    s2 = reconcile_orders(client, registry, current_ts_ms=now_ms + 10000)
    assert s2.fills_recorded == 0
    assert s2.duplicates_ignored == 1
    assert registry.get_size_matched(local_id) == 5.0


def test_duplicate_trade_id_across_polls(registry: OrderRegistry):
    """Test 5: Duplicate trade_id across two polls does not double-count."""
    local_id = str(uuid.uuid4())
    venue_order_id = "0xvenue_dup"
    now_ms = 1723840000000

    registry.create_order(
        OrderRecord(
            id=local_id,
            order_id=venue_order_id,
            condition_id="0xcond_dup",
            token_id="0xtok_dup",
            side="BUY",
            price=0.50,
            original_size=10.0,
            status="open",
            posted_ts=now_ms,
            last_polled_ts=now_ms,
        )
    )

    trade = {"id": "tr_dup_1", "order_id": venue_order_id, "size": 3.0, "price": 0.50, "timestamp": now_ms + 1000}
    client = MockClobClient(open_orders=[{"id": venue_order_id}], trades=[trade])

    s1 = reconcile_orders(client, registry, current_ts_ms=now_ms + 5000)
    assert s1.fills_recorded == 1
    assert s1.duplicates_ignored == 0
    assert registry.get_size_matched(local_id) == 3.0

    # Poll 2 returns same trade
    s2 = reconcile_orders(client, registry, current_ts_ms=now_ms + 10000)
    assert s2.fills_recorded == 0
    assert s2.duplicates_ignored == 1
    assert registry.get_size_matched(local_id) == 3.0


def test_orphan_adoption_and_unattributed(registry: OrderRegistry):
    """Test 6: Orphan venue order is adopted, and an unmatchable one is recorded as unattributed."""
    now_ms = 1723840000000

    # 1. Pending local order waiting for adoption
    local_pending_id = str(uuid.uuid4())
    registry.create_order(
        OrderRecord(
            id=local_pending_id,
            order_id=None,
            condition_id="0xcond_adopt",
            token_id="0xtok_adopt",
            side="BUY",
            price=0.45,
            original_size=15.0,
            status="pending",
            posted_ts=now_ms,
            last_polled_ts=now_ms,
        )
    )

    # Venue reports two open orders:
    # - v_match: matches local_pending_id on (token_id, price, original_size, posted_ts window)
    # - v_unmatch: unmatchable orphan
    v_match = {
        "id": "0xvenue_adopted_1",
        "market": "0xcond_adopt",
        "asset_id": "0xtok_adopt",
        "side": "BUY",
        "price": 0.45,
        "size": 15.0,
        "timestamp": now_ms + 1000,
    }
    v_unmatch = {
        "id": "0xvenue_unattributed_2",
        "market": "0xcond_other",
        "asset_id": "0xtok_other",
        "side": "SELL",
        "price": 0.80,
        "size": 50.0,
        "timestamp": now_ms + 5000,
    }

    client = MockClobClient(open_orders=[v_match, v_unmatch], trades=[])
    summary = reconcile_orders(client, registry, current_ts_ms=now_ms + 6000)

    assert summary.orphans_adopted == 1
    assert summary.unattributed_recorded == 1

    # Check adopted order
    adopted = registry.get_order(local_pending_id)
    assert adopted.order_id == "0xvenue_adopted_1"
    assert adopted.status == "open"

    # Check unattributed order
    unattr = registry.get_order_by_venue_id("0xvenue_unattributed_2")
    assert unattr is not None
    assert unattr.status == "unattributed"
    assert unattr.token_id == "0xtok_other"
    assert unattr.original_size == 50.0


def test_backoff_on_429_or_5xx():
    """Test 7: 429/5xx produces exponential backoff sequence capped at 60s without crashing."""
    # Sequence starting at 2.0s: 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0
    delays = [compute_backoff_delay(err_count=i, base_sec=2.0, max_sec=60.0) for i in range(1, 8)]
    expected = [2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0]
    assert delays == expected


def test_absence_without_trade_evidence_marks_cancelled_not_filled(registry: OrderRegistry):
    """Test 8: Absence from get_open_orders without trade evidence marks row cancelled, NOT filled."""
    local_id = str(uuid.uuid4())
    venue_order_id = "0xvenue_vanished"
    now_ms = 1723840000000

    registry.create_order(
        OrderRecord(
            id=local_id,
            order_id=venue_order_id,
            condition_id="0xcond_v",
            token_id="0xtok_v",
            side="BUY",
            price=0.50,
            original_size=20.0,
            status="open",
            posted_ts=now_ms,
            last_polled_ts=now_ms,
        )
    )

    # Order is absent from venue get_open_orders, and get_trades returns NO fills
    client = MockClobClient(open_orders=[], trades=[])
    summary = reconcile_orders(client, registry, current_ts_ms=now_ms + 5000)

    order = registry.get_order(local_id)
    assert order.status == "cancelled", "A row absent from open orders with no trade evidence MUST be cancelled, not filled"
    assert registry.get_size_matched(local_id) == 0.0
    assert summary.orders_cancelled == 1
    assert summary.orders_filled == 0


class RaisingTradesClient(MockClobClient):
    """A client whose trade query fails, as a 429 or a 5xx would."""

    def get_trades(self, params=None, only_first_page=False, next_cursor=None):
        raise RuntimeError("429 Too Many Requests")


def test_trade_fetch_failure_propagates_and_cancels_nothing(registry: OrderRegistry):
    """A failed trade query must raise, never degrade into an empty trade list.

    Swallowing the error and continuing is the mirror image of marking a row
    filled on absence alone: the order vanished from open orders, we failed to
    ask whether it filled, and reconcile would record `cancelled` for an order
    that actually executed. Stage 4 would then size real money from it.
    """
    local_id = str(uuid.uuid4())
    now_ms = 1723840000000

    registry.create_order(
        OrderRecord(
            id=local_id,
            order_id="0xvenue_during_429",
            condition_id="0xcond_429",
            token_id="0xtok_429",
            side="BUY",
            price=0.50,
            original_size=20.0,
            status="open",
            posted_ts=now_ms,
            last_polled_ts=now_ms,
        )
    )

    client = RaisingTradesClient(open_orders=[], trades=[])
    with pytest.raises(RuntimeError, match="429"):
        reconcile_orders(client, registry, current_ts_ms=now_ms + 5000)

    # The row must be untouched, so the poll loop can back off and retry.
    order = registry.get_order(local_id)
    assert order.status == "open", "a failed trade fetch must not transition any row"


def test_taker_fill_is_attributed(registry: OrderRegistry):
    """A trade carrying only `taker_order_id` must still find its order.

    When we cross the spread our order is the taker. Matching on maker ids alone
    drops every aggressive fill, and `size_matched` silently understates.
    """
    local_id = str(uuid.uuid4())
    venue_order_id = "0xvenue_taker"
    now_ms = 1723840000000

    registry.create_order(
        OrderRecord(
            id=local_id,
            order_id=venue_order_id,
            condition_id="0xcond_taker",
            token_id="0xtok_taker",
            side="BUY",
            price=0.50,
            original_size=30.0,
            status="open",
            posted_ts=now_ms,
            last_polled_ts=now_ms,
        )
    )

    trade = {
        "id": "0xtrade_taker_1",
        "taker_order_id": venue_order_id,
        "size": 12.0,
        "price": 0.50,
        "timestamp": now_ms + 1000,
    }
    client = MockClobClient(open_orders=[{"id": venue_order_id}], trades=[trade])
    summary = reconcile_orders(client, registry, current_ts_ms=now_ms + 5000)

    assert summary.fills_recorded == 1
    assert summary.unmatched_trades == 0
    assert registry.get_size_matched(local_id) == 12.0
    assert registry.get_order(local_id).status == "partial"


def test_unmatched_trade_is_counted_not_dropped(registry: OrderRegistry):
    """A trade we cannot attribute is surfaced, never discarded silently."""
    now_ms = 1723840000000
    trade = {
        "id": "0xtrade_from_nowhere",
        "taker_order_id": "0xventure_we_never_saw",
        "size": 7.5,
        "price": 0.61,
        "timestamp": now_ms + 1000,
    }
    client = MockClobClient(open_orders=[], trades=[trade])
    summary = reconcile_orders(client, registry, current_ts_ms=now_ms + 5000)

    assert summary.unmatched_trades == 1
    assert summary.fills_recorded == 0
    assert any("UNMATCHED_TRADE" in t for t in summary.transitions)


def test_unauthenticated_client_refuses_to_reconcile(registry: OrderRegistry):
    """A client with no L2 creds must be refused before any transition is written.

    `create_or_derive_api_key` swallows the create failure and falls back to
    derive, so a 400 on /auth/api-key is normal and creds are still set. But a
    client that ends up with `creds is None` cannot tell 'no open orders' from
    'never asked', and reconcile would cancel every resting row.
    """
    local_id = str(uuid.uuid4())
    now_ms = 1723840000000

    registry.create_order(
        OrderRecord(
            id=local_id,
            order_id="0xvenue_unauth",
            condition_id="0xcond_unauth",
            token_id="0xtok_unauth",
            side="BUY",
            price=0.50,
            original_size=20.0,
            status="open",
            posted_ts=now_ms,
            last_polled_ts=now_ms,
        )
    )

    client = MockClobClient(open_orders=[], trades=[])
    client.creds = None

    with pytest.raises(PermissionError, match="L2 API credentials"):
        reconcile_orders(client, registry, current_ts_ms=now_ms + 5000)

    assert registry.get_order(local_id).status == "open"


def _pending(registry: OrderRegistry, token: str, price: float, size: float, ts: int) -> str:
    local_id = str(uuid.uuid4())
    registry.create_order(
        OrderRecord(
            id=local_id,
            order_id=None,
            condition_id="0xcond_pool",
            token_id=token,
            side="BUY",
            price=price,
            original_size=size,
            status="pending",
            posted_ts=ts,
            last_polled_ts=ts,
        )
    )
    return local_id


def test_two_venue_orders_cannot_adopt_the_same_pending_row(registry: OrderRegistry):
    """Identical quotes must not collapse onto one local row.

    The match predicate is (token, price, size, posted_ts +/- window) and none
    of it is unique. Two legs quoted at the same price and size inside the
    window is a normal pattern. If the pool is not consumed, both venue orders
    select the same pending row, the second adoption moves `order_id` off the
    first, and that first venue order rests with real money and nothing in the
    registry pointing at it.
    """
    now_ms = 1723840000000
    a = _pending(registry, "0xtok_same", 0.50, 25.0, now_ms)
    b = _pending(registry, "0xtok_same", 0.50, 25.0, now_ms)

    venue = [
        {"id": "0xvenue_A", "asset_id": "0xtok_same", "price": 0.50, "size": 25.0, "timestamp": now_ms},
        {"id": "0xvenue_B", "asset_id": "0xtok_same", "price": 0.50, "size": 25.0, "timestamp": now_ms},
    ]
    client = MockClobClient(open_orders=venue, trades=[])
    summary = reconcile_orders(client, registry, current_ts_ms=now_ms + 5000)

    assert summary.orphans_adopted == 2, "each venue order needs its own pending row"
    assert summary.unattributed_recorded == 0

    bound = {registry.get_order(a).order_id, registry.get_order(b).order_id}
    assert bound == {"0xvenue_A", "0xvenue_B"}, f"one venue order lost its binding: {bound}"


def test_trade_without_any_id_is_not_deduped_against_another(registry: OrderRegistry):
    """Two distinct id-less trades must not collapse into one fills row.

    An empty `trade_id` is not a dedupe key. Keying on "" lets the first id-less
    trade insert and every later one collide with it, so real executed volume
    disappears into `duplicates_ignored`.
    """
    now_ms = 1723840000000
    local_id = str(uuid.uuid4())
    venue_order_id = "0xvenue_noid"
    registry.create_order(
        OrderRecord(
            id=local_id,
            order_id=venue_order_id,
            condition_id="0xcond_noid",
            token_id="0xtok_noid",
            side="BUY",
            price=0.50,
            original_size=20.0,
            status="open",
            posted_ts=now_ms,
            last_polled_ts=now_ms,
        )
    )

    trades = [
        {"order_id": venue_order_id, "size": 4.0, "price": 0.50, "timestamp": now_ms + 1000},
        {"order_id": venue_order_id, "size": 6.0, "price": 0.50, "timestamp": now_ms + 2000},
    ]
    client = MockClobClient(open_orders=[{"id": venue_order_id}], trades=trades)
    summary = reconcile_orders(client, registry, current_ts_ms=now_ms + 5000)

    assert summary.unmatched_trades == 2, "both id-less trades must be surfaced"
    assert summary.duplicates_ignored == 0, "an id-less trade is not a duplicate"
    assert summary.fills_recorded == 0
    assert registry.get_size_matched(local_id) == 0.0


def test_update_order_status_fails_closed_on_missing_row(registry: OrderRegistry):
    """A zero-row status UPDATE must raise, not report a transition it never made."""
    with pytest.raises(KeyError):
        registry.update_order_status(
            "00000000-0000-0000-0000-000000000000", "cancelled", 1723840000000
        )


def test_get_trades_type_error_propagates(registry: OrderRegistry):
    """TypeError raised during get_trades must propagate and not fall back to unfiltered get_trades."""
    now_ms = 1723840000000
    mock_client = MagicMock()
    mock_client.get_open_orders.return_value = []
    # If a fallback existed, the second call would return unfiltered trades instead of failing
    mock_client.get_trades.side_effect = [
        TypeError("internal parsing failure"),
        [{"id": "unfiltered_1", "price": 0.5, "size": 10.0}],
    ]

    with pytest.raises(TypeError, match="internal parsing failure"):
        reconcile_orders(mock_client, registry, current_ts_ms=now_ms)

    assert mock_client.get_trades.call_count == 1

