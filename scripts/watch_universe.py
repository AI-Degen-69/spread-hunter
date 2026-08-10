"""watch_universe.py — background watcher for the evening slate.

The operator does not sit and watch `grep rerank.log`. This script does it:
every `--interval` seconds it records one timestamped line to
`logs/universe_watch.log` with

  * the fleet's picked universe size (run/markets.json),
  * the most recent ranker census (scored/rejected/wrote-top-N from
    logs/rerank.log),
  * live top-3 bid depth + spread for the main-line esports books (the
    current evening slate), marked with whether each clears the $1,000 / 6c
    selector bars.

It is read-only against the venue (the same public endpoints the ranker
uses) and read-only against the run directory. Safe to start anytime;
kill it by closing the console or `taskkill /PID <pid>`. The fleet itself
is untouched -- this is an observer, not a writer.

Usage:
    python -m scripts.watch_universe [--interval 300] [--once]
"""
import argparse
import datetime
import json
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategy.markets import parse_book                       # noqa: E402
LOG = ROOT / "logs" / "universe_watch.log"
MARKETS = ROOT / "run" / "markets.json"
RERANK = ROOT / "logs" / "rerank.log"

# The selector bars, kept in sync with strategy/config.py by hand here
# because the watcher is a script and must not import the strategy config
# (the fleet may be mid-write on the same DB the config loader touches).
# `parse_book` is imported from strategy.markets -- that module imports no
# config and touches no DB, so the isolation principle holds.
MIN_TOP3_DEPTH_USD = 1_000.0
MAX_BOOK_SPREAD = 0.06

GAMMA = "https://gamma-api.polymarket.com/markets"
CLOB = "https://clob.polymarket.com/book"

ESPORTS_RE = re.compile(r"\b(LoL|League of Legends|Counter-Strike|CS2|Dota)\b",
                        re.IGNORECASE)


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _picked() -> int:
    try:
        m = json.loads(MARKETS.read_text(encoding="utf-8"))
        return len(m) if isinstance(m, list) else 0
    except Exception:
        return -1  # unreadable is a real answer, not zero


def _last_census() -> str:
    try:
        lines = RERANK.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return "rerank.log unreadable"
    for ln in reversed(lines):
        if "wrote top" in ln or ("scored" in ln and "rejected" in ln):
            return ln.strip()[:200]
    return "no census line yet"


