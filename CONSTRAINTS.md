# CONSTRAINTS — #525 (locked by Station II, enforced through Station V)

Governs the `freebuff/ii-plan-issue-525-2433c4be-7d14-441b-999b-3cf6ef082ed3` branch only. The #517
contract this file replaced is branch-scoped by its own header line and stays readable in git
history, as #514's–#516's do.

## Scope & File Boundaries

- Target files for modification:
  - `core_brain/single_buy_saver.py` — exactly two edits in `_route_pair` only: add
    `trigger_bid=heavy_bid` to the adverse-drift `exit_single_buy` call (~:2048-2050) and to the
    timer `exit_single_buy` call (~:2065-2067). Reasons, methods, thresholds, trigger conditions,
    and branch order unchanged.
  - `core_brain/statistical_analytics.py` — one docstring line only: the `_stop_reference`
    "`Only the lifecycle hard stop writes it`" sentence goes stale once two more writers exist
    (see tasks/plan.md § Improvement, adopted). No math, no key, no threshold changes.
  - `tests/test_single_buy_saver.py` — new regressions + book stub only (adverse-drift record,
    assumed-count-zero, timer record, timer-None guard, forced shadow case).
  - `SPEC.md` — not touched (Small tier: spec lives embedded in tasks/plan.md).
  - `CONSTRAINTS.md`, `tasks/plan.md`, `tasks/todo.md`.
- Files that must NOT be modified:
  - `exit_single_buy` itself, `_record_exit_close`, the lifecycle hard stop (~:1784-1789),
    the stranded-unhedged exit (~:1899-1903), `_settlement_fallback`, `stray_guard.exit_stray`,
    `single_leg_lifecycle.py`, `ladder.py`, `config.py`, `shadow_exec.py`, `shadow_run.py`,
    `order_registry.py`.
  - Analytics math: `_stop_reference` logic, `STOP_OFFSET_FALLBACK`, drag formulas,
    `rescue_friction_summary`.
  - Sibling regression files: `tests/test_ladder_exit.py`, `tests/test_dual_stop_loss.py`
    (including its stale docstring), `tests/test_hard_stop_fresh_exit.py`,
    `tests/test_rescue_friction_analytics.py` — run as oracles, never edited.
  - LIVE execution and stores: `data/orders.db` (read-only at all times); never run `quote`,
    `complete`, `trader_loop --live`, or dashboard **START**.

## Zero regressions

- Focused suites that must pass on this branch, each run by the agent:
  `tests/test_single_buy_saver.py` (extended), `tests/test_rescue_friction_analytics.py`
  (unchanged), plus oracle runs of `tests/test_ladder_exit.py`, `tests/test_dual_stop_loss.py`,
  `tests/test_hard_stop_fresh_exit.py`.
- The full `python -m pytest -q` sweep is GitHub CI's job, not a local station step.
- Every changed behaviour carries a test that fails without the change (RED before GREEN), except
  the timer-None guard, which is documented in the plan as passing before and after by design.

## Anti-cheat

- No skipped, `xfail`ed or deleted test; no removed or weakened assertion; no `# noqa`,
  `# type: ignore`, bare `except`, or threshold edited downward to reach green.
- A failing case is fixed **in the code**, or the plan is corrected in `tasks/plan.md` with the
  reason — never by loosening the pin.
- Never substitute `fresh_bid` for a missing `heavy_bid`: a timer close with no decision bid stays
  `trigger_bid=NULL` / `assumed_offset` by design.
- A figure that cannot be produced is `None`, never `NaN`, `Infinity`, or `0`.

## Dependencies

- No new external dependency of any kind. Test doubles are hand-rolled stubs in the test file.

## Interface freeze

- `exit_single_buy(..., *, force=False, trigger_bid=None)` signature unchanged — the fix only
  passes an already-computed value at two existing call sites.
- Close-row keys (`trigger_bid`, `fresh_bid`, `reason`, `method`) and
  `rescue_friction_summary` keys (`recorded_reference_count`, `assumed_reference_count`)
  unchanged. Any change to them is a planning change, recorded in `tasks/plan.md` first.

## Reporting

- The operator's **"How to verify"** block is hands-on only: shadow rehearsal store, dashboard,
  report script. Never `pytest`, `gh`, `git`, or "confirm CI is green".
