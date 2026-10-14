# Todo — #524 Rescue closes record an explicit fee

- [ ] T1: Shadow rescue closes save `fee == 0.0` (full + partial, both methods)
- [ ] T2: Live rescue close keeps `fee is None` (policy pin, test only)
- [ ] T3: Saved close reads `fee_measured_count == 1`, `mean_tcdr == 0.0` on friction card
- [ ] Focused suites green: `test_single_buy_saver`, `test_rescue_friction_analytics`
- [ ] Extra checks green: `test_rc_fixes`, `test_shadow_exec`
