# Plan — #514 [ADD] Rescue friction analytics for stop-loss drag and taker costs

Branch: i514/add-rescue-friction-analytics-for-stop-loss-drag | Issue: #514
Size: Standard | Type: Code + UX/Copy (readout) | Stack: Python 3.12 + pytest, vanilla JS dashboard
Labels: `enhancement`, `ready-for-agent`, `family:execution-telemetry`
Note: supersedes the #513 `fill_path_edge` plan in this file's history (kept in git).

Spec: `SPEC.md` (this branch) · Guardrails: `CONSTRAINTS.md` (this branch).

## CodeRabbit intake note

Read once (rendered copy only; the byte-identical `<!-- <rawResChunk><planningResult> -->` echo
was skipped).

**Adopted**
- The per-close function split, the unit set (cents / ratios), the "missing stays missing" rule.
- The named fallback constant for the stop reference, and the `recorded` / `assumed_offset`
  source tag; "never use `fresh_bid` as the stop reference".
- Its four test inputs (zero slippage, adverse + large fee, price improvement, missing
  `trigger_bid` + `NULL` fee) and the zero-denominator and no-mutation cases. All four are used,
  with the arithmetic re-derived independently (see the fixtures below).
- "Do not recover fees from `venue_fees.recover_fee`" — verified: the `closes` schema
  (`core_brain/order_registry.py:284-305`) has no `usdcSize`, so a recovered fee would be
  invented.

**Rejected**
- **New module `core_brain/rescue_friction.py`.** Its stated reason was "avoids edit clashes with
  #513, which will likely change `statistical_analytics.py`" — #513 is merged and closed
  (`f893ec0`), so the reason is gone. Against it: `statistical_analytics.py` already declares
  `RESCUE_METHODS` (`:58`) and the `_finite` helper (`:87`) this work needs, and its `build()`
  already returns every other section of the same payload from the same `closes` list. A sibling
  file would need a **fifth** copy of `("single_buy_exit", "naked_exit", "ladder_exit")`, and the
  #513 plan rejected exactly that shape for exactly that reason (see this file's history).
  The ticket leaves the location open. **Decision: extend `statistical_analytics.py`.**
- **`PRECISION = 6` constant.** The module rounds inline (`round(x, 6)`) in every section; a
  named constant for a literal used the same way adds a name to learn, not a rule to hold.
- **`mark_delta(close)` as a separate function.** It is `trigger_bid - fresh_bid` — the latency
  hop, with the same "both recorded or `None`" rule. A second name for the same number is the
  redundancy the ticket already banned for degradation/overshoot.
- **`payoff_degradation(close)`.** The ticket's settled decision: overshoot replaces degradation,
  because degradation was mathematically `-SLED` (the `entry` term cancels) — a duplicate. Its
  `theoretical_usd = shares * reference - cost_basis` is that same number.
- **`taker_cost_drag` with `friction_usd = drag_usd + fee` and a net-loss denominator.** The
  ticket defines TCDR as `fee_total / |realized_payoff|`; folding drag into the numerator and
  subtracting the fee from the denominator measures a different quantity, and the ticket's TCDR
  is the one with a traffic-light band.
- **`drag_usd` / `drag_pct` dollar and percent forms.** The ticket displays cents for the drags
  and says bps/percent forms apply only "where shown" — nothing shows them. Extra numbers with
  no reader are numbers that drift.
- **A new CSS hue or band class family.** `dashboard/static/styles.css:614-616` already defines
  `.analytics-chip.live` / `.warn` / `.bad` (green / amber / red) — the theme's shared status
  palette, currently defined and unused. The readout uses them; no CSS edit at all.

**Corrections to its claims (verified in the tree, this session)**
- "No #513 classifier exists in the tree" — **false now**.  `statistical_analytics._fill_path` (`:213`) and `RESCUE_METHODS` (`:58`) exist on `main`; #513 merged before this plan.
- "The design document does not exist" — it does:
  `docs/superpowers/specs/2026-10-12-hard-stop-fresh-poll-design.md`. The ticket says not to
  create one; none is created. It is cited as background only.
