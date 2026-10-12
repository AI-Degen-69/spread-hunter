"""The rotation cadence ceiling: 1s maximum, sub-1s deliberately allowed.

Operator directive 2026-10-12: a 5-second polling interval is retired. In
five seconds a live book can cross the pair cap, fill both legs adversely,
or decide outright -- the 2026-10-12 fox1-jdg series moved 0.90 -> 0.75 ->
0.885 -> 0.999 inside a ten-minute window -- so a 5s rotation quotes a
market that no longer exists by the time the order rests.

The ceiling lives in one place (`core_brain.config.MAX_ROTATION_INTERVAL_SEC`)
and is enforced at both CLI boundaries (`trader_loop._parse_args` and
`shadow_run._parse_args`), so the loop body never sees a cadence above 1s.
A request BELOW the ceiling is kept untouched: a faster loop is the
direction the directive points, and the option to circle back to an exact
sub-1s number is reserved by design.
"""
from __future__ import annotations

import logging

from core_brain.config import MAX_ROTATION_INTERVAL_SEC, cap_rotation_interval
from core_brain import shadow_run, trader_loop


class TestCapRotationInterval:
    def test_the_shipped_ceiling_is_one_second(self):
        assert MAX_ROTATION_INTERVAL_SEC == 1.0

    def test_a_value_below_the_ceiling_passes_through_untouched(self):
        assert cap_rotation_interval(0.5) == 0.5
        assert cap_rotation_interval(0.25) == 0.25
        assert cap_rotation_interval(0.01) == 0.01

    def test_the_ceiling_itself_passes_through(self):
        assert cap_rotation_interval(1.0) == 1.0

    def test_a_value_above_the_ceiling_is_clamped(self):
        assert cap_rotation_interval(5.0) == 1.0
        assert cap_rotation_interval(60.0) == 1.0

    def test_clamping_logs_a_warning_naming_both_numbers(self):
        with self._capture_warnings() as records:
            cap_rotation_interval(5.0)
        assert any(
            "5.0" in r.getMessage() and "1.0" in r.getMessage()
            for r in records), "a clamped interval must name the request and the ceiling"

    def test_a_sub_second_request_does_not_log(self):
        with self._capture_warnings() as records:
            cap_rotation_interval(0.5)
        assert records == []

    def test_non_finite_intervals_fall_back_to_the_ceiling(self):
        # A nan interval makes every `time.time() - t0 > interval` comparison
        # False (nan compares False to everything), so the loop would spin
        # with no sleep at all -- a hot loop in a live trading bot is the
        # exact failure mode the ceiling exists to prevent.
        assert cap_rotation_interval(float("nan")) == 1.0
        assert cap_rotation_interval(float("inf")) == 1.0

    def test_non_positive_intervals_fall_back_to_the_ceiling(self):
        # `sleep(max(0.0, -5))` is 0 -- same hot loop, different door.
        assert cap_rotation_interval(-5.0) == 1.0
        assert cap_rotation_interval(0.0) == 1.0

    def test_invalid_intervals_log_a_warning(self):
        with self._capture_warnings() as records:
            cap_rotation_interval(float("nan"))
        assert any("invalid rotation interval" in r.getMessage()
                   for r in records)

    class _capture_warnings:
        def __init__(self):
            self._records = []

        def __enter__(self):
            self._handler = logging.Handler()
            self._handler.emit = self._records.append
            logger = logging.getLogger("core_brain.config")
            logger.addHandler(self._handler)
            return self._records

        def __exit__(self, *exc):
            logging.getLogger("core_brain.config").removeHandler(self._handler)
            return False


class TestTraderLoopIntervalContract:
    def test_the_default_interval_is_the_ceiling(self):
        a = trader_loop._parse_args([])
        assert a.interval == 1.0

    def test_a_five_second_request_is_clamped_to_the_ceiling(self):
        a = trader_loop._parse_args(["--interval", "5"])
        assert a.interval == 1.0

    def test_a_sub_second_request_is_kept(self):
        a = trader_loop._parse_args(["--interval", "0.5"])
        assert a.interval == 0.5


class TestShadowRunIntervalContract:
    def test_the_default_interval_is_the_ceiling(self):
        a = shadow_run._parse_args(["--db", "data/x_shadow_test.db"])
        assert a.interval == 1.0

    def test_a_five_second_request_is_clamped_to_the_ceiling(self):
        a = shadow_run._parse_args(["--db", "data/x_shadow_test.db",
                                    "--interval", "5"])
        assert a.interval == 1.0

    def test_a_sub_second_request_is_kept(self):
        a = shadow_run._parse_args(["--db", "data/x_shadow_test.db",
                                    "--interval", "0.25"])
        assert a.interval == 0.25
