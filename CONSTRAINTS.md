# CONSTRAINTS - #541 (locked by Station II, enforced through Station V)

Governs the `i541/refactor-consolidate-every-project-statistic-onto` branch only.
The #524 contract this file replaces is branch-scoped by its own header line and stays
readable in git history.

## Values rule (operator-ordered t-switch)

- Phase-1 goldens freeze today's numbers FIRST (pre-switch record) and pass before any
  production change.
- The one deliberate value change: every mean interval switches to Student's t via
  `ci_critical_value` (audit script, kpi inline band, `ladder_probe.mean_ci90`). Large-n
  values stay ~identical; small-n deltas are recorded per-stat in the findings doc + PR.
- Goldens are regenerated exactly once for the t-switch — never silently, never twice.
  Any other golden mismatch means the code moved a value it shouldn't have: fix the code.
- `KPI_PAYLOAD_VERSION` stays 257. No payload section renames. Wilson (proportions) and
  power/planning constants are out of the switch (different estimators).

## Scope & File Boundaries (operator override 2026-10-14: original page frozen)

- FROZEN (no structural/value/layout edits): tab-2 renderers in `dashboard/static/app.js`
  (`renderAnalyticsSurface`, `renderKPIs`, the 8 readouts, `renderQuantRiskGrid`,
  `decisionGatesHtml/Rows`, Tier-1 row), tab-2 sections of `dashboard/static/index.html`.
  Two information-only exceptions, both invisible to layout and values: (a) card titles
  gain unit suffixes (`$`/`¢`/`%`/`bps`/`s`/`×`) — the operator's own screenshot complaint;
  (b) defaulted params that preserve current behavior byte-for-byte (renderer host-id /
  chart-ids params for the new tab's use, `renderCapitalRisk` curve-name override).
  The 2a/2b ledger tests keep scanning `app.js` as written — no rescope needed.
- ALLOWED on the original surface only: the tab-switcher shell (4th tab button), the
  `<script>` include for the new module, and optional target-id params on chart fns
  (defaults preserve current behavior byte-for-byte).
- T3 work area: NEW `dashboard/static/data_analysis.js` (registry, claim ledger, one tile
  builder, units, restyle — scoped CSS in the new tab's markup), a new tab-4 panel in
  `index.html`, and mount wiring in `dashboard/static/prototype.js` if the tab needs it.

- Target files for modification (T2 items below are DONE and committed):
  - `dashboard/static/data_analysis.js` (NEW) — registry, claim ledger, one tile builder,
    units, restyle; the only JS the new tab renders.
  - `dashboard/static/index.html` — new tab-4 panel + tab button + module `<script>` include
    (+ optional chart target containers). Tab-2 markup untouched.
  - `dashboard/static/app.js` — tab-switcher 4th-tab support + optional chart target-id
    params (defaults = current behavior) ONLY. No renderer/title/value edits.
  - `dashboard/static/prototype.js` — mount wiring for the new tab only if needed.
  - `core_brain/statistical_analytics.py` — one public normal-interval helper (explicit `z`),
    `_percentile` convention docstring; exact op order preserved. [T2 done]
  - `core_brain/kpi.py` — call the new helper; add a public Wilson name (`_wilson_ci` kept
    as alias); expose the sufficiency z table for import. No semantics change. [T2 done]
  - `core_brain/` — one neutral module for the queue-multiple formula shared by the live
    report and the shadow fill model. [T2 done]
  - `scripts/statistical_trade_audit.py`, `scripts/ladder_probe.py`,
    `scripts/paired_depth_report.py`, `scripts/stat_gate.py`,
    `scripts/family_probe_report.py` — import shared math; adapters only; printed layouts stay.
    [T2 done]
  - `statistical_validation_run/artifacts.py` — import method tuples from `kpi` (no local
    tuples). Layouts and report-only computations stay. [T2 done]
  - `tests/` + `tests/js/` — goldens, parity, registry-ledger tests (2a/2b scan `app.js`
    as written; units land in the shared renderers).
- MUST NOT modify: `data/orders.db` (production registry — read-only); top-nav structure
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
  ($/¢/%/bps/bare); every rendered card shows its unit — the missing-unit audit is T3 work.

## Environment quirk (verified at intake)

- This host's shell exports `PORT=0`, which breaks dashboard test collection. Run focused
  suites with `unset PORT` first or via `.venv/Scripts/python -m pytest`. Never hand pytest
  commands to the operator — agent-run only, reported as background results.
