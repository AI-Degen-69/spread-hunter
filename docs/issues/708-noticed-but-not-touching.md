# 708 — noticed but not touching (Station III, 2026-10-20)

#708 is a documentation-and-triage ticket: one field (`max_spread_from_mid`) gets **one**
description, and every `rebate` reference in the tree gets a keep-or-remove decision with a
written reason. Nothing here is on the money path, and nothing below was changed by it.

The operator directive this answers, verbatim: *"any rebate related stuff not being used for
other things should be gone."* "Gone" means removed **only** where the reference is dead. The
live contracts stay, and each one is named below with the reason it stays.

## Reference triage

| Path | Reference | Decision | Justification |
|---|---|---|---|
| `core_brain/kpi.py:2349-2356` | `rebate_est`, `rebate_est_note`, `total_with_rebate` | **kept** | Emitted output schema. Graduated spread markets pay $0.00, so `None` is the honest value and `total_with_rebate == realized_pnl` by construction. Dropping a key would break every reader of the KPI report. |
| `core_brain/kpi.py:1176-1235` | `rebate_est` in the base and pessimistic sensitivity variants | **kept** | Same schema on both variants; the pessimistic variant must never invent rebate income. |
| `core_brain/pnl_crosscheck.py:57,60` | `CREDIT_TYPES`, `ACTIVITY_TYPES` | **kept** | Real venue credits arrive under these activity types. The replay has to recognise them or it under-counts the cashflow it exists to measure. `tests/test_pnl_crosscheck.py` covers them. |
| `core_brain/pnl_crosscheck.py:319,338,374` | the `maker_rebates` parameter, `expected_gap` | **kept** | The expected gap is `taker fees - maker rebates`. The adjustment is optional, the CLI does not pass it, and removing the parameter would remove the only way to judge the gap. |
| `core_brain/pnl_crosscheck.py:10,18,28-29,140,325` | the docstring's rebate prose | **changed** | Rewritten to keep venue credits separate from trading cashflow and to state that the command-line tool omits the `maker_rebates` adjustment. No executable line was touched. |
| `core_brain/venue_fees.py:11,98` | "maker rebates" sign-safety comment | **kept** | The fee-tier math is load-bearing: the comment is why a sign flip cannot turn a rebate into a charge. |
| `core_brain/order_manager.py:443` | historical measured-run comment | **kept** | History: what the measured run showed at the time. Rewriting it would hide what was true when it was written. |
| `core_brain/quotes.py` — module docstring, `reward_score()`, `_decide_quotes_from_mid()` | the "earning rent" / "it also scores" prose | **changed** | That passage described a liquidity-reward program this strategy no longer runs. `reward_score()` itself stays; only its docstring and the stale passages around it changed. The docstring also still named the retired `objective == "rewards"` literal, while the code pins `"spread_capture"`. |
| `core_brain/trader_loop.py:399-415` | `_market_cfg()`'s `objective="rewards"` docstring | **changed** | Same stale literal. The code pins `objective="spread_capture"`, and the docstring now says so. |
| `scoring/config.py:52-61` | the `max_spread_from_mid` description | **changed** | One canonical description, byte-identical to the copy in `core_brain/config.py`, pinned by `tests/test_config_single_source.py`. |
| `scoring/config.py:54` | "Maker rewards/rebates are $0.00 in graduated spread markets; accrual is disabled." | **kept** | Accurate, and the fact the field description rests on. |
| `statistical_validation_run/artifacts.py:411,513-514,601-602` | the "Rebate income: None" report lines | **kept** | They are the report's guard against inventing income. |
| `docs/prompts/statistical-validation-run.md:45` | "Do not fabricate rebate income" | **kept** | The same guard, in the prompt that produces the report. |
| `dashboard/static/strategy_explainer.html:512` | "rebate accrual is disabled" | **kept** | Accurate, and operator-facing. |
| `docs/agents/strategy.md:21-28` | the "Maker rebates are extra" section | **changed** | It claimed the bot qualifies for the liquidity-reward program, quoted $0.22/day, and cited the `config.rebate_rate` field removed in #594. It now states the $0.00 accrual and that a real venue credit would be a side accrual kept out of trading PnL. |
| `docs/agents/strategy.md:80-84` | the allocator paragraph | **changed** | `scoring/allocate.py` is deleted, so the paragraph naming it went with it. The `quotes.reward_score()` sentence stayed. |
| `README.md:24` | "Maker rebates are a side accrual" | **changed** | Now states the $0.00 accrual on graduated markets and that the KPI report does not estimate it. |
| `docs/explanations/shadow-run-flow.html:149` | "No rebate, or settled-outcome pricing" | **changed** | The real reason a side is skipped is that the quote is too far from mid to fill. |
| `docs/ideas/polymarket-toolkit-survey.md:135` | "`fee_rate` ... for the rebate estimate only" | **changed** | `fee_rate` is a taker-completion cost input; the rebate-estimation rationale is obsolete. |
| `tests/test_wide_book_trial.py:154-157` | the "venue REBATE-qualification window" description | **changed** | Both copies of the field are the quote window; the two shipped defaults (0.15 engine, 0.045 picker) are deliberate. |
| `tests/test_milestone7_telemetry.py`, `tests/test_stat_validation_artifacts.py`, `tests/test_pessimistic_sensitivity.py`, `tests/test_pnl_crosscheck.py`, `tests/test_venue_fees.py`, `tests/test_order_manager.py`, `tests/test_single_buy_saver.py`, `tests/test_rewards_purge.py` | assertions and comments on `rebate_est`, `maker_rebates`, `rebate_rate` | **kept** | They pin the live contracts and the purged-field guards. `tests/test_rewards_purge.py` gained the allocator guard. |
| `scoring/allocate.py` | the whole module | **removed** | No tracked importer, no test, and it models the retired reward-pot split. Removed because nothing reads it and the model it implements is retired — not because a dependency was missing; none is. |
| `docs/issues/690-noticed-but-not-touching.md` | the O4 status cell | **not applicable** | That ledger was deleted in e2f648e ("prune stale worktrees and closed-issue artifacts"), which removed every closed issue's ledger. It is not resurrected to change one cell. **#690 O4 is closed by this ticket** — the three conflicting descriptions of `max_spread_from_mid` are now one description. |

