# Plan — #413: Consolidate single-leg exposure into a unified lifecycle

Branch: i413/tune-live-activity-gates-protect-hedge | Issue: #413

- Tier: **Large** — durable position state, quote admission, live close execution, poll/shadow integration, and safety documentation cross multiple runtime surfaces.
- Task type: **Code + Security + Debug + Docs** — changes affect real-money position lifecycle and must preserve every cancellation, position, sizing, and sell guard.
- Stack: Python, SQLite registry, pytest; no external dependency.
- Approved specification: [Unified Single-Leg Lifecycle design](../docs/superpowers/specs/2026-10-08-single-leg-lifecycle-design.md).
- Detailed execution plan: [Unified Single-Leg Lifecycle implementation plan](../docs/superpowers/plans/2026-10-08-unified-single-leg-lifecycle.md).
- Reconciliation: this replaces the previous #413 activity-gate plan at the operator's direction. No prior task is marked complete. Do not change market movement/velocity selector behavior in this build.
- Safety: do not open or rewrite `data/orders.db`; tests use temporary DBs. Do not run live quoting, Trader, manual completion, or dashboard START. Shadow rehearsal is the only permitted order-loop validation.

## Locked behavior

- `DUAL_RESTING` → `PATIENT_WAIT` on a per-pair one-sided fill; keep the opposite maker order at its target and do not immediately taker-complete.
- Escalate when `held_average_fill - held_best_bid >= 2.0 * dynamic_offset_for(cfg)[0]`.
- Sticky `ESCALATED_HEDGE` price is `floor_to_tick(min(0.99, cfg.max_pair_cost) - held_average_fill)`; exactly `$0.99` is permitted only for this lifecycle escalation and its resulting pair lock.
- Only lifecycle escalation may bypass `max_spread_from_mid`; book-health, price-band, pair-cost, order, naked-risk, bankroll, and venue checks remain.
- `HARD_STOP` triggers at held best bid `<= $0.15`, precedes completion, cancels then re-reads venue state, and uses the guarded exit mechanics.
- Balanced inventory at or below `$0.99` and no stricter configured cap is `PAIR_LOCKED`.
- Preserve the existing settlement/aged-out fallback; only there may the controller try one final under-cap completion before guarded exit.
- Trader, order-manager poll, and shadow rehearsal call one shared lifecycle policy. `single_buy_saver` remains its executor; `unhedged_stop_loss` remains the independent markout gate.

## Dependency graph

- T1 → T2
- T1 → T3
- T1 + T2 → T4 → T5
- T1 + T2 + T3 + T5 → T6
- T1–T6 → T7

## Tasks

### T1 [x] — Implement pure lifecycle transitions [Backend/Logic] (M)

- Target files: `core_brain/single_leg_lifecycle.py`, `tests/test_single_leg_lifecycle.py`
- Build: add `LegState`, per-pair position/decision dataclasses, hard-stop / settlement / sticky escalation precedence, threshold computation, and tick-floored cap logic.
- Helper skill: `test-driven-development`
- Depends on: none.
- Verify: `python -m pytest -q tests/test_single_leg_lifecycle.py`; cover all five states, exact boundaries, missing books, partial fills, and invalid cap prices.

### T2 [x] — Persist sticky state in the registry [Backend/Logic] (M)

- Target files: `core_brain/order_registry.py`, `core_brain/single_leg_lifecycle.py`, `tests/test_order_registry.py`, `tests/test_single_leg_lifecycle.py`
- Build: add an additive lifecycle table and typed `get_lifecycle_state` / `save_lifecycle_state` methods; fail explicitly on persistence errors.
- Helper skill: `test-driven-development`
- Depends on: T1.
- Verify: `python -m pytest -q tests/test_order_registry.py -k "lifecycle_state or lifecycle_schema"` using temporary databases; prove existing order/fill values remain unchanged and escalation state survives reopen.

### T3 [x] — Add guarded hard-stop selling [Backend/Logic] (M)

