"""Tests for the guardrail watcher's detection logic.

Two live-run failure signatures, flagged the moment they happen:
1. REPEAT-EXIT -- the same pair_id exits twice inside the window (the
   repeat-sell loop's signature).
2. OVER-CAP PAIR -- a filled pair at/over `max_pair_cost` (a booked loss on
   an instrument that pays $1.00).
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from scripts.guardrail_watch import (
    GuardrailWatch, detect_over_cap_pairs, detect_repeat_exits,
)
from engine.order_registry import (
    FillRecord, OrderRecord, OrderRegistry, QuoteRecord,
)

COND = "0xcond-guard"
TOK_UP = "tok-guard-up"
TOK_DN = "tok-guard-dn"
BASE_MS = 1_000_000_000

import datetime

# 60s after the later test event (2026-08-21T08:00:00Z): in-window for the
# 900s default, and the 07:00 event falls outside it.
NOW = (datetime.datetime(2026, 8, 21, 8, 1, 0, tzinfo=datetime.timezone.utc)
       .timestamp())


def _exit_event(pair_id: str, ts: str) -> dict:
    return {"action": "pairs_exited", "ts": ts, "extra": {"pair_id": pair_id}}


# ---------------------------------------------------------------------------
# repeat-exit detection
# ---------------------------------------------------------------------------

def test_single_exit_is_not_flagged():
    assert detect_repeat_exits(
        [_exit_event("pair-1", "2026-08-21T08:00:00Z")],
        window_s=900.0, now=NOW) == []


def test_two_exits_same_pair_in_window_are_flagged():
    hits = detect_repeat_exits([
        _exit_event("pair-1", "2026-08-21T07:59:00Z"),
        _exit_event("pair-1", "2026-08-21T08:00:00Z"),
    ], window_s=900.0, now=NOW)
    assert len(hits) == 1
    assert hits[0]["pair_id"] == "pair-1"
    assert hits[0]["count"] == 2


def test_two_exits_different_pairs_are_not_flagged():
    assert detect_repeat_exits([
        _exit_event("pair-1", "2026-08-21T07:59:00Z"),
        _exit_event("pair-2", "2026-08-21T08:00:00Z"),
    ], window_s=900.0, now=NOW) == []


def test_two_exits_outside_window_are_not_flagged():
    assert detect_repeat_exits([
        _exit_event("pair-1", "2026-08-21T07:00:00Z"),
        _exit_event("pair-1", "2026-08-21T08:00:00Z"),
    ], window_s=900.0, now=NOW) == []


def test_three_exits_count_three():
    hits = detect_repeat_exits([
        _exit_event("pair-1", "2026-08-21T07:59:50Z"),
        _exit_event("pair-1", "2026-08-21T07:59:55Z"),
        _exit_event("pair-1", "2026-08-21T08:00:00Z"),
    ], window_s=900.0, now=NOW)
    assert hits[0]["count"] == 3


# ---------------------------------------------------------------------------
# over-cap pair detection
# ---------------------------------------------------------------------------

@pytest.fixture
def registry(tmp_path: Path) -> OrderRegistry:
    return OrderRegistry(db_path=tmp_path / "live.db")


def _seed_pair(registry: OrderRegistry, up_px: float, up_size: float,
               dn_px: float, dn_size: float) -> None:
    for tok, px, size, oid, ts in (
            (TOK_UP, up_px, up_size, "up", BASE_MS),
            (TOK_DN, dn_px, dn_size, "dn", BASE_MS + 1_000)):
        u = str(uuid.uuid4())
        registry.create_order(OrderRecord(
            id=u, order_id=f"ven-{oid}", condition_id=COND, token_id=tok,
            side="BUY", price=px, original_size=size, status="filled",
            posted_ts=ts, last_polled_ts=ts, pair_id=f"pair-{oid}",
            max_pair_cost_at_post=0.995,
        ))
        registry.record_fill(FillRecord(
            trade_id=f"tr-{oid}", order_uuid=u, size=size, price=px,
            venue_ts=ts,
        ))
    for side, tok, px in (("UP", TOK_UP, up_px), ("DOWN", TOK_DN, dn_px)):
        registry.log_quote(QuoteRecord(
            ts=BASE_MS / 1000.0, condition_id=COND, token_id=tok, side=side,
            price=px, size=max(up_size, dn_size),
        ))


def test_over_cap_pair_is_flagged(registry: OrderRegistry):
    """The $1.12 production shape: UP 0.92 + DOWN 0.20 = 1.12 >= 0.995."""
    _seed_pair(registry, 0.92, 6.0, 0.20, 6.0)
    hits = detect_over_cap_pairs(registry.db_path, cap=0.995)
    assert len(hits) == 1
    assert hits[0]["condition_id"] == COND
    assert hits[0]["pair_cost"] == pytest.approx(1.12)


def test_under_cap_pair_is_not_flagged(registry: OrderRegistry):
    _seed_pair(registry, 0.60, 6.0, 0.30, 6.0)   # 0.90 < 0.995
    assert detect_over_cap_pairs(registry.db_path, cap=0.995) == []


def test_single_leg_pair_is_not_flagged(registry: OrderRegistry):
    """pair_cost() returns 0.0 when only one leg is held -- not a pair yet."""
    _seed_pair(registry, 0.92, 6.0, 0.20, 0.0)
    assert detect_over_cap_pairs(registry.db_path, cap=0.995) == []


# ---------------------------------------------------------------------------
# watch dedupe: one alert per violation, re-armed on change
# ---------------------------------------------------------------------------

def test_watch_alerts_once_per_repeat_exit_growth(tmp_path, capsys):
    log = tmp_path / "alerts.log"
    ring = tmp_path / "ring.jsonl"
    w = GuardrailWatch(alerts_log=log, ring_path=ring,
                       db_path=tmp_path / "live.db", window_s=900.0)
    from engine.cycle_stream import emit
    emit(1, "settling", "pairs_exited", extra={"pair_id": "pair-1"},
         ring_path=ring)
    emit(2, "settling", "pairs_exited", extra={"pair_id": "pair-1"},
         ring_path=ring)
    w.check()
    w.check()   # same state again: no duplicate alert
    text = log.read_text(encoding="utf-8")
    assert text.count("REPEAT-EXIT") == 1
    emit(3, "settling", "pairs_exited", extra={"pair_id": "pair-1"},
         ring_path=ring)
    w.check()   # count grew 2 -> 3: alert again
    assert log.read_text(encoding="utf-8").count("REPEAT-EXIT") == 2


def test_repeat_exit_re_arms_after_window_ages_out(tmp_path):
    """A pair alerted at count 2 must re-alert on a FRESH 2-exit recurrence
    once its original events age out of the window.

    Without the re-arm prune, `_exit_alerted[pair] = 2` persists forever and
    the fresh recurrence (2 > 2 is False) is silently missed.
    """
    import json
    import time

    log = tmp_path / "alerts.log"
    ring = tmp_path / "ring.jsonl"
    w = GuardrailWatch(alerts_log=log, ring_path=ring,
                       db_path=tmp_path / "live.db", window_s=900.0)
    now = time.time()

    def _iso(epoch_s: float) -> str:
        return datetime.datetime.fromtimestamp(
            epoch_s, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def write_exits(*epochs: float) -> None:
        ring.write_text(
            "\n".join(json.dumps(
                {"ts": _iso(e), "action": "pairs_exited",
                 "extra": {"pair_id": "pair-1"}}) for e in epochs) + "\n",
            encoding="utf-8")

    # Two exits in-window: alert once; the second check must not duplicate.
    write_exits(now - 120, now - 60)
    w.check()
    w.check()
    assert log.read_text(encoding="utf-8").count("REPEAT-EXIT") == 1

    # The events age out of the window: the pair is pruned from _exit_alerted.
    write_exits(now - 2000, now - 1500)
    w.check()
    assert log.read_text(encoding="utf-8").count("REPEAT-EXIT") == 1

    # A FRESH two-exit recurrence re-alerts: count 2 > 0 (pruned), not 2 > 2.
    write_exits(now - 5, now - 3)
    w.check()
    assert log.read_text(encoding="utf-8").count("REPEAT-EXIT") == 2
