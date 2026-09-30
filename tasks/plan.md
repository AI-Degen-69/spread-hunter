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
