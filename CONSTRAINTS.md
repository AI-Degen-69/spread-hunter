# Quality Guardrails & Constraints — Issue #690
Branch: `i690/apply-spread-bar-at-market-filter` | Issue: `#690` (resolves #674 ledger item N2)

## 1. Zero Regressions & Verification Gates
- **Targeted suites** (run locally during build; the full suite is the GitHub CI merge gate):
  - `python -m pytest -q tests/test_config_single_source.py`
  - `python -m pytest -q tests/test_wide_book_trial.py`
  - `python -m pytest -q tests/test_redesigned_filter_pipeline.py`
  - `python -m pytest -q tests/test_market_selection_bars.py`
  - `python -m pytest -q tests/test_dashboard_server.py`
- **Every changed behaviour needs a test that fails without it.** The unpinned read, the trial
  `max(window, wide)`, and the 50/53 rejection + reason path each require one.
- **Existing pinned cases stay byte-identical** — they are the proof AC-2 held.

## 2. Invariants
- **Two measures, never mixed.** `filter_common_spread_max_cents` = `0.0205` owns wide-book
  rejection. `max_spread_from_mid` is a distance-from-mid quoting window. Do **not** pass
  `max_spread_from_mid` to `pair_books_allowed()` / `book_allowed()`, and do not feed it into
  `MAX_BOOK_SPREAD`.
- **The ceiling does not move.** `0.0205`, `max_book_spread = 0.06`, `select_spread_pct = 41/120`,
  and the trial bound `0.30` are untouchable in this change.
- **Pinned = strict, unpinned = tolerant.** Pinned keeps `float(v) / 100.0` and its raise. Unpinned
  mirrors `core_brain/config.py:636-642` exactly — including that `inf` passes parity. Do not
  "improve" the engine's rule here.
- **Order matters.** The lifted read must precede the wide-book trial block so `max(window, wide)`
  sees the loaded value on unpinned runs.
- **One warning per load.** `HUNTER_MAX_SPREAD is deprecated (#632); set
  BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID instead.` — byte-identical, emitted once, now on both paths.
- **No ranker admission change.** Eligibility, scoring, ordering, and rejection reasons must be
  identical before and after for any given book.
- **Strict file boundary.** Do **not** touch `core_brain/trader_loop.py`, `core_brain/order_manager.py`,
  `core_brain/quotes.py`, `dashboard/static/app.js`, or any endpoint. `scripts/filter_markets.py`
  changes are **comments only** — `rg -n "MAX_BOOK_SPREAD" scripts/filter_markets.py` must show the
  same six call sites afterwards.
- **No venue contact in tests or verification.** Zero orders, zero `BRIDGE_CONTROL`, no dashboard
  START. Tests stay offline; `tests/conftest.py` blocks network.

## 3. Anti-Cheat & Quality Rules
- No test skipping, no muted assertions, no deleted checks, no lint/type suppressions.
- Do not weaken an existing assertion to make the new read pass — if a pinned test breaks, the branch
  is wrong, not the test.
- No new external dependency.
- **Operator verification is hands-on only.** Never ask the operator to run `pytest`, `gh`, or `git`.
  Point them at `runtime/previews/spread-follow-up/market_universe.json`, the parity one-liner in
  `tasks/plan.md` §8, and the untouched `runtime/markets.json`.
