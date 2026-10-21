"""live/tests/test_live_funnel.py - Level 2 funnel sourced from the screener snapshot.

The live Level 2 funnel must reuse the ranker's own gate names and counts so a
refusal compares 1:1 with the paper-run scan (server/fleet_dash.py). These tests pin
that mapping against a hermetic fixture rather than the repo's live
runtime/pipeline.json.
"""

from __future__ import annotations

import json

from core_brain.kpi import _funnel_from_pipeline


def _write(path, obj):
    path.write_text(json.dumps(obj), encoding="utf-8")
    return path


def test_funnel_from_pipeline_reuses_screener_gate_names(tmp_path):
    """RAW/FILTERS/FINAL come from pipeline.json with the screener's exact cause labels."""
    pipeline = _write(tmp_path / "pipeline.json", {
        "counts": {
            "funded": 999, "spread_universe": 16,
            "eligible": 3, "picked": 3, "rejected": 187,
        },
        "rejections": [
            {"cause": "YES: top-3 bid depth", "n": 101,
             "examples": [{"title": "A", "reason": "YES: top-3 bid depth $4 < $1,000"}]},
            {"cause": "volume", "n": 54,
             "examples": [{"title": "B", "reason": "24h volume $50,000 < $250,000"}]},
            {"cause": "horizon", "n": 1, "examples": []},
        ],
    })
    markets = _write(tmp_path / "markets.json", [
        {"cid": "0xabc", "slug": "m1", "title": "Tigers vs Pirates"},
        {"cid": "0xdef", "slug": "m2", "title": "Jodar vs Flavio"},
    ])
    by_mkt = {"0xabc": {"fills_count": 2, "realized_pnl": 0.5}}

    f = _funnel_from_pipeline(by_mkt, pipeline_path=pipeline, markets_path=markets)

    assert f is not None
    assert f["source"] == "screener"
    # RAW = reward pool + gamma liquid pool, matching the paper-run scan's 1015.
    assert f["raw_count"] == 1015
    assert f["final_count"] == 3
    assert [g["cause"] for g in f["filters"]] == ["YES: top-3 bid depth", "volume", "horizon"]
    assert f["filters"][0]["n"] == 101
    assert f["filters"][0]["examples"][0]["title"] == "A"
    # GRADUATED = the ranker's picks, annotated with live fills/PnL.
    assert len(f["graduated"]) == 2
    assert f["graduated"][0]["condition_id"] == "0xabc"
    assert f["graduated"][0]["fills"] == 2
    assert f["graduated"][0]["pnl"] == 0.5
    assert f["graduated"][1]["fills"] == 0
    assert f["counts"]["funded"] == 999
    assert "raw" in f
    assert "final" in f
    assert "picked" in f


def test_funnel_carries_near_miss_counters(tmp_path):
    """would_fund/traps reach the dashboard so the near-miss footer can render."""
    pipeline = _write(tmp_path / "pipeline.json", {
        "counts": {"funded": 4, "spread_universe": 0, "eligible": 1},
        "rejections": [
            {"cause": "volume", "n": 9, "would_fund": 3, "traps": 1, "examples": []},
            {"cause": "horizon", "n": 2, "examples": []},
        ],
    })

    f = _funnel_from_pipeline({}, pipeline_path=pipeline,
                              markets_path=tmp_path / "missing.json")

    assert f is not None
    assert f["filters"][0]["would_fund"] == 3
    assert f["filters"][0]["traps"] == 1
    # A bucket the snapshot wrote without counters reads as zero, not missing.
    assert f["filters"][1]["would_fund"] == 0
    assert f["filters"][1]["traps"] == 0


