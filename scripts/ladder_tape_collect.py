"""Collect both legs of short-window series tapes for the ladder probe.

The standing tape recorder keeps one token per market; the probe needs both
legs of the same market. This collector pages closed events of one series,
records both CLOB tokens of every usable market, backfills each leg's minute
tape over the market's own window, and stamps the recorded resolution.

Read-only against the venue (public GETs only, no signer), writes its own DB,
and refuses the production registry by name:

    python scripts/ladder_tape_collect.py --series btc-up-or-down-5m \\
        --max-markets 200 --out data/ladder_tape_btc5.db
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Any, Optional

import requests

# Running `python scripts/ladder_tape_collect.py` puts `scripts/` on the path,
# not the repo root, so sibling modules are invisible without this.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core_brain.price_tape import (  # noqa: E402
    CLOB_HOST,
    GAMMA_HOST,
    HTTP_TIMEOUT,
    TapeMarket,
    TapeStore,
    TapeStoreError,
    _new_session,
    parse_history,
)
from scripts.ladder_probe import refuse_tape  # noqa: E402

log = logging.getLogger("ladder_tape_collect")

WINDOW_SEC = 300


class CollectRefused(RuntimeError):
    """The destination is a production store that stays untouched."""


def _market_start_epoch(slug: str) -> Optional[int]:
    """BTC up/down slugs end in the market's start epoch; None otherwise."""
    try:
        epoch = int(slug.rsplit("-", 1)[-1])
    except (ValueError, AttributeError):
        return None
    return epoch if epoch > 1_000_000_000 else None


def _usable_market(market: dict) -> Optional[dict]:
    """Both tokens, a start epoch, and a clean binary resolution — or None."""
    raw = market.get("clobTokenIds")
    if isinstance(raw, str):
        try:
            tokens = json.loads(raw)
        except (ValueError, TypeError):
            return None
    elif isinstance(raw, list):
        tokens = raw
    else:
        return None
    if not isinstance(tokens, list) or len(tokens) != 2:
        return None
    start = _market_start_epoch(str(market.get("slug") or ""))
    if start is None:
        return None
    raw_prices = market.get("outcomePrices")
    if isinstance(raw_prices, str):
        try:
            prices = json.loads(raw_prices)
        except (ValueError, TypeError):
            return None
    elif isinstance(raw_prices, list):
        prices = raw_prices
    else:
        return None
    if not isinstance(prices, list) or len(prices) != 2:
        return None
    try:
        up_final = float(prices[0])
        down_final = float(prices[1])
    except (ValueError, TypeError):
        return None
    if (up_final, down_final) not in ((0.0, 1.0), (1.0, 0.0)):
        return None
    return {"tokens": [str(tokens[0]), str(tokens[1])],
            "condition_id": str(market.get("conditionId") or ""),
            "question": str(market.get("question") or ""),
            "slug": str(market.get("slug") or ""),
            "start": start, "up_wins": up_final == 1.0}


def _closed_events(session: Any, series_slug: str, gamma_host: str):
    """Yield closed event rows of one series, newest first, paged."""
    offset = 0
    while True:
        response = session.get(
            f"{gamma_host}/events",
            params={"series_slug": series_slug, "closed": "true",
                    "order": "closedTime", "ascending": "false",
                    "limit": 50, "offset": offset},
            timeout=HTTP_TIMEOUT,
        )
        response.raise_for_status()
        rows = response.json()
        if not isinstance(rows, list) or not rows:
            return
        yield from rows
        if len(rows) < 50:
            return
        offset += len(rows)


def collect(*, series_slug: str, out_db: str | Path, max_markets: int,
            window_sec: int = WINDOW_SEC, session: Any = None,
            gamma_host: str = GAMMA_HOST,
            clob_host: str = CLOB_HOST) -> dict:
    """Backfill both legs of up to `max_markets` series markets. Pure I/O."""
    refuse_tape(out_db)
    session = session or _new_session()
    store = TapeStore(out_db)
    completed = ticks = skipped = 0
    for event in _closed_events(session, series_slug, gamma_host):
        if completed >= max_markets:
            break
        for row in event.get("markets") or []:
            if completed >= max_markets:
                break
            usable = _usable_market(row)
            if not usable or not usable["condition_id"]:
                skipped += 1
                continue
            legs_ok = True
            for token_id in usable["tokens"]:
                store.record_market(TapeMarket(
                    token_id=token_id,
                    condition_id=usable["condition_id"],
                    question=usable["question"], slug=usable["slug"],
                    volume_24h=0.0))
                try:
                    response = session.get(
                        f"{clob_host}/prices-history",
                        params={"market": token_id,
                                "startTs": usable["start"],
                                "endTs": usable["start"] + window_sec,
                                "fidelity": 1},
                        timeout=HTTP_TIMEOUT,
                    )
                    response.raise_for_status()
                    history = parse_history(response.json())
                    if not history:
                        raise TapeStoreError(
                            f"No tape served for {token_id}.")
                    ticks += store.append_ticks(token_id, history)
                except (requests.RequestException, ValueError,
                        TapeStoreError) as exc:
                    log.warning("tape backfill failed for %s: %s",
                                token_id, exc)
                    legs_ok = False
                else:
                    store.mark_resolved(token_id, usable["up_wins"])
            try:
                with sqlite3.connect(out_db) as conn:
                    updated = conn.execute(
                        "UPDATE markets SET first_seen = ?"
                        " WHERE condition_id = ?",
                        (usable["start"], usable["condition_id"])).rowcount
            except sqlite3.Error as exc:
                log.warning("first_seen fixup failed for %s: %s",
                            usable["condition_id"], exc)
                legs_ok = False
            else:
                legs_ok = legs_ok and updated == 2
            if legs_ok:
                completed += 1
            else:
                skipped += 1
    return {"markets": completed, "ticks": ticks, "skipped": skipped,
            "db": str(out_db)}


def main(argv: Optional[list] = None) -> int:
    """CLI: `python scripts/ladder_tape_collect.py --series X --out Y.db`."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--series", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--max-markets", type=int, default=200)
    parser.add_argument("--window-sec", type=int, default=WINDOW_SEC)
    args = parser.parse_args(argv)
    print(json.dumps(collect(series_slug=args.series, out_db=args.out,
                             max_markets=args.max_markets,
                             window_sec=args.window_sec), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
