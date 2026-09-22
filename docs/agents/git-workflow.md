# Git and GitHub

Repo: `AI-Degen-69/spread-hunter-live` (`origin`), default branch `main`. CodeRabbit
reviews every pull request; the conventions below match its configuration, so its title
check passes on the first try.

## Delegation

Operator directive: the agent owns GitHub operations end to end -- commits, pushes,
branches, PRs, review rounds, and merges -- with full autonomy. It decides actions,
executes them directly, and keeps the operator informed with concise status updates
(e.g., `Working on [branch]...`, `Committed & Pushed...`, `PR Opened #...`, `Merged PR #...`).
No operator sign-off is required.

## Commits

Conventional Commits format: `<type>(<scope>): <imperative summary>` —
`fix(dashboard): anchor portfolio card to database marks`.

Types in use: `feat`, `fix`, `refactor`, `perf`, `test`, `docs`, `style`, `chore`,
`revert`. The scope is the package name. One logical change per commit; do not mix
formatting with behavior or refactors with features.

Never commit `.env`, keys, `data/*.db`, or logs. Commit as work completes rather than
batching a day's edits.

## Branches

Never commit straight to `main`. One branch per change, named `<type>/<short-kebab-desc>`
using the commit type — `fix/pair-completion-sizing`, `feat/live-portfolio-card`,
`docs/agent-handbook-refresh`.

## Pull requests

Push the branch, then open the PR with `gh pr create`:

```bash
gh pr create --title "fix(dashboard): anchor portfolio card to database marks" --body-file pr-body.md
```

- **Title: Conventional Commits format** (`<type>(<scope>): <summary>`), written by the
  agent. The `[TAG]` placeholder vocabulary was retired 2026-09-22 — CodeRabbit's UI
  auto-title instructions (if still configured) will simply not match; that is expected
  and harmless.
- **Body: must contain the line `@coderabbitai summary`.** This is the high-level summary
  placeholder. CodeRabbit replaces that line with a five-bullet plain-English summary. The
  rest of the body is yours.

Body template:

````markdown
@coderabbitai summary

## Why

One or two sentences. What was wrong, or what was missing.

## Test output

```
python -m pytest -q tests/test_<module>.py
<paste the real targeted-test output>
```

GitHub CI runs the full `python -m pytest -q` regression suite on Ubuntu and Windows; local
full-suite output is not required at the review/PR stations.

## How to verify

<the same How to verify block given to the operator — see docs/agents/verifying.md>
````

After opening, check the PR: if the body still reads `@coderabbitai summary` after a few
minutes, CodeRabbit did not process it — re-post the line as its own comment rather than
leaving an unprocessed placeholder.

**These two placeholders are the only permitted uses of the `@coderabbitai` handle at PR
creation.** They do not trigger a review. The review must be triggered explicitly after the
PR is opened, as described below.

## Review by CodeRabbit

CodeRabbit does **not** review this repo automatically — it is public with fewer than 10
stars, so every review starts from the manual trigger described below. It is configured
entirely through its repository UI: this repo has no `.coderabbit.yaml`, and adding one
would silently override every UI setting.

#### Where the handle is allowed

`@coderabbitai` may appear in exactly four places, and nowhere else:

| Use | Where |
| --- | --- |
| `@coderabbitai summary` | one line in the PR **body**, set once at creation |
| `@coderabbitai review` | its own comment, **only** to answer a "Trigger review" notice |
| `@coderabbitai resolve` | its own comment, closing only replied-to threads |

Every other use is banned, and `full review` is the one that costs real money.

**Never post `@coderabbitai full review`.** It re-scans the entire diff — all files,
including the ones already passed twice — and costs far more than the incremental pass it
duplicates. Asking for one is how a two-round review turns into six.

#### The manual trigger

This repo is public with fewer than 10 stars, so CodeRabbit does **not** review it
automatically. Every PR opens with this comment instead:

> 🔍 Trigger review
> This repository does not receive automatic reviews because it has fewer than 10 stars.

That notice is an **instruction to fire the trigger**, not permission to skip CodeRabbit.
Post it as its own comment:

```bash
gh pr comment <n> --body "@coderabbitai review"
```

