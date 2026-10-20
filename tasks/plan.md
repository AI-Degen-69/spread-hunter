Branch: i706/remove-legacy-max-spread-env-aliases | Issue: #706

# Plan - #706 retire the legacy max-spread env names

Size: Standard (2 production files, 2 test files, 2 live docs, 1 ledger row) | Type: Code/Docs
Shape A: alias deletion only - no restructuring, no new abstraction.

## Environment intake

- Confirmed on `origin/main` (`710acc4`): five production sites, exactly as the ticket says -
  `core_brain/config.py:608-621` and `:633-655` (two blocks, each `_env_get(canonical,
  "HUNTER_MAX_SPREAD_CENTS")` + an `os.environ.get("HUNTER_MAX_SPREAD")` fallback + a
  warning) and `scoring/config.py:203-218` (one three-name `_env_get` + a warning).
- `_env_get` (core_brain/config.py:944) is shared by every other knob; it is **not** changed
  here, so blank-means-unset survives. Reading it with the canonical name alone returns
  `(val, canonical)` for a non-blank value and `(None, None)` otherwise.
- `_bounded_float` (core_brain/config.py:956, twin at scoring/config.py:83) is untouched; it
  is what carries `#707`'s 1..99-cent rejection and the message. The `#707` matrix tests
  (tests/test_config_single_source.py:599-691) are untouched by this plan.
- CodeRabbit plan comment (1, 50k chars) - intake costed once:
  - **Adopted:** canonical-only reads in both loaders; keep the two engine blocks separate
    (its Design Choice 3, independently confirmed by the #632 ledger); delete both warnings
    rather than reword (Design Choice 4); drop the old names from the test matrix rows and
    `_clear_execution_env`; move the trial tests to the canonical name.
  - **Rejected, with reason:** its Phase 3 asks for the glossary row to be *reworded* ("only
    name is canonical; the two old names were removed") and for a `## How to verify` block
    that exports the old names - both violate "zero mention of old names" and the grep bar.
    Its note that `docs/agents/python-conventions.md:87` lists the old names is false on live
    code (line 87 names the canonical variable only); its four suggested ledger rows
    (duplicate parse, `inf` acceptance, `15c` card text, scoring fallback) are all already
    closed by #632/#707/#705/#709, so none is re-recorded.
  - **Stayed [UNVERIFIED] -> resolved:** "add an inert-name test that names the old vars" -
    resolved against the grep bar: the test uses a near-miss spelling instead, see
    `CONSTRAINTS.md` tension 2.
- `tests/conftest.py` scrubs every `HUNTER_*`/`BOT_*`/`FILTER_*` per test, so a new test that
  sets any name is deterministic regardless of the developer's shell.
- `docs/issues/707-noticed-but-not-touching.md` N3 (glossary drift) is this ticket's row:
  flip status to `fixed #706`, keep the text.
- Runtime caveat: the harness gave this session `gentle-wolf` (detached at `66f35fa`, one
  commit behind); the ticket's own worktree `C:/Users/Tiger/.worktrees/spread-hunter/i706`
  already held this branch at `origin/main` and was clean, so the session moved here and
  `git rev-list --count HEAD..origin/main` prints 0. Nothing was discarded.

## Tasks

- T1 [Backend/Logic] `core_brain/config.py` - both spread blocks read
  `_env_get("BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID")`; delete the `sp_name`/`sp_general_name`
  plumbing, the two `os.environ.get("HUNTER_MAX_SPREAD")` fallbacks and both warnings; keep
  the strict pinned parse, the tolerant general parse, the `#707` `_bounded_float` calls and
  the comments that explain them. Depends on: none. Verify: focused pytest (RED first - the
  existing alias/warning tests fail against the new code).
- T2 [Backend/Logic] `scoring/config.py` - one canonical-name `_env_get`; delete the
  `sp_name == ...` warning branch and the `window_name` plumbing; the three `_bounded_float`
  call sites name the canonical variable. Depends on: T1 (loader parity). Verify: focused.
- T3 [Backend/Logic] `tests/test_config_single_source.py` - spread row in
  `EXECUTION_CANONICAL_ENV` loses its legacy list; the matrix test handles a row with no
  legacy names (blank canonical -> shipped default); `_clear_execution_env` drops the old
  names; delete `test_unpinned_legacy_spread_warns_once` and
  `test_spread_window_rejection_names_the_alias_that_supplied_it`; the unpinned-read test and
  the deprecation-warning table keep only canonical/other-variable cases. Add
  `test_a_name_that_is_not_the_canonical_one_leaves_the_window_shipped` (near-miss spellings,
  both loaders, pinned and unpinned). Depends on: T1, T2. Verify: focused.
- T4 [Backend/Logic] `tests/test_wide_book_trial.py` - `_load_with` stops clearing the old
  names; the widening test sets 20c through the canonical name and its comment is reworded.
  Depends on: T1, T2. Verify: focused.
- T5 [Docs] `.env.example:47` loses the "(deprecated aliases: ...)" tail, keeps the 1..99 /
  non-number text; `docs/agents/glossary.md` loses the spread row; the #707 ledger row N3
  flips to `fixed #706`. Depends on: T1. Verify: grep bar.
- T6 [Docs] `docs/issues/706-noticed-but-not-touching.md` - the one out-of-scope row this
  ticket sees: every other env var still carries its own legacy alias chain. Depends on: none.
- T7 [Process] per-issue files replaced wholesale for #706; full suite once before the push;
  PR with `## Why` / `## Test output` / `## How to verify` / `Closes #706`; one CodeRabbit
  round; merge on green CI; closeout by detach + branch deletion. Depends on: T1-T6.

## Checkpoints

- After T1+T2: `git grep -n HUNTER_MAX_SPREAD -- core_brain scoring` is empty and the focused
  suite fails only on tests that assert the removed behaviour.
- After T3+T4: focused suite green; `git grep -n HUNTER_MAX_SPREAD -- tests` is empty.
- After T5+T6: whole-repo grep bar (minus `docs/issues/**`) is empty.

## Improvement proposal (one, adopted)

Simplification, folded into T1/T2: the three `..._name` locals exist only to name whichever
variable won, and with one name left they are dead weight - the literal goes where the local
was, so `_bounded_float` still names the variable in its error. No scope expansion proposed.
