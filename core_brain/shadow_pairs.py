"""Shadow pair-manager: keep COMPLETE resting pairs, resolve orphans.

Operator directive 2026-10-12. The shadow engine must manage pairs the way the
strategy intends: strive to hold a complete resting UP+DOWN pair per condition,
and never leave an orphan -- a resting order with no paired resting order and no
open position -- sitting unhedged.

For each condition, every sweep:

  1. ADOPT: two detached legs on the same condition under different tokens whose
     combined cost <= max_pair_cost are welded under ONE shared pair_id (reusing
     an existing `pair-` id when either leg carries one, else minting). This is
     what makes the dashboard show one row and lets the merge fire.

  2. CANCEL: a leg with no completing partner at the LIVE book -- combined cost
     with the opposing best ask >= max_pair_cost, or the opposing book is gone --
     is a hopeless stray. Cancel it (registry-only in shadow; no venue call).

  3. RE-QUOTE FRESH: after cancelling, the condition is left to the normal
     decide/quote step, which re-quotes a fresh complete pair from the current
     book. The manager's job is to CLEAR the dead legs so the fresh quote is not
     competing with orphans; it does not itself place orders.

Every decision reads the LIVE book passed in -- no snapshots. Pure: given
orders + books in, a plan out; no clock, no network, no SQLite. The shadow sweep
applies the plan (registry writes) under the single-executor lifecycle guard.
"""
from __future__ import annotations

from dataclasses import dataclass, field

WORKING_STATUSES = ("open", "partial", "pending")


@dataclass
class PairPlan:
    """What the manager wants done for one condition."""
    condition_id: str
    adopt: list[tuple[str, str, str]] = field(default_factory=list)
    # (leg1_local_id, leg2_local_id, shared_pair_id)
    cancel: list[str] = field(default_factory=list)  # local_ids to cancel
    reason: list[str] = field(default_factory=list)  # human log lines


def _best_ask(book) -> float | None:
    """Best (lowest) ask price from a normalized book dict, or None."""
    if not book:
        return None
    asks = book.get("asks")
    if isinstance(asks, dict):
        prices = [float(p) for p in asks if _finite(p)]
        return min(prices) if prices else None
    if isinstance(asks, (list, tuple)):
        prices = []
        for lvl in asks:
            p = lvl[0] if isinstance(lvl, (list, tuple)) else (
                lvl.get("price") if isinstance(lvl, dict) else None)
            try:
                p = float(p)
            except (TypeError, ValueError):
                continue
            prices.append(p)
        return min(prices) if prices else None
    return None


def _finite(x) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    import math
    return math.isfinite(f)


def plan_pairs(
    condition_id: str,
    orders: list,
    books: dict[str, dict],
    market_tokens: dict[str, tuple[str, str]] | None,
    bot_pos_price_max_pair_cost_usd: float,
    mint_pair_id,
) -> PairPlan:
    """Decide adopt/cancel for one condition's working orders.

    `orders`: working OrderRecords on this condition. `books`: token_id -> book.
    A None book means that token's read failed: unknown, not hopeless, so a
    leg waiting on it is deferred, never cancelled on missing evidence.
    `market_tokens`: condition -> (token_a, token_b) so we know the opposing leg.
    `mint_pair_id`: callable returning a fresh `pair-...` id (kept injectable for
    tests). Reads only these inputs; performs no writes.
    """
    plan = PairPlan(condition_id=condition_id)
    working = [o for o in orders
               if o.status in WORKING_STATUSES and o.token_id]
    if not working:
        return plan

    tokens = (market_tokens or {}).get(condition_id)
    # Group working orders by token.
    by_token: dict[str, list] = {}
    for o in working:
        by_token.setdefault(str(o.token_id), []).append(o)

    # --- ADOPT: match one leg on each of two different tokens, cheapest combo
    # under the cap, weld under one shared pair_id. -------------------------
    token_ids = list(by_token.keys())
    used: set[str] = set()
    for i, ta in enumerate(token_ids):
        for tb in token_ids[i + 1:]:
            if tb == ta:
                continue
            best = None  # (combined, oa, ob)
            for oa in by_token[ta]:
                if oa.id in used:
                    continue
                for ob in by_token[tb]:
                    if ob.id in used:
                        continue
                    combined = float(oa.price) + float(ob.price)
                    if combined <= bot_pos_price_max_pair_cost_usd and (
                            best is None or combined < best[0]):
                        best = (combined, oa, ob)
            if best is not None:
                _combined, oa, ob = best
                shared = _shared_pair_id(oa, ob, mint_pair_id)
                plan.adopt.append((oa.id, ob.id, shared))
                used.add(oa.id)
                used.add(ob.id)
                plan.reason.append(
                    f"adopt {oa.id[:8]}+{ob.id[:8]} under {shared} "
                    f"(combined {_combined:.2f} <= {bot_pos_price_max_pair_cost_usd:.2f})")

    # --- CANCEL: a working leg with no partner resting AND no completing ask
    # at the live book is a hopeless stray. ---------------------------------
    opposing = {}
    if tokens and len(tokens) == 2:
        ta, tb = tokens
        opposing = {str(ta): str(tb), str(tb): str(ta)}

    for o in working:
        if o.id in used:
            continue
        opp_tok = opposing.get(str(o.token_id))
        opp_book = books.get(opp_tok) if opp_tok else None
        if opp_tok is not None and opp_book is None and opp_tok in books:
            # Opposing book read failed: unknown, not hopeless. Defer the
            # decision to the next rotation instead of cancelling on no ask.
            plan.reason.append(
                f"defer {o.id[:8]}: opposing book unavailable")
            continue
        ask = _best_ask(opp_book)
        # No partner resting on the other token for this leg's own pair:
        partner_rests = any(
            (x.id != o.id and x.id not in used and str(x.token_id) != str(o.token_id))
            for x in working)
        if partner_rests:
            continue  # a complement rests; the pair pass / decide owns it
        if ask is None:
            plan.cancel.append(o.id)
            plan.reason.append(
                f"cancel {o.id[:8]}: orphan, no opposing book to complete against")
        elif float(o.price) + ask >= bot_pos_price_max_pair_cost_usd:
            plan.cancel.append(o.id)
            plan.reason.append(
                f"cancel {o.id[:8]}: orphan, {o.price:.2f}+ask {ask:.2f} "
                f">= {bot_pos_price_max_pair_cost_usd:.2f}")
        # else: a profitable completion exists -- leave it for the pairs pass,
        # which crosses to complete rather than cancelling a completable leg.

    return plan


def _shared_pair_id(oa, ob, mint_pair_id) -> str:
    """One stable pair_id for two legs being welded: reuse a `pair-` id if
    either leg carries one, else mint. Never splices a non-`pair-` id."""
    for cand in (getattr(oa, "pair_id", None), getattr(ob, "pair_id", None)):
        if cand and str(cand).startswith("pair-"):
            return str(cand)
    return mint_pair_id()
