# SPEC.md — Issue #843: Rehearse the balanced preset and the 5-minute queue-clear gate together as one arm

Branch: `i843/rehearse-balanced-preset-queue-gate` | Issue: #843

## Goal

Determine empirically whether combining the `balanced` quoting preset (`BOT_QUOTE_TOURNAMENT_PRESET=balanced`) with the 5-minute queue-clear gate (`BOT_ORDER_GATE_QUEUE_CLEAR_ON=1`, `BOT_ORDER_QUEUE_MAX_CLEAR_MIN=5`) maintains the risk-adjusted benefits observed when each knob ran individually in Tournament #799. 

The Tournament Cockpit HYBRID blueprint synthesised both knobs into its champion specification, but no tournament arm has ever tested both knobs active simultaneously. This issue defines the 4-arm tournament configuration, locks the pre-registered decision rule and statistical sample power requirements before running, and verifies execution via shadow rehearsal dry-run and targeted tests.

## 4-Arm Tournament Specification

The arms configuration file `configs/tournaments/i843-pair-test_arms.json` defines four arms:

1. **`control`**: The shipped loose baseline with no tournament preset and no queue-clear gate (`env: {}`).
2. **`balanced-only`**: Isolates the balanced preset alone (`env: {"BOT_QUOTE_TOURNAMENT_PRESET": "balanced"}`).
3. **`queue5-only`**: Isolates the 5-minute queue-clear gate alone (`env: {"BOT_ORDER_GATE_QUEUE_CLEAR_ON": "1", "BOT_ORDER_QUEUE_MAX_CLEAR_MIN": "5"}`).
4. **`balanced-queue5`**: Combines both knobs (`env: {"BOT_QUOTE_TOURNAMENT_PRESET": "balanced", "BOT_ORDER_GATE_QUEUE_CLEAR_ON": "1", "BOT_ORDER_QUEUE_MAX_CLEAR_MIN": "5"}`).

### Scope Fences
- **Zero modification to `core_brain/config.py`**: Shipped defaults and `TOURNAMENT_PRESETS` must remain untouched.
- **No safety-layer overrides in arms**: Arms must not set stop loss drop (`BOT_RISK_STOP_ADVERSE_DROP_CENTS`), requote deadband (`BOT_ORDER_REQUOTE_DEAD_BAND_CENTS`), or rescue lead (`BOT_RISK_AGED_OUT_RESCUE_LEAD_SEC`).
- **Shadow execution only**: Uses `core_brain.shadow_run` / `scripts.shadow_tournament`. No signer is loaded; spends zero real capital.
- **No changes to in-flight Tournament #799**: Neither the running #799 tournament nor its arms file `configs/tournaments/i799-tournament-test_arms.json` may be edited.

## Decision Rule & Statistical Gate

Pre-registered in `docs/runs/2026-10-26-843-balanced-queue5-pair.md` before execution:
1. **Directional Expectation**:
   - `balanced-queue5` is hypothesized to match or exceed `queue5-only` on Sharpe proxy ($\ge 2.70$), profit factor ($\ge 2.50$), and drawdown mitigation ($< \$4.00$) while maintaining higher trade velocity and fill efficiency via the balanced 2¢–4¢ band.
   - If the gate refuses the same books the balanced preset targets, the arms will demonstrate mechanistic overlap or volume starvation.
2. **Power & Sample Size Requirements**:
   - Using `python scripts/stat_gate.py --dry-calc --sigma <s>`:
     - At $\sigma = 0.37$ (`queue-clear-5m` baseline), resolving a \$0.02/trade difference requires 2,117 closed trades.
     - At $\sigma = 0.87$ (`balanced-mid` baseline), resolving a \$0.02/trade difference requires 11,701 closed trades.
   - Any 24-hour rehearsal (~150–250 trades per arm) is statistically underpowered to declare a nominal profit winner; conclusions must focus on risk shape, queue clearance distribution, and merge rates.
3. **Mechanism Verification**:
   - Run `python scripts/queue_report.py` across arm databases to assess the proportion of books sitting near the 5-minute clear boundary.

## Acceptance Criteria

- [ ] `configs/tournaments/i843-pair-test_arms.json` exists with all four arms, valid JSON, and exact env keys.
- [ ] Dedicated unit test `tests/test_tournament_arms_i843.py` passes, asserting arms count, names, env contents, and schema validity.
- [ ] Pre-registered decision rule document `docs/runs/2026-10-26-843-balanced-queue5-pair.md` is committed with status `RULE LOCKED, RESULTS PENDING`.
- [ ] Dry-run command `python -m scripts.shadow_tournament --issue 843 --arms-file configs/tournaments/i843-pair-test_arms.json --dry-run` exits with code 0 and prints valid plan JSON.
- [ ] Regression test suite passes: `python -m pytest -q tests/test_tournament_arms_i843.py tests/test_shadow_tournament.py tests/test_tournament_cockpit_arms.py tests/test_tournament_run_number_naming.py`.
