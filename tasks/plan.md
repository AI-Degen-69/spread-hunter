Branch: i646/unified-reset-path | Issue: #646

# Plan — #646 unified reset path with filter re-qualification

## CodeRabbit plan intake (read once, recorded here so later stations never re-read it)

- **Adopted:** new `core_brain/lifecycle_reset.py` module with caller-injected
  ports (keeps policy pure, avoids import cycles); strict two-token balance
  read with explicit-zero-only-after-complete-read; `fstat()` mtime freshness
  from the same open file; generation-conditional registry ops; retry of
  pending resets from the manager pass (no new scheduler); list-subclass
  metadata carrier preserving the no-arg `markets_fn` contract; guarded final
  entry check preserving hedge intents. Three phases → compressed into the
  task list below.
- **Rejected:** per-row `published_at` field and `{rows, published_at}` wrapper
  (breaks list readers / valid `[]`); deleting the mark on exclusion (market
  must stay visible until #621); broad `except` anywhere.
- **[UNVERIFIED] at plan time, resolved from code:** `save_lifecycle_state`
  is UPSERT-only with no deleter (`order_registry.py:1140` — confirmed, no
  `DELETE FROM single_leg_lifecycle` in repo); `lifecycle_mutation_guard`
  exists (`single_buy_saver.py:125` — confirmed); skip at `:1692`
  (`last_fill_ms <= latest_close_ms` — confirmed, fills timestamp compared
  before inventory); assumed-flat fallback when `venue_positions is None`
  (`:1823-1846` — confirmed); merge writes close via `_submit_and_log`
  without touching lifecycle (confirmed); `record_shadow_merges` at
  `shadow_exec.py:1672` (confirmed, line drifted from `:1669`); no publish
  timestamp in `market_feed.py` (confirmed, `load_graduated_markets` at
  `:307`). `SingleLegPosition.opposite_token_id` — flagged by CodeRabbit as
  nonexistent; verified during build (Task 5).

## Spec (Small-spec-in-plan; repo has no root SPEC.md convention)

**Goal:** one guarded reset for live merges, shadow merges, stop exits +
filter re-qualification gate. **Acceptance:** the 8 boxes in the issue body.
**Out of scope:** tiered poller (#621, closed), repricing, slippage
measurement → ledger rows in `docs/issues/646-noticed-but-not-touching.md`.

## Interfaces (locked before logic)

- `ResetPorts` (frozen dataclass, injected fns): `read_inventory`,
  `fill_watermark`, `active_order_ids`, `cancel`, `verify_cancelled`,
  `reconcile_fills`, `read_balances`. `ResetOutcome` frozen: status ∈
  `RESET | RESIDUAL | LATE_FILL | UNAVAILABLE | REFUSED` + residuals + reason.
- Registry: `begin_lifecycle_reset(cid, reason) -> generation`;
  `finish_lifecycle_reset(cid, generation, reset_ts_ms)` (atomic);
  `abandon_lifecycle_reset(cid, generation)`; `requalification_marks()`
  (sqlite errors propagate); `clear_requalification(cid, generation)`;
  `record_requalification_exclusion(cid, generation, publication_ns)`.
  Table `lifecycle_requalification(cid PK lowercased, status, generation,
  reset_ts_ms, reason, excluded_publication_ns, updated_ts_ms)`.
- `load_graduated_snapshot(path)` → `(rows, published_mtime_ns, raw_cids)`;
  `load_graduated_markets` stays rows-only wrapper. `_market_specs` returns
  list subclass carrying `(published_mtime_ns, raw_cids)` or a plain list.
- Entry skip records `LiveFleetResult(status="SKIPPED",
  why="awaiting_requalification")`.

## Improvement proposal (adopt-by-default, simplification)

> **Adopt CodeRabbit's separate-module location** (`core_brain/lifecycle_reset.py`
> instead of growing `single_leg_lifecycle.py`).
> Evidence, issue Scope §1: "beside the lifecycle policy in
> `core_brain/single_leg_lifecycle.py` or in a small new `core_brain/` module
> if that file would become too large" — the issue leaves the location open,
> and the new module keeps the pure policy file import-clean
> (`single_buy_saver` imports it lazily). Folded into Task 2.

## Tasks

- [x] T1 [Backend/Logic, S] Registry reset state (`order_registry.py`).
  Additive `lifecycle_requalification` table + 6 generation-conditional ops.
  Skill: `incremental-implementation`. Depends on: —.
  Verify: `tests/test_order_registry.py` additions (atomic rollback, reopen
  persistence, old-DB migration, stale-generation no-op).
- [x] T2 [Backend/Logic, M] Shared `reset_condition` (`lifecycle_reset.py`
  new, `ResetPorts`/`ResetOutcome`). Skill: `api-and-interface-design`.
  Depends on: T1. Verify: new `tests/test_lifecycle_reset.py` with fake
  ports (6 cases: flat→RESET+awaiting; residual; late fill; unreadable
  balance; unverified cancel; PAIR_LOCKED+nonzero).
- [x] T3 [Backend/Logic, S] Live + shadow port adapters (strict two-token
  read in `single_buy_saver.py`; shadow factory in `shadow_exec.py`).
  Skill: `debugging-and-error-recovery`. Depends on: T2.
  Verify: adapter unit tests (absent-token-after-complete-read = 0;
  malformed/failed page raises `ExposureUnavailable`).
- [x] T4 [Backend/Logic, M] Route stop exit + fix `:1692` skip + retry
  (`single_buy_saver.py`). Replace `:1823-1846` block; skip requires
  `last_fill_ms <= latest_close_ms` AND net inventory ≤ `SIZE_EPS`; retry
  reset when row/`resetting` block exists. Skill:
  `doubt-driven-development`. Depends on: T3.
  Verify: new `tests/test_hard_stop_reset.py` + 
  `tests/test_partial_exit_stays_managed.py`; one deliberate
  `test_single_buy_saver.py` full-exit assertion update (reason in
  docstring: "`#646`: reset now deletes the row and marks re-qualification").
- [x] T5 [Backend/Logic, S] Live merge route (`order_manager.py`):
  `_submit_and_log` → structured outcome; `merge()` resets only on
  executed + `close_written`. Depends on: T3.
  Verify: extend `test_paired_inventory_accounting.py` /
  `test_order_manager_merge.py` (reset / close-fail / failed-submitted /
  20-vs-5 residual).
- [x] T6 [Backend/Logic, S] Shadow merge route (`shadow_exec.py`):
  reset after leg-write commit; failures propagate. Depends on: T3.
  Verify: extend `test_shadow_merge_leg_cost.py` (equal / unequal /
  leg-write-fail).
- [x] T7 [Backend/Logic, S] Feed snapshot (`market_feed.py`:
  `load_graduated_snapshot` via single-open + `fstat()`; doc decision +
  `docs/agents/architecture.md`). Depends on: — (parallel with T1–T3).
  Verify: extend `test_market_feed.py` (`[]` snapshot; atomic-replace
  metadata match).
- [x] T8 [Backend/Logic, M] Trader re-qualification + entry gate
  (`trader_loop.py`: metadata list subclass, per-cycle mark evaluation,
  early skip, guarded `_visit_one` final check, `sqlite3.Error` fail-closed).
  Depends on: T1, T7. Verify: extend `test_trader_loop.py` (10 cases per
  CodeRabbit list: block / stale-pub / clear+resume / exclude+drop /
  empty-pub / restart / legacy supplier / case / hedge-while-resetting /
  concurrent-reset).
- [x] T9 [Backend/Logic, XS] `order_manager status` read-only
  "awaiting re-qualification" section. Depends on: T1.
  Verify: hands-on `status` shows the section (empty OK).
- [x] T10 [Docs, XS] Out-of-scope ledger
  `docs/issues/646-noticed-but-not-touching.md` (open row per item + owner
  + when). Depends on: —. Verify: file exists, no behavior change.

**Dependency graph:** T1 → T2 → T3 → {T4, T5, T6}; T7 → T8 (needs T1 too);
T1 → T9; T10 free. Risk-first order: T1, T2, T7, T3, T4, T8, T5, T6, T9, T10.
**Checkpoints:** after T3 (core reset provable on fakes); after T6 (all
three paths routed); after T8 (gate complete → build hands to review).

## Build deviations (Station III, recorded so review does not re-litigate)

- T5: the 20 UP / 5 DOWN inventory assertions live in
  `tests/test_order_manager_merge.py` (via `inventory_from_registry`), not in
  `tests/test_paired_inventory_accounting.py`. One home for the merge-reset
  cases beats splitting them across two files.
- T8: "marks survive a restart" is covered by the T1 reopen test
  (`test_marks_survive_a_reopen`); not duplicated in the trader suite.
- T4: `test_route_to_merge_leaves_the_row_unchanged` passes pre-change too.
  It is a guard pinning acceptance box 5 (route_to_merge is never proof),
  not a RED/GREEN discriminator; the file's other tests discriminate.
- T8: `_market_specs` with `paired_arm` returns a plain list (no metadata).
  Paired bundles never clear a mark -- fail closed by construction.
- T4: passes with no venue view keep the old skip for partials (ledger N5).
  Without a venue position the exit would size off retired fills.
