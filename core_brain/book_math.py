"""Book math for the Single buy path (#806).

Every number the guarded Single buy execution reads off a book lives here:
touch prices, depth at a limit, and the venue tick grid. Pure: a book in,
numbers out. The interface is the test surface — the saver calls it, never
reimplements it.
"""
from __future__ import annotations

from typing import Optional

DEFAULT_TICK_SIZE: float = 0.01
PRICE_EPS_OFFSET: float = 1e-9


def book_levels(book, key: str) -> list[tuple[float, float]]:
    """Normalise one side of a book to [(price, size)].

    Accepts the SDK's object shape and a plain dict, because the CLOB client
    has returned both across versions and a book parser that only handles one
    of them fails at the moment the book matters most.
    """
    if isinstance(book, dict):
        raw = book.get(key)
    else:
        raw = getattr(book, key, None)

    levels: list[tuple[float, float]] = []
    for lvl in raw or []:
        if isinstance(lvl, dict):
            price, size = lvl.get("price"), lvl.get("size")
        else:
            price, size = getattr(lvl, "price", None), getattr(lvl, "size", None)
        if price is None or size is None:
            continue
        try:
            levels.append((float(price), float(size)))
        except (TypeError, ValueError):
            continue
    return levels


def best_ask(book) -> Optional[float]:
    levels = book_levels(book, "asks")
    return min(p for p, _ in levels) if levels else None


def best_bid(book) -> Optional[float]:
    levels = book_levels(book, "bids")
    return max(p for p, _ in levels) if levels else None


def bid_depth(book) -> float:
    return sum(s for _, s in book_levels(book, "bids"))


def tick_size(book) -> float:
    raw = book.get("tick_size") if isinstance(book, dict) else getattr(book, "tick_size", None)
    try:
        tick = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_TICK_SIZE
    return tick if tick > 0 else DEFAULT_TICK_SIZE


def floor_to_tick(price: float, tick: float) -> float:
    """Round down onto the venue's grid. Off-grid prices are rejected orders.

    The nudge before truncating is not cosmetic: in binary floating point
    `0.29 / 0.01` is 28.999999999999996, so a bare `int()` floors an
    already-aligned price a whole tick lower. The sell bound would sit a tick
    below what we chose and `depth_at_or_above` would count one extra level --
    safe in direction, wrong in value.
    """
    steps = int(round(price / tick, 9))
    return round(steps * tick, 10)


def depth_at_or_above(book, limit: float) -> float:
    """Bid size available at or above a price floor.

    Summing the whole ladder would size an order against depth we have already
    decided is too cheap to accept.
    """
    return sum(s for p, s in book_levels(book, "bids") if p >= limit)


def ask_depth_at_or_below(book, limit: float) -> float:
    """Ask size a BUY at this limit price can take."""
    return sum(s for p, s in book_levels(book, "asks")
               if p <= limit + PRICE_EPS_OFFSET)
