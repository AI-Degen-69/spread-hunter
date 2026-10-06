"""Why an order was cancelled, and the queue it gave up (#131).

On shadow-02 the loop posted 25 orders in 20 minutes, cancelled 23, and filled
none. Median quote lifetime 37 s, minimum 6 s. The best queue position of the
run — a bid with 25 shares ahead of it — was re-quoted away six seconds after it
was posted. Reading the registry afterwards, that cancel is indistinguishable
from one that saved us from a pair over $1.00: `orders` recorded a status and
nothing else.

So every cancel now records its reason and the queue it held. The hold rule is
the payoff, and it ships OFF (`requote_hold_queue_shares = 0.0`) precisely
because choosing the threshold is what the record is for.
"""
from __future__ import annotations

import sqlite3

import pytest

from core_brain.config import MakerConfig
from core_brain.order_registry import SCHEMA, OrderRegistry, OrderRecord
from core_brain.quotes import QuoteIntent
from core_brain.trader_loop import (
    CANCEL_NOT_QUOTED,
    CANCEL_PRICE_MOVED,
    CANCEL_REGATE_PAIR_COST,
    plan_orders,
)

UP = "tok-up"
DOWN = "tok-down"


def _order(order_id: str, token: str, price: float) -> dict:
    return {"id": order_id, "order_id": order_id, "token_id": token,
            "price": price, "side": "BUY"}


def _intent(token: str, price: float) -> QuoteIntent:
    return QuoteIntent(side="BUY", token_id=token, price=price, size=5,
                       mid=price + 0.02, edge_vs_mid=0.02)


# --- attribution ------------------------------------------------------------

def test_a_drifted_order_is_held_and_records_nothing():
    # #387: drift alone holds; the retired pair-cost re-gate (0.60 + 0.52)
    # no longer fires either. A wanted token records no reason.
    orders = [_order("o1", UP, 0.60)]
    reasons: dict = {}

    # Act
    to_cancel, to_submit = plan_orders(
        orders, [_intent(UP, 0.57)], dead_band=0.01,
        cfg=MakerConfig(max_completable_pair_cost=1.00),
        hedge_asks={UP: 0.52}, reasons=reasons)

    # Assert
    assert to_cancel == []
    assert to_submit == []
    assert reasons == {}


def test_a_token_we_no_longer_quote_is_recorded_separately():
    # Arrange — no intent for this token at all. That is a different event
    # from a price move, and conflating them would hide a market dropping out
    # of the universe inside the churn count.
    orders = [_order("o1", UP, 0.50)]
    reasons: dict = {}

    # Act
    to_cancel, _ = plan_orders(orders, [_intent(DOWN, 0.40)], reasons=reasons)

    # Assert
    assert to_cancel == orders
    assert reasons == {"o1": CANCEL_NOT_QUOTED}


def test_a_failing_gate_on_a_wanted_token_records_nothing():
    # #387: the retired re-gate (0.60 + 0.60 = 1.20) does not fire on a
    # wanted token. Held, no reason.
    cfg = MakerConfig()
    orders = [_order("o1", UP, 0.60)]
    reasons: dict = {}

    # Act
    to_cancel, to_submit = plan_orders(
        orders, [_intent(UP, 0.60)], cfg=cfg,
        hedge_asks={UP: 0.60}, reasons=reasons)

    # Assert
    assert to_cancel == []
    assert to_submit == []
    assert reasons == {}


def test_an_order_that_is_kept_records_no_reason():
    # Arrange
    orders = [_order("o1", UP, 0.50)]
    reasons: dict = {}

    # Act
    to_cancel, _ = plan_orders(orders, [_intent(UP, 0.50)], reasons=reasons)

    # Assert
    assert to_cancel == []
    assert reasons == {}


def test_the_reasons_map_is_optional():
    # Arrange / Act — every existing caller passes nothing and must keep working.
    # Place-and-wait (#384): a wanted token is held, so nothing submits either.
    to_cancel, to_submit = plan_orders([_order("o1", UP, 0.50)],
                                       [_intent(UP, 0.40)])

    # Assert
    assert to_cancel == []
    assert to_submit == []


