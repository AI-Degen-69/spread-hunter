# Audit: decide whether to raise the entry edge floor (Issue #787)

**Verdict: keep the current floor (`BOT_ENTRY_FILL_QUALITY_GATE_ON=0`, empty label
list).** The thin-edge signal is real and points the right way, but it fails the bar
that was fixed before the numbers were read — the removed bucket has 16–17 closed
positions per tape against a 30-position bar, and the direction reverses inside two of
the confound bands. The candidate to revisit is a 3¢ floor
(`BOT_ENTRY_FILL_QUALITY_BLOCKED_EDGE=0.00-0.02,0.02-0.03`).

**The larger finding is not about the floor.** On both tapes the 3–4¢ bucket — the one
with enough observations to count as a finding — loses money too (−$6.35 and −$14.22
against 35 and 40 closed positions). Blocking the thin bucket roughly halves the loss
and leaves the book still negative. A floor is not the fix this data is asking for;
the next look should be at why the *mid* bucket loses (fees, single-leg exits, merges
whose pair cost sat at the ceiling).

## What was asked

Does realized money rise with the entry edge, and if so, where should the entry floor
sit? Three surfaces were named in the issue's open questions; this audit answers them
from the code, not from a guess:

1. **Which "EV" surface.** The fill-quality edge buckets: `quotes.edge_vs_mid` in
   **dollars per share**, boundaries `EDGE_EDGES = (0.0, 0.02, 0.03, 0.04, 0.06, ∞)`
   (`core_brain/fill_quality_report.py:64`). The operator's "<1% / >10%" figures do not
   match these dollar buckets; `bot_eval_stat_gate_threshold_pct` is a different
   mechanism (a percent stat gate) and carries no per-edge outcome table. A relative
   (percent-of-price) view is a follow-up, not this audit.
2. **Data window.** Every run whose entries carry an edge, as the issue's default says.
   The live registry `data/orders.db` is **empty** (0 orders, 0 fills, 0 closes), so
   the window is the two shadow tapes recorded on 23-10.
3. **Decision rule.** Fixed before the numbers were read — see
   *The locked decision rule, applied*.

## Where the data came from (and what it is not)

| Tape | Store (snapshot) | Run id | Fills | Positions | Closed | Inclusive PnL |
|---|---|---|---|---|---|---|
| shadow-01 | `reports/edge_floor_shadow-01/snapshot.db` | `shadow-01` | 92 | 55 | 53 | **−$21.65** |
| shadow-01-prudent | `reports/edge_floor_shadow-01-prudent/snapshot.db` | `shadow-01-prudent` | 104 | 61 | 59 | **−$25.74** |

Both tapes are **rehearsal, not evidence about live performance.** The shadow fill
model credits an order when a trade prints at or through its price, so its fills are
optimistic relative to a live queue. The two tapes also ran **different presets**
(`shadow-01`, `shadow-01-prudent`) and are therefore reported side by side and never
summed.

The source stores were still being appended to when this audit ran, so each was frozen
with SQLite's backup API into the snapshot named above; every number in this document
is reproducible from that snapshot and is not a moving target. `data/orders.db` was
read but never written; all analysis opens stores with `mode=ro`.

## Method

- **Unit = the position**, not the fill. One position is one `pair_key`
  (`orders.pair_id`, else `condition_id`); its PnL is the sum of the legs'
  `attr_pnl`, and its edge is the **weakest leg's** edge — the leg a floor would have
  removed.
- `closes` carries no pair, fill, or token identity, so a close is attributed to a
  position by `(run_id, condition_id)` and shared pro-rata by filled shares. That is
  **inferred, never exact**, and it is the main attribution limit here.
- **Pair price paid** is the legs' fill prices summed — the per-share pair cost when the
  legs are one-for-one. A stranded single-leg position contributes only its own leg's
  price, so the cheap band also collects the naked entries; that is a limit of this
  split, not a finding about cost.
- Bookkeeping closes (`venue_sync`) are excluded; settlement, merge and rescue closes
  are kept. No bookkeeping close appeared in either tape.
- A win is a closed position with PnL > 0; a still-open position is counted in the
  position total and left out of the win-rate denominator.
- Data-quality counts for both tapes: 0 orphan fills, 2 open positions each, 0
  unmatched closes, 0 bookkeeping closes excluded. `shadow-01-prudent` has 1 position
  whose weakest leg carries a **negative** edge.

## Bucket table — `shadow-01`

