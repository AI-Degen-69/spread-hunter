# PR — Visit held positions first each rotation (#505)

Closes #505. Tasks #549 (T1), #550 (T2), #551 (T3).

## What changed

Each rotation now visits markets by filled exposure — naked first, then balanced, then flat
quoting-only — via one pure helper (`core_brain/rotation_priority.py::order_by_exposure`)
applied to the shared `trader_loop.run` visit list. The ordering snapshot is the `Inventory`
every visit already reads, recorded under `_cid(spec).lower()`; there are no new database or
venue reads, no new seam port, and no shadow-specific code. Live and shadow both inherit it.

## What this speeds up

Time from **rotation start to each held market's visit**, which is when its risk work runs:
fresh book reads, quote-risk checks, single-leg lifecycle escalation (adverse drift ≥ 2× base
offset), hedge planning, and hard-stop quote suppression and cancellation (held bid ≤ 0.15).
Reconciliation brings new fills in at rotation start, so this shorter delay is the whole win.

## What this does NOT speed up

- The gap between two visits to the same market (C6) — still one full rotation plus the sleep.
  A fast price-watch loop would shorten that; deliberately out of scope.
- The shadow management sweep, ladder `_route_pair` exits, and the live risk guardian (C2, C4)
  — they run on their own schedules, not in the visit list.

## Corrections the ticket text needed (verified against the code)

- C1: ordinary single-leg stop-loss runs **in each visit** through the unified lifecycle check
  (`core_brain/single_leg_lifecycle.py`), not in `_route_pair` (ladder-stamped pairs only).
- C3: `single_buy_grace_sec` defaults to `0.0` (tests pass 45 explicitly); $0.045 is a
  per-share price difference, not a dollar loss.
- C5: the per-visit lifecycle check only sees markets with `pair_id`-linked open orders; a
  held market with none gets no lifecycle check no matter how early it is visited.

## Known limits (honest)

- **One-rotation promotion lag:** a market that becomes held is promoted at the start of the
  *next* rotation (the snapshot is recorded by visits). A market that goes flat returns to its
  feed position the same way. A failed inventory read keeps the last known rank — the visit
  itself still runs and reports the error exactly as before.
- **Delay bound:** the wait from rotation start to the k-th held market's visit is at most
  (remaining previous-rotation work) + (sleep) + (next rotation-start work) + (visits to
  earlier held markets) + (the target's own visit). With the snapshot, the last term is `k`
  held visits, not `k + number of flat markets` as before.
- Strict order it is: flat markets run last every rotation (the ticket's default assumption).
  If quote staleness ever becomes a problem, interleaving is a follow-up that changes only the
  helper.

## Tests

Focused suites cover the touched code (agent-run): `tests/test_rotation_priority.py` (10
purity/rank/stability tests) and `tests/test_trader_loop.py` (6 loop order/delay/invariance
tests, all pre-existing tests unchanged), plus the shadow and caller suites. Every new order
test was confirmed to fail while the loop iterated in feed order. Full regression is CI's job.

Live rehearsal evidence (1-minute shadow smoke, `runtime/shadow-smoke505.jsonl`): a market
that acquired a naked leg mid-run (`wta-paolini-bartunk-2026-10-13`, `up=0 down=5`) moved to
first visit position in the very next rotation and stayed there — feed order in cycles 1–7,
held-first from cycle 8.

## How to verify (operator, hands-on)

1. Fresh feed + fresh shadow store, spend nothing:
   `python -m scripts.filter_markets ; python -m core_brain.shadow_run --minutes 10 --run-id shadow-verify --db data\NN_shadow_<stamp>.db`
   → the run starts and rotates.
2. Dashboard: `python -m dashboard.server --db data\NN_shadow_<stamp>.db --port 8799` →
   open http://127.0.0.1:8799 → the badge reads SHADOW.
3. Wait until the dashboard shows at least one NAKED or BALANCED pair.
4. Read the Cycle feed, or `runtime/shadow-verify.jsonl`: in each rotation after that market
   was first seen as held, its entry comes **before every flat market's entry**. Failure: a
   flat market comes first. If the entries do not identify markets in visit order, report
   "insufficient evidence" (do not use `runtime/markets.json` as visit order).
5. Already-proven shortcut: `runtime/shadow-smoke505.jsonl` from the build smoke shows the
   promotion directly (cycles 1–7 feed order, cycle 8+ the held market first).
