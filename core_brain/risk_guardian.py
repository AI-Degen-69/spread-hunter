"""Risk Guardian: High-alert event-driven sentry for unhedged single-leg positions.

Decoupled from universe scanning and quoter loops.
Operates on a tight <= 1.0s interval exclusively when `up_shares != down_shares`.
Evaluates immediate stop-loss limits on all deteriorating positions simultaneously.
Sleeps / idles when exposure is zero.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional, Sequence
from pathlib import Path

from core_brain.order_registry import OrderRegistry, inventory_from_registry

log = logging.getLogger("risk_guardian")


def detect_unhedged_positions(
    registry: OrderRegistry,
    db_path: Optional[Path | str] = None,
) -> list[dict[str, Any]]:
    """Identify all active conditions that hold single-leg exposure (up_shares != down_shares)."""
    all_quotes = registry.get_all_quotes()
    token_pairs: dict[str, dict[str, str]] = {}
    for q in all_quotes:
        cid = q.get("condition_id")
        side = str(q.get("side") or "").upper()
        tok = q.get("token_id")
        if cid and side in ("UP", "DOWN") and tok:
            token_pairs.setdefault(cid, {})[side] = tok

    unhedged = []
    real_db = db_path or getattr(registry, "db_path", getattr(registry, "_db_path", None))
    for cid, tokens in token_pairs.items():
        if "UP" not in tokens or "DOWN" not in tokens:
            continue
        up_token = tokens["UP"]
        down_token = tokens["DOWN"]
        inv = inventory_from_registry(
            condition_id=cid,
            up_token=up_token,
            down_token=down_token,
            db_path=real_db,
        )
        diff = abs(inv.up_shares - inv.down_shares)
        if diff >= 1e-4:
            unhedged.append({
                "condition_id": cid,
                "up_token": up_token,
                "down_token": down_token,
                "up_shares": inv.up_shares,
                "down_shares": inv.down_shares,
                "naked_shares": diff,
                "heavy_side": "UP" if inv.up_shares > inv.down_shares else "DOWN",
            })
    return unhedged


def run_guardian_tick(
    client: Any,
    registry: OrderRegistry,
    cfg: Any,
    *,
    live: bool = False,
    single_buy_saver_fn: Optional[Callable] = None,
) -> list[dict[str, Any]]:
    """Execute one high-alert defense evaluation pass across all unhedged positions."""
    unhedged = detect_unhedged_positions(registry)
    if not unhedged:
        return []

    log.debug("Risk guardian active on %d unhedged condition(s)", len(unhedged))
    if single_buy_saver_fn is not None:
        return single_buy_saver_fn(client, registry, cfg, live=live)

    # Fallback to direct single_buy_saver
    from core_brain.single_buy_saver import manage_single_leg_positions
    return manage_single_leg_positions(client, registry, cfg, live=live)
