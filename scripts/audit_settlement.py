"""One-pass settlement audit script for spread-hunter.

Captures:
1. Native USDC and Bridged USDC.e balances at all 3 candidate addresses.
2. POL native balances at all 3 candidate addresses.
3. CLOB get_balance_allowance across all supported signature types.
4. Polymarket Data-API positions and portfolio values.
5. Recent relayer transaction status from run/live_orders.json or CLI argument.
"""

import argparse
import json
import os
import sys
import urllib.request
from decimal import Decimal
from pathlib import Path

RPC_ENDPOINTS = [
    "https://polygon.drpc.org",
    "https://1rpc.io/matic",
    "https://polygon-bor-rpc.publicnode.com",
    "https://rpc.ankr.com/polygon",
]

ADDRESSES = {
    "Safe / Deposit Proxy": "0xBa7c21Ac8968983e90BEcB989fe978889FEC266b",
    "Signer EOA": "0xD2C7F5514580184d32C70F6FEA95B69C5Cd72fa0",
    "Deposit Forwarder": "0xF495052dA3a06eB189f6619e8eE197fe5EdC4c82",
}

TOKENS = {
    "Native USDC": "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",
    "Bridged USDC.e": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174",
}


def rpc_call(method: str, params: list):
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    last_err = None
    for endpoint in RPC_ENDPOINTS:
        try:
            req = urllib.request.Request(
                endpoint,
                data=payload,
                headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                res = json.loads(resp.read().decode())
                if "result" in res:
                    return res["result"], endpoint
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"All RPC endpoints failed: {last_err}")


def get_token_balance(token_addr: str, owner_addr: str) -> tuple[Decimal, str]:
    calldata = "0x70a08231" + owner_addr[2:].lower().rjust(64, "0")
    res, host = rpc_call("eth_call", [{"to": token_addr, "data": calldata}, "latest"])
    val = Decimal(int(res, 16)) / Decimal(10**6) if res and res != "0x" else Decimal(0)
    return val, res


def get_pol_balance(owner_addr: str) -> tuple[Decimal, str]:
    res, host = rpc_call("eth_getBalance", [owner_addr, "latest"])
    val = Decimal(int(res, 16)) / Decimal(10**18) if res and res != "0x" else Decimal(0)
    return val, res


def fetch_data_api(addr: str) -> dict:
    url = f"https://data-api.polymarket.com/value?user={addr}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return [{"error": str(e)}]


def check_clob_balance_allowance(funder: str, sig_type: int) -> dict:
    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
        from dotenv import load_dotenv

        load_dotenv()
        key = os.environ.get("PRIVATE_KEY")
        if not key:
            return {"error": "PRIVATE_KEY missing from .env"}

        client = ClobClient(
            host="https://clob.polymarket.com",
            key=key,
            chain_id=137,
            signature_type=sig_type,
            funder=funder,
        )
        creds = client.create_or_derive_api_creds()
        client.set_api_creds(creds)
        res = client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL, signature_type=sig_type))
        return res
    except Exception as e:
        return {"error": str(e)}


def check_relayer_status(tx_id: str = None) -> dict:
    if not tx_id:
        log_file = Path("run/live_orders.json")
        if log_file.exists():
            try:
                for line in reversed(log_file.read_text().splitlines()):
                    if line.strip():
                        entry = json.loads(line)
                        if entry.get("action") == "redeem_positions" and "relayer_response" in entry:
                            resp = entry["relayer_response"]
                            tx_id = resp.get("transactionID") or resp.get("id")
                            if tx_id:
                                break
            except Exception:
                pass
    if not tx_id:
        return {"status": "NO_RELAYER_TX_FOUND"}

    url = f"https://relayer-v2.polymarket.com/transaction/{tx_id}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        return {"error": str(e), "tx_id": tx_id}


def main():
    parser = argparse.ArgumentParser(description="One-pass on-chain balance and CLOB allowance capture.")
    parser.add_argument("--tx", default=None, help="Specific relayer transaction ID to inspect")
    args = parser.parse_args()

    block_hex, rpc_host = rpc_call("eth_blockNumber", [])
    block_num = int(block_hex, 16)

    print("=" * 80)
    print(f"SPREAD-HUNTER SETTLEMENT & BALANCE AUDIT (Polygon Block #{block_num} via {rpc_host})")
    print("=" * 80)

    print("\n--- 1. ON-CHAIN TOKEN BALANCES ---")
    print(f"{'Address Role':<22} | {'Address':<42} | {'Native USDC':<12} | {'USDC.e':<12} | {'POL':<10}")
    print("-" * 105)
    for role, addr in ADDRESSES.items():
        bal_native, _ = get_token_balance(TOKENS["Native USDC"], addr)
        bal_bridged, _ = get_token_balance(TOKENS["Bridged USDC.e"], addr)
        bal_pol, _ = get_pol_balance(addr)
        print(f"{role:<22} | {addr:<42} | ${bal_native:<11.6f} | ${bal_bridged:<11.6f} | {bal_pol:<9.4f}")

    print("\n--- 2. POLYMARKET DATA-API PORTFOLIO VALUE ---")
    for role, addr in ADDRESSES.items():
        data = fetch_data_api(addr)
        val = data[0].get("value") if isinstance(data, list) and data else "N/A"
        print(f"{role:<22} ({addr}): Portfolio Value = ${val}")

    print("\n--- 3. CLOB GET_BALANCE_ALLOWANCE ---")
    for sig_name, sig_type in [("POLY_GNOSIS_SAFE (2)", 2), ("POLY_1271 (3)", 3), ("EOA (0)", 0)]:
        funder_addr = ADDRESSES["Safe / Deposit Proxy"] if sig_type != 0 else ADDRESSES["Signer EOA"]
        res = check_clob_balance_allowance(funder=funder_addr, sig_type=sig_type)
        print(f"SigType {sig_name:<20} | funder={funder_addr[:10]}... | Result: {res}")

    print("\n--- 4. RELAYER TRANSACTION STATUS ---")
    rel_res = check_relayer_status(args.tx)
    print(json.dumps(rel_res, indent=2))
    print("=" * 80)


if __name__ == "__main__":
    main()
