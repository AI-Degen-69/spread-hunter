Branch: i767/registry-targeted-queries | Issue: #767

# Station II plan — Replace full-table SQLite scans in 1Hz guardian and lifecycle hot paths (plan only, no product code)

**Scope:** Station II only. No product code, no test edits this session.
Artifacts: this file, `tasks/todo.md`, `CONSTRAINTS.md`.
Planned against `origin/main` (commit `27aca53e`). Build step re-derives all
line numbers after #764 merges — never trust the numbers below blindly
(see "Rebase point").

**Size tier: Standard** — 3 internal modules (`order_registry.py`,
`risk_guardian.py`, `single_buy_saver.py`), no new dependency, no schema
change, one architectural decision (where the "latest row wins" rule lives).
**Task type: Performance + Code** (hot-path read narrowing with strict
equivalence; reviewers: logic/perf, no UI browser gate).

## 0. What the issue asks (spec, embedded — no SPEC.md ceremony for Standard)

Replace full-table `get_all_quotes()`, `get_all_fills()`, `get_all_closes()`
scans plus per-order N+1 `load_pair` queries in the 1 Hz Risk Guardian and
the single-leg lifecycle loops with targeted `OrderRegistry` queries.
Results, accounting, lock behaviour, and table schemas must not change.

Hot paths mapped on `origin/main`:

- `core_brain/risk_guardian.py:23-43` — `_group_condition_tokens` full
  `get_all_quotes()` scan, called **twice per tick** (`detect_unhedged_positions`
  + `build_watches`, ~46-215). `run_guardian_tick` itself (~185-215) unchanged.
- `core_brain/single_buy_saver.py:635-687` — `load_pair`: 1 `get_orders_by_pair`
  + **2 connections per order** (`get_size_matched` + `get_matched_notional`).
  A 2-order pair = 5 connections; a 6-order pair = 13 connections. Target: 1.
- `single_buy_saver.py:690-747` — `_prior_exit_shares`: 1 `_token_side` scan
  + 1 `get_orders_by_pair` + 1 `get_all_closes()` scan, on the exit path.
- `single_buy_saver.py:816-842` — `_token_side`: full `get_all_quotes()` scan
  per leg, called per pair per pass (~2156) plus via `_prior_exit_shares`
  (~715) and `_check_positions` (~803).
- `single_buy_saver.py:1811-1823` (`_latest_close_ms_by_condition`) —
  full `get_all_closes()` scan per pass.
- `single_buy_saver.py:1885-1901` (`_last_fill_ms_by_pair`) — full
  `get_all_fills()` (13-col join + optional `shadow_clearing_prints` join)
  per pass.
- `single_buy_saver.py:2092-2105` (`_manage_single_leg_positions_locked`
  discovery) — `get_all_closes()` + `get_all_fills()` + inline
  `get_all_quotes()` grouping per pass, then `load_pair` per pair, all under
  `_LIFECYCLE_LOCK` (RLock ~108; `manage_single_leg_positions` ~1983-2032
  wraps the locked pass ~2035+). Extra round-trips inside the lock are a
  regression even when each is individually faster — batch, don't fan out.

None of the needed helpers exist on `origin/main` (verified by grep):
`get_latest_quote` / `get_quotes_for` / `get_token_side` / `get_pair_fill_totals`
are all absent. This is **add-then-rewire**: land each registry method with its
equivalence test before any caller uses it.

## 1. CodeRabbit plan intake (costed once so Stations III–V never re-read it)

Source: `gh issue view 767 --comments` (`coderabbitai`, 8-commit plan).
What was adopted / rejected / left unverified:

- **Adopted:** two separate quote mappings kept apart (condition/side map vs
  token-side lookup); `LEFT JOIN` pair-totals shape; narrow fill/close reads
  that keep every Python conversion/reduction verbatim (no SQL aggregates);
  `rowid` tiebreak; one-connection-per-method via existing `_conn()` scope;
  `get_all_*` kept for other callers; no index/DDL; fresh reads stay fresh
  (no caching across cancel/exit/completion); close-timestamp read moved after
  fill discovery and scoped to candidate conditions.
- **Rejected (over-split merged):** 8 commits → 4 atomic tasks below. Each
  task lands registry method(s) + equivalence tests, then rewires caller(s);
  each stays independently revertible. The window-function-vs-Python-fallback
  branch is folded into Task 1 (SQLite on this interpreter is 3.49.1, window
  functions available; deploy check still required — see CONSTRAINTS.md §7).
  The "two condition/side reads per tick stay" deferral is accepted as-is
  (sharing one result would change `detect`/`build_watches` signatures).
