# CONSTRAINTS.md — Issue #857: Preflight-guided Start Run launcher with mode selection, 24h horizon, and watchdog

Branch: `i857/preflight-guided-start-run-launcher` | Issue: #857

Locked during Station II planning. Stations III–V must hold every line below.
Tighten only with operator approval; never loosen silently.

## Zero regressions

- Focused suites for the touched modules must pass cleanly:
  - `python -m pytest -q tests/test_dashboard_server.py`
  - `python -m pytest -q tests/test_trader_loop.py`
  - `python -m pytest -q tests/test_order_manager.py`
  - `node tests/js/master_toggle_harness.cjs dashboard/static/app.js`
- Full regression suite `python -m pytest -q` is the GitHub CI merge gate on Ubuntu and Windows; local review runs targeted checks only.
- Every new behavior must have a test that fails without it:
  - Preflight sequential enforcement (steps 1 -> 2 -> 3 -> 4 in order).
  - Reach probe failure closed without `confirm: true`.
  - Rejection of start without valid `preflight_id`.
  - Atomicity of `preflight_id` consumption (single-use).
  - `--deadline-ts` enforcement in `trader_loop.py` (clean exit on deadline and immediate exit if past).
  - Watchdog flag `--watch-guardrails` in live stack commands.
  - Numbered shadow database sequence generation (01..99 wrap).
  - Modal appearance on master toggle START click without initiating immediate processes.

## Scope fences (hard)

- **Only these files change:**
  - `dashboard/preflight.py` (new)
  - `dashboard/server.py`
  - `core_brain/order_manager.py` (balance helper extraction only)
  - `core_brain/trader_loop.py` (`--deadline-ts` CLI handling and sleep wrapping only)
  - `dashboard/static/index.html` (`#start-run-modal` markup)
  - `dashboard/static/app.js` (modal & preflight handler logic)
  - `dashboard/static/styles.css` (modal and preflight indicators)
  - `scripts/spread-hunter-menu.ps1` (`Start-BotStack` refusal messaging)
  - `tests/test_dashboard_server.py`
  - `tests/test_trader_loop.py`
  - `tests/js/master_toggle_harness.cjs`
  - `SPEC.md`, `CONSTRAINTS.md`, `tasks/plan.md`, `tasks/todo.md`, `tasks/pr-body.md`
- **Must NOT be modified:**
  - `data/orders.db` (production registry is strictly read-only; never rewrite or delete).
  - `.env`, `.env.example` (never write credentials or modify env templates).
  - `core_brain/config.py`, `core_brain/venue.py`, `core_brain/order_registry.py`, `core_brain/order_execution.py`.
  - `core_brain/reach_probe.py` (already delivered and tested).
  - `scripts/global_stop_loss.py` (already delivered and tested).

## Anti-cheat & Safety rules

- **No test touches the live venue:** All tests must mock venue calls, CLOB clients, processes (`Popen`), and clocks.
- **No live orders during automated build/test:** Never run live trading or unconstrained live probes.
- **Fail closed:** Any unreadable preflight condition or missing requirement blocks live start.
- **No duplicate watchdog:** `order_manager poll` already supervises `scripts.global_stop_loss`; the dashboard must NOT launch a separate standalone watchdog process.
