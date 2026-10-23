# #761 todo — Station II (plan) → handoff to III

- [x] Read `docs/explanations/entry-gate-job.html` (§3/§4/§8/§9) + issue #761 body
- [x] Reproduce evidence numbers read-only from `data/01_shadow_21-10_11-39.db`
      (`mode=ro`): closes by method/reason, 5,464 refusals, schema for join
- [x] Confirm vehicle state: #474 CLOSED, PR #757 MERGED (re-check on build day)
- [x] Write `tasks/plan.md` (read-only contract, buckets, default-off gate,
      inclusive A/B, dust absence test) — header
      `Branch: i761/fill-quality-entry-gate | Issue: #761`
- [x] Write `CONSTRAINTS.md` (Wilson/n<30, inclusive-PnL, off-by-default,
      no stop-loss/sizing/tournament, no #759/#760)
- [ ] Station III (builder): fixture DB → report skeleton (RED→GREEN) →
      fill→book→lifecycle join → Wilson buckets → break-even table + gate →
      inclusive A/B → dust absence test RED on `origin/main` then fix
- [ ] Station IV: single focused CodeRabbit round, one push, targeted checks
- [ ] Station VI: closeout with hands-on verify (shadow rehearsal + report CLI
      + dashboard A/B readout), never pytest counts as proof
