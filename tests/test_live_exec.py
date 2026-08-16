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


# Response shape MEASURED against live relayer 2026-08-16:
#   {"address":"0x6987f531981c95fc998ab20c0935154e9f509a87","nonce":"122"}
# `address` is a ROTATING RELAYER POOL WORKER, never our account. Deliberately
# set to an unrelated address so any code that trusts this field fails the test.
POOL_WORKER = "0x6987f531981c95fc998ab20c0935154e9f509a87"


class MockResponse:
    def __init__(self, data):
        self.data = json.dumps(data).encode("utf-8")

    def read(self):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def make_mock_urlopen(recorded_requests, pool_worker: str = POOL_WORKER, nonce: str = "121", submit_hash: str = "0xabcdef1234567890"):
    def mock_urlopen(req, timeout=30):
        recorded_requests.append(req)
        if "params" in req.full_url:
            return MockResponse({"address": pool_worker, "nonce": nonce})
        elif "submit" in req.full_url:
            return MockResponse({"transactionHash": submit_hash, "status": "PENDING"})
        return MockResponse({})
    return mock_urlopen


def make_live_env(acc: Account, funder: str) -> dict:
    return {
        "POLY_PRIVATE_KEY": acc.key.hex(),
        "POLY_FUNDER": funder,
        "RELAYER_API_KEY": "test_key",
        "RELAYER_API_KEY_ADDRESS": "0x1234567890123456789012345678901234567890",
        "RELAYER_URL": "https://relayer-v2.polymarket.com",
    }


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

    r = sub.add_parser("redeem")
    r.add_argument("condition_id")
    r.add_argument("--skip-resolution-check", action="store_true")

    args = ap.parse_args(["probe", "--cycles", "10", "--min-time-remaining", "120", "--max-complement-bid", "0.80"])
    assert args.cmd == "probe"
    assert args.cycles == 10
    assert args.min_time_remaining == 120.0
    assert args.max_complement_bid == 0.80
    assert args.max_loss == 1.00
    assert args.max_fills == 1

    args_redeem = ap.parse_args(["redeem", "0x26b64228a9fb13e5c2221cd5879fa0f235cee8ab254c0f094977cc86beeb6a2f", "--skip-resolution-check"])
    assert args_redeem.cmd == "redeem"
    assert args_redeem.skip_resolution_check is True


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


def test_build_redeem_submit_payload():
    from_addr = "0xD2C7F5514580184d32C70F6FEA95B69C5Cd72fa0"
    funder = "0xBa7c21Ac8968983e90BEcB989fe978889FEC266b"
    nonce = 121
    deadline = 1786855000
    signature = "0x" + "aa" * 65
    call_data = "0x01b7037c" + "00" * 224

    payload = le.build_redeem_submit_payload(from_addr, funder, nonce, deadline, signature, call_data)
    assert payload["type"] == "WALLET"
    assert payload["from"] == from_addr
    assert payload["to"] == le.DEPOSIT_WALLET_FACTORY
    assert payload["nonce"] == "121"
    assert isinstance(payload["nonce"], str)
    assert payload["signature"] == signature
    assert "metadata" not in payload

    params = payload["depositWalletParams"]
    assert params["depositWallet"] == funder
    assert params["deadline"] == str(deadline)
    assert isinstance(params["deadline"], str)
    assert len(params["calls"]) == 1
    assert params["calls"][0]["target"] == le.CTF_CONTRACT
    assert params["calls"][0]["value"] == "0"
    assert isinstance(params["calls"][0]["value"], str)
    assert params["calls"][0]["data"] == call_data


def test_redeem_dry_run():
    cond_id = "0x26b64228a9fb13e5c2221cd5879fa0f235cee8ab254c0f094977cc86beeb6a2f"
    with patch.object(le, "get_payout_denominator", return_value=1) as mock_denom, \
         patch("urllib.request.urlopen") as mock_url, \
         patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
        le.redeem(cond_id, live=False)

    out = mock_stdout.getvalue()
    assert "resolved        yes" in out
    assert "submit_payload_preview" in out
    assert '"nonce": "0"' in out
    assert '"depositWalletParams"' in out
    assert '"signature"' in out
    assert "DRY RUN -- nothing sent" in out

    mock_url.assert_not_called()
    mock_denom.assert_called_once_with(cond_id)


