"""Tests for core_brain.markets timestamp parsing."""
from __future__ import annotations

import datetime as dt

from core_brain.markets import _iso_to_unix, _iso_to_unix_end

# 2026-10-15T00:00:00Z -- the shape CLOB returns for a date-only end date.
MIDNIGHT_UTC = "2026-10-15T00:00:00Z"
MIDNIGHT_EPOCH = dt.datetime(2026, 10, 15, tzinfo=dt.timezone.utc).timestamp()

# A date with no time component at all, the Gamma `endDateIso` shape.
DATE_ONLY = "2026-10-15"

# Gamma's `endDate` for the same market: a real time-of-day, 22:00 UTC.
WITH_TIME = "2026-10-15T22:00:00Z"
WITH_TIME_EPOCH = dt.datetime(2026, 10, 15, 22, tzinfo=dt.timezone.utc).timestamp()


def test_iso_to_unix_end_rolls_a_midnight_resolution_to_end_of_day():
    # Arrange / Act
    ts = _iso_to_unix_end(MIDNIGHT_UTC)

    # Assert -- midnight means "that whole day", so the market is quotable
    # until the day ends, not refused at its first instant.
    assert ts == MIDNIGHT_EPOCH + 86400.0


def test_iso_to_unix_end_rolls_a_date_only_resolution_to_end_of_day():
    # Arrange / Act
    ts = _iso_to_unix_end(DATE_ONLY)

    # Assert
    assert ts == MIDNIGHT_EPOCH + 86400.0


def test_iso_to_unix_end_leaves_a_real_timestamp_untouched():
    # Arrange / Act
    ts = _iso_to_unix_end(WITH_TIME)

    # Assert -- only whole-day boundaries roll; a stated time is authoritative.
    assert ts == WITH_TIME_EPOCH


def test_iso_to_unix_end_propagates_an_unparseable_value():
    # Arrange / Act / Assert
    assert _iso_to_unix_end("not-a-date") is None
    assert _iso_to_unix_end("") is None
    assert _iso_to_unix_end(None) is None


def test_iso_to_unix_end_keeps_a_midnight_market_quotable_today():
    # Arrange -- the three markets the quoter refused on 2026-10-15.
    now = dt.datetime(2026, 10, 15, 17, 0, tzinfo=dt.timezone.utc).timestamp()

    # Act
    remaining = _iso_to_unix_end(MIDNIGHT_UTC) - now

    # Assert -- before the fix this was negative and the row was refused.
    assert remaining > 0


def test_iso_to_unix_treats_a_naive_timestamp_as_utc():
    # Arrange -- a naive value read as local time shifts by the host offset.
    naive = "2026-10-15T12:00:00"
    expected = dt.datetime(2026, 10, 15, 12, tzinfo=dt.timezone.utc).timestamp()

    # Act / Assert
    assert _iso_to_unix(naive) == expected
