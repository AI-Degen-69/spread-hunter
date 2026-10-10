"""Stray-order guard: detect single-side orders and positions, adopt complementary detached

legs into proper pairs, and cancel/remediate hopeless strays (Issue #205).
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from core_brain.market_lifecycle import DeadBookBackoff, resolved_condition_ids
from core_brain.order_registry import OrderRecord

log = logging.getLogger("stray_guard")


class StrayClassificationType(str, Enum):
    REGISTRY_PAIRED = "registry_paired"
    COMPLEMENTARY_DETACHED = "complementary_detached"
    HOPELESS_STRAY = "hopeless_stray"
    VIABLE_STRAY = "viable_stray"
    UNHEDGED_POSITION = "unhedged_position"


@dataclass
class ClassifiedOrder:
    order: OrderRecord
    classification: StrayClassificationType
    reason: str = ""


@dataclass
class DetachedPair:
    leg1: OrderRecord
    leg2: OrderRecord
    combined_cost: float


@dataclass
class HopelessStray:
    order: OrderRecord
    reason: str
    opposing_ask: Optional[float] = None


@dataclass
class ClassificationResult:
    registry_paired: list[tuple[str, list[OrderRecord]]] = field(default_factory=list)
    complementary_detached: list[DetachedPair] = field(default_factory=list)
    hopeless_strays: list[HopelessStray] = field(default_factory=list)
    viable_strays: list[ClassifiedOrder] = field(default_factory=list)
    protected: list[OrderRecord] = field(default_factory=list)



def _best_ask_from_book(book: dict | None) -> Optional[float]:
    if not book:
        return None
    asks = book.get("asks") or []
    if not asks:
        return None
    top = asks[0]
    if isinstance(top, (list, tuple)) and len(top) >= 1:
        try:
            return float(top[0])
        except (ValueError, TypeError):
            return None
    elif isinstance(top, dict):
        p = top.get("price")
        if p is not None:
            try:
                return float(p)
            except (ValueError, TypeError):
                return None
    return None


def classify_market_orders(
    orders: list[OrderRecord],
    books: dict[str, Any] | None = None,
    max_pair_cost: float = 0.99,
    market_tokens: dict[str, tuple[str, str]] | None = None,
    protected_order_ids: set[str] | frozenset[str] | None = None,
) -> ClassificationResult:
    """Classify a set of active orders into paired, detached complementary, hopeless, or viable."""
    books = books or {}
    market_tokens = market_tokens or {}
    protected_ids = set(protected_order_ids or ())
    result = ClassificationResult()

    # Group orders by condition_id
    by_condition: dict[str, list[OrderRecord]] = {}
    for o in orders:
        by_condition.setdefault(o.condition_id, []).append(o)

    for cond, cond_orders in by_condition.items():
        # First, group by pair_id to identify healthy registry-linked pairs
        by_pair: dict[str, list[OrderRecord]] = {}
        no_pair: list[OrderRecord] = []
        for o in cond_orders:
            if o.pair_id:
                by_pair.setdefault(o.pair_id, []).append(o)
            else:
                no_pair.append(o)

        unpaired_orders: list[OrderRecord] = list(no_pair)

        for pid, pair_orders in by_pair.items():
            distinct_tokens = {o.token_id for o in pair_orders}
            if len(distinct_tokens) >= 2 and len(pair_orders) == 2:
                result.registry_paired.append((pid, pair_orders))
            else:
                unpaired_orders.extend(pair_orders)

        if not unpaired_orders:
            continue

        # Check for complementary detached orders on this condition
        remaining_unpaired: list[OrderRecord] = []
        used_ids: set[str] = set()

        for i, o1 in enumerate(unpaired_orders):
            if o1.id in used_ids:
                continue
            matched_partner: Optional[OrderRecord] = None
            best_cost = 999.0

            for j, o2 in enumerate(unpaired_orders):
                if i == j or o2.id in used_ids:
                    continue
                if o1.token_id != o2.token_id:
                    cost = o1.price + o2.price
                    if cost <= max_pair_cost and cost < best_cost:
                        matched_partner = o2
                        best_cost = cost

            if matched_partner is not None:
                used_ids.add(o1.id)
                used_ids.add(matched_partner.id)
                result.complementary_detached.append(
                    DetachedPair(leg1=o1, leg2=matched_partner, combined_cost=best_cost)
                )
            else:
                remaining_unpaired.append(o1)

        # For remaining lone orders, check feasibility against opposing book ask
        for o in remaining_unpaired:
            if o.id in protected_ids or (o.order_id and o.order_id in protected_ids):
                result.protected.append(o)
                continue

            tokens = market_tokens.get(cond)
            opposing_token: Optional[str] = None
            if tokens:
                t1, t2 = tokens
                opposing_token = t2 if o.token_id == t1 else t1

            opposing_book = books.get(opposing_token) if opposing_token else None
            ask = _best_ask_from_book(opposing_book)

            if ask is not None:
                if (o.price + ask) >= max_pair_cost:
                    result.hopeless_strays.append(
                        HopelessStray(
                            order=o,
                            reason=f"cost_exceeds_cap: price={o.price:.4f} + ask={ask:.4f} >= {max_pair_cost:.4f}",
                            opposing_ask=ask,
                        )
                    )
                else:
                    result.viable_strays.append(
                        ClassifiedOrder(order=o, classification=StrayClassificationType.VIABLE_STRAY)
                    )
            elif opposing_book is not None and len(opposing_book.get("asks", [])) == 0:
                result.hopeless_strays.append(
                    HopelessStray(order=o, reason="no_ask_on_opposing_book")
                )
            else:
                # No opposing book info available; keep as viable stray until book is read
                result.viable_strays.append(
                    ClassifiedOrder(order=o, classification=StrayClassificationType.VIABLE_STRAY)
                )

    return result



def adopt_detached_legs(
    registry: Any,
    detached_pairs: list[DetachedPair],
    live: bool = True,
) -> list[str]:
    """Adopt complementary detached legs under a shared pair_id in registry.

    Returns list of adopted pair_ids.
    """
    adopted: list[str] = []
    for dp in detached_pairs:
        # Determine shared pair_id: pick an existing valid pair_id, or mint fresh
        p1 = dp.leg1.pair_id
        p2 = dp.leg2.pair_id
        shared_pid: str
        if p1 and p1.startswith("pair-"):
            shared_pid = p1
        elif p2 and p2.startswith("pair-"):
            shared_pid = p2
        elif p1:
            shared_pid = p1
        elif p2:
            shared_pid = p2
        else:
            shared_pid = f"pair-{uuid.uuid4().hex[:12]}"

        order_ids = [dp.leg1.id, dp.leg2.id]
        if live:
            registry.adopt_orders_into_pair(order_ids, shared_pid)
        log.info(
            "ADOPT_STRAY: welded %s (%.4f) and %s (%.4f) under %s (combined=%.4f)",
            dp.leg1.id[:8],
            dp.leg1.price,
            dp.leg2.id[:8],
            dp.leg2.price,
            shared_pid,
            dp.combined_cost,
        )
        adopted.append(shared_pid)
    return adopted


def cancel_hopeless_orders(
    client: Any,
    registry: Any,
    hopeless_strays: list[HopelessStray],
    live: bool = True,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Cancel hopeless resting orders that have no prospect of forming a profitable pair.

    In dry-run (live=False), logs and returns would_cancel without mutating venue or registry.
    """
    now_ms = int((now if now is not None else time.time()) * 1000)
    actions: list[dict[str, Any]] = []

    for hs in hopeless_strays:
        o = hs.order
        if o.status not in ("open", "pending", "partial"):
            continue

        target_venue_id = o.order_id or o.id

        if not live:
            log.info(
                "DRY RUN -- would cancel hopeless stray order %s (venue %s) on %s: %s",
                o.id[:8],
                o.order_id,
                o.condition_id[:10],
                hs.reason,
            )
            actions.append({
                "action": "would_cancel",
                "order_id": target_venue_id,
                "local_id": o.id,
                "reason": hs.reason,
            })
            continue

        # LIVE cancellation
        cancel_success = False
        try:
            from py_clob_client_v2.clob_types import OrderPayload
            client.cancel_order(OrderPayload(orderID=target_venue_id))
            cancel_success = True
        except Exception:
            try:
                client.cancel_order(target_venue_id)
                cancel_success = True
            except Exception as exc:
                log.warning("Failed to cancel hopeless order %s: %s", target_venue_id, exc)
                actions.append({
                    "action": "error",
                    "order_id": target_venue_id,
                    "local_id": o.id,
                    "error": str(exc),
                })
                continue

        if cancel_success:
            registry.update_order_status(
                o.id,
                status="cancelled",
                last_polled_ts=now_ms,
                cancel_reason=f"hopeless_stray: {hs.reason}",
            )
            log.info(
                "CANCEL_STRAY: cancelled order %s (venue %s) on %s: %s",
                o.id[:8],
                o.order_id,
                o.condition_id[:10],
                hs.reason,
            )
            actions.append({
                "action": "cancelled",
                "order_id": target_venue_id,
                "local_id": o.id,
                "reason": hs.reason,
            })

    return actions


