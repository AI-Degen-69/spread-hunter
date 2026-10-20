# Quality Guardrails & Constraints — Issue #707

Branch: `i707/validate-max-spread-from-mid-range` | Issue: `#707` (resolves ledger item O3 of #690)

## 1. Zero Regressions & Verification Gates
- **Targeted suites** (run locally during build; the full suite is the GitHub CI merge gate):
  - `python -m pytest -q tests/test_config_single_source.py`
  - `python -m pytest -q tests/test_wide_book_trial.py tests/test_config_for_display.py tests/test_redesigned_filter_pipeline.py tests/test_config_divergence_guard.py`
- **Every changed behaviour needs a test that fails without it.** The acceptance boundary, the
  rejection of `inf`/`nan`/out-of-range, the alias-naming message, and the preserved per-path
  typo rule each have one (`tests/test_config_single_source.py`, `#707` section).

## 2. Invariants
- **The environment unit does not move.** The value stays in **cents**; `1..99` cents is stored as
  `0.01..0.99`. `scripts/shadow_tournament.py:245` writes whole cents and `.env.example` ships `15`.
- **Reject, never clamp, never silently ignore.** `_bounded_float` is the only validator; the
  message names the variable the operator actually set (canonical or alias) and the value.
- **Two measures, never mixed.** `filter_common_spread_max_cents` = `0.0205` owns wide-book
  rejection. `max_spread_from_mid` is a distance-from-mid quoting window. Do **not** pass
  `max_spread_from_mid` to `pair_books_allowed()` / `book_allowed()`, and do not feed it into
  `MAX_BOOK_SPREAD`.
- **The ceiling does not move.** `0.0205`, `max_book_spread = 0.06`, `select_spread_pct = 41/120`,
  the trial bound `0.30` and `max_pair_cost` are untouchable in this change.
- **The two shipped defaults do not move.** Ranker `0.045`, engine `0.15`, pinned by
  `tests/test_config_single_source.py:525,594`. The wide-book trial is not validated here: its own
  bound (`0.0..0.30`) already keeps `max(window, wide)` inside the validated range.
- **The alias chain is untouched.** `BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID` →
  `HUNTER_MAX_SPREAD_CENTS` → `HUNTER_MAX_SPREAD`, same order, same warnings. #706 removes the
  aliases; this PR must behave identically before and after it.
- **The loader RULES stay per-path, deliberately.** Pinned is strict (a typo raises, as it always
  has). Unpinned keeps #690's rule that a *typographical* (non-number) value is inactive rather
  than enforced-at-zero, now pinned with its reason in the test file.
- **Strict file boundary.** No `dashboard/` change. Its `get_parameters` loads the engine config
  unguarded (`dashboard/server.py:4242-4246`), so an illegal value now fails that endpoint instead
  of rendering a made-up number; #709 owns making the refusal readable. No change to
  `core_brain/config.py`'s trial-knob block, and `scoring/config.py`'s copy of `_bounded_float`
  stays a copy.
- **No venue contact in tests.** Zero orders, zero `BRIDGE_CONTROL`, no dashboard START.

## 3. Anti-Cheat & Quality Rules
- No test skipping, no muted assertions, no deleted checks, no lint/type suppressions.
- Repinning `test_unpinned_bad_spread_values_are_inactive_not_enforced` and the `inf` parity case is
  a **deliberate** change of the old contract, not a weakened assertion: the ticket's AC
  ("values outside 0.01–0.99 are rejected, not silently clamped") supersedes #690's tolerance for
  bad numbers. The docstring states the reason inline.

## 4. Deliberate Decisions (do not "fix" these in Stations IV–V)
- **The `0` boundary is a rejection (option (a)).** The operator directive is "can only be
  0.01–0.99", and silently ignoring a value the operator set is the silent-lie pattern #709 exists
  to kill. `0`, `-5`, `0.5`, `100`, `inf`, `nan`, `-inf` all raise; only a non-number stays
  inactive, and only on the unpinned path.
- **The engine keeps both reads** (pinned branch + the general read that always runs). The pinned
  read's strictness is preserved rather than deleted, so the diff adds a range check and changes no
  other pinned behaviour -- including the existing duplicate `HUNTER_MAX_SPREAD` warning count.
- **A pinned typo now names the variable** (`... is not a number` from `_bounded_float`) instead of
  Python's bare `could not convert string to float`. Still a raise, better message, no test pinned
  the old text.
