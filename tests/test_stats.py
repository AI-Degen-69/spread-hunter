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
    """Point the write module (`HUNTER_DB`) and the state reader (`DB`) at the
    same temp database, so what `store` seeds is what `stats` reads."""
    db = tmp_path / "stats.db"
    monkeypatch.setenv("HUNTER_DB", str(db))
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
        "go_live_readiness", "share_history", "markout_stats",
        "pairs_ev"}
    assert snap["run_started"] == 1000.0
    assert snap["db_heartbeat"] == 1000.0
    assert snap["db_stats"]["c"]["fills"] == 1
    assert snap["realized"]["settled"] == 1
    assert snap["settled_positions"][0]["method"] == "RESOLVE"
    assert snap["maker_rebate"]["err"] == ""
    assert snap["go_live_readiness"]["status"] == "COLLECTING"
    assert snap["share_history"] == [0.5]


def test_markout_reads_guard_an_empty_mid_column_schema(monkeypatch, tmp_path):
    """A `markouts` table with no mid_h* column (a pre-migration DB the
    dashboard's read-only connection cannot migrate) must read as an honest
    empty/zero fallback -- not malformed SQL built from an empty column list
    and not a swallowed OperationalError string (coderabbit)."""
    import sqlite3

    _env(monkeypatch, tmp_path)
    db = tmp_path / "stats.db"
    c = sqlite3.connect(str(db))
    try:
        c.execute("CREATE TABLE markouts (id INTEGER PRIMARY KEY, ts REAL, "
                  "condition_id TEXT, side TEXT, ref_mid REAL, size REAL)")
        c.execute("INSERT INTO markouts (ts, condition_id, side, ref_mid, "
                  "size) VALUES (1.0, 'c', 'UP', 0.5, 10.0)")
        c.commit()
    finally:
        c.close()

    from strategy import stats

    assert stats.pooled_markout_neff() == {
        "n_eff": 0.0, "n_rows": 0, "mean_per_share": None}
    m = stats.markout_stats()
    assert m["n"] == 0 and m["matured_n"] == 0
    assert "error" not in m


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


def test_inventory_from_db_rehydrates_after_naked_exit(monkeypatch, tmp_path):
    """U35: a naked_exit close removed ONE leg only, so the rebuild must not
    decrement the untouched leg -- the old pair-close assumption (one of
    each) would silently drop shares that were never sold."""
    _env(monkeypatch, tmp_path)
    from strategy import stats, store

    store.log_fill(market_slug="m", condition_id="c", token_id="TOK-UP",
                   side="UP", price=0.44, size=100.0)
    store.log_fill(market_slug="m", condition_id="c", token_id="TOK-DN",
                   side="DOWN", price=0.46, size=40.0)
    # The pairs rule sold the DOWN residue at 0.48: 60 held + 40 sold.
    store.log_close(condition_id="c", market_slug="m", method="naked_exit",
                    shares=40.0, up_price=None, dn_price=0.48,
                    cost_basis=40 * 0.46, proceeds=40 * 0.48, fee=0.68,
                    realized_pnl=40 * 0.48 - 40 * 0.46 - 0.68,
                    forgone_vs_settlement=None,
                    up_cost_removed=0.0, dn_cost_removed=40 * 0.46)

    inv = stats.inventory_from_db("c")
    assert inv.up_shares == 100.0, "the UP leg was never touched"
    assert inv.down_shares == 0.0
    assert inv.up_cost == pytest.approx(44.0)
    assert inv.down_cost == 0.0
    assert inv.last_fill_ts is not None, "the fill clock survives a rebuild"


def test_realized_is_side_aware_after_naked_exit(monkeypatch, tmp_path):
    """U35: when the EXITED side is the loser, its shares must not be
    credited at resolution -- the old pair-close assumption subtracted the
    closed count from the winning token, which would under-credit the still-
    held winner (and vice versa when the exited side is the winner)."""
    _env(monkeypatch, tmp_path)
    from strategy import stats, store

    store.log_fill(market_slug="m", condition_id="c", token_id="TOK-UP",
                   side="UP", price=0.50, size=100.0)
    store.log_fill(market_slug="m", condition_id="c", token_id="TOK-DN",
                   side="DOWN", price=0.50, size=100.0)
    # Exited 40 DOWN (the eventual loser) at 0.50, booked at cost.
    store.log_close(condition_id="c", market_slug="m", method="naked_exit",
                    shares=40.0, up_price=None, dn_price=0.50,
                    cost_basis=20.0, proceeds=20.0, fee=40 * 0.017,
                    realized_pnl=-40 * 0.017,
                    forgone_vs_settlement=None,
                    up_cost_removed=0.0, dn_cost_removed=20.0)
    store.record_resolution("c", "TOK-UP")

    r = stats.realized()
    # The 100 UP still held win $1 each; the 60 DOWN still held lose. Cost
    # was 100 (fills) - 20 (exited basis) = 80; the exit booked -0.68.
    assert r["realized"] == pytest.approx(100 - 80 - 40 * 0.017)
    assert r["settled"] == 1


