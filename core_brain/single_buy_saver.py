"""Guarded execution paths for closing one-sided pair exposure.

`manage_single_leg_positions` owns the lifecycle policy. This module keeps
completion and exit execution fail-closed because live cancellation, venue
reads, and concurrent fills can turn a correct decision into a worse position.

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

The registry cannot answer that question. Fills reach `data/orders.db` only through
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
import logging
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from typing import Optional

from core_brain.ladder import is_ladder_pair
from core_brain.order_registry import CloseRecord, OrderRegistry, SIZE_EPS

log = logging.getLogger("single_buy_saver")

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
# 3.67c average exit cost measured in the paper run, so a fill worse than this is
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


class ExposureUnavailable(RuntimeError):
    """Open orders or venue state cannot be read to evaluate condition exposure."""



def should_exit(fill_cost: float, light_ask: Optional[float],
                max_pair_cost: float) -> bool:
    """Exit when the pair cannot complete under the cap.

    `>=`, not `>`. A pair costing exactly max_pair_cost is a guaranteed loss
    after gas, which is the whole reason the cap sits below $1.00.

    A missing ask fires rather than holds. No ask means there is nothing to
    complete against, so the leg stays naked for as that is true --
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


def load_condition_exposure(
    client,
    registry: OrderRegistry,
    condition_id: str,
    tokens: tuple[str, str] | list[str],
) -> "ConditionExposure":
    """Load inventory and active working BUY orders to evaluate condition exposure.

    Uses `inventory_from_registry` for share holdings per token.
    Counts active working BUY orders from the registry, requiring venue ID confirmation
    when `client.get_open_orders()` is supported.
    Raises `ExposureUnavailable` if open orders cannot be read.
    """
    from core_brain.order_registry import inventory_from_registry
    from core_brain.single_leg_lifecycle import evaluate_exposure

    if len(tokens) < 2:
        up_token, down_token = (tokens[0], tokens[0]) if tokens else ("", "")
    else:
        up_token, down_token = tokens[0], tokens[1]

    # 1. Holdings
    db_path = getattr(registry, "db_path", getattr(registry, "_db_path", None))
    if db_path is not None:
        inv = inventory_from_registry(condition_id, up_token, down_token, db_path=db_path)
    else:
        inv = inventory_from_registry(condition_id, up_token, down_token)

    held = {
        up_token: float(getattr(inv, "up_shares", 0.0) or 0.0),
        down_token: float(getattr(inv, "down_shares", 0.0) or 0.0),
    }

    # 2. Working BUY orders confirmed by venue
    venue_open_ids: set[str] | None = None
    if client is not None and hasattr(client, "get_open_orders"):
        try:
            raw_orders = client.get_open_orders()
            if raw_orders is None:
                raise ExposureUnavailable("venue get_open_orders returned None")
            venue_open_ids = {
                str(o.get("id") or o.get("order_id") or o.get("orderID") or "")
                for o in raw_orders
                if isinstance(o, dict)
            }
        except Exception as exc:
            raise ExposureUnavailable(f"failed to read open orders from venue: {exc}") from exc

    working_buys: dict[str, float] = {up_token: 0.0, down_token: 0.0}
    active_rows = registry.get_active_orders()
    for row in active_rows:
        if row.condition_id != condition_id:
            continue
        if row.side != "BUY":
            continue
        tok = row.token_id
        if tok not in working_buys:
            continue
        if venue_open_ids is not None and row.order_id:
            if row.order_id not in venue_open_ids:
                continue

        # Remaining working size
        matched = registry.get_size_matched(row.id)
        remaining = max(0.0, float(row.original_size) - matched)
        working_buys[tok] += remaining

    return evaluate_exposure(condition_id, held, working_buys)



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

    The registry cannot answer this question. Fills reach `data/orders.db` only
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
        # Keyed by token as by rank. `heavy` and `light` are a ranking,
        # and a ranking flips: once the light leg fills past the heavy one, a
        # second load_pair call swaps them. Anything comparing a value taken
        # before an action with one taken after must key on the token, not on
        # which side happened to be larger at the time.
        "legs": by_token,
        "naked": naked,
        "fill_cost": fill_cost,
    }


def _prior_exit_shares(registry, condition_id: str, token_id: str) -> float:
    """Shares this token already gave up to single-leg exits on its condition.

    Netting input for the refill-after-exit trap (#326): `load_pair` sizes
    off fills only, so a refill under the same pair_id re-counts shares an
    earlier exit already sold. Only `single_buy_exit`/`naked_exit` closes
    count -- merges and settlements leave through other paths and must not
    discount an exit. Fail-closed 0.0 when the token's side is unresolvable:
    unattributed volume neither shrinks nor grows a later sale.
    """
    if registry is None:
        return 0.0
    side = _token_side(registry, condition_id, token_id)
    if side not in ("UP", "DOWN"):
        return 0.0
    total = 0.0
    for c in registry.get_all_closes():
        if c.get("condition_id") != condition_id:
            continue
        if c.get("method") not in ("single_buy_exit", "naked_exit",
                                      "ladder_exit"):
            continue
        sold_up = c.get("up_price") is not None
        if (sold_up and side == "UP") or (not sold_up and side == "DOWN"):
            try:
                total += float(c.get("shares") or 0.0)
            except (TypeError, ValueError):
                continue
    return total


def _unexplained_divergence(believed: float, observed: float,
                            prior_exited: float) -> float:
    """Divergence no recorded exit accounts for -- the only part that refuses.

    Netting applies solely up to the observed gap, so stale attribution can
    only ever shrink a sale toward the venue view, never grow one past it.
    """
    gap = believed - observed
    if gap <= 0:
        return 0.0
    return max(0.0, gap - max(prior_exited, 0.0))


