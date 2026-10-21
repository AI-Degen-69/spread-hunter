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


class TestTheLedgerScanIsPaidOnlyWhenSomethingMerges:
    """`get_all_quotes` is `SELECT * FROM quotes` with no index.

    `record_shadow_merges` runs every rotation, right after `shadow_positions`
    already paid that scan once. Most rotations close nothing, and the quotes
    table only grows for the length of the run -- so reading the side map
    before knowing whether any pair is eligible doubles a cost that answers a
    question no pair asked.
    """

    def _counting_registry(self, reg):
        calls = {"n": 0}
        real = reg.get_all_quotes

        def counted():
            calls["n"] += 1
            return real()

        reg.get_all_quotes = counted
        return calls

    def test_nothing_to_merge_reads_no_quotes(self, registry):
        from core_brain.shadow_exec import (ensure_shadow_tables,
                                            record_shadow_merges, record_submit)
        reg, db = registry
        ensure_shadow_tables(db)
        # Orders posted, nothing filled: no pair is mergeable.
        record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                      db_path=db, book_fn=lambda h, t: {"bids": {}})
        calls = self._counting_registry(reg)
        assert record_shadow_merges(reg, db) == []
        assert calls["n"] == 0

    def test_a_merge_reads_the_ledger_once(self, registry):
        from core_brain.shadow_exec import (ensure_shadow_tables,
                                            record_shadow_merges, record_submit)
        reg, db = registry
        ensure_shadow_tables(db)
        record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                      db_path=db, book_fn=lambda h, t: {"bids": {}})
        by_token = {o.token_id: o for o in reg.get_active_orders()}
        reg.record_fill(FillRecord(trade_id="f-up",
                                   order_uuid=by_token["tok-up"].id,
                                   size=6.0, price=0.252))
        reg.record_fill(FillRecord(trade_id="f-dn",
                                   order_uuid=by_token["tok-dn"].id,
                                   size=6.0, price=0.618))
        now = int(time.time() * 1000)
        reg.update_order_status(by_token["tok-up"].id, status="filled",
                                last_polled_ts=now)
        reg.update_order_status(by_token["tok-dn"].id, status="filled",
                                last_polled_ts=now)
        calls = self._counting_registry(reg)
        assert len(record_shadow_merges(reg, db)) == 1
        assert calls["n"] == 1

        # And the attribution still lands: laziness must not cost correctness.
        close = reg.get_all_closes()[0]
        assert round(close["up_cost_removed"], 4) == round(6.0 * 0.252, 4)


def _seed_shadow_pair(reg, db, up_shares=6.0, dn_shares=6.0):
    """One pair resting, filled unevenly or evenly, both legs marked filled."""
    from core_brain.shadow_exec import ensure_shadow_tables, record_submit
    ensure_shadow_tables(db)
    record_submit(object(), reg, FakeMarket(), _intents(), _cfg(),
                  db_path=db, book_fn=lambda h, t: {"bids": {}})
    by_token = {o.token_id: o for o in reg.get_active_orders()}
    reg.record_fill(FillRecord(trade_id="f-up",
                               order_uuid=by_token["tok-up"].id,
                               size=up_shares, price=0.252))
    reg.record_fill(FillRecord(trade_id="f-dn",
                               order_uuid=by_token["tok-dn"].id,
                               size=dn_shares, price=0.618))
    now = int(time.time() * 1000)
    reg.update_order_status(by_token["tok-up"].id, status="filled",
                            last_polled_ts=now)
    reg.update_order_status(by_token["tok-dn"].id, status="filled",
                            last_polled_ts=now)


class TestShadowMergeReset:
    def test_an_equal_merge_resets(self, registry):
        """#646: close plus leg writes committed, flat proof resets."""
        from core_brain.shadow_exec import record_shadow_merges
        reg, db = registry
        _seed_shadow_pair(reg, db)

        assert len(record_shadow_merges(reg, db)) == 1
        assert record_shadow_merges(reg, db) == []

        assert reg.requalification_marks()["0xabc"].status == "awaiting"

    def test_an_unequal_merge_does_not_reset(self, registry):
        """#646: the excess leg stays managed, no mark."""
        from core_brain.shadow_exec import record_shadow_merges
        reg, db = registry
        _seed_shadow_pair(reg, db, up_shares=6.0, dn_shares=3.0)

        assert len(record_shadow_merges(reg, db)) == 1
        assert reg.requalification_marks() == {}

    def test_a_failed_leg_write_does_not_reset(self, registry):
        """#646: the exception propagates, the proof never runs."""
        import sqlite3
        from core_brain.shadow_exec import record_shadow_merges
        reg, db = registry
        _seed_shadow_pair(reg, db)
        con = sqlite3.connect(str(db))
        con.execute(
            "CREATE TRIGGER inject_leg_failure BEFORE INSERT ON "
            "shadow_merge_legs BEGIN SELECT RAISE(ABORT, 'injected'); END")
        con.commit()
        try:
            with pytest.raises(sqlite3.Error):
                record_shadow_merges(reg, db)
        finally:
            con.execute("DROP TRIGGER inject_leg_failure")
            con.commit()
            con.close()

        assert reg.requalification_marks() == {}
