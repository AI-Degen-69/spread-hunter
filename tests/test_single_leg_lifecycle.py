import pytest

from core_brain.order_registry import OrderRegistry
from core_brain.single_leg_lifecycle import (
    STOP_LOSS_MODES,
    LegState,
    SingleLegPosition,
    evaluate,
    hedge_unreachable_at_cap,
    max_profitable_hedge_bid,
    stop_trigger,
    transition,
)


def test_hedge_unreachable_at_cap_is_about_the_cap_not_the_spread():
    """#797: wide is reachable; only a market past the cap is unreachable.

    Held 0.82 against a 0.99 cap leaves a 0.17 hedge bid. An ask of 0.17 or
    0.05 is still reachable at the cap, however wide the spread looks; only an
    ask above 0.17 has walked away from what the cap can pay.
    """
    assert hedge_unreachable_at_cap(0.82, 0.17, 0.99, 0.01) is False
    assert hedge_unreachable_at_cap(0.82, 0.05, 0.99, 0.01) is False
    assert hedge_unreachable_at_cap(0.82, 0.4225, 0.99, 0.01) is True


def test_hedge_unreachable_at_cap_treats_unknown_as_reachable():
    """An unreadable hedge price is unknown, and unknown never sells a leg."""
    assert hedge_unreachable_at_cap(0.82, None, 0.99, 0.01) is False
    assert hedge_unreachable_at_cap(0.82, 0.0, 0.99, 0.01) is False
    assert hedge_unreachable_at_cap(0.82, float("nan"), 0.99, 0.01) is False
    # Above 1.0 is not a price in a binary market: a corrupt read is unknown,
    # and unknown never sells a leg.
    assert hedge_unreachable_at_cap(0.82, 1.2, 0.99, 0.01) is False


def test_hedge_unreachable_at_cap_leaves_the_cap_its_own_tick():
    """A market one tick past the cap bid is at the cap, not past it (#797).

    The escalated hedge bids the cap; a market sitting a single tick above that
    bid is the escalation path's ordinary case, so the bail-out must not dump
    the leg there. Only a market that has left the cap's own tick behind is
    unreachable.
    """
    # cap 0.995 is clamped to 0.99: held 0.60 leaves a 0.39 hedge bid.
    assert hedge_unreachable_at_cap(0.60, 0.40, 0.995, 0.01) is False
    assert hedge_unreachable_at_cap(0.60, 0.39, 0.995, 0.01) is False
    assert hedge_unreachable_at_cap(0.60, 0.41, 0.995, 0.01) is True


def test_hedge_unreachable_at_cap_without_a_tick_under_the_cap():
    """Held at 0.99 leaves no positive tick: no market can reach the cap."""
    assert hedge_unreachable_at_cap(0.99, 0.02, 0.99, 0.01) is True


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
        bot_pos_price_max_pair_cost_usd=0.99,
    )

    assert decision.state is LegState.DUAL_RESTING
    assert decision.action == "rest"


def test_first_fill_enters_patient_wait_and_keeps_target():
    decision = transition(
        _position(up_size=2.0, down_size=0.0, up_avg=0.48, up_bid=0.47),
        LegState.DUAL_RESTING,
        bot_pos_price_max_pair_cost_usd=0.99,
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
        bot_pos_price_max_pair_cost_usd=0.99,
    )

    assert decision.state is LegState.PATIENT_WAIT


def test_drawdown_above_escalation_threshold_escalates():
    decision = transition(
        _position(up_size=2.0, down_size=0.0, up_avg=0.48,
                  up_bid=0.399, base_offset=0.04),
        LegState.PATIENT_WAIT,
        bot_pos_price_max_pair_cost_usd=0.99,
    )

    assert decision.state is LegState.ESCALATED_HEDGE


def test_escalates_at_threshold_to_tick_floored_pair_ceiling():
    decision = transition(
        _position(up_size=2.0, down_size=0.0, up_avg=0.48,
                  up_bid=0.40, base_offset=0.04),
        LegState.PATIENT_WAIT,
        bot_pos_price_max_pair_cost_usd=0.99,
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
        bot_pos_price_max_pair_cost_usd=0.99,
    )

    assert decision.state is LegState.ESCALATED_HEDGE
    assert decision.action == "hedge"


