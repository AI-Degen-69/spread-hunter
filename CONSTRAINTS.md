# Constraints - #572 (one spread ceiling from picking to placing)

Branch: i572/use-one-spread-limit-picking-to-placing | Issue: #572

Replaces #576-scoped file wholesale (Step 0A.0: stale issue scope).

## Scope lock
- One spread ceiling owned by the live safety gate `max_book_spread = 0.06`
  (`core_brain/config.py:176`, mirrored in `scoring/config.py:115`).
- Market picking derives from it via a documented fraction
  (`select_spread_frac`), yielding exactly `0.0205` at baseline. No second absolute.
- The wide-book trial keeps working from its single knob
  (`HUNTER_WIDE_BOOK_TRIAL`).
- Boundary tests stay pinned; effective accept/refuse behavior is unchanged.

## Mathematical invariant (non-negotiable)
- `round(0.06 * select_spread_frac, 6) == 0.0205` exactly under default config.
- Canonical fraction: `41.0 / 120.0` (= 0.0205 / 0.06 in real arithmetic).
- `tests/test_market_selection_bars.py:131-132` pass WITHOUT relaxing market quality.

## Trial invariant (non-negotiable)
- Passing the wide-book trial value must adjust the full chain coherently:
  live gate, picker bar, and the `max(window, wide)` reward-window widening.
- No orphaned knobs: after consolidation exactly ONE absolute (`max_book_spread`)
  plus the fraction may exist in each config module.

## Ownership (answers the issue's open question; binds #571)
- The LIVE SAFETY GATE owns the absolute number (`max_book_spread`).
  Rationale: it is the gate that refuses an uncloseable book, and the trial
  already sets it directly.
- Picking takes a SHARE of it (`select_max_book_spread` becomes derived).
- `max_spread_from_mid` is NOT part of the book-width chain: it is the
  score-window width `v` (core_brain default 0.15) / venue rebate window
  (scoring default 0.045). Two different concepts share the field name;
  renaming is out of scope (see 572-noticed-but-not-touching.md).
- #571 (settings-file merge) MUST NOT be done here. #571 imports the unified
  property/fraction terms verbatim: `max_book_spread` owns, `select_spread_frac`
  is the picker share, `select_max_book_spread` is the derived bar.

## Forbidden
- Changing live order placement logic or the pair-cost arithmetic
  `round(p_up + p_down, 4) <= round(cfg.max_pair_cost, 4)`.
- Loosening or tightening effective behavior (same books admitted/refused).
- Merging the two config files (that is #571).
- Touching `data/orders.db`; zero live trader calls.

## Targeted suites (agent runs silently; never operator steps)
- `python -m pytest -q tests/test_market_selection_bars.py tests/test_wide_book_trial.py`
- Regression watch: `tests/test_config_for_display.py tests/test_unified_universe.py`
  `tests/test_loose_baseline.py tests/test_reward_offset_override.py`
  `tests/test_queue_clear_gate.py tests/test_risk.py`
- Full `python -m pytest -q` is the CI merge gate, not a local loop.

## Out-of-scope home
- `docs/issues/572-noticed-but-not-touching.md` — every entry with owner + when.

## Anti-cheat
- No skipped/disabled tests, no weakened assertions, no relaxing boundary pins.
- Every changed behavior ships with a test that fails without the change.

