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
import sqlite3
import time
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from typing import Optional

from core_brain.book_math import (
    DEFAULT_TICK_SIZE,
    ask_depth_at_or_below,
    best_ask,
    best_bid,
    bid_depth,
    book_levels,
    depth_at_or_above,
    floor_to_tick,
    tick_size,
)
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

# Page size for the Data API positions read.
POSITIONS_PAGE_SIZE: int = 500

# Shadow rescue exits pay no taker fee: the rehearsal client sells into its own
# simulated book, so there is no venue to charge one. Recorded on shadow rescue
# closes only — live taker fees are unknown at sell time and stay NULL
# (unmeasured), never an invented zero.
ZERO_FEE: float = 0.0

# The one invariant that makes a single logical owner real: the whole
# venue-mutating lifecycle -- decide -> cancel -> re-read -> sell/complete --
# runs under exactly one process-wide lock, so two executors (the rotation
# sweep thread and the <=1 Hz risk-guardian thread, both wired to
# `manage_single_leg_positions`) can NEVER overlap on the same pair.
#
# RLock, not Lock: the guardian is often the OUTER caller and the rotation
# sweep the inner one, or vice versa, and a nested acquire on a plain Lock
# would dead-lock the second holder against itself. Reentrancy lets a single
# logical pass run start to finish while any other executor blocks at the
# first acquire until the pass is done -- which is the whole guarantee.
#
# This is a backstop, not a substitute for the defense-only scoping in #488:
# the lock makes overlap IMPOSSIBLE by construction, so a future mis-wiring
# that hands the full sweep to a second thread degrades to serialised
# one-at-a-time execution instead of the double cancel/exit/sell corruption.
_LIFECYCLE_LOCK = threading.RLock()


def hold_lifecycle_lock() -> bool:
    """Whether the caller already holds the lifecycle lock.

    Used to distinguish a genuine double-executor overlap (two independent
    holders) from a legitimate nested call by the same logical pass. A nested
    call re-acquires a reentrant lock, so the pass runs end to end; only a
    separate owner contends here.
    """
    acquired = _LIFECYCLE_LOCK.acquire(blocking=False)
    if acquired:
        _LIFECYCLE_LOCK.release()
    return not acquired


@contextmanager
def lifecycle_mutation_guard():
    """Serialise every venue-mutating lifecycle pass, process-wide.

    Held for the whole of `manage_single_leg_positions` so the decide ->
    cancel -> re-read-venue -> sell/complete sequence is atomic against any
    other executor of the same registry. Without it the rotation thread and
    the guardian thread each read the book at a different instant and one
    prices an exit the other already moved -- the double-execution corruption
    this lock exists to make impossible.
    """
    with _LIFECYCLE_LOCK:
        yield


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
                bot_pos_price_max_pair_cost_usd: float) -> bool:
    """Exit when the pair cannot complete under the cap.

    `>=`, not `>`. A pair costing exactly bot_pos_price_max_pair_cost_usd is a guaranteed loss
    after gas, which is the whole reason the cap sits below $1.00.

    A missing ask fires rather than holds. No ask means there is nothing to
    complete against, so the leg stays naked for as that is true --
    holding on the hope that a quote appears is the position this rule exists
    to close.
    """
    if light_ask is None or light_ask <= 0:
        return True
    return (fill_cost + light_ask) >= bot_pos_price_max_pair_cost_usd


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


