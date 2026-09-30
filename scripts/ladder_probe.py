"""Replay a price ladder on recorded tapes, read-only, outside the money path.

Answers issue #49's Q1-Q3 (how many rungs, what exit window, what order
lifetime) from tape data before any ladder code is built. It opens no client
that can sign, reads the tape store read-only, writes one JSON report to an
explicit `--out` path, and refuses the production registry by name.

Tape schema is the `price_tape.db` subset: `markets(token_id, condition_id,
question, slug, volume_24h, first_seen, up_wins)` and
`ticks(token_id, ts, price)`. Sides need no labels: the two tokens of a market
are symmetric legs, and the hold policy scores a one-leg residue at the
tape-winner's resolution value (last print above 0.50 wins; ties are
unmeasurable) -- never at zero, the flattery the range-legging post-mortem
warned about.

    python scripts/ladder_probe.py --tape data/price_tape.db --out reports/ladder_probe.json
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Running `python scripts/ladder_probe.py` puts `scripts/` on the path, not the
# repo root, so sibling modules are invisible without this.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

TICK = 0.01
SLIPPAGE_TICKS = 2
SHAPES = {
    "2": (0.45, 0.55),
    "4": (0.45, 0.48, 0.52, 0.55),
    "6": (0.42, 0.45, 0.48, 0.52, 0.55, 0.58),
}
EXIT_SEC = (60, 120, 180)
REFUSED_TAPE_NAMES = ("orders.db",)
REFUSED_OUT_DIRS = ("data", "run")


class TapeRefused(RuntimeError):
    """The tape or the report path is a production store that stays untouched."""


class TapeError(RuntimeError):
    """The tape store could not be opened or read."""


@dataclass(frozen=True)
class MarketTape:
    """One market's two legs as sorted (ts, price) samples plus the winner."""

    condition_id: str
    open_ts: int
    close_ts: int
    leg_a: list = field(default_factory=list)
    leg_b: list = field(default_factory=list)
    winner: Optional[str] = None  # "leg_a", "leg_b", or None when unmeasurable


def refuse_tape(path: str | Path) -> Path:
    """Reject production-store names before any byte is read."""
    p = Path(path)
    if p.name in REFUSED_TAPE_NAMES:
        raise TapeRefused(f"Refusing production store: {p.name}.")
    return p


def refuse_output(path: str | Path) -> Path:
    """Reject production names and live dirs before any byte is written."""
    p = Path(path)
    if p.name in REFUSED_TAPE_NAMES:
        raise TapeRefused(f"Refusing production store: {p.name}.")
    parts = set(p.resolve().parent.parts) if p.is_absolute() else set(p.parts)
    if parts & set(REFUSED_OUT_DIRS):
        raise TapeRefused(f"Refusing live dir for a probe report: {p}.")
    return p


def _fills(ticks: list, rungs: tuple) -> dict:
    """First tape print at or through each rung: {rung: (ts, price)}."""
    fills = {}
    for rung in rungs:
        for ts, price in ticks:
            if price <= rung:
                fills[rung] = (ts, price)
                break
    return fills


def _last_at_or_before(ticks: list, deadline: int) -> Optional[tuple]:
    seen = None
    for ts, price in ticks:
        if ts <= deadline:
            seen = (ts, price)
        else:
            break
    if seen is None and ticks:
        seen = ticks[0]
    return seen


def simulate_market(market: MarketTape, rungs: tuple,
                    exit_sec: tuple = EXIT_SEC) -> dict:
    """Replay one ladder shape on one market; pure, no I/O."""
    fills_a = _fills(market.leg_a, rungs)
    fills_b = _fills(market.leg_b, rungs)
    first_a = min(fills_a.values()) if fills_a else None
    first_b = min(fills_b.values()) if fills_b else None

    pnl: dict = {}
    if first_a and first_b:
        cost = first_a[1] + first_b[1]
        pnl["pair"] = 1.0 - cost
        return {"outcome": "pair", "pair_cost": cost, "any_fill": True,
                "pnl": pnl}

    if first_a and not first_b:
        filled, side, ticks = first_a, "one_leg_a", market.leg_a
    elif first_b and not first_a:
        filled, side, ticks = first_b, "one_leg_b", market.leg_b
    else:
        return {"outcome": "nothing", "pair_cost": None, "any_fill": False,
                "pnl": pnl}

    cost = filled[1]
    won = market.winner == ("leg_a" if side == "one_leg_a" else "leg_b")
    pnl["hold"] = (1.0 if won else 0.0) - cost if market.winner else None
    for sec in exit_sec:
        seen = _last_at_or_before(ticks, market.open_ts + sec)
        pnl[f"exit_{sec}"] = (seen[1] - SLIPPAGE_TICKS * TICK - cost
                              if seen else None)
    return {"outcome": side, "pair_cost": None, "any_fill": True, "pnl": pnl}


