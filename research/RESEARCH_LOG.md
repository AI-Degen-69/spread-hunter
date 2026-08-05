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

**Verdict.** LIVE — the mechanism is *rest, do not cross*. Built a maker sim
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
- Bot running, single instance, fresh `maker.db`.
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

**Re-run hygiene.** Per AGENTS.md a code change invalidates the sample, so the partial-DB runs were archived (`maker.db.archived_20260722_225907/230650/235454/235939`) and `maker.db` wiped. Bot + dashboard relaunched fresh on the new code with `env -u PYTHONPATH` (the Hermes venv-leak fix from Session 2 still required). Verified: equity $5,000, realized $0, hedges 0, Phase A_CENSUS, census median pair 0.995 (cap fix holding).

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

**Method.** Added `hard_block(cfg, inv, side, price, own_book, hedge_book)` to `strategy/risk.py` and called it from `_decide_quotes_rewards` in place of the `imbalance >= max_naked_shares` branch. Three arms, ordered so the reason names the cheapest certain rejection first: hedge-token health, own-book health, then the dollar cap. An exposure-REDUCING side returns None before any arm runs, so the light side is never gated. `max_naked_shares` was removed from `strategy/config.py` rather than kept beside `max_naked_usd` — two caps in two units cannot both be binding, and the looser one governs silently. The emergency stop-loss trigger was restated in the same unit: the deficit is valued at the heavy leg's average cost and compared against `max_naked_usd * emergency_hedge_frac`. Added `enable_hard_blocks: bool = True` so the gate can be measured on its own. 16 new tests; full suite 359 passed, up from 344.

**Result.** A market holding 140 naked UP at 0.82 ($114.80 against a $120 budget) rests nothing on UP and full size on DOWN; one dollar under the budget it still rests on UP, so the cap and not an unrelated filter is what binds. A healthy UP book paired with a 0.999-bid/no-ask DOWN book now rests nothing on EITHER side, and the UP reason names the hedge token — the case that produced the observed loss, where the position was built in two prints before the book degraded. With `enable_hard_blocks` false the same market at 200% of budget quotes both sides, which isolates the new gate as the cause of every result above. The emergency cross still fires with an unhealthy hedge book, per R4.

**Caveat.** Restating the emergency trigger in dollars preserves the relationship (it fires at 80% of the cap, inside it, with a losing heavy leg) but not the share count: 288 shares was $144 at 0.50 and $234 at 0.8152.

**Verdict.** LIVE. This changes quoting behaviour, so the running sample is invalidated from here — archive `maker.db` before the next run rather than mixing configs in one dataset.

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

**Result.** Replay against the recorded paper database `run/fleet.db` (67 fills, 23 markets) refused 15 fills (22.4%) on the live path, avoiding **$729.88** of incremental naked cost against **$26.19** of attributed realized P&L forgone. The profitable-market stop check refused 6 of 40 fills (15.0%), so it did not trigger. Per-gate evidence was: dollar cap 5 fills / $178.27 naked cost; price band 28 / $743.19; pair cost 16 / $268.18; hedge and own book health 2 each / $223.05 each. Depth remained UNEVALUATED for all 134 health checks. `maker.db` is an empty stale database (0 fills), so it is a zero-data report rather than the recorded-run verdict.

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
