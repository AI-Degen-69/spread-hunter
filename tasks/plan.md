# CONSTRAINTS.md — Issue #229

## Scope
`tape_findings` (viewer layer) discloses the grid's own age and the tape coverage
at compute time; `/tape` page separates "when computed" from "when polled",
renders a STALE state, and names a stopped collector.

## Hard constraints
- Read-only viewer; nothing recomputes `analyse` from the page.
- Thresholds (`SIGNIFICANCE_T`, `DEFAULT_CELLS`, `MIN_CELL_SAMPLES`) unchanged.
- `data/orders.db` refused by name at every layer (unchanged).

## Tasks
1. TDD red: failing tests in `tests/test_tape_view.py` for age, staleness
   (by new ticks, by age, fresh), and not-collecting.
2. Implement in `core_brain/tape_view.py`: `tape_findings` gains
   `computed_at_h` (ISO), `grid_age_hours`, `ticks_since_computed`,
   `computed_over_tape_ticks`, `last_tick_ts_h`, `stale` (bool) + `stale_reason`.
3. `dashboard/static/tape.html`: `#refreshed` becomes poll-only; new
   `#grid-meta` line renders computed-time + age; STALE pill state;
   coverage tile note "not collecting" when newest tick is old.
4. Tests green; full suite green.
