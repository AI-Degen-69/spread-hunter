"""The one analytics page is ordered by decision impact, not by arrival order.

Issue #90 asked for components grouped by what they decide, with the go/no-go
pair dominating attention. #749 consolidated two analytics pages into one and
deleted the decks that carried the per-card `data-tier` labels, so the ranking
that survives is the one an operator actually walks: the confidence band on the
average close and the execution drop-off lead, the drill-downs follow.

The chart-grid ordering rules stay in CSS, so their assertions stay here.

Journeys under test:
1. As the Owner, the go/no-go pair is the first thing on the analytics page.
2. As the Owner, both of those readings are present, once each.
3. As the Owner, exploration decks never outrank the decks that decide.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "dashboard" / "static"
INDEX = STATIC / "index.html"
STYLES = STATIC / "styles.css"


@pytest.fixture(scope="module")
def index_html() -> str:
    return INDEX.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def styles_css() -> str:
    return STYLES.read_text(encoding="utf-8")


# -- The go/no-go pair leads -------------------------------------------------

def test_the_go_no_go_pair_comes_before_every_other_analytics_component(index_html):
    # Arrange -- document order is what a screen reader and an unstyled render
    # both follow, so the pair has to lead there and not only in CSS.
    page = index_html.index('id="analysis-v2"')
    ci = index_html.index('id="pnl-ci-readout"')
    funnel = index_html.index('id="execution-funnel"')
    slippage = index_html.index('id="stop-slippage-readout"')

    # Assert
    assert page < ci < funnel < slippage


def test_both_go_no_go_cards_are_present_once_each(index_html):
    # Assert -- one render site per reading: a second copy is how the pages
    # drifted apart in the first place.
    assert index_html.count('id="pnl-ci-readout"') == 1
    assert index_html.count('id="execution-funnel"') == 1


# -- Order follows tier, not document position -------------------------------

def test_the_chart_grid_orders_its_cards_by_tier(styles_css):
    # Arrange -- the grid keeps document order for accessibility and reorders
    # visually, so the rule has to exist for all three tiers.
    for tier in ("1", "2", "3"):
        rule = re.search(
            rf'\.analytics-chart-grid\s*>\s*\[data-tier="{tier}"\][^{{]*{{[^}}]*}}',
            styles_css,
        )
        assert rule, f'no ordering rule for tier {tier}'
        assert "order:" in rule.group(0)


def test_exploration_never_sorts_above_the_decks_that_decide(styles_css):
    # Assert -- reading the three order values back, Tier 1 sorts first.
    orders = {}
    for tier in ("1", "2", "3"):
        rule = re.search(
            rf'\.analytics-chart-grid\s*>\s*\[data-tier="{tier}"\][^{{]*{{([^}}]*)}}',
            styles_css,
        )
        value = re.search(r"order:\s*(-?\d+)", rule.group(1))
        assert value, f"tier {tier} declares no order value"
        orders[tier] = int(value.group(1))

    assert orders["1"] < orders["2"] < orders["3"]
