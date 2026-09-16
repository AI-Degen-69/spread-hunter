"""Replay the recorded quote tape over a grid of jump sizes and hold times.

`core_brain.reversion_watch` asks the venue one question -- fade a 3c jump,
hold fifteen minutes -- and spends half a day answering it once. The same watch
also writes every book it reads into its `quotes` table: one row per market per
minute, bid and ask, whether or not that minute produced a trade. That tape
already contains the answer to the whole grid of questions the single run was
never asked, so this module reads it back instead of waiting for new games.

Three numbers come out of every jump, and the difference between them is the
entire point:

* `fade_c` -- sell the up-move, buy it back later. What the forward test trades.
* `follow_c` -- buy the up-move, sell it later. The other side of the same jump,
  and it is charged the spread on BOTH legs. Scoring it as `-fade_c` would hand
  it the spread rather than make it pay, which turns a loser into a winner on
  paper by exactly one round trip.
* `drift_c` -- mid to mid, sign-flipped so positive always means the move
  continued. This is the signal with no execution cost at all.

`drift_c` is what decides whether anything is here. A move that continues by
less than `cost_c` -- the two crossings a taker owes -- is real and untradable
at the same time, and the verdict says `PAID_AWAY` rather than picking a side.

READ-ONLY, AND OUTSIDE THE MONEY PATH. No signer, no wallet, no order. The
store is opened `mode=ro` through the same `resolve_store_path` gate the writer
and the dashboard reader use, so `data/orders.db` is refused here too.
"""
from __future__ import annotations

import argparse
import logging
import math
import sqlite3
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, NamedTuple, Optional

from core_brain.price_tape import SIGNIFICANCE_T
from core_brain.reversion_watch import (
    DEFAULT_DB,
    LOOK,
    MAX_SPREAD_C,
    POLL,
    PRICE_HI,
    PRICE_LO,
    REFUSED_STORES,
    RefusedStore,
    resolve_store_path,
)

#: Below this a cell reports no certainty: with a handful of games any such
#: number says more about the sample size than about the venue.
MIN_GROUP_TRADES = 10

log = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_HORIZONS", "DEFAULT_JUMPS", "MAX_SPREAD_C", "Outcome", "Quote",
    "REFUSED_STORES", "RefusedStore", "load_tape", "replay", "summarise_cell",
    "sweep",
]

#: Hold times scored, in seconds. The short one is there because a signal that
#: only survives five minutes is still a signal; the long one is there because
#: a signal that needs an hour is a different trade with a different risk.
DEFAULT_HORIZONS: tuple[int, ...] = (300, 900, 1800, 3600)
#: Jump sizes scored. 0.03 is the size the backward measurement used, kept so
#: one cell of this grid is directly comparable with the recorded number.
DEFAULT_JUMPS: tuple[float, ...] = (0.02, 0.03, 0.05)

#: A quote this far after the look-back target still counts as the look-back
#: read. The tape is per-minute and a missed poll must not drop the jump.
LOOK_SLACK = 2 * POLL


class Quote(NamedTuple):
    """One minute of one book, as the watch recorded it."""

    ts: int
    bid: float
    ask: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread_c(self) -> float:
        return (self.ask - self.bid) * 100.0


@dataclass(frozen=True)
class Outcome:
    """One jump, scored three ways off the same pair of books."""

    slug: str
    league: str
    ts: int
    move_c: float
    spread_c: float
    entry_mid: float
    fade_c: float
    follow_c: float
    drift_c: float

    @property
    def cost_c(self) -> float:
        """What a taker owes to get in and out: the book crossed twice."""
        return 2.0 * self.spread_c


