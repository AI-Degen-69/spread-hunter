# SPEC: Issue #356 — Fix cycle_intent.submitted accounting gap & telemetry visibility

## Goal
Identify and close the submit-accounting gap where `cycle_intent.submitted` reads 0 while orders post. Add visibility into unmatched updates and missing DB paths, preserve partial-submission counts on errors, and document the true semantics of `cycle_intent.submitted`.

## Acceptance Criteria
- [ ] In `core_brain/cycle_stream.py::_update_cycle_intent`, warn on stderr when `db_path` does not exist or when the UPDATE matches 0 rows (`cur.rowcount == 0`), reporting `market_slug`, `cycle`, and `run_id`.
- [ ] In `core_brain/trader_loop.py` and `core_brain/shadow_exec.py`, define and use a shared partial-count attribute (`PARTIAL_SUBMIT_PLACED_ATTR`) so that when submission encounters an error after placing legs, the placed count is preserved on the exception and read in `_visit_one` during `market_error` emission and `LiveFleetResult` return.
- [ ] Add unit test in `tests/test_cycle_stream.py` verifying the unmatched-update warning on stderr using `capsys`.
- [ ] Add integration test in `tests/test_run_attribution.py` testing the real shadow posting boundary and verifying that partial-submission failure records `submitted > 0` in `cycle_intent` and `market_error`.
- [ ] Document the exact semantics in `core_brain/cycle_stream.py` (table comment and docstrings), `docs/issues/351-noticed-but-not-touching.md`, and `docs/issues/analysis-01-shadow-zero-fill.md`.
- [ ] All targeted tests pass: `python -m pytest -q tests/test_cycle_stream.py tests/test_run_attribution.py tests/test_trader_loop.py`.

## Scope
### In scope
- `core_brain/cycle_stream.py`: DB missing warning, 0-row update warning, schema comment and docstring clarification.
- `core_brain/trader_loop.py`: `PARTIAL_SUBMIT_PLACED_ATTR` definition, exception attachment in `_submit_intents`, exception extraction in `_visit_one`.
- `core_brain/shadow_exec.py`: Attach `PARTIAL_SUBMIT_PLACED_ATTR` on rollback in `record_submit`.
- `tests/test_cycle_stream.py`: Unmatched update warning test.
- `tests/test_run_attribution.py`: Real shadow submit boundary and partial submission test.
- `docs/issues/351-noticed-but-not-touching.md` and `docs/issues/analysis-01-shadow-zero-fill.md`: Discrepancy explanation and resolution.

### Out of scope
- Changing `CYCLE_INTENT_KEEP_ROWS` retention limit (kept at 200).
- Modifying production `data/orders.db`.
- Modifying order fill, lifecycle, or pricing logic.
- Changing `live/runtime/cycle_events.jsonl` ring formatting.
