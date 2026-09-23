"""Filter the unified market universe by RETURN, and write the winners to runtime/markets.json.

    python -m scripts.filter_markets            # top 20
    python -m scripts.filter_markets --top 40
    python -m scripts.filter_markets --full-scan    # audit: exhaust Gamma's listing

ONE universe: Gamma /markets ordered by 24h volume, every tradable binary on
it, reward-funded or not -- the reward/spread split is gone. Scored on spread
capture, gated on real movement (a 30-minute notional window), and every
rejected candidate lands in runtime/market_universe.json with its reason.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import math
import os
import re
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scoring.allocate import (marginal, spread_capture_daily)   # noqa: E402
from core_brain import rehearsal   # noqa: E402
from scoring import config as _load_cfg_module   # noqa: E402
from scoring.config import load as _load_cfg   # noqa: E402
from scoring.markets import parse_book   # noqa: E402
from scoring.rewards import score_per_share   # noqa: E402
from scoring.selector import (identity_allowed, maker_queue_allowed,  # noqa: E402
                              pair_books_allowed, top_depth_usd)


def _load_repo_env(root: Path | str = ROOT) -> None:
    """Make the repo's `.env` visible to config, as scripts/filter_loop does.

    A staged gate trial lives in `.env` (HUNTER_VOLUME_TRIAL_USD and friends),
    and `_effective_volume_bar` already falls back to it through config -- but
    only if something put the file's values in the environment first. Nothing
    here did, so a direct `python -m scripts.filter_markets` gated on the
    permanent bar while `scripts/filter_loop`, which loads the file and then
    passes `--trial-volume` explicitly, gated on the trial.

    The menu's pre-flight is a direct run with no arguments, so the two
    disagreed and the pre-flight overwrote the screener's trial universe with
    an untagged one. `override=False`: a value already in the process
    environment is the more deliberate of the two and keeps winning.

    A missing file or an unreadable one is not an error. The permanent bars
    are the safe fallback, and they are what an unstaged repo should gate on.

    The excepts are narrow on purpose. A bare `except Exception` here swallowed
    a `TypeError` from passing a str root and reported success while loading
    nothing -- the gate silently reverted to the permanent bar with no trace.
    Only a missing dependency and an unreadable file are tolerated.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return                      # no python-dotenv: permanent bars stand
    try:
        load_dotenv(Path(root) / ".env", override=False)
    except OSError:
        pass                        # unreadable file: permanent bars stand


# Before `_CFG` below: it is built once at import and never rebuilt, so a
# value that lands in the environment after this point is never seen.
_load_repo_env()

RUN = ROOT / "runtime"
OFFSET = 0.020          # where we intend to quote, in price units
C = 3.0                 # venue's one-sided penalty

# Polymarket: "The minimum reward payout is $1; amounts below this will not be
# paid." A market projecting under a dollar a day does not pay a fraction of a
# dollar, it pays nothing -- so a sub-floor market is not a small position, it
# is capital committed for zero income. Measured 2026-07-30, 16 of 20 fleet
# markets were in exactly that state.
MIN_PAYOUT = 1.0
FLOOR_MULTIPLE = 1.5    # headroom: projections are noisy and rivals arrive

# TRADABILITY AND HORIZON (U6). Sourced from config so the ranker and the
# fleet cannot drift, exactly as the payout floor is.
_CFG = _load_cfg()
MIN_VOLUME_24H = _CFG.select_min_volume_24h_usd
MAX_DAYS_TO_RESOLVE = _CFG.select_max_days_to_resolve
MIN_TOP3_DEPTH_USD = _CFG.select_min_top3_depth_usd
MOVEMENT_WINDOW_SEC = _CFG.select_movement_window_sec
MIN_MOVEMENT_USD = _CFG.select_min_movement_usd
TRADES_API = "https://data-api.polymarket.com/trades"
MAX_BOOK_SPREAD = _CFG.select_max_book_spread

GAMMA = "https://gamma-api.polymarket.com/markets"
ORDERING_FALLBACK_PAGES = 5


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if sys.platform == "win32":
            # Use psutil for non-destructive check on Windows
            try:
                import psutil
                return psutil.pid_exists(pid)
            except ImportError:
                # Fallback to OpenProcess if psutil unavailable
                import ctypes
                from ctypes import wintypes
                SYNCHRONIZE = 0x00100000
                k32 = ctypes.WinDLL("kernel32", use_last_error=True)
                k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
                k32.OpenProcess.restype = wintypes.HANDLE
                k32.CloseHandle.argtypes = [wintypes.HANDLE]
                k32.CloseHandle.restype = wintypes.BOOL
                handle = k32.OpenProcess(SYNCHRONIZE, False, pid)
                if handle:
                    k32.CloseHandle(handle)
                    return True
                return False
        else:
            os.kill(pid, 0)
            return True
    except (OSError, ProcessLookupError, ValueError):
        return False


def q_min(a: float, b: float) -> float:
    return max(min(a, b), max(a / C, b / C))


def days_to_resolve(end_iso: Optional[str],
                    now_iso: Optional[str] = None) -> Optional[float]:
    """Days from now until the venue's stated end date, or None if unstated.

    `now_iso` exists so the horizon arithmetic is testable without freezing
    the clock. Negative means the end date has already passed.
    """
    if not end_iso:
        return None
    try:
        end = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    now = (datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
           if now_iso else datetime.now(timezone.utc))
    # An unqualified venue timestamp carries no offset, and subtracting an
    # aware datetime from a naive one raises TypeError -- which the
    # `except ValueError` above does not catch. This runs inside a
    # ThreadPoolExecutor worker, so a single such endDate aborted the entire
    # ranking run. Venue times are UTC by convention; assuming that keeps the
    # arithmetic aware-vs-aware instead of raising.
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (end - now).total_seconds() / 86400.0


def market_start_iso(m: dict) -> Optional[str]:
    """The event's scheduled start, when the venue states one.

    Sports markets open for trading before the event begins and carry
    `gameStartTime`; everything else has no start signal here and returns None,
    which reads as "already trading" downstream. `startDate` is deliberately
    NOT used: on most markets it is the listing date, hours or days before the
    market became quotable, so it says nothing about whether the underlying
    event is under way.
    """
    if not isinstance(m, dict):
        return None
    direct = m.get("_start_iso") or m.get("gameStartTime")
    if direct:
        return str(direct)
    events = m.get("events") or []
    if isinstance(events, list) and events and isinstance(events[0], dict):
        nested = events[0].get("gameStartTime")
        if nested:
            return str(nested)
    return None


