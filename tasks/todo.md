# TODO - #708 unify the max_spread_from_mid description, triage rebate dead code

Branch: i708/unify-spread-docstring | Issue: #708

- [x] Task 1 - one description in `scoring/config.py`, `core_brain/config.py`,
      `core_brain/quotes.py`, `core_brain/trader_loop.py`, `tests/test_wide_book_trial.py`
- [x] Task 2 - guard test: the canonical block is byte-identical in both config modules
- [x] Task 3 - rebate triage: `pnl_crosscheck.py` docstring, `docs/agents/strategy.md`,
      `README.md`, `shadow-run-flow.html`, `polymarket-toolkit-survey.md`, delete
      `scoring/allocate.py`
- [x] Task 4 - guards: allocator gone, `rebate_est is None`, `total_with_rebate ==
      realized_pnl`; wrote `docs/issues/708-noticed-but-not-touching.md`
- [x] Focused verification run (eight suites, listed in `tasks/plan.md`)
- [x] Extra: repaired `tests/test_docs_layout.py`, failing on `main` since e2f648e
- [ ] PR opened, one CodeRabbit round, merged, issue closed
