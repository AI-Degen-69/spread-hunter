fix(dashboard): drop hardcoded spread suffix from parameter cards (#705)

Closes #705

## Why

The parameter cards in `dashboard/server.py` built their value strings with
hardcoded literal suffixes that only restated the shipped default in another
unit. `max_spread_from_mid`, for example, rendered
`f"${cfg.max_spread_from_mid:.2f} (15¢)"`, so a configured 0.20 produced the
self-contradicting `$0.20 (15¢)`. The same "restated default" pattern existed at
15 sites (30m/2h/15m/10¢/90¢/1¢/6¢/15¢/1.5¢/0.5¢/3¢/5¢), so this fixes the class
behind one rule: strip every suffix that merely restates the default; keep
literals that carry information the bare number does not (`(50% of spread)`,
`(Telemetry Only)`). `default`, `raw_value`, config loading, quoting logic and
the frontend renderer (`app.js` renders the server strings verbatim) are
untouched, as are all money-path configuration values.

Test changes: the `"1800s (30m)"` pin at `tests/test_dashboard_server.py` was
pinning exactly this stale format and is repinned to `"1800s"` with a comment;
one exact-string regression test added for the `max_spread_from_mid` card.

O1 in `docs/issues/705-noticed-but-not-touching.md`.

## Test output

- RED proof: with the ` (15¢)` literal re-applied,
  `test_max_spread_from_mid_card_shows_configured_value_exactly` fails with
  `assert '$0.20 (15¢)' == '$0.20'`; green after the fix.
- Focused suite: `python -m pytest -q tests/test_dashboard_server.py`
  → 138 passed.

## How to verify

1. Start the dashboard (`.\scripts\spread-hunter-menu.ps1 start`, SHADOW badge
   visible — no Trader, no START button, nothing spent).
2. Open http://127.0.0.1:8799 → Parameters tab.
3. Find the "Max Resting Spread From Mid" card.
   - Expected: the value shows only the configured dollar amount (with the
     shipped setting: `$0.15`, and "Default: $0.15").
   - What a failure looks like: any `… (15¢)` suffix after the dollar value, or
     a suffix (e.g. `(6¢)`, `(30m)`) hanging off other cards it does not match —
     e.g. set `BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID=20`, restart only the
     dashboard, and the card must read `$0.20`, never `$0.20 (15¢)`.
4. Sanity: the "Half-Spread Offset Multiplier" card should still read `0.50 (50%
   of spread)` — that literal is information, not a restatement.
