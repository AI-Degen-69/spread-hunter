"""Unit tests for 10-tier bankroll experiment orchestrator."""
import os
import json
from pathlib import Path
from scripts.launch_bankroll_experiments import build_bankroll_configs, setup_experiment_dirs

def test_build_bankroll_configs():
    configs = build_bankroll_configs(start=100, end=1000, step=100)
    assert len(configs) == 10
    assert configs[0]["bankroll"] == 100
    assert configs[0]["workdir"].name == "bankroll_100"
    assert configs[-1]["bankroll"] == 1000
    assert configs[-1]["workdir"].name == "bankroll_1000"

def test_setup_experiment_dirs(tmp_path):
    configs = [
        {"bankroll": 100, "workdir": tmp_path / "bankroll_100"},
        {"bankroll": 200, "workdir": tmp_path / "bankroll_200"}
    ]
    setup_experiment_dirs(configs)
    assert (tmp_path / "bankroll_100" / "status.json").exists()
    assert (tmp_path / "bankroll_200" / "status.json").exists()
    status_100 = json.loads((tmp_path / "bankroll_100" / "status.json").read_text())
    assert status_100["bankroll"] == 100
    assert status_100["status"] == "INITIALIZED"
