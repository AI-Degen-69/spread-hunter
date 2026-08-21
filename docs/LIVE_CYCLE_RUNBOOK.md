# Live Cycle Runbook — Supervised $100 Fleet

**Phase:** live (Owner-approved 2026-08-21). The fleet (`live/engine/live_fleet.py`)
quotes real money on a real account: $100 starting capital, minimal risk per
trade, supervised by the Owner. This is the opposite of the simulation phase —
orders are **sent**, not simulated. See AGENTS.md → Safety.

**Hard limits (enforced in code, not by this runbook):**

| Limit | Value | Where |
| --- | --- | --- |
| Max order | $25 | `engine/venue.py` `MAX_ORDER_USD` |
| Max total exposure | $100 | `engine/venue.py` `MAX_TOTAL_USD` |
| Per-couple sizing | max(bankroll × 1%, $6) ≈ $6, $3/leg | `engine/config.py` `couple_risk_frac` / `min_couple_usd` |
| Pairs | held to resolution (U35 pass may complete/exit in-window, §4) | strategy rule + §4 |
| Budget above $100 | **not approved** — waits on closing stages | AGENTS.md Safety |

---

## 1. Pre-flight checklist

Run every check **before** starting the cycle. Do not skip steps because the
last cycle went fine.

1. **Credentials** — `live/.env` (or repo root `.env`) must have:
   - `POLY_PRIVATE_KEY` (or `POLY_KEY`) — the funded wallet's key
   - `POLY_FUNDER` — the address that holds the $100
   - optional but recommended: `POLY_API_KEY` + `POLY_API_SECRET` +
     `POLY_API_PASSPHRASE` (pre-derived L2 creds — skips the
     rate-limit-sensitive derivation call entirely)
   - `CLOB_HOST` only if not the default `https://clob.polymarket.com`
   - Confirm `.env` is in `.gitignore` before pasting anything.
2. **Market feed exists** — `run/markets.json` must be present (the fleet idles
   with a warning if not). If missing:
   ```bash
   python scripts/rank_markets.py
   ```
3. **Balance is actually $100** — read-only, safe to run anytime:
   ```bash
   cd live && python -m engine.live_exec balance
   ```
   If the collateral is not ~$100, **stop here** and request funding (see §7).
4. **Registry is the one you think it is** — the fleet writes `live/run/live.db`
   unless `--db` says otherwise. Never point two processes at the same db
   (AGENTS.md: run one instance at a time).
5. **Read-only sanity check**:
   ```bash
   cd live && python -m engine.live_exec status
   ```
   Confirms the client authenticates and open-order state matches the registry.
6. **Dashboard up** for watching (separate terminal):
   ```bash
   cd live && python -m dash.live_dash      # then open http://localhost:8799
   ```

## 2. Starting the supervised cycle

First do a **single rotation** (smoke test) — it places real orders, so watch
it, then decide:

```bash
cd live
python -m engine.live_fleet --live --once --max-markets 3
```

- `--once` — one reconcile → decide → submit/cancel → sweep rotation, then exit.
- `--max-markets 3` — limit the first rotation to three graduated markets.
- Without `--live`, nothing is sent (dry run). Dry-run with credentials still
  runs reconcile/sweep (read-only).

When the smoke rotation looks right, start the supervised loop:

```bash
python -m engine.live_fleet --live --interval 5
```

- `--interval 5` — one rotation every 5 seconds (default).
- `--max-markets N` — cap the rotation (default: all graduated markets).
- `--funder <addr>` — override `POLY_FUNDER` for this run.
- `--no-reconcile` / `--no-sweep` — when the poll loop owns reconcile/sweep
  (and the automatic naked-leg exit, §4) in a separate process; otherwise
  leave them on.

**To get the automatic naked-leg exit (§4), run poll alongside the fleet** —
the fleet alone never fires it. The dashboard's Start Bot launches both; by
hand, run them as two processes:

```bash
cd live
python -m engine.live_exec poll --interval 5        # reconcile + sweep + auto-pairs
python -m engine.live_fleet --live --no-reconcile --no-sweep --interval 5
```

The **poll supervises the guardrail watcher** (`live/scripts/guardrail_watch.py`)
as a child process: it launches with the poll, is restarted if it dies
(throttled to one restart per 30s so a crash-loop cannot spin), and is
terminated when the poll stops — so the repeat-exit and over-cap alerts are
on whenever the bot is. No third process to start by hand. Disable with
`--no-watch-guardrails` on the poll (e.g. to run the watcher standalone for
debugging).

## 3. Position watch (the Owner's job — this is supervision)

While the loop runs, watch both:

