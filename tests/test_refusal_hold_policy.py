"""#622: the temporary refusal-hold policy switch and option C's drift rule.

Three answers to "what happens to a resting order when `decide` transiently
refuses this market this cycle":

* `remove` — nothing holds. The first transient refusal expires into a cancel.
  This is what ships today (`d7810fd`), so it is the experiment's control arm.
* `keep` — survive `REFUSED_HOLD_GRACE_CYCLES_HELD` consecutive refused cycles,
  then expire. The pre-`d7810fd` behavior.
* `drift` — survive flicker, but cancel as soon as the token's mid has moved
  further than the tolerance from where it stood at the first refusal of the
  streak. Keeps the churn defense, kills the infinite dodge.

The switch is temporary: #622 deletes it along with the losing branches once
the shadow tournament picks a winner.
"""

from __future__ import annotations

import pytest

from core_brain.config import REFUSAL_HOLD_POLICIES, MakerConfig, load
from core_brain.trader_loop import (
    CANCEL_HOLD_DRIFT,
    REFUSED_HOLD_GRACE_CYCLES,
    REFUSED_HOLD_GRACE_CYCLES_HELD,
    VisitOutcome,
    _drifted_tokens,
    _grace_cycles,
    plan_orders,
)


def _open(token="tok-up", price=0.60, oid="o1"):
    return {"token_id": token, "price": price, "order_id": oid,
            "side": "BUY", "status": "open"}


class TestPolicyDefault:
    """The shipped default must stay option A until the decision is recorded."""

    def test_default_policy_is_remove(self):
        assert MakerConfig().refusal_hold_policy == "remove"

    def test_default_policy_effective_grace_is_off(self):
        assert _grace_cycles(MakerConfig().refusal_hold_policy) == 1
        assert REFUSED_HOLD_GRACE_CYCLES == 1

    def test_every_policy_is_known(self):
        assert REFUSAL_HOLD_POLICIES == frozenset({"remove", "keep", "drift"})


class TestPolicyEnvOverride:
    def test_keep_is_selectable(self, monkeypatch):
        monkeypatch.setenv("HUNTER_REFUSAL_HOLD_POLICY", "keep")
        assert load().refusal_hold_policy == "keep"

    def test_policy_is_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("HUNTER_REFUSAL_HOLD_POLICY", "  DRIFT ")
        assert load().refusal_hold_policy == "drift"

    def test_unknown_policy_raises_with_the_choice_list(self, monkeypatch):
        monkeypatch.setenv("HUNTER_REFUSAL_HOLD_POLICY", "hold")
        with pytest.raises(ValueError) as exc:
            load()
        assert "HUNTER_REFUSAL_HOLD_POLICY" in str(exc.value)
        assert "drift" in str(exc.value)

    def test_a_typo_in_the_dataclass_is_refused_too(self):
        # An arms file that sets the field directly must not silently run the
        # baseline: a mis-spelled arm would measure the control twice.
        with pytest.raises(ValueError):
            MakerConfig(refusal_hold_policy="Keep")

    def test_drift_tolerance_overrides(self, monkeypatch):
        monkeypatch.setenv("HUNTER_REFUSAL_HOLD_DRIFT", "0.05")
        assert load().refusal_hold_drift == 0.05

    def test_drift_tolerance_is_bounded(self, monkeypatch):
        monkeypatch.setenv("HUNTER_REFUSAL_HOLD_DRIFT", "1.50")
        with pytest.raises(ValueError):
            load()


class TestGraceCycles:
    def test_remove_does_not_hold(self):
        assert _grace_cycles("remove") == 1

    def test_keep_and_drift_use_the_hold_count(self):
        assert _grace_cycles("keep") == REFUSED_HOLD_GRACE_CYCLES_HELD
        assert _grace_cycles("drift") == REFUSED_HOLD_GRACE_CYCLES_HELD

    def test_the_hold_count_is_the_pre_d7810fd_value(self):
        assert REFUSED_HOLD_GRACE_CYCLES_HELD == 3

    def test_an_unknown_policy_raises(self):
        with pytest.raises(ValueError):
            _grace_cycles("nope")


