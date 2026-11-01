# Plan: Multi-Strategy Tournament Design Addressing Live Market Adverse Selection

Branch: plan_github_issue_917 | Issue: #917

## Classification

- **Size tier**: Standard (2 existing modules touched, 3 new files created, 3 atomic tasks)
- **Task types**: Code (`[Backend/Logic]`), Docs (`[Docs]`)
- **Spec**: `SPEC.md`
- **Constraints**: `CONSTRAINTS.md`

---

## Step 0A — CodeRabbit Intake & Codebase Verification Note

- **CodeRabbit issue comments**: Checked `gh issue view 917 --comments`; only the `@coderabbitai plan` trigger comment was present (no automated plan comment returned).
- **Verified codebase assumptions (`[UNVERIFIED -> RESOLVED]`)**:
  1. **`MakerConfig.from_env()` env-override wiring (`core_brain/config.py`)**:
     - `MakerConfig` already defines `bot_quote_book_max_spread_usd: float = 0.06` (line 60), `bot_risk_stranded_completion_on: bool = False` (line 273), and `bot_risk_stranded_max_wait_sec: float = 300.0` (line 274), and `core_brain/single_buy_saver.py` consumes them at lines 2206, 2796, and 2797.
     - However, `MakerConfig.from_env()` (lines 320–572) did not parse `BOT_RISK_STRANDED_COMPLETION_ON`, `BOT_RISK_STRANDED_MAX_WAIT_SEC`, or `BOT_QUOTE_BOOK_MAX_SPREAD_USD` from `os.environ`. Wiring those three environment variables in `MakerConfig.from_env()` ensures `taker-snapper`, `fast-bailout-60s`, and `wide-cushion-live` take effect inside each shadow child process without changing any shipped default on `MakerConfig`.
  2. **Per-arm non-sports filtering (`core_brain/trader_loop.py:_market_specs`)**:
     - `scripts/shadow_tournament.py` spawns a single shared `scripts.filter_loop` process writing `runtime/markets.json`, while per-arm `env` overrides are passed to each arm's `core_brain.shadow_run` child process.
     - Enforcing `BOT_MARKET_EXCLUDE_SPORTS_ON=1` (or `FILTER_EXCLUDE_SPORTS=1`) inside `core_brain/trader_loop.py:_market_specs` filters out any `GraduatedMarket` with `gm.event_live == True` or `scoring.selector.is_sports_or_esports(...) == True` specifically for the `macro-non-sports` arm while allowing the remaining 5 arms to quote the full live feed.

---

## Step 4 — Interface Contracts (`type-design-analyzer` Checked)

### 1. `MakerConfig.from_env(cls) -> MakerConfig` (`core_brain/config.py`)
- **Signature**: Unchanged (`@classmethod def from_env(cls) -> "MakerConfig"`).
- **New Environment Variable Bindings**:
  - `BOT_RISK_STRANDED_COMPLETION_ON`:
    - Truthy (`"1"`, `"true"`, `"yes"`, `"on"`, case-insensitive) $\rightarrow$ `cfg.bot_risk_stranded_completion_on = True`
    - Falsy (`"0"`, `"false"`, `"no"`, `"off"`, case-insensitive) $\rightarrow$ `cfg.bot_risk_stranded_completion_on = False`
  - `BOT_RISK_STRANDED_MAX_WAIT_SEC`:
    - Parsed via `_bounded_float("BOT_RISK_STRANDED_MAX_WAIT_SEC", raw, 0.0, 86400.0, logger)` $\rightarrow$ `cfg.bot_risk_stranded_max_wait_sec`
  - `BOT_QUOTE_BOOK_MAX_SPREAD_USD`:
    - Parsed when `os.environ.get("BOT_QUOTE_WIDE_BOOK_TRIAL") is None` and `os.environ.get("BOT_QUOTE_BOOK_MAX_SPREAD_USD") is not None` via `_bounded_float("BOT_QUOTE_BOOK_MAX_SPREAD_USD", raw, 0.0, 0.30, logger)`:
      - Sets `cfg.bot_quote_book_max_spread_usd = val`
      - Ensures `cfg.bot_quote_book_max_spread_from_mid = max(cfg.bot_quote_book_max_spread_from_mid, val)` so wider spreads are not clipped by the default mid-distance cap when `BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID` is not explicitly set.
- **Invariants**:
  - Shipped default attributes on `MakerConfig()` remain untouched (`bot_quote_book_max_spread_usd == 0.06`, `bot_risk_stranded_completion_on == False`, `bot_risk_stranded_max_wait_sec == 300.0`).
  - `tests/test_config_divergence_guard.py`, `tests/test_config_single_source.py`, and `tests/test_wide_book_trial.py` remain 100% green.

