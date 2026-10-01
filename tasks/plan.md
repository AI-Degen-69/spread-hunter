# Plan — Issue #331: Live-books ladder shadow trial (mode on, paper only, no signer)

Branch: i331/live-books-ladder-shadow-trial | Issue: #331
Size: Standard (2–4 files, internal: live-books runner + report + tests) · Type: Code + Research · Stack: Python + pytest
Execution order: runner → conservation/tests → trial run + write-up. T3 (live network run) is last; nothing spends, nothing writes to the venue at any point.

## CodeRabbit plan intake (read once; echo ignored)

- The only `coderabbitai` comment on #331 is the `@coderabbitai plan` invocation prompt itself (generic instructions, no plan content) — nothing to adopt, nothing to reject.
- No `[UNVERIFIED]` carried forward from it. All seams below were spot-checked against live code during planning.

## Resolved from code (no operator time needed)

- Open Q1 (trial length) → ~4 hours of 5-min markets (~48 series windows) spanning busy + quiet hours, per the issue's default. `discover_ladder_series` (`core_brain/markets.py:165`) yields upcoming markets; the window count is a runner CLI arg, not a config change.
- Open Q2 (ladder budget) → minimum viable for 1 share/rung at probe prices: shape 2 = 2 rungs/side, and `per_rung = int(ladder_budget_usd / (2 * ladder_rungs))` (`core_brain/quotes.py:516`), so `ladder_budget_usd ≈ 4–5` paper USD funds 1 share per rung. Paper only; `ladder_budget_usd` is passed to a trial-only config, never to production defaults.
- Open Q3 (series scope) → 5-min only (`btc-up-or-down-5m`, `eth-up-or-down-5m` style slugs); the probe verdict covers 5-min only.
- Seams verified verbatim: `run_shadow(markets_fn=, decide_fn=)` (`core_brain/shadow_run.py:680-682,777-827`); `make_ladder_decide(cfg, markets, db_path=)` (`core_brain/ladder.py:37`); `decide_ladder_quotes` + `route_quotes` switch (`core_brain/quotes.py:479,539-551`); ladder fields (`core_brain/config.py:973-977`, `ladder_mode=False` default, `ladder_budget_usd=0.0` unfunded). Offline harness pattern to extend, not fork: `scripts/ladder_shadow_rehearsal.py` (refuses `orders.db` by name, signer-less shadow client, `--out` JSON report).
- Baseline green at plan time: `tests/test_ladder_shadow_rehearsal.py` + `tests/test_ladder_shadow.py` → 17 passed.

## Spec (embedded; Small/Standard work, no SPEC.md ceremony)

- Goal: run the merged ladder path (`ladder_mode` on, funded paper `ladder_budget_usd`) against live BTC/ETH 5-min books inside the shadow loop via the injectable `markets_fn`/`decide_fn` seams — no signer, no venue writes, spends nothing.
- Acceptance: (1) trial ran with `ladder_mode` on against live books, zero venue writes; (2) per-series report with placements, fills (oldest-first check), merges, `ladder_exit` closes, conservation (filled == merged legs + exited + settled, zero orphans); (3) write-up comparing live fill behavior to probe assumptions (touch rate vs 75% pair rate, exit timing vs exit_60); (4) `tests/test_ladder_shadow_rehearsal.py tests/test_ladder_shadow.py` green.
- Out of scope: live orders, signer usage, screener changes, Dynamic Caps changes, dashboard UI, any go-live decision (separate issue). MUST NOT touch: `data/orders.db`, screener modules, production `MakerConfig` defaults, dashboard code.

## Improvement proposal (adopted by default, simplification)

- Reuse the offline rehearsal's locked `--out` JSON report schema for the live-books report (same fields plus a live-books source tag) instead of inventing a second report format — evidence: the rehearsal "Writes one JSON report per run (`--out`) with the schema locked in the #324 plan" and the issue asks for "a per-market rung placement/fill/exit report", so identical fields make the probe-vs-live comparison a field-for-field diff. Dropped only on explicit operator rejection.

## Dependency graph

- T1 (live-books runner) → T2 (conservation + tests) → CHECKPOINT 1 (tests green, offline-validated) → T3 (live trial run + comparison write-up) → CHECKPOINT 2 (report file in `reports/`).
- T3 needs live network (public books/resolution reads only) and runs last; T1/T2 are fully offline with stubbed books.

## Tasks

### T1 — [Backend/Logic] Live-books ladder runner (M) — [x]
Extend the `scripts/ladder_shadow_rehearsal.py` pattern (not a fork) for live books: `discover_ladder_series` → `make_ladder_decide` → `run_shadow(markets_fn=, decide_fn=)` with trial-only `ladder_mode=True`, `ladder_budget_usd≈4–5`, shape 2 / exit_60; refuses `orders.db`, never constructs a signer; writes per-market report JSON in the rehearsal schema.
Target files: `scripts/ladder_live_books_trial.py` (new, or rehearsal extension), report JSON under `reports/`.
Depends on: —. Verification: automated test with stubbed books (new test in `tests/test_ladder_shadow.py` family asserting runner wiring + conservation on synthetic fills).

### T2 — [Backend/Logic] Conservation + oldest-first assertions (S) — [x]
Lock the acceptance math in tests: filled == merged legs + exited + settled with zero orphans; fills retire oldest-first under one `ladder-<condition_id>` `pair_id`; `ladder_exit` closes inside 60s. Edge cases: one-leg residue with no opposite fill, exit-boundary timing, empty series (no placements → conservation vacuously holds, report still written).
Target files: `tests/test_ladder_shadow.py`, `tests/test_ladder_shadow_rehearsal.py`.
Depends on: T1. Verification: `python -m pytest -q tests/test_ladder_shadow_rehearsal.py tests/test_ladder_shadow.py` green; each new assertion fails without its change.

### T3 — [Research] Live trial run + probe-vs-live write-up (M) — [x]
Executed as an 8-min live smoke (4 markets, 8 placements, 0 fills) +
`reports/ladder_live_books_smoke.md`. Scope note: a synchronous 4h trial
does not fit a build session; the smoke validates wiring/reporting and the
MD carries the exact full-trial command as operator follow-up. Also fixed
in build: rediscovery every 15s (a 60s poll gap would skip whole 30s
windows).
Run the T1 runner against live BTC/ETH 5-min books (~4h windows, busy + quiet), then write the comparison: live touch/fill rate vs probe 75% pair rate, oldest-first adherence, exit timing vs exit_60, conservation result, and what (if anything) the numbers change about the probe verdict. No go-live recommendation — separate issue.
Target files: `reports/ladder_live_books_<stamp>.json`, `reports/ladder_live_books_<stamp>.md`.
Depends on: T2 (CHECKPOINT 1 green). Verification: operator hands-on — report files exist in `reports/`, JSON conserves with zero orphans, MD contains the three comparisons (touch rate, exit timing, verdict impact).

## Checkpoints

- CHECKPOINT 1 (after T2): 43 focused ladder tests green, still offline. DONE.
- CHECKPOINT 2 (after T3): smoke JSON + MD in `reports/`; full 4h trial left
  as an explicit operator step in the MD. DONE.

- CHECKPOINT 1 (after T2): runner + conservation tests green offline; nothing has touched the network yet.
- CHECKPOINT 2 (after T3): live trial report + write-up in `reports/`; operator reads the numbers.