# --- the hold ---------------------------------------------------------------

def test_a_near_front_order_survives_a_price_move():
    # Arrange — 25 shares ahead, the position shadow-02 threw away.
    cfg = MakerConfig()
    orders = [_order("o1", UP, 0.50)]
    reasons: dict = {}

    # Act
    to_cancel, _ = plan_orders(
        orders, [_intent(UP, 0.40)], dead_band=0.03, cfg=cfg,
        hedge_asks={UP: 0.40}, reasons=reasons,
        queue_ahead={"o1": 25.0}, hold_queue_shares=200.0)

    # Assert — kept, and no cancel reason because there was no cancel.
    assert to_cancel == []
    assert reasons == {}


def test_an_order_deep_in_the_queue_is_still_re_quoted():
    # Arrange — 12,930 shares ahead. Place-and-wait (#384): a wanted token is
    # held at its own price regardless of queue depth.
    cfg = MakerConfig()
    orders = [_order("o1", UP, 0.50)]
    reasons: dict = {}

    # Act
    to_cancel, _ = plan_orders(
        orders, [_intent(UP, 0.40)], dead_band=0.03, cfg=cfg,
        hedge_asks={UP: 0.40}, reasons=reasons,
        queue_ahead={"o1": 12930.0}, hold_queue_shares=200.0)

    # Assert
    assert to_cancel == []
    assert reasons == {}


def test_nothing_overrides_the_hold_not_even_the_gate():
    # #387: front of the queue with a failing pair-cost gate -- held.
    # Cost is judged at placement, never after.
    cfg = MakerConfig()
    orders = [_order("o1", UP, 0.60)]
    reasons: dict = {}

    # Act
    to_cancel, _ = plan_orders(
        orders, [_intent(UP, 0.40)], dead_band=0.03, cfg=cfg,
        hedge_asks={UP: 0.60}, reasons=reasons,
        queue_ahead={"o1": 1.0}, hold_queue_shares=200.0)

    # Assert
    assert to_cancel == []
    assert reasons == {}


def test_the_hold_stands_down_when_nothing_is_checking_the_economics():
    # Arrange — no cfg/hedge_asks. Place-and-wait (#384): a wanted token is
    # held; the re-gate simply declines to judge with no cfg.
    orders = [_order("o1", UP, 0.50)]
    reasons: dict = {}

    # Act
    to_cancel, _ = plan_orders(
        orders, [_intent(UP, 0.40)], dead_band=0.03, reasons=reasons,
        queue_ahead={"o1": 1.0}, hold_queue_shares=200.0)

    # Assert
    assert to_cancel == []
    assert reasons == {}


def test_an_unmeasured_queue_is_never_held():
    # Arrange — no entry for this order. Place-and-wait (#384): a wanted
    # token is held even when the queue was never measured.
    cfg = MakerConfig()
    orders = [_order("o1", UP, 0.50)]

    # Act
    to_cancel, _ = plan_orders(
        orders, [_intent(UP, 0.40)], dead_band=0.03, cfg=cfg,
        hedge_asks={UP: 0.40}, queue_ahead={}, hold_queue_shares=200.0)

    # Assert
    assert to_cancel == []


def test_the_hold_is_off_by_default():
    # Arrange — place-and-wait (#384): a wanted token is held regardless of
    # the shipped hold setting; the threshold no longer gates drift.
    cfg = MakerConfig()
    orders = [_order("o1", UP, 0.50)]

    # Act
    to_cancel, _ = plan_orders(
        orders, [_intent(UP, 0.40)], dead_band=0.03, cfg=cfg,
        hedge_asks={UP: 0.40}, queue_ahead={"o1": 1.0},
        hold_queue_shares=cfg.requote_hold_queue_shares)

    # Assert
    assert cfg.requote_hold_queue_shares == 0.0
    assert to_cancel == []


