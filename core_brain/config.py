"""Config for the spread hunter maker.

THE OBJECTIVE IS SPREAD CAPTURE: buy UP+DOWN below $1.00, merge the pair back to
collateral, keep the discount. Rebates are extra. `objective` below names a
PRICING MODE, not the objective -- read its comment before trusting the literal.
See AGENTS.md for the full statement and the universe rules.

Quoting parameters (price band, offsets, balance target, fill caps) are derived
from 56,768 of @powerwinner's BTC/ETH 5-min fills over 2026-07-14..21 (2,970
markets). That is where the NUMBERS came from -- it is not where the bot
trades. The live universe is the Market Filter's graduated list in
runtime/markets.json.
"""
from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass

from core_brain import rehearsal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class MakerConfig:
    # LEGACY. The rolling 5-minute BTC series this project started on, kept only
    # because `core_brain/markets.py` and the `probe` latency harness still resolve a
    # live window from it. THE FLEET DOES NOT TRADE THIS. The live universe is the
    # Market Filter's graduated list in runtime/markets.json -- liquid sports, esports, macro
    # and political markets under `select_max_days_to_resolve` -- and 5-min BTC was
    # measured dead on adverse selection (see the pinned-market block at the bottom
    # of this file). Do not read this field as a statement of what the bot trades.
    series_slug: str = "btc-up-or-down-5m"

    # --- virtual account --------------------------------------------------
    # Fresh paper run wallet. This is the total paper capital available,
    # not a promise that the allocator may commit every dollar at once.
    bankroll_usd: float = 100.0

    # --- pricing mode (NOT the objective) ---------------------------------
    #
    # THE OBJECTIVE IS SPREAD CAPTURE, AND THIS FIELD DOES NOT NAME IT.
    #
    # The income is buying UP+DOWN below $1.00 and merging the pair back to
    # collateral, which pays exactly $1.00: profit = 1.00 - (avg_up + avg_dn).
    # Measured on the paper-run database, 476 merge closes returned +$1,172.35 at an
    # average pair cost of $0.96006. Over the same run maker rebates accrued
    # about $0.22/day against $566 committed -- four hundredths of a percent.
    # Rebates are extra; they are not why this bot exists.
    #
    # What this field actually selects is WHERE THE QUOTE IS PRICED FROM:
    #
    # "pair"           : price off the ASK -- rest one tick under it. MEASURED DEAD over
    #                    60 markets: that puts every quote ~half a spread ABOVE mid, so
    #                    the pair costs 1.00 + spread by construction. The book runs at
    #                    101% all window; there is nothing to capture. Unreachable in
    #                    production and must stay that way.
    # "spread_capture" : price off the MID -- rest both legs at mid - offset. Formerly
    #                    called "rewards". READ IT AS "from mid":
    #                    because mid_up + mid_dn ~ 1.00, resting `offset` under mid on
    #                    both sides assembles the pair at ~1.00 - 2*offset, which is
    #                    exactly how a sub-$1.00 pair gets built. It is the correct
    #                    mechanism for spread capture.
    #
    # The reward SCORE, S(v, s) = ((v - s)/v)^2 * size with v = rewardsMaxSpread,
    # still shapes the offset tradeoff (closer to mid scores more but fills more
    # adversely) and is reported in the quote reason. It does not size anything --
    # see `quotes.reward_score`, whose only caller formats a log string.
    objective: str = "spread_capture"

    # How far below mid to rest, in price units. This is the whole tradeoff:
    # score is quadratic in closeness (0.5c -> ~16% of market score, 2c -> ~7%,
    # measured over 3906 recorded legs), but closer means more fills, and fills
    # on this book are adverse. Start back from the touch and walk in only if
    # realised adverse selection stays small.
    reward_offset: float = 0.020

    # --- inventory skew (replaces the settlement cross-hedge) --------------
    # A resting quote that fills leaves us one-sided, and a one-sided position
    # held to settlement is a coin flip on the full $1.00. The old answer was
    # to cross the spread near close, which only ever executed once the outcome
    # was already known (measured hedge prices: 0.01, 0.02) -- it booked luck
    # as profit and protected nothing.
    #
    # The answer that actually works is the standard market-maker one: skew.
    # Long UP -> quote UP further from mid (harder to fill, adds less) and
    # DOWN closer to mid (easier to fill, which FLATTENS us). It runs every
    # cycle from the first share of imbalance, while both sides still cost real
    # money, and it keeps us two-sided so the reward score is preserved.
    # The spring is wound by DOLLARS at risk, not by share count: `skew_offset`
    # scales this cap by `risk_utilization` of the naked side, so it is at full
    # stretch exactly when the dollar budget is full. A share-denominated ramp
    # (240 shares to full stretch, removed) answered a 100-share naked leg with the
    # same push at 0.85, where it is $85 of downside, as at 0.15, where it is
    # $15 -- so on lol-maz-mg1 it was still ramping at 233 of 240 shares while
    # $190.26 was already at stake and the position was fully built.
    max_skew: float = 0.015             # cap, in price units
    min_reward_offset: float = 0.005    # never quote nearer mid than this

    # THE HARD CAP on directional exposure, in DOLLARS of naked cost. This is
    # the only unit the cap is stated in -- a share-denominated twin was
    # removed rather than kept alongside it, because two caps in two units
    # cannot both be the binding constraint and an operator reading "not
    # adding" would have no way to tell which one bound.
    #
    # Skew is a spring and it bottoms out AT this budget: `risk_utilization`
    # clamps at 1.0, so past the cap more exposure produces no more response.
    # Something has to own that range. The share cap that used to own it could
    # not, and the reason is the unit, not the level:
    # on a binary market the downside of one long share IS the price paid for
    # it, so 360 shares permitted $72 of risk at 0.20 and $293 at 0.8152 --
    # loosest exactly where a wrong resolution costs most.
    #
    # Measured 2026-08-05 on lol-maz-mg1: 233.40 UP shares at an average of
    # 0.8152, $190.26 at risk, against a 360-share cap that read 233 and stayed
    # silent while 85% of the fleet's -$223.32 unhedged float sat in that one
    # market. Three limits were armed and none of them bound.
    #
    # $120 binds between 171 shares (at 0.70) and 400 shares (at 0.30) inside
    # the price band, and would have stopped lol-maz-mg1 at roughly 147 shares
    # instead of 233. 0 disables the rule, same as every other cap here -- and
    # it disables the whole dollar system with it, because this number is the
    # denominator of all three rules that read it: the hard block, the skew
    # spring above, and U3's size ladder (`risk.size_for`), which decays resting
    # size as base*(1-utilization)^2 so the last order before the cap is 16% of
    # full size rather than 100% of it.
    # Dynamic risk scaling percentages (denominated against total portfolio value)
    naked_risk_pct: float = 0.06        # 6% of total portfolio value
    order_risk_pct: float = 0.25        # 25% of total portfolio value
    bankroll_ceiling_pct: float = 0.90  # 90% of total portfolio value (bankroll ceiling)

    max_naked_usd: float = 6.0
    # Switchable so the dollar gates can be measured on their own rather than
    # bundled with the rest of a release -- the same convention as
    # enforce_price_band and enable_emergency_hedge. False makes `hard_block`
    # return None for every side, and nothing else changes.
    enable_hard_blocks: bool = True
    # Strict Paired Inventory Accounting: block quoting the heavy leg when an
    # unhedged imbalance exists and size the light leg strictly to balance the pair.
    strict_paired_inventory: bool = True

    # HARD CEILINGS on order and total notional, mirrored here so the
    # dashboard's /api/parameters endpoint reads one config object, not two.
    #
    # THE VALUE THAT BLOCKS A LIVE ORDER IS core_brain/venue.py, NOT THIS ONE.
    # `trader_loop._enforce_caps` imports MAX_ORDER_USD and MAX_TOTAL_USD from
    # core_brain.venue and checks against those constants; nothing in the live
    # path reads the two fields below. Keep them equal to the venue constants,
    # and change venue.py when you mean to change the limit -- editing only
    # these fields moves the number the dashboard displays and not the number
    # that refuses the order. 0 disables the rule, same as max_naked_usd above.
    max_order_usd: float = 25.0
    max_total_usd: float = 90.0

    # FLOAT-MARK RETENTION. The fleet writes one fleet-wide open-position mark
    # per sweep (unrealized float, committed dollars, naked residue -- the
    # same totals the dashboard derives at read time). 90 days keeps the
    # Total-equity history deep while bounding the table, instead of growing
    # forever like market_events; the dashboard downsamples to one point per
    # minute anyway, so finer-than-a-minute retention buys nothing.
    float_mark_retention_days: float = 90.0

    # BOOK HEALTH. Three arms, all on ONE token's book.
    #
    # A price this close to either end means the market has decided. There is
    # no spread left to capture and the naked leg a fill would create is
    # already decided against us -- wta-kalinsk-kessler finished quoting 0.999
    # bid against a 0.001 ask, and the position was unhedgeable at any price.
    decided_price: float = 0.02
    # Widest two-sided spread still worth quoting into. Above 2 x
    # max_spread_from_mid (9c) the entire reward window lies INSIDE the
    # spread, so landing in it means being the most exposed order in the book
    # -- measured on a 0.26/0.42 market, six cents better than anyone else.
    # 6c leaves margin under that arithmetic.
    max_book_spread: float = 0.06
    # WIDE-BOOK TRIAL (#145). When set, this REPLACES the three ceilings that
    # together decide whether a wide book can be quoted at all. It is not a
    # tuning knob and it has no permanent counterpart: the shipped values above
    # never change, and a run either carries the trial or it does not.
    #
    # Why one knob moves three numbers. Run 145 measured `mid_UP + mid_DOWN` at
    # 1.0000 in 1,247 of 1,247 samples and the touch pair at or above
    # `max_pair_cost` in every one of them, so a one-tick book has no pair to
    # assemble at any offset. The only sub-$0.99 touch pairs the run saw --
    # $0.85 and $0.96 -- came from the ten samples where a book widened to
    # ~2.8c. Three separate gates refuse that regime, and leaving any one of
    # them at its shipped value makes the trial measure nothing:
    #
    #   * `select_max_book_spread` -- the ranker never offers the market;
    #   * `max_book_spread` -- `risk.book_health` refuses to quote it
    #     ("book too wide 13.0c > 6.0c", observed in run 145's own log);
    #   * `max_spread_from_mid` -- on a wide book the `best_bid + tick` cap in
    #     `quote_resting_price` pushes the price further from mid than the
    #     reward window allows, and `_decide_quotes_from_mid` then refuses it
    #     as outside that window. This is the subtlest of the three and the
    #     easiest to leave behind.
    #
    # The third is a REWARD rule binding on markets that pay no reward: every
    # market in run 145's universe carried `daily = 0.0`. Widening it costs
    # reward score that is already zero.
    #
    # SET ONLY FROM `HUNTER_WIDE_BOOK_TRIAL`, AND ONLY IN A DECLARED REHEARSAL.
    # `max_book_spread` is a safety gate, not a preference: a wide book is one
    # where a fill leaves a leg that may not be closeable. Every other
    # `HUNTER_*` override in `load()` documents itself as scoped to a
    # rehearsal and none of them enforces it, because `load()` is shared by
    # both paths. This one enforces it -- see `resolve_wide_book_trial`, which
    # refuses unless `rehearsal.is_rehearsal()` holds.
    wide_book_trial: float | None = None
    # Summed bid depth below which the book cannot absorb an exit. A proxy,
    # not a measurement of exit liquidity: one aggregated number is the most
    # the recorded book shape supports.
    min_book_depth_sh: float = 200.0

    # EMERGENCY STOP-LOSS. The hard cap above stops us ADDING to the heavy
    # side; it does nothing about the exposure already on the book. Skew is
    # supposed to flatten that with resting orders, and it does -- when someone
    # comes to hit our light-side bid. In a market moving hard against us
    # nobody does: our light-side bid sits under a mid that keeps walking away
    # from it while the heavy leg loses money every tick. That is precisely the
    # case where paying the taker fee is the cheap option.
    #
    # Fraction of max_naked_usd at which the light side is allowed to CROSS the
    # spread instead of resting. The deficit is valued at the HEAVY leg's
    # average cost, which is what the missing hedge is worth: 400 shares short
    # is $80 of exposure at 0.20 and $340 at 0.85, and only one of those is an
    # emergency. Stating the trigger in the same unit as the cap is what keeps
    # it inside the cap at every price.
    #
    # 0.8 puts the exception inside the cap on purpose, so it fires while there
    # is still a hedge to buy rather than at the moment the cap freezes us at
    # maximum exposure.
    emergency_hedge_frac: float = 0.8
    # Switchable so the exception can be measured on its own -- a taker order
    # is the one thing this strategy otherwise never does.
    enable_emergency_hedge: bool = True

    # --- EV system -------------------------------------------------------
    # See docs/superpowers/specs/2026-07-29-maker-ev-system-design.md.
    # Rent is measured; the cost of being filled is not. These parameters run
    # the measurement and the rule that acts on it. Every value is a starting
    # hypothesis, to be revised once real markout data exists.
    #
    # Horizons at which a fill is re-priced, in seconds. 5m catches immediate
    # adverse flow; 6h is the shortest horizon on which a long-dated market
    # plausibly repriced on news.
    #
    # 15m (900s, Session 50) is the EXIT-WINDOW read: the naked-exit
    # counterfactual -- where was the mid 15 minutes after we exited? -- which
    # Session 49 had to infer from the bid ladder. APPENDED LAST, not sorted
    # into place: a horizon maps to its column by position, so inserting it
    # between 5m and 1h would relabel every existing mid_h1/mid_h2 reading on
    # the live DB. Because it matures BEFORE the 1h and 6h horizons while
    # sitting AFTER them in the tuple, readers that want the longest matured
    # horizon must compare durations (never columns), and the sampling loop
    # must not seal a row done when this horizon records.
    markout_horizons: tuple[float, ...] = (300.0, 3600.0, 21600.0, 900.0)
    # Below this many fills the mean is dominated by noise on a thin book, and
    # evicting a sound market on noise costs real rent.
    #
    # Was 20, which was unreachable. Measured over the 2026-08-02 fleet run:
    # 58 fills produced 47 markout rows across 18 markets, and the BEST
    # sampled market matured 7. `per_market_stats` therefore returned
    # `insufficient_sample` for every market on every cycle, `next_state`
    # returns the state unchanged on that verdict, and `gate_state` stayed
    # NORMAL for the entire run -- `market_gate` finished with zero rows. A
    # gate that cannot fire is not a conservative gate, it is an absent one.
    # 8 keeps a real noise floor while letting the state machine run.
    markout_min_sample: int = 8
    # FLEET-WIDE markout sample. The per-market number above is thin by
    # nature on a universe that rotates daily, so a market can be toxic for
    # its whole life without ever qualifying for a verdict of its own. The
    # fleet aggregate matures far faster -- n=42 against a per-market best of
    # 7 on the same run -- so a market with no verdict of its own inherits
    # the fleet's rather than defaulting to NORMAL.
    #
    # Higher than the per-market floor on purpose: this reading gates every
    # market at once, so acting on it early is the more expensive mistake.
    markout_fleet_min_sample: int = 25
    # Half the ~1c edge a paired quote earns: losing more than this per share
    # means the fill was unprofitable before inventory risk even starts.
    markout_widen_threshold: float = -0.005
    # CATASTROPHIC markout. The graduated path (NORMAL -> WIDENED -> EXITED)
    # exists because one cent of mispricing and genuine toxic flow look alike
    # on a single reading -- but that argument only holds for SMALL losses. A
    # mean of -2c/share is four times the widen threshold and larger than a
    # full taker fee: no amount of backing off recovers it, and the second full
    # sample the WIDENED path demands is bought with real money. Past this
    # magnitude the gate exits immediately, skipping WIDENED and the doubled
    # sample requirement. The insufficient_sample guard still applies -- this
    # is a magnitude bypass, not a noise bypass.
    markout_catastrophic_threshold: float = -0.020
    # Widened quotes stay inside the 4.5c reward window, so rent continues.
    widen_offset: float = 0.035
    # ALLOCATOR KNOBS -- READ BY NO LIVE CODE.
    # `marginal_return_floor`, `allocation_budget` and `max_market_frac` below
    # belong to the water-fill allocator in `scoring/allocate.py`, which only
    # the Market Filter's scoring uses. The trading path has no allocator: the
    # Trader quotes whatever runtime/markets.json lists and sizes each couple from
    # `couple_allocation_usd`. Grep confirms no reference to these three
    # anywhere in core_brain/ or dashboard/. The live capital bounds that DO bind
    # are `max_naked_usd`,
    # `max_fleet_naked_usd` and `max_committed_usd`.
    #
    # Marginal $/day per $ committed, below which capital is better left idle.
    marginal_return_floor: float = 0.02
    # Leave wallet headroom for inventory and order-lifecycle timing.
    allocation_budget: float = 900.0
    # Ceiling on any ONE market's share of that budget.
    #
    # The water-fill was a diversifier and is not one. `marginal`
    # is daily*T/(capital+T)^2, which is nearly FLAT in capital whenever
    # competitor depth T dominates our size -- so the argmax never changes
    # and a single market absorbs every increment. Measured 2026-08-02: one
    # market took the full $900 budget, `shares_for` turned it into a
    # 900-share order, and it filled in one print for $792 -- 79% of a
    # $1,000 wallet, 1.98x the $400 per-market cost cap.
    #
    # 0.15 puts the floor at roughly seven concurrently funded markets. That
    # number is set by the variance, not by taste: per-fill markout measured
    # -$7.58 with a standard deviation of $56.68, so at full concentration a
    # single fill moves the book by more than the entire expected edge of a
    # hundred fills and no mean is readable at any sample size.
    max_market_frac: float = 0.15
    # Set per-market by the fleet each cycle: NORMAL | WIDENED | EXITED.
    gate_state: str = "NORMAL"

    # --- profit taking ----------------------------------------------------
    # Nothing in this strategy ever closed a position: every fill rode to
    # settlement in 2027, so a filled pair immobilised capital that had been
    # earning daily rent. Selling a pair that has appreciated converts locked
    # capital back into working capital.
    #
    # Selling means crossing the spread, so BOTH legs pay the taker fee. The
    # move therefore has to clear two fees before it clears anything else.
    profit_take_fee_per_share: float = 0.017

    # MERGE (U2). There is no `merge_gas_usd` here, and that is the point.
    #
    # A merge is submitted through Polymarket's relayer, which sends the
    # transaction from its own address and pays the gas. The docs say so
    # outright -- `/trading/wallets-auth`, "Execute Gasless Transactions":
    # approve token spending, transfer funds and manage positions from the
    # account wallet without paying gas, where split, merge and redeem are the
    # position lifecycle methods it names. A merge of N pairs returns exactly
    # N * 1.00 pUSD with nothing deducted on the way out.
    #
    # This field used to hold a seeded $0.05 that no transaction had ever
    # confirmed. Against a one-tick maker edge -- a cent on a $0.99 pair --
    # five cents is five pairs of profit, and it sat on the economic gate that
    # decides GO. It was not conservatism; it was a cost that does not exist.
    #
    # `core_brain/gas.py` remains the only place that may claim a gas figure,
    # because it reads the receipt and attributes the burn to whoever actually
    # sent the transaction. For a relayer-submitted merge that answer is 0.00.

    # OVER-PARITY MERGES (U5, KTD2b). 4.5% of measured pairs cost more than
    # 1.00, so merging them books an immediate loss. Holding is not obviously
    # better: the pair pays exactly 1.00 either way, so the nominal comparison
    # is a wash and the real question is what the freed capital earns in the
    # meantime.
    #
    # The velocity test answers that -- merge when projected rent on the
    # released capital over the remaining hold beats the concession plus gas.
    # This is the hard bound around it. Checked BEFORE the velocity arithmetic
    # runs, and never yielded to: without it, a large projected-rent figure
    # would license an arbitrarily bad exit price, which is how a capital
    # efficiency rule turns into an inventory fire sale.
    #
    # 1c/share. Roughly a third of what selling the same pair would pay in
    # taker fees, so the exception stays cheaper than the alternative it
    # replaces.
    merge_max_loss_per_share: float = 0.01

    # How long the freed capital is assumed to keep earning, in days, when
    # pricing the velocity exception above. NOT the time to resolution --
    # markets here settle in 2027, and crediting ~500 days of rent would make
    # every over-parity pair mergeable regardless of price, which is precisely
    # what merge_max_loss_per_share exists to stop.
    #
    # 30 days is deliberately short: long enough that freeing capital is worth
    # something, short enough that the exception stays rare and the loss cap
    # remains the binding constraint rather than a formality.
    merge_velocity_hold_days: float = 30.0

    # REWARD ELIGIBILITY (U4). Polymarket's published rule: "The minimum reward
    # payout is $1; amounts below this will not be paid." A market projecting
    # under a dollar a day does not pay a fraction of a dollar -- it pays zero.
    #
    # Measured 2026-07-30, only 4 of 20 fleet markets cleared it. The other 16
    # held capital and earned exactly nothing, which makes spreading thin
    # actively harmful rather than merely inefficient. Concentration is not a
    # preference here, it is what the payout rule requires.
    #
    # Whether the threshold applies per-market or across a maker's aggregate is
    # not settled by the docs. Per-market is the conservative reading and is
    # safe under either; the first real payout settles it empirically.
    # PAPER RUN ONLY -- READ BY NO LIVE CODE (same status as the allocator
    # knobs above). These price a REWARD market's payout floor, and the live
    # universe has none: every graduated market in runtime/markets.json carries
    # `source: "spread"` with `daily: 0.00`, because the markets that actually
    # trade publish clobRewards: 0. For those the income is the pair discount,
    # not an emission, and no floor applies.
    reward_min_payout_usd: float = 1.0
    # Multiple of the floor a market must clear to be funded. Projections are
    # noisy and competitors arrive, so 1.0x would fund markets that cross below
    # the line the moment anyone else quotes. 1.5x buys headroom.
    reward_floor_multiple: float = 1.5

    # SPREAD CAPTURE (U6). The payout floor above, and the sizing that feeds
    # it, assume income arrives as reward emissions. The markets that actually
    # trade -- bitcoin-up-or-down-*, ~$92k/24h -- publish clobRewards: 0, so
    # the allocator valued them at exactly zero and the fleet instead funded a
    # universe that printed 9 tape-backed fills in 74 hours.
    #
    # For those markets the income IS the spread, and
    # `allocate.spread_capture_daily` converts it into the same $/day pot the
    # reward path uses. These two are its inputs.
    #
    # Fraction of the quoted spread earned per share traded. A resting maker
    # earns at MOST half the spread on a round trip, and less in practice,
    # because flow that reaches a resting order is adversely selected. This is
    # a starting hypothesis at half of that theoretical half -- to be replaced
    # by the first real markout sample on one of these markets, exactly as
    # every other EV parameter here is.
    spread_capture_frac: float = 0.25
    # Fallback quoted spread when the market spec carries none. 1c is the
    # observed book on the up-or-down series. The estimate is most sensitive to
    # this number, so a spec reporting its own spread is always preferred.
    spread_capture_default_spread: float = 0.01

    # TRADABILITY AND HORIZON (U6). Reward yield per dollar of capital prefers
    # a thin book by construction, and a thin book is thin because nobody
    # trades it. Ranking on that metric alone selected a universe that, over
    # 11.6h, printed 48 trades across 20 markets and never traded at all in 9
    # of them -- so 74 hours of running produced 9 tape-backed fills.
    #
    # A market that does not trade cannot fill a resting order. That is not an
    # argument against reward farming, which is a real strategy earning real
    # emissions; it is an argument against measuring reward farming with
    # fill-based instruments and reading the zeros as a maker result.
    # HARD MARKET SELECTOR. These are intentionally stricter than the older
    # $25k research gate: a market must have enough real flow to make a resting
    # quote reachable and enough immediate exit liquidity to make a naked fill
    # survivable. The selector requires this depth independently on YES and NO.
    #
    # LOOSENED 2026-08-25 (operator directive), permanent, not a trial:
    # $250,000 -> $125,000. The 2026-08-10 funnel audit below read the
    # volume-reject population as 1-3 orders of magnitude under the old bar, so
    # on that data this admits little beyond what a $200k bar would. It is a
    # deliberate widening of the candidate pool; the markouts of whatever it
    # admits are the evidence that says whether to keep it.
    select_min_volume_24h_usd: float = 125_000.0
    # DEPTH AND SPREAD, ALIGNED TO THE LIVE GATE (2026-08-06). The entry
    # pre-filter was stricter than the continuous protection that actually
    # governs every quote decision: `risk.book_health` (below) requires only
    # 200 SHARES of depth (roughly $100 notional at a 0.50 mid) and a 0.06
    # spread, checked on EVERY quote, not once at pick time. $5,000 was an
    # 41x margin over the $120 `max_naked_usd` worst-case position this depth
    # exists to make exitable -- comfortable, but double-gating against a live
    # system that already refuses to add to a bad book, refuses an untradeable
    # hedge leg, and caps naked cost independent of how the market was picked.
    # Measured 2026-08-06: of 189 scored candidates in a live off-peak read,
    # ~120 failed on YES-side depth alone, most by a wide margin (not a close
    # call at $5,000 -- either well over or an order of magnitude under), so
    # this mainly widens the CANDIDATE POOL rather than admitting marginal
    # books. Set to the same bar the live system already enforces rather than
    # a stricter, redundant one: $1,000 (8x the $120 worst case) and 0.06.
    #
    # LOOSENED 2026-08-25 (operator directive), permanent, not a trial:
    # $1,000 -> $500. The "$120 worst case" in the paragraph above is the
    # `max_naked_usd` of `scoring/config.py`, not of this file: the field is
    # $6 here, so $500 is roughly 80x the cap the Trader actually enforces,
    # not 4x it. The two modules disagree on that number and this comment does
    # not reconcile them; it only stops the ratio being read off the wrong
    # one. The continuous `risk.book_health` gate (200 shares, 0.06 spread,
    # checked on EVERY quote) is unchanged, and that gate, not this pick-time
    # one, is what refuses a bad book while a position is open.
    #
    # The two gates also disagree at the boundary on purpose, and
    # `tests/test_market_selection_bars.py` pins it: a book at exactly $500 of
    # top-3 bid depth is REFUSED (`book_allowed` compares `depth <= bar`),
    # while 24h volume at exactly $125,000 is ADMITTED (`tradable` compares
    # `volume < bar`).
    select_min_top3_depth_usd: float = 500.0
    # DEPTH-GATE TRIAL (U32). When set, the RANKER gates on this bar instead of
    # `select_min_top3_depth_usd` -- a controlled loosening licensed by the
    # near-miss tracker (READY_TO_TRIAL: 29 unique markets, 19 with measured
    # depth >= half the bar). The permanent bar above never changes; the trial
    # is opt-in per rank run, adopted markets are tagged `trial_depth_usd` in
    # runtime/markets.json, and their markouts are watched before the change is
    # made permanent. Overridable from HUNTER_DEPTH_TRIAL_USD; the ranker's own
    # `--trial-depth` flag wins over both.
    select_min_top3_depth_usd_trial: float | None = None
    # NOTE (ADR-0001): the two depth fields above are RANKER-ONLY knobs --
    # the live fleet deliberately skips a depth-in-USD gate and no live code
    # reads them. See docs/adr/0001-live-fleet-skips-usd-depth-gate.md
    # VOLUME-GATE TRIAL (U36). Same staging contract as the depth trial above:
    # when set, the RANKER gates 24h volume on this bar instead of
    # `select_min_volume_24h_usd`; adopted markets are tagged `trial_volume_usd`
    # and their markouts are the decision evidence. The figures below were
    # measured against the THEN-permanent $250,000 volume and $1,000 depth
    # bars, both since lowered above (2026-08-25); read them as the history
    # that argued for loosening, not as a description of today's bars.
    # Measured 2026-08-10 funnel
    # audit: of 54 markets that cleared depth but failed volume, only 2 would
    # fund even with the gate fully lifted, and the top of that population
    # sits at $235-242k -- so the honest trial bar is $200k (a 20% staged
    # loosening), NOT the half-bar $125k the depth tracker's convention would
    # suggest (U33 measured the volume-reject population 1-3 orders of
    # magnitude under the bar; a $125k bar admits nothing). The depth trial
    # alone ($1,000 -> $750) admits 0 additional eligible markets today -- the
    # depth near-miss population has 3-25x-thin volume -- which is why the
    # volume trial is the real lever, and the two run together so a
    # $200k-volume market with a $750-depth book is admissible. Overridable
    # from HUNTER_VOLUME_TRIAL_USD; the ranker's own `--trial-volume` flag wins.
    select_min_volume_24h_usd_trial: float | None = None
    select_max_book_spread: float = 0.06
    # 30 days admits liquid macro, sports, and political markets while keeping
    # long-dated 2027 markets excluded.
    select_max_days_to_resolve: float = 30.0

    # How long to average competitor depth before sizing a position. One
    # snapshot sized the whole fleet on 2026-07-29 and read a competing score
    # of 35 for a market that measured 3,727 live -- a 100x error that put the
    # top-ranked market at $0.25/day actual against $18.96 projected.
    rank_sample_window_sec: float = 1800.0
    # Re-rank cadence. runtime/markets.json was frozen from 2026-07-29 01:39 while
    # the fleet ran against it for a day and a half.
    rerank_interval_sec: float = 600.0
    # Required profit per share AFTER both fees. Set at roughly one fee's
    # width again, so a close is only taken on a move clearly larger than the
    # cost of taking it -- at 1c the threshold sits inside the noise of a
    # one-tick book flicker and would close positions on nothing.
    profit_take_net_threshold: float = 0.020

    # OPPORTUNITY COST. The threshold above prices a close against zero -- it
    # asks "is closing better than holding?", never "is this dollar better
    # spent somewhere else?". When the allocator is budget-bound while markets
    # well above the marginal floor go underfunded, holding a stagnant pair
    # costs the return that dollar would have earned in the starved market, and
    # that cost is real whether or not the pair itself is up.
    #
    # Under scarcity the required net drops to a SLIGHTLY NEGATIVE number: pay
    # half a cent per share to free capital that can earn multiples of it per
    # day elsewhere. Deliberately small -- this releases capital, it does not
    # authorise dumping inventory at any price.
    scarcity_close_threshold: float = -0.005
    # How far above the marginal floor a market's return must still sit, with
    # the budget exhausted, before capital counts as scarce. At 1.0 any fully
    # spent budget would trip it, which would make the relaxed threshold the
    # normal case rather than the exception.
    scarcity_marginal_multiple: float = 2.0
    # Set per-cycle by the fleet allocator, same mechanism as gate_state and
    # fleet_naked_usd. False here so a single-market bot (archived on the
    # legacy-bot-8788 branch) is unaffected -- it has no allocator and therefore
    # no scarcity to report.
    capital_scarce: bool = False

    # FLEET-WIDE exposure ceiling, in dollars of unhedged cost.
    #
    # max_naked_usd bounds ONE market. It works -- and it is not enough.
    # Measured 2026-07-29: 16 markets, every one inside its own 360-share cap,
    # summing to $1,630 of unhedged exposure. Expected value on that book was
    # +$62 with a standard deviation of +/-$456 -- the variance is seven times
    # the edge. A per-market cap cannot see that, because no single market is
    # misbehaving.
    #
    # $800 is roughly half the observed exposure, which halves the swing while
    # leaving room for the light side to keep flattening. It costs rent:
    # capped markets quote one-sided and score at 1/c, c=3.0.
    max_fleet_naked_usd: float = 400.0
    # Current fleet-wide unhedged cost, injected each cycle by the fleet
    # runner. Zero here so a single-market bot (archived on the legacy-bot-8788
    # branch) is unaffected -- it has no fleet to be over budget.
    fleet_naked_usd: float = 0.0

    # TOTAL COMMITTED CAPITAL (U3). Everything above bounds the UNHEDGED leg;
    # nothing bounded the hedged one. A matched pair cannot lose -- it pays
    # exactly $1 -- so it was free, and it is not: until U2 it was
    # frozen until 2027, and even with merge it is money that is committed
    # right now and cannot be committed anywhere else.
    #
    # The real ceiling was max_cost_per_market x markets = $400 x 20 = $8,000
    # against a nominal $1,200 allocation_budget. Measured 2026-07-30: $9,588
    # had left the wallet -- $7,452 in paired inventory, $767 naked, $1,369
    # resting in offers -- while the dashboard's headline return divided rent
    # by the $1,369 alone and reported 1.80%/day. Against the money actually
    # committed it was 0.256%/day. The cap and the honest denominator are the
    # same fix.
    #
    # Counts inventory cost PLUS resting offer notional, because both are
    # dollars that are spoken for. $2,000 leaves room above the observed
    # working set without permitting another $9.5k drift.
    max_committed_usd: float = 1000.0
    # Injected each cycle by the fleet runner, same pattern as fleet_naked_usd.
    # Zero for a single-market bot, which has no fleet to total up.
    committed_usd: float = 0.0

    # FLEET CIRCUIT BREAKER (U6): NORMAL | WIDENED | HALTED, derived once per
    # sweep from the POOLED markout by `gate.fleet_posture` and injected here
    # by the fleet runner, same per-cycle mechanism as `gate_state` above.
    #
    # Every cap above bounds a QUANTITY -- dollars naked, dollars committed,
    # shares per order -- and none of them reads whether the fills being bought
    # are any good. This one does, and it is the only fleet-wide rule that can
    # stop us adding while every individual market still looks healthy.
    #
    # Deliberately NOT persisted, unlike EXITED: it describes the current
    # pooled reading rather than judging a market, so it is re-derived every
    # sweep and lifts by itself when the pool recovers. NORMAL here so a
    # single-market bot (archived on the legacy-bot-8788 branch) is unaffected
    # -- it has no fleet whose markout could be pooled.
    fleet_posture: str = "NORMAL"

    # Maker pool per 5-min window, for turning score-share into dollars.
    # Measured 2026-07-28 from 15 recorded windows: 68405 shares traded per
    # window -> $716 median taker-fee pool -> 20% = $143 to makers. Treat as an
    # order of magnitude, not a promise: data-api /trades reports each
    # participant's own side, so the volume behind it may be double-counted,
    # in which case the true pool is nearer $70.
    est_reward_pool_usd: float = 143.0

    # --- quoting ----------------------------------------------------------
    # He posts on BOTH outcomes. In a binary market a bid on DOWN at 0.40 is
    # economically a sell of UP at 0.60, so bidding both sides IS two-sided
    # market making expressed as buys only. He never sells (0 SELLs in 56,768).
    quote_both_sides: bool = True

    # How far below the best ask to rest. 1 tick = passive, at the touch.
    # Deeper = better price but far lower fill probability.
    ticks_below_ask: int = 1
    tick_size: float = 0.01
    # The venue's real minimum tick, from Gamma: orderPriceMinTickSize = 0.001.
    # The 1c book spread is therefore TEN ticks, not one -- `tick_size` above is
    # a spread-width proxy the "pair" objective used, not the venue tick. Reward
    # prices round to this so we can sit at a genuine price level.
    price_tick: float = 0.001

    # Venue minimum size floor (5 shares on graduated markets).
    # Replaces the stale 50-share rewards-scoring floor.
    min_quote_shares: int = 5
    max_spread_from_mid: float = 0.045

    # HOW a funded market is sized. "shares" is the paper run's behaviour --
    # `quote_shares` shares per leg. "dollars" sizes each leg from the Owner's
    # allocation rule below and lets price decide the share count.
    #
    # This is a separate field on purpose. Sizing mode and funding are two
    # different questions, and `quote_shares == 0` already answers the second
    # one: it is the allocator saying THIS MARKET IS DEFUNDED, quote nothing.
    # Overloading 0 to mean "size from dollars" would make every defunded
    # market start quoting again -- the exact incident recorded on
    # `risk.size_for`, where 17 defunded markets kept posting 50-share orders.
    size_mode: str = "dollars"

    # WHETHER this market is funded, and at what share scale in "shares" mode.
    # 0 means defunded in BOTH modes. The allocator owns this number.
    quote_shares: int = 120

    # The Owner's allocation rule, 2026-08-19:
    #     couple = max(bankroll * couple_risk_frac, min_couple_usd)
    #     leg    = couple / 2
    # At a $31.17 wallet and at $100 both give $6.00 a couple, $3.00 a leg --
    # the floor binds until bankroll passes $600.
    couple_risk_frac: float = 0.01
    min_couple_usd: float = 6.0

    # A $3.00 leg buys about 6 shares against a 5-share venue floor, so ANY
    # attenuation of that size lands under the floor and the order disappears.
    # The soft price-risk taper therefore cannot express itself at pilot size:
    # the real choice per market is the floor or nothing.
    #
    # True quotes the floor and says the taper was waived; False refuses. The
    # hard band block (0.10-0.90) is untouched either way -- this waives a
    # multiplier, never a gate. It stops mattering on its own once the leg
    # allocation is large enough for the taper to survive the floor, so it
    # needs no removal, only a bankroll.
    waive_attenuation_below_floor: bool = True

    # A couple is two legs. When inventory is FLAT, resting one leg alone is
    # not a smaller couple -- it is a naked position taken on purpose, and the
    # only thing that can close it is the taker path paying the spread it was
    # resting to earn. So on a flat book both sides quote or neither does.
    #
    # This does NOT apply when inventory is unbalanced: there the single
    # remaining quote is the light side, and it FLATTENS the position. Blocking
    # it would freeze the market at maximum exposure, which is the failure the
    # taper's light-side exemption exists to avoid.
    require_two_sided_when_flat: bool = True

    # --- powerwinner's two entry rules ------------------------------------
    # PRICE BAND. 54% of powerwinner's BTC 5-min volume enters at 0.30-0.70, and
    # he has ZERO trades at 0.98+. That is where the spread -- and the taker fee
    # he is avoiding, fee = 0.07*p*(1-p), which peaks at p=0.50 -- are widest, so
    # it is where being the maker is worth most. Outside the band the spread
    # collapses toward one tick on a near-certain outcome and there is nothing to
    # capture, while the downside stays the full $1.00.
    #
    # WIDENED 0.30-0.70 -> 0.10-0.90 (2026-08-11). The 0.30-0.70 range was tuned
    # to the coin-flip BTC series, where every mid is ~0.50. The spread universe
    # the fleet now quotes (tennis, CS2, MLB favourites) legitimately trades at
    # 0.15-0.85, and the band's own protection -- refusing a near-settled outcome
    # at the extreme -- lives at the 0.95+ edge, which 0.10-0.90 still refuses.
    # Measured on the live funnel 2026-08-11: 7 of 8 adopted markets had mids
    # outside 0.30-0.70, and the allocator's funded picks were both blocked by
    # the band (Shnaider at 0.255/0.705) -- a funded market the strategy then
    # refused to quote for hours.
    price_band_low: float = 0.10
    price_band_high: float = 0.90
    # QUOTE TIMING. 57% of his entries land in the FIRST 40% of the window.
    # A passive order needs time to be reached; posting late means resting into
    # the minutes when the price is converging on the outcome, which is exactly
    # when a fill is most likely to be adverse.
    quote_window_frac: float = 0.40
    # Both rules are switchable so their effect can be measured one at a time
    # rather than bundled -- a previous run changed two things at once and the
    # result could not be read.
    #
    # `enforce_price_band` reads on BOTH objectives as of U4. It used to be a
    # rule of the "pair" objective alone -- not by design, but because
    # `_in_band` sits below the line where `_decide_quotes_from_mid` returns, so
    # on the objective the fleet actually runs it never executed. Measured
    # 2026-08-05: fills averaged 0.8152 against this nominal 0.30-0.70 band.
    enforce_price_band: bool = True
    enforce_quote_window: bool = True

    # ENDGAME PROXIMITY GATE (#240). Post-mortem of the largest shadow loss
    # (close id 64, spx-up-or-down 2026-09-18, -$1.14): a pair posted with
    # ~32min to resolution, the loop did not revisit the market for 2m52s
    # (full-cycle cadence ~3.5min), one leg filled alone into convergence and
    # the saver sold it at $0.01. When the market can converge faster than
    # the loop can revisit it, a new pair is a one-look bet, not a merge --
    # so inside `endgame_horizon_min` of resolution, with an observed cadence
    # slower than `endgame_max_cadence_sec`, new pair legs are refused (or
    # repriced deeper with `endgame_action="deepen"`). Light-side balancing
    # quotes are never blocked: they reduce exposure. Off by default so
    # shadow runs measure the firing rate before it touches live quoting.
    enforce_endgame_gate: bool = False
    endgame_horizon_min: float = 45.0
    endgame_max_cadence_sec: float = 120.0
    endgame_action: str = "refuse"
    endgame_deepen_offset: float = 0.02
    # Rolling median seconds between fleet rotations, recomputed once per
    # cycle by `_fleet_state` (same pattern as `fleet_posture`). None means
    # unknown, and the gate fails open on unknown -- a missing clock must
    # not silently refuse every quote.
    observed_cadence_sec: float | None = None

    # PRICE-DEPENDENT RISK (U4, R6). Every cap above is priced in dollars and
    # none of them notices WHERE in the 0..1 range a fill lands. On a binary
    # market two different risks move in opposite directions across that range,
    # so they get two different treatments rather than one blended knob:
    #
    #   * VARIANCE peaks at the coin flip. The payout is Bernoulli, so variance
    #     per share is p(1-p): 0.2500 at 0.50 against 0.2100 at either band edge
    #     and 0 at the ends. A 0.50 fill is the one least informative about the
    #     outcome, and it is answered with SIZE -- quote less where the coin is
    #     fairest.
    #   * MAGNITUDE rises with the price. The downside of one long share IS the
    #     price paid for it, so the same share count is $30 of risk at 0.30 and
    #     $70 at 0.70 -- the same unit error that made the old share cap
    #     loosest exactly where a wrong resolution cost most. It is answered
    #     with OFFSET -- demand a better price where a share costs more.
    #
    # `risk.band_risk_factor` computes both from one weight,
    #   w(p) = max(0, 1 - |p - 0.50| / coinflip_halfwidth)
    #   size   *= 1 - coinflip_size_cut * w(p)
    #   offset += price_risk_widen * (w(p) + p)
    #
    # 0.20 is half the width of the 0.30-0.70 band above, so the variance
    # treatment reaches zero exactly where the band stops permitting quotes at
    # all. The two rules then describe one geometry: inside the band the
    # response is graduated, at the edge it is nil, past the edge the band
    # refuses outright. Any other halfwidth would leave a seam -- either
    # in-band prices treated as risk-free, or a cut still ramping at a price
    # that is already forbidden.
    coinflip_halfwidth: float = 0.20
    # Originally a 10% trim, sized off the THEORETICAL variance differential
    # alone (0.2500 at 0.50 vs 0.2100 at the band edge, a 19% gap -- paying
    # much more than half of that in forgone rent looked like buying the
    # smaller risk with the larger certainty). The 2026-08-05 forensic audit
    # then measured the REALIZED cost directly and it dominates that estimate:
    # -10.68c mean drift and -$122.00 size-weighted loss in 0.40-0.60 (n=19),
    # the only price band that read negative on both the mean and the
    # size-weighted total. Raised to 55%, the strongest cut that does not
    # zero out at the coin flip: `size = int(ladder * size_mult)` also gates
    # the LIGHT side (the one order exempt from every OTHER risk rule,
    # because it is the only one that reduces exposure), and the 120-share
    # base falls below the 50-share reward minimum for any cut past ~58%
    # (120 * 0.42 =~ 50). 88% was tried first and silently zeroed BOTH sides
    # at 0.50, flat markets included -- the full exclusion this setting was
    # chosen instead of. Reassess against the go-live readiness panel's
    # per-band markout once more data accumulates under this setting.
    coinflip_size_cut: float = 0.55
    # 1c of extra offset at full weight, in price units. Reads as 0.3c at 0.30
    # and 0.7c at 0.70 on the magnitude term -- a 0.4c differential across the
    # band, comparable to the 0.5c `min_reward_offset` and therefore large
    # enough to change which side fills first -- plus up to another 1c at the
    # coin flip. Worst case 1.5c against a 4.5c window: it tilts the quote
    # without evicting it from the reward window on its own.
    price_risk_widen: float = 0.010

    # --- inventory --------------------------------------------------------
    # He finishes markets ~92% balanced between UP and DOWN (median 0.923).
    # Below this we stop adding to the heavy side and only quote the light one.
    target_balance: float = 0.92
    # Stop quoting a side once the pair would cost more than this. The pair
    # pays exactly $1.00, so anything at/above 1.00 is a guaranteed loss.
    max_pair_cost: float = 0.99

    # THE COMPLETABLE-COST GATE. `max_pair_cost` above is a BOTH-MAKER check:
    # what the pair costs if both legs fill as resting bids. On a binary market
    # UP + DOWN = 1.00, so the legs are anti-correlated -- measured -0.9989
    # across 98 mid steps on shadow run run-2809a7161de1, 97 of them opposite
    # sign -- and simultaneous maker fills are rare by construction. The pair
    # that actually assembles is one maker fill plus a TAKER completion at the
    # other leg's ask, and nothing checked that price: all 209 orders of that
    # run carried max_pair_cost_at_post=0.995 while never testing bid + hedge
    # ask. This is that test. The pair pays exactly $1.00, so `>=` the cap is a
    # booked loss or a zero-profit fill slot -- a ceiling reached, not
    # approached, like every other cap here.
    max_completable_pair_cost: float = 1.00
    # Switchable so the rule can be attributed a result on its own, the same
    # escape hatch `enforce_price_band` and `enable_pairs_rule` have.
    enforce_completable_pair_cost: bool = True

    # --- pacing -----------------------------------------------------------
    # He averages 19.1 fills/market (median 17), one every ~5s.
    max_fills_per_market: int = 25
    requote_interval_sec: float = 2.0
    # THE RE-QUOTE DEAD BAND, in price units. Move a resting order only once
    # the desired price has moved at least this far. On run-2809a7161de1, 205
    # of 205 consecutive re-quotes changed price (median 3.0c) and every one
    # sent the order to the back of the queue at a new level; median order
    # lifetime was 11.7s against a median queue_ahead of 1058.7 shares. Not
    # fidgeting -- the mid walked 0.815 -> 0.285 in 30 minutes and every
    # re-quote answered a real book move. Answering it still cost the whole
    # queue position. Measured suppression on that run's own price series:
    # 1c 0/205, 2c 74/205, 3c 98/205, 4c 118/205, 5c 139/205. 3c roughly
    # doubles median lifetime to ~23s. 2c leaves too much churn; 4c and up hold
    # a price the book has left behind.
    requote_dead_band: float = 0.03
    # THE QUEUE HOLD (#131). Shares-ahead threshold under which a resting order
    # is kept through a price move it would otherwise be re-quoted for. Under
    # price-time priority a cancel sends the order to the back of a new level,
    # and on this venue the queue -- not the price -- is what produces fills:
    # shadow-02 rested 25 orders for 20 minutes behind 25 to 47,469 shares and
    # filled none of them, and the best position of the run was re-quoted away
    # six seconds after it was posted.
    #
    # Ships at 0.0 -- the hold is OFF. Every cancel now records its reason and
    # the queue it held (`orders.cancel_reason`, `orders.cancel_queue_ahead`),
    # and picking this threshold is what that record is for. The hold never
    # overrides the pair-cost re-gate.
    requote_hold_queue_shares: float = 0.0
    # THE DIRECTION HOLD. How far the desired price may fall BELOW a resting
    # bid before that bid is re-quoted. A resting BUY is a limit order: it
    # fills at its own price when a seller sweeps down through it, so a
    # FALLING target is the market arriving, not leaving, and cancelling on it
    # hands back the queue position at the one moment the order was going to
    # fill. A RISING target is the opposite -- the book walked away and the
    # bid is stranded under the market -- and is re-quoted as before.
    #
    # Measured on shadow-01 over 2026-09-15 17:06-21:00: 393 of 755
    # `price_moved` cancels (52%) fired while the best bid was falling toward
    # the order, median lifetime 42s, and the run booked ZERO fills across
    # 1,157 orders. Of those 393, 132 (34%) still rested 1c-5c ABOVE the new
    # best bid -- close enough that the next seller through reaches them. The
    # rest ran out to a p90 of 36c: the market leaving, and an adverse fill to
    # hold.
    #
    # Ships ENABLED at one band above the dead band: a cap under the band can
    # never fire, because an order inside the band was already kept by the
    # tolerance. The hold never overrides the pair-cost re-gate.
    requote_hold_below_target: float = 0.05
    poll_interval_sec: float = 1.0

    # Only quote while the window is open enough to resolve sensibly.
    min_t_remaining_sec: float = 15.0

    # --- risk -------------------------------------------------------------
    max_cost_per_market: float = 400.0
    max_open_markets: int = 3

    # --- pairs-only rule (U35): what happens to a one-sided fill -------------
    #
    # Origin (Session 36 / U32, 112h sample): merged pairs were 7/7 positive
    # while a naked leg held past 15 minutes drifted -18.5c/share by the 1h
    # markout. The rule converts each one-sided fill into either a COMPLETED
    # pair (cross the missing leg at ask when the pair stays under
    # `max_pair_cost`, then merge at parity) or a SAME-WINDOW EXIT of the
    # naked leg at the best bid (pay the half-spread instead of the drift).
    # Switchable like every other behavioural change here, so the rule can
    # be measured on its own.
    enable_pairs_rule: bool = True
    # How long after a one-sided fill the rule may still act. 15 minutes is
    # where the measured drift is still ~0 (+0.09c/share at the 5m horizon)
    # and long before the 1h mark where it is -18.5c.
    pairs_exit_window_sec: float = 900.0
    # The EV formula's payoffs (cents per share of a one-sided fill), RE-
    # MEASURED 2026-08-12 (Session 44) on the rule's own recorded decisions:
    # a completion pays the realized merge capture on rule-era completed
    # pairs (+$368.25 on 10,010.7 shares = +3.68c, 144 closes, 100% positive);
    # an exit costs the realized exit economics (-$16.93 on 461 shares =
    # -3.67c, n=4 closes, includes the taker fee; corrected Session 45 -
    # Session 44's -3.89c came from a $1 mis-add of the same four closes).
    # The original +16.3c was a 302.5-share sample (7 pairs) from the
    # pre-spread era and overstates the current instrument's capture ~4.4x;
    # the KPI would have read +15.78c instead of +3.48c per one-sided fill.
    pairs_complete_gain_cents: float = 3.68
    pairs_exit_cost_cents: float = 3.67

    # --- dual-trigger single-buy stop-loss ---------------------------------
    # Grace period (seconds) to allow the missing maker leg to fill naturally (0.0 default for test baseline; 45.0 in .env for live)
    single_buy_grace_sec: float = 0.0
    # Max adverse price move from fill price before triggering immediate stop-loss exit
    single_buy_max_loss_pct: float = 0.10
    single_buy_max_loss_usd: float = 0.045
    # Optional sizing ceiling per quote (None for standard mode; set via .env / CLI)
    max_quote_shares: float | None = None

    # --- statistical validation gate criteria (Issue #51) -----------------
    # Effect sizes to evaluate in power table (USD per share / price units).
    # NOTE: Effect size delta and standard deviation sigma must share the same units.
    stat_gate_deltas: tuple[float, ...] = (0.01, 0.02, 0.04)
    stat_gate_alpha: float = 0.05          # one-sided alpha = 0.05 -> 90% one-sided CI lower bound
    stat_gate_beta: float = 0.20           # beta = 0.20 -> 80% power
    stat_gate_threshold_pct: float = 1.0   # primary gate threshold on return % (1.0%)
    stat_gate_bankroll_fraction: float = 0.01  # dollar twin gate (1% of starting bankroll)
    # Minimum closes a rehearsal must collect before the sample-size gate can
    # pass. shadow-01 cleared the economic gates at n=38 but failed this one
    # (and the CI was still wide enough to dip below zero at n=16), so the
    # harness now collects at least this many closes before it will issue a
    # verdict that is not INCONCLUSIVE-for-sample. The statistical_validation_run
    # harness stops early once the target is met; the menu passes it on every
    # launch; the report writer gates against it even for ad-hoc reports.
    stat_gate_target_closes: int = 60

    # --- experiment end criteria (decisive test of the maker mechanism) -----
    # Phase A (census): observe this many DISTINCT live markets and measure how
    # often a fillable sub-$1.00 hedged pair exists at ask-1tick. Below the
    # threshold the instrument simply cannot be made profitably -> stop (DEAD).
    # Phase B (verdict): once the census passes, settle this many markets with
    # the CORRECTED strategy and read P&L sign + confidence -> final save/lost
    # call. These numbers are the experiment's stop condition, rendered live in
    # the dashboard so the run ends on evidence, not vibes.
    experiment_census_markets: int = 60
    experiment_verdict_markets: int = 120
    hedge_fillable_min_rate: float = 0.50

    # --- balance enforcement --------------------------------------------
    # The proven loss driver on 5-min BTC is PARTIAL fills: one side rests
    # and fills, the other never does before the window closes, so the market
    # settles one-sided and eats the full resolution loss. A passive maker
    # cannot UN-fill an already-rested side, so the only enforceable balance
    # rule is to CROSS THE SPREAD for the missing leg near resolution. We do
    # this once, only if still imbalanced, inside this many seconds before
    # close. This guarantees every settled market is balanced -> Phase B's
    # win/loss is an unambiguous verdict on the INSTRUMENT, not our execution.
    balance_hedge_sec: float = 20.0

    # --- economics --------------------------------------------------------
    # crypto_fees_v2: takerOnly=true -> makers pay NO fee. Rebate pool is 20%
    # of taker fees, shared pro-rata among qualifying makers. We cannot see the
    # pool, so rebates are ESTIMATED and reported separately from trading PnL,
    # never blended in.
    maker_fee: float = 0.0
    rebate_rate: float = 0.20
    fee_rate: float = 0.07          # taker fee rate, for the rebate estimate

    # LATENCY AND PROPAGATION PARAMETERS (issue #27, Phase 1 components 2 & 3).
    # Shared one-way network leg. MEASURED -- median(RTT)/2 = 7.85ms/2 (N=250)
    # from TCP connect probe to CLOB gateway.
    net_oneway_ms: float = 3.93

    # Venue-side legs. MEASURED / ESTIMATE.
    # post_venue_accept_ms: MEASURED -- mean of window medians from live CLOB probe
    # (N=90 across 3 windows: 85.00, 75.51, 82.89ms; mean 81.13ms, between-window SD 4.98ms).
    # Per-window draw, not a fixed venue property.
    cancel_venue_ack_ms: float = 150.0     # tau_cancel = 153.93ms total (estimate)
    post_venue_accept_ms: float = 81.0     # tau_post   = 84.93ms total (~1.8x lower than 150ms estimate) [MEASURED]


    @property
    def cancel_net_oneway_ms(self) -> float:
        """Backward compatibility alias for net_oneway_ms."""
        return self.net_oneway_ms

    @property
    def tau_cancel_sec(self) -> float:
        return (self.net_oneway_ms + self.cancel_venue_ack_ms) / 1000.0

    @property
    def tau_post_sec(self) -> float:
        return (self.net_oneway_ms + self.post_venue_accept_ms) / 1000.0

    sim_only: bool = True

    def db_path(self) -> Path:
        return Path(os.environ.get("SPREAD_HUNTER_DB") or os.environ.get("HUNTER_DB", str(ROOT / "hunter.db")))


    # --- pinned market (multi-bot mode) -----------------------------------
    # Empty = the original rolling 5-min BTC series. Set = quote this one
    # long-dated market instead. Long-dated markets are the ones that actually
    # fund resting liquidity (rewards.rates != null), and because they resolve
    # months out they barely move in an hour -- which is what makes resting
    # survivable. Adverse selection, not fee level, is what killed 5-min BTC.
    pinned_condition_id: str = ""
    market_title: str = ""
    market_url: str = ""
    market_daily_rate: float = 0.0