def _esports_books(session: requests.Session) -> list[dict]:
    """Live top-3 depth + spread for main-line esports series books.

    Uses the same shape the ranker's spread-universe path reads: gamma rows
    carry `clobTokenIds` (a JSON string), not inline `tokens`.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    out: list[dict] = []
    try:
        rows = session.get(GAMMA, params={
            "closed": "false", "active": "true", "archived": "false",
            "order": "volume24hr", "ascending": "false", "limit": 50,
            "end_date_min": now.isoformat()}, timeout=30).json()
    except Exception:
        return out
    # Gamma answers with a bare list on some paths and `{"data": [...]}` on
    # others; `gamma_spread_universe` in scripts/rank_markets.py already
    # normalises both. Without this the dict form iterates its KEYS, `m` is a
    # string, and `m.get` raises AttributeError OUTSIDE the try above -- and
    # this process is a supervised child, so that is a restart loop rather
    # than one bad sample.
    if isinstance(rows, dict):
        rows = rows.get("data") or []
    if not isinstance(rows, list):
        return out
    for m in rows:
        q = m.get("question") or ""
        if not ESPORTS_RE.search(q):
            continue
        # Main lines only: the BO3/BO5 series winner. Game/Map/Round
        # submarkets ("Game 1 Winner", "Game Handicap") are blocked by the
        # selector and are noise for build-up watching.
        if re.search(r"\b(Game|Map|Round|Handicap)\b", q, re.IGNORECASE):
            continue
        if "BO3" not in q and "BO5" not in q:
            continue
        try:
            toks = json.loads(m.get("clobTokenIds") or "[]")
        except (TypeError, ValueError):
            continue
        if len(toks) != 2:
            continue
        depths, spreads, one_sided = [], [], []
        for tok in toks:
            try:
                b = session.get(CLOB, params={"token_id": tok}, timeout=12).json()
            except Exception:
                depths.append(None)
                spreads.append(None)
                one_sided.append(True)  # unreadable is not tradeable either
                continue
            try:
                book = parse_book(b, tok)
            except ValueError:
                depths.append(None)
                spreads.append(None)
                one_sided.append(True)
                continue
            bids = list(book["bids"].items())
            asks = list(book["asks"].items())
            if not bids or not asks:
                depths.append(None)
                spreads.append(None)
                one_sided.append(True)
                continue
            depths.append(sum(p * s for p, s in sorted(bids, reverse=True)[:3]))
            spreads.append(min(p for p, _ in asks) - max(p for p, _ in bids))
            one_sided.append(False)
        out.append({
            "title": q[:60],
            "end": (m.get("endDate") or "")[:16].replace("T", " "),
            "depth": depths,
            "spread": spreads,
            "one_sided": one_sided,
        })
    return out


def _fmt_book(b: dict) -> str:
    dy, dn = b["depth"]
    sy, sn = b["spread"]
    oy, on = b.get("one_sided", [False, False])
    # Depth is None exactly when the book was unreadable OR empty, and both
    # of those paths also set one_sided -- so ONE-SIDED covers them.
    y_txt = "ONE-SIDED" if oy else f"${dy:>7,.0f}/sp {sy:.3f}"
    n_txt = "ONE-SIDED" if on else f"${dn:>7,.0f}/sp {sn:.3f}"
    y_ok = (not oy and dy is not None and sy is not None
            and dy >= MIN_TOP3_DEPTH_USD and sy <= MAX_BOOK_SPREAD)
    n_ok = (not on and dn is not None and sn is not None
            and dn >= MIN_TOP3_DEPTH_USD and sn <= MAX_BOOK_SPREAD)
    return (f"{b['title']:58s} end {b['end']:11s} "
            f"YES {y_txt:>19s} [{'Y' if y_ok else '.'}] "
            f"NO {n_txt:>19s} [{'Y' if n_ok else '.'}]")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--interval", type=float, default=300.0,
                   help="seconds between samples (default 300)")
    p.add_argument("--once", action="store_true",
                   help="take one sample and exit")
    p.add_argument("--max-samples", type=int, default=0,
                   help="stop after N samples (0 = run until killed)")
    args = p.parse_args()

    LOG.parent.mkdir(exist_ok=True)
    session = requests.Session()
    n = 0
    while True:
        n += 1
        picked = _picked()
        census = _last_census()
        books = _esports_books(session)
        lines = [f"[{_now()}] sample {n}: picked={picked}",
                 f"  census: {census}"]
        if books:
            lines.append(f"  esports main lines ({len(books)} live):")
            lines += [f"    {_fmt_book(b)}" for b in books]
            cleared = [b for b in books if all(
                (not o and d is not None and d >= MIN_TOP3_DEPTH_USD
                 and s is not None and s <= MAX_BOOK_SPREAD)
                for d, s, o in zip(b["depth"], b["spread"],
                                   b.get("one_sided", [False] * len(b["depth"]))))]
            if cleared:
                lines.append(f"  ** {len(cleared)} book(s) CLEAR the selector bars: "
                             + "; ".join(b["title"] for b in cleared))
        else:
            lines.append("  no main-line esports books readable")
        if picked > 0:
            lines.append(f"  ** FLEET HAS {picked} MARKET(S) — universe live")
        msg = "\n".join(lines)
        # The console gets an ASCII-folded copy; the log file gets the real
        # text. Windows PowerShell 5.1 hands `print` a legacy codepage, and
        # these lines carry an em dash plus venue titles with accents and
        # curly quotes -- enough for UnicodeEncodeError, which would land
        # AFTER the sample was taken and restart this supervised child on
        # every pass. scripts/rank_markets.py documents the same failure.
        print(msg.encode("ascii", "replace").decode("ascii"), flush=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(msg + "\n")
        if args.once:
            break
        if args.max_samples and n >= args.max_samples:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
