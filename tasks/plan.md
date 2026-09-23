# Plan — Issue #291: Run shadow-03 as a depth-bar trial on its own feed

Branch: `i291/run-shadow-03-as-a-depth-bar-trial-on-its-own-feed` | Issue: #291

## Classification
Size: **Large** — cross-cutting change across ranker, loop, shadow CLI, menu script, docs, plus 3+ new/extended test modules. Type: **Code** (helpers: test-driven-development, incremental-implementation; contracts: api-and-interface-design).

## Resolved open question (needs-answers)
Issue asks: $250 or a staged value from the miss distribution? Resolved from code + CodeRabbit plan: **$250 default as a launch parameter** (`-TrialDepth` / `--trial-depth`, default 250). Evidence: config keeps the shipped bar at `select_min_top3_depth_usd = 500.0` with the trial as opt-in (`core_brain/config.py:488-497`); the ranker already tags picks with `trial_depth_usd`. A later value change needs no code change.

## Personas consulted
- code-explorer (persona file read): traced ranker→feed→shadow→menu paths; findings folded into T1–T5 below.
- type-design-analyzer (persona file read): interfaces locked in Step 4; invariant = baseline byte-identical without flags, manifest absent → old resume path.

## Execution paths (explorer findings)
1. `scripts/filter_markets.py` (`RUN = ROOT/"runtime"`) publishes `markets.json`, `pipeline.json`, `market_universe.json`, `near_misses.jsonl`, `volume_near_misses.jsonl`, `ranking.marker` — all RUN-anchored, all must route via `--out-dir`.
2. `scripts/filter_loop.py` builds the ranker command (forwards configured trial flags) and writes `runtime/rerank.log` + `runtime/cycle_events.jsonl`.
3. `core_brain/shadow_run.py` resolves `markets_fn or _default_markets_fn()` for initial load and refresh; default flows through `_market_specs()` → `load_graduated_markets()` with no path — the seam for `--markets-path`.
4. `scripts/spread-hunter-menu.ps1` mints `data/NN_shadow_*.db` + `shadow-NN`, writes `runtime/shadow-session-<run-id>.json`, derives dashboard port (03 → 8803); Menu R resumes each store and registers one global `filter` entry in `runtime/processes.json`.

## Improvement proposal (adopted by default)
Store **absolute resolved paths** in the trial manifest and session mirror, so Menu R replay works regardless of working directory. Evidence (CodeRabbit plan): "Write `data/<store-basename>.trial.json` next to the store. Record `trial_depth_usd`, `ranker_out_dir`, and `markets_path`." — relative paths would break resume from a different cwd. Folded into T4/T5. No scope expansion proposed.

## Dependency graph (risk-first order)
- T1 (ranker isolation — riskiest: many RUN-anchored writes) → T2 (loop forwarding) → T4 (menu launch) → T5 (resume replay) → T6 (docs)
- T3 (shadow feed override — independent seam) → T4
- T6 depends on T4/T5 (documents final wiring)

## Tasks
### T1 [Backend/Logic] ranker `--out-dir` (M) — Depends on: none
Add `--out-dir` (default `RUN`) to `scripts/filter_markets.py`; route every RUN-anchored read/write (feed, pipeline, universe, both near-miss logs, marker) through it; create on demand; keep atomic publish, depth precedence, tagging, shipped $500 bar untouched. Verify: new `tests/test_filter_markets_out_dir.py` (trial artifacts land in out-dir; shared `runtime/` bytes identical; rows tagged `trial_depth_usd: 250`) — must fail pre-change.

### T2 [Backend/Logic] loop flag forwarding (S) — Depends on: T1
Add `--out-dir` + explicit `--trial-depth` to `scripts/filter_loop.py`; forward when given; loop logs under out-dir; CLI trial value wins over `HUNTER_DEPTH_TRIAL_USD`; byte-identical command without flags; no global config change. Verify: extend `tests/test_filter_loop.py` (forwarding, override precedence, unchanged-without-flags).

### T3 [Backend/Logic] shadow feed override (S) — Depends on: none
`_market_specs(path=None)` in `core_brain/trader_loop.py` → `load_graduated_markets(path=...)`, shape/cap unchanged; `--markets-path` in `core_brain/shadow_run.py` with precedence injected `markets_fn` → flag → default; same error behavior; print resolved path; `market_feed.py` untouched. Verify: extend `tests/test_shadow_run.py` + `tests/test_trader_loop.py` (parse/default None, temp-feed initial+refresh, cap still applies, pathless default preserved) — must fail pre-change.

### T4 [Backend/Logic] menu `shadow-trial` launch (M) — Depends on: T1, T3
Non-destructive `shadow-trial` action + menu entry in `scripts/spread-hunter-menu.ps1` (`-TrialDepth` default 250); mint 03 store/id via existing helper; no stop/wipe of 01/02; seed + loop with trial flags into `runtime/trials/<run-id>/`; shadow CLI with `--markets-path`; reuse observer/watcher/dashboard-8803/session/timebox helpers; trial screener only in session file, never the global `filter` entry. Write `data/<store>.trial.json` manifest with **absolute** `trial_depth_usd`, `ranker_out_dir`, `markets_path`; mirror in session. Verify: new `tests/test_menu_shadow_trial.py` source-contract (flags passed, no wipe/stop-all, manifest written, no global filter registration, manifest excluded from store discovery).

### T5 [Backend/Logic] manifest-driven resume (M) — Depends on: T4
`Resume-ShadowRun` reads the manifest: present → seed trial out-dir, restart trial loop, `--markets-path`, skip global `filter` registration, rewrite session with `resumed: true`; absent → current resume path byte-for-byte; same via All-runs and `-ResumeDb all` loops. Verify: extend `tests/test_menu_shadow_trial.py` (replay flags, no-manifest path unchanged).

### T6 [Docs] architecture note (XS) — Depends on: T4, T5
`docs/agents/architecture.md`: trial rankers publish to `runtime/trials/<run-id>/`, shadow reads a feed via `--markets-path`. Verify: text present; `tests/test_trial_readiness.py` untouched and green alongside focused suites.

## Checkpoints
- After T2: ranker isolation provable (trial dir has tagged feed, shared runtime clean).
- After T3: shadow reads any feed file on demand.
- After T5: full loop — trial launch, dashboard :8803, Menu R lists 01/02/03, resume replays.

## Notes
- Sub-issue mapping deferred: single-agent build, dependency graph above serves as the tracker mirror; keeps the issue tracker free of plan ceremony noise.
- Prior `tasks/plan.md` for #283 (rename) is superseded — that work is complete.
- Never touch: `core_brain/config.py` ($500 bar), `core_brain/market_feed.py`, `core_brain/trial_readiness.py`, dashboard feed/KPI sources. While 01/02 live: no fresh start, no stop without run ID.

## How to verify (hands-on, operator)
1. Menu action `shadow-trial` → `runtime/trials/shadow-03/markets.json` exists, rows carry `trial_depth_usd: 250`; `runtime/markets.json` rows do not; `runtime/near_misses.jsonl` gets no trial lines.
2. Open `http://localhost:8803` — shadow-03 dashboard quoting CIDs from the trial feed.
3. Menu R lists 01, 02, 03; resume 03 → new session file still shows trial `markets_path`.
