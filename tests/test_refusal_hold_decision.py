"""Premise lock for #622's refusal-hold decision.

The experiment's control arm only means something if option A (remove) is
genuinely what ships today. It is: commit `d7810fd` (2026-10-16, PR #623)
turned the hold off and deleted the #419 mid-hold band hours before this issue
was planned, so the issue body's own background -- `REFUSED_HOLD_GRACE_CYCLES = 3`
and a live mid-hold band -- describes a baseline that no longer exists.

These assertions are the cheap guard on that measurement. If a later commit
flips the shipped baseline back to option B without the run record noticing,
they fail and point at the record that has to be corrected first.
"""

from __future__ import annotations

import inspect
import pathlib
import re

from core_brain import trader_loop

RUN_RECORD = pathlib.Path("docs/runs/2026-10-16-622-refusal-hold-tournament.md")

#: The five metrics #622's acceptance criteria require, per option.
REQUIRED_METRICS = (
    "churn",
    "stop-out",
    "time-to-fill",
    "trend",
    "stop slippage",
)


def test_option_a_is_the_shipped_baseline():
    """The hold is OFF: the first transient refusal already expires into a cancel.

    `d7810fd` set the constant to 1 as an interim operator directive "pending
    #622". A value of 3 here would mean option B is live and the control arm is
    mislabelled -- the record's premise needs rewriting before any run.
    """
    assert trader_loop.REFUSED_HOLD_GRACE_CYCLES == 1, (
        "The shipped baseline is no longer option A. #622's run record "
        "(docs/runs/2026-10-16-622-refusal-hold-tournament.md) labels the "
        "control arm 'remove'; correct the record before measuring."
    )


def test_the_mid_hold_band_is_gone():
    """#419's band was removed by the same commit; no constant should remain.

    A reappearing `MID_HOLD_BAND` would mean the band is live again, which
    changes what option C has to build (it would inherit the band instead of
    re-introducing a mid path).
    """
    assert not hasattr(trader_loop, "MID_HOLD_BAND"), (
        "MID_HOLD_BAND is back; #622's option C analysis assumes it was removed "
        "by d7810fd."
    )


def test_plan_orders_takes_no_token_mid_parameter():
    """The planner has no mid input at baseline.

    `d7810fd` deleted `plan_orders`'s `token_mids` keyword along with the band
    that consumed it. Option C therefore has to re-introduce a mid path rather
    than reuse one -- a cost the decision record must carry.
    """
    params = inspect.signature(trader_loop.plan_orders).parameters
    assert "token_mids" not in params


def test_run_record_pre_registers_the_rule_and_metrics():
    """The decision rule and all five metrics are fixed before any result exists.

    This is the operator's standing rule made mechanical: first instrument, then
    decide. A record that gains results but loses its pre-registered rule, its
    evidence label, or its fill-model caveat is not evidence.
    """
    assert RUN_RECORD.exists(), f"missing run record: {RUN_RECORD}"
    text = RUN_RECORD.read_text(encoding="utf-8")

    assert re.search(r"decision rule", text, re.IGNORECASE), (
        "the run record must state the decision rule it will be judged by"
    )
    for metric in REQUIRED_METRICS:
        assert metric in text.lower(), f"run record is missing the metric: {metric}"

    assert "approximately concurrent" in text.lower(), (
        "the evidence label is required: arms poll independently and do not see "
        "identical books"
    )
    assert "strictly below" in text.lower(), (
        "the shadow fill-model limitation must be stated, not implied"
    )