- "The code has no `entry_price - 0.04` fallback" — correct, which is why the fallback ships as
  a tagged assumption (`STOP_OFFSET_FALLBACK`), never as an observed price.

**[UNVERIFIED] remaining:** none. Every seam below was read in the tree at planning time.

## Frozen interface contract

All functions are pure readers over one close, which is either the `dict` that
`OrderRegistry.get_all_closes()` returns (`SELECT * FROM closes`) or a `CloseRecord`; rows are
read through the module's existing `_close_field`. Returns are plain dicts of `float | None` and
`str | None` — never a dataclass, never `NaN`/`Infinity`, `allow_nan=False`-safe, rounded to 6
decimals. Inputs are never mutated.

```python
STOP_OFFSET_FALLBACK = 0.04   # ASSUMED reference, not observed: equals the system default
                              # `config.single_buy_max_loss_usd` (profile presets move it
                              # to 0.030-0.065), so every value derived from it is tagged.
FRICTION_BAND_METRICS = ("sled", "latency", "execution", "tcdr", "overshoot")
_LOSS_EPSILON = 1e-9          # private; mirrors kpi.pnl_by_fill_path's zero-loss guard

def rescue_drag(close) -> dict:
    """SLED and its two exact hops, in cents.
    exit_price     = the recorded leg price (`dn_price`, else `up_price`); when the leg
                     price is absent or ambiguous, `proceeds / shares`.
    reference_bid  = `trigger_bid` (source "recorded"), else `entry - STOP_OFFSET_FALLBACK`
                     (source "assumed_offset"). `fresh_bid` is never the reference.
    latency_drag_cents   = (trigger_bid - fresh_bid) * 100   -> only when BOTH recorded
    execution_drag_cents = (fresh_bid - exit_price) * 100    -> only when fresh_bid + exit
    sled_cents           = latency + execution when both hops measured (exact by
                           construction); else (reference_bid - exit_price) * 100
    Signs unclipped. Every value `None` when its own inputs are missing."""

def taker_cost_drag_ratio(close) -> dict:
    """TCDR in the ticket's own definition: fee / |realized_pnl|.
    fee_usd, fee_measured (bool), realized_pnl_usd, tcdr.
    tcdr is None when the fee is unmeasured or |realized_pnl| <= _LOSS_EPSILON."""

def overshoot_ratio(close) -> dict:
    """SLED in units of the risk the stop intended to cap.
    planned_risk_cents = (entry_price - reference_bid) * 100
    overshoot_ratio    = sled_cents / planned_risk_cents
    status: "measured" | "unmeasured" | "no_planned_risk"."""

def friction_band(metric: str, value: Optional[float]) -> str:
    """The ticket's traffic-light band for one metric's DISPLAYED value.
    "good" | "average" | "bad" | "unmeasured". Unknown `metric` -> ValueError (a typo must
    not render as `unmeasured`, which is a data statement, not a typo statement).
    Drag metrics arrive in cents; the ticket's thresholds are price dollars (its own
    threshold-basis note: 0.02 ~= one book spread), so the band converts cents -> dollars.
    Overshoot/TCDR are unitless ratios. Signed extension, documented in the docstring:
    a negative overshoot (price improvement) reads "good", extending the table's `= 0`."""

def rescue_friction_summary(closes) -> dict:
    """Always present, like `fill_path_edge`. Population = `method` in RESCUE_METHODS
    (lowercased/stripped), never narrowed by `reason`.
    Counts:  rescue_count, measured_count (SLED measurable), recorded_reference_count,
             assumed_reference_count, fee_measured_count, non_rescue_count
    Means (each over its own measured subset, None when empty):
             mean_sled_cents, mean_latency_drag_cents, mean_execution_drag_cents,
             mean_tcdr, mean_overshoot_ratio
    bands:   {metric: friction_band(metric, mean)} for the five FRICTION_BAND_METRICS
    units:   "cents_per_share" (the drag means; tcdr and overshoot are ratios)"""
```

`build()` gains one additive key: `"rescue_friction": rescue_friction_summary(closes)`.

