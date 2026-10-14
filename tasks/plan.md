# Plan — #517 [ADD] Capital risk analytics for expectancy drawdown and downside risk

Branch: `i517/add-capital-risk-analytics-for-expectancy-drawdown` | Issue: #517
Size: **Standard** (2 source modules + 2 dashboard files + the JS harness; internal-only, no new
dependency, no schema change, no new module file, no public API change) | Type: **Code + UX/Copy**
Tags: `enhancement`, `ready-for-agent`, `family:execution-telemetry` · Related: #513, #514, #515, #516,
#527, #535, #537 · Part of #513
Spec: [`SPEC.md`](../SPEC.md) (Standard tier). Guardrails: [`CONSTRAINTS.md`](../CONSTRAINTS.md),
rewritten for this branch — the #516 text it replaced is branch-scoped by its own header and stays in git.

## Pipeline bookkeeping

1. Tree was clean and on `main`, synced with `origin/main` (verified at planning time).
2. `gh issue edit 517 --add-assignee @me` — claimed (assignee `AI-Degen-69`).
3. Branch created from `main`'s HEAD: `git switch -c i517/add-capital-risk-analytics-for-expectancy-drawdown`
   (issue-title slug, lowercased, `[ADD]` stripped, truncated to 50 chars — the settled rule).
4. Labels read fresh from the API: `enhancement`, `ready-for-agent`, `family:execution-telemetry`.
5. CodeRabbit plan comment present (posted after `@coderabbitai plan`), mined once in § CodeRabbit
   intake below. Stations III–V never re-read it.
6. This file replaced the merged #516 plan, which is this repo's convention for these branch-scoped
   root files (`SPEC.md`, `CONSTRAINTS.md`, `tasks/*`, each previous version left readable in git).
   Nothing from #516 is still open, so no `[x]` marks needed reconciling; the #516 plan is in git at
   commit `6ce7f93`.

## Step 0C — quick-fix gate

The issue does **not** carry the `quick-fix` label, so Step 0C's 7-box gate is not run and no lane is
offered (skill Step 0C outcome 1). The tier is Standard regardless: a dashboard card, a payload field
and a payload-version bump are more than "docs/typo/comment-level".

## Step 0 — stack, tier, task type

- Stack: Python 3.12 (`pytest`, stdlib-only analytics module) + vanilla browser JS
  (`dashboard/static/*.js`, exercised by a Node stub-DOM harness under `tests/js/`).
