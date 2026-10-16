# Todo - #626

Branch: i626/purge-instantaneous-taker-ask-re-gating | Issue: #626

- [ ] T1 · Delete `CANCEL_REGATE_PAIR_COST` + trim retired docstring (`trader_loop.py`)
- [ ] T1 · Add `test_retired_regate_constant_is_gone` (RED first)
- [ ] T2 · Retarget historical fixtures to literal `"regate_pair_cost"`
- [ ] T2 · Add resting-bid survival tests (ask rise + retired-gate math + never-records)
- [ ] T2 · Focused suite green (`test_trader_loop.py` + `test_cancel_attribution.py`)
- [ ] T3 · Fix stale re-gate diagram/copy (`strategy_explainer.html`)
- [ ] Checkpoint · `tasks/plan.md` committed on this branch
