# The fleet data flow — how the engine reaches the dashboard

This is an explanation, not a reference. It walks the path a fleet event
takes from the strategy loop to the pixels on the dashboard, and — more
importantly — the rules that keep that path safe. If you are new to the
repo, read this after the README and before touching either
`strategy/` or `server/`. The vocabulary (sweep, book gate, resting,
markout) is defined in [`CONTEXT.md`](../CONTEXT.md).

## The write side: the fleet loop

The fleet is one process — `strategy/fleet.py` — running a rotation loop.
`main()` spins forever: for each market in the fleet it runs a sweep,
catches exceptions per market so one bad venue cannot kill the heartbeat,
and periodically re-ranks and reallocates.

The per-market unit of work is the **sweep** — `sweep(state, ctx) ->
outcome` in `strategy/sweep.py`. The sweep module's docstring says it
plainly: the sweep is the *one* interface into a market pass, and
everything under it is a private step — identity gate, market load, book
gate, fill processing, gate advance, exits, requote, score-and-publish.
`fleet.visit()` still exists but only as a backward-compatible alias; the
loop calls the sweep module directly.

Two things happen inside the sweep that matter for the rest of this
document:

- **Meaningful events are persisted.** The sweep calls `_record_event()`,
  which collapses routine repeats (a QUOTING event repeated within 30s is
  not re-logged; a QUOTING right after a FILLED stays quiet) and writes
  the survivors through `store.log_event()` into the `market_events`
  table. That table is deliberately *event-shaped* — fills, exits, gate
  refusals, quote-state transitions — so the dashboard can show what a
  market did without re-deriving it from inventory arithmetic.

- **The loop's heartbeat is updated.** Once per market visit (~1s) the
  loop stamps `loop_ts` into its in-memory pulse object.

Outside the loop, a dedicated thread and the allocator do their own
things. The `_pulse_writer` thread copies the pulse to disk every 10
seconds; `reallocate()` water-fills the bankroll across markets against
`max_naked_usd` — the $120 per-market cap in `strategy/config.py`.

## The handoff: JSON on disk, plus SQLite — nothing else

The fleet never talks to the dashboard directly. No sockets, no shared
process, no RPC. The entire handoff is:

- `run/fleet_pulse.json` — the live heartbeat. Written atomically (temp
  file + rename) every 10s by a thread that does not compete with the
  trading loop for time.
- `run/fleet_state.json` — one entry per market's current spec. Written
  once per *complete* sweep, so its mtime measures sweep duration, not
  liveness.
- `run/markets.json` — the ranker's output, the fleet's market menu.
- SQLite (`store.db()` in `strategy/store.py`) — written by the strategy,
  read by both dashboards.

Two design decisions hide in that list, and both are about honesty:

**The state file is not the heartbeat.** A 20-market sweep takes 50–70s
when healthy and can pass 120s when one venue is slow. A heartbeat based
on the state file's mtime would call a correctly trading fleet dead. That
is why `fleet_dash.fleet()` prefers the pulse — `loop_ts` refreshed ~1s,
published every 10s — and treats the state file as fallback.

**Atomic writes exist for the reader's sake.** The dashboard reads these
files on its own schedule. A reader that catches a partial write would
parse a truncated JSON object and report the fleet dead — for exactly the
reason the file exists to disprove. Temp-file-plus-rename means readers
only ever see a complete file. (DB writes are kept out of the heartbeat
entirely: historical DB activity must not make a dead fleet look live.)

## The read side: what the dashboard sees

Two dashboards read the same source. `server/fleet_dash.py` is the legacy
view, demoted to :8801 and kept only for its market-scan view.
`server/spread_dash.py` is canonical at :8800.

`fleet_dash.fleet()` does the actual reading: it loads the state file and
makes **one** `stats.snapshot()` call for the whole DB-derived payload.
That one-call rule is the reader/writer seam in miniature — the state
reader (in `strategy/stats.py`) owns every read query; the page owns HTTP
and HTML. From the specs and the snapshot it derives each market row —
paired and naked inventory, committed dollars, unrealized PnL — and the
fleet-wide naked USD total.

