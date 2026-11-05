# CONSTRAINTS - #934

Branch: i934/show-the-running-arm-s-parameters-in-the-dash | Issue: #934

## Zero regressions

- Targeted suites green during development and after review fixes:
  `tests/test_dashboard_server.py -k "milestone8_html or running_arm"`,
  `tests/test_portfolio_card_basis.py`,
  `tests/test_negative_values_read_as_losses.py`.
- `tests/test_live_state_language.py` and `tests/js/live_state_harness.cjs`
  stay untouched and green (shared harness, not this card's).
- Full `python -m pytest -q` is the GitHub CI merge gate (Ubuntu + Windows),
  not a local gate. The repo does not repeat it locally.
- Every changed behaviour needs a test that fails without the change.
- Run checks on a quiet tree only: a live tournament writing `data/` or
  `runtime/` trips `tests/conftest.py`'s `block_real_state_writes` guard —
  environmental, not a failure; never "fix" the guard. Do not press START.

## Hard boundaries

- No backend change: `dashboard/server.py`, `dashboard/arm_params.py`,
  `_resolve_active_profile`, `_read_trader_profile`, `/api/profile/arm-detail`,
  `/api/parameters`, `/api/parameter-arms`, heartbeat schema — read-only.
- Parameters panel code untouched: `loadParamArms`, `renderParameters`,
  `paramFetchSeq`, `selectedParamArm`, the `#param-arm-select` listener.
- Arm-detail modal untouched: `openArmDetailModal`, `renderArmDetailHtml`.
  Reuse them; do not restyle the global `.arm-detail-env` rules.
- `prototype.js`, `prototype.css`, `command-palette.js`, `neon-index.html`,
  `neon-app.js` untouched. Liquidity + equity cards and `#broker-kpi-strip`
  untouched, including realized-P&L and liquidity writes.
- One fetch per arm change (name-keyed cache + card-only sequence guard);
  never fetch on every poll. No new endpoint, no status-payload fields.
- Exactly one new CSS rule, scoped to `#running-arm-env code`. No global
  style changes.

## Anti-cheat

- No skipping/deleting/weakening tests or assertions; no lint suppression to
  get green. Retired-tile checks are removed only where the tile is gone, and
  every removed check is named in the plan.
- NULL stays visible: unknown values render as a readable note, never a
  fabricated number or arm.

## Dependencies

- No new external dependencies. No schema change to `data/orders.db`.
