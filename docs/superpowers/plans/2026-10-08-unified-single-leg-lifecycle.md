# Unified Single-Leg Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace competing automatic one-sided-fill decisions with one persisted lifecycle that waits patiently, escalates a profitable maker hedge, hard-stops at a 15-cent bid, and keeps the settlement fallback.

**Architecture:** Add a pure, per-pair lifecycle controller with durable state in the existing SQLite registry. The Trader passes a lifecycle escalation through the normal quote safety path with only the spread-distance gate bypassed; the poll and shadow runners use the same controller for hard stops and settlement rescue. `single_buy_saver` remains the safe venue executor, not an independent policy owner.

**Tech Stack:** Python, dataclasses/enums, SQLite (`OrderRegistry`), pytest, existing Polymarket CLOB adapters. No external dependencies.

## Global Constraints

- Never use or rewrite `data/orders.db` during development; test schema changes only with temporary databases.
- Keep the actual pair cost at or below `min(0.99, cfg.max_pair_cost)`; allow equality at `$0.99` only for an explicitly identified lifecycle escalation and its resulting pair lock.
- An escalated intent may bypass only the ordinary `max_spread_from_mid` distance gate. Book-health, price-band, pair-cost, sizing, naked-risk, bankroll, and venue-order checks remain active.
- The hard-stop trigger is a held-leg best bid `<= 0.15`; cancel and reread venue state before selling, and preserve position-agreement, depth/size, and slippage checks.
- Keep `ESCALATED_HEDGE` sticky across price recovery and process restarts, until pair lock, hard stop, or settlement fallback.
- During `PATIENT_WAIT`, do not take a profitable ask. The aged-out settlement fallback may make one final under-cap completion attempt before guarded exit.
- Preserve the manual `complete` command and the independent markout policy in `unhedged_stop_loss.py`.
- No live order-placement command or signer-backed Trader run during development. The shadow runner must remain deny-by-default and write only to its explicit shadow database.
- Run focused tests for changed behavior; do not run the full repository test suite locally at review/PR stations.

---

## File Map

| File | Responsibility |
|---|---|
| `core_brain/single_leg_lifecycle.py` | New `LegState`, immutable per-pair observations/results, pure transition and escalation-price calculation. |
| `core_brain/order_registry.py` | Add an additive lifecycle-state table and typed read/upsert methods; no production database is opened in tests. |
| `core_brain/risk.py` | Add a default-strict pair-cap check option used only by lifecycle escalation to allow exact equality; never allow a price above the cap. |
| `core_brain/quotes.py` | Accept an optional typed lifecycle quote override; preserve every existing gate except spread-distance for a valid escalation. |
| `core_brain/trader_loop.py` | Build lifecycle observations from reconciled per-pair fills/books, pass valid escalation context into quote evaluation, preserve pair IDs, and emit state-transition events. |
| `core_brain/single_buy_saver.py` | Add a force-exit option that bypasses only the completion preference; preserve cancel/recheck/venue-position/sizing/slippage safeguards. Keep compatibility adapters delegating to the lifecycle controller. |
| `core_brain/order_manager.py` | Replace the independent in-window and aged-out routing decisions with one post-reconcile call to the shared lifecycle service. |
| `core_brain/shadow_run.py` | Call the same lifecycle service through the current shadow client/store; do not create signing credentials or call live paths. |
| `tests/test_single_leg_lifecycle.py` | New deterministic policy/state-machine tests. |
| `tests/test_order_registry.py` | Additive schema and durable lifecycle-state tests. |
| `tests/test_live_quotes.py`, `tests/test_trader_loop.py` | Escalation gate and Trader integration tests. |
| `tests/test_single_buy_saver.py`, `tests/test_dual_stop_loss.py`, `tests/test_auto_pairs.py`, `tests/test_aged_out_rescue.py` | Hard-stop execution and automatic-route regression tests. |
| `tests/test_shadow_run.py`, `tests/test_shadow_exec.py` | Shadow-path parity and no-live-write regression tests. |
| `docs/agents/architecture.md`, `docs/agents/safety.md`, `docs/agents/strategy.md` | Document the single policy owner, state behavior, preserved safeguards, and operator verification. |

## Interfaces Locked Before Implementation

`core_brain/single_leg_lifecycle.py`:

```python
class LegState(str, Enum):
    DUAL_RESTING = "DUAL_RESTING"
    PATIENT_WAIT = "PATIENT_WAIT"
    ESCALATED_HEDGE = "ESCALATED_HEDGE"
    HARD_STOP = "HARD_STOP"
    PAIR_LOCKED = "PAIR_LOCKED"


@dataclass(frozen=True)
class SingleLegPosition:
    pair_id: str
    condition_id: str
    up_token_id: str
    down_token_id: str
    up_size: float
    down_size: float
    up_avg_price: float
    down_avg_price: float
    up_best_bid: float | None
    down_best_bid: float | None
    base_offset: float


@dataclass(frozen=True)
class LifecycleDecision:
    state: LegState
    action: str
    held_token_id: str | None = None
    held_average_price: float | None = None
    naked_size: float = 0.0
    opposite_token_id: str | None = None
    opposite_limit_price: float | None = None
    reason: str = ""


@dataclass(frozen=True)
class LifecycleQuoteOverride:
    pair_id: str
    token_id: str
    price: float
    size: int
    state: LegState
    held_average_price: float


@dataclass(frozen=True)
class LifecycleQuoteContext:
    override: LifecycleQuoteOverride | None = None
    refusal_reason: str | None = None
```

The full pure-function signature is
`transition(position: SingleLegPosition, previous_state: LegState | None, *,
max_pair_cost: float, settlement_due: bool = False, hard_stop_bid: float = 0.15,
escalation_multiple: float = 2.0, tick_size: float = 0.01) -> LifecycleDecision`.

`transition` is pure. It identifies residual exposure from the two per-pair leg sizes,
not fleet-aggregated inventory. A balanced pair at or below the allowed cap is
`PAIR_LOCKED`; no exposure is `DUAL_RESTING`; otherwise hard stop wins over settlement,
settlement wins over escalation, sticky prior escalation wins over returning to patience,
and the remaining one-sided case is `PATIENT_WAIT`. Unknown bids do not trigger price
transitions. Invalid escalation prices produce an explicit `refused` action.

`LifecycleStateRecord` contains `pair_id: str`, `condition_id: str`, `state: str`,
`reason: str`, `updated_ts_ms: int`, and `evidence_json: str`.

`OrderRegistry` exposes `get_lifecycle_state(pair_id: str) ->
LifecycleStateRecord | None` and `save_lifecycle_state(record: LifecycleStateRecord) ->
None`.

The stored record contains `pair_id`, `condition_id`, `state`, `reason`,
`updated_ts_ms`, and JSON-encoded transition evidence. Re-evaluation in the same state
does not rewrite the current record or produce another transition event.

## Task 1: Implement Pure Lifecycle Decisions

**Files:**
- Create: `core_brain/single_leg_lifecycle.py`
- Create: `tests/test_single_leg_lifecycle.py`

**Interfaces:**
- Consumes: per-pair leg sizes/average costs, both best bids, `base_offset`, prior state,
  pair cap, optional settlement status, and venue tick.
- Produces: the `LegState`, action, affected token IDs, capped limit price, and explicit
  reason using the exact interfaces above.

- [ ] **Step 1: Write transition boundary tests**

```python
def _position(*, up_size, down_size, up_avg, down_avg=0.0,
              up_bid, down_bid=0.52, base_offset=0.04):
    return SingleLegPosition(
        pair_id="pair-1", condition_id="condition-1",
        up_token_id="token-up", down_token_id="token-down",
        up_size=up_size, down_size=down_size,
        up_avg_price=up_avg, down_avg_price=down_avg,
        up_best_bid=up_bid, down_best_bid=down_bid,
        base_offset=base_offset,
    )


def test_hard_stop_at_fifteen_cents_precedes_escalation():
    pos = _position(up_size=2.0, down_size=0.0, up_avg=0.48,
                    up_bid=0.15, base_offset=0.04)
    decision = transition(pos, LegState.PATIENT_WAIT, max_pair_cost=0.99)
    assert decision.state is LegState.HARD_STOP
    assert decision.action == "hard_stop"


def test_escalation_uses_pair_ceiling_and_tick_floor():
    pos = _position(up_size=2.0, down_size=0.0, up_avg=0.48,
                    up_bid=0.40, base_offset=0.04)
    decision = transition(pos, LegState.PATIENT_WAIT, max_pair_cost=0.99)
    assert decision.state is LegState.ESCALATED_HEDGE
    assert decision.opposite_limit_price == 0.51
```

- [ ] **Step 2: Run the new focused tests and confirm they fail because the lifecycle API is absent**

