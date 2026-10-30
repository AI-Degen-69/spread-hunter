# Station II Plan — Issue #875: Correct Sharpe ratio convention (time-series)

Branch: `i875/establish-correct-sharpe-ratio-convention` | Issue: #875

- **Tier:** Standard (new helper + cockpit + reevaluate + docs + tests; one architectural decision)
- **Task types:** Code (primary), Testing, Docs, UX/Copy (cockpit label + tooltip)
- **Stack / runner:** Python 3, `pytest` (`python -m pytest -q`)

---

## 1. The problem, in one line

The cockpit calls `(mean_pnl / std_pnl × 10)` a Sharpe ratio:

> `sharpe_proxy = (mean_pnl / std_pnl * 10.0) if std_pnl > 0.0 else 0.0`
> (`scripts/tournament_cockpit_server.py:687`)

That is a per-trade PnL ratio scaled by ten — not the Sharpe ratio the blueprint's
`9.52` and the gates' `≥ 2.70` pretend it is. Two different metrics share one label.

## 2. Grounding: what the code actually gives us (verified, not assumed)

| Fact | Evidence |
| --- | --- |
| Unthinned closes exist before chart thinning | `analyze_arm_database` reads all `closes` rows `ORDER BY ts ASC` (`tournament_cockpit_server.py:573-579`); thinning to 60 points happens later (`:666-677`) |
| Arm env overrides ride along in `cfg["env"]` | `reevaluate_tournament_run.py:175-182` builds `cfg` with `"env": arm.get("env", {})` |
| `kpi.compute_trade_analytics` must NOT change | `kpi.py:579-585` explicitly rejects annualisation ("trades are not daily observations"); golden payload pins `sharpe_ratio: 0.6229353305576062`; ~10 test files assert it |
| `_bounded_float(name, raw, lo, hi)` exists for the new setting | `core_brain/config.py:703` — import it, don't reimplement |
| No `BOT_SHARPE_*` / `RISK_FREE` anywhere today | grep over `.env.example`, `core_brain/config.py`, `scoring/config.py` — empty |
| No `docs/adr/`, no `CONTEXT.md` | `ls` fails — convention note goes to `docs/sharpe-ratio-convention.md` (new) |
| `shadow_paired_runs` is NOT reachable from arm DBs | cockpit never queries it; paired runs use dedicated 2-arm stores (`paired_shadow.py:154+`), tournaments run 6 arms — **[UNVERIFIED]** whether any arm DB carries the table |

## 3. CodeRabbit intake (read once; Stations III–V need not reopen it)

**Adopted:** the 3-part skeleton (helper → cockpit → reevaluate); 5-minute clock grid with
carry-forward; 365-day annualisation; risk-free as yearly decimal ratio `0.0..1.0` with compound
per-step conversion; thresholds kept as numbers in named constants flagged for review; setting read
in the helper module so `config.py`/`.env.example` stay untouched; the exact known-series test
values (100, 110, 99, 108.9 → returns +0.10, −0.10, +0.10).

**Rejected:**
- *Paired-record capital fallback* (`shadow_paired_runs` lookup as first source) — no plumbing
  carries a tournament DB into `analyze_arm_database(cfg)`; `cfg` has only the arm DB path.
  Two-level fallback instead (arm env → cockpit default), basis published.
- *Seven tasks / per-surface rewrites of `kpi.py` and dashboard tiles* — over-split and wrong:
  `kpi.py` per-trade Sharpe is intentional and pinned; dashboard static binding just renders it.
- *New `HUNTER_*` env alias* — no caller uses it; one name only.

**Left `[UNVERIFIED]` for Build to check, not trust:** exact dashboard `get_parameters`-style card
line numbers (none found — dashboard needs no change); whether `test_541_golden_payload.json`
or cockpit HTML snapshot tests assert the old proxy digits (grep in T4).

## 4. Interface contract (locked before code)

**New module `core_brain/sharpe.py`:**

```python
def sharpe_ratio(points: list[tuple[float, float]],
                 risk_free_rate_annual: float = 0.0,
                 bucket_seconds: int = 300) -> float | None
SHARPE_FORMULA_TEXT: str   # plain formula, reused by both surfaces' tooltips
def risk_free_rate_from_env() -> float  # BOT_SHARPE_RISK_FREE_RATE, unset/blank → 0.0,
                                        # via config._bounded_float, range 0.0..1.0
```

