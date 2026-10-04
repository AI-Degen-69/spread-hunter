# Todo Checklist — Issue #355

- [x] **Task 1: Fresh-Rank Forensics & Standalone Audit Tooling**
  - [x] Write `scripts/audit_matchup_refusals.py`
  - [x] Execute audit over `market_universe.json` and fetch raw Gamma metadata
  - [x] Generate `docs/issues/355-matchup-refusal-audit.md` with full classification table
- [x] **Task 2: Forensics Evaluation & Rule Proposal Gate**
  - [x] Classify shapes and formulate exact predicate (or "no change" verdict)
  - [x] Present results and proposed rule for operator approval
- [x] **Task 3: Implement Selector Matchup Relaxation & Targeted Tests**
  - [x] Update `scoring/selector.py::identity_allowed` matchup branch
  - [x] Add unit tests in `tests/test_unified_universe.py` for admitted and refused shapes
  - [x] Replay offline to verify zero fragment leaks
  - [x] Record approved exception in `CONSTRAINTS.md`
  - [x] Verify targeted test suite passes (`tests/test_unified_universe.py`, `tests/test_family_admission.py`)