def test_funnel_carries_every_gate_bar(tmp_path):
    """The movement, spread, horizon, payout and pair-cost bars reach the dashboard.

    The kanban hero blocks state the contract the screener gated on. Reading
    them from the snapshot is what stops the panel claiming a stale pair-cost
    cap after the config changes -- the movement bar was the one bar still
    frozen into the markup, so an operator setting FILTER_LIVE_VEL_MIN_USD saw
    the parameter card move while the lane header kept its old number.
    """
    pipeline = _write(tmp_path / "pipeline.json", {
        "counts": {"funded": 1, "spread_universe": 0, "eligible": 0},
        "rejections": [],
        "depth_gate_usd": 1000.0,
        "volume_gate_usd": 250000.0,
        "movement_gate_usd": 777.0,
        "spread_gate": 0.06,
        "horizon_gate_days": 30.0,
        "reward_min_income_usd_day": 1.5,
        "spread_min_income_usd_day": 0.0,
        "bot_pos_price_max_pair_cost_usd": 0.995,
    })

    f = _funnel_from_pipeline({}, pipeline_path=pipeline,
                              markets_path=tmp_path / "missing.json")

    assert f is not None
    assert f["movement_gate_usd"] == 777.0
    assert f["spread_gate"] == 0.06
    assert f["horizon_gate_days"] == 30.0
    assert f["reward_min_income_usd_day"] == 1.5
    assert f["spread_min_income_usd_day"] == 0.0
    assert f["bot_pos_price_max_pair_cost_usd"] == 0.995


def test_funnel_passes_the_condition_id_through_to_the_final_rows(tmp_path):
    """The id the dashboard dedupes on survives the funnel unchanged."""
    pipeline = _write(tmp_path / "pipeline.json", {
        "counts": {"funded": 1, "spread_universe": 0, "eligible": 1},
        "rejections": [],
        "final": [{"cid": "0xabc", "title": "Tigers vs Pirates"}],
        "picked": [{"cid": "0xabc", "title": "Tigers vs Pirates"}],
    })

    f = _funnel_from_pipeline({}, pipeline_path=pipeline,
                              markets_path=tmp_path / "missing.json")

    assert f is not None
    assert f["final"][0]["cid"] == "0xabc"
    assert f["picked"][0]["cid"] == "0xabc"


def test_funnel_from_pipeline_returns_none_when_absent(tmp_path):
    assert _funnel_from_pipeline({}, pipeline_path=tmp_path / "missing.json") is None


def test_funnel_from_pipeline_returns_none_on_malformed(tmp_path):
    bad = tmp_path / "pipeline.json"
    bad.write_text("{not json", encoding="utf-8")
    assert _funnel_from_pipeline({}, pipeline_path=bad) is None


def test_funnel_from_pipeline_defaults_to_repo_run_dir(monkeypatch, tmp_path):
    """With no explicit path the helper reads the repo's runtime/pipeline.json."""
    import core_brain.kpi as kpi_mod

    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    _write(runtime_dir / "pipeline.json", {
        "counts": {"funded": 10, "spread_universe": 5, "eligible": 2},
        "rejections": [{"cause": "volume", "n": 1, "examples": []}],
    })
    _write(runtime_dir / "markets.json", [{"cid": "0x1", "slug": "m", "title": "T"}])
    monkeypatch.setattr(kpi_mod, "REPO_ROOT", tmp_path)

    f = _funnel_from_pipeline({})
    assert f is not None
    assert f["raw_count"] == 15
    assert [g["cause"] for g in f["filters"]] == ["volume"]
    assert f["graduated"][0]["condition_id"] == "0x1"


def test_report_funnel_allowed_for_explicit_shadow_db(monkeypatch, tmp_path):
    """An explicit per-run shadow database resolves to pipeline snapshot funnel."""
    import core_brain.kpi as kpi_mod
    from core_brain.order_registry import init_db

    shadow_db = tmp_path / "data" / "shadow_20260829_120000_run-a.db"
    shadow_db.parent.mkdir(parents=True, exist_ok=True)
    init_db(shadow_db)

    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    _write(runtime_dir / "pipeline.json", {
        "counts": {"funded": 10, "spread_universe": 5, "eligible": 2},
        "rejections": [{"cause": "volume", "n": 1, "examples": []}],
    })
    _write(runtime_dir / "markets.json", [{"cid": "0x1", "slug": "m", "title": "T"}])
    monkeypatch.setattr(kpi_mod, "REPO_ROOT", tmp_path)

    rep = kpi_mod.report(db_path=shadow_db)
    assert rep["funnel"]["source"] == "screener"


