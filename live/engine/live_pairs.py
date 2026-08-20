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

    cancel  ->  re-read the venue  ->  sell (only if still one-sided)

**Cancel before acting, on both legs.** Selling while an order rests lets it
fill into the position we just closed. That includes the heavy leg: an order at
`partial` still has working size, and leaving it there re-opens exposure on the
token being sold. The completion path cancels too, or its own resting maker BUY
and the taker BUY it is about to send can both fill and double the leg.

**Re-read the venue between them, not the registry.** A successful cancel does
not mean the pair is still one-sided: the cancel may have raced a match that
already happened. If the other leg filled, the pair is complete and worth $1.00
at merge, and market-selling one leg converts that into a realized loss -- the
worst outcome available here.

The registry cannot answer that question. Fills reach `run/live.db` only through
`reconcile_orders` in the poll loop, so a match from seconds ago is invisible
there until the next cycle -- exactly the window this step covers. An earlier
version of this module read the registry and claimed to detect the race; it
could not. The read goes to the venue, and a failed read refuses rather than
sells.

Every refusal raises rather than returning a value that reads like success --
the same fail-closed shape `merge` uses.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Optional

from engine.order_registry import OrderRegistry, SIZE_EPS

DATA_API_BASE = "https://data-api.polymarket.com"

# How far the venue's position may sit from the registry's before we refuse to
# act. Sized for float dust from summing fills, not for a real discrepancy: a
# genuine divergence is a share or more, and anything at that scale means one
# of the two views is wrong and neither is safe to trade on.
POSITION_DIVERGENCE_TOLERANCE: float = 1e-6

# Venue minimum, in shares. An order below this is rejected by the venue anyway,
# and attempting it burns a round trip to learn what we already know. It gates
# both directions -- the venue rule is about order size, not about side.
MIN_ORDER_SHARES: float = 1.0

# Backwards-compatible alias. The constant was sell-only when Stage 3 landed;
# the name outlived the scope.
MIN_SELL_SHARES: float = MIN_ORDER_SHARES

# How far below the best bid a market sell may be filled, in absolute price.
#
# Absolute rather than proportional: these are probability prices in [0, 1],
# where a 2c give-up means the same thing at 0.10 as at 0.90, and a percentage
# would silently tighten to under a tick down there. Two cents is roughly the
# 3.67c average exit cost measured in simulation, so a fill worse than this is
# outside the behaviour the rule was validated against.
#
# Without a bound the SDK derives the sell price from the requested amount and
# will happily walk the book down to whatever level clears it.
MAX_SELL_SLIPPAGE: float = 0.02

# Fallback venue tick when the book does not carry one. The venue rejects prices
# off its tick grid, so a limit computed at full float precision is a rejected
# order rather than a careful one.
DEFAULT_TICK_SIZE: float = 0.01

# Page size for the Data API positions read.
POSITIONS_PAGE_SIZE: int = 500


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
    positions: dict[str, float] = {}
    offset = 0
    while True:
        # sizeThreshold=0 because the endpoint defaults to 1 and would drop
        # every sub-share holding -- a silent omission that the divergence gate
        # would then read as "the venue says we hold nothing". Paginated for the
        # same reason: the default limit is 100, and a truncated page is not an
        # empty portfolio either.
        url = (
            f"{DATA_API_BASE}/positions?user={funder}"
            f"&sizeThreshold=0&limit={POSITIONS_PAGE_SIZE}&offset={offset}"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "spread-hunter"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))

        rows = payload or []
        for row in rows:
            if not isinstance(row, dict):
                continue
            token = str(row.get("asset") or row.get("tokenId") or row.get("token_id") or "")
            if not token:
                continue
            try:
                positions[token] = float(row.get("size", 0.0) or 0.0)
            except (TypeError, ValueError):
                continue

        if len(rows) < POSITIONS_PAGE_SIZE:
            break
        offset += POSITIONS_PAGE_SIZE

    return positions


WORKING_STATUSES = ("open", "partial", "pending")


