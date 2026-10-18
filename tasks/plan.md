# Plan — Issue #675: The engine accepts nan/inf screener trial bars and raises an unnamed ValueError

Branch: i675/guard-screener-trial-bars | Issue: #675
Size Tier: Small | Task Types: Code, Debug

## Code Intake & Context (Station II Step 0A)
- **Problem:** `core_brain/config.py:655-661` parses the two screener trial bars (`FILTER_COMMON_VOL_MIN_TOP3_DEPTH_USD_TRIAL` / `HUNTER_DEPTH_TRIAL_USD` and `FILTER_STD_VOL_MIN_24H_USD_TRIAL` / `HUNTER_VOLUME_TRIAL_USD`) with bare `float(trial)`. This accepts `nan` (which poisons every subsequent numeric comparison, rendering gates silently permissive), `inf`, and negative values. When an unparseable string is provided (e.g. `abc`), it raises an unnamed `ValueError: could not convert string to float: 'abc'`.
- **Deliberate Design:** In `scoring/config.py:197-249`, the screener silently keeps `None` on invalid trials (fails open). In `core_brain/config.py`, the engine fails loud on invalid configuration by design. We maintain this asymmetry: the engine raises a `ValueError` naming the offending variable.

## Improvement Proposal (Station II Step 5)
- **Evidence:** `core_brain/config.py:963-972` defines `_bounded_float(name: str, raw: str, lo: float, hi: float) -> float`. It already validates float conversion, rejects `nan`/`inf` via `math.isfinite()`, checks bounds `lo <= value <= hi`, and formats error messages with the exact variable name (e.g., `f"{name}={raw!r} is not a number"`, `f"{name}={raw!r} is not finite"`, `f"{name}={raw!r} is outside {lo:g}..{hi:g}"`).
- **Proposal (Simplification / edge-case hardening — adopted):** Route both trial reads in `MakerConfig.from_env()` through `_bounded_float(var_name, raw, 0.0, float("inf"))` (or a dedicated upper limit / helper using `math.isfinite() and value >= 0.0`), reusing the existing `_bounded_float` function and capturing the resolved variable name from `_env_get`. This achieves exact consistency with other `MakerConfig` knobs and prevents duplicating exception handling logic.

---

## Tasks

### [x] T1 — Guard screener trial bars in `core_brain/config.py:from_env()` with named validation
- **Size:** S | **Domain:** `[Backend/Logic]` | **Skill:** `debugging-and-error-recovery`
- **Depends on:** none
- **Target files:** `core_brain/config.py`
- **What is built:**
  1. Capture `trial_var` and `vtri_var` from `_env_get`:
     ```python
     trial, trial_name = _env_get("FILTER_COMMON_VOL_MIN_TOP3_DEPTH_USD_TRIAL", "HUNTER_DEPTH_TRIAL_USD")
     if trial and trial.strip():
         # Screener defaults to None (fails open); engine raises and fails loud with variable name.
         kw["select_min_top3_depth_usd_trial"] = _bounded_float(trial_name or "FILTER_COMMON_VOL_MIN_TOP3_DEPTH_USD_TRIAL", trial.strip(), 0.0, float("inf"))

     vtri, vtri_name = _env_get("FILTER_STD_VOL_MIN_24H_USD_TRIAL", "HUNTER_VOLUME_TRIAL_USD")
     if vtri and vtri.strip():
         kw["select_min_volume_24h_usd_trial"] = _bounded_float(vtri_name or "FILTER_STD_VOL_MIN_24H_USD_TRIAL", vtri.strip(), 0.0, float("inf"))
     ```
  2. Document the deliberate asymmetry in code comments as required by acceptance criteria.
- **Verification:** Unit test verification and CLI checks.

### [x] T2 — Add parametrized tests for trial bar rejection and verification in `tests/test_config_single_source.py`
- **Size:** S | **Domain:** `[Backend/Logic]` | **Skill:** `test-driven-development`
- **Depends on:** T1
- **Target files:** `tests/test_config_single_source.py`
- **What is built:**
  1. Add parametrized test `test_engine_invalid_trial_override_raises_named_error` testing both canonical and legacy variable names against `["abc", "nan", "inf", "-1"]`, verifying that `MakerConfig.from_env()` raises `ValueError` containing the variable name.
  2. Verify valid positive floats still populate `select_min_top3_depth_usd_trial` and `select_min_volume_24h_usd_trial`.
  3. Verify unset/empty keeps `None`.
- **Verification:** `python -m pytest -q tests/test_config_single_source.py tests/test_config_divergence_guard.py`
