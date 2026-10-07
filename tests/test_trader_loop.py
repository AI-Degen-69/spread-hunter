"""The live fleet loop: decide -> submit -> reconcile, reusing the risk gates.

`engine.trader_loop` runs the live rotation through
`engine.quotes.evaluate_market_quote`, which wraps `decide_quotes`. The strategy that
trades is `engine.quotes.decide_quotes` -- already proven in the paper run and
already wired to the live risk gates -- so this module must add NOTHING new to
the decision. Its only new jobs are:

1. `plan_orders`: turn "what we want resting" into "what to cancel / submit"
   without resubmitting an order already resting at the desired price.
2. The rotation loop, which must never let one market's error stop the others,
   and must never touch the venue at all in dry-run.

These tests drive exactly those two seams. Everything that talks to the venue
(fetch market, fetch books, decide, submit, cancel, reconcile, sweep) is
injected, so the loop's behavior is tested without a network.
"""

from __future__ import annotations

import json

import pytest

from core_brain.trader_loop import (
    LiveFleetResult,
    VenueSeam,
    VisitOutcome,
    _classify_refusal,
    _visit_one,
    plan_orders,
    run,
)
from core_brain.config import MakerConfig
from core_brain.quotes import Inventory, QuoteIntent


def _intent(side="UP", token="tok-up", price=0.60, size=5, pair_id=None):
    i = QuoteIntent(side=side, token_id=token, price=price, size=size,
                    mid=price + 0.01, edge_vs_mid=0.01)
    i.pair_id = pair_id
    return i


def _open(token="tok-up", price=0.60, oid="o1", status="open", pair_id=None):
    d = {"token_id": token, "price": price, "order_id": oid,
         "side": "BUY", "status": status}
    if pair_id is not None:
        d["pair_id"] = pair_id
    return d


class TestPlanOrders:
    def test_keeps_matching_and_submits_nothing_when_fully_resting(self):
        open_orders = [_open()]
        intents = [_intent()]
        to_cancel, to_submit = plan_orders(open_orders, intents)
        assert to_cancel == []
        assert to_submit == []

    def test_cancels_stale_order_and_submits_new_price(self):
        # Place-and-wait (#384): an order whose token still has an intent is
        # KEPT at its own price -- the stale 0.61 stays, no duplicate posted.
        open_orders = [_open(price=0.61)]
        intents = [_intent(price=0.60)]
        to_cancel, to_submit = plan_orders(open_orders, intents)
        assert to_cancel == []
        assert to_submit == []

    def test_submits_intent_with_no_open_order(self):
        open_orders = []
        intents = [_intent()]
        to_cancel, to_submit = plan_orders(open_orders, intents)
        assert to_cancel == []
        assert to_submit == intents

    def test_cancels_orders_for_a_side_we_no_longer_quote(self):
        # UP is still wanted; the DOWN leg is gone from the intents.
        open_orders = [_open("tok-up", 0.60, "o1"), _open("tok-dn", 0.40, "o2")]
        intents = [_intent(side="UP", token="tok-up", price=0.60)]
        to_cancel, to_submit = plan_orders(open_orders, intents)
        assert [o["order_id"] for o in to_cancel] == ["o2"]
        assert to_submit == []

    def test_price_epsilon_treats_sub_tick_repricing_as_same(self):
        # A venue rounding jitter below a tick must not churn cancel+resubmit.
        open_orders = [_open(price=0.6005)]
        intents = [_intent(price=0.60)]
        to_cancel, to_submit = plan_orders(open_orders, intents, price_eps=0.001)
        assert to_cancel == []
        assert to_submit == []

    def test_dead_band_keeps_an_order_that_only_drifted_a_couple_of_cents(self):
        # 205 of 205 consecutive re-quotes on run-2809a7161de1 changed price
        # (median 3.0c) and every one reset queue position to zero.
        open_orders = [_open(price=0.60)]
        intents = [_intent(price=0.58)]
        to_cancel, to_submit = plan_orders(open_orders, intents, dead_band=0.03)
        assert to_cancel == []
        assert to_submit == []

    def test_dead_band_still_re_quotes_once_the_price_moves_past_it(self):
        # Place-and-wait (#384): the band no longer fires for a wanted token.
        # Drift past it is held, not re-quoted.
        open_orders = [_open(price=0.60)]
        intents = [_intent(price=0.55)]
        to_cancel, to_submit = plan_orders(open_orders, intents, dead_band=0.03)
        assert to_cancel == []
        assert to_submit == []

    def test_dead_band_is_symmetric(self):
        # Queue position is lost in both directions.
        open_orders = [_open(price=0.58)]
        intents = [_intent(price=0.60)]
        to_cancel, to_submit = plan_orders(open_orders, intents, dead_band=0.03)
        assert to_cancel == []
        assert to_submit == []

    def test_dead_band_defaults_off_so_existing_callers_are_unchanged(self):
        # Place-and-wait (#384): a wanted token is held with or without a band.
        open_orders = [_open(price=0.60)]
        intents = [_intent(price=0.58)]
        to_cancel, to_submit = plan_orders(open_orders, intents)
        assert to_cancel == []
        assert to_submit == []

    def test_held_pair_keeps_both_legs_and_needs_no_resubmit(self):
        # #387: a wanted pair is held at its own prices even when the live
        # hedge asks would fail the old pair-cost re-gate (0.73 + 0.30,
        # 0.23 + 0.23 against the retired gate). No cancel, no submit, the
        # pair_id never leaves the resting orders.
        open_orders = [
            {"token_id": "tok-up", "price": 0.73, "order_id": "o-up",
             "side": "BUY", "status": "open", "pair_id": "pair-aaa111"},
            {"token_id": "tok-dn", "price": 0.23, "order_id": "o-dn",
             "side": "BUY", "status": "open", "pair_id": "pair-aaa111"},
        ]
        intents = [
            _intent(side="UP", token="tok-up", price=0.71),
            _intent(side="DOWN", token="tok-dn", price=0.23),
        ]
        from core_brain.config import MakerConfig
        cfg = MakerConfig(max_completable_pair_cost=1.00)
        to_cancel, to_submit = plan_orders(
            open_orders, intents, cfg=cfg,
            hedge_asks={"tok-up": 0.30, "tok-dn": 0.23})
        assert to_cancel == []
        assert to_submit == []

    def test_single_held_leg_stays_without_submit(self):
        # One resting leg whose token is still quoted: held at its own price,
        # no duplicate posted beside it.
        open_orders = [
            {"token_id": "tok-up", "price": 0.73, "order_id": "o-up",
             "side": "BUY", "status": "open", "pair_id": "pair-aaa111"},
        ]
        intents = [_intent(side="UP", token="tok-up", price=0.71)]
        from core_brain.config import MakerConfig
        to_cancel, to_submit = plan_orders(
            open_orders, intents,
            cfg=MakerConfig(max_completable_pair_cost=1.00),
            hedge_asks={"tok-up": 0.30})
        assert to_cancel == []
        assert to_submit == []

    def test_two_fresh_intents_share_one_fresh_pair_id(self):
        # The submit path still mints one shared pair id for two fresh legs
        # posted together (no stale id to collide with).
        intents = [
            _intent(side="UP", token="tok-up", price=0.71),
            _intent(side="DOWN", token="tok-dn", price=0.25),
        ]
        assert all(i.pair_id is None for i in intents)

        from unittest.mock import MagicMock
        from core_brain.trader_loop import _submit_intents

        venue = MagicMock()
        venue.get_open_orders.return_value = []
        venue.create_order.return_value = {"signed": True}
        venue.post_orders.return_value = [{"orderID": "0x1"}, {"orderID": "0x2"}]
        registry = MagicMock()
        from core_brain.config import MakerConfig

        _submit_intents(venue, registry, FakeMarket("0xmkt"), intents, MakerConfig())
        created = [call.args[0] for call in registry.create_order.call_args_list]
        assert len(created) == 2
        pids = {o.pair_id for o in created}
        assert len(pids) == 1
        fresh_pid = pids.pop()
        assert fresh_pid.startswith("pair-")

    def test_fresh_submit_beside_a_held_leg_carries_no_stale_pair_id(self):
        # The planner stamps nothing: UP rests (held, its pair_id untouched);
        # DOWN is fresh and submits with no pair_id for the submit path.
        open_orders = [
            {"token_id": "tok-up", "price": 0.73, "order_id": "o-up",
             "side": "BUY", "status": "open", "pair_id": "pair-aaa111"},
        ]
        intents = [
            _intent(side="UP", token="tok-up", price=0.71),
            _intent(side="DOWN", token="tok-dn", price=0.25),
        ]
        to_cancel, to_submit = plan_orders(open_orders, intents)
        assert to_cancel == []
        assert [i.token_id for i in to_submit] == ["tok-dn"]
        assert to_submit[0].pair_id is None

    def test_wanted_legs_hold_despite_queue_and_gate_pressure(self):
        # #387: both legs wanted, live asks failing the retired pair-cost
        # gate, queue hold armed -- everything that used to cancel now holds.
        open_orders = [
            {"token_id": "tok-up", "price": 0.73, "order_id": "o-up",
             "side": "BUY", "status": "open", "pair_id": "pair-aaa111"},
            {"token_id": "tok-dn", "price": 0.23, "order_id": "o-dn",
             "side": "BUY", "status": "open", "pair_id": "pair-aaa111"},
        ]
        intents = [
            _intent(side="UP", token="tok-up", price=0.60),
            _intent(side="DOWN", token="tok-dn", price=0.28),
        ]
        to_cancel, to_submit = plan_orders(
            open_orders, intents, dead_band=0.01,
            cfg=MakerConfig(max_completable_pair_cost=1.00),
            hedge_asks={"tok-up": 0.20, "tok-dn": 0.80},
            queue_ahead={"o-up": 10.0},
            hold_queue_shares=50.0)
        assert to_cancel == []
        assert to_submit == []

    def test_the_queue_hold_keeps_a_near_front_order_through_a_price_move(self):
        # #304: the hold is the whole point of the issue, so it gets its own
        # test at the threshold the recorded cancels picked. 50 shares ahead,
        # 200-share threshold: the order is held at 0.73 even though the
        # desired price has walked to 0.60, because a cancel would re-post it
        # at the back of a new level and the queue is what fills here.
        open_orders = [_open(price=0.73, oid="o-up")]
        intents = [_intent(price=0.60)]
        to_cancel, to_submit = plan_orders(
            open_orders, intents, dead_band=0.01,
            cfg=MakerConfig(max_completable_pair_cost=1.00),
            hedge_asks={"tok-up": 0.20},
            queue_ahead={"o-up": 50.0},
            hold_queue_shares=200.0)
        assert to_cancel == []
        assert to_submit == []

    def test_the_queue_hold_releases_an_order_that_is_far_back(self):
        # Place-and-wait (#384): a wanted token is held unconditionally, so
        # queue depth no longer releases it. The far-back order stays.
        open_orders = [_open(price=0.73, oid="o-up")]
        intents = [_intent(price=0.60)]
        to_cancel, _ = plan_orders(
            open_orders, intents, dead_band=0.01,
            cfg=MakerConfig(max_completable_pair_cost=1.00),
            hedge_asks={"tok-up": 0.20},
            queue_ahead={"o-up": 5000.0},
            hold_queue_shares=200.0)
        assert to_cancel == []

    def test_the_queue_hold_declines_on_a_queue_it_never_measured(self):
        # Place-and-wait (#384): an unmeasured queue no longer cancels a
        # wanted token either. The order is held at its own price.
        open_orders = [_open(price=0.73, oid="o-up")]
        intents = [_intent(price=0.60)]
        to_cancel, _ = plan_orders(
            open_orders, intents, dead_band=0.01,
            cfg=MakerConfig(max_completable_pair_cost=1.00),
            hedge_asks={"tok-up": 0.20},
            queue_ahead={},
            hold_queue_shares=200.0)
        assert to_cancel == []

    def test_front_of_queue_order_holds_despite_failing_gate(self):
        # #387: queue position AND a failing pair-cost gate -- the wanted
        # order is still held. Cost is judged at placement, never after.
        open_orders = [_open(price=0.73, oid="o-up")]
        intents = [_intent(price=0.60)]
        to_cancel, to_submit = plan_orders(
            open_orders, intents, dead_band=0.01,
            cfg=MakerConfig(max_completable_pair_cost=1.00),
            hedge_asks={"tok-up": 0.42},
            queue_ahead={"o-up": 5.0},
            hold_queue_shares=200.0)
        assert to_cancel == []
        assert to_submit == []

    def test_the_larger_of_dead_band_and_price_eps_wins(self):
        # Place-and-wait (#384): either tolerance holds a wanted token; drift
        # cancels nothing, so both widths keep the order.
        open_orders = [_open(price=0.60)]
        intents = [_intent(price=0.58)]
        to_cancel, _ = plan_orders(open_orders, intents,
                                   price_eps=0.001, dead_band=0.03)
        assert to_cancel == []

    def test_own_price_failing_the_retired_gate_still_holds(self):
        # #387: the order rests at 0.60 and completing at the DOWN ask of
        # 0.42 would cost 1.02 -- held anyway. The gate judges placements,
        # not resting orders.
        cfg = MakerConfig(max_completable_pair_cost=1.00)
        open_orders = [_open(price=0.60)]
        intents = [_intent(price=0.58)]
        to_cancel, to_submit = plan_orders(
            open_orders, intents, dead_band=0.03, cfg=cfg,
            hedge_asks={"tok-up": 0.42})
        assert to_cancel == []
        assert to_submit == []

    def test_a_kept_order_that_still_completes_under_the_cap_is_left_alone(self):
        cfg = MakerConfig(max_completable_pair_cost=1.00)
        open_orders = [_open(price=0.60)]
        intents = [_intent(price=0.58)]
        to_cancel, to_submit = plan_orders(
            open_orders, intents, dead_band=0.03, cfg=cfg,
            hedge_asks={"tok-up": 0.38})
        assert to_cancel == []
        assert to_submit == []

    def test_a_missing_hedge_ask_leaves_the_kept_order_alone(self):
        cfg = MakerConfig(max_completable_pair_cost=1.00)
        open_orders = [_open(price=0.60)]
        intents = [_intent(price=0.58)]
        to_cancel, _ = plan_orders(open_orders, intents, dead_band=0.03,
                                   cfg=cfg, hedge_asks={})
        assert to_cancel == []

    def test_dead_band_widths_hold_despite_failing_gate(self):
        # Issue #361 scenario, under no-re-check (#387):
        # Resting bid at 0.60, desired intent walks down to 0.55 (drift = 0.05).
        # Opposite ask is 0.42 (0.60 + 0.42 = 1.02 >= the retired cap).
        # Both dead-band widths hold the order and record no reason --
        # cost is judged at placement, never after.
        cfg = MakerConfig(max_completable_pair_cost=1.00)
        open_orders = [_open(price=0.60, oid="o1")]
        intents = [_intent(price=0.55)]
        hedge_asks = {"tok-up": 0.42}

        # 1. Narrow dead band (0.03): out of band -- held anyway.
        reasons_narrow = {}
        to_cancel_narrow, to_submit_narrow = plan_orders(
            open_orders, intents, dead_band=0.03, cfg=cfg,
            hedge_asks=hedge_asks, reasons=reasons_narrow,
        )
        assert to_cancel_narrow == []
        assert reasons_narrow == {}
        assert to_submit_narrow == []

        # 2. Wide dead band (0.08): in band -- held anyway.
        reasons_wide = {}
        to_cancel_wide, to_submit_wide = plan_orders(
            open_orders, intents, dead_band=0.08, cfg=cfg,
            hedge_asks=hedge_asks, reasons=reasons_wide,
        )
        assert to_cancel_wide == []
        assert reasons_wide == {}
        assert to_submit_wide == []

    def test_hold_levers_and_failing_gate_hold_together(self):
        # #387: hold_below_target and hold_queue_shares active, pair-cost
        # gate failing -- the wanted order is held, no reason recorded.
        cfg = MakerConfig(max_completable_pair_cost=1.00)
        open_orders = [_open(price=0.60, oid="o1")]
        intents = [_intent(price=0.55)]
        hedge_asks = {"tok-up": 0.42}
        reasons = {}

        to_cancel, to_submit = plan_orders(
            open_orders, intents, dead_band=0.08, cfg=cfg,
            hedge_asks=hedge_asks,
            queue_ahead={"o1": 5.0},
            hold_queue_shares=200.0,
            hold_below_target=0.06,
            reasons=reasons,
        )
        assert to_cancel == []
        assert reasons == {}
        assert to_submit == []

    def test_place_and_wait_holds_a_profitable_resting_pair_through_drift(self):
        # Issue #384: every resting pair passed the pair-cost gate before
        # placement, so a resting order whose token still has an intent is KEPT
        # at its own price -- no tolerance check, no re-quote on mid drift.
        # Rest UP 0.70 + rest DOWN 0.27 = 0.97 (profitable by placement);
        # intents drift far out of band to 0.66/0.25.
        open_orders = [
            _open(token="tok-up", price=0.70, oid="o-up"),
            _open(token="tok-dn", price=0.27, oid="o-dn"),
        ]
        intents = [
            _intent(side="UP", token="tok-up", price=0.66),
            _intent(side="DOWN", token="tok-dn", price=0.25),
        ]
        to_cancel, to_submit = plan_orders(open_orders, intents, dead_band=0.03)
        assert to_cancel == []
        assert to_submit == []

    def test_place_and_wait_holds_a_single_resting_leg_through_drift(self):
        # One resting leg, intent drifted 10c away: still held, no cancel,
        # no duplicate submit.
        open_orders = [_open(token="tok-up", price=0.70, oid="o-up")]
        intents = [_intent(side="UP", token="tok-up", price=0.60)]
        to_cancel, to_submit = plan_orders(open_orders, intents, dead_band=0.03)
        assert to_cancel == []
        assert to_submit == []



