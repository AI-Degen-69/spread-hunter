"""Time-series Sharpe ratio shared by the tournament cockpit and re-evaluation.

Issue #875: the cockpit used to report ``mean_pnl / std_pnl * 10`` — a per-trade
PnL ratio scaled by ten — under the Sharpe label. This module is the single
source of truth for the real statistic:

    Sharpe Ratio = mean(excess return) / sample-stdev(excess return),
    annualised over a 365-day year.

Returns are measured on portfolio value over fixed clock buckets (default
5 minutes), carrying the last known value forward so quiet periods count as
zero-return steps. Unmeasurable input returns ``None`` — never ``NaN`` and never
a fabricated ``0.0``.

Deliberately separate from ``core_brain.kpi.compute_trade_analytics``: that
per-trade Sharpe is intentional (trades are not daily observations, so it must
not be annualised) and pinned by the golden payload. Do not merge the two.
"""
from __future__ import annotations

import math
import os

#: Seconds in a 365-day year — the annualisation basis for every surface.
SECONDS_PER_YEAR = 31_536_000

#: Unit suffix contract: yearly decimal ratio, ``0.05`` means 5% per year.
RISK_FREE_RATE_ENV_VAR = "BOT_SHARPE_RISK_FREE_RATE"

#: Plain formula text reused by every surface label/tooltip.
SHARPE_FORMULA_TEXT = (
    "Sharpe Ratio = mean(portfolio excess return) / "
    "stdev(portfolio excess return), annualised x sqrt(periods per year)"
)


def sharpe_ratio(
    points: list[tuple[float, float]],
    risk_free_rate_annual: float = 0.0,
    bucket_seconds: int = 300,
) -> float | None:
    """Annualised Sharpe ratio over fixed clock buckets, or ``None``.

    ``points`` are ``(epoch seconds, portfolio value in dollars)`` pairs.
    Points with non-finite time/value or non-positive value are dropped; the
    rest are sorted by time (stable; last wins on duplicate timestamps) and
    resampled onto a ``bucket_seconds`` grid carrying the last known value
    forward. ``None`` means unmeasurable: no valid points, fewer than 2 step
    returns, or zero spread.
    """
    if not math.isfinite(risk_free_rate_annual):
        raise ValueError(
            f"risk_free_rate_annual={risk_free_rate_annual!r} is not finite"
        )
    if not isinstance(bucket_seconds, int) or isinstance(bucket_seconds, bool):
        raise ValueError(f"bucket_seconds={bucket_seconds!r} must be an int")
    if bucket_seconds <= 0:
        raise ValueError(f"bucket_seconds={bucket_seconds!r} must be positive")

    cleaned: list[tuple[float, float]] = []
    for ts, value in points:
        try:
            t = float(ts)
            v = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(t) or not math.isfinite(v) or v <= 0.0:
            continue
        cleaned.append((t, v))
    if not cleaned:
        return None

    cleaned.sort(key=lambda p: p[0])
    deduped: list[tuple[float, float]] = []
    for t, v in cleaned:
        if deduped and deduped[-1][0] == t:
            deduped[-1] = (t, v)
        else:
            deduped.append((t, v))

    first_t = deduped[0][0]
    last_t = deduped[-1][0]
    grid_end = first_t
    while grid_end < last_t:
        grid_end += bucket_seconds
    grid_values: list[float] = []
    idx = 0
    current = deduped[0][1]
    t = first_t
    while t <= grid_end:
        while idx + 1 < len(deduped) and deduped[idx + 1][0] <= t:
            idx += 1
            current = deduped[idx][1]
        grid_values.append(current)
        t += bucket_seconds

    step_returns = [
        grid_values[i + 1] / grid_values[i] - 1.0
        for i in range(len(grid_values) - 1)
    ]
    if len(step_returns) < 2:
        return None

    per_step_risk_free = (1.0 + risk_free_rate_annual) ** (
        bucket_seconds / SECONDS_PER_YEAR
    ) - 1.0
    excess = [r - per_step_risk_free for r in step_returns]
    mean = sum(excess) / len(excess)
    var = sum((e - mean) ** 2 for e in excess) / (len(excess) - 1)
    if var <= 0.0:
        return None
    return mean / math.sqrt(var) * math.sqrt(SECONDS_PER_YEAR / bucket_seconds)


def risk_free_rate_from_env() -> float:
    """Yearly decimal risk-free rate from ``BOT_SHARPE_RISK_FREE_RATE``.

    Unset or blank reads as ``0.0`` (USDC default). Anything else must parse as
    a finite ``0.0..1.0`` ratio — ``_bounded_float`` raises the named error.
    No ``HUNTER_*`` alias: one name only.
    """
    from core_brain.config import _bounded_float

    raw = os.environ.get(RISK_FREE_RATE_ENV_VAR)
    if raw is None or not raw.strip():
        return 0.0
    return _bounded_float(RISK_FREE_RATE_ENV_VAR, raw, 0.0, 1.0)
