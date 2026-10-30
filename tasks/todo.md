# Build checklist — Issue #866

Branch: `i866/fix-strategy-parameters-panel-shows-global-config`

## T1 — Arm catalog + isolated child evaluation `[Backend/Logic]` M · done
- [ ] `dashboard/arm_params.py`: catalog via `parse_tournament_run_dir_name`, resolve order plan → tourney → newest, exact then round-suffix arm match, 5 distinct errors
- [ ] `__main__` child: launcher-style env build, `declare_rehearsal()`, both loaders separately, strict JSON, no trade, no `.env`
- [ ] Parent runner: timeout + rebuild both MakerConfigs + `_NoScoringConfig` path + thread-safe memo (configs only)
- [ ] `tests/test_arm_params.py` green (catalog, matches, round-trip incl. balanced → 4, memo)

## T2 — Endpoint wiring + three Queue Gate cards `[Backend/Logic]` M · done
- [ ] `get_parameters()`: `?arm=&plan=&tourney=`, no-arm path untouched, `arm_context` / `arm_error`
- [ ] Three queue cards incl. flow-window `1800s` + ignored-override note + shipped-defaults basis
- [ ] Endpoint `?arm=` tests green (`4¢` vs `20¢`, error path, no-arm identical)

## T3 — Arm picker dropdown + labels `[Design/UI]` S · done
- [ ] `#params-panel` header select + identity line + global-default label
- [ ] `app.js` param fetch + stale-response guard + context/error render
- [ ] Browser gate (balanced arm `4¢`, cleared `20¢`, bogus arm error)

## T4 — Gates and handoff `[Testing]` S
- [ ] Targeted suites in one run
- [ ] Grep gate: no `os.environ` swap in `dashboard/`, no loader/launcher touched
- [ ] Operator "How to verify" block (hands-on only) + PR body
