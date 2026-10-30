# CONSTRAINTS.md — Issue #875: Correct Sharpe ratio convention (time-series)

Branch: `i875/establish-correct-sharpe-ratio-convention` | Issue: #875

Locked during Station II planning. Stations III–V must hold every line below.
Tighten only with operator approval; never loosen silently.

## The one invariant this change exists to protect

**Every dollar figure stays bit-identical; only the risk-adjusted ratio changes.**

`total_pnl`, `mean_pnl`, `profit_factor`, `max_drawdown`, merge/stop counts and
all gate thresholds (`2.70` / `2.20` / `2.50` / `$4.00`) keep their numeric values.
The Sharpe number itself is *supposed* to move — it was a per-trade ratio ×10 and
becomes an annualized time-series ratio. Any test whose PnL/PF/MDD expectation
moves is evidence of scope creep — stop and re-read.

## Zero regressions

- Targeted suites covering the touched modules must pass cleanly:
  - `python -m pytest -q tests/test_sharpe_ratio.py` (new — fails without the change)
  - `python -m pytest -q tests/test_tournament_cockpit_decision_gates.py tests/test_reevaluate_tournament_run.py`
  - `python -m pytest -q tests/test_shadow_tournament.py tests/test_tournament_cockpit_funnel.py`
  - `python -m pytest -q tests/test_trade_analytics.py tests/test_kpi.py tests/test_541_parity_ledger.py` (kpi untouched — must stay green)
- Full `python -m pytest -q` is the GitHub CI merge gate on Ubuntu and Windows; local runs are
  targeted only.
- Every new behavior needs a test that fails without it:
  - known-series helper test with hand-computed closed form (see plan T1);
  - cockpit test asserting the new `sharpe_ratio` field on a fixture DB (not the proxy);
  - `None`/NO_DATA cases: no trades, single step, flat equity.

## Scope fences (hard)

- **Only these areas change:**
  - `core_brain/sharpe.py` (new) — `sharpe_ratio()`, `risk_free_rate_from_env()`, `SHARPE_FORMULA_TEXT`.
  - `scripts/tournament_cockpit_server.py` — `analyze_arm_database` ratio, `compute_decision_gates`
    thresholds as named constants, cockpit HTML `Sharpe Ratio` label + formula tooltip.
  - `scripts/reevaluate_tournament_run.py` — gate/ranking/display switch to `sharpe_ratio`.
  - `docs/sharpe-ratio-convention.md` (new) — the locked convention note.
  - `tests/test_sharpe_ratio.py` (new) + mechanical `sharpe_proxy` → `sharpe_ratio` updates in
    cockpit/reevaluate/shadow-tournament tests.
  - `tasks/plan.md`, `tasks/todo.md`, `CONSTRAINTS.md`.
- **Must NOT be modified:**
  - `core_brain/kpi.py` (`compute_trade_analytics` per-trade Sharpe stays — explicit
    no-annualisation rationale at `kpi.py:579-585`; golden payload `0.6229353305576062` pins it).
  - `core_brain/config.py`, `.env.example` (new setting lives in `sharpe.py` via `_bounded_float`
    import; no config-schema change this round).
  - `data/orders.db` (production registry, strictly read-only) and any `data/tournaments/*`.
  - Gate threshold *numbers* (`GATE_SHARPE_MIN = 2.70` etc. — relabeled for review, never retuned here).
  - Historical backfills — future runs only, no recomputation of past figures.
  - Live market risk-free rates — constant configurable default `0`, no network source.

## Anti-cheat & safety rules

- **Anti-cheat:** no skipped/xfailed tests, no deleted assertions, no weakened bounds, no
  `# type: ignore`/`noqa` added to get green. `None` means unmeasurable — never render it as `0.0`.
- **No new dependencies.** Standard library only (`math`, `os`).
- **No live venue contact.** Planning, build, tests and review never run `quote`, `complete`,
  the Trader loop, or the dashboard START button.
- **No production database in any manual check.** Any `status` run points at a disposable DB.
