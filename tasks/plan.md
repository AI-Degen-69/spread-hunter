# Station II Plan — Issue #881: PWD-aware worktree menu + nuclear process stop

Branch: `i881/improve-align-spread-hunter-menu-script-for-pwd-aw` | Issue: #881

- **Tier:** Standard — one script plus docs and harness tests; a single architectural
  decision (checkout-local ownership with fail-closed Unknown). Rationale: no new
  dependency, no public API, no schema change; all power is in narrowing match
  predicates and adding one fenced action.
- **Task types:** Code (PowerShell menu logic + pytest harness tests).
- **Stack / runner:** PowerShell 7 menu (`pwsh` 7.6 available, Windows PowerShell 5.1
  fallback); tests are Python `pytest` files that shell lifted functions to a PowerShell
  host and skip when none exists (`python -m pytest -q`).

---

## 1. CodeRabbit intake note (read once, not twice)

The `coderabbitai` plan comment carries its full plan plus a byte-identical echo inside
`<!-- <rawResChunk> -->` — only the rendered copy was used. Adopted: the 3-task split
(1.1 checkout selection → 1.2 ownership/dashboard identity → 1.3 force stop), all named
functions and test files, the four dashboard status labels, the seven-module regex, the
kill order (dash → order_manager → rest), PID-reuse guard, single rescan, three-list
report + resting-orders warning, and the `x`-key wiring. Rejected: the `Add-Type`
memory-read helper and the parent-chain check (fragile/unsupported — stays a follow-up
decision per C5); fixing `Stop-TsBridge` and the resume stray scan (left as-is per
scope); adding a `BOT_ORDER_QUEUE_FLOW_WINDOW_SEC`-style reader (not this issue).
`[UNVERIFIED]` carried into tasks: confirm `x` is unused (verify against
`$script:MenuKeys`); confirm `registry_path` default source in `dashboard/server.py`
before trusting it in `Get-DashboardOwnership`; confirm option 7's command-line
confirmation rule before mirroring it in the `x` action.

## 2. Domain skill routing

Task type is Code → planning skills `test-driven-development` (every behavior gets a
failing-first harness test) and `incremental-implementation` (land T1 → T2 → T3 in
order; each task is a complete vertical slice with its own tests). No frontend, API, or
docs skill applies: menu copy changes are one-line labels, and the README edit is a
tabled row, not a documentation design task.

## 3. Spec pointer

Full acceptance spec: `SPEC.md` (goal, non-goals, §3.1–§3.4 components, §4 acceptance
criteria, §5 edge cases). The one improvement proposal (§5 below) is folded into T1.

## 4. Interface contracts (locked — callers and tests pin these)

| Function / key | Signature / mapping | Contract |
|---|---|---|
| `Resolve-ProjectRoot` | `-CurrentPath -ScriptRoot → @{ Path; Source }` | Exact-folder marker check only; `Source ∈ {"current folder","script location"}`; `GetFullPath`-normalized, no trailing separator |
| `Test-PathInCheckout` | `-Path -Root → bool` | Case-insensitive boundary match; bounded nested-checkout walk between path and root, never above root |
| `Get-CommandLineCheckoutOwner` | `-CommandLine -Root → Owned\|Foreign\|Unknown` | argv[0] ignored; quoting + `--opt=value` honored; none/mixed → Unknown |
| `Get-DashboardOwnership` | `-Port → Owned\|Foreign\|Unknown` | Owned = canonical `data/orders.db` + registry under local `runtime/` or `run/` + `services.dash.pid == Get-PortPid`; read-only, never writes records |
| `Stop-WorktreeProcessesForce` | `→ bool` | Kill order dash → order_manager → rest; `taskkill /T /F` + `Wait-ProcessGone`; PID-reuse re-check; one rescan; three-list report + resting-orders warning |
| Action map / dispatch | `stop-all-force, force-stop, nuke → x` | `x ∉ $script:MenuKeys` (verified at build time); `stop-all/stopall/clean/reset → 7` preserved; `Invoke-LiveAction "x"` calls only the force stop |

## 5. Improvement proposal (adopted — test hardening, not scope expansion)

> **Evidence:** today `$PSScriptRoot` appears exactly once in the 3410-line script
> (line 51: `$ProjectPath = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path`).
> **Proposal:** add a static test asserting `$PSScriptRoot` is still referenced exactly
> once after T1, so no future edit can silently re-couple a path to the script location.
> Folded into T1's test list below.

## 6. Dependency graph

`T1 (checkout root) → T2 (ownership + dashboard identity) → T3 (force stop + wiring)`.
T2 needs T1's root; T3 needs both (kill scoping uses the T2 classifiers on the T1 root).

---

## T1 — Checkout selection from the current folder [Code] S [x]

