"""Tests for `scripts/pairs_ev_report` (Sessions 44-46).

The report is the hand-written-SQL measurement of the pairs-only rule's EV
made into one read-only command. These tests pin the report against a temp
database seeded through the real write module, so the dict the tests read
is the dict the operator's terminal gets -- including the empty-DB case
(verdict NO DATA, not a confident 0).
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _env(monkeypatch, tmp_path) -> Path:
    """Point the write module at a temp DB and seed the schema via a real write.

    The seed event is deliberately NOT a pairs-rule kind: a schema-seeding
    PAIR_COMPLETE would pollute the KPI denominator (Sessions 44-46 count
    the rule's own decisions, nothing else).
    """
    db = tmp_path / "ev.db"
    monkeypatch.setenv("HUNTER_DB", str(db))
    from strategy import store
    store.log_event(ts=999.0, market_slug="m0", condition_id="c0",
                    kind="QUOTING", reason="r", size=1.0)
    return db


def _seed_close(db: Path, ts: float, market: str, method: str, shares: float,
                pnl: float, cost: float = 0.0, proceeds: float = 0.0,
                fee: float = 0.0, cid: str | None = None) -> None:
    """Raw closes insert: the report only reads; the test needs explicit ts.
    `cid` overrides the default f"c-{market}" so the exit-wait tests can link
    a close to a fill that shares the same condition_id."""
    c = sqlite3.connect(str(db))
    try:
        c.execute(
            "INSERT INTO closes (ts, condition_id, market_slug, method, gas, "
            "shares, up_price, dn_price, cost_basis, proceeds, fee, "
            "realized_pnl, forgone_vs_settlement, up_cost_removed, "
            "dn_cost_removed) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ts, cid or f"c-{market}", market, method, None, shares, None, None,
             cost, proceeds, fee, pnl, None, cost, 0.0))
        c.commit()
    finally:
        c.close()


def _set_exit_price(db: Path, price: float) -> None:
    """Record the achieved per-share exit price in up_price, the way the
    side-aware naked_exit closes carry it (proceeds/shares is the fallback)."""
    c = sqlite3.connect(str(db))
    try:
        c.execute("UPDATE closes SET up_price=? WHERE method='naked_exit'",
                  (price,))
        c.commit()
    finally:
        c.close()


def _seed_fill_and_markout(db: Path, ts: float, cid: str, market: str,
                           side: str, price: float, size: float,
                           mid_h3: float | None) -> None:
    """A fill and its markout row sharing one ts. `store.log_fill` stamps its
    own time.time(), so the fixture inserts both rows raw; the report joins
    them by nearest ts (the ~0.3s fill/markout stamp gap is the live shape)."""
    c = sqlite3.connect(str(db))
    try:
        c.execute("INSERT INTO fills (ts, condition_id, market_slug, token_id, "
                  "side, price, size, reason) VALUES (?,?,?,?,?,?,?,?)",
                  (ts, cid, market, f"tok-{side}", side, price, size, "tape"))
        c.execute("INSERT INTO markouts (ts, condition_id, market_slug, side, "
                  "fill_price, size, ref_mid, ref_mid_source, mid_h0, mid_h1, "
                  "mid_h2, mid_h3) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  (ts, cid, market, side, price, size, price + 0.02,
                   "venue_clean", None, None, None, mid_h3))
        c.commit()
    finally:
        c.close()


def _seed_full(monkeypatch, tmp_path):
    """The Session-44-shaped fixture: 2 completes / 1 exit / 1 expiry, one
    rule-era merge set with an IQR outlier, and one pre-rule-era merge that
    must be excluded from the capture slice."""
    db = _env(monkeypatch, tmp_path)
    from strategy import store
    for ts, kind in ((1000.0, "PAIR_COMPLETE"), (1001.0, "PAIR_COMPLETE"),
                     (1002.0, "NAKED_EXIT"), (1003.0, "PAIR_WINDOW_EXPIRED")):
        store.log_event(ts=ts, market_slug="m1", condition_id="c1",
                        kind=kind, reason=kind, size=100.0)
    # Pre-rule-era merge: ts < first PAIR_COMPLETE (1000) -> excluded.
    _seed_close(db, 500.0, "m0", "merge", 100.0, 5.0)
    # Rule-era merges: rates 3.0 / 5.0 / 16.5 c/sh -> 16.5 is an IQR outlier.
    _seed_close(db, 1100.0, "m1", "merge", 100.0, 3.0)
    _seed_close(db, 1101.0, "m1", "merge", 100.0, 5.0)
    _seed_close(db, 1102.0, "m1", "merge", 100.0, 16.5)
    # One naked exit: 100 sh bought 0.40, sold 0.37, fee 1.00 -> -3.00.
    _seed_close(db, 1200.0, "m1", "naked_exit", 100.0, -3.0,
                cost=40.0, proceeds=37.0, fee=1.0)
    # Record the actual per-share exit price in up_price (the side-aware
    # closes carry it; proceeds/shares is only the fallback).
    c = sqlite3.connect(str(db))
    try:
        c.execute("UPDATE closes SET up_price=0.37 WHERE method='naked_exit'")
        c.commit()
    finally:
        c.close()
    return db


def test_report_kpi_block(monkeypatch, tmp_path):
    """Rates come from the rule's decisions; EV uses the in-force constants."""
    _seed_full(monkeypatch, tmp_path)
    from scripts.pairs_ev_report import report

    rep = report(tmp_path / "ev.db")
    k = rep["kpi"]
    assert k["one_sided"] == 4
    assert k["completions"] == 2 and k["exits"] == 1 and k["expired"] == 1
    assert k["completion_rate"] == pytest.approx(0.5)
    assert k["exit_rate"] == pytest.approx(0.25)
    # 0.5 x 3.68 - 0.25 x 3.67 = 0.9225 -> rounds to 0.923 (Session 45 pin).
    assert k["ev_cents"] == pytest.approx(0.923)
    assert k["verdict"] == "PASS (EV > 0)"
    assert rep["payoffs"] == {"complete_gain_cents": 3.68,
                              "exit_cost_cents": 3.67}


def test_report_exits_detail(monkeypatch, tmp_path):
    """The exit economics: realized pnl per share, fee included."""
    _seed_full(monkeypatch, tmp_path)
    from scripts.pairs_ev_report import report

    ex = report(tmp_path / "ev.db")["exits"]
    assert ex["n"] == 1 and ex["shares"] == 100.0
    assert ex["pnl"] == pytest.approx(-3.0)
    assert ex["per_share_c"] == pytest.approx(-3.0)
    assert ex["closes"][0]["avg_cost"] == pytest.approx(0.40)
    assert ex["closes"][0]["exit_price"] == pytest.approx(37.0 / 100.0)


def test_report_merge_capture_distribution_and_outliers(monkeypatch, tmp_path):
    """Dollar-weighted capture, per-close distribution, and IQR outlier flags."""
    _seed_full(monkeypatch, tmp_path)
    from scripts.pairs_ev_report import report

    mg = report(tmp_path / "ev.db")["merges"]
    # The pre-rule-era merge (m0, ts 500) is excluded: only the three rule-era.
    assert mg["n"] == 3 and mg["shares"] == 300.0
    assert mg["pnl"] == pytest.approx(24.5)
    assert mg["per_share_c"] == pytest.approx(24.5 / 3.0, abs=1e-3)
    assert mg["all_positive"] is True

    d = mg["distribution"]
    assert d["n"] == 3
    assert d["mean"] == pytest.approx(8.1667, abs=1e-3)
    assert d["median"] == pytest.approx(5.0)
    assert d["min"] == pytest.approx(3.0) and d["max"] == pytest.approx(16.5)
    lo, hi = d["iqr_fences"]
    assert lo == pytest.approx(0.0) and hi == pytest.approx(8.0)

    assert len(mg["outliers"]) == 1
    assert mg["outliers"][0]["market"] == "m1"
    assert mg["outliers"][0]["per_share_c"] == pytest.approx(16.5)

    # Dollar-weighted capture per market: all three rule-era merges are m1.
    assert len(mg["by_market"]) == 1
    assert mg["by_market"][0]["market"] == "m1"


def test_report_realized_and_attribution(monkeypatch, tmp_path):
    """Realized EV in dollars per one-sided fill; merges-vs-completions check."""
    _seed_full(monkeypatch, tmp_path)
    from scripts.pairs_ev_report import report

    rl = report(tmp_path / "ev.db")["realized"]
    assert rl["completions"] == pytest.approx(24.5)
    assert rl["exits"] == pytest.approx(-3.0)
    assert rl["total"] == pytest.approx(21.5)
    assert rl["per_fill"] == pytest.approx(21.5 / 4.0)

    att = report(tmp_path / "ev.db")["attribution"]
    # m1: 2 completions vs 3 rule-era merges -> flagged. m0's pre-rule merge
    # is outside the slice, so it does not appear.
    assert att == [{"market": "m1", "merges": 3, "completions": 2}]


def test_exit_counterfactual_recorded_pending_and_missing(monkeypatch, tmp_path):
    """Session 50: the 15m mid vs exit price per naked exit, with the honest
    states -- recorded when mid_h3 has landed, pending when the 15m has not
    elapsed, no_markout when the fill has no markout row."""
    db = _env(monkeypatch, tmp_path)
    from strategy import store
    # exit 1 -- recorded: fill 0.40, exit sold 0.37, 15m mid 0.33.
    _seed_fill_and_markout(db, 1100.0, "c1", "m1", "UP", 0.40, 100.0,
                           mid_h3=0.33)
    _seed_close(db, 1100.05, "m1", "naked_exit", 100.0, -3.0, cost=40.0,
                proceeds=37.0, fee=1.0, cid="c1")
    # exit 2 -- pending: mid_h3 still NULL (15m not elapsed).
    _seed_fill_and_markout(db, 1200.0, "c2", "m2", "UP", 0.20, 100.0,
                           mid_h3=None)
    _seed_close(db, 1200.05, "m2", "naked_exit", 100.0, -1.0, cost=20.0,
                proceeds=19.0, fee=1.0, cid="c2")
    # exit 3 -- fill present, no markout row for it.
    c = sqlite3.connect(str(db))
    try:
        c.execute("INSERT INTO fills (ts, condition_id, market_slug, token_id, "
                  "side, price, size, reason) VALUES (1300.0,'c3','m3','t','UP',"
                  "0.10,100.0,'tape')")
        c.commit()
    finally:
        c.close()
    _seed_close(db, 1300.05, "m3", "naked_exit", 100.0, -2.0, cost=10.0,
                proceeds=9.0, fee=1.0, cid="c3")
    _set_exit_price(db, 0.37)   # exit 1's achieved price (exits 2/3 share it)

    from scripts.pairs_ev_report import report
    xc = report(db)["exit_counterfactual"]
    assert xc["recorded"] == 1 and xc["pending"] == 1 and xc["no_markout"] == 1
    by_status = {e["status"]: e for e in xc["closes"]}
    r0 = by_status["recorded"]
    assert r0["mid_h3"] == pytest.approx(0.33)
    assert r0["gap_c"] == pytest.approx((0.33 - 0.37) * 100.0)   # -4c
    assert r0["fill_price"] == pytest.approx(0.40)
    assert r0["fill_reason"] == "tape"
    assert by_status["pending"]["mid_h3"] is None
    assert by_status["no_markout"]["fill_price"] == pytest.approx(0.10)
    a = xc["aggregate"]
    assert a["n"] == 1 and a["exit_beat_wait"] == 1 and a["wait_maybe_better"] == 0
    assert a["median_gap_c"] == pytest.approx(-4.0)
    assert a["mean_15m_drift_c"] == pytest.approx((0.33 - 0.40) * 100.0)


def test_exit_counterfactual_no_column_reports_honestly(monkeypatch, tmp_path):
    """A DB whose markouts table predates mid_h3 (the fleet has not restarted
    since Session 50, so the migration has not run) must read 'no_column' --
    not silently report every exit as if it had no reading."""
    db = tmp_path / "old.db"
    c = sqlite3.connect(str(db))
    c.executescript("""
        CREATE TABLE closes (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,
          condition_id TEXT, market_slug TEXT, method TEXT DEFAULT 'sell',
          shares REAL, cost_basis REAL, proceeds REAL, fee REAL,
          realized_pnl REAL, up_price REAL, dn_price REAL);
        CREATE TABLE fills (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,
          condition_id TEXT, market_slug TEXT, token_id TEXT, side TEXT,
          price REAL, size REAL, reason TEXT DEFAULT 'queue');
        CREATE TABLE markouts (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,
          condition_id TEXT, market_slug TEXT, side TEXT, fill_price REAL,
          size REAL, ref_mid REAL, ref_mid_source TEXT,
          mid_h0 REAL, mid_h1 REAL, mid_h2 REAL, done INTEGER DEFAULT 0);
        INSERT INTO fills (ts, condition_id, market_slug, token_id, side,
          price, size, reason) VALUES (1100.0, 'c1', 'm1', 't', 'UP', 0.40,
          100.0, 'tape');
        INSERT INTO markouts (ts, condition_id, market_slug, side, fill_price,
          size, ref_mid, ref_mid_source) VALUES (1100.0, 'c1', 'm1', 'UP', 0.40,
          100.0, 0.42, 'venue_clean');
        INSERT INTO closes (ts, condition_id, market_slug, method, shares,
          cost_basis, proceeds, fee, realized_pnl, up_price)
          VALUES (1100.05, 'c1', 'm1', 'naked_exit', 100.0, 40.0, 37.0, 1.0,
          -3.0, 0.37);
    """)
    c.commit()
    c.close()
    from scripts.pairs_ev_report import report
    xc = report(db)["exit_counterfactual"]
    assert xc["no_column"] == 1
    assert xc["closes"][0]["status"] == "no_column"
    assert xc["closes"][0]["fill_reason"] == "tape"
    assert xc["aggregate"] is None


def test_report_empty_db_is_no_data_not_zero(monkeypatch, tmp_path):
    """A database with no rule decisions must not read as a measured breakeven."""
    db = tmp_path / "empty.db"
    monkeypatch.setenv("HUNTER_DB", str(db))
    from scripts.pairs_ev_report import report

    rep = report(db)
    assert rep["kpi"]["one_sided"] == 0
    assert rep["kpi"]["ev_cents"] is None
    assert rep["kpi"]["verdict"] == "NO DATA"
    assert rep["exits"]["n"] == 0 and rep["merges"]["n"] == 0
    assert rep["realized"]["per_fill"] is None
    assert rep["attribution"] == []
    assert rep["exit_counterfactual"]["closes"] == []
    assert rep["exit_counterfactual"]["aggregate"] is None


def test_main_json_output(monkeypatch, tmp_path):
    """`--json` writes the exact report dict, UTF-8 (Windows-host safe)."""
    _seed_full(monkeypatch, tmp_path)
    from scripts.pairs_ev_report import main, report

    out = tmp_path / "rep.json"
    assert main([str(tmp_path / "ev.db"), "--json", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8")) == report(
        tmp_path / "ev.db")
