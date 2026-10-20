# #659 — noticed but not touching

Out-of-scope findings from the unify-real-time-pricing build. Each row is a
future issue candidate; the operator decides whether it becomes one. Only
Station VI resolves rows.

| ID | Candidate | Discovering station | Evidence | Status | Owner / when |
|----|-----------|---------------------|----------|--------|--------------|
| N1 | Wire `order_manager` poll-loop defense (`manage_single_leg_positions` at `order_manager.py:2493`, `run_stray_guard` at `order_manager.py:413`, `order_registry.py:2335`) to a process-local stream-backed provider | III | `core_brain/order_manager.py:2493`, `core_brain/order_manager.py:413` | open | Trader-loop wiring first; poll loop is a separate process with no shared in-memory stream, so it needs its own `MarketStream` lifecycle, not a port pass-through |
| N2 | Shadow rehearsal with a live `MarketStream` attached (real-time depth in `run_shadow` instead of REST-only provider) | III | `core_brain/shadow_run.py:998` (`stream=None`) | open | After N1; rehearsal determinism vs live-depth tradeoff needs an operator call |
| N3 | Dashboard `live_marks` rebuild on top of `market_stream` (blocked on #657 differential frames) | II | `core_brain/live_marks.py` | open | #657 owner; this ticket only mirrors the mid validity rule |
| N4 | Equal-price shadow fills + stream-print fill crediting (fill-policy change, not transport) | II | CodeRabbit Design Choices 1–2 on #659 | open | Separate money-sensitive decision, never a side effect of this migration |
