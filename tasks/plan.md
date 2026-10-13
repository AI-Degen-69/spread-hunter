# Plan — #516 [ADD] Post-fill markout and statistical robustness analytics

Branch: i516/add-post-fill-markout-and-statistical-robustness | Issue: #516
Size: **Standard** (2 source modules + 2 dashboard files + the demo builder; internal-only, no
new dependency, no schema change, no public API change) | Type: **Code + UX/Copy**
Tags: `enhancement`, `ready-for-agent`, `family:execution-telemetry` · Related: #513, #514, #515, #517 · Part of #513
Spec: [`SPEC.md`](../SPEC.md) (Standard tier). Guardrails: [`CONSTRAINTS.md`](../CONSTRAINTS.md), rewritten for this branch — the #515 text it replaced is branch-scoped by its own header and stays in git.

## Pipeline bookkeeping

1. Tree was clean and on `main`, synced with `origin/main` (verified at planning time).
2. `gh issue edit 516 --add-assignee @me` — claimed.
3. Branch from HEAD: `i516/add-post-fill-markout-and-statistical-robustness`. Slug derivation:
   title → `add-post-fill-markout-and-statistical-robustness-analytics` (57 chars) exceeds the
   50-char cap, so it is cut at a word boundary (`...-robustness`, 47 chars) rather than mid-word.
4. `tasks/plan.md` and `tasks/todo.md` held #515's merged plan. This issue has no prior plan, so
   both are replaced, not appended to (Step 6.7 reconciliation applies per-issue only). `SPEC.md`
   is Standard-tier and supersedes #514's spec in this branch (kept in git).
5. Step 0C: #516 carries no `quick-fix` label (labels read live from the tracker this session:
   `enhancement`, `ready-for-agent`, `family:execution-telemetry`) → no gate run, no lane
   offered, no gate text printed.
6. Step 6.6 sub-issue mapping: **skipped, recorded.** The family tickets are plain issues under
   `family:execution-telemetry`, not a `wayfinder:map` with child tickets; four sub-issues for a
   plan that touches two source modules is the bloat the tracker already avoids here. #515 took
   the same route for the same reason.
7. Step 0A `code-explorer` persona and Step 4 `type-design-analyzer` persona: **skipped,
   recorded.** Neither exists under `~/.agents/agents/` in this environment; the seams they would
   trace were read directly in the tree this session (every citation below is repository-confirmed).
8. Step 1 domain skill routing (verified against what is actually installed): loaded and applied
   `frontend-ui-engineering` (the readout card: accessible names, the theme's existing chip
   classes, no new colour vocabulary), `humanizer` (the card's copy — band words are operator
   prose, not jargon), and `incremental-implementation` (the four task-sized increments below).
   `constraint-driven-development` governs `CONSTRAINTS.md`. **Recorded as absent from this
   environment**, so the repo's own bar applies instead: `test-driven-development`,
   `spec-driven-development`, `planning-and-task-breakdown`, `performance-optimization`,
   `security-and-hardening`. TDD here means the AGENTS.md / `docs/agents/verifying.md` rule — a
   test that fails without the change, run by the agent — which T1–T4 each carry.

## CodeRabbit intake note (read once; the `<!-- <rawResChunk><planningResult> -->` echo was skipped)

**Adopted**

- **Design Choice 1** — move the interval math into the pure module and import it back into
  `kpi.py`. It satisfies the ticket's AC line "Existing CI helper reused, not duplicated" and the
  module's own declare-don't-import-over-a-cycle rule (`RESCUE_METHODS` is declared, not imported,
  for exactly this reason).
- **Design Choices 2, 3, 5** — `MARKOUT_HORIZONS` as the default with a `horizons` parameter; raw
  signed displacement with **no** peer subtraction (the stored baselines are unsigned:
  `markout.py:139` computes `total += (reference - price) * direction * size` and the sampler
  passes `direction=1.0` for both sides, so signing them is a new contract); uncapped `n` with
  `None` for fewer than two samples or zero variance.
