Branch: i730/record-stop-slippage | Issue: #730

# SPEC — #730 [MEASURE] Record stop-slippage on hard-stop exits

## Goal

Every hard-stop exit sells into the book at a floor
(`best_bid - MAX_SELL_SLIPPAGE`, floored to tick, `single_buy_saver.py:1115`)
and records only the fill it got. The fleet cannot answer "what does a hard
stop actually cost us" — the number that decides between limit-order exits,
a wider cushion, or market orders, and gates #646 N2 (repricing a leftover
leg). Record the decision price and the achieved price, aggregate the
slippage into the KPI report.

## Acceptance criteria

1. An exit whose fill matches the floor reports `0.00` slippage — never
   "unknown".
2. An exit that never filled (refused, zero size, fallback-priced) counts as
   unmeasured — never zero (the queue-gate rule).
3. The KPI report shows the aggregate without a schema break: existing keys
   and meanings unchanged; a version bump on both sides; a stale backend
   still renders the old page.
4. A test fails without the field and passes with it — including the
   unmeasured case.
5. `python -m core_brain.kpi --db data/orders.db` shows a stop-slippage
   figure once a run has produced a hard-stop exit; a shadow rehearsal that
   produces a simulated exit moves the figure off "unmeasured" with a
   rehearsal-labelled note; a refused rehearsal keeps it unmeasured and
   raises the refused count.

## Deliverables

- `closes.submitted_floor` (the floor the order was sent at) and
  `closes.fill_source` ∈ {reported, simulated, assumed} — both nullable, no
  backfill; old rows = historical unmeasured.
- Append-only `hard_stop_refusals` table (refused/errored hard-stop
  attempts; excluded from resets).
- One always-present `stop_slippage` section (pure summary): counts,
  mean/worst vs floor, mean/worst vs decision bid, below-floor count,
  unmeasured reasons, refusal counts, simulated count, units.
- Read-only `python -m core_brain.kpi --db <path> [--run-id <id>]`.
- Dashboard card beside rescue-friction; unmeasured renders "unmeasured",
  never `0.00`.
- Demo seeds for the operator's hands-on check; N3 ledger row marked
  addressed.

## Edge cases

- Shadow walk that takes nothing still books the requested size at the floor
  — the truth (took_shares) must be threaded, not inferred.
- Partial closes (FOK leaves no remainder, but rescue sequences may close in
  parts) count as one observation per close, unweighted.
- Refusals repeat per poll for the same position: positions counted by
  distinct `pair_id`, attempts by rows.
- Live SDK responses without price/size fall back today: those exits remain
  booked as before but read `assumed` ⇒ unmeasured.
- Stores older than the columns: keys absent ⇒ historical unmeasured; the
  CLI never writes anything.

## Out of scope (from issue, unchanged)

- Any behaviour change: exits stay market-FOK at the same floor; no
  repricing, no new order path, no threshold move, no `MAX_SELL_SLIPPAGE`
  change.
- Parsing `takingAmount`-style live responses (separate ticket).
- Existing `rescue_friction` wording/units quirks.
- New scenario launchers (demo store covers the refused case).
