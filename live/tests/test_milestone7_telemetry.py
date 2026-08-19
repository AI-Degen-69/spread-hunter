"""live/tests/test_milestone7_telemetry.py - Tests for Milestone 7 Part B telemetry, KPIs, markouts, and safety guards.
"""

import os
import sqlite3
import tempfile
import time
from pathlib import Path
import pytest

from engine.order_registry import (
    OrderRegistry,
    OrderRecord,
    FillRecord,
    QuoteRecord,
    MarketEventRecord,
    MarkoutRecord,
    CloseRecord,
    FloatMarkRecord,
    HedgeCensusRecord,
    VenueErrorRecord,
    DivergenceEventRecord,
    get_run_id,
    set_run_id,
    _reconcile_pass,
)
from engine.markout import sample_pending_markouts, MARKOUT_HORIZONS
from engine.kpi import report as generate_kpi_report
from engine.live_exec import decide


class DummyClobClient:
    def __init__(self, open_orders=None, trades=None):
        self._open_orders = open_orders or []
        self._trades = trades or []
        self.creds = ("key", "secret", "passphrase")

    def get_open_orders(self):
        return self._open_orders

    def get_trades(self, params=None):
        return self._trades


def test_schema_migrations_and_run_id(tmp_path):
    db_file = tmp_path / "test_reg.db"
    set_run_id("test-run-12345")

    reg = OrderRegistry(db_file)
    with reg._conn() as conn:
        cols_orders = {row["name"] for row in conn.execute("PRAGMA table_info(orders)").fetchall()}
        assert "run_id" in cols_orders

        cols_fills = {row["name"] for row in conn.execute("PRAGMA table_info(fills)").fetchall()}
        assert "recorded_ts" in cols_fills
        assert "run_id" in cols_fills

        cols_markouts = {row["name"] for row in conn.execute("PRAGMA table_info(markouts)").fetchall()}
        assert "mid_h3" in cols_markouts
        assert "run_id" in cols_markouts

        cols_closes = {row["name"] for row in conn.execute("PRAGMA table_info(closes)").fetchall()}
        assert "tx_hash" in cols_closes
        assert "run_id" in cols_closes


def test_reconcile_records_venue_match_time_and_recorded_ts(tmp_path):
    db_file = tmp_path / "test_rec.db"
    reg = OrderRegistry(db_file)

    now_ms = 1787108000000
    reg.create_order(OrderRecord(
        id="order-loc-1",
        order_id="venue-ord-1",
        condition_id="0xcond1",
        token_id="tok1",
        side="BUY",
        price=0.62,
        original_size=5.0,
        status="open",
        posted_ts=now_ms - 3000,
        last_polled_ts=now_ms - 3000,
    ))

    # Venue trade with match_time = 1787105572 (epoch sec)
    client = DummyClobClient(
        open_orders=[],
        trades=[{
            "id": "trade-venue-1",
            "order_id": "venue-ord-1",
            "size": "5.0",
            "price": "0.62",
            "match_time": "1787105572",
        }]
    )

    with reg.reconcile_lock(now_ms):
        summary = _reconcile_pass(client, reg, maker_address="0xfunder", now_ms=now_ms, orphan_window_ms=30000)

    assert summary.fills_recorded == 1
    fills = reg.get_all_fills()
    assert len(fills) == 1
    f = fills[0]
    # Venue TS must be 1787105572000 (ms)
    assert f["venue_ts"] == 1787105572000
    assert f["recorded_ts"] == now_ms
    # Reconcile lag is positive
    assert f["recorded_ts"] - f["venue_ts"] > 0


