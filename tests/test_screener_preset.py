"""#687: the tournament preset toggle covers the ranker, and the UI shows it.

`BOT_QUOTE_TOURNAMENT_PRESET` used to reach the engine only, so a preset-run
ranker scored with default bars while the operator believed the preset was on.
These tests pin the three promises that fix it:

1. The ranker's loader honors the same selector (canonical, legacy, blank
   fallback, unknown raises) with precedence defaults -> preset -> explicit.
2. Preset-off runs are unchanged, and `baseline` keeps the picker's deliberate
   0.045 quote window (no PICKER_PROFILE field in any screener preset).
3. The ranker publishes what it loaded (`pipeline.json` -> KPI funnel), and the
   screener board has a pill that reads it.
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import scripts.filter_markets as fm
from core_brain import kpi as kpi_mod
from core_brain.config import (
    PICKER_PROFILE,
    SCREENER_PRESETS,
    TOURNAMENT_PRESETS,
    MakerConfig as CoreMakerConfig,
    resolve_tournament_preset_name,
)
from scoring import config as scoring_config

REPO = Path(__file__).resolve().parents[1]

SELECTOR_VARS = ("BOT_QUOTE_TOURNAMENT_PRESET", "BOT_QUOTE_TOURNAMENT_PRESET")


def _clear_selector(monkeypatch):
    for var in SELECTOR_VARS:
        monkeypatch.delenv(var, raising=False)


# --- resolver ---------------------------------------------------------------

def test_no_selector_resolves_to_none(monkeypatch):
    _clear_selector(monkeypatch)
    assert resolve_tournament_preset_name() is None


def test_canonical_selector_resolves(monkeypatch):
    monkeypatch.setenv("BOT_QUOTE_TOURNAMENT_PRESET", "balanced")
    assert resolve_tournament_preset_name() == "balanced"


def test_retired_selector_name_is_ignored(monkeypatch):
    """The retired `HUNTER_TOURNAMENT_PRESET` selects nothing."""
    monkeypatch.setenv("HUNTER_TOURNAMENT_PRESET", "Aggressive ")
    assert resolve_tournament_preset_name() is None


def test_canonical_wins_over_legacy(monkeypatch):
    monkeypatch.setenv("BOT_QUOTE_TOURNAMENT_PRESET", "prudent")
    monkeypatch.setenv("HUNTER_TOURNAMENT_PRESET", "aggressive")
    assert resolve_tournament_preset_name() == "prudent"


def test_blank_canonical_selects_nothing(monkeypatch):
    """A blank canonical is a blank canonical: there is no legacy to fall back to."""
    monkeypatch.setenv("BOT_QUOTE_TOURNAMENT_PRESET", "   ")
    monkeypatch.setenv("HUNTER_TOURNAMENT_PRESET", "hybrid")
    assert resolve_tournament_preset_name() is None


def test_unknown_selector_raises_engine_and_ranker(monkeypatch):
    monkeypatch.setenv("BOT_QUOTE_TOURNAMENT_PRESET", "yolo")
    with pytest.raises(ValueError, match="is unknown"):
        resolve_tournament_preset_name()
    with pytest.raises(ValueError, match="is unknown"):
        CoreMakerConfig.from_env()
    with pytest.raises(ValueError, match="is unknown"):
        scoring_config.load()


# --- registry ----------------------------------------------------------------

def test_screener_presets_share_engine_names():
    assert set(SCREENER_PRESETS.keys()) == set(TOURNAMENT_PRESETS.keys())


def test_screener_preset_overrides_are_real_fields_and_never_picker_profile():
    core_fields = {f.name for f in dataclasses.fields(CoreMakerConfig)}
    for name, preset in SCREENER_PRESETS.items():
        for key in preset:
            if key == "description":
                continue
            assert key in core_fields, f"{name}: {key!r} is not a config field"
            assert key not in PICKER_PROFILE, (
                f"{name}: {key!r} is a deliberate picker/engine disagreement "
                f"(PICKER_PROFILE) and must not be clobbered by a preset"
            )


# --- scoring loader precedence -------------------------------------------------

def test_preset_off_scores_with_defaults(monkeypatch):
    _clear_selector(monkeypatch)
    cfg = scoring_config.load()
    assert cfg.max_spread_from_mid == PICKER_PROFILE["max_spread_from_mid"]
    assert cfg.filter_std_vol_min_24h_usd == 125_000.0
    assert cfg.filter_live_activity_min_ratio == 0.0


def test_preset_on_loads_cleanly_and_keeps_picker_window(monkeypatch):
    for name in TOURNAMENT_PRESETS:
        monkeypatch.setenv("BOT_QUOTE_TOURNAMENT_PRESET", name)
        cfg = scoring_config.load()
        assert cfg.max_spread_from_mid == 0.045, (
            f"preset {name!r} must not touch the picker's 0.045 window"
        )
    # Precedence probe: a temporary in-test bar proves the preset merge in
    # scoring.config.load() exists. Monkeypatch restores the registry after
    # the test, so no shipped product value is chosen here. Without the
    # merge, the loader ignores the registry and the first assertion fails.
    monkeypatch.setitem(
        SCREENER_PRESETS["balanced"], "filter_std_vol_min_24h_usd", 77_000.0)
    monkeypatch.setenv("BOT_QUOTE_TOURNAMENT_PRESET", "balanced")
    assert scoring_config.load().filter_std_vol_min_24h_usd == 77_000.0
    monkeypatch.setenv("FILTER_STD_VOL_MIN_24H_USD", "200000")
    assert scoring_config.load().filter_std_vol_min_24h_usd == 200000.0


def test_explicit_setting_overrides_the_preset(monkeypatch):
    monkeypatch.setenv("BOT_QUOTE_TOURNAMENT_PRESET", "balanced")
    monkeypatch.setenv("FILTER_STD_VOL_MIN_24H_USD", "200000")
    assert scoring_config.load().filter_std_vol_min_24h_usd == 200000.0


def test_legacy_selector_reaches_the_ranker_loader(monkeypatch):
    _clear_selector(monkeypatch)
    monkeypatch.setenv("BOT_QUOTE_TOURNAMENT_PRESET", "conservative")
    cfg = scoring_config.load()
    assert cfg.max_spread_from_mid == 0.045


# --- snapshot + funnel ---------------------------------------------------------

def test_snapshot_publishes_the_loaded_preset(tmp_path, monkeypatch):
    monkeypatch.setattr(fm, "RUN", tmp_path)
    monkeypatch.setenv("BOT_QUOTE_TOURNAMENT_PRESET", "balanced")
    fm._write_pipeline_snapshot(
        cands=[], spread_cands=[], out=[], eligible=[], picked=[],
        causes={}, census="", gates="", attempted=0, rejected=0,
    )
    snap = json.loads((tmp_path / "pipeline.json").read_text(encoding="utf-8"))
    assert snap["tournament_preset"] == "balanced"


def test_snapshot_preset_is_null_when_off(tmp_path, monkeypatch):
    monkeypatch.setattr(fm, "RUN", tmp_path)
    _clear_selector(monkeypatch)
    fm._write_pipeline_snapshot(
        cands=[], spread_cands=[], out=[], eligible=[], picked=[],
        causes={}, census="", gates="", attempted=0, rejected=0,
    )
    snap = json.loads((tmp_path / "pipeline.json").read_text(encoding="utf-8"))
    assert snap["tournament_preset"] is None


def test_funnel_carries_the_preset_through(tmp_path):
    pipe = tmp_path / "pipeline.json"
    pipe.write_text(json.dumps({
        "ts": 1_700_000_000.0,
        "tournament_preset": "prudent",
        "counts": {"funded": 0, "spread_universe": 1, "eligible": 0},
        "rejections": [],
    }), encoding="utf-8")
    funnel = kpi_mod._funnel_from_pipeline({}, pipeline_path=pipe,
                                           markets_path=tmp_path / "no-markets.json")
    assert funnel is not None
    assert funnel["tournament_preset"] == "prudent"


def test_funnel_preset_missing_reads_as_missing(tmp_path):
    pipe = tmp_path / "pipeline.json"
    pipe.write_text(json.dumps({
        "ts": 1_700_000_000.0,
        "counts": {"funded": 0, "spread_universe": 0, "eligible": 0},
        "rejections": [],
    }), encoding="utf-8")
    funnel = kpi_mod._funnel_from_pipeline({}, pipeline_path=pipe,
                                           markets_path=tmp_path / "no-markets.json")
    assert funnel is not None
    assert funnel["tournament_preset"] is None


# --- selector reaches the menu-started ranker -----------------------------------

def test_selector_in_env_file_reaches_the_ranker(tmp_path):
    """#674 pattern: a selector staged in `.env` is visible to the ranker process."""
    (tmp_path / ".env").write_text("BOT_QUOTE_TOURNAMENT_PRESET=balanced\n", encoding="utf-8")
    code = textwrap.dedent(f"""
        import os, sys
        sys.path.insert(0, {str(REPO)!r})
        import scripts.filter_markets as fm
        for k in list(os.environ.keys()):
            if k.startswith(("HUNTER_", "FILTER_", "BOT_", "SPREAD_HUNTER_")):
                os.environ.pop(k, None)
        fm._load_repo_env({str(tmp_path)!r})
        from core_brain.config import resolve_tournament_preset_name
        print(resolve_tournament_preset_name())
    """)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("HUNTER_", "FILTER_", "BOT_", "SPREAD_HUNTER_"))}
    proc = subprocess.run([sys.executable, "-c", code], cwd=tmp_path,
                          capture_output=True, text=True, env=env, check=True)
    assert proc.stdout.strip().splitlines()[-1] == "balanced"


# --- screener board pill ----------------------------------------------------------

def test_screener_board_has_a_preset_pill_reading_the_funnel():
    html = (REPO / "dashboard" / "static" / "index.html").read_text(encoding="utf-8")
    assert 'id="screener-preset-pill"' in html
    js = (REPO / "dashboard" / "static" / "app.js").read_text(encoding="utf-8")
    assert "screener-preset-pill" in js
    assert "funnel?.tournament_preset" in js
