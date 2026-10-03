# Plan — Issue #351: Diagnose zero-fill 01_shadow rehearsal and improve queue selection

Branch: i351/diagnose-zero-fill-01-shadow-rehearsal-and-improve | Issue: #351
URL: https://github.com/AI-Degen-69/spread-hunter/issues/351
Workspace branch: freebuff/ii-plan-issue-351-cd7f9c8b-f8f8-499e-99db-b683d3e8ee0e (session worktree; canonical `i351/...` name recorded here for Stations III–V)

## Step 0 — Stack, size, type
- **Stack**: Python 3.12, `pytest` (`testpaths = tests`), sqlite-backed `OrderRegistry` stores.
- **Size: Standard** — 2–4 files changed (`shadow_fills.py`, `statistics_report.py`, optionally `kpi.py`),
  plus 2 test files and 1 new diagnosis doc; one architectural decision (observational surfacing
  vs queue-bar enforcement in the shared ranker).
- **Task type**: Debug (primary) + Docs. Downstream: logic verification via unit/integration runner;
  no browser preview (no UI change).

## Step 0A — CodeRabbit plan intake (read once; echo ignored)
- **Adopted**: 3-task skeleton (pure queue-multiple helper → report surfacing + single-cycle warning →
  diagnosis doc + operator block); tape-only `credit_fills()` rule as explanation, not a bug;
  `None`-for-unmeasured contract (no `math.inf` in medians); read-only operator queries on scratch copies.
- **Rejected**: queue-bar enforcement in the shared ranker (feeds live trading; `queue_minutes_fn`
  absent in normal ranking — enforcement would leak rehearsal policy into live selection);
  `--minutes` run-duration guard (breaks intended short smoke runs); presenting ticket counts as
  measured facts (stores absent from checkout).
- **Stayed [UNVERIFIED]**: ticket evidence numbers (DBs `data/01_shadow.db`,
  `data/01_shadow_12-09_00-58.db` absent — labeled "reported by ticket; not reproduced");
  quote-row size source (quote row vs join to order by `local_id` — confirm schema at build);
  `cycle_intent` run attribution (filter by run if column exists, else whole-store with comment —
  confirm schema at build); intended `--minutes` value and kill cause (default assumption:
  truncated smoke run, not a model bug).
- **Verified seams**: `core_brain/shadow_fills.py` (`ShadowRestingOrder`, `queue_ahead_at`,
  `credit_fills` tape-only rule); `core_brain/shadow_exec.py:326-327,470,618` (queue recorded,
  restored); `core_brain/kpi.py:2139` (`median_queue_ahead`); `scoring/selector.py:245`
  (`maker_queue_allowed`), `scoring/config.py:420-425` (bar inert by default);
  `core_brain/order_registry.py:223-240,398` (quotes + `cycle_intent` tables);
  test files `tests/test_shadow_fills.py`, `tests/test_statistics_report.py`,
  `tests/test_maker_queue_bar.py` all present.

## Open questions (from issue; resolved from code where possible)
- *"How long was this run intended to last (`--minutes` value), and was it killed after ~1s?"*
  → **Unresolvable from code** (stores absent; no CLI log in checkout). Folded into plan as
  default assumption: truncated smoke run. Diagnosis doc records it unresolved and points at the
  heartbeat finished state + `tests/test_shadow_run_stopwatch.py` to distinguish normal
  completion from a crash. No operator ask — nothing to gain by blocking Station II on it.
- *"`last_polled_ts == posted_ts` means the settle pass never revisited them"* → **Corrected from
  code**: `settle_market()` updates `last_polled_ts` only on fills, so equality is expected for
  unfilled orders. Valid evidence is the single-cycle `cycle_intent` span, not the timestamp equality.

## Step 1 — Domain skills
- Tasks carry domain tags `[Debug/Logic]` + `[Docs]`; verification mode is the pytest runner
  (agent-run, background-only) plus a hands-on operator block (report on scratch copies).
- Helper skills: `test-driven-development`, `incremental-implementation`, `debugging-and-error-recovery`.
  (`code-explorer` / `type-design-analyzer` personas: skipped — paths are short and already traced;
  no file for either persona consulted.)

## Step 2 — Spec (concise; embedded, not root SPEC.md)
Root `SPEC.md` is currently scoped to issue #345 — overwriting it would erase another issue's spec,
so the spec lives here:
- **Goal**: explain the zero-fill rehearsal (run length × queue depth × tape-only fill rule) and land
  one shadow-only observational improvement.
- **Acceptance**: (1) evidence table with 4 orders / 0 fills, single-cycle span, multiples 420x–7741x,
  pair cost 0.95 vs 0.99 cap — labeled reported-not-reproduced with read-only confirm queries;
  (2) queue-multiple stats + single-cycle warning live in the shadow report, shown even when the
  close-count gate fails; (3) `tests/test_shadow_fills.py` + `tests/test_maker_queue_bar.py` green
  (plus `tests/test_statistics_report.py`).
- **Out of scope**: changing the tape-only rule, live quoting, touching `data/orders.db`,
  run-duration guard, queue-bar enforcement.

## Step 3 — Guardrails
Locked in `CONSTRAINTS.md` (header `Branch: i351/... | Issue: #351`).

## Step 4 — Interface contracts
- `queue_multiple(queue_ahead, order_size) -> float | None` in `core_brain/shadow_fills.py`:
  `queue_ahead / order_size`; `None` when queue is `None`/negative or size is `None`/non-positive.
  Pure, no clock/DB. A multiple of N means the tape must trade N× order size at the exact order
  price before first fill credit (the `credit_fills()` rule). `credit_fills()`,
  `queue_ahead_at()`, `ShadowRestingOrder` unchanged.
