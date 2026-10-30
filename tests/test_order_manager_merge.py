"""Stage 1 unit tests: mergePositions ABI encoding, selector derivation,
pre-flight guards, idempotency protection, and dry-run safety.
"""
from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from eth_account import Account
from eth_utils import keccak

import core_brain.order_manager as le


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

    mock_client = MagicMock()
    mock_client.get_balance_allowance.return_value = {"balance": "10000000"}  # 10 shares

    acc = Account.create()
    funder = "0xBa7c21Ac8968983e90BEcB989fe978889FEC266b"
    env_vars = make_live_env(acc, funder)

    # --force must clear the idempotency guard specifically; with balances funded
    # and the condition unresolved, every other guard passes and the dry run is clean.
    with patch.dict(os.environ, env_vars, clear=False), \
         patch.object(le, "RUN", tmp_path), \
         patch.object(le, "client", return_value=mock_client), \
         patch.object(le, "get_payout_denominator", return_value=0), \
         patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
        le.merge(cond_id, amount=1.0, force=True, live=False)

    out = mock_stdout.getvalue()
    assert "DRY RUN -- nothing sent" in out
    assert "PRE-FLIGHT FAILED" not in out


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
         patch.object(le, "client", return_value=mock_client), \
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
         patch.object(le, "client", return_value=mock_client), \
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
         patch.object(le, "client", return_value=mock_client), \
         patch.object(le, "get_payout_denominator", return_value=0):
        with pytest.raises(SystemExit) as exc_info:
            le.merge(cond_id, amount=1.0, live=True)

    msg = str(exc_info.value)
    assert "Insufficient balance on DOWN token" in msg
    assert "holds 0.20" in msg
    assert "needs 1.00" in msg
    assert "short by 0.80" in msg


def test_merge_dry_run_touches_no_network(tmp_path, capsys):
    """5. Dry-run prints execution plan, token IDs, expected USDC, net
    collateral and calldata without sending any network request."""
    cond_id = "0x26b64228a9fb13e5c2221cd5879fa0f235cee8ab254c0f094977cc86beeb6a2f"

    mock_client = MagicMock()
    mock_client.get_balance_allowance.return_value = {"balance": "10000000"}  # 10 shares

    acc = Account.create()
    funder = "0xBa7c21Ac8968983e90BEcB989fe978889FEC266b"
    env_vars = make_live_env(acc, funder)

    with patch.dict(os.environ, env_vars, clear=False), \
         patch.object(le, "RUN", tmp_path), \
         patch.object(le, "client", return_value=mock_client), \
         patch.object(le, "get_payout_denominator", return_value=0), \
         patch("urllib.request.urlopen") as mock_urlopen:
        le.merge(cond_id, amount=1.0, live=False)

    mock_urlopen.assert_not_called()
    captured = capsys.readouterr()
    assert "MERGE (gasless via Polymarket Relayer)" in captured.out
    assert "expected_usdc   $1.00" in captured.out
    # The relayer pays the gas, so the wallet receives the whole dollar.
    # `test_merge_floor_is_gasless.py` owns that claim in full.
    assert "net_collateral  $1.00" in captured.out
    assert "0x9e7212ad" in captured.out
    assert "DRY RUN -- nothing sent" in captured.out


def test_merge_dry_run_reports_guard_failures_and_exits_nonzero(tmp_path, capsys):
    """A dry run must refuse exactly where --live refuses: unfunded legs and a
    resolved condition are reported as PRE-FLIGHT FAILED, not previewed as ready."""
    cond_id = "0x26b64228a9fb13e5c2221cd5879fa0f235cee8ab254c0f094977cc86beeb6a2f"
    acc = Account.create()
    funder = "0xBa7c21Ac8968983e90BEcB989fe978889FEC266b"
    env_vars = make_live_env(acc, funder)

    mock_client = MagicMock()
    # Zero balances for both legs -> underfunded
    mock_client.get_balance_allowance.return_value = {"balance": "0"}

    with patch.dict(os.environ, env_vars, clear=False), \
         patch.object(le, "RUN", tmp_path), \
         patch.object(le, "client", return_value=mock_client), \
         patch.object(le, "get_payout_denominator", return_value=1), \
         patch("urllib.request.urlopen") as mock_urlopen:
        with pytest.raises(SystemExit) as exc_info:
            le.merge(cond_id, amount=1.0, live=False)

    mock_urlopen.assert_not_called()
    assert exc_info.value.code != 0
    out = capsys.readouterr().out
    assert "PRE-FLIGHT FAILED -- --live would refuse:" in out
    assert "Insufficient balance on UP token" in out
    assert "Insufficient balance on DOWN token" in out
    assert "already resolved (payoutDenominator == 1 > 0). Use redeem instead." in out
    assert "DRY RUN -- nothing sent" not in out


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


