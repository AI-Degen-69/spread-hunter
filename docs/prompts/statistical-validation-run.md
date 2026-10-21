# 🎯 PROMPT — Statistical Validation Run (Pre-Live Gate for Spread Hunter)

> **Copy this entire file as the instruction to an implementation agent.**
> The agent's job is to **create** a reproducible, no-money, statistically-rigorous validation harness that proves the bot works *before* the wallet is ever funded. It must not place live orders.

---

## 1. 📋 Objective — What you are building

Build a **Statistical Validation Run** — a time-boxed + event-boxed rehearsal harness that:

- Runs the **entire production pipeline** against the **live Polymarket CLOB** (public reads only), spending **$0.00**
- Uses the **exact same files, functions and configs** the live money path uses — no parallel re-implementation, no synthetic books, no optimistic fill assumptions
- Collects enough **independent closing events** (merge closes + single-buy exits + stop-loss exits — every close method) to **statistically reject the null that total PnL ≤ 1%** — i.e. prove `90% CI for total PnL = (1%, ∞)` so the aggregate profit **including all losses** (single-buy exits, stop-loss, failed completions, gas) is robustly positive and not luck. A successful `pair_cost < 1.00 → profit = 1.00 - pair_cost > 0` is **tautologically positive**; testing `E[PnL|successful pair] > 0` proves nothing. The gate is on **inclusive total PnL**, not conditional per-pair profit.
- Produces a **machine-readable report + human-readable decision memo** that says GO / NO-GO with evidence

**Live is the default in this repo** (`AGENTS.md`). `--no-live` is the dry run. The only loop an agent may run unsupervised is `python -m core_brain.shadow_run --minutes N` because it holds **no signer** (`core_brain/shadow_guard.py:52`). Your harness must preserve that property.

---

## 2. 🚫 Non-Negotiables — Read before you write code

1. **No real money.** The harness must be structurally incapable of spending:
   - Venue client = `core_brain/shadow_guard.py:shadow_client()` → `ReadOnlyVenue` deny-by-default proxy. Only `READ_METHODS` (`core_brain/shadow_guard.py:21`) pass. Any write (`post_order`, `cancel_order`, `create_order`) must raise `ShadowSafetyViolation` (`core_brain/shadow_guard.py:39` — `BaseException`, not `Exception`).
   - Store = isolated SQLite file (e.g. `data/shadow_stat_<timestamp>.db`). **Hard-refuse** `data/orders.db` via `assert_not_production_registry()` (`core_brain/shadow_guard.py:134`). Check `tests/conftest.py` guard for URI edge cases.
   - No `POLY_PRIVATE_KEY` / `POLY_API_*` loaded. Public endpoints only: `GET /book`, `GET /price`, `GET /trades`, market metadata.

2. **No mocked venue data.** Every decision must read the **live book** through the production path:
   - Market universe: `runtime/markets.json` via `core_brain/market_feed.py:load_graduated_markets()` and `core_brain/trader_loop.py:_market_specs()` — the Market Filter's graduated list (`scripts/filter_markets.py` → `scripts/filter_loop.py`), not a hand-picked list.
   - Per-market books: `core_brain/markets.py:full_book()` / `parse_book()` (public `GET {CLOB_HOST}/book`).
   - Tape for fill inference: `core_brain/markets.py:recent_trades()` (deduped by `seen` set).
   - Snapshot `runtime/pipeline.json` + `runtime/markets.json` + effective `MakerConfig` into the artifact directory so the run is replayable.

3. **No forked strategy logic.** Call the live decision stack directly:
   - Config: `core_brain/config.py:MakerConfig` via `load()` (`core_brain/shadow_run.py:517`) — same `bot_risk_exposure_max_order_usd` / `bot_risk_exposure_max_total_usd` caps as `core_brain/venue.py:25`, same `max_pair_cost=0.99`, `max_completable_pair_cost=1.00`, `requote_dead_band=0.03`, `price_band 0.10-0.90`, skew, band risk, fleet caps (`core_brain/risk.py`, `core_brain/quotes.py:137`).
   - Decision: `core_brain/quotes.py:decide_quotes` → `_decide_quotes_from_mid` (objective `spread_capture`, `core_brain/quotes.py:420`), including `risk.hard_block` and `risk.completable_pair_block`.
   - Planning: `core_brain/trader_loop.py:plan_orders` with re-gate + dead-band (`trader_loop.py:54`).
   - Loop: `core_brain/trader_loop.py:run` via `VenueSeam` (`core_brain/shadow_run.py:268 build_shadow_seam` / `run_shadow`). Wire `settling_inventory_fn` → `shadow_exec.settle_market` before each decide (same ordering as live).
   - Single-buy rescue + merge: `core_brain/shadow_exec.py:ShadowExecutionClient` + `record_shadow_merges` + `single_buy_saver.auto_manage_pairs` via `shadow_sweep` (`core_brain/shadow_run.py:568`). This is the **actual production rescue**, not a stub.

