"""`/api/state` ships a recent slice of cancelled orders, not a run's worth.

A requote cycle cancels both legs every few seconds, so a day-long run
accumulates thousands. They were the bulk of a 14.78 MB payload the page asked
for every two seconds; a body that size fails to gzip ("Response content longer
than Content-Length"), the browser gets truncated JSON, and the dashboard
renders empty. The page only shows them inside one expanded market, behind a
"Show N cancelled orders" toggle, so a recent slice per market is all it can
display.
"""

import pytest

import core_brain.registry_state as registry_state
import dashboard.server as server


def _order(cid: str, status: str, ts: int) -> dict:
    return {"id": f"{cid}-{status}-{ts}", "condition_id": cid,
            "status": status, "last_polled_ts": ts}


@pytest.fixture(autouse=True)
def clean_snapshot_cache(monkeypatch):
    monkeypatch.setattr(server, "_snapshots", {}, raising=False)
    monkeypatch.setattr(server, "_snapshot_builders", {}, raising=False)


def test_cancelled_orders_are_capped_per_market():
    # Arrange
    cap = server.CANCELLED_ORDERS_PER_MARKET
    state = {"orders": [_order("0xaaa", "cancelled", i) for i in range(cap + 50)]
                       + [_order("0xbbb", "cancelled", i) for i in range(cap + 50)]}

    # Act
    trimmed = server._trim_cancelled_orders(state)

    # Assert
    assert len(trimmed["orders"]) == cap * 2
    assert trimmed["cancelled_orders_total"] == (cap + 50) * 2


def test_the_newest_cancelled_orders_are_the_ones_kept():
    # Arrange
    cap = server.CANCELLED_ORDERS_PER_MARKET
    state = {"orders": [_order("0xaaa", "cancelled", i) for i in range(cap + 50)]}

    # Act
    kept = server._trim_cancelled_orders(state)["orders"]

    # Assert
    newest = set(range(50, cap + 50))
    assert {o["last_polled_ts"] for o in kept} == newest


def test_live_orders_are_never_trimmed():
    # Arrange — far more resting orders than the cancelled cap.
    cap = server.CANCELLED_ORDERS_PER_MARKET
    live = [_order("0xaaa", "open", i) for i in range(cap + 50)]
    live += [_order("0xaaa", "filled", i) for i in range(cap + 50)]
    state = {"orders": live + [_order("0xaaa", "cancelled", i) for i in range(5)]}

    # Act
    kept = server._trim_cancelled_orders(state)["orders"]

    # Assert
    assert sum(1 for o in kept if o["status"] == "open") == cap + 50
    assert sum(1 for o in kept if o["status"] == "filled") == cap + 50
    assert sum(1 for o in kept if o["status"] == "cancelled") == 5


def test_the_state_endpoint_trims_what_it_serves(monkeypatch, tmp_path):
    # Arrange
    cap = server.CANCELLED_ORDERS_PER_MARKET
    monkeypatch.setattr(server, "_ACTIVE_DB_OVERRIDE", tmp_path / "orders.db")
    monkeypatch.setattr(
        registry_state, "summarize_state",
        lambda db: {"orders": [_order("0xaaa", "cancelled", i) for i in range(cap + 500)]})

    # Act
    import json
    payload = json.loads(bytes(server.get_state().body))

    # Assert
    assert len(payload["orders"]) == cap
    assert payload["cancelled_orders_total"] == cap + 500
