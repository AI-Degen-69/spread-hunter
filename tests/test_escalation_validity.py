"""Escalation-validity interface tests (#806, second slice).

`single_leg_lifecycle` owns what counts as a valid escalated hedge: the
quoting path enforces it, the saver probes it, neither re-derives it. One
rule, one module — a mismatch between the override price and the recomputed
maximum profitable bid refuses, so a hedge that cannot merge at a profit is
never quoted.
"""
from __future__ import annotations

import pytest

from core_brain.single_leg_lifecycle import (
    LegState,
    LifecycleQuoteOverride,
    capped_hedge_tick_exists,
    max_profitable_hedge_bid,
    validate_escalation_override,
)


def _override(**kw):
    held = kw.pop("held", 0.55)
    price = kw.pop("price", None)
    if price is None:
        price = max_profitable_hedge_bid(held, 0.99, 0.01)
    base = dict(
        pair_id="pair-1", token_id="tok-dn", price=price, size=50,
        state=LegState.ESCALATED_HEDGE, held_average_price=held,
    )
    base.update(kw)
    return LifecycleQuoteOverride(**base)


def test_valid_override_passes():
    assert validate_escalation_override(
        _override(), cap=0.99, tick_size=0.01) is None


def test_wrong_state_refuses():
    assert validate_escalation_override(
        _override(state=LegState.PATIENT_WAIT),
        cap=0.99, tick_size=0.01,
    ) == "lifecycle quote override is not an escalated hedge"


@pytest.mark.parametrize("field,value,message", [
    ("pair_id", "", "lifecycle escalation requires a pair ID"),
    ("token_id", "", "lifecycle escalation requires a token ID"),
    ("price", 0.0, "lifecycle escalation price must be finite and between zero and one"),
    ("held_average_price", 1.5, "lifecycle held-leg average must be finite and between zero and one"),
    ("size", 0, "lifecycle escalation size must be a positive integer"),
])
def test_malformed_override_refuses(field, value, message):
    assert validate_escalation_override(
        _override(**{field: value}), cap=0.99, tick_size=0.01) == message


def test_non_positive_cap_refuses():
    assert validate_escalation_override(
        _override(), cap=0.0, tick_size=0.01,
    ) == "lifecycle pair cap must be finite and positive"


def test_pair_cost_over_cap_refuses():
    reason = validate_escalation_override(
        _override(price=0.60), cap=0.99, tick_size=0.01)
    assert reason is not None and "exceeds $0.990 cap" in reason


def test_unfittable_tick_refuses():
    # Held at 0.985 leaves 0.005 under a 0.99 cap: no whole tick fits.
    reason = validate_escalation_override(
        _override(held=0.985, price=0.005), cap=0.99, tick_size=0.01)
    assert reason is not None and reason.startswith(
        "lifecycle maximum profitable bid unavailable")


def test_stale_bid_refuses():
    reason = validate_escalation_override(
        _override(price=0.40), cap=0.99, tick_size=0.01)
    assert reason is not None and "does not match the maximum profitable bid" in reason


@pytest.mark.parametrize("held,cap,tick,expected", [
    (0.55, 0.99, 0.01, True),
    (0.985, 0.99, 0.01, False),
    (0.50, 0.0, 0.01, False),
])
def test_tick_existence_matches_the_bid_math(held, cap, tick, expected):
    assert capped_hedge_tick_exists(held, cap, tick) is expected
