"""Live fleet loop: decide -> submit -> reconcile, reusing the risk gates.

The decision is `core_brain.quotes.decide_quotes`
-- already proven in the paper run and already wired to the live risk gates -- so
this module adds NOTHING new to the decision. Its only jobs are:

1. `plan_orders`: turn "what we want resting" into "what to cancel / submit"
   without churning an order already resting at the desired price.
2. `run`: the rotation loop that ties reconcile -> decide -> submit -> sweep
   together, and never lets one market's error stop the others.

Everything that talks to the venue (fetch market, fetch books, decide, submit,
cancel, reconcile, sweep) is injectable, so the loop's behavior is tested
without a network and the production path is wired in `main`.
"""
from __future__ import annotations

import argparse
import logging
import math
import os
import sys
import time
import threading
import uuid
from collections import deque
from contextlib import nullcontext
from dataclasses import dataclass, field, replace
from enum import Enum
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:  # annotation only -- markets is imported lazily at the call site
    from core_brain.markets import SellFlow

from core_brain.quotes import (
    Inventory, QuoteIntent, dynamic_offset_for, evaluate_market_quote,
    mid_price,
)
from core_brain.rotation_priority import order_by_exposure
from core_brain.cycle_stream import emit as _emit_cycle_event
from core_brain.market_lifecycle import (
    LifecycleStop, classify_refusal, resolved_condition_ids,
)
from core_brain.order_registry import InstanceInUse, OrderRegistry
from core_brain import risk
from core_brain.single_leg_lifecycle import (
    LegState,
    LifecycleDecision,
    LifecycleQuoteContext,
    LifecycleQuoteOverride,
    SingleLegPosition,
    evaluate as evaluate_single_leg_lifecycle,
)

log = logging.getLogger("main_spread_hunter_loop")

LIVE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = LIVE_ROOT
RUN = LIVE_ROOT / "runtime"


@dataclass
class LiveFleetResult:
    """One market's outcome from a single rotation visit."""
    status: str                       # QUOTED | DRY_RUN | DECLINED |
                                      # CANCELLED | WARNED | ERROR
    condition_id: str = ""
    title: str = ""
    why: str = ""
    intents: list = field(default_factory=list)
    submitted: int = 0
    cancelled: int = 0
    error: str = ""
    #: The queue-clear gate's reading for this visit's new placements (#393):
    #: the named reason when the queue ahead is too deep or too quiet to clear,
    #: empty when it clears or when nothing was measured. Record-only at ship, so
    #: a non-empty value usually means "reported, not refused".
    queue_why: str = ""


# Why an order was cancelled. Recorded on the row so a cancel that defended the
# strategy can be told apart from one that threw away queue position for
# nothing -- on shadow-02, 23 of 25 orders were cancelled with a median lifetime
# of 37s and nothing in the registry said which kind each one was.
CANCEL_NOT_QUOTED = "not_quoted"          # we no longer quote this token at all
CANCEL_PRICE_MOVED = "price_moved"        # the desired price left the tolerance
CANCEL_MARKET_DROPPED = LifecycleStop.MARKET_DROPPED.code  # market left universe
# Why a quote was cancelled for UMA resolution state (#408). Entries for
# #402's single discard list: per-status reasons naming the UMA status that
# flagged the market.
CANCEL_UMA_RESOLUTION_PROPOSED = "uma_resolution_proposed"
CANCEL_UMA_RESOLUTION_DISPUTED = "uma_resolution_disputed"
CANCEL_UMA_RESOLUTION_RESOLVED = "uma_resolution_resolved"
UMA_RESOLUTION_STATUS_REASONS = {
    "proposed": CANCEL_UMA_RESOLUTION_PROPOSED,
    "disputed": CANCEL_UMA_RESOLUTION_DISPUTED,
    "resolved": CANCEL_UMA_RESOLUTION_RESOLVED,
}

# Attribute attached to exceptions when submit raises after placing some legs.
PARTIAL_SUBMIT_PLACED_ATTR = "placed"


class VisitOutcome(Enum):
    """What one market visit decided, beyond the intents list."""
    QUOTED = "quoted"                    # intents present (or no opinion)
    REFUSED_TRANSIENT = "refused_transient"  # visited, refused: transient reason
    REFUSED_TERMINAL = "refused_cancel"  # visited, refused for good: named stop


def _classify_refusal(why: str) -> VisitOutcome:
    """Terminal or transient, from `decide`'s refusal reason (pure).

    Delegates to the single enumerated list in `market_lifecycle`: a named
    stop refuses for good, everything else (wide book, completable cap,
    quote window, mid band, ...) is transient -- the book moved, not the
    market. Matched case-insensitively.
    """
    if classify_refusal(why) is not None:
        return VisitOutcome.REFUSED_TERMINAL
    return VisitOutcome.REFUSED_TRANSIENT


def _visit_stop(why: str) -> Optional[LifecycleStop]:
    """Name the lifecycle stop for a refused visit (pure)."""
    return classify_refusal(why or "")



def _note_lifecycle_stop(
    seam: VenueSeam,
    cid: str,
    title: str,
    stop: Optional[LifecycleStop],
    text: str,
    memory: Optional[dict],
    cycle: int = 0,
    emit_fn: Optional[Callable] = None,
) -> bool:
    """Log + store one lifecycle stop, on change only (#402).

    `memory` maps condition id -> last stop code for this loop; a repeated
    code writes nothing, and a quote in between (which clears the entry)
    re-arms the row. Returns True when a row was emitted.
    """
    if stop is None:
        return False
    if memory is None:
        memory = {}
    if memory.get(cid) == stop.code:
        return False
    memory[cid] = stop.code
    log.info("[LIFECYCLE_STOP] %s | %s | %s", title or cid[:16],
             stop.code, text or "")
    if emit_fn is None:
        emit_fn = lambda *a, **k: None
    emit_fn(service="decide", cycle=cycle, phase="quoting",
            action="lifecycle_stop", market_slug=title,
            reason=f"{stop.code}: {text or ''}",
            extra={"condition_id": cid})
    try:
        from core_brain.order_registry import MarketEventRecord
        log_fn = getattr(seam.registry, "log_market_event", None)
        if callable(log_fn):
            log_fn(MarketEventRecord(
                ts=time.time(),
                condition_id=cid,
                market_slug=title or None,
                kind="lifecycle_stop",
                reason=text or "",
                reason_code=stop.code,
            ))
    except Exception as e:
        log.warning("lifecycle_stop store failed: %s: %s",
                    type(e).__name__, e)
    return True


def _note_refusal_visit(
    seam: VenueSeam,
    cid: str,
    title: str,
    code: str,
    text: str,
    streak: int = 1,
    emit_fn: Optional[Callable] = None,
) -> None:
    """Store one `market_events` row per refused visit (#622 T3)."""
    if emit_fn is None:
        emit_fn = lambda *a, **k: None
    try:
        from core_brain.order_registry import MarketEventRecord
        log_fn = getattr(seam.registry, "log_market_event", None)
        if callable(log_fn):
            log_fn(MarketEventRecord(
                ts=time.time(),
                condition_id=cid,
                market_slug=title or None,
                kind="refusal_visit",
                reason=text or "",
                reason_code=code,
                size=float(streak),
            ))
    except Exception as e:
        log.warning("refusal_visit store failed: %s: %s",
                    type(e).__name__, e)


def plan_orders(
    open_orders: list[dict],
    intents: list[QuoteIntent],
    price_eps: float = 1e-9,
    *,
    dead_band: float = 0.0,
    cfg=None,
    hedge_asks: Optional[dict] = None,
    hedge_held: Optional[set] = None,
    reasons: Optional[dict] = None,
    queue_ahead: Optional[dict] = None,
    hold_queue_shares: float = 0.0,
    hold_below_target: float = 0.0,
    visit_outcome: Optional[VisitOutcome] = None,
    preserve_order_ids: Optional[set[str] | frozenset[str]] = None,
    replace_order_ids: Optional[set[str] | frozenset[str]] = None,
    cancel_order_ids: Optional[set[str] | frozenset[str]] = None,
    lifecycle_pair_id: str | None = None,
) -> tuple[list[dict], list[QuoteIntent]]:
    """Split open orders + desired intents into (cancel, submit).

    HOLD-AND-WAIT (#384, #387). Every resting pair passed the pair-cost gate
    before placement, so drift alone is never a reason to re-check a resting
    order -- and neither is the live book: a resting price plus a moving hedge
    ask is not the pair's economics (rest price + rest price is), and
    re-testing it cancels profitable pairs on book flicker. A resting order
    whose token still has an intent this cycle is KEPT at its own price,
    period. The intent is suppressed via held_tokens (no duplicate posted).
    The only thing that still cancels is no intent for the token
    (not_quoted). The dead band, queue hold, direction hold, and pair-cost
    re-gate stop firing -- left in code untouched for the follow-up
    cancel-conditions issue to disposition.

    Orders on tokens we no longer quote are cancelled. An intent with no kept
    order near its price is submitted.

    TWO INDEPENDENT REASONS TO KEEP AN ORDER, and the tolerance is the larger
    of them so neither can silently disable the other:

      * `price_eps`, sub-tick venue rounding jitter. A rounding difference
        below a tick is not a price change and must not churn cancel+resubmit.
      * `dead_band`, the re-quote hysteresis. Every cancel+resubmit sends the
        order to the back of the queue at a new level, and on shadow run
        run-2809a7161de1 that happened 205 times out of 205 consecutive
        re-quotes -- median move 3.0c, median order lifetime 11.7s against a
        median queue_ahead of 1058.7 shares. Not fidgeting: the mid genuinely
        walked 0.815 -> 0.285 in 30 minutes and every re-quote answered a real
        book move. Answering it still cost the whole queue position, so the
        band trades a slightly stale price for time in the queue.

    `reasons` is an optional out-parameter: pass a dict and it comes back keyed
    by order id with one of the CANCEL_* constants. It exists because a cancel
    with no recorded reason cannot be tuned -- see #131.

    RETIRED BY #387 -- THE RE-GATE, THE QUEUE HOLD, THE DIRECTION HOLD
    (historical only; none of them fire). A kept order used to be re-tested
    against `risk.completable_pair_block` at its own price, kept when it sat
    inside the front-of-queue threshold (`hold_queue_shares`), and kept through
    a downward move (`hold_below_target`). All three cancelled profitable pairs
    on book flicker, so a wanted token is now kept at its own price. The
    parameters stay in the signature -- `cfg`, `hedge_asks`, `hedge_held`,
    `queue_ahead`, `hold_queue_shares`, `hold_below_target`, `visit_outcome` --
    for the follow-up cancel-conditions issue to disposition; only `dead_band`
    and `cfg.bot_quote_size_min_shares` are read here.
    """
    tolerance = max(float(price_eps), float(dead_band))
    preserve_ids = {str(value) for value in preserve_order_ids or ()}
    replace_ids = {str(value) for value in replace_order_ids or ()}
    cancel_ids = {str(value) for value in cancel_order_ids or ()}
    wanted: dict[str, list[QuoteIntent]] = {}
    for i in intents:
        wanted.setdefault(i.token_id, []).append(i)

    def _record(order: dict, reason: str) -> None:
        if reasons is not None:
            key = order.get("id") or order.get("order_id")
            if key is not None:
                reasons[str(key)] = reason

    kept: dict[str, list[dict]] = {}
    # Tokens whose order was HELD through a price move. A held order rests at a
    # price outside the tolerance by definition, so the submit loop below would
    # not recognise it as covering this cycle's intent and would post a second
    # order beside it -- double the resting size on one token, which is the
    # opposite of what holding a queue position is for.
    held_tokens: set[str] = set()
    to_cancel: list[dict] = []
    for o in open_orders:
        tok = o["token_id"]
        key = str(o.get("id") or o.get("order_id") or "")
        if key in replace_ids or key in cancel_ids:
            _record(
                o,
                "lifecycle_replace" if key in replace_ids else "lifecycle_cancel",
            )
            to_cancel.append(o)
            continue
        if key in preserve_ids:
            kept.setdefault(tok, []).append(o)
            held_tokens.add(tok)
            continue
        targets = wanted.get(tok)
        if not targets:
            _record(o, CANCEL_NOT_QUOTED)
            to_cancel.append(o)
            continue

        # NO RE-CHECK (#387): a placed order was judged once, at placement
        # (rest price + rest price). Re-testing it against the live hedge ask
        # cancels profitable pairs on book flicker, so nothing here re-checks
        # cost. Only a token with no intent this cycle is cancelled.
        kept.setdefault(tok, []).append(o)
        held_tokens.add(tok)

    to_submit: list[QuoteIntent] = []
    for i in intents:
        if lifecycle_pair_id is not None and i.pair_id == lifecycle_pair_id:
            if i.token_id not in held_tokens:
                to_submit.append(i)
            continue
        if i.token_id in held_tokens:
            # We chose to keep the resting order on this token. Posting the new
            # price as well would leave both working.
            continue
        sits = kept.get(i.token_id, [])
        if not any(abs(o["price"] - i.price) <= tolerance for o in sits):
            to_submit.append(i)

    # Delta top-up (#453): if an existing hedge is preserved (kept) on a lifecycle pair
    # but leaves a shortfall >= bot_quote_size_min_shares, generate a supplemental maker intent
    # for the missing delta shares on the same token and pair ID.
    if lifecycle_pair_id is not None and hasattr(cfg, "bot_quote_size_min_shares"):
        min_shares = float(getattr(cfg, "bot_quote_size_min_shares", 5.0) or 5.0)
        # Check if any kept order belongs to lifecycle_pair_id
        for tok, sits in kept.items():
            pair_sits = [o for o in sits if str(o.get("pair_id") or "") == str(lifecycle_pair_id)]
            if not pair_sits:
                continue
            # Look for an intent that matches this lifecycle pair to determine desired size and price
            matching_intents = [i for i in intents if i.pair_id == lifecycle_pair_id and i.token_id == tok]
            if matching_intents:
                target_intent = matching_intents[0]
                total_resting_shares = sum(float(o.get("size") or o.get("original_size") or 0.0) for o in pair_sits)
                shortfall = float(target_intent.size) - total_resting_shares
                if shortfall >= min_shares:
                    to_submit.append(QuoteIntent(
                        side=target_intent.side,
                        token_id=tok,
                        price=target_intent.price,
                        size=int(shortfall),
                        mid=target_intent.mid,
                        edge_vs_mid=target_intent.edge_vs_mid,
                        pair_id=lifecycle_pair_id,
                    ))

    return to_cancel, to_submit


def _cid(spec: Any) -> str:
    """The condition id from a market spec (dict or object)."""
    if isinstance(spec, dict):
        return str(spec.get("cid", ""))
    return str(getattr(spec, "cid", None) or getattr(spec, "condition_id", ""))


def _recording_inventory(read, recorder, cid):
    """Wrap the inventory port so each successful read is recorded (#505).

    Recorded only on success, keyed exactly as
    `rotation_priority.order_by_exposure` looks the market up (`_cid(spec)`,
    lowercased). A raising read records nothing and propagates unchanged: the
    last recorded value stands and the visit keeps its existing error
    handling. `recorder=None` returns the read unwrapped.
    """
    if recorder is None:
        return read

    def wrapped(market):
        inv = read(market)
        # An empty id ranks flat without ever looking the snapshot up, so
        # there is nothing to record for it -- and no "" key to grow.
        if cid:
            recorder(str(cid).lower(), inv)
        return inv

    return wrapped


def _market_cfg(base, spec: Any):
    """Per-market config carried through one rotation of the Trader.

    `decide_quotes` runs the same from-mid pricing path and the same live risk
    gates the paper fleet runs; the only per-market differences are the venue's
    min-size, spread window and tick, copied from the ranker's spec.

    `bot_pos_objective="spread_capture"` below is pinned on every market ON PURPOSE. It
    selects `quotes._decide_quotes_from_mid` -- rest both legs at `mid - offset`,
    which assembles the pair at ~1.00 - 2*offset and is how spread capture is
    executed. The alternative value, "pair", prices off the ask and was measured
    dead (pair cost 1.00 + spread by construction); pinning here keeps a spec
    that omits the field, or carries a stale one, from routing a live market
    onto it. The ranker's own `source` field ("spread" vs "rewards") describes how
    a market is FUNDED, not how it is quoted, and is deliberately not read here.
    """
    if not isinstance(spec, dict):
        if hasattr(spec, "range_cents") or hasattr(spec, "velocity_measured_at"):
            return replace(
                base,
                bot_quote_telemetry_range_cents=getattr(spec, "range_cents", None),
                bot_quote_telemetry_velocity_measured_at=getattr(spec, "velocity_measured_at", None),
            )
        return base
    return replace(
        base,
        bot_pos_objective="spread_capture",
        bot_quote_size_min_shares=int(spec.get("min_size", base.bot_quote_size_min_shares)),
        bot_quote_size_shares=int(spec.get("shares", base.bot_quote_size_shares)),
        # The quote window is the ENGINE's bot_quote_book_max_spread_from_mid, single source.
        # The venue's rewardsMaxSpread used to ride in on the feed row's
        # max_spread field and cap the window here (2.5c on Fed rows, 4.5c on
        # sports) — a rewards-program leftover throttling a strategy that earns
        # the pair discount, not emissions. Deleted with the rewards purge
        # (#594); do not reintroduce a per-market window.
        bot_quote_grid_tick_size_usd=float(spec.get("tick", base.bot_quote_grid_tick_size_usd)),
        bot_market_title=str(spec.get("title", "")),
        bot_quote_telemetry_range_cents=(
            float(spec["range_cents"])
            if spec.get("range_cents") is not None
            else getattr(base, "bot_quote_telemetry_range_cents", None)
        ),
        bot_quote_telemetry_velocity_measured_at=(
            float(spec["velocity_measured_at"])
            if spec.get("velocity_measured_at") is not None
            else getattr(base, "bot_quote_telemetry_velocity_measured_at", None)
        ),
    )


