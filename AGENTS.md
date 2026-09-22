# AGENTS.md — Spread Hunter Live
@RTK.md
Real-money execution engine and operations dashboard for the Polymarket **spread hunter**
strategy: buy one UP share and one DOWN share of the same binary market for less than
$1.00 combined, then merge the pair back into exactly $1.00 of USDC.

```
profit per pair = 1.00 - (avg UP price + avg DOWN price)
```

The two failure modes everything else exists to prevent: a pair assembled **over $1.00**
is a booked loss, and a **single buy** (one leg filled, one not) is a directional bet
nobody decided to take.

## Before you touch anything

1. **LIVE is the default.** Every `core_brain.order_manager` subcommand reaches the venue.
   `--no-live` is the dry run.
2. **Opening commands are the operator's to run** — `quote`, `complete`, the Trader loop,
   and the dashboard's **START** button all spend money. Propose them; do not run them
   unless the operator says so in that session.
3. **`data/orders.db` is the production registry.** Read it; never rewrite or delete it.
4. **Dynamic Caps (`core_brain/config.py`):** `order_risk_pct = 25%` ($25.0 at $100 baseline), `naked_risk_pct = 6%` ($6.0 at $100 baseline), `bankroll_ceiling_pct = 90%` ($90.0 at $100 baseline), `max_pair_cost = 0.99`. Dynamic caps scale with live account value.

Full rules, including which commands are pre-approved: [docs/agents/safety.md](docs/agents/safety.md).

## Commands

```bash
python -m pytest -q                          # full test suite
python -m core_brain.order_manager status    # status and balance (read-only)
python -m core_brain.shadow_run --minutes 5  # full-loop rehearsal; no signer, spends nothing
python -m dashboard.server                   # dashboard on http://127.0.0.1:8799
```

```powershell
.\scripts\spread-hunter-menu.ps1 start       # start the whole stack
.\scripts\spread-hunter-menu.ps1 status
```

Operator-facing commands are PowerShell; sequence with `;`, never `&&`.

## Done means

Targeted checks for the changed behaviour must be green during development and after review
fixes. For Python changes, use the focused test file or selection matching the touched
modules (for example, `python -m pytest -q tests/test_<module>.py`). The full
`python -m pytest -q` regression suite is the GitHub CI merge gate on Ubuntu and Windows;
do not repeat that full suite locally during the review/PR stations. Every changed behaviour
needs a test that fails without the change, plus a **How to verify** block written for the
operator that lists only non-pytest, operator-actionable steps. Report the checks actually
run, not the impression. Rules and examples:
[docs/agents/verifying.md](docs/agents/verifying.md).

## Model conduct — verification

- **The agent runs focused checks itself.** During TDD, run the focused test through the
  RED/GREEN/REFACTOR loop; after review fixes, rerun the focused checks covering the touched
  code. The full `python -m pytest -q` suite runs in GitHub CI on Ubuntu and Windows and is
  the merge gate. These commands are never handed to the operator as a prompt, suggestion,
  or "How to verify" step.
- **Never tell, ask, or suggest the operator to run `pytest`** (or any `/pytest` prompt) to
  validate work. Report the agent-run results instead.
- **Owner Standing Directive 2026-08-29 — tests are not verification for the Owner.**
  The Owner has stated explicitly: asking to "check tests" or citing "tests passed" as proof
  means nothing was built — tests have passed before while the feature was still buggy.
  Tests are background-only, run silently by the agent. For the Owner, verification is
  **hands-on surface behavior only**: launch the menu/stack, run a shadow rehearsal, open
  `http://127.0.0.1:8799`, check the report file in `reports/`, observe the feature working.
  Never present `pytest` counts, `pytest -q` output, or "4 passed" as "meat to check."
  When something has meat to verify, point to the file/screen/command the Owner can touch
  and what value should differ — not to test results.
- **The "How to verify" block is hands-on only.** It steers the operator to *use* the change
  — launch the script/stack, open the dashboard, click through, observe behavior — not to
  audit tooling. It must NOT include:
  - running the test suite (`pytest`);
  - `gh issue view` / `gh pr view` or `git status`, or any "confirm the issue is closed /
    PR is merged / checks are green" step — the operator reads GitHub and CI themselves, so
    don't narrate the agent's own git hygiene back as a verify step;
  - anything that only re-states state the operator can already see.
  Point the operator to touch, experience, and exercise the feature.

## Pushing and merging
You are managing repo actions and work with the remote GitHub repo. Keep organized work and branch names. Use best convention globally that supports clarity, ease of use, and harmony with the CodeRabbit AI reviewer.
Full rules: [docs/agents/git-workflow.md](docs/agents/git-workflow.md).

