"""The queue report's arithmetic and operator-safe default path.

The first version aggregated per MARKET: it summed every mark's movement and
divided by the wall-clock span between the first and last retained mark. Two
compounding errors, both inflating the rate:

  1. Marks whose `cancel_decay` is NULL were dropped, but the first retained
     delta still covers the interval back to the dropped one. Movement spanned
     N intervals while the divisor covered N-1.
  2. `traded` summed deltas from every level interleaved, while the queue it
     was divided into was a single level's median. A combined-level rate over a
     per-level queue is not a ratio of anything.

Both push the same way: rates too high, clear times too short, markets looking
more joinable than they are. For a report whose entire job is to say whether a
queue is joinable, that is the one bias that must not exist.

The fix is to work in DELTAS. Each mark carries the interval it actually
covers, deltas are paired only within one level and one run, and every rate is
computed per level before anything is aggregated to a market.
"""
from __future__ import annotations

import math

import pytest

from core_brain.shadow_exec import ensure_shadow_tables, write_queue_mark
from scripts.queue_report import (
    Delta, level_stats, load_marks, market_stats, verdict,
)


def _mark(db, *, ts, price=0.47, size, traded, decay, run="shadow-a",
          token="tok-up", slug="m"):
    write_queue_mark(db, ts=ts, condition_id="0xabc", market_slug=slug,
                     token_id=token, price=price, level_size=size,
                     traded=traded, cancel_decay=decay, queue_minutes=None,
                     run_id=run)


class TestLoadDeltas:
    def test_each_delta_carries_the_interval_it_actually_covers(self, tmp_path):
        # Marks at 100, 160, 220. The 160 mark's movement covers 100->160 even
        # though 100 itself has no decay of its own. Dividing that movement by
        # the 160->220 span alone is the bug this test exists for.
        db = tmp_path / "s.db"
        ensure_shadow_tables(db)
        _mark(db, ts=100.0, size=1000.0, traded=0.0, decay=None)
        _mark(db, ts=160.0, size=700.0, traded=100.0, decay=200.0)
        _mark(db, ts=220.0, size=500.0, traded=100.0, decay=100.0)

        deltas = load_marks(db, minutes=None).deltas
        assert [d.dt_min for d in deltas] == [1.0, 1.0]
        assert [d.traded for d in deltas] == [100.0, 100.0]
        assert sum(d.dt_min for d in deltas) == 2.0, (
            "two intervals of movement must divide by two minutes, not one")

    def test_deltas_never_pair_across_runs(self, tmp_path):
        # A reused shadow.db. The new run's first mark has no predecessor of
        # its own and must not borrow the old run's.
        db = tmp_path / "s.db"
        ensure_shadow_tables(db)
        _mark(db, ts=100.0, size=9000.0, traded=0.0, decay=None, run="old")
        _mark(db, ts=5000.0, size=1000.0, traded=0.0, decay=None, run="new")
        _mark(db, ts=5060.0, size=900.0, traded=50.0, decay=50.0, run="new")

        deltas = load_marks(db, minutes=None).deltas
        assert len(deltas) == 1
        assert deltas[0].run_id == "new"
        assert deltas[0].dt_min == 1.0

    def test_deltas_never_pair_across_levels(self, tmp_path):
        db = tmp_path / "s.db"
        ensure_shadow_tables(db)
        _mark(db, ts=100.0, price=0.47, size=1000.0, traded=0.0, decay=None)
        _mark(db, ts=110.0, price=0.48, size=20.0, traded=0.0, decay=None)
        _mark(db, ts=160.0, price=0.47, size=900.0, traded=50.0, decay=50.0)

        deltas = load_marks(db, minutes=None).deltas
        assert len(deltas) == 1
        assert deltas[0].price == 0.47
        assert deltas[0].dt_min == 1.0

    def test_the_minutes_filter_keeps_whole_deltas(self, tmp_path):
        db = tmp_path / "s.db"
        ensure_shadow_tables(db)
        for i, ts in enumerate((0.0, 60.0, 120.0, 180.0)):
            _mark(db, ts=ts, size=1000.0 - 10 * i, traded=5.0,
                  decay=None if i == 0 else 5.0)
        recent = load_marks(db, minutes=1.5).deltas
        assert [d.ts for d in recent] == [120.0, 180.0]

    def test_zero_minutes_is_not_treated_as_no_filter(self, tmp_path):
        # `if minutes:` made --minutes 0 report the entire database.
        db = tmp_path / "s.db"
        ensure_shadow_tables(db)
        _mark(db, ts=0.0, size=1000.0, traded=0.0, decay=None)
        _mark(db, ts=60.0, size=900.0, traded=50.0, decay=50.0)
        assert load_marks(db, minutes=0.0).deltas == []

    def test_a_negative_window_is_refused(self, tmp_path):
        db = tmp_path / "s.db"
        ensure_shadow_tables(db)
        with pytest.raises(ValueError, match="minutes"):
            load_marks(db, minutes=-5.0).deltas

    def test_a_store_without_the_table_reads_as_empty(self, tmp_path):
        import sqlite3

        db = tmp_path / "old.db"
        sqlite3.connect(db).close()
        assert load_marks(db, minutes=None).deltas == []


