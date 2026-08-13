"""Unit tests for SPREAD_HUNTER_DB / HUNTER_DB environment variable overrides and bankroll validation."""
import os
import pytest
from pathlib import Path
from strategy import stats
from strategy.config import load

def test_db_path_override(monkeypatch, tmp_path):
    custom_db = tmp_path / "custom_fleet.db"
    monkeypatch.setenv("SPREAD_HUNTER_DB", str(custom_db))
    assert stats.get_active_db_path() == custom_db

def test_db_path_hunter_db_fallback(monkeypatch, tmp_path):
    custom_db = tmp_path / "fallback_fleet.db"
    monkeypatch.delenv("SPREAD_HUNTER_DB", raising=False)
    monkeypatch.setenv("HUNTER_DB", str(custom_db))
    assert stats.get_active_db_path() == custom_db

def test_db_path_default(monkeypatch):
    monkeypatch.delenv("SPREAD_HUNTER_DB", raising=False)
    monkeypatch.delenv("HUNTER_DB", raising=False)
    assert stats.get_active_db_path() == stats.DB

def test_bankroll_validation_valid(monkeypatch):
    monkeypatch.setenv("SPREAD_HUNTER_BANKROLL", "500")
    cfg = load()
    assert cfg.bankroll_usd == 500.0
    assert cfg.allocation_budget == 450.0

def test_bankroll_validation_invalid_negative(monkeypatch):
    monkeypatch.setenv("SPREAD_HUNTER_BANKROLL", "-100")
    with pytest.raises(ValueError, match="strictly positive"):
        load()

def test_bankroll_validation_invalid_zero(monkeypatch):
    monkeypatch.setenv("SPREAD_HUNTER_BANKROLL", "0")
    with pytest.raises(ValueError, match="strictly positive"):
        load()

