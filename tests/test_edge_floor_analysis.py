"""Edge floor analysis (#787): positions, buckets, sweep, confounds.

The decision this feeds is a money decision per pair, so these tests pin the
pair-level unit, the weakest-leg bucket key, the negative/unknown buckets the
live gate cannot express, and the sweep's conservation -- the places where a
wrong number would silently justify raising the floor.
"""

import hashlib
import sqlite3
import subprocess
import sys
from pathlib import Path

import scripts.edge_floor_analysis as efa
from core_brain.fill_quality_report import FillRow
from tests.test_fill_quality_report import (
    FILL_MS,
    FILL_SEC,
    _seed_close,
    _seed_fill,
    _seed_order,
    _seed_quote,
    _write_ddl,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _leg(pair="p1", cond="c1", tok="tU", edge=0.05, pnl=0.0, size=5.0,
         price=0.6, outcome="merged") -> FillRow:
    return FillRow(
        trade_id=f"{pair}-{tok}", order_id=f"o-{pair}-{tok}", pair_key=pair,
        condition_id=cond, token_id=tok, side="BUY", price=price, size=size,
        fill_ts_sec=FILL_SEC, posted_ts_ms=FILL_MS, mid=price, edge_vs_mid=edge,
        queue_ahead=None, book_size=None, opp_price=None,
        implied_pair_cost=price, outcome=outcome, pair_pnl=pnl, attr_pnl=pnl,
        t_to_res_sec=None, leg_gap_sec=0.0,
    )


def _bucket_row(bucket: str, closed: int, mean: float,
                min_obs: int = 30) -> efa.BucketRow:
    return efa.BucketRow(
        bucket=bucket, positions=closed, closed=closed, wins=closed,
        win_rate=1.0, wilson_lo=0.0, wilson_hi=1.0, mean_pnl=mean,
        median_pnl=mean, total_pnl=mean * closed,
        status="OK" if closed >= min_obs else "insufficient",
    )


def test_position_bucket_is_the_weakest_leg_and_negative_is_its_own_bucket():
    fills = (
        _leg(pair="p1", tok="tU", edge=0.05),
        _leg(pair="p1", tok="tD", edge=0.01),
        _leg(pair="p2", cond="c2", tok="tU", edge=-0.01),
        _leg(pair="p3", cond="c3", tok="tU", edge=None),
    )
    positions = {p.pair_key: p for p in efa.position_rows(fills)}
    assert positions["p1"].bucket == "0.00-0.02"
    assert positions["p1"].edge == 0.01
    assert positions["p2"].bucket == efa.NEGATIVE
    assert positions["p3"].bucket == efa.UNKNOWN


def test_position_sums_leg_pnl_shares_and_prices_paid():
    fills = (
        _leg(pair="p1", tok="tU", edge=0.05, pnl=1.0, size=4.0, price=0.60),
        _leg(pair="p1", tok="tD", edge=0.05, pnl=-0.4, size=6.0, price=0.38),
    )
    (position,) = efa.position_rows(fills)
    assert (position.pnl, position.shares) == (0.6, 10.0)
    assert round(position.paid_pair_price, 2) == 0.98


def test_bucket_table_reports_win_rate_mean_median_total_and_insufficiency():
    fills = (
        _leg(pair="p1", edge=0.025, pnl=2.0),
        _leg(pair="p2", edge=0.025, pnl=-1.0),
        _leg(pair="p3", edge=0.025, pnl=0.5),
    )
    (row,) = efa.bucket_table(efa.position_rows(fills), min_obs=30)
    assert (row.positions, row.closed, row.wins) == (3, 3, 2)
    assert round(row.win_rate, 4) == round(2 / 3, 4)
    assert (row.mean_pnl, row.median_pnl, row.total_pnl) == (0.5, 0.5, 1.5)
    assert row.status == "insufficient"
    (row,) = efa.bucket_table(efa.position_rows(fills), min_obs=3)
    assert row.status == "OK"


def test_open_position_is_counted_but_out_of_the_win_rate():
    fills = (
        _leg(pair="p1", edge=0.025, pnl=1.0),
        _leg(pair="p2", edge=0.025, pnl=0.0, outcome="open"),
    )
    (row,) = efa.bucket_table(efa.position_rows(fills))
    assert (row.positions, row.closed, row.wins) == (2, 1, 1)
    assert row.win_rate == 1.0
    assert row.mean_pnl == 1.0


def test_crossover_needs_min_obs_and_a_positive_mean_in_every_bucket_above():
    losing_low = (_bucket_row("0.02-0.03", 30, -0.5),
                  _bucket_row("0.03-0.04", 30, 0.5))
    assert efa.crossover(losing_low) == 0.03

    thin_high = (_bucket_row("0.02-0.03", 30, -0.5),
                 _bucket_row("0.03-0.04", 29, 0.5))
    assert efa.crossover(thin_high) is None

    losing_high = (_bucket_row("0.03-0.04", 30, 0.5),
                   _bucket_row("0.06+", 30, -0.1))
    assert efa.crossover(losing_high) is None


def test_sweep_labels_and_conservation_of_pairs():
    fills = (
        _leg(pair="p1", edge=0.01, pnl=-1.0),
        _leg(pair="p2", edge=0.025, pnl=-2.0),
        _leg(pair="p3", edge=0.035, pnl=3.0),
    )
    positions = efa.position_rows(fills)
    sweep = {row.floor: row for row in efa.floor_sweep(positions, [])}
    assert sweep[0.03].labels == ("0.00-0.02", "0.02-0.03")
    assert (sweep[0.03].blocked, sweep[0.03].kept) == (2, 1)
    assert sweep[0.03].blocked_pnl == -3.0
    assert sweep[0.03].kept_pnl == 3.0
    for row in sweep.values():
        assert row.blocked + row.kept == len(positions)


def test_negative_edge_is_reported_as_unblockable_below_every_floor():
    fills = (_leg(pair="p1", edge=-0.01, pnl=-5.0),)
    positions = efa.position_rows(fills)
    quotes = [{"edge_vs_mid": -0.01}, {"edge_vs_mid": 0.05}]
    for row in efa.floor_sweep(positions, quotes):
        # The live gate's label list cannot name a negative edge.
        assert row.blocked == 0
        assert row.negative_below_floor == 1


def _seeded_db(tmp_path: Path) -> Path:
    db = tmp_path / "seed.db"
    _write_ddl(db)
    conn = sqlite3.connect(db)
    # p1: a real pair, weakest leg at 0.01.
    _seed_order(conn, "o1", "c1", "tU", "BUY", 0.60, 4.0, "filled", "p1")
    _seed_fill(conn, "f1", "o1", 4.0, 0.60)
    _seed_quote(conn, "c1", "tU", "UP", 0.60, FILL_SEC - 30, mid=0.61,
                edge=0.05)
    _seed_order(conn, "o1d", "c1", "tD", "BUY", 0.38, 6.0, "filled", "p1")
    _seed_fill(conn, "f1d", "o1d", 6.0, 0.38)
    _seed_quote(conn, "c1", "tD", "DOWN", 0.38, FILL_SEC - 30, mid=0.39,
                edge=0.01)
    _seed_close(conn, "c1", "shadow_merge", 3.29)
    # p2: a bookkeeping close beside the settlement that actually closed it.
    _seed_order(conn, "o2", "c2", "tU", "BUY", 0.70, 5.0, "filled", "p2")
    _seed_fill(conn, "f2", "o2", 5.0, 0.70)
    _seed_quote(conn, "c2", "tU", "UP", 0.70, FILL_SEC - 30, mid=0.71,
                edge=0.04)
    _seed_close(conn, "c2", "venue_sync", 10.0)
    _seed_close(conn, "c2", "shadow_settlement", -1.0)
    # another run, excluded by --run-id.
    _seed_order(conn, "o9", "c9", "tU", "BUY", 0.50, 1.0, "filled", "p9",
                run="r2")
    _seed_fill(conn, "f9", "o9", 1.0, 0.50, run="r2")
    conn.commit()
    conn.close()
    return db


def test_bookkeeping_close_is_excluded_and_settlement_is_kept(tmp_path: Path):
    result = efa.analyze(_seeded_db(tmp_path), run_id="r1")
    assert result["data_quality"]["bookkeeping_closes_excluded"] == 1
    positions = {p["pair_key"]: p for p in result["positions"]}
    assert positions["p2"]["closed"] is True
    assert positions["p2"]["pnl"] == -1.0


def test_run_filter_excludes_another_runs_positions(tmp_path: Path):
    db = _seeded_db(tmp_path)
    filtered = efa.analyze(db, run_id="r1")
    assert "p9" not in {p["pair_key"] for p in filtered["positions"]}
    everything = efa.analyze(db)
    assert "p9" in {p["pair_key"] for p in everything["positions"]}


def test_confound_split_flags_a_band_that_reverses_the_direction():
    fills = (
        _leg(pair="p1", edge=0.01, pnl=-1.0, size=1.0),
        _leg(pair="p2", edge=0.05, pnl=2.0, size=1.0),
        _leg(pair="p3", edge=0.01, pnl=5.0, size=100.0),
        _leg(pair="p4", edge=0.05, pnl=-3.0, size=100.0),
    )
    positions = efa.position_rows(fills)
    categories = {p.condition_id: "Sports" for p in positions}
    shares = {s.name: s for s in efa.confound_splits(positions, categories)}
    small, large = shares["shares"].groups
    assert small.holds is True
    assert large.holds is False
    assert shares["shares"].holds is False


def test_confound_split_is_inconclusive_when_a_band_is_too_thin_to_measure():
    """A band with nothing in it is not a reversal, so it must not read 'fails'."""
    fills = (_leg(pair="p1", edge=0.01, pnl=-1.0),
             _leg(pair="p2", edge=0.05, pnl=2.0))
    positions = efa.position_rows(fills)
    shares = {s.name: s for s in efa.confound_splits(positions, {})}["shares"]
    assert shares.groups[0].holds is True
    assert shares.groups[1].holds is None
    assert shares.holds is None


def test_demo_store_run_exits_zero_and_leaves_the_file_untouched(tmp_path: Path):
    # A copy: opening a WAL store read-only still creates its -shm beside it,
    # and the suite must never write into the repo's real data/ directory.
    demo = tmp_path / "demo_orders.db"
    demo.write_bytes((REPO_ROOT / "data" / "demo_orders.db").read_bytes())
    before = (demo.stat().st_mtime_ns, hashlib.sha256(demo.read_bytes()).hexdigest())
    done = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "edge_floor_analysis.py"),
         "--db", str(demo), "--out", str(tmp_path / "out")],
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=300, check=False,
    )
    assert done.returncode == 0, done.stderr
    assert efa.REHEARSAL in done.stdout
    assert (tmp_path / "out" / "edge_floor.json").is_file()
    after = (demo.stat().st_mtime_ns, hashlib.sha256(demo.read_bytes()).hexdigest())
    assert after == before


def test_missing_store_exits_one_without_writing_a_table(tmp_path: Path):
    done = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "edge_floor_analysis.py"),
         "--db", str(tmp_path / "absent.db"), "--out", str(tmp_path / "out")],
        capture_output=True, text=True, cwd=REPO_ROOT, timeout=300, check=False,
    )
    assert done.returncode == 1
    assert "NO_DATA" in done.stdout
    # An empty table on disk would read like a finding of zero.
    assert not (tmp_path / "out").exists()
