# Noticed but not touching — #559 (Station III discoveries)

| ID | Candidate | Discovering station | Evidence | Status | Resolved by |
|---|---|---|---|---|---|
| N1 | `KPI_PAYLOAD_VERSION` is not bumped although `pnl_by_fill_path` now ships two new keys (`settled`, `other`). The backend docstring treats the hand-bumped envelope as how a stale backend is told apart from a genuinely unmeasured field; the consumer of those keys is the fill-path card, which #560/T3 owns, so the bump belongs with that card work. | III | core_brain/kpi.py:58 (version), core_brain/kpi.py:919 (`pnl_by_fill_path`) | open | — |
| N2 | `scripts/rescue_exit_report.py:474` compares `r["method"] == "shadow_settlement"` with a bare string literal while the module imports the canonical names; the taxonomy scanner deliberately flags only literal *lists*, so this comparison can drift unnoticed. Same file, line 440, is the aged-out count. | III | scripts/rescue_exit_report.py:440,474 | open | — |
