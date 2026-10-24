# Run: tournament-test, 10 profiles for 24 hours — 2026-10-24

Branch `tournament-test` (commit `ce50f95`). Issue key `#799`. Shadow only: no signer,
no order reaches the venue, no money is spent.

This is a **strategy sweep**, not a hypothesis test. Ten arms with different quoting
profiles and risk theses run side by side for 24 hours over one shared market feed,
so the operator can read fills, merges, stop-outs, churn and PnL on the cockpit before
any single setting is promoted.

## Shared market feed

All ten arms read the **one** file `runtime/markets.json`, refreshed by the **one**
filter loop the launcher starts (`scripts.filter_loop`, 600s cycle). No arm carries
`--markets-path`, so no arm has its own universe. `SH_TOP_MARKETS=10` was set for the
launch environment because the shipped default of 2 left the overnight universe with
only already-ended games.

## Arms

| Arm | Parameters | Thesis |
|---|---|---|
| `loose-baseline` | shipped defaults, empty env | The reference; anything must beat it to earn a change. |
| `tight-touch` | `aggressive` preset: dynamic 0.25x, 2-3c | Tight quotes fill sooner; volume beats per-pair edge. |
| `balanced-mid` | `balanced` preset: dynamic 0.50x, 2-4c | A middle band trades edge for reach. |
| `wide-shield` | `conservative` preset 0.75x / 2-5c + 8c absolute stop | Volatility wants cushion more than fills. |
| `prudent-hybrid` | `prudent` preset: dynamic 0.60x, 2-4c | The compromise between cushion and reach. |
| `static-floor-2c` | dynamic offset off, fixed 2c | Volatility tracking is mostly noise. |
| `low-churn-deadband` | static 2c + 10c requote dead band | Churn is the cost; hold queue position. |
| `hair-trigger-2c` | 2c absolute stop | A naked leg is an unchosen bet; cut it early. |
| `patient-stop-9c` | 9c absolute stop | Most drops are noise a hedge can recover. |
| `queue-clear-5m` | queue-clear gate on, 5-minute max clear | A quote stuck behind a slow queue is dead capital. |

Matched profiles (both a preset and its knob): the five preset arms differ in quoting
offset; the five knob arms differ in stop policy, churn and queue admission. Every arm's
full env is in `configs/tournaments/i799-tournament-test_arms.json` and in the plan record.

## Launch

```powershell
$env:SH_TOP_MARKETS = '10'
python scripts/shadow_tournament.py `
  --issue 799 `
  --arms-file configs/tournaments/i799-tournament-test_arms.json `
  --stamp tournament-test `
  --minutes 1440 `
  --dashboards
```

Started 2026-10-24 04:05 local, deadline 2026-10-25 04:05 local.

## Where the evidence lands

| Artifact | Path |
|---|---|
| Plan (arms, env, ports, stores) | `data/tournaments/i799-tournament-test_2026-10-24T04-05/plan.json` |
| Per-arm stores | same folder, `<arm>.db` |
| Behaviour log (every cycle, skip, tape read) | `data/tournaments/tournament-test-launch.err.log` |
| Results written at the deadline | same folder, `results.json` |
| Per-arm heartbeats | `runtime/shadow_run_shadow-799-*.json` |
| Cockpit | `http://127.0.0.1:8800/` (current setup #799) |
| Arm dashboards | `http://127.0.0.1:8801/` … `:8810/` |

## What is collected

The cockpit computes, per arm and in aggregate: quoted/filled/merged funnel, merges and
single-leg exits, realized PnL, mean/std, win and merge rate, profit factor, max drawdown,
VaR95, Sharpe proxy, a 500-run Monte Carlo, and a live-readiness score. Progress now reads
the plan's 1440 minutes instead of the old hard-coded 720.

The behaviour log is separate from those KPIs on purpose: it is the raw cycle-by-cycle
record (market turns, skips and reasons, tape reads, intents) that a KPI cannot explain.
