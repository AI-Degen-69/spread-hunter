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

from core_brain.naming import STAMP_FORMAT

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
    # All four menu paths -- clean shadow, fresh shadow (Start-NewShadowRun),
    # resume, statistical validation. (The depth-bar trial launcher was retired
    # in #475, so its observer launch is gone.)
    assert len(dirs) == 4, f"expected 4 observer launches, found {len(dirs)}"
    for d in dirs:
        assert d == '(Join-Path $ProjectPath "data")', (
            f"observer writes its store to {d}, not data/")


def test_no_observer_launch_points_at_the_repo_root():
    """The bare $ProjectPath is the spill that put 436 MB beside AGENTS.md."""
    for d in _observer_data_dirs():
        assert d != "$ProjectPath"


# Every stats store the menu records in session state: data/stats_<run-id>_<stamp>.db
_STATS_NAME = re.compile(r'"data/(stats_[^"]+)"')
_STATS_SHAPE = re.compile(r"^stats_.*[Ii]d[})]_\$\(Get-NamingStamp\)\.db$")
_PS_STAMP_FORMAT = re.compile(r'function Get-NamingStamp.*?ToString\("([^"]+)"\)', re.S)
_NET_TO_STRFTIME = (("yyyy", "%Y"), ("MM", "%m"), ("dd", "%d"), ("HH", "%H"), ("mm", "%M"))


def test_the_menu_predicts_the_stats_store_name_the_observer_writes():
    """`statistics_observer` names the store `stats_<run-id>_<stamp>.db` itself.

    The menu does not pass a path -- it passes `--data-dir data` and records its
    own guess in the session file. The guess was `stats_<stamp>_<run-id>.db` in
    the old `dd-MM_HH-mm` stamp, so after the grammar changed it named a file
    that never exists.
    """
    names = _STATS_NAME.findall(MENU.read_text(encoding="utf-8"))
    assert names, "no data/stats_* path found in the menu"
    for name in names:
        assert _STATS_SHAPE.match(name), f"not stats_<run-id>_<stamp>.db: {name}"


def test_the_menu_stamp_is_the_one_stamp_function():
    """`Get-NamingStamp` must stay the `core_brain.naming.stamp()` format."""
    match = _PS_STAMP_FORMAT.search(MENU.read_text(encoding="utf-8"))
    assert match, "Get-NamingStamp no longer formats a date"
    translated = match.group(1)
    for net_format, strftime in _NET_TO_STRFTIME:
        translated = translated.replace(net_format, strftime)
    assert translated == STAMP_FORMAT
