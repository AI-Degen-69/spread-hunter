# Backlog — deferred by #840 (out of scope, do not build in this issue)

Recorded during Station II planning. Each item needs its own issue before any
build station touches it.

1. **Trader-loop rollback semantics** (`trader_loop.py` `_submit_intents`):
   surviving orders marked cancelled when the cancel call fails, venue IDs
   dropped. Needs a dedicated correctness issue + failing test first.
2. **Lenient cancel-response handling** (`_cancel_orders`, `cancel_all_orders`
   treat any non-raising response as success). Needs a contract issue.
3. **Menu launcher preset gap** (`scripts/spread-hunter-menu.ps1` strips
   `BOT_QUOTE_TOURNAMENT_PRESET` for fresh live Trader/dashboard, restores it
   only for shadow). Live may run without `prudent`. Needs a launcher issue.
4. **Dropped dynamic-offset telemetry** (production `_market_specs` drops
   `range_cents` / `velocity_measured_at`, forcing static-offset fallback in
   both lanes). Needs a market-feed issue.
5. **Venue client timeouts** (command-line client calls have no timeout; a hung
   post can delay the cancel past the 5s bound). Needs a reliability issue.
6. **Reconciliation guess risk** (pending-row ↔ unknown venue order matched on
   token/price/size; fully-filled-before-seen orders become `UNMATCHED_TRADE`
   with the row stuck pending). Needs a registry-matching issue.
7. **`is_approved_for_all` read-only check** (unconfirmed whether any command
   reports conditional-token approval needed for sell/merge). If none exists,
   needs a pre-flight observability issue.
8. **Relayer health proof** (accepted orders prove signing/balance/allowance,
   not gas-free relayer health for merges). Needs an ops-runbook issue.
