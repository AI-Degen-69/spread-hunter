"""Locate the repo `.env` without importing anything heavy (#805).

Both the Order Manager CLI and the Dashboard server need the nearest `.env`
walking up from `core_brain/`, stopping at the AGENTS.md boundary. The
dashboard must not import the CLI module to get it — importing the CLI loads
signing keys into the dashboard process — so the lookup lives here, in a
light module with no imports beyond the standard library and no side effects.

Callers that need values still read them themselves; this module only finds
the file.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Union


def find_env_file(start: Union[Path, str, None] = None) -> Optional[Path]:
    """Nearest `.env` walking up from `start`, or None.

    Stops at the AGENTS.md boundary (the repo root check already missed
    there, so nothing further up is ours) and never walks more than four
    levels, so a stranger's `.env` outside the project is never picked up.
    """
    curr = Path(start) if start is not None else Path(__file__).resolve().parent
    for _ in range(4):
        if (curr / ".env").is_file():
            return curr / ".env"
        if (curr / "AGENTS.md").is_file():
            break
        if curr.parent == curr:
            break
        curr = curr.parent
    return None
