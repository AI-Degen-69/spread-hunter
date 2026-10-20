docs: sync git-workflow branch/review conventions; align explainer notation

Resolves the two ledger rows in `docs/issues/705-noticed-but-not-touching.md`
(O1 and O2, recorded while shipping #705).

## Why

- O1: `docs/agents/git-workflow.md` still prescribed `<type>/<slug>` branches and an
  `@coderabbitai summary` PR-body placeholder, but the last five merged PRs (701–704, and
  #705's #710) use `i<number>/<slug>` branches, omit the placeholder, and trigger review
  with a separate `@coderabbitai review` comment. The doc now matches the practice that
  actually merges.
- O2: `dashboard/static/strategy_explainer.html` used the same `$X.xx (N¢)` restatement
  tails the parameter cards used to wear. Same rule as #705, applied uniformly: tails that
  restate a default in another unit (11 table cells, incl. `2.05¢ (0.0205)`) are stripped;
  explanatory prose that carries derivation information (`Derived as max_book_spread *
  41/120 (2.05¢)`) stays.

## Test output

Docs/static-copy only — no runtime behaviour touched, no test to run. GitHub CI's full
`python -m pytest -q` regression suite (Ubuntu + Windows) is the merge gate and must pass.

## How to verify

Open the dashboard (#705 is on `main`, so any local dashboard on current `main`) → the
strategy explainer page → the parameter tables. Every default cell now reads like
`$0.15`, `$2.05¢`-style single-unit values — no `(N¢)` tail after a dollar figure. The
"Derived Picking Spread Cap" prose still explains itself. For the doc: open
`docs/agents/git-workflow.md` — branch naming says `i<number>/<slug>` with examples
`i705/...`, the body template has no `@coderabbitai summary` line, and the handle table
lists `@coderabbitai review` as the post-open trigger.
