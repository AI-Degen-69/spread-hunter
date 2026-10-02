"""Live-books ladder trial runner (issues #331/#333): T2 RED first.

The runner wires discovery + the merged ladder path + live-book seams and
reports in the rehearsal schema. Fully offline: discovery, books and the
tape are all stubbed; the store is a tmp_path file.
"""
from __future__ import annotations

import json
import time

import pytest

from core_brain.config import MakerConfig
from core_brain.markets import LiveMarket
from scripts.ladder_live_books_trial import (
    LiveTrialRefused,
    refuse_db,
    run_trial,
)

NOW = time.time()


def _market(cid="0x-live-trial", start_off=-5.0):
    return LiveMarket(condition_id=cid, market_slug="live-mkt",
                      up_token="tok-live-up", down_token="tok-live-dn",
                      start_ts=NOW + start_off, end_ts=NOW + 295.0,
                      tick_size=0.01, neg_risk=False)


def _books(token_id):
    return {"token_id": token_id,
            "best_bid": 0.47, "best_ask": 0.50,
            "bids": {0.47: 100.0}, "asks": {0.50: 100.0}}


def _rotations(n):
    calls = []

    def sleep_fn(seconds):
        calls.append(seconds)
        if len(calls) >= n:
            raise KeyboardInterrupt()
    return sleep_fn


def _trial(tmp_path, monkeypatch, *, markets, traded, rotations=4,
           out_name="live.json", budget_usd=5.0):
    import core_brain.markets as markets_mod
    import core_brain.shadow_run as shadow_mod
    monkeypatch.setattr(markets_mod, "recent_trades",
                        lambda cid, seen: traded(cid, seen))
    monkeypatch.setattr(shadow_mod, "_default_fetch_books",
                        lambda: (lambda host, token: _books(token)))
    db = tmp_path / "live_trial.db"
    out = tmp_path / out_name
    report = run_trial(
        series_slugs=["btc-up-or-down-5m"], gamma_host="https://x.invalid",
        db_path=db, out_path=out, minutes=5.0, budget_usd=budget_usd,
        open_window_sec=10_000.0, max_markets=10, interval=0.01,
        run_id="ladder-live-test",
        discover_fn=lambda *a, **k: list(markets),
        cfg=MakerConfig(ladder_mode=False, bankroll_usd=100.0),
        sleep_fn=_rotations(rotations))
    return report, json.loads(out.read_text())


def test_refuses_production_registry(tmp_path):
    with pytest.raises(LiveTrialRefused):
        refuse_db(tmp_path / "orders.db")
    with pytest.raises(LiveTrialRefused):
        run_trial(series_slugs=["s"], gamma_host="https://x.invalid",
                  db_path=tmp_path / "orders.db", out_path=None,
                  minutes=0.01, budget_usd=5.0, open_window_sec=30.0,
                  max_markets=10, discover_fn=lambda *a, **k: [])


def test_offline_trial_reports_placements_and_clean_conservation(
        tmp_path, monkeypatch):
    """Rungs rest under one pair; nothing filled, nothing owed."""
    report, from_disk = _trial(tmp_path, monkeypatch, markets=[_market()],
                               traded=lambda cid, seen: {})
    assert report["issue"] == 333
    assert report["source"] == "live-books"
    assert report["config"]["ladder_budget_usd"] == pytest.approx(5.0)
    assert report["placements"]["orders"] > 0
    assert report["placements"]["one_pair_id_per_market"] is True
    assert report["fills"]["shares"] == pytest.approx(0.0)
    assert report["conservation"]["orphan_fills"] == 0
    assert report["conservation"]["overfilled_orders"] == 0
    assert report["conservation"]["resting_shares"] == pytest.approx(0.0)
    assert from_disk["issue"] == 333


