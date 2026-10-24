# #797 pair-cap hedge bail-out — out-of-scope notes

Findings from the cap bail-out that belong to other owners or later passes.
The shipped change is exit-side only: a naked leg whose pair cap cannot reach
the opposing best ask for the strategy's hedge window leaves through the
existing `exit_single_buy` route with `reason=pair_cap_hedge_unreachable`.

## 1. The #453 stranded route still owns a genuinely-stranded exit

Owner: agent. When: the first run that books both shapes.

The cap check sits after the #453 stranded block on purpose: when exposure is
readable AND the pair is stranded/under-covered, that block already force-exits
after the same 300s window with `reason=stranded_unhedged_exit`. So a leg that
is both stranded and cap-unreachable books the #453 reason, not the cap reason.
A cap bail-out therefore appears where the stranded block could not act (moe:
exposure unreadable / not classified stranded). If the ledger needs the cap
cause to win in the overlapping case, that is a deliberate change to the #453
contract and its test, not a silent reordering — decide it on a real run.

## 2. One window, two legs of behaviour

Owner: operator, informational. When: tuning `bot_risk_stranded_max_wait_sec`.

The bail-out reuses `bot_risk_stranded_max_wait_sec` (300s) rather than adding
a knob, as the issue asked. Moving that number moves the maker-wait window AND
the bail-out window together. If a run ever needs them apart, a dedicated knob
is the change — do not fork the constant in code.

## 3. Entry-side reachability is deliberately untouched

Owner: none (closed by the issue). When: never, without a new issue.

moe's hedge was reachable at quote time (DOWN ask 0.151 vs the 0.17 the cap
allowed) and unreachable 15 minutes later. No quote-time "is this hedge still
reachable" gate was added; entry gating would have refused a pair that was
cheap when chosen. The fix is exit-side timing only.

## 4. No dashboard surface names an exit route

Owner: agent. When: a UI issue that shows per-pair lifecycle reasons.

`route` (`pair_cap_bailout`) is returned by the poll and `reason` is persisted
on the close, but no dashboard or report enumerates single-leg exit routes —
`lifecycle_hard_stop`, `stranded_unhedged_exit` and the new reason are all
visible only through `closes.reason`. A route column belongs with a real
single-leg panel, not bolted onto this fix.

## 5. The bail-out counts as a forced sale in stop analytics

Owner: agent, only if the operator wants it separated. When: next
statistical review.

`pair_cap_hedge_unreachable` was added to `HARD_STOP_SELL_REASONS` so the
shadow engine sells into the fresh book instead of a pre-decision snapshot
(the H1/H2 stale-snapshot bug). A side effect is that
`stop_slippage_summary` now includes cap bail-outs in the forced-sale slippage
population, and `fill_quality_report` labels the condition `dead_leg` — both
correct for a leg we chose to sell at a loss, but they are a taxonomy decision
to revisit if cap bail-outs and circuit stops should be measured apart.
