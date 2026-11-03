# Quality Guardrails & Constraints

## Context
- **Branch:** `plan_github_issue_926`
- **Issue:** `#926`

## Quality Guardrails
1. **Zero Regressions & Targeted Verification:**
   - All modified modules must have 100% passing targeted test suites:
     - `python -m pytest -q tests/test_single_leg_lifecycle.py tests/test_quotes.py tests/test_single_buy_saver.py`
   - Zero test skips or deleted assertions.
2. **Anti-Cheat & Strict Invariants:**
   - Never disable or skip tests to achieve green results.
   - Do not bypass linter errors or suppress type checks.
   - Invariants:
     - Peak watermark `peak_bid` is strictly non-decreasing: `peak_bid_t >= peak_bid_{t-1}`.
     - Pair cost invariants are strictly preserved for dual-filled positions.
     - Maker quotes must never exceed `bot_pos_price_max_entry_usd`.
3. **Dependencies:**
   - Strictly zero new external dependencies. Standard library and existing project dependencies only.
4. **Safety & Venue Protection:**
   - Production registry `data/orders.db` must never be modified or overwritten during testing or rehearsal.
   - Live orders are never dispatched unless explicitly authorized by the operator.
   - Shadow rehearsals must use isolated database paths (e.g. `--db data/NN_shadow_<stamp>.db`).