def test_fills_conserve_oldest_first_with_resting(tmp_path, monkeypatch):
    """Tape volume at the oldest rung: filled == accounted + resting."""
    vol = {"tok-live-up": {0.47: 110.0}}
    report, _ = _trial(tmp_path, monkeypatch, markets=[_market()],
                       traded=lambda cid, seen: dict(vol), rotations=6)
    cons = report["conservation"]
    assert report["rungs"] == [0.46, 0.47, 0.5]
    assert report["fills"]["shares"] > 0
    assert report["fills"]["oldest_first"] is True
    assert cons["orphan_fills"] == 0
    assert cons["overfilled_orders"] == 0
    assert cons["filled_shares"] == pytest.approx(
        cons["accounted_shares"] + cons["resting_shares"])


def test_second_run_on_same_store_is_refused(tmp_path, monkeypatch):
    """A rerun would read the first trial's fills/closes as its own."""
    _trial(tmp_path, monkeypatch, markets=[_market()],
           traded=lambda cid, seen: {})
    with pytest.raises(LiveTrialRefused):
        run_trial(
            series_slugs=["btc-up-or-down-5m"],
            gamma_host="https://x.invalid",
            db_path=tmp_path / "live_trial.db",
            out_path=tmp_path / "live_rerun.json",
            minutes=0.01, budget_usd=5.0, open_window_sec=30.0,
            max_markets=10, interval=0.01, run_id="ladder-live-rerun",
            discover_fn=lambda *a, **k: [],
            cfg=MakerConfig(ladder_mode=False, bankroll_usd=100.0),
            sleep_fn=_rotations(1))


def test_empty_window_still_writes_report(tmp_path, monkeypatch):
    import core_brain.markets as markets_mod
    import core_brain.shadow_run as shadow_mod
    monkeypatch.setattr(markets_mod, "recent_trades", lambda cid, seen: {})
    monkeypatch.setattr(shadow_mod, "_default_fetch_books",
                        lambda: (lambda host, token: _books(token)))
    db = tmp_path / "live_empty.db"
    out = tmp_path / "live_empty.json"
    report = run_trial(
        series_slugs=["btc-up-or-down-5m"], gamma_host="https://x.invalid",
        db_path=db, out_path=out, minutes=0.01, budget_usd=5.0,
        open_window_sec=30.0, max_markets=10, interval=0.01,
        run_id="ladder-live-empty",
        discover_fn=lambda *a, **k: [],
        cfg=MakerConfig(ladder_mode=False, bankroll_usd=100.0),
        sleep_fn=_rotations(2))
    assert report["markets"] == 0
    assert json.loads(out.read_text())["markets"] == 0


def test_issue_tag_defaults_to_333(tmp_path, monkeypatch):
    """#333 report must not misattribute itself to #331."""
    report, from_disk = _trial(tmp_path, monkeypatch, markets=[_market()],
                               traded=lambda cid, seen: {})
    assert report["issue"] == 333
    assert from_disk["issue"] == 333


def test_issue_tag_threaded_to_empty_window_report(tmp_path, monkeypatch):
    """Empty-session branch carries the same tag (custom value proves threading)."""
    import core_brain.markets as markets_mod
    import core_brain.shadow_run as shadow_mod
    monkeypatch.setattr(markets_mod, "recent_trades", lambda cid, seen: {})
    monkeypatch.setattr(shadow_mod, "_default_fetch_books",
                        lambda: (lambda host, token: _books(token)))
    db = tmp_path / "live_empty_tag.db"
    out = tmp_path / "live_empty_tag.json"
    report = run_trial(
        series_slugs=["btc-up-or-down-5m"], gamma_host="https://x.invalid",
        db_path=db, out_path=out, minutes=0.01, budget_usd=5.0,
        open_window_sec=30.0, max_markets=10, interval=0.01,
        run_id="ladder-live-empty-tag", issue=999,
        discover_fn=lambda *a, **k: [],
        cfg=MakerConfig(ladder_mode=False, bankroll_usd=100.0),
        sleep_fn=_rotations(2))
    assert report["issue"] == 999
    assert json.loads(out.read_text())["issue"] == 999