def remediate_stray_positions(
    client: Any,
    registry: Any,
    stray_orders_or_positions: list[OrderRecord],
    max_pair_cost: float = 0.99,
    live: bool = True,
    venue_positions: Optional[dict[str, float]] = None,
) -> list[dict[str, Any]]:
    """Remediate unhedged stray filled positions by routing them to exit.

    In dry run (live=False), logs and returns would_exit.
    In live run, assigns a pair_id if missing, then invokes exit_single_buy.
    """
    from core_brain.single_buy_saver import exit_single_buy

    results: list[dict[str, Any]] = []
    for order in stray_orders_or_positions:
        matched = registry.get_size_matched(order.id)
        if matched <= 1e-6:
            continue

        pair_id = order.pair_id
        if not pair_id:
            pair_id = f"pair-stray-{order.id[:8]}"
            if not live:
                results.append({
                    "action": "would_exit",
                    "pair_id": pair_id,
                    "size": matched,
                })
                continue
            registry.adopt_orders_into_pair([order.id], pair_id)

        try:
            exit_res = exit_single_buy(
                client,
                registry,
                pair_id,
                max_pair_cost=max_pair_cost,
                live=live,
                venue_positions=venue_positions,
            )
            results.append(exit_res)
        except Exception as exc:
            log.warning("Failed to exit stray position for pair %s: %s", pair_id, exc)
            results.append({
                "action": "error",
                "pair_id": pair_id,
                "error": str(exc),
            })

    return results


