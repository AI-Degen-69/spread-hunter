# SPEC — #541: One statistics page, one producer per number

Branch: `i541/refactor-consolidate-every-project-statistic-onto` | Tier: Large | Type: Code + Design/UI

Supersedes the #517 spec in this file's history (kept in git).

## Goals

1. The operator opens the statistics page and finds **every figure the project computes, each shown once**, grouped so it can be found — no number rendered twice, none repeated across views.
2. Any number on the page traces to **exactly one producer**: a metric registry maps each statistic id to its single reader, and a second readout claiming the same number fails a test.
3. `scripts/statistical_trade_audit.py` **holds no math of its own**: z table, mean CI, proportion CI, and sample-size planner all import from `core_brain`, and the audit still prints.
4. No silent renames: the family probe's nearest-rank `_percentile` says what it is, and its divergence from the interpolated core helper is recorded.
5. **One convention: Student's t.** Every mean interval uses `ci_critical_value` (t while
    the sample is small, normal once it isn't). Pre-switch values freeze in goldens first;
    every delta lands in the findings doc and the PR. Proportion intervals stay Wilson
    (proportions don't take t); power/planning constants stay normal-approx (different
    estimator, standard practice).
6. A figure on the page and the same figure in `reports/*_statistics_report.md` carry the **same value** (layouts untouched).
7. Every card shows its unit (`$`, `¢`, `%`, `bps`) or is registry-flagged bare (e.g. SQN);
    no more bare numbers with an ambiguous denomination. The stats page reads like a trading
    terminal — minimal, value-first cards, consistent type, aligned spacing.

## Out of scope (from the issue — binding)

No new statistic, model, chart, or metric. No `/api/kpi` semantics change: `portfolio`, `trade_analytics`, `statistical_analytics` stay, no renames, `KPI_PAYLOAD_VERSION` stays 257. Report/artifact layouts stay. Top nav (#504), command palette (#477), close-method rules (#520 — adjacent, do not restate), GO-gate thresholds (#471), tournament arms (#487/#492) all stay out.

## Resolved open questions (defaults adopted, confirmed against code)

1. **Add a tab, keep the original (operator override 2026-10-14).** Tab 2 stays frozen
    as the legacy page — no renderer or layout edits there. A new fourth tab, "Data Analysis",
    is the canonical one-statistics page: registry, ledger, units, and restyle all land there,
    reusing (copied or redesigned) components. Legacy-tab removal is a follow-up, out of scope.
2. **Keep the four views → switcher becomes a filter, one placement per tick.** The Sensitivity Simulator button is already `display:none` (index.html:440) — remove the dead button rather than leaving it.
3. **Shared-reader reach → unify duplicated readers; leave report-only stats, list them in the PR.** Lifetimes, cancel reasons, queue sections, rescue rates, run counters, validation verdict, and research-only figures (tape/probe/two-arm) stay in their products, registered as report-only with producer + reason.
4. **Duplicate = both shapes.** Two producers of one number AND one number placed twice both count. Population rule: same population + same measure → one placed, other `aliasOf`; different population/curve → both placed, each labeled (equity-curve vs account-curve drawdown; gate input marked).

## Ordering note

#520 is OPEN, so close-method groups import from the existing `core_brain/kpi.py` tuples (`MERGE_METHODS`, `SINGLE_BUY_EXIT_METHODS`) — its rules are not restated here. #542 is CLOSED via #543 (merged), so the audit cross-check database (`data/02_shadow_prudent_13-10_03-01.db`) is usable at build time.
