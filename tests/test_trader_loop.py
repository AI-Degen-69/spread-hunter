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
    _production_reconcile_fn,
    _visit_one,
    plan_orders,
    run,
)
from core_brain import trader_loop
from core_brain.config import MakerConfig
from core_brain.quotes import Inventory, QuoteIntent, decide_quotes


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
        cfg = MakerConfig(bot_pos_price_max_complete_usd=1.00)
        to_cancel, to_submit = plan_orders(open_orders, intents, cfg=cfg)
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
            cfg=MakerConfig(bot_pos_price_max_complete_usd=1.00))
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

    def test_carried_pair_id_is_reused_not_minted(self):
        # #397: a batch with a carried pair id keeps it through submit.
        # If the comment's claim and the code disagree again (carry dropped
        # or re-minted), this fails.
        intents = [
            _intent(side="UP", token="tok-up", price=0.71,
                    pair_id="carried-0xmkt"),
            _intent(side="DOWN", token="tok-dn", price=0.25,
                    pair_id="carried-0xmkt"),
        ]

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
        assert {o.pair_id for o in created} == {"carried-0xmkt"}

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
        cfg = MakerConfig(bot_pos_price_max_complete_usd=1.00)
        open_orders = [_open(price=0.60)]
        intents = [_intent(price=0.58)]
        to_cancel, to_submit = plan_orders(
            open_orders, intents, dead_band=0.03, cfg=cfg)
        assert to_cancel == []
        assert to_submit == []

    def test_a_kept_order_that_still_completes_under_the_cap_is_left_alone(self):
        cfg = MakerConfig(bot_pos_price_max_complete_usd=1.00)
        open_orders = [_open(price=0.60)]
        intents = [_intent(price=0.58)]
        to_cancel, to_submit = plan_orders(
            open_orders, intents, dead_band=0.03, cfg=cfg)
        assert to_cancel == []
        assert to_submit == []

    def test_dead_band_widths_hold_despite_failing_gate(self):
        # Issue #361 scenario, under no-re-check (#387):
        # Resting bid at 0.60, desired intent walks down to 0.55 (drift = 0.05).
        # Both dead-band widths hold the order and record no reason -- cost is
        # judged at placement, never after.
        cfg = MakerConfig(bot_pos_price_max_complete_usd=1.00)
        open_orders = [_open(price=0.60, oid="o1")]
        intents = [_intent(price=0.55)]

        # 1. Narrow dead band (0.03): out of band -- held anyway.
        reasons_narrow = {}
        to_cancel_narrow, to_submit_narrow = plan_orders(
            open_orders, intents, dead_band=0.03, cfg=cfg,
            reasons=reasons_narrow,
        )
        assert to_cancel_narrow == []
        assert reasons_narrow == {}
        assert to_submit_narrow == []

        # 2. Wide dead band (0.08): in band -- held anyway.
        reasons_wide = {}
        to_cancel_wide, to_submit_wide = plan_orders(
            open_orders, intents, dead_band=0.08, cfg=cfg,
            reasons=reasons_wide,
        )
        assert to_cancel_wide == []
        assert reasons_wide == {}
        assert to_submit_wide == []

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

    def test_resting_bids_survive_ask_rise(self):
        # #626: a resting pair at 0.73/0.23 (0.96 together) stays live when both
        # opposite asks spike far past $1.00 combined. A resting pair's
        # economics are its REST prices; the live ask is read only when a NEW
        # bid is judged.
        open_orders = [_open("tok-up", 0.73, "o-up"), _open("tok-dn", 0.23, "o-dn")]
        intents = [_intent(side="UP", token="tok-up", price=0.73),
                   _intent(side="DOWN", token="tok-dn", price=0.23)]
        reasons: dict = {}

        to_cancel, to_submit = plan_orders(
            open_orders, intents, dead_band=0.03,
            cfg=MakerConfig(bot_pos_price_max_complete_usd=1.00),
            reasons=reasons,
        )

        assert to_cancel == []
        assert to_submit == []
        assert reasons == {}

    def test_resting_bid_survives_retired_regate_math(self):
        # #626: 0.60 resting + 0.60 opposite ask = 1.20 -- the sum that used to
        # cancel this bid. The wanted token is kept once: no cancel, no reason,
        # and no duplicate posted beside it.
        open_orders = [_open("tok-up", 0.60, "o-up")]
        intents = [_intent(side="UP", token="tok-up", price=0.60)]
        reasons: dict = {}

        to_cancel, to_submit = plan_orders(
            open_orders, intents,
            cfg=MakerConfig(bot_pos_price_max_complete_usd=1.00),
            reasons=reasons,
        )

        assert to_cancel == []
        assert to_submit == []
        assert reasons == {}

    def test_planner_never_records_regate_reason(self):
        # #626: whatever the visit decided, there is no re-gate left to record
        # a reason for. This is the invariant, not one path: a reintroduced
        # cost re-check fails here.
        open_orders = [_open("tok-up", 0.60, "o-up"), _open("tok-dn", 0.23, "o-dn")]
        intents = [_intent(side="UP", token="tok-up", price=0.60),
                   _intent(side="DOWN", token="tok-dn", price=0.23)]
        reasons: dict = {}

        to_cancel, _ = plan_orders(
            open_orders, intents, dead_band=0.03,
            cfg=MakerConfig(bot_pos_price_max_complete_usd=1.00),
            reasons=reasons,
        )

        assert to_cancel == []
        assert "regate_pair_cost" not in reasons.values()


    def test_delta_top_up_intent_generated_when_shortfall_exceeds_minimum(self):
        # Moved here from the retired asymmetric-hold module (#753): a
        # preserved order whose size is short of the target by at least
        # `bot_quote_size_min_shares` gets a supplemental delta intent.
        open_order = {"token_id": "tok-up", "price": 0.60, "order_id": "o1",
                      "side": "BUY", "status": "open", "size": 10.0,
                      "pair_id": "pair-lc-1"}
        target_intent = QuoteIntent(
            side="UP", token_id="tok-up", price=0.60, size=20,
            mid=0.61, edge_vs_mid=0.01, pair_id="pair-lc-1"
        )
        cfg = MakerConfig(bot_quote_size_min_shares=5.0)
        to_cancel, to_submit = plan_orders(
            [open_order], [target_intent],
            preserve_order_ids={"o1"},
            lifecycle_pair_id="pair-lc-1",
            cfg=cfg,
        )
        assert to_cancel == []
        assert len(to_submit) == 1
        delta_intent = to_submit[0]
        assert delta_intent.pair_id == "pair-lc-1"
        assert delta_intent.token_id == "tok-up"
        assert delta_intent.size == 10
        assert delta_intent.price == 0.60


class TestRetiredRegatePurge:
    """#626: the per-cycle taker-ask re-gate is gone from the live path.

    The constant is what the retired re-gate named its cancels with, so its
    absence is the machine-checkable proof that no resting bid is re-gated any
    more. `"regate_pair_cost"` survives only as historical text in
    `cancel_report`, so rows written before #387 still read correctly.
    """

    def test_retired_regate_constant_is_gone(self):
        assert not hasattr(trader_loop, "CANCEL_REGATE_PAIR_COST")



class FakeMarket:
    def __init__(self, cid="0xabc", up_token="tok-up", down_token="tok-dn"):
        self.condition_id = cid
        self.up_token = up_token
        self.down_token = down_token
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
            seen["bot_risk_exposure_fleet_naked_usd"] = cfg.bot_risk_exposure_fleet_naked_usd
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
                fleet_state_fn=lambda r: {"bot_risk_exposure_fleet_naked_usd": 12.5},
            ),
            interval=0.0, once=True, live=False,
            markets=[FakeMarket("0xabc")],
            sleep_fn=lambda s: None,
        )
        assert seen["bot_risk_exposure_fleet_naked_usd"] == 12.5

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
            base_cfg=MakerConfig(bot_order_requote_dead_band_cents=0.03,
                                 bot_pos_price_max_complete_usd=1.00),
            fetch_market=lambda cid: market,
            fetch_books=lambda host, tok: books[tok],
            decide=lambda *a, **k: ([_intent(price=0.49)], ""),
            open_orders_fn=lambda m: [_open(price=0.51)],
        )
        result = _visit_one(seam, {"cid": "0xabc"}, cycle=1, live=False,
                            plan_fn=spy)

        assert seen["dead_band"] == 0.03
        assert seen["cfg"] is not None
        assert result.status in ("DRY_RUN", "DECLINED")


class TestPairedDepthAttribution:

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
            # #646: the plain path loads through the snapshot reader now.
            return feed_mod.GraduatedSnapshot(
                rows=[], published_mtime_ns=0, condition_ids=frozenset())

        monkeypatch.setattr(feed_mod, "load_graduated_snapshot", spy)

        assert _market_specs() == []
        assert seen["path"] is None


class TestFuriaQuoteClock:
    """#386 full path: FURIA venue times through fetch to decision.

    The kickoff-aware rescue window is GONE (time-of-game purge, #597): the
    FURIA shape (venue `endDate` == kickoff, match in progress) now counts
    down on the venue's own clock like every other market. A market past its
    end date is refused, not rescued by an assumed in-play window.
    """

    def _books(self):
        up = {"token_id": "tok-up", "best_bid": 0.59, "best_ask": 0.61,
              "bids": {0.59: 5000.0}, "asks": {0.61: 5000.0}}
        down = {"token_id": "tok-dn", "best_bid": 0.39, "best_ask": 0.41,
                "bids": {0.39: 5000.0}, "asks": {0.41: 5000.0}}
        return up, down

    def test_a_market_past_its_venue_end_date_is_refused(self):
        # Same shape as the FURIA market (venue endDate is kickoff). Under the
        # kickoff-aware clock this read a positive window and quoted; now the
        # venue countdown is the only clock, so it is refused. The real
        # `decide_quotes` runs here, not a stub: the point is that the countdown
        # the decision actually receives is the market's own.
        import time
        from core_brain.markets import LiveMarket
        from core_brain.quotes import evaluate_market_quote
        kickoff = time.time() - 3600.0
        market = LiveMarket(
            condition_id="0xfuria", market_slug="cs2-furia-aur1-2026-10-06",
            up_token="tok-up", down_token="tok-dn",
            start_ts=kickoff - 7200.0, end_ts=kickoff,
            tick_size=0.01, neg_risk=False)
        assert market.t_remaining() < 0
        up, down = self._books()

        ev = evaluate_market_quote(
            market.condition_id, MakerConfig(), "https://clob.polymarket.com",
            fetch_market=lambda cid: market,
            fetch_books=lambda host, tok: up if tok == "tok-up" else down,
            inventory_for=lambda m: Inventory(),
        )
        assert ev.intents == []
        assert "t_remaining" in ev.why

    def test_a_market_inside_its_venue_window_quotes(self):
        import time
        from core_brain.markets import LiveMarket
        from core_brain.quotes import evaluate_market_quote
        now = time.time()
        market = LiveMarket(
            condition_id="0xfuria", market_slug="cs2-furia-aur1-2026-10-06",
            up_token="tok-up", down_token="tok-dn",
            start_ts=now - 7200.0, end_ts=now + 3600.0,
            tick_size=0.01, neg_risk=False)
        assert market.t_remaining() > 0
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
        assert len(ev.intents) == 2
        assert ev.why == ""

    def _outside_band_books(self):
        # Mids 0.09 / 0.91 -- outside the CONFIGURED [0.10,0.90] band. These
        # were 0.14 / 0.84, which the old hardcoded [0.15,0.85] decided edges
        # refused but the configured band admits (#582).
        up = {"token_id": "tok-up", "best_bid": 0.08, "best_ask": 0.10,
              "bids": {0.08: 5000.0}, "asks": {0.10: 5000.0}}
        down = {"token_id": "tok-dn", "best_bid": 0.90, "best_ask": 0.92,
                "bids": {0.90: 5000.0}, "asks": {0.92: 5000.0}}
        return up, down

    def test_outside_the_band_is_refused(self):
        # The live-series exemption fell with the time-of-game purge (#597):
        # a book outside the configured band is decided, whatever the game is
        # doing. A settled book is refused HERE now -- the band fires before
        # the settled-book check can, which is the same verdict one gate
        # earlier.
        from core_brain.quotes import decide_quotes
        up, down = self._outside_band_books()
        intents, why = decide_quotes(MakerConfig(), up, down, Inventory(), 600.0)
        assert intents == []
        assert "decided market" in why


class TestClassifyRefusal:
    """#390: the refusal classifier pins every terminal marker, so a reworded
    `quotes.py` reason cannot silently flip a settled market to hold."""
    @pytest.mark.parametrize("why", [
        "UP: mid 0.950 outside [0.10,0.90] -- decided market; "
        "DOWN: mid 0.050 outside [0.10,0.90] -- decided market",
        "UP: hedge token DOWN not tradeable (settled book 0.999/0.001) "
        "-- a fill here could not be closed",
        "t_remaining 0s: market ended",
        "market exited: fills still lost money after widening",
        "unfunded by the allocator -- quoting nothing",
        "hit 25 fills for this market",
    ])
    def test_terminal_markers_cancel(self, why):
        assert _classify_refusal(why) is VisitOutcome.REFUSED_TERMINAL

    @pytest.mark.parametrize("why", [
        "UP: 8.0c from mid > 4.5c quote window; "
        "DOWN: 8.0c from mid > 4.5c quote window",
        "no side quotable",
        "",
    ])
    def test_flicker_reasons_hold(self, why):
        assert _classify_refusal(why) is VisitOutcome.REFUSED_TRANSIENT