def mean_ci90(xs: list) -> tuple:
    """Mean with a normal-approx 90% interval; empty input stays unmeasured."""
    xs = [x for x in xs if x is not None]
    if not xs:
        return None, None, None
    mean = sum(xs) / len(xs)
    if len(xs) < 2:
        return mean, None, None
    var = sum((x - mean) ** 2 for x in xs) / (len(xs) - 1)
    half = 1.645 * math.sqrt(var) / math.sqrt(len(xs))
    return mean, mean - half, mean + half


def _prop_ci90_diff(n1: int, k1: int, n2: int, k2: int) -> tuple:
    """90% interval for p2 - p1, two-proportion normal approximation."""
    if n1 < 2 or n2 < 2:
        return None, None
    p1, p2 = k1 / n1, k2 / n2
    se = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
    diff = p2 - p1
    half = 1.645 * se
    return diff - half, diff + half


def summarise(results: list, policy: str) -> dict:
    """Collapse per-market simulations into rates, PnL terms, and a CI."""
    pnls = [r["pnl"].get(policy) for r in results]
    pairs = [r["pnl"]["pair"] for r in results if r["outcome"] == "pair"]
    legs = [r["pnl"].get(policy) for r in results
            if r["outcome"].startswith("one_leg")]
    n = len(results)
    mean, lo, hi = mean_ci90(pnls)
    pair_mean = sum(pairs) / len(pairs) if pairs else None
    measured = [x for x in legs if x is not None]
    leg_mean = sum(measured) / len(measured) if measured else None
    return {
        "n": n,
        "n_pair": len(pairs),
        "p_pair": len(pairs) / n if n else 0.0,
        "n_oneleg": len([r for r in results
                         if r["outcome"].startswith("one_leg")]),
        "mean_pnl": mean,
        "ci90_lo": lo,
        "ci90_hi": hi,
        # The verdict formula, split into its two visible terms:
        "term_pair": (len(pairs) / n * pair_mean) if (n and pairs) else 0.0,
        "term_residue": (len(legs) / n * leg_mean) if (n and leg_mean is not None) else 0.0,
    }


def verdict(shape_stats: dict, policy_stats: dict,
            min_markets: int = 30) -> dict:
    """Shape from the 4-vs-2 fill-rate CI, exit from the best funded policy."""
    s2 = shape_stats.get("2", {})
    s4 = shape_stats.get("4", {})
    n = min(s2.get("n", 0), s4.get("n", 0))
    reasons = []
    if n < min_markets:
        return {"shape": "unmeasurable", "exit": "unmeasured",
                "strategy": "unmeasurable",
                "reasons": [f"only {n} markets, floor is {min_markets}"]}
    lo, hi = _prop_ci90_diff(s2["n"], s2.get("any_fill", 0),
                             s4["n"], s4.get("any_fill", 0))
    if lo is not None and lo > 0:
        shape = "4"
        reasons.append("4-level fill-rate CI excludes zero above 2-level")
    else:
        shape = "2"
        reasons.append("no proven 4-level edge; default to 2")
    best, exit_name = None, "unmeasured"
    for policy, st in policy_stats.items():
        if st.get("mean_pnl") is None or st.get("ci90_lo") is None:
            continue
        if st["ci90_lo"] >= 0 and (best is None
                                   or st["mean_pnl"] > best["mean_pnl"]):
            best, exit_name = st, policy
    strategy = "go" if best is not None else "no-go"
    if best is None:
        reasons.append("no exit policy funds itself at 90% confidence")
    return {"shape": shape, "exit": exit_name, "strategy": strategy,
            "reasons": reasons}


