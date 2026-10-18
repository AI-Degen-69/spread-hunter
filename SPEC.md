# SPEC — #672: Dashboard parameter cards still read legacy names and label legacy env vars

Branch: plan_issue_672 | Issue: #672

## Goal
Eliminate legacy configuration vocabulary and fallback literals from the dashboard `/api/parameters` endpoint in `dashboard/server.py`:
1. Replace all 19 `getattr(scfg, "<old_name>", <literal>)` reads with direct access to canonical fields on `scfg`.
2. Replace all 41 plain alias reads (`cfg.<old_name>`) with direct access to canonical dataclass fields (`cfg.<canonical_name>`) mapped authoritatively by `core_brain.config._LEGACY_TO_CANONICAL`.
3. Update environment variable badges (`env_var`): replace all deprecated `HUNTER_*` and `SPREAD_HUNTER_*` badges with canonical `FILTER_*` and `BOT_*` names documented in `.env.example`, keep `LIVE_SWEEP_INTERVAL` for `sweep_interval`, and set `env_var` to `""` where no direct loader-backed override exists.
4. Ensure zero `DeprecationWarning` is emitted by `dashboard/server.py` when calling `/api/parameters`.
5. Maintain strictly identical display strings, units, defaults, raw_values, and trigger descriptions for an unchanged environment.

## Acceptance Criteria
- [ ] No `getattr(scfg, ...)` or `getattr(cfg, ...)` remains in `dashboard/server.py:get_parameters`.
- [ ] No legacy attribute reads on `cfg` or `scfg` remain in `dashboard/server.py:get_parameters` (verified by zero `DeprecationWarning` attributed to `dashboard/server.py`).
- [ ] No `HUNTER_*` or `SPREAD_HUNTER_*` strings remain in any card's `env_var`.
- [ ] Every non-empty `env_var` is a canonical environment variable documented in `.env.example` at repository root.
- [ ] Cards without an active override have `env_var: ""` (uniform string type, sorting remains functional in UI).
- [ ] Displayed values, numbers, units, formatting, `default`, `trigger`, and `raw_value` types remain identical for an unchanged configuration.
- [ ] `tests/test_dashboard_server.py` includes focused automated tests for deprecation warning absence and full badge mapping contract.
- [ ] Targeted tests pass: `python -m pytest -q tests/test_dashboard_server.py tests/test_config_single_source.py`.

## Edge Cases & Nuances
1. `filter_common_spread_max_cents`: property on `MakerConfig` that forwards `select_max_book_spread`. It returns a price fraction (e.g. 0.0205) despite its `_cents` name; display math `* 100` and `:.4f` format must remain unchanged.
2. `LIVE_SWEEP_INTERVAL`: valid canonical environment variable documented in `.env.example` (line 20) despite not using `FILTER_*` or `BOT_*` prefix.
3. `emergency_hedge_pct`: card value reads `cfg.bot_risk_hedge_emergency_pct`, but no loader reads `HUNTER_EMERGENCY_HEDGE_FRAC` or any canonical variable; badge must be `""`.
4. `select_min_top3_depth_usd`: reads baseline depth floor (`filter_common_vol_min_top3_depth_usd`), whereas `FILTER_COMMON_VOL_MIN_TOP3_DEPTH_USD_TRIAL` controls a trial override field (`..._trial`); badge must be `""`.
5. Unset `env_var` normalization: normalize existing `None` values (e.g. lines 4459, 4476, 5321) to empty string `""` to maintain consistent `str` schema across all 59 cards.

## Out of Scope
- Modifying `dashboard/static/app.js` (already correctly handles `""` as empty badge and `—` in table).
- Modifying `core_brain/config.py` (e.g. `derive_dynamic_caps` alias reads) or `scoring/config.py`.
- Switching value sources from `cfg` to `scfg` for screener cards (tracked as future work).
- Modifying `.env.example` or adding new knobs.
