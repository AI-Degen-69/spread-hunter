"""The statistics report lands in the repo's `reports/`, whatever the cwd is.

`Path("reports")` resolves against the CURRENT WORKING DIRECTORY, so the
report only landed in the right place because every menu launch happens to
pass `-WorkingDirectory $ProjectPath`. Run the observer from anywhere else --
a scheduled task, a shell in `scripts/`, a test harness -- and the run's only
human-readable artifact is written into a `reports/` folder nobody looks in.
This is the same class of bug as the statistics store that spilled into the
repo root: a writer that decides its own destination from ambient state.

`out_dir` still wins when a caller passes one; only the DEFAULT is anchored.
"""
from __future__ import annotations

from pathlib import Path

from core_brain import statistics_report
from core_brain.runtime_paths import LIVE_ROOT


def test_the_default_destination_is_the_repo_reports_directory():
    assert statistics_report.DEFAULT_REPORT_DIR == LIVE_ROOT / "reports"
    assert statistics_report.DEFAULT_REPORT_DIR.is_absolute()


def test_the_default_does_not_move_with_the_working_directory(tmp_path, monkeypatch):
    # Arrange -- run from somewhere that is not the repo root.
    monkeypatch.chdir(tmp_path)

    # Act
    resolved = statistics_report.DEFAULT_REPORT_DIR

    # Assert -- still the repo's reports/, not tmp_path/reports.
    assert resolved == LIVE_ROOT / "reports"
    assert not str(resolved).startswith(str(tmp_path))


def test_an_explicit_out_dir_still_wins():
    """A caller that names a destination is never overridden."""
    src = Path(statistics_report.__file__).read_text(encoding="utf-8")
    assert "Path(out_dir) if out_dir is not None else DEFAULT_REPORT_DIR" in src


def test_the_observer_does_not_pass_a_cwd_relative_default():
    src = Path(statistics_report.__file__).parent.joinpath(
        "statistics_observer.py").read_text(encoding="utf-8")
    assert 'Path("reports")' not in src
