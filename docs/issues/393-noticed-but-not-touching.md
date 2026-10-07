# Noticed-but-not-touching — Issue #393 (Station III build)

Out-of-scope findings spotted while building the queue-clear gate. Each row is `open`
until a later station or the operator dispositions it.

| ID | Candidate | Discovering station | Evidence | Status | Resolution |
|----|-----------|---------------------|----------|--------|------------|
| N1 | `_submit_intents` documents a `pair_id` "stamped on the intents by `plan_orders`", but `plan_orders` never assigns one — the carried-id path is only ever populated by `ladder.route_quotes`. Stale docstring: it invites a future session to key couple logic on `pair_id` and silently no-op (exactly what the CodeRabbit plan did before this build corrected it) | III | `core_brain/trader_loop.py` `_submit_intents` carried-id comment vs `plan_orders` body; only setter is `core_brain/ladder.py:113` | open | — |
| N2 | Two levers now govern one idea on two layers: the ranker's maker-queue bar (`select_max_queue_minutes=15.0`, `enforce_max_queue_minutes=False`, inert via `resolve_queue_bar`) and this quoting-time gate (60 min, record-only). Different names, different bars, one concept — a later session should decide whether one bar should govern both or say why they must differ | III | `scoring/config.py:422-427`; `scripts/filter_markets.py:2894-2913`; `core_brain/config.py` queue-clear settings | open | — |
| N3 | The flow read is uncached: while a market's front is deep, the gate re-reads the tape once per rotation (a venue round-trip every `--interval`, default 5s). Accepted for now on purpose — a TTL cache is more moving parts than the measurement it saves, and the record-only reasons are what the threshold gets picked from | III | `core_brain/trader_loop.py::_admit_placements`; `tasks/plan.md` Notes ("Residual cost, accepted honestly") | open | — |
