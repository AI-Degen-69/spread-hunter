"""Dollar-denominated risk primitives.

Every number in this file comes from the 2026-08-05 forensic reading, where
three share-denominated limits were armed and none of them bound: a 233.40
share position cost $190.26 against a nominal 360-share cap. These tests pin
the unit the limits are stated in, not the limits themselves.
"""
import dataclasses
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategy import risk                               # noqa: E402
from strategy.config import load as load_cfg            # noqa: E402
from strategy.quotes import Inventory                   # noqa: E402

BASE = load_cfg()


def _cfg(**kw):
    return dataclasses.replace(BASE, **kw)


def _book(bid, ask, depth=500.0):
    b = {"token_id": "TOK", "best_bid": bid, "best_ask": ask}
    if bid is not None:
        b["bids"] = {bid: depth}
    if ask is not None:
        b["asks"] = {ask: depth}
    return b


# --- naked_side / naked_usd -------------------------------------------------

def test_flat_inventory_has_no_naked_side():
    assert risk.naked_side(Inventory()) is None


def test_balanced_inventory_has_no_naked_side():
    inv = Inventory(up_shares=100.0, down_shares=100.0,
                    up_cost=52.0, down_cost=46.0)
    assert risk.naked_side(inv) is None


def test_naked_side_is_the_heavier_leg():
    inv = Inventory(up_shares=200.0, down_shares=100.0,
                    up_cost=120.0, down_cost=46.0)
    assert risk.naked_side(inv) == "UP"


def test_naked_usd_is_zero_for_a_balanced_book():
    inv = Inventory(up_shares=100.0, down_shares=100.0,
                    up_cost=52.0, down_cost=46.0)
    assert risk.naked_usd(inv, "UP") == 0.0
    assert risk.naked_usd(inv, "DOWN") == 0.0


def test_naked_usd_is_zero_on_the_light_side():
    inv = Inventory(up_shares=200.0, down_shares=100.0,
                    up_cost=120.0, down_cost=46.0)
    assert risk.naked_usd(inv, "DOWN") == 0.0


def test_naked_usd_reproduces_the_observed_loss():
    """lol-maz-mg1: 233.40 UP shares at an average of 0.8152 = $190.26.

    The share cap read 233 against a limit of 360 and stayed silent. The
    dollar reading is the one that describes what was at stake.
    """
    inv = Inventory(up_shares=233.40, down_shares=0.0,
                    up_cost=233.40 * 0.8152, down_cost=0.0)
    assert risk.naked_usd(inv, "UP") == pytest.approx(190.26, abs=0.01)


def test_naked_usd_values_only_the_excess_at_average_cost():
    """200 UP against 100 DOWN at a 0.60 UP average is $60 at risk, not $120.

    The hedged 100 shares pay exactly $1.00 whichever way the market resolves,
    so only the excess can go to zero.
    """
    inv = Inventory(up_shares=200.0, down_shares=100.0,
                    up_cost=120.0, down_cost=40.0)
    assert risk.naked_usd(inv, "UP") == pytest.approx(60.0)


def test_naked_usd_uses_average_cost_not_the_current_mark():
    """The amount at risk is what we paid. It must not shrink because the mid
    already moved against us -- that would loosen the cap exactly when the
    position is losing."""
    inv = Inventory(up_shares=100.0, down_shares=0.0,
                    up_cost=82.0, down_cost=0.0)
    assert risk.naked_usd(inv, "UP") == pytest.approx(82.0)


# --- risk_utilization -------------------------------------------------------

def test_utilization_is_zero_when_flat():
    assert risk.risk_utilization(_cfg(max_naked_usd=120.0), Inventory(), "UP") == 0.0


def test_utilization_is_the_dollar_fraction_of_the_budget():
    inv = Inventory(up_shares=100.0, down_shares=0.0, up_cost=60.0)
    got = risk.risk_utilization(_cfg(max_naked_usd=120.0), inv, "UP")
    assert got == pytest.approx(0.5)


def test_utilization_clamps_at_one_over_budget():
    inv = Inventory(up_shares=300.0, down_shares=0.0, up_cost=240.0)
    assert risk.risk_utilization(_cfg(max_naked_usd=120.0), inv, "UP") == 1.0


def test_a_zero_budget_disables_the_measure_rather_than_dividing_by_zero():
    """0 means unset, the same escape hatch every other cap in this config
    has. It must not read as 'infinite utilization'."""
    inv = Inventory(up_shares=300.0, down_shares=0.0, up_cost=240.0)
    assert risk.risk_utilization(_cfg(max_naked_usd=0.0), inv, "UP") == 0.0


# --- book_health ------------------------------------------------------------

def test_a_one_sided_book_is_unhealthy_and_says_so():
    res = risk.book_health(_book(0.52, None), _cfg())
    assert not res.ok
    assert "one-sided" in res.reason


def test_the_recorded_settled_book_is_refused():
    """wta-kalinsk-kessler ended quoting 0.999 bid against a 0.001 ask.

    There is no spread to capture on a market that has already decided, and
    the naked leg it would create is decided against us.
    """
    res = risk.book_health(_book(0.999, 0.001), _cfg())
    assert not res.ok
    assert "settled" in res.reason


def test_a_near_certain_one_way_book_is_refused_as_settled():
    res = risk.book_health(_book(0.997, 0.999), _cfg())
    assert not res.ok
    assert "settled" in res.reason


def test_a_wide_book_is_refused():
    """0.26/0.42 measured live: the whole 4.5c reward window lies inside the
    spread, so quoting in it means being the most exposed order in the book."""
    res = risk.book_health(_book(0.26, 0.42), _cfg())
    assert not res.ok
    assert "wide" in res.reason


def test_a_thin_book_is_refused():
    cfg = _cfg(min_book_depth_sh=200.0)
    res = risk.book_health(_book(0.52, 0.53, depth=50.0), cfg)
    assert not res.ok
    assert "thin" in res.reason


def test_a_healthy_book_is_accepted():
    res = risk.book_health(_book(0.52, 0.53, depth=500.0), _cfg())
    assert res.ok
    assert res.reason == ""


def test_missing_depth_is_reported_unevaluated_rather_than_failed():
    """Recorded history carries a mid but no depth (U7). A replay must be able
    to tell 'depth passed' apart from 'depth was never measured'."""
    book = {"token_id": "TOK", "best_bid": 0.52, "best_ask": 0.53}
    res = risk.book_health(book, _cfg())
    assert res.ok
    assert res.depth_evaluated is False


def test_present_depth_is_reported_evaluated():
    res = risk.book_health(_book(0.52, 0.53, depth=500.0), _cfg())
    assert res.depth_evaluated is True