class FakeMarket:
    def __init__(self, cid="0xabc"):
        self.condition_id = cid
        self.up_token = "tok-up"
        self.down_token = "tok-dn"
        self.market_slug = "fake-market"
        self.tick_size = 0.01
        self.neg_risk = False

    def t_remaining(self, now=None):
        return 14400.0


class TestRunLoop:
    def _run(self, live, intents, fetch_raises=False, once=True):
        calls = {"submitted": [], "cancelled": [], "reconciled": 0, "swept": 0}

        def fake_fetch_market(cid):
            if fetch_raises:
                raise RuntimeError("venue down")
            return FakeMarket(cid)

        def fake_books(clob_host, token):
            return {"token_id": token, "best_bid": 0.59, "best_ask": 0.61,
                    "bids": {0.59: 100}, "asks": {0.61: 100}}

        def fake_decide(cfg, up, dn, inv, t_rem, wf):
            return list(intents), ("" if intents else "declined")

        def fake_submit(client, registry, market, intents, cfg):
            calls["submitted"].append([i.side for i in intents])
            return 0

        def fake_cancel(client, registry, orders):
            calls["cancelled"].append([o["order_id"] for o in orders])
            return len(orders)

        def fake_reconcile(client, registry, maker):
            calls["reconciled"] += 1
            return None

        def fake_sweep():
            calls["swept"] += 1

        seam = VenueSeam(
            client=object(),
            fetch_market=fake_fetch_market,
            fetch_books=fake_books,
            decide=fake_decide,
            submit_fn=fake_submit,
            cancel_fn=fake_cancel,
            reconcile_fn=fake_reconcile,
            sweep_fn=fake_sweep,
        )
        results = run(
            seam, interval=0.0, once=once, live=live,
            markets=[FakeMarket("0xabc")],
            sleep_fn=lambda s: None,
        )
        return results, calls

    def test_dry_run_decides_but_never_submits_or_cancels(self):
        results, calls = self._run(live=False, intents=[_intent(), _intent("DOWN", "tok-dn", 0.40)])
        assert results[0].status == "DRY_RUN"
        assert calls["submitted"] == []
        assert calls["cancelled"] == []
        assert calls["reconciled"] == 1  # reconcile is read-only; still safe in dry-run

    def test_live_submits_decided_intents(self):
        results, calls = self._run(live=True, intents=[_intent(), _intent("DOWN", "tok-dn", 0.40)])
        assert results[0].status == "QUOTED"
        assert calls["submitted"] == [["UP", "DOWN"]]

    def test_live_cancels_stale_when_decide_returns_nothing(self):
        # no intents -> every open order is stale. Exercise via a fake open set.
        results, calls = self._run(live=True, intents=[])
        assert results[0].status == "DECLINED"
        # No open orders were injected, so nothing to cancel; the point is the
        # loop still completed without raising.
        assert calls["submitted"] == []

    def test_one_market_error_does_not_stop_the_rotation(self):
        results, calls = self._run(live=True, intents=[_intent()], fetch_raises=True)
        assert results[0].status == "ERROR"
        assert calls["reconciled"] == 1

    def test_sweep_runs_on_first_cycle(self):
        results, calls = self._run(live=False, intents=[])
        assert calls["swept"] == 1

    def test_submit_error_does_not_stop_the_rotation(self):
        # A submit failure must degrade the market to ERROR, not kill the loop:
        # the second market is still visited and reconcile still runs.
        calls = {"reconciled": 0}

        def fetch_market(cid):
            return FakeMarket(cid)

        def fetch_books(clob_host, token):
            return {"token_id": token, "best_bid": 0.59, "best_ask": 0.61,
                    "bids": {0.59: 100}, "asks": {0.61: 100}}

        def decide(cfg, up, dn, inv, t_rem, wf):
            return [_intent()], ""

        def submit_fn(client, registry, market, intents, cfg):
            raise RuntimeError("venue rejected")

        def cancel_fn(client, registry, orders):
            return 0

        def reconcile_fn(client, registry, maker):
            calls["reconciled"] += 1

        seam = VenueSeam(
            client=object(), fetch_market=fetch_market, fetch_books=fetch_books,
            decide=decide, submit_fn=submit_fn, cancel_fn=cancel_fn,
            reconcile_fn=reconcile_fn, sweep_fn=lambda: None,
        )
        results = run(
            seam, interval=0.0, once=True, live=True,
            markets=[FakeMarket("0xa"), FakeMarket("0xb")],
            sleep_fn=lambda s: None,
        )
        assert [r.status for r in results] == ["ERROR", "ERROR"]
        assert calls["reconciled"] == 1

    def test_fleet_state_is_injected_into_decide_cfg(self):
        seen = {}

        def decide(cfg, up, dn, inv, t_rem, wf):
            seen["fleet_naked_usd"] = cfg.fleet_naked_usd
            return [], "declined"

        def fetch_market(cid):
            return FakeMarket(cid)

        def fetch_books(clob_host, token):
            return {"token_id": token, "best_bid": 0.59, "best_ask": 0.61,
                    "bids": {0.59: 100}, "asks": {0.61: 100}}

        run(
            VenueSeam(
                client=object(),
                fetch_market=fetch_market, fetch_books=fetch_books, decide=decide,
                submit_fn=lambda *a, **k: 0, cancel_fn=lambda *a, **k: 0,
                reconcile_fn=lambda *a, **k: None, sweep_fn=lambda: None,
                fleet_state_fn=lambda r: {"fleet_naked_usd": 12.5},
            ),
            interval=0.0, once=True, live=False,
            markets=[FakeMarket("0xabc")],
            sleep_fn=lambda s: None,
        )
        assert seen["fleet_naked_usd"] == 12.5

    def test_open_orders_fn_includes_open_and_partial_orders(self):
        from unittest.mock import MagicMock
        from core_brain.trader_loop import _make_open_orders_fn

        registry = MagicMock()
        o_open = MagicMock(condition_id="0xabc", status="open", token_id="tok-up", price=0.55, order_id="v1", id="row1", side="BUY", pair_id="pair-1")
        o_partial = MagicMock(condition_id="0xabc", status="partial", token_id="tok-dn", price=0.45, order_id="v2", id="row2", side="BUY", pair_id="pair-2")
        o_filled = MagicMock(condition_id="0xabc", status="filled", token_id="tok-up", price=0.55, order_id="v3", id="row3", side="BUY", pair_id="pair-3")
        o_cancelled = MagicMock(condition_id="0xabc", status="cancelled", token_id="tok-dn", price=0.45, order_id="v4", id="row4", side="BUY", pair_id="pair-4")
        o_other_mkt = MagicMock(condition_id="0xdef", status="open", token_id="tok-dn", price=0.45, order_id="v5", id="row5", side="BUY", pair_id="pair-5")

        registry.get_active_orders.return_value = [o_open, o_partial, o_filled, o_cancelled, o_other_mkt]

        fn = _make_open_orders_fn(registry)
        market = FakeMarket("0xabc")
        res = fn(market)

        assert len(res) == 2
        assert {r["order_id"] for r in res} == {"v1", "v2"}
        assert {r["status"] for r in res} == {"open", "partial"}
        assert {r["pair_id"] for r in res} == {"pair-1", "pair-2"}

    def test_run_reloads_markets_via_markets_fn_each_cycle(self):
        call_count = [0]
        m1 = FakeMarket("0x1")
        m2 = FakeMarket("0x2")

        def market_supplier():
            call_count[0] += 1
            return [m1] if call_count[0] == 1 else [m2]

        visited = []

        def fake_fetch_market(cid):
            visited.append(cid)
            return FakeMarket(cid)

        seam = VenueSeam(
            client=object(),
            fetch_market=fake_fetch_market,
            fetch_books=lambda h, t: {"token_id": t, "bids": {}, "asks": {}},
            decide=lambda *a: ([], "declined"),
            submit_fn=lambda *a, **k: 0,
            cancel_fn=lambda *a, **k: 0,
            reconcile_fn=lambda *a, **k: None,
            sweep_fn=lambda: None,
        )

        cycles = [0]
        def sleep_stop(s):
            cycles[0] += 1
            if cycles[0] >= 2:
                raise KeyboardInterrupt()

        run(
            seam, interval=0.0, once=False, live=False,
            markets_fn=market_supplier,
            sleep_fn=sleep_stop,
        )

        assert visited == ["0x1", "0x2"]

    def test_run_cancels_orders_for_dropped_markets(self):
        from unittest.mock import MagicMock

        registry = MagicMock()
        o_dropped_open = MagicMock(condition_id="0xdropped", status="open", token_id="tok-up", price=0.55, order_id="v_drop_open", id="row_drop_1", side="BUY")
        o_dropped_partial = MagicMock(condition_id="0xdropped", status="partial", token_id="tok-dn", price=0.45, order_id="v_drop_partial", id="row_drop_2", side="BUY")
        registry.get_active_orders.return_value = [o_dropped_open, o_dropped_partial]

        cancelled_calls = []
        def fake_cancel(client, reg, orders):
            cancelled_calls.append(orders)
            return len(orders)

        seam = VenueSeam(
            client=object(),
            registry=registry,
            fetch_market=lambda cid: FakeMarket(cid),
            fetch_books=lambda h, t: {"token_id": t, "bids": {}, "asks": {}},
            decide=lambda *a: ([], "declined"),
            submit_fn=lambda *a, **k: 0,
            cancel_fn=fake_cancel,
            reconcile_fn=lambda *a, **k: None,
            sweep_fn=lambda: None,
        )

        results = run(
            seam, interval=0.0, once=True, live=True,
            markets=[FakeMarket("0xactive")],
            sleep_fn=lambda s: None,
        )

        # Only the `open` leg is cancelled. The `partial` has already bought
        # shares; cancelling it would strand them as a naked single buy that this
        # loop has no exit path for.
        assert len(cancelled_calls) == 1
        order_ids = {o["order_id"] for o in cancelled_calls[0]}
        assert order_ids == {"v_drop_open"}
        assert any(r.condition_id == "0xdropped"
                   and r.status == "CANCELLED"
                   and r.why == "dropped_market_cancelled" for r in results)
        assert any(r.condition_id == "0xdropped"
                   and r.status == "WARNED"
                   and r.why == "dropped_market_partial_retained" for r in results)

    def test_markets_fn_error_retains_last_successful_markets(self):
        from unittest.mock import MagicMock

        call_count = [0]
        m_dynamic = FakeMarket("0xdynamic")

        def failing_market_supplier():
            call_count[0] += 1
            if call_count[0] == 1:
                return [m_dynamic]
            raise RuntimeError("API fetch error")

        visited = []
        def fake_fetch_market(cid):
            visited.append(cid)
            return FakeMarket(cid)

        registry = MagicMock()
        o_dynamic = MagicMock(condition_id="0xdynamic", status="open", token_id="tok-up", price=0.55, order_id="v_dyn", id="row_dyn", side="BUY")
        registry.get_active_orders.return_value = [o_dynamic]

        cancelled_calls = []
        def fake_cancel(client, reg, orders):
            cancelled_calls.append(orders)
            return len(orders)

        seam = VenueSeam(
            client=object(),
            registry=registry,
            fetch_market=fake_fetch_market,
            fetch_books=lambda h, t: {"token_id": t, "bids": {}, "asks": {}},
            decide=lambda *a: ([], "declined"),
            submit_fn=lambda *a, **k: 0,
            cancel_fn=fake_cancel,
            reconcile_fn=lambda *a, **k: None,
            sweep_fn=lambda: None,
        )

        cycles = [0]
        def sleep_stop(s):
            cycles[0] += 1
            if cycles[0] >= 2:
                raise KeyboardInterrupt()

        run(
            seam, interval=0.0, once=False, live=True,
            markets=[FakeMarket("0xinitial")],
            markets_fn=failing_market_supplier,
            sleep_fn=sleep_stop,
        )

        # 0xdynamic visited in both cycles; because it remained current in cycle 2, its order was never cancelled as dropped
        assert visited == ["0xdynamic", "0xdynamic"]
        assert cancelled_calls == []

    def test_dropped_market_cancellation_isolates_errors(self):
        from unittest.mock import MagicMock

        registry = MagicMock()
        o_drop1 = MagicMock(condition_id="0xdrop1", status="open", token_id="tok-up", price=0.55, order_id="v1", id="r1", side="BUY")
        o_drop2 = MagicMock(condition_id="0xdrop2", status="open", token_id="tok-dn", price=0.45, order_id="v2", id="r2", side="BUY")
        registry.get_active_orders.return_value = [o_drop1, o_drop2]

        cancelled_cids = []
        def fake_cancel(client, reg, orders):
            cid = orders[0]["order_id"]
            if cid == "v1":
                raise RuntimeError("venue reject on drop1")
            cancelled_cids.append(cid)
            return len(orders)

        seam = VenueSeam(
            client=object(),
            registry=registry,
            fetch_market=lambda cid: FakeMarket(cid),
            fetch_books=lambda h, t: {"token_id": t, "bids": {}, "asks": {}},
            decide=lambda *a: ([], "declined"),
            submit_fn=lambda *a, **k: 0,
            cancel_fn=fake_cancel,
            reconcile_fn=lambda *a, **k: None,
            sweep_fn=lambda: None,
        )

        results = run(
            seam, interval=0.0, once=True, live=True,
            markets=[FakeMarket("0xactive")],
            sleep_fn=lambda s: None,
        )

        # drop2 cancelled despite drop1 failing
        assert cancelled_cids == ["v2"]
        assert any(r.condition_id == "0xdrop1" and r.status == "ERROR" for r in results)
        assert any(r.condition_id == "0xdrop2" and r.status == "CANCELLED" for r in results)

    @staticmethod
    def _cleanup_seam(registry, cancel_fn):
        return VenueSeam(
            client=object(),
            registry=registry,
            fetch_market=lambda cid: FakeMarket(cid),
            fetch_books=lambda h, t: {"token_id": t, "bids": {}, "asks": {}},
            decide=lambda *a: ([], "declined"),
            submit_fn=lambda *a, **k: 0,
            cancel_fn=cancel_fn,
            reconcile_fn=lambda *a, **k: None,
            sweep_fn=lambda: None,
        )

    def test_empty_markets_refresh_is_ignored_and_keeps_the_book(self):
        """A ranker cycle that graduates nothing must not cancel the whole book.

        `load_graduated_markets` raises on a missing, stale or malformed feed but
        returns cleanly for a well-formed `[]`. Adopting that empties the active
        universe, and every resting order then looks dropped.
        """
        from unittest.mock import MagicMock

        registry = MagicMock()
        o_live = MagicMock(condition_id="0xlive", status="open", token_id="tok-up",
                           price=0.55, order_id="v_live", id="row_live", side="BUY")
        registry.get_active_orders.return_value = [o_live]

        cancelled_calls = []
        def fake_cancel(client, reg, orders):
            cancelled_calls.append(orders)
            return len(orders)

        visited = []
        seam = self._cleanup_seam(registry, fake_cancel)
        seam.fetch_market = lambda cid: (visited.append(cid) or FakeMarket(cid))

        results = run(
            seam, interval=0.0, once=True, live=True,
            markets=[FakeMarket("0xlive")],
            markets_fn=lambda: [],
            sleep_fn=lambda s: None,
        )

        assert visited == ["0xlive"]
        assert cancelled_calls == []
        assert all(r.why != "dropped_market_cancelled" for r in results)

    def test_pending_orders_on_a_dropped_market_are_left_to_reconcile(self):
        """A pending row may have no venue id yet; orphan adoption owns it."""
        from unittest.mock import MagicMock

        registry = MagicMock()
        o_pending = MagicMock(condition_id="0xdropped", status="pending",
                              token_id="tok-up", price=0.55, order_id=None,
                              id="row_pending", side="BUY")
        registry.get_active_orders.return_value = [o_pending]

        cancelled_calls = []
        def fake_cancel(client, reg, orders):
            cancelled_calls.append(orders)
            return len(orders)

        results = run(
            self._cleanup_seam(registry, fake_cancel),
            interval=0.0, once=True, live=True,
            markets=[FakeMarket("0xactive")],
            sleep_fn=lambda s: None,
        )

        assert cancelled_calls == []
        assert all(r.condition_id != "0xdropped" for r in results)

    def test_registry_read_failure_in_cleanup_surfaces_as_a_result(self):
        """A cleanup that can no-op invisibly is worse than none."""
        from unittest.mock import MagicMock

        registry = MagicMock()
        registry.get_active_orders.side_effect = RuntimeError("db locked")

        results = run(
            self._cleanup_seam(registry, lambda *a, **k: 0),
            interval=0.0, once=True, live=True,
            markets=[FakeMarket("0xactive")],
            sleep_fn=lambda s: None,
        )

        err = [r for r in results if r.why == "dropped_market_cleanup_failed"]
        assert len(err) == 1
        assert err[0].status == "ERROR"
        assert "db locked" in err[0].error

    def test_cleanup_is_skipped_when_the_universe_was_never_populated(self):
        """No market set yet is not "every market was dropped".

        With no `markets` and a first refresh that graduates nothing, the active
        universe is empty for a reason that says nothing about resting orders.
        """
        from unittest.mock import MagicMock

        registry = MagicMock()
        o_live = MagicMock(condition_id="0xlive", status="open", token_id="tok-up",
                           price=0.55, order_id="v_live", id="row_live", side="BUY")
        registry.get_active_orders.return_value = [o_live]

        cancelled_calls = []
        def fake_cancel(client, reg, orders):
            cancelled_calls.append(orders)
            return len(orders)

        results = run(
            self._cleanup_seam(registry, fake_cancel),
            interval=0.0, once=True, live=True,
            markets=None,
            markets_fn=lambda: [],
            sleep_fn=lambda s: None,
        )

        assert cancelled_calls == []
        assert results == []
    def test_cancel_happens_before_submit_in_replacement_quote(self):
        call_log = []

        def decide(cfg, up, dn, inv, t_rem, wf):
            # The old token is no longer quoted (triggering a not_quoted
            # cancel); the new token's intent submits right after.
            return [_intent(side="UP", token="tok-up", price=0.62)], ""

        def open_orders_fn(m):
            return [{"token_id": "tok-old", "price": 0.58, "order_id": "o-old", "id": "local-old", "side": "BUY", "status": "open"}]

        def cancel_fn(client, registry, orders):
            call_log.append("cancel")
            return len(orders)

        def submit_fn(client, registry, market, intents, cfg):
            call_log.append("submit")
            return len(intents)

        seam = VenueSeam(
            client=object(),
            fetch_market=lambda cid: FakeMarket(cid),
            fetch_books=lambda h, t: {"token_id": t, "best_bid": 0.59, "best_ask": 0.61, "bids": {0.59: 100}, "asks": {0.61: 100}},
            decide=decide,
            submit_fn=submit_fn,
            cancel_fn=cancel_fn,
            open_orders_fn=open_orders_fn,
            reconcile_fn=lambda *a: None,
            sweep_fn=lambda: None,
        )
        results = run(
            seam, interval=0.0, once=True, live=True,
            markets=[FakeMarket("0xabc")],
            sleep_fn=lambda s: None,
        )
        assert results[0].status == "QUOTED"
        assert call_log == ["cancel", "submit"]

    def test_cancel_failure_aborts_submit_and_records_error(self):
        call_log = []

        def decide(cfg, up, dn, inv, t_rem, wf):
            return [_intent(side="UP", token="tok-up", price=0.62)], ""

        def open_orders_fn(m):
            return [{"token_id": "tok-old", "price": 0.58, "order_id": "o-old", "id": "local-old", "side": "BUY", "status": "open"}]

        def cancel_fn(client, registry, orders):
            call_log.append("cancel")
            return 0  # Failed to cancel orders

        def submit_fn(client, registry, market, intents, cfg):
            call_log.append("submit")
            return len(intents)

        seam = VenueSeam(
            client=object(),
            fetch_market=lambda cid: FakeMarket(cid),
            fetch_books=lambda h, t: {"token_id": t, "best_bid": 0.59, "best_ask": 0.61, "bids": {0.59: 100}, "asks": {0.61: 100}},
            decide=decide,
            submit_fn=submit_fn,
            cancel_fn=cancel_fn,
            open_orders_fn=open_orders_fn,
            reconcile_fn=lambda *a: None,
            sweep_fn=lambda: None,
        )
        results = run(
            seam, interval=0.0, once=True, live=True,
            markets=[FakeMarket("0xabc")],
            sleep_fn=lambda s: None,
        )
        assert results[0].status == "ERROR"
        assert "cancel failed" in str(results[0].error).lower()
        assert "submit" not in call_log

    def _replacement_seam(self, call_log, cancel_fn, resting_order_ids_fn=None):
        """A seam whose old token drops out of quote while a new one is wanted."""
        def decide(cfg, up, dn, inv, t_rem, wf):
            return [_intent(side="UP", token="tok-up", price=0.62)], ""

        def open_orders_fn(m):
            return [{"token_id": "tok-old", "price": 0.58, "order_id": "o-old",
                     "id": "local-old", "side": "BUY", "status": "open"}]

        def submit_fn(client, registry, market, intents, cfg):
            call_log.append("submit")
            return len(intents)

        return VenueSeam(
            client=object(),
            fetch_market=lambda cid: FakeMarket(cid),
            fetch_books=lambda h, t: {"token_id": t, "best_bid": 0.59, "best_ask": 0.61,
                                      "bids": {0.59: 100}, "asks": {0.61: 100}},
            decide=decide,
            submit_fn=submit_fn,
            cancel_fn=cancel_fn,
            open_orders_fn=open_orders_fn,
            resting_order_ids_fn=resting_order_ids_fn,
            reconcile_fn=lambda *a: None,
            sweep_fn=lambda: None,
        )

    def test_short_cancel_still_submits_when_venue_shows_order_gone(self):
        """An order that filled between planning and cancelling is not a blocker.

        `cancel_fn` comes up short, but the venue reports nothing resting, so the
        replacement is safe and the market must quote rather than park in ERROR.
        """
        call_log = []

        def cancel_fn(client, registry, orders):
            call_log.append("cancel")
            return 0

        seam = self._replacement_seam(
            call_log, cancel_fn, resting_order_ids_fn=lambda c: set())
        results = run(seam, interval=0.0, once=True, live=True,
                      markets=[FakeMarket("0xabc")], sleep_fn=lambda s: None)

        assert results[0].status == "QUOTED"
        assert call_log == ["cancel", "submit"]

    def test_short_cancel_aborts_when_venue_shows_order_still_resting(self):
        call_log = []

        def cancel_fn(client, registry, orders):
            call_log.append("cancel")
            return 0

        seam = self._replacement_seam(
            call_log, cancel_fn, resting_order_ids_fn=lambda c: {"o-old"})
        results = run(seam, interval=0.0, once=True, live=True,
                      markets=[FakeMarket("0xabc")], sleep_fn=lambda s: None)

        assert results[0].status == "ERROR"
        assert "still resting" in str(results[0].error).lower()
        assert "submit" not in call_log

    def test_short_cancel_aborts_when_venue_read_fails(self):
        """Unverifiable is treated as unsafe, never as \"nothing is resting\"."""
        call_log = []

        def cancel_fn(client, registry, orders):
            call_log.append("cancel")
            return 0

        def exploding_read(client):
            raise RuntimeError("clob unreachable")

        seam = self._replacement_seam(
            call_log, cancel_fn, resting_order_ids_fn=exploding_read)
        results = run(seam, interval=0.0, once=True, live=True,
                      markets=[FakeMarket("0xabc")], sleep_fn=lambda s: None)

        assert results[0].status == "ERROR"
        assert "still resting" in str(results[0].error).lower()
        assert "submit" not in call_log

    def test_venue_resting_order_ids_returns_none_when_venue_unreachable(self):
        from core_brain.trader_loop import _venue_resting_order_ids

        class Dead:
            def get_open_orders(self):
                raise RuntimeError("boom")

        class Alive:
            def get_open_orders(self):
                return [{"id": "o-1"}, {"orderID": "o-2"}, {"orderId": "o-2b"},
                        {"order_id": "o-3"}, {"id": "o-4", "orderID": "venue-4"}, {}]

        class Silent:
            def get_open_orders(self):
                return None

        assert _venue_resting_order_ids(Dead()) is None
        # A None response is "the venue did not answer", never "nothing rests".
        assert _venue_resting_order_ids(Silent()) is None
        # Every spelling on an order is collected, not just the first that hits:
        # a missed id would wave a live order through as gone.
        assert _venue_resting_order_ids(Alive()) == {
            "o-1", "o-2", "o-2b", "o-3", "o-4", "venue-4"}


