"""The entry pair-cost cap on the from-mid path (#627).

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
