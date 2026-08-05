"""Quality gate: widen before exiting.

A market priced 1c too aggressively and a market full of informed flow look
identical on one reading. Only the second stays negative after we back off,
and only the second is worth giving up the rent for.
"""
import dataclasses
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strategy.config import load as load_cfg  # noqa: E402
from strategy.gate import (  # noqa: E402
    HALTED, NORMAL, WIDENED, fleet_posture, next_state, offset_for)


def _c():
    return dataclasses.replace(load_cfg(), markout_min_sample=20,
                               markout_widen_threshold=-0.005,
                               markout_catastrophic_threshold=-0.020)


def test_thin_sample_never_moves_off_normal():
    """The expensive mistake is evicting a good market on 3 noisy fills."""
    s = {"verdict": "insufficient_sample", "mean_per_share": None, "n": 3}
    assert next_state("NORMAL", s, _c()) == "NORMAL"


def test_losing_market_widens_first():
    s = {"verdict": "losing", "mean_per_share": -0.02, "n": 25}
    assert next_state("NORMAL", s, _c()) == "WIDENED"


def test_still_losing_after_widening_exits():
    """Backed off and still picked off -- that is toxic flow, not mispricing."""
    s = {"verdict": "losing", "mean_per_share": -0.02, "n": 60}
    assert next_state("WIDENED", s, _c()) == "EXITED"


def test_widened_holds_until_a_second_full_sample():
    """One sample got us here; demand another before surrendering the rent."""
    s = {"verdict": "losing", "mean_per_share": -0.02, "n": 25}
    assert next_state("WIDENED", s, _c()) == "WIDENED"


def test_recovery_returns_to_normal():
    s = {"verdict": "earning", "mean_per_share": 0.01, "n": 60}
    assert next_state("WIDENED", s, _c()) == "NORMAL"


def test_exit_is_terminal():
    s = {"verdict": "earning", "mean_per_share": 0.01, "n": 99}
    assert next_state("EXITED", s, _c()) == "EXITED"


def test_a_small_loss_inside_the_threshold_does_not_widen():
    """-0.2c is inside the -0.5c threshold: still profitable against the ~1c
    paired edge, so widening would give up rent for nothing."""
    s = {"verdict": "losing", "mean_per_share": -0.002, "n": 40}
    assert next_state("NORMAL", s, _c()) == "NORMAL"


# --- catastrophic magnitude bypass ------------------------------------------

def test_catastrophic_loss_exits_straight_from_normal():
    """-3c/share is not ambiguity to be resolved by widening. Skip WIDENED."""
    s = {"verdict": "losing", "mean_per_share": -0.03, "n": 25}
    assert next_state("NORMAL", s, _c()) == "EXITED"


def test_catastrophic_loss_ignores_the_doubled_sample_requirement():
    """n=25 is one sample, not the two the WIDENED->EXITED rule demands. At
    this magnitude the second sample is another 20 fills bought at -3c."""
    s = {"verdict": "losing", "mean_per_share": -0.03, "n": 25}
    assert next_state("WIDENED", s, _c()) == "EXITED"


def test_a_merely_bad_loss_still_widens_first():
    """-1c clears the widen threshold but not the catastrophic one, so the
    graduated path is intact -- the bypass is a magnitude rule, not a rename
    of the old one."""
    s = {"verdict": "losing", "mean_per_share": -0.01, "n": 25}
    assert next_state("NORMAL", s, _c()) == "WIDENED"


def test_the_bypass_does_not_override_the_sample_minimum():
    """A catastrophic MEAN over 3 fills is still 3 fills. The bypass drops the
    sample DOUBLING; the noise guard that stops us evicting a sound market on
    a handful of readings is untouched."""
    s = {"verdict": "insufficient_sample", "mean_per_share": -0.05, "n": 3}
    assert next_state("NORMAL", s, _c()) == "NORMAL"


def test_widened_state_quotes_further_from_mid():
    assert offset_for("WIDENED", 0.020, 0.035) == 0.035
    assert offset_for("NORMAL", 0.020, 0.035) == 0.020
    assert offset_for("EXITED", 0.020, 0.035) == 0.020


# --- fleet posture (U6) ------------------------------------------------------
# A POOLED reading is evidence about the universe, not about any one market, so
# it gets its own function rather than another arm of `next_state`. Posture is
# derived fresh from the pool every sweep and takes no previous state: there is
# nothing to remember, which is exactly what makes it reversible.

def test_the_recorded_pooled_reading_halts_the_fleet():
    """AE6. -0.052375/share on n=52 is the reading off the recorded run, where
    the fleet sits at WIDENED today. Two and a half times the catastrophic
    threshold pooled across every market is not one mispriced book."""
    p = {"verdict": "losing", "mean_per_share": -0.052375, "n": 52}
    assert fleet_posture(p, _c()) == HALTED


def test_a_merely_losing_pool_only_widens():
    """-0.8c is past the widen threshold and nowhere near the catastrophic one.
    Halting the whole fleet on it would forfeit the rent everywhere for a loss
    a wider quote can still trade its way out of."""
    p = {"verdict": "losing", "mean_per_share": -0.008, "n": 52}
    assert fleet_posture(p, _c()) == WIDENED


def test_an_earning_pool_is_normal():
    p = {"verdict": "earning", "mean_per_share": 0.01, "n": 52}
    assert fleet_posture(p, _c()) == NORMAL


def test_a_thin_pool_never_halts():
    """The same noise guard `next_state` keeps: a catastrophic MEAN over a
    handful of fills is a handful of fills. The posture gates every market at
    once, so acting on thin evidence is the more expensive mistake."""
    p = {"verdict": "insufficient_sample", "mean_per_share": -0.05, "n": 3}
    assert fleet_posture(p, _c()) == NORMAL


def test_a_pool_with_no_mean_is_normal():
    """Absence of a reading is not a bad reading."""
    assert fleet_posture({"verdict": "losing", "mean_per_share": None,
                          "n": 52}, _c()) == NORMAL
    assert fleet_posture({}, _c()) == NORMAL


def test_the_halt_clears_when_the_pool_recovers():
    """HALTED is a posture, not a state. Nothing is carried between readings,
    so a recovered pool returns NORMAL with no re-entry rule to satisfy --
    unlike EXITED, which is terminal by design."""
    cfg = _c()
    assert fleet_posture({"verdict": "losing", "mean_per_share": -0.052,
                          "n": 52}, cfg) == HALTED
    assert fleet_posture({"verdict": "earning", "mean_per_share": 0.02,
                          "n": 52}, cfg) == NORMAL


def test_the_posture_never_reaches_the_per_market_state_machine():
    """The two mechanisms are independent. `next_state` is a pure function of
    ONE market's stats and has no posture input; feeding it the same
    catastrophic pooled numbers produces EXITED on its own terms, which is why
    the fallback caps borrowed verdicts at WIDENED (KTD5)."""
    p = {"verdict": "losing", "mean_per_share": -0.052375, "n": 52}
    assert fleet_posture(p, _c()) == HALTED
    assert next_state(HALTED, p, _c()) != HALTED
