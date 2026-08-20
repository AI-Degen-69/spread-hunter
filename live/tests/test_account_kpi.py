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


def test_realized_pnl_comes_from_the_venues_closed_positions(temp_db):
    """The registry only knows closes this bot performed. A position closed by a
    merge on Polymarket itself leaves the registry at $0.00 while the venue
    reports the real result -- which is how -3.10, -1.60, +5.00 disappeared."""
    reg = OrderRegistry(temp_db)
    reg.log_account_mark(_mark(), ts=time.time(), run_id=RUN)

    p = report(db_path=str(temp_db), run_id="all")["portfolio"]
    assert p["realized_pnl"] == pytest.approx(0.0)          # registry knows nothing
    assert p["account"]["pnl_closed_usd"] == pytest.approx(0.30)
    assert p["account"]["closed_positions_count"] == 2


def test_page_sources_realized_from_the_venue():
    assert "a.pnl_closed_usd" in PAGE_HTML
    assert "closed position(s)" in PAGE_HTML


def test_a_failed_sweep_does_not_blank_a_good_reading(temp_db):
    """A sweep whose collateral read failed records NULL. Letting that NULL win
    would blank the headline while a complete reading sits in the table."""
    reg = OrderRegistry(temp_db)
    reg.log_account_mark(_mark(), ts=1000.0, run_id=RUN)
    reg.log_account_mark(_mark(collateral_usd=None), ts=2000.0, run_id=RUN)

    a = report(db_path=str(temp_db), run_id="all")["portfolio"]["account"]
    assert a["account_value_usd"] == pytest.approx(101.88)
    # And it reports the age of the reading it actually used, not of the
    # failed sweep -- so the page can call it stale.
    assert a["ts"] == 1000.0


def test_every_field_comes_from_one_mark_not_assembled_across_marks(temp_db):
    """Mixing a collateral from one sweep with a P&L from another would produce
    a total that was never true at any single moment."""
    reg = OrderRegistry(temp_db)
    reg.log_account_mark(_mark(collateral_usd=50.0, user_pnl_usd=1.0), ts=1000.0, run_id=RUN)
    reg.log_account_mark(_mark(collateral_usd=None, user_pnl_usd=9.0), ts=2000.0, run_id=RUN)

    a = report(db_path=str(temp_db), run_id="all")["portfolio"]["account"]
    assert a["account_value_usd"] == pytest.approx(50.0)
    assert a["pnl_usd"] == pytest.approx(1.0)   # not 9.0 from the failed sweep


def test_all_sweeps_failed_still_reports_measured_with_nulls(temp_db):
    reg = OrderRegistry(temp_db)
    reg.log_account_mark(acct.compose_account_mark(None, None, None, None, None),
                         ts=1000.0, run_id=RUN)

    a = report(db_path=str(temp_db), run_id="all")["portfolio"]["account"]
    assert a["measured"] is True
    assert a["account_value_usd"] is None


def test_page_never_prints_an_unmeasured_leg_as_zero():
    """A failed collateral read must render "--", not "$0.00 cash"."""
    assert "a.collateral_usd ?? 0" not in PAGE_HTML
    assert "usdOrDash" in PAGE_HTML


def test_page_uses_only_defined_css_variables():
    """var(--text-dim) resolves to nothing and silently keeps the inherited
    colour, so the note never looks muted."""
    import re

    defined = set(re.findall(r"(--[a-z0-9-]+):", PAGE_HTML))
    used = set(re.findall(r"var\((--[a-z0-9-]+)\)", PAGE_HTML))
    assert used - defined == set()


