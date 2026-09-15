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
        import sys
        sys.path.insert(0, {str(REPO)!r})
        import scripts.filter_markets as fm
        print(fm._effective_volume_bar(None))
    """)
    env = {k: v for k, v in os.environ.items() if not k.startswith("HUNTER_")}
    env.update(env_overrides)
    proc = subprocess.run([sys.executable, "-c", code], cwd=REPO,
                          capture_output=True, text=True, env=env, check=True)
    return float(proc.stdout.strip().splitlines()[-1])


def _staged_trial_in_env_file() -> float | None:
    """`HUNTER_VOLUME_TRIAL_USD` as the repo's own `.env` stages it, if at all."""
    env_file = REPO / ".env"
    if not env_file.exists():
        return None
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("HUNTER_VOLUME_TRIAL_USD="):
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            try:
                return float(value)
            except ValueError:
                return None
    return None


def test_the_ranker_agrees_with_the_env_file_it_is_started_beside():
    """Whatever `.env` stages, a no-argument ranker run must gate on it.

    This is the invariant the menu's pre-flight broke: the screener read the
    file and the pre-flight did not, so the two wrote different universes.
    """
    # Arrange
    from scripts.filter_markets import MIN_VOLUME_24H
    staged = _staged_trial_in_env_file()
    expected = staged if (staged is not None and staged > 0) else MIN_VOLUME_24H

    # Act
    bar = _child_bar({})

    # Assert
    assert bar == expected


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
    """Order matters: `_CFG` is built at import and never rebuilt."""
    src = (REPO / "scripts" / "filter_markets.py").read_text(encoding="utf-8")
    assert "_load_repo_env()" in src
    assert src.index("_load_repo_env()") < src.index("_CFG = _load_cfg()")