@dataclass
class VenueSeam:
    """Every venue-touching port the fleet loop needs, in one object.

    The interface of the loop: `run` reads every port off the seam, so
    adding a port does not rewire every call site. Production builds one
    in `main` (real venue calls); tests build one with fakes. A seam
    missing a required port raises in `run`, never silently.
    """
    client: Any = None
    registry: Any = None
    base_cfg: Any = None
    maker_address: Optional[str] = None
    clob_host: str = "https://clob.polymarket.com"
    fetch_market: Optional[Callable] = None
    fetch_books: Optional[Callable] = None
    decide: Optional[Callable] = None
    submit_fn: Optional[Callable] = None
    cancel_fn: Optional[Callable] = None
    reconcile_fn: Optional[Callable] = None
    sweep_fn: Optional[Callable] = None
    inventory_fn: Optional[Callable] = None
    open_orders_fn: Optional[Callable] = None
    fleet_state_fn: Optional[Callable] = None
    resting_order_ids_fn: Optional[Callable] = None
    emit_fn: Optional[Callable] = None
    #: Whether an empty `markets_fn` refresh is this caller's normal state or a
    #: signal worth a warning. Only the caller can answer it: the live stack's
    #: filter finding nothing is a finding, while a caller asking for a
    #: 30-second open window inside a 5-minute series is empty ~90% of the time
    #: by construction. Default stays False -- the loud reading -- because
    #: guessing "routine" for a caller that means it would hide a dead feed.
    markets_fn_empty_is_routine: bool = False
    #: Reachable-flow reader for the queue-clear gate (#393):
    #: `(condition_id, window_sec) -> SellFlow`. Optional and lazy -- it is only
    #: called when a new passive placement has shares ahead of it at its own
    #: price, so a clear front never pays for a tape read, and every caller that
    #: leaves it unset (including the shadow seam) behaves exactly as before.
    flow_fn: Optional[Callable[[str, float], "SellFlow"]] = None
    #: UMA resolution reader for the re-check gate (#408):
    #: `(condition_id) -> UmaResolutionStatus` (clean / flagged / unreachable).
    #: Optional and lazy -- called once per visit before `fetch_market`, and
    #: every caller that leaves it unset behaves exactly as before.
    fetch_uma_status: Optional[Callable[[str], Any]] = None
    #: Telemetry sink for the UMA gate (#408): `record_market_event(record)`
    #: writes one `market_events` row per flagged visit. Absent = skip the
    #: write; the cancel and discard still happen.
    record_market_event: Optional[Callable[[Any], None]] = None
    #: WS-book subscription for #650: called once per rotation with the current
    #: universe (`[market, ...]`). Optional and lazy -- `main()` wires a port
    #: that resolves each new market's tokens and hands them to `MarketStream`.
    #: Every caller that leaves it unset behaves exactly as before.
    subscribe_fn: Optional[Callable[[list], None]] = None
    #: WS-book read stats for #650: `() -> {"book_source", "book_read_ms", ...}`.
    #: Optional -- `_visit_one` merges them into the decide row only when the
    #: port is wired, so unwired seams emit byte-identical rows to before.
    book_stats_fn: Optional[Callable[[], dict]] = None
    #: The queued fill events #650 reacts to after each wait: `() -> [event]`.
    #: Optional -- `main()` wires it to the user-stream queue. Unwired, the
    #: loop runs rotation-to-rotation exactly as before.
    fill_events_fn: Optional[Callable[[], list]] = None
    #: The reconcile the loop runs for one fill event: `() -> Any`. Optional --
    #: a closure over `(client, registry, maker_address)`, so the reaction keeps
    #: the 3-argument reconcile port untouched. See `fill_events_fn`.
    fill_reconcile_fn: Optional[Callable[[], Any]] = None
    #: Unified pricing seam for #659: the `BookProvider` behind `fetch_books`.
    #: Optional -- `main()` sets it when the stream is up so the risk guardian
    #: thread evaluates defense against the same books the quotes use. Unset,
    #: the guardian decides against the client, exactly as before.
    book_provider: Optional[Any] = None


def run(
    seam: VenueSeam,
    *,
    interval: float = 1.0,
    once: bool = False,
    live: bool = True,
    markets: Optional[list] = None,
    markets_fn: Optional[Callable[[], list]] = None,
    sleep_fn: Optional[Callable] = None,
    suspects_box: Optional[dict] = None,
    shutdown_box: Optional[dict] = None,
    spawn_guardian: bool = False,
    clock_fn: Optional[Callable[[], float]] = None,
    guardian_wake: Optional[threading.Event] = None,
    trigger_board: Optional[Any] = None,
) -> list[LiveFleetResult]:
    """Rotate over `markets`: reconcile, then decide+submit per market, then sweep.

    All venue-touching behaviour comes off `seam`; only loop control stays on
    the signature. The three venue-touching steps that could kill the loop --
    reconcile, sweep, and each market visit -- are each isolated so one failure
    degrades the cycle rather than stopping it. In dry-run (`live=False`)
    nothing is submitted or cancelled; reconcile and sweep are read-only and
    still run.

    With `once=True` the results of that one rotation are returned. In the
    long-running loop only the most recent rotation is kept, so a hands-off run
    does not accumulate every market visit in memory.
    """
    if seam.base_cfg is None:
        from core_brain.config import load
        seam.base_cfg = load()
    if sleep_fn is None:
        sleep_fn = time.sleep
    # Rotation timing (#650): the loop measures what it did and sleeps only
    # the remainder of the interval, so streamed books that cut the work
    # actually shorten the wait. Tests inject `clock_fn`; production runs the
    # wall clock.
    _now = clock_fn or time.monotonic
    if seam.clob_host is None:
        seam.clob_host = os.environ.get("CLOB_HOST", "https://clob.polymarket.com")

    missing = [n for n, v in (
        ("fetch_market", seam.fetch_market), ("fetch_books", seam.fetch_books),
        ("decide", seam.decide), ("submit_fn", seam.submit_fn),
        ("cancel_fn", seam.cancel_fn), ("reconcile_fn", seam.reconcile_fn),
        ("sweep_fn", seam.sweep_fn),
    ) if v is None]
    if missing:
        raise TypeError(f"VenueSeam missing required ports: {', '.join(missing)}")

    # Telemetry is opt-in: main() wires core_brain.cycle_stream.emit; tests drive
    # the loop without it so no test ever writes into live/run/.
    emit_fn = seam.emit_fn or (lambda *a, **k: None)

    once_results: list[LiveFleetResult] = []
    last_cycle: list[LiveFleetResult] = []
    current_markets = list(markets or [])
    cycle = 0
    # Latest `Inventory` each visit read, keyed by lowercased condition id
    # (#505). Recorded from reads the visits already perform, so ordering the
    # next rotation's visits costs no extra database or venue call.
    inventory_by_cid: dict[str, Inventory] = {}
    # Last lifecycle-stop code per market for on-change-only rows (#402). A
    # quote clears the entry, so the next stop is news again.
    stop_memory: dict[str, str] = {}
    # Last steady-state tail signature per (market, line-kind) for the quiet
    # terminal (`_tail_note`): repeats stay at DEBUG so idle rotations do not
    # scroll the screen. A restart resets it (safe direction: a fresh loop
    # announces state once, then goes quiet).
    tail_memory: dict[tuple, str] = {}
    # Trade ids the fill reaction has already handled (#650): a bounded set
    # so one fill never reconciles twice however often the queue repeats it.
    _seen_fill_trades: Any = deque(maxlen=512)
    shutdown_reason = "once" if once else "stopped"
    registry = seam.registry

    def _finish_shutdown() -> None:
        if suspects_box is not None and "cids" not in suspects_box:
            suspects_box["cids"] = frozenset()
        if shutdown_box is not None:
            shutdown_box["reason"] = shutdown_reason
        log.info("fleet loop shutdown: %s", shutdown_reason)
    # Whole-loop ownership: one fleet loop per database. A second fleet gets
    # InstanceInUse naming the holder; the poll loop's own "poll" slot is
    # untouched so the designed pair keeps working. Fakes/MagicMock/None skip
    # the gate -- only a real OrderRegistry writes a real database.
    lock_cm = (
        registry.instance_lock("fleet", int(time.time() * 1000))
        if isinstance(registry, OrderRegistry)
        else nullcontext(None)
    )
    stop_workers = threading.Event()
    guardian_thread = None
    with lock_cm as holder:
        if spawn_guardian and registry is not None:
            from core_brain.order_manager import merge as live_merge
            start_settlement_sweeper(
                registry=registry,
                merge_fn=live_merge,
                interval=60.0,
                live=live,
                db_path=getattr(registry, "db_path", None),
                stop_event=stop_workers,
            )
            log.info("settlement sweeper started (every 60s, background)")

            guardian_thread = start_risk_guardian(
                client=seam.client,
                registry=registry,
                cfg=seam.base_cfg,
                interval=1.0,
                live=live,
                stop_event=stop_workers,
                wake_event=guardian_wake,
                trigger_board=trigger_board,
                single_buy_saver_fn=_make_guardian_saver_fn(
                    getattr(seam, "book_provider", None),
                    # The wallet the exit sizing nets against (#646 N5). The
                    # seam already carries it -- it is what reconcile is
                    # handed -- and without it the guardian manages blind.
                    funder=seam.maker_address,
                ),
                funder=seam.maker_address,
            )
            log.info("risk guardian sentry started (every 1.0s, background)")

        try:
            while True:
                cycle += 1
                # Rotation timing (#650): stamped before any work so the wait at
                # the end is `interval - work`, never `interval` on top of work.
                rot_t0 = _now()
                # Fleet-wide aggregates (naked cost, committed capital, pooled posture)
                # are recomputed once per cycle and merged into the base config, so the
                # fleet-level gates inside decide_quotes see live numbers rather than
                # their 0.0 defaults. A failure here degrades to the defaults.
                if seam.fleet_state_fn is not None:
                    try:
                        seam.base_cfg = replace(seam.base_cfg, **seam.fleet_state_fn(seam.registry))
                    except Exception as e:
                        log.warning("fleet state failed: %s: %s", type(e).__name__, e)

                try:
                    seam.reconcile_fn(seam.client, seam.registry, seam.maker_address)
                except KeyboardInterrupt:
                    shutdown_reason = "interrupt"
                    _finish_shutdown()
                    raise
                except Exception as e:
                    log.warning("reconcile failed: %s: %s", type(e).__name__, e)

                # An empty refresh is never obeyed. `load_graduated_markets` raises on a
                # missing, empty, malformed or stale feed, but a well-formed `[]` -- the
                # ranker finding nothing that cycle -- returns cleanly. Adopting it would
                # empty the active universe and hand every resting order to the dropped-
                # market cleanup below, cancelling the whole book on a transient scan.
                if markets_fn is not None:
                    try:
                        fresh = markets_fn()
                    except Exception as e:
                        log.warning("markets_fn failed: %s: %s", type(e).__name__, e)
                    else:
                        if fresh:
                            _evaluate_requalification(registry, fresh)
                            current_markets = list(fresh)
                        elif seam.markets_fn_empty_is_routine:
                            # Normal for this caller (see the seam field). Still
                            # logged, still counted -- just not dressed as a fault,
                            # which is what filled a four-hour trial's stderr with
                            # one alarming line per rotation and read as a dead feed.
                            log.info(
                                "markets_fn returned no markets (expected for this "
                                "feed); keeping the previous %d",
                                len(current_markets))
                        else:
                            log.warning(
                                "markets_fn returned no markets; keeping the previous %d",
                                len(current_markets))

                # Universe subscription (#650): the WS book cache follows the
                # markets the loop is actually visiting. A failed subscribe degrades
                # the rest of the rotation (books may be stale or cold) but never
                # stops it -- same isolation as reconcile and sweep.
                if current_markets and seam.subscribe_fn is not None:
                    try:
                        seam.subscribe_fn(list(current_markets))
                    except Exception as e:
                        log.warning("subscribe failed: %s: %s", type(e).__name__, e)

                cycle_results: list[LiveFleetResult] = []
                rotation_suspects: set[str] = set()
                # Resolved markets are never visited again (#402): one durable
                # read per rotation, before any market-data or book fetch. The
                # guard reads the in-memory universe, so a stale feed retaining
                # the market, a reappearing feed, and a restart all stay quiet.
                resolved = resolved_condition_ids(registry)
                # Reset marks, read once per rotation (#646). Unreadable
                # marks fail closed: fresh entries block for the cycle while
                # managed markets still visit.
                try:
                    requal_marks = (registry.requalification_marks()
                                    if registry is not None else {})
                except Exception as e:
                    # Same duck-typed-read convention as
                    # `resolved_condition_ids`: fakes and partial doubles
                    # fail closed with a warning, never with a traceback.
                    log.warning("requalification marks unreadable: %s; "
                                "blocking fresh entries", e)
                    requal_marks = None
                # Held markets first (#505): naked, then balanced, then flat
                # quoting-only -- stable within each rank, every market exactly
                # once. `current_markets` itself is untouched: dropped-market
                # cancellation and empty-refresh retention must keep seeing the
                # original universe. Ordering changes only visit order; each visit
                # still reads fresh inventory.
                visit_list = order_by_exposure(
                    list(current_markets or []), inventory_by_cid, _cid)
                for spec in visit_list:
                    cid = _cid(spec)
                    if cid and cid.lower() in resolved:
                        title = (spec.get("title", "")
                                 if isinstance(spec, dict) else "")
                        _note_lifecycle_stop(
                            seam, cid, title, LifecycleStop.RESOLVED,
                            "market resolved; books are never polled again",
                            stop_memory, cycle, emit_fn)
                        cycle_results.append(LiveFleetResult(
                            status="SKIPPED", condition_id=cid,
                            why="resolved_market_skipped"))
                        continue
                    if cid and _is_awaiting_requalification(
                            registry, requal_marks, cid):
                        title = (spec.get("title", "")
                                 if isinstance(spec, dict) else "")
                        cycle_results.append(LiveFleetResult(
                            status="SKIPPED", condition_id=cid,
                            title=title, why="awaiting_requalification"))
                        continue
                    res = _visit_one(
                        seam=seam, spec=spec, live=live, cycle=cycle,
                        emit_fn=emit_fn,
                        stop_memory=stop_memory,
                        tail_memory=tail_memory,
                        inventory_recorder=inventory_by_cid.__setitem__,
                    )
                    if res.status == "ERROR" and "book fetch error" in (res.error or ""):
                        rotation_suspects.add(cid)
                    elif classify_refusal(res.why or "") in (
                            LifecycleStop.SETTLED_BOOK,
                            LifecycleStop.COUNTDOWN_EXPIRED):
                        rotation_suspects.add(cid)
                    cycle_results.append(res)
                if suspects_box is not None:
                    suspects_box["cids"] = frozenset(rotation_suspects)

                # An empty universe is not evidence that every market was dropped: it is
                # the state before the first successful refresh, or after one that
                # graduated nothing. "Dropped" is only meaningful against a real set.
                if (live and current_markets and seam.registry is not None
                        and seam.cancel_fn is not None):
                    cycle_results.extend(_cancel_dropped_markets(
                        seam=seam, current_markets=current_markets, cycle=cycle,
                        emit_fn=emit_fn,
                        resolved_cids=resolved, stop_memory=stop_memory,
                    ))

                last_cycle = cycle_results
                if once:
                    once_results.extend(cycle_results)

                try:
                    seam.sweep_fn()
                except KeyboardInterrupt:
                    shutdown_reason = "interrupt"
                    _finish_shutdown()
                    raise
                except Exception as e:
                    log.warning("sweep failed: %s: %s", type(e).__name__, e)

                # Rotation accounting (#650): one additive row per rotation with the
                # measured work, so the dashboard (and the operator) can see when a
                # rotation is too fat for its interval. Emitted before the `once`
                # break so smoke runs carry it too.
                work_sec = _now() - rot_t0
                emit_fn(service="decide", cycle=cycle, phase="quoting",
                        action="rotation_complete", market_slug="",
                        reason="", latency_ms=work_sec * 1000.0,
                        extra={"markets": len(current_markets),
                               "suspects": len(rotation_suspects)})

                # Heartbeat: re-stamp our slot so a long run never looks stale. An
                # adopted slot means another loop owns the registry now -- stop writing
                # rather than record into a database we no longer own.
                if holder is not None and not registry.refresh_instance_lock(
                        "fleet", holder, int(time.time() * 1000)):
                    log.error("instance slot adopted by another loop; stopping "
                              "before further writes")
                    shutdown_reason = "lock_lost"
                    break

                if once:
                    shutdown_reason = "once"
                    break
                try:
                    # Sleep the remainder of the interval (#650): with streamed
                    # books the work is fast and the loop waits longer; when the
                    # venue is slow the loop skips the wait but never goes negative.
                    sleep_fn(max(0.0, interval - work_sec))
                except KeyboardInterrupt as e:
                    # The shadow deadline sleep subclasses KeyboardInterrupt to
                    # end a time-boxed rehearsal. Matched by class NAME so this
                    # module never imports the shadow runner.
                    shutdown_reason = ("finish_line"
                                       if type(e).__name__ == "_FinishLine"
                                       else "deadline"
                                       if type(e).__name__ == "_Deadline"
                                       else "interrupt")
                    break

                # Fill reaction (#650): drain what the user stream queued while the
                # loop waited, then react in-loop -- reconcile, visit the filled
                # market, tell the guardian -- instead of waiting for the next
                # rotation. Never in `once` mode (a smoke has no queue to drain).
                # The fill queue is only drained when both ports are wired; either
                # `None` leaves the loop rotation-to-rotation, exactly as before.
                # Placed AFTER the `except KeyboardInterrupt` so the shadow
                # deadline's end-of-rehearsal interrupt never drains: a finished
                # rehearsal reacts to nothing.
                drain = seam.fill_events_fn
                reconcile_port = seam.fill_reconcile_fn
                if drain is not None and reconcile_port is not None:
                    _react_to_fills(
                        seam=seam, current_markets=current_markets,
                        emit_fn=emit_fn, resolved=resolved,
                        stop_memory=stop_memory, tail_memory=tail_memory,
                        inventory_recorder=inventory_by_cid.__setitem__,
                        seen_trade_ids=_seen_fill_trades, live=live,
                        trigger_board=trigger_board)
        finally:
            stop_workers.set()
            if guardian_wake is not None:
                guardian_wake.set()
            if (guardian_thread is not None
                    and hasattr(guardian_thread, "is_alive")
                    and guardian_thread.is_alive()
                    and guardian_thread is not threading.current_thread()):
                guardian_thread.join(timeout=2.0)

    _finish_shutdown()
    return once_results if once else last_cycle


