"""Shadow rehearsal for short-window ladders (issue #324). No signer, no venue.

Drives :func:`core_brain.shadow_run.run_shadow` through its injectable seam
with a ladder-mimic ``decide_fn``: one rung BUY per shape price per side, all
stamped with a single ``pair_id`` per market, stable across rotations so
``plan_orders`` keeps them resting while tape-driven fills arrive.

Fills come from a recorded ladder tape (both-leg schema written by
``scripts/ladder_tape_collect.py``), never from the live trade feed: a print
at or through a rung emits volume at exactly that rung price, the probe
``_fills`` semantics replayed against resting rows. Books come from the same
tape cursor. The only network the process may touch is the public resolution
read the rehearsal's own sweep already degrades on; tests stub even that.

Writes one JSON report per run (``--out``) with the schema locked in the
#324 plan. Refuses the production registry by name; never constructs a
signing client (``run_shadow`` builds the signer-less shadow client itself).

    python scripts/ladder_shadow_rehearsal.py --tape data/ladder_tape_btc5.db \\
        --series btc-up-or-down-5m --db data/NN_shadow_ladder.db \\
        --out reports/ladder_shadow_btc5.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)

# Running `python scripts/ladder_shadow_rehearsal.py` puts `scripts/` on the
# path, not the repo root, so sibling modules are invisible without this.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.ladder_probe import SHAPES, refuse_tape  # noqa: E402

REFUSED_OUT_DIRS = ("data", "run")
TICK = 0.01
BOOK_DEPTH = 1000.0


class RehearsalRefused(RuntimeError):
    """A production store or live dir the rehearsal must not touch."""


def refuse_output(path: str | Path) -> Path:
    """Reject production names and live dirs before any byte is written."""
    p = Path(path)
    if p.name == "orders.db":
        raise RehearsalRefused(f"Refusing production store: {p.name}.")
    parts = set(p.resolve().parent.parts) if p.is_absolute() else set(p.parts)
    if parts & set(REFUSED_OUT_DIRS):
        raise RehearsalRefused(f"Refusing live dir for a rehearsal report: {p}.")
    return p


@dataclass
class TapeLeg:
    """One leg of a series market: its token plus sorted (ts, price) prints."""

    token_id: str
    ticks: list = field(default_factory=list)


@dataclass
class TapeMarket:
    """One series market with both legs as recorded on the tape."""

    condition_id: str
    slug: str
    open_ts: int
    leg_a: TapeLeg
    leg_b: TapeLeg


def load_tape_markets(conn: sqlite3.Connection, slug_like: str,
                      window_sec: int) -> list[TapeMarket]:
    """Both legs per market, clipped to the series open window.

    Mirrors ``scripts/ladder_probe.load_series`` but keeps the token ids the
    probe does not need: the rehearsal rests real rows against them.
    """
    try:
        metas = conn.execute(
            "SELECT token_id, condition_id, slug, first_seen FROM markets"
            " WHERE slug LIKE ? ORDER BY condition_id, token_id",
            (slug_like,)).fetchall()
    except sqlite3.Error as exc:
        raise RehearsalRefused(f"Cannot list markets: {exc}.") from exc
    by_condition: dict[str, list] = {}
    for token_id, condition_id, slug, first_seen in metas:
        by_condition.setdefault(condition_id, []).append(
            (token_id, slug, first_seen))
    markets = []
    for condition_id, tokens in by_condition.items():
        if len(tokens) != 2:
            continue
        open_ts = min(first_seen for _, _, first_seen in tokens)
        legs = []
        try:
            for token_id, _, _ in tokens:
                rows = conn.execute(
                    "SELECT ts, price FROM ticks WHERE token_id = ?"
                    " ORDER BY ts", (token_id,)).fetchall()
                legs.append(TapeLeg(token_id=str(token_id),
                                    ticks=[(ts, p) for ts, p in rows
                                           if open_ts <= ts <= open_ts + window_sec]))
        except sqlite3.Error as exc:
            raise RehearsalRefused(f"Cannot read ticks: {exc}.") from exc
        if not all(leg.ticks for leg in legs):
            continue
        markets.append(TapeMarket(condition_id=condition_id,
                                  slug=tokens[0][1], open_ts=open_ts,
                                  leg_a=legs[0], leg_b=legs[1]))
    return markets


def build_fixture_series() -> list[TapeMarket]:
    """Two synthetic markets with the ladder tape shape, fully deterministic.

    MKT-PAIR prints through both rungs on BOTH legs in a single rotation:
    every rung fills before the pairs pass runs, so the market balances and
    merges (a staggered print would leave it one-sided for a rotation and
    the grace-0 pairs pass would exit it first -- that immediacy is what
    MKT-SOLO demonstrates). MKT-SOLO prints through both rungs on leg A
    only (one-leg residue, exit); leg B never reaches a rung, and its ask
    keeps the pair over the cap so the pass exits rather than completes.
    Tick 0 is a lead-in: rotation 1 settles before anything rests, so the
    first print is consumed with no rows to fill.
    """
    base = 1_700_000_000

    def tk(mkt: str, leg: str, prices: list[float]) -> TapeLeg:
        return TapeLeg(token_id=f"{mkt}-{leg}",
                       ticks=[(base + 5 * k, p) for k, p in enumerate(prices)])

    return [
        TapeMarket(condition_id="fixture-pair", slug="fixture-btc-5m",
                   open_ts=base,
                   leg_a=tk("fixture-pair", "a", [0.60, 0.44, 0.44, 0.44, 0.44, 0.44]),
                   leg_b=tk("fixture-pair", "b", [0.60, 0.44, 0.54, 0.44, 0.44, 0.44])),
        TapeMarket(condition_id="fixture-solo", slug="fixture-eth-5m",
                   open_ts=base,
                   leg_a=tk("fixture-solo", "a", [0.60, 0.44, 0.44, 0.44, 0.44, 0.44]),
                   leg_b=tk("fixture-solo", "b", [0.58, 0.58, 0.58, 0.58, 0.58, 0.58])),
    ]


class LadderMarket:
    """The market object shape the loop's visit needs (cf. test fakes)."""

    def __init__(self, condition_id: str, up_token: str, down_token: str,
                 slug: str = ""):
        self.condition_id = condition_id
        self.up_token = up_token
        self.down_token = down_token
        self.market_slug = slug or condition_id[:16]
        self.tick_size = 0.01
        self.neg_risk = False

    def t_remaining(self, now=None):
        return 3600.0