def test_a_token_we_no_longer_quote_is_never_held():
    # Arrange — front of the queue, but there is nothing left to hold for.
    cfg = MakerConfig()
    orders = [_order("o1", UP, 0.50)]
    reasons: dict = {}

    # Act
    to_cancel, _ = plan_orders(
        orders, [_intent(DOWN, 0.40)], cfg=cfg, hedge_asks={UP: 0.40},
        reasons=reasons, queue_ahead={"o1": 1.0}, hold_queue_shares=200.0)

    # Assert
    assert to_cancel == orders
    assert reasons == {"o1": CANCEL_NOT_QUOTED}


# --- persistence ------------------------------------------------------------

@pytest.fixture
def registry(tmp_path) -> OrderRegistry:
    db = tmp_path / "live.db"
    con = sqlite3.connect(str(db))
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    return OrderRegistry(db)


def _seed_order(registry: OrderRegistry, local_id: str = "o1") -> None:
    registry.create_order(OrderRecord(
        id=local_id, order_id=local_id, condition_id="0xmarket",
        token_id=UP, side="BUY", price=0.50, original_size=5.0,
        status="open", posted_ts=1, last_polled_ts=1, pair_id="pair-1",
        max_pair_cost_at_post=0.99,
    ))


def _row(registry: OrderRegistry, local_id: str = "o1") -> dict:
    con = sqlite3.connect(str(registry.db_path))
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT status, cancel_reason, cancel_queue_ahead FROM orders WHERE id = ?",
        (local_id,)).fetchone()
    con.close()
    return dict(row)


def test_the_reason_and_queue_are_persisted_with_the_cancel(registry):
    # Arrange
    _seed_order(registry)

    # Act
    registry.update_order_status("o1", status="cancelled", last_polled_ts=2,
                                 cancel_reason=CANCEL_PRICE_MOVED,
                                 cancel_queue_ahead=25.0)

    # Assert
    row = _row(registry)
    assert row["status"] == "cancelled"
    assert row["cancel_reason"] == CANCEL_PRICE_MOVED
    assert row["cancel_queue_ahead"] == pytest.approx(25.0)


def test_a_later_status_change_does_not_erase_why_it_was_cancelled(registry):
    # Arrange
    _seed_order(registry)
    registry.update_order_status("o1", status="cancelled", last_polled_ts=2,
                                 cancel_reason=CANCEL_REGATE_PAIR_COST,
                                 cancel_queue_ahead=1058.0)

    # Act — a later write that knows nothing about the cancel.
    registry.update_order_status("o1", status="cancelled", last_polled_ts=3)

    # Assert
    row = _row(registry)
    assert row["cancel_reason"] == CANCEL_REGATE_PAIR_COST
    assert row["cancel_queue_ahead"] == pytest.approx(1058.0)


def test_an_order_that_was_never_cancelled_carries_no_reason(registry):
    # Arrange / Act
    _seed_order(registry)

    # Assert
    row = _row(registry)
    assert row["cancel_reason"] is None
    assert row["cancel_queue_ahead"] is None


# --- reading the evidence back ----------------------------------------------

def test_the_report_separates_churn_from_the_gates_doing_their_job(registry):
    # Arrange — three price moves (candidates for the hold), one re-gate and
    # one universe drop (both correct, and not the hold's business).
    from core_brain.cancel_report import format_report, summarize

    for i, (reason, queue) in enumerate([
        (CANCEL_PRICE_MOVED, 25.0),
        (CANCEL_PRICE_MOVED, 1705.0),
        (CANCEL_PRICE_MOVED, 12930.0),
        (CANCEL_REGATE_PAIR_COST, 40.0),
        (CANCEL_NOT_QUOTED, 900.0),
    ]):
        _seed_order(registry, f"o{i}")
        registry.update_order_status(f"o{i}", status="cancelled",
                                     last_polled_ts=2, cancel_reason=reason,
                                     cancel_queue_ahead=queue)

    # Act
    summary = summarize(registry.db_path)

    # Assert
    assert summary["cancelled"] == 5
    assert summary["reasons"][CANCEL_PRICE_MOVED]["count"] == 3
    assert summary["reasons"][CANCEL_PRICE_MOVED]["median_queue_ahead"] == pytest.approx(1705.0)
    assert summary["reasons"][CANCEL_PRICE_MOVED]["min_queue_ahead"] == pytest.approx(25.0)
    assert summary["reasons"][CANCEL_REGATE_PAIR_COST]["count"] == 1

    text = format_report(summary)
    assert "60% of cancels were price moves" in text


