# Backlog - Issue #942 (Kelly criterion sizing)

Research note required by #942 before any sizing code is written. It records the
formula family, the fractional-Kelly rationale, the multi-market/correlation
caveat, the chosen framing with the rejected framings and why, and the Kelly
fraction setting. Wiring (allocation level, `size_for`, fallback) is specified in
`tasks/plan.md` and SPEC.md; this note does not repeat it.

## What the Kelly criterion is

The Kelly criterion (Kelly, J.L., 1956, *A New Interpretation of Information
Rate*, Bell System Technical Journal) gives the fraction of bankroll `f*` that
maximizes the expected value of **log** wealth — long-run geometric growth, not
arithmetic return.

### Formula family

- **General binary form:** `f* = p/l − q/g`, where `p` = win probability,
  `q = 1 − p`, `g` = fraction gained on a win, `l` = fraction lost on a loss.
- **Net-odds form** (`b = g/l`, reward : risk): `f* = (bp − q)/b = p − q/b`.
  Positive only when `p·b > q` — either the win/loss ratio or the reward/risk
  ratio must beat 1; both can individually be below 1 as long as their product
  exceeds it.
- **Binary share bought at price P paying $1:** `b = (1−P)/P`, giving
  `f* = (p − P)/(1 − P)`. Buying at 0.45 with true win probability 0.50 ⇒
  f* ≈ 9.1% of bankroll. This is the direct "market odds vs my probability"
  reading used on binary prediction markets.
- **The spread-hunter pair is not that bet.** When both legs fill, the pair
  merges for $1.00 — near-risk-free — and p ≈ 1 makes the formula scream for
  leverage. Kelly does not apply to "the pair merges"; it applies to the
  *measured outcome distribution of the bot's closed trades*, which is the
  only bet with an honest, measured p and payoff.

### Kelly requires true probabilities — and amplifies error

Overestimating `p` produces an oversized bet and erodes (reversed) growth;
past roughly `2f*`, geometric growth goes negative. Garbage probabilities in,
garbage sizing out. The bot has no calibrated resolution-probability model, so
"true p" for this strategy can only mean **the bot's own measured closed-trade
distribution** — and even that is an estimate with estimation error.

### Fractional Kelly is the practitioner standard

Full Kelly tolerates 50%+ drawdowns and amplifies `p` estimation error.
Betting half or a quarter of `f*` sacrifices little growth for far better
drawdown and model-error tolerance (MacLean, Thorp & Ziemba, *Good and bad
properties of the Kelly criterion*, 2010; Thorp, *The Kelly criterion in
blackjack, sports betting, and the stock market*; Wikipedia: "Kelly criterion",
retrieved 2026-11-05). Results implying f* > 1 mean the model demands leverage —
**cap the fraction, never lever up**.

### Multi-market / correlation caveat

The single-bet formula assumes one bet at a time. Simultaneous, correlated
bets (this bot quotes many markets at once, and its markets correlate — same
underlying events, same settlement source) violate it; portfolio Kelly is a
different and harder problem. Samuelson's critique applies: an individual's
constraints can override growth-optimal sizing. Practical consequence for this
repo: Kelly here is an **allocation-level budget hint**, not a license to
override the risk gates — it rides on top of the naked-budget taper and the
dynamic caps, never instead of them.

## The chosen framing: (a) bot-wide measured stats, allocation level

**What it is:** `f*` computed from the bot's measured closed-trade
distribution — `win_rate`, `payoff_ratio` — exactly as `trade_analytics` in
`core_brain/kpi.py` already computes (`kelly_fraction = win_rate −
(1−win_rate)/payoff_ratio`, `half_kelly = kelly/2`), and applied at the
**allocation level**: the same role `bot_quote_alloc_couple_pct` plays today,
but computed as `fractional_kelly × bankroll_basis` instead of a constant 1%.

**Why this framing wins:**

1. It is the only framing whose inputs are *measured* for this bot — no model
   to build, no priors to invent.
2. It needs no new instrument: the stats exist in `kpi.py` and the reuse path
   was agreed (never reimplement the formula).
3. Its failure mode is legible: below the minimum sample (default 30 closes)
   sizing falls back to today's dollars mode, byte-for-byte; an edge that is
   gone reads as f* ≤ 0 ⇒ quote nothing.
