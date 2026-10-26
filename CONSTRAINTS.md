# CONSTRAINTS.md — Issue #850: Add Decision Gates & Rehearsal Status tab to Tournament Cockpit (:8800)

## Code Quality & Architecture
- Presentation-layer only in `scripts/tournament_cockpit_server.py`.
- No modifications to `core_brain/config.py` or `core_brain/order_manager.py`.
- Zero live capital or signer involvement.
- Must preserve existing tabs (`#tab-overview`, `#tab-hybrid-config`, `#tab-pnl-chart`, `#tab-risk-stats`, `#tab-execution`, `#tab-monte-carlo`, `#tab-recommendations`).

## Data Truthfulness & Zero Fabrication
- If an arm has no database or 0 trades, display honest `NO_DATA` / `PENDING` states. Never fabricate pseudo-results or invent data.
- If sample size $N < 2,117$, flag nominal PnL as underpowered and evaluate strictly on risk shape.

## Verification
- Targeted unit tests in `tests/test_tournament_cockpit_decision_gates.py` verifying HTML generation, gate computations, and endpoint responses.
- Clean run of regression tests `tests/test_tournament_cockpit_arms.py` and `tests/test_tournament_cockpit_decision_gates.py`.
- Live browser/DevTools verification of the page at `http://127.0.0.1:8800`.
