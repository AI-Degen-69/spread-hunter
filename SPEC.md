# SPEC.md — Issue #881: PWD-aware worktree support and nuclear process stop

Branch: `i881/improve-align-spread-hunter-menu-script-for-pwd-aw` | Issue: #881

## 1. Goal

The PowerShell control center (`scripts/spread-hunter-menu.ps1`) becomes checkout-local:
the operator's current folder decides which spread-hunter checkout (main repo or a
worktree) the menu serves. Every stop/adopt/status path proves ownership before acting,
and a new separate force-stop action (`stop-all-force` / `force-stop` / `nuke`, key `x`)
stops every Owned process of this checkout — even ones the menu did not start — while
leaving sibling/nested checkouts alone. A hand-started dashboard becomes adoptable and
stoppable from its own checkout's menu.

## 2. Non-goals (explicit out of scope)

- No `Add-Type` memory-read helper to fetch another process's working folder, and no
  parent-PID-chain ownership check (open question C5 belongs to a follow-up ticket).
- No Python changes: `core_brain/runtime_paths.py`, `core_brain/order_registry.py`,
  `core_brain/order_manager.py`, `dashboard/server.py`, `scripts/global_stop_loss.py`.
- No change to option 7's role: `stop-all`/`stopall`/`clean`/`reset` stay mapped to `7`.
- No change to `Stop-TsBridge` and the resume stray scan (left as-is, documented as R3).
- No data writes: `data/orders.db`, `processes.json`, `markets.json`, heartbeat, locks,
  and everything under `data/` are never touched by the force stop (dashboard/session
  records removed only after every PID in the record has exited).
- No fix for the `scripts/global_stop_loss.py` `started_at`-heartbeat guardrail gap (R4,
  needs a Python change — separate ticket).

## 3. Architecture & functional components

### 3.1 Checkout selection — `Resolve-ProjectRoot -CurrentPath -ScriptRoot`

- Caller passes `(Get-Location).ProviderPath`. If that exact folder holds both marker
  files (`core_brain/order_manager.py` and `dashboard/server.py`), it wins (source
  `"current folder"`). Otherwise the parent of `ScriptRoot` wins (source
  `"script location"`). No parent-folder walk. Result normalized via `GetFullPath`,
  trailing separator stripped.
- Assigned to `$ProjectPath` before any dependent path is built (current line 51);
  `Resolve-RuntimeFile` and `Get-ServiceEntry` unchanged. `$PSScriptRoot` keeps exactly
  one use (this fallback). `Show-CheckoutIdentity` unchanged.
- The three timebox launches pin `-WorkingDirectory $ProjectPath`; the comment near
  lines 1038–1039 is corrected to cover workers, timeboxes, and browser launches.
- Header and `status` print `Checkout: <path> (from current folder|from script location)`;
  when the script's own checkout differs from the selected one, one note is printed:
  menu logic comes from the script checkout, Python code from the selected checkout.

### 3.2 Ownership — `Test-PathInCheckout`, `Get-CommandLineCheckoutOwner`, `Get-DashboardOwnership`

- `Test-PathInCheckout -Path -Root`: case-insensitive `GetFullPath` compare, equality
  or root-plus-separator prefix; then a bounded walk between path and root (never above
  root) rejects paths inside a nested checkout that carries both marker files.
- `Get-CommandLineCheckoutOwner -CommandLine -Root`: respects quoting and `--opt=value`,
  skips argv[0] (interpreter path proves nothing), collects drive-letter/UNC absolute
  paths. Owned = ≥1 path and all local; Foreign = all outside; Unknown = none or mixed.
- `Get-DashboardOwnership -Port` (read-only, `/api/system/status`): Owned only when the
  canonical `db_path` equals `<root>\data\orders.db` AND `registry_path` is inside local
  `runtime/` or `run/` AND `services.dash.pid` equals `Get-PortPid`. Foreign with the
  served path on checkout mismatch; Unknown on any error, missing field, or PID mismatch.
