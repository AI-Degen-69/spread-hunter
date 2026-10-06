# Constraints & Quality Guardrails — Issue #365

Branch: `i365/prune-stale-local-stores-in-data-runtime-and-repor` | Issue: #365

## Quality & Tests
- **Zero regressions**: `python -m pytest -q tests/test_data_retention.py` must
  pass cleanly before and after every task.
- **New behavior requires tests**: cwd-independence and nested-runtime tests
  must each fail without their change (RED) and pass with it (GREEN).
- **Anti-cheat**: No skipped tests, no deleted assertions, no suppressions or
  linter silencing.
- **No new external dependencies**: Python standard library only; reuse
  `core_brain/runtime_paths.LIVE_ROOT`.

## Safety & Invariants (must survive any change)
- **Production registry is untouchable**: `data/orders.db` plus `-wal`/`-shm`
  is strictly refused by `assert_not_protected_store()` and can never be
  deleted — including via nested-scan paths, forged audit items, `rmtree`
  descendants, or sibling cleanup.
- **`data/price_tape.db` excluded** from deletion; `01_shadow`-style names stay
  user-protected at classification level.
- **Dry-run is the default**: real deletion requires explicit opt-in; the real
  `--no-dry-run` prune on the operator tree is operator-gated — the agent runs
  dry-run + audit only, never live deletion on the working tree.
- **Never open `data/orders.db` for writing**; test fixtures use `tmp_path`
  only.

## Performance
- Audit must complete in seconds on the live tree (single recursive walk, no
  double counting from aggregate dir items).
