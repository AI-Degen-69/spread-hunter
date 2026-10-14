# Noticed-but-not-touching — Issue #517 (Station III)

Issue #517 adds the capital-level readings (`pnl_skewness`, `sortino_ratio`,
`drawdown_recovery`), the always-present `capital_risk` payload section, and the
`#capital-risk-readout` card on the Reports & Analytics page. It changes no curve writer, no
close recorder and no fill-model logic, so the candidate below was recorded rather than fixed
here.

| ID | Candidate | Discovering station | Evidence | Status | Resolution |
|----|-----------|---------------------|----------|--------|------------|
| N1 | **A page can end up with every analytics readout blank while the KPI tiles beside them are painted.** Reproduced once during this station's own live check: a fresh load painted all five readouts (the new card included), then a browser reload followed by a rail-page click left `#fill-path-edge-readout`, `#rescue-friction-readout`, `#queue-realism-readout`, `#robustness-readout` **and** `#capital-risk-readout` at their 23-byte placeholder while `#kpi-grid` held 1257 bytes of tiles and `/api/kpi` kept answering 200 with the full payload. Calling `renderAnalyticsSurface(lastKpi, lastStatus)` by hand painted the card correctly (3233 bytes, 2 chips), and a *fresh tab* load painted it with no manual call — so the renderer is fine and the loss is in the paint schedule, not in any one card. Three of the five affected renderers were untouched by #517. | III | `dashboard/static/app.js:5264-5269` — the `deferPaint` callback inside `renderKPIs` is dropped when `paintGeneration !== analyticsPaintGeneration` or when `tabVisible(document.getElementById('tab-2'))` reads false at frame time; `dashboard/static/app.js:588-627` (`renderCachedSections`) repaints the tiles but does **not** call `renderAnalyticsSurface` from the cached snapshot, so a dropped frame has nothing to restore it until the next KPI render takes the same path. | published | [#540](https://github.com/AI-Degen-69/spread-hunter/issues/540) — operator-approved for publication at Station VI, with the direction stated (it hides a correct reading, losses included, rather than printing a wrong one) and the nearest closed siblings (#264, #266, #268) cited. |
