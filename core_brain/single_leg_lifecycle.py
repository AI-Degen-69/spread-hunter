"""Pure, per-pair policy for managing one-sided fills."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR
from enum import Enum
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core_brain.order_registry import OrderRegistry

SIZE_EPS = 1e-6


class LegState(str, Enum):
    DUAL_RESTING = "DUAL_RESTING"
    PATIENT_WAIT = "PATIENT_WAIT"
    ESCALATED_HEDGE = "ESCALATED_HEDGE"
    HARD_STOP = "HARD_STOP"
    PAIR_LOCKED = "PAIR_LOCKED"


@dataclass(frozen=True)
class SingleLegPosition:
    pair_id: str
    condition_id: str
    up_token_id: str
    down_token_id: str
    up_size: float
    down_size: float
    up_avg_price: float
    down_avg_price: float
    up_best_bid: float | None
    down_best_bid: float | None
    base_offset: float


@dataclass(frozen=True)
class LifecycleDecision:
    state: LegState
    action: str
    held_token_id: str | None = None
    held_average_price: float | None = None
    naked_size: float = 0.0
    opposite_token_id: str | None = None
    opposite_limit_price: float | None = None
    reason: str = ""


@dataclass(frozen=True)
class LifecycleQuoteOverride:
    pair_id: str
    token_id: str
    price: float
    size: int
    state: LegState
    held_average_price: float


@dataclass(frozen=True)
class LifecycleQuoteContext:
    override: LifecycleQuoteOverride | None = None
    refusal_reason: str | None = None
    preserve_order_ids: frozenset[str] = frozenset()
    replace_order_ids: frozenset[str] = frozenset()
    cancel_order_ids: frozenset[str] = frozenset()
    lifecycle_pair_id: str | None = None


def max_profitable_hedge_bid(
    held_average: float, max_pair_cost: float, tick_size: float,
) -> float:
    """Return the highest tick-aligned bid that cannot exceed the pair cap."""
    values = (held_average, max_pair_cost, tick_size)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("held average, pair cap, and tick size must be finite")
    if held_average <= 0 or held_average >= 1:
        raise ValueError("held average must be between zero and one")
    if max_pair_cost <= 0:
        raise ValueError("pair cap must be positive")
    if tick_size <= 0:
        raise ValueError("tick size must be positive")

    cap = min(0.99, max_pair_cost)
    cap_decimal = Decimal(str(cap))
    held_decimal = Decimal(str(held_average))
    tick_decimal = Decimal(str(tick_size))
    raw_bid = cap_decimal - held_decimal
    ticks = (raw_bid / tick_decimal).to_integral_value(rounding=ROUND_FLOOR)
    bid_decimal = ticks * tick_decimal
    if bid_decimal <= 0 or held_decimal + bid_decimal > cap_decimal:
        raise ValueError("no positive tick-aligned hedge bid fits the pair cap")
    return float(bid_decimal)


def transition(
    position: SingleLegPosition,
    previous_state: LegState | None,
    *,
    max_pair_cost: float,
    settlement_due: bool = False,
    hard_stop_bid: float = 0.15,
    escalation_multiple: float = 2.0,
    tick_size: float = 0.01,
) -> LifecycleDecision:
    """Evaluate one pair without performing venue or registry operations."""
    cap_value = float(max_pair_cost)
    if not math.isfinite(cap_value) or cap_value <= 0:
        return _refused(previous_state, "pair cap must be finite and positive")
    if not math.isfinite(hard_stop_bid) or not 0 <= hard_stop_bid <= 1:
        return _refused(previous_state, "hard-stop bid must be between zero and one")
    if not math.isfinite(escalation_multiple) or escalation_multiple <= 0:
        return _refused(previous_state, "escalation multiple must be finite and positive")
    if not math.isfinite(tick_size) or tick_size <= 0:
        return _refused(previous_state, "tick size must be finite and positive")
    if not position.pair_id or not position.condition_id:
        return _refused(previous_state, "pair and condition identifiers are required")
    if not position.up_token_id or not position.down_token_id:
        return _refused(previous_state, "both token identifiers are required")

    observed = (
        position.up_size,
        position.down_size,
        position.up_avg_price,
        position.down_avg_price,
        position.base_offset,
    )
    if not all(math.isfinite(value) for value in observed):
        return _refused(previous_state, "position sizes, prices, and offset must be finite")
    if (position.up_size < 0 or position.down_size < 0
            or not 0 <= position.up_avg_price <= 1
            or not 0 <= position.down_avg_price <= 1
            or position.base_offset <= 0):
        return _refused(previous_state, "position sizes, prices, or offset are invalid")
    for bid in (position.up_best_bid, position.down_best_bid):
        if bid is not None and (not math.isfinite(bid) or not 0 <= bid <= 1):
            return _refused(previous_state, "observed best bid is invalid")

    cap = min(0.99, cap_value)
    residual = position.up_size - position.down_size
    if abs(residual) <= SIZE_EPS:
        if position.up_size <= SIZE_EPS and position.down_size <= SIZE_EPS:
            return LifecycleDecision(
                state=LegState.DUAL_RESTING,
                action="rest",
                reason="no filled exposure",
            )
        pair_cost = Decimal(str(position.up_avg_price)) + Decimal(
            str(position.down_avg_price)
        )
        if pair_cost <= Decimal(str(cap)):
            return LifecycleDecision(
                state=LegState.PAIR_LOCKED,
                action="locked",
                reason=f"balanced pair cost {pair_cost} is within cap {cap:.4f}",
            )
        return LifecycleDecision(
            state=LegState.DUAL_RESTING,
            action="refused",
            reason=f"balanced pair cost {pair_cost} exceeds cap {cap:.4f}",
        )

    if residual > 0:
        held_token = position.up_token_id
        opposite_token = position.down_token_id
        held_average = position.up_avg_price
        held_bid = position.up_best_bid
    else:
        held_token = position.down_token_id
        opposite_token = position.up_token_id
        held_average = position.down_avg_price
        held_bid = position.down_best_bid
    naked_size = abs(residual)
    if held_average <= 0:
        return _refused(
            previous_state,
            "held leg has no positive average fill price",
            held_token_id=held_token,
            naked_size=naked_size,
        )

    details = {
        "held_token_id": held_token,
        "held_average_price": held_average,
        "naked_size": naked_size,
        "opposite_token_id": opposite_token,
    }
    if held_bid is not None and held_bid <= hard_stop_bid:
        return LifecycleDecision(
            state=LegState.HARD_STOP,
            action="hard_stop",
            reason=f"held bid {held_bid:.4f} is at or below {hard_stop_bid:.4f}",
            **details,
        )

    if settlement_due:
        state = (
            LegState.ESCALATED_HEDGE
            if previous_state is LegState.ESCALATED_HEDGE
            else LegState.PATIENT_WAIT
        )
        return LifecycleDecision(
            state=state,
            action="settlement_fallback",
            reason="settlement fallback is due",
            **details,
        )

    sticky = previous_state is LegState.ESCALATED_HEDGE
    drawdown = (
        held_average - held_bid
        if held_bid is not None
        else None
    )
    threshold_crossed = (
        drawdown is not None
        and (
            Decimal(str(held_average)) - Decimal(str(held_bid))
            >= Decimal(str(escalation_multiple))
            * Decimal(str(position.base_offset))
        )
    )
    if sticky or threshold_crossed:
        try:
            limit_price = max_profitable_hedge_bid(
                held_average, cap, tick_size,
            )
        except ValueError as exc:
            return LifecycleDecision(
                state=LegState.ESCALATED_HEDGE,
                action="refused",
                reason=f"cannot create capped escalation bid: {exc}",
                **details,
            )
        return LifecycleDecision(
            state=LegState.ESCALATED_HEDGE,
            action="hedge",
            opposite_limit_price=limit_price,
            reason=(
                "escalation remains sticky"
                if sticky
                else f"drawdown {drawdown:.4f} crossed "
                f"{escalation_multiple * position.base_offset:.4f}"
            ),
            **details,
        )

    return LifecycleDecision(
        state=LegState.PATIENT_WAIT,
        action="wait",
        reason="held leg has not crossed the escalation threshold",
        **details,
    )


def persist_decision(
    registry: OrderRegistry,
    position: SingleLegPosition,
    decision: LifecycleDecision,
    previous_state: LegState | None = None,
) -> None:
    """Persist one lifecycle decision, building the evidence snapshot.

    Callers that own a decision (the Trader for every change, the poll
    loop only for hard-stop and settlement-fallback) write it here so state
    survives restarts and is observable.
    """
    from core_brain.order_registry import LifecycleStateRecord

    evidence = {
        "previous_state": previous_state.value if previous_state else None,
        "state": decision.state.value,
        "action": decision.action,
        "held_token_id": decision.held_token_id,
        "held_average_price": decision.held_average_price,
        "held_best_bid": (
            position.up_best_bid
            if decision.held_token_id == position.up_token_id
            else position.down_best_bid
        ),
        "naked_size": decision.naked_size,
        "opposite_token_id": decision.opposite_token_id,
        "opposite_limit_price": decision.opposite_limit_price,
        "up_size": position.up_size,
        "down_size": position.down_size,
        "up_average_price": position.up_avg_price,
        "down_average_price": position.down_avg_price,
        "base_offset": position.base_offset,
        "reason": decision.reason,
    }
    registry.save_lifecycle_state(
        LifecycleStateRecord(
            pair_id=position.pair_id,
            condition_id=position.condition_id,
            state=decision.state.value,
            reason=decision.reason,
            updated_ts_ms=int(time.time() * 1000),
            evidence_json=json.dumps(
                evidence, sort_keys=True, separators=(",", ":"), allow_nan=False,
            ),
        )
    )


def evaluate(
    position: SingleLegPosition,
    registry: OrderRegistry,
    *,
    max_pair_cost: float,
    settlement_due: bool = False,
    tick_size: float = 0.01,
) -> LifecycleDecision:
    """Load sticky state, decide for one pair, and persist state changes."""
    previous_record = registry.get_lifecycle_state(position.pair_id)
    previous_state = None
    if previous_record is not None:
        if previous_record.condition_id != position.condition_id:
            raise ValueError(
                f"lifecycle pair {position.pair_id!r} belongs to condition "
                f"{previous_record.condition_id!r}, not {position.condition_id!r}"
            )
        previous_state = LegState(previous_record.state)

    decision = transition(
        position,
        previous_state,
        max_pair_cost=max_pair_cost,
        settlement_due=settlement_due,
        tick_size=tick_size,
    )
    if decision.action == "refused" or decision.state is previous_state:
        return decision

    persist_decision(registry, position, decision, previous_state)
    return decision


def _refused(
    previous_state: LegState | None,
    reason: str,
    *,
    held_token_id: str | None = None,
    naked_size: float = 0.0,
) -> LifecycleDecision:
    return LifecycleDecision(
        state=previous_state or LegState.DUAL_RESTING,
        action="refused",
        held_token_id=held_token_id,
        naked_size=naked_size,
        reason=reason,
    )
