"""core_brain/market_feed.py - Market feed reading graduated markets from runtime/markets.json.

Reads the ranker's output (8 graduated markets) directly from disk without
re-deriving the funnel and without importing across a package boundary.
Handles missing, empty, or stale feed files explicitly.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core_brain.runtime_paths import resolve_runtime_file, runtime_file

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LIVE_ROOT = PROJECT_ROOT
REPO_ROOT = PROJECT_ROOT
# Compatibility seam for callers/tests that replace the feed path. Production
# resolution belongs to runtime_paths so the current/legacy rename fallback has
# one owner.
DEFAULT_MARKETS_PATH = runtime_file("markets.json", root=PROJECT_ROOT)


def default_markets_path() -> Path:
    """The feed to read now: DEFAULT_MARKETS_PATH when it exists, otherwise the
    pre-rename run/markets.json while the filter has not written the new one.

    Resolved per call, not once at import: the Trader is a long-running process
    and must pick up runtime/markets.json the moment the filter writes it.
    Without the fallback the Trader quotes nothing for a whole filter cycle
    after the rename, because its entire universe still sits in run/.
    """
    canonical = runtime_file("markets.json", root=PROJECT_ROOT)
    if DEFAULT_MARKETS_PATH != canonical and DEFAULT_MARKETS_PATH.exists():
        # Preserve the injectable path used by callers and tests without making
        # it the production source of runtime-path behavior.
        return DEFAULT_MARKETS_PATH
    return resolve_runtime_file("markets.json", root=PROJECT_ROOT)

# Default maximum age before a feed file is considered stale (e.g. 24 hours).
DEFAULT_MAX_STALENESS_SEC: float = 86400.0


class MarketFeedError(RuntimeError):
    """Base error for market feed issues."""


class MarketFeedAbsentError(MarketFeedError):
    """Raised when runtime/markets.json is absent on disk."""


class MarketFeedStaleError(MarketFeedError):
    """Raised when runtime/markets.json is older than allowed staleness threshold."""


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
    paired_depth_arm: str = ""
    paired_depth_cutoff_usd: float = 0.0
    paired_depth_snapshot_id: str = ""
    event_id: str = ""
    event_slug: str = ""
    event_title: str = ""


def _load_paired_depth_feed(
    target: Path, *, arm: str, max_age_sec: Optional[float],
) -> list[GraduatedMarket]:
    """Read one arm from an atomic paired-depth bundle with strict metadata checks."""
    if arm not in {"control", "treatment"}:
        raise MarketFeedError(f"unknown paired-depth arm {arm!r}")
    if not target.is_file():
        raise MarketFeedAbsentError(f"paired-depth feed missing at {target}")
    try:
        stat = target.stat()
    except OSError as exc:
        raise MarketFeedAbsentError(f"unable to stat {target}: {exc}") from exc
    if stat.st_size == 0:
        raise MarketFeedError(f"paired-depth feed at {target} is empty (0 bytes)")
    if max_age_sec is not None and max_age_sec > 0:
        age = time.time() - stat.st_mtime
        if age > max_age_sec:
            raise MarketFeedStaleError(
                f"paired-depth feed at {target} is stale: age {age:.0f}s > {max_age_sec:.0f}s"
            )
    try:
        bundle = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MarketFeedError(f"failed to parse paired-depth JSON from {target}: {exc}") from exc
    if not isinstance(bundle, dict) or bundle.get("format") != "spread_hunter.paired-depth.v1":
        raise MarketFeedError(f"paired-depth feed at {target} has an unsupported format")
    snapshot_id = bundle.get("snapshot_id")
    rows = bundle.get(arm)
    if not isinstance(snapshot_id, str) or not snapshot_id or not isinstance(rows, list):
        raise MarketFeedError(f"paired-depth feed at {target} is missing {arm} metadata")
    if any(
        not isinstance(row, dict)
        or row.get("paired_depth_snapshot_id") != snapshot_id
        or row.get("paired_depth_arm") != arm
        for row in rows
    ):
        raise MarketFeedError(f"paired-depth feed at {target} has inconsistent {arm} rows")
    try:
        expected_cutoff = float(bundle[
            "control_depth_usd" if arm == "control" else "treatment_depth_usd"])
    except (KeyError, TypeError, ValueError) as exc:
        raise MarketFeedError(f"paired-depth feed at {target} is missing its {arm} cutoff") from exc
    try:
        has_cutoff_mismatch = any(
            float(row.get("paired_depth_cutoff_usd", -1.0)) != expected_cutoff
            for row in rows
        )
    except (TypeError, ValueError) as exc:
        raise MarketFeedError(f"paired-depth feed at {target} has malformed {arm} cutoff") from exc
    if has_cutoff_mismatch:
        raise MarketFeedError(f"paired-depth feed at {target} has an inconsistent {arm} cutoff")
    return _graduated_rows(rows, target)



def _graduated_rows(data: list, target: Path) -> list[GraduatedMarket]:
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
                paired_depth_arm=str(row.get("paired_depth_arm", "")),
                paired_depth_cutoff_usd=float(row.get("paired_depth_cutoff_usd", 0.0)),
                paired_depth_snapshot_id=str(row.get("paired_depth_snapshot_id", "")),
                event_id=str(row.get("event_id", "")),
                event_slug=str(row.get("event_slug", "")),
                event_title=str(row.get("event_title", "")),
            )
            out.append(gm)
        except (ValueError, TypeError) as exc:
            raise MarketFeedError(f"row {idx} ({row.get('cid')}) has malformed field: {exc}") from exc
    return out


def load_graduated_markets(
    path: Path | str | None = None,
    max_age_sec: Optional[float] = DEFAULT_MAX_STALENESS_SEC,
    *,
    paired_arm: Optional[str] = None,
) -> list[GraduatedMarket]:
    """Read graduated markets from runtime/markets.json with staleness and existence checks.

    Raises `MarketFeedAbsentError` if file is missing.
    Raises `MarketFeedStaleError` if file mtime exceeds `max_age_sec`.
    Raises `MarketFeedError` if file is empty or malformed.

    max_age_sec defaults to DEFAULT_MAX_STALENESS_SEC (24h); 0 or None opt out.
    """
    target = Path(path) if path is not None else default_markets_path()
    if paired_arm is not None:
        return _load_paired_depth_feed(target, arm=paired_arm,
                                       max_age_sec=max_age_sec)

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

    # 0 or None disables the staleness check
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

    return _graduated_rows(data, target)


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
