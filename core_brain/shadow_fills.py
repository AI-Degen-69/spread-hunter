"""The shadow fill model: tape-confirmed volume, queue position, nothing else.

Pure by design -- values in, values out, no clock, no socket, no SQLite. That is
what makes it testable against recorded books and tape, which is the only way to
tell an over-crediting model from an honest one.

This module belongs to the rehearsal and to nothing else.
`core_brain/live_fill_engine.py` carries the opposite rule and keeps it: live, a
fill exists only when the venue says so. Inferring one there would be the worst
failure available to this system.

A sell print below our resting price is a sweep that cleared our level: it
zeroes the queue ahead and fills the full remainder at our own price. This
is now the ONLY fill path -- the old exact-price consumption path is
removed (see `credit_fills`).
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

    Descriptive only: how deep the quote sat at post. Queue position cannot
    be measured from the public book, so this no longer gates any fill --
    see `credit_fills`.
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
    """Credit tape volume to resting orders: trade-through only.

    This is the ONLY way a shadow fill is ever credited. No fills from
    mid-price moves, no fills from time spent in the book, no fills from tape
    volume at our own price. A resting order fills ONLY when the tape prints
    strictly below its price -- a trade-through proving the level was cleared.
    `core_brain/live_fill_engine.py` is the inverse and stays that way -- live,
    a fill exists only when the venue says so.

    `orders` arrives in post order. `traded` is `markets.recent_trades`
    output: token -> price -> volume since the last look.

    One case: a print strictly below our price is a trade-through. It proves
    our level was cleared, so the queue drops to zero and the full
    remainder fills at our own price. The lower print is evidence, not
    shared volume -- it is never consumed, so one print can fill every
    order resting above it.

    The previous exact-price path (consume `queue_ahead`, then credit the
    remainder from tape volume at our own price) is REMOVED. Queue position
    cannot be measured from the public book: a price->size snapshot exposes
    no FIFO rank, no cancel/fill split, and no orders added behind us, so
    `queue_ahead` was a pessimistic guess, not a measurement. Crediting
    fills from it risked both over- and under-filling on a number nobody
    can verify. Trade-through is the conservative rule: fill only on
    evidence we can see, under-fill rather than over-fill. `queue_ahead`
    is kept as descriptive telemetry but gates nothing.

    Returns the credited fills and, for each order that traded through, its queue
    zeroed. Orders that did not trade through are absent -- their queue is
    unchanged, so nothing is re-counted.
    """
    remaining_volume: dict[tuple[str, float], float] = {}
    for token_id, by_price in (traded or {}).items():
        for price, volume in (by_price or {}).items():
            key = (str(token_id), round(float(price), 4))
            remaining_volume[key] = remaining_volume.get(key, 0.0) + float(volume)

    # Trade-through evidence, snapshotted up front: every level with finite,
    # positive tape. A print below our price proves the level cleared, so it
    # stays available no matter which order is credited first.
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

    return fills, queues
