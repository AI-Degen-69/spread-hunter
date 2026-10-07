"""One enumerated list of lifecycle stops (#402).

Every reason the loop stops quoting a market lives here and nowhere else.
Poll and rescue code import this light module instead of the heavy Trader:
stop codes, the refusal classifier, and the persisted-resolution lookup.
"""
from __future__ import annotations

import logging
import time
from enum import Enum

log = logging.getLogger("market_lifecycle")


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
    except Exception as exc:
        # Fail closed (no ids => guards stay off books) but never silently:
        # a degraded store must be visible, or the operator would not know
        # the protection had stopped reading.
        log.warning("resolutions read failed; resolved guards degrade closed "
                    "until it recovers: %s: %s", type(exc).__name__, exc)
    return out


class DeadBookBackoff:
    """Per-token backoff for failing book reads (#402).

    A token whose book read fails is not re-read until its wait elapses;
    waits grow per consecutive failure up to a cap and clear on success.
    This bounds 404 spam against a dying book without ever resolving
    anything: a 404 is an availability failure, and only a confirmed venue
    end state may write a resolution (`market_resolution` owns that). The
    class holds no registry handle by construction, so no failure here can
    invent a settlement.
    """

    def __init__(self, base_sec: float = 5.0, cap_sec: float = 300.0,
                 factor: float = 2.0, now_fn=None):
        self._base = max(0.0, float(base_sec))
        self._cap = max(self._base, float(cap_sec))
        self._factor = max(1.0, float(factor))
        self._now = now_fn or time.time
        self._state: dict[str, tuple[int, float]] = {}

    def allow(self, token: str) -> bool:
        """True when a book read for this token may go out now."""
        fails, not_before = self._state.get(str(token), (0, 0.0))
        return self._now() >= not_before

    def note_success(self, token: str) -> None:
        """A read answered: the token is live again, waits restart."""
        self._state.pop(str(token), None)

    def note_failure(self, token: str) -> None:
        """A read failed: push the next allowed read further out, capped."""
        key = str(token)
        fails, _ = self._state.get(key, (0, 0.0))
        fails += 1
        wait = min(self._base * (self._factor ** (fails - 1)), self._cap)
        self._state[key] = (fails, self._now() + wait)