- Wiring: `Test-DashboardServer` additionally requires Owned (adoption flow otherwise
  unchanged; a Foreign refusal prints the other checkout's path). Status rows show
  `ON (owned)` / `LISTENING – this checkout, not adopted` / `LISTENING – other checkout:
  <path>` / `LISTENING – unknown`, and `status` never writes records. `Stop-BotStack`
  POSTs `/api/system/stop` only to an Owned listener. `Stop-ShadowRun` and the three
  `Stop-AllSpreadHunterProcesses` predicates replace the `-like "*$ProjectPath*"` test
  with `Get-CommandLineCheckoutOwner == Owned`, keeping the existing module-regex text
  byte-identical (static test `test_menu_global_stop_clean.py` pins it).
- Untouched: `Kill-RecordedPid`, `Get-DashInstance`, `Get-ProcessRecord`,
  `Stop-Dashboard`, `Stop-ShadowDashboard`, `Stop-TsBridge`, resume stray scan.

### 3.3 Force stop — `Stop-WorktreeProcessesForce` (key `x`)

- Candidates: `Win32_Process` names matching `^python` whose command line matches the
  seven-module regex or `scripts[\\/]global_stop_loss\.py`. Read each creation time once.
  Never stop `$PID`; unrelated modules (`polymarket_docs_mcp`, bare `pytest` mentions)
  are excluded by the regex.
- Ownership per candidate: (a) command-line owner, (b) PID in this checkout's
  `processes.json` (via `Get-ServiceEntry`, 60s `started_at` rule) or in a dashboard /
  session record with present, exactly-equal start ticks (a record without start ticks
  proves nothing), or (c) dashboard-port owner with Owned identity. Foreign command line
  is Foreign unless a local record conflicts → Unknown. Anything else → Unknown, left
  running. Missing/unreadable evidence is Unknown, never Owned.
- Kill order: `dashboard.server` first, `core_brain.order_manager` second, rest after.
  Re-verify creation time immediately before each kill (PID-reuse guard); `taskkill
  /T /F`, then `Wait-ProcessGone`; exactly one rescan for late arrivals.
- Report prints three lists (`stopped` / `left running – other checkout` / `left running –
  could not prove ownership`) plus the exact warning `Stopping processes does not cancel
  resting venue orders.` Success = every Owned target exited.

### 3.4 Wiring & docs

- Action map: `stop-all-force`, `force-stop`, `nuke` → key `x` (verified unused:
  `$script:MenuKeys` lists only `1 2 3 4 5 6 7 8 9 r a p c t q`). `Invoke-LiveAction`
  dispatches `x` to the force stop only; interactive asks yes/no, command-line follows
  option 7's confirmation rule. README actions table documents the aliases, the
  exact-folder checkout rule, and what stays running.

## 4. Acceptance criteria

1. From a worktree folder, the menu header/`status` shows the worktree path
   `(from current folder)`; running main's script by full path from the worktree shows
   the worktree plus the mixed-checkout note.
2. Main's `status` labels a worktree dashboard `LISTENING – other checkout: <path>` and
   writes no adoption record; main's `stop` never POSTs to it.
3. `stop-all-force` in a worktree stops that worktree's adopted dashboard and recorded
   workers and lists main's processes as left running; afterwards main's `status` is
   unchanged.
4. No existing test file is modified; all listed regression suites still pass.

## 5. Edge cases

- Relative-only command lines (e.g. `python -m dashboard.server --port 8799`) → Unknown,
  keeps running; the operator must adopt via `host`/`open` first (accepted gap, C5).
- Mixed local/foreign absolute paths in one command line → Unknown.
- PID creation time changed between discovery and kill → skip, report (recycled PID).
- `--reload` dashboard whose reported PID differs from the port owner → Unknown, refused.
- A `--db` override pointing outside the checkout with a local registry → not Owned.
- Case/`/`/`..` variants normalize to the same checkout (no false Foreign).

## 6. Ticket open questions — resolutions adopted here

- Exact-folder check only, no parent walk (ticket default; subfolder falls back).
- Keep option 7, add separate action (ticket default).
- Narrow the existing stop paths that can reach sibling checkouts (required for
  criterion 3 — each change only narrows the match).
- Dashboard identity = `db_path` + `registry_path` + port-owner PID equality (stronger
  than the ticket's `db_path`-only default; `db_path` alone is overridable).
