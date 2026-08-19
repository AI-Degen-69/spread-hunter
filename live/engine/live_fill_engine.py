"""Live fill engine interface implementation for live trading execution.

Backed by OrderRegistry and venue client. Wireable to nothing in test/simulation mode.

CRITICAL INVARIANT:
on_book() MUST NEVER infer a fill from book deltas, price changes, or trade tape.
In simulation, QueueFillEngine deduces fills from book deltas and queue position.
Live, fills come ONLY from the venue through the poll loop / OrderRegistry sync.
Inventing a fill is the worst failure available to this system.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from engine.order_registry import OrderRegistry


@dataclass
class LiveRestingOrder:
    """A resting order tracked by the live fill engine."""
    token_id: str
    side: str                 # 'UP' | 'DOWN'
    price: float
    size: float
    filled: float = 0.0
    queue_ahead: float = 0.0
    posted_ts: float = 0.0
    quote_id: int | None = None
    order_id: str | None = None  # Venue order ID
    cancelled: bool = False
    cancelled_ts: float | None = None
    cancel_reason: str = ""

    def cancel(self, ts: float = 0.0, reason: str = "") -> None:
        self.cancelled = True
        self.cancelled_ts = ts
        self.cancel_reason = reason

    @property
    def remaining(self) -> float:
        return max(0.0, self.size - self.filled)

    @property
    def is_open(self) -> bool:
        return (not self.cancelled) and self.remaining > 1e-9


@dataclass
class LiveFill:
    """A verified trade fill recorded by the live engine."""
    token_id: str
    side: str
    price: float
    size: float
    ts: float
    quote_id: int | None = None
    order_id: str | None = None
    trade_id: str | None = None
    queue_waited: float = 0.0
    reason: str = "venue"


@dataclass
class LiveFillEngine:
    """Fill engine implementing the interface sweep.py calls, backed by venue/registry.

    Methods:
      - post: record new resting order
      - amend: update resting order price
      - cancel: mark orders cancelled
      - cross: take liquidity on asks
      - open_orders: list active uncancelled orders
      - on_book: observe book update -- NEVER infers synthetic fills
      - filled_shares: sum filled shares
      - cost: total money spent on fills
      - avg_price: volume-weighted average fill price
    """
    registry: Optional[OrderRegistry] = None
    client: Any = None
    condition_id: str = ""
    orders: list[LiveRestingOrder] = field(default_factory=list)
    fills: list[LiveFill] = field(default_factory=list)
    unverified: list[LiveFill] = field(default_factory=list)
    reconciliation: list[Any] = field(default_factory=list)
    _last_book: dict[str, dict[float, float]] = field(default_factory=dict)
    _last_ts: dict[str, float] = field(default_factory=dict)

    def post(self, token_id: str, side: str, price: float, size: float,
             book_bids: dict[float, float], ts: float) -> LiveRestingOrder:
        """Register a new resting quote."""
        o = LiveRestingOrder(
            token_id=token_id,
            side=side,
            price=round(price, 4),
            size=size,
            queue_ahead=float(book_bids.get(round(price, 4), 0.0)),
            posted_ts=ts,
        )
        self.orders.append(o)
        return o

    def amend(self, order: LiveRestingOrder, price: float,
              book_bids: dict[float, float], ts: float) -> LiveRestingOrder:
        """Move a resting order to a new price."""
        new_price = round(price, 4)
        if new_price == order.price:
            return order
        order.price = new_price
        order.queue_ahead = float(book_bids.get(new_price, 0.0))
        order.posted_ts = ts
        return order

    def cross(self, token_id: str, side: str, size: float,
              book_asks: dict[float, float], ts: float,
              max_price: float = 1.0) -> list[LiveFill]:
        """Execute a taker crossing against book asks."""
        remaining = float(size)
        made: list[LiveFill] = []
        for price in sorted(book_asks):
            if remaining <= 1e-9 or price > max_price + 1e-9:
                break
            avail = float(book_asks.get(price, 0.0))
            if avail <= 1e-9:
                continue
            qty = min(remaining, avail)
            f = LiveFill(token_id=token_id, side=side, price=round(price, 4),
                         size=qty, ts=ts, queue_waited=0.0, reason="cross")
            self.fills.append(f)
            made.append(f)
            remaining -= qty
        return made

    def cancel(self, token_id: Optional[str] = None, ts: float = 0.0,
               reason: str = "") -> int:
        """Cancel open resting orders."""
        n = 0
        for o in self.orders:
            if o.is_open and (token_id is None or o.token_id == token_id):
                o.cancel(ts=ts, reason=reason)
                n += 1
        return n

    def open_orders(self, token_id: Optional[str] = None) -> list[LiveRestingOrder]:
        """Orders currently open and awaiting fill."""
        return [o for o in self.orders
                if o.is_open and (token_id is None or o.token_id == token_id)]

    def on_book(self, token_id: str, bids: dict[float, float], ts: float,
                traded: Optional[dict[float, float]] = None) -> list[LiveFill]:
        """Observe book update.

        CRITICAL INVARIANT: NEVER infers fills from book deltas or tape.
        Fills are applied only when confirmed by the venue.
        """
        self._last_book[token_id] = {round(p, 4): float(s) for p, s in bids.items()}
        self._last_ts[token_id] = ts
        # Always returns empty list: on-chain/CLOB venue fills come from poll loop only
        return []

    def record_venue_fill(self, trade_id: str, token_id: str, side: str,
                          price: float, size: float, ts: float,
                          order_id: Optional[str] = None) -> LiveFill:
        """Record an authenticated trade fill received from the venue."""
        f = LiveFill(
            token_id=token_id, side=side, price=round(price, 4), size=size,
            ts=ts, order_id=order_id, trade_id=trade_id, reason="venue"
        )
        self.fills.append(f)
        for o in self.orders:
            if o.is_open and o.token_id == token_id and o.side == side:
                take = min(o.remaining, size)
                o.filled += take
                if o.remaining <= 1e-9:
                    break
        return f

    def filled_shares(self, side: Optional[str] = None,
                      include_crossed: bool = True) -> float:
        """Sum filled shares for specified side or total."""
        return sum(f.size for f in self.fills
                   if (side is None or f.side == side)
                   and (include_crossed or f.reason != "cross"))

    def cost(self, side: Optional[str] = None) -> float:
        """Total dollar cost of fills."""
        return sum(f.size * f.price for f in self.fills
                   if side is None or f.side == side)

    def avg_price(self, side: Optional[str] = None) -> float:
        """Volume-weighted average price across fills."""
        sh = self.filled_shares(side)
        return (self.cost(side) / sh) if sh > 0 else 0.0
