"""What a hard-stop exit actually cost, measured off the exit record. #730

Three states the aggregate must never conflate: a fill that matched the floor
(`0.00`, a real measurement), a fill that landed below it (a cost), and an
exit nobody observed filling (unmeasured -- never zero). The queue gate made
this same distinction; this file holds the exit path to it.
"""
import json
import re
import sqlite3
from pathlib import Path

import pytest

from core_brain.order_registry import CloseRecord, OrderRegistry, init_db
from tests.test_hard_stop_reset import (
    COND,
    NOW,
    PAIR,
    TOK_DN,
    TOK_UP,
    ProvingVenue,
    _balances,
    _seed_pair,
)


# The `closes` table as it shipped before #730: no submitted_floor, no
# fill_source. A store of this shape is what an operator's data/orders.db
# looked like the day this landed.
LEGACY_CLOSES_DDL = """
CREATE TABLE closes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    condition_id TEXT,
    market_slug TEXT,
    method TEXT DEFAULT 'sell',
    gas REAL,
    shares REAL,
    up_price REAL,
    dn_price REAL,
    cost_basis REAL,
    proceeds REAL,
    fee REAL,
    realized_pnl REAL,
    forgone_vs_settlement REAL,
    up_cost_removed REAL,
    dn_cost_removed REAL,
    tx_hash TEXT,
    run_id TEXT NOT NULL,
    reason TEXT,
    trigger_bid REAL,
    fresh_bid REAL
);
"""


def _legacy_store(path):
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_CLOSES_DDL)
    conn.execute(
        "INSERT INTO closes (ts, condition_id, method, dn_price, shares, "
        "run_id, reason) VALUES (1.0, '0xold', 'single_buy_exit', 0.15, 5.0, "
        "'r-old', 'lifecycle_hard_stop')"
    )
    conn.commit()
    conn.close()


def test_registry_legacy_store_gains_the_stop_columns_and_old_rows_read_none(tmp_path):
    db = tmp_path / "old.db"
    _legacy_store(db)

    reg = OrderRegistry(db_path=db)

    closes = reg.get_all_closes()
    assert len(closes) == 1
    assert closes[0]["submitted_floor"] is None
    assert closes[0]["fill_source"] is None


def test_registry_repeat_init_is_safe(tmp_path):
    db = tmp_path / "repeat.db"

    init_db(db)
    init_db(db)
    reg = OrderRegistry(db_path=db)

    assert reg.get_all_closes() == []


def test_registry_close_carries_the_floor_and_the_fill_source(tmp_path):
    db = tmp_path / "close.db"
    init_db(db)
    reg = OrderRegistry(db_path=db)

    reg.log_close(CloseRecord(
        ts=1.0, condition_id="0xabc", method="single_buy_exit",
        shares=5.0, dn_price=0.13, cost_basis=3.0, proceeds=0.65,
        realized_pnl=-2.35, reason="lifecycle_hard_stop",
        trigger_bid=0.15, fresh_bid=0.13,
        submitted_floor=0.13, fill_source="simulated",
    ))

    row = reg.get_all_closes()[0]
    assert row["submitted_floor"] == pytest.approx(0.13)
    assert row["fill_source"] == "simulated"


def test_registry_refusals_round_trip_and_survive_a_lifecycle_reset(tmp_path):
    db = tmp_path / "refusals.db"
    init_db(db)
    reg = OrderRegistry(db_path=db)

    reg.log_hard_stop_refusal(
        pair_id="pair-1", condition_id="0xabc", trigger_bid=0.15,
        outcome="refused", detail="No bid on tok: nothing to sell into.")
    reg.log_hard_stop_refusal(
        pair_id="pair-1", condition_id="0xabc", trigger_bid=0.15,
        outcome="refused", detail="No bid on tok: nothing to sell into.")

    rows = reg.get_hard_stop_refusals()
    assert [r["outcome"] for r in rows] == ["refused", "refused"]
    assert rows[0]["pair_id"] == "pair-1"
    assert rows[0]["trigger_bid"] == pytest.approx(0.15)
    assert rows[0]["run_id"]

    generation = reg.begin_lifecycle_reset("0xabc", "test", now_ms=1000)
    assert reg.finish_lifecycle_reset("0xabc", generation, reset_ts_ms=2000)

    # Reset clears lifecycle rows only; refusal evidence is append-only.
    assert len(reg.get_hard_stop_refusals()) == 2


def _args(token_id, amount, price):
    from py_clob_client_v2.clob_types import MarketOrderArgsV2
    return MarketOrderArgsV2(token_id=token_id, amount=amount, side="SELL",
                             price=price)


