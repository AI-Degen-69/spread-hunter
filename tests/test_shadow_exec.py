"""Shadow submit: the decided couple rests in the shadow store, and only there."""
from __future__ import annotations

import pytest

from core_brain.order_registry import OrderRegistry, init_db
from core_brain.quotes import QuoteIntent


class FakeMarket:
    condition_id = "0xabc"
    up_token = "tok-up"
    down_token = "tok-dn"
    market_slug = "fake-market"
    tick_size = 0.01
    neg_risk = False


def _cfg():
    from core_brain.config import load
    return load()


def _intents():
    return [
        QuoteIntent(side="UP", token_id="tok-up", price=0.47, size=20, mid=0.5, edge_vs_mid=0.0),
        QuoteIntent(side="DOWN", token_id="tok-dn", price=0.51, size=20, mid=0.5, edge_vs_mid=0.0),
    ]


def _books(_clob_host, token_id):
    return {"token_id": token_id, "bids": {0.47: 300.0, 0.51: 80.0},
            "asks": {}, "best_bid": 0.47, "best_ask": None}


@pytest.fixture
def registry(tmp_path):
    db = tmp_path / "shadow.db"
    init_db(db)
    return OrderRegistry(db_path=db), db


def test_each_leg_becomes_an_open_order_row_labelled_shadow(registry):
    from core_brain.shadow_exec import ensure_shadow_tables, record_submit

    reg, db = registry
    ensure_shadow_tables(db)

    placed = record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                           db_path=db, book_fn=_books)

    assert placed == 2
    rows = reg.get_active_orders()
    assert {r.token_id for r in rows} == {"tok-up", "tok-dn"}
    assert all(r.status == "open" for r in rows)
    assert all((r.order_id or "").startswith("shadow-") for r in rows)


def test_both_legs_share_one_pair_id(registry):
    from core_brain.shadow_exec import ensure_shadow_tables, record_submit

    reg, db = registry
    ensure_shadow_tables(db)
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=_books)

    pair_ids = {r.pair_id for r in reg.get_active_orders()}
    assert len(pair_ids) == 1
    assert pair_ids.pop().startswith("pair-")


def test_a_carried_pair_id_joins_the_resting_pair(registry):
    # #206: a replacement leg tagged by plan_orders joins the pair whose
    # complement still rests, instead of opening a fresh one-legged pair.
    from core_brain.shadow_exec import ensure_shadow_tables, record_submit

    reg, db = registry
    ensure_shadow_tables(db)
    intents = _intents()[:1]  # only the UP leg is being re-quoted
    intents[0].pair_id = "pair-aaa111"

    placed = record_submit(object(), reg, FakeMarket(), intents, _cfg(),
                           db_path=db, book_fn=_books)

    assert placed == 1
    rows = reg.get_active_orders()
    assert len(rows) == 1
    assert rows[0].pair_id == "pair-aaa111"


def test_untagged_legs_still_get_a_fresh_pair(registry):
    from core_brain.shadow_exec import ensure_shadow_tables, record_submit

    reg, db = registry
    ensure_shadow_tables(db)
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=_books)

    rows = reg.get_active_orders()
    pair_ids = {r.pair_id for r in rows}
    assert len(pair_ids) == 1  # one id, shared by both legs
    assert not any(r.pair_id is None for r in rows)


def test_queue_position_is_captured_from_the_book_at_post_time(registry):
    from core_brain.shadow_exec import (
        ensure_shadow_tables, read_queue_ahead, record_submit,
    )

    reg, db = registry
    ensure_shadow_tables(db)
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=_books)

    by_token = {r.token_id: r.id for r in reg.get_active_orders()}
    run_id = reg._run_id()
    assert read_queue_ahead(db, run_id, by_token["tok-up"]) == 300.0
    assert read_queue_ahead(db, run_id, by_token["tok-dn"]) == 80.0


def test_a_leg_over_max_order_usd_is_refused_before_any_row_is_written(registry):
    from core_brain.shadow_exec import (
        ShadowOrderRefused, ensure_shadow_tables, record_submit,
    )

    reg, db = registry
    ensure_shadow_tables(db)
    huge = [QuoteIntent(side="UP", token_id="tok-up", price=0.90, size=1000, mid=0.5, edge_vs_mid=0.0)]

    with pytest.raises(ShadowOrderRefused, match="MAX_ORDER_USD"):
        record_submit(object(), reg, FakeMarket(), huge, _cfg(),
                      db_path=db, book_fn=_books)

    assert reg.get_active_orders() == []


def test_no_intents_writes_nothing(registry):
    from core_brain.shadow_exec import ensure_shadow_tables, record_submit

    reg, db = registry
    ensure_shadow_tables(db)

    assert record_submit(object(), reg, FakeMarket(), [], _cfg(),
                         db_path=db, book_fn=_books) == 0
    assert reg.get_active_orders() == []


def test_shadow_queue_is_scoped_by_run_id(registry):
    """One run's queue-ahead can never leak into another run's read.

    `data/shadow.db` is reused between rehearsals; queue state keyed by
    `local_id` alone would let a fresh order read an older run's leftover
    queue and under-credit its own tape.
    """
    from core_brain.shadow_exec import (
        ensure_shadow_tables, read_queue_ahead, write_queue_ahead,
    )

    reg, db = registry
    ensure_shadow_tables(db)

    write_queue_ahead(db, "run-aaa", "ord-1", 300.0)
    write_queue_ahead(db, "run-bbb", "ord-1", 0.0)

    assert read_queue_ahead(db, "run-aaa", "ord-1") == 300.0
    assert read_queue_ahead(db, "run-bbb", "ord-1") == 0.0
    # An order that only ever existed in one run is invisible to the other.
    assert read_queue_ahead(db, "run-bbb", "ord-2") == 0.0
    assert read_queue_ahead(db, "run-aaa", "ord-2") == 0.0


def test_write_queue_ahead_updates_the_same_runs_row_in_place(registry):
    from core_brain.shadow_exec import (
        ensure_shadow_tables, read_queue_ahead, write_queue_ahead,
    )

    reg, db = registry
    ensure_shadow_tables(db)
    write_queue_ahead(db, "run-aaa", "ord-1", 300.0)
    write_queue_ahead(db, "run-aaa", "ord-1", 120.0)
    assert read_queue_ahead(db, "run-aaa", "ord-1") == 120.0


def test_shadow_queue_migration_preserves_legacy_rows_as_no_run(registry):
    """A store predating run scoping must be lifted in place: old rows keep
    their data under `run_id = ''` ("nobody's run") and are never mistaken for
    a real run's state."""
    import sqlite3

    from core_brain.shadow_exec import (
        ensure_shadow_tables, read_queue_ahead,
    )

    reg, db = registry
    # Build the legacy pre-run_id schema by hand.
    con = sqlite3.connect(db)
    try:
        con.execute(
            "CREATE TABLE shadow_queue (local_id TEXT PRIMARY KEY, "
            "queue_ahead REAL NOT NULL)")
        con.execute(
            "INSERT INTO shadow_queue (local_id, queue_ahead) VALUES "
            "('legacy-1', 42.0)")
        con.commit()
    finally:
        con.close()

    ensure_shadow_tables(db)

    con = sqlite3.connect(db)
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(shadow_queue)")}
        assert "run_id" in cols
        legacy = con.execute(
            "SELECT run_id, local_id, queue_ahead FROM shadow_queue "
            "WHERE local_id = 'legacy-1'").fetchone()
    finally:
        con.close()
    assert legacy == ("", "legacy-1", 42.0)
    # Reachable only under the empty run id, never under a real run.
    assert read_queue_ahead(db, "", "legacy-1") == 42.0
    assert read_queue_ahead(db, "run-fresh", "legacy-1") == 0.0


def test_pessimistic_completion_price_is_at_least_one_tick_worse():
    from core_brain.shadow_exec import pessimistic_completion_price

    base, tick = 0.51, FakeMarket.tick_size
    pessimistic = pessimistic_completion_price(base, tick)
    assert pessimistic == pytest.approx(base + tick)
    assert pessimistic >= base + tick


def test_mid_loop_failure_cancels_all_created_rows(registry):
    from core_brain.shadow_exec import ensure_shadow_tables, record_submit

    reg, db = registry
    ensure_shadow_tables(db)

    def failing_book_fn(clob_host, token_id):
        # Succeed for UP leg, fail for DOWN leg
        if token_id == "tok-dn":
            raise RuntimeError("Simulated book fetch failure on DOWN leg")
        return _books(clob_host, token_id)

    with pytest.raises(RuntimeError, match="Simulated book fetch failure"):
        record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                      db_path=db, book_fn=failing_book_fn)

    # No rows should remain in active status (they should all be cancelled)
    assert reg.get_active_orders() == []


