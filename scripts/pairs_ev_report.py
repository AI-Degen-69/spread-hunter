"""Pairs-only rule EV report (Sessions 44-46): the whole stack, one command.

WHY THIS EXISTS
---------------
Sessions 44-46 measured the pairs-only rule's EV by hand-written SQL each
time: PAIR_COMPLETE / NAKED_EXIT / PAIR_WINDOW_EXPIRED counts, the rule-era
merge capture, the naked-exit economics, the distribution, and the outlier
that a plain mean hides (Session 46's 16.50c max against a 3.83c median).
This report is that measurement as one read-only command, so the next
sample lands with the full picture instead of three ad-hoc queries.

THE NUMBER THAT DECIDES
-----------------------
EV per one-sided fill = completion_rate x complete_gain - exit_rate x exit_cost,
the rates from the rule's recorded decisions, the payoffs from the
re-measured config constants (complete_gain 3.68c, Sessions 44/46; exit_cost
3.67c, Session 45). The dashboard's PAIRS-ONLY tile is the same formula on
the same tables; this report prints the same reading plus the distribution,
the per-market capture, and the outlier flags the tile cannot show. The
report's exit criterion is the same comparison the rule lives by: positive
EV -> the rule stays live.

WHAT THE RECORD CANNOT ANSWER -- stated, never papered over
-----------------------------------------------------------
  * MERGE CAPTURE IS THE WHOLE PAIR. `closes.realized_pnl` for a merge is
    the pair's total capture, including the spread earned on the passive
    leg that filled before the rule acted. The completion payoff is
    therefore the pair-level number, not the marginal cost of the crossed
    leg alone.
  * FILLS ARE NOT LINKED TO THEIR CLOSES. The EV denominator (rule
    decisions in market_events) and the closes are counted independently;
    the report shows both and their realized totals, and never invents a
    fill-to-close join.
  * NATURAL PAIRS CAN SLIP INTO THE RULE-ERA SLICE. A pair whose BOTH legs
    filled passively also merges without a PAIR_COMPLETE event, and the
    slice (ts >= the first PAIR_COMPLETE) can include one. The per-market
    merges-vs-completions table is the attribution check.
  * THE EXIT-WAIT JOIN IS WINDOW-BASED, NOT ID-LINKED (Session 50). Nothing
    links a close to its fill: the report matches the triggering fill by
    same condition + side + nearest ts within 10s of the close, and the
    markout row by nearest ts within 30s of the fill. Verified against the
    Session 49 sample (all four fills match with identical prices,
    close-fill deltas 0.08-0.17s). The 15m counterfactual reads mid_h3 and
    compares it to the exit price: a mid BELOW the exit price is decisive
    (the market kept falling); a mid above it by less than a spread is
    inconclusive, because the exit sold at the BID while mid_h3 is a MID.
    And fill+900s equals exit+900s only while exits fire at age ~0 -- a
    nonzero-age exit must subtract its age from the reading.
"""
from __future__ import annotations

import argparse
import sqlite3
import statistics
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategy.config import load as load_cfg                      # noqa: E402
from strategy.store import BUSY_TIMEOUT_SEC                       # noqa: E402

DEFAULT_DB = ROOT / "run" / "fleet.db"
KINDS = ("PAIR_COMPLETE", "NAKED_EXIT", "PAIR_WINDOW_EXPIRED")


# --------------------------------------------------------------------------
# reading (read-only; this script never writes to the database)
# --------------------------------------------------------------------------

def _connect(path: Path) -> sqlite3.Connection:
    """A read-only handle, following `scripts/replay_risk_gates.py`.

    `mode=ro` is the enforcement, not a convention: the fleet may be writing
    this file while the report reads it. WAL databases occasionally refuse a
    URI-readonly open when the -shm file is missing, so `query_only` is the
    fallback -- same guarantee, weaker mechanism.

    The existence check is load-bearing: the fallback `sqlite3.connect(str(path))`
    would otherwise CREATE an empty file for a missing path, contradicting the
    report's never-writes guarantee (coderabbit).
    """
    if not path.exists():
        raise SystemExit(f"no such database: {path}")
    try:
        c = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True,
                            timeout=BUSY_TIMEOUT_SEC)
        c.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchall()
        return c
    except sqlite3.Error:
        c = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_SEC)
        c.execute("PRAGMA query_only = 1")
        return c