Depends on: nothing. Files: `scripts/spread-hunter-menu.ps1` (≈lines 50–61, header,
`status`, 3 timebox launches, comment near 1038–1039), `tests/test_menu_project_root.py`
(new), `README.md` (one checkout-rule sentence — or defer to T3's README pass).

- Add `Resolve-ProjectRoot -CurrentPath -ScriptRoot` near the top (closing brace at
  column zero for `_lift`); set `$ProjectPath` from it at the current line 51;
  `-WorkingDirectory $ProjectPath` on the three timebox launches; fix the 1038–1039
  comment; header/`status` print `Checkout: <path> (from current folder|from script
  location)` + mixed-checkout note. `Resolve-RuntimeFile`, `Get-ServiceEntry`,
  `Show-CheckoutIdentity` untouched.
- Tests (temp folders + `_lift`, `pwsh`/`powershell` or skip): both markers → current
  folder; one marker → script location; subfolder of checkout (`<checkout>/scripts`)
  → fallback (no parent walk); unrelated folder → fallback; spaces + trailing
  separator normalized; timebox launches pin working directory (source inspection);
  **static: `$PSScriptRoot` occurs exactly once** (the improvement proposal).
- Done when: from a worktree folder, main's script by full path prints the worktree
  as the checkout.

## T2 — Ownership proofs + dashboard identity [Code] M [x]

Depends on: T1. Files: `scripts/spread-hunter-menu.ps1` (≈lines 529–561, 909–956,
1832–1841, 1982–1999, 2090–2148, 2563–2638), `tests/test_menu_checkout_ownership.py`
(new), `tests/test_menu_dashboard_identity.py` (new).

- Add `Test-PathInCheckout`, `Get-CommandLineCheckoutOwner`, read-only
  `Get-DashboardOwnership` (column-zero braces); require Owned in
  `Test-DashboardServer`; four status labels; `status` writes nothing; `Stop-BotStack`
  gates the POST; replace the three substring predicates keeping regex text identical;
  leave record helpers, `Stop-Dashboard`, `Stop-ShadowDashboard`, `Stop-TsBridge`,
  resume stray scan untouched.
- Tests: owned path / prefix-sibling refusal / case-slash-`..` normalization /
  nested-worktree both directions / relative command line → Unknown / absolute args
  decide owner / mixed → Unknown / interpreter path ignored / absolute script path
  owned; dashboard adopt-Owned / sibling-with-production-flag Foreign with path /
  PID-mismatch Unknown / db-override-not-owned / request-failure Unknown /
  status-labels-write-nothing / stop_bot_stack skips-or-controls.
- Done when: main's `status` labels a worktree dashboard `other checkout` and main's
  `stop` never POSTs to it.
- Reconciliation note (build): the same Owned-predicate swap was also applied to
  `Test-OrphanStackProcess`, which carries the identical `-like "*$ProjectPath*"`
  prefix flaw (a sibling checkout's orphan would block this checkout's reset).
  Same one-line narrowing, same fail-closed direction (relative command lines stay
  unmatched either way); not in the leave-untouched list, aligned with the
  checkout-local invariant.

## T3 — Separate force-stop action + wiring + docs [Code] M [x]

Depends on: T1, T2. Files: `scripts/spread-hunter-menu.ps1` (new
`Stop-WorktreeProcessesForce`, `$actionMap`, `Invoke-LiveAction`, menu text),
`tests/test_menu_force_stop.py` (new), `README.md` (actions table).

- Seven-module regex candidate scan (creation time read once); Owned/Foreign/Unknown
  per §3.3; kill order dash → order_manager → rest with pre-kill creation-time
  re-check; `taskkill /T /F` (not `taskkill.exe`); `Wait-ProcessGone`; one rescan;
  three-list report + resting-orders warning verbatim; record cleanup only on full
  exit; never touch `processes.json`/`markets.json`/heartbeat/locks/`data/`; never
  call `Stop-TsBridge`/`Stop-BotStack`/`Stop-AllSpreadHunterProcesses`. Wire the three
  aliases to `x` after verifying `x` unused; interactive yes/no; command-line follows
  option 7's rule (verify first). Option 7 untouched.
- Tests with stub harness + fake PIDs 101–110: kill list `[101, 102, 103]` in order /
  Foreign {104, 106} vs Unknown {105, 107, 110} / modules 108–109 ignored /
  changed-start-time skipped / survivor → false + record kept / rescan catch /
  registry+bridge untouched / action-map routes + `stop-all→7`, `stop→2` preserved /
  dispatch calls force-stop only.
- Done when: in a worktree, `stop-all-force` stops its adopted dashboard + recorded
  workers and lists main's processes as left running.
- Reconciliation note (build): `test_menu_navigation.py::test_menu_key_set_is_identical_everywhere`
  pins the exact `MenuKeys` literal, and the specified key `x` cannot pass the dispatch
  allow-list without joining it. The literal gained `"x"` (one-line diff); the pin's
  anti-drift purpose is preserved — `test_every_key_has_a_branch` now also covers `x`.

## Checkpoints

- After T1: header/`status` proves checkout selection; T1 tests green.
- After T2: adoption/ownership proves isolation; T1+T2 tests green.
- After T3: force-stop proves scoped kill; all new + listed regression suites green.

## Sub-issue mapping

Skipped with reason: single-owner 3-task change on one branch — `tasks/todo.md`
checklist is the tracker; no parallel work to coordinate, no `blocked-by` edges needed.

## Operator hands-on checks (safe order; worktree with no live trading for step 4)

1. In a worktree folder run `.\scripts\spread-hunter-menu.ps1 status` → header shows
   the worktree path `(from current folder)`.
2. Same folder, run main's script by full path with `status` → worktree path +
   mixed-checkout note.
3. Only if nothing listens on 8799: in a worktree terminal run
   `python -m dashboard.server --port 8799` (do NOT press START), then main's `status`
   → `LISTENING – other checkout: <worktree>`, and no adoption record written.
4. In the worktree run `host`, then `status` (`ON (owned)`), then `stop-all-force` →
   dashboard + recorded workers stopped, main's processes listed as left running.
5. From main, `status` → main's processes unchanged.
