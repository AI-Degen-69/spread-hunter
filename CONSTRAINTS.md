# CONSTRAINTS.md — Issue #657: Differential live_marks subscription frames

Locked in Station II (`ii-plan-issue`) on 2026-10-18. The build (Station III)
must respect every line below. Changing a constraint requires an explicit
operator decision, recorded at the bottom of `tasks/plan.md`.

## 1. Zero regressions

- Targeted suite `tests/test_live_marks.py` must pass after the change.
- Every new behaviour needs a test that fails without the change (baseline
  RED written FIRST against unmodified `sync_subscription`).
- The full `python -m pytest -q` suite is the GitHub CI merge gate, not a
  local loop.

## 2. Shipped behaviour frozen except the sync path

- Only `LiveMarkWorker.sync_subscription` (+ its docstring + one private frame
  helper in `live_marks.py`) and `tests/test_live_marks.py` move.
- Frozen: `subscribe_payload`, `_send_subscribe`, `_run_venue` reconnect loop,
  `LiveMarkCache` mark math, `WANTED_TTL_SEC`/`WANTED_SETTLE_SEC`, dashboard
  render, `market_stream.py` (read-only reference).

## 3. Anti-cheat

- Forbid skipping/disabling tests, deleting assertions (except the deliberate
  drift-test rewrite recorded in the plan), weakening tests, or adding
  lint/type suppressions to get green.
- The delta tests must assert real wire frames (`assets_ids` + `operation`,
  sorted ids, subscribe-before-unsubscribe) — never assert against a copy of
  the implementation.

## 4. Dependencies and blast radius

- No new external dependencies. Existing `websocket-client` only.
- No touching `data/orders.db`. No live trading, no order-opening commands.
- Send-failure path catches only `OSError` + `websocket.WebSocketException`
  (matches `_run_venue`'s local-import style); any other exception propagates.
- No worker lock added; no cross-module import from `market_stream.py`.

## 5. Scope ceiling

- Reconnect stays strictly for transport drops / heartbeat / socket errors.
- A differential sync never calls `disconnect()`, never drops cache state,
  never bumps `reset_generation`.
- Venue acceptance of `operation` frames stays flagged [UNVERIFIED-in-repo]
  until a live probe re-confirms on this path; the PR states this.
