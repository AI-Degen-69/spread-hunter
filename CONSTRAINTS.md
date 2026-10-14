# CONSTRAINTS — #524 (locked by Station II, enforced through Station V)

Governs the `i524/fix-rescue-closes-never-record-a-fee-so-taker-cost` branch only.
The #517 contract this file replaces is branch-scoped by its own header line and stays
readable in git history.

## Fee policy (adopted default, owner-overridable)

- Shadow rescue closes save `fee=0.0` (measured zero).
- Live rescue closes keep `fee=None` (unmeasured) — the venue charges takers up to 7%
  (`core_brain/venue_fees.py:14`, `core_brain/config.py:1115`), and "an unmeasured fee and
  a zero fee are different claims" (`core_brain/venue_fees.py:77`). A live zero would be
  a false measured claim.
- Override: only on explicit owner approval, pass `ZERO_FEE` on the live branch too.

## Scope & File Boundaries

- Target files for modification:
  - `core_brain/single_buy_saver.py` — exactly three edits: the `ZERO_FEE = 0.0`
    module constant plus its source comment, the optional `fee` keyword on
    `_record_exit_close` (default `None`, passed into the `CloseRecord`), and
    `fee=ZERO_FEE` passed at the existing `_sell_into_bids` branch call site
    (`:992-994`). No new helper, no config setting.
  - `tests/test_single_buy_saver.py` — `ShadowFakeClient` plus four test-first cases
    (shadow parametrized, shadow partial, live-pin, friction summary).
  - `SPEC.md`, `CONSTRAINTS.md`, `tasks/plan.md`, `tasks/todo.md`.
- Files that must NOT be modified:
  - `core_brain/order_registry.py` — schema (`fee REAL` at `:296`), `CloseRecord.fee`
    (`:769`), `log_close` (`:1555-1602`) already persist the fee. No migration, no INSERT change.
  - `core_brain/statistical_analytics.py` — TCDR formula and NULL-vs-zero rules unchanged;
    `tests/test_rescue_friction_analytics.py` unchanged.
  - Merge writers (`core_brain/shadow_exec.py`, `core_brain/order_manager.py`), resolution
    closes (`core_brain/market_resolution.py`), `taker_fees_paid` (`core_brain/kpi.py`).
  - Fee regime sources: `core_brain/config.py`, `core_brain/venue_fees.py`.
  - `exit_single_buy` sell behavior: order args, price floor, size caps, cancel/reread
    steps, `reason` passing; the `exit_naked_leg` alias.
  - LIVE execution and stores: `core_brain/trader_loop.py`, `core_brain/live_fill_engine.py`,
    `core_brain/risk.py`, `data/orders.db` and every production registry (read-only at all times).

## Zero regressions

- Focused suites that must pass on this branch, each run by the agent:
  `tests/test_single_buy_saver.py`, `tests/test_rescue_friction_analytics.py`.
- Focused extra checks (callers of the touched function):
  `tests/test_rc_fixes.py`, `tests/test_shadow_exec.py`.
- The full `python -m pytest -q` sweep is GitHub CI's job, not a local station step.
- Every changed behaviour carries a test that fails without the change.

## Anti-cheat

- No skipped, `xfail`ed or deleted test; no removed or weakened assertion; no `# noqa`,
  `# type: ignore`, bare `except` or threshold edited downward to reach green.
- A failing case is fixed **in the code**, or the plan is corrected in `tasks/plan.md` with
  the reason — never by loosening the pin.

## Dependencies

- No new external dependency of any kind. No new module or script file
  (no `scripts/seed_rescue_fixture.py` — existing test helpers seed and force the exit).

## Interface freeze

- `ZERO_FEE = 0.0` (module constant, `core_brain/single_buy_saver.py`).
- `_record_exit_close(..., fee: Optional[float] = None)` — additive keyword only; every
  existing parameter, position and default unchanged. Its value passes straight into
  `CloseRecord(fee=...)`.
- A change to any of these is a planning change, recorded in `tasks/plan.md` before code moves.

## Reporting

- The operator's **"How to verify"** block is hands-on only: throwaway shadow store,
  dashboard open, read the card. Never `pytest`, `gh`, `git`, or "confirm CI is green".
- The report states the adopted fee policy (shadow-only). Under it, live rescue closes
  still read "unmeasured" on purpose.
