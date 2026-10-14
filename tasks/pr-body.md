@coderabbitai summary

Closes #505. Build tasks #549 (T1), #550 (T2), #551 (T3).

## Why

The rotation visits markets in feed order, so a held position can wait a full pass before the stop-loss rules look at it again. On Polymarket there is no native stop order, so detection latency is the stop-loss. This change visits held markets first each rotation: naked, then balanced, then flat quoting-only.

## Test output

```
python -m pytest -q tests/test_trader_loop.py tests/test_rotation_priority.py
148 passed in 2.41s
```

GitHub CI runs the full `python -m pytest -q` regression suite on Ubuntu and Windows; local full-suite output is not required at the review/PR stations. The focused suites plus the shadow and caller suites (`test_shadow_run`, `test_empty_markets_routine`, `test_instance_lock`, `test_run_attribution`, `test_queue_clear_gate`) are green locally: 333 passed. Every new order test was confirmed to fail while the loop iterated in feed order.

## How to verify

1. Fresh feed plus fresh shadow store, spends nothing: `python -m scripts.filter_markets ; python -m core_brain.shadow_run --minutes 10 --run-id shadow-verify --db data\NN_shadow_<stamp>.db` — the run starts and rotates.
2. Dashboard: `python -m dashboard.server --db data\NN_shadow_<stamp>.db --port 8799` — open http://127.0.0.1:8799 — the badge reads SHADOW.
3. Wait until the dashboard shows at least one NAKED or BALANCED pair.
4. Read the Cycle feed, or `runtime/shadow-verify.jsonl`: in each rotation where that market still holds exposure, its entry comes before every flat market entry. Failure: a flat market comes first while the market is still held. A market that went flat correctly returns to its feed position — that is not a failure. If the entries do not identify markets in visit order, report "insufficient evidence" (do not use `runtime/markets.json` as visit order).
5. Already proven shortcut: `runtime/shadow-smoke505.jsonl` from the build smoke shows the promotion directly (cycles 1-7 feed order, cycle 8 onward the held market first).

## What this speeds up

Time from rotation start to each held market visit, which is when its risk work runs: fresh book reads, quote-risk checks, single-leg lifecycle escalation (adverse drift at least twice the base offset), hedge planning, and hard-stop quote suppression and cancellation (held bid 0.15 or below). Reconciliation brings new fills in at rotation start, so this shorter delay is the whole win.

## What this does not speed up

The gap between two visits to the same market is still one full rotation plus the sleep. The shadow management sweep, ladder `_route_pair` exits, and the live risk guardian run on their own schedules, not in the visit list.

## Corrections the ticket text needed

The ordinary single-leg stop-loss runs in each visit through the unified lifecycle check, not in `_route_pair` (ladder-stamped pairs only). `single_buy_grace_sec` defaults to `0.0`, and $0.045 is a per-share price difference, not a dollar loss. The per-visit lifecycle check only sees markets with pair-id-linked open orders.

## Known limits

A market that becomes held is promoted at the start of the next rotation. A market that goes flat returns to its feed position the same way. A failed inventory read keeps the last known rank, and the visit itself still runs and reports the error exactly as before. Flat markets run last every rotation; interleaving is a follow-up that changes only the helper.

Live rehearsal evidence (one-minute shadow smoke, `runtime/shadow-smoke505.jsonl`): a market that acquired a naked leg mid-run (`wta-paolini-bartunk-2026-10-13`, up 0 down 5) moved to first visit position in the very next rotation and stayed there.

## Verification

Targeted suites green locally (see Test output). Full regression runs in GitHub CI on Ubuntu and Windows as the merge gate. No browser gate applies: this change has no visual or interactive surface.
