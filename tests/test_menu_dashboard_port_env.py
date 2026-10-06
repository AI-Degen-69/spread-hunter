"""Tests that the menu clears a stray PORT env var before spawning dashboards.

`dashboard/server.py` runs `resolve_port(None)` at import time, before `--port`
is parsed. A stray `PORT=0` in the menu shell is inherited by the `Start-Process`
child and crashes it with `ValueError: PORT='0' is outside 1-65535`, which the
menu then misreports as "failed to bind port". The menu must drop `Env:PORT`
for the spawn and restore it afterwards -- never fall back to :8799 in code,
which is the live stack's address.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

MENU = Path(__file__).resolve().parents[1] / "scripts" / "spread-hunter-menu.ps1"


def _menu_source() -> str:
    return MENU.read_text(encoding="utf-8")


def _function_body(name: str) -> str:
    return _menu_source().split(f"function {name} {{", 1)[1].split("\nfunction ", 1)[0]


def test_start_dashboard_clears_stray_port_env():
    """Start-Dashboard must drop Env:PORT around the dashboard spawn."""
    body = _function_body("Start-Dashboard")
    assert "Remove-Item Env:PORT" in body
    assert body.index("Remove-Item Env:PORT") < body.index("Start-Process")


def test_start_dashboard_restores_port_env():
    """Start-Dashboard must restore the caller's PORT after the spawn."""
    body = _function_body("Start-Dashboard")
    assert "$env:PORT = $savedPort" in body
    assert body.index("Start-Process") < body.index("$env:PORT = $savedPort")


def test_start_shadow_dashboard_clears_stray_port_env():
    """Start-ShadowDashboard (resume path) must drop Env:PORT around the spawn."""
    body = _function_body("Start-ShadowDashboard")
    assert "Remove-Item Env:PORT" in body
    assert body.index("Remove-Item Env:PORT") < body.index("Start-Process")


def test_start_shadow_dashboard_restores_port_env():
    """Start-ShadowDashboard must restore the caller's PORT after the spawn."""
    body = _function_body("Start-ShadowDashboard")
    assert "$env:PORT = $savedPort" in body
    assert body.index("Start-Process") < body.index("$env:PORT = $savedPort")


def _run_import_with_port(env_port: str | None) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "PORT"}
    if env_port is not None:
        env["PORT"] = env_port
    return subprocess.run(
        [sys.executable, "-c", "import dashboard.server"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=120,
    )


def test_stray_port_zero_still_refuses_at_import():
    """server.py must keep refusing PORT=0 (no silent fallback to live :8799)."""
    result = _run_import_with_port("0")
    combined = result.stdout + result.stderr
    assert result.returncode != 0
    assert "PORT='0' is outside 1-65535" in combined


def test_import_without_port_succeeds():
    """Dropping PORT (what the menu now does) lets the import succeed."""
    result = _run_import_with_port(None)
    assert result.returncode == 0, result.stderr[-2000:]
