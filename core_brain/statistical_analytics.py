"""The statistics surface the analytics charts read, built from the registry.

The Performance & Analytics tab has five charts — position-return distribution,
pair-cost density, outcome-probability bell, post-fill markout, Monte Carlo —
and every one of them reads `kpi.statistical_analytics`. Nothing ever produced
that key: not `kpi.report`, not the TS bridge, at any commit in this repo's
history. So the charts have shown "unmeasured" since the day they were written,
including on runs that closed positions at a profit.

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
        "probability_bell": probability_bell(fills),
        "markout": markout(markouts),
    }
    fan = monte_carlo([p["pnl_usd"] for p in returns["positions"]],
                      starting_capital)
    if fan is not None:
        payload["monte_carlo"] = fan
    return payload