def _check_positions(pair: dict, venue_positions: Optional[dict[str, float]],
                     registry=None) -> bool:
    """Refuse when the venue does not agree with the registry about holdings.

    Returns whether the check actually ran. `None` means the caller supplied no
    view -- a caller decision, surfaced in the result as positions_checked=False
    rather than silently passing as agreement.

    A gap fully explained by prior single-leg exits on the pair's condition
    (#326) does not refuse: the exit sizing below nets exactly that explained
    part, so the sale still cannot exceed what the venue agrees is held. Any
    unexplained remainder refuses exactly as before.
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
    # Attribution scans the closes/quotes tables, so it runs only when a raw
    # gap exists to explain: with no gap the unexplained part is 0 whatever
    # prior says, and the result would be unused.
    gap = believed - observed
    prior = 0.0
    if gap > POSITION_DIVERGENCE_TOLERANCE and registry is not None:
        prior = _prior_exit_shares(registry, pair["condition_id"], token)
    if _unexplained_divergence(believed, observed, prior) \
            > POSITION_DIVERGENCE_TOLERANCE:
        raise PairExitRefused(
            f"Registry and venue diverge on {token}: registry holds "
            f"{believed:.6f}, Data API reports only {observed:.6f}. Refusing to "
            f"exit -- selling a size the venue does not agree we hold is an "
            f"oversell."
        )
    return True


def _token_side(registry: OrderRegistry, condition_id: str,
                token_id: str) -> Optional[str]:
    """Resolve a token's UP/DOWN label from the quotes ledger.

    The closes table has no token column: a `naked_exit` close records which
    leg was sold by setting `up_price` OR `dn_price`, so the exit needs to
    know whether the sold token is the UP or the DOWN leg. The quotes ledger
    is the in-registry source of that mapping -- every posted order logged a
    quote row carrying its side, which is exactly how the dashboard KPI
    renders UP/DN (kpi.py token_side_map). Returns None when the token was
    never quoted; the caller must fail closed rather than guess, because an
    exit that cannot be recorded is the repeat-sell bug it exists to stop.
    """
    best: Optional[str] = None
    best_ts: float = -1.0
    for q in registry.get_all_quotes():
        if (q.get("condition_id") != condition_id
                or q.get("token_id") != token_id):
            continue
        side = str(q.get("side") or "").upper()
        if side not in ("UP", "DOWN"):
            continue
        ts = float(q.get("ts") or 0.0)
        if ts >= best_ts:
            best_ts = ts
            best = side
    return best


def _exit_fill_price(resp, floor_price: float) -> float:
    """What the exit actually sold at, or the floor when nobody will say.

    The live SDK's market-order response carries no fills, so `floor_price`
    (`best_bid - MAX_SELL_SLIPPAGE`) stays the conservative record there. The
    rehearsal's client DOES know -- it walks the bid ladder -- and booking the
    floor over its answer charged every rehearsed exit a flat 2c concession
    the venue never took. A reported price is the truth in both directions:
    worse than the floor is recorded too.
    """
    if not isinstance(resp, dict):
        return float(floor_price)
    raw = resp.get("price")
    if raw is None:
        return float(floor_price)
    try:
        price = float(raw)
    except (TypeError, ValueError):
        return float(floor_price)
    return price if price > 0 else float(floor_price)


def _exit_fill_size(resp, requested_size: float) -> float:
    """Shares the venue says it sold, bounded by what we asked it to sell.

    A short fill has to reach the ledger as a short fill: recording the
    requested size would retire shares we still hold, and the pair would read
    as closed while a naked leg sat on the venue. Never larger than requested
    -- an oversell is the one error on this path that cannot be undone.
    """
    if not isinstance(resp, dict):
        return float(requested_size)
    raw = resp.get("size")
    if raw is None:
        return float(requested_size)
    try:
        size = float(raw)
    except (TypeError, ValueError):
        return float(requested_size)
    if size <= 0:
        return float(requested_size)
    return min(size, float(requested_size))


def _record_exit_close(registry: OrderRegistry, pair: dict, heavy_token: str,
                       heavy_side: str, size: float, sell_price: float,
                       reason: Optional[str] = None,
                       method: str = "single_buy_exit") -> None:
    """Ledger a completed exit: the sold leg leaves the registry for good.

    Written AFTER the market order succeeds, mirroring the paper run's sweep (which
    logs the close after the walk). The close is the ONLY record of this
    sell -- the venue's trade never lands in the fills table because reconcile
    adopts fills only for orders it knows, and the SELL is a taker order with
    no resting row to attach it to. Without this row the pair reads as still
    held next cycle and the auto pass sells it again: the repeat-sell loop.

    `sell_price` is the price the venue reported, and `min_price` -- the worst
    price we accepted -- only when it reported none. The live SDK's response
    carries no fills, so live still records the floor; the rehearsal walks the
    bid ladder and knows better. See `_exit_fill_price`.

    `reason` is the route's trigger (`adverse_drift` / `grace_expired`), and is
    part of this close -- written here, after the sale succeeded, never before.
    """
    leg = (pair.get("legs") or {}).get(heavy_token, {})
    matched = float(leg.get("matched") or 0.0)
    notional = float(leg.get("notional") or 0.0)
    heavy_avg = (notional / matched) if matched > 0 else 0.0
    cost_basis = size * heavy_avg
    proceeds = size * sell_price
    realized = proceeds - cost_basis
    if heavy_side == "UP":
        up_price, dn_price = sell_price, None
        up_removed, dn_removed = cost_basis, 0.0
    else:
        up_price, dn_price = None, sell_price
        up_removed, dn_removed = 0.0, cost_basis
    close_run_id = None
    paired_context = getattr(registry, "paired_context", None)
    if isinstance(paired_context, dict):
        from core_brain.paired_shadow import validate_paired_pair_attribution
        validate_paired_pair_attribution(
            paired_context["db_path"], run_id=paired_context["run_id"],
            pair_id=str(pair["pair_id"]))
        close_run_id = paired_context["run_id"]
    registry.log_close(CloseRecord(
        ts=time.time(),
        condition_id=pair["condition_id"],
        method=method,
        shares=size,
        up_price=up_price,
        dn_price=dn_price,
        cost_basis=cost_basis,
        proceeds=proceeds,
        realized_pnl=realized,
        # The leg would have paid $1 or $0 at resolution -- unknown, so no
        # forgone figure rather than a guessed one (same as the paper run).
        forgone_vs_settlement=None,
        up_cost_removed=up_removed,
        dn_cost_removed=dn_removed,
        run_id=close_run_id,
        reason=reason,
    ))


def _naked_after(after: dict, heavy_token: str, light_token: Optional[str],
                 venue_light_extra: float,
                 venue_heavy_extra: float = 0.0) -> tuple[float, float, float]:
    """Recompute (signed naked, heavy fill cost, unpriced heavy size) for two
    FIXED tokens.
    """
    legs = after["legs"]
    heavy_leg = legs.get(heavy_token, {"matched": 0.0, "notional": 0.0})
    light_registry = legs.get(light_token, {"matched": 0.0})["matched"] if light_token else 0.0

    heavy_registry = heavy_leg["matched"]
    heavy_matched = heavy_registry + venue_heavy_extra
    light_matched = light_registry + venue_light_extra
    fill_cost = (heavy_leg["notional"] / heavy_registry) if heavy_registry > 0 else 0.0
    return heavy_matched - light_matched, fill_cost, venue_heavy_extra


def exit_single_buy(
    client,
    registry: OrderRegistry,
    pair_id: str,
    max_pair_cost: float,
    live: bool = True,
    venue_positions: Optional[dict[str, float]] = None,
    reason: Optional[str] = None,
    method: str = "single_buy_exit",
    *,
    force: bool = False,
) -> dict:
    """Close a single-sided fill: cancel resting opposite leg, then sell filled inventory.

    `reason` is the caller's route trigger (`adverse_drift` / `grace_expired`),
    persisted on the close so later forensics need not reconstruct it. Purely
    instrumentation: it changes no trigger, threshold, or route decision, and
    callers without one (stray-guard) keep working unchanged.

    `force` skips only the economic preference to complete a profitable pair;
    all cancellation, venue-position, size, depth, and slippage safeguards remain.

    `action` in the returned dict is one of:
      balanced       -- nothing single, nothing to do
      hold           -- the pair still completes under the cap
      would_exit     -- dry run; the trigger fired but nothing was sent
      route_to_merge -- the pair completed between cancel and sell
      exited         -- the single buy was sold

    Raises PairExitRefused on any condition where acting is worse than not
    acting.
    """
    pair = load_pair(registry, pair_id)

    if pair["naked"] <= SIZE_EPS:
        return {"action": "balanced", "pair_id": pair_id, "size": 0.0}

    # Before any venue write: does the venue agree we hold what we think we
    # hold? Checked first so a divergence costs nothing, rather than after a
    # cancel has already gone out.
    positions_checked = _check_positions(pair, venue_positions, registry)

    # The closes table records which leg was sold by setting `up_price` OR
    # `dn_price`, so the exit must know the sold token's side before sending
    # anything. A sell that cannot be recorded is the repeat-sell loop this
    # ledger entry exists to stop -- resolved now, so an unresolvable side
    # costs nothing and refuses rather than selling unrecorded.
    heavy_token = pair["heavy"]["token_id"]
    heavy_side = _token_side(registry, pair["condition_id"], heavy_token)
    if heavy_side is None:
        raise PairExitRefused(
            f"Cannot resolve whether {heavy_token} is the UP or DOWN leg: the "
            f"quotes ledger has no side for it, so the exit could not be "
            f"recorded. Refusing rather than selling a leg the registry would "
            f"never learn was sold."
        )

    light_token = pair["light"]["token_id"]
    light_ask = None
    if not force and light_token:
        light_ask = best_ask(client.get_order_book(light_token))

    if not force and not should_exit(pair["fill_cost"], light_ask, max_pair_cost):
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
    #    load_pair reads data/orders.db, and fills only reach that file through
    #    reconcile_orders in the poll loop -- so a match that completed the light
    #    leg during the cancel is invisible there until the next cycle. Reading
    #    the registry here would confirm what we already believed and sell into
    #    the exact race this step exists to catch.
    venue_light_matched = _venue_extra(client, registry, light_working)
    venue_heavy_matched = _venue_extra(client, registry, heavy_working)
    after = load_pair(registry, pair_id)
    heavy_token = pair["heavy"]["token_id"]
    naked, _, unpriced_heavy = _naked_after(
        after, heavy_token, light_token, venue_light_matched,
        venue_heavy_matched,
    )

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

    # Refill-after-exit (#326): size off what the venue still agrees is
    # held. Prior single-leg exits on this condition explain their share of
    # any gap; only the explained part is netted, so the cap solely shrinks
    # the sale toward venue agreement and can never grow one past it.
    if venue_positions is not None and heavy_token in venue_positions:
        prior = _prior_exit_shares(registry, after["condition_id"],
                                   heavy_token)
        gap = naked - float(venue_positions[heavy_token])
        if gap > 0:
            naked = max(naked - min(gap, max(prior, 0.0)), 0.0)

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

    if unpriced_heavy > SIZE_EPS:
        # The close's cost basis extrapolates the registry's average heavy
        # price onto every share sold, and `naked` already counts these venue
        # shares. We can see them but not what they cost, so recording the
        # close would fabricate an average -- the same refusal `complete_pair`
        # makes in this exact state. The poll loop's reconcile prices the fill,
        # then this exit can re-run.
        raise PairExitRefused(
            f"The venue reports {unpriced_heavy:.4f} more shares of "
            f"{heavy_token} than the registry has priced. The naked_exit "
            f"close needs a real average cost for those shares. Let the "
            f"poll loop reconcile it, then re-run."
        )

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

    # 4. Record the exit BEFORE returning. A sell that exists only on the
    #    venue is invisible to the registry -- reconcile adopts fills only
    #    for orders it knows, and this taker SELL has no resting row, so the
    #    pair would read as still-held next cycle and be sold again (the
    #    repeat-sell loop observed in production). The close is the ledger
    #    entry: `inventory_from_registry` subtracts the sold leg from it, and
    #    the auto pass skips conditions that have a close.
    sold = _exit_fill_size(resp, size)
    fill_price = _exit_fill_price(resp, min_price)
    _record_exit_close(registry, after, heavy_token, heavy_side, sold,
                       fill_price, reason=reason, method=method)

    return {
        "action": "exited",
        "pair_id": pair_id,
        "condition_id": after["condition_id"],
        "token_id": heavy_token,
        "side": heavy_side,
        "size": sold,
        "requested_size": size,
        "bid": bid,
        "fill_price": fill_price,
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
    live: bool = True,
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
    # `depth_at_or_above` reads the bids ladder. This path buys, so it must
    # size against the asks ladder or it will order shares nobody is offering.
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


# --- aged-out legs (the window's complement) ---------------------------------


# Verdicts that are not news: they repeat on every rotation until the condition
# changes, so they reach the cycle ring (where the dashboard counts them) but
# stay out of the console. A long-dated market sits in `awaiting_lead` for
# hours; printing that every five seconds is how an operator stops reading the
# log. `end_unknown` and `venue_closed` stay LOUD -- one is a failing read and
# the other means the position is now settlement's, and both stop on their own.
AGED_OUT_QUIET_ACTIONS = (
    "awaiting_lead", "hold", "balanced", "would_exit", "would_complete",
)

SINGLE_LEG_QUIET_ACTIONS = frozenset({
    "patient_wait", "escalated_wait", "pair_locked", "dual_resting",
    "awaiting_lead", "would_exit", "would_complete",
})

# The verdicts `aged_out_verdict` returns. `due` is the only one that acts.
AGED_OUT_DUE = "due"
AGED_OUT_AWAITING_LEAD = "awaiting_lead"
AGED_OUT_END_UNKNOWN = "end_unknown"
AGED_OUT_VENUE_CLOSED = "venue_closed"
AGED_OUT_NOT_AGED_OUT = "not_aged_out"


def aged_out_verdict(
    *,
    last_fill_ms: int,
    window_ms: int,
    now_s: float,
    end_ts: Optional[float],
    lead_sec: float,
    venue_closed: Optional[bool],
    venue_accepting: Optional[bool],
) -> tuple[str, str]:
    """Should this leg be rescued now, left alone, or refused (Issue #311)?

    `pairs_exit_window_sec` is a *discovery filter*: past it, a naked leg drops
    out of `auto_manage_pairs` and nothing else closes it, so it sits unmanaged
    until settlement pays out whatever the outcome is. This is the decision for
    that complement -- the leg the window deliberately does not reach -- and it
    is market-end aware rather than clock-based, because a wall-clock deadline
    would still fire after the market itself is gone.

    Pure: no registry, no clock, no network. Inputs are primitives so the whole
    fail-closed ladder is testable without a venue.

    Verdicts (the second element is the human reason, and it carries the values
    behind the decision -- #312's legibility rule):

    * `not_aged_out`    -- undated, or still inside the window. The in-window
      route order owns this leg; this arm never touches it.
    * `end_unknown`     -- the venue's own state, or its stated end, could not
      be read. The leg stays naked and the read is retried next rotation. A
      deadline is never invented and a blind close is never sent.
    * `venue_closed`    -- the venue already closed the market (or stopped
      accepting orders). Selling into a closed market is not available, so the
      resolution/settlement path owns the position.
    * `awaiting_lead`   -- genuinely open, with a stated end further away than
      the lead window. Wait.
    * `due`             -- act: either the leg is inside `lead_sec` of the
      venue's stated end, or that end has already passed while the venue still
      accepts orders. The second is the sports case from #312: a live in-play
      market's `endDate` is the kickoff, so there is no future end to wait for
      and the closing phase is now.
    """
    # Undated fills cannot be placed in time. "Older than the window is left
    # alone" reads both directions, exactly as `auto_manage_pairs` reads it.
    if not last_fill_ms or last_fill_ms <= 0:
        return AGED_OUT_NOT_AGED_OUT, "undated fill (no venue_ts); left alone"

    age_s = now_s - (last_fill_ms / 1000.0)
    # Strictly older, matching the discovery filter's `>` so the two arms
    # partition the fills exactly once and no leg belongs to both.
    if age_s <= (window_ms / 1000.0):
        return (AGED_OUT_NOT_AGED_OUT,
                f"inside window ({age_s:.0f}s <= {window_ms / 1000.0:.0f}s)")

    # Fail closed: a missing venue flag is unreadable, never "false". Reading
    # an absent `closed` as live is what #312's review fixed at the gate.
    if venue_closed is None or venue_accepting is None:
        return (AGED_OUT_END_UNKNOWN,
                f"market state unreadable (closed={venue_closed}, "
                f"accepting={venue_accepting})")
    if bool(venue_closed) or not bool(venue_accepting):
        return (AGED_OUT_VENUE_CLOSED,
                f"venue closed the market (closed={bool(venue_closed)}, "
                f"accepting={bool(venue_accepting)}); settlement owns it")

    if end_ts is None:
        return (AGED_OUT_END_UNKNOWN,
                f"market end unreadable (aged out {age_s:.0f}s, no stated end)")

    remaining_s = end_ts - now_s
    if remaining_s <= 0:
        return (AGED_OUT_DUE,
                f"aged out {age_s:.0f}s (window {window_ms / 1000.0:.0f}s); "
                f"stated end passed {abs(remaining_s):.0f}s ago and the venue "
                f"still accepts orders")
    if remaining_s <= lead_sec:
        return (AGED_OUT_DUE,
                f"aged out {age_s:.0f}s (window {window_ms / 1000.0:.0f}s); "
                f"{remaining_s:.0f}s to market end, inside the "
                f"{lead_sec:.0f}s lead")
    return (AGED_OUT_AWAITING_LEAD,
            f"aged out {age_s:.0f}s (window {window_ms / 1000.0:.0f}s); "
            f"{remaining_s:.0f}s to market end, outside the "
            f"{lead_sec:.0f}s lead")


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
    resolved_cids: Optional[set[str]] = None,
    market_state_fn=None,
    state_cache: Optional[AgedOutMarketStateCache] = None,
    gamma_host: Optional[str] = None,
) -> list[dict]:
    """Compatibility adapter for the unified one-sided lifecycle pass."""
    if not getattr(cfg, "enable_pairs_rule", True):
        return []
    return manage_single_leg_positions(
        client, registry, cfg, live=live, now=now,
        venue_positions=venue_positions, funder=funder,
        market_state_fn=market_state_fn, state_cache=state_cache,
        gamma_host=gamma_host, resolved_cids=resolved_cids,
    )


@contextmanager
def _completion_target(client, pair_id: str):
    """Name the pair the next completion BUY belongs to, when the client cares (#311).

    A venue market order carries no pair id -- only token, side, size, price --
    so the rehearsal's execution shim has to reconstruct which pair a
    completion buy belongs to, and its inference prefers an in-window naked
    pair on that token. That preference is right for the in-window pass and
    wrong for this arm: the pair being completed here has its last fill
    OUTSIDE the window by construction, so when a freshly re-quoted pair is
    also naked on the token, the fresh one wins the fill -- the pair we
    actually crossed for still reads naked and is bought a second time next
    rotation. The caller that knows the pair says so instead.

    Cleared on the way out whatever happens: a cross that refused or raised
    must not leave a target aimed at some later, unrelated completion. The
    live CLOB client has no such method, so live is untouched by this -- the
    binding is a no-op there.
    """
    bind = getattr(client, "bind_completion_target", None)
    if not callable(bind):
        yield
        return
    bind(str(pair_id))
    try:
        yield
    finally:
        bind(None)


def _latest_close_ms_by_condition(registry) -> dict[str, int]:
    """When each condition was last closed, in venue milliseconds.

    A close covers only the fills that PREDATE it. The old condition-level
    skip meant one close on a condition permanently disabled the rule for that
    condition -- so a market exited once would never be managed again even if
    the fleet later re-quoted it and took a new one-sided fill. The paper run
    has no such flag: its rule keys off fill age, and a fresh fill re-arms the
    window. Mirror that: a pair whose last fill is OLDER than the condition's
    latest close was the position that close sold (or merged) -- skip it. A
    pair filled after the close is new exposure and must be managed like any
    other.

    Shared by both arms on purpose: "never sell a leg the ledger says is
    already gone" has exactly one definition.
    """
    latest_close_ms: dict[str, int] = {}
    for r in registry.get_all_closes():
        cid = r.get("condition_id")
        ts = r.get("ts")
        if not cid or not ts:
            continue
        try:
            ts_ms = int(float(ts) * 1000.0)
        except (TypeError, ValueError):
            continue
        if ts_ms > latest_close_ms.get(cid, 0):
            latest_close_ms[cid] = ts_ms
    return latest_close_ms


def _last_fill_ms_by_pair(registry) -> tuple[dict[str, int], dict[str, str]]:
    """The newest fill time per pair, and the condition each pair belongs to.

    Discovery for both arms comes from the fills ledger. An undated fill (no
    `venue_ts`) has no entry and is left alone by both.
    """
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
    return last_fill_ms, pair_cids


# --- the aged-out arm (#311) -------------------------------------------------


# How long a parsed market state is reused by the aged-out pass. `poll` defaults
# to `--interval 0.5`, so an arm reading the venue on every cycle spends
# thousands of Gamma calls an hour per waiting leg on a fact -- the stated end
# of a market -- that moves on the scale of hours, and every one of those reads
# is synchronous, delaying the heartbeat and the next cycle. Thirty seconds of
# staleness costs at most thirty seconds of a 900s lead.
DEFAULT_AGED_OUT_STATE_TTL_SEC = 30.0

# When the cached deadline is at least this far beyond `end_ts - lead_sec`, no
# read this cycle could change the verdict, so none is made until the deadline
# gets close enough to matter. `awaiting_lead` is the long-dated case, and it is
# the one that would otherwise be read forever.
AGED_OUT_STATE_SKIP_MARGIN_SEC = 300.0


class AgedOutMarketStateCache:
    """One venue read per condition per TTL, shared across cycles (#311).

    Explicit rather than module state on purpose: whoever owns the clock owns
    the entries, so a cache cannot outlive the run it was built for and answer
    for another. `rescue_aged_out_legs` builds its own when it is not handed
    one, which still keeps a single pass from reading one condition once per
    naked pair.

    Two answers are never cached, both because caching them would turn a
    transient failure into a standing decision:

    * `unreachable=True` -- a failed read is not knowledge. The leg stays naked
      and the next cycle retries the venue, which is the fail-closed direction;
    * `None` -- the market is not in the venue's open listing. That is a state
      to re-ask about, not one to remember.
    """

    def __init__(
        self,
        ttl_sec: float = DEFAULT_AGED_OUT_STATE_TTL_SEC,
        skip_margin_sec: float = AGED_OUT_STATE_SKIP_MARGIN_SEC,
    ) -> None:
        self._ttl_sec = max(0.0, float(ttl_sec))
        self._skip_margin_sec = max(0.0, float(skip_margin_sec))
        self._entries: dict[str, tuple[float, object]] = {}

    def state_for(self, condition_id: str, fetch, *, now_s: float,
                  lead_sec: float):
        """The market state for `condition_id`, reading the venue at most once
        per TTL.

        `fetch` is the reader itself, so a rehearsal or a test injects its own
        and no caller has to know which one is in use.
        """
        hit = self._entries.get(condition_id)
        if hit is not None:
            fetched_at, state = hit
            if (now_s - fetched_at) < self._ttl_sec:
                return state
            if self._deadline_is_far(state, now_s=now_s, lead_sec=lead_sec):
                return state
        state = fetch(condition_id)
        if state is not None and not getattr(state, "unreachable", False):
            self._entries[condition_id] = (now_s, state)
        return state

    def _deadline_is_far(self, state, *, now_s: float, lead_sec: float) -> bool:
        """True when the cached end cannot change this cycle's verdict.

        Only a stated end counts. A market with no end on record, or one whose
        stated end has already passed (#312: for sports that is the kickoff,
        not the close of trading), is re-read on the plain TTL schedule -- the
        cases we cannot reason about are the ones that stay fresh.
        """
        end_ts = getattr(state, "end_ts", None)
        if end_ts is None:
            return False
        return (float(end_ts) - float(lead_sec) - now_s) > self._skip_margin_sec


def manage_single_leg_positions(
    client,
    registry: OrderRegistry,
    cfg,
    *,
    live: bool = True,
    now: Optional[float] = None,
    venue_positions: Optional[dict[str, float]] = None,
    funder: Optional[str] = None,
    market_state_fn=None,
    state_cache: Optional[AgedOutMarketStateCache] = None,
    gamma_host: Optional[str] = None,
    resolved_cids: Optional[set[str]] = None,
) -> list[dict]:
    """Apply the shared lifecycle policy to every active one-sided pair.

    Ordinary exposure waits for its resting hedge; an escalated hedge is
    managed by the Trader's quote path. The poll owns only the hard-stop sell
    and the existing market-end-aware settlement fallback.
    """
    pairs_enabled = bool(getattr(cfg, "enable_pairs_rule", True))
    aged_out_enabled = bool(getattr(cfg, "enable_aged_out_rescue", True))
    if not pairs_enabled and not aged_out_enabled:
        return []

    from core_brain.quotes import dynamic_offset_for
    from core_brain.single_leg_lifecycle import (
        LegState, SingleLegPosition, evaluate, persist_decision, transition,
    )

    now_s = now if now is not None else time.time()
    window_ms = int(getattr(cfg, "pairs_exit_window_sec", 900.0) * 1000)
    lead_sec = float(getattr(cfg, "aged_out_rescue_lead_sec", 900.0))
    max_pair_cost = float(getattr(cfg, "max_pair_cost", 0.995))
    tick_size = float(getattr(cfg, "price_tick", DEFAULT_TICK_SIZE))
    resolved = {str(cid).lower() for cid in (resolved_cids or ()) if cid}

    if market_state_fn is None:
        from core_brain.market_resolution import (
            DEFAULT_GAMMA_HOST, fetch_open_market_state,
        )
        host = gamma_host or DEFAULT_GAMMA_HOST
        market_state_fn = lambda cid: fetch_open_market_state(host, cid)
    if state_cache is None:
        state_cache = AgedOutMarketStateCache()

    if venue_positions is None and live and funder:
        try:
            venue_positions = fetch_positions(funder)
        except Exception as exc:
            return [{
                "pair_id": None, "action": "error",
                "error": f"positions read failed: {type(exc).__name__}: {exc}",
            }]

    latest_close_ms = _latest_close_ms_by_condition(registry)
    last_fill_ms_by_pair, pair_cids = _last_fill_ms_by_pair(registry)
    base_offset = float(dynamic_offset_for(cfg)[0])
    quoted_tokens: dict[str, dict[str, tuple[float, str]]] = {}
    for quote in registry.get_all_quotes():
        cid = str(quote.get("condition_id") or "")
        side = str(quote.get("side") or "").upper()
        token_id = str(quote.get("token_id") or "")
        if not cid or not token_id or side not in ("UP", "DOWN"):
            continue
        ts = float(quote.get("ts") or 0.0)
        prior = quoted_tokens.setdefault(cid, {}).get(side)
        if prior is None or ts >= prior[0]:
            quoted_tokens[cid][side] = (ts, token_id)
    out: list[dict] = []
    skipped_resolved = 0

    for pair_id, last_fill_ms in last_fill_ms_by_pair.items():
        condition_id = pair_cids.get(pair_id)
        if condition_id and condition_id.lower() in resolved:
            skipped_resolved += 1
            continue
        if (condition_id
                and last_fill_ms <= latest_close_ms.get(condition_id, 0)):
            continue

        is_aged_out = (
            last_fill_ms > 0
            and (now_s * 1000.0 - last_fill_ms) > window_ms
        )
        if not pairs_enabled and not (is_aged_out and aged_out_enabled):
            continue

        try:
            pair = load_pair(registry, pair_id)
            if pair["naked"] <= SIZE_EPS:
                continue

            if is_ladder_pair(pair_id):
                # Ladder-stamped pairs bypass the shared lifecycle and keep
                # the pre-#413 `_route_pair` semantics: under `ladder_mode`
                # one-shot rungs rest until `ladder_exit_sec`, then leave
                # with the `ladder_exit` method; without it they answer to
                # the ordinary grace timer with `single_buy_exit`. The
                # unified routing retired the `_route_pair` call; this
                # restores it for ladder-stamped pairs only.
                out.append(_route_pair(
                    client, registry, pair, max_pair_cost, live,
                    venue_positions, cfg=cfg, last_ms=last_fill_ms,
                    now_s=now_s,
                ))
                continue

            pair_side_tokens: dict[str, list[str]] = {}
            for token_id in pair["legs"]:
                side = _token_side(registry, pair["condition_id"], token_id)
                if side in ("UP", "DOWN"):
                    pair_side_tokens.setdefault(side, []).append(token_id)
            if any(len(tokens) > 1 for tokens in pair_side_tokens.values()):
                raise PairExitRefused(
                    f"pair_id={pair_id!r} maps multiple tokens to one outcome"
                )
            side_tokens = {
                side: tokens[0]
                for side, tokens in pair_side_tokens.items()
            }
            for side in ("UP", "DOWN"):
                if side not in side_tokens:
                    token = quoted_tokens.get(pair["condition_id"], {}).get(side)
                    if token is not None:
                        side_tokens[side] = token[1]
            if set(side_tokens) != {"UP", "DOWN"}:
                raise PairExitRefused(
                    f"pair_id={pair_id!r} does not identify one UP token and "
                    "one DOWN token in the quotes ledger"
                )

            empty_leg = {"matched": 0.0, "notional": 0.0}
            up_leg = pair["legs"].get(side_tokens["UP"], empty_leg)
            down_leg = pair["legs"].get(side_tokens["DOWN"], empty_leg)
            up_size = float(up_leg["matched"])
            down_size = float(down_leg["matched"])
            held_token = (
                side_tokens["UP"] if up_size > down_size else side_tokens["DOWN"]
            )
            held_book = client.get_order_book(held_token)
            held_bid = best_bid(held_book)
            up_bid = held_bid if held_token == side_tokens["UP"] else None
            down_bid = held_bid if held_token == side_tokens["DOWN"] else None

            position = SingleLegPosition(
                pair_id=pair_id,
                condition_id=pair["condition_id"],
                up_token_id=side_tokens["UP"],
                down_token_id=side_tokens["DOWN"],
                up_size=up_size,
                down_size=down_size,
                up_avg_price=(
                    float(up_leg["notional"]) / up_size if up_size > 0 else 0.0
                ),
                down_avg_price=(
                    float(down_leg["notional"]) / down_size
                    if down_size > 0 else 0.0
                ),
                up_best_bid=up_bid,
                down_best_bid=down_bid,
                base_offset=base_offset,
            )
            # The poll loop owns only the hard-stop and settlement-fallback
            # decisions. Escalation is the Trader's to own: computing it here
            # with `transition` (pure) never persists a Trader-owned state, so
            # the two processes cannot overwrite each other's row or disagree
            # about the threshold via a different base offset.
            previous_record = registry.get_lifecycle_state(pair_id)
            if (previous_record is not None
                    and previous_record.condition_id != position.condition_id):
                raise PairExitRefused(
                    f"pair_id={pair_id!r} lifecycle state belongs to condition "
                    f"{previous_record.condition_id!r}, not "
                    f"{position.condition_id!r}"
                )
            previous_state = (
                LegState(previous_record.state)
                if previous_record is not None else None
            )
            decision = transition(
                position, previous_state, max_pair_cost=max_pair_cost,
                settlement_due=False, tick_size=tick_size,
            )
            if decision.action == "hard_stop":
                # The poll's hard stop is its own to persist: it survives
                # restart and is observable, and only the poll executes it.
                persist_decision(
                    registry, position, decision, previous_state,
                )
            if decision.action == "hard_stop":
                result = exit_single_buy(
                    client, registry, pair_id, max_pair_cost,
                    live=live, venue_positions=venue_positions,
                    reason="lifecycle_hard_stop", force=True,
                )
                result.update(
                    route="hard_stop", lifecycle_state=decision.state.value,
                    reason=decision.reason,
                )
                out.append(result)
                continue

            if (is_aged_out and aged_out_enabled and last_fill_ms > 0
                    and condition_id):
                state = state_cache.state_for(
                    condition_id, market_state_fn, now_s=now_s,
                    lead_sec=lead_sec,
                )
                verdict, reason = aged_out_verdict(
                    last_fill_ms=last_fill_ms, window_ms=window_ms,
                    now_s=now_s, end_ts=getattr(state, "end_ts", None),
                    lead_sec=lead_sec,
                    venue_closed=getattr(state, "closed", None),
                    venue_accepting=getattr(state, "accepting_orders", None),
                )
                if verdict == AGED_OUT_DUE:
                    decision = evaluate(
                        position, registry, max_pair_cost=max_pair_cost,
                        settlement_due=True, tick_size=tick_size,
                    )
                    result = _settlement_fallback(
                        client, registry, pair, cfg, max_pair_cost, live,
                        venue_positions, reason,
                    )
                    if result.get("action") in ("completed", "exited"):
                        result["action"] = "aged_out_rescue"
                    result.update(
                        lifecycle_stage="settlement_fallback",
                        lifecycle_state=decision.state.value,
                        settlement_reason=reason,
                    )
                    out.append(result)
                    continue
                if verdict != AGED_OUT_NOT_AGED_OUT:
                    out.append({
                        "pair_id": pair_id, "condition_id": condition_id,
                        "action": verdict, "lifecycle_state": decision.state.value,
                        "reason": reason,
                    })
                    continue

            action = {
                "wait": "patient_wait",
                "hedge": "escalated_wait",
                "locked": "pair_locked",
                "rest": "dual_resting",
                "refused": "refused",
            }.get(decision.action, decision.action)
            out.append({
                "pair_id": pair_id, "condition_id": condition_id,
                "action": action, "lifecycle_state": decision.state.value,
                "reason": decision.reason,
            })
        except (PairExitRefused, PairCompletionRefused) as exc:
            out.append({
                "pair_id": pair_id, "condition_id": condition_id,
                "action": "error", "error": str(exc),
            })
        except Exception as exc:
            out.append({
                "pair_id": pair_id, "condition_id": condition_id,
                "action": "error",
                "error": f"{type(exc).__name__}: {exc}",
            })

    if skipped_resolved:
        log.info(
            "single-leg lifecycle: %d pair(s) on resolved markets skipped "
            "(reason=resolved; inventory preserved)", skipped_resolved,
        )
    return out


def _settlement_fallback(client, registry, pair, cfg, max_pair_cost, live,
                         venue_positions, reason: str) -> dict:
    """Try one capped completion at settlement time, then use guarded exit."""
    try:
        with _completion_target(client, pair["pair_id"]):
            result = complete_pair(
                client, registry, pair["pair_id"], max_pair_cost, live=live,
                max_order_usd=getattr(cfg, "max_order_usd", None) or 25.0,
            )
    except PairCompletionRefused:
        result = exit_single_buy(
            client, registry, pair["pair_id"], max_pair_cost,
            live=live, venue_positions=venue_positions,
            reason="aged_out_rescue", force=True,
        )
        result["route"] = "exited"
    else:
        result["route"] = "completed"
    result["reason"] = reason
    return result


def rescue_aged_out_legs(
    client,
    registry: OrderRegistry,
    cfg,
    *,
    live: bool = True,
    now: Optional[float] = None,
    venue_positions: Optional[dict[str, float]] = None,
    funder: Optional[str] = None,
    market_state_fn=None,
    state_cache: Optional[AgedOutMarketStateCache] = None,
    gamma_host: Optional[str] = None,
    resolved_cids: Optional[set[str]] = None,
) -> list[dict]:
    """Compatibility adapter for lifecycle-managed settlement fallback."""
    if not getattr(cfg, "enable_aged_out_rescue", True):
        return []
    return manage_single_leg_positions(
        client, registry, cfg, live=live, now=now,
        venue_positions=venue_positions, funder=funder,
        market_state_fn=market_state_fn, state_cache=state_cache,
        gamma_host=gamma_host, resolved_cids=resolved_cids,
    )


def _route_pair(client, registry, pair, max_pair_cost, live,
                venue_positions, cfg=None, last_ms: int = 0, now_s: float = 0.0) -> dict:
    """Route a single-buy pair under the Dual-Trigger Stop-Loss rules:

    1. TAKER FINISH: If fill_cost + light_ask < max_pair_cost, cross to complete immediately.
    2. ADVERSE DRIFT TRIGGER: If heavy leg's best bid dropped by >= single_buy_max_loss_usd
       or >= single_buy_max_loss_pct, exit immediately to stop fast runaway bleed.
    3. GRACE WINDOW: If age < single_buy_grace_sec (default 45s) and price is stable,
       hold and let the resting maker quote fill.
    4. GRACE EXPIRY: If age >= single_buy_grace_sec, exit at best bid to prevent holding
       an unhedged leg into settlement.
    """
    light_token = pair["light"]["token_id"]
    ask = best_ask(client.get_order_book(light_token)) if light_token else None

    # Check 1: Can we complete the pair profitably as taker right now?
    if not should_exit(pair["fill_cost"], ask, max_pair_cost):
        try:
            max_order = getattr(cfg, "max_order_usd", None) if cfg else None
            return complete_pair(client, registry, pair["pair_id"], max_pair_cost,
                                 live=live, max_order_usd=max_order)
        except PairCompletionRefused:
            pass  # Fall through to dual-trigger check

    # Check 2: Evaluate adverse price drift on the heavy leg
    heavy_token = pair["heavy"]["token_id"]
    heavy_fill_price = float(pair["fill_cost"])
    heavy_book = client.get_order_book(heavy_token) if heavy_token else None
    heavy_bid = best_bid(heavy_book) if heavy_book else None

    window_sec = float(getattr(cfg, "pairs_exit_window_sec", 900.0) if cfg else 900.0)
    raw_grace = float(getattr(cfg, "single_buy_grace_sec", 45.0) if cfg else 45.0)
    grace_sec = min(raw_grace, window_sec)
    # Ladder pairs answer to the ladder timer, not single grace: one-shot
    # rungs rest until `ladder_exit_sec`, then leave with the ladder_exit
    # method. Adverse drift above still exits immediately -- the timer is
    # patience, not permission to bleed.
    is_ladder = is_ladder_pair(pair["pair_id"]) \
        and bool(getattr(cfg, "ladder_mode", False))
    exit_method = "ladder_exit" if is_ladder else "single_buy_exit"
    if is_ladder:
        grace_sec = min(float(getattr(cfg, "ladder_exit_sec", 60.0)),
                        window_sec)
    max_loss_pct = float(getattr(cfg, "single_buy_max_loss_pct", 0.10) if cfg else 0.10)
    max_loss_usd = float(getattr(cfg, "single_buy_max_loss_usd", 0.045) if cfg else 0.045)

    age_s = max(0.0, now_s - (last_ms / 1000.0)) if (last_ms > 0 and now_s > 0) else 0.0

    is_adverse = False
    if heavy_bid is not None and heavy_fill_price > 0:
        loss = heavy_fill_price - heavy_bid
        loss_pct = loss / heavy_fill_price
        if loss >= max_loss_usd or loss_pct >= max_loss_pct:
            is_adverse = True

    if is_adverse:
        res = exit_single_buy(client, registry, pair["pair_id"], max_pair_cost,
                              live=live, venue_positions=venue_positions,
                              reason="adverse_drift", method=exit_method)
        if isinstance(res, dict):
            res["reason"] = "adverse_drift"
        return res

    if age_s < grace_sec:
        return {
            "pair_id": pair["pair_id"],
            "action": "holding_grace",
            "reason": f"grace_active ({age_s:.1f}s < {grace_sec:.0f}s)",
            "fill_price": heavy_fill_price,
            "current_bid": heavy_bid,
        }

    timer_reason = "ladder_exit" if is_ladder else "grace_expired"
    res = exit_single_buy(client, registry, pair["pair_id"], max_pair_cost,
                          live=live, venue_positions=venue_positions,
                          reason=timer_reason, method=exit_method)
    if isinstance(res, dict):
        res["reason"] = timer_reason
    return res


# Backward-compatible alias
exit_naked_leg = exit_single_buy