def test_report_funnel_excluded_for_custom_db_path(monkeypatch, tmp_path):
    """Custom databases (e.g. isolated test or custom shadow db) fall back to runtime telemetry."""
    import time
    import core_brain.kpi as kpi_mod
    from core_brain.order_registry import OrderRegistry, MarketEventRecord, init_db

    custom_db = tmp_path / "custom_test" / "shadow.db"
    custom_db.parent.mkdir(parents=True, exist_ok=True)
    init_db(custom_db)

    reg = OrderRegistry(db_path=custom_db)
    reg.log_market_event(MarketEventRecord(
        ts=time.time(), market_slug="custom-slug", condition_id="0xcustom",
        kind="BLOCKED", reason="depth $5 < $1000", reason_code="CUSTOM_DEPTH_LOW",
    ))

    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    _write(runtime_dir / "pipeline.json", {
        "counts": {"funded": 10, "spread_universe": 5, "eligible": 2},
        "rejections": [{"cause": "screener_volume", "n": 1, "examples": []}],
    })
    monkeypatch.setattr(kpi_mod, "REPO_ROOT", tmp_path)

    rep = kpi_mod.report(db_path=custom_db)
    assert rep["funnel"]["source"] == "runtime"
    assert len(rep["funnel"]["filters"]) == 1
    assert rep["funnel"]["filters"][0]["cause"] == "CUSTOM_DEPTH_LOW"
    assert rep["funnel"]["filters"][0]["n"] == 1


def test_report_funnel_allowed_for_explicit_shadow_db_from_cwd(monkeypatch, tmp_path):
    """An explicit per-run shadow database started outside the repo still gets the screener funnel."""
    import core_brain.kpi as kpi_mod
    from core_brain.order_registry import init_db
    workdir = tmp_path / "elsewhere"
    shadow_db = workdir / "data" / "shadow_20260829_120000_run-a.db"
    shadow_db.parent.mkdir(parents=True, exist_ok=True)
    init_db(shadow_db)

    repo_root = tmp_path / "repo"
    runtime_dir = repo_root / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    _write(runtime_dir / "pipeline.json", {
        "counts": {"funded": 10, "spread_universe": 5, "eligible": 2},
        "rejections": [{"cause": "volume", "n": 1, "examples": []}],
    })
    _write(runtime_dir / "markets.json", [{"cid": "0x1", "slug": "m", "title": "T"}])
    monkeypatch.setattr(kpi_mod, "REPO_ROOT", repo_root)
    monkeypatch.chdir(workdir)

    rep = kpi_mod.report(db_path=shadow_db)
    assert rep["funnel"]["source"] == "screener"


def test_funnel_from_pipeline_includes_slug_and_url(tmp_path):
    """Graduated markets receive url & slug; rejected examples and raw candidates receive slug."""
    pipeline = _write(tmp_path / "pipeline.json", {
        "counts": {"funded": 10, "spread_universe": 5, "eligible": 1, "picked": 1},
        "raw": {
            "rewards": [{"title": "Reward Market", "slug": "slug-rew", "rate": 0.5}],
            "spread": [{"title": "Spread Market", "slug": "slug-spr", "spread": 0.02}],
        },
        "rejections": [
            {
                "cause": "volume",
                "n": 1,
                "examples": [{"title": "Low Vol Mkt", "slug": "low-vol-slug", "reason": "too low"}],
            }
        ],
        "final": [{"cid": "0x1", "title": "Winner Mkt", "slug": "winner-slug", "source": "spread"}],
    })
    markets = _write(tmp_path / "markets.json", [
        {"cid": "0x1", "slug": "winner-slug", "title": "Winner Mkt"},
        {"cid": "0x2", "slug": "fallback-slug", "title": "Fallback Mkt"},
        {"cid": "0x3", "title": "No Slug Mkt"},
    ])
    by_mkt = {
        "0x1": {"url": "https://polymarket.com/market/winner-slug-override"},
        "0x2": {},
        "0x3": {},
    }

    f = _funnel_from_pipeline(by_mkt, pipeline_path=pipeline, markets_path=markets)
    assert f is not None

    # Graduated entries
    grad = f["graduated"]
    assert len(grad) == 3
    # 1. Custom URL override
    assert grad[0]["slug"] == "winner-slug"
    assert grad[0]["url"] == "https://polymarket.com/market/winner-slug-override"

    # 2. Fallback URL generated from slug when by_mkt has no URL
    assert grad[1]["slug"] == "fallback-slug"
    assert grad[1]["url"] == "https://polymarket.com/market/fallback-slug"

    # 3. Empty URL when neither URL nor slug exists
    assert grad[2]["slug"] == ""
    assert grad[2]["url"] == ""

    # Rejections carry slug
    rej = f["filters"][0]
    assert rej["examples"][0]["slug"] == "low-vol-slug"

    # Raw candidates carry slug
    assert f["raw"]["rewards"][0]["slug"] == "slug-rew"
    assert f["raw"]["spread"][0]["slug"] == "slug-spr"

    # Final candidates carry slug
    assert f["final"][0]["slug"] == "winner-slug"


