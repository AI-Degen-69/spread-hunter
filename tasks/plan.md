# Plan — Issue #49: short-window ladder strategy (BTC/ETH 5-min & 15-min)

Branch: i49/d11-short-window-ladder-strategy-for-btc-eth | Issue: #49
Size: Large (new strategy path, 5+ files when built) · Type: Code + Research · Stack: Python + pytest
Execution order: probe-first. T3/T4 are gated on a probe go and do not start on numbers-free assumptions.

## Operator direction (2026-09-30, supersedes the design note where it conflicts)

1. Bankroll: SEPARATE ladder allocation, not shared Dynamic Caps.
2. Exit: time-boxed exit (recommendation adopted as starting hypothesis) + try variants in the probe, then conclude from data.
3. Proof: design note first — already exists (`docs/ideas/short-window-ladder.md`), so the next build step is the replay probe, not the ladder itself.

Divergence note: the design note (2026-09-13) recommends SHARED caps. The operator now directs SEPARATE.
T1 writes this into the note as an addendum; nothing is built on the shared-caps assumption.

## CodeRabbit plan intake (read once; echo ignored)

- Adopted: separate gated ladder path (off = byte-identical quoting); new discovery function leaving `fetch_live_market` alone; one `pair_id` per ladder via existing `record_submit`; `load_pair` token-grouping reuse for one-leg exits; `ladder_exit` close method added to the naked-close sets; shadow `markets_fn` seam; screener untouched; configurable shape/timers.
- Rejected: building Phases 1–4 (config, quotes, shadow logging, exits) before probe numbers exist. The issue text and the owner block both require data before implementation, so the phase order is flipped: probe → go/no-go → build.
- [UNVERIFIED] (spot-checked, not fully traced): `record_submit` multi-intent + per-rung caps behavior; `cycle_stream` submit-count overwrite; exact `markets_fn` seam shape in `shadow_run.py`; full list of naked-close match sites. Station III verifies each against live code before touching it.

## Resolved from code (no operator time needed)

- Single-pair path can stay frozen behind a switch: quoting funnels through `_decide_quotes_from_mid` / `evaluate_market_quote`, so a separate gated function keeps off-behavior identical. Verifiable by the off-test.
- One-leg residue already collapses multi-rung sides: `load_pair` groups by token, so no new exit grouping is needed — only the timer and the label.
- Close-method sets live in named constants/pattern matches (`order_registry.py`, `kpi.py`); adding one value is mechanical.

## Genuinely unresolvable from code (asked 2026-09-30, answered above)

- Shared vs separate bankroll → SEPARATE (operator).
- Exit shape → timed exit hypothesis + probe variants (operator).
- Proof order → design note, then probe (operator).

The issue's Q1–Q3 (rung count, exit window, order lifetime) stay open by design — the probe answers them with CIs, not opinions.

## Spec (embedded; full SPEC.md update deferred until probe go)

- Goal: ladder at series OPEN (first ~30s of `start_ts`) on BTC/ETH 5+15-min series only; equal-sized rungs around 0.50; any opposite fills merge at < $1.00; one-leg residue exits on a timer; everything shadow-only.
- Acceptance: (1) ≥2 submit events/side at distinct prices within 30s of open in cycle telemetry; (2) fills oldest-first under one `pair_id`; (3) one-leg exits inside the window, no orphans; (4) mode off = today's single price, suite green; (5) screener output unchanged.
- Out of scope: live execution, screener changes, cross-market portfolio coordination, shared-cap accounting (separate allocation per direction).

## Dependency graph

- T1 (addendum) → T2 (probe) → CHECKPOINT 1 (go/no-go) → T3 (ladder path) → T4 (shadow wiring + tests) → CHECKPOINT 2.
- T3 and T4 do not start on a no-go. Sub-issue mapping deferred until CHECKPOINT 1 go (no tracker noise for gated work).

## Tasks

### T1 — [Docs] Addendum: separate allocation (XS)
Record the 2026-09-30 direction in the design note: separate ladder allocation, what it bounds (per-rung sizing, blended-residue check against the ladder budget, ceiling interaction), and that shared-caps reasoning is superseded.
Depends on: none. Verify: read-through of the addendum diff.

