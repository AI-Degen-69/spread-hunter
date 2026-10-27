# SPEC.md — Issue #856: Display active strategy profile name in Dashboard top nav bar

Branch: `i856/feat-display-active-strategy-profile-name-in-dashb` | Issue: #856

## Goal

Answer one operator question at a glance: **which configuration is the active bot
running?** A `PROFILE` pill in the dashboard top navigation bar shows the active
preset/profile name (`balanced`, `queue-clear-5m`, `balanced+queue-clear-5m`,
`baseline`, or `default`), refreshed on every status poll from the backend API.

## Core rule (evidence over environment)

The dashboard never guesses the profile from its own process environment. The
tournament launcher strips `BOT_*` variables from dashboard processes and gives arm
settings only to the engines; the live trader loads a `.env` the dashboard never
fully loads; `MakerConfig` does not retain the selected preset name after loading
(`core_brain/config.py:311-313`). So **the engine records the profile it loaded,
and the dashboard reads that record**. A missing record renders as `unknown` —
the same "missing evidence stays missing" rule `renderCodeVersion` already follows
(`dashboard/static/app.js:2087`).

## Acceptance criteria

- [ ] The top navigation bar shows a dedicated `PROFILE` pill (label/value spans, no
      colon — the same pattern as `DB LIVE`), placed inside `.top-group-source`
      directly after `#db-mode-badge`.
- [ ] The pill value comes from `GET /api/system/status` → top-level
      `active_profile` (`{name, preset, queue_clear_on, queue_max_clear_min,
      source, running, tournament_arm?}`), refreshed on every poll.
- [ ] Shadow runs: the engine writes its profile into its heartbeat
      (`runtime/shadow_run_<run_id>.json` → `profile`); the badge shows it with
      `source: "shadow"`. A heartbeat written before the field existed shows
      `unknown`, never a stale or invented name.
- [ ] Live trader: `write_trader_profile` records `runtime/trader_profile.json`;
      with no shadow profile for the selected store, the badge falls back to it
      (`source: "live"`). Shadow takes priority when both exist.
- [ ] Name grammar: preset name when selected, plus `queue-clear-<N>m` when the
      queue gate is on (minutes without trailing zeros: `5.0` → `5m`, `2.5` →
      `2.5m`), joined with `+`; `default` when neither applies. `None` means "no
      named preset selected" — never rendered as `baseline` (a real preset).
- [ ] `python -m pytest -q tests/test_dashboard_server.py` passes unchanged in its
      existing assertions (`test_system_status_endpoint`, the database identity
      test).

## Edge cases

- Unknown `BOT_QUOTE_TOURNAMENT_PRESET` makes `resolve_tournament_preset_name()`
  raise `ValueError` (`core_brain/config.py:620-637`): profile construction must
  fail soft (omit the field), never crash a rehearsal or the status endpoint.
- Malformed heartbeat `profile` (list, numeric `name`): treated as absent.
- Heartbeat for a different store than the selected one: ignored (the existing
  `match_db` rule in `_read_shadow_heartbeat_file`, `dashboard/server.py:965`).
- Stale/unsigned live file: rejected unless the recorded pid is alive, matches
  the `decide` registry entry, and names the active store.
- Dashboard's own env carries `BOT_QUOTE_TOURNAMENT_PRESET` (or a typo of one):
  ignored — the badge must still show `unknown`/the engine record, and the
  endpoint must still return 200.

## Out of scope

- Editing strategy parameters from the UI (`get_parameters()` at
  `dashboard/server.py:4212` and the Strategy tab stay read-only).
- Launcher/cockpit behavior (`scripts/shadow_tournament.py`,
  `scripts/resume_tournament_arms.py`, `scripts/tournament_cockpit_server.py`,
  `scripts/tournament_dashboard_hub.py`), arm definitions
  (`configs/tournaments/*.json`), queue-gate behavior (`core_brain/risk.py`),
  dotenv lookup (`core_brain/env_file.py`), `MakerConfig` fields, and
  `dashboard/static/styles.css` (changed only if
  `tests/test_dashboard_narrow_viewport.py` fails).
