# First run & full run anatomy

How to take a stale `runtime/` back to a first-run state, then start a run that
serves a live-updating dashboard in the background. Also the complete map of every
command a run is made of, with its arguments.

## One-command reset (menu options)

The operations menu (`scripts/spread-hunter-menu.ps1`, PowerShell 7) has reset
options that do the whole sequence in a single choice: **check processes → stop
them → wipe runtime state → verify nothing blocks → start the chosen mode**.

| Option | What one choice covers | Safe to run |
| --- | --- | --- |
| `start -Yes` | **1 · LIVE Start**: preflight-stop everything, wipe data, verify clean, then start the live dashboard + bot stack (Market Filter, Order Manager, Trader). Rests real bids | **No — real bids; requires `-Yes`** |
| `stop` | **2 · LIVE Stop**: stop the live bot stack, then the dashboard | Yes |
| `host` | **3 · LIVE Host**: release :8799 (live-only) from whatever menu-owned dashboard holds it (no wipe), host the live dashboard (`data/orders.db`) & open browser | Yes |
| `shadow-run [-Minutes N]` | **4 · SHADOW Start**: preflight-stop everywhere, wipe data, verify clean, then the shadow dashboard + rehearsal loop (`shadow_run`) + a stop-loss watcher scoped to that session's ring (self-stops after N minutes). Prompts for minutes interactively; default 5 | Yes (spends nothing) |
| `stop-shadow` | **5 · SHADOW Stop**: stop the rehearsal loop (if still running), watcher, and viewer | Yes (spends nothing) |
| `open-shadow` | **6 · SHADOW Host**: mint a new sequenced store (`data/NN_shadow_<stamp>.db`) and host it on its instance port (no wipe) & open browser. To reopen an existing instance, use `shadow-resume` (pinned shadow-01) or its instance URL | Yes (spends nothing) |
| `clean` | **7 · Global Stop & Clean**: kill all bot processes/dashboards, wipe data, verify clean — starts nothing | Yes |
| `status` | **8 · Status**: dashboard + every stack process + feed + repo identity | Yes |

Interactive menu equivalents, grouped in the grid: **🟢 LIVE** `1` start / `2` stop / `3` host dashboard; **🥷 SHADOW** `4` start (prompts minutes) / `5` stop / `6` host; **MAINTENANCE & STATUS** `7` global stop & clean / `8` status.

### Shadow dashboard ports (one per instance)

:8799 is live-only. Every shadow rehearsal instance serves its dashboard on a
port derived from its run id — 8800 plus the two-digit sequence:

| Instance | Store pattern | Dashboard |
| --- | --- | --- |
| shadow-01 | `data/01_shadow_*.db` | http://127.0.0.1:8801 |
| shadow-02 | `data/02_shadow_*.db` | http://127.0.0.1:8802 |
| shadow-03 (next) | `data/03_shadow_*.db` | http://127.0.0.1:8803 |

The menu derives the port itself (`shadow-resume` reopens the pinned store as
shadow-01 on :8801; `shadow-resume -ResumeDb data/02_shadow_exit-regime_23-09.db`
(the exact file name; wildcards are not expanded) hosts the second instance
on :8802). Each instance keeps its own dashboard PID file
(`runtime/shadow-dash-<run-id>.pids.json`), session file
(`runtime/shadow-session-<run-id>.json`) and loop logs, so `status` lists
every running instance with its port and `stop-shadow` stops them all. Live
on :8799 never blocks a shadow start, and a shadow start never touches :8799.

### What the reset refuses to do

- Never touches `data/orders.db` (the production registry) — by construction.
- Refuses to wipe while any spread-hunter process is still alive (checks port
  :8799, dashboard PID files, `runtime/processes.json` PIDs, guardrail heartbeat).
- Refuses to act on a port owned by a process that is not a spread-hunter dashboard.
- Kills the Global Stop Loss watcher only via its heartbeat record (`pid` +
  `started_at`, start-time validated like every other stack PID).

### Manual clean, step by step

For reference; the menu options above do all of this. **Verify nothing is running
first** — a wipe beside a live stack is how a second Trader gets launched next to
the first.

