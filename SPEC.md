# SPEC - #934 Show the running arm's parameters in the dashboard's telemetry card

## Goal

Repurpose the dashboard bento card at `dashboard/static/index.html:419-479` from
`EXECUTION TELEMETRY` (four performance tiles) into `RUNNING ARM`: the running
arm's human label, its one-line profile, and one `KEY = value` row per `env`
override from the arm definition — so the operator sees what the engine is
configured to do without scrolling to the parameters panel.

## Acceptance criteria

- [ ] The bento card at the telemetry slot is titled `RUNNING ARM`, shows the
  running arm's label and profile line, and lists each `env` override from the
  arm definition as `KEY = value`.
- [ ] An arm with no `env` overrides renders the shipped-baseline sentence
  instead of an empty list; a run with no `tournament_arm` renders the
  engine-recorded profile name and says no arm is recorded, without fabricating
  parameters.
- [ ] `⋯` opens the existing arm-detail modal for that arm, and
  `Full parameters` selects it in `#param-arm-select` and brings
  `#params-panel` into view.
- [ ] The arm definition is fetched once per change of running arm, not on
  every status poll, and a failed fetch leaves the card's previous content in
  place with a readable note instead of blanking it.
- [ ] The retired tiles leave no dangling lookups: no code writes to
  `broker-kpi-winrate`, `broker-kpi-pf`, `broker-kpi-spread`,
  `broker-kpi-expectancy`, `broker-kpi-wins`, `broker-kpi-sharpe` or
  `broker-kpi-pairs`.
- [ ] `python -m pytest -q tests/test_dashboard_server.py -k "milestone8_html or running_arm"`
  passes on a quiet tree.

## Edge cases

- `status` null/undefined (failed status fetch) ⇒ renderer returns without
  writing; previous card content stays.
- Empty-string `tournament_arm` / `name` ⇒ treated as missing.
- `active_profile` present, `running !== true` ⇒ tag `STOPPED` (shadow profile
  preferred even after its run stopped); no profile ⇒ tag `NO RUN`.
- New arm loading while old payload shown ⇒ keep old rows, note names the new
  arm; late response for a stale arm ⇒ ignored (sequence guard).
- `env` empty/missing ⇒ baseline sentence, not an empty list.
- Arm name not in the parameters catalog ⇒ card note says so, select
  unchanged, Strategy navigation still happens.
- `arm-detail` resolves names only: a same-named arm from another tournament
  may display (no backend change to fix this here).

## Out of scope

- Parameters panel logic/catalog, arm-detail modal layout, prototype
  (`prototype.js`/`prototype.css`), neon page, Reports tiles, `renderKPIs`,
  arm performance code, any backend route or schema change.
