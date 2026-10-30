# Backlog — Issue #872 (currency-unit naming)

Out-of-scope candidates noticed while planning/building. Station III records them; only
Station VI resolves a row. An `open` row is never deleted.

## The `_cents` inventory the issue asked for

The issue's third open question was *"are there other `_CENTS` settings that this audit
missed?"*. Yes: ten more fields carry `_cents` in their name and **seven of them hold
dollars**, so the misnaming is wider than the three settings #872 renamed. None is converted
here, because each is its own decision — several hold sub-cent values, which an integer-cents
field cannot express without moving behaviour (the reason the adverse-drop stop stayed in
dollars too; see `tasks/plan.md` §2).

| Field | Shipped default | Real unit | Note |
|---|---|---|---|
| ~~`bot_quote_book_max_spread_cents`~~ | `0.06` | **dollars** (6¢) | compared to a price spread (`core_brain/risk.py:308`); **renamed `_usd` by #883** |
| ~~`bot_quote_price_band_min_cents`~~ | `0.10` | **dollars** (10¢) | compared to `mid` (`core_brain/quotes.py:377`); **renamed `_usd` by #883** |
| ~~`bot_quote_price_band_max_cents`~~ | `0.90` | **dollars** (90¢) | same comparison; **renamed `_usd` by #883** |
| ~~`filter_common_spread_max_cents`~~ (derived property) | `0.0205` | **dollars** (2.05¢) | `bot_quote_book_max_spread_usd × 41/120`; **renamed `_usd` by #883** |
| `bot_quote_spread_static_offset_cents` | `0.020` | **dollars** (2¢) | added to a price (`core_brain/quotes.py:118`) |
| `bot_pos_skew_max_offset_cents` | `0.015` | **dollars** (1.5¢) | sub-cent — no integer-cents form |
| `bot_pos_skew_min_reward_offset_cents` | `0.005` | **dollars** (0.5¢) | sub-cent |
| `bot_risk_circuit_decided_price_cents` | `0.02` | **dollars** (2¢) | compared to prices (`core_brain/risk.py:287`) |
| `bot_risk_circuit_widen_offset_cents` | `0.035` | **dollars** (3.5¢) | sub-cent |
| `filter_live_move_min_range_cents` | `2.0` | **cents** (2¢) | correct today; dashboard prints `¢` (`dashboard/server.py:5491`) |
| `bot_risk_pairs_complete_gain_cents` | `3.68` | unverified | no engine reader; printed only in `statistical_validation_run/artifacts.py:469` |
| `bot_risk_pairs_exit_cost_cents` | `3.67` | unverified | same |

`bot_quote_spread_offset_min_cents` / `_max_cents` are already integer cents and were made
strict by #872; they are the two in the list that never lied.

## Rows

| # | Candidate | Found in | Evidence | Owner | When | Status | Resolution |
|---|---|---|---|---|---|---|---|
| N1 | The remaining `_cents` fields still holding dollars (table above), four of them at sub-cent values (`0.015`, `0.005`, `0.035`), so an integer-cents migration there would move live offsets. Needs a per-field decision, not a sweep. | Station II (answering the issue's open question 3) | table above; `core_brain/config.py` | you | after #883 | open | **Partially resolved by #883**: the price band (both ends), the book-spread gate and the derived picking bar are now `_usd`. Still open: `bot_quote_spread_static_offset_cents`, `bot_pos_skew_max_offset_cents`, `bot_pos_skew_min_reward_offset_cents`, `bot_risk_circuit_decided_price_cents`, `bot_risk_circuit_widen_offset_cents`. |
| N2 | `bot_risk_pairs_complete_gain_cents` (3.68) and `bot_risk_pairs_exit_cost_cents` (3.67) have no reader outside a report footer, and no document states their scale. Their `_cents` name is decorative until something consumes them. | Station II (unit audit) | `grep -rn bot_risk_pairs_complete_gain_cents` → `core_brain/config.py:247`, `statistical_validation_run/artifacts.py:469` | you | next unit pass | open | — |
| N3 | `.env.example` documents `BOT_RISK_STOP_MODE=NONE\|DROP_CENTS\|DRAWDOWN_PCT\|BOTH`, but `STOP_LOSS_MODES` is `NONE\|ABSOLUTE_DROP\|PERCENTAGE_DROP` (`core_brain/single_leg_lifecycle.py:25`). Two of the four documented spellings are retired and one is misspelled. | Station III (editing the line above it) | `.env.example:61` vs `core_brain/single_leg_lifecycle.py:25` | you | next env-template pass | open | Untouched: it is a stale enum list, not a unit question, and #872 owes it nothing. |
| N4 | Saved `data/tournaments/*/plan.json` files replay the retired `*_CENTS` spellings. They load today through the alias window, but they are the reason the window cannot close until those runs are archived: dropping the alias turns every future resume of an old plan into a silently-ignored knob. | Station III (alias design) | `scripts/tournament_hub.py:153-157`, `scripts/resume_tournament_arms.py` replays saved `env` verbatim | you | when the alias is removed | open | The alias reader keeps them working, with one warning per key per process. |
