# 705 - noticed but not touching (Station II, 2026-10-20)

Found while planning #705. None of it is on this ticket's path; each row names who owns it and
how it should be handled later.

| # | Noticed | Why out of scope | Owner | When | Status |
|---|---|---|---|---|---|
| O1 | `docs/agents/git-workflow.md` still prescribes the `<type>/<slug>` branch format and an `@coderabbitai summary` PR-body placeholder, but the last four merged PRs (701–704, and this one) use `i<number>/<slug>` branches and omit the summary placeholder, triggering review with a separate `@coderabbitai review` comment. | Doc drift only; syncing it rewrites an agents doc nothing in #705 depends on, and mixing it into a dashboard-copy fix muddies the diff. | you | next time the git-workflow doc is edited for any substantive reason | fixed #705b |
| O2 | `dashboard/static/strategy_explainer.html` carries literal `$0.15 (15¢)` prose in the strategy narrative. Unlike the parameter cards this page describes the *shipped default*, not the configured value, so its literal is self-consistent today. | Forexam-style restatement of a fixed default is not the defect class from the issue; changing narrative copy is an editorial change CodeRabbit's plan also left out of scope. | you | with the next explainer copy pass | fixed #705b (restatement tails stripped, one rule with the cards) |
