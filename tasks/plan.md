# Plan — #419: trader_loop: hold a resting order and never requote when mid <= price + 0.02

Branch: i419/trader-loop-hold-a-resting-order-and-never-requote | Issue: #419

- Tier: **Standard** — planner + visit wiring + docs paragraph, one architectural decision (guard placement at the cancel decision, not in pricing).
- Task type: **Code** — cancel-path guard + visit feed + regression tests.
- Stack: Python, pytest; no external dependency.
- CodeRabbit plan: **adopted as scaffolding, merged into 3 tasks** — all 4 design choices adopted (guard at cancel decision; price-driven routes only; named module constant; today's behavior on missing/crossed books); task phases merged per Rule 4. Verified from code (nothing left `[UNVERIFIED]`): `plan_orders` at `trader_loop.py:237` with `price_eps` and `held_tokens` already present; grace expiry shares `REFUSED_TERMINAL` (`trader_loop.py:1759-1762`), so a distinct member is required; `quotes.mid_price` (`quotes.py:103`) takes bid/ask with no crossed-check, so the caller skips crossed books; books carry `token_id`/`best_bid`/`best_ask` (`trader_loop.py:1709-1713`); test spies forward `**kwargs` (`test_trader_loop.py:1073,2301`); `requote_dead_band = 0.03` (`config.py:875`) with retired meaning — the new constant is justified; every cited test name exists. Rejected: nothing material.
- Open questions resolved from code (no operator question): the residual requote/cancel originates in exactly two places — the `not_quoted` branch (`trader_loop.py:432`, the only remaining canceller after #387's NO RE-CHECK holds every wanted token) and `lifecycle_replace` (`trader_loop.py:413-419`). The 0.02 is a named constant per the issue default (`MID_HOLD_BAND`), since `requote_dead_band` is 0.03 with a retired meaning.
- Improvement proposal (adopted, simplification): guard-held tokens join the EXISTING `held_tokens` set instead of a second set. Evidence: `trader_loop.py:402-408` — "A held order rests at a price outside the tolerance by definition, so the submit loop below would not recognise it as covering this cycle's intent and would post a second order beside it" — the identical hazard, and the existing skip at lines 448-451 then covers the ordinary branch for free; only the lifecycle-pair branch (lines 445-447, which bypasses the check) needs the same one-line skip.
- Type-design analyzer: skipped with reason — no non-trivial domain model (one optional dict param defaulting to `None`, one enum member; nothing to encapsulate).
- Sub-issues: skipped per repo precedent (Standard #416 shipped without them; the plan file is the tracker).
- Safety: do not open or rewrite `data/orders.db`; tests use temporary DBs. No live quoting, Trader loop, manual completion, or dashboard START.

## Locked behavior (see SPEC.md; summary)

- Hold iff the token's mid exists, the book is two-sided and uncrossed, and `mid <= order.price + 0.02` (equality included via `price_eps`).
- Guarded routes: `not_quoted` cancels, grace expiry, `lifecycle_replace`.
- Unguarded routes: named terminal refusals, hard stop, `lifecycle_cancel`, explicit cancel set, cancel-wins-over-replace.
- One fill-side invariant preserved: a held token is never submitted twice.

## Interface contracts (frozen)

- `plan_orders(..., token_mids: Optional[dict] = None)` — keyword-only, `None` keeps today's behavior exactly; existing callers and test spies (`**kwargs`) need no change.
- New `VisitOutcome` member for grace expiry (today it arrives as `REFUSED_TERMINAL`, indistinguishable from a named terminal refusal).
- `token_mids`: token -> mid, built in `_visit_one` from `ev.up_book`/`ev.down_book` via `quotes.mid_price`; token omitted when its id is missing, either side is missing, or bid >= ask.
- Guard-held tokens join `held_tokens`; both submit branches skip them.

## Dependency graph

- T1 → T2 → T3

## Tasks

### T1 [x] — RED+GREEN: mid-hold band on the missing-intent branch [Backend/Logic] (M)

- Target files: `core_brain/trader_loop.py`, `tests/test_plan_orders_mid_hold.py` (new)
- Build: add `MID_HOLD_BAND = 0.02` with a one-line rule comment; add keyword-only `token_mids=None` to `plan_orders`; add a helper holding iff the token has a mid and `mid <= price + MID_HOLD_BAND + price_eps`. In the no-intent branch keep this order: transient-refusal hold → terminal-refusal cancel (incl. hard stop) → in-band hold (no `CANCEL_NOT_QUOTED`, token joins `held_tokens`) → `not_quoted` cancel. Add the distinct grace-expiry `VisitOutcome` member at the `_visit_one` assignment (`trader_loop.py:1759-1762`) and thread it so grace expiry holds in band while terminal refusals cancel. New test file in the style of `test_plan_orders_asymmetric_hold.py` (UP order at 0.48): equality mid 0.50 holds (empty cancels, no UP submit, no UP `not_quoted`); in-band 0.46 holds; out-of-band 0.501 cancels `not_quoted`; missing mid cancels; `token_mids=None` cancels; terminal refusal at 0.50 cancels; grace expiry at 0.50 holds with nothing submitted; drifted intent (UP intent at 0.40, mid 0.46) neither cancels nor submits.
- Helper skill: `test-driven-development`.
- Depends on: nothing.
- Verify: `python -m pytest -q tests/test_plan_orders_mid_hold.py tests/test_plan_orders_asymmetric_hold.py` — new tests fail before, all green after.

**Checkpoint:** in-band missing-intent orders hold instead of cancelling; terminal and out-of-band behavior untouched.

### T2 [x] — GREEN: hold in-band lifecycle replacements, no duplicate submits [Backend/Logic] (M)

- Target files: `core_brain/trader_loop.py`, `tests/test_plan_orders_mid_hold.py`
- Build: an order in `replace_order_ids` but not `cancel_order_ids` that is in band is held (no `lifecycle_replace`, token joins `held_tokens`); `lifecycle_cancel`, explicit preservation, wanted-token holds, and cancel-wins-over-preserve stay exactly. Both submit branches skip guard-held tokens — the ordinary branch via the existing `held_tokens` check, the lifecycle-pair branch with the same one-line skip. Extend the test file (DOWN hedge at 0.48, lifecycle-pair DOWN intent at 0.51): in-band mid 0.49 → no cancel, no DOWN submit; out-of-band 0.51 → `lifecycle_replace` cancel + 0.51 intent submitted; missing mid → replacement as today; replace+cancel in band → cancelled; `lifecycle_cancel` on UP at 0.48 with mid 0.46 → cancelled `lifecycle_cancel`.
- Helper skill: `incremental-implementation`.
- Depends on: T1.
- Verify: `python -m pytest -q tests/test_plan_orders_mid_hold.py tests/test_plan_orders_asymmetric_hold.py` — all green.

### T3 [ ] — Wire real mids from `_visit_one`, protect visit contracts, document [Backend/Logic] (M)

- Target files: `core_brain/trader_loop.py`, `tests/test_trader_loop.py`, `docs/agents/strategy.md`
- Build: in `_visit_one`, build `token_mids` from this cycle's UP/DOWN books keyed by `token_id` via `quotes.mid_price`; omit on missing id, missing side, or bid >= ask. Pass it on every `(plan_fn or plan_orders)(...)` call (spies forward `**kwargs`, no wrapper changes). A rotated-away token has no book and still cancels. Move the shared `TestRefusedHold` books to 0.66/0.68 (out of band for the 0.60 UP order) so the grace-expiry test keeps asserting cancel; add a separate in-band grace test (0.59/0.61 books, transient refusal × GRACE cycles → held, nothing submitted). Add visit tests: in-band (UP 0.48, book 0.45/0.47, DOWN-only intent → no UP cancel) and crossed book (0.50/0.48 → `not_quoted` cancel). Re-check: terminal-refusal, token-rotation, quote-resets-streak, patient-wait (UP remainder via cancel set), escalation (DOWN mid 0.51 outside the 0.48 band → replace proceeds), escalation-preserve, hard-stop, refused-escalation — assertions unchanged. Append one paragraph to `docs/agents/strategy.md` (inclusive rule, covered routes, still-cancelling routes, missing/crossed fallback) without rewriting the dead-band text. Confirm zero changes in `shadow_fills.py`, `config.py`, `quotes.py`, `_market_cfg`.
- Helper skill: `incremental-implementation`.
- Depends on: T2.
- Verify: `python -m pytest -q tests/test_trader_loop.py tests/test_plan_orders_asymmetric_hold.py tests/test_plan_orders_mid_hold.py` — all green.
