# Station II Plan — Issue #866: Arm-specific effective values in Strategy Parameters

Branch: `i866/fix-strategy-parameters-panel-shows-global-config` | Issue: #866

- **Tier:** Standard (2–5 files: one new module + endpoint + two static files + tests; one architectural decision — child-process isolation).
- **Task types:** Code (primary), Design/UI (arm picker dropdown + labels).
- **Stack / runner:** Python 3, dashboard (Flask-style `dashboard/server.py`), vanilla JS (`dashboard/static/app.js`), `pytest` (`python -m pytest -q`).

---

## 1. The problem, in one line

The Strategy Parameters panel always renders the global base config, so an
operator comparing arms reads the wrong numbers (e.g. global 20¢ ceiling while
the balanced arm actually runs 2–4¢).

## 2. CodeRabbit intake (read once; Stations III–V need not reopen it)

Adopted: the task skeleton (catalog → child eval → endpoint + cards → picker);
the non-trading child process running the real loaders with a worker-style env
(only isolation that survives the dashboard's multi-threaded process); distinct
error codes with no silent global fallback; the thread-safe memo keyed by
(plan path, mtime, arm); dashboard-only verification (no worker launch).
Borrowed test ideas: exact/ambiguous arm-match assertions, child-eval
round-trip, endpoint `?arm=` returning 4¢, regression file
`tests/test_dashboard_server.py`.

Rejected:
- *Six phases with separate catalog/runner/memo tasks* — over-split; merged
  into T1 (one module, one contract) per the simplicity rule.
- *`?run_id=` as the arm selector* — the ticket's `?arm=` default stays;
  `plan`/`tourney` are optional disambiguators, not replacements.
- *Cockpit-to-Strategy deep links carrying plan+arm* — out of scope; the
  in-panel dropdown meets acceptance alone (SPEC §2).

Checked during planning instead of trusted (`[UNVERIFIED]` → verified):
- Issue cites `dashboard/server.py:5291` — stale; `get_parameters` lives at
  `:5547`, parameter list extends past `:6383` (verified by grep).
- Issue names arm `balanced-queue5-flow5` — does not exist; repo has
  `balanced-queue5` (i843, 4 arms) and `balanced-mid` / `queue-clear-5m`
  (i799, 10 arms). Plan resolves any arm by name; no hardcoded arm.
- Issue expects flow window `300` — the engine has no
  `BOT_ORDER_QUEUE_FLOW_WINDOW_SEC` reader (`core_brain/config.py:415-433`
  reads only the first two queue keys; field default `1800.0` at `:220`;
  consumed at `trader_loop.py:1422`). T2 shows the true `1800s` with an
  ignored-override note (needs no extra approval: showing `300` would be the
  lie this issue exists to fix).
- No `data/tournaments/` on disk right now — T4 verifies against a fixture
  `plan.json` (or any existing record), never by launching workers.

## 3. Interface contract (locked before code)

**Request:** `GET /api/parameters?arm=<name>&plan=<run-folder-key>&tourney=<id>`
(all three optional; global path = no params, byte-identical to today).

**Response additions:**
```json
{
  "parameters": [ "...unchanged cards + 3 queue cards..." ],
  "arm_context": {"plan_key": "<repo-relative run folder>", "issue": 843,
                  "arm": "balanced-queue5", "source": "plan|tourney|newest"} | null,
  "arm_error": {"code": "UNKNOWN_ARM", "message": "..."} // on failure only
}
```

**Resolution order:** explicit `plan` (confined under the tournament runs
folder) → `tourney` via `latest_tournament_record(issue)` (+ newest-run
override in `_resolve_plan_for_tourney_key`) → newest plan across issues.
Arm match: exact first, then round-suffix-stripped only when exactly one arm
matches.

**Three new cards** (keys = engine field names):
| key | value source | note |
| --- | --- | --- |
| `bot_order_gate_queue_clear_on` | effective bool | — |
| `bot_order_queue_max_clear_min` | effective float (`Xm`) | — |
| `bot_order_queue_flow_window_sec` | effective float (`1800s`) | visible "recorded `300` ignored — no loader reads it" note when the plan records an override |

**Must not change:** engine arithmetic; loader signatures; launcher/cockpit;
`data/orders.db`; saved `plan.json` files.

## 4. Tasks

### [x] T1 — Arm catalog + isolated child evaluation · M · `[Backend/Logic]`
Depends on: — (riskiest first)

- `dashboard/arm_params.py` (new, no cockpit imports): scan stamped run
  folders with `plan.json` via `parse_tournament_run_dir_name()` from
  `core_brain/shadow_run.py`; catalog = newest plan per issue (repo-relative
  key, issue, run number, stamp, arm names). Resolve `(plan, tourney, arm)`
  per §3; validate arm record (non-empty name, `env: dict[str,str]`,
  optional `db_path`); distinct errors, never silent global fallback.
- `__main__` entry point, run as `sys.executable -m dashboard.arm_params`:
  build child env like `scripts/shadow_tournament.py` (copy parent, strip
  `BOT_*`/`FILTER_*`, apply arm `env`, set `SPREAD_HUNTER_DB`); child calls
  `declare_rehearsal()`, loads core (`load(for_display=True)`) + scoring
  separately with per-loader error capture; strict JSON out
  (`dataclasses.asdict` + `filter_common_spread_max_cents`, tuples→lists,
  NaN/Inf = error). No trading, no `.env` load; note the shipped-defaults
  basis in the response.
- Parent runner: timeout, non-zero/invalid-JSON → `EVAL_FAILED` with readable
  message; rebuild both `MakerConfig`s from declared fields only (restore
  tuples); scoring failure → existing `_NoScoringConfig` path. Memo
  (thread-safe, keyed plan-path + mtime + arm) caches configs only; cards
  rebuilt per request; global path uncached.
- Verify: new `tests/test_arm_params.py` (catalog from fixture plans,
  exact/round-suffix/ambiguous/unknown arm, child round-trip incl. balanced
  preset → max `4`, memo invalidation on mtime change).

### T2 — Endpoint wiring + three Queue Gate cards · M · `[Backend/Logic]`
Depends on: T1

- `dashboard/server.py` `get_parameters()` (`:5547`): accept
  `?arm=&plan=&tourney=`; no-arm → today's code path untouched; with-arm →
  `arm_params` resolve + eval, attach `arm_context` / `arm_error`, build the
  same card list from the effective configs (attribute access keeps working
  because real `MakerConfig`s are rebuilt).
- Add the three cards from §3 at the queue-gate subgroup; flow-window card
  carries the ignored-override note. Response states the shipped-defaults
  basis (child never loads `.env`).
- Verify: `tests/test_dashboard_server.py` additions — `?arm=balanced-only`
  (fixture plan) returns max-offset `4¢` + correct `arm_context`; unknown arm
  returns `arm_error` and no parameters masquerading as arm values; no-arm
  response byte-identical to before. Run:
  `python -m pytest -q tests/test_dashboard_server.py -k "parameters"`.

### T3 — Arm picker dropdown + labels · S · `[Design/UI]`
Depends on: T2

- `dashboard/static/index.html` `#params-panel` header: arm `<select>`
  (grouped by tournament, populated from the catalog endpoint data) + identity
  line (plan key + arm) + "Global default" label when unselected.
- `dashboard/static/app.js` `renderParameters()`: fetch with arm/plan params,
  monotonically-guarded against stale responses; `renderParametersView()`
  renders `arm_context`/`arm_error` states without touching scope/view-mode
  logic.
- Verify: browser gate — open the dashboard, pick a balanced arm, confirm the
  max-offset card reads `4¢` with plan+arm identity; clear the picker, confirm
  `20¢` with the global-default label; pick a bogus arm (via console) and
  confirm the structured error, not global values.

### T4 — Gates and handoff · S · `[Testing]`
Depends on: T1, T2, T3

- Targeted runs: `tests/test_arm_params.py` + `tests/test_dashboard_server.py
  -k parameters` in one pass; grep gate confirming no `os.environ` swap was
  introduced in `dashboard/` and no loader/launcher file was touched.
- Operator "How to verify" block (hands-on only, no pytest) + PR body.

**Checkpoints:** after T1 (child round-trip green), after T2 (endpoint `?arm=`
green), after T3 (browser gate), after T4 (targeted run + handoff). In `auto`
mode these are one-line reports.

## 5. Deliberately out of scope

- `?run_id=` selector; cockpit deep links; flow-window env reader
  (`BOT_ORDER_QUEUE_FLOW_WINDOW_SEC` stays unread — T2 surfaces the truth
  instead); `Decimal` money math; risk-ratio changes; `data/orders.db`.

## 6. Open questions from the issue, answered from code

1. *"Load the plan, find the arm's env overrides, apply to a temp config"* —
   answered: temp config = child process with worker-style env (T1), because
   both loaders read process-wide `os.environ` (`core_brain/config.py:840`,
   `scoring/config.py:154`) and the dashboard server is multi-threaded.
2. *"Add missing queue gate parameters"* — answered: two have readers, the
   flow window does not; all three cards ship, the third with an honesty note.
3. *"Dropdown in panel header"* — answered: `#params-panel` header select (T3).

## 7. Rejected proposals log

| Proposal | Verdict | Why |
| --- | --- | --- |
| Swap `os.environ` under a lock in-process | Rejected | Dashboard is multi-threaded; other threads read the same env — the child is the only safe isolation |
| Add env-map params to both loaders | Rejected | Changes the trading path the ticket puts out of scope; child reuses loaders untouched |
| Show recorded `300` as the flow-window engine value | Rejected | No loader reads it; `trader_loop.py:1422` falls back to the `1800.0` field default — showing `300` repeats the exact lie being fixed |
| Launch `reconcile_tournament_stack` workers to verify | Rejected | Real-money-adjacent worker launch for a dashboard-only change; verify against an existing/fixture `plan.json` + dashboard only |