### 2. `_market_specs(max_markets=None, registry=None, path=None, paired_arm=None) -> list[dict]` (`core_brain/trader_loop.py`)
- **Signature & Return Dict Schema**: Unchanged (retains the exact 17 dict keys checked in `tests/test_trader_loop.py`).
- **Pre-slice Per-Arm Sports / In-Play Filter**:
  - When `os.environ.get("BOT_MARKET_EXCLUDE_SPORTS_ON", "").strip().lower() in ("1", "true", "yes", "on")` or `os.environ.get("FILTER_EXCLUDE_SPORTS", "").strip().lower() in ("1", "true", "yes", "on")`:
    - Filters `gms` before `max_markets` slicing to exclude any `gm` where `bool(getattr(gm, "event_live", False))` is `True` or `is_sports_or_esports(title=gm.title, slug=gm.slug, event_title=gm.event_title, sports_market_type=gm.sports_market_type)` is `True`.

### 3. `configs/tournaments/i917-multistrat-defense_arms.json`
- **Format**: Top-level JSON array of 6 objects `{ "name": str, "hypothesis": str, "env": dict[str, str] }`, consumed by `scripts.shadow_tournament.load_arms_file` and `build_tournament_plan`.

---

## Step 5 — Committed Improvement Proposal

**Wire `BOT_RISK_STRANDED_COMPLETION_ON`, `BOT_RISK_STRANDED_MAX_WAIT_SEC`, and `BOT_QUOTE_BOOK_MAX_SPREAD_USD` in `MakerConfig.from_env()` and verify effective per-arm `MakerConfig` mutations in tests.**
Without these three env-var bindings in `MakerConfig.from_env()`, setting them in `configs/tournaments/i917-multistrat-defense_arms.json` would pass `scripts/shadow_tournament.py --dry-run` validation (because `shadow_tournament.py` only inspects `ArmPlan.env` keys) while the child `core_brain.shadow_run` processes silently execute with default values (`False`, `300.0`, `0.06`). Binding them in `MakerConfig.from_env()` and asserting the resulting `MakerConfig` attributes for every arm in `tests/test_tournament_arms_i917.py` guarantees that all 6 tournament arms execute their distinct defense mechanics in shadow rehearsal.

---

## Step 6 — Atomic Task Breakdown

### [x] Task 1: Wire defense env overrides in `MakerConfig.from_env()` and per-arm sports filtering in `_market_specs`
- **Complexity**: M
- **Category**: `[Backend/Logic]`
- **Primary Skill**: `test-driven-development`
- **Depends on**: none
- **Files**:
  - Modify: `core_brain/config.py`
  - Modify: `core_brain/trader_loop.py`
  - Create: `tests/test_tournament_arms_i917.py` (unit tests for `MakerConfig.from_env()` overrides and `_market_specs` sports/live exclusion)
- **Implementation steps**:
  1. Write failing tests in `tests/test_tournament_arms_i917.py` asserting that:
     - `BOT_RISK_STRANDED_COMPLETION_ON="1"` sets `MakerConfig.from_env().bot_risk_stranded_completion_on is True` (and `"0"` sets `False`).
     - `BOT_RISK_STRANDED_MAX_WAIT_SEC="60.0"` sets `MakerConfig.from_env().bot_risk_stranded_max_wait_sec == 60.0`.
     - `BOT_QUOTE_BOOK_MAX_SPREAD_USD="0.12"` (when `BOT_QUOTE_WIDE_BOOK_TRIAL` is unset) sets `MakerConfig.from_env().bot_quote_book_max_spread_usd == 0.12` and `bot_quote_book_max_spread_from_mid >= 0.12`.
     - `_market_specs` excludes `GraduatedMarket` entries with `event_live=True` or sports/esports classification when `BOT_MARKET_EXCLUDE_SPORTS_ON="1"` or `FILTER_EXCLUDE_SPORTS="1"`, and retains them when unset/0.
  2. Run `rtk pytest -q tests/test_tournament_arms_i917.py` and confirm RED.
  3. Implement the env-var parsers in `core_brain/config.py:MakerConfig.from_env()` and the `BOT_MARKET_EXCLUDE_SPORTS_ON` / `FILTER_EXCLUDE_SPORTS` filter in `core_brain/trader_loop.py:_market_specs`.
  4. Run `rtk pytest -q tests/test_tournament_arms_i917.py tests/test_config_divergence_guard.py tests/test_config_single_source.py tests/test_wide_book_trial.py tests/test_trader_loop.py` and confirm GREEN.