- Report (shadow mode, `core_brain/statistics_report.py`): for run-attributed quotes —
  `measured_quotes: int`, `unmeasured_quotes: int`, `median_queue_multiple: float | None`,
  `max_queue_multiple: float | None`; single-cycle line (exact text):
  `"Only one decision cycle observed; resting orders received no settlement pass after posting, so zero fills is expected and is not evidence of a fill-model defect."`
  Shown even when the close-count gate marks the sample insufficient. Optionally
  `median_queue_multiple` next to `median_queue_ahead` in `core_brain/kpi.py` iff KPI is the
  report's natural carrier; existing KPI keys unchanged.

## Step 5 — One improvement proposal (adopt-by-default: edge-case hardening)
- **Evidence (verbatim from the issue body)**: *"2 `cycle_intent` rows all at cycle 1"* and
  *"Both pairs cost 0.95 combined, inside the 0.99 cap"* combined with the report's close-count
  gate: a zero-fill run has 0 closes, so a gate-keyed report would hide the very signal that
  explains it. **Proposal**: render the queue-multiple stats and the single-cycle warning
  unconditionally (even when the gate fails). Adopted into Task 2. No scope expansion proposed;
  queue-bar enforcement stays deferred (rejection recorded so it does not resurface).

## Step 6 — Tasks (dependency graph first, risk-first, 3 atomic slices)

- T1 (helper) unblocks T2 (report). T3 (doc) needs T1+T2 outcomes (what landed vs deferred).
- T1 → T2 → T3. Riskiest first: the ratio contract (T1) decides everything downstream.

### T1 [Debug/Logic] — Queue-multiple helper + tests (S)
- **Target files**: `core_brain/shadow_fills.py`, `tests/test_shadow_fills.py`.
- **Build**: add pure `queue_multiple()` per Step 4 contract + docstring; no other symbol changes.
- **Tests** (`tests/test_shadow_fills.py`): plain arithmetic; zero queue → 0; zero/negative size →
  `None`; missing/negative queue → `None`; evidence values (2,524 / 5,092 / 38,706 / 6,113 vs
  sizes 5–6 → ≈420x–7741x); helper↔`credit_fills()` link (tape ≤ M×size → no fill; tape >
  queue_ahead → fill). Must fail without the helper.
- **Depends on**: nothing. **Verify**: agent-run focused pytest on the touched module.

### T2 [Debug/Logic] — Report surfacing + single-cycle warning + tests (M)
- **Target files**: `core_brain/statistics_report.py`, (`core_brain/kpi.py` iff carrier),
  `tests/test_statistics_report.py`.
- **Build**: confirm quote/`cycle_intent` schema first (size source, run column); compute the four
  stats via T1's helper for run-attributed quotes; distinct-cycle count + first/last span for the
  warning (exact text per Step 4); render unconditionally (even on gate failure); keep disclaimer
  and gate output otherwise unchanged. No lifecycle or ranker changes.
- **Tests**: single-cycle fixture (4 open orders, 4 deep-queue quotes, 2 cycle-1 intents, 0 fills →
  assert median/max + warning); multi-cycle fixture (warning absent); gate output unchanged.
  Must fail without the report change.
- **Depends on**: T1. **Verify**: agent-run focused pytest on the touched modules.

### T3 [Docs] — Diagnosis doc + operator block + notes entry (S)
- **Target files**: `docs/issues/analysis-01-shadow-zero-fill.md` (new), `SHARED_TASK_NOTES.md` (append).
- **Build**: evidence table labeled "reported by ticket; not reproduced in this checkout"
  (4 orders / 2 pairs / 4 quotes / 0 fills / 2 cycle-1 intents ≈0.5s / 420x–7741x / 0.95 vs 0.99;
  sibling 23,844 / 178 / 555); read-only confirm queries; mechanism section (own-price depth in
  `queue_ahead_at`, exact-price tape in `credit_fills`, settle-before-decide + `_Deadline`
  sleep-boundary stop + no final `settle_market` pass); `last_polled_ts` correction; unresolved
  `--minutes`/kill cause with smoke-run default + heartbeat/stopwatch pointer;
  `analysis-why-orders-dont-fill.html` 96/7,427 non-merge note; landed-vs-deferred ledger;
  isolated-shadow future path (`--out-dir` ranker + explicit callback → `shadow_run
  --markets-path` → separate DB; ref maker-queue spec); "How to verify" ≤5 steps, scratch
  copies only, no pytest, failure signs listed (missing line, blank multiples, `data/orders.db`
  touched). Notes entry: doc path, new lines, deferred enforcement, checks actually run.
- **Depends on**: T1, T2. **Verify**: hands-on operator read (file exists, values differ per fixture).

**Checkpoints**: after T1 (helper importable, focused tests green); after T2 (report shows multiples +
warning on fixture); after T3 (doc + notes complete → ready for `/iii-build-plan auto`).
**Sub-issues**: skipped — 3 tasks, single session, single issue; tracker mirroring would be ceremony
without survival value.

## How to verify (hands-on only, no pytest)
1. Copy `data/01_shadow.db` to a scratch path (never open the original for writing).
2. Open the copy read-only — confirm 4 orders, 0 fills, 2 cycle-1 `cycle_intent` rows.
3. Run the shadow statistics report against the copy — expect max multiple ≈7741x plus the
   single-cycle limitation line.
4. Run the report against a sibling-store copy — expect no limitation line.
5. Failure signs: limitation missing, multiples blank, or `data/orders.db` touched.