def _bounded_float(name: str, raw: str, lo: float, hi: float) -> float:
    """One env override, parsed and bounded, or a ValueError naming the variable.

    `float()` accepts "nan" and "inf" without complaint, and NaN poisons every
    comparison it touches: `cost >= float("nan")` is False for every cost, so a
    cap set to NaN reads as enforced while permitting a pair over $1.00 -- the
    one outcome this system exists to prevent. An out-of-range but finite value
    does the same damage more honestly (a $1.05 cap buys a booked loss on
    purpose), so both are refused here rather than found later in the fills
    ledger.

    Raising beats clamping. A silently clamped cap is a config the operator
    believes is in force and is not; a refused load is a message on the console
    before a share is bought.
    """
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{name}={raw!r} is not a number") from None
    if not math.isfinite(value):
        raise ValueError(f"{name}={raw!r} is not finite")
    if not lo <= value <= hi:
        raise ValueError(f"{name}={raw!r} is outside {lo:g}..{hi:g}")
    return value


def resolve_wide_book_trial(raw: str, *, for_display: bool = False) -> float | None:
    """Parse `HUNTER_WIDE_BOOK_TRIAL`, refusing outside a declared rehearsal.

    The gate is `rehearsal.is_rehearsal()`, and the choice of question matters.
    The obvious alternative -- "is a signing key reachable?" -- is wrong twice
    over. Too weak, because `.env` is the documented home for the key and
    `venue.signed_client` copies it into `os.environ` only when it builds a
    signer, which on the live path can happen after `load()` has run. Too
    strong, because `core_brain.shadow_run` cannot sign whatever sits in
    `.env`: it builds a credential-free client behind a deny-by-default proxy,
    so a key on disk says nothing about what that process can do. Gating on
    the key would refuse the one place this trial is safe while passing on a
    live trader whose key had not been read yet.

    So the question is whether this process can place an order, which is a
    property of the entrypoint. Once that is answered no, the key is
    irrelevant and is not consulted -- a second check there would be dead code
    wearing the costume of a safety layer.

    Refusing beats ignoring and beats clamping. An operator who sets this
    outside a rehearsal has asked for a widened SAFETY gate on the money path;
    dropping the override silently would leave them believing a trial is
    running when it is not. Raising stops the process at config load, before a
    client is built.

    The bound is 0.30. A binary market whose two sides are thirty cents apart
    is not a wide book, it is an absent one, and a value past that is a typo.

    `for_display` is for a read-only consumer -- the dashboard's KPI and
    parameter views -- that never places an order. Those processes inherit the
    operator's shell environment only because `Start-Process` copies it, so a
    trial knob exported to hand to a rehearsal also lands in the dashboard;
    raising there crashes the page over a knob it will never act on. With
    `for_display=True` the override is dropped with a logged warning and the
    shipped ceiling stands. The money path never passes it, so the refusal is
    unchanged where it matters.
    """
    if not raw.strip():
        return None
    if not rehearsal.is_rehearsal():
        if for_display:
            logging.getLogger(__name__).warning(
                "HUNTER_WIDE_BOOK_TRIAL ignored for a display-only config load "
                "(this process is not a rehearsal); shipped max_book_spread stands.")
            return None
        raise ValueError(
            "HUNTER_WIDE_BOOK_TRIAL widens max_book_spread, the gate that "
            "refuses a book where a fill may leave a leg that cannot be "
            "closed. It applies only in a process that has declared itself "
            "unable to place an order: core_brain.shadow_run, whose client "
            "cannot sign, or the ranker's --trial-spread, which only reads "
            "books and writes a market list.")
    return _bounded_float("HUNTER_WIDE_BOOK_TRIAL", raw, 0.0, 0.30)


