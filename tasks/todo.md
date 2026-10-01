# Todo — Issue #49 + #324

- [x] T1 (#49) — Design-note addendum: separate ladder allocation (XS, Docs)
- [x] T2 (#49) — Read-only replay probe answering rungs/exit/lifetime (M, Research)
- [x] CHECKPOINT 1 (#49) — probe green; BTC+ETH 5-min tapes collected, both verdicts go
- [x] T0 (#324) — Probe-gate check: BTC + ETH verdicts go; issue claimed; branch `i324/d11-followup-shadow-rehearsal-on-btceth-5-min`
- [x] T1 (#324) — Ladder-mimic rehearsal harness, parameterized by series (M, Backend/Logic)
- [x] T2 (#324) — Fill-attribution proof: one pair_id, oldest-first, conservation (S, Backend/Logic)
- [x] T3 (#324) — One-leg exit + zero-live proof + per-series reports (S, Backend/Logic)
- [x] CHECKPOINT 2 (#324) — BTC 70/70 and ETH 60/60 shares accounted, zero live execution
- [ ] #325 — Ladder path build (gated on #324 green) — see handoff findings above
- [x] T1 (#326) — Repro spike: refusal loop on synthetic store, grace-0 + grace>0 (S, Research)
- [x] T2 (#326) — Decision matrix + recorded verdict with evidence (S, Research)
- [x] CHECKPOINT 1 (#326) — netting-wins opens T3, lifecycle-wins closes on the note
- [x] T3 (#326) — Net prior exit closes in exit sizing, gated (S, Backend/Logic)
- [x] CHECKPOINT 2 (#326) — handoff to #325
- [x] T1 (#325) — Series discovery for BTC/ETH 5+15-min (S, Backend/Logic)
- [x] T2 (#325) — Ladder config: mode + shapes + timers + budget (S, Backend/Logic)
- [x] T3 (#325) — Gated ladder decision function + off-test (M, Backend/Logic)
- [x] CHECKPOINT 1 (#325) — off-identity proven, single-pair path frozen
- [x] T4 (#325) — Per-rung telemetry + ladder_exit + timed exit (M, Backend/Logic)
- [x] T5 (#325) — Shadow wiring + four proof tests (M, Backend/Logic)
- [x] CHECKPOINT 2 (#325) — shadow rehearsal per spec

# Todo — Issue #323 (verify-and-close)

- [x] T1 (#323) — Reconcile posted verdict vs #324 gate (XS, Research)
- [x] T2 (#323) — No-drift check: scripts unchanged + focused suites green (XS, Research)
- [ ] T3 (#323) — Closeout comment, close #323, prune stale branch (XS, Docs)
