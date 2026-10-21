## Why

The cockpit wrapped the #487 arm-record read in `except Exception` and fell back to the static preset table, and the page then read exactly like a run that had not started. A truncated or hand-edited record — what a crash mid-write leaves behind — was therefore invisible, and the operator read default numbers as live ones. Separately, each tournament's metadata carried `badge` / `badge_cls` fields that no template ever rendered.

Nothing in this change touches order placement or pricing, so it cannot move the pair cost toward $1.00 and cannot open a single unmatched buy.

## Test output

```
python -m pytest -q tests/test_tournament_cockpit_arms.py tests/test_tournament_cockpit_funnel.py tests/test_tournament_dashboard_hub.py
..............................                                           [100%]
30 passed in 0.31s
```

The twelve new tests fail against the pre-change server file (checked by stashing only `scripts/tournament_cockpit_server.py`), so each one pins the new behaviour rather than the old.

## How to verify

1. Start the cockpit (`python -m scripts.tournament_cockpit_server`) and open <http://127.0.0.1:8800/>. Under the page header there is no red strip — there is no broken record to report.
2. Stop the server, copy `runtime/tournaments/487_current_setup_10arm.json` somewhere safe, then delete its last line (the closing `}`) and save, so the JSON is truncated.
3. Start the cockpit again and open the page: a red strip appears above the metric cards reading `Tournament record failed to parse: <full path>: Expecting ... line N column M. The table shows default arms, not live data.` The arm table itself still renders.
4. Leave the tab open for ~10 seconds. The strip stays put across the 5-second stats poll instead of blinking away.
5. Put the saved record back and save it. Within ~5 seconds the strip disappears on its own, without a page reload.
6. Check the nav at the top: `CURRENT SETUP` appears once, on the #487 pill, and nowhere else.

Closes #754