class TapeDriver:
    """Tape cursor behind the seam's three market-data ports.

    One cursor per market advances a single tick per ``traded`` call, so both
    legs move together exactly as the series printed them. Books mirror the
    last seen print with deep touch depth, so exits fill at the touch and
    queue position never blocks a fill the tape earned.
    """

    def __init__(self, markets: list[TapeMarket], rung_size: int):
        self._markets = {m.condition_id: m for m in markets}
        self._cursor = {m.condition_id: 0 for m in markets}
        self._last: dict[str, float] = {}
        for m in markets:
            for leg in (m.leg_a, m.leg_b):
                if leg.ticks:
                    self._last[leg.token_id] = leg.ticks[0][1]
        self._rung_size = rung_size
        self._rungs: tuple = ()

    def set_rungs(self, rungs: tuple) -> None:
        self._rungs = rungs

    def _advance(self, condition_id: str) -> dict[str, float]:
        """Newest print per token after moving this market one tick ahead."""
        m = self._markets[condition_id]
        k = self._cursor[condition_id]
        prints: dict[str, float] = {}
        for leg in (m.leg_a, m.leg_b):
            if k < len(leg.ticks):
                self._last[leg.token_id] = leg.ticks[k][1]
            prints[leg.token_id] = self._last[leg.token_id]
        self._cursor[condition_id] = k + 1
        return prints

    def traded(self, condition_id: str, _seen: set) -> dict[str, dict[float, float]]:
        """Volume at exactly each rung a fresh print reached or crossed."""
        prints = self._advance(condition_id)
        out: dict[str, dict[float, float]] = {}
        for token_id, price in prints.items():
            hit = {r: float(self._rung_size) for r in self._rungs if price <= r}
            if hit:
                out[token_id] = hit
        return out

    def books(self, _clob_host: str, token_id: str) -> dict:
        price = self._last.get(token_id, 0.50)
        bid = round(price - TICK, 4)
        ask = round(price + TICK, 4)
        return {"token_id": token_id, "best_bid": bid, "best_ask": ask,
                "bids": {bid: BOOK_DEPTH}, "asks": {ask: BOOK_DEPTH}}