Then immediately inspect the PR comments, reviews, and inline threads. If no review content exists,
use the required 5m → 4m → 3m → 2m → 1m countdown; do not substitute a 30-second wait.

| Reply | What it means | What to do |
| --- | --- | --- |
| A review starts (walkthrough, file comments) | The trigger worked | Work the round normally |
| `Review rate limited` / "wait 1 hour" | The hourly OSS allowance is spent | Give up on CodeRabbit for this round — fall back to the agent review below. Never wait out the window |
| `⚠️ Action not completed — Pull request is closed` | The PR was already merged | Too late; nothing gets reviewed |

**Trigger before merging.** A merged PR refuses the trigger outright. The order is: open
the PR → post the trigger → inspect immediately → use the countdown only if no review content
exists. A review trigger is used for the single review round; do not trigger a secondary review
after accepted fixes are pushed.

A green CodeRabbit status check proves nothing on its own: both `Review skipped: manual
review required for this OSS repository` and `Review rate limited` report `pass`. Read the
check's description, never its colour.

### Working the single round

Judgment and code changes stay with the agent. Do **not** invoke `@coderabbitai autofix`.

1. **Wait for the review to finish.** Read the PR status and review content; a green check
   alone is not proof of a completed review. If the review is not finished, use the required
   5m → 4m → 3m → 2m → 1m countdown.
2. **Triage every comment.** Accept technically correct findings and reject the rest with a
   concrete reason on each thread. Reply before resolving: `ACCEPT: <fix>` or
   `REJECT: <reason>`.
3. **Apply accepted fixes locally** and run targeted tests covering the touched code, such as
   `python -m pytest -q tests/test_<module>.py`. If a test fails, make a surgical correction or
   revert the offending change, reject it with the failure reason, and rerun the targeted tests.
4. **Batch all accepted fixes into one commit and push once.** Do not trigger a secondary
   CodeRabbit review. Post one concise summary comment, then use `@coderabbitai resolve` only
   for threads that already received an explicit reply.
5. Verify the full regression suite through GitHub CI; local targeted tests are pre-push
   verification, while `gh pr checks <n>` is the merge gate.

### Fix by severity, not by comment count

| Severity | Action |
| --- | --- |
| Critical | Always fix. Blocks merge. |
| Major on `core_brain/`, `scoring/`, `dashboard/server.py` | Always fix. Blocks merge. |
| Major elsewhere (docs, tests, tooling) | Fix if quick; otherwise decline with a reason |
| Minor | Batch the quick wins into the same commit, or decline the whole batch in one reply |
| Anything on a vendored path (`.claude/rules/**`, `.ecc/**`, `.agents/**`) | Decline in the summary comment. The fix is a path filter in the CodeRabbit UI, not an edit to vendored files |

### Stop condition

The PR is review-complete when the latest **automatic** review carries no Critical and no
Major touching `core_brain/`, `scoring/` or `dashboard/server.py`. Open Minors do not block
merge.

This pipeline runs exactly one focused review round. Do not start a secondary review after
pushing accepted fixes. If the review is rate-limited or remains stuck after the countdown,
use the objective agent fallback review and record that status honestly.

### CodeRabbit limit fallback

1. **Priority 1**: Get one CodeRabbit review. Fire `@coderabbitai review`, inspect immediately,
   then use the 5m → 4m → 3m → 2m → 1m countdown only when no review content exists.
2. **Priority 2**: If CodeRabbit reports a quota limit or remains stuck after the countdown,
   do not wait an hour. Execute the objective agent fallback review, checking logic, limits,
   tests, and regressions.
3. **Priority 3**: CodeRabbit outages or quota limits must never block development. Triage
   findings, apply accepted fixes locally, run targeted tests, verify CI, and proceed.


### Writing style

Write PR bodies and review replies the way CodeRabbit is configured to write: plain
English, no abbreviations, key point first, technical terms explained in one sentence.
Call out anything that risks a pair over $1.00 or a single unmatched buy.

CI (`.github/workflows/tests.yml`) must be green on both ubuntu and windows.

## Merging

The agent merges autonomously once CI (`.github/workflows/tests.yml`) is green on both
Ubuntu and Windows and CodeRabbit review blockers are resolved. Report concise status
when merged (e.g. `Merged PR #...`).
