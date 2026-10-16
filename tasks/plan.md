# Plan - #627 Anchor entry pair-cost validation and passive single-leg break-even tracking

Branch: i627/anchor-entry-pair-cost-and-passive-break-even | Issue: #627

Tier: **Small** — one narrow entry gate on the from-mid path plus one read-only
telemetry field; no schema, lifecycle, trader-loop, or dashboard changes.

Base: `main` @ `2837e95`, clean. Related: #626 (resting re-gate purged), #622.

## 0. Verified premise (read before any task)

The gap is real and narrow. On the path the fleet runs
(`decide_quotes` -> `_decide_quotes_from_mid` for `objective` in
`("spread_capture", "rewards")`, `quotes.py:705-709`), a flat-book pair has NO
`Bid_UP + Bid_DOWN <= max_pair_cost` check on the final resting prices:

- `risk.hard_block` pair-cost arm (`risk.py:515-527`) needs a held average
  (`other_avg > 0`); flat book has none, so it passes silently.
- `dynamic_pair_sum` (`quotes.py:341-344`) runs only when
  `dynamic_offset_enabled` is true.
- The legacy fresh-market `p_up + p_dn >= max_pair_cost` refusal
  (`quotes.py:769-771`) sits below the from-mid early return — unreachable.

`break_even` exists nowhere in `core_brain/` or `registry_state.py` (only
`break_even_rescue_rate` analytics in `statistical_analytics.py`, unrelated).
`tests/test_quotes.py` does not exist — it is created by this plan and is the
acceptance file the issue names.

Contradiction recorded: the issue allows equality (`<= max_pair_cost`);
`hard_block` refuses equality (`>=`, `risk.py:518-522`) and the legacy
fresh-market check refuses equality (`>=`, `quotes.py:770`). The new entry
gate allows the tie per the issue; existing arms keep their `>=`.

## 1. Interfaces (verified verbatim)

- `MakerConfig.max_pair_cost: float = 0.99` (`config.py:769`);
  `max_completable_pair_cost: float = 1.00` (`config.py:782`).
- `decide_quotes(cfg, up_book, down_book, inv, t_remaining, window_frac=None,
  *, lifecycle_override=None)` (`quotes.py:688-709`) — UNCHANGED signature.
- `_decide_quotes_from_mid(cfg, up_book, down_book, inv, t_remaining,
  lifecycle_override)` (`quotes.py:241-562`) — T1 insertion: after `out` built.
- `quote_resting_price(cfg, inv, side, book)` (`quotes.py:190-238`);
  `calc_pair_price = round(p_up_calc + p_dn_calc, 4)` (`quotes.py:335-339`).
- `QuoteIntent(side, token_id, price, size, mid, edge_vs_mid, reason,
  crossed=False, pair_id=None)` (`quotes.py:44-57`); lifecycle escalation
  stamps `pair_id` (`quotes.py:684`). Gate: exactly two intents, both
  `crossed is False` and `pair_id is None`. Single repair, crossed hedge,
  lifecycle skip.
- `risk.hard_block(...)` (`risk.py:413-424`) — UNCHANGED. Light side exempt
  from exposure arms, never from pair-cost (`risk.py:470-476, 529-530`).
- `risk.completable_pair_block(cfg, price, hedge_ask)` (`risk.py:326-363`) —
  UNCHANGED.
- `summarize_state(db_path, now=None)` (`registry_state.py:71-76`), read-only
  `mode=ro` (`registry_state.py:54-68, 111-115`) — T2 insertion: per-pair
  anchor after `combined_price`.
- Fills query: `SELECT f.trade_id, f.order_uuid, f.size, f.price, f.venue_ts,
  o.side, o.pair_id, o.token_id, o.condition_id ... ORDER BY f.venue_ts DESC`
  (`registry_state.py:197-203`) — no `recorded_ts`. T2 adds `f.recorded_ts`
  to the SELECT only.
- Pair grouping by `pair_id`, `combined_price = round(sum, 4)`
  (`registry_state.py:308-335`). `combined_price`/`avg_price`/`naked_info`
  untouched by T2.
- `FillRecord(trade_id, order_uuid, size, price, venue_ts=None,
  recorded_ts=None, run_id=None)` (`order_registry.py:696-709`); `record_fill`
  dedupes on `trade_id` (`:1187-1216`); fills cols `trade_id PK, venue_ts,
  recorded_ts` (`:220-225`). NO schema / `record_fill` change.
- `OrderRecord(..., pair_id=None, max_pair_cost_at_post=None, ...)`
  (`order_registry.py:678-693`) — UNCHANGED.
- Dashboard `_trim_cancelled_orders` (`server.py:629-688`): live pairs pass
  through whole (anchor flows automatically); dead-pair lean keys omit it.
  NO server change; omission for all-cancelled pairs accepted.
- Test precedents: `_healthy_book` / `_gate_cfg` fixture shape
  (`test_completable_pair_gate.py:76-88`); gate-in-`decide_quotes` tests
  (`:91-131`); `temp_db` via `SCHEMA` (`test_registry_state.py:21-29`);
  naked-pair orders+fill+`summarize_state` flow (`:88-129`).

## 2. Assumptions & risks

- Before T1, the flat-book from-mid pair was ungated (see section 0).
- The new gate allows equality by comparing the exact Decimal sum of the
  resting prices with the exact Decimal cap; old `>=` arms stay.
