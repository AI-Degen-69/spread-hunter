## Why

Follow-up to #872, out of `docs/backlog/i872-currency-unit-inventory.md` row N1. Three fields and one derived property still ended in `_cents` while holding **dollars on the 0–1 price scale**:

| Before | Shipped value | Real unit | After |
|---|---|---|---|
| `bot_quote_price_band_min_cents` | `0.10` | dollars (10¢) | `bot_quote_price_band_min_usd` |
| `bot_quote_price_band_max_cents` | `0.90` | dollars (90¢) | `bot_quote_price_band_max_usd` |
| `bot_quote_book_max_spread_cents` | `0.06` | dollars (6¢) | `bot_quote_book_max_spread_usd` |
| `filter_common_spread_max_cents` (derived) | `0.0205` | dollars (2.05¢) | `filter_common_spread_max_usd` |

An analyst reading the suffix alone was off by 100x — the same defect #872 fixed for the dead band and the adverse-drop stop. The bounds were always correct; only the names lied.

**Every value is unchanged.** `0.10`, `0.90`, `0.06` and `0.0205` stay exactly where they were, the band is still 10¢–90¢, the book gate still refuses a spread wider than 6¢, and the derived picking bar is still 41/120 of it — 2.05¢, which the dashboard prints by multiplying by 100.

**No deprecation window, on purpose.** Unlike #872, none of these four names has an environment variable: `grep -rn "BOT_QUOTE_PRICE_BAND\|BOT_QUOTE_BOOK_MAX_SPREAD_CENTS"` finds no loader, so no `.env` can reference them and there is nothing to alias. `filter_common_spread_max_cents` is a `@property` since #572, not a dataclass field, so it cannot be passed to the constructor either way.

Touched: the four names in `core_brain/config.py`, every reader (`quotes.py`, `risk.py`, `scoring/selector.py`, `scoring/config.py`, `scripts/filter_markets.py`, `statistical_validation_run/run.py`), four dashboard parameter cards (`name`/`key` only — the rendered `$0.10`, `$0.90`, `$0.06` and `2.05¢` text is byte-identical), the strategy explainer page, and the tests that pin them.

Nothing here risks a pair over $1.00 or a single unmatched buy: no quoting, fill, merge or settlement arithmetic changes, and no live venue command was run.

## Test output

```
$ python -m pytest -q tests/test_config.py tests/test_config_single_source.py \
    tests/test_config_divergence_guard.py tests/test_config_for_display.py \
    tests/test_market_selection_bars.py tests/test_wide_book_trial.py tests/test_quotes.py \
    tests/test_risk.py tests/test_live_quotes.py tests/test_completable_pair_gate.py \
    tests/test_rc_fixes.py tests/test_redesigned_filter_pipeline.py \
    tests/test_statistical_validation_run.py tests/test_dashboard_server.py \
    tests/test_ui_276_explainer_tokens.py tests/test_sidebar_prototype.py \
    tests/test_ts_frontend_present.py
690 passed in 41.53s
```

GitHub CI runs the full `python -m pytest -q` regression suite.

## How to verify

Read-only throughout. No orders are placed and START is never pressed.

**1. The four values read where they always did.** In a clean shell:

```powershell
python -c "from core_brain.config import load; c=load(); print(c.bot_quote_price_band_min_usd, c.bot_quote_price_band_max_usd, c.bot_quote_book_max_spread_usd, c.filter_common_spread_max_usd)"
```

Expect exactly `0.1 0.9 0.06 0.0205`. A failed check prints `AttributeError` (a reader still on the old name) or a different number (a unit slip).

**2. The dashboard still shows the same money and cents.** Start `python -m dashboard.server`, open `http://127.0.0.1:8799`, and read the parameters panel:

- **Price Band Min** → `$0.10`, **Price Band Max** → `$0.90`
- **Book Spread** gate → `$0.06`
- **Derived Picking Spread Cap** → `2.05¢ (0.0205)`

A failed check reads `$0.00`, shows a card whose parameter name is missing, or prints something other than `2.05¢`.

**3. The strategy page names the new keys.** Same dashboard, open the strategy explainer and find the price-band and book-spread rows: they now read `bot_quote_price_band_min_usd`, `bot_quote_price_band_max_usd`, `bot_quote_book_max_spread_usd`, and the derived row says `bot_quote_book_max_spread_usd * 41/120` — still `2.05¢`. If an old `_cents` name is still on that page, the doc drift is back.

Closes #883
