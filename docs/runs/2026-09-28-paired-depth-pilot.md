# Paired-depth variance pilot: preregistration (σ prior for $250 vs $500)

**Status:** Preregistered. **No pilot run has been started.** This document is frozen before the
pilot launches; the freeze record in §10 is filled once at freeze time and never edited afterward.
Companion to [2026-09-28-paired-depth-experiment.md](2026-09-28-paired-depth-experiment.md)
(the main protocol), which consumes this pilot's single output.

## 1. Purpose — and what this pilot is not

Purpose: produce **one number** — the paired per-cluster standard deviation σ, in dollars per
$100 bankroll per 100 elapsed hours — to feed the main experiment's registered power formula

```
n ≈ ((1.96 + 0.842) · σ / δ)²        δ = $1 per $100 per 100h
```

This pilot is NOT:

- a measurement of the depth effect. Its own uplift must not be read as evidence for either bar
  (peeking rule, §8.3);
- a substitute for the main run;
- allowed to reuse, re-score, or retouch any historical shadow (§3).

## 2. The measured quantity, defined exactly

σ is the sample standard deviation (ddof = 1) of the per-cluster paired differences the analyzer
already computes — nothing else:

- `d_i = (treatment cluster contribution) − (control cluster contribution)`, where a cluster's
  contribution is `(realized closes + terminal float − starting float) × (100 / bankroll) ×
  (100 / elapsed_hours)`;
- clusters are Gamma event identities (`event_id`, else `event_slug`) — the same mapping
  `scripts/paired_depth_report.py` uses;
- only clusters exposed in the common window count, exactly as in the final analysis.

The estimator and the units MUST be identical to the final analysis, because the power formula's
δ lives in those units. **No new code:** σ is read from the analyzer run over the pilot stores —
`clusters.paired_standard_deviation`, or the chi-square bound of §7 when the selection rule
requires it.

## 3. Excluded sources (registered refusals)

| Source | Why it cannot supply the prior |
| --- | --- |
| shadow-03 | Independently rescored after the fact; not a causal control; no paired arm. |
| shadow-06 | Changed depth AND volume on a mismatched time window — the exact confound the paired design exists to remove. |
| shadow-01 | Control-only. May be used ONLY as a crude sanity bound on σ's magnitude, never as the registered prior: a paired difference is less variable than an unpaired one, so an unpaired spread is an upper-flavored hint, not an estimate. |
| `stat_gate.py --dry-calc` | Independent-close assumption; not paired, not cluster-aware. |

Structural note for the audit: stores 01/03/06 contain no `shadow_paired_*` tables at all — a
paired σ is not merely unrecorded there, it is uncomputable.

## 4. Design

- **Structure:** one paired filter loop + two credential-free shadow runs — the main experiment's
  machine, unchanged. Same arms: control $500, treatment $250.
- **Config parity with the main run** (the prior transfers only if the exposure process is the
  same): top-N from `_get_top_markets`, $100 equal starting bankroll, shipped risk caps, 5s
  rotation cadence, 24h volume pinned to the shipped $125,000 bar, no volume/spread/legacy trials.
- **Window:** 72 continuous hours (`--minutes 4320`) per arm, both arms started together.
- **Continuity:** no restarts, no machine sleep/reboot, no loop-code edits mid-window. ANY
  interruption voids the pilot; restart from zero with a fresh pilot slug and fresh stores.
  Partial windows are never averaged or salvaged.
- **Why 72h:** under the linear-variance model (Var ∝ T), a 72h window normalized to 100h
  overstates the 100h-run σ by ≈ √(100/72) ≈ **1.18×**. Real accrual is capped by bankroll limits,
  which pushes the same direction. The bias is conservative — it can only demand MORE clusters,
  never fewer — and is accepted knowingly. A shorter window produces single-merge lottery
  clusters instead of information and is refused (§6).

## 5. Preregistered commands

PowerShell. `<pid>` is a NEW pilot slug (e.g. `pilot-2026-10-01`); trial directories and stores
are never reused.

