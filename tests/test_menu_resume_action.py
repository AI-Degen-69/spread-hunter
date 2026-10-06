"""shadow-resume action exists, is wired, and never wipes.

`Resume-ShadowRun` reopens the PINNED rehearsal store
`data/01_shadow_12-09_00-58.db` under its original `shadow-01` run id and
restarts the loop/observer/watcher against it, so the long-running rehearsal
(83 closes and counting) can always be continued with one menu press — never
capturing a newer store that merely happens to have the freshest mtime.
`-ResumeDb <path>` overrides the store for one launch. The wiring is
text-level in the menu script (the script takes over the console when
dot-sourced, so it cannot be imported), so the tests parse the source
directly. No PowerShell host is needed: these are string assertions, so they
run everywhere and a removal of the resume flow fails the suite on any
runner.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

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
    assert mapping.get("resume-01") == "r"
    branch = _branch_source("r")
    assert "Resume-ShadowRun" in branch, "the r branch must invoke Resume-ShadowRun"


def test_resume_pins_the_owner_store_and_run_id():
    """Option R always reopens 01_shadow_12-09_00-58.db as shadow-01.

    The Owner's standing choice (2026-09-23): resume must never silently
    grab whatever store has the newest mtime — a live trial DB being written
    right now must not steal the resume.
    """
    body = _resume_function_source()
    # The pinned default is defined once, as a project path.
    src = _menu_source()
    assert '$script:DefaultResumeDb = Join-Path $ProjectPath "data/01_shadow_12-09_00-58.db"' in src
    # The no-override path resolves that constant and fixes the run id.
    assert "Get-Item -LiteralPath $script:DefaultResumeDb" in body
    assert '$script:ShadowRunId = "shadow-01"' in body
    # A missing pinned store aborts instead of falling back to newest.
    assert "Pinned rehearsal store not found" in body


def test_resume_db_override_keeps_seq_prefix_run_id():
    """-ResumeDb overrides the store for one launch, run id from its seq prefix."""
    body = _resume_function_source()
    assert "$ResumeDb -ne" in body
    assert 'if ($db.BaseName -match \'^(\\d{1,2})_shadow_\')' in body
    assert '"shadow-" + ' in body
    assert "Resume store not found" in body


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


def test_resume_validates_the_store_before_stopping_anything():
    """A missing store must abort with the current rehearsal still running.

    The store lookup and the production-registry refusal sit above the teardown
    in the function body: if they fired after Stop-ShadowSession/Stop-ShadowRun,
    a typo'd -ResumeDb would stop the operator's live rehearsal and dashboard
    and then leave nothing running.
    """
    body = _resume_function_source()
    first_stop = body.find("Stop-ShadowSession")
    assert first_stop != -1
    assert body.find("Resume store not found") < first_stop
    assert body.find("Pinned rehearsal store not found") < first_stop
    assert body.find("is the production registry") < first_stop


def test_resume_refuses_the_production_registry_before_launching():
    """Only the Python loop guards data/orders.db (shadow_guard); the menu must
    refuse it too, before the dashboard, observer or watcher are pointed at it.
    Separators normalize, and an unresolvable path refuses rather than passes.
    """
    body = _resume_function_source()
    assert 'Join-Path $ProjectPath "data/orders.db"' in body
    assert "GetFullPath" in body
    assert "Replace('/', '\\')" in body
    assert '-ieq $prodFull' in body
    assert "must never enter the real order history" in body
    # The refusal fires before anything is launched.
    assert body.find("-ieq $prodFull") < body.find("Start-ShadowDashboard")


def test_resume_timeboxes_screener_and_watcher():
    """filter_loop and global_stop_loss have no duration of their own; the
    resumed session must give them the same detached timer the fresh run
    uses, keyed on PID + start time."""
    body = _resume_function_source()
    assert "$timerTargets" in body
    assert "$screener.Id" in body
    assert "Start-Sleep -Seconds $killSec" in body


def test_resume_default_duration_is_24_hours():
    """Resume keeps the 24h default through the shared duration resolver."""
    src = _menu_source()
    body = _resume_function_source()
    assert "$mins = Resolve-ShadowMinutes -RequestedMinutes $Minutes" in body
    assert "if ($RequestedMinutes -eq 0) { return 1440.0 }" in src


def test_resume_registers_the_screener_in_the_process_file():
    """The dashboard reads runtime/processes.json for the Market Scan state.

    A resume that starts filter_loop without recording it there leaves the
    previous run's dead PID in place, and the dashboard reports "Market Scan
    stopped" while the screener is scanning every ten minutes.
    """
    body = _resume_function_source()
    assert 'Register-StackService -Key "filter" -Process $screener' in body
    src = _menu_source()
    assert "function Register-StackService" in src
    helper = src.split("function Register-StackService", 1)[1]
    helper = helper.split(chr(10) + "function ", 1)[0]
    # Merge, never overwrite: starting_account_value lives in the same file.
    assert "$existing.PSObject.Properties" in helper
    assert "started_at" in helper
    # The pre-rename key must not shadow the fresh one in Get-ServiceEntry.
    assert "$saved.Remove($legacyKey)" in helper


def test_register_stack_service_publishes_atomically():
    """The dashboard polls processes.json while we write it.

    A direct Set-Content over the live path is visible half-finished, and
    start_bot() reads a truncated registry as permission to launch a second
    stack. Write to a temp file in the same directory, then rename.
    """
    src = _menu_source()
    helper = src.split("function Register-StackService", 1)[1]
    helper = helper.split(chr(10) + "function ", 1)[0]
    assert "$tmp = " in helper
    assert "Move-Item -LiteralPath $tmp -Destination $ProcsFile -Force" in helper
    # No write straight at the live registry.
    assert "Set-Content -Path $ProcsFile" not in helper
    # The temp file never survives a failed publish.
    assert "Remove-Item $tmp -Force" in helper


def _resume_stores_helper_source() -> str:
    src = _menu_source()
    return src.split("function Get-ShadowResumeStores", 1)[1].split("\nfunction ", 1)[0]


def test_resume_candidates_helper_derives_run_ids_from_store_names():
    """R must know which runs exist before asking: one helper lists every
    data/NN_shadow_*.db store with its shadow-NN run id, so 01 and 02 (and
    later runs) are discovered, never hardcoded."""
    src = _menu_source()
    assert "function Get-ShadowResumeStores" in src
    helper = _resume_stores_helper_source()
    assert r"^(\d{1,2})_shadow_" in helper
    assert '"shadow-"' in helper


def test_r_branch_lists_available_runs_and_offers_all():
    """With 01 and 02 on disk, R lists what is available and lets the
    operator resume one run or all of them — in clear English."""
    branch = _branch_source("r")
    assert "Get-ShadowResumeStores" in branch
    assert "All" in branch
    assert "Resume-ShadowRun" in branch


def test_r_branch_resume_db_all_resumes_every_store():
    """-ResumeDb all (non-interactive) resumes every candidate store instead
    of only the pinned 01."""
    branch = _branch_source("r")
    assert '"all"' in branch


def test_resume_candidates_exclude_the_zero_prefix():
    """`00` has no shadow dashboard port, so it must never be a candidate —
    otherwise an "all" resume aborts before reaching the valid stores."""
    helper = _resume_stores_helper_source()
    assert "'00'" in helper


def test_resume_candidates_dedup_a_shared_run_number_to_the_newest():
    """Two files with the same run number (sequence wrapped past 99) share
    one run id — only the newest store is a candidate."""
    helper = _resume_stores_helper_source()
    assert "LastWriteTime" in helper


def test_r_branch_resumes_each_store_in_script_scope_and_reports_failures():
    """The r branch runs inside Invoke-LiveAction while Resume-ShadowRun
    reads script-level state, so each pick must cross that scope boundary —
    and a failed store must be reported, never silently skipped."""
    branch = _branch_source("r")
    assert "$script:ResumeDb" in branch
    assert "failedResumes" in branch
    assert "Resume incomplete" in branch


def test_r_branch_streams_only_the_last_run_log():
    """-Watch with several runs must not block the first resume on
    Get-Content -Wait while later runs never start."""
    branch = _branch_source("r")
    assert "last resumed run" in branch


def test_dashboard_startup_bind_timeout_and_orphan_cleanup():
    """Start-Dashboard and Start-ShadowDashboard must allow 45s for large shadow
    stores to bind without false timeouts, and must terminate the spawned
    process if binding fails or times out so no orphans linger."""
    src = _menu_source()
    start_dash = src.split("function Start-Dashboard {", 1)[1].split("\nfunction ", 1)[0]
    start_shadow_dash = src.split("function Start-ShadowDashboard {", 1)[1].split("\nfunction ", 1)[0]

    for body in (start_dash, start_shadow_dash):
        assert ".AddSeconds(45)" in body, "bind timeout must be at least 45s for large shadow DBs"
        assert "taskkill /T /F /PID" in body, "timed-out dashboard must be terminated to prevent orphans"


def test_r_branch_supports_prudent_preset():
    """Menu option R offers [P] Prudent Hybrid preset and wires it to shadow-01."""
    branch = _branch_source("r")
    assert "[P] Prudent Hybrid" in branch
    assert "$script:ShadowPreset" in branch
    assert '"prudent"' in branch

    src = _menu_source()
    assert "[string]$Preset" in src
    assert "[switch]$Prudent" in src


def test_r_branch_validates_preset_before_stopping_session():
    """An invalid -Preset is rejected early before touching any session or store."""
    branch = _branch_source("r")
    assert "$validPresets" in branch
    assert "Unknown preset" in branch
    assert branch.find("Unknown preset") < branch.find("Resume-ShadowRun")


def test_resume_rehearsal_env_passes_and_cleans_tournament_preset():
    """Invoke-WithRehearsalTrialEnv injects HUNTER_TOURNAMENT_PRESET and cleans up in finally."""
    src = _menu_source()
    helper = src.split("function Invoke-WithRehearsalTrialEnv {", 1)[1].split("\nfunction ", 1)[0]
    # Injects before execution
    assert 'Set-Item "Env:HUNTER_TOURNAMENT_PRESET" $script:ShadowPreset' in helper
    # Execution wrapped in try
    assert 'try { & $Action }' in helper
    # Cleanup happens unconditionally in finally
    assert 'finally {' in helper
    assert 'Remove-Item "Env:HUNTER_TOURNAMENT_PRESET" -ErrorAction SilentlyContinue' in helper


def test_resume_stores_distinguishes_standard_and_prudent_runs():
    """Get-ShadowResumeStores discovers both shadow-NN and shadow-NN-prudent runs."""
    helper = _resume_stores_helper_source()
    assert '_shadow_prudent' in helper
    assert '"-prudent"' in helper


def test_resume_shadow_run_derives_prudent_run_id_and_preset():
    """Resume-ShadowRun maps NN_shadow_prudent to shadow-NN-prudent and prudent preset."""
    body = _resume_function_source()
    assert '_shadow_prudent' in body
    assert '"-prudent"' in body
    assert '$script:ShadowPreset = "prudent"' in body


def test_option_4_invokes_start_new_shadow_run():
    """Option 4 starts a fresh shadow run with preset support and without wiping stores."""
    src = _menu_source()
    assert "function Start-NewShadowRun" in src
    branch = _branch_source("4")
    assert "Start-NewShadowRun" in branch
    assert "Clear-RuntimeState" not in branch
    assert "Reset-Environment" not in branch


def test_menu_duration_contract_keeps_default_finite_and_accepts_unlimited():
    src = _menu_source()
    helper = src.split("function Resolve-ShadowMinutes {", 1)[1].split(chr(10) + "}", 1)[0]
    assert "if ($RequestedMinutes -lt 0) { return -1.0 }" in helper
    assert "if ($RequestedMinutes -eq 0) { return 1440.0 }" in helper
    assert "default 1440 / 24h; -1 explicitly runs until stopped" in src
    assert "-1 until stopped" in src
    assert r"(?:-1|[0-9]+(?:\.[0-9]+)?)" in src


@pytest.mark.skipif(shutil.which("pwsh") is None,
                    reason="no PowerShell 7 host on this machine")
def test_menu_prompt_keeps_decimal_minutes_short():
    """Regression: "1.2" at the duration prompt must not become a 24h run."""
    script = chr(10).join([
        "$ErrorActionPreference = 'Stop'",
        "$out = @()",
        "foreach ($resp in @('1.2', 'abc', '-1', '', '5')) {",
        "  $mins = 0",
        r"  if ($resp -and $resp -match '^\s*(?:-1|[0-9]+(?:\.[0-9]+)?)\s*$') { $mins = [double]$resp }",
        "  if ($mins -eq 0) { $mins = 1440 }",
        "  $out += [int]$mins",
        "}",
        "ConvertTo-Json -InputObject @($out) -Compress",
    ])
    result = subprocess.run(
        [shutil.which("pwsh"), "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, check=True, encoding="utf-8",
    )

    assert json.loads(result.stdout) == [1, 1440, -1, 1440, 5]


@pytest.mark.skipif(shutil.which("pwsh") is None,
                    reason="no PowerShell 7 host on this machine")
def test_menu_duration_helper_keeps_zero_finite_and_negative_unlimited():
    src = _menu_source()
    body = src.split("function Resolve-ShadowMinutes {", 1)[1].split(chr(10) + "}", 1)[0]
    script = chr(10).join([
        "$ErrorActionPreference = 'Stop'",
        "function Resolve-ShadowMinutes {" + body + "}",
        "$minutes = @(-1, 0, 17) | ForEach-Object { Resolve-ShadowMinutes -RequestedMinutes $_ }",
        "ConvertTo-Json -InputObject @($minutes) -Compress",
    ])
    result = subprocess.run(
        [shutil.which("pwsh"), "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, check=True, encoding="utf-8",
    )

    assert json.loads(result.stdout) == [-1, 1440, 17]


def test_every_shadow_launcher_disables_timeouts_only_for_unlimited_runs():
    src = _menu_source()
    for name in ("Resume-ShadowRun", "Start-ShadowTrial", "Start-NewShadowRun"):
        body = src.split(f"function {name} {{", 1)[1].split(chr(10) + "function ", 1)[0]
        assert "$mins = Resolve-ShadowMinutes -RequestedMinutes $Minutes" in body
        assert "else { -1 }" in body, f"{name} must leave its observer uncapped"
        assert "if ($killSec -gt 0)" in body or "if ($mins -gt 0)" in body, f"{name} must omit timer for unlimited run"

    reset = src.split("function Reset-Environment {", 1)[1].split(chr(10) + "function ", 1)[0]
    assert "$shadowMinutes = Resolve-ShadowMinutes -RequestedMinutes $Minutes" in reset
    assert "if ($shadowMinutes -ne 0)" in reset
    assert "if ($shadowMinutes -gt 0)" in reset
    assert "else { -1 }" in reset
