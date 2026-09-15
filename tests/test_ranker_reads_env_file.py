"""The ranker honours a gate trial staged in `.env`, however it is started.

`_effective_volume_bar` already falls back to the config trial when no
`--trial-volume` is given -- but `scripts/filter_markets` never loaded `.env`,
so that config field was always None for a direct run. Only
`scripts/filter_loop` loaded the file, and it compensated by passing the bar
on the command line.

The menu's pre-flight runs `python -m scripts.rank_markets` with NO arguments,
so it gated on the permanent bar and overwrote the screener's trial universe
with an untagged one. For about ten minutes after every start the rehearsal
quoted a universe nobody had selected, until the next screener pass corrected
it. Observed 2026-09-15 18:18: `markets.json` held 3 untagged markets while
`runtime/rerank.log` recorded the screener picking 7 at the staged $50,000.

Process environment still wins over the file (`override=False`), the same
contract `filter_loop` keeps.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _child_bar(env_overrides: dict[str, str]) -> float:
    """The ranker's no-argument volume bar, read in a fresh interpreter.

    A child process because `.env` is loaded once at import, and this suite's
    conftest scrubs the parent's environment.
    """
    import os

    code = textwrap.dedent(f"""
        import os, sys
        sys.path.insert(0, {str(REPO)!r})
        import scripts.filter_markets as fm
        print(fm._effective_volume_bar(None))
    """)
    env = {k: v for k, v in os.environ.items() if not k.startswith("HUNTER_")}
    env.update(env_overrides)
    proc = subprocess.run([sys.executable, "-c", code], cwd=REPO,
                          capture_output=True, text=True, env=env, check=True)
    return float(proc.stdout.strip().splitlines()[-1])


def _bar_with_generated_env(tmp_path: Path, contents: str) -> float:
    """The no-argument volume bar, with `.env` generated at a controlled root.

    The repo's own `.env` is gitignored, so a test that reads it asserts
    nothing on a clean checkout: `_staged_trial_in_env_file()` returns None,
    the expectation collapses to the permanent bar, and the PRE-FIX code
    returned the permanent bar too. That test passed on CI before and after
    the change. This one generates the file, so it can only pass when the
    loader actually reads it.
    """
    import os

    (tmp_path / ".env").write_text(contents, encoding="utf-8")
    code = textwrap.dedent(f"""
        import os, sys
        sys.path.insert(0, {str(REPO)!r})
        import scripts.filter_markets as fm
        import scoring.config
        # Importing the module ran _load_repo_env() against the REAL repo root,
        # so a value staged in the developer's own .env is already in the
        # environment and override=False would never replace it. Clear it: this
        # test controls the file, not the checkout it happens to run in.
        os.environ.pop("HUNTER_VOLUME_TRIAL_USD", None)
        # Re-run the loader against the generated root and rebuild the config
        # exactly as import does, so the fallback chain is exercised end to end.
        fm._load_repo_env({str(tmp_path)!r})
        fm._CFG = scoring.config.load()
        print(fm._effective_volume_bar(None))
    """)
    env = {k: v for k, v in os.environ.items() if not k.startswith("HUNTER_")}
    proc = subprocess.run([sys.executable, "-c", code], cwd=tmp_path,
                          capture_output=True, text=True, env=env, check=True)
    return float(proc.stdout.strip().splitlines()[-1])


def test_a_trial_staged_in_the_env_file_is_honoured(tmp_path):
    """The invariant the menu's pre-flight broke.

    Red before the fix: with no `_load_repo_env`, the staged value never
    reaches the environment and the bar stays at the permanent one.
    """
    # Arrange / Act
    bar = _bar_with_generated_env(tmp_path, "HUNTER_VOLUME_TRIAL_USD=50000\n")

    # Assert
    assert bar == 50000.0


def test_an_env_file_without_a_trial_leaves_the_permanent_bar(tmp_path):
    # Arrange
    from scripts.filter_markets import MIN_VOLUME_24H

    # Act
    bar = _bar_with_generated_env(tmp_path, "SH_TOP_MARKETS=9\n")

    # Assert
    assert bar == MIN_VOLUME_24H


def test_a_missing_env_file_is_not_an_error(tmp_path):
    """An unstaged repo gates on the permanent bars, and does not crash."""
    # Arrange / Act -- _load_repo_env points at a directory with no .env.
    import os

    code = textwrap.dedent(f"""
        import os, sys
        sys.path.insert(0, {str(REPO)!r})
        import scripts.filter_markets as fm
        import scoring.config
        # Importing the module ran _load_repo_env() against the REAL repo root,
        # so a value staged in the developer's own .env is already in the
        # environment and override=False would never replace it. Clear it: this
        # test controls the file, not the checkout it happens to run in.
        os.environ.pop("HUNTER_VOLUME_TRIAL_USD", None)
        fm._load_repo_env({str(tmp_path)!r})
        fm._CFG = scoring.config.load()
        print(fm._effective_volume_bar(None))
    """)
    env = {k: v for k, v in os.environ.items() if not k.startswith("HUNTER_")}
    proc = subprocess.run([sys.executable, "-c", code], cwd=tmp_path,
                          capture_output=True, text=True, env=env, check=True)

    # Assert
    from scripts.filter_markets import MIN_VOLUME_24H
    assert float(proc.stdout.strip().splitlines()[-1]) == MIN_VOLUME_24H


def test_the_process_environment_wins_over_the_file():
    # Arrange / Act -- a bar no `.env` in this repo would ever stage.
    bar = _child_bar({"HUNTER_VOLUME_TRIAL_USD": "77000"})

    # Assert -- override=False, the same contract filter_loop keeps.
    assert bar == 77000.0


def test_a_non_positive_trial_falls_back_to_the_permanent_bar():
    """A zero is a mistake, not an instruction to gate on nothing."""
    # Arrange
    from scripts.filter_markets import MIN_VOLUME_24H

    # Act
    bar = _child_bar({"HUNTER_VOLUME_TRIAL_USD": "0"})

    # Assert
    assert bar == MIN_VOLUME_24H


def test_the_ranker_loads_the_env_file_before_it_builds_its_config():
    """Order matters: `_CFG` is built at import and never rebuilt.

    Parsed with `ast`, not searched as text: a commented-out call still
    contains the string `_load_repo_env()`, so a substring check passes on
    exactly the broken state this asserts against.
    """
    import ast

    module = ast.parse((REPO / "scripts" / "filter_markets.py").read_text(encoding="utf-8"))

    call_line = None
    cfg_line = None
    for node in module.body:                      # module level only
        if (call_line is None and isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "_load_repo_env"):
            call_line = node.lineno
        if (cfg_line is None and isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "_CFG"
                        for t in node.targets)):
            cfg_line = node.lineno

    assert call_line is not None, "_load_repo_env() is never called at module level"
    assert cfg_line is not None, "_CFG is not assigned at module level"
    assert call_line < cfg_line, (
        f"_load_repo_env() at line {call_line} runs after _CFG at line {cfg_line}; "
        "the staged trial would never reach the config")