def test_funnel_passes_the_per_lane_numbers_through(tmp_path):
    """Each lane's counts and filters reach the dashboard from the same mapping (#744)."""
    pipeline = _write(tmp_path / "pipeline.json", {
        "counts": {"funded": 0, "spread_universe": 137, "attempted": 137,
                   "scored": 137, "dropped_no_verdict": 0, "rejected": 132,
                   "eligible": 5, "picked": 5},
        "rejections": [
            {"cause": "volume", "n": 100, "examples": []},
        ],
        "lanes": {
            "live": {
                "counts": {"funded": 0, "spread_universe": 5, "attempted": 5,
                           "scored": 5, "dropped_no_verdict": 0,
                           "rejected": 4, "eligible": 1, "picked": 1},
                "rejections": [
                    {"cause": "movement", "n": 4, "would_fund": 0, "traps": 0,
                     "examples": [{"title": "WTA", "reason": "no movement",
                                   "lane": "live"}]},
                ],
            },
            "regular": {
                "counts": {"funded": 0, "spread_universe": 132, "attempted": 132,
                           "scored": 132, "dropped_no_verdict": 0,
                           "rejected": 128, "eligible": 4, "picked": 4},
                "rejections": [
                    {"cause": "volume", "n": 100, "examples": []},
                    {"cause": "movement", "n": 28, "examples": []},
                ],
            },
        },
    })

    f = _funnel_from_pipeline({}, pipeline_path=pipeline,
                              markets_path=tmp_path / "missing.json")

    assert f is not None
    # All keeps the global blocks.
    assert f["counts"]["scored"] == 137
    assert [g["cause"] for g in f["filters"]] == ["volume"]
    # Each lane reads its own, built by the same mapper.
    assert f["lanes"]["live"]["counts"]["scored"] == 5
    assert [g["cause"] for g in f["lanes"]["live"]["filters"]] == ["movement"]
    assert f["lanes"]["live"]["filters"][0]["n"] == 4
    assert f["lanes"]["live"]["filters"][0]["examples"][0]["lane"] == "live"
    assert [g["cause"] for g in f["lanes"]["regular"]["filters"]] == ["volume", "movement"]
    # A lane's counts can be summed back to the All view the same run.
    for key, value in f["lanes"]["live"]["counts"].items():
        assert value + f["lanes"]["regular"]["counts"][key] == f["counts"][key]


def test_funnel_reports_no_lane_numbers_for_an_older_snapshot(tmp_path):
    """An artifact written before #744 has no lanes -- None, never fabricated zeros."""
    pipeline = _write(tmp_path / "pipeline.json", {
        "counts": {"funded": 0, "spread_universe": 137, "eligible": 5},
        "rejections": [{"cause": "volume", "n": 1, "examples": []}],
    })

    f = _funnel_from_pipeline({}, pipeline_path=pipeline,
                              markets_path=tmp_path / "missing.json")

    assert f is not None
    assert f["lanes"] is None
    # The global blocks are unaffected by the lane block's absence.
    assert f["counts"]["spread_universe"] == 137
    assert [g["cause"] for g in f["filters"]] == ["volume"]


