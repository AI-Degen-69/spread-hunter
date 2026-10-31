from __future__ import annotations

import json
import sys
from pathlib import Path


class _FakeProc:
    def __init__(self, label: str):
        self.label = label
        self.pid = 9999
        self.terminated = False
        self.killed = False
        self._polled = 0

    def poll(self):
        # Allow 1 poll as alive so loop executes once, then report terminated for arms
        self._polled += 1
        if self.label == "market-filter":
            return None  # Filter stays alive indefinitely until stopped
        return 0 if self._polled >= 2 else None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return 0


def test_resume_tournament_starts_and_stops_market_filter(tmp_path: Path, monkeypatch):
    import scripts.resume_tournament_arms as rta

    spawned: list[tuple[list[str], dict | None, _FakeProc]] = []

    def fake_popen(argv, env=None, **kwargs):
        label = "market-filter" if "filter_loop" in " ".join(argv) else "arm"
        proc = _FakeProc(label)
        spawned.append((list(argv), env.copy() if env is not None else None, proc))
        return proc

    monkeypatch.setattr(rta.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(rta, "clear_instance_lock", lambda p: None)

    plan_file = tmp_path / "plan.json"
    plan_data = {
        "tournament_id": "test-tourney",
        "arms": [
            {
                "index": 1,
                "name": "control",
                "db_path": str(tmp_path / "arm1.db"),
                "run_id": "shadow-arm1",
                "dash_port": 8801,
                "env": {"TEST_VAR": "1"},
            },
            {
                "index": 2,
                "name": "treatment",
                "db_path": str(tmp_path / "arm2.db"),
                "run_id": "shadow-arm2",
                "dash_port": 8802,
                "env": {"TEST_VAR": "2"},
            },
        ],
    }
    plan_file.write_text(json.dumps(plan_data), encoding="utf-8")

    rc = rta.resume_tournament(plan_path=plan_file, max_markets=5, minutes=60.0, poll_interval=0.01)
    assert rc == 0

    filter_calls = [(a, e, p) for a, e, p in spawned if "filter_loop" in " ".join(a)]
    assert len(filter_calls) == 1, "Market filter must be spawned exactly once"
    argv, env, filter_proc = filter_calls[0]
    assert argv == [sys.executable, "-m", "scripts.filter_loop"]
    assert env is not None
    assert env.get("SH_FILTER_INTERVAL_SEC") == str(rta.FILTER_INTERVAL_SEC)
    assert filter_proc.terminated or filter_proc.killed, "Filter must be terminated upon completion"

    arm_calls = [(a, e, p) for a, e, p in spawned if "core_brain.shadow_run" in " ".join(a)]
    assert len(arm_calls) == 2, "Both arms must be spawned"
    assert "--max-markets" in arm_calls[0][0]
    idx = arm_calls[0][0].index("--max-markets")
    assert arm_calls[0][0][idx + 1] == "5"


def test_resume_tournament_honors_filter_interval_env_override(tmp_path: Path, monkeypatch):
    import scripts.resume_tournament_arms as rta

    spawned: list[tuple[list[str], dict | None, _FakeProc]] = []

    def fake_popen(argv, env=None, **kwargs):
        label = "market-filter" if "filter_loop" in " ".join(argv) else "arm"
        proc = _FakeProc(label)
        spawned.append((list(argv), env.copy() if env is not None else None, proc))
        return proc

    monkeypatch.setattr(rta.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(rta, "clear_instance_lock", lambda p: None)
    monkeypatch.setenv("SH_FILTER_INTERVAL_SEC", "300")

    plan_file = tmp_path / "plan.json"
    plan_data = {
        "tournament_id": "test-tourney",
        "arms": [
            {
                "index": 1,
                "name": "control",
                "db_path": str(tmp_path / "arm1.db"),
                "run_id": "shadow-arm1",
                "dash_port": 8801,
            },
        ],
    }
    plan_file.write_text(json.dumps(plan_data), encoding="utf-8")

    rc = rta.resume_tournament(plan_path=plan_file, max_markets=3, minutes=10.0, poll_interval=0.01)
    assert rc == 0

    filter_calls = [(a, e, p) for a, e, p in spawned if "filter_loop" in " ".join(a)]
    assert len(filter_calls) == 1
    assert filter_calls[0][1].get("SH_FILTER_INTERVAL_SEC") == "300"
