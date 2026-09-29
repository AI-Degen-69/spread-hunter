# Plan: Issue #311 — One-sided legs aged out of the rescue window are never revisited

**Branch:** `i311/one-sided-legs-aged-out-of-the-rescue-window` | **Issue:** #311

**Size tier:** Standard — five files, one architectural decision (a second,
market-end-aware discovery arm beside the window filter).
Rationale: one new pure decision helper, one new read on an existing module, two
loop call sites, one report line. No schema change, no new dependency.
**Task type:** Code + Debug (a fail-closed safety gap) + Verification.

## What the issue requires (verbatim)

- "A market-end-aware hard exit for one-sided legs that pass the rescue window
  without being closed."
- "**Do not raise `pairs_exit_window_sec` to 'fix' this.** It is a discovery
  filter; lengthening it enlarges every other exposure it gates and re-opens the
  aged-out class rather than closing it. The fix is a separate mechanism."
- "The deadline must be **market-end aware**: derived from the market own
  resolution/end time, so the leg is sold *before* settlement rather than by it.
  A wall-clock deadline alone would still fire after the market is gone."
- "Fail closed. If the end time cannot be read, the leg stays naked and the read
  is retried... Never guess a deadline."
- "The new mechanism must not change the fail-closed route order for anything
  inside the 900 s window."

Acceptance criteria: leg older than the window sold/completed before market end;
unreadable end → naked + retried, never a blind close; in-window route order
unchanged; the report's `aged_out` flag goes to zero on a fresh rehearsal of the
new build; a focused test RED before / GREEN after (known end → exited before it,
unreadable end → untouched).

## Locked constraints (issue + CONSTRAINTS.md)

- `pairs_exit_window_sec` stays 900.0 and keeps its meaning (a discovery filter).
- New knobs only: `enable_aged_out_rescue` (True), `aged_out_rescue_lead_sec`
  (900.0). Every existing config default is untouched.
- Fail closed, both directions: an end time that cannot be read ⇒ no action, the
  leg stays naked, the read is retried next rotation. Never invent a deadline.
- The in-window route order is frozen: complete → adverse-drift → hold-in-grace →
  grace-expiry. The new arm never reaches a fill whose last fill is inside the
  window.
- `should_exit()`, `single_buy_max_loss_pct` (0.10), `single_buy_max_loss_usd`
  (0.045), grace defaults, rescue route order, dynamic risk caps, `max_pair_cost`
  (0.99) and the direction gate are not modified.
- A close is still written only after a successful venue sale; a refusal is
  reported and retried, never forced.
- No new external dependencies. No test writes into live `data/` or `run/`.

## Verified seams (read verbatim in the tree; zero guesswork)

- `core_brain/single_buy_saver.py:1105-1115` — the gap itself. Inside
  `auto_manage_pairs`, the discovery filter:
  `if last_ms <= 0 or (now_s * 1000.0 - last_ms) > window_ms: continue`.
  Past the window the pair is invisible to the pass and nothing else closes it.
- `core_brain/single_buy_saver.py:1075-1092` — the close-coverage guard
  (`last_fill_ms` vs `latest_close_ms` per condition), which the new arm must
  reuse or it can re-sell an already-closed leg.
- `core_brain/single_buy_saver.py:348-395` — `load_pair` gives `condition_id`,
  `heavy`/`light` token ids and `naked` size; `SIZE_EPS` is the balance epsilon.
- `core_brain/single_buy_saver.py:588-660` — `exit_single_buy(...)`: takes
  `reason: Optional[str]`, checks venue positions first, refuses rather than
  selling unrecorded, and writes the close only after the sale.
- `core_brain/market_resolution.py:96-222` — `MarketEndState` + `parse_end_state`
  already parse `closed`, `endDate`/`end_date_iso`, `end_date_passed` and
  `unreachable`; the epoch is computed at `:162` but **not returned**.
- `core_brain/market_resolution.py:229-273` — `fetch_market_end_state` queries
  `?condition_ids={cid}&closed=true`.