- **Design Choice 6, partly** — round each new figure like its nearest sibling (markout/displacement
  4 decimals, the percentile and SQN figures 6), and classify the SQN band **on the rounded value**
  so the 1.6 / 2.5 boundaries are deterministic.
- **Its seam list, spot-checked against the tree — and its `statistical_analytics.py` line
  numbers were STALE, so they are corrected here rather than carried:** `MARKOUT_HORIZONS` is at
  `:129-130` (it said `:55-56`), `_finite` at `:133` (it said `:59-66`), `markout` at `:1026-1058`
  (it said `:219-252`), `build` at `:1105` (it said `:299-314`). Its `kpi.py` seams are right:
  `:94` (`_CI_Z_BY_LEVEL`), `:103` (`_CI_T_BY_DF`), `:122` (`_ci_critical_value`), `:132`
  (`_mean_pnl_ci`), `:496` (its only call), `:21` (the existing `statistical_analytics` import it
  extends). Also confirmed: `order_registry.py:736-754` (`MarkoutRecord.side` = BUY/SELL, not
  UP/DOWN), `order_registry.py:260-282` (the `markouts` table, columns `mid_h0`..`mid_h3`),
  `statistical_analytics.py:379` (`fill_path_edge`), `:677` (`rescue_friction_summary`), `:779`
  (`_percentile`).
  **Lesson recorded for Stations III–V: do not trust a line number this plan does not re-derive.**
- **Its three `[UNVERIFIED]` items are now resolved, not carried:** df=9 row is
  `{90: 1.833, 95: 2.262}` (`kpi.py:108`) — so the N=10 t-multiplier in its expected values is
  real; `side` is the upper-cased `"BUY"`/`"SELL"` pair (`markout.py:272` comment; the demo
  builder writes `side="BUY"` at `scripts/build_demo_db.py:597`); markout rows do carry `side` and
  `token_id` (`order_registry.py:260-282`).

**Rejected**

- **Its Design Choice 7 — "Do not change `build()` or the dashboard."** This contradicts the
  ticket's own acceptance criterion: *"All four metrics are visible on the Reports & Analytics
  page with their band chips (MAE unbanded, as settled) — a calculation-only or CLI-only
  implementation fails this issue."* Its own Scope line repeats it. The dashboard half is in
  scope; the card is `#robustness-readout`, mounted after `#queue-realism-readout`, on the same
  `page-reports` surface as the #513/#514/#515 readouts (`prototype.js:73` defines that page;
  the reference mounts are `app.js:4517` and `app.js:4626`).
- **Its bps output for `signed_markout` and its `intervals` return shape.** The ticket's field
  vocabulary is `markout_cents` and its band table is in cents, while `payload.markout` already
  ships size-weighted **bps** and the chart reads `intervals[].displacement_bps`
  (`app.js:3830`). A second bps number in an `intervals` key under the name "markout" is precisely
  the one-word-two-numbers failure #515's plan fought over percentiles.
- **Its per-horizon loop refactor as a standalone deliverable.** The shared loop is kept (T2), but
  it is a means, not a task: the deliverable is the banded median figure, with `markout()`'s output
  pinned unchanged.
- **Its MAE case list as the whole MAE story.** The pure function and its cases are kept verbatim
  in spirit; the plan adds the **population** the ticket's dashboard AC needs, per the operator's
  2026-10-14 decision (see "Operator decisions resolved below").
- **Its "no `build()` key" for the payload** — the section must reach the page; `build()` gains
  exactly one additive key and keeps every existing key and value.
- **Its Task 1 case spelling `levels == []` / `mean_usd == 1.0` for one sample** — kept as written;
  verified against `kpi.py:132-140` (n < 2 returns the mean and no levels).
- **Its Task 4 verification text**, which hands the operator `python -c` and pytest steps. This
  repo forbids that: the operator's verification block is hands-on (see the block at the end), and
  pytest is agent-run background only.

