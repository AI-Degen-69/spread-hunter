# AGENTS.md — polymarket-maker

## What this repo is

A maker strategy on the same market. It rests bids on BOTH outcomes rather than crossing, aiming to earn the spread and stay inventory-balanced, and holds to resolution. As a maker it pays no taker fee.

**Measured against:** spread capture versus adverse selection, fill rate against real queue depth, and inventory balance — hedged markets have measured +\0.70/market versus -\0.95 when badly unbalanced.

Simulation only. It never places a real order.

## Non-negotiable: keep the research log current

Any commit touching `strategy/` or `server/` MUST also update `research/`
in the same commit. A pre-commit hook enforces this — it is not a convention
you can quietly skip.

Run once after cloning (git does not install hooks automatically):

    bash scripts/setup-hooks.sh

Update all four files together:

| file | content |
|---|---|
| `research/RESEARCH_LOG.md` | Question → Method → Result → Verdict |
| `research/RESEARCH_SUMMARY.md` | one dated bullet per concrete thing done |
| `research/he_RESEARCH_LOG.md` | Hebrew mirror of the log |
| `research/he_RESEARCH_SUMMARY.md` | Hebrew mirror of the summary |

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
    server/     dashboard.py (API + app) + kanban.py (page)
    research/   the five files above
    deploy/     container entrypoint + preflight

The sibling repo `polymarket-taker` uses the SAME layout. Keep it that way — the only
difference between the repos should be strategy-specific.

## Safety

- **Never place a real order.** Paper simulation only.
- Hosted credentials are placeholders. Never deploy a real `PRIVATE_KEY`.
- Deploy only to a **non-US region**: Binance returns HTTP 451 to US IPs, and
  the preflight will refuse to start rather than run blind.
- **Changing strategy parameters invalidates the current sample.** Archive the
  database and start a fresh run rather than mixing configs in one dataset.
- Run one instance at a time. Concurrent bots writing one database sum their
  independent inventories into silently invalid data.

## Output style

The reader has ADHD. Shape every response so it can be acted on:
1. Lead with the answer or next action: command, path, or snippet first.
2. Number multi-step work; one bounded action per step.
3. End with one next action doable in under two minutes.
4. Finish the current issue before raising a new one.
5. Restate progress each turn ("step 3 of 5 done").
6. Give time estimates in concrete units, never "a bit".
7. After a change, show what now works.
8. Errors: state location, cause, and fix. No drama.
9. Cap lists at 5 items.
10. No preamble, no recaps, no closers.

Exceptions: explain fully when asked to explain. Confirm before destructive actions. After three failed fixes, stop and name the doubtful assumption. If the request is ambiguous, ask one short question.

Source: ayghri/i-have-adhd skill installed at `.agents/skills/i-have-adhd/SKILL.md`. Turn off with "stop adhd mode" or "normal mode".

## Agent skills

### Issue tracker

Issues live in GitHub Issues (`AI-Degen-69/maker`); use the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Five canonical roles map to the default labels (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
