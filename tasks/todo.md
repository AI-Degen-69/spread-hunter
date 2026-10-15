# TODO — #608 10-profile shadow tournament (shipped defaults, one knob per character)

Branch: i608/10-profile-shadow-tournament | Plan: tasks/plan.md

- [ ] T1 [S] Write `configs/tournaments/i608_tournament-1_arms.json` (10 characters, one-knob env deltas)
- [ ] T2 [M] Cockpit refresh: `TOURNAMENTS["608"]` + `ARMS_CONFIG_608`, KPI column swap, aggregate strip, guard stale forensics
- [ ] T3 [S] Hub refresh: plan-order sort (fix `ARMS_ORDER`), worst-catalyst + next-run tweak per card
- [ ] T4 [XS] Dry-run gate: acceptance command prints valid 10-arm plan, exits 0
- [ ] T5 [M] Tests: `tests/test_shadow_tournament.py` — 10 arms, unique names, ports, run-number slugs, one-knob-per-arm
