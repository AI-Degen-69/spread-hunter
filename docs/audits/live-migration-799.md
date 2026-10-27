# Audit: live-migration approval for Tournament #799 (Issue #839)

**Verdict: no arm is approved for live real-money execution.** The repository's
own gate returns **NO-GO for all ten arms** — the 90% CI lower bound on
inclusive mean return sits below the 1.0% bar in every store, and no arm is
close to the sample size that could move it. The candidate the pre-registered
rule points to is **`queue-clear-5m`** (the only arm that clears all three
risk-shape gates, in both halves of the run), but a risk-shape leader with a
NO-GO gate is a direction for more rehearsal, not a deployment.

**The larger finding is not about which arm wins.** The issue names
`balanced-mid` the champion on figures that do not reproduce from the stores
(+$28.37 / Sharpe 2.72 / PF 2.85 / MDD $3.37 vs measured +$23.46 / 1.66 /
1.80 / $9.64), and the cockpit labels seven arms `READY FOR LIVE` on a
descriptive score while the statistical gate says NO-GO for every one of
them. Score and gate are different mechanisms; only the gate can approve
money. A second look also corrected two figures from the planning pass
itself — see *Corrections to the planning pass* — which is the audit doing
its job, not a flaw in it.

## What was asked

Issue #839 asks for three things: (1) an audit documenting `balanced-mid`'s
superiority across PnL, Sharpe, profit factor and drawdown against all 9
competing arms; (2) a deployment verdict for the champion; (3) a deployment
payload. This audit answers all three from the tournament's own stores with
the repository's own gate machinery: (1) the superiority claim does not
reproduce — `balanced-mid` is second on nominal PnL and fails all three
risk-shape gates; (2) the verdict is not-approved for every arm, with
`queue-clear-5m` recorded as the risk-shape candidate; (3) the payload is
documented in two layers, both labelled unapproved.

## Where the data came from (and what it is not)

| Item | Value |
|---|---|
| Run directory | `data/tournaments/i799-tournament-1m_2026-10-26T00-12` (gitignored — `.gitignore:60` `/data/tournaments/` — a fresh checkout cannot re-derive this audit) |
| Arms | 10, cockpit ports 8801–8810, one store per arm (`<arm>.db`) |
| Planned window | 1440 min (`plan.json`) |
| Measured window | closes 2026-10-26 00:14 → 21:57; `queue_report` wall clock 1308.5 min |
| Process state | arms stopped 2026-10-26 ~22:01; not running |
| `results.json` in that folder | a launch-time artifact (all arms `exit_code: 1`, 0 merges) — **not** a result |

All ten stores are **rehearsal, not evidence about live performance.** The
shadow fill model credits an order when a trade prints at or through its
price, so its fills are optimistic relative to a live queue — and the queue
evidence below says the queue books themselves mostly refuse the gate's
5-minute clearance anyway.

Provenance note: four stores (`balanced-mid`, `loose-baseline`,
`prudent-hybrid`, `queue-clear-5m`) carry a 2026-10-27 00:36 mtime from
direct `write_statistics_report` reads in the prior session (that function
opens stores read-write through `OrderRegistry`). Every number in this
document was re-derived afterwards through read-only opens and
scratch-copy gate runs, and reproduces exactly — content is unaffected.
This session's own measurement touched no store (latest mtime predates it).

## Method

- **Unit = the close**, under the cockpit's own trade convention
  (`scripts/tournament_cockpit_server.py:1177`): `settlement`,
  `shadow_merge_note` and `''` are bookkeeping, everything else —
  including `shadow_settlement` — is a trade.
- Shape per arm comes from one command (read-only; every store opens with
  `mode=ro`):
  `python -m scripts.reevaluate_tournament_run --run-dir <run dir> [--json]`
- The verdict per arm comes from the repo's own gate on a scratch copy
  (the store is copied to temp, the report lands in the same temp dir, both
  vanish — no store writes, no report litter):
  `python -m scripts.reevaluate_tournament_run --run-dir <run dir> --verdict`
  which calls `core_brain.statistics_report.write_statistics_report(db,
  run_id, "shadow")` per arm.