- **Stays `[UNVERIFIED]`:** deployed-interpreter SQLite version (plan assumes
  ≥ 3.25.0 for `ROW_NUMBER()`); exact tick-timing gain on a production-shaped
  store (must be measured on a **copy** in scratch, never `data/orders.db`).

## 2. Interface contracts (frozen before build)

All new methods live on `OrderRegistry` in `core_brain/order_registry.py`,
next to the `get_all_*` they narrow. All use the existing `_conn()` scope
(one connection per call), no DDL, no index, no `fill_calibration.py` change.
`get_all_quotes / get_all_fills / get_all_closes / get_orders_by_pair /
get_size_matched / get_matched_notional` keep their signatures — other callers
still use them (see §5).

### Q1 `get_latest_tokens_by_condition_side() -> list[dict{condition_id, side, token_id, ts}]`

SQL shape (one query, no Python grouping):

```sql
SELECT condition_id, token_id, UPPER(side) AS side, ts
FROM quotes
WHERE condition_id IS NOT NULL AND condition_id != ''
  AND token_id IS NOT NULL AND token_id != ''
  AND UPPER(side) IN ('UP','DOWN')
-- rank per (condition_id, side): latest wins
-- preferred: ROW_NUMBER() OVER (
--   PARTITION BY condition_id, UPPER(side)
--   ORDER BY COALESCE(ts, 0.0) DESC, rowid DESC) AS rn, keep rn = 1
-- fallback (deploy SQLite < 3.25.0): ordered projection
--   ORDER BY COALESCE(ts,0.0) ASC, rowid ASC + latest-wins pick in Python
```

