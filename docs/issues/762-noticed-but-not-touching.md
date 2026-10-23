# 762 — noticed but not touching (Station III)

#762 scopes to orphan cleanup in `complete_pair`: when the venue market-order
submit raises or returns no venue order id, the pre-inserted local completion
row is cancelled (or attached when the error carries an id) so no unattached
`pending` row survives in `get_active_orders()` to lock the bankroll ceiling.
This ledger records what was found during the fix and **deliberately left alone**.

## What #762 changed (the deliberate part)

| Path | Reference | Decision | Justification |
|---|---|---|---|
| `core_brain/single_buy_saver.py` (`complete_pair` post block) | pre-inserted `OrderRecord(status="pending", order_id="")` then `create_and_post_market_order` | **changed** | Exception without a recoverable venue id cancels ONLY `local_comp_id` (`completion_submit_failed:<Exc>`) and re-raises; exception carrying a venue id attaches it and keeps the row live for the stray guard; response without an id cancels and raises `PairCompletionRefused`. Shadow path still writes no row. |
| `core_brain/single_buy_saver.py` (`_venue_id_from_submit_error`) | new helper near `ask_depth` | **added** | Recovers `orderID`/`orderId`/`order_id`/`id` from exception attributes, dict args, or `__cause__`; never guesses. Distinguishes unknown-but-reconcilable from nothing-to-reconcile. |
| `tests/test_single_buy_saver.py` | 3 regression tests (generic raise, raise-after-accept, missing id) | **added** | All fail on origin/main, pass on branch; money assertions via `pytest.approx`; `tmp_path` only. |

## Noticed but not touching

| # | Noticed | Why out of scope | Owner | When | Status |
|---|---|---|---|---|---|
| N1 | `complete_pair` sizes with `size = min(pair["naked"], ask_depth(book))` where `ask_depth` sums ALL ask levels, while the BUY is submitted at `price=ask` (best ask). Depth above the limit price is counted as fillable. | Bounding depth to levels at or below the limit price is #764's scope; changing it here would alter sizing/pricing semantics the issue forbids touching. | you | when #764 lands, with its own ticket | open |
