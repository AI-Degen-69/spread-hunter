# Plan — Issue #919: In-play live market screener calibration + activity ratio gate + bidirectional arm split

Branch: i919/live-screener-calibration | Issue: #919
(Branch `i919/live-screener-calibration` already exists in sibling worktree
`C:/Users/Tiger/.worktrees/spread-hunter/i919`; this worktree could not
switch to it, so planning artifacts are written here for carry-over.)

## 0. Right-sizing
- **Tier: Standard** — 4 prod files (`core_brain/config.py`, `scoring/config.py`,
  `scripts/filter_markets.py`, `core_brain/trader_loop.py`) plus `.env.example`
  and per-area tests; one architectural decision (single mixed screener
  universe, live/non-live separation enforced at the trader-arm layer).
- **Task type: [Code]** — config defaults, screener gates, arm filtering,
  boundary tests. No UI, no new dependency, no schema change.
- **Step 0C quick-fix lane:** skipped — labels are `✨ Feature`,
  `🤖 Ready for Agent`, `🧪 Testing`; no `quick-fix` label, so no gate runs.

## 0A. CodeRabbit plan intake (read once, verified against code)
- **Adopted:** 4-phase skeleton (config → live admission → arm split →
  spread-stop lock-in); TDD order; fail-open unavailable tape; sports-only
  range exemption unchanged; `merge_live_event_markets` / `_live_declarations`
  unchanged; per-area test files instead of missing-file stubs;
  `filter_live_vel_lookback_sec` keeps `1800.0` as the non-live window with a
  new `filter_live_inplay_vel_lookback_sec = 900.0` for live rows (its
  Design Choice 1, Option 3 — see deviation flag below).
- **Rejected:** `BOT_MARKET_NONLIVE_ONLY_ON` as the non-live switch name —
  the issue's acceptance criteria name `BOT_MARKET_EXCLUDE_LIVE_ON`, so the
  issue name wins; semantics are identical (independent exclusion filter).
  Rejected a second live screener and any per-trade live storage / new arm
  `scope` tag (saved arm `env` is the arm-type marker, existing per-arm DB
  keeps results separate).
- **Stayed `[UNVERIFIED]` at intake, resolved from code during planning:**
  neither `BOT_MARKET_LIVE_ONLY_ON` nor `BOT_MARKET_EXCLUDE_LIVE_ON` exists
  in this checkout (verified: only `BOT_MARKET_EXCLUDE_SPORTS_ON` exists in
  `core_brain/trader_loop.py::_market_specs`); `core_brain/config.py`
  `MakerConfig.from_env()` reads no `FILTER_LIVE_*` vars today (verified by
  search — only comments mention them); that is exactly the wiring gap Task 1
  closes.
- **Ticket-vs-code conflicts (for the PR description, per CodeRabbit
  Design Choice 8):** "rigid 1-hour lookbacks" — code uses a 30-min movement
  window + 2-h range window; "$35,000 hardcoded" — it is a default with a
  `FILTER_LIVE_VOL_MIN_24H_USD` override in scoring only; `esports_only` /
  `prematch_only` at `trader_loop.py:2548` — do not exist, only
  `exclude_sports` at `trader_loop.py:2549`; "6% Max Spread" — it is an
  absolute 6¢ ceiling (`0.06` dollars), already shipped.

## Spec (embedded — no root SPEC.md ceremony for this repo)
- **Goals:** (1) $5k live 24h floor wired through both loaders;
  (2) 5-min tape-recency gate rejecting ghost-live pre-match books;
  (3) 15-min live velocity window without changing the 30-min non-live
  meaning; (4) 1.5% activity-ratio + 5¢ range gates filtering dormant
  non-live books; (5) bidirectional arm split (`LIVE_ONLY` /
  `EXCLUDE_LIVE`) so live and non-live arms never cross-contaminate;
  (6) locked-in 6¢ spread stop with boundary + recovery proof.
- **Out of scope:** live real-money trading or unapproved venue mutations;
  order-matching or pair-merge mechanics; per-trade live attribution inside
  one mixed arm (needs schema change — explicitly excluded).
- **Edge cases:** unavailable tape fails open (fetch failed ≠ measured
  empty); recency `0` disables the check; live floor `0` falls back to the
  ordinary floor inside `tradable` (operators must use a positive value);
  ended live markets (`event_ended=True`) count as non-live; both lane
  switches on selects nothing (safe empty, no validation error);
  `exclude-sports + live-only` selects nothing (exclude-sports already
  removes every live market); float boundary at exactly 6¢.
- **Acceptance (issue text, with one substitution):** the six boxes in the
  issue, except the pytest command as written fails — `tests/test_filter_markets.py`,
  `tests/test_entry_cap_and_stop_mode.py`, `tests/test_pair_cap_bailout_controls.py`
  do not exist in this checkout (verified). Replacement final run is the
  union of the regression files listed under Tasks 1–4.

