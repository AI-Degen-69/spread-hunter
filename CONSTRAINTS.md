# Constraints & Quality Guardrails — Issue #355

Branch: `i355/matchup-refusal-may-hide-main-line-markets-audit` | Issue: #355

## Quality & Tests
- **Zero regressions**: `tests/test_unified_universe.py` and `tests/test_family_admission.py` must pass.
- **Every changed behaviour needs a test that fails without the change**:
  - Each newly admitted matchup shape must have a dedicated test case that fails against unchanged `scoring/selector.py`.
  - Each still-refused shape (O/U totals, series/finals, fragment labels) must be asserted with exact refusal strings.
- **Anti-cheat**: No skipped tests, no deleted assertions, no suppressions or linter silencing.
- **No new external dependencies**: Standard library + existing dependencies (`requests`/`httpx`, `pytest`).

## Behaviour Boundaries
- **Matchup branch only**: Touch only `scoring/selector.py::identity_allowed` matchup branch (lines 120–143).
- **Preserve rejection string**: The refusal string for non-admitted shapes must remain exactly `"not a primary Moneyline/Outright or Macro/Politics market"` (matched literally by `scoring/family_admission.py` and `scripts/filter_markets.py::_cause`).
- **Preserve gate precedence**: Do NOT touch `_BLOCKED_RE`, `_PRIMARY_RE`, `_MACRO_RE`, non-matchup fallback, or `require_primary=False`.
- **Zero fragment leaks**: Zero admission of spread/handicap, game/map/round, over/under totals, or bare numeric price bands.
- **Read-only audit tooling**: `scripts/audit_matchup_refusals.py` makes only read-only public GET requests and writes solely to the specified output directory. Never touch `data/orders.db` or start trading bots.
- **Operator approval gate**: Any relaxation of `identity_allowed` requires evidence from the forensic audit and explicit classification of all audited rows before code change.

## Approved Exception
- **Date**: 2026-10-04 (Issue #355)
- **Admitted shape**: Sports head-to-head matchup (`_SPORTS_SERIES_RE` matched in series/category/title/slug) grouped under a bare non-fragment group label (e.g. team name or country name) with non-fragment title/slug.
- **Still-refused shapes**: Matchups with line/total group labels (`Spread -3.5`, `O/U 46.5`), matchups with fragment titles (`O/U 46.5`, `Total 48.5`), series/finals tokens, and matchups without league series words.
- **Live-selection impact**: Audited on 148-row checkout snapshot and 151-row fresh rank: exactly 0 change in admission counts (all 11 refused rows are O/U totals and remain 100% refused); 0 fragment leaks. Unblocks legitimate sports matchups carrying team/country group labels when they appear in the universe.

