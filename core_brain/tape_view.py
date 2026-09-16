"""A read-only window onto the recorded price tape, for the dashboard.

`core_brain.price_tape` records a 1-minute tape and answers one question of it:
after price moved, did it keep going or come back? Both halves are terminal
programs, and the recording half runs for weeks, so there is nothing to look at
between the moment collection starts and the moment someone remembers to run the
report.

This module is those two answers, served while collection is still going:

* `tape_status` -- how much tape exists, over how long, how much of it has
  resolved. Three aggregate queries, cheap enough to poll.
* `tape_findings` -- the drift grid, read back exactly as `analyse` stored it.

**Nothing here computes a finding.** Loading the store took 33 seconds at 2.8M
ticks on 2026-09-07, so a page that recomputed would hang; and a page that
computed its own variant would be a second answer to argue with, which is the
thing a measured verdict exists to remove. The verdict is decided here against
`price_tape.SIGNIFICANCE_T`, the bar fixed before any of the grid was run, so
the page cannot render a friendlier threshold than the one that was promised.

READ-ONLY, AND OUTSIDE THE MONEY PATH. Every store is opened `mode=ro`, and
`data/orders.db` is refused by name at every layer including the environment --
an operator exporting `SHL_TAPE_DB=data/orders.db` to "see the real numbers" is
the exact mistake the refusal exists for. The production registry has a
different schema, so pointing this at it would not fail loudly, it would report
"the tape found nothing".
"""
from __future__ import annotations

import datetime as _dt
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Optional

from core_brain.price_tape import DEFAULT_TAPE_PATH, SIGNIFICANCE_T

#: Store names this viewer will never open, matched as a substring of the file
#: name so `data/orders.db` and a copy called `orders.db.bak` are both refused.
REFUSED_STORES = ("orders.db",)

SECONDS_PER_DAY = 86_400.0

#: A grid older than this is stale on age alone, even if no tick was appended
#: after it was computed: the answer on screen is a week old, not a measurement.
STALE_GRID_AGE_SEC = 7 * SECONDS_PER_DAY

#: A store whose newest tick is older than this has stopped collecting, and the
#: page must say so rather than render a healthy, silently frozen coverage tile.
NOT_COLLECTING_SEC = 24 * 3600.0


def _iso(ts: Optional[int]) -> Optional[str]:
    """A unix stamp as UTC ISO, for a page that should not guess a timezone."""
    if ts is None:
        return None
    return _dt.datetime.fromtimestamp(int(ts), _dt.timezone.utc).isoformat(
        timespec="seconds").replace("+00:00", "Z")


class RefusedStore(ValueError):
    """The named store is not a price tape and will not be opened."""


def resolve_tape_db(custom: str | Path | None = None) -> Path:
    """Which tape to read: the argument, `SHL_TAPE_DB`, or the recorder's own."""
    raw = custom or os.environ.get("SHL_TAPE_DB") or DEFAULT_TAPE_PATH
    path = Path(raw)
    lowered = path.name.lower()
    for refused in REFUSED_STORES:
        if refused in lowered:
            raise RefusedStore(
                f"{path} is a live order registry, not a price tape; "
                f"this viewer reads recorded ticks only")
    return path