def _working_orders(leg: dict) -> list:
    """Orders on this leg that can still fill at the venue.

    `partial` counts. A partially filled order still has working size resting,
    and leaving it there is the same hazard as leaving an untouched one.
    """
    return [o for o in leg.get("orders", [])
            if o.status in WORKING_STATUSES and o.order_id]


def _cancel_orders(client, orders: list, leg_name: str,
                   refusal: type = PairExitRefused) -> list[str]:
    """Cancel every working order on a leg, or refuse having sent nothing more.

    `refusal` is parameterised because the completion path calls this too, and a
    caller that catches only PairCompletionRefused would otherwise see this
    refusal escape as an unexpected exception -- marking its audit row
    `interrupted` and blocking every later completion on that condition.
    """
    from py_clob_client_v2.clob_types import OrderPayload

    cancelled: list[str] = []
    for o in orders:
        try:
            client.cancel_order(OrderPayload(orderID=o.order_id))
        except Exception as exc:
            raise refusal(
                f"Cancel of {leg_name} order {o.order_id} failed ({exc!r}). "
                f"Aborting -- acting while that order is still live risks "
                f"refilling the position we are closing."
            ) from exc
        cancelled.append(o.order_id)
    return cancelled


def _venue_extra(client, registry: OrderRegistry, orders: list,
                 refusal: type = PairExitRefused) -> float:
    """Matched size the venue reports beyond what the registry has recorded.

    Compared per order, never leg total against leg total: `orders` is only the
    working subset, while the registry's leg figure includes every order on that
    token. Subtracting one from the other compares two different populations and
    silently understates or overstates the difference.
    """
    total_extra = 0.0
    for o in orders:
        venue = _venue_matched(client, [o], refusal=refusal)
        recorded = registry.get_size_matched(o.id)
        total_extra += max(0.0, venue - recorded)
    return total_extra


def _venue_matched(client, orders: list, refusal: type = PairExitRefused) -> float:
    """Total matched size for these orders, read from the venue right now.

    The registry cannot answer this question. Fills reach `run/live.db` only
    through `reconcile_orders` in the poll loop, so a match that landed seconds
    ago is invisible there until the next cycle -- which is precisely the window
    this read exists to cover.

    Raises rather than returning 0.0 on a failed or unrecognised read. An
    unreadable order is not an unfilled one, and the whole point of the check is
    that we do not sell into uncertainty.
    """
    total = 0.0
    for o in orders:
        if not o.order_id:
            continue
        try:
            raw = client.get_order(o.order_id)
        except Exception as exc:
            raise refusal(
                f"Could not read venue state for {o.order_id} ({exc!r}) after "
                f"cancelling. Refusing to sell -- if that leg filled in the "
                f"meantime the pair is complete and worth $1.00 at merge."
            ) from exc

        if raw is None:
            raise refusal(
                f"Venue returned nothing for order {o.order_id} after the "
                f"cancel. Refusing to sell on an unknown state."
            )

        matched = None
        for key in ("size_matched", "sizeMatched", "matched_size", "filled_size"):
            if isinstance(raw, dict) and key in raw:
                matched = raw[key]
                break
            if not isinstance(raw, dict) and hasattr(raw, key):
                matched = getattr(raw, key)
                break
        if matched is None:
            raise refusal(
                f"Venue response for {o.order_id} carries no matched-size "
                f"field. Refusing to sell rather than assuming it is zero."
            )
        try:
            total += float(matched)
        except (TypeError, ValueError) as exc:
            raise refusal(
                f"Venue matched size for {o.order_id} is not numeric "
                f"({matched!r}). Refusing to sell on an unreadable state."
            ) from exc
    return total


def _tick_size(book) -> float:
    raw = book.get("tick_size") if isinstance(book, dict) else getattr(book, "tick_size", None)
    try:
        tick = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_TICK_SIZE
    return tick if tick > 0 else DEFAULT_TICK_SIZE