def _guardian_fill_tick(**kw) -> None:
    """One risk-guardian tick after a fill event (#650).

    Separated from `run` so the reaction is unit-testable without the loop.
    `kw` carries the seam's own `client` / `registry` / `base_cfg` from the
    call site; importing here keeps `run` free of the import at module scope,
    exactly as `start_risk_guardian` does.
    """
    from core_brain.risk_guardian import run_guardian_tick
    run_guardian_tick(**kw)


def _react_to_fills(
    *,
    seam: VenueSeam,
    current_markets: list,
    emit_fn: Callable,
    resolved: set,
    stop_memory: dict,
    tail_memory: dict,
    inventory_recorder: Callable,
    seen_trade_ids: Any,
    live: bool,
    trigger_board: Optional[Any] = None,
) -> None:
    """Drain the user-stream queue and react to each fill (#650).

    Event first, then reaction: every drained event emits `user_fill_received`,
    then (unless its `status` says FAILED) the loop re-reconciles, re-visits
    the filled market, and runs a guardian tick. Each step is isolated -- a
    failure degrades this event, never the loop. Dedup keys on
    `(trade_id, status)` so a status change (matched -> confirmed, or a later
    FAILED) still reacts while a repeated identical event does not.
    """
    try:
        events = list(seam.fill_events_fn() or [])
    except Exception as e:
        log.warning("fill drain failed: %s: %s", type(e).__name__, e)
        return
    for ev in events:
        trade_id = str(getattr(ev, "trade_id", "") or "")
        status_key = str(getattr(ev, "status", "") or "").upper()
        key = (trade_id, status_key)
        if not trade_id or key in seen_trade_ids:
            continue
        seen_trade_ids.append(key)
        ev_cid = str(getattr(ev, "condition_id", "") or "")
        matched = next(
            (s for s in (current_markets or []) if _cid(s) == ev_cid), None)
        emit_fn(service="decide", cycle=0, phase="reconciling",
                action="user_fill_received", market_slug="",
                reason="", latency_ms=0.0,
                extra={"trade_id": trade_id,
                       "order_id": str(getattr(ev, "order_id", "") or ""),
                       "condition_id": ev_cid,
                       "token_id": str(getattr(ev, "token_id", "") or ""),
                       "side": str(getattr(ev, "side", "") or ""),
                       "price": float(getattr(ev, "price", 0.0) or 0.0),
                       "size": float(getattr(ev, "size", 0.0) or 0.0),
                       "status": str(getattr(ev, "status", "") or ""),
                       "matched": matched is not None})
        status = str(getattr(ev, "status", "") or "").upper()
        if status == "FAILED":
            # Telemetry only: nothing on the venue changed, so there is
            # nothing to reconcile and nothing to re-quote.
            continue
        _react_to_one_fill(
            seam=seam, ev=ev, matched=matched,
            emit_fn=emit_fn, resolved=resolved, stop_memory=stop_memory,
            tail_memory=tail_memory, inventory_recorder=inventory_recorder,
            live=live, trigger_board=trigger_board)


def _react_to_one_fill(
    *,
    seam: VenueSeam,
    ev: Any,
    matched: Any,
    emit_fn: Callable,
    resolved: set,
    stop_memory: dict,
    tail_memory: dict,
    inventory_recorder: Callable,
    live: bool,
    trigger_board: Optional[Any] = None,
) -> None:
    """One non-FAILED fill: reconcile, visit, guardian tick (#650).

    Separated from `_react_to_fills` so the loop body stays a flat drain loop
    and each step's isolation reads as its own `try`. A failed reconcile
    stops this event (nothing downstream can be trusted); a failed visit
    still gets its guardian tick.
    """
    try:
        seam.fill_reconcile_fn()
    except Exception as e:
        log.warning("fill reconcile failed: %s: %s", type(e).__name__, e)
        # Return without reacting: nothing downstream of a failed reconcile
        # can be trusted yet, and the fill is still in the user-stream gap
        # the next rotation's reconcile will find.
        return
    ev_cid = str(getattr(ev, "condition_id", "") or "")
    if matched is not None and ev_cid and ev_cid.lower() not in resolved:
        try:
            _visit_one(
                seam=seam, spec=matched, live=live, cycle=0,
                emit_fn=emit_fn, stop_memory=stop_memory,
                tail_memory=tail_memory,
                inventory_recorder=inventory_recorder,
            )
        except Exception as e:
            log.warning("fill visit failed: %s: %s", type(e).__name__, e)
    try:
        # Same wallet the background sentry gets (#646 N5). Without it the
        # fill-triggered tick would have to fall back to POLY_FUNDER, and a
        # pass with no venue view skips a partial exit's remainder instead
        # of sizing it -- the one moment a remainder is most likely to exist.
        if trigger_board is not None:
            _guardian_fill_tick(
                client=seam.client, registry=seam.registry,
                cfg=seam.base_cfg, live=live, trigger_board=trigger_board,
                funder=seam.maker_address)
        else:
            _guardian_fill_tick(
                client=seam.client, registry=seam.registry,
                cfg=seam.base_cfg, live=live, funder=seam.maker_address)
    except Exception as e:
        log.warning("fill guardian tick failed: %s: %s",
                    type(e).__name__, e)


def _cancel_dropped_markets(
    seam: VenueSeam,
    current_markets: list,
    cycle: int = 0,
    emit_fn: Optional[Callable] = None,
    resolved_cids: Optional[set] = None,
    stop_memory: Optional[dict] = None,
) -> list[LiveFleetResult]:
    """Cancel resting quotes on markets that left the active universe.

    A market dropped by a refresh never reaches `_visit_one`, so `plan_orders`
    never sees its orders and they rest untouched -- and a resting buy whose
    paired leg fills becomes exactly the single buy nobody decided to take.

    Three deliberate limits:

    * Only `open` orders are cancelled. A `partial` has already bought shares;
      cancelling it strands them as a naked leg with no counter-order working,
      and this loop has no exit path -- `single_buy_saver` runs from
      `order_manager`, not here. Partials are reported as WARNED so the operator
      sees them instead of the loop quietly making the position worse.
    * `pending` is left alone. Its row may have no venue id yet, so there is
      nothing to cancel; reconcile's orphan adoption is what claims it.
    * Every failure surfaces as a result row, never as a log line only. This
      cleanup exists to reduce exposure, and a cleanup that can no-op invisibly
      is worse than none.

    One market's failure never stops the rest: each condition id is cancelled in
    its own try block.
    """
    if emit_fn is None:
        emit_fn = lambda *a, **k: None
    out: list[LiveFleetResult] = []

    try:
        active_orders = list(seam.registry.get_active_orders())
    except Exception as e:
        log.warning("dropped-market cleanup could not read the registry: %s: %s",
                    type(e).__name__, e)
        emit_fn(service="decide", cycle=cycle, phase="quoting",
                action="market_error", market_slug="",
                reason=f"dropped_cleanup_registry: {type(e).__name__}: {e}")
        return [LiveFleetResult(
            status="ERROR", why="dropped_market_cleanup_failed",
            error=f"dropped_cleanup_registry: {type(e).__name__}: {e}")]

    current_cids = {_cid(s) for s in (current_markets or [])}
    # A resolved market still in the universe works exactly like a dropped
    # one, except the recorded reason: its books are never polled again, so
    # resting quotes on it are exited here (#402).
    resolved_lc = {str(c).lower() for c in (resolved_cids or ()) if c}
    dropped = [o for o in active_orders
               if o.condition_id
               and (o.condition_id not in current_cids
                    or o.condition_id.lower() in resolved_lc)]

    # Verify if dropped orders are actually resting at the venue
    venue_resting: Optional[set[str]] = None
    if seam.resting_order_ids_fn:
        try:
            r_ids = seam.resting_order_ids_fn(seam.client)
            if r_ids is not None:
                venue_resting = {str(r) for r in r_ids}
        except Exception:
            pass

    stranded = sorted({o.condition_id for o in dropped
                       if getattr(o, "status", "") == "partial"
                       and (venue_resting is None or (o.order_id and str(o.order_id) in venue_resting))})
    for cid in stranded:
        n = sum(1 for o in dropped
                if o.condition_id == cid and getattr(o, "status", "") == "partial"
                and (venue_resting is None or (o.order_id and str(o.order_id) in venue_resting)))
        log.warning("dropped market %s has %d partially filled order(s) left "
                    "resting: cancel would strand the filled shares", cid, n)
        emit_fn(service="decide", cycle=cycle, phase="quoting",
                action="dropped_partial", market_slug="",
                reason="dropped_market_partial_retained",
                extra={"condition_id": cid, "partial_orders": n})
        out.append(LiveFleetResult(
            status="WARNED", condition_id=cid,
            why="dropped_market_partial_retained"))

    # THE HEDGE SHIELD (#416). A market that left the universe while holding
    # a lifecycle-protected leg (ESCALATED_HEDGE, read from the
    # registry, never inferred) keeps its resting hedge: cancelling the
    # protection strands the filled leg as the single buy nobody decided to
    # take. Resolved markets still exit first -- protection never overrides
    # resolution -- and an unreadable registry fails PROTECTED, never
    # cancelled: cancelling a possibly-escalated hedge is the irreversible
    # harm, while a resting order is re-evaluated next cycle. Every skip
    # surfaces as a WARNED row, never a log line only.
    # HARD_STOP pairs (#628) are not protected: their exit already cancels
    # both legs and sells the filled position, leaving nothing to shield.
    protected_cids: dict[str, str] = {}
    if seam.registry is not None and callable(
            getattr(seam.registry, "get_lifecycle_state", None)):
        shield_pairs: dict[str, str] = {}
        for o in dropped:
            pid = getattr(o, "pair_id", None)
            if pid and getattr(o, "status", "") == "open":
                shield_pairs.setdefault(str(pid), o.condition_id)
        for pid, pcid in shield_pairs.items():
            if pcid.lower() in resolved_lc:
                continue
            try:
                rec = seam.registry.get_lifecycle_state(pid)
            except Exception as e:
                log.warning("dropped-market shield could not read lifecycle "
                            "state for pair %s: %s: %s",
                            pid, type(e).__name__, e)
                emit_fn(service="decide", cycle=cycle, phase="quoting",
                        action="dropped_hedge_protected", market_slug="",
                        reason="dropped_market_hedge_protect_read_failed: "
                        f"{type(e).__name__}: {e}",
                        extra={"condition_id": pcid, "pair_id": pid})
                out.append(LiveFleetResult(
                    status="WARNED", condition_id=pcid,
                    why="dropped_market_hedge_protect_read_failed",
                    error=f"dropped_market_hedge_protect: {type(e).__name__}: {e}"))
                protected_cids[pcid] = "unknown"
                continue
            if rec is not None and rec.state in _HEDGE_PROTECTED_STATES:
                protected_cids[pcid] = rec.state

    open_cids = sorted({o.condition_id for o in dropped
                        if getattr(o, "status", "") == "open"})
    for dropped_cid in open_cids:
        if (dropped_cid in protected_cids
                and dropped_cid.lower() not in resolved_lc):
            state = protected_cids[dropped_cid]
            n = sum(1 for o in dropped
                    if o.condition_id == dropped_cid
                    and getattr(o, "status", "") == "open")
            log.warning("dropped market %s holds a %s hedge leg: "
                        "%d open order(s) left resting",
                        dropped_cid, state, n)
            emit_fn(service="decide", cycle=cycle, phase="quoting",
                    action="dropped_hedge_protected", market_slug="",
                    reason="dropped_market_hedge_protected",
                    extra={"condition_id": dropped_cid,
                           "lifecycle_state": state, "open_orders": n})
            out.append(LiveFleetResult(
                status="WARNED", condition_id=dropped_cid,
                why="dropped_market_hedge_protected"))
            continue
        is_resolved = dropped_cid.lower() in resolved_lc
        reason = (LifecycleStop.RESOLVED.code if is_resolved
                  else CANCEL_MARKET_DROPPED)
        dropped_orders = [
            {
                "token_id": o.token_id,
                "price": o.price,
                "order_id": o.order_id or o.id,
                "id": o.id,
                "side": o.side,
                "status": o.status,
                # Not churn and not a gate: the market left the universe (or
                # resolved while still listed), and a cancel with no recorded
                # reason reads as either.
                "cancel_reason": reason,
            }
            for o in dropped
            if o.condition_id == dropped_cid and getattr(o, "status", "") == "open"
        ]
        try:
            cancelled = seam.cancel_fn(seam.client, seam.registry, dropped_orders)
            _note_lifecycle_stop(
                seam, dropped_cid, "",
                LifecycleStop.RESOLVED if is_resolved
                else LifecycleStop.MARKET_DROPPED,
                ("market resolved; resting quotes exited"
                 if is_resolved else "market left the active universe"),
                stop_memory, cycle, emit_fn)
            out.append(LiveFleetResult(
                status="CANCELLED", condition_id=dropped_cid,
                why="dropped_market_cancelled", cancelled=cancelled,
            ))
        except Exception as ce:
            log.warning("cancel dropped market %s failed: %s: %s",
                        dropped_cid, type(ce).__name__, ce)
            emit_fn(service="decide", cycle=cycle, phase="quoting",
                    action="market_error", market_slug="",
                    reason=f"dropped_cancel: {type(ce).__name__}: {ce}",
                    extra={"condition_id": dropped_cid})
            out.append(LiveFleetResult(
                status="ERROR", condition_id=dropped_cid,
                why="dropped_market_cancel_failed",
                error=f"dropped_cancel: {type(ce).__name__}: {ce}",
            ))

    return out
def _still_resting(seam: VenueSeam, to_cancel: list[dict]) -> list[str]:
    """Which of `to_cancel` the venue still shows resting, conservatively.

    `cancel_fn` returns how many orders it actually cancelled, and a short count
    has two very different causes. Either the order is genuinely still resting
    (cancel rejected, the venue is degraded) -- submitting a replacement on top
    of it double-quotes the token and can breach MAX_TOTAL_USD -- or the order
    was already gone (filled or cancelled between planning and cancelling), in
    which case nothing rests and the replacement is safe.

    Treating both as "still resting" parks the market in ERROR for a cycle every
    time a quote fills at the wrong moment, which on a fast rotation is often.
    So ask the venue.

    Unverifiable means unsafe: with no `resting_order_ids_fn`, or a read that
    fails, every order is reported as still resting so the caller aborts. The
    row status is deliberately left alone -- an order that vanished may have
    FILLED, and reconcile is what attributes that, not this check.
    """
    ids = [str(o.get("order_id") or o.get("id") or "") for o in to_cancel]
    if seam.resting_order_ids_fn is None:
        return [i for i in ids if i]
    try:
        resting = seam.resting_order_ids_fn(seam.client)
    except Exception as e:
        log.warning("resting-order read failed: %s: %s", type(e).__name__, e)
        return [i for i in ids if i]
    if resting is None:
        return [i for i in ids if i]
    resting = {str(r) for r in resting}
    return [i for i in ids if i and i in resting]


# Lifecycle states whose hedge execution path outranks placement gating
# (#416, #628). Read from the registry lifecycle state via the quote context --
# never an inferred flag: every other state keeps full gate behavior.
# Narrowed to ESCALATED_HEDGE for dropped-market protection (#628), because
# a stopped-out pair has already cancelled both legs and exited.
_HEDGE_PROTECTED_STATES = frozenset({"ESCALATED_HEDGE"})


