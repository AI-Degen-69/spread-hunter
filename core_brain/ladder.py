"""Short-window ladder path (issue #325): discovery-to-decide adapter.

Productionizes the #324 rehearsal's ladder-mimic decide_fn against the real
`route_quotes`: one `pair_id` per market, one-shot rungs (a filled rung
retires; an exit close retires the market). Drives `run_shadow` through the
`markets_fn`/`decide_fn` seams -- the screener never sees these markets.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Optional

from core_brain.quotes import route_quotes

LADDER_EXIT_METHODS = ("single_buy_exit", "naked_exit", "ladder_exit")


def ladder_pair_id(condition_id: str) -> str:
    """One pair per ladder market: the stamp every rung shares.

    Dash, never colon: merge closes version `tx_hash` as `pair:N`, so a
    colon inside the stamp breaks the version split in `record_shadow_merges`
    (second merge recomputed full size, then died on the UNIQUE constraint).
    """
    return f"ladder-{condition_id}"


def is_ladder_pair(pair_id: str) -> bool:
    """Whether this pair belongs to the ladder path."""
    return str(pair_id or "").startswith("ladder-")


def make_ladder_decide(cfg, markets, *, db_path=None,
                       now_fn: Optional[Callable[[], float]] = None
                       ) -> Callable:
    """A `decide_fn` posting the ladder shape with a rung lifecycle.

    Markets resolve by token: the loop's books carry `token_id`, so each
    visit routes to its market without touching the screener. Without
    `db_path` the lifecycle has nothing to read and every rung posts
    unconditionally (unit-test mode).
    """
    by_token: dict[str, object] = {}
    for m in markets:
        by_token[m.up_token] = m
        by_token[m.down_token] = m

    # Lazy: the store usually does not exist yet when the adapter is built
    # (the loop creates it), so the first decision that finds it opens one
    # read-only handle and keeps it. No handle, no lifecycle.
    state: dict = {"reg": None}

    def reader():
        if state["reg"] is None and db_path is not None \
                and Path(db_path).exists():
            from core_brain.order_registry import OrderRegistry
            state["reg"] = OrderRegistry(db_path=Path(db_path))
        return state["reg"]

    def retired(condition_id: str) -> bool:
        reg = reader()
        if reg is None:
            return False
        try:
            return any(c.get("condition_id") == condition_id
                       and c.get("method") in LADDER_EXIT_METHODS
                       for c in reg.get_all_closes())
        except Exception:
            return True

    def filled_prices(condition_id: str) -> dict[str, set]:
        out: dict[str, set] = {}
        reg = reader()
        if reg is None:
            return out
        try:
            for f in reg.get_all_fills():
                if f.get("condition_id") != condition_id:
                    continue
                out.setdefault(str(f.get("token_id")),
                               set()).add(round(float(f.get("price")), 4))
        except Exception:
            pass
        return out

    def decide(dec_cfg, up_book, down_book, inv, t_rem, wf=None):
        market = by_token.get((up_book or {}).get("token_id"))
        if market is None:
            return [], "not a ladder market"
        if retired(market.condition_id):
            return [], "ladder retired"
        now = now_fn() if now_fn is not None else time.time()
        intents, why = route_quotes(dec_cfg, market, up_book, down_book,
                                    inv, t_rem, wf, now=now)
        done = filled_prices(market.condition_id)
        pid = ladder_pair_id(market.condition_id)
        out = [i for i in intents
               if round(i.price, 4) not in done.get(i.token_id, set())]
        for i in out:
            i.pair_id = pid
        return out, why

    return decide