```powershell
# 1. Paired feed loop — start first, keep running for the whole window.
python -m scripts.filter_loop --out-dir runtime/trials/<pid> --trial-depth 250 --paired-depth-control-usd 500

# 2. Control arm (separate terminal).
python -m core_brain.shadow_run --minutes 4320 --interval 5 --db data/<pid>_control.db --run-id <pid>-control --markets-path runtime/trials/<pid>/paired_markets.json --paired-depth-arm control --paired-starting-bankroll-usd 100

# 3. Treatment arm (separate terminal).
python -m core_brain.shadow_run --minutes 4320 --interval 5 --db data/<pid>_treatment.db --run-id <pid>-treatment --markets-path runtime/trials/<pid>/paired_markets.json --paired-depth-arm treatment --paired-starting-bankroll-usd 100

# 4. At the endpoint — the read-only analyzer.
python -m scripts.paired_depth_report --control-db data/<pid>_control.db --treatment-db data/<pid>_treatment.db --control-run-id <pid>-control --treatment-run-id <pid>-treatment --output reports/paired-depth-pilot-<pid>.json
```

Before steps 2–3: inspect `runtime/trials/<pid>/paired_markets.json` against the main protocol's
launch checklist (one shared snapshot id, both arms nonempty, cutoffs 500/250, volume gate
125000). Stop the filter loop when both arms finish. Launching the pilot is an operator decision,
like the main run — two long rehearsals under the same safety envelope (no signer, isolated
stores, `data/orders.db` refused).

### 6a. Environment gate — checked 2026-09-28 (two rounds)

**Round 1 (superseded diagnosis):** the paired ranker refused with `truncated: true`. Initial
read blamed the venue; a direct probe disproved that — Gamma paginates fine past 500 rows with
zero errors. The real cause: the plain rank's boundary policy (stop one page past the volume
floor) sets `truncated` as a POLICY stop, and the paired completeness gate refuses on that flag,
so paired mode refused on EVERY rank. **Fixed:** paired mode now paginates the listing to
exhaustion (`full_scan` forced on); the gate remains as the safety net for a genuinely failed
read. Sub-floor rows are cheap-rejected inside the scan, so only pagination costs more.

**Round 2 (post-fix):** the bundle published cleanly — one shared snapshot id, cutoffs 500/250,
volume gate 125000, machine-refusal wiring intact — but **both arms came back EMPTY: 0 eligible
markets out of a 25-market universe above the $125k volume floor** (rejections: 17 submarket
group labels, 5 non-primary identity types, 2 decided mids, 1 pre-start). The pipeline mechanics
are proven end to end; the MARKET ENVIRONMENT currently offers no market that clears the
identity + decided-mid + spread + horizon gates on top of the volume floor.

**Consequence:** the pilot window still cannot open — not because of code, but because an empty
paired bundle refuses at the shadow-run gate by design (`paired-depth feed arm is empty;
refusing an unmeasurable run`). Re-check before launch: a plain rank must show a nonempty
eligible pool AND a paired bundle with nonempty arms on both sides. This is a market-population
condition and may persist for days; do not loosen any gate to force it.

**What the shakedown proved overall:** the paired-mode completeness gate and its machine marker
fire and are wired into the loop's `paired_universe_truncated` event; the full-scan fix holds;
the isolated trial directory lifecycle (create, publish, clean) works; and no other experiment
process was touched. The operator's own stack (filter loop + shadow-01 run) ran throughout,
untouched.

## 6. Acceptance criteria (ALL must hold; any failure voids the pilot)

1. Both runs finished cleanly: `shadow_paired_runs.status = 'finished'` and
   `finished_at − started_at ≥ 4320s` per arm. A short run is a hard refusal, not a caveat.
2. The pilot report's `limitations` contains exactly ONE entry — the self-referential
   "no independent paired pilot variance supplied" (unavoidable: the pilot cannot supply its own
   prior). ANY other limitation — missing attribution, invalid or stale marks, snapshot mismatch,
   feed/market coverage events, unequal bankrolls, short run — voids the pilot.