def _admit_placements(
    to_submit: list[QuoteIntent],
    market: Any,
    up_book: dict,
    down_book: dict,
    flow_fn: Optional[Callable[[str, float], Any]],
    cfg: Any,
    lifecycle_state: str | None = None,
) -> tuple[list[QuoteIntent], str]:
    """Which of this cycle's NEW placements may go out, and the queue reason.

    THE QUEUE-CLEAR GATE (#393). Every gate upstream judges a new bid's PRICE;
    this one asks whether it can ever FILL. Shares ahead are the book depth at
    the bid's own price; reachable flow is taker SELL volume on that token at or
    below it inside the configured window. Past the bar, the bid is not worth
    resting -- observed live 2026-10-06, 7-share orders behind $85k/$26k
    queue-ahead against $19,279/30m of tape.

    Three deliberate limits:

    * CROSSED LEGS ARE NOT GATED. A fill-or-kill hedge never joins a queue, so
      the depth in front of it says nothing about it.
    * THE MEASUREMENT IS LAZY. The tape is read only when some new passive
      placement actually has shares ahead of it. A read per market visit costs a
      venue round-trip, and `markets` records what one unreachable endpoint does
      to a rotation -- there is no reason to pay for a number that cannot change
      the answer.
    * AN UNMEASURABLE TAPE FAILS OPEN. `unavailable` (the read failed) or
      `truncated` (the page limit bounded it, so the numbers are a floor) keeps
      today's behaviour and names the skip. Only a complete window refuses, and
      it refuses as a measurement: nothing traded at our price in the window is
      an infinite wait, not missing data.

    A COUPLE IS NEVER SPLIT. When any new passive placement is refused, every
    one of them is dropped with it. A fresh couple carries no `pair_id` at this
    point -- `_submit_intents` mints the pair -- so nothing here could tell the
    two legs apart, and one resting leg with no partner is the Unpaired alert
    state rather than the pair this strategy rests.

    Returns `(admitted, why)`. `why` is empty when nothing was refused or
    reported; under the shipped record-only default (`bot_order_gate_queue_clear_on`
    False) it carries the reason and `admitted` is unchanged.
    """
    from core_brain import risk

    if not to_submit or flow_fn is None:
        return list(to_submit), ""
    passive = [i for i in to_submit if not i.crossed]
    if not passive:
        return list(to_submit), ""
    if lifecycle_state in _HEDGE_PROTECTED_STATES or lifecycle_state == "HARD_STOP":
        # THE HEDGE BYPASS (#416). A market holding an unhedged leg in an
        # escalated or hard-stop lifecycle state keeps its execution path:
        # refusing its hedge placement would strand the filled leg as the
        # single buy nobody decided to take. Dual-resting markets never
        # arrive here with a protected state, so their gating is unchanged.
        # The tape is not read -- a measurement that cannot change the
        # answer is a wasted venue round-trip -- and the bypass travels on
        # the record, never silently.
        return list(to_submit), (
            f"queue gate bypassed: lifecycle {lifecycle_state} hedge "
            f"protection; {len(passive)} placement(s) admitted")

    # The token -> book mapping, not the side: `plan_orders` and the decider
    # both work in tokens, and scoring the wrong book is the one way this can be
    # wrong while everything else still passes.
    books = {
        str(getattr(market, "up_token", "")): up_book,
        str(getattr(market, "down_token", "")): down_book,
    }
    ahead: list[tuple[QuoteIntent, float]] = []
    for i in passive:
        book = books.get(str(i.token_id)) or {}
        bids = book.get("bids") or {}
        ahead.append((i, float(bids.get(round(float(i.price), 4), 0.0))))
    if not any(q > 0 for _, q in ahead):
        return list(to_submit), ""

    cid = str(getattr(market, "condition_id", "") or "")
    window_sec = float(getattr(cfg, "bot_order_queue_flow_window_sec", 0.0) or 0.0)
    try:
        flow = flow_fn(cid, window_sec)
    except Exception as e:
        # Fail open, but never quietly: the reason travels with the placement,
        # and a broken port says so on the loop's own log as well.
        log.warning("queue gate tape read failed for %s: %s: %s",
                    cid[:16], type(e).__name__, e)
        return list(to_submit), (
            f"queue gate skipped: tape read failed ({type(e).__name__}: {e})")

    status = str(getattr(flow, "status", "unavailable"))
    if status != "complete":
        return list(to_submit), (
            f"queue gate skipped: reachable flow {status}; "
            f"nothing was refused")

    levels_by_token = getattr(flow, "by_token", None) or {}
    window_min = float(getattr(flow, "window_sec", 0.0) or 0.0) / 60.0
    reasons: list[str] = []
    refused: list[str] = []
    for i, queue_shares in ahead:
        levels = levels_by_token.get(str(i.token_id)) or {}
        limit = round(float(i.price), 4) + 1e-9
        reachable = sum(float(sh) for p, sh in levels.items()
                        if float(p) <= limit)
        allowed, why = risk.queue_clear_block(
            cfg, str(i.side), float(i.price), queue_shares, reachable,
            window_min)
        if not why:
            continue
        reasons.append(why)
        if not allowed:
            refused.append(why)

    if refused:
        why = refused[0]
        if len(refused) < len(passive):
            why = f"{why} ({len(passive)} new placement(s) dropped with it)"
        return [i for i in to_submit if i.crossed], why
    if reasons:
        # Record-only (`bot_order_gate_queue_clear_on` False): the bar is on the
        # record, nothing is dropped. This is what the threshold gets picked from.
        return list(to_submit), reasons[0]
    return list(to_submit), ""


def make_uma_status_reader(
    gamma_host: str = "https://gamma-api.polymarket.com",
    ttl_sec: Optional[float] = None,
    now_fn: Optional[Callable[[], float]] = None,
) -> Callable[[str], Any]:
    """One cached UMA reader per run for the live seam (#408)."""
    from core_brain.market_resolution import (
        UMA_RESOLUTION_STATUS_TTL_SEC,
        UmaResolutionStatusCache, fetch_uma_resolution_status,
    )
    cache = UmaResolutionStatusCache(
        ttl_sec=UMA_RESOLUTION_STATUS_TTL_SEC if ttl_sec is None else ttl_sec)
    clock = now_fn or time.time

    def fetch_uma_status(condition_id: str):
        return cache.status_for(
            condition_id,
            lambda cid: fetch_uma_resolution_status(gamma_host, cid),
            now_s=clock(),
        )

    return fetch_uma_status


def _uma_reason_for(status_value: str) -> str:
    """The cancel reason for a flagged UMA status (#408)."""
    return UMA_RESOLUTION_STATUS_REASONS.get(
        str(status_value or "").strip().lower(),
        CANCEL_UMA_RESOLUTION_PROPOSED,
    )


def _check_uma_resolution_before_fetch(
    seam: VenueSeam,
    cid: str,
    title: str = "",
    cycle: int = 0,
    emit_fn: Optional[Callable] = None,
) -> Optional[LiveFleetResult]:
    """Cancel resting quotes on a UMA-flagged market, before `fetch_market` (#408)."""
    if getattr(seam, "fetch_uma_status", None) is None:
        return None
    fetch_uma = seam.fetch_uma_status
    if not callable(fetch_uma):
        return None
    if not cid:
        return None
    if emit_fn is None:
        emit_fn = lambda *a, **k: None
    try:
        uma = fetch_uma(cid)
    except Exception as e:
        log.warning("quoting/uma_check_unreachable %s: %s: %s",
                    cid[:16], type(e).__name__, e)
        emit_fn(service="decide", cycle=cycle, phase="quoting",
                action="uma_check_unreachable", market_slug=title or cid[:16],
                reason=f"{type(e).__name__}: {e}",
                extra={"condition_id": cid})
        return None
    if uma is None or getattr(uma, "unreachable", False):
        log.warning("quoting/uma_check_unreachable %s: gamma read failed; "
                    "continuing visit", cid[:16])
        emit_fn(service="decide", cycle=cycle, phase="quoting",
                action="uma_check_unreachable", market_slug=title or cid[:16],
                reason="gamma read failed; continuing visit",
                extra={"condition_id": cid})
        return None
    status_value = str(getattr(uma, "status", None) or "").strip().lower()
    if not getattr(uma, "flagged", False) or not status_value:
        return None
    # Re-validate against the allow-list: the real reader only emits
    # recognized statuses, but a custom port returning flagged + bogus must
    # not cancel with a mislabeled reason. Unknown = unreadable = fail open.
    if status_value not in UMA_RESOLUTION_STATUS_REASONS:
        log.warning("quoting/uma_check_unreachable %s: unrecognized uma "
                    "status %r; continuing visit", cid[:16], status_value)
        emit_fn(service="decide", cycle=cycle, phase="quoting",
                action="uma_check_unreachable", market_slug=title or cid[:16],
                reason=f"unrecognized uma status {status_value!r}; continuing visit",
                extra={"condition_id": cid})
        return None
    reason = _uma_reason_for(status_value)
    # Reuse the existing resting-order lookup shape, not a third one: the
    # dropped-market cleanup reads `registry.get_active_orders()` and maps
    # rows to cancel dicts. Same read here (no invented lookup), except
    # open+partial (the plan's open_orders_fn shape) instead of open-only.
    resting: list[dict] = []
    try:
        active = list(seam.registry.get_active_orders())
    except Exception as e:
        log.warning("uma gate registry read failed for %s: %s: %s",
                    cid[:16], type(e).__name__, e)
        active = []
    for o in active:
        o_cid = getattr(o, "condition_id", None)
        if o_cid is None and isinstance(o, dict):
            o_cid = o.get("condition_id")
        if str(o_cid or "") != cid:
            continue
        st = getattr(o, "status", None)
        if st is None and isinstance(o, dict):
            st = o.get("status", "open")
        if str(st) not in ("open", "partial"):
            continue
        if isinstance(o, dict):
            row = dict(o)
            row["cancel_reason"] = reason
            resting.append(row)
        else:
            resting.append({
                "id": getattr(o, "id", None),
                "order_id": getattr(o, "order_id", None) or getattr(o, "id", None),
                "token_id": getattr(o, "token_id", None),
                "price": getattr(o, "price", None),
                "side": getattr(o, "side", None),
                "status": str(st),
                "cancel_reason": reason,
            })
    cancelled = 0
    failed = 0
    if resting:
        try:
            cancelled = int(seam.cancel_fn(seam.client, seam.registry, resting) or 0)
        except Exception as e:
            log.warning("uma gate cancel failed for %s: %s: %s",
                        cid[:16], type(e).__name__, e)
            cancelled = 0
        failed = max(0, len(resting) - cancelled)
    log.info("[UMA_GATE] %s | status=%s reason=%s cancelled=%d failed=%d",
             title or cid[:16], status_value, reason, cancelled, failed)
    emit_fn(service="decide", cycle=cycle, phase="quoting",
            action="discard", market_slug=title or cid[:16], reason=reason,
            extra={"condition_id": cid, "uma_status": status_value,
                   "cancelled": cancelled, "failed": failed})
    record_fn = getattr(seam, "record_market_event", None)
    if record_fn is not None and callable(record_fn):
        try:
            from core_brain.order_registry import MarketEventRecord
            record_fn(MarketEventRecord(
                ts=time.time(), condition_id=cid,
                market_slug=title or None, kind="BLOCKED",
                reason=(f"uma {status_value}: {reason} "
                        f"(cancelled={cancelled} failed={failed})"),
                reason_code=reason,
            ))
        except Exception as e:
            log.warning("uma gate market_events write failed for %s: %s: %s",
                        cid[:16], type(e).__name__, e)
    return LiveFleetResult(
        status="CANCELLED", condition_id=cid, title=title, why=reason,
        cancelled=cancelled,
        error=(f"{failed} cancel(s) failed; retry next visit" if failed else ""),
    )


def _lifecycle_quote_context(
    seam: VenueSeam,
    market: Any,
    cfg: Any,
    up_book: dict,
    down_book: dict,
    inventory: Inventory,
    open_orders: list[dict],
    *,
    cycle: int,
    title: str,
    emit_fn: Callable,
) -> LifecycleQuoteContext:
    """Build pair-scoped lifecycle decisions from the already-fetched books."""
    pair_orders: dict[str, list[dict]] = {}
    for order in open_orders:
        pair_id = order.get("pair_id")
        if pair_id:
            pair_orders.setdefault(str(pair_id), []).append(order)
    if not pair_orders:
        return LifecycleQuoteContext()

    registry = seam.registry
    required = ("get_orders_by_pair", "get_size_matched",
                "get_matched_notional", "get_lifecycle_state",
                "save_lifecycle_state")
    if registry is None or any(not callable(getattr(registry, name, None))
                               for name in required):
        raise RuntimeError(
            "active paired orders require lifecycle-capable registry methods")

    up_token = str(market.up_token)
    down_token = str(market.down_token)
    base_offset, _ = dynamic_offset_for(cfg)
    decisions: list[tuple[str, SingleLegPosition, LifecycleDecision]] = []
    preserve_order_ids: set[str] = set()
    replace_order_ids: set[str] = set()
    cancel_order_ids: set[str] = set()
    escalated_order_already_resting = False

    def refusal_context(reason: str, pair_ids: list[str],
                        state: str | None = None) -> LifecycleQuoteContext:
        emit_fn(
            service="decide", cycle=cycle, phase="quoting",
            action="single_leg_refusal", market_slug=title, reason=reason,
            extra={"condition_id": str(market.condition_id),
                   "pair_ids": pair_ids},
        )
        return LifecycleQuoteContext(refusal_reason=reason,
                                     lifecycle_state=state)

    def preserve_refusal_context(
        reason: str, pair_ids: list[str],
        state: str | None = None,
    ) -> LifecycleQuoteContext:
        """Refuse WITHOUT cancelling the resting hedge: preserve pair orders."""
        emit_fn(
            service="decide", cycle=cycle, phase="quoting",
            action="single_leg_refusal", market_slug=title, reason=reason,
            extra={"condition_id": str(market.condition_id),
                   "pair_ids": pair_ids},
        )
        preserved = set(preserve_order_ids)
        for pair_id in pair_ids:
            for order in pair_orders.get(pair_id, []):
                order_id = str(order.get("id") or order.get("order_id") or "")
                if order_id:
                    preserved.add(order_id)
        return LifecycleQuoteContext(
            refusal_reason=reason,
            preserve_order_ids=frozenset(preserved),
            lifecycle_state=state,
        )

    for pair_id, active_pair_orders in pair_orders.items():
        sizes = {"UP": 0.0, "DOWN": 0.0}
        notionals = {"UP": 0.0, "DOWN": 0.0}
        pair_records = registry.get_orders_by_pair(pair_id)
        if not pair_records:
            raise ValueError(
                f"active lifecycle pair {pair_id!r} has no registry orders")
        for order in pair_records:
            if order.condition_id != market.condition_id:
                raise ValueError(
                    f"lifecycle pair {pair_id!r} contains an order for "
                    f"condition {order.condition_id!r}")
            if order.side != "BUY":
                continue
            if order.token_id == up_token:
                side = "UP"
            elif order.token_id == down_token:
                side = "DOWN"
            else:
                raise ValueError(
                    f"lifecycle pair {pair_id!r} contains an unknown token "
                    f"{order.token_id!r}")
            matched = float(registry.get_size_matched(order.id))
            notional = float(registry.get_matched_notional(order.id))
            if not math.isfinite(matched) or not math.isfinite(notional):
                raise ValueError(
                    f"lifecycle pair {pair_id!r} has non-finite fill totals")
            sizes[side] += matched
            notionals[side] += notional

        up_avg = notionals["UP"] / sizes["UP"] if sizes["UP"] else 0.0
        down_avg = (
            notionals["DOWN"] / sizes["DOWN"] if sizes["DOWN"] else 0.0
        )
        position = SingleLegPosition(
            pair_id=pair_id,
            condition_id=str(market.condition_id),
            up_token_id=up_token,
            down_token_id=down_token,
            up_size=sizes["UP"],
            down_size=sizes["DOWN"],
            up_avg_price=up_avg,
            down_avg_price=down_avg,
            up_best_bid=up_book.get("best_bid"),
            down_best_bid=down_book.get("best_bid"),
            base_offset=base_offset,
        )
        previous = registry.get_lifecycle_state(pair_id)
        decision = evaluate_single_leg_lifecycle(
            position,
            registry,
            bot_pos_price_max_pair_cost_usd=float(cfg.bot_pos_price_max_pair_cost_usd),
            bot_risk_stop_mode=str(cfg.bot_risk_stop_mode),
            bot_risk_stop_adverse_drop_cents=float(cfg.bot_risk_stop_adverse_drop_cents),
            bot_risk_stop_max_drawdown_pct=float(cfg.bot_risk_stop_max_drawdown_pct),
            bot_risk_circuit_max_drawdown_pct=float(cfg.bot_risk_circuit_max_drawdown_pct),
            tick_size=float(cfg.bot_quote_grid_tick_size_usd),
        )
        decisions.append((pair_id, position, decision))

        previous_state = previous.state if previous is not None else None
        if (decision.action != "refused"
                and (previous_state is None
                     or previous_state != decision.state.value)):
            held_bid = None
            if decision.held_token_id == position.up_token_id:
                held_bid = position.up_best_bid
            elif decision.held_token_id == position.down_token_id:
                held_bid = position.down_best_bid
            emit_fn(
                service="decide", cycle=cycle, phase="quoting",
                action="single_leg_transition", market_slug=title,
                reason=decision.reason,
                extra={
                    "condition_id": position.condition_id,
                    "pair_id": pair_id,
                    "previous_state": previous_state,
                    "state": decision.state.value,
                    "held_token_id": decision.held_token_id,
                    "held_average_price": decision.held_average_price,
                    "held_best_bid": held_bid,
                    "naked_size": decision.naked_size,
                    "opposite_token_id": decision.opposite_token_id,
                    "opposite_limit_price": decision.opposite_limit_price,
                },
            )

        if decision.action == "wait":
            for order in active_pair_orders:
                order_id = str(order.get("id") or order.get("order_id") or "")
                if not order_id:
                    continue
                if order.get("token_id") == decision.held_token_id:
                    cancel_order_ids.add(order_id)
                else:
                    preserve_order_ids.add(order_id)
        elif decision.action == "hedge":
            for order in active_pair_orders:
                order_id = str(order.get("id") or order.get("order_id") or "")
                if not order_id:
                    continue
                if order.get("token_id") == decision.opposite_token_id:
                    if (decision.opposite_limit_price is not None
                            and math.isclose(
                                float(order["price"]),
                                decision.opposite_limit_price,
                                rel_tol=0.0,
                                abs_tol=1e-9,
                            )):
                        preserve_order_ids.add(order_id)
                        escalated_order_already_resting = True
                        continue
                    replace_order_ids.add(order_id)
                cancel_order_ids.add(order_id)

    hard_stops = [
        (pair_id, _position, decision)
        for pair_id, _position, decision in decisions
        if decision.action == "hard_stop"
    ]
    if hard_stops:
        reason = "; ".join(
            f"lifecycle hard stop for pair {pair_id}: {decision.reason}"
            for pair_id, _position, decision in hard_stops
        )
        return refusal_context(
            reason, [pair_id for pair_id, _position, _ in hard_stops],
            state=hard_stops[0][2].state.value)

    refused_escalations = [
        (pair_id, _position, decision)
        for pair_id, _position, decision in decisions
        if decision.action == "refused"
    ]
    if refused_escalations:
        # A refused escalation must not strand the pair by cancelling its
        # resting hedge: hold every working order and suppress a fresh quote,
        # retrying next cycle. Only the poll's hard-stop exit may cancel.
        reason = "; ".join(
            f"lifecycle refused for pair {pair_id}: {decision.reason}"
            for pair_id, _position, decision in refused_escalations
        )
        return preserve_refusal_context(
            reason,
            [pair_id for pair_id, _position, _decision in refused_escalations],
            state=refused_escalations[0][2].state.value,
        )

    escalations = [
        (pair_id, position, decision)
        for pair_id, position, decision in decisions
        if decision.action == "hedge"
    ]
    if len(escalations) > 1:
        pair_ids = [pair_id for pair_id, _position, _decision in escalations]
        reason = (
            "lifecycle ambiguous: simultaneous escalations for pairs "
            + ", ".join(pair_ids)
        )
        return refusal_context(reason, pair_ids)

    if len(escalations) == 1:
        pair_id, position, decision = escalations[0]
        if (decision.held_average_price is None
                or decision.opposite_limit_price is None
                or decision.opposite_token_id is None
                or not math.isfinite(decision.held_average_price)
                or not math.isfinite(decision.opposite_limit_price)
                or decision.naked_size <= 0
                or not math.isfinite(decision.naked_size)):
            return preserve_refusal_context(
                f"lifecycle escalation for pair {pair_id} has invalid price "
                "or residual size",
                [pair_id],
            )
        side = "DOWN" if decision.held_token_id == up_token else "UP"
        pair_price = (
            decision.held_average_price + decision.opposite_limit_price
        )
        size_cap = risk.size_for(
            cfg, inventory, side, decision.opposite_limit_price,
            pair_price=pair_price,
        )
        size = min(int(decision.naked_size), size_cap)
        if size < cfg.bot_quote_size_min_shares:
            # Keep the resting hedge instead of cancelling it; a transient
            # size refusal is not a reason to strand the pair ahead of the
            # hard-stop or settlement path (see single_leg_refusal).
            return preserve_refusal_context(
                f"lifecycle escalation size {size} below venue minimum "
                f"{cfg.bot_quote_size_min_shares}",
                [pair_id],
            )
        return LifecycleQuoteContext(
            override=LifecycleQuoteOverride(
                pair_id=pair_id,
                token_id=decision.opposite_token_id,
                price=decision.opposite_limit_price,
                size=size,
                state=decision.state,
                held_average_price=decision.held_average_price,
            ),
            preserve_order_ids=frozenset(preserve_order_ids),
            replace_order_ids=frozenset(replace_order_ids),
            cancel_order_ids=frozenset(cancel_order_ids),
            lifecycle_pair_id=(
                None if escalated_order_already_resting else pair_id
            ),
            lifecycle_state=decision.state.value,
        )

    return LifecycleQuoteContext(
        preserve_order_ids=frozenset(preserve_order_ids),
        replace_order_ids=frozenset(replace_order_ids),
        cancel_order_ids=frozenset(cancel_order_ids),
    )


