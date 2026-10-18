# Plan — #572: one spread ceiling from picking to placing

Issue: #572 `[FIX] Use one spread limit from picking markets to placing orders`
Branch: `i572/use-one-spread-limit-picking-to-placing`
Stack: Python, dataclass configs, pytest. Size: **Small** (2 config modules +
selector defaults + 2 boundary test files; behavior-preserving consolidation).

## 0. Answer to the issue's open question (locked)

> "Which stage owns the absolute number — the live safety gate or the picker?"

**The live safety gate owns it (`max_book_spread = 0.06`).** The picker takes a
documented fraction. Rationale, quoted from the codebase:

- `core_brain/config.py:204`: "`max_book_spread` is a safety gate, not a
  preference: a wide book is one where a fill leaves a leg that may not be
  closeable."
- The wide-book trial already sets `max_book_spread` directly in both
  `load()` paths (`core_brain/config.py:1354`, `scoring/config.py:884`) — the
  owner is the one future trials adjust, and the trial has already voted.

## 1. Interface map (grounded seams only — all verified verbatim)

| Seam | Location | Today | After |
|------|----------|-------|-------|
| Live gate (OWNER) | `core_brain/config.py:176` / `scoring/config.py:115` | `max_book_spread = 0.06` | Unchanged absolute |
| Picker bar (DERIVED) | `core_brain/config.py:470` / `scoring/config.py:461` | `select_max_book_spread = 0.0205` (second absolute) | Read-only property `round(max_book_spread * select_spread_frac, 6)` |
| Picker share (NEW) | beside each owner | — | `select_spread_frac = 41.0/120.0` with comment |
| Trial writer | `core_brain/config.py:1347-1360` / `scoring/config.py:881-887` | sets both absolutes + `max(window, wide)` | sets `max_book_spread` ONLY (+ trial flag, + `max(window, wide)`); picker follows via property |
| Live consumer | `core_brain/risk.py:305` (`spread > cfg.max_book_spread`) | reads owner | Untouched |
| Picker consumers | `scripts/filter_markets.py:101`, `scoring/selector.py:204,240` | read second absolute / `0.0205` literals | Read the derived property; selector defaults computed from it |

Math lock: `round(0.06 * (41.0/120.0), 6) == 0.0205` — verified `python -c`
this session. The `round(..., 6)` in the property is load-bearing: binary64
`0.06 * frac` is `0.0204999…`, and `round(..., 6)` returns exactly `0.0205`.


## 2. Type-design review (type-design-analyzer persona)

- `select_max_book_spread` as property: encapsulation GOOD (no setter path can
  park a stale absolute), invariant expression GOOD (the fraction IS the rule),
  usefulness GOOD (prevents the exact drift this issue reports), enforcement
  GOOD (frozen dataclass + read-only property; trial writes only the owner).
- `select_spread_frac` as plain float: one escape hatch (frac > 1 makes picking
  looser than quoting). Accepted: documented share, not env-exposed. If env
  exposure is ever added it needs a `0 < frac <= 1` bound (parked as NBT N4 family).

## 3. One improvement proposal (adopted — hardening, not expansion)

