# Plan - #622 Keep, remove, or bound the refusal-hold (grace) quoting machinery

Branch: i622/decision-refusal-hold-keep-remove-or-bound | Issue: #622

Tier: **Large** — cross-cutting (`trader_loop`, `config`, `order_registry`, `cancel_report`),
new script, new docs, new tests, plus a multi-arm shadow tournament.
Task type: **Research (primary) + Code + Docs** — measurement-then-decide; the code changes
exist to make the measurement possible and to delete the loser.

Base: `main` @ `46daac9`, clean, synced with `origin/main`.

---

## 0. Corrected premise (read this before any task)

Commit `d7810fd` (2026-10-16, PR #623) already shipped **two previews of this issue's own
answer**, under the subjects *"feat(trading): disable refusal-hold grace by default pending
#622"* and *"feat(trading): remove mid-hold band, no drift holds pending #622"*:

- `REFUSED_HOLD_GRACE_CYCLES = 1` (`core_brain/trader_loop.py:132`) — **option A is live.**
- The `#419` mid-hold band is gone; `MID_HOLD_BAND` no longer exists.
- `plan_orders`'s `token_mids` parameter was deleted; `_cycle_mids` (`:209`) survives with
  **one caller: `tests/test_trader_loop.py:1661`**.
- `docs/explanations/grace-hold-explainer.html` was **created** (172 lines) documenting GRACE=1.

Two consequences:

1. **"Keep the default unchanged" means keep A**, not keep B. The experiment switch is built
   in the opposite direction from the CodeRabbit plan's Task 1.1: A ships on, B and C are
   opt-in tournament arms. Building it CodeRabbit's way (default B) would make the experiment
   measure B against itself and silently revert live behavior.
2. **Option C costs more than the plan says.** It is not "keep the band and add a check" — the
   band and the mid path into the planner were both removed. C must re-introduce a token-mid
   path. That cost is real and belongs in the decision (it is recorded as a cost, not as a veto).

Full claim-by-claim drift table: `SPEC.md`.

---

## 1. CodeRabbit intake (costed once, so Stations III-V never re-read the comment)

**Adopted:**

- The three-option framing and the five metrics from the issue, and CodeRabbit's mapping of
  where the refusal hold lives.
- Task 1.3's refusal-visit accounting: without a rate denominator the arms are not comparable.
- Task 2.1's metric definitions, especially **censored** time-to-fill (unfilled orders are
  observations, not zeros) and *net move ÷ total path length* for the trend/chop label.
- Task 2.2's arm hygiene: pin bankroll, tell the operator to unset `SPREAD_HUNTER_BANKROLL`
  (it takes precedence), use only the four live stop settings, never the retired
  `HUNTER_STOP_LOSS_TRIGGER_USD_CENTS`.
- Task 2.3's insistence on writing the decision rule **before** results, and its honest
  labelling of the evidence.
- Task 3.4's disposition logic for the `requote_*` hold settings (remove if display-only).
- Its rejection of a replay harness — correct and independently verified (`run_shadow` does
  not forward `traded_fn`).

**Rejected:**

- **Task 1.1's switch direction** and its "keep B as the default" premise. Inverted; see §0.
- **Task 1.1's "use the existing path that carries token mids"** — that parameter was deleted
  by `d7810fd`. T2 re-introduces it deliberately and gives `_cycle_mids` a real consumer.
- **Task 1.2's "keep the three-cycle expiry for option C"** as written, because its premise
  (band still active) is false. C keeps the cycle count; there is no band to bypass.
- **Task 3.2's "Keep the mid-hold band. It still covers `not_quoted` and lifecycle
  replacement."** There is no band to keep — `_near_front`/`_market_arriving` are uncalled.
  Dispositioned in T9, not "kept".
- **The file citations that do not resolve** — `tests/test_plan_orders_mid_hold.py` (absent),
  `strategy.md:168-174` (file is 170 lines). Not carried forward.
- **Task 3.1's "expiry cancel reason is `hold_expired`"** as a *cancel-reason* correction.
  `hold_expired` is the lifecycle-stop attribution; the cancel reason is `not_quoted`. The
  record must state both correctly.

**Left `[UNVERIFIED]` (cannot be checked from repository data):**

- The `shadow-02` churn claim, "23 of 25 cancels at 37s median life". The store is not in the
  repo. It is cited as the operator's motivation, never as a measured fact.

---

## 2. Dependency graph

