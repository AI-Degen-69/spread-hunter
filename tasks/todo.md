# Todo — Issue #837 (Station III checklist)

Branch: `i837/populate-worst-catalyst-tweak-columns` | Plan: `tasks/plan.md`

- [x] **T1 (S, Backend/Logic)** `_note_text` helper + suffix-aware entry lookup + config-note
      precedence in `build_plan_arms`; synthetic test covering full/blank/null/whitespace/
      padded/non-string/thesis-only/round-suffix. Depends on: —
- [x] **T2 (M, Design/UI)** `escHtml` + `annotationCellHtml` used by every render;
      `<` → `\u003c` in the embedded state; new `tests/js/cockpit_annotation_harness.cjs`
      + Python assertions (order, exactly two `N/A`, escaping, `</script>` guard, empty
      arms). Depends on: —
- [x] **T3 (M, UX/Copy)** four notes on all 10 #799 arms with corrected units; update the
      two #799 assertions; real-config + launcher-equivalence test; newest-run description
      copy. Depends on: T1
- [x] Focused check green: `python -m pytest -q tests/test_tournament_cockpit_arms.py`
- [x] Regression neighbours green: `tests/test_tournament_cockpit_funnel.py
      tests/test_shadow_tournament.py tests/test_tournament_run_number_naming.py`
- [ ] `git diff --stat` touches only: `configs/tournaments/i799-tournament-test_arms.json`,
      `scripts/tournament_cockpit_server.py`, `tests/test_tournament_cockpit_arms.py`,
      `tests/js/cockpit_annotation_harness.cjs` (plus planning docs). No `core_brain/`,
      no `reports/` (gitignored), no `data/`.
- [ ] Operator "How to verify" block carried into the PR body (tasks/plan.md §8)
- [ ] Backlog rows reviewed before closeout: `docs/backlog/i837-cockpit-catalyst-columns.md`
