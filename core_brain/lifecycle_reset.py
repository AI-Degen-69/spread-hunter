"""One guarded reset operation for merges and stop exits (#646).

A reset proves a market is flat from real reads -- net ledger inventory AND
both token balances -- and only then clears its lifecycle rows and marks it
awaiting re-qualification. Anything less than proof (a residual, a late fill,
an unreadable balance, an unverified cancel) leaves the market under its
existing manager.

Callers inject venue and store behaviour through `ResetPorts`; this module
holds no venue or store code itself, so the pure lifecycle policy stays
import-clean. `single_buy_saver` must import this module lazily (inside
functions): it is imported here at module level for the guard and the
refusal exceptions.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from typing import Callable

from core_brain.order_registry import OrderRegistry, SIZE_EPS

_log = logging.getLogger(__name__)
from core_brain.single_buy_saver import (
    ExposureUnavailable,
    PairExitRefused,
    lifecycle_mutation_guard,
)

RESET = "RESET"
RESIDUAL = "RESIDUAL"
LATE_FILL = "LATE_FILL"
UNAVAILABLE = "UNAVAILABLE"
REFUSED = "REFUSED"


@dataclass(frozen=True)
class ResetPorts:
    """Caller-injected venue/store operations for one reset.

    Every port declares only `PairExitRefused`, `ExposureUnavailable` and
    `sqlite3.Error`. Adapters translate anything else (a reconcile lock, a
    venue SDK error) into one of those three -- the reset maps them to an
    outcome instead of crashing mid-proof.

    - `read_inventory`: net ledger shares `(up, down)` for both tokens,
      fills minus closes. Never a venue read.
    - `fill_watermark`: opaque identity of the latest recorded fill for the
      condition, e.g. `(fill_count, max_recorded_ts_ms)`. Any newly recorded
      fill must change it.
    - `active_order_ids`: resting order ids for the condition, empty when none.
    - `cancel`: cancel the given ids through the existing cancel path.
      Raises `PairExitRefused` when cancellation itself fails.
    - `verify_cancelled`: True once no order for the condition still rests.
      Raises `ExposureUnavailable` when the venue cannot be read -- an
      unreadable book is not an empty book.
    - `reconcile_fills`: durably record late fills (a fresh store reread).
    - `read_balances`: fresh venue balances `(up, down)` for both tokens.
      Raises `ExposureUnavailable` on any failed or incomplete read.
    """

    read_inventory: Callable[[], tuple[float, float]]
    fill_watermark: Callable[[], tuple]
    active_order_ids: Callable[[], list[str]]
    cancel: Callable[[list[str]], None]
    verify_cancelled: Callable[[], bool]
    reconcile_fills: Callable[[], None]
    read_balances: Callable[[], tuple[float, float]]


@dataclass(frozen=True)
class ResetOutcome:
    status: str
    reason: str
    up_residual: float = 0.0
    down_residual: float = 0.0


def _flat(size: float) -> bool:
    return abs(float(size)) <= SIZE_EPS


def _prove_flat(
    up_token_id: str,
    down_token_id: str,
    ports: ResetPorts,
) -> ResetOutcome:
    """Run the proof. Raises only the three declared port exceptions."""
    ports.read_inventory()
    watermark_before = ports.fill_watermark()

    resting = ports.active_order_ids()
    if resting:
        ports.cancel(list(resting))
    if not ports.verify_cancelled():
        return ResetOutcome(
            status=REFUSED, reason="orders still rest after cancel")

    ports.reconcile_fills()
    if ports.fill_watermark() != watermark_before:
        return ResetOutcome(
            status=LATE_FILL, reason="a fill landed after cancel")

    inv_up, inv_down = ports.read_inventory()
    bal_up, bal_down = ports.read_balances()
    residual_up = max(float(inv_up), float(bal_up))
    residual_down = max(float(inv_down), float(bal_down))
    if not (_flat(inv_up) and _flat(inv_down)
            and _flat(bal_up) and _flat(bal_down)):
        return ResetOutcome(
            status=RESIDUAL,
            reason="ledger or venue still holds shares",
            up_residual=residual_up,
            down_residual=residual_down)

    if ports.active_order_ids() or not ports.verify_cancelled():
        return ResetOutcome(
            status=REFUSED, reason="an order re-appeared before finish")
    return ResetOutcome(status=RESET, reason="ledger and venue both flat")


def reset_condition(
    registry: OrderRegistry,
    condition_id: str,
    up_token_id: str,
    down_token_id: str,
    ports: ResetPorts,
    reason: str,
    now_ms: int,
) -> ResetOutcome:
    """Prove one market flat and reset it, or leave it managed.

    Runs under `lifecycle_mutation_guard()`. `begin` blocks fresh entries
    before cancellation starts; every non-RESET outcome abandons that block
    and preserves the lifecycle rows. Lifecycle state, merge routing and exit
    results are never proof -- only the reads count.
    """
    _ = (up_token_id, down_token_id)
    with lifecycle_mutation_guard():
        try:
            generation = registry.begin_lifecycle_reset(
                condition_id, reason, now_ms)
        except sqlite3.Error as exc:
            return ResetOutcome(
                status=UNAVAILABLE, reason=f"registry unreadable: {exc}")
        try:
            outcome = _prove_flat(up_token_id, down_token_id, ports)
        except PairExitRefused as exc:
            outcome = ResetOutcome(status=REFUSED, reason=str(exc) or "cancel refused")
        except ExposureUnavailable as exc:
            outcome = ResetOutcome(
                status=UNAVAILABLE, reason=str(exc) or "venue unreadable")
        except sqlite3.Error as exc:
            outcome = ResetOutcome(
                status=UNAVAILABLE, reason=f"registry unreadable: {exc}")
        if outcome.status != RESET:
            try:
                registry.abandon_lifecycle_reset(condition_id, generation)
            except sqlite3.Error as exc:
                # The block stays, so entries stay blocked: the safe direction.
                _log.warning("reset abandon failed for %s: %s", condition_id, exc)
            return outcome
        reset_ts_ms = int(time.time() * 1000)
        try:
            finished = registry.finish_lifecycle_reset(
                condition_id, generation, reset_ts_ms)
        except sqlite3.Error as exc:
            try:
                registry.abandon_lifecycle_reset(condition_id, generation)
            except sqlite3.Error as abandon_exc:
                _log.warning("reset abandon failed for %s: %s",
                             condition_id, abandon_exc)
            return ResetOutcome(
                status=UNAVAILABLE, reason=f"registry unreadable: {exc}")
        if not finished:
            return ResetOutcome(
                status=REFUSED, reason="superseded by a newer reset")
        return outcome