def test_redeem_dry_run_rpc_unreachable():
    cond_id = "0x26b64228a9fb13e5c2221cd5879fa0f235cee8ab254c0f094977cc86beeb6a2f"
    with patch.object(le, "get_payout_denominator", return_value=None), \
         patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
        le.redeem(cond_id, live=False)

    out = mock_stdout.getvalue()
    assert "resolved        unknown (RPC unreachable)" in out


def test_redeem_live_unresolved_raises():
    cond_id = "0x26b64228a9fb13e5c2221cd5879fa0f235cee8ab254c0f094977cc86beeb6a2f"
    with patch.object(le, "get_payout_denominator", return_value=0), \
         patch("urllib.request.urlopen") as mock_url:
        with pytest.raises(SystemExit) as exc_info:
            le.redeem(cond_id, live=True)
        assert f"Condition {cond_id} is not resolved yet (payoutDenominator == 0)" in str(exc_info.value)
    mock_url.assert_not_called()


def test_redeem_live_unresolved_skip_check_still_raises():
    cond_id = "0x26b64228a9fb13e5c2221cd5879fa0f235cee8ab254c0f094977cc86beeb6a2f"
    with patch.object(le, "get_payout_denominator", return_value=0), \
         patch("urllib.request.urlopen") as mock_url:
        with pytest.raises(SystemExit) as exc_info:
            le.redeem(cond_id, skip_resolution_check=True, live=True)
        assert f"Condition {cond_id} is not resolved yet (payoutDenominator == 0)" in str(exc_info.value)
    mock_url.assert_not_called()


def test_redeem_live_unknown_resolution_raises():
    cond_id = "0x26b64228a9fb13e5c2221cd5879fa0f235cee8ab254c0f094977cc86beeb6a2f"
    with patch.object(le, "get_payout_denominator", return_value=None), \
         patch("urllib.request.urlopen") as mock_url:
        with pytest.raises(SystemExit) as exc_info:
            le.redeem(cond_id, live=True)
        msg = str(exc_info.value)
        assert f"Cannot determine resolution status for {cond_id}" in msg
        assert "all RPC endpoints failed" in msg
        assert "pass --skip-resolution-check to bypass" in msg
    mock_url.assert_not_called()


def test_redeem_live_unknown_resolution_skip_check_proceeds():
    acc = Account.create()
    funder = "0xBa7c21Ac8968983e90BEcB989fe978889FEC266b"
    cond_id = "0x26b64228a9fb13e5c2221cd5879fa0f235cee8ab254c0f094977cc86beeb6a2f"
    env_vars = make_live_env(acc, funder)
    recorded_requests = []
    mock_urlopen = make_mock_urlopen(recorded_requests)

    with patch.dict(os.environ, env_vars, clear=False), \
         patch.object(le, "get_payout_denominator", return_value=None), \
         patch("urllib.request.urlopen", side_effect=mock_urlopen):
        le.redeem(cond_id, skip_resolution_check=True, live=True)

    assert len(recorded_requests) == 2
    assert "submit" in recorded_requests[1].full_url


def test_redeem_live_mock():
    """Verify gasless redemption request construction and wire types against
    official client schema @polymarket/builder-relayer-client@0.0.10 dist/types.d.ts:147-154.
    """
    acc = Account.create()
    funder = "0xBa7c21Ac8968983e90BEcB989fe978889FEC266b"
    cond_id = "0x26b64228a9fb13e5c2221cd5879fa0f235cee8ab254c0f094977cc86beeb6a2f"
    env_vars = make_live_env(acc, funder)
    recorded_requests = []
    mock_urlopen = make_mock_urlopen(recorded_requests)

    import time
    t_before = int(time.time())
    with patch.dict(os.environ, env_vars, clear=False), \
         patch.object(le, "get_payout_denominator", return_value=1), \
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
    # Proves the submit body carries our EOA and not the worker address echoed by the params endpoint
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