def fetch_token_balances(funder: str, up_token_id: str, down_token_id: str,
                         timeout: float = 10.0) -> tuple[float, float]:
    """Strict two-token balance read for the reset proof (#646).

    Same Data API pages as `fetch_positions`, but strict: any failed page,
    non-list payload, or malformed row raises `ExposureUnavailable` instead
    of being skipped. A token absent from a complete, successful read is an
    explicit zero -- the API omits tokens no longer held, and only a full
    read earns the right to say so.
    """
    balances: dict[str, float] = {}
    offset = 0
    try:
        while True:
            url = (
                f"{DATA_API_BASE}/positions?user={funder}"
                f"&sizeThreshold=0&limit={POSITIONS_PAGE_SIZE}&offset={offset}"
            )
            req = urllib.request.Request(url, headers={"User-Agent": "spread-hunter"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            if not isinstance(payload, list):
                raise ExposureUnavailable(
                    f"token balance read returned {type(payload).__name__}, not a list")
            for row in payload:
                if not isinstance(row, dict):
                    raise ExposureUnavailable(
                        f"token balance row is {type(row).__name__}, not an object")
                token = str(row.get("asset") or row.get("tokenId")
                            or row.get("token_id") or "")
                if not token:
                    raise ExposureUnavailable("token balance row carries no token id")
                try:
                    balances[token] = float(row.get("size", 0.0) or 0.0)
                except (TypeError, ValueError) as exc:
                    raise ExposureUnavailable(
                        f"token balance size for {token!r} is not numeric") from exc
            if len(payload) < POSITIONS_PAGE_SIZE:
                break
            offset += POSITIONS_PAGE_SIZE
    except ExposureUnavailable:
        raise
    except Exception as exc:
        # Translation boundary: the HTTP/JSON stack raises whatever it
        # raises; the reset proof only understands ExposureUnavailable.
        # Nothing is swallowed -- the failure still blocks the reset.
        raise ExposureUnavailable(f"token balance read failed: {exc}") from exc
    return (balances.get(up_token_id, 0.0), balances.get(down_token_id, 0.0))


# Shadow-side reset-port factory, registered by the shadow store module on
# import (#646). The live fill engine must never import the shadow model --
# `test_no_live_module_imports_the_shadow_model` scans for it textually -- so
# the shadow side plugs its factory in here instead of being imported.
# None means shadow resets are unavailable: stop exits and retries in
# shadow mode stay managed, the safe direction.
_SHADOW_RESET_PORTS_FACTORY = None


def register_shadow_reset_ports(factory) -> None:
    """Plug in the shadow store's `ResetPorts` factory (called once on its import)."""
    global _SHADOW_RESET_PORTS_FACTORY
    _SHADOW_RESET_PORTS_FACTORY = factory


def _shadow_reset_ports_or_none(registry, db_path, condition_id,
                                up_token_id, down_token_id):
    """Shadow `ResetPorts` when the factory is registered, else None."""
    factory = _SHADOW_RESET_PORTS_FACTORY
    if factory is None:
        return None
    return factory(registry, db_path, condition_id, up_token_id, down_token_id)


def live_reset_ports(client, funder: str, registry: OrderRegistry,
                     condition_id: str, up_token_id: str,
                     down_token_id: str):
    """`ResetPorts` over the live venue and registry (#646).

    Imported lazily by callers: `lifecycle_reset` imports this module at top
    level, so a top-level import back would be a cycle.
    """
    from core_brain.lifecycle_reset import ResetPorts
    from core_brain.order_registry import (
        ReconcileInProgress,
        inventory_from_registry,
        reconcile_orders,
    )

    cid = str(condition_id or "").lower()
    db_path = getattr(registry, "db_path", None)

    def _active_rows() -> list:
        return [r for r in registry.get_active_orders()
                if str(r.condition_id or "").lower() == cid and r.order_id]

    def _read_inventory() -> tuple[float, float]:
        inv = inventory_from_registry(
            condition_id, up_token_id, down_token_id,
            **({"db_path": db_path} if db_path is not None else {}))
        return (float(inv.up_shares or 0.0), float(inv.down_shares or 0.0))

    def _watermark() -> tuple:
        return registry.fill_watermark(condition_id)

    def _active_order_ids() -> list[str]:
        return [str(r.order_id) for r in _active_rows()]

    def _cancel(order_ids: list[str]) -> None:
        wanted = set(map(str, order_ids))
        rows = [r for r in _active_rows() if str(r.order_id) in wanted]
        _cancel_orders(client, rows, f"reset {condition_id}")

    def _verify_cancelled() -> bool:
        # An unreadable book is not an empty book: exception or None reads
        # as "still resting", which refuses the reset rather than proving it.
        if not hasattr(client, "get_open_orders"):
            return False
        try:
            raw = client.get_open_orders()
        except Exception:
            return False
        if raw is None:
            return False
        venue_ids = {str(o.get("id") or o.get("order_id") or o.get("orderID") or "")
                     for o in raw if isinstance(o, dict)}
        known = {str(r.order_id) for r in _active_rows()}
        return not (known & venue_ids)

    def _reconcile_fills() -> None:
        try:
            reconcile_orders(client, registry, maker_address=funder)
        except (ReconcileInProgress, PermissionError) as exc:
            raise ExposureUnavailable(
                f"cannot reconcile before reset: {exc}") from exc
        except sqlite3.Error:
            raise
        except Exception as exc:
            # Port contract: only PairExitRefused, ExposureUnavailable and
            # sqlite3.Error cross into the proof. A venue SDK failure
            # during the reread fails the reset closed, never escapes it.
            raise ExposureUnavailable(
                f"cannot reconcile before reset: {exc}") from exc

    def _read_balances() -> tuple[float, float]:
        return fetch_token_balances(funder, up_token_id, down_token_id)

    return ResetPorts(
        read_inventory=_read_inventory,
        fill_watermark=_watermark,
        active_order_ids=_active_order_ids,
        cancel=_cancel,
        verify_cancelled=_verify_cancelled,
        reconcile_fills=_reconcile_fills,
        read_balances=_read_balances,
    )


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


def load_pair(registry: OrderRegistry, pair_id: str) -> dict:
    """Reduce a pair's rows to the numbers the rule needs.

    Sizes come from the fills, never from the intended size: an order that
    filled 4 of 10 is a 4-share position, and sizing an exit off 10 would sell
    shares that do not exist.

    Fills are netted against the pair's own already-booked rescue closes
    (#795): a pair that sold all 10 reads zero naked, so the stop path never
    fires for it again. The rescue totals copy the `rescue_net` rule in
    `registry_naked_usd` — same scope, restricted to this pair. Tokens with
    no quote-side mapping net nothing (fail closed; #793 owns that gap).
    """
    rows, rescued = registry.get_pair_orders_with_rescue_totals(pair_id)
    if not rows:
        raise PairExitRefused(f"No orders carry pair_id={pair_id!r}.")

    # The rescue map is already pair-scoped, so every entry belongs to this
    # pair; summing per token matches how the fills above aggregate per token.
    rescued_by_token: dict[str, dict] = {}
    for (_, token_id), totals in rescued.items():
        slot = rescued_by_token.setdefault(
            token_id, {"shares": 0.0, "cost": 0.0})
        slot["shares"] += totals["shares"]
        slot["cost"] += totals["cost"]

    by_token: dict[str, dict] = {}
    for o, matched, notional in rows:
        slot = by_token.setdefault(
            o.token_id,
            {"token_id": o.token_id, "matched": 0.0, "notional": 0.0, "orders": []},
        )
        slot["matched"] += matched
        slot["notional"] += notional
        slot["orders"].append(o)

    for token_id, slot in by_token.items():
        net = rescued_by_token.get(token_id)
        if net is None:
            continue
        slot["matched"] = max(0.0, slot["matched"] - net["shares"])
        slot["notional"] = max(0.0, slot["notional"] - net["cost"])
        if slot["matched"] <= SIZE_EPS:
            # Cost with no shares has no meaning; without this a fully
            # exited leg keeps a stale average that prices the next sale.
            slot["notional"] = 0.0

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
        "condition_id": rows[0][0].condition_id,
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

    The pair view is already netted against the pair's own rescue closes
    (#795), so `believed` is remaining shares: any gap to the venue is
    unexplained by construction and refuses exactly as before.
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
    # The inventory is netted at the source (#795), so there is no prior
    # credit left to consult: a second subtraction here would hide a real
    # shortfall. The backstop stays exact on the netted numbers.
    if _unexplained_divergence(believed, observed, 0.0) \
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
    return registry.get_token_side(condition_id, token_id)


# Outcome tag for the once-per-pair missing-side report (#793). The detail
# carries the stranded token + shares; the outcome lets the pass recognise
# a pair it already reported so the second cycle collapses to a quiet row
# instead of a second identical warning.
MISSING_SIDE_REFUSAL_OUTCOME = "missing_side_tokens"

# Collapsed repeat for a pair whose sides the registry cannot resolve. Loud
# once (the `error` above), then this quiet row: same token + shares, no
# per-cycle warning. A member of SINGLE_LEG_QUIET_ACTIONS below.
STRANDED_MISSING_SIDE_ACTION = "stranded_missing_side"


def _lifecycle_held_and_opposite(
    registry: OrderRegistry, pair_id: str,
    condition_id: Optional[str] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Held + opposite token ids from the persisted lifecycle record (#793).

    Returns (held_token_id, opposite_token_id); either may be None when no
    record exists or its evidence carries neither. A record naming another
    condition is not this pair's record, so it reads as no record. Never
    raises: an unreadable row is "no record", and the caller keeps refusing.
    """
    try:
        record = registry.get_lifecycle_state(pair_id)
    except Exception:
        return None, None
    if record is None:
        return None, None
    if condition_id is not None and str(
        getattr(record, "condition_id", "") or "").lower() != str(condition_id).lower():
        return None, None
    raw = getattr(record, "evidence_json", None)
    if not raw:
        return None, None
    try:
        evidence = json.loads(raw)
    except Exception:
        return None, None
    if not isinstance(evidence, dict):
        return None, None
    held = evidence.get("held_token_id")
    opposite = evidence.get("opposite_token_id")
    held_token = str(held) if held else None
    opposite_token = str(opposite) if opposite else None
    return held_token, opposite_token


def _resolve_side_tokens_with_lifecycle(
    registry: OrderRegistry,
    pair_id: str,
    side_tokens: dict[str, str],
    condition_id: Optional[str] = None,
) -> dict[str, str]:
    """Fill the one empty UP/DOWN slot from the lifecycle record (#793).

    The missing side was never quoted, so the quotes-ledger fallback cannot
    see it -- but the single-leg lifecycle already recorded that token as
    `opposite_token_id`. Assigning it to the empty slot is not a guess: the
    known slot is still anchored in fills/quotes, and the recorded opposite
    only names the other leg. Both slots empty, both filled, or no record
    all return the mapping unchanged so the caller keeps refusing.
    """
    if set(side_tokens) == {"UP", "DOWN"}:
        return side_tokens
    missing = [side for side in ("UP", "DOWN") if side not in side_tokens]
    if len(missing) != 1:
        return side_tokens
    _, opposite_token = _lifecycle_held_and_opposite(
        registry, pair_id, condition_id)
    if not opposite_token:
        return side_tokens
    if opposite_token in side_tokens.values():
        return side_tokens
    resolved = dict(side_tokens)
    resolved[missing[0]] = opposite_token
    return resolved


def _heavy_side_with_lifecycle(
    registry: OrderRegistry,
    condition_id: str,
    pair_id: str,
    heavy_token: str,
) -> Optional[str]:
    """Heavy-leg side via quotes, falling back to the lifecycle record (#793).

    When the heavy token itself was never quoted, its side is still known
    whenever the lifecycle names it as held/opposite and the other token's
    side resolves from quotes: the heavy leg is then the other side. Any
    other shape stays None so the exit keeps refusing rather than guessing.
    """
    side = _token_side(registry, condition_id, heavy_token)
    if side in ("UP", "DOWN"):
        return side
    held_token, opposite_token = _lifecycle_held_and_opposite(
        registry, pair_id, condition_id)
    if heavy_token not in (held_token, opposite_token):
        return None
    other_token = opposite_token if heavy_token == held_token else held_token
    if not other_token:
        return None
    other_side = _token_side(registry, condition_id, other_token)
    if other_side == "UP":
        return "DOWN"
    if other_side == "DOWN":
        return "UP"
    return None


def _missing_side_already_reported(registry: OrderRegistry, pair_id: str) -> bool:
    """Whether this pair already logged its once-per-pair report (#793)."""
    try:
        refusals = registry.get_hard_stop_refusals()
    except Exception:
        return False
    return any(
        isinstance(row, dict)
        and row.get("pair_id") == pair_id
        and row.get("outcome") == MISSING_SIDE_REFUSAL_OUTCOME
        for row in refusals
    )


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


def _exit_fill_source(resp) -> str:
    """Where the booked fill numbers came from (#730).

    `simulated` / `assumed` come from the rehearsal's ladder walk, which is
    the only place that knows whether shares actually crossed the book.
    Otherwise a response carrying a positive price and size is a `reported`
    fill; anything else is a fallback -- missing, unparseable or zero fields
    -- and a fallback is unmeasured, never a free exit at 0.00 slippage.
    """
    if isinstance(resp, dict):
        label = resp.get("fill_source")
        if label:
            return str(label)
        try:
            price = float(resp.get("price"))
            size = float(resp.get("size"))
        except (TypeError, ValueError):
            return "assumed"
        if price > 0 and size > 0:
            return "reported"
    return "assumed"


def _record_exit_close(registry: OrderRegistry, pair: dict, heavy_token: str,
                       heavy_side: str, size: float, sell_price: float,
                       reason: Optional[str] = None,
                       method: str = "single_buy_exit",
                       trigger_bid: Optional[float] = None,
                       fresh_bid: Optional[float] = None,
                       fee: Optional[float] = None,
                       submitted_floor: Optional[float] = None,
                       fill_source: Optional[str] = None) -> None:
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
    # One close never removes more cost than the sold shares cost (#795):
    # the average prices the sold shares, and the remaining notional caps
    # the removal. Callers must pass the netted `after` view.
    cost_basis = min(size * heavy_avg, notional)
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
        trigger_bid=trigger_bid,
        fresh_bid=fresh_bid,
        fee=fee,
        submitted_floor=submitted_floor,
        fill_source=fill_source,
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
    bot_pos_price_max_pair_cost_usd: float,
    live: bool = True,
    venue_positions: Optional[dict[str, float]] = None,
    reason: Optional[str] = None,
    method: str = "single_buy_exit",
    *,
    force: bool = False,
    trigger_bid: Optional[float] = None,
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

    if pair["naked"] < MIN_ORDER_SHARES:
        # Dust (#761): below the venue minimum no sale can ever clear, so a
        # sell attempt refuses on every poll -- 5,464 refusal rows in
        # shadow-01, worst pair 2,041 in 68 minutes. Log once per pair,
        # before any cancel or book read, then hold without another write.
        already = any(
            r.get("pair_id") == pair_id and r.get("outcome") == "dust_skip"
            for r in registry.get_hard_stop_refusals()
        )
        if not already:
            try:
                registry.log_hard_stop_refusal(
                    pair_id=pair_id,
                    condition_id=pair.get("condition_id", ""),
                    trigger_bid=None,
                    outcome="dust_skip",
                    detail=(
                        f"Dust {pair['naked']:.4f} below the venue minimum "
                        f"of {MIN_ORDER_SHARES}."
                    ),
                )
            except Exception:  # noqa: BLE001 - telemetry never masks a hold
                pass
        return {"action": "dust_hold", "pair_id": pair_id,
                "size": pair["naked"]}

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
    heavy_side = _heavy_side_with_lifecycle(
        registry, pair["condition_id"], pair_id, heavy_token)
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

    if not force and not should_exit(pair["fill_cost"], light_ask, bot_pos_price_max_pair_cost_usd):
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

    # The re-read view is already netted against this pair's own closes
    # (#795): subtracting a prior credit here again would count the same
    # close twice and hide a real shortfall. `naked` is remaining shares.
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
    tick = tick_size(heavy_book)
    min_price = floor_to_tick(max(bid - MAX_SELL_SLIPPAGE, tick), tick)
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

    from py_clob_client_v2.clob_types import MarketOrderArgsV2, OrderType

    # `amount` is the maker amount: shares on a SELL. `price` is the worst
    # fill we accept, tick-aligned so the venue does not reject it outright.
    # The shadow rehearsal client accepts the exit reason and sells hard-stop
    # exits into the fresh book instead of the pre-decision snapshot; the live
    # SDK client takes no reason, so it keeps the bare call. Gated on the
    # shadow-only ladder walk rather than a try/except so a real TypeError
    # inside either post still surfaces.
    order_args = MarketOrderArgsV2(token_id=heavy_token, amount=size,
                                   side="SELL", price=min_price)
    if hasattr(client, "_sell_into_bids"):
        resp = client.create_and_post_market_order(order_args, reason=reason)
    else:
        # Fill-or-kill, STATED rather than inherited (#619). An exit is "get the
        # whole leg out now or not at all": a partially filled stop leaves a
        # naked remainder nobody decided to hold, and the poll would re-derive
        # the same stop next cycle into a thinner book. The SDK's default
        # happens to be FOK too, but that is a library detail -- this is the
        # money path, and the instruction belongs in the call.
        resp = client.create_and_post_market_order(
            order_args, order_type=OrderType.FOK)

    # 4. Record the exit BEFORE returning. A sell that exists only on the
    #    venue is invisible to the registry -- reconcile adopts fills only
    #    for orders it knows, and this taker SELL has no resting row, so the
    #    pair would read as still-held next cycle and be sold again (the
    #    repeat-sell loop observed in production). The close is the ledger
    #    entry: `inventory_from_registry` subtracts the sold leg from it, and
    #    the auto pass skips conditions that have a close.
    sold = _exit_fill_size(resp, size)
    fill_price = _exit_fill_price(resp, min_price)
    # Shadow rehearsal sells into its own book: no venue, no taker fee. Live
    # taker fees are unknown at sell time, so live stays NULL (unmeasured).
    fee = ZERO_FEE if hasattr(client, "_sell_into_bids") else None
    fill_source = _exit_fill_source(resp)
    _record_exit_close(registry, after, heavy_token, heavy_side, sold,
                       fill_price, reason=reason, method=method,
                       trigger_bid=trigger_bid, fresh_bid=bid, fee=fee,
                       submitted_floor=min_price,
                       fill_source=fill_source)

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
        "submitted_floor": min_price,
        "fill_source": fill_source,
        "trigger_bid": trigger_bid,
        "fresh_bid": bid,
        "cancelled": cancelled,
        "venue_light_matched": venue_light_matched,
        "positions_checked": positions_checked,
        "response": resp,
    }


def _venue_id_from_submit_error(exc: BaseException) -> str:
    """Recover a venue order id carried on a submit exception, if any.

    A timeout can arrive after the venue accepted the BUY, and some clients
    surface the accepted id on the error (attribute or dict payload). An id
    here means unknown-outcome-but-reconcilable; absence means there is
    nothing to reconcile against. Never guesses: returns "" when no
    spelling is present.
    """
    for key in ("orderID", "orderId", "order_id", "id", "venue_order_id"):
        value = getattr(exc, key, None)
        if value:
            return str(value)
    args = getattr(exc, "args", ()) or ()
    for arg in args:
        if isinstance(arg, dict):
            for key in ("orderID", "orderId", "order_id", "id"):
                if arg.get(key):
                    return str(arg.get(key))
        else:
            for key in ("orderID", "orderId", "order_id", "id"):
                value = getattr(arg, key, None)
                if value:
                    return str(value)
    cause = getattr(exc, "__cause__", None)
    if cause is not None and cause is not exc:
        recovered = _venue_id_from_submit_error(cause)
        if recovered:
            return recovered
    return ""


def complete_pair(
    client,
    registry: OrderRegistry,
    pair_id: str,
    bot_pos_price_max_pair_cost_usd: float,
    live: bool = True,
    bot_risk_exposure_max_order_usd: float = 25.0,
) -> dict:
    """Stage 4 — cross the book to complete a one-sided pair.

    This closes exposure rather than opening it: the half-open leg is already
    at risk, and completing it produces a pair worth $1.00 at merge. That is
    why it sits inside the staged exposure rule alongside the exit.

    The cap is the whole discipline. A cross that pushes the pair to or past
    `bot_pos_price_max_pair_cost_usd` is a guaranteed loss after gas, and closing it that way is
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
    if pair_cost >= bot_pos_price_max_pair_cost_usd:
        raise PairCompletionRefused(
            f"Completing {pair_id} at ask {ask:.4f} against a fill cost of "
            f"{pair['fill_cost']:.4f} gives pair_cost {pair_cost:.4f}, at or "
            f"above bot_pos_price_max_pair_cost_usd {bot_pos_price_max_pair_cost_usd:.4f}. That pair loses money "
            f"after gas; the exit path owns this case, not completion."
        )

    # Size from what actually filled, never from the intended size. Completing
    # the intended size against a partial fill would open fresh exposure on the
    # other side -- the opposite of this path's purpose.
    # `depth_at_or_above` reads the bids ladder. This path buys, so it must
    # size against the asks ladder or it will order shares nobody is offering.
    bounded_depth = ask_depth_at_or_below(book, ask)
    size = min(pair["naked"], bounded_depth)
    if size < MIN_ORDER_SHARES:
        raise PairCompletionRefused(
            f"Completable size {size:.4f} is below the venue minimum of "
            f"{MIN_ORDER_SHARES}. Naked {pair['naked']:.4f}, ask depth "
            f"at or below {ask:.4f} is {bounded_depth:.4f}."
        )

    notional = size * ask
    if notional > bot_risk_exposure_max_order_usd:
        raise PairCompletionRefused(
            f"Completion notional ${notional:.2f} exceeds MAX_ORDER_USD "
            f"${bot_risk_exposure_max_order_usd:.2f}. The Stage 1 cap applies to this order like "
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
    if pair_cost_after >= bot_pos_price_max_pair_cost_usd:
        raise PairCompletionRefused(
            f"Between the guard and the send, the heavy fill cost moved to "
            f"{fill_cost_after:.4f}; at ask {ask:.4f} the pair would cost "
            f"{pair_cost_after:.4f}, at or above bot_pos_price_max_pair_cost_usd "
            f"{bot_pos_price_max_pair_cost_usd:.4f}. Refusing the cross."
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
    import uuid
    from core_brain.order_registry import OrderRecord

    local_comp_id = str(uuid.uuid4())
    now_ms = int(time.time() * 1000)
    is_shadow = type(client).__name__ == "ShadowExecutionClient" or getattr(client, "is_shadow", False)
    created_local_row = False
    if not is_shadow:
        registry.create_order(
            OrderRecord(
                id=local_comp_id,
                order_id="",
                condition_id=pair["condition_id"],
                token_id=light_token,
                side="BUY",
                price=ask,
                original_size=size,
                status="pending",
                posted_ts=now_ms,
                last_polled_ts=now_ms,
                pair_id=pair_id,
                max_pair_cost_at_post=bot_pos_price_max_pair_cost_usd,
            )
        )
        created_local_row = True

    try:
        resp = client.create_and_post_market_order(
            MarketOrderArgsV2(token_id=light_token, amount=notional, side="BUY",
                              price=ask)
        )
    except Exception as exc:
        # Branch A — submit raised. An exception does NOT prove the order never
        # posted: a response timeout can arrive after the venue accepted the
        # BUY. When the error carries a venue order id we keep the row live
        # (unknown outcome, reconcilable) and let the stray guard own
        # reconciliation. Without an id there is nothing to reconcile against,
        # so we cancel ONLY local_comp_id to release the bankroll ceiling and
        # re-raise the original error; the operator must still check the venue
        # after a timeout because a cancelled local row cannot hide a live one.
        if not created_local_row:
            raise
        recovered_oid = _venue_id_from_submit_error(exc)
        if recovered_oid:
            try:
                registry.attach_venue_order_id(local_comp_id, recovered_oid)
            except Exception as attach_exc:
                log.warning(
                    "completion submit for pair %s raised %s; failed to attach "
                    "recovered venue id %s to local order %s (%s); manual "
                    "reconciliation may need this ID",
                    pair_id, type(exc).__name__, recovered_oid,
                    local_comp_id, attach_exc,
                )
            raise
        try:
            registry.update_order_status(
                local_comp_id, "cancelled", int(time.time() * 1000),
                cancel_reason=f"completion_submit_failed:{type(exc).__name__}",
            )
        except Exception as cancel_exc:
            # Never mask the venue error with a registry-write error.
            log.warning(
                "cancelling orphaned completion %s after %s failed: %s",
                local_comp_id, type(exc).__name__, cancel_exc,
            )
        else:
            log.warning(
                "completion submit for pair %s failed (%s); local order %s "
                "cancelled to release bankroll ceiling",
                pair_id, type(exc).__name__, local_comp_id,
            )
        raise

    venue_oid = ""
    if isinstance(resp, dict):
        venue_oid = str(resp.get("orderID") or resp.get("orderId")
                        or resp.get("order_id") or resp.get("id") or "")
    else:
        venue_oid = str(
            getattr(resp, "orderID", None)
            or getattr(resp, "orderId", None)
            or getattr(resp, "order_id", None)
            or getattr(resp, "id", None)
            or ""
        )
    if not venue_oid and created_local_row:
        # Branch B — submit returned but with no venue order id (provably not
        # posted: nothing trackable exists to reconcile). Cancel ONLY
        # local_comp_id so no unattached pending row survives in
        # get_active_orders() to lock the bankroll ceiling, then refuse
        # fail-closed rather than returning a "completed" that never crossed.
        try:
            registry.update_order_status(
                local_comp_id, "cancelled", int(time.time() * 1000),
                cancel_reason="completion_missing_venue_id",
            )
        except Exception as cancel_exc:
            log.warning(
                "cancelling id-less completion %s failed: %s",
                local_comp_id, cancel_exc,
            )
        raise PairCompletionRefused(
            f"Completion BUY for pair {pair_id} returned no venue order id; "
            f"local order {local_comp_id} cancelled, nothing crossed."
        )
    if venue_oid and not is_shadow:
        registry.attach_venue_order_id(local_comp_id, venue_oid)
        if hasattr(client, "get_order"):
            try:
                client.get_order(venue_oid)
            except Exception as exc:
                log.debug("get_order re-read on %s failed: %s", venue_oid, exc)

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
        "order_id": venue_oid,
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
    STRANDED_MISSING_SIDE_ACTION,
})

# Row vocabulary for lifecycle decisions. Owned here: the saver is the only
# renderer of these rows, so the lifecycle owns decision actions and this
# module owns their reporting names. Covers exactly the tail-reachable
# actions of `single_leg_lifecycle.transition` -- `hard_stop` exits upstream
# and `settlement_fallback` only arises with `settlement_due=True`, which the
# main transition call never uses. Anything else is a bug, not a row.
_SAVER_ACTION_BY_DECISION = {
    "wait": "patient_wait",
    "hedge": "escalated_wait",
    "locked": "pair_locked",
    "rest": "dual_resting",
    "refused": "refused",
}


def saver_action_for(action: str) -> str:
    """Return this row's `action` word for a lifecycle decision action."""
    try:
        return _SAVER_ACTION_BY_DECISION[action]
    except KeyError:
        raise KeyError(
            f"unknown lifecycle decision action: {action!r} "
            f"(reachable: {sorted(_SAVER_ACTION_BY_DECISION)})"
        ) from None

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
    if not getattr(cfg, "bot_risk_gate_pairs_rule_on", True):
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


def _latest_close_ms_by_condition(registry,
                                   condition_ids=None) -> dict[str, int]:
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
    if condition_ids is None:
        rows = [(r.get("condition_id"), r.get("ts"))
                for r in registry.get_all_closes()]
    else:
        rows = registry.get_close_timestamps(condition_ids)
    latest_close_ms: dict[str, int] = {}
    for cid, ts in rows:
        if not cid or not ts:
            continue
        try:
            ts_ms = int(float(ts) * 1000.0)
        except (TypeError, ValueError):
            continue
        if ts_ms > latest_close_ms.get(cid, 0):
            latest_close_ms[cid] = ts_ms
    return latest_close_ms


def _reset_tokens_for_condition(quoted_tokens: dict,
                                 condition_id: str) -> tuple[Optional[str], Optional[str]]:
    """UP/DOWN token ids for one market from the quotes ledger, if known."""
    sides = quoted_tokens.get(condition_id) or {}
    up = sides.get("UP")
    down = sides.get("DOWN")
    return (up[1] if up else None, down[1] if down else None)


def _net_leftover_above_eps(registry, condition_id: str,
                            up_token_id: str,
                            down_token_id: str) -> bool:
    """Whether fills minus closes still leave shares on either token (#646)."""
    from core_brain.order_registry import inventory_from_registry
    db_path = getattr(registry, "db_path", None)
    kwargs = {"db_path": db_path} if db_path is not None else {}
    inv = inventory_from_registry(condition_id, up_token_id, down_token_id,
                                  **kwargs)
    return (abs(float(inv.up_shares or 0.0)) > SIZE_EPS
            or abs(float(inv.down_shares or 0.0)) > SIZE_EPS)


def _retry_pending_reset(client, funder, live: bool, registry,
                         condition_id: str, up_token_id: str,
                         down_token_id: str, now_ms: int):
    """Retry a reset an earlier pass could not prove (#646).

    Runs when a lifecycle row or a `resetting` block still exists for the
    condition -- e.g. the Data API still showed sold shares at exit time.
    Returns the `ResetOutcome`, or None when nothing is pending (or when a
    live retry has no funder to read balances with).
    """
    from core_brain.lifecycle_reset import reset_condition
    cid = str(condition_id or "").lower()
    try:
        prev_marks = registry.requalification_marks()
    except Exception:
        prev_marks = {}
    prev_mark = prev_marks.get(cid)
    prev_hard_stop = (prev_mark is not None
                      and prev_mark.status == "awaiting"
                      and "hard_stop" in str(prev_mark.reason or ""))
    pending = registry.has_lifecycle_for_condition(condition_id)
    if not pending:
        mark = prev_mark
        pending = mark is not None and mark.status == "resetting"
    if not pending:
        return None
    if live:
        if not funder:
            return None
        ports = live_reset_ports(client, funder, registry, condition_id,
                                 up_token_id, down_token_id)
    else:
        db_path = (getattr(client, "_db_path", None)
                   or getattr(registry, "db_path", None))
        ports = _shadow_reset_ports_or_none(
            registry, db_path, condition_id, up_token_id, down_token_id)
        if ports is None:
            return None
    outcome = reset_condition(registry, condition_id, up_token_id, down_token_id,
                              ports, reason="reset_retry", now_ms=now_ms)
    if outcome.status != "RESET" and prev_hard_stop:
        # The retry's begin/abandon cycle lifts the `resetting` mark on
        # failure -- restore the stable hard-stop block (#796) so a delayed
        # balance read cannot reopen fresh entries mid-run. Rows untouched.
        try:
            still = registry.requalification_marks().get(cid)
        except Exception:
            still = None
        if still is None:
            try:
                registry.mark_hard_stop_block(
                    condition_id, prev_mark.reason, now_ms)
            except Exception as exc:
                log.warning("hard-stop block restore failed for %s: %s",
                            condition_id, exc)
    return outcome


def _last_fill_ms_by_pair(registry) -> tuple[dict[str, int], dict[str, str]]:
    """The newest fill time per pair, and the condition each pair belongs to.

    Discovery for both arms comes from the fills ledger. An undated fill (no
    `venue_ts`) has no entry and is left alone by both.
    """
    last_fill_ms: dict[str, int] = {}
    pair_cids: dict[str, str] = {}
    for pid, cid, vts in registry.get_fill_discovery_rows():
        if not pid:
            continue
        pair_cids.setdefault(pid, cid or "")
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


def _pair_cap_hedge_bailout(
    client,
    registry: OrderRegistry,
    *,
    pair_id: str,
    position,
    side_tokens: dict,
    held_token: str,
    held_bid: Optional[float],
    last_fill_ms: float,
    now_s: float,
    cfg,
    bot_pos_price_max_pair_cost_usd: float,
    tick_size: float,
    live: bool,
    venue_positions: Optional[dict[str, float]],
    lifecycle_state: Optional[str],
) -> Optional[dict]:
    """Exit a naked leg whose capped hedge has left the market behind (#797).

    moe/shadow-01: UP 10 @ 0.82 was entered against a 0.17 hedge that was
    reachable when the entry was quoted (DOWN ask 0.151). Fifteen minutes later
    the DOWN market had walked to 0.4225 while the best hedge the pair cap
    allows stayed at 0.17 -- unreachable, not merely wide. Waiting cannot
    complete that pair, so the naked leg leaves through the existing exit at
    the strategy's own hedge window instead of holding to the 75% circuit
    floor. Its own reason keeps a cap bail-out distinguishable from a
    lifecycle stop, and its own route keeps the two apart in the ledger.

    Returns None when the hedge is still reachable, when the ask is unknown,
    or when the leg is younger than the window -- the ordinary wait owns it.
    """
    from core_brain.single_leg_lifecycle import hedge_unreachable_at_cap

    age_s = ((now_s * 1000.0 - last_fill_ms) / 1000.0) if last_fill_ms > 0 else 0.0
    try:
        hedge_wait_s = float(getattr(cfg, "bot_risk_stranded_max_wait_sec", 300.0))
    except (TypeError, ValueError):
        hedge_wait_s = 300.0
    if last_fill_ms <= 0 or age_s <= hedge_wait_s:
        return None

    held_is_up = held_token == side_tokens["UP"]
    opposing_token = side_tokens["DOWN"] if held_is_up else side_tokens["UP"]
    held_average_price = (
        position.up_avg_price if held_is_up else position.down_avg_price
    )
    try:
        opposing_book = (
            client.get_order_book(opposing_token)
            if hasattr(client, "get_order_book") else None
        )
        opposing_ask = best_ask(opposing_book) if opposing_book else None
    except Exception as ask_exc:
        # An unreadable hedge book is unknown, and unknown never sells a leg.
        log.debug("Hedge ask read failed for pair %s: %s", pair_id, ask_exc)
        opposing_ask = None

    if not hedge_unreachable_at_cap(
        held_average_price, opposing_ask,
        bot_pos_price_max_pair_cost_usd, tick_size,
    ):
        return None

    bailout = exit_single_buy(
        client, registry, pair_id, bot_pos_price_max_pair_cost_usd,
        live=live, venue_positions=venue_positions,
        reason="pair_cap_hedge_unreachable", force=True,
        trigger_bid=held_bid,
    )
    bailout.update(
        route="pair_cap_bailout",
        lifecycle_state=lifecycle_state,
        unreachable_for_sec=round(age_s, 3),
        opposing_best_ask=opposing_ask,
    )
    return bailout


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
    decision_book_fn=None,
) -> list[dict]:
    """Apply the shared lifecycle policy to every active one-sided pair.

    Ordinary exposure waits for its resting hedge; an escalated hedge is
    managed by the Trader's quote path. The poll owns only the hard-stop sell
    and the existing market-end-aware settlement fallback.

    `decision_book_fn(token_id)` optionally serves the held-leg bid the
    lifecycle evaluates against (#659: stream-first books, REST fallback).
    It takes the same levels shape `client.get_order_book` returns. Exits
    and completions always read fresh from the client, never from this hook.
    """
    pairs_enabled = bool(getattr(cfg, "bot_risk_gate_pairs_rule_on", True))
    aged_out_enabled = bool(getattr(cfg, "bot_risk_gate_aged_out_rescue_on", True))
    if not pairs_enabled and not aged_out_enabled:
        return []

    # Single-executor guarantee. The whole venue-mutating pass runs under one
    # process-wide lock so the rotation sweep thread and the <=1 Hz guardian
    # thread can never decide/cancel/sell the same pair concurrently. A nested
    # call by the same logical pass re-acquires the RLock harmlessly; only a
    # separate owner contends, and it now waits its turn instead of racing.
    if hold_lifecycle_lock():
        log.info(
            "single-leg lifecycle pass deferred: another executor is already "
            "mutating this registry (rotation sweep vs guardian overlap)"
        )
    with lifecycle_mutation_guard():
        return _manage_single_leg_positions_locked(
            client, registry, cfg,
            live=live, now=now, venue_positions=venue_positions,
            funder=funder, market_state_fn=market_state_fn,
            state_cache=state_cache, gamma_host=gamma_host,
            resolved_cids=resolved_cids,
            decision_book_fn=decision_book_fn,
        )


def _manage_single_leg_positions_locked(
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
    decision_book_fn=None,
) -> list[dict]:
    """The mutation pass, called under `lifecycle_mutation_guard`."""

    pairs_enabled = bool(getattr(cfg, "bot_risk_gate_pairs_rule_on", True))
    aged_out_enabled = bool(getattr(cfg, "bot_risk_gate_aged_out_rescue_on", True))

    from core_brain.quotes import dynamic_offset_for
    from core_brain.single_leg_lifecycle import (
        LegState, SingleLegPosition, capped_hedge_tick_exists, evaluate,
        hedge_unreachable_at_cap, max_profitable_hedge_bid, persist_decision,
        transition,
    )

    now_s = now if now is not None else time.time()
    window_ms = int(getattr(cfg, "bot_risk_pairs_exit_window_sec", 900.0) * 1000)
    lead_sec = float(getattr(cfg, "bot_risk_aged_out_rescue_lead_sec", 900.0))
    _mpc = getattr(cfg, "bot_pos_price_max_pair_cost_usd", None)
    bot_pos_price_max_pair_cost_usd = float(0.995 if _mpc is None else _mpc)
    grid_tick = float(getattr(cfg, "bot_quote_grid_tick_size_usd", DEFAULT_TICK_SIZE))
    bot_risk_stop_mode = str(getattr(cfg, "bot_risk_stop_mode", "NONE"))
    bot_risk_stop_adverse_drop_cents = float(getattr(cfg, "bot_risk_stop_adverse_drop_cents", 0.05))
    bot_risk_stop_max_drawdown_pct = float(getattr(cfg, "bot_risk_stop_max_drawdown_pct", 10.0))
    _edp = getattr(cfg, "bot_risk_circuit_max_drawdown_pct", None)
    bot_risk_circuit_max_drawdown_pct = float(75.0 if _edp is None else _edp)
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

    last_fill_ms_by_pair, pair_cids = _last_fill_ms_by_pair(registry)
    latest_close_ms = _latest_close_ms_by_condition(
        registry,
        [c for c in {pair_cids.get(pid) for pid in last_fill_ms_by_pair}
         if c],
    )
    base_offset = float(dynamic_offset_for(cfg)[0])
    quoted_tokens: dict[str, dict[str, tuple[float, str]]] = {}
    for quote in registry.get_latest_tokens_by_condition_side():
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
            # A close covering the latest fill is not proof the market is
            # flat: a partial exit leaves a remainder (#646). Skip only when
            # the net leftover inventory is also gone; otherwise fall through
            # and stay managed. A pending reset is retried on the way out so
            # a delayed balance read can complete without a new scheduler.
            # Unknown tokens fail closed: without them there is no inventory
            # to check, so the pair stays managed.
            up_tok, down_tok = _reset_tokens_for_condition(
                quoted_tokens, condition_id)
            if up_tok is None or down_tok is None:
                continue
            if _net_leftover_above_eps(registry, condition_id, up_tok,
                                       down_tok):
                # A partial exit stays managed -- but only with a venue view:
                # exit sizing nets prior closes against it (#326), and
                # without a view the sale would size off fills the close
                # already retired. No view means no safe sale, so keep
                # the old skip on that path.
                if venue_positions is None:
                    continue
            else:
                _retry_pending_reset(client, funder, live, registry, condition_id,
                                     up_tok, down_tok,
                                     now_ms=int(now_s * 1000.0))
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
            # #793: the missing leg was never quoted, so the quotes ledger
            # cannot name it -- but the lifecycle record already holds that
            # token as `opposite_token_id`. Filling the one empty slot from
            # the record is not a guess; refusing stays correct only when
            # the registry genuinely has no record.
            side_tokens = _resolve_side_tokens_with_lifecycle(
                registry, pair_id, side_tokens, pair["condition_id"])
            if set(side_tokens) != {"UP", "DOWN"}:
                held_tok = pair["heavy"]["token_id"]
                stranded_size = float(pair["naked"])
                detail = (
                    f"pair_id={pair_id!r} does not identify one UP token and "
                    f"one DOWN token in the quotes ledger; stranded leg "
                    f"{held_tok} holds {stranded_size:.4f} shares with no "
                    f"recorded opposite side"
                )
                # Once per pair (#793): the first cycle raises loud so the
                # log + cycle event carry the token + shares; every later
                # cycle collapses to a quiet row with the same payload.
                # Telemetry never masks the refusal: a failing write falls
                # through to the loud raise.
                if _missing_side_already_reported(registry, pair_id):
                    out.append({
                        "pair_id": pair_id, "condition_id": condition_id,
                        "action": STRANDED_MISSING_SIDE_ACTION,
                        "reason": detail,
                        "token_id": held_tok, "size": stranded_size,
                    })
                    continue
                try:
                    registry.log_hard_stop_refusal(
                        pair_id=pair_id,
                        condition_id=pair.get("condition_id", ""),
                        trigger_bid=None,
                        outcome=MISSING_SIDE_REFUSAL_OUTCOME,
                        detail=detail,
                    )
                except Exception:  # noqa: BLE001 - telemetry never masks a hold
                    pass
                raise PairExitRefused(detail)

            empty_leg = {"matched": 0.0, "notional": 0.0}
            up_leg = pair["legs"].get(side_tokens["UP"], empty_leg)
            down_leg = pair["legs"].get(side_tokens["DOWN"], empty_leg)
            up_size = float(up_leg["matched"])
            down_size = float(down_leg["matched"])
            held_token = (
                side_tokens["UP"] if up_size > down_size else side_tokens["DOWN"]
            )
            if decision_book_fn is not None:
                held_book = decision_book_fn(held_token)
            else:
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
                position, previous_state, bot_pos_price_max_pair_cost_usd=bot_pos_price_max_pair_cost_usd,
                settlement_due=False, tick_size=grid_tick,
                bot_risk_stop_mode=bot_risk_stop_mode, bot_risk_stop_adverse_drop_cents=bot_risk_stop_adverse_drop_cents,
                bot_risk_stop_max_drawdown_pct=bot_risk_stop_max_drawdown_pct,
                bot_risk_circuit_max_drawdown_pct=bot_risk_circuit_max_drawdown_pct,
            )
            if decision.action == "hard_stop":
                # The poll's hard stop is its own to persist: it survives
                # restart and is observable, and only the poll executes it.
                persist_decision(
                    registry, position, decision, previous_state,
                )
            if decision.action == "hard_stop":
                try:
                    result = exit_single_buy(
                        client, registry, pair_id, bot_pos_price_max_pair_cost_usd,
                        live=live, venue_positions=venue_positions,
                        reason="lifecycle_hard_stop", force=True,
                        trigger_bid=held_bid,
                    )
                except Exception as exc:
                    # #730: an exit that measured nothing is evidence too -- one
                    # append-only row per attempt. Telemetry is best-effort: a
                    # failing write never changes the exit outcome below.
                    try:
                        registry.log_hard_stop_refusal(
                            pair_id=pair_id,
                            condition_id=position.condition_id,
                            trigger_bid=held_bid,
                            outcome=("refused" if isinstance(exc, PairExitRefused)
                                     else "error"),
                            detail=f"{type(exc).__name__}: {exc}",
                        )
                    except Exception:  # noqa: BLE001 - never mask the exit error
                        pass
                    raise
                result.update(
                    route="hard_stop", lifecycle_state=decision.state.value,
                    reason=decision.reason,
                )
                if result.get("action") == "exited":
                    sold_size = float(result.get("size", 0.0) or 0.0)
                    heavy_tok = result.get("token_id")
                    if (venue_positions is not None
                            and heavy_tok and heavy_tok in venue_positions):
                        venue_positions[heavy_tok] = max(
                            0.0, float(venue_positions[heavy_tok]) - sold_size)
                    # #646: the old block wrote DUAL_RESTING here off an
                    # assumed-flat read (`venue_positions is None` fell back
                    # to `requested - sold`, and `opposite_token_id` does not
                    # exist on SingleLegPosition so the other leg read 0.0).
                    # The shared proof replaces it: only a market flat on
                    # both ledger and venue clears its row. Anything else
                    # keeps the row and stays managed.
                    from core_brain.lifecycle_reset import reset_condition
                    if live:
                        ports = live_reset_ports(
                            client, funder, registry, position.condition_id,
                            position.up_token_id, position.down_token_id)
                    else:
                        db_path = (getattr(client, "_db_path", None)
                                   or getattr(registry, "db_path", None))
                        ports = _shadow_reset_ports_or_none(
                            registry, db_path, position.condition_id,
                            position.up_token_id, position.down_token_id)
                        if ports is None:
                            # No proof ports (shadow without a store factory):
                            # the exit still happened, so block fresh entries
                            # for the rest of the run (#796). Rows untouched.
                            try:
                                registry.mark_hard_stop_block(
                                    position.condition_id, "hard_stop_exited",
                                    int(time.time() * 1000))
                            except Exception as exc:
                                log.warning("hard-stop block failed for %s: %s",
                                            position.condition_id, exc)
                            out.append(result)
                            continue
                    reset_outcome = reset_condition(
                        registry, position.condition_id,
                        position.up_token_id, position.down_token_id,
                        ports, reason="hard_stop_exited",
                        now_ms=int(time.time() * 1000))
                    result.update(
                        reset_status=reset_outcome.status,
                        reset_reason=reset_outcome.reason,
                    )
                    if reset_outcome.status == "RESET":
                        result.update(lifecycle_state=None)
                    else:
                        # #796: the proof keeps the row managed (residual /
                        # late fill / unreadable venue), but fresh entries on
                        # this condition stay blocked for the rest of the run
                        # until a newer qualifying publication clears the
                        # mark. Managed hedges/merges still flow via the final
                        # entry gate; only fresh submits are dropped.
                        try:
                            registry.mark_hard_stop_block(
                                position.condition_id, "hard_stop_exited",
                                int(time.time() * 1000))
                        except Exception as exc:
                            log.warning("hard-stop block failed for %s: %s",
                                        position.condition_id, exc)
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
                        position, registry, bot_pos_price_max_pair_cost_usd=bot_pos_price_max_pair_cost_usd,
                        settlement_due=True, tick_size=grid_tick,
                        bot_risk_stop_mode=bot_risk_stop_mode,
                        bot_risk_stop_adverse_drop_cents=bot_risk_stop_adverse_drop_cents,
                        bot_risk_stop_max_drawdown_pct=bot_risk_stop_max_drawdown_pct,
                        bot_risk_circuit_max_drawdown_pct=bot_risk_circuit_max_drawdown_pct,
                    )
                    result = _settlement_fallback(
                        client, registry, pair, cfg, bot_pos_price_max_pair_cost_usd, live,
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
                    # A long-dated market keeps a naked leg in `awaiting_lead`
                    # for hours, and that is precisely the leg the cap
                    # bail-out exists for (#797). `end_unknown` and
                    # `venue_closed` stay with settlement, and `due` never
                    # reaches this branch.
                    bailout = None
                    if verdict == AGED_OUT_AWAITING_LEAD:
                        bailout = _pair_cap_hedge_bailout(
                            client, registry,
                            pair_id=pair_id,
                            position=position,
                            side_tokens=side_tokens,
                            held_token=held_token,
                            held_bid=held_bid,
                            last_fill_ms=last_fill_ms,
                            now_s=now_s,
                            cfg=cfg,
                            bot_pos_price_max_pair_cost_usd=bot_pos_price_max_pair_cost_usd,
                            tick_size=grid_tick,
                            live=live,
                            venue_positions=venue_positions,
                            lifecycle_state=decision.state.value,
                        )
                    if bailout is not None:
                        bailout.update(aged_out_verdict=verdict)
                        out.append(bailout)
                        continue
                    out.append({
                        "pair_id": pair_id, "condition_id": condition_id,
                        "action": verdict, "lifecycle_state": decision.state.value,
                        "reason": reason,
                    })
                    continue

            # Stranded exposure resolution (#453)
            # Evaluate condition exposure when neither hard-stop nor settlement fallback acted.
            try:
                tokens = (side_tokens["UP"], side_tokens["DOWN"])
                exp = load_condition_exposure(client, registry, condition_id, tokens)
                stranded_completion_on = bool(getattr(cfg, "bot_risk_stranded_completion_on", False))
                stranded_max_wait = float(getattr(cfg, "bot_risk_stranded_max_wait_sec", 300.0))
                bot_quote_size_min_shares = float(getattr(cfg, "bot_quote_size_min_shares", 5.0) or 5.0)

                # Stranded exposure resolution (#453)
                # When completion is enabled with a profitable cross, complete immediately.
                # When stranded without working hedge and aged out past regular window, force resolution.
                if (stranded_completion_on or is_aged_out) and (
                    exp.status.value in ("stranded", "under_covered") and (
                        exp.status.value == "stranded" or exp.coverage_shortfall < bot_quote_size_min_shares
                    )
                ):
                    opposing_token = exp.light_token_id
                    opposing_book = client.get_order_book(opposing_token) if (opposing_token and hasattr(client, "get_order_book")) else None
                    opposing_ask = best_ask(opposing_book) if opposing_book else None
                    held_avg = position.up_avg_price if exp.heavy_token_id == position.up_token_id else position.down_avg_price

                    if (
                        stranded_completion_on
                        and opposing_ask is not None
                        and opposing_ask > 0
                        and (held_avg + opposing_ask) < bot_pos_price_max_pair_cost_usd
                    ):
                        with _completion_target(client, pair_id):
                            res = complete_pair(
                                client, registry, pair_id, bot_pos_price_max_pair_cost_usd,
                                live=live, bot_risk_exposure_max_order_usd=getattr(cfg, "bot_risk_exposure_max_order_usd", 25.0) or 25.0,
                            )
                        if res.get("action") == "completed":
                            # Attempt live merge if live and non-interactive merge entry point is available
                            if live:
                                try:
                                    from core_brain.order_manager import merge
                                    # merge requires positional args; log operator directive if manual
                                    log.info("Completed stranded pair %s; ready for merge", pair_id)
                                except Exception as m_exc:
                                    log.warning("Merge retry next cycle: %s", m_exc)
                        out.append(res)
                        continue

                    # Otherwise, check maker tick availability & age. The tick
                    # question lives in the lifecycle module: same predicate
                    # the escalation path derives from, asked with this probe's
                    # own venue-view inputs rather than re-derived here.
                    age_s = (now_s * 1000.0 - last_fill_ms) / 1000.0 if last_fill_ms > 0 else 0.0
                    has_maker_tick = capped_hedge_tick_exists(
                        held_avg, bot_pos_price_max_pair_cost_usd, grid_tick)

                    if has_maker_tick and age_s <= stranded_max_wait:
                        out.append({
                            "pair_id": pair_id,
                            "condition_id": condition_id,
                            "action": "stranded_awaiting_maker",
                            "lifecycle_state": decision.state.value,
                            "reason": f"unhedged leg awaiting maker quote (age {age_s:.1f}s <= {stranded_max_wait:.1f}s)",
                        })
                        continue
                    else:
                        exit_res = exit_single_buy(
                            client, registry, pair_id, bot_pos_price_max_pair_cost_usd,
                            live=live, venue_positions=venue_positions,
                            reason="stranded_unhedged_exit", force=True,
                        )
                        exit_res.update(route="stranded_exit", lifecycle_state=decision.state.value)
                        out.append(exit_res)
                        continue
            except ExposureUnavailable as exp_exc:
                log.info("Exposure check skipped for pair %s: %s", pair_id, exp_exc)
            except Exception as exp_exc:
                log.debug("Exposure resolution check skipped for pair %s: %s", pair_id, exp_exc)

            # Pair-cap hedge bail-out (#797)
            bailout = _pair_cap_hedge_bailout(
                client, registry,
                pair_id=pair_id,
                position=position,
                side_tokens=side_tokens,
                held_token=held_token,
                held_bid=held_bid,
                last_fill_ms=last_fill_ms,
                now_s=now_s,
                cfg=cfg,
                bot_pos_price_max_pair_cost_usd=bot_pos_price_max_pair_cost_usd,
                tick_size=grid_tick,
                live=live,
                venue_positions=venue_positions,
                lifecycle_state=decision.state.value,
            )
            if bailout is not None:
                out.append(bailout)
                continue

            # Unknown exposure keeps the lifecycle's own verdict: the pair is
            # still managed under the decision above and the check retries
            # next rotation. Row words are this module's contract, which fails
            # loud on any action `transition` cannot emit.
            action = saver_action_for(decision.action)
            out.append({
                "pair_id": pair_id, "condition_id": condition_id,
                "action": action,
 "lifecycle_state": decision.state.value,
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


def _settlement_fallback(client, registry, pair, cfg, bot_pos_price_max_pair_cost_usd, live,
                         venue_positions, reason: str) -> dict:
    """Try one capped completion at settlement time, then use guarded exit."""
    try:
        with _completion_target(client, pair["pair_id"]):
            result = complete_pair(
                client, registry, pair["pair_id"], bot_pos_price_max_pair_cost_usd, live=live,
                bot_risk_exposure_max_order_usd=getattr(cfg, "bot_risk_exposure_max_order_usd", None) or 25.0,
            )
    except PairCompletionRefused:
        result = exit_single_buy(
            client, registry, pair["pair_id"], bot_pos_price_max_pair_cost_usd,
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
    if not getattr(cfg, "bot_risk_gate_aged_out_rescue_on", True):
        return []
    return manage_single_leg_positions(
        client, registry, cfg, live=live, now=now,
        venue_positions=venue_positions, funder=funder,
        market_state_fn=market_state_fn, state_cache=state_cache,
        gamma_host=gamma_host, resolved_cids=resolved_cids,
    )


# Backward-compatible alias
exit_naked_leg = exit_single_buy