def resolve_pair_cost_trial(raw: str, *, for_display: bool = False) -> float:
    """Parse `HUNTER_PAIR_COST_CAP`, refusing outside a declared rehearsal.

    Same gate and same reasoning as `resolve_wide_book_trial`: whether this
    process can place an order is a property of the entrypoint, so a knob that
    loosens a loss-prevention cap is refused unless the entrypoint has declared
    it cannot.

    Bounded at $1.00 and no higher, and that ceiling is not arbitrary. The
    instrument pays exactly $1.00, so a cap ABOVE it is not a looser risk
    setting -- it admits pairs that are a booked loss the moment they assemble.
    `HUNTER_COMPLETABLE_CAP` refuses the same value for the same reason. The
    comparison in `risk.hard_block` is `>=`, so a cap of exactly 1.00 still
    refuses a $1.0000 pair and permits everything under it.

    `for_display` behaves as in `resolve_wide_book_trial`: a read-only
    consumer that inherited the knob from the operator's shell gets the
    shipped `max_pair_cost` back with a logged warning instead of a crash.
    """
    if not rehearsal.is_rehearsal():
        if for_display:
            logging.getLogger(__name__).warning(
                "HUNTER_PAIR_COST_CAP ignored for a display-only config load "
                "(this process is not a rehearsal); shipped max_pair_cost stands.")
            return MakerConfig.max_pair_cost
        raise ValueError(
            "HUNTER_PAIR_COST_CAP loosens max_pair_cost, the rule that stops a "
            "pair assembling above the $1.00 the instrument pays. It applies "
            "only in a process that has declared itself unable to place an "
            "order -- core_brain.shadow_run, whose client cannot sign.")
    return _bounded_float("HUNTER_PAIR_COST_CAP", raw, 0.0, 1.0)


