# Todo - #541 Consolidate every project statistic onto one page with one producer per number

- [ ] T1: Goldens freeze today's values pre-switch; ledger test RED (no prod change)
- [ ] T2: Shared math + Student's-t switch in `core_brain`; deltas logged (no DOM change)
- [ ] T3: One statistics page on the registry (charts, unit audit, card restyle); ledger GREEN
- [ ] T4: Findings doc (t-deltas + disagreements) + report-only registry; coverage test green
- [ ] Focused suites green: `test_statistical_analytics`, `test_account_kpi`,
  `test_analytics_api`, `test_analytics_surface_mount`, `test_statistics_report`
  (full suite deferred to CI)
