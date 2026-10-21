# 2026-10-21 — #487 first 10-arm tournament from the current setup

Parent issue: #487. Arms: one control at today's configuration + nine arms that each move
one live setting.
Status: **prepared and validated** (dry-run). The 12-hour run is the operator's to launch.

## Why this run exists

The last ten-arm run (#608, 2026-10-19 03:51 → 15:51) was not a measurement:

- **Four of its ten arms carried an empty `env`** — the same run four times, so eight of its
  arms carried no comparative weight.
- The knobs its remaining arms named (`stop_loss_trigger_usd_cents`, `single_buy_grace_sec`)
  had already been **retired** as inert by #619/#622.
- It ran on code that predates **every 2026-10-20 gate fix** (#708 12:51Z, #686 13:28Z,
  #687 16:01Z, #646 16:23Z), and the config vocabulary it used was canonicalised the same
  day (#734, #739).

So a tournament has never run on the current code. That is the gap this run closes.

## Arms

Every arm below starts from the shipped defaults plus the operator's `.env` and changes
**exactly one** setting. The env lives in
`configs/tournaments/i487_current_setup_10arm_arms.json`; the prose lives in the cockpit.

| # | Arm | Port | One changed setting | Current → arm | Motive |
| --- | --- | --- | --- | --- | --- |
| 1 | `control` | 8801 | *(none)* | — | The baseline every other arm is measured against |
| 2 | `offset-mult-025` | 8802 | `BOT_QUOTE_SPREAD_OFFSET_MULTIPLIER` | 0.50 → 0.25 | Track a quiet book more closely and rest nearer mid |
| 3 | `offset-min-2c` | 8803 | `BOT_QUOTE_SPREAD_OFFSET_MIN_CENTS` | 1 → 2 | Stop the offset collapsing onto mid in calm markets |
| 4 | `offset-max-3c` | 8804 | `BOT_QUOTE_SPREAD_OFFSET_MAX_CENTS` | 20 → 3 | Stop the offset running away in volatile books |
| 5 | `offset-static` | 8805 | `BOT_QUOTE_GATE_DYNAMIC_OFFSET_ON` | on → off | Test whether the 2¢ static offset beats volatility tracking |
| 6 | `deadband-6c` | 8806 | `BOT_ORDER_REQUOTE_DEAD_BAND_CENTS` | 0.03 → 0.06 | Stop small jitter churning orders to the back of the queue |
| 7 | `widen-on` | 8807 | `BOT_QUOTE_GATE_WIDEN_ON` | off → on | Can the WIDENED-gate snap-back help, or does quoting tighter in a losing market hurt? |
| 8 | `price-risk-030` | 8808 | `BOT_QUOTE_PRICE_RISK_WIDEN` | 0.010 → 0.030 | Demand more offset where a share costs more; deepens **every** quote |
| 9 | `stop-adverse-5c` | 8809 | `BOT_RISK_STOP_MODE` | `NONE` → `ABSOLUTE_DROP` | Cap single-leg losses instead of letting a naked leg run to settlement |
| 10 | `book-window-4c` | 8810 | `BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID` | 15¢ → 4¢ | Quote only the tightest books; the one arm that changes *which* markets quote |

Arms 2–6 take their value from the live `#484` preset table. Arms 7–10 move four levers no
preset ships at a different value, so "fetched from the presets" cannot apply to them — each
one's description in the cockpit says so.

Two notes the arms themselves carry, stated here rather than hidden:

- **Arm 9 changes one variable and two behaviours.** The single-leg stop is off by default;
  switching the mode on also brings the shipped `0.05` adverse-drop threshold into play.
- **Arm 7 can quote TIGHTER, not wider, in a losing market.** `offset_for` returns the 3.5¢
  widen offset once a market is flagged WIDENED, and 3.5¢ is tighter than the dynamic offset
  can run. That is the lever, not a bug.

## Deviation from the plan (Station III, operator-approved 2026-10-21)

The plan's Decision 3 named `BOT_ORDER_REQUOTE_HOLD_QUEUE_SHARES` (arm 7) and
`BOT_ORDER_REQUOTE_HOLD_BELOW_TARGET_CENTS` (arm 8). **Both knobs were retired by #387**
(`053cb2c`, "never re-check resting orders on cost gate"):

- `plan_orders`' docstring says so (`core_brain/trader_loop.py:265-274`), and an AST walk of
  its executable body shows neither parameter is read.
- The production call site never passes them (`core_brain/trader_loop.py:2143-2151`).
- A runtime A/B through `plan_orders` with the two knobs at `0.0` / `500.0` / `0.08` returns
  byte-identical output.

Those two arms would therefore have run **exactly the control** — #608's defect again — at a
cost of ~220 MB and two of the run's twelve hours. The operator approved a substitution to two
provably live levers, `BOT_QUOTE_GATE_WIDEN_ON` (`core_brain/quotes.py:188`) and
`BOT_QUOTE_PRICE_RISK_WIDEN` (`core_brain/risk.py:262`), which is what the table above lists.

A candidate third lever, `BOT_QUOTE_SPREAD_OFFSET_MAX_AGE_SEC`, was rejected: set below the
filter's 600 s cadence it falls back to the same 2¢ static offset arm 5 already runs, so it
would have overlapped arm 5 by roughly 90 %.

`SPEC.md` and `tasks/plan.md` still carry Station II's original two rows; this section is the
record of the difference. The dead knobs themselves are logged as repo-level follow-ups in
`docs/issues/487-noticed-but-not-touching.md` (N1–N3).

## Launch (operator)

```
python -m scripts.shadow_tournament --issue 487 --arms-file configs/tournaments/i487_current_setup_10arm_arms.json --minutes 720 --stamp current_setup_10arm --dashboards
```

One 720-minute (12-hour) window, ten arms, shadow only — no signer, nothing reaches the venue.

**The issue's own code gate, as it stands today.** Its blocking comment named the order
#708 → #686 → #687 → #646 → #659 → #471. All five code tickets are **closed** (the four table
rows on 2026-10-20, which is why the arms above can be read off today's vocabulary at all).
**#471 is still open** — it collects ≥60 live closes for the statistical GO gate, so it gates
the *verdict* this run feeds, not the preparation or the launch. The `🚧 Blocked` label on the
issue is stale; removing it is the operator's call.

Artifacts:

- `runtime/tournaments/487_current_setup_10arm.json` — plan record, with `arms_source` and
  every arm's env
- `runtime/tournaments/487_current_setup_10arm.log` — launcher log
- `data/487_tournament_<NN>_<arm>_current_setup_10arm.db` × 10 — one store per arm
- `reports/tournaments/487_current_setup_10arm_results.json` — per-arm summary at the end

**Storage: budget ~1.1 GB** (10 arms × ~110 MB of shadow store). That is the existing per-arm
size, not a new cost, but worth knowing before the run starts.

The launcher refuses to start if a target store already exists, which is what stops a second
launch from appending to a finished arm. Move or delete the ten stores to re-run the window.

## What to watch

- **Dashboards on 8801–8810**, one per arm, in the arm order above.
- **The cockpit on 8800** — `python -m scripts.tournament_cockpit_server`, then
  <http://127.0.0.1:8800/487>. Before the run it shows ten rows with their hypotheses and
  `NO_DATA`; once the record exists it reads each arm's real store, run id and port from
  `runtime/tournaments/487_current_setup_10arm.json`.

## What the comparison must report — after the run

Per arm: fills, merges, single-leg exits and realized PnL, plus the hard-stop audit — every
`lifecycle_hard_stop` close must carry `trigger_bid` and `fresh_bid` agreeing within the
two-cent sell slippage, and a wider gap is a reopened defect rather than a result.

Read it from the cockpit and `reports/tournaments/487_current_setup_10arm_results.json`
**after** the window closes; nothing is declared from a partial run.

**No production winner may be chosen from modelled shadow results.** Shadow fills are modelled
against a book snapshot; a configuration that wins here is a candidate for a live trial, not a
decision.

## How to verify this preparation (operator)

1. `python -m scripts.tournament_cockpit_server` → open <http://127.0.0.1:8800/487> → the table
   lists **ten** rows, each with a hypothesis, and every metric column reads `NO_DATA`.
2. Dry-run the launch: add `--dry-run` to the command above → it prints ten arms on ports
   8801–8810 with ten distinct `data/487_tournament_...` stores and run ids, names
   `runtime/tournaments/487_current_setup_10arm.json` as the record, and **starts nothing** —
   no dashboard, no arm, no signer.
