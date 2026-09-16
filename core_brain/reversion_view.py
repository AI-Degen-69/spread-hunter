"""A read-only window onto the reversion forward test, for the dashboard.

`core_brain.reversion_watch` runs for hours and answers one question of the
venue: after a jump, does an esports match-winner market come back at prices you
could actually have traded at? The recording half is a terminal program, so
between the moment it starts and the moment someone remembers to read the store
there is nothing to look at.

This module is that reading, served while the watch is still running:

* `reversion_status` -- how much has been collected: markets watched, jumps
  seen, how many are scored and how many are still waiting out their fifteen
  minutes.
* `reversion_results` -- realised cents per share, split by league and by price
  band, with the same significance bar the backward measurement was held to.

The split is the point. Dota jumps often and the jumps stick; LoL jumps often
and they come back; the 0.35-0.65 band held 82% of the measured events. A page
that pooled them would report one number that is true of nothing.

READ-ONLY, AND OUTSIDE THE MONEY PATH. Every store is opened `mode=ro`, and
`data/orders.db` is refused by name at every layer including the environment --
an operator exporting `SHL_REVERSION_DB=data/orders.db` to "see the real
numbers" is the exact mistake the refusal exists for. The production registry
has a different schema, so pointing this at it would not fail loudly, it would
report that the test found nothing.
"""
from __future__ import annotations

import datetime as _dt
import math
import sqlite3
import statistics
import time
from pathlib import Path
from typing import Any, Callable, Optional

from core_brain.price_tape import SIGNIFICANCE_T
from core_brain.reversion_watch import (
    MID_HI,
    MID_LO,
    REFUSED_STORES,
    RefusedStore,
    resolve_store_path,
)

#: The reader's name for the one shared gate, kept so a caller reading this
#: module need not know the writer is where it is defined.
resolve_reversion_db = resolve_store_path

__all__ = [
    "MIN_GROUP_TRADES", "REFUSED_STORES", "RefusedStore",
    "resolve_reversion_db", "reversion_results", "reversion_sweep_grid",
    "reversion_status",
]

#: Below this a group reports no certainty: with a handful of trades any such
#: number says more about the sample size than about the venue.
MIN_GROUP_TRADES = 10

#: The esports momentum test was frozen on 2026-09-08 at this stamp: a 5c jump
#: held 300 seconds. The page labels this cell so a frozen test is never
#: re-tuned by reading the rest of the grid.
PREREGISTERED_JUMP_C = 5.0
PREREGISTERED_HORIZON_S = 300
PREREGISTERED_STAMP = 1_788_849_238

#: A tape whose newest quote is older than this renders as not collecting,
#: the same honesty the tape page owes: a frozen store is a state, not a live
#: measurement.
NOT_COLLECTING_SEC = 24 * 3600.0


def _iso(ts: Optional[int]) -> Optional[str]:
    """A unix stamp as UTC ISO, so a page never guesses a timezone."""
    if ts is None:
        return None
    return _dt.datetime.fromtimestamp(int(ts), _dt.timezone.utc).isoformat(
        timespec="seconds").replace("+00:00", "Z")


