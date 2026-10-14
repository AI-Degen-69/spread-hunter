"""Queue-multiple helper shared by the live statistics report and the rehearsal fill model.

One home for `queue_ahead / size` with its validation (#541). This module is
deliberately neutral ground: it names no engine, imports no model, and carries
no inferred-fill semantics, so both the live report path and the rehearsal
path may import it without meeting each other. A multiple of N means the tape
must trade N times the order size at the exact order price before first fill.
"""
from __future__ import annotations

import math


def queue_multiple(queue_ahead: float | None, order_size: float | None) -> float | None:
    """Queue depth relative to order size. `None` means unmeasured."""
    if queue_ahead is None or order_size is None:
        return None
    try:
        queue = float(queue_ahead)
        size = float(order_size)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(queue) or not math.isfinite(size):
        return None
    if queue < 0.0 or size <= 0.0:
        return None
    return queue / size
