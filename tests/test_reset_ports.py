"""Adapter tests for the live and shadow reset ports (#646).

Fake venue clients and faked Data API pages. No network, isolated stores,
never the production registry.
"""

from __future__ import annotations

import json
import uuid

import pytest

from core_brain.lifecycle_reset import (
    RESET,
    RESIDUAL,
    UNAVAILABLE,
    reset_condition,
)
from core_brain.order_registry import (
    FillRecord,
    OrderRecord,
    OrderRegistry,
    init_db,
)
from core_brain.single_buy_saver import (
    ExposureUnavailable,
    fetch_token_balances,
    live_reset_ports,
)

CID = "0xPorts"
UP = "tok-up"
DOWN = "tok-dn"
FUNDER = "0xfunder"


class _Resp:
    def __init__(self, payload):
        self._data = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _pages(monkeypatch, *pages):
    import urllib.request

    bodies = list(pages)

    def _fake_urlopen(req, timeout=None):
        assert bodies, "venue paged more than the test staged"
        return _Resp(bodies.pop(0))

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)


def _order(token_id, status="open", venue_id=None, size=10.0):
    return OrderRecord(
        id=str(uuid.uuid4()), order_id=venue_id or f"venue-{token_id}-{status}",
        condition_id=CID, token_id=token_id, side="BUY", price=0.5,
        original_size=size, status=status, posted_ts=1_000,
        last_polled_ts=1_000, pair_id="pair-1",
    )


class TestStrictBalanceRead:
    def test_a_complete_read_returns_both_tokens(self, monkeypatch):
        """#646: exact sizes come back from one page."""
        _pages(monkeypatch, [
            {"asset": UP, "size": "4.0"},
            {"asset": DOWN, "size": 2.5},
        ])
        assert fetch_token_balances(FUNDER, UP, DOWN) == (4.0, 2.5)

    def test_an_absent_token_is_zero_after_a_complete_read(self, monkeypatch):
        """#646: the API omits tokens no longer held; that omission is a zero."""
        _pages(monkeypatch, [{"asset": UP, "size": 1.0}])
        assert fetch_token_balances(FUNDER, UP, DOWN) == (1.0, 0.0)

    def test_a_non_list_payload_is_unavailable(self, monkeypatch):
        """#646: an error envelope never reads as an empty portfolio."""
        _pages(monkeypatch, {"error": "upstream"})
        with pytest.raises(ExposureUnavailable):
            fetch_token_balances(FUNDER, UP, DOWN)

    def test_a_malformed_row_is_unavailable(self, monkeypatch):
        """#646: a row with no token, a bad size, or a wrong shape blocks."""
        for bad in ({"size": 1.0}, {"asset": UP, "size": "many"},
                    ["not", "a", "dict"]):
            _pages(monkeypatch, [bad])
            with pytest.raises(ExposureUnavailable):
                fetch_token_balances(FUNDER, UP, DOWN)

    def test_a_network_failure_is_unavailable(self, monkeypatch):
        """#646: an unreadable endpoint is not a zero balance."""
        import urllib.request

        def _boom(req, timeout=None):
            raise ConnectionError("offline")

        monkeypatch.setattr(urllib.request, "urlopen", _boom)
        with pytest.raises(ExposureUnavailable):
            fetch_token_balances(FUNDER, UP, DOWN)

    def test_pagination_reads_every_page(self, monkeypatch):
        """#646: a truncated first page never stands in for the portfolio."""
        import core_brain.single_buy_saver as saver

        monkeypatch.setattr(saver, "POSITIONS_PAGE_SIZE", 2)
        _pages(monkeypatch,
               [{"asset": "other-1", "size": 1.0}, {"asset": "other-2", "size": 1.0}],
               [{"asset": DOWN, "size": 7.0}])
        assert fetch_token_balances(FUNDER, UP, DOWN) == (0.0, 7.0)


class FakeLiveClient:
    """Venue double: cancel records, open orders and trades are staged."""

    def __init__(self):
        self.creds = object()
        self.open_orders: list[dict] = []
        self.trades: list[dict] = []
        self.cancelled: list[str] = []

    def get_open_orders(self):
        return list(self.open_orders)

    def get_trades(self, params=None):
        return list(self.trades)

    def cancel_order(self, payload):
        oid = getattr(payload, "orderID", payload)
        self.cancelled.append(str(oid))
        self.open_orders = [o for o in self.open_orders
                            if str(o.get("id")) != str(oid)]
        return {"success": True}


