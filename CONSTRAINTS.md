# CONSTRAINTS — #517 (locked by Station II, enforced through Station V)

Governs the `i517/add-capital-risk-analytics-for-expectancy-drawdown` branch only. The #516
contract this file replaced is branch-scoped by its own header line and stays readable in git
history, as #514's and #515's do.

## Scope & File Boundaries

- Target files for modification:
  - `core_brain/statistical_analytics.py` — the new capital-risk section (#517): the settled threshold
    constants (`SKEWNESS_AVERAGE_AT_OR_ABOVE`, `SKEWNESS_BAND_WORDS`, `SORTINO_GOOD_AT_OR_ABOVE`,
    `SORTINO_AVERAGE_AT_OR_ABOVE`, `SORTINO_BAND_WORDS`, `RISK_FREE_PCT_PER_CLOSE`), the new pure
    functions `skewness_band`, `pnl_skewness`, `sortino_band`, `sortino_ratio`, `sortino`,
    `drawdown_episodes`, `drawdown_recovery` and `capital_risk`, plus the module docstring's fifth
    section paragraph. **`build()` gains exactly one additive keyword argument (`equity_curve`) and
    exactly one additive key (`capital_risk`); its positional parameters, every existing key and every
    existing value are unchanged.**
  - `core_brain/kpi.py` — exactly three edits: `KPI_PAYLOAD_VERSION` 256 → 257, the venue account-value
    curve hoisted into a local `account_series` name, and that name passed to
    `build_statistical_analytics(...)` as `equity_curve=`. The `account_series` payload value itself is
    byte-identical. No other line changes.
  - `dashboard/static/index.html` — one new `#capital-risk-readout` card after the
    `#robustness-readout` card.
  - `dashboard/static/app.js` — `renderCapitalRisk`, its call inside `renderAnalyticsSurface`, its entry
    in the `module.exports` object, and `EXPECTED_PAYLOAD_VERSION` 256 → 257. Nothing else.
  - `tests/test_capital_risk_analytics.py` (NEW — the ticket's required focused file).
  - `tests/test_statistical_analytics.py` — additions to the two `build()` cases only; no existing
    assertion is edited or removed.
  - `tests/test_analytics_api.py` — the payload-version pin only (`256` → `257`, with #517 appended
to the bump chain in its comment). That pin is this repo's documented place to record a payload
field shipping, so the literal moves with the version; the assertion itself is not relaxed.
  - `tests/test_analytics_surface_mount.py` — new cases for the readout.
  - `tests/js/analytics_surface_harness.cjs` — stub id, direct render call, printed HTML.
  - `tests/test_build_demo_db.py` — one new case pinning the demo store's capital-risk section is
    measured on the account curve it already seeds.
  - `SPEC.md`, `CONSTRAINTS.md`, `tasks/plan.md`, `tasks/todo.md`.
- Files that must NOT be modified:
  - `core_brain/kpi.py`'s existing analytics math: the inline Sharpe/Sortino/drawdown block and
    `compute_trade_analytics`' payload. This branch reads `kpi` as a test oracle; it does not refactor it.
  - The #516 CI/SQN contract: `CI_Z_BY_LEVEL`, `CI_T_BY_DF`, `ci_z`, `ci_critical_value`,
    `ci_critical_values`, `mean_pnl_ci`, `SQN_NOISE_BELOW`, `SQN_STABLE_EDGE_ABOVE`, `sqn_band`,
    `system_quality_number`, and the `robustness`-shaped payload keys. Reused as-is; not renamed, not re-banded,
    not moved.
  - Sibling telemetry sections from #513 / #514 / #515 and their tests — extended by nobody here:
    `tests/test_fill_path_analytics.py`, `tests/test_rescue_friction_analytics.py`,
    `tests/test_queue_realism_analytics.py`, `tests/test_markout_robustness_analytics.py`.
  - Curve writers and account reads: `core_brain/account.py`, `core_brain/order_registry.py`,
    `core_brain/markout.py`, `core_brain/config.py`, `scripts/build_demo_db.py` (the demo fixture this
    branch needs already exists — see `tasks/plan.md` § Improvement), `scripts/`, `statistical_validation_run/`.
  - LIVE execution and stores: `core_brain/order_manager.py`, `core_brain/trader_loop.py`,
    `core_brain/live_fill_engine.py`, `core_brain/risk.py`, `data/orders.db` and every production
    registry (read-only at all times).
  - The dashboard's other surfaces: `dashboard/server.py`, `dashboard/static/styles.css`,
    `dashboard/static/prototype.js`, `dashboard/static/neon-app.js`, `server/`, `server.ts`. The chip
    palette already exists in the theme; no CSS is added and no route changes.
  - The pre-existing statistics contract: `tests/test_mean_pnl_ci.py`, `tests/test_trade_analytics.py`.
    Both must pass **unchanged**.

## Zero regressions

- Focused suites that must pass on this branch, each run by the agent:
  `tests/test_capital_risk_analytics.py` (new), `tests/test_statistical_analytics.py`,
  `tests/test_analytics_surface_mount.py`, `tests/test_build_demo_db.py`,
  `tests/test_markout_robustness_analytics.py`, `tests/test_mean_pnl_ci.py`,
  `tests/test_trade_analytics.py`, `tests/test_kpi.py`, `tests/test_analytics_api.py`,
  `tests/test_account_kpi.py`.
- Renderer cases in `tests/test_analytics_surface_mount.py` skip when Node is absent; that skip is
  expected on a Node-less host and is not a pass.
- The full `python -m pytest -q` sweep is GitHub CI's job, not a local station step.
- Every changed behaviour carries a test that fails without the change. A test that passes before and
  after is not evidence and does not count toward the bar.

## Anti-cheat

- No skipped, `xfail`ed or deleted test; no removed or weakened assertion; no `# noqa`,
  `# type: ignore`, bare `except` or threshold edited downward to reach green.
- A failing case is fixed **in the code**, or the plan is corrected in `tasks/plan.md` with the
  reason — never by loosening the pin.
- `allow_nan=False` stays the payload's contract: a figure that cannot be produced is `None`, never
  `NaN`, `Infinity` or `0`.
- No operator-settled band boundary may be "rounded to taste": `0.0`/`-0.5` for skewness and
  `2.0`/`1.0` for Sortino are pinned exactly, on the rounded figure.

## Dependencies

- No new external dependency of any kind. The module stays standard-library-only
  (`math`, `statistics`, `bisect`, `random`, `dataclasses`, `typing`).
- No new module file: the routines live in `core_brain/statistical_analytics.py`, beside the four
  sibling telemetry sections, as the ticket's Scope requires.

## Interface freeze

- Signatures, payload key names, band vocabularies and the card's tile labels are frozen in
  `tasks/plan.md` ("Frozen interface contract"). A change to any of them is a planning change,
  recorded there before the code moves.
- Band vocabulary on the page is exactly `good` / `average` / `bad` / `unmeasured`, mapped by the
  renderer to the theme's existing `analytics-chip` modifiers. No renderer holds a threshold, and the
  drawdown-recovery tiles ship **no band key at all** (descriptive, operator-settled).
- The drawdown curve is the caller's. `kpi.report` passes the venue account-value curve; the pure
  functions never assemble one.

## Reporting

- The operator's **"How to verify"** block is hands-on only: build the demo store, open the dashboard,
  read the card. Never `pytest`, `gh`, `git`, or "confirm CI is green".
