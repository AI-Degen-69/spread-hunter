"""Live-books ladder shadow trial (issue #331). No signer, no venue writes.

Drives :func:`core_brain.shadow_run.run_shadow` against live BTC/ETH 5-min
books through the injectable ``markets_fn``/``decide_fn`` seams, with the
merged ladder path (``make_ladder_decide`` over ``route_quotes``) and a
funded paper ``ladder_budget_usd``. Fills come from the live public trade
tape (the loop's default reader); books come from the live public book
endpoint. The venue client is the signer-less shadow client ``run_shadow``
builds itself, so the run cannot sign anything.

Markets are discovered live via ``discover_ladder_series`` (throttled,
accumulated for the session) and each new market gets its own ladder
adapter on first sight. The per-market report reuses the locked rehearsal
schema from ``scripts/ladder_shadow_rehearsal.py`` (plus a live-books
source tag and the trial config), so the probe-vs-live comparison is a
field-for-field diff. Exit closes on the live ladder path use
``ladder_exit``/``naked_exit`` (not just ``single_buy_exit``), so the
report recounts exits over all ladder exit methods and surfaces resting
shares (filled but not yet merged/exited/settled) explicitly.

    python scripts/ladder_live_books_trial.py --db data/NN_shadow_ladder_live.db \\
        --out reports/ladder_live_books_<stamp>.json --minutes 240
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from contextlib import closing
from dataclasses import replace as dc_replace
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Running `python scripts/ladder_live_books_trial.py` puts `scripts/` on the
# path, not the repo root, so sibling modules are invisible without this.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.ladder_shadow_rehearsal import (  # noqa: E402
    build_report,
    refuse_output,
)

REFUSED_DB_NAMES = ("orders.db",)
REDISCOVER_SEC = 60.0


class LiveTrialRefused(RuntimeError):
    """A production store the trial must not touch."""


def refuse_db(path: str | Path) -> Path:
    """Reject the production registry before anything is constructed."""
    p = Path(path)
    if p.name in REFUSED_DB_NAMES:
        raise LiveTrialRefused(f"Refusing production store: {p.name}.")
    return p


def run_trial(*, series_slugs: list[str], gamma_host: str,
              db_path, out_path: Optional[Path],
              minutes: float, budget_usd: float,
              open_window_sec: float, max_markets: int,
              interval: float = 5.0,
              run_id: Optional[str] = None,
              discover_fn=None, cfg=None, sleep_fn=None) -> dict:
    """One live-books trial: discover, rest rungs, report placement+fill+exit."""
    from core_brain.ladder import LADDER_EXIT_METHODS, make_ladder_decide
    from core_brain.markets import discover_ladder_series
    from core_brain.order_registry import get_connection
    from core_brain.shadow_run import (
        _default_fetch_books,
        run_shadow,
        shadow_cfg,
    )

    db_path = refuse_db(db_path)
    if out_path is not None:
        out_path = refuse_output(out_path)
        if db_path.resolve() == out_path.resolve():
            raise LiveTrialRefused(
                "Refusing to use the same resolved path for db_path and out_path.")

    if cfg is None:
        cfg = shadow_cfg()
    cfg = dc_replace(cfg, ladder_mode=True, ladder_rungs=2,
                     ladder_exit_sec=60.0, ladder_budget_usd=float(budget_usd),
                     ladder_open_window_sec=float(open_window_sec))

    session: list = []
    seen_cids: set[str] = set()
    state: dict = {"last_discover": 0.0}
    discover = discover_fn or discover_ladder_series

    def markets_fn(cap=None):
        now = time.time()
        if now - state["last_discover"] >= REDISCOVER_SEC:
            state["last_discover"] = now
            try:
                found = discover(gamma_host, series_slugs,
                                 open_window_sec=open_window_sec)
            except Exception as e:  # noqa: BLE001 - degrade, keep session
                log.warning("ladder discovery failed, keeping %d markets: %s",
                            len(session), e)
                found = []
            for m in found:
                if m.condition_id not in seen_cids:
                    seen_cids.add(m.condition_id)
                    if len(session) < max_markets:
                        session.append(m)
        return list(session)

    adapters: dict[str, object] = {}

    def decide_fn(dec_cfg, up_book, down_book, inv, t_rem, wf=None):
        token = (up_book or {}).get("token_id", "")
        adapter = adapters.get(token)
        if adapter is None:
            market = next(
                (m for m in session
                 if m.up_token == token or m.down_token == token), None)
            if market is None:
                return [], "not a ladder market"
            adapter = make_ladder_decide(dec_cfg, [market], db_path=db_path)
            adapters[market.up_token] = adapter
            adapters[market.down_token] = adapter
        return adapter(dec_cfg, up_book, down_book, inv, t_rem, wf)

    # Prime the session so an empty open window fails fast, before the loop.
    markets_fn(cap=None)
    # Force one fresh discovery regardless of the throttle on re-runs.
    state["last_discover"] = 0.0
    markets_fn(cap=None)

    run_shadow(
        minutes=minutes, db_path=db_path,
        markets_fn=markets_fn,
        decide_fn=decide_fn,
        fetch_books=_default_fetch_books(),
        interval=interval,
        run_id=run_id or f"ladder-live-{int(time.time())}",
        cfg=cfg,
        sleep_fn=sleep_fn,
    )

    if not session:
        report = {
            "issue": 331, "series": "+".join(series_slugs),
            "shape": "live", "rungs": [], "markets": 0,
            "pair_ids": [], "placements": {"orders": 0},
            "fills": {"n": 0, "shares": 0.0, "oldest_first": True},
            "exits": {}, "conservation": {},
            "live_execution": False, "source": "live-books",
            "note": "no markets inside the open window during the trial",
        }
    else:
        report = build_report(series="+".join(series_slugs), shape="live",
                              rungs=(), tape_markets=session, db_path=db_path)
        report["issue"] = 331
        report["source"] = "live-books"
        report["config"] = {
            "ladder_mode": True, "ladder_rungs": 2,
            "ladder_exit_sec": 60.0, "ladder_budget_usd": float(budget_usd),
            "ladder_open_window_sec": float(open_window_sec),
            "minutes": minutes,
        }
        # The live ladder path exits via ladder_exit/naked_exit, which the
        # rehearsal schema (written for the mimic) does not count. Recount
        # over every ladder exit method and surface resting shares.
        with closing(get_connection(Path(db_path))) as conn:
            closes = [dict(r) for r in conn.execute("SELECT * FROM closes")]
        exited = [c for c in closes
                  if c.get("method") in LADDER_EXIT_METHODS]
        exited_shares = sum(float(c.get("shares") or 0.0) for c in exited)
        accounted = (float(report["exits"]["merged_shares"])
                     + exited_shares
                     + float(report["exits"]["settled_shares"]))
        report["exits"]["ladder_exits"] = len(exited)
        report["exits"]["exited_shares"] = exited_shares
        report["conservation"]["accounted_shares"] = accounted
        report["conservation"]["resting_shares"] = max(
            0.0, float(report["conservation"]["filled_shares"]) - accounted)
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2))
    return report


def main(argv: Optional[list] = None) -> int:
    """CLI: trial the ladder against live books. No signer, no venue writes."""
    from core_brain import rehearsal

    rehearsal.declare_rehearsal()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--series-slug", action="append",
                    default=["btc-up-or-down-5m", "eth-up-or-down-5m"],
                    help="repeatable; defaults cover BTC+ETH 5-min")
    ap.add_argument("--gamma-host",
                    default="https://gamma-api.polymarket.com")
    ap.add_argument("--minutes", type=float, default=240.0,
                    help="wall-clock time box (~4h covers ~48 windows)")
    ap.add_argument("--budget-usd", type=float, default=5.0,
                    help="paper ladder budget (5 funds 1 share/rung, shape 2)")
    ap.add_argument("--open-window-sec", type=float, default=30.0)
    ap.add_argument("--max-markets", type=int, default=60)
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--db", required=True,
                    help="per-run shadow store; orders.db refused")
    ap.add_argument("--out", required=True,
                    help="report JSON path (not under data/ or run/)")
    ap.add_argument("--run-id", default=None)
    a = ap.parse_args(argv)

    report = run_trial(series_slugs=a.series_slug, gamma_host=a.gamma_host,
                       db_path=Path(a.db), out_path=Path(a.out),
                       minutes=a.minutes, budget_usd=a.budget_usd,
                       open_window_sec=a.open_window_sec,
                       max_markets=a.max_markets, interval=a.interval,
                       run_id=a.run_id)
    print(json.dumps({"series": report["series"], "markets": report["markets"],
                      "pair_ids": report.get("pair_ids"),
                      "placements": report["placements"],
                      "fills": report["fills"], "exits": report["exits"],
                      "conservation": report["conservation"]}, indent=2))
    if not report["markets"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