def test_telemetry_logging_records_all_tables(tmp_path):
    db_file = tmp_path / "test_telem.db"
    reg = OrderRegistry(db_file)
    t_now = time.time()

    # 1. Quote
    qid = reg.log_quote(QuoteRecord(
        ts=t_now,
        condition_id="0xcond1",
        token_id="tok1",
        side="UP",
        price=0.55,
        size=10.0,
        market_slug="slug-1",
        queue_ahead=12.0,
        mid=0.57,
        edge_vs_mid=0.02,
        order_id="0xord1",
        local_id="loc-1",
        latency_ms=45.2,
    ))
    assert qid > 0
    quotes = reg.get_all_quotes()
    assert len(quotes) == 1
    assert quotes[0]["latency_ms"] == 45.2

    # 2. Market Event
    reg.log_market_event(MarketEventRecord(
        ts=t_now,
        condition_id="0xcond1",
        kind="BLOCKED",
        reason="outside price band",
        reason_code="PRICE_BAND",
        market_slug="slug-1",
    ))
    evts = reg.get_all_market_events()
    assert len(evts) == 1
    assert evts[0]["reason_code"] == "PRICE_BAND"

    # 3. Markout
    mid = reg.log_markout(MarkoutRecord(
        ts=t_now - 400.0,
        condition_id="0xcond1",
        side="UP",
        fill_price=0.55,
        size=10.0,
        market_slug="slug-1",
        ref_mid=0.55,
    ))
    assert mid > 0
    pending = reg.get_pending_markouts(t_now, MARKOUT_HORIZONS)
    assert len(pending) == 1
    assert pending[0]["_due"] == 0  # mid_h0 (300s) is due

    # 4. Close
    reg.log_close(CloseRecord(
        ts=t_now,
        condition_id="0xcond1",
        method="merge",
        shares=10.0,
        cost_basis=9.60,
        proceeds=10.00,
        realized_pnl=0.40,
        tx_hash="0x4f802f221594c873764294972f5d14b9152c1fe54f65f084155100beb0e8cb2e",
    ))
    closes = reg.get_all_closes()
    assert len(closes) == 1
    assert closes[0]["realized_pnl"] == 0.40

    # 5. Float mark
    reg.log_float_mark(unrealized_usd=0.15, committed_open_usd=9.60, naked_usd=0.0)
    fmarks = reg.get_all_float_marks()
    assert len(fmarks) == 1

    # 6. Hedge census
    reg.log_hedge_census(HedgeCensusRecord(
        condition_id="0xcond1",
        up_ask=0.56,
        down_ask=0.42,
        pair_cost_at_touch=0.96,
        fillable_sub_one=1.0,
        observed_ts=t_now,
    ))
    census = reg.get_all_hedge_census()
    assert len(census) == 1
    assert census[0]["fillable_sub_one"] == 1.0

    # 7. Venue error
    reg.log_venue_error(VenueErrorRecord(
        ts=t_now,
        condition_id="0xcond1",
        side="UP",
        price=0.55,
        size=10.0,
        error_code="INSUFFICIENT_BALANCE",
        raw_error_msg="not enough collateral",
    ))
    verrs = reg.get_all_venue_errors()
    assert len(verrs) == 1

    # 8. Divergence event
    reg.log_divergence_event(DivergenceEventRecord(
        ts=t_now,
        condition_id="0xcond1",
        pair_id="pair-1",
        registry_diff=0.0,
        venue_diff=0.0,
        chain_diff=5.0,
        divergence_msg="chain holding 5.0 unmerged shares",
    ))
    divs = reg.get_all_divergence_events()
    assert len(divs) == 1


def test_markout_sampler_out_of_band_safe(tmp_path):
    db_file = tmp_path / "test_markout.db"
    reg = OrderRegistry(db_file)
    t_now = time.time()

    # Add a markout past horizon 0 (300s)
    reg.log_markout(MarkoutRecord(
        ts=t_now - 350.0,
        condition_id="0xcond_nonexistent",
        side="UP",
        fill_price=0.50,
        size=5.0,
    ))

    # Must run out-of-band and never crash or raise on network failure
    updated = sample_pending_markouts(reg, clob_host="http://invalid-host-will-fail.local", now_sec=t_now)
    assert updated == 0  # failed safely without crashing


def test_kpi_report_parity_and_live_fields(tmp_path):
    db_file = tmp_path / "test_kpi.db"
    reg = OrderRegistry(db_file)
    t_now = time.time()

    # Insert quote & fill & close
    reg.create_order(OrderRecord(
        id="loc-q1",
        order_id="ven-q1",
        condition_id="0xcond1",
        token_id="tok1",
        side="BUY",
        price=0.62,
        original_size=5.0,
        status="filled",
        posted_ts=int((t_now - 100) * 1000),
        last_polled_ts=int(t_now * 1000),
    ))
    reg.log_quote(QuoteRecord(
        ts=t_now - 100,
        condition_id="0xcond1",
        token_id="tok1",
        side="UP",
        price=0.62,
        size=5.0,
        queue_ahead=10.0,
        mid=0.63,
        edge_vs_mid=0.01,
        filled=5.0,
        latency_ms=12.5,
        local_id="loc-q1",
    ))
    reg.record_fill(FillRecord(
        trade_id="tr-1",
        order_uuid="loc-q1",
        size=5.0,
        price=0.62,
        venue_ts=int((t_now - 50) * 1000),
        recorded_ts=int(t_now * 1000),
    ))
    reg.log_close(CloseRecord(
        ts=t_now,
        condition_id="0xcond1",
        method="merge",
        shares=5.0,
        cost_basis=4.70,
        proceeds=5.00,
        realized_pnl=0.30,
    ))

    kpis = generate_kpi_report(db_path=db_file)

    # Core simulation parity fields
    assert kpis["markets_quoted"] == 1
    assert kpis["markets_filled"] == 1
    assert kpis["fills"] == 1
    assert kpis["filled_shares"] == 5.0
    assert kpis["realized_pnl"] == 0.30
    assert kpis["fill_rate"] == 1.0
    assert kpis["rebate_est"] is None  # Explicit None for rebate

    # Live-specific operational metrics
    assert kpis["order_latency_ms"]["median"] == 12.5
    assert kpis["reconcile_lag_ms"]["median"] == 50000.0
    assert "venue_rejects" in kpis
    assert "three_way_divergences" in kpis


