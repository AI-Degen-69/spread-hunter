Branch: i761/fill-quality-entry-gate | Issue: #761

# Station II plan — Fill quality → entry gate (plan only, no product code)

**Scope:** Station II only. No product code this session. Artifacts: this file,
`tasks/todo.md`, `CONSTRAINTS.md`. #759 / #760 are owned by another session and
stay out. #471 is premature here; stop-loss / sizing / tournament stay untouched.

**Evidence base (read-only):** the operator's shadow store in the main checkout
(`data/01_shadow_21-10_11-39.db`, session `shadow-01`, Oct 21 11:39 → Oct 22
20:38 UTC). Fresh worktrees do NOT carry it (gitignored, untracked) — Station III
copies nothing; it opens the operator's file with `file:<abs>?mode=ro`.
Reference numbers the plan must reproduce: 1414 orders / 467 fills (run
`shadow-01`); closes `shadow_merge` 192 (+$64.66), `shadow_settlement` 17
(+$35.95), `single_buy_exit` 65 (−$100.06); reasons `lifecycle_hard_stop` 32
(−$93.56), `aged_out_rescue` 33 (−$6.50); merge avg +$0.329, dead-leg avg −$2.924,
break-even **8.9:1** vs actual **5.4** (173 ÷ 32), dead-leg share 15.6% vs 10.1%
break-even; `hard_stop_refusals` **5,464**, worst pair 2,041 in 68 min, dust case
0.1087 vs 1.0 share minimum; stop-vs-hold counterfactual n=18: stop −$41.61 vs
hold −$49.10 (stop saves $7.49, leg recovered 1/18).

**First read:** `docs/explanations/entry-gate-job.html` (commit `087825c`),
§3 dead-leg anatomy stepper, §4 break-even arithmetic, §8 rejected-alternatives
table, §9 falsifiers. Do not re-litigate a rejected row without new evidence.

**Delivery-vehicle state (checked 2026-10-23):** #474 CLOSED, PR #757
`i474/fill-model-calibration-cli` MERGED. The "conflict + Windows shard 6/6 red"
row in §8 is therefore satisfied on current `main` — Station III extends the
`fill_calibration.py` pattern, it does not fork a competitor. Station III still
re-checks `gh pr checks` / shard state on its build day and records it; a red
`main` blocks the build.

## 1. Report module read-only contract — `core_brain/fill_quality_report.py`

New module + CLI `python -m core_brain.fill_quality_report --shadow-db <path>
[--run-id shadow-01] [--min-obs 30] [--json]`. Copies the `fill_calibration.py`
read-only construction verbatim (Station III diffs against it):

- Both DB inputs open through `Path.resolve().as_uri() + "?mode=ro"` with
  `uri=True`. `OrderRegistry` is never constructed on an input path (its ctor
  migrates). No transaction is ever opened; zero
  `INSERT/UPDATE/DELETE/CREATE/ALTER/DROP` statements in the file.
- Imports nothing from `core_brain` (keeps the claim auditable in one glance);
  copies any `kpi.py` bucket constants instead of importing them.
- Unreadable input → status `UNREADABLE`/`NO_DATA`, never repair, never write.
- One row per fill: `fills` ⨝ `orders` (price, side, posted_ts, pair_id,
  max_pair_cost_at_post) + book state at fill time (nearest `quotes` row with
  `ts <= COALESCE(venue_ts, recorded_ts)` per condition+token: `mid`,
  `edge_vs_mid`, `queue_ahead`, `size`; spread from best bid/ask or
  `queue_marks.best_bid`) + lifecycle outcome (`closes` by pair/condition:
  method/reason/realized_pnl; `single_leg_lifecycle` state/reason;
  `resolutions` for time-to-resolution and settlement value).
- Terminal labels: `merged` / `dead_leg` (hard-stop or losing single-buy exit) /
  `aged_out_rescue` / `other|open`. Inclusive-PnL attribution per fill = pair
  realized PnL pro-rata by shares (gas/fees as recorded).

Tests (Station III, each fails without the change): source-grep test for write
statements (mirrors `test_fill_calibration.py` style), `mode=ro` URI test incl.
`#`-in-path case, inputs-unmodified (mtime+hash) test, one-row-per-fill join
test on a fixture DB, every-percentage-prints-n test.

## 2. Calibration buckets — `P(leg worthless | conditions)` with Wilson intervals

Five dimensions, fixed in code as constants (exact edges set after the Station III
histogram pass; boundaries printed in every report header):

1. distance from mid (`quotes.edge_vs_mid`), 2. spread at fill, 3. depth on
   opposite side (`queue_ahead`/`level_size`, reusing the `kpi.py` QUEUE_BUCKETS
   boundaries `(0,1),(1,50),(50,150),(150,400),(400,inf)` + an `unknown` bucket;
   NULL never folds into 0), 4. time to resolution (`resolved_ts − fill_ts`),
   5. gap between legs' fill times per `pair_id`.

