"""The rehearsal must measure the exit it would really get.

Two defects made the single-leg exit -- the most expensive path in the
strategy -- unmeasurable in a rehearsal, which is the only surface on which
it is safe to measure anything:

1. `shadow_run` pinned `single_buy_grace_sec` to 0.0 unconditionally, so the
   configured grace (45s in the operator's `.env`) never reached the live
   exit path.
   Every stranded leg was dumped on the first pass after the fill, and the knob
   could not be swept. (#619 retired that knob; what it established is that the
   rehearsal must run on the operator's own exit settings.)

2. The rehearsal's market SELL echoed back the limit floor it was handed
   (`best_bid - MAX_SELL_SLIPPAGE`, a flat 2c) as though that were the fill.
   A real market SELL rests against the bid ladder and takes the top of the
   book first. Booking the floor charged every exit a 2c haircut that the
   venue never took, which is most of the "2-3c per stranded leg" the run
   reports.
"""
from contextlib import closing

import time

import pytest

from core_brain.order_registry import OrderRegistry, get_connection, init_db


@pytest.fixture
def registry(tmp_path):
    db = tmp_path / "shadow.db"
    init_db(db)
    return OrderRegistry(db_path=db), db


# --- 1. the configured exit must survive into the rehearsal ---------------
# The pinned knob was `single_buy_grace_sec`, retired in #619. The rule it was
# pinned in violation of is what matters and is asserted here: the rehearsal
# runs on the operator's own exit settings, so what it measures is the exit the
# live path would really take.

def test_shadow_run_keeps_the_configured_exit(monkeypatch):
    """A rehearsal that discards the exit knobs cannot sweep them."""
    monkeypatch.setenv("BOT_RISK_AGED_OUT_RESCUE_LEAD_SEC", "1234")
    monkeypatch.setenv("BOT_RISK_STOP_MODE", "PERCENTAGE_DROP")
    monkeypatch.setenv("BOT_RISK_STOP_MAX_DRAWDOWN_PCT", "20")

    from core_brain.config import load
    from core_brain.shadow_run import shadow_cfg

    assert load().aged_out_rescue_lead_sec == 1234.0
    assert shadow_cfg().aged_out_rescue_lead_sec == 1234.0
    assert shadow_cfg().stop_loss_mode == "PERCENTAGE_DROP"
    assert shadow_cfg().drawdown_pct == 20.0


def test_shadow_run_exit_defaults_when_unset(monkeypatch):
    """Unset stays the shipped baseline, so existing rehearsals do not move."""
    for name in ("BOT_RISK_AGED_OUT_RESCUE_LEAD_SEC", "BOT_RISK_STOP_MODE",
                 "BOT_RISK_STOP_MAX_DRAWDOWN_PCT"):
        monkeypatch.delenv(name, raising=False)

    from core_brain.shadow_run import shadow_cfg

    cfg = shadow_cfg()
    assert cfg.aged_out_rescue_lead_sec == 900.0
    assert cfg.stop_loss_mode == "NONE"
    assert cfg.drawdown_pct == 10.0


# --- 2. the rehearsed SELL must fill against the book ---------------------

def _args(token_id, amount, price):
    from py_clob_client_v2.clob_types import MarketOrderArgsV2
    return MarketOrderArgsV2(token_id=token_id, amount=amount, side="SELL",
                             price=price)


def _client(registry, book):
    from core_brain.shadow_exec import ShadowExecutionClient, ensure_shadow_tables
    reg, db = registry
    ensure_shadow_tables(db)
    return ShadowExecutionClient(reg, db, book_fn=lambda h, t: book)


def test_rehearsed_sell_takes_the_touch_not_the_slippage_floor(registry):
    """5 shares into 20 at the touch fill at the touch, not at the floor."""
    client = _client(registry, {"bids": {0.66: 20.0, 0.65: 30.0}, "asks": {}})

    resp = client.create_and_post_market_order(_args("tok-dn", 5.0, 0.64))

    assert resp["size"] == 5.0
    assert resp["price"] == pytest.approx(0.66)


def test_rehearsed_sell_walks_down_the_ladder_when_the_touch_is_thin(registry):
    """Depth short at the touch pays a real, weighted concession."""
    client = _client(registry, {"bids": {0.66: 2.0, 0.65: 10.0}, "asks": {}})

    resp = client.create_and_post_market_order(_args("tok-dn", 5.0, 0.64))

    # 2 @ 0.66 + 3 @ 0.65 = 3.27 over 5 shares
    assert resp["price"] == pytest.approx(3.27 / 5.0)