**[UNVERIFIED] remaining:** none. Every seam cited in this plan was read in the tree this session.

## Step 0A — open questions resolved from code (planning input, not questions for the operator)

- **CI decision** — the ticket says the helper lives in `statistical_analytics.py`; it actually
  lives in `kpi.py:94-174`, and `kpi` imports `statistical_analytics`, so a reverse module-level
  import is a cycle and a function-level import drags in config plus a session file. **Resolved:
  move it** (one source of truth, dependency direction preserved).
- **Horizons** — the ticket's idea mentions 1s/5s/30s; the schema holds `mid_h0`–`mid_h3` and
  config pins `markout_horizons = (300.0, 3600.0, 21600.0, 900.0)` seconds. **Resolved: reuse the
  configured four**, sub-minute out of scope.
- **Peer baselines** — stored unsigned. **Resolved: raw signed displacement**, no subtraction.
- **Unified verdict** — the ticket's Background mentions one, its Scope does not list one, and no
  combination rule exists. **Resolved: not built**; each gate keeps its own band and the card
  shows them side by side with a single shared `n`.
- **Chart vs backend multiplier mismatch** (`app.js:3091-3130` normal vs the Student-t table) —
  out of scope on this ticket; not reconciled, not mentioned on the card.
- **CI vs SQN disagreement** on a zero-variance positive sample (CI `good`, SQN `unmeasured`) —
  intentional: a band nobody could judge reads `unmeasured`, never `bad`.
- **MAE population** — the ticket's input shape is a caller-supplied series and DB/tape ingestion
  is out of scope, but the dashboard AC needs numbers. The only recorded post-fill prices in
  `orders.db` are the `markouts` rows (`mid_h0`..`mid_h3`, `order_registry.py:260-282`).
  **Operator decision 2026-10-14: marks bounded by the position's close** — a mark counts only when
  `markouts.ts + MARKOUT_HORIZON_SECONDS[N] <= close.ts` for that condition, so MAE measures the
  holding window the ticket defines. A position still open, or one that closed before its first
  mark, is counted and disclosed rather than folded in as a zero.

## Step 5 — one improvement proposal (evidence-first, adopt-by-default: hardening)

Evidence, verbatim from the ticket: *"Markout, **median** post-fill mid-price displacement across
fills, **in cents** — how the tape moved after we were filled"* and *"**Field vocabulary:** new
fields named `markout_cents` (per horizon)"*. Against the live module: `markout()`'s own docstring
is *"Post-fill price displacement per horizon, **size-weighted, in bps**"*
(`statistical_analytics.py:1027`), and the dashboard chart reads that figure as
`displacement_bps` (`app.js:3830`) and labels it `+xB bps`.

Two different numbers would therefore live on one screen under one word: a size-weighted **mean in
bps, unsigned, unbanded**, and a **median in cents, signed by side, banded**. That is the same trap
#515's plan called out ("one word, two numbers, in one repo, on the value that carries the colour")
— and here one of the two carries a chip.

**Proposal:** the new figure never wears the bare `markout` name. It ships as
`statistical_analytics.robustness.markout.intervals[].markout_cents` (median · cents · signed ·
banded) while the existing chart keeps `statistical_analytics.markout.intervals[].displacement_bps`
(mean · bps · unsigned · unbanded), and the card's subtitle plus the module docstring say which is
which ("median · signed by side" against "the chart above: mean · bps"). Classification:
**hardening / simplification — adopted by default**, folded into T2's section and T4's copy.

Rejection path recorded here so it does not resurface: if the Owner ever wants the *chart* in
cents, that is a change to a shipped chart's contract and belongs in its own issue, not this one.

## Frozen interface contract

All functions are pure: values in, values out. No clock, no socket, no SQLite, no signer, no
`random`. No input is mutated. No value is ever `NaN`/`Infinity`; every float is rounded and the
result is `json.dumps(..., allow_nan=False)`-safe. Missing stays missing — never `0`, never clamped.