4. **Conservative, not optimistic.** Optimism is the failure mode:
   - Fill model: `core_brain/shadow_fills.py:credit_fills` ONLY — tape-confirmed volume after `queue_ahead` is consumed. **Do not** credit fills from mid moves, spread moves, or time-in-book alone. Document the bias explicitly (fills are modeled, `docs/agents/safety.md:89`). Queue position from `shadow_exec.queue_ahead_at` / `shadow_queue` table.
   - Completion fills (single-buy rescue): `ShadowExecutionClient.create_and_post_market_order` fills at `price = ask` (`shadow_exec.py:483` — optimistic end of taker). Flag this in the report as an upper bound; optionally add a pessimistic sensitivity (`ask + 1 tick` or `ask + half-spread`).
   - Merges: `record_shadow_merges` arithmetic `proceeds = shares * 1.00`, cost basis = remaining-average (`shadow_exec.py:839`), `method='shadow_merge'`, `gas=0`. Never invent on-chain gas savings.
   - Do not fabricate rebate income — graduated spread markets carry `daily: 0.00` (`docs/agents/strategy.md:44`), `kpi.py:1108 rebate_est = None`.

---

## 3. 📊 Statistical Design — Prove it is not luck

### 3.1 Frame the hypothesis correctly — total PnL, not conditional per-pair profit

- **⚠️ Per-pair profit of a successful merge is tautologically >0.** By construction a merge closes only when `pair_cost < 1.00`; `profit = 1.00 - pair_cost` is then always positive. Testing `E[PnL | successful pair] > 0` is a tautology that can never fail and proves nothing.
- **Unit of analysis = the inclusive set of ALL closes** (`closes` rows, `core_brain/order_registry.py:CloseRecord` — `method='shadow_merge'` **plus** `method='single_buy_exit'/'naked_exit'` **plus** any stop-loss or failed-completion closes). The strategy's true edge is `total_realized_pnl = Σ realized_pnl` across every close, losses included.
- **Primary H0:** `total PnL ≤ 1%` (or `E[return_pct] ≤ 1%`). **Alt H1:** `total PnL > 1%` with **90% confidence interval = (1%, ∞)**. That is: the **lower bound of the 90% CI for total PnL (or mean_return_pct) must sit above +1%**. Report both one-sided 90% (`ci90_lower_pct`, `core_brain/kpi.py:109`) and two-sided 95% (`ci95_return_pct`) for transparency, but the **gate is the 90% lower bound > 1% inclusive of every loss**.
- **Secondary H0:** win_rate ≤ 0.5 (Wilson interval, `core_brain/kpi.py:31 _wilson_ci`) — useful but not sufficient; a 51% win rate with large exits can still be net negative.
- Do **not** test on orders or fills alone — a high fill count with negative drift is a loss (adverse selection). Do **not** filter closes to `shadow_merge` only — that hides the single-buy failure mode (`docs/agents/strategy.md:72` — the two failure modes).

### 3.2 Sample size — calculate, don't guess

Require the agent to **derive N from the data**, not pick a round number:

1. **Power analysis for expectancy** (primary):
   ```
   n_required = ((z_{1-α} + z_{1-β}) * σ / δ)²
   ```
   where `δ` = minimum detectable edge (propose 1.5–2.0c per pair; historical paper run mean ≈ 4.0c at `0.96006` pair cost, 476 closes +$1172), `σ` = stdev of `realized_pnl` or `return_pct` from `core_brain/kpi.py:compute_trade_analytics` (paper run σ is large — per-fill markout σ ≈ $56 measured 2026-08-02 — so expect n ≈ 100–300 for 80% power). Use one-sided α=0.05 (z=1.645), β=0.20 (z=0.84). Show the table for δ = 1c, 2c, 4c.

