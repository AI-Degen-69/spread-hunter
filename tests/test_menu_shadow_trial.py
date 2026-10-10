"""Resume replays an old trial manifest; the depth-bar trial launcher is gone (#475).

Station II (plan) + CodeRabbit plan for #291 added a `shadow-trial` menu action
that minted a store, seeded `runtime/trials/<run-id>/`, and persisted the feed
choice in `data/<store>.trial.json`; Menu R replayed it. #475 retires that
launcher entirely: no new trial store can be started from the menu. The three
resume tests below are unchanged -- `Resume-ShadowRun` still honours an old
store's manifest, and stores without one resume on the shared feed as before.

The wiring is text-level in the menu script (it takes over the console when
dot-sourced, so it cannot be imported): string assertions, no PowerShell host
needed. The one exception runs the script as a `pwsh` subprocess to prove the
retired aliases are now unknown actions.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

MENU = Path(__file__).resolve().parents[1] / "scripts" / "spread-hunter-menu.ps1"

PWSH = shutil.which("pwsh")  # PowerShell 7+; 5.1 cannot parse this UTF-8 file
requires_pwsh = pytest.mark.skipif(PWSH is None,
                                   reason="pwsh (PowerShell 7) is not installed on this host")


def _menu_source() -> str:
    return MENU.read_text(encoding="utf-8")


def _function_source(name: str) -> str:
    src = _menu_source()
    return src.split(f"function {name}", 1)[1].split("\nfunction ", 1)[0]


def _action_map() -> dict[str, str]:
    src = _menu_source()
    body = src.split("$actionMap = @{", 1)[1].split("}", 1)[0]
    pairs = re.findall(r'"([^"]+)"\s*=\s*"([^"]+)"', body)
    return dict(pairs)


# --- Kept: resume still honours an old trial store's manifest (#291 behaviour) ---


def test_resume_replays_the_trial_manifest():
    body = _function_source("Resume-ShadowRun")
    assert ".trial.json" in body
    assert "--markets-path" in body


def test_resume_without_manifest_keeps_the_baseline_path():
    body = _function_source("Resume-ShadowRun")
    assert "scripts.rank_markets" in body
    assert 'Register-StackService -Key "filter"' in body


def test_manifest_files_are_never_resume_candidates():
    src = _menu_source()
    helper = src.split("function Get-ShadowResumeStores", 1)[1].split("\nfunction ", 1)[0]
    assert '"*_shadow_*.db"' in helper
    assert ".trial.json" not in helper


# --- New: the launcher is gone for good (#475) ---


def test_depth_bar_trial_launcher_is_gone():
    src = _menu_source()
    # No function, definition or call anywhere in the script.
    assert "function Start-ShadowTrial" not in src
    assert "Start-ShadowTrial" not in src
    # The regex \$(script:)?TrialDepth\b skips the resume session field
    # TrialDepthUsd and $trial.trial_depth_usd, both of which stay.
    assert re.search(r"\$(script:)?TrialDepth\b", src) is None
    # No alias maps to a retired "t" key.
    mapping = _action_map()
    assert "shadow-trial" not in mapping
    assert "trial-shadow" not in mapping
    assert "t" not in mapping.values()
    # No switch branch for "t" and the allow-list no longer contains it.
    assert not re.search(r'^\s*"t" \{', src, re.MULTILINE)
    allow = re.search(r'if \(@\((.*?)\) -notcontains \$key\)', src, re.DOTALL)
    assert allow, "allow-list check not found"
    assert '"t"' not in allow.group(1)
    # The grid label is gone too.
    assert "Depth-Bar Trial" not in src


@requires_pwsh
@pytest.mark.parametrize("word", ["shadow-trial", "trial-shadow", "t"])
def test_trial_aliases_are_unknown_actions(word):
    # After the deletions these words fall through to the existing
    # "Unknown action" check and exit non-zero.
    res = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(MENU), word],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    combined = res.stdout + res.stderr
    assert res.returncode != 0
    assert "Unknown action" in combined
