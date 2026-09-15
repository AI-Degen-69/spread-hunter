"""Every statistics observer writes its store into `data/`, never the repo root.

`StatisticsStore.create` puts the run store wherever `--data-dir` points, and
the CLI default is `data`. Two of the three menu launches passed the REPO ROOT
instead, so shadow and resume runs dropped `stats_<stamp>_<run-id>.db` beside
`AGENTS.md` while the validation run put its own store in `data/`. The same
artifact landed in two places depending on which menu option started it, and
`.gitignore` grew a `stats_*.db*` rule to paper over the root spill instead of
the writer being fixed. 436 MB had accumulated in the root by 2026-09-15.
"""
from __future__ import annotations

import re
from pathlib import Path

MENU = Path(__file__).resolve().parents[1] / "scripts" / "spread-hunter-menu.ps1"

# The observer launch lines, each capturing what follows "--data-dir".
_DATA_DIR = re.compile(r'"--data-dir",\s*([^,]+),')


def _observer_data_dirs() -> list[str]:
    src = MENU.read_text(encoding="utf-8")
    lines = [l for l in src.splitlines() if "statistics_observer" in l and "--data-dir" in l]
    assert lines, "no statistics_observer launch found in the menu"
    out = []
    for line in lines:
        m = _DATA_DIR.search(line)
        assert m, f"no --data-dir argument on: {line.strip()[:120]}"
        out.append(m.group(1).strip())
    return out


def test_every_observer_launch_writes_into_the_data_directory():
    dirs = _observer_data_dirs()
    # All three menu paths -- fresh shadow, resume, statistical validation.
    assert len(dirs) == 3, f"expected 3 observer launches, found {len(dirs)}"
    for d in dirs:
        assert d == '(Join-Path $ProjectPath "data")', (
            f"observer writes its store to {d}, not data/")


def test_no_observer_launch_points_at_the_repo_root():
    """The bare $ProjectPath is the spill that put 436 MB beside AGENTS.md."""
    for d in _observer_data_dirs():
        assert d != "$ProjectPath"
