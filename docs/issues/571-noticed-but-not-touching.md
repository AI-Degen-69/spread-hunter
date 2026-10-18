# #571 — Noticed but not touching (Owner: planning agent, When: future issue)

Code-level items seen during Station II, deliberately left out of this diff:

1. Missing `gamma_host` read in the `__main__` blocks of consumer scripts
   (flagged in the CodeRabbit plan). Existing defect, different files — needs
   its own issue, not a drive-by in a config refactor.
2. The #558–#560 "guard scans `core_brain/` for stray literals" could not be
   located in the tree. The confidence-literal surface (`1.645`/`1.96` in
   statistical modules + tests) is real but unlinked to this ticket; if a guard
   exists it needs a pointer before anyone claims compliance with it.
3. `reward_offset` fallback inside `dynamic_offset_for` has no dedicated test.
   Excluded from this ticket by scope; candidate for a quoting-logic issue.
4. Pair-cost and missing-side refusals on the ask-branch: T4's
   `tests/test_legacy_ask_quotes.py` now covers the pair-cost refusal, the
   missing-side refusals remain untested. Same — quoting-logic issue, not this
   refactor.
5. `objective` already agrees (`"spread_capture"` both files) while the issue
   body still describes a pricing-mode disagreement — issue text is stale on
   that point; no action needed, recorded so nobody "fixes" it back apart.
6. `core_brain/quotes.py` imports `replace` from `dataclasses` and never uses it
   (pre-existing on `main`, verified) — this ticket may touch that file's
   comments only, so the import stays. One-line cleanup for a quoting-logic
   issue.
7. `core_brain/trader_loop.py:_market_cfg`'s docstring still describes
   `objective="rewards"` as what is "pinned on every market" while the code pins
   `"spread_capture"` (pre-existing, same purge #594 renamed the label).
   Documentation-only drift, out of this ticket's scope.
