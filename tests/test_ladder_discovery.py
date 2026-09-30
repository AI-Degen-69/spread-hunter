"""Series discovery for the ladder path (issue #325): T1 RED first.

Lists upcoming BTC/ETH 5+15-min series markets inside the OPEN window,
ordered by start. `fetch_live_market` is untouched; this is a new function.
No network: the venue session is stubbed per series slug.
"""
from __future__ import annotations

import core_brain.markets as markets

NOW = 2_000_000.0


def _row(condition_id, start_off, end_off, slug="m1", tokens='["11", "22"]'):
    import time
    base = NOW
    return {
        "conditionId": condition_id,
        "slug": slug,
        "clobTokenIds": tokens,
        "eventStartTime": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(base + start_off)),
        "endDate": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(base + end_off)),
        "orderPriceMinTickSize": "0.01",
    }


class _Resp:
    def __init__(self, events):
        self._events = events

    def raise_for_status(self):
        return None

    def json(self):
        return self._events


def _session_for(mapping):
    def get(url, params=None, timeout=None):
        slug = (params or {}).get("series_slug")
        return _Resp(mapping.get(slug, []))
    return get


def test_discovers_upcoming_markets_ordered_by_start(monkeypatch):
    """Two series, three markets in-window: ordered oldest-open first."""
    mapping = {
        "btc-up-or-down-5m": [{"markets": [
            _row("c-btc-late", -10, 290, slug="btc-late"),
            _row("c-btc-early", -20, 280, slug="btc-early"),
        ]}],
        "eth-up-or-down-5m": [{"markets": [
            _row("c-eth", -15, 285, slug="eth"),
        ]}],
    }
    monkeypatch.setattr(markets._SESSION, "get", _session_for(mapping))
    found = markets.discover_ladder_series(
        "https://gamma.example",
        ["btc-up-or-down-5m", "eth-up-or-down-5m"],
        open_window_sec=30.0, now=NOW)
    assert [m.condition_id for m in found] == ["c-btc-early", "c-eth", "c-btc-late"]


def test_ignores_outside_window_and_malformed_rows(monkeypatch):
    """Already-aged markets, future listings, and garbage rows are skipped."""
    mapping = {
        "btc-up-or-down-5m": [{"markets": [
            _row("c-old", -120, 180, slug="old"),       # window passed
            _row("c-future", 60, 360, slug="future"),   # not open yet
            {"conditionId": None, "slug": "junk"},      # malformed
            _row("c-good", -5, 295, slug="good"),
        ]}],
    }
    monkeypatch.setattr(markets._SESSION, "get", _session_for(mapping))
    found = markets.discover_ladder_series(
        "https://gamma.example", ["btc-up-or-down-5m"],
        open_window_sec=30.0, now=NOW)
    assert [m.condition_id for m in found] == ["c-good"]


def test_empty_universe_returns_empty(monkeypatch):
    monkeypatch.setattr(markets._SESSION, "get", _session_for({}))
    assert markets.discover_ladder_series(
        "https://gamma.example", ["btc-up-or-down-5m"],
        open_window_sec=30.0, now=NOW) == []
