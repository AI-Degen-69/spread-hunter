"""Replay the recorded near-miss log through a trial depth bar (U32).

WHY THIS EXISTS
---------------
The near-miss tracker reads `run/near_misses.jsonl` -- one line per rank, the
if-adopted greens embedded -- and has crossed every bar it needs to license a
CONTROLLED TRIAL (3/3 days, 29/25 unique markets, 19/5 small-margin depth,
0.694/0.5 stability). Its own verdict: adopt the small-margin greens, watch
markouts, and only loosen the gate if the trial's markouts are positive.

This script is the "show which markets a given bar would adopt" half of that
trial. It walks the RECORDED near-miss readings -- the depth each market
actually measured at rank time -- back through a trial bar and reports, per
bar, which depth-rejected markets would graduate and what pot is on the table.
It never touches the network, `run/markets.json`, or the config: the live
adoption is a separate, deliberate step (`python -m scripts.rank_markets
--trial-depth 750`), and this replay exists to show what that step would admit
BEFORE it is run.

WHAT GRADUATES
--------------
A green is a depth-rejected market whose if-adopted first-dollar marginal
return already cleared the allocator's floor at rank time (that is why it was
logged). A market GRADUATES at a trial bar when:

    depth_measured >= trial_bar          (its measured depth clears the new bar)
    AND not a mirage                     (see the trap rule below)

A market is a NEAR-MISS at the trial bar when 0.5 * bar <= depth_measured < bar
-- the market the dashboard's small-margin tile counts -- and a MIRAGE when its
depth is under half the bar or its estimate blew past 10%/day: the empty-book
shape the depth gate exists to catch, which must never read as opportunity.

THE TRAP RULE IS RE-DERIVED AGAINST THE TRIAL BAR
-------------------------------------------------
`server/fleet_dash.near_miss_stats` re-derives the mirage rule at read time
with the recorded bar (depth < 0.5 * recorded_bar). A trial bar is a DIFFERENT
bar, and the rule must answer the trial's question: a market at $400 depth is
a mirage against the permanent $1,000 bar (400 < 500) but a genuine near-miss
against a $750 trial (400 >= 375). Re-deriving with the trial bar is what lets
the sweep show how the opportunity set grows as the bar drops. This function
mirrors the dashboard's `_is_trap` -- keep the two in agreement.

WHAT THE RECORD CANNOT ANSWER -- stated, never papered over
-----------------------------------------------------------
  * DEPTH IS A RANK-TIME SNAPSHOT. The recorded `depth_measured` is one venue
    reading from when the market was rejected, not the fleet's 30-min average
    and not today's book. A market may have filled in or thinned out since.
  * POT IS A PROJECTION. `pot_day` is the venue's pot read at rank time; the
    income a graduate actually earns depends on competition when the fleet
    quotes. These are the same optimistic biases the tracker documents.
  * CONSISTENCY IS NOT PROFITABILITY. Graduating at a bar proves the market
    clears it on recorded evidence; it proves nothing about markout until the
    fleet trades it. That is exactly why adoption is staged: watch the trial
    markets' markouts (the dashboard's per-market tiles), and only a positive
    trial markout justifies making the bar permanent.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RUN = ROOT / "run"
DEFAULT_NM = RUN / "near_misses.jsonl"

# Same suffix the ranker's `_cause` produces for the depth gate, and the same
# string the dashboard's near-miss tile keys on.
_DEPTH_CAUSE = "top-3 bid depth"

# The permanent bar and the bars the sweep compares, sourced from config so a
# config change cannot silently leave this report quoting an old contract.
from strategy.config import load as load_cfg        # noqa: E402

_CFG = load_cfg()
PERMANENT_BAR = _CFG.select_min_top3_depth_usd
SWEEP_BARS = (500.0, 750.0, 1000.0)


def _is_trap(g: dict, bar: float) -> bool:
    """The near-miss mirage rule, re-derived against the trial bar.

    Mirrors `server/fleet_dash.near_miss_stats._is_trap` with `depth_bar`
    replaced by the bar under test -- see the module docstring for why the
    rule has to answer the trial's question, not the permanent bar's.
    """
    d = g.get("depth_measured")
    # `is not None`, not truthiness: a fully empty book parses depth "$0.00"
    # -> 0.0, which is falsy and MUST still count as a trap -- it is the exact
    # empty-book shape the rule exists to catch.
    if d is not None and d < 0.5 * bar:
        return True
    return (g.get("marg_pct_day") or 0.0) > 10.0


def _load_greens(nm_path: Path) -> tuple[list[dict], int]:
    """(greens with their rank timestamp attached, number of ranks read).

    Same read as the dashboard's near-miss stats: one line per rank, greens
    embedded, malformed lines skipped rather than fatal. A green carries no
    timestamp of its own, so the rank's `ts` is attached (the dashboard does
    the same) for last-reading-per-market dedup.
    """
    greens: list[dict] = []
    ranks = 0
    if nm_path.exists():
        try:
            with open(nm_path, encoding="utf-8") as fh:
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
                    ranks += 1
                    ts = row.get("ts")
                    for g in row.get("greens") or []:
                        if not isinstance(g, dict):
                            continue
                        g = dict(g)
                        g["ts"] = ts
                        greens.append(g)
        except Exception:
            pass
    return greens, ranks


def trial_depth_report(nm_path: Path, bar: float,
                       permanent_bar: float = PERMANENT_BAR) -> dict:
    """Which depth-rejected markets the trial bar would adopt, from the record.

    Pure over the recorded log -- no network, no writes. `permanent_bar` is
    injectable so a test can exercise the sweep without importing config.
    """
    greens, ranks = _load_greens(nm_path)
    # LAST READING PER MARKET: the near-miss log accumulates daily, so one
    # market appears across many ranks. Its newest reading is the one that
    # describes it now (same dedup the dashboard's pot tile uses). This is a
    # deliberately stricter basis than the dashboard's small-margin tile,
    # which counts ANY credible reading across all ranks (all-time evidence
    # for "a loosening would admit something"); the trial answers "what
    # would a loosening admit RIGHT NOW", which is a last-reading question.
    last: dict[str, dict] = {}
    for g in greens:
        cid = g.get("cid")
        if not cid:
            continue
        prev = last.get(cid)
        if prev is None or (g.get("ts") or 0) >= (prev.get("ts") or 0):
            last[cid] = g

    depth_rejects = [g for g in last.values()
                     if (g.get("cause") or "").endswith(_DEPTH_CAUSE)]
    depth_unparsed = sum(1 for g in depth_rejects
                         if g.get("depth_measured") is None)

    def _grade(g: dict, b: float) -> str:
        """'graduate' | 'near' | 'mirage' | 'below' at bar b."""
        d = g.get("depth_measured")
        if d is None:
            return "below"
        if _is_trap(g, b):
            return "mirage"
        if d >= b:
            return "graduate"
        if d >= 0.5 * b:
            return "near"
        return "below"

    def _row(g: dict) -> dict:
        return {
            "cid": g.get("cid"), "title": g.get("title"),
            "slug": g.get("slug"), "cause": g.get("cause"),
            "reason": g.get("reason"),
            "depth_measured": g.get("depth_measured"),
            "depth_bar": g.get("depth_bar"),
            "pot_day": g.get("pot_day"), "marg_pct_day": g.get("marg_pct_day"),
            "days": g.get("days"), "volume_24h": g.get("volume_24h"),
            "ts": g.get("ts"),
        }

    # The sweep always shows the standard bars, plus the bar under test when
    # it is not one of them -- a custom --bar must be able to see itself.
    sweep_bars = sorted(set(SWEEP_BARS) | {bar}, reverse=True) if bar > 0 \
        else sorted(SWEEP_BARS, reverse=True)

    def _sweep_at(b: float) -> dict:
        grads = [g for g in depth_rejects if _grade(g, b) == "graduate"]
        nears = [g for g in depth_rejects if _grade(g, b) == "near"]
        mirages = [g for g in depth_rejects if _grade(g, b) == "mirage"]
        return {
            "graduates": len(grads),
            "graduate_pot_day": round(sum(g.get("pot_day") or 0.0
                                          for g in grads), 2),
            "near": len(nears),
            "near_pot_day": round(sum(g.get("pot_day") or 0.0
                                      for g in nears), 2),
            "mirages": len(mirages),
        }

    bar_ok = bar > 0
    if not nm_path.exists():
        report = {
            "nm_path": str(nm_path), "exists": False, "bar": bar,
            "ranks": 0, "greens_seen": 0, "unique_markets": 0,
            "depth_rejects_unique": 0, "depth_unparsed": 0,
            "graduates": [], "n_graduates": 0,
            "graduate_pot_day": 0.0,
            "near_misses_at_bar": [], "n_near_misses_at_bar": 0,
            "near_pot_day_at_bar": 0.0,
            "sweep": {f"{b:.0f}": _sweep_at(b) for b in sweep_bars},
            "notes": [f"{nm_path}: no such file -- nothing replayed. This is "
                      "an absence of data, not a zero result."],
        }
        return report

    grads = [g for g in depth_rejects if bar_ok and _grade(g, bar) == "graduate"]
    nears = [g for g in depth_rejects if bar_ok and _grade(g, bar) == "near"]
    grads.sort(key=lambda g: -(g.get("pot_day") or 0.0))
    nears.sort(key=lambda g: -(g.get("pot_day") or 0.0))

    notes = [
        "depth is a rank-time snapshot (one venue reading), not the fleet's "
        "30-min average and not today's book.",
        "pot_day is the venue's pot read at rank time; income is a "
        "projection, and consistency is not profitability.",
        "trap rule re-derived against the trial bar (mirror of "
        "server/fleet_dash.near_miss_stats._is_trap).",
        "grading uses the LAST reading per market; the dashboard's "
        "small-margin tile counts ANY credible reading (all-time evidence), "
        "so its count can exceed the replay's graduates.",
        "adoption is staged: tag trial markets (trial_depth_usd in "
        "run/markets.json), watch their markouts, and only a positive trial "
        "markout justifies making the bar permanent.",
        "a graduate's depth and pot are rank-time readings that may describe "
        "an already-resolved market (the recorded greens carry no end date; "
        "the days column shows '?' when unrecorded). Verify each graduate "
        "against the live venue before adopting -- the replay proves the "
        "gate loosening admits markets, not that they are still open.",
    ]
    if depth_unparsed:
        notes.append(f"{depth_unparsed} depth-reject green(s) had an "
                     "unparseable depth and could not be graded -- the "
                     "tracker's depth_unparsed count, surfaced here too.")

    return {
        "nm_path": str(nm_path), "exists": True, "bar": bar,
        "permanent_bar": permanent_bar,
        "ranks": ranks, "greens_seen": len(greens),
        "unique_markets": len(last), "depth_rejects_unique": len(depth_rejects),
        "depth_unparsed": depth_unparsed,
        "graduates": [_row(g) for g in grads],
        "n_graduates": len(grads),
        "graduate_pot_day": round(sum(g.get("pot_day") or 0.0 for g in grads),
                                  2),
        "near_misses_at_bar": [_row(g) for g in nears],
        "n_near_misses_at_bar": len(nears),
        "near_pot_day_at_bar": round(sum(g.get("pot_day") or 0.0
                                         for g in nears), 2),
        "sweep": {f"{b:.0f}": _sweep_at(b) for b in sweep_bars},
        "notes": notes,
    }


def _print(rep: dict) -> None:
    bar = rep["bar"]
    print(f"\n=== depth-gate trial replay: {rep['nm_path']} @ ${bar:,.0f} ===")
    if not rep["exists"]:
        for n in rep["notes"]:
            print(f"  note: {n}")
        return
    print(f"  ranks {rep['ranks']}   greens seen {rep['greens_seen']}   "
          f"unique markets {rep['unique_markets']}   "
          f"depth-reject markets {rep['depth_rejects_unique']}")
    if rep["depth_unparsed"]:
        print(f"  WARNING: {rep['depth_unparsed']} depth-reject green(s) "
              "had an unparseable depth and could not be graded")

    grads = rep["graduates"]
    print(f"\n  TRIAL GRADUATES at ${bar:,.0f} -- depth-rejected markets whose "
          f"measured depth clears the bar")
    if not grads:
        print("    (none)")
    else:
        print(f"    {'market':<46} {'depth':>8} {'pot $/day':>10} "
              f"{'marg%/d':>8} {'days':>5}")
        for g in grads:
            title = (g.get("title") or "")[:46]
            title = title.encode("ascii", "replace").decode("ascii")
            days = g.get("days")
            # A missing horizon renders as "?", never as 0.0 -- "resolves
            # today" is a claim the record did not make.
            days_s = f"{days:>5.1f}" if days is not None else "    ?"
            print(f"    {title:<46} ${g['depth_measured']:>7,.0f} "
                  f"{g.get('pot_day') or 0.0:>10,.2f} "
                  f"{g.get('marg_pct_day') or 0.0:>8,.2f} {days_s}")
    print(f"\n  projected pot on the table: ${rep['graduate_pot_day']:,.2f}/day "
          f"across {rep['n_graduates']} markets")

    nears = rep["near_misses_at_bar"]
    print(f"\n  NEAR MISSES at ${bar:,.0f} (0.5*bar <= depth < bar): "
          f"{rep['n_near_misses_at_bar']} markets, "
          f"${rep['near_pot_day_at_bar']:,.2f}/day")

    print("\n  BAR SWEEP (graduates / $day pot on the table)")
    for b, s in sorted(rep["sweep"].items(), key=lambda kv: -float(kv[0])):
        flag = "  <- trial" if float(b) == bar else ""
        print(f"    ${float(b):>6,.0f}: {s['graduates']:>3} / "
              f"${s['graduate_pot_day']:>10,.2f}   "
              f"(near {s['near']}, mirages {s['mirages']}){flag}")
    print(f"    permanent bar (config): ${rep.get('permanent_bar', PERMANENT_BAR):,.0f}")

    print("\n  METHOD AND LIMITS")
    for n in rep["notes"]:
        print(f"    * {n}")


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--bar", type=float, default=750.0,
                   help=f"trial depth bar in USD (default: 750; permanent "
                        f"config bar is {PERMANENT_BAR:,.0f})")
    p.add_argument("--nm", default=str(DEFAULT_NM),
                   help="near-miss log to replay (default: run/near_misses.jsonl)")
    p.add_argument("--json", default=None, help="write the report dict here")
    a = p.parse_args(argv)

    rep = trial_depth_report(Path(a.nm), a.bar)
    _print(rep)
    if a.json:
        # Explicit encoding: the dashboard reads this file, and a legacy
        # codepage write would make its json.load fail on any non-ASCII title.
        Path(a.json).write_text(json.dumps(rep, indent=2), encoding="utf-8")
        print(f"\nwrote {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
