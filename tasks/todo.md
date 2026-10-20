# #717 — Remove the retired reward vocabulary — task checklist

Derived from `tasks/plan.md`. Completion state lives in `tasks/plan.md`; this file is the
readable checklist.

- [ ] **T1 (S) [Backend/Logic]** — `core_brain/quotes.py`: reword the four visible strings,
  delete `reward_score()` and its call site, rewrite the ZERO ALLOCATION comment.
  Verify: `python -m pytest -q tests/test_quotes.py tests/test_live_quotes.py
  tests/test_legacy_ask_quotes.py tests/test_dynamic_offset.py
  tests/test_completable_pair_gate.py tests/test_market_quote.py`.
- [ ] **T2 (S) [Backend/Logic]** — Clear the remaining acceptance-search hits in
  `core_brain/` and `tests/`, re-point the four transient-refusal pins, add the vocabulary
  guard test.
  Verify: `python -m pytest -q tests/test_trader_loop.py tests/test_shadow_run.py
  tests/test_live_quotes.py tests/test_quotes.py tests/test_risk.py
  tests/test_wide_book_trial.py tests/test_rewards_purge.py
  tests/test_unified_universe.py` + the acceptance greps.
- [ ] **T3 (S) [Docs]** — `docs/agents/strategy.md`, `scripts/book_tape_recorder.py`, the two
  explainer HTML files, and the new `docs/issues/717-noticed-but-not-touching.md` ledger.
  Verify: acceptance greps stay empty, `python -m pytest -q tests/test_docs_layout.py`.

## Acceptance criteria (issue #717)

- [ ] `git grep -n "reward window\|reward quote\|reward minimum" -- core_brain/ tests/` → nothing
- [ ] `quotes.reward_score()` is gone, and no test imports it
- [ ] The zero-allocation comment no longer names a function that does not exist
- [ ] The tests that pinned the old wording were re-pointed, not deleted
- [ ] `python -m pytest -q tests/test_trader_loop.py tests/test_shadow_run.py
      tests/test_live_quotes.py` passes
