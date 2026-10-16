# Todo - #622

Branch: i622/decision-refusal-hold-keep-remove-or-bound | Issue: #622

## Phase 0 — ground truth
- [x] T1 · Confirm A is the shipped baseline (`GRACE_CYCLES == 1`; hold helpers uncalled) — as a test, not a grep
- [x] T1 · Write `docs/runs/2026-10-16-622-refusal-hold-tournament.md` with the decision rule FIRST
- [x] T1 · Record the evidence label, the shadow fill-model limit, and the six factual corrections
- [x] **Checkpoint 1** — met

## Phase 1 — make the policy switchable without changing the default
- [x] T2 · Add `refusal_hold_policy` (default `remove`) + `refusal_hold_drift` with `HUNTER_*` overrides; unknown value raises
- [x] T2 · Plumb policy + tolerance into `_visit_one` / `plan_orders` (implemented as `drifted_token_ids`, see plan §6)
- [x] T2 · `keep` branch restores 3-cycle hold; `drift` branch cancels two-sided beyond tolerance, no drift on missing mid
- [x] T2 · Tests: A indefinite hold, B third-cycle expiry, C in/out of tolerance, missing mid, hold→hold→expire, streak restart
- [x] T3 · Refusal visits reach a store — they did NOT; rows added via `log_market_event` (one `market_events` row per refused visit, or confirmed already there)
- [x] T4 · Nullable `orders.cancelled_ts` via the additive-migration pattern; populated at the cancel site
- [x] **Checkpoint 2** — met

## Phase 2 — measure
- [x] T5 · `scripts/refusal_hold_report.py`: churn, stop-out rate, censored time-to-fill, trend-vs-chop fill rate, stop slippage, run checks
- [x] T5 · `tests/test_refusal_hold_report.py`: run-id scoping, censoring, NULL bid fields, trend/chop series, drift cancels
- [x] T6 · `configs/tournaments/i622_tournament-1_arms.json`: 3 arms, policy-only, pinned bankroll + 4 live stop settings
- [x] T6 · Agent runs `--dry-run` only; protocol written (dedicated tape DB, rotated order, repeated runs)
- [x] T7 · **Operator** formal decision: Option A (TOTAL PURGE of refusal-hold / grace machinery)
- [x] **Checkpoint 3** — met (operator directive recorded)

## Phase 3 — decide and simplify
- [x] T8 · Operator chose Option A (TOTAL PURGE); record decision
- [x] T8 · Delete losing paths and all refusal-hold / grace machinery
- [x] T8 · Delete the temporary switch; retire its env names into the warn-and-ignore list
- [x] T8 · Update `strategy.md:168-171` and `docs/explanations/grace-hold-explainer.html`
- [x] T8 · Update/remove tests for deleted behaviour (`test_refusal_hold_policy.py` purged, `test_trader_loop.py` cleaned)
- [x] T9 · Delete uncalled hold helpers (`_near_front`, `_market_arriving`) and dead streak trackers
- [x] T9 · Delete `_cycle_mids` and its `live_marks.py` cross-reference
- [x] T9 · Disposition hold settings (`requote_dead_band` live; others noted in `docs/issues/622-noticed-but-not-touching.md`)
- [x] **Checkpoint 4** — met (Option A shipped; zero hold tombstones; green focused tests)
- [x] T10 · Write the hands-on operator verification steps

## Out-of-scope rows
- [x] Every discovered out-of-scope defect lands in `docs/issues/622-noticed-but-not-touching.md` with owner + date
