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


def test_pairs_ev_exit_card_ladder(monkeypatch, tmp_path):
    """The pending exit-card re-read (Sessions 49-51): every naked-exit close
    gets an honest counterfactual state -- recorded (15m mid landed) / pending
    (15m not elapsed) / no_markout (no markout row) / no_fill (no triggering
    fill) -- plus the re-read threshold, so "exits since the last re-read" is
    visible at a glance on the dashboard tile. Same windowed joins as
    scripts/pairs_ev_report.py's exit_counterfactual (10s close->fill, 30s
    fill->markout), so the tile and the report cannot disagree."""
    import sqlite3

    _env(monkeypatch, tmp_path)
    from strategy import stats, store

    db = tmp_path / "stats.db"
    # Schema first via a real write; the seed event is deliberately NOT a
    # pairs-rule kind so it cannot pollute the KPI denominator.
    store.log_event(ts=999.0, market_slug="m0", condition_id="c0",
                    kind="QUOTING", reason="r", size=1.0)

    def seed_fill_markout(ts, cid, market, side, price, size, mid_h3):
        c = sqlite3.connect(str(db))
        try:
            c.execute("INSERT INTO fills (ts, condition_id, market_slug, "
                      "token_id, side, price, size, reason) "
                      "VALUES (?,?,?,?,?,?,?,?)",
                      (ts, cid, market, f"tok-{side}", side, price, size,
                       "tape"))
            c.execute("INSERT INTO markouts (ts, condition_id, market_slug, "
                      "side, fill_price, size, ref_mid, ref_mid_source, "
                      "mid_h0, mid_h1, mid_h2, mid_h3) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                      (ts, cid, market, side, price, size, price + 0.02,
                       "venue_clean", None, None, None, mid_h3))
            c.commit()
        finally:
            c.close()

    def seed_exit_close(ts, market, cid):
        c = sqlite3.connect(str(db))
        try:
            c.execute("INSERT INTO closes (ts, condition_id, market_slug, "
                      "method, gas, shares, up_price, dn_price, cost_basis, "
                      "proceeds, fee, realized_pnl, forgone_vs_settlement, "
                      "up_cost_removed, dn_cost_removed) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (ts, cid, market, "naked_exit", None, 100.0, 0.37, None,
                       0.0, 0.0, 0.0, -3.0, None, 0.0, 0.0))
            c.commit()
        finally:
            c.close()

    # exit 1 -- recorded: mid_h3 landed at 0.33 vs exit 0.37.
    seed_fill_markout(1100.0, "c1", "m1", "UP", 0.40, 100.0, mid_h3=0.33)
    seed_exit_close(1100.05, "m1", "c1")
    # exit 2 -- pending: mid_h3 still NULL (15m not elapsed).
    seed_fill_markout(1200.0, "c2", "m2", "UP", 0.20, 100.0, mid_h3=None)
    seed_exit_close(1200.05, "m2", "c2")
    # exit 3 -- no_markout: fill present, no markout row for it.
    c = sqlite3.connect(str(db))
    try:
        c.execute("INSERT INTO fills (ts, condition_id, market_slug, "
                  "token_id, side, price, size, reason) "
                  "VALUES (1300.0,'c3','m3','t','UP',0.10,100.0,'tape')")
        c.commit()
    finally:
        c.close()
    seed_exit_close(1300.05, "m3", "c3")
    # exit 4 -- no_fill: no triggering fill within 10s of the close.
    seed_exit_close(1400.05, "m4", "c4")

    ev = stats.pairs_ev()
    card = ev["exit_card"]
    assert card["n"] == 4
    assert card["recorded"] == 1 and card["pending"] == 1
    assert card["no_markout"] == 1 and card["no_fill"] == 1
    assert card["no_column"] == 0
    assert card["re_read_at"] == 10
    # The dashboard payload carries the same ladder (the tile reads snapshot).
    assert stats.snapshot()["pairs_ev"]["exit_card"]["recorded"] == 1
    assert stats.snapshot()["pairs_ev"]["exit_card"]["re_read_at"] == 10