# ── #646 live-merge reset ──────────────────────────────────────────────────

from core_brain.order_registry import OrderRegistry as _RealRegistry

COND_MERGE = "0x26b64228a9fb13e5c2221cd5879fa0f235cee8ab254c0f094977cc86beeb6a2f"


def _merge_tokens(cond_id):
    from core_brain.merge_pairs import (
        USDC_E_CONTRACT, ZERO_BYTES32, get_collection_id, get_position_id,
    )
    return [
        get_position_id(USDC_E_CONTRACT,
                        get_collection_id(ZERO_BYTES32, cond_id, idx))
        for idx in (1, 2)
    ]


class FakeMergeClient:
    """CLOB double for the merge path. No network."""

    def __init__(self, balance_shares=10.0):
        self.creds = object()
        self.balance_shares = balance_shares
        self.cancelled: list[str] = []

    def get_balance_allowance(self, params):
        return {"balance": str(int(self.balance_shares * 1e6))}

    def get_open_orders(self):
        return []

    def get_trades(self, params=None):
        return []

    def cancel_order(self, payload):
        self.cancelled.append(str(getattr(payload, "orderID", payload)))
        return {"success": True}


def _seed_merged_pair(reg, cond_id, up_token, dn_token,
                      up_shares=10.0, dn_shares=10.0):
    """Two filled legs plus the quotes ledger the merge close needs."""
    import uuid as _uuid
    from core_brain.order_registry import FillRecord, OrderRecord, QuoteRecord
    now = 1_000_000
    for tok, side, price, shares in ((up_token, "UP", 0.47, up_shares),
                                     (dn_token, "DOWN", 0.51, dn_shares)):
        order = OrderRecord(
            id=str(_uuid.uuid4()), order_id=f"venue-{tok[-6:]}",
            condition_id=cond_id, token_id=tok, side="BUY", price=price,
            original_size=shares, status="filled", posted_ts=now,
            last_polled_ts=now, pair_id="pair-merge",
            max_pair_cost_at_post=0.995,
        )
        reg.create_order(order)
        reg.record_fill(FillRecord(
            trade_id=f"trade-{order.id}", order_uuid=order.id, size=shares,
            price=price, venue_ts=now))
        reg.log_quote(QuoteRecord(
            ts=now / 1000.0, condition_id=cond_id, token_id=tok, side=side,
            price=price, size=shares))


def _live_merge(monkeypatch, tmp_path, reg, *, submit_state="STATE_EXECUTED",
                positions=None, amount=10.0, balance_shares=10.0,
                break_close=False):
    """Run `merge(live=True)` with every venue call doubled."""
    import core_brain.order_registry as ore
    acc = Account.create()
    funder = "0xBa7c21Ac8968983e90BEcB989fe978889FEC266b"
    monkeypatch.setattr(le, "RUN", tmp_path)
    monkeypatch.setattr(le, "get_payout_denominator", lambda cid: 0)
    monkeypatch.setattr(le, "is_approved_for_all", lambda *a, **k: True)
    monkeypatch.setattr(le, "client", lambda f: FakeMergeClient(balance_shares))
    monkeypatch.setattr(ore, "OrderRegistry", lambda *a, **k: reg)
    if break_close:
        def _boom(close):
            raise RuntimeError("close write exploded")
        monkeypatch.setattr(reg, "log_close", _boom)

    def _fake_urlopen(req, timeout=None):
        url = req.get_full_url() if hasattr(req, "get_full_url") else req
        if "data-api" in str(url):
            return MockResponse(positions if positions is not None else [])
        if str(url).rstrip("/").endswith("/submit"):
            return MockResponse({"state": submit_state,
                                 "transactionHash": "0xabc"})
        if "transactions/params" in str(url):
            return MockResponse({"nonce": 1})
        raise AssertionError(f"unexpected network: {url}")

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    env = make_live_env(acc, funder)
    with patch.dict(os.environ, env, clear=False):
        le.merge(COND_MERGE, amount=amount, live=True, neg_risk=False)