2. **Wilson precision for win_rate** (secondary):
   Half-width ≈ `z * sqrt(p(1-p)/n)`. To claim win_rate > 0.5 with lower bound > 0.55 at p≈0.6 needs n ≥ 100; at p≈0.7 needs n ≥ 70. Report.

3. **Markout maturity** (adverse selection gate):
   `core_brain/config.py:232 markout_min_sample=8`, `markout_fleet_min_sample=25`, horizons `(300, 3600, 21600, 900)`s (`core_brain/markout.py:24`). A verdict with zero matured markouts is `insufficient_sample`, not a pass. Require ≥ 25 pooled matured samples before evaluating `bot_risk_circuit_fleet_posture` (`core_brain/markout.py:219 fleet_stats`).

4. **Historical calibration**:
   Reference: paper run 476 closes, shadow run `run-2809a7161de1` had 209 orders / 0 fills in 30 min — **minutes ≠ sample**. The agent must justify **event-based stopping** (N closes) over pure time-box. E.g., 60 and 120 markets are starting points, but the agent must show they meet the power requirement or raise them.

5. **Recommendation to implement**:
   - **Hybrid stop**: `stop when (elapsed ≥ T_min AND closes ≥ N_min AND matured_markouts ≥ 25) OR elapsed ≥ T_max`. Example: `T_min=6h`, `N_min=120 closes`, `T_max=72h`. If `T_max` hits with `N < N_min`, the report is `INCONCLUSIVE — underpowered`, not GO.
   - Provide a `--target-closes` and `--max-hours` CLI, plus a `--dry-calc` mode that prints required N without running.

### 3.3 Estimators and intervals to report

Reuse `core_brain/kpi.py:50 compute_trade_analytics` — do not re-derive:

- `n_closes` **inclusive** (every `method` — `shadow_merge` + `single_buy_exit`/`naked_exit` + stop-loss), `wins/losses`, `win_rate` + `win_rate_ci95` (Wilson), `expectancy_usd`, `mean_return_pct`, `stdev_return_pct`, **`ci90_lower_pct` gated at > 1.0% (not > 0)**, `ci95_return_pct`, `profit_factor`, `risk_reward_ratio`, `sharpe_ratio`, `sortino_ratio`, `max_drawdown_usd/pct`, `max_naked_exposure_usd`, `pnl_distribution`, **`total_realized_pnl` and `total_return_pct` inclusive**
- Pair-cost: `median_pair_cost`, `pairs_under_1` (`kpi.py:1089`), full sorted distribution — report but **do not gate on conditional merge profit alone** (tautology)
- Fill quality: `fill_rate`, `median_seconds_to_fill`, `median_queue_ahead`, `fill_by_queue`, `quote_uptime`, `top_skip_reasons` (hard blocks), `adverse_selection` per share (size-weighted drift to longest matured horizon), `markout_samples`
- Rescue: completion rate, exit rate, realized `bot_risk_pairs_complete_gain_cents` vs `bot_risk_pairs_exit_cost_cents` (`config.py:818`) — these losses are **inside** the total PnL gate, not beside it
- Mechanics: `order_latency_ms`, `reconcile_lag_ms`, `venue_rejects`, divergences (even if NO-OP in shadow, report as `skipped_stages=("reconcile",)` per `shadow_run.py:633`)

---

## 4. 🔧 Implementation Blueprint — What to build

Create `core_brain/statistical_validation_run.py` (or `scripts/`) with:

```python
# CLI sketch the agent should flesh out
python -m core_brain.statistical_validation_run \
  --target-closes 150 --max-hours 48 --interval 5 \
  --db data/shadow_stat_<ts>.db --report reports/stat_<ts>/ \
  --sensitivity pessimistic   # runs base + pessimistic slippage side-by-side
```

Internals:

1. **Config snapshot** (`core_brain/shadow_run.py:524`): log `bot_quote_spread_static_offset_cents`, `bot_quote_price_risk_widen`, `bot_pos_price_max_complete_usd`, caps, `bot_order_requote_dead_band_cents`, `bot_risk_pairs_exit_window_sec`. Support env overrides `BOT_POS_PRICE_MAX_COMPLETE_USD`, `BOT_ORDER_REQUOTE_DEAD_BAND_CENTS` but snapshot the **effective** values.

