"""live/engine/market_feed.py - Market feed reading graduated markets from run/markets.json.

Reads the ranker's output (8 graduated markets) directly from disk without
re-deriving the funnel and without importing across the simulation boundary.
Handles missing, empty, or stale feed files explicitly.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

LIVE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = LIVE_ROOT.parent
DEFAULT_MARKETS_PATH = REPO_ROOT / "run" / "markets.json"

# Default maximum age before a feed file is considered stale (e.g. 24 hours).
DEFAULT_MAX_STALENESS_SEC: float = 86400.0


class MarketFeedError(RuntimeError):
    """Base error for market feed issues."""


class MarketFeedAbsentError(MarketFeedError):
    """Raised when run/markets.json is absent on disk."""


class MarketFeedStaleError(MarketFeedError):
    """Raised when run/markets.json is older than allowed staleness threshold."""


@dataclass(frozen=True)
class GraduatedMarket:
    cid: str
    min_size: float
    tick: float
    max_spread: float
    days_to_resolve: float
    source: str
    daily: float
    slug: str = ""
    title: str = ""
    shares: int = 120
    est_income: float = 0.0
    est_capital: float = 120.0
    return_pct_day: float = 0.0
    their_score: float = 0.0
    volume_24h: float = 0.0
    spread: float = 0.01
    eligible: bool = True
    reject_reason: str = ""


def load_graduated_markets(
    path: Path | str | None = None,
    max_age_sec: Optional[float] = None,
) -> list[GraduatedMarket]:
    """Read graduated markets from run/markets.json with staleness and existence checks.

    Raises `MarketFeedAbsentError` if file is missing.
    Raises `MarketFeedStaleError` if file mtime exceeds `max_age_sec`.
    Raises `MarketFeedError` if file is empty or malformed.
    """
    target = Path(path) if path is not None else DEFAULT_MARKETS_PATH

    if not target.is_file():
        raise MarketFeedAbsentError(
            f"graduated markets feed missing at {target}. Run scripts/rank_markets.py first."
        )

    try:
        stat = target.stat()
    except OSError as e:
        raise MarketFeedAbsentError(f"unable to stat {target}: {e}") from e

    if stat.st_size == 0:
        raise MarketFeedError(f"graduated markets feed at {target} is empty (0 bytes)")

    if max_age_sec is not None and max_age_sec > 0:
        age = time.time() - stat.st_mtime
        if age > max_age_sec:
            raise MarketFeedStaleError(
                f"graduated markets feed at {target} is stale: age {age:.0f}s > {max_age_sec:.0f}s"
            )

    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception as e:
        raise MarketFeedError(f"failed to parse JSON from {target}: {e}") from e

    if not isinstance(data, list):
        raise MarketFeedError(
            f"graduated markets feed at {target} must contain a JSON list, got {type(data).__name__}"
        )

    out: list[GraduatedMarket] = []
    for idx, row in enumerate(data):
        if not isinstance(row, dict):
            raise MarketFeedError(f"row {idx} in {target} is not a dictionary")
        if "cid" not in row:
            raise MarketFeedError(f"row {idx} in {target} missing required field 'cid'")

        try:
            gm = GraduatedMarket(
                cid=str(row["cid"]),
                min_size=float(row.get("min_size", 5.0)),
                tick=float(row.get("tick", 0.01)),
                max_spread=float(row.get("max_spread", 4.5)),
                days_to_resolve=float(row.get("days_to_resolve", 0.0)),
                source=str(row.get("source", "spread")),
                daily=float(row.get("daily", 0.0)),
                slug=str(row.get("slug", "")),
                title=str(row.get("title", "")),
                shares=int(row.get("shares", 120)),
                est_income=float(row.get("est_income", 0.0)),
                est_capital=float(row.get("est_capital", 120.0)),
                return_pct_day=float(row.get("return_pct_day", 0.0)),
                their_score=float(row.get("their_score", 0.0)),
                volume_24h=float(row.get("volume_24h", 0.0)),
                spread=float(row.get("spread", 0.01)),
                eligible=bool(row.get("eligible", True)),
                reject_reason=str(row.get("reject_reason", "")),
            )
            out.append(gm)
        except (ValueError, TypeError) as e:
            raise MarketFeedError(f"row {idx} ({row.get('cid')}) has malformed field: {e}") from e

    return out


def get_market_by_cid(
    cid: str,
    path: Path | str | None = None,
    max_age_sec: Optional[float] = None,
) -> Optional[GraduatedMarket]:
    """Find a specific graduated market by full or prefix condition_id."""
    markets = load_graduated_markets(path=path, max_age_sec=max_age_sec)
    cid_lower = cid.lower()
    for m in markets:
        if m.cid.lower() == cid_lower or m.cid.lower().startswith(cid_lower):
            return m
    return None
