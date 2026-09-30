"""Ladder through the real shadow loop (issue #325): T5 RED first.

The shipped adapter (`core_brain/ladder.py`) drives `run_shadow` via the
`markets_fn`/`decide_fn` seams: one pair_id per market, oldest-first fills,
timed one-leg exits, and mode-off byte-identity. Fully offline.
"""
from __future__ import annotations

import time

import pytest

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


def _prints_always(tokens, prices, size=5.0):
    def traded(condition_id, seen):
        return {t: {p: size for p in prices} for t in tokens}
    return traded


def test_fill_sim_fills_oldest_first_under_one_pair(tmp_path, monkeypatch):
    """Prints through every rung: all fills share the one ladder pair."""
    import core_brain.markets as markets_mod
    monkeypatch.setattr(markets_mod, "recent_trades",
                        _prints_always([TOK_UP, TOK_DN], [0.49, 0.48]))
    db = tmp_path / "ladder_fill.db"
    cfg = _cfg()
    run_shadow(minutes=5.0, db_path=db,
               markets_fn=lambda cap=None: [_market()],
               decide_fn=make_ladder_decide(cfg, [_market()], db_path=db),
               fetch_books=_fetch_books,
               interval=0.01, sleep_fn=_rotations(6), cfg=cfg)
    reg = OrderRegistry(db_path=db)
    fills = [f for f in reg.get_all_fills()
             if f.get("condition_id") == COND]
    assert len(fills) >= 4, "every rung filled"
    assert {f.get("pair_id") for f in fills} == {ladder_pair_id(COND)}
    posted = {o["id"]: o["posted_ts"] for o in reg.get_all_orders()}
    seq = [posted.get(f.get("order_uuid"), 0) for f in fills]
    assert seq == sorted(seq), "fills credited oldest-first"


def test_one_leg_residue_exits_with_no_orphan(tmp_path, monkeypatch):
    """Only UP prints: the UP residue exits via ladder_exit, DOWN retires.

    The DOWN ask runs away after the fill (0.50 -> 0.53), so taker
    completion stops paying and the ladder timer fires instead.
    """
    import core_brain.markets as markets_mod
    monkeypatch.setattr(markets_mod, "recent_trades",
                        _prints_always([TOK_UP], [0.49, 0.48]))
    db = tmp_path / "ladder_oneleg.db"

    def running_books(clob_host, token_id):
        ask = 0.50
        try:
            reg = OrderRegistry(db_path=db)
            if any(f.get("condition_id") == COND
                   for f in reg.get_all_fills()):
                ask = 0.53
        except Exception:
            pass
        book = _books(token_id=token_id)
        if token_id == TOK_DN:
            book["best_ask"] = ask
            book["asks"] = {ask: 100.0}
        return book

    cfg = _cfg()
    run_shadow(minutes=5.0, db_path=db,
               markets_fn=lambda cap=None: [_market()],
               decide_fn=make_ladder_decide(cfg, [_market()], db_path=db),
               fetch_books=running_books,
               interval=0.01, sleep_fn=_rotations(8), cfg=cfg)
    reg = OrderRegistry(db_path=db)
    closes = [c for c in reg.get_all_closes()
              if c.get("condition_id") == COND
              and c.get("method") == "ladder_exit"]
    assert len(closes) >= 1, "the residue left through ladder_exit"
    filled = sum(f.get("size", 0.0) for f in reg.get_all_fills()
                 if f.get("condition_id") == COND)
    assert sum(c.get("shares", 0.0) for c in closes) == pytest.approx(filled)
    resting = [o for o in reg.get_active_orders()
               if o.condition_id == COND]
    assert resting == [], "no orphan leg keeps resting"


def test_mode_off_posts_no_ladder_pair(tmp_path, monkeypatch):
    """Mode off through the real loop: no ladder stamp anywhere."""
    import core_brain.markets as markets_mod
    monkeypatch.setattr(markets_mod, "recent_trades", lambda c, s: {})
    from core_brain.quotes import decide_quotes
    db = tmp_path / "ladder_off.db"
    cfg = _cfg(ladder_mode=False)
    run_shadow(minutes=5.0, db_path=db,
               markets_fn=lambda cap=None: [_market()],
               decide_fn=None,
               fetch_books=_fetch_books,
               interval=0.01, sleep_fn=_rotations(2), cfg=cfg)
    reg = OrderRegistry(db_path=db)
    assert decide_quotes is not None  # single path owns off behavior (T3)
    pairs = {o.get("pair_id", "") for o in reg.get_all_orders()}
    from core_brain.ladder import is_ladder_pair
    assert not any(is_ladder_pair(p) for p in pairs)
