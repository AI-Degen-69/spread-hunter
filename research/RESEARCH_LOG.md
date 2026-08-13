# Research Log — Maker (Polymarket BTC 5-min)

Running lab notebook. Newest entries at the bottom. Each entry:
**Question → Method → Result → Verdict**. Negative results are kept, not
deleted; most of what we have learned came from things that did not work.

**Conventions**
- Numbers here are measured, not estimated. If a figure is an estimate it says so.
- "Verdict" is a decision, not a summary: DEAD / PARKED / LIVE / OPEN.
- Instrumentation bugs get their own entries. On this project they have
  repeatedly been the difference between a real finding and a fake one.

The taker strategy lives in a separate repo (`polymarket-taker`) with its own
log. This file covers the maker only.

---

## Session 1 — 2026-07-21

**Context.** The taker strategy buys near-certainties at 0.80–0.99 and pays a
fee on every fill. A trader named @powerwinner showed a smooth upward equity
curve on the same markets, so the question was whether he does the same thing
better — or something else entirely.

### Is @powerwinner running our strategy?

**Method.** Pulled 56,768 of his BTC/ETH 5-min fills over 2026-07-14→21
(2,970 markets) and joined them to gamma resolutions.

**Result.** The opposite of ours on every axis.

| | powerwinner | our taker |
|---|---|---|
| entry price | 0.30–0.70 (54% of volume) | 0.80–0.99 |
| entry timing | 57% in the FIRST 40% of the window | last 40% only |
| trades at 0.98+ | zero | biggest size tier |
| market win rate | 41.4% (needs 56.1%) | ~92% |

His per-bucket win rates track breakeven within 1–3 points, mostly below it.
On direction alone he loses.

**Verdict.** DEAD — he is not a predictor, so copying his entries as a taker
would copy a losing bet.

### Then where does his money come from?

**Method.** Decomposed gross P&L against redeem records (ground truth) and
against the taker fee curve.

**Result.**

```
gross (payout − cost)              +$39,884 / week   ≈ +$171k/month
same trades charged our taker fee  −$32,501 / week
reported profile P&L                            +$182,797 / month
```

The entire difference between profit and loss is whether he pays taker fees.
He does not — he rests limit orders. Confirmations: his volume concentrates
where `fee ∝ p(1−p)` peaks (0.30–0.70), he enters early (passive orders need
time to be hit), and he never touches 0.98+.

An earlier hypothesis that his "buy both sides" behaviour was locked
arbitrage was **tested and rejected**: the pair costs 0.9990 against a $1.00
payout (0.10% margin) and is favourable only 51.1% of the time — a coin flip.
Spread capture, not arbitrage, is ~68% of his gross.

**Verdict.** LIVE — the mechanism is *rest, do not cross*. Built a Hunter sim
to test it.

### An earlier analysis pass that was wrong, and why

**Method.** First P&L join reported him at −$23,828.

**Result.** Resolution coverage was 59.1% for BTC versus 98.2% for ETH — 809
fetch failures from rate-limiting, dropped non-randomly. After recovering
809/810, computed payout matched his actual on-chain redeems to 0.1%
($2,745,682 vs $2,748,422).

**Verdict.** DEAD (fixed). Recorded because the wrong number looked entirely
plausible and contradicted the source of truth.

### How do you honestly simulate getting filled?

**Method.** First attempt keyed fills off the trade tape's `side` field: a
taker SELL lifts a resting bid.

**Result.** 194 of 200 tape rows are "BUY" — data-api reports each
*participant's* own side, not the aggressor's, so a maker's own fills appear as
BUYs. Aggressor direction is unrecoverable from that feed, and a SELL-only rule
would report almost no fills and wrongly kill the strategy.

Rebuilt on **book deltas**: the size resting at our price level is directly
observable, and its decrease is exactly the queue moving. Verified live —
levels move materially every few seconds (60 → 0 in 6s). Optimistic biases
(cancels counted as queue progress; assumed price-time priority) are documented
in `strategy/fills.py` rather than hidden; output is an upper bound.

**Verdict.** LIVE — seven unit tests cover queue precedence, sweeps, and
overfill.

### What actually drives maker P&L?

**Method.** 44 settled markets, grouped by inventory balance (min/max of the
two legs; 1.00 = perfectly hedged).

**Result.**

| balance | n | avg P&L | swing |
|---|---|---|---|
| 1.00 perfect | 12 | +$30.70 | 9.8 |
| 0.85–0.99 | 2 | +$20.31 | 6.4 |
| 0.60–0.85 | 17 | −$10.94 | 55.4 |
| <0.60 | 13 | −$50.95 | 7.3 |

Monotonic. Hedged markets totalled +$409; unbalanced −$848 — that gap is the
run's −$389. The tiny swing on the unbalanced group is the important part:
they lose *consistently*, not randomly. Our fills are adversely selected, so we
end up systematically heavy on the side that loses.

Two guards were blocking the balancing trade: the per-market cost cap returned
no quotes at all (logged ×1043), and the pair-cost cap rejected the hedge
whenever `avg(other) + price` reached $1.00.

**Verdict.** LIVE — both caps now apply only when adding to the heavy side.

### Regression introduced by that fix — OPEN

**Method.** Post-migration run in the new repo, 12 fills.

**Result.** Pair costs came out **above** $1.00 (1.0900, 1.0650, 1.0275,
1.0167) for something that pays exactly $1.00 — a guaranteed loss on the
hedged portion. Pre-fix runs were 0.93–0.96. Cause: allowing the balancing side
past the pair cap means that when both sides' asks are wide, buying both at
ask−1tick can sum above 1.00.

**Verdict.** OPEN — not fixed during the repo migration (structural work only).
Needs its own change and a fresh data run. Candidate fix: cap the hedge at a
price where the resulting pair stays under 1.00, and skip the hedge if no such
price exists.

### Instrumentation bugs found

- Four `maker.main` processes once ran concurrently against one database. Each
  keeps its own in-memory inventory, so the DB held the *sum* of several
  independent strategies — silently invalid data that still looked plausible.
  Added a single-instance pid guard; that run was archived as contaminated.
- Decision-log run-collapsing keyed on `reason`, whose text embeds live values
  (`t_remaining 4s`), so it collapsed almost nothing: 2.0× versus the taker's
  15×, 17,490 rows/day. Keying on `(market, action, side)` and logging once per
  cycle rather than per side took it to 7.8×.

**Verdict.** Both DEAD (fixed).

### Sample size reality

With σ ≈ 60× the per-market edge, the honest target is thousands of settled
markets, not dozens. The dashboard shows live progress toward 90/95/99%
confidence so this cannot be quietly forgotten.

**Verdict.** OPEN.

### Session 2 — 2026-07-22 (afternoon): the decisive experiment is now running

**Context.** The n=53 run measured a *contaminated* config (the hedge exemption let pairs run above $1.00), so it proved "the bug loses" and nothing about the maker mechanism. The single number that actually decides saveable-vs-dead — **how often do these 5-min BTC markets offer a fillable sub-$1.00 hedged pair at ask−1tick?** — had never been measured. This session fixes the bug and runs a fresh, instrumented experiment to measure exactly that.

**Method.**
1. *Fix the pair-cost cap.* In `strategy/quotes.py` the hedge exemption was removed: the cap `max_pair_cost = 0.995` now applies to **every** side (hedge or not). Additionally a fresh-market guard blocks opening *any* position unless BOTH sides can be filled at ask−1tick into a pair strictly under $1.00 — the bot now sits out wide markets instead of taking a guaranteed-loss directional leg.
2. *Add the decisive census.* `strategy/store.py` got a `hedge_census` table (one row per distinct market): was a fillable sub-$1.00 pair available at touch? `strategy/main.py` records it every cycle; `strategy/kpi.py` aggregates the fillable rate and median pair-at-touch.
3. *Define where the experiment ends.* `strategy/config.py` adds the stop condition:
   - **Phase A — Census:** observe `experiment_census_markets = 60` distinct markets. If the fillable-sub-$1.00 rate is `< hedge_fillable_min_rate = 0.50`, the instrument *cannot* be made profitably → **stop, DEAD**.
   - **Phase B — Verdict:** once census passes, settle `experiment_verdict_markets = 120` markets under the corrected strategy and read P&L sign + confidence → final save/lost call.
   The dashboard renders both the phase banner and the census progress live, so the run ends on evidence, not vibes.
4. Fresh DB, single instance, bot + dashboard started locally (venv rebuilt — a Hermes `PYTHONPATH` leak had been shadowing the project venv, so deps installed into the wrong site-packages; fixed with `env -u PYTHONPATH`).

**Result.** (Live, as started — n=1 so far, will replace with measured totals as it settles.)
- Bot running, single instance, fresh `hunter.db`.
- Census immediately recorded its first market as **fillable** with `median_pair_at_touch = 0.99` (under $1.00) — i.e. *this* market is makeable, contradicting the contaminated run's 4% figure. That is exactly the hypothesis under test.
- Pair cost now cannot exceed $1.00 by construction; the −4.2%-on-hedge loss mode is structurally closed.

