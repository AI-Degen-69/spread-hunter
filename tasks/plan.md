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
`scripts/` harness replays recorded BTC/ETH series tapes, simulates 2/4/6-level shapes × exit policies (timed exits + hold-to-close baseline), and reports fill-rate CIs + mean-PnL CIs. One-leg residues scored at real resolution outcomes, never zero. Answers rung count, exit window, order lifetime — or reports "unmeasurable".
Depends on: T1. Verify: probe runs on a fixture tape and prints both terms of the verdict formula (P(pair)×edge − P(one-leg)×loss).

CHECKPOINT 1: probe numbers in hand → go (build T3/T4) or no-go (stop, record why). One-line report, not a full pause in auto mode.

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
