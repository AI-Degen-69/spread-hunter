"""Canonical close-method taxonomy for the spread-hunter strategy (#520, #558).

One leaf module owns every close-method spelling, its bucket, its writer, and
its legacy status. It imports nothing from ``core_brain`` (``kpi`` imports
``statistical_analytics``, so the reverse import is a cycle; a leaf dissolves
it) and nothing beyond the standard library.

Three rules:

* **Legacy** means readers still accept the name, but no current code writes
  it. Historical rows can exist in the stores, so a stored name is never
  renamed.
* **A settlement is not a rescue.** A settlement is a leg held until the
  market resolves; a rescue is an exit before that point.
* A bucket is the accounting group analytics uses today. Reclassifying a
  close's accounting is follow-up work, not this module's job.

Accounting readers that need a wider population (leg removal, for example)
combine buckets explicitly at the call site; that combination is an
accounting choice, not a second classification.
"""
from __future__ import annotations

from dataclasses import dataclass

# --- method spellings (the only place these strings appear as text) ---------

MERGE = "merge"
SHADOW_MERGE = "shadow_merge"
SINGLE_BUY_EXIT = "single_buy_exit"
ORPHAN_PROFIT_EXIT = "orphan_profit_exit"
LADDER_EXIT = "ladder_exit"
NAKED_EXIT = "naked_exit" 
SHADOW_SETTLEMENT = "shadow_settlement"
SETTLEMENT = "settlement"
VENUE_SYNC = "venue_sync"
SELL = "sell"
AGED_OUT_EXIT = "aged_out_exit"
STOP_LOSS_EXIT = "stop_loss_exit"
SHADOW_MERGE_NOTE = "shadow_merge_note"

# --- buckets -----------------------------------------------------------------

MERGE_BUCKET = "merge"
RESCUE_BUCKET = "rescue"
SETTLEMENT_BUCKET = "settlement"
BOOKKEEPING_BUCKET = "bookkeeping"
OTHER_BUCKET = "other"


@dataclass(frozen=True)
class CloseMethod:
    """One close-method name: its bucket, its writer, and its legacy status."""

    name: str
    bucket: str
    writer: str | None
    legacy: bool
    note: str = ""


# --- the table (declared order fixes the order of the derived lists) ---------

METHODS: tuple[CloseMethod, ...] = (
    # merge: a strategy-chosen pair merge.
    CloseMethod(MERGE, MERGE_BUCKET,
                "core_brain.order_manager._submit_and_log", False),
    CloseMethod(SHADOW_MERGE, MERGE_BUCKET,
                "core_brain.shadow_exec.record_shadow_merges", False),
    # rescue: an exit before market resolution.
    CloseMethod(SINGLE_BUY_EXIT, RESCUE_BUCKET,
                "core_brain.single_buy_saver._record_exit_close", False,
                "written through the method parameter default"),
    CloseMethod(ORPHAN_PROFIT_EXIT, RESCUE_BUCKET,
                "core_brain.single_buy_saver._record_exit_close", False,
                "automated profitable exit of orphaned held leg"),
    CloseMethod(NAKED_EXIT, RESCUE_BUCKET, None, True,
                "legacy U35 single-leg exit shape; no current writer"),
    CloseMethod(LADDER_EXIT, RESCUE_BUCKET, None, True,
                "legacy short-window ladder exit shape; no current writer"),
    # settlement: a leg held until the market resolves.
    CloseMethod(SHADOW_SETTLEMENT, SETTLEMENT_BUCKET,
                "core_brain.market_resolution.book_shadow_settlement", False),
    CloseMethod(SETTLEMENT, SETTLEMENT_BUCKET, None, True,
                "legacy reader name in the tournament cockpit SQL and hub"),
    # bookkeeping.
    CloseMethod(VENUE_SYNC, BOOKKEEPING_BUCKET,
                "core_brain.order_manager.venue_sync", False),
    # other: names a single consumer treats specially.
    CloseMethod(SELL, OTHER_BUCKET,
                "core_brain.order_registry.CloseRecord", False,
                "the dataclass field default; no writer passes it"),
    CloseMethod(AGED_OUT_EXIT, OTHER_BUCKET, None, True,
                "dashboard/static/app.js EXIT_METHODS only; #560 removes it"),
    CloseMethod(STOP_LOSS_EXIT, OTHER_BUCKET, None, True,
                "a run_scorer metric key, never a close method"),
    CloseMethod(SHADOW_MERGE_NOTE, OTHER_BUCKET, None, True,
                "nontrade marker in the tournament hub set and cockpit SQL"),
)


# --- derived lists (built from the table, in declared order) -----------------

MERGE_METHODS = tuple(r.name for r in METHODS if r.bucket == MERGE_BUCKET)
RESCUE_METHODS = tuple(r.name for r in METHODS if r.bucket == RESCUE_BUCKET)

# --- exit reasons that mean a stop forced the sale (#730) -------------------

# Reasons that must sell NOW into the book read inside the exit call. Every
# other route fills against the prior rotation's `queue_marks` snapshot (the
# pre-decision book, adverse-selection protection); a stop-loss priced off a
# stale snapshot books exits the venue never gave (H1/H2). Both the current
# lifecycle string and the older alias route here. It lives in this leaf
# because the shadow fill engine and the pure analytics both read it, and the
# analytics must not import across the live/shadow boundary.
#
# `pair_cap_hedge_unreachable` (#797) joins them: the cap bail-out is decided
# off the live hedge ask and then sells into the book, so measuring it against
# a pre-decision snapshot would book the same fictional fill.
HARD_STOP_SELL_REASONS = frozenset({
    "hard_stop", "lifecycle_hard_stop", "pair_cap_hedge_unreachable",
})
SETTLEMENT_METHODS = tuple(r.name for r in METHODS if r.bucket == SETTLEMENT_BUCKET)
BOOKKEEPING_METHODS = tuple(r.name for r in METHODS if r.bucket == BOOKKEEPING_BUCKET)
ALL_METHOD_NAMES = frozenset(r.name for r in METHODS)
DASHBOARD_FORCED_EXIT_METHODS = RESCUE_METHODS + (AGED_OUT_EXIT, STOP_LOSS_EXIT)
# a close that resolved a taker's position: the rehearsal merge plus the
# three rescue exits. Drives the pessimistic pnl recost (kpi).
TAKER_RESOLVED_METHODS = (SHADOW_MERGE,) + RESCUE_METHODS