def _tail_note(mem: Optional[dict], cid: str, kind: str, sig: str,
               msg: str, *args) -> None:
    """Steady-state terminal lines: INFO once per change, DEBUG while unchanged.

    A rotation that rests unchanged orders (or re-reports the same skip
    reason) is not news, but the first time it happens is -- so the first
    visit logs at INFO and repeats drop to DEBUG. Every INFO line is followed
    by a blank line, so a decision stands apart from the idle around
    it. Action lines (orders posted or cancelled) are NOT routed here: an
    action is news every time it happens.

    `mem` is the loop's per-market memory, owned by `run` and passed down as
    `tail_memory`. None keeps the old always-INFO behaviour for direct
    callers (tests, one-shot scripts) that own no loop.
    """
    if mem is not None:
        key = (cid, kind)
        # A return to a previous state is a transition, not a repeat: resting
        # again after a skip prints even when the order count is unchanged.
        # Only the same kind twice in a row may go quiet.
        if mem.get(key) == sig and mem.get((cid, "__kind")) == kind:
            log.debug(msg, *args)
            return
        mem[key] = sig
        mem[(cid, "__kind")] = kind
    log.info(msg + "\n", *args)


def _enforce_entry_gate(seam, cid, to_submit, lifecycle_pair_id):
    """Drop fresh-entry submits for a marked market, keep lifecycle ones (#646).

    Returns (submits, blocked). The visit-loop skip handles the steady state;
    this is the race guard for a reset that commits between planning and
    submission. Hedge restoration builds its own submits later and is
    unaffected. An unreadable marks table blocks everything: nothing may be
    proven fresh, so nothing new goes out.
    """
    if not to_submit:
        return to_submit, False
    registry = getattr(seam, "registry", None)
    if registry is None:
        return to_submit, False
    try:
        marks = registry.requalification_marks()
    except Exception:
        return [], True
    if str(cid or "").lower() not in marks:
        return to_submit, False
    if lifecycle_pair_id is None:
        return [], True
    kept = [i for i in to_submit
            if getattr(i, "pair_id", None) == lifecycle_pair_id]
    return kept, True


def _execute_planned_orders(seam, market, cfg, title, cycle, to_cancel,
                            to_submit, cancel_reasons, queue_ahead, emit_fn):
    """Run the planned cancels then submits. Returns (submitted, cancelled, error).

    `error` is None on success; on failure it carries the same `submit/cancel`
    text the visit reports, and the market_error emit has already fired.
    Hedge restoration runs inside, so the whole terminal sequence stays under
    the caller's lifecycle guard.
    """
    submitted = cancelled = 0
    cancelled_lifecycle_orders = [
        o for o in to_cancel
        if cancel_reasons.get(str(o.get("id") or o.get("order_id") or "")) == "lifecycle_replace"
    ]
    try:
        # Cancel first: cancelling old quotes before submitting replacements
        # prevents exceeding MAX_TOTAL_USD notional exposure and avoids double
        # quoting if replacement submission occurs while stale orders rest.
        confirmed_cancelled_lifecycle = []
        if to_cancel:
            for row in to_cancel:
                key = str(row.get("id") or row.get("order_id") or "")
                if key in cancel_reasons:
                    row["cancel_reason"] = cancel_reasons[key]
                if queue_ahead and key in queue_ahead:
                    row["cancel_queue_ahead"] = queue_ahead[key]
            cancelled = seam.cancel_fn(seam.client, seam.registry, to_cancel)
            if cancelled < len(to_cancel):
                still_resting = _still_resting(seam, to_cancel)
                if still_resting:
                    raise RuntimeError(
                        f"cancel failed: {len(still_resting)}/{len(to_cancel)} still "
                        f"resting; aborting replacement submission"
                    )
            # Only consider lifecycle orders whose cancellation confirmed complete
            confirmed_cancelled_lifecycle = list(cancelled_lifecycle_orders)
        if to_submit:
            submitted = seam.submit_fn(seam.client, seam.registry, market, to_submit, cfg)
            if submitted == 0 and confirmed_cancelled_lifecycle:
                # Submit accepted zero orders: attempt to restore cancelled hedge order
                for old_o in confirmed_cancelled_lifecycle:
                    try:
                        restore_intent = QuoteIntent(
                            side=old_o.get("side", "BUY"),
                            token_id=old_o.get("token_id"),
                            price=float(old_o["price"]),
                            size=int(old_o.get("size") or old_o.get("original_size") or cfg.bot_quote_size_min_shares),
                            mid=float(old_o["price"]),
                            edge_vs_mid=0.0,
                            pair_id=old_o.get("pair_id"),
                        )
                        seam.submit_fn(seam.client, seam.registry, market, [restore_intent], cfg)
                        log.warning("[HEDGE RESTORED] %s | Restored hedge %s after 0 orders submitted", title, old_o.get("id"))
                    except Exception as rest_err:
                        log.error("[HEDGE RESTORE FAILED] %s | Failed to restore hedge %s: %s", title, old_o.get("id"), rest_err)
    except Exception as e:
        submitted = getattr(e, PARTIAL_SUBMIT_PLACED_ATTR, getattr(e, "placed", submitted))
        if confirmed_cancelled_lifecycle and submitted == 0:
            # Submit raised exception with 0 placed: attempt to restore confirmed cancelled hedge order
            for old_o in confirmed_cancelled_lifecycle:
                try:
                    restore_intent = QuoteIntent(
                        side=old_o.get("side", "BUY"),
                        token_id=old_o.get("token_id"),
                        price=float(old_o["price"]),
                        size=int(old_o.get("size") or old_o.get("original_size") or cfg.bot_quote_size_min_shares),
                        mid=float(old_o["price"]),
                        edge_vs_mid=0.0,
                        pair_id=old_o.get("pair_id"),
                    )
                    seam.submit_fn(seam.client, seam.registry, market, [restore_intent], cfg)
                    log.warning("[HEDGE RESTORED] %s | Restored hedge %s after submit failure", title, old_o.get("id"))
                except Exception as rest_err:
                    log.error("[HEDGE RESTORE FAILED] %s | Failed to restore hedge %s: %s", title, old_o.get("id"), rest_err)
        # A submit/cancel failure (venue rejection, a split couple rolled back)
        # must degrade this market to ERROR, never stop the rotation.
        emit_fn(service="decide", cycle=cycle, phase="quoting",
                action="market_error", market_slug=title,
                reason=f"submit/cancel: {type(e).__name__}: {e}",
                extra={"submitted": submitted, "cancelled": cancelled})
        return submitted, cancelled, f"submit/cancel: {type(e).__name__}: {e}"
    return submitted, cancelled, None