Run: `python -m pytest -q tests/test_single_leg_lifecycle.py`
Expected: collection or attribute failure for the not-yet-created lifecycle symbols.

- [ ] **Step 3: Implement the state types and deterministic transition**

Implement the interfaces in the preceding section. Compute drawdown as `held average -
held best bid`; cross escalation at `drawdown >= 2.0 * base_offset`. Compute the maximum
bid as `min(0.99, max_pair_cost) - held average`, floor to the supplied tick, and refuse
non-positive prices. Give hard stop first priority for any naked residual, then settlement
fallback, then sticky escalation, then threshold escalation, then patient wait. Use
`SIZE_EPS`-equivalent `1e-6` as the residual-share tolerance.

Implement the tick floor as a finite-input checked helper:

```python
import math
from decimal import Decimal, ROUND_FLOOR


def max_profitable_hedge_bid(
    held_average: float, max_pair_cost: float, tick_size: float,
) -> float:
    if (not math.isfinite(max_pair_cost) or max_pair_cost <= 0
            or not math.isfinite(held_average) or not 0 < held_average < 1
            or not math.isfinite(tick_size) or tick_size <= 0):
        raise ValueError("pair cap, held average, and tick size must be valid")
    cap = Decimal(str(min(0.99, max_pair_cost)))
    held = Decimal(str(held_average))
    tick = Decimal(str(tick_size))
    ticks = ((cap - held) / tick).to_integral_value(rounding=ROUND_FLOOR)
    price = ticks * tick
    if price <= 0 or held + price > cap:
        raise ValueError("no positive tick-aligned hedge bid fits the pair cap")
    return float(price)
```

- [ ] **Step 4: Add complete transition coverage and rerun the module tests**

Add cases for `DUAL_RESTING`, first fill to `PATIENT_WAIT`, below/at/above escalation
threshold, sticky recovery, a partial opposite fill, exact `$0.99` pair lock, above-cap
refusal, unavailable bid, invalid limit, and settlement fallback.

Run: `python -m pytest -q tests/test_single_leg_lifecycle.py`
Expected: all lifecycle unit tests pass without network access.

- [ ] **Step 5: Commit the isolated policy unit**

Commit: `feat(lifecycle): add single-leg state transitions (#413)`

## Task 2: Persist Lifecycle State Additively

**Files:**
- Modify: `core_brain/order_registry.py`
- Modify: `tests/test_order_registry.py`
- Modify: `core_brain/single_leg_lifecycle.py`
- Modify: `tests/test_single_leg_lifecycle.py`

**Interfaces:**
- Consumes: `LifecycleDecision` plus pair identity and evidence.
- Produces: `LifecycleStateRecord`, `get_lifecycle_state(pair_id)`, and
  `save_lifecycle_state(record)`; only the existing registry abstraction writes state.

- [ ] **Step 1: Add failing persistence and restart tests**

```python
def test_lifecycle_state_round_trips_and_stays_sticky(registry):
    record = LifecycleStateRecord(
        pair_id="pair-1", condition_id="condition-1",
        state="ESCALATED_HEDGE", reason="drawdown_threshold",
        updated_ts_ms=1_000, evidence_json='{"held_bid":0.40}',
    )
    registry.save_lifecycle_state(record)
    reopened = OrderRegistry(db_path=registry.db_path)
    assert reopened.get_lifecycle_state("pair-1") == record
```

- [ ] **Step 2: Run the targeted registry test and verify the table/API are missing**

Run: `python -m pytest -q tests/test_order_registry.py -k lifecycle_state`
Expected: FAIL because lifecycle-state persistence has not been added.

- [ ] **Step 3: Add the table and atomic registry methods**

Add `single_leg_lifecycle` as `CREATE TABLE IF NOT EXISTS` in `SCHEMA`, keyed by
`pair_id`; store condition, state, reason, timestamp, and evidence JSON. Implement the
typed record and methods using the registry's normal connection/transaction patterns.
Use an upsert; propagate SQLite errors. Do not delete or rewrite orders, fills, or closes.

- [ ] **Step 4: Verify additive initialization against an old-format temporary database**

Create a test database with the pre-lifecycle schema, open it through `OrderRegistry`,
assert existing `orders` and `fills` rows remain byte-for-byte equivalent in values, and
assert the lifecycle table is present. Run:

`python -m pytest -q tests/test_order_registry.py -k "lifecycle_state or lifecycle_schema"`

Expected: all selected tests pass using `tmp_path` only.

- [ ] **Step 5: Make the lifecycle service load and save transitions**

Add a controller operation with the concrete contract
`evaluate(position, registry, *, max_pair_cost, settlement_due=False, tick_size=0.01)`.
It loads the previous state by pair ID, calls `transition`, and persists only actual
state changes with the observation evidence. Propagate state-store failures; do not
silently restart a pair at `PATIENT_WAIT`.

- [ ] **Step 6: Commit persistence and restart safety**

Commit: `feat(registry): persist single-leg lifecycle state (#413)`

## Task 3: Add the Guarded Hard-Stop Exit

**Files:**
- Modify: `core_brain/single_buy_saver.py`
- Modify: `tests/test_single_buy_saver.py`
- Modify: `tests/test_dual_stop_loss.py`

**Interfaces:**
- Consumes: `exit_single_buy(..., force=False)`; lifecycle hard stop alone passes
  `force=True`.
- Produces: the existing structured exit result, with close reason `hard_stop`.

- [ ] **Step 1: Write tests proving hard stop overrides profitable completion but not safety guards**

Add tests that call `exit_single_buy(..., force=True, reason="hard_stop")` with a
profitable ask and assert a sale is attempted only after cancel; add variants for failed
cancel, a venue match completing the pair during cancel, venue-position divergence, and
insufficient sell depth. Each unsafe variant must assert no sell order is posted.

- [ ] **Step 2: Run the hard-stop tests and confirm the `force` argument is unsupported**

Run: `python -m pytest -q tests/test_single_buy_saver.py -k force_exit`
Expected: FAIL at the unsupported `force` keyword.

- [ ] **Step 3: Add the narrow force-exit option**

Add keyword-only `force: bool = False` to `exit_single_buy`. Keep the existing
`should_exit` refusal when `force` is false. When true, skip only that economic
completion-preference check; preserve all position reads, cancel-both behavior,
post-cancel venue rereads, size/depth checks, tick-aligned slippage floor, and close
recording. Pass `reason="hard_stop"` from the controller.

Keep the existing refusal result when the preference check blocks an ordinary exit:

```python
if not force and not should_exit(pair["fill_cost"], light_ask, max_pair_cost):
    return {
        "action": "hold",
        "pair_id": pair_id,
        "pair_cost": pair["fill_cost"] + (light_ask or 0.0),
        "size": pair["naked"],
        "positions_checked": positions_checked,
    }
```

- [ ] **Step 4: Run the targeted safety suites**

Run: `python -m pytest -q tests/test_single_buy_saver.py tests/test_dual_stop_loss.py -k "force_exit or cancel or venue or position or slippage or hard_stop"`
Expected: all selected safety tests pass; existing default `force=False` behavior is
unchanged.

- [ ] **Step 5: Commit the guarded executor path**

Commit: `fix(lifecycle): execute hard stops through guarded exit (#413)`

## Task 4: Pass Escalated Bids Through Existing Quote Guards

**Files:**
- Modify: `core_brain/risk.py`
- Modify: `core_brain/quotes.py`
- Modify: `tests/test_live_quotes.py`

**Interfaces:**
- Add a typed optional lifecycle override to `decide_quotes` and
  `_decide_quotes_from_mid`; default `None` must preserve byte-for-byte existing quote
  behavior.
- `LifecycleQuoteOverride` contains `pair_id`, `token_id`, `price`, `size`, escalated
  state, and the pair-scoped held average.
- Add a default-false `allow_pair_cost_equal` option to `risk.hard_block`; only the
  explicit escalation candidate may pass it as true. Add an explicit lifecycle marker
  and per-pair held average so the light-side early return cannot skip book-health or
  price-band checks for this candidate, and its pair-cost gate uses the correct pair
  rather than fleet-aggregated inventory.

- [ ] **Step 1: Add failing quote tests for the one permitted gate exception**

Test a valid escalated bid outside `max_spread_from_mid` that passes when its pair cost
is at or below the lifecycle cap; assert the ordinary hard block at the same pair cost
still refuses equality. Test exact `$0.99` lifecycle equality passes, `$0.991` refuses,
and book-health, price-band, lifecycle-side, zero-allocation, and residual-size checks
still refuse or cap the candidate.

- [ ] **Step 2: Run the selected quote tests before implementation**

