"""Every module the live branch of `live_exec` defers must actually import.

`quote` imports `strategy.markets` before its dry-run return and
`strategy.order_registry` after it. A `strategy` namespace that resolves only
half of its two directories therefore prints a clean dry run and raises
ModuleNotFoundError on the `--live` call -- the operator sees the order
described, approves it, and the crash lands where a real order was about to go.

That happened on 2026-08-18 running `python -m live.strategy.live_exec` from the
repo root. These tests exercise the deferred imports directly so the failure
surfaces in CI instead of at the venue.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIVE_EXEC = REPO_ROOT / "live" / "strategy" / "live_exec.py"


def _deferred_strategy_imports() -> set[str]:
    """Module names `live_exec` imports from the `strategy` namespace."""
    src = LIVE_EXEC.read_text(encoding="utf-8")
    names = set(re.findall(r"^\s*from strategy\.(\w+) import", src, re.MULTILINE))
    names |= set(re.findall(r"^\s*from strategy import (\w+)", src, re.MULTILINE))
    return names


def test_deferred_imports_are_discovered():
    """Guard the regex: if it silently matches nothing, the tests below pass vacuously."""
    names = _deferred_strategy_imports()
    assert {"order_registry", "markets", "live_pairs", "config"} <= names, names


@pytest.mark.parametrize("cwd", ["repo_root", "live"])
def test_live_branch_imports_resolve(cwd):
    """Import every deferred module, from both working directories an operator uses."""
    names = sorted(_deferred_strategy_imports())
    prog = "import live.strategy.live_exec\n" if cwd == "repo_root" else "import strategy.live_exec\n"
    prog += "".join(f"import strategy.{n}\n" for n in names)
    prog += "print('ok')"

    res = subprocess.run(
        [sys.executable, "-c", prog],
        cwd=str(REPO_ROOT if cwd == "repo_root" else REPO_ROOT / "live"),
        capture_output=True, text=True,
    )
    assert res.returncode == 0, f"cwd={cwd}\n{res.stderr}"
    assert "ok" in res.stdout


def test_strategy_namespace_spans_both_directories():
    """The engine and the execution path share one package name across two trees."""
    res = subprocess.run(
        [sys.executable, "-c",
         "import live.strategy.live_exec, strategy; print('\\n'.join(strategy.__path__))"],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr
    paths = {Path(p).resolve() for p in res.stdout.strip().splitlines() if p.strip()}
    assert (REPO_ROOT / "strategy").resolve() in paths, paths
    assert (REPO_ROOT / "live" / "strategy").resolve() in paths, paths
