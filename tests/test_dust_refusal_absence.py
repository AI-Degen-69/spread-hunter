"""Dust refusal absence (#761). Station III Task 6.

A 0.1087-share position can never meet the 1.0-share venue minimum, so every
poll's sell attempt refuses and logs -- 5,464 `hard_stop_refusals` rows in
shadow-01, worst pair 2,041 in 68 minutes. The exit must log once per pair
and then hold without another write.

This test FAILS on origin/main (the exit raises `PairExitRefused` on every
call); it passes only with the dust early-check in `exit_single_buy`.
"""

import uuid
from pathlib import Path

from core_brain.order_registry import (
    OrderRegistry, OrderRecord, FillRecord, QuoteRecord,
)
from core_brain import single_buy_saver as lp


DUST_SHARES = 0.1087  # observed dust case vs the 1.0 venue minimum
MAX_PAIR_COST = 0.995
TOK_UP = "tok-up"
TOK_DN = "tok-dn"
COND = "0xcond-dust"


def _dusty_pair(registry: OrderRegistry) -> str:
    pair_id = "pair-dust"
    now = 1_000_000
    heavy = OrderRecord(
        id=str(uuid.uuid4()), order_id="venue-heavy", condition_id=COND,
        token_id=TOK_UP, side="BUY", price=0.60, original_size=DUST_SHARES,
        status="filled", posted_ts=now, last_polled_ts=now, pair_id=pair_id,
        max_pair_cost_at_post=MAX_PAIR_COST,
    )
    registry.create_order(heavy)
    registry.record_fill(FillRecord(
        trade_id="trade-dust", order_uuid=heavy.id, size=DUST_SHARES,
        price=0.60, venue_ts=now,
    ))
    light = OrderRecord(
        id=str(uuid.uuid4()), order_id="venue-light", condition_id=COND,
        token_id=TOK_DN, side="BUY", price=0.38, original_size=DUST_SHARES,
        status="open", posted_ts=now, last_polled_ts=now, pair_id=pair_id,
        max_pair_cost_at_post=MAX_PAIR_COST,
    )
    registry.create_order(light)
    registry.log_quote(QuoteRecord(
        ts=now / 1000.0, condition_id=COND, token_id=TOK_UP, side="UP",
        price=0.60, size=DUST_SHARES,
    ))
    registry.log_quote(QuoteRecord(
        ts=now / 1000.0, condition_id=COND, token_id=TOK_DN, side="DOWN",
        price=0.38, size=DUST_SHARES,
    ))
    return pair_id


class _QuietClient:
    """Any venue call is a failure: dust must never reach the venue."""

    def __init__(self):
        self.calls: list[str] = []

    def get_order_book(self, token_id):
        self.calls.append(f"book:{token_id}")
        raise AssertionError("dust must not read the book")

    def cancel_order(self, payload):
        self.calls.append("cancel")
        raise AssertionError("dust must not cancel")


def test_dust_position_does_not_loop(tmp_path: Path):
    registry = OrderRegistry(db_path=tmp_path / "live.db")
    pair_id = _dusty_pair(registry)
    client = _QuietClient()

    first = lp.exit_single_buy(
        client, registry, pair_id,
        bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST,
        live=True, force=True, reason="hard_stop",
    )
    second = lp.exit_single_buy(
        client, registry, pair_id,
        bot_pos_price_max_pair_cost_usd=MAX_PAIR_COST,
        live=True, force=True, reason="hard_stop",
    )

    assert first["action"] == "dust_hold"
    assert second["action"] == "dust_hold"
    assert client.calls == [], "dust must not touch the venue at all"
    dust_rows = [r for r in registry.get_hard_stop_refusals()
                 if r["outcome"] == "dust_skip"]
    assert len(dust_rows) == 1, "log once per pair, then wait"