Band thresholds, as frozen (value first, in the metric's own unit):

| metric | 🟢 good | 🟡 average | 🔴 bad |
| --- | --- | --- | --- |
| sled (¢) | `<= 0` | `(0, 2.0]` | `> 2.0` |
| latency (¢) | `>= 0` | `(-2.0, 0)` | `<= -2.0` |
| execution (¢) | `<= 2.0` | `(2.0, 5.0]` | `> 5.0` |
| tcdr (ratio) | `<= 0` | `(0, 0.10)` | `>= 0.10` |
| overshoot (ratio) | `<= 0` | `(0, 1.0]` | `> 1.0` |

(Cents = the ticket's 0.02 / 0.05 dollars. `unmeasured` when the value is `None`.)

## Test fixtures (arithmetic re-derived, not copied)

| # | Input (`shares`, `cost_basis`, `proceeds`, `realized_pnl`, `fee`, `trigger_bid`, `fresh_bid`, `dn_price`) | Expected |
| --- | --- | --- |
| A zero slippage (the H2 exhibit's fresh-book close) | `5.0, 3.0, 0.75, -2.25, 0.0, 0.15, 0.15, 0.15` | entry 0.60, exit 0.15; latency `0.0`, execution `0.0`, sled `0.0`, planned risk `45.0`, overshoot `0.0`, tcdr `0.0`; all bands good |
| B adverse + large fee | `10.0, 6.0, 1.2, -4.8, 0.30, 0.20, 0.15, 0.12` | exit 0.12; latency `5.0`, execution `3.0`, sled `8.0` (= 5 + 3 exactly), planned risk `40.0`, overshoot `0.2`, tcdr `0.0625`; bands good / average / bad / average / average |
| C price improvement | `10.0, 6.0, 2.30, -3.70, 0.0, 0.20, 0.22, 0.23` | latency `-2.0`, execution `-1.0`, sled `-3.0`, overshoot `-0.075`; signs kept, bands bad / good / good |
| D missing `trigger_bid` + `NULL` fee | `5.0, 3.0, 2.50, -0.50, None, None, 0.50, 0.50` | reference `0.56` source `assumed_offset`; latency `None`, execution `0.0`, sled `6.0`, planned risk `4.0`, overshoot `1.5` (bad), tcdr `None`, `fee_measured` false |
| E stale-mark drag (the reason this work exists) | `5.0, 2.70, 0.75, -1.95, 0.0, 0.83, 0.15, 0.15` | latency `68.0`, execution `0.0`, sled `68.0`; planned risk negative (trigger above entry) -> overshoot `None`, status `no_planned_risk` |

## Dependency graph & tasks

```
T1 (drag decomposition) ─┬─> T3 (build() key) ──> T4 (dashboard readout)
T2 (tcdr/overshoot/bands)┘            ^
        ^                              |
        └── depends on T1 (reuses rescue_drag) ──┘
```
T2 depends on T1 · T3 depends on T1 + T2 · T4 depends on T3. Risk-first: the drag
decomposition and the reference fallback carry the real uncertainty, so T1 lands first.

Completion state (marked here as each task lands — Station III owns this, Station II owns
everything else in this file):

- [x] T1 — SLED decomposition with its tests
- [x] T2 — TCDR, overshoot, bands and the summary
- [x] T3 — wire the payload
- [x] T4 — dashboard readout with traffic-light bands

All four tasks committed locally on this branch. Built as planned, with two recorded
deviations: (1) `SPEC.md`'s "no shares" edge-case row was corrected to say which metrics
need a share count (the drag metrics read recorded prices, so they do not) — the code does
not require a share count for a per-share figure; (2) the latency-drag band was implemented
literally from the ticket's table even though that row contradicts the ticket's own
definition of the metric — recorded as N1 in `docs/issues/514-noticed-but-not-touching.md`
for the operator's call.

### T1 — SLED decomposition with its tests · S · `[Backend/Logic]`
- Files: `core_brain/statistical_analytics.py`,
  `tests/test_rescue_friction_analytics.py` (new).
- Builds: `STOP_OFFSET_FALLBACK`, `_LOSS_EPSILON`, `_entry_price`, `_exit_price`,
  `_stop_reference`, `rescue_drag()`. Test file opens with a docstring naming #514 and the
  scenarios, a `_close(...)` helper (defaulting `method="single_buy_exit"`, following
  `tests/test_statistical_analytics.py`), and fixtures A, B, C, D, E.
- Depends on: —
- Verify: `python -m pytest -q tests/test_rescue_friction_analytics.py` (RED first: ImportError),
  including exact additivity for B, unclipped signs for C, and the fallback tag for D.

### T2 — TCDR, overshoot, bands and the summary · S · `[Backend/Logic]`
- Files: `core_brain/statistical_analytics.py`, `tests/test_rescue_friction_analytics.py`.
- Builds: `taker_cost_drag_ratio()`, `overshoot_ratio()`, `FRICTION_BAND_METRICS`,
  `friction_band()`, `rescue_friction_summary()`. Tests: the boundary values in the band table
  (0 / 2.0 / 2.01 cents sled, ±2.0 latency, 5.0 / 5.01 execution, 0.10 tcdr, 1.0 / 1.0001
  overshoot), the two zero-division guards (asserting `ZeroDivisionError` would occur without
  them), the empty dataset, a mixed list carrying `shadow_merge` + `venue_sync` rows
  (`non_rescue_count`), dict vs `CloseRecord` equivalence, `json.dumps(..., allow_nan=False)`,
  and a no-mutation assertion on the input copy.
- Depends on: T1
- Verify: `python -m pytest -q tests/test_rescue_friction_analytics.py`

### T3 — wire the payload · XS · `[Backend/Logic]`
- Files: `core_brain/statistical_analytics.py` (`build()` + its docstring), plus the populated
  and empty `build()` tests in `tests/test_statistical_analytics.py`.
- Builds: the additive `rescue_friction` key, always present, `None` means + `unmeasured` bands
  on a run with no closes.
- Depends on: T1, T2
- Verify: `python -m pytest -q tests/test_statistical_analytics.py tests/test_fill_path_analytics.py`

### T4 — dashboard readout with traffic-light bands · S · `[UX/Copy]` + `[Design/UI]`
- Files: `dashboard/static/index.html` (new `#rescue-friction-readout` card, after the
  fill-path card, deliberately not `.stats-chart-card` so the sub-view filter cannot blank it),
  `dashboard/static/app.js` (`renderRescueFriction`, its call inside `renderAnalyticsSurface`,
  its `module.exports` entry), `tests/js/analytics_surface_harness.cjs` (stub id + call +
  printed HTML), `tests/test_analytics_surface_mount.py` (new cases).
- Builds: five tiles — SLED, latency drag, execution drag, TCDR, overshoot ratio — each showing
  the mean with a band chip (`analytics-chip` + `live`/`warn`/`bad`, neutral for `unmeasured`),
  a counts footnote (measured / assumed-reference / fee-measured / non-rescue), and one line of
  copy stating this is an accounting view of rescue closes that includes timed exits.
  A `null` mean renders the word `unmeasured` — never `0.00`.
- Depends on: T3
- Verify: `python -m pytest -q tests/test_analytics_surface_mount.py`; then the operator's
  hands-on check (below).

**Checkpoints:** after T2 — every metric measured, focused suite green, nothing else touched.
After T4 — the card renders on the analytics tab with a real store.

**Sub-issues:** skipped, and recorded rather than invented: sub-issue mapping is the
`wayfinder:map` flow (`create-issue/references/wayfinding-operations.md`), and #514 carries no
`wayfinder:map` parent (labels: `enhancement`, `ready-for-agent`, `family:execution-telemetry`).
The dependency graph above is the tracker for this session.

**Persona skips (recorded, not simulated):** `type-design-analyzer` — the contract introduces no
new types to encapsulate; it returns plain dicts of floats/`None`, matching every sibling
function in the module. `code-explorer` — this is Standard-tier work whose seams were read
directly (`order_registry.CloseRecord`/`closes` schema, `single_buy_saver._record_exit_close`,
`statistical_analytics.build`, `app.js` renderers); no legacy path needed tracing.

## Step 5 — one improvement proposal (adopt-by-default: simplification)

Evidence, verbatim from the ticket's acceptance criteria: *"Each metric's dashboard/readout
display applies the traffic-light color band (green/yellow/red) to its current value per the
spec table."* The renderer is otherwise free to hardcode the thresholds — and this repo has
already been burned there: `tests/test_statistical_analytics.py` carries renderer tests whose
whole purpose is to catch invented constants in `app.js` ("invented statistic back in app.js").

**Proposal:** compute the band in Python and ship it in the payload (`bands`), so `app.js` maps
a band name to a class and holds no threshold numeral. Classification: **simplification /
hardening — adopted by default**, folded into T2 and T4 above. Rejected at review time means
moving five thresholds into the renderer and losing the Python boundary tests.

## Contradictions & risks (verified this session)

- **The ticket's band numbers are price dollars; the displayed drag values are cents.** Its
  threshold-basis note ("0.02 ≈ one book spread") is dollars, while "SLED is displayed in
  cents". `friction_band` converts cents → dollars so the frozen table above matches the
  ticket. Interpretation recorded here because it is the one place the ticket's units collide.
- **Today's rescue rows carry no fee.** `single_buy_saver._record_exit_close` never passes
  `fee`, so the column is `NULL`; the only writers of `fee=0.0` are the merge paths
  (`shadow_exec.py:1531` `shadow_merge`, `order_manager.py:1184` `merge`), both outside the
  rescue population. So TCDR reads **`unmeasured`, not 🟢** today — the ticket's "currently
  always 0, so reads 🟢" assumed a recorded zero fee. Missing stays missing; no zero is invented.
- **Only the saver records the audit pair.** `grep trigger_bid|fresh_bid` finds no hits in
  `ladder.py`, `market_resolution.py` or `shadow_exec.py`, so `naked_exit` / `ladder_exit` rows
  will use the assumed 0.04 reference. They are counted separately
  (`assumed_reference_count`) and the metric is not narrowed by `reason` — per the ticket.
- **`0.04` equals the system default only.** `config.single_buy_max_loss_usd = 0.04` is the
  promoted default; profile presets move it (0.030–0.065). The assumed reference is therefore
  a tagged approximation, and the readout says so.
- **The booked exit price can be a floor, not a fill.** `single_buy_saver.py:75`
  `MAX_SELL_SLIPPAGE = 0.02` and `:949`
  `min_price = _floor_to_tick(max(bid - MAX_SELL_SLIPPAGE, tick), tick)` mean a live rescue
  with no reported fill price records the floor. Live execution drag can therefore carry up to
  ~2c of floor effect. Documented, not fixed here — this work measures, it does not change
  execution.
- **Rescue methods include timed exits** (`grace_expired`, ladder timers), not only stop hits.
  The ticket settles the filter as `method`-only, so the readout copy discloses the population
  instead of pretending it is all stops.
- **`overshoot` for a trigger above entry** is `no_planned_risk` (`None`), not `0` or a negative
  ratio: the stop's intended risk was zero or negative, so the question has no answer. Fixture E
  pins it.

## How to verify (operator — hands-on, no pytest)

1. Launch the stack: `.\scripts\spread-hunter-menu.ps1 start` (or `python -m dashboard.server`),
   then open `http://127.0.0.1:8799`.
2. Open the **Performance & Analytics** tab and scroll to the new **RESCUE FRICTION** card,
   directly under **FILL-PATH EDGE**.
3. Read the five tiles: SLED (¢), Latency Drag (¢), Execution Drag (¢), Taker Cost Drag Ratio,
   Overshoot. Each value carries a green / amber / red chip that changes with the value, and
   `unmeasured` where the closes do not carry the price (with no `NaN`, `undefined` or `0.00`
   standing in for a missing number).
4. Compare against the ticket's table on the issue: a SLED above 2¢ on a rescue close should
   read red; a drag at or below zero should read green; overshoot above 1.0× should read red.
5. Rehearse a run that produces rescue closes (`python -m core_brain.shadow_run --minutes 5
   --db data/514_shadow_<stamp>.db`) and reopen the tab: the counts footnote
   (measured / assumed-reference / fee-measured) should move, and a rescue row with no recorded
   `trigger_bid` should raise the assumed-reference count rather than invent a value.