def pre_start(start_iso: Optional[str],
              now_iso: Optional[str] = None) -> tuple[bool, str]:
    """Has this market's event not started yet?

    A tennis market opens before the first serve and sits flat with zero tape
    until the match begins: 4.3 hours on Vilius Gaubas at 0.23 with 1,777
    shares ahead and nothing traded. The [0.20, 0.80] mid gate cannot catch it
    -- a pre-start 0.45/0.55 is squarely inside the band -- so the start time
    is its own gate.

    Unknown is NOT pre-start. Most markets state no start time at all, and
    refusing every one of them would empty the universe on a missing field.

    `now_iso` is the test seam, matching `days_to_resolve`.
    """
    if not start_iso:
        return False, ""
    try:
        start = datetime.fromisoformat(str(start_iso).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False, ""
    now = (datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
           if now_iso else datetime.now(timezone.utc))
    # Venue times are UTC by convention; the same aware/naive care
    # `days_to_resolve` takes, for the same reason.
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    seconds = (start - now).total_seconds()
    if seconds <= 0:
        return False, ""
    hours = seconds / 3600.0
    when = f"{hours:.1f}h" if hours >= 1.0 else f"{seconds / 60.0:.0f}m"
    return True, f"pre-start: event has not started (starts in {when})"


def traded_notional(session: requests.Session, condition_id: str,
                    window_sec: float = MOVEMENT_WINDOW_SEC,
                    now_ts: Optional[float] = None,
                    limit: int = 500) -> Optional[float]:
    """Dollars that changed hands on this market inside the window.

    None means UNMEASURED -- the tape read failed, or the venue answered with
    a shape this cannot read. Unknown is never treated as flat: a gate that
    fires on a failed HTTP call would empty the universe on one bad minute.

    `now_ts` is the test seam, matching `days_to_resolve` and `pre_start`.
    """
    if not condition_id:
        return None
    try:
        rows = session.get(TRADES_API,
                           params={"market": condition_id, "limit": limit},
                           timeout=12).json()
    except Exception:
        return None
    if not isinstance(rows, list):
        return None
    now = time.time() if now_ts is None else now_ts
    cutoff = now - max(0.0, window_sec)
    total = 0.0
    for t in rows:
        if not isinstance(t, dict):
            continue
        try:
            ts = float(t.get("timestamp") or 0.0)
            price = float(t.get("price") or 0.0)
            size = float(t.get("size") or 0.0)
        except (TypeError, ValueError):
            continue
        # inf/nan or a negative print is venue garbage, and either one would
        # travel straight into the gate: `inf` makes every market look active,
        # a negative subtracts real activity from the window.
        if not all(math.isfinite(x) for x in (ts, price, size)):
            continue
        if price < 0 or size < 0:
            continue
        # A row we cannot place in time cannot be counted toward a windowed
        # figure. Skipping it under-counts, which is the safe direction for a
        # gate that refuses on "too little".
        if ts < cutoff:
            continue
        total += price * size
    return round(total, 2)


def movement_reject(movement_usd: Optional[float],
                    min_movement_usd: Optional[float] = None,
                    window_sec: float = MOVEMENT_WINDOW_SEC) -> tuple[bool, str]:
    """Should this market be refused for having no recent movement?

    Two markets can both clear a six-figure 24h volume while one of them has
    printed nothing for hours: the 4.3-hour stall sat at 0.23 with 1,777
    shares ahead and zero traded. 24h volume cannot see that; a window can.

    Inert at the shipped bar of 0.0 (record only) and inert on an unmeasured
    tape, so enforcing is a deliberate act with evidence behind it.
    """
    bar = MIN_MOVEMENT_USD if min_movement_usd is None else min_movement_usd
    if not bar or bar <= 0:
        return False, ""
    if movement_usd is None:
        return False, ""
    if movement_usd >= bar:
        return False, ""
    minutes = int(round(window_sec / 60.0))
    return True, (f"no movement: ${movement_usd:,.0f} traded in last "
                  f"{minutes}m under ${bar:,.0f} (flat)")


def tradable(volume_24h: Optional[float],
             days: Optional[float],
             title: object = "", slug: object = "",
             category: object = "", market_type: object = "",
             market_group: object = "", series_title: object = "",
             event_title: object = "",
             min_volume_usd: Optional[float] = None) -> tuple[bool, str]:
    """Can this market produce the two observations the run needs?

    A fill needs someone to trade at our price; a settled P&L needs the market
    to resolve inside the run. Reward yield answers neither question, and
    ranking on it alone chose 20 markets that between them printed 48 trades in
    11.6 hours and resolved no sooner than September 2026.

    Unknown is refused on both axes rather than assumed favourable. The
    universe that produced zero fills and zero resolutions was long-dated and
    thin, so a missing field is far more likely to be another one of those than
    a liquid market with a gap in its metadata.
    """
    # Keep this helper backwards-compatible for callers that only supply the
    # numeric tradability inputs. Full selector identity is enforced by
    # `evaluate`, where the venue metadata is available.
    all_meta = (title, slug, category, market_type, market_group,
                series_title, event_title)
    if any(_value not in (None, "") for _value in all_meta):
        identity_ok, identity_reason = identity_allowed(
            title, slug, category, market_type,
            market_group, series_title, event_title)
        if not identity_ok:
            return False, identity_reason
    if volume_24h is None:
        return False, "volume unknown"
    volume_bar = MIN_VOLUME_24H if min_volume_usd is None else min_volume_usd
    if volume_24h < volume_bar:
        return False, f"24h volume ${volume_24h:,.0f} < ${volume_bar:,.0f}"
    if days is None:
        return False, "horizon unknown"
    if days < 0:
        return False, "horizon passed"
    if days > MAX_DAYS_TO_RESOLVE:
        return False, f"horizon {days:.1f}d > {MAX_DAYS_TO_RESOLVE:.0f}d"
    return True, ""


def gamma_volume(session: requests.Session,
                 cids: list[str]) -> dict[str, float]:
    """24h traded volume per condition_id, from gamma.

    The CLOB's market payload carries no volume at all, which is why the
    ranker never had this filter: the number it needed was on a different
    host. Queried in chunks because the endpoint takes repeated
    `condition_ids` parameters and the candidate list is a few hundred long.
    """
    out: dict[str, float] = {}
    for i in range(0, len(cids), 20):
        chunk = cids[i:i + 20]
        try:
            rows = session.get(GAMMA, params={"condition_ids": chunk,
                                              "limit": len(chunk)},
                               timeout=20).json()
        except Exception:
            continue
        if isinstance(rows, dict):
            rows = rows.get("data") or []
        for r in rows:
            cid = r.get("conditionId")
            if cid:
                out[cid] = float(r.get("volume24hr") or 0.0)
    return out


def gamma_universe(session: requests.Session,
                   per_page: int = 100,
                   min_volume_usd: Optional[float] = None,
                   full_scan: bool = False,
                   max_pages: int = 200) -> tuple[list[dict], dict]:
    """ONE market universe: every tradable binary Gamma market, rewards or not.

    This replaces the two-path scan. `/sampling-markets` listed reward-funded
    markets and nothing else, so the ranker structurally could not see the
    markets that actually trade -- the 2026-07-31 universe came from there: 20
    markets, 48 tape prints in 11.6 hours, nine of them never traded at all.
    The distinction is gone: discovery is Gamma `/markets` ordered by 24h
    volume, and reward state is NOT a filter -- `clobRewards` rows are scored
    on the same spread terms as everything else (their reward config still
    feeds the score-window width, nothing else).

    The end-date range is likewise no longer a discovery parameter: long-dated
    markets are fetched, then refused auditably by the horizon gate in
    `evaluate`, so the funnel shows how much of the population the 30-day
    horizon actually removes.

    Cost control: the listing is volume-sorted and the ranker gates on
    `select_min_volume_24h_usd`, so paginating to exhaustion would fetch
    thousands of sub-floor rows every rank. The scan stops one BOUNDARY PAGE
    past the first sub-floor row -- enough to audit the volume near-miss tail
    -- and records the truncation in the returned metadata instead of hiding
    it. `full_scan=True` (the operator's `--full-scan`) paginates until Gamma
    returns nothing, bounded by `max_pages`. If the sort is inverted, normal
    scans switch to per-row filtering for a smaller, separately bounded page
    budget so qualifying rows are retained without turning every rank into a
    general maximum-page scan.

    Cheap per-row filters (active/closed/archived, order book, accepting
    orders, binary, quoted spread) are applied here, and the rows they refuse
    are COUNTED and SAMPLED in the metadata rather than scored. Returned rows
    use CLOB field names (`condition_id`, `tokens`, `rewards`) because
    `evaluate` reads them, plus the gamma-only figures the spread pot needs.
    """
    now = datetime.now(timezone.utc)
    out: list[dict] = []
    volume_bar = MIN_VOLUME_24H if min_volume_usd is None else min_volume_usd
    meta: dict = {
        "volume_bar": volume_bar,
        "pages_fetched": 0,
        "rows_scanned": 0,
        "truncated": False,
        "ordering_violated": False,
        "cheap_rejects": {},
        "cheap_examples": {},
    }
    # ADVANCE BY WHAT THE ENDPOINT ACTUALLY RETURNED, NOT BY WHAT WE ASKED FOR.
    # Gamma caps a page at 100 rows and ignores a larger `limit` -- measured
    # 2026-08-02: limit=100, 250 and 500 all return exactly 100. PAGINATION
    # CONTRACT (verified live 2026-08-10): this endpoint serves a flat array
    # and supports `offset` only; there is no cursor field and `after_cursor`
    # is silently ignored, so keyset pagination is not possible.
    offset = 0
    page_cap: int | None = None
    # The floor cutoff below is only sound while the venue keeps the verified
    # descending-volume sort. Track the first sub-floor row and whether a
    # qualifying row has appeared after one -- the regression that
    # invalidates the cutoff.
    floor_seen = False
    ordering_violated = False
    boundary_pages_done = 0
    ordering_fallback_pages_done = 0

    def _cheap_reject(kind: str, m: dict) -> None:
        meta["cheap_rejects"][kind] = meta["cheap_rejects"].get(kind, 0) + 1
        bucket = meta["cheap_examples"].setdefault(kind, [])
        if len(bucket) < 8:
            title = (m.get("question") or "")[:80]
            if title:
                bucket.append(title)

    for _ in range(max_pages):
        params = {
            "closed": "false", "active": "true", "archived": "false",
            "order": "volume24hr", "ascending": "false",
            "limit": per_page, "offset": offset,
        }
        try:
            rows = session.get(GAMMA, params=params, timeout=30).json()
        except Exception:
            meta["truncated"] = True        # scan ended on an error, not exhaustion
            break
        if isinstance(rows, dict):
            rows = rows.get("data") or []
        if not rows:
            break
        meta["pages_fetched"] += 1
        offset += len(rows)
        meta["rows_scanned"] += len(rows)
        for m in rows:
            vol = float(m.get("volume24hr") or 0.0)
            if vol < volume_bar:
                floor_seen = True
                _cheap_reject("sub-floor volume", m)
                continue
            if floor_seen and not ordering_violated:
                # A qualifying market after a sub-floor one: the venue's
                # sort regressed. Warn once, then keep filtering per-row
                # instead of trusting the cutoff -- silently dropping a
                # qualifying market is exactly the failure the ordering
                # assumption exists to rule out.
                ordering_violated = True
                meta["ordering_violated"] = True
                print("WARNING: gamma page not sorted by volume24hr "
                      "(qualifying market below a sub-floor one); "
                      "falling back to per-row filtering for the rest "
                      "of the scan")
            if not m.get("enableOrderBook"):
                _cheap_reject("no order book", m)
                continue
            if not m.get("acceptingOrders"):
                _cheap_reject("not accepting orders", m)
                continue
            try:
                toks = json.loads(m.get("clobTokenIds") or "[]")
            except (TypeError, ValueError):
                _cheap_reject("unparsable clobTokenIds", m)
                continue
            if not isinstance(toks, list) or len(toks) != 2:
                _cheap_reject("not binary", m)
                continue
            spread = float(m.get("spread") or 0.0)
            if spread <= 0:
                _cheap_reject("no book spread", m)
                continue
            out.append({
                "condition_id": m.get("conditionId"),
                "question": m.get("question") or "",
                "market_slug": m.get("slug") or "",
                "category": m.get("category") or m.get("categorySlug") or "",
                "market_type": m.get("marketType") or m.get("type") or "",
                "market_group": m.get("groupItemTitle") or "",
                "series_title": ((m.get("events") or [{}])[0].get("series") or [{}])[0].get("title", ""),
                "event_title": ((m.get("events") or [{}])[0].get("title") or ""),
                "tokens": [{"token_id": str(t)} for t in toks],
                # Reward config, when the venue publishes one, feeds the score
                # WINDOW only. It is not a filter and not an income source:
                # the pot for every row here is spread capture.
                "rewards": {"max_spread": float(m.get("rewardsMaxSpread") or 3.5),
                            "min_size": float(m.get("rewardsMinSize") or 50)},
                "minimum_tick_size": float(m.get("orderPriceMinTickSize") or 0.01),
                "end_date_iso": m.get("endDate"),
                # Quoting minimum, which is the venue's order minimum --
                # there is no reward score to qualify for, so rewardsMinSize
                # would only inflate the lot the allocator has to buy.
                # Sports markets open before the event does; this is what the
                # pre-start gate reads. Absent on everything else, which reads
                # as "already trading".
                "_start_iso": (m.get("gameStartTime")
                               or ((m.get("events") or [{}])[0] or {}).get("gameStartTime")),
                "_order_min": float(m.get("orderMinSize") or 5),
                "_volume_24h": vol,
                "_spread": spread,
            })
        # Sorted by volume, so the first market under the floor ends the
        # useful part of the listing -- when the sort holds. Verified
        # against the live endpoint 2026-08-02 and re-verified 2026-08-10:
        # 100 rows, zero inversions, and the first row under the floor had
        # no qualifying market after it. An inverted page sets
        # `ordering_violated`, which skips this cut and scans on. Inversion
        # detection is WITHIN a page only: a page that ends on a clean
        # sub-floor tail trusts the cut, so a venue regression at exactly
        # the page boundary (qualifying rows at the top of the next page)
        # is not detected -- the verified sort rules that case out.
        # A SHORT PAGE MEANS THE LISTING ENDED -- measured against what this
        # endpoint actually serves, not what we asked for. Checked BEFORE the
        # boundary policy below: a boundary page that is also short is
        # exhaustion, not truncation. The first response establishes the real
        # page size.
        if page_cap is None:
            page_cap = len(rows)
        if len(rows) < page_cap:
            break
        if floor_seen and not full_scan:
            if ordering_violated:
                ordering_fallback_pages_done += 1
                if ordering_fallback_pages_done >= ORDERING_FALLBACK_PAGES:
                    # The sort can no longer support a floor cutoff, so keep
                    # filtering every fetched row but bound the degraded scan
                    # separately from the general/full-scan page allowance.
                    meta["truncated"] = True
                    break
            elif boundary_pages_done >= 1:
                # Stopped on POLICY, not because the listing ended -- recorded
                # as truncation so no reader mistakes the fetch for complete.
                meta["truncated"] = True
                break
            else:
                boundary_pages_done += 1
                # ONE boundary page past the floor: the volume near-miss tail
                # is fetched and counted so the gate can be tuned from
                # evidence, then the scan stops. `full_scan` keeps going to
                # exhaustion.
    else:
        meta["truncated"] = True            # stopped at max_pages, not exhaustion
    return out, meta




# The old two-path discovery (`gamma_spread_universe`, rewards-only
# /sampling-markets + reward-excluding gamma scan) is deleted with the reward
# path; `--legacy-rewards` reconstructs its candidate list inline in `main()`
# for one comparison period.


def order_score(v: float, s: float, size: float, min_size: float) -> float:
    if s < 0 or s > v or size < min_size:
        return 0.0
    return ((v - s) / v) ** 2 * size


def queue_bar_reject(m: dict, *, source: str,
                     max_queue_minutes, queue_minutes_fn):
    """A rejection row when this market's queue at our price will not clear.

    The three bars around it are FLOORS on depth, and from the maker's side
    depth near mid IS the queue ahead of us: `select_min_top3_depth_usd` and
    `book_health` protect our ability to EXIT, which is real and points the
    other way. `select_min_volume_24h_usd` measures market-wide 24-hour tape
    across every price, so a market can clear six figures a day while trading
    nothing at `mid - 2.5c`, the only level our maker order ever sits at.

    Returns None when the market passes, when no bar is set, or when nothing
    can measure the queue -- and in the first of those cases it does not call
    `queue_minutes_fn` at all. Measuring costs a tape read per market, and
    paying for a number the caller has already decided not to act on is how a
    ranking pass gets slow for nothing. That is also what "record-only" means
    at this layer: `enforce_max_queue_minutes` is False by default, `main`
    passes no bar, and this function stays inert.
    """
    if not max_queue_minutes or max_queue_minutes <= 0 or queue_minutes_fn is None:
        return None
    minutes = queue_minutes_fn(m)
    ok, reason = maker_queue_allowed(minutes, max_queue_minutes)
    if ok:
        return None
    return {
        "source": source, "eligible": False, "reject_reason": reason,
        "queue_minutes": minutes,
        "cid": m.get("condition_id"),
        "title": m.get("question", "")[:90],
        "slug": m.get("market_slug", ""),
    }


def _vol(volume_24h: Optional[float]) -> Optional[float]:
    """Volume rounded for a funnel row, or None when never measured."""
    return round(volume_24h, 2) if volume_24h is not None else None


def _book_stats(book_spreads: dict, book_depths: dict) -> dict:
    """Per-side book readings for a funnel row, keyed by leg.

    The universe file answers "did the book gate reject this market, and by
    how much?" -- so every row that fetched at least one leg carries that
    leg's spread and top-3 bid depth, under the YES_/NO_-prefixed names the
    dashboard and diagnostics read.
    """
    out: dict = {}
    for j, label in ((0, "yes"), (1, "no")):
        if j in book_spreads:
            out[f"{label}_spread"] = round(book_spreads[j], 4)
        if j in book_depths:
            out[f"{label}_depth_usd"] = round(book_depths[j], 2)
    return out


def _reject_row(source: str, reason: str, m: dict,
                volume_24h: Optional[float] = None,
                **extra) -> dict:
    """One funnel row for a rejected market -- never None.

    Book-stage failures used to `return None` and vanish into the
    `dropped_no_verdict` count: the market was discovered, then silently
    unaccounted. Every gate now answers with a row, so the raw population
    reconciles exactly against the buckets.
    """
    row = {
        "source": source, "eligible": False, "reject_reason": reason,
        "volume_24h": _vol(volume_24h),
        "cid": m.get("condition_id"),
        "title": m.get("question", "")[:90],
        "slug": m.get("market_slug", ""),
    }
    row.update(extra)
    return row


def evaluate(session: requests.Session, rate: float, m: dict,
             volume_24h: Optional[float] = None,
             source: str = "spread", *,
             min_depth_usd: Optional[float] = None,
             min_volume_usd: Optional[float] = None,
             min_movement_usd: Optional[float] = None,
             max_spread: Optional[float] = None,
             max_queue_minutes: Optional[float] = None,
             queue_minutes_fn=None) -> dict:
    """Income and capital for one market, from its live book.

    `rate` is the market's pot in $/day. In the unified universe every pot is
    spread capture -- `spread_capture_daily`, paid by the taker on the trade;
    no minimum distribution exists to apply. (`source` remains a parameter
    only for `--legacy-rewards`, which restores the old reward pot and its
    $1.50 floor for one comparison period.)

    GATE ORDER, cheapest first: identity, pre-start, queue, score-window
    shape -- then the TAPE (one request) -- then the books (two requests).
    A market whose tape is dead cannot fill a resting order at any price, so
    its books are never fetched. Book-stage failures return rejection ROWS
    rather than None: a market the funnel discovered must stay accounted for
    all the way to a bucket.
    """
    rw = m.get("rewards") or {}
    identity_ok, identity_reason = identity_allowed(
        m.get("question"), m.get("market_slug") or m.get("slug"),
        m.get("category"), m.get("market_type"),
        m.get("market_group"), m.get("series_title"), m.get("event_title"))
    if not identity_ok:
        return {
            "source": source, "eligible": False,
            "reject_reason": identity_reason,
            "cid": m.get("condition_id"),
            "title": m.get("question", "")[:90],
            "slug": m.get("market_slug", ""),
        }
    # THE PRE-START GATE, before the two book fetches below. A market whose
    # event has not begun prints nothing at any price, so paying for its books
    # buys a reading of a book that cannot move.
    not_started, start_reason = pre_start(market_start_iso(m))
    if not_started:
        return _reject_row(source, start_reason, m, volume_24h)
    # THE MAKER-QUEUE BAR, before the two book fetches below rather than
    # after them. A market whose queue at our own price never clears cannot be
    # quoted at all, so paying for its books to score it is wasted venue work.
    # Inert unless a bar is passed in -- see `resolve_queue_bar`.
    queue_row = queue_bar_reject(m, source=source,
                                 max_queue_minutes=max_queue_minutes,
                                 queue_minutes_fn=queue_minutes_fn)
    if queue_row is not None:
        return queue_row

    v = (rw.get("max_spread") or 3.5) / 100.0
    min_size = rw.get("min_size") or 50
    toks = [t.get("token_id") for t in (m.get("tokens") or [])]
    if len(toks) != 2 or OFFSET >= v:
        return _reject_row(
            source,
            "no score window: two tokens and a window wider than the "
            "quote offset are required",
            m, volume_24h)

    # THE MOVEMENT GATE, BEFORE the two book fetches. A market whose tape is
    # dead cannot fill a resting order at any price, so paying for its books
    # to score it is wasted venue work: one tape read refuses it for one
    # request instead of three. Unmeasured tape stays fail-open -- None
    # proceeds to the book gates and is recorded on the row, never treated
    # as flat, because a failed HTTP call must not empty the universe on one
    # bad minute at the venue.
    movement_bar = (MIN_MOVEMENT_USD if min_movement_usd is None
                    else min_movement_usd)
    movement_usd = traded_notional(
        session, m.get("condition_id"), window_sec=MOVEMENT_WINDOW_SEC)
    flat, flat_reason = movement_reject(
        movement_usd, min_movement_usd=movement_bar,
        window_sec=MOVEMENT_WINDOW_SEC)
    if flat:
        return _reject_row(source, flat_reason, m, volume_24h,
                           movement_usd=movement_usd,
                           movement_window_sec=MOVEMENT_WINDOW_SEC)

    q1 = q2 = 0.0
    capital_per_share = 0.0
    mids: dict[int, float] = {}
    best_bids: dict[int, float] = {}
    book_spreads: dict[int, float] = {}
    book_depths: dict[int, float] = {}
    books: list[tuple[str, list[tuple[float, float]], list[tuple[float, float]]]] = []
    for j, tok in enumerate(toks):
        side = "YES" if j == 0 else "NO"
        try:
            b = session.get("https://clob.polymarket.com/book",
                            params={"token_id": tok}, timeout=12).json()
        except Exception:
            return _reject_row(source, f"{side}: book fetch failed", m,
                               volume_24h, movement_usd=movement_usd)
        # The fetch is guarded with Exception, and so is the parse: the whole
        # point is that the scorer must never crash on venue data, whatever
        # parse_book's structural-failure type evolves into. This also rounds
        # prices to 4 decimals (shared with full_book) -- a no-op on the
        # venue's 3-decimal tick, deliberately one parse for every caller.
        try:
            book = parse_book(b, tok)
        except Exception:
            return _reject_row(source, f"{side}: book parse failed", m,
                               volume_24h, movement_usd=movement_usd)
        # A skipped level under-counts competitor depth, which OVERSTATES our
        # income share -- the dangerous direction for a funding decision.
        # Fail closed rather than scoring against a partial book; this used
        # to crash the whole ranking run (the parse sat outside the try and
        # the exception aborted every ThreadPool worker).
        if book["malformed"]:
            return _reject_row(source, f"{side}: malformed book", m,
                               volume_24h, movement_usd=movement_usd)
        bids = list(book["bids"].items())
        asks = list(book["asks"].items())
        if not bids or not asks:
            return _reject_row(source, f"{side}: empty or one-sided book", m,
                               volume_24h, movement_usd=movement_usd)
        books.append((side, bids, asks))
        mid = (max(bids)[0] + min(asks)[0]) / 2.0
        # Outside [0.20, 0.80] the book is one-sided in practice and the
        # position is mostly a bet on a near-settled outcome.
        # Tightened 2026-08-28 from [0.05, 0.95] per operator directive:
        # 5c left no room to work — a finished market at 100%/0.1% was still
        # quotable until the settled-book arm caught it. 20c keeps a real
        # spread to capture and prevents a decided leg from ever entering the
        # graduated universe.
        if not 0.20 < mid < 0.80:
            return _reject_row(
                source,
                f"{side}: decided mid {mid:.2f} outside [0.20, 0.80]",
                m, volume_24h, movement_usd=movement_usd,
                **_book_stats({j: min(asks)[0] - max(bids)[0]},
                              {j: top_depth_usd(bids)}))
        mids[j] = mid
        best_bids[j] = max(bids)[0]
        book_spreads[j] = min(asks)[0] - max(bids)[0]
        book_depths[j] = top_depth_usd(bids)
        capital_per_share += mid
        for levels, sign, is_bid in ((bids, 1.0, True), (asks, -1.0, False)):
            for p, s in levels:
                d = (mid - p) * sign
                if 0 <= d <= v and s >= min_size:
                    sc = ((v - d) / v) ** 2 * s
                    if is_bid == (j == 0):
                        q1 += sc
                    else:
                        q2 += sc

    # The depth bar is injectable so a DEPTH-GATE TRIAL run can gate on a
    # lower bar without touching the permanent config value. None (the normal
    # path) means the permanent bar; `main` passes the resolved trial bar.
    depth_bar = (MIN_TOP3_DEPTH_USD if min_depth_usd is None
                 else min_depth_usd)
    # The spread ceiling is injectable for the same reason the depth bar is: a
    # WIDE-BOOK TRIAL run gates on a looser ceiling without touching the
    # permanent config value. None means the permanent bar.
    spread_bar = MAX_BOOK_SPREAD if max_spread is None else max_spread
    books_ok, books_reason = pair_books_allowed(
        books, depth_bar, spread_bar)
    if not books_ok:
        return {
            "source": source, "eligible": False,
            "reject_reason": books_reason,
            "volume_24h": _vol(volume_24h),
            # The book WAS readable -- this market failed the depth/spread
            # gate, not the fetch -- so the competition reading an adopted
            # fleet would average over its window is already in hand here.
            # Carried so the pipeline view can estimate what the allocator
            # would have said had this market been admitted.
            "their_score": round(q_min(q1, q2), 1),
            "daily": rate if source == "rewards" else 0.0,
            "spread": round(float(m.get("_spread") or 0.0), 4) or None,
            "max_spread": rw.get("max_spread") or 3.5,
            "cid": m.get("condition_id"),
            "title": m.get("question", "")[:90],
            "slug": m.get("market_slug", ""),
            "movement_usd": movement_usd,
            **_book_stats(book_spreads, book_depths),
        }

    theirs = q_min(q1, q2)
    n = max(min_size, 120)

    # Score the price we would ACTUALLY quote, not the price we would like to.
    # The bot never bids more than one tick above the best bid (see
    # quotes._decide_quotes_rewards): on a wide book, mid-minus-offset sits
    # deep inside the spread, and a market whose reward window is empty is
    # usually empty because quoting there means being the most exposed order in
    # the book. Ranking on the uncapped price overstated those markets badly --
    # one showed 15%/day for a quote six cents above the next best bid.
    tick = m.get("minimum_tick_size") or 0.01
    sides = []
    for j in range(2):
        mid = mids[j]
        want = mid - OFFSET
        price = min(want, best_bids[j] + tick)
        s = mid - price
        sides.append(order_score(v, s, n, min_size))
    ours = q_min(sides[0], sides[1])
    if ours <= 0:
        return _reject_row(
            source, "cannot score here without overbidding the book", m,
            volume_24h, movement_usd=movement_usd,
            **_book_stats(book_spreads, book_depths))
    income = rate * ours / (ours + theirs)
    capital = n * capital_per_share

    # U6. A market must be able to produce the observations before its yield
    # is worth comparing. Both reasons are recorded rather than dropped, so
    # the report can show that a universe was refused for being untradeable
    # rather than for being unprofitable -- the distinction the last six runs
    # could not make.
    days = days_to_resolve(m.get("end_date_iso"))
    can_trade, why = tradable(
        volume_24h, days, m.get("question"),
        m.get("market_slug") or m.get("slug"),
        m.get("category"), m.get("market_type"),
        m.get("market_group"), m.get("series_title"), m.get("event_title"),
        min_volume_usd=min_volume_usd)
    # The movement gate has already been enforced above, before the book
    # fetches -- `flat` cannot be true here. The payout floor is a REWARD
    # rule -- the venue's minimum distribution -- and only under
    # `--legacy-rewards`: a spread market is paid by whoever lifts the offer,
    # in the amount of the spread, so there is no distribution to be under.
    # Holding it to the floor would reject exactly the liquid markets the
    # unified universe exists to admit.
    pays = income >= MIN_PAYOUT * FLOOR_MULTIPLE if source == "rewards" else income > 0
    if not why and not pays:
        why = (f"income ${income:.2f}/day under payout floor"
               if source == "rewards" else "no spread income")

    return {
        "source": source,
        "spread": round(float(m.get("_spread") or 0.0), 4) or None,
        # Below the payout floor this market pays exactly zero, however good
        # its return_pct_day looks. Recorded rather than filtered here so the
        # report can show what was rejected and why.
        "eligible": pays and can_trade,
        "reject_reason": why,
        "volume_24h": _vol(volume_24h),
        # Recorded on every scanned market, gated or not: the bar for
        # `select_min_movement_usd` is meant to be chosen from this column.
        "movement_usd": movement_usd,
        "movement_window_sec": MOVEMENT_WINDOW_SEC,
        "days_to_resolve": round(days, 2) if days is not None else None,
        "cid": m["condition_id"],
        "title": m.get("question", "")[:90],
        "slug": m.get("market_slug", ""),
        "category": m.get("category") or m.get("categorySlug") or "",
        "market_type": m.get("marketType") or m.get("type") or "",
        "market_group": m.get("market_group") or m.get("groupItemTitle") or "",
        "series_title": m.get("series_title") or "",
        "event_title": m.get("event_title") or "",
        # THE REWARD POT, and zero is the honest figure for a market that pays
        # none. `fleet.reallocate` keys the spread path off `daily <= 0` and
        # recomputes the pot from `volume_24h` and `spread`, so the capture
        # assumption stays in config where it can be revised without
        # re-ranking. `est_income` below reports what that pot projects.
        "daily": rate if source == "rewards" else 0.0,
        # Reward markets must quote at least rewardsMinSize or they score
        # nothing; a spread market only has to clear the venue's order
        # minimum, and using the larger figure would force the allocator to
        # buy a lot several times bigger than the market needs.
        "min_size": min_size if source == "rewards" else m.get("_order_min", 5),
        "max_spread": rw.get("max_spread") or 3.5,
        "tick": m.get("minimum_tick_size") or 0.01,
        "shares": n,
        "est_income": round(income, 3),
        "est_capital": round(capital, 2),
        "return_pct_day": round(100 * income / capital, 3) if capital else 0,
        "their_score": round(theirs, 1),
        "yes_spread": round(book_spreads[0], 4),
        "no_spread": round(book_spreads[1], 4),
        "yes_depth_usd": round(book_depths[0], 2),
        "no_depth_usd": round(book_depths[1], 2),
    }


def _cause(reason: str) -> str:
    """Bucket a rejection reason by GATE, not by first word.

    Splitting on whitespace put one gate in two buckets -- "volume unknown"
    landed under `volume` while "24h volume $900 < $5,000" landed under `24h`
    -- and "no spread income" became `no`. Labels that do not match the gates
    cannot answer the question this bucketing exists to answer.
    """
    r = reason.lower()
    if "volume" in r:
        return "volume"
    if "horizon" in r:
        return "horizon"
    if "income" in r:
        return "income"
    # The pre-start and movement reasons embed a countdown and a measured
    # dollar figure, so bucketing on the raw text would make one card per
    # market. The gate is the bucket.
    if "pre-start" in r:
        return "pre-start"
    if "no movement" in r:
        return "no movement"
    # The decided-mid reason embeds the measured price, so raw text would make
    # one dashboard card per price level. The gate is the bucket, side kept.
    if "decided mid" in r:
        side = ("YES" if r.startswith("yes")
                else "NO" if r.startswith("no") else "")
        return f"{side} decided mid" if side else "decided mid"
    # The book gate embeds the measured value in the reason -- "YES: spread
    # 0.8250 > 0.0600" -- so splitting on " $" left one bucket per spread
    # level (23 buckets in one live run). The side still matters (YES-side vs
    # NO-side failures are different problems), but the value only belongs in
    # the example text. Collapse to at most two cards, keeping the side tag.
    if "spread" in r:
        side = ("YES" if r.startswith("yes")
                else "NO" if r.startswith("no") else "")
        return f"{side} spread" if side else "spread"
    return reason.split(" $")[0] or "other"


def _if_adopted(r: dict) -> dict | None:
    """What the allocator WOULD have said had this rejected market been adopted.

    The allocator's admission test is the first-dollar marginal return --
    pot / competitor-depth, compared to `marginal_return_floor` -- and it is
    the same number the GRADUATED lane's alloc-verdict shows for a refused
    market. For a market the ranker refused there is no fleet-measured
    `avg_theirs` to use, so the venue's own score reading (`their_score`, the
    same q_min the fleet averages over its 30-min window) stands in as a
    single-snapshot estimate, and `k` -- the per-share score of the quote we
    would rest -- converts it to competitor depth in dollars exactly as
    `reallocate` does. `pot` is the reward rate for a reward market, or the
    spread-capture pot for a spread one, so both income sources are judged
    on the same axis as the fleet's water-fill.

    Returns None where no book reading exists -- an identity rejection never
    fetched the book, and a readable book with `their_score` 0.0 (nothing
    resting inside the reward window) is the thin-book shape the depth gate
    exists to catch, so treating it as an empty competitor field and guessing
    "we would take the whole pot" would be the wrong signal (the same
    no-guess principle as the fleet's `avg_theirs()` returning None). There
    is nothing honest to estimate from in either case.
    """
    theirs = r.get("their_score")
    if not theirs:
        return None
    pot = r.get("daily") or 0.0
    if pot <= 0 and r.get("source") == "spread":
        pot = spread_capture_daily(
            float(r.get("volume_24h") or 0.0),
            float(r.get("spread") or _CFG.spread_capture_default_spread),
            _CFG.spread_capture_frac)
    k = score_per_share(float(r.get("max_spread") or 3.5) / 100.0, OFFSET)
    # T == inf (k == 0 -- we would score nothing per share -- or a null
    # reading): the first dollar earns nothing, not NaN. Same guard as the
    # fleet's `_alloc_verdict`.
    T = theirs / k if k and k > 0 else float("inf")
    first = 0.0 if T == float("inf") else marginal(0.0, pot, T) * 100.0
    thresh = _CFG.marginal_return_floor * 100.0
    # With no pot `first` is always 0.0, so the floor comparison alone is
    # the admission test -- no separate pot guard needed.
    would_fund = first >= thresh
    # The MIRAGE arm: the estimate is pot / competition, so a book with
    # nobody resting inside the reward window (competition reading near zero)
    # divides by ~nothing and reports an absurd %/day -- 890%/day on the Dem
    # retirees book, 4,938%/day on UK inflation. That is the empty-book shape
    # the depth gate exists to catch, not an opportunity. A depth reject at
    # under half the gate bar is the same shape measured directly. Both are
    # flagged `trap` so the lanes can show them honestly instead of as green
    # "would clear the floor" wins -- same rule the tracker's pot tile uses.
    _d = _DEPTH_RE.search(r.get("reject_reason") or "")
    depth_trap = False
    if _d:
        dm = float(_d.group(1).replace(",", ""))
        db = float(_d.group(2).replace(",", ""))
        depth_trap = db > 0 and dm < 0.5 * db
    trap = depth_trap or first > 10.0
    if pot <= 0:
        reason = "unpayable: no pot (spread/volume unmeasured)"
    elif trap:
        reason = ("empty-book mirage: nobody resting in the reward window, "
                  "the estimate divides by ~zero competition and is not real")
    elif would_fund:
        reason = (f"first dollar clears the {thresh:.2f}%/day floor -- "
                  "the allocator would have admitted it")
    else:
        reason = f"below the {thresh:.2f}%/day floor"
    return {
        "marg_pct_day": round(first, 2),
        "would_fund": would_fund,
        "trap": trap,
        "threshold_pct": round(thresh, 2),
        "pot_day": round(pot, 2),
        "competition": round(theirs, 1),
        "reason": reason,
    }


def _effective_depth_bar(cli_trial_usd: Optional[float]) -> float:
    """The depth bar this run gates on: CLI trial > config trial > permanent.

    `select_min_top3_depth_usd_trial` (env HUNTER_DEPTH_TRIAL_USD) lets an
    operator stage the trial without touching the permanent config; an explicit
    `--trial-depth` on the command line wins over both because it is the most
    deliberate of the three. A non-positive trial value is a mistake, not a
    signal -- fall back to the permanent bar rather than gating on nothing.
    """
    trial = cli_trial_usd
    if trial is None:
        trial = _CFG.select_min_top3_depth_usd_trial
    if trial is not None and trial > 0:
        return float(trial)
    return MIN_TOP3_DEPTH_USD


def _effective_volume_bar(cli_trial_usd: Optional[float]) -> float:
    """The volume bar this run gates on: CLI trial > config trial > permanent.

    Mirrors `_effective_depth_bar` for the VOLUME-GATE TRIAL (U36): the
    permanent `select_min_volume_24h_usd` never changes, and a non-positive
    trial value is a mistake, not a signal.
    """
    trial = cli_trial_usd
    if trial is None:
        trial = _CFG.select_min_volume_24h_usd_trial
    if trial is not None and trial > 0:
        return float(trial)
    return MIN_VOLUME_24H


def _effective_spread_bar(cli_trial: Optional[float]) -> float:
    """The spread ceiling this run gates on: CLI trial > permanent.

    WIDE-BOOK TRIAL (#145). Unlike the depth and volume trials there is no
    config-level trial field to fall back to, and that is deliberate: this
    ceiling is a SAFETY gate, not a selection preference, so the only way to
    loosen it is a deliberate flag on a read-only run. `--trial-spread`
    declares this process a rehearsal first -- the ranker places no orders, it
    reads books and writes a market list -- which is what
    `config.resolve_wide_book_trial` requires before it will widen anything.

    A non-positive trial is a mistake, not a signal: fall back to the
    permanent bar rather than gating on nothing.
    """
    if cli_trial is not None and cli_trial > 0:
        rehearsal.declare_rehearsal()
        return _load_cfg_module.resolve_wide_book_trial(str(cli_trial))
    return MAX_BOOK_SPREAD


def _positive_int(v: str) -> int:
    n = int(v)
    if n < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return n


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """Parse the command line BEFORE any network work happens.

    This used to be a hand-rolled scan of sys.argv for "--top", which meant
    `--help` was not a flag the script recognised -- it was silently ignored,
    and asking for usage instead ran the whole pipeline: a few hundred requests
    against the venue, ending in an overwrite of runtime/markets.json. A mistyped
    flag did the same, and `--top` with no value raised IndexError. argparse
    turns all three into a usage error, and exits before the first request.
    """
    p = argparse.ArgumentParser(
        prog="python -m scripts.filter_markets",
        description=__doc__.split("\n\n")[0],
        epilog="Writes the winners to runtime/markets.json, which the Trader "
               "reads as its market universe.")
    p.add_argument("--top", type=_positive_int, default=20, metavar="N",
                   help="how many markets to write (default: 20)")
    p.add_argument("--out-dir", default=None, metavar="DIR",
                   help="write every artifact (markets.json, pipeline.json, "
                        "market_universe.json, near-miss logs, ranking.marker) "
                        "under DIR instead of runtime/ -- a trial ranker run "
                        "publishes to runtime/trials/<run-id>/ so it cannot "
                        "leak into the shared baseline feed. Default: runtime/")
    p.add_argument("--dry-run", action="store_true",
                   help="score and print the ranking, but leave "
                        "runtime/markets.json untouched")
    p.add_argument("--trial-depth", type=float, default=None, metavar="USD",
                   help="DEPTH-GATE TRIAL (U32): gate on this top-3 bid depth "
                        "bar instead of the permanent one ($%.0f). Wins over "
                        "HUNTER_DEPTH_TRIAL_USD; the permanent config value is "
                        "never changed. Adopted markets are tagged "
                        "trial_depth_usd in runtime/markets.json so their "
                        "markouts can be watched before the bar is loosened "
                        "permanently. See scripts/trial_depth_gate.py for the "
                        "recorded-data replay that shows which markets a bar "
                        "adopts." % MIN_TOP3_DEPTH_USD)
    p.add_argument("--trial-spread", type=float, default=None, metavar="SPREAD",
                   help="WIDE-BOOK TRIAL (#145): admit books up to this spread "
                        "instead of the permanent ceiling (%.2f). Run 145 "
                        "measured the touch pair at or above max_pair_cost in "
                        "1247 of 1247 samples on one-tick books, and the only "
                        "sub-$0.99 pairs it saw came from books that had "
                        "widened. Adopted markets are tagged trial_spread in "
                        "runtime/markets.json. The ranker places no orders, so "
                        "this flag declares the process a rehearsal; the same "
                        "ceiling in a QUOTING process needs "
                        "HUNTER_WIDE_BOOK_TRIAL under core_brain.shadow_run."
                        % MAX_BOOK_SPREAD)
    p.add_argument("--trial-volume", type=float, default=None, metavar="USD",
                   help="VOLUME-GATE TRIAL (U36): gate 24h volume on this bar "
                        "instead of the permanent one ($%.0f). Wins over "
                        "HUNTER_VOLUME_TRIAL_USD; the permanent config value is "
                        "never changed. Adopted markets are tagged "
                        "trial_volume_usd in runtime/markets.json so their "
                        "markouts can be watched before the bar is loosened "
                        "permanently." % MIN_VOLUME_24H)
    p.add_argument("--full-scan", action="store_true",
                   help="paginate Gamma until the listing is exhausted instead "
                        "of stopping one boundary page past the volume floor. "
                        "An operator audit flag: the every-10-min rank keeps "
                        "the bounded scan, because paginating thousands of "
                        "sub-floor rows per rank buys nothing the funnel "
                        "needs. The universe file records whether a scan was "
                        "truncated either way.")
    p.add_argument("--legacy-rewards", action="store_true",
                   help="run the retired two-path scan once for comparison: "
                        "/sampling-markets reward candidates, scored against "
                        "their venue emission with the $1.50 payout floor, "
                        "alongside the unified universe. The reward path pays "
                        "nothing on the markets that actually trade (they "
                        "publish clobRewards: 0) and is deleted from the "
                        "unified scan; this flag exists only to measure that "
                        "claim against the live funnel before the code comes "
                        "out.")
    return p.parse_args(argv)


# A depth-gate reason embeds its measurement -- "YES: top-3 bid depth
# $612.00 <= $1,000.00" -- and the near-miss log records it so the stats can
# answer "how many of these would a modest loosening have admitted?".
_DEPTH_RE = re.compile(
    r"top-3 bid depth \$([\d,.]+) <= \$([\d,.]+)", re.IGNORECASE)

# A volume-gate reason embeds its measurement too -- "24h volume $12,906 <
# $250,000" -- and the VOLUME near-miss log (U34) records it so the stats can
# answer "how many of these would a looser bar have admitted?".
_VOLUME_RE = re.compile(
    r"24h volume \$([\d,.]+) < \$([\d,.]+)", re.IGNORECASE)


def _log_rank_near_misses(out, rejected, verdicts, ts=None,
                          out_dir: Path | str | None = None) -> int:
    """Append this rank's near-misses to runtime/near_misses.jsonl.

    A NEAR-MISS is a rejected market whose if-adopted first-dollar marginal
    return clears the allocator's floor -- the green cards on the FILTERS
    lane. The ranker's own gates refused it, but the allocator would have
    funded it, so every one is a candidate for loosening a gate. Written as
    ONE line per rank (the greens embedded), so a rank with zero greens still
    records itself -- the stats reader needs to tell "no greens" from "no
    data", and the stability bar is a fraction of ranks.

    Telemetry only, and it is the input to the dashboard's near-miss tracker:
    it accumulates whether the estimate is CONSISTENT over days -- the
    precondition for a controlled gate-loosening trial. Consistency is not
    profitability; the trial measures that.

    Returns how many greens were logged this rank.
    """
    greens = []
    depth_unparsed = 0
    for r in out:
        if r.get("eligible"):
            continue
        v = verdicts.get(id(r))
        if not v or not v["would_fund"]:
            continue
        d = _DEPTH_RE.search(r.get("reject_reason") or "")
        if not d and "top-3 bid depth" in (r.get("reject_reason") or ""):
            # The reason format changed and the parse went quiet -- the
            # small-margin bar would undercount with no signal. Recorded on
            # the line so the tracker can show it.
            depth_unparsed += 1
        greens.append({
            "cid": r.get("cid"), "title": r.get("title"),
            "slug": r.get("slug"),
            "cause": _cause(r.get("reject_reason") or ""),
            "reason": r.get("reject_reason"),
            "source": r.get("source"),
            "marg_pct_day": v["marg_pct_day"], "pot_day": v["pot_day"],
            "competition": v["competition"],
            "trap": v.get("trap", False),
            "threshold_pct": v["threshold_pct"],
            "volume_24h": r.get("volume_24h"),
            "days": r.get("days_to_resolve"),
            "depth_measured": (float(d.group(1).replace(",", ""))
                                if d else None),
            "depth_bar": (float(d.group(2).replace(",", ""))
                           if d else None),
        })
    line = {"ts": ts if ts is not None else time.time(),
            "scored": len(out), "rejected": rejected,
            "depth_unparsed": depth_unparsed,
            "greens": greens}
    d = Path(out_dir) if out_dir is not None else RUN
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "near_misses.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")
    return len(greens)


