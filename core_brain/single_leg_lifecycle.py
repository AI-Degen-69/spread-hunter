"""Pure, per-pair policy for managing one-sided fills."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from enum import Enum
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core_brain.order_registry import OrderRegistry

SIZE_EPS = 1e-6

# The dynamic stop modes (#619). Exactly one is selected per run: `NONE` leaves
# the held leg to the emergency floor alone. The two drop modes are mutually
# exclusive by construction -- one field selects one formula. The spec spells
# them `ABSOLUTE`/`PERCENTAGE` in its formulas and `ABSOLUTE_DROP`/
# `PERCENTAGE_DROP` in its mode list; only the `_DROP` spellings are accepted,
# because the retired `usd`/`pct` knobs they replace counted in different units
# and a silent alias would run an operator's number in the wrong scale.
STOP_LOSS_MODES = frozenset({"NONE", "ABSOLUTE_DROP", "PERCENTAGE_DROP"})

# `bot_risk_stop_adverse_drop_cents` is a price drop in DOLLARS per share, bounded to the contract.
# The emergency drawdown is a percentage in the open interval (0, 100].
DROP_CENTS_MIN = 0.01
DROP_CENTS_MAX = 0.90
# No stop can ever trigger below one cent: the venue's smallest tick, and the
# only price at which an exit is still an exit rather than a donation.
EMERGENCY_FLOOR_MIN = 0.01


class LegState(str, Enum):
    DUAL_RESTING = "DUAL_RESTING"
    PATIENT_WAIT = "PATIENT_WAIT"
    ESCALATED_HEDGE = "ESCALATED_HEDGE"
    HARD_STOP = "HARD_STOP"
    PAIR_LOCKED = "PAIR_LOCKED"


class ExposureState(str, Enum):
    BALANCED = "balanced"
    COVERED = "covered"
    UNDER_COVERED = "under_covered"
    STRANDED = "stranded"


@dataclass(frozen=True)
class ConditionExposure:
    condition_id: str
    heavy_token_id: str | None
    light_token_id: str | None
    held_deficit: float
    coverage_shortfall: float
    over_coverage: float
    status: ExposureState


def evaluate_exposure(
    condition_id: str,
    held_shares_by_token: dict[str, float],
    working_buy_shares_by_token: dict[str, float],
) -> ConditionExposure:
    """Evaluate held inventory vs working buy orders on a binary condition.

    Calculates net exposure, heavy/light tokens, deficit, coverage shortfall,
    and returns one of ExposureState (balanced, covered, under_covered, stranded).
    Uses SIZE_EPS for all comparisons.
    """
    all_tokens = list(set(held_shares_by_token.keys()) | set(working_buy_shares_by_token.keys()))
    if len(all_tokens) == 0:
        return ConditionExposure(
            condition_id=condition_id,
            heavy_token_id=None,
            light_token_id=None,
            held_deficit=0.0,
            coverage_shortfall=0.0,
            over_coverage=0.0,
            status=ExposureState.BALANCED,
        )

    if len(all_tokens) == 1:
        t0 = all_tokens[0]
        h0 = held_shares_by_token.get(t0, 0.0)
        w0 = working_buy_shares_by_token.get(t0, 0.0)
        if h0 <= SIZE_EPS:
            return ConditionExposure(
                condition_id=condition_id,
                heavy_token_id=None,
                light_token_id=None,
                held_deficit=0.0,
                coverage_shortfall=0.0,
                over_coverage=0.0,
                status=ExposureState.BALANCED,
            )
        return ConditionExposure(
            condition_id=condition_id,
            heavy_token_id=t0,
            light_token_id=None,
            held_deficit=h0,
            coverage_shortfall=h0,
            over_coverage=0.0,
            status=ExposureState.STRANDED,
        )

    t1, t2 = all_tokens[0], all_tokens[1]
    h1 = held_shares_by_token.get(t1, 0.0)
    h2 = held_shares_by_token.get(t2, 0.0)

    if abs(h1 - h2) <= SIZE_EPS:
        return ConditionExposure(
            condition_id=condition_id,
            heavy_token_id=None,
            light_token_id=None,
            held_deficit=0.0,
            coverage_shortfall=0.0,
            over_coverage=0.0,
            status=ExposureState.BALANCED,
        )

    if h1 > h2:
        heavy_token = t1
        light_token = t2
        deficit = h1 - h2
    else:
        heavy_token = t2
        light_token = t1
        deficit = h2 - h1

    working_hedge = working_buy_shares_by_token.get(light_token, 0.0)

    if working_hedge <= SIZE_EPS:
        return ConditionExposure(
            condition_id=condition_id,
            heavy_token_id=heavy_token,
            light_token_id=light_token,
            held_deficit=deficit,
            coverage_shortfall=deficit,
            over_coverage=0.0,
            status=ExposureState.STRANDED,
        )

    if working_hedge + SIZE_EPS >= deficit:
        return ConditionExposure(
            condition_id=condition_id,
            heavy_token_id=heavy_token,
            light_token_id=light_token,
            held_deficit=deficit,
            coverage_shortfall=0.0,
            over_coverage=max(0.0, working_hedge - deficit),
            status=ExposureState.COVERED,
        )

    return ConditionExposure(
        condition_id=condition_id,
        heavy_token_id=heavy_token,
        light_token_id=light_token,
        held_deficit=deficit,
        coverage_shortfall=max(0.0, deficit - working_hedge),
        over_coverage=0.0,
        status=ExposureState.UNDER_COVERED,
    )



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
    # Registry lifecycle state driving this context (#416: ESCALATED_HEDGE /
    # HARD_STOP), so placement gating can verify protection against the
    # registry state rather than an inferred flag. None on the dual-resting
    # path, which keeps full gate behavior.
    lifecycle_state: str | None = None
    shortfall: float = 0.0


def max_profitable_hedge_bid(
    held_average: float, bot_pos_price_max_pair_cost_usd: float, tick_size: float,
) -> float:
    """Return the highest tick-aligned bid that cannot exceed the pair cap."""
    values = (held_average, bot_pos_price_max_pair_cost_usd, tick_size)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("held average, pair cap, and tick size must be finite")
    if held_average <= 0 or held_average >= 1:
        raise ValueError("held average must be between zero and one")
    if bot_pos_price_max_pair_cost_usd <= 0:
        raise ValueError("pair cap must be positive")
    if tick_size <= 0:
        raise ValueError("tick size must be positive")

    cap = min(0.99, bot_pos_price_max_pair_cost_usd)
    cap_decimal = Decimal(str(cap))
    held_decimal = Decimal(str(held_average))
    tick_decimal = Decimal(str(tick_size))
    raw_bid = cap_decimal - held_decimal
    ticks = (raw_bid / tick_decimal).to_integral_value(rounding=ROUND_FLOOR)
    bid_decimal = ticks * tick_decimal
    if bid_decimal <= 0 or held_decimal + bid_decimal > cap_decimal:
        raise ValueError("no positive tick-aligned hedge bid fits the pair cap")
    return float(bid_decimal)


def capped_hedge_tick_exists(
    held_average: float, bot_pos_price_max_pair_cost_usd: float, tick_size: float,
) -> bool:
    """Whether a capped, tick-aligned hedge bid exists, without pricing it.

    The saver's stranded-exposure probe asks this state-independent question:
    a tick can exist before the drawdown crosses the escalation threshold, so
    the answer must not depend on the current lifecycle state. One predicate,
    one module -- callers never re-derive it.
    """
    try:
        max_profitable_hedge_bid(
            held_average, bot_pos_price_max_pair_cost_usd, tick_size,
        )
    except ValueError:
        return False
    return True


def validate_escalation_override(
    override: LifecycleQuoteOverride, *, cap: float, tick_size: float,
) -> str | None:
    """Refusal reason when an escalated-hedge override is unusable, else None.

    One owner for what counts as a valid escalation: the Trader produces the
    override through `transition`, the quoting path enforces this check, and
    both read the same rule. A mismatch between the override price and the
    recomputed maximum profitable bid refuses -- the guard against quoting a
    hedge that cannot merge at a profit.
    """
    if override.state is not LegState.ESCALATED_HEDGE:
        return "lifecycle quote override is not an escalated hedge"
    if not isinstance(override.pair_id, str) or not override.pair_id.strip():
        return "lifecycle escalation requires a pair ID"
    if not isinstance(override.token_id, str) or not override.token_id:
        return "lifecycle escalation requires a token ID"
    if (not isinstance(override.price, (int, float))
            or isinstance(override.price, bool)
            or not math.isfinite(override.price)
            or not 0 < override.price < 1):
        return "lifecycle escalation price must be finite and between zero and one"
    if (not isinstance(override.held_average_price, (int, float))
            or isinstance(override.held_average_price, bool)
            or not math.isfinite(override.held_average_price)
            or not 0 < override.held_average_price < 1):
        return "lifecycle held-leg average must be finite and between zero and one"
    if (not isinstance(override.size, int) or isinstance(override.size, bool)
            or override.size <= 0):
        return "lifecycle escalation size must be a positive integer"

    if not math.isfinite(cap) or cap <= 0:
        return "lifecycle pair cap must be finite and positive"
    cap_dec = min(Decimal("0.99"), Decimal(str(cap)))
    pair_cost = (
        Decimal(str(override.held_average_price))
        + Decimal(str(override.price))
    )
    if pair_cost > cap_dec:
        return (
            f"lifecycle pair cost ${pair_cost:.4f} exceeds ${cap_dec:.3f} cap")
    try:
        maximum_bid = max_profitable_hedge_bid(
            override.held_average_price,
            cap,
            tick_size,
        )
    except ValueError as exc:
        return f"lifecycle maximum profitable bid unavailable: {exc}"
    if Decimal(str(override.price)) != Decimal(str(maximum_bid)):
        return (
            f"lifecycle bid ${override.price:.4f} does not match the "
            f"maximum profitable bid ${maximum_bid:.4f}")
    return None


def hedge_unreachable_at_cap(
    held_average: float,
    opposing_best_ask: float | None,
    bot_pos_price_max_pair_cost_usd: float,
    tick_size: float,
) -> bool:
    """True when the pair cap cannot reach the opposing side's best ask (#797).

    A hedge that fits the cap but is merely wide -- the cap's best bid at or
    above the ask -- is still reachable and must keep waiting; only a market
    that has walked away from the cap is unreachable. The cap keeps its own
    tick of slack: an ask a single tick above the best bid the cap allows is
    the escalated hedge's ordinary case, and dumping the leg there would exit
    on a one-tick spread rather than on a market that left the cap behind. An
    unknown ask, an unreadable price and a non-positive ask are all "unknown",
    and unknown never sells a leg.
    """
    if opposing_best_ask is None:
        return False
    try:
        ask = float(opposing_best_ask)
    except (TypeError, ValueError):
        return False
    # Above 1.0 is not a price in a binary market, so it is a bad read, not an
    # unreachable hedge: selling a leg on corrupt data is the one thing the
    # exit must never do.
    if not math.isfinite(ask) or ask <= 0 or ask > 1:
        return False
    try:
        held = float(held_average)
        cap = float(bot_pos_price_max_pair_cost_usd)
        tick = float(tick_size)
    except (TypeError, ValueError):
        return False
    # Unusable inputs are unknown, not unreachable. Validating them here leaves
    # exactly one failure inside `max_profitable_hedge_bid` below.
    if (not math.isfinite(held) or not 0 < held < 1
            or not math.isfinite(cap) or cap <= 0
            or not math.isfinite(tick) or tick <= 0):
        return False
    try:
        cap_bid = max_profitable_hedge_bid(held, cap, tick)
    except ValueError:
        # No positive tick-aligned bid fits under the cap at all, so the hedge
        # cannot be reached however the market moves.
        return True
    return ask > cap_bid + tick


def _validated_stop_knobs(
    mode: str,
    bot_risk_stop_adverse_drop_cents: float,
    bot_risk_stop_max_drawdown_pct: float,
    bot_risk_circuit_max_drawdown_pct: float,
) -> tuple[str, Decimal, Decimal, Decimal]:
    """The canonical mode and its three knobs, or a ValueError.

    The bounded inputs are contract, not preference: an out-of-range knob is
    refused rather than clamped, because a silently clamped stop is a stop the
    operator believes is armed and is not. `mode` is trimmed and upper-cased
    so the environment's spelling and the code's agree.
    """
    candidate = str(mode).strip().upper()
    if candidate not in STOP_LOSS_MODES:
        choices = ", ".join(sorted(STOP_LOSS_MODES))
        raise ValueError(
            f"bot_risk_stop_mode must be one of {choices}, got {mode!r}"
        )
    drop_value = float(bot_risk_stop_adverse_drop_cents)
    if (not math.isfinite(drop_value)
            or not DROP_CENTS_MIN <= drop_value <= DROP_CENTS_MAX):
        raise ValueError(
            f"bot_risk_stop_adverse_drop_cents must be between {DROP_CENTS_MIN} and "
            f"{DROP_CENTS_MAX}, got {bot_risk_stop_adverse_drop_cents!r}"
        )
    drawdown_value = float(bot_risk_stop_max_drawdown_pct)
    if not math.isfinite(drawdown_value) or not 0 < drawdown_value <= 100:
        raise ValueError(
            f"bot_risk_stop_max_drawdown_pct must be above zero and at most 100, "
            f"got {bot_risk_stop_max_drawdown_pct!r}"
        )
    emergency_value = float(bot_risk_circuit_max_drawdown_pct)
    if not math.isfinite(emergency_value) or not 0 < emergency_value <= 100:
        raise ValueError(
            f"bot_risk_circuit_max_drawdown_pct must be above zero and at most 100, "
            f"got {bot_risk_circuit_max_drawdown_pct!r}"
        )
    return (
        candidate,
        Decimal(str(drop_value)),
        Decimal(str(drawdown_value)),
        Decimal(str(emergency_value)),
    )


def stop_trigger(
    entry: float,
    mode: str,
    bot_risk_stop_adverse_drop_cents: float,
    bot_risk_stop_max_drawdown_pct: float,
    bot_risk_circuit_max_drawdown_pct: float,
) -> tuple[float, str]:
    """The bid at which a held leg must be sold, and which rule set it.

    The strategy trigger is the selected mode's formula off the leg's own
    average fill; the emergency floor is the universal backstop every mode
    shares, floored at one cent so an exit is never priced at nothing. The
    active trigger is the higher of the two, and `source` names which one it
    was -- so a booked loss can be attributed to the mode the operator chose or
    to the catastrophe backstop.

    Any trigger with a sub-cent fraction rounds UP to the next whole cent
    (12.5c becomes 13c): the venue cannot price below its tick, and the bias
    fires the exit a fraction earlier rather than later.

    `NONE` has no strategy trigger at all, so the floor is the whole rule. This
    is what replaces the static $0.15 stop: active quotes span $0.10-$0.90, and
    a fixed bid at the bottom of that range liquidated every low entry the
    moment it filled.
    """
    entry_value = float(entry)
    if not math.isfinite(entry_value) or not 0 < entry_value <= 1:
        raise ValueError(
            f"entry must be between zero and one, got {entry!r}"
        )
    normalised, drop_value, drawdown_value, emergency_value = (
        _validated_stop_knobs(mode, bot_risk_stop_adverse_drop_cents, bot_risk_stop_max_drawdown_pct,
                              bot_risk_circuit_max_drawdown_pct)
    )

    entry_decimal = Decimal(str(entry_value))
    hundred = Decimal(100)
    cent = Decimal("0.01")
    emergency = entry_decimal * (Decimal(1) - emergency_value / hundred)
    emergency = max(emergency, Decimal(str(EMERGENCY_FLOOR_MIN)))
    emergency = emergency.quantize(cent, rounding=ROUND_CEILING)

    if normalised == "ABSOLUTE_DROP":
        strategy: Decimal | None = (entry_decimal - drop_value).quantize(
            cent, rounding=ROUND_CEILING)
    elif normalised == "PERCENTAGE_DROP":
        strategy = (entry_decimal * (Decimal(1) - drawdown_value / hundred)).quantize(
            cent, rounding=ROUND_CEILING)
    else:
        strategy = None

    if strategy is None or strategy <= emergency:
        return float(emergency), "emergency"
    return float(strategy), "strategy"


def transition(
    position: SingleLegPosition,
    previous_state: LegState | None,
    *,
    bot_pos_price_max_pair_cost_usd: float,
    settlement_due: bool = False,
    bot_risk_stop_mode: str = "NONE",
    bot_risk_stop_adverse_drop_cents: float = 0.05,
    bot_risk_stop_max_drawdown_pct: float = 10.0,
    bot_risk_circuit_max_drawdown_pct: float = 75.0,
    escalation_multiple: float = 2.0,
    tick_size: float = 0.01,
) -> LifecycleDecision:
    """Evaluate one pair without performing venue or registry operations."""
    cap_value = float(bot_pos_price_max_pair_cost_usd)
    if not math.isfinite(cap_value) or cap_value <= 0:
        return _refused(previous_state, "pair cap must be finite and positive")
    try:
        _validated_stop_knobs(
            bot_risk_stop_mode, bot_risk_stop_adverse_drop_cents, bot_risk_stop_max_drawdown_pct, bot_risk_circuit_max_drawdown_pct,
        )
    except ValueError as exc:
        return _refused(previous_state, str(exc))
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
    active_trigger, trigger_source = stop_trigger(
        held_average, bot_risk_stop_mode, bot_risk_stop_adverse_drop_cents, bot_risk_stop_max_drawdown_pct,
        bot_risk_circuit_max_drawdown_pct,
    )
    if held_bid is not None and held_bid <= active_trigger:
        return LifecycleDecision(
            state=LegState.HARD_STOP,
            action="hard_stop",
            reason=(
                f"held bid {held_bid:.4f} is at or below the "
                f"{trigger_source} trigger {active_trigger:.4f}"
            ),
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
    bot_pos_price_max_pair_cost_usd: float,
    settlement_due: bool = False,
    bot_risk_stop_mode: str = "NONE",
    bot_risk_stop_adverse_drop_cents: float = 0.05,
    bot_risk_stop_max_drawdown_pct: float = 10.0,
    bot_risk_circuit_max_drawdown_pct: float = 75.0,
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
        bot_pos_price_max_pair_cost_usd=bot_pos_price_max_pair_cost_usd,
        settlement_due=settlement_due,
        bot_risk_stop_mode=bot_risk_stop_mode,
        bot_risk_stop_adverse_drop_cents=bot_risk_stop_adverse_drop_cents,
        bot_risk_stop_max_drawdown_pct=bot_risk_stop_max_drawdown_pct,
        bot_risk_circuit_max_drawdown_pct=bot_risk_circuit_max_drawdown_pct,
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

@dataclass(frozen=True)
class OrphanProfitVerdict:
    eligible: bool
    reason: str
    held_token_id: str | None = None
    held_size: float = 0.0
    average_cost: float = 0.0
    profit_floor: float = 0.0
    observed_bid: float | None = None


def orphan_profit_floor(average_cost: float, min_profit_pct: float, tick_size: float = 0.01) -> float:
    """Calculate the lowest tick price >= average_cost * (1 + min_profit_pct) and > average_cost."""
    if average_cost <= 0:
        return 0.0
    cost_dec = Decimal(str(average_cost))
    pct_dec = Decimal(str(max(0.0, float(min_profit_pct))))
    tick_dec = Decimal(str(tick_size if tick_size > 0 else 0.01))
    raw_target = cost_dec * (Decimal("1") + pct_dec)
    ticks = math.ceil(float(raw_target / tick_dec - Decimal("1e-9")))
    floor_dec = Decimal(ticks) * tick_dec
    if floor_dec <= cost_dec:
        floor_dec += tick_dec
    return float(floor_dec)


def evaluate_orphan_profit(
    position: SingleLegPosition,
    age_s: float | None,
    min_age_sec: float,
    min_profit_pct: float,
    tick_size: float = 0.01,
) -> OrphanProfitVerdict:
    """Determine whether an orphaned single-leg position is eligible to close for profit.

    Pure: no registry, no clock, no network.
    """
    up_held = position.up_size > SIZE_EPS
    dn_held = position.down_size > SIZE_EPS

    if up_held and dn_held:
        return OrphanProfitVerdict(eligible=False, reason="opposite_leg_filled")
    if not up_held and not dn_held:
        return OrphanProfitVerdict(eligible=False, reason="flat")

    if up_held:
        held_token_id = position.up_token_id
        held_size = position.up_size
        average_cost = position.up_avg_price
        observed_bid = position.up_best_bid
    else:
        held_token_id = position.down_token_id
        held_size = position.down_size
        average_cost = position.down_avg_price
        observed_bid = position.down_best_bid

    if average_cost <= 0.0:
        return OrphanProfitVerdict(
            eligible=False, reason="no_cost",
            held_token_id=held_token_id, held_size=held_size,
            average_cost=average_cost, observed_bid=observed_bid,
        )

    if observed_bid is None or observed_bid <= 0.0:
        return OrphanProfitVerdict(
            eligible=False, reason="no_bid",
            held_token_id=held_token_id, held_size=held_size,
            average_cost=average_cost, observed_bid=observed_bid,
        )

    if age_s is None:
        return OrphanProfitVerdict(
            eligible=False, reason="age_unknown",
            held_token_id=held_token_id, held_size=held_size,
            average_cost=average_cost, observed_bid=observed_bid,
        )

    if age_s <= min_age_sec:
        return OrphanProfitVerdict(
            eligible=False, reason="too_young",
            held_token_id=held_token_id, held_size=held_size,
            average_cost=average_cost, observed_bid=observed_bid,
        )

    floor = orphan_profit_floor(average_cost, min_profit_pct, tick_size)
    if observed_bid < floor - 1e-9:
        return OrphanProfitVerdict(
            eligible=False, reason="below_floor",
            held_token_id=held_token_id, held_size=held_size,
            average_cost=average_cost, profit_floor=floor,
            observed_bid=observed_bid,
        )

    return OrphanProfitVerdict(
        eligible=True, reason="eligible",
        held_token_id=held_token_id, held_size=held_size,
        average_cost=average_cost, profit_floor=floor,
        observed_bid=observed_bid,
    )

