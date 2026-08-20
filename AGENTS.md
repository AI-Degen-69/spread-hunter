# AGENTS.md — Spread Hunter

## What this repo is

A maker strategy on the same market. It rests bids on BOTH outcomes rather than crossing, aiming to earn the spread and stay inventory-balanced, and holds to resolution. As a maker it pays no taker fee.

**Measured against:** spread capture versus adverse selection, fill rate against real queue depth, and inventory balance — hedged markets have measured +\0.70/market versus -\0.95 when badly unbalanced.

Strategy decisions are simulated. The live path is not a stub: `live/engine/live_exec.py` talks to the
venue for real — one order was accepted on the live CLOB (`status='live'`,
`research/RESEARCH_LOG.md:2203`) and gasless `redeem` submits through the relayer. The live phase
(Owner-approved 2026-08-21) runs supervised real-money quoting on a $100 account with minimal risk
per trade; how exposure may be opened is governed by Safety.

## Non-negotiable: keep the research log current on strategy changes

Any commit touching core strategy logic, parameters, or architecture
(`strategy/`, `scripts/rank_markets.py`, `scripts/rerank_loop.py`) MUST also
update `research/` in the same commit. A pre-commit hook enforces this.

Design changes, theme/UI styling, dashboard templates, and helper scripts
do NOT require research log entries.

Run once after cloning (git does not install hooks automatically):

    bash scripts/setup-hooks.sh

Update all four files together:

| file                              | content                                  |
| -----------------------------------| ------------------------------------------|
| `research/RESEARCH_LOG.md`        | Question → Method → Result → Verdict     |
| `research/RESEARCH_SUMMARY.md`    | one dated bullet per concrete thing done |
| `research/he_RESEARCH_LOG.md`     | Hebrew mirror of the log                 |
| `research/he_RESEARCH_SUMMARY.md` | Hebrew mirror of the summary             |

Conventions:
- Verdict is a decision, not a summary: `DEAD` / `PARKED` / `LIVE` / `OPEN`
- Negative results are kept, never deleted — most of what this project learned
  came from things that did not work
- Numbers are measured, not estimated; if a figure is an estimate, say so
- Instrumentation bugs get their own entry. On this project they have
  repeatedly been the difference between a real finding and a fake one
- Hebrew mirrors the English; it is not an independent document

Escape hatch for typos and formatting: `git commit --no-verify`.

## Layout

Two trees. The simulation is at the repo root; everything that can touch real money is
under `live/`, self-contained, and imports nothing from the root.

    strategy/   simulation engine
    server/     fleet_dash.py (dashboard API + page)
    research/   the five files above
    PROGRAM.md  research org code — how a bounded experiment is run (read it before an experiment)

    live/                     the real-money tree. Run it from here.
      engine/                 live_exec, live_pairs, order_registry
      engine/config.py        forked from strategy/config.py
      engine/markets.py       forked from strategy/markets.py
      dash/live_dash.py       the single-cycle dashboard (:8799)
      scripts/                audit_settlement.py, refork.py
      run/live.db             THE live order registry — nothing writes anywhere else
      tests/                  live suite; run `pytest -q` from inside live/
      FORKED_FROM.json        which root commit each forked module came from

**The package is named `engine`, not `strategy`, and the name is load-bearing.** Two
directories called `strategy` merge into one implicit namespace package, which is how live
code came to import the simulation's `markets.py` without saying so — and how a clean dry
run came to certify a live path that then failed to import. Do not name anything under
`live/` after a package that exists at the root; sealing the collision with `__init__.py`
makes it worse, not better (it breaks every root import in the same process).

`live/engine/config.py` and `live/engine/markets.py` are **forks, not imports**. They may
diverge — live tuning must not perturb a simulation sample. `live/tests/test_fork_drift.py`
fails when the root copy moves, so the divergence stays a decision. Clear it with
`python live/scripts/refork.py` after reading the diff.

Commands:

    cd live && python -m engine.live_exec status      # or, from the repo root:
    python live/engine/live_exec.py status
    cd live && python -m dash.live_dash               # dashboard on :8799
    supervised live cycle: docs/LIVE_CYCLE_RUNBOOK.md
    cd live && pytest -q                              # live suite
    pytest -q                                         # simulation suite (repo root)