def test_redeem_ignores_params_response_address():
    """Regression guard: verify that the pool-worker address in the params response
    appears nowhere in the submitted batch payload.
    """
    acc = Account.create()
    funder = "0xBa7c21Ac8968983e90BEcB989fe978889FEC266b"
    cond_id = "0x26b64228a9fb13e5c2221cd5879fa0f235cee8ab254c0f094977cc86beeb6a2f"
    env_vars = make_live_env(acc, funder)
    recorded_requests = []
    mock_urlopen = make_mock_urlopen(recorded_requests)

    with patch.dict(os.environ, env_vars, clear=False), \
         patch.object(le, "get_payout_denominator", return_value=1), \
         patch("urllib.request.urlopen", side_effect=mock_urlopen):
        le.redeem(cond_id, live=True)

    assert len(recorded_requests) == 2
    req_submit = recorded_requests[1]
    raw_json = req_submit.data.decode("utf-8")
    assert POOL_WORKER.lower() not in raw_json.lower(), "Pool worker address must never leak into submit payload"
    body = json.loads(raw_json)
    assert body["from"] != POOL_WORKER
    assert body["depositWalletParams"]["depositWallet"] != POOL_WORKER


def test_get_payout_denominator_failover():
    """First endpoint raises, second returns 0x01, third never consulted.
    POLYGON_RPC is cleared so the built-in list order is deterministic."""
    cond_id = "0x26b64228a9fb13e5c2221cd5879fa0f235cee8ab254c0f094977cc86beeb6a2f"
    calls = []

    def mock_failover_urlopen(req, timeout=5):
        calls.append(req.full_url)
        if len(calls) == 1:
            raise OSError("RPC endpoint unreachable")
        return MockResponse({"jsonrpc": "2.0", "id": 1, "result": "0x0000000000000000000000000000000000000000000000000000000000000001"})

    with patch.dict(os.environ, {}, clear=False), \
         patch("urllib.request.urlopen", side_effect=mock_failover_urlopen):
        os.environ.pop("POLYGON_RPC", None)
        val = le.get_payout_denominator(cond_id)

    assert val == 1
    assert len(calls) == 2
    assert calls[0] == le.POLYGON_RPC_ENDPOINTS[0]
    assert calls[1] == le.POLYGON_RPC_ENDPOINTS[1]


def test_get_payout_denominator_env_override_tried_first():
    """POLYGON_RPC from env takes precedence over the built-in endpoint list."""
    cond_id = "0x26b64228a9fb13e5c2221cd5879fa0f235cee8ab254c0f094977cc86beeb6a2f"
    calls = []

    def mock_urlopen(req, timeout=5):
        calls.append(req.full_url)
        return MockResponse({"jsonrpc": "2.0", "id": 1, "result": "0x0000000000000000000000000000000000000000000000000000000000000001"})

    with patch.dict(os.environ, {"POLYGON_RPC": "https://private.example/rpc"}, clear=False), \
         patch("urllib.request.urlopen", side_effect=mock_urlopen):
        val = le.get_payout_denominator(cond_id)

    assert val == 1
    assert len(calls) == 1
    assert calls[0] == "https://private.example/rpc"


def test_get_payout_denominator_empty_result_raises():
    """Empty eth_call return (0x) indicates contract misconfiguration or wrong chain, raising SystemExit."""
    cond_id = "0x26b64228a9fb13e5c2221cd5879fa0f235cee8ab254c0f094977cc86beeb6a2f"

    def mock_empty_urlopen(req, timeout=5):
        return MockResponse({"jsonrpc": "2.0", "id": 1, "result": "0x"})

    with patch.dict(os.environ, {}, clear=False), \
         patch("urllib.request.urlopen", side_effect=mock_empty_urlopen):
        os.environ.pop("POLYGON_RPC", None)
        with pytest.raises(SystemExit) as exc_info:
            le.get_payout_denominator(cond_id)
        msg = str(exc_info.value)
        assert le.CTF_CONTRACT in msg
        assert "returned empty data" in msg


