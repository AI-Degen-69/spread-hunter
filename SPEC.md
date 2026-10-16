# SPEC - #622: Keep, remove, or bound the refusal-hold (grace) quoting machinery

Branch: i622/decision-refusal-hold-keep-remove-or-bound | Issue: #622

## Authority

The issue body defines the three options and the five required metrics. Where the
issue body and `coderabbitai`'s plan comment disagree with live code, **live code wins**
(this station verified both; see "Corrected premise" below).

## Corrected premise (verified against HEAD 46daac9, 2026-10-16)

The issue body and the CodeRabbit plan both describe a baseline that no longer exists.
Commit `d7810fd` (2026-10-16, PR #623) landed two previews of this issue's own answer,
under the subjects *"feat(trading): disable refusal-hold grace by default pending #622"*
and *"feat(trading): remove mid-hold band, no drift holds pending #622"*.

| Claim (issue body or CodeRabbit plan) | Live code |
|---|---|
| `REFUSED_HOLD_GRACE_CYCLES = 3` (`:128`) — "the live one" | **`= 1`** at `core_brain/trader_loop.py:132`. The hold is **OFF**; the first transient refusal already expires into a cancel |
| `MID_HOLD_BAND = 0.02` at `:109-133` | **No such constant.** Removed by `d7810fd` |
| "the #419 mid-hold band covers the order" / "Keep the mid-hold band" | **Band removed.** `_near_front` (`:374`) and `_market_arriving` (`:386`) are **defined but never called** — dead code. `docs/agents/strategy.md:168` records the removal |
| "Use the existing path that carries token mids" (into `plan_orders`) | The **`token_mids` parameter was deleted** by `d7810fd`. `_cycle_mids` (`:209`) survives; its only caller is `tests/test_trader_loop.py:1661` |
| "The expiry cancel reason is `hold_expired`" | The cancel reason recorded is **`not_quoted`** (`CANCEL_NOT_QUOTED`, `:430`/`:433`). `hold_expired` is the **lifecycle-stop attribution** (`core_brain/market_lifecycle.py:33`, `LifecycleStop.HOLD_EXPIRED`) |
| "`docs/explanations/grace-hold-explainer.html` does not exist" | **It exists** — added by `d7810fd`, 172 lines, and documents the interim GRACE=1 state |
| "`tests/test_plan_orders_mid_hold.py:104-117`" | **File does not exist.** Only `tests/test_plan_orders_asymmetric_hold.py` |
| "policy documentation is at `docs/agents/strategy.md:168-174`" | The file is **170 lines**. It carries the mid-hold-band removal note only; there is no refusal-hold policy text |
| "`closes` has nullable `trigger_bid`/`fresh_bid`" | **Correct** — `core_brain/order_registry.py:303-304` |
| "Add a nullable cancel-time column if none exists" | **Correct** — `orders` has `posted_ts`, `last_polled_ts`, `cancel_reason`, `cancel_queue_ahead`; **no cancel timestamp exists** |

**The consequence that shapes the whole plan:** option A is already the shipped default.
"The default must not change until the decision is recorded" therefore means **keep A**,
not keep B. The experiment switch must be built in the **opposite direction** from the
CodeRabbit plan's Task 1.1 — A ships on, and B and C are opt-in arms.

## Goal

Decide, on shadow evidence, which of three refusal-hold policies the quoting loop ships —
A (remove), B (keep the cycle-limited hold), C (bound the hold by price drift) — record the
rationale on #622, and leave **no dead hold machinery** behind under whichever option wins.

## Acceptance criteria

The issue's three criteria, made measurable:

1. **A/B/C compared on shadow data** with all five metrics present and non-empty in the
   report output for every arm: cancel/replace churn rate, stop-out rate, median
   time-to-fill (censored), fill rate in trend vs chop, stop slippage
   (`trigger_bid − fresh_bid` and `trigger_bid − proceeds/shares`).
2. **One option chosen and the rationale recorded on #622**, linked to the run record,
   with every corrected fact from the table above reflected in it.
3. **Losing paths removed.** Under any winner: no reachable policy selector, no unused
   `VisitOutcome` member, no unused cancel reason, and no uncalled hold helper left in
   `core_brain/trader_loop.py`. `python -m pytest -q` stays green (CI is the merge gate).

## Edge cases the implementation must pin

- `REFUSED_TERMINAL` (named stop) still cancels **immediately, with zero hold**, on every policy.
- Lifecycle overrides (`preserve_order_ids` / `replace_order_ids` / `cancel_order_ids`) and
  the refused-escalation path are untouched by the switch.
- Dropped-market cleanup (`_cancel_dropped_markets`) is untouched.
- A **missing or untrustworthy mid** (book absent, one-sided, non-numeric, crossed) under
  option C means **no drift detected** → hold, matching the old band's missing-mid rule.
- Drift is **two-sided**: the mid moving either way by more than tolerance counts.
- The refusal streak resets on any non-held visit, and on expiry.
- `data/orders.db` (the production registry) is **never opened or rewritten** by the
  tournament, the report, or any test. Every arm and the book-tape recorder get their own DB.

## Explicit out-of-scope

- **Building a replay harness.** `run_shadow` does not forward `traded_fn` and the tape
  lacks event-level data; that is a separate project. Concurrent arms with rotated start
  order are the accepted instrument, and the evidence must be labelled as approximate
  because of it.
- **Retuning unrelated knobs.** Re-quote dead band, bankroll, the four live stop settings,
  and market selection are held constant across arms.
- **Live-money validation.** No signer, no `quote`, no `complete`, no Trader loop.
- **Creating a new explainer HTML.** `docs/explanations/grace-hold-explainer.html` exists
  and gets *updated* to the decided policy, not replaced.
