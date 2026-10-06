# CONSTRAINTS.md — Issue #390 (hold resting orders through transient no-intent cycles)

## Zero regressions
- `tests/test_trader_loop.py` and `tests/test_shadow_run.py` must pass after every change
  (focused suites only; the full `pytest -q` suite is the GitHub CI merge gate, not a local loop).
- New behavior needs tests that fail without the change (RED first, then GREEN).
- Token-rotation cancel and dropped-market cancel are pinned by tests and must keep working.

## Anti-cheat
- No skipping, disabling, or weakening existing tests or assertions.
- No linter suppression, no `type: ignore`, no silent `except: pass` in new code.
- No threshold edits to make red green (grace constant set by design, not tuned to pass).

## Boundaries
- No new dependencies. No config value changes.
- `data/orders.db` is production: read-only, never rewritten.
- `core_brain/quotes.py` decision logic untouched (the classifier only reads `why` strings).
- `_cancel_dropped_markets` untouched (dropped-market path is out of scope to change).
- No new `LiveFleetResult.status` values and no new `CANCEL_*` reasons (dashboard compat;
  grace expiry reuses `not_quoted`).
- Placement gates, post-fill management, and live runs are out of scope.
