"""Tests for scoring.markets timestamp parsing."""
from __future__ import annotations

import json
import os
import subprocess
import sys

from scoring import markets
from scoring.markets import _iso_to_unix, _parse_market

# A POSIX TZ string understood by both glibc and the Windows CRT, so the child
# process below is genuinely non-UTC on every platform CI runs on. Without this
# the assertions pass on a UTC host even with the naive-is-local bug present.
NON_UTC_TZ = "EST5EDT,M3.2.0,M11.1.0"

# 2026-08-30T12:34:56 UTC. Read as US/Eastern instead, it would be 4h larger.
INSTANT = "2026-08-30T12:34:56"
INSTANT_EPOCH = 1788093296.0

_CHILD = """
import json, sys
from scoring.markets import _iso_to_unix
print(json.dumps([_iso_to_unix(s) for s in sys.argv[1:]]))
"""


def _parse_under_non_utc_tz(*raw: str) -> list[float]:
    """Run _iso_to_unix in a child process pinned to a non-UTC timezone."""
    env = {**os.environ, "TZ": NON_UTC_TZ}
    result = subprocess.run(
        [sys.executable, "-c", _CHILD, *raw],
        capture_output=True,
        text=True,
        check=True,
        env=env,
        cwd=os.getcwd(),
    )
    return json.loads(result.stdout)


def test_iso_to_unix_treats_naive_as_utc_on_a_non_utc_host():
    # Arrange
    raw_naive = INSTANT
    raw_offset = f"{INSTANT}+00:00"
    raw_z = f"{INSTANT}Z"

    # Act
    ts_naive, ts_offset, ts_z = _parse_under_non_utc_tz(raw_naive, raw_offset, raw_z)

    # Assert
    assert ts_naive == INSTANT_EPOCH
    assert ts_offset == INSTANT_EPOCH
    assert ts_z == INSTANT_EPOCH
    assert ts_naive == ts_offset == ts_z


def test_iso_to_unix_epoch_zero_on_a_non_utc_host():
    # Arrange
    raw_naive = "1970-01-01T00:00:00"
    raw_z = "1970-01-01T00:00:00Z"

    # Act
    ts_naive, ts_z = _parse_under_non_utc_tz(raw_naive, raw_z)

    # Assert
    assert ts_naive == 0.0
    assert ts_z == 0.0


def test_iso_to_unix_matches_the_child_process_on_this_host():
    # Arrange / Act
    in_process = _iso_to_unix(f"{INSTANT}Z")

    # Assert — the offset-bearing form is host-independent, so the in-process
    # call and the pinned-TZ child must agree.
    assert in_process == INSTANT_EPOCH


# --- malformed-row tolerance (#101) ---------------------------------------

def _valid_row(condition_id: str = "0xabc") -> dict:
    return {
        "clobTokenIds": json.dumps(["111", "222"]),
        "conditionId": condition_id,
        "slug": "btc-up-or-down",
        "eventStartTime": "2026-08-30T12:00:00Z",
        "endDate": "2026-08-30T12:05:00Z",
        "orderPriceMinTickSize": "0.01",
    }


def test_parse_market_skips_a_row_with_non_json_token_ids():
    # Arrange
    row = {**_valid_row(), "clobTokenIds": "not-json"}

    # Act
    parsed = _parse_market(row)

    # Assert
    assert parsed is None


def test_parse_market_skips_a_row_missing_condition_id():
    # Arrange
    row = {k: v for k, v in _valid_row().items() if k != "conditionId"}

    # Act
    parsed = _parse_market(row)

    # Assert
    assert parsed is None


def test_parse_market_skips_a_row_with_a_malformed_timestamp():
    # Arrange
    row = {**_valid_row(), "endDate": "not-a-date"}

    # Act
    parsed = _parse_market(row)

    # Assert
    assert parsed is None


def test_parse_market_skips_a_row_with_a_non_numeric_tick_size():
    # Arrange
    row = {**_valid_row(), "orderPriceMinTickSize": "wide"}

    # Act
    parsed = _parse_market(row)

    # Assert
    assert parsed is None


def test_iso_to_unix_returns_none_instead_of_raising_on_garbage():
    # Arrange / Act / Assert
    assert _iso_to_unix("not-a-date") is None
    assert _iso_to_unix("") is None
    assert _iso_to_unix(None) is None


def test_fetch_live_market_keeps_valid_rows_around_a_malformed_one(monkeypatch):
    # Arrange — one garbage row between two parseable ones, and a live window.
    now = 1788091200.0  # 2026-08-30T12:00:00Z
    good_early = {**_valid_row("0xearly"),
                  "eventStartTime": "2026-08-30T11:55:00Z",
                  "endDate": "2026-08-30T12:05:00Z"}
    garbage = {**_valid_row("0xbad"), "clobTokenIds": "{oops"}
    good_late = {**_valid_row("0xlate"),
                 "eventStartTime": "2026-08-30T12:00:00Z",
                 "endDate": "2026-08-30T12:05:00Z"}
    payload = [{"markets": [good_early, garbage]}, "not-an-event", {"markets": [good_late]}]

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    monkeypatch.setattr(markets._SESSION, "get", lambda *a, **k: _Resp())
    monkeypatch.setattr(markets.time, "time", lambda: now + 1.0)

    # Act
    live = markets.fetch_live_market("https://gamma.example", "btc-5m")

    # Assert — the malformed row is skipped, the newest valid one is returned.
    assert live is not None
    assert live.condition_id == "0xlate"


def test_parse_market_skips_a_row_whose_token_ids_are_not_usable():
    # Arrange — right length, wrong contents: `str(None)` would become "None"
    # and travel on as a token id.
    empty = {**_valid_row(), "clobTokenIds": json.dumps([None, "222"])}
    blank = {**_valid_row(), "clobTokenIds": json.dumps(["", "222"])}
    # `bool` is an `int` subclass, so True would otherwise become "True".
    boolean = {**_valid_row(), "clobTokenIds": [True, "222"]}

    # Act / Assert
    assert _parse_market(empty) is None
    assert _parse_market(blank) is None
    assert _parse_market(boolean) is None


# --- fetch_pinned_market zero rewards & closed checks (#431) --------------

def test_fetch_pinned_market_admits_zero_rewards(monkeypatch):
    payload = {
        "condition_id": "0xpinned",
        "accepting_orders": True,
        "closed": False,
        "tokens": [{"token_id": "tok_up"}, {"token_id": "tok_dn"}],
        "end_date_iso": "2026-09-01T00:00:00Z",
        "rewards": {"rates": []},
        "minimum_order_size": 5.0,
        "minimum_tick_size": 0.01,
    }

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    monkeypatch.setattr(markets._SESSION, "get", lambda *a, **k: _Resp())

    m = markets.fetch_pinned_market("0xpinned")
    assert m is not None
    assert m.condition_id == "0xpinned"
    assert m.up_token == "tok_up"
    assert m.down_token == "tok_dn"


def test_fetch_pinned_market_refuses_closed_market(monkeypatch):
    payload = {
        "condition_id": "0xclosed",
        "accepting_orders": True,
        "closed": True,
        "tokens": [{"token_id": "tok_up"}, {"token_id": "tok_dn"}],
        "end_date_iso": "2026-09-01T00:00:00Z",
        "rewards": {"rates": []},
    }

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    monkeypatch.setattr(markets._SESSION, "get", lambda *a, **k: _Resp())

    m = markets.fetch_pinned_market("0xclosed")
    assert m is None

