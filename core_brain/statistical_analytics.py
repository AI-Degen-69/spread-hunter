"""The statistics surface the analytics charts read, built from the registry.

The Performance & Analytics tab has five charts â€” position-return distribution,
pair-cost density, outcome-probability bell, post-fill markout, Monte Carlo â€”
and every one of them reads `kpi.statistical_analytics`. Nothing ever produced
that key: not `kpi.report`, not the TS bridge, at any commit in this repo's
history. So the charts have shown "unmeasured" since the day they were written,
including on runs that closed positions at a profit.

The payload also carries one section that is not a chart. `fill_path_edge`
splits the same closes into the merge path and the rescue path, because a
healthy merge edge and a rescue bleed cancel out inside one total and the
operator cannot tell which is happening. It summarises closes rather than
drawing them, and it discloses the closes that fall outside both paths instead
of folding them in.

The omission rule below governs the charts, not that summary. A chart with no
input draws nothing honest, so it is left out. `fill_path_edge` is always
present, reporting zeroes and `None` for a run with no closes, because it is a
summary a caller reads rather than a distribution a chart plots -- a caller must
never have to branch on whether the key is there. Its zeroes say "no closes",
never "no such measurement".

The same rule governs the rescue friction section (#514). `fill_path_edge` says
what a rescue cost in dollars; `rescue_friction_summary` says how much of that a
bailout lost to friction rather than to the price move the stop was protecting.
It reads the three prices the rescue writer already books -- the bid that fired
the stop, the fresh bid the exit priced off, and the price the venue gave -- and
every figure it cannot read off them is `None` with the reason attached, because
a zero there would read as "this stop cost nothing" rather than "nobody
recorded it".

The third section is not read off closes at all. `queue realism` (#515) reads the tape
that credited a fill, which is the only other evidence the fill model ever sees. The
trade-through rule credits a resting order only when a print lands strictly below its
price, so a credited fill is proof that the level cleared -- and the two numbers worth
having from that proof are how far below our price the tape had to go (cents, with the
ticket's traffic-light bands) and how long the quote waited for it (seconds, descriptive
only: a wait has no market reference to be judged against, so a good/bad label on it
would be invented). The touch-ratio, drain-ratio and depth-correlation metrics that were
planned alongside them are gone on purpose, not deferred: under this fill rule they
measure a tautology or an unverifiable guess, and `queue_ahead` stays descriptive
telemetry that gates nothing.

The fourth section also is not a chart, and it asks the question a profitable-looking run
raises: was that the tape or the strategy. `robustness` (#516) reads the markout rows and the
closes side by side and reports four readings -- the signed post-fill displacement as a median
across fills, in cents, with the ticket's bands; the maximum adverse excursion over a holding
window, descriptive and deliberately unbanded; the 95% band on mean close PnL; and the system
quality number. The last two look like the same question from different angles, and they are
not merged into one verdict: a run can have a proven positive mean on too few trades to call
the edge stable, and a single combined word would hide which half said so.

The fifth section is the same question at the level of the capital. `capital_risk` (#517) reads the
closes as percentages for two banded readings -- the skewness of the per-close returns, which says
whether the swings come from upside surges or from downside tails, and a Sortino against an explicit
risk-free rate, which says whether the return is worth the downside pain -- and reads a
caller-supplied equity curve for the third: how deep the worst drawdown ran and how long the
drawdowns that came back took to do it, in curve points and in seconds. That one carries no band on
purpose. A duration has nothing to be judged against until it can be compared with how fast this
strategy normally compounds, and a chip on a guess is a confident lie. The confidence-interval
helper and the system quality number above are the ones this section's card links to, reused rather
than copied, so the page holds one definition of each.

This builds the payload, and it builds it only from what the registry actually
holds. The rule throughout: **a section with no input is omitted, not
synthesised.** A chart saying "unmeasured" is information; a chart drawing a
smooth curve through one observation is a lie that looks like analysis.

Two consequences of that rule worth stating plainly:

* Monte Carlo needs a distribution to resample. Below `MIN_MC_SAMPLES` closes
  there is no distribution, only a number, and the fan it would draw would be
  an artefact of the bootstrap rather than a property of the strategy. It stays
  absent until the sample exists.
* Markout comes from matured horizons. An unmatured fill has no displacement
  yet, and counting it as 0 bps would report "no adverse selection" for a
  measurement that has not happened.
"""
from __future__ import annotations

import bisect
import math
import random
import statistics
from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence

from core_brain.close_methods import (
    BOOKKEEPING_METHODS as EXCLUDED_METHODS,
    MERGE_METHODS,
    RESCUE_METHODS,
)

# Merge methods, rescue exits, and bookkeeping rows are single-sourced from the
# canonical leaf (core_brain/close_methods.py), imported above as MERGE_METHODS
# / RESCUE_METHODS / EXCLUDED_METHODS. They were local copies only because
# importing them from `kpi` was a cycle (`kpi` imports this module); the leaf
# dissolves that. A merged pair is the strategy's own exit; a rescue is a leg
# sold back through the book; bookkeeping rows sold nothing and lost no position.

# Zero-loss guard for the ratio denominators, mirroring `kpi.pnl_by_fill_path`. A booked
# loss a hair away from zero has no share to divide into, and the float noise around a
# break-even close must not read as a denominator.
_LOSS_EPSILON = 1e-9

# The traffic-light bands `rescue_friction_summary` reports for its means, in the order a
# reader meets them. The band is a function of the value, never a static label.
FRICTION_BAND_METRICS = ("sled", "latency", "execution", "tcdr", "overshoot")

# The ticket writes the drag bands in price dollars -- 0.02 is about one book spread, 0.05
# the adverse-drift trigger the fleet already acts on -- while the drags are displayed in
# cents, so the same edges read 2.0 and 5.0 here.
SPREAD_CENTS = 2.0
DRIFT_CENTS = 5.0
TCDR_BAD_AT_OR_ABOVE = 0.10
OVERSHOOT_BAD_ABOVE = 1.0

# The assumed stop reference for a rescue close that recorded no `trigger_bid`. It is an
# ASSUMPTION, not an observed price: it equals the system default in `core_brain/config.py`
# (`stop_loss_trigger_usd_cents = 4.0` = $0.04, promoted 2026-10-12), a per-share loss
# limit that profile presets move (3.0-6.5 cents). Every figure derived from it is tagged
# `assumed_offset` and counted apart from the recorded ones.
STOP_OFFSET_FALLBACK = 0.04

# The venue pays exactly $1.00 for a merged pair, so pair cost lives just under
# it. These bounds frame the density chart around the only region that matters.
PAIR_COST_FLOOR = 0.90
PAIR_COST_CEILING = 1.00
PAIR_COST_BINS = 12

# The quoting band the strategy targets. `renderProbabilityBellChart` shades it.
SWEET_SPOT_LOW = 0.10
SWEET_SPOT_HIGH = 0.90
BELL_BINS = 18

# Below this many closed positions a bootstrap is resampling noise. The chart
# is left unmeasured instead.
MIN_MC_SAMPLES = 5
MC_PATHS = 1000
MC_CYCLES = 100

# The horizons `core_brain.markout` samples, in the order the chart reads them.
MARKOUT_HORIZONS = (("mid_h0", "5m"), ("mid_h3", "15m"),
                    ("mid_h1", "1h"), ("mid_h2", "6h"))


