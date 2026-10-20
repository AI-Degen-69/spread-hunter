"""An independent read of our own PnL, from the venue's side of the trade.

Every KPI figure this repo reports is derived from `data/orders.db`. That makes
a systematic registry error invisible by construction: every number would agree
with every other number, and all of them would be wrong. The only check worth
having is one that does not read the registry at all.

This replays the wallet's cashflow from the venue's activity feed — every BUY,
SELL, MERGE, SPLIT, REDEEM and venue credit — adds the current mark on what is still
open, and reports the gap against the registry's own figure. Venue credits (rebates,
rewards, referrals) are money the venue actually paid, so the replay keeps them in
their own field, apart from the trading cashflow they are not.

Three things it refuses to do, because each one turns a check into a rubber
stamp:

* **Assume the gap is zero.** The venue's PnL surfaces are pre-fee; a cashflow
  replay is post-fee. The expected gap is `taker fees − maker rebates`
  (`core_brain.venue_fees`), and a reconciliation that ignores it "passes" by
  being wrong twice.
* **Read MERGE and SPLIT newest-first.** Those two types need `sortDirection=ASC`
  or rows go missing with no error — a silent under-count of exactly the events
  this strategy lives on.
* **Present a truncated walk as a total.** The activity feed repeats rather than
  ending past its offset ceiling, so a walk that hits the cap is reported
  incomplete, per type, and the verdict says so.

`cross_check()` takes an optional `maker_rebates` adjustment: the expected gap is
`taker fees − maker rebates`, so a caller that knows what the venue paid can hand
that figure over. The command-line tool does not pass it today — not even with
`--inclusive` — so a CLI run reports the gap without that adjustment.

Read-only. No signer, no writes, nothing that can reach an order.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

from core_brain.venue_fees import DATA_API_BASE, ACTIVITY_PAGE_SIZE, _as_float

# MERGE and SPLIT are read oldest-first. Under the default DESC ordering the
# venue drops rows of these two types silently -- no error, just a short answer.
ASC_TYPES = ("MERGE", "SPLIT")

# Cash in, cash out. A BUY and a SPLIT both spend USDC to acquire tokens; a
# SELL, a MERGE and a REDEEM all return it.
CASH_IN_TYPES = ("SELL", "MERGE", "REDEEM")
CASH_OUT_TYPES = ("BUY", "SPLIT")

# Platform credits (`CREDIT_TYPES`) vs requested feed types (`ACTIVITY_TYPES`).
# The first decides credit versus trading cashflow; the second decides what
# `replay_cashflow()` requests from the venue's `/activity` feed. Every credit
# type is also requested, so the two lists agree — a credit can never go
# missing from the cashflow the replay exists to measure. Credits stay apart
# from trading cashflow so the headline figure is reproducible from trades
# alone; `pnl_inclusive` adds them back.
# Bare `REBATE` is absent on purpose: the documented `/activity` type enum
# (https://docs.polymarket.com/api-reference/core/get-user-activity) lists
# TRADE, SPLIT, MERGE, REDEEM, REWARD, CONVERSION, DEPOSIT, WITHDRAWAL, YIELD,
# MAKER_REBATE, TAKER_REBATE and REFERRAL_REWARD — no bare `REBATE`. An
# unexpected bare `REBATE` row is therefore unreadable, the replay is
# incomplete and the verdict is `UNJUDGED`; the row is never silently dropped.
CREDIT_TYPES = ("MAKER_REBATE", "REWARD", "REFERRAL_REWARD", "CONVERSION")

ACTIVITY_TYPES = ("TRADE", "MERGE", "SPLIT", "REDEEM", "REWARD",
                  "MAKER_REBATE", "REFERRAL_REWARD", "CONVERSION")

# Past this many rows the feed repeats instead of ending.
ACTIVITY_ROW_CAP = 4_000

_UA = {"User-Agent": "spread-hunter"}


@dataclass(frozen=True)
class Replay:
    """The venue-side reading, and how much of it we actually got."""

    trading_cashflow: float = 0.0
    credits: float = 0.0
    open_value: Optional[float] = None
    rows: int = 0
    unreadable_rows: int = 0
    incomplete_types: tuple[str, ...] = field(default_factory=tuple)

    @property
    def complete(self) -> bool:
        """Every type walked to the end, and every row read.

        A row the replay could not price is a row missing from the cashflow, so
        it makes the total short in exactly the way a truncated walk does.
        Counting it as complete would let a verdict rest on a figure that is
        known to be missing money.
        """
        return not self.incomplete_types and self.unreadable_rows == 0

    @property
    def pnl(self) -> Optional[float]:
        """Trading PnL: cashflow plus what is still open, credits excluded.

        None when the open positions could not be valued -- an unvalued book is
        not an empty one, and treating it as zero would report every open pair
        as a total loss.
        """
        if self.open_value is None:
            return None
        return self.trading_cashflow + self.open_value

    @property
    def pnl_inclusive(self) -> Optional[float]:
        base = self.pnl
        return None if base is None else base + self.credits

    def as_dict(self) -> dict[str, Any]:
        return {
            "trading_cashflow": round(self.trading_cashflow, 6),
            "credits": round(self.credits, 6),
            "open_value": None if self.open_value is None else round(self.open_value, 6),
            "pnl": None if self.pnl is None else round(self.pnl, 6),
            "pnl_inclusive": (None if self.pnl_inclusive is None
                              else round(self.pnl_inclusive, 6)),
            "rows": self.rows,
            "unreadable_rows": self.unreadable_rows,
            "complete": self.complete,
            "incomplete_types": list(self.incomplete_types),
        }


@dataclass(frozen=True)
class CrossCheck:
    """Registry against venue, and whether the difference is explained."""

    registry_pnl: Optional[float]
    venue_pnl: Optional[float]
    expected_gap: Optional[float]
    tolerance: float
    complete: bool

    @property
    def gap(self) -> Optional[float]:
        if self.registry_pnl is None or self.venue_pnl is None:
            return None
        return self.registry_pnl - self.venue_pnl

    @property
    def residual(self) -> Optional[float]:
        """What the gap leaves over once fees and rebates are accounted for."""
        gap = self.gap
        if gap is None or self.expected_gap is None:
            return None
        return gap - self.expected_gap

    @property
    def explained(self) -> Optional[bool]:
        """True/False when it can be judged, None when it cannot.

        An incomplete walk is never "explained": a short venue total can match
        the registry by coincidence, and calling that agreement is the failure
        this whole module exists to avoid.
        """
        residual = self.residual
        if residual is None or not self.complete:
            return None
        return abs(residual) <= self.tolerance

    def as_dict(self) -> dict[str, Any]:
        return {
            "registry_pnl": self.registry_pnl,
            "venue_pnl": self.venue_pnl,
            "gap": self.gap,
            "expected_gap": self.expected_gap,
            "residual": self.residual,
            "tolerance": self.tolerance,
            "complete": self.complete,
            "explained": self.explained,
        }


def _cash_delta(row: Any) -> Optional[float]:
    """Signed USDC this activity row moved, or None if it cannot be read."""
    if not isinstance(row, dict):
        return None
    usdc = _as_float(row.get("usdcSize"))
    if usdc is None:
        usdc = _as_float(row.get("usdcAmount"))
    if usdc is None:
        size = _as_float(row.get("size"))
        price = _as_float(row.get("price"))
        if size is None or price is None:
            return None
        usdc = size * price

    kind = str(row.get("type") or "").strip().upper()
    side = str(row.get("side") or "").strip().upper()
    if kind == "TRADE" or not kind:
        if side == "BUY":
            return -abs(usdc)
        if side == "SELL":
            return abs(usdc)
        return None
    if kind in CASH_IN_TYPES:
        return abs(usdc)
    if kind in CASH_OUT_TYPES:
        return -abs(usdc)
    if kind in CREDIT_TYPES:
        return abs(usdc)
    return None


def replay_rows(rows: Iterable[Any]) -> tuple[float, float, int, int]:
    """(trading cashflow, credits, rows seen, rows that could not be read)."""
    trading = 0.0
    credits = 0.0
    seen = 0
    unreadable = 0
    for row in rows:
        seen += 1
        delta = _cash_delta(row)
        if delta is None:
            unreadable += 1
            continue
        kind = str(row.get("type") or "").strip().upper() if isinstance(row, dict) else ""
        if kind in CREDIT_TYPES:
            credits += delta
        else:
            trading += delta
    return trading, credits, seen, unreadable


def _get_json(path: str, params: dict[str, Any], timeout: float) -> Any:
    url = f"{DATA_API_BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _walk_type(funder: str, activity_type: str, timeout: float, row_cap: int,
               fetch: Optional[Callable[[str, int], Any]]) -> tuple[list, bool]:
    """Every row of one activity type, and whether the walk got all of them."""
    reader = fetch if fetch is not None else (
        lambda kind, offset: _get_json(
            "/activity",
            {"user": funder, "type": kind, "limit": ACTIVITY_PAGE_SIZE,
             "offset": offset,
             # Oldest-first for the two types the venue truncates otherwise.
             **({"sortDirection": "ASC"} if kind in ASC_TYPES else {})},
            timeout))

    rows: list = []
    offset = 0
    while True:
        try:
            page = reader(activity_type, offset)
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
            return rows, False
        if not isinstance(page, list):
            return rows, False
        if not page:
            return rows, True

        remaining = row_cap - offset
        if remaining <= 0:
            return rows, False
        counted = page[:remaining] if len(page) > remaining else page
        rows.extend(counted)
        offset += len(counted)
        if len(counted) < len(page):
            return rows, False
        if len(page) < ACTIVITY_PAGE_SIZE:
            return rows, True
        if offset >= row_cap:
            return rows, False


def replay_cashflow(funder: str, timeout: float = 15.0,
                    row_cap: int = ACTIVITY_ROW_CAP,
                    types: Iterable[str] = ACTIVITY_TYPES,
                    fetch: Optional[Callable[[str, int], Any]] = None,
                    open_value_fn: Optional[Callable[[], Optional[float]]] = None
                    ) -> Optional[Replay]:
    """Replay this wallet's cashflow from the venue's activity feed."""
    if not funder:
        return None

    trading = 0.0
    credits = 0.0
    seen = 0
    unreadable = 0
    incomplete: list[str] = []
    any_read = False

    for activity_type in types:
        rows, complete = _walk_type(funder, activity_type, timeout, row_cap, fetch)
        if rows:
            any_read = True
        if not complete:
            incomplete.append(activity_type)
        t, c, s, u = replay_rows(rows)
        trading += t
        credits += c
        seen += s
        unreadable += u

    if not any_read and incomplete:
        # Nothing came back and the walks failed: that is an unreachable feed,
        # not a wallet that never traded.
        return None

    if open_value_fn is None:
        from core_brain.account import fetch_positions_value
        open_value_fn = lambda: fetch_positions_value(funder, timeout)
    open_value = open_value_fn()

    return Replay(
        trading_cashflow=trading,
        credits=credits,
        open_value=open_value,
        rows=seen,
        unreadable_rows=unreadable,
        incomplete_types=tuple(incomplete),
    )


def cross_check(registry_pnl: Optional[float], replay: Optional[Replay],
                taker_fees: Optional[float] = None,
                maker_rebates: Optional[float] = None,
                tolerance: float = 0.50,
                inclusive: bool = False,
                fees_complete: bool = True) -> CrossCheck:
    """Compare the registry's PnL against the venue-side replay.

    `expected_gap` is `taker fees − maker rebates`: the registry reports what we
    booked, the replay reports what the wallet actually netted, and fees are the
    documented difference. Without a fee figure the gap can be reported but not
    judged, and `explained` stays None rather than guessing.
    """
    venue_pnl = None
    complete = False
    if replay is not None:
        venue_pnl = replay.pnl_inclusive if inclusive else replay.pnl
        complete = replay.complete and venue_pnl is not None

    expected = None
    if taker_fees is not None:
        expected = taker_fees - (maker_rebates or 0.0)

    return CrossCheck(
        registry_pnl=registry_pnl,
        venue_pnl=venue_pnl,
        expected_gap=expected,
        tolerance=tolerance,
        # A fee total taken from a truncated walk is a floor, so the expected
        # gap built on it is a floor too, and a verdict resting on it would be
        # arithmetic on a number known to be short.
        complete=complete and fees_complete,
    )


def format_report(check: CrossCheck, replay: Optional[Replay]) -> str:
    """The block an operator reads. States what was not measured, first."""
    lines: list[str] = []
    if replay is None:
        lines.append("venue replay: UNREAD -- the activity feed could not be "
                     "read. This is not $0.00; nothing was measured.")
    elif not replay.complete:
        lines.append("venue replay: INCOMPLETE -- truncated on "
                     f"{', '.join(replay.incomplete_types)}. The total below is "
                     "a floor, and the verdict is withheld.")
    if replay is not None and replay.open_value is None:
        lines.append("open positions: UNVALUED -- an unvalued book is not an "
                     "empty one, so no venue PnL is reported.")

    reg = "--" if check.registry_pnl is None else f"${check.registry_pnl:,.4f}"
    ven = "--" if check.venue_pnl is None else f"${check.venue_pnl:,.4f}"
    lines.append(f"registry PnL   {reg}")
    lines.append(f"venue PnL      {ven}")
    if check.gap is not None:
        lines.append(f"gap            ${check.gap:,.4f}")
    if check.expected_gap is not None:
        lines.append(f"expected gap   ${check.expected_gap:,.4f} "
                     f"(taker fees - maker rebates)")
    if check.residual is not None:
        lines.append(f"residual       ${check.residual:,.4f} "
                     f"(tolerance ${check.tolerance:,.2f})")
    verdict = {True: "EXPLAINED", False: "UNEXPLAINED", None: "UNJUDGED"}[check.explained]
    lines.append(f"verdict        {verdict}")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    """Read-only cross-check. Touches no registry table and signs nothing."""
    import argparse
    import os

    parser = argparse.ArgumentParser(
        description="Cross-check registry PnL against a venue-side cashflow replay.")
    parser.add_argument("--funder", default=os.environ.get("POLY_FUNDER", ""))
    parser.add_argument("--db", default=None, help="registry path (default: the live one)")
    parser.add_argument("--run-id", default="all",
                        help="run to compare, or 'all' (default: all -- the "
                             "wallet's activity is not scoped to one run)")
    parser.add_argument("--tolerance", type=float, default=0.50)
    parser.add_argument("--inclusive", action="store_true",
                        help="count platform credits in the venue figure")
    args = parser.parse_args(argv)

    from core_brain.kpi import report
    from core_brain.venue_fees import lifetime_taker_fees

    registry_pnl = None
    try:
        kpi = report(db_path=args.db, run_id=args.run_id)
        registry_pnl = kpi.get("portfolio", {}).get("total_pnl")
    except Exception as exc:  # noqa: BLE001 - report the failure, do not mask it
        print(f"registry read failed: {exc!r}")

    replay = replay_cashflow(args.funder)
    fees = lifetime_taker_fees(args.funder)
    if fees is not None and not fees.complete:
        print("taker fees: TRUNCATED -- the fee walk stopped at the activity "
              "row cap, so the expected gap below is a floor.")
    check = cross_check(registry_pnl, replay,
                        taker_fees=None if fees is None else fees.fees_usd,
                        tolerance=args.tolerance, inclusive=args.inclusive,
                        fees_complete=fees is not None and fees.complete)
    print(format_report(check, replay))
    return 0 if check.explained else 1


if __name__ == "__main__":
    raise SystemExit(main())
