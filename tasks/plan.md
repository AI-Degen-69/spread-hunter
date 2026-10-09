# Implementation Plan — #433: Build Market Metrics Telemetry Script for Volume, Notional, and Depth

Branch: i433/build-market-metrics-telemetry-script | Issue: #433

## Intake & CodeRabbit Synthesis
- **Adopted from CodeRabbit:**
  - Standalone read-only module in `scripts/research_market_metrics.py` with CLI module execution support.
  - Import reuse: `full_book` from `scoring.markets`, `top_depth_usd` from `scoring.selector`, `_event_list` from `scripts.live_events_probe`, `LIVE_ROOT` from `core_brain.runtime_paths`.
  - Venue constants: `GAMMA_PAGE_SIZE = 100`, `GAMMA_MAX_PAGES = 5`, `TRADE_PAGE_LIMIT = 500`, `TRADE_MAX_PAGES = 10`, `WINDOW_SECONDS = 1800`, `DEFAULT_SAMPLE_SIZE = 50`, `MAX_SAMPLE_SIZE = 100`.
  - Paging active markets from Gamma until cap or short page.
  - Measure 24h volume from market payload (`volume24hr` or `volume_24h`).
  - Outcome depth: measure both token depths via `full_book` and `top_depth_usd(bids)`; report min across outcomes as `top3_bid_depth`.
  - Traded notional: query Data API `/trades`, deduplicate by `(transactionHash, asset, timestamp, price, size)`, sum `price * size` in `[now - 1800, now]`.
  - Statistical summaries: `count`, `min`, `p25`, `median`, `p75`, `max`, `mean` via `nearest_rank` (`ceil(fraction * n) - 1`).
  - Terminal table formatting and optional JSON report export to `reports/market_metrics_statistics_report_<timestamp>.json` on `--save` or `--output`.
  - Dedicated offline unit tests in `tests/test_research_market_metrics.py` with mock HTTP sessions.
- **Rejected from CodeRabbit:**
  - Over-splitting into 5 micro-tasks; consolidated into 3 vertical slices.
- **Unverified items:** None. All cited imports, signatures, and API structures verified against repository code.

## Goal & Acceptance Criteria
- Running `python -m scripts.research_market_metrics --sample-size 50` fetches live active markets and prints a statistical distribution table to stdout.
- Key metrics captured per market: `volume_24h`, `recent_traded_notional_30m`, and `top3_bid_depth`.
- Running with `--save` writes a structured JSON report to `reports/market_metrics_statistics_report_<UTC>.json`.
- Script is completely read-only and standalone (zero live trading calls, no writes to `data/orders.db`).
- Comprehensive unit test suite in `tests/test_research_market_metrics.py` passes without network dependencies.

## Improvement Proposal (Evidence-based)
- **Evidence:** High-activity markets may produce >500 trades within 30 minutes. In `scoring/markets.py:recent_trades`, only a single unpaginated request of `limit=500` is performed.
- **Classification:** Edge-case hardening (adopted by default).
- **Resolution:** `fetch_window_trades` will paginate trades up to a bounded cap (`TRADE_MAX_PAGES = 10`), stopping as soon as trades pass the 30m cutoff or return a short page. It returns a tuple `(trades, window_complete: bool)`. If the cap is reached before the 30m cutoff, `window_complete=False` is flagged and reported in telemetry so incomplete windows are visible rather than silently undercounting notional.

---

## Task Breakdown

### Task 1: Core Sampling & Metric Measurement Logic [Core/Logic] [Size: M] [x]
- **Target files:** `scripts/research_market_metrics.py`, `tests/test_research_market_metrics.py`
- **Depends on:** None
- **What is built:**
  - `active_markets(session, limit_per_page=100, max_pages=5)`: queries `https://gamma-api.polymarket.com/markets` with `active=true&closed=false`, paginates by offset, unwraps via `_event_list`.
  - `parse_binary_tokens(market)`: validates binary outcomes and extracts YES/NO token IDs.
  - `sample_markets(markets, size=50, seed=None)`: seeded random sample of usable markets.
  - `measure_market_volume(market)`: extracts float 24h volume.
  - `measure_outcome_depth(clob_host, token_id, session=None)`: fetches book and calculates `top_depth_usd(bids)`. Captures both tokens and calculates `top3_bid_depth = min(depth_yes, depth_no)` when both present.
  - `fetch_window_trades(session, condition_id, now, window_seconds=1800, max_pages=10)`: paginates trades from `https://data-api.polymarket.com/trades?market=...&takerOnly=true`, checks cutoff, returns trades and `window_complete`.
  - `notional_in_window(trades, cutoff)`: deduplicates trades by `(transactionHash, asset, timestamp, price, size)` and computes total notional.
  - Unit tests in `tests/test_research_market_metrics.py` with `_FakeResponse` and `_FakeSession` verifying all data extraction functions.
- **Verification:** `python -m pytest -q tests/test_research_market_metrics.py -k "test_data or test_sample or test_depth or test_trade"`

### Task 2: Statistical Summaries, Table Formatting & JSON Reporting [Core/Logic] [Size: S] [x]
- **Target files:** `scripts/research_market_metrics.py`, `tests/test_research_market_metrics.py`
- **Depends on:** Task 1
- **What is built:**
  - `nearest_rank(values, fraction)`: computes percentile rank using `ceil(fraction * n) - 1`.
  - `summarize_metric(values)`: computes `count`, `min`, `p25`, `median`, `p75`, `max`, `mean` using `statistics.fmean`, skipping `None` and tracking missing count.
  - `format_table(summaries, sample_size, pool_size, seed, error_count, incomplete_windows)`: formats aligned ASCII terminal table with footer metadata.
  - `build_report(summaries, market_records, seed, sample_size, pool_size)`: generates serializable JSON report dict with metadata, summaries, and individual market records.
  - Unit tests verifying percentile calculations (edge cases: empty, 1 item, 1..10 values, mixed None) and table rendering.
- **Verification:** `python -m pytest -q tests/test_research_market_metrics.py -k "test_stat or test_table or test_report"`

### Task 3: CLI Interface, File Output & Regression Gate [Core/Logic] [Size: S] [x]
- **Target files:** `scripts/research_market_metrics.py`, `tests/test_research_market_metrics.py`
- **Depends on:** Task 1, Task 2
- **What is built:**
  - `main(argv=None)`: CLI arguments parsing:
    - `--sample-size`: integer 1..100 (default 50, error if <1 or >100 with exit code 2).
    - `--seed`: optional integer seed (generates random seed if omitted and prints it).
    - `--save`: writes report to `reports/market_metrics_statistics_report_<YYYYMMDD_HHMMSS>.json`.
    - `--output`: custom report destination path.
  - Handles errors gracefully: venue unreachable (exit 1), unreadable market list (exit 1).
  - Preserves UTF-8 encoding, indented JSON, trailing newline on saved files.
  - Unit tests covering CLI execution, arguments validation, report writing, and error cases.
- **Verification:** Focused test suite `python -m pytest -q tests/test_research_market_metrics.py` and regression test suites.
