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

import json
import math
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from core_brain.config import VENUE_MIN_NOTIONAL_USD
from core_brain.runtime_paths import runtime_file

#: Hard ceiling on worst-case exposure (size x price).
REACH_PROBE_MAX_EXPOSURE_USD: float = 2.00

REACH_PROBE_DEFAULT_DISCOUNT: float = 0.15
REACH_PROBE_DEFAULT_HOLD_SEC: float = 3.0
REACH_PROBE_HOLD_MIN_SEC: float = 2.0
REACH_PROBE_HOLD_MAX_SEC: float = 4.0

#: Accept-to-cancel-receipt bound the check promises.
REACH_PROBE_CANCEL_DEADLINE_SEC: float = 5.0


class ProbeRefused(Exception):
    """A pre-flight gate or plan bound refused the check. Fail closed."""


class _UnverifiedRead(Exception):
    """The venue would not say whether the probe order filled."""


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
    market_slug: str = ""


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
               open_notional_usd: float,
               max_exposure_usd: float = REACH_PROBE_MAX_EXPOSURE_USD) -> ProbePlan:
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
    if worst > max_exposure_usd:
        raise ProbeRefused(
            f"worst case ${worst:.2f} exceeds the fixed "
            f"${max_exposure_usd:.2f} ceiling; refusing")
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
    slug = str(getattr(market, "market_slug", "") or "")
    if slug.startswith("0x") and len(slug) >= 42:
        slug = ""
    return ProbePlan(
        condition_id=str(market.condition_id), token_id=str(token_id), side=side,
        price=price, size=size, tick_size=float(market.tick_size),
        venue_min_size=(float(market.venue_min_size)
                        if market.venue_min_size is not None else None),
        worst_case_usd=worst, best_bid=best_bid, depth_usd=depth_usd,
        discount=discount, hold_sec=float(hold_sec),
        market_slug=slug,
    )


def print_plan(plan: ProbePlan, *, live: bool) -> None:
    """The full check on stdout, including the worst case, before signing."""
    venue_min = (f"{plan.venue_min_size:g}sh" if plan.venue_min_size is not None
                 else "unknown (local default)")
    slug_label = plan.market_slug or f"{plan.condition_id[:16]}..."
    print(f"market   {slug_label}  token {plan.side} {plan.token_id[:14]}...", flush=True)
    print(f"book     best bid {plan.best_bid:.3f}  top-3 depth ${plan.depth_usd:,.2f}", flush=True)
    print(f"probe    BUY {plan.size:.0f} shares @ {plan.price:.3f}  (tick {plan.tick_size}, "
          f"venue min {venue_min}, discount {plan.discount:.2f})", flush=True)
    print(f"worst    ${plan.worst_case_usd:.2f} exposure for {plan.hold_sec:.0f}s hold", flush=True)
    if not live:
        print("\nDRY RUN -- nothing sent. Re-run with --live to place.", flush=True)