- **Verification**:
  - `rtk pytest -q tests/test_tournament_arms_i917.py tests/test_config_divergence_guard.py tests/test_config_single_source.py tests/test_wide_book_trial.py`

### [x] Task 2: Author `configs/tournaments/i917-multistrat-defense_arms.json` and full tournament plan tests
- **Complexity**: M
- **Category**: `[Backend/Logic]`
- **Primary Skill**: `test-driven-development`
- **Depends on**: Task 1
- **Files**:
  - Create: `configs/tournaments/i917-multistrat-defense_arms.json`
  - Modify: `tests/test_tournament_arms_i917.py`
- **Implementation steps**:
  1. Extend `tests/test_tournament_arms_i917.py` with failing tests verifying:
     - `configs/tournaments/i917-multistrat-defense_arms.json` loads via `load_arms_file` with the exact 6 arm names (`macro-non-sports`, `taker-snapper`, `fast-bailout-60s`, `wide-cushion-live`, `queue-gated-hybrid`, `control-baseline`).
     - Every arm has a non-empty `hypothesis` and `env` dict of strings, contains no retired env keys (`RETIRED_TOURNAMENT_ENV_KEYS`), and passes `build_tournament_plan` with `minutes=1440` and `bankroll=100.0`.
     - Applying each arm's `env` to `MakerConfig.from_env()` produces the intended defense parameters (e.g., `taker-snapper` has `bot_risk_stranded_completion_on=True` and `bot_risk_stranded_max_wait_sec=10.0`; `fast-bailout-60s` has `bot_pos_hold_max_sec=60.0` and `bot_risk_stranded_max_wait_sec=45.0`; `wide-cushion-live` has `bot_pos_price_max_pair_cost_usd=0.96` and `bot_quote_book_max_spread_usd=0.12`; `queue-gated-hybrid` has `bot_quote_velocity_cooldown_sec=30.0`, `bot_quote_min_queue_sec=8.0`, and `bot_pos_hold_max_sec=90.0`; `control-baseline` has `bot_quote_velocity_cooldown_sec=45.0`).
     - Running `python -m scripts.shadow_tournament --tournament-id i917-multistrat-defense --arms-file configs/tournaments/i917-multistrat-defense_arms.json --minutes 1440 --bankroll 100 --dry-run` exits 0 and emits valid 6-arm JSON.
  2. Run `rtk pytest -q tests/test_tournament_arms_i917.py` and confirm RED.
  3. Create `configs/tournaments/i917-multistrat-defense_arms.json` with the 6 orthogonal arms.
  4. Run `rtk pytest -q tests/test_tournament_arms_i917.py` and confirm GREEN.
- **Verification**:
  - `rtk pytest -q tests/test_tournament_arms_i917.py`
  - `rtk python -m scripts.shadow_tournament --tournament-id i917-multistrat-defense --arms-file configs/tournaments/i917-multistrat-defense_arms.json --minutes 1440 --bankroll 100 --dry-run`

### [x] Task 3: Pre-register tournament run doc `docs/runs/2026-11-01-917-multistrat-defense.md`
- **Complexity**: S
- **Category**: `[Docs]`
- **Primary Skill**: `documentation-and-adrs`
- **Depends on**: Task 2
- **Files**:
  - Create: `docs/runs/2026-11-01-917-multistrat-defense.md`
  - Modify: `tests/test_tournament_arms_i917.py` (add doc structure/command consistency assertion)
- **Implementation steps**:
  1. Add a test in `tests/test_tournament_arms_i917.py` verifying `docs/runs/2026-11-01-917-multistrat-defense.md` exists, references every arm name in `configs/tournaments/i917-multistrat-defense_arms.json`, and documents the 1440-minute horizon, 360-minute interim checkpoint, sufficiency gates (`pairs_completed >= 20`, `completion_rate_pct >= 55.0`, `bail_out_loss_rate_pct <= 25.0`), and `--dry-run` / launch / `--score-only` commands.
  2. Create `docs/runs/2026-11-01-917-multistrat-defense.md` following the structure of `docs/runs/2026-11-01-905-top5-rematch.md`, anchoring the `i905-top5-rematch` baseline context (`\$58.39` aggregate loss, `33%–43%` completion, `39%–57%` bail-out loss rate on live sports markets) and pre-registering the 6-arm hypotheses, decision rules, and rehearsal commands.
  3. Run `rtk pytest -q tests/test_tournament_arms_i917.py` and confirm GREEN.
- **Verification**:
  - `rtk pytest -q tests/test_tournament_arms_i917.py`
