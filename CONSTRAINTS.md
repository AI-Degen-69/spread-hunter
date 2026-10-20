Branch: i709/dashboard-scoring-config-fallback | Issue: #709

# Constraints — #709

- Healthy path is byte-for-byte unchanged: 13 screener cards keep every value, scope, key, default, env_var; 46 engine cards untouched. Failure path adds alerting only — no parameter value, scope or key changes.
- No guard around the engine config load (`server.py:4246`); THE500 is out of scope → noticed row in docs/issues/709-noticed-but-not-touching.md.
- Failure is logged at WARNING (with exception type + text), not swallowed, DEBUG allowed only for the neighbouring bankroll handler.
- Marker text must survive `test_no_parameter_card_value_restates_the_default_in_another_unit` (no `(…¢)`/`(…m)`/`(…h)` suffix shape).
- No new dependencies. No `app.js` change. No dashboard/browser test changes beyond the two new pytest tests.
- No pytest/gh/git steps inside the operator's `## How to verify`; browser gate is playwright-cli only, ≤8 calls, reported honestly.
- Tests are never skipped, disabled or softened; no test deletions to get green.
