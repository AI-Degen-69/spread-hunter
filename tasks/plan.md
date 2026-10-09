# Plan: Issue #443 — Sample size sufficiency per confidence level on dashboard
Branch: i443/sample-size-sufficiency-per-confidence-level | Issue: #443

## Intake from CodeRabbit Plan
- **Adopted from CodeRabbit plan:**
  - Pure helper name `required_sample_size_for_mean(std_dev, target_margin, z)` with formula `ceil(((z * std_dev) / target_margin) ** 2)`.
  - Nested payload placement in `trade_analytics.sample_size_sufficiency` on `/api/kpi`.
  - Levels for 95%, 98%, 99% using two-tailed Z-scores (1.95996, 2.32635, 2.57583).
  - Null behavior on N < 2 or zero spread so "unmeasured" is rendered rather than false sufficiency.
  - Bump `KPI_PAYLOAD_VERSION` & `EXPECTED_PAYLOAD_VERSION` to 254.
  - Tier 1 card placement in `index.html` inside `#tier1-decision-row`.
- **Rejected from CodeRabbit plan:**
  - Splitting into unnecessary sub-tasks/phases; condensed into 3 atomic vertical tasks.
  - Adding new routes or modifying `config.py` (ruled out).
- **Verified from live codebase:**
  - `KPI_PAYLOAD_VERSION` in `core_brain/kpi.py` is currently 253.
  - `EXPECTED_PAYLOAD_VERSION` in `dashboard/static/app.js` is currently 253.
  - `test_the_payload_version_is_pinned_on_both_sides` in `tests/test_analytics_api.py` checks both versions and literal "253".
  - `tests/test_negative_values_read_as_losses.py` line 249 has literal `payload_version=253`.
  - `tests/test_analytics_impact_tiers.py` checks `#tier1-decision-row` and `TIER_BY_CARD`. Adding a card inside `#tier1-decision-row` with `data-tier="1"` needs an entry in `TIER_BY_CARD` if registered as a deck ID, or can be structured cleanly.

## Interface Contracts & Schemas

### 1. Python Helper & Payload
```python
# Constants
Z_95_SAMPLE_SUFFICIENCY = 1.95996
Z_98_SAMPLE_SUFFICIENCY = 2.32635
Z_99_SAMPLE_SUFFICIENCY = 2.57583
SAMPLE_SUFFICIENCY_LEVELS = (
    (95, Z_95_SAMPLE_SUFFICIENCY),
    (98, Z_98_SAMPLE_SUFFICIENCY),
    (99, Z_99_SAMPLE_SUFFICIENCY),
)

def required_sample_size_for_mean(
    std_dev: float | None,
    target_margin: float | None = 0.02,
    z: float = Z_95_SAMPLE_SUFFICIENCY,
) -> int:
    """Calculates minimum sample size N = ceil(((z * std_dev) / target_margin) ** 2).
    Returns 0 if std_dev or target_margin is non-positive or non-finite.
    """
```

Payload structure in `kpi["trade_analytics"]["sample_size_sufficiency"]`:
```json
{
  "current_n": 4,
  "std_dev_usd": 0.11547,
  "target_margin_usd": 0.02,
  "levels": [
    {
      "confidence_pct": 95,
      "z": 1.95996,
      "required_n": 129,
      "remaining_n": 125,
      "progress_pct": 3
    },
    {
      "confidence_pct": 98,
      "z": 2.32635,
      "required_n": 181,
      "remaining_n": 177,
      "progress_pct": 2
    },
    {
      "confidence_pct": 99,
      "z": 2.57583,
      "required_n": 222,
      "remaining_n": 218,
      "progress_pct": 2
    }
  ]
}
```
*(When current_n < 2 or std_dev_usd is 0, std_dev_usd is null or 0, and required_n, remaining_n, progress_pct are null).*

### 2. Dashboard UI Contract
- Card added in `dashboard/static/index.html` inside `#tier1-decision-row`:
  `<div class="card tier1-card" id="card-sample-sufficiency" data-tier="1">`
    Header: `.tier1-head` with title "SAMPLE SIZE & CONFIDENCE SUFFICIENCY", tag "TIER 1 · OBSERVATION DEPTH".
    Body: `<div id="sample-sufficiency-readout" class="sample-sufficiency-readout"></div>`
  `</div>`