def test_oserror_on_second_leg_cancels_first_and_propagates(registry):
    from unittest.mock import patch
    from core_brain.shadow_exec import ensure_shadow_tables, record_submit

    reg, db = registry
    ensure_shadow_tables(db)

    original_write_queue_ahead = None
    call_count = [0]  # Use list to allow modification in nested function

    def mock_write_queue_ahead(db_path, run_id, local_id, queue_ahead):
        call_count[0] += 1
        if call_count[0] == 2:  # Fail on second leg
            raise OSError("Simulated system error in write_queue_ahead")
        # Call original for first leg
        return original_write_queue_ahead(db_path, run_id, local_id, queue_ahead)

    import core_brain.shadow_exec
    original_write_queue_ahead = core_brain.shadow_exec.write_queue_ahead

    # OSError from write_queue_ahead should propagate and trigger cancellation
    with patch("core_brain.shadow_exec.write_queue_ahead", side_effect=mock_write_queue_ahead):
        with pytest.raises(OSError, match="Simulated system error"):
            record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                          db_path=db, book_fn=_books)

    # No rows should remain in active status (first order should be cancelled)
    assert reg.get_active_orders() == []


def test_settle_writes_a_fill_row_and_marks_the_order_filled(registry):
    from core_brain.order_registry import inventory_from_registry
    from core_brain.shadow_exec import (
        ensure_shadow_tables, record_submit, settle_market,
    )

    reg, db = registry
    ensure_shadow_tables(db)
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}})

    fills = settle_market(
        reg, FakeMarket(), db_path=db, seen=set(),
        traded_fn=lambda cid, seen: {"tok-up": {0.47: 20.0}},
    )

    assert [f.token_id for f in fills] == ["tok-up"]
    inv = inventory_from_registry("0xabc", "tok-up", "tok-dn", db_path=db)
    assert inv.up_shares == 20.0
    assert inv.down_shares == 0.0
    assert [r.token_id for r in reg.get_active_orders()] == ["tok-dn"]


def test_partial_credit_leaves_the_order_partial(registry):
    from core_brain.shadow_exec import (
        ensure_shadow_tables, record_submit, settle_market,
    )

    reg, db = registry
    ensure_shadow_tables(db)
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}})

    settle_market(reg, FakeMarket(), db_path=db, seen=set(),
                  traded_fn=lambda cid, seen: {"tok-up": {0.47: 5.0}})

    up_row = next(r for r in reg.get_active_orders() if r.token_id == "tok-up")
    assert up_row.status == "partial"


def test_a_legacy_null_run_order_is_never_this_run_s(registry):
    """A `run_id = NULL` row is nobody's order, never this rehearsal's.

    `data/shadow.db` is reused between rehearsals and predates run scoping, so
    it can hold open rows whose `run_id` is NULL. `(o.run_id or mine) == mine`
    adopted those as ours: their tape was credited to them in posting order,
    ahead of this run's own orders on the same market. Ownership must match
    exactly -- an unowned row stays unowned.
    """
    import sqlite3

    from core_brain.order_registry import OrderRecord
    from core_brain.shadow_exec import (
        ensure_shadow_tables, record_submit, settle_market,
    )

    reg, db = registry
    ensure_shadow_tables(db)
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}})

    # A legacy row from before run scoping existed: same market, same token,
    # still open, but with no owner.
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO orders (id, order_id, condition_id, token_id, side,"
            " price, original_size, status, posted_ts, last_polled_ts, run_id)"
            " VALUES ('legacy-1', 'shadow-legacy-1', '0xabc', 'tok-up', 'BUY',"
            " 0.47, 100.0, 'open', 1.0, 1.0, NULL)",
        )
        conn.commit()
    finally:
        conn.close()

    settle_market(reg, FakeMarket(), db_path=db, seen=set(),
                  traded_fn=lambda cid, seen: {"tok-up": {0.47: 20.0}})

    legacy = [r for r in reg.get_active_orders()
              if (r.order_id or "") == "shadow-legacy-1"]
    assert legacy, "the legacy row fixture must be present and active"
    # Observable: the order is still resting (not dropped). NOT credited: the
    # 20.0 of tape at 0.47 must not fill it -- only orders this run owns may
    # consume this run's tape.
    assert legacy[0].status == "open", (
        "a NULL-run row must remain visible as a resting order")
    assert legacy[0].original_size == 100.0, (
        "the NULL-run row must not consume this run's tape -- it stays unfilled")


def test_the_same_tape_volume_is_never_credited_twice(registry):
    """`recent_trades` de-duplicates by trade identity through `seen`; the
    settle step carries one `seen` set per market for the whole session."""
    from core_brain.order_registry import inventory_from_registry
    from core_brain.shadow_exec import (
        ensure_shadow_tables, record_submit, settle_market,
    )

    reg, db = registry
    ensure_shadow_tables(db)
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}})

    calls = {"n": 0}

    def traded_fn(cid, seen):
        calls["n"] += 1
        return {"tok-up": {0.47: 8.0}} if calls["n"] == 1 else {}

    seen = set()
    settle_market(reg, FakeMarket(), db_path=db, seen=seen, traded_fn=traded_fn)
    settle_market(reg, FakeMarket(), db_path=db, seen=seen, traded_fn=traded_fn)

    inv = inventory_from_registry("0xabc", "tok-up", "tok-dn", db_path=db)
    assert inv.up_shares == 8.0


def test_a_shrinking_queue_without_a_fill_is_remembered(registry):
    from core_brain.shadow_exec import (
        ensure_shadow_tables, read_queue_ahead, record_submit, settle_market,
    )

    reg, db = registry
    ensure_shadow_tables(db)
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db,
                  book_fn=lambda h, t: {"bids": {0.47: 50.0, 0.51: 0.0}})

    settle_market(reg, FakeMarket(), db_path=db, seen=set(),
                  traded_fn=lambda cid, seen: {"tok-up": {0.47: 30.0}})

    up_id = next(r.id for r in reg.get_active_orders() if r.token_id == "tok-up")
    assert read_queue_ahead(db, reg._run_id(), up_id) == 20.0


def test_settle_calls_traded_fn_even_with_no_resting_orders(registry):
    """settle_market always calls traded_fn to keep `seen` current, even when
    there are no resting orders. Without this, a fresh order's first settle
    would credit tape volume from before it was posted."""
    from core_brain.shadow_exec import ensure_shadow_tables, settle_market

    reg, db = registry
    ensure_shadow_tables(db)

    calls = {"n": 0}

    def traded_fn(cid, seen):
        calls["n"] += 1
        return {}

    # Call settle with no resting orders — traded_fn must still be called to
    # update `seen`
    result = settle_market(reg, FakeMarket(), db_path=db, seen=set(),
                           traded_fn=traded_fn)

    assert calls["n"] == 1
    assert result == []


def test_stale_tape_does_not_credit_to_fresh_orders(registry):
    """Sequence: settle with nothing resting (seen is updated), then
    record_submit a new order, then settle again. The second settle must only
    see what traded_fn returns on that call, not the stale volume from the
    first settle."""
    from core_brain.order_registry import inventory_from_registry
    from core_brain.shadow_exec import (
        ensure_shadow_tables, record_submit, settle_market,
    )

    reg, db = registry
    ensure_shadow_tables(db)

    tape_history = {"call": 0}

    def tape_feed(cid, seen):
        tape_history["call"] += 1
        # First call (no orders resting): return stale 50 units
        # Second call (order now resting): return only fresh 10 units
        if tape_history["call"] == 1:
            return {"tok-up": {0.47: 50.0}}
        else:
            return {"tok-up": {0.47: 10.0}}

    seen = set()

    # First settle with no resting orders — seen is updated with the 50 units
    settle_market(reg, FakeMarket(), db_path=db, seen=seen, traded_fn=tape_feed)

    # Record a new order
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}})

    # Second settle with the order now resting — only 10 units are available
    # because the earlier 50 units were already in `seen`
    settle_market(reg, FakeMarket(), db_path=db, seen=seen, traded_fn=tape_feed)

    inv = inventory_from_registry("0xabc", "tok-up", "tok-dn", db_path=db)
    assert inv.up_shares == 10.0


