# Ladder trial #333: what the 4-hour live-books run proves — 2026-10-02

[Issue #333](https://github.com/AI-Degen-69/spread-hunter/issues/333) asked for a
~4-hour paper run of `scripts/ladder_live_books_trial.py` over live BTC/ETH
5-minute books, and a probe-vs-live write-up answering **whether the live numbers
change anything about the probe verdict** (shape 2 / exit_60). The trial ran to
completion. The write-up that follows is not the one the ticket expected.

**Result in one line: 274 orders across two live-books segments produced zero
fills, and the reason is not the market — every rung was priced at a constant
0.48/0.49 while the books being quoted traded at 0.56–0.74, so no rung ever
reached the touch ([#339](https://github.com/AI-Degen-69/spread-hunter/issues/339)).
The probe's 69–75% pair rate cannot be compared against that zero, because the
probe priced different rungs (0.45/0.55) through a fill rule that treats a
once-a-minute price touch as an immediate full fill. Neither number is a
fill-rate measurement of this strategy.**

---

## What ran

| | | |
| --- | --- | --- |
| Attempt 1 | `reports/ladder_live_books_333_20261002_0041.json` | ~00:41. No placements: `"note": "no markets inside the open window during the trial"`. Store not retained. |
| Segment A | run `ladder-live-1790907579`, store `data/NN_shadow_ladder_333_mine.db` | 05:19:39 → 06:05:35, 45.3 min. **Interrupted**: a host-level process cleanup killed every `python.exe` on the machine, taking the trial with it (and the dashboard and the market filter). The store was left intact and readable — SQLite had flushed every cycle — and is cited below as the first segment. |
| Segment B | run `ladder-live-1790910938`, store `data/NN_shadow_ladder_333_20261002_0615.db` | 06:15:38 → 10:15:46, **240.1 min, complete**. Report `reports/ladder_live_books_333_20261002_0615.json`, committed alongside this document. |
| Build | `aed268c` + the #338 dashboard merge `56b6734` | |
| Config both segments | `ladder_mode`, `ladder_rungs=2`, `ladder_exit_sec=60`, `ladder_budget_usd=5`, `ladder_open_window_sec=30`, interval 5 s, no signer, `live_execution: false` | |

Segment B's heartbeat read `finished: true` at cycle 1503 with a 4-second-old
timestamp; the run ended on its own clock, not on a kill.

## The picture

| measure | probe, shape 2 / exit_60 | segment A (45 min) | segment B (240 min) |
| --- | --- | --- | --- |
| markets | 200 (BTC) / 200 (ETH) | 20 | 96 |
| rungs priced | **0.45, 0.55** | **0.48, 0.49** | **0.48, 0.49** |
| orders | n/a — no queue model | 51 | 223 |
| fills | 200/200 markets saw at least one | **0** | **0** |
| markets with a complete pair | 138/200 BTC (69%), 150/200 ETH (75%) | 0 | 0 |
| markets carrying an unpaired remainder | 111/200 BTC, 113/200 ETH | n/a | n/a |
| conservation | — | 0 orphans | 0 orphans, 0 overfill, 0 double-count |
| mean PnL per market | 0.337 BTC, 0.305 ETH | — | 0.0 |

Segment A and segment B are the same measurement twice: the same two rungs, the
same cancellation reason, the same zero. Segment B differs only in being four
times longer and completing its box.

## Why zero fills: the rungs never reached the market

Full evidence in [#339](https://github.com/AI-Degen-69/spread-hunter/issues/339);
the numbers that matter here, from segment B:

- The ladder prices rungs off a constant: `price = round(0.50 - i * tick, 4)`
  (`core_brain/quotes.py:523`). It never reads the book's price. Two rung prices
  exist in the whole run: **0.48 and 0.49**.
- The books being quoted were nowhere near 0.50. Across 370 book observations
  the real best bid ranged **0.24–0.92**, and our rung sat a **median of 15–16
  cents behind the touch** (worst case 44c). Worked example,
  `btc-updown-5m-1790911800`: best bid **0.68**, our rungs 0.48 and 0.49.
- The tape printed at a rung price **0 times in 370 observations**. This is the
  measurement that explains the zero: the shadow fill model credits only tape
  printed at an order's exact price, and no such tape existed to credit.
- Orders lived a median of **16.0 s** (min 6.5 s, max 35.9 s); 113 of 223 were
  observed by the book exactly once. `ladder_exit_sec = 60` was never the
  binding constraint — the loop cancelled and re-posted long before it, and
  re-posted **at the identical prices** (on `btc-updown-5m-1790911200`: posted
  5.1 s after open, cancelled at 12.0 s, re-posted at 12.1 s, again at 24.8 s).
- Queue taken at submit: median 185 shares ahead, maximum 6,796.

The zero is therefore arithmetic, not a market verdict. A resting bid 15 cents
behind the touch, held for sixteen seconds, receives no tape at its own price —
under the live fill engine, which requires the venue to say so, or under the
shadow engine, which requires tape to say so.

### What was ruled out

`discover_ladder_series` is healthy. Measured directly against Gamma during this
work: a market enters its open window ~3 s after `start_ts` and stays visible for
the full 30 s across **6 consecutive polls** at the trial's 5 s interval. The
routine `markets_fn returned no markets` INFO lines in the trial log are the
expected ~90% of rotations that fall between 30-second windows, not a discovery
failure. The feed is not starved: at the time of writing the two series expose
100 open markets each, and 96 distinct markets were quoted during the trial.

## The probe never tested what shipped

This is the answer to acceptance criterion 4, and it is why the probe verdict is
neither confirmed nor refuted by the trial. Four independent differences:

| | probe (`scripts/ladder_probe.py`) | what shipped (`plan_ladder_quotes`) |
| --- | --- | --- |
| Rung prices | `SHAPES["2"] = (0.45, 0.55)` — straddling 0.50, summing to exactly 1.00 | `(0.49, 0.48)` — both below 0.50, summing to 0.97 |
| Fill rule | `if price <= rung: fill` — the first sample at or through the rung fills the order **in full**, with no queue, no volume and no counterparty | `credit_fills` — tape at the rung's **exact** price, after `queue_ahead` is consumed |
| Fill price | the **sampled** price, not the limit: a rung at 0.45 books a fill at 0.325 in the worked example below | the rung's own price |
| Book resolution | **5 samples per 5-minute market** — 399 of 400 legs in each tape hold exactly 5 ticks, one per minute | book polled every 5 s, with `best_bid`/`level_size`/`traded` recorded per mark |
| Resting exposure | the order rests the whole window; the probe has no cancel and no re-post | median lifetime 16.0 s, cancelled and re-posted at the same price |

The tape itself is the sharpest of these. Both probe tapes hold 2001 ticks across
400 legs — `data/ladder_tape_btc5.db` and `data/ladder_tape_eth5.db`, collected
2026-09-30 by `scripts/ladder_tape_collect.py`, which backfills *"each leg's
minute tape over the market's own window"*. One leg reads:

```
btc-updown-5m-1790790300   [(1790790316, 0.325), (1790790375, 0.465), (1790790431, 0.755), (1790790495, 0.770), (1790790555, 0.995)]
```

Five points for a five-minute market. Applying the probe's own rule to it: rung
0.45 finds its first sample ≤ 0.45 at t+16 s (0.325) and fills **at 0.325**; rung
0.55 finds the same sample and fills at 0.325 as well. One minute of a price path
becomes two full fills, 12.5 cents better than either rung. That mechanism, not
any measured edge, is what produces `any_fill: 200/200` and a 69–75% pair rate.

## Evidence bundle for #333

| acceptance criterion (#333) | evidence | where |
| --- | --- | --- |
| Trial ran ~4h against live BTC/ETH 5-min books, no signer, zero venue writes | Segment B ran 240.1 min to `finished: true` on 96 live markets; every report carries `"live_execution": false`; the trial refuses a populated store and never builds a signer | report JSON, committed here |
| Report JSON + probe-vs-live write-up saved where they can be read (not only on the running machine) | This document is tracked; the report it cites is committed beside it as `2026-10-02-ladder-333-live-books-report.json` | `docs/runs/` |
| Conservation holds with zero orphans | `orphan_fills: 0`, `overfilled_orders: 0`, `double_counted_fills: 0`, `accounted_shares: 0.0` on a run that placed 223 orders and filled 0 — the trivially-clean case, stated as such because zero fills cannot exercise the conservation path | report JSON |
| Write-up answers whether the live numbers change anything about the probe verdict (shape 2 / exit_60) | Answered above and in the section below: **the live numbers do not test the probe verdict.** The probe priced rungs at 0.45/0.55 that were never quoted, through a fill rule this venue does not implement, against a 5-sample-per-market price path. The trial quoted 0.48/0.49 at a median 15–16c behind the touch. Different rungs, different fill model, different data — no comparison is available. | this document |

## What this changes

**The probe verdict is untouched, and it is also unsupported.** Shape 2 /
exit_60 was chosen on a model whose fill rule is "the price touched my level, so
I own it in full", applied to one sample per minute and booked at the sampled
price. That model cannot distinguish a strategy that fills from one that never
could, because it does not model the two things that decide fills here: queue
position and whether the market trades at your price at all. Its own numbers
already hint at this — 111 of 200 BTC markets ended with an unpaired remainder
even under the generous rule.

**The live run's zero is not a market verdict either.** The rungs were priced
15 cents from the touch. Until [#339](https://github.com/AI-Degen-69/spread-hunter/issues/339)
is settled, no run of this ladder measures fill rate, and this trial should not
be cited as evidence for or against the strategy's economics.

**What the trial does establish, at the level of machinery:**

- The live-books trial runner works end to end on a 4-hour box: discovery,
  quoting, rotation, cancellation, book telemetry, report writing, and a clean
  exit on its own clock.
- The `queue_marks` telemetry added for this work is what made the diagnosis
  possible. Without `best_bid`, `level_size` and `traded` recorded per resting
  level, a zero-fill run is indistinguishable from a subtle accounting bug, and
  the first pass at this died on exactly that ambiguity.
- The pair-discipline machinery is untested by these segments: with zero fills
  there were no pairs to assemble, no merge legs, no ladder exits, and no
  single-buy exposure to defend against. Those paths still have no live-books
  evidence.

**Next step, in order:** settle [#339](https://github.com/AI-Degen-69/spread-hunter/issues/339)
(price the rungs from the book, or refuse to ladder a market whose mid is far
from 0.50), then re-run the same 4-hour box. A run with rungs near the touch
will produce fills, pairs, residues and cancels the conservation path can
actually be tested against — which is the evidence #333 was originally written
to collect. Re-pointing the probe at book-derived rungs and a book tape is a
separate piece of work, and until it is done the probe's 69–75% should not be
quoted as a fill-rate baseline.

## Where the artifacts are

| artifact | path | tracked |
| --- | --- | --- |
| This write-up | `docs/runs/2026-10-02-ladder-333-probe-vs-live.md` | yes |
| Segment B report | `docs/runs/2026-10-02-ladder-333-live-books-report.json` | yes |
| Segment B store | `data/NN_shadow_ladder_333_20261002_0615.db` | no (gitignored) |
| Segment A store | `data/NN_shadow_ladder_333_mine.db` | no (gitignored) |
| Probe reports | `reports/ladder_probe_btc5.json`, `reports/ladder_probe_eth5.json` | no (gitignored) |
| Probe tapes | `data/ladder_tape_btc5.db`, `data/ladder_tape_eth5.db` | no (gitignored) |
| Segment B log | `logs/ladder_333_20261002_0615.err.log` | no (gitignored) |
