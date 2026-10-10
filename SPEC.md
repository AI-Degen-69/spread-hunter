# SPEC — #472: Multi-arm shadow tournament (profile comparison)

## Goals

1. One command runs all five quote-placement profiles (control, conservative,
   balanced, aggressive, prudent) as isolated shadow arms on the same market
   list, each with its own scratch DB, run id, and dashboard port.
2. Each default arm actually applies its full named profile, including the
   shared queue-hold settings — not just the offset fields.
3. After the workers finish, one results file in `reports/tournaments/` records
   per-arm fill/close counts for later comparison. No winner is named; the
   stat gate (#471) decides that later.

## Acceptance criteria

- `--dry-run` prints the tournament plan JSON (five arms, ports 8801–8805,
  five distinct DB paths, `results_path`) with no error.
- The live run launches each arm in its own shadow DB with its own dashboard
  port; dashboards show the SHADOW badge and separate databases.
- Per-arm fill/close counts (fill events, filled orders, close events, merges,
  exits, modeled P&L, status, exit code) are recorded in one results JSON.
- Runnable: `python -m scripts.shadow_tournament --minutes 15 --dry-run`
  then `python -m scripts.shadow_tournament --minutes 15 --dashboards`.

## Edge cases (from issue + code analysis)

- Today's default list has four arms and omits prudent; the bare acceptance
  command has no `--arms-file`, so the default list itself must gain prudent.
- `_preset_to_env` copies only offset fields and drops the shared queue-hold
  settings (`requote_hold_queue_shares` 500.0, `requote_hold_below_target`
  0.08); without the `HUNTER_TOURNAMENT_PRESET` selector the default arms do
  not match the presets they are named after.
- "The same live books": each arm fetches its own books at its own moments.
  Only the market *list* can be shared (frozen `--markets-path` file); the
  results note records the timing skew openly.
- Inherited `HUNTER_*` env vars (or `.env`) override a profile silently —
  the run instructions require a clean-shell check first.
- `--dry-run` does not check port availability; only the live run does.
- Dashboards stop when the run ends — check them during the run, never
  press START on them.
- Interrupted runs may skip the results step; per-arm DBs still exist.
- A 15-minute run is short and fills are modeled — close counts are not a
  statistical sample size.

## Out of scope

- Editing `TOURNAMENT_PRESETS` or `dynamic_offset_for` — presets frozen.
- Choosing a production winner (needs the #471 stat gate).
- Any LIVE execution, any change to the strategy itself.
- `data/orders.db` production registry (tournament uses isolated shadow DBs).
