"""Venue-sourced account value and P&L.

The dashboard's headline number used to be `config.bot_quote_bank_baseline_usd + realized_pnl`.
`bot_quote_bank_baseline_usd` is a paper-run parameter -- nobody deposited it -- so the tile
reported $100.30 for an account the venue valued at $101.88. This module reads
the account from the venue instead, and a sweep records what it read into the
registry so the dashboard can keep its "zero venue network calls" contract.

Every read here is a GET. Nothing in this module can open or increase exposure,
which is what makes it permitted under the staged exposure rule in AGENTS.md.

Two independent venue sources agree on all-time P&L, and the sweep records both:

* `Sum(closed_positions.realizedPnl)` -- what the trades actually returned.
* `user-pnl` -- the series the Polymarket UI itself plots.

They are recorded separately rather than reconciled into one figure. When they
disagree, the disagreement is the finding.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

DATA_API_BASE = "https://data-api.polymarket.com"

# The host behind the Polymarket UI's own P&L chart. It is not in the published
# API reference, so it is used as a cross-check and never as the only source:
# `pnl_closed_usd` is computed from the documented /closed-positions endpoint
# and stands on its own if this host disappears.
USER_PNL_BASE = "https://user-pnl-api.polymarket.com"

# The /positions endpoint defaults to limit=100, and a truncated page is not an
# empty portfolio. Same page size live_pairs.py already uses.
POSITIONS_PAGE_SIZE = 100

_UA = {"User-Agent": "spread-hunter"}


def _get_json(base: str, path: str, params: dict[str, Any], timeout: float) -> Any:
    """GET and decode JSON. Raises on transport or decode failure."""
    url = f"{base}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _as_float(value: Any) -> Optional[float]:
    """Coerce to float, or None. Never 0.0 for an absent field.

    A missing `cashPnl` means the venue did not report one, which is not the
    same claim as "the position is flat". Same NULL-not-zero rule the KPI layer
    already applies to spread_capture and unrealized_usd.
    """
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------------------
# Venue reads. Each returns None on any failure so a sweep can record a partial
# mark rather than fabricating a number it did not obtain.
# ----------------------------------------------------------------------------

def fetch_positions_value(funder: str, timeout: float = 15.0) -> Optional[float]:
    """Total current value of the account's open positions, in USD.

    Data API v2 returns {"data": {"proxy_wallet": ..., "value": ...}}.
    Data API v1 returned a list of one row: [{"user": ..., "value": ...}].
    Both shapes are accepted.
    """
    try:
        payload = _get_json(DATA_API_BASE, "/v2/value", {"user": funder}, timeout)
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        try:
            payload = _get_json(DATA_API_BASE, "/value", {"user": funder}, timeout)
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
            return None

    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict):
            return _as_float(data.get("value"))
        elif isinstance(data, list):
            rows = data
        else:
            rows = [payload]
    elif isinstance(payload, list):
        rows = payload
    else:
        return None

    total = 0.0
    seen = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        v = _as_float(row.get("value"))
        if v is not None:
            total += v
            seen = True
    return total if seen else None


def fetch_open_positions(funder: str, timeout: float = 15.0) -> Optional[list[dict]]:
    """Every open position, paginated, including sub-share dust.

    Uses Data API v2 cursor pagination with filter_amount=0 (replacing sizeThreshold=0).
    Falls back to Data API v1 pagination if needed.
    """
    out: list[dict] = []
    cursor: Optional[str] = None
    offset = 0
    use_v2 = True

    while True:
        try:
            if use_v2:
                params: dict[str, Any] = {"user": funder, "filter_amount": 0, "limit": POSITIONS_PAGE_SIZE}
                if cursor:
                    params["cursor"] = cursor
                payload = _get_json(DATA_API_BASE, "/v2/positions", params, timeout)
            else:
                params = {"user": funder, "sizeThreshold": 0, "limit": POSITIONS_PAGE_SIZE, "offset": offset}
                payload = _get_json(DATA_API_BASE, "/positions", params, timeout)
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
            if use_v2 and not out:
                use_v2 = False
                continue
            return None

        if isinstance(payload, dict) and "data" in payload:
            data = payload.get("data") or []
            rows = [r for r in data if isinstance(r, dict)]
            for r in rows:
                if "condition_id" in r and "conditionId" not in r:
                    r["conditionId"] = r["condition_id"]
                if "unrealized_pnl" in r and "cashPnl" not in r:
                    r["cashPnl"] = r["unrealized_pnl"]
                if "realized_pnl" in r and "realizedPnl" not in r:
                    r["realizedPnl"] = r["realized_pnl"]
                if "entry_cost_usdc" in r and "initialValue" not in r:
                    r["initialValue"] = r["entry_cost_usdc"]
            out.extend(rows)
            pagination = payload.get("pagination") or {}
            next_cursor = pagination.get("next_cursor")
            if not next_cursor:
                break
            if str(next_cursor) == cursor:
                return None
            cursor = str(next_cursor)
        elif isinstance(payload, list):
            rows = [r for r in payload if isinstance(r, dict)]
            for r in rows:
                if "conditionId" in r and "condition_id" not in r:
                    r["condition_id"] = r["conditionId"]
                if "cashPnl" in r and "unrealized_pnl" not in r:
                    r["unrealized_pnl"] = r["cashPnl"]
                if "realizedPnl" in r and "realized_pnl" not in r:
                    r["realized_pnl"] = r["realizedPnl"]
                if "initialValue" in r and "entry_cost_usdc" not in r:
                    r["entry_cost_usdc"] = r["initialValue"]
            out.extend(rows)
            if len(payload) < POSITIONS_PAGE_SIZE:
                break
            offset += POSITIONS_PAGE_SIZE
        else:
            break

    return out


def fetch_closed_positions(funder: str, timeout: float = 15.0) -> Optional[list[dict]]:
    """Every position the account has closed, with the venue's realised P&L.

    Uses Data API v2 GET /v2/positions?status=CLOSED, falling back to /closed-positions.
    """
    out: list[dict] = []
    cursor: Optional[str] = None
    offset = 0
    use_v2 = True

    while True:
        try:
            if use_v2:
                params: dict[str, Any] = {"user": funder, "status": "CLOSED", "limit": POSITIONS_PAGE_SIZE}
                if cursor:
                    params["cursor"] = cursor
                payload = _get_json(DATA_API_BASE, "/v2/positions", params, timeout)
            else:
                params = {"user": funder, "limit": POSITIONS_PAGE_SIZE, "offset": offset}
                payload = _get_json(DATA_API_BASE, "/closed-positions", params, timeout)
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
            if use_v2 and not out:
                use_v2 = False
                continue
            return None

        if isinstance(payload, dict) and "data" in payload:
            data = payload.get("data") or []
            rows = [r for r in data if isinstance(r, dict)]
            for r in rows:
                if "condition_id" in r and "conditionId" not in r:
                    r["conditionId"] = r["condition_id"]
                if "realized_pnl" in r and "realizedPnl" not in r:
                    r["realizedPnl"] = r["realized_pnl"]
                if "entry_cost_usdc" in r and "initialValue" not in r:
                    r["initialValue"] = r["entry_cost_usdc"]
            out.extend(rows)
            pagination = payload.get("pagination") or {}
            next_cursor = pagination.get("next_cursor")
            if not next_cursor:
                break
            if str(next_cursor) == cursor:
                return None
            cursor = str(next_cursor)
        elif isinstance(payload, list):
            rows = [r for r in payload if isinstance(r, dict)]
            for r in rows:
                if "conditionId" in r and "condition_id" not in r:
                    r["condition_id"] = r["conditionId"]
                if "realizedPnl" in r and "realized_pnl" not in r:
                    r["realized_pnl"] = r["realizedPnl"]
                if "initialValue" in r and "entry_cost_usdc" not in r:
                    r["entry_cost_usdc"] = r["initialValue"]
            out.extend(rows)
            if len(payload) < POSITIONS_PAGE_SIZE:
                break
            offset += POSITIONS_PAGE_SIZE
        else:
            break

    return out


def fetch_user_pnl(funder: str, interval: str = "all",
                   fidelity: str = "1d", timeout: float = 15.0) -> Optional[float]:
    """Newest point of the venue's own P&L series, in USD.

    Tries Data API v2 GET /v2/user-pnl first, falling back to legacy USER_PNL_BASE.
    """
    # 1. Try Data API v2
    try:
        payload = _get_json(
            DATA_API_BASE, "/v2/user-pnl",
            {"user": funder, "interval": interval, "fidelity": fidelity},
            timeout,
        )
        if isinstance(payload, dict) and "data" in payload:
            data = payload.get("data") or {}
            points = data.get("points") if isinstance(data, dict) else None
            if points and isinstance(points, list):
                rows = [r for r in points if isinstance(r, dict) and r.get("t") is not None]
                if rows:
                    newest = max(rows, key=lambda r: float(r["t"]))
                    val = _as_float(newest.get("p"))
                    if val is not None:
                        return val
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        pass

    # 2. Legacy fallback
    try:
        payload = _get_json(
            USER_PNL_BASE, "/user-pnl",
            {"user_address": funder, "interval": interval, "fidelity": fidelity},
            timeout,
        )
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None
    rows = [r for r in (payload or []) if isinstance(r, dict) and r.get("t") is not None]
    if not rows:
        return None
    newest = max(rows, key=lambda r: float(r["t"]))
    return _as_float(newest.get("p"))


# ----------------------------------------------------------------------------
# Composition. Pure: no network, no clock, no registry. Everything the sweep
# decides is decided here, where a test can drive it with fixed inputs.
# ----------------------------------------------------------------------------

def _sum_field(rows: Optional[list[dict]], *fields: str) -> Optional[float]:
    """Sum across rows, taking the first matching field from *fields for each row.
    None if rows is None; 0.0 for an empty list.

    The distinction is the whole point: `None` means the venue was not reached,
    `0.0` means it was reached and reported nothing held. Collapsing the two is
    how a fabricated number gets onto a dashboard.
    """
    if rows is None:
        return None
    total = 0.0
    for row in rows:
        for f in fields:
            if f in row:
                v = _as_float(row.get(f))
                if v is not None:
                    total += v
                break
    return total


def compose_account_mark(
    collateral_usd: Optional[float],
    positions_value_usd: Optional[float],
    open_positions: Optional[list[dict]],
    closed_positions: Optional[list[dict]],
    user_pnl_usd: Optional[float],
) -> dict[str, Any]:
    """Build one account mark from raw venue reads.

    account_value = collateral + value of open positions. Both legs are
    required: collateral alone understates an account holding shares, and
    positions alone ignores the cash. If either is missing the value is None.

    P&L is all-time and excludes deposits. `pnl_pct` is measured against net
    deposits, derived as `account_value - pnl` -- which is what makes 0.30 on a
    101.88 account read as +0.30%, matching the venue's own display.
    """
    unrealized_usd = _sum_field(open_positions, "unrealized_pnl", "cashPnl", "cash_pnl")
    committed_usd = _sum_field(open_positions, "entry_cost_usdc", "total_cost_usdc", "initialValue", "initial_value")
    pnl_closed_usd = _sum_field(closed_positions, "realized_pnl", "realizedPnl")

    account_value_usd: Optional[float] = None
    if collateral_usd is not None and positions_value_usd is not None:
        account_value_usd = collateral_usd + positions_value_usd

    # Prefer the venue's own series -- it is the number the account holder sees
    # on Polymarket -- and fall back to the documented endpoint's sum.
    pnl_usd = user_pnl_usd if user_pnl_usd is not None else pnl_closed_usd

    # Disagreement between the two sources is information, not noise. Recorded
    # so a reconciliation can surface it instead of a silent preference.
    pnl_source_gap: Optional[float] = None
    if user_pnl_usd is not None and pnl_closed_usd is not None:
        pnl_source_gap = user_pnl_usd - pnl_closed_usd

    # Net deposits. Zero (a fresh account) leaves the percentage undefined, and
    # a printed 0.00% would read as "flat" rather than "unmeasurable".
    pnl_pct: Optional[float] = None
    if account_value_usd is not None and pnl_usd is not None:
        basis = account_value_usd - pnl_usd
        if basis:
            pnl_pct = 100.0 * pnl_usd / basis

    return {
        "collateral_usd": collateral_usd,
        "positions_value_usd": positions_value_usd,
        "account_value_usd": account_value_usd,
        "pnl_usd": pnl_usd,
        "pnl_pct": pnl_pct,
        "pnl_closed_usd": pnl_closed_usd,
        "pnl_series_usd": user_pnl_usd,
        "pnl_source_gap": pnl_source_gap,
        "unrealized_usd": unrealized_usd,
        "committed_usd": committed_usd,
        "open_positions_count": None if open_positions is None else len(open_positions),
        "closed_positions_count": None if closed_positions is None else len(closed_positions),
        "source": "venue",
    }


def read_account(funder: str, collateral_usd: Optional[float],
                 timeout: float = 15.0) -> dict[str, Any]:
    """Read every venue source and compose one mark.

    `collateral_usd` is passed in rather than fetched: it needs signed CLOB
    credentials, which live in live_exec, while everything here is a public
    address-keyed GET.
    """
    return compose_account_mark(
        collateral_usd=collateral_usd,
        positions_value_usd=fetch_positions_value(funder, timeout),
        open_positions=fetch_open_positions(funder, timeout),
        closed_positions=fetch_closed_positions(funder, timeout),
        user_pnl_usd=fetch_user_pnl(funder, timeout=timeout),
    )


# --- live balance + float mark (moved from core_brain.order_manager) ----------
# `fetch_live_balance` reads the CLOB client's collateral balance; the
# poll loop and the fleet both need it, so it lives with the other
# venue account reads instead of live_exec's CLI grab-bag.


def fetch_live_balance(funder: str | None = None) -> float | None:
    """Fetch live USDC collateral balance from venue. Returns None on network error / offline / no credentials."""
    import os
    if not (os.environ.get("POLY_PRIVATE_KEY") or os.environ.get("POLY_KEY")):
        return None
    try:
        from py_clob_client_v2.clob_types import AssetType, BalanceAllowanceParams
        from core_brain.venue import client
        who = funder or os.environ.get("POLY_FUNDER")
        sig_type = int(os.environ.get("POLY_SIG_TYPE", "3"))
        r = client(who).get_balance_allowance(
            BalanceAllowanceParams(asset_type=AssetType.COLLATERAL,
                                   signature_type=sig_type))
        return float(r.get("balance", 0) or 0) / 1e6
    except Exception:
        return None


def log_float_mark_if_measured(registry, mark: dict) -> None:
    """Record an open-exposure float mark when the venue gave real numbers.

    Unrealised and committed come from the venue sweep; naked is registry-
    derived because the venue has no notion of a one-sided live leg. A
    partial sweep skips rather than writing 0.0 for a number the venue never
    reported.
    """
    from core_brain.order_registry import registry_naked_usd
    unrealized = mark.get("unrealized_usd")
    committed = mark.get("committed_usd")
    if unrealized is None or committed is None:
        return
    registry.log_float_mark(
        unrealized_usd=float(unrealized),
        committed_open_usd=float(committed),
        naked_usd=registry_naked_usd(registry),
    )
