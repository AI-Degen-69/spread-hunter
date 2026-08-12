# The pairs-rule EV report — the whole measurement, one command

This is an explanation, not a reference. It walks what
`scripts/pairs_ev_report.py` measures, why the measurement is shaped the way
it is, and which numbers the desk should read off it. If you want the exact
per-section output, run it — from the repo root:

```bash
python -m scripts.pairs_ev_report
```

## The number that decides

The pairs-only rule lives and dies on one comparison:

```text
EV per one-sided fill = completion_rate × complete_gain − exit_rate × exit_cost
```

- **completion_rate / exit_rate** come from the rule's recorded decisions in
  `market_events` — PAIR_COMPLETE / NAKED_EXIT / PAIR_WINDOW_EXPIRED. The
  report counts the rule's own decisions, nothing else.
- **complete_gain / exit_cost** are the re-measured config constants
  (`strategy/config.py`: `pairs_complete_gain_cents` = 3.68, Sessions 44/46;
  `pairs_exit_cost_cents` = 3.67, Session 45). They are measured from closes,
  not estimated.
- **The verdict** is the same comparison the rule lives by: EV > 0 → the rule
  stays live. An empty database reads `NO DATA`, never a confident 0.

The dashboard's PAIRS-ONLY tile is the same formula on the same tables; the
report prints the same reading plus everything the tile cannot show — the
per-close distribution, the per-market capture, the outlier flags, and the
exit counterfactual.

## What the report reads

Three tables, all read-only (`mode=ro`, with `PRAGMA query_only` as fallback
so a WAL database without its `-shm` file still opens read-only):

- `market_events` — the rule's decisions, the EV denominator;
- `closes` — realized economics per close (naked exits, rule-era merge
  captures);
- `markouts` — the recorded 15-minute mids (`mid_h3`) behind the exit-vs-wait
  counterfactual.

The report never writes. It is safe to run against the live fleet database
mid-sweep.

## What the report deliberately does not do

The honest limits are printed in the report's METHOD section and are part of
the contract:

- **Merge capture is the whole pair.** `closes.realized_pnl` for a merge
  includes the spread earned on the passive leg that filled before the rule
  acted. The completion payoff is the pair-level number, not the marginal
  cost of the crossed leg alone.
- **EV decisions and close totals are counted independently.** The EV
  denominator (rule decisions in `market_events`) and the realized close
  totals are counted separately — the report never invents a fill-to-close
  join for EV accounting. The exit counterfactual is the deliberate
  exception: it links each naked-exit close to its triggering fill with the
  window-based join described below.
- **Natural pairs can slip into the rule-era slice.** A pair whose both legs
  filled passively also merges without a PAIR_COMPLETE event, and the slice
  (ts ≥ the first PAIR_COMPLETE) can include one. The per-market
  merges-vs-completions attribution table is the check.

## The exit-vs-wait counterfactual

The Session 49 exit card *inferred* what waiting would have produced from a
bid ladder. Session 50 made that a recording: every fill now carries a mid at
fill+900s (`mid_h3`), and because exits fire at age ≈ 0, the fill+900s
reading **is** the exit+15m reading for today's exit population. The report
prints, per naked exit, the recorded 15m mid against the exit price — plus an
aggregate: how many exits had the 15m mid *below* the exit price (exit beat
waiting), the median gap, and the mean 15m drift. The instrument itself is
described in [the markout-horizons explanation](explanation-markout-horizons.md).

Two things about the join are load-bearing and easy to get wrong:

- **The join is window-based, not id-linked.** `store.log_fill` stamps
  `time.time()` at INSERT while `log_markout_open` uses the sweep's captured
  `now` — the two timestamps differ by ~0.3s (measured 0.30–0.34s), so an
  exact match silently finds nothing. The report matches close→fill within
  10s and fill→markout within 30s. Validated against the Session 49 sample:
  all four triggering fills reproduced exactly (0.19/0.45/0.10/0.29, deltas
  0.08–0.17s).
- **A mid below the exit price is decisive; a mid above it by less than a
  spread is not.** The exit sold at the BID while `mid_h3` is a MID, so
  "waiting may have been better" needs more than a spread of room to be real.

Five honest states instead of a silent blank: `recorded` / `pending` (15m not
elapsed) / `no_markout` / `no_fill` / `no_column` (mid_h3 missing — the fleet
has not restarted since the Session 50 migration, so its database predates
it). mid_h3 cannot be backfilled: exits recorded before the migration will
read `pending` forever; the instrument records going forward only.

## How to run it

```bash
python -m scripts.pairs_ev_report                  # the full report, default run/fleet.db
python -m scripts.pairs_ev_report path/to/db.db    # a different database
python -m scripts.pairs_ev_report --json out.json  # the same data as UTF-8 JSON
```

The pending exit card's re-read is this one command once ~10 exits
accumulate. `tests/test_pairs_ev_report.py` pins the report against a temp
database seeded through the real write module — including the empty-DB case,
the IQR outlier flag, and the exit-counterfactual state ladder.