Run: `python -m pytest -q tests/test_live_quotes.py -k lifecycle_escalation`
Expected: FAIL because no lifecycle override or equality allowance exists.

- [ ] **Step 3: Add the narrow quote and risk parameters**

The escalation override identifies its `pair_id`, token ID, explicit escalated state,
held average, and maximum bid. Validate the price against the capped filled-leg cost
before inserting the intent. Existing light-side quotes intentionally return early from
`hard_block` after the pair-cost check because they reduce exposure; the explicit
lifecycle path must continue through book-health, price-band, and naked-risk checks.
It must also reject a lifecycle marker on a non-light side. Evaluate pair cost against
the override's per-pair held average, not the market-aggregated inventory average. Skip
only the spread-distance test for that lifecycle candidate. Pass
`allow_pair_cost_equal=True` only for the explicit `ESCALATED_HEDGE`, and still block
any combined cost above `min(0.99, cfg.max_pair_cost)`. All non-lifecycle callers retain
strict existing behavior.

Use decimal comparisons in `risk.hard_block` to preserve the exact cap boundary:

```python
pair_cost = Decimal(str(price)) + Decimal(str(other_avg))
pair_cost_cap = Decimal(str(cfg.max_pair_cost))
pair_cost_blocked = (
    pair_cost > pair_cost_cap
    if allow_pair_cost_equal
    else pair_cost >= pair_cost_cap
)
```

Extend `risk.hard_block` with default-strict keyword parameters
`allow_pair_cost_equal=False`, `lifecycle_escalation=False`, and
`pair_cost_average=None`. Reject `allow_pair_cost_equal=True` without the lifecycle
marker; in lifecycle mode, require the requested side to reduce the observed imbalance
and run the otherwise-skipped safety checks. Use `pair_cost_average` only for this
explicit lifecycle path. Ordinary quote decisions use the existing inventory average
and remain strict.

- [ ] **Step 4: Run quote regressions**

Run: `python -m pytest -q tests/test_live_quotes.py`
Expected: current live quote tests and new override tests pass.

- [ ] **Step 5: Commit the guarded escalation seam**

Commit: `feat(quotes): support capped lifecycle hedge bids (#413)`

## Task 5: Integrate Lifecycle Decisions Into the Trader

**Files:**
- Modify: `core_brain/trader_loop.py`
- Modify: `core_brain/quotes.py`
- Modify: `tests/test_trader_loop.py`
- Modify: `tests/test_live_quotes.py`

**Interfaces:**
- `_visit_one` creates one `SingleLegPosition` per active registry pair using pair-scoped
  fills, but the books are only available after `evaluate_market_quote` fetches them.
- Extend `evaluate_market_quote` with an optional
  `lifecycle_context_for(market, up_book, down_book, inventory)` callback. It runs after
  the existing single book fetch and before the quote decision, then supplies a typed
  `LifecycleQuoteContext`. A refusal produces no BUY intents and a terminal lifecycle
  reason so the normal planner cancels working quotes; otherwise its optional override is
  passed to built-in `decide_quotes`. If an active escalation reaches a custom decision
  port that cannot accept the override, return an explicit market error rather than
  silently quote at the ordinary price. With no callback, preserve the current decision
  call shape.
- Preserve `QuoteIntent.pair_id` through planning and submission for an escalation.

```python
context = LifecycleQuoteContext()
if (decision.state is LegState.ESCALATED_HEDGE
        and decision.held_average_price is not None
        and decision.opposite_limit_price is not None):
    opposite_side = (
        "DOWN" if decision.held_token_id == position.up_token_id else "UP"
    )
    size_cap = risk.size_for(
        cfg, inventory, opposite_side, decision.opposite_limit_price,
        pair_price=decision.held_average_price + decision.opposite_limit_price,
    )
    size = min(int(decision.naked_size), size_cap)
    if size >= cfg.min_quote_shares:
        context = LifecycleQuoteContext(override=LifecycleQuoteOverride(
            pair_id=position.pair_id,
            token_id=decision.opposite_token_id,
            price=decision.opposite_limit_price,
            size=size,
            state=decision.state,
            held_average_price=decision.held_average_price,
        ))
    else:
        context = LifecycleQuoteContext(
            refusal_reason=f"lifecycle escalation size {size} below venue minimum"
        )
elif decision.state is LegState.HARD_STOP:
    context = LifecycleQuoteContext(
        refusal_reason=f"lifecycle hard stop for pair {position.pair_id}"
    )
return context
```