def test_dry_run_probe():
    with patch("strategy.markets.fetch_live_market") as mock_fetch:
        mock_fetch.return_value = MagicMock(
            market_slug="btc-updown-5m-12345",
            t_remaining=lambda: 180.0,
            up_token="token_up_123",
            down_token="token_down_456",
        )
        le.probe(series="btc-up-or-down-5m", cycles=5, live=False)


def test_redeem_submit_http_error_logs_unknown(tmp_path):
    """1. Submit raises HTTPError -> row exists with status='unknown', exception type and message recorded, non-zero exit."""
    acc = Account.create()
    funder = "0xBa7c21Ac8968983e90BEcB989fe978889FEC266b"
    cond_id = "0x26b64228a9fb13e5c2221cd5879fa0f235cee8ab254c0f094977cc86beeb6a2f"
    env_vars = make_live_env(acc, funder)

    import urllib.error
    def mock_urlopen_http_err(req, timeout=30):
        if "params" in req.full_url:
            return MockResponse({"address": POOL_WORKER, "nonce": "121"})
        elif "submit" in req.full_url:
            raise urllib.error.HTTPError(req.full_url, 504, "Gateway Timeout", {}, None)
        return MockResponse({})

    with patch.dict(os.environ, env_vars, clear=False), \
         patch.object(le, "RUN", tmp_path), \
         patch.object(le, "get_payout_denominator", return_value=1), \
         patch("urllib.request.urlopen", side_effect=mock_urlopen_http_err):
        with pytest.raises(SystemExit) as exc_info:
            le.redeem(cond_id, live=True)

    msg = str(exc_info.value)
    assert "signed and sent" in msg
    assert "HTTPError" in msg

    log_file = tmp_path / "live_orders.json"
    assert log_file.exists()
    entries = json.loads(log_file.read_text(encoding="utf-8"))
    assert len(entries) == 1
    rec = entries[0]
    assert rec["action"] == "REDEEM"
    assert rec["condition_id"] == cond_id
    assert rec["status"] == "unknown"
    assert rec["nonce"] == 121
    assert "error_type" in rec and rec["error_type"] == "HTTPError"
    assert "error" in rec and "504" in rec["error"]
    assert "payload" in rec


def test_redeem_submit_timeout_logs_unknown(tmp_path):
    """2. Submit raises a timeout -> same outcome, distinct exception detail."""
    acc = Account.create()
    funder = "0xBa7c21Ac8968983e90BEcB989fe978889FEC266b"
    cond_id = "0x26b64228a9fb13e5c2221cd5879fa0f235cee8ab254c0f094977cc86beeb6a2f"
    env_vars = make_live_env(acc, funder)

    import urllib.error
    def mock_urlopen_timeout(req, timeout=30):
        if "params" in req.full_url:
            return MockResponse({"address": POOL_WORKER, "nonce": "121"})
        elif "submit" in req.full_url:
            raise urllib.error.URLError("timed out")
        return MockResponse({})

    with patch.dict(os.environ, env_vars, clear=False), \
         patch.object(le, "RUN", tmp_path), \
         patch.object(le, "get_payout_denominator", return_value=1), \
         patch("urllib.request.urlopen", side_effect=mock_urlopen_timeout):
        with pytest.raises(SystemExit) as exc_info:
            le.redeem(cond_id, live=True)

    msg = str(exc_info.value)
    assert "signed and sent" in msg
    assert "URLError" in msg

    log_file = tmp_path / "live_orders.json"
    assert log_file.exists()
    entries = json.loads(log_file.read_text(encoding="utf-8"))
    assert len(entries) == 1
    rec = entries[0]
    assert rec["action"] == "REDEEM"
    assert rec["status"] == "unknown"
    assert rec["error_type"] == "URLError"
    assert "timed out" in rec["error"]


