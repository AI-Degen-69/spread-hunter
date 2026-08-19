"""Tests for the run-level portfolio overview on the live dashboard.

The dashboard's top card used to describe ONE market: a strip naming a single
question, and a hero card describing that pair's hedge state. A run touches many
markets, so the first thing on the page must aggregate all of them -- total
value, P&L in dollars and percent, open unrealized -- the way a broker's
portfolio page does.

Journeys under test:
1. As the Owner, I read Total Value, P&L $, P&L %, and Unrealized for the WHOLE
   run, aggregated over every market, not the first one.
2. As the Owner, I see an equity curve for the whole run, stacked on the
   starting bankroll, stepping on each realised close.
3. As the Owner, I see each market's category and click its name through to
   Polymarket from the markets table.
4. As the Owner, I no longer see a single-market strip shouting at the top of
   the page.

Mirrors the simulation's `capitalSeries` widget in
`server/spread_dash_html.py:175` -- closes stacked on bankroll, float marks
folded in at the timestamps they were actually recorded.
"""
from __future__ import annotations

import dataclasses
import json
import sqlite3
import time

import pytest

from engine import kpi as kpi_mod
from engine.kpi import report
from engine.order_registry import SCHEMA, CloseRecord, OrderRegistry
from dash.live_dash import PAGE_HTML

RUN = "run-portfolio"


@pytest.fixture
def temp_db(tmp_path):
    """A temporary registry database on the real schema."""
    db_file = tmp_path / "live.db"
    con = sqlite3.connect(str(db_file))
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    return db_file


@pytest.fixture
def seeded_db(temp_db, tmp_path, monkeypatch):
    """Two markets, two closes, two float marks -- a run, not a single pair.

    REPO_ROOT is redirected even when no feed is written, so metadata never
    resolves against the developer's live run/markets.json.
    """
    monkeypatch.setattr(kpi_mod, "REPO_ROOT", tmp_path)
    reg = OrderRegistry(temp_db)
    t0 = time.time() - 600

    reg.log_close(CloseRecord(
        ts=t0 + 60, condition_id="0xmarket_a", market_slug="market-a",
        method="merge", shares=5.0, cost_basis=4.70, proceeds=5.00,
        realized_pnl=0.30, tx_hash="0xaaa", run_id=RUN,
    ))
    reg.log_close(CloseRecord(
        ts=t0 + 180, condition_id="0xmarket_b", market_slug="market-b",
        method="merge", shares=5.0, cost_basis=4.90, proceeds=5.00,
        realized_pnl=0.10, tx_hash="0xbbb", run_id=RUN,
    ))
    reg.log_float_mark(unrealized_usd=1.25, committed_open_usd=9.60,
                       naked_usd=0.0, ts=t0 + 120, run_id=RUN)
    reg.log_float_mark(unrealized_usd=2.50, committed_open_usd=9.60,
                       naked_usd=0.0, ts=t0 + 240, run_id=RUN)
    return temp_db


@pytest.fixture
def markets_feed(tmp_path, monkeypatch):
    """Point the ranker feed at a temp repo root so category resolution is hermetic."""
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "markets.json").write_text(json.dumps([
        {
            "cid": "0xmarket_a", "slug": "market-a", "title": "Market A resolves up?",
            "category": "Crypto", "series_title": "Bitcoin", "market_group": "Price",
            "volume_24h": 1000.0, "days_to_resolve": 1.5, "min_size": 5.0,
            "source": "spread", "spread": 0.03, "eligible": True, "reject_reason": "",
        },
        {
            "cid": "0xmarket_b", "slug": "market-b", "title": "Market B resolves up?",
            # Category blank on the live feed today -- must fall back, not blank out.
            "category": "", "series_title": "Dota 2", "market_group": "Match Winner",
            "volume_24h": 2000.0, "days_to_resolve": 0.5, "min_size": 5.0,
            "source": "spread", "spread": 0.02, "eligible": True, "reject_reason": "",
        },
    ]), encoding="utf-8")
    monkeypatch.setattr(kpi_mod, "REPO_ROOT", tmp_path)
    return tmp_path


# --------------------------------------------------------------------------
# Journey 1: portfolio aggregates the whole run
# --------------------------------------------------------------------------

