import pytest
from unittest.mock import MagicMock
from core_brain.risk_guardian import detect_unhedged_positions, run_guardian_tick
from core_brain.quotes import Inventory

def test_detect_unhedged_positions(monkeypatch):
    registry = MagicMock()
    registry.get_all_quotes.return_value = [
        {"condition_id": "0x123", "side": "UP", "token_id": "tok_up"},
        {"condition_id": "0x123", "side": "DOWN", "token_id": "tok_dn"},
    ]
    
    # 10 UP, 0 DOWN = unhedged
    mock_inv = Inventory()
    mock_inv.up_shares = 10.0
    mock_inv.down_shares = 0.0
    
    monkeypatch.setattr(
        "core_brain.risk_guardian.inventory_from_registry",
        lambda condition_id, up_token, down_token, db_path=None: mock_inv
    )
    
    unhedged = detect_unhedged_positions(registry)
    assert len(unhedged) == 1
    assert unhedged[0]["condition_id"] == "0x123"
    assert unhedged[0]["naked_shares"] == 10.0
    assert unhedged[0]["heavy_side"] == "UP"

def test_detect_unhedged_positions_balanced(monkeypatch):
    registry = MagicMock()
    registry.get_all_quotes.return_value = [
        {"condition_id": "0x123", "side": "UP", "token_id": "tok_up"},
        {"condition_id": "0x123", "side": "DOWN", "token_id": "tok_dn"},
    ]
    
    # Balanced = 0 unhedged
    mock_inv = Inventory()
    mock_inv.up_shares = 10.0
    mock_inv.down_shares = 10.0
    
    monkeypatch.setattr(
        "core_brain.risk_guardian.inventory_from_registry",
        lambda condition_id, up_token, down_token, db_path=None: mock_inv
    )
    
    unhedged = detect_unhedged_positions(registry)
    assert len(unhedged) == 0

def test_run_guardian_tick_invokes_saver_when_unhedged(monkeypatch):
    registry = MagicMock()
    registry.get_all_quotes.return_value = [
        {"condition_id": "0x123", "side": "UP", "token_id": "tok_up"},
        {"condition_id": "0x123", "side": "DOWN", "token_id": "tok_dn"},
    ]
    mock_inv = Inventory()
    mock_inv.up_shares = 5.0
    mock_inv.down_shares = 0.0
    monkeypatch.setattr(
        "core_brain.risk_guardian.inventory_from_registry",
        lambda condition_id, up_token, down_token, db_path=None: mock_inv
    )
    
    mock_saver = MagicMock(return_value=[{"action": "exited"}])
    res = run_guardian_tick(MagicMock(), registry, MagicMock(), single_buy_saver_fn=mock_saver)
    assert len(res) == 1
    assert mock_saver.called