```python
# core_brain/statistical_analytics.py   (moved in from kpi.py, names public here)

CI_Z_BY_LEVEL = ((90, 1.645), (95, 1.96))          # was kpi._CI_Z_BY_LEVEL
CI_T_BY_DF: dict[int, dict[int, float]] = {...}    # was kpi._CI_T_BY_DF, df 1..30
def ci_critical_value(level: int, n: int, z: float) -> float     # was kpi._ci_critical_value
def mean_pnl_ci(pnls: Sequence[float]) -> dict      # was kpi._mean_pnl_ci, behaviour identical
#   -> {"mean_usd", "n", "levels": [{"level","lower","upper","includes_negative",
#      "negative_depth_usd"}], "verdict"}   (unchanged shape; n < 2 -> levels == [], verdict None)

# --- Robustness gates (#516) -------------------------------------------------

MARKOUT_GOOD_AT_OR_ABOVE_CENTS = 0.0     # ticket: good [0, inf)
MARKOUT_AVERAGE_ABOVE_CENTS = -1.0       # ticket: average (-1.0, 0), bad (-inf, -1.0]
SQN_NOISE_BELOW = 1.6                    # ticket-fixed
SQN_STABLE_EDGE_ABOVE = 2.5              # ticket-fixed

# mid_hN -> SECONDS after the fill, mirroring config.markout_horizons (300, 3600, 21600, 900)
# and the columns markout.py writes. Declared, not imported: core_brain.markout loads config at
# import and pulls in the order registry, and this module's promise is no import side effects.
# Pinned against the live config in tests/test_markout_robustness_analytics.py.
MARKOUT_HORIZON_SECONDS = {"mid_h0": 300.0, "mid_h1": 3600.0,
                           "mid_h2": 21600.0, "mid_h3": 900.0}

# Band words ship as data with the band, so the renderer holds no numeral and no verdict:
MARKOUT_BAND_WORDS = {"good": "mid held or moved in our favour after the fill",
                      "average": "mid drifted against us by less than a tick",
                      "bad": "the tape moved a full cent or more against us after the fill",
                      "unmeasured": "no matured post-fill mark to measure"}
SQN_BAND_WORDS = {"good": "stable edge -- signal dominates noise",
                  "average": "promising but unproven",
                  "bad": "noise -- the results could be luck",
                  "unmeasured": "fewer than two closes, or no spread to measure"}
CI_BAND_WORDS = {"good": "zero is outside the band -- the edge is statistically proven",
                 "average": "the mean is positive but zero is still inside the band",
                 "bad": "zero is outside the band on the wrong side -- the edge is negative",
                 "unmeasured": "fewer than two closes -- no band exists"}

@dataclass(frozen=True)
class HeldMark:
    """One entry fill and the recorded post-fill marks inside its holding window."""
    side: str                              # "BUY" / "SELL", the order book's side
    entry_price: Optional[float] = None    # markouts.fill_price, dollars
    marks: tuple[float, ...] = ()          # in-window matured mid_hN values, dollars

def markout_cents_band(median_cents: Optional[float]) -> str    # good/average/bad/unmeasured
def signed_markout(rows: Sequence[Any],
                   horizons: Sequence[tuple[str, str]] = MARKOUT_HORIZONS) -> dict
#   rows: markout dicts/records (fill_price, size, side, mid_h*) -> {"intervals": [...]}
#   BUY +1, SELL -1 (matching peer_markout); unknown side skipped; a row with size <= 0 is
#   skipped (mirroring markout()); size carries NO weight, because the reported figure is a
#   median across fills, not a size-weighted mean; non-finite/None marks skipped.
#   Per horizon: the MEDIAN of the per-fill signed displacement in cents, linearly interpolated
#   (so p50 IS statistics.median). Unmatured horizons omitted.
#   -> [{"horizon", "samples", "markout_cents", "band", "meaning"}]
def max_adverse_excursion(entry_price: Optional[float], side: Optional[str],
                          prices: Sequence[float]) -> dict
#   BUY: mae = entry - min(prices); SELL: mae = max(prices) - entry; clamped at 0.
#   Non-finite prices dropped. -> {"mae_cents", "worst_price", "samples"}
#   None/None/0 when the series has no usable price, the entry is not > 0, or the side is not
#   BUY/SELL. Reads no tape, no registry, no clock.
def excursion_summary(closes: Sequence[Any], markouts: Sequence[Any]) -> dict
#   Builds the HeldMark population from the rows it is handed, then aggregates. Every markout row
#   lands in exactly one bucket, and the buckets partition the input:
#     measured  (a BUY fill with >= 1 in-window mark) | exit_fill (side != BUY) |
#     open (no close at/after the row for that condition) | no_marks_in_window (window closed,
#     no mark inside it) | not_recorded (fill_price/bucket unusable)
#   Window: mark mid_hN counts when row.ts + MARKOUT_HORIZON_SECONDS[N] <= close.ts, the close
#   being the EARLIEST one for that condition with ts >= row.ts.
#   -> {"count", "measured", "open", "exit_fill", "no_marks_in_window", "not_recorded",
#       "median_cents", "p90_cents", "units": "cents_per_share"}
#   Descriptive only: NO band key at all (a chip on a guess is a confident lie).
def sqn_band(sqn: Optional[float]) -> str        # good/average/bad/unmeasured
def system_quality_number(pnls: Sequence[float]) -> dict
#   _finite-filtered sample; sqn = sqrt(n) * mean / statistics.stdev(sample), rounded to 6;
#   band classified on the ROUNDED value (< 1.6 noise, > 2.5 stable edge, [1.6, 2.5] marginal).
#   n < 2 or stdev == 0 -> sqn None, band "unmeasured".
#   -> {"sqn", "band", "meaning", "n", "mean_usd", "stdev_usd"}
def pnl_ci_band(lower_cents: Optional[float], upper_cents: Optional[float]) -> str
#   good: lower > 0 | bad: upper < 0 | average: contains 0 | unmeasured: either is None.
def robustness(closes: Sequence[Any], markouts: Sequence[Any]) -> dict
#   ALWAYS present, like fill_path_edge / rescue_friction / queue_realism, and for the same
#   reason: a caller must never branch on whether the section exists. Every band is computed
#   from the rounded figure it ships with.
#   -> {"units": "cents_per_share",
#       "markout": {"intervals": [...]},
#       "mae": {...},                                    # excursion_summary(...)
#       "mean_pnl_ci": {"level": 95, "mean_cents", "lower_cents", "upper_cents", "n",
#                       "band", "meaning"},
#       "sqn": {"sqn", "band", "meaning", "n", "mean_usd", "stdev_usd"}}
```

