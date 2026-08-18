# AGENTS.md — Spread Hunter

## What this repo is

A maker strategy on the same market. It rests bids on BOTH outcomes rather than crossing, aiming to earn the spread and stay inventory-balanced, and holds to resolution. As a maker it pays no taker fee.

**Measured against:** spread capture versus adverse selection, fill rate against real queue depth, and inventory balance — hedged markets have measured +\0.70/market versus -\0.95 when badly unbalanced.

Strategy decisions are simulated. The live path is not a stub: `strategy/live_exec.py` talks to the
venue for real — one order was accepted on the live CLOB (`status='live'`,
`research/RESEARCH_LOG.md:2203`) and gasless `redeem` submits through the relayer. What is forbidden
is *opening exposure*; see Safety.

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

    strategy/   engine
    server/     fleet_dash.py (dashboard API + page)
    research/   the five files above
    PROGRAM.md  research org code — how a bounded experiment is run (read it before an experiment)

## Safety

- **Staged exposure rule.** Venue calls are permitted only in the order that *closing*
  capabilities land, and only where they cannot open exposure.
  1. Allowed now: read-only queries, and redeem — it closes a position, never opens one.
  2. **Dry-run is the default.** Every subcommand that can reach the venue takes `--live` through
     `argparse.SUPPRESS`; without that flag nothing is sent.
  3. **Forbidden until Stages 1–4 land and the Owner approves:** any order that opens or increases
     exposure — resting a quote, crossing to complete a pair, the automated loop. A one-sided fill
     with no live stop-loss rides unhedged to resolution at up to 100% loss of that leg.
  4. `--live` on a newly built command needs Owner sign-off for that command, not once per session.

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
