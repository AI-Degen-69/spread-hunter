## Summary

Determine empirically whether pairing the `balanced` quoting preset (`BOT_QUOTE_TOURNAMENT_PRESET=balanced`) with the 5-minute queue-clear gate (`BOT_ORDER_GATE_QUEUE_CLEAR_ON=1`, `BOT_ORDER_QUEUE_MAX_CLEAR_MIN=5`) maintains the risk-adjusted edge observed when each knob ran individually in Tournament #799.

## Changes

- ➕ `configs/tournaments/i843-pair-test_arms.json`: Defined 4-arm tournament configuration (`control`, `balanced-only`, `queue5-only`, `balanced-queue5`).
- ➕ `tests/test_tournament_arms_i843.py`: Unit test suite verifying schema, arm counts, exact environment keys, absence of safety/retired keys, and plan resolution.
- ➕ `docs/runs/2026-10-26-843-balanced-queue5-pair.md`: Pre-registered decision rule document establishing hypotheses, statistical power calculation requirements, mechanism checks, and evaluation criteria prior to rehearsal execution.
- ➕ `SPEC.md` and `CONSTRAINTS.md`: Specification and scope fences for Issue #843.

## How to verify

1. Inspect the tournament arms definition:
   ```powershell
   Get-Content configs/tournaments/i843-pair-test_arms.json | ConvertFrom-Json | Select-Object name, label, env
   ```
2. Verify shadow tournament dry-run plan generation:
   ```powershell
   python -m scripts.shadow_tournament --issue 843 --arms-file configs/tournaments/i843-pair-test_arms.json --dry-run
   ```
3. Inspect the pre-registered decision rule and statistical sample power requirements in `docs/runs/2026-10-26-843-balanced-queue5-pair.md`.
