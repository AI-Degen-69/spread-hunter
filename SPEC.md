# SPEC: Issue #314 - D12 submarket admission trial (family-grouped arms, pre-registered rule)

## Goal
Turn submarket admission from an argument into a number: a reversible two-arm
shadow trial where control uses the shipped `identity_allowed` decision and
treatment admits at most one market per event (mainline preferred, one eligible
submarket fallback). A read-only analyzer returns adopt / reject / inconclusive
from four pre-registered bars only. The shipped default path stays byte-identical.
This ticket builds and rehearsal-tests the machinery; it does not launch the
100-hour trial — launch is an operator decision.

## Acceptance criteria (from issue)
- One ranker pass produces both arms in one atomic bundle (`spread_hunter.paired-admission.v1`, one `snapshot_id`); unflagged `markets.json` bytes unchanged.
- Treatment holds at most one market per event; fallback only when no mainline member is eligible, with `fallback_reason`; blocked-keyword, resolved and unreadable rows stay refused.
- Two shadow loops each read one arm; family attribution persists admission → order → completion; a treatment-only guard keeps one fallback holding per event.
- Analyzer verdict from the four h3 bars only; `inconclusive` on any limitation (incl. <30 clusters with usable h3, any due fill without h3 excess markout); analyzer is read-only (store hashes unchanged).
- Pre-registration doc fixes the rule, 100-hour endpoint, no-peeking, arm setup before launch.

## Scope
### In scope
- New `scoring/family_admission.py` (pure identity classification); trial-only `evaluate` mode + `build_paired_admission_bundle` + `--paired-admission` CLI in `scripts/filter_markets.py`; forwarding in `scripts/filter_loop.py`.
- Format branch in `core_brain/market_feed.py`; `--paired-admission-arm` in `core_brain/shadow_run.py`; additive shadow-only columns + attribution + fallback guard in `core_brain/paired_shadow.py`.
- Admission axis in `scripts/paired_depth_report.py` (depth path unchanged).
- `docs/runs/2026-09-30-paired-admission-experiment.md` pre-registration doc; short rehearsal only.
### Out of scope
- Launching the 100-hour trial; changing the shipped gate on any verdict (even `adopt` — adoption is a separate decision); touching `scoring/selector.py`, `single_buy_saver.py`, risk caps, `market_resolution.py`, `order_registry` schema, `data/**`.

# SPEC: Issue #325 — Build the ladder path (gated on shadow go)

## Goal
Final build of #49: a separate, gated ladder path quoting BTC/ETH 5+15-min
series at OPEN with equal-sized rungs around 0.50 (probe-winning shape 2,
timed exit 60s), merging opposite fills under one `pair_id` per market, and
exiting one-leg residue on a timer. Shadow-only; off means byte-identical
quoting to today.

## Acceptance criteria (from issue)
- `ladder_mode` config off by default; series discovery leaves
  `fetch_live_market` untouched; gated ladder decision function; per-rung
  telemetry; `ladder_exit` close method in all naked-close sets.
- Four proof tests fail-without/pass-with: placement (≥2 submits/side,
  distinct prices), fill-sim (oldest-first, one `pair_id`), one-leg exit
  (inside window, no orphan), off-test (single price, suite green).
- Shadow rehearsal confirms spec behavior (rungs resting, filling, exiting).
- Screener untouched; separate ladder allocation (operator direction).

## Scope
### In scope
- `MakerConfig` ladder fields (mode, shapes, timers, separate budget) +
  validation; series discovery fn; `decide_ladder_quotes` gated fn;
  `ladder_exit` in `order_registry.py`, `kpi.py` (+ sets), reports;
  shadow wiring via `decide_fn`/`markets_fn` seams; one-shot rung lifecycle
  (filled rungs retire, exit closes retire the market); #326 netting reused
  in exit sizing.
### Out of scope
- Live execution; screener changes (`scripts/filter_markets.py`,
  `runtime/markets.json`); cross-market portfolio coordination; shared-cap
  accounting; threshold/gate changes (`max_pair_cost`, grace defaults,
  windows, route order frozen).
