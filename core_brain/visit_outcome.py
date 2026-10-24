"""What one Trader market visit decided (#804).

A visit ends in exactly one outcome: quoted, refused for a transient reason
(the book moved, not the market), or refused for good (a named lifecycle
stop). The mapping from `decide`'s refusal text to its outcome lives here and
nowhere else, so the live rotation and the rehearsal seam classify alike.

Pure: no venue, no registry, no clock. The interface is the test surface.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from core_brain.market_lifecycle import LifecycleStop, classify_refusal


class VisitOutcome(Enum):
    """What one market visit decided, beyond the intents list."""
    QUOTED = "quoted"                    # intents present (or no opinion)
    REFUSED_TRANSIENT = "refused_transient"  # visited, refused: transient reason
    REFUSED_TERMINAL = "refused_cancel"  # visited, refused for good: named stop


def classify_refusal_text(why: str) -> VisitOutcome:
    """Terminal or transient, from `decide`'s refusal reason (pure).

    Delegates to the single enumerated list in `market_lifecycle`: a named
    stop refuses for good, everything else (wide book, completable cap,
    quote window, mid band, ...) is transient -- the book moved, not the
    market. Matched case-insensitively.
    """
    if classify_refusal(why) is not None:
        return VisitOutcome.REFUSED_TERMINAL
    return VisitOutcome.REFUSED_TRANSIENT


def visit_stop(why: str) -> Optional[LifecycleStop]:
    """Name the lifecycle stop for a refused visit (pure)."""
    return classify_refusal(why or "")
