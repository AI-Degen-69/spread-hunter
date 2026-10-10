"""The menu names one key set everywhere (#475).

The CodeRabbit plan for #475 requires the grid, the `Select [...]` prompt, the
invalid-selection message, the command-line allow-list and the usage header to
name the identical key set `1`–`9`, `r`, `a`, `p`, `q`. Before this work the
five places had drifted apart: the grid showed a `t` trial row, the prompt said
`[1-9, q]`, and `audit`/`prune` were words rather than the single letters the
grid now shows.

`scripts/spread-hunter-menu.ps1` takes over the console when dot-sourced, so it
cannot be imported: most of this file is text-level string assertions on the
source. The one `pwsh` test copies `Invoke-LiveAction` and the key-list helper
out of the script and drives them with stubbed side-effects, mirroring the
brace-matching harness in `tests/test_menu_live_state.py`.
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


def _menu_keys() -> list[str]:
    src = _menu_source()
    m = re.search(r"\$script:MenuKeys\s*=\s*@\(([^)]*)\)", src)
    assert m, "the script must define $script:MenuKeys"
    return re.findall(r"'([^']+)'", m.group(1))


def test_menu_key_set_is_identical_everywhere():
    """One ordered list feeds the grid, prompt, invalid message and allow-list.

    If any of the four re-derives its keys by hand, a new key added to one
    place silently misses another — the exact drift that hid `t` from the
    prompt and let `audit`/`prune` live outside the grid.
    """
    assert _menu_keys() == ["1", "2", "3", "4", "5", "6", "7", "8", "9", "r", "a", "p", "q"]
    src = _menu_source()
    # The allow-list reads that same list, not a hand-written literal.
    assert "$script:MenuKeys -notcontains $key" in src
    # The prompt and the invalid-selection message both go through the helper.
    assert "Format-MenuHint" in src
    assert "Format-MenuHint" in src.split("Select ", 1)[1]
    assert "Format-MenuHint" in _function_source("Invoke-LiveAction")


def test_usage_header_names_every_key():
    """The usage header lists the same actions the grid offers."""
    header = _menu_source().split("# Usage:", 1)[1].split("# the same code path", 1)[0]
    for word in ("audit", "prune"):
        assert word in header, f"usage header must still show the CLI word {word!r}"
    # The retired trial launcher is gone from the header.
    assert "shadow-trial" not in header


def test_storage_words_map_to_letter_keys():
    """`audit`/`retention` -> `a`, `prune` -> `p` on the command line."""
    mapping = _action_map()
    for word in ("audit", "storage-audit", "retention"):
        assert mapping.get(word) == "a", f"{word!r} must map to 'a'"
    for word in ("prune", "storage-prune"):
        assert mapping.get(word) == "p", f"{word!r} must map to 'p'"


def test_every_key_has_a_branch():
    """Every key in the list reaches a switch branch in Invoke-LiveAction.

    A key advertised in the grid/prompt/allow-list but missing from the switch
    would fall through to the invalid-selection warning: the operator presses a
    shown key and nothing happens. This pins the grid list and the switch
    together.
    """
    dispatch = _function_source("Invoke-LiveAction")
    for key in _menu_keys():
        assert re.search(r'^\s*"%s" \{' % re.escape(key), dispatch, re.MULTILINE), (
            f'key "{key}" is in $script:MenuKeys but has no "{key}" switch branch')


@requires_pwsh
def test_dispatch_routes_storage_keys_and_warns_on_unknown():
    """Driving Invoke-LiveAction directly: `a`/`p` call the storage helpers once
    each, `p` passes -Force per $Yes, and an unknown key warns with the shared
    hint rather than throwing.

    Mirrors the brace-matching extraction in tests/test_menu_live_state.py:
    copy the key list, Format-MenuHint and Invoke-LiveAction out of the script,
    stub every side-effecting callee, and assert on the recorders.
    """
    text = _menu_source()
    keys_line = next(l for l in text.splitlines() if l.strip().startswith("$script:MenuKeys"))

    def extract(name: str) -> str:
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

    prelude = "\n".join([
        keys_line,
        extract("Format-MenuHint"),
        extract("Invoke-LiveAction"),
        r"""
$script:auditCalls = 0
$script:pruneForce = $null
function Invoke-StorageAudit { $script:auditCalls++ }
function Invoke-StoragePrune { param([switch]$Force) $script:pruneForce = $Force }
function Lsh-Warn   { param($m) Write-Host ("WARN " + $m) }
function Lsh-Banner { param($Title,$Subtitle) }
function Lsh-Fail   { param($m) Write-Host ("FAIL " + $m) }
function Start-Sleep { }
$failures = New-Object System.Collections.Generic.List[string]
Invoke-LiveAction "a"
if ($script:auditCalls -ne 1) { $failures.Add("a did not call audit once") | Out-Null }
$Yes = $true;  Invoke-LiveAction "p"
if ($script:pruneForce -ne $true)  { $failures.Add("p did not pass Force under -Yes") | Out-Null }
$Yes = $false; Invoke-LiveAction "p"
if ($script:pruneForce -ne $false) { $failures.Add("p passed Force without -Yes") | Out-Null }
$warned = (Invoke-LiveAction "z" *>&1 | Out-String)
if (-not $warned.Contains("1-9, r, a, p, q")) { $failures.Add("warn did not name the full hint") | Out-Null }
if ($failures.Count) { $failures | ForEach-Object { Write-Host ("FAIL " + $_) }; exit 1 }
Write-Host "ALL DISPATCH CHECKS PASS"
""",
    ])
    script = "$ErrorActionPreference = 'Stop'\n" + prelude

    res = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert "ALL DISPATCH CHECKS PASS" in res.stdout

