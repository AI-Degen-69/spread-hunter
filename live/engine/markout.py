"""live/engine/markout.py - Out-of-band adverse selection sampler for live trading.

Measures mid-price movement post-fill at standard simulation horizons:
  mid_h0: 300s (5m)
  mid_h1: 3600s (1h)
  mid_h2: 21600s (6h)
  mid_h3: 900s (15m, exit window counterfactual)

Amendment 3 Constraint:
- Must run out-of-band and NEVER block the reconcile or poll loop.
- Network timeouts or book errors leave NULLs rather than delaying registry operations.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

from engine.order_registry import OrderRegistry, DEFAULT_DB_PATH
from engine.config import load as load_cfg

_CFG = load_cfg()
MARKOUT_HORIZONS: tuple[float, ...] = getattr(
    _CFG, "markout_horizons", (300.0, 3600.0, 21600.0, 900.0)
)


def _resolve_leg(
    token_id: Optional[str], mids: dict, side_fallback: str
) -> Optional[str]:
    """Map a fill's token to the UP or DOWN leg of its market.

    Returns None when the token belongs to neither leg -- better to leave the
    markout unsampled than to attribute it to the wrong side of the book.
    """
    if token_id:
        if token_id == mids.get("_up_token"):
            return "UP"
        if token_id == mids.get("_down_token"):
            return "DOWN"
        return None
    return side_fallback if side_fallback in ("UP", "DOWN") else None


def sample_pending_markouts(
    registry: OrderRegistry,
    clob_host: str = "https://clob.polymarket.com",
    now_sec: Optional[float] = None,
    horizons: tuple[float, ...] = MARKOUT_HORIZONS,
) -> int:
    """Sample one pass of due markouts out-of-band. Never raises to caller."""
    from engine.markets import full_book, fetch_pinned_market

    now = now_sec if now_sec is not None else time.time()
    try:
        pending = registry.get_pending_markouts(now, horizons)
    except Exception:
        return 0

    if not pending:
        return 0

    updated_count = 0
    mids_cache: dict[str, dict[str, float]] = {}

    for row in pending:
        cid = row.get("condition_id")
        # `side` on a markout row is the order book's BUY/SELL, never UP/DOWN, so
        # it cannot pick a reference mid on its own. Resolve the leg from the
        # token the fill was on; fall back to the side string only for rows
        # written before token_id existed.
        row_token = row.get("token_id")
        side = str(row.get("side") or "UP").upper()
        h_idx = row.get("_due")
        m_id = row.get("id")
        if cid is None or h_idx is None or m_id is None:
            continue

        # Fetch market mid if not cached
        if cid not in mids_cache:
            try:
                m = fetch_pinned_market(cid, require_rewards=False)
                if m:
                    up_book = full_book(clob_host, m.up_token)
                    dn_book = full_book(clob_host, m.down_token)
                    bb_up, ba_up = up_book.get("best_bid"), up_book.get("best_ask")
                    bb_dn, ba_dn = dn_book.get("best_bid"), dn_book.get("best_ask")
                    # A legitimate price of 0.0 is falsy; test for absence.
                    mid_up = (
                        (bb_up + ba_up) / 2.0
                        if (bb_up is not None and ba_up is not None)
                        else None
                    )
                    mid_dn = (
                        (bb_dn + ba_dn) / 2.0
                        if (bb_dn is not None and ba_dn is not None)
                        else None
                    )
                    mids_cache[cid] = {
                        "UP": mid_up,
                        "DOWN": mid_dn,
                        "_up_token": m.up_token,
                        "_down_token": m.down_token,
                    }
            except Exception:
                mids_cache[cid] = {}

        mids = mids_cache.get(cid, {})
        leg = _resolve_leg(row_token, mids, side)
        mid = mids.get(leg) if leg else None
        if mid is not None:
            # Check if all other horizons are filled
            still_open = any(
                f"mid_h{j}" in row and row[f"mid_h{j}"] is None
                for j in range(len(horizons))
                if j != h_idx
            )
            try:
                registry.update_markout_horizon(m_id, h_idx, mid, last=not still_open)
                updated_count += 1
            except Exception:
                pass

    return updated_count


class MarkoutWorker(threading.Thread):
    """Background daemon thread to sample markouts periodically without blocking main loops."""

    def __init__(
        self,
        registry: Optional[OrderRegistry] = None,
        clob_host: str = "https://clob.polymarket.com",
        interval_sec: float = 10.0,
    ) -> None:
        super().__init__(daemon=True, name="MarkoutSamplerWorker")
        self.registry = registry or OrderRegistry()
        self.clob_host = clob_host
        self.interval_sec = interval_sec
        self.stop_requested = threading.Event()

    def run(self) -> None:
        while not self.stop_requested.is_set():
            try:
                sample_pending_markouts(self.registry, self.clob_host)
            except Exception:
                pass
            self.stop_requested.wait(self.interval_sec)

    def stop(self) -> None:
        self.stop_requested.set()
