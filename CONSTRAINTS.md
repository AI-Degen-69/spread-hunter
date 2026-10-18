# CONSTRAINTS.md — Issue #571: Merge the two disagreeing settings files

Locked in Station II (`ii-plan-issue`). The build (Station III) must respect every
line below. Changing a constraint requires an explicit operator decision, recorded
at the bottom of `tasks/plan.md`.

## 1. Zero regressions

- Targeted suites covering touched code must pass after the change:
  `tests/test_market_selection_bars.py`, `tests/test_config_for_display.py`,
  `tests/test_wide_book_trial.py`, plus the two new test files this plan creates.
- Broader sweep (`tests/test_pipeline_snapshot_gates.py`,
  `tests/test_ci_critical_source.py`, `tests/test_merge_floor_is_gasless.py`)
  must pass before the PR opens. The full `python -m pytest -q` suite is the
  GitHub CI merge gate, not a local loop.
- Every changed behaviour needs a test that fails without the change (baseline
  test written FIRST against unmodified code, guard test verified with a
  deliberate red-check).

## 2. Shipped values are frozen

No live or picker default may change. The five intentional differences are
preserved exactly (verified 2026-10-18 against live code):

| field | `core_brain` (live) | `scoring` (picker) |
|---|---|---|
| `bankroll_usd` | 100.0 | 1000.0 |
| `max_naked_usd` | 6.0 | 120.0 |
| `min_quote_shares` | 5 | 50 |
| `max_spread_from_mid` | 0.15 | 0.045 |
| `max_pair_cost` | 0.99 | 0.995 |

- Value changes belong to #572 / #573. This ticket moves WHERE numbers live,
  never WHAT they are.
- `objective` already agrees (`"spread_capture"` in both files); the issue text
  describing a pricing-mode disagreement is stale — no objective change allowed.
- Backward compatibility: `scoring.config.MakerConfig()` with no arguments must
  keep constructing, stay frozen, and expose every field existing importers read
  (including the 8 picker-only fields and all shared names).

## 3. Anti-cheat

- Forbid skipping/disabling tests, deleting assertions, weakening existing tests,
  or adding lint/type suppressions to get green.
- Forbid editing `tests/test_market_selection_bars.py`,
  `tests/test_config_for_display.py`, `tests/test_wide_book_trial.py`,
  `tests/test_ci_critical_source.py`, `tests/test_merge_floor_is_gasless.py`,
  `tests/test_market_quote.py` — they pin behaviour; read them, don't touch them.
- The guard test must parse the real class body with `ast` and compare real
  defaults — never assert against a copy of itself.

## 4. Dependencies and blast radius

- No new external dependencies. Standard library only (`ast`, `dataclasses`).
- No touching `data/orders.db`. No live trading, no order-opening commands.
- Protected files (read-only for this ticket): `scripts/filter_markets.py`,
  `scripts/filter_loop.py`, `scoring/markets.py`, `core_brain/markets.py`,
  `core_brain/quotes.py` LOGIC (comments only), `dashboard/`,
  `scripts/spread-hunter-menu.ps1`, `configs/`, both `load()` functions, both
  `_bounded_float` helpers, both `resolve_wide_book_trial` helpers.
- Do not add `1.645` / `1.96` literals (or comments containing them) to
  `core_brain/` — the statistical-guard surface (#558–#560 area) must not gain
  new confidence literals from this move.
- Core `ROOT` and scoring `ROOT` resolve to the same folder (verified) — the
  duplicated `db_path()` logic stays behaviour-identical; keep the scoring copy
  (a safety refusal that can break on import failure is not one).

## 5. Performance / scope ceiling

- Import time of `scoring.config` must not grow a venue or network dependency;
  the only new import is `core_brain.config` (already a local, import-safe module).
- Out-of-scope items noticed during planning go to
  `docs/issues/571-noticed-but-not-touching.md`, never into this diff.
