"""Stage 1 unit tests: mergePositions ABI encoding, selector derivation,
pre-flight guards, idempotency protection, and dry-run safety.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from eth_account import Account
from eth_utils import keccak

import strategy.live_exec as le


class MockResponse:
    def __init__(self, data: dict, status_code: int = 200):
        self.data = data
        self.status_code = status_code

    def read(self):
        return json.dumps(self.data).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


def make_live_env(acc, funder: str) -> dict:
    return {
        "POLY_PRIVATE_KEY": "0x" + acc.key.hex(),
        "POLY_FUNDER": funder,
        "POLY_SIG_TYPE": "3",
        "RELAYER_API_KEY": "test_key",
        "RELAYER_API_KEY_ADDRESS": "0x1234567890123456789012345678901234567890",
        "RELAYER_URL": "https://relayer-v2.polymarket.com",
    }


def test_merge_selector_derivation():
    """1. Selector derivation matches canonical Solidity signature:
    mergePositions(address,bytes32,bytes32,uint256[],uint256) -> 0x9e7212ad.
    """
    canonical_sig = b"mergePositions(address,bytes32,bytes32,uint256[],uint256)"
    derived_selector = "0x" + keccak(canonical_sig)[:4].hex()
    assert derived_selector == "0x9e7212ad"


def test_encode_merge_positions_hand_constructed_comparison():
    """2. Encoder output matches hand-constructed expected hex string.
    Verifies selector, static words, dynamic array offset 0xa0 (160 bytes),
    amount, array length, and elements.
    """
    collateral = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
    parent_coll = "0x0000000000000000000000000000000000000000000000000000000000000000"
    cond_id = "0x1111111111111111111111111111111111111111111111111111111111111111"
    index_sets = [1, 2]
    amount_base_units = 5000000  # 5 shares = 0x4c4b40

    # Hand-constructed expected layout:
    # 4 bytes selector + 5 words head + 3 words tail = 260 bytes (520 hex chars + '0x')
    expected_hex = (
        "0x"
        "9e7212ad"  # Selector (4 bytes)
        "0000000000000000000000002791bca1f2de4661ed88a30c99a7a9449aa84174"  # Word 0: collateralToken (32 bytes)
        "0000000000000000000000000000000000000000000000000000000000000000"  # Word 1: parentCollectionId (32 bytes)
        "1111111111111111111111111111111111111111111111111111111111111111"  # Word 2: conditionId (32 bytes)
        "00000000000000000000000000000000000000000000000000000000000000a0"  # Word 3: offset to partition array (160 bytes)
        "00000000000000000000000000000000000000000000000000000000004c4b40"  # Word 4: amount (5000000)
        "0000000000000000000000000000000000000000000000000000000000000002"  # Word 5: length of partition (2)
        "0000000000000000000000000000000000000000000000000000000000000001"  # Word 6: partition[0] (1)
        "0000000000000000000000000000000000000000000000000000000000000002"  # Word 7: partition[1] (2)
    )

    encoded = le.encode_merge_positions(
        collateral_token=collateral,
        parent_collection_id=parent_coll,
        condition_id=cond_id,
        index_sets=index_sets,
        amount=amount_base_units,
    )

    assert encoded == expected_hex
    assert len(encoded) == 2 + 8 + (8 * 64)  # 522 characters


def test_merge_guard_exceeds_max_order_usd(tmp_path):
    """3. Guard: Refuse if amount * $1.00 exceeds MAX_ORDER_USD."""
    cond_id = "0x26b64228a9fb13e5c2221cd5879fa0f235cee8ab254c0f094977cc86beeb6a2f"
    with patch.object(le, "RUN", tmp_path):
        with pytest.raises(SystemExit) as exc_info:
            le.merge(cond_id, amount=30.0, live=False)
    assert "exceeds MAX_ORDER_USD" in str(exc_info.value)



def test_merge_guard_idempotency_duplicate_pending(tmp_path):
    """3. Guard: Refuse if prior order with condition_id has status in (pending, submitted, interrupted)."""
    cond_id = "0x26b64228a9fb13e5c2221cd5879fa0f235cee8ab254c0f094977cc86beeb6a2f"
    log_file = tmp_path / "live_orders.json"
    log_file.write_text(json.dumps([
        {"id": "order-123", "condition_id": cond_id, "action": "MERGE", "status": "pending"}
    ]), encoding="utf-8")

    with patch.object(le, "RUN", tmp_path):
        with pytest.raises(SystemExit) as exc_info:
            le.merge(cond_id, amount=1.0, live=False)
    msg = str(exc_info.value)
    assert "order-123" in msg
    assert "pending" in msg
    assert "--force" in msg


def test_merge_guard_idempotency_force_override(tmp_path):
    """4. --force overrides the idempotency guard and proceeds with dry-run."""
    cond_id = "0x26b64228a9fb13e5c2221cd5879fa0f235cee8ab254c0f094977cc86beeb6a2f"
    log_file = tmp_path / "live_orders.json"
    log_file.write_text(json.dumps([
        {"id": "order-123", "condition_id": cond_id, "action": "MERGE", "status": "submitted"}
    ]), encoding="utf-8")

    with patch.object(le, "RUN", tmp_path), \
         patch.object(le, "get_payout_denominator", return_value=0):
        # Should not raise SystemExit when force=True
        le.merge(cond_id, amount=1.0, force=True, live=False)


def test_merge_guard_resolved_condition_refuses(tmp_path):
    """3. Guard: Refuse if condition is already resolved (payoutDenominator > 0)."""
    acc = Account.create()
    funder = "0xBa7c21Ac8968983e90BEcB989fe978889FEC266b"
    cond_id = "0x26b64228a9fb13e5c2221cd5879fa0f235cee8ab254c0f094977cc86beeb6a2f"
    env_vars = make_live_env(acc, funder)

    # Mock client balance checks to pass
    mock_client = MagicMock()
    mock_client.get_balance_allowance.return_value = {"balance": "10000000"}  # 10 shares

    with patch.dict(os.environ, env_vars, clear=False), \
         patch.object(le, "RUN", tmp_path), \
         patch.object(le, "_client", return_value=mock_client), \
         patch.object(le, "get_payout_denominator", return_value=1):
        with pytest.raises(SystemExit) as exc_info:
            le.merge(cond_id, amount=1.0, live=True)

    assert "already resolved" in str(exc_info.value)
    assert "Use redeem instead" in str(exc_info.value)


def test_merge_guard_insufficient_balance_up_leg(tmp_path):
    """3. Guard: Refuse if wallet holds insufficient balance on UP token."""
    acc = Account.create()
    funder = "0xBa7c21Ac8968983e90BEcB989fe978889FEC266b"
    cond_id = "0x26b64228a9fb13e5c2221cd5879fa0f235cee8ab254c0f094977cc86beeb6a2f"
    env_vars = make_live_env(acc, funder)

    mock_client = MagicMock()
    # UP token returns 0.5 shares, needs 1.0
    mock_client.get_balance_allowance.side_effect = [
        {"balance": "500000"},   # UP: 0.5 shares
        {"balance": "2000000"},  # DOWN: 2.0 shares
    ]

    with patch.dict(os.environ, env_vars, clear=False), \
         patch.object(le, "RUN", tmp_path), \
         patch.object(le, "_client", return_value=mock_client), \
         patch.object(le, "get_payout_denominator", return_value=0):
        with pytest.raises(SystemExit) as exc_info:
            le.merge(cond_id, amount=1.0, live=True)

    msg = str(exc_info.value)
    assert "Insufficient balance on UP token" in msg
    assert "holds 0.50" in msg
    assert "needs 1.00" in msg
    assert "short by 0.50" in msg


def test_merge_guard_insufficient_balance_down_leg(tmp_path):
    """3. Guard: Refuse if wallet holds insufficient balance on DOWN token."""
    acc = Account.create()
    funder = "0xBa7c21Ac8968983e90BEcB989fe978889FEC266b"
    cond_id = "0x26b64228a9fb13e5c2221cd5879fa0f235cee8ab254c0f094977cc86beeb6a2f"
    env_vars = make_live_env(acc, funder)

    mock_client = MagicMock()
    # UP token has enough (2.0 shares), DOWN has 0.2 shares
    mock_client.get_balance_allowance.side_effect = [
        {"balance": "2000000"},  # UP: 2.0 shares
        {"balance": "200000"},   # DOWN: 0.2 shares
    ]

    with patch.dict(os.environ, env_vars, clear=False), \
         patch.object(le, "RUN", tmp_path), \
         patch.object(le, "_client", return_value=mock_client), \
         patch.object(le, "get_payout_denominator", return_value=0):
        with pytest.raises(SystemExit) as exc_info:
            le.merge(cond_id, amount=1.0, live=True)

    msg = str(exc_info.value)
    assert "Insufficient balance on DOWN token" in msg
    assert "holds 0.20" in msg
    assert "needs 1.00" in msg
    assert "short by 0.80" in msg


def test_merge_dry_run_touches_no_network(tmp_path, capsys):
    """5. Dry-run prints execution plan, token IDs, expected USDC, estimated gas,
    and calldata without sending any network request."""
    cond_id = "0x26b64228a9fb13e5c2221cd5879fa0f235cee8ab254c0f094977cc86beeb6a2f"

    with patch.object(le, "RUN", tmp_path), \
         patch.object(le, "get_payout_denominator", return_value=0), \
         patch("urllib.request.urlopen") as mock_urlopen:
        le.merge(cond_id, amount=1.0, live=False)

    mock_urlopen.assert_not_called()
    captured = capsys.readouterr()
    assert "MERGE (gasless via Polymarket Relayer)" in captured.out
    assert "expected_usdc   $1.00" in captured.out
    assert "estimated_gas   $0.05" in captured.out
    assert "net_collateral  $0.95" in captured.out
    assert "0x9e7212ad" in captured.out
    assert "DRY RUN -- nothing sent" in captured.out


def test_redeem_idempotency_guard_refuses_prior_submitted(tmp_path):
    """4. Idempotency guard shared with redeem: refuses if prior submitted row exists unless forced."""
    cond_id = "0x26b64228a9fb13e5c2221cd5879fa0f235cee8ab254c0f094977cc86beeb6a2f"
    log_file = tmp_path / "live_orders.json"
    log_file.write_text(json.dumps([
        {"id": "redeem-999", "condition_id": cond_id, "action": "REDEEM", "status": "submitted"}
    ]), encoding="utf-8")

    with patch.object(le, "RUN", tmp_path):
        with pytest.raises(SystemExit) as exc_info:
            le.redeem(cond_id, live=False)

    assert "redeem-999" in str(exc_info.value)
    assert "submitted" in str(exc_info.value)
    assert "--force" in str(exc_info.value)

    # With force=True, proceeds cleanly
    with patch.object(le, "RUN", tmp_path), \
         patch.object(le, "get_payout_denominator", return_value=1):
        le.redeem(cond_id, force=True, live=False)
