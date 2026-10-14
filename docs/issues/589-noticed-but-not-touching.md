# Noticed but not touching — #589 (open candidates)

Recorded by Station III during the ladder removal. Rows resolve at Station VI
(`vi-close-pipeline`); an operator may promote any row to its own issue earlier.

| # | Candidate | Discovering station | Evidence | Status | Resolution |
|---|---|---|---|---|---|
| N1 | `_route_pair` now has no production caller — only tests invoke it. Deletion is a clean follow-up (CodeRabbit's plan flagged it too); its refill/trigger-bid tests would move to the unified lifecycle. | Station III, T2 | `core_brain/single_buy_saver.py` (`def _route_pair`); callers: `tests/test_refill_after_exit.py`, `tests/test_single_buy_saver.py` only | open | — |
| N2 | `docs/explanations/343-presentation-live-ladder-pricing.html` is a second ladder presentation still shown without a retirement banner — same category as the explainer, but outside this issue's file list. | Station III, T6 | `docs/explanations/343-presentation-live-ladder-pricing.html`; pinned by `tests/test_docs_layout.py:27` | open | — |
| N3 | `docs/data_inventory.md` rows `shadow_run_shadow-ladder-live-*.json` describe real runtime files from past ladder runs. Left untouched: the file is a dated audit (2026-10-06) of files that still exist on disk; renaming rows would falsify it. They age out under the 14-day policy. | Station III, T5 | `docs/data_inventory.md:289-291` | open | — |