- In `dashboard/static/app.js`:
  `function renderSampleSufficiency(ta)` mounted in `renderAnalyticsSurface(kpi, status)`.
  When N < 2: displays message "At least two closed trades are needed to estimate spread." and rows with "unmeasured".
  When std_dev is 0: displays "All closed trades have the same result, so the spread is zero." and "unmeasured".
- In `tests/js/analytics_surface_harness.cjs`:
  Register `sample-sufficiency-readout` and `card-sample-sufficiency` in stub ID list, invoke `app.renderSampleSufficiency(kpi.trade_analytics)`.

---

## Tasks

### Task 1: [Backend/Logic] Formula helper, KPI payload extension, and version bump [x]
- **Size:** M
- **Target files:** `core_brain/kpi.py`, `tests/test_kpi.py`, `tests/test_analytics_api.py`, `tests/test_negative_values_read_as_losses.py`
- **Depends on:** None
- **Helper skills:** `test-driven-development`
- **Description:**
  1. Add Z constants and `required_sample_size_for_mean(std_dev, target_margin=0.02, z=Z_95_SAMPLE_SUFFICIENCY) -> int` in `core_brain/kpi.py`.
  2. In `compute_trade_analytics()`, calculate `sample_size_sufficiency` using sample stdev of realized PnL (`wins + losses` / `_pnl_vals`). If `n < 2`, `std_dev_usd` is `None` and row stats are `None`. If `std_dev_usd == 0`, row stats are `None`.
  3. Bump `KPI_PAYLOAD_VERSION` to 254 in `core_brain/kpi.py`.
  4. Update version pins in `tests/test_analytics_api.py` and `tests/test_negative_values_read_as_losses.py`.
  5. Add unit tests in `tests/test_kpi.py` testing formula edge cases, negative/zero inputs, 95/98/99 levels, empty state, and payload structure.
- **Verification:** `python -m pytest -q tests/test_kpi.py tests/test_analytics_api.py tests/test_negative_values_read_as_losses.py`

### Task 2: [Design/UI] Frontend card, CSS styling, and client-side render logic [x]
- **Size:** M
- **Target files:** `dashboard/static/index.html`, `dashboard/static/app.js`, `dashboard/static/styles.css`, `tests/test_analytics_impact_tiers.py`
- **Depends on:** Task 1
- **Helper skills:** `frontend-ui-engineering`
- **Description:**
  1. In `dashboard/static/index.html`, add `.card.tier1-card#card-sample-sufficiency[data-tier="1"]` inside `#tier1-decision-row`.
  2. In `tests/test_analytics_impact_tiers.py`, add `"card-sample-sufficiency": "1"` to `TIER_BY_CARD`.
  3. In `dashboard/static/styles.css`, add styling for `.sample-sufficiency-readout` (table layout, typography, progress bar spacing).
  4. In `dashboard/static/app.js`:
     - Update comment contract and bump `EXPECTED_PAYLOAD_VERSION` to 254.
     - Implement `renderSampleSufficiency(ta)` handling empty/null states, formatting columns, rendering progress bars with `.dist-progress-wrap`, `.dist-progress-bar`, `.dist-progress-fill`.
     - Call `renderSampleSufficiency(ta)` safely inside `renderAnalyticsSurface`.
     - Export `renderSampleSufficiency` in `module.exports`.
- **Verification:** `python -m pytest -q tests/test_analytics_api.py tests/test_analytics_impact_tiers.py`

### Task 3: [Backend/Logic] JS Harness integration, mount test suite & end-to-end verification [x]
- **Size:** S
- **Target files:** `tests/js/analytics_surface_harness.cjs`, `tests/test_analytics_surface_mount.py`
- **Depends on:** Task 1, Task 2
- **Helper skills:** `test-driven-development`
- **Description:**
  1. In `tests/js/analytics_surface_harness.cjs`, add `sample-sufficiency-readout` and `card-sample-sufficiency` to element stubs, invoke `app.renderSampleSufficiency(kpi.trade_analytics || {})`, and return `sufficiency_html`.
  2. In `tests/test_analytics_surface_mount.py`, add tests:
     - `test_sample_sufficiency_renders_three_levels` (asserts 95%, 98%, 99%, required values, and progress fills).
     - `test_sample_sufficiency_n_zero_has_no_undefined` (empty state, "unmeasured", no NaN/undefined).
     - `test_sample_sufficiency_missing_object_mounts` (handles missing payload gracefully).
- **Verification:** `python -m pytest -q tests/test_analytics_surface_mount.py tests/test_analytics_api.py tests/test_kpi.py`