- `core_brain/shadow_run.py:942` — `shadow_sweep._resolve_fn = resolve_markets_fn`:
  the existing injection precedent for a network read inside a shadow rehearsal.
- `core_brain/shadow_run.py:884-895` — the resolution sweep runs last on purpose
  ("so a market this sweep resolves is not acted on by auto_manage_pairs in the
  same rotation"). The new arm must sit **before** it, or settlement books first.
- `core_brain/order_manager.py:2317-2348` — the live call site of
  `auto_manage_pairs`: per-pair isolation, `_emit_cycle_event(phase="settling")`.
- `scripts/rescue_exit_report.py:24-27,124-127,438-448` — the AGED OUT flag is
  `method == shadow_settlement` and `close_ts - fill_ts > PAIRS_EXIT_WINDOW_SEC`;
  `RESCUE_METHODS = ("single_buy_exit", "shadow_settlement")`.
- `core_brain/config.py:919-946` — `pairs_exit_window_sec = 900.0`,
  `single_buy_grace_sec = 0.0`, the two drift thresholds.

## Probe finding that changes the design (measured, not assumed)

The obvious reuse — `fetch_market_end_state` — **cannot serve this deadline**:

```
closed=true | rows 0 -> would read unreachable          (open ATP market, 0xd65042fa…)
no filter   | rows 1 | closed False | acceptingOrders True | endDate 2026-10-06T03:00:00Z
                       | gameStartTime 2026-09-29 03:05:00+00
```

The `closed=true` filter exists to see *ended* markets, so a still-open market
comes back as an empty list, which that function classifies as `unreachable` —
i.e. every live leg would fail closed and the arm would never fire. The
unfiltered read returns exactly the fields the deadline needs (`closed`,
`acceptingOrders`, `endDate`, `gameStartTime`). So the deadline needs its own
read on the same module, not a new source of truth.

Second measured fact: for sports, the venue `endDate` is **not** the end of
trading. The probe market's `endDate` is a week out while `gameStartTime` is
hours past; #312 established the mirror case (an NFL market whose `endDate` was
kickoff). A live sports market can therefore carry an `endDate` that is already
in the past while the venue still takes orders. That case is the one the issue
measured, and it is why the deadline model below has a second branch.

## Design decision (the one architectural call)

A separate arm, `rescue_aged_out_legs`, complements the window's discovery rule
instead of changing it. Per naked pair older than the window, it reads the
venue's own state for the condition and applies, in order:

1. **End unknown** (`unreachable`, or the market is not in the venue's open
   listing) ⇒ no action. The leg stays naked and the read is retried next
   rotation. Fail closed.
2. **Venue already closed it** (`closed=True`, or not accepting orders) ⇒ no
   action; the resolution/settlement path owns it. Selling into a closed market
   is not a trade we can make.
3. **Stated end in the future** ⇒ due when `now >= end_ts - lead_sec`
   (`aged_out_rescue_lead_sec`, default 900.0). Before that, report
   `awaiting_lead` and do nothing.
4. **Stated end already passed and the venue still accepts orders** ⇒ due now.
   There is no future stated end to wait for; this is the closing phase, and it
   is the branch that covers the sports class the issue measured.
5. **Due** ⇒ try `complete_pair` first when the pair still completes under
   `max_pair_cost` (the better outcome, and the strategy's own priority: never
   assemble over $1.00, merge at parity when you can); otherwise
   `exit_single_buy(..., reason="aged_out_rescue")` at the best bid.

Reported per pair, never raised: `aged_out_rescue` (acted), `awaiting_lead`,
`end_unknown`, `venue_closed`, `balanced`, `error`.

## Interface contracts

- `aged_out_verdict(*, last_fill_ms, window_ms, now_s, end_ts, lead_sec,
  venue_closed, venue_accepting) -> tuple[str, str]` — pure, no registry, no
  clock, no network. Verdicts: `not_aged_out` | `end_unknown` | `venue_closed` |
  `awaiting_lead` | `due`; the second element is the human reason string.