def _log_rank_volume_near_misses(out, rejected, verdicts=None, ts=None,
                                 out_dir: Path | str | None = None) -> int:
    """Append this rank's volume-rejects to runtime/volume_near_misses.jsonl.

    The DEPTH near-miss log records only would-fund greens, and U33's triage
    showed the binding constraint is the VOLUME gate, not depth: on the
    recorded population, 83 of 110 depth-rejects -- including 5 of the 6
    near-misses -- would fail the live $250k/24h bar anyway, and the $500
    depth trial adopted zero new markets because the depth-clear candidates
    failed live re-verification on volume. That population never reached any
    log: volume rejects are refused by `tradable` inside `evaluate`, and the
    depth logger only keeps greens.

    This sibling log records EVERY volume-rejected market whose reason
    carries a measured 24h volume ("24h volume $X < $Y" parses it out;
    "volume unknown" is a data gap, not a near-miss, and is skipped but
    counted). One line per rank, exactly like the depth log, so the stats
    reader accumulates days, unique markets, and how many measured volumes
    came within half the bar -- the same evidence shape that licensed the
    depth trial, applied to the gate that actually binds.

    Returns how many volume-rejects were logged this rank.
    """
    vols = []
    volume_unknown = 0
    for r in out:
        if r.get("eligible"):
            continue
        reason = r.get("reject_reason") or ""
        if _cause(reason) != "volume":
            continue
        v = _VOLUME_RE.search(reason)
        if not v:
            # "volume unknown": gamma never returned a reading. Not a
            # near-miss -- but count it so the tracker can show the gap
            # instead of silently undercounting the population.
            volume_unknown += 1
            continue
        vd = verdicts.get(id(r)) if verdicts else None
        vols.append({
            "cid": r.get("cid"), "title": r.get("title"),
            "slug": r.get("slug"),
            "cause": "volume",
            "reason": reason,
            "source": r.get("source"),
            "volume_measured": float(v.group(1).replace(",", "")),
            "volume_bar": float(v.group(2).replace(",", "")),
            "volume_24h": r.get("volume_24h"),
            "days": r.get("days_to_resolve"),
            # The allocator verdict travels too, so the tracker can show the
            # pot and competition of the population, not just its volume.
            "pot_day": (vd["pot_day"] if vd else r.get("daily") or 0.0),
            "competition": (vd["competition"] if vd
                             else r.get("their_score")),
            "marg_pct_day": vd["marg_pct_day"] if vd else None,
            "trap": bool(vd and vd.get("trap")),
        })
    line = {"ts": ts if ts is not None else time.time(),
            "scored": len(out), "rejected": rejected,
            "volume_unknown": volume_unknown,
            "volumes": vols}
    d = Path(out_dir) if out_dir is not None else RUN
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "volume_near_misses.jsonl", "a",
              encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")
    return len(vols)


