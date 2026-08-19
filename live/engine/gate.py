"""Quality gate: widen before exiting.

A market priced one cent too aggressively and a market full of informed flow
look identical on a single reading. Only the second stays negative after we
back off, and only the second is worth giving up the rent for -- so the
response is graduated rather than a single kill switch.

Widening keeps us inside the 4.5c reward window, which means a WIDENED market
still earns. EXITED is the only state that forfeits income, and it is reached
only after the market has been given a second full sample to recover.
"""
from __future__ import annotations

NORMAL, WIDENED, EXITED = "NORMAL", "WIDENED", "EXITED"

# FLEET POSTURE only. Never a value of a market's own `gate_state`: the state
# machine below is a statement about ONE market and HALTED is a statement about
# the universe, so mixing them would let pooled evidence sentence a market that
# was never measured -- the exact thing `_gate_with_fleet_fallback` caps at
# WIDENED to prevent.
HALTED = "HALTED"


def offset_for(state: str, base: float, widened: float) -> float:
    """How far under mid to quote, given the market's gate state."""
    return widened if state == WIDENED else base


def fleet_posture(pooled: dict, cfg) -> str:
    """How hard the whole fleet should be leaning on the brakes, from the pool.

    A pure function of ONE reading, with no previous posture argument, and that
    is the design rather than an omission. Everything below is a posture the
    fleet ADOPTS while a reading holds, not a sentence it serves: nothing is
    remembered, nothing is persisted, and a recovered pool lifts the halt on
    the next sweep with no re-entry rule to satisfy. Contrast `next_state`
    below, where EXITED is terminal precisely because it is a judgement about a
    market rather than a description of the moment.

    Three readings, on the same two thresholds the per-market machine uses, so
    "catastrophic" means one thing in this codebase:

      * `insufficient_sample`, or no mean at all -> NORMAL. Identical noise
        guard to `next_state`, and it matters MORE here: a per-market verdict
        gates one book, a posture gates every book at once, so acting on thin
        evidence is the more expensive mistake by the size of the fleet.
      * Past `markout_catastrophic_threshold` -> HALTED. Pooled across every
        market, a mean that deep is not one book priced a cent wrong; it is
        the whole universe selling to us on better information. Measured
        2026-08-02 the pool read -4.75c/share while every market individually
        sat at WIDENED, because none of them ever reached a sample of its own.
      * Past `markout_widen_threshold` -> WIDENED. A loss a wider quote can
        still trade its way out of, inside the 4.5c window, so the rent keeps
        coming while we back off.

    The caller decides what a posture DOES. `quotes._decide_quotes_rewards`
    reads HALTED as "no additions to the heavy side anywhere", and deliberately
    leaves the light side, the merge and the emergency exit alone -- a brake
    that also blocked the orders which flatten a position would freeze the
    fleet at maximum exposure, which is the failure the share cap already
    produced once.
    """
    if pooled.get("verdict") == "insufficient_sample":
        return NORMAL
    mean = pooled.get("mean_per_share")
    if mean is None:
        return NORMAL
    if mean < cfg.markout_catastrophic_threshold:
        return HALTED
    if mean < cfg.markout_widen_threshold:
        return WIDENED
    return NORMAL


def next_state(state: str, stats: dict, cfg) -> str:
    """Advance the state machine on one markout reading.

    Deliberately conservative in two places:

      * `insufficient_sample` never moves the state. On a thin, long-dated
        book a handful of fills is noise, and evicting a sound market on noise
        costs real rent for no reason.
      * Leaving WIDENED for EXITED demands twice `markout_min_sample`. One
        sample got us into WIDENED; surrendering the income needs more
        evidence than that.

    Both concessions are arguments about SMALL losses, and neither survives a
    catastrophic one -- see the magnitude bypass below.

    EXITED is terminal. A market that kept picking us off after we had already
    backed off has earned a permanent seat out, and re-entering on a noisy
    recovery reading is how a gate turns into an oscillator.
    """
    if state == EXITED:
        return EXITED
    if stats.get("verdict") == "insufficient_sample":
        return state
    mean = stats.get("mean_per_share")
    if mean is None:
        return state

    # MAGNITUDE BYPASS. Graduation is a response to AMBIGUITY: a small negative
    # markout could be one cent of mispricing, so we widen and look again. At
    # -2c/share there is no ambiguity left to resolve -- four times the widen
    # threshold and more than a full taker fee, a loss no offset inside the
    # 4.5c reward window can quote its way out of. Both concessions above then
    # become actively expensive: WIDENED keeps us in the book, and the second
    # full sample the WIDENED->EXITED rule demands is another
    # `markout_min_sample` fills bought at that rate. Exit now, from whichever
    # state we are in, skipping WIDENED entirely.
    #
    # This bypasses the sample DOUBLING, not the sample MINIMUM: the
    # insufficient_sample guard above still stands, so a handful of bad fills
    # on a thin book cannot trigger it.
    if mean < cfg.markout_catastrophic_threshold:
        return EXITED

    losing = mean < cfg.markout_widen_threshold
    if state == NORMAL:
        return WIDENED if losing else NORMAL
    if losing and stats.get("n", 0) >= 2 * cfg.markout_min_sample:
        return EXITED
    return NORMAL if not losing else WIDENED
