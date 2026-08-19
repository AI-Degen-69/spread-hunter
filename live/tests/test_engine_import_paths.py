"""Every module the live branch of `live_exec` defers must actually import.

`quote` imports `engine.markets` BEFORE its dry-run return and
`engine.order_registry` AFTER it. A dry run therefore exercises the first and
never the second: if the second cannot resolve, the operator sees a clean dry
run, approves the order, and the crash lands on the `--live` call -- at the
venue, with money committed.

That is not hypothetical. On 2026-08-18 `python -m live.strategy.live_exec
quote ... --live` from the repo root died with `ModuleNotFoundError: No module
named 'strategy.order_registry'` after a dry run of the same command had
printed cleanly.

The package has since been renamed from `strategy` to `engine` so it can no
longer merge with the simulation package at the repo root, and `live_exec`
bootstraps `live/` -- and only `live/` -- onto `sys.path`. These tests exercise
the deferred imports through both invocations an operator actually uses, so the
failure surfaces in CI rather than at the venue.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

LIVE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = LIVE_ROOT.parent
LIVE_EXEC = LIVE_ROOT / "engine" / "live_exec.py"


def _deferred_engine_imports() -> set[str]:
    """Module names `live_exec` imports from the `engine` package."""
    src = LIVE_EXEC.read_text(encoding="utf-8")
    names = set(re.findall(r"^\s*from engine\.(\w+) import", src, re.MULTILINE))
    names |= set(re.findall(r"^\s*from engine import (\w+)", src, re.MULTILINE))
    return names


def test_deferred_imports_are_discovered():
    """Guard the regex: if it silently matches nothing, the tests below pass vacuously."""
    names = _deferred_engine_imports()
    assert {"order_registry", "markets", "live_pairs", "config"} <= names, names


# The two ways this module is actually launched. `-m` from live/ is the
# documented form; running the file by path from the repo root is what happens
# when someone has the repo open and does not want to change directory first.
INVOCATIONS = {
    "module_from_live": (LIVE_ROOT, [sys.executable, "-c"]),
    "script_from_repo_root": (REPO_ROOT, [sys.executable, "-c"]),
}


@pytest.mark.parametrize("cwd_name", sorted(INVOCATIONS))
def test_live_branch_imports_resolve(cwd_name):
    """Import every deferred module, from both working directories an operator uses."""
    cwd, argv = INVOCATIONS[cwd_name]
    names = sorted(_deferred_engine_imports())

    # Reach live_exec the way the launcher does -- by putting its own directory
    # first, exactly as `-m` and script execution both do -- then let the
    # module's own sys.path bootstrap resolve the rest.
    prog = (
        "import sys\n"
        f"sys.path.insert(0, {str(LIVE_ROOT)!r})\n"
        "import engine.live_exec\n"
    )
    prog += "".join(f"import engine.{n}\n" for n in names)
    prog += "print('ok')"

    res = subprocess.run(argv + [prog], cwd=str(cwd), capture_output=True, text=True)
    assert res.returncode == 0, f"cwd={cwd_name}\n{res.stderr}"
    assert "ok" in res.stdout


def test_engine_resolves_only_inside_live():
    """`engine` is a regular package in exactly one directory.

    Its predecessor was an implicit namespace package spanning `live/strategy`
    and `<repo>/strategy`, which is how live code came to import simulation
    modules without saying so.
    """
    res = subprocess.run(
        [sys.executable, "-c",
         f"import sys; sys.path.insert(0, {str(LIVE_ROOT)!r}); "
         "import engine; print(*engine.__path__, sep=chr(10))"],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr
    paths = [Path(p).resolve() for p in res.stdout.strip().splitlines() if p.strip()]
    assert paths == [(LIVE_ROOT / "engine").resolve()], paths


def test_importing_live_exec_does_not_put_the_repo_root_on_sys_path():
    """The bootstrap adds `live/` only.

    Adding the repo root as well would make the simulation importable from live
    code, which is how the two `strategy` trees merged in the first place. This
    runs from the repo root -- the cwd most likely to smuggle it back in.
    """
    res = subprocess.run(
        [sys.executable, "-c",
         f"import sys; sys.path.insert(0, {str(LIVE_ROOT)!r}); "
         "import engine.live_exec; "
         "import pathlib; "
         "print(*[pathlib.Path(p).resolve() for p in sys.path if p], sep=chr(10))"],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr
    entries = {Path(p) for p in res.stdout.strip().splitlines() if p.strip()}
    assert LIVE_ROOT.resolve() in entries, entries
    assert REPO_ROOT.resolve() not in entries, (
        f"repo root is on sys.path; live code can reach the simulation\n{entries}"
    )