def test_decide_is_structurally_read_only(tmp_path):
    """Amendment 4: decide must be incapable of sending orders or creating pending orders."""
    db_file = tmp_path / "test_decide.db"
    reg = OrderRegistry(db_file)

    # Count orders before decide
    orders_before = reg.get_active_orders()

    # Run decide against empty/mocked environment
    try:
        decide(target="nonexistent", db_path=db_file)
    except SystemExit:
        pass
    except Exception:
        pass

    orders_after = reg.get_active_orders()
    assert len(orders_after) == len(orders_before) == 0


def test_markout_samples_a_buy_side_row_by_token(tmp_path, monkeypatch):
    """A reconciled fill records side='BUY'; the sampler must still find its mid.

    `mids_cache` is keyed UP/DOWN, but the markouts row's `side` column is copied
    from the order, whose schema constrains it to BUY/SELL. Looking the mid up by
    `side` therefore returned None for every reconciled row: no horizon was ever
    sampled, `done` was never set, and the pending backlog grew without bound.
    The row now carries the token it filled on, and the leg is resolved from that.
    """
    import types
    import engine.markets as markets_mod

    reg = OrderRegistry(tmp_path / "test_markout_leg.db")
    t_now = time.time()

    reg.log_markout(MarkoutRecord(
        ts=t_now - 350.0,
        condition_id="0xcond_leg",
        side="BUY",
        token_id="tok_down",
        fill_price=0.32,
        size=5.0,
    ))

    monkeypatch.setattr(
        markets_mod,
        "fetch_pinned_market",
        lambda cid, require_rewards=False: types.SimpleNamespace(
            up_token="tok_up", down_token="tok_down"
        ),
    )
    monkeypatch.setattr(
        markets_mod,
        "full_book",
        lambda host, token: (
            {"best_bid": 0.60, "best_ask": 0.64}
            if token == "tok_up"
            else {"best_bid": 0.34, "best_ask": 0.38}
        ),
    )

    assert sample_pending_markouts(reg, now_sec=t_now) == 1

    with reg._conn() as conn:
        row = dict(conn.execute("SELECT * FROM markouts").fetchone())
    assert row["token_id"] == "tok_down"
    assert row["mid_h0"] == pytest.approx(0.36)


def test_markout_leg_resolution_rejects_a_foreign_token(tmp_path):
    """A token belonging to neither leg leaves the markout unsampled, not mis-sided."""
    from engine.markout import _resolve_leg

    mids = {"UP": 0.62, "DOWN": 0.36, "_up_token": "tok_up", "_down_token": "tok_down"}
    assert _resolve_leg("tok_up", mids, "BUY") == "UP"
    assert _resolve_leg("tok_down", mids, "BUY") == "DOWN"
    assert _resolve_leg("tok_someone_elses", mids, "BUY") is None
    # Rows written before token_id existed fall back to the side string.
    assert _resolve_leg(None, mids, "UP") == "UP"
    assert _resolve_leg(None, mids, "BUY") is None


# ---------------------------------------------------------------------------
# Regression: latency_ms in fleet path + run_id lock file
# ---------------------------------------------------------------------------

def test_latency_ms_persisted_by_log_quote(tmp_path):
    """Fleet log_quote with latency_ms stores the value, not NULL."""
    db_file = tmp_path / "test_latency.db"
    from engine.order_registry import QuoteRecord
    reg = OrderRegistry(db_file)
    reg.log_quote(QuoteRecord(
        ts=1000.0, market_slug="test-slug", condition_id="test-cid",
        token_id="test-tok", side="BUY", price=0.55, size=5.0,
        order_id="0xvenue", local_id="loc-1", run_id="run-latency",
        latency_ms=47.3,
    ))
    with reg._conn() as conn:
        row = dict(conn.execute("SELECT * FROM quotes WHERE local_id = 'loc-1'").fetchone())
    assert row["latency_ms"] == pytest.approx(47.3)


def test_run_id_lock_file_shared_across_processes(tmp_path, monkeypatch):
    """First import writes .current_run_id; second import reuses it."""
    from engine import order_registry as reg_mod
    lock_dir = tmp_path / "run"
    monkeypatch.setattr(reg_mod, "LIVE_ROOT", tmp_path)
    monkeypatch.delenv("SH_RUN_ID", raising=False)
    monkeypatch.setattr(reg_mod, "_CURRENT_RUN_ID", None)
    id1 = reg_mod._resolve_run_id()
    assert (lock_dir / ".current_run_id").exists()
    stored = (lock_dir / ".current_run_id").read_text().strip()
    assert id1 == stored
    monkeypatch.setattr(reg_mod, "_CURRENT_RUN_ID", None)
    id2 = reg_mod._resolve_run_id()
    assert id2 == id1


def test_sh_run_id_env_overrides_lock_file(tmp_path, monkeypatch):
    """SH_RUN_ID wins when set, no lock file read needed."""
    from engine import order_registry as reg_mod
    monkeypatch.setenv("SH_RUN_ID", "env-override-123")
    monkeypatch.setattr(reg_mod, "_CURRENT_RUN_ID", None)
    rid = reg_mod._resolve_run_id()
    assert rid == "env-override-123"
