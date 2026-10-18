# Plan — Issue #571: Merge the two settings files that disagree with each other

Branch: i571/refactor-merge-the-two-settings-files-that-disagre | Issue: #571
Size tier: **Standard** — 3 files changed (`core_brain/config.py`,
`scoring/config.py`, `core_brain/quotes.py` comments) + 2 new test files, one
architectural decision (where the five differing values live).
Task type: **Code** (refactor) + **Debug** (dead-knob audit, resolved from code).

## Spec (embedded — Standard work, single-issue refactor)

**Goal:** `core_brain/config.py` owns every shared setting; `scoring/config.py`
imports it and keeps only picker-only fields. No shipped value changes.

**Acceptance criteria (from the issue):**
- [ ] One file owns every shared setting; the other imports it. No value changes.
- [ ] New guard test `tests/test_config_divergence_guard.py` fails when a shared
  setting is re-declared with a different value.
- [ ] Every leftover knob from the Open questions is either covered by a test or removed.
- [ ] `python -m pytest -q tests/test_market_selection_bars.py tests/test_config_for_display.py` — all pass.

**Edge cases:** frozen behaviour preserved; no-arg construction preserved;
`db_path()` identical under all three env states; derived properties
(`quote_window_frac` alias, `select_spread_frac`, latency taus) unchanged;
`filter_markets.py` `getattr` reads keep resolving.

