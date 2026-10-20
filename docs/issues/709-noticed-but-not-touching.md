Branch: i709/dashboard-scoring-config-fallback | Issue: #709

# Noticed but not touching — #709

| ID | Area | What was noticed | Status | Resolved-by | Owner | When |
| --- | --- | --- | --- | --- | --- | --- |
| N1 | `dashboard/server.py` `/api/parameters` | The ENGINE config load is unguarded (`cfg = load_cfg(for_display=True)`, ~4246) and the route has no error handler. After #707, an illegal `BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID` makes the engine load raise before this route's scoring fallback runs, and the whole Parameters panel 500s — engine scope included. #709's try/except covers only the scoring load and cannot prevent this. A fix would need a guard around the engine load plus an explicit route error path, deliberately not widened into this PR. | open | | #709 session (PR i709 branch) did the noticing; separate follow-up issue owns the fix | next dashboard-config session (file a dedicated issue) |
| N2 | `docs/agents/git-workflow.md` | The doc still prescribes a `@coderabbitai summary` placeholder in the PR body; the last merged PRs dropped that placeholder and instead trigger the review with a separate `@coderabbitai review` comment. Doc drift — fixing the doc inside this PR would mix process docs into a dashboard fix. | open | | #709 session noticed; docs housekeeping session owns | next touch of docs/agents/git-workflow.md (dedicated docs PR) |