```
T1 (baseline truth + decision rule)
 └─> T2 (policy switch, default A)
      └─> T3 (refusal-visit events)
           └─> T5 (comparison report) ──> T6 (arms file + protocol) ──> T7 (run + record)
T4 (nullable cancel timestamp) ──┘                                          │
                                                                            v
                                                                    T8 (decide + delete)
                                                                     └─> T9 (dead hold machinery)
                                                                      └─> T10 (operator verification)
```

T4 is independent of T1-T3 and can run any time before T5.

---

## 3. Tasks

### [x] T1 — Baseline truth and the decision rule, written before any result exists

- Size: **S** · Domain: **Research** · Depends on: none
- Files: `docs/runs/2026-10-16-622-refusal-hold-tournament.md` (new),
  `tests/test_refusal_hold_decision.py` (new)
- Build:
  1. Assert in a test — not a grep, so it cannot rot — that option A is the shipped baseline:
     `REFUSED_HOLD_GRACE_CYCLES == 1`, and `_near_front` / `_market_arriving` have **no
     callers in `core_brain/`**. This is the guard that makes §0 true forever.
  2. Write the run record with the **decision rule first**, before any measurement:
     *prefer A (simplest, and already proven live); choose B or C only if repeated runs show a
     clearly lower stop-out rate or churn without a clearly worse trend fill rate.* State the
     metric thresholds that count as "clearly" while the rule is still blind to results.
  3. Record in the run record: the evidence label, the shadow fill-model limitation, and the
     six factual corrections from `SPEC.md`.
- Verify: `python -m pytest -q tests/test_refusal_hold_decision.py`
- **Checkpoint 1** — one-line report: baseline confirmed, decision rule frozen.

### [x] T2 — Policy switch and the drift path, default A

- Size: **M** · Domain: **Backend/Logic** · Depends on: T1
- Files: `core_brain/config.py`, `core_brain/trader_loop.py`, `tests/test_trader_loop.py`
- Build:
  - `core_brain/config.py`: add `refusal_hold_policy` (`remove` | `keep` | `drift`, default
    `remove`) and `refusal_hold_drift` (dollars, default `0.02`). Env overrides
    `HUNTER_REFUSAL_HOLD_POLICY` / `HUNTER_REFUSAL_HOLD_DRIFT`, following the existing block
    near `:1388-1410`. An unknown policy value **raises** with the choice list (mirror
    `STOP_LOSS_MODES` at `:1462-1470`) — a typo must not silently pin an arm to the baseline.
  - `core_brain/trader_loop.py`: plumb policy + tolerance through `_visit_one`
    (`refused_streak` is at `:1719`) into `plan_orders`, and **re-introduce the
    `token_mids` keyword** removed by `d7810fd`, fed by `_cycle_mids` (`:209`) so that
    helper regains a production consumer. Do not read the settings as globals inside
    `plan_orders`.
  - `keep` → the pre-`d7810fd` behavior: 3 consecutive refusals before expiry. Make the count
    a function of the policy rather than re-hardcoding 3 in two places.
  - `drift` → store each streak's **first-refusal token mid** beside `refused_streaks`
    (`:674`, reset at `:819-820`), compare every refused order's current mid against it
    **two-sided**, and cancel on `|Δmid| > tolerance` through a new `VisitOutcome` + its own
    cancel reason. Keep the cycle count. A missing/untrustworthy mid ⇒ **no drift**.
  - Terminal refusal, lifecycle overrides, refused-escalation preserve, and dropped-market
    cleanup are untouched.
- Verify: `python -m pytest -q tests/test_trader_loop.py tests/test_cancel_attribution.py`
  plus new tests: A holds indefinitely across many refusals; B still expires on the third;
  C holds inside tolerance, cancels outside it on the **second** refusal, holds on a missing
  mid, and is two-sided; the per-visit hold→hold→expire sequence; streak restart after expiry.

### [x] T3 — Refusal visits as the rate denominator

- Size: **S** · Domain: **Backend/Logic** · Depends on: T2
- Files: `core_brain/order_registry.py` (only if needed), `core_brain/trader_loop.py`
- Build: first **check** whether refused visits already reach a store via `market_events`.
  If they do not, write **one `market_events` row per refused visit** carrying the outcome
  (transient / terminal / expired / drift) and the streak count, through the existing
  event-writing helper. **No new table.** Record in `tasks/plan.md` which of the two it was.
- Verify: focused test asserting one row per refused visit with a parseable outcome.

### [x] T4 — Nullable cancel timestamp

