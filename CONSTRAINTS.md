# CONSTRAINTS — #402 (locked by Station II, enforced through Station V)

Supersedes the #401 constraints (merged work).

## Zero regressions

- These suites must pass after every change touching their modules:
  `tests/test_trader_loop.py`, `tests/test_shadow_run.py`,
  `tests/test_market_resolution.py`, `tests/test_aged_out_rescue.py`,
  `tests/test_markout_maturity.py`, `tests/test_live_event_discovery.py`,
  `tests/test_in_play_gate.py`, `tests/test_live_e2e_lifecycle.py`,
  `tests/test_dynamic_risk_caps.py`, `tests/test_completable_pair_gate.py`.
  (`test_dynamic_risk_caps.py` + `test_completable_pair_gate.py` prove the
  money controls did not move.)
- Full-repo sweep stays with CI on push; locally run only the focused suites.
- Every new behavior needs a test that FAILS without the change (RED first,
  per `test-driven-development`). Book access in zero-request tests must be
  countable (counter stub) or raise — network blocking alone proves nothing.

## Money-lever freeze

- Unchanged: dynamic caps (`derive_dynamic_caps`), `max_pair_cost`, band
  values ([0.20, 0.80], 0.02/0.98, 0.10–0.90), countdown, pair-cost gate,
  `hard_block`, completable-pair gate, enforce flags, live merge (manual).
- Refusal strings in `quotes.py` and `orders.cancel_reason` values unchanged.
- `live_fill_engine.py` untouched — late authenticated fills still record.

## Stores are evidence, not scratch

- `data/orders.db` is the production registry: read it, never rewrite it.
- No schema migration: `resolutions` table and `market_events.reason_code`
  already exist — reuse them.
- Do NOT run opening commands (`quote`, `complete`, Trader loop, dashboard
  START) to verify — propose them, operator runs them. Shadow rehearsals in
  tests use temp DBs only.

## Anti-cheat

- No skipping/disabling tests, no deleting assertions, no linter suppression.
- No new external dependencies without explicit operator approval.
- A 404 alone never resolves a market; unknown/stale series evidence fails
  closed (band applies as today).

## Performance

- `resolved_condition_ids` loads once per rotation/cycle, not per market.
- Dead-book backoff: bounded waits per token, cleared on success; at most one
  market-state check per backoff window.