def _write_universe_file(universe_rows: list[dict],
                         discovery_meta: dict | None = None,
                         out_dir: Path | str | None = None) -> None:
    """Persist the auditable raw population to runtime/market_universe.json.

    The funnel answers "how many, and which gate?"; this file answers "which
    market, exactly?". One row per discovered candidate that reached the
    scoring pool -- the cheap metadata filters (order book, binary, quoted
    spread) are counted and sampled in the snapshot's discovery block instead
    of being scored. Full rejection detail (reason, movement, book stats)
    and full detail on every eligible row, picked or not.

    Written atomically -- temp file plus rename -- for the same reason
    `markets.json` is: the dashboard reads it on its own schedule and a
    half-written JSON file is a broken pane.
    """
    snap = {
        "ts": time.time(),
        "discovery": discovery_meta or {},
        "rows": universe_rows,
    }
    d = Path(out_dir) if out_dir is not None else RUN
    d.mkdir(parents=True, exist_ok=True)
    f = d / "market_universe.json"
    tmp = d / f"market_universe.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        tmp.write_text(json.dumps(snap), encoding="utf-8")
        tmp.replace(f)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _write_pipeline_snapshot(cands, spread_cands, out, eligible, picked,
                             causes, census, gates, attempted,
                             rejected, verdicts=None,
                             depth_gate_usd: Optional[float] = None,
                             trial_depth_usd: Optional[float] = None,
                             volume_gate_usd: Optional[float] = None,
                             trial_volume_usd: Optional[float] = None,
                             spread_gate: Optional[float] = None,
                             trial_spread: Optional[float] = None,
                             discovery: Optional[dict] = None,
                             out_dir: Path | str | None = None) -> None:
    """Persist the whole selection funnel to runtime/pipeline.json.

    runtime/markets.json keeps only the winners, so the dashboard can show the
    fleet but not the funnel that produced it. This file keeps the rest of
    the run: the raw pools the ranker listed, every rejection bucketed by
    gate with example titles, the eligible-but-unpicked ranking, and the
    picks -- enough to replay the funnel's shape live, run after run.

    In the unified universe there is no reward pool: `raw.rewards` is empty
    and `counts.funded` reports the LEGACY path's size only (0 unless
    `--legacy-rewards`). The full raw population is `market_universe.json`;
    the snapshot keeps a preview of the scored pool plus the discovery
    metadata (pages, cheap-filter counts, truncation).

    Telemetry only: nothing in the fleet reads it as input, so writing it
    during a --dry-run audit is safe (and useful -- the dashboard then shows
    the audited funnel, timestamp and gates line included, rather than a
    silently stale picture).
    """
    def _days(m):
        return days_to_resolve(m.get("end_date_iso"))

    raw_rewards = []
    for rate, m in cands[:24]:
        raw_rewards.append({
            "title": (m.get("question") or "")[:80],
            "slug": m.get("market_slug") or m.get("slug") or "",
            "rate": round(rate, 2),
            "days": _days(m),
        })
    raw_spread = []
    for m in spread_cands[:24]:
        raw_spread.append({
            "title": (m.get("question") or "")[:80],
            "slug": m.get("market_slug") or m.get("slug") or "",
            "volume": round(float(m.get("_volume_24h") or 0.0), 0),
            "spread": m.get("_spread"),
            "days": _days(m),
        })

    # Estimated allocator verdict per scored-and-rejected market, keyed by
    # object id so the per-bucket near-miss count and the example cards are
    # computed from the same rows and cannot disagree. Precomputed by `main`
    # and shared with the near-miss logger; computed here for direct callers.
    if verdicts is None:
        verdicts = {id(r): _if_adopted(r) for r in out}

    rejections = []
    for cause, n in sorted(causes.items(), key=lambda kv: -kv[1]):
        bucket_rows = [r for r in out
                       if not r["eligible"] and _cause(r["reject_reason"]) == cause]
        examples = []
        for r in bucket_rows[:4]:
            v = verdicts.get(id(r))
            examples.append({
                "title": r["title"],
                "slug": r.get("slug", ""),
                "reason": r["reject_reason"],
                "volume": r.get("volume_24h"),
                "days": r.get("days_to_resolve"),
                # Absent (not None) for markets with no book reading -- an
                # identity reject never fetched the book, so there is nothing
                # to estimate from and the card shows no verdict.
                **({"marg": v} if v else {}),
            })
        # would_fund counts only CREDIBLE near-misses: an empty-book mirage
        # (the estimate dividing by ~zero competition) is not evidence for
        # loosening a gate -- the traps are shown separately, not hidden.
        would_fund = sum(1 for r in bucket_rows
                         if (v := verdicts.get(id(r)))
                         and v["would_fund"] and not v["trap"])
        traps = sum(1 for r in bucket_rows
                    if (v := verdicts.get(id(r))) and v and v["trap"])
        rejections.append({"cause": cause, "n": n,
                           "would_fund": would_fund, "traps": traps,
                           "examples": examples})

    def _row(r: dict) -> dict:
        return {
            # The dashboard suppresses an eligible runner-up that is already
            # shown as quoting, and it matches on this id. Without it every
            # picked market rendered twice in the passed column.
            "cid": r.get("cid") or "",
            "title": r["title"], "slug": r.get("slug", ""), "source": r["source"],
            "income": r.get("est_income"), "capital": r.get("est_capital"),
            "ret_day_pct": r.get("return_pct_day"),
            "volume": r.get("volume_24h"), "days": r.get("days_to_resolve"),
        }

    snap = {
        "ts": time.time(),
        "census": census,
        "gates": gates,
        # Which depth bar this rank gated on, and whether it was a TRIAL bar
        # rather than the permanent one -- the dashboard's funnel would
        # otherwise silently show a loosened gate as if it were the standing
        # contract.
        "depth_gate_usd": depth_gate_usd,
        "trial_depth_usd": trial_depth_usd,
        # Which volume bar this rank gated on -- same trial contract as depth.
        "volume_gate_usd": (volume_gate_usd if volume_gate_usd is not None
                            else MIN_VOLUME_24H),
        "trial_volume_usd": trial_volume_usd,
        # The remaining gate bars, so the dashboard states the contract it is
        # actually screening against rather than a number frozen into the
        # markup. `max_pair_cost` is the sub-dollar rule the strategy rests
        # on; a stale figure there is worse than no figure at all.
        # Which spread ceiling this rank gated on, on the same trial contract
        # as depth and volume. Passing the module constant here regardless
        # would report the PERMANENT ceiling on a run that admitted books up
        # to a looser one -- the dashboard funnel showing a widened safety
        # gate as if it were the standing contract, which is exactly what the
        # comment above exists to prevent.
        "spread_gate": (spread_gate if spread_gate is not None
                        else MAX_BOOK_SPREAD),
        "trial_spread": trial_spread,
        "horizon_gate_days": MAX_DAYS_TO_RESOLVE,
        # The payout floor is a REWARD rule, and `evaluate` applies it as one:
        # a spread market is paid by whoever lifts the offer and passes on any
        # income at all. Exported per source so the dashboard cannot state one
        # universal bar and call a passing spread market a failure.
        # `reward_*` is inclusive (>=); `spread_*` is exclusive (>).
        "reward_min_income_usd_day": MIN_PAYOUT * FLOOR_MULTIPLE,
        "spread_min_income_usd_day": 0.0,
        "max_pair_cost": getattr(_CFG, "max_pair_cost", 0.995),
        "counts": {
            # LEGACY ACCOUNTING ONLY. In the unified universe the reward pool
            # is gone -- `funded` reports the --legacy-rewards path's size and
            # is 0 on every normal rank. `spread_universe` is the whole
            # scored pool.
            "funded": len(cands),
            "spread_universe": len(spread_cands),
            "attempted": attempted,
            "scored": len(out),
            "dropped_no_verdict": attempted - len(out),
            "rejected": rejected,
            "eligible": len(eligible),
            "picked": len(picked),
        },
        "discovery": discovery or {},
        "raw": {"rewards": raw_rewards, "spread": raw_spread},
        "rejections": rejections,
        "final": [_row(r) for r in eligible],
        "picked": [_row(r) for r in picked],
    }
    d = Path(out_dir) if out_dir is not None else RUN
    d.mkdir(parents=True, exist_ok=True)
    _publish_json(d / "pipeline.json", snap)


