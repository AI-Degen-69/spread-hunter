# Todo — #411 (branch i411/apply-asymmetric-requote-thresholds-and-enforce-the-pair-cost-ceiling)

- [ ] T1 — Hard ceiling helper + config defaults (RED-first)
- [ ] C1 — Review: helper keeps the cap below the hard 99c ceiling and config only tightens it
- [ ] T2 — Asymmetric BUY re-gate in `plan_orders` (RED-first)
- [ ] C2 — Review: falling BUY target is held inside the threshold, rising target still cancels only when the policy says so
- [ ] T3 — Quote generation uses the same cap in from-mid, ladder, and legacy paths
- [ ] T4 — Regression sweep: targeted suites green, no cancel reason drift