- Size: **S** · Domain: **Backend/Logic** · Depends on: none
- Files: `core_brain/order_registry.py`
- Build: `orders` has `posted_ts` and `last_polled_ts` but **no cancel timestamp**, so median
  cancelled-order life is not computable today. Add nullable `cancelled_ts INTEGER` using the
  existing additive-migration pattern at `:481-482` (`if "<col>" not in cols: ALTER TABLE ...
  ADD COLUMN`), and populate it at the cancel write site. Existing rows stay `NULL` and every
  reader must tolerate that.
- Verify: migration is idempotent on a legacy DB, and a fresh cancel stamps the column.
- **Checkpoint 2** — one-line report: switch + events + cancel timestamp working; report next.

### [ ] T5 — Read-only comparison report

- Size: **L** · Domain: **Backend/Logic** · Depends on: T3, T4
- Files: `scripts/refusal_hold_report.py` (new), `tests/test_refusal_hold_report.py` (new)
- Build: one script that opens each arm's store **read-only** and prints the five metrics side
  by side. Scope **every** query by run id,following `read_arm_results` in
    `scripts/shadow_tournament.py`.
  - **Churn:** cancels per posted order grouped by reason (reuse the aggregation in
    `core_brain/cancel_report.py:summarize`); cancel-then-post **replacements on the same
    token** per market; **median cancelled-order life** from the T4 column, printing the `NULL`
    count.
  - **Stop-out rate:** lifecycle stop closes ÷ filled legs, via `closes.method` and
    `core_brain/single_leg_lifecycle.py`.
  - **Time-to-fill:** `orders.posted_ts` → first fill, after `cancelled_ts` censoring; use a
    simple survival estimate so unfilled orders count, and **print the censored count**.
  - **Trend vs chop:** label each market window from the **shared book tape** (one stream for
    all arms, so labels cannot depend on policy) using *net move ÷ total path length* against
    a **fixed cutoff written into the run record**; report fill rate per label.
  - **Stop slippage:** `trigger_bid − fresh_bid`, and `trigger_bid − proceeds/shares`; print
    field-coverage counts, since `closes.trigger_bid`/`fresh_bid` are nullable
    (`order_registry.py:303-304`).
  - **Run checks:** heartbeat start/end and completed cycles, code revision + dirty state, and
    the T3 refusal-visit counts.
- Verify: `python -m pytest -q tests/test_refusal_hold_report.py` — fixture DBs covering
  run-id scoping, censoring, `NULL` bid fields, hand-made trend/chop series, and drift cancels
  counted under their own reason.

### [ ] T6 — Arms file and run protocol

