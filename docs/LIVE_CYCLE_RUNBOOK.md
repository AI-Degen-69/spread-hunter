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
| Pairs | held to resolution | strategy rule |
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
   If the collateral is not ~$100, **stop here** and request funding (see §6).
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
- `--no-reconcile` / `--no-sweep` — only when the poll loop owns those passes
  (`python -m engine.live_exec poll`) in a separate process; otherwise leave
  them on.

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
(≤ $25), but it is exactly the state that needs eyes.

## 4. Stop conditions

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
After resolution:
```bash
python -m engine.live_exec redeem               # gasless, through the relayer
```

**Never:** edit `MAX_ORDER_USD` / `MAX_TOTAL_USD` "just for this order." They
are the difference between a POC and an unbounded loss (venue.py comment).

## 5. What "supervised" means here

- The Owner is present for the run, watching Polymarket's interface.
- One instance of the fleet, one registry.
- **Unattended operation is not approved.** Fully unattended running, and any
  budget above the $100, wait on the closing stages (1–4: fill detection,
  merge, stop-loss, second-leg completion) — see `SESSION-66-BRIEF.md` §5.

## 6. Funding rule

- **Funding is requested before the cycle, never during.**
- Tell the Owner (you) the amount and the reason ahead of time. Do not
  discover mid-cycle that the wallet is short.
- The approved budget is **$100**. If the account is not at $100 before the
  cycle, stop and ask for the top-up first.
- Any request above $100 is out of scope until the closing stages land.
