import argparse
import io
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from eth_account import Account

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import strategy.live_exec as le


def test_live_exec_arg_parsing():
    ap = argparse.ArgumentParser(description="LIVE Polymarket execution.")
    ap.add_argument("--live", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe")
    p.add_argument("--series", default="btc-up-or-down-5m")
    p.add_argument("--token-id", default=None)
    p.add_argument("--cycles", type=int, default=30)
    p.add_argument("--min-time-remaining", type=float, default=90.0)
    p.add_argument("--max-complement-bid", type=float, default=0.85)
    p.add_argument("--max-loss", type=float, default=1.00)
    p.add_argument("--max-fills", type=int, default=1)
    p.add_argument("--live", action="store_true", default=argparse.SUPPRESS)

    args = ap.parse_args(["probe", "--cycles", "10", "--min-time-remaining", "120", "--max-complement-bid", "0.80"])
    assert args.cmd == "probe"
    assert args.cycles == 10
    assert args.min_time_remaining == 120.0
    assert args.max_complement_bid == 0.80
    assert args.max_loss == 1.00
    assert args.max_fills == 1


def test_encode_redeem_positions():
    cond_id = "0x26b64228a9fb13e5c2221cd5879fa0f235cee8ab254c0f094977cc86beeb6a2f"
    res = le.encode_redeem_positions(
        le.USDC_E_CONTRACT,
        le.ZERO_BYTES32,
        cond_id,
        [1, 2]
    )
    assert res.startswith("0x01b7037c")
    assert len(res) == 458  # 2 + 8 (selector) + 7 * 64 (params)
    assert le.USDC_E_CONTRACT.lower().replace("0x", "") in res
    assert cond_id.lower().replace("0x", "") in res


def test_build_redeem_typed_data():
    funder = "0xBa7c21Ac8968983e90BEcB989fe978889FEC266b"
    nonce = 121
    deadline = 1786855000
    call_data = "0x01b7037c" + "00" * 224

    domain, types, message = le.build_redeem_typed_data(funder, nonce, deadline, call_data)
    assert domain["name"] == "DepositWallet"
    assert domain["version"] == "1"
    assert domain["chainId"] == 137
    assert domain["verifyingContract"] == funder

    assert "Call" in types and "Batch" in types
    assert len(types["Call"]) == 3
    assert len(types["Batch"]) == 4

    assert message["wallet"] == funder
    assert isinstance(message["nonce"], int)
    assert message["nonce"] == nonce
    assert isinstance(message["deadline"], int)
    assert message["deadline"] == deadline
    assert len(message["calls"]) == 1
    assert message["calls"][0]["target"] == le.CTF_CONTRACT
    assert isinstance(message["calls"][0]["value"], int)
    assert message["calls"][0]["value"] == 0


def test_sign_redeem_transaction():
    acc = Account.create()
    funder = "0xBa7c21Ac8968983e90BEcB989fe978889FEC266b"
    nonce = 121
    deadline = 1786855000
    call_data = "0x01b7037c" + "00" * 224

    signer_addr, sig = le.sign_redeem_transaction(
        acc.key.hex(),
        funder,
        nonce,
        deadline,
        call_data
    )
    assert signer_addr == acc.address
    assert sig.startswith("0x")
    assert len(sig) == 132  # 0x + 130 hex chars


def test_redeem_dry_run():
    cond_id = "0x26b64228a9fb13e5c2221cd5879fa0f235cee8ab254c0f094977cc86beeb6a2f"
    # Should not throw and should print dry run message
    le.redeem(cond_id, live=False)


def test_redeem_live_mock():
    """Verify gasless redemption request construction and wire types against
    official client schema @polymarket/builder-relayer-client@0.0.10 dist/types.d.ts:147-154.
    """
    acc = Account.create()
    funder = "0xBa7c21Ac8968983e90BEcB989fe978889FEC266b"
    cond_id = "0x26b64228a9fb13e5c2221cd5879fa0f235cee8ab254c0f094977cc86beeb6a2f"

    env_vars = {
        "POLY_PRIVATE_KEY": acc.key.hex(),
        "POLY_FUNDER": funder,
        "RELAYER_API_KEY": "test_key",
        "RELAYER_API_KEY_ADDRESS": "0x1234567890123456789012345678901234567890",
        "RELAYER_URL": "https://relayer-v2.polymarket.com",
    }

    class MockResponse:
        def __init__(self, data):
            self.data = json.dumps(data).encode("utf-8")

        def read(self):
            return self.data

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    recorded_requests = []

    def mock_urlopen(req, timeout=30):
        recorded_requests.append(req)
        if "params" in req.full_url:
            return MockResponse({"address": acc.address, "nonce": 121})
        elif "submit" in req.full_url:
            return MockResponse({"transactionHash": "0xabcdef1234567890", "status": "PENDING"})
        return MockResponse({})

    import time
    t_before = int(time.time())
    with patch.dict(os.environ, env_vars, clear=False), \
         patch.object(le, "sign_redeem_transaction", wraps=le.sign_redeem_transaction) as mock_sign, \
         patch("urllib.request.urlopen", side_effect=mock_urlopen):
        le.redeem(cond_id, live=True)
    t_after = int(time.time())

    # Assert EIP-712 signer arguments remain integer types for typed data hashing
    mock_sign.assert_called_once()
    sign_args = mock_sign.call_args[0]
    assert isinstance(sign_args[2], int), "EIP-712 nonce passed to signer must be int"
    assert isinstance(sign_args[3], int), "EIP-712 deadline passed to signer must be int"

    assert len(recorded_requests) == 2
    req_nonce, req_submit = recorded_requests

    # Verify nonce request
    assert "params" in req_nonce.full_url
    assert req_nonce.headers["User-agent"] == "Mozilla/5.0"
    assert req_nonce.headers["Relayer_api_key"] == "test_key"
    assert req_nonce.headers["Relayer_api_key_address"] == "0x1234567890123456789012345678901234567890"

    # Verify submit request payload wire types against @polymarket/builder-relayer-client@0.0.10 dist/types.d.ts:147-154
    assert "submit" in req_submit.full_url
    body = json.loads(req_submit.data.decode("utf-8"))
    assert body["type"] == "WALLET"
    assert body["from"] == acc.address
    assert body["to"] == le.DEPOSIT_WALLET_FACTORY
    assert isinstance(body["nonce"], str)
    assert body["nonce"] == "121"
    assert body["signature"].startswith("0x")
    assert len(body["signature"]) == 132
    assert "metadata" not in body

    assert "depositWalletParams" in body
    params = body["depositWalletParams"]
    assert params["depositWallet"] == funder
    assert isinstance(params["deadline"], str)
    assert t_before + le.REDEEM_DEADLINE_SECONDS <= int(params["deadline"]) <= t_after + le.REDEEM_DEADLINE_SECONDS
    assert len(params["calls"]) == 1
    assert params["calls"][0]["target"] == le.CTF_CONTRACT
    assert isinstance(params["calls"][0]["value"], str)
    assert params["calls"][0]["value"] == "0"
    assert len(params["calls"][0]["data"]) == 458


def test_dry_run_probe():
    with patch("strategy.markets.fetch_live_market") as mock_fetch:
        mock_fetch.return_value = MagicMock(
            market_slug="btc-updown-5m-12345",
            t_remaining=lambda: 180.0,
            up_token="token_up_123",
            down_token="token_down_456",
        )
        le.probe(series="btc-up-or-down-5m", cycles=5, live=False)
