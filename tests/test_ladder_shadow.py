"""Ladder through the real shadow loop (issue #325): T5 RED first.

The shipped adapter (`core_brain/ladder.py`) drives `run_shadow` via the
`markets_fn`/`decide_fn` seams: one pair_id per market, oldest-first fills,
timed one-leg exits, and mode-off byte-identity. Fully offline.
"""
from __future__ import annotations

import time

from core_brain.config import MakerConfig
from core_brain.ladder import ladder_pair_id, make_ladder_decide
from core_brain.markets import LiveMarket
from core_brain.order_registry import OrderRegistry
from core_brain.shadow_run import run_shadow

COND = "0xcond-ladder-shadow"
TOK_UP = "tok-ladder-up"
TOK_DN = "tok-ladder-dn"
NOW = time.time()


def _market(start_off=-10.0):
    return LiveMarket(condition_id=COND, market_slug="ladder-mkt",
                      up_token=TOK_UP, down_token=TOK_DN,
                      start_ts=NOW + start_off, end_ts=NOW + 290.0,
                      tick_size=0.01, neg_risk=False)


def _books(**kw):
    book = {"token_id": kw.pop("token_id", ""),
            "best_bid": 0.47, "best_ask": 0.50,
            "bids": {0.47: 100.0}, "asks": {0.50: 100.0}}
    book.update(kw)
    return book


def _fetch_books(clob_host, token_id):
    return _books(token_id=token_id)


def _rotations(n):
    calls = []

    def sleep_fn(seconds):
        calls.append(seconds)
        if len(calls) >= n:
            raise KeyboardInterrupt()
    return sleep_fn


def _cfg(**kw):
    base = dict(ladder_mode=True, ladder_rungs=2, ladder_exit_sec=0.0,
                ladder_budget_usd=20.0, ladder_open_window_sec=10_000.0)
    base.update(kw)
    return MakerConfig(**base)


def test_placement_posts_two_rungs_per_side_under_one_pair(tmp_path,
                                                            monkeypatch):
    """Two rotations, no trades: 2 distinct rungs/side, one ladder pair."""
    import core_brain.markets as markets_mod
    monkeypatch.setattr(markets_mod, "recent_trades", lambda c, s: {})
    db = tmp_path / "ladder_place.db"
    cfg = _cfg()
    run_shadow(minutes=5.0, db_path=db,
               markets_fn=lambda cap=None: [_market()],
               decide_fn=make_ladder_decide(cfg, [_market()], db_path=db),
               fetch_books=_fetch_books,
               interval=0.01, sleep_fn=_rotations(2), cfg=cfg)
    reg = OrderRegistry(db_path=db)
    orders = reg.get_active_orders()
    ups = sorted({o.price for o in orders if o.token_id == TOK_UP})
    dns = sorted({o.price for o in orders if o.token_id == TOK_DN})
    assert len(ups) >= 2 and len(dns) >= 2
    assert len({o.pair_id for o in orders}) == 1
    assert next(iter({o.pair_id for o in orders})) == ladder_pair_id(COND)
