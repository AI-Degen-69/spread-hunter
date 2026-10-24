"""Execution-seam tests (#805, second slice).

`core_brain.order_execution.cancel_all_orders` is the one seam behind every
cancel-all: the Order Manager verb and the Dashboard server endpoints route
through it. The venue is injected, never created here, so the seam returns a
result instead of printing or exiting — each adapter renders the outcome its
own way. Cancelling twice is safe: an empty book still answers success.
"""
from __future__ import annotations

from core_brain.order_execution import CancelAllResult, cancel_all_orders


def test_dry_run_never_touches_the_venue():
    calls = []

    result = cancel_all_orders(client_fn=lambda: calls.append(1), live=False)

    assert calls == []
    assert isinstance(result, CancelAllResult)
    assert result.ok is False
    assert result.dry_run is True


def test_live_cancel_returns_the_venue_response():
    response = {"canceled": ["ord-1", "ord-2"]}

    result = cancel_all_orders(client_fn=lambda: _FakeClient(response), live=True)

    assert result.ok is True
    assert result.dry_run is False
    assert result.response == response
    assert result.refusal is None


def test_venue_failure_becomes_a_refusal_not_an_exit():
    def boom():
        raise RuntimeError("Unauthorized")

    result = cancel_all_orders(client_fn=boom, live=True)

    assert result.ok is False
    assert "Unauthorized" in (result.refusal or "")


def test_repeated_cancel_stays_ok():
    client = _FakeClient({"canceled": []})

    first = cancel_all_orders(client_fn=lambda: client, live=True)
    second = cancel_all_orders(client_fn=lambda: client, live=True)

    assert first.ok and second.ok
    assert client.calls == 2


class _FakeClient:
    def __init__(self, response):
        self._response = response
        self.calls = 0

    def cancel_all(self):
        self.calls += 1
        return self._response


# ── Dashboard server adapters (third slice) ───────────────────────────────────
# Both machine-state endpoints adapt the same seam instead of reaching through
# the CLI verb: the verb prints to stdout and raises SystemExit, which is the
# CLI's rendering, not the server's. These fail until the endpoints call
# `core_brain.order_execution.cancel_all_orders` directly.

import sqlite3 as _sqlite3

import pytest as _pytest


@_pytest.fixture
def _server_client(tmp_path, monkeypatch):
    import dashboard.server as dash_mod
    from dashboard.server import app, set_db_override
    from fastapi.testclient import TestClient

    db_file = tmp_path / "live.db"
    from core_brain.order_registry import SCHEMA
    con = _sqlite3.connect(str(db_file))
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    monkeypatch.setattr(dash_mod, "LIVE_ROOT", tmp_path)
    (tmp_path / "runtime").mkdir(parents=True, exist_ok=True)
    set_db_override(db_file)
    try:
        yield TestClient(app)
    finally:
        set_db_override(None)


def _server_control():
    from dashboard.server import CONTROL_TOKEN
    return {"X-Control-Token": CONTROL_TOKEN}


def test_server_cancel_all_adapts_the_seam_not_the_cli_verb(_server_client, monkeypatch):
    import core_brain.order_execution as seam
    from core_brain.order_execution import CancelAllResult

    seen = {}

    def fake_cancel_all_orders(*, client_fn, live):
        seen["live"] = live
        seen["client_fn"] = client_fn
        return CancelAllResult(ok=True, dry_run=False, response={"canceled": []})

    monkeypatch.setattr(seam, "cancel_all_orders", fake_cancel_all_orders)

    def no_cli_verb(*args, **kwargs):
        raise AssertionError("server reached through the CLI verb")

    monkeypatch.setattr("core_brain.order_manager.cancel_all", no_cli_verb)

    res = _server_client.post("/api/system/cancel-all", headers=_server_control())
    assert res.status_code == 200
    assert res.json() == {"ok": True, "message": "All open orders cancelled on the venue."}
    assert seen["live"] is True
    assert callable(seen["client_fn"])


def test_server_cancel_all_refusal_is_a_502_with_the_venue_refusal(_server_client, monkeypatch):
    import core_brain.order_execution as seam
    from core_brain.order_execution import CancelAllResult

    monkeypatch.setattr(
        seam, "cancel_all_orders",
        lambda *, client_fn, live: CancelAllResult(ok=False, dry_run=False, refusal="Unauthorized"),
    )

    res = _server_client.post("/api/system/cancel-all", headers=_server_control())
    assert res.status_code == 502
    assert res.json() == {
        "ok": False,
        # One prefix and the venue's own words -- not the CLI verb's
        # "CANCEL-ALL REFUSED: venue rejected cancel-all:" wrapper re-prefixed.
        "message": "Venue rejected cancel-all: Unauthorized",
    }


def test_server_reset_cancels_through_the_seam(_server_client, monkeypatch):
    import core_brain.order_execution as seam
    from core_brain.order_execution import CancelAllResult

    monkeypatch.setattr(
        seam, "cancel_all_orders",
        lambda *, client_fn, live: CancelAllResult(ok=True, dry_run=False, response={"canceled": []}),
    )

    def no_cli_verb(*args, **kwargs):
        raise AssertionError("server reached through the CLI verb")

    monkeypatch.setattr("core_brain.order_manager.cancel_all", no_cli_verb)

    res = _server_client.post("/api/system/reset", headers=_server_control())
    assert res.status_code == 200
    assert res.json()["ok"] is True
    assert "venue: all open orders cancelled" in res.json()["steps"]


def test_server_reset_survives_a_venue_refusal(_server_client, monkeypatch):
    import core_brain.order_execution as seam
    from core_brain.order_execution import CancelAllResult

    monkeypatch.setattr(
        seam, "cancel_all_orders",
        lambda *, client_fn, live: CancelAllResult(ok=False, dry_run=False, refusal="boom"),
    )

    res = _server_client.post("/api/system/reset", headers=_server_control())
    assert res.status_code == 200
    assert res.json()["ok"] is True
    assert "venue: cancel-all skipped (boom)" in res.json()["steps"]


def test_client_construction_failure_is_a_refusal_not_an_exit():
    # This repo's venue client signals missing credentials with SystemExit,
    # which is not an Exception subclass. The seam maps it like any other
    # venue failure so adapters never see a raw exit.
    from core_brain.order_execution import cancel_all_orders

    def no_key():
        raise SystemExit("POLY_PRIVATE_KEY not set")

    result = cancel_all_orders(client_fn=no_key, live=True)

    assert result.ok is False
    assert "POLY_PRIVATE_KEY" in (result.refusal or "")
