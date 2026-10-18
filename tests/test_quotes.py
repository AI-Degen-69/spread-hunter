"""The two halves of #627: the entry pair-cost cap and the break-even anchor.

Part 1 (below) is the entry pair-cost cap on the from-mid path.
Part 2 is the read-only break-even anchor in `summarize_state`.

The fleet quotes both legs under mid: `mid - reward_offset` each side, which is
the income -- a pair assembled at ~1.00 - 2*offset merges back into exactly
$1.00. Every gate that protects that arithmetic lives on the ENTRY path (judging
a NEW bid before it is posted); nothing re-checks a resting order (#387, #626).

The gap this file covers: on a flat book with the dynamic offset switched off,
`Bid_UP + Bid_DOWN <= max_pair_cost` was checked nowhere that could see the
FINAL resting prices. `risk.hard_block`'s pair-cost arm needs a held average
(`other_avg > 0`) and a flat book has none; `dynamic_pair_sum` runs only when
`dynamic_offset_enabled` is true; the legacy fresh-market check sits below the
from-mid early return. So a wide mid pair posted both legs and paid $1.00+ for
a $1.00 instrument.
"""
from __future__ import annotations

import pytest

from core_brain.config import MakerConfig
from core_brain.quotes import (
    Inventory,
    decide_quotes,
    pair_cost_over_cap,
)
from core_brain.single_leg_lifecycle import (
    LegState,
    LifecycleQuoteOverride,
    max_profitable_hedge_bid,
)

UP = "tok-up"
DOWN = "tok-dn"


def _book(mid: float, token: str) -> dict:
    """A centred, deep book: mid +/- 1c, 5000 shares a side.

    Tight enough to clear every book-health arm, so a test reaches the entry
    gate instead of failing earlier for an unrelated reason.
    """
    bid, ask = round(mid - 0.01, 4), round(mid + 0.01, 4)
    return {"token_id": token, "best_bid": bid, "best_ask": ask,
            "bids": {bid: 5000.0}, "asks": {ask: 5000.0}}


def _cfg(**kw) -> MakerConfig:
    base = dict(objective="rewards", size_mode="shares", quote_shares=120,
                min_quote_shares=50, reward_offset=0.02,
                price_band_low=0.10, price_band_high=0.90,
                max_pair_cost=0.99, max_completable_pair_cost=1.00)
    base.update(kw)
    return MakerConfig(**base)


# UP mid 0.30 / DOWN mid 0.78 rest at 0.27 and 0.75: a $1.02 pair.
_WIDE_UP, _WIDE_DOWN = _book(0.30, UP), _book(0.78, DOWN)
# UP mid 0.32 / DOWN mid 0.73 rest at 0.29 and 0.70: a $0.99 pair, at the cap.
_EDGE_UP, _EDGE_DOWN = _book(0.32, UP), _book(0.73, DOWN)


def _flat_cfg(**kw) -> MakerConfig:
    """Dynamic offset OFF: the preset where the flat-book pair was ungated.

    With `dynamic_offset_enabled=True` (the shipped default) `dynamic_pair_sum`
    already refuses any pair that rounds to $1.00 or more, so the new entry cap
    is the binding one on the dynamic-off preset (`HUNTER_DYNAMIC_OFFSET=false`)
    and in the band the completable arm cannot see.
    """
    return _cfg(dynamic_offset_enabled=False, **kw)


