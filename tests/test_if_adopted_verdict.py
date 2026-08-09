"""The if-adopted allocator verdict the ranker attaches to every rejection.

The GRADUATED lane shows the allocator's verdict for adopted markets; the
FILTERS lane now estimates the SAME number for markets the ranker rejected,
so near-misses -- a depth-$950 book that would have cleared the 2%/day floor
-- are visible a stage before adoption. These tests pin that estimate to the
fleet's own `_alloc_verdict` refusal math, so the two lanes cannot drift.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import rank_markets as rank   # noqa: E402
from strategy.allocate import spread_capture_daily   # noqa: E402
from strategy.fleet import _alloc_verdict   # noqa: E402


def _reward(their_score=5000.0, daily=5.0, max_spread=3.5, vol=900_000,
            spread=0.02, source="rewards"):
    return {"source": source, "daily": daily, "their_score": their_score,
            "max_spread": max_spread, "volume_24h": vol, "spread": spread}


def _k():
    """Per-share score of the quote we would rest -- the fleet's `reallocate`
    converts the competition reading into dollars through exactly this."""
    return rank.score_per_share(3.5 / 100.0, rank.OFFSET)


def test_reward_reject_matches_allocator_refusal_math():
    """The ranker's if-adopted marginal must equal what `_alloc_verdict`
    reports as the first-dollar marginal for a refused market."""
    v = rank._if_adopted(_reward(their_score=150.0, daily=20.0))
    av = _alloc_verdict(0.0, 50, 20.0, 150.0, _k(), 0.02)
    assert v["marg_pct_day"] == av["first_marginal_pct"]
    assert v["marg_pct_day"] == 2.45
    assert v["would_fund"] is True
    # The allocator's refusal reason confirms: first dollar cleared the floor.
    assert "clears" in av["reason"]
    assert v["threshold_pct"] == 2.0
    assert v["pot_day"] == 20.0
    assert v["competition"] == 150.0


def test_crowded_reject_stays_below_floor():
    v = rank._if_adopted(_reward(their_score=5000.0, daily=5.0))
    av = _alloc_verdict(0.0, 50, 5.0, 5000.0, _k(), 0.02)
    assert v["marg_pct_day"] == av["first_marginal_pct"]
    assert v["would_fund"] is False
    assert "below" in av["reason"]


def test_spread_reject_uses_recomputed_pot():
    """A spread market carries `daily` 0; the pot is reconstructed from
    volume and spread exactly as `MarketState.refresh_pot` does, so the
    estimate and the fleet size the same market on the same number."""
    v = rank._if_adopted(_reward(source="spread", daily=0.0, vol=800_000,
                                 spread=0.04))
    pot = spread_capture_daily(800_000.0, 0.04, rank._CFG.spread_capture_frac)
    assert v["pot_day"] == round(pot, 2)
    av = _alloc_verdict(0.0, 5, pot, 5000.0, _k(), 0.02)
    assert v["marg_pct_day"] == av["first_marginal_pct"]


def test_no_book_reading_returns_none():
    """Identity rejections happen before the book is fetched -- there is no
    score reading, so there must be no estimate at all."""
    assert rank._if_adopted({"source": "rewards", "daily": 5.0}) is None


def test_zero_per_share_score_guards_to_zero_not_nan():
    """max_spread == 2 * OFFSET makes k == 0 (we would score nothing per
    share): competitor depth is unbounded and the marginal must be 0.0, not
    NaN -- the same guard the fleet's `_alloc_verdict` carries."""
    v = rank._if_adopted(_reward(their_score=150.0, daily=20.0,
                                 max_spread=2.0))
    assert v["marg_pct_day"] == 0.0
    assert v["would_fund"] is False
    assert v["pot_day"] == 20.0


def test_spread_without_volume_is_unpayable():
    v = rank._if_adopted({"source": "spread", "daily": 0.0,
                          "their_score": 500.0, "max_spread": 3.5,
                          "volume_24h": None, "spread": None})
    assert v["marg_pct_day"] == 0.0
    assert v["would_fund"] is False
    assert v["reason"].startswith("unpayable")


def test_empty_window_estimate_is_flagged_trap():
    """A book with ~nothing resting in the reward window divides the pot by
    ~zero competition and reports an absurd %/day -- the Dem-retirees
    890%/day and UK-inflation 4,938%/day shapes. That is the empty-book
    mirage the depth gate exists to catch, not an opportunity: `trap` must be
    True so the lanes render it amber instead of a green 'would clear the
    floor' win."""
    v = rank._if_adopted(_reward(their_score=0.1, daily=16.0))
    assert v["would_fund"] is True   # clears the floor arithmetically...
    assert v["trap"] is True         # ...but the estimate is a mirage
    assert v["marg_pct_day"] > 100.0
    assert "mirage" in v["reason"]


def test_depth_reject_under_half_bar_is_flagged_trap():
    """A depth reject at under half the gate bar (measured in the reason) is
    the same empty-book shape measured directly: a $22 book is a trap even
    when the estimate clears the floor."""
    r = _reward(their_score=150.0, daily=20.0)
    r["reject_reason"] = "YES: top-3 bid depth $22.00 <= $1,000.00"
    v = rank._if_adopted(r)
    assert v["trap"] is True


def test_credible_near_miss_is_not_trapped():
    """A depth reject close to the bar with a sane estimate stays a genuine
    near-miss -- the candidate a trial would actually adopt."""
    r = _reward(their_score=150.0, daily=20.0)
    r["reject_reason"] = "YES: top-3 bid depth $950.00 <= $1,000.00"
    v = rank._if_adopted(r)
    assert v["would_fund"] is True
    assert v["trap"] is False
