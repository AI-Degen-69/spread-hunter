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
            equity_series: list[dict] | None = None,
            timeframe: str = "ALL") -> dict:
    payload = {
        "kpi": {
            "portfolio": portfolio,
            "trade_analytics": {},
            "equity_series": equity_series or [],
        },
        "status": None if starting_capital is None else {"starting_capital": starting_capital},
        "timeframe": timeframe,
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
    # Arrange — Issue #252: the session snapshot (status) differs from the
    # run's DB anchor (portfolio). The card describes the run, so the DB
    # anchor wins everywhere on it.
    portfolio = _shadow_portfolio(starting_capital=100.0)

    # Act
    card = _render(portfolio, starting_capital=STARTING)

    # Assert
    assert card["starting_capital"] == "$100.00"
    assert card["chart_starting_capital"] == pytest.approx(100.0)


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
        {"label": "2023-11-14T22:14:20.000Z", "v": 85.72, "ts": 1_700_000_060,
         "pnl": 0.30, "market": "first-market"},
        {"label": "2023-11-14T22:15:20.000Z", "v": 86.02, "ts": 1_700_000_120,
         "pnl": 0.30, "market": "second-market"},
        {"label": "Current", "v": 86.17, "ts": 1_700_000_120},
    ]
    assert 'stroke-dasharray="2,2"' in card["chart_html"]
    assert ">Current</text>" in card["chart_html"]


def test_chart_start_point_uses_anchor_timestamp():
    # Arrange — Issue #252: the payload carries the anchor mark's timestamp.
    portfolio = _shadow_portfolio(starting_capital_ts=1_700_000_060)
    equity_series = [
        {"type": "close", "ts": 1_700_000_120, "v": 86.02, "pnl": 0.30,
         "market": "second-market"},
    ]

    # Act
    card = _render(portfolio, equity_series=equity_series)

    # Assert — left edge shows the run's real start stamp, not "Start".
    assert card["chart_series"][0] == {
        "label": "2023-11-14T22:14:20.000Z", "v": pytest.approx(STARTING),
        "ts": 1_700_000_060}


def test_chart_start_point_falls_back_to_start_without_timestamp():
    # Arrange — degenerate store: anchor is the config bankroll, ts null.
    card = _render(_shadow_portfolio(starting_capital_ts=None), equity_series=[])

    # Act / Assert
    assert card["chart_series"][0] == {"label": "Start", "v": pytest.approx(STARTING)}
    assert "NaN" not in card["chart_html"]
    assert "undefined" not in card["chart_html"]


def test_chart_series_is_flat_when_there_are_no_closes():
    card = _render(_shadow_portfolio(total_value=90.0), equity_series=[
        {"type": "mark", "ts": 1_700_000_000, "v": 90.0},
    ])

    assert card["chart_series"] == [
        {"label": "Start", "v": pytest.approx(STARTING)},
        {"label": "Current", "v": pytest.approx(STARTING)},
    ]


def test_chart_timeframe_filters_close_entries():
    # Issue #257: windowed frames clip at the window edge instead of drawing a
    # Start-to-first-close jump. The first point sits at the window edge with
    # the running value from that moment (here the pre-window close 85.70).
    equity_series = [
        {"type": "close", "ts": 1_699_900_000, "v": 85.70},
        {"type": "close", "ts": 1_700_086_400, "v": 86.10},
    ]

    card = _render(_shadow_portfolio(total_value=86.10), equity_series=equity_series,
                   timeframe="1D")

    assert [point["v"] for point in card["chart_series"]] == pytest.approx([
        85.70, 86.10, 86.10,
    ])
    assert card["chart_series"][0]["ts"] == pytest.approx(1_700_086_400 - 86400)
    assert card["chart_series"][0]["label"] != "Start"


def test_chart_x_positions_follow_real_time_gaps():    # Issue #257 RED: three closes with uneven time gaps (10x ratio). The SVG
    # line vertices must sit at time-proportional x positions — fails on the
    # old index-spaced layout where all gaps render equal.
    import re
    equity_series = [
        {"type": "close", "ts": 1_700_000_000, "v": 85.50, "pnl": 0.10,
         "market": "m1"},
        {"type": "close", "ts": 1_700_001_000, "v": 85.60, "pnl": 0.10,
         "market": "m2"},
        {"type": "close", "ts": 1_700_011_000, "v": 85.80, "pnl": 0.20,
         "market": "m3"},
    ]

    card = _render(_shadow_portfolio(total_value=85.80),
                   equity_series=equity_series)

    series = card["chart_series"]
    assert len(series) == 5  # Start + 3 closes + Current
    assert [p.get("ts") for p in series[1:4]] == [1_700_000_000, 1_700_001_000, 1_700_011_000]
    segment = re.search(r'<path d="([^"]+)"[^>]*stroke="#10b981"', card["chart_html"])
    assert segment is not None
    xs = [float(m.split(",")[0]) for m in re.findall(r"[ML]\s*([\d.]+),", segment.group(1))]
    gap_small = xs[2] - xs[1]
    gap_large = xs[3] - xs[2]
    assert gap_large / gap_small == pytest.approx(10.0, rel=0.05)


def test_tooltip_shows_trade_facts_not_raw_hex():
    # Issue #259: the hovered close names the market in words, shows dollar +
    # percent, the close method, and the hold time -- never the bare hex.
    portfolio = _shadow_portfolio(total_value=86.02)
    equity_series = [
        {"type": "close", "ts": 1_700_000_060, "v": 85.72, "pnl": 0.30,
         "market": "0x6054e1e79b478e61a65cf478b9a0fee265d1f4e8201a91e189af1f1a5a5cff",
         "title": "Market A resolves up?", "cost_basis": 4.70,
         "method": "merge", "hold_seconds": 11520},
        {"type": "close", "ts": 1_700_000_120, "v": 86.02, "pnl": 0.30,
         "market": "second-market", "title": "Second Market",
         "cost_basis": 4.90, "method": "single_buy_exit", "hold_seconds": 90},
    ]

    card = _render(portfolio, equity_series=equity_series)
    tip = card["tooltip_html"]

    assert "Market A resolves up?" in tip
    assert "0x6054" not in tip
    assert "+$0.30" in tip
    assert "+6.38%" in tip
    assert "MERGED" in tip
    assert "3h 12m" in tip


def test_tooltip_falls_back_when_basis_and_hold_unmeasured():
    # Issue #259: missing cost_basis or hold renders `--`, and a missing
    # method row stays out -- never a fabricated 0% or 0 hold.
    portfolio = _shadow_portfolio(total_value=86.02)
    equity_series = [
        {"type": "close", "ts": 1_700_000_060, "v": 85.72, "pnl": 0.30,
         "market": "lonely-market"},
        {"type": "close", "ts": 1_700_000_120, "v": 86.02, "pnl": 0.30,
         "market": "second-market", "title": "Second Market",
         "cost_basis": 4.90, "method": "merge", "hold_seconds": 90},
    ]

    card = _render(portfolio, equity_series=equity_series)
    tip = card["tooltip_html"]

    assert "lonely-market" in tip  # title missing: slug fallback, not blank
    assert "P&L %:</span>" in tip and ">--</span>" in tip
    assert "Method:" not in tip
    assert "Held:</span>" in tip
    assert tip.count("--") >= 2  # unmeasured percent AND unmeasured hold