class TestEntryPairCostCap:
    def test_over_cap_from_mid_pair_is_refused_at_entry(self):
        # Arrange -- first look at the prices the gate will judge, with the cap
        # moved out of the way so both legs actually post.
        loose = _flat_cfg(enforce_completable_pair_cost=False, max_pair_cost=1.10)
        intents, why = decide_quotes(loose, _WIDE_UP, _WIDE_DOWN, Inventory(),
                                     1e9, None)
        assert [i.price for i in intents] == [0.27, 0.75]
        assert round(sum(i.price for i in intents), 4) == 1.02
        assert not why

        # Act -- the shipped cap.
        cfg = _flat_cfg(enforce_completable_pair_cost=False)
        intents, why = decide_quotes(cfg, _WIDE_UP, _WIDE_DOWN, Inventory(),
                                     1e9, None)

        # Assert
        assert intents == []
        assert why.startswith("entry_pair_cost:")
        assert "1.0200" in why
        assert "$0.990 cap" in why

    def test_a_pair_exactly_at_the_cap_is_quoted(self):
        # The issue states the rule as `<= max_pair_cost`, so the tie is
        # allowed: 0.29 + 0.70 = $0.99 on a $0.99 cap posts both legs.
        cfg = _cfg(enforce_completable_pair_cost=False)
        intents, why = decide_quotes(cfg, _EDGE_UP, _EDGE_DOWN, Inventory(),
                                     1e9, None)
        assert [i.price for i in intents] == [0.29, 0.70]
        assert round(sum(i.price for i in intents), 4) == 0.99
        assert "entry_pair_cost" not in why

    def test_the_cap_comparison_absorbs_float_noise(self):
        # A raw float sum refuses its own tie: 0.495 + 0.495 evaluates to
        # 0.9900000000000001. The cap compares rounded to 4dp on both sides, so
        # a pair that IS $0.99 is never refused as "over $0.99".
        assert pair_cost_over_cap([0.495, 0.495], 0.99) is False
        assert pair_cost_over_cap([0.49, 0.50], 0.99) is False
        assert pair_cost_over_cap([0.56, 0.53], 0.99) is True

    def test_held_balanced_inventory_still_gated_on_the_new_pair_sum(self):
        # 100 UP and 100 DOWN held at 10c each: the book is balanced, so
        # `hard_block`'s pair-cost arm reads a held average of 0.10 and clears,
        # and there is no imbalance for the strict-paired arm to block. The new
        # pair being assembled is still $1.02, and that is what the cap judges.
        inv = Inventory(up_shares=100.0, up_cost=10.0,
                        down_shares=100.0, down_cost=10.0)
        cfg = _flat_cfg(enforce_completable_pair_cost=False)

        intents, why = decide_quotes(cfg, _WIDE_UP, _WIDE_DOWN, inv, 1e9, None)

        assert intents == []
        assert why.startswith("entry_pair_cost:")

    def test_a_lone_light_side_repair_is_not_gated(self):
        # Heavy UP (100 shares), light DOWN: the strict-paired arm blocks the
        # heavy side, so exactly one intent comes back. A one-legged result is
        # not a pair -- the cap has nothing to judge -- and the repair quote
        # that would buy the missing leg must survive (#622's `not_quoted` rule
        # would otherwise cancel the leg that fixes the imbalance).
        inv = Inventory(up_shares=100.0, up_cost=10.0)
        cfg = _flat_cfg(enforce_completable_pair_cost=False)

        intents, why = decide_quotes(cfg, _WIDE_UP, _WIDE_DOWN, inv, 1e9, None)

        assert [i.side for i in intents] == ["DOWN"]
        assert [i.price for i in intents] == [0.76]
        assert "entry_pair_cost" not in why

    def test_a_lifecycle_escalation_is_exempt(self):
        # The escalation path prices from the registry's hottest hedge bid, not
        # from mid, and carries the pair id it belongs to. Its own Decimal cap
        # check (held average + bid <= $0.99, equality allowed) already ran, so
        # the entry cap must not double-judge it: a pair_id intent is never
        # inspected here.
        held = 0.55
        bid = max_profitable_hedge_bid(held, 0.99, 0.01)
        assert bid == 0.44
        override = LifecycleQuoteOverride(
            pair_id="pair-1", token_id=DOWN, price=bid, size=50,
            state=LegState.ESCALATED_HEDGE, held_average_price=held)
        inv = Inventory(up_shares=100.0, up_cost=55.0)

        intents, why = decide_quotes(
            _cfg(), _book(0.30, UP), _book(0.44, DOWN), inv, 1e9,
            lifecycle_override=override)

        assert [(i.side, i.price, i.pair_id) for i in intents] == [
            ("DOWN", 0.44, "pair-1")]
        assert "entry_pair_cost" not in why


# --- Part 2: the break-even anchor in the state summary ----------------------
#
# `summarize_state` reads the registry read-only (`mode=ro`) and derives the
# anchor from the fills already on file: the price the missing leg must beat for
# the pair to still merge at a profit is `1.00 - the first opening fill`. Nothing
# is stored, nothing cancels, nothing exits -- the anchor is telemetry that
# follows the pair's current membership, so a restart cannot change it.

import sqlite3

from core_brain.order_registry import FillRecord, OrderRecord, OrderRegistry
from core_brain.registry_state import break_even_price, summarize_state

_TS = 1_700_000_000_000


@pytest.fixture
def live_db(tmp_path):
    """A live-schema registry in a temp dir: the tests never touch data/orders.db."""
    return tmp_path / "live.db"


