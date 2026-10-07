"""Read-only probe: live sports markets, fetched directly and filtered.

This replaces the volume-sorted pagination hunt, which could not reliably reach
a live match: measured 2026-10-06, in-play sub-floor rows are spread 8-16 per
page across every page of the listing, so no bounded scan reliably finds a
named game.

The venue answers the question directly. `GET /events?live=true&closed=false`
returns every live event with the fields that matter:

    live        bool      the event is under way RIGHT NOW
    ended       bool      the event is over
    period      "3/5"     which game/period, of how many
    score       "8-1|2-0|Bo5"   running score and series state
    sport.sport  "lol"    the sport slug (cs2=37, lol=39)
    markets[]   each market carries `sportsMarketType`

`sportsMarketType` is the main-line selector that matters. `moneyline` is the
match winner and is present on every genuine match event; `child_moneyline` is
the per-game "Game N Winner" submarket on an LoL event, and `totals` /
`spreads` / `map_handicap` and the sport-specific types are all submarkets.
Exactly one `moneyline` market exists per event for the two-way sports, so the
main line is found without relying on title parsing -- every market on the
event carries a `groupItemTitle`, so an absent-label test would find nothing.

Two things this probe learned the hard way, both encoded below:

1. **Real sports split submarkets into their own top-level events**, unlike
   esports which nests them as markets on one event. A live MLB game appears as
   `mlb-lad-atl-2026-10-06` plus `...-inning-1-winner` through `...-inning-9`,
   `...-first-five-winner`; a live soccer match as `...-halftime-result`,
   `...-second-half-result`, `...-exact-score`, `...-first-to-score`,
   `...-more-markets`. Measured 2026-10-06: NOT ONE of those pseudo-events
   carries a `moneyline` -- they carry `baseball_team_inning5_winner`,
   `soccer_second_half_result`, `soccer_exact_score`, `spreads`, `totals` and
   so on. So the type selector does not leak: selecting `moneyline` finds the
   match winner and nothing else, in every sport.

2. **Soccer moneylines are 3-way** (Home / Draw / Away) where hockey, baseball,
   basketball and tennis are 2-way. Each outcome is still its own binary
   Yes/No market, so each is still a mergeable pair, but a reader must not
   assume "one moneyline per event" means "one tradeable market per event".
   The probe prints the count so a 3-way soccer line is visible as such.

Note on status: this endpoint exposes `live`/`ended` BOOLEANS, not the raw
status string. Checked against the live set 2026-10-06 -- no event field
carries a value from the documented per-sport vocabulary (`running`,
`finished`, `not_started`) in `docs/sports-game-status-values.md`. The booleans
are what this probe reads.

Note on the clock: `live=true` and a kickoff time in the FUTURE co-occur. Of
the 19 live moneyline events on 2026-10-06, four tennis matches were flagged
live with `gameStartTime` 10-15 hours ahead, with scores showing a match
already in progress. The venue's per-event start time is not reliable for
multi-day tournaments, so the probe reports the clock alongside the flag
rather than picking a winner between them.

Run:  python scripts/live_events_probe.py
      python scripts/live_events_probe.py --sport nhl --sport mlb
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

import requests

EVENTS = "https://gamma-api.polymarket.com/events"

# The main line. `child_moneyline` is the per-game submarket on esports and is
# NOT the match winner, so it is excluded here and by the selector downstream.
MAIN_LINE = "moneyline"

# A side at or above this is decided: the contest is over and only settlement
# remains. The funnel's own [0.20, 0.80] mid gate is narrower still.
FINISHED_PRICE = 0.95

# Walls of submarkets carry no book or a nonsense one; a tradeable pair needs
# both sides quoted inside this spread.
MAX_SPREAD = 0.06


def _prices(m: dict) -> list[float]:
    try:
        return [float(x) for x in json.loads(m.get("outcomePrices") or "[]")]
    except (TypeError, ValueError):
        return []


def _spread(m: dict) -> float | None:
    bid, ask = m.get("bestBid"), m.get("bestAsk")
    try:
        if bid is None or ask is None:
            return None
        return float(ask) - float(bid)
    except (TypeError, ValueError):
        return None


def live_events(session: requests.Session) -> list[dict]:
    """Every live, not-closed event the venue reports."""
    r = session.get(EVENTS, params={"live": "true", "closed": "false",
                                    "limit": 200}, timeout=30)
    r.raise_for_status()
    rows = r.json()
    return rows if isinstance(rows, list) else []


def elapsed_hours(game_start_time: object, now: datetime) -> float | None:
    """Hours since the stated kickoff, or None when there is no usable clock.

    The venue writes this field space-separated with an hour-only offset
    (`"2026-10-06 23:00:00+00"`), which `fromisoformat` parses on Python 3.11+.
    A negative result means the clock disagrees with the `live` flag.
    """
    if not game_start_time:
        return None
    try:
        start = datetime.fromisoformat(str(game_start_time).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    return (now - start).total_seconds() / 3600.0


def main_lines(events: list[dict], sports: set[str] | None = None) -> list[dict]:
    """Main-line markets on live events, with their event context.

    `sports` filters by the venue's `sport.sport` slug; None means every sport.
    Filtering is client-side because `tag_slug` is silently ignored on the
    venue side (measured: `esports` and `league-of-legends` returned
    byte-identical results to passing no tag at all).

    The `live`/`ended` booleans are re-checked here rather than trusted to the
    fetch's query string. The filter is server-side, but a caller that widens
    the fetch -- or a venue that stops honouring `live=true` -- would otherwise
    let a finished match through, and a finished match is exactly the shape
    that reads as a tradeable book while being pure settlement. Both flags are
    required to agree: `live` true and `ended` false.
    """
    out = []
    for ev in events:
        sport = (ev.get("sport") or {}).get("sport")
        if sports is not None and sport not in sports:
            continue
        if not ev.get("live") or ev.get("ended"):
            continue
        for m in ev.get("markets") or []:
            if (m.get("sportsMarketType") or "") != MAIN_LINE:
                continue
            out.append({
                "event_slug": ev.get("slug"),
                "sport": sport,
                "period": ev.get("period"),
                "score": ev.get("score"),
                "live": ev.get("live"),
                "ended": ev.get("ended"),
                "game_start_time": m.get("gameStartTime"),
                "market": m,
            })
    return out


def ways(rows: list[dict]) -> dict[str, int]:
    """How many `moneyline` markets each event carries: 2-way vs 3-way.

    Soccer is 3-way (Home/Draw/Away); the two-way sports are 1. Each outcome
    is its own binary market, so a 3-way event offers three mergeable pairs
    rather than one, and an event listing of "how many live main lines" reads
    low if it counts events instead of markets.
    """
    counts: dict[str, int] = {}
    for row in rows:
        slug = str(row["event_slug"])
        counts[slug] = counts.get(slug, 0) + 1
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sport", action="append", default=None,
                        metavar="SLUG",
                        help="restrict to a venue sport slug (lol, cs2, nhl, "
                             "mlb, nba, atp, wta, fif, ...). Repeatable; "
                             "omit for every sport.")
    args = parser.parse_args(argv)
    sports = set(args.sport) if args.sport else None

    session = requests.Session()
    try:
        events = live_events(session)
    except Exception as e:                                   # noqa: BLE001
        print(f"venue unreachable: {type(e).__name__}: {e}")
        return 1

    print(f"live & not-closed events: {len(events)}")
    mains = main_lines(events, sports)
    label = ", ".join(sorted(sports)) if sports else "all sports"
    print(f"live main lines ({label}): {len(mains)} "
          f"across {len(ways(mains))} events\n")

    if not mains:
        print(f"no live main line right now ({label})")
        return 0

    now = datetime.now(timezone.utc)
    counts = ways(mains)
    for row in mains:
        m = row["market"]
        prices = _prices(m)
        spread = _spread(m)
        vol = float(m.get("volume24hr") or 0)
        elapsed = elapsed_hours(row["game_start_time"], now)

        reasons = []
        if not prices:
            reasons.append("no prices")
        elif max(prices) >= FINISHED_PRICE:
            reasons.append("decided")
        if spread is None:
            reasons.append("no two-sided book")
        elif spread > MAX_SPREAD:
            reasons.append(f"spread {spread:.3f}")
        if not row["live"] or row["ended"]:
            reasons.append("not live")
        if elapsed is not None and elapsed <= 0:
            # The live flag and the clock disagree. Reported, not resolved:
            # the venue's own liveness flag is the stronger signal, but a
            # reader tuning the in-play window has to see this to know why a
            # live match looked "not started" to a clock-based gate.
            reasons.append(f"clock {elapsed:.1f}h (not started)")

        ways_n = counts[str(row["event_slug"])]
        verdict = "DROP " + ", ".join(reasons) if reasons else "KEEP"
        print(f"{verdict:40} {str(row['event_slug'])[:34]:36} "
              f"{row['sport']!s:5} {row['period']!s:7} "
              f"ways={ways_n} px={prices} vol24=${vol:,.0f}")
        shown = f"{spread:.4f}" if spread is not None else "None"
        print(f"    {m.get('groupItemTitle')!s:18} "
              f"bid/ask={m.get('bestBid')}/{m.get('bestAsk')} "
              f"spread={shown} score={row['score']} "
              f"clock={row['game_start_time']!s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