def load_tape(path: str | Path) -> dict[str, list[Quote]]:
    """Every recorded book, by slug, oldest first.

    Re-resolves the path rather than trusting the caller, for the same reason
    the writer and the dashboard reader do: the refusal has to hold at every
    entry point onto the store, not only at whichever one a route calls first.
    """
    path = resolve_store_path(path)
    if not path.exists():
        return {}
    uri = f"file:{path.as_posix().replace('?', '%3f').replace('#', '%23')}?mode=ro"
    tape: dict[str, list[Quote]] = {}
    # sqlite3's connection context manager commits or rolls back a
    # transaction; it does NOT close the handle on exit. Explicit close so a
    # long-lived sweep process does not leak one per store it reads.
    conn = sqlite3.connect(uri, uri=True)
    try:
        for slug, ts, bid, ask in conn.execute(
                "SELECT slug, ts, bid, ask FROM quotes "
                "WHERE bid IS NOT NULL AND ask IS NOT NULL "
                "ORDER BY slug, ts"):
            tape.setdefault(str(slug), []).append(
                Quote(int(ts), float(bid), float(ask)))
    finally:
        conn.close()
    return tape


def _look_back(rows: list[Quote], index: int, look: int) -> Optional[Quote]:
    """The read `look` seconds before this one, or None when the tape has none.

    Scanning backwards and stopping at the first row inside the window keeps
    the most recent qualifying read, which is the one the live watch would have
    had in its own deque.
    """
    now = rows[index].ts
    for position in range(index - 1, -1, -1):
        age = now - rows[position].ts
        if age > look + LOOK_SLACK:
            return None
        if age >= look - POLL:
            return rows[position]
    return None


def _first_after(rows: list[Quote], index: int, horizon: int) -> Optional[Quote]:
    """The first read a full horizon after this one, or None if the tape ends."""
    due = rows[index].ts + horizon
    for position in range(index + 1, len(rows)):
        later = rows[position]
        if later.ts >= due:
            return later
    return None


def _score(entry: Quote, exit_: Quote, move: float) -> tuple[float, float, float]:
    """Fade, follow and drift for one jump, in cents per share.

    Both traded numbers cross the book twice. Fading an up-move sells at the
    BID and buys back at the ASK; following it buys at the ASK and sells at the
    BID. Neither one is the other's negation, and treating them as if they were
    is the single mistake that makes a losing trade read as a winner.
    """
    if move > 0:
        fade = (entry.bid - exit_.ask) * 100.0
        follow = (exit_.bid - entry.ask) * 100.0
    else:
        fade = (exit_.bid - entry.ask) * 100.0
        follow = (entry.bid - exit_.ask) * 100.0
    drift = (exit_.mid - entry.mid) * 100.0 * (1.0 if move > 0 else -1.0)
    return fade, follow, drift


def replay(tape: dict[str, list[Quote]], *, jump: float, horizon: int,
           look: int = LOOK, max_spread_c: float = MAX_SPREAD_C,
           price_lo: float = PRICE_LO, price_hi: float = PRICE_HI,
           games_from: Optional[int] = None) -> list[Outcome]:
    """Every jump in the tape, scored at one cell of the grid.

    The gates are the forward test's own -- price band, spread ceiling,
    look-back window -- because a number measured over different moments could
    not be compared with the one already recorded.

    Jumps are separated by at least one `horizon`. A market that grinds a cent
    a minute otherwise re-qualifies on every read, and the same move gets
    counted once per minute it lasts.

    `games_from` scores only games whose FIRST recorded quote is at or after
    that stamp. Peeking at a growing tape and re-asking whether it crossed the
    bar is what manufactures a crossing, so a real held-out run needs games
    nobody has looked at yet. The cut is on the game rather than on the jump
    because a match that was already running when the cut was taken has
    already been read, however many of its jumps land after the timestamp.
    """
    outcomes: list[Outcome] = []
    for slug, rows in sorted(tape.items()):
        if games_from is not None and (not rows or rows[0].ts < games_from):
            continue
        league = slug.split("-")[0] if "-" in slug else slug
        last_ts = -math.inf
        for index, entry in enumerate(rows):
            if entry.ts - last_ts < horizon:
                continue
            if not price_lo <= entry.mid <= price_hi:
                continue
            if entry.spread_c > max_spread_c:
                continue
            earlier = _look_back(rows, index, look)
            if earlier is None:
                continue
            move = entry.mid - earlier.mid
            if abs(move) < jump:
                continue
            exit_ = _first_after(rows, index, horizon)
            if exit_ is None:
                continue
            fade, follow, drift = _score(entry, exit_, move)
            outcomes.append(Outcome(
                slug=slug, league=league, ts=entry.ts, move_c=move * 100.0,
                spread_c=entry.spread_c, entry_mid=entry.mid,
                fade_c=fade, follow_c=follow, drift_c=drift))
            last_ts = entry.ts
    return outcomes


