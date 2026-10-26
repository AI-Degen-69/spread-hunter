# Rehearsal: balanced preset paired with 5-minute queue-clear gate — 2026-10-26

Parent issue: [#843](https://github.com/AI-Degen-69/spread-hunter/issues/843).

Decide whether pairing the `balanced` preset (`BOT_QUOTE_TOURNAMENT_PRESET=balanced`) with the 5-minute queue-clear gate (`BOT_ORDER_GATE_QUEUE_CLEAR_ON=1`, `BOT_ORDER_QUEUE_MAX_CLEAR_MIN=5`) maintains the risk-adjusted edge each knob showed alone in Tournament #799, rather than assuming their individual benefits simply add.

**Status: RULE LOCKED, RESULTS PENDING.** This document was committed before any run was launched. The sections below the rule are filled in only from measured output once execution concludes.

---

## 1. Pre-Registered Decision Rule

Written and committed before any measurement was taken, so conclusions cannot be reverse-engineered from post-hoc results.

### 1a. Hypotheses
1. **Complementary Edge (Ideal):** The balanced dynamic offset band (2¢–4¢) provides reach and fast fill conversion, while the 5-minute queue-clear gate filters out slow books vulnerable to adverse selection. In this outcome, `balanced-queue5` maintains a Sharpe proxy $\ge 2.70$, profit factor $\ge 2.50$, and maximum drawdown $< \$4.00$ while retaining high fill velocity.
2. **Mechanistic Overlap (Redundancy):** If the books refused by the 5-minute queue-clear gate are the exact same books where the 2¢–4¢ balanced offset would have rested, the gate adds no additional Sharpe or drawdown protection over `balanced-only` alone.
3. **Volume Starvation (Harmful Interaction):** If both knobs constrain quote opportunities simultaneously, `balanced-queue5` suffers severely degraded trade volume and fill rates without an offsetting gain in EV per trade.

### 1b. Statistical Power & Sample Size Gate
The per-trade standard deviation observed in Tournament #799 is $\sigma \approx 0.37$ for `queue-clear-5m` and $\sigma \approx 0.87$ for `balanced-mid`. Running `python scripts/stat_gate.py --dry-calc --sigma <s>` yields:

| Difference to resolve ($\Delta$) | Required $N$ ($\sigma = 0.37$) | Required $N$ ($\sigma = 0.87$) |
|---|---|---|
| \$0.01 / trade | 8,465 closed trades | 46,801 closed trades |
| \$0.02 / trade | 2,117 closed trades | 11,701 closed trades |
| \$0.04 / trade | 530 closed trades | 2,926 closed trades |

At the baseline trade arrival rate (~7.7 trades per hour per arm), a 24-hour rehearsal collects ~180–220 closed trades per arm. Therefore:
- **Rule:** A 24-hour run is **statistically underpowered** to resolve nominal PnL differences. **No arm may be declared a profit winner unless the observed sample size clears the required $N$ for $\Delta = \$0.02$ using that arm's observed $\sigma$.**
- If sample size $N$ is underpowered, the evaluation must be reported as **INCONCLUSIVE on nominal PnL**, and judged strictly on risk shape (Sharpe, profit factor, max drawdown, merge rate) and queue mechanism clearance.

### 1c. Separation Criteria ("Clearly Different")
For risk-adjusted metrics, an arm is considered "clearly different" if:
1. **Sharpe proxy:** Separates by at least 25% relative margin ($|\Delta| \ge 0.25 \times \text{baseline}$).
2. **Profit Factor:** Separates by at least 0.30.
3. **Maximum Drawdown:** Compresses drawdown by at least \$1.50 or increases drawdown by at least \$1.50.
4. **Merge Rate:** Separates by at least 10 percentage points.

### 1d. Mechanism Check (`queue_report.py`)
Using `python scripts/queue_report.py --db <db_path>`:
- Measure the distribution of queue clearance times (`CLEAR (trades)` vs `CLEAR (all)`).
- If $\ge 15\%$ of quoted books cluster in the 4–6 minute window, the gate actively trims marginal books. If $< 5\%$ cluster there, the gate rarely acts in the balanced band.

---

## 2. Arms Under Evaluation

Configured in `configs/tournaments/i843-pair-test_arms.json`:

| Arm Name | Preset Key (`BOT_QUOTE_TOURNAMENT_PRESET`) | Queue Gate On (`BOT_ORDER_GATE_QUEUE_CLEAR_ON`) | Max Queue Clear (`BOT_ORDER_QUEUE_MAX_CLEAR_MIN`) | Role in Test |
|---|---|---|---|---|
| `control` | *(none / default)* | *(none / 0)* | *(none / 60)* | Control baseline (shipped loose defaults) |
| `balanced-only` | `balanced` | *(none / 0)* | *(none / 60)* | Isolates the balanced 2¢–4¢ offset band |
| `queue5-only` | *(none / default)* | `1` | `5` | Isolates the 5-minute queue-clear refusal gate |
| `balanced-queue5` | `balanced` | `1` | `5` | **Test Arm:** Evaluates the combination |

*Note: All arms strictly inherit shipped risk stops and deadbands; no arm carries safety-layer overrides.*

---

## 3. Execution Commands (For Operator Rehearsal)

To launch the 4-arm shadow rehearsal:
```powershell
python -m scripts.shadow_tournament --issue 843 --arms-file configs/tournaments/i843-pair-test_arms.json --minutes 1440 --stamp pair-test
```

To collect per-arm shape metrics:
```powershell
python -c "from scripts.tournament_cockpit_server import analyze_arm_database; import json; print(json.dumps(analyze_arm_database('data/tournaments/i843-pair-test_<stamp>/<arm>.db', '<run_id>', 8801, 1440.0), indent=2))"
```

To run the queue mechanism check:
```powershell
python scripts/queue_report.py --db data/tournaments/i843-pair-test_<stamp>/<arm>.db
```

---

## 4. Rehearsal Results (Pending Execution)

*This section will be populated once the rehearsal run completes.*

| Metric | `control` | `balanced-only` | `queue5-only` | `balanced-queue5` |
|---|---|---|---|---|
| Run Status | PENDING | PENDING | PENDING | PENDING |
| Closed Trades ($N$) | — | — | — | — |
| Realized PnL (\$) | — | — | — | — |
| Sharpe Proxy | — | — | — | — |
| Profit Factor | — | — | — | — |
| Max Drawdown (\$) | — | — | — | — |
| Monte Carlo P5 (\$) | — | — | — | — |
| Merge Rate (%) | — | — | — | — |
| Stop Loss Exits | — | — | — | — |
| Observed Sigma ($\sigma$) | — | — | — | — |
| Stat Gate Required $N$ | — | — | — | — |
| Stat Gate Verdict | PENDING | PENDING | PENDING | PENDING |

---

## 5. Mechanism Check Results (Pending Execution)

*Queue clearance shares near the 5-minute boundary from `scripts/queue_report.py`.*

| Arm | Books $< 5\text{ min}$ (%) | Books in $4\text{–}6\text{ min}$ (%) | Books $> 10\text{ min}$ (%) | Median Clear Time |
|---|---|---|---|---|
| `control` | — | — | — | — |
| `balanced-only` | — | — | — | — |
| `queue5-only` | — | — | — | — |
| `balanced-queue5` | — | — | — | — |

---

## 6. Official Findings & Verdict (Pending Execution)

- **Interaction Finding:** Pending measured data.
- **Statistical Gate Verdict:** Pending measured data.
- **Champion Blueprint Recommendation:** Pending measured data.
