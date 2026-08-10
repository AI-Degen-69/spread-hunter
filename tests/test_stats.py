"""The state reader (issue #13): one module owns every read query.

The dashboard page and the report module call `strategy.stats` instead of
writing SQL. These tests pin the moved surface against a temp database -- the
same seam the ticket's acceptance criteria call out: the state reader is the
only place besides the write module where SQL is allowed, and both the
dashboard payload (`snapshot()`) and the report module's numbers
(`kpi.report()`) come out of it.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _env(monkeypatch, tmp_path):
    """Point the write module (`MAKER_DB`) and the state reader (`DB`) at the
    same temp database, so what `store` seeds is what `stats` reads."""
    db = tmp_path / "stats.db"
    monkeypatch.setenv("MAKER_DB", str(db))
    from strategy import stats
    monkeypatch.setattr(stats, "DB", db)


def test_snapshot_returns_every_db_derived_payload(monkeypatch, tmp_path):
    """One call covers the whole read surface -- the dashboard page never
    opens its own queries (issue #13 acceptance: page keeps HTTP + HTML)."""
    _env(monkeypatch, tmp_path)
    from strategy import stats, store

    store.log_fill(market_slug="m", condition_id="c", token_id="TOK-UP",
                   side="UP", price=0.44, size=10.0)
    store.record_resolution("c", "TOK-UP")
    store.log_reward_sample(ts=1000.0, market_slug="m", condition_id="c",
                            our_score=100.0, market_score=100.0, offset_c=0,
                            n_sides=2)

    snap = stats.snapshot()

    assert set(snap) == {
        "run_started", "db_heartbeat", "db_stats", "settled_positions",
        "market_event_stats", "maker_rebate", "realized",
        "go_live_readiness", "share_history", "markout_stats"}
    assert snap["run_started"] == 1000.0
    assert snap["db_heartbeat"] == 1000.0
    assert snap["db_stats"]["c"]["fills"] == 1
    assert snap["realized"]["settled"] == 1
    assert snap["settled_positions"][0]["method"] == "RESOLVE"
    assert snap["maker_rebate"]["err"] == ""
    assert snap["go_live_readiness"]["status"] == "COLLECTING"
    assert snap["share_history"] == [0.5]


def test_db_stats_aggregates_rewards_fills_and_closes(monkeypatch, tmp_path):
    """Per-market history combines all three tables, exactly as the fleet
    page's rows need them."""
    _env(monkeypatch, tmp_path)
    from strategy import stats, store

    store.log_reward_sample(ts=1000.0, market_slug="m", condition_id="c",
                            our_score=100.0, market_score=100.0, offset_c=0,
                            n_sides=2)
    store.log_reward_sample(ts=2000.0, market_slug="m", condition_id="c",
                            our_score=100.0, market_score=100.0, offset_c=0,
                            n_sides=1)
    store.log_fill(market_slug="m", condition_id="c", token_id="TOK-UP",
                   side="UP", price=0.44, size=10.0)
    store.log_close(condition_id="c", market_slug="m", method="merge",
                    gas=0.01, shares=4.0, cost_basis=4.0, proceeds=4.0,
                    realized_pnl=0.0, forgone_vs_settlement=0.0,
                    up_cost_removed=2.0, dn_cost_removed=2.0)

    out = stats.db_stats()["c"]
    assert out["samples"] == 2
    assert out["avg_share"] == pytest.approx(0.5)
    assert out["uptime"] == 1.0
    assert out["hours"] == pytest.approx(1000.0 / 3600.0)
    assert out["fills"] == 1
    assert out["closes"] == 1
    assert out["closed_shares"] == 4.0


def test_kpi_report_reads_through_the_state_reader(monkeypatch, tmp_path):
    """The report module kept its public shape and its pure math, but every
    row now comes from the state reader -- a seeded temp DB produces real
    numbers."""
    _env(monkeypatch, tmp_path)
    from strategy import kpi, store

    store.log_quote(market_slug="m", condition_id="c", token_id="TOK-UP",
                    side="UP", price=0.44, size=100.0, queue_ahead=0.0,
                    mid=0.50, edge_vs_mid=-0.06, t_remaining=3600.0)
    store.log_fill(market_slug="m", condition_id="c", token_id="TOK-UP",
                   side="UP", price=0.44, size=10.0)
    store.record_resolution("c", "TOK-UP")

    out = kpi.report()

    assert out["markets_filled"] == 1
    assert out["fills"] == 1
    assert out["quotes"] == 1
    assert out["markets_settled"] == 1
    assert out["realized_pnl"] == pytest.approx(10.0 - 4.4)
    assert out["balance_hedges"] == 0


def test_inventory_from_db_rehydrates_after_closes(monkeypatch, tmp_path):
    """The engine's startup rehydration moved into the state reader with its
    per-leg removed-cost handling intact (issue #13: no SQL outside the write
    module and the state reader)."""
    _env(monkeypatch, tmp_path)
    from strategy import stats, store

    store.log_fill(market_slug="m", condition_id="c", token_id="TOK-UP",
                   side="UP", price=0.50, size=100.0)
    store.log_fill(market_slug="m", condition_id="c", token_id="TOK-DN",
                   side="DOWN", price=0.4728, size=100.0)
    store.log_close(condition_id="c", market_slug="m", method="merge",
                    gas=0.02, shares=40.0, cost_basis=40 * 0.9728,
                    proceeds=40.0, realized_pnl=1.0,
                    forgone_vs_settlement=0.0,
                    up_cost_removed=40 * 0.50, dn_cost_removed=40 * 0.4728)

    inv = stats.inventory_from_db("c")
    assert inv.up_shares == 60.0 and inv.down_shares == 60.0
    assert inv.up_cost == pytest.approx(100 * 0.50 - 40 * 0.50)
    assert inv.down_cost == pytest.approx(100 * 0.4728 - 40 * 0.4728)