def ladder_pair_id(series: str, condition_id: str) -> str:
    """One deterministic pair id per rehearsed market (no uuid churn)."""
    safe = "".join(c if c.isalnum() or c in ("-", "_") else "-"
                   for c in condition_id)[-24:]
    return f"ladder-{series}-{safe}"


EXIT_STATUSES = ("single_buy_exit", "naked_exit", "ladder_exit")


def ladder_decide(series: str, rungs: tuple,
                  token_side: dict[str, str],
                  db_path=None) -> Callable:
    """A ``decide_fn`` posting the ladder shape with a rung lifecycle.

    Every rung carries the market's single ``pair_id`` attribute, which
    ``record_submit`` carries onto the rows instead of minting a fresh pair
    per rotation. A rung is posted while it never filled: open rows are kept
    by returning the same price (``plan_orders`` keeps what did not move),
    cancelled rows are re-quoted under the same stamp, filled rows retire --
    the rung's capital is deployed, re-posting it would double the exposure.
    Once the pairs pass exits the market (an exit close on the condition),
    the ladder is done with that market and posts nothing: re-quoting
    exited shares under the market-wide stamp is what the refill-after-exit
    finding (see the #324 handoff) forbids.

    Without ``db_path`` the lifecycle has nothing to read and every rung is
    posted unconditionally (unit-test mode).
    """
    def rung_rows(condition_id: str) -> tuple[dict, bool]:
        by_level: dict[tuple, list] = {}
        retired = False
        if db_path is None:
            return by_level, retired
        try:
            with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as conn:
                conn.row_factory = sqlite3.Row
                for r in conn.execute(
                        "SELECT token_id, price, status FROM orders"
                        " WHERE condition_id = ?", (condition_id,)).fetchall():
                    by_level.setdefault((str(r["token_id"]), round(float(r["price"]), 4)),
                                        []).append(str(r["status"]))
                retired = conn.execute(
                    "SELECT 1 FROM closes WHERE condition_id = ?"
                    f" AND method IN ({', '.join('?' * len(EXIT_STATUSES))})"
                    " LIMIT 1",
                    (condition_id,) + EXIT_STATUSES).fetchone() is not None
        except (sqlite3.Error, OSError, ValueError) as e:
            # Fail closed and loud: an unreadable store must not read as "no
            # exits yet" (that would re-post exited shares under the
            # market-wide stamp) nor as "no rows" (that would double-post).
            # The market goes dark for this rotation and retries the next.
            log.warning("rung lifecycle unreadable for %s: %s -- market dark",
                        condition_id[:16], e)
            return {}, True
        return by_level, retired

    def decide(cfg, up_book, down_book, inv, t_rem, wf):
        from core_brain.quotes import QuoteIntent
        size = int(getattr(cfg, "min_quote_shares", 5) or 5)
        intents = []
        for leg, book in (("leg_a", up_book), ("leg_b", down_book)):
            token_id = book.get("token_id", "")
            condition_id = book.get("condition_id", token_id)
            by_level, retired = rung_rows(condition_id)
            if retired:
                continue
            side = token_side.get(token_id, "UP" if leg == "leg_a" else "DOWN")
            for rung in rungs:
                if db_path is not None:
                    seen = by_level.get((token_id, round(float(rung), 4)), [])
                    if any(s == "filled" for s in seen):
                        continue
                qi = QuoteIntent(side=side, token_id=token_id,
                                 price=float(rung), size=size,
                                 mid=float(rung), edge_vs_mid=0.0,
                                 reason="ladder-mimic")
                qi.pair_id = ladder_pair_id(series, condition_id)
                intents.append(qi)
        return intents, f"ladder-mimic shape={len(rungs)}"
    return decide


