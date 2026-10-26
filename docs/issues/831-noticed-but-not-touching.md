# Noticed but not touching — Issue #831

Out-of-scope findings from the Closed Trades build. Each row is a candidate the operator
decides on; nothing here was changed.

| # | Candidate | Found in | Evidence | Status | Resolution |
| --- | --- | --- | --- | --- | --- |
| N1 | Dead Stitch CSS in `dashboard/static/prototype.css`: `.stitch-badge-neutral`, `.stitch-dual-rung-badge`, `.stitch-leg-sub`, `.stitch-leg-role`, `.stitch-status-badge.stop-loss/.grace-expired`, `.stitch-status-dot.stop-loss/.grace-expired` have no string in `dashboard/static/*.js` that emits them. | Station III (removing the #831 strip CSS made the pattern obvious) | `grep -rn "stitch-badge-neutral\|stitch-dual-rung-badge\|stitch-leg-sub\|stitch-leg-role" dashboard/static/*.js` → no matches; rules live at `dashboard/static/prototype.css:1741-1827` | open | — |
