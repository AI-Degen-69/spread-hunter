"""Market metrics telemetry script for volume, notional, and depth research.

Discovers live binary markets on Polymarket via Gamma API, samples a subset,
measures 24h volume, 30m traded notional, and top-3 bid book depth,
and displays statistical distribution summaries (min, p25, median, p75, max, mean).

Completely read-only: zero live trading, no cancellations, no database writes.
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import math
import random
import statistics
import sys
import time
from pathlib import Path
from typing import Mapping, Sequence

import requests

from core_brain.runtime_paths import LIVE_ROOT
from scoring.markets import full_book, parse_book
from scoring.selector import top_depth_usd
from scripts.live_events_probe import _event_list

log = logging.getLogger("market_metrics")

GAMMA_HOST = "https://gamma-api.polymarket.com"
CLOB_HOST = "https://clob.polymarket.com"
TRADES_API = "https://data-api.polymarket.com/trades"

GAMMA_PAGE_SIZE = 100
GAMMA_MAX_PAGES = 5
TRADE_PAGE_LIMIT = 500
TRADE_MAX_PAGES = 10
WINDOW_SECONDS = 1800
DEFAULT_SAMPLE_SIZE = 50
MAX_SAMPLE_SIZE = 100

TIMEOUT = (3.05, 5.0)


def active_markets(
    session: requests.Session | None = None,
    limit_per_page: int = GAMMA_PAGE_SIZE,
    max_pages: int = GAMMA_MAX_PAGES,
    gamma_host: str = GAMMA_HOST,
) -> list[dict]:
    """Fetch active, open markets from Gamma API across multiple pages."""
    s = session or requests.Session()
    out: list[dict] = []
    offset = 0
    url = f"{gamma_host}/markets"
    for _ in range(max_pages):
        r = s.get(
            url,
            params={
                "active": "true",
                "closed": "false",
                "limit": limit_per_page,
                "offset": offset,
            },
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        page = _event_list(r.json())
        if page is None:
            raise ValueError("the payload is not a list of markets")
        if not page:
            break
        out.extend(page)
        offset += len(page)
        if len(page) < limit_per_page:
            break
    return out


def parse_binary_tokens(market: dict) -> tuple[str, str] | None:
    """Validate binary market outcomes and return (yes_token, no_token) or None."""
    if not isinstance(market, dict):
        return None
    raw_tokens = market.get("clobTokenIds")
    if isinstance(raw_tokens, str):
        try:
            tokens = json.loads(raw_tokens)
        except (ValueError, TypeError):
            return None
    elif isinstance(raw_tokens, list):
        tokens = raw_tokens
    else:
        return None

    if not isinstance(tokens, list) or len(tokens) != 2:
        return None

    # Disallow bools (bool is int subclass) or empty tokens
    if not all(
        isinstance(t, (str, int)) and not isinstance(t, bool) and str(t).strip()
        for t in tokens
    ):
        return None

    t0, t1 = str(tokens[0]).strip(), str(tokens[1]).strip()

    # Check outcomes if available to order (YES, NO) consistently
    raw_outcomes = market.get("outcomes")
    if isinstance(raw_outcomes, str):
        try:
            outcomes = json.loads(raw_outcomes)
        except (ValueError, TypeError):
            outcomes = None
    elif isinstance(raw_outcomes, list):
        outcomes = raw_outcomes
    else:
        outcomes = None

    if isinstance(outcomes, list) and len(outcomes) == 2:
        o0, o1 = str(outcomes[0]).strip().lower(), str(outcomes[1]).strip().lower()
        if o0 in ("no", "down") and o1 in ("yes", "up"):
            return (t1, t0)

    return (t0, t1)


def sample_markets(
    markets: list[dict],
    size: int = DEFAULT_SAMPLE_SIZE,
    seed: int | None = None,
) -> list[dict]:
    """Sample up to `size` binary markets reproducibly."""
    usable = [
        m for m in markets
        if isinstance(m, dict) and parse_binary_tokens(m) is not None
    ]
    if len(usable) <= size:
        return list(usable)
    rng = random.Random(seed)
    return rng.sample(usable, size)


def measure_market_volume(market: dict) -> float | None:
    """Extract 24-hour volume in USD from market payload."""
    if not isinstance(market, dict):
        return None
    for field in ("volume24hr", "volume_24h", "volume24h", "volume"):
        val = market.get(field)
        if val is not None:
            try:
                v = float(val)
                if not math.isnan(v) and v >= 0:
                    return v
            except (ValueError, TypeError):
                continue
    return None


def measure_outcome_depth(
    clob_host: str,
    token_id: str,
    session: requests.Session | None = None,
) -> float:
    """Fetch order book for a single token and calculate top-3 bid depth in USD."""
    if session is not None:
        r = session.get(f"{clob_host}/book", params={"token_id": token_id}, timeout=TIMEOUT)
        r.raise_for_status()
        book = parse_book(r.json(), token_id)
    else:
        book = full_book(clob_host, token_id)

    bids = book.get("bids", {})
    return top_depth_usd(bids, count=3)


def measure_market_depth(
    clob_host: str,
    yes_token: str,
    no_token: str,
    session: requests.Session | None = None,
) -> float:
    """Calculate bottleneck top-3 bid depth across both binary outcomes (min)."""
    d_yes = measure_outcome_depth(clob_host, yes_token, session=session)
    d_no = measure_outcome_depth(clob_host, no_token, session=session)
    return min(d_yes, d_no)


def fetch_window_trades(
    session: requests.Session | None,
    condition_id: str,
    now: float,
    window_seconds: int = WINDOW_SECONDS,
    max_pages: int = TRADE_MAX_PAGES,
    page_limit: int = TRADE_PAGE_LIMIT,
    trades_api: str = TRADES_API,
) -> tuple[list[dict], bool]:
    """Paginate recent trades within the window [now - window_seconds, now].

    Returns (trades, window_complete). If max_pages is reached before seeing a
    short page or crossing the window cutoff, window_complete is False.
    """
    cutoff = now - window_seconds
    s = session or requests.Session()
    all_trades: list[dict] = []
    window_complete = True

    for page_index in range(max_pages):
        offset = page_index * page_limit
        r = s.get(
            trades_api,
            params={
                "market": condition_id,
                "limit": page_limit,
                "offset": offset,
                "takerOnly": "true",
            },
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        page = r.json()
        if not isinstance(page, list) or not page:
            break
        all_trades.extend(t for t in page if isinstance(t, dict))
        if len(page) < page_limit:
            break

        timestamps: list[float] = []
        for t in page:
            if isinstance(t, dict):
                try:
                    timestamps.append(float(t.get("timestamp") or 0.0))
                except (TypeError, ValueError):
                    pass

        if timestamps and min(timestamps) <= cutoff:
            break

        if page_index == max_pages - 1:
            window_complete = False

    return all_trades, window_complete


def notional_in_window(trades: list[dict], cutoff: float) -> float:
    """Deduplicate trades and sum traded notional (price * size) where ts >= cutoff."""
    seen: set[tuple] = set()
    total = 0.0
    for t in trades:
        if not isinstance(t, dict):
            continue
        try:
            ts = float(t.get("timestamp") or 0.0)
            if ts < cutoff:
                continue
            price = float(t.get("price") or 0.0)
            size = float(t.get("size") or 0.0)
            if price <= 0 or size <= 0:
                continue
        except (ValueError, TypeError):
            continue

        tx_hash = t.get("transactionHash")
        asset = str(t.get("asset") or "")
        key = (tx_hash if tx_hash else id(t), asset, ts, price, size)
        if key in seen:
            continue
        seen.add(key)
        total += price * size

    return total


def nearest_rank(values: Sequence[float], fraction: float) -> float:
    """Compute percentile using nearest rank method (ceil(fraction * n) - 1)."""
    if not values:
        raise ValueError("Cannot compute nearest rank on empty sequence")
    n = len(values)
    k = math.ceil(fraction * n)
    idx = max(0, min(n - 1, k - 1))
    return values[idx]


def summarize_metric(values: Sequence[float | None]) -> dict:
    """Compute distribution statistics for a metric series."""
    clean = sorted([
        float(v) for v in values
        if v is not None and not math.isnan(float(v))
    ])
    missing = len(values) - len(clean)
    if not clean:
        return {
            "count": 0,
            "min": None,
            "p25": None,
            "median": None,
            "p75": None,
            "max": None,
            "mean": None,
            "missing": missing,
        }

    return {
        "count": len(clean),
        "min": clean[0],
        "p25": nearest_rank(clean, 0.25),
        "median": nearest_rank(clean, 0.50),
        "p75": nearest_rank(clean, 0.75),
        "max": clean[-1],
        "mean": statistics.fmean(clean),
        "missing": missing,
    }


def format_table(
    summaries: Mapping[str, dict],
    sample_size: int,
    pool_size: int,
    seed: int,
    error_count: int = 0,
    incomplete_windows: int = 0,
) -> str:
    """Format an aligned ASCII table summarizing metric distributions."""
    headers = ["Metric", "Count", "Min ($)", "P25 ($)", "Median ($)", "Mean ($)", "P75 ($)", "Max ($)"]
    rows = []

    def _fmt(val: float | None) -> str:
        if val is None:
            return "N/A"
        return f"{val:,.2f}"

    for key, label in (
        ("volume_24h", "24h Volume"),
        ("recent_traded_notional_30m", "30m Traded Notional"),
        ("top3_bid_depth", "Top-3 Bid Depth"),
    ):
        s = summaries.get(key, {})
        rows.append([
            label,
            str(s.get("count", 0)),
            _fmt(s.get("min")),
            _fmt(s.get("p25")),
            _fmt(s.get("median")),
            _fmt(s.get("mean")),
            _fmt(s.get("p75")),
            _fmt(s.get("max")),
        ])

    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(cell))

    # Build border and content
    sep = "+" + "+".join("-" * (w + 2) for w in col_widths) + "+"
    header_str = "|" + "|".join(f" {headers[i].ljust(col_widths[i])} " for i in range(len(headers))) + "|"
    lines = [
        "",
        "=== Polymarket Market Metrics Distribution Telemetry ===",
        sep,
        header_str,
        sep,
    ]
    for row in rows:
        line = "|" + "|".join(
            f" {row[i].ljust(col_widths[i]) if i == 0 else row[i].rjust(col_widths[i])} "
            for i in range(len(row))
        ) + "|"
        lines.append(line)
    lines.append(sep)
    lines.append(f"Sample size: {sample_size} (drawn from pool of {pool_size} active binary markets)")
    lines.append(f"Sampling seed: {seed}")
    if error_count > 0:
        lines.append(f"Encountered {error_count} market sampling errors during book/trade probes")
    if incomplete_windows > 0:
        lines.append(f"Notice: {incomplete_windows} markets reached the {TRADE_MAX_PAGES}-page trade pagination limit")
    lines.append("")
    return "\n".join(lines)


def build_report(
    summaries: Mapping[str, dict],
    market_records: Sequence[dict],
    seed: int,
    sample_size: int,
    pool_size: int,
    error_count: int = 0,
    incomplete_windows: int = 0,
) -> dict:
    """Build structured JSON telemetry report dictionary."""
    return {
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "parameters": {
            "sample_size": sample_size,
            "pool_size": pool_size,
            "seed": seed,
            "window_seconds": WINDOW_SECONDS,
            "trade_page_limit": TRADE_PAGE_LIMIT,
            "trade_max_pages": TRADE_MAX_PAGES,
        },
        "telemetry_summary": {
            "error_count": error_count,
            "incomplete_windows": incomplete_windows,
        },
        "distributions": dict(summaries),
        "markets": list(market_records),
    }


def main(argv: Sequence[str] | None = None) -> int:
    """CLI execution entrypoint."""
    parser = argparse.ArgumentParser(
        description="Market metrics telemetry: volume, notional, and depth distribution.",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=DEFAULT_SAMPLE_SIZE,
        help=f"Number of active binary markets to sample (1..{MAX_SAMPLE_SIZE}, default: {DEFAULT_SAMPLE_SIZE})",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Optional pseudo-random integer seed for market sampling reproduction",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="Save report to reports/market_metrics_statistics_report_<timestamp>.json",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Custom destination filepath for the JSON report",
    )

    args = parser.parse_args(argv)

    if args.sample_size < 1 or args.sample_size > MAX_SAMPLE_SIZE:
        sys.stderr.write(
            f"Error: --sample-size must be between 1 and {MAX_SAMPLE_SIZE} (got {args.sample_size})\n"
        )
        return 2

    seed = args.seed if args.seed is not None else random.randint(1, 1_000_000)

    session = requests.Session()
    session.headers.update({"User-Agent": "spread-hunter-research/1.0"})

    try:
        raw_markets = active_markets(session=session)
    except Exception as exc:
        sys.stderr.write(f"Fatal: Failed to query active markets from venue: {exc}\n")
        return 1

    sample = sample_markets(raw_markets, size=args.sample_size, seed=seed)
    if not sample:
        sys.stderr.write("Fatal: No usable binary markets discovered in active pool\n")
        return 1

    records: list[dict] = []
    error_count = 0
    incomplete_windows_count = 0
    now = time.time()

    vol_vals: list[float | None] = []
    notional_vals: list[float | None] = []
    depth_vals: list[float | None] = []

    for m in sample:
        cid = str(m.get("conditionId") or "")
        slug = str(m.get("slug") or "")
        tokens = parse_binary_tokens(m)
        if not tokens:
            continue
        yes_tok, no_tok = tokens

        vol = measure_market_volume(m)
        vol_vals.append(vol)

        d_val: float | None = None
        try:
            d_val = measure_market_depth(CLOB_HOST, yes_tok, no_tok, session=session)
        except Exception:
            error_count += 1
        depth_vals.append(d_val)

        notional_val: float | None = None
        window_complete = True
        try:
            trades, window_complete = fetch_window_trades(
                session=session,
                condition_id=cid,
                now=now,
                window_seconds=WINDOW_SECONDS,
            )
            notional_val = notional_in_window(trades, cutoff=now - WINDOW_SECONDS)
            if not window_complete:
                incomplete_windows_count += 1
        except Exception:
            error_count += 1
        notional_vals.append(notional_val)

        records.append({
            "condition_id": cid,
            "slug": slug,
            "volume_24h": vol,
            "top3_bid_depth": d_val,
            "recent_traded_notional_30m": notional_val,
            "window_complete": window_complete,
        })

    summaries = {
        "volume_24h": summarize_metric(vol_vals),
        "recent_traded_notional_30m": summarize_metric(notional_vals),
        "top3_bid_depth": summarize_metric(depth_vals),
    }

    table_output = format_table(
        summaries=summaries,
        sample_size=len(records),
        pool_size=len(raw_markets),
        seed=seed,
        error_count=error_count,
        incomplete_windows=incomplete_windows_count,
    )
    print(table_output)

    if args.save or args.output:
        report_data = build_report(
            summaries=summaries,
            market_records=records,
            seed=seed,
            sample_size=len(records),
            pool_size=len(raw_markets),
            error_count=error_count,
            incomplete_windows=incomplete_windows_count,
        )
        if args.output:
            out_path = Path(args.output)
        else:
            stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
            reports_dir = LIVE_ROOT / "reports"
            reports_dir.mkdir(parents=True, exist_ok=True)
            out_path = reports_dir / f"market_metrics_statistics_report_{stamp}.json"

        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=2)
            f.write("\n")
        print(f"Report saved to: {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