def test_a_single_buy_waits_without_crossing_in_the_shadow_store(registry):
    """The lifecycle keeps the resting complement and records patient wait."""
    from core_brain.order_registry import inventory_from_registry
    from core_brain.shadow_exec import (
        ShadowExecutionClient, ensure_shadow_tables, record_submit,
        settle_market, shadow_positions,
    )
    from core_brain.single_buy_saver import auto_manage_pairs

    reg, db = registry
    ensure_shadow_tables(db)
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}})
    settle_market(reg, FakeMarket(), db_path=db, seen=set(),
                  traded_fn=lambda cid, seen: {"tok-up": {0.47: 20.0}})

    # `markets.parse_book`'s canonical shape -- bids/asks as PRICE-KEYED
    # DICTS -- exactly what `book_fn` returns in production
    # (`shadow_run._default_fetch_books` -> `markets.full_book` ->
    # `markets.parse_book`). `ShadowExecutionClient.get_order_book` is the
    # thing under test that adapts this into the list-of-levels shape
    # `single_buy_saver._book_levels` wants; feeding the client the already-
    # adapted shape here would test nothing about that adaptation.
    def book_fn(_clob_host, token_id):
        return {"token_id": token_id, "bids": {0.50: 500.0},
                "asks": {0.51: 500.0}, "best_bid": 0.50, "best_ask": 0.51,
                "malformed": 0}

    client = ShadowExecutionClient(reg, db, book_fn=book_fn)
    results = auto_manage_pairs(
        client, reg, _cfg(), venue_positions=shadow_positions(reg, db),
    )

    assert results, "auto_manage_pairs produced no result for the naked pair"
    assert results[0]["action"] == "patient_wait", results
    assert reg.get_lifecycle_state(results[0]["pair_id"]).state == "PATIENT_WAIT"

    inv = inventory_from_registry("0xabc", "tok-up", "tok-dn", db_path=db)
    assert inv.up_shares == pytest.approx(20.0)
    assert inv.down_shares == pytest.approx(0.0)
    assert not any(fill["token_id"] == "tok-dn"
                   for fill in reg.get_all_fills())


def test_completion_refuses_when_pair_has_empty_condition_id(registry):
    """A pair row with a NULL or empty condition_id cannot be completed: the
    condition_id is the key that ties an order to its market, and without it,
    inventory_from_registry has no way to track the position. Accepting an
    empty condition_id would produce a completion row invisible to the pair
    it belongs to.
    """
    from py_clob_client_v2.clob_types import MarketOrderArgsV2

    from core_brain.order_registry import OrderRecord, FillRecord
    from core_brain.shadow_exec import (
        ShadowExecutionClient, ShadowOrderRefused, ensure_shadow_tables,
    )

    reg, db = registry
    ensure_shadow_tables(db)

    # Hand-craft a pair with empty condition_id in the store.
    pair_id = "pair-empty-cid-test"
    local_id_up = str(__import__("uuid").uuid4())
    local_id_dn = str(__import__("uuid").uuid4())
    now_ms = int(__import__("time").time() * 1000)

    reg.create_order(OrderRecord(
        id=local_id_up,
        order_id="shadow-test-up",
        condition_id="", token_id="tok-up", side="BUY",
        price=0.47, original_size=20, status="open",
        posted_ts=now_ms, last_polled_ts=now_ms, pair_id=pair_id
    ))
    reg.create_order(OrderRecord(
        id=local_id_dn,
        order_id="shadow-test-dn",
        condition_id="", token_id="tok-dn", side="BUY",
        price=0.51, original_size=20, status="open",
        posted_ts=now_ms, last_polled_ts=now_ms, pair_id=pair_id
    ))

    # Record a fill on tok-up to make tok-dn the naked leg.
    reg.record_fill(FillRecord(
        trade_id="shadow-fill-up",
        order_uuid=local_id_up,
        size=20,
        price=0.47
    ))

    client = ShadowExecutionClient(reg, db, book_fn=lambda h, t: {})

    # Attempting to complete tok-dn should raise ShadowOrderRefused.
    with pytest.raises(Exception) as exc_info:
        client.create_and_post_market_order(
            MarketOrderArgsV2(token_id="tok-dn", amount=10.2, side="BUY",
                              price=0.51))

    # Verify it's the right exception and mentions the pair and token.
    from core_brain.shadow_exec import ShadowOrderRefused
    assert isinstance(exc_info.value, ShadowOrderRefused)
    assert "tok-dn" in str(exc_info.value) or "pair" in str(exc_info.value).lower()

    # Verify no new order was written to the store.
    all_orders = reg.get_all_orders()
    # Should still be the 2 we created (the tok-up and tok-dn naked pair).
    order_ids = [o["id"] for o in all_orders]
    assert len([oid for oid in order_ids if oid in (local_id_up, local_id_dn)]) == 2
    # Verify no new shadow- order was added.
    new_shadow_orders = [o for o in all_orders if o["id"] not in (local_id_up, local_id_dn)]
    assert len(new_shadow_orders) == 0


def test_a_completion_lands_on_the_in_window_pair_not_the_stale_one(registry):
    """Two pairs are naked on tok-dn: one half-filled two hours ago, one
    half-filled now. The completion under test belongs to the FRESH one,
    because that is the only one `auto_manage_pairs` acts on.

    `MarketOrderArgsV2` carries no pair id, so the shim reconstructs which pair
    a completion belongs to. Picking the OLDEST naked pair (an earlier version
    did) is backwards: `auto_manage_pairs` SKIPS any pair whose last fill is
    older than `pairs_exit_window_sec`, so the oldest naked pair is precisely
    the one it never acts on. `data/shadow.db` is not deleted between runs, so
    stale naked pairs accumulate -- and every completion the pass makes for a
    fresh pair got booked to a stale one instead, leaving the fresh pair naked
    to be bought again next cycle. With N stale pairs that is N+1 completion
    buys.
    """
    from py_clob_client_v2.clob_types import MarketOrderArgsV2

    from core_brain.shadow_exec import (
        ShadowExecutionClient, ensure_shadow_tables, record_submit,
        settle_market,
    )

    reg, db = registry
    ensure_shadow_tables(db)
    now = 1_700_000_000.0
    two_hours_ago = now - 7200.0

    # Stale pair: tok-up filled two hours ago, tok-dn still naked. Out of the
    # 900s window, so the pairs pass will never route it.
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}},
                  now_fn=lambda: two_hours_ago)
    settle_market(reg, FakeMarket(), db_path=db, seen=set(),
                  traded_fn=lambda cid, seen: {"tok-up": {0.47: 20.0}},
                  now_fn=lambda: two_hours_ago)
    stale_pair_id = next(
        r.pair_id for r in reg.get_active_orders() if r.token_id == "tok-dn")

    # Fresh pair on the SAME tokens, half-filled now.
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}},
                  now_fn=lambda: now)
    settle_market(reg, FakeMarket(), db_path=db, seen=set(),
                  traded_fn=lambda cid, seen: {"tok-up": {0.47: 20.0}},
                  now_fn=lambda: now)
    fresh_pair_id = next(
        r.pair_id for r in reg.get_active_orders()
        if r.token_id == "tok-dn" and r.pair_id != stale_pair_id)
    assert fresh_pair_id != stale_pair_id

    client = ShadowExecutionClient(reg, db, book_fn=lambda h, t: {},
                                   window_sec=900.0, now_fn=lambda: now)
    resp = client.create_and_post_market_order(
        MarketOrderArgsV2(token_id="tok-dn", amount=10.2, side="BUY",
                          price=0.51))

    completion_order = reg.get_order(resp["orderID"])
    assert completion_order is not None
    assert completion_order.pair_id == fresh_pair_id
    assert completion_order.pair_id != stale_pair_id