GitHub operations are **fully autonomous and delegated to the agent**: commit, push, branch, PR creation, review rounds, and merging are decided and executed directly by the agent. No operator sign-off is needed. Keep the operator informed with concise status updates (e.g. `Working on [branch]...`, `Committed & Pushed...`, `PR Opened #...`, `Merged PR #...`).

1. **One push per review round.** Batch every accepted fix into one commit and push once.
2. **One focused review round.** Triage every CodeRabbit comment before acting. Reply to every accepted or rejected thread with a concrete `ACCEPT:` or `REJECT:` rationale, resolve only replied-to threads, apply accepted fixes locally, and run the targeted checks covering the touched code. Do **not** invoke `@coderabbitai autofix`; the agent applies accepted fixes and performs the verification. Post one concise summary comment for the round. CodeRabbit reviews do **not** fire automatically here: after opening the PR, post `@coderabbitai review` as its own comment. Check immediately for an existing review, then use the required 5m → 4m → 3m → 2m → 1m countdown only when no review content exists.
3. **No secondary review loop.** This pipeline runs exactly one focused CodeRabbit round. Do not re-trigger a second review after pushing the accepted fixes. If CodeRabbit is rate-limited or remains stuck after the countdown, use the objective agent fallback review and record that status honestly.
4. **Read the full diff and check the stack before merging.** A stacked merge can carry another PR onto `main` with it. Merge autonomously only when targeted local checks pass, CI is green, and blockers are resolved. GitHub CI, not local pytest, is the merge gate; inspect it with `gh pr checks <pr-number>`.
5. **Review fallback.** Prefer CodeRabbit for the single review round. A quota response or a review that remains stuck after the allowed countdown triggers the agent fallback review; do not wait an hour or claim a clean CodeRabbit pass.

A `PreToolUse` hook (`scripts/hooks/git_workflow_guard.py`) puts these rules at the command. It **reminds** on rule 1, printing the round discipline before a push, and **guards** against runaway loops (blocking a 4th push without triage).

## Reference

| Read this | When |
| --- | --- |
| [docs/agents/workflow-cheatsheet.md](docs/agents/workflow-cheatsheet.md) | **Core Agent Responsibilities & Default Workflows** — Read for how I proactively manage PRs, UI/UX, and TDD loops |
| [docs/agents/safety.md](docs/agents/safety.md) | Before running any command that could reach the venue |
| [docs/agents/verifying.md](docs/agents/verifying.md) | Before reporting any change done |
| [docs/agents/architecture.md](docs/agents/architecture.md) | Finding the module that owns a behaviour; runtime state files |
| [docs/agents/first-run.md](docs/agents/first-run.md) | Resetting a stale runtime to first-run state; every run command and its arguments; what each menu option covers |
| [docs/agents/glossary.md](docs/agents/glossary.md) | Naming anything in code, commits, issues or dashboard copy |
| [docs/agents/strategy.md](docs/agents/strategy.md) | Changing quoting, sizing, market selection or pricing mode |
| [docs/agents/python-conventions.md](docs/agents/python-conventions.md) | Writing or reviewing Python in this repo |
| [docs/agents/git-workflow.md](docs/agents/git-workflow.md) | **In full, before your first push to any branch.** Conventional commits, branching, opening a PR, CodeRabbit review, merging |
| [docs/agents/issue-tracker.md](docs/agents/issue-tracker.md) | Working GitHub issues via `gh` |
| [docs/agents/triage-labels.md](docs/agents/triage-labels.md) | Labelling an issue |
| [docs/agents/domain.md](docs/agents/domain.md) | `CONTEXT.md` and ADR conventions |

## Rule precedence

This file and `docs/agents/*` win over `.claude/rules/ecc/**`. The ECC common rules are
language-agnostic defaults written for TypeScript projects; where they disagree with the
Python conventions or the test bar here, this repo's rules apply.

## GBrain search guidance

This repo is indexed in gbrain as source `spread-hunter-live`, pinned by `.gbrain-source` in
the repo root, so the commands below route here without a `--source` flag.

**Prefer gbrain over Grep for structural questions** — who calls a symbol, where
it is defined, what it references, what it calls. One query beats opening every
file:

```bash
gbrain code-def <symbol>       # where it is defined
gbrain code-refs <symbol>      # every reference
gbrain code-callers <symbol>   # who calls it
gbrain code-callees <symbol>   # what it calls
gbrain query "<question>"      # semantic search over this repo
```

**Read `status` before trusting an empty result.** `count: 0` means "nothing
found" only when `status` is `ready`. `not_built` or `indexing` means the call
graph is still being built and the empty list proves nothing — fall back to Grep
and say that is what you did.

**Use Grep instead** for literal text, config values, comments, and anything
added since the last sync. The index refreshes on every commit (a `post-commit`
hook) and nightly at 03:00; uncommitted work in progress is not in it. To
refresh now:

```bash
gbrain sync --source spread-hunter-live --strategy code
```