def _visit_one(
    seam: VenueSeam,
    spec,
    live: bool,
    cycle: int = 0,
    emit_fn: Optional[Callable] = None,
    plan_fn: Optional[Callable] = None,
    stop_memory: Optional[dict] = None,
    tail_memory: Optional[dict] = None,
    *,
    inventory_recorder: Optional[Callable[[str, Inventory], None]] = None,
) -> LiveFleetResult:
    """One poll of one market: fetch -> decide -> plan -> submit/cancel.

    `stop_memory` is the loop's condition-id -> last-stop-code map for
    on-change-only `lifecycle_stop` rows; None behaves as an empty map.
    `tail_memory` is the loop's (condition-id, line-kind) -> last-signature
    map for the quiet terminal tail (`_tail_note`); None keeps the old
    always-INFO tail for callers that own no loop.

    `inventory_recorder` (#505), when set, receives `(lowercased condition id,
    Inventory)` after each successful inventory read so `run` can order the
    next rotation's visits; None (every caller that owns no loop) reads the
    inventory exactly as before.
    """
    cid = _cid(spec)
    if emit_fn is None:
        emit_fn = getattr(seam, "emit_fn", None) or (lambda *a, **k: None)
    feed_metadata = spec if isinstance(spec, dict) else {}
    paired_context = getattr(seam.registry, "paired_context", None)
    paired_enabled = isinstance(paired_context, dict)
    if paired_enabled and not cid:
        from core_brain.paired_shadow import record_paired_feed_event
        record_paired_feed_event(
            paired_context["db_path"], run_id=paired_context["run_id"],
            kind="missing_condition_id", detail="selected paired feed row has no condition id")
        return LiveFleetResult(
            status="ERROR", error="paired feed row has no condition id")
    if paired_enabled:
        from core_brain.paired_shadow import (
            PairedShadowError, record_paired_market_selection,
        )
        try:
            record_paired_market_selection(
                paired_context["db_path"], run_id=paired_context["run_id"],
                arm=paired_context["arm"], cutoff_usd=paired_context["cutoff_usd"],
                spec={**feed_metadata, "cid": cid},
            )
        except PairedShadowError as exc:
            emit_fn(service="decide", cycle=cycle, phase="quoting",
                    action="market_error", market_slug=cid[:16],
                    reason=f"paired attribution: {exc}",
                    extra={"condition_id": cid})
            return LiveFleetResult(
                status="ERROR", condition_id=cid,
                error=f"paired attribution: {exc}")
    # UMA re-check gate (#408): runs BEFORE fetch_market so a CLOB outage
    # on a flipped market cannot skip the cancel. Title is unknown until
    # the fetch, so the gate logs cid[:16] when title is empty.
    uma_gate = _check_uma_resolution_before_fetch(
        seam, cid, title=cid[:16], cycle=cycle, emit_fn=emit_fn)
    if uma_gate is not None:
        return uma_gate
    try:
        market = seam.fetch_market(cid)
    except Exception as e:
        if paired_enabled:
            from core_brain.paired_shadow import record_paired_feed_event
            record_paired_feed_event(
                paired_context["db_path"], run_id=paired_context["run_id"],
                kind="market_resolution_failed",
                detail=f"market={cid} error={type(e).__name__}")
        log.warning("[MARKET LOOKUP ERROR] %s | %s", cid[:16], e)
        emit_fn(service="decide", cycle=cycle, phase="quoting",
                action="market_error", market_slug="",
                reason=f"{type(e).__name__}: {e}")
        return LiveFleetResult(status="ERROR", condition_id=cid,
                               error=f"{type(e).__name__}: {e}")

    # Series evidence and game clocks were demolished in the time-of-game
    # purge (#597): the feed row's series state is not attached and the
    # observed rotation cadence is not carried (nothing keys off it).
    title = getattr(market, "market_slug", "") or cid[:16]
    if paired_enabled:
        from core_brain.paired_shadow import PairedShadowError, record_paired_market_tokens
        try:
            record_paired_market_tokens(
                paired_context["db_path"], run_id=paired_context["run_id"],
                condition_id=cid,
                up_token_id=getattr(market, "up_token", ""),
                down_token_id=getattr(market, "down_token", ""),
            )
        except PairedShadowError as exc:
            emit_fn(service="decide", cycle=cycle, phase="quoting",
                    action="market_error", market_slug=title,
                    reason=f"paired attribution: {exc}",
                    extra={"condition_id": cid})
            return LiveFleetResult(
                status="ERROR", condition_id=cid, title=title,
                error=f"paired attribution: {exc}")
    cfg = _market_cfg(seam.base_cfg, spec)
    # Set inside the try below on every path that reaches the placement code;
    # the ERROR returns before it are the only callers that never read it.
    queue_why = ""
    try:
        open_orders = (
            seam.open_orders_fn(market)
            if seam.open_orders_fn
            else []
        )

        def lifecycle_context_for(got_market, up_book, down_book, inventory):
            return _lifecycle_quote_context(
                seam, got_market, cfg, up_book, down_book, inventory,
                open_orders, cycle=cycle, title=title, emit_fn=emit_fn,
            )

        ev = evaluate_market_quote(
            cid, cfg, seam.clob_host,
            # The market is already fetched (the block above exists so a fetch
            # failure degrades to ERROR with no title); hand it to the shared
            # step, which owns the books -> inventory -> decide sequence.
            fetch_market=lambda c: market,
            fetch_books=seam.fetch_books,
            inventory_for=_recording_inventory(
                seam.inventory_fn or (lambda m: Inventory()),
                inventory_recorder, cid),
            decide=seam.decide,
            lifecycle_context_for=lifecycle_context_for,
        )
        intents, why = ev.intents, ev.why
        # The hedge ask for a token is the OTHER token's ask -- that is the
        # price a fill on this leg would have to pay to finish the pair.
        # `evaluate_market_quote` already fetched both books; re-reading them
        # here would be a second venue round-trip for a number we hold.
        up_tok = ev.up_book.get("token_id")
        dn_tok = ev.down_book.get("token_id")
        hedge_asks = {
            up_tok: ev.down_book.get("best_ask"),
            dn_tok: ev.up_book.get("best_ask"),
        }
        # A token whose hedge we already hold stands down from the re-gate:
        # the pair is finished from inventory, not by paying the hedge ask.
        # This mirrors the `inv.avg(other) > 0` skip in
        # `quotes._decide_quotes_from_mid` -- the two gates must agree, or the
        # planner cancels every cycle what the decider was happy to post.
        hedge_held: dict[str, float] = {}
        if ev.inventory.avg("DOWN") > 0:
            hedge_held[up_tok] = ev.inventory.avg("DOWN")
        if ev.inventory.avg("UP") > 0:
            hedge_held[dn_tok] = ev.inventory.avg("UP")
        # Why each cancel happened, and where the order stood in its queue when
        # it did. Both travel to the registry with the status change: a cancel
        # with no recorded reason cannot be told from churn afterwards.
        cancel_reasons: dict[str, str] = {}
        queue_ahead = _queue_ahead_for(seam, open_orders)
        # HOLD-ON-REFUSAL (#390): an empty intent list from a visited market
        # is a refusal, not a disappearance. Transient reasons hold resting
        # orders; terminal ones (settled, expired, exited, unfunded) cancel.
        # `None` (legacy callers) keeps the old cancel-on-empty semantics.
        lifecycle_refused = bool(
            ev.lifecycle_context is not None
            and ev.lifecycle_context.refusal_reason
        )
        lifecycle_holds = bool(
            ev.lifecycle_context is not None
            and ev.lifecycle_context.preserve_order_ids
        )
        # A refused ESCALATION preserves its resting orders (resting hedge):
        # classify it like an ordinary refusal so transient reasons hold and
        # only genuinely terminal reasons (plus hard-stop's own cancellations,
        # which are explicit in the lifecycle context) cancel. A lifecycle
        # refusal with no preserve intent (hard stop, or a refusal that the
        # planner itself must convert) stays terminal -- it is the poll that
        # executes the hard stop after re-reading venue state.
        visit_outcome = (
            VisitOutcome.REFUSED_TERMINAL
            if lifecycle_refused and not lifecycle_holds
            else _classify_refusal(why) if not intents
            else VisitOutcome.QUOTED
        )
        if intents:
            # A quote in between re-arms stop rows: the next stop is news.
            if stop_memory is not None:
                stop_memory.pop(cid, None)
        else:
            _note_lifecycle_stop(
                seam, cid, title, _visit_stop(why),
                why, stop_memory, cycle, emit_fn)
            _note_refusal_visit(
                seam, cid, title,
                visit_outcome.value,
                why, 1, emit_fn)
        plan_kwargs = {
            "dead_band": float(getattr(cfg, "bot_order_requote_dead_band_cents", 0.0)),
            "cfg": cfg,
            "hedge_asks": hedge_asks,
            "hedge_held": hedge_held,
            "reasons": cancel_reasons,
            "queue_ahead": queue_ahead,
            "visit_outcome": visit_outcome,
        }
        lifecycle_context = ev.lifecycle_context
        if lifecycle_context is not None and (
                lifecycle_context.preserve_order_ids
                or lifecycle_context.replace_order_ids
                or lifecycle_context.cancel_order_ids
                or lifecycle_context.lifecycle_pair_id is not None):
            plan_kwargs.update({
                "preserve_order_ids": set(
                    lifecycle_context.preserve_order_ids),
                "replace_order_ids": set(
                    lifecycle_context.replace_order_ids),
                "cancel_order_ids": set(
                    lifecycle_context.cancel_order_ids),
                "lifecycle_pair_id": lifecycle_context.lifecycle_pair_id,
            })
        to_cancel, to_submit = (plan_fn or plan_orders)(
            open_orders, intents, **plan_kwargs,
        )
        # THE QUEUE-CLEAR GATE (#393), on NEW placements only and after the
        # planner, so held orders, the refusal-grace counter and every recorded
        # cancel reason stay exactly as they were. Record-only at ship: it
        # normally reports and changes nothing.
        lifecycle_state = (
            getattr(lifecycle_context, "lifecycle_state", None)
            if lifecycle_context is not None else None
        )
        to_submit, queue_why = _admit_placements(
            to_submit, market, ev.up_book, ev.down_book, seam.flow_fn, cfg,
            lifecycle_state=lifecycle_state)
    except Exception as e:
        emit_fn(service="decide", cycle=cycle, phase="quoting",
                action="market_error", market_slug=title,
                reason=f"{type(e).__name__}: {e}")
        return LiveFleetResult(status="ERROR", condition_id=cid, title=title,
                               error=f"{type(e).__name__}: {e}")

    event_extra = {"intent_count": len(intents), "condition_id": cid}
    # WS-book provenance (#650): when the loop reads books from the stream
    # cache instead of REST, say so on the row so a quote is traceable to its
    # source. Unwired seams (shadow, tests, `--no-ws-books`) emit byte-identical
    # rows to before.
    if seam.book_stats_fn is not None:
        try:
            _stats = seam.book_stats_fn() or {}
        except Exception:
            _stats = {}
        if isinstance(_stats, dict):
            event_extra.update(_stats)
    if title:
        event_extra["bot_market_title"] = title
    _decide_quotes = []
    for _di in list(intents)[:4]:
        _dside = getattr(_di, "side", None)
        if _dside not in ("UP", "DOWN"):
            continue
        try:
            _decide_quotes.append({
                "side": _dside,
                "price": float(getattr(_di, "price", 0.0)),
                "size": float(getattr(_di, "size", 0.0)),
            })
        except (TypeError, ValueError):
            continue
    if _decide_quotes:
        event_extra["quotes"] = _decide_quotes
    if queue_why:
        event_extra["queue_why"] = queue_why
    if feed_metadata.get("paired_depth_arm"):
        event_extra.update({
            "paired_depth_arm": feed_metadata["paired_depth_arm"],
            "paired_depth_cutoff_usd": feed_metadata["paired_depth_cutoff_usd"],
            "paired_depth_snapshot_id": feed_metadata["paired_depth_snapshot_id"],
        })
    emit_fn(service="decide", cycle=cycle, phase="quoting", action="decide",
            market_slug=title, reason=why, extra=event_extra)



    if not live:
        if intents:
            orders_desc = ", ".join(f"{i.side} {i.size}sh @ ${i.price:.3f}" for i in intents)
            log.info("[DRY RUN QUOTE] %s | Planned %d orders: %s\n", title, len(intents), orders_desc)
        elif why:
            _tail_note(tail_memory, cid, "dry-skipped", why,
                       "[DRY RUN SKIPPED] %s | %s", title, why)
        else:
            _tail_note(tail_memory, cid, "dry-idle", "idle",
                       "[DRY RUN IDLE] %s | No quote action needed", title)
        if queue_why:
            # The dry run is where this gate is watched before it is enforced:
            # how many new placements would go out, and why the rest were
            # measured as unfillable.
            log.info("[DRY RUN QUEUE] %s | admitted %d new placement(s) -- %s",
                     title, len(to_submit), queue_why)
        return LiveFleetResult(
            status="DRY_RUN" if intents else "DECLINED",
            condition_id=cid, title=title, why=why, intents=list(intents),
            queue_why=queue_why)

    # Hedge preservation (#453):
    # Do not cancel an active hedge under `lifecycle_replace` if no replacement intent survives placement admission filters.
    if to_cancel:
        filtered_to_cancel = []
        for o in to_cancel:
            key = str(o.get("id") or o.get("order_id") or "")
            reason = cancel_reasons.get(key, "")
            if reason == "lifecycle_replace":
                tok = o.get("token_id")
                # Check if there is an admitted submission covering this token on the lifecycle pair
                has_admitted_replacement = any(
                    i.token_id == tok and getattr(i, "pair_id", None) == o.get("pair_id")
                    for i in to_submit
                ) if o.get("pair_id") else any(i.token_id == tok for i in to_submit)
                if not has_admitted_replacement:
                    log.warning(
                        "[HEDGE PRESERVED] %s | Aborting cancellation of resting hedge %s: no replacement survived admission",
                        title, key,
                    )
                    continue
            filtered_to_cancel.append(o)
        to_cancel = filtered_to_cancel

    # Final entry gate (#646): a reset may commit between planning and this
    # submission. Reread the mark under the lifecycle guard and drop
    # fresh-entry submits; lifecycle-context submits (hedge replace/restore
    # on the managed pair) still go out.
    from core_brain.single_buy_saver import lifecycle_mutation_guard
    gate_pair_id = (getattr(lifecycle_context, "lifecycle_pair_id", None)
                    if lifecycle_context is not None else None)
    with lifecycle_mutation_guard():
        to_submit, gate_blocked = _enforce_entry_gate(
            seam, cid, to_submit, gate_pair_id)
        submitted, cancelled, submit_error = _execute_planned_orders(
            seam, market, cfg, title, cycle, to_cancel, to_submit,
            cancel_reasons, queue_ahead, emit_fn)
    if submit_error is not None:
        return LiveFleetResult(
            status="ERROR", condition_id=cid, title=title, why=why,
            intents=list(intents), submitted=submitted, cancelled=cancelled,
            error=submit_error)

    submit_extra = {"submitted": submitted, "cancelled": cancelled}
    if queue_why:
        submit_extra["queue_why"] = queue_why
    emit_fn(service="decide", cycle=cycle, phase="quoting", action="submit",
            market_slug=title, extra=submit_extra)

    if queue_why:
        _tail_note(tail_memory, cid, "queue", queue_why,
                   "[QUEUE] %s | %s", title, queue_why)
    if submitted > 0:
        orders_desc = ", ".join(f"{i.side} {i.size}sh @ ${i.price:.3f}" for i in to_submit)
        log.info("[QUOTING] %s | Posted %d orders: %s\n", title, submitted, orders_desc)
    elif cancelled > 0:
        log.info("[REQUOTE] %s | Cancelled %d stale order(s)\n", title, cancelled)
    elif open_orders:
        _tail_note(tail_memory, cid, "resting", str(len(open_orders)),
                   "[RESTING] %s | %d quote(s) resting at target spread", title, len(open_orders))
    elif why:
        _tail_note(tail_memory, cid, "skipped", why,
                   "[SKIPPED] %s | %s", title, why)

    gate_held = gate_blocked and bool(intents) and not to_submit
    final_why = why
    if gate_held:
        final_why = (why + "; awaiting_requalification") if why else "awaiting_requalification"
    return LiveFleetResult(
        status="DECLINED" if gate_held else ("QUOTED" if intents else "DECLINED"),
        condition_id=cid, title=title, why=final_why, intents=list(intents),
        submitted=submitted, cancelled=cancelled,
        queue_why=queue_why)


# --- production wiring ------------------------------------------------------

class MarketSpecList(list):
    """Specs carrying their publication metadata (#646).

    Only the production feed path mints these, via `load_graduated_snapshot`.
    Plain lists from any other supplier (shadow, tests, paired bundles)
    carry no metadata and can never clear a re-qualification mark.
    """

    def __init__(self, specs=(), *, published_mtime_ns=None,
                 condition_ids=frozenset()):
        super().__init__(specs)
        self.published_mtime_ns = published_mtime_ns
        self.publication_condition_ids = frozenset(condition_ids)


def _evaluate_requalification(registry, fresh) -> None:
    """Clear or retain reset marks against one publication (#646).

    Runs after a successful `markets_fn()` load, before empty-result
    handling. Only a publication strictly newer than the reset clears its
    generation when it includes the market; a newer one that excludes it is
    recorded and the market stays blocked. Suppliers without metadata,
    and any failure here, leave every mark exactly where it was.
    """
    published_ns = getattr(fresh, "published_mtime_ns", None)
    raw_cids = getattr(fresh, "publication_condition_ids", None)
    if published_ns is None or raw_cids is None or registry is None:
        return
    try:
        marks = registry.requalification_marks()
    except Exception as exc:
        log.warning("re-qualification evaluation skipped: %s", exc)
        return
    for cid, mark in marks.items():
        try:
            if published_ns <= int(mark.reset_ts_ms) * 1_000_000:
                continue
            if cid in raw_cids:
                registry.clear_requalification(cid, mark.generation)
            else:
                registry.record_requalification_exclusion(
                    cid, mark.generation, int(published_ns),
                    now_ms=int(time.time() * 1000))
        except Exception as exc:
            log.warning("re-qualification evaluation for %s skipped: %s",
                        cid, exc)


def _is_awaiting_requalification(registry, marks, cid: str) -> bool:
    """Whether a market gets no new entry quotes this cycle (#646).

    A marked market with no lifecycle row is blocked. One under lifecycle
    management still visits -- its hedges, not fresh entries, are the work.
    Unreadable marks fail closed for fresh entries only, never for managed
    markets.
    """
    if registry is None or not cid:
        return False
    try:
        managed = registry.has_lifecycle_for_condition(cid)
    except Exception:
        return False
    if managed:
        return False
    if marks is None:
        return True
    return str(cid).lower() in marks


def _market_specs(max_markets: Optional[int] = None, registry=None,
                  path=None, paired_arm: Optional[str] = None) -> list[dict]:
    """Graduated markets as per-market dict specs, mirroring fleet.MarketState.

    If max_markets is 1 and a market already has active open orders in the registry,
    prioritise that active market so we never quote a second market concurrently.

    `path` reroutes the feed read (a trial shadow run's own markets file);
    None keeps the default feed every other caller uses. `paired_arm` reads one
    side of an atomic paired-depth bundle without changing the live path.
    """
    from core_brain.market_feed import load_graduated_markets
    published_ns = None
    publication_cids = frozenset()
    if paired_arm is None:
        from core_brain.market_feed import load_graduated_snapshot
        snapshot = load_graduated_snapshot(path=path)
        gms = snapshot.rows
        published_ns = snapshot.published_mtime_ns
        publication_cids = snapshot.condition_ids
    else:
        gms = load_graduated_markets(path=path, paired_arm=paired_arm)
    if not gms:
        return []

    if max_markets == 1 and registry is not None:
        try:
            active_orders = registry.get_active_orders()
            active_cids = {
                o.condition_id for o in active_orders
                if o.condition_id and getattr(o, "status", "") in ("open", "partial", "pending")
            }
            if active_cids:
                active_cid = next(iter(active_cids))
                active_gm = next((gm for gm in gms if gm.cid == active_cid), None)
                if active_gm:
                    gms = [active_gm]
                else:
                    return []
        except Exception as e:
            log.warning("active orders lookup failed in _market_specs: %s", e)
            raise

    if max_markets:
        gms = gms[:max_markets]
    specs = []
    for gm in gms:
        spec = {
            "cid": gm.cid,
            "min_size": gm.min_size,
            "shares": gm.shares,
            "max_spread": gm.max_spread,
            "tick": gm.tick,
            "daily": gm.daily,
            "title": gm.title,
            "slug": gm.slug,
            # Venue event evidence for the dashboard/funnel, not a gate
            # (time-of-game purge, #597). Absent on old feeds: the parser
            # fails closed without it.
            "sports_market_type": getattr(gm, "sports_market_type", ""),
            "event_score": getattr(gm, "event_score", None),
            "event_period": getattr(gm, "event_period", None),
            "question": getattr(gm, "question", None) or gm.title,
            "event_live": getattr(gm, "event_live", False),
            "event_ended": getattr(gm, "event_ended", False),
            "series_ts": getattr(gm, "series_ts", None),
        }
        if paired_arm is not None:
            spec.update({
                "paired_depth_arm": gm.paired_depth_arm,
                "paired_depth_cutoff_usd": gm.paired_depth_cutoff_usd,
                "paired_depth_snapshot_id": gm.paired_depth_snapshot_id,
                "trial_arm": gm.trial_arm,
                "trial_axis": gm.trial_axis,
                "snapshot_id": gm.snapshot_id,
                "event_cluster_id": gm.event_cluster_id,
                "family": gm.family,
                "admission_role": gm.admission_role,
                "fallback_reason": gm.fallback_reason,
                "event_id": gm.event_id,
                "event_slug": gm.event_slug,
                "event_title": gm.event_title or gm.title,
            })
        specs.append(spec)
    if paired_arm is None:
        return MarketSpecList(specs, published_mtime_ns=published_ns,
                              condition_ids=publication_cids)
    return specs


def _fetch_market(cid: str):
    """Resolve one market on the venue, raising so the loop records ERROR."""
    from core_brain.markets import fetch_pinned_market
    m = fetch_pinned_market(cid)
    if m is None:
        raise LookupError(
            f"no tradeable market at {cid[:16]}... (missing, closed, or not 2 tokens)")
    return m


def _make_inventory_fn(registry, db_path: Path):
    def inventory_fn(market) -> Inventory:
        from core_brain.order_registry import inventory_from_registry
        return inventory_from_registry(
            market.condition_id, market.up_token, market.down_token,
            db_path=db_path)
    return inventory_fn


def _make_open_orders_fn(registry):
    """Open orders for a market, shaped for plan_orders (token/price/order_id/side)."""
    def open_orders_fn(market) -> list[dict]:
        out = []
        for o in registry.get_active_orders():
            if o.condition_id != market.condition_id or o.status not in ("open", "partial"):
                continue
            out.append({
                "token_id": o.token_id,
                "price": o.price,
                "order_id": o.order_id or o.id,
                "id": o.id,
                "side": o.side,
                "status": o.status,
                "pair_id": o.pair_id,
            })
        return out
    return open_orders_fn


