# CONSTRAINTS — #408 (locked by Station II, enforced through Station V)

Supersedes the #402 constraints (done work).

## Zero regressions

- These suites must pass after every change touching their modules:
  `tests/test_uma_resolution_gate.py`, `tests/test_trader_loop.py`,
  `tests/test_shadow_run.py`.
  (`test_uma_resolution_gate.py` proves the reader; the other two prove the
  visit sequence and the rehearsal.)
- Full-repo sweep stays with CI on push; locally run only the focused suites.
- Every new behavior needs a test that FAILS without the change (RED first,
  per `test-driven-development`). Gamma reads in tests use an injected
  `urlopen` fake — no test touches the network.

## Money-lever freeze

- Unchanged: dynamic caps (`derive_dynamic_caps`), `max_pair_cost`, band
  values, countdown, pair-cost gate, `hard_block`, completable-pair gate,
  enforce flags, selection-time UMA gate (#377), live merge (manual).
- `plan_orders`, ladder routing, registry pair merging, `single_buy_saver`,
  settlement, inventory, fills, closes, merges — untouched.
- Existing cancel-reason values unchanged; only ADD the three
  `uma_resolution_*` reasons.

## Stores are evidence, not scratch

- `data/orders.db` is the production registry: read it, never rewrite it.
- No schema migration: `market_events` + `log_market_event` already exist —
  reuse them (event type `BLOCKED`, reason code + UMA status in details).
- Do NOT run opening commands (`quote`, `complete`, Trader loop, dashboard
  START) to verify — propose them, operator runs them. Shadow rehearsals in
  tests use temp DBs only.

## Anti-cheat

- No skipping/disabling tests, no deleting assertions, no linter suppression.
- No new external dependencies without explicit operator approval.
- A Gamma outage alone never cancels a quote (fail open); an unparseable row
  is unreadable, never "clean" and never "flagged".
- The new reader must match the requested condition id; first-row blind
  reads (today's `fetch_open_market_state:414` shape) are banned here.

## Performance

- At most one Gamma UMA read per condition per 30s TTL; flagged results
  pinned (no re-read); unreachable results never cached.
- ~5s timeout on the Gamma read so a slow response cannot stall a cycle.
- Clean markets pay one cached read per TTL and otherwise behave exactly as
  today (no extra fetch, no extra cancel, no extra log line).
