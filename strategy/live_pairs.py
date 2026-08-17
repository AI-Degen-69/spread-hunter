"""Stage 3 — stop-loss / naked exit for a one-sided live pair.

This is a port of the pairs rule already proven in simulation at
`strategy/sweep.py:700-830`, not a redesign of it. The trigger there fired 16
times across 26,777 pairs, always on `pair_cost >= max_pair_cost`, and cost
3.67c per exit against 3.68c gained per completed pair. Those numbers are the
reason it is worth porting faithfully.

What changes in live is not the rule but the failure surface. In simulation a
cancel always succeeds, a book read is free, and nobody else can fill our order
between two statements. Live, each of those is a place to lose money, so the
sequence is written around them:

    cancel  ->  re-read state  ->  sell (only if still one-sided)

**Cancel before sell.** Selling first leaves a live resting order that can fill
into a position we just closed, re-opening exposure at the worst moment.

**Re-read between them.** A successful cancel does not mean the pair is still
one-sided: the cancel may have raced a match that already happened. If the
other leg filled, the pair is complete and worth $1.00 at merge, and
market-selling one leg of it converts that into a realized loss. That is the
worst outcome available on this path, so the re-read is not optional.

Every refusal raises rather than returning a value that reads like success --
the same fail-closed shape `merge` uses.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Optional

from strategy.order_registry import OrderRegistry, SIZE_EPS

DATA_API_BASE = "https://data-api.polymarket.com"

# How far the venue's position may sit from the registry's before we refuse to
# act. Sized for float dust from summing fills, not for a real discrepancy: a
# genuine divergence is a share or more, and anything at that scale means one
# of the two views is wrong and neither is safe to trade on.
POSITION_DIVERGENCE_TOLERANCE: float = 1e-6

# Venue minimum. A sell below this is rejected by the venue anyway, and
# attempting it burns a round trip to learn what we already know.
MIN_SELL_SHARES: float = 1.0


class PairExitRefused(RuntimeError):
    """The exit did not happen, and no venue write was left half-done.

    Raised rather than returned so a caller cannot mistake a refusal for a
    completed exit by ignoring a status field.
    """


class PairCompletionRefused(RuntimeError):
    """The completion did not happen, and nothing was sent.

    Separate from PairExitRefused because the two paths fail for opposite
    reasons: an exit refuses when it cannot safely close, a completion refuses
    when crossing would make the pair worse than holding it.
    """


def should_exit(fill_cost: float, light_ask: Optional[float],
                max_pair_cost: float) -> bool:
    """Port of the sim trigger: exit when the pair cannot complete under the cap.

    `>=`, not `>`. A pair costing exactly max_pair_cost is a guaranteed loss
    after gas, which is the whole reason the cap sits below $1.00.

    A missing ask fires rather than holds. No ask means there is nothing to
    complete against, so the leg stays naked for as long as that is true --
    holding on the hope that a quote appears is the position this rule exists
    to close.
    """
    if light_ask is None or light_ask <= 0:
        return True
    return (fill_cost + light_ask) >= max_pair_cost


def _book_levels(book, key: str) -> list[tuple[float, float]]:
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
    levels = _book_levels(book, "asks")
    return min(p for p, _ in levels) if levels else None


def best_bid(book) -> Optional[float]:
    levels = _book_levels(book, "bids")
    return max(p for p, _ in levels) if levels else None


def bid_depth(book) -> float:
    return sum(s for _, s in _book_levels(book, "bids"))


def fetch_positions(funder: str, timeout: float = 10.0) -> dict[str, float]:
    """Read held size per token from the Data API.

    An independent view of what the venue says we hold. The registry records
    what we believe; reconciling the two catches a class of bug that neither
    source can catch alone.

    Raises on any failure. An unreadable positions endpoint is not an empty
    portfolio, and treating it as one would let the divergence check pass by
    knowing nothing.
    """
    url = f"{DATA_API_BASE}/positions?user={funder}"
    req = urllib.request.Request(url, headers={"User-Agent": "spread-hunter"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))

    positions: dict[str, float] = {}
    for row in payload or []:
        if not isinstance(row, dict):
            continue
        token = str(row.get("asset") or row.get("tokenId") or row.get("token_id") or "")
        if not token:
            continue
        try:
            positions[token] = float(row.get("size", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
    return positions


def load_pair(registry: OrderRegistry, pair_id: str) -> dict:
    """Reduce a pair's rows to the numbers the rule needs.

    Sizes come from the fills, never from the intended size: an order that
    filled 4 of 10 is a 4-share position, and sizing an exit off 10 would sell
    shares that do not exist.
    """
    orders = registry.get_orders_by_pair(pair_id)
    if not orders:
        raise PairExitRefused(f"No orders carry pair_id={pair_id!r}.")

    by_token: dict[str, dict] = {}
    for o in orders:
        slot = by_token.setdefault(
            o.token_id,
            {"token_id": o.token_id, "matched": 0.0, "notional": 0.0, "orders": []},
        )
        slot["matched"] += registry.get_size_matched(o.id)
        slot["notional"] += registry.get_matched_notional(o.id)
        slot["orders"].append(o)

    legs = sorted(by_token.values(), key=lambda s: s["matched"], reverse=True)
    heavy = legs[0]
    light = legs[1] if len(legs) > 1 else {
        "token_id": None, "matched": 0.0, "notional": 0.0, "orders": []
    }

    naked = heavy["matched"] - light["matched"]
    fill_cost = (heavy["notional"] / heavy["matched"]) if heavy["matched"] > 0 else 0.0

    return {
        "pair_id": pair_id,
        "condition_id": orders[0].condition_id,
        "heavy": heavy,
        "light": light,
        "naked": naked,
        "fill_cost": fill_cost,
    }


def _check_positions(pair: dict, venue_positions: Optional[dict[str, float]]) -> bool:
    """Refuse when the venue does not agree with the registry about holdings.

    Returns whether the check actually ran. `None` means the caller supplied no
    view -- a caller decision, surfaced in the result as positions_checked=False
    rather than silently passing as agreement.
    """
    if venue_positions is None:
        return False

    token = pair["heavy"]["token_id"]
    believed = pair["heavy"]["matched"]
    observed = float(venue_positions.get(token, 0.0))
    if abs(observed - believed) > POSITION_DIVERGENCE_TOLERANCE:
        raise PairExitRefused(
            f"Registry and venue diverge on {token}: registry holds "
            f"{believed:.6f}, Data API reports {observed:.6f}. Refusing to "
            f"exit -- selling a size the venue does not agree we hold is "
            f"either an oversell or an exit of the wrong position."
        )
    return True


def exit_naked_leg(
    client,
    registry: OrderRegistry,
    pair_id: str,
    max_pair_cost: float,
    live: bool = False,
    venue_positions: Optional[dict[str, float]] = None,
) -> dict:
    """Close a one-sided pair: cancel the resting leg, then sell the filled one.

    `action` in the returned dict is one of:
      balanced       -- nothing naked, nothing to do
      hold           -- the pair still completes under the cap
      would_exit     -- dry run; the trigger fired but nothing was sent
      route_to_merge -- the pair completed between cancel and sell
      exited         -- the naked leg was sold

    Raises PairExitRefused on any condition where acting is worse than not
    acting.
    """
    pair = load_pair(registry, pair_id)

    if pair["naked"] <= SIZE_EPS:
        return {"action": "balanced", "pair_id": pair_id, "size": 0.0}

    # Before any venue write: does the venue agree we hold what we think we
    # hold? Checked first so a divergence costs nothing, rather than after a
    # cancel has already gone out.
    positions_checked = _check_positions(pair, venue_positions)

    light_token = pair["light"]["token_id"]
    light_ask = None
    if light_token:
        light_ask = best_ask(client.get_order_book(light_token))

    if not should_exit(pair["fill_cost"], light_ask, max_pair_cost):
        return {
            "action": "hold",
            "pair_id": pair_id,
            "pair_cost": pair["fill_cost"] + (light_ask or 0.0),
            "size": pair["naked"],
            "positions_checked": positions_checked,
        }

    if not live:
        return {
            "action": "would_exit",
            "pair_id": pair_id,
            "size": pair["naked"],
            "fill_cost": pair["fill_cost"],
            "light_ask": light_ask,
            "positions_checked": positions_checked,
        }

    # 1. Cancel the resting leg FIRST. Selling first would leave a live order
    #    that can fill into the position we are closing.
    from py_clob_client_v2.clob_types import OrderPayload

    cancelled: list[str] = []
    for o in pair["light"]["orders"]:
        if o.status not in ("open", "partial", "pending") or not o.order_id:
            continue
        try:
            client.cancel_order(OrderPayload(orderID=o.order_id))
        except Exception as exc:
            raise PairExitRefused(
                f"Cancel of resting leg {o.order_id} failed ({exc!r}). "
                f"Aborting before the sell -- selling while that order is "
                f"still live risks refilling the position we are closing."
            ) from exc
        cancelled.append(o.order_id)

    # 2. Re-read. The cancel may have raced a match that already happened, in
    #    which case the pair is complete and worth $1.00 at merge.
    after = load_pair(registry, pair_id)
    if after["naked"] <= SIZE_EPS:
        return {
            "action": "route_to_merge",
            "pair_id": pair_id,
            "condition_id": after["condition_id"],
            "cancelled": cancelled,
            "size": 0.0,
            "positions_checked": positions_checked,
        }

    # 3. Sell, sized by the registry and capped by the depth actually there.
    heavy_token = after["heavy"]["token_id"]
    heavy_book = client.get_order_book(heavy_token)
    bid = best_bid(heavy_book)
    if bid is None or bid <= 0:
        raise PairExitRefused(
            f"No bid on {heavy_token}: the resting leg is cancelled and the "
            f"position is naked, but there is nothing to sell into. Retry when "
            f"the book returns."
        )

    depth = bid_depth(heavy_book)
    size = min(after["naked"], depth)
    if size < MIN_SELL_SHARES:
        raise PairExitRefused(
            f"Sellable size {size:.4f} is below the venue minimum of "
            f"{MIN_SELL_SHARES}. Registry holds {after['naked']:.4f}, bid depth "
            f"is {depth:.4f}."
        )
    if size > after["naked"] + SIZE_EPS:
        # Unreachable by construction. Asserted anyway because an oversell is
        # the one error on this path that cannot be undone.
        raise PairExitRefused(
            f"Refusing to sell {size:.4f} against a registry holding of "
            f"{after['naked']:.4f}."
        )

    from py_clob_client_v2.clob_types import MarketOrderArgsV2

    resp = client.create_and_post_market_order(
        MarketOrderArgsV2(token_id=heavy_token, amount=size, side="SELL")
    )

    return {
        "action": "exited",
        "pair_id": pair_id,
        "condition_id": after["condition_id"],
        "token_id": heavy_token,
        "size": size,
        "bid": bid,
        "cancelled": cancelled,
        "positions_checked": positions_checked,
        "response": resp,
    }


def ask_depth(book) -> float:
    return sum(s for _, s in _book_levels(book, "asks"))


def complete_pair(
    client,
    registry: OrderRegistry,
    pair_id: str,
    max_pair_cost: float,
    live: bool = False,
    max_order_usd: float = 25.0,
) -> dict:
    """Stage 4 — cross the book to complete a one-sided pair.

    This closes exposure rather than opening it: the half-open leg is already
    at risk, and completing it produces a pair worth $1.00 at merge. That is
    why it sits inside the staged exposure rule alongside the exit.

    The cap is the whole discipline. A cross that pushes the pair to or past
    `max_pair_cost` is a guaranteed loss after gas, and closing it that way is
    the stop-loss's job -- this path must not do that job badly. So it refuses
    rather than crossing anyway.

    `action` is one of: balanced, would_complete, completed.
    Raises PairCompletionRefused when crossing would be worse than holding.
    """
    pair = load_pair(registry, pair_id)

    if pair["naked"] <= SIZE_EPS:
        return {"action": "balanced", "pair_id": pair_id, "size": 0.0}

    light_token = pair["light"]["token_id"]
    if not light_token:
        raise PairCompletionRefused(
            f"Pair {pair_id} has only one leg on record, so there is no token "
            f"to complete into."
        )

    book = client.get_order_book(light_token)
    ask = best_ask(book)
    if ask is None or ask <= 0:
        raise PairCompletionRefused(
            f"Cannot complete {pair_id}: no ask on {light_token}. With nothing "
            f"to cross into, the leg stays naked and the exit rule owns it."
        )

    pair_cost = pair["fill_cost"] + ask
    if pair_cost >= max_pair_cost:
        raise PairCompletionRefused(
            f"Completing {pair_id} at ask {ask:.4f} against a fill cost of "
            f"{pair['fill_cost']:.4f} gives pair_cost {pair_cost:.4f}, at or "
            f"above max_pair_cost {max_pair_cost:.4f}. That pair loses money "
            f"after gas; the exit path owns this case, not completion."
        )

    # Size from what actually filled, never from the intended size. Completing
    # the intended size against a partial fill would open fresh exposure on the
    # other side -- the opposite of this path's purpose.
    size = min(pair["naked"], ask_depth(book))
    if size < MIN_SELL_SHARES:
        raise PairCompletionRefused(
            f"Completable size {size:.4f} is below the venue minimum of "
            f"{MIN_SELL_SHARES}. Naked {pair['naked']:.4f}, ask depth "
            f"{ask_depth(book):.4f}."
        )

    notional = size * ask
    if notional > max_order_usd:
        raise PairCompletionRefused(
            f"Completion notional ${notional:.2f} exceeds MAX_ORDER_USD "
            f"${max_order_usd:.2f}. The Stage 1 cap applies to this order like "
            f"any other."
        )

    if not live:
        return {
            "action": "would_complete",
            "pair_id": pair_id,
            "token_id": light_token,
            "size": size,
            "ask": ask,
            "pair_cost": pair_cost,
            "notional": notional,
        }

    from py_clob_client_v2.clob_types import MarketOrderArgsV2

    # `amount` is NOT a share count on a BUY. The SDK's
    # get_market_order_amounts treats it as the maker amount -- the thing we
    # give -- so on a BUY it is USDC and the shares received are amount / price,
    # while on a SELL it is shares and the USDC received is amount * price.
    #
    # Passing the share count here would have submitted a $10.00 buy for a
    # 10-share completion at $0.30, acquiring about 33 shares: 23 shares of
    # fresh exposure on the leg this path exists to close. None of the guards
    # above would have caught it, because every one of them validated the
    # $3.00 we meant.
    resp = client.create_and_post_market_order(
        MarketOrderArgsV2(token_id=light_token, amount=notional, side="BUY",
                          price=ask)
    )

    return {
        "action": "completed",
        "pair_id": pair_id,
        "condition_id": pair["condition_id"],
        "token_id": light_token,
        "size": size,
        "ask": ask,
        "pair_cost": pair_cost,
        "notional": notional,
        "response": resp,
    }
