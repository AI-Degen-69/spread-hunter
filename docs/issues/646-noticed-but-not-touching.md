# 646 — noticed but not touching (Station III)

Scope: #646's build. Each row is something seen while working that was left alone, and what happened to it.

| # | Noticed | Severity | Status |
| --- | --- | --- | --- |
| N1 | The screener caches Gamma payloads for a TTL, so a market that just resolved can still be quoted for the rest of the window. | medium | resolved — the UMA re-check gate reads Gamma directly per visit through its own 30s cache, so a resolved market is refused before sizing |
| N2 | A partial fill leaves a leftover leg with no repricing rule: it keeps the original limit until the stop exits it. | medium | open — blocked: needs the number from #730. Repricing a remainder is only worth building if a stop exit already bleeds, and that cost is unmeasured today |
| N3 | Hard-stop exits record the fill they got but never the slippage against the floor that triggered them, so the cost of an exit is invisible. | medium | published as #730 (measurement only, no behaviour change) |
| N4 | Exit paths and defect paths read books through two different seams, so a fix to one does not reach the other. | low | dismissed — `BookProvider` closes this by construction: `fetch_fresh_books`/`get_fresh_book` are the REST-only read every execution path gets, whatever seam was installed upstream |
| N5 | The risk guardian ran the lifecycle pass with no wallet in hand, so `venue_positions` stayed `None`: every pair whose remainder could not be proven flat on the registry alone — a partial exit's leftover — was skipped instead of sold. | **high** | resolved — the guardian takes a `funder` (falling back to `POLY_FUNDER`, the var the rest of the repo uses) and forwards it, so the pass fetches the account's position and sizes the exit against it. `trader_loop` hands it `seam.maker_address`, the wallet the seam already reconciles against. A remainder now gets sold rather than skipped |

## N5 — the fix, and why it was the highest row

`manage_single_leg_positions` fetches the venue position only when it has a wallet: `if venue_positions is None and live and funder`. With no funder it cannot, and a pair whose registry inventory looks non-flat — because an ordinary `single_buy_exit` close does not net fills out of the inventory the way a `venue_sync` close does — was skipped on a silent `continue`.

So the guardian, the one process whose whole job is defending unhedged legs, was the one caller that could never sell a remainder it had left behind. That is the second failure mode this repo exists to prevent: a naked leg nobody manages, sitting there while the process that exists to catch it walks past.

The fix is the view, not a louder message: `run_guardian_tick` takes a `funder`, resolves it from `POLY_FUNDER` when a caller passes none, and forwards it to the saver; `trader_loop` passes `seam.maker_address` to both. Where no view exists at all (a `--no-live` dry run) the old silent skip stands, because a pass that cannot reach the venue cannot sell either.

One thing that did **not** ship: an extra per-pair "skipped for want of a view" action. A dry run would have printed it once a second for every exited condition, and it would have claimed a remainder exists when the registry only ever proved that it could not prove the pair flat. That is a fabricated signal, so it was dropped in favour of fixing the reason the skip happened.

## N2 and N3 — why they are separate, and which comes first

Repricing a leftover leg (N2) is a real money decision: it moves a live order while the book is moving. Building it before knowing what a stop exit costs would be optimising the smaller of two leaks. #730 measures the larger one with no behaviour change, and N2 stays blocked until that number exists rather than being pre-decided on intuition.
