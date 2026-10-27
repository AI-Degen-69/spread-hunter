# CONSTRAINTS.md — Issue #856: Active strategy profile badge

## Scope & blast radius

- **Display-only.** The deliverable is a badge plus the engine records behind it.
  No quoting, sizing, market-selection, or risk-formula change. No new external
  dependency. No database schema change.
- **Writable seams (only these):** `core_brain/config.py` (one pure helper, no
  `MakerConfig` field change), `core_brain/shadow_run.py` (heartbeat `profile`
  field + startup computation), `core_brain/trader_loop.py` (one writer
  function + one call), `core_brain/runtime_paths.py` (one filename constant, no
  `LEGACY_FILE_NAMES` entry), `dashboard/server.py` (shadow/live readers +
  `active_profile` in `get_system_status()`), `dashboard/static/index.html` (one
  pill), `dashboard/static/app.js` (one renderer + call), plus tests.
- **Frozen:** `scripts/shadow_tournament.py`, `scripts/resume_tournament_arms.py`,
  `scripts/tournament_cockpit_server.py`, `scripts/tournament_dashboard_hub.py`,
  `configs/tournaments/*.json`, `core_brain/risk.py`, `core_brain/env_file.py`,
  `get_parameters()` and the Strategy tab, `MakerConfig` fields,
  `dashboard/static/styles.css` (only if the narrow-viewport test fails),
  `.gitignore` (no rule to hide runtime files).
- **Zero live capital.** No signer, no `quote`, no `complete`, no Trader loop
  start, no dashboard START. `data/orders.db` is read-only as ever — never
  rewritten or deleted.

## Data truthfulness

- **Engine-record rule:** the badge shows only what an engine recorded about
  itself. The dashboard never calls `load_cfg` or
  `resolve_tournament_preset_name()` in its own process.
- **Missing evidence stays missing:** no record → `active_profile: null` →
  pill shows `unknown` in `state-unknown`. Never fall back to the dashboard env,
  never render `baseline` for "no preset".
- **Fail soft:** a `ValueError`/`OSError` in profile construction, heartbeat
  parsing, or the live-file reader omits the field; it never crashes a
  rehearsal, the trader, or the endpoint.

## Verification bar

- Every new behavior ships with a test that fails without it:
  `tests/test_active_profile.py` (new), plus extensions to
  `tests/test_shadow_run_stopwatch.py`, `tests/test_dashboard_server.py`,
  `tests/test_ui_top_nav_consistency.py`, `tests/test_ui_top_nav_groups.py`,
  and the `profile` scenario in `tests/js/live_state_harness.cjs`.
- Targeted suites green after every change (agent-run, never operator-run):
  `tests/test_active_profile.py`, `tests/test_shadow_run_stopwatch.py`,
  `tests/test_per_run_shadow_heartbeat.py`, `tests/test_dashboard_server.py`,
  `tests/test_ui_top_nav_consistency.py`, `tests/test_ui_top_nav_groups.py`,
  `tests/test_live_state_language.py`, `tests/test_dashboard_narrow_viewport.py`.
  Existing assertions (`test_system_status_endpoint`, the database identity
  test, the three-group nav check) must pass untouched.
- **"How to verify" is hands-on only** — the operator opens the dashboard,
  hovers the badge, switches stores, reads a heartbeat file; it never asks the
  operator to run pytest, git, or gh.

## Anti-cheat

- No skipped or deleted tests, no removed assertions, no `xfail`/`skip`
  markers, no lint or type suppressions added to reach green.
- No gate threshold, cap, or arm definition edited to make a check pass.
- `styles.css` changes require a failing narrow-viewport test as the reason.
