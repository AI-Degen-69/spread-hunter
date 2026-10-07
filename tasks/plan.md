# Plan — #411: Apply asymmetric requote thresholds and enforce the pair-cost ceiling

Branch: i411/apply-asymmetric-requote-thresholds-and-enforce-the-pair-cost-ceiling | Issue: #411

- Tier: **Standard** — one config seam, one risk seam, and two quote/planner paths (`quotes.py`, `trader_loop.py`) plus focused regression tests. The work is narrow but cross-cutting because the cap and the hold are enforced in multiple branches.
- Task type: **Code + Debug + Security** (real-money path; a pair over $1.00 is a booked loss, and a bad cancel path can turn a valid fill into a churn loop).
- Stack: Python, pytest (`python -m pytest -q tests/test_plan_orders_asymmetric_hold.py tests/test_ladder_quotes.py tests/test_completable_pair_gate.py tests/test_trader_loop.py tests/test_live_quotes.py` for focused checks).
- Skills: `test-driven-development`, `incremental-implementation`, `debugging-and-error-recovery`.
- Spec: issue body for #411 plus the CodeRabbit design notes already in the issue comments; no external dependency and no schema migration.
- CodeRabbit plan intake (read once; rendered copy used, HTML echo ignored):
  - Adopted: hard $0.99 ceiling as a guardrail that always applies to any rounded pair, while configured caps only tighten the rule; asymmetric BUY rule should compare the current mid to the resting bid (`mid - resting >= edge_vs_mid + dead_band`) and keep a downward move inside the hold window instead of cancelling; planner logic keeps wanted tokens held until the target has moved past the configured floor; ladder/legacy quote paths must reuse the same pair-cost refusal helper before emitting a quote.
  - Rejected/trimmed: over-split work merged into 4 atomic tasks; no registry or order-manager work; no new public abstractions or new files.
  - `[UNVERIFIED]` at plan time to confirm in build: the exact call sites for the ladder path and legacy path in `quotes.py`, and whether the asymmetry is enforced in `plan_orders` before or after the existing token-without-intent logic. These are code-backed checks, not guesses.
- Open questions: none at plan time; issue already names the exact policy contract and the affected seams.
- Skipped personae: none; direct reads of `quotes.py`, `risk.py`, `trader_loop.py`, and the relevant tests were enough to ground the plan.

## Interface lock

Changed (`core_brain/config.py`):
- `max_pair_cost` remains the general cap and `max_completable_pair_cost` stays the both-maker gate; the new hard ceiling is always-on and must not be bypassed by configuration.
- `requote_dead_band` stays the hysteresis threshold; `requote_hold_below_target` is the downward-move hold cap that prevents cancellation when the market is arriving on a resting BUY.

Changed (`core_brain/risk.py`):
- Add a single helper that refuses any rounded pair above `$0.99` when the pair is evaluated against the opposite-side hold or best ask.
- Use the effective cap helper to return `min(cfg.max_pair_cost, 0.99)` so config cannot loosen the rule.
- Keep `completable_pair_block` and `hard_block` active; this bug is a missing enforcement layer, not a rewrite of the risk gates.

Changed (`core_brain/quotes.py`):
- Apply the hard pair ceiling in the from-mid production path and in the ladder/legacy quote generation that may assemble a pair over the cap.
- `quote_resting_price` and the quote-generation branches should be checked for final-price refusal before a quote is emitted.

Changed (`core_brain/trader_loop.py`):
- `plan_orders` is the asymmetric re-quote gate. It must treat a falling target differently from a rising target for resting BUY orders so a tide of downward fills is not cancelled away.
- Keep the existing reason-recording path (`reasons` dict / CANCEL_* constants) and only add or tighten the decision gate logic.

Frozen / out of scope:
- `core_brain/single_buy_saver.py`
- `core_brain/order_registry.py`
- `core_brain/order_manager.py`
- `core_brain/shadow_run.py`
- `core_brain/shadow_exec.py`
- `core_brain/markets.py`
- `core_brain/cancel_report.py`
- `scoring/config.py`
- `tests/conftest.py`
- `tests/test_limit_order_pricing.py` (regression-only reference, not a driver for this issue)
- `docs/` and all non-code collateral

