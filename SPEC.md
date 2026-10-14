# SPEC — #573: Unify one-sided stop-loss (shadow == live, one mode per run)

## Goal
A one-sided position (one leg filled, the other never did — a naked directional
bet) gets exactly ONE stop rule per run, in ONE unit, chosen by a mode selector.
Shadow and live apply it identically (only the fill source differs).

## The two modes (parallel, human whole-number units)
- `stop_loss_trigger_pct` = 10  → sell when the leg is down 10% from entry.
- `stop_loss_trigger_usd_cents` = 4 → sell when the leg is down 4c ($0.04) from entry.
- Example: buy at 0.50, willing to lose 5c → Stop-Loss Trigger ($) = 5.
- `single_buy_stop_mode` selects which is active ("usd" | "pct"); default "usd".

## Acceptance criteria
- A run activates exactly one stop mode (usd XOR pct) — never both.
- Shadow and live make the same stop decision for a given run (identical except fills).
- The dollar trigger is tunable from config/env (iterated in tests/tournaments).
- The percent trigger runs in isolation (its own run) and still works.
- `python -m pytest -q tests/test_dual_stop_loss.py tests/test_single_buy_saver.py` passes.

## Out of scope
- The user-facing mode-selector config surface (later follow-up; the logic lands here).
- Changing the dollar level itself (tournament tuning, #569).
- The grace period, aged-out rescue, and the non-ladder lifecycle fixed floor
  (`single_leg_lifecycle.transition` `hard_stop_bid = 0.15`) — separate rules,
  untouched [see the scope question in the plan].
- Any live trading.
