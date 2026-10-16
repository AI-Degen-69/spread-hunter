# NOTICED-BUT-NOT-TOUCHING — #632 planning (scope discipline log)

Owner: planning agent (Station II, 2026-10-16) | When: during seam
verification on synced `main`. None of these are modified; each becomes
a future issue candidate. No strategy code was changed in this station.

1. `tests/test_risk.py` missing while issue acceptance cites it.
   Owner: test-infra. When: `Test-Path` 2026-10-16. Build resolves.
2. `scoring/config.py` lacks `couple_risk_frac` (core-only field).
   Owner: scoring-maintainer. When: grep 2026-10-16. Confirm intentional.
3. `HUNTER_MAX_SPREAD` parsed in TWO blocks (HUNTER_MARKET + general path).
   Owner: config-maintainer. When: read lines 1316/1329. Consolidation idea.
4. `order_manager.py:3331` reads via `getattr(cfg,"couple_risk_frac",0.01)`
   stringly-typed. Owner: execution-team. When: grep 2026-10-16.
5. `docs/explanations/prudent_profile_and_strategy_guide.html` currency
   unknown (CodeRabbit-flagged). Owner: docs. When: planning 2026-10-16.