def _sureness(values: list[float]) -> Optional[float]:
    """How sure a mean is of its sign, or None when the sample is too small."""
    if len(values) < MIN_GROUP_TRADES:
        return None
    spread = statistics.stdev(values)
    if not spread:
        return None
    return abs(statistics.fmean(values)) / (spread / math.sqrt(len(values)))


def _match_means(outcomes: Iterable[Outcome]) -> list[float]:
    """One drift per match, because jumps inside one match are not independent.

    A single CS2 game produced twelve qualifying 5c jumps on the recorded tape,
    and half a cell's sample came from that one match. Counting them as twelve
    observations divides the standard error by the square root of twelve and
    reports a certainty the tape never earned: the twelve share a scoreboard, a
    map and the same two teams, so they move together by construction.

    Collapsing each match to its own mean first is the cheapest honest fix. It
    costs sample size, which is the point -- the sample really is that small.
    """
    grouped: dict[str, list[float]] = {}
    for done in outcomes:
        grouped.setdefault(done.slug, []).append(done.drift_c)
    return [statistics.fmean(values) for values in grouped.values()]


def _verdict(drift: float, cost: float, sureness: Optional[float],
             matches: int) -> str:
    """What one cell says, in the order the objections have to be cleared.

    Sample size first, then whether the drift is distinguishable from noise,
    and only then whether it is bigger than the two crossings. A cell that
    fails an earlier question is not asked the later one, because a mean read
    off four matches is not evidence that anything was paid away.

    The size that gates this is MATCHES, not moves. `_match_means` says why.
    """
    if matches < MIN_GROUP_TRADES:
        return "THIN"
    if sureness is None or sureness < SIGNIFICANCE_T:
        return "NO_SIGNAL"
    if drift <= 0.0:
        return "REVERTS"
    return "TRADABLE" if drift > cost else "PAID_AWAY"


def summarise_cell(outcomes: list[Outcome], *, jump: float,
                   horizon: int) -> dict[str, Any]:
    """One cell of the grid: what the tape says at this jump and hold."""
    trades = len(outcomes)
    drifts = [done.drift_c for done in outcomes]
    fades = [done.fade_c for done in outcomes]
    follows = [done.follow_c for done in outcomes]
    costs = [done.cost_c for done in outcomes]
    per_match = _match_means(outcomes)
    drift = statistics.fmean(drifts) if drifts else 0.0
    cost = statistics.median(costs) if costs else 0.0
    sureness = _sureness(per_match)
    continued = (100.0 * sum(1 for x in drifts if x > 0) / trades
                 if trades else 0.0)
    return {
        "jump_c": jump * 100.0,
        "horizon_s": horizon,
        "trades": trades,
        "matches": len(per_match),
        "drift_c": drift,
        "drift_median_c": statistics.median(drifts) if drifts else 0.0,
        # The certainty the verdict is decided on: one observation per match.
        "drift_t": sureness,
        # The same number counted per move, kept only so the gap between the
        # two is visible on the page rather than argued about.
        "drift_t_moves": _sureness(drifts),
        "continued_pct": continued,
        "cost_c": cost,
        "fade_c": statistics.fmean(fades) if fades else 0.0,
        "follow_c": statistics.fmean(follows) if follows else 0.0,
        "verdict": _verdict(drift, cost, sureness, len(per_match)),
    }