def _submit_intents(client, registry, market, intents, cfg) -> int:
    """Place decided intents as BUY orders, reusing `live_exec.quote`'s discipline.

    Row first, then send: a registry row is written before the venue call so a
    crash in between leaves a `pending` row that reconcile's orphan adoption can
    claim, never an untracked live order. Passive legs batch-post post_only;
    crossed (emergency-hedge) legs are the one place this strategy takes
    liquidity, so they post as FOK.

    A couple whose legs split (one accepted, one rejected) is rolled back by
    cancelling the survivor -- a lone resting leg is a naked position taken on
    purpose. Rows the venue never acknowledged are marked cancelled, never left
    half-open.
    """
    from py_clob_client_v2.clob_types import (
        OrderArgsV2, OrderPayload, OrderType, PostOrdersV2Args,
    )
    from py_clob_client_v2.order_builder.constants import BUY
    from core_brain.venue import (
        open_notional, order_caps, venue_order_id,
    )
    from core_brain.order_registry import OrderRecord, QuoteRecord, get_run_id

    if not intents:
        return 0

    # Dynamic caps scale with live account value (AGENTS.md). When the trader
    # loop has already merged the latest venue account_value into base_cfg via
    # _fleet_state, use those; otherwise fall back to the live venue caps.
    _live_order, _live_total = order_caps()
    bot_risk_exposure_max_order_usd = float(getattr(cfg, "bot_risk_exposure_max_order_usd", _live_order)) if cfg is not None else _live_order
    bot_risk_exposure_max_total_usd = float(getattr(cfg, "bot_risk_exposure_max_total_usd", _live_total)) if cfg is not None else _live_total

    total_cost = sum(i.price * i.size for i in intents)
    for i in intents:
        if i.price * i.size > bot_risk_exposure_max_order_usd:
            raise RuntimeError(
                f"leg {i.side} ${i.price * i.size:.2f} exceeds "
                f"MAX_ORDER_USD ${bot_risk_exposure_max_order_usd:.2f}")
    already = open_notional(client)
    if already is None:
        raise RuntimeError(
            "Cannot check MAX_TOTAL_USD cap: venue open_orders unreachable")
    if already + total_cost > bot_risk_exposure_max_total_usd:
        raise RuntimeError(
            f"open ${already:.2f} + ${total_cost:.2f} exceeds "
            f"MAX_TOTAL_USD ${bot_risk_exposure_max_total_usd:.2f}")

    now_ms = int(time.time() * 1000)
    pair_id = f"pair-{uuid.uuid4().hex[:12]}"
    bot_pos_price_max_pair_cost_usd = getattr(cfg, "bot_pos_price_max_pair_cost_usd", 0.995)

    # A carried pair_id means this batch REPLACES one leg of an existing pair
    # whose complement still rests: the replacement must join that pair, not
    # open a new one, or the market ends up with two one-legged pairs -- the
    # detachment seen live (#206). `plan_orders` never stamps -- it only reads
    # the carry (`i.pair_id == lifecycle_pair_id`). The stamps come from the
    # lifecycle-escalation path in `core_brain/quotes.py` (which carries
    # `override.pair_id`); a fresh
    # `decide_quotes` batch carries None and mints below (#397).
    # Every carried intent in one batch shares one complement, so one id wins;
    # a batch mixing carried ids would be a planner bug, and minting fresh is
    # the safe fall-back rather than silently picking a side.
    carried = {getattr(i, "pair_id", None) for i in intents} - {None}
    if len(carried) == 1:
        pair_id = carried.pop()

    passive = [i for i in intents if not i.crossed]
    crossed = [i for i in intents if i.crossed]

    placed = 0
    try:
        for batch, order_type, post_only in (
            (passive, OrderType.GTC, True),
            (crossed, OrderType.FOK, False),
        ):
            if not batch:
                continue

            local_legs = []
            batch_args = []
            for i in batch:
                local_id = str(uuid.uuid4())
                registry.create_order(OrderRecord(
                    id=local_id, order_id=None, condition_id=market.condition_id,
                    token_id=str(i.token_id), side="BUY", price=i.price,
                    original_size=i.size, status="pending",
                    posted_ts=now_ms, last_polled_ts=now_ms,
                    pair_id=pair_id, max_pair_cost_at_post=bot_pos_price_max_pair_cost_usd,
                ))
                signed = client.create_order(OrderArgsV2(
                    price=i.price, size=i.size, side=BUY, token_id=i.token_id,
                    expiration=0))
                batch_args.append(PostOrdersV2Args(order=signed, orderType=order_type))
                local_legs.append({
                    "local_id": local_id, "token_id": str(i.token_id),
                    "price": i.price, "size": i.size, "side": i.side,
                    "mid": i.mid, "edge_vs_mid": i.edge_vs_mid, "crossed": i.crossed,
                })

            t_start = time.perf_counter()
            resp = client.post_orders(batch_args, post_only=post_only)
            post_latency_ms = (time.perf_counter() - t_start) * 1000.0
            resp_list = (resp if isinstance(resp, list)
                         else [resp] if isinstance(resp, dict) else [])

            # Validate response structure and asset identity before mapping to local_legs
            if len(resp_list) != len(local_legs):
                # Response count mismatch: refuse to map by position
                for leg in local_legs:
                    registry.update_order_status(
                        leg["local_id"], status="cancelled", last_polled_ts=now_ms)
                raise RuntimeError(
                    f"post_orders response count mismatch: sent {len(local_legs)} legs, "
                    f"got {len(resp_list)} responses; refusing to attach IDs by position")

            extracted = []
            for idx, leg in enumerate(local_legs):
                item = resp_list[idx] if idx < len(resp_list) else None
                if item is None:
                    extracted.append(None)
                    continue

                # Validate asset_id matches the token we submitted
                resp_token = None
                if isinstance(item, dict):
                    resp_token = item.get("asset_id") or item.get("token_id") or item.get("assetId")
                else:
                    resp_token = (getattr(item, "asset_id", None) or
                                 getattr(item, "token_id", None) or
                                 getattr(item, "assetId", None))

                if resp_token and str(resp_token) != leg["token_id"]:
                    # Asset identity mismatch: response is for wrong token
                    for leg_inner in local_legs:
                        registry.update_order_status(
                            leg_inner["local_id"], status="cancelled", last_polled_ts=now_ms)
                    raise RuntimeError(
                        f"Asset identity mismatch at position {idx}: sent token {leg['token_id']}, "
                        f"response carries {resp_token}; refusing to attach wrong ID")

                extracted.append(venue_order_id(item))

            ok = sum(1 for v in extracted if v is not None)

            # PARTIAL FAILURE: a couple that split must not leave a naked survivor.
            if ok == 0:
                for leg in local_legs:
                    registry.update_order_status(
                        leg["local_id"], status="cancelled", last_polled_ts=now_ms)
                log.warning("no order ids in post response for %s; rows cancelled",
                            market.condition_id[:12])
                continue

            if 0 < ok < len(local_legs):
                for idx, leg in enumerate(local_legs):
                    v_id = extracted[idx]
                    if v_id is not None:
                        try:
                            client.cancel_order(OrderPayload(orderID=v_id))
                        except Exception as e:
                            log.warning("rollback cancel of %s failed: %s", v_id, e)
                    registry.update_order_status(
                        leg["local_id"], status="cancelled", last_polled_ts=now_ms)
                err = RuntimeError(
                    f"partial post for {market.condition_id[:12]}: "
                    f"{ok}/{len(local_legs)} legs accepted; survivors cancelled")
                setattr(err, PARTIAL_SUBMIT_PLACED_ATTR, placed)
                raise err

            # Full agreement: commit venue ids and log the quote telemetry.
            for idx, leg in enumerate(local_legs):
                v_id = extracted[idx]
                registry.attach_venue_order_id(
                    leg["local_id"], v_id, status="open", last_polled_ts=now_ms)
                registry.log_quote(QuoteRecord(
                    ts=time.time(), market_slug=market.market_slug,
                    condition_id=market.condition_id, token_id=leg["token_id"],
                    side=leg["side"], price=leg["price"], size=leg["size"],
                    mid=leg["mid"], edge_vs_mid=leg["edge_vs_mid"],
                    order_id=v_id, local_id=leg["local_id"], run_id=get_run_id(),
                    latency_ms=post_latency_ms,
                ))
                placed += 1
    except Exception as exc:
        if not hasattr(exc, PARTIAL_SUBMIT_PLACED_ATTR):
            setattr(exc, PARTIAL_SUBMIT_PLACED_ATTR, placed)
        raise

    return placed


def _queue_ahead_for(seam, open_orders: list[dict]) -> dict:
    """Shares resting ahead of each open order, keyed by order id.

    Read from the registry's own `quotes.queue_ahead`, recorded at post time.
    Deliberately NOT from the rehearsal's queue store: the live loop must not
    import the shadow model, and `tests/test_shadow_run.py` enforces that line.
    An order whose quote carried no measurement is absent from the result,
    which the hold rule reads as "position unknown" and declines to act on.

    Never raises: a queue we cannot read must not stop the rotation.
    """
    registry = getattr(seam, "registry", None)
    if registry is None or not hasattr(registry, "queue_ahead_by_local_id"):
        return {}
    ids = [o.get("id") or o.get("order_id") for o in open_orders]
    try:
        return registry.queue_ahead_by_local_id(ids)
    except Exception:
        return {}


def _cancel_orders(client, registry, orders) -> int:
    """Cancel resting orders and mark the matching registry rows cancelled."""
    from py_clob_client_v2.clob_types import OrderPayload

    now_ms = int(time.time() * 1000)
    cancelled = 0
    for o in orders:
        v_id = o.get("order_id") or o.get("id")
        row_id = o.get("id")
        try:
            client.cancel_order(OrderPayload(orderID=v_id))
        except Exception as e:
            log.warning("cancel %s failed: %s", v_id, e)
            continue
        for row in registry.get_active_orders():
            if row.order_id == v_id or (row_id and row.id == row_id):
                registry.update_order_status(
                    row.id, status="cancelled", last_polled_ts=now_ms,
                    cancel_reason=o.get("cancel_reason"),
                    cancel_queue_ahead=o.get("cancel_queue_ahead"),
                )
                break
        cancelled += 1
    return cancelled


def _venue_resting_order_ids(client) -> Optional[set[str]]:
    """Venue order ids currently resting, or None when the venue cannot say.

    Returning None rather than an empty set on failure is the whole point: an
    unreachable venue must not read as "nothing is resting", which would let a
    replacement go out on top of a live order. Mirrors `venue.open_notional`,
    which refuses the same way for the MAX_TOTAL_USD cap.
    """
    try:
        orders = client.get_open_orders()
    except Exception as e:
        log.warning("get_open_orders failed: %s: %s", type(e).__name__, e)
        return None
    if orders is None:
        # Coercing a null response to an empty list would launder "the venue did
        # not answer" into "nothing is resting", and the caller submits a
        # replacement on that. Refuse, exactly as the except branch does.
        log.warning("get_open_orders returned None; treating as unknown")
        return None
    # Every id spelling the SDK has used goes into the set, not just the first
    # that matches. The set answers one question -- "is this id still resting?"
    # -- so an extra id can only ever produce a conservative abort, while a
    # missed one would wave a live order through as gone.
    out: set[str] = set()
    for o in orders:
        for key in ("id", "orderID", "orderId", "order_id"):
            oid = o.get(key) if isinstance(o, dict) else getattr(o, key, None)
            if oid:
                out.add(str(oid))
    return out


def _make_sweep_fn(funder: Optional[str], db_path: Path, registry):
    """Sweep the account and log a float mark, without failing the loop."""
    def sweep_fn() -> None:
        from core_brain.account import log_float_mark_if_measured
        from core_brain.order_manager import account_sweep
        if not funder:
            return
        mark = account_sweep(funder=funder, db_path=str(db_path), quiet=True)
        log_float_mark_if_measured(registry, mark)
    return sweep_fn


def _fleet_state(registry, cfg) -> dict:
    """The fleet-wide aggregates `decide_quotes` gates on, read once per cycle.

    `run` recomputes these before every rotation and merges them into the base
    config, so the fleet-level gates inside `decide_quotes` see live numbers
    instead of their 0.0 / NORMAL defaults. A failure raises to `run`, which
    keeps the previous cycle's values -- never resetting a live cap to open on
    a bad read.
    """
    from core_brain.unhedged_stop_loss import bot_risk_circuit_fleet_posture
    from core_brain.markout import fleet_stats
    from core_brain.order_registry import (
        registry_committed_usd, registry_naked_usd,
    )
    from core_brain.config import derive_dynamic_caps

    portfolio_usd = None
    if registry is not None:
        if hasattr(registry, "get_latest_account_mark"):
            try:
                am = registry.get_latest_account_mark()
                if am and am.get("account_value_usd") is not None and float(am["account_value_usd"]) > 0:
                    portfolio_usd = float(am["account_value_usd"])
                # Fallback to global latest when run-scoped mark is absent
                # (new run_id with no sweep yet — same fix as dashboard get_parameters)
                if portfolio_usd is None and hasattr(registry, "get_all_account_marks"):
                    try:
                        all_marks = registry.get_all_account_marks()
                        if all_marks:
                            for m in reversed(all_marks):
                                v = m.get("account_value_usd")
                                if v is not None and float(v) > 0:
                                    portfolio_usd = float(v)
                                    break
                    except Exception:
                        pass
            except Exception:
                pass
        if portfolio_usd is None and hasattr(registry, "get_all_float_marks"):
            try:
                fms = registry.get_all_float_marks()
                if fms:
                    from core_brain.order_registry import get_run_id
                    active_rid = get_run_id()
                    run_fms = [fm for fm in fms if (not fm.get("run_id") or fm.get("run_id") == active_rid)] if active_rid else fms
                    # Fallback to most recent global mark when filtered set is empty
                    if not run_fms:
                        run_fms = fms
                    if run_fms:
                        latest = run_fms[-1]
                        unrealized = latest.get("unrealized_usd")
                        if unrealized is not None:
                            val = float(cfg.bot_quote_bank_baseline_usd) + float(unrealized)
                            if val > 0:
                                portfolio_usd = val
            except Exception:
                pass

    dynamic_caps = derive_dynamic_caps(cfg, portfolio_usd)

    return {
        "bot_risk_exposure_fleet_naked_usd": registry_naked_usd(registry),
        "bot_risk_exposure_committed_usd": registry_committed_usd(registry),
        "bot_risk_circuit_fleet_posture": bot_risk_circuit_fleet_posture(
            fleet_stats(registry, cfg.bot_risk_markout_fleet_min_sample), cfg),
        **dynamic_caps,
    }


def start_resolution_sweeper(
    registry,
    db_path: Path,
    interval: float = 600.0,
    markets_fn: Optional[Callable] = None,
    suspects_fn: Optional[Callable[[], Any]] = None,
) -> Any:
    """Run the market-resolution sweep on a slow background cadence.

    Resolution detection (gamma reads + writes to the resolutions table) is
    deliberately kept OFF the trading critical path: it needs the network and
    touches nothing the quote/submit/reconcile loop depends on, so a slow or
    wedged gamma endpoint must never block a rotation. A daemon thread sleeps
    `interval` seconds between passes and dies with the fleet process.

    This is read-mostly at the venue and drops nothing a live loop needs: it
    marks externally-ended markets resolved (so the dashboard shows FINISHED),
    it does NOT cancel resting orders (the venue-authoritative
    `_cancel_dropped_markets` owns that), and it never books settlement PnL
    in live (redemption is on-chain).

    `suspects_fn` (#402) reads the rotation's suspect set -- markets whose
    visit ended on a book-fetch error, a settled book, or an elapsed
    countdown -- so the sweep confirms even markets the stale feed retains.
    """
    import threading

    def _loop() -> None:
        from core_brain.market_resolution import (
            DEFAULT_GAMMA_HOST, sweep_market_resolutions,
        )
        host = os.environ.get("GAMMA_HOST", DEFAULT_GAMMA_HOST)
        while True:
            try:
                universe = markets_fn() if markets_fn is not None else []
                try:
                    extra = set(suspects_fn() or ()) if suspects_fn else set()
                except Exception:
                    extra = set()
                for r in sweep_market_resolutions(
                    registry, db_path, markets=universe,
                    gamma_host=host, book_settlement=False,
                    cancel_resting=False, extra_candidates=extra,
                ):
                    if r.action in ("resolved_recorded", "partial_stranded"):
                        log.info("resolved %s (%s): %s winner=%s",
                                 r.condition_id[:12], r.reason, r.action,
                                 r.winning_token)
                    elif r.action in ("still_open", "unreachable"):
                        log.debug("resolve %s %s: %s",
                                  r.action, r.condition_id[:12], r.reason)
            except Exception as e:  # noqa: BLE001 - degrade, do not stop
                log.warning("resolution sweeper failed: %s: %s",
                            type(e).__name__, e)
            time.sleep(max(30.0, interval))

    thread = threading.Thread(
        daemon=True, name="market-resolution-sweeper", target=_loop)
    thread.start()
    return thread


def start_settlement_sweeper(
    registry,
    merge_fn: Callable[..., Any],
    interval: float = 60.0,
    live: bool = True,
    db_path: Optional[Path | str] = None,
    stop_event: Optional[Any] = None,
) -> Any:
    """Run the capital recycling settlement sweep on a relaxed background cadence (default 60s)."""
    import threading
    from core_brain.settlement_sweeper import execute_settlement_sweep

    evt = stop_event if stop_event is not None else threading.Event()

    def _loop() -> None:
        while not evt.is_set():
            try:
                execute_settlement_sweep(
                    registry=registry,
                    merge_fn=merge_fn,
                    db_path=db_path,
                    live=live,
                )
            except Exception as e:
                log.warning("settlement sweeper failed: %s: %s", type(e).__name__, e)
            evt.wait(max(10.0, interval))

    thread = threading.Thread(
        daemon=True, name="settlement-sweeper", target=_loop)
    thread.stop_event = evt
    thread.start()
    return thread


def start_risk_guardian(
    client: Any,
    registry: Any,
    cfg: Any,
    interval: float = 1.0,
    live: bool = True,
    single_buy_saver_fn: Optional[Callable] = None,
    stop_event: Optional[Any] = None,
    wake_event: Optional[Any] = None,
    trigger_board: Optional[Any] = None,
    emit_fn: Optional[Callable] = None,
    ring_path: Optional[Path | str] = None,
    funder: Optional[str] = None,
) -> Any:
    """Run the high-alert risk guardian sentry on a tight <=1.0s background cadence."""
    import threading
    from core_brain.risk_guardian import run_guardian_tick

    evt = stop_event if stop_event is not None else threading.Event()
    wait_evt = wake_event if wake_event is not None else evt
    db_path = getattr(registry, "db_path", getattr(registry, "_db_path", None))
    if not isinstance(db_path, (str, Path)):
        db_path = None

    def _loop() -> None:
        while not evt.is_set():
            if wake_event is not None:
                wake_event.clear()
            t0 = time.monotonic()
            if trigger_board is not None:
                for trig in trigger_board.pop_triggers():
                    triggered_at = float(trig.get("triggered_at", t0))
                    latency_ms = max(0.0, (t0 - triggered_at) * 1000.0)
                    extra = {
                        "condition_id": trig.get("condition_id"),
                        "token_id": trig.get("token_id"),
                        "kind": trig.get("kind"),
                        "source": trig.get("source"),
                        "level": trig.get("level"),
                        "price": trig.get("price"),
                        "latency_ms": round(latency_ms, 3),
                    }
                    try:
                        if emit_fn is not None:
                            emit_fn(
                                0,
                                "guardian",
                                "guardian_reactive_wake",
                                service="guardian",
                                reason=str(trig.get("kind") or ""),
                                latency_ms=latency_ms,
                                extra=extra,
                            )
                        else:
                            _emit_cycle_event(
                                0,
                                "guardian",
                                "guardian_reactive_wake",
                                service="guardian",
                                reason=str(trig.get("kind") or ""),
                                latency_ms=latency_ms,
                                extra=extra,
                                ring_path=Path(ring_path) if ring_path else None,
                                db_path=Path(db_path) if db_path else None,
                            )
                    except Exception as emit_exc:
                        log.warning(
                            "guardian_reactive_wake emit failed: %s: %s",
                            type(emit_exc).__name__, emit_exc,
                        )
            try:
                run_guardian_tick(
                    client=client,
                    registry=registry,
                    cfg=cfg,
                    live=live,
                    single_buy_saver_fn=single_buy_saver_fn,
                    trigger_board=trigger_board,
                    funder=funder,
                )
            except Exception as e:
                log.warning("risk guardian tick failed: %s: %s", type(e).__name__, e)
            if evt.is_set():
                break
            elapsed = time.monotonic() - t0
            wait_evt.wait(max(0.01, interval - elapsed))

    thread = threading.Thread(
        daemon=True, name="risk-guardian-sentry", target=_loop)
    thread.stop_event = evt
    thread.start()
    return thread