class TestRefusedHold:
    """#390: a visited-but-refused market holds resting orders; only a dropped
    market, a terminal refusal, or an expired grace cancels."""

    TRANSIENT_WHY = ("UP: 8.0c from mid > 4.5c quote window; "
                     "DOWN: 8.0c from mid > 4.5c quote window")
    TERMINAL_WHY = ("UP: mid 0.950 outside [0.10,0.90] -- decided market; "
                    "DOWN: mid 0.050 outside [0.10,0.90] -- decided market")

    def _seam(self, decide, calls, books=None):
        def open_orders_fn(m):
            return [_open(token="tok-up", price=0.60, oid="o-up"),
                    _open(token="tok-dn", price=0.40, oid="o-dn")]

        def submit_fn(client, registry, market, intents, cfg):
            calls["submitted"].append([i.token_id for i in intents])
            return len(intents)

        def cancel_fn(client, registry, orders):
            calls["cancelled"].append([o["order_id"] for o in orders])
            return len(orders)

        if books is None:
            books = {"best_bid": 0.59, "best_ask": 0.61,
                     "bids": {0.59: 100}, "asks": {0.61: 100}}

        def fetch_books(host, token):
            book = {"token_id": token}
            book.update(books)
            return book

        return VenueSeam(
            client=object(),
            fetch_market=lambda cid: FakeMarket(cid),
            fetch_books=fetch_books,
            decide=decide,
            submit_fn=submit_fn,
            cancel_fn=cancel_fn,
            open_orders_fn=open_orders_fn,
            reconcile_fn=lambda *a: None,
            sweep_fn=lambda: None,
        )

    def test_transient_refusal_cancels_on_first_visit(self):
        # Interim default (operator directive 2026-10-16, pending #622):
        # the refusal hold is OFF, so a transient refusal cancels on the
        # first visit exactly like a terminal one -- quotes rest until
        # fill, not through refusals.
        calls = {"submitted": [], "cancelled": []}
        seam = self._seam(
            lambda cfg, up, dn, inv, t_rem, wf: ([], self.TRANSIENT_WHY), calls)
        results = run(
            seam, interval=0.0, once=True, live=True,
            markets=[FakeMarket("0xabc")],
            sleep_fn=lambda s: None,
        )
        assert results[0].status == "DECLINED"
        assert calls["cancelled"] == [["o-up", "o-dn"]]
        assert calls["submitted"] == []

    def test_refusal_cancels_on_the_first_cycle(self):
        calls = {"submitted": [], "cancelled": []}
        seam = self._seam(
            lambda cfg, up, dn, inv, t_rem, wf: ([], self.TRANSIENT_WHY), calls)
        results = run(
            seam, interval=0.0, once=True, live=True,
            markets=[FakeMarket("0xabc")],
            sleep_fn=lambda s: None,
        )
        assert results[0].status == "DECLINED"
        assert calls["cancelled"] == [["o-up", "o-dn"]]

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

    def test_first_refusal_cancels_immediately(self):
        calls = {"submitted": [], "cancelled": []}
        decides = []
        sleeps = []

        def decide(cfg, up, dn, inv, t_rem, wf):
            decides.append(1)
            return [], self.TRANSIENT_WHY

        seam = self._seam(decide, calls, books={
            "best_bid": 0.66, "best_ask": 0.68,
            "bids": {0.66: 100}, "asks": {0.68: 100}})

        def sleep_fn(s):
            sleeps.append(s)
            raise KeyboardInterrupt

        run(
            seam, interval=0.0, once=False, live=True,
            markets=[FakeMarket("0xabc")],
            sleep_fn=sleep_fn,
        )
        assert len(decides) == 1
        assert calls["submitted"] == []
        assert calls["cancelled"] == [["o-up", "o-dn"]]

    def test_refusal_cancels_both_legs(self):
        calls = {"submitted": [], "cancelled": []}
        decides = []
        sleeps = []

        def decide(cfg, up, dn, inv, t_rem, wf):
            decides.append(1)
            return [], self.TRANSIENT_WHY

        seam = self._seam(decide, calls)

        def sleep_fn(s):
            sleeps.append(s)
            raise KeyboardInterrupt

        run(
            seam, interval=0.0, once=False, live=True,
            markets=[FakeMarket("0xabc")],
            sleep_fn=sleep_fn,
        )
        assert len(decides) == 1
        assert calls["submitted"] == []
        assert calls["cancelled"] == [["o-up", "o-dn"]]

    def test_first_refusal_cancels_before_any_quote(self):
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
            if len(sleeps) >= 2:
                raise KeyboardInterrupt

        run(
            seam, interval=0.0, once=False, live=True,
            markets=[FakeMarket("0xabc")],
            sleep_fn=sleep_fn,
        )
        assert len(decides) == 2
        assert calls["cancelled"] == [["o-up", "o-dn"]]
        assert calls["submitted"] == []


