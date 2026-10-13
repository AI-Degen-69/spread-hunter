# CONSTRAINTS — #516 (locked by Station II, enforced through Station V)

Governs the `i516/add-post-fill-markout-and-statistical-robustness` branch only. The #515
contract this file replaced is branch-scoped by its own header line and stays readable in git
history.

## Scope & File Boundaries

- Target files for modification:
  - `core_brain/statistical_analytics.py` — the moved CI helper (`CI_Z_BY_LEVEL`, `CI_T_BY_DF`,
    `ci_critical_value`, `mean_pnl_ci`), the new `HeldMark` dataclass, the new constants
    (`MARKOUT_GOOD_AT_OR_ABOVE_CENTS`, `MARKOUT_AVERAGE_ABOVE_CENTS`, `SQN_NOISE_BELOW`,
    `SQN_STABLE_EDGE_ABOVE`, `MARKOUT_HORIZON_SECONDS`), the band/meaning tables, and the new
    functions `markout_cents_band`, `signed_markout`, `max_adverse_excursion`,
    `excursion_summary`, `sqn_band`, `system_quality_number`, `pnl_ci_band`, `robustness`, plus
    the module docstring's fourth-section paragraph. **`build()` keeps every key and value it
    has and gains exactly one additive key (`robustness`); its signature does not change.**
  - `core_brain/kpi.py` — exactly two edits: the `statistical_analytics` import line gains
    `mean_pnl_ci as _mean_pnl_ci`, and the four CI definitions (`_CI_Z_BY_LEVEL`, `_CI_T_BY_DF`,
    `_ci_critical_value`, `_mean_pnl_ci`) are deleted. No other line changes; the call site at
    `kpi.py:496` stays byte-identical, and no payload key is added or altered.
  - `dashboard/static/index.html` — one new `#robustness-readout` card after the
    `#queue-realism-readout` card.
  - `dashboard/static/app.js` — `renderRobustness`, its call inside `renderAnalyticsSurface`,
    and its entry in the `module.exports` object. Nothing else.
  - `scripts/build_demo_db.py` — demo-store seeding so the new card is populated on the demo
    registry (an entry close on the markout-mix market timed to leave marks inside the holding
    window). The existing scenarios keep their rows and their counts.
  - `tests/test_markout_robustness_analytics.py` (NEW — the ticket's required focused file).
  - `tests/test_analytics_surface_mount.py` — new cases for the readout.
  - `tests/js/analytics_surface_harness.cjs` — stub id, direct render call, printed HTML.
  - `tests/test_statistical_analytics.py` — additions to the two `build()` cases only; no
    existing assertion is edited or removed.
  - `tests/test_build_demo_db.py` — one new case pinning the demo store's MAE is measured.
  - `SPEC.md`, `CONSTRAINTS.md`, `tasks/plan.md`, `tasks/todo.md`.
- Files that must NOT be modified:
  - Sampling and fill logic: `core_brain/markout.py`, `core_brain/shadow_fills.py`,
    `core_brain/shadow_exec.py`, `core_brain/live_fill_engine.py`, `core_brain/ladder.py`,
    `core_brain/single_buy_saver.py`. This branch scores recorded trajectories; it does not
    change how they are recorded.
  - Config and schema: `core_brain/config.py`, `core_brain/order_registry.py`,
    `core_brain/price_tape.py`, `core_brain/statistics_store.py`, `core_brain/statistics_report.py`.
  - The dashboard's other surfaces: `dashboard/server.py`, `dashboard/static/styles.css`,
    `dashboard/static/prototype.js`, `server/`, `server.ts`. The chip palette already exists in
    the theme; no CSS is added and no route changes.
  - LIVE execution: `core_brain/order_manager.py`, `core_brain/trader_loop.py`,
    `core_brain/single_leg_lifecycle.py`, `core_brain/risk.py`, `core_brain/market_resolution.py`.
  - Sibling telemetry sections from #513 / #514 / #515 and their tests — extended by nobody here:
    `tests/test_fill_path_analytics.py`, `tests/test_rescue_friction_analytics.py`,
    `tests/test_queue_realism_analytics.py`.
  - The pre-existing CI contract: `tests/test_mean_pnl_ci.py`, `tests/test_trade_analytics.py`.
    Both must pass **unchanged**.
  - `tests/test_shadow_markouts.py`, `tests/test_markout_maturity.py`,
    `docs/audits/expectancy-vs-mean-return.md`.
  - `data/orders.db` and every production store (read-only at all times).

## Zero regressions

- Focused suites that must pass on this branch, each run by the agent:
  `tests/test_markout_robustness_analytics.py` (new), `tests/test_statistical_analytics.py`,
  `tests/test_mean_pnl_ci.py`, `tests/test_trade_analytics.py`,
  `tests/test_analytics_surface_mount.py`, `tests/test_build_demo_db.py`,
  `tests/test_queue_realism_analytics.py`, `tests/test_kpi.py`.
- The full `python -m pytest -q` sweep is GitHub CI's job, not a local station step.
- Every changed behaviour carries a test that fails without the change. A test that passes
  before and after is not evidence and does not count toward the bar.

## Anti-cheat

- No skipped, `xfail`ed or deleted test; no removed or weakened assertion; no `# noqa`,
  `# type: ignore`, bare `except` or threshold edited downward to reach green.
- A failing case is fixed **in the code**, or the plan is corrected in `tasks/plan.md` with the
  reason — never by loosening the pin.
- `allow_nan=False` stays the payload's contract: a figure that cannot be produced is `None`,
  never `NaN`, `Infinity` or `0`.

## Dependencies

- No new external dependency of any kind. The module stays standard-library-only
  (`math`, `random`, `statistics`, `dataclasses`, `typing`).
- No new module file: the routines live in `core_brain/statistical_analytics.py`, beside the
  three sibling telemetry sections, as the ticket's Scope requires.

## Interface freeze

- Signatures, payload key names and band vocabularies are frozen in `tasks/plan.md`
  ("Frozen interface contract"). A change to any of them is a planning change, recorded there
  before the code moves.
- Band vocabulary on the page is exactly `good` / `average` / `bad` / `unmeasured`, mapped by
  the renderer to the theme's existing `analytics-chip` modifiers. No renderer holds a
  threshold, and MAE ships **no band key at all** (a chip on a guess is a confident lie).

## Reporting

- The operator's **"How to verify"** block is hands-on only: build the demo store, open the
  dashboard, read the card. Never `pytest`, `gh`, `git`, or "confirm CI is green".
