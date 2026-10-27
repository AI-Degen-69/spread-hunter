"""Tests for core_brain.markets timestamp parsing."""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys

import pytest

from core_brain import markets
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


def test_iso_to_unix_end_preserves_an_offset_equivalent_timed_close():
    # Arrange -- 01:00+01:00 IS UTC midnight, but the stated wall clock is a
    # real close time, so it must not inherit the date-only extension.
    ts = _iso_to_unix_end("2026-10-15T01:00:00+01:00")

    # Assert
    assert ts == MIDNIGHT_EPOCH


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


# --- both production conversions -----------------------------------------


def _market_row(end_iso: str) -> dict:
    """A Gamma-shaped market row, as `_parse_market_row` receives it."""
    return {
        "conditionId": "0x" + "ab" * 32,
        "clobTokenIds": json.dumps(["111", "222"]),
        "eventStartTime": "2026-10-15T16:00:00Z",
        "endDate": end_iso,
        "orderPriceMinTickSize": 0.01,
        "negRisk": False,
    }


def test_market_row_extends_a_midnight_end_date():
    # Arrange / Act
    lm = markets._parse_market_row(_market_row(MIDNIGHT_UTC))

    # Assert -- the row path must roll too, not just the helper. This is the
    # test that fails if a production caller reverts to `_iso_to_unix`.
    assert lm is not None
    assert lm.end_ts == MIDNIGHT_EPOCH + 86400.0


def test_market_row_keeps_a_timed_end_date():
    # Arrange / Act
    lm = markets._parse_market_row(_market_row(WITH_TIME))

    # Assert
    assert lm is not None
    assert lm.end_ts == WITH_TIME_EPOCH


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeSession:
    """Stands in for the CLOB session `fetch_pinned_market` uses."""

    def __init__(self, payload: dict):
        self._payload = payload

    def get(self, url, **kwargs):
        return _FakeResponse(self._payload)


def test_pinned_market_extends_a_midnight_end_date(monkeypatch):
    # Arrange -- a CLOB payload whose end_date_iso is the date-only encoding.
    payload = {
        "tokens": [{"token_id": "111"}, {"token_id": "222"}],
        "end_date_iso": MIDNIGHT_UTC,
        "closed": False,
        "accepting_orders": True,
        "market_slug": "cs2-lgc-furia-2026-10-15",
        "minimum_tick_size": 0.01,
        "neg_risk": False,
    }
    monkeypatch.setattr(markets, "_SESSION", _FakeSession(payload))

    # Act
    lm = markets.fetch_pinned_market("0x" + "cd" * 32)

    # Assert -- the pinned path must roll too, not just the helper.
    assert lm is not None
    assert lm.end_ts == MIDNIGHT_EPOCH + 86400.0


# --- naive timestamps are UTC, not local ---------------------------------

# A POSIX TZ string understood by both glibc and the Windows CRT, so the child
# process below is genuinely non-UTC on every platform CI runs on. Without this
# the assertion passes on a UTC host even with the local-time bug present.
# Mirrors the sibling suite in tests/scoring/test_markets.py.
NON_UTC_TZ = "EST5EDT,M3.2.0,M11.1.0"

# 2026-10-15T12:00:00 with no offset. Read as US/Eastern it is 4h larger.
NAIVE = "2026-10-15T12:00:00"
NAIVE_UTC_EPOCH = dt.datetime(2026, 10, 15, 12, tzinfo=dt.timezone.utc).timestamp()

_CHILD = """
import json, sys
from core_brain.markets import _iso_to_unix
print(json.dumps(_iso_to_unix(sys.argv[1])))
"""


def test_iso_to_unix_treats_a_naive_timestamp_as_utc_on_a_non_utc_host():
    # Arrange -- run in a child pinned to a non-UTC zone, so a local read
    # diverges from the UTC answer by the host offset and the test can fail.
    env = {**os.environ, "TZ": NON_UTC_TZ}
    result = subprocess.run(
        [sys.executable, "-c", _CHILD, NAIVE],
        capture_output=True, text=True, check=True, env=env, cwd=os.getcwd(),
    )

    # Act / Assert
    assert json.loads(result.stdout) == NAIVE_UTC_EPOCH


# --- venue minimum order size (#840) --------------------------------------

def _pinned_payload(**kw):
    payload = {
        "tokens": [{"token_id": "111"}, {"token_id": "222"}],
        "end_date_iso": MIDNIGHT_UTC,
        "closed": False,
        "accepting_orders": True,
        "market_slug": "cs2-lgc-furia-2026-10-15",
        "minimum_tick_size": 0.01,
        "neg_risk": False,
    }
    payload.update(kw)
    return payload


def test_pinned_market_keeps_min_size_when_present(monkeypatch):
    # Arrange -- the CLOB `/markets` response carries `minimum_order_size`.
    monkeypatch.setattr(markets, "_SESSION", _FakeSession(_pinned_payload(minimum_order_size=5.0)))

    # Act
    lm = markets.fetch_pinned_market("0x" + "cd" * 32)

    # Assert
    assert lm is not None
    assert lm.venue_min_size == pytest.approx(5.0)


def test_pinned_market_min_size_is_none_when_missing(monkeypatch):
    # Arrange -- no `minimum_order_size` key: unknown, not zero, not rewards.
    monkeypatch.setattr(markets, "_SESSION", _FakeSession(_pinned_payload()))

    # Act
    lm = markets.fetch_pinned_market("0x" + "cd" * 32)

    # Assert
    assert lm is not None
    assert lm.venue_min_size is None
