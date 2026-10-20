# TODO - #708 unify the max_spread_from_mid description, triage rebate dead code

Branch: i708/unify-spread-docstring | Issue: #708

- [ ] Task 1 - one description in `scoring/config.py`, `core_brain/config.py`,
      `core_brain/quotes.py`, `core_brain/trader_loop.py` (comments/docstrings only)
- [ ] Task 2 - guard test: the canonical block is byte-identical in both config modules
- [ ] Task 3 - rebate triage: `pnl_crosscheck.py` docstring, `docs/agents/strategy.md`,
      `README.md`, `shadow-run-flow.html`, `polymarket-toolkit-survey.md`, delete
      `scoring/allocate.py`
- [ ] Task 4 - guards: allocator gone, `rebate_est is None`, `total_with_rebate ==
      realized_pnl`; write `docs/issues/708-noticed-but-not-touching.md`
- [ ] Focused verification run (eight suites, listed in `tasks/plan.md`)
- [ ] PR opened, one CodeRabbit round, merged, issue closed