def _rows(c: sqlite3.Connection, sql: str, params: tuple = ()) -> list[tuple]:
    """Query, or an empty result if the table is not there.

    A database with no `closes`/`market_events` tables is a database nothing
    has been recorded into yet. That is a zero, not an error -- the tables
    are created lazily by `store._conn`.
    """
    try:
        return c.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []


def _f(x) -> Optional[float]:
    return None if x is None else float(x)


# --------------------------------------------------------------------------
# the report (pure: DB reads in, dict out)
# --------------------------------------------------------------------------

def report(path: Path) -> dict:
    """The full pairs-rule EV stack as a dict (Sessions 44-46 surface)."""
    out: dict = {"db": str(path)}
    cfg = load_cfg()
    out["payoffs"] = {"complete_gain_cents": cfg.pairs_complete_gain_cents,
                      "exit_cost_cents": cfg.pairs_exit_cost_cents}

    c = _connect(path)
    try:
        # --- rule decisions (the EV denominator and rates) ---------------
        by_kind = {k: 0 for k in KINDS}
        for k, n in _rows(c, "SELECT kind, COUNT(*) FROM market_events WHERE "
                             "kind IN ('PAIR_COMPLETE','NAKED_EXIT',"
                             "'PAIR_WINDOW_EXPIRED') GROUP BY kind"):
            if k in by_kind:
                by_kind[k] = int(n)
        one = sum(by_kind.values())
        cr = by_kind["PAIR_COMPLETE"] / one if one else None
        er = by_kind["NAKED_EXIT"] / one if one else None
        ev = (round(cr * cfg.pairs_complete_gain_cents
                    - er * cfg.pairs_exit_cost_cents, 3)
              if one else None)
        verdict = ("NO DATA" if one == 0
                   else "PASS (EV > 0)" if (ev or 0.0) > 0 else "FAIL")
        out["kpi"] = {"one_sided": one, "completions": by_kind["PAIR_COMPLETE"],
                      "exits": by_kind["NAKED_EXIT"],
                      "expired": by_kind["PAIR_WINDOW_EXPIRED"],
                      "completion_rate": cr, "exit_rate": er,
                      "ev_cents": ev, "verdict": verdict}

        # --- rule era: from the first completion decision ----------------
        t0 = _rows(c, "SELECT MIN(ts) FROM market_events WHERE "
                      "kind='PAIR_COMPLETE'")
        era_start = _f(t0[0][0]) if t0 and t0[0][0] is not None else None
        out["rule_era_start_ts"] = era_start

        # --- per-market decisions (attribution context) ------------------
        kind_key = {"PAIR_COMPLETE": "completions",
                    "NAKED_EXIT": "exits",
                    "PAIR_WINDOW_EXPIRED": "expired"}
        dec: dict[str, dict] = {}
        for k, slug, n in _rows(
                c, "SELECT kind, market_slug, COUNT(*) FROM market_events "
                   "WHERE kind IN ('PAIR_COMPLETE','NAKED_EXIT',"
                   "'PAIR_WINDOW_EXPIRED') GROUP BY kind, market_slug"):
            d = dec.setdefault(slug or "(unknown)",
                               {"completions": 0, "exits": 0, "expired": 0})
            d[kind_key[k]] = int(n)
        out["decisions_by_market"] = [
            {"market": m, **d} for m, d in sorted(
                dec.items(), key=lambda kv: -sum(kv[1].values()))]

        # --- naked exits: realized economics per close -------------------
        cols = [r[1] for r in _rows(c, "PRAGMA table_info(closes)")]
        has_close = bool(cols)
        exits = []
        if has_close:
            for r in _rows(c, "SELECT ts, market_slug, shares, cost_basis, "
                              "proceeds, fee, realized_pnl, up_price, "
                              "dn_price FROM closes WHERE method='naked_exit' "
                              "ORDER BY ts"):
                ts, slug, sh, cb, pr, fee, pnl, up, dn = r
                sh = float(sh or 0.0)
                # The recorded per-share exit price is `up_price`/`dn_price`
                # (the fill the exit sold at); proceeds/shares is the fallback
                # for rows written before the side-aware closes.
                exit_price = (up if up is not None
                              else dn if dn is not None
                              else (float(pr or 0.0) / sh if sh else None))
                exits.append({
                    "ts": float(ts), "market": slug or "(unknown)",
                    "shares": sh, "avg_cost": float(cb or 0.0) / sh if sh else None,
                    "exit_price": exit_price, "fee": _f(fee),
                    "pnl": float(pnl or 0.0),
                    "per_share_c": (float(pnl or 0.0) / sh * 100.0
                                    if sh else None)})
        esh = sum(e["shares"] for e in exits)
        out["exits"] = {"closes": exits, "n": len(exits), "shares": esh,
                        "pnl": sum(e["pnl"] for e in exits),
                        "per_share_c": (sum(e["pnl"] for e in exits) / esh * 100.0
                                        if esh else None)}

        # --- exit-vs-wait counterfactual (Session 50) --------------------
        # The recorded replacement for Session 49's inferred bid ladder:
        # for each naked exit, the triggering fill (same condition + side,
        # nearest ts -- exits fire at age ~0) and that fill's markout row.
        # The 15m horizon (mid_h3) answers "where was the mid 15 minutes
        # after the exit?", recorded instead of inferred. Five honest states
        # rather than a silent blank: recorded / pending (15m not elapsed) /
        # no_markout / no_fill / no_column (mid_h3 missing -- the fleet has
        # not restarted since Session 50, so its DB predates the migration).
        mk_cols = {r[1] for r in _rows(c, "PRAGMA table_info(markouts)")}
        has_h3 = "mid_h3" in mk_cols
        xc = {"closes": [], "recorded": 0, "pending": 0, "no_markout": 0,
              "no_fill": 0, "no_column": 0}
        if has_close:
            for r in _rows(c, "SELECT ts, condition_id, market_slug, shares, "
                              "up_price, dn_price FROM closes "
                              "WHERE method='naked_exit' ORDER BY ts"):
                ts, cid, slug, sh, up, dn = r
                sh = float(sh or 0.0)
                side = "UP" if up is not None else "DOWN"
                exit_price = up if up is not None else dn
                row = {"ts": float(ts), "market": slug or "(unknown)",
                       "side": side, "shares": sh, "exit_price": _f(exit_price),
                       "fill_price": None, "fill_reason": None, "mid_h3": None,
                       "gap_c": None, "status": None}
                fill = _rows(c, "SELECT ts, price, size, reason FROM fills "
                                "WHERE condition_id=? AND side=? AND "
                                "ABS(ts - ?) <= 10 ORDER BY ABS(ts - ?) LIMIT 1",
                             [cid, side, ts, ts])
                if not fill:
                    row["status"] = "no_fill"
                else:
                    row["fill_price"] = _f(fill[0][1])
                    row["fill_reason"] = fill[0][3]
                    if not has_h3:
                        row["status"] = "no_column"
                    else:
                        mk = _rows(c, "SELECT ts, fill_price, size, mid_h0, "
                                      "mid_h1, mid_h2, mid_h3 FROM markouts "
                                      "WHERE condition_id=? AND side=? AND "
                                      "ABS(ts - ?) <= 30 ORDER BY ABS(ts - ?) "
                                      "LIMIT 1",
                                   [cid, side, fill[0][0], fill[0][0]])
                        if not mk:
                            row["status"] = "no_markout"
                        elif mk[0][6] is None:
                            row["status"] = "pending"
                        else:
                            row["status"] = "recorded"
                            row["mid_h3"] = _f(mk[0][6])
                            row["gap_c"] = ((mk[0][6] - exit_price) * 100.0
                                            if exit_price is not None else None)
                xc["closes"].append(row)
                xc[row["status"]] += 1
        rec = [e for e in xc["closes"] if e["status"] == "recorded"]
        xc["aggregate"] = None
        if rec:
            gaps = [e["gap_c"] for e in rec if e["gap_c"] is not None]
            drifts = [((e["mid_h3"] - e["fill_price"]) * 100.0)
                      for e in rec if e["fill_price"] is not None]
            xc["aggregate"] = {
                "n": len(rec),
                "exit_beat_wait": sum(1 for g in gaps if g < 0),
                "wait_maybe_better": sum(1 for g in gaps if g > 0),
                "mean_gap_c": (statistics.mean(gaps) if gaps else None),
                "median_gap_c": (statistics.median(gaps) if gaps else None),
                "mean_15m_drift_c": (statistics.mean(drifts) if drifts else None),
            }
        out["exit_counterfactual"] = xc

        # --- completed pairs: rule-era merge capture ---------------------
        merges = {"n": 0, "pnl": 0.0, "shares": 0.0, "by_market": {}}
        rates: list[float] = []
        # (slug, shares, pnl, rate) per nonzero-share close, collected in the
        # one rule-era read so the outlier pass below reuses it instead of
        # issuing a second identical query (coderabbit).
        merge_rows: list[tuple[str, float, float, float]] = []
        if has_close and era_start is not None:
            for r in _rows(c, "SELECT market_slug, shares, realized_pnl FROM "
                              "closes WHERE method='merge' AND ts >= ?",
                           [era_start]):
                slug, sh, pnl = r
                sh = float(sh or 0.0)
                pnl = float(pnl or 0.0)
                merges["n"] += 1
                merges["pnl"] += pnl
                merges["shares"] += sh
                b = merges["by_market"].setdefault(
                    slug or "(unknown)", {"n": 0, "shares": 0.0, "pnl": 0.0})
                b["n"] += 1
                b["shares"] += sh
                b["pnl"] += pnl
                if sh:
                    rate = pnl / sh * 100.0
                    rates.append(rate)
                    merge_rows.append((slug or "(unknown)", sh, pnl, rate))
        dist = None
        outliers = []
        if rates:
            rates.sort()
            n = len(rates)
            q = lambda p: rates[min(n - 1, int((n - 1) * p))]          # noqa: E731
            p25, p75 = q(0.25), q(0.75)
            iqr = p75 - p25
            lo, hi = p25 - 1.5 * iqr, p75 + 1.5 * iqr
            dist = {"mean": statistics.mean(rates), "median": statistics.median(rates),
                    "p25": p25, "p75": p75, "min": rates[0], "max": rates[-1],
                    "iqr_fences": [lo, hi], "n": n}
            # IQR outliers, per close (market + rate), flagged not hidden --
            # from the rows already collected in the first read, no re-query.
            for market, sh, pnl, rate in merge_rows:
                if rate < lo or rate > hi:
                    outliers.append({"market": market,
                                     "per_share_c": rate,
                                     "pnl": pnl, "shares": sh})
        out["merges"] = {
            **merges,
            "per_share_c": (merges["pnl"] / merges["shares"] * 100.0
                            if merges["shares"] else None),
            "all_positive": (all(r > 0 for r in rates) if rates else None),
            "distribution": dist, "outliers": outliers,
            "by_market": sorted(
                ({"market": m, **b,
                  "per_share_c": (b["pnl"] / b["shares"] * 100.0
                                  if b["shares"] else None)}
                 for m, b in merges["by_market"].items()),
                key=lambda r: -r["pnl"])}

        # --- realized EV in dollars ---------------------------------------
        out["realized"] = {
            "completions": merges["pnl"], "exits": out["exits"]["pnl"],
            "total": merges["pnl"] + out["exits"]["pnl"], "one_sided": one,
            "per_fill": ((merges["pnl"] + out["exits"]["pnl"]) / one
                         if one else None)}

        # --- attribution: merges vs completions per market ----------------
        att = []
        for d in out["decisions_by_market"]:
            b = merges["by_market"].get(d["market"], {"n": 0})
            if b["n"] != d["completions"]:
                att.append({"market": d["market"], "merges": b["n"],
                            "completions": d["completions"]})
        out["attribution"] = att
    finally:
        c.close()
    return out