def test_hard_stop_at_the_entry_relative_floor_precedes_escalation():
    # Entry 0.48 at the 75% default floor is 0.12, not a static 0.15.
    decision = transition(
        _position(up_size=2.0, down_size=0.0, up_avg=0.48,
                  up_bid=0.12, base_offset=0.04),
        LegState.PATIENT_WAIT,
        bot_pos_price_max_pair_cost_usd=0.99,
    )

    assert decision.state is LegState.HARD_STOP
    assert decision.action == "hard_stop"


def test_unreadable_held_bid_does_not_escalate_or_hard_stop():
    position = _position(
        up_size=2.0, down_size=0.0, up_avg=0.48, up_bid=None,
    )

    decision = transition(position, LegState.PATIENT_WAIT, bot_pos_price_max_pair_cost_usd=0.99)

    assert decision.state is LegState.PATIENT_WAIT
    assert decision.action == "wait"


def test_partial_opposite_fill_remains_a_naked_position():
    decision = transition(
        _position(up_size=2.0, down_size=1.0, up_avg=0.48,
                  down_avg=0.48, up_bid=0.40),
        LegState.PATIENT_WAIT,
        bot_pos_price_max_pair_cost_usd=0.99,
    )

    assert decision.state is LegState.ESCALATED_HEDGE
    assert decision.naked_size == pytest.approx(1.0)


def test_equal_filled_legs_at_ninety_nine_cents_are_pair_locked():
    decision = transition(
        _position(up_size=2.0, down_size=2.0, up_avg=0.48,
                  down_avg=0.51, up_bid=0.48, down_bid=0.51),
        LegState.ESCALATED_HEDGE,
        bot_pos_price_max_pair_cost_usd=0.99,
    )

    assert decision.state is LegState.PAIR_LOCKED
    assert decision.action == "locked"


def test_equal_filled_legs_above_cap_are_not_pair_locked():
    decision = transition(
        _position(up_size=2.0, down_size=2.0, up_avg=0.50,
                  down_avg=0.50, up_bid=0.50, down_bid=0.50),
        LegState.ESCALATED_HEDGE,
        bot_pos_price_max_pair_cost_usd=0.99,
    )

    assert decision.state is not LegState.PAIR_LOCKED
    assert decision.action == "refused"


def test_escalation_without_a_positive_capped_tick_is_refused():
    decision = transition(
        _position(up_size=2.0, down_size=0.0, up_avg=0.99,
                  up_bid=0.90, base_offset=0.04),
        LegState.PATIENT_WAIT,
        bot_pos_price_max_pair_cost_usd=0.99,
    )

    assert decision.state is LegState.ESCALATED_HEDGE
    assert decision.action == "refused"
    assert "no positive tick-aligned" in decision.reason


