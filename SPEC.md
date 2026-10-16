# SPEC - #619: Stop-loss architecture — dynamic modes plus entry-relative emergency floor

Branch: i619/add-stop-loss-architecture-dynamic-modes-plus-entr | Issue: #619

## Authority

`docs/ideas/stop_loss_architecture_logic_requirements.md` is the operator
specification. It is read-only and decides every conflict. The issue body
summarizes; the file decides. Do not edit it.

#618 (`[CONFIGURE] Retire the dead grace and stop-loss exit knobs`) is
CLOSED/superseded by #619 — recorded here so no station reuses it. Verified
2026-10-16 via `gh issue view 618 --json state`.

## Goal

Replace the fixed `$0.15` lifecycle hard stop in
`core_brain/single_leg_lifecycle.py:transition()` (fires when
`held_bid <= hard_stop_bid=0.15`, lines 231-329) with an entry-relative stop:
one optional dynamic mode (`NONE` / `ABSOLUTE_DROP` / `PERCENTAGE_DROP`) plus
an always-on emergency floor at `emergency_drawdown_pct` (default 75%) of the
fill-weighted entry price (`held_average`), never below `$0.01`.

## Acceptance criteria

- Entry `0.10` at 75% triggers at `0.025` (bid `0.026` waits; `0.025`/`0.024`
  hard-stop). Entry `0.12` with bid `0.14` does NOT stop under `NONE`
  (the original low-entry bug: old `$0.15` rule sold on fill).
- No live path in `core_brain/` uses `hard_stop_bid` or `$0.15` as a stop
  (proven by search at build time).
- `python -m pytest tests/test_dual_stop_loss.py tests/test_single_leg_lifecycle.py -q` — all pass.
- `LegState.HARD_STOP`, action `hard_stop`, close reason `lifecycle_hard_stop`
  unchanged (Trader suppression + shadow fresh-book path key on them).
- Live stop exit sends explicit `OrderType.FOK`; shadow branch and
  `core_brain/shadow_exec.py` signature unchanged.
- #618 closed as superseded on publish.

## Explicit out-of-scope

Dashboard controls, stream-driven per-tick stop path, selling already-paired
shares, widening the `tests/test_sale_close_pricing.py` guard, touching
`data/orders.db`, any live `quote` / `complete` / Trader loop / dashboard START.
Full untouched-file list lives in `tasks/plan.md`; noticed-but-untouched rows
live in `docs/issues/619-noticed-but-not-touching.md`.