def _order(reg, oid, token, price, *, side="BUY", pair_id="pair-1"):
    reg.create_order(OrderRecord(
        id=oid, condition_id="cond-1", token_id=token, side=side, price=price,
        original_size=10.0, status="open", posted_ts=_TS, last_polled_ts=_TS,
        pair_id=pair_id))
    return oid


def _fill(reg, trade_id, oid, price, *, venue_ts=_TS + 1000,
          recorded_ts=_TS + 2000, size=10.0):
    return reg.record_fill(FillRecord(
        trade_id=trade_id, order_uuid=oid, size=size, price=price,
        venue_ts=venue_ts, recorded_ts=recorded_ts, run_id="run-a"))


def _pairs(db_path):
    return {p["pair_id"]: p for p in summarize_state(db_path)["pairs"]}


def _snapshot(db_path):
    """Every row of the three tables the anchor's read path touches."""
    con = sqlite3.connect(str(db_path))
    try:
        return {t: con.execute(f"SELECT * FROM {t} ORDER BY rowid").fetchall()
                for t in ("orders", "fills", "single_leg_lifecycle")}
    finally:
        con.close()


class TestBreakEvenAnchor:
    def test_the_helper_rounds_the_break_even_to_four_places(self):
        assert break_even_price(0.45) == 0.55
        assert break_even_price(0.29) == 0.71
        assert break_even_price(0.3333) == 0.6667

    def test_the_first_opening_fill_anchors_the_pair(self, live_db):
        reg = OrderRegistry(db_path=live_db)
        _order(reg, "o-up", UP, 0.54)
        _order(reg, "o-dn", DOWN, 0.43)
        _fill(reg, "t1", "o-up", 0.45)

        pair = _pairs(live_db)["pair-1"]

        assert pair["break_even"] == {
            "filled_token_id": UP,
            "first_fill_trade_id": "t1",
            "first_fill_price": 0.45,
            "break_even_price": 0.55,
            "opposite_token_id": DOWN,
        }
        # ...and the fields it sits beside are what they were before the anchor:
        # the leg that filled is priced at what it cost, not at what we bid.
        assert pair["hedge_state"] == "NAKED"
        assert pair["naked_info"]["unhedged_shares"] == 10.0
        assert pair["combined_price"] == 0.88

    def test_later_partial_and_opposite_fills_do_not_move_the_anchor(self, live_db):
        reg = OrderRegistry(db_path=live_db)
        _order(reg, "o-up", UP, 0.54)
        _order(reg, "o-dn", DOWN, 0.43)
        _fill(reg, "t1", "o-up", 0.45, venue_ts=_TS + 1000)
        _fill(reg, "t2", "o-up", 0.50, venue_ts=_TS + 2000, size=2.0)
        _fill(reg, "t3", "o-dn", 0.40, venue_ts=_TS + 3000)

        anchor = _pairs(live_db)["pair-1"]["break_even"]

        assert anchor["first_fill_trade_id"] == "t1"
        assert anchor["first_fill_price"] == 0.45
        assert anchor["break_even_price"] == 0.55

    def test_a_duplicate_trade_id_is_refused_and_a_sell_never_anchors(self, live_db):
        reg = OrderRegistry(db_path=live_db)
        _order(reg, "o-up", UP, 0.54)
        _order(reg, "o-dn", DOWN, 0.43)
        _fill(reg, "t1", "o-up", 0.45)
        # The venue replaying a trade must not rewrite the anchor: `record_fill`
        # refuses the duplicate, so the stored fill (and the anchor) stands.
        assert reg.record_fill(FillRecord(
            trade_id="t1", order_uuid="o-up", size=10.0, price=0.99,
            venue_ts=_TS - 99_999, recorded_ts=_TS)) is False

        # A pair whose only fill closes a leg has no opening fill to anchor on.
        _order(reg, "s-dn", DOWN, 0.43, side="SELL", pair_id="pair-sell")
        _order(reg, "s-up", UP, 0.54, pair_id="pair-sell")
        _fill(reg, "t9", "s-dn", 0.46, venue_ts=_TS - 50_000)

        pairs = _pairs(live_db)
        assert pairs["pair-1"]["break_even"]["first_fill_trade_id"] == "t1"
        assert pairs["pair-1"]["break_even"]["first_fill_price"] == 0.45
        assert pairs["pair-sell"]["break_even"] is None

    def test_timestamp_ties_break_on_recorded_then_trade_id(self, live_db):
        reg = OrderRegistry(db_path=live_db)
        _order(reg, "o-up", UP, 0.54)
        _order(reg, "o-dn", DOWN, 0.43)
        # Same venue clock, different arrival: the venue is the authority, so the
        # tie is broken by when we recorded it.
        _fill(reg, "t-late", "o-up", 0.50, venue_ts=_TS + 1000,
              recorded_ts=_TS + 9000)
        _fill(reg, "t-early", "o-up", 0.45, venue_ts=_TS + 1000,
              recorded_ts=_TS + 100)
        # Identical on both clocks: the trade id decides, so the anchor cannot
        # wander between two reads of the same store.
        _order(reg, "o-up2", UP, 0.54, pair_id="pair-2")
        _order(reg, "o-dn2", DOWN, 0.43, pair_id="pair-2")
        _fill(reg, "t-b", "o-up2", 0.48, venue_ts=_TS + 5000,
              recorded_ts=_TS + 5000)
        _fill(reg, "t-a", "o-up2", 0.47, venue_ts=_TS + 5000,
              recorded_ts=_TS + 5000)

        pairs = _pairs(live_db)

        assert pairs["pair-1"]["break_even"]["first_fill_trade_id"] == "t-early"
        assert pairs["pair-2"]["break_even"]["first_fill_trade_id"] == "t-a"

    def test_a_fill_with_no_venue_timestamp_sorts_last(self, live_db):
        reg = OrderRegistry(db_path=live_db)
        _order(reg, "o-up", UP, 0.54)
        _order(reg, "o-dn", DOWN, 0.43)
        _fill(reg, "t-null", "o-up", 0.45, venue_ts=None, recorded_ts=_TS)
        _fill(reg, "t-real", "o-up", 0.50, venue_ts=_TS + 1000,
              recorded_ts=_TS + 2000)

        state = summarize_state(live_db)

        pair = {p["pair_id"]: p for p in state["pairs"]}["pair-1"]
        assert pair["break_even"]["first_fill_trade_id"] == "t-real"
        # An unknown venue time is unknown, not zero seconds old.
        ages = {f["trade_id"]: f["age_sec"] for f in state["fills"]}
        assert ages["t-null"] is None
        assert ages["t-real"] is not None

    def test_a_pair_whose_only_fill_has_no_venue_timestamp_still_reads(self, live_db):
        reg = OrderRegistry(db_path=live_db)
        _order(reg, "o-up", UP, 0.54)
        _order(reg, "o-dn", DOWN, 0.43)
        _fill(reg, "t-null", "o-up", 0.45, venue_ts=None, recorded_ts=_TS)

        pair = _pairs(live_db)["pair-1"]

        # It is still the only opening fill, so it anchors; None-last only ranks
        # it against fills that do have a venue time.
        assert pair["break_even"]["first_fill_trade_id"] == "t-null"
        assert pair["break_even"]["break_even_price"] == 0.55
        # With no venue time to date the naked leg from, the order's own post
        # time is the next best evidence.
        assert pair["naked_info"]["naked_since_ts"] == _TS

    def test_a_pair_with_no_fill_or_no_pair_id_has_no_anchor(self, live_db):
        reg = OrderRegistry(db_path=live_db)
        _order(reg, "o-up", UP, 0.54)
        _order(reg, "o-dn", DOWN, 0.43)          # pair-1: never filled
        _order(reg, "u-up", UP, 0.54, pair_id=None)
        _fill(reg, "t1", "u-up", 0.45)           # unattributed: no pair to anchor

        pairs = _pairs(live_db)

        assert pairs["pair-1"]["break_even"] is None
        assert pairs["unpaired_u-up"]["break_even"] is None

    def test_the_anchor_is_identical_across_a_reread(self, live_db):
        reg = OrderRegistry(db_path=live_db)
        _order(reg, "o-up", UP, 0.54)
        _order(reg, "o-dn", DOWN, 0.43)
        _fill(reg, "t1", "o-up", 0.45)

        first = [p["break_even"] for p in summarize_state(live_db)["pairs"]]
        second = [p["break_even"] for p in summarize_state(live_db)["pairs"]]

        assert first == second
        assert first[0]["first_fill_trade_id"] == "t1"

    def test_reading_the_state_writes_nothing(self, live_db):
        reg = OrderRegistry(db_path=live_db)
        _order(reg, "o-up", UP, 0.54)
        _order(reg, "o-dn", DOWN, 0.43)
        _fill(reg, "t1", "o-up", 0.45)
        con = sqlite3.connect(str(live_db))
        try:
            con.execute(
                "INSERT INTO single_leg_lifecycle "
                "(pair_id, condition_id, state, reason, updated_ts_ms, "
                " evidence_json) VALUES (?, ?, ?, ?, ?, ?)",
                ("pair-1", "cond-1", "ESCALATED_HEDGE", "test", _TS, "{}"))
            con.commit()
        finally:
            con.close()

        before = _snapshot(live_db)
        summarize_state(live_db)

        # The anchor is derived at read time: no orders posted, none cancelled,
        # no fill written, no lifecycle transition.
        assert _snapshot(live_db) == before