```powershell
.\scripts\spread-hunter-menu.ps1 status       # confirm all OFF
netstat -ano | Select-String ":8799.*LISTENING"  # must be empty

.\scripts\spread-hunter-menu.ps1 stop         # bot stack, then dashboard
.\scripts\spread-hunter-menu.ps1 stop-shadow  # if a shadow dash is up

# Wipe regenerable state (gitignored only; data/orders.db untouched):
Remove-Item runtime/.current_run_id, data/.current_run_id -ErrorAction SilentlyContinue
Get-ChildItem runtime -File | Remove-Item -Force
Remove-Item data/shadow.db, data/shadow_stat_verify.db* -ErrorAction SilentlyContinue
Get-ChildItem data -Directory -Filter "shadow_stat_*" | Remove-Item -Recurse -Force
Remove-Item run -Recurse -Force -ErrorAction SilentlyContinue

# Verify clean, then start your mode:
.\scripts\spread-hunter-menu.ps1 status
.\scripts\spread-hunter-menu.ps1 shadow-run   # or stop / open-shadow / clean (see table above)
```

## The full run — five background processes

A live run is the dashboard plus a stack of four: screening, reconciliation,
decision/execution, and the stop-loss watchdog. The menu launches them exactly as
below (`$StackCmds` in `scripts/spread-hunter-menu.ps1`).