def load(*, for_display: bool = False) -> MakerConfig:
    """Config, with the per-bot fields overridable from the environment.

    Four bots run the same code against different markets; each gets its own
    HUNTER_DB, port and HUNTER_MARKET. Nothing else differs, so a difference in
    results is a difference in the MARKET, not in the settings.

    `for_display` is threaded to the two rehearsal-gated trial resolvers
    (`resolve_wide_book_trial`, `resolve_pair_cost_trial`). A read-only
    consumer -- the dashboard's `/api/kpi` and `/api/parameters` -- passes it
    so an inherited trial knob degrades to the shipped default with a warning
    instead of raising. Every order-placing entrypoint calls `load()` with no
    argument, so the refusal that protects the money path is untouched.
    """
    kw: dict = {}
    cid = os.environ.get("HUNTER_MARKET", "").strip()
    if cid:
        kw["pinned_condition_id"] = cid
        kw["market_title"] = os.environ.get("HUNTER_TITLE", "")
        kw["market_url"] = os.environ.get("HUNTER_URL", "")
        kw["market_daily_rate"] = float(os.environ.get("HUNTER_DAILY_RATE", "0") or 0)
        # Long-dated markets do not close in 15 seconds, and the min-size floor
        # is per-market (20, 50, 100, 200) -- quoting under it earns nothing.
        kw["min_t_remaining_sec"] = 0.0
        ms = os.environ.get("HUNTER_MIN_SIZE")
        if ms:
            kw["min_quote_shares"] = int(float(ms))
            kw["quote_shares"] = max(int(float(ms)), 120)
        sp = os.environ.get("HUNTER_MAX_SPREAD")   # in cents, from the venue
        if sp:
            kw["max_spread_from_mid"] = float(sp) / 100.0
        tk = os.environ.get("HUNTER_TICK")
        if tk:
            kw["price_tick"] = float(tk)
    wide = resolve_wide_book_trial(
        os.environ.get("HUNTER_WIDE_BOOK_TRIAL") or "", for_display=for_display)
    if wide is not None and wide > 0:
        # All three ceilings move together. The reward window is widened only
        # if the trial is wider than it already is -- a trial NARROWER than
        # 4.5c must not quietly tighten where we may quote.
        kw["wide_book_trial"] = wide
        kw["max_book_spread"] = wide
        kw["select_max_book_spread"] = wide
        # Read whatever the per-bot `HUNTER_MAX_SPREAD` block above may have
        # set, not the class default, so a venue-supplied window is widened
        # rather than replaced.
        window = kw.get("max_spread_from_mid", MakerConfig.max_spread_from_mid)
        kw["max_spread_from_mid"] = max(window, wide)
    trial = os.environ.get("HUNTER_DEPTH_TRIAL_USD") or ""
    if trial.strip():
        kw["select_min_top3_depth_usd_trial"] = float(trial)
    vtri = os.environ.get("HUNTER_VOLUME_TRIAL_USD") or ""
    if vtri.strip():
        kw["select_min_volume_24h_usd_trial"] = float(vtri)
    pr = os.environ.get("HUNTER_PAIRS_RULE") or ""
    if pr.strip():
        kw["enable_pairs_rule"] = pr.strip().lower() not in ("0", "false", "off")
    eg = os.environ.get("HUNTER_ENDGAME_GATE") or ""
    if eg.strip():
        kw["enforce_endgame_gate"] = eg.strip().lower() not in ("0", "false", "off")
    egh = os.environ.get("HUNTER_ENDGAME_HORIZON_MIN") or ""
    if egh.strip():
        # Minutes before resolution the rule starts to apply. The ceiling is
        # a day: past that the "endgame" is the whole market.
        kw["endgame_horizon_min"] = _bounded_float(
            "HUNTER_ENDGAME_HORIZON_MIN", egh, 0.0, 1440.0)
    egc = os.environ.get("HUNTER_ENDGAME_MAX_CADENCE_SEC") or ""
    if egc.strip():
        # The convergence budget in seconds. The ceiling is an hour: a loop
        # slower than that is not a loop.
        kw["endgame_max_cadence_sec"] = _bounded_float(
            "HUNTER_ENDGAME_MAX_CADENCE_SEC", egc, 0.0, 3600.0)
    ega = os.environ.get("HUNTER_ENDGAME_ACTION") or ""
    if ega.strip():
        action = ega.strip().lower()
        if action not in ("refuse", "deepen"):
            raise ValueError(
                f"HUNTER_ENDGAME_ACTION={ega!r} must be 'refuse' or 'deepen'")
        kw["endgame_action"] = action
    egd = os.environ.get("HUNTER_ENDGAME_DEEPEN_OFFSET") or ""
    if egd.strip():
        # Extra offset in price units, applied through the existing offset
        # pipeline and its clamp. The ceiling is the instrument's whole
        # price range, as for the dead band.
        kw["endgame_deepen_offset"] = _bounded_float(
            "HUNTER_ENDGAME_DEEPEN_OFFSET", egd, 0.0, 1.0)
    pcap = os.environ.get("HUNTER_PAIR_COST_CAP") or ""
    if pcap.strip():
        # PAIR-COST TRIAL (#145). `max_pair_cost = 0.99` is compared with `>=`
        # in `risk.hard_block`, and run 145 measured the touch pair at exactly
        # $0.99 on every 1c-tick book -- so the cap refuses the ONLY profitable
        # configuration those books offer. On a one-tick spread a maker-only
        # pair earns exactly one cent per share, taker completion breaks even
        # at $1.0000, and crossing both legs loses; $0.99 is the whole trade.
        #
        # Whether that cent survives costs is now measured rather than
        # assumed: a merge costs $0.01138 (median of 12 on-chain
        # `mergePositions` transactions), which is a per-TRANSACTION cost
        # amortised over the shares merged. At the shipped `quote_shares = 120`
        # it is 0.9% of gross edge. So the cap is refusing a trade that clears
        # its costs by two orders of magnitude at full size, and this knob
        # exists to rehearse whether both legs ever fill.
        #
        # Rehearsal-only, on the same gate as the wide-book trial: this is a
        # loss-prevention cap on the money path, not a preference.
        kw["max_pair_cost"] = resolve_pair_cost_trial(pcap, for_display=for_display)
    ccap = os.environ.get("HUNTER_COMPLETABLE_CAP") or ""
    if ccap.strip():
        # 0 disables the cap, the escape hatch every other limit here has.
        # Above $1.00 is refused outright: the pair pays exactly $1.00, so a
        # higher cap is not a looser risk setting, it is a losing one.
        kw["max_completable_pair_cost"] = _bounded_float(
            "HUNTER_COMPLETABLE_CAP", ccap, 0.0, 1.0)
    rdb = os.environ.get("HUNTER_REQUOTE_DEAD_BAND") or ""
    if rdb.strip():
        # 0 turns the hysteresis off. The ceiling is the instrument's whole
        # price range: a band of 1.00 already means "never re-quote", and
        # anything past it is a typo, not a setting.
        kw["requote_dead_band"] = _bounded_float(
            "HUNTER_REQUOTE_DEAD_BAND", rdb, 0.0, 1.0)
    rhb = os.environ.get("HUNTER_REQUOTE_HOLD_BELOW") or ""
    if rhb.strip():
        # 0 turns the direction hold off and restores the symmetric re-quote,
        # which is how the change gets attributed a result on its own. The
        # ceiling is the instrument's whole price range, as for the dead band.
        kw["requote_hold_below_target"] = _bounded_float(
            "HUNTER_REQUOTE_HOLD_BELOW", rhb, 0.0, 1.0)
    rwo = os.environ.get("HUNTER_REWARD_OFFSET") or ""
    if rwo.strip():
        # How far below mid to rest, for ONE run, without moving the shipped
        # default. Measured over 24h on the recorded slate: every two-sided
        # token quotes a 10-tick spread, so the best bid is `mid - 5 ticks`,
        # and 35% of all below-mid volume prints at exactly that price while
        # almost none prints past it -- 98% and 100% of the two MLB markets'
        # below-mid volume was at the touch with nothing beyond. Resting at
        # 0.005 reaches 18.0% of traded volume against 1.4% at the shipped
        # 0.020, and the only way to learn whether that becomes fills is to
        # rehearse it. `min_reward_offset` still floors the result in
        # `quotes.quote_resting_price`; this cannot quote nearer than that.
        kw["reward_offset"] = _bounded_float(
            "HUNTER_REWARD_OFFSET", rwo, 0.0, 1.0)
    prw = os.environ.get("HUNTER_PRICE_RISK_WIDEN") or ""
    if prw.strip():
        # A RISK CONTROL, overridable only because it is also the term that
        # decides whether a maker can reach the touch at all: it widens by
        # `price_risk_widen * (w + price)`, about 15 ticks near 0.50, against a
        # book spread measured at 10 ticks on every two-sided token in the
        # slate. `reward_offset` alone therefore cannot rest at the best bid,
        # and whether tape at the touch becomes fills is only answerable by
        # resting there. Scoped to a shadow rehearsal, which cannot sign.
        # Zeroing this leaves the coin-flip SIZE cut untouched -- that is
        # `coinflip_size_cut`, a separate term with its own knob.
        kw["price_risk_widen"] = _bounded_float(
            "HUNTER_PRICE_RISK_WIDEN", prw, 0.0, 1.0)
    cno = os.environ.get("HUNTER_NET_ONEWAY_MS") or os.environ.get("HUNTER_CANCEL_NET_ONEWAY_MS")
    if cno and cno.strip():
        kw["net_oneway_ms"] = float(cno)
    cva = os.environ.get("HUNTER_CANCEL_VENUE_ACK_MS")
    if cva and cva.strip():
        kw["cancel_venue_ack_ms"] = float(cva)
    pva = os.environ.get("HUNTER_POST_VENUE_ACCEPT_MS")
    if pva and pva.strip():
        kw["post_venue_accept_ms"] = float(pva)
    mf = os.environ.get("HUNTER_MARGINAL_FLOOR") or ""
    if mf.strip():
        kw["marginal_return_floor"] = float(mf)
    bk = os.environ.get("SPREAD_HUNTER_BANKROLL") or os.environ.get("HUNTER_BANKROLL") or ""
    if bk.strip():
        val = float(bk)
        if not math.isfinite(val) or val <= 0:
            raise ValueError(f"Bankroll override must be finite and strictly positive, got: {val}")
        kw["bankroll_usd"] = val
        kw["allocation_budget"] = val * 0.9
        kw["max_committed_usd"] = val
    sbg = os.environ.get("HUNTER_SINGLE_BUY_GRACE_SEC")
    if sbg and sbg.strip():
        val = float(sbg)
        if not math.isfinite(val) or val < 0:
            raise ValueError(f"HUNTER_SINGLE_BUY_GRACE_SEC must be finite and non-negative, got: {val}")
        kw["single_buy_grace_sec"] = val
    sblp = os.environ.get("HUNTER_SINGLE_BUY_MAX_LOSS_PCT")
    if sblp and sblp.strip():
        val = float(sblp)
        if not math.isfinite(val) or not (0.0 <= val <= 1.0):
            raise ValueError(f"HUNTER_SINGLE_BUY_MAX_LOSS_PCT must be between 0.0 and 1.0, got: {val}")
        kw["single_buy_max_loss_pct"] = val
    sblu = os.environ.get("HUNTER_SINGLE_BUY_MAX_LOSS_USD")
    if sblu and sblu.strip():
        val = float(sblu)
        if not math.isfinite(val) or val < 0:
            raise ValueError(f"HUNTER_SINGLE_BUY_MAX_LOSS_USD must be finite and non-negative, got: {val}")
        kw["single_buy_max_loss_usd"] = val
    mqs = os.environ.get("HUNTER_MAX_SHARES") or os.environ.get("SPREAD_HUNTER_MAX_SHARES")
    if mqs and mqs.strip():
        val = float(mqs)
        if not math.isfinite(val) or val <= 0:
            raise ValueError(f"HUNTER_MAX_SHARES must be strictly positive, got: {val}")
        kw["max_quote_shares"] = val
    sgt = os.environ.get("HUNTER_STAT_GATE_THRESHOLD_PCT")
    if sgt and sgt.strip():
        kw["stat_gate_threshold_pct"] = _bounded_float(
            "HUNTER_STAT_GATE_THRESHOLD_PCT", sgt, -100.0, 100.0
        )
    sgb = os.environ.get("HUNTER_STAT_GATE_BANKROLL_FRACTION")
    if sgb and sgb.strip():
        kw["stat_gate_bankroll_fraction"] = _bounded_float(
            "HUNTER_STAT_GATE_BANKROLL_FRACTION", sgb, 0.0, 1.0
        )
    stc = os.environ.get("HUNTER_STAT_GATE_TARGET_CLOSES")
    if stc and stc.strip():
        try:
            val = int(stc)
            if val <= 0:
                raise ValueError
        except ValueError as exc:
            raise ValueError(
                f"HUNTER_STAT_GATE_TARGET_CLOSES must be a positive integer, got: {stc!r}"
            ) from exc
        kw["stat_gate_target_closes"] = val
    return MakerConfig(**kw)

