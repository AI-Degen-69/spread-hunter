"""Replay recorded fills through the dollar-denominated gates (U7, R11/R12).

WHY THIS EXISTS
---------------
Every gate in `strategy/risk.py` was written from a forensic reading of one
run: 233.40 UP shares on `lol-maz-mg1` at an average of 0.8152, $190.26 at
risk, against three armed limits none of which bound. A gate justified by a
post-mortem is a hypothesis. This script is the test of it -- it walks the
recorded fills back through `risk.hard_block` and reports, per gate, what
would not have been bought.

THE NUMBER THAT DECIDES
-----------------------
Naked cost avoided must EXCEED realized P&L forgone. A gate set that refuses
more profitable flow than toxic flow has failed even with a green suite, and
the plan's stop condition is stated on the same reading: if the gates would
have blocked more than half of the fills that produced the recorded realized
P&L, they are cutting the wrong flow and the thresholds need rework.

WHAT THE RECORD CANNOT ANSWER -- stated, never papered over
-----------------------------------------------------------
  * NO BOOK DEPTH. `quotes` carries a mid and no ladder, so the too-thin arm
    of `book_health` cannot run. It is reported UNEVALUATED, not passed.
    `BookHealth.depth_evaluated` exists for exactly this distinction.
  * NO PER-FILL REALIZED P&L. `closes` books realized money per MARKET
    (`realized_pnl`), and there is no column that splits it across the fills
    that built the position. What is reported is an ATTRIBUTION -- a market's
    realized P&L apportioned across its refused fills by cost share -- and it
    is labelled as one everywhere it appears.
  * NO RECORDED SPREAD. A book is reconstructed as mid +/- half of
    `cfg.tick_size` (the measured 1c median book), with the hedge token at
    1 - mid. Health arms that turn on the spread therefore read the assumed
    spread, not the observed one.

TWO READINGS, KEPT APART
------------------------
`hard_block` exempts any order that REDUCES exposure (R4), so on the live path
the light side is never refused whatever the rule says. That is correct
behavior and it hides evidence: the 14 pairs `wta-kalinsk-kessler` assembled
at $1.0200 were bought on the light side, so the pair-cost rule fires on them
as a RULE while R4 exempts them on the PATH. Both are reported:

    per gate   -- what that rule alone would have caught, R4 ignored
    headline   -- what `hard_block` would actually have refused, R4 honored

Inventory follows the RECORDED path (a refused fill is still added), because
the question asked at each fill is "which limit would have refused the order
that produced this?", and answering it needs the position the fleet really
held.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategy import risk                              # noqa: E402
from strategy.config import load as load_cfg           # noqa: E402
from strategy.quotes import Inventory                  # noqa: E402
from strategy.store import BUSY_TIMEOUT_SEC            # noqa: E402

# Order matters: it is `hard_block`'s own arm order, cheapest and most certain
# first, so a report row and a blocked reason name the same gate.
GATES = ("hedge_book", "own_book", "dollar_cap", "price_band", "pair_cost")

NOTE_DEPTH = ("book depth: UNEVALUATED -- `quotes` records a mid and no "
              "ladder, so `book_health`'s too-thin arm never ran. Not a pass.")
NOTE_PNL = ("realized P&L: no per-fill column exists. `closes.realized_pnl` "
            "is per MARKET and is ATTRIBUTED across that market's refused "
            "fills by cost share.")
NOTE_SPREAD = ("book shape: reconstructed as mid +/- half tick with the hedge "
               "token at 1 - mid. The recorded spread is not in the data.")


# --------------------------------------------------------------------------
# reading (read-only; this script never writes to the database)
# --------------------------------------------------------------------------

def _connect(path: Path) -> sqlite3.Connection:
    """A read-only handle, following `strategy/store.py` for the timeout.

    `mode=ro` is the enforcement, not a convention: the fleet may be writing
    this file while the replay reads it, and a replay that could take a write
    lock would be able to stall the trading loop it is measuring. WAL
    databases occasionally refuse a URI-readonly open when the -shm file is
    missing, so `query_only` is the fallback -- same guarantee, weaker
    mechanism.
    """
    try:
        c = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True,
                            timeout=BUSY_TIMEOUT_SEC)
        c.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchall()
        return c
    except sqlite3.Error:
        c = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_SEC)
        c.execute("PRAGMA query_only = 1")
        return c


def _rows(c: sqlite3.Connection, sql: str) -> list[tuple]:
    """Query, or an empty result if the table is not there.

    A database with no `fills` table is a database nothing has been recorded
    into yet. That is a zero, not an error -- the tables are created lazily by
    `store._conn`, so a fresh file legitimately has none of them.
    """
    try:
        return c.execute(sql).fetchall()
    except sqlite3.OperationalError:
        return []


def _load(path: Path) -> tuple[list[dict], dict[str, float], dict]:
    """Fills in time order, realized P&L per market, and mid lookups."""
    c = _connect(path)
    try:
        fills = [
            {"ts": r[0], "slug": r[1] or "(unknown)", "cond": r[2],
             "side": r[3], "price": float(r[4] or 0.0),
             "size": float(r[5] or 0.0), "mid_at_post": r[6],
             "quote_id": r[7]}
            for r in _rows(c, "SELECT ts, market_slug, condition_id, side, "
                              "price, size, mid_at_post, quote_id FROM fills "
                              "ORDER BY ts")
        ]
        pnl: dict[str, float] = {}
        for slug, amt in _rows(c, "SELECT market_slug, "
                                  "COALESCE(SUM(realized_pnl), 0) FROM closes "
                                  "GROUP BY market_slug"):
            pnl[slug or "(unknown)"] = float(amt or 0.0)

        by_id = {r[0]: r[1] for r in
                 _rows(c, "SELECT id, mid FROM quotes WHERE mid IS NOT NULL")}
        # Fallback lookup: the mid of the nearest quote on the same token,
        # for fills whose quote row is gone or whose quote_id was never set.
        by_side: dict[tuple, list[tuple[float, float]]] = {}
        for cond, side, ts, mid in _rows(
                c, "SELECT condition_id, side, ts, mid FROM quotes "
                   "WHERE mid IS NOT NULL ORDER BY ts"):
            by_side.setdefault((cond, side), []).append((float(ts), float(mid)))
    finally:
        c.close()
    return fills, pnl, {"by_id": by_id, "by_side": by_side}


def _mid_for(fill: dict, mids: dict) -> tuple[float, str]:
    """The best available mid for this fill, and which source gave it."""
    if fill["mid_at_post"] is not None:
        return float(fill["mid_at_post"]), "fill.mid_at_post"
    q = mids["by_id"].get(fill["quote_id"])
    if q is not None:
        return float(q), "quotes.mid (quote_id)"
    series = mids["by_side"].get((fill["cond"], fill["side"]))
    if series:
        ts = fill["ts"]
        near = min(series, key=lambda r: abs(r[0] - ts))
        return near[1], "quotes.mid (nearest)"
    # The fill price itself. Weakest of the four and counted separately, so a
    # report carried by this fallback can be seen to be carried by it.
    return fill["price"], "fill.price (no mid recorded)"


def _books(cfg, mid: float) -> tuple[dict, dict]:
    """One token's book and its hedge, from a single mid. No depth key."""
    h = cfg.tick_size / 2.0
    own = {"best_bid": max(0.0, mid - h), "best_ask": min(1.0, mid + h)}
    o = 1.0 - mid
    hedge = {"best_bid": max(0.0, o - h), "best_ask": min(1.0, o + h)}
    return own, hedge


