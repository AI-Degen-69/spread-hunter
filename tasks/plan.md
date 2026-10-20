Branch: i708/unify-spread-docstring | Issue: #708

# Plan - #708 unify the max_spread_from_mid description, triage rebate dead code

**Size tier:** Standard. Task type: **Docs** (primary) + **Code** (one dead-module
deletion, three focused test guards). No behaviour change, no interface change.

**Stack:** Python 3 (pytest), docs (Markdown/HTML). Repo: `AI-Degen-69/spread-hunter`.

## Intake note (CodeRabbit plan on the issue)

The issue carries a CodeRabbit plan comment. What was adopted, what was rejected:

- **Adopted:** the shared description's content (distance from the book midpoint to our
  resting quote; dollars on the 0-1 scale, cents in the env; the engine refuses/skips
  and clamps the offset; explicitly not a market-selection filter, with
  `filter_common_spread_max_cents` named as the wide-book bar); the deliberate 0.15 /
  0.045 split; the "do not blanket-delete rebate" rule and the keep-list (KPI fields,
  PnL credit replay, `CREDIT_TYPES`, `maker_rebates`, fee sign-safety comments, the
  validation artifacts and prompt, the historical measured-run comments, the accurate
  dashboard line); delete `scoring/allocate.py`; the three new assertions
  (`total_with_rebate == realized_pnl`, `rebate_est is None`, the allocator guard);
  the ledger format; "keep every visible message string unchanged".
- **Rejected:** the plan's instruction to edit the O4 status cell in
  `docs/issues/690-noticed-but-not-touching.md` -- that file no longer exists. It was
  deleted in e2f648e, which pruned every closed issue's ledger. Resurrecting a pruned
  ledger to change one cell is churn against a deliberate cleanup. #690 O4 is closed by
  this ticket's own ledger instead (see Task 4).
- **Rejected:** the plan's "two different concepts share this field name" framing in
  `tests/test_wide_book_trial.py:154-157` (picker = venue REBATE-qualification window,
  engine = resting-offset ceiling). The issue's acceptance criterion is ONE accurate
  description; keeping two roles in the test comment would fail it.
- **[UNVERIFIED] -> resolved:** the plan cites `scoring/config.py:52-55`, `:181-182`,
  `:215-223`, `core_brain/config.py:255` and `:486-491`, `core_brain/quotes.py:16`,
  `:106-117`, `:213`, `:263-285`, `:505-507`, `:806-808`, `:842-843`,
  `core_brain/trader_loop.py:399-415`, `core_brain/pnl_crosscheck.py:9` and `:16`,
  `tests/test_wide_book_trial.py:129/141-146/152-157/170/200`, `core_brain/kpi.py:2349-2356`.
  All exist in the current tree; the line numbers were re-derived against the files on
  this branch (they match). `scoring/allocate.py` exists (325 lines, no importer).
  `docs/issues/690-noticed-but-not-touching.md` does NOT exist (pruned, above).

## Spec (Standard tier: no separate SPEC.md)

**Goal.** One field, one description, everywhere it is documented. One ledger, one
decision per surviving `rebate` reference.

**Acceptance criteria**

1. `scoring/config.py` and `core_brain/config.py` carry a byte-identical description of
   `max_spread_from_mid`, and a test fails if they ever differ.
2. The description names the field's real role (distance from the book midpoint to our
   resting quote) and does not describe it as a rebate window or a market-selection
   filter.
3. No default, env var, parse rule, validation or visible message changes.
4. `rebate_est is None` and `total_with_rebate == realized_pnl` hold in the KPI report
   and in the statistical-validation report.
5. `scoring/allocate.py` is gone and a guard fails if it comes back.
6. Every `rebate` reference is kept (with a written reason) or removed; the ledger says
   which, per path.

**Out of scope.** Rewording the visible "reward window" / "score N" quote messages,
removing `quotes.reward_score()`, renaming `reward_offset` / `min_reward_offset`,
adding the literal `REBATE` to `ACTIVITY_TYPES`, the dashboard's hardcoded "(15c)" card
text, and anything touching the money path.

## The canonical description (authored once, mirrored byte-identically)

```
# THE QUOTE WINDOW. `max_spread_from_mid` is the largest allowed distance
# between the book midpoint and our resting quote. Prices are dollars on the
# 0-1 scale; the env value is cents. The quoting engine refuses or skips a
# quote outside the window and clamps the dynamic offset to it. It is NOT a
# market-selection filter -- `filter_common_spread_max_cents` (2.05c) is the
# wide-book rejection bar (see CONSTRAINTS.md). The two shipped defaults are
# deliberate: 0.15 for the engine, 0.045 for the picker, which the ranker does
# not read; the picker copy exists to keep the two loaders' rules aligned.
```

Authored in `scoring/config.py` above the field; mirrored byte-identically in
`core_brain/config.py` above the field.

## Tasks

### Task 1 - one description, in both configs and in the engine's prose

- **Size:** M. **Tag:** [Docs] / [Backend/Logic] (comments only).
- **Files:** `scoring/config.py`, `core_brain/config.py`, `core_brain/quotes.py`,
  `core_brain/trader_loop.py`.
- **What:**
  - `scoring/config.py`: replace the "4.5c maximum offset from mid ..." line with the
    canonical block; keep the `min_quote_shares` and `max_pair_cost` lines.
  - `core_brain/config.py`: the same canonical block above the field (line 255) and the
    same wording in the property/alias comment near 486-491.
  - `core_brain/quotes.py`: module docstring (line 16), `reward_score()` docstring
    (106-117, `v` is the quote window, the score is a legacy diagnostic that feeds no
    sizing decision), the `_decide_quotes_from_mid()` docstring (263-285: production
    uses `"spread_capture"`, `"rewards"` is a legacy alias dispatch still accepts, drop
    the "earning rent" passage), and the stale "reward window" wording in the comments
    at 486, 554, 560, 806-808, 842-843.
  - `core_brain/trader_loop.py`: `_market_cfg()` docstring (399-415) - the
    `"spread_capture"` pin and the engine's single-source window from #594.
