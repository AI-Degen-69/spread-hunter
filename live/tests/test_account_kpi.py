"""The KPI report and the dashboard tile source the account from the venue.

Journeys under test:
1. As the Owner, the headline tile shows what Polymarket says the account holds,
   not `config.bankroll_usd + realized_pnl`.
2. As the Owner, an account that has never been swept shows "--", not a number.
3. As the Owner, the tile tells me how old the reading is, because a balance is
   only as true as its last sweep.
4. As the Owner, the dashboard still makes zero venue network calls.
"""
from __future__ import annotations

import sqlite3
import time

import pytest

from engine import account as acct
from engine import kpi as kpi_mod
from engine.kpi import report
from engine.order_registry import SCHEMA, OrderRegistry
from dash.live_dash import PAGE_HTML

RUN = "run-account"


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(kpi_mod, "REPO_ROOT", tmp_path)
    db_file = tmp_path / "live.db"
    con = sqlite3.connect(str(db_file))
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    return db_file


def _mark(**over):
    base = dict(collateral_usd=101.88, positions_value_usd=0.0,
                open_positions=[], closed_positions=[{"realizedPnl": 0.9},
                                                     {"realizedPnl": -0.6}],
                user_pnl_usd=0.3)
    base.update(over)
    return acct.compose_account_mark(**base)


def test_unswept_account_reports_null_not_a_number(temp_db):
    """No sweep means no measurement. A number here would be invented."""
    rep = report(db_path=str(temp_db), run_id="all")
    a = rep["portfolio"]["account"]
    assert a["measured"] is False
    assert a["account_value_usd"] is None
    assert a["pnl_usd"] is None
    assert a["pnl_pct"] is None
    assert a["unrealized_usd"] is None


def test_swept_account_reports_the_venue_figures(temp_db):
    reg = OrderRegistry(temp_db)
    reg.log_account_mark(_mark(), ts=time.time(), run_id=RUN)

    a = report(db_path=str(temp_db), run_id="all")["portfolio"]["account"]
    assert a["measured"] is True
    assert a["source"] == "venue"
    assert a["account_value_usd"] == pytest.approx(101.88)
    assert a["pnl_usd"] == pytest.approx(0.30)
    assert round(a["pnl_pct"], 2) == 0.30
    assert a["collateral_usd"] == pytest.approx(101.88)
    assert a["positions_value_usd"] == pytest.approx(0.0)


def test_account_value_does_not_come_from_the_config_bankroll(temp_db):
    """The whole point: $101.88 from the venue, not $100.30 from a constant."""
    reg = OrderRegistry(temp_db)
    reg.log_account_mark(_mark(), ts=time.time(), run_id=RUN)

    p = report(db_path=str(temp_db), run_id="all")["portfolio"]
    assert p["account"]["account_value_usd"] != pytest.approx(p["total_value"])
    assert p["account"]["account_value_usd"] == pytest.approx(101.88)


def test_no_gap_is_reported_against_the_config_bankroll(temp_db):
    """A "gap" measured against `bankroll_usd` would restate the fabrication in
    a footnote. The registry records no deposits, so there is nothing real to
    reconcile the venue's balance against."""
    reg = OrderRegistry(temp_db)
    reg.log_account_mark(_mark(), ts=time.time(), run_id=RUN)

    a = report(db_path=str(temp_db), run_id="all")["portfolio"]["account"]
    assert "book_value_usd" not in a
    assert "venue_vs_book_usd" not in a


def test_newest_mark_wins(temp_db):
    reg = OrderRegistry(temp_db)
    reg.log_account_mark(_mark(collateral_usd=90.0), ts=1000.0, run_id=RUN)
    reg.log_account_mark(_mark(collateral_usd=101.88), ts=2000.0, run_id=RUN)

    a = report(db_path=str(temp_db), run_id="all")["portfolio"]["account"]
    assert a["account_value_usd"] == pytest.approx(101.88)
    assert a["ts"] == 2000.0


