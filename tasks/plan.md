Branch: i573/unify-one-sided-stop-loss | Issue: #573

## Tier & type
Standard (config + `single_buy_saver._route_pair` + tests). Code + Debug.

## Discovery (Step 0A intake)
- Dual-trigger (pct OR usd) at `single_buy_saver.py:_route_pair:2055`
  (`if loss >= max_loss_usd or loss_pct >= max_loss_pct`); `_route_pair` runs ONLY
  for ladder-stamped pairs (`_manage_single_leg_positions_locked:1705`).
- Non-ladder pairs use `single_leg_lifecycle.transition` with a FIXED absolute
  `hard_stop_bid = 0.15` (not loss-from-entry). So the 4c/10% dual-trigger is
  ladder-only today. **[OPEN — scope question to operator, see below]**
- Config: `single_buy_max_loss_pct = 0.10` (config.py:1039),
  `single_buy_max_loss_usd = 0.04` (config.py:1053); env at config.py:1621-1632.
- Tests: `tests/test_dual_stop_loss.py` pins current behavior (uses 0.045 usd).
- CodeRabbit plan (2 comments): planned the OLD "retire %" scope — SUPERSEDED by
  the operator's redirect. Only its file/test locations adopted (match the issue).
- [UNVERIFIED] resolved: call graph confirmed — `auto_manage_pairs` ->
  `_manage_single_leg_positions_locked` -> `_route_pair` (ladder) OR
  `evaluate`->`transition` (non-ladder, fixed 0.15 floor).

## SCOPE QUESTION (needs operator, unresolvable from code)
The 4c/10% loss-from-entry dual-trigger is **ladder-only** today. The main
(non-ladder) strategy has NO loss-from-entry stop — it uses a fixed absolute
`hard_stop_bid = 0.15` floor + escalation. Does #573's unified pct/$ stop:
- **(a) Ladder-only** — refactor `_route_pair` (recommended; matches the issue's
  "separate rules untouched"). Non-ladder floor stays as-is (follow-up).
- **(b) All one-sided** — also give non-ladder a loss-from-entry stop, replacing or
  gating the fixed 0.15 floor. Scope expansion; touches `single_leg_lifecycle` too.

## Interface contract (Step 4)
- Mode selector: `single_buy_stop_mode: str = "usd"` ("usd"|"pct");
  env `HUNTER_SINGLE_BUY_STOP_MODE`. Exactly one active per run.
- Parallel trigger vars (same structure, human whole-number units):
  - `stop_loss_trigger_pct: int = 10` (10 = 10% drawdown from entry).
  - `stop_loss_trigger_usd_cents: int = 4` (4 = 4c = $0.04 loss/share).
  - env `HUNTER_STOP_LOSS_TRIGGER_PCT`, `HUNTER_STOP_LOSS_TRIGGER_USD_CENTS`.
- Internal: convert to decimals (pct/100, cents/100) — same math as today.
- Shadow==live: stop DECISION identical for live True/False; only order placement
  differs. Asserted by a parity test.

## Improvement proposal (Step 5) — adopt-by-default
Store the $ trigger in CENTS (whole number, `stop_loss_trigger_usd_cents = 4`) so it
parallels the % trigger (`stop_loss_trigger_pct = 10`) — both whole numbers in their
unit. Evidence (operator, verbatim): "buy at 0.50 ... lose 5c => Stop-Loss Trigger
(0.XX$) = 5" and "Stop-Loss Trigger (XX%) = 10" — both whole numbers.

## Dependency graph
T1 (config: mode + rename) -> T2 (`_route_pair`: one-mode) -> T3 (tests)

## Tasks
### T1 [XS] [Backend/Logic] Config: mode selector + parallel trigger vars
- Files: core_brain/config.py
- Add `single_buy_stop_mode`; rename `single_buy_max_loss_pct` ->
  `stop_loss_trigger_pct` (int, percent) and `single_buy_max_loss_usd` ->
  `stop_loss_trigger_usd_cents` (int, cents); update env overrides + validation.
- Depends on: —
- Verify: import smoke + `python -m pytest -q tests/test_config*.py`

### T2 [S] [Backend/Logic] _route_pair: one stop mode per run, shadow==live
- Files: core_brain/single_buy_saver.py (`_route_pair:2046-2056`)
- Replace the OR'd dual-trigger with a mode switch: usd -> `loss >= usd`;
  pct -> `loss_pct >= pct`. Only the active mode fires. Same decision shadow/live.
- Depends on: T1
- Verify: `python -m pytest -q tests/test_dual_stop_loss.py tests/test_single_buy_saver.py`

### T3 [S] [Code] Tests: pin one-mode, shadow==live, tunable, %-isolated
- Files: tests/test_dual_stop_loss.py (+ a shadow==live parity test)
- Pin: exactly one mode fires; usd and pct each run in isolation; shadow/live
  parity; dollar tunable.
- Depends on: T2
- Verify: `python -m pytest -q tests/test_dual_stop_loss.py tests/test_single_buy_saver.py`