def _by_league(outcomes: Iterable[Outcome]) -> list[dict[str, Any]]:
    """The best cell's outcomes split by league, largest sample first.

    Pooled esports numbers hide both halves: the recorded measurement put LoL
    at a few cents of reversion and Dota at about zero, so one mean over the
    two is true of neither.
    """
    grouped: dict[str, list[Outcome]] = {}
    for done in outcomes:
        grouped.setdefault(done.league, []).append(done)
    rows = []
    for league, group in grouped.items():
        drifts = [done.drift_c for done in group]
        per_match = _match_means(group)
        rows.append({
            "league": league,
            "trades": len(group),
            "matches": len(per_match),
            "drift_c": statistics.fmean(drifts),
            "drift_median_c": statistics.median(drifts),
            "drift_t": _sureness(per_match),
            "drift_t_moves": _sureness(drifts),
            "cost_c": statistics.median([done.cost_c for done in group]),
            "follow_c": statistics.fmean([done.follow_c for done in group]),
            "fade_c": statistics.fmean([done.fade_c for done in group]),
        })
    return sorted(rows, key=lambda row: (-row["trades"], row["league"]))


def _rank(cell: dict[str, Any]) -> tuple[int, float, int]:
    """How a cell is ranked for the league split it carries.

    Certainty first: the biggest mean on this grid always sits in the smallest
    cell, so ranking by mean would pick the one moment that moved most. A tape
    too short for any cell to clear the sample bar still gets a split, drawn
    from the largest cell, because "no leagues" reads on the page as a finding
    rather than as a short run.
    """
    sureness = cell["drift_t"]
    return (1 if sureness is not None else 0, sureness or 0.0, cell["trades"])


def _outranks(cell: dict[str, Any],
              incumbent: Optional[dict[str, Any]]) -> bool:
    return incumbent is None or _rank(cell) > _rank(incumbent)


def sweep(path: str | Path, *, jumps: Iterable[float] = DEFAULT_JUMPS,
          horizons: Iterable[int] = DEFAULT_HORIZONS,
          games_from: Optional[int] = None) -> dict[str, Any]:
    """Score the whole grid off one tape.

    A store that is not there yet reports `MISSING` rather than raising: the
    page and the CLI are both opened while the watch is still filling it.

    `games_from` runs the grid over games that started at or after that stamp
    and reports the cut it used, so a held-out answer cannot be mistaken later
    for a full-tape one. See `replay` for why the cut is on the game.
    """
    path = resolve_store_path(path)
    empty: dict[str, Any] = {
        "db": str(path), "state": "MISSING", "cells": [], "by_league": [],
        "best": None, "markets": 0, "quotes": 0, "games_from": games_from,
        "significance_t": SIGNIFICANCE_T, "min_trades": MIN_GROUP_TRADES,
    }
    if not path.exists():
        return empty
    try:
        tape = load_tape(path)
    except sqlite3.Error:
        return {**empty, "state": "UNREADABLE"}
    if games_from is not None:
        tape = {slug: rows for slug, rows in tape.items()
                if rows and rows[0].ts >= games_from}
    if not tape:
        return {**empty, "state": "NO_TAPE"}

    cells: list[dict[str, Any]] = []
    best_cell: Optional[dict[str, Any]] = None
    best_outcomes: list[Outcome] = []
    for jump in jumps:
        for horizon in horizons:
            outcomes = replay(tape, jump=jump, horizon=horizon,
                              games_from=games_from)
            cell = summarise_cell(outcomes, jump=jump, horizon=horizon)
            cells.append(cell)
            if _outranks(cell, best_cell):
                best_cell, best_outcomes = cell, outcomes

    return {
        **empty,
        "state": "READY",
        "cells": cells,
        "best": best_cell,
        "by_league": _by_league(best_outcomes),
        "markets": len(tape),
        "quotes": sum(len(rows) for rows in tape.values()),
    }


def _t(sureness: Optional[float]) -> str:
    """A certainty for the table, or a dash when the sample cannot carry one."""
    return "--" if sureness is None else f"{sureness:.2f}"