def _floor_to_tick(price: float, tick: float) -> float:
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
    return sum(s for p, s in _book_levels(book, "bids") if p >= limit)


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

    if len(by_token) > 2:
        # A pair is two tokens. Silently reducing three to the largest two would
        # size an exit against a position a dropped leg partly offsets.
        raise PairExitRefused(
            f"pair_id={pair_id!r} spans {len(by_token)} token ids "
            f"({sorted(by_token)}). A pair is two legs; refusing rather than "
            f"acting on a reduced view of the position."
        )

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
        # Keyed by token as well as by rank. `heavy` and `light` are a ranking,
        # and a ranking flips: once the light leg fills past the heavy one, a
        # second load_pair call swaps them. Anything comparing a value taken
        # before an action with one taken after must key on the token, not on
        # which side happened to be larger at the time.
        "legs": by_token,
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
    if token not in venue_positions:
        raise PairExitRefused(
            f"The venue reports no position at all in {token}, while the "
            f"registry holds {believed:.6f}. Absence is not zero here -- it is "
            f"equally consistent with a truncated or filtered positions read, "
            f"and selling against either reading is unsafe."
        )
    observed = float(venue_positions[token])

    # Direction matters. An oversell is only possible when the venue holds LESS
    # than the registry believes, so that is the only direction that refuses.
    #
    # A surplus is ordinary: the same token can be held by another pair, or part
    # of a position may already have been merged. Refusing on it would block the
    # one action that closes exposure, over a discrepancy that cannot cause the
    # harm the gate exists to prevent.
    if observed < believed - POSITION_DIVERGENCE_TOLERANCE:
        raise PairExitRefused(
            f"Registry and venue diverge on {token}: registry holds "
            f"{believed:.6f}, Data API reports only {observed:.6f}. Refusing to "
            f"exit -- selling a size the venue does not agree we hold is an "
            f"oversell."
        )
    return True


