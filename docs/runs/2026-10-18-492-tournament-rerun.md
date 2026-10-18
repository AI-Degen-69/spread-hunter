# 2026-10-18 — #492 ten-arm tournament rerun on fixed code

Parent issue: #492. Arms: the original #484/#485 ten, first-run values.
Status: **prepared and validated** (dry-run). The 12-hour run is the operator's to launch.

## Why this run exists

The first run's books are not results. Two script defects produced them, and both are fixed on
`main`: hard-stop exits booked prices the venue never gave (`e7ddc45` — the SELL now fills
against the book read inside the exit call, and `trigger_bid` / `fresh_bid` land on every
close), and merge closes attributed leg costs positionally instead of by side (`bec4589`).
Until the original arms run clean, neither the first-run leaderboard nor any #487
tweak-vs-baseline claim built on it can be trusted.

## Read this before comparing the two runs

| Dimension | First run (#484/#485) | This rerun | Why |
| --- | --- | --- | --- |
| Per-leg stop | `HUNTER_SINGLE_BUY_MAX_LOSS_USD` 0.045 / 0.030 / 0.050 / 0.065 / 0.038 | `BOT_RISK_STOP_MODE=ABSOLUTE_DROP` + `BOT_RISK_STOP_ADVERSE_DROP_CENTS` 0.045 / 0.030 / 0.050 / 0.065 / 0.038 | #573 renamed the knob to cents, #619 replaced it with the entry-relative trigger, and variables use the canonical `BOT_*` names. Same threshold, current units. Leaving it out would run every arm with no stop at all. |
| Single-buy grace | 45 s (0 s on snapstop) | **not expressible** | `HUNTER_SINGLE_BUY_GRACE_SEC` retired by #619; `aged_out_rescue_lead_sec` is a different mechanism. snapstop now differs from aggressive only by its 3.0¢ stop. |
| Control's static 2¢ offset | `HUNTER_REWARD_OFFSET=0.02` | field default 0.020 | the env override was purged; the effective value is unchanged. |
| Hard-stop exit price | prior rotation's `queue_marks` snapshot | book read inside the exit call | `e7ddc45` — the first defect being measured. |
| Merge leg cost | by token-id string | by quoted side | `bec4589` — the second defect being measured. |

Everything else (offset multiplier / min / max, dead band, hold queue, hold-below, and the
preset each named arm selects) is the first run's value, byte for byte. The full env of every
arm is embedded in `runtime/tournaments/i492_tournament-1.json` once the run starts, and
`arms_source` names the file it came from.

## Arms

| Slug | Base | Distinguishing env |
| --- | --- | --- |
| control-1 | control | dynamic offset off, stop 4.5¢ |
| conservative-1 | conservative | mult 0.75, 2–5¢, stop 3.0¢, preset |
| balanced-1 | balanced | mult 0.50, 2–4¢, stop 5.0¢, preset |
| aggressive-1 | aggressive | mult 0.25, 2–3¢, stop 6.5¢, preset |
| prudent-1 | prudent | mult 0.60, 2–4¢, stop 3.8¢, preset |
| stickhold-1 | stickhold | mult 0.25, 2–3¢, hold queue 0, hold-below 0, stop 6.5¢ |
| queuehug-1 | queuehug | mult 0.50, 2–4¢, hold queue 800, hold-below 1.0, stop 5.0¢ |
| deadband-tight-1 | deadband-tight | mult 0.75, 2–5¢, dead band 0.01, queue 500, below 0.08, stop 3.0¢ |
| deadband-wide-1 | deadband-wide | mult 0.50, 2–4¢, dead band 0.06, queue 500, below 0.08, stop 5.0¢ |
| snapstop-1 | snapstop | mult 0.25, 2–3¢, queue 500, below 0.08, stop 3.0¢ |

## Launch (operator)

```
python -m scripts.shadow_tournament --issue 492 --minutes 720 --run-number 1 --dashboards
```

One 12-hour window, ten arms. Artifacts:

- `runtime/tournaments/i492_tournament-1.json` — plan record, with `arms_source` and every
  arm's env
- `runtime/tournaments/i492_tournament-1.log` — launcher log
- `data/i492_tournament-1_<slug>-1.db` × 10
- `reports/tournaments/i492_tournament-1_results.json` — per-arm summary at the end

Watch: dashboards 8801–8810, one per arm; `python -m scripts.tournament_hub` (port 8798) reads
the plan record. The cockpit (`python -m scripts.tournament_cockpit_server`, port 8800) still
serves the #484/#485 stores — it is the "before" side, not the rerun.

A second run must bump `--run-number 2`. The launcher refuses an existing store, which is what
stops a rerun from appending to a finished arm.

## What the comparison must report

Per arm: fills, merges, single-leg exits, realized PnL. Plus the hard-stop audit — every
`lifecycle_hard_stop` close must carry `trigger_bid` and `fresh_bid` agreeing within the 2-cent
sell slippage. An arm where they disagree by more than that is a reopened defect, not a
result. State the stop translation and the lost grace dimension next to the numbers.