- `tests/test_quotes.py` is new; the issue acceptance command targets it.
- `recorded_ts` added to the state SELECT only; `ORDER BY` and `mode=ro` stay.
- `venue_ts` may be NULL or tied — anchor order is None-last, then
  `recorded_ts`, then `trade_id`.
- Only opening BUY fills anchor; SELL fills never do.
- Anchor is read-time derived, follows current pair membership, stable across
  restarts because nothing is written.
- No storage, no registry writes, no dashboard whitelist change.
- Risk: equality / float-noise tests need `enforce_completable_pair_cost=False`
  to isolate the new gate from the completable arm.

## 3. Improvement (adopted, edge-case hardening)

The T1 gate uses `pair_cost_over_cap`, which compares exact `Decimal(str(price))`
values and `Decimal(str(cap))` without float rounding noise. Equality passes.

## 4. Tasks (2 atomic vertical slices)

### T1 (M) — entry pair-cost gate on the from-mid path — DONE (590c595)
Files: `core_brain/quotes.py`, `tests/test_quotes.py` (new). Depends on: none.
- In `_decide_quotes_from_mid`, after `out` is built: if exactly two intents
  and both have `crossed is False` and `pair_id is None`, compare the exact
  Decimal sum of `i.price` with `Decimal(str(cfg.max_pair_cost))` using
  `pair_cost_over_cap([i.price for i in out], cfg.max_pair_cost)`; if the sum is
  greater, return `[]` with reason `entry_pair_cost: combined {combined:.4f} >
  ${cap:.3f} cap -- pays exactly $1.00`. Single-intent, crossed, lifecycle
  (`pair_id` set) skip.
- Keep `hard_block`, completable gate, `dynamic_pair_sum`, sizing, legacy
  branch byte-identical.
- Tests in `tests/test_quotes.py` (fixtures follow
  `test_completable_pair_gate.py:76-88`; isolate with
  `enforce_completable_pair_cost=False` where noted): over-cap pair refused
  with the entry reason (RED before); exact tie at cap passes (2 intents);
  float-noise tie passes; balanced held inventory still gated on new-pair sum;
  lone light-side repair survives; lifecycle escalation (`pair_id` set)
  exempt. Each decision test first asserts the final intent prices.
- Verify (agent): RED pre-change, GREEN after; regressions
  `test_live_quotes.py`, `test_completable_pair_gate.py`,
  `test_trader_loop.py` green.

### T2 (S) — read-only break-even anchor in state summary — DONE
Files: `core_brain/registry_state.py`, `tests/test_quotes.py` (append).
Depends on: none (shares the new test file — append, never overwrite).
- Add pure `break_even_price(fill_price) = round(1.00 - fill_price, 4)`.
- Add `f.recorded_ts` to the fills SELECT (`registry_state.py:197-203`);
  keep `ORDER BY` and `mode=ro`.
- Per non-null `pair_id`, pick the earliest opening BUY fill ordered by
  (`venue_ts is None`, `venue_ts`, `recorded_ts or 0`, `trade_id`); set
  `pair["break_even"]` to `{filled_token_id, first_fill_trade_id,
  first_fill_price, break_even_price, opposite_token_id}` (opposite = the
  pair's other order token, else `None`); `None` when no BUY fill. Leave
  `combined_price`, `avg_price`, `naked_info` untouched. Never read the anchor
  in execution/lifecycle; no writes.
- Tests in `tests/test_quotes.py` (temp registry via `SCHEMA`, fills via
  `OrderRegistry.record_fill` + `FillRecord`, read via `summarize_state`):
  helper rounding (`0.45 -> 0.55`); first BUY anchors with correct opposite
  token; later/partial/opposite fills ignored; duplicate `trade_id` + SELL
  ignored; `venue_ts` ties broken by `recorded_ts` then `trade_id`; NULL
  `venue_ts` sorts last; no-fill/no-pair gives `None`; re-read after restart
  identical; orders/fills/`single_leg_lifecycle` rows snapshotted before/after
  the read are identical (zero mutations, zero orders/cancels).
- Verify (agent): RED before, GREEN after; regressions
  `test_registry_state.py`, `test_completable_pair_gate.py` green. Full suite
  is the GitHub CI merge gate, not a local step.

## 5. Checkpoints

- After T1: over-cap from-mid pairs refused at entry; focused suites green.
- After T2: first-fill anchor present read-only with zero side effects;
  `tasks/plan.md` committed on this branch.

## 6. NOTICED-BUT-NOT-TOUCHING

- `hard_block` / completable `>=` vs issue `<=` wording — section 0, future
  alignment issue, 2026-10-16.
- `_trim_cancelled_orders` lean dead-pair keys omit the anchor — accepted.
- `CANCEL_PRICE_MOVED` unreferenced in `core_brain/` — future cleanup.

## Files that must NOT be modified

`core_brain/risk.py`, `core_brain/order_registry.py`,
`core_brain/order_manager.py`, `core_brain/trader_loop.py`,
`core_brain/single_leg_lifecycle.py`, `core_brain/unhedged_stop_loss.py`,
`dashboard/server.py`.

## How to verify (operator, hands-on only)

- Dashboard live state after a single-leg fill: the pair carries a
  `break_even` anchor (`1.00 - first fill`); nothing cancels or exits for it.
- Shadow rehearsal `python -m core_brain.shadow_run --minutes 5 --db
  data/NN_shadow_<stamp>.db`: wide markets sit out with the entry-cap reason.
- `python -m core_brain.cancel_report <that db>`: no anchor-driven cancels.