## Improvement proposal (adopted — hardening, evidence-based)

> A resting BUY that is being swept downward should not be cancelled by the market arriving at it; the bot should hold it within the configured threshold while a real pair-cost failure still rejects it. Evidence: the issue asks for the asymmetric BUY rule and the repo already documents `requote_hold_below_target` as a downward hold in `core_brain/config.py`, while the current `plan_orders` contract still treats the two directions as the same event. This is the missing logic step that hardens the queue without changing the strategy objective.

## Dependency graph

- T1 → T2 → T3 → T4
- T1 and T2 are the main policy blocks; T3 uses them in quote generation; T4 is final validation and regression sweep.
- Checkpoint C1 after T1: hard cap helper and config values are wired and unit-tested.
- Checkpoint C2 after T2: `plan_orders` holds falling BUY targets and cancels only when the cap or drift test truly says to.

## Tasks

### T1 [ ] — Hard ceiling helper + config defaults [Backend/Logic] (S)

- Target files: `core_brain/config.py`, `core_brain/risk.py`
- Build: add the always-on helper implementing the hard `$0.99` cap and the effective-cap function; leave the existing `hard_block`/`completable_pair_block` call sites intact while tightening them by the new helper.
- Verify: `tests/test_completable_pair_gate.py` and a new focused assertion that a rounded pair above `0.99` is refused by the helper even when config is looser.
- Depends on: none.

### T2 [ ] — Asymmetric BUY re-gate in `plan_orders` [Backend/Logic] (M)

- Target files: `core_brain/trader_loop.py`, `tests/test_plan_orders_asymmetric_hold.py`
- Build: restore the intended asymmetric behavior in the held-order path so a falling target inside `requote_hold_below_target` is kept, while a truly stale or rising target keeps the old cancel discipline; make the pair-cost re-gate use the same hard cap and effective cap semantics as the quote generation path.
- Verify: the existing `test_plan_orders_asymmetric_hold.py` suite, especially the held-BUY cases and the cap-edge assertions for a downward move inside the threshold.
- Depends on: T1.

### T3 [ ] — Quote generation uses the same bound in all paths [Backend/Logic] (M)

- Target files: `core_brain/quotes.py`, `tests/test_ladder_quotes.py`, `tests/test_live_quotes.py`
- Build: apply the helper in the from-mid, ladder, and legacy quote branches before submission so no path can emit a pair above `$0.99`. Keep the existing fill-quality logic and only reject the over-cap outcomes.
- Verify: `tests/test_ladder_quotes.py` and the live-quote regression file for the touched quote routing; every new case should fail without the helper placement.
- Depends on: T1, T2.

### T4 [ ] — Regression sweep + final proof [Backend/Logic] (S)

- Target files: `tests/test_completable_pair_gate.py`, `tests/test_trader_loop.py`, `tests/test_cancel_attribution.py`, `tests/test_shadow_run.py` (selected regression set)
- Build: run the focused, issue-matched suite and confirm there are no cancel-reason regressions or pair-cost escapes. No live venue commands are run in this station.
- Verify: `python -m pytest -q tests/test_plan_orders_asymmetric_hold.py tests/test_ladder_quotes.py tests/test_completable_pair_gate.py tests/test_trader_loop.py tests/test_live_quotes.py` plus any additional regression file needed for the exact touched path.
- Depends on: T1, T2, T3.

## Verification plan

- Focused gate: `python -m pytest -q tests/test_plan_orders_asymmetric_hold.py tests/test_ladder_quotes.py tests/test_completable_pair_gate.py tests/test_trader_loop.py tests/test_live_quotes.py`
- This is the exact issue-control set for the changed behavior and covers both the cancel path and the quote generation path. The full suite remains the GitHub CI merge gate.
- No live `quote`, `complete`, or dashboard START command is run in this station; the proof is the red/green test loop and the fact that the logic is isolated to quoting and planner decisions.