- `fetch_open_market_state(gamma_host, condition_id, *, timeout=10.0, now_ts=None,
  urlopen=urllib.request.urlopen) -> Optional[MarketEndState]` — the unfiltered
  gamma read (`?condition_ids={cid}`). `None` means "not in the venue's open
  listing" (distinct from `unreachable=True`, a failed read).
- `MarketEndState` gains `end_ts: Optional[float] = None`, filled by
  `parse_end_state` from the epoch it already computes at `:162`. Additive field
  with a default: every existing constructor call and test still works.
- `rescue_aged_out_legs(client, registry, cfg, *, live=True, now=None,
  venue_positions=None, market_state_fn=None, gamma_host=DEFAULT_GAMMA_HOST)
  -> list[dict]` — one pass per rotation, per-pair isolation identical to
  `auto_manage_pairs`; `market_state_fn` is the test/rehearsal seam (defaults to
  `fetch_open_market_state`).
- Close reason string `aged_out_rescue` (the report's rescue vocabulary).

## Improvement proposal (adopted by default, one)

**Read the deadline through the module that already owns market end, and reuse
its fail-closed vocabulary — do not build a second end-date source.** Evidence,
verbatim from the issue: *"The deadline must be **market-end aware**: derived
from the market own resolution/end time, so the leg is sold before settlement
rather than by it."* and from `market_resolution.py`: *"Any failure (network,
malformed payload, empty result) returns an `unreachable` state -- the caller
degrades and retries next rotation."* Classified **simplification / edge-case
hardening**: one new read function plus one additive field on an existing
dataclass, no new dependency, no second parser, no new network surface beyond a
public GET the loop already performs. Folded into T2 below.

Rejected alternative: parsing `endDate` inside `single_buy_saver` (a second ISO
parser, a second failure vocabulary, and the `closed=true` trap above re-created
in a new place). Recorded so it does not resurface.

## CodeRabbit plan intake (costed once — nothing to read)

`gh api repos/.../issues/311/comments` returns **zero comments**: there is no
CodeRabbit plan comment on this issue, so there is nothing to adopt, reject, or
carry as `[UNVERIFIED]`. The issue body itself is unusually complete (root-cause
line, binding constraints, acceptance criteria), and every seam it names was
re-verified above.

## Task graph

T1 (pure verdict + knobs) and T2 (the open read) are independent and both are on
the critical path of T3, which cannot decide anything without them. T3 wires the
arm into both loops. T4 makes the result visible and rehearsable. There is no
task that depends on T4.

## Tasks

### T1 — The aged-out verdict, as a pure function (+ new knobs) [x]
- **Size:** S | **Domain:** Backend/Logic (safety) | **Helper:** `test-driven-development`
- **Files:** `core_brain/single_buy_saver.py`, `core_brain/config.py`,
  `tests/test_aged_out_rescue.py` (new)
- **Build:** `aged_out_verdict(...)` per the contract above, encoding every
  fail-closed rule in one place; `enable_aged_out_rescue: bool = True` and
  `aged_out_rescue_lead_sec: float = 900.0` in `MakerConfig` — new defaults only,
  with the existing env-override pattern for the lead knob.
- **Depends on:** —
- **Verify:** RED on untouched code, GREEN after, one case per branch: undated
  fill → `not_aged_out`; last fill inside the window → `not_aged_out`; unreadable
  end → `end_unknown`; venue closed → `venue_closed`; future end outside the lead
  → `awaiting_lead`; inside the lead → `due`; end passed + still accepting →
  `due`; end passed + not accepting → `venue_closed`.

### T2 — The open-market read + `end_ts` on `MarketEndState` [x]
- **Size:** S | **Domain:** Backend/Logic (fail-closed I/O) | **Helper:** `test-driven-development`
- **Files:** `core_brain/market_resolution.py`, `tests/test_market_resolution.py`
- **Build:** add `end_ts` to `MarketEndState` and fill it from the epoch already
  parsed in `parse_end_state`; add `fetch_open_market_state` (unfiltered query,
  `None` for "not in the open listing", `unreachable=True` for a failed read).
  `fetch_market_end_state` and the resolution sweep stay behaviourally untouched.
- **Depends on:** —
- **Verify:** RED first: an open row yields `end_ts` and `closed=False`; an empty
  listing yields `None`; a network failure yields `unreachable=True`; a malformed
  row yields `None`. The existing `tests/test_market_resolution.py` and
  `tests/test_market_resolution_settlement.py` stay green unmodified.

### T3 — The aged-out arm, wired into both loops [x]
- **Size:** M | **Domain:** Backend/Logic (safety) | **Helper:** `test-driven-development`
- **Files:** `core_brain/single_buy_saver.py`, `core_brain/order_manager.py`,
  `core_brain/shadow_run.py`, `tests/test_aged_out_rescue.py`
- **Build:** `rescue_aged_out_legs(...)`: discovery is the window's complement
  (dated last fill strictly older than the window), reusing the existing
  close-coverage guard so a leg already closed by merge/exit/settlement is never
  sold twice; naked legs only; `SIZE_EPS` as the balance epsilon; per-pair
  isolation and a reported reason for every pair either way. In `order_manager`
  it runs right after the pairs pass, with the same event/logging pattern
  (`action="aged_out_" + action`). In `shadow_sweep` it runs after
  `auto_manage_pairs` and **before** the resolution sweep, driven by
  `ShadowExecutionClient`, with `shadow_sweep._market_state_fn` as the injectable
  seam (same shape as `_resolve_fn`).
- **Depends on:** T1, T2
- **Verify:** RED first, on a synthetic store: (a) leg aged past the window with a
  known end inside the lead → exited before the end, close carries
  `reason="aged_out_rescue"`; (b) the same leg with an unreadable end → untouched
  and retried, no close row written; (c) a leg whose venue already closed the
  market → untouched; (d) a leg inside the window → the arm returns nothing for it
  and `tests/test_auto_pairs.py` / `tests/test_dual_stop_loss.py` stay green
  unmodified; (e) a pair that still completes under `max_pair_cost` → completed,
  not dumped at the bid. **Checkpoint:** the whole pass reported on the rehearsal
  store before T4.

### T4 — The report, and the rehearsal proof [x]
- **Size:** S | **Domain:** Reporting + Verification | **Helper:** `test-driven-development`
- **Files:** `scripts/rescue_exit_report.py`, `tests/test_rescue_exit_report.py`
- **Build:** an `aged_out_rescue` close is the success shape of this issue, so the
  report says so explicitly instead of leaving the operator to infer it from a
  missing settlement row: print the rescue line by reason, and put the count of
  `AGED OUT` settlement closes in the summary — the single number acceptance
  criterion #4 asks to go to zero.
- **Depends on:** T3
- **Verify:** RED first: a store whose only aged-out leg was rescued by
  `reason="aged_out_rescue"` reports zero aged-out settlements and prints the
  rescue; a store with a `shadow_settlement` close past the window still reports
  one. Then the hands-on rehearsal below.

## Checkpoints

1. After T1 + T2 — the pure verdict and the read are green; nothing runs yet.
2. After T3 — both loops call the arm; the pass is visible on the rehearsal store.
3. After T4 — the report's aged-out count is readable, and the rehearsal below is
   the operator's proof.

## Rehearsal evidence (status, honest)

The issue is explicit that it is "deliberately not started until a rehearsal on
the current build runs", because `closes.reason` only exists from #306 onward and
the shadow-01/02/03 stores predate it. That rehearsal is **in flight now** on the
current build: `data/05_shadow_29-09.db` (run id `shadow-04`) plus
`data/05_booktape_29-09.db`, started 06:56 for 240 minutes, dashboard 8804. It
costs nothing (no signer) and is independent of the retired 24 h boxes.

Measured at minute 5: the store is alive and cycling (~5 s per cycle) but holds
**0 fills, 0 closes, 0 quotes**, because the ranked universe was the single
decided tennis market (`mid 0.845` outside `[0.20, 0.80]` → "decided market"
skip). The ranker pass at 06:52 shows the #312 fix working end to end:
`126 tradable binaries … wrote top 1`, and `runtime/markets.json` is non-empty
with `fetch_truncated: true` on the row.

So the "before" evidence is at risk of being empty: with one unquotable market
the run may take no fills at all, and an aged-out leg requires a fill that ages
1200 s past a window. Two consequences, both stated rather than papered over:
the fix does not depend on this run (its RED/GREEN tests are synthetic and
deterministic), and criterion #4's rehearsal is on the **new** build, run after
T4. If the evidence run ends with no rescue closes, that is reported as "the
before-picture could not be measured on this universe", not as a clean bill.

## Explicitly NOT modified

`pairs_exit_window_sec` and its meaning; `should_exit()`; the drift thresholds;
grace defaults; the in-window route order; `max_pair_cost`; the dynamic risk caps
(`order_risk_pct` 25%, `naked_risk_pct` 6%, `bankroll_ceiling_pct` 90%); the
direction gate; `enable_pairs_rule`; `filter_markets.py` and the ranker;
`data/**`, `run/**`, `runtime/**`; `fetch_market_end_state`'s own semantics.

## Sub-issues

Skipped deliberately: single-owner session, one branch, four slices — the same
shape as #294, #301, #306 and #312.

## Verification & TDD summary

- New harness `tests/test_aged_out_rescue.py`: `aged_out_verdict` (T1) and the
  full pass against a synthetic store (T3), RED before each change.
- Extended in place: `tests/test_market_resolution.py` (T2),
  `tests/test_rescue_exit_report.py` (T4).
- Guard suites that must stay green unmodified: `tests/test_single_buy_saver.py`,
  `tests/test_auto_pairs.py`, `tests/test_dual_stop_loss.py`,
  `tests/test_market_resolution_settlement.py`, `tests/test_shadow_run.py`.
- Full `python -m pytest -q` stays with GitHub CI as the merge gate.
- Operator "How to verify" is hands-on only: the rehearsal on the new build, the
  dashboard at 8804, and `scripts/rescue_exit_report.py` over the two stores.

## Build notes (Station III)

Two things surfaced during the build that the plan did not anticipate, both
recorded here rather than discovered again at review:

1. **`MarketEndState` had no `accepting_orders`.** The "closing phase" branch
   (stated end passed + venue still accepting) needs the venue's acceptance
   flag, and `parse_end_state` only parsed `closed`. Added as an additive field,
   with #312's review lesson applied: a non-boolean shape ("false", 0) reads as
   unreadable rather than as false-live.
2. **Shadow attribution refused the arm's completions.**
   `shadow_exec._naked_pair_for_token` deliberately reproduced the U35 window
   when deciding which pair a completion belongs to, so every completion the
   new arm made was refused in rehearsal (`ShadowOrderRefused`) and the shadow
   run would have booked only its exits. The rule is now two-tier: the
   in-window set keeps priority (that defect is why the function exists) and
   the most recent aged-out naked pair is the fallback. The pre-existing test
   `test_a_completion_is_refused_when_every_naked_pair_is_out_of_window` pinned
   the superseded premise; it is now
   `test_a_completion_is_refused_when_no_naked_pair_exists` (same refusal,
   pinned against a balanced pair) with the change of premise stated in its
   docstring, plus a new test pinning the fallback. No assertion was weakened
   and none was deleted.

## Archived plan — Issue #312 (merged)

#312's plan and checklist were superseded here; they remain in git history at the
merge of PR #313 (`20313bb2`). Its only open item was the operator's live-window
ranker read, which the run above satisfied (`wrote top 1`).