def test_portfolio_aggregates_every_market_not_just_one(seeded_db):
    """Total value sums realised P&L across ALL markets plus open float."""
    data = report(db_path=seeded_db, run_id=RUN)
    p = data["portfolio"]

    start = kpi_mod._CFG.bankroll_usd
    assert p["starting_capital"] == start
    assert p["realized_pnl"] == pytest.approx(0.40)      # 0.30 + 0.10, both markets
    assert p["unrealized_usd"] == pytest.approx(2.50)    # latest float mark
    assert p["total_value"] == pytest.approx(start + 0.40 + 2.50)
    assert p["markets_count"] == 2


def test_portfolio_pnl_pct_is_percent_of_starting_capital(seeded_db):
    """P&L % is total P&L over starting capital, expressed as a percent."""
    data = report(db_path=seeded_db, run_id=RUN)
    p = data["portfolio"]

    start = kpi_mod._CFG.bankroll_usd
    assert p["total_pnl"] == pytest.approx(0.40 + 2.50)
    assert p["pnl_pct"] == pytest.approx(100.0 * (0.40 + 2.50) / start)


def test_portfolio_pnl_pct_is_null_when_starting_capital_is_zero(seeded_db, monkeypatch):
    """A zero bankroll yields NULL, never a divide-by-zero or a fake 0.0%."""
    # MakerConfig is a frozen dataclass; swap the whole config, not one field.
    monkeypatch.setattr(kpi_mod, "_CFG", dataclasses.replace(kpi_mod._CFG, bankroll_usd=0.0))
    data = report(db_path=seeded_db, run_id=RUN)
    assert data["portfolio"]["pnl_pct"] is None


def test_portfolio_zero_state_on_empty_database(temp_db):
    """An empty registry reports the bankroll untouched, not an error or a blank."""
    data = report(db_path=temp_db)
    p = data["portfolio"]

    assert p["total_value"] == pytest.approx(kpi_mod._CFG.bankroll_usd)
    assert p["realized_pnl"] == 0.0
    # NULL, not 0.0: no sweep has ever marked the open book.
    assert p["unrealized_usd"] is None
    assert p["unrealized_measured"] is False
    assert p["open_committed_usd"] is None
    assert p["markets_count"] == 0


def test_unmarked_open_float_is_null_not_zero(temp_db):
    """A run with closes but no float mark must not claim the float is $0.00.

    Nothing in the engine calls log_float_mark today, so this is the shape of
    every real run: reporting a measured zero next to a resting naked leg is the
    instrumentation lie spread_capture and adverse_selection already refuse.
    """
    reg = OrderRegistry(temp_db)
    reg.log_close(CloseRecord(
        ts=time.time() - 60, condition_id="0xmarket_a", market_slug="market-a",
        method="merge", shares=5.0, cost_basis=4.70, proceeds=5.00,
        realized_pnl=0.30, run_id=RUN,
    ))
    p = report(db_path=temp_db, run_id=RUN)["portfolio"]

    assert p["unrealized_usd"] is None
    assert p["unrealized_measured"] is False
    # The total still stands on what WAS measured: the realised close.
    assert p["total_value"] == pytest.approx(kpi_mod._CFG.bankroll_usd + 0.30)


def test_float_mark_older_than_the_last_close_is_not_counted(temp_db):
    """A mark taken before a close describes money that has since been realised.

    Counting both bills the same dollars twice on the headline tile.
    """
    reg = OrderRegistry(temp_db)
    t0 = time.time() - 600
    reg.log_float_mark(unrealized_usd=2.50, committed_open_usd=9.60,
                       naked_usd=0.0, ts=t0, run_id=RUN)
    reg.log_close(CloseRecord(
        ts=t0 + 120, condition_id="0xmarket_a", market_slug="market-a",
        method="merge", shares=5.0, cost_basis=4.70, proceeds=5.00,
        realized_pnl=0.30, run_id=RUN,
    ))
    p = report(db_path=temp_db, run_id=RUN)["portfolio"]

    assert p["unrealized_measured"] is False
    assert p["unrealized_usd"] is None
    # Not 100.30 + 2.50: the 2.50 became the 0.30 that is already banked.
    assert p["total_value"] == pytest.approx(kpi_mod._CFG.bankroll_usd + 0.30)


def test_markets_only_refused_are_not_counted_as_traded(seeded_db):
    """A market blocked at the gate was never traded and must not inflate the count."""
    from engine.order_registry import MarketEventRecord

    reg = OrderRegistry(seeded_db)
    reg.log_market_event(MarketEventRecord(
        ts=time.time() - 300, condition_id="0xmarket_blocked",
        kind="BLOCKED", reason="outside price band",
        reason_code="PRICE_BAND", run_id=RUN,
    ))
    data = report(db_path=seeded_db, run_id=RUN)

    # The refused market is still visible in the funnel and the drill-down...
    assert "0xmarket_blocked" in data["by_market"]
    # ...but the portfolio counts the two markets that actually traded.
    assert data["portfolio"]["markets_count"] == 2