def resolve_market_identifier(identifier: str | None) -> tuple[str | None, str | None]:
    """Resolve a condition ID, slug, path, or Polymarket URL to (condition_id, slug).

    Supports:
    - Raw condition ID: '0xdda9d...'
    - Plain slug: 'cs2-ts7-mouz-2026-10-27'
    - Event path: 'esports/cs2/pgl-masters/cs2-ts7-mouz-2026-10-27'
    - Full URL: 'https://polymarket.com/event/cs2-ts7-mouz-2026-10-27'
    """
    if not identifier:
        return None, None
    s = str(identifier).strip()
    if "?" in s:
        s = s.split("?")[0]
    if "#" in s:
        s = s.split("#")[0]
    s = s.rstrip("/")
    slug_candidate = s.split("/")[-1]

    # If already a hex condition ID
    if s.startswith("0x"):
        cid = s
        resolved_slug = None
        try:
            p = runtime_file("markets.json")
            if p.is_file():
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    for m in data:
                        if isinstance(m, dict) and m.get("cid") == cid:
                            resolved_slug = m.get("slug") or m.get("market_slug")
                            break
        except Exception:
            pass
        return cid, resolved_slug

    # 1. Match local runtime/markets.json
    try:
        p = runtime_file("markets.json")
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list):
                for m in data:
                    if not isinstance(m, dict):
                        continue
                    if m.get("slug") == slug_candidate or m.get("event_slug") == slug_candidate:
                        return m.get("cid"), m.get("slug") or slug_candidate
    except Exception:
        pass

    # 2. Query Gamma API /markets?slug=
    try:
        import requests
        r = requests.get(
            f"https://gamma-api.polymarket.com/markets?slug={slug_candidate}",
            timeout=5, headers={"User-Agent": "Mozilla/5.0"}
        )
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and data:
                for m in data:
                    if isinstance(m, dict) and m.get("slug") == slug_candidate and m.get("conditionId"):
                        return m.get("conditionId"), m.get("slug")
                if isinstance(data[0], dict) and data[0].get("conditionId"):
                    return data[0].get("conditionId"), data[0].get("slug")
    except Exception:
        pass

    # 3. Query Gamma API /events?slug=
    try:
        import requests
        r = requests.get(
            f"https://gamma-api.polymarket.com/events?slug={slug_candidate}",
            timeout=5, headers={"User-Agent": "Mozilla/5.0"}
        )
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and data:
                ev = data[0] if isinstance(data[0], dict) else {}
                markets = ev.get("markets") or []
                for m in markets:
                    if isinstance(m, dict) and m.get("slug") == slug_candidate and m.get("conditionId"):
                        return m.get("conditionId"), m.get("slug")
                if markets and isinstance(markets[0], dict) and markets[0].get("conditionId"):
                    return markets[0].get("conditionId"), markets[0].get("slug")
    except Exception:
        pass

    raise ProbeRefused(
        f"cannot resolve market identifier {identifier!r} (slug {slug_candidate!r}) to a Polymarket condition ID")


def resolve_market(condition_id: str | None, *, fetch_pinned_fn,
                   graduated_markets_fn, client, book_fn,
                   open_notional_fn):
    """One pinned market: explicit ID/slug/URL, or the deepest fresh-bid graduated market."""
    if condition_id:
        cid, resolved_slug = resolve_market_identifier(condition_id)
        cid = cid or condition_id
        market = fetch_pinned_fn(cid)
        if market is None:
            raise ProbeRefused(
                f"no tradeable market at condition_id {cid[:12]}... -- it "
                "is missing, closed, not accepting orders, or does not carry "
                "exactly two tokens. Check the id.")
        curr_slug = getattr(market, "market_slug", "") or ""
        if resolved_slug:
            if hasattr(market, "__dataclass_fields__"):
                market = replace(market, market_slug=resolved_slug)
            else:
                try:
                    market.market_slug = resolved_slug
                except Exception:
                    pass
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
        gm_slug = getattr(gm, "slug", None) or (gm.get("slug") if isinstance(gm, dict) else None)
        curr_slug = getattr(market, "market_slug", "") or ""
        if gm_slug and (not curr_slug or curr_slug == cid[:10] or curr_slug.startswith("0x")):
            if hasattr(market, "__dataclass_fields__"):
                market = replace(market, market_slug=gm_slug)
            else:
                try:
                    market.market_slug = gm_slug
                except Exception:
                    pass
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


def _id_list(values) -> list:
    """Normalize cancel-receipt ID lists (str or dict rows, both spellings)."""
    out: list = []
    for v in values or []:
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, dict):
            for key in ("orderID", "orderId", "order_id", "id"):
                if v.get(key):
                    out.append(str(v.get(key)))
                    break
        elif v is not None:
            out.append(str(v))
    return out


def _short_address(client: Any) -> str:
    try:
        addr = str(client.get_address() or "")
    except Exception:
        return "unknown"
    if not addr:
        return "unknown"
    return (addr[:10] + "...") if len(addr) > 13 else addr


def undo_lines(plan: ProbePlan, pair_id: str, order_id: str | None,
               resting: bool, filled: bool) -> list:
    """What the operator runs next when the result is not PASS."""
    lines = [f"REACH-PROBE result is not PASS (worst case was "
             f"${plan.worst_case_usd:.2f})."]
    if resting and order_id:
        lines.append(f"IF RESTING: python -m core_brain.order_manager cancel {order_id} --live")
    if filled:
        lines.append(f"IF FILLED:  python -m core_brain.order_manager exit {pair_id} --live")
    lines.append("ALWAYS:     python -m core_brain.order_manager poll --once")
    return lines


