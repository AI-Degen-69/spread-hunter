# Issue #314 — D12 submarket admission trial checklist

- [x] **T1** Ranker admission axis + atomic trial bundle — DONE 2026-09-30 (16 new tests + loop tests green, regressions green)
- [x] **T2** Shadow arm consumption + attribution + fallback guard — DONE 2026-09-30 (feed/attribution/guard tests green, depth regressions green)
- [x] **T3** Read-only admission analyzer + four-bar verdict — DONE 2026-09-30 (12 verdict tests green, depth fixtures green)
- [ ] **T4** Pre-registration doc (`docs/runs/2026-09-30-paired-admission-experiment.md`) + short rehearsal, no launch

Prohibited: `scoring/selector.py`, `single_buy_saver.py`, risk caps, `market_resolution.py`, `order_registry` schema, `data/**`. No trial launch in this pipeline.
