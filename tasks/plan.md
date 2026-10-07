# Plan — #402: Full lifecycle quoting: read market state right, quote-fill-merge loop until resolved or discarded

Branch: i402/improve-full-lifecycle-quoting-read-market-state | Issue: #402

- Tier: **Large** — cross-cutting (trader_loop, quotes, markets,
  market_resolution, order_manager, single_buy_saver, stray_guard, markout,
  paired_shadow, shadow_run, filter_markets), one new module, no new
  dependency, no schema migration.
- Task type: **Code + Debug**.
- Stack: Python, pytest (`python -m pytest -q <file>` for focused runs).
- Skills: `test-driven-development` (RED-first regressions),
  `incremental-implementation` (thin slices, atomic commits),
  `debugging-and-error-recovery` (reproduce-then-fix on the BO3/404 episodes).
- Spec: `SPEC.md` (supersedes #401). Guardrails: `CONSTRAINTS.md`.
- CodeRabbit plan intake (read once; rendered copy only, HTML echo ignored):
  - Adopted: 4-phase shape; new `core_brain/market_lifecycle.py`; enum entries
    + markers + `hold_expired`; resolved-guard at Trader admission/visit, poll
    consumers, markout/paired terminal-first; sweeper extra-candidate set;
    `lifecycle_stop` on-change-only rows; per-token backoff; full-cycle
    rehearsal; borrowed test assertions for T1–T5; out-of-scope list.
  - Rejected/trimmed: 13-task split merged into 5 atomic tasks (Rule 4 —
    over-split); line numbers treated as hints, re-verified at build time.
  - `[UNVERIFIED]` at plan time, resolved from code: `quotes.py:321-329` band
    ✓, `TERMINAL_REFUSAL_MARKERS` + `_classify_refusal`
    (`trader_loop.py:102-117`) ✓, `REFUSED_HOLD_GRACE_CYCLES = 3` ✓,
    `MarketEventRecord.reason_code` + `log_market_event`
    (`order_registry.py:706-716, :1328`) ✓, `parse_end_state` /
    `fetch_market_end_state` / `sweep_market_resolutions`
    (`market_resolution.py:191, :305, :687`) ✓, UMA parsing (`:133-134`) ✓,
    all 11 regression test files present ✓. Still for build to confirm:
    `single_buy_saver.py` rescue/discovery line ranges, `stray_guard.py`
    427-434, `markout.py` 242-400/361-365, `paired_shadow.py` ~845,
    `filter_markets.py` 1178-1241/1640-1655 row-builder/ranking ranges.
  - Drift found while verifying: none material — cited seams exist where
    named. `market_lifecycle.py` confirmed absent (new file, no clash).
- Skipped personae (recorded, not simulated): `code-explorer` — every cited
  seam verified by direct read/grep; a trace pass would duplicate evidence.
  `type-design-analyzer` — no new domain model; contract frozen below and in
  CONSTRAINTS.md.

## Interface lock

New (`core_brain/market_lifecycle.py`):
- `LifecycleStop` enum. Codes: `resolved`, `market_dropped`,
  `decided_by_price`, `settled_book`, `countdown_expired`, `market_exited`,
  `unfunded`, `fill_cap_reached`, `hold_expired`. Each entry: stable store
  code, `ends_lifecycle` flag (True ONLY for `resolved`, `market_dropped`),
  quote-refusal marker (exact substrings from today: "decided market",
  "settled book", "t_remaining", "market exited",
  "unfunded by the allocator", "fills for this market").
- `classify_refusal(text) -> LifecycleStop | "transient"` (case-insensitive).
- `resolved_condition_ids(registry) -> set[str]` (lowercase cids, one read).

Changed (signatures otherwise frozen):
- `trader_loop._classify_refusal` delegates to the classifier;
  `TERMINAL_REFUSAL_MARKERS` deleted (no second copy).
- `sweep_market_resolutions(..., extra_candidates=())` — empty default.
- Markout settlement cache key becomes (condition, token).
- Stop rows: `MarketEventRecord(kind="lifecycle_stop",
  reason_code=<enum code>, reason=<text>)` via existing `log_market_event`.

Frozen: `quotes.py` refusal strings, `orders.cancel_reason` values,
`decide_quotes`/`route_quotes` shapes, `live_fill_engine`, money levers
(CONSTRAINTS.md), manual live merge.

## Improvement proposal (adopted — simplification, evidence-based)

> Define the dead-book backoff tracker next to its sole owner
> (`order_manager`), not in the new shared module — keep
> `market_lifecycle.py` to stop-reasons + the resolution lookup only.
> Evidence for: the plan itself scopes the tracker to one lifecycle —
> "Create one tracker per `poll` invocation in `order_manager.py`. Retain it
> across cycles like `aged_out_state_cache` and pass it to book reads in
> `single_buy_saver.py` and `stray_guard.py`." A single-consumer helper needs
> no shared-module surface. Adopted by default (simplification, no scope
> change). If build finds a second owner, the tracker moves to the shared
> module and this note is updated.

## Dependency graph

- T1 → T3, T1 → T4 (enum + classifier + lookup unblock loop and poll guards).
- T2 ∥ T1 (series parser independent; merges at T5).
- T3 + T4 → T5 (rehearsal needs loop and poll guards in place).
- Checkpoint C1 after T2 (live series quotable, refusals named).
  Checkpoint C2 after T4 (dead books silent everywhere). Then T5.

## Tasks

### T1 [x] — Shared lifecycle facts + truthful resolution reads [Backend/Logic] (M)
- Target files: `core_brain/market_lifecycle.py` (new), `core_brain/trader_loop.py`
  (classifier delegation, delete markers), `core_brain/market_resolution.py`
  (`parse_end_state` open-row rule, `record_failed` action,
  `resolved_condition_ids` helper).
- Build: enum + `classify_refusal` + `resolved_condition_ids` per Interface
  lock; elapsed end date resolves ONLY when the row does not explicitly
  report `closed is False` + `acceptingOrders is True`; cancel/insert failure
  returns `record_failed`, never `resolved_recorded`.
- Verify: `tests/test_trader_loop.py` — parametrized refusal→entry mapping,
  cancel/hold + ends-lifecycle disposition, exactly one `lifecycle_stop` row
  per code across repeats, enum completeness, 3-transient-refusals →
  `hold_expired` cancel; `tests/test_market_resolution.py` — elapsed-date
  open row stays unresolved, unknown-`closed` elapsed date still resolves.
- Depends on: none.

### T2 [x] — Series-state reading + band exemption [Backend/Logic] (M)
- Target files: `core_brain/markets.py` (parser + `LiveMarket` optional field),
  `core_brain/quotes.py` (`:321-329` exemption only), `scripts/filter_markets.py`
  (keep `sportsMarketType` in row builder, same predicate at ranking gate).
- Build: scope (series iff `moneyline` + best-of > 1; `child_moneyline` =
  single; else unknown), best-of from score suffix / `k/N` period / question
  text, games-remaining from wins-to-clinch; live-series predicate requires
  `live=true` + `ended=false` + series scope + games left + fresh evidence
  (else fail closed).
- Verify: parser vectors (NHL, tennis, finished CS2, `8-1|2-0|Bo5`) in
  `tests/test_live_event_discovery.py` + ranking-retains-series test;
  `TestFuriaQuoteClock` extension — live BO3 w/ game left quotes across
  rotations, same-mid single-game refused `decided_by_price`, unknown/stale
  refused, near-endpoint refused `settled_book`.
- Depends on: none (parallel with T1).

### T3 [x] — Trader respects true state, names every stop [Backend/Logic] (L)
- Target files: `core_brain/trader_loop.py` (resolved guard in `_market_specs`
  + `_visit_one` before any fetch, series attach after `fetch_market`, suspect
  tracking, `lifecycle_stop` on-change-only, shutdown reasons),
  `core_brain/market_resolution.py` (sweeper `extra_candidates`),
  `core_brain/shadow_run.py` (suspect set, shutdown reason in heartbeat).
- Build: per-rotation `resolved_condition_ids`; skips fetch nothing; open
  orders on skipped-resolved cids exit via dropped-market cleanup with reason
  `resolved`; suspect = book-fetch error / `settled_book` /
  `countdown_expired`, confirmed via venue state + backoff only; per-cid
  last-stop memory; shutdown (`once`/deadline/interrupt/lock-lost) logged
  separately from market stops.
- Verify: resolved cid through rotations → empty refresh → reappearance →
  restart on same temp DB, `fetch_books` counter = 0 for resolved tokens while
  an unrelated market quotes; `lifecycle_stop` rows correct per code.
- Depends on: T1. Checkpoint C1 after T2+T3 review.

### T4 [x] — Poll path + secondary readers go quiet on dead books [Backend/Logic] (M)
- Target files: `core_brain/order_manager.py` (per-cycle resolved set, owns
  backoff tracker), `core_brain/single_buy_saver.py` (skip resolved in
  discovery + rescue before any fetch; log once `resolved`; no
  buy/sell/cancel, inventory preserved), `core_brain/stray_guard.py` (omit
  resolved tokens), `core_brain/markout.py` (terminal-first: persisted
  resolution before books; 1.0/0.0 winner, pending w/o books if unknown;
  cache key → (condition, token)), `core_brain/paired_shadow.py` (skip
  resolved reads).
- Build: backoff tracker lives in `order_manager` (per proposal); post-failure
  `fetch_market_end_state` at most once per window; 404 never resolves;
  rescue verdict order + closed/accepting checks preserved.
- Verify: `tests/test_aged_out_rescue.py` — resolved partial inventory (fresh
  + aged fills): zero book/buy/sell/cancel, inventory preserved; fail-then-
  succeed book read retries via backoff with no invented resolution;
  `tests/test_markout_maturity.py` — winner + loser one pass, both row
  orders; shadow multi-rotation per-token book counts = 0 post-resolution
  (markout `full_book` patched to a counter).
- Depends on: T1. Checkpoint C2 after T4 review.

### T5 [ ] — Full-cycle shadow rehearsal on one market [Backend/Logic] (M)
- Target files: `tests/test_shadow_run.py` (new rehearsal), minimal
  inventory/attribution fix ONLY if re-quote after merge is blocked (no cap,
  gate, or live-merge change).
- Build: from `_canonical_book` + `_seed_single_buy` + scripted tape + sleep,
  over rotations of one cid: real shadow orders → paper fills both legs →
  `shadow_merge` close removes both legs → later rotation quotes fresh; no
  duplicate resting orders or merge closes. Fill-cap config sufficient for the
  cycle without touching defaults.
- Verify: rehearsal green + full targeted sweep: `test_trader_loop`,
  `test_shadow_run`, `test_market_resolution`, `test_aged_out_rescue`,
  `test_markout_maturity`, `test_in_play_gate`, `test_live_e2e_lifecycle`,
  `test_dynamic_risk_caps`, `test_completable_pair_gate`.
- Depends on: T2, T3, T4.

---
Supersedes: `tasks/plan.md` for #401 (merged as `609c9ec`); that plan's work
is done, its branch merged. `tasks/todo.md` likewise re-issued for #402.
