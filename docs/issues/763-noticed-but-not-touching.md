# 763 — noticed but not touching (Station III)

#763 scopes condition-level close netting to pairs: `registry_naked_usd` nets
rescue closes per pair, `registry_committed_usd` counts post-cutoff fills plus
rescue-netted leftovers, and `_prior_exit_shares` credits only closes booked
while the pair existed. This ledger records what was found during the fix and
**deliberately left alone**.

## What #763 changed (the deliberate part)

| Path | Reference | Decision | Justification |
|---|---|---|---|
| `core_brain/single_buy_saver.py:690-765` | `_prior_exit_shares` gains `pair_id`, time gate `close.ts >= pair min posted_ts` | **changed** | A prior pair's rescue shares no longer explain a new pair's venue gap or shrink its sale; same-pair refill/partial-retry still nets. Callers `_check_positions` and the `exit_single_buy` netting step pass `pair["pair_id"]` through. |
| `core_brain/order_registry.py:2725-2810` | `registry_naked_usd` drops the `closed_conditions` exclusion, adds `pair_min`/`token_side`/`rescue_net` CTEs | **changed** | Re-entered pairs count in full; rescue leftovers net exactly; balanced merged pairs still read 0 via heavy-minus-light. One SQL statement, no `load_pair`. |
| `core_brain/order_registry.py:2813-2900` | `registry_committed_usd` cutoff + per-group rescue netting, `_close_cutoff_map` helper | **changed** | New fills/resting BUYs after the latest close commit; pre-close fills commit only via their rescue-netted leftover; uninformative closes hide rather than double-count. |
| `CONSTRAINTS.md`, `tasks/plan.md`, `tasks/todo.md` | new per-issue planning artifacts | **added** | Station II outputs; the three-way risk-figure rule lives in CONSTRAINTS.md. |

## Noticed but not touching

| # | Noticed | Why out of scope | Owner | When | Status |
|---|---|---|---|---|---|
| N1 | Two pairs live on one token at once share a single venue number: a rescue close landing between their opens credits both pairs' `_prior_exit_shares` and both pairs' `rescue_net`, so one close can net twice. `core_brain/single_buy_saver.py:690` and `core_brain/order_registry.py` (rescue CTEs) document it as a known limit. | Fixing it needs per-pair close attribution (`closes` has no `pair_id`, and the issue's Job Guards forbid adding one). The strategy opens one pair per market; sequential re-entry — the reported bug — is fixed. Refusing on an unexplained gap stays the safe direction for the concurrent case. | you | when a second live pair per condition becomes a real pattern, with its own ticket | open |
