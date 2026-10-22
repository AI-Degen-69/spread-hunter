# SPEC — #753 Retire the dead hold-lever surface left behind by #387

## Goal

Delete every declaration that still presents the re-quote queue hold and the
direction hold as working levers. #387 retired the behaviour; the names survived
in the planner signature, in `MakerConfig`, in all six tournament presets, on the
dashboard parameter cards, in the strategy explainer, in the cancel report and in
the tournament cockpit. Each survivor invites the same mistake: an operator or an
arm turns a knob that changes nothing.

## Acceptance criteria

1. `plan_orders` accepts none of `hold_queue_shares`, `hold_below_target`,
   `hedge_asks`, `hedge_held`, `queue_ahead`, `visit_outcome`. `cfg` and
   `dead_band` stay — the body reads both.
2. `MakerConfig` has neither `bot_order_requote_hold_queue_shares` nor
   `bot_order_requote_hold_below_target_cents`; no environment parsing, no bounds.
   Setting either `BOT_ORDER_REQUOTE_HOLD_*` variable — including an invalid value —
   neither changes nor breaks `load()`.
3. No `TOURNAMENT_PRESETS` entry carries either key, and no description advertises
   a queue hold. All six presets still build a valid `MakerConfig`.
4. `/api/parameters` returns no card for either setting; the strategy explainer
   lists no row for either setting.
5. `core_brain.cancel_report` prints no advice claiming the holds can keep a
   price move, and `_hold_state()` is gone. Counts, ordering, unattributed rows,
   the `price_moved` percentage (all cancels as denominator), the unreadable
   message, `--db` and exit codes 0/1 are unchanged.
6. `scripts.shadow_tournament` refuses an arm that sets either retired field name
   or either retired `BOT_*` environment name, naming the setting and #387.
7. Both committed arm files still load: the retired entries are gone from
   `configs/tournaments/i492_tournament-1_arms.json` and
   `configs/tournaments/i608_tournament-1_arms.json`, and no arm is left empty.
8. A new absence test fails if any of the above comes back.

## Edge cases

- **`cfg` is live.** Issue finding 1 calls `cfg` dead; it is not —
  `plan_orders` reads `cfg.bot_quote_size_min_shares`. Keep it; the plan records
  the correction.
- **`queue_ahead` has a second life.** The local in `_run_one_cycle` still feeds
  `cancel_queue_ahead` attribution (`trader_loop.py:2286`). Only the planner
  parameter goes; the local and `_queue_ahead_for` stay.
- **`visit_outcome` feeds a registry write.** `_note_refusal_visit` uses
  `visit_outcome.value` (`trader_loop.py:2139-2142`). The value stays computed;
  only the unused parameter goes.
- **`hedge_asks` / `hedge_held` become fully dead** once the parameter is gone —
  their only consumer was `plan_kwargs`. Their computation blocks go with it.
- **`the-gambler` / `the-chaperone` exist only to set the holds.** After the
  entries are removed those arms are empty and must be deleted, not left blank.
- **Historical cockpit cards are history, not levers.** The #485 `stickhold-1` /
  `queuehug-1` forensic cards describe runs that really happened. They stay, with
  the hypothesized tweak annotated as retired, so the record is not falsified.
  The "HYBRID Configuration Blueprint" table rows claim *current* values and go.

## Out of scope

- `docs/runs/*` (dated run records — evidence, immutable).
- The `dead_band` mechanism, the pair-cost re-gate removal history, the refusal
  hold in `quotes.py`/`risk.py` (live, unrelated).
- Re-wiring any hold to a new mechanism. #387 retired the behaviour on purpose.
