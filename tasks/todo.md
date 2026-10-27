# Todo — Issue #839: Live migration approval audit (#799 champion arm)

Branch: `i839/live-migration-approval-audit` | Plan: `tasks/plan.md` | Spec: `SPEC.md`

## T1 — Measurement + verdict CLI `[Backend/Logic]` (M)
- [x] Bring `scripts/reevaluate_tournament_run.py` onto this branch and add `--verdict`
- [x] Extend `tests/test_reevaluate_tournament_run.py` to pin the verdict wiring
- [x] Confirm every store opens read-only; missing plan → exit 1
- [x] `python -m pytest -q tests/test_reevaluate_tournament_run.py` (7 passed)
- [x] Print the ten-arm table for the real run folder (checkpoint 1)

## T2 — Audit document `[Docs]` (M)
- [ ] `docs/audits/live-migration-799.md`: verdict up front, commands behind every table
- [ ] Ten-arm measured table + issue-claim vs measured discrepancy table
- [ ] Risk-shape ruling with the half-split and concentration evidence
- [ ] Queue mechanism evidence from `python -m scripts.queue_report --db <arm>.db`
- [ ] Two-layer payload documentation, both labelled unapproved
- [ ] Reopen conditions; "How to verify" written for the operator
- [ ] Re-run each table's command and match it against the doc (checkpoint 2)

## T3 — Backlog cross-reference `[Docs]` (XS)
- [ ] `docs/backlog/i837-cockpit-catalyst-columns.md` row N4 → points at the audit
- [ ] `docs/backlog/i838-cockpit-hybrid-blueprint.md` row N3 → points at the audit
- [ ] Note that the 6.4 h snapshot values are superseded

## Close-out
- [ ] `python -m pytest -q tests/test_reevaluate_tournament_run.py tests/test_tournament_cockpit_arms.py tests/test_tournament_cockpit_funnel.py`
- [ ] Confirm no cockpit, arms-JSON, config, or risk-formula change in the diff
- [ ] Confirm no opening command was run and no store was written
