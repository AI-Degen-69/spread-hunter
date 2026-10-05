"""Menu options 4, 6 and 9 survive a data/ that already holds a shadow run db.

`Get-NextShadowSeq` mints the next two-digit run id by scanning `data/` for
files named `NN_shadow_<stamp>.db` and formatting `max + 1` with the `D2`
specifier. `Measure-Object -Maximum` hands its `Maximum` back as a
`System.Double` on both Windows PowerShell 5.1 and PowerShell 7, and `D` is
defined for integral types only, so the format call throws:

    Error formatting a string: Format specifier was invalid.

The crash is state-dependent, which is why it read as intermittent: with an
empty `data/` the sequence never leaves the integer `1`. As soon as one run db
exists, every caller dies -- `Start-ShadowDashboard` (option 6),
`Reset-Environment -Mode "shadow"` (option 4) and
`Reset-Environment -Mode "statistical"` (option 9).

Journeys under test:
1. As the operator, a second run on a session that already produced
   `01_shadow_<stamp>.db` gets run id "02" instead of a format error.
2. As the operator, a clean checkout still starts at "01".
3. As the operator, the run id wraps back to "01" after 99.
4. As the operator, `data/01_shadow.db` (no trailing underscore) is not a run db
   and does not push the sequence forward.
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


def _get_next_shadow_seq_source() -> str:
    """Lift just the Get-NextShadowSeq definition out of the menu script.

    The menu script starts a live control centre when it is dot-sourced, so
    the test runs the one function under test on its own.
    """
    text = MENU.read_text(encoding="utf-8")
    match = re.search(r"^function Get-NextShadowSeq \{.*?^\}", text,
                      re.MULTILINE | re.DOTALL)
    assert match, "Get-NextShadowSeq is no longer defined in the menu script"
    return match.group(0)


def _next_seq(tmp_path: Path, db_names: list[str], tag: str = "") -> dict:
    """Seed `tmp_path/data` with `db_names`, then report what the function returns."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    for name in db_names:
        (data_dir / name).write_bytes(b"")

    # The lifted body reads the module-scope $ProjectPath the menu script sets
    # at load time; standing it up here is what points the scan at tmp_path.
    project_path = str(tmp_path).replace("'", "''")
    tag_arg = f" -Tag '{tag}'" if tag else ""
    script = "\n".join([
        "$ErrorActionPreference = 'Stop'",
        _get_next_shadow_seq_source(),
        f"$ProjectPath = '{project_path}'",
        "try {",
        f"  $v = Get-NextShadowSeq{tag_arg}",
        "  $out = @{ threw = $false; value = [string]$v; message = $null }",
        "} catch {",
        "  $out = @{ threw = $true; value = $null; message = $_.Exception.Message }",
        "}",
        "$out | ConvertTo-Json -Compress",
    ])
    out = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", script],
                         capture_output=True, text=True, check=True, encoding="utf-8")
    return json.loads(out.stdout)


def test_second_run_follows_an_existing_run_db(tmp_path):
    result = _next_seq(tmp_path, ["01_shadow_04-09_09-08.db"])

    assert result["threw"] is False, result["message"]
    assert result["value"] == "02"


def test_clean_data_dir_starts_at_one(tmp_path):
    result = _next_seq(tmp_path, [])

    assert result["threw"] is False, result["message"]
    assert result["value"] == "01"


def test_run_id_wraps_back_to_one_after_ninety_nine(tmp_path):
    result = _next_seq(tmp_path, ["99_shadow_04-09_09-08.db"])

    assert result["threw"] is False, result["message"]
    assert result["value"] == "01"


def test_db_without_the_shadow_prefix_is_not_a_run_db(tmp_path):
    # No trailing underscore, so it never matches ^(\d{1,2})_shadow_ .
    result = _next_seq(tmp_path, ["01_shadow.db"])

    assert result["threw"] is False, result["message"]
    assert result["value"] == "01"


def test_prudent_sequence_starts_at_one_when_no_prudent_db_exists(tmp_path):
    result = _next_seq(tmp_path, ["01_shadow_04-09_09-08.db", "02_shadow_04-09_10-00.db"], tag="prudent")

    assert result["threw"] is False, result["message"]
    assert result["value"] == "01"


def test_prudent_sequence_increments_following_existing_prudent_db(tmp_path):
    result = _next_seq(tmp_path, ["01_shadow_prudent_05-10_12-00.db"], tag="prudent")

    assert result["threw"] is False, result["message"]
    assert result["value"] == "02"