- Tier: **Standard**; Task type: **Code** (primary) + **UX/Copy** (the card's labels and disclosure lines).
- Domain skills loaded for planning: `test-driven-development`, `incremental-implementation` (Core),
  plus `api-and-interface-design` for the frozen contract and `humanizer` for the card copy. Verified
  present in this environment; no skill was named that does not resolve.

## Step 0A — CodeRabbit plan intake (non-binding; simplicity enforced)

`@coderabbitai plan` returned three tasks with five design choices. What this plan did with it:

| # | CodeRabbit position | Verdict | Why |
|---|---|---|---|
| 1 | **Standalone pure functions in `core_brain/statistical_analytics.py`; `kpi` used as a cross-check oracle in tests; do not refactor `kpi.py`** (Design Choice 2, option 3) | **ADOPTED** | This is the ticket's own operator-settled mechanism ("implement standalone pure functions … use `kpi` values as cross-check oracles in tests. Do not refactor `kpi.py`"). The "second implementation" it worries about is sanctioned and pinned by test. |
| 2 | `sortino_ratio(returns, risk_free=0.0)`: mean excess return over the RMS of **negative excess returns over their own count**; `None` for N<2, zero variance, no negative return, invalid `risk_free`; percent-per-trade, never annualized | **ADOPTED** | Matches #516's sibling naming and reproduces `kpi`'s own denominator (`kpi.py:556-559`) exactly at `risk_free=0.0`, so the oracle holds. |
| 3 | `pnl_skewness(values)` = third standardized moment with **population** moments; `None` for N<3 or zero variance; `_finite`-filtered | **ADOPTED** | Hand-verified: `[-1,-1,-1,-1,10]` → `1.5`, `[1,1,1,1,-10]` → `-1.5`, `[-1,0,1]` → `0.0`. On two values the moment is 0 by construction, which would read `average` — `None` is the honest answer. |
| 4 | `drawdown_recovery(equity_points, starting_value=None)`: read points in the order given, never sort, `_finite` on `v` and `ts`, return the largest-dollar episode, skip invalid points, `recovery_seconds = None` when the peak is the starting value | **ADOPTED, reshaped** | The shape and the reading order are adopted. Reshaped: one scalar "recovery" cannot carry the settled *median/p90* vocabulary, so the function enumerates the episodes and summarises them (below). Nothing in this branch passes a `starting_value`, so that `None` rule never fires in the payload. |
| 5 | **Do not touch `kpi.py`, `build()`, the dashboard or `/api/kpi`** | **REJECTED** | Directly contradicts the ticket's hardest acceptance criterion: *"All three new metrics are visible on the Reports & Analytics page with their band chips … A calculation-only or CLI-script implementation does not satisfy this issue."* CodeRabbit planned the calculation half and dropped the half the operator made mandatory. |
| 6 | Design Choice 1 + Task 1.3: hold for #516, or look up "the #516 symbol" by name and `pytest.skip` with reason `"#516 SQN not landed"`; "the module and the name of the symbol are [UNVERIFIED]" | **REJECTED as obsolete** | #516 merged as PR #533. `core_brain/statistical_analytics.py` already exports `system_quality_number` (line 1441), `sqn_band` (1426), `SQN_NOISE_BELOW`/`SQN_STABLE_EDGE_ABOVE` (1415-1416) and `mean_pnl_ci` (1079, with `kpi.py:21` importing it back as `_mean_pnl_ci`). Nothing is skipped; the reuse is pinned as an identity assertion instead (`kpi._mean_pnl_ci is sa.mean_pnl_ci`). |
| 7 | "reuse the existing CI helper (`kpi._mean_pnl_ci`)" and drawdown logic at `kpi.compute_trade_analytics` (:621-627, :631-646) | **PARTLY REJECTED (stale)** | `_mean_pnl_ci` no longer lives in `kpi.py` — #516 moved it to the analytics module and `kpi` imports it back; the line numbers it cites are pre-#516. Also, `kpi`'s drawdown reads a **different curve** (`equity_series`: realized closes + float marks) from the capital-level curve this ticket measures (see below), so it is an algorithm oracle on a synthetic curve, not a value oracle on the account curve. |
| 8 | CodeRabbit's 3-task split (1.1 drawdown / 1.2 skewness+sortino / 1.3 CI reuse + regression) | **MERGED** | Re-split into 2 pure-function tasks + wiring + card, risk-first. Two of its tasks were test-writing for tests it had already described; those collapse into their tasks' verification. |
| 9 | Assumption 5's "proposed behaviours" (skewness `None` under three values, invalid points skipped, `None` percentage when the peak ≤ 0, "recovery" = at-or-above the old peak, no-drop → `None` durations, percent-per-trade unit, absent from `build`) | **ADOPTED** | It independently flagged exactly the differences this plan freezes. They are recorded as the ticket's "clean N<2 / zero-variance handling" being made explicit, and the two `kpi` differences (invalid point, non-positive peak) are pinned in a test so the divergence is deliberate. |

Nothing from that comment stays `[UNVERIFIED]`: every path, symbol and line it cited was re-checked
against the working tree above, and the only genuinely unknown item — the remote state of #516 — is now
resolved locally (PR #533 merged, #516 closed 2026-10-13).

## Step 0A.2 — open questions resolved from code (the operator was asked nothing)

The issue carries no `needs-answers` label and no Open-questions section, but four questions had to be
answered before the contract could freeze. Each was resolved from the repository, never assumed:

1. **Do the #516 CI/SQN symbols exist to reuse?** Yes — `core_brain/statistical_analytics.py`
   (`mean_pnl_ci`, `ci_critical_value`, `system_quality_number`, `sqn_band`), with `kpi.py:21-22`
   importing `mean_pnl_ci as _mean_pnl_ci`. #516 is closed; PR #533 merged. The ticket's "reuse only"
   rule is satisfiable today, so no sequencing blocker and no skip-marked test.
2. **Which curve does the recovery metric read?** The caller supplies one (settled: "pure functions over
   a caller-supplied equity series … Curve assembly is out of scope"). This payload passes the **venue
   account-value curve** `kpi.report` already computes (`kpi.py:2220-2226`, `account_series`), because
   (a) the ticket's Relevant-files row names `core_brain/account.py:read_account` the "venue value curve
   source" and `tests/test_account_kpi.py:166` the curve's own pin; (b) the ticket is *capital*-level,
   and the account curve is the capital while `equity_series` is run-scoped realized + float; (c) the
   merged #516 demo builder already seeds exactly this curve for these metrics
   (`scripts/build_demo_db.py:672-707`, "so the capital-risk metrics have a real series"), and the
   measured closes curve on that store is deeply underwater and never recovers — a card that can only
   ever read `unmeasured` cannot be verified on the screen. The rejected alternative (the close-stepped
   realized curve, which makes the field name `drawdown_recovery_trades` literally true in production)
   is recorded in § Rejected, with its reason.
3. **Which series do skewness and Sortino read?** The per-close percent returns `kpi` already builds
   (`100 × realized_pnl / cost_basis`, `kpi.py:369-380`), restricted to closes with a positive cost
   basis. `build()` already holds that exact population as `position_returns(closes)["positions"]`
   (`statistical_analytics.py:153-201`), so `build()` passes `p["pnl_pct"]` and no second percent
   formula is written. Same population ⇒ `kpi`'s Sortino is a real oracle.
4. **Does the payload version move?** Yes — `KPI_PAYLOAD_VERSION` is documented "Bump this whenever new
   payload fields ship" (`kpi.py:45-49`, currently 256) and the page pins it equal to
   `EXPECTED_PAYLOAD_VERSION` (`dashboard/static/app.js:88`, asserted by
   `tests/test_analytics_api.py:170-171`). Both move to 257 in the same commit.

## Persona application

- **`code-explorer`** (applied inline — this host exposes no sub-agent spawn other than web research, and
  the persona's method was followed by hand rather than simulated):
  entry points = `/api/kpi` (`dashboard/server.py:3156`) → `kpi.report` (`kpi.py:1230`) →
  `statistical_analytics.build` (`kpi.py:2300`) → the payload's `statistical_analytics` key →
  `renderAnalyticsSurface` (`app.js:5005-5008`) → the five sibling cards in `index.html:470-522`. One
  production caller of `build()`; one renderer per card; 15 closes and 45 account marks on the demo
  store; the harness `tests/js/analytics_surface_harness.cjs` mounts each renderer directly. Shared
  utilities worth reusing: `_finite`, `_close_field`, `_percentile`, `ci_critical_value`,
  `mean_pnl_ci`, `system_quality_number`.
- **`type-design-analyzer`** (applied to the frozen contract): encapsulation — the section is a plain
  dict built by one function, and the renderer receives band *names*, never thresholds (enforced by the
  mount test that changes a band in the payload and asserts the chip changes). Invariant expression —
  `good`/`average`/`bad`/`unmeasured` is a closed vocabulary and every band function returns `unmeasured`
  rather than a verdict nobody could make. Invariant usefulness — the two settled tables are the
  operator's own thresholds, and `None` (never `0`) is the module's existing missing-value contract.
  Enforcement — score: strong on the two bands, deliberately absent on recovery (an unbanded metric is
  the honest encoding of "no intrinsic good/bad exists"); the improvement suggestion was to keep the
  recovery half out of the band vocabulary entirely, which the operator had already settled.

## Step 5 — one improvement proposal (evidence-based)

**Adopted by default (simplification):** create **no** new demo fixture. Evidence, verbatim from the
merged #516 demo builder, `scripts/build_demo_db.py:672-676`:

> `def scenario_account_curve(reg, t, out) -> str:` … `"""An equity curve with a drawdown and recovery and asymmetric`
> `returns, so the capital-risk metrics have a real series: mostly small`
> `wins with a few large losses (negative skew), one deep drawdown that`
> `recovers to a new high, one shallower drawdown still open."""`

Measured on that store (this planning session, `scripts.build_demo_db.build` into a temp path, then
`kpi.report(run_id="all")`): 15 closes → `pnl_skewness = -1.504061` (band `bad`), `sortino_ratio(0.0) =
-0.23345` matching `kpi`'s `-0.23344982…`, and 45 account marks → 3 regained drawdowns, deepest
`14.60` usd / `11.9574` % of its `122.10` peak, median `4` marks / `4800` s, p90 `7.2` marks / `8640` s.
So the demo store is already a populated, checkable fixture for all three metrics; this branch writes no
demo rows and instead **pins those numbers in a new `tests/test_build_demo_db.py` case**.

**Recorded rejection (do not resurface):** unifying the Sortino/drawdown math by having `kpi.py` import
the new functions from the analytics module. Rejected on the operator's own settled decisions —
"implement standalone pure functions in `core_brain/statistical_analytics.py`; use `kpi` values as
cross-check oracles in tests. **Do not refactor `kpi.py`**" — and because `kpi`'s drawdown reads a
different curve than this section's. The tests pin the two implementations together instead.

## Frozen interface contract (Step 4) — `core_brain/statistical_analytics.py`

```python
# --- Capital risk (#517) -----------------------------------------------------
# The settled skewness table: a right tail is good, a left tail is the
# catastrophic-loss scenario, and the closed band between is "no dominant tail".
SKEWNESS_AVERAGE_AT_OR_ABOVE = -0.5
SKEWNESS_BAND_WORDS = {"good": "upside tails -- losses stay small, wins occasionally large",
                       "average": "roughly symmetric -- no dominant tail",
                       "bad": "downside tails -- the catastrophic-loss shape",
                       "unmeasured": "fewer than three closes, or every close identical"}
# The settled Sortino table, on return per unit of downside pain.
SORTINO_GOOD_AT_OR_ABOVE = 2.0
SORTINO_AVERAGE_AT_OR_ABOVE = 1.0
SORTINO_BAND_WORDS = {"good": "returns are twice the downside pain",
                      "average": "returns merely match the downside pain",
                      "bad": "the downside pain outweighs the returns",
                      "unmeasured": "fewer than two closes, or no spread to measure"}
# The shipped risk-free assumption, percent per close -- the unit every return is in.
RISK_FREE_PCT_PER_CLOSE = 0.0

def skewness_band(value: Optional[float]) -> str            # good | average | bad | unmeasured
def pnl_skewness(return_pcts: Sequence[float]) -> dict      # {"pnl_skewness","band","meaning","n"}
def sortino_band(value: Optional[float]) -> str
def sortino_ratio(return_pcts, risk_free=RISK_FREE_PCT_PER_CLOSE) -> Optional[float]
def sortino(return_pcts, risk_free=RISK_FREE_PCT_PER_CLOSE) -> dict
        # {"sortino_ratio","risk_free_pct_per_close","band","meaning","n"}
def drawdown_episodes(points, starting_value=None) -> list[dict]
        # curve order; {"peak","trough","depth_usd","depth_pct","recovered","points","seconds"}
def drawdown_recovery(points, starting_value=None) -> dict
def capital_risk(return_pcts, curve=()) -> dict
        # {"units","pnl_skewness","sortino_ratio","drawdown_recovery"}
```

- **`drawdown_recovery` return (shape-frozen):**
  `{"units": "curve_points_and_seconds", "episodes": int, "open": int, "skipped": int,
   "deepest_usd": float|None, "deepest_pct": float|None, "deepest_recovered": bool|None,
   "drawdown_recovery_trades": {"median": float|None, "p90": float|None, "n": int},
   "drawdown_recovery_sec": {"median": float|None, "p90": float|None, "n": int}}`
  — `drawdown_recovery_sec` is **omitted entirely** when no regained episode carried both endpoint
  timestamps (the ticket's "when timestamps are supplied"; a zero would read as "instant recovery").
- **Reading the curve (frozen semantics):** points are read in the order given and never sorted; a point
  whose `v` fails `_finite` is skipped and counted in `skipped`, its neighbours stay in order; the peak
  starts at `starting_value` when supplied and otherwise at the first usable point; a point with
  `v >= peak` **regains** the old peak and closes the open episode (`points` = index distance from the
  peak, `seconds` = `ts` distance when both stamps are finite), then the peak updates; `v < peak` opens
  or deepens the episode's trough. An episode still open at the end of the curve is counted in `open`,
  gets **no** length and never enters the medians. A curve with no drop at all reports
  `deepest_usd == 0.0` (a real measurement: nothing dropped), `deepest_recovered is None`, `episodes == 0`
  and `None` medians; an empty curve reports every figure `None`. The medians are over the **regained**
  episodes and are read off the module's existing `_percentile`, so `median` IS `statistics.median`.
- **Documented, deliberate differences from `kpi.compute_trade_analytics`** (pinned by a test so the
  divergence stays deliberate): a point with an unusable `v` is skipped here and counted as `0.0` there;
  a peak at or below zero gives `deepest_pct is None` here and `0.0` there; the two functions read
  different curves by design, so no value-level oracle is claimed for the payload.
- **Band tables (settled, inclusive edges):** skewness `good` when `> 0`, `average` when
  `>= -0.5`, `bad` otherwise; Sortino `good` when `>= 2.0`, `average` when `>= 1.0`, `bad` otherwise;
  `unmeasured` for `None`/non-finite in both.
- **`build()` change:** `build(closes, fills, markouts, starting_capital, quotes=None, equity_curve=None)`.
  All existing positional parameters keep their positions; `equity_curve` is additive and keyword-only by
  position. The payload gains one key:
  `"capital_risk": capital_risk([p["pnl_pct"] for p in returns["positions"]], equity_curve or ())`.
  `capital_risk` receives only plain numbers and a curve — never registry rows — so it stays unit-testable
  without a store, and it holds no CI or SQN math of its own.
- **`kpi.py` edits (frozen):** `KPI_PAYLOAD_VERSION = 257`; the account-value comprehension hoisted into
  `account_series` before the return dict; `build_statistical_analytics(closes, fills, markouts,
  starting_capital, quotes, equity_curve=account_series)`. The `account_series` payload value is
  byte-identical.
- **Card copy (frozen):** title `CAPITAL RISK — DRAWDOWN RECOVERY, SKEWNESS & SORTINO`, right-hand note
  `Per Close & Per Curve Point · Percent · Descriptive & Banded`, host `#capital-risk-readout` mounted
  directly after `#robustness-readout`. Tiles, in order:
  `Deepest DD ($)` · `Recovery median (marks)` · `Recovery p90 (marks)` · `Drawdowns regained` — all four
  with **no chip** — then `PnL skewness` and `Sortino` with their payload chips. Disclosure lines name
  the curve the numbers were read off ("the venue account-value curve, one point per sweep") and state
  that `CI`/`SQN` are the robustness card's own single definition, reused rather than restated.
- **Renderer rule (frozen):** `BAND_CHIP = {good: 'live', average: 'warn', bad: 'bad'}`; the same
  `chip()` guard the sibling renderers use, so a payload with no band key (and a payload from before this
  section existed) renders **no chips**, and `unmeasured` names itself without a colour.

## Dependency graph (Step 6)

```
T1 drawdown_recovery ─┐
T2 skewness + sortino ┴─► T3 payload wiring ─► T4 dashboard card
```

T1 and T2 are independent of each other (different functions, one shared test file created by T1 and
appended to by T2 — so T2's file write depends on T1 having created it; that is a file-order dependency,
not a logic one). T3 consumes T1 + T2's exports. T4 consumes T3's payload shape. Risk-first: T1 carries
the only genuinely new semantics (episode enumeration over a caller-supplied curve), so it lands first.

## Tasks

### [x] T1 — Drawdown recovery over a caller-supplied equity curve · size M · `[Backend/Logic]`
**Files:** `core_brain/statistical_analytics.py`, `tests/test_capital_risk_analytics.py` (NEW).
**Builds:** `drawdown_episodes()`, `drawdown_recovery()`, the `# Capital risk (#517)` section header
comment and the module docstring paragraph's first half, exactly per the frozen contract.
**Depends on:** nothing.
**Verification (agent-run, `python -m pytest -q tests/test_capital_risk_analytics.py`):**
- Recovery curve, `starting_value=1000`: `v = 1100, 900, 950, 1100, 1200`, `ts = 100..500` →
  `deepest_usd == 200`, `deepest_pct ≈ 18.181818` (`pytest.approx`), `deepest_recovered is True`,
  `episodes == 1`, `open == 0`, `drawdown_recovery_trades == {"median": 3.0, "p90": 3.0, "n": 1}`,
  `drawdown_recovery_sec == {"median": 300.0, "p90": 300.0, "n": 1}` — the episode spans the three
  points from the peak to the recovery (`900`, `950`, the `1100` that equals the old peak and is a
  recovery rather than a new peak, pinned on purpose), so 3 points and the `ts` gap `100 → 400`.
- Same curve without `ts`: identical lengths in points, `"drawdown_recovery_sec" not in out`.
- No recovery: `starting_value=1000`, points `1200, 1000, 1100` → `deepest_usd == 200`,
  `deepest_pct ≈ 16.666667`, `deepest_recovered is False`, `episodes == 0`, `open == 1`, both medians
  `None`.
- Rising only `1000, 1100, 1200` → `deepest_usd == 0.0`, `deepest_recovered is None`, `episodes == 0`.
- `[]` → every figure `None`, `episodes == 0`, no exception. Peak at zero (`starting_value=0`, points
  `0, -50, 0`) → `deepest_usd == 50`, `deepest_pct is None`.
- Bad points (`v` = `float("nan")`, `None`, `"abc"`, `float("inf")` interleaved) → same figures as the
  clean curve, `skipped == 4`, no exception; a point with a bad `ts` keeps its point length and yields no
  seconds.
- `json.dumps(out, allow_nan=False)` round-trips on every one of the above.
- **Oracle:** `kpi.compute_trade_analytics([], 1000.0, [{"v": v} for v in (1100, 900, 950, 1100, 1200)], [])`
  reports `max_drawdown_usd == 200` and `max_drawdown_pct ≈ 18.181818` — asserted equal to
  `deepest_usd`/`deepest_pct` with `pytest.approx`. Excluded on purpose: the zero-peak and bad-point
  curves, where the two differ (§ Frozen contract).
- **RED:** `ImportError: cannot import name 'drawdown_recovery' from 'core_brain.statistical_analytics'`.

### [x] T2 — PnL skewness and risk-free Sortino · size M · `[Backend/Logic]`
**Files:** `core_brain/statistical_analytics.py`, `tests/test_capital_risk_analytics.py`.
**Builds:** `pnl_skewness()`, `skewness_band()`, `sortino_ratio()`, `sortino()`, `sortino_band()`, the
band constants/tables and the docstring paragraph's second half.
**Depends on:** T1 (creates the test file it appends to).
**Verification (agent-run, same file):**
- Skewness vectors: `[1,1,1,1,-10]` → `-1.5`; `[-1,-1,-1,-1,10]` → `1.5`; `[-1,0,1]` → `0.0` (band
  `average`); `[1,2]`, `[2,2,2]`, `[]`, `[5]` → `None` with band `unmeasured` — never `bad`.
- Band edges on the rounded figure: `skewness_band(0.0) == "average"`, `skewness_band(-0.5) == "average"`,
  `skewness_band(-0.500001) == "bad"`, `skewness_band(0.000001) == "good"`,
  `skewness_band(None) == "unmeasured"`.
- Sortino: `[-1,-1,-1,-1,10]` → `1.2` at `risk_free=0`, `0.1` at `1`, `-0.91227` at `11`
  (`pytest.approx`); `[2,2,2,2,-5]` → `0.12`; `[]`, `[3]`, `[2,2,2]`, `[1,2,3]` → `None`; `risk_free=float("nan")`
  → `None`.
- Band edges: `sortino_band(2.0) == "good"`, `sortino_band(1.999999) == "average"`,
  `sortino_band(1.0) == "average"`, `sortino_band(0.999999) == "bad"`, `sortino_band(-0.23) == "bad"`.
- **Oracle:** for both datasets, with closes built as `{"realized_pnl": r, "cost_basis": 100.0}`,
  `kpi.compute_trade_analytics(closes, 100.0, [], [])["sortino_ratio"]` equals
  `sortino_ratio([...], 0.0)` within `abs=1e-6` (the module rounds to 6 places; `kpi` does not).
  `[-1,-1,-1,-1,10]` also asserts `sortino_ratio(...) > kpi …["sharpe_ratio"]` and `[2,2,2,2,-5]`
  asserts the reverse — the asymmetry the ticket asks the card to expose.
- **Reuse pins (no fork):** `kpi._mean_pnl_ci is sa.mean_pnl_ci`; `sa.system_quality_number` is the SQN
  the #516 card reads (same object, `band` vocabulary unchanged); the new test file contains no
  confidence-interval and no SQN formula (source-scan assertion over its own text, the way
  `tests/test_ci_critical_source.py` pins a single critical-value source).
- **RED:** `ImportError: cannot import name 'pnl_skewness' from 'core_brain.statistical_analytics'`.

### [x] T3 — Payload wiring: `capital_risk` on `statistical_analytics` · size S/M · `[Backend/Logic]`
**Files:** `core_brain/statistical_analytics.py` (module docstring paragraph + `capital_risk` +
`build()`'s new keyword and key), `core_brain/kpi.py` (3 edits), `tests/test_statistical_analytics.py`,
`tests/test_build_demo_db.py`, `dashboard/static/app.js` (the version constant only).
**Depends on:** T1, T2.
**Verification (agent-run):**
`python -m pytest -q tests/test_capital_risk_analytics.py tests/test_statistical_analytics.py tests/test_build_demo_db.py tests/test_kpi.py tests/test_analytics_api.py tests/test_trade_analytics.py tests/test_markout_robustness_analytics.py`
- `build([], [], [], 100.0)["capital_risk"]` is present, shape-frozen, every figure `None` /
  `unmeasured`, and `drawdown_recovery_sec` omitted.
- `build(closes, [], [], 100.0, equity_curve=points)["capital_risk"]["drawdown_recovery"]["deepest_usd"]`
  moves with the curve while every sibling section keeps its exact previous shape (the #516
  `set(bare[section]) == set(loaded[section])` idiom, extended).
- `kpi.report` on the demo store: `statistical_analytics["capital_risk"]` reads `pnl_skewness ≈ -1.504061`
  (band `bad`), `sortino_ratio ≈ -0.23345` (band `bad`), `deepest_usd == 14.6`, `deepest_pct ≈ 11.9574`,
  `episodes == 3`, `open == 0`, `median 4` marks / `4800` s, `p90 7.2` marks / `8640` s — and
  `payload["account_series"]` is unchanged in length and values.
- `KPI_PAYLOAD_VERSION == 257 == EXPECTED_PAYLOAD_VERSION` (the existing pin in
  `tests/test_analytics_api.py` covers the pair).
- **RED:** `KeyError: 'capital_risk'` on the payload, and the version pin failing at 256.

### [x] T4 — The `#capital-risk-readout` card · size M · `[Design/UI]`
**Files:** `dashboard/static/index.html`, `dashboard/static/app.js`, `tests/js/analytics_surface_harness.cjs`,
`tests/test_analytics_surface_mount.py`.
**Depends on:** T3.
**Verification (agent-run, `python -m pytest -q tests/test_analytics_surface_mount.py`; skips with a
reason on a Node-less host):**
- Section payload (skewness `bad`, Sortino `good`, drawdown figures from the demo store) → the six tiles
  read their figures, the four recovery tiles carry **no** chip, the two banded tiles carry exactly two
  chips (`analytics-chip bad` ×1, `analytics-chip live` ×1), and no tile prints `NaN`, `undefined`,
  `null` or `0.00` where the payload is `None`.
- A payload with `unmeasured` bands renders `UNMEASURED` with no colour; a payload **without** the
  `capital_risk` key (every store written before this change) renders no chips and no exception —
  back-compat tested, not assumed.
- Changing a band name in the payload changes the chip and nothing else (the renderer holds no threshold).
- Every figure on the card matches the payload the socket delivered (no invented numbers, the
  `renderAnalyticsSurface` call is wrapped like its siblings so one broken card cannot blank the page).
- **RED (before T4):** the harness reports `capital_risk_html` empty, and `index.html` has no
  `#capital-risk-readout`.

**Checkpoints (Mode A `auto` reports one line each):**
- **CHKPT A (after T1+T2):** both pure layers green in the ticket's focused file, RED output captured for
  each, and nothing outside `core_brain/statistical_analytics.py` + the new test file has moved.
- **CHKPT B (after T4):** the payload carries `capital_risk`, the card renders on the demo store with two
  chips and four unbanded recovery tiles, and every focused suite in `CONSTRAINTS.md` is green.

## What fails without the change (RED evidence to capture)

1. T1: `ImportError: cannot import name 'drawdown_recovery' from 'core_brain.statistical_analytics'`.
2. T2: `ImportError: cannot import name 'pnl_skewness' from 'core_brain.statistical_analytics'`.
3. T3: `KeyError: 'capital_risk'` on `build()`'s payload.
4. T4: harness `capital_risk_html` empty; `analytics-chip` count `0` on the new card.

## How to verify (operator — hands-on, no venue, no money at risk)

Nothing in this change places, cancels or merges an order, signs anything, or touches the registry; no
step reaches the venue.

1. `python -m scripts.build_demo_db --out data/demo_517.db --force` — ends with
   `demo registry written:` and lists the scenarios (unchanged by this branch).
2. `python -m dashboard.server --db data/demo_517.db`, then open `http://127.0.0.1:8799` and go to
   **Reports & Analytics**.
3. Read the new card `CAPITAL RISK — DRAWDOWN RECOVERY, SKEWNESS & SORTINO`, directly under
   `POST-FILL ROBUSTNESS — MARKOUT, MAE, CI & SQN`. Expected on the demo store: `Deepest DD ($)` reads
   `14.60`, the recovery tiles read `4`/`7.2` marks with `4800`/`8640` seconds beside them and **no
   chips**, `PnL skewness` reads about `-1.50` with a red `BAD` chip, and `Sortino` reads about `-0.23`
   with a red `BAD` chip.
4. Same card against the live registry (`python -m dashboard.server`, then the same page): expected —
   the card is present, recovery and skewness read `unmeasured` until that store holds enough closes and
   account marks, and no tile prints `0.00` where the store recorded nothing.
5. A failure looks like one of: the card missing entirely, a chip on any recovery tile, a tile printing
   `0.00`/`NaN`/`undefined` instead of `unmeasured`, or `CI`/`SQN` restated in a second place on this
   card instead of the robustness card's own figures.

## Open decisions carried to Station III

None. The curve, the series, the band tables, the field names, the card copy and the version bump are all
frozen above; anything that changes them is a planning change recorded in this file first.