def load_series(conn: sqlite3.Connection, slug_like: str,
                window_sec: int) -> list:
    """Group tape ticks into two-leg markets; read-only by construction."""
    try:
        metas = conn.execute(
            "SELECT token_id, condition_id, slug, first_seen FROM markets"
            " WHERE slug LIKE ? ORDER BY condition_id, token_id",
            (slug_like,)).fetchall()
    except sqlite3.Error as exc:
        raise TapeError(f"Cannot list markets: {exc}.") from exc
    by_condition: dict = {}
    for token_id, condition_id, _slug, first_seen in metas:
        by_condition.setdefault(condition_id, []).append((token_id, first_seen))
    markets = []
    for condition_id, tokens in by_condition.items():
        if len(tokens) != 2:
            continue
        legs = []
        try:
            for token_id, _seen in tokens:
                rows = conn.execute(
                    "SELECT ts, price FROM ticks WHERE token_id = ?"
                    " ORDER BY ts", (token_id,)).fetchall()
                legs.append([(ts, price) for ts, price in rows])
        except sqlite3.Error as exc:
            raise TapeError(f"Cannot read ticks: {exc}.") from exc
        open_ts = min(first_seen for _, first_seen in tokens)
        legs = [[(ts, p) for ts, p in leg
                 if open_ts <= ts <= open_ts + window_sec] for leg in legs]
        if not all(legs):
            continue
        last_a = legs[0][-1][1] if legs[0] else None
        last_b = legs[1][-1][1] if legs[1] else None
        winner = None
        if last_a is not None and last_b is not None and last_a != last_b:
            winner = "leg_a" if last_a > last_b else "leg_b"
        markets.append(MarketTape(condition_id=condition_id, open_ts=open_ts,
                                  close_ts=open_ts + window_sec,
                                  leg_a=legs[0], leg_b=legs[1],
                                  winner=winner))
    return markets


def run(tape: str, out: str, slug_like: str, window_sec: int,
        shapes: Optional[dict] = None,
        exit_sec: tuple = EXIT_SEC) -> dict:
    """Replay every shape on every series market and write one JSON report."""
    tape_path = refuse_tape(tape)
    out_path = refuse_output(out)
    try:
        conn = sqlite3.connect(f"file:{tape_path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise TapeError(f"Cannot open tape read-only: {exc}.") from exc
    with conn:
        markets = load_series(conn, slug_like, window_sec)
    shapes = shapes or SHAPES
    by_shape = {}
    for name, rungs in shapes.items():
        results = [simulate_market(m, rungs, exit_sec) for m in markets]
        any_fill = sum(1 for r in results if r["any_fill"])
        policies = {"pair": summarise(results, "pair"),
                    "hold": summarise(results, "hold")}
        for sec in exit_sec:
            policies[f"exit_{sec}"] = summarise(results, f"exit_{sec}")
        by_shape[name] = {"n": len(results), "any_fill": any_fill,
                          "policies": policies}
    shape_stats = {k: {"n": v["n"], "any_fill": v["any_fill"]}
                   for k, v in by_shape.items()}
    best_shape = verdict(shape_stats, {}, min_markets=0)["shape"]
    policy_stats = (by_shape[best_shape]["policies"]
                    if best_shape in by_shape else {})
    report = {"tape": str(tape_path), "slug_like": slug_like,
              "window_sec": window_sec, "markets": len(markets),
              "by_shape": by_shape,
              "verdict": verdict(shape_stats, policy_stats)}
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2))
    return report


def main(argv: Optional[list] = None) -> int:
    """CLI: `python scripts/ladder_probe.py --tape <db> --out <json>`."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tape", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--slug-like", default="%",
                        help="SQL LIKE filter on the market slug")
    parser.add_argument("--window-sec", type=int, default=300)
    args = parser.parse_args(argv)
    report = run(args.tape, args.out, args.slug_like, args.window_sec)
    print(json.dumps({"markets": report["markets"],
                      "verdict": report["verdict"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
