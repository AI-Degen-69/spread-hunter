"""Project name guard (issue #283): the product is 'Spread Hunter', not 'Live'.

Scans in-scope files for leftover `Live` name variants. The GitHub repo, the
local checkout folder, the gbrain source pin, and all URLs have been renamed to
`spread-hunter`, so no live file should carry the old name. The only allowance
is docs history: runs, old plans/specs, and old showcase pages.
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent

# One case-insensitive rule instead of five exact-case literals: a stray
# `Spread Hunter LIVE` or `spread hunter live` would otherwise walk past.
BANNED_RE = re.compile(r"hunter[\s_-]+live", re.IGNORECASE)

ALLOW_DIRS = {
    pathlib.Path("docs/runs"),
    pathlib.Path("docs/superpowers"),
    pathlib.Path("docs/issues"),
    pathlib.Path("docs/presentations"),
}

ALLOW_FILES = {
    pathlib.Path("tests/test_project_name.py"),  # this guard lists the variants
}

# (relative path, substring): lines that must keep the old text and why.
ALLOW_LINES = {}

# Not name carriers: VCS and tool caches, virtualenvs, build output, generated
# runtime state, agent-harness dirs, and the agent's own work artifacts.
SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".pytest_cache", ".venv", "venv",
    "dist", ".ecc", ".gstack", ".agents", ".claude", ".opencode", ".freebuff",
    "statistical_validation_run", "reports", "runtime", "data", "tasks",
}

# A guard that silently stops scanning is worse than none: prove these carriers
# were reached, not only that the survivors are clean.
SENTINELS = (
    "README.md",
    "dashboard/static/index.html",
    "scripts/spread-hunter-menu.ps1",
)


def read_text(path: pathlib.Path) -> str | None:
    """Text of a name-carrying file, or None when it is not one.

    UTF-8 first (what the repo ships), then UTF-16 for the BOM'd text
    PowerShell writes. No BOM and not UTF-8 means binary -- ASCII copy in a
    cp1252 file still decodes as UTF-8, so nothing readable is dropped here.
    """
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
            return raw.decode("utf-16")
        return None


def scan() -> tuple[list[str], set[str]]:
    """(hits, relative POSIX paths actually scanned)."""
    hits: list[str] = []
    scanned: set[str] = set()
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        rel_posix = rel.as_posix()
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if any(rel_posix == d.as_posix()
               or rel_posix.startswith(d.as_posix() + "/")
               for d in ALLOW_DIRS):
            continue
        if rel in ALLOW_FILES:
            continue
        text = read_text(path)
        if text is None:
            continue
        scanned.add(rel_posix)
        for i, line in enumerate(text.splitlines(), start=1):
            for match in BANNED_RE.finditer(line):
                allowed = any(
                    rel_posix == p and sub in line
                    for (p, sub) in ALLOW_LINES
                )
                if not allowed:
                    hits.append(f"{rel_posix}:{i}: {match.group(0)}")
    return hits, scanned


def test_no_live_name_leftovers():
    hits, scanned = scan()
    missing = [s for s in SENTINELS if s not in scanned]
    assert not missing, f"guard never scanned these name carriers: {missing}"
    assert not hits, "leftover 'Live' name variants:\n" + "\n".join(hits)