def test_rehearsed_sell_never_fills_below_the_floor_it_was_given(registry):
    """The floor is a limit. Depth beneath it is not ours to take."""
    client = _client(registry, {"bids": {0.66: 1.0, 0.60: 100.0}, "asks": {}})

    resp = client.create_and_post_market_order(_args("tok-dn", 5.0, 0.64))

    assert resp["size"] == pytest.approx(1.0)
    assert resp["price"] == pytest.approx(0.66)


def test_rehearsed_sell_falls_back_to_the_floor_without_a_book(registry):
    """No book means no better information -- keep the conservative floor.

    Driven through `_sell_into_bids` rather than through
    `create_and_post_market_order`, deliberately. The old implementation
    returned the requested size at the floor for EVERY book, so asserting that
    outcome through the public method would pass with this change reverted --
    it is the one input where old and new agree. Naming the ladder walk is what
    ties the assertion to the new path.
    """
    client = _client(registry, {"bids": {}, "asks": {}})

    filled, avg = client._sell_into_bids("tok-dn", 5.0, floor=0.64)

    assert filled == pytest.approx(5.0)
    assert avg == pytest.approx(0.64)


def test_rehearsed_buy_walks_the_ask_ladder_and_records_a_short_fill(registry):
    """A thin ask ladder returns only its available, weighted fill."""
    from types import SimpleNamespace

    from core_brain.quotes import QuoteIntent
    from core_brain.shadow_exec import (
        ensure_shadow_tables, record_submit, settle_market,
    )

    reg, db = registry
    ensure_shadow_tables(db)
    market = SimpleNamespace(condition_id="0xabc", market_slug="fake-market")
    intents = [
        QuoteIntent(side="UP", token_id="tok-up", price=0.47, size=2,
                    mid=0.5, edge_vs_mid=0.0),
        QuoteIntent(side="DOWN", token_id="tok-dn", price=0.51, size=2,
                    mid=0.5, edge_vs_mid=0.0),
    ]
    record_submit(
        object(), reg, market, intents, SimpleNamespace(max_pair_cost=0.995),
        db_path=db, book_fn=lambda _host, _token: {"bids": {}},
    )
    settle_market(
        reg, market, db_path=db, seen=set(),
        traded_fn=lambda _condition_id, _seen: {"tok-up": {0.46: 2.0}},
    )

    client = _client(registry, {
        "bids": {},
        "asks": {0.51: 1.0, 0.52: 0.5, 0.54: 100.0},
    })
    from py_clob_client_v2.clob_types import MarketOrderArgsV2

    capped_client = _client(registry, {
        "bids": {}, "asks": {0.51: 100.0},
    })
    capped_size, capped_price = capped_client._buy_from_asks(
        "tok-dn", 1.0, price=0.51,
    )
    assert capped_size == pytest.approx(1.0 / 0.51)
    assert capped_price == pytest.approx(0.51)

    resp = client.create_and_post_market_order(
        MarketOrderArgsV2(token_id="tok-dn", amount=1.0, side="BUY",
                          price=0.51)
    )

    # 1 @ 0.51 + 0.5 @ 0.52 = 0.77 over 1.5 shares; 0.54 is above the 2c ceiling.
    assert resp["size"] == pytest.approx(1.5)
    assert resp["price"] == pytest.approx(0.77 / 1.5)
    completion = reg.get_order(resp["orderID"])
    assert completion.original_size == pytest.approx(1.5)
    assert completion.price == pytest.approx(0.77 / 1.5)
    fill = next(f for f in reg.get_all_fills() if f["order_uuid"] == completion.id)
    assert fill["size"] == pytest.approx(1.5)
    assert fill["price"] == pytest.approx(0.77 / 1.5)
    with closing(get_connection(db)) as conn:
        markout = conn.execute(
            "SELECT fill_price, size FROM markouts WHERE token_id = ?",
            ("tok-dn",),
        ).fetchone()
    assert markout["fill_price"] == pytest.approx(0.77 / 1.5)
    assert markout["size"] == pytest.approx(1.5)


def test_rehearsed_buy_falls_back_to_touch_without_usable_asks(registry):
    client = _client(registry, {"bids": {}, "asks": {}})

    shares, avg = client._buy_from_asks("tok-dn", 1.02, price=0.51)

    assert shares == pytest.approx(2.0)
    assert avg == pytest.approx(0.51)


def test_rehearsed_buy_orders_list_shaped_asks_before_applying_ceiling(registry):
    client = _client(registry, {
        "bids": {},
        "asks": [
            {"price": 0.54, "size": 100.0},
            {"price": 0.52, "size": 0.5},
            {"price": 0.51, "size": 1.0},
        ],
    })

    shares, avg = client._buy_from_asks("tok-dn", 1.0, price=0.51)

    assert shares == pytest.approx(1.5)
    assert avg == pytest.approx(0.77 / 1.5)


