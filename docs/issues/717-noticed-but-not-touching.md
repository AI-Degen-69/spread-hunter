# 717 — noticed but not touching (Station III, 2026-10-20)

#717 removes a retired liquidity-reward vocabulary from the quoting engine. The venues we
trade are graduated spread markets: they pay **$0.00** in maker rewards and the KPI report
does not estimate them. The engine still spoke that model's language in its visible strings,
in one dead function and in one stale comment.

This ledger records what was found while removing it and **deliberately left alone**. Every
row is an `open` row with an owner and a when. Only Station VI (`vi-close-pipeline`) resolves
a row.

## What #717 changed (the deliberate part)

| Path | Reference | Decision | Justification |
|---|---|---|---|
| `core_brain/quotes.py:108-122` | `reward_score()` and its docstring | **removed** | A legacy diagnostic with exactly one caller — the `"score N"` figure it printed into the quote reason. It fed no sizing decision, and the number it produced described a reward program the venues do not run. |
| `core_brain/quotes.py:511-513` | the distance refusal, `"…c from mid > …c reward window"` | **changed** | The number is the quote window (`max_spread_from_mid`), so the string now says "quote window". The figures, the sides and the order are unchanged. |
| `core_brain/quotes.py:559-562` | the truncation refusal, `"reward window truncated the quote to …"` | **changed** | Same window, same reading: "quote window truncated the quote to …". The percentage, the ladder shares and the minimum are unchanged. |
| `core_brain/quotes.py:563-566` | the price-risk refusal, `"… under the …sh reward minimum"` | **changed** | "venue minimum", the phrase the same output already uses for the couple-allocation refusal two branches above. The price, the ladder and `cfg.min_quote_shares` are unchanged. |
| `core_brain/quotes.py:572-576` | the success reason, `"reward quote …c under mid …, score N"` | **changed** | Now `"quote …c under mid …"`. The waiver suffix and the dynamic-offset tag are appended exactly as before; only the retired prefix and the score figure are gone. |
| `core_brain/quotes.py:342-353` | the ZERO ALLOCATION comment | **changed** | It credited `reallocate`, a function that exists nowhere in the tree, and the retired `U4` milestone. It now states today's fact — a nonpositive `quote_shares` is an explicit no-quote signal, and `risk.size_for` returns 0 for it in every sizing mode, so a 0 can never be promoted to a minimum-size order — and keeps one line of the past incident. The refusal text below it is untouched. |
| `core_brain/risk.py:280` | the `book_health` docstring, "the whole reward window inside it" | **changed** | The window that `max_book_spread` can swallow is the quote window. Docstring only. |
| `core_brain/trader_loop.py:118` | `_classify_refusal`'s docstring, "reward window, mid band, …" | **changed** | Same list, same meaning; the transient examples are named in the engine's own vocabulary now. Docstring only. |
| `core_brain/unhedged_stop_loss.py:8,106` | "the 4.5c reward window" | **changed** | The stale `4.5c` figure is gone (the shipped engine window default is 0.15) and the window is named by what it is: the configured quote window. No threshold and no comparison moved. |
| `core_brain/config.py:921` | the `baseline` tournament preset description, "15c score window" | **changed** | "score window" named the retired reward score. The field it describes is `max_spread_from_mid` = 0.15 — the quote window. This string is operator-visible in the dashboard's preset list. |
| `tests/test_trader_loop.py:1517-1518, 1530-1531, 3198-3199`, `tests/test_shadow_run.py:1652-1653` | the four transient-refusal pins | **changed** | Re-pointed to the new wording, every assertion kept. The pins are opaque input text; they still classify as `REFUSED_TRANSIENT` because no reworded string contains a `LifecycleStop` marker. |
| `tests/test_rewards_purge.py:132` | "# tight reward window" | **changed** | The fixture is the venue's legacy `rewards` payload; the comment describes its spread limit. The dictionary beside it is untouched. |
| `tests/test_unified_universe.py:333` | "legacy venue reward windows (#681)" | **changed** | The venue's legacy `rewards.max_spread` field is a spread limit, and the test says so. |
| `docs/agents/strategy.md:83` | "`quotes.reward_score()` formats a log string and feeds no sizing decision." | **changed** | The sentence became false the moment the function was deleted. It now states that nothing in the quoting path computes a reward score. |
| `scripts/book_tape_recorder.py:22` | "The reward window, the offset knobs …" | **changed** | Docstring only. The tape recorder measures against the engine's quote window. |
| `docs/explanations/grace-hold-explainer.html:102-103` | "transient (wide book, cap, reward window…)" | **changed** | The explainer mirrors `core_brain/trader_loop.py:118`; both now name the quote window. |
| `docs/explanations/prudent_profile_and_strategy_guide.html:530` | "חלון ה-Reward Window (4.5¢) של Polymarket המזכה ב-Liquidity Rewards" | **changed** | The explainer told the operator that staying inside the window qualifies for Polymarket's Liquidity Rewards. Graduated spread markets pay $0.00, so the claim was false. The window and its figure stay; the retired program does not. |

## Noticed but not touching

| # | Noticed | Why out of scope | Owner | When | Status |
|---|---|---|---|---|---|
| N1 | `core_brain/risk.py:109-112` — the `size_for` docstring justifies the size floor with the retired rewards program: "THE FLOOR at `min_quote_shares`. rewardsMinSize is 50: below it an order earns no score at all while still buying inventory, so the honest answer is 0". | Docstring-only fix landed on main: rationale now reads "Below the venue's order minimum an order cannot be posted at all". Verified: `MakerConfig.min_quote_shares = 5`, venue `orderMinSize` read live in `filter_markets.py:929`, venue-minimum gate proven in `single_buy_saver.py:57-60`; `rewardsMinSize` appears nowhere in code — only in this ledger row. No behavior change, quoting suites green. | you | — | resolved |
| N2 | `core_brain/quotes.py:326-330` — the market-exited rationale reads "Giving up the rent is the point: rent is worth ~$50/day across the fleet". | A stale unmeasured figure attached to the "giving up" argument. Re-measuring or dropping the number is a data decision, not a wording change. | you | next vocabulary pass | open |
| N3 | `core_brain/unhedged_stop_loss.py:5-10, 100-110` — "only the second is worth giving up the rent for", "a WIDENED market still earns", "the only state that forfeits income". | In a graduated-spread strategy "rent" and "income" mean merge spread capture, not venue rewards. What they should now say is a content decision about the gate's own vocabulary. | you | next vocabulary pass | open |
| N4 | `scripts/filter_markets.py:8, 587, 763-793, 926` — the market filter still reasons in "reward score" / "reward-funded" terms around the venue's legacy `rewards` payload. | The `rewards` payload is a live venue field the filter still reads, so the prose around it needs its own pass over what the filter actually does now. | you | next vocabulary pass | open |
| N5 | `reward_offset`, `min_reward_offset` and `bot_pos_skew_min_reward_offset_cents` keep "reward" in their names. | Live config keys read by the engine and rendered by the dashboard. Renaming them is `docs/ideas/variable-names-refactor.md`'s job (SCOPE 2, phase 2). | you | with that idea's ticket | dismissed: already covered by `docs/ideas/variable-names-refactor.md` — a second ticket would duplicate it |
| N6 | The venue's own reward-program field names (`rewards.max_spread`, `rewardsMinSize`, `rewards_daily_rate`) still appear in the tree. | They are the venue's field names, read from the market payload. Renaming a venue field on our side would break the read. | you | — | dismissed: venue field names, not our vocabulary |