# --------------------------------------------------------------------------
# the replay
# --------------------------------------------------------------------------

@dataclass
class GateTally:
    fills: int = 0
    naked_cost_avoided: float = 0.0
    realized_pnl_forgone: float = 0.0


@dataclass
class MarketTally:
    fills: int = 0
    shares: float = 0.0
    cost: float = 0.0
    refused_fills: int = 0
    refused_cost: float = 0.0
    naked_cost_avoided: float = 0.0
    gates: dict = field(default_factory=dict)
    # Fills each gate fired on, beside the dollars it saved. Both are needed:
    # a rule that fires on an exposure-REDUCING fill saves no naked dollars at
    # all, and $0.00 on its own reads as "never fired". That is not a corner
    # case -- it is `wta-kalinsk-kessler`, whose 14 pairs at $1.0200 were
    # bought on the light side, which is the only recorded evidence for the
    # pair-cost rule.
    gate_fills: dict = field(default_factory=dict)
    end_naked_usd: float = 0.0


def _naked_cost(inv: Inventory) -> float:
    """Dollars at risk across both legs -- 0 when flat or balanced."""
    side = risk.naked_side(inv)
    return 0.0 if side is None else risk.naked_usd(inv, side)


def _arms(cfg, inv: Inventory, side: str, price: float, own: dict,
          hedge: dict, added: float, over_before: float, over_after: float
          ) -> dict[str, float]:
    """Every gate that would refuse this fill, with the dollars it saves.

    Each arm is evaluated ON ITS OWN rather than short-circuited, because a
    single first-binding answer cannot say what any individual rule is worth:
    on `lol-maz-mg1` the price band refuses both fills outright, and reporting
    only that would leave the dollar cap -- the rule the plan is actually
    sizing -- with no measured effect at all.
    """
    out: dict[str, float] = {}
    if not risk.book_health(hedge, cfg).ok:
        out["hedge_book"] = added
    if not risk.book_health(own, cfg).ok:
        out["own_book"] = added
    # The cap is the one arm that binds PART WAY THROUGH a fill: it is a
    # dollar budget, not a permission slip per order, so what it saves is the
    # naked cost above the budget rather than the whole fill.
    if cfg.max_naked_usd > 0 and over_after > over_before:
        out["dollar_cap"] = over_after - over_before
    if cfg.enforce_price_band and not (
            cfg.price_band_low <= price <= cfg.price_band_high):
        out["price_band"] = added
    other_avg = inv.avg(risk.OTHER[side])
    if other_avg > 0 and (price + other_avg) >= cfg.max_pair_cost:
        out["pair_cost"] = added
    return out


