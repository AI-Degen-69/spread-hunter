# Plan — Station II: #632 Standardize parameter & env naming

Branch: main | Issue: #632
Title: [REFACTOR] Standardize parameter/env naming: purge frac, enforce unit suffixes
Size: Standard (cross-cutting rename, no behavior change) | Type: Code + Docs
Labels: ready-for-agent | Base: main == origin/main, 0/0 (2026-10-16), tree clean.

## Job-guard note (branch deviation, deliberate)
- Step 0B prescribes `i632/<slug>`, but Job Guards order planning on synced `main`
  with exit `tasks/plan.md written, no strategy code modified`, and forbid stale
  refs (#628, #634). Planning stays on `main`, writes only the planning set.
  Station III creates the feature branch.
- Step 0A.0: root `CONSTRAINTS.md` carried #628's header, `SPEC.md` #622's — both
  merged. Both replaced wholesale for #632. No `tasks/` dir existed.

## CodeRabbit intake (adopted / rejected / unverified)
- ADOPTED: 2-phase skeleton (code renames + aliases + env fallback; then
  explainer/docs); `cancel_net_oneway_ms` read-only alias pattern; keep shared
  KPI output keys; keep venue constants with clarified comment; TDD-first;
  `tests/test_quotes.py` EXISTS (16 tests) — no creation needed.
- REJECTED: stale line numbers only; `test_market_quote.py` `wf` is positional
  forward, not a keyword; no `threshold_pct` rename (only the documented
  percentage-points exceptions stay).
- [UNVERIFIED] for build: `decide_quotes` refusal return shape at window
  boundary; emergency-hedge fixture two-sidedness; mermaid old-name hits.

## Spec (embedded)
Goal: one canonical lowercase name per setting with explicit unit suffix; zero
`_frac`/`_fraction` field/param names in active code; canonical `HUNTER_*`
first + legacy fallback + warning; explainer/docs show names + badges + scope.
Contract: all defaults and math unchanged (0.80, 0.40, 0.01, 0.015, 200.0,
bounds 0.0-1.0, cents/100). Out of scope: rescaling, deleting aliases,
`data/orders.db`, opening commands (quote/complete/Trader/START).
Acceptance: (1) zero `_frac`/`_fraction` fields on BOTH MakerConfigs (live
today: core 4, scoring 3); (2) `max_skew_offset` + `min_book_depth_shares`
fields with legacy read aliases; (3) canonical env loads, legacy `.env` works;
(4) explainer lowercase + badges + scope; (5) focused suites green
(NOTE: `tests/test_risk.py` DOES NOT EXIST — build creates it or corrects the
command; recorded in CONSTRAINTS.md).

## Interface contract (paths locked; line numbers re-derive at build)
- `core_brain/config.py` MakerConfig (frozen): `max_skew`->`max_skew_offset`
  (0.015), `emergency_hedge_frac`->`_pct` (0.8), `couple_risk_frac`->`_pct`
  (0.01), `quote_window_frac`->`_pct` (0.40), `stat_gate_bankroll_fraction`->
  `_pct` (0.01), `min_book_depth_sh`->`min_book_depth_shares` (200.0). Legacy
  read-only `@property` aliases beside `cancel_net_oneway_ms`. Frozen =>
  set raises AttributeError. Legacy ctor keywords REJECTED (TypeError);
  move all in-repo call sites in the same change (deliberate).
- `scoring/config.py` MakerConfig: same for its 3 fields (no couple_risk).
- `load()`: canonical-first + `logging.warning` fallback (NOT warnings
  module): `HUNTER_STAT_GATE_BANKROLL_PCT`->legacy `_FRACTION`;
  `HUNTER_MAX_SPREAD_CENTS`->`HUNTER_MAX_SPREAD` (BOTH blocks);
  `HUNTER_PRICE_TICK`->`HUNTER_TICK`; `HUNTER_MIN_QUOTE_SHARES`->
  `HUNTER_MIN_SIZE`; `HUNTER_MAX_QUOTE_SHARES`->`HUNTER_MAX_SHARES`.
  Bounds/conversions unchanged.
- `quotes.py`: `range_fraction`->`range_pct` (0.4); `window_frac`->`window_pct`
  + refusal text. `order_manager.py:3331` getattr -> canonical.
- `risk.py`: `cfg.max_skew`->`max_skew_offset`; `cfg.min_book_depth_sh`->
  shares name. `kpi.py`: rename param/local only (`bankroll_pct`,
  `kelly_pct`); KEEP dict keys `"bankroll_fraction"`/`"kelly_fraction"`.
- `scoring/allocate.py`: `capture_frac`->`capture_pct`, `max_frac`->
  `max_pct` (+internal call). `reachable_fraction` UNCHANGED (func name).
- `venue.py`: keep MAX_* constants; comment-only rewrite (import-time
  snapshot; enforcement via `order_caps()`).
- Explainer rows ~508-545: MAX_ORDER_USD, MAX_TOTAL_USD, max_skew,
  emergency_hedge_frac, min_book_depth_sh -> lowercase + badges + scope.
- MUST NOT MODIFY: `data/orders.db`; dated history (`docs/superpowers/
  specs/2026-08-*`, `docs/runs/`, `docs/ideas/`, `docs/issues/`);
  `derive_dynamic_caps`, `apply_tournament_preset`, `_sanitize_pct`,
  `threshold_pct`/`stat_gate_threshold_pct`/`*_return_pct` (pct-points
  exceptions: 1.0 means 1%, documented not renamed).

## Improvement proposal (adopted, simplification/hardening)
Evidence verbatim: `config.py:1547: sgb = os.environ.get(
"HUNTER_STAT_GATE_BANKROLL_FRACTION")` + `kpi.py:244:
"bankroll_fraction": cfg.stat_gate_bankroll_fraction,` — field, loader kw,
and reader must move in ONE atomic change or the gate/dashboard breaks
silently. Tasks below are shaped that way. No operator question needed.

## Tasks (4, dependency-ordered, risk-first)
### [x] T1 [S][Backend/Logic] Rename MakerConfig fields + read aliases (both classes)
Depends on: none. Files: `core_brain/config.py`, `scoring/config.py`.
Rename 6 core / 3 scoring fields (defaults+units unchanged); legacy
read-only properties; move all in-repo ctor call sites + old-kw tests.
Verify: NEW tests in `tests/test_quotes.py` RED-first then GREEN
(defaults, no-`_frac`-field scan both classes, alias-reads-canonical,
alias-set-raises-AttributeError, legacy-kw-TypeError); then
`pytest -q tests/test_quotes.py tests/test_config_for_display.py`.

### [x] T2 [M][Backend/Logic] Canonical-first env loader + all readers
Depends on: T1. Files: `config.py::load`, `quotes.py`, `risk.py`, `kpi.py`,
`order_manager.py` (1 line), `scoring/allocate.py`, touched tests.
5 env canonical-first+fallback+log-warning; rename `range_pct`,
`window_pct` (+refusal text), `bankroll_pct` param, `kelly_pct` local
(keys kept + format comment), `capture_pct`/`max_pct`; update readers;
`test_stat_gate.py` 3 loader tests (monkeypatch+caplog: new, legacy,
new-wins) + display test + window/skew/hedge threshold tests (reuse
`test_live_quotes.py::test_decide_quotes_basic_two_sided` and
`test_paired_inventory_accounting.py::test_emergency_hedge_pair_inversion_blocked`
fixtures — confirm shapes first).
Verify: `pytest -q tests/test_quotes.py tests/test_config_for_display.py
tests/test_trader_loop.py tests/test_stat_gate.py` + each edited file.
`tests/test_risk.py` MISSING — create it or correct the command (record).

### T3 [S][Docs/UX] Explainer: lowercase + badges + venue-vs-maker scope
Depends on: T1. File: `dashboard/static/strategy_explainer.html`.
Pre-grep 5 old patterns, record; hand-edit table/diagrams/descriptions;
badge per row ([USD]/[cents Offset]/[% Ratio 0..1]/[Shares]) + style;
venue note (per-order vs all-open, 25%/90%, $1 min, `order_caps()` +
`derive_dynamic_caps` sources, 3 enforcers) + maker note (6%, skew+block);
keep $90/$100 examples.
Verify: post-grep only `max_skew_offset` remains; hands-on FRESH-SHADOW
dashboard check (`/api/parameters` + explainer page). No pytest.

### T4 [S][Docs] Guidance + naming rule + venue comment + How-to-verify
Depends on: T2, T3. Files: `docs/agents/safety.md architecture.md
first-run.md verifying.md strategy.md python-conventions.md`,
`docs/prompts/statistical-validation-run.md`, `core_brain/venue.py`
(comment only), `glossary.md` iff it governs params.
Dynamic-ceiling text; new names; `_pct`=0..1 rule + 3 pct-points
exceptions + `_usd`/`_shares`/`_offset` + `_frac`-ban; venue comment;
check prudent-guide currency; dated history untouched.
Verify: zero old canonicals in touched docs; PR How-to-verify <=5
hands-on steps (shadow cmd + URLs + expected + START warning; NO
pytest/git steps).

## Agent-run test commands (NOT operator steps)
`python -m pytest -q tests/test_quotes.py tests/test_config_for_display.py
tests/test_trader_loop.py tests/test_stat_gate.py` + edited files.
Full suite = CI merge gate.
