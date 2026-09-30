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
    assert args.paired_depth_control_usd is None
    assert args.paired_admission is False


def test_paired_admission_forwarded(tmp_path):
    cmd = filter_loop._rank_cmd(2, out_dir=tmp_path / "adm",
                                paired_admission=True)
    assert "--paired-admission" in cmd
    assert cmd[cmd.index("--out-dir") + 1] == str(tmp_path / "adm")


def test_paired_admission_loop_flag_requires_isolated_out_dir():
    with pytest.raises(SystemExit):
        filter_loop.parse_args(["--paired-admission"])


def test_rank_cmd_without_flags_carries_no_new_options(monkeypatch):
    from types import SimpleNamespace

    import scoring.config as scoring_config

    monkeypatch.setattr(scoring_config, "load", lambda: SimpleNamespace(
        select_min_top3_depth_usd_trial=None,
        select_min_volume_24h_usd_trial=None))

    cmd = filter_loop._rank_cmd(2)
    assert cmd == [filter_loop.sys.executable, "-m",
                   "scripts.filter_markets", "--top", "2"]


def test_rank_cmd_forwards_out_dir_and_trial_depth(tmp_path):
    cmd = filter_loop._rank_cmd(2, out_dir=tmp_path / "t",
                                trial_depth=250.0)
    assert "--out-dir" in cmd
    assert cmd[cmd.index("--out-dir") + 1] == str(tmp_path / "t")
    assert "--trial-depth" in cmd
    assert cmd[cmd.index("--trial-depth") + 1] == "250.0"


def test_rank_cmd_forwards_paired_control_cutoff(tmp_path):
    cmd = filter_loop._rank_cmd(
        2, out_dir=tmp_path / "pair", trial_depth=250.0,
        paired_depth_control_usd=500.0,
    )

    assert cmd[cmd.index("--paired-depth-control-usd") + 1] == "500.0"


def test_paired_loop_flags_require_explicit_isolated_inputs():
    with pytest.raises(SystemExit):
        filter_loop.parse_args(["--paired-depth-control-usd", "500"])
    with pytest.raises(SystemExit):
        filter_loop.parse_args(["--out-dir", "runtime/trials/pair",
                                "--trial-depth", "250",
                                "--paired-depth-control-usd", "200"])


def test_truncated_paired_universe_raises_a_dedicated_event(tmp_path, monkeypatch, capsys):
    """A paired ranker refusal on a truncated Gamma listing is an environment
    condition, not a ranker crash. The loop must say so: a dedicated
    paired_universe_truncated event beside the generic rerank_error, plus a
    loud banner in the log -- hours of this must never read as routine noise."""
    from types import SimpleNamespace

    events = []
    monkeypatch.setattr(filter_loop, "_emit_scan_event", events.append)
    monkeypatch.setattr(filter_loop, "LOG", tmp_path / "rerank.log")
    monkeypatch.setattr(filter_loop, "_get_top_markets", lambda: 2)

    marker = filter_loop.PAIRED_TRUNCATED_MARKER
    captured = {"cmd": None}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return SimpleNamespace(
            returncode=1,
            stdout="universe: 23 tradable binaries (2 pages, 200 rows, TRUNCATED)",
            stderr=(f"{marker} refusing to publish a paired bundle on a partial "
                    "Gamma listing\n"
                    "SystemExit: paired-depth mode requires a complete Gamma universe"),
        )

    monkeypatch.setattr(filter_loop.subprocess, "run", fake_run)
    monkeypatch.setattr(filter_loop.time, "sleep",
                        lambda _s: (_ for _ in ()).throw(_StopLoop()))

    with pytest.raises(_StopLoop):
        filter_loop.main(["--out-dir", str(tmp_path / "t"),
                          "--trial-depth", "250",
                          "--paired-depth-control-usd", "500"])

    actions = [e["action"] for e in events]
    assert "paired_universe_truncated" in actions
    assert "rerank_error" in actions
    dedicated = next(e for e in events
                     if e["action"] == "paired_universe_truncated")
    assert "environment" in dedicated["reason"]
    out = capsys.readouterr().err
    assert "TRUNCATED" in out
    assert "do NOT loosen gates" in out


def test_ordinary_ranker_failure_does_not_raise_the_paired_event(tmp_path, monkeypatch, capsys):
    """The dedicated event is reserved for the truncation marker: a generic
    ranker crash keeps producing exactly one rerank_error and no paired noise."""
    from types import SimpleNamespace

    events = []
    monkeypatch.setattr(filter_loop, "_emit_scan_event", events.append)
    monkeypatch.setattr(filter_loop, "LOG", tmp_path / "rerank.log")
    monkeypatch.setattr(filter_loop, "_get_top_markets", lambda: 2)

    def fake_run(cmd, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="KeyError: volume24hr")

    monkeypatch.setattr(filter_loop.subprocess, "run", fake_run)
    monkeypatch.setattr(filter_loop.time, "sleep",
                        lambda _s: (_ for _ in ()).throw(_StopLoop()))

    with pytest.raises(_StopLoop):
        filter_loop.main(["--out-dir", str(tmp_path / "t")])

    actions = [e["action"] for e in events]
    assert actions == ["rerank_error"]
    assert "TRUNCATED" not in capsys.readouterr().err


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
    old_log, old_ring = filter_loop.LOG, filter_loop.RING_PATH
    try:
        with pytest.raises(_StopLoop):
            filter_loop.main(["--out-dir", str(tmp_path / "t"),
                              "--trial-depth", "250",
                              "--paired-depth-control-usd", "500"])
    finally:
        filter_loop.LOG, filter_loop.RING_PATH = old_log, old_ring

    assert "--out-dir" in seen["cmd"]
    assert seen["cmd"][seen["cmd"].index("--out-dir") + 1] == str(tmp_path / "t")
    assert seen["cmd"][seen["cmd"].index("--trial-depth") + 1] == "250.0"
    assert seen["cmd"][seen["cmd"].index("--paired-depth-control-usd") + 1] == "500.0"
    assert (tmp_path / "t" / "rerank.log").exists()

def test_explicit_trial_depth_survives_a_config_read_failure(tmp_path, monkeypatch):
    # A config read failure must not silently rank the trial feed at the
    # permanent bar: the explicit CLI flag travels independently of config.
    import scoring.config as scoring_config

    def boom():
        raise RuntimeError("config store unreachable")

    monkeypatch.setattr(scoring_config, "load", boom)
    monkeypatch.setattr(filter_loop, "LOG", tmp_path / "rerank.log")

    cmd = filter_loop._rank_cmd(2, out_dir=tmp_path / "t", trial_depth=250.0)

    assert cmd[cmd.index("--trial-depth") + 1] == "250.0"
    assert cmd[cmd.index("--out-dir") + 1] == str(tmp_path / "t")
