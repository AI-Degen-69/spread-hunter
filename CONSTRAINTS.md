# CONSTRAINTS â€” #531 (locked by Station II, enforced through Station V)

Governs the `i531/persist-clearing-print-at-fill-credit-time` branch only.
The #541 contract this file replaces is branch-scoped by its own header line and stays
readable in git history.

## The N1 follow-up and its storage decision

#515 shipped the `queue realism` readout but left the clearing print unwritten
(N1 in `docs/issues/515-noticed-but-not-touching.md`, filed as #531 by operator
decision 2026-10-14). This ticket is that writer.

**Storage: a shadow-only companion table, NOT a `fills` column.**
A new nullable `clearing_price` column on `fills` would travel through the shared
`SCHEMA` + `_apply_migrations()` (`core_brain/order_registry.py:213-222`,
`:461-475`) and alter production stores the next time those stores open.
`data/orders.db` is read-only and every prior contract forbids touching it. So the
print lands in a small table created in `ensure_shadow_tables`
(`core_brain/shadow_exec.py:106`), keyed by `trade_id`, exactly like
`shadow_queue` / `shadow_merge_legs` beside it: a shadow store opened by live code
reads as a registry with odd ids, never as one with columns that do not exist.

**Travel: `ShadowFill.clearing_price: Optional[float] = None`, `compare=False`.**
`credit_fills`'s return type `(fills, queues)` is part of the fill-model seam â€”
it stays unchanged. With `compare=False` the existing equality assertions in
`tests/test_shadow_fills.py` stay green without edits. Telemetry must not
decide whether two fills are equal.

## Scope & File Boundaries

- Target files for modification (exactly five, plus this planning set):
  - `core_brain/shadow_fills.py` â€” select `max(price for (t,price) in swept
    if t == token and price < own)` inside the branch that already credits;
    attach it to the credited `ShadowFill`. Nothing else in the function.
  - `core_brain/shadow_exec.py` â€” the companion table DDL in
    `ensure_shadow_tables` + one write per credited fill in `settle_market`.
    `settle_market` keeps writing `FillRecord` exactly as today.
  - `core_brain/order_registry.py` â€” `FillRecord` gains the optional field;
    `record_fill` INSERT unchanged; `get_all_fills()` enriches rows with the
    print via an optional join guarded by a table-existence check.
  - `core_brain/statistical_analytics.py` â€” `cleared_fills` reads the print
    from the row that supplies the earliest usable timestamp (latency and
    depth then describe the same event; latency figures do not move).
  - `tests/test_shadow_fills.py`, `tests/test_queue_realism_analytics.py` â€”
    new tests only, except updating the two assertions that pin
    `clearing_price is None` as the honest state (they become the
    present-print cases).
  - `SPEC.md`, `CONSTRAINTS.md`, `tasks/plan.md`, `tasks/todo.md`.
