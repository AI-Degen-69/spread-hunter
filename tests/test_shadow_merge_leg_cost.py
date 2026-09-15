"""A shadow merge must take each leg's OWN cost out of that leg.

`record_shadow_merges` split a merged pair's cost basis evenly between
`up_cost_removed` and `dn_cost_removed`, on a comment claiming the live merge
does the same. It does not: `core_brain/order_manager.py` writes
`amt * up_unit_cost` and `amt * dn_unit_cost`, the real per-leg figures.

The two columns already exist on `closes`, so the even split was never a
schema limit -- it was a divergence between the rehearsal and live, in the one
number a rehearsal exists to reproduce.

It is not cosmetic. `core_brain/kpi.py` subtracts `up_cost_removed` from the
running UP inventory cost when a merge closes, so on a real spread pair -- where
the legs never cost the same, that is the whole strategy -- every merge leaves
both legs carrying the wrong basis. Observed on shadow-01 close #50: legs cost
$1.512 and $3.708, recorded as $2.61 and $2.61.

`realized_pnl` and `cost_basis` were always right and must stay right; only the
attribution between the legs changes.
"""

from __future__ import annotations

import time

import pytest

from core_brain.order_registry import FillRecord, OrderRegistry, init_db
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
        QuoteIntent(side="UP", token_id="tok-up", price=0.47, size=20,
                    mid=0.5, edge_vs_mid=0.0),
        QuoteIntent(side="DOWN", token_id="tok-dn", price=0.51, size=20,
                    mid=0.5, edge_vs_mid=0.0),
    ]


@pytest.fixture
def registry(tmp_path):
    db = tmp_path / "shadow.db"
    init_db(db)
    return OrderRegistry(db_path=db), db


def _merge_one_uneven_pair(reg, db, up_price=0.252, dn_price=0.618, shares=6.0):
    """Fill a pair at two genuinely different prices, then merge it."""
    from core_brain.shadow_exec import (ensure_shadow_tables,
                                        record_shadow_merges, record_submit)
    ensure_shadow_tables(db)
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}})
    by_token = {o.token_id: o for o in reg.get_active_orders()}
    reg.record_fill(FillRecord(trade_id="f-up", order_uuid=by_token["tok-up"].id,
                               size=shares, price=up_price))
    reg.record_fill(FillRecord(trade_id="f-dn", order_uuid=by_token["tok-dn"].id,
                               size=shares, price=dn_price))
    now = int(time.time() * 1000)
    reg.update_order_status(by_token["tok-up"].id, status="filled", last_polled_ts=now)
    reg.update_order_status(by_token["tok-dn"].id, status="filled", last_polled_ts=now)
    assert len(record_shadow_merges(reg, db)) == 1
    return reg.get_all_closes()[0]


class TestPerLegCostAttribution:
    def test_each_leg_is_charged_what_it_actually_cost(self, registry):
        # The shadow-01 numbers: 6 shares at 0.252 and 0.618.
        reg, db = registry
        close = _merge_one_uneven_pair(reg, db)
        assert round(close["up_cost_removed"], 4) == round(6.0 * 0.252, 4)
        assert round(close["dn_cost_removed"], 4) == round(6.0 * 0.618, 4)

    def test_the_even_split_is_gone(self, registry):
        # The defect itself: half of 5.22 is 2.61 on both legs.
        reg, db = registry
        close = _merge_one_uneven_pair(reg, db)
        assert round(close["up_cost_removed"], 2) != 2.61
        assert close["up_cost_removed"] != close["dn_cost_removed"]

    def test_the_legs_still_sum_to_the_cost_basis(self, registry):
        # Attribution may move cost between legs; it may never create or lose
        # any. `cost_basis` and `realized_pnl` were always right.
        reg, db = registry
        close = _merge_one_uneven_pair(reg, db)
        total = close["up_cost_removed"] + close["dn_cost_removed"]
        assert round(total, 6) == round(close["cost_basis"], 6)
        assert round(close["realized_pnl"], 6) == round(
            close["proceeds"] - close["cost_basis"], 6)

    def test_an_evenly_priced_pair_still_splits_evenly(self, registry):
        # Not a special case -- it falls out of charging each leg its own cost.
        reg, db = registry
        close = _merge_one_uneven_pair(reg, db, up_price=0.45, dn_price=0.45)
        assert round(close["up_cost_removed"], 4) == round(close["dn_cost_removed"], 4)

    def test_an_unlabelled_leg_falls_back_to_the_even_split(self, registry):
        # The quotes ledger is the only UP/DOWN source here. With no side for a
        # token, the even split is what the code recorded before and is the
        # honest stand-in -- guessing which leg is UP would put real money on
        # the wrong one.
        from core_brain.shadow_exec import _leg_cost_removed

        up, dn = _leg_cost_removed(
            [("tok-a", 1.512), ("tok-b", 3.708)], {}, "0xabc")
        assert (round(up, 4), round(dn, 4)) == (2.61, 2.61)

    def test_the_helper_reads_the_side_map(self, registry):
        from core_brain.shadow_exec import _leg_cost_removed

        sides = {("0xabc", "tok-a"): "DOWN", ("0xabc", "tok-b"): "UP"}
        up, dn = _leg_cost_removed(
            [("tok-a", 1.512), ("tok-b", 3.708)], sides, "0xabc")
        assert (round(up, 4), round(dn, 4)) == (3.708, 1.512)


class TestInventoryBasis:
    def test_kpi_removes_the_right_cost_from_each_leg(self, registry):
        # The reason this matters: kpi subtracts `up_cost_removed` from the
        # running UP cost. Under the even split the UP leg was over-charged by
        # 1.098 and the DOWN leg under-charged by the same.
        reg, db = registry
        close = _merge_one_uneven_pair(reg, db)
        up_overcharge = close["up_cost_removed"] - 6.0 * 0.252
        assert round(up_overcharge, 6) == 0.0
