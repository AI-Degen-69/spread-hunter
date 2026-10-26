# CONSTRAINTS.md — Issue #839: Live migration approval audit for the #799 champion arm

## Scope & blast radius

- **Docs-first.** The deliverable is `docs/audits/live-migration-799.md`. Code changes are
  limited to one read-only measurement CLI (`scripts/reevaluate_tournament_run.py`) and its
  test.
- **No change** to `scripts/tournament_cockpit_server.py`, `configs/tournaments/*_arms.json`,
  `core_brain/config.py`, `core_brain/order_manager.py`, or any risk formula or cap.
  `tests/test_tournament_cockpit_arms.py` pins nine champion env values across three places;
  it must pass untouched.
- **Read-only on every store.** Arm databases open with `mode=ro`. `data/orders.db` (the
  production registry) is never opened, rewritten, or deleted; `/data/tournaments/` stays
  untracked.
- **Zero live capital.** No signer, no `quote`, no `complete`, no Trader loop, no dashboard
  START. No command that could reach the venue.

## Data truthfulness

- **Every figure is traceable to a command run in this session.** The audit records the
  command beside each table. A number with no command behind it does not ship.
- **No fabricated states.** A missing store, a missing `σ`, or an uncomputable N reports as
  "not computable" / `NO_DATA`, never as a zero or an invented value.
- **The verdict comes from the repo's gate**, not from a bespoke rule:
  `write_statistics_report` / `resolve_verdict` with
  `bot_eval_stat_gate_threshold_pct = 1.0` and `bot_eval_stat_gate_target_closes = 60`.
- **Provenance is labelled**: shadow/modelled fills are named as such; attributed figures are
  never presented as locally measured.
- **Coverage is reported honestly** (1308.5 measured minutes of a 1440-minute plan), and the
  audit states that a gitignored run folder is not re-derivable in CI.

## Verification bar

- New behaviour ships with a test that fails without it:
  `tests/test_reevaluate_tournament_run.py`.
- Targeted suites green after every change:
  `python -m pytest -q tests/test_reevaluate_tournament_run.py tests/test_tournament_cockpit_arms.py tests/test_tournament_cockpit_funnel.py`.
- **"How to verify" is hands-on only** — the operator opens the run table, the cockpit, or the
  report; it never asks the operator to run the suite.

## Anti-cheat

- No skipped or deleted tests, no removed assertions, no `xfail`/`skip` markers, no lint or
  type suppressions added to reach green.
- No gate threshold edited to make a verdict pass; no arm dropped from the table to improve
  the ranking.
- Nothing in this work may be described as "production approved".
