"""Replay validation (U7): what the gates would have refused, in dollars.

Every fixture here is built in `tmp_path`. NONE of these tests touch the real
`maker.db` or `run/fleet.db`: a test that reads the live database measures
whatever the fleet did last night, so it would pass or fail on data rather than
on code. The two market shapes below are transcribed from the recorded run --
`lol-maz-mg1-2026-08-04` (98.397296 UP at 0.74, then 135.0 UP at 0.87) and
`wta-kalinsk-kessler-2026-08-04` (107.41 DOWN at 0.54, then 14.0 UP at 0.48) --
so the fixtures reproduce the observed failures without depending on the file.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from strategy.store import SCHEMA                      # noqa: E402
from scripts.replay_risk_gates import main, replay     # noqa: E402

LOL = "lol-maz-mg1-2026-08-04"
WTA = "wta-kalinsk-kessler-2026-08-04"


def _build(path: Path, fills=(), closes=(), quotes=()) -> Path:
    """A fixture database with the real schema and nothing else in it.

    Uses `strategy.store.SCHEMA` verbatim rather than a hand-rolled subset, so
    a column the replay reads cannot be present here and absent in production.
    """
    c = sqlite3.connect(str(path))
    c.executescript(SCHEMA)
    for i, (ts, slug, side, price, size) in enumerate(fills):
        c.execute(
            "INSERT INTO fills (ts, quote_id, market_slug, condition_id, "
            "token_id, side, price, size, reason) VALUES (?,?,?,?,?,?,?,?,?)",
            (ts, None, slug, "cond-" + slug, "tok-" + side, side, price, size,
             "tape"))
    for ts, slug, pnl, shares in closes:
        c.execute(
            "INSERT INTO closes (ts, condition_id, market_slug, method, shares, "
            "cost_basis, proceeds, realized_pnl, up_cost_removed, "
            "dn_cost_removed) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (ts, "cond-" + slug, slug, "merge", shares, 0.0, 0.0, pnl, 0.0, 0.0))
    for ts, slug, side, price, mid in quotes:
        c.execute(
            "INSERT INTO quotes (ts, market_slug, condition_id, token_id, side, "
            "price, size, queue_ahead, mid, edge_vs_mid, t_remaining) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (ts, slug, "cond-" + slug, "tok-" + side, side, price, 120.0, 0.0,
             mid, 0.0, 1e9))
    c.commit()
    c.close()
    return path


# --- the two recorded failures -------------------------------------------

def test_lol_maz_mg1_dollar_cap_avoids_seventy_dollars(tmp_path):
    """233.40 UP shares at an average of 0.8152 -- $190.26 at risk.

    $120 of that was inside the budget. The other $70.26 is what the dollar
    cap alone would have refused, and it is the number the plan sizes the cap
    on: the cap binds partway THROUGH the second fill, not at a fill boundary.
    """
    db = _build(tmp_path / "lol.db", fills=[
        (1785888340.4, LOL, "UP", 0.74, 98.397296),
        (1785890140.6, LOL, "UP", 0.87, 135.0),
    ])
    rep = replay(db)

    assert rep["by_gate"]["dollar_cap"]["naked_cost_avoided"] >= 70.0
    mkt = rep["by_market"][LOL]
    assert "dollar_cap" in mkt["gates"]
    assert mkt["gates"]["dollar_cap"] >= 70.0
    assert rep["fills"] == 2


def test_wta_kalinsk_kessler_reports_the_pair_cost_gate(tmp_path):
    """14 pairs assembled at $1.0200 against a $0.995 cap.

    The pair pays exactly $1.00, so the 2c above parity is a booked loss and
    not a risk. The replay has to name `pair_cost` on this market -- it is the
    only evidence the repo has that the rule, which never executed on the live
    path, would have fired.
    """
    db = _build(tmp_path / "wta.db", fills=[
        (1785888691.9, WTA, "DOWN", 0.54, 107.41),
        (1785888999.8, WTA, "UP", 0.48, 14.0),
    ])
    rep = replay(db)

    assert "pair_cost" in rep["by_market"][WTA]["gates"]
    assert rep["by_gate"]["pair_cost"]["fills"] >= 1
    # The over-parity leg was bought on the LIGHT side, so R4 exempts it on the
    # live path and it avoids no naked dollars. The fill count is what makes
    # the rule visible at all -- without it the row reads as "never fired".
    assert rep["by_market"][WTA]["gate_fills"]["pair_cost"] >= 1


# --- the gates must not refuse healthy flow ------------------------------

def test_healthy_two_sided_flow_is_never_refused(tmp_path):
    """In band, two-sided, under budget, pair under $1.00 -> nothing blocked.

    The stop condition in the plan is that the gates cut profitable flow. This
    is the floor under that: a market that did everything right must come back
    with a zero, or every other number in the report is unreadable.
    """
    db = _build(
        tmp_path / "healthy.db",
        fills=[
            (1000.0, "healthy-mkt", "UP", 0.50, 60.0),
            (1010.0, "healthy-mkt", "DOWN", 0.48, 60.0),
            (1020.0, "healthy-mkt", "UP", 0.51, 60.0),
            (1030.0, "healthy-mkt", "DOWN", 0.47, 60.0),
        ],
        closes=[(1100.0, "healthy-mkt", 12.50, 60.0)],
    )
    rep = replay(db)

    assert rep["refused_fills"] == 0
    assert rep["naked_cost_avoided"] == 0.0
    assert rep["realized_pnl_forgone"] == 0.0
    assert rep["by_gate"] == {}
    assert rep["stop_condition_triggered"] is False


# --- what the recorded data cannot answer --------------------------------

def test_depth_arm_is_unevaluated_not_passed(tmp_path):
    """`quotes` carries a mid and no ladder, so depth was never measured.

    `book_health` returns `depth_evaluated` for exactly this: a replay must be
    able to tell "the book had depth" apart from "the book's depth is not in
    the record". Reporting the second as the first would credit the gate with
    a test it never ran.
    """
    db = _build(tmp_path / "nodepth.db", fills=[
        (1000.0, "healthy-mkt", "UP", 0.50, 60.0),
        (1010.0, "healthy-mkt", "DOWN", 0.48, 60.0),
    ])
    rep = replay(db)

    assert rep["depth_arm"] == "UNEVALUATED"
    assert rep["depth_evaluations"] == 0
    assert rep["health_evaluations"] > 0
    assert any("depth" in n.lower() for n in rep["notes"])


def test_empty_database_reports_zero_rather_than_raising(tmp_path):
    """A fresh database is a zero report, not a crash and not a verdict."""
    db = _build(tmp_path / "empty.db")
    rep = replay(db)

    assert rep["fills"] == 0
    assert rep["markets"] == 0
    assert rep["refused_fills"] == 0
    assert rep["naked_cost_avoided"] == 0.0
    assert rep["realized_pnl_forgone"] == 0.0
    assert rep["stop_condition_triggered"] is False
    assert main([str(db)]) == 0


def test_missing_database_says_so_and_does_not_raise(tmp_path):
    """An absent file must read as absent, never as a measured zero."""
    rep = replay(tmp_path / "nope.db")
    assert rep["exists"] is False
    assert rep["fills"] == 0
    assert main([str(tmp_path / "nope.db")]) == 0


def test_realized_pnl_attribution_is_labelled_not_invented(tmp_path):
    """There is no per-fill realized P&L column, and the report has to say so.

    `closes` books realized money per MARKET. Splitting it across that
    market's fills is an attribution, not a record, and the difference is the
    whole reason the stop-condition number is readable at all.
    """
    db = _build(
        tmp_path / "pnl.db",
        fills=[(1000.0, "band-mkt", "UP", 0.90, 100.0)],
        closes=[(1100.0, "band-mkt", 20.0, 50.0)],
    )
    rep = replay(db)

    assert rep["realized_pnl_total"] == 20.0
    assert rep["by_market"]["band-mkt"]["gates"].get("price_band")
    assert any("per-fill" in n.lower() for n in rep["notes"])
