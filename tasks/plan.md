# Plan — Issue #392: prefer live competitive markets over flat long-dated ones

Branch: i392/improve-prefer-live-competitive-markets-over-flat- | Issue: #392
Size: Standard — scripts/filter_markets.py ranking + tests, one design decision
(rank-score shape from already-measured fields).
Type: [Backend/Logic]. Stack: Python 3.12, pytest. Spec embedded below;
no root SPEC.md exists in this repo (prior spec lives in docs/archive/; same
precedent as #386/#390).

## Spec (embedded)
Goal: the ranker graduates live competitive markets with real turnover instead of
flat-mid long-dated ones whose tape passes but whose mids never move.
- New pure `rank_score(row) -> float` in `scripts/filter_markets.py` replaces the
  bare `-return_pct_day` key at the shipped ranking (`eligible.sort`, :2690).
- Inputs restricted to already-measured row fields: `return_pct_day`,
  `movement_usd`, `trade_count`, `range_cents`, `days_to_resolve`, plus
  started-ness derived from the existing `market_start_iso` + now. No new venue
  calls in the rank path.
- Live boost: started + short horizon + hot tape outranks flat tape at equal
  return. Flat-mid long-dated penalty: low `range_cents` + long `days_to_resolve`
  is deprioritized even when window tape passes the movement bar.
- Missing/unmeasured fields fail safe: no boost, no penalty, never a crash
  (`.get` with defaults) — an unreadable row ranks exactly as today.
- Unchanged: the [0.20, 0.80] decided-mid band, depth/spread gates, movement and
  velocity gates, pre-start/expiry gates. A CS match at mid 0.82 stays rejected.
- Out of scope: `core_brain/quotes.py`, `core_brain/trader_loop.py`, the maker
  queue bar (#393 owns it), paired/admission trial sorts, live runs, orders.db.

## CodeRabbit plan intake (3-line note)
- Adopted: nothing — at planning time the issue carries only the Related note
  and the posted `@coderabbitai plan` prompt; no plan comment has arrived.
- Rejected: nothing (no plan to reject).
- [UNVERIFIED]: nothing — every cited path/symbol was read live in this session.

## Open questions (both resolved from code — none asked of the operator)
- Q1 (wider in-play band vs fixed band + scoring boost) → keep the band fixed.
  The selection band (filter_markets.py:1199-1202) mirrors the quoting refusal
  (`mid outside [0.20,0.80] -- decided market` in quotes.py): widening selection
  alone would graduate markets the quoter refuses every cycle. Boost ranking
  instead; the band question is a quoting-strategy change and out of scope.
- Q2 (mid-flatness penalty) → yes, via measured fields. Mid history is not
  available without new fetches (the tape endpoint yields trades only), but
  `range_cents` + `days_to_resolve` already on the row express "flat and far".
  No operator question remains.

## Interface contracts (locked before logic)
- `rank_score(row: dict, *, now: float | None = None) -> float`, pure, in
  `scripts/filter_markets.py` near the velocity/movement helpers. `now=None`
  means wall-clock (tests pass fixed times, per test_velocity_gate.py precedent).
- Shipped ranking becomes `key=lambda r: -rank_score(r)` at :2690. Paired-depth
  (:1540+) and paired-admission (:1710/:1740) sorts stay on `return_pct_day`.
- Started-ness reuses the existing `market_start_iso`; unparseable/missing start
  reads as not-started (no boost, matching the pre-start gate's caution).
- No signature changes to `evaluate()`; no new CLI flags; no `config.py` change
  (named constants in filter_markets.py, following the MIN_* pattern minus the
  _CFG read — trial-conditional knobs can come later).
- `type-design-analyzer` skipped: no new types or domain model (one float score,
  one pure function). `code-explorer` skipped: the path is already traced by
  hand (evaluate gate order 985-1314 + ranking key :2690).

## Improvement proposal
- Skipped: no evidence-backed improvement beyond the issue exists. The one
  adjacent idea (the inert maker-queue bar in evaluate) belongs to #393 and is
  explicitly out of scope here.

## Tasks
Dependency graph: T1 (tests) <- T2 (rank_score + wiring) <- T3 (flat penalty) <- T4 (end-to-end proof).

### [ ] T1 — RED ranking tests: live outranks flat [Backend/Logic] (S)
Target: tests/test_rank_score.py (new, following test_velocity_gate.py style:
fixed NOW, plain dict rows, no venue).
- `rank_score` orders live-like (started, short horizon, hot tape) above
  flat-like (long horizon, low range) at equal `return_pct_day`.
- Missing fields (`{}` row) score exactly `return_pct_day` (today's behavior).
- Unstarted event gets no live boost.
Helper skill: test-driven-development. Depends on: none.
Verify: fail first (helper missing: ImportError), then
`pytest -q tests/test_rank_score.py` after T2.
Checkpoint: contract proven by tests (T1+T2).

### [ ] T2 — GREEN: rank_score + shipped-ranking wiring [Backend/Logic] (M)
Target: scripts/filter_markets.py (new pure `rank_score` + `market started`
derivation via `market_start_iso`; repoint the :2690 sort key; paired/admission
sorts untouched), tests/test_rank_score.py (fill GREEN bodies).
Live = started AND short horizon AND hot tape (thresholds as named constants);
everything else ranks as today, byte-identical order on old rows.
Helper skills: test-driven-development, incremental-implementation.
Depends on: T1.
Verify: `pytest -q tests/test_rank_score.py tests/test_filter_markets_publish_json.py`.
Checkpoint: ranking proven, penalty pending (T2).

### [ ] T3 — GREEN: flat-mid long-dated penalty [Backend/Logic] (S)
Target: scripts/filter_markets.py (penalty arm inside `rank_score`),
tests/test_rank_score.py (low range_cents + long days_to_resolve sinks below an
equal-return live row; unmeasured range/horizon → no penalty, fail-open).
Helper skill: test-driven-development. Depends on: T2.
Verify: `pytest -q tests/test_rank_score.py tests/test_velocity_gate.py
tests/test_movement_gate.py tests/test_pre_start_gate.py`.

### [ ] T4 — End-to-end: Senate-like vs live-like ranking [Backend/Logic] (S)
Target: tests/test_rank_score.py (new test): fixture rows shaped like the
2026-10-06 universe (Senate-like: 28d horizon, 3.0c range, mid return; live-like:
started, hours-long horizon, hot tape) through the shipped sort → live row
ranks first. No prod code (verification only unless it exposes a wiring miss).
Helper skill: test-driven-development. Depends on: T3.
Verify: `pytest -q tests/test_rank_score.py tests/test_filter_markets_publish_json.py
tests/test_velocity_gate.py tests/test_movement_gate.py`.
Checkpoint: selection proven.

## Guardrails
- Focused suites only (named per task); the full `pytest -q` suite is the GitHub
  CI merge gate, not a local loop. New behavior has tests that fail without it.
- No new dependencies, no `config.py` changes, `data/orders.db` read-only.
- `core_brain/` untouched (quoting band stays aligned with selection band).
- Paired-depth/admission trial paths untouched (shipped ranking only).
- No sub-issue ceremony (4 coupled tasks on one branch; precedent #386/#390).

## Notes (session memory — do not lose)
- Prior `tasks/plan.md` header was Issue #390 (merged); this file now plans #392.
- Labels at intake: `ready-for-agent` + `needs-answers`; both Open questions were
  resolved from code above, so no operator round-trip was needed.
- Evidence anchors (2026-10-06 23:54 rank): Texas $19,279/30m + Kansas
  $2,783/30m graduated; CS Falcons-NAVI ($35,314/30m, mid 0.82) and Antofagasta
  tennis ($74,703/$46,155 tape) rejected by band/depth. Census: 58 no-movement,
  4 flat-range rejects — the tape gates work; ranking is the gap.
- #393 (queue-aware quoting) is independent; `queue_bar_reject` in evaluate is
  noted and not touched.