Per bucket: n fills, death rate, merge rate, lift vs base rate, **Wilson 95%
interval** on the death rate (local function, z=1.96, no scipy), status
`OK | INSUFFICIENT`. Rule: **n < ~30 (`--min-obs`, default 30) →
`INSUFFICIENT`, printed as "not-a-finding"**; sparse buckets never clear a gate.
A percentage without n is a defect — the test asserts n on every row.

## 3. Default-off gate switch + break-even table

- `core_brain/config.py`: `bot_entry_fill_quality_gate_on: bool = False` +
  threshold fields (blocked-bucket set / `min_lift`, exact shape set by what
  clears §2) + `BOT_ENTRY_FILL_QUALITY_GATE_ON` env override. Off-by-default so
  live behavior cannot change by accident; `trader_loop.plan_orders` consults the
  gate only when the flag is true. The evaluate function is pure
  (features → allow/reject + reason) so the A/B can replay without the venue.
- `report --propose-gate` prints one row per candidate threshold:
  **legs rejected / merges lost (n + $) / dead legs avoided (n + $) / net
  inclusive-PnL delta**. Rule: **no threshold ships unless lift clears 8.9:1 AND
  net inclusive delta > 0 on the shadow window**; otherwise the honest output is
  "no bucket clears → redirect to sizing/market selection" (§9 falsifier).

## 4. Shadow A/B scoring definition (inclusive PnL — never merge-only)

Same window (`shadow-01`), gate off vs on, replayed through the pure evaluate
function: off = all fills as observed; on = fills in blocked buckets dropped
(first leg blocked → pair never opens; second leg blocked → pair stays single
and is scored at its inclusive outcome, never silently dropped). Score = **total
realized inclusive PnL**: `shadow_merge + shadow_settlement + single_buy_exit`
grouped by reason **including** `lifecycle_hard_stop` + `aged_out_rescue` with
gas/fees as recorded. Merge-only scoring is explicitly rejected (tautological —
it hides the dead legs). The A/B test asserts both branches print inclusive
totals and fails if either branch scores merge-only.

## 5. Dust fix + absence test (deliverable, not a footnote)

Loop: 5,464 `hard_stop_refusals`, worst pair 2,041 in 68 min, dust case 0.1087
vs 1.0 venue-minimum share. Station III locates the exact retry site first
(candidates: `order_manager` exit, `single_buy_saver`, `risk_guardian`
`stop_trigger` path) with a stack trace, then: position < venue minimum →
**log once per `pair_id`** (reuse `hard_stop_refusals` with
`outcome='dust_skip'`) **then wait — no retry**. New test
`tests/test_dust_refusal_absence.py::test_dust_position_does_not_loop`
(0.1087-share fixture, run exit twice, assert ≤1 refusal row and the second
call no-ops) **must be run pre-fix on `origin/main` and recorded RED**; the
absence test failing today is the entry ticket for the fix.

## 6. Out of scope / traps (plan rejects these)

Stop-loss tuning (loses $7.49 on n=18) · sizing / #487 tournament retune (wrong
order — tunes pre-gate) · #471 quote ladder (premature — quotes an unmeasured
function) · `dashboard/server.py` refactor (zero PnL) · merge-only A/B ·
any write path to the shadow DB · #759/#760 (other session).

## 7. Station III build order (for the builder, not this session)

1. Fixture DB + report skeleton with read-only contract tests (RED→GREEN).
2. Fill→book→lifecycle join + one-row-per-fill test. 3. Bucket calibration +
   Wilson + INSUFFICIENT rule. 4. Break-even table + default-off config +
   pure evaluate. 5. Shadow A/B on inclusive PnL. 6. Dust absence test RED on
   `main`, then fix. 7. Docs + report output under `reports/`.

## How to verify (hands-on only — no pytest prompt)

1. Open `docs/explanations/entry-gate-job.html` in a browser and walk §3→§4→§8;
   confirm the plan's 8.9:1, $0.329/$2.924, and rejected-rows match the memo.
2. Launch the stack from the menu (`.\scripts\spread-hunter-menu.ps1 start`),
   run a 5-minute shadow rehearsal to a fresh `data/NN_shadow_<stamp>.db`, then
   run the new report CLI read-only against the operator's
   `data/01_shadow_21-10_11-39.db` (`mode=ro`) and open the printed break-even
   table + `reports/` output; check every percentage carries its n and sparse
   buckets read "not-a-finding".
3. Flip the gate switch on in a shadow run, open `http://127.0.0.1:8799`, and
   confirm the A/B readout scores inclusive PnL (stops included) with the gate
   off-by-default on restart.
