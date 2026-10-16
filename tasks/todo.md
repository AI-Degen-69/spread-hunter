# Todo - #627

Branch: i627/anchor-entry-pair-cost-and-passive-break-even | Issue: #627

- [x] T1 — Entry pair-cost gate in `_decide_quotes_from_mid` (`quotes.py`)
- [x] T1 — New `tests/test_quotes.py` entry tests (over-cap, tie, noise, held, repair, lifecycle)
- [x] T1 — Regressions green (`test_live_quotes` + `test_completable_pair_gate` + `test_trader_loop`)
- [x] T2 — Read-only `break_even` anchor in `summarize_state` (`registry_state.py`)
- [x] T2 — Anchor tests (first-fill, ties, NULL ts, SELL/dup ignored, restart, zero-mutation)
- [x] T2 — Regressions green (`test_registry_state` + `test_completable_pair_gate`)
- [x] Checkpoint — `tasks/plan.md` committed on this branch
