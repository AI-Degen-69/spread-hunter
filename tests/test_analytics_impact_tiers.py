"""The analytics surface is ordered by decision impact, not by arrival order.

Issue #90 asks for components grouped by what they decide, with Tier 1
dominating attention. The panel it replaced laid every deck out at the same
weight, so a 1,000-path Monte Carlo projection sat level with the question of
whether our average close is profitable at all.

Tier 1 is the go/no-go pair (the confidence band on the average close, and the
execution drop-off) plus the two charts that explain them. Tier 2 is
drill-down. Tier 3 is exploration -- projections and the what-if simulator --
and it renders last no matter where it sits in the document.

Journeys under test:
1. As the Owner, the go/no-go row is the first thing on the analytics surface.
2. As the Owner, every analytics deck declares which tier it belongs to.
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


# The decks that carry a tier, and the tier each one belongs to.
TIER_BY_CARD = {
    "card-pnl-ci": "1",
    "card-execution-funnel": "1",
    "card-position-returns-dist": "1",
    "card-markout": "1",
    "card-hist-kde": "2",
    "card-prob-bell": "2",
    "card-monte-carlo": "3",
    "card-sensitivity-simulator": "3",
}


def _card_attrs(html: str, card_id: str) -> str:
    """The opening tag of one card, by id."""
    match = re.search(rf'<div[^>]*id="{re.escape(card_id)}"[^>]*>', html)
    assert match, f"card {card_id!r} is not on the analytics surface"
    return match.group(0)


# -- Tier 1 leads ------------------------------------------------------------

def test_the_go_no_go_row_comes_before_every_other_analytics_component(index_html):
    # Arrange -- document order is what a screen reader and an unstyled render
    # both follow, so the row has to lead there and not only in CSS.
    surface = index_html.index('id="analytics-surface"')
    tier1_row = index_html.index('id="tier1-decision-row"')
    kpi_grid = index_html.index('id="kpi-grid"')
    charts = index_html.index('id="analytics-charts-matrix"')

    # Assert
    assert surface < tier1_row < kpi_grid < charts


def test_both_go_no_go_cards_are_present_and_labelled_tier_1(index_html):
    # Assert -- the label is on screen, so the ranking is legible rather than
    # implied by position alone.
    assert 'id="pnl-ci-readout"' in index_html
    assert 'id="execution-funnel"' in index_html
    assert index_html.count("TIER 1") >= 2


# -- Every deck declares its tier --------------------------------------------

@pytest.mark.parametrize("card_id,tier", sorted(TIER_BY_CARD.items()))
def test_each_analytics_deck_declares_its_impact_tier(index_html, card_id, tier):
    # Assert -- an undeclared deck falls back to the CSS default and silently
    # outranks whatever it was placed above.
    attrs = _card_attrs(index_html, card_id)
    assert f'data-tier="{tier}"' in attrs


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