def _shadow_client(tmp_path, book):
    from core_brain.shadow_exec import ShadowExecutionClient, ensure_shadow_tables
    db = tmp_path / "shadow.db"
    init_db(db)
    reg = OrderRegistry(db_path=db)
    ensure_shadow_tables(db)
    return ShadowExecutionClient(reg, db, book_fn=lambda h, t: book)


def test_shadow_walk_that_takes_shares_labels_the_fill_simulated(tmp_path):
    client = _shadow_client(tmp_path, {"bids": {0.15: 20.0}, "asks": {}})

    resp = client.create_and_post_market_order(
        _args("tok-dn", 5.0, 0.13), reason="lifecycle_hard_stop")

    assert resp["fill_source"] == "simulated"
    assert resp["price"] == pytest.approx(0.15)
    assert resp["size"] == pytest.approx(5.0)


def test_sell_into_bids_reports_whether_the_walk_took_shares(tmp_path):
    client = _shadow_client(tmp_path, {"bids": {0.10: 20.0}, "asks": {}})

    filled, avg, took = client._sell_into_bids("tok-dn", 5.0, floor=0.13,
                                               fresh=True)

    assert filled == pytest.approx(5.0)
    assert avg == pytest.approx(0.13)
    assert took is False


def test_shadow_sell_into_a_book_below_the_floor_is_assumed(tmp_path):
    client = _shadow_client(tmp_path, {"bids": {0.10: 20.0}, "asks": {}})

    resp = client.create_and_post_market_order(
        _args("tok-dn", 5.0, 0.13), reason="hard_stop")

    assert resp["fill_source"] == "assumed"
    assert resp["size"] == pytest.approx(5.0)
    assert resp["price"] == pytest.approx(0.13)


def test_exit_fill_source_truth_table():
    from core_brain.single_buy_saver import _exit_fill_source

    assert _exit_fill_source(
        {"fill_source": "simulated", "price": 0.13, "size": 5}) == "simulated"
    assert _exit_fill_source({"price": 0.13, "size": 5}) == "reported"
    assert _exit_fill_source({"price": "0.13", "size": "5"}) == "reported"
    assert _exit_fill_source({"price": 0.13}) == "assumed"
    assert _exit_fill_source({"price": "0.13", "size": 0}) == "assumed"
    assert _exit_fill_source({"price": "bad", "size": 5}) == "assumed"
    assert _exit_fill_source(None) == "assumed"


def _route(registry, venue):
    from core_brain import single_buy_saver as lp
    from core_brain.market_resolution import MarketEndState
    from tests.test_hard_stop_reset import _cfg

    results = lp.manage_single_leg_positions(
        venue, registry, _cfg(), live=True, now=NOW / 1000.0 + 10.0,
        venue_positions={TOK_UP: 10.0, TOK_DN: 0.0},
        market_state_fn=lambda cid: MarketEndState(
            condition_id=COND, closed=False, end_ts=NOW / 1000.0 + 600.0,
            end_date_iso=None, end_date_passed=False, accepting_orders=True,
            resolved=False, unreachable=False))
    assert len(results) == 1
    return results[0]


class _RefusingVenue(ProvingVenue):
    def create_and_post_market_order(self, *args, **kwargs):
        from core_brain import single_buy_saver as lp
        raise lp.PairExitRefused("venue refused the stop sale")


class _ErroringVenue(ProvingVenue):
    def create_and_post_market_order(self, *args, **kwargs):
        raise RuntimeError("venue connection lost")


def test_a_live_exit_without_reported_numbers_books_the_floor_assumed(
        tmp_path, monkeypatch):
    """A live response with no price/size is a fallback: unmeasured, never 0."""
    db = tmp_path / "live.db"
    reg = OrderRegistry(db_path=db)
    _balances(monkeypatch, [])
    _seed_pair(reg)

    routed = _route(reg, ProvingVenue())

    assert routed["action"] == "exited"
    close = reg.get_all_closes()[0]
    assert close["submitted_floor"] == pytest.approx(0.12)  # 0.14 bid - 0.02
    assert close["fill_source"] == "assumed"


def test_a_refused_hard_stop_is_recorded_and_keeps_the_error_result(
        tmp_path, monkeypatch):
    db = tmp_path / "live.db"
    reg = OrderRegistry(db_path=db)
    _balances(monkeypatch, [])
    _seed_pair(reg)

    routed = _route(reg, _RefusingVenue())

    assert routed["action"] == "error"
    rows = reg.get_hard_stop_refusals()
    assert [r["outcome"] for r in rows] == ["refused"]
    assert rows[0]["pair_id"] == PAIR
    assert rows[0]["condition_id"] == COND
    assert reg.get_all_closes() == []