| Bucket | Positions | Closed | Win rate [Wilson 95%] | Mean | Median | Total | Status |
|---|---|---|---|---|---|---|---|
| 0.00-0.02 | 0 | 0 | — | — | — | — | no observations |
| 0.02-0.03 | 18 | 17 | 35.3% [17–59] | −0.81 | −0.09 | **−13.77** | insufficient |
| 0.03-0.04 | 36 | 35 | 68.6% [52–81] | −0.18 | +0.21 | **−6.35** | OK |
| 0.04-0.06 | 1 | 1 | 0.0% [0–79] | −1.53 | −1.53 | −1.53 | insufficient |
| 0.06+ | 0 | 0 | — | — | — | — | no observations |

## Bucket table — `shadow-01-prudent`

| Bucket | Positions | Closed | Win rate [Wilson 95%] | Mean | Median | Total | Status |
|---|---|---|---|---|---|---|---|
| negative | 1 | 1 | 100.0% | +0.28 | +0.28 | +0.28 | insufficient |
| 0.00-0.02 | 0 | 0 | — | — | — | — | no observations |
| 0.02-0.03 | 17 | 16 | 62.5% [39–82] | −0.73 | +0.27 | **−11.72** | insufficient |
| 0.03-0.04 | 41 | 40 | 65.0% [50–78] | −0.36 | +0.21 | **−14.22** | OK |
| 0.04-0.06 | 2 | 2 | 50.0% [9–91] | −0.04 | −0.04 | −0.07 | insufficient |

Read together: the median position is **profitable in the mid bucket on both tapes**
(+21¢ and +21¢, and +27¢ in the thin prudent bucket), while the mean is negative in every
bucket — a small number of large single-leg losses drags the average down. The thin
bucket carries the worst average on both tapes, which is exactly the operator's
observation. But the mid bucket is not profitable either, and that is where most of the
money is lost simply because it holds most of the positions.

## The crossover

**No crossover exists on either tape.** A crossover is the lowest boundary where every
bucket at or above it is non-insufficient *and* earns a positive mean. That requires
30 closed positions in every bucket above the line; `0.04-0.06` has 1 and 2. The
mid bucket's negative mean rules it out independently.

## Floor sweep

The gate can only block **labels**, so a floor is the label list below it.

`shadow-01` (284 posted quotes):

| Floor | Labels blocked | Positions dropped | PnL dropped | Positions kept | PnL kept | Kept win rate | Quotes below floor |
|---|---|---|---|---|---|---|---|
| 0.02 | `0.00-0.02` | 0 | +0.00 | 55 | −21.65 | 56.6% | 5 |
| **0.03** | `0.00-0.02`, `0.02-0.03` | 18 | **−13.77** | 37 | −7.88 | **66.7%** | 51 |
| 0.04 | + `0.03-0.04` | 54 | −20.12 | 1 | −1.53 | 0.0% | 257 |
| 0.06 | + `0.04-0.06` | 55 | −21.65 | 0 | +0.00 | — | 267 |

`shadow-01-prudent` (349 posted quotes):

| Floor | Labels blocked | Positions dropped | PnL dropped | Positions kept | PnL kept | Kept win rate | Quotes below floor |
|---|---|---|---|---|---|---|---|
| 0.02 | `0.00-0.02` | 0 | +0.00 | 61 | −25.74 | 64.4% | 18 |
| **0.03** | `0.00-0.02`, `0.02-0.03` | 17 | **−11.72** | 44 | −14.02 | **65.1%** | 78 |
| 0.04 | + `0.03-0.04` | 58 | −25.94 | 3 | +0.20 | 66.7% | 320 |
| 0.06 | + `0.04-0.06` | 60 | −26.02 | 1 | +0.28 | 100.0% | 334 |

A 3¢ floor drops 18 of 55 positions (33%) and 51 of 284 posted quotes (18%), and lifts
the win rate of what remains from 56.6% to 66.7%. The dollars it removes
(−$13.77 / −$11.72) are real losses, not foregone profit.

## Paired gate A/B (same tape, both settings)

`core_brain.fill_quality_report --ab --block edge:<label>`, replayed over the same
recorded tape — the paired comparison, not two runs sampling different markets.

| Floor | shadow-01 off → on | delta | pairs excluded | shadow-01-prudent off → on | delta | pairs excluded |
|---|---|---|---|---|---|---|
| 0.02 | −21.65 → −21.65 | +0.00 | 0 | −25.74 → −25.74 | +0.00 | 0 |
| **0.03** | −21.65 → **−8.10** | **+13.55** | 10 of 55 | −25.74 → **−11.57** | **+14.17** | 11 of 61 |
| 0.04 | −21.65 → −1.53 | +20.12 | 54 of 55 | −25.74 → +0.14 | +25.88 | 58 of 61 |
| 0.06 | −21.65 → +0.00 | +21.65 | 55 of 55 | −25.74 → +0.00 | +25.74 | 61 of 61 |

**The empty-book trap.** The 4¢ and 6¢ floors look best precisely because they delete
the book: a run that places almost no pairs cannot lose money. Treat only the 3¢ row as
a real candidate — it keeps 37 and 44 positions while cutting the loss by about
two-thirds.

