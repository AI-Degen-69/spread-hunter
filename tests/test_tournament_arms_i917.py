"""Unit test suite for Issue #917 multi-strategy adverse-selection defense tournament."""
from __future__ import annotations

from dataclasses import replace

import pytest

from core_brain import rehearsal
from core_brain.config import MakerConfig
from core_brain.market_feed import GraduatedMarket, GraduatedSnapshot
from core_brain.trader_loop import _market_specs


def _sample_graduated_market(
    cid: str,
    title: str,
    slug: str,
    *,
    event_live: bool = False,
    sports_market_type: str = "",
    event_title: str = "",
) -> GraduatedMarket:
    return GraduatedMarket(
        cid=cid,
        min_size=5.0,
        tick=0.01,
        max_spread=0.04,
        days_to_resolve=5.0,
        source="spread",
        daily=0.0,
        shares=20,
        title=title,
        slug=slug,
        sports_market_type=sports_market_type,
        event_score="",
        event_period="",
        question=title,
        event_live=event_live,
        event_ended=False,
        series_ts=None,
        range_cents=4.0,
        velocity_measured_at=1700000000.0,
        event_title=event_title or title,
    )


def test_from_env_parses_stranded_completion_and_max_wait(monkeypatch):
    monkeypatch.setenv("BOT_RISK_STRANDED_COMPLETION_ON", "1")
    monkeypatch.setenv("BOT_RISK_STRANDED_MAX_WAIT_SEC", "10.0")
    cfg = MakerConfig.from_env()
    assert cfg.bot_risk_stranded_completion_on is True
    assert cfg.bot_risk_stranded_max_wait_sec == pytest.approx(10.0)

    monkeypatch.setenv("BOT_RISK_STRANDED_COMPLETION_ON", "0")
    monkeypatch.setenv("BOT_RISK_STRANDED_MAX_WAIT_SEC", "45.0")
    cfg2 = MakerConfig.from_env()
    assert cfg2.bot_risk_stranded_completion_on is False
    assert cfg2.bot_risk_stranded_max_wait_sec == pytest.approx(45.0)


def test_from_env_parses_pairs_exit_window_and_hold_max_alias(monkeypatch):
    monkeypatch.setenv("BOT_RISK_PAIRS_EXIT_WINDOW_SEC", "60.0")
    cfg = MakerConfig.from_env()
    assert cfg.bot_risk_pairs_exit_window_sec == pytest.approx(60.0)

    monkeypatch.delenv("BOT_RISK_PAIRS_EXIT_WINDOW_SEC", raising=False)
    monkeypatch.setenv("BOT_POS_HOLD_MAX_SEC", "90.0")
    cfg2 = MakerConfig.from_env()
    assert cfg2.bot_risk_pairs_exit_window_sec == pytest.approx(90.0)


def test_from_env_parses_book_max_spread_usd_and_widens_window(monkeypatch):
    monkeypatch.delenv("BOT_QUOTE_WIDE_BOOK_TRIAL", raising=False)
    monkeypatch.setenv("BOT_QUOTE_BOOK_MAX_SPREAD_FROM_MID", "8")  # 0.08
    monkeypatch.setenv("BOT_QUOTE_BOOK_MAX_SPREAD_USD", "0.12")
    cfg = MakerConfig.from_env()
    assert cfg.bot_quote_book_max_spread_usd == pytest.approx(0.12)
    assert cfg.bot_quote_book_max_spread_from_mid == pytest.approx(0.12)


def test_market_specs_excludes_sports_and_live_when_env_set(monkeypatch):
    macro_gm = _sample_graduated_market(
        "0xmacro1",
        "Will the Fed cut interest rates in December?",
        "fed-rate-cut-december",
        event_live=False,
    )
    sports_type_gm = _sample_graduated_market(
        "0xsports1",
        "Lakers vs Celtics Winner",
        "nba-lal-bos-2026-11-01",
        event_live=False,
        sports_market_type="moneyline",
    )
    live_event_gm = replace(
        macro_gm,
        cid="0xlive1",
        title="Live Political Debate Mention",
        slug="debate-mention",
        event_live=True,
    )
    snapshot = GraduatedSnapshot(
        rows=[macro_gm, sports_type_gm, live_event_gm],
        published_mtime_ns=123456789,
        condition_ids=frozenset({"0xmacro1", "0xsports1", "0xlive1"}),
    )
    monkeypatch.setattr(
        "core_brain.market_feed.load_graduated_snapshot",
        lambda path=None: snapshot,
    )

    monkeypatch.delenv("BOT_MARKET_EXCLUDE_SPORTS_ON", raising=False)
    monkeypatch.delenv("FILTER_EXCLUDE_SPORTS", raising=False)
    all_specs = _market_specs()
    assert [s["cid"] for s in all_specs] == ["0xmacro1", "0xsports1", "0xlive1"]

    monkeypatch.setenv("BOT_MARKET_EXCLUDE_SPORTS_ON", "1")
    filtered_specs = _market_specs()
    assert [s["cid"] for s in filtered_specs] == ["0xmacro1"]
