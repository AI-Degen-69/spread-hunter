"""Controlled venue reachability probe (#840): one small post-only GTC buy.

Proves CLOB signing, order acceptance, and cancellation against the live
venue without opening a position: the bid rests at least ~15c below the best
bid, holds 2-4s (default 3s), and is cancelled by exact order ID. PASS needs
the venue's own word (order status + matched size 0 + absent from open
orders), never the cancel receipt alone.

Fail-closed throughout: any unreadable or unknown condition refuses with a
non-zero exit before anything is signed. `live=False` prints the full plan
and stops before signing. All venue seams are injectable so tests drive this
module with fakes and no test ever touches the venue.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from core_brain.config import VENUE_MIN_NOTIONAL_USD

#: Hard ceiling on worst-case exposure (size x price). Not raisable from CLI.
REACH_PROBE_MAX_EXPOSURE_USD: float = 2.00

REACH_PROBE_DEFAULT_DISCOUNT: float = 0.15
REACH_PROBE_DEFAULT_HOLD_SEC: float = 3.0
REACH_PROBE_HOLD_MIN_SEC: float = 2.0
REACH_PROBE_HOLD_MAX_SEC: float = 4.0

#: Accept-to-cancel-receipt bound the check promises.
REACH_PROBE_CANCEL_DEADLINE_SEC: float = 5.0


class ProbeRefused(Exception):
    """A pre-flight gate or plan bound refused the check. Fail closed."""


@dataclass
class ProbePlan:
    condition_id: str
    token_id: str
    side: str  # "UP" or "DOWN", by token match
    price: float
    size: float
    tick_size: float
    venue_min_size: float | None
    worst_case_usd: float
    best_bid: float
    depth_usd: float
    discount: float
    hold_sec: float


_ORDER_TOKEN_KEYS = ("asset_id", "token_id", "tokenId", "assetId", "asset")
_ORDER_MARKET_KEYS = ("market", "condition_id", "conditionId", "condition")


def floor_to_tick(price: float, tick: float) -> float:
    """Round down to the tick grid; refuse anything below one tick."""
    if not (math.isfinite(price) and math.isfinite(tick)) or tick <= 0:
        raise ProbeRefused(f"unpriceable inputs price={price!r} tick={tick!r}")
    floored = round(math.floor(price / tick + 1e-9) * tick, 4)
    if floored < tick:
        raise ProbeRefused(
            f"probe price ${floored:.4f} is below one tick (${tick}); refusing")
    return floored


def size_for_order(price: float, venue_min_size: float | None, cfg) -> float:
    """Shares for the probe: venue floor (or local default) vs $1 notional."""
    if not (math.isfinite(price) and price > 0):
        raise ProbeRefused(f"unsizeable price {price!r}")
    try:
        venue_min = float(venue_min_size) if venue_min_size is not None else None
        if venue_min is not None and (
                not math.isfinite(venue_min) or venue_min <= 0):
            venue_min = None
    except (TypeError, ValueError):
        venue_min = None
    local_min = float(getattr(cfg, "bot_quote_size_min_shares", 5) or 5)
    floor_shares = max(venue_min if venue_min is not None else local_min, local_min)
    need = math.ceil(VENUE_MIN_NOTIONAL_USD / price)
    return max(floor_shares, float(need))


def top3_bid_depth_usd(book: dict | None) -> float:
    """Dollar value resting in the top three bid levels of a fresh book."""
    if not isinstance(book, dict):
        return 0.0
    bids = book.get("bids") or {}
    try:
        levels = sorted(((float(p), float(s)) for p, s in bids.items()),
                        reverse=True)[:3]
    except (TypeError, ValueError):
        return 0.0
    return sum(p * s for p, s in levels
               if math.isfinite(p) and math.isfinite(s) and p > 0 and s > 0)


def _order_matches_market(order: Any, market) -> bool | None:
    """True when the open order is ours-on-this-market, False when unrelated,
    None when the response shape carries nothing attributable (fail closed)."""
    if not isinstance(order, dict):
        order = getattr(order, "__dict__", None) or {}
        if not isinstance(order, dict):
            return None
    tokens = {str(market.up_token), str(market.down_token)}
    for key in _ORDER_TOKEN_KEYS:
        if order.get(key) is not None and str(order.get(key)).strip():
            return str(order.get(key)) in tokens
    for key in _ORDER_MARKET_KEYS:
        if order.get(key) is not None and str(order.get(key)).strip():
            return str(order.get(key)).lower() == str(market.condition_id).lower()
    return None


def preflight_market(client: Any, market, *, book_fn: Callable[[str], dict],
                     open_notional_fn: Callable[[Any], float | None],
                     ) -> tuple[str, float, float]:
    """Fail-closed gates for one pinned market.

    Returns (token_id, best_bid, depth_usd) for the deeper-bid token.
    Raises ProbeRefused on any unreadable, unknown, or unsafe condition.
    """
    try:
        open_orders = client.get_open_orders() or []
    except Exception as exc:
        raise ProbeRefused(f"cannot read open orders: {exc!r}") from exc

    for order in open_orders:
        match = _order_matches_market(order, market)
        if match is None:
            raise ProbeRefused(
                "cannot attribute an open order to a market "
                f"({str(order)[:120]}); refusing")
        if match:
            raise ProbeRefused(
                f"account already has open orders on {market.condition_id[:12]}...; "
                "refusing to probe a market with resting exposure")

    candidates: list[tuple[str, float, float]] = []
    for token_id in (market.up_token, market.down_token):
        try:
            book = book_fn(str(token_id))
        except Exception as exc:
            raise ProbeRefused(
                f"cannot read fresh book for token {str(token_id)[:14]}...: "
                f"{exc!r}") from exc
        best = book.get("best_bid") if isinstance(book, dict) else None
        try:
            best_f = float(best) if best is not None else None
        except (TypeError, ValueError):
            best_f = None
        if best_f is None or not math.isfinite(best_f):
            continue
        if not 0.0 < best_f < 1.0:
            raise ProbeRefused(
                f"best bid ${best_f} on {str(token_id)[:14]}... is outside "
                "the binary 0-1 range; refusing")
        candidates.append((str(token_id), best_f, top3_bid_depth_usd(book)))
    if not candidates:
        raise ProbeRefused(
            f"no readable best bid on either token of {market.condition_id[:12]}...")
    # Deeper top-3 book first; ties rest on UP (first in tuple order).
    candidates.sort(key=lambda c: c[2], reverse=True)

    try:
        committed = open_notional_fn(client)
    except Exception as exc:
        raise ProbeRefused(f"cannot read open notional: {exc!r}") from exc
    if committed is None:
        raise ProbeRefused("open notional is None; the venue read failed")
    return candidates[0]


def build_plan(market, token_id: str, best_bid: float, depth_usd: float, *,
               discount: float, hold_sec: float, cfg,
               order_caps_fn: Callable[[], tuple[float, float]],
               open_notional_usd: float) -> ProbePlan:
    """Price, size, and exposure for the probe; refuses anything out of bounds."""
    if not (math.isfinite(discount) and 0.0 < discount < 1.0):
        raise ProbeRefused(f"discount {discount!r} must be in (0, 1)")
    if not (REACH_PROBE_HOLD_MIN_SEC <= float(hold_sec) <= REACH_PROBE_HOLD_MAX_SEC):
        raise ProbeRefused(
            f"hold {hold_sec}s outside "
            f"{REACH_PROBE_HOLD_MIN_SEC:.0f}-{REACH_PROBE_HOLD_MAX_SEC:.0f}s; "
            "a longer hold leaves no time for the cancel receipt")
    price = floor_to_tick(best_bid - discount, float(market.tick_size))
    size = size_for_order(price, market.venue_min_size, cfg)
    worst = round(price * size, 2)
    if worst > REACH_PROBE_MAX_EXPOSURE_USD:
        raise ProbeRefused(
            f"worst case ${worst:.2f} exceeds the fixed "
            f"${REACH_PROBE_MAX_EXPOSURE_USD:.2f} ceiling; refusing")
    try:
        max_order, max_total = order_caps_fn()
    except Exception as exc:
        raise ProbeRefused(f"cannot read order caps: {exc!r}") from exc
    if worst > float(max_order):
        raise ProbeRefused(
            f"worst case ${worst:.2f} exceeds venue order cap ${max_order:.2f}")
    if float(open_notional_usd) + worst > float(max_total):
        raise ProbeRefused(
            f"open ${float(open_notional_usd):.2f} + probe ${worst:.2f} exceeds "
            f"venue total cap ${max_total:.2f}")
    side = "UP" if str(token_id) == str(market.up_token) else "DOWN"
    return ProbePlan(
        condition_id=str(market.condition_id), token_id=str(token_id), side=side,
        price=price, size=size, tick_size=float(market.tick_size),
        venue_min_size=(float(market.venue_min_size)
                        if market.venue_min_size is not None else None),
        worst_case_usd=worst, best_bid=best_bid, depth_usd=depth_usd,
        discount=discount, hold_sec=float(hold_sec),
    )


def print_plan(plan: ProbePlan, *, live: bool) -> None:
    """The full check on stdout, including the worst case, before signing."""
    venue_min = (f"{plan.venue_min_size:g}sh" if plan.venue_min_size is not None
                 else "unknown (local default)")
    print(f"market   {plan.condition_id[:16]}...  token {plan.side} {plan.token_id[:14]}...")
    print(f"book     best bid {plan.best_bid:.3f}  top-3 depth ${plan.depth_usd:,.2f}")
    print(f"probe    BUY {plan.size:.0f} @ {plan.price:.3f}  (tick {plan.tick_size}, "
          f"venue min {venue_min}, discount {plan.discount:.2f})")
    print(f"worst    ${plan.worst_case_usd:.2f} exposure for {plan.hold_sec:.0f}s hold")
    if not live:
        print("\nDRY RUN -- nothing sent. Re-run with --live to place.")


def resolve_market(condition_id: str | None, *, fetch_pinned_fn,
                   graduated_markets_fn, client, book_fn,
                   open_notional_fn):
    """One pinned market: explicit ID, or the deepest fresh-bid graduated market."""
    if condition_id:
        market = fetch_pinned_fn(condition_id)
        if market is None:
            raise ProbeRefused(
                f"no tradeable market at condition_id {condition_id[:12]}... -- it "
                "is missing, closed, not accepting orders, or does not carry "
                "exactly two tokens. Check the id.")
        preflight_market(client, market, book_fn=book_fn,
                         open_notional_fn=open_notional_fn)
        return market
    graduated = graduated_markets_fn()
    if not graduated:
        raise ProbeRefused("no graduated markets found in runtime/markets.json")
    ranked: list[tuple[float, Any, str, float]] = []
    for gm in graduated:
        cid = getattr(gm, "cid", None) or (gm.get("cid") if isinstance(gm, dict) else None)
        if not cid:
            continue
        try:
            market = fetch_pinned_fn(cid)
        except Exception:
            continue
        if market is None:
            continue
        try:
            token_id, best_bid, depth = preflight_market(
                client, market, book_fn=book_fn, open_notional_fn=open_notional_fn)
        except ProbeRefused:
            continue
        ranked.append((depth, market, token_id, best_bid))
    if not ranked:
        raise ProbeRefused("no graduated market passed pre-flight; refusing")
    ranked.sort(key=lambda r: r[0], reverse=True)
    return ranked[0][1]


def reach_probe_cmd(*, condition_id: str | None = None,
                    discount: float = REACH_PROBE_DEFAULT_DISCOUNT,
                    hold_sec: float = REACH_PROBE_DEFAULT_HOLD_SEC,
                    live: bool = True,
                    db_path: str | Path | None = None,
                    client_fn: Callable[[], Any] | None = None,
                    fetch_pinned_fn: Callable[[str], Any] | None = None,
                    book_fn: Callable[[str], dict] | None = None,
                    open_notional_fn: Callable[[Any], float | None] | None = None,
                    graduated_markets_fn: Callable[[], list] | None = None,
                    order_caps_fn: Callable[[], tuple[float, float]] | None = None,
                    ) -> dict:
    """Run the reachability check. Returns the plan/evidence dict.

    Task 3 delivers pre-flight + dry run: with `live=False` the full plan
    prints and the function returns before signing. The live post/hold/cancel
    leg lands in Task 4.
    """
    from core_brain.config import load as load_cfg
    from core_brain.markets import fetch_pinned_market
    from core_brain.market_feed import load_graduated_markets
    from core_brain.venue import client as build_client, open_notional, order_caps
    from core_brain.book_provider import BookProvider

    cfg = load_cfg()
    client_fn = client_fn or build_client
    fetch_pinned_fn = fetch_pinned_fn or fetch_pinned_market
    graduated_markets_fn = graduated_markets_fn or load_graduated_markets
    open_notional_fn = open_notional_fn or open_notional
    order_caps_fn = order_caps_fn or order_caps
    if book_fn is None:
        _provider = BookProvider()
        book_fn = _provider.get_fresh_book

    _ = db_path  # reserved for the Task-4 pending row (registry write)

    try:
        try:
            client = client_fn()
        except Exception as exc:
            raise ProbeRefused(f"cannot build venue client: {exc!r}") from exc
        market = resolve_market(
            condition_id, fetch_pinned_fn=fetch_pinned_fn,
            graduated_markets_fn=graduated_markets_fn, client=client,
            book_fn=book_fn, open_notional_fn=open_notional_fn)
        token_id, best_bid, depth = preflight_market(
            client, market, book_fn=book_fn, open_notional_fn=open_notional_fn)
        committed = open_notional_fn(client)
        if committed is None:
            raise ProbeRefused("open notional is None; the venue read failed")
        plan = build_plan(
            market, token_id, best_bid, depth, discount=discount,
            hold_sec=hold_sec, cfg=cfg, order_caps_fn=order_caps_fn,
            open_notional_usd=committed)
    except ProbeRefused as exc:
        print(f"REACH-PROBE REFUSED: {exc}")
        raise SystemExit(1) from exc

    print_plan(plan, live=live)
    if not live:
        return {"result": "DRY_RUN", "plan": plan.__dict__}
    print("LIVE execution lands in Task 4; nothing was sent.")
    raise SystemExit(2)