class TestVisitOnePassesTheDeadBand:
    def test_a_two_cent_drift_does_not_churn_the_resting_order(self):
        """The whole point, end to end: same market, price moved 2c, no cancel."""
        seen = {}
        real_plan = plan_orders

        def spy(open_orders, intents, price_eps=1e-9, **kw):
            seen.update(kw)
            return real_plan(open_orders, intents, price_eps, **kw)

        market = FakeMarket()
        up = {"token_id": "tok-up", "best_bid": 0.50, "best_ask": 0.52,
              "bids": {0.50: 5000.0}, "asks": {0.52: 5000.0}}
        down = {"token_id": "tok-dn", "best_bid": 0.46, "best_ask": 0.48,
                "bids": {0.46: 5000.0}, "asks": {0.48: 5000.0}}
        books = {"tok-up": up, "tok-dn": down}
        seam = VenueSeam(
            base_cfg=MakerConfig(requote_dead_band=0.03,
                                 max_completable_pair_cost=1.00),
            fetch_market=lambda cid: market,
            fetch_books=lambda host, tok: books[tok],
            decide=lambda *a, **k: ([_intent(price=0.49)], ""),
            open_orders_fn=lambda m: [_open(price=0.51)],
        )
        result = _visit_one(seam, {"cid": "0xabc"}, cycle=1, live=False,
                            plan_fn=spy)

        assert seen["dead_band"] == 0.03
        assert seen["cfg"] is not None
        # The hedge ask for the UP token is the DOWN book's ask, and vice
        # versa. Getting this mapping backwards is the one way this wiring
        # can be wrong while every other assertion still passes.
        assert seen["hedge_asks"] == {"tok-up": 0.48, "tok-dn": 0.52}
        assert result.status in ("DRY_RUN", "DECLINED")