def test_the_wta_row_is_live_without_a_lane_field(tmp_path):
    """The 2026-10-21 markets.json shape: `event_live` and no lane.

    That row was screened and graduated while the live lane reported 0 (#744),
    so the reader has to classify the artifact it is given. A row the ranker
    stamps decides for itself; these old fields are the fallback only.
    """
    pipeline = _write(tmp_path / "pipeline.json", {
        "counts": {"funded": 0, "spread_universe": 2, "eligible": 2},
        "rejections": [],
    })
    markets = _write(tmp_path / "markets.json", [
        {"cid": "0xwta", "slug": "wta-buyukak-klimovi-2026-10-05",
         "title": "Samsun: Cagla Buyukakcay vs Linda Klimovicova",
         "event_live": True, "sport": "wta",
         "event_period": "S3", "event_score": "6-3, 6-7(2-7), 0-3"},
        {"cid": "0xbtc", "slug": "btc-above-100k", "title": "BTC above 100k?"},
        {"cid": "0xstamped", "slug": "stamped", "title": "Stamped row",
         "lane": "regular", "_live_event": True},
    ])

    f = _funnel_from_pipeline({}, pipeline_path=pipeline, markets_path=markets)

    assert f is not None
    assert f["graduated"][0]["lane"] == "live"
    assert f["graduated"][0]["event_live"] is True
    assert f["graduated"][0]["event_period"] == "S3"
    assert f["graduated"][1]["lane"] == "regular"
    # The ranker's stamp wins over a stale flag.
    assert f["graduated"][2]["lane"] == "regular"


def test_write_pipeline_snapshot_slug_serialization(monkeypatch, tmp_path):
    """_write_pipeline_snapshot serializes slug in raw, rejections, final, and picked."""
    import scripts.filter_markets as fm

    rt = tmp_path / "runtime"
    monkeypatch.setattr(fm, "RUN", rt)

    cands = [(0.85, {"question": "Rew Q", "market_slug": "rew-slug"})]
    spread_cands = [{"question": "Spr Q", "slug": "spr-slug", "_volume_24h": 1000, "_spread": 0.01}]
    out_row = {
        "eligible": False,
        "title": "Rej Title",
        "slug": "rej-slug",
        "reject_reason": "volume: too low",
        "volume_24h": 500,
        "days_to_resolve": 3,
    }
    elig_row = {
        "cid": "0xelig",
        "title": "Elig Title",
        "slug": "elig-slug",
        "source": "spread",
        "est_income": 1.2,
        "est_capital": 50.0,
        "return_pct_day": 2.4,
        "volume_24h": 10000,
        "days_to_resolve": 5,
    }
    pick_row = {
        "cid": "0xpick",
        "title": "Pick Title",
        "slug": "pick-slug",
        "source": "reward",
        "est_income": 2.0,
        "est_capital": 40.0,
        "return_pct_day": 5.0,
        "volume_24h": 20000,
        "days_to_resolve": 2,
    }

    fm._write_pipeline_snapshot(
        cands=cands,
        spread_cands=spread_cands,
        out=[out_row],
        eligible=[elig_row],
        picked=[pick_row],
        causes={"volume": 1},
        census="",
        gates="",
        attempted=1,
        rejected=1,
    )

    snap_file = rt / "pipeline.json"
    assert snap_file.is_file()
    snap = json.loads(snap_file.read_text(encoding="utf-8"))

    assert snap["raw"]["rewards"][0]["slug"] == "rew-slug"
    assert snap["raw"]["spread"][0]["slug"] == "spr-slug"
    assert snap["rejections"][0]["examples"][0]["slug"] == "rej-slug"
    assert snap["final"][0]["slug"] == "elig-slug"
    assert snap["picked"][0]["slug"] == "pick-slug"



