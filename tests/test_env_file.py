"""Env-file lookup interface tests (#805).

`core_brain.env_file.find_env_file` is the one seam both the Order Manager
CLI and the Dashboard server use to locate the repo `.env`: the nearest
`.env` walking up from a start directory, stopping at the AGENTS.md
boundary. The dashboard must never import the heavy CLI module (importing it
would load signing keys into the dashboard process), so the lookup lives in
a light module with no side effects.
"""
from __future__ import annotations

from pathlib import Path

from core_brain.env_file import find_env_file


def _tree(root: Path) -> Path:
    """A fake repo: <root>/.env plus AGENTS.md boundary, nested start dir."""
    (root / ".env").write_text("BOT_X=1\n", encoding="utf-8")
    (root / "AGENTS.md").write_text("# agents\n", encoding="utf-8")
    start = root / "core_brain"
    start.mkdir()
    return start


def test_finds_nearest_env_walking_up(tmp_path):
    assert find_env_file(_tree(tmp_path)) == tmp_path / ".env"


def test_finds_env_at_start_dir(tmp_path):
    start = _tree(tmp_path)
    (start / ".env").write_text("BOT_X=2\n", encoding="utf-8")
    assert find_env_file(start) == start / ".env"


def test_missing_env_returns_none(tmp_path):
    start = tmp_path / "core_brain"
    start.mkdir()
    (tmp_path / "AGENTS.md").write_text("# agents\n", encoding="utf-8")
    assert find_env_file(start) is None


def test_stops_at_agents_boundary(tmp_path):
    """A stranger's .env above the boundary is never picked up."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / ".env").write_text("BOT_X=9\n", encoding="utf-8")
    repo = outside / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("# agents\n", encoding="utf-8")
    start = repo / "core_brain"
    start.mkdir()
    assert find_env_file(start) is None


def test_never_walks_out_of_a_boundaryless_tree(tmp_path):
    """Without AGENTS.md, the walk is still capped and will not claim a
    far-away .env (here: the real repo's own .env, if any)."""
    (tmp_path / ".env").write_text("BOT_X=1\n", encoding="utf-8")
    deep = tmp_path / "a" / "b" / "c" / "d" / "e"
    deep.mkdir(parents=True)
    assert find_env_file(deep) is None


def test_helper_has_no_side_effects():
    """Importing the helper in a fresh process must not pull in dotenv,
    the Order Manager CLI, or the Dashboard server — the dashboard's whole
    reason for a mirror is keeping signing keys out of its process."""
    import subprocess
    import sys

    probe = (
        "import core_brain.env_file, sys; "
        "mods = set(sys.modules); "
        "assert 'dotenv' not in mods, 'dotenv loaded'; "
        "assert 'core_brain.order_manager' not in mods, 'CLI loaded'; "
        "assert 'dashboard.server' not in mods, 'dashboard loaded'; "
        "print('clean')"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True, text=True, check=False,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "clean"