def test_a_completion_is_refused_when_no_naked_pair_exists(registry):
    """No naked pair on the token means nothing could have asked for this buy.
    Booking it anyway would credit shares to a position nothing is managing;
    refusing says so out loud instead.

    Before #311 this test seeded a pair whose only fill was OUT of window and
    expected the same refusal, because `auto_manage_pairs` was the only arm
    that could complete a pair. That premise is gone: a second arm completes
    aged-out pairs by design, so an out-of-window naked pair is now a real
    target (see `test_a_completion_is_booked_to_the_aged_out_naked_pair`).
    The refusal itself is what survives, and it is pinned here against a pair
    that is BALANCED -- two filled legs, nothing naked, nothing to attach to.
    """
    from py_clob_client_v2.clob_types import MarketOrderArgsV2

    from core_brain.shadow_exec import (
        ShadowExecutionClient, ShadowOrderRefused, ensure_shadow_tables,
        record_submit, settle_market,
    )

    reg, db = registry
    ensure_shadow_tables(db)
    now = 1_700_000_000.0
    long_ago = now - 7200.0

    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}},
                  now_fn=lambda: long_ago)
    # Both legs filled: the pair is whole, so no pair is naked here.
    settle_market(reg, FakeMarket(), db_path=db, seen=set(),
                  traded_fn=lambda cid, seen: {"tok-up": {0.47: 20.0},
                                               "tok-dn": {0.51: 20.0}},
                  now_fn=lambda: long_ago)

    client = ShadowExecutionClient(reg, db, book_fn=lambda h, t: {},
                                   window_sec=900.0, now_fn=lambda: now)

    with pytest.raises(ShadowOrderRefused):
        client.create_and_post_market_order(
            MarketOrderArgsV2(token_id="tok-dn", amount=10.2, side="BUY",
                              price=0.51))



def test_a_completion_is_booked_to_the_aged_out_naked_pair(registry):
    """The aged-out arm's completion must be attributable (#311).

    Its pair's last fill is outside the window by construction, so an
    in-window-only attribution rule would refuse every completion that arm
    makes -- the rehearsal would book its exits and lose its completions. The
    in-window set keeps priority; this pins the fallback.
    """
    from py_clob_client_v2.clob_types import MarketOrderArgsV2

    from core_brain.shadow_exec import (
        ShadowExecutionClient, ensure_shadow_tables, record_submit,
        settle_market,
    )

    reg, db = registry
    ensure_shadow_tables(db)
    now = 1_700_000_000.0
    long_ago = now - 7200.0

    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}},
                  now_fn=lambda: long_ago)
    settle_market(reg, FakeMarket(), db_path=db, seen=set(),
                  traded_fn=lambda cid, seen: {"tok-up": {0.47: 20.0}},
                  now_fn=lambda: long_ago)

    aged_pair_id = [o["pair_id"] for o in reg.get_all_orders()
                    if o["token_id"] == "tok-up"][0]

    client = ShadowExecutionClient(reg, db, book_fn=lambda h, t: {},
                                   window_sec=900.0, now_fn=lambda: now)
    resp = client.create_and_post_market_order(
        MarketOrderArgsV2(token_id="tok-dn", amount=10.2, side="BUY",
                          price=0.51))

    assert reg.get_order(resp["orderID"]).pair_id == aged_pair_id


def test_two_aged_out_naked_pairs_on_one_token_are_refused_not_guessed(
        registry):
    """Ambiguity is refused, because the wrong answer is a double buy.

    A market re-quoted across rotations accumulates pair_ids, and the aged-out
    arm acts on the OLDEST first (the fills ledger orders by `recorded_ts`),
    while attribution would have to pick one. Guessing books the completion to
    the other pair, which then reads over-filled while the intended one stays
    naked and is bought again next rotation -- the N+1 defect this function
    exists to prevent. Refusing is loud and costs one completion.
    """
    from py_clob_client_v2.clob_types import MarketOrderArgsV2

    from core_brain.shadow_exec import (
        ShadowExecutionClient, ShadowOrderRefused, ensure_shadow_tables,
        record_submit, settle_market,
    )

    reg, db = registry
    ensure_shadow_tables(db)
    now = 1_700_000_000.0
    long_ago = now - 7200.0

    # Two rounds of the same market, each leaving only tok-up filled.
    for _ in range(2):
        record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                      db_path=db, book_fn=lambda h, t: {"bids": {}},
                      now_fn=lambda: long_ago)
        settle_market(reg, FakeMarket(), db_path=db, seen=set(),
                      traded_fn=lambda cid, seen: {"tok-up": {0.47: 20.0}},
                      now_fn=lambda: long_ago)

    client = ShadowExecutionClient(reg, db, book_fn=lambda h, t: {},
                                   window_sec=900.0, now_fn=lambda: now)

    with pytest.raises(ShadowOrderRefused) as err:
        client.create_and_post_market_order(
            MarketOrderArgsV2(token_id="tok-dn", amount=10.2, side="BUY",
                              price=0.51))

    assert "aged-out" in str(err.value)
    assert [o for o in reg.get_all_orders()
            if o["token_id"] == "tok-dn" and o["status"] == "filled"] == []


def test_an_in_window_pair_still_wins_over_an_aged_out_one(registry):
    """The fresh pair keeps priority, so the in-window path is unchanged."""
    from py_clob_client_v2.clob_types import MarketOrderArgsV2

    from core_brain.shadow_exec import (
        ShadowExecutionClient, ensure_shadow_tables, record_submit,
        settle_market,
    )

    reg, db = registry
    ensure_shadow_tables(db)
    now = 1_700_000_000.0
    long_ago = now - 7200.0

    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}},
                  now_fn=lambda: long_ago)
    settle_market(reg, FakeMarket(), db_path=db, seen=set(),
                  traded_fn=lambda cid, seen: {"tok-up": {0.47: 20.0}},
                  now_fn=lambda: long_ago)
    stale_pair = [o["pair_id"] for o in reg.get_all_orders()
                  if o["token_id"] == "tok-up"][0]

    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}},
                  now_fn=lambda: now)
    settle_market(reg, FakeMarket(), db_path=db, seen=set(),
                  traded_fn=lambda cid, seen: {"tok-up": {0.47: 20.0}},
                  now_fn=lambda: now)
    fresh_pair = [o["pair_id"] for o in reg.get_all_orders()
                  if o["token_id"] == "tok-up"][-1]

    client = ShadowExecutionClient(reg, db, book_fn=lambda h, t: {},
                                   window_sec=900.0, now_fn=lambda: now)
    resp = client.create_and_post_market_order(
        MarketOrderArgsV2(token_id="tok-dn", amount=10.2, side="BUY",
                          price=0.51))

    booked = reg.get_order(resp["orderID"]).pair_id
    assert booked == fresh_pair
    assert booked != stale_pair


def test_a_named_target_wins_over_an_in_window_pair(registry):
    """The arm that is crossing names its pair, and the name wins (#311).

    The fallback rule above cannot be right for the aged-out arm when an
    in-window pair also exists on the token: the fresh pair wins by preference,
    the completion fill is booked to it, and the aged-out pair -- the one the
    rescue actually crossed for -- still reads naked and is bought AGAIN next
    rotation. `bind_completion_target` is how the caller that knows the pair
    says so; the fallback stays for callers that do not.
    """
    from py_clob_client_v2.clob_types import MarketOrderArgsV2

    from core_brain.shadow_exec import (
        ShadowExecutionClient, ensure_shadow_tables, record_submit,
        settle_market,
    )

    reg, db = registry
    ensure_shadow_tables(db)
    now = 1_700_000_000.0
    long_ago = now - 7200.0

    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}},
                  now_fn=lambda: long_ago)
    settle_market(reg, FakeMarket(), db_path=db, seen=set(),
                  traded_fn=lambda cid, seen: {"tok-up": {0.47: 20.0}},
                  now_fn=lambda: long_ago)
    aged_pair = [o["pair_id"] for o in reg.get_all_orders()
                 if o["token_id"] == "tok-up"][0]

    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}},
                  now_fn=lambda: now)
    settle_market(reg, FakeMarket(), db_path=db, seen=set(),
                  traded_fn=lambda cid, seen: {"tok-up": {0.47: 20.0}},
                  now_fn=lambda: now)
    fresh_pair = [o["pair_id"] for o in reg.get_all_orders()
                  if o["token_id"] == "tok-up"][-1]
    assert fresh_pair != aged_pair

    client = ShadowExecutionClient(reg, db, book_fn=lambda h, t: {},
                                   window_sec=900.0, now_fn=lambda: now)
    client.bind_completion_target(aged_pair)
    resp = client.create_and_post_market_order(
        MarketOrderArgsV2(token_id="tok-dn", amount=10.2, side="BUY",
                          price=0.51))

    assert reg.get_order(resp["orderID"]).pair_id == aged_pair
    assert reg.get_order(resp["orderID"]).pair_id != fresh_pair