def run_stray_guard(
    client: Any,
    registry: Any,
    cfg: Any = None,
    live: bool = True,
    condition_ids: Optional[list[str]] = None,
    now: Optional[float] = None,
    venue_positions: Optional[dict[str, float]] = None,
    remediate_positions: bool = True,
    resolved_cids: Optional[set[str]] = None,
    book_tracker: Any = None,
) -> dict[str, Any]:
    """Unified Stray-Order Guard execution pass.

    1. Gathers active orders and unpaired filled positions.
    2. Identifies complementary market tokens.
    3. Fetches order books if client supports them.
    4. Classifies orders (paired, detached, hopeless, viable, unhedged).
    5. Adopts complementary detached legs into shared pairs (idempotent).
    6. Cancels hopeless resting orders with no prospect of profitable completion.
    7. Remediates remaining unhedged stray positions via single-buy exit.

    `resolved_cids` (#402): tokens on resolved conditions are omitted from
    book requests -- and resolved orders never reach classification books.
    `book_tracker` (#402): a per-token backoff (`DeadBookBackoff`) gating
    book reads; failures are noted so the next pass waits, successes clear.
    Neither invents a resolution: the tracker holds no registry handle.

    Both default ON when the caller passes nothing: the resolved set is read
    from the store and one backoff tracker is kept on the registry instance,
    because the production callers that reach here every cycle (the poll
    account sweep, the stray CLI, the dashboard) pass neither keyword -- an
    unwired default would leave the dead-book spam the issue names in place.
    An explicit value always wins; `resolved_cids=set()` deliberately means
    "none are resolved".
    """
    if cfg is None:
        try:
            from core_brain.config import load as _load_cfg
            cfg = _load_cfg()
        except Exception:
            cfg = None
    max_pair_cost = float(getattr(cfg, "max_pair_cost", 0.99)) if cfg else 0.99

    if resolved_cids is None:
        resolved_cids = resolved_condition_ids(registry)
    resolved = {str(c).lower() for c in (resolved_cids or ()) if c}
    if book_tracker is None:
        stored = getattr(registry, "_dead_book_backoff", None)
        if isinstance(stored, DeadBookBackoff):
            book_tracker = stored
        else:
            try:
                book_tracker = DeadBookBackoff()
                registry._dead_book_backoff = book_tracker
            except Exception:
                book_tracker = None

    active_orders = registry.get_active_orders()
    unpaired_filled = []
    if hasattr(registry, "get_unpaired_filled_orders"):
        try:
            unpaired_filled = registry.get_unpaired_filled_orders()
        except Exception as exc:
            log.debug("Failed to get unpaired filled orders: %s", exc)

    combined_map = {o.id: o for o in (active_orders + unpaired_filled)}
    candidate_orders = list(combined_map.values())
    if condition_ids:
        candidate_orders = [o for o in candidate_orders if o.condition_id in condition_ids]
    # A resolved condition's orders never reach classification, adoption, or
    # remediation either (#402): with their books withheld they would classify
    # as viable strays, and `remediate_stray_positions` would route a filled
    # leg to `exit_single_buy` -- a dead-book read and a sell attempt on a
    # market that is already over.
    if resolved:
        candidate_orders = [
            o for o in candidate_orders
            if str(o.condition_id or "").lower() not in resolved]

    market_tokens: dict[str, tuple[str, str]] = {}
    for cond in {o.condition_id for o in candidate_orders}:
        found_toks = set(o.token_id for o in candidate_orders if o.condition_id == cond and o.token_id)
        if len(found_toks) < 2 and hasattr(registry, "_conn"):
            try:
                with registry._conn() as conn:
                    rows = conn.execute(
                        "SELECT DISTINCT token_id FROM quotes WHERE condition_id = ?",
                        (cond,),
                    ).fetchall()
                    for r in rows:
                        if r["token_id"]:
                            found_toks.add(r["token_id"])
            except Exception:
                pass
        if len(found_toks) >= 2:
            t_list = list(found_toks)
            market_tokens[cond] = (t_list[0], t_list[1])

    books: dict[str, dict] = {}
    needed_tokens: set[str] = set()
    token_conditions: dict[str, str] = {}
    for o in candidate_orders:
        if o.token_id:
            needed_tokens.add(o.token_id)
            token_conditions.setdefault(o.token_id, o.condition_id or "")
    for (cond, (t1, t2)) in market_tokens.items():
        needed_tokens.add(t1)
        needed_tokens.add(t2)
        token_conditions.setdefault(t1, cond or "")
        token_conditions.setdefault(t2, cond or "")
    # Resolved conditions contribute no book reads: drop their tokens before
    # any venue request, preserving rescue verdict order and the closed /
    # accepting-orders checks downstream (which read no books here).
    needed_tokens = {
        t for t in needed_tokens
        if str(token_conditions.get(t, "")).lower() not in resolved
    }

    if hasattr(client, "get_order_book"):
        for tok in needed_tokens:
            if book_tracker is not None and not book_tracker.allow(tok):
                continue
            try:
                bk = client.get_order_book(tok)
                if bk:
                    books[tok] = bk
                if book_tracker is not None:
                    book_tracker.note_success(tok)
            except Exception as exc:
                if book_tracker is not None:
                    book_tracker.note_failure(tok)
                log.debug("Failed to get order book for %s: %s", tok, exc)

    # Evaluate condition exposure to protect resting hedges covering held inventory
    protected_order_ids: set[str] = set()
    from core_brain.single_buy_saver import load_condition_exposure, ExposureUnavailable
    for cond, (t1, t2) in market_tokens.items():
        try:
            exp = load_condition_exposure(client, registry, cond, (t1, t2))
            if exp.held_deficit > 1e-6 and exp.light_token_id:
                # Protect active BUY orders on the light token
                for o in candidate_orders:
                    if o.condition_id == cond and o.token_id == exp.light_token_id and o.side == "BUY":
                        protected_order_ids.add(o.id)
                        if o.order_id:
                            protected_order_ids.add(o.order_id)
        except ExposureUnavailable as exc:
            log.warning("Exposure unavailable for %s (%s); protecting all active orders on condition", cond, exc)
            for o in candidate_orders:
                if o.condition_id == cond:
                    protected_order_ids.add(o.id)
                    if o.order_id:
                        protected_order_ids.add(o.order_id)
        except Exception as exc:
            log.debug("Exposure check error for %s: %s", cond, exc)

    classification = classify_market_orders(
        candidate_orders,
        books=books,
        max_pair_cost=max_pair_cost,
        market_tokens=market_tokens,
        protected_order_ids=protected_order_ids,
    )


    adopted_pairs = adopt_detached_legs(registry, classification.complementary_detached, live=live)

    cancelled_orders = cancel_hopeless_orders(
        client,
        registry,
        classification.hopeless_strays,
        live=live,
        now=now,
    )

    adopted_order_ids = set()
    for dp in classification.complementary_detached:
        adopted_order_ids.add(dp.leg1.id)
        adopted_order_ids.add(dp.leg2.id)

    remaining_strays_map = {
        o.id: o for o in unpaired_filled
        if o.id not in adopted_order_ids
    }
    for hs in classification.hopeless_strays:
        if hs.order.id not in adopted_order_ids:
            try:
                matched = registry.get_size_matched(hs.order.id)
            except Exception:
                matched = 0.0
            if matched > 1e-6 and hs.order.id not in remaining_strays_map:
                remaining_strays_map[hs.order.id] = hs.order

    remaining_strays = list(remaining_strays_map.values())

    remediated_positions = []
    if remediate_positions and remaining_strays:
        remediated_positions = remediate_stray_positions(
            client,
            registry,
            remaining_strays,
            max_pair_cost=max_pair_cost,
            live=live,
            venue_positions=venue_positions,
        )

    return {
        "classification": classification,
        "adopted_pairs": adopted_pairs,
        "cancelled_orders": cancelled_orders,
        "remediated_positions": remediated_positions,
        "unhedged_positions": remaining_strays,
    }