# --------------------------------------------------------------------------
# printing
# --------------------------------------------------------------------------

def _fmt_ts(ts: Optional[float]) -> str:
    if ts is None:
        return "n/a"
    import datetime
    return datetime.datetime.fromtimestamp(ts, datetime.UTC).strftime(
        "%m-%d %H:%M UTC")


def _print(rep: dict) -> None:
    p = rep["payoffs"]
    k = rep["kpi"]
    print(f"PAIRS-ONLY RULE -- EV REPORT (read-only)")
    print(f"  db          {rep['db']}")
    print(f"  rule era    {_fmt_ts(rep['rule_era_start_ts'])} +  "
          f"payoffs: complete +{p['complete_gain_cents']}c (S44/46) / "
          f"exit -{p['exit_cost_cents']}c (S45)")

    print("\n  KPI (mirrors strategy.stats.pairs_ev -- the dashboard tile)")
    print(f"    one-sided fills  {k['one_sided']}")
    print(f"    completions      {k['completions']}"
          + (f"  ({100.0 * k['completion_rate']:.1f}%)"
             if k["completion_rate"] is not None else ""))
    print(f"    exits            {k['exits']}"
          + (f"  ({100.0 * k['exit_rate']:.1f}%)"
             if k["exit_rate"] is not None else ""))
    print(f"    expired          {k['expired']}")
    print(f"    EV               {('%.3fc' % k['ev_cents']) if k['ev_cents'] is not None else 'n/a'}"
          f"   -> {k['verdict']}")

    print("\n  RULE DECISIONS BY MARKET")
    if rep["decisions_by_market"]:
        print(f"    {'market':<44} {'cmp':>4} {'ext':>4} {'exp':>4}")
        for d in rep["decisions_by_market"]:
            print(f"    {d['market'][:44]:<44} {d['completions']:>4} "
                  f"{d['exits']:>4} {d['expired']:>4}")
    else:
        print("    (no pairs-rule decisions recorded)")

    ex = rep["exits"]
    print("\n  NAKED EXITS (realized economics)")
    if ex["closes"]:
        for e in ex["closes"]:
            # avg_cost / per_share_c are None when shares is 0; exit_price is
            # None when a legacy row carries neither side price -- a format
            # spec on None aborts the whole report (coderabbit).
            avg = 'n/a' if e['avg_cost'] is None else f"{e['avg_cost']:.3f}"
            pps = ('n/a' if e['per_share_c'] is None
                   else f"{e['per_share_c']:6.2f}c/sh")
            xp = 'n/a' if e['exit_price'] is None else f"{e['exit_price']}"
            print(f"    {_fmt_ts(e['ts'])} {e['market'][:28]:<28} "
                  f"sh={e['shares']:6.1f} avg={avg} "
                  f"exit={xp} pnl={e['pnl']:7.2f} {pps}")
        print(f"    aggregate: {ex['n']} closes, {ex['shares']:.1f} sh, "
              f"${ex['pnl']:,.2f} = {ex['per_share_c']:.2f}c/sh"
              if ex["per_share_c"] is not None else
              f"    aggregate: {ex['n']} closes, {ex['shares']:.1f} sh")
    else:
        print("    (no naked exits yet)")

    xc = rep["exit_counterfactual"]
    print("\n  EXIT-VS-WAIT COUNTERFACTUAL (recorded 15m mid vs exit price, Session 50)")
    if xc["closes"]:
        for e in xc["closes"]:
            # exit_price is None when a close row has neither up_price nor
            # dn_price (legacy rows) -- guard before formatting (coderabbit).
            xp = 'n/a' if e['exit_price'] is None else f"{e['exit_price']:.3f}"
            line = (f"    {_fmt_ts(e['ts'])} {e['market'][:26]:<26} "
                    f"{e['side']:<4} sh={e['shares']:6.1f} "
                    f"exit={xp}")
            if e["status"] == "recorded":
                line += (f"  mid15={e['mid_h3']:.3f}  gap={e['gap_c']:+.2f}c  "
                         + ("exit BEAT waiting (mid kept falling)"
                            if e["gap_c"] is not None and e["gap_c"] < 0
                            else "waiting may have been better"))
            else:
                line += f"  -- {e['status']}"
            print(line)
        a = xc["aggregate"]
        if a:
            print(f"    recorded {a['n']}: {a['exit_beat_wait']} exits with the 15m mid "
                  f"BELOW the exit price (exit beat waiting), "
                  f"{a['wait_maybe_better']} above")
            print(f"    median gap (mid15 - exit) {a['median_gap_c']:+.2f}c/sh · "
                  f"mean 15m drift (mid15 - fill) {a['mean_15m_drift_c']:+.2f}c/sh")
        states = [f"{xc[s]} {s}" for s in ("pending", "no_markout", "no_fill",
                                           "no_column") if xc[s]]
        if states:
            print("    unrecorded: " + " · ".join(states))
    else:
        print("    (no naked exits yet)")

    mg = rep["merges"]
    d = mg["distribution"]
    print("\n  COMPLETED PAIRS -- MERGE CAPTURE (rule era)")
    print(f"    closes {mg['n']} · ${mg['pnl']:,.2f} · {mg['shares']:,.1f} sh"
          + (f" · {mg['per_share_c']:.2f}c/sh (dollar-weighted)"
             if mg["per_share_c"] is not None else ""))
    if d:
        lo, hi = d["iqr_fences"]
        print(f"    per-close rates: mean {d['mean']:.2f}c · median "
              f"{d['median']:.2f}c · p25 {d['p25']:.2f}c · p75 {d['p75']:.2f}c")
        print(f"    min {d['min']:.2f}c · max {d['max']:.2f}c · "
              f"{'ALL POSITIVE' if mg['all_positive'] else 'has non-positive closes'}"
              f" ({d['n']}/{d['n']})")
        if mg["outliers"]:
            print(f"    OUTLIERS (outside IQR fences {lo:.2f}..{hi:.2f}c): "
                  f"{len(mg['outliers'])}")
            for o in mg["outliers"]:
                print(f"      {o['market'][:44]:<44} {o['per_share_c']:6.2f}c/sh "
                      f"(${o['pnl']:,.2f} on {o['shares']:,.1f} sh)")
        else:
            print("    outliers: none")
    else:
        print("    (no rule-era merges yet)")

    if mg["by_market"]:
        print("    by market:")
        for b in mg["by_market"]:
            print(f"      {b['market'][:40]:<40} n={b['n']:>3} "
                  f"{b['per_share_c']:6.2f}c/sh  ${b['pnl']:>9,.2f}")

    rl = rep["realized"]
    print("\n  REALIZED EV")
    print(f"    completions ${rl['completions']:,.2f} · exits ${rl['exits']:,.2f} "
          f"= ${rl['total']:,.2f}"
          + (f" over {rl['one_sided']} one-sided fills = ${rl['per_fill']:.2f}/fill"
             if rl["per_fill"] is not None else ""))

    print("\n  ATTRIBUTION (merges vs completions, where they differ)")
    if rep["attribution"]:
        for a in rep["attribution"]:
            print(f"    {a['market'][:44]:<44} merges={a['merges']} "
                  f"completions={a['completions']}")
    else:
        print("    (1:1 everywhere -- no natural pairs detected in the slice)")

    print("\n  METHOD AND LIMITS")
    print("    * merge capture is the WHOLE pair's capture (includes the passive")
    print("      leg's spread); it is not the marginal cost of the crossed leg alone.")
    print("    * the EV denominator (rule decisions) and the closes are counted")
    print("      independently -- no fill-to-close join is invented.")
    print("    * the rule-era slice (ts >= first PAIR_COMPLETE) can include a natural")
    print("      pair; the attribution table above is the check.")
    print("    * the exit-wait join is window-based (10s close->fill, 30s fill->markout),")
    print("      not id-linked; mid_h3 is a MID, the exit sold at the BID, and fill+900s")
    print("      equals exit+900s only while exits fire at age ~0.")
    print("    * mid_h3 exists only after the fleet restarts (Session 50 migration);")
    print("      exits on an unmigrated DB read 'no_column', not a silent blank.")


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("db", nargs="?", default=str(DEFAULT_DB),
                   help="database to read (default: run/fleet.db)")
    p.add_argument("--json", default=None, help="write the report dict here")
    a = p.parse_args(argv)

    rep = report(Path(a.db))
    _print(rep)
    if a.json:
        import json
        # Explicit encoding: `write_text` otherwise takes the platform
        # default, which on this repo's Windows hosts is a legacy codepage.
        Path(a.json).write_text(json.dumps(rep, indent=2), encoding="utf-8")
        print(f"\nwrote {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
