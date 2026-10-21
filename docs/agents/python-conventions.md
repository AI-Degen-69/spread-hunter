# Python conventions

Python 3.12. FastAPI + uvicorn for `dashboard/server.py`. Venue client:
`py-clob-client-v2`, `eth-account` (Polymarket CLOB). Tests: pytest
(`pytest.ini`: `testpaths = tests`, `pythonpath = .`).

These rules override `.claude/rules/ecc/common/coding-style.md`, which is written for
TypeScript.

## Naming

| Element | Convention | Evidence |
| --- | --- | --- |
| Files | `snake_case` | all modules |
| Functions | `snake_case` | 249 defs, 0 camelCase |
| Classes | `PascalCase` | `OrderRegistry`, `LiveFillEngine` |
| Constants | `SCREAMING_SNAKE_CASE` | `MIN_ORDER_SHARES`, `DEFAULT_TICK_SIZE` |

## Imports

Absolute, rooted at the repo. `pytest.ini` sets `pythonpath = .`.

```python
from core_brain.order_registry import OrderRegistry, DEFAULT_DB_PATH
from core_brain.quotes import Inventory, QuoteIntent
```

No relative imports (0 in the codebase). No wildcard imports in new code — the only two
are `scripts/rank_markets.py` and `scripts/rerank_loop.py`, legacy forwarders that re-export
their replacements. Modules open with `from __future__ import annotations` (20 of 21 in
`core_brain/`; only `__init__.py` omits it).

## Error handling

Raise domain-specific exceptions that name the refusal; do not swallow errors.

```python
class PairCompletionRefused(RuntimeError):
    ...

if size < MIN_ORDER_SHARES:
    raise PairCompletionRefused(
        f"Completable size {size:.4f} is below the venue minimum of "
        f"{MIN_ORDER_SHARES}."
    )
```

Narrow `except` clauses only (`except sqlite3.Error`, `except OSError`). A bare
`except Exception: pass` in an execution path hides money-losing state.

## Tests

- pytest, file pattern `test_*.py` in `tests/`.
- `tests/conftest.py` is hermetic: it scrubs credentials from `os.environ` and blocks
  every non-loopback socket. Opt out per test with `@pytest.mark.allow_network`.
- Platform-specific tests carry `@pytest.mark.skipif(sys.platform ...)`.
- CI runs the suite on ubuntu and windows via `.github/workflows/tests.yml`.

## Engine-specific traps

- **Never close a connection inside `with get_connection(...)`.** The context manager
  commits on exit; use `contextlib.closing`.
- `complete_pair` sizes against `ask_depth` (the asks ladder). `depth_at_or_above` reads
  **bids** and will oversize a buy.
- Fill attribution must not silently drop fills. Under-counting a position invites fresh
  exposure on top of it.

## Parameter & Environment Variable Naming

- **Ban `_frac` / `_fraction`:** In active configuration and logic, ratios in `0.0..1.0`
  must use `_pct` (`bot_quote_window_max_pct`, `bot_risk_hedge_emergency_pct`, `bot_eval_stat_gate_bankroll_pct`).
  The `_frac` / `_fraction` aliases those fields once answered to were deleted in #732, so
  `_pct` is the only spelling and a retired one is inert rather than translated.
- **Percentage-points exceptions:** Where a value is given in whole percentage points
  (e.g., `1.0` means 1%): `threshold_pct`, `bot_eval_stat_gate_threshold_pct`, and `*_return_pct`.
  These are explicitly documented exceptions and are never scaled or renamed.
- **Strict unit suffixes:** Every parameter name must carry its physical unit:
  - `_usd` for currency in USD (`bot_risk_exposure_max_naked_usd`,
    `bot_risk_exposure_max_order_usd`, `bot_risk_exposure_max_total_usd`).
  - `_cents` for price in cents.
  - `_shares` for share counts (`bot_quote_book_min_depth_shares`, `bot_quote_size_max_shares`).
  - `_sec` for seconds, `_ms` for milliseconds (`bot_latency_net_oneway_ms`).
  - `_offset` for additive price deltas (`bot_pos_skew_max_offset_cents`).
- **Canonical environment variables:** Each canonical environment variable is the
  canonical config field name in uppercase (`FILTER_*` for the market screener and
  `BOT_*` for the trading bot engine, e.g., `FILTER_LIVE_VEL_MIN_USD`,
  `FILTER_LIVE_MOVE_MIN_RANGE_CENTS`, `BOT_QUOTE_SIZE_MIN_SHARES`,
  `BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID`, `BOT_EVAL_STAT_GATE_BANKROLL_PCT`) and carries
  strict unit suffixes. The retired `HUNTER_*` variables are ignored: a loader
  reads the canonical name only, so a retired name in `.env` changes nothing.

