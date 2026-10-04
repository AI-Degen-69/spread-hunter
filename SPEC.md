# SPEC: Issue #355 — Matchup refusal audit & selective identity relaxation

## Goal
Audit the ranker's `not a primary Moneyline/Outright or Macro/Politics market` refusal bucket across a fresh market universe. Extract raw Gamma metadata for each refused matchup candidate, classify them into main lines vs submarkets based on venue evidence, and (subject to operator approval) implement an evidence-backed relaxation in `scoring/selector.py::identity_allowed` that admits proven main lines while keeping fragments and submarkets strictly refused.

## Acceptance Criteria
- [ ] Implement standalone, read-only audit tool `scripts/audit_matchup_refusals.py` to extract refused matchup rows from a universe snapshot, fetch raw Gamma market and event details, normalize using existing discovery logic, and produce machine-readable forensic summaries.
- [ ] Generate comprehensive forensic documentation in `docs/issues/355-matchup-refusal-audit.md` classifying all refused rows into main lines, submarkets (e.g. O/U totals, series/finals), or unsupported with decisive venue evidence and shape tags.
- [ ] Determine the exact evidence-backed predicate (e.g. allowing non-fragment group labels for sports matchups, or specific field patterns) and present the classification and projected impact to the operator before editing selector logic.
- [ ] Narrow only the matchup branch of `scoring/selector.py::identity_allowed` (lines 120–143) to admit verified main lines while keeping all submarkets refused with the exact string `"not a primary Moneyline/Outright or Macro/Politics market"`.
- [ ] Add unit and regression tests in `tests/test_unified_universe.py` covering newly admitted shapes, still-refused shapes (O/U titles, series/best-of, fragment labels), and an end-to-end `evaluate` check.
- [ ] Offline replay over saved audit evidence proving 0 fragment leaks (spread/handicap, game/map/round, over/under totals, numeric price bands).
- [ ] All targeted test suites pass: `python -m pytest -q tests/test_unified_universe.py tests/test_family_admission.py`.

## Scope
### In scope
- `scripts/audit_matchup_refusals.py`: Read-only public API audit script.
- `docs/issues/355-matchup-refusal-audit.md`: Forensics run documentation and per-row classification table.
- `scoring/selector.py`: Matchup branch in `identity_allowed` (lines 120–143), related docstring/comments.
- `tests/test_unified_universe.py`: Unit tests for admitted and refused matchup shapes and evaluate flow.
- `CONSTRAINTS.md`: Approved exception record with live-selection impact.

### Out of scope
- `_BLOCKED_RE`, `_PRIMARY_RE`, `_MACRO_RE`, `_FRAGMENT_LABEL_RE`, `_is_fragment_label`.
- Non-matchup branches and `require_primary=False` logic.
- Downstream volume, depth, spread, movement, horizon gates.
- Paired admission trial machinery (`scoring/family_admission.py`).
- Any live execution, order placement, or `data/orders.db` interaction.
