"""Publishing a runtime JSON file never leaves a truncated one behind.

`runtime/markets.json` and `runtime/pipeline.json` are swapped in by rename so
a reader never sees a half-written file. On Windows the dashboard can hold a
read handle at the moment of the swap and the rename raises `PermissionError`,
so the publish retries. When every retry loses, the previous file is KEPT: a
non-atomic `write_text` over the live path is exactly the torn read the rename
exists to prevent, and a stale universe is the safe failure -- the trader keeps
quoting the markets it already adopted.
"""
from __future__ import annotations

import json

import pytest

from scripts import filter_markets


def test_a_successful_publish_replaces_the_file(tmp_path):
    # Arrange
    target = tmp_path / "markets.json"
    target.write_text('["old"]', encoding="utf-8")

    # Act
    published = filter_markets._publish_json(target, ["new"])

    # Assert
    assert published is True
    assert json.loads(target.read_text(encoding="utf-8")) == ["new"]


def test_a_locked_target_keeps_the_previous_file(tmp_path, monkeypatch, capsys):
    # Arrange -- every rename attempt loses the race to a reader's handle.
    target = tmp_path / "markets.json"
    target.write_text('["old"]', encoding="utf-8")
    monkeypatch.setattr(filter_markets.time, "sleep", lambda _s: None)

    def always_locked(self, _dest):
        raise PermissionError(32, "being used by another process")

    monkeypatch.setattr(filter_markets.Path, "replace", always_locked)

    # Act
    published = filter_markets._publish_json(target, ["new"])

    # Assert -- the reader still sees whole, valid JSON, not a truncated file.
    assert published is False
    assert json.loads(target.read_text(encoding="utf-8")) == ["old"]
    assert "could not publish" in capsys.readouterr().err.lower()


def test_the_temp_file_is_removed_when_the_publish_fails(tmp_path, monkeypatch):
    # Arrange
    target = tmp_path / "pipeline.json"
    target.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(filter_markets.time, "sleep", lambda _s: None)
    monkeypatch.setattr(filter_markets.Path, "replace",
                        lambda self, d: (_ for _ in ()).throw(PermissionError(32, "locked")))

    # Act
    filter_markets._publish_json(target, {"a": 1})

    # Assert -- no orphan .tmp left in runtime/ for the next pass to trip over.
    assert list(tmp_path.glob("*.tmp")) == []


def test_a_non_permission_oserror_is_not_swallowed(tmp_path, monkeypatch):
    """Only the reader-handle race is retried; a real filesystem fault raises."""
    # Arrange
    target = tmp_path / "markets.json"
    monkeypatch.setattr(filter_markets.Path, "replace",
                        lambda self, d: (_ for _ in ()).throw(OSError(28, "no space left")))

    # Act / Assert
    with pytest.raises(OSError):
        filter_markets._publish_json(target, ["new"])
