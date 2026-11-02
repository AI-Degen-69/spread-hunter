# Todo — Issue #919

- [ ] Task 1 [Backend/Logic] (S): config single-source calibration — defaults, both loaders, .env.example, config tests green
- [ ] Task 2 [Backend/Logic] (M): live admission — 900s window, 300s recency, dormant rejection, screener tests green
- [ ] Task 3 [Backend/Logic] (S): bidirectional arm split — LIVE_ONLY / EXCLUDE_LIVE in _market_specs, arm tests green
- [ ] Task 4 [Debug] (XS): 6¢ spread-stop boundary + recovery — tests first, conditional 1e-9 fix only
- [ ] CP1: shadow evidence — $13k live admitted, ghost-live + dormant non-live refused
- [ ] CP2: arm sets disjoint with union = mixed; spread boundary + recovery proven
- [ ] Final: replacement acceptance run (union of Task 1–4 regression files) green in CI
