# Todo — #705

## Task 1 — Strip literal suffixes from parameter card values (server.py)
- [ ] Remove ` (30m)` at 4319, ` (2h)` at 4387, ` (10¢)` at 4684, ` (90¢)` at 4701,
      ` (1¢)` at 4805, ` (6¢)` at 4841, ` (15¢)` at 4858, ` (1.5¢)` at 4984,
      ` (0.5¢)` at 5001, ` (1¢)` at 5020, ` (3¢)` at 5076, ` (5¢)` at 5093,
      ` (15m)` at 5180, ` (15m)` at 5214, ` (5¢)` at 5250.
- [ ] Leave `(50% of spread)` (4754) and `(Telemetry Only)` untouched.
- [ ] Do not touch `default`, `raw_value`, `trigger`, `action`, quoting logic, config loader, or frontend.

## Task 2 — Tests (test_dashboard_server.py)
- [ ] Repin 3259: `"1800s (30m)"` → `"1800s"` with a comment naming why the old pin was wrong.
- [ ] Leave 3245 (`"$777.00"`) and 3262 (`"False (Telemetry Only)"`) untouched.
- [ ] New test: `max_spread_from_mid` card with `BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID=20`
      → raw 0.20, value exactly `"$0.20"`, default `"$0.15"`.

## Task 3 — Verify
- [ ] RED/GREEN: new test fails before suffix strip, passes after.
- [ ] Focused suite: `python -m pytest -q tests/test_dashboard_server.py`.