def test_a_binding_that_names_a_pair_with_nothing_naked_is_refused(registry):
    """A named target that the store does not agree is naked fails LOUD.

    Falling back to another pair here is the mis-attribution this binding
    exists to prevent, and it fails silently -- the rehearsal would report a
    completion for a position the caller never touched.
    """
    from py_clob_client_v2.clob_types import MarketOrderArgsV2

    from core_brain.shadow_exec import (
        ShadowExecutionClient, ShadowOrderRefused, ensure_shadow_tables,
        record_submit, settle_market,
    )

    reg, db = registry
    ensure_shadow_tables(db)
    now = 1_700_000_000.0
    long_ago = now - 7200.0

    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}},
                  now_fn=lambda: long_ago)
    # Both legs filled: this pair has nothing naked to complete.
    settle_market(reg, FakeMarket(), db_path=db, seen=set(),
                  traded_fn=lambda cid, seen: {"tok-up": {0.47: 20.0},
                                               "tok-dn": {0.51: 20.0}},
                  now_fn=lambda: long_ago)
    whole_pair = reg.get_all_orders()[0]["pair_id"]

    client = ShadowExecutionClient(reg, db, book_fn=lambda h, t: {},
                                   window_sec=900.0, now_fn=lambda: now)
    client.bind_completion_target(whole_pair)
    orders_before = len(reg.get_all_orders())

    with pytest.raises(ShadowOrderRefused) as err:
        client.create_and_post_market_order(
            MarketOrderArgsV2(token_id="tok-dn", amount=10.2, side="BUY",
                              price=0.51))

    assert "does not read as naked" in str(err.value)
    # Nothing was booked: a refused cross writes no order and no fill.
    assert len(reg.get_all_orders()) == orders_before


def test_a_spent_binding_is_not_re_aimed_at_its_own_pair(registry):
    """One binding, one cross -- a target must not survive to the next call.

    The rescue binds its pair immediately before crossing and clears it after;
    the shim clears it on use as well, so a binding can never aim a later,
    unrelated completion at a pair that already got its shares.
    """
    from py_clob_client_v2.clob_types import MarketOrderArgsV2

    from core_brain.shadow_exec import (
        ShadowExecutionClient, ensure_shadow_tables, record_submit,
        settle_market,
    )

    reg, db = registry
    ensure_shadow_tables(db)
    now = 1_700_000_000.0
    long_ago = now - 7200.0

    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}},
                  now_fn=lambda: long_ago)
    settle_market(reg, FakeMarket(), db_path=db, seen=set(),
                  traded_fn=lambda cid, seen: {"tok-up": {0.47: 20.0}},
                  now_fn=lambda: long_ago)
    aged_pair = [o["pair_id"] for o in reg.get_all_orders()
                 if o["token_id"] == "tok-up"][0]

    client = ShadowExecutionClient(reg, db, book_fn=lambda h, t: {},
                                   window_sec=900.0, now_fn=lambda: now)
    client.bind_completion_target(aged_pair)
    resp = client.create_and_post_market_order(
        MarketOrderArgsV2(token_id="tok-dn", amount=10.2, side="BUY",
                          price=0.51))
    assert reg.get_order(resp["orderID"]).pair_id == aged_pair

    # A market re-quoted this rotation leaves a fresh naked pair on the same
    # token. The spent binding must not be re-aimed at `aged_pair` (which is
    # whole now) or the second cross falls over: it belongs to the fresh pair.
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}},
                  now_fn=lambda: now)
    settle_market(reg, FakeMarket(), db_path=db, seen=set(),
                  traded_fn=lambda cid, seen: {"tok-up": {0.47: 20.0}},
                  now_fn=lambda: now)
    fresh_pair = [o["pair_id"] for o in reg.get_all_orders()
                  if o["token_id"] == "tok-up"][-1]

    second = client.create_and_post_market_order(
        MarketOrderArgsV2(token_id="tok-dn", amount=10.2, side="BUY",
                          price=0.51))

    assert reg.get_order(second["orderID"]).pair_id == fresh_pair


def test_the_shim_refuses_a_method_it_does_not_implement(registry):
    """A silent no-op for an unimplemented SDK call would make a rehearsal look
    successful where the live path would have done something."""
    from core_brain.shadow_exec import ShadowExecutionClient

    reg, db = registry
    client = ShadowExecutionClient(reg, db, book_fn=lambda h, t: {})

    with pytest.raises(AttributeError):
        client.post_orders([])


def test_a_balanced_pair_is_merged_into_one_dollar_per_share(registry):
    from core_brain.shadow_exec import (
        ensure_shadow_tables, record_shadow_merges, record_submit, settle_market,
    )

    reg, db = registry
    ensure_shadow_tables(db)
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}})
    settle_market(reg, FakeMarket(), db_path=db, seen=set(),
                  traded_fn=lambda cid, seen: {"tok-up": {0.47: 20.0},
                                               "tok-dn": {0.51: 20.0}})

    merged = record_shadow_merges(reg, db)

    assert len(merged) == 1
    close = reg.get_all_closes()[0]
    assert close["method"] == "shadow_merge"
    assert close["shares"] == 20.0
    assert round(close["proceeds"], 2) == 20.00
    assert round(close["cost_basis"], 2) == round(20 * (0.47 + 0.51), 2)
    assert round(close["realized_pnl"], 2) == round(20 * (1.0 - 0.98), 2)


def test_an_unbalanced_pair_is_left_alone(registry):
    from core_brain.shadow_exec import (
        ensure_shadow_tables, record_shadow_merges, record_submit, settle_market,
    )

    reg, db = registry
    ensure_shadow_tables(db)
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}})
    settle_market(reg, FakeMarket(), db_path=db, seen=set(),
                  traded_fn=lambda cid, seen: {"tok-up": {0.47: 20.0}})

    assert record_shadow_merges(reg, db) == []
    assert reg.get_all_closes() == []


def test_a_merged_pair_is_not_merged_again(registry):
    from core_brain.shadow_exec import (
        ensure_shadow_tables, record_shadow_merges, record_submit, settle_market,
    )

    reg, db = registry
    ensure_shadow_tables(db)
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}})
    settle_market(reg, FakeMarket(), db_path=db, seen=set(),
                  traded_fn=lambda cid, seen: {"tok-up": {0.47: 20.0},
                                               "tok-dn": {0.51: 20.0}})

    assert len(record_shadow_merges(reg, db)) == 1
    assert record_shadow_merges(reg, db) == []
    assert len(reg.get_all_closes()) == 1


def test_a_pair_that_fills_incrementally_records_merges_incrementally(registry):
    """A pair filled 10/10 at same prices, merged, then fills another 10/10 should
    produce a SECOND close for the newly merged 10 shares, not be excluded outright."""
    from core_brain.shadow_exec import (
        ensure_shadow_tables, record_shadow_merges, record_submit, settle_market,
    )

    reg, db = registry
    ensure_shadow_tables(db)
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}})

    # Cycle 1: settle 10/20 on each leg at original prices
    settle_market(reg, FakeMarket(), db_path=db, seen=set(),
                  traded_fn=lambda cid, seen: {"tok-up": {0.47: 10.0},
                                               "tok-dn": {0.51: 10.0}})

    # First merge records 10 shares
    merged_1 = record_shadow_merges(reg, db)
    assert len(merged_1) == 1
    closes_after_first = reg.get_all_closes()
    assert len(closes_after_first) == 1
    first_close = closes_after_first[0]
    assert first_close["shares"] == 10.0

    # Cycle 2: settle remaining 10/10 on each leg
    settle_market(reg, FakeMarket(), db_path=db, seen=set(),
                  traded_fn=lambda cid, seen: {"tok-up": {0.47: 10.0},
                                               "tok-dn": {0.51: 10.0}})

    # Second merge records the additional 10 shares
    merged_2 = record_shadow_merges(reg, db)
    assert len(merged_2) == 1
    closes_after_second = reg.get_all_closes()
    assert len(closes_after_second) == 2
    second_close = closes_after_second[1]
    assert second_close["shares"] == 10.0
    # Both closes should reference the same logical pair (tx_hash starts with same pair_id)
    first_pair_id = first_close["tx_hash"].split(":")[0]
    second_pair_id = second_close["tx_hash"].split(":")[0]
    assert first_pair_id == second_pair_id
    # Sum of merged shares never exceeds min(leg fills) = 20
    total_merged = sum(c["shares"] for c in closes_after_second)
    assert total_merged == 20.0


