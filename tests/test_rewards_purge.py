"""The quote window is the engine's, never the feed row's.

A feed row used to carry `max_spread` (the venue's rewardsMaxSpread) and
`trader_loop.spec_to_cfg` fed it into the per-market config as
`max_spread_from_mid` -- so a Fed market quoted with a 2.5c window and a
sports market 4.5c, while the engine said 15c. The rewards purge (#594) removed
that override; these tests pin the removal so the venue's reward field can
never throttle the window again.
"""
from __future__ import annotations

from dataclasses import replace

from core_brain.config import MakerConfig
from core_brain.trader_loop import _market_cfg as spec_to_cfg
from scoring.config import load as load_scoring


def _row(**extra):
    row = {
        "min_size": 5,
        "shares": 10,
        "tick": 0.01,
        "title": "Test Market",
        "range_cents": 5.0,
        "velocity_measured_at": 1700000000.0,
    }
    row.update(extra)
    return row


def test_feed_row_reward_field_does_not_cap_the_window():
    base = MakerConfig(max_spread_from_mid=0.15)
    # The venue's rewardsMaxSpread as the ranker wrote it for years.
    cfg = spec_to_cfg(base, _row(max_spread=2.5))
    assert cfg.max_spread_from_mid == 0.15


def test_no_daily_income_field_on_the_market_config():
    base = MakerConfig()
    cfg = spec_to_cfg(base, _row(daily=1234.5))
    assert not hasattr(cfg, "market_daily_rate")
    assert cfg.max_spread_from_mid == base.max_spread_from_mid


def test_engine_window_override_survives_through_the_ranker_row():
    base = MakerConfig(max_spread_from_mid=0.20)
    cfg = spec_to_cfg(base, _row(max_spread=6.5, daily=99.0))
    assert cfg.max_spread_from_mid == 0.20


def test_objective_is_spread_capture_only():
    cfg = spec_to_cfg(MakerConfig(), _row())
    assert cfg.objective == "spread_capture"


def test_rewards_mode_fields_are_gone():
    base = MakerConfig()
    for field in ("reward_min_payout_usd", "reward_floor_multiple",
                  "est_reward_pool_usd", "rebate_rate", "market_daily_rate",
                  "marginal_return_floor", "allocation_budget",
                  "max_market_frac", "spread_capture_frac",
                  "spread_capture_default_spread"):
        assert not hasattr(base, field), f"{field} must be deleted (#594)"


def test_venue_fee_fields_stay():
    """fee_rate is real venue economics (pair_scanner charges it on
    completions); maker_fee is the venue's takerOnly fact. Neither is a
    rebate and both survive the purge."""
    base = MakerConfig()
    assert base.fee_rate == 0.07
    assert base.maker_fee == 0.0


def test_bankroll_override_survives_the_allocator_purge(monkeypatch):
    """The purge deleted allocation_budget but its bankroll writer stayed
    behind: load() with a bankroll override crashed with TypeError on the
    unknown kwarg. The override now lands on bankroll_usd only (#594)."""
    monkeypatch.setenv("SPREAD_HUNTER_BANKROLL", "2000")
    monkeypatch.delenv("HUNTER_BANKROLL", raising=False)
    cfg = load_scoring()
    assert cfg.bankroll_usd == 2000
    assert not hasattr(cfg, "allocation_budget")


def test_scoring_rewards_module_is_deleted():
    """scoring.rewards was orphaned Polymarket liquidity reward math;
    Issue #681 purges it permanently."""
    import importlib.util
    spec = importlib.util.find_spec("scoring.rewards")
    assert spec is None, "scoring.rewards must be completely deleted (#681)"


def test_filter_markets_has_no_order_score_or_q_min():
    """scripts.filter_markets must not define order_score or q_min (#681)."""
    import scripts.filter_markets as fm
    assert not hasattr(fm, "order_score"), "order_score must be purged (#681)"
    assert not hasattr(fm, "q_min"), "q_min must be purged (#681)"


def test_evaluate_produces_spread_metrics_without_reward_pool_math():
    """evaluate() ranks on spread/movement and does not calculate reward pool splits (#681)."""
    import time
    import scripts.filter_markets as fm

    class FakeSession:
        def get(self, url, params=None, timeout=None):
            if "trades" in url:
                return type("Resp", (), {
                    "json": lambda self: [{"timestamp": time.time(), "price": 0.5, "size": 1000.0}],
                    "raise_for_status": lambda self: None,
                })()
            return type("Resp", (), {
                "json": lambda self: {
                    "bids": [{"price": "0.48", "size": "1000"}],
                    "asks": [{"price": "0.52", "size": "1000"}],
                },
                "raise_for_status": lambda self: None,
            })()

    from datetime import datetime, timezone, timedelta

    cand = {
        "condition_id": "0xtest_pure_spread",
        "question": "Binary Question?",
        "tokens": [{"token_id": "tok1"}, {"token_id": "tok2"}],
        "rewards": {"max_spread": 0.5, "min_size": 1000, "rate": 500.0},  # tight reward window
        "minimum_tick_size": 0.01,
        "end_date_iso": (datetime.now(timezone.utc) + timedelta(days=5)).isoformat(),
        "_order_min": 5,
        "_spread": 0.04,
        "closed": False,
        "accepting_orders": True,
    }

    res = fm.evaluate(FakeSession(), rate=0.0, m=cand, volume_24h=100_000.0,
                      source="spread", min_volume_usd=1000.0, min_depth_usd=100.0,
                      max_spread=0.08)

    assert res["eligible"] is True
    assert res["movement_usd"] == 500.0
    assert res["est_income"] == 0.0
    assert res["est_capital"] == 0.0
    assert res["return_pct_day"] == 0.0
    assert res["their_score"] == 0.0


def test_screener_terminal_output_has_no_reward_terms(tmp_path, monkeypatch, capsys):
    """filter_markets.main() terminal output must not print reward vocabulary (#681)."""
    import scripts.filter_markets as fm

    winner = {
        "cid": "0xtest", "title": "Test Spread Market", "source": "spread",
        "eligible": True, "spread": 0.025, "volume_24h": 150_000.0,
        "movement_usd": 1200.0, "yes_depth_usd": 800.0, "no_depth_usd": 750.0,
        "est_income": 0.0, "est_capital": 0.0, "return_pct_day": 0.0,
    }
    monkeypatch.setattr(fm, "RUN", tmp_path / "shared")
    monkeypatch.setattr(fm, "gamma_universe", lambda s, min_volume_usd=None, full_scan=False: (
        [], {"pages_fetched": 0, "rows_scanned": 0, "truncated": False, "cheap_rejects": {}}))
    monkeypatch.setattr(fm, "_score_universe", lambda *a, **k: ([winner], 1))
    monkeypatch.setattr(fm, "_write_universe_file", lambda *a, **k: None)
    monkeypatch.setattr(fm, "_write_pipeline_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(fm, "_log_rank_near_misses", lambda *a, **k: 0)
    monkeypatch.setattr(fm, "_log_rank_volume_near_misses", lambda *a, **k: 0)
    monkeypatch.setattr("sys.argv", ["filter_markets", "--dry-run"])

    fm.main()
    out = capsys.readouterr().out

    for forbidden in ("income > $0/day", "$/day", "ret%/d", "TOTAL capital", "spread / 0 reward", "reward"):
        assert forbidden not in out, f"Terminal output contained purged reward term '{forbidden}' (#681)"
    assert "picked 1 spread markets" in out
    assert "spread" in out
    assert "vol_24h" in out
    assert "move_30m" in out
    assert "depth" in out




