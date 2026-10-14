# Noticed but not touching — #558 (Station III discoveries)

| ID | Candidate | Discovering station | Evidence | Status | Resolved by |
|---|---|---|---|---|---|
| N1 | `kpi.py:2195` holds a third inline exit triple (`_single_exits`) that #520's site inventory missed. #559 absorbs it — same file, same pattern, zero new work. | III | core_brain/kpi.py:2195 | open | — |
| N2 | `tournament_cockpit_server.py:626` SQL selects with `NOT IN ('settlement', 'shadow_merge_note', '')` — beyond #560's parenthetical scope, but the file is in the adopted CodeRabbit plan's changed-scripts list, so #560's scope should include it. | III | scripts/tournament_cockpit_server.py:626 | open | — |
| N3 | `tournament_hub.py:101` nontrade set holds `shadow_merge_note` — not named in #560's issue text, but the adopted CodeRabbit plan lists the file as changed, so #560's scope should include it. | III | scripts/tournament_hub.py:101 | open | — |
| N4 | Scanner false positive: `git_workflow_guard.py:131` `rest[:2] == ["pr", "merge"]` is git command vocabulary, not close methods. Recommended fix: exclude `scripts/hooks/` from the scan roots (CI tooling, never a taxonomy consumer). Until decided, the guard cannot reach GREEN at #560 — the pipeline's end state depends on this one. | III | scripts/hooks/git_workflow_guard.py:131 | open | — |