class TestLifecycleStops:
    """One enumerated stop list (#402 T1): every refusal maps to one code."""

    CASES = [
        ("UP: mid 0.880 outside [0.10,0.90] -- decided market",
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


def _quoting_decide(cfg, up, dn, inv, t_rem, wf):
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
        why = ("UP: mid 0.950 outside [0.10,0.90] -- decided market; "
               "DOWN: mid 0.050 outside [0.10,0.90] -- decided market")
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

    def test_quote_between_stops_rearms_the_row(self, tmp_path):
        why = ("UP: mid 0.950 outside [0.10,0.90] -- decided market")
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


class TestSuspectsAttach:
    """#402 T3: suspect visits feed the sweeper."""

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

    def test_raise_warns_without_cancelling_and_fetch_still_runs(self):
        """A raising UMA port fails open: warn, then continue the visit."""
        from core_brain.trader_loop import _visit_one

        registry = self._registry_with_resting()
        events, discards, warns = [], [], []
        seam = self._seam({"0xuma": "raise"}, registry, events, discards)
        fetched = []
        seam.fetch_market = lambda cid: fetched.append(cid) or FakeMarket(cid)
        seam.decide = lambda *a, **k: ([], "declined")

        def emit_fn(service, cycle, phase, action, **kw):
            warns.append(action)
            if action == "discard":
                discards.append((action, kw))

        seam.emit_fn = emit_fn
        res = _visit_one(seam, {"cid": "0xuma"}, cycle=3, live=True)
        assert res.status != "CANCELLED"
        assert "uma_check_unreachable" in warns
        assert fetched == ["0xuma"]
        assert discards == [] and events == []

    def test_disputed_and_resolved_map_to_their_named_reasons(self):
        from core_brain.trader_loop import _visit_one

        for status_value, reason in (
                ("disputed", "uma_resolution_disputed"),
                ("resolved", "uma_resolution_resolved")):
            registry = self._registry_with_resting()
            events, discards = [], []
            seam = self._seam({"0xuma": status_value}, registry,
                              events, discards)
            res = _visit_one(seam, {"cid": "0xuma"}, cycle=3, live=True)
            assert res.status == "CANCELLED"
            assert res.why == reason
            assert discards[0][1]["reason"] == reason
            assert discards[0][1]["extra"]["uma_status"] == status_value
            assert events[0].reason_code == reason

    def test_unknown_flagged_status_fails_open_without_cancelling(self):
        from core_brain.market_resolution import UmaResolutionStatus
        from core_brain.trader_loop import _visit_one

        registry = self._registry_with_resting()
        events, discards, warns = [], [], []
        seam = self._seam({"0xuma": "clean"}, registry, events, discards)
        seam.fetch_uma_status = lambda cid: UmaResolutionStatus(
            condition_id=cid, status="bogus")
        seam.fetch_market = lambda cid: FakeMarket(cid)
        seam.decide = lambda *a, **k: ([], "declined")

        def emit_fn(service, cycle, phase, action, **kw):
            warns.append(action)
            if action == "discard":
                discards.append((action, kw))

        seam.emit_fn = emit_fn
        res = _visit_one(seam, {"cid": "0xuma"}, cycle=3, live=True)
        assert res.status != "CANCELLED"
        assert "uma_check_unreachable" in warns
        assert discards == [] and events == []


def _seed_lifecycle_pair(
    registry,
    pair_id,
    *,
    up_filled=0.0,
    down_filled=0.0,
    up_price=0.48,
    down_price=0.48,
    order_size=200.0,
):
    from core_brain.order_registry import FillRecord, OrderRecord

    active_orders = []
    for side, token_id, fill_size, price in (
        ("UP", "tok-up", up_filled, up_price),
        ("DOWN", "tok-dn", down_filled, down_price),
    ):
        order_id = f"{pair_id}-{side.lower()}"
        status = (
            "filled" if fill_size >= order_size
            else "partial" if fill_size > 0
            else "open"
        )
        registry.create_order(OrderRecord(
            id=order_id,
            condition_id="0xabc",
            token_id=token_id,
            side="BUY",
            price=price,
            original_size=order_size,
            status=status,
            posted_ts=1,
            last_polled_ts=1,
            order_id=f"venue-{order_id}",
            pair_id=pair_id,
        ))
        if fill_size > 0:
            registry.record_fill(FillRecord(
                trade_id=f"fill-{order_id}",
                order_uuid=order_id,
                size=fill_size,
                price=price,
                venue_ts=1,
                recorded_ts=1,
                run_id="lifecycle-test",
            ))
        if status in ("open", "partial"):
            active_orders.append({
                "id": order_id,
                "order_id": f"venue-{order_id}",
                "condition_id": "0xabc",
                "token_id": token_id,
                "side": "BUY",
                "price": price,
                "status": status,
                "pair_id": pair_id,
            })
    return active_orders


def _lifecycle_visit_seam(tmp_path, pairs, *, up_bid=0.45, decide):
    from core_brain.order_registry import inventory_from_registry
    from core_brain.trader_loop import VenueSeam

    from core_brain.order_registry import OrderRegistry

    registry = OrderRegistry(tmp_path / "lifecycle-visit.db",
                             run_id="lifecycle-test")
    active_orders = []
    for pair_id, pair_options in pairs:
        active_orders.extend(
            _seed_lifecycle_pair(registry, pair_id, **pair_options))
    books = {
        "tok-up": {
            "token_id": "tok-up",
            "best_bid": up_bid,
            "best_ask": up_bid + 0.02,
            "bids": {up_bid: 1000.0},
            "asks": {up_bid + 0.02: 1000.0},
        },
        "tok-dn": {
            "token_id": "tok-dn",
            "best_bid": 0.50,
            "best_ask": 0.52,
            "bids": {0.50: 1000.0},
            "asks": {0.52: 1000.0},
        },
    }
    cfg = MakerConfig(
        bot_pos_objective="spread_capture",
        bot_quote_size_mode="shares",
        bot_quote_size_shares=120,
        bot_quote_size_min_shares=5,
        bot_pos_price_max_pair_cost_usd=0.99,
        bot_risk_exposure_max_naked_usd=100.0,
        bot_quote_spread_static_offset_cents=0.02,
    )
    seam = VenueSeam(
        client=object(),
        registry=registry,
        base_cfg=cfg,
        fetch_market=lambda cid: FakeMarket(cid),
        fetch_books=lambda host, token: books[token],
        decide=decide,
        submit_fn=lambda *args, **kwargs: 0,
        cancel_fn=lambda *args, **kwargs: len(args[2]),
        reconcile_fn=lambda *args, **kwargs: None,
        sweep_fn=lambda: None,
        inventory_fn=lambda market: inventory_from_registry(
            market.condition_id,
            market.up_token,
            market.down_token,
            db_path=registry.db_path,
        ),
        open_orders_fn=lambda market: list(active_orders),
    )
    return seam, registry, active_orders


def _capture_lifecycle_plan(captured):
    def plan_fn(open_orders, intents, price_eps=1e-9, **kwargs):
        result = plan_orders(open_orders, intents, price_eps, **kwargs)
        captured["cancel"] = result[0]
        captured["submit"] = result[1]
        captured["preserve_order_ids"] = kwargs.get("preserve_order_ids", set())
        captured["replace_order_ids"] = kwargs.get("replace_order_ids", set())
        return result
    return plan_fn


class TestSingleLegLifecycleVisit:
    def test_patient_wait_preserves_opposite_order_and_emits_transition_once(
        self, tmp_path
    ):
        seam, registry, _ = _lifecycle_visit_seam(
            tmp_path,
            [("pair-patient", {"up_filled": 100.0})],
            up_bid=0.45,
            decide=lambda *args, **kwargs: ([], "unfunded by the allocator"),
        )
        captured = {}
        events = []
        plan_fn = _capture_lifecycle_plan(captured)

        for cycle in (1, 2):
            result = _visit_one(
                seam,
                {"cid": "0xabc"},
                live=False,
                cycle=cycle,
                emit_fn=lambda **event: events.append(event),
                plan_fn=plan_fn,
            )
            assert result.submitted == 0

        patient_order = next(
            order for order in seam.open_orders_fn(FakeMarket())
            if order["token_id"] == "tok-dn"
        )
        assert patient_order["id"] in captured["preserve_order_ids"]
        assert [row["id"] for row in captured["cancel"]] == [
            "pair-patient-up",
        ]
        assert captured["submit"] == []
        assert registry.get_lifecycle_state("pair-patient").state == "PATIENT_WAIT"
        transitions = [
            event for event in events
            if event.get("action") == "single_leg_transition"
        ]
        assert len(transitions) == 1
        assert transitions[0]["extra"]["pair_id"] == "pair-patient"
        assert transitions[0]["extra"]["state"] == "PATIENT_WAIT"

    def test_escalation_replaces_only_its_pair_order_at_exact_maximum_bid(
        self, tmp_path
    ):
        from core_brain.quotes import decide_quotes

        seam, registry, _ = _lifecycle_visit_seam(
            tmp_path,
            [("pair-escalate", {"up_filled": 100.0})],
            up_bid=0.43,
            decide=decide_quotes,
        )
        captured = {}
        result = _visit_one(
            seam,
            {"cid": "0xabc"},
            live=False,
            emit_fn=lambda **event: None,
            plan_fn=_capture_lifecycle_plan(captured),
        )

        old_hedge_id = "pair-escalate-down"
        assert old_hedge_id in captured["replace_order_ids"]
        assert old_hedge_id in [row["id"] for row in captured["cancel"]]
        assert [intent.price for intent in captured["submit"]] == [pytest.approx(0.51)]
        assert [intent.pair_id for intent in captured["submit"]] == ["pair-escalate"]
        assert [intent.size for intent in captured["submit"]] == [100]
        assert result.intents[0].pair_id == "pair-escalate"
        assert registry.get_lifecycle_state("pair-escalate").state == "ESCALATED_HEDGE"

    def test_escalation_keeps_an_order_already_at_the_maximum_bid(self, tmp_path):
        from core_brain.quotes import decide_quotes

        seam, _registry, _ = _lifecycle_visit_seam(
            tmp_path,
            [("pair-already-escalated", {
                "up_filled": 100.0,
                "down_price": 0.51,
            })],
            up_bid=0.43,
            decide=decide_quotes,
        )
        captured = {}

        _visit_one(
            seam,
            {"cid": "0xabc"},
            live=False,
            emit_fn=lambda **event: None,
            plan_fn=_capture_lifecycle_plan(captured),
        )

        order_id = "pair-already-escalated-down"
        assert order_id in captured["preserve_order_ids"]
        assert [row["id"] for row in captured["cancel"]] == [
            "pair-already-escalated-up",
        ]
        assert captured["submit"] == []

    def test_hard_stop_is_terminal_and_never_submits_a_buy(self, tmp_path):
        # The trigger is entry-relative since #619: the seam's fill price is
        # 0.48, so the 75% emergency floor is 0.12 -- not the retired $0.15
        # static stop this case used to sit under.
        seam, _registry, _ = _lifecycle_visit_seam(
            tmp_path,
            [("pair-stop", {"up_filled": 100.0})],
            up_bid=0.12,
            decide=lambda *args, **kwargs: pytest.fail(
                "hard-stop refusal must skip quote decision"),
        )
        captured = {}

        result = _visit_one(
            seam,
            {"cid": "0xabc"},
            live=False,
            emit_fn=lambda **event: None,
            plan_fn=_capture_lifecycle_plan(captured),
        )

        assert "hard stop" in result.why
        assert captured["submit"] == []
        assert {row["id"] for row in captured["cancel"]} == {
            "pair-stop-up", "pair-stop-down",
        }


    def test_refused_escalation_preserves_the_resting_hedge(self, tmp_path):
        from core_brain.quotes import decide_quotes

        # A held average near the cap leaves no positive tick-aligned hedge
        # bid, so the lifecycle refuses the escalation. It must NOT cancel the
        # resting hedge order -- that would strand a single unmatched buy.
        seam, _registry, _ = _lifecycle_visit_seam(
            tmp_path,
            [("pair-refused", {
                "up_filled": 100.0, "up_price": 0.99,
            })],
            up_bid=0.43,
            decide=decide_quotes,
        )
        captured = {}

        result = _visit_one(
            seam,
            {"cid": "0xabc"},
            live=False,
            emit_fn=lambda **event: None,
            plan_fn=_capture_lifecycle_plan(captured),
        )

        assert "refused" in result.why
        # Both resting orders are preserved; nothing is cancelled or submitted.
        assert {"pair-refused-up", "pair-refused-down"} <= set(
            captured["preserve_order_ids"])
        assert captured["cancel"] == []
        assert captured["submit"] == []
        seam, _registry, _ = _lifecycle_visit_seam(
            tmp_path,
            [
                ("pair-a", {"up_filled": 100.0}),
                ("pair-b", {"up_filled": 100.0}),
            ],
            up_bid=0.43,
            decide=lambda *args, **kwargs: pytest.fail(
                "ambiguous escalations must not reach quote decision"),
        )
        captured = {}

        result = _visit_one(
            seam,
            {"cid": "0xabc"},
            live=False,
            emit_fn=lambda **event: None,
            plan_fn=_capture_lifecycle_plan(captured),
        )

        assert "ambiguous" in result.why.lower()
        assert captured["submit"] == []
        assert {row["id"] for row in captured["cancel"]} == {
            "pair-a-up", "pair-a-down", "pair-b-up", "pair-b-down",
        }

    def test_locked_pair_does_not_receive_a_lifecycle_replacement(self, tmp_path):
        calls = []

        def ordinary_decide(*args, **kwargs):
            calls.append(kwargs)
            return [_intent(side="DOWN", token="tok-dn", price=0.50)], ""

        seam, registry, _ = _lifecycle_visit_seam(
            tmp_path,
            [("pair-locked", {
                "up_filled": 100.0,
                "down_filled": 100.0,
                "down_price": 0.48,
                "order_size": 200.0,
            })],
            decide=ordinary_decide,
        )
        captured = {}

        _visit_one(
            seam,
            {"cid": "0xabc"},
            live=False,
            emit_fn=lambda **event: None,
            plan_fn=_capture_lifecycle_plan(captured),
        )

        assert registry.get_lifecycle_state("pair-locked").state == "PAIR_LOCKED"
        assert calls == [{}]
        assert all(intent.pair_id is None for intent in captured["submit"])

    def test_the_placement_gate_receives_the_registry_lifecycle_state(
        self, tmp_path, monkeypatch
    ):
        """#416 — the hedge bypass only exists if the state survives the trip
        from the quote context to the gate. Nothing else covers that seam: the
        gate's own tests hand `lifecycle_state` in as an argument, so dropping
        or renaming the thread in `_visit_one` would leave every one of them
        green while the escalated hedge silently lost its protection.
        """
        from core_brain import trader_loop
        from core_brain.quotes import decide_quotes

        seen = []
        real = trader_loop._admit_placements

        def spy(to_submit, market, up_book, down_book, flow_fn, cfg,
                lifecycle_state=None):
            seen.append((lifecycle_state,
                         [intent.pair_id for intent in to_submit]))
            return real(to_submit, market, up_book, down_book, flow_fn, cfg,
                        lifecycle_state=lifecycle_state)

        monkeypatch.setattr(trader_loop, "_admit_placements", spy)

        escalated, _registry, _ = _lifecycle_visit_seam(
            tmp_path,
            [("pair-escalate", {"up_filled": 100.0})],
            up_bid=0.43,
            decide=decide_quotes,
        )
        _visit_one(escalated, {"cid": "0xabc"}, live=False,
                   emit_fn=lambda **event: None)

        # The escalated hedge reaches the gate carrying its registry state.
        assert seen == [("ESCALATED_HEDGE", ["pair-escalate"])]

        # A dual-resting market reaches the same gate with no state at all, so
        # nothing outside the two protected states can buy a bypass here.
        dual_dir = tmp_path / "dual"
        dual_dir.mkdir()
        seen.clear()
        dual, _registry, _ = _lifecycle_visit_seam(
            dual_dir,
            [("pair-dual", {})],
            decide=lambda *args, **kwargs: (
                [_intent(side="DOWN", token="tok-dn", price=0.50)], ""),
        )
        _visit_one(dual, {"cid": "0xabc"}, live=False,
                   emit_fn=lambda **event: None)

        assert seen, "the placement gate must be consulted on every visit"
        assert {state for state, _ in seen} == {None}

    def test_hedge_replacement_aborts_cancel_if_no_replacement_survives_admission(
        self, tmp_path, monkeypatch
    ):
        """Hedge preservation (#453): if an escalated hedge replacement is filtered
        out by admission gating, do not cancel the resting hedge order into thin air.
        """
        from core_brain import trader_loop
        from core_brain.quotes import decide_quotes

        # Mock _admit_placements to reject all submissions
        monkeypatch.setattr(
            trader_loop, "_admit_placements",
            lambda to_submit, market, up_book, down_book, flow_fn, cfg, lifecycle_state=None: ([], "filtered_out")
        )

        escalated, _registry, _ = _lifecycle_visit_seam(
            tmp_path,
            [("pair-escalate", {"up_filled": 100.0})],
            up_bid=0.43,
            decide=decide_quotes,
        )
        cancels = []
        escalated.cancel_fn = lambda client, registry, to_cancel: (cancels.extend(to_cancel) or len(to_cancel))

        result = _visit_one(escalated, {"cid": "0xabc"}, live=True, emit_fn=lambda **event: None)

        # The resting hedge order ("pair-escalate-down") must NOT have been cancelled!
        cancelled_ids = [str(o.get("id") or o.get("order_id")) for o in cancels]
        assert "pair-escalate-down" not in cancelled_ids

    def test_hedge_replacement_restores_hedge_if_submit_fails(
        self, tmp_path
    ):
        """Hedge restore (#453): if an escalated hedge is cancelled for replacement
        but submit fails or raises an error, attempt to restore the previous hedge.
        """
        from core_brain.quotes import decide_quotes

        escalated, _registry, _ = _lifecycle_visit_seam(
            tmp_path,
            [("pair-escalate", {"up_filled": 100.0})],
            up_bid=0.43,
            decide=decide_quotes,
        )
        submits = []
        def failing_submit(client, registry, market, intents, cfg):
            submits.append(list(intents))
            if len(submits) == 1:
                # First submit (the replacement) fails
                raise RuntimeError("venue connection dropped")
            # Second submit is the restore attempt
            return len(intents)

        escalated.submit_fn = failing_submit

        result = _visit_one(escalated, {"cid": "0xabc"}, live=True, emit_fn=lambda **event: None)

        # A restore attempt was made with the original hedge order's parameters
        assert len(submits) == 2
        restored_intent = submits[1][0]
        assert restored_intent.token_id == "tok-dn"
        assert restored_intent.pair_id == "pair-escalate"


def test_production_reconcile_passes_fill_observer(monkeypatch):
    """The production reconcile wiring reports fills through the Trader emitter."""
    from core_brain.order_registry import FillRecord, OrderRecord

    seen = {}
    monkeypatch.setattr(
        "core_brain.order_registry.reconcile_orders",
        lambda c, r, **kw: seen.update(kw) or object(),
    )
    emitted = []
    reconcile = _production_reconcile_fn(lambda **kw: emitted.append(kw))
    reconcile(object(), object(), "0xmaker")
    observer = seen.get("on_fill_recorded")
    assert callable(observer)

    fill = FillRecord(trade_id="tr_tl_1", order_uuid="o-tl-1", size=2.0, price=0.47)
    order = OrderRecord(
        id="o-tl-1", order_id="0xvenue_tl_1", condition_id="0xcond_tl",
        token_id="0xtok_tl", side="SELL", price=0.47, original_size=5.0,
        status="open", posted_ts=1723840000000, last_polled_ts=1723840000000,
    )
    observer(fill, order)
    assert len(emitted) == 1
    assert emitted[0]["action"] == "fill_recorded"
    assert emitted[0]["service"] == "decide"
    assert emitted[0]["extra"]["side"] == "SELL"


class TestDecideTelemetry:
    def _seam(self, intents, market=None):
        from core_brain.config import MakerConfig
        from core_brain.trader_loop import VenueSeam
        market = market or FakeMarket("0xdecide1")
        return VenueSeam(
            base_cfg=MakerConfig(),
            fetch_market=lambda cid: market,
            fetch_books=lambda host, token: {
                "token_id": token, "best_bid": 0.47, "best_ask": 0.49,
                "bids": {0.47: 100}, "asks": {0.49: 100},
            },
            decide=lambda *a: (list(intents), ""),
            submit_fn=lambda *a, **k: 0,
            cancel_fn=lambda *a, **k: 0,
            reconcile_fn=lambda *a, **k: None,
            sweep_fn=lambda: None,
        )

    def test_decide_carries_title_and_first_quote(self):
        from core_brain.trader_loop import _visit_one
        emitted = []
        seam = self._seam([_intent(side="UP", price=0.48, size=5),
                           _intent(side="DOWN", token="tok-dn", price=0.49, size=6)])
        _visit_one(seam, {"cid": "0xdecide1"}, cycle=1, live=False,
                   emit_fn=lambda **event: emitted.append(event))
        decision = next(e for e in emitted if e.get("action") == "decide")
        assert decision["extra"]["intent_count"] == 2
        assert decision["extra"]["bot_market_title"]
        assert decision["extra"]["quotes"][0] == {"side": "UP", "price": 0.48, "size": 5.0}
        assert len(decision["extra"]["quotes"]) == 2

    def test_skip_decide_has_title_but_no_quotes(self):
        from core_brain.trader_loop import _visit_one
        emitted = []
        seam = self._seam([])
        _visit_one(seam, {"cid": "0xdecide1"}, cycle=1, live=False,
                   emit_fn=lambda **event: emitted.append(event))
        decision = next(e for e in emitted if e.get("action") == "decide")
        assert decision["extra"]["intent_count"] == 0
        assert decision["extra"]["bot_market_title"]
        assert "quotes" not in decision["extra"]

    def test_quotes_capped_at_four_and_skips_unknown_sides(self):
        from core_brain.trader_loop import _visit_one
        emitted = []
        intents = [_intent(side="UP", price=0.40 + i * 0.01, size=5) for i in range(5)]
        bad = _intent(side="YES", price=0.50, size=5)
        seam = self._seam(intents + [bad])
        _visit_one(seam, {"cid": "0xdecide1"}, cycle=1, live=False,
                   emit_fn=lambda **event: emitted.append(event))
        decision = next(e for e in emitted if e.get("action") == "decide")
        assert decision["extra"]["intent_count"] == 6
        assert len(decision["extra"]["quotes"]) == 4
        assert {q["side"] for q in decision["extra"]["quotes"]} == {"UP"}


class TestQuietTail:
    """Steady-state visits stay off the terminal; actions print distinctly.

    A rotation that rests unchanged orders (or re-reports the same skip
    reason) logged an INFO line every cycle, so the terminal scrolled while
    nothing happened and a real decision was indistinguishable from idle.
    Steady-state lines now log once per change at INFO (opening with a blank
    separator line) and drop to DEBUG while unchanged. Posted/cancelled
    orders always print: an action is news every time.
    """

    def _books(self):
        return {
            "tok-up": {"token_id": "tok-up", "best_bid": 0.50, "best_ask": 0.52,
                       "bids": {0.50: 5000.0}, "asks": {0.52: 5000.0}},
            "tok-dn": {"token_id": "tok-dn", "best_bid": 0.46, "best_ask": 0.48,
                       "bids": {0.46: 5000.0}, "asks": {0.48: 5000.0}},
        }

    def _resting_seam(self):
        market = FakeMarket()
        books = self._books()
        return VenueSeam(
            base_cfg=MakerConfig(bot_pos_price_max_complete_usd=1.00),
            fetch_market=lambda cid: market,
            fetch_books=lambda host, tok: books[tok],
            decide=lambda *a, **k: ([_intent(price=0.49)], ""),
            open_orders_fn=lambda m: [_open(price=0.51)],
        )

    def _skipped_seam(self, box):
        market = FakeMarket()
        books = self._books()
        return VenueSeam(
            base_cfg=MakerConfig(bot_pos_price_max_complete_usd=1.00),
            fetch_market=lambda cid: market,
            fetch_books=lambda host, tok: books[tok],
            decide=lambda *a, **k: ([], box[0]),
            open_orders_fn=lambda m: [],
        )

    def _posting_seam(self, calls):
        market = FakeMarket()
        books = self._books()

        def fake_submit(client, registry, market, intents, cfg):
            calls.append(len(intents))
            return len(intents)

        return VenueSeam(
            base_cfg=MakerConfig(bot_pos_price_max_complete_usd=1.00),
            fetch_market=lambda cid: market,
            fetch_books=lambda host, tok: books[tok],
            decide=lambda *a, **k: ([_intent(price=0.49)], ""),
            open_orders_fn=lambda m: [],
            submit_fn=fake_submit,
            cancel_fn=lambda client, registry, orders: len(orders),
        )

    def test_resting_repeats_go_quiet_after_the_first(self, caplog):
        import logging

        mem = {}
        seam = self._resting_seam()
        with caplog.at_level(logging.DEBUG, logger="main_spread_hunter_loop"):
            _visit_one(seam, {"cid": "0xabc"}, cycle=1, live=True,
                       tail_memory=mem)
            _visit_one(seam, {"cid": "0xabc"}, cycle=2, live=True,
                       tail_memory=mem)

        infos = [r for r in caplog.records
                 if r.levelno == logging.INFO and "RESTING" in r.getMessage()]
        debugs = [r for r in caplog.records
                  if r.levelno == logging.DEBUG and "RESTING" in r.getMessage()]
        assert len(infos) == 1
        assert len(debugs) == 1

    def test_steady_lines_end_with_a_blank_separator(self, caplog):
        import logging

        seam = self._resting_seam()
        with caplog.at_level(logging.INFO, logger="main_spread_hunter_loop"):
            _visit_one(seam, {"cid": "0xabc"}, cycle=1, live=True,
                       tail_memory={})

        rec = next(r for r in caplog.records
                   if "RESTING" in r.getMessage())
        assert rec.getMessage().startswith("[RESTING]")
        assert rec.getMessage().endswith("\n")

    def test_skipped_repeats_go_quiet_but_a_new_reason_prints(self, caplog):
        import logging

        box = ["book too wide"]
        mem = {}
        seam = self._skipped_seam(box)
        with caplog.at_level(logging.DEBUG, logger="main_spread_hunter_loop"):
            _visit_one(seam, {"cid": "0xabc"}, cycle=1, live=True,
                       tail_memory=mem)
            _visit_one(seam, {"cid": "0xabc"}, cycle=2, live=True,
                       tail_memory=mem)
            box[0] = "mid band moved"
            _visit_one(seam, {"cid": "0xabc"}, cycle=3, live=True,
                       tail_memory=mem)

        infos = [r for r in caplog.records
                 if r.levelno == logging.INFO and "SKIPPED" in r.getMessage()]
        assert len(infos) == 2
        assert "book too wide" in infos[0].getMessage()
        assert "mid band moved" in infos[1].getMessage()

    def test_return_to_resting_after_a_skip_prints_again(self, caplog):
        """A state transition is news even when the signature is unchanged.

        Resting, then skipped, then resting with the same order count: the
        second resting visit must print at INFO -- only the same kind twice
        in a row may go quiet.
        """
        import logging

        mem = {}
        resting = self._resting_seam()
        box = ["book too wide"]
        skipped = self._skipped_seam(box)
        with caplog.at_level(logging.DEBUG, logger="main_spread_hunter_loop"):
            _visit_one(resting, {"cid": "0xabc"}, cycle=1, live=True,
                       tail_memory=mem)
            _visit_one(skipped, {"cid": "0xabc"}, cycle=2, live=True,
                       tail_memory=mem)
            _visit_one(resting, {"cid": "0xabc"}, cycle=3, live=True,
                       tail_memory=mem)
            _visit_one(resting, {"cid": "0xabc"}, cycle=4, live=True,
                       tail_memory=mem)

        infos = [r for r in caplog.records
                 if r.levelno == logging.INFO and "RESTING" in r.getMessage()]
        debugs = [r for r in caplog.records
                  if r.levelno == logging.DEBUG and "RESTING" in r.getMessage()]
        assert len(infos) == 2
        assert len(debugs) == 1

    def test_posted_orders_print_every_cycle(self, caplog):
        import logging

        calls = []
        mem = {}
        seam = self._posting_seam(calls)
        with caplog.at_level(logging.DEBUG, logger="main_spread_hunter_loop"):
            _visit_one(seam, {"cid": "0xabc"}, cycle=1, live=True,
                       tail_memory=mem)
            _visit_one(seam, {"cid": "0xabc"}, cycle=2, live=True,
                       tail_memory=mem)

        assert calls == [1, 1]
        infos = [r for r in caplog.records
                 if r.levelno == logging.INFO and "QUOTING" in r.getMessage()]
        assert len(infos) == 2
        assert all(r.getMessage().startswith("[QUOTING]")
                   and r.getMessage().endswith("\n")
                   for r in infos)

class TestHeldFirstRotation:
    """Each rotation visits held markets before flat quoting-only ones (#505).

    Naked first, then balanced, then flat -- stable, deterministic, every
    market exactly once per rotation. The fake clock advances only inside
    `fetch_market`, by a fixed per-visit cost `c`, so a visit's clock minus the
    rotation-start stamp measures pure ordering delay. `markets_fn` stamps the
    rotation start; `sleep_fn` stops the loop after a fixed rotation count.
    Nothing patches `time.time`.
    """

    C = 10.0  # per-visit fake-clock cost

    def _build(self, cids, inventory_script, decide=None, rotations=3):
        """Return (run_loop, visits, starts, submissions).

        `inventory_script(cid, rotation)` -> `Inventory` (may raise).
        `decide(cfg, up, dn, inv, t_rem, wf)` -> (intents, why); default
        declines, so only ordering is exercised.
        """
        state = {"rotation": 0, "clock": 0.0}
        visits = []        # (rotation, cid, clock) in fetch order
        starts = []        # clock at each rotation start (markets_fn)
        submissions = []   # (rotation, cid, token_id, side, price, size)

        markets = [FakeMarket(c, up_token=f"{c}-up", down_token=f"{c}-dn")
                   for c in cids]
        by_cid = {m.condition_id: m for m in markets}

        def markets_fn():
            state["rotation"] += 1
            starts.append(state["clock"])
            return list(markets)

        def fetch_market(cid):
            visits.append((state["rotation"], cid, state["clock"]))
            state["clock"] += self.C
            return by_cid[cid]

        def fetch_books(host, token):
            return {"token_id": token, "best_bid": 0.59, "best_ask": 0.61,
                    "bids": {0.59: 100}, "asks": {0.61: 100}}

        def default_decide(cfg, up, dn, inv, t_rem, wf):
            return [], "declined"

        def submit_fn(client, registry, market, intents, cfg):
            for i in intents:
                submissions.append((
                    state["rotation"], market.condition_id, i.token_id,
                    i.side, i.price, i.size))
            return len(intents)

        def inventory_fn(market):
            return inventory_script(market.condition_id, state["rotation"])

        sleeps = {"n": 0}

        def sleep_stop(s):
            sleeps["n"] += 1
            if sleeps["n"] >= rotations:
                raise KeyboardInterrupt()

        seam = VenueSeam(
            client=object(),
            fetch_market=fetch_market,
            fetch_books=fetch_books,
            decide=decide or default_decide,
            submit_fn=submit_fn,
            cancel_fn=lambda *a, **k: 0,
            reconcile_fn=lambda *a, **k: None,
            sweep_fn=lambda: None,
            inventory_fn=inventory_fn,
        )

        def run_loop(live=False):
            run(seam, interval=0.0, once=False, live=live,
                markets_fn=markets_fn, sleep_fn=sleep_stop)

        return run_loop, visits, starts, submissions

    @staticmethod
    def _visits_by_rotation(visits):
        out = {}
        for rotation, cid, _clock in visits:
            out.setdefault(rotation, []).append(cid)
        return out

    def test_rotation_visits_held_markets_first(self):
        def inv(cid, rotation):
            if cid == "0xn1":
                return Inventory(up_shares=7, down_shares=0)   # naked
            if cid == "0xb1":
                return Inventory(up_shares=5, down_shares=5)   # balanced
            return Inventory()

        run_loop, visits, starts, _ = self._build(
            ["0xf1", "0xf2", "0xb1", "0xf3", "0xn1"], inv, rotations=3)
        run_loop()
        by_rotation = self._visits_by_rotation(visits)
        # Rotation 1 has no snapshot yet: feed order is correct and safe.
        assert by_rotation[1] == ["0xf1", "0xf2", "0xb1", "0xf3", "0xn1"]
        assert by_rotation[2] == ["0xn1", "0xb1", "0xf1", "0xf2", "0xf3"]
        assert by_rotation[3] == ["0xn1", "0xb1", "0xf1", "0xf2", "0xf3"]

    def test_held_visit_delay_does_not_grow_with_flat_markets(self):
        def held_offsets(flat_count):
            def inv(cid, rotation):
                if cid == "0xh1":
                    return Inventory(up_shares=7, down_shares=0)   # naked
                if cid == "0xh2":
                    return Inventory(up_shares=5, down_shares=5)   # balanced
                return Inventory()

            # Held markets sit LAST in feed order, so a feed-order loop makes
            # their delay grow with the number of flat markets.
            cids = [f"0xflat{i}" for i in range(flat_count)] + ["0xh1", "0xh2"]
            run_loop, visits, starts, _ = self._build(cids, inv, rotations=3)
            run_loop()
            offsets = {}
            for rotation in sorted({r for r, _, _ in visits}):
                if rotation < 2:
                    continue
                held_clocks = [clock for r, cid, clock in visits
                               if r == rotation and cid in ("0xh1", "0xh2")]
                offsets[rotation] = [clock - starts[rotation - 1]
                                     for clock in held_clocks]
            return offsets

        few, many = held_offsets(2), held_offsets(20)
        assert few == many                       # flat count must not matter
        for offsets in (few, many):
            for rotation, offs in offsets.items():
                assert len(offs) == 2
                for k, off in enumerate(offs):   # k-th held (from 0) <= k * c
                    assert off <= k * self.C

    def test_newly_held_market_is_promoted_next_rotation(self):
        def inv(cid, rotation):
            # f2 acquires exposure from rotation 2 on.
            if cid == "0xf2" and rotation >= 2:
                return Inventory(up_shares=7, down_shares=0)
            return Inventory()

        run_loop, visits, starts, _ = self._build(
            ["0xf1", "0xf2", "0xf3"], inv, rotations=3)
        run_loop()
        by_rotation = self._visits_by_rotation(visits)
        assert by_rotation[1] == ["0xf1", "0xf2", "0xf3"]
        assert by_rotation[2] == ["0xf1", "0xf2", "0xf3"]  # promoted next rot
        assert by_rotation[3] == ["0xf2", "0xf1", "0xf3"]

    def test_market_that_goes_flat_returns_to_feed_position(self):
        def inv(cid, rotation):
            # n1 is naked only during rotation 1 (e.g. merged away mid-run).
            if cid == "0xn1" and rotation == 1:
                return Inventory(up_shares=7, down_shares=0)
            return Inventory()

        run_loop, visits, starts, _ = self._build(
            ["0xf1", "0xn1", "0xf2"], inv, rotations=3)
        run_loop()
        by_rotation = self._visits_by_rotation(visits)
        assert by_rotation[1] == ["0xf1", "0xn1", "0xf2"]
        assert by_rotation[2] == ["0xn1", "0xf1", "0xf2"]
        assert by_rotation[3] == ["0xf1", "0xn1", "0xf2"]  # back in feed spot

    def test_failed_inventory_read_keeps_last_known_rank(self):
        def inv(cid, rotation):
            if cid == "0xn1":
                if rotation == 2:
                    raise RuntimeError("registry read failed")
                return Inventory(up_shares=7, down_shares=0)
            return Inventory()

        run_loop, visits, starts, _ = self._build(
            ["0xf1", "0xn1", "0xf2"], inv, rotations=3)
        run_loop()
        by_rotation = self._visits_by_rotation(visits)
        # The failed read must not skip the visit nor look like "flat".
        assert by_rotation[2] == ["0xn1", "0xf1", "0xf2"]
        assert by_rotation[3] == ["0xn1", "0xf1", "0xf2"]

    def test_flat_market_submissions_unchanged_by_ordering(self, monkeypatch):
        import core_brain.trader_loop as trader_loop_module

        def inv(cid, rotation):
            if cid == "0xn1":
                return Inventory(up_shares=7, down_shares=0)   # naked
            if cid == "0xb1":
                return Inventory(up_shares=5, down_shares=5)   # balanced
            return Inventory()

        def decide(cfg, up, dn, inv_, t_rem, wf):
            return ([_intent("UP", up["token_id"], 0.60, 5),
                     _intent("DOWN", dn["token_id"], 0.40, 5)], "")

        cids = ["0xf1", "0xb1", "0xf2", "0xn1", "0xf3"]
        flat = {"0xf1", "0xf2", "0xf3"}

        run_a, visits_a, _, subs_a = self._build(cids, inv, decide=decide)
        run_a(live=True)

        run_b, visits_b, _, subs_b = self._build(cids, inv, decide=decide)
        monkeypatch.setattr(
            trader_loop_module, "order_by_exposure",
            lambda specs, snapshot, cid_of: list(specs))
        run_b(live=True)

        # The monkeypatch really ran feed order; the normal run really did not.
        order_a = self._visits_by_rotation(visits_a)
        order_b = self._visits_by_rotation(visits_b)
        assert order_b[2] == cids
        assert order_a[2] != order_b[2]

        def flat_subs(subs):
            out = {}
            for rotation, cid, token, side, price, size in subs:
                if cid in flat:
                    out.setdefault(rotation, set()).add(
                        (cid, token, side, price, size))
            return out

        assert flat_subs(subs_a) == flat_subs(subs_b)
        # Every flat market is still visited exactly once per rotation.
        for rotation, order in order_a.items():
            for cid in flat:
                assert order.count(cid) == 1


class TestRefusalVisitRows:
    """#622 T3: the rate denominator.

    `_note_lifecycle_stop` writes nothing for a HELD refusal -- `_visit_stop`
    returns `None` when the reason is transient -- so under `keep`/`drift` almost
    every refusal left no row at all and the arms had no comparable denominator.
    Without a count of refusal VISITS, "this arm cancelled more" cannot be told
    apart from "this arm had more chances to".
    """

    TRANSIENT_WHY = ("UP: 8.0c from mid > 4.5c quote window; "
                     "DOWN: 8.0c from mid > 4.5c quote window")

    def _seam(self, tmp_path, decide, books=None):
        reg, _db = _t3_registry(tmp_path)
        calls = {"submitted": [], "cancelled": []}
        seam = _t3_seam(reg, {}, decide, calls)
        seam.base_cfg = MakerConfig()
        state = books if books is not None else {
            "best_bid": 0.59, "best_ask": 0.61,
            "bids": {0.59: 100}, "asks": {0.61: 100}}

        def fetch_books(host, token):
            book = {"token_id": token}
            book.update(state)
            return book

        seam.fetch_books = fetch_books
        seam.open_orders_fn = lambda m: [
            {"token_id": "tok-x", "price": 0.60, "order_id": "o-x",
             "id": "o-x", "side": "BUY", "status": "open"}]
        return reg, seam, calls, state

    def _rows(self, reg, code=None):
        rows = [r for r in reg.get_all_market_events()
                if r["kind"] == "refusal_visit"]
        if code is not None:
            rows = [r for r in rows if r["reason_code"] == code]
        return rows

    def _run_cycles(self, seam, cycles):
        idx = [0]

        def sleep_fn(s):
            idx[0] += 1
            if idx[0] >= cycles:
                raise KeyboardInterrupt

        run(seam, interval=0.0, once=False, live=True,
            markets=[_t3_spec("0xabc")], sleep_fn=sleep_fn)
        return idx[0]

    def test_refused_visit_writes_a_row(self, tmp_path):
        reg, seam, _calls, _b = self._seam(
            tmp_path, lambda *a: ([], self.TRANSIENT_WHY))
        self._run_cycles(seam, 1)
        assert len(self._rows(reg, "refused_transient")) == 1

    def test_every_refused_visit_is_counted(self, tmp_path):
        reg, seam, _calls, _b = self._seam(
            tmp_path, lambda *a: ([], self.TRANSIENT_WHY))
        self._run_cycles(seam, 3)
        assert len(self._rows(reg)) == 3
        assert len(self._rows(reg, "refused_transient")) == 3

    def test_a_quoted_visit_is_not_a_refusal(self, tmp_path):
        reg, seam, _calls, _b = self._seam(
            tmp_path, lambda *a: ([_intent(token="tok-x", price=0.60)], ""))
        self._run_cycles(seam, 2)
        assert self._rows(reg) == []

    def test_the_row_carries_default_size_one(self, tmp_path):
        reg, seam, _calls, _b = self._seam(
            tmp_path, lambda *a: ([], self.TRANSIENT_WHY))
        self._run_cycles(seam, 2)
        assert [r["size"] for r in self._rows(reg, "refused_transient")] == [1.0, 1.0]


def test_dropped_market_shield_narrows_to_escalated_hedge_and_cancels_hard_stop():
    """#628: HEDGE SHIELD protects ESCALATED_HEDGE only; HARD_STOP cleans up."""
    from types import SimpleNamespace
    from core_brain.trader_loop import _cancel_dropped_markets

    o_hedge = SimpleNamespace(
        id="o_h", order_id="v_h", condition_id="0xhedge", token_id="tok-up",
        price=0.50, side="BUY", status="open", pair_id="pair-hedge",
    )
    o_stop = SimpleNamespace(
        id="o_s", order_id="v_s", condition_id="0xstopped", token_id="tok-dn",
        price=0.45, side="BUY", status="open", pair_id="pair-stopped",
    )
    cancelled: list = []

    def _cancel_fn(_client, _reg, orders):
        cancelled.extend(orders)
        return len(orders)

    def _lifecycle_state(pair_id):
        if pair_id == "pair-hedge":
            return SimpleNamespace(pair_id="pair-hedge", condition_id="0xhedge",
                                   state="ESCALATED_HEDGE")
        if pair_id == "pair-stopped":
            return SimpleNamespace(pair_id="pair-stopped", condition_id="0xstopped",
                                   state="HARD_STOP")
        return None

    seam = SimpleNamespace(
        client=object(),
        registry=SimpleNamespace(
            get_active_orders=lambda: [o_hedge, o_stop],
            get_lifecycle_state=_lifecycle_state,
        ),
        cancel_fn=_cancel_fn,
        resting_order_ids_fn=None,
        db_path=None,
    )

    results = _cancel_dropped_markets(seam, current_markets=[])

    # ESCALATED_HEDGE stays protected; HARD_STOP is cancelled
    cancelled_ids = {getattr(o, "order_id", None) or o.get("order_id") for o in cancelled}
    assert cancelled_ids == {"v_s"}, "HARD_STOP must be cancelled, ESCALATED_HEDGE kept"

    warned = [r for r in results if r.why == "dropped_market_hedge_protected"]
    assert len(warned) == 1
    assert warned[0].condition_id == "0xhedge"

    cancelled_res = [r for r in results if r.why == "dropped_market_cancelled"]
    assert len(cancelled_res) == 1
    assert cancelled_res[0].condition_id == "0xstopped"


def test_stopped_out_pair_does_not_block_fresh_quote(tmp_path):
    """#628: A pair stopped out and fully exited resets to DUAL_RESTING so fresh quotes are submitted."""
    from core_brain.order_registry import LifecycleStateRecord
    reg, db = _t3_registry(tmp_path, "fresh_quote.db")
    cid = "0xmkt1"

    # Previously stopped-out pair, now reset to DUAL_RESTING
    reg.save_lifecycle_state(
        LifecycleStateRecord(
            pair_id="pair-stopped",
            condition_id=cid,
            state="DUAL_RESTING",
            reason="hard_stop_exited",
            updated_ts_ms=1_000_000,
            evidence_json="{}",
        )
    )

    calls = {"submitted": [], "cancelled": []}
    books_calls = {}

    def decide_fn(cfg, up, dn, inv, t_rem, wf):
        tok_up = up["token_id"]
        tok_dn = dn["token_id"]
        return ([_intent(side="UP", token=tok_up, price=0.48),
                 _intent(side="DOWN", token=tok_dn, price=0.48)], "")

    seam = _t3_seam(reg, books_calls, decide_fn, calls)
    results = run(seam, markets=[_TokMarket(cid)], once=True, live=True,
                  sleep_fn=lambda s: None)

    assert len(calls["submitted"]) == 1
    submitted_cid, submitted_tokens = calls["submitted"][0]
    assert submitted_cid == cid
    assert len(submitted_tokens) == 2


def test_lifecycle_quote_context_passes_canonical_drawdown():
    from unittest.mock import MagicMock, patch
    from core_brain.config import MakerConfig
    from core_brain.quotes import Inventory
    from core_brain.single_leg_lifecycle import LifecycleDecision, LegState
    from core_brain.trader_loop import _lifecycle_quote_context

    seam = MagicMock()
    seam.registry.get_orders_by_pair.return_value = [
        MagicMock(condition_id="0xcond", side="BUY", token_id="tok-up", id="ord-1")
    ]
    seam.registry.get_size_matched.return_value = 10.0
    seam.registry.get_matched_notional.return_value = 5.0
    seam.registry.get_lifecycle_state.return_value = None

    market = MagicMock()
    market.condition_id = "0xcond"
    market.up_token = "tok-up"
    market.down_token = "tok-dn"

    cfg = MakerConfig(bot_risk_circuit_max_drawdown_pct=65.0)
    up_book = {"best_bid": 0.50, "best_ask": 0.52}
    down_book = {"best_bid": 0.46, "best_ask": 0.48}
    inventory = Inventory()
    open_orders = [{"pair_id": "pair-1", "id": "ord-1", "token_id": "tok-up"}]

    mock_decision = LifecycleDecision(
        action="refused",
        state=LegState.DUAL_RESTING,
        reason="test",
        held_token_id="tok-up",
    )

    with patch("core_brain.trader_loop.evaluate_single_leg_lifecycle", return_value=mock_decision) as mock_eval:
        ctx = _lifecycle_quote_context(
            seam, market, cfg, up_book, down_book, inventory, open_orders,
            cycle=1, title="test", emit_fn=MagicMock()
        )
        assert mock_eval.called
        call_kwargs = mock_eval.call_args[1]
        assert call_kwargs["bot_risk_circuit_max_drawdown_pct"] == 65.0
        assert "emergency_drawdown_pct" not in call_kwargs


def test_visit_one_passes_canonical_dead_band():
    import warnings
    from core_brain.config import MakerConfig
    from core_brain.trader_loop import _visit_one, VenueSeam

    market = FakeMarket()
    books = {
        "tok-up": {"token_id": "tok-up", "best_bid": 0.50, "best_ask": 0.52, "bids": {0.50: 100}, "asks": {0.52: 100}},
        "tok-dn": {"token_id": "tok-dn", "best_bid": 0.46, "best_ask": 0.48, "bids": {0.46: 100}, "asks": {0.48: 100}},
    }
    seen = {}

    def spy(open_orders, intents, price_eps=1e-9, **kw):
        seen.update(kw)
        return [], []

    cfg = MakerConfig(bot_order_requote_dead_band_cents=0.05)
    seam = VenueSeam(
        base_cfg=cfg,
        fetch_market=lambda cid: market,
        fetch_books=lambda host, tok: books[tok],
        decide=lambda *a, **k: ([_intent(price=0.49)], ""),
        open_orders_fn=lambda m: [_open(price=0.51)],
    )

    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always")
        _visit_one(seam, {"cid": "0xabc"}, cycle=1, live=False, plan_fn=spy)

    dead_band_warns = [w for w in recorded if "trader_loop.py" in w.filename and "bot_order_requote_dead_band_cents" in str(w.message)]
    assert dead_band_warns == []
    assert seen["dead_band"] == 0.05

class TestRotationSubscribeAndTiming:
    """#650: each rotation subscribes the universe and sleeps less by measured work.

    New ports are inert when absent: an unwired seam visits exactly as before,
    with exactly one additive `rotation_complete` row per rotation.
    """

    def _seam(self, events, subscribe=None, stats=None):
        from core_brain.config import MakerConfig
        return VenueSeam(
            base_cfg=MakerConfig(),
            fetch_market=lambda cid: FakeMarket(cid),
            fetch_books=lambda host, token: {
                "token_id": token, "best_bid": 0.59, "best_ask": 0.61,
                "bids": {0.59: 100}, "asks": {0.61: 100},
            },
            decide=lambda *a: ([], "declined"),
            submit_fn=lambda *a, **k: 0,
            cancel_fn=lambda *a, **k: 0,
            reconcile_fn=lambda *a, **k: None,
            sweep_fn=lambda: None,
            subscribe_fn=subscribe,
            book_stats_fn=stats,
            emit_fn=lambda **event: events.append(event),
        )

    def test_run_sleeps_interval_minus_measured_work(self):
        events = []
        seam = self._seam(events)
        sleeps = []
        ticks = iter([0.0, 0.4])

        def sleep_stop(s):
            sleeps.append(s)
            raise KeyboardInterrupt()

        run(seam, interval=1.0, once=False, live=False,
            markets=[FakeMarket("0xm1")], sleep_fn=sleep_stop,
            clock_fn=lambda: next(ticks, 0.4))
        assert sleeps == [pytest.approx(0.6)]

    def test_run_never_sleeps_negative_when_work_exceeds_interval(self):
        events = []
        seam = self._seam(events)
        sleeps = []
        ticks = iter([0.0, 2.5])

        def sleep_stop(s):
            sleeps.append(s)
            raise KeyboardInterrupt()

        run(seam, interval=1.0, once=False, live=False,
            markets=[FakeMarket("0xm1")], sleep_fn=sleep_stop,
            clock_fn=lambda: next(ticks, 2.5))
        assert sleeps == [0.0]

    def test_run_emits_rotation_complete_with_measured_latency(self):
        events = []
        seam = self._seam(events)
        ticks = iter([100.0, 100.25])
        run(seam, interval=0.0, once=True, live=False,
            markets=[FakeMarket("0xm1")], sleep_fn=lambda s: None,
            clock_fn=lambda: next(ticks, 100.25))
        completions = [e for e in events if e.get("action") == "rotation_complete"]
        assert len(completions) == 1
        assert completions[0]["service"] == "decide"
        assert completions[0]["latency_ms"] == pytest.approx(250.0)
        assert completions[0]["extra"]["markets"] == 1


    def test_run_emits_one_completion_per_rotation_with_cycle_numbers(self):
        events = []
        seam = self._seam(events)
        sleeps = {"n": 0}

        def sleep_stop(s):
            sleeps["n"] += 1
            if sleeps["n"] >= 2:
                raise KeyboardInterrupt()

        run(seam, interval=0.0, once=False, live=False,
            markets=[FakeMarket("0xm1")], sleep_fn=sleep_stop)
        completions = [e for e in events if e.get("action") == "rotation_complete"]
        assert [c["cycle"] for c in completions] == [1, 2]

    def test_run_subscribes_current_universe_each_rotation(self):
        events = []
        subscribed = []
        state = {"n": 0}

        def markets_fn():
            state["n"] += 1
            return ([FakeMarket("0xm1")] if state["n"] == 1
                    else [FakeMarket("0xm1"), FakeMarket("0xm2")])

        seam = self._seam(
            events, subscribe=lambda ms: subscribed.append(
                [m.condition_id for m in ms]))
        sleeps = {"n": 0}

        def sleep_stop(s):
            sleeps["n"] += 1
            if sleeps["n"] >= 2:
                raise KeyboardInterrupt()

        run(seam, interval=0.0, once=False, live=False,
            markets_fn=markets_fn, sleep_fn=sleep_stop)
        assert subscribed == [["0xm1"], ["0xm1", "0xm2"]]


    def test_failed_refresh_keeps_old_universe_and_subscriptions(self):
        events = []
        subscribed = []
        state = {"n": 0}

        def markets_fn():
            state["n"] += 1
            if state["n"] == 2:
                raise RuntimeError("feed down")
            return [FakeMarket("0xm1")]

        seam = self._seam(
            events, subscribe=lambda ms: subscribed.append(
                [m.condition_id for m in ms]))
        sleeps = {"n": 0}

        def sleep_stop(s):
            sleeps["n"] += 1
            if sleeps["n"] >= 2:
                raise KeyboardInterrupt()

        run(seam, interval=0.0, once=False, live=False,
            markets_fn=markets_fn, sleep_fn=sleep_stop)
        assert subscribed == [["0xm1"], ["0xm1"]]

    def test_subscribe_failure_degrades_the_cycle_and_keeps_the_loop(self):
        events = []
        calls = {"n": 0}

        def bad_subscribe(ms):
            calls["n"] += 1
            raise RuntimeError("socket closed")

        seam = self._seam(events, subscribe=bad_subscribe)
        sleeps = {"n": 0}

        def sleep_stop(s):
            sleeps["n"] += 1
            if sleeps["n"] >= 2:
                raise KeyboardInterrupt()

        results = run(seam, interval=0.0, once=False, live=False,
                      markets=[FakeMarket("0xm1")], sleep_fn=sleep_stop)
        assert calls["n"] == 2
        assert results

    def test_unwired_seam_runs_as_before_with_one_additive_completion(self):
        from core_brain.config import MakerConfig
        events = []
        seam = VenueSeam(
            base_cfg=MakerConfig(),
            fetch_market=lambda cid: FakeMarket(cid),
            fetch_books=lambda host, token: {
                "token_id": token, "best_bid": 0.59, "best_ask": 0.61,
                "bids": {0.59: 100}, "asks": {0.61: 100},
            },
            decide=lambda *a: ([], "declined"),
            submit_fn=lambda *a, **k: 0,
            cancel_fn=lambda *a, **k: 0,
            reconcile_fn=lambda *a, **k: None,
            sweep_fn=lambda: None,
            emit_fn=lambda **event: events.append(event),
        )
        results = run(seam, interval=0.0, once=True, live=False,
                      markets=[FakeMarket("0xm1")], sleep_fn=lambda s: None)
        assert [r.status for r in results] == ["DECLINED"]
        completions = [e for e in events if e.get("action") == "rotation_complete"]
        assert len(completions) == 1

    def test_decide_row_carries_book_stats_when_port_wired(self):
        events = []
        seam = self._seam(
            events, stats=lambda: {"book_source": "stream", "book_read_ms": 0.4})
        _visit_one(seam, {"cid": "0xdecide1"}, cycle=1, live=False,
                   emit_fn=lambda **event: events.append(event))
        decision = next(e for e in events if e.get("action") == "decide")
        assert decision["extra"]["book_source"] == "stream"
        assert decision["extra"]["book_read_ms"] == 0.4

    def test_decide_row_has_no_book_keys_without_stats_port(self):
        events = []
        seam = self._seam(events)
        _visit_one(seam, {"cid": "0xdecide1"}, cycle=1, live=False,
                   emit_fn=lambda **event: events.append(event))
        decision = next(e for e in events if e.get("action") == "decide")
        assert "book_source" not in decision["extra"]
        assert "book_read_ms" not in decision["extra"]



class TestFillDrainReactsInLoop:
    """#650: a fill event during the wait is reconciled, visited and guarded.

    The drain runs only in the long loop (never `once`), deduplicates by
    `(trade_id, status)`, and isolates each reaction step so one failure cannot stop
    the loop.
    """

    def _fill(self, trade="t1", cid="0xfill", status="FILLED"):
        from core_brain.user_stream import UserTradeEvent
        return UserTradeEvent(
            trade_id=trade, order_id="o1", condition_id=cid,
            token_id="tok-up", side="BUY", price=0.60, size=5.0,
            status=status, fills=(),
        )

    def _seam(self, events, fills=(), reconcile=None):
        from core_brain.config import MakerConfig
        queue = {"events": list(fills)}

        def fill_events():
            return list(queue["events"])

        seam = VenueSeam(
            base_cfg=MakerConfig(),
            client=object(),
            fetch_market=lambda cid: FakeMarket(cid),
            fetch_books=lambda host, token: {
                "token_id": token, "best_bid": 0.59, "best_ask": 0.61,
                "bids": {0.59: 100}, "asks": {0.61: 100},
            },
            decide=lambda *a: ([], "declined"),
            submit_fn=lambda *a, **k: 0,
            cancel_fn=lambda *a, **k: 0,
            reconcile_fn=lambda *a, **k: None,
            sweep_fn=lambda: None,
            fill_events_fn=fill_events,
            fill_reconcile_fn=reconcile,
            emit_fn=lambda **event: events.append(event),
        )
        return seam, queue

    @staticmethod
    def _fake_visit(order):
        def visit(seam, spec, **kw):
            if kw.get("cycle") == 0:
                order.append("visit")
            cid = getattr(spec, "condition_id", None) or getattr(spec, "cid", "0xfill")
            return LiveFleetResult(status="DECLINED", condition_id=str(cid))
        return visit

    @staticmethod
    def _stop_after(n):
        """Stop the loop after `n` normal sleeps."""
        sleeps = {"n": 0}

        def sleep_stop(s):
            sleeps["n"] += 1
            if sleeps["n"] > n:
                raise KeyboardInterrupt()
        return sleep_stop

    def test_fill_after_wait_triggers_reconcile_then_visit_then_guardian(
            self, monkeypatch):
        events = []
        order = []

        def recording_reconcile():
            order.append("reconcile")

        seam, queue = self._seam(
            events, fills=[self._fill()], reconcile=recording_reconcile)

        monkeypatch.setattr(
            "core_brain.trader_loop._visit_one", self._fake_visit(order))
        monkeypatch.setattr(
            "core_brain.trader_loop._guardian_fill_tick",
            lambda **kw: order.append("guardian") or None)
        run(seam, interval=0.0, once=False, live=False,
            markets=[FakeMarket("0xfill")], sleep_fn=self._stop_after(1))
        assert order == ["reconcile", "visit", "guardian"]
        received = next(
            e for e in events if e.get("action") == "user_fill_received")
        assert received["service"] == "decide"
        assert received["extra"]["trade_id"] == "t1"
        assert received["extra"]["matched"] is True

    def test_fill_event_is_deduped_by_trade_id(self, monkeypatch):
        events = []
        order = []
        seam, queue = self._seam(
            events, fills=[self._fill(), self._fill()],
            reconcile=lambda: order.append("reconcile"))
        monkeypatch.setattr(
            "core_brain.trader_loop._visit_one", self._fake_visit(order))
        monkeypatch.setattr(
            "core_brain.trader_loop._guardian_fill_tick", lambda **kw: None)
        run(seam, interval=0.0, once=False, live=False,
            markets=[FakeMarket("0xfill")], sleep_fn=self._stop_after(1))
        assert order == ["reconcile", "visit"]

    def test_fill_status_change_reacts_again(self, monkeypatch):
        events = []
        order = []
        seam, queue = self._seam(
            events,
            fills=[self._fill(status="MATCHED"), self._fill(status="CONFIRMED")],
            reconcile=lambda: order.append("reconcile"))
        monkeypatch.setattr(
            "core_brain.trader_loop._visit_one", self._fake_visit(order))
        monkeypatch.setattr(
            "core_brain.trader_loop._guardian_fill_tick", lambda **kw: None)
        run(seam, interval=0.0, once=False, live=False,
            markets=[FakeMarket("0xfill")], sleep_fn=self._stop_after(1))
        assert order == ["reconcile", "visit", "reconcile", "visit"]
        received = [e for e in events
                    if e.get("action") == "user_fill_received"]
        assert len(received) == 2

    def test_failed_fill_does_not_reconcile(self, monkeypatch):
        events = []
        order = []
        seam, queue = self._seam(
            events, fills=[self._fill(status="FAILED")],
            reconcile=lambda: order.append("reconcile"))
        monkeypatch.setattr(
            "core_brain.trader_loop._visit_one", self._fake_visit(order))
        monkeypatch.setattr(
            "core_brain.trader_loop._guardian_fill_tick", lambda **kw: None)
        run(seam, interval=0.0, once=False, live=False,
            markets=[FakeMarket("0xfill")], sleep_fn=self._stop_after(1))
        assert order == []
        received = next(
            e for e in events if e.get("action") == "user_fill_received")
        assert received["extra"]["status"] == "FAILED"

    def test_fill_for_unknown_market_still_reconciles_without_visit(
            self, monkeypatch):
        events = []
        order = []
        seam, queue = self._seam(
            events, fills=[self._fill(cid="0xgone")],
            reconcile=lambda: order.append("reconcile"))
        monkeypatch.setattr(
            "core_brain.trader_loop._visit_one", self._fake_visit(order))
        monkeypatch.setattr(
            "core_brain.trader_loop._guardian_fill_tick", lambda **kw: None)
        run(seam, interval=0.0, once=False, live=False,
            markets=[FakeMarket("0xfill")], sleep_fn=self._stop_after(1))
        assert order == ["reconcile"]
        received = next(
            e for e in events if e.get("action") == "user_fill_received")
        assert received["extra"]["matched"] is False

    def test_once_mode_never_drains_fills(self):
        events = []
        order = []
        seam, queue = self._seam(
            events, fills=[self._fill()],
            reconcile=lambda: order.append("reconcile"))
        run(seam, interval=0.0, once=True, live=False,
            markets=[FakeMarket("0xfill")], sleep_fn=lambda s: None)
        assert order == []
        assert not [e for e in events
                    if e.get("action") == "user_fill_received"]

    def test_fill_reconcile_failure_keeps_the_loop_alive(self, monkeypatch):
        events = []

        def bad_reconcile():
            raise RuntimeError("registry locked")

        seam, queue = self._seam(
            events, fills=[self._fill()], reconcile=bad_reconcile)
        monkeypatch.setattr(
            "core_brain.trader_loop._visit_one",
            lambda seam, spec, **kw: LiveFleetResult(
                status="DECLINED", condition_id="0xfill"))
        monkeypatch.setattr(
            "core_brain.trader_loop._guardian_fill_tick", lambda **kw: None)
        results = run(seam, interval=0.0, once=False, live=False,
                      markets=[FakeMarket("0xfill")],
                      sleep_fn=self._stop_after(2))
        assert results

    def test_resolved_market_fill_is_not_visited(self, monkeypatch):
        events = []
        order = []
        seam, queue = self._seam(
            events, fills=[self._fill()],
            reconcile=lambda: order.append("reconcile"))
        monkeypatch.setattr(
            "core_brain.trader_loop._visit_one", self._fake_visit(order))
        monkeypatch.setattr(
            "core_brain.trader_loop._guardian_fill_tick", lambda **kw: None)
        monkeypatch.setattr(
            "core_brain.trader_loop.resolved_condition_ids",
            lambda registry: {"0xfill"})
        run(seam, interval=0.0, once=False, live=False,
            markets=[FakeMarket("0xfill")], sleep_fn=self._stop_after(1))
        assert order == ["reconcile"]


class TestMainStreamWiring:
    """T3: CLI flags, stream cache reader, subscriptions, user stream, and shutdown."""

    def test_parse_args_ws_books_defaults(self):
        from core_brain.trader_loop import _parse_args
        # Loop mode defaults to ws_books=None, effective stream
        a = _parse_args([])
        assert a.ws_books is None
        assert (a.ws_books if a.ws_books is not None else not a.once) is True

        # Once mode defaults to ws_books=None, effective REST
        a_once = _parse_args(["--once"])
        assert a_once.ws_books is None
        assert (a_once.ws_books if a_once.ws_books is not None else not a_once.once) is False

        # Explicit overrides
        a_ws = _parse_args(["--ws-books"])
        assert a_ws.ws_books is True
        assert (a_ws.ws_books if a_ws.ws_books is not None else not a_ws.once) is True

        a_once_ws = _parse_args(["--once", "--ws-books"])
        assert a_once_ws.ws_books is True
        assert (a_once_ws.ws_books if a_once_ws.ws_books is not None else not a_once_ws.once) is True

        a_no_ws = _parse_args(["--no-ws-books"])
        assert a_no_ws.ws_books is False
        assert (a_no_ws.ws_books if a_no_ws.ws_books is not None else not a_no_ws.once) is False

    def test_stream_reader_returns_cache_shape_and_loads_stats(self):
        from core_brain.book_provider import BookProvider
        from core_brain.market_stream import BookNotReady
        stats_box = {}

        class FakeStream:
            def fetch_books(self, clob_host, token_id):
                return {
                    "token_id": token_id,
                    "bids": {0.49: 100},
                    "asks": {0.51: 100},
                    "best_bid": 0.49,
                    "best_ask": 0.51,
                }

        provider = BookProvider(stream=FakeStream(), rest_reader=None,
                                stats_box=stats_box)
        res = provider.fetch_books("https://clob", "tok-1")
        assert res["token_id"] == "tok-1"
        assert res["best_bid"] == 0.49
        assert stats_box["book_source"] == "stream"
        assert stats_box["token_id"] == "tok-1"
        assert stats_box["book_read_ms"] >= 0.0

    def test_cold_stream_falls_back_to_rest_and_marks_source(self):
        from core_brain.book_provider import BookProvider
        from core_brain.market_stream import BookNotReady

        class ColdStream:
            def fetch_books(self, clob_host, token_id):
                raise BookNotReady(f"token {token_id} has no live book")

        rest_calls = []

        def fake_rest(clob_host, token_id):
            rest_calls.append((clob_host, token_id))
            return {"token_id": token_id, "bids": {0.4: 1},
                    "asks": {0.6: 1}, "best_bid": 0.4, "best_ask": 0.6,
                    "malformed": 0}

        stats_box = {}
        provider = BookProvider(stream=ColdStream(), rest_reader=fake_rest,
                                stats_box=stats_box)
        res = provider.fetch_books("https://clob", "tok-cold")
        assert res["best_bid"] == 0.4
        assert rest_calls == [("https://clob", "tok-cold")]
        assert stats_box["book_source"] == "rest"

    def test_subscribe_dedupes_and_retains(self):
        from core_brain.trader_loop import _make_subscribe_fn

        ensured = []
        retained = []
        fetched = []

        class FakeStream:
            def ensure_market(self, cid, slug, up_token, down_token):
                ensured.append((cid, slug, up_token, down_token))

            def retain_markets(self, cids):
                retained.append(list(cids))

        class FakeCache:
            def ready_tokens(self):
                return {"tok-up-1", "tok-dn-1", "tok-up-2", "tok-dn-2"}

        def fake_fetch_market(cid):
            fetched.append(cid)
            m = FakeMarket(cid, f"tok-up-{cid}", f"tok-dn-{cid}")
            m.market_slug = f"slug-{cid}"
            return m

        sub = _make_subscribe_fn(FakeStream(), FakeCache(), fake_fetch_market)

        universe = [FakeMarket("1"), FakeMarket("2")]
        sub(universe)
        assert fetched == ["1", "2"]
        assert len(ensured) == 2
        assert retained == [["1", "2"]]

        # Second rotation of the same universe: no new fetch, no new ensure, but retain is called
        sub(universe)
        assert fetched == ["1", "2"]
        assert len(ensured) == 2
        assert retained == [["1", "2"], ["1", "2"]]

    def test_subscribe_skips_market_whose_fetch_raised(self):
        from core_brain.trader_loop import _make_subscribe_fn

        ensured = []
        fetched = []

        class FakeStream:
            def ensure_market(self, cid, slug, up_token, down_token):
                ensured.append(cid)

            def retain_markets(self, cids):
                pass

        class FakeCache:
            def ready_tokens(self):
                return {"tok-up-good", "tok-dn-good"}

        def fake_fetch_market(cid):
            fetched.append(cid)
            if cid == "bad":
                raise RuntimeError("503 venue down")
            m = FakeMarket(cid, "tok-up-good", "tok-dn-good")
            m.market_slug = "good"
            return m

        sub = _make_subscribe_fn(FakeStream(), FakeCache(), fake_fetch_market)
        sub([FakeMarket("bad"), FakeMarket("good")])
        assert ensured == ["good"]
        assert fetched == ["bad", "good"]

        # Next rotation: bad is retried because it was not added to subscribed set
        sub([FakeMarket("bad"), FakeMarket("good")])
        assert fetched == ["bad", "good", "bad"]

    def test_user_stream_missing_creds_leave_loop_unchanged(self, monkeypatch):
        from core_brain.trader_loop import _setup_user_stream
        from core_brain.user_stream import UserStreamCredentialsMissing

        def raise_creds():
            raise UserStreamCredentialsMissing("missing POLY_API_KEY")

        monkeypatch.setattr("core_brain.user_stream.get_user_stream_creds", raise_creds)
        client, fill_events, fill_reconcile, wake = _setup_user_stream(
            decide_emit=lambda **kw: None,
            client=object(),
            registry=object(),
            maker="0xmaker",
        )
        assert client is None
        assert fill_events is None
        assert fill_reconcile is None

    def test_sleep_fn_wakes_on_event(self):
        import threading
        import time
        from core_brain.trader_loop import _make_sleep_fn

        wake = threading.Event()
        sleep_fn = _make_sleep_fn(wake)

        # Pre-set event causes sleep to return immediately
        wake.set()
        t0 = time.monotonic()
        sleep_fn(5.0)
        elapsed = time.monotonic() - t0
        assert elapsed < 0.5
        assert not wake.is_set()

    def test_main_finally_stops_market_and_user_streams(self, monkeypatch, tmp_path):
        import threading
        from core_brain.trader_loop import main
        from core_brain.order_registry import InstanceInUse

        market_stopped = []
        user_stopped = []

        class FakeMarketStream:
            def start(self):
                pass
            def stop(self):
                market_stopped.append(True)

        class FakeUserStream:
            def on_trade(self, cb):
                pass
            def on_state(self, cb):
                pass
            def start(self, cids):
                pass
            def stop(self):
                user_stopped.append(True)

        monkeypatch.setattr("core_brain.market_stream.MarketStream", lambda **kw: FakeMarketStream())
        monkeypatch.setattr(
            "core_brain.trader_loop._setup_user_stream",
            lambda **kw: (FakeUserStream(), lambda: [], lambda: None, threading.Event()),
        )
        # Isolate main() from the venue: no .env read, no balance fetch, no
        # background resolution sweeper thread.
        monkeypatch.setattr(
            "core_brain.trader_loop.start_resolution_sweeper",
            lambda *a, **k: None)
        monkeypatch.setattr(
            "core_brain.account.fetch_live_balance", lambda *a, **k: None)
        monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
        for var in ("POLY_PRIVATE_KEY", "POLY_KEY"):
            monkeypatch.delenv(var, raising=False)

        def fake_run(*a, **kw):
            raise InstanceInUse("fleet already active")

        monkeypatch.setattr("core_brain.trader_loop.run", fake_run)
        monkeypatch.setattr("core_brain.trader_loop._market_specs", lambda *a, **k: [FakeMarket("1")])

        db_file = tmp_path / "test_main.db"
        # Running main should return 2 on InstanceInUse, and both streams should be stopped
        ret = main(["--ws-books", "--no-live", "--db", str(db_file)])
        assert ret == 2
        assert market_stopped == [True]
        assert user_stopped == [True]


class TestGuardianReactiveWake:
    """T3 (#699): Guardian reactive wake, burst coalescing, retention, and end-to-end stream wake."""

    @staticmethod
    def _await(predicate, timeout=3.0):
        import time
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return predicate()

    def test_wake_tick_fires_without_waiting_for_timer(self, monkeypatch):
        import threading
        from unittest.mock import MagicMock
        from core_brain.trader_loop import start_risk_guardian
        from core_brain.quotes import Inventory

        registry = MagicMock()
        registry.get_all_quotes.return_value = [
            {"condition_id": "0x1", "side": "UP", "token_id": "tok-u", "ts": 1.0},
            {"condition_id": "0x1", "side": "DOWN", "token_id": "tok-d", "ts": 1.0},
        ]
        monkeypatch.setattr(
            "core_brain.risk_guardian.inventory_from_registry",
            lambda **kw: Inventory(up_shares=10.0, down_shares=0.0, up_cost=5.0),
        )

        saver = MagicMock(return_value=[{"action": "patient_wait"}])
        stop_evt = threading.Event()
        wake_evt = threading.Event()

        thread = start_risk_guardian(
            client=MagicMock(),
            registry=registry,
            cfg=MagicMock(),
            interval=3600.0,
            live=False,
            single_buy_saver_fn=saver,
            stop_event=stop_evt,
            wake_event=wake_evt,
        )
        try:
            # First initial tick runs on thread start
            assert self._await(lambda: saver.call_count >= 1)
            # Trigger wake during the 3600s sleep
            wake_evt.set()
            assert self._await(lambda: saver.call_count >= 2)
        finally:
            stop_evt.set()
            wake_evt.set()
            thread.join(timeout=2.0)

    def test_burst_wakes_coalesce_while_saver_in_flight(self, monkeypatch):
        import threading
        import time
        from unittest.mock import MagicMock
        from core_brain.trader_loop import start_risk_guardian
        from core_brain.quotes import Inventory

        registry = MagicMock()
        registry.get_all_quotes.return_value = [
            {"condition_id": "0x1", "side": "UP", "token_id": "tok-u", "ts": 1.0},
            {"condition_id": "0x1", "side": "DOWN", "token_id": "tok-d", "ts": 1.0},
        ]
        monkeypatch.setattr(
            "core_brain.risk_guardian.inventory_from_registry",
            lambda **kw: Inventory(up_shares=10.0, down_shares=0.0, up_cost=5.0),
        )

        entered = threading.Event()
        gate = threading.Event()
        calls = []

        def gated_saver(client, reg, cfg, live=False):
            calls.append(1)
            entered.set()
            gate.wait(2.0)
            return []

        stop_evt = threading.Event()
        wake_evt = threading.Event()
        thread = start_risk_guardian(
            client=MagicMock(),
            registry=registry,
            cfg=MagicMock(),
            interval=3600.0,
            live=False,
            single_buy_saver_fn=gated_saver,
            stop_event=stop_evt,
            wake_event=wake_evt,
        )
        try:
            assert entered.wait(2.0)
            for _ in range(20):
                wake_evt.set()
            gate.set()
            # The burst must produce exactly one extra tick.
            assert self._await(lambda: len(calls) >= 2)
            assert len(calls) == 2
        finally:
            stop_evt.set()
            wake_evt.set()
            gate.set()
            thread.join(timeout=2.0)

    def test_shutdown_stops_and_joins_within_timeout(self):
        import threading
        import time
        from unittest.mock import MagicMock
        from core_brain.trader_loop import start_risk_guardian

        registry = MagicMock()
        registry.get_all_quotes.return_value = []
        stop_evt = threading.Event()
        wake_evt = threading.Event()

        thread = start_risk_guardian(
            client=MagicMock(),
            registry=registry,
            cfg=MagicMock(),
            interval=3600.0,
            live=False,
            stop_event=stop_evt,
            wake_event=wake_evt,
        )
        time.sleep(0.05)
        stop_evt.set()
        wake_evt.set()
        thread.join(timeout=2.0)
        assert not thread.is_alive()

    def test_run_reconcile_interrupt_Still_stops_and_joins_guardian(self, monkeypatch):
        import threading
        from unittest.mock import MagicMock
        from core_brain.trader_loop import run

        joined = []

        class FakeGuardianThread:
            def is_alive(self):
                return True
            def join(self, timeout=None):
                joined.append(timeout)

        monkeypatch.setattr("core_brain.trader_loop.start_settlement_sweeper", lambda **kw: None)
        monkeypatch.setattr(
            "core_brain.trader_loop.start_risk_guardian",
            lambda **kw: FakeGuardianThread(),
        )

        seam = VenueSeam(
            base_cfg=MakerConfig(),
            client=object(),
            registry=MagicMock(),
            fetch_market=lambda cid: FakeMarket(cid),
            fetch_books=lambda host, token: {
                "token_id": token, "best_bid": 0.59, "best_ask": 0.61,
                "bids": {0.59: 100}, "asks": {0.61: 100},
            },
            decide=lambda *a: ([], "declined"),
            submit_fn=lambda *a, **k: 0,
            cancel_fn=lambda *a, **k: 0,
            reconcile_fn=lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()),
            sweep_fn=lambda: None,
        )
        guardian_wake = threading.Event()

        with pytest.raises(KeyboardInterrupt):
            run(
                seam,
                interval=1.0,
                once=False,
                live=False,
                markets=[FakeMarket("1")],
                spawn_guardian=True,
                guardian_wake=guardian_wake,
            )

        assert guardian_wake.is_set()
        assert joined == [2.0]

    def test_disabled_modes_leave_board_and_price_listener_none(self, monkeypatch, tmp_path):
        import threading
        from unittest.mock import MagicMock
        from core_brain.trader_loop import main

        captured_stream_kwargs = []
        captured_run_kwargs = []

        class FakeMarketStream:
            def __init__(self, **kw):
                captured_stream_kwargs.append(kw)
            def start(self):
                pass
            def stop(self):
                pass

        monkeypatch.setattr("core_brain.market_stream.MarketStream", FakeMarketStream)
        monkeypatch.setattr(
            "core_brain.trader_loop._setup_user_stream",
            lambda **kw: (None, None, None, threading.Event()),
        )
        monkeypatch.setattr("core_brain.trader_loop.start_resolution_sweeper", lambda *a, **k: None)
        monkeypatch.setattr("core_brain.account.fetch_live_balance", lambda *a, **k: None)
        monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
        for var in ("POLY_PRIVATE_KEY", "POLY_KEY"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setattr("core_brain.trader_loop._market_specs", lambda *a, **k: [FakeMarket("1")])

        def fake_run(*a, **kw):
            captured_run_kwargs.append(kw)
            return [MagicMock()]

        monkeypatch.setattr("core_brain.trader_loop.run", fake_run)

        db_file = tmp_path / "disabled_modes.db"
        # 1. --once with --ws-books -> no trigger_board, price_listener is None
        main(["--once", "--ws-books", "--no-live", "--db", str(db_file)])
        assert captured_stream_kwargs[-1].get("price_listener") is None
        assert captured_run_kwargs[-1].get("trigger_board") is None

        # 2. --no-ws-books -> no MarketStream created, trigger_board is None
        captured_stream_kwargs.clear()
        main(["--no-ws-books", "--no-live", "--db", str(db_file)])
        assert captured_stream_kwargs == []
        assert captured_run_kwargs[-1].get("trigger_board") is None

    def test_subscribe_retains_union_of_universe_and_board_conditions(self):
        import threading
        from core_brain.trader_loop import _make_subscribe_fn
        from core_brain.orphan_trigger import OrphanTriggerBoard, Watch

        retained = []

        class FakeStream:
            def ensure_market(self, cid, slug, up_token, down_token):
                pass
            def retain_markets(self, cids):
                retained.append(set(cids))

        class FakeCache:
            def ready_tokens(self):
                return {"tok-up-1", "tok-dn-1"}

        def fake_fetch_market(cid):
            m = FakeMarket(cid, f"tok-up-{cid}", f"tok-dn-{cid}")
            m.market_slug = f"slug-{cid}"
            return m

        board = OrphanTriggerBoard(threading.Event())
        board.publish(
            {
                "tok-orphan": Watch(
                    condition_id="0xorphan",
                    kind="stop",
                    level=0.45,
                    source="strategy",
                    token_id="tok-orphan",
                )
            },
            generation=board.next_generation(),
        )

        sub = _make_subscribe_fn(FakeStream(), FakeCache(), fake_fetch_market, trigger_board=board)
        sub([FakeMarket("1")])
        assert retained == [{"1", "0xorphan"}]

    def test_end_to_end_stream_frame_wakes_guardian_and_emits_ring_event(
        self, monkeypatch, tmp_path
    ):
        import json
        import sys
        import threading
        import time
        import types
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from core_brain import market_stream as ms
        from core_brain.market_stream import MarketStream
        from core_brain.orphan_trigger import OrphanTriggerBoard
        from core_brain.trader_loop import start_risk_guardian
        from core_brain.quotes import Inventory

        apps = []

        class StubApp:
            def __init__(self, url, on_open=None, on_message=None, on_close=None, on_error=None):
                self.on_open = on_open
                self.on_message = on_message
                self.on_close = on_close
                self.closed = False
                self._park = threading.Event()
                apps.append(self)

            def run_forever(self, **kwargs):
                if self.on_open:
                    self.on_open(self)
                self._park.wait(10.0)

            def send(self, msg):
                pass

            def close(self):
                self._park.set()
                self.closed = True
                if self.on_close:
                    self.on_close(self, 1000, "closed")

            def feed(self, msg):
                if self.on_message:
                    self.on_message(self, msg)

        monkeypatch.setitem(sys.modules, "websocket", types.SimpleNamespace(WebSocketApp=StubApp))

        registry = MagicMock()
        registry.get_all_quotes.return_value = [
            {"condition_id": "0x1", "side": "UP", "token_id": "tok-u", "ts": 1.0},
            {"condition_id": "0x1", "side": "DOWN", "token_id": "tok-d", "ts": 1.0},
        ]
        inv = Inventory(up_shares=10.0, down_shares=0.0, up_cost=5.20, down_cost=0.0)
        monkeypatch.setattr(
            "core_brain.risk_guardian.inventory_from_registry",
            lambda **kw: inv,
        )

        cfg = SimpleNamespace(
            bot_risk_stop_mode="ABSOLUTE_DROP",
            bot_risk_stop_adverse_drop_cents=0.05,
            bot_risk_stop_max_drawdown_pct=10.0,
            bot_risk_circuit_max_drawdown_pct=75.0,
            bot_pos_price_max_pair_cost_usd=0.99,
            bot_risk_stranded_completion_on=False,
        )

        saver = MagicMock(return_value=[{"action": "exited"}])
        stop_evt = threading.Event()
        wake_evt = threading.Event()
        board = OrphanTriggerBoard(wake_evt)
        ring_file = tmp_path / "cycle_events.jsonl"

        stream = MarketStream(price_listener=board.observe)
        stream.ensure_market("0x1", "slug-1", "tok-u", "tok-d")
        stream.start()

        thread = start_risk_guardian(
            client=MagicMock(),
            registry=registry,
            cfg=cfg,
            interval=3600.0,
            live=False,
            single_buy_saver_fn=saver,
            stop_event=stop_evt,
            wake_event=wake_evt,
            trigger_board=board,
            ring_path=ring_file,
        )
        try:
            # Wait for initial guardian pass to populate the board watches (stop level = 0.47)
            assert self._await(lambda: saver.call_count >= 1 and "tok-u" in board.watches())
            assert self._await(lambda: stream.connected)
            app = apps[0]

            # Initial book snapshot in safe band (best_bid = 0.50 > 0.47)
            app.feed(json.dumps({
                "event_type": "book",
                "asset_id": "tok-u",
                "bids": [{"price": "0.50", "size": "10"}],
                "asks": [{"price": "0.54", "size": "10"}],
            }))
            time.sleep(0.05)
            assert saver.call_count == 1

            # Feed price_change dropping best_bid from 0.50 to 0.46 (<= 0.47 stop level)
            app.feed(json.dumps({
                "event_type": "price_change",
                "price_changes": [
                    {"asset_id": "tok-u", "side": "BUY", "price": "0.50", "size": "0"},
                    {"asset_id": "tok-u", "side": "BUY", "price": "0.46", "size": "10"},
                ],
            }))
            assert self._await(lambda: saver.call_count >= 2)

            lines = [
                json.loads(line)
                for line in ring_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            wake_lines = [r for r in lines if r.get("action") == "guardian_reactive_wake"]
            assert len(wake_lines) == 1
            extra = wake_lines[0]["extra"]
            assert extra["condition_id"] == "0x1"
            assert extra["token_id"] == "tok-u"
            assert extra["kind"] == "stop"
            assert extra["source"] == "strategy"
            assert extra["level"] == 0.47
            assert extra["price"] == 0.46
            assert "latency_ms" in extra
        finally:
            stop_evt.set()
            wake_evt.set()
            thread.join(timeout=2.0)
            stream.stop()


class TestRequalificationGate:
    """No new entry quotes for a reset market until a newer publication (#646)."""

    CID = "0xRequal"

    def _reg(self, tmp_path):
        from core_brain.order_registry import OrderRegistry
        return OrderRegistry(db_path=tmp_path / "t.db")

    def _mark_awaiting(self, reg, cid, reset_ts_ms):
        gen = reg.begin_lifecycle_reset(cid, "test", now_ms=reset_ts_ms)
        assert reg.finish_lifecycle_reset(cid, gen, reset_ts_ms=reset_ts_ms)
        return gen

    def _books(self, token="tok-up"):
        return {"token_id": token, "best_bid": 0.59, "best_ask": 0.61,
                "bids": {0.59: 100}, "asks": {0.61: 100}}

    def _seam(self, reg, submitted):
        def decide(cfg, up, dn, inv, t_rem, wf):
            return [_intent()], ""

        return VenueSeam(
            client=object(),
            registry=reg,
            fetch_market=lambda cid: FakeMarket(cid),
            fetch_books=lambda host, token: self._books(token),
            decide=decide,
            submit_fn=lambda c, r, m, i, cfg: submitted.append(
                [x.side for x in i]) or len(i),
            cancel_fn=lambda c, r, o: 0,
            reconcile_fn=lambda *a, **k: None,
            sweep_fn=lambda: None,
        )

    def _specs(self, *cids, published_ns, cids_in_pub):
        from core_brain.trader_loop import MarketSpecList
        return MarketSpecList(
            [FakeMarket(cid) for cid in cids],
            published_mtime_ns=published_ns,
            condition_ids=frozenset(cids_in_pub))

    def _run_once(self, reg, submitted, specs):
        return run(
            self._seam(reg, submitted), interval=0.0, once=True, live=True,
            markets_fn=lambda: specs, sleep_fn=lambda s: None)

    def test_a_marked_market_gets_no_entry_quote(self, tmp_path):
        """#646: an older publication leaves the block in place."""
        import time
        reg = self._reg(tmp_path)
        reset_ms = int(time.time() * 1000) - 60_000
        self._mark_awaiting(reg, self.CID, reset_ms)
        submitted = []
        specs = self._specs(self.CID, published_ns=reset_ms * 1_000_000 - 1,
                            cids_in_pub={self.CID.lower()})

        results = self._run_once(reg, submitted, specs)

        assert [(r.status, r.why) for r in results] == [
            ("SKIPPED", "awaiting_requalification")]
        assert submitted == []
        assert self.CID.lower() in reg.requalification_marks()

    def test_an_equal_publication_time_does_not_clear(self, tmp_path):
        """#646: strictly newer is the rule; equal keeps the mark."""
        import time
        reg = self._reg(tmp_path)
        reset_ms = int(time.time() * 1000) - 60_000
        self._mark_awaiting(reg, self.CID, reset_ms)
        submitted = []
        specs = self._specs(self.CID, published_ns=reset_ms * 1_000_000,
                            cids_in_pub={self.CID.lower()})

        results = self._run_once(reg, submitted, specs)

        assert results[0].status == "SKIPPED"
        assert self.CID.lower() in reg.requalification_marks()

    def test_a_newer_publication_including_the_market_clears(self, tmp_path):
        """#646: quoting resumes once the filter re-qualifies the market."""
        import time
        reg = self._reg(tmp_path)
        reset_ms = int(time.time() * 1000) - 60_000
        self._mark_awaiting(reg, self.CID, reset_ms)
        submitted = []
        specs = self._specs(self.CID,
                            published_ns=int(time.time() * 1_000_000_000),
                            cids_in_pub={self.CID.lower()})

        results = self._run_once(reg, submitted, specs)

        assert results[0].status == "QUOTED"
        assert submitted == [["UP"]]
        assert reg.requalification_marks() == {}

    def test_a_newer_publication_excluding_the_market_drops_it(self, tmp_path):
        """#646: dropped from trading, but the block stays recorded."""
        import time
        reg = self._reg(tmp_path)
        reset_ms = int(time.time() * 1000) - 60_000
        self._mark_awaiting(reg, self.CID, reset_ms)
        submitted = []
        specs = self._specs("0xother",
                            published_ns=int(time.time() * 1_000_000_000),
                            cids_in_pub={"0xother"})

        results = self._run_once(reg, submitted, specs)

        assert all(r.condition_id != self.CID for r in results)
        mark = reg.requalification_marks()[self.CID.lower()]
        assert mark.status == "awaiting"
        assert mark.excluded_publication_ns is not None

    def test_an_empty_publication_never_readmits(self, tmp_path):
        """#646: keeping the old universe is not re-qualification."""
        import time
        reg = self._reg(tmp_path)
        self._mark_awaiting(reg, self.CID, int(time.time() * 1000) - 60_000)

        results = self._run_once(reg, [], [])

        assert results == []
        assert self.CID.lower() in reg.requalification_marks()

    def test_a_legacy_supplier_never_clears_a_mark(self, tmp_path):
        """#646: a plain list carries no publication time, so no clearing."""
        import time
        reg = self._reg(tmp_path)
        self._mark_awaiting(reg, self.CID, int(time.time() * 1000) - 60_000)
        submitted = []

        results = self._run_once(reg, submitted, [FakeMarket(self.CID)])

        assert [(r.status, r.why) for r in results] == [
            ("SKIPPED", "awaiting_requalification")]
        assert submitted == []
        assert self.CID.lower() in reg.requalification_marks()

    def test_the_block_ignores_case(self, tmp_path):
        """#646: 0xABC marked still blocks a 0xabc quote."""
        import time
        reg = self._reg(tmp_path)
        reset_ms = int(time.time() * 1000) - 60_000
        self._mark_awaiting(reg, "0xABC", reset_ms)
        submitted = []
        specs = self._specs("0xabc", published_ns=reset_ms * 1_000_000 - 1,
                            cids_in_pub={"0xabc"})

        results = self._run_once(reg, submitted, specs)

        assert results[0].status == "SKIPPED"
        assert submitted == []

    def test_a_hedge_intent_is_submitted_while_resetting(self, tmp_path):
        """#646: the final gate keeps lifecycle-context submits."""
        import time
        from core_brain.trader_loop import _enforce_entry_gate
        reg = self._reg(tmp_path)
        reset_ms = int(time.time() * 1000) - 60_000
        gen = reg.begin_lifecycle_reset(self.CID, "test", now_ms=reset_ms)
        assert gen == 1
        seam = self._seam(reg, [])
        hedge = _intent(pair_id="pair-9")
        fresh = _intent()

        kept, blocked = _enforce_entry_gate(
            seam, self.CID, [hedge, fresh], "pair-9")

        assert blocked is True
        assert kept == [hedge]

        kept, _ = _enforce_entry_gate(seam, self.CID, [fresh], None)
        assert kept == []

    def test_a_reset_between_planning_and_submission_drops_entries(
            self, tmp_path):
        """#646: the guarded reread catches a mark the visit skip missed."""
        import time
        reg = self._reg(tmp_path)
        real_marks = reg.requalification_marks
        calls = {"n": 0}
        reset_ms = int(time.time() * 1000) - 60_000

        def _flaky_marks():
            # Reads 1-2 are evaluation plus the visit-loop skip (both miss
            # the mark); read 3 is the guarded final check (catches it).
            calls["n"] += 1
            if calls["n"] <= 2:
                return {}
            return real_marks()

        reg.requalification_marks = _flaky_marks
        self._mark_awaiting(reg, self.CID, reset_ms)
        submitted = []
        specs = self._specs(self.CID, published_ns=reset_ms * 1_000_000 - 1,
                            cids_in_pub={self.CID.lower()})

        results = self._run_once(reg, submitted, specs)

        assert results[0].status == "DECLINED"
        assert results[0].why == "awaiting_requalification"
        assert submitted == []

    def test_market_specs_carries_publication_metadata(self, tmp_path):
        """#646: the production feed path mints metadata-carrying lists."""
        import json
        from core_brain.trader_loop import _market_specs
        path = tmp_path / "markets.json"
        path.write_text(json.dumps([{"cid": "0xMeta"}]), encoding="utf-8")

        specs = _market_specs(path=path)

        assert [s["cid"] for s in specs] == ["0xMeta"]
        assert specs.published_mtime_ns == path.stat().st_mtime_ns
        assert specs.publication_condition_ids == frozenset({"0xmeta"})






def test_book_source_rest_is_the_one_flag_undo():
    """#659: --book-source rest forces REST-only books, no socket."""
    from core_brain.trader_loop import _parse_args

    assert _parse_args([]).book_source == "stream"
    assert _parse_args(["--book-source", "rest"]).book_source == "rest"
    assert _parse_args(["--book-source", "stream"]).book_source == "stream"


def test_guardian_saver_fn_routes_decisions_through_provider(monkeypatch):
    """#659: guardian defense evaluates the held bid from the provider."""
    import core_brain.single_buy_saver as saver_mod
    from core_brain.trader_loop import _make_guardian_saver_fn

    seen_books: list[str] = []

    class FakeProvider:
        def get_levels_book(self, token_id):
            seen_books.append(token_id)
            return {"bids": [{"price": "0.5", "size": "1"}], "asks": []}

    captured: dict = {}

    def fake_manage(client, registry, cfg, live=True, **kwargs):
        captured.update(kwargs)
        decision = kwargs.get("decision_book_fn")
        if decision is not None:
            decision("tok-held")
        return []

    monkeypatch.setattr(saver_mod, "manage_single_leg_positions", fake_manage)
    provider = FakeProvider()
    saver_fn = _make_guardian_saver_fn(provider)
    assert saver_fn(object(), object(), object(), live=False) == []
    decision = captured["decision_book_fn"]
    assert decision.__self__ is provider
    assert decision.__func__ is provider.get_levels_book.__func__
    assert seen_books == ["tok-held"]

    captured.clear()
    plain_fn = _make_guardian_saver_fn(None)
    assert plain_fn(object(), object(), object(), live=False) == []
    assert "decision_book_fn" not in captured


def test_the_guardian_saver_carries_the_funder_it_is_given(monkeypatch):
    """#646 N5: the guardian's saver fn is the one that sizes the exit.

    Without the funder the pass reaches `manage_single_leg_positions` with no
    venue view, and a partial exit's remainder is skipped instead of sold.
    """
    import core_brain.single_buy_saver as sbs
    from core_brain.trader_loop import _make_guardian_saver_fn

    seen: dict = {}

    def fake(client, registry, cfg, **kw):
        seen.update(kw)
        return []

    monkeypatch.setattr(sbs, "manage_single_leg_positions", fake)
    saver = _make_guardian_saver_fn(None, funder="0xmaker")

    saver(object(), object(), object(), live=True)

    assert seen["funder"] == "0xmaker"


def test_the_guardian_saver_omits_the_funder_when_there_is_none(monkeypatch):
    """No wallet is not the same as an empty-string wallet heading to a read."""
    import core_brain.single_buy_saver as sbs
    from core_brain.trader_loop import _make_guardian_saver_fn

    seen: dict = {}

    def fake(client, registry, cfg, **kw):
        seen.update(kw)
        return []

    monkeypatch.setattr(sbs, "manage_single_leg_positions", fake)
    _make_guardian_saver_fn(None, funder=None)(object(), object(), object(),
                                               live=True)

    assert "funder" not in seen