- `points` = `(epoch seconds, portfolio value in dollars)`; drop non-finite times/values and
  values ≤ 0; sort stably by time; last wins on duplicate timestamps.
- Grid from first point in `bucket_seconds` steps through first grid time ≥ last point;
  carry last known value forward (quiet periods = zero-return steps).
- Step return = `end/start − 1`; per-step risk-free = `(1 + annual)^(step/31_536_000) − 1`;
  ratio = `mean(excess) / stdev_sample(excess) × √(31_536_000/step)` (n−1).
- Return `None` (never `NaN`, never placeholder `0.0`) when: no valid points, < 2 step
  returns, or zero spread. Callers render `None` as `NO_DATA`.

**Cockpit payload:** field `sharpe_proxy` → `sharpe_ratio` (float | `None` → JSON `null`).
All readers move in the same change: `analyze_arm_database`, `compute_decision_gates`,
`generate_cockpit_html` (2 sites), `reevaluate_tournament_run.py` (gate, ranking key, display).
Gates keep numbers, gain names: `SHARPE_TARGET = 2.70`, `SHARPE_MARGINAL = 2.20` + review flag.

**Capital base (2-level, published):** `sharpe_capital_basis ∈ {arm_env, assumed_default}` —
arm `env["BOT_QUOTE_BANK_BASELINE_USD"]` first, else cockpit-process `config.load()` default.
Equity points from **unthinned** close rows: opening point at `min(first_ts, first close)`,
then `starting_money + running realized PnL` per close, closing point at `max(last_ts, last close)`.

**Must not change:** `kpi.py` outputs and golden values; threshold numbers; past figures.

## 5. Tasks

### [x] T1 — Shared helper + tests + convention note · M · `[Backend/Logic]`
Depends on: —

- `core_brain/sharpe.py` (new): implement the contract in §4 exactly. Import `_bounded_float`
  from `core_brain.config` (verified at `config.py:703`); no edits to `config.py`.
- `tests/test_sharpe_ratio.py` (new): every test name contains `sharpe`. Series 100, 110, 99,
  108.9 at 0/300/600/900 s (returns +0.10, −0.10, +0.10; per-step ratio √3/6; hand calc in comment):
  known-series ≈ `(√3/6) × √105120`; nonzero-rate `0.05` with compound per-step deduction;
  irregular gap `(0,100)→(900,110)` carry-forward (returns 0,0,0.10); duplicate-timestamp +
  shuffled input; invalid (`NaN`/`inf`/≤0) dropped; flat → `None`; single step → `None`;
  empty/all-invalid → `None`; env unset/blank → `0.0`, `"0.05"` → `0.05`,
  `"-0.01"`/`"1.5"`/`"abc"`/`"nan"` raise.
- `docs/sharpe-ratio-convention.md` (new): formula, 5-min steps, carry-forward, 365-day scaling,
  rate units + compound conversion, capital fallback + assumption warning, `None` cases,
  short-sample/serial-dependence caveats, threshold-review flag (2.70/2.20 tuned for the proxy),
  old-vs-new golden note, no live-rate / no backfill.
- Verify: `python -m pytest -q tests/test_sharpe_ratio.py`

### [x] T2 — Cockpit switch (analyzer + gates + UI) · L · `[Backend/Logic]` + `[UX/Copy]`
Depends on: T1

- `scripts/tournament_cockpit_server.py`: in `analyze_arm_database`, delete the proxy line
  (`:686-687`); build equity points from unthinned rows per §4; call `sharpe_ratio()` with
  `risk_free_rate_from_env()`; emit `sharpe_ratio` (+ `sharpe_capital_basis`), drop `sharpe_proxy`.
  `compute_decision_gates`: read `sharpe_ratio`, keep `2.70`/`2.20` as named constants, `None` → `NO_DATA`.
  HTML: `Sharpe:` → `Sharpe Ratio` with `title="{SHARPE_FORMULA_TEXT}"` at both card sites
  (`~:1057`, `~:1086`); gate copy `Sharpe Proxy Gate` → `Sharpe Ratio Gate`.