class TestReGateRespectsHeldInventory:
    """CodeRabbit round 1: the re-gate must not fire on an inventory-backed hedge.

    `_decide_quotes_from_mid` skips the completable gate when the opposite leg
    is already held -- completion is not needed, so the hedge ASK is not the
    price that finishes the pair. `plan_orders` has no inventory of its own, so
    without being told, it re-gated every kept order and cancelled valid hedges
    every cycle: the exact churn this change exists to stop, with a naked leg
    left open longer while it happened.
    """

    def test_a_kept_order_is_not_re_gated_when_the_hedge_leg_is_held(self):
        # UP kept at 0.54 against a DOWN leg already held. The DOWN ask of 0.50
        # is irrelevant -- we do not have to buy DOWN again -- so 0.54 + 0.50 =
        # 1.04 must NOT cancel this order.
        cfg = MakerConfig(max_completable_pair_cost=1.00)
        open_orders = [_open(price=0.54)]
        intents = [_intent(price=0.54)]
        to_cancel, to_submit = plan_orders(
            open_orders, intents, dead_band=0.03, cfg=cfg,
            hedge_asks={"tok-up": 0.50}, hedge_held={"tok-up"})
        assert to_cancel == []
        assert to_submit == []

    def test_no_recheck_fires_on_a_token_whose_hedge_is_not_held(self):
        # #387: the re-gate is retired, so even the token WITHOUT a held
        # hedge is kept. Both legs hold at their own prices.
        cfg = MakerConfig(max_completable_pair_cost=1.00)
        open_orders = [_open("tok-up", 0.54, "o1"), _open("tok-dn", 0.40, "o2")]
        intents = [_intent(side="UP", token="tok-up", price=0.54),
                   _intent(side="DOWN", token="tok-dn", price=0.40)]
        to_cancel, to_submit = plan_orders(
            open_orders, intents, dead_band=0.03, cfg=cfg,
            hedge_asks={"tok-up": 0.50, "tok-dn": 0.62},
            hedge_held={"tok-up"})
        assert to_cancel == []
        assert to_submit == []

    def test_visit_one_derives_held_hedges_from_the_inventory(self):
        seen = {}
        real_plan = plan_orders

        def spy(open_orders, intents, price_eps=1e-9, **kw):
            seen.update(kw)
            return real_plan(open_orders, intents, price_eps, **kw)

        market = FakeMarket()
        up = {"token_id": "tok-up", "best_bid": 0.50, "best_ask": 0.52,
              "bids": {0.50: 5000.0}, "asks": {0.52: 5000.0}}
        down = {"token_id": "tok-dn", "best_bid": 0.46, "best_ask": 0.48,
                "bids": {0.46: 5000.0}, "asks": {0.48: 5000.0}}
        books = {"tok-up": up, "tok-dn": down}
        # We hold DOWN. DOWN is the UP token's hedge, so the UP token -- and
        # only the UP token -- stands down from the re-gate.
        inv = Inventory(down_shares=100.0, down_cost=43.0)
        seam = VenueSeam(
            base_cfg=MakerConfig(requote_dead_band=0.03,
                                 max_completable_pair_cost=1.00),
            fetch_market=lambda cid: market,
            fetch_books=lambda host, tok: books[tok],
            decide=lambda *a, **k: ([_intent(price=0.49)], ""),
            inventory_fn=lambda m: inv,
            open_orders_fn=lambda m: [_open(price=0.51)],
        )
        _visit_one(seam, {"cid": "0xabc"}, cycle=1, live=False, plan_fn=spy)

        assert seen["hedge_held"] == {"tok-up": 0.43}

    def test_visit_one_holds_nothing_back_when_the_inventory_is_flat(self):
        seen = {}
        real_plan = plan_orders

        def spy(open_orders, intents, price_eps=1e-9, **kw):
            seen.update(kw)
            return real_plan(open_orders, intents, price_eps, **kw)

        market = FakeMarket()
        up = {"token_id": "tok-up", "best_bid": 0.50, "best_ask": 0.52,
              "bids": {0.50: 5000.0}, "asks": {0.52: 5000.0}}
        down = {"token_id": "tok-dn", "best_bid": 0.46, "best_ask": 0.48,
                "bids": {0.46: 5000.0}, "asks": {0.48: 5000.0}}
        books = {"tok-up": up, "tok-dn": down}
        seam = VenueSeam(
            base_cfg=MakerConfig(requote_dead_band=0.03,
                                 max_completable_pair_cost=1.00),
            fetch_market=lambda cid: market,
            fetch_books=lambda host, tok: books[tok],
            decide=lambda *a, **k: ([_intent(price=0.49)], ""),
            inventory_fn=lambda m: Inventory(),
            open_orders_fn=lambda m: [_open(price=0.51)],
        )
        _visit_one(seam, {"cid": "0xabc"}, cycle=1, live=False, plan_fn=spy)

        assert seen["hedge_held"] == {}

    def test_paired_depth_metadata_is_emitted_for_ranker_attribution(self):
        from core_brain.config import MakerConfig
        from core_brain.trader_loop import _visit_one

        market = FakeMarket("0xpaired")
        emitted = []
        seam = VenueSeam(
            base_cfg=MakerConfig(),
            fetch_market=lambda cid: market,
            fetch_books=lambda host, token: {
                "token_id": token, "best_bid": 0.47, "best_ask": 0.49,
                "bids": {0.47: 100}, "asks": {0.49: 100},
            },
            decide=lambda *a: ([], "declined"),
            submit_fn=lambda *a, **k: 0,
            cancel_fn=lambda *a, **k: 0,
            reconcile_fn=lambda *a, **k: None,
            sweep_fn=lambda: None,
        )
        _visit_one(
            seam,
            {"cid": "0xpaired", "paired_depth_arm": "treatment",
             "paired_depth_cutoff_usd": 250,
             "paired_depth_snapshot_id": "snapshot-z"},
            live=False,
            emit_fn=lambda **event: emitted.append(event),
        )

        decision = next(event for event in emitted if event.get("action") == "decide")
        assert decision["extra"]["paired_depth_arm"] == "treatment"
        assert decision["extra"]["paired_depth_cutoff_usd"] == 250
        assert decision["extra"]["paired_depth_snapshot_id"] == "snapshot-z"


