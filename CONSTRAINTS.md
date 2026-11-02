# CONSTRAINTS.md — Station II quality guardrails (issue #919)

## Zero regressions
- Focused suites covering touched modules must pass before any push:
  `tests/test_config_single_source.py`, `tests/test_config.py`,
  `tests/test_config_divergence_guard.py`, `tests/test_velocity_gate.py`,
  `tests/test_movement_gate.py`, `tests/test_loose_baseline.py`,
  `tests/test_live_event_discovery.py`, `tests/test_redesigned_filter_pipeline.py`,
  `tests/test_tournament_arms_i917.py`, `tests/test_shadow_tournament.py`,
  `tests/test_trader_loop.py`, `tests/test_arm_params.py`,
  `tests/test_wide_book_trial.py`, `tests/test_live_quotes.py`,
  `tests/test_market_selection_bars.py`.
- Full `python -m pytest -q` is the GitHub CI merge gate, not a local gate.
  Do not run the full suite locally to "prove" this change.
- Every changed behaviour needs a test that fails without the change
  (TDD: write the failing test first).

## Performance / cost ceilings
- Screener: no extra venue requests per market. Live rows reuse the single
  tape read with a different window (900s vs 1800s); the recency check reads
  the already-fetched `last_trade_sec_ago`, never a new fetch.
- `_market_specs` lane filters run before the `max_markets` slice (no wasted
  prioritization), and stay O(n) list comprehensions — no new I/O.

## Anti-cheat (hard rules)
- Never skip, deselect, or weaken a failing test to get green.
- Never delete assertions or add type/lint suppressions to silence a check.
- Never invent APIs, files, or line numbers: every seam in `tasks/plan.md`
  was verified verbatim in this checkout; anything uncertain is marked
  `[UNVERIFIED]` and must be resolved from code, not assumed.
- A missing test file never justifies creating a same-named stub to satisfy
  a command: use the existing per-area test files named in the plan.

## Dependencies / scope locks
- No new external dependencies. No new top-level screener, no schema change
  (no per-trade live flag, no new arm `scope` tag — saved arm `env` is the
  arm-type marker).
- `bot_quote_book_max_spread_usd` stays `0.06`; `core_brain/risk.py` changes
  only if the exact-6¢ boundary test fails (1e-9 tolerance, one comparison).
- LIVE default: every `core_brain.order_manager` subcommand reaches the venue
  and opening commands spend money. Build verification uses shadow rehearsal
  with a separate non-production store; never touch `data/orders.db`, never
  press START, never run `quote`/`complete`/Trader loop against the venue.