Tiebreak (pins today's behaviour): order key is
`(COALESCE(ts,0.0) DESC, rowid DESC)` — newest inserted row wins ties.
This matches the live loop (`ts >= prior[0]` over `ORDER BY ts ASC`, whose
ties arrive in `rowid` insertion order). Applies no `-1.0` floor — the
current grouping accepts any timestamp. Returns `ts` raw; caller still does
`float(ts or 0.0)`.

Replaces: `risk_guardian._group_condition_tokens` body (scan → call);
`single_buy_saver` inline `quoted_tokens` grouping (~2096-2105, once per
pass under the same lock). Output shape `cid → side → (ts, token)` preserved.

### Q2 `get_token_side(condition_id, token_id) -> "UP" | "DOWN" | None`

```sql
SELECT UPPER(side) AS side FROM quotes
WHERE condition_id = ? AND token_id = ?
  AND UPPER(side) IN ('UP','DOWN')
  AND COALESCE(ts, 0.0) >= -1.0
ORDER BY COALESCE(ts, 0.0) DESC, rowid DESC
LIMIT 1
```

Tiebreak + floor pin today's `_token_side` exactly: `best_ts` starts at
`-1.0`, `ts >= best_ts` wins — so a row below `-1.0` never sets the answer
and ties go to the newest inserted row. Exact-equality match on both ids.
Must NOT be derived from the Q1 map (counter-example pinned in test:
history A→UP@1, B→UP@2, B→DOWN@3 — the Q1 map loses token A, Q2 must still
return UP for A).

Replaces: `_token_side` body (~829-842); every call site stays a fresh read
(~715 via `_prior_exit_shares`, ~803, ~1019, ~2156 plus pass fallback ~2169).

### Q3 `get_pair_orders_with_fill_totals(pair_id) -> list[(OrderRecord, size_matched, matched_notional)]`

Batched `load_pair` contract — **one query, one connection**, replaces
`1 + 2*N` round-trips:

```sql
SELECT o.*, COALESCE(SUM(f.size), 0.0) AS _matched,
       COALESCE(SUM(f.size * f.price), 0.0) AS _notional
FROM orders o LEFT JOIN fills f ON f.order_uuid = o.id
WHERE o.pair_id = ?
GROUP BY o.id
ORDER BY o.posted_ts ASC
```

Rules: `LEFT JOIN` (zero-fill orders survive with 0.0/0.0); include every
status; no side filter; exact `pair_id` match; posted order; empty list for
unknown pair. Reuse the existing `_row_to_order` conversion; do NOT sign
SELL fills; do NOT subtract closes. `load_pair` keeps 100% of its reduction:
token-encounter order, ordered order lists, >2-token refusal (including
unfilled tokens), stable matched-size heavy/light ranking, first order's
condition. Every fresh `load_pair` after cancel/exit/completion stays fresh.

### Q4 `get_fill_discovery_rows() -> list[(pair_id, condition_id, venue_ts)]`

```sql
SELECT o.pair_id, o.condition_id, f.venue_ts
FROM fills f JOIN orders o ON o.id = f.order_uuid
WHERE o.pair_id IS NOT NULL AND o.pair_id != ''
ORDER BY f.recorded_ts ASC, f.rowid ASC  -- NULLs first, same as today
```

Narrow projection only — no status/side/size/run/timestamp filter, no
`shadow_clearing_prints` join in this path (that join stays inside
`get_all_fills` for its consumers). `_last_fill_ms_by_pair` keeps its Python
verbatim: first-row condition assignment `condition_id or ""` even for
undated fills; truthy venue-time check; `max(..., int(venue_ts))` from 0;
malformed venue times still raise (no CAST in SQL).

### Q5 `get_close_timestamps(condition_ids) -> list[(condition_id, ts)]`

```sql
SELECT condition_id, ts FROM closes
WHERE condition_id IN (?, ?, ...)   -- exact match, chunks of ≤500 (copy the
                                    -- queue_ahead_by_local_id placeholder pattern)
```

One `_conn()` scope for all chunks; empty input → `[]` with no query.
`_latest_close_ms_by_condition(registry, condition_ids=None)` gains the
optional arg: `None` preserves the full read for other callers; given list
uses Q5. Reduction verbatim: skip falsy condition/timestamp,
`int(float(ts)*1000.0)`, skip only `TypeError`/`ValueError`, keep max from 0.
Pass moves the close read **after** fill discovery and passes only the
nonempty first-fill conditions of pairs present in `last_fill_ms_by_pair`
(no first-order conditions, no lowercased keys).

### Q6 `get_closes_for_condition(condition_id, methods) -> list[(up_price, shares)]`

```sql
SELECT up_price, shares FROM closes
WHERE condition_id = ? AND method IN (?, ?, ...)
```

Empty method set → `[]` without querying. Method ownership stays in
`close_methods.py` — caller passes `RESCUE_METHODS`
(`single_buy_exit`, `naked_exit`, `ladder_exit`); the registry never imports
the taxonomy. `_prior_exit_shares` keeps its verbatim rules: fresh
`_token_side` read, `0.0` on unresolvable side, UP iff `up_price is not None`
(zero counts as UP), `float(shares or 0.0)`, skip only `TypeError`/`ValueError`,
no pair/token/run/timestamp filter beyond the existing `pair_id` posted-ts
floor. Both fresh call sites (exit + completion paths) stay uncached.

## 3. Tiebreak rule + the test that pins it (the hard part of this issue)

"Latest row wins" today means **iteration order**, not timestamp order:
`get_all_quotes()` is `ORDER BY ts ASC` with **no** tie rule, so ties arrive
in `rowid` order and the `>=` comparison lets the later row win. A
`GROUP BY … MAX(ts)` rewrite would collapse ties to an arbitrary row.
The plan's rule — `(COALESCE(ts,0.0) DESC, rowid DESC)`, newest inserted
wins — reproduces today's outcome deterministically, including the
multi-row-per-millisecond tick case.

RED-first pin (Task 1, real temp registry, compared against a
`get_all_quotes()` reference implementation): seed A→UP@1.0, B→UP@2.0,
B→DOWN@2.0 (tie — DOWN inserted last must win Q1 for B, Q2 for B must be
DOWN while Q2 for A stays UP); NULL/zero/`-0.5`/`-1.0`/`-1.5` timestamps
(`-1.5` invisible to Q2, visible to Q1); lowercase/invalid sides; empty/NULL
ids. Fails on any `MAX(ts)`-without-`rowid` implementation.

## 4. Task breakdown (risk-first, atomic vertical slices, 5–10 min each)

Dependency graph: T1 → T2, T1 → T3 (T2/T3 parallel after T1); T1 → T4
(fill discovery needs the quote-tiebreak convention settled first only for
review focus, no code dependency — may run parallel); T4 → T5 (close scoping
needs the fill-discovery pair set). Sizes: T1 M, T2 S, T3 S, T4 M, T5 M.

### T1 [Backend/Logic, M] Registry quote queries + equivalence tests
Target: `order_registry.py` near `get_all_quotes()` (~1994-1997);
`tests/test_order_registry.py` (new tests). Depends on: nothing.
Build: Q1 + Q2 with tests, no caller changes (zero runtime effect).
RED: the tiebreak/counter-example tests above fail without Q1/Q2
(reference built from `get_all_quotes()`).
Verify: `python -m pytest -q tests/test_order_registry.py -k "latest_tokens or token_side"`.
Perf note: describe the gain honestly — SQLite still reads quote rows
internally; Python stops materialising the whole table.

### T2 [Backend/Logic, S] Rewire guardian + mock hardening
Target: `risk_guardian.py:23-43`; `tests/test_risk_guardian.py` (6-66, 248-359).
Depends on: T1. Build: `_group_condition_tokens` calls Q1; output shape,
`float(ts or 0.0)`, id string conversions unchanged; `detect`/`build_watches`/
`run_guardian_tick` untouched (two Q1 reads per tick stay — no signature
change). Tests: feed prior fixtures through Q1 mock; `get_all_quotes`
side-effect raises (unspecced `MagicMock` would otherwise pass while checking
nothing); inventory + stop-watch assertions unchanged.
RED: `get_all_quotes`-raises test fails if any guardian path still scans.
Verify: `python -m pytest -q tests/test_risk_guardian.py`.

### T3 [Backend/Logic, S] Rewire lifecycle quote paths
Target: `single_buy_saver.py` inline map (~2096-2105) + `_token_side`
(~816-842); `tests/test_single_buy_saver.py` (new). Depends on: T1.
Build: pass reads Q1 once per pass under the same lock (no
`risk_guardian` import — import loop risk); `_token_side` delegates to Q2;
all call sites stay fresh reads; map shape `cid → side → (ts, token)` + 
`str(value or "")` preserved.
RED: real-fixture test with `get_all_quotes` patched to raise — runs
`_token_side` + one `manage_single_leg_positions` pass.
Verify: `python -m pytest -q tests/test_single_buy_saver.py -k "quote or token_side"`.

### T4 [Backend/Logic, M] Batched pair totals + narrow fill discovery
Target: `order_registry.py` beside `get_orders_by_pair` (~1534-1541) and
`get_all_fills` (~1999-2031); `single_buy_saver.py load_pair` (~635-687) +
`_last_fill_ms_by_pair` (~1885-1901). Depends on: T1 (review order only).
Build: Q3 + Q4 with tests, then `load_pair` → Q3, `_last_fill_ms_by_pair` →
Q4, reductions verbatim (§2). Count round-trips: assert `_conn()` uses equal
for 2-order and 6-order pairs (1 vs 5/13 today); `get_size_matched`/
`get_matched_notional` raise in the `load_pair` test. Cover: multi-price
fills, zero-fill/cancelled-with-fill/replayed fills, unknown pair → `[]`,
undated-first/undated-only pairs, zero/negative venue times, unpaired fills
ignored, insertion-order dict stability.
RED: equivalence-vs-`get_all_fills()` + connection-count tests fail on
`origin/main`.
Verify: `python -m pytest -q tests/test_order_registry.py tests/test_single_buy_saver.py -k "pair_totals or fill_discovery or load_pair"`.

### T5 [Backend/Logic, M] Bounded close reads + rescue-close query
Target: `order_registry.py` near `get_all_closes()` (~2066-2069);
`single_buy_saver.py` `_latest_close_ms_by_condition` (~1811-1823) +
`_prior_exit_shares` (~690-747) + pass call order (~2092-2096).
Depends on: T4. Build: Q5 + Q6 with tests, then rewire; move close read
after fill discovery, scope to candidate conditions (chunked ≤500);
`_prior_exit_shares` passes `RESCUE_METHODS`; both fresh call sites stay
fresh; second-exit-in-one-pass sees the first exit's close (same-pass test).
Cover: every close method, exact key case, sub-ms/zero/negative ts,
residual inventory after partial exit, >500 candidate conditions, ladder
exits, excluded merges, zero UP price, NULL shares, other-condition rows.
RED: comparisons vs `get_all_closes()` + >500-chunk test fail on `origin/main`.
Verify: `python -m pytest -q tests/test_single_buy_saver.py -k "close"`.
Then full-pass guard: one `manage_single_leg_positions` run on a real
fixture reaching exit + completion with `get_all_quotes/get_all_fills/
get_all_closes/get_size_matched/get_matched_notional` all patched to raise.

Checkpoints: after T1 (quote contract pinned); after T3 (guardian +
lifecycle quote scans gone); after T5 (no full scans in hot paths; see §6).

## 5. No second quote reader — `get_all_*` survival list

`get_all_quotes()` MUST survive for callers this issue does not rewire
(grep `origin/main`, verified above — do not delete or deprecate it):

- `core_brain/kpi.py:1326-1328` (dashboard KPI: quotes + fills + closes)
- `core_brain/settlement_sweeper.py:27`
- `core_brain/shadow_exec.py:1565` (+ comment at 1559-1840 about the scan)
- `core_brain/statistics_report.py:81,220-222`
- `core_brain/trader_loop.py:2848-2851` (guarded `hasattr` read)
- `core_brain/order_registry.py:2533,2922` (`get_size_matched` consumers:
  `audit.py:168`, `stray_guard.py:359,595`, `trader_loop.py:1622-1623` —
  Q3 does NOT replace these; they keep calling the per-order helpers)
- `core_brain/statistical_analytics.py`, `tests/*` (reporting/tests read
  the full ledgers directly)

## 6. Rebase point (for the Station III builder — #764 merges first)

Sibling session on `i764/complete-pair-ask-depth` (issue #764, bounding
`complete_pair` ask depth) touches ONLY `core_brain/single_buy_saver.py`
+ its test. Expected hunks on `origin/main` (from
`git diff origin/main..origin/i764/complete-pair-ask-depth`):

1. `+PRICE_EPS_OFFSET = 1e-9` constant near line ~66 (after
   `MIN_SELL_SHARES`) — shifts every line below by ~+5.
2. `+ask_depth_at_or_below(book, limit)` helper near line ~1290 (after
   `ask_depth`) — shifts lines below by ~+6 more (~+11 total past ~1290).
3. `complete_pair` sizing line near ~1384: `ask_depth(book)` →
   `ask_depth_at_or_below(book, ask)` + error-string change.

No textual overlap with this plan's hunks (`load_pair` ~635,
`_prior_exit_shares` ~690, `_token_side` ~816, close/fill helpers
~1811/1885, pass ~2092 — all read cleanly through the rebase; only numbers
move). Builder: `git fetch origin && git rebase origin/main`, then re-derive
every line number with `grep -n "def load_pair\|def _token_side\|def
_latest_close_ms_by_condition\|def _last_fill_ms_by_pair\|quoted_tokens\|
get_all_quotes\|get_all_fills\|get_all_closes"` — never edit by stale number.
If #764's error string changed, keep its wording; this plan does not touch
`complete_pair`.

