# CONSTRAINTS — Issue #675: Guard screener trial bars against nan/inf/negatives in engine config

Branch: i675/guard-screener-trial-bars | Issue: #675

## Non-Negotiable Quality Guardrails

1. **Zero Regressions**:
   - `python -m pytest -q tests/test_config_single_source.py` must pass cleanly.
   - Core config divergence checks (`python -m pytest -q tests/test_config_divergence_guard.py`) must pass cleanly.
   - The full test suite on CI must remain green.

2. **Explicit Error Variable Naming**:
   - If `FILTER_COMMON_VOL_MIN_TOP3_DEPTH_USD_TRIAL` or `FILTER_STD_VOL_MIN_24H_USD_TRIAL` receives a non-numeric (`abc`), non-finite (`nan`, `inf`), or negative (`-1`) value, `MakerConfig.from_env()` must raise a `ValueError` whose message explicitly names the active variable (canonical name or active legacy alias name).

3. **Preserve Deliberate Asymmetry**:
   - Screener (`scoring/config.py:load()`) fails open and keeps default `None` (already tested by `test_invalid_trial_override_keeps_shipped_default`).
   - Engine (`core_brain/config.py:from_env()`) fails loud by design. Do NOT silence engine config parsing errors.

4. **No Side Effects**:
   - Unset or empty trial environment variables must continue to yield `None` (shipped defaults unchanged).
   - Valid positive finite floats must continue to populate `kw["select_min_top3_depth_usd_trial"]` and `kw["select_min_volume_24h_usd_trial"]`.

5. **Anti-Cheat**:
   - No `@pytest.mark.skip`, no deleted tests or assertions, no suppression of errors.