def _read_only(path: Path) -> sqlite3.Connection:
    """Open a store read-only, with the path escaped into the URI.

    `f"file:{path}?mode=ro"` is string concatenation into a URI, and a path
    holding `#` re-parses: the fragment swallows `mode=ro`, SQLite drops the
    read-only flag, and it then happily CREATES the truncated file it thinks it
    was asked for. `as_uri()` percent-encodes those characters, so the query
    survives whatever the file is called.
    """
    conn = sqlite3.connect(f"{path.absolute().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def reversion_status(path: str | Path) -> dict[str, Any]:
    """How much the watch has collected. A store not there yet is a state.

    The path is re-resolved rather than trusted: this is a public entry
    point, and a guard only the HTTP route applies is one every other
    caller walks past.
    """
    path = resolve_store_path(path)
    empty: dict[str, Any] = {
        "db": str(path), "exists": path.exists(), "state": "MISSING",
        "markets": 0, "events": 0, "scored": 0, "pending": 0, "unscorable": 0,
        "quotes": 0, "first_ts": None, "last_ts": None, "hours": 0.0,
    }
    if not path.exists():
        return empty
    try:
        with _read_only(path) as conn:
            events, scored, unscorable = conn.execute(
                "SELECT COUNT(*), COUNT(pnl_c), "
                "SUM(pnl_c IS NULL AND token_id IS NULL) FROM events"
            ).fetchone()
            markets, quotes, first_ts, last_ts = conn.execute(
                "SELECT COUNT(DISTINCT slug), COUNT(*), MIN(ts), MAX(ts) "
                "FROM quotes").fetchone()
    except sqlite3.Error:
        # A store this viewer cannot read is NOT an empty one. Reporting "no
        # trades yet" for a file with the wrong schema is the silent failure
        # the whole read-only path exists to avoid: the operator would watch a
        # page say zero for hours while a healthy watch filled a file nobody
        # was reading.
        return {**empty, "state": "UNREADABLE"}
    if not quotes:
        return {**empty, "state": "EMPTY"}
    # Unscorable rows are neither scored nor waiting: they were recorded
    # before the token was, so no run can ever fetch their exit quote. Counting
    # them as pending would promise a result that is not coming.
    unscorable = unscorable or 0
    return {
        "db": str(path), "exists": True, "state": "READY",
        "markets": markets, "events": events, "scored": scored,
        "pending": events - scored - unscorable, "unscorable": unscorable,
        "quotes": quotes, "first_ts": first_ts, "last_ts": last_ts,
        "hours": (last_ts - first_ts) / 3600.0,
    }


def reversion_sweep_grid(
    path: str | Path,
    *,
    games_from: Optional[int] = None,
    now_fn: Optional[Callable[[], int]] = None,
) -> dict[str, Any]:
    """The whole jump-by-hold grid, served to the page with its honesty labels.

    `reversion_sweep.sweep()` answers every cell; this adds what the page needs
    to keep the pre-registration intact and the data age visible:

    * `pre_registered` on the frozen 5c/300s cell, so the rest of the grid can
      be browsed without ever being mistaken for the test that was promised.
    * `games_from` carried back out, so a held-out answer cannot be mistaken
      later for a full-tape one.
    * the tape's own age -- when its newest quote landed, how old it is now,
      and whether collection has stopped -- because a grid over a store that
      stopped recording is a state, not a measurement.

    `sweep()` walks the whole quote tape, so this is not free; the route that
    serves it measures its own cost (see the server's response-time note).
    """
    now = time.time if now_fn is None else now_fn
    # Imported here, not at module top: reversion_sweep sits on the same
    # reversion_watch base this module does, and a top-level import makes the
    # two readers initialise each other.
    from core_brain.reversion_sweep import DEFAULT_HORIZONS, DEFAULT_JUMPS, sweep
    report = sweep(path, games_from=games_from,
                   jumps=DEFAULT_JUMPS, horizons=DEFAULT_HORIZONS)
    if report["state"] != "READY":
        return report

    for cell in report["cells"]:
        cell["pre_registered"] = (
            cell["jump_c"] == PREREGISTERED_JUMP_C
            and cell["horizon_s"] == PREREGISTERED_HORIZON_S)

    last_quote_ts = _last_quote_ts(report["db"])
    now_ts = int(now())
    quote_age_hours = (None if last_quote_ts is None
                       else (now_ts - last_quote_ts) / 3600.0)
    report.update({
        "games_from": games_from,
        "last_quote_ts_h": _iso(last_quote_ts),
        "quote_age_hours": quote_age_hours,
        "stale": (quote_age_hours is not None
                  and quote_age_hours * 3600.0 > NOT_COLLECTING_SEC),
    })
    return report


def _last_quote_ts(path: str | Path) -> Optional[int]:
    """The newest quote's stamp, or None when the store holds none."""
    try:
        with _read_only(Path(path)) as conn:
            return conn.execute("SELECT MAX(ts) FROM quotes").fetchone()[0]
    except sqlite3.Error:
        return None


def _verdict(mean: float, sureness: Optional[float]) -> str:
    """What a group of trades says, against the bar fixed before the run."""
    if sureness is None or sureness < SIGNIFICANCE_T:
        return "NO_SIGNAL"
    return "PAYS" if mean > 0 else "LOSES"


def _summarise(values: list[float]) -> dict[str, Any]:
    """Trades, mean cents per share, and how sure that mean is of its sign."""
    count = len(values)
    mean = statistics.fmean(values) if count else 0.0
    sureness: Optional[float] = None
    if count >= MIN_GROUP_TRADES:
        # Sample sd, not population: these are a sample of the venue's moments,
        # and dividing by n overstates certainty on a fixed bar.
        spread = statistics.stdev(values)
        if spread:
            sureness = abs(mean) / (spread / math.sqrt(count))
    return {"trades": count, "cents": mean, "sureness": sureness,
            "verdict": _verdict(mean, sureness)}


def reversion_results(path: str | Path) -> dict[str, Any]:
    """Realised cents per share, split by league and by price band.

    Re-resolves the path for the same reason `reversion_status` does.
    """
    path = resolve_store_path(path)
    empty: dict[str, Any] = {
        "db": str(path), "state": "MISSING", "groups": [], "overall": None,
        "significance_t": SIGNIFICANCE_T, "mid_band": [MID_LO, MID_HI],
        "in_game": [], "pending": 0, "unscorable": 0,
    }
    if not path.exists():
        return empty
    try:
        with _read_only(path) as conn:
            rows = conn.execute(
                "SELECT league, band, in_game, pnl_c, spread_c FROM events "
                "WHERE pnl_c IS NOT NULL").fetchall()
            pending, unscorable = conn.execute(
                "SELECT SUM(pnl_c IS NULL AND token_id IS NOT NULL), "
                "SUM(pnl_c IS NULL AND token_id IS NULL) FROM events"
            ).fetchone()
            pending, unscorable = pending or 0, unscorable or 0
    except sqlite3.Error:
        return {**empty, "state": "UNREADABLE"}
    if not rows:
        return {**empty, "state": "NOT_SCORED", "pending": pending,
                "unscorable": unscorable}

    by_group: dict[tuple[str, str], list[float]] = {}
    by_phase: dict[str, list[float]] = {}
    spreads: list[float] = []
    for row in rows:
        key = (row["league"] or "?", row["band"] or "?")
        by_group.setdefault(key, []).append(row["pnl_c"])
        phase = {1: "in_game", 0: "pre_game"}.get(row["in_game"], "unstamped")
        by_phase.setdefault(phase, []).append(row["pnl_c"])
        if row["spread_c"] is not None:
            spreads.append(row["spread_c"])

    groups = []
    for (league, band), values in sorted(
            by_group.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        groups.append({"league": league, "band": band, **_summarise(values)})

    return {
        "db": str(path), "state": "READY", "groups": groups,
        "overall": _summarise([row["pnl_c"] for row in rows]),
        "in_game": [{"phase": phase, **_summarise(values)}
                    for phase, values in sorted(by_phase.items())],
        "median_spread_c": statistics.median(spreads) if spreads else None,
        "significance_t": SIGNIFICANCE_T, "mid_band": [MID_LO, MID_HI],
        "pending": pending, "unscorable": unscorable,
    }