class TestDriftedTokens:
    REF = {"tok-up": 0.60, "tok-dn": 0.40}

    def test_movement_inside_the_tolerance_is_flicker(self):
        assert _drifted_tokens(self.REF, {"tok-up": 0.61, "tok-dn": 0.39},
                               0.02) == frozenset()

    def test_movement_at_the_tolerance_is_not_yet_drift(self):
        # Strictly greater than the tolerance: the boundary is flicker.
        assert _drifted_tokens(self.REF, {"tok-up": 0.62}, 0.02) == frozenset()

    def test_a_rise_is_drift(self):
        assert _drifted_tokens(self.REF, {"tok-up": 0.70}, 0.02) == frozenset({"tok-up"})

    def test_a_fall_is_drift(self):
        # Two-sided on purpose: "flicker versus drift" is about how far the mid
        # moved, not which way it went.
        assert _drifted_tokens(self.REF, {"tok-up": 0.50}, 0.02) == frozenset({"tok-up"})

    def test_only_the_moved_token_drifts(self):
        assert _drifted_tokens(self.REF, {"tok-up": 0.70, "tok-dn": 0.40},
                               0.02) == frozenset({"tok-up"})

    def test_a_missing_current_mid_is_never_drift(self):
        assert _drifted_tokens(self.REF, {"tok-dn": 0.40}, 0.02) == frozenset()

    def test_a_missing_reference_is_never_drift(self):
        assert _drifted_tokens({}, {"tok-up": 0.70}, 0.02) == frozenset()

    def test_no_mids_at_all_is_never_drift(self):
        assert _drifted_tokens(None, None, 0.02) == frozenset()

    def test_zero_tolerance_cancels_on_any_movement(self):
        assert _drifted_tokens(self.REF, {"tok-up": 0.6001}, 0.0) == frozenset({"tok-up"})


class TestPlanOrdersDriftCancel:
    ORDERS = [_open(token="tok-up", price=0.60, oid="o-up"),
              _open(token="tok-dn", price=0.40, oid="o-dn")]

    def test_a_transient_refusal_with_no_drift_holds_everything(self):
        to_cancel, to_submit = plan_orders(
            self.ORDERS, [], dead_band=0.03, cfg=MakerConfig(),
            visit_outcome=VisitOutcome.REFUSED_TRANSIENT,
            drifted_token_ids=frozenset())
        assert to_cancel == []
        assert to_submit == []

    def test_only_the_drifted_token_is_cancelled(self):
        reasons: dict = {}
        to_cancel, to_submit = plan_orders(
            self.ORDERS, [], dead_band=0.03, cfg=MakerConfig(), reasons=reasons,
            visit_outcome=VisitOutcome.REFUSED_TRANSIENT,
            drifted_token_ids=frozenset({"tok-up"}))
        assert [o["order_id"] for o in to_cancel] == ["o-up"]
        assert reasons == {"o-up": CANCEL_HOLD_DRIFT}
        assert to_submit == []

    def test_drift_cancels_inside_the_grace_window(self):
        # Drift shortens a hold; it does not wait for the cycle count to run out.
        to_cancel, _ = plan_orders(
            self.ORDERS, [], dead_band=0.03, cfg=MakerConfig(),
            visit_outcome=VisitOutcome.REFUSED_TRANSIENT,
            drifted_token_ids=frozenset({"tok-dn"}))
        assert [o["order_id"] for o in to_cancel] == ["o-dn"]

    def test_the_drift_reason_differs_from_the_not_quoted_reason(self):
        # A cancel that defended the strategy must be countable apart from one
        # that threw away queue position: the report groups churn by reason.
        assert CANCEL_HOLD_DRIFT == "hold_drift"

    def test_drift_does_not_override_a_lifecycle_preserve(self):
        reasons: dict = {}
        to_cancel, _ = plan_orders(
            self.ORDERS, [], dead_band=0.03, cfg=MakerConfig(), reasons=reasons,
            visit_outcome=VisitOutcome.REFUSED_TRANSIENT,
            drifted_token_ids=frozenset({"tok-up"}),
            preserve_order_ids={"o-up"})
        assert to_cancel == []
        assert reasons == {}