def _rotation_cap(n: int) -> Callable[[float], None]:
    """A ``sleep_fn`` ending the run after ``n`` rotations, results intact."""
    calls = []

    def sleep_fn(seconds: float) -> None:
        calls.append(seconds)
        if len(calls) >= n:
            raise KeyboardInterrupt()
    return sleep_fn


def run_rehearsal(*, series: str, tape_markets: list[TapeMarket],
                  db_path, out_path: Optional[Path],
                  shape: str = "2", rotations: int = 8,
                  run_id: Optional[str] = None,
                  cfg=None) -> dict:
    """One rehearsal: rest rungs, replay the tape, report placement+fill+exit."""
    from core_brain.shadow_run import run_shadow

    rungs = SHAPES[shape]
    db_path = Path(db_path)
    if out_path is not None:
        out_path = refuse_output(out_path)
        if db_path.resolve() == out_path.resolve():
            raise RehearsalRefused(
                "Refusing to use the same resolved path for db_path and out_path.")
    if cfg is None:
        from core_brain.shadow_run import shadow_cfg
        cfg = shadow_cfg()
    size = int(getattr(cfg, "min_quote_shares", 5) or 5)
    driver = TapeDriver(tape_markets, rung_size=size)
    driver.set_rungs(rungs)

    loop_markets = []
    token_side: dict[str, str] = {}
    book_cids: dict[str, str] = {}
    for m in tape_markets:
        loop_markets.append(LadderMarket(m.condition_id, m.leg_a.token_id,
                                         m.leg_b.token_id, m.slug))
        token_side[m.leg_a.token_id] = "UP"
        token_side[m.leg_b.token_id] = "DOWN"
        book_cids[m.leg_a.token_id] = m.condition_id
        book_cids[m.leg_b.token_id] = m.condition_id

    def fetch_books(clob_host: str, token_id: str) -> dict:
        book = driver.books(clob_host, token_id)
        book["condition_id"] = book_cids.get(token_id, token_id)
        return book

    import core_brain.markets as markets_mod
    real_trades = markets_mod.recent_trades
    markets_mod.recent_trades = driver.traded
    try:
        run_shadow(
            minutes=5.0, db_path=db_path,
            markets_fn=lambda cap=None: list(loop_markets),
            decide_fn=ladder_decide(series, rungs, token_side, db_path=db_path),
            fetch_books=fetch_books,
            interval=0.01, run_id=run_id or f"ladder-{series}",
            sleep_fn=_rotation_cap(rotations), cfg=cfg,
        )
    finally:
        markets_mod.recent_trades = real_trades

    report = build_report(series=series, shape=shape, rungs=rungs,
                          tape_markets=tape_markets, db_path=db_path)
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2))
    return report


