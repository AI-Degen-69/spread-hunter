# Noticed but not touching — #628 planning (2026-10-16, owner: planner)

| ID | Candidate | Discovering station | Evidence | Status | Resolved by |
|---|---|---|---|---|---|
| N1 | Brief names `stop_memory`, `market_sell`, `execute_exit` — none exist in tree; real seams are loop-local `stop_state` (`trader_loop.py:557+`) and `exit_single_buy` (`single_buy_saver.py:784`) | II | trader_loop.py:557, single_buy_saver.py:784 | dismissed | Naming drift in planning brief; plan used verified names |
| N2 | CodeRabbit plan comment on #628 proposes `cancelled`-row writes, `DUAL_RESTING`/`PAIR_LOCKED` overwrite via `save_lifecycle_state`, and shadow tests | II | PR #628 review comments | dismissed | Overlapping third-party proposal; minimal subset adopted in PR #634 |
| N3 | `single_buy_saver.py` STOP block persists HARD_STOP before executing the exit | II | single_buy_saver.py:1790 | dismissed | Deliberate restart-observability by-design for crash resilience |

