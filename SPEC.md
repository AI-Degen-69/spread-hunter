# SPEC.md — Issue #866: Arm-specific effective values in Strategy Parameters panel

Branch: `i866/fix-strategy-parameters-panel-shows-global-config` | Issue: #866

## 1. Goal

The Strategy Parameters panel stops lying by omission: when an arm is selected it
shows that arm's **effective** engine values (preset + arm env overrides applied
through the real loaders), and when nothing is selected it clearly labels the
global base config. Acceptance: `GET /api/parameters?arm=balanced-only` (or any
preset arm) returns `bot_quote_spread_offset_max_cents = 4¢` instead of the global
20¢ default.

## 2. Non-goals (explicit out of scope)

- No change to trading-path loaders (`core_brain/config.py`, `scoring/config.py`)
  or the shadow launcher (`scripts/shadow_tournament.py`, `scripts/tournament_hub.py`).
- No live venue contact: no `quote`, `complete`, Trader loop, or START button.
- No production database writes; `data/orders.db` stays read-only.
- No cockpit cross-page handoff (links carrying plan+arm into the Strategy page):
  deferred, the in-panel dropdown meets the acceptance criteria alone.
- No new external dependencies; standard library only.

## 3. Functional components

### 3.1 Arm context resolution
- Catalog stamped run folders containing `plan.json` (via
  `core_brain/shadow_run.parse_tournament_run_dir_name`), newest plan per issue,
  with arm names for the dropdown.
- Resolve order: explicit `plan` (confined to the tournament runs folder) →
  `tourney` via `latest_tournament_record(issue)` → newest plan across issues.
- Exact arm-name match first; round-suffix-stripped match only when exactly one
  arm matches. Distinct errors, never silent fallback to global:
  `NO_PLAN / MALFORMED_PLAN / UNKNOWN_ARM / AMBIGUOUS_ARM / EVAL_FAILED`.

### 3.2 Isolated evaluation (non-trading child process)
- New module `dashboard/arm_params.py`, free of cockpit imports so the dashboard
  still starts when research modules fail.
- Parent builds the child env exactly like the launcher: copy parent env, strip
  every `BOT_*` / `FILTER_*`, apply the arm's `env` overrides, set
  `SPREAD_HUNTER_DB` to the arm's `db_path`. Child calls `declare_rehearsal()`
  before loading (same as shadow workers), loads core (`load(for_display=True)`)
  and scoring configs separately, serializes strict JSON (`dataclasses.asdict` +
  `filter_common_spread_max_cents`, tuples→lists, NaN/Inf = error). Never trades,
  never loads `.env`.
- Parent runs `sys.executable -m …` with a timeout, rebuilds both `MakerConfig`
  objects from declared fields only (tuples restored), routes scoring failure to
  the existing `_NoScoringConfig` path. Small thread-safe memo keyed by
  (plan path, plan mtime, arm name); cards are rebuilt fresh per request so
  account marks stay current; the global (no-arm) path stays uncached.

### 3.3 Endpoint (`GET /api/parameters`)
- Optional `?arm=<name>&plan=<key>&tourney=<id>`. Response adds
  `arm_context: {plan_key, issue, arm, source} | null` plus `arm_error` on
  failure. Global path byte-identical to today.
- Three missing Queue Gate cards, showing what the engine actually uses:
  `bot_order_gate_queue_clear_on`, `bot_order_queue_max_clear_min`, and
  `bot_order_queue_flow_window_sec` (= 1800s default; the loader has **no**
  `BOT_ORDER_QUEUE_FLOW_WINDOW_SEC` reader, so a plan recording `300` gets a
  visible "recorded override ignored" note rather than a fake 300).

### 3.4 UI (panel header dropdown)
- Arm picker in `#params-panel` header (populated from the catalog, grouped by
  tournament), `app.js` fetches with arm/plan params, guards against stale
  responses, and displays exact plan+arm identity. A "Global default" label
  marks the unselected state.

## 4. Acceptance

- `GET /api/parameters?arm=<balanced-preset-arm>` returns max-offset `4¢`;
  no-arm request returns the global `20¢` with a global-default label.
- Queue cards present with true engine values; flow-window card notes the
  ignored override when a plan records one.
- Unknown/ambiguous arm returns a structured error, never global values.
- Dashboard-only verification: no worker launch, no venue contact.
