Branch: i730/record-stop-slippage | Issue: #730

# Constraints — spread-hunter measurement change #730

## Hard scope (measurement only)

- No behaviour change: selling paths, FOK submission, requested sizes,
  `_exit_fill_price` / `_exit_fill_size` fallbacks, `MAX_SELL_SLIPPAGE`,
  reason labels, and reset logic stay byte-for-byte in behaviour. A change
  that alters any execution behaviour is out of scope — stop and split it.
- No new venue call, no new order path, no threshold moved.

## Money-accuracy rules

- Unknown is never zero: unmeasured counts and `None` means stay `None`/
  explicit, never defaulted with `x or 0.0` / `get(..., 0)` on measurement
  fields.
- A fill at the floor reports `0.00` slippage (`is not None`), not unknown.
- Old rows are never backfilled; NULL `submitted_floor`/`fill_source` reads
  as `historical` unmeasured.
- Rehearsal figures are labelled rehearsal results, never venue results.
- All summary floats go through `_finite()`; payload survives
  `json.dumps(..., allow_nan=False)`.

## Zero regressions (targeted only locally)

- Every changed behaviour is covered by a test in
  `tests/test_stop_slippage.py` (or the named existing suite) that fails
  without the change.
- Touched-module suites during work: the per-task Verify lines in
  `tasks/plan.md`. The full `python -m pytest -q` suite is the GitHub CI
  merge gate (Ubuntu + Windows) — never repeated locally to validate work.
- Execution-unchanged proof suites that must keep passing unmodified:
  `tests/test_hard_stop_fresh_exit.py`, `tests/test_dual_stop_loss.py`,
  `tests/test_exit_price_realism.py`.

## Anti-cheat

- No skipping, renaming, or deleting tests; no deleting assertions; no
  widening tolerance until a test passes; no `pytest.ini`/conftest hacks
  that silence checks; no `# noqa`/`type: ignore`/`@ts-ignore` added to
  bypass a failed gate; thresholds are never edited down to go green.
- The 9 mechanical `_sell_into_bids` unpack edits in test files change no
  assertion — reviewed as such.

## Dependencies

- No new external dependency (stdlib argparse/sqlite3 only).

## Database rules

- `data/orders.db` is the production registry: read-only from the CLI
  (`mode=ro`, no `OrderRegistry`); never rewritten or deleted by this work.
- `hard_stop_refusals` is append-only: rows are added, never edited, and the
  table is excluded from every delete/reset path.
- `closes` gains only nullable columns; the migration must be idempotent and
  safe on repeat init.

## Version discipline

- `KPI_PAYLOAD_VERSION` bump 259→260 lands on BOTH sides (backend + page
  const) with the pinned test literal updated, in the same commit(s) as the
  new section.
