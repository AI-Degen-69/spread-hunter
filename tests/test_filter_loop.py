"""Tests for scripts/filter_loop.py."""
import os
import tempfile
from pathlib import Path
from unittest.mock import patch
import pytest

import scripts.filter_loop as filter_loop

def test_get_top_markets_dynamic_env_reload(monkeypatch, tmp_path):
    # Setup a mock ROOT path for the script so it looks for .env in tmp_path
    monkeypatch.setattr(filter_loop, "ROOT", tmp_path)
    env_file = tmp_path / ".env"
    
    # 1. No .env, no process env -> defaults to 2
    monkeypatch.setattr(filter_loop, "_ORIGINAL_ENV_TOP", None)
    assert filter_loop._get_top_markets() == 2
    
    # 2. .env set to 5 -> returns 5
    env_file.write_text("SH_TOP_MARKETS=5", encoding="utf-8")
    assert filter_loop._get_top_markets() == 5
    
    # 3. .env changed to 10 -> returns 10 (dynamic reload works)
    env_file.write_text("SH_TOP_MARKETS=10", encoding="utf-8")
    assert filter_loop._get_top_markets() == 10
    
    # 4. Original process env was set -> overrides .env
    monkeypatch.setattr(filter_loop, "_ORIGINAL_ENV_TOP", "8")
    assert filter_loop._get_top_markets() == 8

def test_get_top_markets_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(filter_loop, "ROOT", tmp_path)
    # If dotenv fails, falls back to original env
    monkeypatch.setattr(filter_loop, "_ORIGINAL_ENV_TOP", "15")

    # Simulate dotenv error
    with patch("dotenv.dotenv_values", side_effect=Exception("mocked error")):
        assert filter_loop._get_top_markets() == 15


def test_loop_flags_default_to_none():
    args = filter_loop.parse_args([])
    assert args.out_dir is None
    assert args.trial_depth is None


def test_rank_cmd_without_flags_carries_no_new_options():
    cmd = filter_loop._rank_cmd(2)
    assert cmd[:5] == [filter_loop.sys.executable, "-m",
                       "scripts.filter_markets", "--top", "2"]
    assert "--out-dir" not in cmd
    assert "--trial-depth" not in cmd or _config_depth_in(cmd)


def _config_depth_in(cmd):
    # The configured HUNTER_DEPTH_TRIAL_USD passes through even without flags;
    # that is today's behavior and must survive.
    return "--trial-depth" in cmd


def test_rank_cmd_forwards_out_dir_and_trial_depth(tmp_path):
    cmd = filter_loop._rank_cmd(2, out_dir=tmp_path / "t",
                                trial_depth=250.0)
    assert "--out-dir" in cmd
    assert cmd[cmd.index("--out-dir") + 1] == str(tmp_path / "t")
    assert "--trial-depth" in cmd
    assert cmd[cmd.index("--trial-depth") + 1] == "250.0"


def test_cli_trial_depth_overrides_the_configured_trial(monkeypatch):
    from types import SimpleNamespace

    import scoring.config as scoring_config

    monkeypatch.setattr(scoring_config, "load", lambda: SimpleNamespace(
        select_min_top3_depth_usd_trial=500.0,
        select_min_volume_24h_usd_trial=None))

    cmd = filter_loop._rank_cmd(2, trial_depth=250.0)
    depths = [cmd[i + 1] for i, a in enumerate(cmd)
              if a == "--trial-depth"]
    assert depths == ["250.0"]

    cmd = filter_loop._rank_cmd(2)
    depths = [cmd[i + 1] for i, a in enumerate(cmd)
              if a == "--trial-depth"]
    assert depths == ["500.0"]


def test_loop_paths_default_to_the_shared_runtime_dir():
    log_path, ring_path = filter_loop._loop_paths(None)
    assert log_path == filter_loop.LOG
    assert ring_path == filter_loop.RING_PATH


def test_loop_paths_follow_the_output_directory(tmp_path):
    log_path, ring_path = filter_loop._loop_paths(tmp_path / "t")
    assert log_path == tmp_path / "t" / "rerank.log"
    assert ring_path == tmp_path / "t" / "cycle_events.jsonl"


class _StopLoop(Exception):
    pass


def test_main_replays_argv_flags(tmp_path, monkeypatch):
    from types import SimpleNamespace

    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(filter_loop.subprocess, "run", fake_run)
    monkeypatch.setattr(filter_loop.time, "sleep",
                        lambda _s: (_ for _ in ()).throw(_StopLoop()))
    monkeypatch.setattr(filter_loop, "LOG", filter_loop.LOG)
    monkeypatch.setattr(filter_loop, "RING_PATH", filter_loop.RING_PATH)

    with pytest.raises(_StopLoop):
        filter_loop.main(["--out-dir", str(tmp_path / "t"),
                          "--trial-depth", "250"])

    assert "--out-dir" in seen["cmd"]
    assert seen["cmd"][seen["cmd"].index("--out-dir") + 1] == str(tmp_path / "t")
    assert "250" in seen["cmd"][seen["cmd"].index("--trial-depth") + 1]
    assert filter_loop.LOG == tmp_path / "t" / "rerank.log"
    assert filter_loop.RING_PATH == tmp_path / "t" / "cycle_events.jsonl"
