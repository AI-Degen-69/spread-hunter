"""In-memory reactive trigger board for unhedged single-leg positions (#699).

The `market-stream` WebSocket callback feeds `(best_bid, best_ask)` updates into
`OrphanTriggerBoard.observe`. Lookups and comparisons run entirely in memory
with zero SQLite or HTTP I/O. When a watched stop-loss bid or profitable hedge
ask threshold is crossed, the board records the trigger details, mutes that
condition for 2.0s so a burst of frames coalesces into one wake, and sets
`wake_event`.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

MUTE_DURATION_SEC = 2.0


@dataclass(frozen=True)
class Watch:
    """One active price threshold on an unhedged outcome token."""

    condition_id: str
    kind: str  # "stop" | "hedge"
    level: float
    source: str
    token_id: str = ""


class OrphanTriggerBoard:
    """Thread-safe watch board bridging `MarketStream` frames to `guardian_wake`."""

    def __init__(
        self,
        wake_event: threading.Event,
        clock: Callable[[], float] = time.monotonic,
        mute_sec: float = MUTE_DURATION_SEC,
    ) -> None:
        self._wake_event = wake_event
        self._clock = clock
        self._mute_sec = float(mute_sec)
        self._lock = threading.Lock()
        self._generation = 0
        self._published_generation = 0
        self._watches: dict[str, Watch] = {}
        self._muted_until: dict[str, float] = {}
        self._triggers: list[dict[str, Any]] = []

    def next_generation(self) -> int:
        with self._lock:
            self._generation += 1
            return self._generation

    def publish(
        self,
        watches: Mapping[str, Watch],
        generation: Optional[int] = None,
    ) -> bool:
        """Replace the whole watch map if `generation` is not stale."""
        new_map = {str(tok): w for tok, w in watches.items()}
        active_cids = {w.condition_id for w in new_map.values()}
        with self._lock:
            if generation is not None and generation < self._published_generation:
                return False
            if generation is not None:
                self._published_generation = generation
            else:
                self._generation += 1
                self._published_generation = self._generation
            self._watches = new_map
            # Drop mutes for conditions that are no longer in the published map.
            for cid in [c for c in self._muted_until if c not in active_cids]:
                del self._muted_until[cid]
            return True

    def observe(
        self,
        prices: Mapping[str, tuple[Optional[float], Optional[float]]],
    ) -> None:
        """Evaluate incoming `(best_bid, best_ask)` updates against active watches."""
        watches = self._watches
        if not watches or not prices:
            return
        crossed: list[tuple[str, Watch, float]] = []
        for token, pair in prices.items():
            watch = watches.get(str(token))
            if watch is None or pair is None:
                continue
            best_bid, best_ask = pair
            if watch.kind == "stop":
                if best_bid is not None and round(float(best_bid), 4) <= round(float(watch.level), 4):
                    crossed.append((str(token), watch, float(best_bid)))
            elif watch.kind == "hedge":
                if (
                    best_ask is not None
                    and float(best_ask) > 0.0
                    and round(float(best_ask), 4) < round(float(watch.level), 4)
                ):
                    crossed.append((str(token), watch, float(best_ask)))

        if not crossed:
            return

        now = self._clock()
        should_wake = False
        with self._lock:
            for token, watch, price in crossed:
                # Re-verify against current watches under lock so a concurrent
                # publish that replaced the watch level or kind is respected.
                current_watch = self._watches.get(token)
                if current_watch != watch:
                    continue
                cid = current_watch.condition_id
                muted_until = self._muted_until.get(cid)
                if muted_until is not None and now < muted_until:
                    continue
                self._muted_until[cid] = now + self._mute_sec
                self._triggers.append({
                    "condition_id": cid,
                    "token_id": current_watch.token_id or token,
                    "kind": current_watch.kind,
                    "source": current_watch.source,
                    "level": float(current_watch.level),
                    "price": price,
                    "triggered_at": now,
                })
                should_wake = True

        if should_wake:
            self._wake_event.set()

    def pop_triggers(self) -> list[dict[str, Any]]:
        """Return and clear recorded trigger events."""
        with self._lock:
            out = list(self._triggers)
            self._triggers.clear()
            return out

    def condition_ids(self) -> set[str]:
        """Return the set of currently watched condition IDs."""
        watches = self._watches
        return {w.condition_id for w in watches.values() if w.condition_id}

    def watches(self) -> dict[str, Watch]:
        """Return a shallow copy of the active token-to-watch map."""
        return dict(self._watches)