4. The venue's `venue_sync` closes are excluded from the sample (they are
   wallet history, not the bot's record — 72 rows, +$112.93, 67.1% win rate vs
   the bot's own seven closes at −$2.40 on the live registry, `kpi.py` comment
   above `VENUE_SYNC_RUN_ID` slice).

## Rejected framings

**(b) Per-market measured edge** — rejected: the sample per market is zero.
A market resolves once; there is no repeated bet at the same odds to measure.
Bot-wide closes number in the tens; splitting them per market yields pure
noise. Discriminating per market is a follow-up question only after a much
larger realized sample exists.

**(c) Directional per-token resolution probability** (`f* = (p−P)/(1−P)`) —
rejected: the bot has no resolution-probability model, and building one is a
new project (priors per market, calibration, backtest) that #942 explicitly
excludes ("A per-market resolution-probability model … If the research note
selects that framing instead, the issue must be re-scoped before Station II
plans"). It also misprices the instrument this bot trades: for a near-risk-free
pair, p ≈ 1 and the formula demands leverage — absurd for this strategy. The
direct "market odds vs my probability" reading is correct for directional
binary markets; the spread hunter is not trading that.

**Sizing the pair as the bet** (allocating Kelly to "will the two legs fill")
— rejected for the same reason: fill completion is near-certain conditional on
the quote logic and irrelevant to risk-adjusted growth; the binding risk is
the inventory residual, and that is what the naked-budget taper already
governs.

## The Kelly fraction setting

- **Quarter Kelly (operator decision, 2026-11-05)**: `bot_quote_kelly_pct =
  0.25`, operator-tunable, **hard-capped at 1.0** (a setting above 1.0 clamps
  to 1.0; it never levers). Full Kelly implied by the model is capped at
  f* = 1.0 before the multiplier. See "Sizing decision — 2026-11-05" below;
  the earlier half-Kelly default was superseded on the operator's call.
- **Full Kelly (multiplier 1.0) is for shadow/rehearsal runs only** — an
  operator rule, recorded here; not enforceable from inside config and not
  code-gated. Live runs should keep ≤ 0.25.
- **Sample threshold**: below `bot_quote_kelly_min_closes` (default **30**,
  env-tunable), sizing is byte-identical to `bot_quote_size_mode="dollars"`
  and the quote reason says so (`kelly fallback: insufficient sample (n
  closes)`). The codebase's sufficiency machinery is z-score per estimator
  (`SAMPLE_SUFFICIENCY_LEVELS`, `kpi.py`), not a flat bar; 30 is the chosen
  conservative default, consistent with the ≥30-closed bars used elsewhere in
  the repo's evidence standard. The exact number remains an operator dial.
- **Basis**: the same dynamic portfolio-value basis `derive_dynamic_caps`
  uses — at quote time `cfg.bot_quote_bank_baseline_usd` already carries it
  (merged by the fleet state each rotation). No separate static baseline.
- **Mode**: opt-in `bot_quote_size_mode="kelly"`; the shipped default stays
  `"dollars"` until the operator flips it hands-on.

## Samuelson, correlation, and the honest limits

Kelly is a growth-optimal bet for one repeated bet with known odds. It is not a
license to override constraints; correlated simultaneous bets degrade it toward
zero edge in effect; estimation error is amplified, not hedged, by betting
bigger. This note's settlement of those objections is the fraction (quarter,
not full), the sample floor, the cap at no-leverage, and the rule that Kelly
rides
*inside* the existing caps and taper — never past them.

## Sizing decision — 2026-11-05 (operator call: quarter Kelly)

The operator's position: *"since we don't know the edge and never will know
the true edge, the convention is to use quarter Kelly — just to be on the
safe side."* The 2026-11-05 follow-up research supports it for this bot.

**What the research says:**

- **Thorp's asymmetry** (Kelly 2007; MacLean/Thorp/Ziemba 2010; recap in
  Downey 2023): overbetting is worse than underbetting — half Kelly protects
  against negative growth at the cost of ≤ 25% less growth. Fractional Kelly
  is *overdetermined*; it needs no single justification.
- **Crane, *Prediction Markets Newsletter* (2023-03-10)** — the strongest
  direct evidence for this operator: half Kelly does not provide strong
  evidence to stop on a ruinous drawdown (50% drawdown in 1,000 rounds with a
  real 10% edge is 11% likely; LR = 7.4 — ambiguous). **25% Kelly: 0.8%
  likely, LR = 75 — a 50% drawdown is near-conclusive evidence you have no
  edge.** Full Kelly: 46% likely, LR = 2.1 — you cannot act on it. The point
  of sizing smaller is not comfort; it is being able to *learn whether you
  have an edge* and quit decisively if you don't. "In practice, we never
  know everything we need to know." No universal magic number — the fraction
  follows from a stop-loss (E) and an evidence threshold (LR), not dogma.
