"""One enumerated list of lifecycle stops (#402).

Every reason the loop stops quoting a market lives here and nowhere else.
Poll and rescue code import this light module instead of the heavy Trader:
stop codes, the refusal classifier, and the persisted-resolution lookup.
"""
from __future__ import annotations

from enum import Enum


class LifecycleStop(Enum):
    """Why quoting stopped for a market. ``code`` is the stable store value.

    Only ``RESOLVED`` and ``MARKET_DROPPED`` end the lifecycle. Every other
    entry cancels quotes (or holds them) but keeps today's re-visit behavior.
    Entries with an empty ``marker`` never come from refusal text: they are
    assigned by the loop itself (feed absence, resolution skip, grace expiry).
    """

    RESOLVED = ("resolved", True, "")
    MARKET_DROPPED = ("market_dropped", True, "")
    DECIDED_BY_PRICE = ("decided_by_price", False, "decided market")
    SETTLED_BOOK = ("settled_book", False, "settled book")
    COUNTDOWN_EXPIRED = ("countdown_expired", False, "t_remaining")
    MARKET_EXITED = ("market_exited", False, "market exited")
    UNFUNDED = ("unfunded", False, "unfunded by the allocator")
    FILL_CAP_REACHED = ("fill_cap_reached", False, "fills for this market")
    HOLD_EXPIRED = ("hold_expired", False, "")

    def __init__(self, code: str, ends_lifecycle: bool, marker: str):
        self.code = code
        self.ends_lifecycle = ends_lifecycle
        self.marker = marker


def classify_refusal(why: str) -> LifecycleStop | None:
    """Map `decide`'s refusal text to its stop, or None when transient.

    Case-insensitive substring match, same as the old marker table. Entries
    without a marker never match: a held-out market with no named refusal is
    transient, not a stop.
    """
    lowered = (why or "").lower()
    for stop in LifecycleStop:
        if stop.marker and stop.marker in lowered:
            return stop
    return None


def resolved_condition_ids(registry) -> set[str]:
    """Condition ids with a persisted resolution row (lowercased).

    The single access point: Trader admission, poll consumers, and secondary
    book readers all check this set instead of re-reading the table.
    """
    out: set[str] = set()
    try:
        for r in registry.get_all_resolutions():
            cid = r.get("condition_id") if isinstance(r, dict) else None
            if cid:
                out.add(str(cid).lower())
    except Exception:
        pass
    return out