def test_a_cancel_with_no_recorded_reason_is_counted_apart(registry):
    # Arrange — rows written before attribution existed. Folding them into a
    # named reason would invent evidence.
    from core_brain.cancel_report import summarize

    _seed_order(registry, "old")
    registry.update_order_status("old", status="cancelled", last_polled_ts=2)

    # Act
    summary = summarize(registry.db_path)

    # Assert
    assert summary["unattributed"] == 1
    assert summary["reasons"] == {}


def test_an_unreadable_registry_is_not_zero_cancels(tmp_path):
    # Arrange / Act
    from core_brain.cancel_report import format_report, summarize

    summary = summarize(tmp_path / "does-not-exist.db")

    # Assert
    assert summary["readable"] is False
    assert "UNREAD" in format_report(summary)
    assert "not zero cancels" in format_report(summary)


def test_a_store_written_before_attribution_counts_its_cancels_anyway(tmp_path):
    # Arrange — the pre-#131 schema: cancels exist, the columns do not.
    from core_brain.cancel_report import summarize

    db = tmp_path / "old.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE orders (id TEXT PRIMARY KEY, status TEXT)")
    con.executemany("INSERT INTO orders (id, status) VALUES (?, 'cancelled')",
                    [("a",), ("b",), ("c",)])
    con.commit()
    con.close()

    # Act
    summary = summarize(db)

    # Assert — readable, counted, and honestly unattributed.
    assert summary["readable"] is True
    assert summary["cancelled"] == 3
    assert summary["unattributed"] == 3


def test_a_market_leaving_the_universe_is_its_own_reason():
    """Driven through `_cancel_dropped_markets`, not read out of its source.

    That path is neither churn nor a gate, and a cancel with no recorded reason
    reads as either. This fails if the reason is dropped anywhere between the
    sweep and the canceller.
    """
    # Arrange
    from types import SimpleNamespace

    from core_brain.trader_loop import CANCEL_MARKET_DROPPED, _cancel_dropped_markets

    resting = SimpleNamespace(
        id="o1", order_id="v1", condition_id="0xgone", token_id=UP,
        price=0.50, side="BUY", status="open")
    handed: list = []

    def _cancel_fn(_client, _registry, orders):
        handed.extend(orders)
        return len(orders)

    seam = SimpleNamespace(
        client=object(),
        registry=SimpleNamespace(get_active_orders=lambda: [resting]),
        cancel_fn=_cancel_fn,
        resting_order_ids_fn=None,
        db_path=None,
    )

    # Act — the universe no longer contains 0xgone.
    _cancel_dropped_markets(seam, current_markets=[])

    # Assert
    assert [o["id"] for o in handed] == ["o1"]
    assert handed[0]["cancel_reason"] == CANCEL_MARKET_DROPPED


def test_a_held_order_does_not_get_a_replacement_posted_beside_it():
    """A held order rests OUTSIDE the tolerance by definition.

    Without this the submit loop does not recognise it as covering the cycle's
    intent and posts a second order on the same token -- double the resting
    size, which is the opposite of what holding a queue position is for.
    """
    # Arrange
    cfg = MakerConfig()
    orders = [_order("o1", UP, 0.50)]

    # Act
    to_cancel, to_submit = plan_orders(
        orders, [_intent(UP, 0.40)], dead_band=0.03, cfg=cfg,
        hedge_asks={UP: 0.40}, queue_ahead={"o1": 25.0},
        hold_queue_shares=200.0)

    # Assert
    assert to_cancel == []
    assert to_submit == []