- **Poluri, *How Many Trades Before You Trust Kelly?* (SSRN 7549165,
  2026-10-01)** — Monte Carlo of 200k traders per setup: optimal fraction of
  Kelly ≈ **S/(S+1)** with evidence score **S = N·k²**; roughly **half Kelly
  needs ~0.7/k² trades** to be justified. At small n the correct fraction is
  far below half — "earn your Kelly as n grows", not a fixed 0.5.
- **Estimation error does not wash out with "we think we know p"** — Kelly
  amplifies it symmetrically in size, asymmetrically in damage.

**Why this binds harder on this bot:**

1. **Live evidence is ~zero.** The live registry carries a handful of bot
   closes beside 72 `venue_sync` wallet-sync rows (fake 67.1% win rate) —
   the bot has not earned half Kelly on live data, let alone full Kelly. The
   476 paper merges show economics (+$1,172, avg pair cost 0.960) but paper
   fills do not suffer the real adverse selection that audits fills live.
2. **The "bet" is not a directional coin.** For the near-risk-free pair,
   f* → leverage nonsense; the only honest inputs are the measured closes,
   and those say "not enough n yet". Sizing must therefore be conservative
   until n builds.
3. **The bot caps are features, not friction.** Kelly at 0.25 inside 6%
   naked / 25% order / 90% total caps means the binding constraint stays the
   risk layer — which is what a live-money repo wants while the edge is
   unproven.

**Conclusion for this bot (binding on #942):**

- **Shipped default: `bot_quote_kelly_pct = 0.25`** (quarter Kelly),
  operator-tunable, hard cap 1.0. Half Kelly (0.5) becomes a setting to
  *earn*, not a starting point; full Kelly (1.0) stays shadow/rehearsal-only.
- **Revisit at n ≥ 30 live closes** with the edge holding: quarter → half is
  the sanctioned step; the step must come from measured live closes, not
  paper, and not from hope.
- **The 30-close dollars fallback stays.** Quarter Kelly with no data is not
  a license to bet — the floor gates before the fraction applies.
- **No-edge (f* ≤ 0) still quotes nothing;** fraction choice cannot rescue a
  non-edge (Crane: fractional Kelly only delays ruin if there's no edge).
- **Review cadence:** re-derive the fraction after every 25 additional live
  closes; if the measured edge decays or f* ≤ 0, the sizing self-silences by
  design.
- **What would falsify quarter Kelly as too timid:** sustained n ≥ 100 live
  closes with f* > 0 and realized drawdown ≪ quarter-Kelly projection — the
  operator then promotes the knob to 0.5, not before.

## Sources (retrieved 2026-11-05)

- Kelly, J.L., 1956. *A New Interpretation of Information Rate.* Bell System
  Technical Journal.
- Thorp, E.O., *The Kelly criterion in blackjack, sports betting, and the stock
  market* (10th Int. Conf. on Gambling and Risk Taking).
- MacLean, Thorp & Ziemba, *Good and bad properties of the Kelly criterion*, 2010.
- Wikipedia: "Kelly criterion", retrieved 2026-11-05.
- Downey, M. (2023-04-07), *Why fractional Kelly? Simulations of bet size with
  uncertainty and downside risk mitigation*, matthewdowney.github.io.
- Crane, H. (2023-03-10), *How much to bet?* (Prediction Markets Newsletter),
  harrycrane.substack.com.
- Poluri, N. (2026-10-01), *How Many Trades Before You Trust Kelly? Fractional
  Kelly Sizing Under Parameter Uncertainty*, SSRN abstract_id=7549165 (optimal
  fraction ≈ S/(S+1), S = N·k²).
- Codebase evidence: `core_brain/kpi.py` (`compute_trade_analytics`,
  `kelly_fraction` / `half_kelly`, VENUE_SYNC_RUN_ID slice), `core_brain/risk.py`
  (`size_for` ladder), `core_brain/trader_loop.py` (`_fleet_state` dynamic-caps
  merge), `core_brain/config.py` (`couple_allocation_usd`, `derive_dynamic_caps`),
  `docs/backlog/i787-*.md` (≥30-closed evidence bar).