def test_incremental_merges_charge_actual_incremental_cost(registry):
    """Incremental merges charge only the incremental cost, not an average.
    Verifies the reconciliation property: sum of cost_basis across closes
    equals the apportioned target cost. Directly constructs fills to avoid
    credit_fills' price-matching constraints."""
    from core_brain.order_registry import FillRecord

    reg, db = registry
    from core_brain.shadow_exec import ensure_shadow_tables, record_shadow_merges, record_submit

    ensure_shadow_tables(db)
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}})

    # Manually construct fills to simulate two cycles with different prices
    # Cycle 1: 10 shares at 0.47 (UP) and 0.51 (DN), cost = 9.8
    orders = reg.get_active_orders()
    by_token = {o.token_id: o for o in orders}
    up_id = by_token["tok-up"].id
    dn_id = by_token["tok-dn"].id

    reg.record_fill(FillRecord(
        trade_id="shadow-fill-1", order_uuid=up_id, size=10.0, price=0.47,
    ))
    reg.record_fill(FillRecord(
        trade_id="shadow-fill-2", order_uuid=dn_id, size=10.0, price=0.51,
    ))
    reg.update_order_status(up_id, status="partial", last_polled_ts=int(__import__("time").time() * 1000))
    reg.update_order_status(dn_id, status="partial", last_polled_ts=int(__import__("time").time() * 1000))

    # First merge: 10 shares at avg cost 9.8
    merged_1 = record_shadow_merges(reg, db)
    assert len(merged_1) == 1
    closes_c1 = reg.get_all_closes()
    assert len(closes_c1) == 1
    close_c1 = closes_c1[0]
    assert close_c1["shares"] == 10.0
    assert round(close_c1["cost_basis"], 2) == 9.80

    # Cycle 2: add 10 more shares at 0.40 (UP) and 0.45 (DN), incremental cost = 8.5
    # Cumulative: (10@0.47 + 10@0.40) + (10@0.51 + 10@0.45) = 18.3
    reg.record_fill(FillRecord(
        trade_id="shadow-fill-3", order_uuid=up_id, size=10.0, price=0.40,
    ))
    reg.record_fill(FillRecord(
        trade_id="shadow-fill-4", order_uuid=dn_id, size=10.0, price=0.45,
    ))
    reg.update_order_status(up_id, status="filled", last_polled_ts=int(__import__("time").time() * 1000))
    reg.update_order_status(dn_id, status="filled", last_polled_ts=int(__import__("time").time() * 1000))

    # Second merge: 10 more shares, cost_basis must be 8.5 (incremental cost)
    merged_2 = record_shadow_merges(reg, db)
    assert len(merged_2) == 1, f"Expected 1 pair merged in cycle 2, got {merged_2}"
    closes_c2 = reg.get_all_closes()
    assert len(closes_c2) == 2
    close_c2 = closes_c2[1]
    assert close_c2["shares"] == 10.0
    # Cost basis must be incremental cost (8.5), not average (9.15)
    assert round(close_c2["cost_basis"], 2) == 8.50, (
        f"Expected cost_basis 8.50 (incremental), got {close_c2['cost_basis']}. "
        "Calculation is averaging instead of tracking incremental cost."
    )
    # Reconciliation property: sum of cost_basis == target cost
    # Target = (4.7 + 5.1) + (4.0 + 4.5) = 18.3
    total_cost = round(sum(c["cost_basis"] for c in closes_c2), 2)
    assert total_cost == 18.30, (
        f"Sum of cost_basis should be 18.30 (target cost), got {total_cost}. "
        "Reconciliation property violated."
    )


def test_a_shadow_merge_takes_its_shares_and_its_cost_out_of_inventory(registry):
    """The decision reads `inventory_from_registry`. A merge that the decision
    cannot see is a rehearsal whose inventory only ever grows, so
    `max_cost_per_market` and `max_fills_per_market` trip sooner than they
    would live.

    `inventory_from_registry` matches close methods by exact string, so
    `'shadow_merge'` has to be recognised there beside `'merge'`, and the
    close has to carry `up_cost_removed`/`dn_cost_removed` the way the live
    merge path does -- otherwise the shares leave and the money does not.
    """
    from core_brain.order_registry import inventory_from_registry
    from core_brain.shadow_exec import (
        ensure_shadow_tables, record_shadow_merges, record_submit, settle_market,
    )

    reg, db = registry
    ensure_shadow_tables(db)
    # Both legs at the same price so the live path's even cost split is exact
    # and the assertion below is about recognition, not about rounding.
    intents = [
        QuoteIntent(side="UP", token_id="tok-up", price=0.48, size=20,
                    mid=0.5, edge_vs_mid=0.0),
        QuoteIntent(side="DOWN", token_id="tok-dn", price=0.48, size=20,
                    mid=0.5, edge_vs_mid=0.0),
    ]
    record_submit(object(), reg, FakeMarket(), intents, _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}})
    settle_market(reg, FakeMarket(), db_path=db, seen=set(),
                  traded_fn=lambda cid, seen: {"tok-up": {0.48: 20.0},
                                               "tok-dn": {0.48: 20.0}})

    before = inventory_from_registry("0xabc", "tok-up", "tok-dn", db_path=db)
    assert before.up_shares == pytest.approx(20.0)

    assert len(record_shadow_merges(reg, db)) == 1

    after = inventory_from_registry("0xabc", "tok-up", "tok-dn", db_path=db)
    assert after.up_shares == pytest.approx(0.0)
    assert after.down_shares == pytest.approx(0.0)
    assert after.up_cost == pytest.approx(0.0)
    assert after.down_cost == pytest.approx(0.0)


def test_a_shadow_merge_removes_exactly_the_cost_it_closed(registry):
    """Whatever the split between the legs, the two removals sum to the close's
    own cost basis -- the same accounting the live merge writes."""
    from core_brain.shadow_exec import (
        ensure_shadow_tables, record_shadow_merges, record_submit, settle_market,
    )

    reg, db = registry
    ensure_shadow_tables(db)
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}})
    settle_market(reg, FakeMarket(), db_path=db, seen=set(),
                  traded_fn=lambda cid, seen: {"tok-up": {0.47: 20.0},
                                               "tok-dn": {0.51: 20.0}})

    record_shadow_merges(reg, db)

    close = reg.get_all_closes()[0]
    assert (close["up_cost_removed"] + close["dn_cost_removed"]
            == pytest.approx(close["cost_basis"]))


def test_a_live_shaped_store_is_untouched_by_shadow_merge_recognition(tmp_path):
    """The one live-module edit on this branch is additive by construction: no
    live store can hold a `shadow_merge` row, because only
    `core_brain/shadow_exec.py` writes that string and it only ever writes to a
    shadow store. This pins that a store carrying live method strings only
    reads exactly as it did before.
    """
    from core_brain.order_registry import (
        CloseRecord, FillRecord, OrderRecord, OrderRegistry, init_db,
        inventory_from_registry,
    )

    db = tmp_path / "live_shaped.db"
    init_db(db)
    reg = OrderRegistry(db_path=db)

    now_ms = int(__import__("time").time() * 1000)
    for local_id, token, price in (("live-up", "tok-up", 0.47),
                                   ("live-dn", "tok-dn", 0.51)):
        reg.create_order(OrderRecord(
            id=local_id, order_id=f"venue-{local_id}", condition_id="0xlive",
            token_id=token, side="BUY", price=price, original_size=20,
            status="filled", posted_ts=now_ms, last_polled_ts=now_ms,
            pair_id="pair-live"))
        reg.record_fill(FillRecord(
            trade_id=f"trade-{local_id}", order_uuid=local_id, size=20,
            price=price, venue_ts=now_ms, recorded_ts=now_ms))

    # A live merge of 5 shares, recorded exactly as core_brain/order_manager
    # records one.
    reg.log_close(CloseRecord(
        ts=1.0, condition_id="0xlive", method="merge", shares=5.0,
        cost_basis=4.9, proceeds=5.0, fee=0.0, gas=0.0, realized_pnl=0.1,
        up_cost_removed=2.45, dn_cost_removed=2.45, tx_hash="0xdeadbeef"))

    inv = inventory_from_registry("0xlive", "tok-up", "tok-dn", db_path=db)

    assert inv.up_shares == pytest.approx(15.0)
    assert inv.down_shares == pytest.approx(15.0)
    assert inv.up_cost == pytest.approx(20 * 0.47 - 2.45)
    assert inv.down_cost == pytest.approx(20 * 0.51 - 2.45)


