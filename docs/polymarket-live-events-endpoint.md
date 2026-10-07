# Live markets: fetch them directly, do not paginate for them

Measured against the live venue 2026-10-06. This exists because the
rank's volume-sorted scan could not reliably reach an in-play market.

## The problem it solves

`GET /markets?order=volume24hr&ascending=false` is the rank's discovery path.
It stops one boundary page past the volume floor, and the floor is a *cost*
control — so anything below it is either never scanned or reached only by luck.

Measured across ten pages of that listing: in-play sub-floor rows are **not** a
cluster near the floor. They run **8–16 per page on every page from 1 to 9+**,
mixed with long-dated and dead markets. No bounded page budget reliably reaches
a named live match. On one run the live `lol-est-kbm-2026-10-06` sat on page 3;
on the next it had moved. Hunting for live markets by paginating a
volume-sorted listing is not a strategy, it is a coincidence.

## The endpoint

```http
GET https://gamma-api.polymarket.com/events?live=true&closed=false&limit=200
```

`live=true` is a real server-side filter. It returned 35 events where the
full listing returned 0 flagged live on the same call. `closed=false` is
required: `live=true` alone also returns long-finished events (2024 CFB rows
were in that result).

Pagination is unnecessary — the live set is small by construction.

## The fields that matter

| field | example | meaning |
| - | - | - |
| `live` | `true` | the event is under way right now |
| `ended` | `false` | the event is over |
| `period` | `"3/5"` | which game/period, of how many |
| `score` | `"8-1\|2-0\|Bo5"` | running score and series state |
| `sport.sport` | `"lol"`, `"cs2"` | sport slug (cs2=37, lol=39) |
| `markets[].sportsMarketType` | `"moneyline"` | main line vs submarket |

## Selecting the main line

**`sportsMarketType == "moneyline"` is the match winner.** Exactly one market
per event carries it.

`sportsMarketType == "child_moneyline"` is the **per-game** submarket
(`groupItemTitle: "Game 3 Winner"`), and it is *not* the match winner.

This matters because the group label is not a reliable discriminator — every
market on an LoL event carries a `groupItemTitle`, including the main line
(`"Match Winner"`). Do not select on an absent label; select on the type.

For `lol-est-kbm-2026-10-06`, the 41 markets break down as:

```
moneyline               Match Winner           x1   <- THE main line
child_moneyline         Game 1..4 Winner       x4
map_handicap            Game Handicap: ...     x4
totals                  O/U 3.5 / 4.5 Games    x2
lol_both_teams_baron    Both Teams Slay Baron  x5
lol_both_teams_dragon   ...                    x5
lol_both_teams_inhibitors / quadra_kill /
penta_kill / odd_even_total_kills             x20
```

## Dropping the decided ones

A side at or above `0.95` means the contest is over and only settlement
remains. The rank's own `[0.20, 0.80]` mid gate is narrower still and owns this
call downstream; this is the cheap pre-filter.

Measured on the live set: `lol-cpd-fue-2026-10-06` sat at `[0.9955, 0.0045]`
with score `15-5|2-0|Bo5` — in-play, but decided. Dropped.

## Every sport, not just esports

`moneyline` is the match winner in **every** sport, so the selector above is
not esports-specific. Measured live 2026-10-06 across nhl, mlb, nba, atp, wta,
fif, bra2, conl, ufc and lol.

### Real sports split submarkets into their own events

This is the trap. Esports nests its submarkets as `markets[]` on **one** event
(`lol-est-kbm` carries all 41, with `map_handicap`, `totals`,
`lol_both_teams_baron`, ...). Real sports expose each submarket group as its
own **top-level event**:

```
mlb-lad-atl-2026-10-06                    moneyline  x1   <- THE main line
mlb-lad-atl-2026-10-06-first-five-winner  baseball_team_first_five_winner
mlb-lad-atl-2026-10-06-inning-1-winner    baseball_team_inning1_winner
...                                       ... inning2..9

fif-arg-ben-2026-10-06                    moneyline  x3
fif-arg-ben-2026-10-06-halftime-result    soccer_halftime_result
fif-arg-ben-2026-10-06-second-half-result soccer_second_half_result
fif-arg-ben-2026-10-06-exact-score        soccer_exact_score
fif-arg-ben-2026-10-06-first-to-score     soccer_first_to_score
fif-arg-ben-2026-10-06-more-markets       spreads / totals / both_teams_to_score / ...
```

An inning-winner event carries the same `live` flag, the same sport and the
same `period`/`score` as the real game, so anything that selects live events by
sport alone admits it. **Not one of these pseudo-events carries a
`moneyline`** (verified across the whole live set), so selecting on the type is
what keeps them out. Selecting on the slug prefix or the sport does not.