class TestZeroSizeReads:
    """A level that reads as 0 and comes back is a failed book read.

    `queue_ahead_at` returns 0.0 when the price is simply absent from the book
    it was handed, which is indistinguishable from a level that genuinely
    emptied. On the tennis markets 6-9% of marks read exactly 0 while the tape
    showed no trade at all -- one observed delta was 154,132 shares to 0 with
    nothing on the tape explaining it. Differencing against a read like that
    invents a six-figure cancel.
    """

    def test_a_delta_into_a_zero_read_is_dropped(self, tmp_path):
        db = tmp_path / "s.db"
        ensure_shadow_tables(db)
        _mark(db, ts=0.0, size=20000.0, traded=0.0, decay=None)
        _mark(db, ts=60.0, size=0.0, traded=0.0, decay=20000.0)

        assert load_marks(db, minutes=None).deltas == []

    def test_a_delta_out_of_a_zero_read_is_dropped(self, tmp_path):
        # The rebound is the other half of the same artefact: 0 -> 20,000
        # reads as 20,000 shares joining, which would net against real decay
        # elsewhere on the level.
        db = tmp_path / "s.db"
        ensure_shadow_tables(db)
        _mark(db, ts=0.0, size=0.0, traded=0.0, decay=None)
        _mark(db, ts=60.0, size=20000.0, traded=0.0, decay=-20000.0)

        assert load_marks(db, minutes=None).deltas == []

    def test_a_dropped_delta_does_not_stretch_the_next_interval(self, tmp_path):
        # 20k -> 0 -> 20k drops BOTH deltas. Bridging the gap instead would
        # pair the two good reads across two minutes and report a clean zero,
        # hiding that the observation window was unusable.
        db = tmp_path / "s.db"
        ensure_shadow_tables(db)
        _mark(db, ts=0.0, size=20000.0, traded=0.0, decay=None)
        _mark(db, ts=60.0, size=0.0, traded=0.0, decay=20000.0)
        _mark(db, ts=120.0, size=20000.0, traded=0.0, decay=-20000.0)

        marks = load_marks(db, minutes=None)
        assert marks.deltas == []
        assert marks.dropped == {"m": 2}

    def test_drops_are_counted_per_market(self, tmp_path):
        db = tmp_path / "s.db"
        ensure_shadow_tables(db)
        _mark(db, ts=0.0, size=900.0, traded=0.0, decay=None, slug="clean")
        _mark(db, ts=60.0, size=800.0, traded=0.0, decay=100.0, slug="clean")
        _mark(db, ts=0.0, size=900.0, traded=0.0, decay=None, slug="flaky",
              token="tok-2")
        _mark(db, ts=60.0, size=0.0, traded=0.0, decay=900.0, slug="flaky",
              token="tok-2")

        marks = load_marks(db, minutes=None)
        assert marks.dropped == {"flaky": 1}
        assert [d.market_slug for d in marks.deltas] == ["clean"]

    def test_a_level_that_reads_empty_on_both_sides_is_still_dropped(
            self, tmp_path):
        # 0 -> 0 carries no information either way, and keeping it would put a
        # zero-size level into the median.
        db = tmp_path / "s.db"
        ensure_shadow_tables(db)
        _mark(db, ts=0.0, size=0.0, traded=0.0, decay=None)
        _mark(db, ts=60.0, size=0.0, traded=0.0, decay=0.0)

        assert load_marks(db, minutes=None).deltas == []

    def test_a_clean_run_reports_no_drops(self, tmp_path):
        db = tmp_path / "s.db"
        ensure_shadow_tables(db)
        _mark(db, ts=0.0, size=600.0, traded=0.0, decay=None)
        _mark(db, ts=60.0, size=500.0, traded=50.0, decay=50.0)

        assert load_marks(db, minutes=None).dropped == {}


def _d(dt_min, traded, decay, price=0.47, slug="m", token="tok-up", size=600.0):
    return Delta(ts=0.0, run_id="r", market_slug=slug, token_id=token,
                 price=price, level_size=size, traded=traded,
                 cancel_decay=decay, dt_min=dt_min)