- **Depends on:** none.
- **Verification:** `python -m pytest -q tests/test_config_single_source.py
  tests/test_wide_book_trial.py` plus the new guard from Task 2.

### Task 2 - guard the description's equality

- **Size:** S. **Tag:** [Backend/Logic].
- **File:** `tests/test_config_single_source.py`.
- **What:** a test that extracts the canonical block (anchored on the
  `# THE QUOTE WINDOW.` line) from `scoring/config.py` and from `core_brain/config.py`
  and asserts the two are equal and non-empty. Written first: it fails against the
  current tree (the two files describe the field differently) and passes after Task 1.
- **Depends on:** Task 1.
- **Verification:** `python -m pytest -q tests/test_config_single_source.py`.

### Task 3 - rebate triage: correct the stale prose, delete the dead allocator

- **Size:** M. **Tag:** [Docs] / [Code].
- **Files:** `core_brain/pnl_crosscheck.py` (module docstring only), `docs/agents/strategy.md`
  (21-28, 80-84), `README.md` (24), `docs/explanations/shadow-run-flow.html` (149),
  `docs/ideas/polymarket-toolkit-survey.md` (135), delete `scoring/allocate.py`.
- **What:**
  - `pnl_crosscheck.py`: the docstring explains that real venue credits are kept
    separate from trading cashflow, that `cross_check()` accepts an optional
    `maker_rebates` adjustment, and that the CLI does not pass it today (not even with
    `--inclusive`). No executable line changes: `CREDIT_TYPES`, `ACTIVITY_TYPES` and the
    parameter stay exactly as they are.
  - `docs/agents/strategy.md`: the "Maker rebates are extra" section stops claiming the
    bot qualifies for the liquidity-reward program and stops citing the removed
    `config.rebate_rate`; real venue credits are a side accrual kept out of trading PnL.
    The allocator paragraph goes with the deleted module; the `quotes.reward_score()`
    sentence stays.
  - `README.md`: accrued rewards on graduated markets are $0.00 and the KPI report does
    not estimate them.
  - `shadow-run-flow.html`: the real reason for skipping a side is that the quote is too
    far from mid to fill (or settled-outcome pricing) - not "no rebate".
  - `polymarket-toolkit-survey.md`: `fee_rate` is a taker-completion cost input; the
    rebate-estimation rationale is obsolete.
  - Delete `scoring/allocate.py` (no tracked importer, no test, retired reward-pot
    model).
- **Depends on:** Task 1 (the docs quote the same field description).
- **Verification:** `python -m pytest -q tests/test_rewards_purge.py
  tests/test_pnl_crosscheck.py tests/test_venue_fees.py` plus the new guards (Task 4).

### Task 4 - the guards that prove nothing changed, and the ledger

- **Size:** S. **Tag:** [Backend/Logic] / [Docs].
- **Files:** `tests/test_rewards_purge.py`, `tests/test_milestone7_telemetry.py`,
  `tests/test_stat_validation_artifacts.py`, `docs/issues/708-noticed-but-not-touching.md`.
- **What:**
  - `test_rewards_purge.py`: assert `importlib.util.find_spec("scoring.allocate")` is
    `None` (mirrors the existing `scoring.rewards` guard at 90-100). Fails before the
    deletion.
  - `test_milestone7_telemetry.py`: `total_with_rebate == realized_pnl` next to the
    existing `rebate_est is None` assertion.
  - `test_stat_validation_artifacts.py`: top-level `report["rebate_est"] is None` and
    `report["total_with_rebate"] == report["realized_pnl"]`.
  - `docs/issues/708-noticed-but-not-touching.md`: the ledger, in the format of the
    pruned #690 ledger - a reference triage table (path, reference, decision,
    justification) covering every kept reference, every changed file, and the removed
    module, plus a noticed-but-not-touching table for the six out-of-scope items
    (visible "reward window" / "score N" strings, `reward_score()`, the
    `reward_offset` / `min_reward_offset` renames proposed in
    `docs/ideas/variable-names-refactor.md`, the literal `REBATE` missing from
    `ACTIVITY_TYPES`, and #690 O1's hardcoded "(15c)" dashboard text).
- **Depends on:** Task 3.
- **Verification:** the eight focused suites listed under "Verification run".

## Verification run (targeted only; CI is the merge gate)

```
python -m pytest -q tests/test_config_single_source.py tests/test_wide_book_trial.py \
  tests/test_rewards_purge.py tests/test_milestone7_telemetry.py \
  tests/test_stat_validation_artifacts.py tests/test_pnl_crosscheck.py \
  tests/test_venue_fees.py tests/test_redesigned_filter_pipeline.py
```

## One improvement proposal (adopted)

**Add the guard test from Task 2.** Evidence, verbatim: the issue's acceptance criterion
is "`max_spread_from_mid` has a single consistent docstring across all modules", and
#690 O4 recorded the opposite outcome - the field was documented three ways and stayed
that way for a release, because nothing compared the two copies. A comment fix with no
guard drifts again. Adopted by default (a new test, no behaviour change, no scope
expansion).

## Out-of-scope findings

Recorded in `docs/issues/708-noticed-but-not-touching.md` (Task 4), not fixed here:
the visible "reward window" / "score N" quote messages, `quotes.reward_score()`, the
`reward_offset` / `min_reward_offset` renames, the literal `REBATE` missing from
`ACTIVITY_TYPES`, and #690 O1's hardcoded "(15c)" dashboard card text.