def test_pairs_ev_counts_rule_decisions(monkeypatch, tmp_path):
    """The EV KPI is completion rate x merge capture - exit rate x half-
    spread, over every rule-triggering one-sided fill (completed, exited, or
    rode out the window). None before the first decision, not a confident 0."""
    _env(monkeypatch, tmp_path)
    from strategy import stats, store

    ev0 = stats.pairs_ev()
    assert ev0["one_sided"] == 0 and ev0["ev_cents"] is None

    for kind, side in (("PAIR_COMPLETE", "DOWN"), ("PAIR_COMPLETE", "DOWN"),
                       ("NAKED_EXIT", "UP"), ("PAIR_WINDOW_EXPIRED", "UP")):
        store.log_event(ts=1000.0, market_slug="m", condition_id="c",
                        kind=kind, reason=kind, side=side, size=100.0)

    ev = stats.pairs_ev()
    assert ev["one_sided"] == 4
    assert ev["completions"] == 2 and ev["exits"] == 1 and ev["expired"] == 1
    assert ev["completion_rate"] == pytest.approx(0.5)
    assert ev["exit_rate"] == pytest.approx(0.25)
    # 0.5 x 3.68 - 0.25 x 3.67 = 0.9225 -> rounds to 0.923 (constants
    # re-measured in Sessions 44/45: 16.3c was a 302.5-share pre-spread-era
    # sample; the exit cost was corrected from a mis-added 3.89 to 3.67).
    assert ev["ev_cents"] == pytest.approx(0.923)
    # No completed-pair closes exist yet -> the distribution is None, not a
    # confident 0 (the same empty-run rule as the EV itself).
    assert ev["dist"] is None and ev["outliers"] is None
    assert stats.snapshot()["pairs_ev"]["one_sided"] == 4, \
        "the dashboard payload must carry the EV read"


def test_pairs_ev_distribution_and_outliers(monkeypatch, tmp_path):
    """The completed-pair capture distribution + IQR outlier count ride the
    same payload the tile reads (Sessions 44-47): median/p25-p75 visible next
    to the EV, outliers flagged -- None, not empty, before the first
    rule-era merge."""
    import sqlite3

    _env(monkeypatch, tmp_path)
    from strategy import stats, store

    # A rule decision but no rule-era merge yet -> dist stays None.
    store.log_event(ts=1000.0, market_slug="m", condition_id="c",
                    kind="PAIR_COMPLETE", reason="PAIR_COMPLETE", size=100.0)
    assert stats.pairs_ev()["dist"] is None
    assert stats.pairs_ev()["outliers"] is None

    # Three rule-era merges: rates 3.0 / 5.0 / 16.5 c/sh -> 16.5 is an IQR
    # outlier against the 3-5c set (fences 0.0..8.0), exactly as the report
    # computes it.
    db = tmp_path / "stats.db"
    c = sqlite3.connect(str(db))
    try:
        for ts, pnl in ((1100.0, 3.0), (1101.0, 5.0), (1102.0, 16.5)):
            c.execute(
                "INSERT INTO closes (ts, condition_id, market_slug, method, "
                "gas, shares, up_price, dn_price, cost_basis, proceeds, fee, "
                "realized_pnl, forgone_vs_settlement, up_cost_removed, "
                "dn_cost_removed) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ts, "c", "m", "merge", None, 100.0, None, None, 0.0, 0.0,
                 0.0, pnl, None, 0.0, 0.0))
        c.commit()
    finally:
        c.close()

    ev = stats.pairs_ev()
    d = ev["dist"]
    assert d["n"] == 3
    assert d["mean"] == pytest.approx(8.1667, abs=1e-3)
    assert d["median"] == pytest.approx(5.0)
    assert d["p25"] == pytest.approx(3.0) and d["p75"] == pytest.approx(5.0)
    assert d["min"] == pytest.approx(3.0) and d["max"] == pytest.approx(16.5)
    assert ev["outliers"]["count"] == 1
    assert ev["outliers"]["fences"] == pytest.approx([0.0, 8.0])
    # The dashboard payload carries the same fields (the tile reads snapshot).
    assert stats.snapshot()["pairs_ev"]["dist"]["median"] == pytest.approx(5.0)