def test_redeem_submit_success_single_row_submitted(tmp_path):
    """3. Submit succeeds -> exactly one row, transitioning pending -> submitted, not two rows.
    Asserts status is 'submitted' against a mock relayer response whose body says PENDING."""
    acc = Account.create()
    funder = "0xBa7c21Ac8968983e90BEcB989fe978889FEC266b"
    cond_id = "0x26b64228a9fb13e5c2221cd5879fa0f235cee8ab254c0f094977cc86beeb6a2f"
    env_vars = make_live_env(acc, funder)
    recorded_requests = []
    # make_mock_urlopen returns {"transactionID": ..., "status": "PENDING"}
    mock_urlopen = make_mock_urlopen(recorded_requests, submit_hash="0xdeadbeef12345678")

    with patch.dict(os.environ, env_vars, clear=False), \
         patch.object(le, "RUN", tmp_path), \
         patch.object(le, "get_payout_denominator", return_value=1), \
         patch("urllib.request.urlopen", side_effect=mock_urlopen):
        le.redeem(cond_id, live=True)

    log_file = tmp_path / "live_orders.json"
    assert log_file.exists()
    entries = json.loads(log_file.read_text(encoding="utf-8"))
    assert len(entries) == 1
    rec = entries[0]
    assert rec["action"] == "REDEEM"
    assert rec["status"] == "submitted"
    assert "0xdeadbeef12345678" in rec["response"]
    assert rec["tx_hash"] == "0xdeadbeef12345678"
    assert len(rec["response"]) <= 400
    assert rec["nonce"] == 121
    assert "payload" in rec


def test_redeem_dry_run_writes_no_row(tmp_path):
    """4. Dry-run writes no row at all."""
    cond_id = "0x26b64228a9fb13e5c2221cd5879fa0f235cee8ab254c0f094977cc86beeb6a2f"

    def mock_urlopen_rpc(req, timeout=5):
        return MockResponse({"jsonrpc": "2.0", "id": 1, "result": "0x0000000000000000000000000000000000000000000000000000000000000001"})

    with patch.dict(os.environ, {}, clear=False), \
         patch.object(le, "RUN", tmp_path), \
         patch("urllib.request.urlopen", side_effect=mock_urlopen_rpc):
        os.environ.pop("POLYGON_RPC", None)
        le.redeem(cond_id, live=False)

    log_file = tmp_path / "live_orders.json"
    assert not log_file.exists()


def test_audit_settlement_relayer_log_reader_finds_redeem_fixture(tmp_path):
    """5. audit_settlement.py's relayer-log reader finds a REDEEM record in a fixture written by _log_order itself."""
    import scripts.audit_settlement as audit

    # Build the fixture using _log_order itself
    log_file = tmp_path / "live_orders.json"
    with patch.object(le, "RUN", tmp_path):
        le._log_order({
            "ts": 1723812345.67,
            "action": "REDEEM",
            "condition_id": "0x26b64228a9fb13e5c2221cd5879fa0f235cee8ab254c0f094977cc86beeb6a2f",
            "safe_funder": "0xBa7c21Ac8968983e90BEcB989fe978889FEC266b",
            "signer": "0xD2C7F5514580184d32C70F6FEA95B69C5Cd72fa0",
            "target": le.CTF_CONTRACT,
            "call_data": "0x01b7037c...",
            "nonce": 121,
            "deadline": 1723812945,
            "payload": {"type": "WALLET"},
            "status": "submitted",
            "tx_hash": "0x9876543210fedcba",
            "response": json.dumps({"transactionHash": "0x9876543210fedcba", "status": "CONFIRMED"}),
        })

    def mock_relayer_get(req, timeout=5):
        return MockResponse({"transactionHash": "0x9876543210fedcba", "state": "MINED", "status": "CONFIRMED"})

    with patch("urllib.request.urlopen", side_effect=mock_relayer_get):
        res = audit.check_relayer_status(log_file=log_file)

    assert res.get("transactionHash") == "0x9876543210fedcba"
    assert res.get("status") == "CONFIRMED"


