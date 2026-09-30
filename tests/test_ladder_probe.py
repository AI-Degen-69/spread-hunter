"""The ladder probe scores ladder shapes on recorded tapes, read-only."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from scripts import ladder_probe
from scripts.ladder_probe import (
    MarketTape,
    mean_ci90,
    refuse_output,
    refuse_tape,
    run,
    simulate_market,
    summarise,
    verdict,
)

OPEN = 1_700_000_000
RUNGS = (0.45, 0.48)


def _tape_db(path: Path, markets, ticks):
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE markets (token_id TEXT PRIMARY KEY, condition_id TEXT NOT NULL,"
        " question TEXT NOT NULL, slug TEXT NOT NULL, volume_24h REAL NOT NULL,"
        " first_seen INTEGER NOT NULL, up_wins INTEGER)"
    )
    con.execute(
        "CREATE TABLE ticks (token_id TEXT NOT NULL, ts INTEGER NOT NULL,"
        " price REAL NOT NULL, PRIMARY KEY (token_id, ts))"
    )
    con.executemany(
        "INSERT INTO markets VALUES (?,?,?,?,?,?,?)", markets)
    con.executemany("INSERT INTO ticks VALUES (?,?,?)", ticks)
    con.commit()
    con.close()


def _market(**over):
    base = dict(condition_id="0x1", open_ts=OPEN, close_ts=OPEN + 300,
                leg_a=[(OPEN + 5, 0.50), (OPEN + 10, 0.44)],
                leg_b=[(OPEN + 5, 0.50), (OPEN + 10, 0.43)],
                winner="leg_a")
    base.update(over)
    return MarketTape(**base)


# --- simulation ---------------------------------------------------------------

def test_both_legs_crossing_makes_a_pair():
    res = simulate_market(_market(), rungs=RUNGS, exit_sec=(60,))
    assert res["outcome"] == "pair"
    assert res["pair_cost"] == pytest.approx(0.44 + 0.43)
    assert res["pnl"]["pair"] == pytest.approx(1.0 - 0.87)


def test_pair_uses_first_fill_per_side_oldest_first():
    m = _market(leg_a=[(OPEN + 5, 0.50), (OPEN + 8, 0.46), (OPEN + 20, 0.40)],
                leg_b=[(OPEN + 5, 0.50), (OPEN + 10, 0.44)])
    res = simulate_market(m, rungs=RUNGS, exit_sec=(60,))
    assert res["pair_cost"] == pytest.approx(0.46 + 0.44)


def test_one_leg_scores_resolution_value_not_zero():
    m = _market(leg_a=[(OPEN + 5, 0.50), (OPEN + 10, 0.44)],
                leg_b=[(OPEN + 5, 0.50), (OPEN + 10, 0.56)],
                winner="leg_b")
    res = simulate_market(m, rungs=RUNGS, exit_sec=(60,))
    assert res["outcome"] == "one_leg_a"
    # leg_a bid filled at 0.44; leg_a lost -> worth 0.0
    assert res["pnl"]["hold"] == pytest.approx(0.0 - 0.44)


def test_one_leg_winner_scores_full_value():
    m = _market(leg_a=[(OPEN + 5, 0.50), (OPEN + 10, 0.44)],
                leg_b=[(OPEN + 5, 0.50), (OPEN + 10, 0.56)],
                winner="leg_a")
    res = simulate_market(m, rungs=RUNGS, exit_sec=(60,))
    assert res["outcome"] == "one_leg_a"
    assert res["pnl"]["hold"] == pytest.approx(1.0 - 0.44)


def test_timed_exit_uses_last_price_before_deadline_minus_slippage():
    m = _market(leg_a=[(OPEN + 5, 0.60), (OPEN + 30, 0.44), (OPEN + 59, 0.40),
                       (OPEN + 61, 0.30)],
                leg_b=[(OPEN + 5, 0.50), (OPEN + 10, 0.56)],
                winner="leg_a")
    res = simulate_market(m, rungs=RUNGS, exit_sec=(60,))
    assert res["outcome"] == "one_leg_a"
    assert res["pnl"]["exit_60"] == pytest.approx(0.40 - 0.02 - 0.44)


def test_timed_exit_without_early_sample_is_unmeasurable():
    m = _market(leg_a=[(OPEN + 100, 0.44)],
                leg_b=[(OPEN + 5, 0.50), (OPEN + 10, 0.56)],
                winner="leg_a")
    res = simulate_market(m, rungs=RUNGS, exit_sec=(60,))
    assert res["outcome"] == "one_leg_a"
    assert res["pnl"]["exit_60"] is None


def test_nothing_fills_is_nothing():
    m = _market(leg_a=[(OPEN + 5, 0.50), (OPEN + 10, 0.51)],
                leg_b=[(OPEN + 5, 0.50), (OPEN + 10, 0.51)])
    res = simulate_market(m, rungs=RUNGS, exit_sec=(60,))
    assert res["outcome"] == "nothing"


def test_unknown_winner_is_unmeasurable_on_hold():
    m = _market(leg_a=[(OPEN + 5, 0.50), (OPEN + 10, 0.44)],
                leg_b=[(OPEN + 5, 0.50), (OPEN + 10, 0.56)],
                winner=None)
    res = simulate_market(m, rungs=RUNGS, exit_sec=(60,))
    assert res["outcome"] == "one_leg_a"
    assert res["pnl"]["hold"] is None


# --- statistics -----------------------------------------------------------------

def test_ci90_excludes_zero_on_clear_edge():
    mean, lo, hi = mean_ci90([0.05] * 30)
    assert lo > 0


def test_ci90_includes_zero_on_noise():
    mean, lo, hi = mean_ci90([0.05, -0.05] * 6)
    assert lo < 0 < hi


def test_verdict_prefers_more_levels_only_with_proof():
    stats = {"2": {"n": 200, "any_fill": 100}, "4": {"n": 200, "any_fill": 140}}
    assert verdict(stats, {}, min_markets=10)["shape"] == "4"
    tied = {"2": {"n": 200, "any_fill": 100}, "4": {"n": 200, "any_fill": 102}}
    assert verdict(tied, {}, min_markets=10)["shape"] == "2"


def test_verdict_is_unmeasurable_when_too_few_markets():
    stats = {"2": {"n": 3, "any_fill": 2}, "4": {"n": 3, "any_fill": 3}}
    assert verdict(stats, {}, min_markets=10)["shape"] == "unmeasurable"


# --- read-only guarantees ---------------------------------------------------------

def test_run_leaves_the_tape_untouched(tmp_path):
    tape = tmp_path / "some_tape.db"
    _tape_db(tape,
             [("a", "0x1", "q", "btc-5min-x", 1.0, OPEN, None),
              ("b", "0x1", "q", "btc-5min-x", 1.0, OPEN, None)],
             [("a", OPEN + 5, 0.50), ("b", OPEN + 5, 0.50)])
    before = tape.read_bytes()
    out = tmp_path / "report.json"
    run(str(tape), str(out), slug_like="btc-5min-%", window_sec=300)
    assert tape.read_bytes() == before
    report = json.loads(out.read_text())
    assert report["markets"] == 1


def test_production_store_names_are_refused(tmp_path):
    with pytest.raises(ladder_probe.TapeRefused):
        refuse_tape(tmp_path / "orders.db")
    with pytest.raises(ladder_probe.TapeRefused):
        refuse_output(tmp_path / "data" / "orders.db")
