# Plan - #619 [ADD] Stop-loss architecture: dynamic modes plus entry-relative emergency floor

Branch: i619/add-stop-loss-architecture-dynamic-modes-plus-entr | Issue: #619
Size: Standard (lifecycle + config + 2 callers + exit + tests + docs; one policy decision) | Type: Code/Debug
Labels: ready-for-agent (no `quick-fix` label → Step 0C gate skipped, no lane offered)
No `needs-answers` label and no Open-questions section → no operator questions.

## CodeRabbit plan intake (issue comment, read once — echo ignored)

- **Adopted:** 3-task skeleton and order (policy+config+wiring → FOK exit+e2e →
  docs); affected-file seams (all verified live 2026-10-16); TDD case tables
  (entry 0.10→0.025, $0.01 floor, higher-of, refusals, endpoints).
- **Rejected:** over-split phases merged into 3 tasks; `FAK`/both-branches FOK
  variants (live-only FOK is the minimal explicit change); stream-driven path
  (separate work, polling passes stay).
- **Resolved from code:** `_bounded_float` is INCLUSIVE (`lo <= value <= hi`,
  config.py:1162+, rejects nan/inf) → percentages need an explicit `> 0`
  check; `OrderType` import pattern confirmed
  (trader_loop.py:2326-2327, use 2385-2386); `MarketOrderArgsV2` already
  imported in single_buy_saver.py:974; `test_stop_loss_config.py` does not
  exist (to be created); `shadow_run.py:354-359` is a grace COMMENT only.