def test_each_rested_leg_is_logged_in_the_quotes_ledger(registry):
    """`trader_loop._submit_intents` logs a `QuoteRecord` per leg, and the
    quotes ledger is the registry's only record of which token is the UP leg
    and which the DOWN. Without it a shadow store can rest orders and take
    fills, but nothing downstream can name a side.
    """
    from core_brain.shadow_exec import ensure_shadow_tables, record_submit

    reg, db = registry
    ensure_shadow_tables(db)

    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=_books)

    quotes = reg.get_all_quotes()
    assert {q["token_id"]: q["side"] for q in quotes} == {
        "tok-up": "UP", "tok-dn": "DOWN"}
    assert all(q["condition_id"] == "0xabc" for q in quotes)
    assert all(q["market_slug"] == "fake-market" for q in quotes)
    # The quote row names the order it belongs to, both ways round, exactly as
    # the live one does -- otherwise it cannot be tied back to a fill.
    order_by_token = {o.token_id: o for o in reg.get_active_orders()}
    for q in quotes:
        row = order_by_token[q["token_id"]]
        assert q["local_id"] == row.id
        assert q["order_id"] == row.order_id
        assert str(q["order_id"]).startswith("shadow-")


def test_the_exit_branch_reaches_a_decision_instead_of_refusing_on_the_side(
        registry):
    """The half of the pairs pass that owns the pair-over-$1.00 case.

    `exit_single_buy` resolves UP/DOWN through `_token_side`, which reads the
    quotes ledger, and refuses outright when it comes back None -- before
    `should_exit` is ever consulted. A shadow store that rested orders without
    logging quotes could therefore never take an exit: every pair came back
    `action: 'error'`, "the quotes ledger has no side for it".
    """
    from core_brain.shadow_exec import (
        ShadowExecutionClient, ensure_shadow_tables, record_submit,
        settle_market, shadow_positions,
    )
    from core_brain.single_buy_saver import exit_single_buy

    reg, db = registry
    ensure_shadow_tables(db)
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}})
    settle_market(reg, FakeMarket(), db_path=db, seen=set(),
                  traded_fn=lambda cid, seen: {"tok-up": {0.47: 20.0}})
    pair_id = next(o.pair_id for o in reg.get_active_orders())

    def book_fn(_host, token_id):
        # The light leg's ask has run away: 0.47 + 0.60 is well over the cap,
        # so the pass routes to the exit rather than the completion.
        if token_id == "tok-dn":
            return {"bids": {0.55: 500.0}, "asks": {0.60: 500.0}}
        return {"bids": {0.46: 500.0}, "asks": {0.49: 500.0}}

    client = ShadowExecutionClient(reg, db, book_fn=book_fn)
    result = exit_single_buy(
        client, reg, pair_id, 0.995, live=True,
        venue_positions=shadow_positions(reg, db))

    assert result["action"] == "exited"
    assert result["side"] == "UP"
    assert result["size"] == pytest.approx(20.0)


def _leg(reg, *, pair_id, local_id, token, price, size, posted_ts):
    """One filled leg row plus its fill, written straight to the store.

    Hand-built rather than driven through `record_submit`/`settle_market`
    because the state under test needs several rows on ONE leg at different
    prices -- which is what a completion buy landing on a pair produces, and
    what the tape-driven path (fills credit at the resting order's own price)
    cannot express in one order.
    """
    from core_brain.order_registry import FillRecord, OrderRecord

    reg.create_order(OrderRecord(
        id=local_id, order_id=f"shadow-{local_id}", condition_id="0xabc",
        token_id=token, side="BUY", price=price, original_size=size,
        status="filled", posted_ts=posted_ts, last_polled_ts=posted_ts,
        pair_id=pair_id))
    reg.record_fill(FillRecord(
        trade_id=f"shadow-{local_id}", order_uuid=local_id, size=size,
        price=price, venue_ts=posted_ts, recorded_ts=posted_ts))


def test_a_merge_charges_each_leg_its_remaining_average_cost(registry):
    """Every merged share is charged, and charged what it actually cost.

    The apportioned model this replaced derived a close's cost by subtracting
    what earlier closes charged from a recomputed cumulative target. Because
    the target scaled each leg's cost by `min_fills / that leg's shares`, a
    later round of cheap fills on one leg could pull it BELOW the amount
    already charged -- and the floor that stopped a negative cost then booked
    the whole $1.00 payout as profit, on shares that were bought with money.

    A leg's shares leave at the average price of the shares still in it, so
    the sum of every close's cost equals the cost of the shares merged. That
    is the property asserted here; the fabricated profit is the thing it makes
    impossible.
    """
    from core_brain.shadow_exec import ensure_shadow_tables, record_shadow_merges

    reg, db = registry
    ensure_shadow_tables(db)
    pair = "pair-drifting-cost"

    # Cycle 1: a balanced 10/10 pair, both legs bought at 0.90.
    _leg(reg, pair_id=pair, local_id="up-1", token="tok-up", price=0.90,
         size=10, posted_ts=1000)
    _leg(reg, pair_id=pair, local_id="dn-1", token="tok-dn", price=0.90,
         size=10, posted_ts=1000)
    assert record_shadow_merges(reg, db) == [pair]

    # Cycle 2: 10 cheap DOWN shares and 2 more expensive UP shares. Two more
    # pairs are mergeable, and they cost 0.90 + 0.01 each -- not nothing.
    _leg(reg, pair_id=pair, local_id="dn-2", token="tok-dn", price=0.01,
         size=10, posted_ts=2000)
    _leg(reg, pair_id=pair, local_id="up-2", token="tok-up", price=0.90,
         size=2, posted_ts=2000)
    assert record_shadow_merges(reg, db) == [pair]

    closes = reg.get_all_closes()
    assert len(closes) == 2

    second = closes[1]
    assert second["shares"] == pytest.approx(2.0)
    # Remaining UP average is 0.90 (2 shares, $1.80 left after the first close
    # took 10 shares and $9.00); remaining DOWN average is 0.01.
    assert second["cost_basis"] == pytest.approx(2 * (0.90 + 0.01))
    assert second["cost_basis"] > 0.0, "a merge booked shares as free"
    assert second["realized_pnl"] == pytest.approx(
        second["proceeds"] - second["cost_basis"])

    # Reconciliation: 12 UP shares ($10.80, all of them) and 12 DOWN shares
    # ($9.00 for the first ten, $0.02 for the next two) were merged.
    assert sum(c["cost_basis"] for c in closes) == pytest.approx(19.82)


def test_a_lost_cache_row_costs_one_merge_and_then_heals(registry):
    """`shadow_merge_legs` is a cache, and a cache can go missing.

    `registry.log_close` commits on its own connection, so the close row and
    the per-leg rows behind it cannot be written in one transaction from here.
    A store older than the table, or a crash between the two commits, leaves a
    `shadow_merge` close with no leg rows -- and the fallback that then prices
    the pair is the apportioned estimate this replaced. Paying that estimate
    once is the cost of the gap. Paying it on every later merge, because
    nothing ever wrote the missing rows, would be the bug.
    """
    import sqlite3

    from core_brain.shadow_exec import ensure_shadow_tables, record_shadow_merges

    reg, db = registry
    ensure_shadow_tables(db)
    pair = "pair-lost-cache"

    _leg(reg, pair_id=pair, local_id="up-1", token="tok-up", price=0.90,
         size=10, posted_ts=1000)
    _leg(reg, pair_id=pair, local_id="dn-1", token="tok-dn", price=0.90,
         size=10, posted_ts=1000)
    assert record_shadow_merges(reg, db) == [pair]

    # The close survives; its cache rows do not.
    con = sqlite3.connect(db)
    con.execute("DELETE FROM shadow_merge_legs")
    con.commit()
    con.close()

    _leg(reg, pair_id=pair, local_id="dn-2", token="tok-dn", price=0.01,
         size=10, posted_ts=2000)
    _leg(reg, pair_id=pair, local_id="up-2", token="tok-up", price=0.90,
         size=2, posted_ts=2000)
    assert record_shadow_merges(reg, db) == [pair]

    # The rows are back: what the first close took, plus what the second did.
    con = sqlite3.connect(db)
    rebuilt = {
        token: (round(shares, 4), round(cost, 4))
        for token, shares, cost in con.execute(
            "SELECT token_id, SUM(shares), SUM(cost) FROM shadow_merge_legs "
            "GROUP BY token_id")
    }
    con.close()
    assert set(rebuilt) == {"tok-up", "tok-dn"}
    assert rebuilt["tok-up"][0] == pytest.approx(12.0)
    assert rebuilt["tok-dn"][0] == pytest.approx(12.0)

    # A third merge now prices off those rows rather than re-deriving the
    # estimate, so it charges the two cheap DOWN shares what they cost.
    _leg(reg, pair_id=pair, local_id="up-3", token="tok-up", price=0.50,
         size=8, posted_ts=3000)
    assert record_shadow_merges(reg, db) == [pair]

    third = reg.get_all_closes()[2]
    assert third["shares"] == pytest.approx(8.0)
    assert third["cost_basis"] > 0.0, (
        "the third merge handed over shares as if they were free")


