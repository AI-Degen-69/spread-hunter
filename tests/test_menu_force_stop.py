"""Separate force-stop action for the menu script (#881 T3).

`Stop-WorktreeProcessesForce` stops every Owned process of this checkout —
recorded or not — while Foreign and Unknown processes keep running. Kills go
in supervisor order (dashboard, order manager, rest), each PID's creation
time is re-verified at kill time, one rescan catches late arrivals, and a
dashboard/session record is removed only after every PID in it has exited.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

MENU = Path(__file__).resolve().parents[1] / "scripts" / "spread-hunter-menu.ps1"

PWSH = shutil.which("pwsh") or shutil.which("powershell")

pytestmark = pytest.mark.skipif(PWSH is None,
                                reason="no PowerShell host on this machine")

T0 = 1760000000


def _lift(*names: str) -> str:
    text = MENU.read_text(encoding="utf-8")
    parts = []
    for name in names:
        match = re.search(r"^function %s \{.*?^\}" % re.escape(name), text,
                          re.MULTILINE | re.DOTALL)
        assert match, "%s is no longer defined in the menu script" % name
        parts.append(match.group(0))
    return "\n".join(parts)


def _braced(name: str) -> str:
    text = MENU.read_text(encoding="utf-8")
    start = text.index(f"function {name} ")
    i = text.index("{", start)
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[start:j + 1]
    raise AssertionError(f"unbalanced braces in {name}")


LIFTED = _lift(
    "Test-PathInCheckout", "Get-CommandLineCheckoutOwner",
    "Get-DashboardOwnership", "Get-ForceStopTargets",
    "Stop-WorktreeProcessesForce", "Get-ServiceEntry", "Test-RecordLive",
    "ConvertTo-RecordedStart", "Test-PidAlive", "Get-ProcessRecord",
    "Get-DashInstance", "Kill-RecordedPid",
)


def _run(main: Path, sib: Path, wt: Path, *, port_pid: int = 101,
         wpg_fail: tuple = (), flip: tuple = (), late: dict | None = None,
         dash_record: bool = False, legacy_session: bool = False,
         owned_only: bool = False, extra: tuple = (),
         tree_kill: dict | None = None) -> dict:
    main_s = str(main).replace("'", "''")
    run_dir = main / "runtime"
    run_dir.mkdir(parents=True, exist_ok=True)
    procs = run_dir / "processes.json"
    procs.write_text(json.dumps({"decide": {"pid": 102, "started_at": T0},
                                 "query": {"pid": 107, "started_at": T0}}),
                     encoding="utf-8")
    dash_file = run_dir / "live-dash.pids.json"
    if dash_record:
        dash_file.write_text(json.dumps({"strategy": "s", "saved": "t",
                                         "dash": {"pid": 101, "started_ticks": 1,
                                                  "started": "t", "port": 8799}}),
                             encoding="utf-8")
    sess_file = run_dir / "legacy-session.json"
    if legacy_session:
        sess_file.write_text(json.dumps({"loop": {"pid": 110}}), encoding="utf-8")
    table = [
        {"pid": 101, "name": "python.exe",
         "cmd": "python -m dashboard.server --port 8799", "t": 0},
        {"pid": 102, "name": "python.exe",
         "cmd": "python -m core_brain.order_manager poll --interval 0.5", "t": 0},
        {"pid": 103, "name": "python.exe",
         "cmd": f"python -m core_brain.shadow_run --db \"{main}/data/s103.db\"",
         "t": 0},
        {"pid": 104, "name": "python.exe",
         "cmd": f"python -m core_brain.shadow_run --db \"{sib}/data/s104.db\"",
         "t": 0},
        {"pid": 105, "name": "python.exe",
         "cmd": "python -m scripts.filter_loop", "t": 0},
        {"pid": 106, "name": "python.exe",
         "cmd": f"python -m core_brain.shadow_run --db \"{wt}/data/s106.db\"",
         "t": 0},
        {"pid": 107, "name": "python.exe",
         "cmd": "python -m core_brain.order_manager poll --interval 5", "t": 3600},
        {"pid": 108, "name": "python.exe",
         "cmd": "python -m polymarket_docs_mcp", "t": 0},
        {"pid": 109, "name": "pytest.exe",
         "cmd": "pytest tests/test_x.py -k core_brain", "t": 0},
        {"pid": 110, "name": "python.exe",
         "cmd": "python -m core_brain.shadow_run --db relative110.db", "t": 0},
    ]
    if owned_only:
        table = [row for row in table if row["pid"] in (101, 102, 103)]
    for row in extra:
        table.append({"pid": row["pid"], "name": row.get("name", "python.exe"),
                      "cmd": row["cmd"], "t": 0})
    if late:
        late_row = dict(late)
        late_row["t"] = 0
    else:
        late_row = None
    table_json = json.dumps(table)
    late_json = json.dumps(late_row)
    if tree_kill:
        tree_lit = "@{ " + " ".join(
            f"{p} = @({', '.join(str(c) for c in ch) })" for p, ch in tree_kill.items()) + " }"
    else:
        tree_lit = "@{}"
    extra_starts = [f"$script:Starts[{r['pid']}] = $script:BaseUnix" for r in extra]
    script = "\n".join([
        "$ErrorActionPreference = 'Stop'",
        LIFTED,
        f"$ProjectPath = '{main_s}'",
        "$Port = 8799",
        f"$ProcsFile = '{str(procs).replace(chr(39), chr(39)*2)}'",
        f"$RunDir = '{str(run_dir).replace(chr(39), chr(39)*2)}'",
        f"$DashPidFile = '{str(dash_file).replace(chr(39), chr(39)*2)}'",
        f"$ShadowSessionFile = '{str(sess_file).replace(chr(39), chr(39)*2)}'",
        "$script:ShadowRunId = $null",
        '$LegacyServiceKeys = @{ filter = "screener"; query = "engine"; decide = "fleet" }',
        f"$script:BaseUnix = {T0}",
        f"$script:FlipPids = @({'; '.join(str(p) for p in flip)})",
        f"$script:WpgFail = @({'; '.join(str(p) for p in wpg_fail)})",
        f"$script:LateRow = '{late_json}'",
        "$script:Calls = @{}",
        "$script:Log = @()",
        "function Get-CimInstance {",
        "  $script:Calls['cim'] = [int]$script:Calls['cim'] + 1",
        "  $rows = $script:Table1",
        "  if ($script:Calls['cim'] -ge 2 -and $script:LateRow -ne 'null') {",
        "    $rows = @($script:Table1) + @($script:LateRow | ConvertFrom-Json)",
        "  }",
        "  return @($rows | Where-Object { $script:Starts.ContainsKey([int]$_.pid) } | ForEach-Object {",
        "    [pscustomobject]@{ Name = $_.name; ProcessId = [int]$_.pid;",
        "      CommandLine = [string]$_.cmd } })",
        "}",
        "function Get-Process {",
        "  param([int]$Id, [string]$ErrorAction)",
        "  if (-not $script:Starts.ContainsKey($Id)) { throw 'no such process' }",
        "  $script:Calls[\"gp$Id\"] = [int]$script:Calls[\"gp$Id\"] + 1",
        "  $t = [int]$script:Starts[$Id]",
        "  if (($script:FlipPids -contains $Id) -and ($script:Calls[\"gp$Id\"] -ge 2)) { $t += 3600 * ($script:Calls[\"gp$Id\"] - 1) }",
        "  $dt = [DateTimeOffset]::FromUnixTimeSeconds($t).LocalDateTime",
        "  return [pscustomobject]@{ Id = $Id; StartTime = $dt }",
        "}",
        "function Invoke-RestMethod {",
        "  return [pscustomobject]@{",
        f"    db_path = '{main_s}/data/orders.db';",
        f"    registry_path = '{main_s}/runtime/processes.json';",
        "    services = [pscustomobject]@{ dash = [pscustomobject]@{ pid = 101 } };",
        "    db_is_production = $false } }",
        "function Get-PortPid { param([int]$PortNumber = 0) "
        f"return {port_pid} }}",
        "function Wait-ProcessGone { param([int]$ProcessId, [int]$TimeoutSeconds = 10)",
        "  $script:Calls['wpg'] += @($ProcessId)",
        "  if ($script:WpgFail -contains $ProcessId) { return $false }",
        "  $script:Starts.Remove($ProcessId)",
        "  if ($script:TreeKill.ContainsKey($ProcessId)) {",
        "    foreach ($ch in $script:TreeKill[$ProcessId]) { $script:Starts.Remove([int]$ch) } }",
        "  return $true }",
        "function taskkill { $script:Calls['kill'] += @(($args -join ' ')) }",
        "function Stop-TsBridge { $script:Calls['ts'] = [int]$script:Calls['ts'] + 1 }",
        "function Lsh-Step($m) { $script:Log += @(\"STEP:$m\") }",
        "function Lsh-Ok($m) { $script:Log += @(\"OK:$m\") }",
        "function Lsh-Fail($m) { $script:Log += @(\"FAIL:$m\") }",
        "function Lsh-Warn($m) { $script:Log += @(\"WARN:$m\") }",
        "$script:Table1 = ('" + table_json.replace("'", "''") + "' | ConvertFrom-Json)",
        f"$script:TreeKill = {tree_lit}",
        "$script:Starts = @{ 101 = $script:BaseUnix; 102 = $script:BaseUnix;",
        "  103 = $script:BaseUnix; 104 = $script:BaseUnix; 105 = $script:BaseUnix;",
        "  106 = $script:BaseUnix; 107 = ($script:BaseUnix + 3600);",
        "  108 = $script:BaseUnix; 110 = $script:BaseUnix }",
        "if ($script:LateRow -ne 'null') {",
        "  $lr = ($script:LateRow | ConvertFrom-Json)",
        "  $script:Starts[[int]$lr.pid] = $script:BaseUnix }",
        *[f"{line}" for line in extra_starts],
        "$r = Stop-WorktreeProcessesForce",
        "$out = @{ result = $r; log = @($script:Log);",
        "  killed = @($script:Calls['kill']); ts = [int]$script:Calls['ts'] }",
        "$out | ConvertTo-Json -Compress -Depth 5",
    ])
    out = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", script],
                         capture_output=True, text=True, check=True, encoding="utf-8")
    return json.loads(out.stdout)


def _killed_pids(res: dict) -> list:
    pids = []
    for entry in res["killed"]:
        m = re.search(r"(\d+)\s*$", entry)
        assert m, entry
        pids.append(int(m.group(1)))
    return pids


def _make_tree(tmp_path) -> tuple:
    main = tmp_path / "main"
    sib = tmp_path / "sib"
    wt = main / "wt"
    for root in (main, sib, wt):
        (root / "core_brain").mkdir(parents=True, exist_ok=True)
        (root / "dashboard").mkdir(parents=True, exist_ok=True)
        (root / "core_brain" / "order_manager.py").write_text("# m", encoding="utf-8")
        (root / "dashboard" / "server.py").write_text("# m", encoding="utf-8")
    return main, sib, wt


def test_force_stop_kills_only_owned_targets_in_supervisor_order(tmp_path):
    main, sib, wt = _make_tree(tmp_path)
    res = _run(main, sib, wt, legacy_session=True)
    assert _killed_pids(res) == [101, 102, 103]
    assert res["result"] is True


def test_force_stop_reports_foreign_and_unknown(tmp_path):
    main, sib, wt = _make_tree(tmp_path)
    res = _run(main, sib, wt, legacy_session=True)
    text = "\n".join(res["log"])
    assert "other checkout: 104, 106" in text
    line = next(l for l in res["log"] if "could not prove ownership:" in l)
    assert set(int(x) for x in re.findall(r"\d+", line)) == {105, 107, 110}


def test_force_stop_ignores_non_target_modules(tmp_path):
    main, sib, wt = _make_tree(tmp_path)
    res = _run(main, sib, wt, legacy_session=True)
    blob = "\n".join(res["log"] + res["killed"])
    assert "108" not in blob
    assert "109" not in blob


def test_force_stop_skips_pid_whose_start_time_changed(tmp_path):
    main, sib, wt = _make_tree(tmp_path)
    res = _run(main, sib, wt, legacy_session=True, flip=(103,))
    assert 103 not in _killed_pids(res)
    assert _killed_pids(res) == [101, 102]
    text = "\n".join(res["log"])
    assert "103" in text
    assert res["result"] is False


def test_force_stop_reports_failure_and_keeps_record_when_target_survives(tmp_path):
    main, sib, wt = _make_tree(tmp_path)
    res = _run(main, sib, wt, dash_record=True, wpg_fail=(101,))
    assert res["result"] is False
    assert (main / "runtime" / "live-dash.pids.json").exists()


def test_force_stop_rescan_catches_late_owned_process(tmp_path):
    main, sib, wt = _make_tree(tmp_path)
    late = {"pid": 111, "name": "python.exe",
            "cmd": f"python -m core_brain.shadow_run --db \"{main}/data/late.db\""}
    res = _run(main, sib, wt, legacy_session=True, late=late)
    killed = _killed_pids(res)
    assert 111 in killed
    assert killed.index(111) > killed.index(103)
    assert res["result"] is True


def test_force_stop_all_owned_rescan_is_not_inconclusive(tmp_path):
    main, sib, wt = _make_tree(tmp_path)
    res = _run(main, sib, wt, legacy_session=True, owned_only=True)
    assert _killed_pids(res) == [101, 102, 103]
    assert res["result"] is True
    text = "\n".join(res["log"])
    assert "inconclusive" not in text


def test_force_stop_tree_killed_child_not_reported_running(tmp_path):
    main, sib, wt = _make_tree(tmp_path)
    extra = ({"pid": 112, "cmd": f"python -m scripts.filter_loop \"{main}/c.json\""},
             {"pid": 113, "cmd": f"python -m scripts.filter_loop \"{main}/c.json\""})
    res = _run(main, sib, wt, legacy_session=True, owned_only=True, extra=extra,
               tree_kill={112: [113]})
    assert res["result"] is True
    text = "\n".join(res["log"])
    assert "still running" not in text


def test_force_stop_leaves_registry_and_bridge_alone(tmp_path):
    main, sib, wt = _make_tree(tmp_path)
    procs = main / "runtime" / "processes.json"
    res = _run(main, sib, wt, legacy_session=True)
    assert res["ts"] == 0
    assert procs.exists()
    assert json.loads(procs.read_text(encoding="utf-8")) == {
        "decide": {"pid": 102, "started_at": T0},
        "query": {"pid": 107, "started_at": T0}}


def test_action_map_routes_force_stop_aliases():
    src = MENU.read_text(encoding="utf-8")
    body = src.split("$actionMap = @{", 1)[1].split("}", 1)[0]
    pairs = dict(re.findall(r'"([^"]+)"\s*=\s*"([^"]+)"', body))
    assert pairs.get("stop-all-force") == "x"
    assert pairs.get("force-stop") == "x"
    assert pairs.get("nuke") == "x"
    assert pairs.get("stop-all") == "7"
    assert pairs.get("stop") == "2"


def test_dispatch_key_x_calls_force_stop_only():
    text = MENU.read_text(encoding="utf-8")
    keys_line = next(l for l in text.splitlines() if l.strip().startswith("$script:MenuKeys"))
    start = text.index("function Invoke-LiveAction ")
    i = text.index("{", start)
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                dispatch = text[start:j + 1]
                break
    for with_targets in (False, True):
        prelude = "\n".join([
            keys_line,
            dispatch,
            "$Action = 'stop-all-force'",
            "$script:ForceCalls = 0",
            "$script:AllCalls = 0",
            "$script:Log = @()",
            "function Stop-WorktreeProcessesForce {",
            "  param([switch]$Preview)",
            "  $script:ForceCalls++",
            ("  return ,@()" if not with_targets else
             "  return ,@([pscustomobject]@{ Pid = 5; CommandLine = 'x'; Verdict = 'Owned' })"),
            "}",
            "function Stop-AllSpreadHunterProcesses { $script:AllCalls++ }",
            "function Lsh-Step($m) { $script:Log += @(\"STEP:$m\") }",
            "function Lsh-Ok($m) { $script:Log += @(\"OK:$m\") }",
            "function Lsh-Warn($m) { $script:Log += @(\"WARN:$m\") }",
            "function Lsh-Fail($m) { $script:Log += @(\"FAIL:$m\") }",
            "$null = Invoke-LiveAction 'x'",
            "$out = @{ force = $script:ForceCalls; all = $script:AllCalls;",
            "  log = @($script:Log) }",
            "$out | ConvertTo-Json -Compress -Depth 4",
        ])
        out = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", prelude],
                             capture_output=True, text=True, check=True,
                             encoding="utf-8")
        got = json.loads(out.stdout)
        assert got["all"] == 0
        if with_targets:
            assert got["force"] == 2
        else:
            assert got["force"] == 1