- Size: **S** · Domain: **Research/Config** · Depends on: T2, T5
- Files: `configs/tournaments/i622_tournament-1_arms.json` (new)
- Build: three arms differing **only** in refusal policy (plus C's tolerance): `remove`
  (control = today), `keep`, `drift`. Pin `HUNTER_BANKROLL` identically in all three and tell
  the operator to unset `SPREAD_HUNTER_BANKROLL` (it takes precedence). Hold the four live stop
  settings constant — `HUNTER_STOP_LOSS_MODE`, `HUNTER_DROP_CENTS`, `HUNTER_DRAWDOWN_PCT`,
  `HUNTER_EMERGENCY_DRAWDOWN_PCT` — and do **not** carry the retired
  `HUNTER_STOP_LOSS_TRIGGER_USD_CENTS` from the i492 presets. Sanity-check that the arms'
  `env` keys survive the tournament's parent-env stripping (#624).
- Protocol (recorded in `docs/runs/`): agent runs `--dry-run` and checks the plan → **operator**
  launches → run `scripts/book_tape_recorder.py` against a **dedicated** database → repeat
  several times, **rotating arm start order** (workers start sequentially) → save market lists,
  logs, heartbeats, stores.
- Verify: `--dry-run` prints exactly three arms with the intended env deltas.

### [ ] T7 — Run and record  *(operator-launched; blocks T8–T10)*

- Size: **M** · Domain: **Research** · Depends on: T6
- Files: `docs/runs/2026-10-16-622-refusal-hold-tournament.md`
- Build: **operator** launches the tournament; the agent runs the report per run and appends
  real output — no hand-written numbers. Label every published result **"approximately
  concurrent shadow evidence"**, state that arms do not see identical books, state the
  strictly-below shadow fill rule and the absent queue simulation, and note that the
  `shadow-02` claim is unverifiable from repo data.
- Verify: the record contains a non-empty table for all five metrics for all three arms.
- **Checkpoint 3** — one-line report: runs complete, metrics in hand; the decision is next.

### [ ] T8 — Record the decision, keep the winner, delete the losers

- Size: **M** · Domain: **Docs + Backend/Logic** · Depends on: T7
- Files: `docs/agents/strategy.md`, `docs/explanations/grace-hold-explainer.html`,
  `core_brain/trader_loop.py`, `core_brain/config.py`, `tests/test_trader_loop.py`,
  `tests/test_shadow_run.py`
- Build:
  1. Post the chosen option, the metrics, the rationale, and the run-record link on #622;
     apply the six factual corrections from `SPEC.md`.
  2. **A wins:** remove `REFUSED_HOLD_GRACE_CYCLES`, `REFUSED_GRACE_EXPIRED`, the drift
     outcome, `refused_streaks`, reference mids, the `refused_streak` parameter, the visit
     result's `held` flag, and the `[RESTING]`/`[HOLDING]` branches that exist only for it.
     **B wins:** keep the cycle machinery; change the constant only if the runs support it;
     remove the drift outcome, reference mids, and the drift reason. **C wins:** keep the cycle
     count and the drift check; make the tolerance permanent; remove A's branch.
  3. Delete the temporary policy setting; add `HUNTER_REFUSAL_HOLD_POLICY` (and the drift var
     unless C won) to the retired **warn-and-ignore** list at `config.py:1490-1520`, so a stale
     arms file warns instead of silently running the baseline. A drift tolerance that is still
     consumed by code is **not** retired — it is policy, not a leftover.
  4. Rewrite `docs/agents/strategy.md:168-170` to describe the shipped policy, and refresh
     `docs/explanations/grace-hold-explainer.html` to the interim→final state. Fix the
     inaccurate re-quote text if the winner makes it wrong; otherwise leave `:151-164` alone
     (the dead band is live and is not this issue's target).
  5. Update tests for deleted behaviour — `tests/test_trader_loop.py:~1552-1641` and
     `~1905-1935`, `tests/test_shadow_run.py:~1711-1723` — keeping every test that still
     describes the winner. Name each deletion and its justifying option in this file.
- Verify: focused `trader_loop` / `shadow_run` suites green.

### [ ] T9 — Sweep the dead hold machinery actually present

- Size: **S** · Domain: **Backend/Logic** · Depends on: T8
- Files: `core_brain/trader_loop.py`, `core_brain/config.py`, `core_brain/cancel_report.py`,
  `tests/test_trader_loop.py`
- Build:
  1. `_near_front` (`:374`) and `_market_arriving` (`:386`) are **uncalled** — delete them and
     the `hold_queue_shares` / `hold_below_target` plumbing that only fed them, unless the
     winner needs it. This is the "no dead hold machinery left behind" criterion applied to the
     code that actually exists (the CodeRabbit plan looked for a band that was already gone).
  2. Re-check `_cycle_mids`: if the winner does not consume it, either delete it and its
     `live_marks.py:14` cross-reference, or keep it **with** a reader. Do not leave it
     test-only.
  3. Search every `requote_hold_queue_shares` / `requote_hold_below_target` /
     `requote_dead_band` reader outside `config.py`. `requote_dead_band` is **live** — leave it.
     The other two: if only `cancel_report.py` displays them, remove the fields, their presets,
     their env overrides (to the retired list), and the display; if a live reader exists, write
     a `noticed-but-not-touching` row **instead** and record it here.
- Verify: focused suites green; a search proving zero uncalled hold helpers remain.

### [ ] T10 — Operator verification

- Size: **XS** · Domain: **Docs** · Depends on: T9
- Files: the How-to-verify block in the PR body / final report
- Build: write **hands-on only** steps (per `AGENTS.md` and `docs/agents/verifying.md`) for the
  shipped policy. No `pytest`, no `gh`, no `git status`.
- Verify: the operator can exercise it.

---

## 4. Checkpoints

| # | After | Shows |
|---|---|---|
| 1 | T1 | Baseline confirmed as A; decision rule frozen before any result exists |
| 2 | T4 | Policy switch, refusal-visit events, and cancel timestamp working |
| 3 | T7 | Three arms run; all five metrics present for each |
| 4 | T9 | Winner shipped; no dead hold machinery left; tree clean |

In `/iii-build-plan auto` each checkpoint is a one-line progress report, not an approval pause.

---

## 5. Improvement proposal (Step 5)

**Proposal — adopt by default (correction, not scope expansion):** build the switch so it
**defaults to A**, because A is already the shipped behavior.

Evidence, verbatim from the repo:

> `core_brain/trader_loop.py:126-132` — *"Interim default (operator directive 2026-10-16,
> pending #622): 1, i.e. the hold is OFF -- the first transient refusal already expires into a
> cancel, so quotes rest until fill / terminal refusal / stop exit. Restore to 3 if #622 keeps
> the hold."*

> commit `d7810fd` — *"feat(trading): disable refusal-hold grace by default pending #622"*

The CodeRabbit plan's Task 1.1 instead sets the default to `keep` and says "preserve current
behavior until Task 3.1 is complete". Taken literally that **reverts live behavior before the
measurement it is meant to justify**, and it makes the control arm measure B against B. The
correct reading of "no behavior changes in this issue" is: nothing changes until the decision
is recorded — and what is live today is A.

**Recorded rejection:** adding a replay harness so all arms read one recorded stream. Rejected
because `run_shadow` does not forward `traded_fn` and the tape lacks event-level data; it is a
separate project, and rotating repeated concurrent runs is adequate for this decision.

---

## 6. Build notes — deviations from this plan (reconciled, not silent)

- **T2 — the drift decision travels, not the prices.** This plan said to
  re-introduce `plan_orders(token_mids=...)` fed by `_cycle_mids`. Implemented
  instead as `plan_orders(drifted_token_ids=...)`: `_visit_one` owns the books and
  the streak's reference snapshot, so it computes *which tokens drifted* and hands
  the planner that verdict. `plan_orders` stays a pure function of what it is
  given — no prices, no tolerance, no streak state — which is what makes the
  cancel path directly testable. `_cycle_mids` still regains a production
  consumer (`_visit_one`), and T1's premise-lock assertion that `plan_orders`
  takes no `token_mids` stays true, so the baseline facts the record publishes
  are unchanged.
- **T2 — two constants, not one.** `REFUSED_HOLD_GRACE_CYCLES` stays `1` (the
  `remove` policy's effective limit), and `REFUSED_HOLD_GRACE_CYCLES_HELD = 3`
  carries the hold count for `keep`/`drift`. Renaming the shipped constant to 3
  and deriving `remove` from it would have changed what every existing grace test
  reads and silently rewritten the baseline the run record publishes. This way
  the default policy is byte-for-byte today's behavior and no existing assertion
  moved.
- **T2 — the drift boundary is rounded to the tick grid.** Found by a test that
  failed on `0.62 - 0.60 == 0.020000000000000018`: a move of exactly the tolerance
  read as drift. Both sides now round to 4 decimals before comparing, matching
  `_market_arriving`'s existing rounding rule for the same reason.
- **T3 — the answer to "do refused visits already reach a store?" is NO, so the
  rows had to be added.** `_note_lifecycle_stop` returns early when the stop is
  `None`, and `_visit_stop` returns `None` for a transient reason, so a HELD
  refusal wrote nothing anywhere. Under `keep`/`drift` that is most refusals.
  Added `kind="refusal_visit"` rows through the existing `log_market_event`
  helper — no new table, no migration — with the outcome in `reason_code`
  (`refused_hold` / `refused_grace_expired` / `refused_cancel` / `refused_drift`)
  and the streak ordinal in `size`, the table's only free numeric column.
- **T3 — the test harness pins `base_cfg`, so env overrides are invisible to it.**
  `_t3_seam` passes `base_cfg=MakerConfig()`, which means `run()` never calls
  `load()` and a `HUNTER_REFUSAL_HOLD_POLICY` override is silently ignored. The
  new tests set the policy on the config object instead. This is worth knowing
  for T5/T6: **only an arm process that lets `run()` call `load()` will honour
  the arms file**, and the tournament launches real processes, so the arms path
  is fine — but a test that relies on env here would measure the baseline twice.

## 7. Environment caveats and assumptions

- **`tasks/plan.md` and `tasks/todo.md` were empty** at branch time (closed-issue artifacts are
  pruned, e.g. `#624`, `#608`), so this plan is written fresh rather than reconciled. Nothing
  was overwritten.
- `REFUSED_HOLD_GRACE_CYCLES` is a **module constant, not a config field**, and has no env
  override today. T2 is what makes it arm-selectable; until then no tournament arm can vary it.
- `docs/agents/strategy.md` is **170 lines**, so any citation to `:168-174` in the CodeRabbit
  plan cannot resolve; `:168-170` is the mid-hold-band removal note.
- `scripts/book_tape_recorder.py` and `scripts/grace_sweep_report.py` both exist, and
  `tests/test_grace_sweep_report.py` exists — those two citations do resolve.
