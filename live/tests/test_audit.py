"""live/tests/test_audit.py - Unit tests for three-way audit module."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from engine.audit import (
    AuditResult,
    audit_three_way,
    format_audit_report,
    read_merged_amount_from_logs,
)
from engine.order_registry import OrderRecord, OrderRegistry, FillRecord


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    return tmp_path / "test_live.db"


@pytest.fixture
def mock_pinned_market(monkeypatch):
    m = MagicMock()
    m.up_token = "tok_up_123"
    m.down_token = "tok_dn_456"
    monkeypatch.setattr("engine.audit.fetch_pinned_market", lambda *args, **kwargs: m)
    return m


def test_audit_three_way_agree(temp_db, mock_pinned_market, monkeypatch, tmp_path):
    registry = OrderRegistry(temp_db)
    now_ms = 1787000000000

    # Insert 2 orders
    registry.create_order(
        OrderRecord(
            id="uuid-up",
            order_id="venue-up",
            condition_id="0xabc",
            token_id="tok_up_123",
            side="BUY",
            price=0.60,
            original_size=5.0,
            status="filled",
            posted_ts=now_ms,
            last_polled_ts=now_ms,
            pair_id="pair-test1",
        )
    )
    registry.create_order(
        OrderRecord(
            id="uuid-dn",
            order_id="venue-dn",
            condition_id="0xabc",
            token_id="tok_dn_456",
            side="BUY",
            price=0.35,
            original_size=5.0,
            status="filled",
            posted_ts=now_ms,
            last_polled_ts=now_ms,
            pair_id="pair-test1",
        )
    )
    # Insert fills
    registry.record_fill(FillRecord("tr1", "uuid-up", 5.0, 0.60, now_ms))
    registry.record_fill(FillRecord("tr2", "uuid-dn", 5.0, 0.35, now_ms))

    # Mock venue client
    client = MagicMock()
    client.get_order.side_effect = lambda vid: {
        "venue-up": {"asset_id": "tok_up_123", "size_matched": "5.0", "status": "MATCHED"},
        "venue-dn": {"asset_id": "tok_dn_456", "size_matched": "5.0", "status": "MATCHED"},
    }[vid]

    # Mock chain call (0 held because 5 merged)
    monkeypatch.setattr("engine.audit.get_onchain_erc1155_balance", lambda tok, owner: 0.0)

    # Mock merge log
    orders_json = tmp_path / "live_orders.json"
    orders_json.write_text(
        '[{"action": "MERGE", "condition_id": "0xabc", "amount": 5.0, "state": "STATE_EXECUTED"}]'
    )

    res = audit_three_way(
        "pair-test1",
        client=client,
        funder="0x123",
        db_path=temp_db,
        orders_log_path=orders_json,
    )
    assert res.agree is True
    assert len(res.divergences) == 0
    assert res.registry_up_filled == 5.0
    assert res.venue_up_matched == 5.0
    assert res.merged_amount == 5.0

    report = format_audit_report(res)
    assert "RESULT:          AGREE" in report
    assert "ALL 3 LAYERS AGREE" in report


def test_audit_three_way_divergence_detected(temp_db, mock_pinned_market, monkeypatch, tmp_path):
    registry = OrderRegistry(temp_db)
    now_ms = 1787000000000

    # Registry has 0 fills recorded
    registry.create_order(
        OrderRecord(
            id="uuid-up",
            order_id="venue-up",
            condition_id="0xabc",
            token_id="tok_up_123",
            side="BUY",
            price=0.60,
            original_size=5.0,
            status="cancelled",
            posted_ts=now_ms,
            last_polled_ts=now_ms,
            pair_id="pair-test2",
        )
    )

    # Venue reports matched 5.0
    client = MagicMock()
    client.get_order.return_value = {
        "asset_id": "tok_up_123",
        "size_matched": "5.0",
        "status": "MATCHED",
    }

    monkeypatch.setattr("engine.audit.get_onchain_erc1155_balance", lambda tok, owner: 5.0)
    orders_json = tmp_path / "live_orders.json"
    orders_json.write_text("[]")

    res = audit_three_way(
        "pair-test2",
        client=client,
        funder="0x123",
        db_path=temp_db,
        orders_log_path=orders_json,
    )
    assert res.agree is False
    assert len(res.divergences) >= 2  # fill mismatch + on-chain mismatch + status mismatch
    report = format_audit_report(res)
    assert "RESULT:          DIVERGENCE" in report
    assert "DIVERGENCES DETECTED" in report


def test_read_merged_amount_from_calldata(tmp_path):
    orders_json = tmp_path / "live_orders.json"
    # Call data with amount = 5,000,000 (0x4c4b40)
    orders_json.write_text(
        """[
        {
            "action": "MERGE",
            "condition_id": "0x70de0744c8c2d7d31fab0f2d75b44e7d7577807cbff2e39b02ab547c68d81b45",
            "call_data": "0x9e7212ad0000000000000000000000002791bca1f2de4661ed88a30c99a7a9449aa84174000000000000000000000000000000000000000000000000000000000000000070de0744c8c2d7d31fab0f2d75b44e7d7577807cbff2e39b02ab547c68d81b4500000000000000000000000000000000000000000000000000000000000000a000000000000000000000000000000000000000000000000000000000004c4b40000000000000000000000000000000000000000000000000000000000000000200000000000000000000000000000000000000000000000000000000000000010000000000000000000000000000000000000000000000000000000000000002",
            "state": "STATE_EXECUTED"
        }
    ]"""
    )
    amt = read_merged_amount_from_logs(
        "0x70de0744c8c2d7d31fab0f2d75b44e7d7577807cbff2e39b02ab547c68d81b45",
        orders_log_path=orders_json,
    )
    assert amt == 5.0
