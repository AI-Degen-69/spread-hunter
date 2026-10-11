"""Settlement Sweeper: Periodic background capital recycling via CTF merge.

Redeems matched binary outcomes (UP + DOWN) back into $1.00 USDC.
Fully decoupled from the high-alert Risk-On defense sentry and Offense quoter.
Because matched pairs carry zero directional market risk, this pass runs on a
relaxed background cadence (default: 60s - 300s).
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional
from pathlib import Path

from core_brain.order_registry import OrderRegistry, inventory_from_registry

log = logging.getLogger("settlement_sweeper")


def find_mergeable_conditions(
    registry: OrderRegistry,
    min_shares: float = 1.0,
    db_path: Optional[Path | str] = None,
) -> list[dict[str, Any]]:
    """Scan the registry for conditions holding >= min_shares of both UP and DOWN tokens."""
    # Find all conditions with fills
    all_quotes = registry.get_all_quotes()
    token_pairs: dict[str, dict[str, str]] = {}
    for q in all_quotes:
        cid = q.get("condition_id")
        side = str(q.get("side") or "").upper()
        tok = q.get("token_id")
        if cid and side in ("UP", "DOWN") and tok:
            token_pairs.setdefault(cid, {})[side] = tok

    mergeable = []
    for cid, tokens in token_pairs.items():
        if "UP" not in tokens or "DOWN" not in tokens:
            continue
        up_token = tokens["UP"]
        down_token = tokens["DOWN"]
        
        inv = inventory_from_registry(
            condition_id=cid,
            up_token=up_token,
            down_token=down_token,
            db_path=db_path or getattr(registry, "db_path", getattr(registry, "_db_path", None)),
        )
        shares_to_merge = min(inv.up_shares, inv.down_shares)
        if shares_to_merge >= min_shares:
            mergeable.append({
                "condition_id": cid,
                "up_token": up_token,
                "down_token": down_token,
                "shares": shares_to_merge,
                "up_shares": inv.up_shares,
                "down_shares": inv.down_shares,
            })
    return mergeable


def execute_settlement_sweep(
    registry: OrderRegistry,
    merge_fn: Callable[..., Any],
    min_shares: float = 1.0,
    db_path: Optional[Path | str] = None,
    live: bool = True,
) -> list[dict[str, Any]]:
    """Execute merges on all eligible balanced pairs.
    
    Fail-safe: one failing merge does not block other conditions.
    """
    candidates = find_mergeable_conditions(registry, min_shares=min_shares, db_path=db_path)
    results = []
    for cand in candidates:
        cid = cand["condition_id"]
        shares = cand["shares"]
        try:
            log.info("Settlement sweep: merging %s (%.4f shares)", cid, shares)
            res = merge_fn(condition_id=cid, amount=shares, live=live)
            results.append({
                "condition_id": cid,
                "shares": shares,
                "status": "merged",
                "result": res,
            })
        except Exception as exc:
            log.warning("Settlement sweep: merge failed for %s: %s", cid, exc)
            results.append({
                "condition_id": cid,
                "shares": shares,
                "status": "error",
                "error": str(exc),
            })
    return results
