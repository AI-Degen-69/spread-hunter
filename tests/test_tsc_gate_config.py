"""Issue #506: the bridge type-check gate must stay working.

`tsc` ignores `tsconfig.json` when a file is named on the command line, so the
module setting has to ride on the invocation. These static checks pin the exact
documented gate text (plus the two facts that justify the flags) without
running the compiler as a subprocess.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

GATE_CMD = (
    "npx tsc --noEmit --module nodenext --moduleResolution nodenext server.ts"
)


def _read(rel: str) -> str:
    return (_ROOT / rel).read_text(encoding="utf-8")


def test_gate_command_documented_verbatim():
    assert GATE_CMD in _read("docs/agents/verifying.md")


def test_bare_command_not_documented_as_gate():
    text = _read("docs/agents/verifying.md")
    bare = [m for m in re.finditer(r"npx tsc --noEmit server\.ts", text)
            if not m.group(0).startswith("npx tsc --noEmit --module")]
    assert bare == []


def test_package_json_is_esm():
    pkg = json.loads(_read("package.json"))
    assert pkg.get("type") == "module"


def test_server_uses_import_meta():
    assert "import.meta" in _read("server.ts")