def test_one_lost_cache_row_out_of_several_is_recovered_too(registry):
    """A shortfall in the cache, not only an empty one, has to heal.

    A pair that has merged more than once holds a cache row per merge. Losing
    one of them leaves a total that is positive but short, so a test for
    "no rows at all" never fires: the pair keeps pricing off an understated
    consumed total, and nothing puts the missing shares back.
    """
    import sqlite3

    from core_brain.shadow_exec import ensure_shadow_tables, record_shadow_merges

    reg, db = registry
    ensure_shadow_tables(db)
    pair = "pair-partial-cache"

    _leg(reg, pair_id=pair, local_id="up-1", token="tok-up", price=0.60,
         size=10, posted_ts=1000)
    _leg(reg, pair_id=pair, local_id="dn-1", token="tok-dn", price=0.30,
         size=10, posted_ts=1000)
    assert record_shadow_merges(reg, db) == [pair]

    _leg(reg, pair_id=pair, local_id="up-2", token="tok-up", price=0.55,
         size=10, posted_ts=2000)
    _leg(reg, pair_id=pair, local_id="dn-2", token="tok-dn", price=0.35,
         size=10, posted_ts=2000)
    assert record_shadow_merges(reg, db) == [pair]

    # Twenty shares of each leg have been merged across two closes, and the
    # cache holds a row per close. Lose exactly one of the UP rows.
    con = sqlite3.connect(db)
    con.execute(
        "DELETE FROM shadow_merge_legs WHERE token_id = 'tok-up' "
        "AND rowid = (SELECT MIN(rowid) FROM shadow_merge_legs "
        "             WHERE token_id = 'tok-up')")
    short = con.execute(
        "SELECT SUM(shares) FROM shadow_merge_legs "
        "WHERE token_id = 'tok-up'").fetchone()[0]
    con.commit()
    con.close()
    assert 0 < short < 20, (
        "the fixture needs a cache that is short, not one that is empty")

    _leg(reg, pair_id=pair, local_id="up-3", token="tok-up", price=0.50,
         size=5, posted_ts=3000)
    _leg(reg, pair_id=pair, local_id="dn-3", token="tok-dn", price=0.40,
         size=5, posted_ts=3000)
    assert record_shadow_merges(reg, db) == [pair]

    closes = reg.get_all_closes()
    merged_shares = sum(float(c["shares"]) for c in closes)

    con = sqlite3.connect(db)
    cached = dict(con.execute(
        "SELECT token_id, SUM(shares) FROM shadow_merge_legs GROUP BY token_id"))
    con.close()

    # Both legs give up the same shares in a merge, so each token's cached
    # total is the cumulative share count in `closes`.
    assert cached["tok-up"] == pytest.approx(merged_shares)
    assert cached["tok-dn"] == pytest.approx(merged_shares)
    assert closes[2]["cost_basis"] > 0.0


def test_the_position_view_drops_shares_a_merge_already_closed(registry):
    """`shadow_positions` is the oversell pre-flight, so it must not over-report.

    `auto_manage_pairs` compares the size it wants to sell against what this
    view says is held. Summing every fill and never subtracting a close leaves
    merged shares in the answer, and the check then passes for shares the same
    store shows as gone.
    """
    from core_brain.shadow_exec import (ensure_shadow_tables,
                                        record_shadow_merges, shadow_positions)

    reg, db = registry
    ensure_shadow_tables(db)
    pair = "pair-merged-away"

    _leg(reg, pair_id=pair, local_id="up-1", token="tok-up", price=0.47,
         size=10, posted_ts=1000)
    _leg(reg, pair_id=pair, local_id="dn-1", token="tok-dn", price=0.51,
         size=10, posted_ts=1000)

    assert shadow_positions(reg, db) == {"tok-up": 10.0, "tok-dn": 10.0}

    assert record_shadow_merges(reg, db) == [pair]

    assert shadow_positions(reg, db) == {}, (
        "the merge took both legs out of the store, so the position view "
        "must not still report them as held")


def test_the_position_view_drops_the_leg_a_single_buy_exit_sold(registry):
    """An exit sells ONE leg. The other one is still held, and still reported."""
    from core_brain.order_registry import CloseRecord, QuoteRecord
    from core_brain.shadow_exec import ensure_shadow_tables, shadow_positions

    reg, db = registry
    ensure_shadow_tables(db)

    _leg(reg, pair_id="pair-naked", local_id="up-1", token="tok-up",
         price=0.60, size=10, posted_ts=1000)
    _leg(reg, pair_id="pair-naked", local_id="dn-1", token="tok-dn",
         price=0.30, size=4, posted_ts=1000)
    # The quotes ledger is where UP/DOWN lives; the closes table has no token
    # column, so this is the same mapping `single_buy_saver._token_side` reads.
    for token, side in (("tok-up", "UP"), ("tok-dn", "DOWN")):
        reg.log_quote(QuoteRecord(
            ts=1000.0, market_slug="m", condition_id="0xabc", token_id=token,
            side=side, price=0.5, size=1.0))

    # Six UP shares sold. `up_price` set and `dn_price` unset is how the exit
    # path records which leg went.
    reg.log_close(CloseRecord(
        ts=2000.0, condition_id="0xabc", method="single_buy_exit", shares=6.0,
        cost_basis=3.6, proceeds=3.9, fee=0.0, gas=0.0, realized_pnl=0.3,
        up_price=0.65, up_cost_removed=3.6))

    assert shadow_positions(reg, db) == {"tok-up": 4.0, "tok-dn": 4.0}


def test_no_merge_cycle_books_a_profit_it_did_not_make(registry):
    """Three merge cycles on one pair whose average cost MOVES between them.

    Rows arriving on one leg at a very different price -- a completion buy is
    exactly that -- are what broke the apportioned cost model this replaced.
    The guarantee now is stronger than "never negative": every close charges
    the shares it took at what they cost, so no cycle can book a payout it did
    not earn, and none can hand over shares for free either. The third cycle
    has nothing left to merge and must stay silent.

    `test_a_merge_charges_each_leg_its_remaining_average_cost` pins the exact
    figures; this one pins the properties that must hold for every cycle.
    """
    from core_brain.shadow_exec import ensure_shadow_tables, record_shadow_merges

    reg, db = registry
    ensure_shadow_tables(db)
    pair = "pair-drifting-cost"

    # Cycle 1: a balanced 10/10 pair bought expensively on both legs.
    _leg(reg, pair_id=pair, local_id="up-1", token="tok-up", price=0.90,
         size=10, posted_ts=1000)
    _leg(reg, pair_id=pair, local_id="dn-1", token="tok-dn", price=0.90,
         size=10, posted_ts=1000)
    assert record_shadow_merges(reg, db) == [pair]

    # Cycle 2: more shares land on the pair at prices nothing like the first
    # round -- 10 cheap DOWN shares and 2 more expensive UP shares. The
    # apportioned total now sits BELOW the cost the first close already took.
    _leg(reg, pair_id=pair, local_id="dn-2", token="tok-dn", price=0.01,
         size=10, posted_ts=2000)
    _leg(reg, pair_id=pair, local_id="up-2", token="tok-up", price=0.90,
         size=2, posted_ts=2000)
    assert record_shadow_merges(reg, db) == [pair]

    # Cycle 3: nothing new to merge.
    record_shadow_merges(reg, db)

    closes = reg.get_all_closes()
    assert len(closes) == 2
    for c in closes:
        assert c["cost_basis"] > 0.0, (
            "a merge handed over shares that cost money as if they were free")
        assert c["realized_pnl"] <= c["proceeds"] + 1e-9, (
            "a merge booked more profit than the merge paid")
        assert c["realized_pnl"] < c["proceeds"], (
            "a merge booked the whole payout as profit")
        assert c["up_cost_removed"] + c["dn_cost_removed"] == pytest.approx(
            c["cost_basis"])