def build_report(*, series: str, shape: str, rungs: tuple,
                 tape_markets: list[TapeMarket], db_path) -> dict:
    """Read the rehearsal store into the locked per-series report schema."""
    from core_brain.order_registry import get_connection

    if not tape_markets:
        raise ValueError("build_report needs at least one market")
    cids = [m.condition_id for m in tape_markets]
    with closing(get_connection(Path(db_path))) as conn:
        orders = [dict(r) for r in conn.execute(
            "SELECT * FROM orders WHERE condition_id IN (%s)"
            % ",".join("?" * len(cids)), tuple(cids)).fetchall()]
        fills = [dict(r) for r in conn.execute("SELECT * FROM fills").fetchall()]
        closes = [dict(r) for r in conn.execute("SELECT * FROM closes").fetchall()]
        # Shadow order rows are all side=BUY; the UP/DOWN leg lives on the
        # quotes ledger (one side per token), which record_submit writes.
        token_side = {}
        try:
            for r in conn.execute(
                    "SELECT token_id, side FROM quotes WHERE condition_id IN (%s)"
                    % ",".join("?" * len(cids)), tuple(cids)).fetchall():
                token_side.setdefault(str(r["token_id"]), str(r["side"]))
        except (sqlite3.Error, OSError, ValueError, KeyError, IndexError):
            token_side = {}  # fall back to token suffix below
        try:
            merge_legs = [dict(r) for r in conn.execute(
                "SELECT * FROM shadow_merge_legs").fetchall()]
        except (sqlite3.Error, OSError, ValueError):
            merge_legs = []  # older store without the table

    by_order = {o["id"]: o for o in orders}
    pair_ids = sorted({o["pair_id"] for o in orders if o.get("pair_id")})
    per_market_pair = {}
    for o in orders:
        per_market_pair.setdefault(o["condition_id"], set()).add(o.get("pair_id"))
    one_pair_per_market = all(len(v) == 1 for v in per_market_pair.values())

    side_of = lambda o: _side_of(o, token_side)
    prices_up = sorted({o["price"] for o in orders
                        if side_of(o) == "UP" and o.get("status") != "cancelled"})
    prices_dn = sorted({o["price"] for o in orders
                        if side_of(o) == "DOWN" and o.get("status") != "cancelled"})

    # Oldest-first: no order may hold fills while an older order on the same
    # (token, price) still has unfilled shares. Every fill must reference a
    # known order, and no order may fill past its size (no double-count).
    filled_by_order: dict[str, float] = {}
    orphans = 0
    for f in fills:
        o = by_order.get(f.get("order_uuid"))
        if o is None:
            orphans += 1
            continue
        filled_by_order[o["id"]] = filled_by_order.get(o["id"], 0.0) + float(f.get("size") or 0.0)
    overfilled = sum(1 for oid, s in filled_by_order.items()
                     if s > float(by_order[oid].get("original_size") or 0.0) + 1e-9)
    oldest_first = True
    for (tok, price), group in _group_orders(orders).items():
        if orphans:
            break
        ranked = sorted(group, key=lambda o: (o.get("posted_ts") or 0, o["id"]))
        for older, newer in zip(ranked, ranked[1:]):
            older_missing = float(older.get("original_size") or 0.0) - filled_by_order.get(older["id"], 0.0)
            if older_missing > 1e-9 and filled_by_order.get(newer["id"], 0.0) > 1e-9:
                oldest_first = False
                break

    merges = [c for c in closes if c.get("method") == "shadow_merge"]
    exits = [c for c in closes if c.get("method") == "single_buy_exit"]
    settled = [c for c in closes if c.get("method") == "shadow_settlement"]
    # Merge close rows count pair-units (min of the two legs); the legs table
    # counts the shares each leg actually gave up -- that is the unit that
    # conserves against fills.
    merged_leg_shares = sum(float(r.get("shares") or 0.0) for r in merge_legs)
    exited_shares = sum(float(c.get("shares") or 0.0) for c in exits)
    settled_shares = sum(float(c.get("shares") or 0.0) for c in settled)
    filled_shares = sum(filled_by_order.values())
    accounted = merged_leg_shares + exited_shares + settled_shares

    return {
        "issue": 324, "series": series, "shape": shape, "rungs": list(rungs),
        "markets": len(tape_markets),
        "pair_ids": pair_ids,
        "placements": {
            "orders": len(orders),
            "per_side": {
                "UP": sum(1 for o in orders if side_of(o) == "UP"),
                "DOWN": sum(1 for o in orders if side_of(o) == "DOWN"),
            },
            "distinct_prices_per_side": {"UP": prices_up, "DOWN": prices_dn},
            "one_pair_id_per_market": one_pair_per_market,
        },
        "fills": {
            "n": len([f for f in fills if f.get("order_uuid") in by_order]),
            "shares": filled_shares,
            "oldest_first": bool(oldest_first),
        },
        "exits": {
            "merges": len(merges), "merged_shares": merged_leg_shares,
            "single_exits": len(exits), "exited_shares": exited_shares,
            "settlements": len(settled), "settled_shares": settled_shares,
        },
        "conservation": {
            "orphan_fills": orphans,
            "overfilled_orders": overfilled,
            "filled_shares": filled_shares,
            "accounted_shares": accounted,
            "double_counted_fills": overfilled,
        },
        "live_execution": False,
    }