- **The verdict rule is locked, not fitted**
  (`statistical_validation_run/artifacts.py:257 resolve_verdict`, driven by
  `core_brain/statistics_report.py:208 write_statistics_report`):
  underpowered (closes < `bot_eval_stat_gate_target_closes = 60`,
  `core_brain/config.py:268`) → `INCONCLUSIVE`; primary gate (90% CI lower
  bound on inclusive mean return) strictly above
  `bot_eval_stat_gate_threshold_pct = 1.0` (`core_brain/config.py:266`) →
  `GO`; else `NO-GO`. The 60-close count the issue leans on is a
  **precondition for a verdict, never the GO itself.** #471 names the same
  gate, inclusive of every close method.
- **Candidate ranking**, only if no arm earns GO: cleared risk-shape gates
  first (Sharpe ≥ 2.70, profit factor ≥ 2.50, max drawdown < $4.00 — the
  cockpit's decision gates, asserted per arm), then Sharpe, then realized
  PnL. Underpowered samples are judged on risk shape, never nominal PnL
  (`docs/runs/2026-10-26-843-balanced-queue5-pair.md` §1b).

## Measured ten-arm table

`python -m scripts.reevaluate_tournament_run --run-dir data/tournaments/i799-tournament-1m_2026-10-26T00-12`

| Arm | Closes | Merges | Rescue exits | Merge % | PnL | Sharpe | PF | MDD | σ | Nreq $0.02 | Gates S/P/D/N | Repo verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| loose-baseline | 171 | 99 | 66 | 57.9 | +$23.97 | 1.59 | 1.74 | $7.51 | 0.880 | 11,977 | 0/3 | NO-GO (−4.219%) |
| tight-touch | 144 | 81 | 57 | 56.2 | +$16.95 | 1.24 | 1.56 | $9.25 | 0.948 | 13,898 | 0/3 | NO-GO (−6.456%) |
| balanced-mid | 166 | 99 | 63 | 59.6 | +$23.46 | 1.66 | 1.80 | $9.64 | 0.849 | 11,148 | 0/3 | NO-GO (−4.740%) |
| wide-shield | 151 | 68 | 78 | 45.0 | +$6.27 | 0.44 | 1.15 | $11.87 | 0.947 | 13,860 | 0/3 | NO-GO (−7.620%) |
| prudent-hybrid | 170 | 98 | 64 | 57.6 | +$19.66 | 1.49 | 1.60 | $11.29 | 0.774 | 9,261 | 0/3 | NO-GO (−5.318%) |
| static-floor-2c | 169 | 100 | 66 | 59.2 | +$17.69 | 1.25 | 1.57 | $7.33 | 0.835 | 10,778 | 0/3 | NO-GO (−4.695%) |
| low-churn-deadband | 172 | 99 | 68 | 57.6 | +$10.33 | 0.92 | 1.30 | $10.77 | 0.652 | 6,566 | 0/3 | NO-GO (−6.717%) |
| hair-trigger-2c | 141 | 58 | 81 | 41.1 | +$8.51 | 1.07 | 1.33 | $9.29 | 0.562 | 4,885 | 0/3 | NO-GO (−4.358%) |
| patient-stop-9c | 129 | 63 | 59 | 48.8 | +$5.73 | 0.46 | 1.17 | $11.09 | 0.969 | 14,521 | 0/3 | NO-GO (−9.470%) |
| **queue-clear-5m** | **87** | **56** | **29** | **64.4** | **+$17.74** | **4.74** | **3.28** | **$2.60** | **0.431** | **2,865** | **3/3** | **NO-GO (−2.501%)** |

Gates: S = Sharpe ≥ 2.70, P = profit factor ≥ 2.50, D = max drawdown <
$4.00, N = closes ≥ N required for a $0.02 effect (upper case = pass). No
arm passes N: the gate also costs volume (−49% trades versus the loose
baseline). EV per trade, MC P5 (100 trades) and the cockpit score for the
four contenders: queue-clear-5m +$0.2039 / +$13.28 / 98; balanced-mid
+$0.1413 / +$0.81 / 93; loose-baseline +$0.1402 / +$1.54 / 94;
prudent-hybrid +$0.1156 / −$0.35 / 91.

Ranked by the locked rule (gates, Sharpe, PnL): queue-clear-5m,
balanced-mid, loose-baseline, prudent-hybrid, static-floor-2c,
tight-touch, hair-trigger-2c, low-churn-deadband, patient-stop-9c,
wide-shield.

## The issue's figures vs the stores

| Metric | `balanced-mid` claimed | `balanced-mid` measured | `loose-baseline` claimed | `loose-baseline` measured | `prudent-hybrid` claimed | `prudent-hybrid` measured |
|---|---|---|---|---|---|---|
| Closes | 121 | **166** | 131 | **171** | 125 | **170** |
| Realized PnL | +$28.37 | **+$23.46** | +$27.50 | **+$23.97** | +$24.24 | **+$19.66** |
| Sharpe proxy | 2.72 | **1.66** | 2.22 | **1.59** | 2.54 | **1.49** |
| Profit factor | 2.85 | **1.80** | 2.19 | **1.74** | 2.31 | **1.60** |
| Max drawdown | $3.37 | **$9.64** | $7.51 | **$7.51** | $4.95 | **$11.29** |
| Merge rate | 65.3% | **59.6%** | — | 57.9% | — | 57.6% |
| MC P5 (100 trades) | +$11.56 | **+$0.81** | — | +$1.54 | — | −$0.35 |
| EV / trade | +$0.235 | **+$0.141** | — | +$0.140 | — | +$0.116 |

The claimed figures match a ~6.4 h interim snapshot of a run that went on
to 21.8 measured hours; the arms kept trading and the shape moved. Two
consequences the operator should read plainly:

1. **`balanced-mid` is not the top-PnL arm.** Measured PnL ranks
   `loose-baseline` first (+$23.97) with `balanced-mid` second (+$23.46) —
   a $0.51 gap this sample cannot resolve (both arms' Nreq exceed 11,000
   closes). The issue's "+$28.37, #1 rank" does not reproduce.
2. **`balanced-mid` is not the risk-shape leader.** It fails all three
   pre-registered gates (1.66 / 1.80 / $9.64), and its edge fades across
   the run (see the half-split). The only arm clearing all three gates is
   `queue-clear-5m`.

## The locked rule, applied

**Half-split** (same trade convention; halves split at the midpoint of each
arm's close timestamps; Sharpe = mean/pstdev × 10, the cockpit's formula):

| Arm | H1 (n, mean/trade, Sharpe) | H2 (n, mean/trade, Sharpe) |
|---|---|---|
| queue-clear-5m | 52, +$0.2356, 6.00 | 35, +$0.1569, 3.33 |
| balanced-mid | 76, +$0.1993, 4.46 | 90, +$0.0923, 0.86 |
| loose-baseline | 83, +$0.1517, 2.34 | 88, +$0.1293, 1.23 |
| prudent-hybrid | 73, +$0.1723, 3.67 | 97, +$0.0730, 0.78 |

Only `queue-clear-5m` holds its edge in both halves (H2 Sharpe 3.33 still
clears the 2.70 gate); every other contender's H2 Sharpe falls below 1.3.

**Concentration** (share of arm PnL by `market_slug`, same convention):
`queue-clear-5m` spreads over 38 markets, the largest contributing 15.1%
(nba-mem-sac-2026-10-25, +$2.68; next two 14.7%, 11.7%).
`prudent-hybrid` spreads over 44 markets with the largest at 29.3%
(dota2-og-gl-2026-10-26, +$5.76) — concentrated, but nowhere near a
single-market book.

**Queue mechanism** (`python -m scripts.queue_report --db
<queue-clear-5m>.db`): 15,884 deltas across 182 levels in 45 markets over
1308.5 wall-clock minutes; 89% of queue movement is cancels (925,897 net
cancelled vs 119,455 traded), so the fill model — which counts only the
traded half — understates real queue progress by roughly that much; most
trade-based clear times read infinite — the books the 5-minute gate refuses.
The gate costs volume (87 vs ~170 trades) and that is the point: it is the
only arm whose books were filtered by clearance at all.

**Score is not the gate.** The cockpit's `readiness` field labels seven
arms `READY FOR LIVE` (queue-clear-5m scores 98, static-floor-2c 95,
loose-baseline 94, balanced-mid 93, tight-touch 92, prudent-hybrid 91,
low-churn-deadband 80). That label is descriptive — it never passed the
statistical gate, which says NO-GO ten times out of ten. Nothing in this
audit is "production approved".

## Corrections to the planning pass

Two figures recorded in `tasks/plan.md`'s appendix from the prior session
did not reproduce under a stated method and are superseded by the values
above: the half-split H2 means/Sharpes (no timestamp-based split — median,
time-midpoint, 12 h after first close, or full-table halves — yields a
negative H2 for `balanced-mid`; measured H2 is +$0.0923, Sharpe 0.86), and
the "74.5% single-market concentration" for `prudent-hybrid` (measured
29.3% by market and by condition id alike). The qualitative findings those
figures supported survive in weaker form: the leader's edge holds across
halves while the contenders' fades, and the leader's book is the most
spread. Every table in this audit re-runs from the commands named beside
it.

## Deployment payload (two layers, both unapproved)

**Layer 1 — measured.** The `queue-clear-5m` arm as run (port 8810):

```json
{
  "BOT_ORDER_GATE_QUEUE_CLEAR_ON": "1",
  "BOT_ORDER_QUEUE_MAX_CLEAR_MIN": "5"
}
```

No quote preset — the arm runs no `BOT_QUOTE_TOURNAMENT_PRESET` at all,
only the two queue-gate keys. That is the configuration the measured
record actually belongs to.

**Layer 2 — untested synthesis.** Any combination of these keys with a
quote preset (e.g. `balanced`), a deadband, or a stop distance has **no
measured record**: the ten arm stores cover ten configurations, and a
blend is an eleventh. It may be the next rehearsal, never the next
deployment.

No live-capital command was run for this audit, and none is authorized by
it. No cockpit, arms-JSON, config, or risk-formula change is part of it.

## What would reopen the decision

1. **Sample.** `queue-clear-5m` needs ~2,865 closes for a $0.02 effect at
   its measured σ ($0.431); the other arms need 4,800–14,500. Until the
   primary gate's lower bound sits above 1.0%, the verdict cannot move.
2. **Shape held.** The risk-shape triple (Sharpe ≥ 2.70, PF ≥ 2.50, MDD <
   $4.00) must clear on the extended sample, in both halves — the failure
   mode this run already shows in the contenders.
3. **Mechanism intact.** The queue-gate evidence must re-produce on the
   longer window (cancel share, clearance refusals, volume cost); a gate
   that stops refusing books is a gate that stopped working.
4. A follow-up the cockpit's blueprint pane should carry instead of its
   `READY FOR LIVE` badge: the score label and the gate verdict side by
   side, so the next reader never mistakes one for the other.

## How to verify

1. Open a terminal in the project root → run
   `python -m scripts.reevaluate_tournament_run --run-dir data/tournaments/i799-tournament-1m_2026-10-26T00-12 --verdict`
   → see the ten-arm table with `queue-clear-5m` first and NO-GO under
   every arm, matching the table above.
2. Open the cockpit → `python -m scripts.tournament_cockpit_server --port
   8800`, browser to `http://127.0.0.1:8800` → open any arm tab and check
   its PnL, Sharpe and merge counts against this audit's row.
3. Read this file against `tasks/plan.md`'s appendix → every figure names
   the command behind it; anything without a command is a correction
   notice, not a number.