def test_account_is_not_sliced_by_run(temp_db):
    """A balance belongs to the wallet. Filtering it per run would report an
    empty account for any run that happened not to sweep."""
    reg = OrderRegistry(temp_db)
    reg.log_account_mark(_mark(), ts=1000.0, run_id="some-other-run")

    a = report(db_path=str(temp_db), run_id="a-run-with-no-sweep")["portfolio"]["account"]
    assert a["measured"] is True
    assert a["account_value_usd"] == pytest.approx(101.88)


def test_partial_mark_keeps_nulls_through_the_report(temp_db):
    reg = OrderRegistry(temp_db)
    reg.log_account_mark(
        acct.compose_account_mark(None, None, None, None, None),
        ts=1000.0, run_id=RUN)

    a = report(db_path=str(temp_db), run_id="all")["portfolio"]["account"]
    assert a["measured"] is True          # a sweep ran
    assert a["account_value_usd"] is None  # but it obtained nothing
    assert a["pnl_usd"] is None


def test_open_positions_populate_unrealized_and_committed(temp_db):
    reg = OrderRegistry(temp_db)
    reg.log_account_mark(
        _mark(positions_value_usd=61.5,
              open_positions=[{"cashPnl": 8.79, "initialValue": 52.71}]),
        ts=1000.0, run_id=RUN)

    a = report(db_path=str(temp_db), run_id="all")["portfolio"]["account"]
    assert a["unrealized_usd"] == pytest.approx(8.79)
    assert a["committed_usd"] == pytest.approx(52.71)
    assert a["open_positions_count"] == 1


def test_account_series_is_the_venue_value_curve(temp_db):
    reg = OrderRegistry(temp_db)
    reg.log_account_mark(_mark(collateral_usd=100.0), ts=1000.0, run_id=RUN)
    reg.log_account_mark(_mark(collateral_usd=101.88), ts=2000.0, run_id=RUN)

    series = report(db_path=str(temp_db), run_id="all")["account_series"]
    assert [pt["ts"] for pt in series] == [1000.0, 2000.0]
    assert series[-1]["v"] == pytest.approx(101.88)


def test_failed_sweep_is_dropped_from_the_curve_not_plotted_as_zero(temp_db):
    reg = OrderRegistry(temp_db)
    reg.log_account_mark(_mark(), ts=1000.0, run_id=RUN)
    reg.log_account_mark(acct.compose_account_mark(None, None, None, None, None),
                         ts=2000.0, run_id=RUN)

    series = report(db_path=str(temp_db), run_id="all")["account_series"]
    assert len(series) == 1
    assert series[0]["v"] == pytest.approx(101.88)


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------

def test_tile_is_labelled_account_value_from_the_venue():
    assert "Account Value" in PAGE_HTML
    assert "Book Value" not in PAGE_HTML
    # The interim disclaimer described a number the page no longer shows.
    assert "config bankroll + realised" not in PAGE_HTML


def test_page_renders_the_account_object_not_the_bankroll_total():
    assert "portfolio.account" in PAGE_HTML or "p.account" in PAGE_HTML
    assert "a.account_value_usd" in PAGE_HTML


def test_page_tells_the_owner_how_to_take_the_first_sweep():
    assert "account-sweep" in PAGE_HTML


def test_page_still_makes_no_venue_calls():
    """The dashboard reads SQLite. The sweep is what talks to the venue."""
    for host in ("data-api.polymarket.com", "clob.polymarket.com",
                 "user-pnl-api.polymarket.com"):
        assert host not in PAGE_HTML


def test_page_shows_how_stale_the_reading_is():
    """A balance is only as true as its last sweep."""
    assert "swept ${ageStr} ago" in PAGE_HTML
    # The old footnote compared the venue against the config bankroll.
    assert "registry book" not in PAGE_HTML
