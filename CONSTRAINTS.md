# CONSTRAINTS — #417 (locked by Station II, enforced through Station V)

Supersedes the previous issue plan constraints for this branch only.

## Zero regressions

- Focused suites that must pass for the touched behavior:
  `tests/test_shadow_fills.py`,
  `tests/test_shadow_exec.py`.
- Full-repo sweep stays with CI on push (Ubuntu + Windows); locally run only the focused suites.
- Every behavior change needs a test that fails without the change (RED first, per `test-driven-development`).

## Fill-model semantics freeze

- Exact-price queue-first rule unchanged: tape at the order's own rounded price
  consumes `queue_ahead` first, then fills `min(volume, remaining)`, oldest-first
  sharing at the same level.
- Trade-through fills at the order's OWN price, never at the print price.
- At most one fill per order per `credit_fills` call — the caller
  (`shadow_exec.py:settle_market`) derives status from `order.filled + f.size`.
- Lower-bucket evidence is not consumed: one print can fill several orders above it.
- `live_fill_engine.py` untouched — live, a fill exists only when the venue says so.
- `markets.py:recent_trades` and the `traded` shape (`token -> price -> volume`) unchanged.
- Telemetry comment at `shadow_exec.py:585` unchanged.

## Stores are evidence, not scratch

- `data/orders.db` is the production registry: read it, never rewrite it.
- No schema migration, no database rewriting; tests use temporary DBs.
- No live orders are opened or completed; no `quote`, `complete`, Trader loop,
  or dashboard START. The only order-loop validation is a direct `python -c`
  call that spends nothing.

## Anti-cheat

- No skipping/disabling tests, no deleting assertions, no suppressing lint or type checks.
- No new external dependencies without explicit operator approval.
- No new helper modules; `math` is already imported in `shadow_fills.py`.
