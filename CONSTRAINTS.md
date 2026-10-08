# CONSTRAINTS — #416 (locked by Station II, enforced through Station V)

Supersedes the previous issue plan constraints for this branch only.

## Zero regressions

- Focused suites that must pass for the touched behavior:
  `tests/test_velocity_gate.py`,
  `tests/test_movement_gate.py`,
  `tests/test_queue_clear_gate.py`,
  `tests/test_cancel_attribution.py`,
  `tests/test_trader_loop.py`.
- Full-repo sweep stays with CI on push (Ubuntu + Windows); locally run only the focused suites.
- Every behavior change needs a test that fails without the change (RED first, per `test-driven-development`).

## Gate semantics freeze

- Fail-open preserved: unmeasured tape (`None`) passes; tape-read failure passes with the reason recorded, never silent.
- Crossed (fill-or-kill) legs stay ungated; the couple-is-never-split rule stays.
- Dual-resting markets keep full gate behavior; bypass fires only on registry lifecycle state `ESCALATED_HEDGE` / `HARD_STOP`.
- Resolved markets still exit resting quotes first; protection never overrides resolution.
- Pair-cost ceilings, gate telemetry columns, venue-state / cancel / reconcile / sell-risk paths: untouched.

## Stores are evidence, not scratch

- `data/orders.db` is the production registry: read it, never rewrite it.
- No schema migration, no database rewriting; tests use temporary DBs.
- No live orders are opened or completed in this station; the only order-loop validation is a shadow rehearsal to its own DB.

## Anti-cheat

- No skipping/disabling tests, no deleting assertions, no suppressing lint or type checks.
- No new external dependencies without explicit operator approval.
- Thresholds move only with documented rationale in the code comment; env overrides (`HUNTER_MIN_MOVEMENT_USD`, `HUNTER_MIN_RANGE_CENTS`) stay working.

## Performance

- The hedge bypass must not add per-cycle venue reads: bypassed admissions skip the tape read; dropped-market protection queries lifecycle state only on the rare dropped-market path.
- No extra per-market book fetches or network reads on the hot path.