def test_redeem_submit_exception_log_update_failure_dumps_to_stderr(tmp_path, capsys):
    """R9 Item 1: When submit fails AND _update_order_log fails (returns False),
    SystemExit message states log update failed, and full transaction record reaches stderr."""
    acc = Account.create()
    funder = "0xBa7c21Ac8968983e90BEcB989fe978889FEC266b"
    cond_id = "0x26b64228a9fb13e5c2221cd5879fa0f235cee8ab254c0f094977cc86beeb6a2f"
    env_vars = make_live_env(acc, funder)

    import urllib.error
    def mock_urlopen_http_err(req, timeout=30):
        if "params" in req.full_url:
            return MockResponse({"address": POOL_WORKER, "nonce": "121"})
        elif "submit" in req.full_url:
            raise urllib.error.HTTPError(req.full_url, 504, "Gateway Timeout", {}, None)
        return MockResponse({})

    # Mock _update_order_log to simulate log file update failure
    with patch.dict(os.environ, env_vars, clear=False), \
         patch.object(le, "RUN", tmp_path), \
         patch.object(le, "get_payout_denominator", return_value=1), \
         patch.object(le, "_update_order_log", return_value=False), \
         patch("urllib.request.urlopen", side_effect=mock_urlopen_http_err):
        with pytest.raises(SystemExit) as exc_info:
            le.redeem(cond_id, live=True)

    msg = str(exc_info.value)
    assert "signed and sent" in msg
    assert "could NOT be updated" in msg

    captured = capsys.readouterr()
    assert "ERROR: Failed to update live_orders.json" in captured.err
    assert "REDEEM" in captured.err
    assert cond_id in captured.err
    assert "121" in captured.err


def test_log_order_corrupted_file_renamed_not_destroyed(tmp_path):
    """R9 Item 2: Malformed live_orders.json is preserved under a .corrupt. name
    and not overwritten on parse failure."""
    corrupt_content = '{"broken": json['
    log_file = tmp_path / "live_orders.json"
    log_file.write_text(corrupt_content, encoding="utf-8")

    with patch.object(le, "RUN", tmp_path):
        entry_id = le._log_order({
            "action": "REDEEM",
            "condition_id": "0x1234",
            "status": "pending",
        })

    # New log file exists and contains the new entry
    assert log_file.exists()
    entries = json.loads(log_file.read_text(encoding="utf-8"))
    assert len(entries) == 1
    assert entries[0]["id"] == entry_id

    # The corrupted file was renamed and preserved
    corrupt_files = list(tmp_path.glob("live_orders.corrupt.*.json"))
    assert len(corrupt_files) == 1
    assert corrupt_files[0].read_text(encoding="utf-8") == corrupt_content


def test_atomic_write_json_interrupted_leaves_file_intact(tmp_path):
    """R10 Item 1: Atomic write interrupted midway leaves the original file intact and valid."""
    log_file = tmp_path / "live_orders.json"
    original_data = [
        {"id": "entry-1", "action": "REDEEM", "status": "pending"},
        {"id": "entry-2", "action": "REDEEM", "status": "submitted"},
    ]
    log_file.write_text(json.dumps(original_data, indent=2), encoding="utf-8")

    # Patch os.fsync to raise an IOError simulating an interrupted write
    with patch("os.fsync", side_effect=IOError("Simulated disk error during fsync")):
        res = le._atomic_write_json(log_file, [{"id": "entry-3", "action": "NEW"}])

    assert res is False
    # Original file is intact, uncorrupted, and parses cleanly
    assert log_file.exists()
    recovered = json.loads(log_file.read_text(encoding="utf-8"))
    assert recovered == original_data