def test_an_errored_hard_stop_is_recorded_as_error(tmp_path, monkeypatch):
    db = tmp_path / "live.db"
    reg = OrderRegistry(db_path=db)
    _balances(monkeypatch, [])
    _seed_pair(reg)

    routed = _route(reg, _ErroringVenue())

    assert routed["action"] == "error"
    rows = reg.get_hard_stop_refusals()
    assert [r["outcome"] for r in rows] == ["error"]
    assert "venue connection lost" in rows[0]["detail"]


def _close(**kw):
    row = {
        "reason": "lifecycle_hard_stop", "method": "single_buy_exit",
        "shares": 5.0, "up_price": None, "dn_price": 0.13,
        "submitted_floor": 0.13, "fill_source": "reported",
        "trigger_bid": 0.15, "run_id": "run-1",
    }
    row.update(kw)
    return row


def _summary(*closes, refusals=None):
    from core_brain.statistical_analytics import stop_slippage_summary
    return stop_slippage_summary(list(closes), refusals)


def test_summary_a_fill_at_the_floor_is_zero_not_unknown():
    out = _summary(_close())

    assert out["measured_count"] == 1
    assert out["mean_vs_floor"] is not None
    assert out["mean_vs_floor"] == 0.0
    assert out["worst_vs_floor"] == 0.0
    assert out["below_floor_count"] == 0


def test_summary_a_fill_below_the_floor_sets_worst_and_count():
    out = _summary(_close(submitted_floor=0.15))

    assert out["below_floor_count"] == 1
    assert out["mean_vs_floor"] == pytest.approx(0.02)
    assert out["worst_vs_floor"] == pytest.approx(0.02)


def test_summary_measures_the_decision_bid_separately():
    out = _summary(_close(trigger_bid=0.16, submitted_floor=0.13))
    assert out["decision_measured_count"] == 1
    assert out["mean_vs_decision"] == pytest.approx(0.03)

    out = _summary(_close(trigger_bid=None))
    assert out["decision_measured_count"] == 0
    assert out["mean_vs_decision"] is None
    assert out["worst_vs_decision"] is None


def test_summary_never_zeroes_an_unmeasured_exit():
    out = _summary(
        _close(fill_source="assumed"),
        _close(shares=0.0),
        _close(up_price=0.13, dn_price=0.13),  # ambiguous sold side
        _close(submitted_floor=None),
        _close(fill_source=None),
    )

    assert out["measured_count"] == 0
    assert out["unmeasured_count"] == 5
    assert out["unmeasured_reasons"]["historical"] == 2
    assert out["unmeasured_reasons"]["assumed_fill"] == 3
    assert out["mean_vs_floor"] is None
    assert out["worst_vs_floor"] is None


def test_summary_keeps_a_better_than_floor_fill_negative():
    out = _summary(_close(dn_price=0.15))

    assert out["worst_vs_floor"] == pytest.approx(-0.02)
    assert out["below_floor_count"] == 0


def test_summary_treats_each_partial_close_as_one_observation():
    out = _summary(_close(submitted_floor=0.14), _close(submitted_floor=0.16))

    assert out["measured_count"] == 2
    assert out["mean_vs_floor"] == pytest.approx(0.02)
    assert out["worst_vs_floor"] == pytest.approx(0.03)


def test_summary_snaps_float_noise_at_the_floor_to_zero():
    out = _summary(_close(dn_price=0.13 + 1e-12))

    assert out["mean_vs_floor"] == 0.0


def test_summary_empty_population_is_none_not_zero():
    out = _summary(_close(reason="aged_out_rescue"))

    assert out["stop_exit_count"] == 0
    assert out["measured_count"] == 0
    for key in ("mean_vs_floor", "worst_vs_floor",
                "mean_vs_decision", "worst_vs_decision"):
        assert out[key] is None


def test_summary_counts_refused_positions_not_polls():
    out = _summary(_close(), refusals=[
        {"pair_id": "p1", "outcome": "refused"},
        {"pair_id": "p1", "outcome": "refused"},
        {"pair_id": "p2", "outcome": "error"},
    ])

    assert out["refused_position_count"] == 2
    assert out["refused_attempt_count"] == 3


def test_summary_serialises_strictly_and_counts_simulated():
    import json

    out = _summary(_close(fill_source="simulated", submitted_floor=0.15))

    assert out["simulated_count"] == 1
    assert out["units"] == "usd_per_share"
    json.dumps(out, allow_nan=False)


