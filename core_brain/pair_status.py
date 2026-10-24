"""One owner for the pair-status shape the dashboard renders.

`pairStatus` lived in dashboard/static/app.js with 5+ call sites deriving
paired / partial / unpaired from two share counts (held `up_sh`/`dn_sh` in
Positions, resting sizes in Open Orders, the hedge roll-up). The rule is now
shaped once here, carried on each KPI market as `pair_status`, and the JS page
variants adapt by reading that field with a local fallback.

Rule (verbatim from the JS it replaces): coerce each side with `Number(x) ||
0`; paired when both are held and equal, partial when both are held and
unequal, unpaired otherwise. A missing number must never decide a paired
verdict, so unmeasurable inputs fall to unpaired.
"""
from __future__ import annotations

import math
from typing import Any

PAIRED = "paired"
PARTIAL = "partial"
UNPAIRED = "unpaired"

PAIR_STATUS: dict[str, dict[str, str]] = {
    PAIRED: {"label": "Paired", "tone": "good"},
    PARTIAL: {"label": "Partial", "tone": "warn"},
    UNPAIRED: {"label": "Unpaired", "tone": "alert"},
}


def _coerce_shares(value: Any) -> float:
    """Mirror JS `Number(x) || 0`: unmeasurable reads as no shares."""
    try:
        num = float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0
    if isinstance(num, float) and (math.isnan(num) or math.isinf(num)):
        return 0.0
    return num or 0.0


def pair_status(up_size: Any, down_size: Any) -> str:
    """Classify the pair from two share counts: paired/partial/unpaired."""
    up = _coerce_shares(up_size)
    dn = _coerce_shares(down_size)
    if up > 0 and dn > 0:
        return PAIRED if up == dn else PARTIAL
    return UNPAIRED
