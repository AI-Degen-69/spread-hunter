"""Triage the depth-reject population: mirages vs near-misses vs graduates (U33).

WHY THIS EXISTS
---------------
U32's replay showed the depth-reject population is dominated by mirages -- at
the permanent bar, 91 of the 98 recorded depth-reject markets grade as
empty-book traps (depth under half the bar, or an estimate past 10%/day). The
near-miss log therefore carries mostly noise, and the tracker's small-margin
tiles count readings that a looser gate would never adopt anyway.

This script answers, offline and from the recorded log only:

  1. WHAT the 98 depth-reject markets are made of -- the distribution of
     depth ratio, 24h volume, pot, and reading count across the population.
  2. HOW mirages differ from genuine near-misses and graduates on the signals
     the log records (volume, reading consistency, depth ratio).
  3. WHETHER the live pipeline's volume gate would have excluded them anyway.
     The live ranker gates on 24h volume >= $250,000 BEFORE depth (config
     `select_min_volume_24h_usd`) -- a market can clear a depth bar on
     recorded evidence and still fail the live run on volume, which is
     exactly what U32's live re-verification found. This cross-tab makes the
     gate interaction visible instead of leaving it implied.
  4. WHICH SHARPENING SIGNALS would filter mirages before they reach the
     near-miss log without excluding the near-misses -- measured as a
     precision/recall table over the recorded population, so a proposed
     change to the depth check is argued with numbers, not taste.

Pure over the recorded log -- no network, no writes, no config mutation.

THE TRAP RULE
-------------
Imported from `scripts.trial_depth_gate._is_trap` (which mirrors
`server/fleet_dash.near_miss_stats._is_trap`) so the two tools cannot drift:
depth < 0.5 * bar, or if-adopted marginal estimate past 10%/day. A market
grading 'below' at every bar (depth under half the bar even at $500) is the
empty-book shape the depth gate exists to catch.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from statistics import median
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.trial_depth_gate import _is_trap, _DEPTH_CAUSE  # noqa: E402

RUN = ROOT / "run"
DEFAULT_NM = RUN / "near_misses.jsonl"

from strategy.config import load as load_cfg  # noqa: E402

_CFG = load_cfg()
PERMANENT_BAR = _CFG.select_min_top3_depth_usd
VOLUME_BAR = _CFG.select_min_volume_24h_usd
BARS = (500.0, 750.0, 1000.0)


def _load_ranks(nm_path: Path) -> tuple[list[dict], int]:
    """(rank rows with their greens, number of rank lines read).

    Same tolerant read as `trial_depth_gate._load_greens` -- one line per
    rank, malformed lines skipped, the rank's `ts` attached to each green.
    Returns the rank rows themselves so per-market reading counts can be
    built (the flatter green list loses how many ranks saw a market).
    """
    ranks: list[dict] = []
    if nm_path.exists():
        with open(nm_path, encoding="utf-8") as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    row = json.loads(ln)
                except (ValueError, json.JSONDecodeError):
                    continue
                if not isinstance(row, dict) or "greens" not in row:
                    continue
                ts = row.get("ts")
                greens = []
                for g in row.get("greens") or []:
                    if not isinstance(g, dict):
                        continue
                    g = dict(g)
                    g["ts"] = ts
                    greens.append(g)
                ranks.append({"ts": ts, "greens": greens})
    return ranks, len(ranks)


def _per_market(ranks: list[dict]) -> dict[str, dict]:
    """cid -> aggregate of every recorded depth-reject green for that market.

    Keeps ALL readings (not just the last) so the analysis can separate
    one-shot mirages from markets the ranker kept seeing, and so an
    any-reading basis can be compared with the last-reading one.
    """
    markets: dict[str, dict] = {}
    for rank in ranks:
        for g in rank.get("greens") or []:
            if not (g.get("cause") or "").endswith(_DEPTH_CAUSE):
                continue
            cid = g.get("cid")
            if not cid:
                continue
            m = markets.setdefault(cid, {
                "cid": cid, "title": g.get("title"), "slug": g.get("slug"),
                "readings": [],
            })
            m["readings"].append(g)
    for m in markets.values():
        readings = m["readings"]
        # The log is append-only, but never rely on FILE order for "last":
        # sort by rank timestamp so a rewritten or reordered log cannot
        # silently change which reading describes the market (same basis as
        # trial_depth_gate's ts-dedup).
        readings.sort(key=lambda r: r.get("ts") or 0)
        m["last"] = readings[-1]          # a real green, for `_grade`
        m["n_readings"] = len(readings)
        vols = [r.get("volume_24h") for r in readings
                if r.get("volume_24h") is not None]
        depths = [r.get("depth_measured") for r in readings
                  if r.get("depth_measured") is not None]
        m["volume_median"] = median(vols) if vols else None
        m["volume_last"] = m["last"].get("volume_24h")
        m["depth_median"] = median(depths) if depths else None
        m["depth_max"] = max(depths) if depths else None
        m["depth_last"] = m["last"].get("depth_measured")
        m["pot_last"] = m["last"].get("pot_day")
        m["marg_last"] = m["last"].get("marg_pct_day")
    return markets


def _grade(g: dict, bar: float) -> str:
    """'graduate' | 'near' | 'mirage' | 'below' at bar, last-reading basis."""
    d = g.get("depth_measured")
    if d is None:
        return "below"
    if _is_trap(g, bar):
        return "mirage"
    if d >= bar:
        return "graduate"
    if d >= 0.5 * bar:
        return "near"
    return "below"


def _buckets(markets: dict[str, dict], bar: float) -> dict[str, list[dict]]:
    """Bucket the depth-reject markets at bar, on their LAST reading."""
    out: dict[str, list[dict]] = {"graduate": [], "near": [], "mirage": [],
                                  "below": []}
    for m in markets.values():
        out[_grade(m["last"], bar)].append(m)
    return out


def _vol_fail(m: dict, volume_bar: float) -> bool:
    """True when the market's last recorded 24h volume is under the gate."""
    v = m.get("volume_last")
    return v is None or v < volume_bar