**Single-leg cost.** Blocking is per intent, not per pair
(`tests/test_fill_quality_gate.py::test_blocking_one_leg_still_posts_the_other_and_cancels_the_resting_leg`):
the leg below the floor is dropped, its opposite leg still posts, and the resting order
on the dropped token is cancelled as `not_quoted`. In the 3¢ replay that produced 8
and 6 "single-kept" pairs — the exact state the strategy exists to avoid. Any future
floor decision has to price that in.

## Confounds — is it edge, or is it size / pair cost / category?

Each band is split at the same edge cut (the median position edge), so the comparison
is consistent across bands. "Holds" means the high-edge half earns more than the
low-edge half *inside that band*; "inconclusive" means a band was too thin to measure.
Pair price paid stands in for the issue's "spread" confound — what a pair costs to
assemble is where the spread, the fee and the queue all land.

| Split | shadow-01 | shadow-01-prudent |
|---|---|---|
| Shares filled across both legs (≤ 12 / > 12) | **holds** (−1.28 vs −0.19; +0.11 vs +0.37) | **holds** (−1.60 vs −0.15; +0.23 vs +0.30) |
| Pair price paid, legs summed (≤ 0.93 / > 0.93) | **holds** (−1.35 vs −0.07; −0.15 vs +0.17) | **fails** (+0.09 vs +0.01 in the > 0.93 band) |
| Category | **fails** (Basketball n=2 and Tennis n=13 reverse; the rest hold) | **fails** (Counter-Strike 2 n=5 and Tennis n=21 reverse) |

So the effect survives size on both tapes, survives pair cost on one, and does **not**
survive category on either — Tennis reverses it on both. Category is display-only
metadata derived from the current title/slug (`market_meta.classify_display_category`),
not from metadata at entry time, and the per-category counts are 1–21 positions, so
this is a flag on the trend rather than a settled confound. It is enough to stop a
floor from shipping on this data.

## The locked decision rule, applied

Raise the floor only if **all three** hold:

| # | Rule | shadow-01 | shadow-01-prudent | Verdict |
|---|---|---|---|---|
| 1 | Every removed bucket has ≥ 30 closed positions | 17 in `0.02-0.03` | 16 | **fails** |
| 2 | Paired A/B delta > 0 | +13.55 | +14.17 | passes |
| 3 | The direction holds in all three confound splits | category fails | pair cost + category fail | **fails** |

**Outcome: keep current.** Two of the three tests fail, and rule 1 is a sample-size
bar, not a judgement call. Pooling the two tapes would reach 35 positions in
`0.02-0.03`, but they ran different presets, so pooling would mix configurations — the
honest read is "re-run this when one tape reaches 30 closed positions in the bucket".

## Proposal

**Keep `BOT_ENTRY_FILL_QUALITY_GATE_ON` unset and `BOT_ENTRY_FILL_QUALITY_BLOCKED_EDGE`
empty.** No live setting changes today.

The candidate to revisit, named so the next run does not re-derive it:

```
BOT_ENTRY_FILL_QUALITY_GATE_ON=1
BOT_ENTRY_FILL_QUALITY_BLOCKED_EDGE=0.00-0.02,0.02-0.03
```

Its projected effect on this rehearsal data, if it ever clears the bar:

| | shadow-01 | shadow-01-prudent |
|---|---|---|
| Net PnL on the same tape | −21.65 → **−8.10** (+13.55) | −25.74 → **−11.57** (+14.17) |
| Win rate of retained positions | 56.6% → **66.7%** | 64.4% → **65.1%** |
| Positions dropped | 18 of 55 (33%) | 17 of 61 (28%) |
| Posted quotes filtered | 51 of 284 (18%) | 78 of 349 (22%) |
| Book after the floor | still **negative** | still **negative** |

## Shadow rehearsal at the candidate settings

One 5-minute `shadow_run` with the candidate floor loaded, on a fresh store — the
end-to-end proof that the setting parses, the gate fires, and the loop exits clean. It
is *not* the comparison (that is the paired A/B above), because a second fresh run
samples different markets.

```
BOT_ENTRY_FILL_QUALITY_GATE_ON=1
BOT_ENTRY_FILL_QUALITY_BLOCKED_EDGE=0.00-0.02,0.02-0.03
python -m core_brain.shadow_run --minutes 5 --max-markets 5 \
  --db data/01_shadow_787_floor.db --run-id shadow-787-floor --starting-bankroll-usd 100
```