| # | Role | Command | Writes / watches |
| --- | --- | --- | --- |
| 1 | **Market Filter** (screener) | `python -m scripts.filter_loop` | `runtime/markets.json` (the Trader's universe) |
| 2 | **Order Manager** (poll/reconcile) | `python -m core_brain.order_manager poll --interval 0.5` | `data/orders.db`, account sweep |
| 3 | **Trader** (decide & execute) | `python -m core_brain.trader_loop --live --no-reconcile --no-sweep --interval 5` | rests REAL maker bids |
| 4 | **Global Stop Loss** (guardrail) | `python -m scripts.global_stop_loss` | `runtime/global_stop_loss_heartbeat.json`, alerts log |
| 5 | **Dashboard** | `python -m dashboard.server --port 8799` | serves http://127.0.0.1:8799 |

### 1 · Market Filter — `scripts/filter_loop.py`
Re-runs the filter forever, regenerating `runtime/markets.json`. **No CLI
arguments** — env-var driven:
- `SH_FILTER_INTERVAL_SEC` — seconds between reranks (default `600` = every 10 min)
- `SH_TOP_MARKETS` — how many winners to write

One-shot engine, `scripts/filter_markets.py`:
- `--top N` — how many markets to write (default 20)
- `--dry-run` — score and print the ranking, leave `markets.json` untouched
- `--trial-depth USD` / `--trial-volume USD` — U32/U36 gate trials: test a looser
  depth ($500) / volume ($125000) bar without changing the permanent config;
  adopted markets are tagged in `markets.json`

### 2 · Order Manager — `core_brain/order_manager.py poll`
Reconciliation heartbeat: polls the CLOB, turns fills into registry rows, sweeps
the account.
- `--interval` — cadence in seconds (default `0.5`)
- `--once` — reconcile once and exit
- `--db` — registry path (default `data/orders.db`)
- `--sweep-every N` / `--sweep-interval SEC` — account-sweep cadence (poll owns the
  sweep when running with the fleet)
- `--no-watch-guardrails` — don't supervise the guardrail as a child process
  (default: on)

### 3 · Trader — `core_brain/trader_loop.py` ⚠️ real bids
Rotation loop: decide → submit → reconcile across the graduated universe.
**`--live` defaults to True.**
- `--live` / `--no-live` — venue vs dry-run
- `--interval` — rotation cadence in seconds (default `5.0`)
- `--once` — one rotation, then exit
- `--db` — registry (default `data/orders.db`)
- `--max-markets N` — cap markets rotated (default: all)
- `--funder ADDR` — balance read funder (default `POLY_FUNDER`)
- `--no-reconcile` / `--no-sweep` — skip those passes because the poll loop owns
  them (the menu always passes both)

### 4 · Global Stop Loss — `scripts/global_stop_loss.py`
Watchdog, independent of the poll loop. Flags the two live-run failure signatures:
**repeat-exit** (same pair_id exits twice in the window) and **over-cap pair**
(filled at/above the pair-cost cap).
- `--interval` — seconds between checks (default `5`)
- `--window` — repeat-exit window in seconds (default `900`)
- `--cap` — pair-cost alert threshold (default `0.995`)
- `--db`, `--ring`, `--alerts-log` — store overrides
- `--once` — single check and exit

### 5 · Dashboard — `dashboard/server.py`
- `--port` (default `8799`), `--host` (default `127.0.0.1`),
  `--db` (default `data/orders.db`; `data/shadow.db` for shadow mode)

**A backend change does not reload live.** `core_brain/*` (including `kpi.py`)
and `dashboard/server.py` are imported once at process start, while
`dashboard/static/*` is read by the browser on every page load. After changing
backend code, restart the dashboard — stop and host again
(`.\scripts\spread-hunter-menu.ps1 stop` then `host`, or the dashboard's restart
endpoint). A page that is newer than the process answering it says so: the Quant
Risk grid shows an amber **"Dashboard backend is older than this page — restart
the dashboard to see all metrics."** note and logs one console warning. A stale
payload can still carry `unmeasured` tiles for metrics it predates, but the note
tells you the page itself is newer than the process answering it — restart the
dashboard so the two agree.

## One-shot operators (not the stack, but part of a run)

All under `core_brain/order_manager.py`; `--no-live` anywhere is the dry run:

| Command | What it does |
| --- | --- |
| `status`, `balance [--funder ADDR]` | Read-only |
| `quote --price X [--down-price Y] [--post-only] [--tif GTC\|GTD\|FOK\|FAK] [--expiration TS]` | Rest both legs |
| `cancel <order_id>` / `cancel-market <condition_id>` | Pull resting orders |
| `poll`, `probe --series S [--cycles 30]` | Stack poll; latency probe |
| `merge <condition_id> --amount N [--index-sets 1,2] [--collateral USDC.e]` | Gasless pair merge back to $1 |
| `redeem <condition_id>` | Gasless redemption |
| `exit <pair_id>` | Close a one-sided pair (cancel resting leg, sell filled leg) |
| `complete <pair_id>` | Cross the book to finish a one-sided pair |
| `account-sweep` | Balance sync (the menu's `open` action runs it once) |
| `python -m scripts.audit_settlement [--tx TX]` | 3-way settlement/balance verification (Registry vs Venue vs Chain) |

## Shadow rehearsal (the `--minutes` command)

`python -m core_brain.shadow_run --minutes 5` runs the **same full loop**
(screener → quoting → fills → merge path) against the live book, spending
nothing — no signer is loaded.
- `--minutes` — time box (default `5.0`); the run stops on this wall clock
- `--interval` — rotation cadence (default `5.0`)
- `--db` — shadow store (default `data/shadow.db`; `data/orders.db` is refused)
- `--max-markets N` — cap rotation universe
- `--funder ADDR` — balance-read funder

It rehearses everything: the same `MakerConfig`, the same `MAX_ORDER_USD` /
`MAX_TOTAL_USD`, the same gates, the same `decide_quotes`. Only the signer, the
store, and the wall clock differ. Point the shadow dashboard at `data/shadow.db`
and it updates live while the rehearsal runs.

## The wiring rule that prevents contradictions

The menu launches the Trader with `--no-reconcile --no-sweep` because **the poll
loop owns reconciliation and the sweep**. If you hand-start stack pieces, keep the
division: one sweeper, one reconciler, one decide loop, one filter loop, one
guardrail. Two of any of those against `data/orders.db` is how a run looks fine
but contradicts itself.

## Safety, restated

1. **LIVE is the default.** `reset-live`, `start`, `quote`, `complete`, and the
   dashboard's START all reach the venue. `--no-live` and the shadow modes are the
   dry runs.
2. **`data/orders.db` is the production registry.** Read it; never rewrite or
   delete it. Every wipe in this doc leaves it alone.
3. **Never wipe `runtime/processes.json` beside a live stack** — that orphans the
   live PIDs and a later start launches a second Trader. The menu's `reset` stops
   first, then wipes.
