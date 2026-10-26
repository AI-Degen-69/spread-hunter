# Backlog — Issue #831 (Closed-trades outcome shows wins and losses with exec-type pills)

Out-of-scope candidates noticed during the build. Station III records them; only
Station VI resolves a row. An `open` row is never deleted.

| # | Candidate | Found in | Evidence | Status | Resolution |
|---|---|---|---|---|---|
| N1 | Dead Stitch CSS in `dashboard/static/prototype.css`: `.stitch-badge-neutral`, `.stitch-dual-rung-badge`, `.stitch-leg-sub`, `.stitch-leg-role`, `.stitch-status-badge.stop-loss/.grace-expired`, `.stitch-status-dot.stop-loss/.grace-expired` have no string in `dashboard/static/*.js` that emits them. | Station III (removing the #831 strip CSS made the pattern obvious) | `grep -rn "stitch-badge-neutral\|stitch-dual-rung-badge\|stitch-leg-sub\|stitch-leg-role" dashboard/static/*.js` → no matches; rules live in `dashboard/static/prototype.css` | dismissed | Removed in the #831 closeout — the eight rules were deleted from `dashboard/static/prototype.css` (`refactor: remove dead code from #831`), each one proven emitter-free by a repo-wide search (code, tests, docs). Re-open with its own ticket if a future Stitch surface actually needs those tones. |
