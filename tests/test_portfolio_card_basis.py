"""The Portfolio Overview card reads one equity basis (#97).

The card used to mix two sources: the headline and Cash Available came from the
venue wallet mark, while the equity chart ended on registry equity
(`starting_capital + total_pnl`). Under a shadow run the simulated gain never
reaches the wallet, so the card showed $85.42 beside a chart ending at $85.77
and a pill claiming +$0.35 — three numbers, one card, no way to reconcile them.

Driven through node against the real `dashboard/static/app.js` and a stub DOM,
so these are the values the page would actually print.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parent / "js" / "portfolio_card_harness.cjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not installed on this host")

STARTING = 85.418581
REALIZED = 0.35
REGISTRY_EQUITY = STARTING + REALIZED
WALLET = STARTING


def _render(portfolio: dict, starting_capital: float | None = STARTING,
            equity_series: list[dict] | None = None) -> dict:
    payload = {
        "kpi": {
            "portfolio": portfolio,
            "trade_analytics": {},
            "equity_series": equity_series or [],
        },
        "status": None if starting_capital is None else {"starting_capital": starting_capital},
    }
    out = subprocess.run([shutil.which("node"), str(HARNESS), json.dumps(payload)],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def _shadow_portfolio(**overrides) -> dict:
    portfolio = {
        "starting_capital": STARTING,
        "realized_pnl": REALIZED,
        "total_value": REGISTRY_EQUITY,
        "open_committed_usd": 0.0,
        "account": {"account_value_usd": WALLET, "cash_usd": WALLET},
    }
    portfolio.update(overrides)
    return portfolio


def test_headline_equals_the_charts_final_point_on_a_shadow_run():
    # Arrange / Act
    card = _render(_shadow_portfolio())

    # Assert — headline, chart basis and gain pill all on registry equity.
    assert card["equity"] == "$85.77"
    assert card["chart_total"] == pytest.approx(REGISTRY_EQUITY)
    assert card["pnl"] == "+$0.35"


def test_cash_available_is_consistent_with_the_headline():
    # Arrange — a dollar of the book is committed to resting orders.
    card = _render(_shadow_portfolio(open_committed_usd=1.0))

    # Act / Assert — cash is headline minus committed, not the wallet mark.
    assert card["equity"] == "$85.77"
    assert card["cash"] == "$84.77"


def test_a_diverging_wallet_is_shown_with_an_explicit_label():
    # Arrange / Act
    card = _render(_shadow_portfolio())

    # Assert
    assert card["wallet_row_display"] != "none"
    assert card["wallet"] == "$85.42"
    assert "not settled" in card["wallet_note"]


def test_the_wallet_line_is_hidden_when_it_agrees_with_registry_equity():
    # Arrange — a live run whose gains did land in the wallet.
    portfolio = _shadow_portfolio(
        account={"account_value_usd": REGISTRY_EQUITY, "cash_usd": REGISTRY_EQUITY})

    # Act
    card = _render(portfolio)

    # Assert
    assert card["wallet_row_display"] == "none"


def test_the_chart_baseline_matches_the_headlines_starting_capital():
    # Arrange — the status payload's starting capital differs from the
    # portfolio's, which is what the header renders against.
    portfolio = _shadow_portfolio(starting_capital=100.0)

    # Act
    card = _render(portfolio, starting_capital=STARTING)

    # Assert
    assert card["starting_capital"] == "$85.42"
    assert card["chart_starting_capital"] == pytest.approx(STARTING)


def test_chart_series_uses_real_closes_and_current_value():
    portfolio = _shadow_portfolio(total_value=86.17)
    equity_series = [
        {"type": "mark", "ts": 1_700_000_000, "v": 85.50},
        {"type": "close", "ts": 1_700_000_060, "v": 85.72, "pnl": 0.30,
         "market": "first-market"},
        {"type": "close", "ts": 1_700_000_120, "v": 86.02, "pnl": 0.30,
         "market": "second-market"},
    ]

    card = _render(portfolio, equity_series=equity_series)

    assert card["chart_series"] == [
        {"label": "Start", "v": pytest.approx(STARTING)},
        {"label": "2023-11-14T22:14:20.000Z", "v": 85.72,
         "pnl": 0.30, "market": "first-market"},
        {"label": "2023-11-14T22:15:20.000Z", "v": 86.02,
         "pnl": 0.30, "market": "second-market"},
        {"label": "Current", "v": 86.17},
    ]
    assert 'stroke-dasharray="2,2"' in card["chart_html"]
    assert ">Current</text>" in card["chart_html"]


def test_chart_series_is_flat_when_there_are_no_closes():
    card = _render(_shadow_portfolio(total_value=STARTING), equity_series=[
        {"type": "mark", "ts": 1_700_000_000, "v": 90.0},
    ])

    assert card["chart_series"] == [
        {"label": "Start", "v": pytest.approx(STARTING)},
        {"label": "Current", "v": pytest.approx(STARTING)},
    ]
