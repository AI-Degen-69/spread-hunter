# Plan: Issue #314 — D12 submarket admission trial (family-grouped arms, pre-registered rule)

**Branch:** `i314/d12-submarket-admission-trial-family-grouped-arms` | **Issue:** #314

**Size tier:** Large — cross-cutting (ranker + shadow loop + analyzer + pre-reg doc),
new files (`scoring/family_admission.py`, `tests/test_family_admission.py`,
pre-reg doc), a new bundle format. Rationale: four modules change plus a new
contract between ranker and shadow.
**Task type:** Code + Docs (pre-registration run document).

## What the issue requires (verbatim, condensed)

- "Run the operator-approved submarket admission as a measured, reversible two-arm
  shadow experiment on its own trial feed, with the decision rule and window fixed
  before the first fill."
- Control: shipped `identity_allowed`. Treatment: at most one market per event,
  best eligible mainline; one eligible submarket fallback only if no mainline
  qualifies, with reason. Blocked-keyword / resolved / unreadable rows stay refused.
- Shadow arms read one arm each with family attribution; treatment-only guard: one
  fallback holding per event.
- Read-only analyzer; verdict from four pre-registered h3 bars only
  (markout parity ≥ −0.01, paired 95% interval lower bound > −0.01, drawdown ≤ $1
  per $100 bankroll, single-buy-exit loss per fill notional ≤ control);
  `inconclusive` on any limitation (30-cluster floor, missing h3, unequal
  bankrolls, short/aborted run, snapshot mismatch, zero notional).
- Pre-reg doc before launch; 100-hour endpoint; no efficacy peeking; launch is an
  operator decision. This ticket does NOT launch long-running loops.

## CodeRabbit intake (read once; echo ignored)

- Adopted: skeleton (Phase 1 ranker+shadow / Phase 2 analyzer+doc), all named test
  lists, seam pointers (verified against live code — line numbers corrected where
  drifted: shadow telemetry seam `:488-498`, depth bundle `:1294-1388`), the six
  design choices (event-cluster grouping, relax group-label + not-primary only,
  mainline-preferred fallback, one pass / one snapshot, ranker + shadow guard,
  fail-closed missing marks + fill-notional denominator), prohibited-files list.
- Rejected: over-split phasing — merged into 4 atomic tasks below; "family-clustered
  interval" wording (existing unit is `event_cluster_id`); grouping by `family_key`
  (template label — tag only); inventing an in-progress gate or touching caps.
- Resolved from code (was `[UNVERIFIED]`): refusals at `selector.py:117/:125`
  confirmed verbatim; blocked-keyword arm reads title+slug+category+type+group+
  series+event (pass group label as `market_group` — both title and groupItemTitle
  covered); `family_probe.py` has no `filter_markets` import (no cycle risk);
  `cutoff_usd REAL NOT NULL` in 3 tables (additive columns + CREATE-only change
  confirmed); `market_universe.json` writes into the parameterized out-dir;
  evaluated rows carry `series_title`/`event_title` (family tag available); markout
  sign is `(reference - price) * direction`, excess = raw − peer.
- No sub-issue mapping: single implementer, this file is the tracker; avoids
  tracker spam for a branch that closes with its parent issue.

## Improvement proposal (adopted by default)

Evidence, from the issue: "Create `docs/runs/<date>-paired-admission-experiment.md`".
Proposal: pin the filename date to today — `docs/runs/2026-09-30-paired-admission-experiment.md`
— so Station III never guesses the name. Dropped only on explicit operator rejection.

## Dependency graph

- T1 (bundle format + tags) → T2 (shadow reads the format).
- T1 + T2 (columns/attribution the analyzer reads) → T3.
- T1 + T2 + T3 → T4 (doc + rehearsal exercises all three).
- Checkpoints: after T2 (feed + consume works end to end on fixtures); after T4.

## Tasks

