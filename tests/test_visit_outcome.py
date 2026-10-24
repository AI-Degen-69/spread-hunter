"""Visit-outcome interface tests (#804).

`core_brain.visit_outcome` owns the Trader's visit taxonomy: what one market
visit decided, derived from `decide`'s refusal text. One interface, one place
to test — callers (Trader rotation, shadow seam) classify the same way.
"""
from __future__ import annotations

import pytest

from core_brain.market_lifecycle import LifecycleStop
from core_brain.visit_outcome import (
    VisitOutcome,
    classify_refusal_text,
    visit_stop,
)


class TestClassifyRefusalText:
    """Terminal markers refuse for good; everything else is transient."""

    @pytest.mark.parametrize("why", [
        "UP: mid 0.950 outside [0.10,0.90] -- decided market; "
        "DOWN: mid 0.050 outside [0.10,0.90] -- decided market",
        "UP: hedge token DOWN not tradeable (settled book 0.999/0.001) "
        "-- a fill here could not be closed",
        "t_remaining 0s: market ended",
        "market exited: fills still lost money after widening",
        "unfunded by the allocator -- quoting nothing",
        "hit 25 fills for this market",
    ])
    def test_terminal_markers_refuse_terminal(self, why):
        assert classify_refusal_text(why) is VisitOutcome.REFUSED_TERMINAL

    @pytest.mark.parametrize("why", [
        "UP: 8.0c from mid > 4.5c quote window; "
        "DOWN: 8.0c from mid > 4.5c quote window",
        "no side quotable",
        "wide book, retry",
        "",
    ])
    def test_flicker_reasons_refuse_transient(self, why):
        assert classify_refusal_text(why) is VisitOutcome.REFUSED_TRANSIENT

    def test_matching_ignores_case(self):
        assert classify_refusal_text("DECIDED MARKET") is VisitOutcome.REFUSED_TERMINAL


class TestVisitStop:
    def test_terminal_text_names_its_stop(self):
        assert visit_stop("t_remaining 0s: market ended") is LifecycleStop.COUNTDOWN_EXPIRED

    def test_transient_text_names_no_stop(self):
        assert visit_stop("wide book, retry") is None

    def test_empty_text_names_no_stop(self):
        assert visit_stop("") is None
        assert visit_stop(None) is None
