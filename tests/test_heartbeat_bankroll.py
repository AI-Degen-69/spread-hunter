"""Starting capital falls back to the shadow heartbeat's bankroll, not the display default."""
from __future__ import annotations

import json

from core_brain import kpi
from core_brain import shadow_run


def test_heartbeat_bankroll_read(tmp_path, monkeypatch):
    hb = tmp_path / "hb.json"
    hb.write_text(json.dumps({"starting_bankroll_usd": 1000.0}), encoding="utf-8")
    monkeypatch.setattr(shadow_run, "shadow_heartbeat_path", lambda root=None, run_id="": hb)
    assert kpi._heartbeat_bankroll("shadow-x") == 1000.0
    assert kpi._heartbeat_bankroll("all") is None
    assert kpi._heartbeat_bankroll(None) is None


def test_heartbeat_bankroll_missing_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr(shadow_run, "shadow_heartbeat_path",
                        lambda root=None, run_id="": tmp_path / "nope.json")
    assert kpi._heartbeat_bankroll("shadow-x") is None


def test_heartbeat_payload_carries_bankroll(tmp_path):
    out = tmp_path / "hb.json"
    shadow_run.write_shadow_heartbeat(
        db_path=tmp_path / "a.db", run_id="shadow-x", minutes=1.0, interval=1.0,
        started_at=0.0, starting_bankroll_usd=1000.0, path=out)
    assert json.loads(out.read_text(encoding="utf-8"))["starting_bankroll_usd"] == 1000.0