def execute_live(client: Any, plan: ProbePlan, *, cfg, registry,
                 sleep_fn: Callable[[float], None] | None = None,
                 time_fn: Callable[[], float] | None = None,
                 evidence_dir: str | Path | None = None,
                 stream_fn: Callable[[str], None] | None = None) -> dict:
    """Post one post-only GTC buy, hold, cancel by exact ID, verify. Never raises."""
    import json as _json
    import time as _time_mod
    import uuid as _uuid
    from types import SimpleNamespace as _NS

    from py_clob_client_v2.clob_types import (
        OrderArgsV2, OrderType, PostOrdersV2Args, OrderPayload,
    )
    from py_clob_client_v2.order_builder.constants import BUY
    from core_brain.order_registry import OrderRecord
    from core_brain.single_buy_saver import _venue_matched
    from core_brain.venue import venue_order_id

    stream = stream_fn or (lambda msg: print(f"[stream] {msg}", flush=True))
    sleep_fn = sleep_fn or _time_mod.sleep
    time_fn = time_fn or _time_mod.time
    now_ms = int(time_fn() * 1000)
    local_id = str(_uuid.uuid4())
    pair_id = f"pair-{_uuid.uuid4().hex[:12]}"

    evidence: dict = {
        "condition_id": plan.condition_id,
        "market_slug": plan.market_slug,
        "token_id": plan.token_id,
        "price": plan.price,
        "size": plan.size,
        "tick_size": plan.tick_size,
        "venue_min_size": (plan.venue_min_size
                             if plan.venue_min_size is not None else "unknown"),
        "worst_case_usd": plan.worst_case_usd,
        "hold_sec": plan.hold_sec,
        "pair_id": pair_id,
        "local_id": local_id,
        "funder": _short_address(client),
        "order_id": None,
        "canceled": [],
        "not_canceled": [],
    }

    def _write() -> dict:
        dest_dir = Path(evidence_dir) if evidence_dir else None
        if dest_dir is None:
            path = runtime_file(
                f"reach_probe_{int(time_fn())}_{local_id[:8]}.json")
            path.parent.mkdir(parents=True, exist_ok=True)
        else:
            dest_dir.mkdir(parents=True, exist_ok=True)
            path = dest_dir / f"reach_probe_{int(time_fn())}_{local_id[:8]}.json"
        path.write_text(_json.dumps(evidence, indent=2, default=str))
        evidence["evidence_file"] = str(path)
        return evidence

    # Row first, then send -- a crash between send and write must never leave
    # a live order untracked (same rule as `quote`).
    registry.create_order(OrderRecord(
        id=local_id, order_id=None, condition_id=plan.condition_id,
        token_id=plan.token_id, side="BUY", price=plan.price,
        original_size=plan.size, status="pending", posted_ts=now_ms,
        last_polled_ts=now_ms, pair_id=pair_id,
        max_pair_cost_at_post=cfg.bot_pos_price_max_pair_cost_usd,
    ))

    stream(f"Placing order on venue: BUY {plan.size:.0f} shares @ ${plan.price:.3f} (post-only GTC)...")
    try:
        signed = client.create_order(OrderArgsV2(
            price=plan.price, size=plan.size, side=BUY, token_id=plan.token_id,
            expiration=0))
    except Exception as exc:
        evidence["sign_error"] = type(exc).__name__
        registry.update_order_status(local_id, status="cancelled",
                                     last_polled_ts=int(time_fn() * 1000))
        evidence["result"] = "FAIL_sign_error"
        evidence["undo"] = undo_lines(plan, pair_id, None, False, False)
        return _write()
    batch = [PostOrdersV2Args(order=signed, orderType=OrderType.GTC)]
    try:
        resp = client.post_orders(batch, post_only=True)
    except Exception as exc:
        evidence["post_error"] = f"{type(exc).__name__}"
        evidence.update(_no_id_fallback(client, plan))
        evidence["result"] = "uncertain"
        evidence["undo"] = undo_lines(plan, pair_id, None, False, False)
        return _write()
    t_accept = time_fn()
    evidence["ts_accept"] = t_accept
    items = resp if isinstance(resp, list) else [resp]
    vid = venue_order_id(items[0]) if items else None
    if not vid:
        evidence.update(_no_id_fallback(client, plan))
        evidence["result"] = "uncertain"
        evidence["undo"] = undo_lines(plan, pair_id, None, False, False)
        return _write()
    evidence["order_id"] = vid
    stream(f"Placed order {vid} on venue. Verifying readback...")

    try:
        raw = client.get_order(vid)
    except Exception as exc:
        evidence["readback_error"] = f"{type(exc).__name__}"
        _try_exact_cancel(client, vid)
        evidence["result"] = "uncertain"
        evidence["undo"] = undo_lines(plan, pair_id, vid, True, False)
        return _write()
    venue_token = None
    if isinstance(raw, dict):
        for key in ("asset_id", "token_id", "tokenId", "assetId"):
            if raw.get(key):
                venue_token = str(raw.get(key))
                break
    else:
        for key in ("asset_id", "token_id", "tokenId", "assetId"):
            if getattr(raw, key, None):
                venue_token = str(getattr(raw, key))
                break
    if venue_token != plan.token_id:
        evidence["token_mismatch"] = str(venue_token)
        _try_exact_cancel(client, vid)
        evidence["result"] = "FAIL_token_mismatch"
        evidence["undo"] = undo_lines(plan, pair_id, vid, True, False)
        return _write()
    registry.attach_venue_order_id(local_id, vid, status="open",
                                   last_polled_ts=int(time_fn() * 1000))
    raw_status = raw.get("status") if isinstance(raw, dict) else "open"
    stream(f"Order details verified: ID {vid}, {plan.size:.0f} shares @ ${plan.price:.3f}, status={raw_status}. Resting for {plan.hold_sec:.0f}s...")

    sleep_fn(plan.hold_sec)
    stream(f"Cancelling order {vid} by exact ID...")
    try:
        receipt = client.cancel_order(OrderPayload(orderID=vid))
    except Exception as exc:
        evidence["cancel_error"] = f"{type(exc).__name__}: {exc}"
        evidence["result"] = "FAIL_cancel_error"
        evidence["undo"] = undo_lines(plan, pair_id, vid, True, False)
        return _write()
    t_receipt = time_fn()
    evidence["ts_cancel_receipt"] = t_receipt
    evidence["accept_to_receipt_sec"] = round(t_receipt - t_accept, 3)
    canceled = _id_list((receipt or {}).get("canceled")
                         if isinstance(receipt, dict) else [])
    raw_nc = (receipt or {}).get("not_canceled") if isinstance(receipt, dict) else None
    if raw_nc is None and isinstance(receipt, dict):
        raw_nc = receipt.get("notCanceled")
    not_canceled = _id_list(raw_nc)
    evidence["canceled"] = canceled
    evidence["not_canceled"] = not_canceled
    cancelled_ok = vid in canceled and vid not in not_canceled

    try:
        matched = _venue_matched(client, [_NS(order_id=vid)],
                                 refusal=_UnverifiedRead)
    except _UnverifiedRead:
        matched = None
    evidence["matched_size"] = matched
    stream(f"Cancel receipt received in {evidence['accept_to_receipt_sec']:.2f}s (matched: {matched if matched is not None else 0} shares)")
    try:
        resting_book = client.get_open_orders() or []
    except Exception:
        resting_book = None
    if resting_book is None:
        evidence["result"] = "FAIL_open_orders_unreadable"
        evidence["undo"] = undo_lines(plan, pair_id, vid, True,
                                       filled=matched is not None and matched > 0)
        return _write()
    absent = not any(str(o.get("id") or o.get("order_hash")
                         or o.get("orderID") or "") == vid
                     for o in resting_book if isinstance(o, dict))
    evidence["status"] = None
    if isinstance(raw, dict):
        evidence["status"] = raw.get("status")

    filled = matched is not None and matched > 0
    slow = evidence["accept_to_receipt_sec"] > REACH_PROBE_CANCEL_DEADLINE_SEC
    if cancelled_ok and matched == 0 and absent:
        registry.update_order_status(local_id, status="cancelled",
                                     last_polled_ts=int(time_fn() * 1000))
    if cancelled_ok and matched == 0 and absent and not slow:
        evidence["result"] = "PASS"
        stream(f"Cancelled order {vid} successfully: 0 fills, confirmed absent from open orders.")
        return _write()
    if matched is None:
        evidence["result"] = "unverified"
    elif filled:
        evidence["result"] = "FAIL_filled"
    elif not cancelled_ok:
        evidence["result"] = "FAIL_not_canceled"
    elif not absent:
        evidence["result"] = "FAIL_still_resting"
    else:
        evidence["result"] = "FAIL_slow_cancel"
    evidence["undo"] = undo_lines(plan, pair_id, vid, resting=not absent,
                                   filled=filled)
    return _write()