3. `clusters.count ≥ 20` (target 30). Below 20 no usable σ exists (§7).
4. Feed health across the window: the paired bundle stayed readable and both arms observed the
   same snapshot set (both surface as limitations, hence criterion 2).

A voided pilot is restarted from zero, never averaged with anything.

## 7. σ selection rule (mechanical, preregistered)

Let `k = clusters.count` and `s = clusters.paired_standard_deviation` (sample stdev, ddof = 1).

- **k ≥ 30** → registered σ = s, the point estimate (relative standard error ≈ 1/√(2(k−1)) ≤ ~13%).
- **20 ≤ k < 30** → registered σ = the 75% upper confidence bound
  `s · sqrt((k−1) / χ²_0.25(k−1))`, where `χ²_0.25(k−1)` is the 25th percentile of the
  chi-square distribution with k−1 degrees of freedom. This prices the estimation uncertainty
  into the power calculation on the safe side.
- **k < 20** → the pilot is insufficient (criterion 3 already voids it): extend the window, or
  declare the main run screening-only BEFORE it starts.

The selection is made once, at freeze time, from the pilot report — never after seeing main-run
results.

## 8. Independence audit (at freeze time; all four must pass)

1. **Provenance:** the pilot stores and run IDs are new artifacts created after this document's
   date; stores 01/03/06 were not read, moved, or modified (and contain no `shadow_paired_*`
   tables to read).
2. **Policy:** `shadow_paired_runs` in the two pilot stores shows control/$500 and
   treatment/$250 — both arms under the registered policy, the opposite of shadow-06's
   single-run confound.
3. **Chronology:** pilot `finished_at` (both arms) < main run `started_at`. The prior is computed
   from data closed before the experiment opens, so no two-way peeking is possible; conversely,
   the pilot's own uplift is not to be inspected for decisions.
4. **Bit-for-bit:** the number passed as `--pilot-paired-sigma-usd-per-cluster` in the final
   analysis equals the frozen value recorded in §10; the final report's
   `power_analysis.pilot_paired_sigma_usd_per_cluster` is compared against this document at
   review. Recomputing σ after the main run is prohibited, whatever the result.

## 9. Sequence and gating

1. This preregistration committed.
2. The 15-minute authorized shakedown (main protocol's readiness gate) passes.
3. Pilot launched (operator-authorized).
4. Analyzer over the pilot stores → acceptance criteria (§6) checked.
5. **Freeze:** σ recorded in §10 with its sources; independence audit (§8) signed.
6. Only then may the main 100-hour run start. Without a frozen, audited σ, the main run is
   screening-only and an inconclusive result cannot justify changing the shipped bar.

## 10. Freeze record (filled once at freeze time; never edited afterward)

| Field | Value |
| --- | --- |
| Pilot slug | |
| Control store / run id | |
| Treatment store / run id | |
| Control started_at / finished_at | |
| Treatment started_at / finished_at | |
| Clusters observed (k) | |
| `paired_standard_deviation` (s) | |
| Selection rule applied (point / 75% bound) | |
| **Registered σ** | |
| Pilot report artifact (+ sha256) | |
| Acceptance criteria checked by / date | |
| Independence audit checked by / date | |

## How to verify (operator, hands-on)

1. `python -m core_brain.shadow_run --help` → lists `--markets-path`, `--paired-depth-arm`,
   `--paired-starting-bankroll-usd` exactly as written in §5.
2. `python -m scripts.paired_depth_report --help` → lists `--pilot-paired-sigma-usd-per-cluster`
   and `--output` (the freeze and audit hooks).
3. Open the main protocol doc, comparison-design step 8 → it points to this document as the
   pilot preregistration.

A failed check looks like: a flag in §5 that `--help` does not list — that command would die at
launch and the 72-hour window would be lost. Fix the document before authorizing the pilot.
