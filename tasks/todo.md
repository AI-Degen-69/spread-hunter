# Todo — Issue #333: Full 4-hour live-books ladder trial

- [x] T1 — [Backend/Logic] Parameterize report issue tag (S; RED→GREEN, committed)
- [x] CHECKPOINT 1 — 3 new/updated tag tests green offline (2-min live wiring check started but exceeded the 30s tool window; store removed, nothing committed)
- [x] T2 — [Research] Full ~4h live trial, fresh --db, JSON machine-local. **RUNNING IN BACKGROUND** (third launch, 2026-10-02 05:19:38, PID 25984, `--minutes 240`, `--db data/NN_shadow_ladder_333_mine.db`, `--out reports/ladder_live_books_333_mine.json`, `--issue-tag 333`). Completes ~09:19. Earlier launches (task-209, PID 28720) are dead and superseded.
  - [x] Live diagnostic at 20 min: 27 orders, 0 fills, mean rest 17.0s, mean queue_ahead 394 → projected ~320 placements / ~0-3 fills over 4h. Run left going per operator decision.
- [x] T3 — [Docs] **Rescoped to placement & censoring only.** Committed to `docs/runs/`. Must carry the explicit "fills ~0, fill/exit comparison NOT measured" statement. Probe baseline (75% pair rate, shape 2 / exit_60, +0.30 CI 0.26–0.35) is transcribed in `tasks/plan.md` and is **not comparable** to this run's placement data.
  - [x] Operator decision (2026-10-02 05:54): **no draft before the final numbers.** T3 is written in one pass at ~09:20, from the completed run only. Interim figures are not to be committed, and no `docs/runs/` file is to be created until the report JSON exists.
  - Watcher running: `logs/watch_333_trial.py` (PID 29320) polls every 60s and writes `logs/ladder_trial_333_verdict.txt` the moment the JSON appears or the process dies. Read that file at 09:20 before writing.
- [x] CHECKPOINT 2 — MD in git with placement + queue + cancel distributions and the conservation table, plus the not-measured disclosure
- [ ] #321 shadcn migration stays on HOLD — do not touch