## Interface contracts (locked before business logic)
- `core_brain/config.py`: `filter_live_vol_min_24h_usd = 5000.0`,
  `filter_live_activity_min_ratio = 0.015`,
  `filter_live_move_min_range_cents = 5.0` (unit comments refreshed);
  NEW `filter_live_inplay_vel_lookback_sec = 900.0`,
  NEW `filter_live_last_trade_max_age_sec = 300.0` (`0` disables recency);
  KEEP `filter_live_vel_lookback_sec = 1800.0`, `bot_quote_book_max_spread_usd = 0.06`;
  `MakerConfig.from_env()` reads `FILTER_LIVE_VOL_MIN_24H_USD` (finite, ≥ 0)
  plus the two new vars (inplay window finite > 0; recency finite ≥ 0);
  no redeclaration of shared fields in `scoring/config.py` (divergence guard).
- `scoring/config.py`: reads `FILTER_LIVE_INPLAY_VEL_LOOKBACK_SEC`,
  `FILTER_LIVE_LAST_TRADE_MAX_AGE_SEC` next to the existing `FILTER_LIVE_*`
  reads, same validation; no `HUNTER_*` aliases.
- `scripts/filter_markets.py`: module constants mirror the two new fields
  next to the existing `_CFG`-derived constants; single live predicate
  computed once in `evaluate` from `_live_event`/`live_event` (never the
  display `lane`); live rows → 900s window into `tape_movement_and_range`,
  exported `movement_window_sec` reflects the actual window; recency ceiling
  per row (CLI `--max-last-trade-sec` wins, else live rows use the config
  ceiling, non-live rows use none) through existing `velocity_gate_reject`
  with a declared-live rejection reason; `activity_ratio_reject` skipped for
  live rows.
- `core_brain/trader_loop.py::_market_specs`: `BOT_MARKET_LIVE_ONLY_ON` +
  `BOT_MARKET_EXCLUDE_LIVE_ON`, truthy `1/true/yes/on` (same as
  exclude-sports), no `FILTER_*` alias; shared live rule
  `gm.event_live and not gm.event_ended`; independent filters next to
  exclude-sports, before active-market prioritization and the `max_markets`
  slice. Combination table: none → mixed (today); live-only → live only;
  exclude-live → non-live only (incl. prematch sports); both → nothing;
  exclude-sports → non-live non-sports (unchanged);
  exclude-sports + live-only → nothing; exclude-sports + exclude-live →
  exclude-sports alone.
- `.env.example`: commented examples for the new values/switches and the
  combination table; no shipped arm file changes.

## Improvement proposal (1, evidence-based, adopted by default)
- **Proposal (simplification):** implement ONE shared live predicate
  (`event_live and not event_ended`) used by both `evaluate` and
  `_market_specs`, so the two categories cannot overlap by construction.
- **Evidence (verbatim):** issue scope says
  "`BOT_MARKET_LIVE_ONLY_ON`: when enabled, arms strictly and exclusively
  quote on in-play live events (`event_live=True`, `event_ended=False`)".
- **Classification:** simplification — adopted by default, folded into
  Tasks 2–3 below. No scope expansion proposed.

## Tasks (risk-first, atomic vertical slices)

### Task 1 [Backend/Logic] — Config single-source calibration (S) [x]
- **Target files:** `core_brain/config.py`, `scoring/config.py`,
  `.env.example`, `tests/test_config_single_source.py`, `tests/test_config.py`,
  `tests/test_config_divergence_guard.py`.
- **Build:** set the three new defaults; add the two new fields; wire
  `FILTER_LIVE_VOL_MIN_24H_USD` (+2 new vars) into core `from_env`;
  refresh unit comments; update `.env.example`; fix tests pinning old
  values (`35000.0`, `0.0`, `2.0` — prefer the module constant over the
  literal). Keep `1800.0` / `0.06`; no shared-field redeclaration in scoring.
- **Depends on:** none. **Verifies by:** focused pytest on the three files
  above (unit/integration runner).

### Task 2 [Backend/Logic] — Live admission: 15-min window + ghost-live recency + dormant rejection (M) [x]
- **Target files:** `scripts/filter_markets.py` (`evaluate`, `_live_rows`
  untouched, module constants), plus tests `tests/test_loose_baseline.py`,
  `tests/test_movement_gate.py`, `tests/test_velocity_gate.py`,
  `tests/test_live_event_discovery.py`,
  `tests/test_redesigned_filter_pipeline.py`.
