# Plan — #488 [FEAT] Decouple Offense (Quoting), Defense (Stop-Loss Sentry), and Settlement (Merge Sweeper) into independent loops

Branch: i488/decouple-offense-defense-settlement-loops | Issue: #488
Size: Standard | Type: Code (Backend Architecture / Execution Loops / TDD)
Stack: Python (FastAPI, pytest, SQLite)
Labels: enhancement, ready-for-agent

## Architecture Specification (The 3 Decoupled Loops)

1. **Process 1: Offense (Risk-Off Market Quoter)**
   - Module: `core_brain/trader_loop.py`
   - Cadence: 5.0s – 10.0s (Relaxed)
   - Scope: Scans approved binary markets, evaluates spreads, places dual-sided maker limit orders.
   - Decoupled from open position waiting or merge blocking; never starves quoting or freezes capital across the broader universe.

2. **Process 2: Defense Sentry (Risk-On Position Guardian)**
   - Module: `core_brain/risk_guardian.py` (and invoked within `shadow_run.py` / `trader_loop.py` fast-path)
   - Cadence: Sub-second to 1.0s (High Alert / Critical)
   - Scope: Active exclusively when unhedged, single-leg exposure exists (`up_shares != down_shares`).
   - Trigger: Event-woken instantly upon any fill event or active inventory check.
   - Responsibilities:
     - Continuously evaluates immediate stop-loss limits on deteriorating positions.
     - Guards and completes complementary hedge orders.
     - Returns to idle/chill once balanced (`up_shares == down_shares`).

3. **Process 3: Settlement Sweeper (Capital Recycler)**
   - Module: `core_brain/settlement_sweeper.py` (invoked as background service / periodic pass)
   - Cadence: 60s – 300s (Very Chill / Low Priority)
   - Scope: Completely separated from high-alert risk defense.
   - Responsibilities:
     - Identifies balanced pairs (`min(up_shares, down_shares) >= 1.0`).
     - Executes gasless CTF `merge()` calls to redeem $1.00 USDC and replenish spendable collateral.
     - Because paired positions carry 0 market risk, contract latency or RPC hiccups in merging never impede Offense or Defense.

## Locked Constraints (CONSTRAINTS.md)

- Focused suites that must pass:
  - `tests/test_single_buy_saver.py`
  - `tests/test_trader_loop.py`
  - `tests/test_shadow_run.py`
  - `tests/test_shadow_tournament.py`
- Production safety: `data/orders.db` must never be touched, modified, or overwritten.
- No new external packages.
- Zero regression on existing dynamic caps and position risk limits.

## Dependency Graph

- Task 1 (Extract Settlement Sweeper for chill background merging) — unblocks Task 2.
- Task 2 (Build Defense Sentry: dedicated high-frequency <=1.0s unhedged position guardian) — unblocks Task 3.
- Task 3 (Streamline Offense Quoter in `trader_loop.py` to be non-blocking & wire seams) — Depends on: Task 1, Task 2.
- Task 4 (Shadow Seam & Tournament integration in `shadow_run.py` & `shadow_tournament.py`) — Depends on: Task 3.
- Task 5 (Targeted test verification & verification report) — Depends on: Task 4.

## Tasks

### [x] Task 1 — Extract Settlement Sweeper (`core_brain/settlement_sweeper.py`) [Size: S]
- Domain: Backend/Logic.
- Extract merge detection (`min(up_shares, down_shares) >= 1.0`) and CTF merge batching from `single_buy_saver.py` into `core_brain/settlement_sweeper.py`.
- Provides standalone `run_settlement_sweep(registry, client, ...)` with configurable 60s+ interval and background execution.
- Tests: `tests/test_settlement_sweeper.py`.

### [x] Task 2 — Build Defense Sentry (`core_brain/risk_guardian.py`) [Size: M]
- Domain: Backend/Logic.
- Create dedicated `RiskGuardian` sentry loop that monitors active unhedged positions (`up_shares != down_shares`).
- Operates at <= 1.0s cadence when active, evaluates stop losses on all open positions concurrently, and idles when exposure is zero.
- Tests: `tests/test_risk_guardian.py`.

### [x] Task 3 — Streamline Offense Quoter (`core_brain/trader_loop.py`) [Size: M]
- Domain: Backend/Logic.
- Remove synchronous blocking single-buy and merge sweeps from the critical path of the universe quoting loop.
- Quoter focuses cleanly on scanning markets and placing maker limit orders without being blocked by stale positions.
- Verify: `python -m pytest -q tests/test_trader_loop.py`.

### [x] Task 4 — Shadow Run & Tournament Runner Integration [Size: M]
- Domain: Backend/Logic.
- Update `core_brain/shadow_run.py` and `scripts/shadow_tournament.py` to support the 3-loop architecture in shadow mode.
- Ensure all 10 tournament arms run with the high-alert <= 1.0s defense loop and independent background settlement.
- Verify: `python -m pytest -q tests/test_shadow_run.py tests/test_shadow_tournament.py`.

### [x] Task 5 — Full Targeted Verification & Hands-On Verify [Size: S]
- Domain: Testing/Verification.
- Run all focused test suites.
- Verify with a 1-minute dry-run / rehearsal smoke test.