def _production_reconcile_fn(emit_fn: Any) -> Any:
    """Production reconcile wiring: report each new fill through `emit_fn`.

    Keeps the 3-argument reconcile port `(client, registry, maker_address)`
    unchanged; only adds the telemetry observer. Telemetry-only: execution
    and accounting are unchanged.
    """
    from core_brain.cycle_stream import make_fill_observer
    from core_brain.market_meta import resolve_market_meta
    from core_brain.order_registry import reconcile_orders

    observer = make_fill_observer(
        emit_fn, service="decide", phase="reconciling",
        meta_lookup=resolve_market_meta,
    )

    def _reconcile(client: Any, registry: Any, maker_address: Any) -> Any:
        return reconcile_orders(
            client, registry, maker_address=maker_address,
            on_fill_recorded=observer,
        )

    return _reconcile


BOOK_SOURCE_DEFAULT = "stream"


def _make_guardian_saver_fn(book_provider: Any,
                            funder: Optional[str] = None) -> Callable[..., Any]:
    """Guardian defense with stream-first decision reads (#659).

    The held-leg bid the lifecycle poll evaluates comes from the provider
    (WS books, REST fallback); exits and completions keep their own fresh
    venue reads inside `manage_single_leg_positions`. Without a provider
    the decision read stays on the client, exactly as before.

    `funder` is the venue view the exit sizing needs (#646 N5). It is what
    separates "this pair is already flat" from "this pair's close retired
    fills that left a remainder": without it a partial exit reached by the
    guardian is skipped outright and its naked leg keeps sitting there.
    """
    from core_brain.single_buy_saver import manage_single_leg_positions

    def _saver(client: Any, registry: Any, cfg: Any, live: bool = True) -> Any:
        kwargs: dict[str, Any] = {}
        if book_provider is not None:
            kwargs["decision_book_fn"] = book_provider.get_levels_book
        if funder:
            kwargs["funder"] = funder
        return manage_single_leg_positions(client, registry, cfg, live=live,
                                           **kwargs)

    return _saver


def _make_subscribe_fn(
    market_stream: Any,
    cache: Any,
    fetch_market_fn: Callable[[str], Any],
    *,
    trigger_board: Optional[Any] = None,
    timeout_sec: float = 5.0,
    poll_sec: float = 0.05,
    time_fn: Callable[[], float] = time.monotonic,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> Callable[[list], None]:
    """Return a `subscribe_fn(markets)` callable that registers universe tokens on `MarketStream`.

    Dedupes by condition ID so `fetch_market_fn` is only called once per market,
    calls `ensure_market` and `retain_markets`, and bounds the wait up to 5s
    for newly added tokens to show up in `cache.ready_tokens()`.
    """
    subscribed_cids: set[str] = set()

    def _subscribe(markets: list) -> None:
        current_cids = []
        new_tokens = []
        for m in markets:
            cid = getattr(m, "condition_id", None) or (
                m.get("cid") or m.get("condition_id") if isinstance(m, dict) else None
            )
            if not cid:
                continue
            cid_str = str(cid)
            current_cids.append(cid_str)
            if cid_str not in subscribed_cids:
                try:
                    resolved = fetch_market_fn(cid_str)
                    slug = (
                        getattr(resolved, "market_slug", "")
                        or getattr(m, "market_slug", "")
                        or (m.get("market_slug") if isinstance(m, dict) else "")
                        or ""
                    )
                    up_token = str(getattr(resolved, "up_token", ""))
                    down_token = str(getattr(resolved, "down_token", ""))
                    market_stream.ensure_market(cid_str, slug, up_token, down_token)
                    subscribed_cids.add(cid_str)
                    if up_token:
                        new_tokens.append(up_token)
                    if down_token:
                        new_tokens.append(down_token)
                except Exception as e:
                    log.warning(
                        "failed to resolve market for stream subscription %s: %s: %s",
                        cid_str, type(e).__name__, e,
                    )

        if trigger_board is not None:
            retained_cids = list(dict.fromkeys([*current_cids, *sorted(trigger_board.condition_ids())]))
        else:
            retained_cids = current_cids
        market_stream.retain_markets(retained_cids)
        subscribed_cids.intersection_update(retained_cids)

        if new_tokens:
            deadline = time_fn() + timeout_sec
            needed = set(new_tokens)
            while time_fn() < deadline:
                ready = cache.ready_tokens()
                if needed.issubset(ready):
                    break
                sleep_fn(poll_sec)

    return _subscribe


def _setup_user_stream(
    decide_emit: Callable,
    client: Any,
    registry: Any,
    maker: Optional[str],
    wake: Optional[threading.Event] = None,
) -> tuple[Optional[Any], Optional[Callable], Optional[Callable], threading.Event]:
    """Start `UserStreamClient` when credentials are present.

    Returns `(user_client, fill_events_fn, fill_reconcile_fn, wake_event)`.
    If credentials are missing, logs a message and returns `(None, None, None, wake_event)`.
    """
    import queue
    import threading
    from core_brain.user_stream import (
        UserStreamClient, UserStreamCredentialsMissing, get_user_stream_creds,
    )

    if wake is None:
        wake = threading.Event()

    try:
        get_user_stream_creds()
    except UserStreamCredentialsMissing:
        log.info("user stream disabled: credentials missing (fill-triggered reconcile off)")
        return None, None, None, wake

    user_client = UserStreamClient(emit_fn=decide_emit)
    fill_queue: queue.Queue = queue.Queue()

    user_client.on_trade(lambda ev: (fill_queue.put(ev), wake.set()))

    was_disconnected = False

    def _on_state(state: str, detail: str) -> None:
        nonlocal was_disconnected
        if state in ("DISCONNECTED", "RECONNECTING"):
            was_disconnected = True
        elif state == "AUTH_OK":
            if was_disconnected:
                wake.set()
                was_disconnected = False

    user_client.on_state(_on_state)
    user_client.start(None)

    def _drain_fills() -> list:
        events = []
        while True:
            try:
                events.append(fill_queue.get_nowait())
            except queue.Empty:
                break
        return events

    fill_reconcile = _production_reconcile_fn(decide_emit)
    fill_reconcile_fn = lambda: fill_reconcile(client, registry, maker)

    return user_client, _drain_fills, fill_reconcile_fn, wake


def _make_sleep_fn(wake: threading.Event) -> Callable[[float], None]:
    """Return a `sleep_fn(seconds)` that waits on `wake` and clears it afterwards."""
    def _sleep(seconds: float) -> None:
        wake.wait(max(0.0, seconds))
        wake.clear()

    return _sleep


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """The Trader loop's CLI, separated from `main` so the interval contract
    is testable without launching a loop (operator directive 2026-10-12).

    `--interval` defaults to `MAX_ROTATION_INTERVAL_SEC` (1.0s) and any
    larger request is clamped to the ceiling by `cap_rotation_interval`.
    Sub-second values (0.5, 0.25) pass through untouched.
    """
    from core_brain.config import MAX_ROTATION_INTERVAL_SEC, cap_rotation_interval

    ap = argparse.ArgumentParser(
        description="LIVE fleet: decide -> submit -> reconcile across graduated markets.")
    ap.add_argument("--live", action=argparse.BooleanOptionalAction, default=True,
                    help="send to venue (default: True). Use --no-live for dry-run.")
    ap.add_argument("--interval", type=float, default=MAX_ROTATION_INTERVAL_SEC,
                    help=f"rotation cadence in seconds (default: "
                         f"{MAX_ROTATION_INTERVAL_SEC}); the ceiling -- any larger "
                         f"value is clamped, smaller values are kept")
    ap.add_argument("--once", action="store_true",
                    help="run one rotation and exit")
    ap.add_argument("--db", default=None,
                    help="registry db path (default: data/orders.db)")
    ap.add_argument("--max-markets", type=int, default=None,
                    help="cap the number of markets rotated (default: all)")
    ap.add_argument("--funder", default=None,
                    help="funder address (default: POLY_FUNDER)")
    ap.add_argument("--no-reconcile", action="store_true",
                    help="skip the reconcile pass (the poll loop owns it when "
                          "running alongside this fleet)")
    ap.add_argument("--no-sweep", action="store_true",
                    help="skip the account sweep (the poll loop owns it when "
                          "running alongside this fleet)")
    ap.add_argument("--resolution-interval", type=float, default=600.0,
                    help="seconds between market-resolution sweeps on a "
                         "background, non-blocking thread (default: 600)")
    ap.add_argument("--ws-books", action=argparse.BooleanOptionalAction, default=None,
                    help="read order books from the WebSocket market stream instead of "
                         "REST (default: True in loop mode, False with --once)")
    ap.add_argument("--book-source", choices=("stream", "rest"),
                    default=BOOK_SOURCE_DEFAULT,
                    help="book source with REST fallback (default: stream). "
                         "--book-source rest is the one-flag undo: today's "
                         "REST-only behavior, no socket.")
    a = ap.parse_args(argv)
    # The ceiling is enforced HERE, at the CLI boundary, so the loop body
    # never sees a cadence above 1s -- not silently clamped mid-flight
    # where the heartbeat would report one number and the loop run another.
    a.interval = cap_rotation_interval(a.interval)
    return a


def main(argv: Optional[list[str]] = None) -> int:
    """The Trader loop entry point.

    LIVE by default: real orders are placed unless `--no-live` is passed.
    `--once` runs a single rotation (the smoke-test path); without it the loop
    runs until interrupted.
    """
    from dotenv import load_dotenv
    load_dotenv()

    from core_brain.config import load
    from core_brain.markets import full_book, recent_sell_flow
    from core_brain.order_registry import DEFAULT_DB_PATH, OrderRegistry
    from core_brain.quotes import decide_quotes

    a = _parse_args(argv)

    use_ws_books = a.ws_books if a.ws_books is not None else not a.once
    if a.book_source == "rest":
        use_ws_books = False
    books_mode = "stream" if use_ws_books else "rest"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stderr)],
    )
    # Suppress verbose HTTP network request noise from CLOB SDK
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    cfg = load()
    try:
        from core_brain.account import fetch_live_balance
        live_bal = fetch_live_balance(a.funder)
        if live_bal is not None and live_bal > 0:
            cfg = replace(cfg, bot_quote_bank_baseline_usd=live_bal)
    except Exception as e:
        log.warning("live balance read failed, using config bankroll: %s", e)

    specs = _market_specs(a.max_markets)
    if not specs:
        log.warning("no graduated markets in runtime/markets.json; "
                    "run scripts/rank_markets.py first -- idling")
    else:
        log.info("rotating %d markets (live=%s interval=%ss once=%s books=%s)",
                 len(specs), a.live, a.interval, a.once, books_mode)

    db_path = Path(a.db) if a.db else DEFAULT_DB_PATH
    registry = OrderRegistry(db_path=db_path)
    maker = a.funder or os.environ.get("POLY_FUNDER")

    if a.live:
        from core_brain.venue import client
        client = client(a.funder)
    elif os.environ.get("POLY_PRIVATE_KEY") or os.environ.get("POLY_KEY"):
        # Dry-run with credentials: reconcile and sweep (read-only) can run.
        from core_brain.venue import client
        client = client(a.funder)
    else:
        log.info("no credentials in env: dry-run will skip reconcile/sweep "
                 "(they need auth) and only show decide/plan outcomes")
        client = object()

    decide_emit = partial(_emit_cycle_event, db_path=db_path)

    market_stream = None
    book_provider = None
    book_stats: dict = {}
    guardian_wake = None
    trigger_board = None
    if use_ws_books:
        from core_brain.book_provider import BookProvider
        from core_brain.market_stream import MarketBookCache, MarketStream
        if not a.once:
            from core_brain.orphan_trigger import OrphanTriggerBoard
            guardian_wake = threading.Event()
            trigger_board = OrphanTriggerBoard(guardian_wake)
        cache = MarketBookCache()
        market_stream = MarketStream(
            cache=cache,
            emit_fn=decide_emit,
            price_listener=trigger_board.observe if trigger_board is not None else None,
        )
        market_stream.start()
        # Stream first, REST fallback (#659): a cold or silent feed reads
        # from REST instead of holding every quote. The provider owns the
        # stream, so `stop()` below joins both threads.
        book_provider = BookProvider(
            stream=market_stream,
            rest_reader=full_book,
            clob_host=os.environ.get("CLOB_HOST", "https://clob.polymarket.com"),
            emit_fn=decide_emit,
            owns_stream=True,
            stats_box=book_stats,
        )
        fetch_books_fn = book_provider.fetch_books
        subscribe_fn = _make_subscribe_fn(
            market_stream, cache, _fetch_market, trigger_board=trigger_board,
        )
        book_stats_fn = lambda: dict(book_stats)
    else:
        fetch_books_fn = full_book
        subscribe_fn = None
        book_stats_fn = None

    user_stream = None
    fill_events_fn = None
    fill_reconcile_fn = None
    wake = threading.Event()

    if not a.once:
        user_stream, fill_events_fn, fill_reconcile_fn, wake = _setup_user_stream(
            decide_emit=decide_emit,
            client=client,
            registry=registry,
            maker=maker,
            wake=wake,
        )

    seam = VenueSeam(
        client=client,
        registry=registry,
        base_cfg=cfg,
        maker_address=maker,
        clob_host=os.environ.get("CLOB_HOST", "https://clob.polymarket.com"),
        fetch_market=_fetch_market,
        fetch_books=fetch_books_fn,
        decide=decide_quotes,
        book_provider=book_provider,
        submit_fn=_submit_intents,
        cancel_fn=_cancel_orders,
        reconcile_fn=(
            (lambda c, r, m: None) if a.no_reconcile
            else _production_reconcile_fn(decide_emit)
        ),
        sweep_fn=(
            (lambda: None) if a.no_sweep
            else _make_sweep_fn(maker, db_path, registry)
        ),
        inventory_fn=_make_inventory_fn(registry, db_path),
        open_orders_fn=_make_open_orders_fn(registry),
        fleet_state_fn=lambda r: _fleet_state(r, cfg),
        resting_order_ids_fn=_venue_resting_order_ids,
        emit_fn=decide_emit,
        # The live fleet measures reachable tape flow for the queue-clear gate.
        # The shadow seam deliberately does NOT wire this: its client is a
        # deny-by-default proxy, so a blocked tape read would come back as an
        # empty tape, and an unmeasurable reading is reported rather than
        # refused -- a rehearsal of this gate would show nothing either way.
        flow_fn=lambda cid, window: recent_sell_flow(cid, window),
        # The UMA re-check gate (#408): one cached reader per run, beside the
        # live cancel adapter above; the visit check runs before fetch_market.
        fetch_uma_status=make_uma_status_reader(
            os.environ.get("GAMMA_HOST", "https://gamma-api.polymarket.com")),
        record_market_event=registry.log_market_event,
        subscribe_fn=subscribe_fn,
        book_stats_fn=book_stats_fn,
        fill_events_fn=fill_events_fn,
        fill_reconcile_fn=fill_reconcile_fn,
    )
    # Resolution detection runs off the critical path on a slow background
    # thread (won't block a 5s rotation). Disabled for --once smoke runs.
    # The suspect box is filled by `run` every rotation; the thread reads it
    # through the closure, replacing (never mutating) the set, so no lock is
    # needed between the loop and the reader.
    suspects_box: dict = {}
    if not a.once:
        start_resolution_sweeper(
            registry, db_path, interval=a.resolution_interval,
            markets_fn=lambda: _market_specs(a.max_markets, registry=registry),
            suspects_fn=lambda: suspects_box.get("cids", frozenset()),
        )
        log.info("resolution sweeper started (every %ss, background)",
                 a.resolution_interval)

    try:
        results = run(
            seam,
            interval=a.interval, once=a.once, live=a.live, markets=specs,
            markets_fn=lambda: _market_specs(a.max_markets, registry=registry),
            sleep_fn=_make_sleep_fn(wake),
            suspects_box=suspects_box,
            spawn_guardian=not a.once,
            guardian_wake=guardian_wake,
            trigger_board=trigger_board,
        )
    except InstanceInUse as exc:
        print(f"fleet refused: {exc}", file=sys.stderr)
        return 2
    finally:
        # The provider owns the stream whenever one is up, so stopping it
        # joins both threads; `market_stream` is never set without one.
        if book_provider is not None:
            try:
                book_provider.stop()
            except Exception as e:
                log.warning("market stream stop failed: %s", e)
        if user_stream is not None:
            try:
                user_stream.stop()
            except Exception as e:
                log.warning("user stream stop failed: %s", e)
    return 0 if results else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
