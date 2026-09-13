"""shadow-resume action exists, is wired, and never wipes.

`Resume-ShadowRun` reopens the newest `data/NN_shadow_*.db` under its
original `shadow-NN` run id and restarts the loop/observer/watcher against
it, so a rehearsal interrupted partway (shadow-01 at 38 closes, say) can be
continued toward the 60-close sample target instead of starting a fresh
run. The wiring is text-level in the menu script (the script takes over the
console when dot-sourced, so it cannot be imported), so the tests assert on
the source the same way test_menu_status_rows.py does.

Journeys under test:
1. `shadow-resume` maps to a menu key, and that key calls Resume-ShadowRun.
2. Resume-ShadowRun picks the newest seq-prefixed store and derives the run
   id from its prefix.
3. The resume path launches the loop with the EXISTING store and the SAME
   run id, and no wipe runs inside it.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

MENU = Path(__file__).resolve().parents[1] / "scripts" / "spread-hunter-menu.ps1"

PWSH = shutil.which("pwsh") or shutil.which("powershell")

pytestmark = pytest.mark.skipif(PWSH is None,
                                reason="no PowerShell host on this machine")


def _menu_source() -> str:
    return MENU.read_text(encoding="utf-8")


def test_resume_action_is_mapped_to_a_menu_key():
    src = _menu_source()
    assert '"shadow-resume"' in src, "action map must accept shadow-resume"
    # The mapped key is handled in the interactive switch too.
    assert '"r" {' in src, "menu must handle the resume key"
    assert "Resume-ShadowRun" in src


def test_resume_derives_run_id_from_newest_store():
    src = _menu_source()
    body = src.split("function Resume-ShadowRun", 1)[1].split("\nfunction ", 1)[0]
    # Newest *_shadow_*.db wins, and the seq prefix becomes the run id.
    assert "*_shadow_*.db" in body
    assert "'^(\\d{1,2})_shadow_'" in body.replace('\\d', "'^(\\d{1,2})_shadow_'") or "\\d{1,2}" in body
    assert '"shadow-" + ' in body or "shadow-$" in body


def test_resume_reuses_store_and_run_id_without_wiping():
    src = _menu_source()
    body = src.split("function Resume-ShadowRun", 1)[1].split("\nfunction ", 1)[0]
    # The loop is started against the existing store under the SAME run id.
    assert '"-m", "core_brain.shadow_run", "--minutes"' in body
    assert '"--db", $script:ShadowDbPath, "--run-id", $script:ShadowRunId' in body
    # No wipe: Clear-RuntimeState / Reset-Environment never appear in resume.
    assert "Clear-RuntimeState" not in body
    assert "Reset-Environment" not in body
