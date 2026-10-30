# Backlog — Issue #873 (Cockpit renders with zero arms)

Out-of-scope presentation debt noticed while building the guard. #873 asked for the
crash fix plus an empty state, not a layout pass; #867 owns dashboard layout.
`renderCharts()` now returns early with a banner on the Overview tab, so none of
these are broken behaviour -- they are places that still look unfinished.

| # | Candidate | Found in | Evidence | Owner | When | Status | Resolution |
|---|---|---|---|---|---|---|---|
| N1 | The empty state exists only on TAB 1 (Overview & Leaderboard), where `#arms-empty-state` sits above the table. TABS 2–5 (PnL curves, Risk & Volatility, Execution & Merges, Monte Carlo) render their empty `.chart-box` frames with no message when arms are zero — the charts are skipped rather than labelled, so a zero-arms operator who clicks into those tabs sees bare boxes and no explanation. | Station III (reviewing the guard's blast radius) | `scripts/tournament_cockpit_server.py` — `#arms-empty-state` is emitted once, before the TAB 1 `<div class="table-box">`; the other tabs' canvases (`pnlLineChart`, `evBarChart`, `pfBarChart`, `mergeStackedBarChart`, `mcBarChart`) get no sibling notice | — | — | duplicate | Duplicate of #867 (open dashboard-layout ticket, which owns this scope). |
| N2 | The leaderboard keeps its full 16-column `<thead>` with zero rows beneath the banner, so the empty state reads as "banner above an empty grid" rather than replacing the table. Hiding the table (or spanning a single placeholder row across it) needs a CSS/markup decision that belongs with #867. | Station III (same pass) | `scripts/tournament_cockpit_server.py` — `renderAll()` still runs `tbody.innerHTML = ''` then `state.arms.forEach(...)`, leaving the header intact; the guard does not touch `.table-box` | — | — | duplicate | Duplicate of #867 (open dashboard-layout ticket, which owns this scope). |
