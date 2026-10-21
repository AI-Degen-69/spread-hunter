"""Hard-stop exits must sell into the fresh book, not the stale snapshot.

Exhibits from the audited run: H1 fired at bid 0.13 but booked 0.24, H2
fired at 0.15 but booked 0.83 -- the stop triggers on the fresh poll book
while the rehearsed SELL fills against the ~13-min-stale `queue_marks`
snapshot. A stop must sell *now*, into the book read inside the exit call.
Other routes keep the pre-decision snapshot (adverse-selection protection).
"""
import time

import pytest

from core_brain.order_registry import CloseRecord, OrderRegistry, init_db


@pytest.fixture
def registry(tmp_path):
    db = tmp_path / "shadow.db"
    init_db(db)
    return OrderRegistry(db_path=db), db


def _args(token_id, amount, price):
    from py_clob_client_v2.clob_types import MarketOrderArgsV2
    return MarketOrderArgsV2(token_id=token_id, amount=amount, side="SELL",
                             price=price)


def _client(registry, book):
    from core_brain.shadow_exec import ShadowExecutionClient, ensure_shadow_tables
    reg, db = registry
    ensure_shadow_tables(db)
    return ShadowExecutionClient(reg, db, book_fn=lambda h, t: book)


def _stale_snapshot(registry, best_bid, best_bid_size):
    from core_brain.shadow_exec import ensure_shadow_tables, write_queue_mark
    reg, db = registry
    ensure_shadow_tables(db)
    write_queue_mark(
        db, ts=time.time(), condition_id="0xabc", market_slug="fake-market",
        token_id="tok-dn", price=0.51, level_size=10.0, traded=0.0,
        cancel_decay=None, queue_minutes=None, run_id=reg._run_id(),
        best_bid=best_bid, best_bid_size=best_bid_size,
    )


def test_hard_stop_sell_uses_fresh_book_not_stale_snapshot(registry):
    """H2 exhibit: stale snapshot 0.83, fresh touch 0.15 -> fill ~0.15."""
    _stale_snapshot(registry, best_bid=0.83, best_bid_size=10.0)
    client = _client(registry, {"bids": {0.15: 20.0}, "asks": {}})

    resp = client.create_and_post_market_order(
        _args("tok-dn", 5.0, 0.13), reason="lifecycle_hard_stop")

    assert resp["size"] == pytest.approx(5.0)
    assert resp["price"] == pytest.approx(0.15)


def test_hard_stop_reason_alias_uses_fresh_book(registry):
    """H1 exhibit via the older `hard_stop` reason string."""
    _stale_snapshot(registry, best_bid=0.24, best_bid_size=10.0)
    client = _client(registry, {"bids": {0.13: 20.0}, "asks": {}})

    resp = client.create_and_post_market_order(
        _args("tok-dn", 5.0, 0.11), reason="hard_stop")

    assert resp["size"] == pytest.approx(5.0)
    assert resp["price"] == pytest.approx(0.13)


def test_sell_into_bids_fresh_flag_skips_snapshot(registry):
    """Direct ladder-walk coverage, independent of the order entry point."""
    _stale_snapshot(registry, best_bid=0.83, best_bid_size=10.0)
    client = _client(registry, {"bids": {0.15: 20.0}, "asks": {}})

    filled, avg, _ = client._sell_into_bids("tok-dn", 5.0, floor=0.13, fresh=True)

    assert filled == pytest.approx(5.0)
    assert avg == pytest.approx(0.15)


def test_non_hard_stop_sell_still_uses_snapshot(registry):
    """Locks existing behavior: every other route keeps the snapshot."""
    _stale_snapshot(registry, best_bid=0.83, best_bid_size=10.0)
    client = _client(registry, {"bids": {0.15: 20.0}, "asks": {}})

    resp = client.create_and_post_market_order(_args("tok-dn", 5.0, 0.13))

    assert resp["size"] == pytest.approx(5.0)
    assert resp["price"] == pytest.approx(0.83)


def test_close_records_trigger_and_fresh_bids(tmp_path):
    """The close row must carry both bids so a future audit can replay this."""
    db = tmp_path / "c.db"
    init_db(db)
    reg = OrderRegistry(db_path=db)
    reg.log_close(CloseRecord(
        ts=1.0, condition_id="0xabc", method="single_buy_exit",
        shares=5.0, dn_price=0.15, cost_basis=3.0, proceeds=0.75,
        realized_pnl=-2.25, reason="lifecycle_hard_stop",
        trigger_bid=0.15, fresh_bid=0.15,
    ))

    closes = reg.get_all_closes()

    assert len(closes) == 1
    assert closes[0]["trigger_bid"] == pytest.approx(0.15)
    assert closes[0]["fresh_bid"] == pytest.approx(0.15)