def _merge_reg(tmp_path):
    # Module-global binding: `_live_merge` patches the registry attribute
    # for the code under test, and this helper must keep the real class.
    return _RealRegistry(db_path=tmp_path / "merge.db")


class TestLiveMergeReset:
    def test_an_executed_merge_with_a_close_write_resets(
            self, tmp_path, monkeypatch, capsys):
        """#646: EXECUTED plus a landed close clears the row, marks awaiting."""
        up_tok, dn_tok = _merge_tokens(COND_MERGE)
        reg = _merge_reg(tmp_path)
        _seed_merged_pair(reg, COND_MERGE, up_tok, dn_tok)
        _live_merge(monkeypatch, tmp_path, reg)
        capsys.readouterr()

        closes = [c for c in reg.get_all_closes() if c.get("method") == "merge"]
        assert len(closes) == 1
        assert reg.requalification_marks()[COND_MERGE.lower()].status == "awaiting"

    def test_a_failed_close_write_never_resets(
            self, tmp_path, monkeypatch, capsys):
        """#646: the merge stands, but nothing is proven flat."""
        up_tok, dn_tok = _merge_tokens(COND_MERGE)
        reg = _merge_reg(tmp_path)
        _seed_merged_pair(reg, COND_MERGE, up_tok, dn_tok)
        _live_merge(monkeypatch, tmp_path, reg, break_close=True)
        err = capsys.readouterr().err

        assert "Failed to log close record" in err
        assert reg.requalification_marks() == {}

    def test_failed_and_submitted_responses_never_reset(
            self, tmp_path, monkeypatch, capsys):
        """#646: only STATE_EXECUTED even attempts the proof."""
        for state in ("STATE_FAILED", "STATE_SUBMITTED"):
            up_tok, dn_tok = _merge_tokens(COND_MERGE)
            reg = _merge_reg(tmp_path / state)
            _seed_merged_pair(reg, COND_MERGE, up_tok, dn_tok)
            _live_merge(monkeypatch, tmp_path / state, reg, submit_state=state)
            capsys.readouterr()

            assert reg.get_all_closes() == []
            assert reg.requalification_marks() == {}

    def test_a_partial_merge_leaves_the_larger_leg_managed(
            self, tmp_path, monkeypatch, capsys):
        """#646: 20 UP / 5 DOWN merged 5 leaves 15 UP, no reset."""
        up_tok, dn_tok = _merge_tokens(COND_MERGE)
        reg = _merge_reg(tmp_path)
        _seed_merged_pair(reg, COND_MERGE, up_tok, dn_tok,
                          up_shares=20.0, dn_shares=5.0)
        _live_merge(monkeypatch, tmp_path, reg, amount=5.0)
        capsys.readouterr()

        from core_brain.order_registry import inventory_from_registry
        inv = inventory_from_registry(COND_MERGE, up_tok, dn_tok,
                                      db_path=tmp_path / "merge.db")
        assert inv.up_shares == pytest.approx(15.0)
        assert inv.down_shares == pytest.approx(0.0)
        assert reg.requalification_marks() == {}


def test_v2_merge_encoding_and_target():
    from core_brain.merge_pairs import (
        encode_v2_merge,
        merge_target,
        ROUTER_V2_CONTRACT,
    )

    # V2 routing targets Router directly regardless of neg_risk
    assert merge_target(None, version="v2") == ROUTER_V2_CONTRACT
    assert merge_target(True, version="v2") == ROUTER_V2_CONTRACT
    assert merge_target(False, version="v2") == ROUTER_V2_CONTRACT

    # 1.0 share = 1_000_000 base units
    cid = "0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"
    calldata = encode_v2_merge(cid, 1_000_000)
    assert calldata.startswith("0x5b63685e")
    assert len(calldata) == 2 + 8 + 64 + 64
    assert calldata.endswith("00000000000000000000000000000000000000000000000000000000000f4240")