# --------------------------------------------------------------------------
# Journey 2: run-level equity curve
# --------------------------------------------------------------------------

def test_equity_series_steps_on_each_close_and_mark(seeded_db):
    """The curve carries a point per close and per float mark, in time order."""
    data = report(db_path=seeded_db, run_id=RUN)
    series = data["equity_series"]

    assert len(series) == 4                      # 2 closes + 2 marks
    ts = [pt["ts"] for pt in series]
    assert ts == sorted(ts)
    assert {pt["type"] for pt in series} == {"close", "mark"}

    start = kpi_mod._CFG.bankroll_usd
    # First close: bankroll + 0.30, no float recorded yet.
    assert series[0]["type"] == "close"
    assert series[0]["v"] == pytest.approx(start + 0.30)
    # The mark at t+120 floats on top of the first close.
    assert series[1]["type"] == "mark"
    assert series[1]["v"] == pytest.approx(start + 0.30 + 1.25)
    # The second close realises that float. Not start + 0.40 + 1.25: the 1.25
    # described positions this close just settled, and realized_pnl holds them.
    assert series[2]["type"] == "close"
    assert series[2]["v"] == pytest.approx(start + 0.40)
    # Last point: both closes banked, latest mark floated on top.
    assert series[-1]["v"] == pytest.approx(start + 0.40 + 2.50)


def test_equity_series_is_empty_when_nothing_has_happened(temp_db):
    """No closes and no marks means no curve -- not a fabricated flat line."""
    data = report(db_path=temp_db)
    assert data["equity_series"] == []


# --------------------------------------------------------------------------
# Journey 3: markets table identity
# --------------------------------------------------------------------------

def test_market_meta_carries_category_from_the_ranker_feed(seeded_db, markets_feed):
    """by_market rows expose the category the ranker recorded."""
    data = report(db_path=seeded_db, run_id=RUN)
    assert data["by_market"]["0xmarket_a"]["category"] == "Crypto"


def test_blank_category_falls_back_to_series_title(seeded_db, markets_feed):
    """A blank category on the feed resolves to the series, never to an empty cell."""
    data = report(db_path=seeded_db, run_id=RUN)
    assert data["by_market"]["0xmarket_b"]["category"] == "Dota 2"


def test_unknown_market_category_is_labelled_not_blank(seeded_db, tmp_path, monkeypatch):
    """A market absent from the feed is 'Uncategorized', not None."""
    (tmp_path / "run").mkdir(parents=True, exist_ok=True)
    (tmp_path / "run" / "markets.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(kpi_mod, "REPO_ROOT", tmp_path)
    data = report(db_path=seeded_db, run_id=RUN)
    assert data["by_market"]["0xmarket_a"]["category"] == "Uncategorized"


def test_markets_table_links_the_market_name_to_polymarket(seeded_db, markets_feed):
    """Each market row carries the Polymarket URL the name links to."""
    data = report(db_path=seeded_db, run_id=RUN)
    assert data["by_market"]["0xmarket_a"]["url"] == "https://polymarket.com/market/market-a"


def test_markets_table_renders_name_as_link_and_category_column():
    """The rendered table has a Category header and an anchor in the name cell."""
    assert "<th>Category</th>" in PAGE_HTML
    assert "mkt-name-link" in PAGE_HTML


# --------------------------------------------------------------------------
# Journey 4: the single-market strip is gone, the portfolio card replaces it
# --------------------------------------------------------------------------

def test_single_market_strip_is_removed_from_the_page():
    """No element shouts one market as the hero of a multi-market run."""
    assert 'id="market-strip"' not in PAGE_HTML
    assert "renderMarketStrip" not in PAGE_HTML


def test_portfolio_overview_is_the_first_card_on_the_page():
    """The portfolio card and its equity curve are present, and lead the page."""
    for element_id in (
        'id="portfolio-total-value"',
        'id="portfolio-pnl"',
        'id="portfolio-unrealized"',
        'id="portfolio-equity-curve"',
    ):
        assert element_id in PAGE_HTML

    assert PAGE_HTML.index('id="portfolio-total-value"') < PAGE_HTML.index('id="sec-run-kpis"')