# --- 2b. the rehearsed SELL must fill against the pre-decision book -------

def test_rehearsed_sell_fills_against_the_prior_rotations_snapshot(registry):
    """A stored queue-mark drives the exit fill, not the book read at post time.

    Seconds pass between the sweep's decision and the taker SELL landing, and
    the fills we get live are precisely the adversely-selected ones. The
    rehearsal must not read the exit's book fresh: the newest `queue_marks`
    row for the token -- written before the exit sweep ran -- is the book the
    decision was actually made against. Depth at best_bid is used, distinct
    from the order's resting level_size.
    """
    from core_brain.shadow_exec import ensure_shadow_tables, write_queue_mark

    reg, db = registry
    ensure_shadow_tables(db)
    write_queue_mark(
        db, ts=time.time(), condition_id="0xabc", market_slug="fake-market",
        token_id="tok-dn", price=0.51, level_size=10.0, traded=0.0,
        cancel_decay=None, queue_minutes=None, run_id=reg._run_id(),
        best_bid=0.60, best_bid_size=3.0,
    )

    # The fresh book has a much better touch than the snapshot. If the fill
    # matches the fresh touch, the exit read the book it should not have.
    client = _client(registry, {"bids": {0.66: 20.0}, "asks": {}})

    filled, avg = client._sell_into_bids("tok-dn", 5.0, floor=0.58)

    # Snapshot best_bid 0.60, stored best_bid_size 3.0: 3 shares fill at 0.60;
    # the floor (0.58) stops the remaining 2 -- depth beneath it is not ours.
    assert filled == pytest.approx(3.0)
    assert avg == pytest.approx(0.60)


def test_rehearsed_sell_ignores_a_stale_snapshot_and_uses_the_fresh_book(registry):
    """A 901s-old snapshot is not the pre-decision book: the exit prices fresh.

    Mirrors the i492 NBA leg — booked 0.54 off a fill-time snapshot while the
    live book read 0.31-0.34. Under the #488 decoupled loops the decision is
    made in the 1s sentry, so an aged-out leg's snapshot is always stale by
    construction and must never price the exit.
    """
    from core_brain.shadow_exec import ensure_shadow_tables, write_queue_mark

    reg, db = registry
    ensure_shadow_tables(db)
    write_queue_mark(
        db, ts=time.time() - 901.0, condition_id="0xabc",
        market_slug="fake-market",
        token_id="tok-dn", price=0.65, level_size=8.0, traded=0.0,
        cancel_decay=None, queue_minutes=None, run_id=reg._run_id(),
        best_bid=0.54, best_bid_size=8.0,
    )

    client = _client(registry, {"bids": {0.33: 20.0}, "asks": {}})

    filled, avg = client._sell_into_bids("tok-dn", 5.0, floor=0.31)

    assert filled == pytest.approx(5.0)
    assert avg == pytest.approx(0.33)


def test_rehearsed_sell_snapshot_still_respects_a_better_fresh_floor(registry):
    """The floor the caller passes stays a real limit over the snapshot.

    The floor is computed from the decision-time book; if it is worse than
    the snapshot touch the snapshot still applies (it is above the floor),
    and depth below the floor is never taken.
    """
    from core_brain.shadow_exec import ensure_shadow_tables, write_queue_mark

    reg, db = registry
    ensure_shadow_tables(db)
    write_queue_mark(
        db, ts=time.time(), condition_id="0xabc", market_slug="fake-market",
        token_id="tok-dn", price=0.51, level_size=10.0, traded=0.0,
        cancel_decay=None, queue_minutes=None, run_id=reg._run_id(),
        best_bid=0.61, best_bid_size=10.0,
    )

    client = _client(registry, {"bids": {0.66: 20.0}, "asks": {}})

    # Floor 0.60 admits the snapshot touch 0.61; returns 0.61 (distinguishable from 0.60 floor fallback).
    filled, avg = client._sell_into_bids("tok-dn", 4.0, floor=0.60)

    assert filled == pytest.approx(4.0)
    assert avg == pytest.approx(0.61)

    # Floor 0.62 is above the snapshot touch: nothing fills at 0.61, and the
    # fresh book is not consulted -- the conservative floor fallback stands.
    filled2, avg2 = client._sell_into_bids("tok-dn", 4.0, floor=0.62)
    assert filled2 == pytest.approx(4.0)
    assert avg2 == pytest.approx(0.62)


def test_rehearsed_sell_without_a_snapshot_uses_the_fresh_book(registry):
    """No queue-mark row for this run -> the fresh book, exactly as today."""
    client = _client(registry, {"bids": {0.66: 20.0, 0.65: 30.0}, "asks": {}})

    filled, avg = client._sell_into_bids("tok-dn", 5.0, floor=0.64)

    assert filled == pytest.approx(5.0)
    assert avg == pytest.approx(0.66)


