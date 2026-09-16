# Todo: Issue #230

- [x] T1 [Backend/Logic] Register `--resolve-only` flag in `_main` (`core_brain/price_tape.py`)
- [x] T2 [Backend/Logic] Add early-return resolve-only branch (stamped + pending, no ticks)
- [x] T3 [Backend/Test] End-to-end CLI test in `tests/test_price_tape.py`
- [x] Gate: targeted `pytest tests/test_price_tape.py -q` green (53 passed)
- [x] Gate: full `python -m pytest -q` green (agent-run: 2125 passed, 2 skipped)
- [ ] Decision: pending-count proposal (re-read vs arithmetic) — adopt / defer / drop
- [x] Decision: ADOPTED re-read of `tracked_tokens()` after the pass (T2 implements it)