Evidence, verbatim from `tests/test_wide_book_trial.py:261`:
`BID, ASK = 0.435, 0.565` — "a 13c book is refused at the shipped 6c and
admitted at the 15c trial." And from `core_brain/config.py:1359`:
`window = kw.get("max_spread_from_mid", MakerConfig.max_spread_from_mid)` —
the trial reads "whatever the per-bot block may have set, not the class
default, so a venue-supplied window is widened rather than replaced."
Proposal: the trial block keeps reading the in-flight `kw` window (not the
class default) after consolidation, and the new derivation test pins a 13c
book admitted under the 15c trial through BOTH configs. Folded into T2.
Rejected: deriving `max_spread_from_mid` from the ceiling (changes real
quoting behavior), merging config files (#571's job), renaming the window (N1).

## 4. Tasks (3 atomic vertical slices, risk-first; T1 → T2 → T3)

### T1 [x] config-derivation — derive the picker bar in both modules
Depends on: nothing. Domain: config.
- In `core_brain/config.py` AND `scoring/config.py` (mirror edit):
  add `select_spread_frac: float = 41.0 / 120.0` beside `max_book_spread`;
  replace the `select_max_book_spread = 0.0205` FIELD with a read-only
  `@property` returning `round(self.max_book_spread * self.select_spread_frac, 6)`,
  with a trial branch returning `self.wide_book_trial` when set.
  Delete the `kw["select_max_book_spread"] = wide` trial lines
  (`core_brain/config.py:1355`, `scoring/config.py:885`); keep
  `kw["max_book_spread"] = wide` and the `max(window, wide)` widening.
- Frozen-dataclass caution: `@property` is legal on a frozen dataclass, but the
  old `__init__` parameter disappears — `MakerConfig(select_max_book_spread=…)`
  breaks loudly. Grep shows no such construction today; the new test locks it.
- Verify: `python -c` prints `0.06`/`0.0205` from both modules;
  `pytest -q tests/test_market_selection_bars.py tests/test_wide_book_trial.py`.

### T2 [x] selector-and-trial — wire defaults, pin the chain (§3 hardening)
Depends on: T1. Domain: selection + trial.
- `scoring/selector.py:204,240`: replace literal `0.0205` defaults with one
  module-level default computed from the imported ceiling × fraction (round 6).
- New fail-without-change tests in the EXISTING files: derivation pin
  (`ScoringConfig().select_max_book_spread == MakerConfig()… == 0.0205`;
  `ScoringConfig(select_spread_frac=1.0)… == 0.06`; `TypeError` on constructing
  with `select_max_book_spread=`); 13c-book coherence under trial `0.15`
  through BOTH configs (`risk.book_health` admits `0.435/0.565`); narrow trial
  `0.02` keeps widening-only window behavior.
- Existing assertions stay byte-identical in intent.
- Verify: same two suites as T1.

### T3 [x] display-and-docs — keep the surface honest
Depends on: T2. Domain: docs/display.
- `scripts/filter_markets.py:101` needs NO logic edit (same attribute name,
  now derived) — existing `test_the_filter_markets_spread_bar_matches_config`
  proves `fm.MAX_BOOK_SPREAD == 0.0205`.
- `core_brain/kpi.py` `for_display` path: inherited trial still degrades to
  shipped defaults with warning; picker reads `0.0205`.
- Comment-only updates where the picker bar is called an absolute
  (`core_brain/config.py:468-470`, `scoring/config.py:459-461`).
- Verify: `pytest -q tests/test_config_for_display.py
  tests/test_market_selection_bars.py tests/test_wide_book_trial.py`.

Checkpoints: after T1 derivation holds at import; after T2 the full chain
moves as one; after T3 the surface reports it.

## 5. Risks & assumptions
- No code constructs `MakerConfig(select_max_book_spread=…)` — VERIFIED by grep
  this session (only attribute READS); T1's TypeError test locks it.
- `round(x, 6)` bit-equality is platform-stable (non-half value); CI proves it.
- Contradiction resolved: issue cites trial block `:1460-1473` and knob
  `HUNTER_WIDE_BOOK_SPREAD`; repo at HEAD has the block at `:1347-1360` and
  knob `HUNTER_WIDE_BOOK_TRIAL`. Plan uses repo truth; no behavior impact.
- Old-kwarg construction now raises instead of drifting. Intended, pinned by test.

## 6. Handoff
Next: `iii-build-plan auto` on this branch. The CodeRabbit `@plan` output on
the issue is advisory; THIS file is the binding spec. #571 needs no ordering —
it imports `max_book_spread` / `select_spread_frac` / `select_max_book_spread`
terms verbatim.

