# Plan — #589 [REFACTOR] Delete the retired short-window ladder path

Branch: i589/remove-short-window-ladder | Issue: #589
Worktree: .worktrees/spread-hunter-i589

## Tier & type

**Large** — cross-cutting removal: 1 engine module, 4 scripts, 9 dedicated suites, ~14
edited files (engine, config schema, dashboard, docs). Mechanical deletion, but every
strategy entry point must be traced before the module can go. No new dependencies.
Task type: **Code** (primary) + **Docs**. Helper skills: `test-driven-development`,
`incremental-implementation`.

## Discovery (Step 0A intake)

**CodeRabbit plan comment (61.8K chars):** adopted — its file-level map, Design Choice 1
(keep `_route_pair`, strip only its ladder selection), Design Choice 2 (delete
`route_quotes`), the keep-taxonomy rules, and its probe-helper warning. Rejected — nothing
material; its "redirect non-ladder `route_quotes` callers" step came back empty (verified:
the only callers are `core_brain/ladder.py:105` and the deleted `tests/test_ladder_quotes.py`).
`[UNVERIFIED]` items all resolved below. Read once; do not re-read.

**Verified against live code (all symbols confirmed at these sites):**
- `route_quotes` callers: `core_brain/ladder.py:105`, `tests/test_ladder_quotes.py` only → delete outright.
- `_route_pair` callers: `single_buy_saver.py:1705` (inside the `ladder_mode` bypass — dies
  with it), `tests/test_refill_after_exit.py:201,209,227`, `tests/test_single_buy_saver.py:1736`
  (both must keep passing) → keep the function.
- Ladder config fields (exactly five): `config.py:1066-1070` — `ladder_mode`,
  `ladder_rungs`, `ladder_exit_sec`, `ladder_budget_usd`, `ladder_open_window_sec`;
  plus `validate_ladder` `config.py:1149-1163`.
- `is_ladder_pair`: `ladder.py:33` (def), `single_buy_saver.py:44` (import), `:1697`
  (bypass), `:2041` (`_route_pair` ladder selection).
- Probe helpers `mean_ci90` / `_prop_ci90_diff`: defined `scripts/ladder_probe.py:187,201`,
  consumed only by `tests/test_541_audit_goldens.py:217-224`. The
  `statistical_analytics.py` equivalents give different numbers — inline, do not substitute.
- Live "ladder" references that are NOT the strategy and must survive: `snapshot_ladder`
  (order-book depth) `shadow_exec.py:1075-1099`; risk-sizing comments `config.py:127,840`,
  `risk.py:112`; `ladder_exit` taxonomy strings (`close_methods.py`, `kpi.py`,
  `statistical_analytics.py`, analytics buckets, dashboard labels).

**Resolved open questions:**
1. `docs/explanations/btc-eth-ladder-strategy.html` — **keep**, add a retirement notice
   (issue default; dated explanations are knowledge per Station VI convention).
2. Acceptance-criterion contradiction — see the improvement proposal below; the literal
   `grep "ladder"` can never reach zero without destroying the order-book depth ladder and
   the taxonomy chain's close-method names.
3. Merge order vs. the #558-560 taxonomy chain — state it in the PR body; if
   `close_methods.py` conflicts at merge, keep the taxonomy chain's version, then mark the
   `ladder_exit` writer legacy.
4. `_route_pair` deletion is explicitly **out of scope** (refill tests still cover it) —
   possible follow-up, not this issue.

**Persona skips (recorded):** `code-explorer` and `type-design-analyzer` personas exist at
`~/.agents/agents/` but were skipped — execution paths were already traced via the targeted
caller greps above, and no new interfaces are being locked (pure deletion; `_route_pair`
signature preserved).

## Spec (Small/Large inline spec — no SPEC.md)

