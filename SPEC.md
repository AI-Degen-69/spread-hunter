# SPEC.md — Issue #840: Live execution lane audit and reachability verification

Branch: `i840/live-execution-lane-audit` | Issue: #840

## 1. Goal

Prove, in writing plus tests plus one bounded venue check, that the live
execution lane behaves identically to the shadow loop wherever it should, and
that CLOB signing, order acceptance, and cancellation work — before any live
capital deployment. No unconstrained live trading loop is part of this issue.

## 2. Non-goals (explicit out of scope)

- Running unsupervised / unconstrained live trading loops.
- Re-architecting the CLOB client or changing dynamic capital caps.
- Fixing the unexplained divergences found by the audit (trader-loop rollback,
  cancel-response handling, launcher preset gap, telemetry gap). Those are
  recorded as follow-up tickets only.
- Rewriting or deleting `data/orders.db` (production registry — read-only).

## 3. Ticket corrections (verified against the codebase)

1. The venue adapter is `core_brain/venue.py`. `core_brain/polymarket_client.py`
   does not exist.
2. Shadow stores are explicit, separate database files (never the production
   registry) — not "ephemeral" stores.
3. A resting post-only bid placed 15¢ below the best bid can still fill if the
   market moves. Fill probability is **not** 0.0% — it is small but non-zero,
   which is why the check carries a $2 exposure ceiling and undo steps.
4. `cancel-all` / `cancel-market` cancel unrelated orders. The check must
   cancel by exact order ID; `cancel-market` is only a fallback when no ID was
   returned (pre-flight already proved zero other account orders on the market).
5. `balance` shows the CLOB collateral balance + allowance only. It does not
   prove on-chain conditional-token approval (`is_approved_for_all`), which
   selling/merging need.

## 4. Champion profile (authoritative definition)

The champion is the `prudent` tournament preset plus five per-field
environment overrides (loader order: preset first, per-field env wins):

```bash
BOT_QUOTE_TOURNAMENT_PRESET=prudent
BOT_ORDER_REQUOTE_DEAD_BAND_CENTS=0.06
BOT_RISK_STOP_MODE=ABSOLUTE_DROP
BOT_RISK_STOP_ADVERSE_DROP_CENTS=0.045
BOT_ORDER_GATE_QUEUE_CLEAR_ON=1
BOT_ORDER_QUEUE_MAX_CLEAR_MIN=3
```

Semantics (verified in `core_brain/config.py`):

- `prudent` = dynamic offset ON, multiplier 0.60, clamp 2¢–4¢. It carries **no**
  deadband, no stop, no queue gate of its own — those come from the overrides.
- The `_cents` fields hold **dollar** values (no division by 100).
- The stop drop is inert unless stop mode is `ABSOLUTE_DROP` (shipped default
  mode is `NONE`).
- The queue gate only records unless `BOT_ORDER_GATE_QUEUE_CLEAR_ON=1`.
- The 6¢ requote deadband and the 6¢ offset ceiling (tournament arm) are
  different settings — do not conflate them.
- Retired settings nobody may use: `stop_loss_trigger_usd_cents`,
  `single_buy_grace_sec`, the old `usd`/`pct` stop settings, `HUNTER_*` aliases.

## 5. Acceptance criteria

- [ ] `docs/live-lane-audit.md` exists: every live/shadow divergence split into
      "planned simulation choice" vs "unexplained (follow-up ticket)".
- [ ] `tests/test_champion_lane_parity.py` proves `config.load()` (live) and
      `shadow_run.shadow_cfg()` (shadow) load identical champion settings.
- [ ] `status` and `balance` exit non-zero on any failed venue read; exit 0
      means the venue was reached. Normal stdout unchanged; `decide` untouched.
- [ ] New `reach-probe` command: one small post-only GTC buy ≥15¢ below best
      bid, hold 2–4s (default 3s), cancel by exact ID, PASS only on
      accepted + ID in `canceled` + matched size 0 + absent from open orders +
      accept-to-receipt ≤ 5s. Fixed $2 exposure ceiling, not raisable from CLI.
- [ ] `reach-probe --no-live` prints the full plan + worst-case exposure and
      never signs.
- [ ] JSON evidence file under the `runtime/` directory (git-ignored): no keys,
      credentials, signatures, or raw responses; shortened wallet address.
- [ ] `docs/agents/safety.md` lists `reach-probe --live` as an
      opening-exposure command (session approval + undo steps).
- [ ] Regression suites green:
      `tests/test_shadow_guard.py tests/test_order_manager.py tests/test_order_manager_decide.py`
      plus the new parity file and the suites listed in `tasks/plan.md`.

## 6. Open question (resolved into the plan)

- *Test market for the reachability probe?* → Operator may pass a condition ID;
  without one the probe auto-selects: graduated markets passing pre-flight,
  ranked by dollar value of the top three fresh bid levels from a book read at
  check time. File order in `runtime/markets.json` is NOT liquidity order.