# How many times a rename may lose the race to a reader's open handle before
# the publish gives up. On Windows the dashboard polls these files, and
# `Path.replace` raises PermissionError while it holds one; the read is short,
# so a handful of short retries clears it.
_PUBLISH_ATTEMPTS = 5
_PUBLISH_BACKOFF_SEC = 0.2


def _publish_json(target: Path, payload) -> bool:
    """Swap `payload` into `target` by rename, or leave the old file in place.

    Write-then-rename, never a write over the live path: the fleet re-reads
    `runtime/markets.json` on its own schedule and a half-written file is a
    SystemExit on the next re-rank.

    A rename that loses the race to a reader's open handle raises
    PermissionError on Windows, which used to kill the swap outright and leave
    the trader quoting an empty universe -- so the rename retries. When every
    retry loses, the PREVIOUS file is kept and the caller is told: a
    `write_text` over the live path would be exactly the torn read the rename
    exists to prevent, and a stale universe is the safe failure, since the
    trader keeps the markets it already adopted. Any other OSError (a full
    disk, a vanished directory) propagates -- it is not this race.

    Returns True when the file was replaced.
    """
    target.parent.mkdir(exist_ok=True)
    tmp = target.parent / f"{target.stem}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        last: Optional[PermissionError] = None
        for attempt in range(_PUBLISH_ATTEMPTS):
            try:
                tmp.replace(target)
                return True
            except PermissionError as e:
                last = e
                if attempt < _PUBLISH_ATTEMPTS - 1:
                    time.sleep(_PUBLISH_BACKOFF_SEC)
        print(f"WARN: could not publish {target.name} after "
              f"{_PUBLISH_ATTEMPTS} attempts ({last}); keeping the previous "
              f"file rather than truncating it", file=sys.stderr)
        return False
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


