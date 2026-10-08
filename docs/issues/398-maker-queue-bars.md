# Two maker-queue bars (Issue #398) — decision record

Operator decision 2026-10-08. No config value changed.

## The two layers

Both layers answer one question — "how long would our queue take to clear at
our own price?" — with the same arithmetic (`queue_minutes_at` and
`maker_queue_allowed` in `scoring/selector.py`; `max_queue_minutes <= 0`
disables, as everywhere else). What differs is only the moment of measurement.

### Layer 1 — ranker bar (market-selection time)

- Setting: `select_max_queue_minutes = 15.0` (`scoring/config.py`), gated by
  `enforce_max_queue_minutes = False`.
- Moment: selection time, in `scripts/filter_markets.py` (`queue_bar_reject`,
  applied before the two book fetches) — avoids paying for books the layer
  would refuse.
- Posture: genuinely inert today, not merely disabled. `resolve_queue_bar`
  returns `None` unless enforced, so `evaluate` calls no measurement function
  and the ranking pass buys no extra tape reads for a number nothing acts on.
- The 15 minutes are anchored to `pairs_exit_window_sec = 900`: a queue that
  cannot clear inside the window where a naked leg is still called fresh is a
  queue we should never join.

### Layer 2 — queue-clear gate (placement time, #393)

- Setting: `max_queue_clear_minutes = 60.0`, `enforce_queue_clear_gate = False`,
  `queue_flow_window_sec = 1800.0` (`core_brain/config.py`; env
  `HUNTER_QUEUE_CLEAR_GATE`, `HUNTER_MAX_QUEUE_CLEAR_MIN`).
- Moment: placement time, in `trader_loop.py` via `risk.queue_clear_block` —
  judges the queue in front of the bid actually being posted (queue ahead at
  the bid's own price vs reachable taker-SELL flow inside the 30m window).
- Posture: live but record-only. It measures on every gated placement and
  reports the reason without dropping anything.

## The rule

Keep the two moments separate, with one vocabulary: the ranker measures early
to skip work, the gate measures late to judge the real bid. Each bar keeps its
own name, default, and moment — neither is renamed onto the other.

## What this does not cover

Retuning either value, placement pricing or `decide_quotes`, the shadow seam,
`filter_markets.py` ranking thresholds beyond the queue bar itself, or flipping
either `enforce_*` flag — each flag flip is its own operator decision, taken
with real record-only numbers in hand.

## Consolidation (deferred, operator decision)

A single shared bar is a money lever, not an agent default: the smaller bar is
the stricter one, and unifying on 15 minutes would refuse markets the ranker
currently admits, while unifying on 60 would loosen the ranker's anchor to the
exit window. Revisit only with accumulated record-only reasons — and if so,
name the surviving setting, its default, and which layer reads it.