def test_pairs_ev_fill_horizon_capture(monkeypatch, tmp_path):
    """The 15m fill-capture ladder (Session 55): every rule-era fill's
    mid_h3 is classified so the exit-window counterfactual accumulates on the
    current pace instead of waiting on naked exits (which almost never fire).
    Classification is TIME-based: a NULL mid_h3 whose 15m window has elapsed
    is no_markout (never recorded), not pending -- pending means the window
    is genuinely still open."""
    import time
    import sqlite3

    _env(monkeypatch, tmp_path)
    from strategy import stats, store

    # The rule era starts at the first PAIR_COMPLETE; the fill read slices on
    # it, so a seed decision is required or the read stays at n=0.
    store.log_event(ts=1000.0, market_slug="m0", condition_id="c0",
                    kind="PAIR_COMPLETE", reason="PAIR_COMPLETE", size=100.0)

    db = tmp_path / "stats.db"

    def seed_markout(ts, cid, price, mid_h3):
        c = sqlite3.connect(str(db))
        try:
            c.execute("INSERT INTO fills (ts, condition_id, market_slug, "
                      "token_id, side, price, size, reason) "
                      "VALUES (?,?,?,?,?,?,?,?)",
                      (ts, cid, "m1", "tok", "UP", price, 100.0, "tape"))
            c.execute("INSERT INTO markouts (ts, condition_id, market_slug, "
                      "side, fill_price, size, ref_mid, ref_mid_source, "
                      "mid_h0, mid_h1, mid_h2, mid_h3) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                      (ts, cid, "m1", "UP", price, 100.0, price + 0.02,
                       "venue_clean", None, None, None, mid_h3))
            c.commit()
        finally:
            c.close()

    # Two recorded: drift -7.0c (0.33 vs 0.40) and +10.0c (0.30 vs 0.20).
    seed_markout(1100.0, "c1", 0.40, mid_h3=0.33)
    seed_markout(1101.0, "c2", 0.20, mid_h3=0.30)
    # Pending: window genuinely open (ts + 900s > now).
    seed_markout(time.time() + 100.0, "c3", 0.30, mid_h3=None)
    # no_markout: window elapsed, never recorded.
    seed_markout(1200.0, "c4", 0.25, mid_h3=None)

    ev = stats.pairs_ev()
    fh = ev["fill_horizon"]
    assert fh["n"] == 4
    assert fh["recorded"] == 2 and fh["pending"] == 1
    assert fh["no_markout"] == 1 and fh["no_column"] == 0
    assert fh["window_sec"] == 900.0
    assert fh["drift"] == {"n": 2, "mean_c": 1.5, "median_c": 1.5,
                            "pos": 1, "neg": 1}
    # The dashboard payload carries the same ladder (the tile reads snapshot).
    assert stats.snapshot()["pairs_ev"]["fill_horizon"]["recorded"] == 2


def test_pairs_ev_exit_card_no_column(monkeypatch, tmp_path):
    """A DB whose markouts table predates mid_h3 (the fleet has not restarted
    since the Session 50 migration) must read every exit as 'no_column' -- not
    silently claim the counterfactual is being captured."""
    import sqlite3

    _env(monkeypatch, tmp_path)
    from strategy import stats, store
    # The schema must exist before the raw inserts: `pairs_ev` reads the
    # market_events KPI counts up front, and a missing table would swallow
    # the whole read (it degrades to the empty card) -- which would make this
    # test pass for the WRONG reason. Seed via a real write, then recreate
    # the markouts table in its pre-mid_h3 shape.
    store.log_event(ts=999.0, market_slug="m0", condition_id="c0",
                    kind="QUOTING", reason="r", size=1.0)
    # Seed the rule era too: the fill-horizon read slices on the first
    # PAIR_COMPLETE, so without one it would stay at n=0 and never exercise
    # the pre-mid_h3 no_column branch.
    store.log_event(ts=1000.0, market_slug="m0", condition_id="c0",
                    kind="PAIR_COMPLETE", reason="PAIR_COMPLETE", size=100.0)

    db = tmp_path / "stats.db"
    c = sqlite3.connect(str(db))
    c.execute("DROP TABLE markouts")
    c.executescript("""
        CREATE TABLE markouts (id INTEGER PRIMARY KEY AUTOINCREMENT,
          ts REAL NOT NULL, condition_id TEXT, market_slug TEXT, side TEXT,
          fill_price REAL, size REAL, ref_mid REAL, ref_mid_source TEXT,
          mid_h0 REAL, mid_h1 REAL, mid_h2 REAL);
    """)
    c.execute("INSERT INTO fills (ts, condition_id, market_slug, side, price, "
              "size) VALUES (1100.0,'c1','m1','UP',0.40,100.0)")
    c.execute("INSERT INTO markouts (ts, condition_id, market_slug, side, "
              "fill_price, size, ref_mid, ref_mid_source) "
              "VALUES (1100.0,'c1','m1','UP',0.40,100.0,0.42,'venue_clean')")
    c.execute("INSERT INTO closes (ts, condition_id, market_slug, method, "
              "shares, up_price) VALUES (1100.05,'c1','m1','naked_exit',"
              "100.0,0.37)")
    c.commit()
    c.close()

    monkeypatch.setattr(stats, "DB", db)
    ev = stats.pairs_ev()
    card = ev["exit_card"]
    assert card["n"] == 1
    assert card["no_column"] == 1
    assert card["recorded"] == 0 and card["pending"] == 0
    # The fill-horizon ladder reads the same pre-migration markouts table:
    # every rule-era fill is no_column, never a false "pending".
    fh = ev["fill_horizon"]
    assert fh["n"] == 1 and fh["no_column"] == 1
    assert fh["recorded"] == 0 and fh["pending"] == 0
    assert fh["no_markout"] == 0 and fh["drift"] is None
