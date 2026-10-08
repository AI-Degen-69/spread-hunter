import pytest

from core_brain.order_registry import OrderRegistry
from core_brain.single_leg_lifecycle import (
    LegState,
    SingleLegPosition,
    evaluate,
    max_profitable_hedge_bid,
    transition,
)


def _position(*, up_size, down_size, up_avg, down_avg=0.0,
              up_bid, down_bid=0.52, base_offset=0.04):
    return SingleLegPosition(
        pair_id="pair-1",
        condition_id="condition-1",
        up_token_id="token-up",
        down_token_id="token-down",
        up_size=up_size,
        down_size=down_size,
        up_avg_price=up_avg,
        down_avg_price=down_avg,
        up_best_bid=up_bid,
        down_best_bid=down_bid,
        base_offset=base_offset,
    )


def test_no_fills_stays_dual_resting():
    decision = transition(
        _position(up_size=0.0, down_size=0.0, up_avg=0.0, up_bid=0.48),
        None,
        max_pair_cost=0.99,
    )

    assert decision.state is LegState.DUAL_RESTING
    assert decision.action == "rest"


def test_first_fill_enters_patient_wait_and_keeps_target():
    decision = transition(
        _position(up_size=2.0, down_size=0.0, up_avg=0.48, up_bid=0.47),
        LegState.DUAL_RESTING,
        max_pair_cost=0.99,
    )

    assert decision.state is LegState.PATIENT_WAIT
    assert decision.action == "wait"
    assert decision.held_token_id == "token-up"
    assert decision.opposite_token_id == "token-down"


def test_drawdown_below_escalation_threshold_remains_patient():
    decision = transition(
        _position(up_size=2.0, down_size=0.0, up_avg=0.48,
                  up_bid=0.401, base_offset=0.04),
        LegState.PATIENT_WAIT,
        max_pair_cost=0.99,
    )

    assert decision.state is LegState.PATIENT_WAIT


def test_drawdown_above_escalation_threshold_escalates():
    decision = transition(
        _position(up_size=2.0, down_size=0.0, up_avg=0.48,
                  up_bid=0.399, base_offset=0.04),
        LegState.PATIENT_WAIT,
        max_pair_cost=0.99,
    )

    assert decision.state is LegState.ESCALATED_HEDGE


def test_escalates_at_threshold_to_tick_floored_pair_ceiling():
    decision = transition(
        _position(up_size=2.0, down_size=0.0, up_avg=0.48,
                  up_bid=0.40, base_offset=0.04),
        LegState.PATIENT_WAIT,
        max_pair_cost=0.99,
    )

    assert decision.state is LegState.ESCALATED_HEDGE
    assert decision.action == "hedge"
    assert decision.opposite_limit_price == pytest.approx(0.51)
    assert decision.opposite_limit_price + decision.held_average_price <= 0.99


def test_escalated_state_stays_sticky_after_price_recovers():
    decision = transition(
        _position(up_size=2.0, down_size=0.0, up_avg=0.48,
                  up_bid=0.47, base_offset=0.04),
        LegState.ESCALATED_HEDGE,
        max_pair_cost=0.99,
    )

    assert decision.state is LegState.ESCALATED_HEDGE
    assert decision.action == "hedge"


def test_hard_stop_at_fifteen_cents_precedes_escalation():
    decision = transition(
        _position(up_size=2.0, down_size=0.0, up_avg=0.48,
                  up_bid=0.15, base_offset=0.04),
        LegState.PATIENT_WAIT,
        max_pair_cost=0.99,
    )

    assert decision.state is LegState.HARD_STOP
    assert decision.action == "hard_stop"


def test_unreadable_held_bid_does_not_escalate_or_hard_stop():
    position = _position(
        up_size=2.0, down_size=0.0, up_avg=0.48, up_bid=None,
    )

    decision = transition(position, LegState.PATIENT_WAIT, max_pair_cost=0.99)

    assert decision.state is LegState.PATIENT_WAIT
    assert decision.action == "wait"


def test_partial_opposite_fill_remains_a_naked_position():
    decision = transition(
        _position(up_size=2.0, down_size=1.0, up_avg=0.48,
                  down_avg=0.48, up_bid=0.40),
        LegState.PATIENT_WAIT,
        max_pair_cost=0.99,
    )

    assert decision.state is LegState.ESCALATED_HEDGE
    assert decision.naked_size == pytest.approx(1.0)