## Safety

- **Staged exposure rule.** Venue calls are permitted only in the order that *closing*
  capabilities land, and only where they cannot open exposure.
  1. Allowed now: read-only queries, and redeem — it closes a position, never opens one.
  2. **Dry-run is the default.** Every subcommand that can reach the venue takes `--live` through
     `argparse.SUPPRESS`; without that flag nothing is sent.
  3. **Live quoting is approved (Owner, 2026-08-21):** supervised real-money operation on the $100
     account, minimal risk per trade — `MAX_ORDER_USD=25`, `MAX_TOTAL_USD=100`, pairs held to
     resolution. Exposure is opened only through the gated verbs in 4. The staged rule's rationale
     still stands: a one-sided fill with no live stop-loss rides unhedged to resolution at up to
     100% loss of that leg, so unattended operation and any budget above $100 wait on the closing
     stages (1–4: fill detection, merge, stop-loss, second-leg completion).
  4. **`--live` is gated on DIRECTION, not on the command's name.** Owner decision, 2026-08-18,
     replacing the earlier per-command sign-off rule.
     - **Pre-approved — closing commands.** `exit`, `complete`, `merge`, `redeem`, `cancel`,
       `cancel-market`, `cancel-all`. Each of these only reduces exposure, so a full cycle runs
       without stopping for approval at every step. Friction here costs money: a naked leg that
       waits on a copy-paste is a naked leg for longer.
     - **Explicit approval each time — opening commands.** `quote`, and anything else that rests
       or crosses to create a position. `quote` is the only verb in the set that can create a loss,
       so it is the only one that stops for a human. The Owner watches the position on
       Polymarket's own interface while it runs.
     - **Unattended operation stays gated.** The loop may run supervised — the Owner watches the
       position on Polymarket's own interface while it runs. Fully unattended operation, and any
       budget above the approved $100, wait on the closing stages (1–4); see SESSION-66-BRIEF §5.
     - **Funding is requested before the cycle, never during.** Tell the Owner the amount and the
       reason ahead of time; do not discover mid-cycle that the wallet is short.

  Stage list and current status: `.claude/reviews/SESSION-66-BRIEF.md` §5.
- Hosted credentials are placeholders. Never deploy a real `PRIVATE_KEY`.
- Deploy only to a **non-US region**: Binance returns HTTP 451 to US IPs, and
  the preflight will refuse to start rather than run blind.
- **Changing strategy parameters invalidates the current sample.** Archive the
  database and start a fresh run rather than mixing configs in one dataset.
- Run one instance at a time. Concurrent bots writing one database sum their
  independent inventories into silently invalid data.

## Workspace hygiene & temporary files

- **Delete temporary/scratch files immediately:** Scratch scripts, diagnostic dumps, one-off analysis snippets, and temporary mockups must be deleted as soon as their output has been verified.
- **Never commit generated output or debug logs:** Telemetry, audit reports, browser logs, and intermediate test artifacts must be cleaned up and kept out of the repository.
- **Zero dead weight:** If a file, skill, or configuration becomes obsolete or redundant, delete it immediately.

## Output style

The reader has ADHD. Shape every response so it can be acted on in plain simple English for non advanced developers:
1. Lead with the answer or next action: command, path, or snippet first.
2. Number multi-step work; one bounded action per step.
3. Finish the current issue before raising a new one.
4. Restate progress each turn ("step 3 of 5 done").
5. After a change, show what now works.
6. Errors: state location, cause, and fix. No drama.
7. Cap lists at 5 items.
8. No preamble, no recaps, no closers.

Exceptions: explain fully when asked to explain. Confirm before destructive actions. After three failed fixes, stop and name the doubtful assumption. If the request is ambiguous, ask one short question.

Source: ayghri/i-have-adhd skill installed at `.agents/skills/i-have-adhd/SKILL.md`. Turn off with "stop adhd mode" or "normal mode".

## Agent skills

### Issue tracker

Issues live in GitHub Issues (`AI-Degen-69/spread-hunter`); use the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Five canonical roles map to the default labels (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
