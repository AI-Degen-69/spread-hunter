# Quality Constraints: Issue #359 (Order Lifetime & Cancel-Reason Mix)

## Boundaries & Quality Gates

1. **Zero regressions on existing reporting & testing:**
   - Existing single-cycle and multi-cycle tests in `tests/test_statistics_report.py` must pass untouched.
   - Existing keys returned by `write_statistics_report` (`db_path`, `run_id`, `mode`, `report_path`, `verdict`, `gate_rows`, `fills`, `quotes`, `measured_quotes`, `unmeasured_quotes`, `median_queue_multiple`, `max_queue_multiple`, `distinct_cycles`) must remain unmodified.
   - Existing lines in the Markdown output (including `## Queue depth (shadow)`, `SINGLE_CYCLE_LINE`, disclaimer lines) must remain intact.
   - When `mode == "live"`, lifetime and cancel-reason keys must NOT be added to the returned dictionary or the Markdown report.

2. **No new external dependencies:**
   - Use Python standard library only (`statistics.median`, `math`, `re`, `pathlib`, etc.). No new packages.

3. **Strict calculation & data integrity:**
   - `lifetime_s = (last_polled_ts - posted_ts) / 1000.0`.
   - Return `None` (invalid) if either timestamp is None/non-numeric/non-finite, or if `last_polled_ts < posted_ts`.
   - Only terminal statuses `cancelled` and `filled` contribute to measured lifetimes.
   - Non-terminal statuses (`open`, `pending`, `partial`, `unattributed`), and terminal orders with invalid/negative delta, are counted as `lifetime_unknown_orders` and tracked in `lifetime_unknown_by_status`. Never treat unknown lifetime as 0.
   - Cancel reason breakdown groups only `cancelled` orders. Reasons that are `None`, empty `""`, or whitespace-only group into `(no reason recorded)`.
   - In SQLite queries / connections: use existing connections safely; do not close or break connection contexts.
   - Do not query production `data/orders.db` destructively. Read-only scratch copies for any manual verification.

4. **Anti-cheat:**
   - No `@pytest.mark.skip` or commented-out assertions.
   - No mock bypasses of the underlying calculation.
   - One runnable targeted test file (`tests/test_statistics_report.py`) verifies all new keys and markdown lines.
