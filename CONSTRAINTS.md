# CONSTRAINTS.md — Issue #840: Live execution lane audit and reachability verification

Branch: `i840/live-execution-lane-audit` | Issue: #840

Locked during Station II planning. Stations III–V must hold every line below.
Tighten only with operator approval; never loosen silently.

## Zero regressions

- Focused suites for the touched modules must be green after the change:
  `tests/test_shadow_guard.py tests/test_order_manager.py tests/test_order_manager_decide.py`
  plus the new `tests/test_champion_lane_parity.py` and the regression files the
  plan touches: `tests/test_production_registry_guard.py`,
  `tests/test_config_single_source.py`, `tests/test_dynamic_offset.py`,
  `tests/test_stop_loss_config.py`, `tests/test_queue_clear_gate.py`,
  `tests/test_book_provider.py`, `tests/test_live_venue_order_caps.py`.
  The full `python -m pytest -q` regression suite is the GitHub CI merge gate;
  it is not re-run locally during review.
- Every new behaviour needs a test that fails without it: all five parity
  tests, all four status/balance exit-code tests, and every `reach-probe`
  pre-flight/post/cancel/evidence test named in `tasks/plan.md`.
- Existing `status`/`balance` stdout assertions stay valid unchanged — only the
  exit code changes on the failure paths.

## Scope fences (hard)

- **Only these files change:** `docs/live-lane-audit.md` (new),
  `tests/test_champion_lane_parity.py` (new), `core_brain/reach_probe.py`
  (new), `core_brain/order_manager.py` (status/balance exit codes + `reach-probe`
  subcommand only), `core_brain/markets.py` (`fetch_pinned_market` optional venue
  minimum-size field only), `docs/agents/safety.md` (one `reach-probe --live`
  entry), `tests/test_order_manager.py` (new cases only),
  `docs/backlog/i840-live-reachability.md` (out-of-scope log).
- **Must NOT be modified:** `data/orders.db` (read-only; never rewrite or
  delete), `.env`, `.env.example`, `core_brain/trader_loop.py`,
  `core_brain/config.py`, `core_brain/venue.py`, `core_brain/order_registry.py`,
  `core_brain/order_execution.py`, `core_brain/single_buy_saver.py`,
  `core_brain/shadow_run.py`, `core_brain/shadow_exec.py`,
  `core_brain/shadow_guard.py`, `scripts/spread-hunter-menu.ps1`,
  `scripts/shadow_tournament.py`, `configs/tournaments/*`, `dashboard/*`,
  `tests/test_shadow_guard.py`, the legacy `probe` command and the
  `order_manager.py` module docstring.
- **Document, don't fix:** trader-loop rollback marking survivors cancelled on
  failed cancel (and dropping venue IDs), lenient cancel-response handling,
  missing order readback in `_submit_intents`, pair-ID handling differences,
  menu launcher dropping `BOT_QUOTE_TOURNAMENT_PRESET` for live Trader, dropped
  dynamic-offset telemetry in production `_market_specs`. File as follow-ups.
- **Plan only in this station:** no venue orders, no live commands, no
  `reach-probe --live` / `quote` / Trader START during planning or build
  without operator session approval.

## Anti-cheat

- Never skip, xfail, or delete a test to get green; never weaken an assertion,
  and never edit output text just to satisfy a stdout assertion.
- An unreadable matched size is `unverified`, never zero. A missing venue ID
  after post leaves the row pending and reports `uncertain`, never PASS.
- Never treat a cancel receipt alone as proof — PASS requires venue order
  status + matched size 0 + absence from open orders.
- Never log, print, or persist keys, API credentials, signatures, or full raw
  venue responses (evidence file included).

## Performance / architecture ceilings

- Reachability check bounds (hard, not configurable upward): exactly one
  post-only GTC order, discount default 0.15, hold 2–4s (default 3s),
  worst-case exposure (size × price) ≤ $2.00 fixed ceiling, accept-to-cancel-
  receipt ≤ 5s. No CLI flag may raise the $2 ceiling.
- `status`/`balance` change is exit-code only — no new output format, no new
  dependencies.
- `fetch_pinned_market` addition is one optional field defaulting to `None` —
  existing callers unchanged. Never use `rewards.min_size` as the venue minimum.
- Reuse, don't copy: pending-row helper from `quote`, `venue_order_id` from
  `core_brain/venue.py`, matched-size reader imported from
  `core_brain/single_buy_saver.py` (do not duplicate it).

## Safety (repo rules)

- LIVE is the default: every `order_manager` subcommand reaches the venue.
  `--no-live` is the dry run. Opening commands (`quote`, `complete`, Trader
  loop, dashboard START, and now `reach-probe --live`) need operator approval
  in that session — propose, don't run.
- `data/*.db` and `runtime/*` state are gitignored and must never be committed.
- Verification for the operator is hands-on surface behavior (shadow rehearsal,
  dashboard, evidence file) — never `pytest`, `gh`, or `git` steps.