def _naked_after(after: dict, heavy_token: str, light_token: Optional[str],
                 venue_light_extra: float,
                 venue_heavy_extra: float = 0.0) -> tuple[float, float, float]:
    """Recompute (signed naked, heavy fill cost, unpriced heavy size) for two
    FIXED tokens.

    Never re-derive heavy and light from the post-action ranking. If the light
    leg filled past the heavy one, `after["heavy"]` is the other token, and
    comparing it against a venue reading taken on the original light orders
    mixes two different positions into one subtraction.

    The result is SIGNED and callers must respect the sign. Positive means the
    original heavy token is naked; negative means the original *light* token is,
    by that many shares. Treating a negative as zero would report a pair as
    complete while the other side is exposed.

    `unpriced` is heavy size the venue reports and the registry does not. We
    know it exists but not what it cost, so any decision needing an average
    heavy price is unsafe while it is non-zero.
    """
    legs = after["legs"]
    heavy_leg = legs.get(heavy_token, {"matched": 0.0, "notional": 0.0})
    light_registry = legs.get(light_token, {"matched": 0.0})["matched"] if light_token else 0.0

    heavy_registry = heavy_leg["matched"]
    # The venue figures are *extras* over the registry, computed per order, so
    # they add rather than compete with the leg totals.
    heavy_matched = heavy_registry + venue_heavy_extra
    light_matched = light_registry + venue_light_extra
    fill_cost = (heavy_leg["notional"] / heavy_registry) if heavy_registry > 0 else 0.0
    return heavy_matched - light_matched, fill_cost, venue_heavy_extra


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

    # 1. Cancel every working order on BOTH legs, light first.
    #
    #    The light leg is the obvious one -- selling while it rests lets it fill
    #    into the position we are closing. But a heavy leg sitting at `partial`
    #    still has working size of its own, and leaving that resting re-opens
    #    exposure on the very token we are about to sell. Both must be quiet
    #    before anything is sent.
    light_working = _working_orders(pair["light"])
    heavy_working = _working_orders(pair["heavy"])
    cancelled = _cancel_orders(client, light_working, "resting light leg")
    cancelled += _cancel_orders(client, heavy_working, "working heavy leg")

    # 2. Re-read from the VENUE, not from the registry.
    #
    #    load_pair reads run/live.db, and fills only reach that file through
    #    reconcile_orders in the poll loop -- so a match that completed the light
    #    leg during the cancel is invisible there until the next cycle. Reading
    #    the registry here would confirm what we already believed and sell into
    #    the exact race this step exists to catch.
    venue_light_matched = _venue_extra(client, registry, light_working)
    venue_heavy_matched = _venue_extra(client, registry, heavy_working)
    after = load_pair(registry, pair_id)
    heavy_token = pair["heavy"]["token_id"]
    naked, _, _ = _naked_after(after, heavy_token, light_token,
                               venue_light_matched, venue_heavy_matched)

    if naked < -SIZE_EPS:
        # The light leg overtook the heavy one during the cancel. The position
        # is still one-sided, just on the other token -- so this is neither
        # mergeable nor closed, and selling the heavy token would deepen it.
        raise PairExitRefused(
            f"The light leg {light_token} now exceeds {heavy_token} by "
            f"{-naked:.4f} shares, so the naked side has changed token. This "
            f"exit was invoked for {heavy_token} and will not sell the other "
            f"leg on its own. Re-run the exit against the pair once the "
            f"registry reflects the fill, or close {light_token} deliberately."
        )

    if naked <= SIZE_EPS:
        return {
            "action": "route_to_merge",
            "pair_id": pair_id,
            "condition_id": after["condition_id"],
            "cancelled": cancelled,
            "size": 0.0,
            "venue_light_matched": venue_light_matched,
            "positions_checked": positions_checked,
        }

    # 3. Sell, sized by the registry and bounded by a price we will accept.
    heavy_book = client.get_order_book(heavy_token)
    bid = best_bid(heavy_book)
    if bid is None or bid <= 0:
        raise PairExitRefused(
            f"No bid on {heavy_token}: the resting orders are cancelled and the "
            f"position is naked, but there is nothing to sell into. Retry when "
            f"the book returns."
        )

    # Without a floor the SDK derives the price from the requested amount and
    # will walk the ladder down to whatever clears it. Depth is counted only at
    # levels we would actually accept, so size and price agree.
    tick = _tick_size(heavy_book)
    min_price = _floor_to_tick(max(bid - MAX_SELL_SLIPPAGE, tick), tick)
    depth = depth_at_or_above(heavy_book, min_price)

    size = min(naked, depth)
    if size < MIN_ORDER_SHARES:
        raise PairExitRefused(
            f"Sellable size {size:.4f} is below the venue minimum of "
            f"{MIN_ORDER_SHARES}. Naked {naked:.4f}, depth at or above "
            f"{min_price:.4f} is {depth:.4f}. Best bid is {bid:.4f}; the book "
            f"below the slippage floor was not counted."
        )
    if size > naked + SIZE_EPS:
        # Unreachable by construction. Asserted anyway because an oversell is
        # the one error on this path that cannot be undone.
        raise PairExitRefused(
            f"Refusing to sell {size:.4f} against a holding of {naked:.4f}."
        )

    from py_clob_client_v2.clob_types import MarketOrderArgsV2

    # `amount` is the maker amount: shares on a SELL. `price` is the worst
    # fill we accept, tick-aligned so the venue does not reject it outright.
    resp = client.create_and_post_market_order(
        MarketOrderArgsV2(token_id=heavy_token, amount=size, side="SELL",
                          price=min_price)
    )

    return {
        "action": "exited",
        "pair_id": pair_id,
        "condition_id": after["condition_id"],
        "token_id": heavy_token,
        "size": size,
        "bid": bid,
        "min_price": min_price,
        "cancelled": cancelled,
        "venue_light_matched": venue_light_matched,
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
    if size < MIN_ORDER_SHARES:
        raise PairCompletionRefused(
            f"Completable size {size:.4f} is below the venue minimum of "
            f"{MIN_ORDER_SHARES}. Naked {pair['naked']:.4f}, ask depth "
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

    # Cancel the light leg's own resting BUY before crossing for it.
    #
    # Without this the original maker BUY and the completion taker BUY are both
    # live on the same token, and if both fill the light leg is double-sized --
    # naked exposure on the opposite side, created by the path whose entire
    # purpose is to remove naked exposure.
    # Quiet BOTH legs, as the exit does. A working heavy order left resting
    # keeps filling during and after the cross, so the pair this path just
    # balanced goes one-sided again moments later.
    light_working = _working_orders(pair["light"])
    heavy_working = _working_orders(pair["heavy"])
    cancelled = _cancel_orders(client, light_working, "resting light leg",
                               refusal=PairCompletionRefused)
    cancelled += _cancel_orders(client, heavy_working, "working heavy leg",
                                refusal=PairCompletionRefused)

    # Then re-read from the venue, for the same reason the exit does: the cancel
    # can race a match, and the registry will not know about it until the next
    # poll cycle. If the leg already filled, crossing now would overshoot.
    venue_light_matched = _venue_extra(client, registry, light_working,
                                       refusal=PairCompletionRefused)
    venue_heavy_matched = _venue_extra(client, registry, heavy_working,
                                       refusal=PairCompletionRefused)
    after = load_pair(registry, pair_id)
    heavy_token = pair["heavy"]["token_id"]
    naked, fill_cost_after, unpriced_heavy = _naked_after(
        after, heavy_token, light_token, venue_light_matched,
        venue_heavy_matched,
    )

    if naked < -SIZE_EPS:
        raise PairCompletionRefused(
            f"The light leg {light_token} now exceeds {heavy_token} by "
            f"{-naked:.4f} shares. There is nothing to complete on this side; "
            f"crossing again would deepen the imbalance."
        )

    if unpriced_heavy > SIZE_EPS:
        # We can see the shares but not what they cost: a matched-size read
        # carries no execution price, and the cap is a statement about price.
        # Refusing beats crossing against an average we cannot compute.
        raise PairCompletionRefused(
            f"The venue reports {unpriced_heavy:.4f} more shares of "
            f"{heavy_token} than the registry has priced. The pair-cost cap "
            f"needs an average heavy price and that fill has none yet. Let the "
            f"poll loop reconcile it, then re-run."
        )

    # Re-check the cap against the heavy cost as it stands now. The figure the
    # guard above approved was taken before the cancel, and a heavy order that
    # filled in the meantime at a worse price can push the real pair cost past
    # the cap -- which is precisely the pair this path must never create.
    pair_cost_after = fill_cost_after + ask
    if pair_cost_after >= max_pair_cost:
        raise PairCompletionRefused(
            f"Between the guard and the send, the heavy fill cost moved to "
            f"{fill_cost_after:.4f}; at ask {ask:.4f} the pair would cost "
            f"{pair_cost_after:.4f}, at or above max_pair_cost "
            f"{max_pair_cost:.4f}. Refusing the cross."
        )
    pair_cost = pair_cost_after

    if naked <= SIZE_EPS:
        return {
            "action": "balanced",
            "pair_id": pair_id,
            "condition_id": after["condition_id"],
            "cancelled": cancelled,
            "size": 0.0,
            "venue_light_matched": venue_light_matched,
        }

    if naked < size:
        # Part of the leg filled during the cancel. Complete only the remainder;
        # the original size would buy shares we no longer need.
        size = naked
        notional = size * ask
        if size < MIN_ORDER_SHARES:
            raise PairCompletionRefused(
                f"After the cancel only {size:.4f} shares remain to complete, "
                f"below the venue minimum of {MIN_ORDER_SHARES}."
            )

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
        "cancelled": cancelled,
        "venue_light_matched": venue_light_matched,
        "response": resp,
    }


# --- U35 in the live loop ----------------------------------------------------


def auto_manage_pairs(
    client,
    registry: OrderRegistry,
    cfg,
    *,
    live: bool = True,
    now: Optional[float] = None,
    venue_positions: Optional[dict[str, float]] = None,
    funder: Optional[str] = None,
) -> list[dict]:
    """U35 in the live loop: convert each in-window one-sided fill.

    One pass per poll cycle, run after reconcile so the registry is fresh.
    Discovery is from the fills ledger: every pair that has a fill, whose
    condition has no close, whose last fill is inside `pairs_exit_window_sec`,
    and whose legs are unbalanced. Each such pair is routed exactly like the
    sim's sweep -- complete the missing leg at ask when the pair stays under
    `max_pair_cost`, else same-window exit of the naked leg at the best bid.

    Closing actions only, so the direction gate pre-approves them. Per-pair
    failures are isolated and reported: one pair's refusal never stops the
    cycle. An unreadable Data API positions endpoint fails the pass closed --
    the leg stays naked one more tick and the read is retried next cycle.
    """
    if not getattr(cfg, "enable_pairs_rule", True):
        return []

    now_s = now if now is not None else time.time()
    window_ms = int(getattr(cfg, "pairs_exit_window_sec", 900.0) * 1000)
    max_pair_cost = float(getattr(cfg, "max_pair_cost", 0.995))

    # Same pre-flight the manual exit uses: selling a size the venue does not
    # agree we hold is an oversell. `None` means the caller supplied no view;
    # when we are live we fetch one, and an unreadable endpoint fails the pass
    # closed rather than acting blind.
    if venue_positions is None and live and funder:
        try:
            venue_positions = fetch_positions(funder)
        except Exception as e:
            return [{
                "pair_id": None, "action": "error",
                "error": f"positions read failed: {type(e).__name__}: {e}",
            }]

    closed_cids = {
        r["condition_id"] for r in registry.get_all_closes()
        if r.get("condition_id")
    }

    last_fill_ms: dict[str, int] = {}
    pair_cids: dict[str, str] = {}
    for f in registry.get_all_fills():
        pid = f.get("pair_id")
        if not pid:
            continue
        pair_cids.setdefault(pid, f.get("condition_id") or "")
        vts = f.get("venue_ts")
        if vts:
            last_fill_ms[pid] = max(last_fill_ms.get(pid, 0), int(vts))

    out: list[dict] = []
    for pid, last_ms in last_fill_ms.items():
        if pair_cids.get(pid) in closed_cids:
            continue
        # U35 window: act only while the fill is fresh enough that the measured
        # drift is still ~0. An undated fill (no venue_ts) is left alone --
        # "older than the window is left alone" reads both directions.
        if last_ms <= 0 or (now_s * 1000.0 - last_ms) > window_ms:
            continue
        try:
            pair = load_pair(registry, pid)
            if pair["naked"] <= SIZE_EPS:
                continue
            out.append(_route_pair(
                client, registry, pair, max_pair_cost, live, venue_positions))
        except (PairExitRefused, PairCompletionRefused) as e:
            out.append({"pair_id": pid, "action": "error", "error": str(e)})
        except Exception as e:
            out.append({"pair_id": pid, "action": "error",
                        "error": f"{type(e).__name__}: {e}"})
    return out


def _route_pair(client, registry, pair, max_pair_cost, live,
                venue_positions) -> dict:
    """Complete when the pair stays under the cap, else exit the naked leg.

    The sim's sweep decides the same way: cross the missing leg at ask when
    `fill_cost + ask < max_pair_cost`, otherwise sell the naked leg at the
    best bid. `should_exit` is the ported trigger; the light ask is read once
    to route, and the action functions re-read what they need. If the ask
    moves above the cap between our read and the completion's re-check, the
    completion refuses and the exit owns the case.
    """
    light_token = pair["light"]["token_id"]
    ask = best_ask(client.get_order_book(light_token)) if light_token else None

    if should_exit(pair["fill_cost"], ask, max_pair_cost):
        return exit_naked_leg(client, registry, pair["pair_id"], max_pair_cost,
                              live=live, venue_positions=venue_positions)

    try:
        return complete_pair(client, registry, pair["pair_id"], max_pair_cost,
                             live=live)
    except PairCompletionRefused:
        return exit_naked_leg(client, registry, pair["pair_id"], max_pair_cost,
                              live=live, venue_positions=venue_positions)
