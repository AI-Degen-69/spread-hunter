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
import os
import sys
import time
import uuid
from contextlib import nullcontext
from dataclasses import dataclass, field, replace
from enum import Enum
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:  # annotation only -- markets is imported lazily at the call site
    from core_brain.markets import SellFlow

from core_brain.quotes import Inventory, QuoteIntent, evaluate_market_quote
from core_brain.cycle_stream import emit as _emit_cycle_event
from core_brain.market_lifecycle import (
    LifecycleStop, classify_refusal, resolved_condition_ids,
)
from core_brain.order_registry import InstanceInUse, OrderRegistry

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
    #: True when this visit held resting orders through a transient refusal
    #: (#390). `run` reads it to maintain the per-market grace counter.
    held: bool = False
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
CANCEL_REGATE_PAIR_COST = "regate_pair_cost"  # holding would break max_pair_cost
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
    """What one market visit decided, beyond the intents list.

    Empty intents currently mean two different things -- the market was
    visited but `decide` refused (transient book flicker: hold), or the
    market is gone/settled (cancel now). The enum names which one so
    `plan_orders` never has to guess from `intents == []`.
    """
    QUOTED = "quoted"                    # intents present (or no opinion)
    REFUSED_TRANSIENT = "refused_hold"   # visited, refused: hold resting
    REFUSED_TERMINAL = "refused_cancel"  # visited, refused for good: cancel


# Consecutive visited-but-refused cycles a market's resting orders survive
# before the hold expires and they cancel via `not_quoted` (#390). A market
# that never comes back is dead, not flickering. Named, not magic.
REFUSED_HOLD_GRACE_CYCLES = 3


def _classify_refusal(why: str) -> VisitOutcome:
    """Terminal or transient, from `decide`'s refusal reason (pure).

    Delegates to the single enumerated list in `market_lifecycle`: a named
    stop refuses for good, everything else (wide book, completable cap,
    reward window, mid band, ...) is transient -- the book moved, not the
    market. Matched case-insensitively.
    """
    if classify_refusal(why) is not None:
        return VisitOutcome.REFUSED_TERMINAL
    return VisitOutcome.REFUSED_TRANSIENT


def _visit_stop(why: str, grace_expired: bool) -> Optional[LifecycleStop]:
    """Name the lifecycle stop for a refused visit (pure).

    A transient refusal that outlasted the hold grace expires into
    HOLD_EXPIRED -- today that path cancels without a named reason (#402).
    """
    if grace_expired:
        return LifecycleStop.HOLD_EXPIRED
    return classify_refusal(why or "")


