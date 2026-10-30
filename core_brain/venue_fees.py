"""What the venue actually charged us, recovered from its own activity rows.

`kpi.taker_fees_paid` sums a `fee` column we wrote ourselves, so it reports what
this process believed at close time and nothing else. The venue states the fee
implicitly on every trade row: `usdcSize` is **not** `size × price`, and the
residual is the fee — added on a BUY, deducted on a SELL. One public read
recovers it, with no signer, no RPC and no receipt decoding.

That figure is the reconciling term for a venue-side PnL cross-check: the
venue's own PnL surfaces are pre-fee, a cashflow replay is post-fee, and the
gap between them should be `lifetime taker fees − maker rebates`.

Two dates matter and are not ours to assume away: fees did not exist before late
June 2026, and the taker rate stepped 0.03 → 0.05 → 0.07 afterwards. A row from
the free era recovers exactly 0.0, and no rate is ever assumed here — the
residual is measured, never modelled.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterable, Optional

DATA_API_BASE = "https://data-api.polymarket.com"
ACTIVITY_PAGE_SIZE = 500

# The Data API repeats rows rather than ending cleanly past roughly this many;
# a total taken from a truncated walk is understated, and saying so is the whole
# point of the flag on the result.
ACTIVITY_ROW_CAP = 4_000

# Sub-cent noise in the venue's own rounding. A residual under this is rounding,
# not a fee, and reporting it as one would put a fabricated tenth of a cent on
# every free-era trade.
FEE_EPSILON = 0.0005

_UA = {"User-Agent": "spread-hunter"}


@dataclass(frozen=True)
class FeeTotal:
    """Recovered fees over a walk of the activity feed."""

    fees_usd: float
    rows: int
    priced_rows: int
    complete: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "fees_usd": round(self.fees_usd, 6),
            "rows": self.rows,
            "priced_rows": self.priced_rows,
            "complete": self.complete,
        }


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def recover_fee(row: Any) -> Optional[float]:
    """The fee the venue charged on one trade row, or None if unmeasurable.

    `usdcSize` is what actually moved. On a BUY it is `size × price` plus the
    fee; on a SELL it is `size × price` minus the fee. Anything else about the
    row — the side spelling, extra keys, a missing field — resolves to None
    rather than to zero: an unmeasured fee and a zero fee are different claims,
    and only one of them belongs in a reconciliation.
    """
    if not isinstance(row, dict):
        return None
    size = _as_float(row.get("size"))
    price = _as_float(row.get("price"))
    usdc = _as_float(row.get("usdc_size") if row.get("usdc_size") is not None else row.get("usdcSize"))
    if size is None or price is None or usdc is None:
        return None
    if size < 0 or price < 0 or usdc < 0:
        return None

    side = str(row.get("side") or "").strip().upper()
    notional = size * price
    if side == "BUY":
        fee = usdc - notional
    elif side == "SELL":
        fee = notional - usdc
    else:
        # Without a side the residual's sign is unreadable, and guessing it
        # turns a rebate into a charge.
        return None

    if fee < -FEE_EPSILON:
        # The venue does not charge a negative taker fee. A residual materially
        # below zero is a row we are misreading, not a discount. Float noise of
        # a fraction of a cent is not that, and falls through to normalise.
        return None
    return 0.0 if abs(fee) < FEE_EPSILON else fee


def fees_from_rows(rows: Iterable[Any]) -> tuple[float, int, int]:
    """(total fees, rows seen, rows a fee could be recovered from)."""
    total = 0.0
    seen = 0
    priced = 0
    for row in rows:
        seen += 1
        fee = recover_fee(row)
        if fee is None:
            continue
        priced += 1
        total += fee
    return total, seen, priced


def _get_json(path: str, params: dict[str, Any], timeout: float) -> Any:
    url = f"{DATA_API_BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def lifetime_taker_fees(funder: str, timeout: float = 15.0,
                        row_cap: int = ACTIVITY_ROW_CAP,
                        fetch=None) -> Optional[FeeTotal]:
    """Walk this wallet's TRADE activity and total what it was charged.

    Returns None when the first read fails — an empty total from an unreachable
    endpoint would read as "this wallet has paid no fees". `complete` is False
    when the walk stopped at the row cap, so a truncated total is never
    presented as a lifetime figure.
    """
    if not funder:
        return None

    reader = fetch

    total = 0.0
    seen = 0
    priced = 0
    offset = 0
    cursor: Optional[str] = None
    complete = True
    while True:
        try:
            if reader is not None:
                raw_page = reader(offset)
            else:
                params: dict[str, Any] = {
                    "user": funder,
                    "type": "TRADE",
                    "limit": ACTIVITY_PAGE_SIZE,
                }
                if cursor:
                    params["cursor"] = cursor
                raw_page = _get_json("/v2/activity", params, timeout)
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
            if offset == 0:
                return None
            # A page that failed mid-walk truncates the total; report it as
            # incomplete rather than as a lifetime figure.
            complete = False
            break

        if isinstance(raw_page, dict) and "data" in raw_page:
            page = raw_page.get("data")
            pagination = raw_page.get("pagination") or {}
            next_cursor = pagination.get("next_cursor")
            cursor = str(next_cursor) if next_cursor else None
        elif isinstance(raw_page, list):
            page = raw_page
            cursor = None
        else:
            if offset == 0:
                return None
            complete = False
            break

        if not isinstance(page, list):
            if offset == 0:
                return None
            complete = False
            break
        if not page:
            break

        # Trim to the allowance BEFORE summing. Counting a page and then
        # noticing the cap totals rows the cap says not to trust.
        remaining = row_cap - offset
        if remaining <= 0:
            complete = False
            break
        counted = page[:remaining] if len(page) > remaining else page
        if len(counted) < len(page):
            complete = False

        page_total, page_seen, page_priced = fees_from_rows(counted)
        total += page_total
        seen += page_seen
        priced += page_priced
        offset += len(counted)

        if not complete:
            break
        if len(page) < ACTIVITY_PAGE_SIZE:
            break
        if offset >= row_cap:
            complete = False
            break

    return FeeTotal(fees_usd=total, rows=seen, priced_rows=priced, complete=complete)


def format_report(total: Optional[FeeTotal], funder: str) -> str:
    """One block an operator can read without opening the JSON."""
    if total is None:
        return (f"lifetime taker fees for {funder or '(no funder)'}: UNREAD\n"
                f"  The activity feed could not be read. This is not $0.00 -- "
                f"nothing was measured.")
    lines = [
        f"lifetime taker fees for {funder}: ${total.fees_usd:,.4f}",
        f"  trade rows read      {total.rows}",
        f"  rows with a fee read {total.priced_rows}",
    ]
    if total.rows != total.priced_rows:
        lines.append(
            f"  rows unmeasurable    {total.rows - total.priced_rows} "
            f"(counted, never summed)")
    if not total.complete:
        lines.append("  TRUNCATED: the walk stopped at the activity row cap, so "
                     "this is a floor, not a lifetime total.")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    """Read-only: totals what the venue charged this wallet. Signs nothing."""
    import argparse
    import os

    parser = argparse.ArgumentParser(
        description="Recover lifetime taker fees from the venue's activity rows.")
    parser.add_argument("--funder", default=os.environ.get("POLY_FUNDER", ""),
                        help="wallet to total (default: POLY_FUNDER)")
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args(argv)

    total = lifetime_taker_fees(args.funder, timeout=args.timeout)
    print(format_report(total, args.funder))
    return 0 if total is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