- **Build:** live predicate once per `evaluate`; 900s live /
  1800s non-live tape window (range window stays 7200 for all rows);
  live-only 300s recency ceiling via `velocity_gate_reject`
  (CLI-flag precedence, fail-open unavailable tape, ghost-live rejection
  reason); activity-ratio gate live-skipped / non-live-enforced at 1.5%;
  sports-only range exemption unchanged; `$13k` live esports qualifies,
  sub-`$5k` live refused; dormant high-volume non-live (Alaska-style)
  rejected.
- **Depends on:** Task 1 (needs the new config values).
  **Verifies by:** focused pytest on the five test files above.

### Task 3 [Backend/Logic] — Bidirectional arm universe split (S)
- **Target files:** `core_brain/trader_loop.py` (`_market_specs`),
  `.env.example` docs, tests `tests/test_tournament_arms_i917.py`,
  `tests/test_shadow_tournament.py`, `tests/test_trader_loop.py`,
  `tests/test_arm_params.py`.
- **Build:** the two opt-in switches with the shared live rule, applied as
  independent filters before the `max_markets` slice; saved arm `env` as the
  arm-type marker (no schema change); combination table in `.env.example`;
  no shipped-arm edits (all current arms stay mixed until the owner edits
  arm settings); saved plans keep their saved settings (restart keeps
  behavior).
- **Depends on:** none (parallelizable after Task 1 for the docs table).
  Runs after Task 2 in the build order so screener semantics settle first.
  **Verifies by:** focused pytest on the four test files above, incl.
  fixture extension (live-sports + ended-live fixtures), no-overlap /
  union-equals-mixed checks, conflict-matrix checks, pre-limit filtering
  checks, and worker-env isolation checks.

### Task 4 [Debug] — 6¢ spread-stop boundary + recovery lock-in (XS)
- **Target files:** `core_brain/risk.py` (conditional),
  `tests/test_wide_book_trial.py`, `tests/test_trader_loop.py`.
- **Build:** tests first — exact-6¢ pass (e.g. 0.47/0.53 bid/ask) + 7¢
  refusal in `book_health`; place → cancel-above-6¢ → place-again recovery
  in the trader loop. Only if the boundary test fails: add 1e-9 tolerance
  to the single spread comparison in `book_health`; nothing else in
  `book_health`/`hard_block`. If green without changes, ship tests only.
- **Depends on:** none. **Verifies by:** focused pytest on
  `test_wide_book_trial.py`, `test_trader_loop.py`, `test_live_quotes.py`,
  `test_market_selection_bars.py`.

## Dependency graph
- T1 → T2 (config values feed the screener). T3 and T4 are independent of
  T1/T2 in code and may run in parallel; build order is T1, T2, T3, T4 so
  the combination-table docs and final run see settled semantics.

## Checkpoints
- **CP1 (after T1+T2):** screener admits a `$13k` live esports book, rejects
  ghost-live (stale tape) and dormant high-volume non-live books.
- **CP2 (after T3+T4):** live-only and non-live-only arms select disjoint
  sets whose union is today's mixed set; 6¢ boundary + recovery proven.
  (Mode A: one-line progress notes, not approval pauses.)

## Sub-issue mapping (Standard tier — tracked here, not yet created)
- T1 → sub-issue "Config single-source calibration ($5k/1.5%/5¢ + 900s/300s fields)".
- T2 → sub-issue "Live admission: in-play window, recency gate, dormant rejection".
- T3 → sub-issue "Bidirectional arm split (LIVE_ONLY / EXCLUDE_LIVE)".
- T4 → sub-issue "Spread-stop 6¢ boundary + recovery lock-in".
  Native `blocked-by`: T2 blocked by T1. (Creation left to the build
  station to avoid unsolicited tracker writes from planning.)

## Deviation flag needing owner eyes
- CodeRabbit's plan does NOT set `filter_live_vel_lookback_sec = 900` as the
  issue text could be read; it keeps `1800` (non-live meaning intact) and
  adds `filter_live_inplay_vel_lookback_sec = 900` for live rows. Adopted
  here — existing `FILTER_LIVE_VEL_LOOKBACK_SEC` overrides otherwise change
  meaning silently. Reject by reply if literal `900` on the shared field is
  wanted instead.

## How to verify (hands-on only, no pytest commands)
- After the build: open the shadow rehearsal report in `reports/` and check
  a live esports market near `$13k` 24h volume is admitted while a stale
  ghost-live row and a flat high-volume politics book are refused with the
  new reasons.
- Open the dashboard (`http://127.0.0.1:8799`) filter-loop view and confirm
  the live-volume card reads `$5,000` and the movement gate reflects the
  900s live window.
- In a tournament shadow run with one live-only and one exclude-live arm,
  open each arm's saved plan and confirm the lane switch in its `env` and
  that their candidate sets do not overlap.
