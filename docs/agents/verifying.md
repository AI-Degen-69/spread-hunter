# Verifying a change

Every change ships with two proofs. Both are required before reporting the work done.

> **Owner Standing Directive 2026-08-29:** For the Owner, the automated proof is
> background-only and is never "meat to check." The Owner has seen green tests while the
> feature remained buggy, so a passed suite proves nothing to him. The only verification
> that matters is hands-on surface behavior (§2). Never instruct the Owner to run `pytest`
> or present test counts as evidence — run them silently yourself.

## 1. Automated — run it yourself

During implementation, run the focused test matching the changed behavior through the
RED/GREEN/REFACTOR loop. After review fixes, rerun the focused checks covering the touched
code, for example:

```bash
python -m pytest -q tests/test_<module>.py
```

Paste the real targeted-test output, not a summary of it. The full `python -m pytest -q`
regression suite runs in GitHub CI on Ubuntu and Windows and is the merge gate; do not repeat
that full suite locally during the review/PR stations. **The agent runs these checks internally
and reports the result; it must never prompt or suggest the operator to run the test suite.**

Sizing, fill attribution, and merge paths always land with a test.

There is no coverage gate in this repo, and no coverage tooling is installed. The bar is
"a test that fails without the change", not a percentage.

## 2. Manual — write it out for the operator

End the work with a short **"How to verify"** block the operator can follow without
reading the code. Pick the cheapest route that actually proves the change. The block must
steer the operator to exercise the change hands-on — launch the script/stack, open the UI,
click through, observe — never to `pytest`, `gh`, `git`, or confirming GitHub/CI state the
operator can already see on their own.

- **Terminal, read-only:** the exact command plus the line to look for.
  Example: `python -m core_brain.order_manager status` → the `open_notional` row reads `$0.00`.
- **Dashboard:** the click path and the value that should differ.
  Example: start `python -m dashboard.server`, open `http://127.0.0.1:8799`, the
  **Trader** card → poll cadence reads `0.5s`.
- **Live, with real money:** a change to quoting, filling or merging is only proven when a
  real order behaves. Say so, and give the smallest test that settles it: one pair at the
  venue minimum, inside `MAX_ORDER_USD` / `MAX_TOTAL_USD`, on a graduated market from
  `runtime/markets.json`.

## Rules for the block

1. Name the file or screen, one expected value per step, five steps or fewer.
2. Say what a **failed** check looks like, not only a passing one.
3. Every live step carries its undo on the next line, and the undo has to match what
   actually happened:
   - **Nothing filled:** `cancel`, `cancel-market` or `cancel-all` pulls the resting
     orders. This does *not* close a leg that already filled.
   - **One leg filled:** `complete <pair_id> --live` buys the missing side; if it refuses,
     `exit <pair_id> --live` sells the leg you are holding.
   - **Both legs filled:** `merge <condition_id>` turns the pair back into USDC.
   - Then confirm: the market is no longer `NAKED` on the dashboard and holds no
     single-buy shares.
4. State the money at risk in dollars before the first live step.
5. The operator runs the order-placing commands. Agents run read-only and closing
   commands, and may run an opening command only when the operator says so in that
   session.
6. No verification/CI/reporting commands. Never use `pytest`, `gh issue view`, `gh pr view`, `git`, or
   "confirm merged / closed / green" as a step. The block proves the change by having the
   operator launch, open, click, and observe — not by re-reading state they already see.