- Tests: update `tests/test_tournament_cockpit_decision_gates.py`,
  `tests/test_tournament_cockpit_funnel.py`, `tests/test_shadow_tournament.py` fixtures/keys
  (`sharpe_proxy` → `sharpe_ratio`); add one analyzer test on a temp arm DB asserting the new
  field equals the helper on the same closes and `None` renders `NO_DATA`.
- Verify: `python -m pytest -q tests/test_tournament_cockpit_decision_gates.py tests/test_shadow_tournament.py tests/test_tournament_cockpit_funnel.py`

### [x] T3 — Re-evaluate script switch · S · `[Backend/Logic]`
Depends on: T1

- `scripts/reevaluate_tournament_run.py`: `risk_gates["sharpe"]` from `sharpe_ratio`
  (`:190`); ranking key (`:267`) `sharpe_proxy` → `sharpe_ratio`; per-arm record (`:217`) and
  table format (`:308-326,338`) renamed; golden text `S=Sharpe>=2.70` kept.
- Tests: `tests/test_reevaluate_tournament_run.py` keys/fixtures renamed; one gate test with
  `sharpe_ratio: None` → gate `False`.
- Verify: `python -m pytest -q tests/test_reevaluate_tournament_run.py`

### [x] T4 — Gates and handoff · S · `[Testing]`
Depends on: T1, T2, T3

- Grep gate: no production `sharpe_proxy` remains
  (`grep -rn sharpe_proxy scripts/ core_brain/ dashboard/ --include=*.py --include=*.js` → only
  historical docs/comments); confirm `test_541_golden_payload.json` `sharpe_ratio: 0.6229…` untouched.
- Targeted suites from T1–T3 plus kpi regression in one run:
  `test_sharpe_ratio + decision_gates + reevaluate + shadow_tournament + trade_analytics + kpi`.
- Issue acceptance: `python -m pytest -q tests/ -k "sharpe"` passes.
- Operator "How to verify" block (hands-on only, no pytest) + PR body.

**Checkpoints:** after T1 (helper green), after T2 (cockpit card shows new ratio), after T3
(reevaluate agrees), after T4 (targeted run + handoff). In `auto` mode these are one-line reports.

## 6. Deliberately out of scope

- Retuning the 2.70/2.20 gates — flagged for review on future runs, numbers unchanged here.
- `kpi.compute_trade_analytics` per-trade Sharpe — intentional, pinned, untouched.
- Dashboard tiles rendering `trade_analytics.sharpe_ratio` — same kpi meaning, no change.
- `BOT_SHARPE_RISK_FREE_RATE` in `config.py`/`.env.example` — lives in `sharpe.py` this round.
- Live risk-free rates; historical backfills; paired-record capital lookup (no plumbing).

## 7. Open questions from the issue, answered from code

1. *Risk-free rate?* — `0` default for USDC, configurable `BOT_SHARPE_RISK_FREE_RATE` (yearly
   decimal ratio); issue allows exactly this.
2. *Which series?* — unthinned close history inside `analyze_arm_database`, not the 60-point
   chart `series` (same curve, full detail).
3. *Do cockpit, dashboard, trader share one function?* — narrowed with evidence: cockpit +
   reevaluate share the new helper; trader/dashboard kpi metric is a different, documented
   statistic that stays.
4. *Label?* — cockpit `Sharpe` → `Sharpe Ratio` + formula tooltip; dashboard tile already reads
   `Sharpe & Sortino Ratio` and keeps its kpi meaning.

## 8. Rejected proposals log

| Proposal | Verdict | Why |
| --- | --- | --- |
| Paired-record capital fallback first | Rejected | No tournament-DB path in `cfg`; arm DBs may lack the table — [UNVERIFIED], 2-level fallback instead |
| Rewrite `kpi.py` per-trade Sharpe to the time-series formula | Rejected | Explicit no-annualisation rationale + golden pin + ~10 test files |
| Keep payload key `sharpe_proxy` with new value | Rejected | Key would lie about its contents; rename + move readers atomically |
| Retune gates to the new metric now | Deferred | Needs future-run data; numbers kept, review flagged |
| `HUNTER_*` alias for the new setting | Rejected | No caller uses it; one name only |
