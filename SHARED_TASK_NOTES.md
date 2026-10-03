# Self-improvement loop backlog

## Iteration 1 — anchor the queue report default database

- **Area / Files:** `scripts/queue_report.py`, `tests/test_queue_report.py`
- **Problem:** The operator-facing queue report defaults to `data/shadow.db` relative to the current working directory, so launching it from `scripts/`, a scheduled task, or another shell location can read the wrong store or fail to find the rehearsal data.
- **Solution:** Resolve the default through the existing repository-root path constant and add a regression test that invokes the CLI from another cwd and verifies the exact database path passed to the report.
- **Benefits:** Keeps read-only queue diagnostics aligned with the shadow runner and prevents misleading empty reports caused by ambient cwd.
- **Strength Badge:** Strong
- **Status:** Selected and implemented in this iteration.

## Iteration 2 — centralize market-feed runtime paths

- **Area / Files:** `core_brain/market_feed.py`, `tests/test_market_feed.py`
- **Problem:** The market feed constructed its canonical `runtime/markets.json` path locally and only called the shared resolver as a fallback, leaving runtime-directory ownership split across two modules.
- **Solution:** Build the canonical path through `runtime_paths.runtime_file` and use the shared resolver for the default read, while preserving explicit path overrides used by callers and tests.
- **Benefits:** One owner for current/legacy runtime naming and safer future state-directory migrations without changing feed precedence.
- **Strength Badge:** Strong
- **Status:** Selected and implemented in this iteration.

## Iteration 3 — consistent read-only report errors

- **Area / Files:** `dashboard/server.py`, `tests/test_dashboard_server.py`
- **Problem:** The KPI, run-profitability, and closed-markets read-only endpoints each formatted backend failures independently, and their payloads omitted the exception type needed to diagnose stale or malformed report data.
- **Solution:** Centralize their 500 response formatting as `{error, error_type}` while leaving all POST/control handlers untouched.
- **Benefits:** Predictable frontend handling and actionable operator diagnostics without changing live execution controls.
- **Strength Badge:** Strong
- **Status:** Implemented and verified.

## Iteration 4 — surface dashboard telemetry read failures

- **Area / Files:** `dashboard/server.py`, `dashboard/static/app.js`, `tests/test_dashboard_server.py`
- **Problem:** Scan-state, pair activity, guardrail alerts, and guardrail health silently converted cycle-ring read failures into empty `200` payloads, while the frontend ignored telemetry diagnostics and could render the failure as idle or stopped.
- **Solution:** Share one ring reader that preserves the existing empty-state payloads but adds a structured `telemetry_error` diagnostic only when the ring read fails; render affected scan/guardrail surfaces as UNKNOWN with an actionable tooltip.
- **Benefits:** Operators can distinguish “no events yet” from “telemetry unavailable” without changing endpoint status codes or live controls.
- **Strength Badge:** Strong
- **Status:** Implemented and verified.


## Issue #351 — zero-fill 01_shadow diagnosis + queue-depth surfacing

- **Area / Files:** `core_brain/shadow_fills.py`, `core_brain/statistics_report.py`,
  `tests/test_shadow_fills.py`, `tests/test_statistics_report.py`,
  `docs/issues/analysis-01-shadow-zero-fill.md`
- **Problem:** A `01_shadow` rehearsal recorded 0 fills with no explanation; deep queue
  multiples (420x–7741x) and a single decision cycle were invisible in the shadow report.
- **Solution:** Pure `queue_multiple()` helper; queue-depth stats + single-cycle warning in the
  shadow report (shown even when the close-count gate fails); diagnosis doc with
  reported-not-reproduced evidence and read-only confirm queries. Queue-bar enforcement deferred
  (shared ranker feeds live trading).
- **Benefits:** The next zero-fill run explains itself in the report instead of reading as a
  fill-model defect.
- **Status:** Implemented and verified — `python -m pytest -q tests/test_shadow_fills.py
  tests/test_maker_queue_bar.py tests/test_statistics_report.py` → 48 passed (agent-run).
