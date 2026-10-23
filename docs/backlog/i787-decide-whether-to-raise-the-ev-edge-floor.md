# Backlog — Issue #787 (decide whether to raise the entry edge floor)

Out-of-scope candidates noticed during the build. Station III records them; only
Station VI resolves a row. An `open` row is never deleted.

| # | Candidate | Found in | Evidence | Status | Resolution |
|---|---|---|---|---|---|
| N1 | A negative edge is labelled `"0.06+"` by `edge_bucket_for`, so no label floor can ever block it — the live gate cannot express "worse than zero". | Station III (T3) | `core_brain/fill_quality_report.py:288`; 1 such position on `shadow-01-prudent` | open | — |
| N2 | A position with no book quote buckets as `"unknown"` and also passes every label floor. | Station III (T3) | `scripts/edge_floor_analysis.py` `UNKNOWN` bucket; 0 positions in both tapes | open | — |
| N3 | Gate refusals are only in-memory reasons, never persisted per quote, so "how many opportunities did the floor refuse" cannot be answered from a store — only "how many quotes posted". | Station III (T3) | `core_brain/trader_loop.py:266-283`; `fill_quality_report --ab` counts labels, not refusals | open | — |
| N4 | The edge framing is dollars-per-share; the operator's `<1% / >10%` questions are percent-of-price. A relative-edge view needs its own analysis. | Station III (T3) | `core_brain/fill_quality_report.py:64` `EDGE_EDGES`; `docs/audits/edge-floor-decision.md` § "What was asked" | open | — |
| N5 | The 3–4¢ bucket loses money on both tapes (−$6.35 over 35 closed, −$14.22 over 40 closed) while its median position is profitable — the loss is concentrated in a few large single-leg exits, not in thin edges. | Station III (T3) | `reports/edge_floor_shadow-01/edge_floor.json`, `...-prudent/edge_floor.json` | open | — |
| N6 | `data/orders.db`, the production registry, is empty in this checkout (0 orders, 0 fills, 0 closes), so no audit can answer a live-performance question yet. | Station III (T3) | `python -m core_brain.order_manager status` | open | — |
| N7 | The lifecycle logs `pair_id=... does not identify one UP token and one DOWN token in the quotes ledger` once per cycle for a pair that only ever had one side quoted, and the run ends holding that lone leg (185 repeats, 1 filled 10-share leg). | Station III (T3) | `reports/shadow-787-floor.log`; condition `0xfb9d77bf...` has 1 quote, 1 filled order | open | — |