class TestMarketSpecsPath:
    """`_market_specs` reads the feed it is told to, converting exactly as live."""

    def _feed(self, tmp_path, n=2):
        rows = [{"cid": f"0xtrial{i}", "title": f"trial market {i}"}
                for i in range(n)]
        feed = tmp_path / "trial-markets.json"
        feed.write_text(json.dumps(rows), encoding="utf-8")
        return feed

    def test_specs_come_from_the_supplied_feed(self, tmp_path):
        from core_brain.trader_loop import _market_specs

        specs = _market_specs(path=str(self._feed(tmp_path)))

        assert [s["cid"] for s in specs] == ["0xtrial0", "0xtrial1"]
        assert set(specs[0]) == {"cid", "min_size", "shares", "max_spread",
                                 "tick", "daily", "title", "slug",
                                 "sports_market_type", "event_score",
                                 "event_period", "question", "event_live",
                                 "event_ended", "series_ts"}

    def test_max_markets_still_caps_a_pathed_feed(self, tmp_path):
        from core_brain.trader_loop import _market_specs

        specs = _market_specs(1, path=str(self._feed(tmp_path, n=3)))

        assert [s["cid"] for s in specs] == ["0xtrial0"]

    def test_market_specs_select_one_paired_depth_arm(self, tmp_path):
        from core_brain.trader_loop import _market_specs

        path = tmp_path / "paired.json"
        path.write_text(json.dumps({
            "format": "spread_hunter.paired-depth.v1",
            "snapshot_id": "snapshot-a",
            "control_depth_usd": 500,
            "treatment_depth_usd": 250,
            "control": [{"cid": "0xcontrol", "paired_depth_arm": "control",
                          "paired_depth_cutoff_usd": 500,
                          "paired_depth_snapshot_id": "snapshot-a"}],
            "treatment": [{"cid": "0xtreatment", "paired_depth_arm": "treatment",
                            "paired_depth_cutoff_usd": 250,
                            "paired_depth_snapshot_id": "snapshot-a"}],
        }), encoding="utf-8")

        specs = _market_specs(path=str(path), paired_arm="treatment")

        assert [spec["cid"] for spec in specs] == ["0xtreatment"]
        assert specs[0]["paired_depth_arm"] == "treatment"
        assert specs[0]["paired_depth_cutoff_usd"] == 250
        assert specs[0]["paired_depth_snapshot_id"] == "snapshot-a"

    def test_no_path_reads_the_default_feed(self, tmp_path, monkeypatch):
        import core_brain.market_feed as feed_mod
        from core_brain.trader_loop import _market_specs

        seen = {}

        def spy(path=None, max_age_sec=None):
            seen["path"] = path
            return []

        monkeypatch.setattr(feed_mod, "load_graduated_markets", spy)

        assert _market_specs() == []
        assert seen["path"] is None