_worker_local = threading.local()


def _worker_session() -> requests.Session:
    """One `requests.Session` per worker thread, created lazily.

    The requests documentation is explicit that a Session is not thread-safe
    ("use a session per thread, or use a connection pool elsewhere"), and the
    ranking pool ran 12 ThreadPool workers against one shared session -- a
    latent shared-state bug that every future edit to the fetchers could
    trip. Each thread lazily owns its own keep-alive pool instead; the
    session is created on first use inside the thread and reused for all of
    that thread's markets.
    """
    s = getattr(_worker_local, "session", None)
    if s is None:
        s = requests.Session()
        _worker_local.session = s
    return s


def resolve_queue_bar(cfg) -> Optional[float]:
    """The maker-queue bar in force, or None while it is not being enforced.

    Record-only is the shipping default, and it has to be honoured HERE rather
    than deeper: with no bar resolved, `score_pool` passes none, `evaluate`
    calls no measurement function, and the ranking pass buys no extra tape
    reads for a number nothing will act on.

    A bar that never reaches `evaluate` is worse than no bar -- it reads as an
    enforced limit in the config while changing nothing. That failure has now
    been found twice on this branch, at two different frames, which is why the
    tests assert the bar at every frame from here down to the predicate.
    """
    if not getattr(cfg, "enforce_max_queue_minutes", False):
        return None
    bar = float(getattr(cfg, "select_max_queue_minutes", 0.0) or 0.0)
    # `> 0`, not truthiness. A negative bar is truthy, so it would travel down
    # the stack and make `queue_bar_reject` pay for a tape read per market
    # before `maker_queue_allowed` declined to use it -- a disabled rule
    # costing venue work. 0 disables, as everywhere else here; negative is a
    # typo and disables too rather than half-running.
    return bar if bar > 0 else None


