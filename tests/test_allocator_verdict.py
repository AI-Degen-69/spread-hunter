"""The allocator verdict that drives the pipeline view's GRADUATED lane.

`reallocate` computes why each adopted market is or isn't funded and surfaces
it on the live payload (`_live["alloc"]`); the pipeline endpoint passes it
through to the board. These tests pin the verdict numbers and reason strings
against the same math the water-fill uses (`strategy/allocate.marginal`), so
the reason shown can never drift from the decision that produced it.
"""
from strategy.fleet import _alloc_verdict


def test_funded_market_shows_marginal_at_final_size():
    # avg_theirs=100, k=0.05 => competitor depth T = 2000 dollars; pot $50/day.
    # First dollar earns 50/2000 = 2.5%/day > 2% floor, so the water-fill
    # funds it; dilution lowers the marginal at the final size.
    v = _alloc_verdict(dollars=60.0, min_size=5, pot=50.0,
                       avg_theirs=100.0, k=0.05, floor=0.02)
    assert v["funded"] is True
    # A pair costs ~$1, so $60 funds 60 shares per side.
    assert v["shares"] == 60
    assert v["dollars"] == 60.0
    assert v["threshold_pct"] == 2.0
    assert v["competition_avg"] == 100.0
    assert v["first_marginal_pct"] == 2.5
    assert v["marginal_pct"] < v["first_marginal_pct"]
    assert v["reason"] == "funded 60 shares"


def test_unfunded_below_floor_reports_the_exact_numbers():
    # avg_theirs=100, k=0.02 => T = 5000; pot $50/day.
    # First dollar earns 50/5000 = 1%/day < 2% floor -> refused. The reason
    # and the number that caused it must agree.
    v = _alloc_verdict(dollars=0.0, min_size=5, pot=50.0,
                       avg_theirs=100.0, k=0.02, floor=0.02)
    assert v["funded"] is False
    assert v["shares"] == 0
    assert v["first_marginal_pct"] == 1.0
    assert v["marginal_pct"] == 1.0
    assert v["reason"] == "unfunded: below 2.00%/day floor"


def test_zero_score_competition_is_zero_not_nan():
    """k == 0 means we score nothing per share, so competitor depth is
    unbounded and the first dollar earns 0 -- never NaN, which would render
    as a fake verdict on the board."""
    v = _alloc_verdict(dollars=0.0, min_size=5, pot=50.0,
                       avg_theirs=100.0, k=0.0, floor=0.02)
    assert v["first_marginal_pct"] == 0.0
    assert v["marginal_pct"] == 0.0
    assert "below 2.00%/day floor" in v["reason"]


def test_unpayable_market_names_the_reason():
    """A market with no pot (spread/volume unmeasured) is refused before the
    floor even applies; the verdict must say so rather than quote a marginal
    on an unknown pot."""
    v = _alloc_verdict(dollars=0.0, min_size=5, pot=0.0,
                       avg_theirs=50.0, k=0.02, floor=0.02)
    assert v["funded"] is False
    assert v["reason"] == "unpayable: no pot (spread/volume unmeasured)"


def test_allocation_below_the_venue_minimum_quotes_nothing():
    """Dollars were allocated but `shares_for` refuses below min_size -- the
    market is present in the water-fill yet quotes nothing, and the reason
    names the minimum rather than the floor."""
    v = _alloc_verdict(dollars=8.0, min_size=50, pot=50.0,
                       avg_theirs=100.0, k=0.05, floor=0.02)
    assert v["funded"] is False
    assert "under the 50-share minimum" in v["reason"]


def test_first_dollar_clears_floor_but_minimum_cannot_pay():
    """A market whose first dollar beats the floor can still end at 0 when
    the drop happened at the indivisible minimum lot (allocate_fundable's
    promote/drop pass). The reason must name that, not the floor."""
    # avg_theirs=100, k=0.05 => T=2000; pot $60/day: the first dollar earns
    # 3%/day (> floor), so the refusal must be the indivisible lot, not the
    # marginal bar.
    v = _alloc_verdict(dollars=0.0, min_size=100, pot=60.0,
                       avg_theirs=100.0, k=0.05, floor=0.02)
    assert v["funded"] is False
    assert v["first_marginal_pct"] == 3.0
    assert "clears 2.00%/day but the 100-share minimum cannot pay it" \
        in v["reason"]
