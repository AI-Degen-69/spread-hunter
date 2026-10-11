import pytest
from unittest.mock import MagicMock
from core_brain.settlement_sweeper import find_mergeable_conditions, execute_settlement_sweep
from core_brain.quotes import Inventory

def test_find_mergeable_conditions(monkeypatch):
    registry = MagicMock()
    registry.get_all_quotes.return_value = [
        {"condition_id": "0x123", "side": "UP", "token_id": "tok_up"},
        {"condition_id": "0x123", "side": "DOWN", "token_id": "tok_dn"},
    ]
    
    mock_inv = Inventory()
    mock_inv.up_shares = 10.0
    mock_inv.down_shares = 12.0
    
    monkeypatch.setattr(
        "core_brain.settlement_sweeper.inventory_from_registry",
        lambda condition_id, up_token, down_token, db_path=None: mock_inv
    )
    
    results = find_mergeable_conditions(registry, min_shares=1.0)
    assert len(results) == 1
    assert results[0]["condition_id"] == "0x123"
    assert results[0]["shares"] == 10.0

def test_execute_settlement_sweep(monkeypatch):
    registry = MagicMock()
    registry.get_all_quotes.return_value = [
        {"condition_id": "0x123", "side": "UP", "token_id": "tok_up"},
        {"condition_id": "0x123", "side": "DOWN", "token_id": "tok_dn"},
    ]
    
    mock_inv = Inventory()
    mock_inv.up_shares = 5.0
    mock_inv.down_shares = 5.0
    
    monkeypatch.setattr(
        "core_brain.settlement_sweeper.inventory_from_registry",
        lambda condition_id, up_token, down_token, db_path=None: mock_inv
    )
    
    mock_merge = MagicMock(return_value={"tx": "0xabc"})
    results = execute_settlement_sweep(registry, mock_merge, min_shares=1.0, live=False)
    
    assert len(results) == 1
    assert results[0]["status"] == "merged"
    mock_merge.assert_called_once_with(condition_id="0x123", amount=5.0, live=False)
