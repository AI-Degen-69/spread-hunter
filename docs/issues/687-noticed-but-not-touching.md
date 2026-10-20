# 687 — noticed but not touching (Station III, 2026-10-20)

#687 wires the tournament preset toggle through the ranker: shared selector
resolver, `SCREENER_PRESETS` registry, scoring-loader precedence, preset
publishing (`pipeline.json` -> KPI funnel), and the screener-board pill.
Anything below was seen during the work and deliberately left alone.

## Noticed but not touching

| # | Noticed | Why out of scope | Owner | When | Status |
|---|---|---|---|---|---|
| N1 | Preset-specific screener bar numbers do not exist anywhere: no env wiring, no run record (`docs/runs/2026-10-14-loose-baseline-i569.md` carries no screener bars). All six `SCREENER_PRESETS` entries therefore ship on the shipped bars (`filter_live_activity_min_ratio=0.0`, `filter_std_vol_min_24h_usd=125_000.0`). | Picking looser/tighter bars per preset is a product decision, not a refactor. Inventing a conservative→aggressive scale would guess numbers the strategy has never measured. | you | when the operator decides the per-preset bars | open |
| N2 | No `docs/issues/674*` ledger exists in the tree (closed-issue artifacts were pruned), so #674 follow-up item N3 has no ledger cell to close here. | There is nothing to edit; the closed-issue sweep removed the ledger outright, so there is no missing file to chase. | you | — | resolved: not applicable — the e2f648e sweep pruned every closed issue's ledger, so there is no cell to edit and none should be resurrected. |