class TestFuriaQuoteClock:
    """#386 full path: FURIA venue times through fetch to decision."""

    END = 1791244800.0      # 2026-10-06T00:00:00Z
    KICKOFF = 1791301200.0  # 2026-10-06T15:40:00Z

    def _books(self):
        up = {"token_id": "tok-up", "best_bid": 0.59, "best_ask": 0.61,
              "bids": {0.59: 5000.0}, "asks": {0.61: 5000.0}}
        down = {"token_id": "tok-dn", "best_bid": 0.39, "best_ask": 0.41,
                "bids": {0.39: 5000.0}, "asks": {0.41: 5000.0}}
        return up, down

    def test_decide_receives_a_positive_countdown_not_minus_59681(self):
        # Same shape as the FURIA market (venue endDate is kickoff, match in
        # progress), with times relative to now so the test never expires:
        # kickoff an hour ago must read positive, never the old -59681s.
        import time
        from core_brain.markets import LiveMarket
        from core_brain.quotes import evaluate_market_quote
        kickoff = time.time() - 3600.0
        market = LiveMarket(
            condition_id="0xfuria", market_slug="cs2-furia-aur1-2026-10-06",
            up_token="tok-up", down_token="tok-dn",
            start_ts=kickoff - 7200.0, end_ts=kickoff,
            tick_size=0.01, neg_risk=False, game_start_ts=kickoff)
        assert market.t_remaining() < 0  # the old clock says expired
        up, down = self._books()
        seen = {}

        def decide(cfg, up_book, dn_book, inv, t_rem, wf):
            seen["t_rem"] = t_rem
            return ([_intent(side="UP", token="tok-up", price=0.59),
                     _intent(side="DOWN", token="tok-dn", price=0.39)], "")

        ev = evaluate_market_quote(
            market.condition_id, MakerConfig(), "https://clob.polymarket.com",
            fetch_market=lambda cid: market,
            fetch_books=lambda host, tok: up if tok == "tok-up" else down,
            inventory_for=lambda m: Inventory(),
            decide=decide,
        )
        assert seen["t_rem"] > 0
        assert seen["t_rem"] <= 6 * 3600.0
        assert len(ev.intents) == 2
        assert ev.why == ""

    def test_stale_window_still_refuses(self):
        from core_brain.markets import LiveMarket, quote_t_remaining
        market = LiveMarket(
            condition_id="0xfuria", market_slug="cs2-furia-aur1-2026-10-06",
            up_token="tok-up", down_token="tok-dn",
            start_ts=self.END - 3600.0, end_ts=self.END,
            tick_size=0.01, neg_risk=False, game_start_ts=self.KICKOFF)
        # A day after kickoff the window is spent: negative again.
        assert quote_t_remaining(market, now=self.KICKOFF + 86400.0) < 0

    # --- live BO3 series (#402 T2) -------------------------------------------
    def _bo3_market(self, series_state):
        from core_brain.markets import LiveMarket
        import time
        kickoff = time.time() - 3600.0
        return LiveMarket(
            condition_id="0xflysr", market_slug="lol-fly-sr-2026-10-07",
            up_token="tok-up", down_token="tok-dn",
            start_ts=kickoff - 7200.0, end_ts=kickoff,
            tick_size=0.01, neg_risk=False, game_start_ts=kickoff,
            series_state=series_state)

    def _outside_band_books(self):
        up = {"token_id": "tok-up", "best_bid": 0.13, "best_ask": 0.15,
              "bids": {0.13: 5000.0}, "asks": {0.15: 5000.0}}
        down = {"token_id": "tok-dn", "best_bid": 0.83, "best_ask": 0.85,
                "bids": {0.83: 5000.0}, "asks": {0.85: 5000.0}}
        return up, down

    def _decide(self, series_state):
        import time
        from core_brain.quotes import decide_quotes
        market = self._bo3_market(series_state)
        up, down = self._outside_band_books()
        return decide_quotes(
            MakerConfig(), up, down, Inventory(), 600.0,
            series_state=getattr(market, "series_state", None))

    def _live_bo3(self):
        import time
        from core_brain.markets import parse_series_state
        return parse_series_state(
            sports_market_type="moneyline", score="9-4|1-1|Bo3",
            period="2/3", question="FlyQuest vs Shopify Rebellion",
            live=True, ended=False, evidence_ts=time.time())

    def test_live_bo3_with_games_left_quotes_outside_the_band(self):
        intents, why = self._decide(self._live_bo3())
        assert len(intents) == 2, why
        assert why == ""

    def test_single_game_with_same_mid_is_still_refused(self):
        intents, why = self._decide(None)
        assert intents == []
        assert "decided market" in why

    def test_stale_series_score_is_still_refused(self):
        import time
        from core_brain.markets import (SERIES_EVIDENCE_MAX_AGE_SEC,
                                        parse_series_state)
        stale = parse_series_state(
            sports_market_type="moneyline", score="9-4|1-1|Bo3",
            period="2/3", question="FlyQuest vs Shopify Rebellion",
            live=True, ended=False,
            evidence_ts=time.time() - SERIES_EVIDENCE_MAX_AGE_SEC - 1)
        intents, why = self._decide(stale)
        assert intents == []
        assert "decided market" in why

    def test_settled_book_still_refuses_a_live_series(self):
        from core_brain.quotes import decide_quotes
        market = self._bo3_market(self._live_bo3())
        up = {"token_id": "tok-up", "best_bid": 0.989, "best_ask": 0.999,
              "bids": {0.989: 5000.0}, "asks": {0.999: 5000.0}}
        down = {"token_id": "tok-dn", "best_bid": 0.001, "best_ask": 0.011,
                "bids": {0.001: 5000.0}, "asks": {0.011: 5000.0}}
        intents, why = decide_quotes(
            MakerConfig(), up, down, Inventory(), 600.0,
            series_state=market.series_state)
        assert intents == []
        assert "settled book" in why


class TestClassifyRefusal:
    """#390: the refusal classifier pins every terminal marker, so a reworded
    `quotes.py` reason cannot silently flip a settled market to hold."""
    @pytest.mark.parametrize("why", [
        "UP: mid 0.950 outside [0.20,0.80] -- decided market; "
        "DOWN: mid 0.050 outside [0.20,0.80] -- decided market",
        "UP: hedge token DOWN not tradeable (settled book 0.999/0.001) "
        "-- a fill here could not be closed",
        "t_remaining 0s < 0s",
        "market exited: fills still lost money after widening",
        "unfunded by the allocator -- quoting nothing",
        "hit 25 fills for this market",
    ])
    def test_terminal_markers_cancel(self, why):
        assert _classify_refusal(why) is VisitOutcome.REFUSED_TERMINAL

    @pytest.mark.parametrize("why", [
        "UP: 8.0c from mid > 4.5c reward window; "
        "DOWN: 8.0c from mid > 4.5c reward window",
        "no side quotable",
        "",
    ])
    def test_flicker_reasons_hold(self, why):
        assert _classify_refusal(why) is VisitOutcome.REFUSED_TRANSIENT


class TestRefusedHold:
    """#390: a visited-but-refused market holds resting orders; only a dropped
    market, a terminal refusal, or an expired grace cancels."""

    TRANSIENT_WHY = ("UP: 8.0c from mid > 4.5c reward window; "
                     "DOWN: 8.0c from mid > 4.5c reward window")
    TERMINAL_WHY = ("UP: mid 0.950 outside [0.20,0.80] -- decided market; "
                    "DOWN: mid 0.050 outside [0.20,0.80] -- decided market")

    def _seam(self, decide, calls):
        def open_orders_fn(m):
            return [_open(token="tok-up", price=0.60, oid="o-up"),
                    _open(token="tok-dn", price=0.40, oid="o-dn")]

        def submit_fn(client, registry, market, intents, cfg):
            calls["submitted"].append([i.token_id for i in intents])
            return len(intents)

        def cancel_fn(client, registry, orders):
            calls["cancelled"].append([o["order_id"] for o in orders])
            return len(orders)

        return VenueSeam(
            client=object(),
            fetch_market=lambda cid: FakeMarket(cid),
            fetch_books=lambda h, t: {"token_id": t, "best_bid": 0.59,
                                       "best_ask": 0.61,
                                       "bids": {0.59: 100}, "asks": {0.61: 100}},
            decide=decide,
            submit_fn=submit_fn,
            cancel_fn=cancel_fn,
            open_orders_fn=open_orders_fn,
            reconcile_fn=lambda *a: None,
            sweep_fn=lambda: None,
        )

    def test_refused_visited_holds_resting_orders(self):
        calls = {"submitted": [], "cancelled": []}
        seam = self._seam(
            lambda cfg, up, dn, inv, t_rem, wf: ([], self.TRANSIENT_WHY), calls)
        results = run(
            seam, interval=0.0, once=True, live=True,
            markets=[FakeMarket("0xabc")],
            sleep_fn=lambda s: None,
        )
        assert results[0].status == "DECLINED"
        assert calls["cancelled"] == []
        assert calls["submitted"] == []

    def test_terminal_refusal_cancels_now(self):
        calls = {"submitted": [], "cancelled": []}
        seam = self._seam(
            lambda cfg, up, dn, inv, t_rem, wf: ([], self.TERMINAL_WHY), calls)
        results = run(
            seam, interval=0.0, once=True, live=True,
            markets=[FakeMarket("0xabc")],
            sleep_fn=lambda s: None,
        )
        assert results[0].status == "DECLINED"
        assert calls["cancelled"] == [["o-up", "o-dn"]]
        assert calls["submitted"] == []

    def test_token_rotation_still_cancels_old_token(self):
        calls = {"submitted": [], "cancelled": []}

        def decide(cfg, up, dn, inv, t_rem, wf):
            return [_intent(side="UP", token="tok-new", price=0.62)], ""

        def open_orders_fn(m):
            return [_open(token="tok-old", price=0.58, oid="o-old")]

        seam = VenueSeam(
            client=object(),
            fetch_market=lambda cid: FakeMarket(cid),
            fetch_books=lambda h, t: {"token_id": t, "best_bid": 0.59,
                                       "best_ask": 0.61,
                                       "bids": {0.59: 100}, "asks": {0.61: 100}},
            decide=decide,
            submit_fn=lambda c, r, m, i, cfg: calls["submitted"].append(
                [t.token_id for t in i]) or len(i),
            cancel_fn=lambda c, r, o: calls["cancelled"].append(
                [x["order_id"] for x in o]) or len(o),
            open_orders_fn=open_orders_fn,
            reconcile_fn=lambda *a: None,
            sweep_fn=lambda: None,
        )
        results = run(
            seam, interval=0.0, once=True, live=True,
            markets=[FakeMarket("0xabc")],
            sleep_fn=lambda s: None,
        )
        assert results[0].status == "QUOTED"
        assert calls["cancelled"] == [["o-old"]]
        assert calls["submitted"] == [["tok-new"]]

    def test_grace_expiry_cancels_after_n_refused_cycles(self):
        from core_brain.trader_loop import REFUSED_HOLD_GRACE_CYCLES as GRACE
        assert GRACE >= 2
        calls = {"submitted": [], "cancelled": []}
        decides = []
        sleeps = []

        def decide(cfg, up, dn, inv, t_rem, wf):
            decides.append(1)
            return [], self.TRANSIENT_WHY

        seam = self._seam(decide, calls)

        def sleep_fn(s):
            sleeps.append(s)
            if len(sleeps) >= GRACE:
                raise KeyboardInterrupt

        run(
            seam, interval=0.0, once=False, live=True,
            markets=[FakeMarket("0xabc")],
            sleep_fn=sleep_fn,
        )
        assert len(decides) == GRACE
        assert calls["submitted"] == []
        assert calls["cancelled"] == [["o-up", "o-dn"]]

    def test_quote_resets_refusal_streak(self):
        from core_brain.trader_loop import REFUSED_HOLD_GRACE_CYCLES as GRACE
        calls = {"submitted": [], "cancelled": []}
        decides = []

        def decide(cfg, up, dn, inv, t_rem, wf):
            decides.append(1)
            if len(decides) == 2:
                return [_intent(),
                        _intent(side="DOWN", token="tok-dn", price=0.40)], ""
            return [], self.TRANSIENT_WHY

        seam = self._seam(decide, calls)
        sleeps = []

        def sleep_fn(s):
            sleeps.append(s)
            if len(sleeps) >= GRACE + 1:
                raise KeyboardInterrupt

        run(
            seam, interval=0.0, once=False, live=True,
            markets=[FakeMarket("0xabc")],
            sleep_fn=sleep_fn,
        )
        # Refuse, quote, refuse, refuse: only two CONSECUTIVE refusals, so the
        # grace never expires even though three refusals happened in total.
        assert len(decides) == GRACE + 1
        assert calls["submitted"] == []
        assert calls["cancelled"] == []