def test_active_run_falls_back_to_all_when_most_recent_run_has_no_fills(temp_db):
    """When the most-recent run has zero fills but an earlier run does, default
    the active run to "all" so the dashboard doesn't render a misleading zeros
    grid over real, recent fills.

    Regression for: live_exec process restart orphans fills under a defunct
    run_id; the dashboard's default most-recent run had no fills, so all 29
    fills became invisible.
    """
    from engine.order_registry import FillRecord, OrderRecord
    reg = OrderRegistry(temp_db)
    # Earlier run: orders + fills. Lower max_ts so it's NOT the most-recent.
    earlier = "run-orphan"
    reg.log_account_mark(_mark(collateral_usd=101.0), ts=1000.0, run_id=earlier)
    reg.create_order(OrderRecord(
        id="ord-orphan-1", condition_id="cond-orphan", token_id="tok-orphan",
        side="BUY", price=0.21, original_size=10.0, status="open",
        posted_ts=1000, last_polled_ts=1000, pair_id="pair-orphan",
    ))
    reg.record_fill(FillRecord(
        trade_id="t-orphan-1", order_uuid="ord-orphan-1", size=8.0, price=0.21,
        venue_ts=1100.0, recorded_ts=1100.5, run_id=earlier,
    ))
    # Current run: orders only, no fills. Higher max_ts so it IS most-recent.
    current = "run-current"
    reg.log_account_mark(_mark(collateral_usd=101.5), ts=5000.0, run_id=current)
    reg.log_quote(_make_quote(run_id=current, ts=5000.0))

    # No run_id argument: must fall back to "all".
    rep = report(db_path=str(temp_db))
    assert rep["active_run_id"] == "all"
    # And the fills must be visible (n=1, not n=0).
    by_mkt = rep["by_market"]
    fills_visible = sum(m.get("fills_count", 0) for m in by_mkt.values())
    assert fills_visible == 1


def test_active_run_sticks_to_most_recent_when_it_has_fills(temp_db):
    """When the most-recent run DOES have fills, do NOT fall back to "all" --
    the operator wants to see the current run in isolation, not pooled."""
    from engine.order_registry import FillRecord, OrderRecord
    reg = OrderRegistry(temp_db)
    reg.log_account_mark(_mark(collateral_usd=101.0), ts=1000.0, run_id="run-old")
    reg.create_order(OrderRecord(
        id="ord-old", condition_id="cond-old", token_id="tok-old",
        side="BUY", price=0.21, original_size=10.0, status="open",
        posted_ts=1000, last_polled_ts=1000, pair_id="pair-old",
    ))
    reg.record_fill(FillRecord(
        trade_id="t-old", order_uuid="ord-old", size=8.0, price=0.21,
        venue_ts=1100.0, recorded_ts=1100.5, run_id="run-old",
    ))
    reg.log_account_mark(_mark(collateral_usd=102.0), ts=9000.0, run_id="run-new")
    reg.create_order(OrderRecord(
        id="ord-new", condition_id="cond-new", token_id="tok-new",
        side="BUY", price=0.79, original_size=10.0, status="open",
        posted_ts=9000, last_polled_ts=9000, pair_id="pair-new",
    ))
    reg.record_fill(FillRecord(
        trade_id="t-new", order_uuid="ord-new", size=4.0, price=0.79,
        venue_ts=9100.0, recorded_ts=9100.5, run_id="run-new",
    ))

    rep = report(db_path=str(temp_db))
    assert rep["active_run_id"] == "run-new"
    by_mkt = rep["by_market"]
    fills_visible = sum(m.get("fills_count", 0) for m in by_mkt.values())
    assert fills_visible == 1  # only the run-new fill, not pooled.


def _make_quote(*, run_id, ts, **over):
    from engine.order_registry import QuoteRecord
    base = dict(
        ts=ts, market_slug="slug", condition_id="cond-1",
        token_id="tok-1", side="BUY", price=0.5, size=5.0,
        order_id="0xvenue", local_id="local-1", run_id=run_id,
    )
    base.update(over)
    return QuoteRecord(**base)


def test_order_only_markets_appear_in_by_market_for_drilldown(temp_db):
    """Regression: an order that was placed but never filled (no quote telemetry,
    no fill, no close, no market_events) must still appear in by_market so the
    operator can drill down on it. Before the fix, order-only markets were excluded
    from all_cids and thus never reached by_mkt."""
    from engine.order_registry import OrderRecord
    reg = OrderRegistry(temp_db)
    # Market with order only, no other telemetry
    reg.create_order(OrderRecord(
        id="ord-order-only", condition_id="cond-order-only", token_id="tok-order-only",
        side="BUY", price=0.50, original_size=10.0, status="open",
        posted_ts=1000, last_polled_ts=1000, pair_id="pair-order-only",
    ))

    rep = report(db_path=str(temp_db), run_id="all")
    by_mkt = rep["by_market"]
    assert "cond-order-only" in by_mkt, "Order-only market must appear in by_market for drill-down"