def _try_exact_cancel(client: Any, venue_id: str) -> None:
    """Best-effort exact-ID cancel; never raises, never touches other orders."""
    try:
        from py_clob_client_v2.clob_types import OrderPayload as _Payload
        client.cancel_order(_Payload(orderID=venue_id))
    except Exception:
        pass


def _no_id_fallback(client: Any, plan: ProbePlan) -> dict:
    """No venue ID came back: guarded market fallback, row stays pending."""
    try:
        from py_clob_client_v2.clob_types import OrderMarketCancelParams
        client.cancel_market_orders(OrderMarketCancelParams(market=plan.condition_id))
        return {"fallback": "cancel-market attempted (pre-flight proved zero "
                             "other account orders on this market)"}
    except Exception as exc:
        return {"fallback": f"cancel-market failed: {type(exc).__name__}"}


def reach_probe_cmd(condition_id: str | None = None, *,
                    discount: float = REACH_PROBE_DEFAULT_DISCOUNT,
                    hold_sec: float = REACH_PROBE_DEFAULT_HOLD_SEC,
                    max_exposure: float = REACH_PROBE_MAX_EXPOSURE_USD,
                    live: bool = True,
                    db_path: str | Path | None = None,
                    client_fn: Callable[[], Any] | None = None,
                    fetch_pinned_fn: Callable[[str], Any] | None = None,
                    book_fn: Callable[[str], dict] | None = None,
                    open_notional_fn: Callable[[Any], float | None] | None = None,
                    graduated_markets_fn: Callable[[], list] | None = None,
                    order_caps_fn: Callable[[], tuple[float, float]] | None = None,
                    sleep_fn: Callable[[float], None] | None = None,
                    time_fn: Callable[[], float] | None = None,
                    evidence_dir: str | Path | None = None,
                    stream_fn: Callable[[str], None] | None = None,
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

    stream = stream_fn or (lambda msg: print(f"[stream] {msg}", flush=True))
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

    stream(f"Peeking market '{condition_id or 'graduated feed'}'...")
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
        slug_label = market.market_slug or market.condition_id[:16]
        stream(f"Peeking book: best bid ${best_bid:.3f}, top-3 depth ${depth:,.2f} on {slug_label}")
        committed = open_notional_fn(client)
        if committed is None:
            raise ProbeRefused("open notional is None; the venue read failed")
        plan = build_plan(
            market, token_id, best_bid, depth, discount=discount,
            hold_sec=hold_sec, cfg=cfg, order_caps_fn=order_caps_fn,
            open_notional_usd=committed,
            max_exposure_usd=min(float(max_exposure), REACH_PROBE_MAX_EXPOSURE_USD))
        stream(f"Order details: BUY {plan.size:.0f} shares @ ${plan.price:.3f} (${plan.worst_case_usd:.2f} max exposure, {plan.hold_sec:.0f}s hold)")
    except ProbeRefused as exc:
        print(f"REACH-PROBE REFUSED: {exc}")
        raise SystemExit(1) from exc

    print_plan(plan, live=live)
    if not live:
        stream("DRY RUN complete -- no orders placed. Re-run with --live to place.")
        return {"result": "DRY_RUN", "plan": plan.__dict__}

    import time as _time_mod
    from core_brain.order_registry import OrderRegistry, DEFAULT_DB_PATH

    registry = OrderRegistry(
        db_path=Path(db_path) if db_path else DEFAULT_DB_PATH)
    evidence = execute_live(
        client, plan, cfg=cfg, registry=registry,
        sleep_fn=sleep_fn, time_fn=time_fn, evidence_dir=evidence_dir,
        stream_fn=stream)
    if evidence["result"] != "PASS":
        for line in evidence.get("undo", []):
            print(line)
        raise SystemExit(1)
    stream(f"REACH-PROBE PASS: order {evidence['order_id']} cancelled, "
          f"matched 0, receipt in {evidence['accept_to_receipt_sec']:.2f}s")
    return evidence
