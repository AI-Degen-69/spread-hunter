# CONSTRAINTS.md — Issue #392 (prefer live competitive markets in selection)

## Zero regressions
- Focused suites named per task (`tests/test_rank_score.py`,
  `tests/test_filter_markets_publish_json.py`, `tests/test_velocity_gate.py`,
  `tests/test_movement_gate.py`, `tests/test_pre_start_gate.py`) must pass after
  every change (focused suites only; the full `pytest -q` suite is the GitHub CI
  merge gate, not a local loop).
- New behavior needs tests that fail without the change (RED first, then GREEN).
- Rows without the new signal must rank exactly as today (byte-identical order).

## Anti-cheat
- No skipping, disabling, or weakening existing tests or assertions.
- No linter suppression, no `type: ignore`, no silent `except: pass` in new code.
- No threshold edits to make red green (gates, bars, and bands stay as configured).

## Boundaries
- No new dependencies. No `config.py` changes (named constants in
  `scripts/filter_markets.py`).
- `data/orders.db` is production: read-only, never rewritten.
- `core_brain/` untouched — the quoting band must stay aligned with selection.
- Paired-depth/admission trial paths untouched (shipped ranking only).
- No new venue calls in the rank path (measured fields only).
- Maker-queue gating belongs to #393 and is out of scope.
- Selection gates (movement, velocity, depth, spread, pre-start, decided-mid)
  keep refusing exactly as today; only the ranking order changes.