### Soccer moneylines are 3-way

Soccer (`fif`, `bra2`, `conl`) carries **three** `moneyline` markets per event
-- Home, Draw and Away -- where hockey, baseball, basketball, tennis, MMA and
esports are two-way. Each outcome is its own binary Yes/No market, so each is
still a mergeable pair, but "one live match" is not "one tradeable market":
live main-line **markets** outnumber live **events**.

## How the ranker consumes it

The live set is **merged into the universe**, not paginated for. In
`scripts/filter_markets.py`:

- `live_event_markets()` fetches the live set and normalises each main line into
the same row shape `gamma_universe` emits, stamped `_live_event`.
- `merge_live_event_markets()` folds those rows into the scanned universe,
keyed on `condition_id`. A market the scan also found is **replaced in place**
by the live row, because the live row is the one carrying the stamp.
- `evaluate()` lets a `_live_event` row **skip both clock gates** (pre-start and
in-play). Both refuse on `gameStartTime`, and a market the venue declares live
is exactly the case where that clock is wrong.
- `tradable()` gates it on `select_min_volume_24h_usd_live` ($10,000) instead of
the permanent floor.

Everything downstream is unchanged: movement, depth, spread, horizon and the
mid gate all still apply, so a live market buys passage through the two clock
gates and the volume floor -- never through the funnel.

`_live_event` is also carried onto the scored row as `live_event`, with
`_sport`, `event_period`, `event_score` and the `volume_bar_usd` it was gated
on, so `market_universe.json` and the dashboard show which picks are live.

### The clock still refuses

A market the clock says is in play, which the venue does **not** declare live,
is still refused -- `in-play: event started 20m ago`. That gate was added
because a started match reaches the depth arm and gets refused for liquidity
when its real defect is the clock (the 2026-10-06 CS2 market's
`NO: top-3 bid depth $44.56`). Nothing about the venue's live set changes that:
the declaration is an extra admission path, not a replacement gate.

### What the page-walking was for, and why it is gone

An earlier attempt widened the volume-sorted scan to keep walking past the
floor while it kept finding started matches. Measured against the live set, that
admitted rows `evaluate` refused on the clock and paid pagination for them. It
is removed: the endpoint answers the question directly, above and below any
floor.

## What this endpoint does NOT do

- **It does not list pre-kickoff markets.** It is the live set only. The
  pre-start / quote-window logic is unchanged and still owns that case.
- **`tag_slug` is silently ignored** on both `/markets` and `/events`: passing
  `esports` or `league-of-legends` returned byte-identical results to passing
  no tag. Filter on `sport.sport` client-side instead.

## The clock disagrees with the flag

`live=true` and a kickoff time in the **future** co-occur. Of 19 live
moneyline events on 2026-10-06, four were tennis matches flagged live with
`period`/`score` showing a match well under way, while `gameStartTime` sat
10-15 hours ahead:

```
wta-buyukak-klimovi-2026-10-05  period S3  score "6-3, 6-7(2-7), 0-3"
                                gameStartTime 2026-10-07 11:00:00+00
```

A clock-based in-play gate reads those as *not started*, because the venue's
per-event start time is not reliable for multi-day tournaments. The probe
reports the clock next to the flag and marks the disagreement rather than
silently resolving it -- the venue's own `live` flag is the stronger signal,
but anyone tuning an in-play window has to see this.

Format note: the venue writes `gameStartTime` space-separated with an
hour-only offset (`"2026-10-06 23:00:00+00"`), not `T`/`Z` ISO. Python 3.11+
`datetime.fromisoformat` parses it; the `"Z" -> "+00:00"` substitution used
elsewhere is a no-op here.

## Status vocabulary -- and a correction

`docs/sports-game-status-values.md` documents a per-sport, case-sensitive
status vocabulary (esports: `not_started`, `running`, `finished`, `postponed`,
`canceled`).

**That string is NOT exposed on this endpoint.** Verified against the live set
2026-10-06: scanning every field of every live event for a value matching the
documented vocabulary returned **zero matches**. What `/events` carries is the
`live`/`ended` boolean pair (plus `closed`, `active`, `archived`).

So the two are related by intent but not by wire format, and code must not
expect `running`/`finished` here. The booleans are what the probe reads.

## Probe

```bash
python scripts/live_events_probe.py              # every sport
python scripts/live_events_probe.py --sport nhl --sport mlb
```

Read-only, no orders, no state written. It prints each live main line with a
KEEP/DROP verdict, the sport, the period and score, the number of ways the
event offers (`ways=3` is a 3-way soccer line), the prices and 24h volume, and
the stated clock. `DROP` reasons name what actually failed: `decided`, `no
two-sided book`, `spread 0.078`, `clock -11.0h (not started)`.