def _seed_run(db, run_id, *, close_kw=None, refusals=()):
    reg = OrderRegistry(db_path=db, run_id=run_id)
    if close_kw is not None:
        row = {"ts": 1.0, "condition_id": "0xshared",
               "method": "single_buy_exit", "shares": 5.0, "dn_price": 0.13,
               "reason": "lifecycle_hard_stop", "submitted_floor": 0.13,
               "fill_source": "reported", "trigger_bid": 0.15}
        row.update(close_kw)
        reg.log_close(CloseRecord(**row))
    for kw in refusals:
        reg.log_hard_stop_refusal(condition_id="0xshared", **kw)


def test_report_ships_the_stop_slippage_section_and_bumps_the_version(tmp_path):
    from core_brain import kpi

    db = tmp_path / "kpi.db"
    init_db(db)
    _seed_run(db, "run-a", close_kw={"submitted_floor": 0.15})

    payload = kpi.report(db, run_id="run-a")

    section = payload["statistical_analytics"]["stop_slippage"]
    assert section["measured_count"] == 1
    assert section["mean_vs_floor"] == pytest.approx(0.02)
    assert payload["payload_version"] == 260


def test_report_separates_two_runs_that_share_a_condition(tmp_path):
    from core_brain import kpi

    db = tmp_path / "kpi.db"
    init_db(db)
    _seed_run(db, "run-a", close_kw={"submitted_floor": 0.15})
    _seed_run(db, "run-b", close_kw={"submitted_floor": 0.16})

    run_a = kpi.report(db, run_id="run-a")["statistical_analytics"]["stop_slippage"]
    run_b = kpi.report(db, run_id="run-b")["statistical_analytics"]["stop_slippage"]

    assert run_a["measured_count"] == 1
    assert run_a["mean_vs_floor"] == pytest.approx(0.02)
    assert run_b["measured_count"] == 1
    assert run_b["mean_vs_floor"] == pytest.approx(0.03)


def test_report_counts_refusals_for_the_chosen_run_only(tmp_path):
    from core_brain import kpi

    db = tmp_path / "kpi.db"
    init_db(db)
    _seed_run(db, "run-a", refusals=[
        {"pair_id": "p1", "trigger_bid": 0.15, "outcome": "refused"},
        {"pair_id": "p1", "trigger_bid": 0.15, "outcome": "refused"},
    ])
    _seed_run(db, "run-b", refusals=[
        {"pair_id": "p2", "trigger_bid": 0.15, "outcome": "error"},
    ])

    run_a = kpi.report(db, run_id="run-a")["statistical_analytics"]["stop_slippage"]
    run_b = kpi.report(db, run_id="run-b")["statistical_analytics"]["stop_slippage"]

    assert (run_a["refused_position_count"], run_a["refused_attempt_count"]) == (1, 2)
    assert (run_b["refused_position_count"], run_b["refused_attempt_count"]) == (1, 1)


def test_cli_reads_a_legacy_store_without_migrating_it(tmp_path, capsys):
    import json

    from core_brain import kpi

    db = tmp_path / "legacy.db"
    _legacy_store(db)
    with sqlite3.connect(db) as conn:
        before = [r[1] for r in conn.execute("PRAGMA table_info(closes)")]

    rc = kpi.main(["--db", str(db), "--json"])

    assert rc == 0
    section = json.loads(capsys.readouterr().out)
    assert section["stop_exit_count"] == 1
    assert section["unmeasured_reasons"]["historical"] == 1
    assert section["mean_vs_floor"] is None
    with sqlite3.connect(db) as conn:
        after = [r[1] for r in conn.execute("PRAGMA table_info(closes)")]
    assert after == before


def test_partial_stop_exits_stay_two_measurements_after_the_reset(tmp_path):
    db = tmp_path / "partials.db"
    init_db(db)
    reg = OrderRegistry(db_path=db)
    for ts, shares, floor in ((1.0, 4.0, 0.14), (2.0, 6.0, 0.16)):
        reg.log_close(CloseRecord(
            ts=ts, condition_id="0xpartial", method="single_buy_exit",
            shares=shares, dn_price=0.13, reason="lifecycle_hard_stop",
            submitted_floor=floor, fill_source="reported"))

    generation = reg.begin_lifecycle_reset("0xpartial", "stop exit", now_ms=1000)
    assert reg.finish_lifecycle_reset("0xpartial", generation, reset_ts_ms=2000)

    out = _summary(*reg.get_all_closes())
    assert out["measured_count"] == 2
    assert out["mean_vs_floor"] == pytest.approx(0.02)  # (0.01 + 0.03) / 2
    assert out["worst_vs_floor"] == pytest.approx(0.03)