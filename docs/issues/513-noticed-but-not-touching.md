# Noticed-but-not-touching — Issue #513 (Station III build)

One candidate, spotted while building the T3 dashboard readout. Not fixed here: it is a
pre-existing styling gap on the analytics tab, outside #513's scope.

| ID | Candidate | Discovering station | Evidence | Status | Resolution |
|----|-----------|---------------------|----------|--------|------------|
| N1 | The four KPI hint lines on the Performance & Analytics tab render with class `hint`, but no `.hint` rule exists anywhere in `dashboard/static/` — they inherit body styling instead of the muted secondary-text treatment the neighbouring `.kpi-tile` hints use. Purely cosmetic: the copy is correct and readable. | III | `dashboard/static/app.js:4606`, `:4611`, `:4616`, `:4621` (`class="hint"`); `grep -rn "hint *{" dashboard/static/` returns nothing and `index.html` has no inline `<style>` block | open | — |
