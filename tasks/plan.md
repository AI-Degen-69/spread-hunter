# Plan — Issue #293: Enforce symmetric share sizes across UP and DOWN paired quotes

Branch: `i293/enforce-symmetric-share-sizes-across-up-and-down` | Issue: #293

## Classification
Size: **Small** — one gate function plus tests; no new files, no schema, API, or dependency change. Type: **Code** (bug fix) (`[Backend/Logic]`; skill `test-driven-development`).

## Spec (concise — embedded; root SPEC.md is product-level, issue spec lives here)
**Problem:** `_decide_quotes_from_mid` sizes each leg independently (`quotes.py:407-411`: `size = int(ladder * band.size_mult * truncated)` per side). Price skew plus per-side band taper and `int()` truncation diverge the two legs (e.g. 9 UP vs 10 DOWN), leaving a naked surplus that cannot merge and trips the dashboard `Partial` flag.
**Approach:** after both intents exist, clamp flat-inventory UP+DOWN pairs to `min(up.size, down.size)` with a reason note; if the common size falls below `cfg.min_quote_shares`, drop both legs with an informative reason instead of resting half a pair. Deficit-rebalancing quotes on unbalanced inventory are deliberately asymmetric and stay untouched.
**Out of scope:** venue post/cancel (`order_manager.py`), UI rendering (`app.js`), rest-under-ask path (fixed `cfg.quote_shares`, already symmetric — verified at `quotes.py:599-603`).

## CodeRabbit plan intake
Zero comments on the issue at plan time — nothing adopted, nothing rejected, nothing `[UNVERIFIED]` from CodeRabbit. All issue file:line pointers spot-checked verbatim against live code (`quotes.py:406-423`, `:467-485`, `:610-647`; `risk.py:85-165`).

## Resolved open questions
None — no `needs-answers` label and no Open-questions section; the issue is fully specified.

## Interface contracts
No signature changes. `_require_two_sided(cfg, inv, intents, why)` gains internal harmonization before the `require_two_sided_when_flat` early return; `QuoteIntent` is mutated in place (`size`, `reason` suffix). Callers (`decide_quotes`) unchanged.

## 💡 Improvement (adopted by default — architectural fit, evidence-based)
Harmonize inside `_require_two_sided`, not `_decide_quotes_from_mid`. Evidence, verbatim from the code: `_require_two_sided` docstring opens "A flat book quotes a couple or nothing at all." (`quotes.py:611`) — the couple-or-nothing invariant, including the below-floor drop-both shape, already lives in that gate.

## Depends graph
T1 → T2 → T3 (linear; T1's RED tests target T2's gate first per TDD).

## Tasks
1. `[x]` `[Backend/Logic]` **RED tests (S)** — `tests/test_live_quotes.py`: flat inventory with skewed prices/bands yields divergent leg sizes today; assert post-fix `up.size == down.size == min` plus a clamp note in `reason`; assert a below-floor pair (crafted via direct `_require_two_sided` call) returns `[]` with an informative reason. Files: `tests/test_live_quotes.py`. Skill: `test-driven-development`. Depends on: -. Verify: fail on untouched code.
2. `[x]` `[Backend/Logic]` **Gate (S)** — `core_brain/quotes.py::_require_two_sided`: when both UP and DOWN intents are present and `risk.naked_side(inv) is None`, set both sizes to the min with a reason suffix; when the min is below `cfg.min_quote_shares`, return `[]` with a reason. Files: `core_brain/quotes.py`. Skill: `test-driven-development`. Depends on: T1. Verify: T1 green.
3. `[x]` `[Backend/Logic]` **Deficit + regressions (S)** — new test: unbalanced inventory deficit leg passes through at its `size_for` deficit size, untouched; then run `tests/test_live_quotes.py`, `tests/test_paired_inventory_accounting.py`, `tests/test_rc_fixes.py`, `tests/test_trader_loop.py`. Files: `tests/test_live_quotes.py` (or paired-inventory suite). Skill: `test-driven-development`. Depends on: T2. Verify: focused suites green.

Checkpoint after T2: skewed pair clamps to symmetric sizes (shown by T1 RED→GREEN); deficit path proven intact in T3.

## Plan history
Supersedes the completed #296 plan (merged via #297) — its todo is all `[x]`.
