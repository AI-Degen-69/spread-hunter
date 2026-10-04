# Task Plan — Issue #355: Matchup refusal may hide main-line markets

Branch: `i355/matchup-refusal-may-hide-main-line-markets-audit` | Issue: #355

## CodeRabbit Intake
- **Adopted**: 3-phase progression (isolated forensics & row classification -> evidence-based matchup rule proposal -> offline replay & targeted tests); read-only audit script pattern saving raw Gamma JSON payloads; preserving exact refusal string `"not a primary Moneyline/Outright or Macro/Politics market"`.
- **Rejected**: Complex multi-script architecture; unnecessary extra CLI flags or redundant intermediate wrappers.
- **Unverified**: Historical claim of ~32 NCAAF rejections from 2026-10-03 (the current checkout snapshot has 11 rows in this bucket, mostly O/U totals; fresh scan will capture live ground truth).

## Open Questions Resolution (`needs-answers`)
1. **How many of the rejected rows are genuine main lines?**
   - *Resolution*: Investigated live snapshot in `runtime/market_universe.json` (11 rows refused under `"not a primary..."`). All 11 in that snapshot are over/under total submarkets (e.g. `Colts vs. Commanders: O/U 46.5`), which trip `is_matchup` because of `vs.` in title, but are NOT main lines. A fresh rank audit will collect live candidates to discover any genuine sports moneyline matchups (e.g. CFB/NCAAF) and classify each row.
2. **What vocabulary distinguishes a real main-line matchup from a series/finals submarket when metadata is absent?**
   - *Resolution*: True main lines have two team/competitor outcomes for a single game without submarket tokens (O/U, total, spread, handicap, game/map/round, series, best of, season). `_FRAGMENT_LABEL_RE` catches totals and bands in group labels, but if the fragment is in the title, it must also be checked or refused. If venue metadata has `groupItemTitle` that is not a fragment, #351 pattern allows admitting it if sports keywords match.

## Improvement Proposal (Step 5)
- **Evidence**: In `scoring/selector.py:120-143`, `Colts vs. Commanders: O/U 46.5` lands in `not a primary Moneyline/Outright or Macro/Politics market` only because `_BLOCKED_RE` lacks `O/U` and `_FRAGMENT_LABEL_RE` is only applied to `market_group`.
- **Proposal (Adopted - Edge-case hardening)**: Ensure that in the matchup branch, title/slug is explicitly checked against `_FRAGMENT_LABEL_RE` or positive submarket tokens before any relaxation can admit it. A matchup with `O/U` in title must stay strictly refused even if group label restrictions are loosened.

---

## Task Decomposition

### [x] Task 1: Fresh-Rank Forensics & Standalone Audit Tooling [Debug / Code] (Size: S)
- Target files:
  - `scripts/audit_matchup_refusals.py`
  - `docs/issues/355-matchup-refusal-audit.md`
- Work:
  - Create `scripts/audit_matchup_refusals.py` to ingest a `market_universe.json` file, extract all rows with `reject_reason == "not a primary Moneyline/Outright or Macro/Politics market"`, fetch raw Gamma market and event sibling payloads via public GET requests, and save raw evidence to an output folder.
  - Re-run discovery normalization from `scripts/filter_markets.py` to generate an audit table with: `cid`, title, slug, groupItemTitle, market_type, series_title, event_title, outcomes, classification (`main line`, `series/finals submarket`, `season/futures`, `other submarket`, `unsupported`), and shape tag.
  - Run fresh isolated rank (`python -m scripts.filter_markets --dry-run --out-dir data/tmp_forensics_355 --top 40`) or use current snapshot, then audit the rows and document findings in `docs/issues/355-matchup-refusal-audit.md`.
- Helper skills: `debugging-and-error-recovery`, `spec-driven-development`
- Depends on: None
- Verification: Execute `python -m scripts.audit_matchup_refusals --universe runtime/market_universe.json --out-dir runtime/audit_355` and verify output JSON and markdown table.

### Checkpoint 1
Forensic evidence captured and documented in `docs/issues/355-matchup-refusal-audit.md`. Every refused row is classified.

### [x] Task 2: Forensics Evaluation & Rule Proposal Gate [Research / Logic] (Size: XS)
- Target files:
  - `docs/issues/355-matchup-refusal-audit.md`
- Work:
  - Evaluate the shapes observed in Task 1.
  - If consistent main-line shapes exist (e.g. sports matchup with bare team group label, or no league word but clean game event): define exact predicate.
  - If no shape cleanly separates main lines from submarkets: document "no change" rationale.
  - Present proposed rule and impact to operator before modifying selector.
- Helper skills: `doubt-driven-development`, `api-and-interface-design`
- Depends on: Task 1
- Verification: Operator review & acceptance of classification and predicate.

### [x] Task 3: Implement Selector Matchup Relaxation & Targeted Tests [Backend / Logic] (Size: M)
- Target files:
  - `scoring/selector.py`
  - `tests/test_unified_universe.py`
  - `CONSTRAINTS.md`
- Work:
  - Implement approved predicate in `scoring/selector.py::identity_allowed` matchup branch (lines 120–143).
  - Update comments and docstrings reflecting audit evidence and admitted/refused shapes.
  - In `tests/test_unified_universe.py`, add parametrized tests for newly admitted shapes and still-refused shapes (O/U totals, series/best-of, fragment labels).
  - Add end-to-end `evaluate` test confirming admitted matchup passes downstream.
  - Run offline replay through `scripts/audit_matchup_refusals.py` to confirm zero fragment leaks.
  - Add approved exception entry to `CONSTRAINTS.md`.
- Helper skills: `test-driven-development`, `constraint-driven-development`
- Depends on: Task 2
- Verification: `python -m pytest -q tests/test_unified_universe.py tests/test_family_admission.py`.