def test_a_partially_migrated_store_is_not_called_unreadable(tmp_path):
    # Arrange — one column present, the other not. Checking only the first
    # would pass the guard and then fail selecting the second.
    from core_brain.cancel_report import summarize

    db = tmp_path / "partial.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE orders (id TEXT PRIMARY KEY, status TEXT, "
                "cancel_reason TEXT)")
    con.execute("INSERT INTO orders VALUES ('a', 'cancelled', 'price_moved')")
    con.commit()
    con.close()

    # Act
    summary = summarize(db)

    # Assert
    assert summary["readable"] is True
    assert summary["cancelled"] == 1
    assert summary["unattributed"] == 1


def test_a_path_with_uri_characters_still_opens(tmp_path):
    # Arrange — `#` in a filename would otherwise be read as the URI's
    # fragment. (`?` is the other case the escaping covers, but Windows will
    # not create a file with one, so it cannot be exercised portably.)
    from core_brain.cancel_report import summarize

    db = tmp_path / "run#2 rehearsal.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE orders (id TEXT PRIMARY KEY, status TEXT, "
                "cancel_reason TEXT, cancel_queue_ahead REAL)")
    con.execute("INSERT INTO orders VALUES ('a','cancelled','price_moved',25.0)")
    con.commit()
    con.close()

    # Act
    summary = summarize(db)

    # Assert
    assert summary["readable"] is True
    assert summary["reasons"]["price_moved"]["count"] == 1


def test_the_report_states_the_hold_setting_it_actually_read(monkeypatch):
    # Arrange — a hardcoded "currently off" would keep saying so after someone
    # turned the hold on, which is the reading that matters most.
    import core_brain.cancel_report as cr

    summary = {"db_path": "x.db", "readable": True, "cancelled": 2,
               "unattributed": 0,
               "reasons": {"price_moved": {"count": 2, "measured_queues": 0,
                                           "median_queue_ahead": None,
                                           "min_queue_ahead": None}}}

    # Act / Assert — off
    monkeypatch.setattr(cr, "_hold_state", lambda: "currently off")
    assert "currently off" in cr.format_report(summary)

    # Act / Assert — on
    monkeypatch.setattr(cr, "_hold_state", lambda: "on at 200 shares")
    assert "on at 200 shares" in cr.format_report(summary)


def test_the_hold_stands_down_when_there_is_no_hedge_ask_to_check_against():
    """Place-and-wait (#384): a missing hedge ask means the re-gate declines
    to judge, and a wanted token is held.
    """
    # Arrange — front of the queue, re-gate nominally armed, but the book has
    # no ask on the other leg.
    cfg = MakerConfig()
    orders = [_order("o1", UP, 0.50)]
    reasons: dict = {}

    # Act
    to_cancel, _ = plan_orders(
        orders, [_intent(UP, 0.40)], dead_band=0.03, cfg=cfg,
        hedge_asks={UP: None}, reasons=reasons,
        queue_ahead={"o1": 1.0}, hold_queue_shares=200.0)

    # Assert
    assert to_cancel == []
    assert reasons == {}


def test_the_hold_stands_down_on_a_zero_hedge_ask():
    # Arrange — 0 is the same "no opinion" case as None. Place-and-wait
    # (#384): a wanted token is held.
    cfg = MakerConfig()
    orders = [_order("o1", UP, 0.50)]

    # Act
    to_cancel, _ = plan_orders(
        orders, [_intent(UP, 0.40)], dead_band=0.03, cfg=cfg,
        hedge_asks={UP: 0.0}, queue_ahead={"o1": 1.0}, hold_queue_shares=200.0)

    # Assert
    assert to_cancel == []
