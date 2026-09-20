"""DOM and copy contracts for the analytics surface.

These replace four tests that described the pre-#94 analytics tab. Every
marker they asserted on (`analytics-hist-wrap`, `portfolioLine`, "Required
Observations", "Price Band") was gone from the page long before anything
failed, because the file was parked outside `tests/` and never ran. They were
kept skipped as a reminder of what the old panel guaranteed; #90 is the
redesign they were waiting for, so this is the replacement.

What the surface owes the Owner, and what these tests hold it to:

- The two go/no-go readings lead, and say what they mean in words.
- Nothing on the surface invents a number. Absent measurements read as
  absent -- no fabricated zero, no placeholder series.
- The squares and gates stay in human language, not field names.

Journeys under test:
1. As the Owner, Tier 1 renders the confidence verdict in plain words.
2. As the Owner, the drop-off names its worst step in plain words.
3. As the Owner, every figure on the surface comes from the payload.
4. As the Owner, the panels read as English, not as JSON keys.
"""
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "dashboard" / "static"


@pytest.fixture(scope="module")
def app_js() -> str:
    return (STATIC / "app.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def index_html() -> str:
    return (STATIC / "index.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def styles_css() -> str:
    return (STATIC / "styles.css").read_text(encoding="utf-8")


# -- Tier 1 says what it means -----------------------------------------------

def test_the_confidence_card_states_its_verdict_in_words(app_js):
    # Every branch of the verdict is spelled out. A band that reaches below
    # zero has to say so in the copy, not leave it to a colour.
    for phrase in ("BAND CLEAR OF ZERO",
                   "BAND INCLUDES A LOSS DOWN TO",
                   "BAND ENTIRELY BELOW ZERO",
                   "NO BAND YET"):
        assert phrase in app_js

    # Both levels are named, on one quantity.
    assert "Mean realized PnL per close" in app_js
    assert "mean_pnl_ci" in app_js


def test_a_sample_too_small_for_a_band_explains_itself(app_js):
    # The old panel's failure mode was a zero that looked like a measurement.
    assert "A confidence interval needs at least two closes" in app_js
    assert "nothing has been measured" in app_js


def test_the_drop_off_card_names_its_worst_step(app_js):
    assert "Worst step:" in app_js
    assert "carried on" in app_js
    assert "No step has lost anything yet" in app_js
    assert "No execution telemetry in this run yet." in app_js


# -- Nothing is invented -----------------------------------------------------

def test_the_tier_1_cards_read_every_figure_from_the_payload(app_js):
    # The renderers reach for payload fields; none of them carries a literal
    # sample series like the placeholder the old portfolio line shipped with.
    for field in ("execution_funnel", "mean_pnl_ci", "negative_depth_usd",
                  "retained_pct", "worst_step"):
        assert field in app_js
    assert "points = [100,101,100.5,102,101.8,103,104,103.6,105,106]" not in app_js


def test_an_unmeasured_retention_reads_as_absent_rather_than_zero(app_js):
    # `retained_pct` is NULL when no leg reached the earlier stage. Rendering
    # that as 0% would report a verdict the run has not earned.
    assert "'no rate yet'" in app_js


# -- The surface is ordered, and stays in English ----------------------------

def test_the_go_no_go_row_leads_the_surface(index_html):
    assert index_html.index('id="tier1-decision-row"') < index_html.index('id="kpi-grid"')
    assert 'id="pnl-ci-readout"' in index_html
    assert 'id="execution-funnel"' in index_html


def test_the_squares_and_gates_use_human_language(app_js):
    for title in ("Average Profit Per Close", "Mean Return Per Trade",
                  "Annualized Sharpe Ratio", "Executed Sample Size"):
        assert title in app_js

    # The gates table names what each row measures and what it needs, in
    # column headers a reader can follow without the schema.
    assert ("<th>Hypothesis / decision gate</th><th>Parameter &amp; standard</th>"
            "<th>Required threshold</th><th>Observed value</th><th>Gate verdict</th>") in app_js
    assert "n_required" not in app_js


def test_the_tier_1_row_has_styling_of_its_own(styles_css):
    # Tier 1 is a distinct band above the decks, not another card in the grid.
    for selector in (".tier1-decision-row", ".pnl-ci-verdict", ".funnel-bar-fill"):
        assert selector in styles_css


# -- Issue #248: the $ vs % sign divergence explains itself -------------------

def test_expectancy_tiles_explain_the_dollar_vs_percent_sign_gap(app_js):
    # Both grids carry the click-triggered explainer; pinned labels stay put.
    assert "Why can $ and % disagree?" in app_js
    assert "percents average % per close over measured closes only" in app_js
    assert "Average Profit Per Close" in app_js
    assert "Mean Return Per Trade" in app_js


def test_expectancy_tiles_show_the_companion_sublabels(app_js):
    # Dollar-weighted bridge + measured-count coverage on both surfaces.
    assert "dollar-weighted" in app_js
    assert "dollar-weighted unmeasured" in app_js
    assert "n_measured_returns" in app_js
    assert "dollar_weighted_return_pct" in app_js
    assert "measured" in app_js