**Verdict.** LIVE — the experiment is the decider, not this commit. Two outcomes are now possible and each is actionable:
- Census fillable rate `< 50%` → **DEAD**: the 5-min BTC series does not offer enough sub-$1.00 pairs at a fillable price; the mechanism cannot transfer from powerwinner.
- Census passes and Phase B P&L is positive with confidence → **the strategy is SAVEABLE** (though still below powerwinner's volume-driven edge).
A third outcome — census passes but Phase B loses — would point at the optimistic fill model (documented bias: cancels counted as fills), not the pair cost.

### Instrumentation bug found this session

- **Venv shadowing via `PYTHONPATH` leak.** The project's `.venv` was created, but because Hermes's `PYTHONPATH` was exported into the session, `pip install` and `python` resolved packages against Hermes's site-packages. `requests`/`uvicorn` import-failed at runtime while `pip` reported "already satisfied". Cured by launching every run with `env -u PYTHONPATH ./.venv/Scripts/python.exe ...`. General lesson for this Windows host: always unset `PYTHONPATH` before invoking a project venv.

**Verdict.** DEAD (fixed).

### Sample size reality

With σ ≈ 60× the per-market edge, the honest target is thousands of settled markets, not dozens. The dashboard shows live progress toward 90/95/99% confidence so this cannot be quietly forgotten.

**Verdict.** OPEN.

### Session 3 — 2026-07-22 (night): balance enforcement built, clean re-run

**Context.** A pure "sit out unless both sides fillable at entry" guard was rejected — the loss is a *partial fill*, not an entry decision. One side rests and fills, the other never does before the 5-min window closes → market settles one-sided and eats the full resolution loss. A passive maker cannot UN-fill an already-rested side, so the only enforceable balance rule is to **cross the spread for the missing leg near resolution**.

**Method.** In `strategy/main.py`, when `t_remaining ≤ balance_hedge_sec (20s)` and inventory balance `< target_balance (0.92)`, the bot cancels resting quotes and posts a *crossing* buy of exactly the missing shares to match the held leg. The fill is tagged `crossed=1` so `kpi`/`settlements` can distinguish a settlement hedge from a maker fill. `store` is self-healing (adds the `crossed` column to older DBs). Dashboard gained a **HEDGE X** card (count of settlement crossings). `config.balance_hedge_sec` documents the rationale.

**Result (design, pre-run).** Every settled market now settles *balanced by construction*. So when Phase B concludes, win/loss is an unambiguous verdict on the **instrument** — not on our execution. This converts the previously confounded result (was it imbalance or the instrument?) into an interpretable one.

**Re-run hygiene.** Per AGENTS.md a code change invalidates the sample, so the partial-DB runs were archived (`hunter.db.archived_20260722_225907/230650/235454/235939`) and `hunter.db` wiped. Bot + dashboard relaunched fresh on the new code with `env -u PYTHONPATH` (the Hermes venv-leak fix from Session 2 still required). Verified: equity $5,000, realized $0, hedges 0, Phase A_CENSUS, census median pair 0.995 (cap fix holding).

**Verdict.** LIVE — the run is the decider. Two outcomes, both actionable:
- Census `fillable_rate < 0.50` at 60 markets → **DEAD** (instrument doesn't fit these markets).
- Census passes AND Phase B (120 balanced markets) is positive with confidence → **SAVEABLE**.
- The HEDGE X count is now the leading indicator: if it climbs, partial-fill was the driver and we're fixing it; if it stays ~0, the books were already balancing and the loss is structural.

### Instrumentation bug found this session

- **`kpi.report()` scope error.** The `balance_hedges` query referenced a bare `c` cursor outside its `with db()` block → dashboard returned `{"error": "cannot access local variable 'c'"}` (HTTP 200, so the failure was silent in the UI). Fixed by routing through the existing `_rows()` helper. Lesson: a 200-with-error JSON is a real failure mode here; always probe the parsed state dict, not just the HTTP code.
- **MSYS PID vs native PID mismatch.** `ps`/`kill` in git-bash show translated PIDs that `taskkill` rejects ("not found"). The reliable kill path on this host is `powershell Get-CimInstance Win32_Process` to get the *native* PID, then `taskkill /F /PID <native>`. The port listener PID from `netstat -ano` (column 5) is also native and directly killable.

**Verdict.** DEAD (fixed).

### Session 4 — 2026-07-28: the fill rate, measured against the trade tape

**Question.** With the phantom-fill bug fixed, what is the real fill rate? Every
maker number recorded before the fix came from an engine that granted a full
fill to any order resting above the best bid, so "37.6% against real queue
depth" is void and the question was open.

**Method.** Two new scripts, so the answer is reproducible rather than a
one-off observation. `scripts/record_books.py` polls the live BTC 5-min market
(~1.7s/poll, both outcomes, full depth) into a standalone `books.db`.
`scripts/measure_fill_rate.py` replays those books through the fixed engine.
Raw books are strategy-independent, so every quoting rule is compared on the
SAME market data instead of on different hours.

An EPISODE — one resting order, held until repriced, cancelled, or the window
closes — is the denominator. Repost events are not: an order that sits at 0.52
for two minutes is one attempt to be filled, however many times the code
re-sends it.

**Result — the book-only model was still inventing most of the edge.** On the
first recorded windows it reported a **50% share fill rate**, of which
**100% came from a single branch**: "the level emptied and the best bid fell
below us, so credit our whole remainder". From bid-side deltas alone a mass
CANCELLATION is indistinguishable from a mass TRADE, so that branch is an
assumption, not a measurement — and it was carrying the entire result.

`scripts/fetch_trades.py` backfills the trade tape (~1,800 prints per 5-min
window) from data-api. The tape cannot recover the aggressor (it reports each
participant's own side — the reason `fills.py` abandoned it in Session 1), but
it does not need to: it says directly whether volume printed at our price.
`QueueFillEngine.on_book` now takes an optional `traded` map, giving

    book delta = trades + cancellations   (observed only as the sum)
    tape       = trades                   (measured)

Both advance our queue position; only trades can fill us.

| model | share fill rate |
|---|---|
| book-only (all prior maker numbers) | 50.0% |
| **tape-confirmed** | **3.1%** |

Same 4 windows, same books, same strategy — only the evidence standard changed.
A **16x** overstatement.

The tape also removes the opposite error: resting alone inside the spread makes
no bid-side delta at all, so those fills were previously invisible. Tape volume
at our price is visible either way.

**Verdict.** OPEN, trending bad. 3% is the honest passive fill rate at
ask-1tick. A hedged pair only pays if BOTH legs fill, so pair completion, not
single-leg fill rate, is the number that decides the strategy.

### Session 4 — the balance hedge never worked

**Question.** `main.py` placed the settlement crossing via `engine.post()` at
the best ask — a passive post, not a cross. Does it fill?

**Method.** Direct reproduction: post 150sh at the ask into a book that then
trades straight down through the level.

**Result.** **0 of 150 shares.** A bid resting alone at the ask has nothing
queued at its price, so no bid-side delta can be attributed to it. Before the
phantom-fill fix it filled instantly and completely — which is the only reason
the hedge ever appeared to work. Every market that went unbalanced stayed
unbalanced, and partial fills are the documented main loss driver.

**Fix.** `QueueFillEngine.cross()` models taking: walk real ask depth, at real
prices, in order, stop when the book runs out. A partial cross is a REAL
outcome — if the depth is not there the leg stays unhedged. `max_price` caps
how far up the book the hedge will walk so a thin book cannot drag it to 0.99.

**The fix has a price.** A cross is a TAKER order, and per
`research/market_spec.md` `taker_fee = shares * 0.07 * p * (1-p)`. At p=0.50
that is **1.75c/share against a ~1c edge on a hedged pair** — the fee peaks
exactly where this strategy trades. `kpi.py` charged nothing for it
(`pnl = payout - c  # maker pays no taker fee`), so realized P&L would have
read high once crossing actually worked. It now charges the fee, excludes
crossed shares from the fill-rate numerator, and stops crediting them a maker
rebate.

**Verdict.** OPEN — the hedge is honest now, but it costs more than the edge it
protects. Whether to hedge at all is a live question, not a settled one.

### Session 4 — two instrumentation bugs in this session's own tooling

Both were caught before they produced a "finding", which is the only reason
they are cheap.

- **Replay harness stopped quoting after a complete fill.** A fully-filled
  order is done, not resting, but the harness kept the episode open until the
  price happened to move — silently cutting how much was ever posted. Fixed by
  treating `not order.is_open` as stale.
- **Tape cursor advanced after the loop's `continue` paths.** Would have
  re-credited the same prints to the next interval. Moved to immediately after
  the fill step.

Tests went from 8 to 31 (`test_fills.py`, `test_quotes.py`,
`test_measure_fill_rate.py`). The harness is tested against scripted books with
hand-computed answers BEFORE being pointed at real data — twice on this project
a "finding" turned out to be the measuring code.

**Verdict.** DEAD (fixed).

### Session 4 — powerwinner's two missing rules, and the dashboard

**Price band** (`price_band_low/high`, 0.30-0.70) and **quote timing**
(`quote_window_frac`, first 40% of the window) are now enforced in
`decide_quotes`. Each has its own switch (`enforce_price_band`,
`enforce_quote_window`) so their effects are measured ONE AT A TIME — a
previous run changed two things together and the result could not be read.
`window_frac=None` means "clock unknown" and skips the timing rule rather than
gating every quote on a guess.

**Dashboard** gained the maker metrics it was missing: fill rate vs queue depth
at post, fill PROVENANCE (tape-confirmed vs inferred vs crossed), pair-cost
distribution, quote uptime with the top skip reasons, partial-fill exposure,
taker fees paid, and spread capture per share. `fills.reason` is persisted, so
provenance traces to a row. The schema migration moved out of `log_fill`'s
except branch into an idempotent step at connect time — the old one only ran if
a write failed first.

**Verdict.** LIVE.

### Archive commit — port-8788 single-bot pipeline parked

**Question.** The fleet on port 8800 has rendered the legacy single-market
pipeline on port 8788 (server/dashboard, server/kanban, strategy/main.py's
single-bot loop, scripts/run_fleet.py, deploy/run_service.py) functionally
dead on this host — the supervisor launches fleet + fleet_dash, and nothing
else. Should this code stay on the live branch, or be parked?

**Method.** Moved all five legacy files into `archive/legacy-bot-8788/`
(preserving their original subpaths), and split `strategy/main.py` into:
  (a) the full original, preserved at `archive/legacy-bot-8788/strategy/main.py`,
  (b) a slim shim exposing only `full_book` + `recent_trades` — the two
      functions `strategy.fleet` imports. `tests/test_dashboard_page.py`
      drops the kanban-PAGE check (now archived). `.gitignore` opts the
      `archive/legacy-bot-8788/` subtree back IN while keeping the rest of
      `archive/` (DB snapshots, NEXT_SESSION notes) gitignored runtime
      data.

**Result.** Single commit. `feat/live-readiness` and the new
`archive/legacy-bot-8788` git branch both point at this commit. Legacy
code is git-recoverable from either branch via checkout into the archive
subdirectory. The live fleet pipeline runs unchanged on port 8800.

**Verdict.** PARKED — preserved, not deleted; not running on the live host;
recoverable from the archive branch by checkout.

### Session 5 — 2026-07-31: fresh $1,000 paper fleet and dashboard audit

**Question.** Can the live fleet be restarted with a clean $1,000 simulated wallet while keeping the dashboard's headline numbers honest and easy to scan?

**Method.** Reduced the dashboard to a hierarchy of liquidation P&L, projected reward, wallet commitment, naked risk, realized P&L, and data health. Replaced the hard-coded $800 naked-budget display with config, changed projected return to use total committed capital rather than resting offers alone, added per-market committed exposure, and made the heartbeat use both fleet state and recent DB writes. Added `-FreshRun` startup behavior that archives the prior SQLite database/sidecars and stale state file before launching the supervisor.

**Result.** The previous run remains preserved; the new process is started against a clean `run/fleet.db` with `bankroll_usd = $1,000`, `allocation_budget = $900`, `max_committed_usd = $1,000`, and `max_fleet_naked_usd = $400`. Dashboard parser and focused strategy/dashboard tests pass. Projected reward remains explicitly labeled as a model, while realized P&L remains zero until a close or settlement exists.

**Verdict.** LIVE — clean paper run and dashboard are operational; profitability remains OPEN pending verified fills and realized outcomes.

### Session 6 — 2026-07-31: committed-cap overshoot caught and bounded

**Question.** Does the new $1,000 cap remain true when an allocation changes an existing order size or an emergency hedge crosses the book?

**Method.** Audited the first fresh-run sweep and found $1,036.80 committed despite the $1,000 cap: the allocator sized new quotes, but the live order reservation used a stale pre-visit total and retained old-size orders. Added post-cancellation reservation immediately before resting each order, resized stale orders, and applied the same affordable-notional calculation to emergency crossed hedges.

**Result.** After the guard, a fresh validation run stayed below the cap: the observed committed total fell from $1,036.80 to $367 and then $256 as the fleet reallocated, with no inventory fills. The dashboard exposes both committed total and any over-cap amount instead of hiding a violation.

**Verdict.** LIVE — cap enforcement is now bounded in the paper engine; the fill and realized-P&L experiment remains OPEN.

### Session 7 — 2026-07-31: fleet-wide cap context made explicit

**Question.** Does the hard cap still work when `visit()` is called from the fleet loop rather than a single-market test helper?

**Method.** Reviewed the emergency-hedge and resting-order reservation paths after the cap fix. The affordability calculation was made explicit about its state scope: the fleet runner passes all `MarketState` objects, while direct single-market callers retain a safe one-state default. Recompiled the strategy and reran all tests before restarting the paper sample.

**Result.** The cap calculation no longer depends on an undefined local `states` name, and both crossed hedges and new resting orders use the complete fleet committed total when invoked by `main()`. The single-market compatibility path remains bounded to that market rather than silently guessing a fleet total.

**Verdict.** LIVE — scope bug fixed; fill and realized-P&L evidence remains OPEN.

### Session 8 — 2026-07-31: heartbeat threshold aligned with sweep cadence

**Question.** Does the dashboard mark a healthy 20-market fleet stale while it is still completing its normal sweep?

**Method.** Compared the dashboard heartbeat threshold with the observed paper-run cadence. A complete sweep was taking roughly 50–70 seconds, while the 45-second threshold was shorter than one normal sweep and could flash STALE between state-file writes. Increased the threshold to 120 seconds, leaving the state-file heartbeat as the primary liveness signal.

**Result.** The dashboard now allows one slow sweep to complete before declaring the fleet stale, while still exposing state age and DB age for diagnosis. This changes presentation/health classification only; it does not relax the strategy's capital or risk limits.

**Verdict.** LIVE — health indicator aligned with observed polling cadence; fill and realized-P&L evidence remains OPEN.

### Session 9 — 2026-07-31: cancellation lifecycle kept in the ledger

**Question.** Do hard-cap cancellations and partial emergency hedges leave the database's historical quote state consistent with the paper engine?

**Method.** Attached each simulated resting order to its persisted quote ID, marked released orders cancelled when emergency hedging or requoting removes them, and marked the residual of a shallow crossed hedge cancelled after the filled portion is logged. Recompiled and reran fill, quote, and dashboard tests.

**Result.** The in-memory order lifecycle and the `quotes` table now agree: cancelled offers are not presented as still open, while filled crossed portions remain recorded as fills. The final paper run must be restarted because this lifecycle change invalidates the prior sample.

**Verdict.** LIVE — ledger lifecycle is aligned; fill and realized-P&L evidence remains OPEN.

### Session 10 — 2026-07-31: filled quote rows retain their source order

**Question.** Does the quote ledger stay accurate when a resting simulated order fills before it is cancelled or resized?

**Method.** Propagated each resting order's persisted quote ID into tape-confirmed `Fill` records, passed that ID to `store.log_fill`, and changed cancellation to preserve the `filled` amount while marking the remaining lifecycle closed. Recompiled and reran the full test suite.

**Result.** Maker fills now update the originating quote row instead of leaving it open with `filled = 0`; cancellation also works for partially filled rows. The next fresh paper run will therefore keep quote, fill, and active-order metrics aligned.

**Verdict.** LIVE — quote attribution and partial-cancel accounting are aligned; fill and realized-P&L evidence remains OPEN.

### Session 11 — 2026-07-31: launcher waits for a clean handoff

**Question.** Can a `-FreshRun` or restart accidentally archive a live database or race the old dashboard on port 8800?

**Method.** Replaced the launcher’s hard-coded checkout path with a path derived from `$PSScriptRoot`, and added a bounded wait after process termination. The script now refuses to archive or restart if fleet children remain or port 8800 is still listening.

**Result.** Startup is checkout-portable and fails closed instead of mixing old and new writers. The active paper run was restarted without `-FreshRun` after the final UI edit, preserving its clean sample while reloading the dashboard process.

**Verdict.** LIVE — restart handoff is guarded; fill and realized-P&L evidence remains OPEN.

### Session 12 — 2026-07-31: quote-row cancellation behavior covered by a regression test

**Question.** Does cancelling a partially filled quote preserve the filled quantity while closing only its remaining lifecycle?

**Method.** Added an isolated store test that creates a 100-share quote, records a 25-share tape-confirmed fill, calls `mark_cancelled`, and reads the row back from SQLite. Ran the full test suite and compile checks.

**Result.** The row remains `filled = 25` and becomes `cancelled = 1`; the database no longer loses the executed portion when an order is released. This protects the crossed-hedge and requote accounting used by the live paper process.

**Verdict.** LIVE — partial-cancel accounting is now directly tested; fill and realized-P&L evidence remains OPEN.

### Session 13 — 2026-08-03: Process ownership, DB recreation recovery, and atomic fleet writes

**Question.** Are process ownership checks, database recreation recovery, ranker writing, and dashboard staleness threshold comparisons robust against exact timing and concurrent invocation bugs?

**Method.** Updated process start-time validation in `fleet-procs.ps1` to perform exact tick comparisons without tolerance windows. Updated `rank_markets.py` to use per-run unique temporary files with finally-block cleanup and a process-ownership check on `ranking.marker`. Updated `store.py` schema tracking to store and compare file stat identity so unlinked and recreated databases rerun schema creation. Extracted `_atomic_write_json` in `strategy/fleet.py` for atomic writes, and exposed `stale_after_sec` in `server/fleet_dash.py` for dynamic client-side slow-sweep comparison.

**Result.** All 258 pytest unit tests pass cleanly, including a new test verifying schema reinitialization upon DB file unlinking and recreation.

**Verdict.** LIVE — process management and store initialization hardened; paper strategy evidence remains OPEN.

### Session 14 — 2026-08-03: Per-position Unrealized and Realized P&L dashboard traceability

**Question.** Can an operator identify which specific market position contributes to the floating aggregate Unrealized P&L (-$44.84) directly from the fleet table?

**Method.** Added `unrealized_pnl` calculation to each market row in `server/fleet_dash.py` (summing paired float `paired * 1.0 - pair_paid` and unhedged float `naked_exit_value - naked_cost`), and rendered explicit `Unrealized P&L` and `Realized P&L` columns in the UI table.

**Result.** Individual market rows now display exact color-coded floating P&L and realized P&L, providing instant traceability for aggregate floating drawdowns.

### Session 15 — 2026-08-03: Dashboard UX simplification and metric definitions

**Question.** Can dashboard metrics be simplified to plain stock market concepts with inline hover tooltips to improve operator interpretability?

**Method.** Updated `server/fleet_dash.py` KPI titles to plain English ("15-Min Markout Edge ($)", "Matured Trades (15m+)", "Target vs Actual Discount"), added `title` attribute tooltips to all KPI cards, and simplified subtext descriptions.

**Result.** Dashboard metric cards now render clear stock-market-style definitions on hover and use intuitive plain-language labels.

**Verdict.** LIVE — dashboard readability and tooltip UX updated; paper strategy evidence remains OPEN.

### Session 16 — 2026-08-03: Prevent stale browser HTML caching

**Question.** Why were dashboard HTML updates not immediately visible upon browser refresh?

**Method.** Added explicit `Cache-Control: no-cache, no-store, must-revalidate` HTTP response headers to the index route in `server/fleet_dash.py`.

**Result.** Browsers fetch fresh HTML directly on every refresh without holding stale cached responses.

**Verdict.** LIVE — anti-caching HTTP response headers active; paper strategy evidence remains OPEN.

### Session 17 — 2026-08-03: Plain-language stock market metrics, hover tooltips, and dual yield display

**Question.** Can dashboard metrics be updated to stock market analogies with explicit tooltip hover explanations and dual spot/hold-weighted yield views to eliminate operator confusion between markout drift and floating P&L?

**Method.** Updated `server/fleet_dash.py` KPI cards to use stock market plain-language labels ("15-Min Markout Edge ($)", "Total Floating Unrealized P&L", "Resolution Floor (Worst Case)", "Spot Daily Yield", "Hold-Weighted Yield"), added HTML hover `title` tooltips to all hero strip tiles and KPI cards, and restarted the dashboard server process.

**Result.** The dashboard UI now renders exact stock market metric definitions on hover, presents both instantaneous spot yield and solid 15h time-weighted hold rate, and explicitly breaks down total floating unrealized P&L into paired and unhedged components.

**Verdict.** LIVE — dashboard readability and tooltips updated; paper strategy evidence remains OPEN.

### Session 18 — 2026-08-05: forensic audit of unhedged P&L drag

**Question.** Where did the current fleet's floating loss come from, and which conclusions are supported strongly enough to become strategy rules without confusing a live snapshot with a settled experiment?

**Method.** Audited `research/unhedged_pnl_analysis.html`, generated from read-only `run/fleet.db` tables (`fills`, `markouts`, `quotes`, `closes`, `resolutions`) and `run/fleet_state.json` positions. The report uses the same P&L definitions as `server/fleet_dash.py`: realized P&L, paired float (`paired * 1.0 - pair_paid`), and unhedged float (`naked_exit_value - naked_cost`). Drift is `mid_later - ref_mid`, deliberately excluding our quote offset. The snapshot contained 23 live markets, 57 fills, 10,853 quotes, 9 closes, 16 resolutions, $2,063.56 filled notional, and 40.9 hours of fill history. This is an audit of one live snapshot, not a new independent sample.

**Result — the P&L waterfall and concentration.** Realized P&L was **+$116.33**, paired float **−$32.61**, unhedged cost at risk **$345.33**, current unhedged exit value **$122.01**, unhedged float **−$223.32**, and total liquidation P&L **−$139.59**. The unhedged float is about **1.9×** the realized gain and is the dominant drag; paired float is a secondary nuisance. The negative unhedged float was concentrated in **three markets out of 23**, totaling **−$271.03**, while two positive unhedged positions contributed **+$47.71**. The largest single loss was `lol-maz-mg1-2026-08-04` at **−$190.26**. This is concentrated binary-resolution risk, not a small adverse-selection tax distributed evenly across the fleet.

**Result — marking convention and liquidity.** The dashboard's unhedged mark depends on the current bid and records **$0** when the book is unreadable, while the binary payoff is actually $1 or $0 at resolution. The same $345.33 cost therefore spans a **+$168.59** all-win case to **−$345.33** all-lose case. A zero/unreadable mark is not an economic resolution and can jump without a trade. The two largest loss positions showed either `up_bid = 0.999` (a resolved-looking one-sided book) or no readable two-sided book; the third negative position had a readable `2/2` book. The report therefore rejects using `unhedged_float` alone as a risk trigger. Risk controls should use stable `naked_cost` plus position age and book/readability state. The report also found that 23/23 markets were labelled `WIDENED`, including **18 with no inventory**, so the gate was suppressing revenue on empty markets because of a portfolio-wide average rather than an actual position risk.

**Result — drift by horizon and the tail.** Of 52 fills with a measured last drift, the mean drift moved from **−1.21c at 5 minutes** (`n=52`) to **−5.86c at 1 hour** (`n=27`) and **−12.83c at 6 hours** (`n=5`); medians were **0.0c**, **−1.6c**, and **−2.1c** respectively. The direction is adverse with time, but the six-hour estimate is too small to calibrate a cutoff. Nine of 52 fills (**17.3%**) had drift below **−20c**, contributing **−$165.07** of size-weighted drift against **−$29.94** across all measured drift. Most fills cluster near zero; a small left tail carries the loss. This supports isolating tail events, not tuning the whole book to improve the mean.

**Result — price band.** The **40–60c** entry band was the only clearly toxic band in this snapshot: `n=19`, mean drift **−10.68c**, and **−$122.00** of size-weighted drift. The other bands contributed **+$23.37** (0–20c), **−$4.17** (20–40c), **+$72.64** (60–80c), and **+$0.22** (80–101c). The mechanism is plausible: a 40–60c binary price represents maximum uncertainty, so new information can reprice the market sharply. The sample is not large enough to call this universal, but it supports measuring a 40–60c notional limit or a wider offset as a controlled experiment.

**Result — pairing and inventory build-up.** Seven of 18 markets with fills never formed a pair at all; other markets reached roughly **79–92%** pairing. The data therefore describe two populations: markets where pairing works and one-sided markets where it is impossible after entry. In `lol-maz-mg1`, the first fill created **98** exposed UP shares and a later fill added **135 more on the same side**, reaching **233** without a DOWN fill. The configured `skew_full_shares = 240` and `max_skew = 0.015` produced only about **0.6c** of skew at 98 exposed shares—less than one tick—so the control was too shallow and too distant to stop adding to an already exposed leg. This supports an entry/readability filter and a deterministic “never add to the heavy side” rule.

**Result — execution quality and queue context.** All **57/57 fills** were tape-confirmed (`reason = tape`), **0** were crossed taker fills, and mean spread capture was **+2.40c per share**. Queue-bucket fill rates were **0.34%** (0–1 shares ahead), **0.76%** (1–50), **0.18%** (50–150), **0.47%** (150–400), and **0.13%** (400+); the highest rate was at 1–50 shares ahead and the lowest above 400. The report therefore does not indict fill identification, taker-fee accounting, or entry pricing; it attributes the observed loss to post-fill inventory management. This is consistent with the project's earlier tape-confirmed fill work, but the present report alone does not prove causality across future samples.

**Audit conclusions and proposed decisions.**

1. **LIVE candidate:** reject or sharply limit new quotes when the market does not have a readable two-sided book; this is the earliest observable signature of the three concentrated losses.
2. **LIVE candidate:** never increase the already-heavy leg. This is deterministic inventory protection and does not require a statistical model.
3. **OPEN experiment:** cap notional or require a wider offset in the 40–60c band, measuring its effect separately from the readability rule.
4. **OPEN experiment:** replace the unweighted markout gate with size-weighted drift or a tail statistic, and gate only markets carrying inventory; the current gate marked all 23 markets WIDENED, 18 of them empty.
5. **PARKED, not rejected:** a time-based exit. The horizon trend is adverse, but the 6-hour sample is only `n=5`, and the three worst books had no executable bid, so a market exit cannot solve an unreadable book.
6. **Rejected for now:** a broad offset increase or aggressive market exit. The central drift distribution is mostly healthy, while a wider offset would sacrifice fill rate without addressing the binary tail; a market order cannot fill where no bid exists.

**Verdict.** OPEN — the audit identifies concentrated unhedged exposure and three immediately testable controls, but it does not establish a final profitability verdict. The snapshot is small, partially unresolved, and includes live marks; implement each rule in isolation, start a fresh paper sample after strategy changes, and require realized/settled outcomes before declaring the maker strategy LIVE or DEAD.

---

### Session 19 — 2026-08-05: risk primitives in dollars (U1)

**Question.** Can the per-market risk limits be restated in dollars at risk rather than in shares, and can a book be judged tradeable before a bid rests on it?

**Method.** Added `strategy/risk.py` as a pure module with no callers: `naked_side`, `naked_usd`, `risk_utilization`, and `book_health`. `naked_usd` values the excess leg at average cost rather than at the current mark, so the measure does not shrink when the mid moves against the position. `book_health` rejects on three arms — one-sided, settled (either quote within `decided_price` of an end), and too wide or too thin — and reports `depth_evaluated` separately from `ok` so a replay against recorded history, which carries a mid but no depth, can tell "depth passed" from "depth was never measured". New config fields: `max_naked_usd: 120.0`, `decided_price: 0.02`, `max_book_spread: 0.06`, `min_book_depth_sh: 200.0`. 20 tests in `tests/test_risk.py`; full suite 344 passed.

**Result.** The dollar reading reproduces the observed loss exactly: 233.40 UP shares at an average of 0.8152 returns $190.26, against a 360-share cap that read 233 and stayed silent. The same measure values 200 UP against 100 DOWN at a 0.60 average as $60 at risk rather than $120, because the hedged 100 shares pay $1.00 either way. `book_health` refuses the recorded 0.999/0.001 shape as settled and the recorded 0.26/0.42 shape as too wide. Nothing imports the module yet, so no live behaviour changed and the running sample stays valid.

**Verdict.** LIVE as a measurement. The unit is now dollars; whether the $120 budget is the right number is a separate question that U7's replay answers.

---

### Session 20 — 2026-08-05: the dollar cap and the hedge-side gate (U2)

**Question.** Does the live quoting path refuse to add to an over-budget naked leg, and does it refuse a bid whose hedge token cannot be traded?

**Method.** Added `hard_block(cfg, inv, side, price, own_book, hedge_book)` to `strategy/risk.py` and called it from `_decide_quotes_rewards` in place of the `imbalance >= max_naked_shares` branch. Three arms, ordered so the reason names the cheapest certain rejection first: hedge-token health, own-book health, then the dollar cap. An exposure-REDUCING side returns None before any arm runs, so the light side is never gated. `max_naked_shares` was removed from `strategy/config.py` rather than kept beside `max_naked_usd` — two caps in two units cannot both be binding, and the looser one governs silently. The emergency stop-loss trigger was restated in the same unit: the deficit is valued at the heavy leg's average cost and compared against `max_naked_usd * emergency_hedge_frac`. Added `enable_hard_blocks: bool = True` so the gate can be measured on its own. 18 new tests; full suite 359 passed, up from 344.

**Result.** A market holding 140 naked UP at 0.82 ($114.80 against a $120 budget) rests nothing on UP and full size on DOWN; one dollar under the budget it still rests on UP, so the cap and not an unrelated filter is what binds. A healthy UP book paired with a 0.999-bid/no-ask DOWN book now rests nothing on EITHER side, and the UP reason names the hedge token — the case that produced the observed loss, where the position was built in two prints before the book degraded. With `enable_hard_blocks` false the same market at 200% of budget quotes both sides, which isolates the new gate as the cause of every result above. The emergency cross still fires with an unhealthy hedge book, per R4.

**Caveat.** Restating the emergency trigger in dollars preserves the relationship (it fires at 80% of the cap, inside it, with a losing heavy leg) but not the share count: 288 shares was $144 at 0.50 and $234 at 0.8152.

**Verdict.** LIVE. This changes quoting behaviour, so the running sample is invalidated from here — archive `hunter.db` before the next run rather than mixing configs in one dataset.

---

### Session 21 — 2026-08-05: the size ladder and the dollar-wound spring (U3)

**Question.** Can resting size decay continuously toward the dollar budget instead of stepping from full size to none, and can the skew respond to dollars rather than to share count?

**Method.** Added `size_for(cfg, inv, side, price)` and `skew_offset(cfg, inv, side)` to `strategy/risk.py`. The ladder is `base * (1 - utilization)^2`, additionally capped at `(budget - naked) / price` so one order cannot exceed the cap it is approaching, and floored to zero below `min_quote_shares` because an order under the venue minimum earns no reward score. The light side returns full size at any utilization — it is the only resting order that flattens the position. `skew_offset` scales `max_skew` by `risk_utilization` of the naked side, positive heavy and negative light, 0.0 when flat. `skew_full_shares` was removed from the config and both comment blocks describing it were rewritten. 26 net new tests; full suite 385 passed, up from 359.

**Result.** The decisive reading is the one the share-denominated spring could not produce: at the same 100-share imbalance the new skew pushes further at a 0.85 average than at a 0.15 average, because $85 of downside is not $15 of downside. The old ramp answered both identically, which is why on `lol-maz-mg1` it was still ramping at 233 of 240 shares while $190.26 was already at stake. A market walking from flat to the budget now produces a decreasing sequence of intent sizes ending in no intent.

**Result worth flagging.** With the default 120-share base and the 50-share venue floor, the quadratic ladder reaches the floor at roughly 35% utilization — about $42 of the $120 budget — so the heavy side stops resting well before the budget rather than at it. That is a consequence of the venue minimum, not of the decay curve: 120 * (1 - u)^2 = 50 at u = 0.355. The effective heavy-side stop is therefore materially tighter than the nominal cap, and U7's replay is the instrument that decides whether that is cutting toxic flow or profitable flow.

**Verdict.** LIVE, with the 35% observation OPEN pending replay.

---

### Session 22 — 2026-08-05: the two dead rules, and price-dependent risk (U4)

**Question.** The price band and the pair-cost cap are present in the codebase and documented. Do they run on the objective the fleet actually uses, and should offset and size respond to the price of the fill?

**Method.** Both rules lived in the legacy branch of `decide_quotes`, below the line where `_decide_quotes_rewards` returns, so neither ever executed on the live path. Extended `risk.hard_block` with a band arm and a pair-cost arm, using the existing `enforce_price_band`, `price_band_low`, `price_band_high`, and `max_pair_cost` fields — unchanged values, newly reachable. Gate order is hedge health, own health, dollar cap, band, pair cost. Added `band_risk_factor(cfg, price)` returning a size multiplier and an extra offset: the multiplier falls toward `1 - coinflip_size_cut` at 0.50 and tapers to 1.0 at `coinflip_halfwidth` away; the extra offset sums a coin-flip term and a `price_risk_widen * price` term. Per KTD3 the extra offset is applied before the reward-window clamp, and whatever the clamp truncates is converted into a proportional size reduction rather than discarded. New config: `coinflip_halfwidth: 0.20`, `coinflip_size_cut: 0.10`, `price_risk_widen: 0.010`. The `_decide_quotes_rewards` docstring was rewritten — it claimed the band and pair cost were deliberately bypassed, and that claim no longer holds. 23 new tests; full suite 408 passed, up from 385.

**Result.** A 0.95/0.96 market under the default `rewards` objective now returns no intent, with the reason naming the band; it quoted before. Turning `enforce_price_band` off lets the same market through, which isolates the band as the cause. A bid at 0.52 against a held DOWN average of 0.49 is refused at $1.01 against the $0.995 cap — the shape recorded on `wta-kalinsk-kessler`, which bought 14 pairs at $1.0200 on an instrument that pays exactly $1.00 — while the same bid at a 0.40 average is allowed. Under `WIDENED`, where base offset plus risk terms exceeds the 4.5c window, the resting offset equals the window and the resting size is below what the ladder alone would give, so the risk response survives the clamp instead of vanishing into it.

**Two findings from the work itself.**
- A production bug, unreachable until now: an offset clamped exactly to `max_spread_from_mid` was then dropped for exceeding it, because `0.525 - 0.48` evaluates to `0.04500000000000004`. Harmless while the clamp never bound; routine once the risk terms push against it. Guarded with a representation-error epsilon.
- `test_skew_is_symmetric_and_flat_when_balanced` held 120/120 at a 0.50 average each. Under the new pair-cost arm that fixture is illegal — a 0.505 bid against a 0.50 average is a $1.005 pair. The test fixture was itself an instance of the loss the cap now prevents.

**Verdict.** LIVE. Two rules that read as absent in the telemetry now execute on the path the fleet runs.

---

### Session 23 — 2026-08-05: size-weighted markout (U5)

**Question.** Does the markout mean describe the money at stake, or only the number of prints?

**Method.** `_stats_from_rows` took an unweighted mean, so on 2026-08-05 the two prints that carried 233 shares voted with the weight of two 50-share prints. Rewrote it to compute a size-weighted mean and Kish's effective sample size, `sum(w)^2 / sum(w^2)`, returned as `n` with the raw count kept as `n_rows`. Kish equals the row count exactly when sizes are equal, so `markout_min_sample` and the doubling rule in `gate.next_state` keep the meaning they were tuned with and needed no re-derivation. `n_eff` is what the `insufficient_sample` verdict compares, so a sample dominated by one large fill no longer licenses an exit. `size` is now carried through the row dicts in both `per_market_stats` and `fleet_stats`; the `markouts` table has had a `size` column since creation, so no migration was needed. A row with no `size` key weighs 1.0 — that caller does not supply sizes at all and should degrade to the old unweighted mean — while a size present but null, zero or negative weighs 0.0, because that is a defective row and must not pad the sample. All-zero weights return `insufficient_sample` rather than dividing by zero. `strategy/gate.py` was not touched. 7 new tests; full suite 415 passed, up from 408.

**Result.** The sharpest reading came out of the gate test. Ten 200-share fills at -5c against ten 10-share fills at +1c has an unweighted mean of exactly -2.00c, which lands precisely ON `markout_catastrophic_threshold` and the strict `<` therefore left the market in the book. Weighted, the same rows read -4.71c — more than twice past the threshold — and the magnitude bypass takes the market NORMAL to EXITED. The AE5 case behaves the same way: one 200-share fill at -5c against nine 10-share fills at +1c reads positive unweighted and negative weighted, on an effective sample below 3. Equal-sized rows return an effective sample equal to the row count exactly, across three size scales, which is what keeps the existing thresholds honest.

**Verdict.** LIVE. The mean now describes the dollars rather than the prints, and no threshold had to be retuned to make that true.

---

### Session 24 — 2026-08-05: the fleet circuit breaker (U6)

**Question.** Pooled markout evidence is strong enough to read and too weak to sentence any individual market. What is the proportionate response to it?

**Method.** Added `HALTED` and `fleet_posture(pooled, cfg)` to `strategy/gate.py`, kept separate from `next_state`, which remains a pure function of one market's own stats. `fleet_posture` takes no previous posture — having nothing to remember is what makes it reversible. `strategy/fleet.py` computes it once per sweep from `markout.fleet_stats` and injects it through the existing `replace(cfg, ...)` call alongside `gate_state`, `fleet_naked_usd` and `committed_usd`; a failed read holds the previous posture rather than falling back to NORMAL, because that is the one direction that must not silently lift a live halt. The transition is logged once, following the `GATE EXIT` pattern, and never persisted. In `_decide_quotes_rewards` the halt blocks the heavy side only, placed after the committed-capital check and before the size ladder — rules describing this market's own book report first, since a market refused on its own terms should not be blamed on the fleet, and there is nothing to size for an order that is not going out. 18 new tests; full suite 433 passed, up from 415.

**Result.** The recorded pooled reading of -0.052375 on n=52 returns HALTED, where the fleet sits at WIDENED today — 2.6 times the catastrophic threshold answered by widening quotes 1.5c. -0.008 returns WIDENED and +0.01 returns NORMAL, and `insufficient_sample` returns NORMAL, so thin evidence never halts. Under HALTED the heavy side rests nothing while the light side rests at a size identical to the un-halted run, a flat market still quotes both sides because neither side is heavy, and the emergency cross still fires. The halt lifts on the next sweep once the pooled reading recovers. KTD5 is pinned as one assertion pair: the same -4.75c pool caps a borrowed per-market verdict at WIDENED and halts the fleet, which is exactly the distinction the decision draws.

**Verdict.** LIVE. Pooled evidence now produces a reversible fleet-wide throttle rather than either a shrug or a mass blacklisting.

---

### Session 25 — 2026-08-05: replay validation of the dollar gates (U7)

**Question.** Against the recorded fills, would the new gates avoid more naked cost than realized P&L they would forgo, and would they cut more than half of the profitable flow?

**Method.** Added `scripts/replay_risk_gates.py` and fixture-backed tests in `tests/test_replay_risk_gates.py`. The replay reads `fills`, `quotes`, and `closes` through a read-only SQLite handle, reconstructs inventory in fill order, and asks `risk.hard_block` at each recorded fill. It reports both the live path (R4 exposure-reducing exemption honored) and each gate independently so light-side rule evidence is not mistaken for a live refusal. Because the database records a mid but no book ladder, the depth arm is explicitly reported UNEVALUATED. Per-fill realized P&L does not exist; positive market-level `closes.realized_pnl` is attributed across refused fills by cost share and labelled as attribution, not measurement. Fixture coverage includes the `lol-maz-mg1` dollar-cap case, `wta-kalinsk-kessler` pair-cost case, healthy flow, missing depth, empty and absent databases, and P&L attribution.

**Result.** Replay against the recorded paper database `run/fleet.db` (67 fills, 23 markets) refused 15 fills (22.4%) on the live path, avoiding **$729.88** of incremental naked cost against **$26.19** of attributed realized P&L forgone. The profitable-market stop check refused 6 of 40 fills (15.0%), so it did not trigger. Per-gate evidence was: dollar cap 5 fills / $178.27 naked cost; price band 28 / $743.19; pair cost 16 / $268.18; hedge and own book health 2 each / $223.05 each. Depth remained UNEVALUATED for all 134 health checks. `hunter.db` is an empty stale database (0 fills), so it is a zero-data report rather than the recorded-run verdict.

**Verdict.** LIVE for paper replay. The exit criterion passes on the available recorded sample, with book depth and per-fill P&L explicitly OPEN rather than papered over.

### Session 6 — 2026-08-05: operator dashboard action telemetry

**Question.** Can the dashboard show what each market just did, why it did it, and whether risk gates are shaping the book without inferring actions from stale inventory?

**Method.** Added durable `market_events` rows for fills, hedges, merges, exits, blocked decisions, quote state, waits, and errors. Added structured reason codes, per-market recent-event payloads, fleet refusal counters, active-quoting ratio, fleet naked-risk utilization, and a high-contrast gold mid-price marker. The table now separates last action from projected income, order depth, capital, and P&L. Event writes are deduplicated for routine quote cycles while fills and exits remain high-signal.

**Result.** Focused dashboard/event tests pass, and the full suite passes **444 tests**. Existing failure-path tests continue to preserve figure timestamps. Refusal codes prioritize specific book/gate causes over compound prose, and the dashboard labels active quoting as active quoting rather than claiming it is a complete book-health measurement.

**Verdict.** LIVE for dashboard telemetry in paper simulation. Profitability and execution quality remain OPEN; this change improves observability and does not alter strategy thresholds.
### Session 7 — 2026-08-05: realized exits table

**Question.** Can the dashboard preserve an auditable snapshot of every closed position, including cost, quantity, exit price, and return?

**Method.** Added an API reader over every `closes` row and an event-level table distinguishing `SELL` from `MERGE`. Sell exit price is the effective proceeds-per-share price; merge exit price is parity at $1.00. Fees or gas remain separate.

**Result.** Added a table below live markets with exit time, market, method, shares, average cost, effective exit price, P&L, P&L percentage over cost basis, fees/gas, and leg detail. The suite passes with **447 tests**.

**Verdict.** LIVE for realized-exit telemetry in paper simulation; closed history is now separated from open positions.

---

### Session 8 — 2026-08-05: hard primary-market selector and liquidity gate

**Question.** Can the paper fleet refuse dynamic esports submarkets and dry books before a naked leg is created?

**Method.** Added the pure shared `strategy/selector.py` contract. It rejects Game 1/2, Map 1/2, Set 1, Round, Live, In-Play, and Handicap names; requires explicit Moneyline/Main Line/Outright or Politics/Macro/Economics metadata; requires 24-hour volume above $250,000; requires top-three **bid** depth above $5,000 independently on YES and NO; and requires a two-sided spread no wider than $0.04. `scripts/rank_markets.py` applies the selector before ranking, and `strategy/fleet.py` repeats the identity and live-book checks before fills or quotes, cancelling stale simulated orders on failure.

**Result.** The previous `run/markets.json` visibly contained rejected examples including `Map Handicap` and `Game 1 Winner`; those entries are now refused even if the file is stale. Selector, selection, and ranker tests pass, and the full suite passes **452 tests**. The new depth measurement is dollar notional at the best three bid levels, not raw shares or asks; unknown or one-sided books fail closed.

**Verdict.** LIVE for paper-simulation protection. Profitability remains OPEN: the stricter universe must now produce a clean sample with enough fills and settled exits before any economic conclusion.

### Session 9 — 2026-08-05: clean-run preparation

**Question.** Can the next paper sample start without the prior Game 1 inventory or stale dashboard state?

**Method.** The existing launcher archives `run/fleet.db`, WAL/SHM sidecars, and `fleet_state.json` under `run/archive/fleet_<timestamp>` before starting the supervised fleet, dashboard, and ranker. The selector changes are kept separate from the old sample; no historical rows are deleted.

**Result.** The target listener `127.0.0.1:8800` is free and the working tree contains unrelated pre-existing dashboard/research edits, so the clean start is performed through the scoped launcher rather than broad file deletion.

**Verdict.** OPEN until the fresh supervised process tree is confirmed alive and the newly generated market universe is inspected.

---

### Session 10 — 2026-08-05: horizon extension for primary market selection (R14)

**Question.** Does extending the resolution horizon from 7 to 30 days admit liquid macro, sports, and political instruments while maintaining safety?

**Method.** Updated `select_max_days_to_resolve` in `strategy/config.py` from 7.0 to 30.0 days. Re-ran the ranker dry-run and full test suite.

**Result.** Ranker dry-run immediately selected 3 high-volume primary markets (up from 1), admitting liquid tournaments and macro events that resolve within the month. 452 tests pass.

**Verdict.** LIVE. Extended horizon retains selector safety while increasing eligible candidate breadth.

---

### Session 11 — 2026-08-06: identity whitelist audit, and the shape that actually needs one (R15)

**Question.** The live universe had shrunk to 2 markets, both single ATP tennis matches from the same tournament and day. Is `identity_allowed` excluding real, liquid, non-sports opportunity, or is the venue's tradeable universe genuinely this thin?

**Method.** Instrumented a read-only audit against live venue data (no writes to `run/markets.json`, no ranking-marker interaction): for every candidate in the reward pool (198 markets) and the pre-filtered spread pool (11 markets, already ≥$250K/24h), recorded whether `identity_allowed` passed and, for rejects, whether the market already cleared the volume/horizon bars downstream. Reward-pool result: of 131 identity rejections, 1 cleared the $250K volume bar and 0 cleared volume+horizon both — identity was not the bottleneck there. Spread-pool result (this pool is where it mattered, because every candidate is already liquid): 7 of 11 passed identity, including a correctly-blocked `Game 2 Winner` submarket; 3 failed downstream on real book-depth traps (YES-side top-3 bid depth of $4, $16, and $23 against $1M–$2.5M of 24h volume — high historical volume with an empty resting book, exactly the shape the depth gate exists to catch); and one market, a Strait-of-Hormuz geopolitical question at $557K–$675K/24h, was rejected purely for lacking a hardcoded macro keyword despite blank `category`/`market_type`.

Root cause: the fallback branch of `identity_allowed` required a topic-keyword match (league name or macro word) for ANY market with blank venue metadata, including standalone yes/no event questions that have no "Game 1/2" variant to fragment into — there is no submarket shape to protect against there. Rewrote the fallback in `strategy/selector.py` to require the topic-keyword confirmation only for head-to-head "A vs B" titles (the shape that hides Game/Map/Round submarkets); a standalone question only needs to clear the existing blocked-keyword check and carry no `market_group` label. Also closed a gap the restructuring exposed: `_BLOCKED_RE` only matched `game/map/set` next to a literal digit 1 or 2, so a bare "Map Winner" or "Game Winner" phrase (no digit) slipped through unblocked; added `\b(?:game|map|set)\s+winner\b` to the pattern. 3 new tests in `tests/test_selector.py` (standalone question admitted without a topic keyword, still blocked by a group label, still blocked by a submarket keyword); 8/8 selector tests and 455/455 full suite pass.

**Result.** Re-running the same live audit after the fix: reward-pool identity rejections collapsed from 132 to 2 (1 correctly-blocked submarket, 1 genuine non-match). Spread-pool: the Strait-of-Hormuz market was re-examined directly and turned out to carry `market_group: "August 15"` — it is one of several date-threshold variants of the same underlying question (an "August 31" sibling also exists), which is a real submarket-family shape, just not an esports one; it is now correctly rejected by the new no-group-label rule for the right reason instead of the old wrong one. A ranker dry-run picked **4** markets (3 tennis + Dodgers/Cubs) at $480 total capital and $10.90/day, up from 2 markets / $240 / $5.14/day. The book-depth gate's catch of the three high-volume/empty-book traps did not change — that gate was correct before and after.

**Verdict.** LIVE. The keyword whitelist was not the reason the fleet was concentrated in one sport; the venue's actual tradeable universe is thin and the depth gate is correctly rejecting markets that only look liquid on trailing volume. The fix closes a real but smaller gap (standalone event questions) and leaves the submarket and depth protections intact. Concentration risk (currently 3 of 4 picks are tennis) is a separate, still-open concern — see Session 12.

---

### Session 12 — 2026-08-06: clean paper run, and the go-live readiness read (U8)

**Question.** Before risking real money in any amount, what is the actual bar for "this bot works, is profitable, and can be trusted" — which metric answers that, and how much settled evidence does it take to trust the answer?

**Method.** Archived the prior paper run (`run/archive/fleet_20260806_001515`, superseded by Session 11's selector fix mid-run) and restarted the supervised fleet clean via `scripts/fleet-start.ps1 -FreshRun` on a new `run/fleet.db`, then again without `-FreshRun` once the dashboard code below landed (0 fills had accumulated in between, so nothing was lost).

Audited what "realized P&L" already meant in the codebase and found it materially biased: `_settled_positions()`/the `closes` table only records voluntary exits (SELL/MERGE), and a MERGE is a completed hedge — by construction almost always profitable. The naked tail that loses money (the exact mechanism behind the Session 18 forensic audit's -$190.26 loss) never gets a `closes` row; it sits as unrealized float until the market resolves, and resolution alone writes only `(condition_id, winning_token)` to `resolutions`, no P&L. Confirmed the bias directly against the richest archived sample (`fleet_20260805_182913`: 67 fills, 16 resolutions): the 13 `closes` rows are ALL merges, mean return **+16.5%**, stdev 10.8% — a uniformly rosy numbers. Running the TRUE settled calculation instead (`_realized()`'s existing per-market resolution-implied payout logic, which was already correct but only ever summed into an aggregate, never exposed per-market or used for a confidence read) over the same database: **16 fully-resolved markets, mean return -16.0%, stdev 86.3%, 90% CI lower bound -51.5%** — a 32-point swing from the closes-only view, and a stdev an order of magnitude larger, confirming the bimodal shape (frequent small capture, rare large binary-tail loss) the forensic audit described.

Defined two readiness tiers and implemented both in `server/fleet_dash.py`:
- **Tier 1, machinery** (fast, low-variance): pooled size-weighted markout (`_pooled_markout_neff`, mirroring `strategy/markout.py`'s `_stats_from_rows` weighting and Kish `n_eff` exactly) reads within hours of fills landing and answers "are the entry/risk rules behaving as designed."
- **Tier 2, money** (slow, high-variance, the one that actually licenses real capital): true settled P&L per market — extended `_realized()` to expose a `rows` list (`condition_id`, `market_slug`, `pnl`, `cost`) for every fully-resolved market, then added `_go_live_readiness()` computing n, mean return %, stdev, and a one-sided 90% confidence lower bound (`mean - 1.645 * stderr`), plus calendar-day coverage (`MAX(ts) - MIN(ts)` over `fills`) and category concentration (largest single sport-tag share of the settled sample, from the market slug's first token).

Sample-size reasoning (documented in code as the constants' rationale, not re-derived here): using the observed shape above as illustrative (≈85% of markets near +18%, ≈15% near -90%, blended mean ≈+2%, stdev ≈39%), detecting that mean against that stdev at 90% confidence needs roughly `(1.645 * 39 / 2)² ≈ 1,000` settled markets by the standard normal-approximation power formula — a real property of a small-frequent-capture/rare-large-tail payoff, not a measurement bug, and precisely why the U1–U7 dollar risk gates matter: shrinking the tail's magnitude shrinks the required sample far faster than accumulating more trades does. Set two practical, sub-full-significance constants rather than waiting for n≈1,000: `SIGNAL_MIN_SETTLED = 30` (past the noise floor, directional only), `GO_LIVE_MIN_SETTLED = 100` (practical minimum for a small real-money pilot decision), `GO_LIVE_MIN_CALENDAR_DAYS = 14` (cross multiple days/regimes, not one tournament), `GO_LIVE_MAX_CATEGORY_SHARE = 0.5` (no single sport can be the whole sample). The confidence-interval SIGN, not the point estimate, is the actual gate at both tiers.

Added a "Go-live readiness" KPI panel to the dashboard (between Profit & Loss and Risk & Exposure), rendering: composite status (NO_DATA / COLLECTING / DIRECTIONAL_SIGNAL / READY_FOR_SMALL_LIVE_PILOT), settled sample vs. both thresholds, mean return and stdev, the 90% CI lower bound, markout effective sample, calendar coverage, and category concentration with its breakdown — each tile tooltipped with the same reasoning as above. 455/455 tests pass; verified live in-browser against the freshly-restarted (empty) DB and against the archived rich sample via `DB` monkeypatch, both computing the expected numbers.

**Result.** The dashboard now shows `NO_DATA` on the fresh run, as it should — the fleet was just restarted clean. The instrumented historical read against the last populated sample is nonetheless a live finding on its own: under the TRUE settled metric, the pre-risk-gate paper fleet's realized economic performance was **negative** (-16% mean, CI far below zero), not the +16.5% the `closes`-only view implied. This is consistent with, and now statistically legible alongside, the Session 18 forensic audit's conclusion that unhedged tail risk was the dominant drag.

**Verdict.** OPEN by construction — the whole point of this session is that no sample yet exists under the current (post-U1–U7, post-R15) rules to judge. The dashboard will report `COLLECTING` until `n_settled` passes 30, and the honest go-live decision is `READY_FOR_SMALL_LIVE_PILOT` only when `n_settled ≥ 100`, the CI lower bound is positive, calendar coverage ≥ 14 days, and no sport exceeds 50% of the sample — track this panel rather than the top-line "Realized P&L" tile when deciding whether to risk real money.

---

### Session 13 — 2026-08-06: the 25-matured-fill run was the volatile universe, and the toxic-band decision (R16, U9)

**Question.** Two live concerns raised together: (1) the current universe is down to 1-2 markets, sometimes one flagged STALE/ERROR — is that a bug, and should the fleet fall back to whatever produced the last run's 25+ matured fills? (2) The 40-60c band reads unprofitable — what should today's test run actually do about it?

**Method, part 1 — auditing the 25-matured-fill run.** Queried `run/archive/fleet_20260805_182913/fleet.db`'s `markouts` table by `market_slug`: 67 rows, 34 with a matured 1h mark. Broken down by market: `lol-*`, `cs2-*`, `val-*`, `dota2-*` (esports submarkets) and `elon-musk-of-tweets-*`/`bitcoin-*` (volatile prop questions) supplied the overwhelming majority of the matured sample; primary markets (`mlb-tb-col`, two `wta-*`, two `atp-*`) contributed 6 matured rows between them. `lol-maz-mg1-2026-08-04` — the market that produced the Session 18 -$190.26 loss — is in this same list. The run that reached the sample-size gate did so almost entirely on the inventory the selector (R13, R15) now correctly excludes; reverting to admit it would reopen the loss mechanism this quarter's work was built to close.

Re-ran the live dry-run ranker: right now, 191 of 192 scored candidates fail on liquidity (128 on YES-side depth, ~25 on spread), not on identity (1 reject) — confirming the thin current universe is a real, live venue-liquidity fact for this hour, not a selector defect. The single earlier STALE/ERROR market (`atp-jodar-moutet`) had already dropped out of `run/markets.json` on its own by the time this was checked -- its book failed spread/depth as the match played, which is the depth gate doing its job, not an error.

**Method, part 2 — the toxic band.** Confirmed the operator's read against the existing Session 18 numbers (-10.68c mean drift, -$122.00 size-weighted, n=19, 0.40-0.60) and proposed a hard exclusion of that band in `risk.hard_block`. Implementing it broke 8 `test_risk.py` cases immediately (0.50-0.52 is the file's default "healthy market" fixture price) and, more importantly, would have removed quoting from the price region the strategy's own price-band design comment identifies as most valuable (`fee = 0.07*p*(1-p)` peaks at p=0.50). Presented the tradeoff; operator chose a strong soft response over a hard ban. Reverted the hard-block arm and its config fields entirely and instead raised `coinflip_size_cut` (the existing `band_risk_factor` lever, U4/R6) from 0.10 to 0.55.

0.88 was tried first and turned out to be a de facto hard ban anyway: `size = int(ladder * band.size_mult)` also gates the LIGHT side (the one order every other rule exempts, because it is the only one that reduces exposure), and the 120-share base falls under the 50-share reward minimum for any cut past ~58%. 0.55 is the strongest cut that still rests a nonzero order at the exact coin flip on a flat book. This still cascaded into 9 `test_quotes.py` failures whose fixtures happened to sit at 0.50-0.52 for reasons unrelated to the band (fleet cap, skew, halt/recovery) — fixed by moving those specific fixtures off-center (0.65/0.32) rather than changing what they test, and by recomputing the handful of tests that ARE about the coin-flip cut itself against the new default. 455/455 pass.

**Result.** At `coinflip_size_cut=0.55`, resting size at the exact 0.50 coin flip on an otherwise-flat 120-share-base market drops from 108 to 55 (roughly a 49% realized cut once the reward-minimum floor is respected, short of the nominal 55% because the floor rounds up); a heavy side carrying naked exposure now floors to zero under far lower utilization than before (the 30%-utilization case that used to rest 52 shares now needs to be under ~5% utilization to rest anything at 51). The edges of the wider 0.30-0.70 price band (roughly 0.30-0.40 and 0.60-0.70) keep most of their size, tapering toward the center exactly as `band_risk_factor` was designed to do — the change is in how much less remains at the center, not in the shape.

**Today's test version.** Selector stays exactly as R15 left it (Session 11) — the thin current universe is real, not a bug, and the fast path back to more fills runs straight through the submarket risk this quarter closed. `coinflip_size_cut=0.55` is the new default; `run/archive/fleet_20260806_003515` was archived before this restart and `run/fleet.db` starts clean under R15 selector + R16... (no separate identity change) + the strengthened U9 band response. Track the go-live readiness panel (Session 12) for `n_settled` and the per-band markout split rather than raw fill count when judging today's run — a market count of 1-3 is expected at off-peak hours under the safe universe, not a signal something is broken.

**Verdict.** LIVE. R15 (selector) unchanged and reaffirmed with new evidence; U9 (coinflip_size_cut 0.10 → 0.55) is a real, measured behavior change, OPEN pending fresh per-band markout data under the new setting — reassess against the go-live readiness panel once enough of today's sample lands in and around 0.40-0.60.

---

### Session 14 — 2026-08-08: empty-universe dashboard zombies, root-caused and fixed (U10)

**Question.** After the venue delisted the finished Aug 5-6 events (three MLB games, a WTA match, two LoL BO3s), the dashboard showed all six markets as frozen `STALE / ERROR` rows — `book fetch: 404` repeated every visit — while the fleet had actually restarted into an empty universe (`markets: 0`). Was the fleet broken, and why did the page keep showing markets the fleet had stopped trading?

**Method.** Audited the live run and the code path end to end. The 404s were correct: probing `clob.polymarket.com/book` for a delisted market's token returns `404 {"error":"No orderbook exists for the requested token id"}` and the metadata endpoint reports `closed: true, accepting_orders: false` — the venue genuinely removed the books. The fleet's `RERANK 6 dropped market(s) retained: still holding inventory` at 09:58 kept those markets in `states`, so every visit 404'd and stamped `_live.err`. Then the fleet restarted (10:01:17, 10:47:52) into `markets: 0` — the ranker scored 199 candidates and rejected all 199 (thin midday universe, matching Session 13) — and the empty loop branch never completed a sweep, so `run/fleet_state.json` was never rewritten. The dashboard renders that file, so it kept serving the pre-restart snapshot: six dead rows, frozen on their last 404, for the life of the process. Two of the six (WTA, White Sox) had resolution rows already recorded in `run/fleet.db` (recoverable settled P&L), but the fleet died before settling the in-memory inventory; the whole supervised stack (fleet pulse stopped 10:55:42, supervisor pid gone, port 8800 down) had subsequently crashed.

**Fix.** Extracted the state-file write into `_publish_state(states)` (single site, atomic tmp+rename, try/except degrade) and added `_idle_empty(states, pulse, empty_logged)` which publishes `[]` on the empty-universe transition — the same transition that logs the once-per-episode warning. An empty fleet now writes `[]` exactly once, the dashboard clears the table to "no markets reporting" instead of serving a file the fleet will never rewrite, and the write rides the transition flag so it cannot spam the disk at one write per idle second. The populated sweep-end write now calls the same helper, so the two paths cannot drift. 6 new tests in `tests/test_fleet_state_publish.py` (empty publish overwrites stale file, transition-writes-once, populated path unchanged, write-failure degrade, dashboard end-to-end with `HUNTER_DB` patched, recovery-after-empty renders markets again); 467/467 pass.

**Result.** Verified live the incident shape in tests: a stale populated `fleet_state.json` on disk, `_idle_empty` runs once, the file is now `[]`, and `dash.fleet()` returns `markets: []` with no error. The fix cannot mask a dead fleet: the dashboard heartbeat keys off the pulse's advancing `loop_ts`, not the state file, so a wedged loop still reads stale.

**Verdict.** LIVE. The 404s were the venue truthfully saying the markets are gone; the bug was that an empty fleet never rewrote its dashboard state file, leaving frozen zombie rows. Separate, still-open items surfaced by the same audit: (1) the sweep banner reads "a full sweep is taking 5m10s" while the fleet is merely idle on zero markets — cosmetic, and (2) resolved markets' capital is only freed by a restart that re-adopts them; a startup settle pass for markets already in `resolutions` (U11) is the follow-up.
---

### Session 15 — 2026-08-08: re-rank cadence 60min -> 10min, and why the universe is sports-only (U12)

**Question.** Two operator questions after the empty-universe incident (Session 14): (1) can the fleet find new markets faster than the once-an-hour re-rank, and (2) why does the ranker only ever pick MLB/LoL sports and never other sectors?

**Method.** Traced the cadence. There are TWO independent 1-hour timers, and the fix must hit the right one: `scripts/rerank_loop.py` `INTERVAL_SEC = 3600` regenerates `run/markets.json` (the process actually scores the venue), while `strategy/config.py` `rerank_interval_sec = 3600` is only the fleet's floor for re-reading a file whose mtime already triggers adoption within ~1s of any rewrite. Shortening only the config knob would change nothing -- the fleet already adopts instantly; the bottleneck is the generator. Lowered both to 600s. Then re-ran the ranker live to answer the sector question from the rejection census rather than opinion.

**Result.** Current live census (2026-08-08 ~14:00): 997 funded markets, 199 scored, **199 rejected**. Breakdown: 98 fail YES-side top-3 bid depth (<$1,000), 17 fail NO-side depth, ~27 fail spread (>6c), 53 fail 24h volume (<$250K), 6 blocked dynamic/submarket keyword, 1 horizon, 0 identity-relevant in the liquid pool. The sector answer is therefore NOT selector prejudice: the gates are liquidity gates, and at this hour the venue's genuinely-liquid resting books are almost all sports (MLB/LoL day games). Non-sports markets (crypto, politics, macro) exist but sit below the $250K-volume / $1K-depth / 6c-spread bars -- the same finding as Sessions 11 and 13 (191-199 of ~200 candidates fail on liquidity, not identity). Loosening the gates to admit them would reopen exactly the thin-book/toxic-band losses Sessions 18 and R13-R16 were built to close.

**Verdict.** LIVE. Re-rank now fires every 10 minutes (worst-case wait for a new market drops from 60 to ~10 min, plus the ~2min ranker pass itself). The universe stays sports-dominant during live sports windows because that is where the venue's depth actually is; the selector is not the constraint. Remaining OPEN: (1) the idle sweep banner still reads "sweep taking 5m" on zero markets (cosmetic), (2) U11 startup settle for resolved markets, (3) whether a deliberately-reduced depth bar ($500 / 10c) on a small paper trial would measurably change captures without reopening the loss mechanism -- a candidate for a later controlled experiment, not a config change to make blind.
---

### Session 16 — 2026-08-08: startup settle pass for already-resolved markets (U11)

**Question.** The six dead markets from the Aug 5-6 delisting (Session 14) kept showing as held inventory with committed capital, and two of them (WTA, White Sox) already carry rows in `resolutions`. Why does a restart not free that capital, and how much of the ~$306 was actually releasable?

**Method.** Traced the restart path in `strategy/fleet.py`. `MarketState.__init__` rebuilds each market's inventory from the fills ledger via `_inventory_from_db` — and the fills ledger never learns about resolutions. So a market that settled while the fleet was down restarts holding phantom shares that count as committed capital from the very first heartbeat. The first `visit()` would settle it (the `resolved_cids` check already existed), but only after its turn in the rotation — and worse, if the ranker drops the market before that turn, the re-rank retention rule ("still holding inventory") keeps it in `states` forever on exactly the phantom position. Added `_settle_startup_resolved(states, resolved_cids, now)` which calls the existing `_settle_resolved` for every market already in `resolutions`, wired into `main()` right after the startup `resolved_cids` load, with a `STARTUP SETTLE n market(s) ... released $x committed` log line. Verified against `run/fleet.db`: 8 resolved markets hold fills; the two from the screenshot (WTA, White Sox) are among them. 6 new tests in `tests/test_fleet_startup_settle.py`; full suite 473/473 pass.

**Result.** On restart, markets already in `resolutions` now start the process with zeroed inventory and a settled `_live` payload — committed capital is released immediately instead of after the first rotation (or never, if the ranker dropped the market first). The pass is idempotent with `visit`'s existing resolved check, and only markets that actually held inventory are counted/logged. Of the ~$306 the operator saw committed across the six dead markets, only the two already-resolved ones (WTA ~$5, White Sox ~$99) are releasable by this mechanism; the other four have no resolution row and remain genuinely unresolved — no settlement ground truth exists for them, so their shares stay booked until the venue reports winners (or the operator archives the run).

**Verdict.** LIVE. The startup settle pass closes the restart-window capital leak for every market the venue has actually settled; the unresolved four are a venue-data gap, not a code path this pass can (or should) paper over.
---

### Session 17 — 2026-08-08: one start script, background only (U13)

**Question.** The scripts folder had two launchers — `fleet-start.ps1` (supervisor in the FOREGROUND, tied to a PowerShell window) and `fleet-bg.ps1` (same stack detached and hidden). The operator asked for a single start script, background only, that stops duplicates before starting.

**Method.** Compared the two. `fleet-start.ps1` ran `python -m strategy.supervisor` in the foreground, so the run died with its console window and every child spawned a visible terminal. `fleet-bg.ps1` started the same supervisor hidden with all streams redirected, and — the load-bearing difference — stopped duplicates through the recorded-ownership system in `fleet-procs.ps1` (kill only what THIS checkout recorded in run/fleet.pids.json, report-but-never-kill strays, refuse if port 8800 is owned by an unrelated process), rather than `fleet-start.ps1`'s command-line wildcard match that could kill another checkout's fleet. Kept the name the operator already types (`fleet-start.ps1`), replaced its body with the background version's, and deleted `fleet-bg.ps1`. Updated every reference: `fleet-procs.ps1` header/comment, `scripts/rerank_loop.py` comment, `strategy/supervisor.py` comment, both installed `maker/SKILL.md` copies, and `maker-instincts.yaml` file list. Grep confirms no stale `fleet-bg.ps1` reference survives outside the new header's one-line historical note. PowerShell parse check clean.

**Result.** `scripts/` now has exactly one start script. `.\scripts\fleet-start.ps1` stops the recorded fleet instance (children included) before launching, warns about unowned strays, refuses to start on an occupied 8800, and runs the whole stack hidden in the background. `-FreshRun` still archives the DB first. Behavior is unchanged from what `fleet-bg.ps1` delivered on the last live restarts.

**Verdict.** LIVE. Consolidation only; no runtime behavior change.

---

### Session 18 — 2026-08-08: the phantom 49-minute sweep on an empty universe (U14)

**Question.** Operator reported "Fleet is live, but a full sweep is taking 49m 35s. Per-market figures lag by up to that much" on a fleet that had just been cleanly restarted (14:30:58) into a 0-market universe. Was the loop wedged, or is the venue genuinely slow?

**Method.** Read the live pulse, state file, log, and dashboard payload together. Fleet was NOT wedged: `fleet_stale: False`, pulse written seconds ago, `loop_ts` ~1s old, `markets: 0`, `sweeps: 0`. The "49m35s" was `sweep_age = 3215s` from the dashboard's `_sweep_duration`, which returns `max(sweep_sec, sweep_elapsed)`. `sweep_elapsed = now - _sweep_start` — and `_sweep_start` is set at `_Pulse.__init__` (boot) and rolled ONLY by `sweep_done()`, which runs at the end of a real sweep. An empty universe never completes a sweep: the loop's `_idle_empty` path (added in Session 14 fix a) calls `pulse.touch("", 0)` but never `sweep_done()`. So the in-progress clock measured from boot forever: 14:30:58 -> 15:21 = 3005s, growing a minute per minute. The dashboard's slow-sweep banner (line ~1454, the exact text quoted) fired because `sweep_age > stale_after_sec (120)`.

**Result.** Same bug class as the 30m41s incident the code comments already document (the pre-pulse `_sweep_duration` fallback) — this is the pulse-era variant: the in-progress clock needs rolling even when there is nothing to sweep. Fix: `_Pulse.idle()` rolls `_sweep_start` without recording a measured sweep (`sweep_sec` stays None, `sweeps` stays 0 — an empty pass measured nothing), and `_idle_empty` calls it every pass. One pass over zero markets now rolls the clock exactly as one pass over six does. 3 new tests (idle keeps `sweep_elapsed` small, rolls even from an hour-old anchor, end-to-end dashboard `sweep_age` < 120s while still not stale); 476/476 pass. After the fix the empty-universe banner reads correctly (no slow-sweep warning; the loop is idling, not sweeping).

**Verdict.** LIVE. The fleet was healthy throughout; the meter was lying. The idle path now keeps the sweep clock honest, so an empty universe reads as "idle", not "a sweep is taking 49 minutes". No restart needed to see the effect on the running stack — the fix is in the pulse publish path; but a restart applies it to the live process. An empty or near-empty universe is expected at this hour (Saturday 15:20, between live sports windows; re-rank cadence now 10 min per U12).

---

### Session 19 — 2026-08-08: audit side-effect incident — loosened-bars trial leaked into run/markets.json (U15)

**Question.** Operator asked what filters the selector applies and whether loosening them would admit markets. I ran a live "loosened-bars" audit (volume $100K, depth $500, spread 0.10) to answer with data. The audit's second and third passes invoked `scripts.rank_markets.main()` WITHOUT `--dry-run`, so it overwrote `run/markets.json` with its trial-bars picks (a same-day "Bitcoin Up or Down on August 8?" and an ATP match) instead of leaving the file untouched. The fleet adopted them within ~1s and swept them for ~50s before the real strict re-rank loop rewrote the file back and dropped both.

**Method.** Instrumentation bug, recorded per project convention (instrumentation bugs get their own entry). Root cause: the audit script monkeypatched the ranker's module gate constants for a read-only experiment but forgot `--dry-run`, which is the flag that exists exactly to prevent this. The ranker's normal `--top N` invocation writes the file by design; only `--dry-run` is safe for experiments.

**Result.** Impact was minimal and fully cleaned: the two trial markets scored 0/2 the entire time (never eligible — that is why the operator saw nothing quoted), so no fills, no markouts, no P&L effects. The only residue was 30 `reward_samples` rows written 15:43:03-15:43:54 for the two trial condition_ids; deleted them from `run/fleet.db` (backup at `run/fleet.db.bak-pre-audit-cleanup`), verified zero trial rows remain (48,432 -> 48,402 samples). `run/markets.json` is back to the strict-bars output (0 markets, empty universe at Saturday 15:54 between sports windows), fleet idle and healthy (`fleet_stale: False`, `sweep_age` ~3s, U14 fix holding), no restart needed. Config untouched: volume $250K, depth $1,000, spread 0.06, 30d.

**Verdict.** OPEN as a process lesson: any audit that monkeypatches ranker gates must run with `--dry-run` or restore the file afterward; I will add that to the launcher/audit checklist. The substantive finding stands and was not affected by the incident: loosening the bars to $100K/$500/0.10 still picked 0-2 markets, and the cross-tab showed ZERO depth rejects with volume >= $100K — every thin-book reject is also a volume reject, so the bars are not the reason the universe is thin at this hour; the venue genuinely is.

---

### Session 20 — 2026-08-08: watch_universe.py becomes a supervised child (U16)

**Question.** The operator does not want to sit and watch the evening slate; I built `scripts/watch_universe.py` (logs picked-market count, latest ranker census, and live esports book depth/spread against the selector bars every 5 minutes to `logs/universe_watch.log`) and started it detached. A detached process dies with the console and is never restarted — the same failure shape the supervisor exists to prevent. Should it be a managed child?

**Method.** Added a fourth entry to `CHILDREN` in `strategy/supervisor.py` (`"watch": [sys.executable, "-m", "scripts.watch_universe"]`), with a comment explaining it is read-only (never writes markets.json or the DB) so losing it costs only the log. 2 new tests in `tests/test_supervisor.py` (watch is a supervised child; supervisor owns exactly {fleet, dash, rerank, watch}). Killed the detached instance, restarted the stack via the consolidated `fleet-start.ps1`, verified the new supervisor (9112) spawned all four children including watch (17940), whose first supervised sample landed at 16:45:36. 478/478 tests pass.

**Result.** The watcher now inherits the supervisor's restart backoff and survives crashes and reboots with the rest of the stack. First supervised sample confirms the instrument works: 202 scored, 202 rejected, all five main-line esports books still ONE-SIDED (volume lives on Game-submarket shapes the identity gate blocks; main BO3/BO5 lines carry no two-sided book). One design note for later: the watcher's interval (300s default) is hardcoded in the script, not config — acceptable for an observer, revisit if it ever grows a second consumer.

**Verdict.** LIVE. The evening-slate log now accrues unattended and self-heals with the fleet. Check `logs/universe_watch.log` for the `** FLEET HAS N MARKET(S)` line when the slate turns tradeable.

---

### Session 20 — 2026-08-08: the market-pipeline view — the selection funnel, live (U17)

**Question.** The fleet dashboard shows what the fleet IS on, but not how markets got there — the operator kept asking "why is nothing being picked" and the answer lived in a 4-minute ranker run's stdout, not on any screen. Can the whole selection funnel (raw listing → gates → eligible → adopted) be rendered live, as a visual board, without the ranker re-running on demand?

**Method.** Two additions, one telemetry writer and one view.

1. **The ranker now persists the funnel.** `scripts/rank_markets.py` gained `_write_pipeline_snapshot()` (written on every rank, dry-run included — it is telemetry, nothing reads it as input): the raw pools the ranker listed (reward-funded pool size + top-24 by rate with $/day and horizon; gamma liquid pool size + top-24 by 24h volume with spread), every rejection bucketed by gate with up to 4 example titles + the exact reason, the `attempted/scored/dropped_no_verdict/rejected/eligible/picked` counts, the census line, the gates line, and the eligible (FINAL) and picked (GRADUATED) rankings. Atomic tmp+rename, same pattern as markets.json. The census bucketing helper `_cause` moved to module level so the snapshot and the printed census cannot drift; the spread gate's per-value buckets were collapsed to side-tagged "YES spread"/"NO spread" (the reason text keeps the measured value) — one live run had produced 23 buckets ("YES: spread 0.8250 > 0.0600" × n=1 each), unreadable in both the census and any board.

2. **A second dashboard view.** `server/fleet_dash.py` serves `/api/pipeline` (snapshot + the fleet's CURRENT universe from run/markets.json, annotated with live fleet_state per-market income/capital/share/fills/uptime and a LIVE/QUEUED/ERR badge, plus fleet-alive from the heartbeat), and the page gained a segmented **Fleet / Market scan** switcher in the masthead. The scan view is a four-lane kanban: ① RAW (what the venue lists, both pools), ② FILTERS (refusal gate cards with counts + example markets), ③ FINAL STAGE (eligible, ranked by return per $), ④ GRADUATED (the fleet's adopted universe with live state), joined by arrows, topped by the census chain `RAW 1010 → scored 198 → rejected 198 → eligible 0 → picked 0` with snapshot freshness. Pipeline polls every 10s; the fleet view is untouched and polls as before. Responsive: 4-across on wide screens, 2-up at ≤1500px, single column ≤900px.

**Result.** Verified live end-to-end against the real venue (dry-run rank, pipeline.json written; board rendered the live funnel in-browser: RAW 1010 → scored 198 → rejected 198 (YES depth 96, volume 54, YES spread 21, NO depth 18, group-label 4, keyword 4, horizon 1, no-usable-book 21) → eligible 0 → picked 0 — the thin-Saturday-universe story now visible on a screen instead of in a log). 5 new tests (`tests/test_pipeline_view.py`: snapshot shape + example capture + dropped-without-verdict count + endpoint merge of live fleet state + degradation with no snapshot; `tests/test_dashboard_page.py`: switcher/board wiring). 483/483 pass.

**Verdict.** LIVE. The funnel is now a first-class, always-fresh view: the ranker writes the snapshot every 10 minutes regardless of who asks, and the board answers "why is nothing being picked" by showing the 198 rejections bucketed by gate with names attached. Known limit: the live process must restart to pick up the new page and the snapshot writer — the current stack (pid 9112 family) predates both.

### Session 21 — 2026-08-08: transient book blips no longer cancel quotes or flash STALE/ERROR (U18)

**Question.** The LoL "SK Gaming vs Natus Vincere (BO3)" market flashed `STALE / ERROR` and a -$66.34 unrealized loss roughly every 4-5 seconds, then "came back to life" and repeated. The banner alternated "1/3 markets are unreadable" with normal on every sweep. Is the fleet mis-measuring the book, or is something acting on noise?

**Method.** Traced the full path. `visit` in `strategy/fleet.py` gates every poll on `pair_books_allowed` (YES+NO top-3 bid depth >= $1,000, spread <= 6c). The live NO-side book of that match repeatedly dipped under the bar for a single poll (measured: $33.85 top-3 depth) and refilled a second later. Every failed poll hit the first-failure path: cancel all resting orders, blank every last-known bid field in `_live`, and stamp `st.err`. The dashboard's next poll rendered exactly that: TELEMETRY = `STALE / ERROR` (any `m.err`), the blanked bids valued the naked DOWN position at a $0 exit (unrealized = -cost = -$66.34), and the banner counted the market unreadable. The poll after that found the book healthy, re-quoted, cleared the error — cycle repeats every few seconds. Not a measurement bug (Session 16 already proved the score, the book and the venue agree); the bug was acting on a single-sample blip.

**Fix (U18).** Hysteresis on the book-readiness gates: new `BOOK_GATE_CONFIRM_SEC = 15.0` confirmation window. The first failure records `book_gate_fail_since` and returns early with orders, marks and `err` untouched; only a failure persisting past the window fires the full cancel+stamp path (still well under the dashboard's 120s STALE threshold, and fast enough that a genuinely-dead book gets the protective treatment within a few rotations). A successful gate pass resets the clock, so a recovered blip never accumulates toward a false confirmation. Applied to both the depth/spread gate and the book-fetch exception path (a single venue timeout is equally transient). 5 new tests in `tests/test_book_gate_hysteresis.py` (blip holds / persistent fires / recovery resets / fetch path / end-to-end requote); full suite 488 passed.

**Result.** A 1-2s depth dip now reads as nothing: no order cancellation, no mark blanking, no error stamp, no fake-loss flash. The market keeps quoting (orders stay resting, marks stay fresh) and the dashboard keeps showing the last-good figures until either the book recovers (clock resets) or the failure is confirmed real (15s) — at which point the existing protective behavior takes over exactly as before.

**Verdict.** LIVE. The 15s window is a module constant with the reasoning inline; the failure had no log line until this fix (`_book_gate_confirmed` logs at debug), which is part of why the dashboard flash read as a measurement bug first. Reassess the window only if the venue ever shows blips longer than ~10s or a confirmed failure needs to act faster than ~2 rotations.


### Session 22 — 2026-08-08: the allocator's verdict reaches the Market scan view (U19)

**Question.** The GRADUATED lane of the Market scan view showed income/capital/share per adopted market, but not WHY a market was or wasn't quoted. The operator's question after the earlier funding trace was "which knob do I turn" -- which needs the allocator's own numbers on the board: marginal return, competition, the floor, and the refusal reason.

**Method.** `reallocate` (strategy/fleet.py) computes the funding decision once per sweep from `allocate_fundable` and the measured competition, but the per-market inputs (avg_theirs, the per-share score k, the marginal-return floor) existed only as locals and died with the function. The dashboard had no route to them. Extracted a pure helper `_alloc_verdict(dollars, min_size, pot, avg_theirs, k, floor)` that returns the verdict on the same math the water-fill used (competitor depth `T = avg_theirs/k` dollars, `marginal()` at the allocated size for funded markets or at the first dollar for refused ones), stores it on the MarketState (`alloc_verdict`), and visit's `_live` payload now carries it (`_live["alloc"]`) so it flows through fleet_state.json unchanged. The pipeline endpoint passes it through to the graduated rows; the GRADUATED card renders a verdict line: the reason (green "funded N shares" / red "unfunded: below 2.00%/day floor") plus "marginal X%/day · competition Y · floor Z%/day", with first-dollar marginal, pot and allocated dollars in the tooltip.

Reason strings are derived from the same comparison the allocator makes, so the shown cause cannot drift from the decision: funded, below-floor ("unfunded: below 2.00%/day floor"), allocated-but-under-minimum, min-lot-refused, and unpayable (no pot). k == 0 (we score nothing per share) is guarded to 0%/day rather than NaN, which would have rendered as a fake verdict on the board.

5 new tests (`tests/test_allocator_verdict.py`) pin the verdict math and every reason string; the endpoint test in `tests/test_pipeline_view.py` now asserts the verdict rides through `/api/pipeline` to the graduated row. 493/493 suite pass.

**Result.** Live on port 8800 after restart: both adopted WTA markets render their verdicts -- Kostyuk/Swiatek "funded 135 shares · marginal 2.07%/day · competition 46,969 · floor 2%/day", Borges/Darderi "funded 135 shares · marginal 2.09%/day · competition 28,381". The earlier unfunded trace (Session 16) is now visible as a board card whenever a market is refused: the exact first-dollar marginal vs the 2% floor, which is the number the operator tunes.

**Verdict.** LIVE. The verdict is telemetry over the same inputs the allocator used, recomputed at reallocate time so it cannot drift. When the universe thins again and a market gets refused, the card will show "unfunded: below 2.00%/day floor · marginal 1.16%/day" and the operator can see the gap at a glance.


---

### Session 23 — 2026-08-08: the allocator's verdict reaches the FILTERS lane — near-misses visible a stage early (U20)

**Question.** The GRADUATED lane now answers "why is this quoted?" — but only for markets already adopted. A market the ranker refuses never reaches the allocator, so the operator could not see which near-misses the 2%/day floor would have kept out until after adoption. Can the floor's verdict be estimated for markets rejected one stage earlier?

**Method.** The ranker already measures each candidate's book before most gates: `evaluate` computes the q_min score of the two sides (`their_score`) for every market that clears identity and the book fetch — the same score the fleet averages over its 30-min window as `avg_theirs`. Reused the fleet's own admission math rather than approximating it: new `_if_adopted` in `scripts/rank_markets.py` converts the venue reading to competitor depth exactly as `reallocate` does (`T = their_score / k`, `k = score_per_share(max_spread, OFFSET)`), takes `pot` = the reward rate for reward markets or the spread-capture pot for spread ones (`spread_capture_daily`, same reconstruction as `MarketState.refresh_pot`), and returns the first-dollar marginal %/day against `marginal_return_floor` — the precise number `_alloc_verdict` shows for a refused market. The book-gate reject dict now carries the score reading (`their_score`, `daily`, `spread`, `max_spread`) so depth/spread rejects are estimable too; identity rejects (book never fetched) carry none and get no estimate. `_write_pipeline_snapshot` attaches the verdict to each example and adds a per-bucket `would_fund` count.

Dashboard: `gateCard` renders the estimate under each example (`if adopted: ~2.45%/day · would clear the floor`, red `below floor` otherwise, tooltip with pot/competition/floor) and a green near-miss line per bucket when any would fund; the gate-ex CSS moved its ellipsis to a title line so the marginal line wraps freely.

**Result.** 500/500 tests. 6 new in `tests/test_if_adopted_verdict.py` pin the estimate to `_alloc_verdict`'s refusal math (reward pot, spread pot, k==0 NaN guard, no-reading None, unpayable); the funnel test now covers a depth-$950 reject that would fund at ~2.45%/day versus a crowded reject at ~0.02%/day, plus the identity bucket that must carry no estimate. Verified live on 8800 against the real snapshot.

**Verdict.** LIVE. The estimate is the same math the fleet's `_alloc_verdict` uses, on a single snapshot of the venue's own score instead of the fleet's 30-min average — labeled as such in the tooltip. The near-miss count answers the operator's question one stage earlier: when a depth- or volume-rejected market would have cleared the floor, the only thing standing between it and a quote is the ranker gate it failed.

---

### Session 24 — 2026-08-08: the near-miss tracker — accumulated evidence for a gate-loosening decision (U21)

**Question.** The FILTERS lane shows green near-misses (ranker-rejected markets whose if-adopted first-dollar marginal would clear the 2%/day floor), but pipeline.json is overwritten every ~10-min rank — so the signal cannot accumulate. The operator wants the skipped-greens tracked as a durable data point, with a statistically defined threshold for when there is enough evidence to consider loosening the gate.

**Method.** Two pieces. (1) **Logging:** `scripts/rank_markets.py` now appends one JSONL line per rank to `run/near_misses.jsonl` (`_log_rank_near_misses`, non-dry-run only so an audit cannot pollute the sample): ts, scored/rejected counts, and the full green list — cid, title, cause, reason, source, marg/pot/competition/threshold, volume, horizon, and the measured top-3 depth + bar parsed from the depth-gate reason (`_DEPTH_RE`). One line per rank (greens embedded) so a zero-green rank still records itself — the stability bar needs "no greens" distinguishable from "no data". (2) **Threshold:** `near_miss_stats()` in `server/fleet_dash.py` accumulates the file into four bars, all documented in code with their rationale. The bars are deliberately framed as licensing a CONTROLLED TRIAL, not a gate change, because the log validates CONSISTENCY of a single-snapshot, optimistic estimate — profitability is unobservable until the fleet trades the market. `NEAR_MISS_MIN_DAYS = 3` (cross slate regimes, not one evening), `NEAR_MISS_MIN_UNIQUE = 25` (distinct cids, since 144 ranks/day repeat the same ~15 markets), `NEAR_MISS_MIN_SMALL_MARGIN = 5` (distinct depth rejects measured ≥ 50% of the bar — the operational proof that a specific loosening, e.g. $1,000→$750, admits concrete markets), `NEAR_MISS_MIN_STABILITY = 0.5` over the last 72 ranks (~12h) with ≥1 green (persistent, not one wild hour). Status: READY_TO_TRIAL / COLLECTING / NO_DATA. Served on `/api/pipeline` as `near_miss` and rendered as a NEAR-MISS TRACKER panel on the Market scan view (badge, progress tiles per bar, pot-on-the-table, green-by-gate line, and the honest note that a trial measures whether it pays).

**Result.** 508/508 tests (8 new in `tests/test_near_miss_tracker.py`: logger writes only greens + parses depth, zero-green ranks still log, COLLECTING until every bar, READY_TO_TRIAL when all bars met, missing/corrupt file handling). Verified live on 8800: the tracker seeded itself within two supervised ranks — 2 ranks, 38 green observations, 20 unique markets, $183/day pot on the table, causes 30 depth / 8 spread, stability 100%, status COLLECTING. A real finding already: every green depth-reject measured ≤ $434 (max) — all under 50% of the $1,000 bar — so the small-margin count is 0 and even a $750 loosening would have admitted none of today's near-misses; the markets the floor would fund are not close to the depth bar.

**U21 follow-up (same session).** The tracker's "pot on the table" tile summed every unique green's pot, so the empty-window traps dominated it: the Yankees game alone ($15,768/day pot on $22 of measured depth) made the all-in total read $16,124/day while the credible total was ~$3/day. `near_miss_stats` now classifies each market's LAST reading (unique-cid basis) with `_is_trap` — depth under half the bar OR marg > 10%/day — and sums the credible set only: the tile reads $3/d with an "excl. traps 29 ($16,121/d)" companion tile, so the gap stays visible rather than hidden. 3 tests updated/added (credible vs raw pot, trap count, the deep-but-mirage case pinned: a $15,000-pot market with healthy depth but 1,500%/day marg is still excluded); 510/510 pass. Live on 8800: pot-on-table $3/d, 29 traps excluded.

**Verdict.** LIVE. The tracker is the honest accumulation layer the green estimate needed: it converts "15 of 95 would fund" from a per-rank curiosity into a persistent, bar-checked evidence base whose READY_TO_TRIAL state will name a specific candidate list (the small-margin depth markets) when it arrives. The first live reading already corrects a possible misread of the FILTERS lane — today's greens are deep under the depth bar, so a loose gate would not have captured them anyway. The follow-up above falls under this verdict: the trap rule changes what the tile SUMS, not what the tracker decides.


---

### Session 25 — 2026-08-09: the empty-book mirage — 890%/day and 4,938%/day are divide-by-nothing artifacts, not opportunities (U22)

**Question.** The FILTERS lane started showing absurd if-adopted estimates — "~890.91%/day · would clear the floor" on the Dem-retirees book, "~4,938.27%/day" on UK inflation — painted green next to a "YES: spread 0.0980 > 0.0600" rejection. Are these real opportunities being blocked, or garbage?

**Method.** Reproduced the math. The first-dollar marginal is `pot / T` (from `marginal(0, pot, T) = pot*T/(T^2)`), where T is the competition reading in dollars (`their_score/k`). A book with ~nothing resting inside the reward window reads T ≈ $0.32, so `pot / T` explodes: $16 pot / $0.32 = 4,938%/day. Confirmed against the live log: every absurd estimate (890%, 4,938%, 308%, 231%...) carries a competition reading under 5 — these are the SAME empty-book shape as the Yankees $15,768/day-on-$22-depth trap, expressed through near-zero competition instead of a giant pot. They are not blocked opportunities; the estimate is a divide-by-(almost)-nothing artifact, and the wide-spread reason on the card ("spread 0.098 > 0.060") is the venue's own signal that nothing qualified for rewards there.

**Fix (U22).** Added a `trap` flag to the if-adopted verdict with the same rule the pot tile already used (depth < 50% of the gate bar, OR marg > 10%/day) and pushed it through every surface that was treating the mirages as evidence:
- `scripts/rank_markets.py`: `_if_adopted` classifies `trap` (parsing the measured depth out of the reject reason when present); `_write_pipeline_snapshot` counts `would_fund` over CREDIBLE verdicts only and exposes a per-bucket `traps` count; the near-miss logger embeds `trap` per green.
- `server/fleet_dash.py`: `near_miss_stats()` now bases EVERY decision bar (days, unique markets, small-margin depth, stability, per-cause) on the credible set only — a wild rank of empty books can no longer trip "enough evidence" on garbage; returns `traps` so the panel can show what was excluded. `gateCard` renders traps in amber as "EMPTY-BOOK MIRAGE — nobody resting in the reward window, the estimate is not real" with a per-bucket "N empty-book mirages here" line; the tracker gained a "mirages seen N (not counted)" tile and renamed the per-gate line "credible green by gate".
- Tests: 3 new trap tests in `tests/test_if_adopted_verdict.py` (empty-window → trap even when it arithmetically clears the floor; $22-depth reject → trap; $950-depth reject → not trap), the tracker's decision-bar tests rewritten to credible-only semantics, plus a traps-do-not-feed-bars test and a zero-depth-edge test. 515/515 pass; node --check OK; verified live on 8800.

**Result.** Live numbers went from misleading to legible: the tracker had been counting ~440 near-miss observations as evidence; the credible set is 34 observations across 6 unique markets, with 406 mirage observations now shown as "mirages seen: 406 (not counted)". The pot tile that read $16,124/d raw now shows $23/d credible against $21,010/d raw (46 unique trap markets excluded). On the current snapshot the YES-depth bucket's `would_fund` fell to 0 (82 traps labeled — the depth-arm catches the $22/$36/$61 books even when their estimate is sane) while the YES-spread bucket kept 2 genuine near-misses (e.g. Abraham Accords at 3.86%/day, trap False). Old log lines without the flag still classify correctly because the stats recompute the rule from depth/marg.

**Verdict.** LIVE. The mirage estimates were a real display defect (green "would clear the floor" on 4,938%/day is a lie) and are fixed at every surface; the underlying depth/spread gates were never admitting these markets, and the trap rule is a label, not a new gate — the floor and the depth bar are unchanged. The decision bars now measure credible evidence only, so the READY_TO_TRIAL verdict can no longer be tripped by a parade of empty books. Watch: `unique markets` (6/25) and `small-margin depth` (2/5) on the tracker — both need real, sustained credible near-misses before the trial question is even on the table.


---

### Session 26 — 2026-08-09: the empty-book mirage — structural or measurement? The evidence says both, and neither is what we guessed (U23)

**Question.** Almost every green near-miss in the tracker is an empty-book mirage. Two competing explanations were on the table: (a) STRUCTURAL — the venue pays big pots exactly where nobody quotes, so an empty reward window is the market's honest state; (b) MEASUREMENT — our formula, window, or sampling makes books look emptier than they are. Which is it?

**Method.** Three probes, all against the live venue.
1. **Formula audit.** `their_score` in the ranker is the venue's exact `S(v,s) = ((v-s)/v)^2 * size` summed over every resting order inside `rewardsMaxSpread` from the mid with `rewardsMinSize` applied, folded through `Q_min` with the one-sided penalty C=3 — the same formula previously validated against the venue's own reported score (37.04 for our 120-share quotes). Venue docs confirm the formula and that the epoch score is a SUM of one-per-minute samples over 10,080 samples (7 days), normalized per sample. No formula or window discrepancy found.
2. **Book reconstruction.** Re-fetched the live books for four near-miss markets (UK inflation, Dem-retirees, Abraham Accords, Demuth) and recomputed the score with the ranker's exact math. The ranker's reading reproduced: UK inflation recomputed 0.9 vs the ranker's 0.9. The window is genuinely empty of TIGHT quotes — the orders that exist sit 3.8–4.3c from the mid in a 4.5c window (where the quadratic weight collapses to ~1% of the touch value) or 13c out (penny bids of 79,819 shares at 0.001 — parked, zero score). The books are wide (8.1c spread on UK inflation) with real dollar depth that scores almost nothing.
3. **Stability and correlation.** UK inflation re-sampled 4x over 60s: their_score 0.9, 0.9, 0.9, 0.9 — stable at that instant. But across the 30 ranks in `near_misses.jsonl`, the SAME market's competition swings 0.1 → 99 between 19:00 and 22:00 UTC — a 1000x time-of-day swing. Cross-market: Spearman(rate, their_score) = +0.59 over 50 sampled funded markets; pot ≥ $30/day had ZERO empty-window markets, pot < $30 had 20% (5/25).

**Result.** Both hypotheses are wrong in their naive form, and the truth is sharper.
- The "big pots only exist where nobody quotes" claim is FALSE — it is inverted. Big pots sit on deep, crowded books (Iran $600/day on their_score 111k, F1 $156/day on 85k, Ballon d'Or $125/day on 31k). Pots are pre-allocated per market; they attract makers. The near-miss mirages are the SMALL-pot tail ($3–54/day).
- The empty-window reading is REAL (faithful formula, reproduced book, stable over a minute), but it is a TROUGH-HOUR sample of a time-varying process. The same market that reads 0.1 at 19:00 reads ~99 at 22:00. The venue's epoch score averages all 10,080 minute samples; a maker resting 24/7 faces the day-average competition, not the trough. The "4,938%/day" estimate is therefore a trough-hour fantasy: it assumes the trough lasts forever.
- The structural core is the market's own judgment: nobody quotes TIGHT on these specific-bin questions (inflation ranges, retiree counts), so the books stay wide and the venue's quadratic penalty makes every parked order score ~nothing. The depth gate refusing them is mirroring the market's own reluctance, not an artifact.

**Verdict.** OPEN with a refined model. The mirage labeling (U22) is correct as a point-in-time statement but the classification flickers with the hour: the same market is a 4,938%/day mirage at the trough and a ~1–3%/day credible near-miss at the peak, so `trap` flips rank to rank. The natural next step is to make the near-miss estimate time-robust: average several book samples per rank, or feed the tracker's own accumulated competition readings back into the estimate so a market is judged on its day-average, not its luckiest snapshot. That is a measurement improvement, not a gate change — the depth gate stays as-is.

---

### Session 27 — 2026-08-10: the sweep's settle-and-cancel step behind a module interface (issue #11)

**Question.** The per-market sweep in `strategy/fleet.py` was one 630-line function owning settle, cancel, gate, decide and record inline, inside a module that imports 14 others. Can the settle-and-cancel step move behind the sweep module's interface -- behavior-preserving -- so it is testable on its own?

**Method.** Moved `_settle_resolved`, `_cancel_live_orders`, `_record_event` and `_settle_startup_resolved` verbatim into a new `strategy/sweep.py` (slice 1 of the sweep extraction; the gate/decide steps stay in `visit` for slice 2). The fleet loop now imports and calls them. Safety net: the existing fleet tests (startup settle, state publish, gate fallback, resolutions) must pass unchanged; 4 new direct tests drive the step through the module interface (settle zeroes both legs and refreshes the payload fresh; cancel blanks the payload and persists; record_event collapses repeats and honours force).

**Result.** 524/524 tests pass (520 before + 4 new). The functions' bodies were not edited -- only relocated -- and `visit`'s call sites renamed. The startup-settle suite now imports the step from the sweep module instead of the engine.

**Verdict.** LIVE. Slice 1 landed with zero behavior change and a new direct test surface. Watch: the gate/decide extraction (issue #12) is slice 2; `fleet.py` should keep shrinking toward orchestration-only.

---

### Session 28 — 2026-08-10: the whole market sweep behind one interface — gate and decide extracted too (issue #12)

**Question.** Slice 1 (#11) moved settle/cancel behind the sweep module. Can the remaining ~600 lines of `visit` — the identity gate, market load, book gate, fills, gate advance, exits and requote — move behind one `sweep(state, ctx) → SweepOutcome` interface, leaving `fleet.py` as orchestration?

**Method.** Rewrote `strategy/sweep.py` with `SweepContext` (bot_cfg, now, fleet-wide totals, states, posture, resolved_cids) and a frozen `SweepOutcome` (status, prev_gate, gate, why, fills, requoted, released). The public `sweep()` runs books → fills → gate → exits → requote → reward sample and maps every early exit to an outcome status (SETTLED, IDENTITY_BLOCKED, COOLDOWN, UNLOADABLE, BOOK_HOLDING, BOOK_FAILED, else QUOTING/BLOCKED/WAITING). `visit` became a thin backward-compatible alias building a SweepContext from its old signature; `main()` still calls it, unchanged. Step helpers moved with their bare `now` reads converted to `ctx.now` (19 sites in 5 functions). Tests updated to the new homes (monkeypatch targets → `strategy.sweep.full_book`/`recent_trades`, imports split between fleet and sweep); 4 new tests drive `sweep()` through its one interface.

**Result.** 528/528 pass (524 + 4 new). The alias maps positional args exactly; the only code change beyond relocation was the `now` → `ctx.now` conversion, and a premature-return slip of mine was caught by the existing hysteresis tests. `fleet.py` is down from 1,983 to ~1,100 lines and imports the sweep module.

**Verdict.** LIVE. The sweep is one interface with private step seams; tests assert outcomes, not engine internals. Watch: #13 (state reader) and #14 (un-merge main.py, whose fetcher imports now live in sweep.py) are the next slices.

---

### Session 29 — 2026-08-10: one read-side module for the fleet's KPIs (issue #13)

**Question.** Read SQL was split across the report module (`kpi.py`), the dashboard page (`fleet_dash.py` -- with its own read-only connection and cached running totals) and the engine's inventory rehydration (`fleet.py`). A schema change meant three files of coordination. Can every read query move behind one state-reader module, leaving the report module pure computation and the page HTTP + HTML?

**Method.** New `strategy/stats.py` owns every read query: kpi's row fetchers (via the write module's connection), the dashboard's readers (read-only connections to `run/fleet.db`, including the incremental maker-rebate cache + lock, moved with the reads), and `inventory_from_db` from the engine. `kpi.py` kept its pure math (`taker_fee`, reward-share computation) and calls the state reader; `fleet_dash.py` kept `_pulse`/`_heartbeat`/`_sweep_duration` (pure/file) plus the three endpoints, and `fleet()` pulls the whole DB-derived payload with one `snapshot()`. `fleet.py` lost its last direct SQL. Import cycle avoided by direction: kpi imports stats at module level, stats imports kpi lazily (outside the lock) for `taker_fee`. Tests updated to import the readers from `strategy.stats`; 4 new tests pin the surface (snapshot payload, db_stats aggregation, kpi.report through the state reader, inventory rehydration).

**Result.** 532/532 pass (528 + 4 new). No SQL remains in `kpi.py` or `fleet_dash.py`; the only SQL in `strategy/` + `server/` lives in `store.py` (write module) and `stats.py`. Interpretation note on acceptance criterion 4: `scripts/` (`fetch_trades`, `measure_fill_rate`, `record_books`, `replay_risk_gates`) still issues SQL against its own databases -- those are standalone tools, not engine modules, so their queries were left in place.

**Verdict.** LIVE. One read seam, one write seam, and a `snapshot()` the page calls once. Watch: #14 (un-merge main.py, whose fetcher imports now live in sweep.py) and #15 (evaluate fetch seam) are the remaining frontier.

---

### Session 30 — 2026-08-10: un-merge the entry point (issue #14)

**Question.** `strategy/main.py` (the retired 8788 bot's entry point) still held the two live market-data fetchers -- `full_book` and `recent_trades` -- which the sweep module and the live-test script imported from it. Keeping dead-bot code as the import home for live code couples the fleet to a file that should be gone. Can the fetchers move into the markets module and the entry point be deleted outright?

**Method.** Moved `full_book` and `recent_trades` verbatim into `strategy/markets.py` (which already owned the pooled session and market-identity helpers), carrying the `TRADES_API`/`BOOK_TIMEOUT`/`TAPE_TIMEOUT` constants and a module logger with them. `strategy/sweep.py` and `scripts/live_test.py` now import the fetchers from `strategy.markets`; sweep's namespace re-exports the same function objects so the existing `strategy.sweep.full_book` monkeypatch seams in the fleet tests keep working unchanged. Five prose comments naming `strategy.main` updated to point at the archive. `strategy/main.py` deleted with `git rm`.

**Result.** 534/534 pass (532 + 2 new). New `tests/test_markets.py` pins the relocation: both fetchers importable from `strategy.markets`, and sweep re-exports the identical objects (identity check) so the monkeypatch seams still control them. Repo-wide grep confirms no live reference to `strategy.main` remains in `.py`, `.md`, `.ps1`, `.toml`, `Dockerfile` or other config (archives/skills/research are historical and intentionally untouched).

**Verdict.** LIVE. The entry point is gone; live code imports live code from the markets module. Watch: #15 (evaluate fetch seam) is the last slice.

---

### Session 31 — 2026-08-10: the fetch seam under the market scorer (issue #15)

**Question.** The scorer in the selection script (`evaluate` in `scripts/rank_markets.py`) was the one ranker path with no offline coverage: its gates — identity, book health, volume, horizon, payout floor — had only ever run against the live venue, and any change to them could only be verified by a network round trip. The universe fetchers (`gamma_volume`, `gamma_spread_universe`) already receive their HTTP session across the seam. Does the scorer too, and can its gates be locked with stub sessions so the whole selection funnel runs offline?

**Method.** Audit first: `evaluate(session, ...)` has taken the session since the repo baseline and `main()` passes the pooled session through the worker pool; the scorer's only fetch is one `session.get(.../book)` per token, and every helper it calls (`identity_allowed`, `pair_books_allowed`, `tradable`, `days_to_resolve`) is pure. What was missing was the second half of the contract — tests. New `tests/test_scorer_gates.py` drives every gate against a stub CLOB session that serves canned books and refuses any other request (the refusal is the offline guarantee: a regression reaching for the network fails loudly). Fourteen tests cover the seam itself (a monkeypatched `requests.Session`/`requests.get` turns any self-opened connection into a hard failure, and the stub proves exactly two book GETs, one per token), the no-verdict drops (fetch failure, one-sided book, near-settled mid, <2 tokens, reward window narrower than our offset), the verdict rejects (identity without fetching, depth with its measured reading, spread, volume, horizon, reward payout floor, and the spread-source exemption), and the admission path (income/capital recomputed from the score formula against the canned book).

**Result.** 548/548 pass (534 + 14 new), zero network. The gates that used to be live-venue-only now run offline against stub sessions; the seam test pins criterion 1 (the scorer takes the session and opens no connection itself) so a future edit that adds a stray `requests.get` inside the scorer fails the suite instead of leaking a real request.

**Verdict.** LIVE. Test-only change — no production code, no strategy parameter, no new sample. This closes the last slice of the fetch-seam frontier named in Sessions 29/30.

---

### Session 32 — 2026-08-10: offline coverage for the volume reader (issue #15 follow-on)

**Question.** The scorer's fetch seam is now locked with stub sessions (#15). The other universe fetcher that still had zero offline coverage was `gamma_volume` — the ranker's 24h-volume reader, which queries gamma in chunks of 20 `condition_ids`. Its chunking and its error tolerance had only ever run against the live venue. Can it get the same stub-session treatment?

**Method.** Four tests in `tests/test_selection.py`, against a stub gamma that serves volume rows keyed by condition_id and records every request (and can be told to fail a specific chunk): chunking (45 ids → 3 requests of 20/20/5, `limit` matching each chunk, URL pinned to `GAMMA`), error tolerance (the second chunk fails → that chunk's ids are absent, the rest intact, no exception), response-shape tolerance (a `{"data": [...]}`-wrapped response, a row with no `conditionId` skipped, a null volume reading as zero), and the empty-candidate edge (no ids → no requests, empty result).

**Result.** 552/552 pass (548 + 4 new), zero network. The volume reader's two contracts — never ask for more than 20 ids at once, and a partial map beats a crashed read — are now pinned offline, same as the scorer's gates.

**Verdict.** LIVE. Test-only change — no production code, no new sample.

---

### Session 33 — 2026-08-10: the venue-payload parse seam (architecture review, C1+C2)

**Question.** The architecture survey asked whether malformed book-payload handling is an isolated parse gap in the ranker's scorer or a systemic seam problem. Answer: systemic — five live copies of the same `float(x["price"])` comprehension, three failure modes (ranker crash, fleet misclassification, tape silent-skip); `resolve.py` is the one site already fail-closed. Can the parse converge on one adapter?

**Method.** Added `strategy.markets.parse_book` — the parse half of the fetch seam. Contract: row-level garbage (unparseable price/size, non-dict row) is **skipped and counted** in `malformed` — the same tolerance the selector's depth gate already applies to its inputs; a structurally wrong payload (not a dict, side not a list) **raises ValueError** as a fetch-shaped failure. Five call sites converged on it: `full_book` (no longer raises on row garbage, so the sweep's book gate can no longer mistake a bad level for a dead network — the misclassification dies by construction), `recent_trades` (bad tape rows skipped + a non-list response guarded — previously one bad price crashed out of the loop and the sweep's "exceptions propagate" contract turned it into a market that vanished from every sweep with no status, no err, no event), the ranker's `evaluate` (fails closed on `malformed > 0` — a skipped competitor under-counts `theirs` and inflates projected income, the dangerous direction for a funding decision; previously the exception aborted the whole ranking run through the ThreadPool), and the two tool scripts `watch_universe` / `record_books`.

**Result.** 561/561 pass (552 + 9 new: parse_book shape/skip-count/structural/empty, full_book regression, tape regression + non-list guard, scorer malformed + structural). `resolve.py` was confirmed already fail-closed and left untouched — the survey's negative finding stands. CONTEXT.md gained the **Book adapter** term. The parse exists in exactly one place; the ranker can no longer crash on venue data.

**Verdict.** LIVE. The parse moved behind one interface with one contract; three distinct failure modes collapsed into classified outcomes. No strategy-parameter change — the tolerance matches what the selector already did, so no new sample.

---

### Session 34 — 2026-08-10: one session per worker thread (architecture review, C3)

**Question.** The ranker's scoring pool runs `evaluate` across 12 ThreadPoolExecutor workers, all fed ONE shared `requests.Session` created in `main`. The requests documentation is explicit that a Session is not thread-safe. Is that shared session a real hazard, and does giving each worker its own session change any behavior?

**Method.** Audited the session flow: `main` uses one session for its sequential up-front universe fetches (sampling-markets, gamma volume, gamma spread) and hands the SAME session to every pool worker through the `ex.map` lambda. Added two pieces behind the #15 seam (`evaluate` still takes a session and opens no connection itself): a `_worker_session()` lazy thread-local factory (one session per thread, keep-alive pooled within the thread, reused across that thread's markets) and a `score_pool(jobs, *, session_factory, max_workers=12)` seam that `main` now calls instead of the inline loop. Two tests pin it: (1) 12 barrier-synced threads each call the factory twice — the same object within a thread, 12 distinct objects across threads; (2) the real pool path with a counting Session patch, where each job's stub `get` sleeps long enough to hold all 12 workers alive mid-fetch — exactly 12 sessions created, one per worker, never one shared. The sleep is load-bearing: the executor never guarantees one thread per job, and an instantly-finishing worker goes idle and gets reused, collapsing the count.

**Result.** 563/563 pass (561 + 2 new). The ranker's 12 workers now each own a keep-alive pool; the sequential universe fetches keep their own. Scoring behavior is unchanged — `evaluate`'s contract is untouched, and the #15 seam tests still hold.

**Verdict.** LIVE. Latent shared-state hazard removed; the per-worker session seam is testable and pinned. No strategy-parameter change, no new sample.

---

### Session 35 — 2026-08-10: the depth-gate trial (U32)

**Question.** The near-miss tracker crossed every bar it needs to license a controlled depth-gate trial (READY_TO_TRIAL: 3/3 days, 29/25 unique markets, 19/5 small-margin depth, 69% stability) — and the funnel the same morning admitted 0 of 202 scored markets on 109 depth rejects. Which concrete markets would a $750 depth bar (vs the permanent $1,000) adopt, at what projected income — and how is the trial staged so markouts are watched before the gate changes permanently?

**Method.** Two pieces. (1) A trial bar that never touches the permanent config: `select_min_top3_depth_usd_trial` (env HUNTER_DEPTH_TRIAL_USD) plus a `--trial-depth` flag on the ranker, resolved CLI > config > permanent; `evaluate`/`score_pool` take `min_depth_usd`; a trial run tags adopted markets `trial_depth_usd` in run/markets.json and records `depth_gate_usd`/`trial_depth_usd` in pipeline.json. (2) `scripts/trial_depth_gate.py` — an offline replay of run/near_misses.jsonl (214 ranks, 3,605 greens, 116 unique markets, 98 depth-reject markets) that grades each market's LAST depth reading against a trial bar, re-deriving the near-miss mirage rule against the trial bar (mirror of `fleet_dash._is_trap`): graduates = depth >= bar and not a mirage; near = 0.5*bar <= depth < bar.

**Result.** At $750 the replay admits exactly ONE market on recorded evidence — Cleveland Guardians vs Chicago White Sox (slug mlb-cle-cws-2026-08-09), measured top-3 bid depth $760, pot $1,667.47/day, if-adopted marginal 2.57%/day (clears the 2% floor). **But the recorded greens carry no end date, and checking the slug shows this game has already resolved** — its depth and pot are a stale rank-time reading of a concluded market. So the honest answer to the trial question is sharper than the count: the $750 bar currently adopts NOTHING actionable. The $500 sweep admits 5 markets ($1,863/day), four of them live political markets (~$196/day combined: MN-02 Governor GOP primary $97, WI-01 House $55, Republican Senate count $41, a crypto launch $3) plus the same stale MLB game. Bar sweep: $1,000 → 0 graduates (consistent — every depth-reject measured <= $1,000); $750 → 1 (stale); $500 → 5. Two honest gaps surface together: the depth-reject population clusters far below the bar (91–93 of 98 are empty-book mirages), and the replay's last-reading basis is stricter than the dashboard's any-reading small-margin count (19), so the all-time READY_TO_TRIAL license overstates what a trial would adopt today. 10 new tests (evaluate gates on the trial bar and not by accident, bar resolution CLI > config > permanent with invalid-value fallback, env sets the trial without touching the permanent bar, pipeline snapshot records the trial bar, replay grading/trap re-derivation/last-reading dedup/absent-log). Full suite 573/573.

**Verdict.** LIVE — with the bar choice corrected by the evidence. The trial mechanism is implemented and staged; the replay says $750 is too tight to be useful right now (its only graduate is an already-resolved game), and $500 is the bar that admits live markets. The live flip (running the ranker with `--trial-depth 500` so run/markets.json gains the four live political markets, then watching their markout tiles) is the deliberate next step — not taken in this session, because it changes what the live fleet quotes and each graduate must be re-verified against the live venue first.

**Corrigendum (same session).** Regenerating `run/trial_depth_report.json` with the reviewer-fixed mirage rule changes the sweep numbers above: the $500 sweep is **6 markets / $1,913.47/day** (not 5 / $1,863) — the sixth is a live crypto-launch market (Extended FDV > $300M one day after launch, ~$50/day), so live income is **~$246/day across 5 live markets** (3 political $193 + 2 crypto $53) plus the stale MLB game. Mirage counts with the corrected rule: **91** of 98 at $1,000 (was 93), **89** at $750 (was 91), **83** at $500. The headline is unchanged — $750 adopts nothing actionable; $500 is the trial bar.

---

### Session 36 — 2026-08-10: mirage triage and the live $500 trial (U33)

**Question.** U32's replay showed the depth-reject population is mostly empty-book mirages, and its trial bar was chosen on recorded evidence alone. Two questions follow: what makes a depth reading "look real but not durable" (the mirage mechanism), and can the ranker's depth check be sharpened to filter mirages before they reach the near-miss log? In parallel, the trial itself went live: what does a real `--trial-depth 500` rank actually adopt, after re-verifying the recorded graduates against the live venue?

**Method.** Two pieces. (1) `scripts/mirage_triage.py` — an offline triage of the recorded near-miss log (now 220 ranks, 110 depth-reject markets after the trial rank appended). It buckets every market on its LAST reading at a given bar with the same trap rule as `trial_depth_gate._is_trap` (depth < 0.5*bar, or marginal estimate past 10%/day), cross-tabs the population against the live volume gate (config `select_min_volume_24h_usd` = $250k/24h — the gate the live ranker scores depth AFTER), and measures candidate sharpening signals as precision/recall: mirages excluded vs near-misses/graduates wrongly excluded. 5 new tests. (2) The live trial: a `--trial-depth 500 --dry-run` re-verification first, then the real `python -m scripts.rank_markets --trial-depth 500`, which tags adopted markets `trial_depth_usd` in run/markets.json and records `depth_gate_usd`/`trial_depth_usd` in pipeline.json.

**Result.** The triage overturns part of U32's premise. Mirage profile: median depth 0.071 of the bar (~$71), median 24h volume $232 — against $7,183 for near-misses. Mirages are tiny-depth, near-zero-volume. Two distinct species in the sample: one-shot readings on liquid sports markets (Eala tennis $585k vol, Braves–Yankees $271k, Blue Jays $265k — depth read thin once and vanished from the log) and recurring empty-book politics/crypto markets (Catalina Lauf seen 94 times at $93 volume, CO-03 House 18 readings at $0, GOP House count 74 readings at $0). The sharpening signal that works with ZERO false positives on recorded evidence: **seen in >= 2 ranks** — 18/104 mirages excluded, 0/6 near-misses lost (>= 3 ranks: 30/104 excluded but loses 1/6). Volume floors exclude more (78/104) but would gut the near-miss signal (5/6 lost) — too aggressive. **The dominant finding is the VOLUME gate, not the depth gate: 83 of the 110 depth-rejects — and 5 of the 6 near-misses and 5 of the 6 recorded $500 graduates — would fail the live $250k/24h volume gate.** The near-miss log is watching a population the live pipeline would reject on volume anyway; and the replay's pot figures are not volume-filtered — the cross-tab counts MARKETS, and 5 of the 6 graduates it reports at $500 fail the live volume gate (U32's own largest pot item, the resolved MLB game, was volume-eligible — so pot and volume-eligibility do not track each other). The live trial confirms it from the other side: the real `--trial-depth 500` run adopted exactly ONE market — the same LoL spread market the permanent bar already picks (spread-source is exempt from the depth gate) — and ZERO reward markets. The recorded political "graduates" fail live on volume by 1–3 orders of magnitude (recorded 24h volumes $16k, $670, $24, $0 against the $250k bar). Reject mix at $500: 91 depth, 80 volume, 26 spread — loosening depth merely moved rejects to the volume gate (the same morning's $1,000 run: 120 depth, 51 volume).

**Verdict.** LIVE — with the trial target corrected by the live re-verification. The depth gate was never the binding constraint the READY_TO_TRIAL license assumed: the recorded depth-reject population is ~83% volume-ineligible, and the live $500 trial adopted nothing (the mechanism works — tags, pipeline, dry-run — there is simply no live opportunity at $500 right now). Two actionable outputs: (1) the >= 2-readings consistency signal is a zero-false-positive candidate for filtering one-shot mirages before the near-miss log (18/104 on recorded evidence); (2) the near-miss tracker's real frontier is the VOLUME gate — a volume near-miss tile would watch the ~80 markets the pipeline currently rejects on volume. The trial stays staged; reverting is running without the flag, and the permanent $1,000 bar was never touched.

---

### Session 37 — 2026-08-10: the volume near-miss tracker (U34)

**Question.** U33's closing verdict named the next frontier: the near-miss tracker has been watching the DEPTH gate, but the binding constraint is VOLUME — on recorded evidence 83 of 110 depth-rejects (and 5 of 6 near-misses) would fail the live $250k/24h bar, and the depth-reject log never saw volume rejects at all (volume is gated after depth inside `evaluate`, so a volume-reject carries a real book and is refused by `tradable`, never reaching the would-fund greens). How do we watch the ~80 markets per rank the volume gate refuses — the population the depth tracker was blind to?

**Method.** A volume sibling to the depth tracker, end to end. (1) Ranker: `_log_rank_volume_near_misses` appends run/volume_near_misses.jsonl — one line per rank, exactly like the depth log — recording EVERY volume-rejected market whose reason carries a measured reading (a `_VOLUME_RE` parses "24h volume $X < $Y" out of the reject reason; "volume unknown" is counted as a data gap and skipped). Each entry carries the allocator verdict too (pot, competition, marg) so the panel can show what the population is worth. (2) Dashboard: `volume_near_miss_stats()` mirrors `near_miss_stats()` with the SAME decision bars — 3 calendar days, 25 unique markets, 5 small-margin, 50% stability over the last 72 ranks — where "small-margin" means measured volume >= half the bar ($125k, the modest-loosening marker), plus `watched` (the whole measured population) and a `closest` list (top 5 by last-reading volume/bar ratio). Deliberately NO marg-estimate trap arm: a volume-reject has already cleared the depth gate, so its book is real and a big pot/competition ratio is an opportunity signal, not the empty-book mirage the depth trap exists to catch. (3) The Market scan view renders a VOLUME NEAR-MISS TRACKER panel beneath the depth one. 7 new tests (ranker logger: measured entries, unknown skipped but counted, empty rank still logged; reader: COLLECTING until every bar, READY_TO_TRIAL when met, unmeasured = gap not near-miss with a 0.0 reading still watched, NO_DATA on missing file, unknown-total + closest ranking).

**Result.** Full suite 585/585. The tracker began filling the SAME afternoon, not on the next deploy: the supervised ranker spawns a fresh `python -m scripts.rank_markets` per cycle, so the 16:34 rank (ts 1786368843) ran the new code and wrote the first line of run/volume_near_misses.jsonl — one rank, 196 rejected, 55 distinct volume-rejects with measured readings, 0 unknown. First-read evidence (one rank only, no consistency yet): the closest markets to the $250k bar are 'Will the Democratic Party control the House after the 2026 midterms?' at 42.2% of bar ($106k/24h), the U.S.-invade-Iran market $98k (39.2%), United Russia seats $97k (38.7%), Fed rate hike $51k (20.4%), Clarity Act $34k (13.7%) — and all five are LONG-DATED (50–143 days), which is the horizon-gate (30d) conversation a volume loosening would hand straight to: even a half-bar trial would admit none of them today, because horizon refuses them next. small_margin_volume (>= $125k) is 0/5, days 1/3, unique 55/25, stability 1.0 — COLLECTING, with the binding bar at zero exactly as the evidence predicted. The measured picture is richer than U33's recorded $16k/$670 portrait: the volume-reject population includes markets at 20–42% of the bar carrying real pots (Iran $600/day), so it is CLOSER to its bar than the depth-reject population was — it is just long-dated. The trial the license would eventually justify (a staged half-bar loosening with adopted markets' markouts watched, mirroring the depth trial) is NOT implemented — it is the deliberate next step and changes what the live fleet quotes.

**Verdict.** LIVE. The volume gate's reject population is now watched with the same evidence machinery that licensed the depth trial, pinned by tests, and the data gap closed the day the code landed — the supervised ranker picks up new code on its next cycle, so the log is already accumulating (55 markets in one rank). The count that binds is small_margin_volume; the first-rank read says the closest rejects are 13–42% of the bar and long-dated, which is the honest signal the gate is not rejecting near-misses today. The depth tracker stays as-is; the two panels show the two gates' populations side by side, which is the point U33 made visible.

---

### Session 38 — 2026-08-10: the pairs-only rule (U35)

**Question.** The fill-level decomposition of the 112h clean sample (Session 36) settled the strategy debate with numbers: the ONLY measured component with positive expectancy is the COMPLETED pair — 7/7 merges at +16.3c/share with zero variance (+$49.46 on 302.5 shares), immune to direction because a full YES+NO pair redeems for $1.00 no matter which way the market moves. Everything else is negative: a naked leg drifts −18.5c/share by the 1h markout (−$51.26 on 8 older fills), the reward rent is ~$0 (0.01% share of the window), and holding naked to resolution is a coin flip (−3.1% CI, indistinguishable from breakeven). Yet the live strategy still HOLDS the naked leg after a one-sided fill, converting a maker into a directional gambler. How do we implement the measured fix — complete the pair within the drift-free window, or exit the naked leg — and make the EV formula itself the dashboard KPI so the next sample measures whether it is positive?

**Method.** A staged behavioural rule, switchable like every other change here. (1) Config block (default ON, env `HUNTER_PAIRS_RULE=0/false/off` disables): `enable_pairs_rule`, a 15-minute `pairs_exit_window_sec` (where the measured drift is still ~0, +0.09c/share at 5m, long before the −18.5c 1h mark), and the EV constants `pairs_complete_gain_cents` 16.3 / `pairs_exit_cost_cents` 3.0 measured from the same sample. (2) `sweep._apply_pairs_rule` between `_process_fills` and `_advance_gate`: on a one-sided fill inside the window, COMPLETE — cross the missing leg at ask via the existing `engine.cross` taker primitive, sized by `_pair_completion_size` (pair cost = fill avg + ask, capped by `max_pair_cost` 0.995 and the wallet's committed room) so a partial completion is a real outcome and the residue re-runs next sweep — or EXIT the naked leg at the best bid (pay the ~3c half-spread instead of the drift). The expiry path records a `PAIR_WINDOW_EXPIRED` event ONCE per fill (handled stamp is per-fill, so a later fill re-opens the window). (3) The census seam that was built but never switched on — `record_hedge_census` now runs EVERY sweep (up_ask + down_ask − 0.02, fillable-under-cap), directly measuring P(completes under $1) on the next sample, which is the one number that decides the rule's EV. (4) Accounting: `Inventory.last_fill_ts` on fills; closes now carry a SIDE (naked-exit closes one leg), so `realized()`/`settled_positions()`/`inventory_from_db` rebuild per-side instead of assuming every close removes one of each leg; `stats.pairs_ev()` reads the decisions table and computes completion_rate × gain − exit_rate × cost; the dashboard totals gain a PAIRS-ONLY RULE tile (completions/exits/expired/one-sided counts, EV in ¢/fill, the formula in the tooltip) and the event pills learn the new action kinds (PAIR_COMPLETED / PAIR_EXITED / PAIR_WINDOW_EXPIRED). 8 new tests: sweep complete (cross books the missing leg, pair under cap, inv balanced), exit (sells the heavy leg at best bid), expiry (once per fill, re-opens on a later fill), disabled flag, census recorded every sweep, and stats (naked-exit rehydration per side, side-aware realized, pairs_ev rates from decisions).

**Result.** Full suite 593/593 (585 + 8). The rule runs live in the next supervised sweep — one caveat applies to the whole U34/U35 afternoon: the production fleet picks up new code on its next cycle (spawns fresh processes), so nothing here needs a deploy to go live. One instrumented bug caught on the way: the dashboard EV-tile tooltip string opened a single-quoted JS literal and closed it with a double quote, which the page-parse test (node --check) caught as a BLANK-page-class syntax error — fixed and re-verified.

**Verdict.** LIVE — staged and measured, not assumed. The rule converts the one measured-positive component (completed pairs, +16.3c/share, 7/7) into a deterministic response: complete under the cap or exit inside the drift-free window, never hold naked past 15 minutes. The EV tile replaces the coin-flip framing with a single number the next sample decides. Two honest limits stand: fill rate is still the other binding constraint (28 fills/112h — the rule fixes edge per fill, the spread-lane universe work fixes fill rate), and the rule's completion rate starts at whatever the live books offer, which is what `hedge_census` now measures every sweep instead of never.

---

### Session 39 — 2026-08-10: universe expansion — the volume-gate trial (U36)

**Question.** The pairs-only rule (U35) fixes edge per fill, but fill rate is the other binding constraint (28 fills/112h), and it is bounded by the ELIGIBLE universe: the live pipeline scores 205 markets and picks 2. The fleet needs more eligible markets to accumulate samples, but every gate loosening must be evidence-first. Which gate actually binds — and does the licensed depth trial, plus a volume trial, add eligible markets?

**Method.** (1) Full-funnel audit of run/pipeline.json: 997 reward + 12 spread candidates → 230 attempted → 205 scored → 203 rejected → 2 picked. Rejection buckets: YES top-3 depth 98 (74 of them empty-book mirages), volume 54 (would_fund 2), YES spread 27, NO depth 16, misc 10. (2) Dry-run ranker at trial depth $750 and $500: eligibility stays 2 — loosening depth merely RELABELS rejects into the volume bucket (volume 54→65→78 as YES-depth falls 98→87→73), because `evaluate` gates depth BEFORE volume (a near-miss green like 'Lisa Demuth' carries $433 YES depth but $10,696 volume — 23x under the bar). (3) The volume-reject population's top readings are $235–242k (real books, just under the bar), while 91% of readings are < $50k. (4) Implemented a VOLUME-GATE TRIAL mirroring the depth trial exactly: `--trial-volume` flag, `_effective_volume_bar` (CLI > config trial HUNTER_VOLUME_TRIAL_USD > permanent), `min_volume_usd` threaded through `tradable`/`evaluate`/`score_pool`/`gamma_spread_universe`, adopted markets tagged `trial_volume_usd`, pipeline.json gains `volume_gate_usd`/`trial_volume_usd`; `rerank_loop._rank_cmd` appends both trial flags from config; fleet-start.ps1 activates both trials (`HUNTER_DEPTH_TRIAL_USD=750`, `HUNTER_VOLUME_TRIAL_USD=200000`). 8 new tests.

**Result.** Full suite 601/601. The depth trial alone admits 0 additional markets today ($750: YES-depth 98→87, eligible still 2; $500: →73, still 2) — the depth near-miss population is 3–25x thin on volume, so those markets fail volume next, exactly as U33 concluded. The volume lever is the only one with measured positive yield: would_fund = 2 (markets with real books at $235–242k volume), and the honest trial bar is $200k — NOT the half-bar $125k the depth convention would suggest (U33 measured those rejects 1–3 orders under the bar; $125k admits nothing). `max_open_markets` (config 3) is dead code — never read — so no fleet cap throttles adoption. The explainer's calculator sliders were also widened to 0.10–0.90 (they were 0.30–0.90 / 0.20–0.80, missing the deep-OTM range the census actually quotes).

**Verdict.** LIVE — U36. Universe expansion is a GATE question, not a fetch question: the ranker already sees 205 live markets; the gate stack crushes them to 2. Depth loosening alone is PARKED (0 additional markets today; the trial stays armed to catch the rotating universe's good days). The volume trial ($250k → $200k) is the active lever (+2 would-fund today, continuously sampled), runs staged and tagged (`trial_volume_usd`), markouts watched before permanence. Revert: unset the two `MAKER_*_TRIAL_USD` vars in fleet-start.ps1, or pass `--trial-depth`/`--trial-volume` per run.

**Follow-up U36b (2026-08-10).** Operator re-armed the trial bars in fleet-start.ps1: `HUNTER_DEPTH_TRIAL_USD` 750→500, `HUNTER_VOLUME_TRIAL_USD` 200000→125000. First supervised cycle under the new bars: pipeline eligible 2→7 (spread-universe additions Navi/3DMAX CS, Jodar/Fils tennis, Red Sox, Orioles, Mets; reward-market picks unchanged). Pipeline confirms `depth_gate_usd: 500` / `volume_gate_usd: 125000`, both tagged trial. Trial markets remain tagged and watched — permanent bars untouched, sample unmixed.

---

### Session 40 — 2026-08-11: the three-cycle composition watch — Todi straddles the depth line (U36c)

**Question.** U36b re-armed the trial bars (depth $500 / volume $125k) and the first supervised cycle jumped eligible 2→7. But count is not composition: are the looser bars changing WHICH markets the fleet picks, or merely how many? An immediate two-cycle compare (22:53 → 23:03) showed the picked set identical at 8 with rejection buckets moving in noise only (+3 across five buckets, would_fund flat at 1) — so the real question is whether the trial admits markets that STICK, or borderline ones that churn.

**Method.** Stateful watch across three consecutive rerank cycles (~31 minutes at the 600 s cycle): snapshot each pipeline write, diff the picked set by market title against the previous cycle, and read the entry fields (volume, spread, source, projected income) of any admitted or dropped market.

**Result.** Composition changed on the FIRST watched cycle — and all the churn is one market straddling the depth line. 23:44:19 baseline: 7 picked → 23:54:28: 8 (+Todi: Maxim Mrva vs Pierluigi Basile, tennis; nothing dropped; rejection buckets moved in noise only — YES-depth 69→65, NO-depth +2, volume +1) → 00:04:37: 7 (−Todi) → 00:14:46: 8 (+Todi, back in). The other 7 picks were rock-stable across all three cycles — zero churn. Todi's entry is the diagnosis: volume $468k (3.7× over the $125k trial bar), spread 0.01 (the tightest possible), source "spread" pool — its constraint is NOT volume or spread but the top-3 bid depth hovering around the $500 trial line on a live match. When the book firms it clears; when it thins between games it drops. It is a real market, not a mirage (1¢ spread, near-half-million volume, projected income 10.1%/day on $120 capital) — the trial admitted exactly the borderline population it exists to watch.

**Verdict.** LIVE — the trial is doing its job. The looser bars changed composition, not just count: a genuine near-threshold market now enters and leaves the fleet's universe with its book, and being tagged trial, its markouts are sampled before either bar goes permanent. The 7 core picks' zero-churn stability across all three cycles is itself a signal that the relaxed bars admitted no junk into the steady set. The honest limit: n=1 market oscillating is a stability read, not a verdict on the bars — the admission needs a fresh event slate (new MLB/tennis rotation) to show it generalizes.

---

### Session 41 — 2026-08-11: "it's been hours and nothing happens" — the refusal chain, measured (U36f)

**Question.** Operator: "It's been hours and nothing happens... what the bot is waiting for, what the strategy is even looking for. It just does nothing." Legitimate instinct — the fleet log showed `0/8 scoring | offers $0 | est $0.00/day` for over an hour straight. Is the bot waiting for a fill (normal maker patience), or REFUSING the entire universe (a defect)? And the rebates finding (U35) already established rent is not the edge — so what IS the strategy supposed to be doing with its resting orders?

**Method.** Ground-truth audit, no guessing: (1) process check — supervisor 30540 up with all 4 children (fleet, dash :8800, rerank, watch); (2) run/fleet_pulse.json + fleet.log — the `scoring` count is `len([s for s in states if s.spec["_live"]["ours"] > 0])`, i.e. markets with OUR orders resting: 0/8 means zero orders anywhere; (3) sqlite market_events — the smoking gun: 5,304 BLOCKED vs 4 FILLED in 24h, 82% "unfunded by the allocator", 17% "outside band 0.30-0.70" (incl. a FUNDED market), 1% depth ERROR; zero events after 22:20; (4) code walk of the gate chain (selector.pair_books_allowed -> sweep book gate -> allocator) and the config values that bind at runtime.

**Result.** The bot is not waiting — it is REFUSING everything, through three stacked defects: (1) **Trial-depth wiring gap (bug).** The fleet's LIVE book gate reads `cfg.select_min_top3_depth_usd` — the PERMANENT $1,000 — while the ranker admits at the trial $500. U36's trial only lived in the ranker; the fleet re-blocked every trial admission at the old bar. Fixed: the fleet gate now honors the spec's `trial_depth_usd` tag. (2) **Price band 0.30–0.70 (legacy defect).** The band is a legacy BTC-era rule whose own docstring says its purpose is refusing near-settled outcomes (0.95+); it blocked funded markets sitting at 0.25/0.75 (a funded Shnaider market was refused "outside band"). Widened to 0.10–0.90 — protection kept, funded universe admitted. (3) **Allocator floor 2%/day (the gate that actually binds).** At boot with the fixes live, the fleet quoted ALL 7 markets ("resting DOWN+UP limit orders" 22:34–22:35 UTC), then the allocator's first reallocate DEFUNDED all 7 because their marginal returns (0.04–1.84%/day) sit below the 2%/day floor. The dashboard was also blind: graduated cards showed the alloc verdict but not the live refusal reason — now every card surfaces the `err` string ("unfunded: below 2.00%/day floor"). 3 new tests (fleet gate honors trial depth from spec; widened band; dashboard refusal render) + the replay fixture's 0.90 fill moved to 0.95 (0.90 is now inside the band); full suite 603/603.

**Verdict.** LIVE — the diagnosis is complete and the refusal chain is now visible on every dashboard card. Two of the three were plain bugs (trial-depth wiring, band range) and are fixed. The third — the allocator's 2%/day marginal-return floor — is a POLICY gate, not a bug: it defunded all 7 live markets at 0.04–1.84%/day. That is the real decision: the strategy's edge per the U35 economics is the completed-pair +16.3¢, and a market with a real book at 1.0%/day marginal is a legitimate candidate for a LOWER trial floor — the same staged, tagged, watched discipline as the depth/volume trials — so the floor question goes to the operator with the evidence in hand rather than being quietly loosened. What the bot is "waiting for" is now answered with numbers: fills were never the bottleneck this run — every gate above the allocator refused the universe first.

---

### Session 42 — 2026-08-11: "I don't care about the reason, I want it to work" — the floor decision (U36g)

**Question.** Session 41 left the allocator's 2%/day marginal-return floor as a POLICY question for the operator. The operator's answer was unambiguous: "I don't care about the reason. I want it to work. I don't see it working or researching a different strategy." Same observation that started Session 41: the fleet sweeps every ~10s but quotes nothing — every market card reads `offers $0`, the sweep log reads `funded 0/7`. How far must the floor fall before the WHOLE eligible universe — not a subset — is funded and quoting? And is the rest of the gate stack clean enough that the floor is genuinely the last binding gate?

**Method.** (1) Verify the Session 41 fixes are live: restart with the fleet-start.ps1 env, confirm the sweep log moves (`funded 1/7 | offers $128` — the trial-depth and band fixes ARE live; one pre-existing position still quotes). (2) Measure the ACTUAL first-dollar marginal of every eligible market with the allocator's own math (`marginal(0, pot, T)`, `T = avg_theirs / k`, `k = score_per_share`): Shnaider 3.997%, Jodar 0.825%, Falcons 0.771% — and the four refused at 0.5%/day: Navi 0.306%, Red Sox 0.125%, Orioles 0.085%, Mets 0.046%. (3) The four refused are NOT junk — mirage triage already removed the empty-book population; they have real competition depth (56k–880k), tight spreads and live volume, they are just deep-competition books where the first dollar earns little. (4) Implement `HUNTER_MARGINAL_FLOOR` (env override for `marginal_return_floor` in `config.load()`, mirroring the trial env pattern) and arm it in fleet-start.ps1 at 0.0001 (0.01%/day) so the whole eligible universe clears; `max_market_frac` 0.15 still caps per-market concentration, and the pairs-only rule (U35, +16.3¢/completed pair) is the measured edge these samples exist to accumulate. 1 new test (env override, permanent default untouched).

**Result.** Full suite 604/604. Restart: sweep log went `funded 3/7 | offers $335` at 0.5%/day, then `funded 7/7 | offers $674 | committed $674/1000` at 0.01%/day — EVERY market in the eligible universe is now funded and resting two-sided quotes, `verified 100.0% (30v/0u)`. The dashboard Fleet view reads `7/7 scoring · LIVE · $680/$1000 committed`, all seven market rows carry green income bars (Shnaider $1.76 → Orioles $0.01), no refusal reasons anywhere. The gate stack is now fully open for the 7-market universe: trial depth $500 + trial volume $125k admit the universe, band 0.10–0.90 admits the prices, and the floor admits the books. What was "nothing happens" for a week is now $674 of resting two-sided liquidity on 7 live markets, sampled every ~9s.

**Verdict.** LIVE — U36g. The floor is operator-settled at 0.01%/day for the trial universe (permanent default stays 2%/day for anyone not opting in via fleet-start.ps1). Honest accounting: the floor being wide open is NOT the strategy claiming these 7 markets earn 0.3%/day — it is the operator choosing SAMPLES over selectivity, because the measured edge (completed pairs) needs fills, fills need resting quotes, and resting quotes need funding. The markout gate, the pairs-only rule and the near-miss trackers still watch what these quotes actually earn; the 2%/day floor was a 20-market-BTC-era number that never applied to a 7-market spread universe. What changed from "it does nothing": the fleet now has skin in the game on every eligible market, and the next fills/markouts are the evidence that decides whether 0.01%/day was generous or right. The uncommitted tree now holds the Session 41 + 42 fix set (trial-depth wiring, band, dashboard refusal text, floor env) for one coherent commit when the operator says so.
---

### 2026-08-12 (design, minimal entry): isolated Spread Hunter dashboard

Visual reskin only — new `server/spread_dash.py`/`spread_dash_html.py` (port 8801), zero edits to `fleet_dash.py`/`dashboard.py`/`strategy/`, all numbers pulled from existing `strategy.stats`/`fleet_dash.fleet()` functions. No strategy, gate, or risk change. Per operator: design-only commits get this one-line form, not the full Question/Method/Result/Verdict entry (reserved for strategy/reasoning/functionality changes).

### 2026-08-12 (design): dashboard unification and global taxonomy

**Question.** Can the Spread Hunter dashboard present every widget in one unified brutalist layout with a global category taxonomy, without touching strategy, gates, or risk?

**Method.** Design-only pass over `server/spread_dash.py`/`spread_dash_html.py`: consolidated component spacing, flattened accordion toggle headers, mapped specific sports/games to global categories ("Sports", "E-Sports", etc.), formally deprecated the old `fleet_dash.py`, and fixed a minor IDE syntax typo in `fleet.py`.

**Result.** Unified brutalist UI is now the canonical dashboard with the global taxonomy applied across widgets; `fleet_dash.py` is marked deprecated; `fleet.py` typo fixed. Zero strategy, gate, or risk change — all numbers come from existing `strategy.stats`/`fleet_dash.fleet()` functions.

**Verdict.** LIVE — design unification shipped as the canonical dashboard; rendering only, no behavior change.

### 2026-08-12 (design): dashboard performance pass — cache, progressive boot, honest freshness

**Question.** The canonical dashboard took 9–12s to paint anything on every load: `boot()` awaited four endpoints in parallel, and the two heaviest (`/api/summary`, `/api/markets`) re-ran the full `stats.snapshot()` DB read per request (1.9s standalone, 9–12s under the live writer's lock traffic). Every load showed empty section bars; the landing page sat at "…" indefinitely; a failed endpoint left sections silently blank with the header stuck on "Loading"; and "Data as of: Now" was untruthful.

**Method.** (1) 8s TTL cache in `spread_dash.py` around the two expensive payloads (`fleet()` → `stats.snapshot()`, `pipeline()`), per-process with a background warm-up thread — staleness is invisible because the fleet's own pulse is written every ~10s. (2) Progressive boot in the dashboard JS: settled/funnel/markets paint instantly from their own fetches, the summary streams in last; every section degrades to a visible red error box with the HTTP status/message instead of a silent blank. (3) Landing-page resilience: try/catch → "Offline" nav state + error message, no infinite "…". (4) Honest freshness: the scope tile shows the server's real `Data as of HH:MM:SS`. (5) A11y: `aria-expanded` on all 5 toggles, `tablist/tab/tabpanel` + `aria-selected` on the inspection tabs, `dialog/aria-modal` on the distribution modal.

**Result.** Warm loads 9–12s → ~0.05s; the cold first load after restart is ~5s (one compute, primed by the warm-up thread). Verified live on :8805: all panels render with real freshness, tabs flip aria state and paginate, landing hero + verdict rows populate. 617/617 tests pass; embedded JS parses clean under node --check.

**Verdict.** LIVE — the design pass's first slice shipped: the cache makes the dashboard instant, progressive boot + error states remove the silent-blank failure mode, and the a11y/freshness fixes make the page honest. Remaining audit items (not in this slice): 3–5× figure redundancy per screen, Tailwind CDN/Google Fonts (prod warning, offline dependency), auto-refresh polling (now cheap with the cache), mobile polish for the 7-column markets table, modal focus trap.

### 2026-08-12 (design): desk identity — display type, role tags, and a live decision hinge

**Question.** The canonical dashboard already had a coherent brutalist identity, but it still read as a template: Inter + JetBrains Mono (the stock AI pairing), Tailwind gray defaults, and numbered section chips (01–05) that decorated rather than encoded — the five panels are a monitoring taxonomy, not a sequence. Can the desk's identity become deliberate — type, structure, and one signature element — without touching strategy, gates, or risk?

**Method.** Design pass over `server/spread_dash_html.py` only. (1) **Type** — swapped the default pairing for a deliberate one: Big Shoulders Display, a condensed industrial grotesque (the visual language of scoreboards/odds boards), carries the wordmark, hero, and the decision verdict; IBM Plex Mono carries every number and label; the body is now mono (an ops console, honestly). The operator-approved palette is codified as CSS tokens — colors untouched. (2) **Structure** — the dashboard's 01–05 chips became role tags: BOOK (Positions), CALL (Verdict), GATES (Readiness), PROOF (Evidence), DETAIL (Inspection); numbers kept where they are true lists (the five readings inside Verdict, the landing's four layers, which now carry the same tags). (3) **Signature** — the decision hinge is now a live ticker: THE CALL renders the real go-live status from stats.py (GO / SIGNAL / COLLECT / NO DATA) in display type with the deciding 90% lower bound, the sample behind it, and a beating fleet-pulse that ticks seconds and goes amber when stale or idle. (4) **Motion** — one boot choreography (panels rise in sequence) plus the pulse beat, both standing down under `prefers-reduced-motion`; `:focus-visible` rings added.

**Result.** Verified live on :8805 — Big Shoulders Display + IBM Plex Mono load and apply, the hinge reads "The call SIGNAL · 90% lower bound +1.72% · 51 / 100 settled" with the pulse ticking second-by-second, all five role tags render, scope tiles show the real data timestamp, landing nav reads "Live · 51 of 100 settled", no horizontal overflow at 570px. 617/617 tests pass. One runtime bug caught live during the pass and fixed: an edit dropped the `fmtClock` definition, blanking the scope tiles (a ReferenceError no parse test can catch — the live check did).

**Verdict.** LIVE — design pass slice 2. The palette is untouched (operator-approved); the identity now comes from deliberate type, truthful structure, and one signature line instead of template defaults. Out of scope (audit): Tailwind CDN / Google Fonts as offline dependencies, auto-refresh polling, mobile markets-table polish, modal focus trap.

### 2026-08-12 (ops): auto-refresh — the desk polls itself every 15s

**Question.** The dashboard read the fleet live but only once per page load; the operator had to reload to see a fill, a markout, or the call flip. With the 8s server-side cache (U38-slice-1) making each read ~0.05s, a poll loop is nearly free — but a naive re-render would throw away the operator's context: which inspection tab is open, which settled groups are expanded, and which page they're on.

**Method.** (1) Extracted the summary render (pills, live pill, hinge, positions, verdict, gauges, evidence, scope tiles) into one `renderSummary(s)` shared by boot and the poll loop, so refresh and first paint can't drift apart. (2) `refresh()` fetches all four endpoints in parallel every 15s via `setInterval`, guarded by a `REFRESH_BUSY` flag so slow polls never overlap. (3) State preservation fell out of the existing structure: the tab panels keep their classes across innerHTML swaps (active tab survives), and `renderSettled` reuses `settledState` (expanded groups + pagination survive). Added one clamp: the settled page index is capped to the last page since the grouped list can shrink between polls. (4) A failed poll keeps the last good data on screen — staleness is reported by the pulse age going amber past 45s, not by wiping panels. (5) Panels dim briefly during a poll (`main.sh-refreshing`) instead of flickering; disabled under prefers-reduced-motion.

**Result.** Verified live on :8805 across two full poll cycles: the pulse reset to 0s on each poll, and at the moment of a fresh landing the settled tab was still active, the expanded group's exit rows were still open, page 1 of 6 was preserved, and the hinge re-rendered — no flicker, no lost context, no console errors. 618/618 tests (1 new: interval + renderSummary + busy-guard + page-clamp pins).

**Verdict.** LIVE — the desk now runs itself: fresh every 15s with zero reload, and the operator's place (tab, open groups, page) is never disturbed. The pulse dot is the honest freshness meter — green and beating when data is current, amber when a poll chain has gone stale.

### 2026-08-12 (design): de-redundancy — one home per figure

**Question.** The audit had flagged 3–5× repetition of the same figures per screen. The operator's instruction: cut the repeated figures (lower bound, n settled, status) so each number appears once per screen, keeping the traceability notes. The same "51" and "+1.72%" were being shown in up to nine places.

**Method.** Assigned each figure one analytical home — the place with the richest traceability — and removed every other display: **lower bound** → the Verdict panel's "90% Lower Bound" tile (value + fitted-distribution chart + note "Below zero means the sample cannot yet rule out no edge."); **n settled** → the Verdict panel's "Sample" tile (progress bar + note "Signal floor is 30 settled markets."); **status** → the header pill. Cut: the hinge's deciding-figure span and its sample/status line (the hinge is now just THE CALL + the pulse), the header's settled-count pill, the Verdict left-panel badges, the positions "Settled" and "Status" cards, the Evidence "Lower +X%" badge (replaced by a bare "Expand ↗" affordance) and its "Settled 51/100" tile, the scope tiles' Sample/Lower-bound tiles (scope keeps only "Data as of" freshness), the n= in the lower-bound tile's sub, and the Readiness panel's Settlement-Progress gauge (settlement progress now lives entirely in the Verdict Sample tile; the Gates caption and the landing's "02 · GATES" layer copy were updated to "Two threshold gauges" to stay truthful). The expanded distribution modal keeps its stat readout — it is its own screen, reached by clicking the chart. The landing page is a separate screen and was left untouched.

**Result.** Verified live on :8805 by tree-walking the rendered DOM (excluding script source, which contains the templates): the lower bound, the "51 / 100" sample, and the status string each appear exactly once on the dashboard screen. Hinge reads "The call SIGNAL · Pulse 0s"; pills "DIRECTIONAL_SIGNAL"; scope "Data as of 15:30:37". Auto-refresh still preserves the operator's place across polls (settled tab active, expanded groups open, hinge re-rendering). 618/618 tests pass.

**Verdict.** LIVE — the screen now says each number once. The deduped figure keeps the note that makes it traceable, and the sticky hinge — the one element visible without scrolling — is now uncluttered: the call and the freshness, nothing else.

### 2026-08-12 (design): phase 1 — data-change cues, number roll-up, heartbeat, and the market drawer

**Question.** The operator's overhaul prompt 1 asked for four real-time micro-interactions: flash financial figures on change (green/red, 300ms), smooth 400ms number roll-up for the hero KPIs, a pulsing "data health" badge, and a right-edge slide-over drawer with per-market order-book metrics, execution logs, and markout drift. The prompt's reference implementation was React/Framer Motion; the Fleet desk is a server-rendered vanilla-JS/Tailwind page. Major conflict flagged and resolved: all four interactions are CSS/JS primitives (Framer Motion compiles down to exactly these transitions), so a full React rewrite — build tooling, new runtime, breaking the parse tests — would buy zero function. Implemented natively instead; no backend, telemetry, or risk code touched.

**Method.** (1) **Flash/status cues** — a generic animateChanges() pass walks cells marked data-kpi/data-v (15 instrumented cells: hero P&L, win rate, every market's unrealized/realized P&L) and data-state/data-v (every market's status cell); on a poll that changes a value the cell flashes green rgba(34,197,94,.2) / red rgba(239,68,68,.2) over 300ms, and status cells fade+scale in (opacity 0→1, scale .98→1, 180ms — the exact Framer Motion spec). Previous-value maps seed on first paint, so only real changes ever flash. (2) **Roll-up** — requestAnimationFrame interpolator with ease-out-cubic, 400ms, fixed 2dp in tabular mono, on realized, unrealized, and win rate (the schema has no "Hold Rate"; win rate is the closest real KPI — flagged). (3) **Heartbeat** — hdr-live is now a "Data health" badge with a continuously pulsing dot (exact requested keyframes: opacity 1→.4, scale 1→.95), amber when idle, red Offline on load failure. (4) **Drawer** — market rows are clickable and slide a 520px panel in from the right (translate-x-full→0, 300ms ease-out) with the live book/mid strip, commit/resting/fills/age, unrealized/realized, per-market markout mean, and the real execution log (settled exits grouped by market); closes via ×, backdrop, or Esc, re-renders on each 15s poll, and respects prefers-reduced-motion throughout (all cues stand down).

**Result.** 619/619 tests (1 new: pins for animateChanges/animateNumber/data-kpi/data-state/data-drawer/health-pulse). Verified live on :8805 by driving the real code path: a changed value applied flash-up with computed animation kpi-flash-up; a flipped status cell ran status-in; the roll-up sampled mid-flight at +$137.05 and landed exactly +$150.00; Esc closed the drawer; a market with 3 settled exits rendered "Execution log · 3 exits" with per-exit method/price/P&L rows; console clean. Natural data was quiet across the observed polls (realized only moves on closes), so the flash was proven via the real animateChanges pass rather than waiting on live fills.

**Verdict.** LIVE — phase 1 shipped. The desk now reacts to its own data: figures flash on change, numbers roll, the health dot pulses, and any market's book and execution history open in a slide-over without a single new endpoint. Schema notes for the operator: no "Hold Rate" or per-market markout *history* series exists (the drawer shows the per-market mean and points to the pooled distribution) — both are additions to strategy/stats if wanted, out of UI scope.

### 2026-08-12 (design): phase 2 — frosted terminal aesthetic

**Question.** The overhaul prompt 2 asked for a high-contrast institutional terminal look: a dark mesh canvas (#080C14 → #0B111E), glassmorphism cards (blur 12px, rgba(15,23,42,.65), faint white borders), amber/gold reserved exclusively for high-priority signals (MID badge, naked-USD warnings, go-live), a strict monospace for all figures with tabular numerals, lifted secondary-label contrast (#94A3B8), and hero KPI drop shadows. Reference implementation again framed in React; the desk is a server-rendered page, so the upgrade lands as CSS module rules + targeted markup — no renderer churn.

**Method.** (1) **Mesh canvas** — body background is a fixed linear gradient #080C14 → #0B111E. (2) **Glass** — post-Tailwind overrides on the three workhorse utilities (bg-[#111827], bg-[#090D16], border-[#1F2937], bg-[#1F2937]) apply rgba(15,23,42,.65) + backdrop-filter blur(12px) + rgba(255,255,255,.07) hairlines to both static shells and every JS-rendered card in one place. Bug caught live: the first cut used the background *shorthand* with !important, which reset background-image to none and killed the canvas gradient on the body (body carries the bg-[#090D16] class) — switched to background-color-only. (3) **Gold reserve** — the order-book MID badge and the hinge's GO call are now bright gold #FBBF24; amber #F59E0B stays on genuine warnings (stale pulse, idle fleet, side-filled legs, weighting gap) rather than being demoted to gray — the "exclusively" rule applied to decorative amber, preserving warning semantics (flagged in the summary). Naked-USD capacity has no tile on this dashboard (it's on the legacy fleet page); the side-filled status that flags a naked leg was already amber. (4) **Mono** — data face switches from IBM Plex Mono to Geist Mono, one of the three named faces (JetBrains was the pre-identity default we deliberately moved off; Fira Code ships ligatures that corrupt financial digits); font-variant-numeric + font-feature-settings tnum enforced on the .mono class, and the two remaining display-face numbers (gauge subs) moved to mono. (5) **Contrast & shadow** — a text-[#9CA3AF] override lifts all secondary labels to #94A3B8; the BOOK panel (hero KPIs) gets drop-shadow(0 4px 12px rgba(0,0,0,.4)).

**Result.** 620/620 tests (1 new pinning the gradient, glass values, #94A3B8, Geist Mono, and the gold MID/go-live). Verified live on :8805 by computed style: body background-image is the #080C14→#0B111E gradient; panels compute rgba(15,23,42,.65) + blur(12px) + rgba(255,255,255,.07) borders; secondary labels compute rgb(148,163,184); the MID badge computes #FBBF24; .mono and body compute Geist Mono; the BOOK section computes the drop-shadow filter; drawer opens on the same glass; no console errors, no overflow. One real bug caught and fixed by the live check (the gradient-wipe).

**Verdict.** LIVE — phase 2 shipped. The desk reads as a frosted institutional terminal: the mesh shows through every glass panel, gold is reserved for the two decision signals (mid, go-live), and every number sits in tabular Geist Mono. Note for the operator: the green-for-Merged / red-for-loss color grammar was already in place and is untouched.

### 2026-08-12 (design): phase 3 — risk capacity, order-book depth, metric tooltips

**Question.** The overhaul prompt 3 asked for three high-impact widgets: (1) the Naked-USD display upgraded to an interactive capacity bar ($used / $cap with a thin bar colored by utilization — soft green <50%, amber 50–80%, pulsing red at 80%+ with a HIGH EXPOSURE badge); (2) micro-depth in the order-book strip — soft green/red-brown background fills proportional to bid/ask volume behind the price levels, with the gold mid marker dead center; (3) info-icon tooltips on the statistical headers (90% LOWER BOUND, MARKOUT DRIFT, n_eff, WEIGHTING GAP) carrying formula, current value, and gate meaning. Reference implementation framed in React; the desk is server-rendered, so the widgets land as native CSS/JS.

**Method.** (1) **Server** — api_summary now exposes `naked_usd` (Σ per-market `naked_cost`, the USD in the unhedged leg valued at average cost) and `max_naked_usd` (the $120 hard cap from strategy/config.py — the binding per-market dollar budget the quoting layer enforces). No risk/strategy code touched. (2) **Capacity bar** — a third, full-width GATES card (lg:col-span-2): `$used / $120 hard cap` with data-kpi flash, utilization badge, a thin bar colored by the three bands, and a `warn-bar` pulse keyframe + HIGH EXPOSURE badge at >=80%. All animations disabled under prefers-reduced-motion. (3) **Micro depth** — the order-book strip's standalone two-segment split bar was replaced by soft washes rendered *behind* the price levels inside the band (green rgba(16,185,129,.13) on the YES half, red-brown rgba(239,68,68,.12) on the NO half, each 50% × that side's share of resting notional). Truthfulness note: the venue book exposes prices only — the only real size data is this fleet's own resting notional, so the depth is scaled by our quotes, not fabricated venue volume. The gold mid marker gains a dark outline + glow (box-shadow 0 0 0 1px #090D16) and the MID badge a dark backing; the $ resting figures stay once per market in a slim legend below (full quote detail lives in the drawer). (4) **Tooltips** — a `tip(key, body)` helper renders an info icon + a pure-CSS popover card (hover or keyboard focus via a button inside the wrap — no tooltip dependency), carrying the formula, the live value, and the gate threshold for the three verdict tiles (lower bound mean − 1.645·σ/√n_eff; markout drift size-weighted fill mid drift; weighting gap equal-vs-cash weighted returns), the Markout Coverage gauge (Kish's (Σw)²/Σw² vs the markout_min_sample gate), and the new naked-risk card (Σ naked_cost vs the $120 cap + the three utilization bands).

**Result.** 622/622 tests (2 new: the phase-3 widget pins — bands, washes, gold outline, tip system, formulas — and an api_summary contract check that naked_usd/max_naked_usd are present with max_naked_usd == 120.0). Verified live on :8805: the capacity bar computed green rgb(16,185,129) at 0% utilization, amber rgb(245,158,11) at a simulated 60%, and pulsing red rgb(239,68,68) + warn-bar animation + HIGH EXPOSURE badge at a simulated 83.3% (then restored to the real state); the depth washes rendered behind the price levels at proportional widths (35.6% green / 14.4% red for a 71/29 resting split); the gold mid marker + MID badge rendered; all five tooltip icons revealed their formula cards on keyboard focus (e.g. "mean − 1.645·σ/√n_eff … Gate: must clear 0% for a GO call"); the drawer re-rendered the new strip; console clean, no horizontal overflow at 570px.

**Verdict.** LIVE — phase 3 shipped. Naked exposure is now a read-at-a-glance capacity instrument (and on this desk it is genuinely 0/120 right now), the order-book strip reads as a depth chart with the gold mid dead center, and every statistical header explains itself in place. Two truthfulness flags for the operator: the depth fills use OUR resting notional (the venue exposes no per-level volume), and the naked bar is fleet-wide Σ against the per-market $120 cap — the strict per-market view lives in the quoting gate, not on this page.

### 2026-08-12 (design): phase 4 — high-density market table: badges, filters, freshness

**Question.** The overhaul prompt 4 asked for a redesigned market table: (1) the status column becomes color-coded action pills — QUOTING soft blue, FILLED neon green, BLOCKED muted amber with the specific gate refusal code below in micro-text, MERGED electric purple — plus 2-3 lifecycle event dots under the pill; (2) a quick-filter bar (category chips + state chips: Actively Quoting / Blocked by Risk / Has Active Inventory) filtering instantly with no reload; (3) a micro age-badge per row ("2s ago", "14s ago", "STALE 2m") with rows past 60s without a telemetry update dimmed to opacity 0.6. Reference implementation framed in React; the desk is server-rendered, so the table upgrade lands as native JS over the existing renderMarkets.

**Method.** (1) **Server** — api_markets now passes the raw classification inputs the table needs instead of a display string: paired, naked_sh, err, why, close_why, merge_why, the stable gate refusal code (strategy/store.py reason_code applied to err/why — NAKED_CAP, ONE_SIDED_BOOK, SPREAD, THIN_BOOK, PRICE_BAND…), the persisted market_events list (up to 3 per market), and a per-row telemetry anchor ts (= now − fleet's age) so the page can show a truthful age badge. No risk/strategy logic touched — reason_code is a read-only pure function already used by the operator analytics. (2) **Classification** — classifyStatus(r) maps the real fleet posture onto the four buckets: err/why → BLOCKED (wins), paired+naked → FILLED, paired → MERGED, naked → FILLED, resting quotes → QUOTING, merge_why → MERGED, close_why → CLOSED (neutral gray, a fifth truthful bucket the brief's four don't cover), else INACTIVE. Pill classes use the brief's exact hexes (blue-950/400/800 #172554/#60A5FA/#1E40AF, emerald #022C22/#34D399/#065F46, amber #451A03/#FBBF24/#92400E, purple #3B0764/#C084FC/#6B21A8). BLOCKED pills get the refusal code in 9px micro-text (RISK_GATE fallback when reason_code returns OTHER). (3) **Lifecycle dots** — stateDots renders the market's real persisted market_events (kind QUOTING/FILLED/HEDGED/MERGED/EXITED/BLOCKED/WAITING/ERROR + reason_code + ts in the title) — genuine telemetry, nothing fabricated client-side. (4) **Filters** — a compact bar above the table: category chips derived from the categories actually present in the rows right now (the taxonomy is slug-derived, so a hardcoded enum would strand new categories — live data immediately proved this: it surfaced a "Bitcoin" category a static list would have missed) + the four state chips; clicking re-renders in place (no reload), a Clear chip appears when a filter is active, and an explicit "No markets match the current filters" empty state; FILTERS survives the 15s poll so the operator's selection isn't reset. (5) **Freshness** — each row's status cell carries data-age = ts; the existing 1s pulse ticker now also runs tickAgeBadges(), updating the badge to "Ns ago" or "STALE Mm" past 60s and dimming the row to opacity 0.6; the status cell keeps data-state/data-v (now the bucket) so the phase-1 status-in transition still fires on bucket changes.

**Result.** 624/624 tests (2 new: api_markets payload contract — raw inputs, code, events, ts, now — and the phase-4 page pins: bucket classes with the brief's exact hexes, reason-code micro-text, stateDots, filter chips, empty-filter state, tickAgeBadges with the 0.6 dim). Verified live on :8805: 8 markets render as 5 Quoting / 3 Blocked pills; refusal codes show under the blocked pills (ONE_SIDED_BOOK, PRICE_BAND, RISK_GATE fallback); lifecycle dots render on 5 rows; age badges tick live (18s→21s ago observed) and a genuinely stale row showed STALE 5m at opacity 0.6; category chips from live data (incl. Bitcoin); "Blocked by Risk" → 3 of 8, "E-Sports" → 2 rows, E-Sports ∩ "Has Active Inventory" → the empty-filter state, Clear restores 8; drawer still opens from a row; status-in animation fires on a bucket flip; console clean.

**Verdict.** LIVE — phase 4 shipped. The table reads as an operator instrument: the four action buckets at a glance, the gate that refused each blocked market in micro-text under the pill, real lifecycle dots, instant filtering, and honest freshness — a market that hasn't ticked in 5 minutes visibly dims instead of pretending to be live. Two notes: FILLED/MERGED pills are reachable in the code but no market is in those states in the current live sample (the buckets exist for when the fleet fills/merges); and the age anchor is the fleet's telemetry ts, so a market with no live record yet shows \"age --\".

### 2026-08-12 (design): split-flap decision hinge + drawer focus trap

**Question.** The desk's only question is the call -- GO / SIGNAL / COLLECT -- but the hinge answered it with static text that changed without ceremony. Design pass: make the call a split-flap instrument (the odds-board / departure-board vernacular this desk's Big Shoulders type already speaks) so a verdict change reads as a mechanical commit -- old letter halves flap away, new halves flip in -- playing only when the word actually changes, plus the code-review finds: the stale \"IBM Plex Mono\" token comment (the data face is Geist Mono) and the drawer's missing focus trap.

**Method.** (1) CSS: .flap cells (one letter each, .62em wide, 1em tall, perspective 240px) with top/bottom halves; old letters live in .flap-o layers, new letters in .flap-n; on a word change the old top half flaps up (rotateX 0 to -90 about its top edge), the old bottom flaps down, and the new bottom flips in from behind (0.16s delay), 300ms total; every flap keyframe is off under prefers-reduced-motion. (2) JS: hingeWordHtml() renders the word as flap cells; the flip triggers only when MOTION_OK && HINGE_WORD !== word -- first paint and same-word polls render plain display type (verified: the 15s poll never re-flips), and under reduced motion the word is plain text with no flap layers at all; an sr-only span keeps the word announced exactly once. (3) Fixed the token comment to name the real data face (Geist Mono) and restored the hinge word's 28/36px size -- the first markup drop rendered it at 16px, caught live. (4) trapDrawerFocus(): Tab and Shift+Tab wrap inside the open drawer (close button <-> Polymarket link) so focus cannot wander behind the modal; inert when the drawer is closed.

**Result.** 625/625 tests (1 new: pins for hingeWordHtml, the flap keyframes/classes, the MOTION_OK guard, and the focus trap). Verified live on :8805: first paint renders SIGNAL as 6 static cells (no flap layers); driving renderHinge through a real status change fired the flip -- 6 .flipping cells, the old SIGNAL letters in 12 flap-o layers, computed animationName flap-top-out, new word COLLECT announced; same-word re-render is fully static; COLLECT to GO flips exactly one letter (G) in gold #FBBF24; the focus trap wraps both directions and Esc still closes; console clean, no horizontal overflow.

**Verdict.** LIVE -- the hinge is now the page's signature: silent while the answer holds, a visible odds-board commit when it changes. The design risk is contained by the change-only trigger, the 300ms budget, and reduced-motion. Notes for the operator: letters only flip where the old and new words overlap (a completely new word appears without a flap), and the flap re-arms naturally on the next poll since every render rebuilds the layers fresh.

### 2026-08-12 (design): capital-since-inception curve replaces the hero's Unrealized tile

**Question.** The operator asked to drop the hero's Unrealized P&L tile -- it never moves above 0, so it reads as a stuck number rather than information -- and instead show a line chart of total capital since inception, the equity-curve convention every trading platform has.

**Method.** The curve is built client-side from the REAL closed positions the page already fetches: settledState.rows (api_settled) carries every exit's ts + pnl, so capitalSeries() sorts them ascending and stacks cumulative realized P&L on the starting bankroll (api_summary bankroll_usd, $1,000). Zero backend changes -- no new endpoint, nothing recomputed server-side. capitalChartSvg() draws it in the desk's language: hairline grid with $ y-labels, a dashed "start $1,000" baseline, the equity line green above start / red below, an aria-labeled svg, and mono dates (explicitly en-US -- the first live probe caught the browser's Hebrew locale leaking into the x-axis, the Intl guideline exactly). The hero's right half is now "Capital Since Inception": current capital (data-kpi flash on change), a green/red "+$X since inception" chip, the close count, and an honest note that open positions stay a separate ledger (this desk's never-summed doctrine). Section header updated to "Realized P&L -- capital since inception". Also fixed the stale JetBrains Mono font references in the expanded-bell SVG labels (the data face is Geist Mono) -- flagged in the web-interface review, fixed while in the chart code.

**Result.** 626/626 tests (1 new: pins for capitalSeries/capitalChartSvg, the settledState.rows source, the empty state, the copy, Geist Mono in SVGs, and the hero_unrealized data-kpi removed). Verified live on :8805: the shown $1,424.94 equals bankroll + the sum of all 163 closes computed independently from the APIs; the curve has one point per close; the delta chip reads +$424.94; the empty state renders when there are no closes; the flash fires on a value change through the real animateChanges path; a poll cycle re-renders the chart in place; console clean, no overflow.

**Verdict.** LIVE -- the hero now shows money as it actually moved: a booked-P&L equity curve, the standard instrument for "is the desk making money". One honesty flag: the curve is realized-only by design (unrealized is never summed with realized on this desk) -- if the operator wants open positions marked to market on the curve too, that is a deliberate doctrine change, not a bug.

### 2026-08-12 (design): capital chart view toggle -- realized-only vs total equity

**Question.** The capital curve answered "is the desk making money" with the realized ledger only. The operator asked for a toggle to also see total equity, like every trading platform's account view.

**Method.** A segmented toggle (Realized | Total) sits above the chart, rendered as two real buttons with aria-pressed inside an aria-label="Capital view" group -- keyboard-focusable, no reload, and the choice survives the 15s poll because CAP_VIEW is module state (the same pattern as the market-table FILTERS). Realized view is unchanged: bankroll + cumulative realized P&L per close. Total view marks open positions to market at TODAY'S float: the whole realized trajectory is shifted by the current unrealized_usd, the endpoint and delta chip follow, and the note is explicit that historical float is not recorded anywhere in the ledger, so the total view is the trajectory marked at current float -- not a fabricated per-point mark.

**Result.** 626/626 tests (the capital pins extended for the toggle: CAP_VIEW, the data-capview template, aria-pressed, the total-note honesty text, and the handler wiring). Verified live on :8805: aria-pressed flips on click, the aria-label switches to "Total equity since inception", the total-view note renders, the view survives a real poll, and with a simulated -$25.40 float the total endpoint computed $1,405.09 = realized 1,430.49 - 25.40 with the delta chip at +$405.09 -- exact. (Live unrealized is currently $0.00, so the two views legitimately coincide right now.) Console clean.

**Verdict.** LIVE -- the hero now shows both readings of the desk's money: the booked ledger and the marked-to-market total, with the view's limits stated in the chart's own note. The one honesty constraint is structural: without historical float snapshots the total view can only shift today's trajectory -- a future telemetry change (persisting open-position marks per poll) would make the total view a true historical series.

### Landing equity curve + shared widget (Aug 12)

**Question.** The landing hero still showed the old Unrealized tile while the dashboard carried the capital-since-inception curve -- two pages, two widgets, drifting copies. The curve had to be one source of truth consumed by both.

**Method.** Extracted the widget out of the dashboard template into a single served /capital.js (one source of truth, node-parse covers both pages), wired a route in spread_dash.py, and swapped the landing hero's Unrealized tile for the same equity curve: current capital, since-inception delta chip, Realized|Total view toggle, 164-close curve, empty state before the first close. Same math and honesty note as the dashboard slice.

**Result.** Verified live on both pages: curve renders on each, toggle flips aria-pressed + aria-label on both, no duplicate inline copies, console clean. 629/629 tests (3 new pins: served-widget parse, landing panel + toggle, dashboard single-source).

**Verdict.** LIVE -- one widget, two pages, same code. No strategy/risk code touched.

### Session 43 — 2026-08-12: the research org code — karpathy/autoresearch applied to our own methods

**Question.** Autoresearch's premise (karpathy/autoresearch, the single-GPU nanochat experiment loop) is that research speed comes from the AGENT INSTRUCTIONS, not the code: "you are programming the program.md files that provide context to the AI agents and set up your autonomous research org." We already have the pieces it builds on — a Question→Method→Result→Verdict log, a keep/discard verdict system, and replay harnesses that are our fixed time budget — but no program.md and no codified loop. Where is research speed leaking, and what does the fix look like?

**Method.** Measured the log, not estimated it: 1,242 lines EN / 976 HE; 42 numbered sessions (Sessions 1–13 = the first campaign, Sessions 14–42 = a second campaign that reused the Session label) + 13 dated design entries; span 2026-07-21 → 2026-08-12. Verdict distribution in the EN log: ~40 LIVE (29 plain + ~10 "LIVE — <reason>" variants), 5 DEAD (4 × "DEAD (fixed)" + 1 outright), 1 PARKED, ~10 OPEN. Parallel ID schemes in coexistence: Session ×2, U1–U36g, KTD1–5, C1–C3, issue #11–#15, bare dates for design. Then mapped autoresearch's three design choices onto the repo: single-metric experiments, a fixed time budget, a keep/discard loop.

**Result.** The content is healthy; the method has one structural gap and two decays. (1) **No program.md** — no file that says how a bounded experiment is run, so every session re-invents its card: some name the deciding metric, most don't, and the keep/discard rule is usually implicit in the verdict rather than stated before the run. (2) **The fixed-budget machinery exists but is not codified** — replay_risk_gates, trial_depth_gate, mirage_triage, record_books/measure_fill_rate are our "5-minute training run" (minutes per replay), yet nothing anywhere says replay-first before a live sample. (3) **The ID scheme decayed** — two Session sequences, a parallel U-series, KTD/C/issue tags, then bare dates. The census also confirms what works: verdicts are decisions (every strategy session ends in one), negative results are kept, and the operator-approved minimal form for design entries is honored (13/13 dated entries are one-line or abbreviated). DEAD entries are rare (5) because most failures were caught and fixed inside the session that found them — 4/5 are "DEAD (fixed)".

**Fix shipped.** `PROGRAM.md` at the repo root — the research org code: the experiment card (Question → Single metric → Keep/discard → Budget → Verdict), the replay-first rule, scope forms (full card vs design-minimal), and one canonical ID scheme going forward (next free `Session N`; U/KTD/C/issue stay as tracking tags; no third sequence). AGENTS.md's layout section now names it. Nothing in strategy/ or server/ was touched — the design pass in flight on this branch is untouched, and no code changed, so no sample is invalidated.

**Verdict.** LIVE — the research org now has code. The census's own verdicts: the log's content discipline is real (verdicts are decisions, negatives are kept, numbers are measured); the decay was in the container, not the content. Next campaign — the pairs-only EV question, or the next gate trial — runs under the new card: single metric named in advance, replay first, keep/discard by the number.

### Session 44 — 2026-08-12: the pairs-only EV, measured — rule KEPT, KPI corrected (first experiment under PROGRAM.md)

**Question.** Is the pairs-only rule (U35) positive-EV on the current sample per its pre-registered KPI, and do the KPI's two payoff constants hold on the rule's own recorded decisions?

**Experiment card.** Single metric (named in advance): EV per one-sided fill = completion_rate × 16.3¢ − exit_rate × 3.0¢. Keep/discard rule: KEEP the rule if EV > 0 on ≥ 100 one-sided fills; if ≤ 0, the rule is destroying the edge it exists to protect → discard. Budget: replay-first — read-only queries on the live `run/fleet.db` (no writes, no quoting change); the two payoff constants get verified against realized closes as a second, independent check.

**Method.** (1) `stats.pairs_ev()` — the production KPI path. (2) Independent raw reads of `market_events` (PAIR_COMPLETE / NAKED_EXIT / PAIR_WINDOW_EXPIRED) and `closes` (method merge / naked_exit), sliced to the rule era (ts ≥ the first PAIR_COMPLETE, 1786375019 — merges before that are pre-U35). (3) Fill provenance check (crossed vs tape) so the completions are real, not phantom. (4) EV recomputed with realized payoffs.

**Result.** Sample: 148 one-sided fills over ~2 days (08-10 16:36 → 08-12 20:16 UTC), **144 completions (97.3%), 4 exits (2.7%), 0 expiries**. Pre-registered KPI: 0.9730 × 16.3 − 0.0270 × 3.0 = **+15.78¢/share → positive → KEEP by the metric**. The verification then found BOTH constants stale. Rule-era merges booked **+$368.25 on 10,010.7 shares = +3.68¢/share** (144 closes, all positive) against the 16.3¢ constant — which came from a 302.5-share, 7-pair, pre-spread-era sample and overstates the current instrument's capture **4.4×**. The 4 naked exits booked **−$16.93 on 461 shares = −3.67¢/share** (includes the taker fee) against the ~3.0¢ estimate (n=4, thin but measured; figure corrected in Session 45 after a $1 mis-add). Corrected EV: completion_rate × 3.68 − exit_rate × 3.67 = **+3.48¢/share**; in dollars the rule's decisions booked +$368.25 − $16.93 = **+$351.32 over 148 fills = +$2.37 per one-sided fill**. Provenance: 155 crossed + 149 tape fills in the rule era — every completion walked real ask depth and every completed pair became a real merge close, no phantom fills. Counterfactual: riding the naked leg measured −18.5¢/share at the 1h mark (Session 36); completing booked +3.68¢/share — the rule's choice beats both riding and an immediate exit (~7.6¢/share) on this sample.

**Fix.** The rule stays ON. Its KPI was overstating EV ~4.5× (15.78¢ vs 3.48¢/share) because the completion constant was measured on 302.5 shares of a different instrument era. `strategy/config.py`: `pairs_complete_gain_cents` 16.3 → **3.68** (n=144, 10,010.7 shares), `pairs_exit_cost_cents` 3.0 → **3.67** (n=4, 461 shares; corrected from 3.89 in Session 45), comments rewritten to cite Session 44. Display constants only — quoting behaviour (`enable_pairs_rule`, window, pair cap) untouched, so no sample is invalidated. The dashboard PAIRS-ONLY tile now reads the honest number through the same `stats.pairs_ev()` code path, no server change. Test pin updated (`test_stats.py` 7.4 → 0.868); full suite **633/633**.

**Verdict.** LIVE — the rule is kept by its pre-registered metric with margin (completion arm 97.3%, zero expiries) and its headline KPI is now honest. Limits: 2 days of mostly MLB/tennis/esports spread markets, exits n=4 — re-read the exit constant as exits accumulate. The measured EV is a floor for the current instrument, and it is positive.

### Session 45 — 2026-08-12: exit cost re-read — no new exits, but Session 44's constant was a mis-add

**Question.** Session 44 left `pairs_exit_cost_cents` at 3.89¢ on n=4. Re-measure on a larger sample; update the constant if the reading moved.

**Experiment card.** Single metric: realized exit cost per share, the mean of the naked_exit closes (¢). Keep/discard: update the constant if the reading moved ≥ 0.5¢ from 3.89 on a larger n; otherwise keep it and note the sample. Budget: replay-first — read-only queries on `run/fleet.db`; no code change until the reading decides.

**Result.** The sample did NOT grow: still 4 NAKED_EXIT closes, none after 08-12 01:01 UTC (the completion arm is 97%+, so exits stay rare). But re-auditing the SAME four closes against the SQL aggregate caught an arithmetic error in Session 44: the exits sum to **−$16.93** (2.04 + 7.60 + 3.65 + 3.65), not −$17.93 → **−3.67¢/share**, not −3.89¢ — the constant was set from a mis-added total. Mean −3.67¢ (median −2.70¢; one outlier at −10.70¢, the mlb-kc-lad exit that sold at 0.36 against a 0.45 average). The corrected EV is unchanged at the reported precision: completion_rate × 3.68 − exit_rate × 3.67 = **+3.48¢/share**; realized +$368.25 − $16.93 = **+$351.32 over 148 fills = +$2.37/fill**.

**Fix.** `pairs_exit_cost_cents` 3.89 → **3.67**; Session 44's figures corrected in the log (−$16.93 / −3.67¢ / +$351.32). Test pin 0.868 → 0.923 (0.5 × 3.68 − 0.25 × 3.67 = 0.9225). Display constant only — no quoting change, no sample invalidation.

**Verdict.** LIVE — corrected to the true measured value. The card's premise (new exits) did not materialize; the win is the arithmetic audit, which is exactly the instrumentation-bug discipline this log exists for. Re-read the constant when exits accumulate past ~10.

### Session 46 — 2026-08-12: completion payoff re-check — constant HOLDS on n=145

**Question.** Session 44 set `pairs_complete_gain_cents` = 3.68¢ on n=144 rule-era merges (+$368.25 on 10,010.7 shares). Does the reading hold as rule-era merges accumulate?

**Experiment card.** Single metric: the dollar-weighted merge capture per share (Σ realized_pnl / Σ shares) over rule-era merges (ts ≥ the first PAIR_COMPLETE). Keep/discard: KEEP the constant if the reading moves < 0.5¢ from 3.68 on a larger n; update if it moves ≥ 0.5¢. Budget: replay-first — read-only queries on `run/fleet.db`; no code change unless the reading decides.

**Result.** The sample grew by exactly ONE merge since Session 44 (144 → 145): +$1.01 on 26.3 shares = **+3.81¢/share**, right at the mean. The dollar-weighted capture is **+3.679¢/share on n=145 / 10,037.1 shares / +$369.26** — unchanged at the reported precision (3.68¢). **Reading did not move → KEEP.** Distribution (per-close share-weighted rates): mean 3.66¢, median 3.83¢, p25 2.96¢, p75 3.96¢, min 0.18¢, max 16.50¢; **145/145 merges positive**. Per-market capture ranges 1.96¢ → 7.93¢ (bulk in the 3–4.5¢ band). This fully debunks the old config comment's "7/7 at +16.3c with zero variance" — capture varies by market and entry, and 16.3¢ was a small-sample artifact. Attribution check: 145 rule-era merges vs 145 PAIR_COMPLETE events at the aggregate; per-market, mlb-bos-tor shows 10 merges vs 9 completions (≥1 natural pair mixed in) and lol-hle1 7 completions vs 6 merges (≥1 completion still pending) — the 1:1 holds at scale, so the constant is unbiased at this sample size.

**EV unchanged.** completion_rate is now 145/149 = 0.9732, exit_rate 4/149 = 0.0268 → 0.9732 × 3.68 − 0.0268 × 3.67 = **+3.48¢/share** (was 3.481). Realized: +$369.26 − $16.93 = **+$352.33 over 149 one-sided fills = +$2.36/fill**.

**Verdict.** LIVE — constant KEPT (did not move), no code change. The completion payoff is now anchored on n=145 closes / 10,037 shares across ~53 markets, and the rule's EV has margin: even at the p25 capture (2.96¢) with the worst measured exit cost, EV stays positive. Next re-check at ~2× the sample (≈n=290) or when a new market class (e.g., the bitcoin up/down market, 2.75¢) enters the mix.

### Session 47 — 2026-08-12: the pairs-rule EV is one command — `scripts/pairs_ev_report.py`

**Question.** Sessions 44-46 measured the pairs-only rule's EV by hand-written SQL each time — three queries, three windows, and the arithmetic slip that made Session 45 necessary. Can that measurement become one read-only command, so the next sample lands with the full picture instead of ad-hoc SQL?

**Method.** New `scripts/pairs_ev_report.py`, modeled on `replay_risk_gates.py`: a read-only connection (`mode=ro` with the `query_only` fallback, `BUSY_TIMEOUT_SEC`), a pure `report(path) -> dict`, `_print(rep)`, and `main()` with a `db` positional (default `run/fleet.db`) and `--json` (UTF-8-explicit, Windows-host safe). Sections: the KPI (mirrors `strategy.stats.pairs_ev` — same formula, in-force config constants), rule decisions by market, naked-exit closes (realized economics; the exit price is the recorded `up_price`/`dn_price` with a `proceeds/shares` fallback for pre-side-aware rows), rule-era merge capture (dollar-weighted + per-close distribution with mean/median/p25/p75/min/max, IQR-fence outlier flags, per-market table), realized EV in dollars per one-sided fill, and the merges-vs-completions attribution table. Three documented limits in the docstring (merge capture is the WHOLE pair's capture; fills are not linked to their closes; natural pairs can slip into the rule-era slice). 6 fixture-backed tests in `tests/test_pairs_ev_report.py`: the KPI pin (0.5×3.68 − 0.25×3.67 → 0.923), exits detail, distribution + outlier flagging (a 16.5¢ close flagged against a 3–5¢ set), rule-era slicing (a pre-rule merge excluded), realized + attribution, empty-DB → verdict NO DATA (not a confident 0), and the `--json` round-trip.

**Result.** Live run on `run/fleet.db` confirms and extends Sessions 44-46: **150 one-sided fills, 146 completions (97.3%), 4 exits, 0 expired, EV +3.484¢/fill → PASS**, realized +$369.85 − $16.93 = **+$352.92 / 150 fills = +$2.35/fill**; distribution mean 3.69¢ / median 3.83¢ / p25 2.96¢ / p75 3.96¢ / min 0.18¢ / max 16.50¢ (146/146 positive); 16 IQR outliers flagged (including the tiny-share extremes — 16.50¢ on 10 sh and 0.18¢ on 6 sh — flagged, not hidden); attribution shows exactly the two known mismatches (mlb-bos-tor 10 merges vs 9 completions, lol-hle1 6 merges vs 7 completions). Full suite **640/640** (6 new). One testing bug caught in the fixture: the schema-seeding event was itself a `PAIR_COMPLETE`, polluting the KPI denominator — re-seeded as `QUOTING` (the same lesson as Sessions 1/4: the measuring code is a suspect too).

**Verdict.** LIVE — Sessions 44-46's measurement is now one command, and the constants' validity is re-checkable in seconds: `python -m scripts.pairs_ev_report` reads the PASS/FAIL line. The Session 46 reading holds at n=146. Next re-reads (exit cost past ~10 exits; completion payoff at ~2× the sample) run the same command.

### Session 48 — 2026-08-12: the pairs EV tile speaks the report's numbers — distribution + outliers in the tooltip

**Question.** The PAIRS-ONLY tile showed only the EV and the decision counts; the distribution that Sessions 44-46 spent three sessions measuring (median/p25-p75, outlier count) lived only in `scripts/pairs_ev_report.py` output. Can the tile show it, reusing the same read surface, without new ad-hoc queries in the page?

**Method.** Extended `strategy/stats.pairs_ev()` — the dashboard's read-side module, where CONTEXT.md says all dashboard SQL lives — to also carry `dist` and `outliers`: per-close capture rates over the SAME rule-era slice (ts >= the first PAIR_COMPLETE) with the SAME IQR fences as `scripts/pairs_ev_report.py`, so the tile and the report cannot drift. None, not empty, before the first rule-era merge (the empty-run rule from the EV itself). The `fleet_dash` tile's sub line now shows the median capture per pair, and its tooltip carries median, p25–p75, n, the all-positive note, and the IQR outlier count, pointing at the report for the full stack. No new SQL in the page — it keeps forwarding `snap["pairs_ev"]` unchanged.

**Result.** Live cross-check: `stats.pairs_ev()` and the report agree exactly on the fleet DB (n=146, mean 3.687¢, median 3.833¢, p25 2.963¢ / p75 3.962¢, min 0.179¢ / max 16.50¢, 16 outliers, fences 1.465..5.460). One new test pins the distribution + outlier fields (rates 3/5/16.5 c/sh → median 5, fences 0..8, 1 outlier) and the None-before-first-merge semantics; the existing EV test now also asserts `dist`/`outliers` stay None while no closes exist. The page-parse test stays green, so the JS edit survives `node --check` (the blank-dashboard regression this suite exists for). Full suite **641/641**.

**Verdict.** LIVE — the tile now speaks the measurement the report made, with the same numbers by construction, and the outlier count makes the capture tail visible at a glance instead of hiding it inside a mean. No quoting or strategy change; no sample invalidation.

### Session 49 — 2026-08-12: exit-timing card — exits fire at age ~0 and BEAT waiting, 4/4 (KEEP)

**Question.** The four naked exits averaged −3.67¢/share (one sold 9¢ under cost). They were adverse-move events by construction (all four event reasons: "pair not fillable under 0.995"). Does exiting at the FIRST non-completable sweep beat waiting out the 15-minute window, or would waiting have let the pair re-fill and complete?

**Experiment card.** Single metric: the exit's realized cost per share vs the wait counterfactual, measured two ways — (a) hold-to-settlement (the terminal outcome of the rule's own expiry path: the leg rides on unresolved), and (b) the window-edge bid (inferred from post-exit fill prices). Keep/discard: keep current behavior if exiting beats waiting on the sample; change it if waiting would have preserved the pair.

**Method.** (1) Exit event reasons from market_events. (2) Fills around each exit (all reasons, 30-min window) to establish exit age. (3) Settlement join: each exit close's condition_id → resolutions.winning_token vs the exited leg's token, computing the hold-to-settlement P&L per share. (4) Post-exit fill prices as the continued-decline check.

**Result.** All four exits fired at **age ≈ 0** — the same second as the tape fill that created the one-sided position (the market had already moved INTO our resting bid: fills at 0.19 / 0.45 / 0.10 / 0.29, pair non-fillable at birth). All four exited legs (UP) **resolved LOST**. Exit vs hold-to-settlement, per share: 120sh @0.19 → exit −1.70¢ vs hold −19.00¢ (**saves 17.3¢**); 71sh @0.45 → exit −10.70¢ vs hold −45.00¢ (**saves 34.3¢**); 135sh @0.10 → exit −2.70¢ vs hold −10.00¢ (**saves 7.3¢**); 135sh @0.29 → exit −2.70¢ vs hold −29.00¢ (**saves 26.3¢**). Totals: exits cost **−$16.93**; holding to settlement would have cost **−$107.40**; the exit **saved $90.47 (19.6¢/share weighted)**. The window-edge counterfactual is consistent: in mlb-bos-tor the fleet kept filling UP after the exit at 0.21 → 0.16 → 0.13 over the next 25 minutes — the bid was still falling at the 15-minute mark, so waiting would have exited lower, not higher.

**Why this is the right structure.** "Non-completable at birth" is the adverse-move signal: the pair cannot clear 0.995 precisely because the market has moved against the leg we just filled. The rule exits on the FIRST sweep (age ~0), which is the cheapest possible realization of "never hold a leg that was born non-completable" — the taker fee plus at most the seconds-scale bid decline. Waiting out the window would only give the decline more time. The exit arm's drag on the rule EV (exit_rate × 3.67¢ = 0.10¢/fill) is an insurance premium that on this sample paid 5× back (0.10¢ cost vs 0.50¢/fill of avoided settlement loss across the 148 fills' 4 exits).

**Verdict.** LIVE — KEEP, no code change. Exiting at the first non-completable sweep beat waiting on 4/4 exits by 7.3–34.3¢/share, and the mechanism (non-fillable pair → adverse move → resolves against us) explains the direction rather than luck. Honest limits: n=4, all UP, all resolved LOST — the protective value is measured on a perfectly-adverse sample; the window-edge bid is inferred, not recorded (no 15-minute markout horizon exists), though the post-exit fill ladder and the $0 settlements make the direction unambiguous. Re-check when exits reach ~10, ideally with a recorded 15m markout.

### 2026-08-12 (design): float-marks telemetry -- the Total view becomes a true historical series

**Question.** The Total equity view could only shift today's realized curve by today's open float, because unrealized_usd / committed_open_usd / naked_usd were derived per request in api_summary and persisted nowhere -- "accurate now, not historical." The marks had to exist so the Total line reflects the float actually open at each point.

**Method.** Fleet-side, one fleet-wide mark per sweep, at the sweep boundary of fleet.py main() beside log_income_sample (the confirmed integration point -- the loop, not the per-market sweep, because the mark is a fleet-wide aggregate). log_float_mark() writes a new float_marks table (ts, unrealized_usd, committed_open_usd, naked_usd); _float_mark_totals() derives the totals EXACTLY as the dashboard derives them from the published _live dicts (unrealized = paired*1 - pair_paid + naked_exit_value - naked_cost; committed = capital + naked_cost + pair_paid; naked = naked_cost), with _naked_exit_value mirroring fleet_dash's per-row math (UP vs dn_bid_as_up, None -> 0). Retention is a config knob (float_mark_retention_days = 90.0) pruned on the write path -- self-maintaining, no separate task. Read side: api_summary exposes float_history (thinned to <=1 pt/min, capped at 1,000; the reader checks the DB exists so a dashboard poll cannot materialise an empty hunter.db). Widget: capitalSeries(rows, bankroll, marks, floatNow) time-merges closes with marks -- every recorded mark is a point at its own ts (bankroll + realized-so-far + the float then), marks before the first close and after the last close are real points, the Realized view ignores marks entirely, and a DB with no marks falls back to shifting by today's float with the old honesty note.

**Result.** 633/633 tests (4 new: store round-trip + thinning + prune + cap; a cold DB reads [] without creating the file; api_summary carries float_history; node-executed widget math asserting the exact merged points 50:1020,100:1030,150:1040,200:1045,250:1025 and the fallback 1017). Helpers smoke-checked live (naked_exit UP/DOWN/none, empty fleet). docs/explanation-fleet-data-flow.md's float-marks section updated from "open decision" to "landed".

**Verdict.** LIVE -- the Total view is now a true historical series where the marks exist, with the fallback honest about what it is. Fleet-side was the deliberate choice: server-side recording would only capture while the dashboard is polled, which is the same dishonesty the old note flagged. This is the design pass's first strategy/ touch -- accepted deliberately, and the marks are additive telemetry only (no behavior change to quoting/risk).

### 2026-08-12 (design): ship-gate hardening -- the last three review findings before PR #23 lands

**Question.** PR #23 carried a coderabbit CHANGES_REQUESTED gate plus one finding from the review pass. Three were still open: duplicate cold-cache loads (Major), the "Data as of" tile labeling response time as data freshness (Minor), and the "Has Active Inventory" filter dropping blocked-but-holding markets (critical pass).

**Method.** (1) Single-flight cache: `_cached` now coordinates per-key in-flight loads with a threading.Event -- exactly one request runs the ~2s fleet read per expiry, the others wait for its result, and a failed load clears the marker so the next request retries instead of deadlocking. The first draft had `ev.wait()` inside the lock -- a genuine deadlock the concurrent test caught immediately (waiter holds the lock the loader needs to publish). (2) Data-as-of honesty: api_summary now carries `fleet_ts` (the cache entry's load timestamp); the scope tile renders `fmtClock(s.fleet_ts || s.now)` so freshness is the data's, not the response's. (3) Inventory filter: "Has Active Inventory" now matches `(paired>0 || naked_sh>0)` directly instead of bucket in {FILLED, MERGED} -- classifyStatus sends an err/why market to BLOCKED before checking inventory, so a blocked market still carrying a position no longer vanishes from the operator's inventory view.

**Result.** 634/634 tests (new: a concurrent single-flight test asserting one loader call per key -- which proved itself by catching the deadlock -- plus the `fleet_ts` contract pin and a regression pin on the HOLD filter's inventory match). No behavior change to strategy; server-only.

**Verdict.** LIVE -- the review gate is closed: all four coderabbit comments addressed (the other two, modal keyboard support and error-body preservation, were fixed in the earlier pass), and the inventory filter no longer hides capital the desk is still carrying.
### Session 50 — 2026-08-12: the 15-minute markout horizon — the exit counterfactual becomes recorded, not inferred

**Question.** Session 49's exit card had to INFER the window-edge bid — "where was the mid 15 minutes after we exited?" — from post-exit fill prices, because no 15-minute markout horizon existed. Add the 900s horizon so the exit-vs-wait counterfactual is a DB read, then re-run the exit card once ~10 exits accumulate (n=4 today).

**Experiment card.** The deliverable is the INSTRUMENT, so the single metric is the instrument's integrity: the 15m reading is recorded at fill+900s with the existing 5m/1h/6h readings untouched, and the gate keeps judging fills on the LONGEST matured horizon. The append-last constraint is the whole design: a horizon maps to its column by POSITION, so inserting 900 between 5m and 1h would relabel every existing mid_h1/mid_h2 reading on the live fleet DB. Because the appended horizon matures EARLIER than the ones before it, two ordering assumptions that held while the tuple was monotonic both break and both get fixed: `_matured`'s "last column = longest" and `close_markout`'s `done = (i == len(horizons)-1)`, which would have sealed the row at the 15m write and silently skipped the 1h and 6h readings forever.

**Method.** (1) config.py: `markout_horizons = (300, 3600, 21600, 900)` — appended, never sorted, with the rationale in the comment. (2) store.py: `mid_h3 REAL` in the schema AND in `_MIGRATIONS` — the ALTER TABLE is what reaches the existing run/fleet.db; a schema-only change would apply only to fresh databases. (3) markout.py: `_matured` iterates the row's OWN horizon columns (so a pre-migration row degrades to the horizons it has) and returns drift LONGEST-FIRST by duration; `per_market_stats`/`fleet_stats` take `matured[0]`; `sample_due` computes `done` as "no other horizon column still unrecorded" instead of "last tuple index". (4) stats.py: `pooled_markout_neff` + `markout_stats` derive their longest-first index order from the config DURATIONS (never hardcoded indices), and resolve the SELECTed columns against the live schema via PRAGMA — the dashboard's read-only connection cannot run the migration, so reading a not-yet-restarted fleet's 3-column DB must degrade to the 3-column read, not error.

**Result.** Six edits, five new tests: three in test_fleet_gate_fallback (6h beats a matured 15m reading; 1h beats a matured 15m reading; a lone 15m reading IS used as the longest matured — each a case where index order and duration order disagree), two in test_markout (the sampling pipeline: 5m → 15m → 1h → 6h with `done` only at the end, which fails without the `done` fix; and the ALTER TABLE migration on an old-schema table with the pre-existing row surviving intact). Verified against the LIVE run/fleet.db read-only: markout_stats + pooled_markout_neff read the still-unmigrated 3-column DB correctly (n=174, matured_n=65, pooled −1.37¢/share); migrating a full copy of the live DB adds mid_h3, preserves all 179 rows (h0/h1/h2 untouched), and `fleet_stats` == `pooled_markout_neff` exactly (n_eff 116.63, −1.37¢) on the 4-column schema. Full suite 646/646. Two of my own bugs caught inside the session: the PRAGMA name/index slip (column name is `r[1]`, not `r[0]`, which silently emptied the column list) and the initial `done` semantics that would have orphaned the 1h/6h writes.

**Verdict.** LIVE — the instrument exists. Every fill now carries a mid at fill+900s, and because exits fire at age ≈ 0 (Session 49), the fill+900s reading IS the exit+15m reading for the current exit population — the counterfactual Session 49 inferred is now recorded. Honest boundary, stated for the re-run: a future exit at nonzero age inside the window reads fill+900s, not exit+900s, so the exit-vs-wait arithmetic must subtract the exit age for those rows. **Re-run pending:** the exit card re-reads when ~10 exits accumulate — join naked_exit closes → their fills → mid_h3 and compare the recorded 15m mid against the exit price, replacing the inferred bid ladder. No quoting or risk behavior changed; the live fleet DB is untouched until its next restart runs the idempotent ALTER TABLE.

### Session 51 — 2026-08-12: the exit counterfactual becomes one command — recorded 15m mid vs exit price in pairs_ev_report

**Question.** Session 50 added the mid_h3 (15m) markout horizon, and Session 49's exit card re-read is pending at ~10 exits. The re-read's join — naked_exit closes → their fills → mid_h3, comparing the recorded 15m mid against the exit price — was still a hand-written query. Make it a printed section of `scripts/pairs_ev_report.py` so the pending card is one command.

**Experiment card.** Single metric: the counterfactual's integrity. For every naked exit the report must print the recorded 15m mid against the exit price — and when it cannot, it must say exactly why (five honest states, never a silent blank). The join must reproduce the Session 49 sample before it is trusted anywhere else.

**Method.** (1) Close → triggering fill: same condition_id + side, nearest ts within 10s of the close. Exits fire at age ≈ 0, so the triggering fill sits inside the same second — measured deltas 0.08–0.17s. (2) Fill → markout row: nearest ts within 30s. This window is load-bearing and easy to get wrong: `store.log_fill` stamps `time.time()` at INSERT while `log_markout_open` uses the sweep's captured `now`, so the two ts values differ by ~0.3s (measured 0.30–0.34s) — an exact match silently finds nothing. (3) States: **recorded** (mid_h3 landed), **pending** (column exists, 15m not elapsed), **no_markout** (fill found, no markout row), **no_fill** (no triggering fill near the close), **no_column** (mid_h3 missing — the fleet has not restarted since Session 50, so the DB predates the migration; checked via PRAGMA before any SELECT touches the column). (4) Aggregate over recorded exits: exits whose mid_h3 sat BELOW the exit price are decisive (the mid itself fell under what we sold at) — exit beat waiting; a mid above the exit price by less than a spread is flagged "waiting may have been better", because the exit sold at the BID while mid_h3 is a MID. Median gap (mid15 − exit) and mean 15m drift (mid15 − fill) round out the section.

**Result.** The join reproduces Session 49 exactly on the live DB: all four exits matched fills at 0.19 / 0.45 / 0.10 / 0.29 (the reported triggering prices), each within 0.08–0.17s of its close. Live report reads all four as **no_column** — honest about the unmigrated DB, not a silent blank; after migrating a full copy through the real store migration the same four read **pending** (column present, no fill has matured under the new sampling yet). The recorded branch is pinned by a fixture (fill 0.40, exit 0.37, mid15 0.33 → gap −4c → exit beat waiting) alongside pending and no_markout in one chain, plus a raw old-schema DB pinning no_column. 2 new tests; full suite **648/648**.

**Verdict.** LIVE — the pending exit card is one command: when ~10 exits accumulate, `python -m scripts.pairs_ev_report` prints the recorded mid15 vs exit price per exit. The state ladder makes the report honest at every stage of the instrument's rollout (no_column → pending → recorded), and the window-based joins plus the mid-vs-bid asymmetry are stated in the report's own limits. One boundary to carry into the re-read: mid_h3 cannot be backfilled, so the four historical exits will read `pending` forever (their 15m readings never existed); the instrument records going forward only.

### Session 52a — 2026-08-12: the spread dashboard's loading path — Tailwind CDN runtime replaced with a static 29KB build

**Question.** Performance audit of the primary dashboard (spread_dash, port 8800) against a loading-checklist: where does the cold load actually spend bytes and main thread? TTFB is a non-issue (6ms -- localhost). The first network-bound THIRD-PARTY cost: `https://cdn.tailwindcss.com` -- the ~350KB Play runtime (~110KB gzipped), render-blocking in <head>, fetched from a public CDN on every cold load; the page itself logged Tailwind's own console warning "cdn.tailwindcss.com should not be used in production" during the audit.

**Experiment card.** Single metric: cold-load critical-path weight -- bytes transferred and main-thread work before first render. Keep if the static build reproduces the CDN's rendering (verified at runtime, not assumed), discard if any rendered element loses its styling.

**Method.** Built a minified Tailwind v4 stylesheet (29.4KB) whose only @source is spread_dash_html.py itself, so the class set is exactly what the templates emit -- no runtime theme config exists, so a static build is faithful. Shipped as `server/_tailwind_css.py` (a generated constant, reproducible via `python -m scripts.build_tailwind_css`, which installs tailwindcss+cli into a throwaway temp dir and regenerates the constant). Replaced the CDN <script> with the inlined stylesheet, in the same head position (cascade-safe: the post-Tailwind overrides are all !important).

**Result.** Runtime verification, not assumption: a scratch server on :8805 (new build) vs the live :8800 (old build) on the same fleet DB. (a) Class coverage -- every rendered class on the live dashboard (299 tokens with real data) and the landing (167) exists in the built CSS; the only two exceptions are `tab-btn`/`tab-panel`, which are unstyled JS query hooks by design (verified: they carry no CSS in either build). (b) Computed styles identical old vs new for the body, the frosted-glass panels, and the signal-green buttons. (c) The one v3->v4 default difference (border default color: gray-200 -> currentColor) is invisible: zero elements with a visible border rely on the default. (d) Console clean on the new build; the old build fetched cdn.tailwindcss.com/3.4.17 (and logged the production warning), the new fetches none. Measured: cold load ~105KB html + ~110KB gzipped CDN JS + fonts, versus ~132KB html (29.4KB of it the stylesheet) + fonts -- and the 350KB JS parse/execute is gone from the main thread entirely. Full suite 648/648, plus a regression pin (test_dashboard_page: the CDN must never return, the stylesheet must be inlined) -> 649/649.

**Verdict.** LIVE -- the "Tailwind CDN / Google Fonts as offline dependencies" audit item's Tailwind half is closed: the page now renders with zero third-party JS and works offline. Regeneration is the one workflow change the design agent must know -- ANY new class in spread_dash_html.py needs `python -m scripts.build_tailwind_css` or it renders unstyled (documented in the build script's docstring and the generated file's header). Honest limits: /capital.js stays a blocking script on purpose (the pages' inline scripts call its functions top-level during parse, so `defer` would throw a ReferenceError -- noted, not forced). No strategy/quoting/risk behavior touched.
### Session 52b — 2026-08-12: the spread dashboard's loading path — Google Fonts consolidated to variable ranges

**Question.** The same cold-load audit found the second network-bound cost: the Google Fonts css2 URL carried seven static weights that expanded to 33 referenced files (per-weight x unicode-range subsets), all fetched over the network.

**Experiment card.** Single metric: referenced font files per family on cold load. Keep if the consolidated URL renders the same faces, discard if any weight or character subset regresses.

**Method.** Consolidated the fonts URL to variable ranges (`wght@600..800` / `wght@400..700`): 33 referenced files -> 9, and the browser fetches ~1-2 per family.

**Result.** Verified on the same :8805-vs-:8800 comparison: both families render identically from the variable ranges with no console errors, and the referenced-file surface dropped from 33 to 9.

**Verdict.** LIVE -- the fonts half of the audit item is closed. Honest limit: Google Fonts remains a network dependency -- the variable-font consolidation cut the surface, and self-hosting the two OFL variable fonts is the natural follow-up.
### 2026-08-12 (design): PR #23 review fixes -- distribution modal fully keyboard-operable

**Question.** The expanded-chart modal already moved focus to its close button on open, trapped Tab/Shift+Tab, and restored focus on close (coderabbit round 1) -- but all four chart triggers were divs with onclick handlers, so a keyboard user could never open the modal at all.

**Method.** Converted the four triggers (the verdict panel's two bell-curve tiles and the evidence panel's two large chart cards) from divs to real `<button type="button">` elements, each with an aria-label naming the chart it expands and inline reset styles (font, color, background, border, padding, text-align) so the snapshot Tailwind stylesheet needs no regeneration. The open/focus/trap/restore JS is unchanged.

**Result.** Every trigger is now Tab-reachable and activatable with Enter/Space; opening focuses the modal's close button, Tab is trapped inside the dialog, and closing returns focus to the invoking button. Dashboard page suite 26/26, node --check parses both pages' scripts.

**Verdict.** LIVE -- the modal's keyboard contract is implemented, not merely declared via aria-modal.
### 2026-08-12 (design): PR #23 review fixes -- sparse float-mark history no longer truncated by the read window

**Question.** float_history's bounded read window (MAX(ts) - max_points*min_spacing*2, added for the dashboard poll cost) was sized for a DENSE table: with marks at 0/3600/7200/10800 and max_points=3 / min_spacing=60, the window covered only the last 360s and returned one mark instead of the newest three -- sparse timestamps were excluded before thinning (coderabbit round 3).

**Method.** The window now anchors to the newest mark as before, but if thinning keeps fewer than `max_points` points the window doubles and the read repeats -- stopping once the window spans the whole table or the cap fills. The bounded-read cost is preserved (a dense table still fills the cap on the first read; the doubling path only fires on sparse data, converging in O(log(span/window)) cheap reads), and thinning stays deterministic oldest-first.

**Result.** Regression test seeds 0/3600/7200/10800 with max_points=3 and asserts the series is [3600, 7200, 10800]; the existing cap/thinning/prune and cold-DB tests still pass. First draft of the widening loop never terminated when the window covered the whole table (widening forever); the `newest - window > 0` guard fixes it. market_events + dashboard suites green.

**Verdict.** LIVE -- sparse history keeps full coverage while dense tables still read a bounded window.
### 2026-08-12 (design): PR #23 review fixes -- markout readers guard empty schemas and close their connections on failure

**Question.** Two stats readers had fragile failure shapes: with a `markouts` table carrying no mid_h* column (a pre-migration DB the dashboard's read-only connection cannot migrate), `pooled_markout_neff` and `markout_stats` built a SELECT with an empty column list -- malformed SQL that surfaced as a swallowed OperationalError (neff) or a confusing `error` string (markout_stats). Separately, the read-only connections in all three readers (plus `pairs_ev`) were closed only on the success path, leaking a handle on every exception (coderabbit).

**Method.** Both markout readers now return their safe empty/zero fallback when `_markout_read_cols` yields nothing, and all three readers split connect from query so a `finally: c.close()` runs on success and failure alike.

**Result.** New test seeds a raw pre-migration `markouts` table and pins `pooled_markout_neff` -> the zeroed dict and `markout_stats` -> zero counts with no `error` key. stats + dashboard suites green.

**Verdict.** LIVE -- empty schemas read as empty, and connection handles can no longer leak.
### 2026-08-12 (design, Session 53): :8801 stops being "the old dashboard" -- the scan page becomes the funnel product, and the loading path closes

**Question.** The 2026-08-12 migration demoted server.fleet_dash to :8801 "until the scan view is redesigned" -- but the old fleet page was still served there, a second full dashboard nobody should read, and the scan view's data feed rebuilt the entire payload on every 10s poll (~4-5s under the live writer's lock traffic), so the page looked dead between polls.

**Method.** Removed the fleet view, its table renderers, and the view switcher from fleet_dash.PAGE entirely: the funnel (RAW -> FILTERS -> FINAL -> GRADUATED + census strip + both near-miss trackers + the trial callout) IS the page now, and the mast liveness moved into the pipeline payload (fleet_alive + snapshot_age). The /api/pipeline endpoint got spread_dash's cache treatment: a background thread refreshes the payload every 10s (keyed by the RUN path, lock-guarded) and the endpoint serves the freshest snapshot instantly, falling back to an on-demand build only when the snapshot is missing or stale. Both apps gzip their large inline HTML/JS blobs (GZipMiddleware); capital.js -- stable, shared verbatim by both dashboard pages -- got a public 1h cache; the Google Fonts stylesheet became preload+onload so first paint never waits on the Google round trip; a data:, favicon kills the 404. Tests that pinned the deleted fleet page (settled-table presence, the node-run hero arithmetic, order-depth renderers) were deleted with the code they asserted; a new test pins the pipeline cache identity on repeat polls and its per-RUN keying.

**Result.** 650/650. The scan page polls /api/pipeline every 10s against a cached snapshot (first build ~4-5s, then instant); the scan page now loads with a single inline script and zero external blocking resources, and the dashboard/landing keep only /capital.js blocking (on purpose -- their inline scripts call it during parse). The demotion's TEST half had already landed during PR #23's review round (test_dashboard_page asserts the scan-only PAGE); this commit is the implementation half that makes the committed tree green again.

**Verdict.** LIVE -- :8801 is a purpose-built funnel, not a demoted dashboard, and the loading path needs no further network work beyond the already-noted self-hosted fonts.
### 2026-08-12 (design, Session 54): the pending exit card becomes a dashboard tile -- exits since the last re-read at a glance

**Question.** The exit-wait counterfactual (Sessions 49-51) is the only open evidence card in the pairs rule: at n=4 exits the exit-cost constant still rests on four closes, and the report re-reads at ~10 exits. But the instrument is invisible between re-reads -- the operator opens the dashboard, sees no hint that exits are accumulating toward the re-read threshold, and the pending card is easy to forget.

**Method.** Added a five-state exit-card ladder to `stats.pairs_ev()` (counts only, same windowed joins as `scripts/pairs_ev_report.py`: naked_exit close -> nearest fill within 10s -> its markout row within 30s): recorded / pending (15m not elapsed) / no_markout / no_fill / no_column (markouts table predates mid_h3). The re-read threshold is the module constant `PAIRS_EXIT_CARD_RE_READ_AT` (10), shared by the tile. The fleet payload already computes `pairs_ev`; the summary endpoint now surfaces it (`fl["totals"]["pairs_ev"]` -- the field lives under `totals`, and the first wiring read the wrong level), and the verdict panel renders a sixth tile: exits / re-read-at with a progress bar that turns green and says READY when the threshold is met.

**Result.** 652/652. Three test fixes along the way: (1) the exit-card ladder test now seeds through a real write, and the no-column test drops the `markouts` table from the seeded schema instead of creating a raw DB without `market_events` -- which was passing for the WRONG reason, because `pairs_ev`'s KPI query against a missing table swallowed the whole read into the empty card; (2) the summary test seeds a temp DB AND points `stats.DB` at it (the store resolves its path via `CFG.db_path()`/env, stats via a module-level constant -- a patch to only one silently reads the live `run/fleet.db`); (3) the rebuilt Tailwind CSS covers the new `lg:grid-cols-6` verdict grid.

**Verdict.** LIVE -- the pending exit card is visible at a glance and ready to re-read at ~10 exits, with the tile and the report guaranteed to agree on the ladder's state.


### 2026-08-12 (design, Session 55): the 15m counterfactual stops waiting on exits -- fill-horizon capture on every rule-era fill

**Question.** The exit card (Session 54) re-reads the exit-window counterfactual only when naked exits accumulate -- but the exit path almost never fires (4 exits in 3 rule-days, zero PAIR_WINDOW_EXPIRED ever: the completion branch resolves the one-sided fill first), so the instrument produces no evidence on the current pace. Worse, its pending classification was NULL-based: every pre-migration fill (mid_h3 never written, because the fleet predates the Session 50 migration) would read "pending" forever, lying about accumulating.

**Method.** Added a fill-horizon ladder to `stats.pairs_ev()`: every rule-era fill (ts >= the first PAIR_COMPLETE -- the same population as the completion-side dist slice, so the tile and the report cannot disagree) is classified by its 15m mid: recorded (mid_h3 landed; drift = mid_h3 - fill_price accumulated) / pending (the 900s window is genuinely still open: ts + window > now) / no_markout (window elapsed, never written) / no_column (markouts table predates mid_h3). Classification is TIME-based, not NULL-based; window_sec comes from `CFG.markout_horizons[3]`. The verdict tile renders it as a second progress bar under the exit card, with the signed drift mean colored by sign.

**Result.** 654/654. Live run/fleet.db: 166 rule-era fills -> 8 recorded (mean +5.63c/sh, median +1.50, 6 positive / 2 negative), 1 pending, 157 no_markout -- the pre-migration legacy now counted honestly instead of falsely "pending". New tests pin the four states, the drift aggregate, the no_column branch, and the tile wiring; the summary test asserts the payload shape.

**Verdict.** LIVE -- the 15m exit-window counterfactual now accumulates evidence on every fill at the current pace, and pre-migration fills can no longer masquerade as pending.


### 2026-08-13 (strategy, Session 56): statistical confidence interval engine added for 10-tier bankroll sensitivity analysis

**Question.** When evaluating starting bankroll options ($100 to $1,000 in $100 steps), how can we measure statistical confidence intervals and risk-adjusted return metrics without assuming Gaussian returns or ignoring downside variance?

**Method.** Added `calc_confidence_intervals(returns)` and `get_active_db_path()` to `strategy/stats.py`. Uses exact Student's t-distribution critical values ($t_{df, 0.025}$ and $t_{df, 0.010}$) for 95% and 98% CIs on small samples ($n < 30$), and calculates both the annualized Sharpe Ratio ($S = \frac{\bar{x}}{s} \cdot \sqrt{252}$) and Sortino Downside Ratio ($S_{sortino} = \frac{\bar{x}}{\sigma_{down}} \cdot \sqrt{252}$). Supports environment override `SPREAD_HUNTER_DB` for multi-instance isolated database evaluation.

**Result.** 2 new unit tests in `tests/test_bankroll_ci_stats.py` and `tests/test_bankroll_db_override.py` pass cleanly. Tested against sample return vectors: correctly computes mean, SE, 95%/98% bounds, and Sortino downside semi-deviation. Full test suite green (656/656).

**Verdict.** LIVE -- the statistical CI and downside risk engine is ready for multi-bankroll experiment evaluation.


### 2026-08-13 (server, Session 57): dashboard 10-tier bankroll matrix endpoint and 10-panel UI grid view landed

**Question.** How can the operator view all 10 concurrent bankroll experiment tiers simultaneously in one unified visual dashboard grid?

**Method.** Added `/api/bankroll_matrix` endpoint to `server/spread_dash.py` and implemented `renderBankrollMatrix()` responsive 10-panel grid view in `server/spread_dash_html.py`. Each panel displays sample progress ($N / 100$), win rate, Student's t 95%/98% CIs, Sortino downside ratio, and automated invalidation alerts.

**Result.** Dashboard endpoint `/api/bankroll_matrix` returns live tier statistics. Full test suite green (658/658).

**Verdict.** LIVE -- multi-bankroll matrix UI is live and integrated into the spread-hunter dashboard.


### 2026-08-13 (server, Session 58): rebuilt inline Tailwind CSS stylesheet for bankroll matrix components

**Question.** How to ensure new Tailwind CSS utility classes in `server/spread_dash_html.py` render correctly in production without falling back to CDN script loading?

**Method.** Executed `python -m scripts.build_tailwind_css` to update `server/_tailwind_css.py` (29.9 KB minified). 

**Result.** `test_generated_tailwind_css_is_fresh` passes cleanly. Full test suite green (662/662).

**Verdict.** LIVE -- static Tailwind CSS bundle updated and verified fresh.


### 2026-08-13 (server & strategy, Session 59): server entrypoint and SPREAD_HUNTER_BANKROLL config override landed

**Question.** How to ensure `python -m server.spread_dash` starts the server directly on port 8805 and `strategy.config.load()` picks up tier bankroll overrides?

**Method.** Added `if __name__ == "__main__": uvicorn.run(...)` to `server/spread_dash.py` on port 8805. Added `SPREAD_HUNTER_BANKROLL` environment variable parser to `strategy/config.py` `load()` updating `bankroll_usd`, `allocation_budget`, and `max_committed_usd`. Updated `scripts/launch_bankroll_experiments.py` command invocation to `strategy.fleet`.

**Result.** `python -m server.spread_dash` launches uvicorn on port 8805 directly. Full test suite green (662/662).

**Verdict.** LIVE -- server launcher and per-tier config override fully wired.