The CI half reads the level whose `"level" == 95` explicitly (not `levels[-1]`), and a test pins
that the helper's last level IS 95 — so a future reorder of `CI_Z_BY_LEVEL` fails loudly instead of
silently relabelling the 90% band as 95%. With one close or none, the band is `unmeasured` and every
cent figure is `None`.

`build(closes, fills, markouts, starting_capital, quotes=None)` gains **one** line:
`payload["robustness"] = robustness(closes, markouts)`. Its signature does not change, and no
existing key or value moves. `kpi.py` needs no call-site change (it already passes `closes` and
`markouts`).

## Dependency graph

```
T1 (CI helper moved)  ──┬─→ T4 (payload section + card + demo seeding)
                        │
T2 (signed markout)  ───┤
                        │
T3 (MAE + SQN)  ────────┘
```
`Depends on:` — T2: T1 (shares the module file and `_finite`/`_percentile`; sequential edits avoid
conflicts). T3: T1 (same file, and T3's SQN reads the same PnL sample the CI half documents).
T4: T1, T2, T3 (it ships what they compute). T2 and T3 are functionally independent; they are
serialised because they write the same file.

## Tasks

### T1 — Share the confidence interval helper, in the pure module · size S · `[Backend/Logic]`
- Files: `core_brain/statistical_analytics.py`, `core_brain/kpi.py`,
  `tests/test_markout_robustness_analytics.py` (NEW).
- Build: move `_CI_Z_BY_LEVEL`, `_CI_T_BY_DF`, `_ci_critical_value`, `_mean_pnl_ci` into the pure
  module under their public names (`CI_Z_BY_LEVEL`, `CI_T_BY_DF`, `ci_critical_value`,
  `mean_pnl_ci`), behaviour byte-for-byte identical. In `kpi.py`, extend the existing
  `statistical_analytics` import line with `mean_pnl_ci as _mean_pnl_ci` and delete the four
  definitions; the call at `kpi.py:496` stays as it is.
- Verification (automated): the new file's T1 cases —
  N=10 `range(1, 11)` → `mean_usd == 5.5`, `lower ≈ 3.334300`, `upper ≈ 7.665700` (abs 1e-5, the
  df=9 t=2.262 row), `verdict == "positive"`; N=10 adverse `[1, -1] * 5` → `lower ≈ -0.754`,
  `verdict == "spans_zero"`, `negative_depth_usd ≈ 0.754`; N=100 `[1.0, -0.5] * 50` →
  `mean_usd == 0.25`, `lower ≈ 0.102259`, `upper ≈ 0.397741` (abs 1e-6, z=1.96);
  low variance `[0.99, 1.01] * 5` → `lower ≈ 0.99246`, `upper ≈ 1.00754`; zero variance
  `[2.0] * 5` → `lower == upper == 2.0`; one sample → `levels == []`, `verdict is None`,
  `mean_usd == 1.0`; zero samples → `{"mean_usd": None, "n": 0, "levels": [], "verdict": None}`;
  identity `kpi._mean_pnl_ci is statistical_analytics.mean_pnl_ci`; and a subprocess check that
  `import core_brain.statistical_analytics` leaves `core_brain.kpi` and `core_brain.order_registry`
  out of `sys.modules`. Regression: `tests/test_mean_pnl_ci.py` and `tests/test_trade_analytics.py`,
  untouched.
- RED expectation: `ImportError: cannot import name 'mean_pnl_ci' from 'core_brain.statistical_analytics'`.

### T2 — Signed post-fill markout, in cents, with the ticket's band · size M · `[Backend/Logic]`
- Files: `core_brain/statistical_analytics.py`, `tests/test_markout_robustness_analytics.py`.
- Build: share `markout()`'s per-horizon loop with a private helper that takes a per-row direction
  (`markout()` passes `+1` for every row and its output does not change); add
  `markout_cents_band()`, `MARKOUT_BAND_WORDS` and `signed_markout()`. Median of per-fill signed
  cents, `band` + `meaning` per interval, unmatured horizons omitted.
- Verification (automated): each row `fill_price 0.50`, `size 10.0` unless stated —
  BUY `mid_h0 0.51` → `markout_cents == 2.0`, `band == "good"`; BUY `0.50` → `0.0` / `good`;
  BUY `0.49` → `-2.0` / `bad`; SELL `0.51` → `-2.0`, SELL `0.49` → `2.0`; BUY at `0.51` and `0.49`
  (different sizes) → median `0.0`, `samples == 2`, and the mean would have been a different
  number (the pin that this is a median); unknown side `"HOLD"` only → `intervals == []`;
  `mid_h0` set with `mid_h3 None` → only the 5m interval; `horizons=(("mid_h1", "1h"),)` → only 1h;
  band edges `0.0` → `good`, `-0.4` → `average`, `-1.0` → `bad`, `None` → `unmeasured` (never
  `bad`); unchanged sibling — `markout([<SELL row, mid_h0 0.51>])["intervals"][0]["displacement_bps"]
  == 200.0`.
- RED expectation: `ImportError: cannot import name 'markout_cents_band'` (or `signed_markout`).

### T3 — Maximum adverse excursion over the holding window, and SQN · size M · `[Backend/Logic]`
- Files: `core_brain/statistical_analytics.py`, `tests/test_markout_robustness_analytics.py`.
- Build: `HeldMark`, `MARKOUT_HORIZON_SECONDS`, `max_adverse_excursion()`, `excursion_summary()`,
  `SQN_NOISE_BELOW`, `SQN_STABLE_EDGE_ABOVE`, `sqn_band()`, `SQN_BAND_WORDS`,
  `system_quality_number()`.
- Verification (automated): MAE with entry `0.50` — BUY dip `[0.52, 0.47, 0.49, 0.53]` →
  `mae_cents ≈ 3.0`, `worst_price == 0.47`, `samples == 4`; SELL rise `[0.48, 0.54, 0.51]` →
  `mae_cents ≈ 4.0`, `worst_price == 0.54`; BUY never adverse `[0.50, 0.51, 0.52]` → `0.0`;
  flat `[0.50, 0.50]` → `0.0`; non-finite filtered `[0.52, nan, 0.47]` → `samples == 2`, `3.0`;
  empty series / entry `0.0` / side `"HOLD"` → all three fields `None`. Window semantics —
  a mark at exactly `close.ts` counts (`<=`), a mark one second later does not; a row with no close
  lands in `open`; a SELL row lands in `exit_fill`; a closed row with no in-window mark lands in
  `no_marks_in_window`; the buckets sum to `len(markouts)`; `median_cents == statistics.median`
  of the per-row values (the interpolation pin). SQN — N=10 `range(1, 11)` → `sqn ≈ 5.744563`,
  `band == "good"`; `[3, -1] * 5` → `≈ 1.5` → `bad`; `[4, -1] * 5` → `≈ 1.8` → `average`;
  `[23, -7] * 5` → exactly `1.6` → `average`; `[11, -1] * 5` → exactly `2.5` → `average`;
  N=100 `[1.0, -0.5] * 50` → `≈ 3.316625` → `good`; `[1.0, -0.9] * 50` → `≈ 0.523681` → `bad`;
  low variance `[0.99, 1.01] * 5` → `≈ 300.0` → `good`; zero variance `[2.0] * 5` → `sqn is None`,
  `stdev_usd == 0.0`; one sample → `sqn is None`, `stdev_usd is None`; zero samples → `n == 0`.
  Plus: `MARKOUT_HORIZON_SECONDS` pinned against `core_brain.config.load().markout_horizons`
  (300/3600/21600/900) so a config move fails the pin instead of silently mis-windowing MAE.
- RED expectation: `ImportError: cannot import name 'max_adverse_excursion'`.

### T4 — Payload section, the readout card, and the operator's demo store · size L · `[Backend/Logic]` + `[Design/UI]`
- Files: `core_brain/statistical_analytics.py`, `dashboard/static/index.html`,
  `dashboard/static/app.js`, `scripts/build_demo_db.py`,
  `tests/js/analytics_surface_harness.cjs`, `tests/test_analytics_surface_mount.py`,
  `tests/test_statistical_analytics.py` (additions only, per #515's precedent),
  `tests/test_build_demo_db.py`, `tests/test_markout_robustness_analytics.py`.
- Build: `robustness(closes, markouts)` (always present, bands from the rounded figures, the 95%
  level selected by `"level" == 95`); `build()` gains the additive `robustness` key;
  `#robustness-readout` card after `#queue-realism-readout` with the title
  `POST-FILL ROBUSTNESS — MARKOUT, MAE, CI & SQN`; `renderRobustness(stats)` reading
  `stats.robustness` — 4 markout tiles (one chip each), MAE median + p90 tiles (**no chip at all**),
  a 95% CI tile (chip), an SQN tile (chip), a disclosed counts line, and the subtitle/copy that
  keeps the median-cents figure distinct from the chart's mean-bps figure; `module.exports` entry;
  harness stub id + direct call + printed HTML; demo seeding so the demo store measures MAE (an
  entry close on the markout-mix market timed to leave marks inside the window) and its pinning test.
- Verification (automated): the focused suites in `CONSTRAINTS.md`; section shape-frozen on an empty
  run (every figure `None`, every band `unmeasured`, no `NaN`/`Infinity` under
  `json.dumps(..., allow_nan=False)`); mount cases — the card renders from a full payload, the
  markout/CI/SQN chips come from the payload's band names, `html.count("analytics-chip") == 6`,
  MAE carries none, a payload with no `robustness` key renders no chips and no crash, and no
  `NaN`/`undefined`/`null` reaches the rendered text; the demo pin asserting the demo store's
  `excursion_summary()["measured"] >= 1` and `median_cents is not None`.
- RED expectation: the mount case fails without `renderRobustness` (`TypeError`/missing HTML), and
  the demo pin fails without the seeding (`measured == 0`).
- Verify before reporting (agent-run, no venue): build the demo store, start the dashboard against
  it, and read the card over HTTP; the operator gets the shorter path in the block below.

## Checkpoints

- **CHKPT A (after T1):** the helper lives in one place, `kpi.py` is down four definitions, and
  `tests/test_mean_pnl_ci.py` + `tests/test_trade_analytics.py` pass unchanged. One-line progress
  report under `auto` mode.
- **CHKPT B (after T3):** all four pure functions green in the ticket's focused file, with the
  RED output recorded for each; `tests/test_statistical_analytics.py` still green (nothing in it
  was edited yet).
- **CHKPT C (after T4):** the payload carries `robustness`, the card renders on the demo store
  with six chips and an unbanded MAE, and every focused suite in `CONSTRAINTS.md` is green.

## What fails without the change (RED evidence to capture)

1. T1: `ImportError: cannot import name 'mean_pnl_ci' from 'core_brain.statistical_analytics'`.
2. T2: `ImportError: cannot import name 'markout_cents_band'`.
3. T3: `ImportError: cannot import name 'max_adverse_excursion'`.
4. T4: the mount harness reports the missing card HTML (`robustness_html` empty / chips `0`), and
   the demo pin reports `measured == 0` before the seeding lands.

## How to verify (operator — hands-on, no venue, no money at risk)

Nothing in this change places, cancels or merges an order; no step reaches the venue.

1. `python -m scripts.build_demo_db --out data/demo_516.db --force` — the scenario list gains the
   post-fill robustness rows and the run ends with `demo registry written:`.
2. `python -m dashboard.server --db data/demo_516.db`, then open `http://127.0.0.1:8799` and go to
   **Reports & Analytics**.
3. Read the new card `POST-FILL ROBUSTNESS — MARKOUT, MAE, CI & SQN`, directly under
   `QUEUE REALISM — TRADE-THROUGH DEPTH & FILL LATENCY`. Expected: four markout tiles carrying a
   cent figure and a coloured chip each, `MAE median` and `MAE p90` tiles carrying a cent figure and
   **no chip**, a `95% CI` tile in cents with its chip, and an `SQN` tile with its chip and a short
   word from the payload (`stable edge`, `promising but unproven`, `noise`).
4. Open the same card against the live registry (`python -m dashboard.server`, then
   `http://127.0.0.1:8799` → Reports & Analytics). Expected: the card is present with counts that
   explain each figure, SQN and CI read `unmeasured` until at least two closes exist, and MAE reads
   `unmeasured` with a count where no mark fell inside a holding window.
5. A failure looks like one of: the card missing entirely, a tile printing `0.00` where the store
   recorded nothing (only `unmeasured` is honest), a chip on either MAE tile, or any tile reading
   `NaN`/`undefined`.
