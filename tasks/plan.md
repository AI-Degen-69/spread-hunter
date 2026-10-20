Branch: i687/ranker-tournament-preset | Issue: #687

# Plan — #687 ranker tournament preset (N3 from #674)

## Size tier & task type

- **Tier: Standard** — 4–6 files (core_brain/config.py, scoring/config.py,
  scripts/filter_markets.py snapshot, dashboard server + JS, tests), one
  architectural decision (where preset lives + precedence rule).
- **Task type: Code + UX / Copy** — loader logic plus the visible "preset
  loaded" pill on the screener board.

## Issue summary

`BOT_QUOTE_TOURNAMENT_PRESET` (`core_brain/config.py:577`) reaches the engine
only. Scoring has no preset knob, so a preset-run ranker scores with default
bars instead of preset-specific ones, and the UI shows nothing about a preset.

## CodeRabbit plan intake (1 comment, read once — echo ignored)

- **Adopted:** shared preset-name resolver in core (canonical
  `BOT_QUOTE_TOURNAMENT_PRESET` → legacy `HUNTER_TOURNAMENT_PRESET`, trim +
  lowercase, blank → None, unknown → existing `ValueError` text);
  `SCREENER_PRESETS` registry in `core_brain/config.py` (same six names as
  `TOURNAMENT_PRESETS`); scoring `load()` precedence defaults → preset →
  explicit env; ranker publishes loaded preset in `pipeline.json` → KPI
  funnel → dashboard pill; tournament shared filter stays preset-free.
- **Rejected:** inventing a conservative→aggressive tightness scale for
  screener bars — the ticket says the screener needs "its own defaults to
  pick", and no product decision ties preset names to tighter/looser bars.
- **[UNVERIFIED]:** the plan's claim that `baseline` should copy
  "loose-baseline screener values" (`filter_live_activity_min_ratio` + lowered
  `filter_std_vol_min_24h_usd`) from "existing wiring + run record".
  Verified against the code: shipped defaults are `0.0` / `125_000.0`
  (`core_brain/config.py:314,317`); no env wiring or run record
  (`docs/runs/2026-10-14-loose-baseline-i569.md`) documents preset-specific
  screener numbers. Task 2 resolves this by starting `baseline` with shipped
  bars and flagging the numbers as an operator product decision.
- Cost record: skeleton + seams borrowed (registry location, resolver sharing,
  snapshot reporting path); all cited paths spot-checked; task list merged
  from CodeRabbit's 4+ phases into 7 atomic tasks below.

## Open questions — resolved from code

1. *Where do preset-specific screener numbers come from?* Nowhere in the
   repo — no wiring, no run record. Resolution: ship the mechanism with
   shipped bars; numbers are opt-in operator input, not invented.
2. *Does the scoring class body accept new fields?* No —
   `tests/test_config_divergence_guard.py` fails on any new declaration.
   Resolution: registry + resolver live in `core_brain/config.py`; scoring
   `load()` only merges into `kw`.
3. *Which UI surface shows the preset?* The screener board (Market Filter
   tab, funnel from `/api/kpi`) — pill reads the ranker's published value,
   never the dashboard process env.
4. *#674 ledger N3 closeout?* No `docs/issues/674*` ledger exists in the
   repo (closed-issue artifacts were pruned). Nothing to close here; noted
   so Station VI does not chase a missing file.

## Spec (embedded — Small ceremony for a registry + precedence change)

**Goals:** preset toggle covers the ranker; preset-off runs are byte-identical
to today; the screener board shows which preset the ranker loaded.

**Acceptance:**
- Preset-on ranker scores with preset bars (observable via effective config
  + `pipeline.json` preset field).