- Target files: `core_brain/single_buy_saver.py`, `tests/test_single_buy_saver.py`, `tests/test_dual_stop_loss.py`
- Build: add `force=False` to `exit_single_buy`; when the lifecycle passes `force=True`, bypass only the profitable-completion preference, retaining cancel-both, venue reread, position agreement, sell sizing, depth, slippage, and close recording.
- Helper skill: `test-driven-development`
- Depends on: T1.
- Verify: `python -m pytest -q tests/test_single_buy_saver.py tests/test_dual_stop_loss.py -k "force_exit or cancel or venue or position or slippage or hard_stop"`; profitable completion must not block the hard stop, while failed safety checks must send no sell.

### T4 [x] — Add the narrow escalated quote path [Backend/Logic] (L)

- Target files: `core_brain/risk.py`, `core_brain/quotes.py`, `tests/test_live_quotes.py`
- Build: pass a typed lifecycle override through quote decisions; require the exact tick-floored maximum bid and pair-scoped cost, then apply normal book-health, price-band, and size checks for the reducing side. Bypass only spread-distance and permit equality at `min(0.99, cfg.max_pair_cost)` only for this explicit lifecycle override; keep the Trader's final funding caps intact.
- Helper skill: `api-and-interface-design`, `test-driven-development`
- Depends on: T1, T2.
- Verify: `python -m pytest -q tests/test_live_quotes.py`; assert non-lifecycle calls remain strict, lifecycle cost `<= min(0.99, cfg.max_pair_cost)` passes, a larger cost fails, and every other safety gate remains active.

### T5 [x] — Integrate lifecycle decisions into the Trader [Backend/Logic] (L)

- Target files: `core_brain/trader_loop.py`, `core_brain/quotes.py`, `tests/test_trader_loop.py`, `tests/test_live_quotes.py`
- Build: derive pair-scoped position snapshots from fills and fetched books, load persisted state, keep patient orders unchanged, carry escalation price and existing pair ID through planning, and emit transition events only on changes.
- Helper skill: `test-driven-development`, `api-and-interface-design`
- Depends on: T1, T2, T4.
- Verify: `python -m pytest -q tests/test_trader_loop.py tests/test_live_quotes.py`; cover patient keep, exact escalation replace, hard-stop no-buy, pair-ID attribution, unchanged-state quietness, and ambiguous simultaneous pair handling.

### T6 [x] — Unify poll and shadow rescue routing [Backend/Logic] (L)

- Target files: `core_brain/single_leg_lifecycle.py`, `core_brain/single_buy_saver.py`, `core_brain/order_manager.py`, `core_brain/shadow_run.py`, `tests/test_auto_pairs.py`, `tests/test_dual_stop_loss.py`, `tests/test_aged_out_rescue.py`, `tests/test_shadow_run.py`, `tests/test_shadow_exec.py`
- Build: add one shared post-reconcile `manage_single_leg_positions` service for all active one-sided pairs, hard stops, and settlement fallback. Retire automatic in-window completion/drift/grace decisions; retain `auto_manage_pairs` and `rescue_aged_out_legs` as delegating compatibility adapters. Wire both poll and shadow through the same service and one pass per cycle.
- Helper skill: `test-driven-development`, `debugging-and-error-recovery`
- Depends on: T1, T2, T3, T5.
- Verify: `python -m pytest -q tests/test_auto_pairs.py tests/test_dual_stop_loss.py tests/test_aged_out_rescue.py tests/test_shadow_run.py tests/test_shadow_exec.py`; shadow actions must stay in the explicit shadow DB and use no signing client.

### T7 [ ] — Document safeguards and finish focused verification [Docs + Backend/Logic] (M)

- Target files: `docs/agents/architecture.md`, `docs/agents/safety.md`, `docs/agents/strategy.md`, all focused tests listed in the detailed plan
- Build: document state ownership, thresholds, sticky escalation, exact-cap exception, hard-stop safeguards, settlement-only completion, and hands-on shadow verification.
- Helper skill: `documentation-and-adrs`, `verification-before-completion`
- Depends on: T1, T2, T3, T4, T5, T6.
- Verify: run the focused suite listed in Task 7 of the detailed plan; inspect the full diff for any production DB, live-command, selector-gate, or unrelated changes.

## Checkpoints

- C1 after T1–T2: transition logic and restart-persistent state work against temporary stores.
- C2 after T3–T4: forced sell safeguards and the narrowly scoped quote exception are proven.
- C3 after T5–T6: Trader, poll, and shadow use the shared lifecycle controller.
- C4 after T7: focused regression set and operator-facing docs agree.