def score_pool(jobs: list[tuple[float, dict, Optional[float], str]],
               *, session_factory=_worker_session,
               max_workers: int = 12,
               min_depth_usd: Optional[float] = None,
               min_volume_usd: Optional[float] = None,
               min_movement_usd: Optional[float] = None,
               max_spread: Optional[float] = None,
               max_queue_minutes: Optional[float] = None,
               queue_minutes_fn=None) -> list[dict]:
    """Score candidate jobs across a worker pool, one session per worker.

    `session_factory` is injected so a test can prove the pool never shares
    one session object across workers -- the documented-not-thread-safe
    shape `main` used to have. Each worker's session comes from the factory,
    so the #15 seam still holds: `evaluate` takes a session and opens no
    connection itself.
    """
    out: list[dict] = []
    with cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
        for r in ex.map(
                lambda a: evaluate(session_factory(), a[0], a[1], a[2],
                                   source=a[3],
                                   min_depth_usd=min_depth_usd,
                                   min_volume_usd=min_volume_usd,
                                   min_movement_usd=min_movement_usd,
                                   max_spread=max_spread,
                                   max_queue_minutes=max_queue_minutes,
                                   queue_minutes_fn=queue_minutes_fn),
                jobs):
            if r:
                out.append(r)
    return out


def _score_universe(universe: list[dict], *, volume_bar: float,
                    movement_bar: float, depth_bar: float,
                    spread_bar: float) -> tuple[list[dict], int]:
    """Score the unified universe and return (scored rows, attempted count).

    One job per candidate: the pot is ALWAYS spread capture --
    `spread_capture_daily(volume_24h, quoted_spread)`, the income a taker
    crossing pays the resting maker. Reward state is not consulted: a market
    carrying `clobRewards` is scored on the same terms, its reward config
    feeding only the score-window width.
    """
    jobs = [(spread_capture_daily(m["_volume_24h"], m["_spread"],
                                  _CFG.spread_capture_frac),
             m, m["_volume_24h"], "spread")
            for m in universe]
    out = score_pool(jobs, min_depth_usd=depth_bar,
                     min_volume_usd=volume_bar,
                     min_movement_usd=movement_bar,
                     max_spread=spread_bar,
                     max_queue_minutes=resolve_queue_bar(_CFG))
    return out, len(jobs)


def _legacy_reward_candidates(s: requests.Session) -> tuple[list[dict], list[dict], list[tuple[float, dict]], dict[str, float]]:
    """The retired two-path scan, behind `--legacy-rewards`, for comparison.

    Returns (the old top-250 reward job list, additional legacy-only spread
    rows -- none today -- all funded candidates, and the chunked gamma volume
    map). The legacy reward rows are scored as "rewards" alongside the
    unified universe so the funnel can show what retiring the reward path
    cost -- which the evidence so far says is nothing, because the markets
    that actually trade publish clobRewards: 0.
    """
    # LEGACY PATH 1: the old /sampling-markets reward scan, verbatim.
    try:
        data = s.get("https://clob.polymarket.com/sampling-markets",
                     timeout=30).json()
    except Exception as exc:
        # The file's fail-soft contract, not a list of expected errors: a
        # ChunkedEncodingError from a flaky CDN aborts main() just as dead
        # as a timeout, and by this point a full scored pool is in hand.
        print("WARNING: legacy-rewards sampling-markets unavailable "
              f"({type(exc).__name__}); continuing without legacy markets",
              file=sys.stderr)
        return [], [], [], {}
    if not isinstance(data, dict):
        print("WARNING: legacy-rewards sampling-markets returned an "
              "unreadable response; continuing without legacy markets",
              file=sys.stderr)
        return [], [], [], {}
    cands = []
    for m in data.get("data") or []:
        if not m.get("accepting_orders") or m.get("closed"):
            continue
        rate = sum(x.get("rewards_daily_rate", 0) or 0
                   for x in ((m.get("rewards") or {}).get("rates") or []))
        if rate > 0:
            cands.append((rate, m))
    cands.sort(key=lambda x: -x[0])
    # Volume lives on gamma, the book lives on the CLOB. Fetched up front for
    # the whole candidate list so the per-market workers stay one round trip
    # each, as they were before the filter existed.
    short = [(rate, m) for rate, m in cands[:250]
             if (days_to_resolve(m.get("end_date_iso")) or -1) >= 0]
    vols = gamma_volume(s, [m["condition_id"] for _, m in short])
    # LEGACY PATH 2: the old spread scan, minus the markets the unified
    # universe already carries -- so no market is scored twice and competes
    # against itself in the ranking.
    legacy_spread_extra = []
    return short, legacy_spread_extra, cands, vols