class TestMakerConfigNamingStandardization:
    """TDD pins for #632: parameter naming standardization across MakerConfig."""

    def test_canonical_fields_and_defaults_core_config(self):
        from core_brain.config import MakerConfig
        cfg = MakerConfig()
        assert cfg.max_skew_offset == 0.015
        assert cfg.min_book_depth_shares == 200.0
        assert cfg.emergency_hedge_pct == 0.8
        assert cfg.couple_risk_pct == 0.01
        assert cfg.quote_window_pct == 0.40
        assert cfg.stat_gate_bankroll_pct == 0.01

    def test_canonical_fields_and_defaults_scoring_config(self):
        from scoring.config import MakerConfig as ScoringMakerConfig
        cfg = ScoringMakerConfig()
        assert cfg.max_skew_offset == 0.015
        assert cfg.min_book_depth_shares == 200.0
        assert cfg.emergency_hedge_pct == 0.8
        assert cfg.quote_window_pct == 0.40

    def test_no_frac_or_fraction_fields_on_both_classes(self):
        from dataclasses import fields
        from core_brain.config import MakerConfig
        from scoring.config import MakerConfig as ScoringMakerConfig

        core_field_names = [f.name for f in fields(MakerConfig)]
        assert not any("_frac" in name or "_fraction" in name for name in core_field_names), (
            f"Found _frac/_fraction field in core MakerConfig: {core_field_names}"
        )

        scoring_field_names = [f.name for f in fields(ScoringMakerConfig)]
        assert not any("_frac" in name or "_fraction" in name for name in scoring_field_names), (
            f"Found _frac/_fraction field in scoring MakerConfig: {scoring_field_names}"
        )

    def test_read_aliases_core_config(self):
        from core_brain.config import MakerConfig
        cfg = MakerConfig(
            max_skew_offset=0.02,
            min_book_depth_shares=300.0,
            emergency_hedge_pct=0.75,
            couple_risk_pct=0.02,
            quote_window_pct=0.50,
            stat_gate_bankroll_pct=0.02,
        )
        assert cfg.max_skew == 0.02
        assert cfg.min_book_depth_sh == 300.0
        assert cfg.emergency_hedge_frac == 0.75
        assert cfg.couple_risk_frac == 0.02
        assert cfg.quote_window_frac == 0.50
        assert cfg.stat_gate_bankroll_fraction == 0.02
        assert cfg.select_spread_frac == 41.0 / 120.0

    def test_read_aliases_scoring_config(self):
        from scoring.config import MakerConfig as ScoringMakerConfig
        cfg = ScoringMakerConfig(
            max_skew_offset=0.02,
            min_book_depth_shares=300.0,
            emergency_hedge_pct=0.75,
            quote_window_pct=0.50,
        )
        assert cfg.max_skew == 0.02
        assert cfg.min_book_depth_sh == 300.0
        assert cfg.emergency_hedge_frac == 0.75
        assert cfg.quote_window_frac == 0.50
        assert cfg.select_spread_frac == 41.0 / 120.0

    def test_alias_set_raises_attribute_error(self):
        from core_brain.config import MakerConfig
        cfg = MakerConfig()
        with pytest.raises(AttributeError):
            setattr(cfg, "max_skew", 0.05)
        with pytest.raises(AttributeError):
            setattr(cfg, "emergency_hedge_frac", 0.5)

    def test_legacy_keyword_raises_type_error(self):
        from core_brain.config import MakerConfig
        with pytest.raises(TypeError):
            MakerConfig(emergency_hedge_frac=0.8)
        with pytest.raises(TypeError):
            MakerConfig(max_skew=0.015)
        with pytest.raises(TypeError):
            MakerConfig(select_spread_frac=41.0 / 120.0)