- Files that must NOT be modified:
  - `data/orders.db` and every production registry â€” read-only at all times.
  - Fill eligibility, credited size, execution price, timestamps â€” the fill
    model is frozen; which orders fill must not change.
  - The taker path (`core_brain/shadow_exec.py (taker: create_and_post_market_order)`) â€” no resting level,
    no depth; stays unmeasured.
  - Bands, thresholds, the queue-realism card, the latency half of #515.
  - `core_brain/config.py`, quoting, sizing, close-method tuples (#520 owns
    them), top nav (#504 owns it).
  - `dashboard/static/app.js` â€” `renderQueueRealism` already renders a real
    figure; no change expected.

## Population & missing-value rules

- One `ClearedFill` per credited quote. The clearing price comes from the row
  that supplies the earliest usable timestamp; ties take the first row seen;
  all rows timestamp-less take the first row.
- A missing clearing print stays `None` â€” never `0.0`, never `fills.price`.
  `trade_through_depth` keeps counting it as `missing`, not zero.

## Quality bar

- Focused suites the agent runs: `tests/test_shadow_fills.py`,
  `tests/test_queue_realism_analytics.py`; boundary `tests/test_shadow_run.py`
  + `tests/test_statistical_analytics.py` (readers of the touched seams).
  The full `python -m pytest -q` sweep is GitHub CI's job, not a local step.
- Every changed behaviour carries a test that fails without the change; the
  end-to-end regression lives in `tests/test_queue_realism_analytics.py` so
  the issue's own acceptance command runs it.
- Anti-cheat: no skipped/disabled tests, no deleted assertions, no linter
  suppression. No new external dependencies.
- `data/orders.db` is never opened for writing; tests use `tmp_path` or fakes.

## Environment quirk (verified at intake)

- This host's shell exports `PORT=0`, which breaks dashboard test collection.
  Run focused suites via `.venv/Scripts/python -m pytest`. Never hand pytest
  commands to the operator â€” agent-run only, reported as background results.


## Values rule (operator-ordered t-switch)

- Phase-1 goldens freeze today's numbers FIRST (pre-switch record) and pass before any
  production change.
- The one deliberate value change: every mean interval switches to Student's t via
  `ci_critical_value` (audit script, kpi inline band, `ladder_probe.mean_ci90`). Large-n
  values stay ~identical; small-n deltas are recorded per-stat in the findings doc + PR.
- Goldens are regenerated exactly once for the t-switch â€” never silently, never twice.
  Any other golden mismatch means the code moved a value it shouldn't have: fix the code.
- `KPI_PAYLOAD_VERSION` stays 257. No payload section renames. Wilson (proportions) and
  power/planning constants are out of the switch (different estimators).

## Scope & File Boundaries (operator override 2026-10-14: original page frozen)

- FROZEN (no structural/value/layout edits): tab-2 renderers in `dashboard/static/app.js`
  (`renderAnalyticsSurface`, `renderKPIs`, the 8 readouts, `renderQuantRiskGrid`,
  `decisionGatesHtml/Rows`, Tier-1 row), tab-2 sections of `dashboard/static/index.html`.
  Two information-only exceptions, both invisible to layout and values: (a) card titles
  gain unit suffixes (`$`/`Â¢`/`%`/`bps`/`s`/`Ã—`) â€” the operator's own screenshot complaint;
  (b) defaulted params that preserve current behavior byte-for-byte (renderer host-id /
  chart-ids params for the new tab's use, `renderCapitalRisk` curve-name override).
  The 2a/2b ledger tests keep scanning `app.js` as written â€” no rescope needed.
- ALLOWED on the original surface only: the tab-switcher shell (4th tab button), the
  `<script>` include for the new module, and optional target-id params on chart fns
  (defaults preserve current behavior byte-for-byte).
- T3 work area: NEW `dashboard/static/data_analysis.js` (registry, claim ledger, one tile
  builder, units, restyle â€” scoped CSS in the new tab's markup), a new tab-4 panel in
  `index.html`, and mount wiring in `dashboard/static/prototype.js` if the tab needs it.

- Target files for modification (T2 items below are DONE and committed):
  - `dashboard/static/data_analysis.js` (NEW) â€” registry, claim ledger, one tile builder,
    units, restyle; the only JS the new tab renders.
  - `dashboard/static/index.html` â€” new tab-4 panel + tab button + module `<script>` include
    (+ optional chart target containers). Tab-2 markup untouched.
  - `dashboard/static/app.js` â€” tab-switcher 4th-tab support + optional chart target-id
    params (defaults = current behavior) ONLY. No renderer/title/value edits.
  - `dashboard/static/prototype.js` â€” mount wiring for the new tab only if needed.
  - `core_brain/statistical_analytics.py` â€” one public normal-interval helper (explicit `z`),
    `_percentile` convention docstring; exact op order preserved. [T2 done]
  - `core_brain/kpi.py` â€” call the new helper; add a public Wilson name (`_wilson_ci` kept
    as alias); expose the sufficiency z table for import. No semantics change. [T2 done]
  - `core_brain/` â€” one neutral module for the queue-multiple formula shared by the live
    report and the shadow fill model. [T2 done]
  - `scripts/statistical_trade_audit.py`, `scripts/ladder_probe.py`,
    `scripts/paired_depth_report.py`, `scripts/stat_gate.py`,
    `scripts/family_probe_report.py` â€” import shared math; adapters only; printed layouts stay.
    [T2 done]
  - `statistical_validation_run/artifacts.py` â€” import method tuples from `kpi` (no local
    tuples). Layouts and report-only computations stay. [T2 done]
  - `tests/` + `tests/js/` â€” goldens, parity, registry-ledger tests (2a/2b scan `app.js`
    as written; units land in the shared renderers).
- MUST NOT modify: `data/orders.db` (production registry â€” read-only); top-nav structure
  (#504 owns it); close-method classification rules (#520 owns them); any report/artifact
  layout; `paired_depth_report`'s `.842` pilot constant and `max(2, n)` rule.

## Quality bar

- Zero regressions: focused suites for touched modules pass
  (`test_statistical_analytics`, `test_account_kpi`, `test_analytics_api`,
  `test_analytics_surface_mount`, `test_statistics_report`, `test_shadow_run` boundary).
  Full suite is CI's merge gate, never a local step.
- New behavior (registry ledger, goldens, parity) requires tests; the ledger test must be
  RED before the registry exists and GREEN after.
- Anti-cheat: no skipped/disabled tests, no deleted assertions, no linter suppression.
- No new external dependencies without explicit operator approval.
- UI: keep `tablist` semantics, keyboard access, and empty/error states. Stats-card restyle
  is operator-ordered: minimal new CSS scoped to the stats surface only, same palette
  family, no global stylesheet changes. Every registry entry declares `unit`
  ($/Â¢/%/bps/bare); every rendered card shows its unit â€” the missing-unit audit is T3 work.

## Environment quirk (verified at intake)

- This host's shell exports `PORT=0`, which breaks dashboard test collection. Run focused
  suites with `unset PORT` first or via `.venv/Scripts/python -m pytest`. Never hand pytest
  commands to the operator â€” agent-run only, reported as background results.
