"""One dashboard for the whole fleet: aggregate on top, per-market below.

Replaces four separate pages on four ports. The aggregate strip answers "is
this working overall", the table answers "which market is carrying it" -- and
with 20 markets the second question is the one that matters, because income is
concentrated: measured, a single market can be a third of the total.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from strategy import stats, store
from strategy.config import load as load_config

ROOT = Path(__file__).resolve().parent.parent
RUN = ROOT / "run"
CFG = load_config()
# A 20-market sweep normally takes about 50-70 seconds at the public-book
# polling interval. Give one slow sweep room before calling the fleet dead;
# otherwise a healthy fleet flashes STALE between every state-file write.
STALE_AFTER_SEC = 120.0

app = FastAPI(title="Hunter fleet")


def _pulse() -> dict:
    """The fleet's loop heartbeat, or {} if the fleet has not published one.

    Written by a background thread in `strategy.fleet` every PULSE_WRITE_SEC.
    `loop_ts` is the trading loop's OWN last-iteration stamp, deliberately not
    the writer thread's clock -- a wedged loop stops advancing it while the
    thread keeps writing, so a hung fleet still reads as STALE here.
    """
    f = RUN / "fleet_pulse.json"
    if not f.exists():
        return {}
    try:
        p = json.loads(f.read_text(encoding="utf-8"))
        return p if isinstance(p, dict) else {}
    except Exception:
        # A fleet too old to publish a pulse, or a torn read (the writer
        # renames into place, so this should not happen). Fall back to the
        # state file rather than calling a live fleet dead.
        return {}


def _heartbeat(now: float, live_ts: float, state_mtime: float,
               pulse: dict) -> tuple[float, float | None, bool, str]:
    """Decide how alive the fleet is: (heartbeat_ts, age, stale, source).

    Pure so the decision is testable without standing up a fleet, and because
    the previous version of it -- one expression inline in the endpoint -- was
    wrong in a way nothing could catch.
    """
    loop_ts = float(pulse.get("loop_ts") or 0.0)
    ts = max(loop_ts, live_ts, state_mtime)
    age = (now - ts) if ts else None
    stale = age is None or age > STALE_AFTER_SEC
    src = ("loop" if ts and ts == loop_ts
           else "sweep" if ts and ts == live_ts
           else "mtime" if ts else "none")
    return ts, age, stale, src


def _sweep_duration(pulse: dict, now: float,
                    live_ts: float) -> float | None:
    """How long a full sweep is taking, in seconds.

    Prefers what the fleet MEASURED. `sweep_sec` is the last completed sweep;
    `sweep_elapsed` is the one in progress, and the larger of the two is
    returned so a sweep that has wedged reports its true cost immediately
    rather than the healthy duration of the sweep before it.

    Falls back to `now - live_ts` -- the freshest per-market payload -- only
    for a fleet too old to publish either field. That fallback is the original
    calculation and it OVERSTATES the sweep whenever a market fails to load,
    which is why it is no longer the primary source.
    """
    done = pulse.get("sweep_sec")
    running = pulse.get("sweep_elapsed")
    measured = [float(v) for v in (done, running) if v is not None]
    if measured:
        return max(measured)
    return (now - live_ts) if live_ts else None


@app.get("/api/fleet")
def fleet():
    f = RUN / "fleet_state.json"
    if not f.exists():
        return {"markets": [], "error": "fleet not running (run/fleet_state.json missing)"}
    specs = json.loads(f.read_text(encoding="utf-8"))
    # One call for the whole DB-derived payload (issue #13): the state
    # reader owns every read query; this page owns HTTP + HTML.
    snap = stats.snapshot()
    hist = snap["db_stats"]
    event_by_market, refusal_counts = snap["market_event_stats"]
    settled_positions = snap["settled_positions"]
    mk = snap["markout_stats"]
    now = time.time()
    live_ts = max((s.get("_live", {}).get("ts", 0) or 0
                   for s in specs), default=0.0)
    db_ts = snap["db_heartbeat"] or 0.0
    # THE LOOP PULSE IS THE HEARTBEAT; the state file is the fallback.
    #
    # `fleet_state.json` is written only after a COMPLETE sweep, so it measures
    # sweep duration, not liveness. A 20-market sweep is 50-70s healthy and one
    # slow venue pushes it past 120s -- which is how a fleet that was trading
    # correctly came to display "heartbeat is stale (3m26s)". `loop_ts` is
    # stamped once per market visit (~1s) and published every 10s by a thread
    # that does not compete with the trading loop for time.
    #
    # DB writes stay out of the heartbeat entirely: historical DB activity must
    # not make a dead fleet look LIVE.
    p = _pulse()
    heartbeat_ts, state_age, fleet_stale, heartbeat_src = _heartbeat(
        now, live_ts, f.stat().st_mtime if f.exists() else 0.0, p)

    rows = []
    for s in specs:
        live = s.get("_live") or {}
        h = hist.get(s["cid"], {})
        rows.append({
            "title": s["title"], "slug": s.get("slug", ""),
            "url": f"https://polymarket.com/market/{s['slug']}" if s.get("slug") else "",
            # THE POT THAT ACTUALLY PAYS THIS MARKET. `spec["daily"]` is the
            # reward pot alone and reads 0 for every spread market, which made
            # the whole table report $0.00/day on markets that were filling.
            # The fleet publishes the effective pot in live state; the spec
            # figure is the fallback for a market not yet visited.
            "daily": live.get("pot", s["daily"]),
            "reward_daily": s["daily"],
            "source": live.get("source", s.get("source",
                                               "rewards" if s["daily"] > 0
                                               else "spread")),
            "min_size": s["min_size"],
            # Specs store the venue window in cents (4.5); live state stores
            # the normalized decimal (0.045). Keep one contract for the page.
            "max_spread": (live.get("max_spread")
                            if live.get("max_spread") is not None
                            else float(s["max_spread"]) / 100.0),
            "share": live.get("share", 0.0),
            "income": live.get("income", 0.0),
            # `capital` is resting offers only. `committed` includes offers
            # plus inventory cost, so the table uses the same denominator as
            # the wallet gauge instead of silently understating exposure.
            "capital": live.get("capital", 0.0),
            "committed": (live.get("capital", 0.0)
                          + (live.get("naked_cost", 0.0) or 0.0)
                          + (live.get("pair_paid", 0.0) or 0.0)),
             "quotes": live.get("quotes", []),
             "up_sh": live.get("up_sh", 0.0),
             "dn_sh": live.get("dn_sh", 0.0),
             "up_avg": live.get("up_avg", 0.0),
             "dn_avg": live.get("dn_avg", 0.0),
             "fills": h.get("fills", 0),
            "uptime": h.get("uptime", 0.0),
            "samples": h.get("samples", 0),
            # Estimate, not a ledger entry: no dollar amount is persisted per
            # sample (reward_samples only stores our_share), so this assumes
            # today's funded daily rate held constant over the whole window --
            # same assumption the live "income" projection already makes.
            "collected_rent": h.get("avg_share", 0.0)
                              * (live.get("pot", s["daily"]) or 0.0)
                              * (h.get("hours", 0.0) / 24.0),
            "age": (now - live["ts"]) if live.get("ts") else None,
            "err": live.get("err") or "",
            "why": live.get("why") or "",
            # price-ladder fields, all on the UP axis
            "up_bid": live.get("up_bid"), "up_ask": live.get("up_ask"),
            "mid_up": live.get("mid_up"), "our_up": live.get("our_up"),
            "our_dn_as_up": live.get("our_dn_as_up"),
            "dn_bid_as_up": live.get("dn_bid_as_up"),
            "pair_cost": live.get("pair_cost"),
            # position: paired shares are safe (always pay $1), naked shares
            # are the only thing that can lose (pay $1 or $0)
            "paired": live.get("paired", 0.0),
            "naked_side": live.get("naked_side", ""),
            "naked_sh": live.get("naked_sh", 0.0),
            "naked_cost": live.get("naked_cost", 0.0),
            "pair_paid": live.get("pair_paid", 0.0),
            # What the naked leg fetches if sold at the current best bid right
            # now, instead of waiting to be paid $1 or $0 at resolution. UP
            # sells against up_bid directly; DOWN sells against dn_bid, which
            # is carried on the UP axis as dn_bid_as_up = 1 - dn_bid, so it is
            # un-folded back here. None (no live bid) means "can't exit right
            # now" -- valued at 0, same as the worst-case resolution number,
            # not blended into a guess.
            "naked_exit_value": (
                live.get("naked_sh", 0.0) * live["up_bid"]
                if live.get("naked_side") == "UP" and live.get("up_bid") is not None
                else live.get("naked_sh", 0.0) * (1.0 - live["dn_bid_as_up"])
                if live.get("naked_side") == "DOWN" and live.get("dn_bid_as_up") is not None
                else 0.0
            ),
            "unrealized_pnl": (
                ((live.get("paired", 0.0) or 0.0) * 1.0 - (live.get("pair_paid", 0.0) or 0.0)) +
                ((
                    live.get("naked_sh", 0.0) * live["up_bid"]
                    if live.get("naked_side") == "UP" and live.get("up_bid") is not None
                    else live.get("naked_sh", 0.0) * (1.0 - live["dn_bid_as_up"])
                    if live.get("naked_side") == "DOWN" and live.get("dn_bid_as_up") is not None
                    else 0.0
                ) - (live.get("naked_cost", 0.0) or 0.0))
            ),
            # cost of being filled -- the half of EV the rent line ignores
            "gate": live.get("gate", "NORMAL"),
            "markout": mk["by_market"].get(s["cid"], {}).get("mean_per_share"),
            "markout_n": mk["by_market"].get(s["cid"], {}).get("n", 0),
            # Profit-take closes: shares already sold early, and why -- an
            # operator watching positions shrink with no explanation is the
            # exact gap this closes.
            "closes": h.get("closes", 0),
            "closed_pnl": h.get("closed_pnl", 0.0),
            "closed_forgone": h.get("closed_forgone", 0.0),
            "close_why": live.get("close_why") or "",
            # U2. Merge is the exit that actually fires -- the sell path's
            # ceiling is -0.007/share against a +0.020 threshold, which is why
            # `closes` sat at zero for 18.7 hours. Reported separately so a
            # reader can see which mechanism released the capital.
            "merge_why": live.get("merge_why") or "",
            "merged_shares": live.get("merged_shares", 0.0),
            "recycled_usd": live.get("recycled_usd", 0.0),
            "pairing_rate": live.get("pairing_rate"),
            "events": event_by_market.get(s["cid"], []),
        })
    rows.sort(key=lambda r: -r["income"])

    scoring = [r for r in rows if r["income"] > 0]
    cap = sum(r["capital"] for r in rows)
    inc = sum(r["income"] for r in rows)
    total_collected_rent = sum(r["collected_rent"] for r in rows)
    rz = snap["realized"]

    # The projection integrated over the time it was actually held, rather
    # than whatever it happens to read this second.
    try:
        accrual = store.income_accrual()
    except Exception:
        accrual = {"accrued": 0.0, "twa_day": None, "hours": 0.0, "n": 0}

    rebate = snap["maker_rebate"]
    merged_total = sum(r["merged_shares"] for r in rows)
    try:
        vr = store.verified_ratio()
    except Exception:
        # A dashboard that cannot read one metric must still render the rest.
        vr = {"verified_fills": 0, "verified_shares": 0.0,
              "unverified_fills": 0, "unverified_shares": 0.0,
              "unverified_sweep_shares": 0.0, "ratio": None}

    locked = sum((r["paired"] or 0) * 1.0 - (r["pair_paid"] or 0) for r in rows)
    at_risk = sum(r["naked_cost"] or 0 for r in rows)
    # Naked value at the current bid, not the $1/$0 resolution outcome --
    # what selling out actually raises if it happened this second.
    naked_exit_total = sum(r["naked_exit_value"] for r in rows)
    # Unfunded now means "no pot from EITHER source". A spread market has no
    # reward rate by definition, and counting those as unfunded reported the
    # entire working universe as dead capital.
    unfunded = [r for r in rows if not (r["daily"] or 0) > 0]
    spread_rows = [r for r in rows if r["source"] == "spread"]
    committed_total = cap + at_risk + sum(r["pair_paid"] or 0 for r in rows)
    available_cash = max(0.0, CFG.bankroll_usd - committed_total)
    committed_overage = max(0.0, committed_total - CFG.max_committed_usd)
    active_quoting = sum(1 for r in rows if r["quotes"] and not r["err"])
    book_health_ratio = (active_quoting / len(rows)) if rows else None

    return {
        "now": now,
        "run_started": snap["run_started"],
        "markets": rows,
        "settled_positions": settled_positions,
        "go_live": snap["go_live_readiness"],
        "share_history": snap["share_history"],
        "totals": {
            "markets": len(rows),
            "scoring": len(scoring),
            "income_day": inc,
            "income_hour": inc / 24.0,
            "collected_rent_total": total_collected_rent,
            # RENT SPLIT BY WHETHER IT IS OWED TO US OR MERELY MODELLED.
            #
            # Reward rent is money the venue distributes for resting size. It
            # is earned but not yet in the wallet, so it is a genuine P&L term
            # the headline is missing.
            #
            # Spread "rent" is not a distribution at all -- it is a projection
            # of income that arrives BY BEING FILLED, and those same dollars
            # are already counted in booked P&L and pair P&L the moment a fill
            # happens. Adding it would book the same income twice, which is
            # exactly the double-count that makes a paper strategy look
            # profitable when it is not.
            # MODELLED INCOME ACCRUED, integrated over time. Replaces the old
            # `collected_rent_total`, which multiplied today's pot by the whole
            # run's hours and so rewrote history every time a pot moved.
            "income_accrued": accrual["accrued"],
            "income_twa_day": accrual["twa_day"],
            "income_hours": accrual["hours"],
            "income_samples": accrual["n"],
            "rent_reward": sum(r["collected_rent"] for r in rows
                               if r["source"] == "rewards"),
            # THE OTHER PROGRAM. `rent_reward` above is liquidity rewards, paid
            # for resting size, and it is $0.00 whenever the fleet holds only
            # clobRewards: 0 markets. This is Maker Rebates, paid as a share of
            # the taker fee on volume we MADE -- disjoint from the pot, so the
            # two add without double-counting, and additive to booked P&L
            # because a rebate is money the venue sends on top of the fill.
            "maker_rebate": rebate["earned"],
            "maker_rebate_shares": rebate["shares"],
            "maker_rebate_fills": rebate["fills"],
            "maker_rebate_cps": rebate["per_share_cents"],
            "maker_rebate_err": rebate["err"],
            "rent_modelled_spread": sum(r["collected_rent"] for r in rows
                                        if r["source"] == "spread"),
            "unfunded": len(unfunded),
            "realized": rz["realized"],
            "settled": rz["settled"],
            "wins": rz["wins"],
            "losses": rz["losses"],
            "closes": rz["closes"],
            "closed_pnl": rz["closed_pnl"],
            "closed_forgone": rz["closed_forgone"],
            "locked_pair": locked,
            # The pieces `locked_pair` is made of, published separately so the
            # page can show the arithmetic instead of one net figure labelled
            # as though it were a holding. A reader seeing -$13.59 under
            # "matched shares valued at $1" cannot tell that the shares are
            # worth $571 and cost $584.59 -- which is the actual news.
            "pair_value": sum((r["paired"] or 0) * 1.0 for r in rows),
            "pair_paid": sum(r["pair_paid"] or 0 for r in rows),
            "naked_exit": naked_exit_total,
            "at_risk": at_risk,
            "net_worst": rz["realized"] + locked - at_risk,
            # Liquidate & cancel everything: booked P&L + pairs merged ($1 each)
            # + naked shares sold at current bid - cost of naked shares.
            "liquidate_now_pnl": rz["realized"] + locked
                                 + (naked_exit_total - at_risk),
            # Cash currently locked in active limit orders (released 100% on cancel)
            "locked_bids_cash": cap,
            "markout_total": mk["total"],
            "markout_spread": mk["spread"],
            "markout_n": mk["n"],
            # Fills whose 1h (h1) mark has landed, and the fleet-wide sample the
            # markout gate needs before it will act on them. Published as a pair
            # so the page never hardcodes the threshold.
            "matured_n": mk["matured_n"],
            "gate_min_sample": CFG.markout_fleet_min_sample,
            # THE MEASURED ANSWER, as opposed to the modelled one. Spread
            # capture is a projection until a fill proves it: `markout_spread`
            # is the edge actually captured on filled shares (mid minus what
            # we paid) and `markout_total` is the market then moving against
            # us. Their sum is what being filled was worth in dollars, and it
            # is the number that decides whether this strategy makes money.
            "fill_edge": mk["spread"] + mk["total"],
            "income_spread": sum(r["income"] for r in spread_rows),
            "income_reward": inc - sum(r["income"] for r in spread_rows),
            "markets_spread": len(spread_rows),
            "fleet_naked_budget": CFG.max_fleet_naked_usd,
            "wallet": CFG.bankroll_usd,
            "committed_total": committed_total,
            "available_cash": available_cash,
            "committed_overage": committed_overage,
            "max_committed": CFG.max_committed_usd,
            "state_age": state_age,
            "db_age": (now - db_ts) if db_ts else None,
            "heartbeat_ts": heartbeat_ts or None,
            "fleet_stale": fleet_stale,
            # Which signal answered, and how far behind the sweep is running.
            # When the fleet DOES go stale these separate "the loop stopped"
            # from "the loop is fine but a sweep is taking minutes" -- the two
            # have different causes and this page was previously unable to tell
            # them apart.
            "heartbeat_src": heartbeat_src,
            # SWEEP DURATION IS MEASURED BY THE FLEET, NOT DERIVED HERE.
            #
            # This used to be `now - max(_live.ts)`, which is the age of the
            # freshest per-market payload and not a sweep duration at all.
            # `visit` returns early -- without writing `_live` -- for a market
            # it cannot load, so once markets started expiring the figure grew
            # without bound: a fleet completing a sweep every 21 seconds was
            # reported as "a full sweep is taking 30m41s", and the operator was
            # sent hunting a bottleneck in a loop that did not have one.
            "sweep_age": _sweep_duration(p, now, live_ts),
            # The old quantity, under a name that says what it is: how stale
            # the per-market FIGURES are. Distinct from sweep duration whenever
            # some markets are unreadable, which is exactly when the two were
            # being confused.
            "data_age": (now - live_ts) if live_ts else None,
            "stale_after_sec": STALE_AFTER_SEC,
            "loop_market": p.get("market") or "",
            "loop_markets": p.get("markets"),
            "sweeps": p.get("sweeps"),
            "exited": len([r for r in rows if r["gate"] == "EXITED"]),
            "widened": len([r for r in rows if r["gate"] == "WIDENED"]),
            "capital": cap,
            # Honest wallet return: offers-only was the old misleading
            # denominator. Open inventory is committed capital too.
            "return_pct_day": (100 * inc / committed_total)
                               if committed_total else 0.0,
            "merged_shares": merged_total,
            "recycled_usd": sum(r["recycled_usd"] for r in rows),
            "pairing_rate": ((merged_total / vr["verified_shares"])
                             if vr["verified_shares"] > 1e-9 else None),
            "verified": vr,
            "funded_total": sum(r["daily"] for r in rows),
            "fills": sum(r["fills"] for r in rows),
            "uptime": (sum(r["uptime"] for r in rows) / len(rows)) if rows else 0,
            "concentration": (max((r["income"] for r in rows), default=0) / inc)
                             if inc else 0,
            "active_quoting": active_quoting,
            "book_health_ratio": book_health_ratio,
            "gate_refusals": refusal_counts,
            # U35: the pairs-only rule's measured EV per one-sided fill
            # (completion rate x merge capture - exit rate x half-spread).
            "pairs_ev": snap["pairs_ev"],
        },
    }


# NEAR-MISS TRACKER (U21): how much evidence it takes to loosen a gate.
#
# The FILTERS lane estimates, for every ranker-rejected market, what the
# allocator would have said had it been adopted (first-dollar marginal %/day
# vs the 2%/day floor). Markets that clear the floor are near-misses -- the
# ranker's gates refused them but the allocator would have funded them, so
# each is a candidate for loosening a gate. The ranker appends them to
# run/near_misses.jsonl, one line per rank, and this is the accumulated read.
#
# What the numbers mean and why these bars:
#   * The estimate is a SINGLE snapshot of the venue's own score reading,
#     not the fleet's 30-min average -- noisy, and optimistic (the crowd can
#     arrive after we quote). No single rank justifies a gate change.
#   * The log can only validate CONSISTENCY of the estimate, never its
#     profitability -- a near-miss's actual markout is unobservable until the
#     fleet trades it. The bars therefore license a CONTROLLED TRIAL (adopt
#     the small-margin greens, watch markouts and the gate), and only
#     positive trial markouts justify loosening the bar itself.
#   * Days guard against one evening's slate (the universe is day-sport
#     dominated); unique markets guard against 144 rows/day of the same ~15
#     cids re-appearing; the small-margin depth count is the operational
#     proof that a SPECIFIC loosening (e.g. $1,000 -> $750) admits concrete
#     markets; the stability fraction guards against the estimate firing in
#     one wild hour.
#   * days/unique/small-margin accumulate over the WHOLE file -- all-time
#     evidence is the point of the log -- while stability is the only recency
#     gate (last 72 ranks). The two are deliberately different: evidence
#     accumulates, persistence is judged on the recent window.
NEAR_MISS_MIN_DAYS = 3.0
NEAR_MISS_MIN_UNIQUE = 25
NEAR_MISS_MIN_SMALL_MARGIN = 5
NEAR_MISS_MIN_STABILITY = 0.5
NEAR_MISS_LOOKBACK_RANKS = 72
NEAR_MISS_FILE = RUN / "near_misses.jsonl"


def near_miss_stats() -> dict:
    """Accumulated near-miss evidence, and whether it justifies a trial."""
    ranks: list[dict] = []
    greens: list[dict] = []
    depth_unparsed_total = 0
    if NEAR_MISS_FILE.exists():
        try:
            with open(NEAR_MISS_FILE, encoding="utf-8") as fh:
                for ln in fh:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        row = json.loads(ln)
                    except ValueError:
                        continue
                    if not isinstance(row, dict) or "greens" not in row:
                        continue
                    ranks.append(row)
                    depth_unparsed_total += int(row.get("depth_unparsed") or 0)
                    ts = row.get("ts")
                    for g in row.get("greens") or []:
                        if not isinstance(g, dict):
                            continue
                        g = dict(g)
                        g["ts"] = ts
                        greens.append(g)
        except Exception:
            pass

    # The MIRAGE rule, applied to every decision bar below: a near-miss whose
    # depth is under half the gate bar, or whose estimate blew past 10%/day
    # (pot / ~zero competition -- the Dem-retirees 890%/day and UK-inflation
    # 4,938%/day shapes), is an empty book, not an opportunity. Only the
    # CREDIBLE set feeds the bars, or a single wild rank of empty books would
    # trip "enough evidence" on garbage.
    def _is_trap(g):
        d, b = g.get("depth_measured"), g.get("depth_bar")
        # `is not None`, not truthiness: a fully empty book parses depth
        # "$0.00" -> 0.0, which is falsy and MUST still count as a trap --
        # it is the exact empty-book shape the rule exists to catch, and it
        # must agree with the verdict's `db > 0 and dm < 0.5 * db` arm.
        if d is not None and b and d < 0.5 * b:
            return True
        return (g.get("marg_pct_day") or 0.0) > 10.0

    credible = [g for g in greens if not _is_trap(g)]
    unique_cids = {g.get("cid") for g in credible if g.get("cid")}
    days = {time.strftime("%Y-%m-%d", time.gmtime(g["ts"]))
            for g in credible if g.get("ts")}
    # A depth reject whose measured depth was at least half the bar: a modest
    # loosening (e.g. $1,000 -> $750 or $500) would have admitted it, which is
    # the concrete candidate list a trial would adopt. All-time evidence
    # within the credible set: ANY credible reading qualifies.
    small_margin_cids = {
        g.get("cid") for g in credible
        if g.get("depth_measured") and g.get("depth_bar")
        and g["depth_measured"] >= 0.5 * g["depth_bar"]}
    # If the venue ever changes the depth-gate reason format, the logger's
    # parse goes quiet and the small-margin bar silently undercounts. Surface
    # the count so that degradation is visible instead of invisible.
    depth_unparsed = sum(1 for g in credible
                         if (g.get("cause") or "").endswith("top-3 bid depth")
                         and not g.get("depth_measured"))
    # Stability is a fraction of recent RANKS with at least one credible
    # green: ranks with none count against the estimate, or one wild hour of
    # deep books would look like a persistent opportunity.
    recent = sorted((r for r in ranks if r.get("ts")),
                    key=lambda r: -r["ts"])[:NEAR_MISS_LOOKBACK_RANKS]
    stable_ranks = sum(1 for r in recent
                       if any(not _is_trap(g) for g in r.get("greens") or []))
    stability = (stable_ranks / len(recent)) if recent else 0.0

    per_cause: dict[str, int] = {}
    for g in credible:
        k = g.get("cause") or "other"
        per_cause[k] = per_cause.get(k, 0) + 1
    # The $/day pot the CURRENT unique near-miss set represents (last reading
    # per cid) -- a materiality read, not a rate. Summed over the CREDIBLE
    # set only: an empty-window trap carries a giant pot no sane quote would
    # capture -- the Yankees game at $15,768/day on $22 of depth made the
    # all-in total read $16,124/day while the credible total (depth >= 50% of
    # the bar AND marg <= 10%/day) was ~$3/day, a single market. The raw
    # total is kept alongside so the gap itself stays visible.
    # One pass over the LAST reading per market, so the trap count, the
    # credible pot and the raw pot all use the same unique-cid basis.
    last_by_cid: dict[str, dict] = {}
    for g in greens:
        if g.get("cid"):
            last_by_cid[g["cid"]] = g
    by_cid: dict[str, float] = {}
    raw_by_cid: dict[str, float] = {}
    pot_traps = 0
    for cid, g in last_by_cid.items():
        raw_by_cid[cid] = g.get("pot_day") or 0.0
        if _is_trap(g):
            pot_traps += 1
            continue
        by_cid[cid] = g.get("pot_day") or 0.0

    ready = (len(days) >= NEAR_MISS_MIN_DAYS
             and len(unique_cids) >= NEAR_MISS_MIN_UNIQUE
             and len(small_margin_cids) >= NEAR_MISS_MIN_SMALL_MARGIN
             and stability >= NEAR_MISS_MIN_STABILITY)
    if ready:
        status = "READY_TO_TRIAL"
    elif greens:
        status = "COLLECTING"
    else:
        status = "NO_DATA"

    return {
        "status": status,
        "ranks": len(ranks),
        "greens": len(credible),
        "traps": len(greens) - len(credible),
        "days": len(days), "min_days": NEAR_MISS_MIN_DAYS,
        "unique_markets": len(unique_cids), "min_unique": NEAR_MISS_MIN_UNIQUE,
        "small_margin_depth": len(small_margin_cids),
        "min_small_margin": NEAR_MISS_MIN_SMALL_MARGIN,
        "depth_unparsed": depth_unparsed + depth_unparsed_total,
        "stability": round(stability, 3),
        "min_stability": NEAR_MISS_MIN_STABILITY,
        "lookback_ranks": NEAR_MISS_LOOKBACK_RANKS,
        "uniq_pot_day": round(sum(by_cid.values()), 2),
        "raw_uniq_pot_day": round(sum(raw_by_cid.values()), 2),
        "pot_traps": pot_traps,
        "top_causes": dict(sorted(per_cause.items(),
                                   key=lambda kv: -kv[1])[:5]),
    }


# VOLUME NEAR-MISS TRACKER (U34): the gate that actually binds.
#
# U33's triage overturned the depth tracker's premise. Of the recorded
# depth-reject population, 83 of 110 markets -- including 5 of the 6
# near-misses -- would fail the live $250k/24h volume gate anyway, and the
# $500 depth trial adopted zero new markets because the depth-clear
# candidates failed live re-verification on volume. Depth was never the
# binding constraint; volume is, and until U34 nothing watched it: volume
# rejects are refused inside `evaluate`/`tradable`, so they never reached the
# depth log, which only keeps would-fund greens.
#
# The ranker appends every volume-rejected market with a measured 24h volume
# to run/volume_near_misses.jsonl -- one line per rank, mirroring the depth
# log -- and this is the accumulated read. Same evidence philosophy: a
# "small-margin" market (measured volume >= half the bar) is the concrete
# candidate a loosening to $125k would admit, and the bars license a
# CONTROLLED TRIAL -- consistency of the volume readings over days, then a
# staged loosening whose adopted markets' markouts are watched.
#
# There is deliberately NO marg-estimate trap arm here: a volume-reject has
# ALREADY cleared the depth gate (volume is gated after depth in the ranker),
# so its book is real and a large pot/competition ratio is an opportunity
# signal, not the empty-book mirage the depth trap catches. The one thing
# excluded is a MISSING measurement -- that is a data gap, not a near-miss.
VOLUME_NEAR_MISS_MIN_DAYS = 3.0
VOLUME_NEAR_MISS_MIN_UNIQUE = 25
VOLUME_NEAR_MISS_MIN_SMALL_MARGIN = 5
VOLUME_NEAR_MISS_MIN_STABILITY = 0.5
VOLUME_NEAR_MISS_LOOKBACK_RANKS = 72
VOLUME_NEAR_MISS_FILE = RUN / "volume_near_misses.jsonl"
# Half the volume bar: the "modest loosening" marker, mirroring the depth
# tracker's 0.5 * depth_bar. A market at >= half the bar is the concrete
# candidate a $125k trial bar would admit. The choice is DELIBERATE about
# what it will not do: U33 measured the volume-reject population 1-3 orders
# of magnitude under the bar, so this count staying at zero is the honest
# signal that the gate rejects far-misses, not near-misses -- it does not
# stretch the definition down to a loosening the strategy would not stage.
VOLUME_NEAR_MISS_FRACTION = 0.5


def volume_near_miss_stats() -> dict:
    """Accumulated volume-reject evidence, and whether it justifies a trial."""
    ranks: list[dict] = []
    vols: list[dict] = []
    volume_unknown_total = 0
    if VOLUME_NEAR_MISS_FILE.exists():
        try:
            with open(VOLUME_NEAR_MISS_FILE, encoding="utf-8") as fh:
                for ln in fh:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        row = json.loads(ln)
                    except ValueError:
                        continue
                    if not isinstance(row, dict) or "volumes" not in row:
                        continue
                    ranks.append(row)
                    volume_unknown_total += int(row.get("volume_unknown") or 0)
                    ts = row.get("ts")
                    for g in row.get("volumes") or []:
                        if not isinstance(g, dict):
                            continue
                        g = dict(g)
                        g["ts"] = ts
                        vols.append(g)
        except Exception:
            pass

    def _is_trap(g):
        # A measured volume near or under the bar is DATA; a missing
        # measurement is a gap. Same `is not None` discipline as the depth
        # trap -- 0.0 volume is falsy but is a real reading of a market that
        # trades nothing.
        v, b = g.get("volume_measured"), g.get("volume_bar")
        return v is None or not b

    credible = [g for g in vols if not _is_trap(g)]
    unique_cids = {g.get("cid") for g in credible if g.get("cid")}
    days = {time.strftime("%Y-%m-%d", time.gmtime(g["ts"]))
            for g in credible if g.get("ts")}
    # A volume reject whose measured volume reached at least half the bar: a
    # loosening to $125k (0.5 * $250k) would have admitted it -- the concrete
    # candidate list a volume trial would adopt. All-time, any reading, like
    # the depth tracker's small-margin count.
    small_margin_cids = {
        g.get("cid") for g in credible
        if g.get("volume_measured") and g.get("volume_bar")
        and g["volume_measured"] >= (VOLUME_NEAR_MISS_FRACTION
                                      * g["volume_bar"])}
    # The WHOLE measured population -- the ~80 markets per rank the volume
    # gate refuses. Distinct from the near-miss set: most are FAR below the
    # bar, which is itself the finding (the gate is not rejecting
    # near-misses).
    watched_cids = {g.get("cid") for g in credible if g.get("cid")}

    # Stability is a fraction of recent RANKS with at least one measured
    # volume-reject -- the population is persistent by construction, so this
    # bar is nearly always met when the log is being written; the bar that
    # binds is small_margin_volume.
    recent = sorted((r for r in ranks if r.get("ts")),
                    key=lambda r: -r["ts"])[:VOLUME_NEAR_MISS_LOOKBACK_RANKS]
    stable_ranks = sum(1 for r in recent
                       if any(not _is_trap(g)
                              for g in r.get("volumes") or []))
    stability = (stable_ranks / len(recent)) if recent else 0.0

    last_by_cid: dict[str, dict] = {}
    for g in vols:
        if g.get("cid"):
            last_by_cid[g["cid"]] = g
    by_cid: dict[str, float] = {}
    raw_by_cid: dict[str, float] = {}
    pot_gaps = 0
    for cid, g in last_by_cid.items():
        raw_by_cid[cid] = g.get("pot_day") or 0.0
        if _is_trap(g):
            pot_gaps += 1
            continue
        by_cid[cid] = g.get("pot_day") or 0.0

    ready = (len(days) >= VOLUME_NEAR_MISS_MIN_DAYS
             and len(unique_cids) >= VOLUME_NEAR_MISS_MIN_UNIQUE
             and len(small_margin_cids) >= VOLUME_NEAR_MISS_MIN_SMALL_MARGIN
             and stability >= VOLUME_NEAR_MISS_MIN_STABILITY)
    if ready:
        status = "READY_TO_TRIAL"
    elif vols:
        status = "COLLECTING"
    else:
        status = "NO_DATA"

    # The closest markets right now: last reading per cid, ranked by how far
    # along the bar the measured volume is. Rank-time snapshots, not today's
    # book -- the note on the panel says so.
    closest: list[dict] = []
    for cid, g in last_by_cid.items():
        v, b = g.get("volume_measured"), g.get("volume_bar")
        if v is None or not b:
            continue
        closest.append({
            "cid": cid, "title": g.get("title"), "slug": g.get("slug"),
            "volume": v, "bar": b, "ratio": round(v / b, 3),
            "pot_day": g.get("pot_day"), "days": g.get("days"),
        })
    closest.sort(key=lambda m: -m["ratio"])
    closest = closest[:5]

    return {
        "status": status,
        "ranks": len(ranks),
        "watched": len(watched_cids),
        "volumes": len(credible),
        "gaps": len(vols) - len(credible),
        "volume_unknown_total": volume_unknown_total,
        "days": len(days), "min_days": VOLUME_NEAR_MISS_MIN_DAYS,
        "unique_markets": len(unique_cids),
        "min_unique": VOLUME_NEAR_MISS_MIN_UNIQUE,
        "small_margin_volume": len(small_margin_cids),
        "min_small_margin": VOLUME_NEAR_MISS_MIN_SMALL_MARGIN,
        "stability": round(stability, 3),
        "min_stability": VOLUME_NEAR_MISS_MIN_STABILITY,
        "lookback_ranks": VOLUME_NEAR_MISS_LOOKBACK_RANKS,
        "uniq_pot_day": round(sum(by_cid.values()), 2),
        "raw_uniq_pot_day": round(sum(raw_by_cid.values()), 2),
        "pot_gaps": pot_gaps,
        "volume_bar": CFG.select_min_volume_24h_usd,
        "half_bar_usd": round(CFG.select_min_volume_24h_usd
                               * VOLUME_NEAR_MISS_FRACTION),
        "closest": closest,
    }


@app.get("/api/pipeline")
def pipeline():
    """The market-selection funnel, live: raw -> filters -> final -> adopted.

    Serves run/pipeline.json -- rewritten by scripts/rank_markets.py on every
    rank, capturing the raw pools, every rejection bucketed by gate with
    example titles, the eligible ranking and the picks -- plus the fleet's
    CURRENT universe from run/markets.json, annotated with live fleet state
    from run/fleet_state.json so the last lane shows what the fleet is
    actually working right now, not just what the latest rank chose.

    All three files are telemetry; nothing here writes anything.
    """
    now = time.time()
    snap = None
    f = RUN / "pipeline.json"
    if f.exists():
        try:
            snap = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            snap = None

    hist = stats.db_stats()
    live_by_cid: dict[str, dict] = {}
    state_f = RUN / "fleet_state.json"
    if state_f.exists():
        try:
            for s in json.loads(state_f.read_text(encoding="utf-8")):
                live = s.get("_live") or {}
                live_by_cid[s["cid"]] = {
                    "income": live.get("income", 0.0),
                    "capital": live.get("capital", 0.0),
                    "share": live.get("share", 0.0),
                    "err": bool(live.get("err")),
                    # The live refusal string itself, not just the bool -- a
                    # market showing $0.00/day with no explanation is how
                    # "the fleet is doing nothing" reads to an operator, when
                    # it is actually refusing a market for a named reason
                    # (depth gate, book failure, band). The card renders this.
                    "err_text": live.get("err") or "",
                    "why": live.get("why") or "",
                    "ts": live.get("ts"),
                    "source": live.get("source") or s.get("source", ""),
                    "alloc": live.get("alloc"),
                }
        except Exception:
            pass

    graduated: list[dict] = []
    picked_n = 0
    live_n = 0
    mf = RUN / "markets.json"
    if mf.exists():
        try:
            specs = json.loads(mf.read_text(encoding="utf-8"))
        except Exception:
            specs = []
        if isinstance(specs, list):
            picked_n = len(specs)
            for s in specs:
                live = live_by_cid.get(s.get("cid"))
                is_live = live is not None
                # Presence in fleet_state, NOT income > 0: the lane badge
                # shows LIVE for any row the fleet is working (a defunded
                # market still being quoted is live), so the header count
                # must count the same rows or the two disagree.
                if is_live:
                    live_n += 1
                h = hist.get(s.get("cid"), {})
                graduated.append({
                    "title": s.get("title", ""),
                    "slug": s.get("slug", ""),
                    "url": (f"https://polymarket.com/market/{s['slug']}"
                            if s.get("slug") else ""),
                    "source": (live or {}).get("source")
                              or s.get("source", "rewards"
                                       if (s.get("daily") or 0) > 0
                                       else "spread"),
                    "income": (live or {}).get("income", 0.0),
                    "capital": (live or {}).get("capital", 0.0),
                    "share": (live or {}).get("share", 0.0),
                    "fills": h.get("fills", 0),
                    "uptime": h.get("uptime", 0.0),
                    "err": bool((live or {}).get("err")),
                    "err_text": (live or {}).get("err_text") or "",
                    "live": is_live,
                    "daily": s.get("daily", 0.0),
                    "alloc": (live or {}).get("alloc"),
                })

    fleet_alive = None
    if state_f.exists():
        p = _pulse()
        live_ts = max((v.get("ts") or 0 for v in live_by_cid.values()),
                      default=0.0)
        _, _, fleet_stale, _ = _heartbeat(now, live_ts,
                                          state_f.stat().st_mtime, p)
        fleet_alive = not fleet_stale

    return {
        "now": now,
        "snapshot": snap,
        "snapshot_age": (now - snap["ts"])
                         if snap and snap.get("ts") else None,
        "graduated": graduated,
        "picked": picked_n,
        "live": live_n,
        "fleet_alive": fleet_alive,
        "near_miss": near_miss_stats(),
        "volume_near_miss": volume_near_miss_stats(),
    }


PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Spread Hunter Fleet</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=IBM+Plex+Mono:wght@400;500;600;700&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>
 :root{
   --bg:#0a0d12; --panel:#12161d; --panel-2:#171c24; --line:#232a35; --line-soft:#1a2029;
   --tx:#e7ebf3; --tx-dim:#8792a6; --tx-faint:#535e70;
   --up:#33c9b5; --up-soft:#12302c;
   --down:#f0684d; --down-soft:#3a201a;
   --gold:#e8b84b; --gold-soft:#3a2f18;
   --proj:#7b9bf7; --proj-soft:#1c2540;
   --alert:#ff5c5c;
   --r-lg:12px; --r-md:8px; --r-sm:5px;
   --disp:'Space Grotesk',system-ui,sans-serif;
   --mono:'IBM Plex Mono',ui-monospace,Menlo,Consolas,monospace;
   --body:'IBM Plex Sans',system-ui,-apple-system,"Segoe UI",sans-serif;
 }
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--tx);font:14px/1.5 var(--body);
      -webkit-font-smoothing:antialiased}
 a{color:inherit}
 a:focus-visible,button:focus-visible{outline:2px solid var(--proj);outline-offset:2px}
 .up{color:var(--up)}.down{color:var(--down)}.gold{color:var(--gold)}
 .proj{color:var(--proj)}.alert-tx{color:var(--alert)}.dim{color:var(--tx-dim)}
 .bold{font-weight:600}.mono{font-family:var(--mono)}
 .num{text-align:right;font-variant-numeric:tabular-nums}
 .mid-label{color:#0a0d12;background:var(--gold);border-radius:3px;padding:1px 4px;text-shadow:none;box-shadow:0 0 8px rgba(232,184,75,.65)}
 .action-cell{min-width:210px;max-width:290px}.action-pill{display:inline-block;border-radius:99px;padding:2px 8px;font-size:10px;letter-spacing:.07em;font-weight:700}.action-recent{font-family:var(--mono);font-size:10px;line-height:1.35;margin-top:4px} .event-line{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:270px}
 .settled-section{padding:24px;background:var(--bg);border-top:1px solid var(--line)}
 .section-heading{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:10px;color:var(--tx-dim);font-size:11px;letter-spacing:.1em;text-transform:uppercase;font-weight:600}
 .settled-section table{background:var(--panel);border:1px solid var(--line);border-radius:var(--r-md);overflow:hidden}
 .settled-section td,.settled-section th{padding:10px 12px}
 .settled-empty{text-align:center;color:var(--tx-dim);padding:22px!important;font-size:12px}


 /* ---------- masthead ---------- */
 .mast{display:flex;align-items:center;gap:14px;padding:14px 24px;
       background:var(--panel);border-bottom:1px solid var(--line)}
 .mast-id{font-family:var(--disp);font-weight:700;font-size:16px;letter-spacing:.01em}
 .mast-id b{color:var(--gold)}
 .tag{border:1px solid var(--down);color:var(--down);border-radius:99px;
      padding:3px 10px;font-size:11px;font-weight:600;letter-spacing:.06em}
 .legend{display:flex;gap:14px;font-size:11px;color:var(--tx-dim);letter-spacing:.02em}
 .legend span{display:inline-flex;align-items:center;gap:5px}
 .legend i{width:7px;height:7px;border-radius:50%;display:inline-block}
 .live{font-size:12px;font-weight:600}
 .clock{font-family:var(--mono);font-size:12px;color:var(--tx-dim)}

 /* ---------- hero ---------- */
 /* The equation and the strip live in hero-main, so main takes the width and
    the rail is capped. It was the other way round, which squeezed a one-line
    sum into four wrapped lines while six rank bars -- five of them $0.00 --
    stretched across two thirds of the screen. */
 .hero{display:grid;grid-template-columns:1fr minmax(300px,380px);gap:1px;
       background:var(--line);border-bottom:1px solid var(--line)}
 .hero-main{background:var(--panel);padding:24px 28px}
 .hero-eyebrow{font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--tx-dim);font-weight:600}
 .hero-duo{display:flex;flex-wrap:wrap;gap:36px;align-items:flex-start}
 .hero-value{font-family:var(--disp);font-size:44px;font-weight:700;line-height:1.05;margin-top:8px;
             transition:color .3s ease}
 /* The sum reads as one statement or it reads as noise -- 32ch broke it
    across four lines. Wraps only when the viewport genuinely cannot hold it. */
 .hero-sub{font-size:13px;color:var(--tx-dim);margin-top:8px;max-width:none;
   line-height:1.7}
 .hero-spark{width:100%;height:36px;margin-top:14px;display:block}
 /* The five facts that decide whether this run means anything, on one line.
    They were spread across three KPI groups, so answering "is it working?"
    meant assembling them by eye every time. */
 .hero-strip{display:flex;flex-wrap:wrap;gap:22px;margin-top:16px;
   padding-top:14px;border-top:1px solid var(--line-soft)}
 .hs{min-width:96px}
 .hs .hs-n{font-size:10px;letter-spacing:.08em;text-transform:uppercase;
   color:var(--tx-dim);font-weight:600}
 .hs .hs-v{font-family:var(--mono);font-size:17px;font-weight:600;margin-top:3px}
 .hs .hs-s{font-size:11px;color:var(--tx-dim);margin-top:1px}
 .hero-rail{background:var(--panel);padding:24px 28px;display:flex;flex-direction:column;gap:14px}
 .hero-rail-hdr{font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--tx-dim);font-weight:600}
 .rank-row{display:grid;grid-template-columns:1fr 84px auto;align-items:center;gap:10px;font-size:12px}
 .rank-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--tx-dim)}
 .rank-track{height:8px;background:var(--line-soft);border-radius:4px;overflow:hidden}
 .rank-fill{height:100%;background:var(--up);border-radius:4px;transition:width .4s ease}
 .rank-val{font-family:var(--mono);font-weight:600;text-align:right;min-width:6ch}

 /* ---------- gauge strip ---------- */
 .gauge-strip{background:var(--panel);border-bottom:1px solid var(--line);padding:14px 28px;
              display:flex;align-items:center;gap:16px}
 .gauge-label{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--tx-dim);
              font-weight:600;min-width:15ch}
 .gauge-track{flex:1;height:14px;background:var(--line-soft);border-radius:7px;position:relative;overflow:hidden}
 .gauge-fill{height:100%;border-radius:7px;background:var(--up);transition:width .4s ease,background .4s ease}
 .gauge-cap{position:absolute;top:-3px;bottom:-3px;width:2px;background:var(--tx-faint)}
 .gauge-value{font-family:var(--mono);font-size:12px;color:var(--tx-dim);min-width:16ch;text-align:right}
 .sample-blocks{font-size:13px;letter-spacing:.06em;white-space:nowrap;line-height:1}

 /* ---------- kpi groups ---------- */
 .kpi-wrapper{display:flex;flex-direction:column;gap:18px;padding:20px 24px;
              background:var(--bg);border-bottom:1px solid var(--line)}
 .kpi-hdr{color:var(--tx-faint);font-size:11px;padding:0 0 8px 2px;
          letter-spacing:.1em;font-weight:600;text-transform:uppercase}
 .kpi-group{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));
            gap:1px;background:var(--line);border:1px solid var(--line);border-radius:var(--r-md);overflow:hidden}
 .k{background:var(--panel);padding:14px 16px}
 .k.alert{box-shadow:inset 3px 0 0 var(--alert);background:rgba(255,92,92,.06)}
 .k .n{color:var(--tx-dim);font-size:10.5px;letter-spacing:.07em;text-transform:uppercase;font-weight:600}
 .k .v{font-family:var(--mono);font-size:21px;font-weight:600;margin-top:5px}
 .k .s{color:var(--tx-faint);font-size:11.5px;margin-top:4px;line-height:1.35}

 .exp-err{padding:12px 24px;background:rgba(255,92,92,.1);color:var(--alert);
          border-bottom:1px solid var(--line);display:none;font-weight:500;font-size:13px}

 /* ---------- table ---------- */
 .wrap{overflow-x:auto}
 table{width:100%;border-collapse:collapse;font-size:13px}
 th{text-align:left;color:var(--tx-faint);font-weight:600;font-size:10.5px;
    letter-spacing:.07em;padding:12px;border-bottom:1px solid var(--line);text-transform:uppercase;
    white-space:nowrap}
 td{padding:12px;border-bottom:1px solid var(--line-soft);vertical-align:middle}
 tr:hover td{background:var(--panel-2)}
 tr.alert td{background:rgba(255,92,92,.05)}
 .mkt-link{color:var(--tx);text-decoration:none;font-weight:500}
 .mkt-link:hover{color:var(--gold);text-decoration:underline}

 /* ---------- view switcher + market-pipeline (selection funnel) ---------- */
 .views{display:flex;gap:3px;background:var(--panel-2);border:1px solid var(--line);border-radius:99px;padding:3px;margin-left:6px}
 .view-btn{border:0;background:transparent;color:var(--tx-dim);font:600 12px/1 var(--body);letter-spacing:.05em;padding:6px 14px;border-radius:99px;cursor:pointer;transition:color .15s,background .15s}
 .view-btn:hover{color:var(--tx);background:rgba(255,255,255,.05)}
 .view-btn.active{background:var(--proj-soft);color:var(--proj);box-shadow:inset 0 0 0 1px rgba(123,155,247,.35)}
 .pipe-view{padding:20px 24px}
 .pipe-strip{display:flex;flex-direction:column;gap:6px;padding:12px 14px;background:var(--panel);border:1px solid var(--line);border-radius:var(--r-md);margin-bottom:14px}
 .pipe-census{font-family:var(--mono);font-size:11px;color:var(--tx-dim)}
 .pipe-chain{font-family:var(--mono);font-size:12px;color:var(--tx)}
 .pipe-chain span{margin:0 2px}
 .pipe-gates{font-size:11px;color:var(--tx-faint)}
 .pipe-board{display:grid;grid-template-columns:minmax(250px,1fr) 26px minmax(280px,1.35fr) 26px minmax(250px,1fr) 26px minmax(280px,1.2fr);gap:8px;align-items:stretch}
 .pipe-arrow{align-self:center;text-align:center;font-size:20px;color:var(--tx-faint);user-select:none}
 .pipe-lane{background:var(--panel);border:1px solid var(--line);border-radius:var(--r-md);display:flex;flex-direction:column;min-height:340px;max-height:680px}
 .pipe-lane-raw{border-color:var(--line)}
 .pipe-lane-filter{border-color:rgba(240,104,77,.45)}
 .pipe-lane-final{border-color:rgba(123,155,247,.45)}
 .pipe-lane-grad{border-color:rgba(51,201,181,.45)}
 .pipe-lane-hdr{padding:10px 12px;border-bottom:1px solid var(--line);display:flex;align-items:flex-start;justify-content:space-between;gap:8px}
 .pipe-lane-hdr h3{margin:0;font:700 12px/1.25 var(--disp);letter-spacing:.08em;text-transform:uppercase}
 .pipe-count{font:700 13px/1 var(--mono);border-radius:99px;padding:4px 8px;background:var(--panel-2);border:1px solid var(--line);white-space:nowrap}
 .pipe-lane-body{padding:8px;overflow:auto;display:flex;flex-direction:column;gap:8px;flex:1}
 .pipe-lane-body::-webkit-scrollbar{width:6px}
 .pipe-lane-body::-webkit-scrollbar-thumb{background:var(--line);border-radius:3px}
 .pipe-group{border:1px solid var(--line-soft);border-radius:var(--r-sm);padding:6px}
 .pipe-group-hdr{font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:var(--tx-faint);font-weight:600;margin-bottom:6px}
 .pipe-empty{padding:6px;font-size:11px;color:var(--tx-dim)}
 .chip{background:var(--panel-2);border:1px solid var(--line);border-radius:var(--r-sm);padding:5px 7px;font-size:11px;line-height:1.3}
 .chip-t{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:230px}
 .chip-s{font-family:var(--mono);font-size:10px;color:var(--tx-dim);margin-top:2px}
 .gate-card{background:var(--down-soft);border:1px solid rgba(240,104,77,.35);border-radius:var(--r-sm);padding:7px 9px}
 .gate-hdr{display:flex;align-items:center;justify-content:space-between;gap:8px}
 .gate-name{font-weight:600;font-size:12px;text-transform:capitalize}
 .gate-n{font:700 13px/1 var(--mono);color:var(--down);background:rgba(240,104,77,.15);border-radius:99px;padding:3px 8px}
 .gate-exs{margin-top:6px;display:flex;flex-direction:column;gap:3px}
 .gate-ex{font-size:10.5px;color:var(--tx-dim)}
 .gate-ex-t{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:250px}
 .gate-ex .r{color:var(--tx-faint)}
 .gate-marg{margin-top:2px;font:10px/1.3 var(--mono)}
 .gate-marg.trap{color:var(--gold)}
 .gate-near{margin-top:5px;font:10px/1.2 var(--mono)}
 .gate-near.trap{color:var(--gold)}
 .pipe-near{background:var(--panel);border:1px solid var(--line);border-radius:var(--r-md);padding:9px 11px;margin-top:8px}
 .pipe-near-hdr{display:flex;align-items:center;justify-content:space-between;gap:8px}
 .pipe-near-t{font-size:11px;font-weight:600;letter-spacing:.06em;color:var(--tx-dim)}
 .pipe-near-body{display:flex;flex-wrap:wrap;gap:7px 16px;margin-top:7px}
 .pn-tile{display:flex;flex-direction:column;gap:2px}
 .pn-tl{font-size:9px;text-transform:uppercase;letter-spacing:.05em;color:var(--tx-faint)}
 .pn-tv{font:600 12px/1 var(--mono)}
 .pipe-near-sub{margin-top:7px;font-size:10px}
 .pipe-near-note{margin-top:7px;font-size:10.5px;border-top:1px dashed var(--line);padding-top:5px}
 .mkt-card{background:var(--panel-2);border:1px solid var(--line);border-radius:var(--r-sm);padding:7px 9px;transition:border-color .15s}
 .mkt-card:hover{border-color:var(--proj)}
 .mkt-top{display:flex;align-items:center;justify-content:space-between;gap:6px}
 .mkt-t{font-size:11.5px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:200px}
 .mkt-mid{display:flex;gap:10px;margin-top:5px;font-size:11px;flex-wrap:wrap}
 .mkt-sub{margin-top:3px;font-size:10px}
 .alloc-line{margin-top:5px;padding-top:4px;border-top:1px dashed var(--line);font-size:10px}
 .alloc-line .alloc-nums{margin-top:2px}
 .pill{font-size:9px;letter-spacing:.07em;font-weight:700;border-radius:99px;padding:2px 6px;white-space:nowrap}
 .pill-rew{color:var(--gold);background:var(--gold-soft)}
 .pill-spr{color:var(--proj);background:var(--proj-soft)}
 .pill-live{color:var(--up);background:var(--up-soft)}
 .pill-wait{color:var(--tx-dim);background:var(--panel-2);border:1px solid var(--line)}
 @media(max-width:1500px){
   .pipe-board{grid-template-columns:1fr 1fr}
   .pipe-arrow{display:none}
 }
 @media(max-width:900px){
   .pipe-board{grid-template-columns:1fr}
   .mast{flex-wrap:wrap}
 }
</style></head><body>
<header class="mast">
  <div class="mast-id"><b>◆</b> Spread Hunter Fleet</div>
  <span class="tag">Paper · simulated fills</span>
  <span class="legend">
    <span><i style="background:var(--up)"></i>gain</span>
    <span><i style="background:var(--down)"></i>loss / risk</span>
    <span><i style="background:var(--gold)"></i>income</span>
    <span><i style="background:var(--proj)"></i>projected</span>
  </span>
  <nav class="views" id="viewNav" title="Switch between the fleet page and the live market-selection funnel">
    <button type="button" id="viewFleet" class="view-btn active">Fleet</button>
    <button type="button" id="viewScan" class="view-btn">Market scan</button>
  </nav>
  <span style="flex:1"></span>
  <span id="live" class="live"></span>
  <span id="health" class="live"></span>
  <span id="clock" class="clock"></span>
</header>
<div id="view-fleet">
<section class="hero">
  <div class="hero-main">
    <div class="hero-duo">
      <div>
        <div class="hero-eyebrow">Liquidation P&L</div>
        <div class="hero-value" id="heroValue">$0.00</div>
      </div>
      <!-- The modelled side, deliberately the same size and deliberately a
           different colour. It is income the strategy claims to have earned,
           and it is NOT summed into the hard number to its left: for a
           spread-funded market that income arrives by being filled, so it is
           already inside `booked` and `pairs held`. Two figures, one hard and
           one modelled, is the honest presentation -- adding them would book
           the same dollars twice. -->
      <div>
        <div class="hero-eyebrow">Unrealized P&L <span class="dim">(Floating)</span></div>
        <div class="hero-value proj" id="heroIncome">$0.00</div>
      </div>
    </div>
    <div class="hero-sub" id="heroBridge">&nbsp;</div>
    <svg class="hero-spark" id="heroSpark" viewBox="0 0 300 36" preserveAspectRatio="none"></svg>
    <div class="hero-strip" id="heroStrip"></div>
  </div>
  <div class="hero-rail">
    <div class="hero-rail-hdr">Which market is carrying the fleet</div>
    <div id="rankRows"></div>
  </div>
</section>
<div class="gauge-strip">
  <div class="gauge-label">Wallet committed</div>
  <div class="gauge-track"><div class="gauge-fill" id="gaugeFill"></div><div class="gauge-cap" id="gaugeCap"></div></div>
  <div class="gauge-value" id="gaugeValue"></div>
</div>
<!-- SAMPLE PROGRESS. The markout gate cannot act on a thin sample, so until
     this bar fills the fleet's verdicts are noise and every markout tile below
     is provisional. Sitting beside the wallet gauge because "can I trust these
     numbers yet?" is asked at the same moment as "how much is committed?". -->
<div class="gauge-strip" id="sampleStrip">
  <div class="gauge-label">Sample size</div>
  <div class="sample-blocks mono" id="sampleBlocks"></div>
  <div class="gauge-track"><div class="gauge-fill" id="sampleFill"></div></div>
  <div class="gauge-value" id="sampleValue"></div>
</div>
<!-- GO-LIVE PROGRESS. Same bar language as the sample-size gauge above, but
     against the money question: how many fully-resolved markets, of the
     100 a small real-money pilot decision needs, does the fleet have right
     now. The KPI group below carries the confidence-interval detail; this is
     the one-glance version. -->
<div class="gauge-strip" id="readyStrip">
  <div class="gauge-label">Go-live progress</div>
  <div class="sample-blocks mono" id="readyBlocks"></div>
  <div class="gauge-track"><div class="gauge-fill" id="readyFill"></div></div>
  <div class="gauge-value" id="readyValue"></div>
</div>
<div class="kpi-wrapper" id="agg"></div>
<div class="exp-err" id="exp"></div>
<div class="wrap"><table id="tbl">
 <thead><tr>
  <th>Market</th>
  <th>Last action</th>
  <th class="num">Projected / day</th>
  <th class="num">Committed</th>
   <th>Order depth / mid</th>
   <th class="num">Unrealized P&L</th>
  <th class="num">Realized P&L</th>
  <th class="num">Score share</th>
  <th class="num">Uptime</th>
  <th class="num">Fills</th>
  <th>Telemetry</th>
 </tr></thead><tbody id="rows"></tbody></table></div>
<section class="settled-section">
  <div class="section-heading"><span>Realized exits</span><span class="dim" style="font-size:11px;font-weight:400">one row per close event · effective exit price · P&amp;L % is return on cost basis</span></div>
  <div class="wrap"><table id="settledTbl">
   <thead><tr>
    <th>Exit time</th><th>Market</th><th>Method</th><th class="num">Shares</th>
    <th class="num">Avg cost</th><th class="num">Effective exit price</th>
    <th class="num">P&amp;L %</th><th class="num">P&amp;L</th>
    <th class="num">Fees / gas</th><th>Exit detail</th>
   </tr></thead><tbody id="settledRows"></tbody></table></div>
</section>
</div><!-- /view-fleet -->
<section id="view-pipeline" class="pipe-view" hidden>
  <div class="pipe-strip" id="pipeStrip"></div>
  <div class="pipe-near" id="nearMiss"></div>
  <div class="pipe-near" id="volumeNearMiss"></div>
  <div class="pipe-board" id="pipeBoard">
    <div class="pipe-lane pipe-lane-raw" id="laneRaw"></div>
    <div class="pipe-arrow" aria-hidden="true">→</div>
    <div class="pipe-lane pipe-lane-filter" id="laneFilter"></div>
    <div class="pipe-arrow" aria-hidden="true">→</div>
    <div class="pipe-lane pipe-lane-final" id="laneFinal"></div>
    <div class="pipe-arrow" aria-hidden="true">→</div>
    <div class="pipe-lane pipe-lane-grad" id="laneGrad"></div>
  </div>
</section>
 <script>
const $=x=>document.getElementById(x);
const esc=s=>String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
const usd=(v,d=2)=>v==null?'-':'$'+Number(v).toFixed(d);
const pct=(v,d=1)=>v==null?'-':(100*v).toFixed(d)+'%';
const cls=v=>v==null||v===0?'dim':(v>0?'up':'down');
const thresh=(v,goodAbove,cut)=>v==null?'dim':((v>=cut)===goodAbove?'up':'down');
const hms=s=>{s=Math.max(0,Math.floor(s));
  const h=Math.floor(s/3600),m=Math.floor(s%3600/60),x=s%60;
  const p=n=>String(n).padStart(2,'0');
  return h?`${h}h ${p(m)}m ${p(x)}s`:`${m}m ${p(x)}s`;};

function ladder(m){
  const mid=m.mid_up, bid=m.up_bid, ask=m.up_ask;
  if(mid==null||bid==null||ask==null) return '<span class="dim">No two-sided book</span>';
  const v=(m.max_spread||0.045);
  const half=Math.max(v*1.35, (ask-bid)*0.75, 0.01);
  const lo=mid-half, hi=mid+half, W=hi-lo;
  const x=p=>Math.max(0,Math.min(100,100*(p-lo)/W));
  const tag=(p,cls,lbl,top)=>p==null?'':
    `<span style="position:absolute;left:${x(p)}%;top:${top}px;transform:translateX(-50%)">
       <span class="${cls}" style="font-family:var(--mono);font-weight:700;font-size:10.5px;white-space:nowrap;text-shadow:0 1px 2px rgba(0,0,0,.9)">${lbl}</span></span>`;
  const mark=(p,color,top,h)=>p==null?'':
    `<span style="position:absolute;left:${x(p)}%;top:${top}px;width:2px;height:${h}px;
       background:${color};width:${color==='var(--gold)'?'3px':'2px'};transform:translateX(-50%)"></span>`;
  const wl=x(mid-v), wr=x(mid+v);
  return `<div style="position:relative;height:38px;width:100%;max-width:280px">
    <div style="position:absolute;left:${wl}%;width:${wr-wl}%;top:12px;height:12px;
         background:var(--up-soft);border-left:1px solid #24463f;border-right:1px solid #24463f"></div>
    <div style="position:absolute;left:0;right:0;top:17px;height:1px;background:var(--line)"></div>
    ${mark(mid,'var(--gold)',7,22)}
    ${mark(bid,'var(--tx-faint)',13,10)}${mark(ask,'var(--tx-faint)',13,10)}
    ${mark(m.our_up,'var(--proj)',8,20)}
    ${mark(m.our_dn_as_up,'var(--down)',8,20)}
    ${tag(m.our_up,'proj',(m.our_up!=null?m.our_up.toFixed(3):''),27)}
    ${tag(m.our_dn_as_up,'down',(m.our_dn_as_up!=null?m.our_dn_as_up.toFixed(3):''),27)}
    ${tag(mid,'mid-label','MID '+mid.toFixed(3),-4)}
  </div>`;
}

function capBar(m){
  let up=0, dn=0, upSh=0, dnSh=0;
  for(const o of (m.quotes||[])){
    const remaining=o.remaining==null?Math.max(0,(o.size||0)-(o.filled||0)):o.remaining;
    const notional=o.notional==null?(o.price||0)*remaining:o.notional;
    if(o.side==='UP'){ up+=notional; upSh+=remaining; }
    else { dn+=notional; dnSh+=remaining; }
  }
  const total=up+dn;
  if(total<=0) return '<div class="dim" style="font-size:11px;margin-top:6px">No capital resting</div>';
  const upPct=100*up/total, dnPct=100*dn/total;
  return `<div style="width:100%;max-width:280px;margin-top:6px" title="YES ${upSh.toFixed(0)} shares / ${usd(up,2)} · NO ${dnSh.toFixed(0)} shares / ${usd(dn,2)}">
    <div style="display:flex;height:16px;background:var(--line-soft);border-radius:4px;overflow:hidden;font-size:11px;font-weight:600;letter-spacing:0.02em">
      <div style="width:${dnPct}%;background:var(--down-soft);display:flex;align-items:center;padding-left:6px;color:var(--down);white-space:nowrap">${usd(dn,0)} NO</div>
      <div style="width:${upPct}%;background:var(--proj-soft);display:flex;align-items:center;justify-content:flex-end;padding-right:6px;color:var(--proj);white-space:nowrap">${usd(up,0)} YES</div>
    </div>
    <div style="display:flex;justify-content:space-between;margin-top:3px;font-family:var(--mono);font-size:10px;color:var(--tx-dim)">
      <span class="down">NO ${dnSh.toFixed(0)} sh</span><span class="proj">YES ${upSh.toFixed(0)} sh</span>
    </div>
  </div>`;
}

function orderDepth(m){
  const detail=capBar(m);
  const yesHeld=(m.up_sh||0)*(m.up_avg||0);
  const noHeld=(m.dn_sh||0)*(m.dn_avg||0);
  const held=(m.up_sh||0)+(m.dn_sh||0)>0
    ? `<div class="dim" style="font-family:var(--mono);font-size:10px;margin-top:4px" title="Held inventory cost, separate from resting-order collateral">held YES ${(m.up_sh||0).toFixed(0)} sh / ${usd(yesHeld,0)} · NO ${(m.dn_sh||0).toFixed(0)} sh / ${usd(noHeld,0)}</div>`
    : '';
  return `${ladder(m)}${detail}${held}`;
}

function finBox(m) {
  if (!(m.paired>0) && !(m.naked_sh>0) && !(m.closes>0)) return '<span class="dim">-</span>';
  let h = `<div style="display:grid;grid-template-columns:auto 1fr;gap:6px 16px;align-items:center;font-family:var(--mono);font-size:12px">`;

  if (m.paired > 0) {
    const locked = m.paired * 1.0 - m.pair_paid;
    h += `<span class="dim" style="font-family:var(--body)">Locked</span>
          <span class="${locked>=0?'up':'down'} bold">${locked>=0?'+':''}${usd(locked)}</span>`;
  }
  if (m.naked_sh > 0) {
    h += `<span class="dim" style="font-family:var(--body)">Risk</span>
          <span class="down bold">-${usd(m.naked_cost)}</span>`;
  }
  if (m.closes > 0) {
    h += `<span class="dim" style="font-family:var(--body)">Closed</span>
          <span class="${m.closed_pnl>=0?'up':'down'} bold">${m.closed_pnl>=0?'+':''}${usd(m.closed_pnl)}</span>`;
  }
  h += `</div>`;

  if (m.close_why) {
    h += `<div class="dim" style="font-size:11px;margin-top:8px;line-height:1.3;">${m.close_why}</div>`;
  }
  return h;
}

function sparkline(points){
  if(!points || points.length<2) return '';
  const w=300,ht=36,pad=2;
  const lo=Math.min(...points), hi=Math.max(...points), span=(hi-lo)||1;
  const step=(w-2*pad)/(points.length-1);
  const xy=(v,i)=>[pad+i*step, ht-pad-((v-lo)/span)*(ht-2*pad)];
  const d=points.map((v,i)=>{const [x,y]=xy(v,i); return `${i===0?'M':'L'}${x.toFixed(1)},${y.toFixed(1)}`;}).join(' ');
  const [lx,ly]=xy(points[points.length-1],points.length-1);
  return `<path d="${d}" fill="none" stroke="var(--proj)" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
          <circle cx="${lx}" cy="${ly}" r="2.5" fill="var(--proj)"/>`;
}

async function tick(){
  let s; try{ s=await (await fetch('/api/fleet',{cache:'no-store'})).json(); }
  catch(e){ return; }
  $('clock').textContent = s.run_started
    ? 'T+ ' + hms(s.now - s.run_started)
    : 'Not started';

  if(s.error){
    $('exp').textContent = s.error;
    $('exp').style.display = 'block';
    $('health').innerHTML = '<span class="alert-tx">● OFFLINE</span>';
    return;
  } else {
    $('exp').style.display = 'none';
  }

  const t=s.totals;
  const pe=t.pairs_ev||{};
  const activeCount = s.markets.filter(m => (m.income || 0) > 0).length;
  // Markets the fleet visited and could not read. `visit` now records the
  // failure on every early-return path, so this counts markets that are
  // actually broken rather than markets that merely have no data yet.
  const unreadable = s.markets.filter(m => m.err).length;
  const healthy = !t.fleet_stale;
  $('live').innerHTML = `<span class="${activeCount > 0 ? 'up' : 'down'}">● ${activeCount}/${t.markets} scoring</span>`;
  $('health').innerHTML = healthy
    ? '<span class="up">● LIVE</span>'
    : (t.state_age !== null
        ? `<span class="alert-tx">● STALE · ${hms(t.state_age)}</span>`
        : '<span class="alert-tx">● STALE · no heartbeat</span>');
  if(!healthy){
    // Name the market the loop died on when we know it. The old message said
    // only "stale", which read identically whether the process had crashed or
    // one venue was hanging -- two different problems with two different fixes.
    const at = t.loop_market ? ` Last market visited: ${t.loop_market}.` : '';
    const ageMsg = t.state_age !== null
      ? `Fleet heartbeat is stale (${hms(t.state_age)} old).`
      : 'Fleet heartbeat is missing (no state file write recorded).';
    $('exp').textContent = `${ageMsg} Displayed figures are historical, not live.${at}`;
    $('exp').style.display = 'block';
  } else if(t.sweep_age !== null && t.sweep_age > (t.stale_after_sec || 120)){
    // Loop alive, sweep genuinely slow. This now reads the duration the fleet
    // MEASURED, so it means what it says; the old version derived it from the
    // freshest per-market payload and reported 30m41s against a fleet that was
    // completing a sweep every 21 seconds.
    $('exp').textContent = `Fleet is live, but a full sweep is taking ${hms(t.sweep_age)}. Per-market figures lag by up to that much.`;
    $('exp').style.display = 'block';
  } else if(unreadable > 0){
    // THE CONDITION THAT USED TO MASQUERADE AS A SLOW SWEEP. The loop is fine
    // and the sweep is fast; some markets simply cannot be read, so their
    // figures are frozen at whenever they last answered. Naming the count and
    // the age of the stalest data points at the real fix -- the universe --
    // instead of at the loop.
    const dataMsg = t.data_age !== null ? ` Their figures are up to ${hms(t.data_age)} old.` : '';
    $('exp').textContent = `Fleet is live and sweeping in ${hms(t.sweep_age || 0)}, but ${unreadable}/${t.markets} markets are unreadable (closed, or the book will not load).${dataMsg}`;
    $('exp').style.display = 'block';
  }

  // ---- hero: the one number, its trend, and who's carrying it ----
  // TWO PROGRAMS PAY A MAKER, AND BOTH BELONG IN THIS TERM.
  //
  // Reward rent is money the venue owes for RESTING size. Maker rebates are a
  // share of the taker fee on volume we MADE. They are disjoint products, so
  // they add; and neither is inside `booked`, because a rebate arrives on top
  // of the fill rather than through its price.
  //
  // Spread "rent" is still NOT added: it projects income that arrives BY being
  // filled, and a fill is already in `booked` and `pairs held`. That is the
  // one line here that would double-count, which is why the split exists.
  //
  // Declared BEFORE the headline because the headline has to include it. It
  // did not, while the bridge directly below already summed it -- so the big
  // number and the "Total Liquidation P&L" under it disagreed by exactly the
  // rebate, on a page whose whole job is to make that arithmetic checkable.
  const rent = (t.rent_reward || 0) + (t.maker_rebate || 0);
  const liquidation = t.liquidate_now_pnl + rent;
  const hv=$('heroValue');
  hv.textContent = usd(liquidation);
  hv.className = 'hero-value ' + cls(liquidation);
  // FLOATING P&L IS A POSITION VALUE, NOT A MODEL. Unrealized P&L means what
  // the open book is worth against what it cost -- inventory float plus
  // unhedged float -- and that is exactly the middle of the liquidation
  // equation below. The modelled accrual that used to sit here is a
  // projection integrated over time; giving it a brokerage name for a live
  // position would put a forecast where a mark-to-market belongs. It keeps
  // its place in the income-rate tile, labelled as the benchmark it is.
  const floating = (t.locked_pair || 0) + (t.naked_exit || 0) - (t.at_risk || 0);
  const hi = $('heroIncome');
  hi.textContent = usd(floating);
  hi.className = 'hero-value ' + cls(floating);
  // THE ARITHMETIC, SPELLED OUT. The hero used to carry a prose description
  // of a formula while four tiles showed pieces of it under names that did
  // not match -- so $40 realized sitting above an $18.89 headline read as a
  // contradiction. Every term below is signed and they sum to the headline.
  const term=(label,v)=>`<span class="${cls(v)}">${v>=0?'+':'−'}${usd(Math.abs(v))}</span> ${label}`;
  // Naked cost and resale are one term now -- "Unhedged Float", the mark on
  // the unpaired leg -- because a reader tracking a brokerage statement wants
  // realized, inventory float and unhedged float, not the venue mechanics
  // underneath each.
  $('heroBridge').innerHTML =
    term('Realized', t.realized) + ' &nbsp;|&nbsp; ' +
    // Shown unconditionally now: a rebate line reading +$0.00 states that
    // nothing held pays a rebate, which is itself the fact worth knowing on a
    // fleet whose entire universe publishes clobRewards: 0. Hiding it left the
    // reader unable to tell "no rebate" from "rebates not counted".
    term('Earned Rebates', rent) + ' &nbsp;|&nbsp; ' +
    term('Paired Unrealized', t.locked_pair) + ' &nbsp;|&nbsp; ' +
    term('Unhedged Unrealized', t.naked_exit - t.at_risk) +
    ` &nbsp;=&nbsp; <b>${usd(liquidation)}</b> Total Liquidation P&L`;
  $('heroSpark').innerHTML = sparkline(s.share_history);

  // THE STORY, IN FIVE FACTS. Ordered as the questions actually get asked:
  // has it run long enough, is it trading, is being filled profitable, is the
  // model believable, and has anything settled to prove it.
  const edgePerFill = t.markout_n ? t.fill_edge / t.markout_n : null;
  const HS=(n,v,sub,cl,tip)=>`<div class="hs" ${tip?`title="${tip}"`:''}><div class="hs-n">${n}</div>
    <div class="hs-v ${cl||''}">${v}</div><div class="hs-s">${sub||''}</div></div>`;
  $('heroStrip').innerHTML =
    HS('Active Fills', String(t.fills),
       (t.verified && t.verified.ratio !== null)
         ? pct(t.verified.ratio)+' tape-verified' : 'no tape yet',
       t.fills ? 'up' : 'dim',
       'Fills recorded in current session') +
    HS('Markout Edge / fill', edgePerFill === null ? '—' : usd(edgePerFill),
       t.markout_n ? t.markout_n+' matured (15m+)'+(t.markout_n<20?' · need 20':'') : 'awaiting 15m horizon',
       edgePerFill === null ? 'dim' : cls(edgePerFill),
       'Average dollar gain/loss per trade 15 minutes after fill. If negative, filled orders dropped in value.') +
    HS('Avg Income Rate (Hold Rate)',
       t.income_twa_day === null ? '—' : usd(t.income_twa_day)+'/d',
       t.income_twa_day === null
         ? 'need 2 samples'
         : 'avg over '+t.income_hours.toFixed(1)+'h · spot now '+usd(t.income_day)+'/d',
       'proj',
       'Time-Weighted Average Yield: Solid average income rate held over time, filtering out temporary spikes from entering/exiting positions.') +
    HS('Settled P&L', String(t.settled),
       t.settled ? usd(t.realized)+' booked' : 'no ground truth yet',
       t.settled ? 'up' : 'dim',
       'Actual cash profit collected from resolved markets');

  const top = [...s.markets].sort((a,b)=>b.income-a.income).slice(0,6);
  const maxInc = Math.max(1e-9, ...top.map(m=>m.income||0));
  $('rankRows').innerHTML = top.map(m=>`
    <div class="rank-row">
      <span class="rank-name" title="${esc(m.title)}">${esc(m.title)}</span>
      <div class="rank-track"><div class="rank-fill" style="width:${Math.max(2,100*(m.income||0)/maxInc)}%"></div></div>
      <span class="rank-val mono ${m.income>0?'up':'dim'}">${usd(m.income)}</span>
    </div>`).join('') || '<span class="dim" style="font-size:12px">No markets reporting yet</span>';

  // ---- exposure gauge ----
  const budgetPct = t.wallet > 0 ? Math.min(100, 100*t.committed_total/t.wallet) : 0;
  const capPct = t.wallet > 0 ? Math.min(100, 100*t.max_committed/t.wallet) : 100;
  const budgetAlert = t.committed_total >= t.max_committed;
  const gf=$('gaugeFill');
  gf.style.width = budgetPct+'%';
  gf.style.background = budgetAlert ? 'var(--alert)' : (budgetPct>70?'var(--down)':'var(--up)');
  $('gaugeCap').style.left = capPct+'%';
  $('gaugeValue').innerHTML = `<span class="${budgetAlert?'alert-tx bold':'dim'}">${usd(t.committed_total,0)} / ${usd(t.wallet,0)}</span>`+
    (budgetAlert ? ` <span class="alert-tx">· ${usd(t.committed_overage,0)} over cap</span>` : ` · ${usd(t.available_cash,0)} available`);

  // ---- markout sample progress (n / gate threshold) ----
  // Counts fills whose 1h mark has landed, not every fill on the tape: the
  // gate reads h1, so anything earlier is a fill the gate cannot see yet.
  const gateN = t.matured_n || 0;
  const gateNeed = t.gate_min_sample || 25;
  const gateReady = gateN >= gateNeed;
  const gatePct = Math.min(100, 100 * gateN / gateNeed);
  const CELLS = 10, filled = Math.round(CELLS * gatePct / 100);
  $('sampleBlocks').innerHTML =
    `<span class="${gateReady?'up':'gold'}">${'█'.repeat(filled)}</span>` +
    `<span class="dim">${'░'.repeat(CELLS - filled)}</span>`;
  const sf = $('sampleFill');
  sf.style.width = gatePct + '%';
  sf.style.background = gateReady ? 'var(--up)' : 'var(--gold)';
  $('sampleValue').innerHTML =
    `<span class="${gateReady?'up bold':'gold bold'}">${gateN}/${gateNeed}</span>` +
    ` <span class="dim">(Matured Fills) · ${gatePct.toFixed(0)}%</span>`;
  $('sampleStrip').title = gateReady
    ? `Fleet markout gate is armed: ${gateN} fills have a matured 1h mark (threshold ${gateNeed}).`
    : `Fleet markout gate stays inactive until ${gateNeed} fills have a matured 1h mark. ${gateNeed - gateN} to go.`;

  // ---- go-live progress (n settled markets / 100 needed for a pilot call) ----
  const gl = s.go_live || {};
  const glN = gl.n_settled || 0;
  const glNeed = gl.go_live_min_settled || 100;
  const glReady = gl.status === 'READY_FOR_SMALL_LIVE_PILOT';
  const glSignal = glN >= (gl.signal_min_settled || 30);
  const glPct = Math.min(100, 100 * glN / glNeed);
  const glFilled = Math.round(CELLS * glPct / 100);
  $('readyBlocks').innerHTML =
    `<span class="${glReady?'up':(glSignal?'gold':'dim')}">${'█'.repeat(glFilled)}</span>` +
    `<span class="dim">${'░'.repeat(CELLS - glFilled)}</span>`;
  const rf = $('readyFill');
  rf.style.width = glPct + '%';
  rf.style.background = glReady ? 'var(--up)' : (glSignal ? 'var(--gold)' : 'var(--tx-dim)');
  const glLabel = {
    NO_DATA: 'no settled markets', COLLECTING: 'collecting',
    DIRECTIONAL_SIGNAL: 'directional signal only',
    READY_FOR_SMALL_LIVE_PILOT: 'ready for small live pilot',
  }[gl.status] || (gl.status || '').toLowerCase();
  $('readyValue').innerHTML =
    `<span class="${glReady?'up bold':(glSignal?'gold bold':'dim bold')}">${glN}/${glNeed}</span>` +
    ` <span class="dim">(Settled Markets) · ${glLabel}</span>`;
  $('readyStrip').title = glReady
    ? `Ready for a small live pilot: ${glN} settled markets, 90% CI lower bound ${(gl.ci90_lower_pct||0).toFixed(1)}% (above zero), ${(gl.calendar_days||0).toFixed(1)} calendar days, largest category ${((gl.max_category_share||0)*100).toFixed(0)}% of sample.`
    : `Needs ${glNeed} settled markets (has ${glN}), a positive 90% CI lower bound, ${gl.go_live_min_calendar_days}+ calendar days, and no single sport over ${((gl.go_live_max_category_share||0)*100).toFixed(0)}% of the sample before a small live pilot is warranted.`;

  const K=(n,v,sub,cl,isAlert,tip)=>`<div class="k ${isAlert?'alert':''}" ${tip?`title="${tip}"`:''}><div class="n">${n}</div>
    <div class="v ${cl||''}">${v}</div><div class="s">${sub||''}</div></div>`;
  const renderGroup = (title, tiles) => `<div><div class="kpi-hdr">${title}</div><div class="kpi-group">${tiles.join('')}</div></div>`;

  const totalFloating = (t.locked_pair || 0) + (t.naked_exit || 0) - (t.at_risk || 0);

  const t_pl = [
    K('Spot Daily Yield',usd(t.income_day)+'/day',
      'Spot rate right now (' + t.return_pct_day.toFixed(1) + '%/day on cap)',
      'proj', false,
      'Instantaneous earning rate at this exact second based on active quote placement'),
    K('15-Min Markout Edge ($)',usd(t.fill_edge),
      t.markout_n ? usd(t.markout_spread)+' Discount Bought + '+usd(t.markout_total)+' 15m Price Change · '+t.markout_n+' fills'
                  : 'no completed trades evaluated yet',
      t.markout_n ? cls(t.fill_edge) : 'dim', false,
      'Post-Trade Quality Check: Total value change 15m after buying. (Discount captured below market price) + (Market price movement after 15m). NOT overall portfolio P&L.'),
    K('Realized P&L',usd(t.realized),
      (t.closes?t.closes+' closed trade'+(t.closes===1?'':'s'):'no closed trades')
        +' · '+(t.settled?t.settled+' settled':'$0.00 settled'),
      (t.settled||t.closes)?cls(t.realized):'dim', false,
      'Actual cash profit booked and finalized from closed or settled positions'),
    K('Target vs Actual Discount',
      (t.markout_n
        ? usd(t.markout_spread)+' – '+usd(t.rent_modelled_spread + t.rent_reward)
        : usd(t.rent_modelled_spread + t.rent_reward)),
      (t.markout_n
        ? 'Baseline: Discount Secured | Target: Model Target'
        : 'Target: Model Target · no evaluated fills yet'),
      'proj', false,
      'Compares actual discount captured on filled orders against theoretical model target'),
    K('Dividend / Fee Rebates',t.maker_rebate_err ? '—' : usd(rent),
      t.maker_rebate_err
        ? 'rebate read failed · '+t.maker_rebate_err
        : rent > 0
        ? [t.rent_reward > 0 ? usd(t.rent_reward)+' emissions' : null,
           t.maker_rebate > 0
             ? usd(t.maker_rebate)+' rebates on '+(t.maker_rebate_shares||0).toFixed(0)+' filled sh'
             : null].filter(Boolean).join(' · ')+' · unpaid, in headline'
        : 'no emissions funded · no maker fills yet',
      t.maker_rebate_err ? 'alert-tx' : (rent > 0 ? 'gold' : 'dim'), false,
      'Fee rebates paid back by exchange for making liquidity'),
    K('Hold-Weighted Yield',
      t.income_twa_day === null ? '—' : usd(t.income_twa_day)+'/day',
      t.income_twa_day === null ? 'need 2 samples' : (t.income_hours !== null ? t.income_hours+'h time-weighted avg hold rate' : '15h time-weighted avg hold rate'),
      'proj', false,
      'Solid average daily yield sustained over time, smoothing out position enter/exit noise'),
    K('Pairs EV / one-sided fill', pe.ev_cents === null ? '—' : pe.ev_cents.toFixed(1)+'¢',
      pe.one_sided
        ? pe.completions+' completed · '+pe.exits+' exited · '+pe.expired+' rode out · '+pe.one_sided+' fills'
        : 'no one-sided fills yet',
      pe.ev_cents === null ? 'dim' : cls(pe.ev_cents), false,
      "The pairs-only rule's measured EV per one-sided fill: completion rate × "+pe.complete_gain_cents+'¢ (merge capture) − exit rate × '+pe.exit_cost_cents+'¢ (half-spread). Positive means completing pairs instead of holding naked legs into the drift pays.')
  ];

  // ---- go-live readiness: is this trustworthy enough for real dollars ----
  // Two tiers. MACHINERY (pooled size-weighted markout, same statistic that
  // drives the fleet HALTED gate) reaches a real sample in hours. MONEY (true
  // settled P&L per market -- closes plus the $1/$0 the venue paid on
  // whatever was still held, NOT closes alone, which is biased toward
  // successful hedge-merges) needs far more settled markets because the
  // payoff is small-frequent-capture against a rare-large-binary-tail. See
  // `go_live_readiness` in strategy/stats.py for the reasoning behind the
  // thresholds below. `gl` itself is declared above, next to the progress bar.
  const glStatusLabel = {
    NO_DATA: 'No settled markets yet', COLLECTING: 'Collecting data',
    DIRECTIONAL_SIGNAL: 'Directional signal only',
    READY_FOR_SMALL_LIVE_PILOT: 'Ready for small live pilot',
  }[gl.status] || gl.status || '-';
  const glStatusCls = {
    NO_DATA: 'dim', COLLECTING: 'dim', DIRECTIONAL_SIGNAL: 'gold',
    READY_FOR_SMALL_LIVE_PILOT: 'up',
  }[gl.status] || 'dim';
  const t_ready = [
    K('Go-Live Status', glStatusLabel,
      (gl.n_settled||0)+' settled markets seen', glStatusCls, false,
      'Composite readiness read: needs '+gl.go_live_min_settled+'+ settled markets, a positive 90% confidence lower bound on mean return, '+gl.go_live_min_calendar_days+'+ calendar days, and no single sport/category over '+pct(gl.go_live_max_category_share)+' of the sample'),
    K('Settled Sample (Money)', (gl.n_settled||0)+' / '+gl.go_live_min_settled,
      (gl.n_settled||0) >= (gl.signal_min_settled||0)
        ? 'past the '+gl.signal_min_settled+'-market noise floor'
        : 'below the '+gl.signal_min_settled+'-market noise floor',
      (gl.n_settled||0) >= (gl.go_live_min_settled||1e9) ? 'up'
        : (gl.n_settled||0) >= (gl.signal_min_settled||1e9) ? 'gold' : 'dim',
      false,
      'Fully-resolved markets, TRUE settled P&L (closes + venue $1/$0 payout on anything still held) -- not just voluntary closes, which are biased toward profitable hedge-merges'),
    K('Mean Return / Trade', gl.mean_return_pct==null?'-':gl.mean_return_pct.toFixed(1)+'%',
      gl.stdev_return_pct==null?'stdev unknown':'stdev '+gl.stdev_return_pct.toFixed(1)+'%',
      gl.mean_return_pct==null?'dim':cls(gl.mean_return_pct), false,
      'Mean realized P&L as a percent of cost basis, across all TRUE settled markets'),
    K('90% CI Lower Bound', gl.ci90_lower_pct==null?'-':gl.ci90_lower_pct.toFixed(1)+'%',
      gl.ci90_lower_pct==null ? 'need 2+ settled markets'
        : (gl.ci90_lower_pct>0 ? 'distinguishable from breakeven' : 'not yet distinguishable from breakeven'),
      gl.ci90_lower_pct==null?'dim':cls(gl.ci90_lower_pct), false,
      'One-sided 90% confidence lower bound on mean return; the actual go/no-go threshold is whether this stays above zero, not the point estimate'),
    K('Markout Effective Sample', (gl.markout_n_eff||0).toFixed(0),
      'size-weighted (Kish n_eff), fleet-pooled', (gl.markout_n_eff||0)>=30?'up':'dim', false,
      'Same pooled size-weighted markout statistic the HALTED gate reads -- a fast, low-variance leading indicator of whether the entry/risk rules are behaving as designed, well before enough markets have resolved for the money read'),
    K('Calendar Coverage', gl.calendar_days==null?'-':gl.calendar_days.toFixed(1)+'d',
      'need '+gl.go_live_min_calendar_days+'d+ to cross multiple days/regimes',
      gl.calendar_days>=gl.go_live_min_calendar_days?'up':'dim', false,
      'Span between the earliest and latest fill; a short window can look good or bad purely from one event cluster'),
    K('Category Concentration', gl.max_category_share==null?'-':pct(gl.max_category_share),
      Object.entries(gl.category_counts||{}).map(([k,v])=>k+':'+v).join(' · ') || 'no settled markets',
      gl.max_category_share==null?'dim':(gl.max_category_share<=gl.go_live_max_category_share?'up':'down'), false,
      'Largest single sport/category share of the settled sample; a sample that is all one sport is not diversification even if the mean looks good')
  ];

  const t_risk = [
    K('Capital Deployed',usd(t.committed_total,0),`of ${usd(t.wallet,0)} simulated wallet`,t.committed_total>=t.max_committed?'alert-tx':'dim',t.committed_total>=t.max_committed,
      'Total capital currently locked in open bids and active inventory'),    K('Fleet Naked Risk',usd(t.at_risk,0)+' / '+usd(t.fleet_naked_budget,0),
      Math.min(100,100*t.at_risk/Math.max(1,t.fleet_naked_budget)).toFixed(0)+'% of hard naked-risk budget',
      t.at_risk >= t.fleet_naked_budget ? 'alert-tx' : (t.at_risk > .8*t.fleet_naked_budget ? 'down' : 'up'),
      t.at_risk >= t.fleet_naked_budget,
      'Naked cost at risk, using the same cost basis the dollar risk gate enforces'),
    K('Unhedged Exit Value',usd(t.naked_exit,0),'current bid value if liquidated now','down', false,
       'Current resale value of single-sided positions; not the risk-gate cost basis'),

    K('Resolution Floor (Worst Case)',usd(t.net_worst),
      'P&L if all unhedged positions expire to $0',cls(t.net_worst), false,
      'Final portfolio profit/loss floor if all unhedged bets expire to $0.00 at resolution, keeping past realized gains.'),
    K('Total Floating Unrealized P&L',usd(totalFloating),
      usd(t.locked_pair)+' paired float + '+usd(t.naked_exit - t.at_risk)+' unhedged float',cls(totalFloating), false,
      'Current paper profit/loss on all open positions if sold right now at market price'),
    K('Matured Trades (15m+)',String(t.markout_n),'fills with 15m+ history (need 20+)',t.markout_n>=20?'up':'dim', false,
      'Number of completed trades older than 15 minutes used for trade quality stats.'),
    K('Markets Exited',String(t.exited),'stopped for adverse selection',t.exited?'down':'up',t.exited>0,
      'Markets auto-stopped due to severe post-trade price drops')
  ];

  const t_cap = [
    // Unallocated cash only -- deliberately NOT buying power, which would
    // also count collateral released by cancelling open orders.
    K('Net Available Cash',usd(t.available_cash,0),'unallocated of '+usd(t.wallet,0)+' wallet','up'),
    K('Open Orders Collateral',usd(t.capital,0),'cash held against resting bids','gold'),
    K('Active Traded Bets',t.scoring+' / '+t.markets,
      t.markets_spread ? t.markets_spread+' priced on spread capture' : 'positive projected income',
      t.scoring?'up':'dim'),
    K('Scoring uptime',pct(t.uptime),'checks with non-zero score',thresh(t.uptime,true,0.8)),
    K('Fills',String(t.fills),'tape-confirmed + crossed','dim'),
    K('Max Allowed Unrealized Loss',usd(t.fleet_naked_budget,0),'hard unhedged-loss ceiling','dim'),
    K('Active Quoting Markets',String(t.active_quoting||0)+' / '+String(t.markets||0),
      t.book_health_ratio == null ? 'no markets' : pct(t.book_health_ratio)+' actively quoting',
      t.book_health_ratio == null ? 'dim' : thresh(t.book_health_ratio,true,.8), false,
      'Markets with active resting quotes divided by monitored markets; this is not a two-sided book-depth test'),
    K('Gate Refusals',String(Object.values(t.gate_refusals||{}).reduce((a,b)=>a+b,0)),
      Object.entries(t.gate_refusals||{}).map(([k,v])=>k+': '+v).join(' · ') || 'none recorded',
      Object.keys(t.gate_refusals||{}).length ? 'down' : 'dim', false,
      'Structured refusal events from the risk and budget gates'),
    K('Data health',healthy?'LIVE':'STALE',healthy?'heartbeat < 120s':'do not trust live figures',healthy?'up':'alert-tx',!healthy)
  ];

  $('agg').innerHTML = renderGroup('Profit &amp; loss', t_pl) + renderGroup('Go-live readiness', t_ready) + renderGroup('Risk &amp; exposure', t_risk) + renderGroup('Capital &amp; operations', t_cap);

  $('rows').innerHTML=s.markets.map(m=>{
    const currentIncome = (m.income || 0);
    const isGenerating = currentIncome > 0;
    const position = orderDepth(m);
    const events = m.events || [];
    const latest = events[0];
    const actionClass = latest ? ({FILLED:'up',QUOTING:'proj',HEDGED:'gold',MERGED:'up',EXITED:'down',BLOCKED:'down',ERROR:'alert-tx',WAITING:'dim',PAIR_COMPLETE:'up',NAKED_EXIT:'gold',PAIR_WINDOW_EXPIRED:'down'}[latest.kind] || 'dim') : 'dim';
    const actionHtml = latest
      ? `<div class="action-cell" title="${esc(latest.reason)}"><span class="action-pill ${actionClass}" style="background:rgba(255,255,255,.06)">${esc(latest.kind)}</span><span class="dim" style="font-size:10px;margin-left:6px">${hms(Math.max(0,s.now-latest.ts))} ago</span><div class="action-recent"><div class="event-line">${esc(latest.reason || latest.reason_code)}</div>${events.slice(1,3).map(e=>`<div class="event-line dim">${esc(e.kind)} · ${esc(e.reason || e.reason_code)}</div>`).join('')}</div></div>`
      : `<span class="dim">No event telemetry</span>`;
    const unrealized = m.unrealized_pnl || 0;
    const hasPos = (m.paired > 0) || (m.naked_sh > 0);
    const unrlHtml = hasPos
      ? `<span class="${cls(unrealized)} bold mono">${usd(unrealized)}</span>`
      : `<span class="dim">-</span>`;
    const rzlHtml = m.closes
      ? `<span class="${cls(m.closed_pnl)} bold mono">${usd(m.closed_pnl)}</span>`
      : `<span class="dim">-</span>`;
    const statusHtml = m.err
      ? `<span class="down bold">STALE / ERROR</span>`
      : `<span class="${isGenerating?'up':'dim'} bold">${m.source === 'spread' ? 'SPREAD' : 'SCORING'}</span><div class="dim" style="font-size:10px;margin-top:3px">${m.age==null?'no live data':hms(m.age)+' old'}</div>`;
    return `<tr class="${m.gate === 'EXITED' ? 'alert' : ''}">
      <td style="max-width:300px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;" title="${esc(m.title)}">${m.url?`<a class="mkt-link" href="${esc(m.url)}" target="_blank">${esc(m.title)}</a>`:esc(m.title)}</td>
      <td>${actionHtml}</td>
      <td class="num bold mono ${isGenerating ? 'up' : 'dim'}" style="font-size:15px">${usd(currentIncome)}</td>
      <td class="num mono" title="Offers ${usd(m.capital,0)}">${usd(m.committed,0)}</td>
      <td>${position}</td>
      <td class="num mono">${unrlHtml}</td>
      <td class="num mono">${rzlHtml}</td>
      <td class="num mono">${pct(m.share,1)}</td>
      <td class="num mono ${thresh(m.uptime,true,0.8)}">${pct(m.uptime,0)}</td>
      <td class="num mono dim">${m.fills}</td>
      <td>${statusHtml}</td>
    </tr>`;  }).join('');

  const settled = s.settled_positions || [];
  $('settledRows').innerHTML = settled.length ? settled.map(p=>{
    const exitTime = new Date(p.ts * 1000).toLocaleString();
    const pnlClass = cls(p.realized_pnl);
    const detail = p.method === 'SELL'
      ? `YES ${p.yes_exit == null ? '-' : Number(p.yes_exit).toFixed(4)} · NO ${p.no_exit == null ? '-' : Number(p.no_exit).toFixed(4)}`
      : p.method === 'RESOLVE'
      ? `Venue resolution payout, blended ${usd(p.exit_price)}/sh (winning shares pay $1, losing pay $0)`
      : 'Parity redemption at $1.0000';
    const feeLabel = p.fee_or_gas == null ? '-' : usd(p.fee_or_gas);
    const pillCls = p.method === 'MERGE' ? 'up' : p.method === 'RESOLVE' ? 'gold' : 'proj';
    return `<tr>
      <td class="mono dim" style="white-space:nowrap">${esc(exitTime)}</td>
      <td style="max-width:280px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${esc(p.market_slug)}">${esc(p.market_slug || p.condition_id)}</td>
      <td><span class="action-pill ${pillCls}" style="background:rgba(255,255,255,.06)">${esc(p.method)}</span></td>
      <td class="num mono">${Number(p.shares || 0).toFixed(2)}</td>
      <td class="num mono">${usd(p.avg_cost)}</td>
      <td class="num mono">${usd(p.exit_price)}</td>
      <td class="num mono ${p.pnl_pct == null ? 'dim' : pnlClass}">${p.pnl_pct == null ? '-' : (p.pnl_pct >= 0 ? '+' : '') + Number(p.pnl_pct).toFixed(2) + '%'}</td>
      <td class="num mono ${pnlClass} bold">${usd(p.realized_pnl)}</td>
      <td class="num mono dim">${feeLabel}</td>
      <td class="dim" title="${esc(detail)}">${esc(detail)}</td>
    </tr>`;
  }).join('') : '<tr><td colspan="10" class="settled-empty">No realized exits yet — closed positions will appear here.</td></tr>';
}
// ---------- view switcher: fleet page <-> market pipeline ----------
const viewShow=(name)=>{
  $('view-fleet').hidden=(name!=='fleet');
  $('view-pipeline').hidden=(name!=='scan');
  $('viewFleet').classList.toggle('active',name==='fleet');
  $('viewScan').classList.toggle('active',name==='scan');
  history.replaceState(null,'','?view='+name);
};
// The scan view is the evening watch; a reload should not kick the operator
// back to the fleet page. Re-apply the ?view= param at boot.
const viewFromUrl=new URLSearchParams(location.search).get('view');
$('viewFleet').addEventListener('click',()=>viewShow('fleet'));
$('viewScan').addEventListener('click',()=>viewShow('scan'));
if(viewFromUrl==='scan'){viewShow('scan');}

// ---------- market pipeline view: the selection funnel, live ----------
// Four lanes mirror the ranker's actual funnel -- RAW (what the venue
// lists) -> FILTERS (the selector gates, bucketed by refusal cause) ->
// FINAL (eligible, ranked by return per dollar) -> GRADUATED (what the
// fleet adopted). Data comes from run/pipeline.json, which
// scripts/rank_markets.py rewrites every rank.
function pipeLane(title,sub,count,countCls,body){
  return `<div class="pipe-lane-hdr"><div><h3>${title}</h3><div class="dim" style="font-size:10px;margin-top:3px">${sub}</div></div><span class="pipe-count ${countCls||''}">${count}</span></div><div class="pipe-lane-body">${body}</div>`;
}
function pipeEmpty(txt){return `<div class="pipe-empty">${txt}</div>`;}
function pipeChip(t,sub){
  return `<div class="chip"><div class="chip-t">${esc(t)}</div><div class="chip-s">${sub}</div></div>`;
}
function gateCard(g){
  // `e.marg` is the ranker's estimate of what the allocator would have said
  // had this rejected market been adopted -- first-dollar marginal %/day on
  // the venue's own score reading, the same math the GRADUATED lane shows
  // for a refused market. Absent for identity rejects (no book was fetched).
  const ex=(g.examples||[]).map(e=>{
    const m=e.marg;
    const margHtml=m?`<div class="gate-marg ${m.trap?'trap':m.would_fund?'up':'down'}" title="${esc(m.reason)} · pot $${m.pot_day}/day · competition ${Number(m.competition).toLocaleString()} · floor ${m.threshold_pct}%/day">if adopted: ~${m.marg_pct_day}%/day · ${m.trap?'EMPTY-BOOK MIRAGE — nobody resting in the reward window, the estimate is not real':m.would_fund?'would clear the floor':'below floor'}</div>`:'';
    return `<div class="gate-ex" title="${esc(e.reason)}"><div class="gate-ex-t">${esc(e.title)}<span class="r"> — ${esc((e.reason||'').slice(0,46))}</span></div>${margHtml}</div>`;
  }).join('');
  // First-dollar admission is not a funded quote -- the allocator can still
  // drop a market at its min-lot payout check -- so the headline says "clear
  // the floor", the claim that is actually being made. would_fund counts
  // CREDIBLE near-misses only: an empty-book mirage (pot divided by ~zero
  // competition) is evidence of nothing, so it is shown separately in amber,
  // not counted as a green.
  const near=(g.would_fund||0)>0?`<div class="gate-near up">${g.would_fund} of ${g.n} rejected here would clear the 2%/day floor</div>`:'';
  const traps=(g.traps||0)>0?`<div class="gate-near trap">${g.traps} empty-book mirages here — the estimate divides by ~zero competition and is not real</div>`:'';
  return `<div class="gate-card"><div class="gate-hdr"><span class="gate-name">${esc(g.cause||'other')}</span><span class="gate-n">${g.n||0}</span></div>${near}${traps}${ex?`<div class="gate-exs">${ex}</div>`:''}</div>`;
}
function mktCard(m){
  const src=m.source==='spread';
  return `<div class="mkt-card"><div class="mkt-top"><span class="mkt-t" title="${esc(m.title)}">${esc(m.title)}</span><span class="pill ${src?'pill-spr':'pill-rew'}">${src?'SPREAD':'REWARD'}</span></div><div class="mkt-mid"><span class="proj bold mono">${usd(m.income)}/d</span><span class="dim mono">${usd(m.capital,0)} cap</span><span class="dim mono">${m.ret_day_pct==null?'-':m.ret_day_pct.toFixed(2)+'%/d'}</span></div><div class="mkt-sub dim mono">${m.volume==null?'vol ?':'$'+(m.volume/1000).toFixed(0)+'K vol'} · ${m.days==null?'horizon ?':m.days.toFixed(1)+'d'}</div></div>`;
}
function gradCard(m){
  const st=m.live?(m.err?'ERR':'LIVE'):'QUEUED';
  const pillCls=m.live?(m.err?'pill-spr':'pill-live'):'pill-wait';
  const a=m.alloc;
  const allocHtml=a?`<div class="alloc-line"><div class="${a.funded?'up':'down'}" title="first dollar ${a.first_marginal_pct}%/day · pot $${a.pot_day}/day · allocated $${a.dollars}">${esc(a.reason)}</div><div class="alloc-nums dim mono">marginal ${a.marginal_pct}%/day · competition ${Number(a.competition_avg).toLocaleString()} · floor ${a.threshold_pct}%/day</div></div>`:`<div class="alloc-line dim">allocator: no verdict yet</div>`;
  // WHY THIS MARKET ISN'T QUOTING. A funded market that rests nothing reads
  // as "the bot does nothing" unless the named refusal is on the card: the
  // live book-gate error (depth/spread), the allocator's funding verdict, or
  // the requote's blocked reason. All three are now on the same card.
  const reasonHtml=(m.err_text||m.why||(!m.live?'not adopted yet':''))
    ?`<div class="alloc-line" style="border-top-color:rgba(240,104,77,.35)"><div class="down" title="live gate refusal">${esc(m.err_text||m.why||(!m.live?'queued — not adopted yet':''))}</div></div>`
    :(m.income<=0?'<div class="alloc-line dim">resting nothing — allocator has not funded this market</div>':'');
  return `<div class="mkt-card"><div class="mkt-top"><span class="mkt-t" title="${esc(m.title)}">${esc(m.title)}</span><span class="pill ${pillCls}">${st}</span></div><div class="mkt-mid"><span class="proj bold mono">${usd(m.income)}/d</span><span class="dim mono">${usd(m.capital,0)} cap</span><span class="dim mono">${pct(m.share,1)} share</span></div><div class="mkt-sub dim mono">${m.fills||0} fills · ${pct(m.uptime,0)} uptime</div>${allocHtml}${reasonHtml}</div>`;
}
function pipeStrip(s,snap){
  if(!snap){
    const fleetTxt=s.fleet_alive===true?' The fleet is alive.':s.fleet_alive===false?' The fleet is down.':'';
    return pipeEmpty('No pipeline snapshot yet — the ranker writes <span class="mono">run/pipeline.json</span> on its next pass (every 10 min).'+fleetTxt+(s.picked?' '+s.picked+' market(s) adopted':''));
  }
  const c=snap.counts||{};
  const age=Math.max(0,s.snapshot_age||0);
  const stale=age>900;
  const chain=`<span class="up">RAW ${(c.funded||0)+(c.spread_universe||0)}</span><span>→</span><span>scored ${c.scored||0}</span><span>→</span><span class="down">rejected ${c.rejected||0}</span><span>→</span><span class="proj">eligible ${c.eligible||0}</span><span>→</span><span class="up bold">picked ${c.picked||0}</span>`;
  return `<div class="pipe-census">${esc(snap.census||'')} <span class="${stale?'down':'dim'}">· snapshot ${hms(age)} old</span></div><div class="pipe-chain">${chain}</div><div class="pipe-gates">${esc((snap.gates||'').trim())}</div>`;
}
function pipeNearMiss(nm){
  if(!nm) return '';
  const st=nm.status;
  const badge=st==='READY_TO_TRIAL'?'<span class="pill pill-live">READY TO TRIAL</span>'
    :st==='COLLECTING'?'<span class="pill pill-wait">COLLECTING</span>'
    :'<span class="pill pill-wait">NO DATA</span>';
  const tile=(label,val,cls)=>`<span class="pn-tile"><span class="pn-tl">${label}</span><span class="pn-tv ${cls||''}">${val}</span></span>`;
  const causes=Object.entries(nm.top_causes||{}).slice(0,3).map(([k,v])=>`${esc(k)} ${v}`).join(' · ');
  const note=st==='READY_TO_TRIAL'
    ?`<span class="up bold">Enough consistent evidence — the next step is a controlled trial (adopt the small-margin greens, watch markouts), not an immediate gate change.</span>`
    :(st==='COLLECTING'&&nm.greens===0&&nm.traps>0
      ?`<span class="dim">Only empty-book mirages so far (${nm.traps} excluded) — no credible near-miss yet, so the bars stay at zero. Not a malfunction; real candidates will move them.</span>`
      :`<span class="dim">Logging the green near-misses the floor would fund but the gates refuse — the estimate is single-snapshot and optimistic, so this validates it is CONSISTENT over time; a trial measures whether it actually pays.</span>`);
  return `<div class="pipe-near-hdr"><span class="pipe-near-t">NEAR-MISS TRACKER</span>${badge}</div>`+
    `<div class="pipe-near-body">`+
    tile('days',`${nm.days}/${nm.min_days}`,nm.days>=nm.min_days?'up':'proj')+
    tile('unique markets',`${nm.unique_markets}/${nm.min_unique}`,nm.unique_markets>=nm.min_unique?'up':'proj')+
    tile('small-margin depth',`${nm.small_margin_depth}/${nm.min_small_margin}`,nm.small_margin_depth>=nm.min_small_margin?'up':'proj')+
    (nm.depth_unparsed?`<span class="pn-tile"><span class="pn-tl">unparsed depth reasons</span><span class="pn-tv down">${nm.depth_unparsed}</span></span>`:'')+
    tile('stability (72 ranks)',`${Math.round(100*nm.stability)}%`,nm.stability>=nm.min_stability?'up':'proj')+
    tile('pot on the table',`$${nm.uniq_pot_day}/d`,'')+
    (nm.pot_traps?`<span class="pn-tile"><span class="pn-tl">excl. traps</span><span class="pn-tv dim">${nm.pot_traps} ($${Math.round((nm.raw_uniq_pot_day||0)-(nm.uniq_pot_day||0))}/d)</span></span>`:'')+
    (nm.traps?`<span class="pn-tile"><span class="pn-tl">mirages seen</span><span class="pn-tv down">${nm.traps} (not counted)</span></span>`:'')+
    tile('ranks logged',`${nm.ranks}`,'')+
    `</div>`+
    (causes?`<div class="pipe-near-sub dim mono">credible green by gate: ${causes}</div>`:'')+
    `<div class="pipe-near-note">${note}</div>`;
}
function pipeVolumeNearMiss(nm){
  if(!nm) return '';
  const st=nm.status;
  const badge=st==='READY_TO_TRIAL'?'<span class="pill pill-live">READY TO TRIAL</span>'
    :st==='COLLECTING'?'<span class="pill pill-wait">COLLECTING</span>'
    :'<span class="pill pill-wait">NO DATA</span>';
  const tile=(label,val,cls)=>`<span class="pn-tile"><span class="pn-tl">${label}</span><span class="pn-tv ${cls||''}">${val}</span></span>`;
  const half=Math.round((nm.half_bar_usd||0)/1000);
  const bar=Math.round((nm.volume_bar||250000)/1000);
  const closest=(nm.closest||[]).slice(0,3).map(m=>{
    const t=(m.title||'').slice(0,44);
    return `<div class="gate-ex" title="${esc(t)} · ${esc(m.slug||'')}"><div class="gate-ex-t">${esc(t)}<span class="r"> — ${(100*(m.ratio||0)).toFixed(1)}% of bar · $${Math.round((m.volume||0)/1000)}K/24h${m.pot_day?' · pot $'+Math.round(m.pot_day)+'/d':''}</span></div></div>`;
  }).join('');
  const note=st==='READY_TO_TRIAL'
    ?`<span class="up bold">Enough consistent evidence — the next step is a controlled volume trial (loosen to half the bar, watch markouts), not an immediate gate change.</span>`
    :(st==='NO_DATA'
      ?`<span class="dim">No volume-reject log yet — the ranker writes <span class="mono">run/volume_near_misses.jsonl</span> from the next rank after it runs the updated code.</span>`
      :`<span class="dim">Watching every market the $${bar}k/24h gate refuses. A small-margin market (measured volume ≥ $${half}k) is the concrete candidate a loosening would admit — U33 showed most rejects are 1-3 orders of magnitude under the bar, so a low count is the honest read, not a malfunction.</span>`);
  return `<div class="pipe-near-hdr"><span class="pipe-near-t">VOLUME NEAR-MISS TRACKER</span>${badge}</div>`+
    `<div class="pipe-near-body">`+
    tile('watched',`${nm.watched||0}`,nm.watched?'':'dim')+
    tile('days',`${nm.days||0}/${nm.min_days}`,(nm.days||0)>=nm.min_days?'up':'proj')+
    tile('unique markets',`${nm.unique_markets||0}/${nm.min_unique}`,(nm.unique_markets||0)>=nm.min_unique?'up':'proj')+
    tile(`≥ half bar (≥$${half}k)`,`${nm.small_margin_volume||0}/${nm.min_small_margin}`,(nm.small_margin_volume||0)>=nm.min_small_margin?'up':'proj')+
    tile('stability (72 ranks)',`${Math.round(100*(nm.stability||0))}%`,(nm.stability||0)>=nm.min_stability?'up':'proj')+
    tile('pot on the table',`$${nm.uniq_pot_day||0}/d`,'')+
    ((nm.gaps||nm.volume_unknown_total)?`<span class="pn-tile"><span class="pn-tl">unmeasured</span><span class="pn-tv dim">${(nm.gaps||0)+(nm.volume_unknown_total||0)} (not counted)</span></span>`:'')+
    tile('ranks logged',`${nm.ranks||0}`,'')+
    `</div>`+
    (closest?`<div class="pipe-near-sub dim">closest to the bar now (last reading — rank-time snapshot, not today's book):</div><div class="gate-exs">${closest}</div>`:'')+
    `<div class="pipe-near-note">${note}</div>`;
}
function pipeRaw(snap){
  const c=snap.counts||{};
  const raw=snap.raw||{};
  const rew=(raw.rewards||[]).map(m=>pipeChip(m.title,'$'+Number(m.rate||0).toFixed(2)+'/day · '+(m.days==null?'?':m.days.toFixed(1))+'d')).join('');
  const spr=(raw.spread||[]).map(m=>pipeChip(m.title,'$'+(Number(m.volume||0)/1000).toFixed(0)+'K vol · sp '+(m.spread==null?'?':m.spread)+' · '+(m.days==null?'?':m.days.toFixed(1))+'d')).join('');
  return pipeLane('① RAW — what the venue lists','sampling-markets reward pool + gamma liquid pool',(c.funded||0)+(c.spread_universe||0),'',
    `<div class="pipe-group"><div class="pipe-group-hdr">Reward-funded (${c.funded||0}) · top by rate shown</div>${rew||pipeEmpty('no funded reward markets listed')}</div><div class="pipe-group"><div class="pipe-group-hdr">Unfunded liquid (${c.spread_universe||0}) · by 24h volume</div>${spr||pipeEmpty('no unfunded markets cleared the listing scan')}</div>`);
}
function pipeFilter(snap){
  const c=snap.counts||{};
  const cards=(snap.rejections||[]).map(gateCard).join('');
  const nb=c.dropped_no_verdict||0;
  const nbCard=nb>0?gateCard({cause:'no usable book',n:nb,examples:[{title:'dropped inside scoring',reason:'book fetch failed / one-sided / mid out of band / would overbid'}]}):'';
  return pipeLane('② FILTERS — who is refused, and why','identity · depth · spread · volume · horizon · income',c.rejected||0,'down',cards+nbCard||pipeEmpty('nothing rejected on the last rank'));
}
function pipeFinal(snap){
  const fin=snap.final||[];
  const body=fin.length?fin.map(mktCard).join(''):pipeEmpty('nothing cleared every gate on the last rank — the bars are doing their job');
  return pipeLane('③ FINAL STAGE — eligible, ranked','passed every gate · return per $ of capital',fin.length,'proj',body);
}
function pipeGrad(s){
  const g=s.graduated||[];
  const body=g.length?g.map(gradCard).join(''):pipeEmpty('fleet has adopted no markets yet');
  return pipeLane('④ GRADUATED — the fleet\'s universe','from run/markets.json · annotated with live fleet state',(s.live||0)+'/'+(s.picked||0),'up',body);
}
async function tickPipeline(){
  let s; try{ s=await (await fetch('/api/pipeline',{cache:'no-store'})).json(); }catch(e){ return; }
  const snap=s.snapshot||null;
  $('pipeStrip').innerHTML=pipeStrip(s,snap);
  $('nearMiss').innerHTML=pipeNearMiss(s.near_miss||null);
  $('volumeNearMiss').innerHTML=pipeVolumeNearMiss(s.volume_near_miss||null);
  $('laneRaw').innerHTML=snap?pipeRaw(snap):pipeLane('① RAW','','-','',pipeEmpty('waiting for the next rank…'));
  $('laneFilter').innerHTML=snap?pipeFilter(snap):pipeLane('② FILTERS','','-','',pipeEmpty('waiting for the next rank…'));
  $('laneFinal').innerHTML=snap?pipeFinal(snap):pipeLane('③ FINAL STAGE','','-','',pipeEmpty('waiting for the next rank…'));
  $('laneGrad').innerHTML=pipeGrad(s);
}
tickPipeline(); setInterval(tickPipeline,10000);
 tick(); setInterval(tick,4000);
</script>
</body></html>
"""
@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(content=PAGE, headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"})
