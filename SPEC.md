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