**Out of scope:** value changes (#572, #573); close-method cleanup (#558–#560);
live trading; merging the two `load()`s / helpers; `reward_offset` fallback tests;
pair-cost / missing-side refusal tests.

## CodeRabbit plan intake (read once, costed once)

- **Adopted:** task skeleton (baseline test BEFORE the edit → refactor →
  guard + red-check); frozen-subclass seam; keep both `load()`s and helpers
  untouched; keep the four legacy knobs with clarifying comments + coverage;
  protected-files list; `TOURNAMENT_PRESETS`-style table pattern for the profile.
- **Rejected:** the "93 shared / 101 fields / six differences" counts — verified
  against live code on 2026-10-18: **74 shared, 5 differences** (`objective`
  already agrees). The "guard scans `core_brain/` for stray literals" claim —
  no such guard found; treated as unconfirmed, and the plan instead forbids
  adding `1.645`/`1.96` to `core_brain/`.
- **`[UNVERIFIED]` at intake, now resolved from code:** the ask-branch
  reachability questions — resolved (see "Open questions — resolved" below).
  Nothing remains `[UNVERIFIED]`.

## Open questions — resolved from code (no operator input needed)

1. **`ticks_below_ask` / `tick_size` reachable on the mid-priced live path?**
   NO. `decide_quotes` (`core_brain/quotes.py:750`) returns early via
   `_decide_quotes_from_mid` for `objective in ("spread_capture", "rewards")`
   — both shipped objectives. The ask-branch readers (`quotes.py:793,831,872`)
   run only under the dead `"pair"` objective. Plan: keep as fallback, mark
   clearly, cover with a test.
2. **Does anything still read `quote_window_frac` on the live path?** The live
   evaluator (`quotes.py:1044` + `live_exec.decide`) passes `window_pct=None`
   for both production callers, and the from-mid path never reads the window.
   `quote_window_frac` is an alias property for `quote_window_pct` (both files),
   and `enforce_quote_window` only gates the dead ask-branch. Plan: keep,
   comment, cover — remove nothing.

## Verified ground truth (2026-10-18, live code)

- `core_brain` 114 fields / `scoring` 82 fields / **74 shared / 5 diffs**
  (table in CONSTRAINTS.md §2).
- Scoring-only (8, all picker): `enforce_max_queue_minutes`,
  `select_max_queue_minutes`, `select_min_activity_ratio`,
  `select_min_movement_usd`, `select_min_range_cents`,
  `select_movement_window_sec`, `select_velocity_gate_enabled`,
  `select_volatility_window_sec`.
- Core-only (40, incl. `size_mode` and the risk-cap family) — inherited, unread
  by the picker; allowed as extras.
- `TOURNAMENT_PRESETS` exists at `core_brain/config.py:1095` — the
  `PICKER_PROFILE` table follows a real local pattern.
- Both `ROOT`s resolve to the repo root; both `db_path()`s are identical.

## Design decision

`PICKER_PROFILE` table in `core_brain/config.py` (near `TOURNAMENT_PRESETS`)
holding the five current picker values; `scoring.MakerConfig` becomes a frozen
subclass of core `MakerConfig` redeclaring ONLY the 8 picker-only fields (own
literals) + the 5 profile fields (defaults read from `PICKER_PROFILE`).
Fallback (keep 5 literals in scoring + allowlist) recorded below; primary stands
unless the operator rejects it before build.

## Dependency graph

- T1 → T2 → T3 (T3 needs the refactored class to guard).
- T4 depends on T2 (config comments land with the refactor) but is otherwise
  independent of T3 — T3 and T4 may run in either order after T2.

## Tasks

### T1 [x] — Baseline single-source test (write FIRST, green on old code) [M, Backend/Logic]
- **Target files:** `tests/test_config_single_source.py` (new).
- **Build:** hardcoded baseline table generated once from the CURRENT scoring
  class (82 entries: 74 shared + 8 picker-only) + core table for the 5 diffs;
  `test_scoring_defaults_unchanged`, `test_core_defaults_unchanged`,
  `test_derived_values_unchanged` (taus + `db_path()` under 3 env states via
  `monkeypatch`), `test_loader_output_unchanged` (env stripped),
  `test_scoring_config_is_frozen` (`FrozenInstanceError`).
- **Skill:** test-driven-development. **Depends on:** nothing.
- **Verify:** new file passes on UNMODIFIED code (run before any config edit).

### T2 [x] — Core-owns-shared refactor [M, Backend/Logic]
- **Target files:** `core_brain/config.py` (+`PICKER_PROFILE`, 1 comment, zero
  default changes), `scoring/config.py` (frozen subclass; keep 8 picker-only +
  5 profile fields; delete the 69 copied identical fields + copied latency
  properties; keep `ROOT`, `load()`, `_bounded_float`,
  `resolve_wide_book_trial`, `db_path()`; update the copied-helper comment).
- **Skill:** incremental-implementation. **Depends on:** T1.
- **Verify:** T1 baseline passes AFTER the edit with zero table changes; manual
  check that `filter_markets.py` `getattr` reads resolve and neither class has
  `merge_gas_usd`.

### T3 — Divergence guard + red-check [S, Backend/Logic]
- **Target files:** `tests/test_config_divergence_guard.py` (new; the issue's
  required guard filename).
- **Build:** shared-default comparison (profile fields assert against
  `PICKER_PROFILE`, all others against core — message names field + both
  values); profile-is-exactly-five-known-fields; `ast` class-body check
  (annotated names ⊆ 8 picker-only + 5 profile; picker-only names ∉ core
  fields; profile defaults not plain literals); subclass assertion.
  Red-check: temporarily redeclare `max_book_spread` with a wrong value,
  confirm the guard fails, revert.
- **Skill:** test-driven-development. **Depends on:** T2.
- **Verify:** guard green on new code + observed red during the temporary
  mis-declaration.

### T4 — Legacy ask-knob comments + coverage [S, Debug]
- **Target files:** `core_brain/config.py` (4 knob comments),
  `core_brain/quotes.py` (1 branch comment, logic untouched),
  `tests/test_legacy_ask_quotes.py` (new).
- **Build:** each comment states the knob reads ONLY on the ask-branch under
  `objective == "pair"`, that shipped objectives never reach it, and that the
  live evaluator passes `window=None`; `tick_size` comment distinguishes it
  from live `price_tick`. Test (via `dataclasses.replace` with
  `objective="pair"`): one-tick-under-ask pricing + window gate/refusal cases +
  mid-path-ignores-window for both shipped objectives.
- **Skill:** debugging-and-error-recovery. **Depends on:** T2.
- **Verify:** new test passes; `tests/test_market_quote.py:98–164` (window-None
  assertion) untouched and passing.

## Checkpoints

- **CP1 (after T2):** baseline green before AND after with an untouched table —
  the move changed location, not values.
- **CP2 (after T4):** guard green + red-check observed; legacy knobs commented
  and covered; acceptance suites green.

## Improvement proposal (adopt-by-default: simplification)

> **Evidence (verbatim):** `scoring/config.py:783` — `def quote_window_frac(self)
> -> float:` + `"""Backward compatibility alias for quote_window_pct (#632)."""`
> (identical alias exists at `core_brain/config.py:1043`).
> The issue's Open question 2 asks "Does anything still read `quote_window_frac`
> on the live path?" — since it is an alias property, not a separate knob, the
> plan covers the underlying `quote_window_pct`/`enforce_quote_window` pair and
> explicitly asserts the alias equals the field in T1's derived-values test,
> rather than treating the alias as a fourth independent knob. No scope added.

## Rejected / fallback record

- Fallback design (5 literals in scoring + allowlist, no `PICKER_PROFILE`):
  rejected as primary because the station goal names core as single source for
  ALL shared settings; kept as fallback if the operator rejects the new core
  table before build.
- "Use core values for the 5 diffs": rejected — changes shipped values owned by
  #572/#573.
- Factory-function replacement for scoring `MakerConfig`: rejected — breaks
  no-arg construction pinned by `test_market_selection_bars.py`.
- Deleting the ask-branch: rejected — the branch still executes under
  `objective == "pair"`; removal would change behaviour.
- New `reward_offset`-fallback / pair-cost-refusal / missing-side tests:
  excluded per CodeRabbit out-of-scope + issue scope; noted in
  `docs/issues/571-noticed-but-not-touching.md`.

## Handoff

- Next: **`/iii-build-plan auto`** on branch
  `i571/refactor-merge-the-two-settings-files-that-disagre`.
- `tasks/todo.md` mirrors T1–T4 with CP1/CP2 gates.