### T1 [Backend/Logic] Ranker admission axis + atomic trial bundle (L) — DONE 2026-09-30 (commit 32d3cae)
Target files: new `scoring/family_admission.py`; `scripts/filter_markets.py`
(trial `evaluate` mode, `build_paired_admission_bundle`, `--paired-admission`
CLI); `scripts/filter_loop.py` (forwarding); new `tests/test_family_admission.py`;
`tests/test_filter_loop.py` (+1 test).
What is built: pure identity classification (shipped verdict via unchanged
`identity_allowed(require_primary=True)` + treatment role; group-label/not-primary
refusals re-checked with label withheld + `require_primary=False`; blocked-keyword
always refused incl. groupItemTitle terms); one-pass control + treatment selection
(one mainline per event, one fallback w/ reason); `spread_hunter.paired-admission.v1`
bundle, one snapshot, atomic publish + audit line only on success; CLI guards
(isolated out-dir, no dry-run/trials/depth flags, complete listing, permanent
volume gate, event-identity required).
Helper skill: `test-driven-development`. Depends on: none.
Verification: every named T1 test fails before / passes after; regression files
(`test_paired_depth`, `test_unified_universe`, `test_family_probe`,
`test_filter_markets_publish_json`) green.

### T2 [Backend/Logic] Shadow arm consumption + attribution + fallback guard (L) — DONE 2026-09-30 (commit 800d804)
Target files: `core_brain/market_feed.py` (format branch); `core_brain/shadow_run.py`
(`--paired-admission-arm`, mutually exclusive with depth arm, null cutoff for
admission only); `core_brain/paired_shadow.py` (additive `trial_axis`,
`family_label`, `admission_role` columns; attribution copy; treatment-fallback
guard vs open same-event exposure + feed event); tests in `test_market_feed.py`,
`test_shadow_run.py`, `test_family_admission.py`.
Helper skill: `test-driven-development`. Depends on: T1.
Verification: named T2 tests RED→GREEN; depth-arm tests + `markets_fn` precedence
unchanged and green. Checkpoint: ranker bundle → shadow arm reads green on fixtures.

### T3 [Backend/Logic] Read-only admission analyzer + four-bar verdict (L) — DONE 2026-09-30 (commit 2ff3052)
Target files: `scripts/paired_depth_report.py` (admission axis; joint
event-cluster bootstrap sibling; per-arm h0–h3 means + coverage; four h3 bars;
adopt/reject/inconclusive); `tests/test_paired_shadow_report.py` (all named
admission cases on tmp-path stores + read-only immutability test).
Helper skill: `test-driven-development`. Depends on: T2.
Verification: named T3 tests RED→GREEN; existing depth fixtures (`:255-375`,
`:428-458`) unchanged and green; no output under `data/` or `run/`.

### T4 [Docs] Pre-registration doc + short rehearsal, no launch (S)
Target files: new `docs/runs/2026-09-30-paired-admission-experiment.md` (four bars,
verdict classes, 30-cluster floor, missing-mark rule, fill-notional denominator,
100-hour endpoint, no-peeking, arm setup with unique store/run-id/port per arm,
identical bankroll/caps/cadence, `data/orders.db` + shadow-01/02 never targets,
status "not started; launch is an operator decision", hands-on How-to-verify
without gh/git-status/pytest steps); rehearsal artifacts under
`runtime/trials/<id>/` + report in `reports/` (short `--minutes N` shadow reads of
one arm each on unique stores — opening commands stay operator-run: propose the
exact commands, do not run loops that spend venue reads beyond the read-only
ranker pass without operator say-so).
Helper skill: `documentation-and-adrs`. Depends on: T1, T2, T3.
Verification: doc exists with the full rule text matching T3's implementation;
rehearsal bundle + analyzer output present in trial dir.

## Rejection log

None yet. The pinned doc filename (`2026-09-30-...`) is recorded above; if the
operator rejects the date, note it here and rename in T4.
