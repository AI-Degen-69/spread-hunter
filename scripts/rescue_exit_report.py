"""Why did the four worst single-buy rescues lose what they lost? (Issue #306)

A read-only forensic report over a recorded registry store (and, optionally, a
same-window book-tape store). It answers the ticket's three questions from
recorded data or says, explicitly, that the data cannot answer:

  Q1  Would a longer grace window have caught the companion leg?
      -> an upper bound only. Recorded fills cannot show a fill a cancelled
         quote did not get, so any answer is labelled "sampled upper bound,
         not an executable fill" and, with no opposite-leg samples, is
         "unanswerable from this store".

  Q2  Was the exit late (the bid was still there) or gapped (the bid was
      already gone)? The bid path is SAMPLED -- marks bracket bid changes and
      the poll phase is not recorded -- so classifications are evidence, not
      proof. A close that carries a recorded route reason (see the reason
      instrumentation) is preferred over this reconstruction.

  Q3  Did anything at quote time separate the four worst exits from pairs
      that merged? Compared with the strong caveat that n=4 cannot calibrate
      a gate, and several fields the question really needs are simply not
      recorded.

It also flags `shadow_settlement` closes whose leg aged out past
`pairs_exit_window_sec` before market end -- the fail-closed gap where a
one-sided leg is held into settlement because nothing outside the rescue
sweep closes it.

The report NEVER writes: every store is opened in SQLite read-only mode.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Optional

# The rescue window: `pairs_exit_window_sec` in core_brain/config.py. Aged-out
# detection is measured against it; a store carries no config, so the default
# is stated here rather than guessed per run.
PAIRS_EXIT_WINDOW_SEC = 900.0

RESCUE_METHODS = ("single_buy_exit", "shadow_settlement")
MERGE_METHODS = ("merge", "shadow_merge")

# Fields Question 3 needs but the registry does not record. Listed verbatim in
# the output so the gap is visible next to the features that do exist.
ABSENT_FIELDS = (
    "t_remaining", "latency_ms", "opposite-leg ask/depth", "spread",
    "volume", "selection rejections",
)


def _ro(path: Path) -> sqlite3.Connection:
    if not Path(path).exists():
        print(f"error: registry store not found: {path}", file=sys.stderr)
        raise SystemExit(2)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _usd(x: Optional[float]) -> str:
    if x is None:
        return "--"
    return f"-${abs(x):.2f}" if x < 0 else f"${x:.2f}"


# --- reconstruction -----------------------------------------------------------


def _fills_by_condition(reg: sqlite3.Connection) -> dict[str, list[sqlite3.Row]]:
    rows = reg.execute(
        """
        SELECT f.trade_id, f.order_uuid, f.size, f.price, f.venue_ts,
               o.condition_id, o.token_id, o.side, o.pair_id
        FROM fills f JOIN orders o ON f.order_uuid = o.id
        ORDER BY f.venue_ts ASC
        """
    ).fetchall()
    out: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        out.setdefault(r["condition_id"], []).append(r)
    return out


def _orders_by_condition(reg: sqlite3.Connection) -> dict[str, list[sqlite3.Row]]:
    rows = reg.execute("SELECT * FROM orders").fetchall()
    out: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        out.setdefault(r["condition_id"], []).append(r)
    return out


def _settlement_row(close, cfills, heavy_token) -> Optional[dict]:
    """Reconstruct a `shadow_settlement` close: the one-sided leg held to the end.

    Its P&L is the whole story -- the leg redeemed at $1.00 or died at $0.00 --
    and its fill-to-settlement age is the measure of the fail-closed gap.
    """
    leg_fills = [f for f in cfills if f["side"] == "BUY"
                 and f["token_id"] == heavy_token]
    if not leg_fills:
        return None
    matched = sum(f["size"] for f in leg_fills)
    notional = sum(f["size"] * f["price"] for f in leg_fills)
    paid = (notional / matched) if matched > 0 else 0.0
    fill_ts_ms = min(f["venue_ts"] or 0 for f in leg_fills)
    fill_ts_s = fill_ts_ms / 1000.0 if fill_ts_ms else None
    close_ts = close["ts"]
    return {
        "cid": close["condition_id"],
        "pair_id": leg_fills[0]["pair_id"] or "--",
        "method": close["method"],
        "reason": close["reason"] if "reason" in close.keys() else None,
        "token": heavy_token,
        "sold_side": "--",
        "shares": close["shares"],
        "paid": paid,
        "sold": None,
        "pnl": close["realized_pnl"],
        "fill_ts_s": fill_ts_s,
        "close_ts": close_ts,
        "fill_to_exit": (close_ts - fill_ts_s) if (fill_ts_s and close_ts) else None,
        "aged_out": bool(fill_ts_s and close_ts
                         and (close_ts - fill_ts_s) > PAIRS_EXIT_WINDOW_SEC),
    }


def _exit_row(close, fills, orders) -> Optional[dict]:
    """Reconstruct one rescue close from its condition's rows.

    The sold leg is the one the close prices (`up_price` or `dn_price` set);
    its fills give the position, its fills' first venue_ts the fill time.
    """
    cid = close["condition_id"]
    sold_side = "UP" if close["up_price"] is not None else (
        "DOWN" if close["dn_price"] is not None else None)
    if sold_side is None:
        # A `shadow_settlement` close prices neither leg: it books the leg the
        # venue settled at $1.00/$0.00, not a market sell. Reconstruct the
        # one-sided held leg from its BUY fills instead of dropping the row.
        if close["method"] != "shadow_settlement":
            return None
        cfills = fills.get(cid, [])
        buys = [f for f in cfills if f["side"] == "BUY"]
        if not buys:
            return None
        by_token: dict[str, float] = {}
        for f in buys:
            by_token[f["token_id"]] = by_token.get(f["token_id"], 0.0) + f["size"]
        # The settlement books whichever token the venue resolved; both held
        # tokens belong to this condition, so reconstruct from the heavier one.
        heavy_token = max(by_token, key=by_token.get)
        row_q = orders.get(cid, [])
        heavy_side = None
        for o in row_q:
            if o["token_id"] == heavy_token:
                heavy_side = "UP"  # resolved below from the fills' quote side
                break
        return _settlement_row(close, cfills, heavy_token)
    sold_price = close["up_price"] if sold_side == "UP" else close["dn_price"]

    cfills = fills.get(cid, [])
    leg_fills = [f for f in cfills if f["side"] == "BUY"]
    if not leg_fills:
        return None
    # The heavy leg is the one the sold token belongs to; with one BUY leg
    # filled (the rescue shape) that is the whole set. Several fills on one
    # order sum through the average below.
    matched = sum(f["size"] for f in leg_fills)
    notional = sum(f["size"] * f["price"] for f in leg_fills)
    paid = (notional / matched) if matched > 0 else 0.0
    fill_ts_ms = min(f["venue_ts"] or 0 for f in leg_fills)
    fill_ts_s = fill_ts_ms / 1000.0 if fill_ts_ms else None
    heavy_token = leg_fills[0]["token_id"]
    pair_id = leg_fills[0]["pair_id"]

    shares = close["shares"] if close["shares"] is not None else matched
    sold_per = sold_price if sold_price is not None else (
        (close["proceeds"] / shares) if shares else None)
    pnl = close["realized_pnl"]
    close_ts = close["ts"]

    return {
        "cid": cid,
        "pair_id": pair_id or "--",
        "method": close["method"],
        "reason": close["reason"] if "reason" in close.keys() else None,
        "token": heavy_token,
        "sold_side": sold_side,
        "shares": shares,
        "paid": paid,
        "sold": sold_per,
        "pnl": pnl,
        "fill_ts_s": fill_ts_s,
        "close_ts": close_ts,
        "fill_to_exit": (close_ts - fill_ts_s) if (fill_ts_s and close_ts) else None,
        "aged_out": bool(fill_ts_s and close_ts
                         and (close_ts - fill_ts_s) > PAIRS_EXIT_WINDOW_SEC),
        "quotes": [o for o in orders.get(cid, [])],
    }


def _light_leg_quote(reg: sqlite3.Connection, cid: str, sold_side: str) -> Optional[dict]:
    """The companion quote: side opposite the sold leg, most recent first."""
    want = "DOWN" if sold_side == "UP" else "UP"
    row = reg.execute(
        """
        SELECT ts, token_id, side, price FROM quotes
        WHERE condition_id = ? AND side = ? AND price IS NOT NULL
        ORDER BY ts DESC LIMIT 1
        """,
        (cid, want),
    ).fetchone()
    return dict(row) if row else None


# --- Q2 classifier ------------------------------------------------------------


def classify_exit(reg: sqlite3.Connection, row: dict) -> str:
    """`late_trigger` / `gapped` / `unresolved` from the sampled bid path.

    A recorded reason always wins: the reconstruction brackets bid changes,
    it does not observe them.

    Semantics: `late_trigger` needs a sample that crossed the drift threshold
    while still bid-safe (a rotation where selling was not yet necessary).
    `gapped` is when the first crossing sample already sits at or below the
    sold price -- the bid was gone when the trigger could first have fired.
    A NULL best_bid in the window, or no samples at all, is `unresolved`.
    """
    marks_table = reg.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='queue_marks'"
    ).fetchone()
    if marks_table is None:
        return "unresolved"  # no mark store in this registry
    if row["reason"]:
        return row["reason"]
    if not row["fill_ts_s"] or not row["close_ts"]:
        return "unresolved"
    samples = reg.execute(
        """
        SELECT ts, best_bid FROM queue_marks
        WHERE token_id = ? AND ts BETWEEN ? AND ? ORDER BY ts ASC
        """,
        (row["token"], row["fill_ts_s"], row["close_ts"]),
    ).fetchall()
    bids = [(s["ts"], s["best_bid"]) for s in samples
            if s["best_bid"] is not None]
    if not bids:
        return "unresolved"  # includes the NULL-best_bid case

    fill_price = row["paid"]
    sold_price = row["sold"] or 0.0
    # The route's drift trigger, from core_brain/config.py defaults: exit at
    # max(single_buy_max_loss_usd, single_buy_max_loss_pct * fill).
    threshold = max(0.045, 0.10 * fill_price)
    for ts, b in bids:
        if b > fill_price - threshold:
            continue  # above the trigger: not a crossing sample
        if b <= sold_price:
            # The first crossing sample is already at/below the sold price:
            # the bid gapped through the exit level before the trigger fired.
            return "gapped"
        return "late_trigger"  # crossed while still bid-safe
    return "unresolved"


# --- Q1 upper bound -----------------------------------------------------------


def q1_grace_upper_bound(reg: sqlite3.Connection, row: dict,
                         tape: Optional[sqlite3.Connection],
                         window_sec: float = PAIRS_EXIT_WINDOW_SEC) -> str:
    """Did the sampled opposite-leg book reach the companion quote price?

    Upper bound only: reaching the price is necessary, never sufficient, for
    a maker fill on a quote that was already cancelled.
    """
    quote = _light_leg_quote(reg, row["cid"], row["sold_side"])
    if quote is None:
        return "unanswerable from this store (no companion quote recorded)"
    if tape is None:
        return "unanswerable from this store (no same-window book tape)"
    col = "best_bid_down" if quote["side"] == "DOWN" else "best_bid_up"
    start = row["close_ts"] or 0.0
    samples = tape.execute(
        f"""
        SELECT ts, {col} AS bid FROM book_samples
        WHERE condition_id = ? AND ts >= ? AND ts <= ?
        ORDER BY ts ASC
        """,
        (row["cid"], start, start + window_sec),
    ).fetchall()
    hits = [s["bid"] for s in samples if s["bid"] is not None
            and s["bid"] >= quote["price"]]
    if not hits:
        if all(s["bid"] is None for s in samples):
            return ("unanswerable from this store (no opposite-leg samples "
                    "in the exit window)")
        return (f"opposite-leg samples exist in the window but never reached "
                f"the {quote['side']} quote of {quote['price']:.2f} "
                f"(sampled upper bound: no fill)")
    return (f"sampled upper bound, not an executable fill: the opposite bid "
            f"reached {hits[0]:.2f} >= quote {quote['price']:.2f} within "
            f"{window_sec:.0f}s of the exit")


# --- Q3 features --------------------------------------------------------------


def q3_feature_rows(reg: sqlite3.Connection, exits: list[dict]) -> tuple[int, int, list[str]]:
    """Build the exit-vs-merged quote-time feature block; return sizes + lines."""
    lines: list[str] = []
    def quote_rows(cids: list[str]) -> list[sqlite3.Row]:
        if not cids:
            return []
        marks = ",".join("?" for _ in cids)
        return reg.execute(
            f"SELECT size, price, mid, edge_vs_mid, queue_ahead FROM quotes "
            f"WHERE condition_id IN ({marks}) AND price IS NOT NULL",
            tuple(cids),
        ).fetchall()

    exit_quotes = quote_rows([e["cid"] for e in exits])
    merge_closes = reg.execute(
        f"SELECT condition_id FROM closes WHERE method IN "
        f"({','.join('?' for _ in MERGE_METHODS)})",
        MERGE_METHODS,
    ).fetchall()
    merge_quotes = quote_rows([r["condition_id"] for r in merge_closes])

    def avg(rows, key):
        vals = [r[key] for r in rows if r[key] is not None]
        return (sum(vals) / len(vals)) if vals else None

    lines.append("  Q3 - quote-time features: exits vs merged pairs")
    for key, label in (("size", "quote size"), ("price", "price"),
                       ("mid", "mid"), ("edge_vs_mid", "edge vs mid"),
                       ("queue_ahead", "queue ahead")):
        e, m = avg(exit_quotes, key), avg(merge_quotes, key)
        fmt = lambda v: f"{v:.4f}" if isinstance(v, float) else "--"
        lines.append(f"    {label:<12} exits={fmt(e)}  merged={fmt(m)}")
    n_e, n_m = len(exit_quotes), len(merge_quotes)
    lines.append(f"    sample: n={n_e} exit quotes vs n={n_m} merged quotes. "
                 f"n=4 exits cannot calibrate a gate.")
    lines.append(f"    fields the question needs but no store records: "
                 f"{', '.join(ABSENT_FIELDS)}.")
    return n_e, n_m, lines


# --- report -------------------------------------------------------------------


def report(registry_path: Path, booktape_path: Optional[Path], top: int) -> str:
    reg = _ro(registry_path)
    tape = None
    if booktape_path is not None:
        if not booktape_path.exists():
            print(f"error: book-tape store not found: {booktape_path}",
                  file=sys.stderr)
            raise SystemExit(2)
        tape = _ro(booktape_path)

    fills = _fills_by_condition(reg)
    orders = _orders_by_condition(reg)
    closes = reg.execute(
        f"SELECT * FROM closes WHERE method IN "
        f"({','.join('?' for _ in RESCUE_METHODS)}) ORDER BY realized_pnl ASC",
        RESCUE_METHODS,
    ).fetchall()
    rows = [r for r in (_exit_row(c, fills, orders) for c in closes) if r]

    out: list[str] = []
    say = out.append

    total_loss = sum(r["pnl"] for r in rows if r["pnl"] and r["pnl"] < 0)
    total_capital = sum((r["shares"] or 0) * (r["paid"] or 0) for r in rows)

    say(f"Rescue exit forensics -- {registry_path.name}")
    say(f"  rescue closes: {len(rows)}  total rescue loss: {_usd(total_loss)}"
        f"  capital at risk: {_usd(total_capital)}")
    say("")

    n_e, _, q3_lines = q3_feature_rows(reg, rows[:top])
    for line in q3_lines:
        say(line)
    say("")

    for r in rows[:top]:
        share = (abs(r["pnl"]) / abs(total_loss) * 100) if total_loss else 0.0
        cls = classify_exit(reg, r)
        say(f"  [{r['method']}] pair={r['pair_id']} cond={r['cid']}")
        say(f"    shares={r['shares']:.4g}  paid/share={r['paid']:.4f}  "
            f"sold/share={r['sold'] if r['sold'] is not None else '--'}  "
            f"pnl={_usd(r['pnl'])}  loss share={share:.1f}%")
        f2e = f"{r['fill_to_exit']:.1f}s" if r["fill_to_exit"] else "--"
        say(f"    fill_to_exit={f2e}  classifier={cls}")
        say(f"    Q1: {q1_grace_upper_bound(reg, r, tape)}")
        if r["method"] == "shadow_settlement":
            one_sided = True  # a rescue close on this path is one-sided by construction
            if r["aged_out"]:
                say(f"    AGED OUT: one-sided leg held past "
                    f"{PAIRS_EXIT_WINDOW_SEC:.0f}s into settlement "
                    f"(fail-closed gap: nothing else closes it).")
            else:
                say(f"    settlement close, aged_out=no (one_sided={one_sided}).")
        say("")

    if not rows:
        say("  no rescue closes recorded; the summary table is empty (0 exits).")
        say("")

    say("  Limitations: bid paths are SAMPLED (marks bracket bid changes; the "
        "poll phase is not recorded); Q1 answers are upper bounds, never "
        "executable fills; Q3 carries the n=4 caveat above.")
    for line in out:
        print(line)
    return "\n".join(out)


def main(argv: Optional[list[str]] = None) -> None:
    ap = argparse.ArgumentParser(
        description="Read-only forensic report on single-buy rescue exits (#306).")
    ap.add_argument("--registry", required=True, help="registry store (read-only)")
    ap.add_argument("--booktape", default=None,
                    help="optional same-window book-tape store (read-only)")
    ap.add_argument("--top", type=int, default=4,
                    help="report only the worst N exits (default 4)")
    args = ap.parse_args(argv)
    report(Path(args.registry),
           Path(args.booktape) if args.booktape else None, args.top)


if __name__ == "__main__":
    main()
