# Noticed but not touching — #628 planning (2026-10-16, owner: planner)

| Noticed | Why out of scope | Status |
|---|---|---|
| Brief names `stop_memory`, `market_sell`, `execute_exit` — none exist in tree; real seams are loop-local `stop_state` (`trader_loop.py:557+`) and `exit_single_buy` (`single_buy_saver.py:784`) | Naming drift in the planning brief, not a code defect; plan uses verified names | documented |
| CodeRabbit plan comment on #628 proposes `cancelled`-row writes, `DUAL_RESTING`/`PAIR_LOCKED` overwrite via `save_lifecycle_state`, and shadow tests | Third-party proposal, partly overlapping; this plan adopts the minimal subset (DUAL_RESTING reset on full exit) with RED-first tests | documented |
| `single_buy_saver.py` STOP block persists HARD_STOP before executing the exit | Deliberate restart-observability; changing write order is a separate reliability issue | open |