- Preset-off run is unchanged (no selector → defaults, today's numbers).
- UI flag visible on the screener board when a preset is loaded; absent (not
  "default") when none is.
- Engine behavior and error text unchanged; `baseline` keeps the 0.045 picker
  window (no `PICKER_PROFILE` field in any preset).

**Out of scope:** new screener bar numbers beyond shipped defaults (operator
decision); touching the tournament shared filter (stays preset-free by
design); any live/opening command; any write to `data/orders.db`.

## Improvement proposal (adopted by default — hardening, not scope)

> Exclude all five `PICKER_PROFILE` fields from every `SCREENER_PRESETS`
> entry — in particular `max_spread_from_mid`, which the engine `baseline`
> preset sets to 0.15 (a quoting-window field, not a screener bar) and which
> would clobber the picker's deliberate 0.045 window.
> Evidence, verbatim: `scoring/config.py:65`
> `max_spread_from_mid: float = PICKER_PROFILE["max_spread_from_mid"]` and
> CodeRabbit Task 2 "Do not put the picker's five `PICKER_PROFILE` fields in
> any preset. In particular, keep the 0.045 window." A registry test pins it.

## Dependency graph & task order (risk-first)

- T1 (resolver + engine refactor) → T2 (registry) → T3 (scoring load) →
  T4 (snapshot + funnel) → T5 (UI pill) → T6 (menu passthrough verify) →
  T7 (tests pin both paths).
- T1 is riskiest (shared loader both paths use) → runs first. T2's
  baseline-numbers question is decided above, so T2 is S.

## Tasks

- [x] **T1 [Backend/Logic] (M)** — Shared preset resolver in
  `core_brain/config.py`; `MakerConfig.from_env()` (~L574-585) calls it.
  Target: `core_brain/config.py`. Skill: test-driven-development.
  Depends on: —. Verify: `tests/test_dynamic_offset.py` unchanged-green.
- [x] **T2 [Backend/Logic] (S)** — `SCREENER_PRESETS` dict next to
  `TOURNAMENT_PRESETS`: same six names, `description` + scoring-field
  overrides only, zero `PICKER_PROFILE` fields. Non-baseline entries carry
  no overrides (shipped bars); `baseline` carries shipped bars too, Pending
  operator numbers. Target: `core_brain/config.py`. Depends on: T1.
  Verify: registry test (names match, keys are real fields).
- [x] **T3 [Backend/Logic] (M)** — `scoring/config.py::load()` merges preset
  into `kw` right after `kw = {}`; explicit env reads after it keep winning;
  class body untouched. Target: `scoring/config.py`. Depends on: T2.
  Verify: precedence tests (preset applies, explicit overrides, unknown
  raises, blank falls back, off unchanged).
- [x] **T4 [Backend/Logic] (S)** — Ranker publishes loaded preset in
  `pipeline.json` snapshot; value flows through the KPI funnel
  (`core_brain/kpi.py` / `scripts/filter_markets.py` snapshot builder).
  Shared-filter tournament path stays preset-free. Depends on: T3.
  Verify: snapshot contains preset name on preset runs, absent/null off.
- [x] **T5 [Design/UI] (S)** — Screener board preset pill (dashboard server +
  static JS): shows ranker-reported preset, hidden when none.
  Verification mode: browser preview. Depends on: T4.
- [x] **T6 [Backend/Logic] (XS)** — Verify the menu-started ranker receives
  the selector (env passthrough, same pattern as
  `tests/test_ranker_reads_env_file.py`); no change if it already flows.
  Depends on: T3. Verify: generated-`.env` subprocess test.
- [x] **T7 [Backend/Logic] (M)** — Tests pin both paths: preset-on scores
  with preset bars, preset-off unchanged, `baseline` keeps 0.045 window.
  Depends on: T1–T6. Verify: focused suites green
  (`test_config_single_source`, `test_config_divergence_guard`,
  `test_dynamic_offset`, `test_ranker_reads_env_file`, new preset tests).

## Checkpoints

- After T3: loader precedence demonstrable via focused config tests.
- After T5: pill visible in dashboard preview against a preset snapshot.
- After T7: all focused suites green; ready for Station IV.