def _games_from(value: str) -> int:
    """The --games-from cut, as unix seconds.

    Accepts either the raw epoch-seconds integer (what the report prints, so
    a previous held-out cut can be re-used verbatim) or a date a human
    naturally types: `2026-09-05`, optionally with a time
    (`2026-09-05T14:30`, `2026-09-05 14:30`). A bare date means midnight UTC
    of that day. Anything else is an argparse error naming both forms.
    """
    text = value.strip()
    if text.lstrip("-").isdigit():
        return int(text)
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return int(datetime.strptime(text, fmt)
                       .replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(
        f"{value!r} is not a unix stamp (e.g. 1788600000) or a date "
        f"(YYYY-MM-DD, optionally with HH:MM)")


def _main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=str(DEFAULT_DB),
                        help="the reversion store to replay")
    parser.add_argument("--jumps", default=",".join(
        f"{jump:g}" for jump in DEFAULT_JUMPS),
        help="jump sizes in dollars, comma separated")
    parser.add_argument("--horizons", default=",".join(
        str(horizon) for horizon in DEFAULT_HORIZONS),
        help="hold times in seconds, comma separated")
    parser.add_argument("--games-from", type=_games_from, default=None,
                        metavar="STAMP|YYYY-MM-DD",
                        help="score only games whose first quote is at or "
                             "after this unix stamp (or date, YYYY-MM-DD); "
                             "a held-out run")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    report = sweep(
        args.db,
        jumps=[float(part) for part in args.jumps.split(",") if part.strip()],
        horizons=[int(part) for part in args.horizons.split(",")
                  if part.strip()],
        games_from=args.games_from)
    print(f"{report['db']}  state={report['state']}  "
          f"markets={report['markets']}  quotes={report['quotes']}")
    if report["games_from"] is not None:
        print(f"HELD OUT: games whose first quote is at or after "
              f"{report['games_from']} only.")
    if report["state"] != "READY":
        return 0

    print(f"\n{'jump':>6}{'hold':>7}{'n':>6}{'games':>7}{'drift':>9}{'med':>8}"
          f"{'t':>7}{'t/move':>8}{'cont':>7}{'cost':>7}{'fade':>8}"
          f"{'follow':>8}  verdict")
    for cell in report["cells"]:
        print(f"{cell['jump_c']:>5.0f}c{cell['horizon_s']:>7}{cell['trades']:>6}"
              f"{cell['matches']:>7}"
              f"{cell['drift_c']:>+9.2f}{cell['drift_median_c']:>+8.2f}"
              f"{_t(cell['drift_t']):>7}{_t(cell['drift_t_moves']):>8}"
              f"{cell['continued_pct']:>6.0f}%{cell['cost_c']:>7.2f}"
              f"{cell['fade_c']:>+8.2f}{cell['follow_c']:>+8.2f}"
              f"  {cell['verdict']}")

    best = report["best"]
    if best is None:
        print("\nNo cell produced a single move; the tape is too short.")
        return 0
    label = ("Most certain cell" if best["drift_t"] is not None
             else "Largest cell (none reached the sample bar)")
    print(f"\n{label}: jump {best['jump_c']:.0f}c, hold "
          f"{best['horizon_s']}s, {best['trades']} moves -- {best['verdict']}")
    print(f"{'league':<8}{'n':>6}{'games':>7}{'drift':>9}{'med':>8}{'t':>7}"
          f"{'t/move':>8}{'cost':>7}{'follow':>8}")
    for row in report["by_league"]:
        print(f"{row['league']:<8}{row['trades']:>6}{row['matches']:>7}"
              f"{row['drift_c']:>+9.2f}{row['drift_median_c']:>+8.2f}"
              f"{_t(row['drift_t']):>7}{_t(row['drift_t_moves']):>8}"
              f"{row['cost_c']:>7.2f}{row['follow_c']:>+8.2f}")
    print("\nt is measured across GAMES, not across moves: jumps inside one "
          "match share\na scoreboard and move together, so counting them "
          "separately overstates certainty.")
    print(f"A cell needs {MIN_GROUP_TRADES} games and t >= {SIGNIFICANCE_T} "
          f"before it is anything but THIN.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