### T2 — [Research] Read-only replay probe (M)
`scripts/` harness replays recorded BTC/ETH series tapes, simulates 2/4/6-level shapes × exit policies (timed exits + hold-to-close baseline), and reports fill-rate CIs + mean-PnL CIs. One-leg residues scored at real resolution outcomes, never zero. Answers rung count and exit window; order lifetime stays open — or the probe reports "unmeasurable".
Depends on: T1. Verify: probe runs on a fixture tape and prints both terms of the verdict formula (P(pair)×edge − P(one-leg)×loss).

CHECKPOINT 1: probe numbers in hand → go (build T3/T4) or no-go (stop, record why). One-line report, not a full pause in auto mode.

CHECKPOINT 1 OUTCOME (2026-09-30): NO-GO for T3/T4. The probe is built, 13/13
focused tests green, CLI proven read-only on the real store — but the store
cannot feed it: `price_tape.db` records one token per market (183 markets, one
token each), and none is a 5-min/15-min up/down series market. The probe
reported `markets: 0, strategy: unmeasurable`, exactly as designed. Next data
step before any ladder code: record both legs of the BTC/ETH 5-min and 15-min
series (both clob token IDs per market), then rerun the probe. T3/T4 stay
gated; nothing was built on numbers-free assumptions.

### T3 — [Backend/Logic] Ladder path behind the switch (L, gated)
Config (`ladder_mode` off default + shapes/timers/budget), series discovery (upcoming markets, `fetch_live_market` untouched), separate ladder decision function (open-window gate, equal sizes, distinct-side counting), per-rung telemetry, `ladder_exit` close method in all naked-close sets.
Depends on: CHECKPOINT 1 go. Verify: focused tests for config validation, discovery ordering, ladder gating.

### T4 — [Backend/Logic] Shadow wiring + proof tests (M, gated)
Crypto series into shadow via the injectable seam (screener untouched); tests: placement (≥2 submits/side, distinct prices), fill-sim (oldest-first, one `pair_id`), one-leg exit (inside window, no orphan), off-test (single price, suite green).
Depends on: T3. Verify: the four tests fail without the change, pass with it.

CHECKPOINT 2: shadow rehearsal shows rungs resting, filling, and exiting per spec. One-line report.

## Improvement proposal (adopted by default)

Build the probe before the ladder, not after — the issue requires probe data before implementation, so code tasks stay gated on probe numbers.
Evidence, verbatim from the issue: "### Open questions — to be answered by an objective probe before implementation" and owner: "Status: not started, and it should not be started without a direction from you first."
Rejected: none. Scope expansion: none proposed.

---
# Plan — Issue #324: shadow rehearsal on BTC+ETH 5-min ladders (no signer)

