# SPEC — #674: Route the scoring loader's nine execution overrides through the canonical `BOT_*` names

Branch: `i674/canonical-bot-overrides-in-scoring` | Issue: #674

## Goal

`core_brain.order_manager` obeys the canonical `BOT_*` environment names. `scoring.config.load()` —
the second, independent loader that `scripts/filter_markets.py`, `scripts/filter_loop.py` and the
dashboard's screener cards use — reads only the deprecated `HUNTER_*` / `SPREAD_HUNTER_*` spellings
for nine execution settings. An operator who follows `.env.example` and sets
`BOT_QUOTE_SIZE_MIN_SHARES=200` therefore sizes the live bot at 200 shares and leaves the ranker at
the shipped 50, with no error and no warning. After this change each of those nine reads resolves
canonical-first through `core_brain.config._env_get`, keeping the legacy names as fallbacks with
their existing deprecation warnings.

Only *how a value is found* changes. Parsing, units, validation, branch conditions and shipped
defaults stay exactly as they are.

## The contract — one row per read site

Canonical wins. Old names are checked newest-first after it. A blank or whitespace-only value counts
as unset (`_env_get` semantics), which is the one documented compatibility exception.

| # | Canonical name | Legacy chain (newest → oldest) | Dataclass field(s) written | Unit rule |
| --- | --- | --- | --- | --- |
| 1 | `BOT_MARKET_PINNED_CONDITION_ID` | `HUNTER_MARKET` | `pinned_condition_id` (turns on the whole pinned branch) | `.strip()`; non-empty turns the branch on |
| 2 | `BOT_MARKET_TITLE` / `BOT_MARKET_URL` | `HUNTER_TITLE` / `HUNTER_URL` | `market_title`, `market_url` | unset → `""` |
| 3 | `BOT_QUOTE_SIZE_MIN_SHARES` | `HUNTER_MIN_QUOTE_SHARES`, `HUNTER_MIN_SIZE` | `min_quote_shares`, `quote_shares = max(n, 120)` | `int(float(raw))` |
| 4 | `BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID` | `HUNTER_MAX_SPREAD_CENTS`, `HUNTER_MAX_SPREAD` | `max_spread_from_mid` | **input is whole cents**, stored as dollars: `/ 100.0` stays |
| 5 | `BOT_QUOTE_GRID_TICK_SIZE_USD` | `HUNTER_PRICE_TICK`, `HUNTER_TICK` | `price_tick` | `float(raw)` |
| 6 | `BOT_QUOTE_WIDE_BOOK_TRIAL` | `HUNTER_WIDE_BOOK_TRIAL` | `wide_book_trial`, `max_book_spread`, `max_spread_from_mid` (widen only) | rehearsal-gated, finite, `0.0`–`0.30` |
| 7 | `BOT_RISK_GATE_PAIRS_RULE_ON` | `HUNTER_PAIRS_RULE` | `enable_pairs_rule` | only `0` / `false` / `off` (any case) turns it off |
| 8a | `BOT_LATENCY_NET_ONEWAY_MS` | `HUNTER_NET_ONEWAY_MS`, `HUNTER_CANCEL_NET_ONEWAY_MS` | `net_oneway_ms` | `float(raw)` |
| 8b | `BOT_LATENCY_CANCEL_VENUE_ACK_MS` | `HUNTER_CANCEL_VENUE_ACK_MS` | `cancel_venue_ack_ms` | `float(raw)` |
| 8c | `BOT_LATENCY_POST_VENUE_ACCEPT_MS` | `HUNTER_POST_VENUE_ACCEPT_MS` | `post_venue_accept_ms` | `float(raw)` |
| 9 | `BOT_QUOTE_BANK_BASELINE_USD` | `SPREAD_HUNTER_BANKROLL`, `HUNTER_BANKROLL` | `bankroll_usd`, `max_committed_usd` (both, from one value) | finite and `> 0`, else `ValueError` |

Rows 3–5 sit **inside** the pinned branch, exactly as they do today. Rows 6–9 sit outside it.

## Acceptance criteria

1. Each canonical name in the table overrides its field through `scoring.config.load()` with the
   legacy names absent, and produces the value the legacy spelling produces for the same input.
2. When a canonical name and any legacy name are both set, the canonical value wins.
3. Legacy-only setups still load, still emit the same deprecation warnings
   (`HUNTER_MIN_SIZE`, `HUNTER_MAX_SPREAD`, `HUNTER_TICK`, `#632` text, `logging.warning`), and
   produce identical config — except for the documented blank-value exception below.
4. A warning fires only when the *oldest* legacy name is the source. Canonical or newer-legacy wins
   log nothing.
5. `HUNTER_MAX_SPREAD="20"` / `BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID="20"` both yield
   `max_spread_from_mid == 0.20`. No double conversion, no removal of `/ 100.0`.
6. Shipped defaults are untouched: bankroll `1000.0`, qualification bar `120`, minimum shares `50`,
   spread `0.045`, tick `0.01`, pairs rule `True`, latency `3.93 / 150.0 / 81.0`, pair cost `0.995`.
7. `BOT_QUOTE_WIDE_BOOK_TRIAL` outside a declared rehearsal raises the same `ValueError` as the
   legacy name does today, and the message names the variable the operator actually set.
8. `scoring/config.py`'s class body is unchanged in shape: no new field, method, property or alias
   entry, so `tests/test_config_divergence_guard.py` keeps passing without edits.
9. Targeted suites green: `tests/test_config_single_source.py`,
   `tests/test_config_divergence_guard.py`, `tests/test_ranker_reads_env_file.py`,
   `tests/test_wide_book_trial.py`.

## Known compatibility exception (stated in the PR body)

`_env_get` treats a blank or whitespace-only value as unset. Scoring is stricter today: for size,
spread and tick a blank newer legacy name *blocks* the oldest legacy name (`ms is None` vs
`ms.strip()`); for latency and bankroll a whitespace-only newer name likewise blocks. After this
change the oldest legacy name applies and logs its warning. No existing test depends on the old
blocking behaviour, core already behaves the new way, and the ticket asks for core's order.

## Out of scope

- `core_brain/config.py` — already canonical-first.
- `dashboard/server.py` and the frontend — issue #672.
- The `_LEGACY_TO_CANONICAL` alias table and `tests/test_config_divergence_guard.py`.
- `.env.example` — the canonical `BOT_*` names are already documented there.
- Applying the spread bar to every market (core's second block), the pinned `min_t_remaining_sec=0.0`
  rule, `BOT_QUOTE_TOURNAMENT_PRESET`, and a display-only wide-book escape. These are divergences,
  not part of the nine reads: see `docs/issues/674-noticed-but-not-touching.md`.
