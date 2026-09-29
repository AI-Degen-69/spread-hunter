# Shadow-01 rescue exits: what the four worst losses are made of — 2026-09-29

[Run 153](2026-09-02-run153-grace-sweep.md) answered *when the companion leg
arrives*; [Issue #306](https://github.com/AI-Degen-69/spread-hunter/issues/306)
asked *why the rescue exits themselves cost what they cost*. Single-buy
exits consumed a large share of the strategy's profit, and the ticket put three
specific questions. This run builds the tool to answer them from recorded data
and applies it to the shadow-01 store.

**Result in one line: shadow-01 cannot answer Question 1 at all (no same-window
book tape), Question 2 is mostly unobservable from sampled marks (30 of 34
exits unresolved; nothing recorded says why any exit fired), and the largest
single rescue loss of all is not an exit — it is a one-sided leg held past the
exit window into settlement, the fail-closed gap.**

No rescue-policy change ships here. Nothing below calibrates one.

---

## What was built

| | |
| --- | --- |
| Report | `scripts/rescue_exit_report.py` — read-only, opens every store `mode=ro` |
| Instrumentation | `closes.reason` column (`adverse_drift` / `grace_expired`), written only after a successful venue sale; future runs record the trigger instead of forcing a reconstruction |
| Tests | `tests/test_rescue_exit_report.py` (16) + reason-persistence tests in `tests/test_dual_stop_loss.py`, `tests/test_single_buy_saver.py` |

The report reconstructs every `single_buy_exit` and `shadow_settlement` close
from orders, fills and quotes, ranks the loss tail, classifies Question 2 from
the sampled `queue_marks.best_bid` path, bounds Question 1 against an optional
book-tape store, and compares quote-time features for Question 3. It prefers a
recorded `closes.reason` over its own reconstruction — that column did not
exist when shadow-01 ran, which is exactly why Question 2 below is thin.

## Setup

| | |
| --- | --- |
| Store | `data/01_shadow_12-09_00-58.db` (run id `shadow-01`, git-ignored, opened read-only) |
| Book tape | **none for this window** — the book-tape stores on disk cover other runs |
| Method | aggregate report output only; no `data/` file is or was committed |

## The numbers

```
rescue closes: 37   total rescue loss: -$10.45   capital at risk: $77.15
  34 single_buy_exit   realized -$8.32
   3 shadow_settlement realized -$1.45 (net; one leg lost -$1.54, two small wins)
```

Top of the loss tail (worst first):

| pair | method | shares | paid | sold | P&L | share | fill→exit |
| --- | --- | --- | --- | --- | --- | --- | --- |
| pair-1aff1a45ef84 | single_buy_exit | 5 | 0.5100 | 0.20 | -$1.55 | 14.8% | 1.0 s |
| pair-33eac2d5688a | shadow_settlement | 2.8 | 0.5500 | — | -$1.54 | 14.7% | 10 452 s |
| pair-42bbccf244e3 | single_buy_exit | 6 | 0.2000 | 0.01 | -$1.14 | 10.9% | 1.7 s |
| pair-a3cc2fb6565f | single_buy_exit | 6 | 0.2700 | 0.13 | -$0.84 | 8.0% | 4.6 s |
| pair-33eac2d5688a | single_buy_exit | 2.2 | 0.5500 | 0.23 | -$0.70 | 6.7% | 2.1 s |

The two worst exits sold 5–6 shares for $0.01–0.20 within seconds of filling:
the heavy leg filled into a bid that was already gone. That is the gapped-book
shape, arrived at from the fill economics alone.

## Q1 — would a longer grace have caught the companion?

**Unanswerable from this store.** A grace answer needs the opposite leg's book
in the same window as the exits, and no book-tape store covers shadow-01. The
report emits `unanswerable from this store (no same-window book tape)` for
every exit.

What can be said comes from run 153, on its own stores: **4 of 13 companions
arrived, all within 1.5 s, and holding 5–120 s rescued nothing extra** — so a
longer grace buys nothing unless the book changed shape between runs, and
shadow-01's worst exits show a book that was already gone at fill time. The
shadow-01 store pins `single_buy_grace_sec` to 0.0 (the run-151 default at the
time), so no grace was in effect during these exits.

For any future rehearsal: record a same-window book tape
(`scripts/book_tape_recorder.py`), then the Q1 section becomes a measured
sampled upper bound instead of a refusal.

## Q2 — late trigger, or the bid was already gone?

**Mostly unobservable, and nothing recorded says why an exit fired.**

- `closes.reason` did not exist when shadow-01 ran, so every trigger must be
  reconstructed from sampled marks.
- Classifications across all 34 `single_buy_exit` closes: **4 `gapped`,
  30 `unresolved`** (marks missing from the fill→exit window or NULL
  `best_bid`). Zero `late_trigger`: no sampled path shows the bid still
  actionable a rotation after the trigger could first have fired.
- The four `gapped` exits are long-window legs (fill→exit 30 s – 1800 s) whose
  first crossing sample already sat at/below the sold price — consistent with
  expiry exits into a dead book, but sampled marks bracket changes, they do
  not observe them, and the poll phase is not recorded.

From here on the question closes itself: new exits persist `adverse_drift` or
`grace_expired` on the close, and the report prefers that recorded reason over
any reconstruction.

## Q3 — did anything at quote time separate the exits from the merges?

```
quote size   exits=5.2343   merged=5.2377
price        exits=0.4720   merged=0.4697
mid          exits=0.5007   merged=0.4987
edge vs mid  exits=0.0286   merged=0.0289
queue ahead  exits=6512.4   merged=6145.0
```

No recorded field separates the groups meaningfully — size, price and edge are
near-identical; queue-ahead is larger on exits but a few hundred shares of
queue is noise at this sample size. **n=37 rescue closes cannot calibrate a
gate.** The fields the question really needs are not recorded anywhere:
`t_remaining`, `latency_ms`, opposite-leg ask/depth, spread, volume, and
selection rejections.

## The gap the ticket surfaced: aged-out legs held into settlement

All **three** `shadow_settlement` closes on shadow-01 aged out — the leg sat
one-sided for 6 334 s, 9 053 s and 10 452 s (window: 900 s) with nothing outside
the rescue sweep to close it, into settlement:

| pair | shares | paid | P&L |
| --- | --- | --- | --- |
| pair-33eac2d5688a | 2.8 | 0.55 | **-$1.54** |
| pair-80b7c5b41368 | 0.014 | 0.20 | +$0.01 |
| pair-8fbf1bbb10f7 | 0.14 | 0.45 | +$0.08 |

The worst single rescue loss in the store is this gap, not an exit decision.
That is a live-behaviour change and gets its own issue and its own rehearsal —
**it is not fixed here.**

## Follow-ups recorded

1. **Pre-settlement hard exit** for one-sided legs past the exit window — new
   issue; rehearse on its own store before touching any config default. Any
   change that lengthens the hold must include a market-end-aware deadline.
2. **Same-window book tape** for future rehearsals, so Q1 becomes measurable.
3. **Reason column is live** from this change forward: the next rehearsal that
   takes a rescue exit answers Q2 directly.

## What would change the answers

A same-window book tape for shadow-01 (Q1 becomes an upper bound, possibly a
measured one), or a longer rehearsal on the new build (Q2 becomes a recorded
count instead of a reconstruction). Nothing in this store can.
