# SPEC.md — Issue #857: Preflight-guided Start Run launcher with mode selection, 24h horizon, and watchdog

Branch: `i857/preflight-guided-start-run-launcher` | Issue: #857

## 1. Goal

Transform the dashboard top-nav `START RUN` button (`#btn-master-toggle`) from an immediate blind launcher into an informative, safety-gated preflight modal launcher (`#start-run-modal`). The launcher enables selecting between **Shadow Rehearsal** and **Live Real-Money Run**, enforces sequential preflight verification for live capital, runs with a default 24-hour horizon (`1440m`), logs shadow rehearsals to timestamped sequence databases (`data/NN_shadow_<stamp>.db`), and actively includes the `scripts.global_stop_loss` guardrail risk watchdog.

## 2. Non-goals (explicit out of scope)

- Direct on-chain collateral deposit or withdrawal from the dashboard.
- Arbitrary custom duration inputs beyond specified presets (1h, 6h, 24h).
- Spawning a duplicate or standalone watchdog process from the dashboard (`order_manager poll` already owns and supervises `scripts.global_stop_loss`).
- Relaxing or bypassing preflight checks for live execution.
- Rewriting or deleting `data/orders.db` (production registry is strictly read-only).

## 3. Architecture & Functional Components

### 3.1 Preflight Modal Launcher (`#start-run-modal`)
- Replaces the direct start request on `#btn-master-toggle` when stopped.
- Accessible dialog overlay matching existing modal patterns (`role="dialog"`, `aria-labelledby`, Escape/Dismiss handlers).
- Two modes:
  - **Shadow Rehearsal**: Dry run, simulated balances, creates fresh numbered database (`data/NN_shadow_<stamp>.db`). Start button active immediately.
  - **Live Real-Money**: Real capital exposed on Polymarket. Live Start button remains locked until all 4 sequential preflight checks pass.
- Duration selection: Presets `60m` (1h), `360m` (6h), and `1440m` (24h, default).

### 3.2 Sequential Preflight Verification (`/api/system/preflight`)
Sequential check pipeline with in-memory session and approval gating:
1. **Credentials & Collateral (`credentials`)**:
   - Validates Polymarket API credentials, signer, and funder.
   - Verifies USDC balance and allowance are >= `VENUE_MIN_NOTIONAL_USD`.
   - Never exposes secret keys in response.
2. **Market Screener Freshness (`screener`)**:
   - Inspects `runtime/markets.json` via `load_graduated_snapshot()`.
   - Rejects stale screener data (> 15 minutes old).
3. **Market Data & Stream Connectivity (`stream`)**:
   - Performs REST book check (`full_book()`) and tests WebSocket market stream connectivity within a strict timeout.
4. **Reachability Probe (`reach_probe`)**:
   - Controlled 1-share post-only buy-and-cancel verification using `core_brain.reach_probe`.
   - Requires explicit operator opt-in (`confirm: true`) triggered by a distinct button warning of worst-case exposure ($2.00 max).
   - Passes ONLY on verified cancel, known order status, and zero matched size.

### 3.3 Single-Use Approval Store & Gated Live Start
- Passing all 4 checks generates a short-lived, single-use `preflight_id`.
- `POST /api/system/start` requires `preflight_id` and selected `minutes`.
- Consumes `preflight_id` atomically; duplicate or expired tokens are rejected.
- Spawns live stack:
  - `filter_loop`
  - `order_manager poll --interval 0.5 --watch-guardrails` (supervises `scripts.global_stop_loss`)
  - `trader_loop --live --no-reconcile --no-sweep --interval 5 --deadline-ts <timestamp>`
- Atomically saves run metadata (`run_id`, `minutes`, `started_at`, `deadline_at`, `preflight_id`) to `processes.json`.

### 3.4 Trader Loop Deadline Horizon
- `core_brain/trader_loop.py` accepts `--deadline-ts <epoch_seconds>`.
- Sleep interval is clamped using `make_deadline_sleep()` from `core_brain/shadow_run.py`.
- When deadline is reached, raises `_Deadline` and triggers clean loop exit (`shutdown_reason = "deadline"`).
- If deadline has already passed at startup, exits immediately.
- Background services (`poll`, `filter_loop`) continue running until operator presses `STOP`.

### 3.5 Shadow Run Sequence Numbering
- Implements sequence generator in Python matching PowerShell `Get-NextShadowSeq`:
  - Scans `data/*.db` matching `^(\d{1,2})_shadow_`.
  - Increments highest sequence, wraps after 99 back to 1.
  - Names db: `data/NN_shadow_<dd-MM_HH-mm>.db`, run ID: `shadow-NN`.
- `POST /api/system/shadow/start` accepts `fresh: true` to generate and switch to the new DB.
