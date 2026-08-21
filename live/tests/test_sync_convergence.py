"""The dashboard's Sync (⟳) writes `venue_sync` closes from Polymarket's
closed-positions history. Those rows carry no leg encoding -- both legs write
`up_price` -- so `inventory_from_registry` and the KPI `by_market` block
ignored them, and the board kept showing the phantom positions (the $1.12 and
$1.0105 pairs from production) long after the account had closed them.

The fix: a `venue_sync` close means the venue reports the condition's position
as CLOSED, so every fill that predates the latest such close is retired.
Fills after the close are new exposure and survive -- the same timestamp rule
the auto-pairs pass's skip already uses.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from engine.kpi import report as kpi_report
from engine.order_registry import (
    CloseRecord, FillRecord, OrderRecord, OrderRegistry, QuoteRecord,
    inventory_from_registry,
)

TOK_UP = "tok-sync-up"
TOK_DN = "tok-sync-dn"
COND = "0xcond-sync"
BASE_MS = 1_000_000_000  # ms epoch, well before the close timestamps below


@pytest.fixture
def registry(tmp_path: Path) -> OrderRegistry:
    return OrderRegistry(db_path=tmp_path / "live.db")


def _seed_order(registry: OrderRegistry, token: str, price: float,
                size: float, order_id: str, ts_ms: int) -> str:
    order_uuid = str(uuid.uuid4())
    registry.create_order(OrderRecord(
        id=order_uuid, order_id=order_id, condition_id=COND, token_id=token,
        side="BUY", price=price, original_size=size, status="filled",
        posted_ts=ts_ms, last_polled_ts=ts_ms, pair_id=f"pair-{order_id}",
        max_pair_cost_at_post=0.995,
    ))
    registry.record_fill(FillRecord(
        trade_id=f"trade-{order_id}", order_uuid=order_uuid, size=size,
        price=price, venue_ts=ts_ms,
    ))
    return order_uuid


def _seed_quotes(registry: OrderRegistry, ts_s: float) -> None:
    registry.log_quote(QuoteRecord(
        ts=ts_s, condition_id=COND, token_id=TOK_UP, side="UP",
        price=0.92, size=6.0,
    ))
    registry.log_quote(QuoteRecord(
        ts=ts_s, condition_id=COND, token_id=TOK_DN, side="DOWN",
        price=0.20, size=6.0,
    ))


def _venue_sync_close(registry: OrderRegistry, ts_s: float, shares: float,
                      price: float) -> None:
    """One closed leg, exactly as the sync writes it (both legs use up_price)."""
    registry.log_close(CloseRecord(
        ts=ts_s, condition_id=COND, method="venue_sync", shares=shares,
        up_price=price, dn_price=None, cost_basis=None, proceeds=None,
        realized_pnl=0.0, tx_hash=f"sync-{ts_s}-{price}", run_id="venue_sync",
    ))


def test_venue_sync_retires_phantom_inventory(registry: OrderRegistry):
    """The $1.12 production shape: UP 6 @ 0.92 + DOWN 6 @ 0.20, then the sync.

    After a venue_sync close the inventory must read zero -- the account no
    longer holds the pair -- not the phantom 1.1200 the board kept showing.
    """
    _seed_order(registry, TOK_UP, 0.92, 6.0, "up", BASE_MS)
    _seed_order(registry, TOK_DN, 0.20, 6.0, "dn", BASE_MS + 1_000)
    _seed_quotes(registry, BASE_MS / 1000.0)
    # The sync runs after both legs closed: both rows, seconds timestamps.
    _venue_sync_close(registry, BASE_MS / 1000.0 + 120.0, 6.0, 0.92)
    _venue_sync_close(registry, BASE_MS / 1000.0 + 121.0, 6.0, 0.20)

    inv = inventory_from_registry(COND, TOK_UP, TOK_DN,
                                  db_path=registry.db_path)
    assert inv.up_shares == pytest.approx(0.0)
    assert inv.down_shares == pytest.approx(0.0)
    assert inv.up_cost == pytest.approx(0.0)
    assert inv.down_cost == pytest.approx(0.0)
    assert inv.pair_cost() == pytest.approx(0.0)


def test_venue_sync_keeps_fills_after_the_close(registry: OrderRegistry):
    """A fill AFTER the sync close is new exposure and must survive."""
    _seed_order(registry, TOK_UP, 0.92, 6.0, "old", BASE_MS)
    _seed_quotes(registry, BASE_MS / 1000.0)
    close_s = BASE_MS / 1000.0 + 60.0
    _venue_sync_close(registry, close_s, 6.0, 0.92)
    # The fleet re-opened the market after the sync: a fresh fill.
    _seed_order(registry, TOK_DN, 0.30, 4.0, "new", int(close_s * 1000) + 5_000)

    inv = inventory_from_registry(COND, TOK_UP, TOK_DN,
                                  db_path=registry.db_path)
    assert inv.up_shares == pytest.approx(0.0)      # retired
    assert inv.down_shares == pytest.approx(4.0)    # survives
    assert inv.down_cost == pytest.approx(4.0 * 0.30)


def test_venue_sync_converges_kpi_report(registry: OrderRegistry):
    """The dashboard's PAIR COST / UP-DN SHARES must converge after a sync."""
    _seed_order(registry, TOK_UP, 0.92, 6.0, "up", BASE_MS)
    _seed_order(registry, TOK_DN, 0.20, 6.0, "dn", BASE_MS + 1_000)
    _seed_quotes(registry, BASE_MS / 1000.0)
    _venue_sync_close(registry, BASE_MS / 1000.0 + 120.0, 6.0, 0.92)
    _venue_sync_close(registry, BASE_MS / 1000.0 + 121.0, 6.0, 0.20)

    by_mkt = kpi_report(db_path=registry.db_path)["by_market"]
    m = by_mkt[COND]
    assert m["up_sh"] == pytest.approx(0.0)
    assert m["dn_sh"] == pytest.approx(0.0)
    assert m["total_sh"] == pytest.approx(0.0)
    assert m["pair_cost"] is None      # no held pair: no stale $1.12 row


def test_naked_exit_and_venue_sync_together_do_not_go_negative(
        registry: OrderRegistry):
    """Mixed close methods converge to zero, never negative.

    The naked_exit branch subtracts the sold leg explicitly while the
    venue_sync cutoff retires the same fills; the max(0, ...) guards must keep
    the result at zero rather than dipping negative.
    """
    _seed_order(registry, TOK_UP, 0.60, 5.0, "up", BASE_MS)
    _seed_quotes(registry, BASE_MS / 1000.0)
    close_s = BASE_MS / 1000.0 + 30.0
    # The U35 pass exited the UP leg and recorded it leg-encoded...
    registry.log_close(CloseRecord(
        ts=close_s, condition_id=COND, method="naked_exit", shares=5.0,
        up_price=0.55, dn_price=None, cost_basis=3.0, proceeds=2.75,
        realized_pnl=-0.25, up_cost_removed=3.0, dn_cost_removed=0.0,
    ))
    # ...and a later sync also reported the position closed.
    _venue_sync_close(registry, close_s + 10.0, 5.0, 0.55)

    inv = inventory_from_registry(COND, TOK_UP, TOK_DN,
                                  db_path=registry.db_path)
    assert inv.up_shares == pytest.approx(0.0)
    assert inv.down_shares == pytest.approx(0.0)
    assert inv.up_cost == pytest.approx(0.0)
