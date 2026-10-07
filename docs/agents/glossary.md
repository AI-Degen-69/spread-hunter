# Glossary — current names

The stack was renamed for clarity. Use the right-hand column everywhere: code, commits,
issues, dashboard copy and operator instructions.

| Was | Now | Where it lives |
| --- | --- | --- |
| Screener / ranker | **Market Filter** | `scripts/filter_markets.py`, loop in `scripts/filter_loop.py` |
| Engine Poll Loop (5s) | **Order Manager** (0.5s) | `core_brain/order_manager.py`, `poll --interval 0.5` |
| Fleet / Quoting Fleet | **Trader** | `core_brain/trader_loop.py` |
| Naked leg / one-sided fill | **Single buy** | `core_brain/single_buy_saver.py` |
| `dash/live_dash.py` | **Dashboard server** | `dashboard/server.py` |
| `run/live.db` | **Orders DB** | `data/orders.db` |
| `scripts/live-spread-hunter-menu.ps1` | **Operations menu** | `scripts/spread-hunter-menu.ps1` |
| `engine/` | **`core_brain/`** | the execution package; import as `from core_brain.x import y` |
| `run/` | **`runtime/`** | on-disk state: `markets.json`, `processes.json`, the cycle ring |
| `scripts/guardrail_watch.py` | **Global Stop Loss** | `scripts/global_stop_loss.py` |
| `run/live_procs.json` | **Process file** | `runtime/processes.json` |
| process keys `screener` / `engine` / `fleet` | **`filter` / `query` / `decide`** | `runtime/processes.json`, `/api/system/status` |

Also fixed vocabulary: **single buy** (not naked leg, one-sided, or unhedged),
**pair cost**, **merge** as the exit, **graduated** for the markets listed in
`runtime/markets.json`.

## Code revision

A rehearsal outlives the code it started with. One word for which code it holds, and
one for the verdict when the tree has moved on:

| Term | Means | Does not mean |
| --- | --- | --- |
| **code revision** | The code a running process loaded, recorded once at start: short commit, `+dirty` when the working tree was modified, and the mtime of the newest decision file under `core_brain/` or `scoring/`. | The code on disk now |
| **older than this tree** | That revision predates the decision code on disk now, so the run's numbers describe code that is no longer here. | A run that has stopped, or one whose numbers are wrong |
| **unknown** | No usable evidence of the run's code (no recorded clock and no process start time to bound it). | Current — evidence-free is never reported as stale, and never as clean |

Write it as `code 80d03f1+dirty`, `code 1f2e3d4 (older than this tree)`, or `code
unknown`. A heartbeat from before the stamp can still read `code unknown (older
than this tree)`: with no recorded revision the run is bounded by when its
process started, and a process cannot hold code newer than the process. Dashboard copy stays sentence case with a full stop. Code that does not
decide a quote -- `dashboard/`, `scripts/` -- is outside the clock on purpose, so
editing the page does not mark every running rehearsal stale.

## Order lifecycle

Three stages, named the same way in code, tests and dashboard copy. Each name means
one stage and no other:

| Term | Means | Does not mean |
| --- | --- | --- |
| **Active Market** | A market that graduated the Market Filter and is being quoted. Nothing is owned. | A market with a position in it |
| **Open Order** | An order resting on the book, unfilled or partly filled. No exposure, so no PnL. | A filled leg |
| **Position** | Shares the account holds after a leg filled. The only stage with PnL. | An order waiting to fill |
| **Pair Cost** | UP price + DOWN price. Under $1.00 the merge books a profit, exactly $1.00 books nothing, over $1.00 books a loss. | The cost of one leg |

## Pair status

One vocabulary, used on the book and on what filled. A pair is in exactly one of
these three states, and nothing else is a pair state:

| Status | On the book (Open Orders) | Filled (Positions) | Tone |
| --- | --- | --- | --- |
| **Paired** | Both legs resting, same size | Both legs held, same share count | good |
| **Partial** | Both legs resting, sizes differ | Both legs held, share counts differ | warning |
| **Unpaired** | One leg resting, no partner | One leg held — this is a **single buy** | alert |

`Unpaired` names the state of the *pair*. `single buy` names what the account is
holding once an Unpaired position exists. Do not use one for the other, and never
write "one leg resting", "half a pair", "single leg", "hedged" or "unhedged" for any
of them.

## Dashboard copy

Operator-facing strings are sentence case with a full stop; column headers and tags are
Title Case. Name the component, never the old name: **Market Filter** (not screener or
ranker), **Trader**, **Order Manager**, **Global Stop Loss**. Status tags carry one of
three tones and each tone means one thing — good, warning, alert — and no tag is asked
to mean two things at once. Pair status and Pair Cost are separate tags because they
answer separate questions: is the pair whole, and does merging it pay.

Old module paths (`scripts/rank_markets.py`, `scripts/rerank_loop.py`,
`scripts/live-spread-hunter-menu.ps1`) survive only as thin forwarders for anything still
calling them. Do not add to them.

Commit scopes follow the package name: `fix(core_brain): ...`, not `fix(engine): ...`.
