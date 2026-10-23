# CONSTRAINTS.md — #761 fill-quality entry gate

1. **Read-only evidence.** `data/01_shadow_21-10_11-39.db` and any input DB open
   only via `file:<abs>?mode=ro` + `uri=True`. No `INSERT/UPDATE/DELETE/CREATE/
   ALTER/DROP` in the report module; no `OrderRegistry` on an input path; no
   imports from `core_brain` inside the report. Failure → `UNREADABLE`, never
   repair.
2. **Honest intervals.** Every death/merge rate carries n and a Wilson 95%
   interval. Buckets with n < 30 print `INSUFFICIENT / not-a-finding` and can
   never clear a gate. A percentage without n fails review.
3. **Break-even bar.** No threshold ships without its row: legs rejected /
   merges lost (n+$) / dead legs avoided (n+$) / net inclusive-PnL delta. Ships
   only where lift clears **8.9:1** with net inclusive delta > 0.
4. **Inclusive PnL only.** A/B and gate tables score
   merge + single_buy_exit + settlement including hard-stop and aged-out-rescue
   with gas/fees. Merge-only scoring is rejected outright.
5. **Default-off gate.** `bot_entry_fill_quality_gate_on = False`; live changes
   only when the operator flips it. Pure evaluate function, replayable offline.
6. **Do not touch:** stop-loss, sizing, tournament params, `dashboard/server.py`
   refactor, #471 ladder, #759/#760 (other session).
7. **Dust gate.** The absence test (`dust_position_does_not_loop`, 0.1087-share
   fixture, ≤1 refusal, second call no-ops) must be recorded RED on
   `origin/main` before the fix lands.
