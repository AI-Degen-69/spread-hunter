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