def test_equal_filled_legs_at_ninety_nine_cents_are_pair_locked():
    decision = transition(
        _position(up_size=2.0, down_size=2.0, up_avg=0.48,
                  down_avg=0.51, up_bid=0.48, down_bid=0.51),
        LegState.ESCALATED_HEDGE,
        max_pair_cost=0.99,
    )

    assert decision.state is LegState.PAIR_LOCKED
    assert decision.action == "locked"


def test_equal_filled_legs_above_cap_are_not_pair_locked():
    decision = transition(
        _position(up_size=2.0, down_size=2.0, up_avg=0.50,
                  down_avg=0.50, up_bid=0.50, down_bid=0.50),
        LegState.ESCALATED_HEDGE,
        max_pair_cost=0.99,
    )

    assert decision.state is not LegState.PAIR_LOCKED
    assert decision.action == "refused"


def test_escalation_without_a_positive_capped_tick_is_refused():
    decision = transition(
        _position(up_size=2.0, down_size=0.0, up_avg=0.99,
                  up_bid=0.90, base_offset=0.04),
        LegState.PATIENT_WAIT,
        max_pair_cost=0.99,
    )

    assert decision.state is LegState.ESCALATED_HEDGE
    assert decision.action == "refused"
    assert "no positive tick-aligned" in decision.reason


def test_settlement_fallback_follows_hard_stop_precedence():
    decision = transition(
        _position(up_size=2.0, down_size=0.0, up_avg=0.48, up_bid=0.14),
        LegState.PATIENT_WAIT,
        max_pair_cost=0.99,
        settlement_due=True,
    )

    assert decision.state is LegState.HARD_STOP
    assert decision.action == "hard_stop"


@pytest.mark.parametrize(
    ("held_average", "cap", "tick_size"),
    [
        (0.48, 0.99, 0.01),
        (0.48, 0.985, 0.01),
    ],
)
def test_hedge_bid_is_floored_to_fit_cap(held_average, cap, tick_size):
    price = max_profitable_hedge_bid(held_average, cap, tick_size)

    assert price + held_average <= min(0.99, cap)
    assert price == pytest.approx(0.51 if cap == 0.99 else 0.50)


@pytest.mark.parametrize(
    ("held_average", "cap", "tick_size"),
    [
        (float("nan"), 0.99, 0.01),
        (0.48, float("inf"), 0.01),
        (0.48, 0.99, 0.0),
        (0.995, 0.99, 0.01),
    ],
)
def test_invalid_hedge_bid_inputs_are_refused(held_average, cap, tick_size):
    with pytest.raises(ValueError):
        max_profitable_hedge_bid(held_average, cap, tick_size)


def test_settlement_due_uses_fallback_without_overriding_hard_stop():
    decision = transition(
        _position(up_size=2.0, down_size=0.0, up_avg=0.48,
                  up_bid=0.40),
        LegState.PATIENT_WAIT,
        max_pair_cost=0.99,
        settlement_due=True,
    )

    assert decision.state is LegState.PATIENT_WAIT
    assert decision.action == "settlement_fallback"


def test_evaluate_persists_sticky_escalation_and_does_not_duplicate_transition(
    tmp_path,
):
    db_path = tmp_path / "lifecycle.db"
    position = _position(
        up_size=2.0, down_size=0.0, up_avg=0.48,
        up_bid=0.40, base_offset=0.04,
    )
    registry = OrderRegistry(db_path=db_path)

    first = evaluate(position, registry, max_pair_cost=0.99)
    first_record = registry.get_lifecycle_state("pair-1")

    assert first.state is LegState.ESCALATED_HEDGE
    assert first_record is not None
    assert first_record.state == LegState.ESCALATED_HEDGE.value

    reopened = OrderRegistry(db_path=db_path)
    recovered_position = _position(
        up_size=2.0, down_size=0.0, up_avg=0.48,
        up_bid=0.47, base_offset=0.04,
    )
    second = evaluate(recovered_position, reopened, max_pair_cost=0.99)

    assert second.state is LegState.ESCALATED_HEDGE
    assert reopened.get_lifecycle_state("pair-1") == first_record


def test_evaluate_propagates_lifecycle_state_write_errors():
    class BrokenRegistry:
        def get_lifecycle_state(self, pair_id):
            return None

        def save_lifecycle_state(self, record):
            raise OSError("state store unavailable")

    with pytest.raises(OSError, match="state store unavailable"):
        evaluate(
            _position(up_size=2.0, down_size=0.0, up_avg=0.48, up_bid=0.47),
            BrokenRegistry(),
            max_pair_cost=0.99,
        )