def replay(db_path, cfg=None) -> dict:
    """Walk the recorded fills through the gates. Returns the report dict.

    Read-only. Nothing here writes, and nothing here mutates `strategy/`.
    """
    cfg = cfg or load_cfg()
    path = Path(db_path)
    rep: dict = {
        "db": str(path), "exists": path.exists(), "fills": 0, "markets": 0,
        "refused_fills": 0, "naked_cost_avoided": 0.0,
        "realized_pnl_forgone": 0.0, "realized_pnl_total": 0.0,
        "realized_pnl_available": False, "unhedged_cost_end": 0.0,
        "unhedged_cost_avoided": 0.0, "profitable_market_fills": 0,
        "profitable_market_fills_refused": 0,
        "stop_condition_triggered": False, "depth_arm": "UNEVALUATED",
        "depth_evaluations": 0, "health_evaluations": 0,
        "by_gate": {}, "by_market": {}, "mid_source": {},
        "notes": [NOTE_DEPTH, NOTE_PNL, NOTE_SPREAD],
    }
    if not path.exists():
        rep["notes"].insert(0, f"{path}: no such file -- nothing replayed. "
                               "This is an absence of data, not a zero result.")
        return rep

    fills, pnl, mids = _load(path)
    rep["realized_pnl_total"] = sum(pnl.values())
    rep["realized_pnl_available"] = bool(pnl)
    if not pnl:
        rep["notes"].append("realized P&L: `closes` is empty -- nothing has "
                            "been realized, so nothing can be forgone.")
    if not fills:
        rep["notes"].insert(0, f"{path}: 0 fills recorded -- nothing to "
                               "replay. Not a verdict on the gates.")
        return rep

    inv: dict[str, Inventory] = {}
    mkt: dict[str, MarketTally] = {}
    gates: dict[str, GateTally] = {}
    # Per-fill detail, kept so P&L can be attributed after every market's
    # total fill cost is known -- a share cannot be computed on the way past.
    detail: list[dict] = []

    for f in fills:
        slug, side, price, size = f["slug"], f["side"], f["price"], f["size"]
        cost = size * price
        iv = inv.setdefault(slug, Inventory())
        m = mkt.setdefault(slug, MarketTally())
        m.fills += 1
        m.shares += size
        m.cost += cost

        mid, src = _mid_for(f, mids)
        rep["mid_source"][src] = rep["mid_source"].get(src, 0) + 1
        own, hedge = _books(cfg, mid)

        for bk in (hedge, own):
            h = risk.book_health(bk, cfg)
            rep["health_evaluations"] += 1
            if h.depth_evaluated:
                rep["depth_evaluations"] += 1

        naked_before = _naked_cost(iv)
        live = risk.hard_block(cfg, iv, side, price, own, hedge)

        after = Inventory(iv.up_shares, iv.down_shares, iv.up_cost,
                          iv.down_cost, iv.fills)
        if side == "UP":
            after.up_shares += size
            after.up_cost += cost
        else:
            after.down_shares += size
            after.down_cost += cost
        naked_after = _naked_cost(after)
        # What this fill ADDED to the naked leg. A light-side fill flattens the
        # position, so it adds nothing -- refusing it would not avoid a dollar.
        added = max(0.0, naked_after - naked_before)
        budget = cfg.max_naked_usd
        over_before = max(0.0, naked_before - budget) if budget > 0 else 0.0
        over_after = max(0.0, naked_after - budget) if budget > 0 else 0.0

        fired = _arms(cfg, iv, side, price, own, hedge, added,
                      over_before, over_after)
        for g, usd in fired.items():
            t = gates.setdefault(g, GateTally())
            t.fills += 1
            t.naked_cost_avoided += usd
            m.gates[g] = m.gates.get(g, 0.0) + usd
            m.gate_fills[g] = m.gate_fills.get(g, 0) + 1

        if live is not None:
            m.refused_fills += 1
            m.refused_cost += cost
            m.naked_cost_avoided += added
            rep["refused_fills"] += 1
            rep["naked_cost_avoided"] += added

        detail.append({"slug": slug, "cost": cost, "refused": live is not None,
                       "gates": fired, "reason": live})

        # The recorded path, refused or not: the next fill's question is about
        # the position the fleet actually held.
        iv.up_shares, iv.up_cost = after.up_shares, after.up_cost
        iv.down_shares, iv.down_cost = after.down_shares, after.down_cost
        iv.fills += 1

    # --- realized P&L: attributed, per the note, never invented ------------
    for d in detail:
        realized = pnl.get(d["slug"], 0.0)
        total_cost = mkt[d["slug"]].cost
        if realized <= 0 or total_cost <= 0:
            continue
        share = d["cost"] / total_cost
        if d["refused"]:
            rep["realized_pnl_forgone"] += realized * share
        for g in d["gates"]:
            gates[g].realized_pnl_forgone += realized * share

    for slug, m in mkt.items():
        m.end_naked_usd = _naked_cost(inv[slug])
        rep["unhedged_cost_end"] += m.end_naked_usd
        if m.end_naked_usd > 0:
            rep["unhedged_cost_avoided"] += m.naked_cost_avoided
        if pnl.get(slug, 0.0) > 0:
            rep["profitable_market_fills"] += m.fills
            rep["profitable_market_fills_refused"] += m.refused_fills

    rep["fills"] = len(fills)
    rep["markets"] = len(mkt)
    rep["depth_arm"] = ("EVALUATED" if rep["depth_evaluations"]
                        else "UNEVALUATED")
    rep["by_gate"] = {
        g: {"fills": gates[g].fills,
            "naked_cost_avoided": gates[g].naked_cost_avoided,
            "realized_pnl_forgone": gates[g].realized_pnl_forgone}
        for g in GATES if g in gates}
    rep["by_market"] = {
        s: {"fills": m.fills, "shares": m.shares, "cost": m.cost,
            "refused_fills": m.refused_fills, "refused_cost": m.refused_cost,
            "naked_cost_avoided": m.naked_cost_avoided,
            "realized_pnl": pnl.get(s, 0.0),
            "end_naked_usd": m.end_naked_usd,
            "gates": dict(m.gates), "gate_fills": dict(m.gate_fills)}
        for s, m in mkt.items()}

    n = rep["profitable_market_fills"]
    rep["stop_condition_triggered"] = bool(
        n and rep["profitable_market_fills_refused"] > n / 2.0)
    return rep


