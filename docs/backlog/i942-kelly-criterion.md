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

- **Half Kelly**: `bot_quote_kelly_fraction = 0.5`, operator-tunable, **hard-
  capped at 1.0** (a setting above 1.0 clamps to 1.0; it never levers). Full
  Kelly implied by the model is capped at f* = 1.0 before the multiplier.
- **Full Kelly (multiplier 1.0) is for shadow/rehearsal runs only** — an
  operator rule, recorded here; not enforceable from inside config and not
  code-gated. Live runs should keep ≤ 0.5.
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
bigger. This note's settlement of those objections is the fraction (half, not
full), the sample floor, the cap at no-leverage, and the rule that Kelly rides
*inside* the existing caps and taper — never past them.

## Sources (retrieved 2026-11-05)

- Kelly, J.L., 1956. *A New Interpretation of Information Rate.* Bell System
  Technical Journal.
- Thorp, E.O., *The Kelly criterion in blackjack, sports betting, and the stock
  market* (10th Int. Conf. on Gambling and Risk Taking).
- MacLean, Thorp & Ziemba, *Good and bad properties of the Kelly criterion*, 2010.
- Wikipedia: "Kelly criterion", retrieved 2026-11-05.
- Codebase evidence: `core_brain/kpi.py` (`compute_trade_analytics`,
  `kelly_fraction` / `half_kelly`, VENUE_SYNC_RUN_ID slice), `core_brain/risk.py`
  (`size_for` ladder), `core_brain/trader_loop.py` (`_fleet_state` dynamic-caps
  merge), `core_brain/config.py` (`couple_allocation_usd`, `derive_dynamic_caps`),
  `docs/backlog/i787-*.md` (≥30-closed evidence bar).
