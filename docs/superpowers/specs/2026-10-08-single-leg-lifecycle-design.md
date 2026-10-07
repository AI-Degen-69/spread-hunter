# Unified Single-Leg Lifecycle — Design

**Status:** approved design; awaiting written-spec review
**Scope:** replace the conflicting automatic single-leg routing with one persisted,
shared lifecycle controller. Keep the existing venue-safe execution mechanics.

## Problem

One-sided fills currently pass through independent automatic decisions in
`core_brain/single_buy_saver.py` and quote decisions in `core_brain/trader_loop.py`.
The saver may complete a profitable pair immediately, exit on adverse drift, or exit
after its grace timer, while the trader continues to calculate ordinary quotes. These
paths do not express one progressive policy for a single position.

## Goals

- Represent the state of each paired position with an explicit `LegState` and position
  structure.
- Keep the missing maker leg at its original target while a one-sided fill is within the
  patient stage.
- Escalate only when the held leg has materially moved against its fill, and never price
  the completing leg above the pair-cost ceiling.
- Give the hard stop precedence over completion and ordinary quoting.
- Preserve venue/registry agreement, cancel-race checks, sizing, slippage bounds, and the
  market-settlement fallback.
- Make the current stage survive process restarts and be shared by the Trader and the
  order-manager poll path.

## Non-goals

- Changing market selection, movement/velocity gates, or quote pricing outside the
  one-sided lifecycle.
- Weakening the $0.99 pair-cost ceiling, dynamic order/naked/portfolio caps, or sell
  safeguards.
- Removing the operator-triggered `complete` command.
- Replacing the independent per-market markout gate in `unhedged_stop_loss.py`.
- Editing or migrating the live `data/orders.db` during development.

## State model

`SingleLegPosition` is keyed by the registry pair ID and contains the held token and side,
held average fill price and size, opposite token, state, and the evidence needed for the
current decision. `LegState` has these values:

| State | Meaning and action |
|---|---|
| `DUAL_RESTING` | No one-sided fill; both legs use their ordinary maker target prices. |
| `PATIENT_WAIT` | One leg is held; leave the opposite leg resting at its existing target. Do not take a profitable ask during this stage. |
| `ESCALATED_HEDGE` | The held-leg drawdown crossed the threshold; keep the stage sticky and rest the opposite bid at the capped escalation price. |
| `HARD_STOP` | The held token's best bid is at or below `$0.15`; cancel working orders and run the guarded naked-leg sell path. |
| `PAIR_LOCKED` | Both legs are filled and their combined average cost is at or below `$0.99`; no further single-leg action is needed. |

The one-sided drawdown is `held_average_fill_price - held_leg_best_bid`. The base offset
comes from the existing `dynamic_offset_for(cfg)` calculation, before inventory skew and
other quote adjustments. The threshold is `2.0 * base_offset`. A missing or unreadable
held-leg bid is not treated as a drawdown; it does not trigger escalation or hard stop.

The escalation limit is:

```text
cap = min(0.99, cfg.max_pair_cost)
max_bid = cap - held_average_fill_price
posted_bid = floor_to_venue_tick(max_bid)
```

Tick flooring ensures the actual pair cost cannot exceed the ceiling. If the computed
limit is non-positive or otherwise invalid, the controller refuses escalation and emits
an explicit error; it does not invent a price.

The hard-stop check has priority over completion and escalation. It enters `HARD_STOP`
when the observed best bid is `<= 0.15`. The state is terminal for that position. A
balanced position enters `PAIR_LOCKED` only when its combined average cost is `<= 0.99`
and does not exceed any stricter configured cap; the controller never labels an
over-cap pair profitable. The explicitly requested `$0.99` equality is permitted only
for this lifecycle's capped escalation and resulting pair lock, not as a relaxation to
ordinary quote or manual-completion gates.

`ESCALATED_HEDGE` is sticky until `PAIR_LOCKED`, `HARD_STOP`, or the settlement fallback.
An additive lifecycle record in the existing registry schema stores the pair ID, current
state, and transition evidence. Schema initialization/migration is exercised against
temporary test databases only; no production database file is edited as part of this
change.

## Ownership and data flow

Add a dedicated lifecycle controller as the single policy owner:

1. `trader_loop` supplies reconciled inventory, open-order state, the current held-leg
   book, and the quote configuration. The controller decides whether to preserve the
   target maker intent or replace the opposite intent with the escalation limit.
2. `order_manager poll` calls the same controller after reconcile. It detects hard stops
   and settlement fallback conditions and dispatches only the resulting close action.
3. `single_buy_saver.py` remains the low-level executor for completion and exit operations:
   cancel working orders, verify venue fills and positions, enforce sell depth/size and
   slippage limits, then persist closes. Its conflicting automatic in-window
   completion/drift/grace routing is retired. The settlement fallback remains an explicit
   lifecycle action.
4. `unhedged_stop_loss.py` continues to manage per-market markout posture; it is not a
   second single-leg lifecycle controller.

During `PATIENT_WAIT`, the automated pass must not issue the old immediate taker
completion. At the existing aged-out settlement rescue point only, make one final
completion attempt if the combined cost remains under the configured cap; if it cannot
complete safely, use the guarded exit path. Manual `complete` remains an operator action.

## Hard-stop execution and failures

The hard-stop policy may bypass only the normal preference to complete a profitable pair.
It must not bypass any execution safeguard:

1. Cancel the opposite resting order and other working orders on the pair.
2. Read venue order matches again to catch fills racing cancellation.
3. Confirm the position against the venue and recompute the remaining naked size.
4. Sell only venue-agreed shares supported by acceptable bid depth and the current
   slippage floor.
5. Record the exit and its `hard_stop` reason.

A cancel failure, unreadable venue state, position divergence, missing/invalid book, or
unsellable size remains an explicit refusal/error. No success-shaped fallback is allowed.

## Observability

Emit a lifecycle transition event only when a pair changes state. Include pair ID,
previous and next state, trigger/reason, held token, measured bid/fill prices, and the
chosen escalation price when applicable. Surface execution refusals through the existing
cycle event and operator log paths; repeated unchanged states should not flood logs.

## Verification

Add deterministic tests for:

- every state transition, including first fill, balanced pair, hard-stop boundary, and
  settlement fallback;
- no immediate taker completion in `PATIENT_WAIT`;
- escalation at and above `2 * base_offset`, no escalation below it, and sticky behavior
  through recovery/restart;
- exact cap calculation and conservative venue-tick flooring;
- hard stop taking priority over a profitable completion, cancellation preceding sale,
  venue reread catching a racing completion, and all existing position/size/slippage
  refusals;
- additive registry-state persistence using temporary databases;
- both Trader and poll paths using the same controller decision.

Run the focused lifecycle, single-buy-saver, dual-stop-loss, trader-loop, and registry
tests. Do not run the full suite locally at review/PR stations. No live order-placement
command is part of development verification.
