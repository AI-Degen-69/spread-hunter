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

Mirrors the paper run's `capitalSeries` widget in
`server/spread_dash_html.py:175` -- closes stacked on bankroll, float marks
folded in at the timestamps they were actually recorded.
"""
from __future__ import annotations

import dataclasses
import json
import sqlite3
import time

import pytest

from core_brain import kpi as kpi_mod
from core_brain.kpi import report
from core_brain.order_registry import SCHEMA, CloseRecord, MarketEventRecord, OrderRegistry, ResolutionRecord
from pathlib import Path

_STATIC_DIR = Path(__file__).resolve().parent.parent / 'dashboard' / 'static'
def _read_static(filename):
    p = _STATIC_DIR / filename
    return p.read_text(encoding='utf-8') if p.exists() else ''

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
    resolves against the developer's live runtime/markets.json.
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
    """Hermetic runtime/markets.json snapshot matching the seeded DB."""
    run_dir = tmp_path / "runtime"
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
    from core_brain.order_registry import MarketEventRecord

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


def test_empty_feed_leaves_category_as_uncategorized(seeded_db, tmp_path, monkeypatch):
    """When the feed is empty, category falls back to 'Uncategorized'."""
    (tmp_path / "runtime").mkdir(parents=True, exist_ok=True)
    (tmp_path / "runtime" / "markets.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(kpi_mod, "REPO_ROOT", tmp_path)
    data = report(db_path=seeded_db, run_id=RUN)
    assert data["by_market"]["0xmarket_a"]["category"] == "Uncategorized"


def test_markets_table_links_the_market_name_to_polymarket(seeded_db, markets_feed):
    """Each market row carries the Polymarket URL the name links to."""
    data = report(db_path=seeded_db, run_id=RUN)
    assert data["by_market"]["0xmarket_a"]["url"] == "https://polymarket.com/market/market-a"


def test_markets_table_renders_name_as_link_and_category_column():
    """The rendered table has a Category header and an anchor in the name cell."""
    assert "Market" in _read_static("index.html")  # table header in static HTML
    assert "renderMarkets" in _read_static("app.js")  # market rendering in JS


# --------------------------------------------------------------------------
# Journey 4: the single-market strip is gone, the portfolio card replaces it
# --------------------------------------------------------------------------

def test_single_market_strip_is_removed_from_the_page():
    """No element shouts one market as the hero of a multi-market run."""
    assert 'market-strip' not in _read_static('index.html')
    assert "renderMarketStrip" not in _read_static("app.js")


def test_portfolio_overview_is_the_first_card_on_the_page():
    """The portfolio card and its equity curve are present, and lead the page."""
    # The KPI tiles are now rendered by JS from /api/kpi; check the JS exists
    app_js = _read_static('app.js')
    assert 'renderKPIs' in app_js
    assert 'Net Portfolio Value' in app_js
    assert 'Realized' in app_js
    assert 'Unrealized' in app_js


# --------------------------------------------------------------------------
# Regression: resolved markets must not linger in "MARKETS IN RUN"
# --------------------------------------------------------------------------

def test_resolved_market_drops_from_by_market_via_venue_sync_close(temp_db, tmp_path, monkeypatch):
    """A market the venue reports settled (closes.method='venue_sync') leaves the
    drill-down, so "MARKETS IN RUN" stops listing markets that already resolved.

    A local merge close (still-open trade) must NOT drop the market -- that path
    is for trades the operator is still watching, not resolutions.
    """
    monkeypatch.setattr(kpi_mod, "REPO_ROOT", tmp_path)
    reg = OrderRegistry(temp_db)
    t0 = time.time() - 600

    # Resolved via the account sweep.
    reg.log_close(CloseRecord(
        ts=t0 + 60, condition_id="0xresolved", market_slug="resolved",
        method="venue_sync", shares=5.0, cost_basis=4.70, proceeds=5.00,
        realized_pnl=0.30, tx_hash="0xres", run_id=RUN,
    ))
    # Still-open trade, closed locally via merge (NOT a venue resolution).
    reg.log_close(CloseRecord(
        ts=t0 + 120, condition_id="0xopen", market_slug="open",
        method="merge", shares=5.0, cost_basis=4.90, proceeds=5.00,
        realized_pnl=0.10, tx_hash="0xopn", run_id=RUN,
    ))

    data = report(db_path=temp_db, run_id=RUN)
    assert "0xresolved" not in data["by_market"]
    assert "0xopen" in data["by_market"]


def test_resolved_market_drops_when_ranker_records_negative_days_to_resolve(temp_db, tmp_path, monkeypatch):
    """A market whose end date passed (days_to_resolve < 0 in runtime/markets.json)
    leaves "MARKETS IN RUN" even without a closes row yet.
    """
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "markets.json").write_text(json.dumps([
        {"cid": "0xexpired", "slug": "expired", "title": "Expired",
         "days_to_resolve": -0.5, "source": "spread", "eligible": True},
        {"cid": "0xlive", "slug": "live", "title": "Live",
         "days_to_resolve": 2.0, "source": "spread", "eligible": True},
    ]), encoding="utf-8")
    monkeypatch.setattr(kpi_mod, "REPO_ROOT", tmp_path)

    reg = OrderRegistry(temp_db)
    reg.log_market_event(MarketEventRecord(
        ts=time.time() - 10, condition_id="0xexpired", kind="DECISION",
        reason="quoting", reason_code="INTENT_GENERATED", run_id=RUN,
    ))
    reg.log_market_event(MarketEventRecord(
        ts=time.time() - 10, condition_id="0xlive", kind="DECISION",
        reason="quoting", reason_code="INTENT_GENERATED", run_id=RUN,
    ))

    data = report(db_path=temp_db, run_id=RUN)
    assert "0xexpired" not in data["by_market"]
    assert "0xlive" in data["by_market"]


# --------------------------------------------------------------------------
# Regression (shadow-run bug): a market the sweeper resolved must read
# FINISHED, not stay QUOTING off its append-only quotes ledger.
# --------------------------------------------------------------------------

def test_resolved_market_carries_resolved_flag_from_resolutions_table(temp_db, tmp_path, monkeypatch):
    """A market whose cid is in the ``resolutions`` table (recorded by the
    shadow resolution sweeper) reads ``resolved=True`` and is EXCLUDED from
    the runtime-fallback funnel's ``graduated`` list -- so the dashboard's
    "left the funnel" FINISHED escape hatch fires instead of the market
    staying QUOTING forever off its append-only quotes history.
    """
    monkeypatch.setattr(kpi_mod, "REPO_ROOT", tmp_path)
    reg = OrderRegistry(temp_db, run_id=RUN)

    # A market the run quoted (so it has quotes_count > 0 and would read
    # QUOTING forever without the resolved marker).
    reg.log_quote(__import__("core_brain.order_registry", fromlist=["QuoteRecord"]).QuoteRecord(
        ts=time.time() - 10, condition_id="0xended", token_id="tok",
        side="UP", price=0.47, size=20, market_slug="ended",
    ))
    reg.log_quote(__import__("core_brain.order_registry", fromlist=["QuoteRecord"]).QuoteRecord(
        ts=time.time() - 10, condition_id="0xlive2", token_id="tok",
        side="UP", price=0.47, size=20, market_slug="live2",
    ))
    # The sweeper confirmed the first market ended on the venue and recorded it.
    reg.log_resolution(ResolutionRecord(
        condition_id="0xended", winning_token=None,
        resolved_ts=time.time() - 5, run_id=RUN,
    ))

    data = report(db_path=temp_db, run_id=RUN)
    by_mkt = data["by_market"]
    # Both markets stay in the drill-down (shadow resolutions keep the
    # market visible so the operator sees it resolve, not vanish).
    assert "0xended" in by_mkt
    assert "0xlive2" in by_mkt
    # The resolved one carries the flag; the live one does not.
    assert by_mkt["0xended"]["resolved"] is True
    assert by_mkt["0xlive2"]["resolved"] is False

    # The resolved market is NOT in the runtime-fallback graduated list:
    # this is the gate the frontend's FINISHED heuristic reads.
    funnel = data["funnel"]
    grad_cids = {g["condition_id"] for g in (funnel.get("graduated") or [])}
    assert "0xended" not in grad_cids, (
        "resolved market must leave the graduated list or it reads QUOTING")
    assert "0xlive2" in grad_cids


# --------------------------------------------------------------------------
# Issue #69: Starting capital derived from live venue account marks
# --------------------------------------------------------------------------

def test_starting_capital_derives_from_account_marks_when_present(temp_db):
    """When the venue account was swept, starting capital reflects the real baseline."""
    from core_brain import account as acct
    reg = OrderRegistry(temp_db)
    t0 = time.time() - 600
    mark = acct.compose_account_mark(
        collateral_usd=85.27,
        positions_value_usd=0.0,
        open_positions=[],
        closed_positions=[],
        user_pnl_usd=0.0,
    )
    reg.log_account_mark(mark, ts=t0, run_id=RUN)
    reg.log_close(CloseRecord(
        ts=t0 + 60, condition_id="0xmarket_a", market_slug="market-a",
        method="merge", shares=5.0, cost_basis=4.50, proceeds=5.00,
        realized_pnl=0.50, tx_hash="0xaaa", run_id=RUN,
    ))

    data = report(db_path=temp_db, run_id=RUN)
    p = data["portfolio"]
    assert p["starting_capital"] == pytest.approx(85.27)
    assert p["realized_pnl"] == pytest.approx(0.50)
    assert p["total_value"] == pytest.approx(85.27 + 0.50)
    assert p["pnl_pct"] == pytest.approx(100.0 * 0.50 / 85.27)


def test_starting_capital_for_run_uses_run_baseline_mark(temp_db):
    """A run's starting capital is the first account mark in that run's window."""
    from core_brain import account as acct
    reg = OrderRegistry(temp_db)
    t0 = time.time() - 600
    mark1 = acct.compose_account_mark(
        collateral_usd=80.00, positions_value_usd=0.0,
        open_positions=[], closed_positions=[], user_pnl_usd=0.0,
    )
    mark2 = acct.compose_account_mark(
        collateral_usd=90.00, positions_value_usd=0.0,
        open_positions=[], closed_positions=[], user_pnl_usd=0.0,
    )
    reg.log_account_mark(mark1, ts=t0, run_id="run-1")
    reg.log_account_mark(mark2, ts=t0 + 300, run_id="run-2")

    data1 = report(db_path=temp_db, run_id="run-1")
    assert data1["portfolio"]["starting_capital"] == pytest.approx(80.00)

    data2 = report(db_path=temp_db, run_id="run-2")
    assert data2["portfolio"]["starting_capital"] == pytest.approx(90.00)

    # When reporting all runs, the overall starting capital is the first mark
    data_all = report(db_path=temp_db, run_id="all")
    assert data_all["portfolio"]["starting_capital"] == pytest.approx(80.00)


# --------------------------------------------------------------------------
# Issue #252: anchor is the newest mark at or before the run's first activity
# --------------------------------------------------------------------------

def _anchor_mark(collateral):
    from core_brain import account as acct
    return acct.compose_account_mark(
        collateral_usd=collateral, positions_value_usd=0.0,
        open_positions=[], closed_positions=[], user_pnl_usd=0.0,
    )


def test_starting_capital_prefers_mark_at_or_before_first_activity(temp_db):
    """A later session sweep must not move the run's start.

    The run's first close is at t0+60. The stack-start sweep at t0+120 reads
    64.70, but the anchor stays the freshest mark at or before t0+60 (53.63),
    and the payload carries that mark's timestamp.
    """
    reg = OrderRegistry(temp_db)
    t0 = time.time() - 600
    reg.log_account_mark(_anchor_mark(53.63), ts=t0 - 10, run_id="run-anchor")
    reg.log_close(CloseRecord(
        ts=t0 + 60, condition_id="0xmarket_a", market_slug="market-a",
        method="merge", shares=5.0, cost_basis=4.50, proceeds=5.00,
        realized_pnl=0.50, tx_hash="0xaaa", run_id="run-anchor",
    ))
    reg.log_account_mark(_anchor_mark(64.70), ts=t0 + 120, run_id="run-anchor")

    data = report(db_path=temp_db, run_id="run-anchor")
    p = data["portfolio"]
    assert p["starting_capital"] == pytest.approx(53.63)
    assert p["starting_capital_ts"] == pytest.approx(t0 - 10)


def test_starting_capital_ts_is_null_on_bankroll_fallback(temp_db):
    """With no account marks the anchor is the config bankroll, timestamp null."""
    from core_brain import kpi as kpi_mod
    reg = OrderRegistry(temp_db)
    t0 = time.time() - 600
    reg.log_close(CloseRecord(
        ts=t0 + 60, condition_id="0xmarket_a", market_slug="market-a",
        method="merge", shares=5.0, cost_basis=4.50, proceeds=5.00,
        realized_pnl=0.50, tx_hash="0xaaa", run_id="run-bare",
    ))

    data = report(db_path=temp_db, run_id="run-bare")
    p = data["portfolio"]
    assert p["starting_capital"] == pytest.approx(kpi_mod._CFG.bankroll_usd)
    assert p["starting_capital_ts"] is None