| | Result |
|---|---|
| Exit code | **0** |
| Code under test | `23d792c9` (the commit that carries this audit) |
| Duration / rotations | 5 minutes, 283 cycles, `rotations_returned=5` |
| Fleets | `quoted=3`, `declined=1`, `errors=0`, `intents_recorded=26`, `skipped=reconcile` |
| End state | 26 orders (19 cancelled, 3 filled, 4 open), 3 fills, 1 close, **+$0.40** |
| Open inventory at shutdown | 1 position: a lone filled 10-share leg @ $0.76, no opposite leg |

**The setting loads.** The rehearsal builds its config through `config.load()` →
`MakerConfig.from_env()`, and that same call with these two variables exported returns
`gate_on=True` with `('0.00-0.02', '0.02-0.03')`.

**The floor never had to fire — say it plainly.** The smallest edge posted all run was
3.00¢, and *zero* posted quotes fell below 3¢ (`quotes below floor 0` at the 0.03 sweep
row). So this run proves the two labels parse, the gate is armed, and the loop exits
clean — it does **not** prove the floor removes anything. On these five markets the
filter had nothing to remove.

**The one logged warning, and its cause.** `pair-a55b6be00c25` repeats 185 times:
`pair_id=... does not identify one UP token and one DOWN token in the quotes ledger`. Its
condition has a single UP quote and a single filled UP order — the pair never had a DOWN
side. That leg is the open inventory above, and the gate is not the cause (it blocked
nothing this run). Rehearsal error count is 0; the warning is the lifecycle reporting the
half-pair, not a crash.

**Reading the two positions.** 2 positions: one open at 0.03-0.04, one closed at 0.04-0.06
for +$0.40 (`shadow_merge`). Too few to compare against any bucket in the tables above,
which is exactly why the paired A/B replay is the comparison and this run is not.


## Follow-ups needing sign-off (not done here)

1. **The negative edge is unreachable by a label floor.** `edge_bucket_for(-0.01)`
   returns `"0.06+"` — a negative edge is labelled as the *top* bucket, so no label
   list can block it, and `plan_orders` never sees `"negative"`. One such position
   exists on `shadow-01-prudent` (+0.28, so it would not have been blocked anyway).
   Fixing the label is a live-behaviour change and belongs in its own issue.
2. **Missing edges are unblockable too.** A position with no book quote buckets as
   `"unknown"`; a floor built from edge labels lets it through. Zero such positions in
   these tapes.
3. **Gate refusals are not persisted.** Opportunity counts here mean *posted quotes*,
   not unique opportunities — a refused intent leaves only an in-memory reason.
4. **A relative (percent-of-price) edge view** would answer the operator's original
   "<1% / >10%" framing; the dollar buckets do not.
5. **The live registry is empty.** Once real closes accumulate, this same script run
   against `data/orders.db` (no `--db`) is the live answer; `--db` pointing anywhere
   else prints the rehearsal banner.
6. **Why the 3–4¢ bucket loses money** is the question this data actually raises
   (fees, single-leg exits, pairs assembled near the cost ceiling). Out of scope here.

## Operator live step (only if a future audit recommends a change)

Set both settings in `.env`, then restart the trader:

```
BOT_ENTRY_FILL_QUALITY_GATE_ON=1
BOT_ENTRY_FILL_QUALITY_BLOCKED_EDGE=0.00-0.02,0.02-0.03
```

**Undo:** set `BOT_ENTRY_FILL_QUALITY_GATE_ON=0` and restart. The gate only removes
entries, so **added dollars at risk are $0** — it cannot open a position the current
configuration would not have opened. Its real risk is the single-leg state described
above, not extra exposure.

## Reproduce

```
python scripts/edge_floor_analysis.py --db reports/edge_floor_shadow-01/snapshot.db --run-id shadow-01
python scripts/edge_floor_analysis.py --db reports/edge_floor_shadow-01-prudent/snapshot.db --run-id shadow-01-prudent
python -m core_brain.fill_quality_report --shadow-db reports/edge_floor_shadow-01/snapshot.db --run-id shadow-01 --ab --block edge:0.00-0.02 --block edge:0.02-0.03
```

The snapshots are gitignored (`reports/`), so a fresh clone has no tape to read; point
`--db` at any shadow store instead. Both scripts open stores read-only.

## How to verify

1. Run the first command above. Expect a bucket table, a `crossover: none` line naming
   `min_obs=30`, a floor sweep with the exact label lists, and the
   `rehearsal / not evidence` banner. A traceback is a failure.
2. Run the third command. Expect `gate off: -21.65 gate on: -8.10 delta: +13.55` —
   the same tape scored both ways.
3. Open `reports/edge_floor_shadow-01/edge_floor.md` and
   `reports/edge_floor_shadow-01-prudent/edge_floor.md`. Each is the same table rendered as
   a file: the `store:` line names the snapshot and the fill count, the next line is the
   `rehearsal / not evidence` banner, and further down is the `crossover: none` line naming
   `min_obs=30`.
