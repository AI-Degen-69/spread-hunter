# Build checklist — Issue #881

Branch: `i881/improve-align-spread-hunter-menu-script-for-pwd-aw`

## T1 — Checkout selection (`Resolve-ProjectRoot`) [Code] S
- [x] Resolver added, `$ProjectPath` set from it, timebox launches pinned, comment fixed
- [x] Header/`status` checkout line + mixed-checkout note
- [x] `tests/test_menu_project_root.py` (6 behavior tests + `$PSScriptRoot`-once static test)
- [x] Focused run green

## T2 — Ownership + dashboard identity [Code] M
- [x] `Test-PathInCheckout`, `Get-CommandLineCheckoutOwner`, `Get-DashboardOwnership`
- [x] Wiring: adoption gate, 4 status labels, `Stop-BotStack` gate, predicate swaps
- [x] `tests/test_menu_checkout_ownership.py` + `tests/test_menu_dashboard_identity.py`
- [x] Focused run green, existing menu suites untouched and green

## T3 — Force stop + wiring + docs [Code] M
- [x] `Stop-WorktreeProcessesForce` (order, PID-reuse guard, rescan, 3-list report)
- [x] `x` wiring verified unused; option 7 preserved; menu text + README rows
- [x] `tests/test_menu_force_stop.py` (9 assertions on PIDs 101–110)
- [x] Focused run green; operator hands-on checks (status-first, no live trading)