`fleet_dash.pipeline()` caches its own payload the same way on the scan
side: a background thread refreshes it every 10s and the endpoint serves
the freshest snapshot instantly, falling back to an on-demand build only
when the snapshot is missing or the thread is dead. The cache is keyed by
the run directory so per-test `RUN` overrides can never serve each other's
snapshots.

`spread_dash.py`'s `api_summary` layers on top: it calls
`_cached("fleet", fleet_dash.fleet)` and `_cached("pipeline", ...)` — 8s
TTL, guarded by a lock — plus `go_live_readiness()` and the realized /
markout / maker-rebate stats. The cache exists because a full snapshot
read can take 9–12s under the live writer's lock traffic, and a
monitoring page that blocks 10 seconds on every load looks dead.
8s-stale is indistinguishable from live, because the pulse writes roughly
every 10s. A warm-up thread primes the cache shortly after startup so the
first load after a restart is not the slow one.

The verdict is pure read-side math. `go_live_readiness()` in
`strategy/stats.py` computes where the settled sample stands against the
readiness tiers — COLLECTING, DIRECTIONAL_SIGNAL,
READY_FOR_SMALL_LIVE_PILOT. The dashboard's `renderHinge()` is the only
place those become the desk's one-line call: GO, SIGNAL, COLLECT — gray
NO DATA when there is no verdict. The deciding figure (the confidence
lower bound) stays in the Verdict panel; the hinge answers the desk's
only question: is the strategy working, and how fresh is that answer?

## The reader/writer seam

One sentence holds the whole architecture together: **the strategy
writes; the dashboards read; nothing writes from the read side.** The
seam is the contract — writers in `strategy/` (SQLite via `store.py`,
atomic JSON via `fleet.py`), readers in `server/`. It is why `fleet()`
returns an `{"markets": [], "error": ...}` payload instead of raising
when the fleet is down, why the dashboard never opens the DB in write
mode, and why a caching layer is safe to add on top of a read.

The seam has a second property worth naming: it is *bounded and
testable*. The API surface is tiny — a handful of JSON files plus the
SQLite tables `store.py` creates. `tests/test_dashboard_page.py` pins
the `api_summary` contract; store round-trips pin the writer. Anything
that respects the seam can be added, profiled, or swapped without the
other side knowing.

## The float-marks gap: nothing on the read side is truly historical

`market_events` is the only persisted telemetry stream, and it is
event-shaped — fills and exits, not a continuous time-series of fleet
state. The dashboard's live numbers (`unrealized_usd`,
`committed_open_usd`, `naked_usd`) are **derived per request** inside
`api_summary` and persisted nowhere. That has a consequence the dashboard
already owns up to: the Total equity view cannot plot a real historical
series of open float. It can only shift today's realized curve by today's
float — which is exactly what the widget's honesty note says.

The gap is now closed (fleet-side, 90-day retention). The one thing
`market_events` is not — a periodic mark — was added:

- a `float_marks` table in `store.py`, one fleet-wide row per sweep
  (`log_float_mark()`, written at the sweep boundary of `fleet.py main()`
  beside `log_income_sample`), carrying the unrealized float, the dollars
  committed, and the naked residue — derived exactly as the dashboard
  derives them at read time, so the series means what the dashboard's
  open-position numbers mean;
- a `float_history` slice in `api_summary` (downsampled to ≤1 point per
  minute, capped at 1,000), and
- a `marks` input to the widget's `capitalSeries()`, so the Total line
  time-merges closes and marks instead of applying a constant shift.

The writer placement trade-off was decided **fleet-side**: server-side
recording would only capture while the dashboard is polled, which is the
same class of dishonesty the old note flagged. Retention is a `float_mark_retention_days`
knob (`strategy/config.py`, default 90 days), pruned on the write path so
the table stays bounded while the history stays deep — the dashboard
downsamples to one point per minute anyway, so finer-than-a-minute
retention buys nothing.

One honest caveat remains: marks only exist for sweeps that actually ran.
A gap in the run (restart, crash, laptop asleep) is a gap in the float
history too — before the first recorded mark, the Total line carries no
float shift, and a DB with no marks yet falls back to today's float.