## Noticed but not touching

| # | Noticed | Why out of scope | Owner | When | Status |
|---|---|---|---|---|---|
| N1 | `core_brain/quotes.py` still prints reward vocabulary in visible messages: "... c from mid > 15.0c reward window", "reward window truncated the quote to ...", "under the 5sh reward minimum", "reward quote 3.0c under mid 0.512, score 41". | These strings are output the operator reads in the dashboard and the skip log. Rewording visible output is a deliberate change, not a docstring fix. | you | published | published #717 |
| N2 | `quotes.reward_score()` still exists and still produces the "score N" figure in the quote reason. | A legacy diagnostic that feeds no sizing decision. Removing it changes the quote reason string, so it belongs with N1. | you | published | published #717 |
| N3 | `reward_offset` and `min_reward_offset` keep "reward" in their names, and `bot_pos_skew_min_reward_offset_cents` too. | `docs/ideas/variable-names-refactor.md` already proposes these renames. A rename touches the dashboard parameter keys and the config loaders, which is a change of behaviour surface, not of prose. | you | with that idea's ticket | dismissed: already covered by `docs/ideas/variable-names-refactor.md` (SCOPE 2, phase 2) — a second ticket would duplicate it |
| N4 | `pnl_crosscheck.ACTIVITY_TYPES` lists `MAKER_REBATE` but not the literal `REBATE` that `CREDIT_TYPES` accepts. | The venue has not emitted a bare `REBATE` activity type. Adding one would be a guess about the feed's shape. | you | published | published #718 |
| N5 | #690 O1: `dashboard/server.py` renders the `max_spread_from_mid` card as `$0.15 (15c)` with a hardcoded suffix. | Cosmetic dashboard copy, published as its own issue (#705). | you | #705 | dismissed: the suffix is gone — the card now renders `f"${cfg.max_spread_from_mid:.2f}"` and #705 is closed |
| N6 | `core_brain/quotes.py:339` credits a function named `reallocate`, which no longer exists anywhere in the tree. | Not a rebate reference. It is a stale comment in the decision path, and a stale comment in the money path is a separate change from this ticket's scope. | you | published | published #717 |
| N7 | Two showcase files sit outside `docs/issues/`: `docs/presentations/695-presentation-retired-env-shared-module.html` and `docs/explanations/498-presentation-tournament-run.html`. The layout contract says issue showcases belong in `docs/issues/`, and `tests/test_docs_layout.py` only scans the top level of `docs/`, so it does not see them. | Both are closed issues. The e2f648e sweep deletes a closed issue's showcase artifacts, so these two are leftovers the sweep missed — they want sweeping, not moving, and that is the sweep's job, not a docstring ticket's. | you | published | published #719 |
