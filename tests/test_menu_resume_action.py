"""shadow-resume action exists, is wired, and never wipes.

`Resume-ShadowRun` reopens the newest `data/NN_shadow_*.db` under its
original `shadow-NN` run id and restarts the loop/observer/watcher against
it, so a rehearsal interrupted partway (shadow-01 at 38 closes, say) can be
continued toward the 60-close sample target instead of starting a fresh
run. The wiring is text-level in the menu script (the script takes over the
console when dot-sourced, so it cannot be imported), so the tests parse the
source directly. No PowerShell host is needed: these are string assertions,
so they run everywhere and a removal of the resume flow fails the suite on
any runner.
"""
from __future__ import annotations

import re
from pathlib import Path

MENU = Path(__file__).resolve().parents[1] / "scripts" / "spread-hunter-menu.ps1"


def _menu_source() -> str:
    return MENU.read_text(encoding="utf-8")


def _action_map() -> dict[str, str]:
    """Extract the menu's action-name -> key map."""
    src = _menu_source()
    body = src.split("$actionMap = @{", 1)[1].split("}", 1)[0]
    pairs = re.findall(r'"([^"]+)"\s*=\s*"([^"]+)"', body)
    return dict(pairs)


def _branch_source(key: str) -> str:
    """Extract one quoted-key case from the interactive switch."""
    src = _menu_source()
    m = re.search(r'^\s{8}"%s" \{(.*?)(?=^\s{8}"\d"|^\s{8}default \{)' % key,
                  src, re.MULTILINE | re.DOTALL)
    assert m, f'branch "{key}" not found in the menu switch'
    return m.group(1)


def _resume_function_source() -> str:
    src = _menu_source()
    return src.split("function Resume-ShadowRun", 1)[1].split("\nfunction ", 1)[0]


def test_shadow_resume_maps_to_resume_call():
    """The alias must land on the key whose branch calls Resume-ShadowRun."""
    mapping = _action_map()
    assert mapping.get("shadow-resume") == "r"
    assert mapping.get("resume") == "r"
    assert mapping.get("resume-shadow") == "r"
    branch = _branch_source("r")
    assert "Resume-ShadowRun" in branch, "the r branch must invoke Resume-ShadowRun"


def test_resume_derives_run_id_from_newest_store():
    body = _resume_function_source()
    # Newest *_shadow_*.db wins, and the seq prefix becomes the run id.
    assert "*_shadow_*.db" in body
    assert re.search(r"Sort-Object\s+LastWriteTime\s+-Descending", body)
    assert re.search(r"'(\^\\d\{1,2\})_shadow_'", body) or "^\\d{1,2}_shadow_" in body
    assert '"shadow-" + ' in body or "shadow-$" in body


def test_resume_reuses_store_and_run_id_without_wiping():
    body = _resume_function_source()
    # The loop is started against the existing store under the SAME run id.
    assert '"-m", "core_brain.shadow_run", "--minutes"' in body
    assert '"--db", $script:ShadowDbPath, "--run-id", $script:ShadowRunId' in body
    # No wipe: Clear-RuntimeState / Reset-Environment never appear in resume.
    assert "Clear-RuntimeState" not in body
    assert "Reset-Environment" not in body


def test_resume_verifies_previous_processes_stopped():
    """A stop that cannot be proven must abort, not launch on top."""
    body = _resume_function_source()
    assert "Stop-ShadowSession" in body
    assert "Stop-ShadowRun" in body
    # The inconclusive result ($null) aborts, and the loop-kill result is used.
    assert "$runStopped -eq $null" in body
    assert "return $false" in body


def test_resume_timeboxes_screener_and_watcher():
    """filter_loop and global_stop_loss have no duration of their own; the
    resumed session must give them the same detached timer the fresh run
    uses, keyed on PID + start time."""
    body = _resume_function_source()
    assert "$timerTargets" in body
    assert "$screener.Id" in body
    assert "Start-Sleep -Seconds $killSec" in body