def test_settlement_fallback_follows_hard_stop_precedence():
    decision = transition(
        _position(up_size=2.0, down_size=0.0, up_avg=0.48, up_bid=0.12),
        LegState.PATIENT_WAIT,
        bot_pos_price_max_pair_cost_usd=0.99,
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


# --- entry-relative stop (#619) --------------------------------------------
# The retired static $0.15 stop liquidated ANY entry at or below $0.15 the
# moment it filled, and the strategy quotes (and fills) between $0.10 and
# $0.90. The trigger is now derived from the held leg's own average fill, with
# the emergency floor as the only universal backstop.


def test_stop_loss_modes_are_the_three_canonical_spellings():
    assert STOP_LOSS_MODES == frozenset(
        {"NONE", "ABSOLUTE_DROP", "PERCENTAGE_DROP"}
    )


def test_stop_trigger_none_mode_uses_the_emergency_floor():
    # 0.10 - 75% = 0.025, which rounds UP to the 0.03 cent.
    trigger, source = stop_trigger(0.10, "NONE", 0.05, 10.0, 75.0)

    assert trigger == pytest.approx(0.03)
    assert source == "emergency"


def test_stop_trigger_rounds_a_fractional_cent_up():
    # The operator rule: any sub-cent fraction ceilings to the next whole
    # cent, on both the strategy trigger and the emergency floor.
    trigger, source = stop_trigger(0.50, "NONE", 0.05, 10.0, 75.0)
    assert trigger == pytest.approx(0.13)
    assert source == "emergency"

    trigger, source = stop_trigger(0.50, "ABSOLUTE_DROP", 0.365, 10.0, 75.0)
    assert trigger == pytest.approx(0.14)
    assert source == "strategy"

    # A strategy that ceilings onto the floor ties -- and the tie goes to
    # the emergency backstop (0.50 - 0.375 = 0.125 -> 0.13, floor 0.13).
    trigger, source = stop_trigger(0.50, "ABSOLUTE_DROP", 0.375, 10.0, 75.0)
    assert trigger == pytest.approx(0.13)
    assert source == "emergency"

    # Whole-cent triggers are untouched by the ceiling.
    trigger, source = stop_trigger(0.50, "ABSOLUTE_DROP", 0.05, 10.0, 75.0)
    assert trigger == pytest.approx(0.45)
    assert source == "strategy"


def test_stop_trigger_percentage_mode_subtracts_the_drawdown():
    trigger, source = stop_trigger(0.60, "PERCENTAGE_DROP", 0.05, 10.0, 75.0)

    assert trigger == pytest.approx(0.54)
    assert source == "strategy"


def test_stop_trigger_absolute_mode_subtracts_the_cent_drop():
    trigger, source = stop_trigger(0.50, "ABSOLUTE_DROP", 0.05, 10.0, 75.0)

    assert trigger == pytest.approx(0.45)
    assert source == "strategy"


def test_stop_trigger_keeps_the_higher_of_strategy_and_floor():
    # 0.12 - 25% = 0.09, well above 0.12's 0.03 emergency floor.
    trigger, source = stop_trigger(0.12, "PERCENTAGE_DROP", 0.05, 25.0, 75.0)

    assert trigger == pytest.approx(0.09)
    assert source == "strategy"


def test_stop_trigger_falls_back_to_the_floor_when_the_strategy_is_lower():
    # 0.20 - 0.85 is below zero: the emergency floor is what protects.
    trigger, source = stop_trigger(0.20, "ABSOLUTE_DROP", 0.85, 10.0, 75.0)

    assert trigger == pytest.approx(0.05)
    assert source == "emergency"


def test_stop_trigger_floors_at_one_cent():
    # A 100% emergency drawdown on a $0.02 entry would otherwise sit at zero.
    trigger, source = stop_trigger(0.02, "NONE", 0.05, 10.0, 100.0)

    assert trigger == pytest.approx(0.01)
    assert source == "emergency"


@pytest.mark.parametrize(
    ("bot_risk_stop_adverse_drop_cents", "expected", "source"),
    # 0.50 - 0.90 is below zero, so the 0.125 emergency floor protects --
    # rounded up to the 0.13 cent.
    [(0.01, 0.49, "strategy"), (0.90, 0.13, "emergency")],
)
def test_stop_trigger_accepts_both_drop_cents_endpoints(bot_risk_stop_adverse_drop_cents, expected,
                                                       source):
    trigger, got_source = stop_trigger(
        0.50, "ABSOLUTE_DROP", bot_risk_stop_adverse_drop_cents, 10.0, 75.0,
    )

    assert trigger == pytest.approx(expected)
    assert got_source == source


def test_absolute_mode_ignores_the_drawdown_knob():
    """The two dynamic modes cannot bleed into each other."""
    low = stop_trigger(0.50, "ABSOLUTE_DROP", 0.05, 1.0, 75.0)
    high = stop_trigger(0.50, "ABSOLUTE_DROP", 0.05, 99.0, 75.0)

    assert low == high


def test_percentage_mode_ignores_the_cent_drop_knob():
    low = stop_trigger(0.50, "PERCENTAGE_DROP", 0.01, 10.0, 75.0)
    high = stop_trigger(0.50, "PERCENTAGE_DROP", 0.90, 10.0, 75.0)

    assert low == high


@pytest.mark.parametrize(
    "mode", ["ABSOLUTE", "PERCENTAGE", "usd", "pct", "bogus", ""]
)
def test_stop_trigger_refuses_non_canonical_modes(mode):
    with pytest.raises(ValueError, match="ABSOLUTE_DROP"):
        stop_trigger(0.50, mode, 0.05, 10.0, 75.0)


@pytest.mark.parametrize(
    "bot_risk_stop_adverse_drop_cents", [0.0, 0.009, 0.91, float("nan"), float("inf")]
)
def test_stop_trigger_refuses_drop_cents_outside_the_contract(bot_risk_stop_adverse_drop_cents):
    with pytest.raises(ValueError, match="bot_risk_stop_adverse_drop_cents"):
        stop_trigger(0.50, "ABSOLUTE_DROP", bot_risk_stop_adverse_drop_cents, 10.0, 75.0)


@pytest.mark.parametrize("pct", [0.0, -1.0, 100.1, float("nan")])
def test_stop_trigger_refuses_drawdown_pct_outside_the_contract(pct):
    with pytest.raises(ValueError, match="bot_risk_stop_max_drawdown_pct"):
        stop_trigger(0.50, "PERCENTAGE_DROP", 0.05, pct, 75.0)


@pytest.mark.parametrize("pct", [0.0, 100.1, float("nan"), float("inf")])
def test_stop_trigger_refuses_emergency_pct_outside_the_contract(pct):
    with pytest.raises(ValueError, match="bot_risk_circuit_max_drawdown_pct"):
        stop_trigger(0.50, "NONE", 0.05, 10.0, pct)


def test_stop_trigger_accepts_a_full_emergency_drawdown():
    trigger, _ = stop_trigger(0.02, "NONE", 0.05, 10.0, 100.0)

    assert trigger == pytest.approx(0.01)


@pytest.mark.parametrize("entry", [0.0, -0.5, 1.01, float("nan")])
def test_stop_trigger_refuses_an_entry_outside_the_price_range(entry):
    with pytest.raises(ValueError, match="entry"):
        stop_trigger(entry, "NONE", 0.05, 10.0, 75.0)


@pytest.mark.parametrize(
    ("entry", "bid", "expected"),
    # The 75% floor of 0.10 is 0.025, rounded UP to the 0.03 cent.
    [(0.10, 0.031, LegState.PATIENT_WAIT),
     (0.10, 0.03, LegState.HARD_STOP),
     (0.10, 0.029, LegState.HARD_STOP)],
)
def test_none_mode_stops_a_ten_cent_entry_at_the_floored_emergency(
    entry, bid, expected,
):
    decision = transition(
        _position(up_size=2.0, down_size=0.0, up_avg=entry, up_bid=bid,
                  base_offset=0.04),
        LegState.PATIENT_WAIT,
        bot_pos_price_max_pair_cost_usd=0.99,
    )

    assert decision.state is expected


@pytest.mark.parametrize(
    "entry,bid", [(0.10, 0.10), (0.12, 0.14), (0.14, 0.14)]
)
def test_low_entries_are_no_longer_liquidated_on_fill(entry, bid):
    """A resting $0.15 stop booked a loss on every 0.10-0.14 entry."""
    decision = transition(
        _position(up_size=2.0, down_size=0.0, up_avg=entry, up_bid=bid,
                  base_offset=0.04),
        LegState.PATIENT_WAIT,
        bot_pos_price_max_pair_cost_usd=0.99,
    )

    assert decision.state is LegState.PATIENT_WAIT
    assert decision.action == "wait"


def test_hard_stop_reason_names_the_trigger_that_fired():
    decision = transition(
        _position(up_size=2.0, down_size=0.0, up_avg=0.60, up_bid=0.54,
                  base_offset=0.04),
        LegState.PATIENT_WAIT,
        bot_pos_price_max_pair_cost_usd=0.99,
        bot_risk_stop_mode="PERCENTAGE_DROP",
        bot_risk_stop_max_drawdown_pct=10.0,
    )

    assert decision.state is LegState.HARD_STOP
    assert "strategy" in decision.reason
    assert "0.5400" in decision.reason


def test_transition_refuses_an_unknown_stop_mode():
    decision = transition(
        _position(up_size=2.0, down_size=0.0, up_avg=0.50, up_bid=0.45),
        LegState.PATIENT_WAIT,
        bot_pos_price_max_pair_cost_usd=0.99,
        bot_risk_stop_mode="ABSOLUTE",
    )

    assert decision.state is LegState.PATIENT_WAIT
    assert decision.action == "refused"
    assert "ABSOLUTE_DROP" in decision.reason


@pytest.mark.parametrize(
    "knobs",
    [{"bot_risk_stop_adverse_drop_cents": 0.0},
     {"bot_risk_stop_adverse_drop_cents": 0.91},
     {"bot_risk_stop_max_drawdown_pct": 0.0},
     {"bot_risk_stop_max_drawdown_pct": float("nan")},
     {"bot_risk_circuit_max_drawdown_pct": 101.0},
     {"bot_risk_circuit_max_drawdown_pct": float("nan")}],
)
def test_transition_refuses_out_of_range_stop_knobs(knobs):
    decision = transition(
        _position(up_size=2.0, down_size=0.0, up_avg=0.50, up_bid=0.45),
        LegState.PATIENT_WAIT,
        bot_pos_price_max_pair_cost_usd=0.99,
        **knobs,
    )

    assert decision.action == "refused"


def test_transition_accepts_canonical_drawdown_keyword():
    decision = transition(
        _position(up_size=2.0, down_size=0.0, up_avg=0.50, up_bid=0.45),
        LegState.PATIENT_WAIT,
        bot_pos_price_max_pair_cost_usd=0.99,
        bot_risk_circuit_max_drawdown_pct=50.0,
    )
    assert decision.action != "refused"


def test_legacy_drawdown_keyword_is_rejected():
    """The retired keyword is not a keyword any more."""
    with pytest.raises(TypeError, match="unexpected keyword argument 'emergency_drawdown_pct'"):
        transition(
            _position(up_size=2.0, down_size=0.0, up_avg=0.50, up_bid=0.45),
            LegState.PATIENT_WAIT,
            bot_pos_price_max_pair_cost_usd=0.99,
            emergency_drawdown_pct=50.0,
        )


def test_evaluate_forwards_the_stop_knobs(tmp_path):
    registry = OrderRegistry(db_path=tmp_path / "lifecycle.db")
    position = _position(
        up_size=2.0, down_size=0.0, up_avg=0.60, up_bid=0.54,
        base_offset=0.04,
    )

    decision = evaluate(
        position, registry, bot_pos_price_max_pair_cost_usd=0.99,
        bot_risk_stop_mode="PERCENTAGE_DROP", bot_risk_stop_max_drawdown_pct=10.0,
    )

    assert decision.state is LegState.HARD_STOP
    record = registry.get_lifecycle_state("pair-1")
    assert record is not None
    assert record.state == LegState.HARD_STOP.value


def test_settlement_due_uses_fallback_without_overriding_hard_stop():
    decision = transition(
        _position(up_size=2.0, down_size=0.0, up_avg=0.48,
                  up_bid=0.40),
        LegState.PATIENT_WAIT,
        bot_pos_price_max_pair_cost_usd=0.99,
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

    first = evaluate(position, registry, bot_pos_price_max_pair_cost_usd=0.99)
    first_record = registry.get_lifecycle_state("pair-1")

    assert first.state is LegState.ESCALATED_HEDGE
    assert first_record is not None
    assert first_record.state == LegState.ESCALATED_HEDGE.value

    reopened = OrderRegistry(db_path=db_path)
    recovered_position = _position(
        up_size=2.0, down_size=0.0, up_avg=0.48,
        up_bid=0.47, base_offset=0.04,
    )
    second = evaluate(recovered_position, reopened, bot_pos_price_max_pair_cost_usd=0.99)

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
            bot_pos_price_max_pair_cost_usd=0.99,
        )


def test_evaluate_exposure_balanced_when_no_holdings():
    from core_brain.single_leg_lifecycle import evaluate_exposure, ExposureState
    res = evaluate_exposure("cond-1", {"tok-up": 0.0, "tok-dn": 0.0}, {"tok-up": 10.0, "tok-dn": 10.0})
    assert res.status == ExposureState.BALANCED
    assert res.held_deficit == 0.0
    assert res.coverage_shortfall == 0.0


def test_evaluate_exposure_covered_when_opposite_orders_match_or_exceed():
    from core_brain.single_leg_lifecycle import evaluate_exposure, ExposureState
    res = evaluate_exposure("cond-1", {"tok-up": 10.0, "tok-dn": 0.0}, {"tok-up": 0.0, "tok-dn": 12.0})
    assert res.status == ExposureState.COVERED
    assert res.heavy_token_id == "tok-up"
    assert res.light_token_id == "tok-dn"
    assert res.held_deficit == 10.0
    assert res.coverage_shortfall == 0.0
    assert res.over_coverage == 2.0


def test_evaluate_exposure_under_covered_when_orders_less_than_deficit():
    from core_brain.single_leg_lifecycle import evaluate_exposure, ExposureState
    res = evaluate_exposure("cond-1", {"tok-up": 10.0, "tok-dn": 0.0}, {"tok-up": 0.0, "tok-dn": 7.0})
    assert res.status == ExposureState.UNDER_COVERED
    assert res.heavy_token_id == "tok-up"
    assert res.light_token_id == "tok-dn"
    assert res.held_deficit == 10.0
    assert res.coverage_shortfall == 3.0
    assert res.over_coverage == 0.0


def test_evaluate_exposure_stranded_when_no_opposing_orders():
    from core_brain.single_leg_lifecycle import evaluate_exposure, ExposureState
    res = evaluate_exposure("cond-1", {"tok-up": 10.0, "tok-dn": 0.0}, {"tok-up": 0.0, "tok-dn": 0.0})
    assert res.status == ExposureState.STRANDED
    assert res.heavy_token_id == "tok-up"
    assert res.light_token_id == "tok-dn"
    assert res.held_deficit == 10.0
    assert res.coverage_shortfall == 10.0


def test_patient_wait_never_cancels():
    """#628: PATIENT_WAIT policy never emits a cancel action."""
    decision = transition(
        _position(up_size=2.0, down_size=0.0, up_avg=0.48, up_bid=0.47),
        LegState.PATIENT_WAIT,
        bot_pos_price_max_pair_cost_usd=0.99,
    )
    assert decision.state is LegState.PATIENT_WAIT
    assert decision.action == "wait"
    assert decision.action != "cancel"


def test_escalated_hedge_never_cancels():
    """#628: ESCALATED_HEDGE policy emits hedge quote, never cancel."""
    decision = transition(
        _position(up_size=2.0, down_size=0.0, up_avg=0.48, up_bid=0.39, base_offset=0.04),
        LegState.ESCALATED_HEDGE,
        bot_pos_price_max_pair_cost_usd=0.99,
    )
    assert decision.state is LegState.ESCALATED_HEDGE
    assert decision.action == "hedge"
    assert decision.action != "cancel"



def test_saver_action_for_maps_decision_to_row_action():
    from core_brain.single_leg_lifecycle import saver_action_for
    assert saver_action_for("wait") == "patient_wait"
    assert saver_action_for("hedge") == "escalated_wait"
    assert saver_action_for("locked") == "pair_locked"
    assert saver_action_for("rest") == "dual_resting"
    assert saver_action_for("refused") == "refused"


def test_saver_action_for_rejects_unreachable_actions():
    # "hold" is a saver-side row from another path, never a decision action;
    # the old `.get(action, action)` pass-through would have echoed these.
    import pytest
    from core_brain.single_leg_lifecycle import saver_action_for
    for unknown in ("bogus", "hold", "hard_stop", "settlement_fallback"):
        with pytest.raises(KeyError):
            saver_action_for(unknown)
