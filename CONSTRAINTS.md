# CONSTRAINTS.md — Issue #881: PWD-aware menu checkout + force stop

Branch: `i881/improve-align-spread-hunter-menu-script-for-pwd-aw` | Issue: #881

Locked during Station II planning. Stations III–V must hold every line below.
Tighten only with operator approval; never loosen silently.

## The one invariant this change exists to protect

**A menu never touches a process or dashboard that belongs to another checkout.**

`Unknown` means leave it running. `Foreign` means name the other checkout's path and
stop. Ownership needs positive proof (command-line paths, matching local records with
exact start ticks, or an Owned dashboard-port identity) — never the absence of contrary
evidence.

## Zero regressions

- Targeted suites covering the touched area must pass cleanly (harness shells out to
  `pwsh`/`powershell` and skips when neither exists — a skip is reported, never faked):
  - `python -m pytest -q tests/test_menu_project_root.py tests/test_menu_checkout_ownership.py tests/test_menu_dashboard_identity.py tests/test_menu_force_stop.py`
  - `python -m pytest -q tests/test_menu_shadow_ports.py tests/test_menu_navigation.py tests/test_menu_extra_words.py tests/test_menu_global_stop_clean.py tests/test_menu_open_dashboard_sweep.py tests/test_menu_status_rows.py tests/test_menu_live_state.py tests/test_menu_process_args.py tests/test_menu_dashboard_port_env.py`
  - `python -m pytest -q tests/test_pid_recycling.py tests/test_runtime_paths.py tests/test_single_registry_path.py tests/test_instance_lock.py tests/test_stop_lifecycle_457.py`
  - `python -m pytest -q tests/test_dashboard_server.py tests/test_dashboard_port.py tests/test_global_stop_loss.py`
- Full `python -m pytest -q` is the GitHub CI merge gate on Ubuntu and Windows; local
  runs are targeted only.
- Every new behavior needs a test that fails without it (see `tasks/plan.md` per-task
  assertions: current-folder selection, nested-worktree rejection, relative-command-line
  Unknown, Foreign refusal with path, PID-reuse skip, order-of-kill, rescan catch).
- **Only the pinned `MenuKeys` literal in `tests/test_menu_navigation.py` may change**
  (to cover key `x`); no existing assertion is weakened. All other new tests live
  only in the four new files. If a green run needs an old expectation moved, the
  change is wrong — re-read the script, do not edit the expectation.

## Scope fences (hard)

- **Only these change:**
  - `scripts/spread-hunter-menu.ps1` — the resolver, the three ownership helpers, the
    four wiring sites, the force-stop function, key `x` dispatch, menu text.
  - `README.md` — the actions-table rows for the three aliases + checkout rule + what
    stays running. Nothing else in the README.
  - New: `tests/test_menu_project_root.py`, `tests/test_menu_checkout_ownership.py`,
    `tests/test_menu_dashboard_identity.py`, `tests/test_menu_force_stop.py`.
  - `tests/test_menu_navigation.py` — pinned `MenuKeys` literal only (adds `x`).
  - `tasks/plan.md`, `tasks/todo.md`, `CONSTRAINTS.md`, `SPEC.md`.
- **Must NOT be modified:**
  - Any Python module (`core_brain/**`, `dashboard/**`, `scripts/*.py`).
  - `data/orders.db` and everything else under `data/`; `processes.json`,
    `markets.json`, heartbeat, locks, `runtime/` pid files (force stop removes a
    dashboard/session record only after every PID in it has exited).
  - `AGENTS.md`, `docs/agents/*`, `.gitignore`.
  - `Stop-TsBridge`, the resume stray scan, `Kill-RecordedPid`, `Get-DashInstance`,
    `Get-ProcessRecord`, `Stop-Dashboard`, `Stop-ShadowDashboard`.
  - The module-regex text pinned by `test_menu_global_stop_clean.py`
    (`-m\s+(core_brain\.|dashboard\.server|scripts\.(`) — byte-identical.

## Anti-cheat & safety rules

- **Anti-cheat:** no skipped/xfailed tests added, no deleted assertions, no weakened
  bounds, no `# type: ignore`/`noqa`, no PowerShell `Set-StrictMode` loosening to get
  green. A PowerShell host missing on the machine is a reported skip, not a pass.
- **Harness discipline:** new tests use the `_lift` pattern (function closing braces at
  column zero — all new functions obey this), temporary checkout folders with marker
  files, and stub side effects (`Get-CimInstance`, `taskkill`, `Invoke-RestMethod`,
  `Get-PortPid`, `Wait-ProcessGone`). Never run stop/adopt against live services.
- **Stub fidelity:** the code calls `taskkill`, not `taskkill.exe`, so the stub
  intercepts it — keep it that way.
- **No new dependencies.** No Python changes at all; no `psutil`, no `Add-Type`
  memory-read helper, no parent-chain check (C5 stays a follow-up-ticket decision).
- **No live venue contact.** Build and tests never run `quote`, `complete`, the Trader
  loop, or the dashboard START button. Operator hands-on checks run `status` first and
  use a worktree with no live trading for the force-stop step.
- **No production database in any manual check.** Any `status` run points at a
  disposable DB.
