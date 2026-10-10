# Todo - Issue 457 (Branch: i457/single-state-driven-start-stop-button-with-graceful-stack)

- [ ] T1 [M] [Backend/Logic] Graceful, verified, honest STOP — helper + registry rewrite
- [ ] T2 [M] [Backend/Logic] Partial START fills only missing services — fix NameError + scoped rollback
- [x] T3 [M] [Frontend/UI] One state-driven toggle with visible feedback — button, handler, harness

Checkpoints: after T1 (STOP honest) / after T2 (no NameError) / after T3 (toggle + harness green).
All done. Next: /iv-review-build-and-pr.