class TestLevelStats:
    def test_rates_divide_by_the_summed_interval(self):
        # 200 traded over two minutes is 100/min, not 200/min.
        stats = level_stats([_d(1.0, 100.0, 0.0), _d(1.0, 100.0, 0.0)])
        assert len(stats) == 1
        assert stats[0].trade_rate == pytest.approx(100.0)

    def test_clear_time_counts_trades_only_and_then_everything(self):
        # 600 resting; 100/min traded, 100/min cancelled.
        stats = level_stats([_d(1.0, 100.0, 100.0)])
        assert stats[0].clear_trade == pytest.approx(6.0)
        assert stats[0].clear_all == pytest.approx(3.0)

    def test_a_level_that_never_trades_is_unmeasurable_not_instant(self):
        stats = level_stats([_d(1.0, 0.0, 0.0)])
        assert stats[0].clear_trade == math.inf
        assert stats[0].clear_all == math.inf

    def test_negative_decay_does_not_credit_queue_progress(self):
        # Size joining behind us is recorded honestly by the collector, but it
        # is not progress toward the front and must not shorten a clear time.
        stats = level_stats([_d(1.0, 0.0, -500.0)])
        assert stats[0].clear_all == math.inf

    def test_levels_are_reported_separately(self):
        stats = level_stats([_d(1.0, 100.0, 0.0, price=0.47),
                             _d(1.0, 10.0, 0.0, price=0.48)])
        assert {round(s.price, 2) for s in stats} == {0.47, 0.48}


class TestNetDecayNotSummedPositives:
    """Summing only the positive deltas turned oscillation into progress.

    The collector is right to record `cancel_decay` unclamped -- a negative
    value is real information about size joining the level. The READER was
    wrong to sum `max(0, decay)`: that keeps every downswing and discards every
    upswing, so a level flapping around a flat mean reads as pure decay. On the
    two tennis markets the median cycle-to-cycle change in `level_size` was
    41-59%, and summed positives came to 3.4-4.0x the level's whole net drift
    across the run.

    Net over the window is the only aggregation that cannot manufacture
    progress out of noise.
    """

    def test_oscillation_around_a_flat_level_is_not_decay(self):
        # 1000 -> 100 -> 1000, no tape. Summed positives say 900 shares left
        # and the queue clears in about a minute. Nothing left.
        stats = level_stats([_d(1.0, 0.0, 900.0, size=100.0),
                             _d(1.0, 0.0, -900.0, size=1000.0)])
        assert stats[0].cancel_rate == pytest.approx(0.0)
        assert stats[0].clear_all == math.inf

    def test_the_cancel_rate_is_the_net_over_the_span(self):
        # +300 then -100 over two minutes is 100/min, not 150/min.
        stats = level_stats([_d(1.0, 0.0, 300.0), _d(1.0, 0.0, -100.0)])
        assert stats[0].cancel_rate == pytest.approx(100.0)

    def test_a_level_filling_in_faster_than_it_trades_never_clears(self):
        stats = level_stats([_d(1.0, 50.0, -500.0)])
        assert stats[0].clear_all == math.inf
        assert stats[0].clear_trade == pytest.approx(12.0)

    def test_real_one_way_decay_still_reads_as_decay(self):
        # The MLB levels: no tape at all, size falling monotonically. This is
        # the case the fix must NOT suppress.
        stats = level_stats([_d(1.0, 0.0, 400.0, size=20000.0),
                             _d(1.0, 0.0, 400.0, size=19600.0)])
        assert stats[0].cancel_rate == pytest.approx(400.0)
        assert stats[0].clear_all == pytest.approx(19800.0 / 400.0)


class TestMarketStats:
    def test_a_market_reports_its_median_level(self):
        # Three levels clearing in 2, 6 and 60 minutes -> the market reads 6.
        deltas = [_d(1.0, 300.0, 0.0, price=0.47),
                  _d(1.0, 100.0, 0.0, price=0.48),
                  _d(1.0, 10.0, 0.0, price=0.49)]
        markets = market_stats(level_stats(deltas))
        assert len(markets) == 1
        assert markets[0].clear_all == pytest.approx(6.0)

    def test_the_level_count_is_levels_not_observations(self):
        # The header said `levels` while counting mark rows across time, so a
        # market watched for ten cycles displayed ten levels.
        deltas = [_d(1.0, 100.0, 0.0, price=0.47) for _ in range(10)]
        markets = market_stats(level_stats(deltas))
        assert markets[0].levels == 1
        assert markets[0].observations == 10