# hook probe
# hook probe


def couple_allocation_usd(cfg) -> float:
    """Dollars committed to one UP+DOWN couple, per the Owner's rule."""
    return max(cfg.bankroll_usd * cfg.couple_risk_frac, cfg.min_couple_usd)


def leg_allocation_usd(cfg) -> float:
    """Dollars for one side of a couple. Half the couple, always."""
    return couple_allocation_usd(cfg) / 2.0


# One share at the top of the price range: the venue refuses anything under
# `single_buy_saver.MIN_ORDER_SHARES` (1.0 share), so a dollar cap below this
# cannot fund a single legal order at any price. Kept as a literal rather than
# an import so `config` stays free of execution-path imports.
VENUE_MIN_NOTIONAL_USD: float = 1.0


def derive_dynamic_caps(cfg: MakerConfig, portfolio_usd: float | None = None) -> dict[str, float]:
    """Derive dynamic risk caps scaled to the account's total portfolio value.

    - max_naked_usd = 6% (naked_risk_pct) of portfolio value
    - max_order_usd = 25% (order_risk_pct) of portfolio value
    - max_total_usd = 90% (bankroll_ceiling_pct) of portfolio value

    `max_order_usd` is floored at the venue minimum notional
    (`VENUE_MIN_NOTIONAL_USD`): a percentage cap below one share's cost cannot
    fund a legal order at all, so the bot would rest nothing while believing it
    was sized. The floor never exceeds the total cap -- a cap that outruns the
    portfolio ceiling would be the ceiling in name only.

    If portfolio_usd is None, non-finite, or <= 0, falls back to cfg.bankroll_usd.
    """
    base_val = None
    if portfolio_usd is not None:
        try:
            val = float(portfolio_usd)
            if math.isfinite(val) and val > 0:
                base_val = val
        except (TypeError, ValueError):
            pass

    if base_val is None:
        try:
            val = float(getattr(cfg, "bankroll_usd", 100.0))
            base_val = val if (math.isfinite(val) and val > 0) else 100.0
        except (TypeError, ValueError):
            base_val = 100.0

    def _sanitize_pct(val, default_pct: float) -> float:
        try:
            fval = float(val)
            if math.isfinite(fval) and 0.0 < fval <= 1.0:
                return fval
        except (TypeError, ValueError):
            pass
        return default_pct

    naked_pct = _sanitize_pct(getattr(cfg, "naked_risk_pct", 0.06), 0.06)
    order_pct = _sanitize_pct(getattr(cfg, "order_risk_pct", 0.25), 0.25)
    total_pct = _sanitize_pct(getattr(cfg, "bankroll_ceiling_pct", 0.90), 0.90)

    raw_naked = base_val * naked_pct
    raw_order = base_val * order_pct
    raw_total = base_val * total_pct

    # Retain positive unrounded value when rounding would collapse to zero sentinel (0 disables risk blocks)
    naked_val = round(raw_naked, 2) if raw_naked >= 0.005 else raw_naked
    order_val = round(raw_order, 2) if raw_order >= 0.005 else raw_order
    total_val = round(raw_total, 2) if raw_total >= 0.005 else raw_total

    # Venue-minimum floor, applied after rounding so the published cap is the
    # one the guards compare against. Bounded by the total cap: on an account
    # too small to fund one share, the ceiling still wins and the order is
    # refused rather than sized past the portfolio limit.
    floor = min(VENUE_MIN_NOTIONAL_USD, total_val)
    order_val = max(order_val, floor)

    return {
        "bankroll_usd": round(base_val, 2) if base_val >= 0.005 else base_val,
        "max_naked_usd": naked_val,
        "max_order_usd": order_val,
        "max_total_usd": total_val,
    }