**Goal:** remove the retired short-window ladder strategy (#325) end to end: module,
scripts, suites, config fields, and every engine branch, while ordinary quoting, the unified
lifecycle, carried pair IDs, `_route_pair`'s generic exit logic, and the `ladder_exit`
taxonomy string (owned by #558-560) are all preserved bit-for-bit in behavior.

**Acceptance (refined — see proposal):**
- No short-window-ladder strategy references remain: `grep -rn
  "ladder_mode\|is_ladder_pair\|discover_ladder_series\|route_quotes\|decide_ladder_quotes"
  core_brain scripts tests --include="*.py"` → empty.
- `py -3.12 -m pytest -q tests/test_shadow_run.py tests/test_trader_loop.py
  tests/test_single_buy_saver.py tests/test_config_for_display.py` → pass.
- `py -3.12 -m pytest -q tests/test_market_quote.py tests/test_refill_after_exit.py
  tests/test_exit_price_realism.py` → pass.
- Order-book depth ladder (`snapshot_ladder`), risk-sizing ladder code, and all
  `ladder_exit` taxonomy strings still present and untouched.

**Out of scope:** the `ladder_exit` close-method name and its rescue bucket; the idea doc
`docs/ideas/short-window-ladder.md`; issue #325; any new behavior.

## Tasks

Dependency graph: T1 (engine entry points) → T2 (`_route_pair` internals) → T3
(taxonomy note) → T4 (scripts + suites + survivor repairs) → T5 (docs/dashboard) → T6
(acceptance sweep). T3 depends on T1 only; T4 on T1+T2; T5 on T4; T6 on all.

### T1 [M] [Code] — Delete `core_brain/ladder.py` and strip engine entry points
- Delete `core_brain/ladder.py`.
- `core_brain/config.py`: remove the five `ladder_*` fields (`:1061-1070` block incl. its
  comment header) and `validate_ladder` (`:1149-1163`).
- `core_brain/markets.py`: delete `discover_ladder_series` (`:352`) and imports used only
  by it.
- `core_brain/quotes.py`: delete `decide_ladder_quotes` and `route_quotes` (no non-ladder
  callers exist — verified).
- `core_brain/single_buy_saver.py`: remove the `is_ladder_pair` import (`:44`) and the
  bypass in `_manage_single_leg_positions_locked` (`:1697-1705`); all pairs go through the
  unified lifecycle.
- Depends on: —
- Verify: `grep -rn "ladder_mode\|is_ladder_pair\|discover_ladder_series\|route_quotes\|decide_ladder_quotes" core_brain scripts --include="*.py"` → empty;
  `py -3.12 -m pytest -q tests/test_config_for_display.py tests/test_empty_markets_routine.py tests/test_market_quote.py`.

**Checkpoint 1** — after T1+T2: ordinary quoting path imports clean; focused engine suites green.

### T2 [M] [Code] — Strip ladder selection from `_route_pair`
- `core_brain/single_buy_saver.py:2041`: keep the function; always use `single_buy_exit`,
  grace from `min(single_buy_grace_sec, pairs_exit_window_sec)`, timer reason
  `grace_expired`. Preserve completion, adverse-drift, grace-hold, and `trigger_bid`
  recording exactly.
- Depends on: T1
- Verify: `py -3.12 -m pytest -q tests/test_single_buy_saver.py tests/test_refill_after_exit.py tests/test_exit_price_realism.py tests/test_grace_sweep_report.py`.

### T3 [XS] [Code] — Mark the `ladder_exit` writer legacy
- `core_brain/close_methods.py`: change only the writer note — legacy, no current writer.
  Keep `LADDER_EXIT`, the `ladder_exit` string, and `RESCUE_METHODS` membership untouched
  (old databases still contain these rows).
- Depends on: T1
- Verify: `grep -n "LADDER_EXIT\|ladder_exit" core_brain/close_methods.py` still present;
  `py -3.12 -m pytest -q tests/test_close_method_taxonomy.py`.

### T4 [M] [Code] — Delete scripts + dedicated suites, repair survivors
- Delete `scripts/ladder_probe.py`, `scripts/ladder_live_books_trial.py`,
  `scripts/ladder_shadow_rehearsal.py`, `scripts/ladder_tape_collect.py` and the nine
  `tests/test_ladder_*.py` suites.
- Repair `tests/test_541_audit_goldens.py`: inline `mean_ci90` and `_prop_ci90_diff` (move
  from the probe script verbatim — the `statistical_analytics.py` equivalents give different
  results) before the probe is gone.
- Rename the `ladder-live` fixture label in `tests/js/live_state_harness.cjs` and the
  ladder-named cases in `test_dashboard_run_switcher.py`, `test_scan_state_shadow.py`,
  `test_live_state_language.py`, `test_trader_loop.py` (renames only — keep assertions).
- Depends on: T1, T2
- Verify: `py -3.12 -m pytest -q tests/test_541_audit_goldens.py tests/test_dashboard_run_switcher.py tests/test_scan_state_shadow.py tests/test_live_state_language.py tests/test_trader_loop.py`.

**Checkpoint 2** — after T4: every surviving suite green, zero ladder suites remain.

### T5 [S] [Docs] — Comment-only edits + current guidance
- Reword stale ladder-path comments: `shadow_exec.py:348`, `shadow_run.py:684`,
  `trader_loop.py:675,2405-2406`, `statistical_analytics.py:526`. Leave all order-book
  depth-ladder code and risk-sizing ladder comments untouched.
- Update `docs/agents/first-run.md` and `docs/data_inventory.md`; update
  `dashboard/static/data_analysis.js` ladder references.
- Add a retirement notice to `docs/explanations/btc-eth-ladder-strategy.html` (keep the doc).
- Depends on: T4
- Verify: `py -3.12 -m pytest -q tests/test_docs_layout.py`; `grep -rn "ladder" core_brain scripts --include="*.py"` shows only order-book depth + taxonomy strings.

### T6 [XS] [Code] — Acceptance sweep
- Run the refined acceptance grep and the issue's four suites + the focused quote-lifecycle
  suites. Confirm `snapshot_ladder`, risk-sizing ladder code, and all `ladder_exit`
  taxonomy strings still present.
- Depends on: T5
- Verify: the three acceptance commands from the Spec section above.

## Improvement proposal (Step 5)

**Adopt-by-default (edge-case hardening).** Evidence, verbatim from the issue's acceptance
criteria: *"`grep -rn \"ladder\" core_brain scripts tests --include=\"*.py\"` returns no code
references (only the `ladder_exit` taxonomy strings the taxonomy chain owns)."* That criterion
is unreachable against live code: `core_brain/shadow_exec.py:1075-1099` uses `snapshot_ladder`
for order-book depth, `core_brain/risk.py:112` ("tail of the ladder terminate") and
`config.py:840` are risk-sizing prose — none are the short-window strategy. Proposal: the
acceptance check targets the enumerated strategy symbols
(`ladder_mode`/`is_ladder_pair`/`discover_ladder_series`/`route_quotes`/`decide_ladder_quotes`
+ module/scripts/suites) instead of the literal string. Folded into the Spec above. A scope
expansion (deleting the order-book depth ladder too) was considered and rejected — that would
break `shadow_exec` pricing.

## Merge order (must appear in the PR body)

This branch deletes ladder-path code from `single_buy_saver.py`, `close_methods.py`, and
`statistical_analytics.py`, which the #558-560 taxonomy chain also touches. Land behind
#558-560; on conflict in `close_methods.py`, keep the taxonomy chain's version, then re-apply
the legacy-writer note.
