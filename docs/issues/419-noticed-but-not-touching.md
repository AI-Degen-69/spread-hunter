# 419 — noticed but not touching

| ID | candidate (one line) | discovering station | evidence (path:line) | status | resolution |
|----|----------------------|---------------------|----------------------|--------|------------|
| N1 | The `[HOLDING]` visit log only fires on transient-refusal holds, so a grace-expiry mid-hold logs as `[RESTING]` ("resting at target spread") — log text slightly off, no behavior impact | III | `core_brain/trader_loop.py` held-flag near the submit logging | dismissed | Operator: RESTING is the right word — an order waiting for its fill is resting; grace expiry changes nothing about the order, so the log stands as is |