1. **Polymarket's own interface** — the account the wallet funds: resting
   orders, fills, balances. This is the ground truth.
2. **The local dashboard** (`http://localhost:8799`) — the registry's view:
   pairs, inventory balance, hedge state, telemetry ring.

Ad-hoc checks (safe anytime):

```bash
cd live
python -m engine.live_exec pairs          # registry pairs + exposure
python -m engine.live_exec status         # auth + open-order summary
python -m engine.live_exec balance        # venue collateral
```

**Watch for:** a one-sided fill (one leg filled, the other resting). That is a
naked leg — it rides unhedged to resolution. It is within the approved risk
(≤ $25). **In-window naked legs are handled automatically** by the U35 pass
(§4); the state that still needs your eyes is a naked leg *outside* the
15-minute window (left alone by design) and any `error` lines in the poll
output.

## 4. Automatic naked-leg exit (the U35 pass)

The poll loop automatically resolves in-window one-sided fills — the sim's
proven guardrail, ported to the live tree. It runs in `poll` right after
reconcile, every cycle, and only sends when poll has a real (authenticated)
client. **It does not run in the fleet process** — see §2 for the two-process
invocation.

**Knobs** (all in `engine/config.py`, defaults shown):

| Knob | Default | Meaning |
| --- | --- | --- |
| `enable_pairs_rule` | `true` | master switch |
| `pairs_exit_window_sec` | `900` (15 min) | only pairs whose last fill is inside the window are touched |
| `max_pair_cost` | `0.995` | complete/exit routing threshold |
| `HUNTER_PAIRS_RULE=off` (env) | — | runtime kill switch — set it to disable the pass without a config edit |

**Routing, per in-window naked pair** (economics measured in U35):
- **Under `max_pair_cost` (0.995)** → **complete** — cross the light side at
  ask to finish the pair (+3.68c/share captured; acting late forfeits it).
- **At/over the cap, or no ask on the light side** → **exit** — sell the
  filled leg at best bid, capped at bid depth (−3.67c floor; the pair is a
  guaranteed loss, so it is closed, not completed).
- **Balanced or already closed** → skipped.
- **Older than the window** → left alone. Acting late loses money (measured
  +0.09c at 5m vs −18.5c at 1h) — the window is where action pays.

**Safety:** positions are read once per cycle and each exit fails closed on
position divergence; one pair's failure never stops the cycle. All actions
are closing-only (pre-approved under the direction gate). A dry-run mode
returns `would_complete` / `would_exit` with zero sends.

**Watch in the logs:** `[POLL ...] pairs <id> completed|exited|error`. An
`error` means the pair was skipped, not force-closed — read the message.

## 5. Stop conditions

**Clean stop (normal):**
- **Ctrl-C / SIGTERM** — the loop stops between cycles. Everything already
  resting stays resting; nothing is force-cancelled.
- **`--once`** — exits after one rotation.

**Stop now (any of these):**
- Balance on Polymarket's interface drops below what you expect, or below the
  point where the strategy's sizing assumptions hold.
- Repeated venue errors in the loop output (a degraded connection is not a
  reason to keep sending).
- A naked leg appears and you are not actively watching it.
- The dashboard shows exposure you did not intend.
- You say stop. That is sufficient.

**Closing orders (all pre-approved, reduce exposure only):**
```bash
cd live
python -m engine.live_exec exit <pair_id>       # cancel resting leg + sell the filled one
python -m engine.live_exec complete <pair_id>   # second-leg completion
python -m engine.live_exec cancel-all           # pull every resting order, no sells
python -m engine.live_exec cancel <order_id>    # one order
```
The automatic pass (§4) only touches in-window pairs — these manual verbs
still exist for out-of-window naked legs and anything you want to close by
hand.
After resolution:
```bash
python -m engine.live_exec redeem               # gasless, through the relayer
```

**Never:** edit `MAX_ORDER_USD` / `MAX_TOTAL_USD` "just for this order." They
are the difference between a POC and an unbounded loss (venue.py comment).

## 6. What "supervised" means here

- The Owner is present for the run, watching Polymarket's interface.
- One instance of the fleet, one registry.
- **Unattended operation is not approved.** In-window second-leg completion
  and stop-loss now run automatically (§4), but fully unattended running, and
  any budget above the $100, still wait on the closing stages (1–4) — see
  `SESSION-66-BRIEF.md` §5.

## 7. Funding rule

- **Funding is requested before the cycle, never during.**
- Tell the Owner (you) the amount and the reason ahead of time. Do not
  discover mid-cycle that the wallet is short.
- The approved budget is **$100**. If the account is not at $100 before the
  cycle, stop and ask for the top-up first.
- Any request above $100 is out of scope until the closing stages land.
