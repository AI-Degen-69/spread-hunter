# Plan - #626 Purge instantaneous taker-ask re-gating on resting maker bids

Branch: i626/purge-instantaneous-taker-ask-re-gating | Issue: #626

Tier: **Small** — live behavior already correct (`NO RE-CHECK (#387)`); remaining
work is a dead-constant purge plus pinning tests and one stale-doc fix.

Base: `main` @ `5cdd75b`, clean. Related: #622 (done), #627 (entry), #628.

## 0. Corrected premise (read before any task)

The issue text describes a live per-cycle cancel (`Our Bid + Opposing Ask >=
$1.00`). That code no longer exists. `plan_orders`
(`core_brain/trader_loop.py:379-384`) says verbatim:

```
# NO RE-CHECK (#387): a placed order was judged once, at placement
# (rest price + rest price). Re-testing it against the live hedge ask
# cancels profitable pairs on book flicker, so nothing here re-checks
# cost. Only a token with no intent this cycle is cancelled.
```

`CANCEL_REGATE_PAIR_COST` (`trader_loop.py:88`) is defined but never referenced
in `core_brain/` — only in `tests/test_cancel_attribution.py` (lines 26, 292,
300, 427, 443). This ticket deletes dead scaffolding, pins resting behavior, and
fixes the one doc still promising a per-second re-gate. Entry gates stay:
`dynamic_pair_sum` (`quotes.py:335-344`) and `hard_block` (`risk.py:413-568`)
judge NEW bids only (owner: #627).

"Completely removed" cannot include the stored string: `cancel_report.py:21,26`
keeps `"regate_pair_cost"` so old rows still report correctly.

## 1. Interfaces (verified verbatim)

- `CANCEL_NOT_QUOTED = "not_quoted"` (`trader_loop.py:86`) — ONLY cancel reason
  on the live path (line 375). Stays.
- `CANCEL_PRICE_MOVED = "price_moved"` (`trader_loop.py:87`) — defined,
  unreferenced in `core_brain/`. NOTICED-BUT-NOT-TOUCHING.
- `CANCEL_REGATE_PAIR_COST = "regate_pair_cost"` (`trader_loop.py:88`) — DELETE.
- `plan_orders(open_orders, intents, price_eps=1e-9, *, dead_band, cfg,
  hedge_asks, hedge_held, reasons, ...)` (`trader_loop.py:211-229`) — UNCHANGED.
- `REASON_ORDER` / `REASON_BLURB["regate_pair_cost"]` (`cancel_report.py:21,26`)
  — UNCHANGED (historical reporting).
- Must stay green: `test_held_pair_keeps_both_legs_and_needs_no_resubmit`,
  `test_single_held_leg_stays_without_submit`,
  `test_a_failing_gate_on_a_wanted_token_records_nothing`,
  `TestReGateRespectsHeldInventory`, `TestVisitOnePassesTheDeadBand`.

## 2. Assumptions & risks

- Per-cycle resting re-gate already inert (#387); no live path changes behavior.
- Constant is dead in `core_brain/` (tests only) — deletion is safe.
- Stored `"regate_pair_cost"` strings must stay for old-row readability.
- `quotes.py`/`risk.py` pair-cost checks are entry-only; touching them breaks #627
  scope — explicitly excluded.
- Risk: `strategy_explainer.html:450-452,466-469` still promises a "1s Re-Gate";
  fixed in T3 below.

## 3. Improvement (adopted, simplification)

Evidence (verbatim, `strategy_explainer.html:466-469`): "Kept orders are re-tested
against `risk.completable_pair_block` **every single second**." Contradicts the
shipped `NO RE-CHECK (#387)` planner. Fold the diagram/copy fix into T3 — same
behavior, same review surface (one static file).

## 4. Tasks (3 atomic vertical slices)

### T1 (S) — delete dead constant + trim retired docstring ✅
Files: `core_brain/trader_loop.py`. Depends on: none.
- Delete line 88 (`CANCEL_REGATE_PAIR_COST`). Keep `CANCEL_NOT_QUOTED`,
  `CANCEL_PRICE_MOVED`, UMA reasons.
- Trim `plan_orders` docstring `RETIRED BY #387` paragraphs to one short
  historical note; keep the live `HOLD-AND-WAIT` paragraph. No logic change.
- Verify: `rg CANCEL_REGATE_PAIR_COST core_brain` → no matches; new test
  `test_retired_regate_constant_is_gone` asserts `not hasattr(trader_loop,
  "CANCEL_REGATE_PAIR_COST")` (RED before, GREEN after).

### T2 (M) — fixtures to literal + pin resting bids ✅
Files: `tests/test_cancel_attribution.py`, `tests/test_trader_loop.py`.
Depends on: T1.
- Attribution file: drop the import (line 26); use literal `"regate_pair_cost"`
  + one-line historical comment at lines 292, 300, 427, 443.
- New tests: (a) `test_resting_bids_survive_ask_rise` — pair at 0.73/0.23,
  hedge asks spiked, assert `to_cancel == []`, `to_submit == []`, `reasons ==
  {}`; (b) `test_resting_bid_survives_retired_regate_math` — resting 0.60 +
  ask 0.60 = 1.20 with matching intent, assert held, no reason, no duplicate;
  (c) `test_planner_never_records_regate_reason` — hostile `hedge_asks` x each
  `VisitOutcome`, assert `"regate_pair_cost"` never in `reasons`.
- Verify: `python -m pytest -q tests/test_trader_loop.py
  tests/test_cancel_attribution.py` green; each new test fails if a cost
  re-check is reintroduced.

### T3 (XS) — fix stale re-gate diagram
Files: `dashboard/static/strategy_explainer.html`. Depends on: T1.
- Replace `REGATE` diamond (lines 450-452) with KEEP node (wanted token → KEEP
  at own price; unwanted → `not_quoted` cancel). Rewrite the "1.0s Cycle
  Re-Gating" subcard (lines 466-469): judged once at placement, never re-tested
  vs live hedge ask; entry gates judge new bids only.
- Verify: `rg -i "re-gate|regate" dashboard/static/strategy_explainer.html` →
  no matches; open page, confirm no "CANCEL immediately" branch.

## 5. Checkpoints

- After T1: constant gone from `core_brain/`, focused suite green.
- After T2: resting survival pinned, focused suite green.
- After T3: docs match shipped behavior; commit plan on this branch.

## 6. NOTICED-BUT-NOT-TOUCHING

- `CANCEL_PRICE_MOVED` unreferenced in `core_brain/` — future cleanup, 2026-10-16.
- Refused fresh-entry ask → indirect `not_quoted` cancel — owner #622/#627.
- `hedge_asks`/`hedge_held` plumbing now unused but kept (signature freeze).

## How to verify (operator, hands-on only)

- Open `dashboard/static/strategy_explainer.html`: quoting diagram shows KEEP for
  wanted tokens, no "1s Re-Gate / CANCEL immediately" branch.
- Shadow rehearsal `python -m core_brain.shadow_run --minutes 5 --db
  data/NN_shadow_<stamp>.db` then `python -m core_brain.cancel_report <that db>`:
  no new `regate_pair_cost` rows while bids rest across ask moves.

