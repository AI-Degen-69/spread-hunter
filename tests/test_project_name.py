"""Project name guard (issue #283): the product is 'Spread Hunter', not 'Live'.

Scans in-scope files for leftover `Live` name variants. A small allow-list
covers lines that must keep the old text:
- github.com URLs: the repo itself is still named spread-hunter-live
  (owner renames it in settings; links follow in a second pass).
- local folder-path reality (the checkout dir still carries the old name).
- gbrain source pin/commands: the external index still lives under the old name.
- docs history: runs, old plans/specs, old showcase pages.
"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent

BANNED = [
    "Spread Hunter Live",
    "SPREAD HUNTER LIVE",
    "SPREAD-HUNTER LIVE",
    "spread-hunter-live",
    "spread_hunter_live",
]

ALLOW_DIRS = {
    pathlib.Path("docs/runs"),
    pathlib.Path("docs/superpowers"),
    pathlib.Path("docs/issues"),
    pathlib.Path("docs/presentations"),
}

ALLOW_FILES = {
    pathlib.Path("docs/agents/git-workflow.md"),  # Repo: line names the GitHub repo
    pathlib.Path("docs/agents/architecture.md"),  # folder tree = real checkout dir
    pathlib.Path("tests/test_project_name.py"),  # this guard lists the variants
}

# (relative path, substring): lines that must keep the old text and why.
ALLOW_LINES = {
    ("README.md", "AI-Degen-69/spread-hunter-live"): "GitHub repo URL",
    ("README.md", "spread-hunter-live/"): "folder tree = real checkout dir",
    ("dashboard/static/prototype.js", "github.com"): "repo link in nav rail",
    ("tests/test_sidebar_prototype.py", "github.com"): "asserts the repo link",
    ("scripts/spread-hunter-menu.ps1", "Projects\\spread-hunter-live"): "real path comment",
    ("AGENTS.md", "spread-hunter-live"): "gbrain source pin/commands",
    ("CLAUDE.md", "spread-hunter-live"): "gbrain source pin/commands",
    (".gbrain-source", "spread-hunter-live"): "external index pin",
    (".mcp.json", "spread-hunter-live"): "real machine path",
}


def iter_hits() -> list[str]:
    skip_dirs = {".git", "node_modules", "__pycache__", ".pytest_cache",
                 ".venv", "venv", "dist", ".ecc", ".gstack", ".agents",
                 ".claude", ".opencode", "reports", "runtime", "data",
                 ".freebuff", "statistical_validation_run", "tasks"}
    hits: list[str] = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        rel_posix = str(rel).replace("\\", "/")
        if any(part in skip_dirs for part in rel.parts):
            continue
        if any(rel_posix == str(d).replace("\\", "/")
               or rel_posix.startswith(str(d).replace("\\", "/") + "/")
               for d in ALLOW_DIRS):
            continue
        if rel in ALLOW_FILES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, OSError):
            continue  # binary or unreadable: not a display-name carrier
        for i, line in enumerate(text.splitlines(), start=1):
            for variant in BANNED:
                if variant not in line:
                    continue
                allowed = any(
                    rel_posix == p and sub in line
                    for (p, sub) in ALLOW_LINES
                )
                if not allowed:
                    hits.append(f"{rel}:{i}: {variant}")
    return hits


def test_no_live_name_leftovers():
    hits = iter_hits()
    assert not hits, "leftover 'Live' name variants:\n" + "\n".join(hits)
