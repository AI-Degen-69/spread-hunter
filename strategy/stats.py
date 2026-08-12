"""One read-side module for the fleet's KPIs (issue #13).

Every read query that used to live in the report module (`strategy/kpi.py`),
the dashboard page (`server/fleet_dash.py`) and the engine's inventory
rehydration (`strategy/fleet.py`) lives here. The write module
(`strategy/store.py`) keeps schema, migrations and writes; this module is the
only other place SQL is allowed. The dashboard page and the report module call
it instead of writing queries, and the dashboard's main endpoint pulls the
whole DB-derived payload with one `snapshot()`.

Two connection styles coexist, mirroring the code that moved here:

  * the report module's fetchers read through `store.db()` (the write
    module's connection, which owns schema creation) -- these rows feed
    `kpi.report()`'s pure math;
  * the dashboard's readers open their own read-only connection to
    `run/fleet.db` (or an explicit test path) so a web poll never contends
    with the fleet's writes.

The dashboard's running maker-rebate total (path-keyed cache + lock) moved
here too -- it is a read accelerator for `maker_rebate()`, not page state.
"""
from __future__ import annotations

import logging
import math
import sqlite3
import statistics
import threading
from pathlib import Path

from strategy import store
from strategy.config import load as load_cfg
from strategy.quotes import Inventory

ROOT = Path(__file__).resolve().parent.parent
RUN = ROOT / "run"
DB = RUN / "fleet.db"
CFG = load_cfg()

log = logging.getLogger("stats")

# Running maker-rebate totals per DB path: {path: (last_rowid, earned, shares,
# fills)}. `fills` is append-only, so carrying the total forward and reading
# only new rows is exact, not a cached approximation -- there is no staleness
# window to trade against. Keyed by path so a test DB cannot poison the live
# one. Process-local by design: it is a read accelerator, not state worth
# persisting, and a restart simply rebuilds it on the first request.
_REBATE_CACHE: dict[str, tuple[int, float, float, int]] = {}
# FastAPI runs a sync endpoint in a threadpool, so two polls can be in flight at
# once. Read-modify-write on the running total is not atomic: overlapping polls
# could both read the same checkpoint and both add the same rows. The critical
# section is bounded by the number of NEW fills, so holding it costs nothing.
_REBATE_LOCK = threading.Lock()


def _rows(sql: str, params: tuple = ()) -> list[dict]:
    """Run a read query against the write module's connection, as dicts.

    Owned here, not in the report module: the SQL string is the state
    reader's business. Schema creation happens on the write side (`store.db()`
    ensures it), so a fresh database is readable the moment it is written.
    """
    with store.db() as c:
        cur = c.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


# --- the report module's row fetchers (ex-kpi.py) -----------------------------

def reward_samples() -> list[dict]:
    return _rows("SELECT ts, our_score, market_score, our_share, n_sides "
                 "FROM reward_samples ORDER BY ts")


def quotes() -> list[dict]:
    return _rows("SELECT * FROM quotes")


def fills() -> list[dict]:
    return _rows("SELECT * FROM fills")


def resolutions() -> list[dict]:
    return _rows("SELECT * FROM resolutions")


def decisions() -> list[dict]:
    return _rows("SELECT action, count, t_remaining FROM decisions")


def decisions_non_quote() -> list[dict]:
    return _rows("SELECT action, reason, count FROM decisions "
                 "WHERE action <> 'QUOTE'")


def hedge_census() -> list[dict]:
    return _rows("SELECT pair_cost_at_touch, fillable_sub_one FROM hedge_census")


def balance_hedge_count() -> int:
    rows = _rows("SELECT count(*) AS n FROM decisions "
                 "WHERE action='CROSS_HEDGE'")
    return rows[0]["n"]