def test_redeem_submit_success_log_update_failure_dumps_to_stderr(tmp_path, capsys):
    """R10 Item 2: When submit succeeds but _update_order_log fails, SystemExit is raised,
    stderr carries the transaction record and tx_hash, and message names the ambiguity."""
    acc = Account.create()
    funder = "0xBa7c21Ac8968983e90BEcB989fe978889FEC266b"
    cond_id = "0x26b64228a9fb13e5c2221cd5879fa0f235cee8ab254c0f094977cc86beeb6a2f"
    env_vars = make_live_env(acc, funder)
    mock_urlopen = make_mock_urlopen([], submit_hash="0xabcdef1234567890")

    original_update = le._update_order_log

    def mock_update_order_log(entry_id, updates):
        # Allow pending write, fail submitted update
        if updates.get("status") == "submitted":
            return False
        return original_update(entry_id, updates)

    with patch.dict(os.environ, env_vars, clear=False), \
         patch.object(le, "RUN", tmp_path), \
         patch.object(le, "get_payout_denominator", return_value=1), \
         patch.object(le, "_update_order_log", side_effect=mock_update_order_log), \
         patch("urllib.request.urlopen", side_effect=mock_urlopen):
        with pytest.raises(SystemExit) as exc_info:
            le.redeem(cond_id, live=True)

    msg = str(exc_info.value)
    assert "Relayer accepted transaction" in msg
    assert "audit log update failed" in msg
    assert "0xabcdef1234567890" in msg

    captured = capsys.readouterr()
    assert "ERROR: Relayer accepted transaction" in captured.err
    assert "0xabcdef1234567890" in captured.err
    assert "submitted" in captured.err


def test_redeem_submit_keyboard_interrupt_logs_interrupted(tmp_path, capsys):
    """R10 Item 2: KeyboardInterrupt during submit stamps status='interrupted' and exits with warning."""
    acc = Account.create()
    funder = "0xBa7c21Ac8968983e90BEcB989fe978889FEC266b"
    cond_id = "0x26b64228a9fb13e5c2221cd5879fa0f235cee8ab254c0f094977cc86beeb6a2f"
    env_vars = make_live_env(acc, funder)

    def mock_urlopen_interrupt(req, timeout=30):
        if "params" in req.full_url:
            return MockResponse({"address": POOL_WORKER, "nonce": "121"})
        elif "submit" in req.full_url:
            raise KeyboardInterrupt()
        return MockResponse({})

    with patch.dict(os.environ, env_vars, clear=False), \
         patch.object(le, "RUN", tmp_path), \
         patch.object(le, "get_payout_denominator", return_value=1), \
         patch("urllib.request.urlopen", side_effect=mock_urlopen_interrupt):
        with pytest.raises(SystemExit) as exc_info:
            le.redeem(cond_id, live=True)

    msg = str(exc_info.value)
    assert "KeyboardInterrupt" in msg
    assert "may have been broadcast" in msg

    log_file = tmp_path / "live_orders.json"
    assert log_file.exists()
    entries = json.loads(log_file.read_text(encoding="utf-8"))
    assert len(entries) == 1
    assert entries[0]["status"] == "interrupted"
    assert entries[0]["error_type"] == "KeyboardInterrupt"


def test_log_order_corrupt_rename_failure_aborts_without_overwriting(tmp_path):
    """R10 Item 4.1: If corrupt log file cannot be renamed, _log_order aborts via SystemExit rather than overwriting."""
    corrupt_content = '{"broken": json['
    log_file = tmp_path / "live_orders.json"
    log_file.write_text(corrupt_content, encoding="utf-8")

    # Patch os.replace to raise OSError when renaming corrupt file
    with patch.object(le, "RUN", tmp_path), \
         patch("os.replace", side_effect=OSError("Access denied during rename")):
        with pytest.raises(SystemExit) as exc_info:
            le._log_order({
                "action": "REDEEM",
                "condition_id": "0x1234",
                "status": "pending",
            })

    assert "Refusing to overwrite corrupted log file" in str(exc_info.value)
    # The file still has its original corrupt content, NOT overwritten
    assert log_file.read_text(encoding="utf-8") == corrupt_content
