"""The shadow fill model: tape-confirmed volume, queue position, nothing else.

Pure by design -- values in, values out, no clock, no socket, no SQLite. That is
what makes it testable against recorded books and tape, which is the only way to
tell an over-crediting model from an honest one.

This module belongs to the rehearsal and to nothing else.
`core_brain/live_fill_engine.py` carries the opposite rule and keeps it: live, a
fill exists only when the venue says so. Inferring one there would be the worst
failure available to this system.

A sell print below our resting price is a sweep that cleared our level: it
zeroes the queue ahead and fills the full remainder at our own price.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class ShadowRestingOrder:
    """One simulated resting BUY, as the shadow store knows it."""
    local_id: str
    token_id: str
    price: float
    size: float
    filled: float = 0.0
    queue_ahead: float = 0.0

    @property
    def remaining(self) -> float:
        return max(0.0, self.size - self.filled)


@dataclass(frozen=True)
class ShadowFill:
    """Volume credited to one resting order, at that order's own price."""
    local_id: str
    token_id: str
    price: float
    size: float


def queue_multiple(
    queue_ahead: float | None, order_size: float | None
) -> float | None:
    """How deep a quote sits in its queue, relative to its own size.

    Returns `queue_ahead / order_size`. `None` means unmeasured: the queue is
    missing/negative or the size is missing/not positive, so the row stays out
    of medians and maxima instead of distorting them.

    A multiple of N means the tape must trade N times the order size at the
    order's exact price before the first fill credit -- the `credit_fills()`
    rule below consumes the whole queue ahead before any volume reaches us.
    """
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


def queue_ahead_at(book: dict, price: float) -> float:
    """Size already resting at exactly `price` on the bids side.

    Better-priced bids sit ahead of the book, not ahead of this order at its own
    level: they trade against different volume, so counting them would delay
    every fill by depth that was never in this order's way.
    """
    bids = (book or {}).get("bids") or {}
    return float(bids.get(round(float(price), 4), 0.0))


def credit_fills(
    orders: list[ShadowRestingOrder],
    traded: dict[str, dict[float, float]],
) -> tuple[list[ShadowFill], dict[str, float]]:
    """Credit tape volume to resting orders, oldest first.

    This is the ONLY way a shadow fill is ever credited. No fills from
    mid-price moves, no fills from time spent in the book, tape volume only:
    a resting order fills only when real trade tape at its own price consumes
    the queue ahead of it and then reaches it. `core_brain/live_fill_engine.py`
    is the inverse and stays that way -- live, a fill exists only when the
    venue says so.

    `orders` arrives in post order, which is queue order at a price level.
    `traded` is `markets.recent_trades` output: token -> price -> volume since
    the last look. Volume consumes each order's remaining `queue_ahead` before
    any of it reaches the order itself.

    Two cases, compared at rounded price levels. A print at our own price
    consumes the queue first and then fills what it reaches, oldest first.
    A print strictly below our price is a trade-through: it proves our level
    was cleared, so the queue drops to zero and the full remainder fills at
    our own price. The lower print is evidence, not shared volume -- it is
    never consumed, so one print can fill every order resting above it.

    Returns the credited fills and every order's updated `queue_ahead`, so the
    caller can persist a queue that shrank without producing a fill -- forgetting
    that is how the same volume gets counted twice.
    """
    remaining_volume: dict[tuple[str, float], float] = {}
    for token_id, by_price in (traded or {}).items():
        for price, volume in (by_price or {}).items():
            key = (str(token_id), round(float(price), 4))
            remaining_volume[key] = remaining_volume.get(key, 0.0) + float(volume)

    # Trade-through evidence, snapshotted before any exact-price consumption:
    # every level with finite, positive tape. A print below our price proves
    # the level cleared, so it stays available no matter which order is
    # credited first.
    swept = {
        key for key, volume in remaining_volume.items()
        if math.isfinite(volume) and volume > 0.0
    }

    fills: list[ShadowFill] = []
    queues: dict[str, float] = {}
    for o in orders:
        own = round(float(o.price), 4)
        token = str(o.token_id)
        if any(price < own for (t, price) in swept if t == token):
            queues[o.local_id] = 0.0
            if o.remaining > 0:
                fills.append(ShadowFill(o.local_id, o.token_id, o.price,
                                        o.remaining))
            continue

        key = (token, own)
        volume = remaining_volume.get(key, 0.0)
        queue = float(o.queue_ahead)

        consumed = min(volume, queue)
        queue -= consumed
        volume -= consumed

        credited = min(volume, o.remaining)
        if credited > 0:
            fills.append(ShadowFill(o.local_id, o.token_id, o.price, credited))
            volume -= credited

        remaining_volume[key] = volume
        queues[o.local_id] = queue

    return fills, queues