class TestVerdict:
    @pytest.mark.parametrize("minutes,expected", [
        (5.0, "JOINABLE"), (59.9, "JOINABLE"), (60.0, "JOINABLE"),
        (61.0, "ambiguous"), (719.0, "ambiguous"),
        (720.0, "NEVER"), (math.inf, "NEVER"),
    ])
    def test_the_bands_are_pinned(self, minutes, expected):
        assert verdict(minutes) == expected


class TestThePublicPaths:
    """The exit codes are the operator contract, and nothing asserted them.

    Everything above tests the pure helpers. `report` and `main` are what an
    operator actually runs, and their three outcomes -- nothing to report,
    a real reading, a bad argument -- were untested.
    """

    def test_no_history_explains_itself_and_exits_one(self, tmp_path, capsys):
        import sqlite3

        from scripts.queue_report import report

        db = tmp_path / "empty.db"
        sqlite3.connect(db).close()
        assert report(db, None) == 1
        out = capsys.readouterr().out
        assert "predates the telemetry" in out
        assert "wait one cycle" in out

    def test_a_real_reading_prints_the_verdict_and_exits_zero(
            self, tmp_path, capsys):
        from scripts.queue_report import report

        db = tmp_path / "s.db"
        ensure_shadow_tables(db)
        # 600 resting, 300 shares/min traded -> 2 minutes to clear: JOINABLE.
        _mark(db, ts=0.0, size=600.0, traded=0.0, decay=None)
        _mark(db, ts=60.0, size=600.0, traded=300.0, decay=0.0)

        assert report(db, None) == 0
        out = capsys.readouterr().out
        assert "JOINABLE" in out
        assert "DIRECTIONAL" in out, "the caveat must survive into the output"
        assert "cancel share of all queue movement" in out
        assert "joinable now: ['m']" in out

    def test_an_unclearable_queue_reads_as_never(self, tmp_path, capsys):
        from scripts.queue_report import report

        db = tmp_path / "s.db"
        ensure_shadow_tables(db)
        _mark(db, ts=0.0, size=50000.0, traded=0.0, decay=None)
        _mark(db, ts=60.0, size=50000.0, traded=0.0, decay=0.0)

        assert report(db, None) == 0
        out = capsys.readouterr().out
        assert "NEVER" in out
        assert "inf" in out
        assert "joinable now: none" in out

    def test_the_report_names_failed_book_reads_per_market(
            self, tmp_path, capsys):
        from scripts.queue_report import report

        db = tmp_path / "s.db"
        ensure_shadow_tables(db)
        _mark(db, ts=0.0, size=600.0, traded=0.0, decay=None)
        _mark(db, ts=60.0, size=600.0, traded=300.0, decay=0.0)
        _mark(db, ts=120.0, size=0.0, traded=0.0, decay=600.0)

        assert report(db, None) == 0
        out = capsys.readouterr().out
        assert "failed book reads" in out
        assert "1 delta" in out

    def test_a_run_of_nothing_but_failed_reads_says_so(self, tmp_path, capsys):
        # Every delta dropped is not the same as no telemetry at all, and the
        # operator must not read one as the other.
        from scripts.queue_report import report

        db = tmp_path / "s.db"
        ensure_shadow_tables(db)
        _mark(db, ts=0.0, size=600.0, traded=0.0, decay=None)
        _mark(db, ts=60.0, size=0.0, traded=0.0, decay=600.0)

        assert report(db, None) == 1
        out = capsys.readouterr().out
        assert "failed book reads" in out

    def test_a_negative_window_exits_two_with_the_reason(self, tmp_path, capsys):
        from scripts.queue_report import main

        db = tmp_path / "s.db"
        ensure_shadow_tables(db)
        assert main(["--db", str(db), "--minutes", "-5"]) == 2
        assert "must not be negative" in capsys.readouterr().out

    def test_main_reaches_the_report_and_returns_its_code(self, tmp_path):
        from scripts.queue_report import main

        db = tmp_path / "s.db"
        ensure_shadow_tables(db)
        _mark(db, ts=0.0, size=600.0, traded=0.0, decay=None)
        _mark(db, ts=60.0, size=600.0, traded=300.0, decay=0.0)
        assert main(["--db", str(db)]) == 0

    def test_main_defaults_to_the_repo_shadow_store(self, tmp_path, monkeypatch):
        # The bare operator command must resolve the canonical store even when
        # launched from another working directory.
        from core_brain.runtime_paths import LIVE_ROOT
        from scripts import queue_report

        captured = []
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            queue_report, "report",
            lambda db_path, minutes: captured.append((db_path, minutes)) or 0,
        )

        assert queue_report.main([]) == 0
        assert captured == [(LIVE_ROOT / "data" / "shadow.db", None)]
        assert not (tmp_path / "data" / "shadow.db").exists()