# --------------------------------------------------------------------------
# reporting -- a number with its method beside it (research/RESEARCH_LOG.md)
# --------------------------------------------------------------------------

def _print(rep: dict) -> None:
    print(f"\n=== risk-gate replay: {rep['db']} ===")
    if not rep["exists"]:
        print("  DATABASE ABSENT -- nothing replayed.")
        for n in rep["notes"]:
            print(f"    note: {n}")
        return
    print(f"  fills {rep['fills']}   markets {rep['markets']}   "
          f"refused {rep['refused_fills']}"
          + (f" ({100.0 * rep['refused_fills'] / rep['fills']:.1f}%)"
             if rep["fills"] else ""))
    if not rep["fills"]:
        for n in rep["notes"]:
            print(f"    note: {n}")
        return

    print(f"  book depth arm           {rep['depth_arm']}  "
          f"({rep['depth_evaluations']}/{rep['health_evaluations']} health "
          f"checks reached it)")
    print("  mid source: " + ", ".join(
        f"{k} x{v}" for k, v in sorted(rep["mid_source"].items())))

    print("\n  PER GATE (each rule alone, R4 exemption ignored)")
    print(f"    {'gate':<12} {'fills':>6} {'naked $ avoided':>17} "
          f"{'realized $ forgone':>19}")
    for g in GATES:
        e = rep["by_gate"].get(g)
        if not e:
            continue
        print(f"    {g:<12} {e['fills']:>6} {e['naked_cost_avoided']:>17,.2f} "
              f"{e['realized_pnl_forgone']:>19,.2f}")
    if not rep["by_gate"]:
        print("    (no gate fired on any recorded fill)")

    print("\n  LIVE PATH (`hard_block`, exposure-reducing orders exempt)")
    print(f"    naked cost avoided       ${rep['naked_cost_avoided']:,.2f}")
    print(f"    realized P&L forgone     ${rep['realized_pnl_forgone']:,.2f}"
          f"   of ${rep['realized_pnl_total']:,.2f} recorded  [ATTRIBUTED]")
    verdict = ("PASS" if rep["naked_cost_avoided"] > rep["realized_pnl_forgone"]
               else "FAIL")
    print(f"    exit criterion           {verdict} -- avoided must exceed "
          f"forgone")
    print(f"    unhedged cost left in the record  "
          f"${rep['unhedged_cost_end']:,.2f}  "
          f"(of which refused: ${rep['unhedged_cost_avoided']:,.2f})")

    n, r = rep["profitable_market_fills"], rep["profitable_market_fills_refused"]
    print(f"\n  STOP CONDITION: fills in markets that realized P&L, refused "
          f"{r}/{n}"
          + (f" ({100.0 * r / n:.1f}%)" if n else "")
          + f"  -> {'TRIGGERED' if rep['stop_condition_triggered'] else 'clear'}")

    print("\n  PER MARKET (by naked cost avoided)")
    print(f"    {'market':<44} {'fills':>5} {'cost':>9} {'avoided':>9} "
          f"{'naked end':>10} {'realized':>9}  gates")
    for s, m in sorted(rep["by_market"].items(),
                       key=lambda kv: -kv[1]["naked_cost_avoided"]):
        gl = " ".join(f"{g}({m['gate_fills'][g]}f/${v:.0f})" for g, v in sorted(
            m["gates"].items(), key=lambda kv: -kv[1]))
        print(f"    {s[:44]:<44} {m['fills']:>5} {m['cost']:>9,.2f} "
              f"{m['naked_cost_avoided']:>9,.2f} {m['end_naked_usd']:>10,.2f} "
              f"{m['realized_pnl']:>9,.2f}  {gl}")

    print("\n  METHOD AND LIMITS")
    for note in rep["notes"]:
        print(f"    * {note}")


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("db", nargs="?", default=str(ROOT / "maker.db"),
                   help="database to replay (default: maker.db)")
    p.add_argument("--json", default=None, help="write the report dict here")
    a = p.parse_args(argv)

    rep = replay(Path(a.db))
    _print(rep)
    if a.json:
        import json
        # Explicit encoding: `write_text` otherwise takes the platform
        # default, which on this repo's Windows hosts is a legacy codepage.
        # One market slug or note carrying a non-ASCII character then raises
        # UnicodeEncodeError after the entire replay has already run.
        Path(a.json).write_text(json.dumps(rep, indent=2), encoding="utf-8")
        print(f"\nwrote {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