class TestLivePorts:
    def test_cancel_goes_through_the_existing_cancel_path(self, tmp_path):
        """#646: the reset cancels resting orders via `_cancel_orders`."""
        reg = OrderRegistry(db_path=tmp_path / "live.db")
        reg.create_order(_order(UP, venue_id="venue-1"))
        client = FakeLiveClient()
        client.open_orders = [{"id": "venue-1"}]

        ports = live_reset_ports(client, FUNDER, reg, CID, UP, DOWN)
        assert ports.active_order_ids() == ["venue-1"]
        ports.cancel(["venue-1"])

        assert client.cancelled == ["venue-1"]
        assert ports.verify_cancelled() is True

    def test_verify_treats_an_unreadable_book_as_still_resting(self, tmp_path):
        """#646: a failed open-orders read refuses, never proves."""
        reg = OrderRegistry(db_path=tmp_path / "live.db")
        reg.create_order(_order(UP, venue_id="venue-1"))
        client = FakeLiveClient()
        client.open_orders = [{"id": "venue-1"}]

        ports = live_reset_ports(client, FUNDER, reg, CID, UP, DOWN)
        assert ports.verify_cancelled() is False

        def _boom():
            raise RuntimeError("CLOB offline")

        client.get_open_orders = _boom
        assert ports.verify_cancelled() is False

    def test_a_flat_market_resets_end_to_end(self, tmp_path, monkeypatch):
        """#646: empty ledger, empty venue, empty book resets."""
        reg = OrderRegistry(db_path=tmp_path / "live.db")
        client = FakeLiveClient()
        _pages(monkeypatch, [])

        outcome = reset_condition(
            reg, CID, UP, DOWN,
            live_reset_ports(client, FUNDER, reg, CID, UP, DOWN),
            "test", now_ms=2_000)

        assert outcome.status == RESET
        assert reg.requalification_marks()[CID.lower()].status == "awaiting"

    def test_a_venue_reconcile_failure_is_unavailable_not_a_crash(
            self, tmp_path, monkeypatch):
        """#646: the port contract translates venue SDK errors, never escapes."""
        import core_brain.order_registry as reg_mod
        reg = OrderRegistry(db_path=tmp_path / "live.db")
        client = FakeLiveClient()
        _pages(monkeypatch, [])

        def _boom(*a, **k):
            raise RuntimeError("CLOB offline")

        monkeypatch.setattr(reg_mod, "reconcile_orders", _boom)
        outcome = reset_condition(
            reg, CID, UP, DOWN,
            live_reset_ports(client, FUNDER, reg, CID, UP, DOWN),
            "test", now_ms=2_000)

        assert outcome.status == UNAVAILABLE
        assert reg.requalification_marks() == {}

    def test_a_venue_balance_blocks_the_reset(self, tmp_path, monkeypatch):
        """#646: shares the ledger forgot still count."""
        reg = OrderRegistry(db_path=tmp_path / "live.db")
        client = FakeLiveClient()
        _pages(monkeypatch, [{"asset": UP, "size": 5.0}])

        outcome = reset_condition(
            reg, CID, UP, DOWN,
            live_reset_ports(client, FUNDER, reg, CID, UP, DOWN),
            "test", now_ms=2_000)

        assert outcome.status == RESIDUAL
        assert reg.has_lifecycle_for_condition(CID) is False
        assert reg.requalification_marks() == {}


class TestShadowPorts:
    def test_a_flat_shadow_market_resets(self, tmp_path):
        """#646: the store's own cancel path clears a shadow position."""
        from core_brain.shadow_exec import (
            ensure_shadow_tables,
            shadow_reset_ports,
        )

        db = tmp_path / "shadow.db"
        init_db(db)
        ensure_shadow_tables(db)
        reg = OrderRegistry(db_path=db)
        reg.create_order(_order(UP, venue_id="sh-1"))

        outcome = reset_condition(
            reg, CID, UP, DOWN,
            shadow_reset_ports(reg, db, CID, UP, DOWN),
            "test", now_ms=2_000)

        assert outcome.status == RESET
        assert reg.requalification_marks()[CID.lower()].status == "awaiting"
        assert reg.get_active_orders() == []

    def test_a_leftover_shadow_leg_stays_managed(self, tmp_path):
        """#646: an unequal shadow merge leaves its excess leg behind."""
        from core_brain.shadow_exec import (
            ensure_shadow_tables,
            shadow_reset_ports,
        )

        db = tmp_path / "shadow.db"
        init_db(db)
        ensure_shadow_tables(db)
        reg = OrderRegistry(db_path=db)
        order = _order(UP, status="filled", venue_id="sh-1")
        reg.create_order(order)
        reg.record_fill(FillRecord(
            trade_id="sh-fill-1", order_uuid=order.id, size=15.0, price=0.4))

        outcome = reset_condition(
            reg, CID, UP, DOWN,
            shadow_reset_ports(reg, db, CID, UP, DOWN),
            "test", now_ms=2_000)

        assert outcome.status == RESIDUAL
        assert outcome.up_residual == 15.0
        assert reg.requalification_marks() == {}
