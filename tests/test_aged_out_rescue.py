"""The aged-out rescue (Issue #311): a one-sided leg past the rescue window.

`pairs_exit_window_sec` is a discovery filter, not a deadline. A naked leg older
than it fell out of `auto_manage_pairs` and nothing else closed it, so it sat
unmanaged until settlement. These tests pin the decision that replaces that gap:
a market-end-aware deadline, computed by one pure function, failing closed on
anything it cannot read.

No network, no venue, no clock: the verdict is a pure function of its inputs,
and the store-driven pass gets its own tests further down.
"""
from pathlib import Path
import pytest

from core_brain.config import MakerConfig
from core_brain import single_buy_saver as lp

WINDOW_MS = 900_000          # pairs_exit_window_sec = 900.0
LEAD_SEC = 900.0             # aged_out_rescue_lead_sec
NOW_S = 1_800_000_000.0      # an arbitrary, fixed instant
WINDOW_SEC = WINDOW_MS / 1000.0


def _verdict(*, age_sec: float = 3600.0, end_in_sec: float = 7200.0,
             closed=False, accepting=True, lead_sec: float = LEAD_SEC,
             last_fill_ms: int | None = None):
    """The verdict for a leg `age_sec` old, on a market ending in `end_in_sec`."""
    if last_fill_ms is None:
        last_fill_ms = int((NOW_S - age_sec) * 1000.0)
    end_ts = None if end_in_sec is None else NOW_S + end_in_sec
    return lp.aged_out_verdict(
        last_fill_ms=last_fill_ms, window_ms=WINDOW_MS, now_s=NOW_S,
        end_ts=end_ts, lead_sec=lead_sec,
        venue_closed=closed, venue_accepting=accepting,
    )


# --- 1. the window keeps its meaning --------------------------------------

def test_an_undated_fill_is_left_alone():
    # Arrange / Act - no venue_ts, so the fill cannot be placed in time.
    verdict, reason = _verdict(last_fill_ms=0)

    # Assert
    assert verdict == "not_aged_out"
    assert "undated" in reason


def test_a_fill_inside_the_window_keeps_the_rescue_route():
    # Arrange / Act - 300s old against a 900s window.
    verdict, reason = _verdict(age_sec=300.0)

    # Assert - the in-window route order owns this leg, not the new arm.
    assert verdict == "not_aged_out"
    assert "inside window" in reason


def test_a_fill_exactly_at_the_window_edge_is_not_aged_out():
    # Arrange / Act - the existing discovery filter compares with `>`.
    verdict, _ = _verdict(age_sec=WINDOW_SEC)

    # Assert
    assert verdict == "not_aged_out"


def test_a_fill_past_the_window_is_aged_out():
    # Arrange / Act
    verdict, reason = _verdict(age_sec=WINDOW_SEC + 1.0)

    # Assert - aged out, but not yet due: the market ends in two hours.
    assert verdict == "awaiting_lead"
    assert f"{WINDOW_SEC:.0f}s" in reason


# --- 2. fail closed on anything unreadable --------------------------------

def test_an_unreadable_venue_state_never_invents_a_deadline():
    # Arrange / Act / Assert - a missing boolean is not "false", never live.
    for closed, accepting in ((None, True), (False, None), (None, None)):
        verdict, reason = _verdict(closed=closed, accepting=accepting)
        assert verdict == "end_unknown", (closed, accepting)
        assert "unreadable" in reason


def test_an_unreadable_end_time_leaves_the_leg_naked():
    # Arrange / Act - the venue is open but reports no end to measure against.
    verdict, reason = _verdict(end_in_sec=None)

    # Assert
    assert verdict == "end_unknown"
    assert "unreadable" in reason


def test_a_market_the_venue_already_closed_is_left_to_settlement():
    # Arrange / Act / Assert - selling into a closed market is not available,
    # and the resolution path owns the position.
    for closed, accepting in ((True, True), (True, False), (False, False)):
        verdict, reason = _verdict(closed=closed, accepting=accepting)
        assert verdict == "venue_closed", (closed, accepting)
        assert "closed" in reason


# --- 3. the deadline is market-end aware ----------------------------------

def test_a_future_end_outside_the_lead_is_waited_out():
    # Arrange / Act - ends in 2h, lead is 15min, so 105 minutes remain.
    verdict, reason = _verdict(end_in_sec=7200.0)

    # Assert
    assert verdict == "awaiting_lead"
    assert "lead" in reason


def test_entering_the_lead_window_before_the_end_is_due():
    # Arrange / Act - 600s to the end, lead is 900s.
    verdict, reason = _verdict(end_in_sec=600.0)

    # Assert
    assert verdict == "due"
    assert "600s" in reason


def test_a_stated_end_already_past_while_the_venue_still_accepts_is_due():
    # Arrange - the sports case from #312: `endDate` is the kickoff, so a live
    # in-play market carries an end that has already passed while the venue is
    # still taking orders. There is no future end to wait for.
    verdict, reason = _verdict(end_in_sec=-3600.0)

    # Assert
    assert verdict == "due"
    assert "passed" in reason


def test_the_lead_window_is_measured_against_the_end_not_the_clock():
    # Arrange / Act - same leg and market, two different leads.
    soon, _ = _verdict(end_in_sec=1200.0, lead_sec=3600.0)
    later, _ = _verdict(end_in_sec=1200.0, lead_sec=900.0)

    # Assert
    assert soon == "due"
    assert later == "awaiting_lead"


def test_a_due_reason_names_the_age_the_window_and_the_lead():
    # Arrange / Act - the #312 legibility rule: a refusal or an action carries
    # the values behind it, never one identical string for every row.
    verdict, reason = _verdict(age_sec=10_452.0, end_in_sec=600.0)

    # Assert
    assert verdict == "due"
    assert "10452s" in reason
    assert f"{WINDOW_SEC:.0f}s" in reason
    assert f"{LEAD_SEC:.0f}s" in reason


# --- 4. the knobs ---------------------------------------------------------

def test_the_new_knobs_are_on_by_default():
    # Arrange / Act
    cfg = MakerConfig()

    # Assert
    assert cfg.enable_aged_out_rescue is True
    assert cfg.aged_out_rescue_lead_sec == 900.0


def test_no_existing_default_moved():
    # Arrange - the issue forbids touching any shipped default.
    cfg = MakerConfig()

    # Act / Assert
    assert cfg.pairs_exit_window_sec == 900.0
    assert cfg.max_pair_cost == 0.99
    assert cfg.single_buy_max_loss_pct == 0.10
    assert cfg.single_buy_max_loss_usd == 0.045
    assert cfg.single_buy_grace_sec == 0.0
    assert cfg.enable_pairs_rule is True


def test_the_lead_knob_has_an_env_override(monkeypatch):
    # Arrange - the operator can retune the deadline without a code change.
    monkeypatch.setenv("HUNTER_AGED_OUT_RESCUE_LEAD_SEC", "120")

    from core_brain.config import load

    # Act / Assert
    assert load().aged_out_rescue_lead_sec == 120.0


def test_a_negative_lead_override_is_refused(monkeypatch):
    # Arrange - a negative lead would fire the deadline after the market ended.
    monkeypatch.setenv("HUNTER_AGED_OUT_RESCUE_LEAD_SEC", "-5")

    from core_brain.config import load

    # Act / Assert
    with pytest.raises(ValueError):
        load()
