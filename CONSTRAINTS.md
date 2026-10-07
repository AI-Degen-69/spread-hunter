# CONSTRAINTS.md — Issue #393 (refuse quotes resting behind an unfillable queue)

## Zero regressions
- Focused suites named per task (`tests/test_queue_clear_gate.py`,
  `tests/test_maker_queue_bar.py`, `tests/test_trader_loop.py`,
  `tests/test_market_quote.py`, `tests/test_shadow_run.py`,
  `tests/test_plan_orders_asymmetric_hold.py`) must pass after every change. Focused
  suites only — the full `pytest -q` suite is the GitHub CI merge gate, not a local loop.
- New behaviour needs tests that fail without the change (RED first, then GREEN).
- A market whose queue ahead is zero must place exactly as today, and a visit with no
  passive intent (or `flow_fn` unset) must call no new code path at all.

## Anti-cheat
- No skipping, disabling or weakening existing tests or assertions.
- No linter suppression, no `type: ignore`, no silent `except: pass` in new code.
- No edits to bars, caps or gates to make a red test green — the 60-minute bar is a named
  starting value, not a dial to tune tests.

## Boundaries
- No new dependencies.
- `data/orders.db` is production: read-only, never rewritten. No live command is run.
- Untouched: `core_brain/quotes.py` and `decide_quotes`, `scripts/filter_markets.py` and
  its inert `queue_bar_reject`, `scoring/`, the shadow run and the shadow seam.
- The gate applies to **new resting placements only**. Held orders, `intents`, `why`,
  `visit_outcome`, the refusal-grace counter and `cancel_queue_ahead` recording stay
  exactly as they are.
- Crossed (FOK) intents never rest and are never gated.
- An unmeasurable tape (`unavailable` / `truncated`) must FAIL OPEN and say so in the
  reason; it must never refuse a placement.
- Record-only is the shipped default (`enforce_queue_clear_gate = False`): the reason is
  reported, nothing is dropped, until `HUNTER_QUEUE_CLEAR_GATE=1`.
- A refused visit never places a single leg of a fresh couple: a passenger leg is dropped
  with its partner.