Only create the override when the position has positive residual size and all price/size
values are finite and positive. If it cannot be constructed, emit a refusal and do not
submit an ordinary-price substitute for that same escalated pair.

- [ ] **Step 1: Add Trader tests for patient hold and escalation replacement**

Test that PATIENT_WAIT retains an existing opposite order at its target with no new
submission, while ESCALATED_HEDGE replaces that pair's opposite order at the exact
controller limit and carries the original pair ID. Test that a hard-stop state does not
submit a BUY and a locked pair receives no lifecycle replacement.

- [ ] **Step 2: Run the Trader tests and confirm they fail**

Run: `python -m pytest -q tests/test_trader_loop.py -k lifecycle`
Expected: FAIL because `_visit_one` does not yet supply lifecycle context.

- [ ] **Step 3: Connect registry observation, persisted state, and quote override**

In `_visit_one`, provide the callback to `evaluate_market_quote`. From its already-fetched
books/inventory and registry fills, build per-pair snapshots, use
`dynamic_offset_for(cfg)[0]` as the baseline, load prior state, and evaluate/persist each
decision. The callback returns an override only for the single unambiguous escalation;
patient intents remain unchanged. A `refusal_reason` yields no quote intents with terminal
classification so the planner cancels resting orders. Do not combine different pair IDs in one submit batch;
if the market has ambiguous simultaneous escalations, emit an explicit lifecycle refusal
instead of misattributing an order. A hard-stop decision suppresses all BUY intents for
the market and makes the refusal terminal so the planner cancels its resting orders; the
shared post-reconcile executor owns the guarded sell.

- [ ] **Step 4: Add transition events and verify no-op state is quiet**

Emit one structured cycle event on actual state changes with pair ID, previous/new state,
reason, held token/fill/bid, and escalation price. Assert unchanged state emits no
duplicate transition event.

- [ ] **Step 5: Run Trader and quote tests**

Run: `python -m pytest -q tests/test_trader_loop.py tests/test_live_quotes.py`
Expected: selected integration tests and existing Trader/quote regressions pass.

- [ ] **Step 6: Commit the Trader lifecycle integration**

Commit: `feat(trader): preserve and escalate active hedge orders (#413)`

## Task 6: Unify Poll and Shadow Rescue Routing

**Files:**
- Modify: `core_brain/single_buy_saver.py`
- Modify: `core_brain/order_manager.py`
- Modify: `core_brain/shadow_run.py`
- Modify: `tests/test_auto_pairs.py`
- Modify: `tests/test_dual_stop_loss.py`
- Modify: `tests/test_aged_out_rescue.py`
- Modify: `tests/test_shadow_run.py`
- Modify: `tests/test_shadow_exec.py`

**Interfaces:**
- Add `manage_single_leg_positions(client, registry, cfg, *, live=True, funder=None,
  now=None, resolved_cids=None, settlement_state_cache=None)` as the shared poll/shadow
  policy entry point, returning structured action results per pair.
- Keep `auto_manage_pairs` and `rescue_aged_out_legs` as compatibility adapters that
  delegate to the shared service; they must not independently decide immediate
  completion, adverse drift, or grace expiry.

- [ ] **Step 1: Write regressions for patience, hard-stop routing, and settlement-only completion**

Update focused old-route tests so a profitable ask inside PATIENT_WAIT results in a hold
and no BUY; a `<= 0.15` held bid calls the guarded forced exit before any completion; and
the aged-out fallback alone can try one capped completion before safe exit.

- [ ] **Step 2: Run the poll and shadow target tests and confirm they fail**

Run: `python -m pytest -q tests/test_auto_pairs.py tests/test_dual_stop_loss.py tests/test_aged_out_rescue.py`
Expected: failures on the old immediate-completion/drift/grace expectations.

- [ ] **Step 3: Replace independent route policy with the shared controller**

Implement `manage_single_leg_positions` to enumerate all current one-sided pairs after
reconcile, not only fills inside the former 15-minute auto window. Preserve resolved
market handling. For each position, read its books, load/persist lifecycle state, apply
hard-stop first, and use the explicit settlement-due signal for the one final capped
completion attempt. The hard-stop uses `exit_single_buy(force=True, reason="hard_stop")`.
Do not run the old in-window taker completion, drift threshold, or grace timeout.

- [ ] **Step 4: Wire the order-manager poll to one lifecycle pass**

