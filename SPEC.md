# SPEC: Issue #288 - Shadow dashboards get run-id-derived ports

## Goal
Every shadow rehearsal instance serves its dashboard on a port derived from
its run id: shadow-01 on :8801, shadow-02 on :8802, a future shadow-03 on
:8803. :8799 becomes live-only. No two instances fight over one port, and the
port number tells the operator which instance is answering.

## Acceptance criteria (from issue)
- [ ] `shadow-resume` hosts the shadow-01 dashboard on :8801 and records it
  in a per-instance PID file
- [ ] A second instance is hostable on :8802 through the menu (no
  hand-launched processes, no ad-hoc log names) and both dashboards answer
  simultaneously
- [ ] With live running on :8799, starting or resuming a shadow no longer
  reports a port conflict
- [ ] `docs/agents/first-run.md` documents :8799 as live-only and the 880x
  range as shadow instances
- [ ] `curl http://127.0.0.1:8801/api/system/status` and
  `curl http://127.0.0.1:8802/api/system/status` each answer as a shadow
  dashboard for the expected store while
  `.\scripts\spread-hunter-menu.ps1 status` lists both instances with ports

## Scope
### In scope
- `scripts/spread-hunter-menu.ps1`: port-derivation helper (`8800 + NN`),
  per-instance PID files (`runtime/shadow-dash-<run-id>.pids.json`) and log
  names, rewire of every `$ShadowPort` / `$ShadowDashUrl` reader (launch,
  adopt, stop, status rows, open-browser sites, TS bridge target)
- Second-instance hosting through the menu; migrate the hand-launched :8801
  dash, retire the `buffy_dash_8801` ad-hoc log
- `docs/agents/first-run.md` shadow sections
- New menu port test (`tests/test_menu_shadow_ports.py`, pwsh-subprocess
  harness following `tests/test_menu_shadow_seq.py`)

### Out of scope (per issue)
- Live stack, rehearsal loop, or any trading behavior -- ports and hosting
  only
- Moving existing databases or renaming run ids

## Interface contracts
- `Get-ShadowDashPort <run-id: string> -> int`: parses the leading `NN`
  from `shadow-NN`, returns `8800 + NN`; the menu's own unnumbered
  `shadow-resume` fallback id returns 8899. Empty or garbage ids throw --
  never silently fall back to :8799 beside the live stack (same rule as
  `resolve_port` in `dashboard/server.py`, pinned by
  `tests/test_dashboard_port.py`).
- `Get-ShadowDashUrl <run-id> -> string`: the single builder of
  `http://127.0.0.1:<port>`; all open-browser and status call sites use it.
- PID record schema unchanged (`pid`, `port`, `db`, ...), file name gains
  the run id: `runtime/shadow-dash-shadow-01.pids.json`.
- `dashboard/server.py` unchanged (`--port` / `--db` already suffice).

## Edge cases
- Run id 99 wraps the sequence (existing `Get-NextShadowSeq` behavior);
  port math holds for any two-digit id (01-99 -> 8801-8899).
- A foreign process already on the instance port: refuse with the owning
  PID, same as today -- never adopt blindly, never kill.
- Live on :8799 while shadow starts: no conflict, no message (the old
  "stop live first" failure path is deleted, not reworded).