def main() -> None:
    args = parse_args()
    top = args.top
    # Trial isolation (#291): every artifact below lands under out_dir, which
    # is runtime/ unless --out-dir reroutes it. No prior-feed reads exist to
    # reroute -- the ranker only writes.
    out_dir = Path(args.out_dir) if args.out_dir is not None else RUN
    # DEPTH-GATE TRIAL (U32): the bar this run gates on. Same contract as the
    # permanent config value, but opt-in per run and never written back to
    # config; adopted markets are tagged so the trial's markouts can be
    # watched before the bar is loosened permanently.
    trial_bar = _effective_depth_bar(args.trial_depth)
    trial_active = trial_bar != MIN_TOP3_DEPTH_USD
    volume_bar = _effective_volume_bar(args.trial_volume)
    volume_trial_active = volume_bar != MIN_VOLUME_24H
    movement_bar = MIN_MOVEMENT_USD
    # WIDE-BOOK TRIAL (#145). Resolved here so the bar travels as an argument
    # from this frame down to `pair_books_allowed`, rather than as a module
    # global that a test cannot vary.
    spread_bar = _effective_spread_bar(args.trial_spread)
    spread_trial_active = spread_bar != MAX_BOOK_SPREAD

    # Up-front universe fetches run sequentially on the main thread and keep
    # their own keep-alive session; the worker pool below uses one session
    # per thread (see `_worker_session`) instead of sharing this one.
    s = requests.Session()

    # THE ONE UNIVERSE. Every tradable binary Gamma market, reward-funded or
    # not. `end_date_min`/`end_date_max` are left OFF the request on purpose:
    # long-dated markets are fetched and refused auditably by the horizon
    # gate, so the funnel shows what the horizon actually removes.
    universe, disc_meta = gamma_universe(s, min_volume_usd=volume_bar,
                                         full_scan=args.full_scan)
    volume_str = (f"${volume_bar:,.0f}"
                  + (f" [TRIAL vs permanent ${MIN_VOLUME_24H:,.0f}]"
                     if volume_trial_active else ""))
    print(f"universe: {len(universe)} tradable binaries "
          f"({disc_meta['pages_fetched']} pages, {disc_meta['rows_scanned']} rows"
          f"{', TRUNCATED' if disc_meta['truncated'] else ''})")
    if disc_meta["cheap_rejects"]:
        summary = ", ".join(f"{k}={v}"
                            for k, v in sorted(disc_meta["cheap_rejects"].items(),
                                               key=lambda kv: -kv[1]))
        print(f"  cheap filters: {summary}")

    out, attempted = _score_universe(
        universe, volume_bar=volume_bar, movement_bar=movement_bar,
        depth_bar=trial_bar, spread_bar=spread_bar)

    # --legacy-rewards: the retired two-path scan, scored alongside the
    # unified universe so the funnel shows what retiring it cost. The reward
    # markets are ALSO in the unified universe -- this measures the old
    # pot/payout-floor treatment of the same books, not extra markets.
    cands: list[tuple[float, dict]] = []
    if args.legacy_rewards:
        short, legacy_extra, cands, vols = _legacy_reward_candidates(s)
        legacy_jobs = [(rate, m, vols.get(m["condition_id"]), "rewards")
                       for rate, m in short]
        legacy_jobs += [(spread_capture_daily(m["_volume_24h"], m["_spread"],
                                              _CFG.spread_capture_frac),
                        m, m["_volume_24h"], "spread")
                       for m in legacy_extra]
        legacy_out = score_pool(
            legacy_jobs, min_depth_usd=trial_bar,
            min_volume_usd=volume_bar, min_movement_usd=movement_bar,
            max_spread=spread_bar,
            max_queue_minutes=resolve_queue_bar(_CFG))
        out += legacy_out
        attempted += len(legacy_jobs)
        print(f"legacy-rewards: {len(cands)} funded candidates, "
              f"{len(short)} scored (top 250 by rate)")
    # Eligibility BEFORE ranking. Sorting on return_pct_day alone put the
    # top-ranked market at $0.25/day actual against $18.96 projected, because a
    # spectacular percentage return on an income of eleven cents is still
    # eleven cents -- and under the payout floor it is zero.
    eligible = [r for r in out if r["eligible"]]
    rejected = len(out) - len(eligible)
    eligible.sort(key=lambda r: -r["return_pct_day"])
    picked = eligible[:top]

    if not args.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        marker = out_dir / "ranking.marker"
        # Validate marker both for PID liveness AND timestamp freshness
        if marker.exists():
            try:
                mdata = json.loads(marker.read_text(encoding="utf-8"))
                owner_pid = mdata.get("pid")
                marker_ts = mdata.get("ts")
                now_ts = time.time()
                # Stale threshold: 5 minutes. If marker is older, it's abandoned.
                MARKER_STALE_SEC = 300
                is_stale = (marker_ts is None or (now_ts - marker_ts) > MARKER_STALE_SEC)
                is_dead = (owner_pid is None or not _pid_is_running(owner_pid))

                if is_stale or is_dead:
                    # Clear stale or invalid marker
                    try:
                        marker.unlink()
                    except OSError:
                        pass
                elif owner_pid != os.getpid():
                    # Valid, fresh marker from a different running process
                    print(f"Skipping write: concurrent ranker run detected (PID {owner_pid})")
                    return
            except Exception:
                # Malformed marker, clear it
                try:
                    marker.unlink()
                except OSError:
                    pass
        try:
            marker.write_text(json.dumps({"pid": os.getpid(), "ts": time.time()}), encoding="utf-8")
        except Exception:
            pass

        # STAGING MARKER. A trial-run adoption is not a permanent gate change:
        # each picked spec carries the bar it was admitted under, so the fleet
        # and dashboard can identify trial markets and their markouts are the
        # evidence that decides whether the bar becomes permanent.
        if trial_active:
            for r in picked:
                r["trial_depth_usd"] = trial_bar
        if volume_trial_active:
            for r in picked:
                r["trial_volume_usd"] = volume_bar
        if spread_trial_active:
            for r in picked:
                r["trial_spread"] = spread_bar

        try:
            _publish_json(out_dir / "markets.json", picked)
        finally:
            # Remove the marker after successful write so subsequent runs aren't blocked
            if marker.exists():
                try:
                    marker.unlink()
                except OSError:
                    pass

    ti = sum(r["est_income"] for r in picked)
    tc = sum(r["est_capital"] for r in picked)
    # Rejections grouped by cause. A run that returns nothing must say whether
    # the venue had no tradeable market today or the filters are set wrong --
    # a bare count cannot, and a silent empty universe is how the fleet ended
    # up quoting markets that never traded.
    causes: dict[str, int] = {}
    for r in out:
        if not r["eligible"]:
            k = _cause(r["reject_reason"])
            causes[k] = causes.get(k, 0) + 1
    census = (f"scored {len(out)}, rejected {rejected} "
              f"({', '.join(f'{k}={v}' for k, v in sorted(causes.items())) or 'none'}), "
              f"{'would write' if args.dry_run else 'wrote'} top {len(picked)}"
              f" -> runtime/markets.json")
    print(census)
    depth_bar_str = (f"${trial_bar:,.0f}"
                     + (f" [TRIAL vs permanent ${MIN_TOP3_DEPTH_USD:,.0f}]"
                        if trial_active else ""))
    volume_bar_str = (f"${volume_bar:,.0f}"
                      + (f" [TRIAL vs permanent ${MIN_VOLUME_24H:,.0f}]"
                         if volume_trial_active else ""))
    gates = (f"gates: primary/main-line only, blocked submarkets/live; "
             f"24h volume >= {volume_bar_str}, "
             f"YES+NO top-3 bid depth >= {depth_bar_str} each, "
             f"spread <= {spread_bar:.2f}"
             f"{' (TRIAL)' if spread_trial_active else ''}, "
             f"resolves within {MAX_DAYS_TO_RESOLVE:.0f}d, "
             f"movement >= ${movement_bar:,.0f}/"
             f"{int(round(MOVEMENT_WINDOW_SEC / 60.0))}m, "
             f"income > 0 (spread; >= ${MIN_PAYOUT * FLOOR_MULTIPLE:.2f}/day "
             f"legacy-rewards only)\n")
    print(gates)
    if trial_active:
        print(f"DEPTH-GATE TRIAL: gating on ${trial_bar:,.0f} instead of "
              f"the permanent ${MIN_TOP3_DEPTH_USD:,.0f}; adopted markets "
              "are tagged trial_depth_usd and their markouts are the "
              "decision evidence (see scripts/trial_depth_gate.py)")
    if volume_trial_active:
        print(f"VOLUME-GATE TRIAL: gating on ${volume_bar:,.0f} instead of "
              f"the permanent ${MIN_VOLUME_24H:,.0f}; adopted markets are "
              "tagged trial_volume_usd and their markouts are the "
              "decision evidence")

    verdicts = {id(r): _if_adopted(r) for r in out}
    _write_universe_file(out, disc_meta, out_dir=out_dir)
    _write_pipeline_snapshot(
        cands=cands, spread_cands=universe, out=out, eligible=eligible,
        picked=picked, causes=causes, census=census, gates=gates,
        attempted=attempted, rejected=rejected, verdicts=verdicts,
        discovery=disc_meta,
        depth_gate_usd=trial_bar,
        trial_depth_usd=(trial_bar if trial_active else None),
        volume_gate_usd=volume_bar,
        trial_volume_usd=(volume_bar if volume_trial_active else None),
        spread_gate=spread_bar,
        trial_spread=(spread_bar if spread_trial_active else None),
        out_dir=out_dir)
    # The near-miss log is the accumulated evidence for a gate decision; a
    # dry-run audit must not pollute it (it would double-count against the
    # supervised every-10-min ranks).
    if not args.dry_run:
        n_greens = _log_rank_near_misses(out, rejected, verdicts,
                                         out_dir=out_dir)
        if n_greens:
            print(f"near-misses logged: {n_greens} would clear the floor")
        # The VOLUME tracker (U34): every volume-reject with a measured
        # reading, for the gate U33 showed actually binds. Same dry-run guard
        # as the depth log -- an audit must not pollute the evidence.
        n_vols = _log_rank_volume_near_misses(out, rejected, verdicts,
                                                 out_dir=out_dir)
        if n_vols:
            print(f"volume-rejects logged: {n_vols} measured "
                  "(volume near-miss tracker)")
    n_spread = sum(1 for r in picked if r["source"] == "spread")
    print(f"picked {n_spread} spread / {len(picked) - n_spread} reward\n")
    print(f"{'market':<40}{'src':>7}{'$/day':>7}{'capital':>9}{'ret%/d':>8}")
    for r in picked:
        # Windows consoles default to a legacy codepage; market titles carry
        # curly quotes and accents that crash a plain print AFTER the file is
        # already written, which looks like a failed run when it succeeded.
        title = r["title"][:40].encode("ascii", "replace").decode("ascii")
        print(f"{title:<40}{r['source'][:6]:>7}{r['est_income']:>7.2f}"
              f"{r['est_capital']:>9.0f}{r['return_pct_day']:>8.2f}")
    if tc:
        print(f"\nTOTAL capital ${tc:,.0f}  income ${ti:,.2f}/day  = {100*ti/tc:.2f}%/day")


if __name__ == "__main__":
    main()
