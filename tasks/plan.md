# Plan — Issue #386: quote in-play esports markets whose venue endDate is kickoff

Branch: i386/quote-in-play-esports-markets | Issue: #386
Size: Standard — 2 prod files + tests, one design decision (in-play window length).
Type: [Backend/Logic]. Stack: Python, pytest. No SPEC.md ceremony (spec embedded below);
no root SPEC.md exists in this repo (prior spec lives in docs/archive/).

## Spec (embedded)
Goal: an in-play sports/esports market whose venue `endDate` is kickoff must be
quotable at quote time instead of skipped on a negative countdown.
- Carry venue `game_start_time` onto the fleet market as optional `game_start_ts`
  (None when missing/unparseable; `end_ts` parsing unchanged).
- New pure helper `quote_t_remaining(market, now=None)`: no kickoff → old countdown;
  kickoff in future → old countdown (pre-start stays refused); kickoff passed →
  `max(old countdown, game_start_ts + IN_PLAY_WINDOW_SEC - now)`; never shortens a
  real later end date; after the window it goes negative and quoting stops.
- `IN_PLAY_WINDOW_SEC = 6 * 3600` (covers BO3/BO5 + football; operator-tunable later).
- Only `evaluate_market_quote` uses the helper; `decide_quotes`, gates, config,
  selection, submission, and all other `t_remaining`/`end_ts` consumers unchanged.
- Out of scope: placement/selection gates, post-fill management, orders.db, live runs.

## CodeRabbit plan intake (3-line note)
- Adopted: kickoff carried as optional field; helper used only in the quote evaluator;
  6h constant (not a config field); FURIA fixed-time test vectors; must-not-change list.
- Rejected: overwriting `end_ts` in `fetch_pinned_market` (would corrupt other clock
  consumers); `days_to_resolve` as the clock (same kickoff-valued source, negative here);
  no-deadline option (no hard stop if the venue keeps accepting orders).
- [UNVERIFIED] at intake, since resolved from code: `_iso_to_unix` exists and handles
  `Z` (markets.py:125); `LiveMarket` constructions all use keywords (safe to append a
  defaulted field); `scoring/markets.py` owns a separate `LiveMarket` (untouched);
  `_Resp`/`_venue_returns` stub pattern exists (test_negrisk_merge_routing.py:93,106).

## Open question (resolved from code)
- Which clock? `game_start_time` + fixed window (CodeRabbit Choice 1). `days_to_resolve`
  is out (same kickoff-valued `endDate`, negative for these markets). Window length 6h
  is a default the operator can retune — flagged in the report, not blocking.

## Improvement proposal (adopted, simplification)
- CodeRabbit split verification into its own phase; folded as per-task verification
  instead (3 tasks, not 4+). Evidence: the tasks' checks are the same test files the
  phase named. No scope change.

## Tasks

### [x] T1 — RED tests: kickoff-aware quote clock [Backend/Logic] (S)
Target: tests/test_pre_start_gate.py (new "quote-time kickoff clock" section).
Fixed vectors END=2026-10-06T00:00:00Z, KICKOFF=2026-10-06T15:40:00Z,
NOW=2026-10-06T16:34:41Z (old countdown exactly -59681s): in-play extends
positive; pre-start (kickoff NOW+58m) stays -59681; no-kickoff stays -59681;
stale (kickoff NOW-window-60s) negative; real later end (NOW+7d, kickoff 30m
ago) returns 7d; stand-in without the field returns its own value.
Helper skill: test-driven-development. Depends on: none.
Verify: fail first (helper missing), then `pytest -q tests/test_pre_start_gate.py`.

### [x] T2 — GREEN: carry kickoff + switch the quote clock [Backend/Logic] (M)
Target: core_brain/markets.py (`game_start_ts` field last w/ default None;
parse `game_start_time` in `fetch_pinned_market` via `_iso_to_unix`, None on
missing/garbage; `IN_PLAY_WINDOW_SEC`; `quote_t_remaining` per spec, using
getattr for stand-ins and never shortening a later end), core_brain/quotes.py
(one line in `evaluate_market_quote`), tests/test_pre_start_gate.py (fetch
test with stubbed `_SESSION.get` per `_Resp` pattern: game_start_ts parsed,
end_ts unchanged).
Helper skill: test-driven-development. Depends on: T1.
Verify: `pytest -q tests/test_pre_start_gate.py tests/test_market_quote.py`.
Checkpoint: unit clock proven (T1+T2).

### [ ] T3 — Full-path proof: FURIA times through fetch-to-decision [Backend/Logic] (S)
Target: tests/test_trader_loop.py (new test): stubbed venue returning the
FURIA payload (endDate midnight, game_start_time 15:40Z) + books with depth,
run `evaluate_market_quote` at NOW with a recording decide: assert decide
received t_remaining > 0 (not -59681) and intents flow; plus a stale-window
variant asserting refusal. No prod code (verification only unless it exposes a
wiring miss).
Helper skill: test-driven-development. Depends on: T2.
Verify: `pytest -q tests/test_trader_loop.py tests/test_market_quote.py
tests/test_completable_pair_gate.py`.
Checkpoint: end-to-end quote path proven.

## Guardrails
- Suites test_pre_start_gate + test_market_quote + test_trader_loop +
  test_completable_pair_gate green, no skipped assertions.
- No new deps, no config values, no selection/ranking, no orders.db, no live runs.
