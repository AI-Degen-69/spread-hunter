# SPEC.md — Issue #917: Multi-Strategy Tournament Design Addressing Live Market Adverse Selection

Branch: `plan_github_issue_917` | Issue: #917

## 1. Goal

Design and wire a 6-arm multi-strategy shadow tournament (`configs/tournaments/i917-multistrat-defense_arms.json`) with orthogonal defense profiles (`macro-non-sports`, `taker-snapper`, `fast-bailout-60s`, `wide-cushion-live`, `queue-gated-hybrid`, `control-baseline`) to counter live in-play adverse selection and pair-cap lockout observed in Tournament #905, ensure every arm's `BOT_*` / `FILTER_*` environment overrides take effect at runtime without altering shipped production defaults, and lock the pre-registered decision rule in `docs/runs/2026-11-01-917-multistrat-defense.md`.

## 2. Non-Goals (Out of Scope)

- Modifying shipped production default values on `MakerConfig` in `core_brain/config.py` or `SCORING_DEFAULTS` in `scoring/config.py`.
- Running live real-money venue commands (`quote`, `complete`, `trader_loop`, or starting the live dashboard trader).
- Mutating historical run databases in `data/tournaments/i799*`, `data/tournaments/i905*`, or `data/orders.db`.

## 3. Components & Behaviour

### 3.1 Runtime Environment Override Wiring (`core_brain/config.py`, `core_brain/trader_loop.py`)
- **`MakerConfig.from_env()` (`core_brain/config.py`):**
  - Parse `BOT_RISK_STRANDED_COMPLETION_ON` (`"1"`, `"true"`, `"yes"`, `"on"` → `True`; `"0"`, `"false"`, `"no"`, `"off"` → `False`) into `bot_risk_stranded_completion_on` (shipped default remains `False`).
  - Parse `BOT_RISK_STRANDED_MAX_WAIT_SEC` via `_bounded_float("BOT_RISK_STRANDED_MAX_WAIT_SEC", raw, 0.0, 86400.0)` into `bot_risk_stranded_max_wait_sec` (shipped default remains `300.0`).
  - Parse `BOT_QUOTE_BOOK_MAX_SPREAD_USD` via `_bounded_float("BOT_QUOTE_BOOK_MAX_SPREAD_USD", raw, 0.0, 0.30)` into `bot_quote_book_max_spread_usd` when `BOT_QUOTE_WIDE_BOOK_TRIAL` is not set (shipped default remains `0.06`), widening `bot_quote_book_max_spread_from_mid` to `max(window, val)` when needed.
- **Per-Arm Non-Sports Market Exclusion (`core_brain/trader_loop.py` `_market_specs`):**
  - When `BOT_MARKET_EXCLUDE_SPORTS_ON` or `FILTER_EXCLUDE_SPORTS` is set to a truthy value (`"1"`, `"true"`, `"yes"`, `"on"`), filter `gms` inside `_market_specs` (before `max_markets` slicing) to exclude any `GraduatedMarket` where `gm.event_live` is `True` or `scoring.selector.is_sports_or_esports(title=gm.title, slug=gm.slug, event_title=gm.event_title, sports_market_type=gm.sports_market_type)` is `True`.
  - Preserve the exact 17-key dict schema of `_market_specs` items (`tests/test_trader_loop.py`).

### 3.2 Six-Arm Multi-Strategy Tournament Configuration (`configs/tournaments/i917-multistrat-defense_arms.json`)
Define 6 orthogonal arms with full metadata (`name`, `label`, `color`, `profile`, `desc`, `thesis`, `env`):
1. `macro-non-sports`: Structural exclusion of in-play sports/esports (`BOT_MARKET_EXCLUDE_SPORTS_ON="1"`, `FILTER_EXCLUDE_SPORTS="1"`).
2. `taker-snapper`: Instant taker completion when `held_avg + opposing_ask < $0.99` (`BOT_RISK_STRANDED_COMPLETION_ON="1"`).
3. `fast-bailout-60s`: Rapid 60-second stranded wait and 5% drawdown stop (`BOT_RISK_STRANDED_MAX_WAIT_SEC="60"`, `BOT_RISK_STOP_MODE="PERCENTAGE_DROP"`, `BOT_RISK_STOP_MAX_DRAWDOWN_PCT="5.0"`).
4. `wide-cushion-live`: Deep volatility buffer quoting 5¢–10¢ from mid with 10¢ book spread tolerance (`BOT_QUOTE_SPREAD_OFFSET_MIN_CENTS="5"`, `BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS="10"`, `BOT_QUOTE_BOOK_MAX_SPREAD_USD="0.10"`).
5. `queue-gated-hybrid`: Fast 2-minute queue clearance gate with `balanced` preset (`BOT_QUOTE_TOURNAMENT_PRESET="balanced"`, `BOT_ORDER_GATE_QUEUE_CLEAR_ON="1"`, `BOT_ORDER_QUEUE_MAX_CLEAR_MIN="2"`).
6. `control-baseline`: Shipped loose baseline (`env: {}`) for direct benchmarking.

### 3.3 Pre-Registered Tournament Run Document & Verification Suite (`docs/runs/2026-11-01-917-multistrat-defense.md`, `tests/test_tournament_arms_i917.py`)
- Pre-register hypotheses, per-KPI sample sufficiency gates, separation criteria, 1440-minute horizon (with 360-minute interim checkpoint), and operator rehearsal commands in `docs/runs/2026-11-01-917-multistrat-defense.md`.
- Validate schema, port isolation, runtime `MakerConfig.from_env()` resolution, `_market_specs` non-sports filtering, and `python -m scripts.shadow_tournament --dry-run` pure JSON output in `tests/test_tournament_arms_i917.py`.

## 4. Acceptance Criteria

- [ ] `configs/tournaments/i917-multistrat-defense_arms.json` created with 6 distinct strategy profiles (`macro-non-sports`, `taker-snapper`, `fast-bailout-60s`, `wide-cushion-live`, `queue-gated-hybrid`, `control-baseline`).
- [ ] Every arm's environment overrides take effect at runtime (`MakerConfig.from_env()` and `_market_specs`) while shipped defaults remain 100% unchanged.
- [ ] Schema, naming, port allocation, config resolution, and isolation verified via `tests/test_tournament_arms_i917.py`.
- [ ] `python -m scripts.shadow_tournament --issue 917 --arms-file configs/tournaments/i917-multistrat-defense_arms.json --dry-run` passes cleanly with pure JSON output.
- [ ] Pre-registered decision rule and rehearsal protocol documented in `docs/runs/2026-11-01-917-multistrat-defense.md`.
