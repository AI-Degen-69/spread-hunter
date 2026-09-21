"""A losing number has to look like a loss.

Every card that carried a signed figure picked its colour from "do we have any
samples yet" -- `n > 0 ? 'positive' : ''` -- so once a single close existed the
whole panel went green. On 2026-09-06 the live board showed an expectancy of
-$0.34, a mean return of -13.85%, a Sharpe of -0.63 and a profit factor of
0.13x, all in the green of a win, beside a hero pill whose chevron pointed up
at a $2.40 loss. The run banner said "net loss" in green too, because the CSS
had a rule for `profit` and none for `loss`.

The amounts read `$-2.40`, which is the minus in the wrong place: the sign
belongs in front of the amount, not inside the currency.

Journeys under test:
1. As the Owner, a negative figure is red wherever it appears.
2. As the Owner, the arrow beside a loss points down.
3. As the Owner, a loss reads `-$2.40`.
4. As the Owner, the Monte Carlo end value is inside the card, not clipped.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from core_brain.kpi import _signed_usd

HARNESS = Path(__file__).resolve().parent / "js" / "sign_colours_harness.cjs"

# The zero fixture these tiles shipped with: numbers the backend never produced.
_DEFAULT_RISK = {
    "payoff_ratio": 0.0, "var_95_usd": 0.0, "cvar_95_usd": 0.0,
    "kelly_fraction": 0.0, "half_kelly": 0.0,
}

requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node is not installed on this host")


def _render(realized: float = -2.40, *, expectancy: float = -0.343,
            mean_return: float | None = -13.85,
            sharpe: float = -0.63, profit_factor: float = 0.13,
            mc_end: float | None = None, win_rate_ci95=None,
            risk: dict | None = None, include_risk_fields: bool = True,
            payload_version=252) -> dict:
    ta = {
        "n_closes": 7, "expectancy_usd": expectancy,
        "mean_return_pct": mean_return, "sharpe_ratio": sharpe,
        "sortino_ratio": -0.53, "profit_factor": profit_factor,
        "win_rate": 0.286,
        "win_rate_ci95": (win_rate_ci95 if win_rate_ci95 is not None
                          else {"lower": 0.0822, "upper": 0.6411}),
    }
    # Issue #251: the risk tiles used to be fed hard-coded zeros. They are real
    # payload fields now, so a test picks between the historical zero fixture,
    # genuinely absent fields, and measured values.
    if include_risk_fields:
        ta.update(_DEFAULT_RISK if risk is None else risk)
    stats = {"trade_analytics": ta}
    payload = {
        "kpi": {
            "portfolio": {"starting_capital": 79.78, "realized_pnl": realized,
                          "total_value": 81.28, "cash_usd": 81.28},
            "statistical_analytics": stats,
        },
        "status": {},
        "statistical_analytics": stats,
        "payload_version": payload_version,
    }
    if mc_end is not None:
        stats["monte_carlo"] = {
            "steps": [
                {"cycle": 0, "p01": 100, "p10": 100, "p50": 100,
                 "p90": 100, "p99": 100},
                {"cycle": 100, "p01": mc_end - 20, "p10": mc_end - 10,
                 "p50": mc_end, "p90": mc_end + 10, "p99": mc_end + 20},
            ],
            "paths": 1000, "prob_positive_return": 0.0,
            "worst_case_drawdown_pct": -56.05,
        }
        payload["cycles"] = 100
    out = subprocess.run([shutil.which("node"), str(HARNESS), json.dumps(payload)],
                         capture_output=True, text=True, check=True,
                         encoding="utf-8")
    return json.loads(out.stdout)


@requires_node
def test_a_loss_is_red_everywhere_it_is_shown():
    # Arrange — the figures the live board was showing in green.
    rendered = _render()

    # Assert
    assert "negative" in rendered["pnl_pill_class"]
    assert "negative" in rendered["spread_class"]
    assert rendered["quant_expectancy"] == "negative"
    assert rendered["quant_sharpe"] == "negative"


@requires_node
def test_a_profit_factor_below_one_is_not_a_win():
    # Arrange — 0.13x means the losses are eight times the wins. It is never
    # negative, so a sign test alone would keep painting it green.
    assert _render(profit_factor=0.13)["quant_profit_factor"] == "negative"
    assert _render(profit_factor=1.8)["quant_profit_factor"] == "positive"


@requires_node
def test_a_win_rate_is_not_coloured_as_a_verdict():
    # Arrange — 28.6% is a rate, not a gain. Green claimed it was good; the
    # break-even rate this strategy needs is far higher.
    assert _render()["quant_win_rate"] == ""


@requires_node
def test_the_arrow_beside_a_loss_points_down():
    # Arrange — the chevron was fixed markup, so it pointed up at every number.
    down = _render(realized=-2.40)["pnl_arrow"]
    up = _render(realized=+2.40)["pnl_arrow"]

    # Assert — in this 24x24 chevron the middle point is the tip: y=9 above the
    # ends (up), y=15 below them (down).
    assert down == "18 9 12 15 6 9"
    assert up == "18 15 12 9 6 15"


@requires_node
def test_the_minus_goes_in_front_of_the_dollar():
    # Arrange
    rendered = _render(realized=-2.40)

    # Assert — `$-2.40` reads as an odd currency; `-$2.40` reads as a loss.
    assert rendered["pnl_text"] == "-$2.40"
    assert rendered["spread_text"] == "-$2.40"
    assert _render(realized=2.40)["pnl_text"] == "+$2.40"


@requires_node
def test_a_gain_still_reads_as_a_gain():
    rendered = _render(realized=2.40, expectancy=0.34, sharpe=1.2)
    assert "positive" in rendered["pnl_pill_class"]
    assert "positive" in rendered["spread_class"]
    assert rendered["quant_expectancy"] == "positive"
    assert rendered["quant_sharpe"] == "positive"


@requires_node
def test_the_monte_carlo_end_value_stays_inside_the_card():
    # Arrange — the badge sat at `w - padR + 2` anchored at its start, so it
    # ran off the right edge of the viewBox and the card clipped it.
    rendered = _render(mc_end=57.93)

    # Assert — inside the plot, right-anchored, and signed from the number
    # rather than with a hardcoded `+` in front of a negative.
    assert rendered["mc_end_anchor"] is True
    assert rendered["mc_end_x"] <= 480.0
    assert rendered["mc_end_label"] == "-$42.07"
    assert "Median Return: -$42.07" in rendered["mc_footer"]


@requires_node
def test_a_monte_carlo_gain_keeps_its_plus():
    rendered = _render(mc_end=142.07)
    assert rendered["mc_end_label"] == "+$42.07"


def test_the_run_verdict_puts_the_minus_in_front_of_the_dollar():
    """The banner read `-$2.40 net loss · $-2.40 realized` -- both spellings of
    the same number in one line, and one of them wrong."""
    assert _signed_usd(-2.40) == "-$2.40"
    assert _signed_usd(2.40) == "+$2.40"
    assert _signed_usd(0.0) == "+$0.00"


@requires_node
def test_the_wilson_interval_is_read_off_the_shape_the_report_sends():
    """`win_rate_ci95` is `{lower, upper}`, and the card indexed it as `[0]`
    and `[1]`. Both ends came back `undefined`, so the interval the report had
    computed rendered as `[NaN%-NaN%]` and was never once displayed."""
    assert _render()["quant_ci"] == "95% CI: [8%–64%]"
    # An array is still accepted, for any caller that sends one.
    assert _render(win_rate_ci95=[0.0822, 0.6411])["quant_ci"] == "95% CI: [8%–64%]"


@requires_node
def test_a_confidence_interval_with_a_missing_end_shows_no_interval():
    # Better the placeholder than half an interval presented as a whole one.
    assert _render(win_rate_ci95={"lower": 0.08})["quant_ci"] == "95% CI: [0%–0%]"


@requires_node
def test_the_expectancy_tile_puts_the_minus_in_front_of_the_dollar():
    assert _render(expectancy=-0.343)["quant_expectancy_text"] == "-$0.343"
    assert _render(expectancy=0.343)["quant_expectancy_text"] == "$0.343"


# -- Issue #251: a measured metric shows its value, never a fabricated zero ----

@requires_node
def test_the_risk_tiles_show_the_measured_values():
    """VaR, CVaR, Kelly and Payoff come from the backend now."""
    rendered = _render(risk={
        "payoff_ratio": 1.50, "var_95_usd": 4.05, "cvar_95_usd": 5.00,
        "kelly_fraction": 0.1667, "half_kelly": 0.0833,
    })
    assert rendered["quant_var"] == "$4.05"
    assert rendered["quant_cvar"] == "CVaR Tail: $5.00"
    assert rendered["quant_kelly"] == "16.7%"
    assert "8.3%" in rendered["quant_kelly_sub"]
    assert rendered["quant_payoff"] == "Payoff Ratio: 1.50x"


@requires_node
def test_a_null_risk_metric_reads_unmeasured_not_zero():
    """A field the payload carries as null is unmeasurable, not zero risk."""
    rendered = _render(include_risk_fields=False)
    assert rendered["quant_var"] == "unmeasured"
    assert rendered["quant_cvar"] == "CVaR Tail: unmeasured"
    assert rendered["quant_kelly"] == "unmeasured"
    assert "unmeasured" in rendered["quant_kelly_sub"]
    assert "unmeasured" in rendered["quant_payoff"]


@requires_node
def test_a_null_expectancy_and_mean_read_unmeasured_not_zero():
    """#260: the last two fabricated zeros in the grid. A null expectancy is
    unmeasured, not $0.000; a null mean is unmeasured, not 0.00%."""
    rendered = _render(expectancy=None, mean_return=None)
    assert rendered["quant_expectancy_text"] == "unmeasured"
    assert rendered["quant_expectancy"] == ""
    assert rendered["quant_expectancy_sub"].startswith(
        "unmeasured mean return / trade")


@requires_node
def test_a_stale_backend_says_so_once():
    """Field-absent used to be silent. Now the operator is told to restart."""
    stale = _render(payload_version=250)
    assert stale["stale_note_shown"] is True
    assert stale["stale_warn_count"] == 1
    assert "older than this page" in stale["stale_warn_text"]


@requires_node
def test_a_current_backend_shows_no_note_and_warns_not():
    fresh = _render(payload_version=252)
    assert fresh["stale_note_shown"] is False
    assert fresh["stale_warn_count"] == 0


@requires_node
def test_a_payload_predating_the_version_field_reads_as_stale():
    """The backend that never heard of `payload_version` is the stale one."""
    stale = _render(payload_version=None)
    assert stale["stale_note_shown"] is True
    assert "absent" in stale["stale_warn_text"]
