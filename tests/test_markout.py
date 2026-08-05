"""Markout: the cost of being filled.

These markets resolve in 2026-2027, so settlement P&L reads $0.00 for months.
Markout answers the same question in hours: after we were filled, where did the
price actually go?
"""
import dataclasses
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strategy.markout import markout_per_share, _stats_from_rows  # noqa: E402


def _row(mk, source="venue_clean"):
    """A row with NO `size` key -- deliberately.

    Every production row comes from `store.markout_rows()`, which is
    `SELECT *` over a table that has carried a `size` column since it was
    created, so the absent key never occurs live. It stands here for a caller
    that supplies no size at all, and the documented rule is that such a row
    weighs 1.0: one row, one vote, i.e. exactly the unweighted mean this
    module computed before sizes entered it.
    """
    return {"markout": mk, "ref_mid_source": source}


def _sized(mk, size, source="venue_clean"):
    return {"markout": mk, "size": size, "ref_mid_source": source}


def test_buy_that_drifts_down_is_a_loss():
    """Bought UP at 0.57, mid later 0.55 -- the fill was informed against us."""
    assert markout_per_share(0.57, 0.55, "UP") == pytest.approx(-0.02)


def test_buy_that_drifts_up_is_a_gain():
    """Each side is measured against its OWN token's mid, so one formula does
    both. Buying DOWN at 0.38 into a 0.40 DOWN mid is a 2c gain."""
    assert markout_per_share(0.38, 0.40, "DOWN") == pytest.approx(0.02)


def test_stats_ignore_markets_under_min_sample():
    """3 fills on a thin book is noise. Refusing to render a verdict is the
    point -- evicting a sound market on noise costs real rent."""
    stats = _stats_from_rows([_row(-0.02)] * 3, min_sample=20)
    assert stats["verdict"] == "insufficient_sample"
    assert stats["mean_per_share"] is None


def test_stats_report_mean_once_sample_is_adequate():
    stats = _stats_from_rows([_row(-0.02)] * 20, min_sample=20)
    assert stats["mean_per_share"] == pytest.approx(-0.02)
    assert stats["verdict"] == "losing"


def test_contaminated_rows_are_excluded():
    """A live run that cannot exclude our own resting size from the reference
    mid would measure our own footprint and report it as edge. Those rows are
    marked and must not count toward the sample."""
    rows = [_row(-0.02, source="contaminated")] * 30
    stats = _stats_from_rows(rows, min_sample=20)
    assert stats["verdict"] == "insufficient_sample"


def test_drift_excludes_our_own_entry_discount():
    """THE correction. Total markout bakes in the 2c we quote under mid, so a
    market whose price never moved reads '+2.15c, fills are great' and the
    gate can only ever trip on a catastrophe. Drift measures the move alone.
    """
    from strategy.markout import drift_per_share
    # bought 2c under a 0.59 mid; the mid never moved
    assert drift_per_share(ref_mid=0.59, mid_later=0.59) == pytest.approx(0.0)
    # same fill, but the price fell 1c afterwards -- that is the real cost
    assert drift_per_share(ref_mid=0.59, mid_later=0.58) == pytest.approx(-0.01)


def test_a_stationary_market_is_not_reported_as_edge():
    """Regression on the live reading: +2.11c captured spread, +0.04c drift.
    The verdict must follow the drift, not the 2.15c total."""
    from strategy.markout import _stats_from_rows
    rows = [{"markout": -0.004, "ref_mid_source": "venue_clean"}] * 25
    assert _stats_from_rows(rows, min_sample=20)["verdict"] == "losing"


def test_positive_mean_reads_as_earning():
    stats = _stats_from_rows([_row(0.01)] * 25, min_sample=20)
    assert stats["verdict"] == "earning"


# --- size weighting ----------------------------------------------------------

def test_one_large_toxic_fill_outvotes_nine_small_good_ones():
    """AE5, and the whole reason this unit exists.

    An unweighted mean lets a 10-share print and a 200-share print cast the
    same vote: (9*+1c + 1*-5c)/10 = +0.4c, and the market reads EARNING while
    290 shares changed hands at a net loss. Weighted it is
    (200*-5c + 90*+1c)/290 = -3.14c -- the number the money actually saw.
    """
    rows = [_sized(-0.05, 200.0)] + [_sized(+0.01, 10.0)] * 9
    stats = _stats_from_rows(rows, min_sample=1)
    assert stats["mean_per_share"] == pytest.approx(-9.1 / 290.0)
    assert stats["mean_per_share"] < 0
    # ...and the evidence is thin, because one fill carries 69% of the size.
    # Kish: 290^2 / (200^2 + 9*10^2) = 84100/40900 = 2.056.
    assert stats["n"] == pytest.approx(2.0562, abs=1e-4)
    assert stats["n"] < 3
    assert stats["n_rows"] == 10


