"""The market sweep -- one pass of the fleet engine over one market.

First slice of the sweep extraction (issue #11): the settle-and-cancel step.
The fleet engine used to own this inline inside `strategy.fleet`; it now
lives behind this module's interface so it can be driven and tested
directly: feed a market state and a now, assert the outcome. The
gate/decide steps move here in a later slice (issue #12), leaving the fleet
loop with orchestration only.

The vocabulary is defined in `CONTEXT.md`: the sweep is the per-market unit
of engine work; settle-and-cancel is its first internal step.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from strategy import store

if TYPE_CHECKING:
    from strategy.fleet import MarketState

log = logging.getLogger("sweep")


def record_event(st: "MarketState", now: float, kind: str,
                 reason: str = "", side: str | None = None,
                 price: float | None = None, size: float | None = None,
                 reason_code: str | None = None, force: bool = False) -> None:
    """Write a meaningful operator event, collapsing routine repeats."""
    code = reason_code or store.reason_code(reason)
    key = (kind, side, code)
    previous_key = getattr(st, "event_key", None)
    previous_ts = getattr(st, "event_ts", 0.0)
    if not force and previous_key == key and now - previous_ts < 30.0:
        return
    # A fill/exit/hedge is more informative than the routine requote that
    # follows it in the same visit. Keep the event visible for one short window.
    if (not force and kind == "QUOTING" and previous_key
            and previous_key[0] in {"FILLED", "HEDGED", "MERGED", "EXITED"}
            and now - previous_ts < 30.0):
        return
    try:
        store.log_event(market_slug=getattr(st, "spec", {}).get("slug", ""),
                        condition_id=getattr(st, "cid", None), kind=kind, reason=reason,
                        reason_code=code, side=side, price=price, size=size,
                        ts=now)
        st.event_key, st.event_ts = key, now
    except Exception as e:
        log.warning("event log failed for %s: %s", getattr(st, "title", "market")[:30], e)


def cancel_live_orders(st: "MarketState") -> None:
    """Cancel simulated resting quotes when a market loses eligibility.

    Inventory remains owned and continues to be monitored, but stale offers may
    not survive a hard selector failure or consume committed-capacity budget.
    """
    released = st.engine.open_orders()
    for order in released:
        order.cancelled = True
    try:
        store.mark_cancelled([order.quote_id for order in released
                              if order.quote_id is not None])
    except Exception as e:
        log.warning("selector cancellation not recorded for %s: %s", st.title[:30], e)
    live = st.spec.get("_live")
    if isinstance(live, dict):
        live["quotes"] = []
        live["capital"] = 0.0
        live["stale"] = True
        for field in ("up_bid", "up_ask", "dn_bid", "dn_ask", "mid_up",
                      "our_up", "our_dn_as_up", "dn_bid_as_up", "pair_cost"):
            live[field] = None


def settle_resolved(st: "MarketState", now: float) -> float:
    """The venue has already reported a winner for this market (`resolutions`
    carries a row for it). Its book is gone from the venue -- every further
    fetch is a guaranteed 404 -- and its true settled P&L already comes out
    of `fills` + `resolutions` on the dashboard side (`_settled_positions` /
    `_realized`). Leaving the position "open" here double-books it as both
    realized and still-naked, and is exactly why a resolved market kept
    showing up as a permanent "unreadable" market with capital that never
    freed.

    `ts` IS touched, unlike `_stamp_failure`: these zeroed figures are
    correct as of right now, not stale ones the fleet failed to refresh.

    Returns the dollars of committed cost released (both legs) -- the
    outcome of the step, so the interface is self-describing. The engine
    ignores it; a test can assert it.
    """
    cancel_live_orders(st)
    if st.inv.up_shares or st.inv.down_shares:
        record_event(
            st, now, "RESOLVED",
            f"venue resolved; released {st.inv.up_shares:.2f} UP / "
            f"{st.inv.down_shares:.2f} DOWN", reason_code="RESOLVED",
            force=True)
    freed = (st.inv.up_cost or 0.0) + (st.inv.down_cost or 0.0)
    st.inv.up_shares = st.inv.down_shares = 0.0
    st.inv.up_cost = st.inv.down_cost = 0.0
    st.err = ""
    live = st.spec.get("_live")
    if not isinstance(live, dict):
        live = {}
        st.spec["_live"] = live
    live.update({
        "up_sh": 0.0, "dn_sh": 0.0, "up_avg": 0.0, "dn_avg": 0.0,
        "paired": 0.0, "naked_side": "", "naked_sh": 0.0, "naked_cost": 0.0,
        "pair_paid": 0.0, "fills": st.inv.fills,
        # `stale` back to False, because `cancel_live_orders` above set it
        # True on the way in. That flag means "these figures are older than
        # the fleet's last look at the market", and these figures are the
        # opposite: measured now, against a settlement that is final. Left
        # True the dashboard renders a settled market as permanently STALE
        # while carrying a fresh `ts`, which is the page disagreeing with
        # itself about the one market whose numbers can no longer move.
        "stale": False,
        "err": "", "ts": now,
    })
    return freed


def settle_startup_resolved(states, resolved_cids,
                            now: float) -> tuple[int, float]:
    """Zero inventory for any market the venue has already settled.

    `MarketState.__init__` rebuilds each market's inventory from the fills
    ledger, and the fills ledger never learns about resolutions -- so a market
    that resolved while the fleet was down comes back holding phantom shares
    that count as committed capital from the very first heartbeat. The first
    `visit` would settle it, but only after its turn in the rotation comes
    around -- and if the ranker drops the market before that turn, the re-rank
    retention rule ("still holding inventory") keeps it in `states` forever on
    exactly the phantom position this pass clears.

    Settling here, before the first visit, releases that capital at startup.
    `visit` will still find the cid in `resolved_cids` and settle it again,
    which is a no-op on an already-zeroed inventory.

    Scoped to `states` on purpose: only markets in the universe can display
    phantom inventory, and a resolved market the ranker has already dropped
    simply has no MarketState -- the dashboard's `_settled_positions` /
    `_realized` already reports its P&L straight from `fills` + `resolutions`.

    Returns (markets that held inventory and were settled, dollars of
    committed cost released) for the startup log line. `freed` is the FULL
    two-leg cost basis (`up_cost + down_cost`), so a paired position counts
    both legs -- that is the committed capital being released. One bad market
    must not stop the rest of the pass.
    """
    settled = 0
    freed = 0.0
    for st in states:
        if st.cid not in resolved_cids:
            continue
        try:
            if st.inv.up_shares or st.inv.down_shares:
                settled += 1
                freed += (st.inv.up_cost or 0.0) + (st.inv.down_cost or 0.0)
            settle_resolved(st, now)
        except Exception as e:
            log.warning("startup settle failed for %s: %s: %s",
                        st.title[:30], type(e).__name__, e)
    return settled, freed