Replace the independent in-window plus aged-out decision calls with one shared service
call after reconcile. Keep the current per-pair error isolation and cycle-event/log
reporting. Retain the market-state cache inputs needed by settlement fallback.

- [ ] **Step 5: Wire shadow rehearsal through the same service**

Replace `shadow_sweep`'s duplicate old routing calls with the shared service using its
existing simulated client, temporary shadow registry, resolved-market callback, and
aged-out state cache. Assert the lifecycle close/order rows stay in the shadow store and
the deny-by-default client receives no live write method.

- [ ] **Step 6: Run focused poll, aged-out, shadow, and execution regressions**

Run: `python -m pytest -q tests/test_auto_pairs.py tests/test_dual_stop_loss.py tests/test_aged_out_rescue.py tests/test_shadow_run.py tests/test_shadow_exec.py`
Expected: all focused routing tests pass; legacy APIs remain as adapters and no shadow
path reaches a signing client.

- [ ] **Step 7: Commit unified automatic routing**

Commit: `refactor(lifecycle): unify poll and shadow rescue routing (#413)`

## Task 7: Document, Review, and Run the Focused Regression Set

**Files:**
- Modify: `docs/agents/architecture.md`
- Modify: `docs/agents/safety.md`
- Modify: `docs/agents/strategy.md`
- Test: focused lifecycle, registry, quote, Trader, poll, and shadow suites

**Interfaces:**
- Documentation must describe the lifecycle controller as the single policy owner,
  `single_buy_saver` as the guarded executor, and `unhedged_stop_loss` as the separate
  per-market markout gate.

- [ ] **Step 1: Update architecture and strategy documentation**

Replace statements that describe independent automatic single-buy routing with the new
five-state flow, sticky escalation, 99-cent maximum, and the settlement-only completion
exception. State that normal PATIENT_WAIT does not taker-complete.

- [ ] **Step 2: Update safety documentation**

Document the 15-cent hard-stop trigger, the required cancel/re-read/position-check order,
the existing sell depth/slippage bounds, explicit failure behavior, and the fact that
development does not run live order-placement commands.

- [ ] **Step 3: Run the complete focused change set**

Run:

```powershell
python -m pytest -q tests/test_single_leg_lifecycle.py tests/test_order_registry.py tests/test_single_buy_saver.py tests/test_dual_stop_loss.py tests/test_auto_pairs.py tests/test_aged_out_rescue.py tests/test_live_quotes.py tests/test_trader_loop.py tests/test_shadow_run.py tests/test_shadow_exec.py
```

Expected: all listed focused suites pass; the full repository regression suite remains
the GitHub CI merge gate.

- [ ] **Step 4: Review the full diff for scope and safety**

Confirm no `data/*.db`, credentials, unrelated activity-gate files, disabled tests, or
broadened cap exceptions are in the diff. Confirm the single spread-distance exception
cannot be used without a persisted `ESCALATED_HEDGE` decision.

- [ ] **Step 5: Commit docs and final regression fixes**

Commit: `docs(lifecycle): document unified single-leg management (#413)`

## Execution Checkpoints

- **C1 after Tasks 1–2:** pure transitions and restart-persistent sticky state pass on
  temporary databases.
- **C2 after Tasks 3–4:** hard stop preserves all exit safety checks; escalation passes
  only its explicitly permitted quote-gate exceptions.
- **C3 after Tasks 5–6:** Trader, poll, and shadow all use the same lifecycle policy.
- **C4 after Task 7:** focused regression set is green and operator-facing docs match the
  implemented behavior.

## How to Verify (operator-facing, hands-on only)

1. Start a time-boxed shadow rehearsal using a new `data\20261008_lifecycle_shadow.db`
   path and open the dashboard against that same store.
2. Dashboard → Orders / Cycle feed → observe one-sided exposure move through
   `PATIENT_WAIT` and, when the shadow book crosses the threshold, `ESCALATED_HEDGE` with
   a bid no higher than `$0.99 - filled average`.
3. In the same dashboard, observe no completion BUY during PATIENT_WAIT; if the held
   best bid reaches `$0.15` or below, observe cancellation and a guarded simulated sell;
   at the settlement fallback, observe the one final capped completion attempt.
4. If a transition or action is missing, or any pair cost exceeds `$0.99`, stop the
   rehearsal and inspect its shadow store only; do not press dashboard START.

No live-money opening command is part of this verification.
