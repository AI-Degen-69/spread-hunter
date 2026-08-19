"""live/engine/audit.py - Three-way audit comparing Registry, Venue, and Chain views.

Compares:
1. Local Registry View (live/run/live.db: orders, fills, size_matched)
2. Venue CLOB View (Polymarket CLOB API: get_order, get_open_orders, get_trades)
3. On-Chain State (Polygon CTF ERC-1155 token balances via RPC, plus relayer merge logs)

Reports AGREE or names the divergence with exact quantities from each layer.
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from engine.order_registry import OrderRegistry, DEFAULT_DB_PATH, SIZE_EPS
from engine.markets import fetch_pinned_market

RPC_ENDPOINTS = [
    "https://polygon-bor-rpc.publicnode.com",
    "https://1rpc.io/matic",
    "https://rpc.ankr.com/polygon",
    "https://polygon.drpc.org",
]
CTF_CONTRACT = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
LIVE_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class AuditResult:
    agree: bool
    pair_id: Optional[str]
    condition_id: str
    up_token: str
    down_token: str
    registry_up_filled: float
    registry_dn_filled: float
    venue_up_matched: float
    venue_dn_matched: float
    merged_amount: float
    chain_up_balance: float
    chain_dn_balance: float
    divergences: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


def get_onchain_erc1155_balance(token_id: str, owner_addr: str) -> float:
    """Read ERC-1155 balance directly from Polygon RPC with endpoint fallback."""
    if not token_id or not owner_addr or owner_addr == "0x" + "0" * 40:
        return 0.0
    calldata = "0x00fdd58e" + owner_addr[2:].lower().rjust(64, "0") + hex(int(token_id))[2:].rjust(64, "0")
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [{"to": CTF_CONTRACT, "data": calldata}, "latest"],
    }).encode()

    for endpoint in RPC_ENDPOINTS:
        try:
            req = urllib.request.Request(
                endpoint,
                data=payload,
                headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                res = json.loads(resp.read().decode())
                if "result" in res and res["result"] and res["result"] != "0x":
                    return float(Decimal(int(res["result"], 16)) / Decimal(10**6))
        except Exception:
            continue
    return 0.0


def read_merged_amount_from_logs(condition_id: str, orders_log_path: Optional[Path] = None) -> float:
    """Sum executed merge amounts for this condition_id from live_orders.json."""
    path = orders_log_path or (LIVE_ROOT / "run" / "live_orders.json")
    if not path.is_file():
        return 0.0
    try:
        with open(path, "r") as f:
            entries = json.load(f)
        total_merged = 0.0
        cond_clean = condition_id.lower().replace("0x", "")
        for e in entries:
            if not isinstance(e, dict):
                continue
            is_merge = (e.get("action") == "MERGE" or e.get("kind") == "merge")
            entry_cond = str(e.get("condition_id") or "").lower().replace("0x", "")
            if is_merge and entry_cond == cond_clean:
                is_executed = (
                    e.get("state") == "STATE_EXECUTED"
                    or e.get("relayer_state") == "STATE_EXECUTED"
                    or e.get("status") in ("executed", "submitted")
                )
                if is_executed:
                    amt = e.get("amount") or e.get("size")
                    if amt is not None:
                        total_merged += float(amt)
                    elif "call_data" in e and len(e["call_data"]) >= 10 + 64 * 5:
                        amount_hex = e["call_data"][10 + 64 * 4 : 10 + 64 * 5]
                        amt_dec = int(amount_hex, 16) / 10**6
                        total_merged += float(amt_dec)
        return total_merged
    except Exception:
        return 0.0


def audit_three_way(
    target: str,
    client: Any = None,
    funder: Optional[str] = None,
    db_path: Optional[Path | str] = None,
    orders_log_path: Optional[Path] = None,
) -> AuditResult:
    """Perform a read-only 3-way audit across Registry, Venue CLOB, and Chain."""
    db = Path(db_path) if db_path else DEFAULT_DB_PATH
    registry = OrderRegistry(db)
    funder_addr = funder or os.environ.get("POLY_FUNDER", "")

    # 1. Resolve pair / condition from target
    if target.startswith("pair-"):
        orders = registry.get_orders_by_pair(target)
        if not orders:
            raise ValueError(f"No orders found in registry for pair {target}")
        condition_id = orders[0].condition_id
        pair_id = target
    else:
        condition_id = target
        pair_id = None
        # Find all orders for this condition
        with registry._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM orders WHERE condition_id = ? ORDER BY posted_ts",
                (condition_id,),
            ).fetchall()
            orders = [registry._row_to_order(r) for r in rows]

    m = fetch_pinned_market(condition_id, require_rewards=False)
    if m is None:
        raise ValueError(f"Could not fetch market metadata for condition_id {condition_id}")

    up_token = str(m.up_token)
    dn_token = str(m.down_token)

    # 2. Registry View
    reg_up_filled = 0.0
    reg_dn_filled = 0.0
    order_details = []

    for o in orders:
        matched = registry.get_size_matched(o.id)
        if o.token_id == up_token:
            reg_up_filled += matched
        elif o.token_id == dn_token:
            reg_dn_filled += matched
        order_details.append({
            "local_id": o.id,
            "venue_order_id": o.order_id,
            "token_id": o.token_id,
            "side": o.side,
            "price": o.price,
            "original_size": o.original_size,
            "registry_matched": matched,
            "registry_status": o.status,
        })

    # 3. Venue View
    venue_up_matched = 0.0
    venue_dn_matched = 0.0
    venue_orders_data = {}

    if client is not None:
        for od in order_details:
            v_id = od["venue_order_id"]
            if v_id:
                try:
                    v_data = client.get_order(v_id)
                    venue_orders_data[v_id] = v_data
                    v_matched = float(v_data.get("size_matched") or 0.0)
                    v_tok = str(v_data.get("asset_id") or v_data.get("token_id") or "")
                    if v_tok == up_token:
                        venue_up_matched += v_matched
                    elif v_tok == dn_token:
                        venue_dn_matched += v_matched
                    od["venue_status"] = v_data.get("status")
                    od["venue_matched"] = v_matched
                except Exception as e:
                    od["venue_error"] = str(e)

    # 4. Chain View & Merge Log
    merged_amount = read_merged_amount_from_logs(condition_id, orders_log_path)
    chain_up_bal = get_onchain_erc1155_balance(up_token, funder_addr)
    chain_dn_bal = get_onchain_erc1155_balance(dn_token, funder_addr)

    # Expected on-chain held = filled - merged
    expected_chain_up = max(0.0, reg_up_filled - merged_amount)
    expected_chain_dn = max(0.0, reg_dn_filled - merged_amount)

    # 5. Divergence Checks
    divergences = []

    # Check Registry Fills vs Venue Fills
    if abs(reg_up_filled - venue_up_matched) > SIZE_EPS and client is not None:
        divergences.append(
            f"UP leg fill mismatch: Registry has {reg_up_filled:.4f} sh, Venue reported {venue_up_matched:.4f} sh"
        )
    if abs(reg_dn_filled - venue_dn_matched) > SIZE_EPS and client is not None:
        divergences.append(
            f"DOWN leg fill mismatch: Registry has {reg_dn_filled:.4f} sh, Venue reported {venue_dn_matched:.4f} sh"
        )

    # Check On-Chain Balance vs Expected Remaining Position
    if abs(chain_up_bal - expected_chain_up) > SIZE_EPS:
        divergences.append(
            f"UP on-chain balance divergence: Chain has {chain_up_bal:.4f} sh, Expected {expected_chain_up:.4f} sh "
            f"(Filled {reg_up_filled:.4f} - Merged {merged_amount:.4f})"
        )
    if abs(chain_dn_bal - expected_chain_dn) > SIZE_EPS:
        divergences.append(
            f"DOWN on-chain balance divergence: Chain has {chain_dn_bal:.4f} sh, Expected {expected_chain_dn:.4f} sh "
            f"(Filled {reg_dn_filled:.4f} - Merged {merged_amount:.4f})"
        )

    # Check Order Status agreement
    for od in order_details:
        v_status = od.get("venue_status")
        r_status = od.get("registry_status")
        if v_status and r_status:
            # Map venue MATCHED -> filled
            v_norm = "filled" if v_status == "MATCHED" else v_status.lower()
            if v_norm != r_status and not (v_norm == "filled" and r_status == "filled"):
                divergences.append(
                    f"Order {od['local_id'][:8]} ({od['venue_order_id']}) status mismatch: "
                    f"Registry={r_status}, Venue={v_status}"
                )

    agree = len(divergences) == 0

    return AuditResult(
        agree=agree,
        pair_id=pair_id,
        condition_id=condition_id,
        up_token=up_token,
        down_token=dn_token,
        registry_up_filled=reg_up_filled,
        registry_dn_filled=reg_dn_filled,
        venue_up_matched=venue_up_matched,
        venue_dn_matched=venue_dn_matched,
        merged_amount=merged_amount,
        chain_up_balance=chain_up_bal,
        chain_dn_balance=chain_dn_bal,
        divergences=divergences,
        details={
            "orders": order_details,
            "funder": funder_addr,
        },
    )


def format_audit_report(res: AuditResult) -> str:
    """Render a clean textual audit summary report."""
    lines = [
        "=" * 80,
        f"THREE-WAY AUDIT: {res.condition_id}",
        f"PAIR ID:         {res.pair_id or 'all-pairs-for-condition'}",
        f"RESULT:          {'AGREE' if res.agree else 'DIVERGENCE'}",
        "=" * 80,
        "1. REGISTRY VIEW (live/run/live.db):",
        f"   UP Filled:    {res.registry_up_filled:.4f} shares",
        f"   DOWN Filled:  {res.registry_dn_filled:.4f} shares",
        "",
        "2. VENUE CLOB VIEW (Polymarket Matching Engine):",
        f"   UP Matched:   {res.venue_up_matched:.4f} shares",
        f"   DOWN Matched: {res.venue_dn_matched:.4f} shares",
        "",
        "3. CHAIN & SETTLEMENT VIEW (Polygon RPC & Relayer Logs):",
        f"   Merged:       {res.merged_amount:.4f} pairs",
        f"   UP On-Chain:  {res.chain_up_balance:.4f} shares",
        f"   DOWN On-Chain:{res.chain_dn_balance:.4f} shares",
        "=" * 80,
    ]
    if res.divergences:
        lines.append("DIVERGENCES DETECTED:")
        for d in res.divergences:
            lines.append(f"  [X] {d}")
        lines.append("=" * 80)
    else:
        lines.append("ALL 3 LAYERS AGREE: Registry Fills == Venue Fills, Net Held == On-Chain Balance.")
        lines.append("=" * 80)
    return "\n".join(lines)