def _attach_series_state(market: Any, spec: Any) -> Any:
    """Attach the feed row's series evidence to a fetched market (#402).

    The CLOB read carries no series fields; the ranker's row does. Attached
    ONLY when the evidence proves a live series with games remaining -- every
    other market keeps no state, so custom `decide` ports (which predate the
    `series_state` keyword) are called exactly as before. A frozen dataclass
    market (LiveMarket) is rebuilt; any other shape is stamped in place.
    Unparseable evidence attaches nothing and fails closed downstream --
    never raises.
    """
    from core_brain.markets import live_series_with_games_remaining, parse_series_state
    get = (lambda k: spec.get(k)) if isinstance(spec, dict) else (
        lambda k: getattr(spec, k, None))
    try:
        state = parse_series_state(
            sports_market_type=get("sports_market_type"),
            score=get("event_score"),
            period=get("event_period"),
            question=get("question") or get("title"),
            live=bool(get("event_live")),
            ended=bool(get("event_ended")),
            evidence_ts=get("series_ts"),
        )
    except Exception:
        return market
    if not live_series_with_games_remaining(state):
        return market
    try:
        return replace(market, series_state=state)
    except Exception:
        pass
    try:
        market.series_state = state
    except Exception:
        pass
    return market


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

    RETIRED BY #387 -- THE RE-GATE (historical behavior; not active). A kept
    from the price `risk.hard_block` approved. Left unchecked, the band is a
    hole through that gate: a 3c-stale bid in a moving market can carry a
    completable cost 3c worse than anything the gate ever allowed. So a kept
    order is re-tested against `risk.completable_pair_block` at its own price
    and cancelled when it no longer passes. `cfg` and `hedge_asks` (token id ->
    the OTHER token's best ask) are what that test needs; without both, the
    re-gate stands down and the band behaves as a plain tolerance.

    `hedge_held` names the tokens whose OPPOSITE leg we already own, and the
    re-gate skips them -- exactly as `quotes._decide_quotes_from_mid` skips the
    gate when `inv.avg(other) > 0`. Completion is not needed there, so the
    hedge ASK is not the price that finishes the pair, and gating on it
    cancels a valid hedge. Concretely: a kept UP bid at 0.54 against a DOWN leg
    held at 0.43 average is a 0.97 pair, but a DOWN ask of 0.50 reads as 1.04
    and would cancel and resubmit that bid every single cycle -- the churn this
    whole change exists to stop, while the naked DOWN leg stays open longer for
    it. This function has no inventory of its own, so it has to be told.

    A re-gated cancel drops the order out of the kept set, so this cycle's
    intent for that token IS submitted in its place. Cancelling without
    replacing would leave the market dark for a cycle on a price that is
    still quotable.

    `reasons` is an optional out-parameter: pass a dict and it comes back keyed
    by order id with one of the CANCEL_* constants. It exists because a cancel
    with no recorded reason cannot be tuned -- see #131.

    RETIRED BY #387 -- THE QUEUE HOLD (historical behavior; not active).
    order at its own price, and `hold_queue_shares` is the front-of-queue
    threshold. An order that is inside the threshold is KEPT through a price
    move it would otherwise be re-quoted for, because under price-time priority
    a cancel sends it to the back of a new level and the queue is what produces
    fills here: on shadow-02 the best position of the run, 25 shares from the
    front, was re-quoted away six seconds after it was posted.

    The hold is deliberately narrow. It never applies when:

      * the token is no longer quoted at all -- there is nothing to hold for;
      * the re-gate fails -- holding would carry a completable pair over
        `max_pair_cost`, and queue position is not worth a booked loss;
      * the re-gate is not ARMED (`cfg` or `hedge_asks` absent) -- with nothing
        checking the economics of a stale price, holding is an unmeasured bet.

    `hold_queue_shares` ships at 0.0, which disables the hold entirely. The
    reasons above are recorded either way, and choosing the threshold is what
    that record is for.

    RETIRED BY #387 -- THE DIRECTION HOLD (historical behavior; not active).
    band are not the same event, and re-quoting on both of them cancels the
    order at the only moment it was ever going to fill:

      * the target RISES -- the book walked away and our bid is stranded under
        the market. It will not be reached. Re-quote.
      * the target FALLS -- the book is walking down ONTO our bid. A limit BUY
        at 0.47 fills at 0.47 when a seller sweeps through it; that is the
        entire fill mechanism on this venue. Cancelling here hands back the
        queue position and the fill, and the replacement rests lower down where
        the same thing happens again.

    `hold_below_target` caps how far the target may fall below a held bid,
    because a LARGE drop is the market leaving rather than arriving: a bid held
    36c above the book is an adverse fill, not a queue position. Measured on
    shadow-01 across 2026-09-15 17:06-21:00, 393 of 755 `price_moved` cancels
    (52%) fired while the best bid was falling toward the order, median order
    lifetime 42s, and the run booked zero fills over 1,157 orders. Of those 393,
    132 (34%) still rested 1c-5c ABOVE the new best bid -- close enough that the
    next seller through reaches them. The rest ran out to a p90 of 36c, which is
    the market leaving.

    The direction hold takes the SAME standby conditions as the queue hold --
    it never overrides the pair-cost re-gate, and it refuses to act on an
    unarmed one. The two are independent: an order far back in the queue is
    still held on a downward move, because a limit order fills at its own price
    whoever is resting in front of it.
    """
    tolerance = max(float(price_eps), float(dead_band))

    wanted: dict[str, list[QuoteIntent]] = {}
    for i in intents:
        wanted.setdefault(i.token_id, []).append(i)

    def _record(order: dict, reason: str) -> None:
        if reasons is not None:
            key = order.get("id") or order.get("order_id")
            if key is not None:
                reasons[str(key)] = reason

    def _near_front(order: dict) -> bool:
        """Is this order close enough to the front to be worth holding?"""
        if not hold_queue_shares or hold_queue_shares <= 0 or not queue_ahead:
            return False
        key = order.get("id") or order.get("order_id")
        ahead = queue_ahead.get(str(key)) if key is not None else None
        if ahead is None:
            # An unknown position is not a good one. Holding on a queue we
            # never measured is the same guess the hold rule exists to replace.
            return False
        return float(ahead) <= float(hold_queue_shares)

    def _market_arriving(order: dict, targets: list[QuoteIntent]) -> bool:
        """Is the book walking DOWN onto this bid, and still close to it?

        Compared against the HIGHEST target for the token: that is the price
        this cycle would rest at, so it is the one that says whether the book
        has come to the order or left it behind.
        """
        if not hold_below_target or hold_below_target <= 0:
            return False
        drop = round(float(order["price"]) - max(float(i.price) for i in targets), 4)
        return 0.0 < drop <= round(float(hold_below_target), 4)

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
        targets = wanted.get(tok)
        if not targets:
            if visit_outcome is VisitOutcome.REFUSED_TRANSIENT:
                # HOLD-ON-REFUSAL (#390): the market was visited but decide
                # refused this cycle (book flicker, not abandonment). The
                # resting order stays: no cancel, and with no intents there
                # is nothing to submit either.
                continue
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
        if i.token_id in held_tokens:
            # We chose to keep the resting order on this token. Posting the new
            # price as well would leave both working.
            continue
        sits = kept.get(i.token_id, [])
        if not any(abs(o["price"] - i.price) <= tolerance for o in sits):
            to_submit.append(i)

    return to_cancel, to_submit


def _cid(spec: Any) -> str:
    """The condition id from a market spec (dict or object)."""
    if isinstance(spec, dict):
        return str(spec.get("cid", ""))
    return str(getattr(spec, "cid", None) or getattr(spec, "condition_id", ""))


def _market_cfg(base, spec: Any):
    """Per-market config carried through one rotation of the Trader.

    `decide_quotes` runs the same from-mid pricing path and the same live risk
    gates the paper fleet runs; the only per-market differences are the venue's
    min-size, spread window and tick, copied from the ranker's spec.

    `objective="rewards"` below is pinned on every market ON PURPOSE, and the
    string does NOT mean the bot is farming rebates. It selects
    `quotes._decide_quotes_from_mid` -- rest both legs at `mid - offset`, which
    assembles the pair at ~1.00 - 2*offset and is how spread capture is executed.
    The alternative value, "pair", prices off the ask and was measured dead
    (pair cost 1.00 + spread by construction); pinning here keeps a spec that
    omits the field, or carries a stale one, from routing a live market onto it.
    The ranker's own `source` field ("spread" vs "rewards") describes how a market
    is FUNDED, not how it is quoted, and is deliberately not read here.
    """
    if not isinstance(spec, dict):
        if hasattr(spec, "range_cents") or hasattr(spec, "velocity_measured_at"):
            return replace(
                base,
                range_cents=getattr(spec, "range_cents", None),
                velocity_measured_at=getattr(spec, "velocity_measured_at", None),
            )
        return base
    return replace(
        base,
        objective="spread_capture",
        min_quote_shares=int(spec.get("min_size", base.min_quote_shares)),
        quote_shares=int(spec.get("shares", base.quote_shares)),
        max_spread_from_mid=float(
            spec.get("max_spread", base.max_spread_from_mid * 100.0)
        ) / 100.0,
        price_tick=float(spec.get("tick", base.price_tick)),
        min_t_remaining_sec=0.0,
        market_title=str(spec.get("title", "")),
        market_daily_rate=float(spec.get("daily", 0.0)),
        range_cents=(
            float(spec["range_cents"])
            if spec.get("range_cents") is not None
            else getattr(base, "range_cents", None)
        ),
        velocity_measured_at=(
            float(spec["velocity_measured_at"])
            if spec.get("velocity_measured_at") is not None
            else getattr(base, "velocity_measured_at", None)
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
    #: filter finding nothing is a finding, while a ladder trial asking for a
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
    # Consecutive refused-hold cycles per market (#390). A quoted cycle, a
    # terminal cancel, or an error resets the count; a restart resets it too
    # (safe direction: a fresh loop holds, never wipes).
    refused_streaks: dict[str, int] = {}
    # Last lifecycle-stop code per market for on-change-only rows (#402). A
    # quote clears the entry, so the next stop is news again.
    stop_memory: dict[str, str] = {}
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
    with lock_cm as holder:
        while True:
            cycle += 1
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

            cycle_results: list[LiveFleetResult] = []
            rotation_suspects: set[str] = set()
            # Resolved markets are never visited again (#402): one durable
            # read per rotation, before any market-data or book fetch. The
            # guard reads the in-memory universe, so a stale feed retaining
            # the market, a reappearing feed, and a restart all stay quiet.
            resolved = resolved_condition_ids(registry)
            for spec in list(current_markets or []):
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
                res = _visit_one(
                    seam=seam, spec=spec, live=live, cycle=cycle,
                    emit_fn=emit_fn,
                    refused_streak=refused_streaks.get(cid, 0),
                    stop_memory=stop_memory,
                )
                if res.status == "ERROR" and "book fetch error" in (res.error or ""):
                    rotation_suspects.add(cid)
                elif classify_refusal(res.why or "") in (
                        LifecycleStop.SETTLED_BOOK,
                        LifecycleStop.COUNTDOWN_EXPIRED):
                    rotation_suspects.add(cid)
                refused_streaks[cid] = (
                    refused_streaks.get(cid, 0) + 1 if res.held else 0
                )
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
                sleep_fn(max(0.0, interval))
            except KeyboardInterrupt as e:
                # The shadow deadline sleep subclasses KeyboardInterrupt to
                # end a time-boxed rehearsal. Matched by class NAME so this
                # module never imports the shadow runner.
                shutdown_reason = ("deadline"
                                   if type(e).__name__ == "_Deadline"
                                   else "interrupt")
                break

    _finish_shutdown()
    return once_results if once else last_cycle


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

    open_cids = sorted({o.condition_id for o in dropped
                        if getattr(o, "status", "") == "open"})
    for dropped_cid in open_cids:
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


def _admit_placements(
    to_submit: list[QuoteIntent],
    market: Any,
    up_book: dict,
    down_book: dict,
    flow_fn: Optional[Callable[[str, float], Any]],
    cfg: Any,
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
    reported; under the shipped record-only default (`enforce_queue_clear_gate`
    False) it carries the reason and `admitted` is unchanged.
    """
    from core_brain import risk

    if not to_submit or flow_fn is None:
        return list(to_submit), ""
    passive = [i for i in to_submit if not i.crossed]
    if not passive:
        return list(to_submit), ""

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
    window_sec = float(getattr(cfg, "queue_flow_window_sec", 0.0) or 0.0)
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
        # Record-only (`enforce_queue_clear_gate` False): the bar is on the
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


def _visit_one(
    seam: VenueSeam,
    spec,
    live: bool,
    cycle: int = 0,
    emit_fn: Optional[Callable] = None,
    plan_fn: Optional[Callable] = None,
    refused_streak: int = 0,
    stop_memory: Optional[dict] = None,
) -> LiveFleetResult:
    """One poll of one market: fetch -> decide -> plan -> submit/cancel.

    `refused_streak` is this market's consecutive refused-hold count coming
    into the cycle (owned by `run`). When the streak reaches
    `REFUSED_HOLD_GRACE_CYCLES`, a transient refusal expires into a cancel:
    a market that never comes back is dead, not flickering (#390).
    `stop_memory` is the loop's condition-id -> last-stop-code map for
    on-change-only `lifecycle_stop` rows; None behaves as an empty map.
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

    # Series evidence rides the feed row, not the CLOB read: attach it now so
    # `decide` (via `evaluate_market_quote`) can exempt a live series with
    # games left from the mid band (#402).
    market = _attach_series_state(market, spec)

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
        ev = evaluate_market_quote(
            cid, cfg, seam.clob_host,
            # The market is already fetched (the block above exists so a fetch
            # failure degrades to ERROR with no title); hand it to the shared
            # step, which owns the books -> inventory -> decide sequence.
            fetch_market=lambda c: market,
            fetch_books=seam.fetch_books,
            inventory_for=seam.inventory_fn or (lambda m: Inventory()),
            decide=seam.decide,
        )
        intents, why = ev.intents, ev.why
        open_orders = seam.open_orders_fn(market) if seam.open_orders_fn else []
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
        visit_outcome = (
            _classify_refusal(why) if not intents else VisitOutcome.QUOTED
        )
        grace_expired = (
            visit_outcome is VisitOutcome.REFUSED_TRANSIENT
            and refused_streak + 1 >= REFUSED_HOLD_GRACE_CYCLES
        )
        if grace_expired:
            # GRACE EXPIRED (#390): held through enough refused cycles with
            # no quotable one between. Cancel via the terminal path below.
            visit_outcome = VisitOutcome.REFUSED_TERMINAL
        if intents:
            # A quote in between re-arms stop rows: the next stop is news.
            if stop_memory is not None:
                stop_memory.pop(cid, None)
        else:
            _note_lifecycle_stop(
                seam, cid, title, _visit_stop(why, grace_expired),
                why, stop_memory, cycle, emit_fn)
        to_cancel, to_submit = (plan_fn or plan_orders)(
            open_orders, intents,
            dead_band=float(getattr(cfg, "requote_dead_band", 0.0)),
            cfg=cfg, hedge_asks=hedge_asks, hedge_held=hedge_held,
            reasons=cancel_reasons, queue_ahead=queue_ahead,
            hold_queue_shares=float(getattr(cfg, "requote_hold_queue_shares", 0.0)),
            hold_below_target=float(getattr(cfg, "requote_hold_below_target", 0.0)),
            visit_outcome=visit_outcome,
        )
        # THE QUEUE-CLEAR GATE (#393), on NEW placements only and after the
        # planner, so held orders, the refusal-grace counter and every recorded
        # cancel reason stay exactly as they were. Record-only at ship: it
        # normally reports and changes nothing.
        to_submit, queue_why = _admit_placements(
            to_submit, market, ev.up_book, ev.down_book, seam.flow_fn, cfg)
    except Exception as e:
        emit_fn(service="decide", cycle=cycle, phase="quoting",
                action="market_error", market_slug=title,
                reason=f"{type(e).__name__}: {e}")
        return LiveFleetResult(status="ERROR", condition_id=cid, title=title,
                               error=f"{type(e).__name__}: {e}")

    event_extra = {"intent_count": len(intents), "condition_id": cid}
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
            log.info("[DRY RUN QUOTE] %s | Planned %d orders: %s", title, len(intents), orders_desc)
        elif why:
            log.info("[DRY RUN SKIPPED] %s | %s", title, why)
        else:
            log.info("[DRY RUN IDLE] %s | No quote action needed", title)
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

    submitted = cancelled = 0
    try:
        # Cancel first: cancelling old quotes before submitting replacements
        # prevents exceeding MAX_TOTAL_USD notional exposure and avoids double
        # quoting if replacement submission occurs while stale orders rest.
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
        if to_submit:
            submitted = seam.submit_fn(seam.client, seam.registry, market, to_submit, cfg)
    except Exception as e:
        submitted = getattr(e, PARTIAL_SUBMIT_PLACED_ATTR, getattr(e, "placed", submitted))
        # A submit/cancel failure (venue rejection, a split couple rolled back)
        # must degrade this market to ERROR, never stop the rotation.
        emit_fn(service="decide", cycle=cycle, phase="quoting",
                action="market_error", market_slug=title,
                reason=f"submit/cancel: {type(e).__name__}: {e}",
                extra={"submitted": submitted, "cancelled": cancelled})
        return LiveFleetResult(
            status="ERROR", condition_id=cid, title=title, why=why,
            intents=list(intents), submitted=submitted, cancelled=cancelled,
            error=f"submit/cancel: {type(e).__name__}: {e}")

    submit_extra = {"submitted": submitted, "cancelled": cancelled}
    if queue_why:
        submit_extra["queue_why"] = queue_why
    emit_fn(service="decide", cycle=cycle, phase="quoting", action="submit",
            market_slug=title, extra=submit_extra)

    held = (
        visit_outcome is VisitOutcome.REFUSED_TRANSIENT
        and not to_cancel and not to_submit and bool(open_orders)
    )
    if queue_why:
        log.info("[QUEUE] %s | %s", title, queue_why)
    if submitted > 0:
        orders_desc = ", ".join(f"{i.side} {i.size}sh @ ${i.price:.3f}" for i in to_submit)
        log.info("[QUOTING] %s | Posted %d orders: %s", title, submitted, orders_desc)
    elif cancelled > 0:
        log.info("[REQUOTE] %s | Cancelled %d stale order(s)", title, cancelled)
    elif held:
        log.info("[HOLDING] %s | %d quote(s) held through transient refusal: %s",
                 title, len(open_orders), why)
    elif open_orders:
        log.info("[RESTING] %s | %d quote(s) resting at target spread", title, len(open_orders))
    elif why:
        log.info("[SKIPPED] %s | %s", title, why)

    return LiveFleetResult(
        status="QUOTED" if intents else "DECLINED",
        condition_id=cid, title=title, why=why, intents=list(intents),
        submitted=submitted, cancelled=cancelled, held=held,
        queue_why=queue_why)


# --- production wiring ------------------------------------------------------

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
    if paired_arm is None:
        gms = load_graduated_markets(path=path)
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
            # Series evidence for the live-series exemption (#402). Absent
            # on old feeds: the parser fails closed without it.
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
    return specs


def _fetch_market(cid: str):
    """Resolve one market on the venue, raising so the loop records ERROR."""
    from core_brain.markets import fetch_pinned_market
    m = fetch_pinned_market(cid, require_rewards=False)
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
        MAX_ORDER_USD, MAX_TOTAL_USD, open_notional, venue_order_id,
    )
    from core_brain.order_registry import OrderRecord, QuoteRecord, get_run_id

    if not intents:
        return 0

    # Dynamic caps scale with live account value (AGENTS.md). When the trader
    # loop has already merged the latest venue account_value into base_cfg via
    # _fleet_state, use those; otherwise fall back to the static venue constants.
    max_order_usd = float(getattr(cfg, "max_order_usd", MAX_ORDER_USD)) if cfg is not None else MAX_ORDER_USD
    max_total_usd = float(getattr(cfg, "max_total_usd", MAX_TOTAL_USD)) if cfg is not None else MAX_TOTAL_USD

    total_cost = sum(i.price * i.size for i in intents)
    for i in intents:
        if i.price * i.size > max_order_usd:
            raise RuntimeError(
                f"leg {i.side} ${i.price * i.size:.2f} exceeds "
                f"MAX_ORDER_USD ${max_order_usd:.2f}")
    already = open_notional(client)
    if already is None:
        raise RuntimeError(
            "Cannot check MAX_TOTAL_USD cap: venue open_orders unreachable")
    if already + total_cost > max_total_usd:
        raise RuntimeError(
            f"open ${already:.2f} + ${total_cost:.2f} exceeds "
            f"MAX_TOTAL_USD ${max_total_usd:.2f}")

    now_ms = int(time.time() * 1000)
    pair_id = f"pair-{uuid.uuid4().hex[:12]}"
    max_pair_cost = getattr(cfg, "max_pair_cost", 0.995)

    # A carried pair_id (stamped on the intents by `plan_orders`) means this
    # batch REPLACES one leg of an existing pair whose complement still rests:
    # the replacement must join that pair, not open a new one, or the market
    # ends up with two one-legged pairs -- the detachment seen live (#206).
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
                    pair_id=pair_id, max_pair_cost_at_post=max_pair_cost,
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
    from core_brain.unhedged_stop_loss import fleet_posture
    from core_brain.markout import fleet_stats
    from core_brain.order_registry import (
        registry_committed_usd, registry_cycle_cadence_sec, registry_naked_usd,
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
                            val = float(cfg.bankroll_usd) + float(unrealized)
                            if val > 0:
                                portfolio_usd = val
            except Exception:
                pass

    dynamic_caps = derive_dynamic_caps(cfg, portfolio_usd)

    return {
        "fleet_naked_usd": registry_naked_usd(registry),
        "committed_usd": registry_committed_usd(registry),
        "fleet_posture": fleet_posture(
            fleet_stats(registry, cfg.markout_fleet_min_sample), cfg),
        # Feeds the endgame gate (#240). None when unmeasurable; the gate
        # fails open on None.
        "observed_cadence_sec": registry_cycle_cadence_sec(registry),
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
    from core_brain.order_registry import reconcile_orders
    from core_brain.quotes import decide_quotes

    ap = argparse.ArgumentParser(
        description="LIVE fleet: decide -> submit -> reconcile across graduated markets.")
    ap.add_argument("--live", action=argparse.BooleanOptionalAction, default=True,
                    help="send to venue (default: True). Use --no-live for dry-run.")
    ap.add_argument("--interval", type=float, default=5.0,
                    help="rotation cadence in seconds (default: 5.0)")
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
    a = ap.parse_args(argv)

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
            cfg = replace(cfg, bankroll_usd=live_bal)
    except Exception as e:
        log.warning("live balance read failed, using config bankroll: %s", e)

    specs = _market_specs(a.max_markets)
    if not specs:
        log.warning("no graduated markets in runtime/markets.json; "
                    "run scripts/rank_markets.py first -- idling")
    else:
        log.info("rotating %d markets (live=%s interval=%ss once=%s)",
                 len(specs), a.live, a.interval, a.once)

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

    seam = VenueSeam(
        client=client,
        registry=registry,
        base_cfg=cfg,
        maker_address=maker,
        clob_host=os.environ.get("CLOB_HOST", "https://clob.polymarket.com"),
        fetch_market=_fetch_market,
        fetch_books=full_book,
        decide=decide_quotes,
        submit_fn=_submit_intents,
        cancel_fn=_cancel_orders,
        reconcile_fn=(
            (lambda c, r, m: None) if a.no_reconcile
            else (lambda c, r, m: reconcile_orders(c, r, maker_address=m))
        ),
        sweep_fn=(
            (lambda: None) if a.no_sweep
            else _make_sweep_fn(maker, db_path, registry)
        ),
        inventory_fn=_make_inventory_fn(registry, db_path),
        open_orders_fn=_make_open_orders_fn(registry),
        fleet_state_fn=lambda r: _fleet_state(r, cfg),
        resting_order_ids_fn=_venue_resting_order_ids,
        emit_fn=partial(_emit_cycle_event, db_path=db_path),
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
            suspects_box=suspects_box,
        )
    except InstanceInUse as exc:
        print(f"fleet refused: {exc}", file=sys.stderr)
        return 2
    return 0 if results else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