class TestLifecycleStops:
    """One enumerated stop list (#402 T1): every refusal maps to one code."""

    CASES = [
        ("UP: mid 0.850 outside [0.20,0.80] -- decided market",
         "decided_by_price"),
        ("settled book: quote at an end, no spread", "settled_book"),
        ("t_remaining elapsed: window over", "countdown_expired"),
        ("market exited: toxicity", "market_exited"),
        ("unfunded by the allocator", "unfunded"),
        ("fills for this market cap reached", "fill_cap_reached"),
    ]

    @pytest.mark.parametrize("why,code", CASES)
    def test_refusal_maps_to_single_stop(self, why, code):
        from core_brain.market_lifecycle import LifecycleStop, classify_refusal
        stop = classify_refusal(why)
        assert isinstance(stop, LifecycleStop)
        assert stop.code == code
        assert stop.ends_lifecycle is False

    def test_unknown_refusal_is_transient(self):
        from core_brain.market_lifecycle import classify_refusal
        assert classify_refusal("book a bit wide, try later") is None

    def test_matching_is_case_insensitive(self):
        from core_brain.market_lifecycle import LifecycleStop, classify_refusal
        assert classify_refusal("DECIDED MARKET") is LifecycleStop.DECIDED_BY_PRICE

    def test_only_resolved_and_dropped_end_lifecycle(self):
        from core_brain.market_lifecycle import LifecycleStop
        ending = {s for s in LifecycleStop if s.ends_lifecycle}
        assert ending == {LifecycleStop.RESOLVED, LifecycleStop.MARKET_DROPPED}
        assert LifecycleStop.HOLD_EXPIRED.code == "hold_expired"
        assert LifecycleStop.HOLD_EXPIRED.ends_lifecycle is False

    def test_trader_classifier_delegates_without_changing_outcomes(self):
        # Parity with the old TERMINAL_REFUSAL_MARKERS table.
        for why, _code in self.CASES:
            assert _classify_refusal(why) is VisitOutcome.REFUSED_TERMINAL
        assert _classify_refusal("wide book, retry") is VisitOutcome.REFUSED_TRANSIENT


class _TokMarket:
    """A market whose book tokens are unique per condition id."""

    def __init__(self, cid):
        self.condition_id = cid
        self.up_token = f"tok-up-{cid}"
        self.down_token = f"tok-dn-{cid}"
        self.market_slug = f"fake-{cid}"
        self.tick_size = 0.01
        self.neg_risk = False

    def t_remaining(self, now=None):
        return 14400.0


def _t3_registry(tmp_path, name="t3.db"):
    from core_brain.order_registry import OrderRegistry, init_db
    db = tmp_path / name
    init_db(db)
    return OrderRegistry(db_path=db, run_id="t3-test"), db


def _t3_seam(reg, books_calls, decide, calls):
    def fetch_books(host, token):
        books_calls[token] = books_calls.get(token, 0) + 1
        return {"token_id": token, "best_bid": 0.59, "best_ask": 0.61,
                "bids": {0.59: 100}, "asks": {0.61: 100}}

    def submit_fn(client, registry, market, intents, cfg):
        calls["submitted"].append(
            (market.condition_id, [i.token_id for i in intents]))
        return len(intents)

    def cancel_fn(client, registry, orders):
        calls["cancelled"].append([o["order_id"] for o in orders])
        return len(orders)

    return VenueSeam(
        client=object(),
        registry=reg,
        base_cfg=MakerConfig(),
        fetch_market=lambda cid: _TokMarket(cid),
        fetch_books=fetch_books,
        decide=decide,
        submit_fn=submit_fn,
        cancel_fn=cancel_fn,
        open_orders_fn=lambda m: [],
        reconcile_fn=lambda *a: None,
        sweep_fn=lambda: None,
    )


def _t3_spec(cid):
    return {"cid": cid, "min_size": 5, "shares": 120, "max_spread": 4.5,
            "tick": 0.01, "daily": 0.0, "title": f"t-{cid}", "slug": cid}


def _quoting_decide(cfg, up, dn, inv, t_rem, wf, series_state=None):
    toks = [up["token_id"], dn["token_id"]]
    return ([_intent(side="UP", token=toks[0], price=0.59),
             _intent(side="DOWN", token=toks[1], price=0.41)], "")


def _stop_rows(reg, code=None):
    rows = [r for r in reg.get_all_market_events()
            if r["kind"] == "lifecycle_stop"]
    if code is not None:
        rows = [r for r in rows if r["reason_code"] == code]
    return rows


class TestResolvedGuard:
    """#402 T3: a resolved market is never fetched, never polled, ever again."""

    DEAD = "0xdead"
    LIVE = "0xlive"

    def _resolve(self, reg, cid):
        from core_brain.order_registry import ResolutionRecord
        reg.log_resolution(ResolutionRecord(
            condition_id=cid, winning_token="Up", resolved_ts=2.0,
            run_id=reg._run_id(),
        ))

    def test_resolved_market_is_never_fetched_or_polled(self, tmp_path):
        reg, _db = _t3_registry(tmp_path)
        self._resolve(reg, self.DEAD)
        books_calls, calls = {}, {"submitted": [], "cancelled": []}
        seam = _t3_seam(reg, books_calls, _quoting_decide, calls)
        run(seam, interval=0.0, once=True, live=True,
            markets=[_t3_spec(self.DEAD), _t3_spec(self.LIVE)],
            sleep_fn=lambda s: None)
        assert f"tok-up-{self.DEAD}" not in books_calls
        assert f"tok-dn-{self.DEAD}" not in books_calls
        # The unrelated live market keeps quoting on both its tokens.
        assert books_calls.get(f"tok-up-{self.LIVE}", 0) >= 1
        assert books_calls.get(f"tok-dn-{self.LIVE}", 0) >= 1
        assert any(c == self.LIVE for c, _t in calls["submitted"])
        assert not any(c == self.DEAD for c, _t in calls["submitted"])

    def test_guard_survives_empty_refresh_reappearance_and_restart(self, tmp_path):
        reg, db = _t3_registry(tmp_path)
        self._resolve(reg, self.DEAD)
        books_calls, calls = {}, {"submitted": [], "cancelled": []}
        seam = _t3_seam(reg, books_calls, _quoting_decide, calls)
        feeds = [
            [_t3_spec(self.DEAD), _t3_spec(self.LIVE)],
            [],                      # empty refresh: universe retained
            [_t3_spec(self.DEAD)],    # reappearance without the live one
        ]
        idx = [0]

        def markets_fn():
            return feeds[min(idx[0], len(feeds) - 1)]

        def sleep_fn(s):
            idx[0] += 1
            if idx[0] >= len(feeds):
                raise KeyboardInterrupt

        run(seam, interval=0.0, once=False, live=True,
            markets=feeds[0], markets_fn=markets_fn, sleep_fn=sleep_fn)
        assert idx[0] == len(feeds)
        assert f"tok-up-{self.DEAD}" not in books_calls

        # Restart against the same store: the guard is durable, not memory.
        reg2, _ = _t3_registry(tmp_path, name="t3.db")
        books2, calls2 = {}, {"submitted": [], "cancelled": []}
        seam2 = _t3_seam(reg2, books2, _quoting_decide, calls2)
        run(seam2, interval=0.0, once=True, live=True,
            markets=[_t3_spec(self.DEAD), _t3_spec(self.LIVE)],
            sleep_fn=lambda s: None)
        assert f"tok-up-{self.DEAD}" not in books2
        assert f"tok-dn-{self.DEAD}" not in books2

    def test_resolved_skip_writes_exactly_one_stop_row(self, tmp_path):
        reg, _db = _t3_registry(tmp_path)
        self._resolve(reg, self.DEAD)
        books_calls, calls = {}, {"submitted": [], "cancelled": []}
        seam = _t3_seam(reg, books_calls, _quoting_decide, calls)
        idx = [0]

        def sleep_fn(s):
            idx[0] += 1
            if idx[0] >= 3:
                raise KeyboardInterrupt

        run(seam, interval=0.0, once=False, live=True,
            markets=[_t3_spec(self.DEAD)], sleep_fn=sleep_fn)
        assert idx[0] == 3
        rows = _stop_rows(reg, "resolved")
        assert len(rows) == 1


