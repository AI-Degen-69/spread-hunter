"""The statistics surface the analytics charts read, built from the registry.

The Performance & Analytics tab has five charts — position-return distribution,
pair-cost density, outcome-probability bell, post-fill markout, Monte Carlo —
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

import math
import random
import statistics
from typing import Any, Optional, Sequence

# Merge methods, mirroring `registry_state.MERGE_CLOSE_METHODS`. A merged pair
# is the strategy's own exit; anything else unwound some other way, and the
# distribution chart splits the two.
MERGE_METHODS = ("merge", "shadow_merge")

# The single-buy exits: a leg whose partner never filled, sold back through the
# book. Mirrors `kpi.SINGLE_BUY_EXIT_METHODS`,
# `statistical_validation_run.artifacts.EXIT_METHODS` and
# `ladder.LADDER_EXIT_METHODS`, and shares `artifacts.rescue_stats`'
# merges-plus-exits denominator. Declared rather than imported because `kpi`
# imports THIS module, so the reverse import is a cycle; the copies are pinned
# against each other in `tests/test_fill_path_analytics.py`.
RESCUE_METHODS = ("single_buy_exit", "naked_exit", "ladder_exit")

# Bookkeeping rows the account sweep writes against a market. Nothing was sold
# and no position was lost, so they belong to no path. Mirrors
# `kpi.NON_TRADE_CLOSE_METHODS`.
EXCLUDED_METHODS = ("venue_sync",)

# The assumed stop reference for a rescue close that recorded no `trigger_bid`. It is an
# ASSUMPTION, not an observed price: it equals the system default in `core_brain/config.py`
# (`single_buy_max_loss_usd = 0.04`, promoted 2026-10-12), a per-share loss limit that
# profile presets move (0.030-0.065). Every figure derived from it is tagged
# `assumed_offset` and counted apart from the recorded ones.
STOP_OFFSET_FALLBACK = 0.04

# The venue pays exactly $1.00 for a merged pair, so pair cost lives just under
# it. These bounds frame the density chart around the only region that matters.
PAIR_COST_FLOOR = 0.90
PAIR_COST_CEILING = 1.00
PAIR_COST_BINS = 12

# The quoting band the strategy targets. `renderProbabilityBellChart` shades it.
SWEET_SPOT_LOW = 0.15
SWEET_SPOT_HIGH = 0.85
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
    """
    positions: list[dict[str, Any]] = []
    for row in closes or []:
        pnl_usd = _finite(row.get("realized_pnl"))
        if pnl_usd is None:
            continue
        cost = _finite(row.get("cost_basis"))
        pnl_pct = (100.0 * pnl_usd / cost) if cost and cost > 0 else None
        method = str(row.get("method") or "").lower()
        positions.append({
            "ts": _finite(row.get("ts")),
            "condition_id": row.get("condition_id"),
            "market_slug": row.get("market_slug"),
            "pnl_usd": round(pnl_usd, 6),
            "pnl_pct": None if pnl_pct is None else round(pnl_pct, 6),
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
    absolute bid floor and `single_buy_max_loss_usd` is a per-profile per-share
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

    return {
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

    `trigger_bid` is the recorded bid that fired the stop. Only the lifecycle hard stop
    writes it, so the other rescue routes fall back to `entry - STOP_OFFSET_FALLBACK`,
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
    """
    intervals = []
    for column, label in MARKOUT_HORIZONS:
        weighted = 0.0
        weight = 0.0
        samples = 0
        for row in markouts or []:
            later = _finite(row.get(column))
            fill = _finite(row.get("fill_price"))
            if later is None or fill is None or fill <= 0:
                continue
            size = _finite(row.get("size")) or 1.0
            if size <= 0:
                continue
            # bps of the fill price: a 1c move on a 50c fill is 200bps.
            weighted += ((later - fill) / fill) * 10_000.0 * size
            weight += size
            samples += 1
        if samples == 0:
            continue
        intervals.append({
            "horizon": label,
            "displacement_bps": round(weighted / weight, 4),
            "samples": samples,
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
          markouts: Sequence[dict], starting_capital: float) -> dict[str, Any]:
    """The whole `statistical_analytics` payload the analytics tab reads."""
    returns = position_returns(closes)
    payload: dict[str, Any] = {
        "position_returns": returns,
        "closed_positions": returns["positions"],
        "pair_costs": pair_costs(closes),
        "fill_path_edge": fill_path_edge(closes),
        "probability_bell": probability_bell(fills),
        "markout": markout(markouts),
    }
    fan = monte_carlo([p["pnl_usd"] for p in returns["positions"]],
                      starting_capital)
    if fan is not None:
        payload["monte_carlo"] = fan
    return payload
