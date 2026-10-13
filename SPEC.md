# SPEC — #516: Post-fill markout and statistical robustness analytics

Branch: `i516/add-post-fill-markout-and-statistical-robustness` | Tier: Standard | Type: Code + UX/Copy (readout)

Supersedes the #514 `rescue friction` spec in this file's history (kept in git), which had
superseded #513's. Part 4 of the 4-part execution-telemetry family (`#513` → `#514` → `#515` → this).

## Goals

1. The operator can tell **toxic-flow absorption from spread capture**: signed post-fill
   mid-price displacement per configured horizon, as a **median across fills, in cents**, with
   the ticket's traffic-light band.
2. The operator can see **how far a position ran against us while we held it** — maximum
   adverse excursion (MAE) in cents, as a **median and p90**, descriptive only, no band.
3. The operator can tell **a real edge from small-sample luck**: a 95% confidence interval on
   mean close PnL, and a system quality number (SQN) with the ticket's noise/stable-edge bands.
4. All four are **pure and deterministic**: values in, values out. No clock, no socket, no
   SQLite handle, no signer, no `random`. Inputs are never mutated. Every float is rounded and
   `json.dumps(..., allow_nan=False)`-safe. Missing stays missing — never `0`.
5. **The four metrics are VISIBLE on the Reports & Analytics page** (`page-reports`) with their
   band chips (MAE unbanded). A calculation-only or CLI-only implementation does not satisfy
   this issue.
6. Bands are computed **server-side from the rounded payload** in the same function that shapes
   it, shipped as data; the renderer maps a band name to the existing `analytics-chip` modifier
   and holds no threshold. A payload without the section renders no chips (tested, not assumed).
7. The 95% CI helper is **reused, not duplicated**: one definition, in the pure module, with
   `kpi.py` importing it back under its existing private name so the KPI payload is unchanged.

## Acceptance criteria (from the ticket, restated as testable statements)

- [ ] `signed_markout()` reports, per configured horizon, the **median** of per-fill signed
      displacement in cents (BUY `+1`, SELL `-1`), with `band` + `meaning` and a sample count;
      unmatured horizons are omitted, unknown sides skipped.
- [ ] `max_adverse_excursion()` returns the worst adverse move of a caller-supplied price
      series against the entry, in cents and in price, clamped at zero, `None` when the series,
      the entry or the side is unusable.
- [ ] `system_quality_number()` returns `sqrt(n) × mean ÷ sample stdev` with the ticket's bands
      (`< 1.6` noise, `> 2.5` stable edge, `[1.6, 2.5]` promising but unproven), and `None` for
      fewer than two samples or zero variance.
- [ ] The 95% band on mean close PnL comes from the **one** helper moved into
      `core_brain/statistical_analytics.py`; `kpi._mean_pnl_ci is statistical_analytics.mean_pnl_ci`.
- [ ] Positive/flat/adverse trajectories, synthetic MAE series over a holding window, SQN/CI at
      N=10 and N=100, low-variance, zero-variance and zero-sample cases are covered by unit tests
      in `tests/test_markout_robustness_analytics.py` that **fail without the change**.
- [ ] The `build()` payload gains exactly one additive key, `robustness`, always present and
      shape-frozen; every existing key and value is unchanged.
- [ ] The `#robustness-readout` card renders the four metrics on the Reports & Analytics page,
      each banded tile carrying the chip its payload band names, and MAE carrying **no chip**.
- [ ] No network, signer or store side effect: importing the analytics module loads neither
      `core_brain.kpi` nor `core_brain.order_registry`; no fill or sampling logic is touched.
- [ ] Runnable: `python -m pytest -q tests/test_markout_robustness_analytics.py`

## Edge cases the tests pin

- Median vs mean: `signed_markout` medians (linearly interpolated, so p50 IS
  `statistics.median`), on an even sample and a single fill.
- Band boundaries: `0.0` is `good`, `-0.4` is `average`, exactly `-1.0` is `bad`, `None` is
  `unmeasured` — never `bad`.
- Exact SQN boundaries: `1.6` and `2.5` both read `average` (the closed band is inclusive).
- Non-finite (`NaN`) marks and PnLs are dropped, not sorted and not counted.
- A position that closed before its first horizon: no in-window mark → counted, MAE unmeasured.
- A mark landing exactly on the close timestamp: inside the window (`<=`).
- A run with one close: CI has no band (a zero would read "breaks even"); SQN is `None`.
- A run with zero closes: every figure `None`, every band `unmeasured`, no division by zero.

## Explicit out of scope

- Sub-minute horizons (1s/5s/30s). The schema holds `mid_h0`–`mid_h3` only and the tape is
  1-minute; sampling changes are out of scope on this ticket.
- Peer-baseline subtraction. The stored baselines are unsigned (`peer_markout(direction=1.0)`),
  so the new figures are raw signed displacement; signing the baselines is a new contract.
- A unified robustness verdict combining the gates. The ticket defines no combination rule, and
  the two gates may disagree on purpose (a zero-variance positive sample: CI `good`, SQN
  `unmeasured`).
- Reconciling the dashboard chart's normal multipliers with the backend's Student-t table
  (`dashboard/static/app.js:3091–3130`).
- Any change to fill simulation, markout sampling mechanics or the markout sampler's writers.
- Parts 1–3 (siblings `#513`, `#514`, `#515`) and the validation ladder (`#470`, `#471`).