def recent_decisions(limit: int = 60) -> list[dict]:
    return _rows("SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,))


def recent_fills(limit: int = 40) -> list[dict]:
    return _rows("SELECT * FROM fills ORDER BY id DESC LIMIT ?", (limit,))


def recent_quotes(limit: int = 40) -> list[dict]:
    return _rows("SELECT * FROM quotes ORDER BY id DESC LIMIT ?", (limit,))


# --- the dashboard's readers (ex-fleet_dash.py) -------------------------------

def run_started() -> float | None:
    """When this run began, as a unix timestamp, or None before any data.

    Taken from the DB rather than from this process's start time, because the
    supervisor restarts the dashboard independently of the fleet -- a clock
    anchored to module import would silently reset to zero on a dashboard
    crash and report a fresh run that never happened. The first reward sample
    is written on the first visit to the first market, so it is the earliest
    honest moment to call the run started.
    """
    if not DB.exists():
        return None
    try:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        r = c.execute("SELECT MIN(ts) FROM reward_samples").fetchone()
        c.close()
        return r[0] if r and r[0] else None
    except Exception:
        # A brand-new DB has no reward_samples table yet. That is "not started",
        # not an error worth surfacing on the page.
        return None


def db_heartbeat() -> float | None:
    """Most recent write timestamp from the fleet DB, if it has started."""
    if not DB.exists():
        return None
    try:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        row = c.execute(
            "SELECT MAX(ts) FROM ("
            "SELECT ts FROM reward_samples "
            "UNION ALL SELECT ts FROM fill_evidence "
            "UNION ALL SELECT ts FROM live_state"
            ")"
        ).fetchone()
        c.close()
        return row[0] if row and row[0] else None
    except Exception:
        return None


def db_stats() -> dict:
    """Per-market history from the fleet DB. Empty dict if it has not run."""
    if not DB.exists():
        return {}
    try:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        out: dict = {}
        for r in c.execute(
                "SELECT condition_id, COUNT(*) n, AVG(our_share) share, "
                "MIN(ts) t0, MAX(ts) t1, "
                "SUM(CASE WHEN our_score>0 THEN 1 ELSE 0 END) scoring "
                "FROM reward_samples GROUP BY condition_id"):
            out[r["condition_id"]] = {
                "samples": r["n"], "avg_share": r["share"] or 0.0,
                "uptime": (r["scoring"] / r["n"]) if r["n"] else 0.0,
                "hours": (r["t1"] - r["t0"]) / 3600.0,
            }
        for r in c.execute("SELECT condition_id, COUNT(*) n, SUM(size) sh, "
                           "SUM(size*price) cost FROM fills GROUP BY condition_id"):
            out.setdefault(r["condition_id"], {}).update(
                {"fills": r["n"], "shares": r["sh"] or 0, "cost": r["cost"] or 0})
        # Closes reduce the position (fills alone overstate what is still
        # held) and book their own realized money -- both need to be visible
        # per-market, not just folded into a fleet-wide total.
        for r in c.execute(
                "SELECT condition_id, COUNT(*) n, SUM(shares) sh, "
                "SUM(realized_pnl) pnl, SUM(forgone_vs_settlement) forgone "
                "FROM closes GROUP BY condition_id"):
            out.setdefault(r["condition_id"], {}).update({
                "closes": r["n"], "closed_shares": r["sh"] or 0,
                "closed_pnl": r["pnl"] or 0.0,
                "closed_forgone": r["forgone"] or 0.0,
            })
        c.close()
        return out
    except Exception:
        return {}


def settled_positions() -> list[dict]:
    """One audit row per realized close event, newest first.

    Sell rows report the achieved combined effective exit price (both legs'
    proceeds divided by shares); merge rows report parity at $1.00 and expose
    gas separately. P&L percentage is net realized P&L over the recorded cost
    basis -- return on cost basis, including fees/gas in net P&L.

    A naked position that is never voluntarily closed does not stop being
    realized -- it settles the moment the venue resolves the market, paying
    $1/share on the winning token and $0 on the losing one. That never wrote a
    `closes` row (there was no SELL or MERGE, just a payout), so it was
    counted in `realized()`'s aggregate total but invisible here: an operator
    could see "$8.20 realized" at the top of the page and an empty table below
    it, which reads as a discrepancy rather than as two views of the same
    money. Synthesized as METHOD "RESOLVE" rows below, using the same
    held-shares-after-closes math `realized()` already uses.
    """
    if not DB.exists():
        return []
    try:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        rows = []
        for r in c.execute(
                "SELECT id, ts, market_slug, condition_id, method, gas, shares, "
                "up_price, dn_price, cost_basis, proceeds, fee, realized_pnl, "
                "forgone_vs_settlement FROM closes ORDER BY ts DESC, id DESC"):
            shares = float(r["shares"] or 0.0)
            cost = float(r["cost_basis"] or 0.0)
            proceeds = float(r["proceeds"] or 0.0)
            pnl = float(r["realized_pnl"] or 0.0)
            method = (r["method"] or "sell").upper()
            rows.append({
                "id": r["id"], "ts": r["ts"],
                "market_slug": r["market_slug"] or "",
                "condition_id": r["condition_id"] or "",
                "method": method, "shares": shares,
                "avg_cost": (cost / shares) if shares else None,
                "exit_price": (proceeds / shares) if shares else None,
                "yes_exit": r["up_price"], "no_exit": r["dn_price"],
                "cost_basis": cost, "proceeds": proceeds,
                "fee_or_gas": (r["gas"] if method == "MERGE" else r["fee"]),
                "realized_pnl": pnl,
                "pnl_pct": (100.0 * pnl / cost) if cost else None,
                "forgone_vs_settlement": r["forgone_vs_settlement"],
            })

        res = {rr["condition_id"]: (rr["winning_token"], rr["resolved_ts"])
               for rr in c.execute(
                   "SELECT condition_id, winning_token, resolved_ts "
                   "FROM resolutions")}
        by: dict[str, dict] = {}
        slugs: dict[str, str] = {}
        side_by_tok: dict[str, dict[str, str]] = {}
        for r in c.execute(
                "SELECT condition_id, market_slug, token_id, side, size, "
                "price FROM fills"):
            m = by.setdefault(r["condition_id"], {"cost": 0.0, "tok": {}})
            m["cost"] += (r["size"] or 0) * (r["price"] or 0)
            m["tok"][r["token_id"]] = (m["tok"].get(r["token_id"], 0.0)
                                       + (r["size"] or 0))
            slugs.setdefault(r["condition_id"], r["market_slug"] or "")
            side_by_tok.setdefault(r["condition_id"], {})[
                r["token_id"]] = r["side"]
        closed: dict[str, dict] = {}
        for r in c.execute(
                "SELECT condition_id, method, up_price, dn_price, shares, "
                "cost_basis FROM closes"):
            cl = closed.setdefault(r["condition_id"], {
                "sh": 0.0, "cb": 0.0, "up_closed": 0.0, "dn_closed": 0.0})
            n = r["shares"] or 0.0
            cl["sh"] += n
            cl["cb"] += r["cost_basis"] or 0.0
            if r["method"] == "naked_exit":
                if r["up_price"] is not None:
                    cl["up_closed"] += n
                else:
                    cl["dn_closed"] += n
            else:
                cl["up_closed"] += n
                cl["dn_closed"] += n
        c.close()

        for cid, m in by.items():
            win_res = res.get(cid)
            if not win_res:
                continue
            win, resolved_ts = win_res
            cl = closed.get(cid)
            cl_shares = cl["sh"] if cl else 0.0
            cl_cost = cl["cb"] if cl else 0.0
            
            win_side = side_by_tok.get(cid, {}).get(win)
            closed_win = (cl["up_closed"] if (cl and win_side == "UP")
                          else cl["dn_closed"] if (cl and win_side == "DOWN")
                          else cl_shares)
            held_win_shares = max(0.0, m["tok"].get(win, 0.0) - closed_win)

            lose_toks = [t for t in m["tok"] if t != win]
            lose_tok = lose_toks[0] if lose_toks else None
            lose_side = side_by_tok.get(cid, {}).get(lose_tok) if lose_tok else None
            closed_lose = (cl["up_closed"] if (cl and lose_side == "UP")
                           else cl["dn_closed"] if (cl and lose_side == "DOWN")
                           else cl_shares)
            held_lose_shares = max(0.0, m["tok"].get(lose_tok, 0.0) - closed_lose) if lose_tok else 0.0
            
            remaining_shares = max(held_win_shares, held_lose_shares)
            
            if remaining_shares <= 1e-4:
                continue          # fully closed voluntarily, nothing left to settle

            remaining_cost = max(0.0, m["cost"] - cl_cost)
            pnl = held_win_shares - remaining_cost
            rows.append({
                "id": f"resolve:{cid}", "ts": resolved_ts,
                "market_slug": slugs.get(cid, ""),
                "condition_id": cid,
                "method": "RESOLVE", "shares": remaining_shares,
                "avg_cost": (remaining_cost / remaining_shares),
                "exit_price": (held_win_shares / remaining_shares),
                "yes_exit": None, "no_exit": None,
                "cost_basis": remaining_cost, "proceeds": held_win_shares,
                "fee_or_gas": None,
                "realized_pnl": pnl,
                "pnl_pct": (100.0 * pnl / remaining_cost) if remaining_cost else None,
                "forgone_vs_settlement": None,
            })
        rows.sort(key=lambda r: (r["ts"] or 0, str(r["id"])), reverse=True)
        return rows
    except Exception as e:
        log.debug("settled position telemetry unavailable: %s", e)
        return []


def market_event_stats() -> tuple[dict[str, list[dict]], dict[str, int]]:
    """Return recent per-market events and structured refusal counters."""
    if not DB.exists():
        return {}, {}
    try:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        events: dict[str, list[dict]] = {}
        for r in c.execute(
                "SELECT condition_id, ts, kind, reason, reason_code, side, "
                "price, size FROM market_events "
                "ORDER BY condition_id, ts DESC, id DESC"):
            cid = r["condition_id"]
            bucket = events.setdefault(cid, [])
            if len(bucket) >= 3:
                continue
            bucket.append({
                "ts": r["ts"], "kind": r["kind"],
                "reason": r["reason"] or "", "reason_code": r["reason_code"] or "OTHER",
                "side": r["side"], "price": r["price"], "size": r["size"],
            })
        refusals = {r["reason_code"] or "OTHER": r["n"] for r in c.execute(
            "SELECT reason_code, COUNT(*) n FROM market_events "
            "WHERE kind='BLOCKED' GROUP BY reason_code")}
        c.close()
        return events, refusals
    except Exception as e:
        # Existing databases created before event telemetry are still valid;
        # report no events rather than making the whole dashboard disappear.
        log.debug("market event telemetry unavailable: %s", e)
        return {}, {}


def maker_rebate(db: Path | None = None) -> dict:
    """Serialised entry point -- see `_maker_rebate_locked` for the substance.

    `taker_fee` is imported here, OUTSIDE the lock: kpi.py imports this module
    at module level, so a module-level import here would form a cycle, and
    importing inside the critical section would put a (cached) import under
    the poll lock. The fee curve is a pure function; the first call resolves
    it and every call after is a dict hit.
    """
    from strategy.kpi import taker_fee
    with _REBATE_LOCK:
        return _maker_rebate_locked(db, taker_fee)


def _maker_rebate_locked(db: Path | None, taker_fee) -> dict:
    """Maker Rebates earned on matched volume. NOT the liquidity-reward pot.

    Two venue programs pay a maker, and the dashboard had only ever wired one:

      * LIQUIDITY REWARDS pay for RESTING size, sampled once a minute, filled
        or not. That is `rent_reward`, and it reads $0.00 because every market
        the fleet currently holds publishes clobRewards: 0 -- the program is
        not funded on them. The zero is the truth, not a missing wire.
      * MAKER REBATES pay a share of the taker fee on volume we MADE. An
        unfilled resting order earns exactly zero here no matter how long it
        rests, which is why no amount of uptime moves this number.

    So this is a fills query, not a score-share integral. Quoting both off the
    resting-size formula is the trap: applied to a spread market it multiplies
    a spread-capture PROJECTION by uptime and reports it as a venue
    distribution, double-counting income booked P&L already holds the moment
    the fill lands.

    Crossed fills are excluded because we were the taker on them: crediting our
    own aggressive leg with a maker rebate would pay us for the side we are
    also being charged the fee on.

    `taker_fee` is passed in rather than re-derived -- kpi.py already owns the
    crypto_fees_v2 curve, and a second copy is a second thing to get wrong.

    `err` is the difference between "no fills yet" and "the fills table could
    not be read". Both render $0.00, and on a MONEY figure those two must not
    look alike -- a silent zero here reads as "we earned nothing" when the real
    statement is "we do not know". The read still degrades rather than blanking
    the page, but it says so, in the payload and in the log.
    """
    path = DB if db is None else Path(db)
    key = str(path)
    seen, earned, shares, n = _REBATE_CACHE.get(key, (0, 0.0, 0.0, 0))
    out = {"earned": earned, "shares": shares, "fills": n,
           "per_share_cents": None, "err": ""}
    if not path.exists():
        # Not an error: before the first run there is no DB, and $0.00 earned
        # is the honest answer rather than a failure to report.
        return out
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            # ONLY THE ROWS WE HAVE NOT ALREADY COUNTED.
            #
            # `fills` is append-only -- store.py INSERTs and nothing updates or
            # deletes -- so a running total is exact rather than an
            # approximation, and the read is O(new fills) instead of O(all
            # fills). The dashboard polls this every 4 seconds, so the old full
            # scan grew without bound over a long-running fleet.
            top = c.execute("SELECT MAX(rowid) FROM fills").fetchone()[0] or 0
            if top < seen:
                # The DB was replaced (archived and recreated), so rowids
                # restarted and the running total describes a different
                # database. Start over rather than carry it across.
                seen, earned, shares, n = 0, 0.0, 0.0, 0
            # BOUNDED ABOVE BY THE CHECKPOINT WE ARE ABOUT TO STORE.
            #
            # `top` and this SELECT are separate statements, and the fleet
            # inserts continuously while the dashboard polls every 4s. Without
            # the upper bound a fill committed between the two was counted here
            # but not covered by `top`, so the stored checkpoint sat below a row
            # already added -- and the next poll counted its rebate a second
            # time. Silent, and on a money figure.
            rows = c.execute(
                "SELECT price, size FROM fills WHERE rowid > ? AND rowid <= ? "
                "AND (crossed = 0 OR crossed IS NULL)", (seen, top)).fetchall()
        finally:
            # The old code leaked the handle on any query failure -- `close()`
            # sat after the statement that raises.
            c.close()
    except Exception as e:
        # One unreadable metric must not blank the page, so this still returns.
        # What it no longer does is return silently.
        log.warning("maker rebate read failed on %s: %s", path, e)
        out["err"] = f"{type(e).__name__}: {e}"
        return out
    for price, size in rows:
        earned += taker_fee(price or 0.0, size or 0.0) * CFG.rebate_rate
        shares += size or 0.0
        n += 1
    _REBATE_CACHE[key] = (top, earned, shares, n)
    out.update(earned=earned, shares=shares, fills=n)
    if shares > 0:
        out["per_share_cents"] = 100.0 * earned / shares
    return out


def realized() -> dict:
    """Settled P&L from the fleet DB: payout - cost, per resolved market.

    Deliberately separate from the rent projection. Rent is what the venue is
    expected to pay for resting size; this is money the book has already
    decided. Returns zeros rather than None when nothing has resolved, so the
    tile renders a real number instead of a blank that reads as "unknown".

    Closes are folded in here because they change BOTH sides of the payout:
    a pair sold before resolution (a) no longer collects the $1 resolution
    credit for the shares that were sold, and (b) already booked its own
    realized_pnl at close time. Crediting the full fill count at resolution
    while ALSO ignoring closes would double-count money that was never paid
    (the resolution credit) while omitting money that actually was (the
    close proceeds) -- a payout that never happened, on a number the venue
    never sent.
    """
    out = {"realized": 0.0, "settled": 0, "wins": 0, "losses": 0, "cost": 0.0,
           "closes": 0, "closed_pnl": 0.0, "closed_forgone": 0.0,
           # One row per fully-resolved market: the TRUE settled outcome
           # (closes' own realized_pnl plus the $1/$0 the venue paid on
           # whatever was still held), not just voluntary exits. `closes`
           # alone is a biased sample -- a merge is a completed hedge and is
           # therefore almost always positive, while the naked tail that
           # loses money sits unrealized until it resolves and never gets a
           # `closes` row of its own. This is the population the go-live
           # readiness read (below) draws its statistics from.
           "rows": []}
    if not DB.exists():
        return out
    try:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        res = {r["condition_id"]: r["winning_token"]
               for r in c.execute(
                   "SELECT condition_id, winning_token FROM resolutions")}
        slugs = {r["condition_id"]: r["market_slug"] for r in c.execute(
            "SELECT condition_id, market_slug FROM fills "
            "GROUP BY condition_id")}
        by: dict[str, dict] = {}
        side_by_tok: dict[str, dict[str, str]] = {}
        for r in c.execute(
                "SELECT condition_id, token_id, side, size, price FROM fills"):
            m = by.setdefault(r["condition_id"], {"cost": 0.0, "tok": {}})
            m["cost"] += (r["size"] or 0) * (r["price"] or 0)
            m["tok"][r["token_id"]] = (m["tok"].get(r["token_id"], 0.0)
                                       + (r["size"] or 0))
            side_by_tok.setdefault(r["condition_id"], {})[
                r["token_id"]] = r["side"]
        closes: dict[str, dict] = {}
        for r in c.execute(
                "SELECT condition_id, method, up_price, dn_price, shares, "
                "cost_basis, realized_pnl, forgone_vs_settlement "
                "FROM closes"):
            cl = closes.setdefault(r["condition_id"], {
                "n": 0, "shares": 0.0, "cost_basis": 0.0, "pnl": 0.0,
                "forgone": 0.0, "up_closed": 0.0, "dn_closed": 0.0})
            n = r["shares"] or 0.0
            cl["n"] += 1
            cl["shares"] += n
            cl["cost_basis"] += r["cost_basis"] or 0.0
            cl["pnl"] += r["realized_pnl"] or 0.0
            cl["forgone"] += r["forgone_vs_settlement"] or 0.0
            if r["method"] == "naked_exit":
                # U35: one leg sold. The side is encoded by which price
                # field the row set.
                if r["up_price"] is not None:
                    cl["up_closed"] += n
                else:
                    cl["dn_closed"] += n
            else:
                cl["up_closed"] += n
                cl["dn_closed"] += n
        c.close()
        for cond, m in by.items():
            win = res.get(cond)
            if not win:
                continue
            cl = closes.get(cond, {"n": 0, "shares": 0.0, "cost_basis": 0.0,
                                    "pnl": 0.0, "forgone": 0.0,
                                    "up_closed": 0.0, "dn_closed": 0.0})
            # Resolution only pays for shares still held. A pair close
            # (merge/sell) removed one UP + one DOWN share, so the winning
            # token's fill count drops by the same amount; a naked exit
            # (U35) removed only the exited side, so only THAT side's count
            # drops before the $1 credit is applied.
            win_side = side_by_tok.get(cond, {}).get(win)
            closed_win = (cl["up_closed"] if win_side == "UP"
                          else cl["dn_closed"] if win_side == "DOWN"
                          else cl["shares"])
            held_win_shares = m["tok"].get(win, 0.0) - closed_win
            # cost is every dollar ever spent on fills in this market. The
            # portion already removed by closes (cost_basis) must come back
            # out here too, or it is charged against P&L twice: once inside
            # the close's own realized_pnl, and again here against a payout
            # those shares no longer collect.
            remaining_cost = m["cost"] - cl["cost_basis"]
            pnl = held_win_shares - remaining_cost + cl["pnl"]
            out["realized"] += pnl
            out["cost"] += m["cost"]
            out["settled"] += 1
            out["wins" if pnl > 0 else "losses"] += 1
            out["closes"] += cl["n"]
            out["closed_pnl"] += cl["pnl"]
            out["closed_forgone"] += cl["forgone"]
            out["rows"].append({
                "condition_id": cond, "market_slug": slugs.get(cond, ""),
                "pnl": pnl, "cost": m["cost"],
            })
        # Closes on markets that have NOT yet resolved still book real,
        # already-realized money -- count them too, so an operator sees the
        # close activity even before the underlying market settles.
        for cond, cl in closes.items():
            if cond in res:
                continue
            out["realized"] += cl["pnl"]
            out["closes"] += cl["n"]
            out["closed_pnl"] += cl["pnl"]
            out["closed_forgone"] += cl["forgone"]
    except Exception:
        pass
    return out


# GO-LIVE READINESS (2026-08-06). Two tiers, because they answer different
# questions and reach significance on different timelines.
#
# Tier 1, MACHINERY: does the pooled size-weighted markout (strategy/markout.py
# fleet_stats, same statistic that drives the HALTED gate) read positive on a
# real effective sample. This is a fast, low-variance leading indicator --
# drift is measured within hours of a fill, not weeks -- so it answers "are the
# entry/risk rules behaving as designed" long before enough markets have
# resolved to answer the money question.
#
# Tier 2, MONEY: does TRUE realized P&L per market (closes.realized_pnl plus
# the $1/$0 the venue paid on whatever was still held at resolution -- see
# `realized`, NOT the closes table alone, which is biased toward voluntary
# hedge-merges and mostly excludes the naked tail that loses) read positive
# with a confidence interval that excludes zero.
#
# WHY THE MONEY-TIER SAMPLE SIZE IS LARGE. Audited 2026-08-06 against the last
# populated paper run (13 closes, all merges, mean +16.5% / stdev 10.8% --
# optimistic because it excludes the naked tail entirely) and against the
# 2026-08-05 forensic audit (3 of 23 markets carried concentrated naked losses
# up to -$190 on a ~$190 cost basis, i.e. close to -100% on that market). A
# blend illustrative of that shape -- 85% of markets near +18%, 15% near -90%
# -- has a mean near +2% and a stdev near 39%. Detecting a ~2% mean against a
# ~39% stdev at 90% confidence needs roughly (1.645 * 39 / 2) ** 2 =~ 1,000
# settled markets. That is a real property of a strategy whose payoff is
# "small frequent capture, rare large binary tail," not a bug in the read --
# and it is exactly why U1-U7 (the dollar risk gates) matter: shrinking the
# tail's magnitude shrinks the required sample far faster than waiting out
# more trades does. GO_LIVE_MIN_SETTLED below is therefore a practical minimum
# for a small real-money pilot decision, not a claim of full significance;
# treat SIGNAL_MIN_SETTLED as "enough to stop being pure noise" and
# GO_LIVE_MIN_SETTLED as "enough to bet the first real dollars," with the
# confidence interval sign as the actual gate in both cases.
SIGNAL_MIN_SETTLED = 30
GO_LIVE_MIN_SETTLED = 100
GO_LIVE_MIN_CALENDAR_DAYS = 14.0
GO_LIVE_MAX_CATEGORY_SHARE = 0.5


def _markout_read_cols(c) -> tuple[int, ...]:
    """Longest-first horizon column indices for the markouts table `c` sees.

    Session 50: the 15m exit-window read is APPENDED to the schema as mid_h3,
    after the 6h column, so column index and horizon length diverge once it
    exists -- every reader that needs "the longest matured horizon" must
    descend by DURATION, never by index. A dashboard polling a fleet that has
    not restarted since the column landed may still see a 3-column table (the
    read-only connection cannot run the ALTER TABLE migration), so the request
    is resolved against the live schema rather than assumed.
    """
    # PRAGMA table_info rows are (cid, name, type, notnull, dflt, pk) -- the
    # column NAME is at index 1; index 0 is the numeric cid.
    cols = {r[1] for r in c.execute("PRAGMA table_info(markouts)")}
    return tuple(i for i, _ in sorted(enumerate(CFG.markout_horizons),
                                      key=lambda p: -p[1])
                 if f"mid_h{i}" in cols)


def pooled_markout_neff() -> dict:
    """Kish effective sample size and size-weighted mean drift, pooled fleet-
    wide over every fill's longest matured horizon. Mirrors
    strategy/markout.py's `_stats_from_rows` exactly (same weighting, same
    contamination exclusion) so this number means what the live HALTED gate
    means, not an approximation of it.
    """
    out = {"n_eff": 0.0, "n_rows": 0, "mean_per_share": None}
    if not DB.exists():
        return out
    try:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        order = _markout_read_cols(c)
        cols = ", ".join(f"mid_h{i}" for i in order)
        weights: list[float] = []
        values: list[float] = []
        for r in c.execute(
                f"SELECT ref_mid, {cols}, size, ref_mid_source FROM markouts"):
            if r["ref_mid_source"] == "contaminated" or r["ref_mid"] is None:
                continue
            mid = next((r[f"mid_h{i}"] for i in order
                        if r[f"mid_h{i}"] is not None), None)
            if mid is None:
                continue
            size = r["size"]
            w = float(size) if (size and size > 0) else 0.0
            if w <= 0:
                continue
            weights.append(w)
            values.append(mid - r["ref_mid"])
        c.close()
        total = sum(weights)
        out["n_rows"] = len(weights)
        if total > 0:
            out["n_eff"] = total * total / sum(w * w for w in weights)
            out["mean_per_share"] = sum(
                w * v for w, v in zip(weights, values)) / total
    except Exception:
        pass
    return out


def go_live_readiness() -> dict:
    """Where the fleet stands against both readiness tiers, right now."""
    real = realized()
    rows = real["rows"]
    n = len(rows)
    returns = [100.0 * r["pnl"] / r["cost"] for r in rows if r["cost"]]
    mean_ret = (sum(returns) / len(returns)) if returns else None
    ci_low = None
    stdev_ret = None
    if len(returns) > 1:
        stdev_ret = statistics.stdev(returns)
        se = stdev_ret / math.sqrt(len(returns))
        ci_low = mean_ret - 1.645 * se

    markout = pooled_markout_neff()

    calendar_days = None
    if DB.exists():
        try:
            c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
            t0, t1 = c.execute(
                "SELECT MIN(ts), MAX(ts) FROM fills").fetchone()
            c.close()
            if t0 is not None and t1 is not None:
                calendar_days = (t1 - t0) / 86400.0
        except Exception:
            pass

    # A rough sport/category tag: the venue slug's first hyphen-delimited
    # token ("atp-jodar-...", "lol-maz-...", "cs2-arc4-..."). Good enough to
    # catch "every settled market is the same sport", which is the
    # concentration failure a thin universe reproduces even after a filter
    # fix -- it says nothing about individual-market correlation.
    category_counts: dict[str, int] = {}
    for r in rows:
        tag = (r["market_slug"] or "?").split("-", 1)[0] or "?"
        category_counts[tag] = category_counts.get(tag, 0) + 1
    max_category_share = (max(category_counts.values()) / n) if n else None

    if n >= GO_LIVE_MIN_SETTLED and ci_low is not None and ci_low > 0 \
            and (calendar_days or 0) >= GO_LIVE_MIN_CALENDAR_DAYS \
            and (max_category_share or 1.0) <= GO_LIVE_MAX_CATEGORY_SHARE:
        status = "READY_FOR_SMALL_LIVE_PILOT"
    elif n >= SIGNAL_MIN_SETTLED:
        status = "DIRECTIONAL_SIGNAL"
    elif n > 0:
        status = "COLLECTING"
    else:
        status = "NO_DATA"

    return {
        "status": status,
        "n_settled": n,
        "signal_min_settled": SIGNAL_MIN_SETTLED,
        "go_live_min_settled": GO_LIVE_MIN_SETTLED,
        "mean_return_pct": mean_ret,
        "stdev_return_pct": stdev_ret,
        "ci90_lower_pct": ci_low,
        "calendar_days": calendar_days,
        "go_live_min_calendar_days": GO_LIVE_MIN_CALENDAR_DAYS,
        "max_category_share": max_category_share,
        "go_live_max_category_share": GO_LIVE_MAX_CATEGORY_SHARE,
        "category_counts": category_counts,
        "markout_n_eff": markout["n_eff"],
        "markout_mean_per_share": markout["mean_per_share"],
    }


def markout_stats() -> dict:
    """Cost-of-fill per market, straight from the markouts table.

    Read here rather than taken from live state so the figure survives a bot
    restart -- the fills happened whether or not the process that recorded
    them is still running.
    """
    out: dict = {"by_market": {}, "total": 0.0, "spread": 0.0, "n": 0,
                 "matured_n": 0}
    if not DB.exists():
        return out
    try:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        order = _markout_read_cols(c)
        cols = ", ".join(f"mid_h{i}" for i in order)
        for r in c.execute(
                "SELECT condition_id, side, fill_price, size, ref_mid, "
                f"ref_mid_source, {cols} FROM markouts"):
            if r["ref_mid_source"] == "contaminated":
                continue
            mid = next((r[f"mid_h{i}"] for i in order
                        if r[f"mid_h{i}"] is not None), None)
            if mid is None:
                continue          # no horizon matured yet
            # DRIFT, not total. Total includes the ~2c we quote under mid, so
            # a market that never moved reads +2.15c and looks like edge.
            # Drift is the market moving against us, which is what deserves a
            # tile. Captured spread is reported separately, not blended in.
            ref = r["ref_mid"]
            if ref is None:
                continue
            # THE GATE'S OWN SAMPLE COUNT, which is stricter than `n` below.
            # `n` accepts any matured horizon, so a fill counts the moment h0
            # (5m) lands. The fleet gate reads the h1 (1h) mark, so a dashboard
            # reporting `n` against the 25-fill threshold overstates progress
            # toward activation. Counted separately, on the same contaminated/
            # missing-ref exclusions the aggregates use.
            if r["mid_h1"] is not None:
                out["matured_n"] += 1
            drift = mid - ref
            spread = ref - (r["fill_price"] or 0.0)
            b = out["by_market"].setdefault(
                r["condition_id"], {"sum": 0.0, "n": 0})
            b["sum"] += drift
            b["n"] += 1
            out["total"] += drift * (r["size"] or 0.0)
            out["spread"] += spread * (r["size"] or 0.0)
            out["n"] += 1
        c.close()
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    for cid, b in out["by_market"].items():
        b["mean_per_share"] = b["sum"] / b["n"] if b["n"] else None
    return out


def pairs_ev() -> dict:
    """The pairs-only rule's EV read (U35), per one-sided fill.

    EV = completion_rate x complete_gain_cents - exit_rate x exit_cost_cents,
    where the rates come from the rule's own recorded decisions and the two
    payoffs are the re-measured config constants (Sessions 44-46): +3.68c for
    a completed pair (rule-era merge capture on 145+ closes) and -3.67c for a
    naked exit (realized exit economics, n=4). The denominators are the
    rule-triggering one-sided fills: completed, exited, or rode out the
    window.

    `dist` and `outliers` (Sessions 44-47) describe the completed-pair
    capture over the SAME rule-era slice and IQR fences as
    `scripts/pairs_ev_report.py`, so the tile's tooltip and the report
    always agree. None, not 0.0, for the rates, EV, and distribution before
    the first decision: an empty run must not read as a measured breakeven.
    """
    out = {"one_sided": 0, "completions": 0, "exits": 0, "expired": 0,
           "completion_rate": None, "exit_rate": None, "ev_cents": None,
           "complete_gain_cents": CFG.pairs_complete_gain_cents,
           "exit_cost_cents": CFG.pairs_exit_cost_cents,
           "dist": None, "outliers": None}
    if not DB.exists():
        return out
    try:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        rows = c.execute(
            "SELECT kind, COUNT(*) FROM market_events WHERE kind IN "
            "('PAIR_COMPLETE','NAKED_EXIT','PAIR_WINDOW_EXPIRED') "
            "GROUP BY kind").fetchall()
        by = dict(rows)
        out["completions"] = int(by.get("PAIR_COMPLETE", 0))
        out["exits"] = int(by.get("NAKED_EXIT", 0))
        out["expired"] = int(by.get("PAIR_WINDOW_EXPIRED", 0))
        one = out["completions"] + out["exits"] + out["expired"]
        out["one_sided"] = one
        if one > 0:
            out["completion_rate"] = out["completions"] / one
            out["exit_rate"] = out["exits"] / one
            out["ev_cents"] = round(
                out["completion_rate"] * CFG.pairs_complete_gain_cents
                - out["exit_rate"] * CFG.pairs_exit_cost_cents, 3)

        # Completed-pair capture distribution: per-close rates over the
        # rule-era slice (ts >= the first PAIR_COMPLETE), IQR fences exactly
        # as scripts/pairs_ev_report.py computes them. None before the first
        # rule-era merge, not an empty dict.
        era = c.execute(
            "SELECT MIN(ts) FROM market_events WHERE kind='PAIR_COMPLETE'"
        ).fetchone()
        if era and era[0] is not None:
            rates = sorted(float(r[0]) for r in c.execute(
                "SELECT realized_pnl / shares * 100.0 FROM closes "
                "WHERE method='merge' AND ts >= ? AND shares > 0",
                (era[0],)))
            if rates:
                n = len(rates)

                def _pct(idx):
                    return rates[min(n - 1, int((n - 1) * idx))]

                p25, p75 = _pct(0.25), _pct(0.75)
                iqr = p75 - p25
                lo, hi = p25 - 1.5 * iqr, p75 + 1.5 * iqr
                out["dist"] = {
                    "n": n,
                    "mean": round(sum(rates) / n, 3),
                    "median": round(statistics.median(rates), 3),
                    "p25": round(p25, 3), "p75": round(p75, 3),
                    "min": round(rates[0], 3), "max": round(rates[-1], 3),
                }
                out["outliers"] = {
                    "count": sum(1 for r in rates if r < lo or r > hi),
                    "fences": [round(lo, 3), round(hi, 3)],
                }
        c.close()
    except Exception:
        return out
    return out


def share_history(n: int = 24) -> list[float]:
    """Fleet-wide avg our_share, one point per hour, most recent n hours.

    Not a dollar series -- no $ is persisted per sample (reward_samples only
    stores our_share), so this is the raw signal the $ estimates are built
    FROM: how much of the book the fleet held, over time. Sparkline data for
    the hero, not a ledger.
    """
    if not DB.exists():
        return []
    try:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        rows = c.execute(
            "SELECT CAST(ts/3600 AS INTEGER) hr, AVG(our_share) s "
            "FROM reward_samples GROUP BY hr ORDER BY hr").fetchall()
        c.close()
        return [r[1] or 0.0 for r in rows[-n:]]
    except Exception:
        return []


def inventory_from_db(cid: str) -> Inventory:
    """Rebuild a market's share position from its persisted fills.

    The fills table is the ledger; Inventory was only ever a running total of
    it held in memory. Recomputing from the ledger on startup makes the two
    agree, which is the difference between a dashboard that says "no position"
    and one that shows the shares we are actually holding.

    Returns an empty Inventory on any failure -- a fresh DB, a missing table.
    That is the same state as before this function existed, so a broken read
    degrades to the old behaviour rather than stopping the fleet.
    """
    inv = Inventory()
    last_fill_ts: float | None = None
    try:
        with store.db() as c:
            for side, size, price, ts in c.execute(
                    "SELECT side, size, price, ts FROM fills WHERE condition_id=?",
                    (cid,)):
                if side == "UP":
                    inv.up_shares += size or 0.0
                    inv.up_cost += (size or 0.0) * (price or 0.0)
                else:
                    inv.down_shares += size or 0.0
                    inv.down_cost += (size or 0.0) * (price or 0.0)
                inv.fills += 1
                if ts:
                    last_fill_ts = (ts if last_fill_ts is None
                                    else max(last_fill_ts, ts))
            for shares, cost_basis, up_removed, dn_removed, method, \
                    up_price, dn_price in c.execute(
                        "SELECT shares, cost_basis, up_cost_removed, "
                        "dn_cost_removed, method, up_price, dn_price "
                        "FROM closes WHERE condition_id=?",
                        (cid,)):
                n = shares or 0.0
                if method == "naked_exit":
                    # U35. A naked exit sold ONE leg only. The side is encoded
                    # by which price field is set -- the same encoding the
                    # settled-positions reader uses. The OTHER leg is
                    # untouched, so it must not be decremented.
                    if up_price is not None:
                        inv.up_shares -= n
                        inv.up_cost -= up_removed or 0.0
                    else:
                        inv.down_shares -= n
                        inv.down_cost -= dn_removed or 0.0
                    continue
                # A pair close (merge or sell) removed one UP and one DOWN
                # share per pair, each at its OWN average cost at close time --
                # not in proportion to share counts, which only coincides with
                # the true split when both legs happen to share the same
                # average price. The exact per-leg amounts removed are recorded
                # on the row, so use them directly instead of re-deriving (and
                # getting wrong) a split.
                inv.up_shares -= n
                inv.down_shares -= n
                if up_removed is not None and dn_removed is not None:
                    inv.up_cost -= up_removed
                    inv.down_cost -= dn_removed
                else:
                    # Row written before up_cost_removed/dn_cost_removed
                    # existed: fall back to the old (approximate) even split
                    # rather than crashing on a NULL.
                    inv.up_cost -= (cost_basis or 0.0) * 0.5
                    inv.down_cost -= (cost_basis or 0.0) * 0.5
    except Exception as e:
        log.warning("inventory rehydrate failed for %s: %s", cid[:10], e)
    inv.last_fill_ts = last_fill_ts
    return inv


def snapshot() -> dict:
    """Everything the dashboard page reads out of SQL, in one call.

    The page still reads the live state file and the loop pulse itself; this
    is the state-reader half of the render, fetched once so the page never
    opens its own queries. Each value is exactly what the underlying reader
    returns -- `market_event_stats` is the (events, refusals) pair, and
    `maker_rebate` carries its own `err` field.
    """
    return {
        "run_started": run_started(),
        "db_heartbeat": db_heartbeat(),
        "db_stats": db_stats(),
        "settled_positions": settled_positions(),
        "market_event_stats": market_event_stats(),
        "maker_rebate": maker_rebate(),
        "realized": realized(),
        "go_live_readiness": go_live_readiness(),
        "share_history": share_history(),
        "markout_stats": markout_stats(),
        "pairs_ev": pairs_ev(),
    }