def _side_of(order: dict, token_side: Optional[dict] = None) -> str:
    token = str(order.get("token_id") or "")
    if token_side and token in token_side:
        return token_side[token]
    return "UP" if token.endswith("-a") else "DOWN"


def _group_orders(orders: list[dict]) -> dict:
    groups: dict[tuple, list] = {}
    for o in orders:
        if o.get("status") == "cancelled":
            continue
        groups.setdefault((str(o.get("token_id")), round(float(o.get("price") or 0), 4)),
                          []).append(o)
    return groups


def main(argv: Optional[list] = None) -> int:
    """CLI: rehearse one series tape, write one report. No signer, no venue."""
    from core_brain import rehearsal

    rehearsal.declare_rehearsal()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tape", default=None,
                    help="ladder tape DB (both-leg schema); omit for --fixture")
    ap.add_argument("--fixture", action="store_true",
                    help="rehearse the built-in two-market scenario, no tape")
    ap.add_argument("--series", required=True, help="series label for pair ids + report")
    ap.add_argument("--slug-like", default="%",
                    help="SQL LIKE filter on the market slug (tape mode)")
    ap.add_argument("--window-sec", type=int, default=300)
    ap.add_argument("--markets", type=int, default=10,
                    help="cap the series markets rehearsed (tape mode)")
    ap.add_argument("--shape", default="2", choices=sorted(SHAPES),
                    help="probe rung shape to mimic (default: 2)")
    ap.add_argument("--rotations", type=int, default=30)
    ap.add_argument("--db", required=True, help="per-run shadow store; orders.db refused")
    ap.add_argument("--out", required=True, help="report JSON path (not under data/ or run/)")
    ap.add_argument("--run-id", default=None)
    a = ap.parse_args(argv)

    if not a.fixture and not a.tape:
        raise SystemExit("--tape or --fixture is required")
    if a.tape:
        tape_path = refuse_tape(a.tape)
        with closing(sqlite3.connect(f"file:{tape_path}?mode=ro", uri=True)) as conn:
            tape_markets = load_tape_markets(conn, a.slug_like, a.window_sec)[:a.markets]
        if not tape_markets:
            raise SystemExit(f"no two-leg markets under {a.slug_like} in {tape_path}")
    else:
        tape_markets = build_fixture_series()

    report = run_rehearsal(series=a.series, tape_markets=tape_markets,
                           db_path=Path(a.db), out_path=Path(a.out),
                           shape=a.shape, rotations=a.rotations, run_id=a.run_id)
    print(json.dumps({"series": report["series"], "markets": report["markets"],
                      "pair_ids": report["pair_ids"],
                      "placements": report["placements"]["orders"],
                      "fills": report["fills"],
                      "exits": report["exits"],
                      "conservation": report["conservation"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
