"""A live merge close must attribute each leg's cost by quoted side, not by token-id string.

`_submit_and_log`'s MERGE branch derived UP vs DOWN by substring-matching the
token id ("up"/"yes"/"long" vs "dn"/"down"/"no"/"short"). Production
Polymarket token ids are numeric strings -- digits only, no letters -- so
neither branch ever matches and every live merge falls through to the
positional fallback: first GROUP BY group is called UP. That is token-id
sort order, not side. On any market whose DOWN token sorts first, every
merge's `up_cost_removed` / `dn_cost_removed` are exchanged, and `kpi`
subtracts each from the wrong leg's inventory -- the live-data version of
the even-split corruption `test_shadow_merge_leg_cost` killed in rehearsal.

The quotes table already records (token_id, side) per condition; that map is
the source of truth the merge path must use.
"""

from __future__ import annotations

import time

from core_brain.order_registry import (
    FillRecord,
    OrderRecord,
    OrderRegistry,
    QuoteRecord,
    init_db,
)

COND = "0xmergeattribution"
# Numeric venue-style token ids, DOWN sorts first: the layout that swaps
# under positional attribution.
DN_TOKEN = "1234567890123456789012345678901234567890"
UP_TOKEN = "9876543210987654321098765432109876543210"


def _seed_pair(reg: OrderRegistry, up_price=0.252, dn_price=0.618, shares=6.0):
    """Two filled orders (DN inserted first) plus their quoting sides."""
    now = int(time.time() * 1000)
    # Insertion order matches sort order on purpose: whichever way GROUP BY
    # returns its groups, the old positional fallback calls the first one UP.
    reg.create_order(OrderRecord(
        id="o-dn", condition_id=COND, token_id=DN_TOKEN, side="BUY",
        price=dn_price, original_size=shares, status="filled",
        posted_ts=now, last_polled_ts=now))
    reg.record_fill(FillRecord(trade_id="f-dn", order_uuid="o-dn",
                               size=shares, price=dn_price))
    reg.create_order(OrderRecord(
        id="o-up", condition_id=COND, token_id=UP_TOKEN, side="BUY",
        price=up_price, original_size=shares, status="filled",
        posted_ts=now, last_polled_ts=now))
    reg.record_fill(FillRecord(trade_id="f-up", order_uuid="o-up",
                               size=shares, price=up_price))
    reg.log_quote(QuoteRecord(
        ts=time.time(), condition_id=COND, token_id=DN_TOKEN, side="DOWN",
        price=dn_price, size=shares, run_id="test-run"))
    reg.log_quote(QuoteRecord(
        ts=time.time(), condition_id=COND, token_id=UP_TOKEN, side="UP",
        price=up_price, size=shares, run_id="test-run"))


def _unit_costs(reg: OrderRegistry):
    from core_brain.order_manager import _resolve_merge_unit_costs
    with reg._conn() as conn:
        return _resolve_merge_unit_costs(conn, COND)


class TestLiveMergeSideAttribution:
    def test_up_cost_follows_the_up_fills(self, tmp_path):
        db = tmp_path / "live.db"
        init_db(db)
        reg = OrderRegistry(db_path=db)
        _seed_pair(reg)
        up, dn = _unit_costs(reg)
        assert round(up, 4) == round(0.252, 4)
        assert round(dn, 4) == round(0.618, 4)

    def test_costs_are_not_exchanged(self, tmp_path):
        # The defect itself: UP charged 0.618, DN charged 0.252.
        db = tmp_path / "live.db"
        init_db(db)
        reg = OrderRegistry(db_path=db)
        _seed_pair(reg)
        up, dn = _unit_costs(reg)
        assert up < dn

    def test_pair_cost_still_sums(self, tmp_path):
        db = tmp_path / "live.db"
        init_db(db)
        reg = OrderRegistry(db_path=db)
        _seed_pair(reg)
        up, dn = _unit_costs(reg)
        assert round(up + dn, 6) == round(0.252 + 0.618, 6)


class TestInventedCostWarning:
    def test_empty_fills_book_defaults_loudly(self, tmp_path, capsys):
        # A merge with zero fills books 0.49/0.49 -- an asserted $0.98 pair
        # nobody observed. That must be visible, never silent.
        db = tmp_path / "live.db"
        init_db(db)
        reg = OrderRegistry(db_path=db)
        _seed_pair(reg)
        with reg._conn() as conn:
            conn.execute("DELETE FROM fills")
            conn.commit()
        up, dn = _unit_costs(reg)
        assert (round(up, 2), round(dn, 2)) == (0.49, 0.49)
        out = capsys.readouterr().out
        assert "WARNING" in out
        assert "invented" in out

    def test_partial_fills_warn_on_the_guessed_leg(self, tmp_path, capsys):
        # UP measured, DOWN guessed from the complement: the guess is said.
        db = tmp_path / "live.db"
        init_db(db)
        reg = OrderRegistry(db_path=db)
        _seed_pair(reg)
        with reg._conn() as conn:
            conn.execute("DELETE FROM fills WHERE order_uuid = 'o-dn'")
            conn.commit()
        up, dn = _unit_costs(reg)
        assert round(up, 4) == round(0.252, 4)
        assert round(dn, 4) == round(max(0.01, 0.98 - 0.252), 4)
        assert "WARNING" in capsys.readouterr().out