def test_a_sample_dominated_by_one_fill_does_not_license_an_exit():
    """The same ten rows, judged against the real per-market minimum. A raw
    count of 10 would clear a min_sample of 8; 2.06 effective does not, and
    the gate is handed `insufficient_sample` instead of a licence to evict."""
    rows = [_sized(-0.05, 200.0)] + [_sized(+0.01, 10.0)] * 9
    stats = _stats_from_rows(rows, min_sample=8)
    assert stats["verdict"] == "insufficient_sample"
    assert stats["mean_per_share"] is None
    assert stats["n_rows"] == 10


def test_equal_sizes_give_an_effective_sample_equal_to_the_row_count():
    """Why Kish and not some invented discount. `sum(w)^2 / sum(w^2)` equals
    the row count EXACTLY when the sizes are equal, so `markout_min_sample`
    and the doubling rule in `gate.next_state` keep the meaning they were
    tuned with instead of needing to be re-derived against a new scale."""
    for size in (1.0, 25.0, 100.0):
        stats = _stats_from_rows([_sized(-0.02, size)] * 20, min_sample=20)
        assert stats["n"] == 20
        assert stats["n_rows"] == 20
        assert stats["mean_per_share"] == pytest.approx(-0.02)
        assert stats["verdict"] == "losing"


def test_rows_with_no_size_key_each_weigh_one():
    """The documented rule for a caller that supplies no size: unit weight,
    which reproduces the old unweighted mean exactly rather than silently
    zeroing the row and reporting `insufficient_sample` on real evidence."""
    stats = _stats_from_rows([_row(-0.02)] * 20, min_sample=20)
    assert stats["n"] == 20
    assert stats["n_rows"] == 20
    assert stats["mean_per_share"] == pytest.approx(-0.02)


def test_contaminated_rows_are_excluded_before_weighting():
    """Exclusion happens FIRST. A contaminated 10000-share row would otherwise
    dominate the weighted mean with a measurement of our own footprint --
    strictly worse than the unweighted version of the same bug."""
    rows = [_sized(+0.05, 10000.0, source="contaminated")]
    rows += [_sized(-0.02, 100.0)] * 20
    stats = _stats_from_rows(rows, min_sample=20)
    assert stats["mean_per_share"] == pytest.approx(-0.02)
    assert stats["n"] == 20
    assert stats["n_rows"] == 20


def test_all_zero_sizes_return_insufficient_sample_without_raising():
    """Total weight zero. Kish and the weighted mean both divide by it, so
    this is the one input that can crash the aggregation; it must read as an
    absence of evidence, not a ZeroDivisionError inside the fleet loop."""
    for bad in (0.0, None):
        stats = _stats_from_rows([_sized(-0.05, bad)] * 30, min_sample=1)
        assert stats["verdict"] == "insufficient_sample"
        assert stats["mean_per_share"] is None
        assert stats["n"] == 0
        assert stats["n_rows"] == 30


def test_the_gate_consumes_the_new_shape_unmodified():
    """`strategy/gate.py` is untouched by this unit: it reads `verdict`,
    `mean_per_share` and `n`, and `n` is now the effective sample.

    Ten 200-share fills at -5c against ten 10-share fills at +1c is -4.71c
    weighted and exactly -2.00c unweighted. The old mean therefore landed ON
    the -2c catastrophic threshold and the strict `<` left the market in the
    book; the weighted mean is more than twice past it, and the magnitude
    bypass fires from NORMAL straight to EXITED with no new branch anywhere
    in the gate."""
    from strategy import gate
    from strategy.config import load as load_cfg
    cfg = dataclasses.replace(load_cfg(), markout_min_sample=8,
                              markout_widen_threshold=-0.005,
                              markout_catastrophic_threshold=-0.020)
    rows = [_sized(-0.05, 200.0)] * 10 + [_sized(+0.01, 10.0)] * 10
    stats = _stats_from_rows(rows, min_sample=8)
    assert stats["mean_per_share"] < cfg.markout_catastrophic_threshold
    assert gate.next_state(gate.NORMAL, stats, cfg) == gate.EXITED
    # ...and the thin-evidence case still moves nothing, from either state.
    thin = _stats_from_rows([_sized(-0.05, 200.0)] + [_sized(+0.01, 10.0)] * 9,
                            min_sample=8)
    assert gate.next_state(gate.NORMAL, thin, cfg) == gate.NORMAL
    assert gate.next_state(gate.WIDENED, thin, cfg) == gate.WIDENED
