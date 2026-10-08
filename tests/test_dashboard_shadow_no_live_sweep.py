"""#422: a shadow dashboard must never sweep the live Polymarket account.

`dashboard/server.py` snapshots the venue account value at launch so the live
page opens with real equity, and that snapshot is an `account_sweep`: it reads
collateral, positions and P&L from the venue and records an account mark in the
store the page was pointed at.

A rehearsal launches the same dashboard pointed at its own isolated store, so
the sweep pulled real capital into a rehearsal and contradicted the fixed
starting bankroll it runs under. Only a LIVE store may sweep, and an
unresolvable store fails closed the same way -- no sweep we cannot prove belongs
to the live page.
"""
from __future__ import annotations

import pytest

from core_brain.order_registry import DEFAULT_DB_PATH
from dashboard import server as dash


@pytest.fixture
def sweep_calls(monkeypatch):
    """Record every venue sweep and every persisted snapshot."""
    calls: list[dict] = []

    def fake_sweep(**kwargs):
        calls.append({"swept_into": kwargs.get("db_path")})
        return {"account_value_usd": 5000.0}

    monkeypatch.setattr("core_brain.order_manager.account_sweep", fake_sweep)
    # Never write runtime/processes.json from a test.
    monkeypatch.setattr(dash, "_save_starting_account_value",
                        lambda val: calls.append({"saved": val}))
    return calls


def test_a_shadow_store_never_sweeps_the_live_account(tmp_path, monkeypatch,
                                                     sweep_calls):
    monkeypatch.setattr(dash, "_ACTIVE_DB_OVERRIDE",
                        tmp_path / "01_shadow_24-09.db")

    assert dash._capture_starting_capital() is None
    assert sweep_calls == [], "a rehearsal dashboard read the live account"


def test_the_live_store_still_sweeps_and_reports_equity(monkeypatch,
                                                        sweep_calls):
    """The guard must not disable the snapshot the live page depends on."""
    monkeypatch.setattr(dash, "_ACTIVE_DB_OVERRIDE", DEFAULT_DB_PATH)

    assert dash._capture_starting_capital() == 5000.0
    assert sweep_calls == [
        {"swept_into": str(DEFAULT_DB_PATH)},
        {"saved": 5000.0},
    ]
