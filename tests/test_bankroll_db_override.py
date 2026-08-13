"""Unit tests for SPREAD_HUNTER_DB environment variable override in strategy/stats.py."""
import os
from pathlib import Path
from strategy import stats

def test_db_path_override(monkeypatch, tmp_path):
    custom_db = tmp_path / "custom_fleet.db"
    monkeypatch.setenv("SPREAD_HUNTER_DB", str(custom_db))
    assert stats.get_active_db_path() == custom_db

def test_db_path_default(monkeypatch):
    monkeypatch.delenv("SPREAD_HUNTER_DB", raising=False)
    assert stats.get_active_db_path() == stats.DB