2. **Seam wiring** (`build_shadow_seam` pattern):
   - `db_path` = isolated file, `run_id = shadow_run_id()` (`shadow_run.py:51` shadow- prefix).
   - `client_fn = shadow_guard.shadow_client` (no injection of signing client).
   - `fetch_books = _default_fetch_books()` (real `full_book`), `fetch_market = _lookup_fetch_market`, `traded_fn = _default_traded_fn`.
   - `decide_fn = quotes.decide_quotes`, `inventory_fn = settling_inventory_fn`, `fleet_state_fn = _fleet_state` (naked/committed/posture per `trader_loop.py:865`).
   - `sweep_fn = shadow_sweep` (single-buy rescue + shadow merges).
   - `emit_fn = _make_logging_emit(db_path, run_id=...)` with `ring_path = runtime/shadow-<run_id>.jsonl` (no rotation).

3. **Run loop**: delegate to `trader_loop.run` with `interval`, `live=True` (safe — submit/cancel are recorders), `sleep_fn = make_deadline_sleep` **or** better: an event-aware `sleep_fn` that checks `closes_count >= target` each cycle and raises `_Deadline` early. Log every `quoting/decide` with `up= / down=` inventory (`shadow_run.py:248`).

4. **Maturity handling**: markouts are sampled from `markouts` table; `sample_pending_markouts` is normally background (`MarkoutWorker`). In shadow, maturities are wall-clock based — a short run matures nothing, which must read as `insufficient_sample`, not zero drift. Either run long enough for 5m/1h horizons to mature, or clearly flag immature runs as underpowered. Do not fake maturity timestamps.

5. **Reporting** (`core_brain/kpi.py:report`):
   - Call `kpi.report(db_path, run_id=...)` or `compute_trade_analytics` directly on the shadow DB.
   - Emit `report.json` (full KPI dict), `report.md` (human memo with GO/NO-GO), `closes.csv`, `fills.csv`, `quotes.csv`, `config_snapshot.json`, `pipeline_snapshot.json`, `markets_snapshot.json`.
   - Include sensitivity columns: base vs `pessimistic_fill = +1 tick on completion` vs `gas=0.05` (`config.merge_gas_usd`).

6. **Tests** (required per `docs/agents/verifying.md`):
   - Unit: Wilson CI edge cases (n=0, n=1, all-wins), `compute_trade_analytics` with mocked closes, power-calc table, hybrid stop condition.
   - Integration: 3-cycle loop test like `tests/test_shadow_run.py:826 TestSecondRotation` — proves re-quote + settle + rescue work together.
   - Guard test: asserts no import of `shadow_fills` / `shadow_exec` from `core_brain/live_fill_engine.py` / `dashboard/` / `scoring/` (see `tests/test_shadow_run.py:720 TestBoundaryGuard`).
   - Failure-mode test: minutes-only run that gets 0 fills must exit `INCONCLUSIVE`, not GO.

7. **Dashboard wiring (optional but valuable)**:
   - Document `python -m dashboard.server --db <shadow_stat.db> --port 8799` read-only view. Note the SHADOW badge and that START is refused on shadow DB (`docs/agents/safety.md:50`).

---

## 5. ✅ Decision Gate — When to say GO

The agent must define **explicit, numeric gates** in `report.md`. Suggested (tune with justification, but keep the primary gate as stated):

| Gate | Metric | Threshold | Rationale |
|------|--------|-----------|-----------|
| **Primary — total PnL** | `ci90_lower_pct` (one-sided, **inclusive** total) | **`> 1.0`** | **90% CI for total return = (1%, ∞)** — lower bound of inclusive mean return (every close method, every loss) sits above +1%. Fix: `E[PnL|successful pair] >0` is tautological (`pair_cost <1 → profit >0` by construction); the proof is that the *aggregate* survives exits/stops |
| **Total PnL $** | `total_realized_pnl` lower 90% bound | `> 0` and `> 1% of bankroll` | Dollar version of the same gate — report both forms |
| **Win rate** | `win_rate_ci95.lower` | `> 0.50` | Wins not a coin flip (Wilson, `kpi.py:31`) — secondary only |
| **Economic** | `expectancy_usd` vs `merge_gas_usd` | `> 0.05` | Edge survives real gas (`config.py:315`) — folded into total PnL anyway |
| **Adverse sel.** | `adverse_selection` per share | `> -0.005` | Fleet markout not catastrophically adverse (`bot_risk_markout_widen_threshold`) |
| **Rescue** | `single_buy` completion rate | report inside total | Completion/exit CIs are **not** separate gates — their losses are already inside the primary total PnL gate |
| **Sample** | `n_closes` inclusive | `≥ n_required` from §3.2 | Underpowered runs cannot be GO |
| **Maturity** | `markout_samples` | `≥ 25` pooled | Otherwise `insufficient_sample` |

