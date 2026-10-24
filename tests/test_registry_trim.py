"""Read-side trim interface tests (#807).

`core_brain.registry_state.trim_cancelled_orders` is the one place that
shapes the dashboard's dead-order payload: newest N cancelled rows per
market, dead pairs leaned to metadata, totals preserved. The Dashboard
server renders this shape; it never recomputes it.
"""
from __future__ import annotations

from core_brain.registry_state import (
    CANCELLED_ORDERS_PER_MARKET,
    trim_cancelled_orders,
)


def _order(cid: str, status: str, ts: int) -> dict:
    return {"id": f"{cid}-{status}-{ts}", "condition_id": cid,
            "status": status, "last_polled_ts": ts}


def test_cancelled_orders_are_capped_per_market():
    cap = CANCELLED_ORDERS_PER_MARKET
    state = {"orders": [_order("0xaaa", "cancelled", i) for i in range(cap + 50)]
                       + [_order("0xbbb", "cancelled", i) for i in range(cap + 50)]}
    trimmed = trim_cancelled_orders(state)
    assert len(trimmed["orders"]) == cap * 2
    assert trimmed["cancelled_orders_total"] == (cap + 50) * 2


def test_the_newest_cancelled_orders_are_the_ones_kept():
    cap = CANCELLED_ORDERS_PER_MARKET
    state = {"orders": [_order("0xaaa", "cancelled", i) for i in range(cap + 50)]}
    kept = trim_cancelled_orders(state)["orders"]
    newest = set(range(50, cap + 50))
    assert {o["last_polled_ts"] for o in kept} == newest


def test_live_orders_are_never_trimmed():
    cap = CANCELLED_ORDERS_PER_MARKET
    live = [_order("0xaaa", "open", i) for i in range(cap + 50)]
    live += [_order("0xaaa", "filled", i) for i in range(cap + 50)]
    state = {"orders": live + [_order("0xaaa", "cancelled", i) for i in range(3)]}
    trimmed = trim_cancelled_orders(state)
    assert len([o for o in trimmed["orders"] if o["status"] != "cancelled"]) == (cap + 50) * 2


def test_input_snapshot_is_not_mutated():
    cap = CANCELLED_ORDERS_PER_MARKET
    orders = [_order("0xaaa", "cancelled", i) for i in range(cap + 10)]
    state = {"orders": orders}
    trim_cancelled_orders(state)
    assert len(state["orders"]) == cap + 10


def test_dead_pairs_are_leaned_but_live_pairs_kept_whole():
    live_order = _order("0xaaa", "open", 1)
    dead_orders = [_order("0xbbb", "cancelled", i) for i in range(10)]
    state = {
        "pairs": [
            {"pair_id": "p-live", "condition_id": "0xaaa", "market": "M",
             "orders": [live_order], "tokens": ["t"],
             "max_pair_cost_at_post": 0.97, "combined_price": 0.95,
             "combined_price_is_paid": True, "hedge_state": "x"},
            {"pair_id": "p-dead", "condition_id": "0xbbb", "market": "M",
             "orders": dead_orders, "tokens": ["t"],
             "max_pair_cost_at_post": 0.97, "combined_price": 0.95,
             "combined_price_is_paid": True, "hedge_state": "x"},
        ]
    }
    trimmed = trim_cancelled_orders(state)
    by_id = {p["pair_id"]: p for p in trimmed["pairs"]}
    assert by_id["p-live"]["orders"] == [live_order]
    assert by_id["p-dead"]["orders"] == []
    assert by_id["p-dead"]["tokens"] == []
    assert by_id["p-dead"]["max_pair_cost_at_post"] == 0.97


def test_cap_is_advertised_to_the_page():
    trimmed = trim_cancelled_orders({"orders": []})
    assert trimmed["cancelled_orders_per_market_cap"] == CANCELLED_ORDERS_PER_MARKET
