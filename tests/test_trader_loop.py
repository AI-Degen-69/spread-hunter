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

import pytest

from core_brain.trader_loop import (
    LiveFleetResult,
    VenueSeam,
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
        open_orders = [_open(price=0.61)]
        intents = [_intent(price=0.60)]
        to_cancel, to_submit = plan_orders(open_orders, intents)
        assert to_cancel == open_orders
        assert [i.price for i in to_submit] == [0.60]

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
        open_orders = [_open(price=0.60)]
        intents = [_intent(price=0.55)]
        to_cancel, to_submit = plan_orders(open_orders, intents, dead_band=0.03)
        assert to_cancel == open_orders
        assert [i.price for i in to_submit] == [0.55]

    def test_dead_band_is_symmetric(self):
        # Queue position is lost in both directions.
        open_orders = [_open(price=0.58)]
        intents = [_intent(price=0.60)]
        to_cancel, to_submit = plan_orders(open_orders, intents, dead_band=0.03)
        assert to_cancel == []
        assert to_submit == []

    def test_dead_band_defaults_off_so_existing_callers_are_unchanged(self):
        open_orders = [_open(price=0.60)]
        intents = [_intent(price=0.58)]
        to_cancel, to_submit = plan_orders(open_orders, intents)
        assert to_cancel == open_orders

    def test_requoted_leg_carries_the_resting_complement_pair_id(self):
        # Root cause of #206: re-quoting one leg of a market whose complement
        # still rests used to mint a brand-new pair_id, splitting one economic
        # pair into two one-legged pairs (seen live: 0.71 + 0.23 on Sweden
        # landing in pair-9028... and pair-3dab...). plan_orders knows which
        # resting order a submitted intent replaces, so it tags the intent
        # with that order's pair_id for _submit_intents to carry forward.
        open_orders = [
            {"token_id": "tok-up", "price": 0.73, "order_id": "o-up",
             "side": "BUY", "status": "open", "pair_id": "pair-aaa111"},
            {"token_id": "tok-dn", "price": 0.23, "order_id": "o-dn",
             "side": "BUY", "status": "open", "pair_id": "pair-aaa111"},
        ]
        # Both legs are quoted this cycle (the normal decide flow); the DOWN
        # leg still sits at its desired price and is KEPT, the UP leg drifted
        # and is cancelled + resubmitted.
        intents = [
            _intent(side="UP", token="tok-up", price=0.71),
            _intent(side="DOWN", token="tok-dn", price=0.23),
        ]
        to_cancel, to_submit = plan_orders(open_orders, intents)
        assert [o["order_id"] for o in to_cancel] == ["o-up"]
        assert len(to_submit) == 1
        assert to_submit[0].token_id == "tok-up"
        assert to_submit[0].pair_id == "pair-aaa111"

    def test_requote_with_no_resting_complement_carries_no_pair_id(self):
        # A market with nothing else resting starts a fresh pair.
        open_orders = [
            {"token_id": "tok-up", "price": 0.73, "order_id": "o-up",
             "side": "BUY", "status": "open", "pair_id": "pair-aaa111"},
        ]
        intents = [_intent(side="UP", token="tok-up", price=0.71)]
        to_cancel, to_submit = plan_orders(open_orders, intents)
        assert len(to_submit) == 1
        assert to_submit[0].pair_id is None

    def test_fully_requoted_pair_gets_one_fresh_pair_id_not_the_old_one(self):
        # Both legs re-priced in the same cycle: nothing of the old pair
        # survives this cycle's cancels, so the replacement legs must NOT
        # resurrect the old id (a stale id could later collide with fills
        # attributed to the cancelled legs). They land together under one NEW
        # shared id, minted by the submit path.
        # Both legs re-priced in the same cycle land in ONE new pair together.
        open_orders = [
            {"token_id": "tok-up", "price": 0.73, "order_id": "o-up",
             "side": "BUY", "status": "open", "pair_id": "pair-aaa111"},
            {"token_id": "tok-dn", "price": 0.23, "order_id": "o-dn",
             "side": "BUY", "status": "open", "pair_id": "pair-aaa111"},
        ]
        intents = [
            _intent(side="UP", token="tok-up", price=0.71),
            _intent(side="DOWN", token="tok-dn", price=0.25),
        ]
        to_cancel, to_submit = plan_orders(open_orders, intents)
        assert len(to_cancel) == 2
        assert len(to_submit) == 2
        assert all(i.pair_id is None for i in to_submit)

        from unittest.mock import MagicMock
        from core_brain.trader_loop import _submit_intents

        venue = MagicMock()
        venue.get_open_orders.return_value = []
        venue.create_order.return_value = {"signed": True}
        venue.post_orders.return_value = [{"orderID": "0x1"}, {"orderID": "0x2"}]
        registry = MagicMock()

        _submit_intents(venue, registry, FakeMarket("0xmkt"), to_submit, MakerConfig())
        created = [call.args[0] for call in registry.create_order.call_args_list]
        assert len(created) == 2
        pids = {o.pair_id for o in created}
        assert len(pids) == 1
        fresh_pid = pids.pop()
        assert fresh_pid != "pair-aaa111"
        assert fresh_pid.startswith("pair-")

    def test_carried_pair_id_survives_the_queue_hold(self):
        # The queue hold keeps the UP leg at 0.73 while the DOWN leg drifts out of tolerance.
        # The replacement intent for the cancelled DOWN leg must join the resting UP leg's pair.
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
            hedge_asks={"tok-up": 0.20, "tok-dn": 0.70},
            queue_ahead={"o-up": 10.0},
            hold_queue_shares=50.0)
        assert [o["order_id"] for o in to_cancel] == ["o-dn"]
        assert len(to_submit) == 1
        assert to_submit[0].token_id == "tok-dn"
        assert to_submit[0].pair_id == "pair-aaa111"

    def test_the_larger_of_dead_band_and_price_eps_wins(self):
        # Two independent reasons to keep an order; neither may silently
        # disable the other.
        open_orders = [_open(price=0.60)]
        intents = [_intent(price=0.58)]
        to_cancel, _ = plan_orders(open_orders, intents,
                                   price_eps=0.001, dead_band=0.03)
        assert to_cancel == []

    def test_a_kept_order_is_cancelled_when_its_own_price_fails_the_gate(self):
        # The order rests at 0.60. The desired price is 0.58, within the band,
        # so the band would keep it -- but completing at the DOWN ask of 0.42
        # costs 1.02, which is exactly the hole the band would otherwise open.
        cfg = MakerConfig(max_completable_pair_cost=1.00)
        open_orders = [_open(price=0.60)]
        intents = [_intent(price=0.58)]
        to_cancel, to_submit = plan_orders(
            open_orders, intents, dead_band=0.03, cfg=cfg,
            hedge_asks={"tok-up": 0.42})
        assert to_cancel == open_orders
        assert [i.price for i in to_submit] == [0.58]

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
            # Propose new price for UP leg (triggering replace)
            return [_intent(side="UP", token="tok-up", price=0.62)], ""

        def open_orders_fn(m):
            return [{"token_id": "tok-up", "price": 0.58, "order_id": "o-old", "id": "local-old", "side": "BUY", "status": "open"}]

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
            return [{"token_id": "tok-up", "price": 0.58, "order_id": "o-old", "id": "local-old", "side": "BUY", "status": "open"}]

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
        """A seam whose market wants a new UP price while an old quote rests."""
        def decide(cfg, up, dn, inv, t_rem, wf):
            return [_intent(side="UP", token="tok-up", price=0.62)], ""

        def open_orders_fn(m):
            return [{"token_id": "tok-up", "price": 0.58, "order_id": "o-old",
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

    def test_the_re_gate_still_fires_on_a_token_whose_hedge_is_not_held(self):
        # The guard against over-correcting: only the held token stands down.
        cfg = MakerConfig(max_completable_pair_cost=1.00)
        open_orders = [_open("tok-up", 0.54, "o1"), _open("tok-dn", 0.40, "o2")]
        intents = [_intent(side="UP", token="tok-up", price=0.54),
                   _intent(side="DOWN", token="tok-dn", price=0.40)]
        to_cancel, _ = plan_orders(
            open_orders, intents, dead_band=0.03, cfg=cfg,
            hedge_asks={"tok-up": 0.50, "tok-dn": 0.62},
            hedge_held={"tok-up"})
        assert [o["order_id"] for o in to_cancel] == ["o2"]

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

        assert seen["hedge_held"] == {"tok-up"}

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

        assert seen["hedge_held"] == set()