Branch: i324/d11-followup-shadow-rehearsal-on-btceth-5-min | Issue: #324
Size: Standard · Type: Code · Stack: Python + pytest
Status: BUILT 2026-09-30. Gate was GO (BTC probe `reports/ladder_probe_btc5.json`
verdict go, shape 2 / exit_60, 200 markets; ETH probe `reports/ladder_probe_eth5.json`
verdict go, shape 2 / exit_60, 200 markets, posted on #323). No production code changed.

## What was built
- `scripts/ladder_shadow_rehearsal.py`: parameterized harness (series tape or
  built-in 2-market fixture) driving `run_shadow` via the injectable seam.
  Ladder-mimic `decide_fn` posts the probe-winning shape with one `pair_id`
  per market (the `record_submit` carry semantic, pinned by test); rung
  lifecycle (filled retires, cancelled re-quotes, exit closes retire the
  market); tape-cursor trades (print at/through a rung = volume at that rung)
  and books; per-series JSON report with conservation.
- `tests/test_ladder_shadow_rehearsal.py`: 9 tests, RED-verified (stamp removal
  fails the placement tests), fully offline (tape driver in-process,
  resolution sweep stubbed, `tmp_path` stores).

## Rehearsal outcomes (operator-rerunnable; artifacts in C:/Temp, see report)
- Fixture: placements under one pair_id/market, oldest-first fills, 0 orphans,
  0 double-counts; balanced market merges, one-leg residue exits, all fills
  accounted; zero live execution (shadow ids, production store refused).
- BTC tape (6 markets, 12 rotations): 70 filled = 20 merged legs + 40 exited + 10 settled.
- ETH tape (6 markets, 12 rotations): 60 filled = 50 exited + 10 settled.
- Report fix during build: merge close rows count pair-units; conservation
  reads leg shares from `shadow_merge_legs` (+ exits + settlements).

## Handoff to #325 (findings, not code)
1. REFILL-AFTER-EXIT (filed as follow-up): `load_pair` naked is fills-only;
   prior `single_buy_exit` closes net only on the venue side, so re-posting
   exited shares under the same market-wide stamp trips the oversell
   pre-flight (`_check_positions` refuses; no oversell occurs, but the leg
   strands). The harness avoids it by retiring exited markets. #325 must
   decide the rung lifecycle (one-shot rungs vs re-post with a fresh pair)
   or net prior exits in exit sizing. Failing that decision, the ladder will
   strand refills the same way.
2. Grace-0 immediacy: with the shipped grace default (0.0), any one-sided
   fill exits the next pass -- a staggered opposite fill never gets to
   arrive. The fixture balances both legs in one rotation to show the merge
   path; operator grace (>0) would hold instead.
3. Re-gate vs high rungs: a kept rung is re-tested at rung + opposite ask,
   so 0.55 rungs churn against ~0.5x asks until the opposite leg is held
   (`hedge_held` exemption). Same pair_id throughout; harmless here, worth
   knowing for #325 telemetry.

---
# Plan — Issue #326: refill-after-exit strands legs under a market-wide pair_id

Branch: i326/d11-followup-refill-after-exit-strands-legs | Issue: #326
Size: Standard (one architectural decision + one conditional accounting fix, 2-3 files) · Type: Research + Code · Stack: Python + pytest
Execution order: repro-first. T3 builds only on a CHECKPOINT 1 netting-wins verdict; a lifecycle-wins verdict closes this issue on the decision note and hands the lifecycle code to #325.

## CodeRabbit plan intake
No `coderabbitai` plan comment exists on #326 (zero comments) — nothing to adopt, nothing to verify. Code-explorer persona not deployed: the trap's code paths (`load_pair`, `exit_single_buy`, `_check_positions`, `shadow_positions`, `record_submit` carry) were traced live during #324; the code is familiar, not legacy.

## Resolved from code (no operator time needed)
- The trap mechanics are verified from the #324 rehearsal: `load_pair` naked is fills-only (`get_size_matched`); prior `single_buy_exit` closes net only on the venue side; re-posting exited shares under the same stamp makes the next exit size fills-only naked against a netted venue position, and `_check_positions` refuses (correctly — no oversell, but the leg strands, every rotation the same way).
- The trap is live-reachable today, not ladder-only: the #206 carry semantic lets replacement legs join a resting complement's pair, so a same-pair refill after an exit can happen on the live loop too. This makes netting a genuine (small) live fix, not ladder scaffolding.
- `load_pair` must stay fills-only: `complete_pair`, merge accounting (`shadow_merge_legs`), and reports read the same view. Any netting lives locally in the exit-sizing path, never in the shared ledger view.

## Type-design lens (encapsulation / invariants, applied from persona)
- Invariant to preserve: "an exit sells at most venue-agreed shares" (today enforced by `_check_positions`). Netting must make the SIZED amount consistent with that check, not bypass it.
- Open wrinkle for T2/T3 (not decided here): `closes` carries `condition_id` + `method`, no `pair_id`. Attributing prior exits to one pair under condition-scoping over-nets when several pairs share a condition over time (common: re-quotes mint fresh pairs). Over-netting under-sells (fail-closed direction: strands residue); under-netting over-sells (the harm). The decision must pick the scoping and prove its bias with a test.

## Spec (embedded; full SPEC.md update rides with the #325 build)
- Goal: decide one-shot rungs vs fresh-pair re-post vs netting prior exit closes in exit sizing, with quoted code evidence per option; implement code only if netting wins.
- Acceptance: (1) a repro test shows the refusal loop on a synthetic store; (2) the decision note names the winner with evidence and records the losers' reasons; (3, gated) if netting wins, refill exits the netted remainder with no oversell and the guard still refuses genuine divergence.
- Out of scope: the ladder decision function itself, rung lifecycle code, screener changes, threshold/gate changes (`max_pair_cost`, grace, windows, route order all frozen).

## Dependency graph
- T1 (repro) → T2 (decision) → CHECKPOINT 1 (netting-wins → T3; lifecycle-wins → close on note, #325 owns the code).
- No sub-issues: decision work plus at most one conditional fix; tracker mirroring resumes at the #325 build per the #49 precedent.

## Tasks

### T1 — [Research] Repro spike: the refusal loop on a synthetic store (S)
Same pair_id, exit, refill, exit again: assert the second exit refuses with the venue diverging by exactly the exited shares, and the leg strands while the guard holds (no oversell). Cover grace-0 and grace>0 (the issue's compounding note).
Depends on: none. Verify: the test demonstrates the refusal (RED that documents the trap); targeted suites green.

### T2 — [Research] Decision matrix + recorded verdict (S)
Evaluate one-shot vs fresh-pair vs netting against code evidence (live carry path, closes-schema scoping limits, ladder needs from #49/#324); record winner, evidence, and losers' reasons in the decision note.
Depends on: T1. Verify: read-through — every option has a quoted-evidence verdict.

CHECKPOINT 1: decision locked. Netting-wins → T3. Lifecycle-wins → #326 closes on the note; rung lifecycle code belongs to #325.

### T3 — [Backend/Logic] Net prior exit closes in exit sizing (S, gated)
Local to the exit path (`load_pair` untouched): size the exit off naked-minus-prior-exits for the pair's scope; `_check_positions` keeps guarding the netted belief. Tests: refill exits the remainder, no oversell, genuine divergence still refused, completion/merge accounting unchanged.
Depends on: CHECKPOINT 1 netting-wins. Verify: T1-style repro now exits netted shares; focused suites green.

CHECKPOINT 2: handoff to #325 (decision + any new primitive it should reuse).

## Improvement proposal (adopted by default)
Cover both grace regimes (0 and >0) in the repro and the decision matrix, not just the grace-0 case the finding was observed under.
Evidence, verbatim from the issue: "Grace-0 immediacy compounds it: with the shipped grace default (0.0) any one-sided fill exits next pass, so staggered opposite fills never arrive unless the market balances in one rotation."
Rejected: none. Scope expansion: none proposed.

## CHECKPOINT 1 OUTCOME (2026-09-30): NETTING WINS — as venue-capped sizing
Decision matrix evaluated against code (T1 repro + reads):
- (A) One-shot rungs: necessary ladder lifecycle, already proven clean in the
  #324 harness — but leaves the live carry-path trap (#206 replacement legs
  joining an exited pair) open. Adopted as #325 design input, not the fix.
- (B) Fresh-pair re-post: dodges the accounting instead of fixing it; fights
  the #206 carry semantic and the ladder's one-pair design. REJECTED.
- (C) Netting: WINS. Implemented as condition-scoped close attribution
  (`_prior_exit_shares` sums prior `single_buy_exit`/`naked_exit` closes on
  the pair's condition and side -- closes carry condition_id + method +
  side-via-price-columns, no pair_id), capped by the observed venue gap, so
  the exit sizes at min(fills-only naked, venue-agreed heavy shares)
  whenever a venue view is present. Over-attribution across pairs sharing a
  condition can only shrink the sale toward the venue view, never grow one
  past it (fail-closed direction). Absence still refuses; no-view dry runs
  unchanged; `load_pair` untouched; no schema change. `_check_positions`
  has exactly one caller (`exit_single_buy`), so the contract change is
  contained.
T3 builds the cap. Lifecycle-wins path not taken.

## CHECKPOINT 2 OUTCOME (2026-09-30): BUILT — handoff to #325

---
# Plan — Issue #325: build the ladder path (gated on shadow go)

Branch: i325/build-the-ladder-path-gated-on-shadow-go | Issue: #325
Size: Large (new strategy path, 5+ files) · Type: Code · Stack: Python + pytest
Execution order: risk-first. Gate is GO (BTC + ETH 5-min probe verdicts go,
shape 2 / exit_60, 200 markets each; posted on #323). T1/T2 are independent
seams and may build in either order; T3 needs both; T4 needs T3; T5 needs T4.
No sub-issues: the issue itself tracks the build (#49 precedent: no tracker
noise for gated work).

## CodeRabbit plan intake
No `coderabbitai` plan comment exists on #325 (zero comments) — nothing to
adopt, nothing to verify. Code-explorer persona not deployed: all seams
below were verified by direct reads during planning (file:line in Resolved).

## Resolved from code (no operator time needed)
- Quoting funnels through `_decide_quotes_from_mid` / `evaluate_market_quote`
  (`core_brain/quotes.py:137,704`); the shadow loop defaults to
  `decide_quotes` (`core_brain/quotes.py:474`, wired at
  `core_brain/shadow_run.py:523`). A separate gated `decide_ladder_quotes`
  keeps off-behavior byte-identical. Verifiable by the off-test.
- Shadow entry is injectable: `run_shadow(markets_fn=...)`
  (`core_brain/shadow_run.py:676-690`) and per-cycle `decide_fn`
  (`core_brain/shadow_run.py:435,523`). Ladder proof tests drive the real
  loop through these seams — no harness fork.
- `record_submit` carries one `pair_id` per market
  (`core_brain/shadow_exec.py:223`); `load_pair` groups by token
  (`core_brain/single_buy_saver.py:349`), so one-leg exits need no new
grouping — only the timer and the `ladder_exit` label.
- Close-method sets live in `core_brain/order_registry.py:2285` and
  `core_brain/kpi.py:723` (`SINGLE_BUY_EXIT_METHODS`); adding one value is
  mechanical. `fetch_live_market` (`core_brain/markets.py:139`) stays
  untouched; series discovery is a new function.
- Config is `MakerConfig` (`core_brain/config.py:28`) with `max_pair_cost =
  0.99` and `single_buy_grace_sec = 0.0`; ladder fields are additive with
  validation, off by default.
- Q1 answered by probe: 2 rungs. Q2 answered: timed exit 60s (starting
  hypothesis confirmed). Q3 open by design: order lifetime stays
  configurable.

## Type-design lens (encapsulation / invariants, from persona read)
- Off-identity is a type-level gate: one `ladder_mode: bool = False` field
  guards the whole path; the single-pair path is frozen behind it, so no
  ladder state can leak into today's quotes.
- The separate allocation is its own budget field, never a Dynamic Caps
  alias: per-rung sizing and the blended-residue check cannot resolve to
  shared caps by construction.
- One-shot rung lifecycle is the ladder-side invariant matching the #326
  fix: a filled rung retires, an exit close retires the market — ladder legs
  can never re-post under an exited pair, while #326 netting
  (`_prior_exit_shares`, `_unexplained_divergence`, venue-capped sizing)
  guards the live carry path. Both sides recorded; neither re-argued here.

## Spec (SPEC.md, #325 section)
- Goal: ladder at series OPEN (first ~30s of `start_ts`) on BTC/ETH 5+15-min
  series; equal-sized rungs around 0.50 (shape 2); opposite fills merge at
  < $1.00 under one `pair_id`; one-leg residue exits on the 60s timer;
  shadow-only; off = today's single price.
- Acceptance: (1) ≥2 submit events/side at distinct prices within 30s of open
  in cycle telemetry; (2) fills oldest-first under one `pair_id`;
  (3) one-leg exits inside the window, no orphans; (4) mode off = today's
  single price, suite green; (5) screener output unchanged.
- Out of scope: live execution, screener changes, cross-market portfolio
  coordination, shared-cap accounting, threshold/gate changes.

## Dependency graph
- T1 (discovery) + T2 (config) → T3 (decision fn + off-test) →
  CHECKPOINT 1 (off-identity proven) → T4 (telemetry + `ladder_exit` +
  timed one-leg exit) → T5 (shadow wiring + 4 proof tests) → CHECKPOINT 2.

## Tasks

### T1 — [Backend/Logic] Series discovery for BTC/ETH 5+15-min (S)
New function listing upcoming series markets (open-window gate inputs:
condition, tokens, `start_ts`); `fetch_live_market` untouched. Equal-size
rung inputs come from the probe shape (2).
Depends on: none. Verify: focused test with a stubbed venue response —
upcoming markets ordered by `start_ts`, non-series rows ignored.

### T2 — [Backend/Logic] Ladder config: mode + shapes + timers + budget (S)
Additive `MakerConfig` fields (`ladder_mode` off default, rung shape,
exit timer default 60s, separate budget) + validation (shape ≥ 2,
non-negative budget, timer > 0); no existing default changes.
Depends on: none. Verify: focused config test — off default, invalid
shape/budget rejected.

### T3 — [Backend/Logic] Gated ladder decision function + off-test (M)
`decide_ladder_quotes`: open-window gate, equal sizes, distinct-side
counting, per-rung submit intents under one `pair_id` via `record_submit`;
mode-off delegates to today's single-price path untouched.
Depends on: T1, T2. Verify: off-test (mode off = today's single price)
plus gate tests (outside window → no submits).

CHECKPOINT 1: off-identity proven (off-test green) — the single-pair path
is frozen behind the switch. One-line report, not a pause in auto mode.

CHECKPOINT 1 OUTCOME (2026-10-01): PROVEN — mode-off output equals
`decide_quotes` exactly (unit off-test); plus a decide-time completable
filter so rungs the planner would cancel are never posted (no churn).

CHECKPOINT 2 OUTCOME (2026-10-01): BUILT — real-loop rehearsal holds the
shape (2 rungs/side, one `ladder-<cond>` pair), fills oldest-first under it,
one-leg residue exits via `ladder_exit` with no orphan, mode off posts no
ladder stamp. Pair stamp uses a dash: a colon broke merge-tx versioning.

### T4 — [Backend/Logic] Per-rung telemetry + `ladder_exit` + timed exit (M)
Per-rung submit/fill telemetry; `ladder_exit` added to every naked-close
set (`order_registry.py`, `kpi.py`, reports); one-leg residue exits on the
ladder timer reusing `load_pair` token grouping, sized through the #326
netting (`_check_positions(pair, venue, registry)`); one-shot lifecycle
(filled rung retires, exit close retires market).
Depends on: T3. Verify: focused tests — close sets include the method;
residue exits inside the window with no orphan.

### T5 — [Backend/Logic] Shadow wiring + four proof tests (M)
Crypto series into the real shadow loop via `markets_fn`/`decide_fn`
seams (screener untouched); tests: placement (≥2 submits/side, distinct
prices), fill-sim (oldest-first, one `pair_id`), one-leg exit (inside
window, no orphan), off-test (from T3, re-run here). Each fails without
the change, passes with it.
Depends on: T4. Verify: the four tests + focused regression suites green.

CHECKPOINT 2: shadow rehearsal shows rungs resting, filling, and exiting
per spec. One-line report.

## Improvement proposal (adopted by default)
One-shot rungs as the ladder lifecycle: filled rungs retire and exit closes
retire the market, so ladder legs never re-post under an exited pair_id.
Evidence, verbatim from #326 CHECKPOINT 1: "(A) One-shot rungs: necessary
ladder lifecycle, already proven clean in the #324 harness" and from the
#324 plan: "filled rungs retire, exit closes retire the market". This is
the structural side of the #326 trap; the netting primitive guards the live
carry path. Rejected: none. Scope expansion: none proposed.
T3 green: refill exits the netted remainder (min fills-only naked, venue
heavy) with no oversell; genuine and partially-explained divergence still
refuse; route-pair coverage under both graces. Reuse for #325:
`_prior_exit_shares` + `_unexplained_divergence` + `_check_positions(pair,
venue, registry)` contract; `exit_single_buy` nets the post-cancel naked
before sizing. `load_pair` untouched, no schema change.