def test_rehearsed_sell_ignores_snapshots_from_other_runs(registry):
    """`data/shadow.db` is reused between runs: a stale run's snapshot is not
    this run's pre-decision book, and filling against it would book this run
    an exit price nobody observed."""
    from core_brain.shadow_exec import ensure_shadow_tables, write_queue_mark

    reg, db = registry
    ensure_shadow_tables(db)
    # Valid current-run snapshot
    write_queue_mark(
        db, ts=time.time(), condition_id="0xabc", market_slug="fake-market",
        token_id="tok-dn", price=0.51, level_size=10.0, traded=0.0,
        cancel_decay=None, queue_minutes=None, run_id=reg._run_id(),
        best_bid=0.61, best_bid_size=10.0,
    )
    # Newer snapshot from another run
    write_queue_mark(
        db, ts=2_000.0, condition_id="0xabc", market_slug="fake-market",
        token_id="tok-dn", price=0.51, level_size=10.0, traded=0.0,
        cancel_decay=None, queue_minutes=None, run_id="some-other-run",
        best_bid=0.68, best_bid_size=10.0,
    )

    client = _client(registry, {"bids": {0.66: 20.0}, "asks": {}})

    filled, avg = client._sell_into_bids("tok-dn", 5.0, floor=0.60)

    # Current-run snapshot (0.61) is selected, NOT the foreign snapshot (0.68)
    # and NOT the fresh book (0.66).
    assert filled == pytest.approx(5.0)
    assert avg == pytest.approx(0.61)


def test_rehearsed_sell_without_best_bid_size_does_not_use_level_size(registry):
    """An older queue_marks row lacking best_bid_size must not assume level_size
    applies to best_bid when resting price != best_bid. It falls back to fresh book."""
    from core_brain.shadow_exec import ensure_shadow_tables, write_queue_mark

    reg, db = registry
    ensure_shadow_tables(db)
    write_queue_mark(
        db, ts=time.time(), condition_id="0xabc", market_slug="fake-market",
        token_id="tok-dn", price=0.51, level_size=10.0, traded=0.0,
        cancel_decay=None, queue_minutes=None, run_id=reg._run_id(),
        best_bid=0.60, best_bid_size=None,
    )
    client = _client(registry, {"bids": {0.66: 20.0}, "asks": {}})
    filled, avg = client._sell_into_bids("tok-dn", 5.0, floor=0.64)
    # Falls back to fresh book because best_bid_size is missing
    assert filled == pytest.approx(5.0)
    assert avg == pytest.approx(0.66)


# --- 3. the close must record what the venue reported --------------------

def test_exit_close_records_the_achieved_price_when_the_venue_reports_one():
    """Booking the floor when the response carries a better fill understates
    proceeds, and the understatement is the headline of every rehearsal."""
    from core_brain.single_buy_saver import _exit_fill_price

    assert _exit_fill_price({"price": 0.66, "size": 5.0}, 0.64) == pytest.approx(0.66)


def test_exit_close_keeps_the_floor_when_the_venue_reports_no_price():
    """The live SDK response carries no fills, so the floor stays the record."""
    from core_brain.single_buy_saver import _exit_fill_price

    assert _exit_fill_price({"success": True}, 0.64) == pytest.approx(0.64)
    assert _exit_fill_price(None, 0.64) == pytest.approx(0.64)


def test_exit_close_never_records_a_price_better_than_reported_is_absurd():
    """A response price at or below the floor is still the truth: record it."""
    from core_brain.single_buy_saver import _exit_fill_price

    assert _exit_fill_price({"price": 0.63}, 0.64) == pytest.approx(0.63)


def test_exit_close_records_a_short_fill_as_short():
    """Retiring shares the venue never sold leaves a naked leg reading closed."""
    from core_brain.single_buy_saver import _exit_fill_size

    assert _exit_fill_size({"size": 1.0}, 5.0) == pytest.approx(1.0)


def test_exit_close_never_records_more_than_it_asked_to_sell():
    """An oversell is the one error on this path that cannot be undone."""
    from core_brain.single_buy_saver import _exit_fill_size

    assert _exit_fill_size({"size": 9.0}, 5.0) == pytest.approx(5.0)


def test_exit_close_keeps_the_requested_size_when_the_venue_is_silent():
    from core_brain.single_buy_saver import _exit_fill_size

    assert _exit_fill_size({"success": True}, 5.0) == pytest.approx(5.0)
    assert _exit_fill_size(None, 5.0) == pytest.approx(5.0)