def triage_report(nm_path: Path, bar: float = PERMANENT_BAR,
                  volume_bar: float = VOLUME_BAR) -> dict:
    """The full triage: population, mirage profile, volume interaction, signals."""
    if not (math.isfinite(bar) and bar > 0):
        raise ValueError(f"bar must be finite and greater than zero, got {bar}")
    if not (math.isfinite(volume_bar) and volume_bar >= 0):
        raise ValueError(f"volume_bar must be finite and non-negative, got {volume_bar}")
    ranks, n_ranks = _load_ranks(nm_path)
    markets = _per_market(ranks)
    buckets = _buckets(markets, bar)
    if not nm_path.exists():
        return {"nm_path": str(nm_path), "exists": False, "ranks": 0,
                "depth_rejects": 0, "bar": bar,
                "volume_bar": volume_bar, "buckets": {k: 0 for k in buckets},
                "volume_cross": {}, "signals": [], "notes": [
                    f"{nm_path}: no such file -- nothing triaged. This is an "
                    "absence of data, not a zero result."]}

    def _med(ms: list[dict], key: str) -> float | None:
        vals = [m[key] for m in ms if m.get(key) is not None]
        return median(vals) if vals else None

    profile = {
        "mirage": {"n": len(buckets["mirage"]),
                   "volume_median": _med(buckets["mirage"], "volume_median"),
                   "depth_ratio_median": None},
        "near": {"n": len(buckets["near"]),
                 "volume_median": _med(buckets["near"], "volume_median")},
        "graduate": {"n": len(buckets["graduate"]),
                     "volume_median": _med(buckets["graduate"],
                                           "volume_median")},
        "below": {"n": len(buckets["below"]),
                  "volume_median": _med(buckets["below"], "volume_median")},
    }
    mirage_depth_med = _med(buckets["mirage"], "depth_last")
    if mirage_depth_med is not None:
        profile["mirage"]["depth_ratio_median"] = round(
            mirage_depth_med / bar, 3)

    # --- volume-gate cross-tab: would the live pipeline have taken them? ---
    def _cross(markets_list: list[dict]) -> dict:
        fail = [m for m in markets_list if _vol_fail(m, volume_bar)]
        return {"n": len(markets_list), "volume_fail": len(fail),
                "pot_day": round(sum(m.get("pot_last") or 0.0
                                     for m in markets_list), 2)}

    volume_cross = {
        "all_depth_rejects": _cross(list(markets.values())),
        "mirage": _cross(buckets["mirage"]),
        "near": _cross(buckets["near"]),
        "graduate": _cross(buckets["graduate"]),
    }

    # --- sharpening signals, precision/recall on the recorded population ---
    # Every signal is evaluated as: how many MIRAGES it would exclude (true
    # positives for filtering) vs how many genuine near-misses/graduates it
    # would wrongly exclude (false positives). A signal with zero false
    # positives and high true positives is a candidate for the depth check.
    nears_grads = buckets["near"] + buckets["graduate"]

    def _test(name: str, exclude: callable) -> dict:
        mirages_out = [m for m in buckets["mirage"] if exclude(m)]
        wrongly_out = [m for m in nears_grads if exclude(m)]
        return {"signal": name, "mirages_excluded": len(mirages_out),
                "mirages_total": len(buckets["mirage"]),
                "near_or_grad_lost": len(wrongly_out),
                "near_or_grad_total": len(nears_grads)}

    signals = [
        _test("volume_last >= $250k (live volume gate)",
              lambda m: _vol_fail(m, volume_bar)),
        _test("volume_last >= $50k",
              lambda m: (m.get("volume_last") or 0) < 50_000),
        _test("volume_last >= $25k",
              lambda m: (m.get("volume_last") or 0) < 25_000),
        _test("seen in >= 2 ranks (reading consistency)",
              lambda m: m["n_readings"] < 2),
        _test("seen in >= 3 ranks",
              lambda m: m["n_readings"] < 3),
        _test("depth_max >= bar on any reading (illustrative, definitional)",
              lambda m: (m.get("depth_max") or 0) < bar),
    ]

    # how many near-misses would graduate on an any-reading (max-depth) basis
    any_read_grad = sum(1 for m in buckets["near"]
                        if (m.get("depth_max") or 0) >= bar)

    return {
        "nm_path": str(nm_path), "exists": True,
        "ranks": n_ranks, "depth_rejects": len(markets),
        "bar": bar, "permanent_bar": PERMANENT_BAR,
        "volume_bar": volume_bar,
        "buckets": {k: len(v) for k, v in buckets.items()},
        "profile": profile,
        "volume_cross": volume_cross,
        "any_read_graduates_among_near": any_read_grad,
        "signals": signals,
        "mirage_sample": [
            {"title": m.get("title"), "slug": m.get("slug"),
             "depth_last": m.get("depth_last"),
             "volume_last": m.get("volume_last"),
             "n_readings": m["n_readings"]}
            for m in sorted(buckets["mirage"],
                            key=lambda m: -(m.get("depth_last") or 0))[:15]],
        "notes": [
            "depth/volume/pot are rank-time snapshots, not today's book.",
            "buckets grade on each market's LAST reading (trial_depth_gate "
            "basis); the dashboard's small-margin tile counts any reading.",
            "the volume gate in the live ranker is config "
            "select_min_volume_24h_usd; depth is scored AFTER volume, so a "
            "depth-clear market can still fail the live run on volume.",
            "a 'signal' with mirages_excluded high and near_or_grad_lost == 0 "
            "is a candidate for filtering mirages before the near-miss log.",
        ],
    }