def _read_only(path: Path) -> sqlite3.Connection:
    """Open a store read-only, with the path escaped into the URI rather than
    pasted into it.

    `f"file:{path}?mode=ro"` is string concatenation into a URI, and a path
    holding `#` re-parses: the fragment swallows `mode=ro`, SQLite drops the
    read-only flag, and it then happily CREATES the truncated file it thinks it
    was asked for. `as_uri()` percent-encodes those characters, so the query
    survives whatever the file is called.
    """
    conn = sqlite3.connect(f"{path.absolute().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def tape_status(path: str | Path) -> dict[str, Any]:
    """How much tape exists. A store that is not there yet is a state, not an error."""
    path = Path(path)
    empty: dict[str, Any] = {
        "db": str(path), "exists": path.exists(), "state": "MISSING",
        "markets": 0, "resolved": 0, "ticks": 0,
        "first_ts": None, "last_ts": None, "span_days": 0.0,
    }
    if not path.exists():
        return empty
    try:
        with _read_only(path) as conn:
            markets, resolved = conn.execute(
                "SELECT COUNT(*), COUNT(up_wins) FROM markets").fetchone()
            ticks, first_ts, last_ts = conn.execute(
                "SELECT COUNT(*), MIN(ts), MAX(ts) FROM ticks").fetchone()
    except sqlite3.Error:
        return {**empty, "state": "EMPTY"}
    if not ticks:
        return {**empty, "state": "EMPTY", "markets": markets, "resolved": resolved}
    return {
        "db": str(path), "exists": True, "state": "READY",
        "markets": markets, "resolved": resolved, "ticks": ticks,
        "first_ts": first_ts, "last_ts": last_ts,
        "last_ts_h": _iso(last_ts),
        "span_days": (last_ts - first_ts) / SECONDS_PER_DAY,
    }


def _verdict(t: float) -> str:
    if t >= SIGNIFICANCE_T:
        return "CONTINUES"
    if t <= -SIGNIFICANCE_T:
        return "COMES_BACK"
    return "NO_SIGNAL"


def tape_findings(
    path: str | Path,
    *,
    now_fn: Optional[Callable[[], int]] = None,
) -> dict[str, Any]:
    """The stored drift grid, read back with each cell's verdict applied.

    The grid is a snapshot, not a live answer: `analyse` wrote it once and
    nothing on the page recomputes it. So this reports the grid's own age --
    when it was computed, how much tape existed at that moment, and how much
    has been recorded since -- and flags it STALE when ticks were appended
    after the compute stamp, or when the snapshot itself is older than
    `STALE_GRID_AGE_SEC`. A page must not let an old verdict pass as current.
    """
    now = time.time if now_fn is None else now_fn
    path = Path(path)
    empty: dict[str, Any] = {
        "db": str(path), "state": "MISSING", "cells": [],
        "computed_at": None, "computed_at_h": None, "grid_age_hours": None,
        "ticks_since_computed": None, "computed_over_tape_ticks": None,
        "last_tick_ts_h": None, "stale": None, "stale_reason": None,
        "significance_t": SIGNIFICANCE_T,
        "continues": 0, "comes_back": 0, "no_signal": 0, "samples": 0,
    }
    if not path.exists():
        return empty
    try:
        with _read_only(path) as conn:
            rows = conn.execute(
                "SELECT label, trigger, lookback_m, horizon_m, n, mean, t_stat, "
                "computed_at FROM findings "
                "ORDER BY trigger, lookback_m, horizon_m").fetchall()
            last_tick = conn.execute("SELECT MAX(ts) FROM ticks").fetchone()[0]
            tick_count = conn.execute("SELECT COUNT(*) FROM ticks").fetchone()[0]
    except sqlite3.Error:
        return {**empty, "state": "NOT_ANALYSED"}
    if not rows:
        return {**empty, "state": "NOT_ANALYSED"}

    computed_at = rows[0]["computed_at"]
    now_ts = int(now())
    has_new_ticks = last_tick is not None and last_tick > computed_at
    ticks_since = 0
    if has_new_ticks:
        ticks_since = conn.execute(
            "SELECT COUNT(*) FROM ticks WHERE ts > ?", (computed_at,)
        ).fetchone()[0]
    if has_new_ticks:
        stale_reason = "NEW_TICKS"
    elif now_ts - computed_at > STALE_GRID_AGE_SEC:
        stale_reason = "GRID_AGE"
    else:
        stale_reason = None

    cells = []
    tally = {"CONTINUES": 0, "COMES_BACK": 0, "NO_SIGNAL": 0}
    for row in rows:
        verdict = _verdict(row["t_stat"])
        tally[verdict] += 1
        cells.append({
            "label": row["label"],
            "trigger_c": row["trigger"] * 100.0,
            "lookback_min": row["lookback_m"],
            "horizon_min": row["horizon_m"],
            "n": row["n"],
            "mean": row["mean"],
            "mean_c": row["mean"] * 100.0,
            "t": row["t_stat"],
            "verdict": verdict,
        })
    return {
        "db": str(path), "state": "READY", "cells": cells,
        "computed_at": computed_at,
        "computed_at_h": _iso(computed_at),
        "grid_age_hours": (now_ts - computed_at) / 3600.0,
        "ticks_since_computed": ticks_since,
        "computed_over_tape_ticks": tick_count - ticks_since,
        "last_tick_ts_h": _iso(last_tick),
        "stale": stale_reason is not None,
        "stale_reason": stale_reason,
        "significance_t": SIGNIFICANCE_T,
        "continues": tally["CONTINUES"],
        "comes_back": tally["COMES_BACK"],
        "no_signal": tally["NO_SIGNAL"],
        "samples": sum(c["n"] for c in cells),
    }
