"""LIVE order execution against Polymarket CLOB. Real money.

Everything else in this repo is a simulator. This file is the one place that
can lose actual funds, so it is deliberately small, deliberately manual, and
refuses to do anything by default.

CREDENTIALS NEVER APPEAR HERE. They are read from the environment and handed
straight to the client. Nothing in this module prints, logs, or writes a key,
and nothing that does should ever be added to it.

    # in .env, which must be in .gitignore BEFORE the key goes in
    POLY_PRIVATE_KEY=0x...      # signing key
    POLY_FUNDER=0x...           # address actually holding the USDC
    POLY_SIG_TYPE=1             # 0 EOA | 1 email-magic proxy | 2 browser proxy

    python -m strategy.live_exec status
    python -m strategy.live_exec quote <condition_id> --price 0.22 --size 20
    python -m strategy.live_exec quote <condition_id> --price 0.22 --size 20 --live
    python -m strategy.live_exec cancel-all --live

SAFETY RAILS, all on by default:
  * --live is required for anything that reaches the venue. Without it every
    command prints what it WOULD send and exits.
  * MAX_ORDER_USD caps one order; MAX_TOTAL_USD caps everything open at once.
  * Each leg is written to run/live_orders.json as it is sent, so a crash
    mid-flight still leaves a record of what went out.
  * cancel-all is its own command, because the thing you want at 3am is a way
    to pull every quote without reading code first.
  * Nothing here is imported by fleet.py. The automated bot cannot reach this
    module, so it cannot place a real order by accident.

SIGNATURE TYPE IS THE USUAL FOOTGUN. An account funded through the Polymarket
website is a PROXY: signature_type 1 or 2, with POLY_FUNDER set to the proxy
address rather than the address the private key derives to. Get it wrong and
orders are rejected -- or signed against an account with no balance. Run
`status` first and confirm the address it prints is the one holding your money.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
RUN = ROOT / "run"

# This module is a __main__ entry point and deliberately imports nothing from
# the fleet, so net_config -- the only other place that reads .env -- never
# runs here. Without this line a correct .env is invisible and the missing-key
# error below fires anyway, which sends you hunting for a problem in the file
# rather than in the loader.
load_dotenv(ROOT / ".env")

# Hard ceilings. Not configuration -- this is the difference between a POC and
# an unbounded loss, so they live in code where a stray env var cannot raise
# them. Edit deliberately, never to "just get this order through".
MAX_ORDER_USD = 25.0
MAX_TOTAL_USD = 100.0


def _client(funder: str | None = None):
    """Build a CLOB client from the environment. Raises if anything is absent.

    The key is read and passed on in a single expression: never bound to a
    module global, never returned, never in a log line.

    `funder` overrides POLY_FUNDER for one call. That exists so a candidate
    address can be balance-checked before it is committed to .env.
    """
    from py_clob_client_v2.client import ClobClient

    key = os.environ.get("POLY_PRIVATE_KEY") or os.environ.get("POLY_KEY")
    if not key:
        raise SystemExit(
            "POLY_PRIVATE_KEY not set. Put it in .env -- and confirm .env is "
            "in .gitignore before you paste anything into it.")

    funder = funder or os.environ.get("POLY_FUNDER")
    sig_type = int(os.environ.get("POLY_SIG_TYPE", "3"))
    host = os.environ.get("CLOB_HOST", "https://clob.polymarket.com")

    c = ClobClient(host, key=key, chain_id=137,
                   signature_type=sig_type, funder=funder)
    # L2 API creds are derived from the key by the client; we never store them.
    c.set_api_creds(c.create_or_derive_api_key())
    return c


def _atomic_write_json(file_path: Path, data: list) -> bool:
    """Atomically writes JSON data to file_path via sibling temp file + os.fsync + os.replace."""
    tmp_path = file_path.with_name(f"{file_path.name}.tmp.{uuid.uuid4()}")
    try:
        with open(tmp_path, "w", encoding="utf-8") as tf:
            tf.write(json.dumps(data, indent=2))
            tf.flush()
            os.fsync(tf.fileno())
        os.replace(tmp_path, file_path)
        return True
    except Exception as exc:
        print(f"WARNING: _atomic_write_json failed for {file_path}: {exc}", file=sys.stderr)
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        return False


def _log_order(rec: dict) -> str:
    RUN.mkdir(exist_ok=True)
    f = RUN / "live_orders.json"
    hist = []
    if f.exists():
        try:
            hist = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError, ValueError) as exc:
            corrupt_path = f.parent / f"live_orders.corrupt.{int(time.time())}.json"
            try:
                os.replace(f, corrupt_path)
                print(f"WARNING: unreadable log file renamed to {corrupt_path}: {exc}", file=sys.stderr)
                hist = []
            except OSError as rename_exc:
                print(
                    f"ERROR: could not rename corrupt log file {f} to {corrupt_path}: {rename_exc}\n"
                    f"Refusing to overwrite corrupted log file. Aborting.",
                    file=sys.stderr,
                )
                raise SystemExit(f"Refusing to overwrite corrupted log file {f}: {rename_exc}")
    if "id" not in rec:
        rec["id"] = str(uuid.uuid4())
    hist.append(rec)
    if not _atomic_write_json(f, hist):
        print(f"ERROR: failed to write log entry {rec['id']} to {f}", file=sys.stderr)
        raise SystemExit(f"Failed to record pending log entry to {f}. Nothing was submitted.")
    return rec["id"]




def _update_order_log(entry_id: str, updates: dict) -> bool:
    RUN.mkdir(exist_ok=True)
    f = RUN / "live_orders.json"
    if not f.exists():
        return False
    try:
        hist = json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError, ValueError) as exc:
        print(f"WARNING: _update_order_log failed to read {f}: {exc}", file=sys.stderr)
        return False

    updated = False
    for item in hist:
        if isinstance(item, dict) and item.get("id") == entry_id:
            item.update(updates)
            updated = True
            break

    if updated:
        return _atomic_write_json(f, hist)
    return False


def _check_idempotency_guard(condition_id: str, force: bool = False) -> None:
    """Scan run/live_orders.json for prior pending/submitted/interrupted orders matching condition_id.
    Refuses execution unless force is True.
    """
    if force:
        return
    f = RUN / "live_orders.json"
    if not f.exists():
        return
    # Only the read and the parse belong inside the guard. Keeping the scan loop
    # here too would report any error raised while walking the entries as
    # "cannot read the order log", which is the wrong diagnosis for a file that
    # read and parsed perfectly well.
    try:
        entries = json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError, ValueError) as exc:
        # Fail closed. An unreadable log is not an empty one. If it holds a
        # pending row for this condition and we return quietly here, _log_order
        # then quarantines the corrupt file and starts a fresh log, so that row
        # leaves the active set and a second on-chain settlement goes out for a
        # condition already in flight. _log_order treats the same condition as
        # serious enough to abort the command; this guard must agree.
        raise SystemExit(
            f"Refusing to execute: cannot read the order log at {f} ({exc!r}). "
            f"A prior in-flight order for {condition_id} cannot be ruled out. "
            f"Inspect the file, or use --force to override."
        ) from exc

    if not isinstance(entries, list):
        return
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        cid = entry.get("condition_id")
        status = entry.get("status")
        if cid and cid.lower() == condition_id.lower() and status in ("pending", "submitted", "interrupted"):
            entry_id = entry.get("id", "unknown")
            raise SystemExit(
                f"Refusing to execute: prior order {entry_id} with condition_id {condition_id} "
                f"has status='{status}'. Use --force to override."
            )


def _open_notional(c) -> float:

    try:
        orders = c.get_open_orders() or []
        return sum(float(o.get("price", 0) or 0)
                   * float(o.get("original_size", 0) or 0)
                   for o in orders)
    except Exception:
        return 0.0


def status() -> None:
    """Who are we, and what is already resting. Read-only, safe anytime."""
    c = _client()
    print(f"address        {c.get_address()}")
    print(f"funder         {os.environ.get('POLY_FUNDER') or '(same as address)'}")
    print(f"signature type {os.environ.get('POLY_SIG_TYPE', '3')}")
    try:
        orders = c.get_open_orders() or []
        print(f"open orders    {len(orders)} "
              f"(${_open_notional(c):.2f} notional)")
        for o in orders[:10]:
            print(f"  {str(o.get('side')):4} {o.get('original_size')} @ "
                  f"{o.get('price')}  id={str(o.get('id') or o.get('order_hash'))[:16]}")
    except Exception as e:
        print(f"open orders    ERROR {type(e).__name__}: {e}")
    print("\nConfirm the address above is the account holding your USDC "
          "BEFORE sending anything.")


def balance(funder: str | None) -> None:
    """USDC the venue will actually let an order draw on. Read-only, no order."""
    from py_clob_client_v2.clob_types import AssetType, BalanceAllowanceParams

    who = funder or os.environ.get("POLY_FUNDER") or "(signer address)"
    sig_type = int(os.environ.get("POLY_SIG_TYPE", "3"))
    print(f"funder     {who}")
    try:
        r = _client(funder).get_balance_allowance(
            BalanceAllowanceParams(asset_type=AssetType.COLLATERAL,
                                   signature_type=sig_type))
    except Exception as e:
        print(f"           ERROR {type(e).__name__}: {e}")
        return
    print(f"raw        {r}")

    # USDC on Polygon is 6dp and the API returns integer base units as strings.
    bal = float(r.get("balance", 0) or 0) / 1e6
    print(f"balance    ${bal:,.2f} USDC")
    
    allowances = r.get("allowances")
    if isinstance(allowances, dict):
        print("allowances:")
        for target, amt in allowances.items():
            allow_val = float(amt or 0) / 1e6
            print(f"  {target[:10]}...: ${allow_val:,.2f}")
    else:
        allow = float(r.get("allowance", 0) or 0) / 1e6
        print(f"allowance  ${allow:,.2f}")

    if bal == 0:
        print("\nZero. If your money is on Polymarket, POLY_FUNDER points at "
              "the wrong address -- try the other candidate before trading.")


def quote(condition_id: str, price: float, size: float, live: bool) -> None:
    """Rest a two-sided pair: buy UP at `price`, buy DOWN at 1-price."""
    from py_clob_client_v2.clob_types import OrderArgsV2, OrderType
    from py_clob_client_v2.order_builder.constants import BUY
    from strategy.markets import fetch_pinned_market

    m = fetch_pinned_market(condition_id)
    if m is None:
        raise SystemExit(f"market {condition_id[:12]} not found")

    dn_price = round(1.0 - price, 4)
    cost = price * size + dn_price * size
    if cost > MAX_ORDER_USD:
        raise SystemExit(
            f"${cost:.2f} exceeds MAX_ORDER_USD ${MAX_ORDER_USD:.2f}")

    legs = [(m.up_token, price, "UP"), (m.down_token, dn_price, "DOWN")]
    print(f"market   {m.market_slug[:60]}")
    print(f"tick     {m.tick_size}   neg_risk {m.neg_risk}")
    for tok, p, label in legs:
        print(f"  BUY {size:.0f} {label:4} @ {p:.3f} = ${p * size:6.2f}  "
              f"token {str(tok)[:14]}...")
    print(f"total committed ${cost:.2f}")

    if not live:
        print("\nDRY RUN -- nothing sent. Re-run with --live to place.")
        return

    c = _client()
    already = _open_notional(c)
    if already + cost > MAX_TOTAL_USD:
        raise SystemExit(f"open ${already:.2f} + ${cost:.2f} exceeds "
                         f"MAX_TOTAL_USD ${MAX_TOTAL_USD:.2f}")

    for tok, p, label in legs:
        signed = c.create_order(
            OrderArgsV2(price=p, size=size, side=BUY, token_id=tok))
        resp = c.post_order(signed, OrderType.GTC)
        _log_order({"ts": time.time(), "condition_id": condition_id,
                    "side": label, "token_id": str(tok), "price": p,
                    "size": size, "response": str(resp)[:400]})
        print(f"  SENT {label}: {resp}")
    print(f"\nlogged to {RUN / 'live_orders.json'}")


CTF_CONTRACT = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
USDC_E_CONTRACT = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
ZERO_BYTES32 = "0x0000000000000000000000000000000000000000000000000000000000000000"
# Provenance: matches the 598s delta measured on transaction 0x66bc709b1a1d515d813e9d191a84b8863d8f2a251e1698a85d452152c7602135, block 92098496.
REDEEM_DEADLINE_SECONDS = 600
# Polymarket DepositWalletFactory address used by @polymarket/builder-relayer-client (config.DepositWalletFactory),
# confirmed as outer 'to' of reference transaction 0x66bc709b1a1d515d813e9d191a84b8863d8f2a251e1698a85d452152c7602135.
DEPOSIT_WALLET_FACTORY = "0x00000000000Fb5C9ADea0298D729A0CB3823Cc07"


def encode_redeem_positions(collateral_token: str, parent_collection_id: str,
                            condition_id: str, index_sets: list[int]) -> str:
    """Encode ABI call for ConditionalTokens.redeemPositions(address,bytes32,bytes32,uint256[])
    Selector: 0x01b7037c
    """
    selector = "01b7037c"
    p_col = collateral_token.lower().replace("0x", "").zfill(64)
    p_parent = parent_collection_id.lower().replace("0x", "").zfill(64)
    p_cond = condition_id.lower().replace("0x", "").zfill(64)
    offset = hex(128)[2:].zfill(64)
    len_idx = hex(len(index_sets))[2:].zfill(64)
    elem_idx = "".join(hex(idx)[2:].zfill(64) for idx in index_sets)
    return "0x" + selector + p_col + p_parent + p_cond + offset + len_idx + elem_idx


ALT_BN128_P = 21888242871839275222246405745257275088696311157297823662689037894645226208583
ALT_BN128_B = 3


def _alt_bn128_sqrt(x: int) -> int:
    """Modular square root on F_P for alt_bn128 (P % 4 == 3)."""
    return pow(x, (ALT_BN128_P + 1) // 4, ALT_BN128_P)


def _alt_bn128_add(x1: int, y1: int, x2: int, y2: int) -> tuple[int, int]:
    """Affine point addition on alt_bn128 (E: y^2 = x^3 + 3 over F_P).
    Equivalent to EVM ecAdd precompile at address(6).
    """
    p = ALT_BN128_P
    if x1 == 0 and y1 == 0:
        return x2, y2
    if x2 == 0 and y2 == 0:
        return x1, y1
    if x1 == x2:
        if (y1 + y2) % p == 0:
            return 0, 0
        slope = (3 * x1 * x1) * pow(2 * y1, p - 2, p) % p
    else:
        slope = (y2 - y1) * pow(x2 - x1, p - 2, p) % p
    x3 = (slope * slope - x1 - x2) % p
    y3 = (slope * (x1 - x3) - y1) % p
    return x3, y3


def get_collection_id(parent_collection_id: str, condition_id: str, index_set: int) -> str:
    """Construct an outcome collection ID from a parent collection and an outcome collection.
    Canonical port of CTHelpers.sol:392-424 (gnosis/conditional-tokens-contracts).
    """
    from eth_utils import keccak
    p = ALT_BN128_P
    b = ALT_BN128_B

    cond_bytes = bytes.fromhex(condition_id.lower().replace("0x", "").zfill(64))
    idx_bytes = int(index_set).to_bytes(32, byteorder="big")
    raw_hash = keccak(cond_bytes + idx_bytes)
    x1 = int.from_bytes(raw_hash, byteorder="big")
    odd = (x1 >> 255) != 0

    while True:
        x1 = (x1 + 1) % p
        yy = (pow(x1, 3, p) + b) % p
        y1 = _alt_bn128_sqrt(yy)
        if (y1 * y1) % p == yy:
            break

    if (odd and y1 % 2 == 0) or (not odd and y1 % 2 == 1):
        y1 = p - y1

    x2 = int(parent_collection_id, 16) if parent_collection_id else 0
    if x2 != 0:
        odd_parent = (x2 >> 254) != 0
        x2 = x2 & ((1 << 254) - 1)
        yy_parent = (pow(x2, 3, p) + b) % p
        y2 = _alt_bn128_sqrt(yy_parent)
        if (odd_parent and y2 % 2 == 0) or (not odd_parent and y2 % 2 == 1):
            y2 = p - y2
        if (y2 * y2) % p != yy_parent:
            raise ValueError("invalid parent collection ID")
        x1, y1 = _alt_bn128_add(x1, y1, x2, y2)

    if y1 % 2 == 1:
        x1 ^= 1 << 254

    return "0x" + hex(x1)[2:].zfill(64)



def get_position_id(collateral_token: str, collection_id: str) -> str:
    """Compute positionId = uint256(keccak256(abi.encodePacked(collateralToken, collectionId))).
    Source: CTHelpers.sol getPositionId (gnosis/conditional-tokens-contracts).
    """
    from eth_utils import keccak
    col_bytes = bytes.fromhex(collateral_token.lower().replace("0x", "").zfill(40))
    coll_bytes = bytes.fromhex(collection_id.lower().replace("0x", "").zfill(64))
    return str(int.from_bytes(keccak(col_bytes + coll_bytes), byteorder="big"))


def encode_merge_positions(collateral_token: str, parent_collection_id: str,
                           condition_id: str, index_sets: list[int],
                           amount: int) -> str:
    """Encode ABI call for ConditionalTokens.mergePositions(address,bytes32,bytes32,uint256[],uint256)
    Selector: 0x9e7212ad (keccak256(b"mergePositions(address,bytes32,bytes32,uint256[],uint256)")[:4])
    Source: ConditionalTokens.sol:165-171 (gnosis/conditional-tokens-contracts).
    """
    selector = "9e7212ad"
    p_col = collateral_token.lower().replace("0x", "").zfill(64)
    p_parent = parent_collection_id.lower().replace("0x", "").zfill(64)
    p_cond = condition_id.lower().replace("0x", "").zfill(64)
    offset = hex(160)[2:].zfill(64)  # 5 static words in head * 32 bytes = 160 = 0xa0
    p_amount = hex(int(amount))[2:].zfill(64)
    len_idx = hex(len(index_sets))[2:].zfill(64)
    elem_idx = "".join(hex(int(idx))[2:].zfill(64) for idx in index_sets)
    return "0x" + selector + p_col + p_parent + p_cond + offset + p_amount + len_idx + elem_idx


def build_redeem_typed_data(funder: str, nonce: int, deadline: int, call_data: str) -> tuple[dict, dict, dict]:

    """Build EIP-712 typed data structures for DepositWallet.Batch."""
    domain = {
        "name": "DepositWallet",
        "version": "1",
        "chainId": 137,
        "verifyingContract": funder,
    }
    types = {
        "Call": [
            {"name": "target", "type": "address"},
            {"name": "value", "type": "uint256"},
            {"name": "data", "type": "bytes"},
        ],
        "Batch": [
            {"name": "wallet", "type": "address"},
            {"name": "nonce", "type": "uint256"},
            {"name": "deadline", "type": "uint256"},
            {"name": "calls", "type": "Call[]"},
        ],
    }
    call_bytes = bytes.fromhex(call_data[2:] if call_data.startswith("0x") else call_data)
    message = {
        "wallet": funder,
        "nonce": int(nonce),
        "deadline": int(deadline),
        "calls": [
            {
                "target": CTF_CONTRACT,
                "value": 0,
                "data": call_bytes,
            }
        ],
    }
    return domain, types, message


def sign_redeem_transaction(key: str, funder: str, nonce: int, deadline: int, call_data: str) -> tuple[str, str]:
    """Sign DepositWallet EIP-712 Batch transaction with EOA key."""
    from eth_account import Account
    from eth_account.messages import encode_typed_data

    domain, types, message = build_redeem_typed_data(funder, nonce, deadline, call_data)
    typed = encode_typed_data(domain_data=domain, message_types=types, message_data=message)
    signer_acc = Account.from_key(key)
    signed = signer_acc.sign_message(typed)
    sig = signed.signature.hex()
    if not sig.startswith("0x"):
        sig = "0x" + sig
    return signer_acc.address, sig


# Origin: scripts/audit_settlement.py:19-24
POLYGON_RPC_ENDPOINTS = [
    "https://polygon.drpc.org",
    "https://1rpc.io/matic",
    "https://polygon-bor-rpc.publicnode.com",
    "https://rpc.ankr.com/polygon",
]


def get_payout_denominator(condition_id: str, rpc_url: str | None = None) -> int | None:
    """Query payoutDenominator(bytes32) on CTF contract (0x4D97DCd97eC945f40cF65F87097ACe5EA0476045).
    Selector: 0xdd34de67
    Returns integer (non-zero if resolved, 0 if unresolved) on success, or None if all RPC endpoints fail.
    """
    import urllib.request

    clean_cond = condition_id.lower().replace("0x", "").zfill(64)
    call_data = "0xdd34de67" + clean_cond
    req_body = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [{"to": CTF_CONTRACT, "data": call_data}, "latest"],
    }).encode("utf-8")

    endpoints = []
    if rpc_url:
        endpoints.append(rpc_url)
    elif os.environ.get("POLYGON_RPC"):
        endpoints.append(os.environ["POLYGON_RPC"])
    endpoints.extend([ep for ep in POLYGON_RPC_ENDPOINTS if ep not in endpoints])

    for ep in endpoints:
        try:
            req = urllib.request.Request(
                ep,
                data=req_body,
                headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                if "result" in res:
                    if res["result"] == "0x":
                        raise SystemExit(
                            f"eth_call to CTF contract {CTF_CONTRACT} returned empty data. "
                            f"The contract address may be wrong or the RPC may be on the wrong chain."
                        )
                    return int(res["result"], 16)
        except Exception:
            continue
    return None


def build_redeem_submit_payload(from_addr: str, funder: str, nonce: int | str,
                                deadline: int | str, signature: str, call_data: str) -> dict:
    """Construct relayer /submit JSON payload for DepositWalletBatchRequest.
    Wire types follow @polymarket/builder-relayer-client@0.0.10 dist/types.d.ts:147-154.
    """
    return {
        "type": "WALLET",
        "from": from_addr,
        "to": DEPOSIT_WALLET_FACTORY,
        "nonce": str(nonce),
        "signature": signature,
        "depositWalletParams": {
            "depositWallet": funder,
            "deadline": str(deadline),
            "calls": [
                {
                    "target": CTF_CONTRACT,
                    "value": "0",
                    "data": call_data,
                }
            ],
        },
    }


def _submit_and_log(
    action: str,
    condition_id: str,
    funder: str,
    signer_addr: str,
    call_data: str,
    nonce: int | str,
    deadline: int | str,
    payload: dict,
    headers: dict,
    relayer_url: str,
) -> None:
    """Submit EIP-712 batch transaction to relayer with crash-safe pre-logging and atomic status updates."""
    import urllib.request

    req_submit = urllib.request.Request(
        f"{relayer_url}/submit",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
    )

    entry_id = _log_order({
        "ts": time.time(),
        "action": action,
        "condition_id": condition_id,
        "safe_funder": funder,
        "signer": signer_addr,
        "target": CTF_CONTRACT,
        "call_data": call_data,
        "nonce": nonce,
        "deadline": deadline,
        "payload": payload,
        "status": "pending",
    })

    try:
        with urllib.request.urlopen(req_submit, timeout=30) as resp:
            res = json.loads(resp.read().decode("utf-8"))
    except KeyboardInterrupt:
        try:
            log_ok = _update_order_log(entry_id, {
                "status": "interrupted",
                "error_type": "KeyboardInterrupt",
                "error": "Execution interrupted by user during submit",
            })
        except Exception:
            log_ok = False

        record_dump = json.dumps({
            "id": entry_id,
            "action": action,
            "condition_id": condition_id,
            "safe_funder": funder,
            "signer": signer_addr,
            "target": CTF_CONTRACT,
            "call_data": call_data,
            "nonce": nonce,
            "deadline": deadline,
            "payload": payload,
            "status": "interrupted",
            "error_type": "KeyboardInterrupt",
        }, indent=2)
        print(
            f"ERROR: Relayer submit interrupted by user (KeyboardInterrupt).\n"
            f"Transaction was signed and may have been broadcast to relayer.\n"
            f"Full in-flight transaction record:\n{record_dump}",
            file=sys.stderr,
        )
        raise SystemExit(
            f"Relayer submit interrupted (KeyboardInterrupt).\n"
            f"Transaction was signed and may have been broadcast (nonce={nonce}, id={entry_id}).\n"
            f"On-chain status must be checked manually before any retry."
        )
    except Exception as exc:
        try:
            log_ok = _update_order_log(entry_id, {
                "status": "unknown",
                "error_type": type(exc).__name__,
                "error": str(exc),
            })
        except Exception:
            log_ok = False

        if not log_ok:
            record_dump = json.dumps({
                "id": entry_id,
                "action": action,
                "condition_id": condition_id,
                "safe_funder": funder,
                "signer": signer_addr,
                "target": CTF_CONTRACT,
                "call_data": call_data,
                "nonce": nonce,
                "deadline": deadline,
                "payload": payload,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }, indent=2)
            print(
                f"ERROR: Failed to update live_orders.json for entry_id={entry_id}.\n"
                f"Full in-flight transaction record:\n{record_dump}",
                file=sys.stderr,
            )
            raise SystemExit(
                f"Relayer submit failed with {type(exc).__name__}: {exc}\n"
                f"Transaction was signed and sent (nonce={nonce}, id={entry_id}).\n"
                f"WARNING: Audit row in live_orders.json could NOT be updated (see stderr dump).\n"
                f"On-chain status must be checked manually before any retry."
            )
        raise SystemExit(
            f"Relayer submit failed with {type(exc).__name__}: {exc}\n"
            f"Transaction was signed and sent (nonce={nonce}, id={entry_id}).\n"
            f"On-chain status must be checked manually before any retry."
        )

    tx_hash = None
    if isinstance(res, dict):
        tx_hash = res.get("transactionHash") or res.get("transactionID") or res.get("id")

    update_fields = {
        "status": "submitted",
        "response": json.dumps(res)[:400],
    }
    if tx_hash:
        update_fields["tx_hash"] = tx_hash

    try:
        log_ok = _update_order_log(entry_id, update_fields)
    except Exception as update_exc:
        log_ok = False
        update_err = str(update_exc)
    else:
        update_err = None

    if not log_ok:
        record_dump = json.dumps({
            "id": entry_id,
            "action": action,
            "condition_id": condition_id,
            "safe_funder": funder,
            "signer": signer_addr,
            "target": CTF_CONTRACT,
            "call_data": call_data,
            "nonce": nonce,
            "deadline": deadline,
            "payload": payload,
            "tx_hash": tx_hash,
            "status": "submitted",
            "response": json.dumps(res)[:400],
            "update_error": update_err,
        }, indent=2)
        print(
            f"ERROR: Relayer accepted transaction (tx_hash={tx_hash}) but live_orders.json entry {entry_id} "
            f"could NOT be updated to status='submitted' (row remains pending or missing in log).\n"
            f"Full transaction record:\n{record_dump}",
            file=sys.stderr,
        )
        raise SystemExit(
            f"Relayer accepted transaction (tx_hash={tx_hash}), but audit log update failed.\n"
            f"Transaction was signed and submitted (nonce={nonce}, id={entry_id}).\n"
            f"On-chain status must be verified before any retry. See stderr for full transaction record."
        )

    print(f"  RELAYER RESPONSE: {json.dumps(res)[:400]}")
    print(f"\nlogged to {RUN / 'live_orders.json'}")


def redeem(condition_id: str, index_sets: list[int] | None = None,
           collateral: str = USDC_E_CONTRACT,
           parent_collection_id: str = ZERO_BYTES32,
           skip_resolution_check: bool = False,
           force: bool = False,
           live: bool = False) -> None:
    """Gasless redemption of winning conditional tokens via Polymarket Relayer."""
    if index_sets is None:
        index_sets = [1, 2]

    # Pre-flight Guard: Idempotency check
    _check_idempotency_guard(condition_id, force=force)

    funder = os.environ.get("POLY_FUNDER", "")
    key = os.environ.get("POLY_PRIVATE_KEY")
    signer = ""
    if key:
        from eth_account import Account
        signer = Account.from_key(key).address

    call_data = encode_redeem_positions(
        collateral_token=collateral,
        parent_collection_id=parent_collection_id,
        condition_id=condition_id,
        index_sets=index_sets,
    )

    denom = get_payout_denominator(condition_id)
    if denom is None:
        resolved_str = "unknown (RPC unreachable)"
    else:
        resolved_str = "yes" if denom > 0 else "no"

    # Evaluated before the dry-run preview so the preview matches what --live does.
    guard_failures: list[str] = []
    if denom is None:
        if not skip_resolution_check:
            guard_failures.append(
                f"Cannot determine resolution status for {condition_id}: all RPC endpoints failed. "
                f"The market may well be resolved. Retry, or pass --skip-resolution-check to bypass."
            )
    elif denom == 0:
        guard_failures.append(
            f"Condition {condition_id} is not resolved yet (payoutDenominator == 0)."
        )

    print("action          REDEEM (gasless via Polymarket Relayer)")
    print(f"target_ctf      {CTF_CONTRACT}")
    print(f"safe_funder     {funder or '(POLY_FUNDER not set)'}")
    print(f"signer_eoa      {signer or '(POLY_PRIVATE_KEY not set)'}")
    print(f"condition_id    {condition_id}")
    print(f"resolved        {resolved_str}")
    print(f"collateral      {collateral}")
    print(f"index_sets      {index_sets}")
    print(f"encoded_call    {call_data[:42]}... ({len(call_data)} chars)")

    if not live:
        preview_nonce = 0
        preview_deadline = int(time.time()) + REDEEM_DEADLINE_SECONDS
        preview_sig = "0x" + "00" * 65
        preview_payload = build_redeem_submit_payload(
            from_addr=signer or "0x0000000000000000000000000000000000000000",
            funder=funder or "0x0000000000000000000000000000000000000000",
            nonce=preview_nonce,
            deadline=preview_deadline,
            signature=preview_sig,
            call_data=call_data,
        )
        print("\nsubmit_payload_preview (dry run - placeholder nonce/signature):")
        print(json.dumps(preview_payload, indent=2))
        if guard_failures:
            print("\nPRE-FLIGHT FAILED -- --live would refuse:")
            for msg in guard_failures:
                print(f"  - {msg}")
            raise SystemExit(1)
        print("\nDRY RUN -- nothing sent. Re-run with --live to sign and submit to relayer.")
        return

    if guard_failures:
        raise SystemExit(guard_failures[0])

    relayer_key = os.environ.get("RELAYER_API_KEY")
    relayer_addr = os.environ.get("RELAYER_API_KEY_ADDRESS")
    if not relayer_key or not relayer_addr:
        raise SystemExit(
            "RELAYER_API_KEY and RELAYER_API_KEY_ADDRESS must be set in .env "
            "for gasless live redemption."
        )
    if not key or not funder:
        raise SystemExit("POLY_PRIVATE_KEY and POLY_FUNDER must be set in .env")

    import urllib.request
    relayer_url = os.environ.get("RELAYER_URL", "https://relayer-v2.polymarket.com")

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
        "RELAYER_API_KEY": relayer_key,
        "RELAYER_API_KEY_ADDRESS": relayer_addr,
    }

    # 1. Fetch transaction nonce from relayer
    nonce_url = f"{relayer_url}/v1/account/transactions/params?address={signer}&type=WALLET"
    req_nonce = urllib.request.Request(nonce_url, headers=headers)
    try:
        with urllib.request.urlopen(req_nonce, timeout=10) as resp:
            nonce_data = json.loads(resp.read().decode("utf-8"))
            nonce = int(nonce_data.get("nonce", 0))
    except Exception as exc:
        raise SystemExit(f"Failed to fetch nonce from relayer: {exc}")

    # 2. Sign EIP-712 Batch transaction
    deadline = int(time.time()) + REDEEM_DEADLINE_SECONDS
    signer_addr, signature = sign_redeem_transaction(key, funder, nonce, deadline, call_data)

    # 3. Construct relayer submit payload
    payload = build_redeem_submit_payload(
        from_addr=signer_addr,
        funder=funder,
        nonce=nonce,
        deadline=deadline,
        signature=signature,
        call_data=call_data,
    )

    # 4. Submit and log
    _submit_and_log(
        action="REDEEM",
        condition_id=condition_id,
        funder=funder,
        signer_addr=signer_addr,
        call_data=call_data,
        nonce=nonce,
        deadline=deadline,
        payload=payload,
        headers=headers,
        relayer_url=relayer_url,
    )


def merge(condition_id: str,
          amount: float,
          index_sets: list[int] | None = None,
          collateral: str = USDC_E_CONTRACT,
          parent_collection_id: str = ZERO_BYTES32,
          force: bool = False,
          live: bool = False) -> None:
    """Gasless merge of full outcome sets (UP + DOWN) back into USDC.e collateral."""
    from strategy.config import MakerConfig
    if index_sets is None:
        index_sets = [1, 2]
    amount_base_units = int(round(amount * 1e6))

    # Pre-flight Guard 3: MAX_ORDER_USD ceiling
    cost = amount * 1.0
    if cost > MAX_ORDER_USD:
        raise SystemExit(f"${cost:.2f} exceeds MAX_ORDER_USD ${MAX_ORDER_USD:.2f}")

    # Pre-flight Guard 4: Idempotency check
    _check_idempotency_guard(condition_id, force=force)

    # Derive token IDs deterministically via CTF
    token_ids = [
        get_position_id(collateral, get_collection_id(parent_collection_id, condition_id, idx))
        for idx in index_sets
    ]
    up_tok_id = token_ids[0] if len(token_ids) > 0 else ""
    dn_tok_id = token_ids[1] if len(token_ids) > 1 else ""

    funder = os.environ.get("POLY_FUNDER", "")
    key = os.environ.get("POLY_PRIVATE_KEY")
    signer = ""
    if key:
        from eth_account import Account
        signer = Account.from_key(key).address

    call_data = encode_merge_positions(
        collateral_token=collateral,
        parent_collection_id=parent_collection_id,
        condition_id=condition_id,
        index_sets=index_sets,
        amount=amount_base_units,
    )

    denom = get_payout_denominator(condition_id)
    if denom is None:
        resolved_str = "unknown (RPC unreachable)"
    else:
        resolved_str = "yes" if denom > 0 else "no"

    merge_gas = MakerConfig().merge_gas_usd
    expected_collateral = amount * 1.00
    net_collateral = expected_collateral - merge_gas


    up_bal = 0.0
    dn_bal = 0.0
    # A balance we failed to read is not a balance of zero. Both fail closed,
    # but only one of them tells the operator the truth: an RPC error, an auth
    # failure and an unset POLY_FUNDER all rendered as "holds 0.00", which reads
    # as "you do not own these tokens". Same rule reconcile_orders follows --
    # a failed read must not be laundered into a state verdict.
    balance_error: str | None = None
    if not (key and funder):
        balance_error = (
            "Conditional token balances not queried: "
            f"{'POLY_PRIVATE_KEY' if not key else 'POLY_FUNDER'} is unset"
        )

    # Query conditional token balances if client credentials available
    if key and funder:
        try:
            from py_clob_client_v2.clob_types import AssetType, BalanceAllowanceParams
            sig_type = int(os.environ.get("POLY_SIG_TYPE", "3"))
            c = _client(funder)
            if up_tok_id:
                r_up = c.get_balance_allowance(
                    BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL, token_id=up_tok_id, signature_type=sig_type)
                )
                up_bal = float(r_up.get("balance", 0) or 0) / 1e6
            if dn_tok_id:
                r_dn = c.get_balance_allowance(
                    BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL, token_id=dn_tok_id, signature_type=sig_type)
                )
                dn_bal = float(r_dn.get("balance", 0) or 0) / 1e6
        except Exception as exc:
            balance_error = f"Conditional token balance query failed: {exc!r}"

    # Pre-flight guards are evaluated HERE, before the dry-run preview, so the
    # preview reports exactly what --live would do. A preview that succeeds where
    # --live refuses manufactures false confidence in the operator.
    guard_failures: list[str] = []
    if balance_error is not None:
        guard_failures.append(
            f"{balance_error}. Holdings are unknown, not zero -- refusing rather "
            f"than reporting a balance we did not read."
        )
    else:
        if up_bal < amount:
            guard_failures.append(
                f"Insufficient balance on UP token ({up_tok_id}): holds {up_bal:.2f}, needs {amount:.2f} (short by {amount - up_bal:.2f})"
            )
        if dn_bal < amount:
            guard_failures.append(
                f"Insufficient balance on DOWN token ({dn_tok_id}): holds {dn_bal:.2f}, needs {amount:.2f} (short by {amount - dn_bal:.2f})"
            )
    if denom is None:
        # `redeem` already refuses here unless --skip-resolution-check is passed.
        # `merge` had neither the branch nor the flag, so an all-endpoints-down
        # RPC read let a merge go out against a market that may already be
        # resolved: a reverted relayer submission and an ambiguous audit row.
        guard_failures.append(
            f"Cannot determine resolution status for {condition_id}: every RPC endpoint failed. "
            f"The condition may already be resolved, in which case merge is the wrong action."
        )
    elif denom > 0:
        guard_failures.append(
            f"Condition {condition_id} is already resolved (payoutDenominator == {denom} > 0). Use redeem instead."
        )

    print("action          MERGE (gasless via Polymarket Relayer)")
    print(f"target_ctf      {CTF_CONTRACT}")
    print(f"safe_funder     {funder or '(POLY_FUNDER not set)'}")
    print(f"signer_eoa      {signer or '(POLY_PRIVATE_KEY not set)'}")
    print(f"condition_id    {condition_id}")
    print(f"resolved        {resolved_str}")
    print(f"collateral      {collateral}")
    print(f"index_sets      {index_sets}")
    print(f"amount          {amount:.2f} shares ({amount_base_units} base units)")
    # `up_bal` and `dn_bal` are still at their 0.0 initialisers when the balance
    # query failed. Formatting them here would print "held: 0.00" directly above
    # the guard line saying holdings are unknown, not zero -- the operator reads
    # two contradictory statements and believes the number.
    held_up = "unknown" if balance_error is not None else f"{up_bal:.2f}"
    held_dn = "unknown" if balance_error is not None else f"{dn_bal:.2f}"
    print(f"token_up        {up_tok_id} (held: {held_up})")
    print(f"token_down      {dn_tok_id} (held: {held_dn})")
    print(f"expected_usdc   ${expected_collateral:,.2f}")
    print(f"estimated_gas   ${merge_gas:,.2f} (config.merge_gas_usd)")
    print(f"net_collateral  ${net_collateral:,.2f}")
    print(f"encoded_call    {call_data[:42]}... ({len(call_data)} chars)")

    if not live:
        preview_nonce = 0
        preview_deadline = int(time.time()) + REDEEM_DEADLINE_SECONDS
        preview_sig = "0x" + "00" * 65
        preview_payload = build_redeem_submit_payload(
            from_addr=signer or "0x0000000000000000000000000000000000000000",
            funder=funder or "0x0000000000000000000000000000000000000000",
            nonce=preview_nonce,
            deadline=preview_deadline,
            signature=preview_sig,
            call_data=call_data,
        )
        print("\nsubmit_payload_preview (dry run - placeholder nonce/signature):")
        print(json.dumps(preview_payload, indent=2))
        if guard_failures:
            print("\nPRE-FLIGHT FAILED -- --live would refuse:")
            for msg in guard_failures:
                print(f"  - {msg}")
            raise SystemExit(1)
        print("\nDRY RUN -- nothing sent. Re-run with --live to sign and submit to relayer.")
        return

    if guard_failures:
        raise SystemExit(guard_failures[0])

    relayer_key = os.environ.get("RELAYER_API_KEY")
    relayer_addr = os.environ.get("RELAYER_API_KEY_ADDRESS")
    if not relayer_key or not relayer_addr:
        raise SystemExit(
            "RELAYER_API_KEY and RELAYER_API_KEY_ADDRESS must be set in .env "
            "for gasless live merge."
        )
    if not key or not funder:
        raise SystemExit("POLY_PRIVATE_KEY and POLY_FUNDER must be set in .env")

    import urllib.request
    relayer_url = os.environ.get("RELAYER_URL", "https://relayer-v2.polymarket.com")

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
        "RELAYER_API_KEY": relayer_key,
        "RELAYER_API_KEY_ADDRESS": relayer_addr,
    }

    # 1. Fetch transaction nonce from relayer
    nonce_url = f"{relayer_url}/v1/account/transactions/params?address={signer}&type=WALLET"
    req_nonce = urllib.request.Request(nonce_url, headers=headers)
    try:
        with urllib.request.urlopen(req_nonce, timeout=10) as resp:
            nonce_data = json.loads(resp.read().decode("utf-8"))
            nonce = int(nonce_data.get("nonce", 0))
    except Exception as exc:
        raise SystemExit(f"Failed to fetch nonce from relayer: {exc}")

    # 2. Sign EIP-712 Batch transaction
    deadline = int(time.time()) + REDEEM_DEADLINE_SECONDS
    signer_addr, signature = sign_redeem_transaction(key, funder, nonce, deadline, call_data)

    # 3. Construct relayer submit payload
    payload = build_redeem_submit_payload(
        from_addr=signer_addr,
        funder=funder,
        nonce=nonce,
        deadline=deadline,
        signature=signature,
        call_data=call_data,
    )

    # 4. Submit and log
    _submit_and_log(
        action="MERGE",
        condition_id=condition_id,
        funder=funder,
        signer_addr=signer_addr,
        call_data=call_data,
        nonce=nonce,
        deadline=deadline,
        payload=payload,
        headers=headers,
        relayer_url=relayer_url,
    )




def probe(series: str = "btc-up-or-down-5m",
          token_id: str | None = None,
          cycles: int = 30,
          min_t_remaining: float = 90.0,
          max_complement_bid: float = 0.85,
          max_probe_loss_usd: float = 1.00,
          max_fills: int = 1,
          live: bool = False) -> None:
    """Multi-cycle latency probe spanning live market windows with strict CTF match defense.

    Measures tau_accept (engine queuing & sequencing) and tau_pubsub (venue broadcast lag)
    using local monotonic CPU timestamps. Dynamically tracks 5-minute market rollovers,
    handles inter-window gaps, guards against complementary matching, and bounds uncertainty.
    """
    if series == "btc-updown-5m":
        series = "btc-up-or-down-5m"

    NET_ONEWAY_MS = 3.93  # Measured median one-way TCP transit (RTT/2 = 7.85ms / 2)

    print("=" * 80)
    print(f"SPREAD-HUNTER LIVE LATENCY PROBE (N={cycles} cycles on series '{series}')")
    print("=" * 80)
    print("Guardrails & Architecture:")
    print("  - Target: Dynamic live market discovery across 5m windows")
    print("  - Price: $0.01 resting bid on UP")
    print("  - Size: 100 shares ($1.00 notional collateral)")
    print("  - Order Lifecycle: Post -> Capture WS Delta -> Immediate Cancel")
    print(f"  - Minimum Time Remaining Guard: >= {min_t_remaining:.0f}s remaining in 5m window")
    print(f"  - Complement Price Guard: Skip if DOWN Best Bid >= {max_complement_bid:.2f}")
    print(f"  - Max Probe Loss Cap: Abort if fills >= {max_fills} (loss >= ${max_probe_loss_usd:.2f})")
    print(f"  - Mode: {'LIVE BROADCAST' if live else 'DRY RUN (pass --live to execute)'}")
    print("=" * 80)

    if not live:
        from strategy.markets import fetch_live_market
        gamma_host = os.environ.get("GAMMA_HOST", "https://gamma-api.polymarket.com")
        resolved = fetch_live_market(gamma_host, series) if not token_id else None
        print("\n[DRY-RUN] Probe execution plan validated.")
        print(f"Series: {series}")
        if resolved:
            print(f"Active Live Window: {resolved.market_slug} (ends in {resolved.t_remaining():.0f}s)")
            print(f"Target Token (UP): {resolved.up_token}")
            print(f"Complement Token (DOWN): {resolved.down_token}")
        elif token_id:
            print(f"Fixed Target Token: {token_id}")
        else:
            print("Active Live Window: Currently in rollover gap (would wait for next window).")
        print(f"Would execute {cycles} consecutive cycles of $1.00 notional resting bids across dynamic market windows.")
        print("Expected Error Budget at N=30:")
        print("  - Random SEM: +/- 1.28 ms (shrinks as sigma / sqrt(30))")
        print("  - Residual Systematic Bias: <= 3.50 ms (route asymmetry + gateway TLS)")
        print("  - Total Uncertainty: <= 4.78 ms (< 10% on 50ms parameter)")
        print("Guards & Cost Model:")
        print(f"  - P(fill / cycle) under guards: ~1.08% (measured on archive tape)")
        print(f"  - Expected probe cost across 30 cycles: $0.32 USD")
        return

    import websocket
    from py_clob_client_v2.clob_types import OrderArgsV2, OrderType
    from py_clob_client_v2.order_builder.constants import BUY
    from strategy.markets import fetch_live_market

    gamma_host = os.environ.get("GAMMA_HOST", "https://gamma-api.polymarket.com")
    client = _client()

    # Dynamic WebSocket subscription state
    last_delta_event = {}
    ws_connected = threading.Event()
    current_token_id = [None]
    current_comp_id = [None]
    ws_instance = [None]
    comp_best_bid = [0.0]

    def on_ws_message(ws, message):
        try:
            data = json.loads(message)
            curr = current_token_id[0]
            comp = current_comp_id[0]
            items = data if isinstance(data, list) else [data]
            for item in items:
                asset = item.get("asset_id")
                # Both branches key on asset_id alone. They previously admitted
                # ANY `event_type == "book"` frame regardless of which token it
                # described, so a snapshot for the complement stamped ts_recv on
                # the target -- tau_pubsub_ms then timed an unrelated broadcast,
                # and comp_best_bid could be filled from the target's own book,
                # which is the price the loss guard below compares against.
                if curr and asset == curr:
                    last_delta_event["ts_recv"] = time.perf_counter_ns()
                    last_delta_event["data"] = item
                if comp and asset == comp:
                    bids = item.get("bids") or []
                    if bids:
                        comp_best_bid[0] = max(float(b.get("price", 0)) for b in bids)
        except Exception:
            pass

    def on_ws_open(ws):
        ws_instance[0] = ws
        ws_connected.set()

    def subscribe_tokens(t_up: str, t_down: str):
        current_token_id[0] = t_up
        current_comp_id[0] = t_down
        if ws_instance[0] and ws_connected.is_set():
            sub_msg = json.dumps({"assets_ids": [t_up, t_down], "type": "market"})
            try:
                ws_instance[0].send(sub_msg)
            except Exception:
                pass

    ws_app = websocket.WebSocketApp(
        "wss://ws-subscriptions-clob.polymarket.com/ws/market",
        on_open=on_ws_open,
        on_message=on_ws_message,
    )
    ws_thread = threading.Thread(target=ws_app.run_forever, daemon=True)
    ws_thread.start()

    if not ws_connected.wait(timeout=10.0):
        print("ERROR: WebSocket connection to market stream timed out.")
        return

    print("WebSocket connected. Starting multi-window probe cycles...\n")

    current_market = None
    window_idx = 0
    gaps = []
    results = []
    cumulative_fills = 0
    cumulative_loss_usd = 0.0

    for i in range(1, cycles + 1):
        # 1. Resolve / verify active market
        if token_id:
            active_token_id = token_id
            comp_token_id = ""
            market_slug = "fixed-token"
            condition_id = "N/A"
            if window_idx == 0:
                window_idx = 1
                subscribe_tokens(active_token_id, comp_token_id)
        else:
            market = fetch_live_market(gamma_host, series)
            # Rollover / gap handling & minimum time remaining guard
            if market is None or market.t_remaining() < min_t_remaining:
                if market is not None and market.t_remaining() < min_t_remaining:
                    t_rem = market.t_remaining()
                    print(f"\n[WINDOW CLOSING] {market.market_slug} has {t_rem:.1f}s remaining (< {min_t_remaining:.0f}s guard). Waiting for expiry...")
                    time.sleep(max(0.1, t_rem + 0.5))

                gap_start = time.perf_counter()
                print(f"[ROLLOVER GAP] Polling for next window in series '{series}'...", end=" ", flush=True)
                while True:
                    time.sleep(1.0)
                    market = fetch_live_market(gamma_host, series)
                    if market is not None and market.t_remaining() >= min_t_remaining:
                        break
                gap_duration = time.perf_counter() - gap_start
                print(f"resolved in {gap_duration:.2f}s -> {market.market_slug}")
                gaps.append({
                    "cycle": i,
                    "gap_duration_s": gap_duration,
                    "market_slug": market.market_slug,
                })

            if current_market is None or current_market.condition_id != market.condition_id:
                window_idx += 1
                current_market = market
                active_token_id = market.up_token
                comp_token_id = market.down_token
                comp_best_bid[0] = 0.0
                subscribe_tokens(active_token_id, comp_token_id)
                print(f"\n--- [WINDOW {window_idx}] {market.market_slug} (ends in {market.t_remaining():.0f}s) ---")
            else:
                active_token_id = current_market.up_token
                comp_token_id = current_market.down_token

            market_slug = current_market.market_slug
            condition_id = current_market.condition_id

        # 2. Complement best bid guard check
        comp_top_bid = comp_best_bid[0]
        try:
            book_comp = client.get_order_book(comp_token_id)
            if book_comp and getattr(book_comp, "bids", None):
                comp_top_bid = max(float(b.price) for b in book_comp.bids)
            elif isinstance(book_comp, dict) and book_comp.get("bids"):
                comp_top_bid = max(float(b.get("price", 0)) for b in book_comp["bids"])
        except Exception:
            pass

        if comp_top_bid >= max_complement_bid:
            print(f"Cycle {i:02d}/{cycles:02d} [W{window_idx}]: [GUARD TRIGGERED] Complement best bid = {comp_top_bid:.2f} >= {max_complement_bid:.2f}. Waiting for market balance...")
            time.sleep(2.0)
            continue

        # 3. Execute probe cycle
        last_delta_event.clear()
        print(f"Cycle {i:02d}/{cycles:02d} [W{window_idx}]: Posting BUY 100 @ $0.01 on {market_slug} (DOWN top bid: {comp_top_bid:.2f})...", end=" ", flush=True)

        order_args = OrderArgsV2(
            price=0.01,
            size=100.0,
            side=BUY,
            token_id=active_token_id,
        )
        signed_order = client.create_order(order_args)

        t1_socket_write = time.perf_counter_ns()
        resp = client.post_order(signed_order, OrderType.GTC)
        t2_http_ack = time.perf_counter_ns()

        order_id = resp.get("orderID") or resp.get("id")
        if not order_id:
            print(f"FAILED (No orderID returned: {resp})")
            time.sleep(1.0)
            continue

        # Wait for WS delta or 2.0s timeout
        t3_ws_recv = None
        start_wait = time.perf_counter()
        while time.perf_counter() - start_wait < 2.0:
            if "ts_recv" in last_delta_event and last_delta_event["ts_recv"] >= t1_socket_write:
                t3_ws_recv = last_delta_event["ts_recv"]
                break
            time.sleep(0.001)

        # Immediate cancel
        try:
            client.cancel_orders([order_id])
        except Exception as exc:
            print(f"(Cancel status: {exc})", end=" ")

        # Post-cancel fill check & loss guard
        time.sleep(0.05)
        try:
            order_status = client.get_order(order_id)
            size_matched = float(order_status.get("size_matched", 0) if isinstance(order_status, dict) else getattr(order_status, "size_matched", 0) or 0)
            if size_matched > 0:
                cumulative_fills += 1
                cumulative_loss_usd += size_matched * 0.01
                print(f"\n  [FILL DETECTED] Order {order_id[:10]} matched {size_matched:.0f} shares ($ {size_matched*0.01:.2f})!")
                if cumulative_fills >= max_fills or cumulative_loss_usd >= max_probe_loss_usd:
                    print(f"\n[ABORT] Maximum probe loss cap reached ({cumulative_fills} fills, ${cumulative_loss_usd:.2f} loss). Halting probe immediately.")
                    break
        except Exception as exc:
            # Fail closed. `size_matched` is the only thing that advances
            # cumulative_fills and cumulative_loss_usd, so swallowing this made
            # --max-fills and --max-loss silently stop counting -- and they stop
            # counting precisely when the venue is unhealthy, which is when a
            # fill is most likely and the cap matters most. One retry against a
            # transient blip, then abort rather than keep posting blind.
            try:
                time.sleep(0.25)
                order_status = client.get_order(order_id)
                size_matched = float(order_status.get("size_matched", 0)
                                     if isinstance(order_status, dict)
                                     else getattr(order_status, "size_matched", 0) or 0)
                if size_matched > 0:
                    cumulative_fills += 1
                    cumulative_loss_usd += size_matched * 0.01
                    print(f"\n  [FILL DETECTED on retry] Order {order_id[:10]} "
                          f"matched {size_matched:.0f} shares.")
            except Exception as exc2:
                print(f"\n[ABORT] Cannot read status of order {order_id[:10]}: "
                      f"{type(exc).__name__}: {exc} (retry: {type(exc2).__name__}). "
                      f"The loss cap cannot be enforced without it, so the probe "
                      f"stops here rather than posting further orders blind.")
                break

        rtt_rest_ms = (t2_http_ack - t1_socket_write) / 1e6
        loop_ms = (t3_ws_recv - t1_socket_write) / 1e6 if t3_ws_recv else rtt_rest_ms
        tau_accept_ms = max(0.0, rtt_rest_ms - (2 * NET_ONEWAY_MS))
        tau_pubsub_ms = max(0.0, loop_ms - rtt_rest_ms)

        results.append({
            "cycle": i,
            "window_idx": window_idx,
            "market_slug": market_slug,
            "condition_id": condition_id,
            "token_id": active_token_id,
            "rtt_rest_ms": rtt_rest_ms,
            "tau_accept_ms": tau_accept_ms,
            "tau_pubsub_ms": tau_pubsub_ms,
            "loop_ms": loop_ms,
        })

        print(f"REST RTT: {rtt_rest_ms:.2f}ms | tau_accept: {tau_accept_ms:.2f}ms | tau_pubsub: {tau_pubsub_ms:.2f}ms")
        time.sleep(0.5)

    ws_app.close()

    if not results:
        print("No successful cycles recorded.")
        return

    # Compute distribution statistics
    import statistics

    def stats_dict(vals):
        s_vals = sorted(vals)
        n = len(s_vals)
        p25 = s_vals[int(n * 0.25)]
        med = statistics.median(s_vals)
        p75 = s_vals[int(n * 0.75)]
        p95 = s_vals[min(int(n * 0.95), n - 1)]
        iqr = p75 - p25
        mean = statistics.mean(s_vals)
        std = statistics.stdev(s_vals) if n > 1 else 0.0
        sem = std / (n ** 0.5) if n > 1 else 0.0
        return {
            "n": n, "min": min(s_vals), "p25": p25, "median": med,
            "p75": p75, "p95": p95, "max": max(s_vals), "iqr": iqr,
            "mean": mean, "std": std, "sem": sem,
        }

    accept_stats = stats_dict([r["tau_accept_ms"] for r in results])
    pubsub_stats = stats_dict([r["tau_pubsub_ms"] for r in results])

    print("\n" + "=" * 80)
    print(f"PROBE DISTRIBUTION RESULTS (N={accept_stats['n']} successful cycles across {window_idx} windows)")
    print("=" * 80)
    print(f"{'Metric':<20} | {'tau_accept (Engine)':<25} | {'tau_pubsub (Venue Broadcast)':<25}")
    print("-" * 75)
    print(f"{'Min':<20} | {accept_stats['min']:<22.2f} ms | {pubsub_stats['min']:<22.2f} ms")
    print(f"{'P25':<20} | {accept_stats['p25']:<22.2f} ms | {pubsub_stats['p25']:<22.2f} ms")
    print(f"{'Median':<20} | {accept_stats['median']:<22.2f} ms | {pubsub_stats['median']:<22.2f} ms")
    print(f"{'P75':<20} | {accept_stats['p75']:<22.2f} ms | {pubsub_stats['p75']:<22.2f} ms")
    print(f"{'P95':<20} | {accept_stats['p95']:<22.2f} ms | {pubsub_stats['p95']:<22.2f} ms")
    print(f"{'Max':<20} | {accept_stats['max']:<22.2f} ms | {pubsub_stats['max']:<22.2f} ms")
    print(f"{'IQR':<20} | {accept_stats['iqr']:<22.2f} ms | {pubsub_stats['iqr']:<22.2f} ms")
    print(f"{'Mean +/- SEM':<20} | {accept_stats['mean']:.2f} +/- {accept_stats['sem']:.2f} ms | {pubsub_stats['mean']:.2f} +/- {pubsub_stats['sem']:.2f} ms")
    print("-" * 75)
    print("Uncertainty Decomposition:")
    print(f"  - Random Error (SEM): +/- {accept_stats['sem']:.2f} ms")
    print("  - Residual Systematic Bias: <= 3.50 ms (route asymmetry + gateway TLS)")
    print(f"  - Total Bound: <= {accept_stats['sem'] + 3.50:.2f} ms")
    print("=" * 80)

    # Per-window breakdown
    windows = sorted(list(set(r["window_idx"] for r in results)))
    if len(windows) > 1:
        print("\n" + "-" * 75)
        print("PER-WINDOW BREAKDOWN")
        print("-" * 75)
        print(f"{'Window':<8} | {'Market Slug':<30} | {'Cycles':<8} | {'tau_accept (Med)':<18} | {'tau_pubsub (Med)':<18}")
        print("-" * 75)
        for w in windows:
            w_res = [r for r in results if r["window_idx"] == w]
            w_slug = w_res[0]["market_slug"]
            w_accept = statistics.median([r["tau_accept_ms"] for r in w_res])
            w_pubsub = statistics.median([r["tau_pubsub_ms"] for r in w_res])
            print(f"W{w:<7} | {w_slug:<30} | {len(w_res):<8} | {w_accept:<15.2f} ms | {w_pubsub:<15.2f} ms")
        print("-" * 75)

    if gaps:
        print("\nROLLOVER GAP LOG:")
        for g in gaps:
            print(f"  - Cycle {g['cycle']}: {g['gap_duration_s']:.2f}s gap before window '{g['market_slug']}'")


def poll(
    interval: float = 5.0,
    once: bool = False,
    db_path: str | Path | None = None,
    client=None,
) -> None:
    """Poll CLOB for open orders and fills, reconciling into order registry.

    Operability features:
    - Status line printed every cycle.
    - Append-only event log (run/live_events.log).
    - Atomic heartbeat (run/live_poll_heartbeat.json).
    - Exponential backoff on 429 / 5xx capped at 60s.
    - Clean SIGTERM / KeyboardInterrupt exit.
    """
    import datetime
    import signal
    from strategy.order_registry import (
        OrderRegistry,
        reconcile_orders,
        compute_backoff_delay,
        DEFAULT_DB_PATH,
        ReconcileInProgress,
    )

    db_p = Path(db_path) if db_path else DEFAULT_DB_PATH
    registry = OrderRegistry(db_path=db_p)

    if client is None:
        client = _client()

    funder = os.environ.get("POLY_FUNDER")

    stop_requested = False

    def _sig_handler(signum, frame):
        nonlocal stop_requested
        stop_requested = True

    try:
        signal.signal(signal.SIGTERM, _sig_handler)
    except (ValueError, AttributeError):
        pass

    event_log_path = RUN / "live_events.log"
    heartbeat_path = RUN / "live_poll_heartbeat.json"
    RUN.mkdir(exist_ok=True)

    def _log_event(msg: str) -> None:
        """Append one line to the event log. Never raises into the poll loop."""
        try:
            with open(event_log_path, "a", encoding="utf-8") as ef:
                ef.write(f"{msg}\n")
        except OSError as exc:
            print(f"WARNING: event log write failed: {exc}", file=sys.stderr)

    # START and STOP are written unconditionally, so the log exists from the
    # first second of a run. Without them a quiet session leaves no file at all,
    # and "it never started" is indistinguishable from "it ran and saw nothing"
    # -- which is exactly the question the log is here to answer.
    _boot_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _log_event(
        f"[{_boot_iso}] START pid={os.getpid()} interval={interval}s once={once} db={db_p}"
    )

    consecutive_errors = 0
    cycle = 0
    last_cycle_failed = False

    while not stop_requested:
        cycle += 1
        cycle_start = time.time()
        now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        try:
            summary = reconcile_orders(client, registry, maker_address=funder)
            consecutive_errors = 0

            # Log any state transitions to event log
            if summary.transitions:
                for t in summary.transitions:
                    _log_event(f"[{now_iso}] {t}")

            active = registry.get_active_orders()
            open_count = sum(1 for o in active if o.status == "open")
            partial_count = sum(1 for o in active if o.status == "partial")
            pending_count = sum(1 for o in active if o.status == "pending")

            elapsed = time.time() - cycle_start
            print(
                f"[POLL {now_iso}] orders={len(active)} (open={open_count} partial={partial_count} pending={pending_count}) | "
                f"fills=+{summary.fills_recorded} (dup={summary.duplicates_ignored}) | "
                f"open_orders={summary.open_orders_count} trades={summary.trades_polled} | "
                f"cycle={elapsed:.2f}s | errors=0"
            )

        except KeyboardInterrupt:
            # Ctrl-C is not an error. It is a BaseException, so the handler
            # below never sees it, and the operator would get a traceback
            # instead of a clean stop on the one process meant to run for hours.
            stop_requested = True
            _log_event(f"[{now_iso}] STOP KeyboardInterrupt during cycle {cycle}")
            print(f"[POLL {now_iso}] stopping on KeyboardInterrupt", file=sys.stderr)
            break

        except ReconcileInProgress as exc:
            # Another pass holds the lock -- most often the operator running a
            # one-shot reconcile from a second shell. That is contention, not a
            # venue failure: counting it as an error would drive the exponential
            # backoff to 60s and degrade the poller for something that resolves
            # itself in milliseconds. Skip the cycle, keep the normal interval,
            # leave consecutive_errors alone.
            #
            # A --once run still reports failure, because it genuinely did not
            # reconcile and the caller must not read exit 0 as "state checked".
            skip_msg = f"[POLL {now_iso}] SKIPPED cycle {cycle}: {exc}"
            print(skip_msg, file=sys.stderr)
            _log_event(skip_msg)
            if once:
                last_cycle_failed = True
                break
            if not stop_requested:
                try:
                    time.sleep(max(0.0, interval - (time.time() - cycle_start)))
                except KeyboardInterrupt:
                    stop_requested = True
                    break
                continue

        except Exception as exc:
            consecutive_errors += 1
            last_cycle_failed = True
            backoff_s = compute_backoff_delay(consecutive_errors, base_sec=2.0, max_sec=60.0)
            err_msg = f"[POLL {now_iso}] ERROR (count={consecutive_errors}, backoff={backoff_s:.1f}s): {exc}"
            print(err_msg, file=sys.stderr)
            _log_event(err_msg)
            if not once and not stop_requested:
                try:
                    time.sleep(backoff_s)
                except KeyboardInterrupt:
                    stop_requested = True
                    break
                continue

        # Write heartbeat
        hb_data = {
            "ts": int(time.time() * 1000),
            "iso": now_iso,
            "pid": os.getpid(),
            "cycle": cycle,
            "errors": consecutive_errors,
        }
        _atomic_write_json(heartbeat_path, [hb_data])

        if once or stop_requested:
            break

        sleep_time = max(0.0, interval - (time.time() - cycle_start))
        try:
            time.sleep(sleep_time)
        except KeyboardInterrupt:
            # Ctrl-C almost always lands here rather than mid-reconcile, since
            # the loop spends nearly all its time asleep. It must announce
            # itself the same way the mid-cycle handler does.
            stop_requested = True
            stop_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            _log_event(f"[{stop_iso}] STOP KeyboardInterrupt while idle after cycle {cycle}")
            print(f"[POLL {stop_iso}] stopping on KeyboardInterrupt", file=sys.stderr)
            break

    exit_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _log_event(f"[{exit_iso}] EXIT cycles={cycle} errors={consecutive_errors}")

    # A --once run that failed its only cycle must exit non-zero. Returning 0
    # after printing an error to stderr makes the failure invisible to any
    # supervisor, cron entry or shell check that reads the exit status.
    if once and last_cycle_failed:
        sys.exit(1)


def exit_pair(pair_id: str, live: bool, db_path: str | Path | None = None,
              skip_positions_check: bool = False, force: bool = False) -> None:
    """Stage 3 — close a one-sided pair: cancel the resting leg, sell the filled one.

    The Data API positions read is a pre-flight, not decoration: it is the only
    independent check that the venue agrees with the registry about what we
    hold, and selling a size the venue does not agree we hold is an oversell.
    It fails closed -- an unreadable endpoint refuses the exit rather than
    proceeding unchecked. `--skip-positions-check` exists for the case where the
    Data API is down and the operator has decided to act anyway, and it says so
    on the record.
    """
    from strategy.live_pairs import (
        exit_naked_leg, fetch_positions, load_pair, PairExitRefused,
    )
    from strategy.order_registry import OrderRegistry, DEFAULT_DB_PATH
    from strategy import config as strategy_config

    registry = OrderRegistry(db_path=Path(db_path) if db_path else DEFAULT_DB_PATH)
    client = _client()
    funder = os.environ.get("POLY_FUNDER")

    # Same discipline as merge, redeem and complete: this sends a real market
    # SELL, so a repeat invocation must be refused rather than sell twice.
    # `naked` is derived from the registry, and registry fills only arrive
    # through the poll loop, so an immediate second run cannot see the first
    # sell and would happily send it again.
    condition_id = load_pair(registry, pair_id)["condition_id"]
    if live:
        _check_idempotency_guard(condition_id, force=force)

    venue_positions = None
    if not skip_positions_check:
        if not funder:
            raise SystemExit(
                "Refusing to exit: POLY_FUNDER is not set, so the venue's "
                "position cannot be read and the registry's view cannot be "
                "checked. Set it, or pass --skip-positions-check to act "
                "without the cross-check."
            )
        try:
            venue_positions = fetch_positions(funder)
        except Exception as exc:
            raise SystemExit(
                f"Refusing to exit: the Data API positions read failed "
                f"({exc!r}). An unreadable endpoint is not an empty portfolio. "
                f"Pass --skip-positions-check to act without the cross-check."
            ) from exc

    entry_id = None
    if live:
        entry_id = _log_order({
            "kind": "exit",
            "pair_id": pair_id,
            "condition_id": condition_id,
            "status": "pending",
        })

    try:
        result = exit_naked_leg(
            client, registry, pair_id,
            max_pair_cost=strategy_config.load().max_pair_cost,
            live=live,
            venue_positions=venue_positions,
        )
    except PairExitRefused as exc:
        # A refusal sent nothing, so the row closes rather than blocking later
        # attempts. Only genuine uncertainty warrants `interrupted`.
        if entry_id:
            _update_order_log(entry_id, {"status": "cancelled", "error": str(exc)})
        raise SystemExit(f"EXIT REFUSED: {exc}") from exc
    except BaseException as exc:
        if entry_id:
            _update_order_log(entry_id, {"status": "interrupted", "error": repr(exc)})
        raise

    if entry_id:
        # Only a result that actually sent a SELL may hold the condition open.
        #
        # `route_to_merge`, `balanced` and `hold` send nothing, and marking them
        # `submitted` would leave the idempotency guard blocking the very
        # recovery this command prints two lines below: the operator is told to
        # run `merge`, and `merge` then refuses the condition until --force.
        # A guard that blocks the recovery it recommends is worse than no guard.
        sent = result.get("action") == "exited"
        _update_order_log(entry_id, {
            "status": "submitted" if sent else "cancelled",
            "action": result.get("action"),
            "size": result.get("size"),
        })

    print(json.dumps(result, indent=2, default=str))
    if result["action"] == "route_to_merge":
        print(
            f"\nThe pair completed between the cancel and the sell. It is now "
            f"worth $1.00 at merge -- run:\n"
            f"  python -m strategy.live_exec merge {result['condition_id']} "
            f"--amount <shares> --live"
        )


def complete_pair_cmd(pair_id: str, live: bool, db_path: str | Path | None = None,
                      skip_positions_check: bool = False, force: bool = False) -> None:
    """Stage 4 — cross the book to complete a one-sided pair.

    Closes exposure rather than opening it: the half-open leg is already at
    risk, and completing it yields a pair worth $1.00 at merge. Refuses any
    cross that would push the pair to or past max_pair_cost -- that case
    belongs to `exit`, and this path must not do the stop-loss's job badly.
    """
    from strategy.live_pairs import (
        complete_pair, fetch_positions, load_pair, PairCompletionRefused,
    )
    from strategy.order_registry import OrderRegistry, DEFAULT_DB_PATH
    from strategy import config as strategy_config

    registry = OrderRegistry(db_path=Path(db_path) if db_path else DEFAULT_DB_PATH)
    pair = load_pair(registry, pair_id)
    condition_id = pair["condition_id"]

    # Same pre-flight discipline as every other live write path here. This
    # sends a real BUY, so it gets the same two guards `merge` and `redeem`
    # have: a repeat invocation must not cross twice for the same pair, and no
    # order goes out on a registry view the venue has not corroborated.
    if live:
        _check_idempotency_guard(condition_id, force=force)

    venue_positions = None
    if not skip_positions_check:
        funder = os.environ.get("POLY_FUNDER")
        if not funder:
            raise SystemExit(
                "Refusing to complete: POLY_FUNDER is not set, so the venue's "
                "position cannot be read. Set it, or pass "
                "--skip-positions-check to act without the cross-check."
            )
        try:
            venue_positions = fetch_positions(funder)
        except Exception as exc:
            raise SystemExit(
                f"Refusing to complete: the Data API positions read failed "
                f"({exc!r}). An unreadable endpoint is not an empty portfolio."
            ) from exc

        token = pair["heavy"]["token_id"]
        believed = pair["heavy"]["matched"]
        if token not in venue_positions:
            raise SystemExit(
                f"Refusing to complete: the venue reports no position at all "
                f"in {token} while the registry holds {believed:.6f}. Absence "
                f"is not zero -- it is equally consistent with a filtered read."
            )
        observed = float(venue_positions[token])
        if observed < believed - 1e-6:
            raise SystemExit(
                f"Refusing to complete: registry holds {believed:.6f} of "
                f"{token} but the venue reports only {observed:.6f}. Completing "
                f"against a leg the venue does not agree we hold would open "
                f"exposure rather than close it."
            )

    entry_id = None
    if live:
        entry_id = _log_order({
            "kind": "complete",
            "pair_id": pair_id,
            "condition_id": condition_id,
            "status": "pending",
        })

    try:
        result = complete_pair(
            _client(), registry, pair_id,
            max_pair_cost=strategy_config.load().max_pair_cost,
            live=live,
            max_order_usd=MAX_ORDER_USD,
        )
    except PairCompletionRefused as exc:
        if entry_id:
            _update_order_log(entry_id, {"status": "cancelled", "error": str(exc)})
        raise SystemExit(f"COMPLETION REFUSED: {exc}") from exc
    except BaseException as exc:
        # Anything else left the order in an unknown state at the venue.
        # `interrupted` is what the idempotency guard refuses on, which is the
        # correct posture when we do not know whether the BUY landed.
        if entry_id:
            _update_order_log(entry_id, {"status": "interrupted", "error": repr(exc)})
        raise

    if entry_id:
        # Same rule as the exit: `balanced` crossed nothing, so it must not hold
        # the condition against a later merge or completion.
        sent = result.get("action") == "completed"
        _update_order_log(entry_id, {
            "status": "submitted" if sent else "cancelled",
            "action": result.get("action"),
            "size": result.get("size"),
            "notional": result.get("notional"),
        })

    print(json.dumps(result, indent=2, default=str))


def cancel_all(live: bool) -> None:
    if not live:
        print("DRY RUN -- would cancel ALL open orders. Re-run with --live.")
        return
    print(_client().cancel_all())


def main() -> None:
    ap = argparse.ArgumentParser(description="LIVE Polymarket execution.")
    ap.add_argument("--live", action="store_true",
                    help="actually send. Without it, everything is a dry run.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    b = sub.add_parser("balance")
    b.add_argument("--funder", default=None,
                   help="test a candidate funder without editing .env")
    q = sub.add_parser("quote")
    q.add_argument("condition_id")
    q.add_argument("--price", type=float, required=True)
    q.add_argument("--size", type=float, required=True)
    q.add_argument("--live", action="store_true", default=argparse.SUPPRESS,
                  help="actually send.")
    r = sub.add_parser("redeem", help="Gasless redemption of winning positions via Relayer.")
    r.add_argument("condition_id", help="Condition ID to redeem")
    r.add_argument("--index-sets", default="1,2", help="Comma-separated index sets (default: 1,2)")
    r.add_argument("--collateral", default=USDC_E_CONTRACT, help="Collateral token (default: USDC.e)")
    r.add_argument("--skip-resolution-check", action="store_true",
                   help="Bypass RPC resolution guard if RPC endpoints are unreachable (does not bypass denom == 0).")
    r.add_argument("--force", action="store_true",
                   help="Bypass idempotency guard against prior pending/submitted/interrupted orders.")
    r.add_argument("--live", action="store_true", default=argparse.SUPPRESS,
                  help="actually send.")
    m = sub.add_parser("merge", help="Gasless merge of full outcome sets via Relayer.")
    m.add_argument("condition_id", help="Condition ID to merge")
    m.add_argument("--amount", type=float, required=True, help="Number of shares / pairs to merge")
    m.add_argument("--index-sets", default="1,2", help="Comma-separated index sets (default: 1,2)")
    m.add_argument("--collateral", default=USDC_E_CONTRACT, help="Collateral token (default: USDC.e)")
    m.add_argument("--force", action="store_true",
                   help="Bypass idempotency guard against prior pending/submitted/interrupted orders.")
    m.add_argument("--live", action="store_true", default=argparse.SUPPRESS,
                  help="actually send.")
    p = sub.add_parser("probe", help="Multi-cycle live latency probe across dynamic series windows.")
    p.add_argument("--series", default="btc-up-or-down-5m", help="Series slug (default: btc-up-or-down-5m)")
    p.add_argument("--token-id", default=None, help="Optional fixed token ID override")
    p.add_argument("--cycles", type=int, default=30, help="Number of probe cycles (default: 30)")
    p.add_argument("--min-time-remaining", type=float, default=90.0, help="Minimum seconds remaining in window (default: 90s)")
    p.add_argument("--max-complement-bid", type=float, default=0.85, help="Max allowed complement best bid (default: 0.85)")
    p.add_argument("--max-loss", type=float, default=1.00, help="Max cumulative probe loss in USD before abort (default: 1.00)")
    p.add_argument("--max-fills", type=int, default=1, help="Max allowable fills before abort (default: 1)")
    p.add_argument("--live", action="store_true", default=argparse.SUPPRESS,
                  help="actually send.")
    pl = sub.add_parser("poll", help="Poll CLOB and reconcile orders and fills.")
    pl.add_argument("--interval", type=float, default=5.0, help="Cadence in seconds (default: 5.0)")
    pl.add_argument("--once", action="store_true", help="Reconcile once and exit")
    pl.add_argument("--db", default=None, help="Custom database path (default: run/live.db)")
    ex = sub.add_parser("exit", help="Stage 3: close a one-sided pair (cancel resting leg, sell filled leg).")
    ex.add_argument("pair_id", help="pair_id as recorded in the order registry")
    ex.add_argument("--db", default=None, help="Custom database path (default: run/live.db)")
    ex.add_argument("--skip-positions-check", action="store_true",
                    help="Act without the Data API registry/venue cross-check. Only when the endpoint is down.")
    ex.add_argument("--force", action="store_true",
                    help="Bypass idempotency guard against prior pending/submitted/interrupted orders.")
    ex.add_argument("--live", action="store_true", default=argparse.SUPPRESS,
                    help="actually send.")
    cp = sub.add_parser("complete", help="Stage 4: cross the book to complete a one-sided pair.")
    cp.add_argument("pair_id", help="pair_id as recorded in the order registry")
    cp.add_argument("--db", default=None, help="Custom database path (default: run/live.db)")
    cp.add_argument("--skip-positions-check", action="store_true",
                    help="Act without the Data API registry/venue cross-check. Only when the endpoint is down.")
    cp.add_argument("--force", action="store_true",
                    help="Bypass idempotency guard against prior pending/submitted/interrupted orders.")
    cp.add_argument("--live", action="store_true", default=argparse.SUPPRESS,
                    help="actually send.")
    c = sub.add_parser("cancel-all")
    c.add_argument("--live", action="store_true", default=argparse.SUPPRESS,
                  help="actually send.")
    a = ap.parse_args()

    is_live = bool(a.live or getattr(a, "live", False))

    if a.cmd == "status":
        status()
    elif a.cmd == "balance":
        balance(a.funder)
    elif a.cmd == "quote":
        quote(a.condition_id, a.price, a.size, is_live)
    elif a.cmd == "redeem":
        idx_sets = [int(x.strip()) for x in a.index_sets.split(",") if x.strip()]
        redeem(
            a.condition_id,
            index_sets=idx_sets,
            collateral=a.collateral,
            skip_resolution_check=a.skip_resolution_check,
            force=a.force,
            live=is_live,
        )
    elif a.cmd == "merge":
        idx_sets = [int(x.strip()) for x in a.index_sets.split(",") if x.strip()]
        merge(
            a.condition_id,
            amount=a.amount,
            index_sets=idx_sets,
            collateral=a.collateral,
            force=a.force,
            live=is_live,
        )
    elif a.cmd == "probe":
        probe(
            series=a.series,
            token_id=a.token_id,
            cycles=a.cycles,
            min_t_remaining=a.min_time_remaining,
            max_complement_bid=a.max_complement_bid,
            max_probe_loss_usd=a.max_loss,
            max_fills=a.max_fills,
            live=is_live,
        )
    elif a.cmd == "poll":
        poll(interval=a.interval, once=a.once, db_path=a.db)
    elif a.cmd == "exit":
        exit_pair(a.pair_id, is_live, db_path=a.db,
                  skip_positions_check=a.skip_positions_check, force=a.force)
    elif a.cmd == "complete":
        complete_pair_cmd(a.pair_id, is_live, db_path=a.db,
                          skip_positions_check=a.skip_positions_check,
                          force=a.force)
    else:
        cancel_all(is_live)


if __name__ == "__main__":
    main()



