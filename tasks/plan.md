# Plan: Issue #448 — Redesign sample size sufficiency using effect size and per-status observation bases
Branch: i448/redesign-sample-size-sufficiency | Issue: #448

## Intake & Context Analysis
- **Context & Diagnosis:**
  - Issue #443 introduced `required_sample_size_for_mean` using $E = \$0.02$ against PnL dollar standard deviation $\sigma$. When $\sigma \approx 1.77$, $N = \lceil ((1.96 \cdot 1.77)/0.02)^2 \rceil \approx 30,000$, which is unrealistic for live trading evaluation.
  - Issue #448 redesigns sample size calculation:
    1. Continuous metrics (Cohen's d = 0.20): $N = \lceil (Z / d)^2 \rceil \approx 97$ at 95% CL ($Z = 1.95996, d = 0.20$).
    2. Proportional metrics ($E_{pct} = 0.05$): $N = \lceil (Z^2 \cdot p(1-p)) / E_{pct}^2 \rceil \approx 385$ at 95% CL ($p = 0.50, E_{pct} = 0.05$).
    3. Separation of observation bases:
       - `closes` table for PnL Expectancy, Holding Duration (or exits), Stop Loss exits, and Merge exits.
       - `orders` table for Fill Rate (orders placed vs fills received).
- **Adopted Elements:**
  - Relative effect size helper `required_sample_size_cohen_d(d=0.20, z=Z_95_SAMPLE_SUFFICIENCY) -> int`.
  - Proportional sample size helper `required_sample_size_proportion(p=0.50, margin=0.05, z=Z_95_SAMPLE_SUFFICIENCY) -> int`.
  - Keep backward-compatible signature for `required_sample_size_for_mean` (or alias/deprecate cleanly) while updating sufficiency evaluation.
  - Structure `sample_size_sufficiency` payload into per-status categories: `pnl_expectancy`, `stop_loss_rate`, `merge_rate`, `fill_rate` (and overall summary if applicable).
  - Surface status segmentation on the Reports & Analytics tab in the dashboard.
  - Bump `KPI_PAYLOAD_VERSION` & `EXPECTED_PAYLOAD_VERSION` to 255.
- **Improvement Proposal (Ground in Evidence):**
  - *Evidence:* Issue description states:
    > "Separate observation counts across their correct domain tables:
    > - `closes` table for PnL Expectancy, Holding Duration, Stop Loss exits, and Merge exits.
    > - `orders` table for Fill Rate (Orders Placed vs Fills Received)."
  - *Classification:* Simplification / edge-case hardening (adopt-by-default).
  - *Implementation:* In `core_brain/kpi.py`, pass `total_orders_count = len(orders)` or read from orders into `compute_trade_analytics` (or enrich sufficiency in `report()`), so `fill_rate` sufficiency uses placed orders as `current_n`, while `pnl_expectancy`, `stop_loss_rate`, and `merge_rate` use `n_closes`. When `orders` is empty/not provided, `current_n` defaults cleanly to 0 without breaking standalone callers.

---

## Interface Contracts & Schemas

### 1. Mathematical Formulas & Helpers (`core_brain/kpi.py`)

```python
DEFAULT_COHEN_D = 0.20
DEFAULT_PROPORTION_MARGIN = 0.05

def required_sample_size_cohen_d(
    d: float | None = DEFAULT_COHEN_D,
    z: float = Z_95_SAMPLE_SUFFICIENCY,
) -> int:
    """Minimum sample size for continuous metric using Cohen's d effect size.
    Formula: N = ceil((z / d) ** 2)
    Returns 0 when d or z is non-positive or non-finite.
    """
    if d is None or z is None:
        return 0
    if not math.isfinite(d) or not math.isfinite(z) or d <= 0.0 or z <= 0.0:
        return 0
    return math.ceil((z / d) ** 2)


def required_sample_size_proportion(
    p: float | None = 0.50,
    margin: float | None = DEFAULT_PROPORTION_MARGIN,
    z: float = Z_95_SAMPLE_SUFFICIENCY,
) -> int:
    """Minimum sample size for binary status proportion.
    Formula: N = ceil((z**2 * p * (1 - p)) / (margin**2))
    Returns 0 when p, margin, or z is invalid.
    """
    if p is None or margin is None or z is None:
        return 0
    if not math.isfinite(p) or not math.isfinite(margin) or not math.isfinite(z):
        return 0
    if p <= 0.0 or p >= 1.0 or margin <= 0.0 or z <= 0.0:
        return 0
    return math.ceil((z ** 2 * p * (1.0 - p)) / (margin ** 2))
```

### 2. Payload Structure (`trade_analytics.sample_size_sufficiency` in `/api/kpi`)

```json
{
  "current_n": 4,
  "statuses": {
    "pnl_expectancy": {
      "label": "PnL Expectancy",
      "type": "continuous",
      "base": "closes",
      "current_n": 4,
      "effect_size_d": 0.2,
      "levels": [
        {"confidence_pct": 95, "z": 1.95996, "required_n": 97, "remaining_n": 93, "progress_pct": 4},
        {"confidence_pct": 98, "z": 2.32635, "required_n": 136, "remaining_n": 132, "progress_pct": 3},
        {"confidence_pct": 99, "z": 2.57583, "required_n": 166, "remaining_n": 162, "progress_pct": 2}
      ]
    },
    "stop_loss_rate": {
      "label": "Stop Loss Rate",
      "type": "proportion",
      "base": "closes",
      "current_n": 4,
      "target_margin": 0.05,
      "levels": [
        {"confidence_pct": 95, "z": 1.95996, "required_n": 385, "remaining_n": 381, "progress_pct": 1},
        {"confidence_pct": 98, "z": 2.32635, "required_n": 542, "remaining_n": 538, "progress_pct": 1},
        {"confidence_pct": 99, "z": 2.57583, "required_n": 664, "remaining_n": 660, "progress_pct": 1}
      ]
    },
    "merge_rate": {
      "label": "Merge Rate",
      "type": "proportion",
      "base": "closes",
      "current_n": 4,
      "target_margin": 0.05,
      "levels": [
        {"confidence_pct": 95, "z": 1.95996, "required_n": 385, "remaining_n": 381, "progress_pct": 1},
        {"confidence_pct": 98, "z": 2.32635, "required_n": 542, "remaining_n": 538, "progress_pct": 1},
        {"confidence_pct": 99, "z": 2.57583, "required_n": 664, "remaining_n": 660, "progress_pct": 1}
      ]
    },
    "fill_rate": {
      "label": "Fill Rate",
      "type": "proportion",
      "base": "orders",
      "current_n": 12,
      "target_margin": 0.05,
      "levels": [
        {"confidence_pct": 95, "z": 1.95996, "required_n": 385, "remaining_n": 373, "progress_pct": 3},
        {"confidence_pct": 98, "z": 2.32635, "required_n": 542, "remaining_n": 530, "progress_pct": 2},
        {"confidence_pct": 99, "z": 2.57583, "required_n": 664, "remaining_n": 652, "progress_pct": 2}
      ]
    }
  },
  "levels": [ ... ] // backward compatibility with 95/98/99 levels for pnl_expectancy
}
```

---

## Tasks

### Task 1: [Backend/Logic] Mathematical helpers, per-status sufficiency computation & KPI version bump [x]
- **Task ID:** task-1
- **Size:** M
- **Domain:** `[Backend/Logic]`
- **Target files:** `core_brain/kpi.py`, `tests/test_kpi.py`, `tests/test_statistical_analytics.py`, `tests/test_mean_pnl_ci.py`, `tests/test_analytics_api.py`, `tests/test_negative_values_read_as_losses.py`
- **Depends on:** None
- **Helper skills:** `test-driven-development`
- **Description:**
  1. Add `required_sample_size_cohen_d(d=0.20, z=Z_95_SAMPLE_SUFFICIENCY)` and `required_sample_size_proportion(p=0.50, margin=0.05, z=Z_95_SAMPLE_SUFFICIENCY)` in `core_brain/kpi.py`.
  2. In `compute_trade_analytics()`, support `orders_count: int = 0` (or derive from context). Compute per-status sufficiency for `pnl_expectancy` (continuous, Cohen's d=0.20, base closes), `stop_loss_rate` (proportional, margin=0.05, base closes), `merge_rate` (proportional, margin=0.05, base closes), and `fill_rate` (proportional, margin=0.05, base orders).
  3. Maintain backward compatibility in `levels` array mapping to `pnl_expectancy` levels.
  4. Pass `orders_count=len(orders)` from `report()` in `core_brain/kpi.py`.
  5. Bump `KPI_PAYLOAD_VERSION` to 255.
  6. Update tests in `tests/test_kpi.py`, `tests/test_analytics_api.py`, `tests/test_negative_values_read_as_losses.py`.
- **Verification:** `python -m pytest -q tests/test_kpi.py tests/test_statistical_analytics.py tests/test_mean_pnl_ci.py tests/test_analytics_api.py`

### Task 2: [Design/UI] Frontend status segmentation rendering & CSS styling [x]
- **Task ID:** task-2
- **Size:** M
- **Domain:** `[Design/UI]`
- **Target files:** `dashboard/static/app.js`, `dashboard/static/styles.css`
- **Depends on:** task-1
- **Helper skills:** `frontend-ui-engineering`
- **Description:**
  1. Bump `EXPECTED_PAYLOAD_VERSION` to 255 in `dashboard/static/app.js`.
  2. Update `renderSampleSufficiency(ta)` in `dashboard/static/app.js`:
     - Render segmented cards/sections for each status (`pnl_expectancy`, `stop_loss_rate`, `merge_rate`, `fill_rate`).
     - Display metric label, observation base (`closes` vs `orders`), current N, required N, remaining N, and progress bar.
     - Show clear note if observations are 0 or below minimum.
  3. Update `dashboard/static/styles.css` if necessary for segmented sufficiency table or multi-metric readout.
- **Verification:** `python -m pytest -q tests/test_analytics_api.py tests/test_analytics_impact_tiers.py`

### Task 3: [Backend/Logic] JS Harness and mount tests update [x]
- **Task ID:** task-3
- **Size:** S
- **Domain:** `[Backend/Logic]`
- **Target files:** `tests/js/analytics_surface_harness.cjs`, `tests/test_analytics_surface_mount.py`
- **Depends on:** task-1, task-2
- **Helper skills:** `test-driven-development`
- **Description:**
  1. Update `tests/js/analytics_surface_harness.cjs` and `tests/test_analytics_surface_mount.py` to assert the segmented status readout.
  2. Verify all 4 statuses (PnL Expectancy, Stop Loss Rate, Merge Rate, Fill Rate) render correctly with ~97 and ~385 targets and no `undefined` or `NaN`.
- **Verification:** `python -m pytest -q tests/test_analytics_surface_mount.py`
