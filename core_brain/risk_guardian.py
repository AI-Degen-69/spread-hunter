"""Risk Guardian: High-alert event-driven sentry for unhedged single-leg positions.

Decoupled from universe scanning and quoter loops.
Operates on a tight <= 1.0s interval exclusively when `up_shares != down_shares`.
Evaluates immediate stop-loss limits on all deteriorating positions simultaneously.
Sleeps / idles when exposure is zero.
"""
from __future__ import annotations

import logging
import math
import os
from pathlib import Path
from typing import Any, Callable, Optional

from core_brain.order_registry import OrderRegistry, inventory_from_registry
from core_brain.orphan_trigger import Watch
from core_brain.single_leg_lifecycle import stop_trigger

log = logging.getLogger("risk_guardian")


def _group_condition_tokens(registry: OrderRegistry) -> dict[str, dict[str, str]]:
    """Group the latest UP and DOWN token IDs per condition_id from the quotes ledger."""
    latest: dict[str, dict[str, tuple[float, str]]] = {}
    for q in registry.get_latest_tokens_by_condition_side():
        cid = q.get("condition_id")
        side = str(q.get("side") or "").upper()
        tok = q.get("token_id")
        if not cid or side not in ("UP", "DOWN") or not tok:
            continue
        cid_str = str(cid)
        tok_str = str(tok)
        ts = float(q.get("ts") or 0.0)
        prior = latest.setdefault(cid_str, {}).get(side)
        if prior is None or ts >= prior[0]:
            latest[cid_str][side] = (ts, tok_str)

    return {
        cid: {side: pair[1] for side, pair in sides.items()}
        for cid, sides in latest.items()
    }


def detect_unhedged_positions(
    registry: OrderRegistry,
    db_path: Optional[Path | str] = None,
) -> list[dict[str, Any]]:
    """Identify all active conditions that hold single-leg exposure (up_shares != down_shares)."""
    token_pairs = _group_condition_tokens(registry)
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
                "up_cost": getattr(inv, "up_cost", 0.0),
                "down_cost": getattr(inv, "down_cost", 0.0),
                "naked_shares": diff,
                "heavy_side": "UP" if inv.up_shares > inv.down_shares else "DOWN",
            })
    return unhedged


def build_watches(
    registry: OrderRegistry,
    cfg: Any,
    db_path: Optional[Path | str] = None,
) -> dict[str, Watch]:
    """Build the token-to-Watch map for all currently unhedged positions."""
    token_pairs = _group_condition_tokens(registry)
    if not token_pairs:
        return {}

    # One read per field: the second `getattr` in each pair used to be the
    # legacy alias, and #732 deleted every alias, so it re-read the same name.
    bot_risk_stop_mode = str(getattr(cfg, "bot_risk_stop_mode", None) or "NONE")
    _dc = getattr(cfg, "bot_risk_stop_adverse_drop_cents", None)
    bot_risk_stop_adverse_drop_cents = float(0.05 if _dc is None else _dc)
    _ddp = getattr(cfg, "bot_risk_stop_max_drawdown_pct", None)
    bot_risk_stop_max_drawdown_pct = float(10.0 if _ddp is None else _ddp)
    _edp = getattr(cfg, "bot_risk_circuit_max_drawdown_pct", None)
    bot_risk_circuit_max_drawdown_pct = float(75.0 if _edp is None else _edp)
    _mpc = getattr(cfg, "bot_pos_price_max_pair_cost_usd", None)
    bot_pos_price_max_pair_cost_usd = float(0.995 if _mpc is None else _mpc)
    stranded_completion_on = bool(getattr(cfg, "bot_risk_stranded_completion_on", False))

    real_db = db_path or getattr(registry, "db_path", getattr(registry, "_db_path", None))
    watches: dict[str, Watch] = {}

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
        up_shares = float(getattr(inv, "up_shares", 0.0) or 0.0)
        down_shares = float(getattr(inv, "down_shares", 0.0) or 0.0)
        if abs(up_shares - down_shares) < 1e-4:
            continue

        if up_shares > down_shares:
            heavy_token = up_token
            light_token = down_token
            heavy_shares = up_shares
            heavy_cost = float(getattr(inv, "up_cost", 0.0) or 0.0)
        else:
            heavy_token = down_token
            light_token = up_token
            heavy_shares = down_shares
            heavy_cost = float(getattr(inv, "down_cost", 0.0) or 0.0)

        if heavy_shares <= 0.0 or heavy_cost <= 0.0:
            continue
        held_avg = heavy_cost / heavy_shares
        if not math.isfinite(held_avg) or not (0.0 < held_avg <= 1.0):
            continue

        try:
            stop_level, stop_source = stop_trigger(
                held_avg,
                bot_risk_stop_mode,
                bot_risk_stop_adverse_drop_cents,
                bot_risk_stop_max_drawdown_pct,
                bot_risk_circuit_max_drawdown_pct,
            )
        except ValueError:
            continue

        watches[heavy_token] = Watch(
            condition_id=cid,
            kind="stop",
            level=float(stop_level),
            source=stop_source,
            token_id=heavy_token,
        )

        if stranded_completion_on:
            hedge_cap = min(0.99, bot_pos_price_max_pair_cost_usd)
            hedge_level = round(hedge_cap - held_avg, 4)
            if hedge_level > 0.0:
                watches[light_token] = Watch(
                    condition_id=cid,
                    kind="hedge",
                    level=float(hedge_level),
                    source="stranded_hedge",
                    token_id=light_token,
                )

    return watches


def run_guardian_tick(
    client: Any,
    registry: OrderRegistry,
    cfg: Any,
    *,
    live: bool = False,
    single_buy_saver_fn: Optional[Callable] = None,
    trigger_board: Optional[Any] = None,
    funder: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Execute one high-alert defense evaluation pass across all unhedged positions.

    `funder` is the wallet the lifecycle pass nets exits against (#646 N5).
    Without it the pass has no venue view, and a pair whose close predates its
    last fill stays skipped even when a partial exit left a remainder -- which
    is a naked leg nobody manages. Resolved here from the same env var the
    loop itself uses when the caller does not pass one, because a guardian
    with no view is the case this parameter exists to remove.
    """
    if funder is None:
        funder = (os.environ.get("POLY_FUNDER") or "").strip() or None
    gen = trigger_board.next_generation() if trigger_board is not None else None
    unhedged = detect_unhedged_positions(registry)
    if not unhedged:
        if trigger_board is not None:
            trigger_board.publish({}, generation=gen)
        return []

    log.debug("Risk guardian active on %d unhedged condition(s)", len(unhedged))
    try:
        if single_buy_saver_fn is not None:
            return single_buy_saver_fn(client, registry, cfg, live=live)

        from core_brain.single_buy_saver import manage_single_leg_positions
        return manage_single_leg_positions(client, registry, cfg, live=live,
                                           funder=funder)
    finally:
        if trigger_board is not None:
            try:
                trigger_board.publish(build_watches(registry, cfg), generation=gen)
            except Exception as exc:
                log.warning("trigger board watch refresh failed: %s: %s", type(exc).__name__, exc)
