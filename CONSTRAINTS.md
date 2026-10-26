# CONSTRAINTS.md — Issue #843: Rehearse the balanced preset and the 5-minute queue-clear gate together as one arm

Branch: `i843/rehearse-balanced-preset-queue-gate` | Issue: #843

Locked during Station II planning. Stations III–V must hold every constraint below.
Tighten only with operator approval; never loosen silently.

## Zero regressions

- Focused test suites covering tournament planning and arms execution must pass:
  `python -m pytest -q tests/test_tournament_arms_i843.py tests/test_shadow_tournament.py tests/test_tournament_cockpit_arms.py tests/test_tournament_run_number_naming.py`
- The full `python -m pytest -q` regression suite runs in GitHub CI and is the merge gate.

## Scope fences (hard)

- **Permitted file changes:**
  - `configs/tournaments/i843-pair-test_arms.json`: four arms only (`control`, `balanced-only`, `queue5-only`, `balanced-queue5`).
  - `docs/runs/2026-10-26-843-balanced-queue5-pair.md`: pre-registered decision rule and run report.
  - `tests/test_tournament_arms_i843.py`: dedicated test suite verifying the arms configuration and plan generation.
  - `tasks/plan.md`, `tasks/todo.md`, `tasks/pr-body.md`, `SPEC.md`, `CONSTRAINTS.md`: planning and tracking artifacts.
- **Files explicitly frozen / not to modify:**
  - `core_brain/config.py`: shipped defaults and `TOURNAMENT_PRESETS` must NOT be touched.
  - `.env` and `.env.example`: no live configuration modifications.
  - `configs/tournaments/i799-tournament-test_arms.json`: in-flight Tournament #799 is live and read-only.
  - Any production database (`data/orders.db`): strictly read-only.
  - Tournament Cockpit tabs and server (`scripts/tournament_cockpit_server.py`, `dashboard/static/app.js`).
- **Safety overrides prohibited in arms:**
  - No arm may set `BOT_RISK_STOP_MODE`, `BOT_RISK_STOP_ADVERSE_DROP_CENTS`, `BOT_ORDER_REQUOTE_DEAD_BAND_CENTS`, or `BOT_RISK_AGED_OUT_RESCUE_LEAD_SEC`.
  - Every arm runs only the shipped baseline plus its explicitly assigned knobs.

## Anti-cheat

- Never skip, xfail, or delete existing tests to get green.
- Never weaken assertions or mock around broken contracts.
- No dummy/mock arms files replacing production schemas.
- If sample size $n$ from `stat_gate.py` is not reached, never declare a winner; declare the run underpowered as specified by the decision rule.