## 7. Hands-on verification (operator-run, no pytest — background only)

Per task the builder runs the targeted pytest in §4 silently and reports
only the hands-on surface. Operator steps (each on a **copy** in scratch —
`cp <store> /tmp/sh767_<stamp>.db` or `data/NN_shadow_<stamp>.db` fresh from
`python -m core_brain.shadow_run --minutes 5`; never `data/orders.db`):

1. Launch the stack (`.\scripts\spread-hunter-menu.ps1 start`), open
   `http://127.0.0.1:8799`, START the Trader loop, watch one 1 Hz guardian
   tick complete with no `lifecycle pass failed` in the log.
2. Run a 5-minute shadow rehearsal on the copied/fresh store; confirm
   `shadow risk guardian sentry engaged` appears and, if single-leg exposure
   occurs, normal `lifecycle <pair_id> <action>` lines with exactly one close
   per successful exit (a quiet run counts only as startup verification).
3. Optional timing note the builder may quote: wall-clock one guardian tick
   + one lifecycle pass on the copied store before/after (same copy, same
   machine, 3 runs median) — reported as observation, never as proof.

Full `python -m pytest -q` stays the GitHub CI merge gate; it is not an
operator step and is never cited as verification to the Owner.

## Build status (Station III, 2026-10-23)

- [x] T1 Q1+Q2 + tiebreak equivalence tests (RED: AttributeError pre-impl)
- [x] T2 guardian rewire + mock hardening (14 passed)
- [x] T3 lifecycle quote paths + no-scan test
- [x] T4 Q3+Q4, load_pair/_last_fill rewire, one-connection test
- [x] T5 Q5+Q6, close helpers + pass order, rescue-only + scoped tests
- [x] Full-pass guard (exit + completion, all scans banned), executor mock fix,
      scratch-copy timing. Deviations: Q6 carries `ts` (N2) and
      `test_lifecycle_single_executor.py` mock update — both recorded.