def format_stray_guard_summary(res: dict[str, Any], live: bool = True) -> str:
    mode_str = "LIVE" if live else "DRY-RUN (--no-live)"
    classification = res.get("classification")
    adopted = res.get("adopted_pairs", [])
    cancelled = res.get("cancelled_orders", [])
    remediated = res.get("remediated_positions", [])
    unhedged = res.get("unhedged_positions", [])

    lines = [
        f"=== STRAY ORDER GUARD [{mode_str}] ===",
    ]
    if classification:
        lines.append("Classification:")
        lines.append(f"  - Paired orders: {len(classification.registry_paired)}")
        lines.append(f"  - Detached pairs: {len(classification.complementary_detached)}")
        lines.append(f"  - Hopeless strays: {len(classification.hopeless_strays)}")
        lines.append(f"  - Viable strays: {len(classification.viable_strays)}")
        lines.append(f"  - Unhedged positions: {len(unhedged)}")

    lines.append("Actions:")
    if adopted:
        lines.append(f"  - Adopted pairs ({len(adopted)}):")
        for p in adopted:
            lines.append(f"    * {p}")
    else:
        lines.append("  - Adopted pairs: 0")

    if cancelled:
        lines.append(f"  - Cancelled orders ({len(cancelled)}):")
        for c in cancelled:
            act = c.get("action")
            oid = c.get("order_id") or c.get("local_id")
            reason = c.get("reason", "")
            lines.append(f"    * [{act}] {oid}: {reason}")
    else:
        lines.append("  - Cancelled orders: 0")

    if remediated:
        lines.append(f"  - Remediated positions ({len(remediated)}):")
        for r in remediated:
            act = r.get("action")
            pid = r.get("pair_id")
            lines.append(f"    * [{act}] {pid}")
    else:
        lines.append("  - Remediated positions: 0")

    lines.append("=" * 35)
    return "\n".join(lines)