- **GO** = all primary gates pass on **both** base and pessimistic sensitivity.
- **NO-GO** = any primary gate fails with adequate sample.
- **INCONCLUSIVE** = `n < n_required` or `markout_samples < 25` or `T_max` hit — extend, don't ship.

The memo must state the **cost of being wrong** (directional bet loss ≈ $1.00 per stranded share, `docs/agents/strategy.md:72`) and the **money at risk if GO is acted on** (venue caps `MAX_ORDER_USD=25`, `MAX_TOTAL_USD=100`).

---

## 6. 📦 Deliverables & Done means

Per `docs/agents/verifying.md`:

1. `python -m pytest -q` green — every new behaviour has a test that **fails without the change**.
2. Code + tests + docs in one PR, `VERSION` + `CHANGELOG` bumped for `/ship`.
3. **How to verify** block (5 steps or fewer, with expected value per step and what failure looks like + undo):

```markdown
## How to verify
1. `python -m pytest tests/test_statistical_validation_run.py -q` → all pass (or paste output).
2. `python -m core_brain.statistical_validation_run --target-closes 150 --max-hours 48 --db data/shadow_stat_test.db` → runs with banner `SHADOW RUN starting: mode=shadow ... NO SIGNER LOADED`, creates `reports/stat_*/report.json`.
3. Open `reports/stat_*/report.md` → `n_closes`, `ci90_lower_pct`, `win_rate_ci95`, `pairs_under_1` populated; verdict is GO / NO-GO / INCONCLUSIVE with justification.
4. `python -m dashboard.server --db data/shadow_stat_test.db --port 8799` → badge `SHADOW`, Trader card shows fills/closes, no `data/orders.db` writes.
5. `ls data/shadow_stat_test.db runtime/shadow-*.jsonl` exist; `sqlite3 data/orders.db "select count(*) from orders where run_id like 'shadow-%'"` → 0 (or `data/orders.db` mtime unchanged).

Failed checks: missing report → harness crashed; `n_closes=0` after hours → universe empty (check `runtime/markets.json`); `ShadowSafetyViolation` → client tried to write (blocked correctly — inspect call site).
```

---

## 7. 💡 Guidance for the Agent — How to think

- **Start from `core_brain/shadow_run.py:475 run_shadow`** — do not rewrite the loop. Extend it.
- **Read `docs/agents/architecture.md`, `docs/agents/strategy.md`, `docs/superpowers/specs/2026-08-24-shadow-fill-simulation.md`** before coding.
- **Bias toward pessimism.** If uncertain whether a modeled fill over- or under-credits, choose the interpretation that makes the report **worse**. A GO that survives pessimism is worth acting on; a GO that needs optimism is not.
- **Log the config** (`shadow_run.py:524 effective config`) — without it, two runs hours apart are indistinguishable (see `shadow-` prefix rationale, `shadow_run.py:51`).
- **Handle the `data/shadow.db` reuse trap** — `settle_market` filters by `run_id` (`shadow_exec.py:390`), and `queue_marks` are scoped to `run_id`. Do not let a new run inherit old orders' queue.
- **Document limitations** in `report.md`: modeled fills, optimistic completion price, immature markouts, gas = 0 in shadow. Never present rehearsal PnL as live PnL (`docs/agents/safety.md:45`).

---

## 8. 🚀 Stretch (if time)

- Replay mode: feed recorded `recent_trades` + `full_book` snapshots to `credit_fills` offline for deterministic regression.
- Power-curve plot: `report.md` figure of CI width vs n.
- Multi-run aggregation: `kpi.py:list_runs` across several shadow `run_id`s to show stability.

---

**One concrete next step for the operator after the agent ships:**

> Run the harness once with `--target-closes 150 --max-hours 48` and paste `reports/stat_*/report.md` verdict + `report.json` `trade_analytics` block in the PR — that is the statistical proof the wallet is waiting for.