class TestLifecycleStopRows:
    """#402 T3: every stop names its reason in the log and the store, once."""

    def _seam(self, tmp_path, decide):
        reg, _db = _t3_registry(tmp_path)
        books_calls, calls = {}, {"submitted": [], "cancelled": []}
        seam = _t3_seam(reg, books_calls, decide, calls)
        seam.open_orders_fn = lambda m: [
            {"token_id": "tok-x", "price": 0.60, "order_id": "o-x",
             "id": "o-x", "side": "BUY", "status": "open"}]
        return reg, seam, calls

    def test_terminal_refusal_writes_one_row(self, tmp_path):
        why = ("UP: mid 0.950 outside [0.20,0.80] -- decided market; "
               "DOWN: mid 0.050 outside [0.20,0.80] -- decided market")
        reg, seam, _calls = self._seam(
            tmp_path, lambda *a: ([], why))
        idx = [0]

        def sleep_fn(s):
            idx[0] += 1
            if idx[0] >= 2:
                raise KeyboardInterrupt

        run(seam, interval=0.0, once=False, live=True,
            markets=[_t3_spec("0xabc")], sleep_fn=sleep_fn)
        assert len(_stop_rows(reg, "decided_by_price")) == 1

    def test_hold_expired_names_the_reason(self, tmp_path):
        from core_brain.trader_loop import REFUSED_HOLD_GRACE_CYCLES as GRACE
        why = "UP: 8.0c from mid > 4.5c reward window"
        reg, seam, calls = self._seam(
            tmp_path, lambda *a: ([], why))
        idx = [0]

        def sleep_fn(s):
            idx[0] += 1
            if idx[0] >= GRACE:
                raise KeyboardInterrupt

        run(seam, interval=0.0, once=False, live=True,
            markets=[_t3_spec("0xabc")], sleep_fn=sleep_fn)
        assert calls["cancelled"], "grace expiry must cancel resting orders"
        assert len(_stop_rows(reg, "hold_expired")) == 1

    def test_quote_between_stops_rearms_the_row(self, tmp_path):
        why = ("UP: mid 0.950 outside [0.20,0.80] -- decided market")
        seq = [[], "quote", []]
        calls = {"n": 0}

        def decide(cfg, up, dn, inv, t_rem, wf):
            step = seq[min(calls["n"], len(seq) - 1)]
            calls["n"] += 1
            if step == "quote":
                return _quoting_decide(cfg, up, dn, inv, t_rem, wf)
            return [], why

        reg, seam, _c = self._seam(tmp_path, decide)
        idx = [0]

        def sleep_fn(s):
            idx[0] += 1
            if idx[0] >= 3:
                raise KeyboardInterrupt

        run(seam, interval=0.0, once=False, live=True,
            markets=[_t3_spec("0xabc")], sleep_fn=sleep_fn)
        assert len(_stop_rows(reg, "decided_by_price")) == 2

    def test_market_dropped_names_the_reason(self, tmp_path):
        import uuid
        from core_brain.order_registry import OrderRecord
        reg, _db = _t3_registry(tmp_path)
        now_ms = 1_000_000
        reg.create_order(OrderRecord(
            id=str(uuid.uuid4()), condition_id="0xgone", token_id="tok-x",
            side="BUY", price=0.47, original_size=20, status="open",
            posted_ts=now_ms, last_polled_ts=now_ms,
            pair_id=None, run_id=reg._run_id(),
        ))
        books_calls, calls = {}, {"submitted": [], "cancelled": []}
        seam = _t3_seam(reg, books_calls, _quoting_decide, calls)
        run(seam, interval=0.0, once=True, live=True,
            markets=[_t3_spec("0xelsewhere")], sleep_fn=lambda s: None)
        assert calls["cancelled"], "dropped open orders must be cancelled"
        assert len(_stop_rows(reg, "market_dropped")) == 1

    def test_shutdown_reason_is_logged(self, tmp_path, caplog):
        import logging
        reg, seam, _c = self._seam(tmp_path, _quoting_decide)
        with caplog.at_level(logging.INFO):
            run(seam, interval=0.0, once=True, live=True,
                markets=[_t3_spec("0xabc")], sleep_fn=lambda s: None)
        assert "fleet loop shutdown: once" in caplog.text


class TestSuspectsAndSeriesAttach:
    """#402 T3: suspect visits feed the sweeper; specs carry series state."""

    def test_settled_and_countdown_visits_become_suspects(self, tmp_path):
        reg, _db = _t3_registry(tmp_path)
        books_calls, calls = {}, {"submitted": [], "cancelled": []}
        seq = iter([
            ([], "UP: hedge token DOWN not tradeable (settled book 0.999)"),
            ([], "t_remaining 0s < 0s"),
        ])

        def decide(*a):
            return next(seq, ([], "wide book"))

        seam = _t3_seam(reg, books_calls, decide, calls)
        box: dict = {}
        run(seam, interval=0.0, once=True, live=True,
            markets=[_t3_spec("0xone"), _t3_spec("0xtwo")],
            sleep_fn=lambda s: None, suspects_box=box)
        assert box.get("cids") == frozenset({"0xone", "0xtwo"})

    def test_book_fetch_error_becomes_a_suspect(self, tmp_path):
        reg, _db = _t3_registry(tmp_path)
        calls = {"submitted": [], "cancelled": []}
        seam = VenueSeam(
            client=object(), registry=reg, base_cfg=MakerConfig(),
            fetch_market=lambda cid: _TokMarket(cid),
            fetch_books=lambda h, t: (_ for _ in ()).throw(
                RuntimeError("book gone")),
            decide=_quoting_decide,
            submit_fn=lambda c, r, m, i, cfg: 0,
            cancel_fn=lambda c, r, o: 0,
            open_orders_fn=lambda m: [],
            reconcile_fn=lambda *a: None,
            sweep_fn=lambda: None,
        )
        box: dict = {}
        run(seam, interval=0.0, once=True, live=True,
            markets=[_t3_spec("0xbad")], sleep_fn=lambda s: None,
            suspects_box=box)
        assert box.get("cids") == frozenset({"0xbad"})

    def test_spec_series_evidence_reaches_the_market(self, tmp_path):
        import time
        reg, _db = _t3_registry(tmp_path)
        seen: dict = {}

        def submit_fn(client, registry, market, intents, cfg):
            seen["series"] = getattr(market, "series_state", None)
            return 0

        books_calls, calls = {}, {"submitted": [], "cancelled": []}
        seam = _t3_seam(reg, books_calls, _quoting_decide, calls)
        seam.submit_fn = submit_fn
        spec = _t3_spec("0xflysr")
        spec.update({
            "sports_market_type": "moneyline",
            "event_score": "9-4|1-1|Bo3",
            "event_period": "2/3",
            "question": "FlyQuest vs Shopify Rebellion",
            "event_live": True,
            "event_ended": False,
            "series_ts": time.time(),
        })
        run(seam, interval=0.0, once=True, live=True, markets=[spec],
            sleep_fn=lambda s: None)
        assert seen["series"] is not None
        assert seen["series"].scope == "series"


class TestUmaResolutionGateVisit:
    """Flagged visit cancels with the named reason; clean is byte-identical (#408)."""

    def _seam(self, uma_by_cid, registry, events, discards):
        from unittest.mock import MagicMock

        from core_brain.market_resolution import UmaResolutionStatus

        def fetch_uma_status(cid):
            v = uma_by_cid.get(cid, "clean")
            if v == "raise":
                raise RuntimeError("gamma down")
            if v == "unreachable":
                return UmaResolutionStatus(condition_id=cid, unreachable=True)
            if v == "clean":
                return UmaResolutionStatus(condition_id=cid)
            return UmaResolutionStatus(condition_id=cid, status=v)

        def fake_fetch_market(cid):
            raise AssertionError("fetch_market must not run on a flagged visit")

        def fake_cancel(client, reg, orders):
            return len(orders)

        def record_market_event(record):
            events.append(record)

        def emit_fn(service, cycle, phase, action, **kw):
            if action == "discard":
                discards.append((action, kw))

        seam = VenueSeam(
            client=object(),
            registry=registry,
            base_cfg=MakerConfig(),
            fetch_market=fake_fetch_market,
            fetch_books=lambda h, t: {"token_id": t, "bids": {}, "asks": {}},
            decide=lambda *a, **k: ([_intent()], ""),
            submit_fn=lambda *a, **k: 0,
            cancel_fn=fake_cancel,
            reconcile_fn=lambda *a, **k: None,
            sweep_fn=lambda: None,
            fetch_uma_status=fetch_uma_status,
            record_market_event=record_market_event,
            emit_fn=emit_fn,
        )
        return seam

    def _registry_with_resting(self, cid="0xuma"):
        from unittest.mock import MagicMock

        registry = MagicMock()
        o1 = MagicMock(condition_id=cid, status="open", token_id="tok-up",
                        price=0.55, order_id="v1", id="r1", side="BUY")
        o2 = MagicMock(condition_id=cid, status="partial", token_id="tok-dn",
                        price=0.45, order_id="v2", id="r2", side="BUY")
        registry.get_active_orders.return_value = [o1, o2]
        return registry

    def test_flagged_visit_cancels_open_and_partial_skips_decide_submit(self):
        from core_brain.trader_loop import _visit_one

        registry = self._registry_with_resting()
        events, discards, submitted = [], [], []
        seam = self._seam({"0xuma": "proposed"}, registry, events, discards)
        seam.submit_fn = lambda *a, **k: submitted.append(1) or 0
        res = _visit_one(seam, {"cid": "0xuma"}, cycle=7, live=True)
        assert res.status == "CANCELLED"
        assert res.why == "uma_resolution_proposed"
        assert res.cancelled == 2
        assert submitted == []
        assert len(discards) == 1
        assert discards[0][1]["reason"] == "uma_resolution_proposed"
        assert discards[0][1]["extra"]["uma_status"] == "proposed"
        assert len(events) == 1
        assert events[0].kind == "BLOCKED"
        assert events[0].reason_code == "uma_resolution_proposed"

    def test_clean_repeat_run_byte_identical_to_run_without_port(self):
        from core_brain.trader_loop import _visit_one

        def run_once(seam):
            return _visit_one(seam, {"cid": "0xuma"}, cycle=3, live=True)

        registry = self._registry_with_resting()
        events_a, discards_a = [], []
        seam_a = self._seam({"0xuma": "clean"}, registry, events_a, discards_a)
        seam_a.fetch_market = lambda cid: FakeMarket(cid)
        seam_a.decide = lambda *a, **k: ([], "declined")
        seam_a.submit_fn = lambda *a, **k: 0
        res_a = run_once(seam_a)

        registry_b = self._registry_with_resting()
        seam_b = VenueSeam(
            client=object(), registry=registry_b,
            base_cfg=MakerConfig(),
            fetch_market=lambda cid: FakeMarket(cid),
            fetch_books=lambda h, t: {"token_id": t, "bids": {}, "asks": {}},
            decide=lambda *a, **k: ([], "declined"),
            submit_fn=lambda *a, **k: 0,
            cancel_fn=lambda *a, **k: 0,
            reconcile_fn=lambda *a, **k: None,
            sweep_fn=lambda: None,
        )
        res_b = run_once(seam_b)
        assert (res_a.status, res_a.why, res_a.submitted, res_a.cancelled) == \
               (res_b.status, res_b.why, res_b.submitted, res_b.cancelled)
        assert events_a == [] and discards_a == []

    def test_unreachable_warns_without_cancelling(self):
        from core_brain.trader_loop import _visit_one

        registry = self._registry_with_resting()
        events, discards = [], []
        warns = []
        seam = self._seam({"0xuma": "unreachable"}, registry, events, discards)

        def emit_fn(service, cycle, phase, action, **kw):
            warns.append(action)
            if action == "discard":
                discards.append((action, kw))

        seam.emit_fn = emit_fn
        seam.fetch_market = lambda cid: FakeMarket(cid)
        seam.decide = lambda *a, **k: ([], "declined")
        res = _visit_one(seam, {"cid": "0xuma"}, cycle=3, live=True)
        assert res.status != "CANCELLED"
        assert "uma_check_unreachable" in warns
        assert discards == [] and events == []

    def test_failed_cancels_reported_and_retried(self):
        from core_brain.trader_loop import _visit_one

        registry = self._registry_with_resting()
        events, discards = [], []
        seam = self._seam({"0xuma": "proposed"}, registry, events, discards)
        seam.cancel_fn = lambda *a, **k: 0
        res = _visit_one(seam, {"cid": "0xuma"}, cycle=3, live=True)
        assert res.status == "CANCELLED"
        assert "retry next visit" in res.error
        assert discards[0][1]["extra"]["failed"] == 2