def _finite(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def position_returns(closes: Sequence[dict]) -> dict[str, Any]:
    """Per-closed-position return, in dollars and in percent of cost.

    `pnl_pct` is measured against the position's own cost basis, so a 5c edge on
    a $0.95 pair reads as ~5.3% rather than being diluted by bankroll size. A
    close with no cost basis contributes its dollar figure and no percentage --
    dividing by zero to fill a column would put a fabricated number in the
    distribution the operator judges the strategy on.

    `spread_cost` is the per-share assembly price of a merged pair, the same
    population and the same division `pair_costs` densifies: an unwind never
    assembled a pair at a price, so it carries none (#538).
    """
    positions: list[dict[str, Any]] = []
    for row in closes or []:
        pnl_usd = _finite(row.get("realized_pnl"))
        if pnl_usd is None:
            continue
        cost = _finite(row.get("cost_basis"))
        pnl_pct = (100.0 * pnl_usd / cost) if cost and cost > 0 else None
        method = str(row.get("method") or "").lower()
        shares = _finite(row.get("shares"))
        spread_cost = (cost / shares
                       if method in MERGE_METHODS and cost and cost > 0
                       and shares and shares > 0 else None)
        positions.append({
            "ts": _finite(row.get("ts")),
            "condition_id": row.get("condition_id"),
            "market_slug": row.get("market_slug"),
            "pnl_usd": round(pnl_usd, 6),
            "pnl_pct": None if pnl_pct is None else round(pnl_pct, 6),
            "spread_cost": None if spread_cost is None else round(spread_cost, 6),
            "type": "MERGED_PAIR" if method in MERGE_METHODS else "UNWIND",
            "method": method or None,
        })

    out: dict[str, Any] = {"positions": positions}
    usd = [p["pnl_usd"] for p in positions if p["pnl_usd"] is not None]
    pct = [p["pnl_pct"] for p in positions if p["pnl_pct"] is not None]
    for name, sample in (("usd", usd), ("pct", pct)):
        if not sample:
            continue
        mean = statistics.fmean(sample)
        # A single observation has a mean and no spread. Reporting 0.0 for the
        # deviation would read as "perfectly consistent" on a sample of one.
        stdev = statistics.stdev(sample) if len(sample) > 1 else None
        out[f"mean_pnl_{name}"] = round(mean, 6)
        out[f"stdev_pnl_{name}"] = None if stdev is None else round(stdev, 6)
        out[f"sem_pnl_{name}"] = (None if stdev is None
                                  else round(stdev / math.sqrt(len(sample)), 6))
        # The multiplier for THIS sample, shipped beside its standard error, so the renderer
        # multiplies a number the server decided. The two samples can differ in length -- a
        # close with no cost basis carries a dollar figure and no percent -- so each names its
        # own. None on a sample of one, which has no spread to scale (#535).
        out[f"ci_critical_{name}"] = (None if stdev is None
                                      else ci_critical_values(len(sample)))
    return out


def pair_costs(closes: Sequence[dict]) -> dict[str, Any]:
    """What each merged pair actually cost to assemble, as a density.

    Only merges: an unwind did not assemble a pair at a price, so including it
    would blur the one number this chart exists to show. `status` marks the bin
    against the $1.00 the instrument pays -- at or above it, the pair was a
    booked loss.
    """
    costs: list[float] = []
    for row in closes or []:
        if str(row.get("method") or "").lower() not in MERGE_METHODS:
            continue
        cost = _finite(row.get("cost_basis"))
        shares = _finite(row.get("shares"))
        if cost is None or not shares or shares <= 0:
            continue
        costs.append(cost / shares)

    if not costs:
        return {"bins": [], "samples_count": 0}

    width = (PAIR_COST_CEILING - PAIR_COST_FLOOR) / PAIR_COST_BINS
    bins = []
    for i in range(PAIR_COST_BINS):
        low = PAIR_COST_FLOOR + i * width
        high = low + width
        count = sum(1 for c in costs if low <= c < high)
        bins.append({
            "min": round(low, 4),
            "max": round(high, 4),
            "label": f"${low:.3f}",
            "count": count,
            "density": round(count / len(costs), 6),
            "status": "profit",
        })

    # THE OVERFLOW BIN. A pair assembled at or above $1.00 is a booked loss --
    # the instrument pays exactly a dollar -- and it is the single outcome this
    # whole strategy exists to prevent. Without a bin to land in it would fall
    # off the end of the chart: counted in the mean, invisible in the density,
    # which is the one place an operator would look for it.
    over = sum(1 for c in costs if c >= PAIR_COST_CEILING)
    bins.append({
        "min": round(PAIR_COST_CEILING, 4),
        "max": None,
        "label": f"${PAIR_COST_CEILING:.2f}+",
        "count": over,
        "density": round(over / len(costs), 6),
        "status": "loss",
    })

    return {
        "bins": bins,
        "samples_count": len(costs),
        # The chart's own standard error is `stdev / sqrt(samples_count)`, computed in the
        # renderer; the multiplier that scales it is decided here and shipped, and is None on
        # exactly the samples that have no `stdev` for it to scale (#535).
        "ci_critical": ci_critical_values(len(costs)) if len(costs) > 1 else None,
        "mean": round(statistics.fmean(costs), 6),
        "median": round(statistics.median(costs), 6),
        "stdev": round(statistics.stdev(costs), 6) if len(costs) > 1 else None,
        "min_observed": round(min(costs), 6),
    }


def _close_field(row: Any, name: str) -> Any:
    """Read one field off a close dict or a `CloseRecord`.

    `kpi.report` hands this module the registry's plain dicts; a caller may be
    holding the records instead. Both shapes measure identically.
    """
    if isinstance(row, dict):
        return row.get(name)
    return getattr(row, name, None)


def _fill_path(method: Any) -> str:
    """Which path a close's `method` belongs to: merge, rescue, excluded, other.

    `method` is a free-form recorded string, so this mapping is the load-bearing
    part of the section. The buckets are an accounting proxy, not literal
    `PAIRED_MERGE` / `RESCUE_STOP` execution labels: a merge close can follow a
    taker completion, and a leg held to market resolution is neither path.
    """
    method = str(method or "").strip().lower()
    if method in MERGE_METHODS:
        return "merge"
    if method in RESCUE_METHODS:
        return "rescue"
    if method in EXCLUDED_METHODS:
        return "excluded"
    return "other"


def break_even_rescue_rate(merge_mean: Optional[float],
                           rescue_mean: Optional[float],
                           ) -> tuple[Optional[float], str]:
    """The rescue share at which the two paths net to exactly zero.

    Solving `(1 - r) * merge + r * rescue == 0` gives `merge / (merge - rescue)`,
    so a run whose rescue rate sits above this number is losing on the two paths
    even while its merges are profitable. The result is always below 1.0: a run
    where every resolution is a rescue can never break even.

    No stop-offset constant is involved, deliberately. `hard_stop_bid` is an
    absolute bid floor and `stop_loss_trigger_usd_cents` is a per-profile per-share
    limit, so a constant here would drift away from execution; the realized
    close dollars already contain the exit that actually filled.

    `None` back with a reason when the question has no answer: a path with no
    closes, a merge mean at or below zero (nothing to lose), or a rescue mean at
    or above zero (nothing to lose to).
    """
    if merge_mean is None or rescue_mean is None:
        return None, "unmeasured"
    if merge_mean <= 0:
        return None, "merge_not_profitable"
    if rescue_mean >= 0:
        return None, "rescue_not_losing"
    return merge_mean / (merge_mean - rescue_mean), "measured"


# The rescue-rate headroom zone: inside this share of the run's own break-even
# bar there is little room left before the two paths net negative, so the rate
# reads average while still under the bar.
RESCUE_RATE_HEADROOM = 0.8

# Break-even bar bands, on how large a rescue share the merge edge absorbs
# before the run nets negative: a high bar is healthy, a low one means even a
# few rescues sink it.
BREAK_EVEN_GOOD_AT_OR_ABOVE = 0.5
BREAK_EVEN_AVERAGE_AT_OR_ABOVE = 0.25


def fill_path_bands(edge: dict[str, Any]) -> dict[str, str]:
    """The traffic-light bands for the fill-path edge readout (#513).

    Same contract as `friction_band`: the thresholds live here and reach the
    dashboard as data, so the renderer maps a band name to a colour and holds
    no threshold of its own. A band nobody could judge reads `unmeasured`,
    never `bad` -- a missing bar is not a failing run.

    The dollar means band on their sign: the merge path and the net are meant
    to profit, and zero expectancy there reads average because an edge of
    exactly zero has no edge. The rescue path is meant to lose money, so a
    rescue mean at or above zero is the good side of that metric.

    The rescue rate bands against the run's OWN break-even bar, never against
    its size: a 25% rescue rate is comfortable when the merge edge absorbs 60%
    of closes and fatal when it absorbs 15%. At or above the bar the run nets
    negative on the two paths even while its merges profit, which is bad; the
    headroom zone below the bar reads average. The bar itself bands on how
    much rescue share the edge absorbs.
    """
    merge = _finite(edge.get("merge_expectancy_usd"))
    rescue = _finite(edge.get("rescue_expectancy_usd"))
    net = _finite(edge.get("net_expectancy_usd"))
    rate = _finite(edge.get("rescue_rate"))
    bar = _finite(edge.get("break_even_rescue_rate"))

    def sign_band(value: Optional[float], at_zero: str) -> str:
        if value is None:
            return "unmeasured"
        if value > 0:
            return "good"
        if value < 0:
            return "bad"
        return at_zero

    if rate is None or bar is None:
        rate_band = "unmeasured"
    elif rate >= bar:
        rate_band = "bad"
    elif rate >= RESCUE_RATE_HEADROOM * bar:
        rate_band = "average"
    else:
        rate_band = "good"

    if bar is None:
        bar_band = "unmeasured"
    elif bar >= BREAK_EVEN_GOOD_AT_OR_ABOVE:
        bar_band = "good"
    elif bar >= BREAK_EVEN_AVERAGE_AT_OR_ABOVE:
        bar_band = "average"
    else:
        bar_band = "bad"

    return {
        "merge_expectancy": sign_band(merge, "average"),
        "rescue_expectancy": sign_band(rescue, "good"),
        "rescue_rate": rate_band,
        "break_even_rescue_rate": bar_band,
        "net_expectancy": sign_band(net, "average"),
    }


def fill_path_edge(closes: Sequence[Any]) -> dict[str, Any]:
    """Merge expectancy against rescue expectancy, in dollars per close.

    `closes` is loosely typed on purpose: `kpi.report` passes the registry's
    close dicts, while a test or a repair script may hold `CloseRecord` objects,
    and `_close_field` reads whichever shape it gets. Naming the registry type
    here would buy nothing at runtime and would make this module import the
    registry. Both shapes measure identically.

    One close record is one equal observation. `other` (a leg held to
    settlement, a generic sell) and `excluded` (bookkeeping rows) are counted
    and disclosed, but enter no mean, no sum and no denominator -- only the two
    paths the strategy actually chose between. A row with no usable
    `realized_pnl` is skipped and counted, so one malformed record cannot drag a
    mean toward zero while looking like a measurement. A row that would push its
    aggregate past the float ceiling is skipped the same way, which keeps every
    figure here finite for `json.dumps(..., allow_nan=False)`.
    """
    by_path: dict[str, list[float]] = {"merge": [], "rescue": [], "other": []}
    totals = {"merge": 0.0, "rescue": 0.0, "other": 0.0}
    excluded = skipped = 0
    for row in closes or []:
        bucket = _fill_path(_close_field(row, "method"))
        if bucket == "excluded":
            excluded += 1
            continue
        pnl = _finite(_close_field(row, "realized_pnl"))
        if pnl is None:
            skipped += 1
            continue
        # Finite rows can still overflow the running total -- two closes of
        # 1e308 sum to `inf`. Such a row is skipped like a non-finite one: an
        # overflowed total is not a measurement, and `inf` in the payload would
        # break the strict-JSON promise this module makes to its callers. The
        # guard is per aggregate, because that is where the overflow happens.
        total = totals[bucket] + pnl
        if not math.isfinite(total):
            skipped += 1
            continue
        totals[bucket] = total
        by_path[bucket].append(pnl)

    merge_count, rescue_count = len(by_path["merge"]), len(by_path["rescue"])
    merge_pnl = totals["merge"]
    rescue_pnl = totals["rescue"]
    resolved = merge_count + rescue_count
    merge_mean = (merge_pnl / merge_count) if merge_count else None
    rescue_mean = (rescue_pnl / rescue_count) if rescue_count else None
    # From the raw means: rounding first and then dividing would put a rounded
    # figure into the very threshold the operator reads as the bar.
    break_even, status = break_even_rescue_rate(merge_mean, rescue_mean)

    def shape(value: Optional[float]) -> Optional[float]:
        return None if value is None else round(value, 6)

    payload = {
        "merge_count": merge_count,
        "merge_pnl_usd": round(merge_pnl, 6),
        "merge_expectancy_usd": shape(merge_mean),
        "rescue_count": rescue_count,
        "rescue_pnl_usd": round(rescue_pnl, 6),
        "rescue_expectancy_usd": shape(rescue_mean),
        "rescue_rate": shape(rescue_count / resolved) if resolved else None,
        "net_expectancy_usd": (shape((merge_pnl + rescue_pnl) / resolved)
                               if resolved else None),
        "break_even_rescue_rate": shape(break_even),
        "break_even_status": status,
        "other_count": len(by_path["other"]),
        "other_pnl_usd": round(totals["other"], 6),
        "excluded_count": excluded,
        "skipped_count": skipped,
        "units": "usd_per_close",
    }
    # The traffic-light bands ride along with the figures they judge, computed
    # from the rounded payload so the readout and the chip can never disagree.
    payload["bands"] = fill_path_bands(payload)
    return payload


def _entry_price(close: Any) -> Optional[float]:
    """Cost per share of the leg that was sold -- what the stop was protecting."""
    cost = _finite(_close_field(close, "cost_basis"))
    shares = _finite(_close_field(close, "shares"))
    if cost is None or shares is None or shares <= 0:
        return None
    return cost / shares


def _exit_price(close: Any) -> Optional[float]:
    """What the venue gave per share, in the price chain's own order.

    The recorded leg price first: a rescue sells exactly one leg, and
    `single_buy_saver._record_exit_close` writes the price the venue reported as
    `dn_price` or `up_price` depending on the side sold. When no leg price was written,
    `proceeds / shares` is the same number by construction.
    """
    dn = _finite(_close_field(close, "dn_price"))
    up = _finite(_close_field(close, "up_price"))
    leg = dn if dn is not None else up
    if leg is not None:
        return leg
    proceeds = _finite(_close_field(close, "proceeds"))
    shares = _finite(_close_field(close, "shares"))
    if proceeds is None or shares is None or shares <= 0:
        return None
    return proceeds / shares


def _stop_reference(close: Any, entry: Optional[float],
                    ) -> tuple[Optional[float], Optional[str]]:
    """The bid the stop intended to exit at, and where that bid came from.

    `trigger_bid` is the recorded bid that fired the stop. The lifecycle hard
    stop writes it; the settlement-fallback exit leaves it null, so the
    remaining rescue routes fall back to `entry - STOP_OFFSET_FALLBACK`,
    which is an assumption and is tagged as one.

    `fresh_bid` is never the reference. It is the bid the exit priced off, and measuring
    the exit against it would hide the gap between the mark that fired the stop and the
    live book -- which is one of the two things this section exists to measure.
    """
    trigger = _finite(_close_field(close, "trigger_bid"))
    if trigger is not None:
        return trigger, "recorded"
    if entry is not None:
        return entry - STOP_OFFSET_FALLBACK, "assumed_offset"
    return None, None


def rescue_drag(close: Any) -> dict[str, Any]:
    r"""SLED -- stop-loss execution drag -- split into its two exact hops, in cents.

    The strategy's maker entries and its merged pairs pay the venue nothing, so every
    cent of slippage and fee in a run concentrates on the take-profit-less bailout of a
    leg whose partner never filled. `_record_exit_close` books the three prices that make
    the split measurable:

    ```
    SLED = (trigger_bid - fresh_bid) + (fresh_bid - exit_price)
            \__ latency drag __/       \__ execution drag __/
    ```

    * **Latency drag** is what the book did between the mark that fired the stop and the
      live bid the exit priced off. It is signed: the book can drift toward us while the
      decision is made, and a clipped zero would erase exactly that case.
    * **Execution drag** is the taker fill's own slippage off the live book. A SELL walks
      the bid ladder down, so it is non-negative in theory -- but it is still reported
      unclipped, because a capped number would be an invented one and this module's rule
      is that a measurement is either recorded or absent.

    `sled_cents` is the SUM of the two returned hops whenever both were measured, never a
    second derivation from `trigger_bid`: the ledger entry and its parts cannot then
    disagree. When only one hop is measurable (no recorded trigger, or no fresh bid), the
    total is still measured against the reference with its `reference_source` disclosed,
    and the hop that could not be measured is `None`.

    Every value is `None` when its own inputs are missing or non-finite. Nothing here is
    a share count scaled by a price: these are per-share cents, so a row is usable
    without a dollar figure.
    """
    trigger = _finite(_close_field(close, "trigger_bid"))
    fresh = _finite(_close_field(close, "fresh_bid"))
    exit_price = _exit_price(close)
    reference, source = _stop_reference(close, _entry_price(close))

    latency = None if trigger is None or fresh is None else (trigger - fresh) * 100.0
    execution = (None if fresh is None or exit_price is None
                 else (fresh - exit_price) * 100.0)
    if latency is not None and execution is not None:
        sled = latency + execution
    elif reference is not None and exit_price is not None:
        sled = (reference - exit_price) * 100.0
    else:
        sled = None

    def shape(value: Optional[float]) -> Optional[float]:
        return None if value is None else round(value, 6)

    return {
        "sled_cents": shape(sled),
        "latency_drag_cents": shape(latency),
        "execution_drag_cents": shape(execution),
        "reference_bid": shape(reference),
        "reference_source": source,
        "exit_price": shape(exit_price),
    }


def taker_cost_drag_ratio(close: Any) -> dict[str, Any]:
    """The share of a booked bailout loss that is pure fee: `fee / |realized_pnl|`.

    A taker SELL is the only order this strategy sends that can pay the venue anything
    (the venue's graduated spread markets carry no maker fee and a merged pair pays none),
    so this is where the whole fee bill, if there is one, has to appear. The denominator
    is the payoff the close booked, so the ratio answers "how much of this loss was the
    fee" in the units the operator already reads.

    A `NULL` fee is unmeasured, not zero: `fee_measured` says which claim the ratio rests
    on. The rescue writer leaves the column empty, so on today's rows this metric is
    `None` -- and `0.0` there would have said the venue charged nothing, which nobody
    measured. A break-even close has no denominator and is `None` for the same reason.
    """
    fee = _finite(_close_field(close, "fee"))
    pnl = _finite(_close_field(close, "realized_pnl"))
    tcdr = None
    if fee is not None and pnl is not None and abs(pnl) > _LOSS_EPSILON:
        tcdr = fee / abs(pnl)

    def shape(value: Optional[float]) -> Optional[float]:
        return None if value is None else round(value, 6)

    return {
        "tcdr": shape(tcdr),
        "fee_usd": shape(fee),
        "fee_measured": fee is not None,
        "realized_pnl_usd": shape(pnl),
    }


def overshoot_ratio(close: Any) -> dict[str, Any]:
    """The drag in units of the risk the stop meant to cap, not a separate loss figure.

    `planned_risk` is `entry - reference_bid`: the distance the stop was willing to lose
    before it fired. Dividing the drag by it says whether the stop contained the loss it
    promised to (`<= 1x`) or let more escape than it was ever risking (`> 1x`). A negative
    ratio is price improvement, and it is kept rather than clipped.

    There is deliberately no `theoretical_usd` or `degradation_usd` companion. Degradation
    measured against the same reference is mathematically `-SLED` -- the entry term
    cancels -- so it would be a second name for the number `rescue_drag` already returns.

    `status` carries the reason a ratio is absent: `unmeasured` (no drag, or no reference
    to plan from) or `no_planned_risk` (the trigger sat at or above entry, so the stop was
    risking nothing and no overshoot can be expressed as a fraction of it).
    """
    drag = rescue_drag(close)
    entry = _entry_price(close)
    reference = drag["reference_bid"]
    planned = (None if entry is None or reference is None
               else round((entry - reference) * 100.0, 6))
    ratio = None
    status = "unmeasured"
    if drag["sled_cents"] is not None and planned is not None:
        if planned <= 0:
            status = "no_planned_risk"
        else:
            ratio = round(drag["sled_cents"] / planned, 6)
            status = "measured"

    return {
        "overshoot_ratio": ratio,
        "sled_cents": drag["sled_cents"],
        "planned_risk_cents": planned,
        "reference_source": drag["reference_source"],
        "status": status,
    }


def friction_band(metric: str, value: Optional[float]) -> str:
    """The traffic-light band for one metric's displayed value: good/average/bad.

    The thresholds live here and reach the dashboard as data, so the renderer maps a band
    name to a colour and holds no threshold of its own -- a renderer that carried its own
    copy is how a screen starts disagreeing with the payload it is drawing.

    The drag metrics are banded in cents (see `SPREAD_CENTS` / `DRIFT_CENTS`); `tcdr` and
    `overshoot` are unitless ratios. `None` and anything non-finite read `unmeasured`: a
    number nobody recorded is not a bad number. A name outside `FRICTION_BAND_METRICS`
    raises, because a typo returning `unmeasured` would hide behind a plausible reading.
    """
    if metric not in FRICTION_BAND_METRICS:
        raise ValueError(f"unknown friction metric: {metric!r}")
    number = _finite(value)
    if number is None:
        return "unmeasured"

    if metric == "sled":
        if number <= 0:
            return "good"
        return "average" if number <= SPREAD_CENTS else "bad"
    if metric == "latency":
        # Signed: the book drifting toward us between trigger and decision is good news.
        if number <= 0:
            return "good"
        return "average" if number <= SPREAD_CENTS else "bad"
    if metric == "execution":
        if number <= SPREAD_CENTS:
            return "good"
        return "average" if number <= DRIFT_CENTS else "bad"
    if metric == "tcdr":
        if number <= 0:
            return "good"
        return "average" if number < TCDR_BAD_AT_OR_ABOVE else "bad"
    # overshoot: a negative ratio is a stop that lost less than it planned to.
    if number <= 0:
        return "good"
    return "average" if number <= OVERSHOOT_BAD_ABOVE else "bad"


def rescue_friction_summary(closes: Sequence[Any]) -> dict[str, Any]:
    """Every rescue close's friction, in five means with their bands.

    Always present, like `fill_path_edge`, and for the same reason: a caller must never
    have to branch on whether the key exists. A run with no bailouts reports zero counts
    and `None` means, which say "nothing to measure", never "nothing was lost".

    The population is the rescue methods -- the leg whose partner never filled, sold back
    through the book. It is deliberately not narrowed to stop hits: the same routes serve
    the timed exits, and narrowing by `reason` would drop the rows this measurement most
    needs. `non_rescue_count` discloses what was left out rather than folding it in.

    Each mean uses the closes that carry its own metric (`latency` needs a recorded
    trigger, the fee ratio needs a measured fee), so one unrecorded field cannot drag a
    mean toward zero while looking like a measurement. The reference counts say how many
    of the drag figures rest on the named offset assumption rather than a recorded bid,
    and `assumed_stop_offset` carries that offset itself so a readout prints the number
    the computation used rather than a copy of its own.
    """
    rows = [row for row in closes or []
            if str(_close_field(row, "method") or "").strip().lower() in RESCUE_METHODS]
    by_metric: dict[str, list[float]] = {metric: [] for metric in FRICTION_BAND_METRICS}
    recorded = assumed = fee_measured = 0

    for row in rows:
        drag = rescue_drag(row)
        cost = taker_cost_drag_ratio(row)
        over = overshoot_ratio(row)
        if drag["reference_source"] == "recorded":
            recorded += 1
        elif drag["reference_source"] == "assumed_offset":
            assumed += 1
        if cost["fee_measured"]:
            fee_measured += 1
        for metric, value in (("sled", drag["sled_cents"]),
                              ("latency", drag["latency_drag_cents"]),
                              ("execution", drag["execution_drag_cents"]),
                              ("tcdr", cost["tcdr"]),
                              ("overshoot", over["overshoot_ratio"])):
            if value is not None:
                by_metric[metric].append(value)

    means = {metric: (round(statistics.fmean(values), 6) if values else None)
             for metric, values in by_metric.items()}
    return {
        "rescue_count": len(rows),
        "measured_count": len(by_metric["sled"]),
        "recorded_reference_count": recorded,
        "assumed_reference_count": assumed,
        "assumed_stop_offset": STOP_OFFSET_FALLBACK,
        "fee_measured_count": fee_measured,
        "non_rescue_count": len(closes or []) - len(rows),
        "mean_sled_cents": means["sled"],
        "mean_latency_drag_cents": means["latency"],
        "mean_execution_drag_cents": means["execution"],
        "mean_tcdr": means["tcdr"],
        "mean_overshoot_ratio": means["overshoot"],
        "bands": {metric: friction_band(metric, value)
                  for metric, value in means.items()},
        "units": "cents_per_share",
    }


# ---------------------------------------------------------------------------
# Queue realism (#515) -- read off the tape that credited a fill, in cents and seconds.
# ---------------------------------------------------------------------------

# The ticket's settled depth bands. They score the MEDIAN across cleared fills: under a
# cent the level cleared on the tape we sat on, 1-3c the tape had to move past us, past
# 3c the quote sat far from where the market actually traded.
TRADE_THROUGH_GOOD_BELOW_CENTS = 1.0
TRADE_THROUGH_AVERAGE_AT_OR_BELOW_CENTS = 3.0


@dataclass(frozen=True)
class ClearedFill:
    """One credited fill, its resting price, and the print that cleared it.

    Field -> registry mapping. The caller converts; this module never ingests:

    * `order_price`    -- our resting price in DOLLARS (`orders.price` / `quotes.price`).
    * `clearing_price` -- the closest tape print STRICTLY below `order_price` that credited
      the fill, in DOLLARS. Recorded at fill-credit time by the shadow path (#531,
      the shadow rehearsal path (`_write_clearing_print`)) and read back via `get_all_fills`; stores that
      never ran a rehearsal still leave it `None`.
    * `placed_ts`      -- `quotes.ts`, in SECONDS.
    * `filled_ts`      -- the credited fill's time in SECONDS. `quotes.fill_ts` and
      `fills.venue_ts` are MILLISECONDS (`order_registry._attribute_fill_to_quote` builds
      `fill_ts` as `MIN(COALESCE(venue_ts, recorded_ts))`), so the caller divides by 1000
      before building a record -- exactly the conversion `kpi.py:1447-1449` already makes.

    A shadow fill's `venue_ts` is the cycle that RECOGNISED the trade-through, not the
    print's own time, so a wait measured from these fields is an upper bound on the real
    rest, never its exact length.
    """
    order_id: str
    order_price: Optional[float] = None
    clearing_price: Optional[float] = None
    placed_ts: Optional[float] = None
    filled_ts: Optional[float] = None


def _percentile(values: Sequence[float], fraction: float) -> Optional[float]:
    """The value at `fraction` through a sorted sample, linearly interpolated.

    Convention: linear interpolation at index `fraction * (n - 1)` (#541).
    `None` for an empty sample, and the value itself for a single one: an index of
    `fraction * (n - 1)` has no neighbour to blend with at n = 1 and no meaning at all at
    n = 0, which is where this section's zero-denominator question lives. Non-finite
    entries are dropped rather than sorted, so one `NaN` cannot poison a whole sample.

    Interpolated rather than nearest-rank so that `p50` IS the median `statistics.median`
    returns. `scripts/family_probe_report.py` takes the nearest-rank route and documents
    why (a thin family must still report a number) -- but that is a script's own reader,
    while the depth band here is computed from this figure, and two conventions must not
    meet on the number a colour rides on.
    """
    sample = sorted(v for v in (_finite(v) for v in values or []) if v is not None)
    if not sample:
        return None
    if len(sample) == 1:
        return sample[0]
    index = fraction * (len(sample) - 1)
    low = math.floor(index)
    high = min(low + 1, len(sample) - 1)
    return sample[low] + (sample[high] - sample[low]) * (index - low)


def trade_through_depth_band(median_cents: Optional[float]) -> str:
    """The traffic-light band for a trade-through depth median: good / average / bad.

    The settled table, in cents: `[0, 1.0)` the level cleared on the tape we sat on,
    `[1.0, 3.0]` the tape had to move past us, `(3.0, inf)` the quote sat far from where
    the market actually traded. `None` and anything non-finite read `unmeasured` -- a
    number nobody recorded is not a bad number.

    A NEGATIVE figure reads `unmeasured` too, not `good`. The table starts at zero because
    a cleared fill's depth cannot be negative: a print at or above our own price is not a
    trade-through, and `trade_through_depth` excludes such a row before any band is
    computed. A negative argument therefore means the caller handed over something no
    cleared fill can produce, and the good side of a table it is not on would be the wrong
    answer for it.

    The thresholds live here and reach a reader as data, following `friction_band`: a
    renderer maps a band name to a colour and holds no numeral of its own.
    """
    number = _finite(median_cents)
    if number is None or number < 0.0:
        return "unmeasured"
    if number < TRADE_THROUGH_GOOD_BELOW_CENTS:
        return "good"
    return "average" if number <= TRADE_THROUGH_AVERAGE_AT_OR_BELOW_CENTS else "bad"


def trade_through_depth(fills: Sequence[ClearedFill]) -> dict[str, Any]:
    """How far below our own price the tape had to print to clear the level, in cents.

    Always present, like `fill_path_edge` and `rescue_friction`, and for the same reason: a
    caller must never have to branch on whether the reading exists. A run with nothing
    measurable reports zero counts and `None` percentiles, which say "nothing to measure",
    never "the tape sat on our price".

    A fill is measured only when both prices are finite and `clearing_price` is STRICTLY
    below `order_price` -- that strictness is the fill model itself, so a row which equals
    or exceeds our price is disclosed as `not_trade_through` instead of being folded in as
    a zero. `missing` and `not_trade_through` are counted apart from each other because
    they are different claims: one is a value nobody recorded, the other a value the model
    cannot produce. The three counts partition the input.

    `band` scores `median_cents` -- the same rounded figure a reader sees, so the number
    and its colour cannot disagree.
    """
    depths: list[float] = []
    missing = 0
    not_trade_through = 0
    for row in fills or []:
        order_price = _finite(row.order_price)
        clearing = _finite(row.clearing_price)
        if order_price is None or clearing is None:
            missing += 1
        elif clearing >= order_price:
            not_trade_through += 1
        else:
            depths.append((order_price - clearing) * 100.0)

    def shape(value: Optional[float]) -> Optional[float]:
        return None if value is None else round(value, 6)

    median = shape(_percentile(depths, 0.50))
    return {
        "count": len(depths),
        "missing": missing,
        "not_trade_through": not_trade_through,
        "median_cents": median,
        "p75_cents": shape(_percentile(depths, 0.75)),
        "band": trade_through_depth_band(median),
    }


def fill_latency(fills: Sequence[ClearedFill]) -> dict[str, Any]:
    """How long a resting quote waited for its first credited fill, in seconds.

    Descriptive only, and deliberately without a band. How far the tape had to move below
    our price can be judged against the price itself -- that is what the depth table does.
    A wait cannot: two seconds is neither good nor bad without a reference for how fast
    THIS market fills, and a band invented here would be a verdict wearing a measurement's
    clothes. The same rule leaves the queue-depth distribution unbanded.

    The population is every fill whose placement and fill times were both recorded. A
    negative wait is EXCLUDED and counted rather than clamped to `0.0`, which is where this
    reader parts company with `kpi.median_seconds_to_fill` (`kpi.py:1449` clamps with
    `max(0.0, ...)`): under a shadow run the fill's timestamp is the cycle that recognised
    the trade-through, so the row whose fill appears to precede its placement is measuring
    a settlement artefact, and reporting it as an instant fill would be a claim nobody
    measured. `count + excluded == len(fills)`.

    Independent of `trade_through_depth` on purpose: a record carrying prices but no
    timestamps still measures for depth (and is `excluded` here), and the reverse. One
    unrecorded field must not silently zero the other metric.
    """
    waits: list[float] = []
    excluded = 0
    for row in fills or []:
        placed = _finite(row.placed_ts)
        filled = _finite(row.filled_ts)
        if placed is None or filled is None or filled < placed:
            excluded += 1
        else:
            waits.append(filled - placed)

    def shape(value: Optional[float]) -> Optional[float]:
        return None if value is None else round(value, 6)

    return {
        "count": len(waits),
        "excluded": excluded,
        "median_sec": shape(_percentile(waits, 0.50)),
        "p90_sec": shape(_percentile(waits, 0.90)),
    }


def cleared_fills(quotes: Sequence[Any], fills: Sequence[Any]) -> list[ClearedFill]:
    """The records the two metrics read, built from rows the registry already holds.

    Pure over supplied rows, like every other section here: `quotes` is
    `get_all_quotes()` and `fills` is `get_all_fills()`, joined on
    `fills.order_uuid == quotes.local_id` -- the identifier `record_fill`,
    `_attribute_fill_to_quote` and `kpi.py:1443-1449` all join on.

    One record per quote that a fill credits:

    * `placed_ts`  -- `quotes.ts`, already in SECONDS.
    * `filled_ts`  -- the EARLIEST fill's `venue_ts` (falling back to `recorded_ts`, which a
      shadow fill sets to the same instant) in MILLISECONDS, divided by 1000 here, once, so
      no second reader has to re-derive the factor. `quotes.fill_ts` carries that same
      millisecond number. The earliest fill is the one the wait is measured to: averaging
      the last partial in would shorten every reading.
    * `order_price` -- `quotes.price`, in dollars.
    * `clearing_price` -- the closest print strictly below our price, from the
      EARLIEST fill row that carries one (#531), so a print-less earliest row
      never suppresses a usable later one. Rows from stores that never recorded
      a print (or fills with no print) keep `None` -- a measurement nobody
      took, which is not the same as a depth of zero.

    A quote that never filled is left out instead of carried as an unmeasurable wait: it did
    not wait for a fill, it was cancelled or expired, and counting it in `excluded` would
    report a missing measurement where there is nothing to measure. A fill whose timestamp
    nobody recorded is the opposite case and is KEPT, with `filled_ts` left `None`: a fill
    that happened is a real row, and dropping it would hide it from the count instead of
    reporting the wait as unmeasurable.
    """
    credited: set[str] = set()
    first_fill_ms: dict[str, float] = {}
    first_fill_print: dict[str, Optional[float]] = {}
    first_print_ms: dict[str, Optional[float]] = {}
    for fill in fills or []:
        order_uuid = _close_field(fill, "order_uuid")
        if order_uuid is None:
            continue
        key = str(order_uuid)
        credited.add(key)
        stamp = _finite(_close_field(fill, "venue_ts"))
        if stamp is None:
            stamp = _finite(_close_field(fill, "recorded_ts"))
        if stamp is not None:
            if key not in first_fill_ms or stamp < first_fill_ms[key]:
                first_fill_ms[key] = stamp
        # Prefer the EARLIEST row that actually carries a print (#531): a
        # timestamp-less or print-less earliest row must not suppress a usable
        # print on a later row of the same order.
        clearing = _finite(_close_field(fill, "clearing_price"))
        if clearing is not None and (
            key not in first_print_ms
            or (stamp is not None and stamp < first_print_ms[key])
        ):
            first_fill_print[key] = clearing
            first_print_ms[key] = stamp

    records: list[ClearedFill] = []
    for quote in quotes or []:
        local_id = _close_field(quote, "local_id")
        if local_id is None:
            continue
        key = str(local_id)
        if key not in credited:
            continue
        stamp = first_fill_ms.get(key)
        records.append(ClearedFill(
            order_id=key,
            order_price=_finite(_close_field(quote, "price")),
            clearing_price=first_fill_print.get(key),
            placed_ts=_finite(_close_field(quote, "ts")),
            filled_ts=None if stamp is None else stamp / 1000.0,
        ))
    return records


# ---------------------------------------------------------------------------
# Statistical robustness gates (#516) -- is the mean an edge or an accident, and
# is the tape behind our fills helping or hurting.
# ---------------------------------------------------------------------------

# Two-sided normal critical values, by confidence level. Used once the sample
# is large enough for the normal approximation to hold.
#
# This block lived in `kpi.py` until #516. It is here now because the robustness card needs the
# same band over the same closes, and a second copy of a threshold table is how two screens start
# disagreeing about one run. `kpi.py` imports it back as `_mean_pnl_ci`, so the KPI payload is
# unchanged; this module still imports nothing of its own (see the subprocess test in
# `tests/test_markout_robustness_analytics.py`).
CI_Z_BY_LEVEL = ((90, 1.645), (95, 1.96))

# Two-sided Student-t critical values by degrees of freedom (n - 1), for the
# same two levels. On the samples this dashboard actually runs on -- three
# closes, seven closes -- the normal value is far too narrow: two closes of
# $1.00 and $3.00 give a 95% normal band of +$0.04 to +$3.96, a confident
# "profitable" verdict drawn from two observations. The t band on the same two
# closes spans zero, which is the truth. Beyond 30 degrees of freedom the two
# agree to the third decimal and the normal value takes over.
CI_T_BY_DF: dict[int, dict[int, float]] = {
    1: {90: 6.314, 95: 12.706}, 2: {90: 2.920, 95: 4.303},
    3: {90: 2.353, 95: 3.182}, 4: {90: 2.132, 95: 2.776},
    5: {90: 2.015, 95: 2.571}, 6: {90: 1.943, 95: 2.447},
    7: {90: 1.895, 95: 2.365}, 8: {90: 1.860, 95: 2.306},
    9: {90: 1.833, 95: 2.262}, 10: {90: 1.812, 95: 2.228},
    11: {90: 1.796, 95: 2.201}, 12: {90: 1.782, 95: 2.179},
    13: {90: 1.771, 95: 2.160}, 14: {90: 1.761, 95: 2.145},
    15: {90: 1.753, 95: 2.131}, 16: {90: 1.746, 95: 2.120},
    17: {90: 1.740, 95: 2.110}, 18: {90: 1.734, 95: 2.101},
    19: {90: 1.729, 95: 2.093}, 20: {90: 1.725, 95: 2.086},
    21: {90: 1.721, 95: 2.080}, 22: {90: 1.717, 95: 2.074},
    23: {90: 1.714, 95: 2.069}, 24: {90: 1.711, 95: 2.064},
    25: {90: 1.708, 95: 2.060}, 26: {90: 1.706, 95: 2.056},
    27: {90: 1.703, 95: 2.052}, 28: {90: 1.701, 95: 2.048},
    29: {90: 1.699, 95: 2.045}, 30: {90: 1.697, 95: 2.042},
}


def ci_z(level: int) -> Optional[float]:
    """The plain two-sided normal multiplier at `level`, or `None` when there is no such row.

    THE table, for every caller that has a normal approximation to draw. A caller writes the
    number down from here rather than as a literal, and `None` for an unknown level is not the
    same answer as `1.0`: a level nobody tabulated has no multiplier, and returning one would
    put a confident band around a figure no table supports.

    A caller whose sample is small enough for the tail to matter wants
    `ci_critical_value`, which swaps in the t row for the same level (#535).
    """
    return dict(CI_Z_BY_LEVEL).get(level)


def normal_mean_interval(mean: float, std: float, n: int, mult: float) -> tuple[float, float, float]:
    """Mean Â± multÂ·(std/sqrt(n)): the one arithmetic every mean interval shares.

    The multiplier stays the caller's choice (`ci_z` for a normal approximation,
    `ci_critical_value` for Student-t) â€” this function only fixes the order of
    operations so all producers compute bit-identical bands from the same inputs.
    Callers keep their own empty/small-sample behavior; `n < 2` has no standard
    error and must never reach here.
    """
    margin = mult * (std / math.sqrt(n))
    return mean - margin, mean + margin, margin


def ci_critical_value(level: int, n: int, z: float) -> float:
    """The multiplier for a two-sided interval on a sample of `n`.

    Student-t while the sample is small enough for it to matter, the normal
    value once it is not. No new dependency: the table is the whole of what
    `scipy.stats.t.ppf` would be used for here.
    """
    return CI_T_BY_DF.get(n - 1, {}).get(level, z)


def ci_critical_values(n: int) -> dict[str, float]:
    """Every two-sided multiplier for a sample of `n`, keyed by level as a string.

    Shipped as payload DATA so a renderer multiplies its own standard error by a number the
    server decided. The renderer then holds no table, no threshold and no opinion about the
    distribution, which is the difference between one definition and two that can quietly draw
    different intervals over the same closes (#535).

    Callers ship this only for a sample that has a standard deviation to scale; a distribution
    with nothing to multiply stores `None` instead, so `None` never becomes a critical value.
    """
    return {str(level): ci_critical_value(level, n, z) for level, z in CI_Z_BY_LEVEL}


def mean_pnl_ci(pnls: Sequence[float]) -> dict[str, Any]:
    """The 90% and 95% band on the mean realized PnL per close, in dollars.

    The GO/NO-GO question #90 names is not "what is the average" but "does the
    interval around it still contain a loss, and how deep". So each level
    carries that answer outright rather than leaving it to be inferred from a
    lower bound printed next to a mean.

    A sample too small for a standard error reports no levels at all. A zero
    would read as "breaks even", which is a measurement one close has not made.
    """
    n = len(pnls)
    mean_usd = statistics.mean(pnls) if n else None
    if n < 2 or mean_usd is None:
        return {"mean_usd": mean_usd, "n": n, "levels": [], "verdict": None}

    se = statistics.stdev(pnls) / math.sqrt(n)
    levels = []
    for level, z in CI_Z_BY_LEVEL:
        crit = ci_critical_value(level, n, z)
        lower = mean_usd - crit * se
        upper = mean_usd + crit * se
        levels.append({
            "level": level,
            "lower": lower,
            "upper": upper,
            "includes_negative": lower < 0,
            # How far below zero the band reaches -- the "and how much" half
            # of the question. NULL when it never gets there.
            "negative_depth_usd": abs(lower) if lower < 0 else None,
        })

    # The verdict reads off the wider band: passing on 90% while the 95% band
    # still spans zero is not a pass.
    widest = levels[-1]
    if widest["lower"] > 0:
        verdict = "positive"
    elif widest["upper"] < 0:
        verdict = "negative"
    else:
        verdict = "spans_zero"

    return {"mean_usd": mean_usd, "n": n, "levels": levels, "verdict": verdict}


# The ticket's settled markout bands, on the MEDIAN signed post-fill displacement in cents:
# `good` is the tape holding or moving our way after the fill, `average` a drift against us of
# less than a tick, `bad` a full cent or more the wrong way -- we were filled, then it dropped.
MARKOUT_GOOD_AT_OR_ABOVE_CENTS = 0.0
MARKOUT_AVERAGE_ABOVE_CENTS = -1.0

# What each band MEANS, shipped beside it as data. The renderer names a colour for the band and
# prints these words; no threshold, and no verdict of its own, lives in the JavaScript.
MARKOUT_BAND_WORDS = {
    "good": "mid held or moved in our favour after the fill",
    "average": "mid drifted against us by less than a tick",
    "bad": "the tape moved a full cent or more against us after the fill",
    "unmeasured": "no matured post-fill mark to measure",
}


def markout_cents_band(median_cents: Optional[float]) -> str:
    """The traffic-light band for a median post-fill displacement in cents.

    The ticket's table: `[0, inf)` good, `(-1.0, 0)` average, `(-inf, -1.0]` bad. `None` and
    anything non-finite read `unmeasured` -- a figure nobody measured is not a bad figure.

    The thresholds live here and reach the dashboard as data, following `friction_band`: a
    renderer maps a band name to a colour and holds no numeral of its own.
    """
    number = _finite(median_cents)
    if number is None:
        return "unmeasured"
    if number >= MARKOUT_GOOD_AT_OR_ABOVE_CENTS:
        return "good"
    return "average" if number > MARKOUT_AVERAGE_ABOVE_CENTS else "bad"


def _side_direction(side: Any) -> Optional[float]:
    """+1 for a BUY fill, -1 for a SELL, `None` for a side that cannot be signed.

    The same convention `core_brain.markout.peer_markout` takes as its `direction`: a buyer is
    hurt by the mid falling, a seller by it rising.
    """
    text = str(side or "").strip().upper()
    if text == "BUY":
        return 1.0
    if text == "SELL":
        return -1.0
    return None


def _markout_direction(row: Any) -> Optional[float]:
    """The direction of the fill a markout row came from."""
    return _side_direction(_close_field(row, "side"))


def _markout_samples(rows: Sequence[Any], horizons: Sequence[tuple[str, str]],
                     direction_for: Callable[[Any], Optional[float]],
                     ) -> dict[str, list[tuple[float, float, float]]]:
    """Per matured horizon, one `(signed displacement, fill price, size)` triple per usable fill.

    Shared by `markout()` (the size-weighted mean of that displacement, unsigned, bps) and
    `signed_markout()` (the median of it, signed by side, cents), so the two readers cannot
    disagree about which rows count or how one displacement is derived.

    The displacement is the RAW price difference in DOLLARS, and no unit is applied here on
    purpose. Each reader names its own from the raw triple -- bps (`delta / fill * 10_000`) for
    the chart, cents of price (`delta * 100`) for the gates -- because a unit baked into the
    shared helper is precisely how two readers end up publishing different quantities under one
    word. The sibling conversion is not interchangeable: bps/100 is the PERCENT move of the fill
    price, which equals cents only when the fill happened at $1.00.

    A row is skipped when `direction_for` refuses it, when the horizon's mark or the fill price
    is missing or non-finite, when the fill price is not positive (the divisor), or when the
    size is NEGATIVE. A missing or zero size takes unit weight instead of dropping the row,
    mirroring `markout()`'s long-standing `_finite(size) or 1.0` -- the two readers share this
    helper precisely so they cannot diverge on which rows count. A horizon nothing matured into
    is absent from the mapping, which is how an unmatured fill avoids being read as a flat one.
    """
    samples: dict[str, list[tuple[float, float]]] = {}
    for column, label in horizons:
        collected: list[tuple[float, float]] = []
        for row in rows or []:
            direction = direction_for(row)
            if direction is None:
                continue
            later = _finite(_close_field(row, column))
            fill = _finite(_close_field(row, "fill_price"))
            if later is None or fill is None or fill <= 0:
                continue
            size = _finite(_close_field(row, "size")) or 1.0
            if size <= 0:
                continue
            # Raw and signed, in dollars, with the fill it happened against and the weight.
            collected.append(((later - fill) * direction, fill, size))
        if collected:
            samples[label] = collected
    return samples


def signed_markout(rows: Sequence[Any],
                   horizons: Sequence[tuple[str, str]] = MARKOUT_HORIZONS) -> dict[str, Any]:
    """Post-fill mid displacement, signed for the side we traded, as a median in CENTS.

    Positive means the tape moved our way after the fill -- spread capture. Negative means it
    moved against us -- the fill we got was toxic flow. An unmatured horizon is omitted rather
    than shown at zero, and a row whose side is neither BUY nor SELL is skipped: its sign cannot
    be derived, and a guessed sign would put a fabricated number under a colour.

    Cents of PRICE, the absolute move -- a 1c drift on a 50c fill reads `1.0` here and `200.0` on
    the chart's bps scale, so the ticket's cent bands score the same quantity the MAE half
    reports. Units are named outright because "a full cent against us" means a cent of price,
    not a percent of what we paid.

    The MEDIAN across fills, deliberately, not the size-weighted mean: one whale fill must not
    move the figure the traffic-light band is computed from. `markout()` above is the
    size-weighted mean in bps, and the two are different questions about the same rows.

    `band` scores the rounded `markout_cents` a reader sees, so the number and its colour cannot
    disagree.
    """
    intervals = []
    for label, samples in _markout_samples(rows, horizons, _markout_direction).items():
        # Dollars -> cents of PRICE (`delta * 100`), linearly interpolated, so p50 IS the median.
        # Going via the chart's bps figure instead would report the move as a percent of what we
        # paid -- 2.0 for a 1c move on a 50c fill -- and would score the ticket's cents bands
        # against that percent, flagging a 0.6c drift on a cheap fill as `bad`.
        median = _percentile([delta * 100.0 for delta, _, _ in samples], 0.50)
        rounded = None if median is None else round(median, 4)
        band = markout_cents_band(rounded)
        intervals.append({
            "horizon": label,
            "markout_cents": rounded,
            "samples": len(samples),
            "band": band,
            "meaning": MARKOUT_BAND_WORDS[band],
        })
    return {"intervals": intervals}


# `mid_hN` -> how many seconds after the fill that column is sampled, in the display order the
# chart reads them. Declared rather than imported from `core_brain.markout`, which loads config
# and pulls in the order registry at import time; this module promises no import side effects,
# and `tests/test_markout_robustness_analytics.py` pins these numbers against
# `config.markout_horizons` so the copy cannot drift into mis-winding every window.
MARKOUT_HORIZON_SECONDS = {"mid_h0": 300.0, "mid_h3": 900.0,
                           "mid_h1": 3600.0, "mid_h2": 21600.0}


@dataclass(frozen=True)
class HeldMark:
    """One entry fill and the recorded post-fill marks inside its holding window.

    Field -> registry mapping. The caller converts; this module never ingests:

    * `side`        -- `markouts.side`, the order book's BUY/SELL, not the UP/DOWN leg.
    * `entry_price` -- `markouts.fill_price`, in dollars.
    * `marks`       -- the matured `mid_hN` values whose stamp
      (`markouts.ts` + `MARKOUT_HORIZON_SECONDS[N]`) falls at or before the position's close,
      in dollars, in `MARKOUT_HORIZON_SECONDS` order.

    `excursion_summary` builds these from the rows it is handed; the dataclass exists so the
    per-position arithmetic can be tested on its own, without a store behind it.
    """
    side: str
    entry_price: Optional[float] = None
    marks: tuple[float, ...] = ()


def max_adverse_excursion(entry_price: Optional[float], side: Optional[str],
                          prices: Sequence[float]) -> dict[str, Any]:
    """The worst the tape went against us while we held, in cents and in price.

    Measured from the side we traded: a BUY is hurt by the lowest price in the series, a SELL by
    the highest. Clamped at zero, because a position the tape never moved against has an
    excursion of zero -- we looked, and nothing went against us.

    `None` when the question has no answer: no usable price in the series, an entry price that is
    missing or not positive (there is nothing to measure a move against), or a side that is
    neither BUY nor SELL. Descriptive only, and deliberately unbanded -- "6c of excursion" has no
    verdict until it can be compared against the stop distance that was meant to cap it.

    Reads nothing: no tape, no registry, no clock. The series is whatever the caller recorded.
    """
    entry = _finite(entry_price)
    direction = _side_direction(side)
    series = [p for p in (_finite(p) for p in prices or []) if p is not None]
    result: dict[str, Any] = {"mae_cents": None, "worst_price": None, "samples": len(series)}
    if not series or entry is None or entry <= 0 or direction is None:
        return result

    worst = min(series) if direction > 0 else max(series)
    move = (entry - worst) if direction > 0 else (worst - entry)
    return {
        "mae_cents": round(max(0.0, move) * 100.0, 6),
        "worst_price": round(worst, 6),
        "samples": len(series),
    }


def _closes_by_condition(closes: Sequence[Any]) -> dict[str, list[float]]:
    """Sorted close timestamps per condition, for bounding a fill's window."""
    stamps: dict[str, list[float]] = {}
    for row in closes or []:
        condition_id = _close_field(row, "condition_id")
        ts = _finite(_close_field(row, "ts"))
        if condition_id is None or ts is None:
            continue
        stamps.setdefault(str(condition_id), []).append(ts)
    for column in stamps.values():
        column.sort()
    return stamps


def _first_at_or_after(stamps: Sequence[float], moment: float) -> Optional[float]:
    """The earliest stamp at or after `moment`, or `None` if the series has none."""
    index = bisect.bisect_left(stamps, moment)
    return stamps[index] if index < len(stamps) else None


def excursion_summary(closes: Sequence[Any], markouts: Sequence[Any]) -> dict[str, Any]:
    """How far positions ran against us while held, as a median and a p90 in cents.

    The population is one entry fill at a time, and its price series is the marks the sampler
    RECORDED for that fill -- `mid_h0`..`mid_h3`, at 5m/15m/1h/6h -- kept only where the mark's
    own stamp falls at or before the position's close. A mark after the exit was not part of the
    holding window the ticket defines, so including it would answer a different question.

    The window's end is the EARLIEST close for that condition at or after the fill's own time: a
    mark can share its instant with the close and still be in the window, and a close that
    predates the fill is not this position's exit at all.

    Every markout row lands in exactly one bucket, and the five partition the input:

    * `measured`          -- an entry fill with at least one mark inside its window.
    * `exit_fill`         -- any row whose side is not BUY. Our own exits are SELLs, and an exit
      is not a position: there is no holding window to measure an excursion over.
    * `open`              -- no close yet for that condition, so the window has not ended.
    * `no_marks_in_window`-- the window closed before any recorded mark fell inside it.
    * `not_recorded`      -- no entry price or no fill time to build a window from.

    The buckets are counted rather than folded in, because each is a different reason for a
    missing number and only one of them is a measurement of zero.
    """
    windows = _closes_by_condition(closes)
    measured: list[float] = []
    counts = {"open": 0, "exit_fill": 0, "no_marks_in_window": 0, "not_recorded": 0}

    for row in markouts or []:
        # Only an entry opens a position. A SELL row is our own exit, and a row with no usable
        # side is not something a holding window can be built around -- neither is measured.
        if _side_direction(_close_field(row, "side")) != 1.0:
            counts["exit_fill"] += 1
            continue
        entry = _finite(_close_field(row, "fill_price"))
        row_ts = _finite(_close_field(row, "ts"))
        condition_id = _close_field(row, "condition_id")
        if entry is None or entry <= 0 or row_ts is None or condition_id is None:
            counts["not_recorded"] += 1
            continue
        stamps = windows.get(str(condition_id))
        close_ts = None if not stamps else _first_at_or_after(stamps, row_ts)
        if close_ts is None:
            counts["open"] += 1
            continue

        series = [mark for mark, stamp in
                  ((_finite(_close_field(row, column)), row_ts + offset)
                   for column, offset in MARKOUT_HORIZON_SECONDS.items())
                  if mark is not None and stamp <= close_ts]
        if not series:
            counts["no_marks_in_window"] += 1
            continue
        measured.append(max_adverse_excursion(entry, _close_field(row, "side"),
                                              series)["mae_cents"])

    def shape(value: Optional[float]) -> Optional[float]:
        return None if value is None else round(value, 6)

    return {
        "measured": len(measured),
        "open": counts["open"],
        "exit_fill": counts["exit_fill"],
        "no_marks_in_window": counts["no_marks_in_window"],
        "not_recorded": counts["not_recorded"],
        "median_cents": shape(_percentile(measured, 0.50)),
        "p90_cents": shape(_percentile(measured, 0.90)),
        "units": "cents_per_share",
    }


# The ticket's fixed SQN thresholds: below `SQN_NOISE_BELOW` the result could be luck, above
# `SQN_STABLE_EDGE_ABOVE` the edge is stable, and the closed band between them is promising but
# unproven. The band name is the dashboard's own vocabulary -- good / average / bad / unmeasured
# -- so a reader maps one word to the theme's chip; `SQN_BAND_WORDS` carries what it means.
SQN_NOISE_BELOW = 1.6
SQN_STABLE_EDGE_ABOVE = 2.5

SQN_BAND_WORDS = {
    "good": "stable edge -- signal dominates noise",
    "average": "promising but unproven",
    "bad": "noise -- the results could be luck",
    "unmeasured": "fewer than two closes, or no spread to measure",
}


def sqn_band(sqn: Optional[float]) -> str:
    """The traffic-light band for a system quality number.

    `None` and anything non-finite read `unmeasured`: one close, or a run where every close was
    identical, has no score at all, and a score of zero would read as "no edge" when the truth
    is "not enough evidence to say".
    """
    number = _finite(sqn)
    if number is None:
        return "unmeasured"
    if number > SQN_STABLE_EDGE_ABOVE:
        return "good"
    return "bad" if number < SQN_NOISE_BELOW else "average"


def system_quality_number(pnls: Sequence[float]) -> dict[str, Any]:
    """How large the average trade is next to the spread of the results, times sqrt(n).

    `sqrt(n) * mean / stdev` over the close PnLs: the same mean scores higher on a longer run and
    lower on a noisier one, which is the whole point -- a positive mean over five closes is not
    the same evidence as the same mean over five hundred.

    Sample standard deviation, uncapped n, and a band classified on the ROUNDED score so the
    ticket's 1.6 and 2.5 boundaries are exactly reproducible. Fewer than two closes, or a sample
    with no spread at all, is `None` rather than a confident verdict.

    `stdev_usd` distinguishes the two absences: a flat sample measured a spread of zero, while a
    one-close sample never measured one.
    """
    sample = [p for p in (_finite(p) for p in pnls or []) if p is not None]
    n = len(sample)
    if not sample:
        return {"sqn": None, "band": "unmeasured", "meaning": SQN_BAND_WORDS["unmeasured"],
                "n": 0, "mean_usd": None, "stdev_usd": None}

    mean = statistics.fmean(sample)
    stdev = statistics.stdev(sample) if n > 1 else None
    if stdev is None or stdev == 0:
        return {"sqn": None, "band": "unmeasured", "meaning": SQN_BAND_WORDS["unmeasured"],
                "n": n, "mean_usd": round(mean, 6),
                "stdev_usd": None if stdev is None else 0.0}

    score = round(math.sqrt(n) * mean / stdev, 6)
    band = sqn_band(score)
    return {"sqn": score, "band": band, "meaning": SQN_BAND_WORDS[band], "n": n,
            "mean_usd": round(mean, 6), "stdev_usd": round(stdev, 6)}


# The confidence-interval gate, in the ticket's own words: zero outside the band on the good
# side is a proven edge, zero inside it is suggestive only, and zero outside it on the bad side
# is a negative edge. Shipped beside the band so the renderer prints the operator's language.
# The middle wording is deliberately silent about the mean's SIGN: the band straddles zero
# whenever the mean is not yet decisive, and that includes a run whose mean is negative -- copy
# that asserted "the mean is positive" here would print a falsehood on exactly those runs.
CI_BAND_WORDS = {
    "good": "zero is outside the band -- the edge is statistically proven",
    "average": "zero is inside the band -- suggestive, not proven",
    "bad": "zero is outside the band on the wrong side -- the edge is negative",
    "unmeasured": "fewer than two closes -- no band exists",
}


# The level the card reads off `mean_pnl_ci`. Selected by name rather than by position, so a
# reorder of `CI_Z_BY_LEVEL` cannot silently relabel the 90% band as the 95% one.
CI_CARD_LEVEL = 95


def pnl_ci_band(lower_cents: Optional[float],
                upper_cents: Optional[float]) -> str:
    """The traffic-light band for a two-sided confidence interval, in cents.

    `good` when the whole band sits above zero, `bad` when it sits below, `average` when it
    contains zero -- the edge is not proven, whatever the mean's sign. Either end missing reads
    `unmeasured`: an interval with no boundary is not a verdict.

    Both arguments are the ROUNDED cents figures the payload ships, so the chip and the numbers
    beside it can never disagree.
    """
    lower = _finite(lower_cents)
    upper = _finite(upper_cents)
    if lower is None or upper is None:
        return "unmeasured"
    if lower > 0:
        return "good"
    return "bad" if upper < 0 else "average"


def _cents(value: Optional[float]) -> Optional[float]:
    """Dollars to cents, rounded, or `None` when there is no figure to convert."""
    number = _finite(value)
    return None if number is None else round(number * 100.0, 6)


def _mean_pnl_ci_cents(pnls: Sequence[float]) -> dict[str, Any]:
    """The 95% band on mean close PnL in cents, with the gate's band and its words.

    The band comes from `mean_pnl_ci`, the same helper the KPI payload reads, so the two screens
    cannot report different intervals over the same closes.
    """
    out = mean_pnl_ci(pnls)
    chosen = next((level for level in out["levels"]
                   if level["level"] == CI_CARD_LEVEL), None)
    lower = None if chosen is None else _cents(chosen["lower"])
    upper = None if chosen is None else _cents(chosen["upper"])
    band = pnl_ci_band(lower, upper)
    return {
        "level": CI_CARD_LEVEL,
        "mean_cents": _cents(out["mean_usd"]),
        "lower_cents": lower,
        "upper_cents": upper,
        "n": out["n"],
        "band": band,
        "meaning": CI_BAND_WORDS[band],
    }


def robustness(closes: Sequence[Any], markouts: Sequence[Any]) -> dict[str, Any]:
    """The four robustness readings the Reports & Analytics card mounts, in cents.

    Always present, like `fill_path_edge`, `rescue_friction` and `queue_realism`, and for the
    same reason: a caller must never have to branch on whether a reading exists. A run with no
    closes reports `None` and `unmeasured` rather than zeroes, which say "nothing to measure",
    never "nothing was lost" or "no edge".

    The halves are deliberately independent. `markout` and `mae` read the fill records, `mean_pnl_ci`
    and `sqn` read the closes, and one unrecorded field must not silently blank the other half.

    Each band is computed here, from the same rounded figure it ships with, so the chip a reader
    sees can never disagree with the number beside it.
    """
    pnls = [pnl for pnl in
            (_finite(_close_field(row, "realized_pnl")) for row in closes or [])
            if pnl is not None]
    return {
        "units": "cents_per_share",
        "markout": signed_markout(markouts),
        "mae": excursion_summary(closes, markouts),
        "mean_pnl_ci": _mean_pnl_ci_cents(pnls),
        "sqn": system_quality_number(pnls),
    }


# ---------------------------------------------------------------------------
# Capital risk (#517) -- how deep and how long the equity curve was underwater,
# and where the tail risk sits.
# ---------------------------------------------------------------------------

# The settled skewness table, on the third standardized moment of the per-close returns: a right
# tail is the shape where the losses stay small and the wins occasionally run, and the closed band
# down to `SKEWNESS_AVERAGE_AT_OR_ABOVE` is "no dominant tail" -- zero included, because a
# symmetric sample is not an upside tail. Below the edge the losses are the tail.
SKEWNESS_AVERAGE_AT_OR_ABOVE = -0.5

SKEWNESS_BAND_WORDS = {
    "good": "upside tails -- losses stay small, wins occasionally large",
    "average": "roughly symmetric -- no dominant tail",
    "bad": "downside tails -- the catastrophic-loss shape",
    "unmeasured": "fewer than three closes, or no spread to divide by",
}

# The settled Sortino table, on the return per unit of downside pain.
SORTINO_GOOD_AT_OR_ABOVE = 2.0
SORTINO_AVERAGE_AT_OR_ABOVE = 1.0

SORTINO_BAND_WORDS = {
    "good": "returns are twice the downside pain",
    "average": "returns merely match the downside pain",
    "bad": "the downside pain outweighs the returns",
    "unmeasured": "fewer than two closes, no spread, or no downside to measure",
}

# The shipped risk-free assumption, percent per close -- the unit every return is already in, so a
# caller compares like with like. It is named on the reading rather than buried in the call.
RISK_FREE_PCT_PER_CLOSE = 0.0


def skewness_band(value: Optional[float]) -> str:
    """The traffic-light band for a per-close PnL skewness: good / average / bad.

    The settled table: above zero the tails are on the upside, down to
    `SKEWNESS_AVERAGE_AT_OR_ABOVE` the sample has no dominant tail, below it the losses are the
    tail. `None` and anything non-finite read `unmeasured` -- a sample too small to have a third
    moment is not a sample with a symmetric one, and a chip nobody could justify is the confident
    lie this band exists to avoid.
    """
    number = _finite(value)
    if number is None:
        return "unmeasured"
    if number > 0:
        return "good"
    return "average" if number >= SKEWNESS_AVERAGE_AT_OR_ABOVE else "bad"


def pnl_skewness(return_pcts: Sequence[float]) -> dict[str, Any]:
    """Where the tail risk sits, as the third standardized moment of the per-close returns.

    Population moments, so the figure is the sample's own shape rather than a bias-corrected
    estimate of a wider population's, and so a run's reading does not shift because one close was
    added. Negative means the losses are the tail -- small wins with occasional large losses -- and
    positive means the wins are.

    Fewer than three usable values is `None`: with two the moment is zero by construction, which
    would print "roughly symmetric" over a sample that cannot say so. A flat sample has no spread
    to divide by and is `None` for the same reason. Values that are missing, non-numeric, `NaN` or
    infinite are dropped rather than read as a break-even close.

    The moment is measured in units of the sample's own spread, so no intermediate can overflow: a
    magnitude whose raw squares and cubes do not fit a float still has a shape, and that shape is
    what ships. A reading that cannot be produced at all is `None` with the `unmeasured` band --
    never `NaN` and never `Infinity`, both of which the payload's strict serialization rejects.
    """
    sample = [value for value in (_finite(value) for value in return_pcts or [])
              if value is not None]
    n = len(sample)

    def unmeasured() -> dict[str, Any]:
        return {"pnl_skewness": None, "band": "unmeasured",
                "meaning": SKEWNESS_BAND_WORDS["unmeasured"], "n": n}

    if n < 3:
        return unmeasured()

    mean = statistics.fmean(sample)
    if not math.isfinite(mean):
        return unmeasured()

    # The spread is the unit everything below is divided by, which keeps every deviation inside
    # [-1, 1] and the third moment well inside a float.
    spread = max(abs(value - mean) for value in sample)
    if not math.isfinite(spread) or spread == 0:
        return unmeasured()

    deviations = [(value - mean) / spread for value in sample]
    second = sum(value * value for value in deviations) / n
    if second == 0:
        return unmeasured()

    score = _finite(round(sum(value ** 3 for value in deviations) / n / second ** 1.5, 6))
    if score is None:
        return unmeasured()
    band = skewness_band(score)
    return {"pnl_skewness": score, "band": band,
            "meaning": SKEWNESS_BAND_WORDS[band], "n": n}


def sortino_band(value: Optional[float]) -> str:
    """The traffic-light band for a Sortino ratio: good / average / bad.

    The settled table: at or above `SORTINO_GOOD_AT_OR_ABOVE` the returns are twice the downside
    pain, at or above `SORTINO_AVERAGE_AT_OR_ABOVE` they merely match it, and below that the pain
    outweighs them -- a negative reading included, which is an edge that pays nothing for its risk.
    `None` and anything non-finite read `unmeasured`.
    """
    number = _finite(value)
    if number is None:
        return "unmeasured"
    if number >= SORTINO_GOOD_AT_OR_ABOVE:
        return "good"
    return "average" if number >= SORTINO_AVERAGE_AT_OR_ABOVE else "bad"


def sortino_ratio(return_pcts: Sequence[float],
                  risk_free: float = RISK_FREE_PCT_PER_CLOSE) -> Optional[float]:
    """Return per unit of downside pain, against an explicit risk-free rate.

    The numerator is the mean EXCESS return and the denominator is the root-mean-square of the
    negative excess returns over their own count -- the same denominator `kpi.compute_trade_analytics`
    already uses, so at `risk_free = 0.0` the two agree to the last digit and the KPI payload stays
    the oracle for this one. Textbook versions divide by the total count; that would read a run
    with one bad close in fifty as nearly painless, which is the opposite of what this ratio is for.

    `risk_free` is percent PER CLOSE, the unit of every return handed in, and nothing here is
    annualized: trades are not daily observations, and annualizing a five-close sample would quote
    a rate the sample never earned. `None` for fewer than two usable returns, a sample with no
    spread, no negative excess return (nothing was paid for), or a non-finite `risk_free`.

    The downside deviation is computed in units of the widest loss, so no square overflows. That is
    not only a precision matter: a squared loss past the float range used to make the denominator
    `inf`, and a finite numerator over it returned `0.0` -- a measured-looking "no return at all"
    where the real reading was a small positive one. An intermediate that cannot be represented is
    `None`, never a figure.
    """
    rate = _finite(risk_free)
    if rate is None:
        return None
    sample = [value for value in (_finite(value) for value in return_pcts or [])
              if value is not None]
    if len(sample) < 2 or len(set(sample)) < 2:
        return None

    excess = [value - rate for value in sample]
    downside = [value for value in excess if value < 0]
    if not downside:
        return None

    scale = max(abs(value) for value in downside)
    deviation = scale * math.sqrt(sum((value / scale) ** 2 for value in downside) / len(downside))
    if not math.isfinite(deviation) or deviation == 0:
        return None

    mean_excess = statistics.fmean(excess)
    if not math.isfinite(mean_excess):
        return None
    return _finite(round(mean_excess / deviation, 6))


def sortino(return_pcts: Sequence[float],
            risk_free: float = RISK_FREE_PCT_PER_CLOSE) -> dict[str, Any]:
    """The Sortino reading with its band, its words, and the risk-free rate it assumed.

    The rate ships beside the number so the card can print the assumption: a Sortino is only
    meaningful against the rate it was measured over, and a reader who cannot see it has to guess.
    """
    sample = [value for value in (_finite(value) for value in return_pcts or [])
              if value is not None]
    score = sortino_ratio(sample, risk_free)
    band = sortino_band(score)
    return {"sortino_ratio": score, "risk_free_pct_per_close": _finite(risk_free),
            "band": band, "meaning": SORTINO_BAND_WORDS[band], "n": len(sample)}


def _equity_points(points: Sequence[Any]) -> tuple[list[tuple[float, Optional[float]]], int]:
    """The usable `(value, timestamp)` pairs of a caller-supplied curve, in the order given.

    `_close_field` reads a dict or a record, because a caller holds whichever shape it holds: the
    registry's account marks arrive as dicts while a test or a repair script may pass objects. A
    point whose value is not finite is dropped and counted rather than read as a crash to zero, and
    it is not a curve point -- the episode lengths count the points that were actually measured.

    The timestamp is optional and never substituted: a point without one still contributes its
    length in points, and contributes nothing to the seconds.
    """
    usable: list[tuple[float, Optional[float]]] = []
    skipped = 0
    for row in points or []:
        value = _finite(_close_field(row, "v"))
        if value is None:
            skipped += 1
            continue
        usable.append((value, _finite(_close_field(row, "ts"))))
    return usable, skipped


def drawdown_episodes(points: Sequence[Any],
                      starting_value: Optional[float] = None) -> list[dict[str, Any]]:
    """Every peak-to-recovery run the curve made, in curve order.

    Points are read in the order given and never sorted: an equity curve's order is its meaning, and
    a sorted one would report the drawdown of a run that never happened. A point at or above the
    running peak REGAINS it -- that is what recovered means on a capital curve, and it matches the
    drawdown walk in `kpi.compute_trade_analytics`, where a point back at the peak is a zero
    drawdown.

    Each episode carries its peak, its trough, the depth in dollars and (unless the peak is zero or
    below) as a share of that peak, whether it was regained, and -- only when it was -- its length in
    curve points and in seconds. An episode still open when the curve ends is returned with
    `recovered: False` and no length: a drawdown nobody has come back from yet has no duration, and
    reporting the length so far would put a still-running loss into the medians as a finished one.
    A depth or a duration no float can hold is `None` rather than an infinity the payload's strict
    serialization would reject, and a recovery timestamped before its own peak carries no seconds:
    the curve is read in the order given and is never sorted, so that is a real shape, but a
    negative wait is not a duration.
    """
    usable, _ = _equity_points(points)
    return _episodes_from(usable, starting_value)


def _episodes_from(usable: Sequence[tuple[float, Optional[float]]],
                   starting_value: Optional[float]) -> list[dict[str, Any]]:
    """The episode walk itself, over already-validated `(value, timestamp)` pairs."""
    peak = _finite(starting_value)
    peak_index = -1 if peak is not None else None
    peak_ts: Optional[float] = None
    trough: Optional[float] = None
    episodes: list[dict[str, Any]] = []

    def record(recovered: bool, index: int, ts: Optional[float]) -> None:
        depth = _finite(float(peak) - float(trough))
        elapsed = (_finite(float(ts) - float(peak_ts))
                   if (recovered and ts is not None and peak_ts is not None
                       and float(ts) >= float(peak_ts)) else None)
        episodes.append({
            "peak": float(peak),
            "trough": float(trough),
            "depth_usd": depth,
            "depth_pct": (_finite(100.0 * depth / float(peak))
                          if depth is not None and float(peak) > 0 else None),
            "recovered": recovered,
            "points": (index - int(peak_index)) if recovered else None,
            "seconds": elapsed,
        })

    for index, (value, ts) in enumerate(usable):
        if peak is None:
            peak, peak_index, peak_ts = value, index, ts
            continue
        if value >= peak:
            if trough is not None:
                record(True, index, ts)
            peak, peak_index, peak_ts, trough = value, index, ts, None
        elif trough is None or value < trough:
            trough = value

    if trough is not None:
        record(False, len(usable), None)
    return episodes


def drawdown_recovery(points: Sequence[Any],
                      starting_value: Optional[float] = None) -> dict[str, Any]:
    """How far a caller-supplied equity curve fell, and how long the drawdowns took to come back.

    Always present, like its sibling sections, and descriptive on purpose: the ticket settles that a
    recovery duration carries no band, because "recovered in four points is good" has no meaning
    without knowing how fast this strategy normally compounds. The medians read the REGAINED
    drawdowns only, and are read off the module's `_percentile` so `median` IS the median.

    `deepest_usd` is `0.0` on a curve that was read and never dropped -- a measurement -- and `None`
    on a curve with no usable point, which measured nothing, and on one whose only drawdown ran
    wider than a float can represent, which measured nothing this reader can name. The depth comes
    from the deepest episode whose depth is representable; the lengths and the waits still count
    every regained episode. `drawdown_recovery_sec` is omitted entirely when no regained episode
    carried both endpoint timestamps in order, because a zero there would read as an instant
    recovery.
    """
    usable, skipped = _equity_points(points)
    episodes = _episodes_from(usable, starting_value)
    regained = [episode for episode in episodes if episode["recovered"]]
    measured = [episode for episode in episodes if episode["depth_usd"] is not None]
    deepest = max(measured, key=lambda episode: episode["depth_usd"]) if measured else None

    def shape(value: Optional[float]) -> Optional[float]:
        return None if value is None else round(float(value), 6)

    def deepest_figure(key: str) -> Optional[float]:
        if not usable:
            return None
        if deepest is None:
            return 0.0 if not episodes else None
        return shape(deepest[key])

    out: dict[str, Any] = {
        "units": "curve_points_and_seconds",
        "episodes": len(regained),
        "open": len(episodes) - len(regained),
        "skipped": skipped,
        "deepest_usd": deepest_figure("depth_usd"),
        "deepest_pct": deepest_figure("depth_pct"),
        "deepest_recovered": None if deepest is None else bool(deepest["recovered"]),
    }

    lengths = [float(episode["points"]) for episode in regained]
    out["drawdown_recovery_trades"] = {
        "median": _percentile(lengths, 0.50),
        "p90": _percentile(lengths, 0.90),
        "n": len(lengths),
    }
    seconds = [float(episode["seconds"]) for episode in regained
               if episode["seconds"] is not None]
    if seconds:
        out["drawdown_recovery_sec"] = {
            "median": _percentile(seconds, 0.50),
            "p90": _percentile(seconds, 0.90),
            "n": len(seconds),
        }
    return out


def capital_risk(return_pcts: Sequence[float],
                 curve: Sequence[Any] = ()) -> dict[str, Any]:
    """The capital-level readings the Reports & Analytics card mounts (#517).

    Always present, like `robustness` and the three sections before it, and for the same reason: a
    caller must never have to branch on whether a reading exists. A run with nothing recorded
    reports `None` and `unmeasured`, which say "nothing to measure", never "no risk" and never
    "no edge".

    Two halves that read different sources and are deliberately independent. The banded readings --
    `pnl_skewness` and `sortino_ratio` -- read the per-close percentage returns the caller hands
    over; the descriptive `drawdown_recovery` reads the equity curve. One unrecorded writer must not
    blank the other half, and the drawdown half carries NO band: the ticket settles that a recovery
    duration has no intrinsic good or bad, because it has nothing to be judged against.

    Each band is computed here, from the same rounded figure it ships with, so the chip a reader
    sees cannot disagree with the number beside it. The confidence-interval helper and the system
    quality number are the module's own (`robustness`), reused rather than restated.
    """
    sample = [value for value in (_finite(value) for value in return_pcts or [])
              if value is not None]
    return {
        "units": "percent_per_close_and_curve_points",
        "pnl_skewness": pnl_skewness(sample),
        "sortino_ratio": sortino(sample),
        "drawdown_recovery": drawdown_recovery(curve),
    }


def probability_bell(fills: Sequence[dict]) -> dict[str, Any]:
    """Where our executions landed across the probability range.

    Built from executed prices, not from quotes: what we asked for is a
    intention, what filled is the distribution the strategy actually holds.
    `theoretical_pdf` is a normal curve fitted to those same samples, drawn for
    comparison only -- it is never a source of counts.
    """
    prices = [p for p in (_finite(f.get("price")) for f in fills or [])
              if p is not None and 0.0 < p < 1.0]
    if not prices:
        return {"bins": [], "samples_count": 0, "sweet_spot_pct": None}

    mean = statistics.fmean(prices)
    stdev = statistics.stdev(prices) if len(prices) > 1 else None
    width = 1.0 / BELL_BINS
    bins = []
    for i in range(BELL_BINS):
        low = i * width
        high = low + width
        centre = (low + high) / 2.0
        count = sum(1 for p in prices
                    if low <= p < high or (i == BELL_BINS - 1 and p == high))
        if stdev and stdev > 0:
            pdf = (1.0 / (stdev * math.sqrt(2 * math.pi))) * math.exp(
                -0.5 * ((centre - mean) / stdev) ** 2)
        else:
            # One sample, or every fill at one price: there is no curve to fit.
            pdf = None
        bins.append({
            "bin": round(centre, 4),
            "empirical_count": count,
            "theoretical_pdf": None if pdf is None else round(pdf, 6),
            "in_sweet_spot": SWEET_SPOT_LOW <= centre <= SWEET_SPOT_HIGH,
        })

    in_band = sum(1 for p in prices if SWEET_SPOT_LOW <= p <= SWEET_SPOT_HIGH)
    return {
        "bins": bins,
        "samples_count": len(prices),
        # Multiplier for the mean of THESE samples, shipped beside them, and None exactly
        # when there is no spread to scale (#535).
        "ci_critical": ci_critical_values(len(prices)) if len(prices) > 1 else None,
        "sweet_spot_pct": round(100.0 * in_band / len(prices), 2),
        "mean": round(mean, 6),
        "stdev": None if stdev is None else round(stdev, 6),
    }


def markout(markouts: Sequence[dict]) -> dict[str, Any]:
    """Post-fill price displacement per horizon, size-weighted, in bps.

    Only matured horizons count. An unmatured fill has no displacement yet, and
    entering it as 0 bps would report "no adverse selection" for a measurement
    that has not happened. A horizon nobody has matured into is omitted rather
    than shown at zero.

    Unsigned, mean, in bps -- this is the chart's reading, and it counts every row whatever the
    side. Its signed, median, banded counterpart is `signed_markout()`; the two share a loop so
    they cannot disagree about which fills count, and they answer different questions about the
    same rows. `tests/test_markout_robustness_analytics.py` pins that this output did not move
    when the loop was shared.
    """
    intervals = []
    for label, samples in _markout_samples(markouts, MARKOUT_HORIZONS,
                                           lambda row: 1.0).items():
        # The chart's unit, named here: bps of the fill price. A 1c move on a 50c fill is 200bps.
        weighted = sum((delta / fill) * 10_000.0 * size for delta, fill, size in samples)
        weight = sum(size for _, _, size in samples)
        intervals.append({
            "horizon": label,
            "displacement_bps": round(weighted / weight, 4),
            "samples": len(samples),
        })
    return {"intervals": intervals}


def monte_carlo(returns_usd: Sequence[float], starting_capital: float,
                paths: int = MC_PATHS, cycles: int = MC_CYCLES,
                seed: int = 20260831) -> Optional[dict[str, Any]]:
    """Bootstrap the observed per-position returns forward, as a percentile fan.

    None below `MIN_MC_SAMPLES`: resampling two or three closes produces a fan
    whose width is a property of the bootstrap, not of the strategy, and a
    chart cannot say that about itself. The seed is fixed so the same registry
    renders the same fan -- an envelope that shifts on every poll reads as new
    information when nothing changed.
    """
    sample = [r for r in (_finite(r) for r in returns_usd or []) if r is not None]
    if len(sample) < MIN_MC_SAMPLES or not starting_capital or starting_capital <= 0:
        return None

    rng = random.Random(seed)
    equity = [[float(starting_capital)] for _ in range(paths)]
    for _ in range(cycles):
        for path in equity:
            path.append(path[-1] + rng.choice(sample))

    steps = []
    for cycle in range(cycles + 1):
        column = sorted(path[cycle] for path in equity)

        def pct(p: float) -> float:
            idx = min(len(column) - 1, max(0, int(round(p * (len(column) - 1)))))
            return round(100.0 * column[idx] / starting_capital, 4)

        steps.append({"cycle": cycle, "p01": pct(0.01), "p10": pct(0.10),
                      "p50": pct(0.50), "p90": pct(0.90), "p99": pct(0.99)})

    finals = [path[-1] for path in equity]
    positive = sum(1 for f in finals if f > starting_capital)
    worst = min((min(path) - starting_capital) / starting_capital for path in equity)
    return {
        "steps": steps,
        "paths": paths,
        "samples_count": len(sample),
        "prob_positive_return": round(100.0 * positive / paths, 2),
        "worst_case_drawdown_pct": round(100.0 * worst, 4),
    }


def build(closes: Sequence[dict], fills: Sequence[dict],
          markouts: Sequence[dict], starting_capital: float,
          quotes: Optional[Sequence[Any]] = None,
          equity_curve: Optional[Sequence[Any]] = None) -> dict[str, Any]:
    """The whole `statistical_analytics` payload the analytics tab reads.

    The charts come and go with their input; `fill_path_edge`, `rescue_friction`,
    `queue_realism`, `robustness` and `capital_risk` are always present, because they summarise the
    closes, the fills, the quotes and the curve this call already holds and a caller must never have
    to branch on whether a reading exists.

    `quotes` is optional and additive. The queue-realism section reads the placement price
    and timestamp a close never carries, so a caller without quotes gets that section at
    zero counts rather than a missing key -- and a caller with them gets the one part of it
    the registry can answer today, the wait between placement and the first credited fill.

    `equity_curve` is optional and additive for the same reason. The capital-risk section reads a
    curve this call cannot assemble (realised closes stacked with the venue's own float marks), so a
    caller without one gets every drawdown figure `None` rather than a curve invented from the
    closes it happens to hold. `kpi.report` supplies the venue account-value curve it already emits.
    """
    returns = position_returns(closes)
    records = cleared_fills(quotes or [], fills)
    payload: dict[str, Any] = {
        "position_returns": returns,
        "closed_positions": returns["positions"],
        "pair_costs": pair_costs(closes),
        "fill_path_edge": fill_path_edge(closes),
        "rescue_friction": rescue_friction_summary(closes),
        "queue_realism": {
            "cleared_fill_count": len(records),
            "trade_through_depth": trade_through_depth(records),
            "fill_latency": fill_latency(records),
        },
        # The robustness gates (#516) read the closes and the markout rows, both of which this
        # call already holds -- no new parameter, and no section above it changes.
        "robustness": robustness(closes, markouts),
        # The capital-level readings (#517) take the per-close percentages the distribution above
        # already measured -- one definition of a close's return, never a second -- and the equity
        # curve from the caller. No section above them changes.
        "capital_risk": capital_risk(
            [position["pnl_pct"] for position in returns["positions"]
             if position["pnl_pct"] is not None],
            equity_curve or ()),
        "probability_bell": probability_bell(fills),
        "markout": markout(markouts),
    }
    fan = monte_carlo([p["pnl_usd"] for p in returns["positions"]],
                      starting_capital)
    if fan is not None:
        payload["monte_carlo"] = fan
    return payload
