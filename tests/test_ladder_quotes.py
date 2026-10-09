"""Gated ladder decision function (issue #325): T3 RED first.

Mode off routes to today's single-price path byte-identically; mode on
inside the OPEN window posts equal-sized rungs around 0.50 on both sides.
"""
from __future__ import annotations

from core_brain.config import MakerConfig
from core_brain.markets import LiveMarket
from core_brain.quotes import Inventory, decide_quotes, route_quotes

NOW = 2_000_000.0


def _market(start_off=-10.0):
    return LiveMarket(condition_id="0xc", market_slug="m", up_token="U",
                      down_token="D", start_ts=NOW + start_off,
                      end_ts=NOW + 290.0, tick_size=0.01, neg_risk=False)


def _books():
    up = {"best_bid": 0.47, "best_ask": 0.50}
    down = {"best_bid": 0.47, "best_ask": 0.50}
    return up, down


def _inv():
    return Inventory()


def test_mode_off_is_byte_identical_to_today():
    """The switch adds nothing: off output equals decide_quotes exactly."""
    cfg = MakerConfig()  # ladder_mode False by default
    up, down = _books()
    expected_intents, expected_why = decide_quotes(
        cfg, up, down, _inv(), 290.0, window_frac=0.1)
    intents, why = route_quotes(cfg, _market(), up, down, _inv(), 290.0,
                                window_frac=0.1, now=NOW)
    assert [(i.side, i.token_id, i.price, i.size)
            for i in intents] == [(i.side, i.token_id, i.price, i.size)
                                  for i in expected_intents]
    assert why == expected_why


def test_open_window_posts_two_equal_rungs_per_side():
    cfg = MakerConfig(ladder_mode=True, ladder_rungs=2,
                      ladder_budget_usd=20.0)
    up, down = _books()
    intents, why = route_quotes(cfg, _market(), up, down, _inv(), 290.0,
                                window_frac=0.0, now=NOW)
    ups = sorted(i.price for i in intents if i.side == "UP")
    dns = sorted(i.price for i in intents if i.side == "DOWN")
    assert len(ups) == 2 and len(dns) == 2
    assert ups[0] != ups[1] and dns[0] != dns[1]  # distinct prices
    sizes = {i.size for i in intents}
    assert len(sizes) == 1  # equal-sized rungs
    assert all(i.price < 0.50 for i in intents)  # resting rungs, not taking
    assert "ladder" in why


def test_blocked_top_rung_is_not_posted():
    """Tight books: the 0.49 rung cannot complete, only 0.48 rests."""
    cfg = MakerConfig(ladder_mode=True, ladder_rungs=2,
                      ladder_budget_usd=20.0)
    up = {"best_bid": 0.49, "best_ask": 0.51}
    down = {"best_bid": 0.49, "best_ask": 0.51}
    intents, _ = route_quotes(cfg, _market(), up, down, _inv(), 290.0,
                              window_frac=0.0, now=NOW)
    ups = sorted(i.price for i in intents if i.side == "UP")
    assert ups == [0.48], "no churn: blocked rungs stay unposted"


def test_unfunded_ladder_posts_nothing():
    """Zero budget (or dust) is a hard gate: no one-share orders."""
    up, down = _books()
    for kw in ({"ladder_budget_usd": 0.0}, {"ladder_budget_usd": 1.0}):
        cfg = MakerConfig(ladder_mode=True, **kw)
        intents, _ = route_quotes(cfg, _market(), up, down, _inv(), 290.0,
                                  window_frac=0.0, now=NOW)
        assert intents == []


def test_outside_window_posts_nothing():
    cfg = MakerConfig(ladder_mode=True, ladder_budget_usd=20.0)
    up, down = _books()
    intents, _ = route_quotes(cfg, _market(start_off=-120.0), up, down,
                              _inv(), 180.0, window_frac=0.5, now=NOW)
    assert intents == []


def test_missing_book_posts_nothing():
    cfg = MakerConfig(ladder_mode=True, ladder_budget_usd=20.0)
    intents, _ = route_quotes(cfg, _market(), {"best_bid": None, "best_ask": None},
                              _books()[1], _inv(), 290.0, window_frac=0.0,
                              now=NOW)
    assert intents == []


