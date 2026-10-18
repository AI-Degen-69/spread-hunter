# Todo — #492: ten-arm rerun on fixed code

- [x] T1 Restore the ten arm definitions — `configs/tournaments/i492_tournament-1_arms.json`
- [x] T2 Per-issue arms-file lookup + `arms_source` — `scripts/shadow_tournament.py`
- [x] T3 Refuse an unknown `HUNTER_TOURNAMENT_PRESET` at plan time
- [x] T4 Tests: exact env per arm, no retired knobs, no #487 tweaks, per-arm load, plan shape
- [x] T5 Operator run sheet — `docs/runs/2026-10-18-492-tournament-rerun.md`
- [x] T6 Anomaly log — `docs/issues/492-noticed-but-not-touching.md`
- [ ] Operator: launch the 12-hour run with `--dashboards`
- [ ] Comparison report: rerun vs first run, per arm, with the stop/grace caveats stated
