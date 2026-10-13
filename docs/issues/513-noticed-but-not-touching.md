# Noticed-but-not-touching — Issue #513 (Stations III–IV)

Two candidates, both pre-existing and outside #513's scope. Neither was fixed here: one is a
styling gap on the analytics tab, the other is a defect in adjacent chart code that this diff
does not touch. Both are the operator's call to promote into their own Issue.

| ID | Candidate | Discovering station | Evidence | Status | Resolution |
|----|-----------|---------------------|----------|--------|------------|
| N1 | The four KPI hint lines on the Performance & Analytics tab render with class `hint`, but no `.hint` rule exists anywhere in `dashboard/static/` — they inherit body styling instead of the muted secondary-text treatment the neighbouring `.kpi-tile` hints use. Purely cosmetic: the copy is correct and readable. | III | `dashboard/static/app.js:4606`, `:4611`, `:4616`, `:4621` (`class="hint"`); `grep -rn "hint *{" dashboard/static/` returns nothing and `index.html` has no inline `<style>` block | open | — |
| N2 | `renderPairCostKdeChart` divides by a nullable `stdev` with no guard, so a run whose pair-cost standard deviation is unmeasured emits `NaN` into the SVG path. The chart draws a broken curve and the browser logs 12 `<path> attribute d: Expected number` errors per page load — console noise that hides real errors during any browser check. One-line fix: skip the KDE curve when `stdev` is missing or 0. | IV | Gate console: `<path> attribute d: Expected number, "…4,190.0 L 248.0,NaN L 258.6,190.…"` ×12, at `app.js?v=…:3564`. Source: `dashboard/static/app.js:3488` (`renderPairCostKdeChart`), `:3500` (`const stdev = pc.stdev != null ? pc.stdev : null`), `:3558` (`(xVal - mean) / stdev`), `:3560`, `:3563`. Not this diff's: `git diff origin/main...HEAD -- dashboard/static/app.js` has exactly three hunks (`~4503`, `~4627`, `~8717`), so this function is byte-identical to `main`. | open | — |