def test_asymmetric_volatile_books_price_off_touch():
    """Asymmetric 5m books (e.g. 0.68 / 0.28) quote at the touch, not around 0.50."""
    cfg = MakerConfig(ladder_mode=True, ladder_rungs=2, ladder_budget_usd=20.0)
    up = {"best_bid": 0.68, "best_ask": 0.70}
    down = {"best_bid": 0.28, "best_ask": 0.30}
    intents, why = route_quotes(cfg, _market(), up, down, _inv(), 290.0,
                                window_frac=0.0, now=NOW)
    ups = sorted(i.price for i in intents if i.side == "UP")
    dns = sorted(i.price for i in intents if i.side == "DOWN")
    assert ups == [0.67, 0.68], "UP rungs rest at 0.68 touch and 1 tick below (0.67)"
    assert dns == [0.27, 0.28], "DOWN rungs rest at 0.28 touch and 1 tick below (0.27)"
    assert "ladder 2 rungs/side" in why


def test_uncompletable_market_posts_nothing():
    """When both sides cannot complete under max_pair_cost, nothing is posted."""
    cfg = MakerConfig(ladder_mode=True, ladder_rungs=2, ladder_budget_usd=20.0)
    up = {"best_bid": 0.60, "best_ask": 0.65}
    down = {"best_bid": 0.40, "best_ask": 0.45}
    # UP: 0.60 + 0.45 = 1.05 >= 0.99; 0.59 + 0.45 = 1.04 >= 0.99
    # DOWN: 0.40 + 0.65 = 1.05 >= 0.99; 0.39 + 0.65 = 1.04 >= 0.99
    intents, why = route_quotes(cfg, _market(), up, down, _inv(), 290.0,
                                window_frac=0.0, now=NOW)
    assert intents == []
    assert "no rung completes under the cap" in why


def test_rung_prices_clamped_to_valid_range():
    """Rung prices floor above 0.0 and never emit 0 or negative quotes."""
    cfg = MakerConfig(ladder_mode=True, ladder_rungs=3, ladder_budget_usd=30.0)
    up = {"best_bid": 0.01, "best_ask": 0.03}
    down = {"best_bid": 0.95, "best_ask": 0.97}
    intents, _ = route_quotes(cfg, _market(), up, down, _inv(), 290.0,
                              window_frac=0.0, now=NOW)
    # UP: 0.01 + 0.97 = 0.98 < 0.99; rung 2 (0.00) and rung 3 (-0.01) dropped
    ups = [i.price for i in intents if i.side == "UP"]
    assert ups == [0.01]


def test_route_quotes_never_stamps_pair_id():
    """#397: `route_quotes` itself stamps nothing -- the ladder stamps after."""
    cfg = MakerConfig(ladder_mode=True, ladder_rungs=2,
                      ladder_budget_usd=20.0)
    up, down = _books()
    intents, _ = route_quotes(cfg, _market(), up, down, _inv(), 290.0,
                              window_frac=0.0, now=NOW)
    assert intents, "expected ladder rungs in the open window"
    assert all(i.pair_id is None for i in intents)


def test_make_ladder_decide_stamps_one_pair_id_per_market():
    """#397: the ladder decide closure is the real stamper."""
    from core_brain.ladder import ladder_pair_id, make_ladder_decide
    cfg = MakerConfig(ladder_mode=True, ladder_rungs=2,
                      ladder_budget_usd=20.0)
    market = _market()
    decide = make_ladder_decide(cfg, [market], now_fn=lambda: NOW)
    up = {"token_id": "U", "best_bid": 0.47, "best_ask": 0.50}
    down = {"token_id": "D", "best_bid": 0.47, "best_ask": 0.50}
    intents, _ = decide(cfg, up, down, _inv(), 290.0)
    assert intents, "expected ladder rungs in the open window"
    assert {i.pair_id for i in intents} == {ladder_pair_id("0xc")}

