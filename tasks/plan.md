# Plan: Issue #312 â€” Market universe is empty, ranker writes top 0

**Branch:** `i312/market-universe-empty` | **Issue:** #312

**Size tier:** Standard â€” two files, one architectural decision (started â‰  resolved).
Rationale: one gate-semantics change plus row legibility, three suites pin it.
**Task type:** Code + Debug (root cause confirmed live: sports `endDate` is
kickoff, `closed=False`, `acceptingOrders=True`).

## Locked constraints (from the issue + CONSTRAINTS.md)

- Started â‰  resolved: use real venue signals (`closed`, `acceptingOrders`).
  `closed=False` + accepting = not resolved.
- Fail closed on ambiguity: unreadable resolution state is refused, never live.
- Unknown START stays fail-open (`pre_start` False); pinned by existing test.
- No in-play policy change: admitting live sports past the horizon gate fixes a
  misclassification; `identity_allowed` and the blocked-keyword arm keep
  refusing genuine submarkets. The submarket trial (ADDENDUM) runs later on its
  own feed.
- `_cause()` (`filter_markets.py:1023`) keeps its gate cards; new reason text
  must bucket identically; existing bucket tests stay green.
- Full sweep with GitHub CI; never run the filter CLI; no `data/` or `run/` writes.

## Verified seams (all read verbatim, zero guesswork)

- `filter_markets.py:142-167` â€” `days_to_resolve(end_iso, now_iso=None)` returns
  (end âˆ’ now) in days; `now_iso` is the test seam.
- `filter_markets.py:304-345` â€” `tradable(volume, days, title, slug, category,
  market_type, market_group, series_title, event_title, min_volume)`: identity
  then volume floor then `days<0 â†’ "horizon passed"`.
- `filter_markets.py:589,596-597` â€” rows carry `end_date_iso`, `_start_iso`
  (`gameStartTime`); `closed`/`acceptingOrders` are used only in cheap filters
  (`:504,541`) and `:1918` â€” never reach `tradable`/`evaluate`. That is the gap.
- `filter_markets.py:952-958` â€” `evaluate` calls `days_to_resolve` then
  `tradable(...)` with metadata but NO resolution state.
- `filter_markets.py:1023-1060` â€” `_cause(reason)` buckets by substring:
  volume, horizon, income, pre-start, no movement, decided-mid, spread.
- `test_pre_start_gate.py` â€” start-time gate, fail-open on unknown; `evaluate`
  rejects pre-start before any book fetch (`_ExplodingSession`).
- `test_unified_universe.py:82-105` â€” `_universe_candidate` fixture clears
  identity and horizon; suite covers discovery, truncation, reasons, tags.

## Improvement proposal (adopted by default, evidence-based)

Rename the refusal so the words cannot lie again: `horizon passed` becomes a
resolved-market verdict only, with the far-market `horizon Xd > Nd` form
unchanged. Evidence: this bug came from one string covering two meanings
(resolved vs merely started).

## Task graph

T2 (reasons) and T3 (truncation) touch `evaluate`'s row surface but not its
gate logic, so they depend on T1's semantics being settled first, not the reverse.
T4 verifies the assembled change.

## Tasks

### T1 - Resolve-state-aware horizon gate in `tradable` (+ runner plumbing) [x]
- **Size:** M | **Domain:** Selection | **Helper:** `test-driven-development`
- **Files:** `scripts/filter_markets.py`, `tests/test_unified_universe.py`
  (new focused cases; `test_pre_start_gate.py` untouched)
- **Build:**
  - New pure helper, e.g. `resolve_state(closed, accepting_orders)` returning
    `True` (resolved), `False` (live), `None` (unreadable). Fail closed on None.
  - `tradable` gains the resolution input; order: a market reading `resolved`
    is refused with a resolved verdict; a market past `end_date_iso` but reading
    live (`closed=False` + accepting) proceeds to the horizon-length arm
    (`days` semantics for far markets unchanged). Unknown â†’ refused, never live.
  - `evaluate` reads the venue `closed`/`acceptingOrders` signals already present
    on the row (`filter_markets.py:504,541` shape) and passes them through.
  - Rename the refusal strings per the improvement proposal; every new string
    still buckets to the same `_cause()` card as its predecessor.
- **Depends on:** â€”
- **Verify:** new tests RED on untouched code, GREEN after: (a) live fixture
  past kickoff admitted past the horizon arm; (b) genuinely closed market
  refused; (c) unreadable state refused; (d) far-future market keeps its
  distance refusal; (e) pre-start suite still green unmodified.

### T2 â€” Legible reject reasons: carry the value that caused them [x]
- **Size:** S | **Domain:** Reporting | **Helper:** `test-driven-development`
- **Files:** `scripts/filter_markets.py`, `tests/test_unified_universe.py`
- **Build:** the venue field behind each refusal rides in the reason string.
  Per-gate counts stay on `_cause()` cards, not raw text.
- **Depends on:** T1 (refusal strings are being renamed there)
- **Verify:** RED-first: identity refusal carries the group value; horizon
  refusal carries endDate + state; `_cause()` output equals today's bucket.


### T3 â€” Fetch truncation is an explicit condition, not a silent cap [x]
- **Size:** S | **Domain:** Discovery | **Helper:** `test-driven-development`
- **Files:** `scripts/filter_markets.py`, `tests/test_unified_universe.py`
- **Build:** the `truncated` meta already exists (`:511,628,633`); carry it onto
  the universe row set (the shape `_universe_candidate` fixtures assert on) so
  a capped pass reads differently from a thin market, without changing fetch policy.
- **Depends on:** T1
- **Verify:** RED-first: truncated pass marks its rows; exhausted pass does not;
  existing truncation tests (`:131-186` family) stay green.

### T4 â€” Sweep + hands-on ranker proof [ ]
- **Size:** S | **Domain:** Verification | **Helper:** â€”
- **Files:** none (read-only operator step)
- **Build:** operator runs the ranker during a live sports window and confirms
  `runtime/markets.json` is non-empty and carries the main-line market.
- **Depends on:** T1 + T2 + T3
- **Verify:** operator reads `runtime/markets.json`; empty file = failed check.

## Explicitly NOT modified

- `scoring/selector.py` (`identity_allowed`, blocked-keyword arm), `should_exit()`,
  risk caps, grace defaults, `pairs_exit_window_sec`, rescue route order,
  pre-start fail-open behaviour, any fetch policy or volume floor, `data/**`, `run/**`.

## Sub-issues

Skipped deliberately: single-owner session, one branch, four slices; prior
stations (#294, #301, #306) ran the same shape without tracker ceremony.

## Verification & TDD summary

- New unit tests (T1â€“T3) through RED/GREEN on `tests/test_unified_universe.py`
  plus the untouched guard suites `test_movement_gate.py`, `test_pre_start_gate.py`,
  `test_family_probe.py`.
- Full `python -m pytest -q` stays with GitHub CI on push (merge gate).
- Operator "How to verify" lives in the issue: non-empty `runtime/markets.json`
  with the main-line market during a live sports window.
