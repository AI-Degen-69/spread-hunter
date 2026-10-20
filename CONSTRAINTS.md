Branch: i687/ranker-tournament-preset | Issue: #687

# Constraints - #687 ranker tournament preset (N3 from #674)

Preset toggle covers the ranker; engine/scoring defaults stay independent.

## The bar

- **Zero regressions:** `tests/test_dynamic_offset.py`,
  `tests/test_config_divergence_guard.py`, `tests/test_config_single_source.py`
  and `tests/test_ranker_reads_env_file.py` pass unchanged in intent; new
  behavior (preset-on scoring, UI pill) ships with tests that fail without
  the change.
- **Engine behavior frozen:** `MakerConfig.from_env()` precedence
  (defaults → preset → explicit) and the unknown-preset `ValueError` text
  stay byte-identical in behavior; the refactor only shares the resolver.
- **Scoring class body untouched:** no new declaration in
  `scoring.config.MakerConfig` — the divergence guard owns this. Registry +
  resolver live in `core_brain/config.py`.
- **Defaults independence:** preset-off runs produce today's numbers;
  no `PICKER_PROFILE` field (especially the 0.045 `max_spread_from_mid`
  window) appears in any `SCREENER_PRESETS` entry — pinned by a registry test.
- **No invented numbers:** `baseline` (and all other presets) start on shipped
  screener bars; preset-specific bar values arrive only as an explicit
  operator decision, never guessed.
- **Dashboard honesty:** the pill renders what the ranker published in
  `pipeline.json`, never the dashboard process's own environment.

## Deliberate non-changes (a later reviewer must not "fix" these back)

- Tournament shared filter stays preset-free by design (one filter serves
  several arms with different presets).
- `data/orders.db` is read-only; no live/opening commands during this work.
- No `docs/issues/674*` ledger exists (closed-issue artifacts pruned) — N3
  closeout is recorded in the plan, not chased as a missing file.

## Process

- No live command, no venue call, no write to `data/orders.db`.
- No direct push to `main`; the change ships through a PR.
- Targeted suites locally only; GitHub CI on Ubuntu and Windows is the merge gate.
- No `pytest` / `gh` / `git` step inside the operator's "How to verify".