def _print(rep: dict) -> None:
    print(f"\n=== mirage triage: {rep['nm_path']} @ ${rep['bar']:,.0f} bar ===")
    if not rep["exists"]:
        for n in rep["notes"]:
            print(f"  note: {n}")
        return
    b = rep["buckets"]
    print(f"  ranks {rep['ranks']}   depth-reject markets {rep['depth_rejects']}"
          f"\n  buckets (last reading): graduate {b['graduate']}  "
          f"near {b['near']}  mirage {b['mirage']}  below {b['below']}")

    p = rep["profile"]
    print("\n  PROFILE (median of last readings)")
    print(f"    {'bucket':<10} {'n':>4} {'vol_median':>12} {'depth/bar':>10}")
    for k in ("graduate", "near", "mirage", "below"):
        pr = p[k]
        dr = pr.get("depth_ratio_median")
        print(f"    {k:<10} {pr['n']:>4} "
              f"{('$'+format(pr['volume_median'] or 0, ',.0f')):>12} "
              f"{('' if dr is None else f'{dr:.3f}'):>10}")

    vc = rep["volume_cross"]
    print(f"\n  LIVE VOLUME-GATE CROSS-TAB (would fail at "
          f"${rep['volume_bar']:,.0f}/24h)")
    for k in ("all_depth_rejects", "mirage", "near", "graduate"):
        c = vc[k]
        print(f"    {k:<18} n={c['n']:>3}  volume_fail={c['volume_fail']:>3}"
              f"  pot ${c['pot_day']:>10,.2f}/day")

    print(f"\n  ANY-READING GRADUATES among near-misses at this bar: "
          f"{rep['any_read_graduates_among_near']} "
          f"(dashboard's all-time basis vs the replay's last-reading)")

    print("\n  SHARPENING SIGNALS (filter mirages before the near-miss log)")
    print(f"    {'signal':<52} {'mirage':>8} {'n/g lost':>9}")
    for s in rep["signals"]:
        print(f"    {s['signal']:<52} "
              f"{s['mirages_excluded']:>3}/{s['mirages_total']:<4} "
              f"{s['near_or_grad_lost']:>3}/{s['near_or_grad_total']}")

    if rep["mirage_sample"]:
        print("\n  MIRAGE SAMPLE (15 largest recorded depths)")
        for m in rep["mirage_sample"]:
            title = (m.get("title") or "")[:44]
            title = title.encode("ascii", "replace").decode("ascii")
            print(f"    {title:<46} depth ${m['depth_last'] or 0:>7,.0f}  "
                  f"vol ${m['volume_last'] or 0:>9,.0f}  "
                  f"readings {m['n_readings']}")

    print("\n  METHOD AND LIMITS")
    for n in rep["notes"]:
        print(f"    * {n}")


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--bar", type=float, default=PERMANENT_BAR,
                   help=f"bar to bucket at (default: permanent "
                        f"{PERMANENT_BAR:,.0f})")
    p.add_argument("--nm", default=str(DEFAULT_NM),
                   help="near-miss log to triage (default: run/near_misses.jsonl)")
    p.add_argument("--json", default=None, help="write the report dict here")
    a = p.parse_args(argv)

    try:
        rep = triage_report(Path(a.nm), a.bar)
    except ValueError as e:
        p.error(str(e))
    _print(rep)
    if a.json:
        Path(a.json).write_text(json.dumps(rep, indent=2), encoding="utf-8")
        print(f"\nwrote {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