- **Still [UNVERIFIED] at build:** whether installed `MarketOrderArgsV2`
  supports `.order_type` (library not installed locally — verify, don't assume).

## Spec (embedded; ideas file decides)

Static `$0.15` stop fires on entries `0.10–0.14` the moment they fill — a
booked loss by construction. Replace with:
`active_trigger = max(strategy_trigger, max(entry*(1-emerg_pct/100), 0.01))`;
under `NONE`, `active_trigger = emergency floor`. Fire when
`held_bid <= active_trigger`, entry = `held_average`, branch stays before
settlement fallback and escalation. Defaults: `NONE`, `drop_cents=0.05`,
`drawdown_pct=10.0`, `emergency_drawdown_pct=75.0`.

## Interface contract (locked before logic)

- `stop_trigger(entry, mode, drop_cents, drawdown_pct, emergency_drawdown_pct)
  -> (trigger: float, source: str)` in `single_leg_lifecycle.py`; source is
  `strategy` or `emergency`; `Decimal(str(...))`, no epsilon.
- `transition(..., stop_loss_mode="NONE", drop_cents=0.05, drawdown_pct=10.0,
  emergency_drawdown_pct=75.0)` — `hard_stop_bid` REMOVED; invalid inputs →
  `refused`. `evaluate()` takes the same four and forwards.
- `MakerConfig`: new defaults/fields as above; REMOVE `single_buy_grace_sec`,
  `stop_loss_trigger_pct`, `stop_loss_trigger_usd_cents`, preset cents
  (config.py:1083-1144), hybrid grace (:1121); rewrite behavior comment (:935-938).
- Env: `HUNTER_STOP_LOSS_MODE` trim+upper, only `NONE/ABSOLUTE_DROP/
  PERCENTAGE_DROP` else `ValueError` listing choices; `HUNTER_DROP_CENTS`
  [0.01,0.90], `HUNTER_DRAWDOWN_PCT`/`HUNTER_EMERGENCY_DRAWDOWN_PCT` (0,100]
  via `_bounded_float` + explicit `> 0`. Retire the 3 old vars with the existing
  warning pattern (name replacement + units, no value conversion).
- `exit_single_buy()`: `order_type=OrderType.FOK` on the NON-shadow branch only.

## Improvement proposal (adopted by default — edge-case hardening)

Spec §2 formulas spell the modes `ABSOLUTE`/`PERCENTAGE` while the mode list
spells them `ABSOLUTE_DROP`/`PERCENTAGE_DROP`. Accept ONLY the `_DROP` spellings
and refuse `ABSOLUTE`/`PERCENTAGE` (with legacy `usd`/`pct` and `bogus`) —
evidence: spec *"ABSOLUTE_DROP and PERCENTAGE_DROP cannot be active at the same
time"* vs formulas *"stop_loss_mode = "ABSOLUTE""*. Silent aliasing is unsafe:
the retired `usd`/`pct` knobs used different units. Rejected alternative: none
(no scope expansion proposed).

## Tasks (risk-first; each independently reviewable with own tests)

Depends on: T1 → T2 → T3 (T2 needs T1's trigger semantics; T3 needs final behavior).

- [ ] T1.1 [Backend/Logic] M — Stop policy + config + both callers with tests.
  Files: `core_brain/single_leg_lifecycle.py`, `core_brain/config.py`,
  `core_brain/trader_loop.py` (:1555), `core_brain/single_buy_saver.py`
  (:1768,:1806), `scripts/verify_loose_baseline_tape.py:175`,
  `scripts/stream_market_decisions.py:167`, `statistical_validation_run/run.py:331`,
  `core_brain/shadow_run.py:354-359` (comment), `tests/test_single_leg_lifecycle.py`,
  new `tests/test_stop_loss_config.py`, updates to `test_aged_out_rescue.py:194-196`,
  `test_dynamic_offset.py:205-240`, `test_exit_price_realism.py:38-55`,
  `test_shadow_tournament.py:597-606`. TDD: NONE floor (0.10→0.025, bids
  0.026/0.025/0.024), $0.01 floor (entry 0.02; 100% drawdown), ABSOLUTE_DROP
  (0.50-0.05=0.45), PERCENTAGE_DROP (0.60-10%=0.54), both higher-of cases,
  0.12/0.14 regression, refusals (modes, ranges, nan), endpoints 0.01/0.90.
  Verify: focused `pytest tests/test_single_leg_lifecycle.py -q` + config tests.
- [ ] T2.1 [Backend/Logic] S — Explicit live FOK + e2e rework. Files:
  `core_brain/single_buy_saver.py` (:974-988 exit), `tests/test_dual_stop_loss.py`
  (sentinel order-type default, ≥5-share bid depth, sell/no-sell boundaries incl.
  0.10→0.025 and floor-wins cases, close reason + explicit FOK via registry
  accessor; age/drift/opposite-ask/cap cases under NONE). Verify: `pytest
  tests/test_dual_stop_loss.py tests/test_single_buy_saver.py
  tests/test_hard_stop_fresh_exit.py tests/test_exit_price_realism.py -q`.
- [ ] T3.1 [Docs] XS — Current docs to entry-relative stop. Files: `README.md:29`,
  `docs/agents/strategy.md:103`, `docs/agents/architecture.md:69`,
  `docs/agents/safety.md:143,169-177` (walkthrough step 4: default trigger 25%
  of entry, $0.01 minimum), `docs/explanations/hard-stop-stale-marks.html`
  (:87,:97,:116,:125 — only lines stating $0.15 as CURRENT rule; past-incident
  prose stays). No tests; verify by `git diff --stat` + read-back.

Checkpoints: after T1.1 (acceptance pair green) → after T2.1 (exit suites green)
→ after T3.1 (diff read-back). Mode A: one-line progress per checkpoint.

## Untouched files (do not open for edits)

`docs/ideas/stop_loss_architecture_logic_requirements.md` (read-only),
`data/orders.db`, `docs/archive/`, `docs/runs/`, `docs/issues/`,
`configs/tournaments/i492_tournament-1_arms.json`,
`scripts/tournament_cockpit_server.py`, `scripts/grace_sweep_report.py`,
`scripts/tournament_hub.py`, `dashboard/`, `core_brain/shadow_exec.py`,
`core_brain/unhedged_stop_loss.py`, `core_brain/order_manager.py`,
`core_brain/risk_guardian.py`, `core_brain/statistical_analytics.py`,
`tests/test_sale_close_pricing.py` (no guard widening),
`scripts/shadow_tournament.py` (only if its tests fail).

## Run-environment caveats (owner: planner, when: 2026-10-16)

- `py_clob_client_v2` not installed locally → `MarketOrderArgsV2.order_type`
  support is verified at build, never assumed (Class 2 if absent: leave the
  `create_and_post_market_order(order_type=...)` kwarg, note the fallback).
- Hands-on verification is `shadow_run` with a FRESH non-production DB only;
  `data/orders.db` is never written/deleted; no live quote/complete/Trader/START.
- `HUNTER_*` env names, units, ranges, defaults = working assumptions awaiting
  operator confirmation of the spec file.

## Base freshness (proven, not assumed)

`main` 649 commits, `main...origin/main` 0↔0 after `git fetch origin`
2026-10-16, tree clean on branch creation. Stale and never used as bases:
i603-plan files (replaced wholesale here — #603 CLOSED), `scrub_game_clock_mechanics`,
`sync-main`, `i586/topnav-status-redesign` branches.
