"""An empty market refresh is routine for some callers and a signal for others.

`markets_fn` returning `[]` means two different things depending on who asked.
The live stack's market filter returning nothing is worth a warning: the ranker
found nothing that cycle and the operator should know. The ladder trial asks
for markets inside a 30-second open window out of a 5-minute series, so it is
empty ~90% of the time by construction -- logging that as a warning filled the
trial's stderr with one line per rotation for four hours and read as a fault.
It cost a real diagnosis: two readings of that log concluded the market feed
had died while the feed was returning 168 upcoming markets.

Only the caller knows which contract it holds, so the caller declares it:
`markets_fn_empty_is_routine` on the seam, set by the ladder trial.
"""
from __future__ import annotations

import logging

import pytest

from core_brain import trader_loop
from core_brain.trader_loop import VenueSeam

#: `core_brain.trader_loop` logs under this name; setting the level on the
#: module path instead would let the INFO branch be filtered before it reaches
#: caplog, and the test would pass for the wrong reason on the WARNING branch.
_LOOP_LOGGER = "main_spread_hunter_loop"


class _Registry:
    def __init__(self):
        self.calls: list = []


def _seam(**kw) -> VenueSeam:
    base = dict(
        client=object(),
        registry=_Registry(),
        base_cfg=object(),
        reconcile_fn=lambda *a, **k: None,
        sweep_fn=lambda *a, **k: None,
        fetch_market=lambda *a, **k: None,
        fetch_books=lambda *a, **k: None,
        decide=lambda *a, **k: None,
        submit_fn=lambda *a, **k: None,
        cancel_fn=lambda *a, **k: None,
        inventory_fn=lambda *a, **k: None,
        open_orders_fn=lambda *a, **k: [],
        resting_order_ids_fn=lambda *a, **k: set(),
    )
    base.update(kw)
    return VenueSeam(**base)


def _run_empty(monkeypatch, seam) -> None:
    monkeypatch.setattr(trader_loop, "_cancel_dropped_markets",
                        lambda *a, **k: [])
    trader_loop.run(seam, once=True, live=False, markets_fn=lambda: [],
                    markets=[], sleep_fn=lambda _s: None)


def _levels(caplog) -> list[str]:
    return [r.levelname for r in caplog.records
            if "no markets" in r.getMessage()]


def test_an_empty_refresh_warns_by_default(caplog, monkeypatch):
    # The live stack's filter finding nothing is a real signal. This default
    # must not be relaxed to make the ladder trial quiet.
    caplog.set_level(logging.DEBUG, logger=_LOOP_LOGGER)
    _run_empty(monkeypatch, _seam())
    assert _levels(caplog) == ["WARNING"]


def test_a_routine_empty_refresh_is_info_not_a_warning(caplog, monkeypatch):
    caplog.set_level(logging.DEBUG, logger=_LOOP_LOGGER)
    _run_empty(monkeypatch, _seam(markets_fn_empty_is_routine=True))
    assert _levels(caplog) == ["INFO"]


def test_the_routine_message_says_the_empty_refresh_is_expected(caplog, monkeypatch):
    # "returned no markets" reads as a failure. The routine wording has to name
    # why emptiness is correct, or it is the same misleading line at a quieter
    # volume.
    caplog.set_level(logging.DEBUG, logger=_LOOP_LOGGER)
    _run_empty(monkeypatch, _seam(markets_fn_empty_is_routine=True))
    msg = [r.getMessage() for r in caplog.records if "no markets" in r.getMessage()][0]
    assert "expected" in msg.lower()
    assert "keeping the previous 0" in msg


def test_the_warning_default_still_names_what_it_kept(caplog, monkeypatch):
    caplog.set_level(logging.DEBUG, logger=_LOOP_LOGGER)
    _run_empty(monkeypatch, _seam())
    msg = [r.getMessage() for r in caplog.records if "no markets" in r.getMessage()][0]
    assert "keeping the previous 0" in msg


# ── the ladder trial is the caller that declares it routine ────────────────

def test_the_live_books_trial_declares_an_empty_refresh_routine():
    from scripts import ladder_live_books_trial as trial

    captured = {}
    import core_brain.shadow_run as shadow_run

    real = shadow_run.run_shadow

    def spy(**kwargs):
        captured.update(kwargs)
        raise SystemExit(0)  # the run is not what this test exercises

    shadow_run.run_shadow = spy
    try:
        with pytest.raises(SystemExit):
            trial.run_trial(
                series_slugs=["btc-up-or-down-5m"],
                gamma_host="https://example.invalid",
                db_path="data/NN_shadow_probe.db",
                out_path=None,
                minutes=1.0, budget_usd=5.0, open_window_sec=30.0,
                max_markets=10,
            )
    finally:
        shadow_run.run_shadow = real

    assert captured.get("markets_fn_empty_is_routine") is True