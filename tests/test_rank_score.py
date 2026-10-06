"""Rank-score: live competitive markets outrank flat long-dated ones (#392)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from scripts.filter_markets import rank_score

NOW = 1_788_000_000.0  # fixed clock, mirrors test_velocity_gate.py


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def _live(**over):
    row = {
        "return_pct_day": 0.9,
        "movement_usd": 35000.0,
        "trade_count": 200,
        "range_cents": 8.0,
        "days_to_resolve": 0.2,
        "gameStartTime": _iso(NOW - 3600.0),
    }
    row.update(over)
    return row


def _flat(**over):
    row = {
        "return_pct_day": 0.9,
        "movement_usd": 2800.0,
        "trade_count": 24,
        "range_cents": 0.5,
        "days_to_resolve": 28.0,
    }
    row.update(over)
    return row


def test_live_outranks_flat_at_equal_return():
    assert rank_score(_live(), now=NOW) > rank_score(_flat(), now=NOW)


def test_missing_fields_rank_as_today():
    assert rank_score({"return_pct_day": 0.9}, now=NOW) == 0.9


def test_unstarted_event_gets_no_live_boost():
    row = _live(gameStartTime=_iso(NOW + 7200.0))
    assert rank_score(row, now=NOW) == 0.9


def test_flat_long_dated_scores_below_bare_return():
    assert rank_score(_flat(), now=NOW) < 0.9


def test_unmeasured_range_or_horizon_escapes_penalty():
    assert rank_score({"return_pct_day": 0.9, "range_cents": 0.5},
                      now=NOW) == 0.9
    assert rank_score({"return_pct_day": 0.9, "days_to_resolve": 28.0},
                      now=NOW) == 0.9
