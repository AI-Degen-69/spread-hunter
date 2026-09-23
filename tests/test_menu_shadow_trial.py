"""shadow-trial launches shadow-03 on its own feed; resume replays the manifest.

Station II (plan) + CodeRabbit plan for #291: a non-destructive `shadow-trial`
action mints the next store/run id, seeds `runtime/trials/<run-id>/` with
`--trial-depth`/`--out-dir`, launches the shadow loop with `--markets-path`,
and persists the feed choice in `data/<store>.trial.json`. Menu R replays the
manifest; stores without one resume exactly as before.

The wiring is text-level in the menu script (it takes over the console when
dot-sourced, so it cannot be imported): string assertions, no PowerShell host
needed.
"""
from __future__ import annotations

import re
from pathlib import Path

MENU = Path(__file__).resolve().parents[1] / "scripts" / "spread-hunter-menu.ps1"


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


def _branch_source(key: str) -> str:
    src = _menu_source()
    m = re.search(r'^\s{8}"%s" \{(.*?)(?=^\s{8}"\d"|^\s{8}"[a-z]|^\s{8}default \{)' % key,
                  src, re.MULTILINE | re.DOTALL)
    assert m, f'branch "{key}" not found in the menu switch'
    return m.group(1)


def test_shadow_trial_action_is_wired():
    mapping = _action_map()
    assert mapping.get("shadow-trial") == "t"
    branch = _branch_source("t")
    assert "Start-ShadowTrial" in branch


def test_shadow_trial_takes_a_depth_with_250_default():
    src = _menu_source()
    assert "[double]$TrialDepth = 250" in src
    body = _function_source("Start-ShadowTrial")
    assert "--trial-depth" in body


def test_shadow_trial_passes_isolation_flags():
    body = _function_source("Start-ShadowTrial")
    assert "--out-dir" in body
    assert "--markets-path" in body
    assert "runtime/trials/" in body.replace("\\", "/")


def test_shadow_trial_is_non_destructive():
    body = _function_source("Start-ShadowTrial")
    assert "Reset-Environment" not in body
    assert "Clear-RuntimeState" not in body
    assert "Get-NextShadowSeq" in body


def test_shadow_trial_writes_the_manifest_not_the_global_filter():
    body = _function_source("Start-ShadowTrial")
    assert ".trial.json" in body
    assert "trial_depth_usd" in body
    assert "ranker_out_dir" in body
    assert "markets_path" in body
    assert 'Register-StackService -Key "filter"' not in body


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
