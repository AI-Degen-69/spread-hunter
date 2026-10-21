"""Fake-port tests for the shared guarded reset (#646).

Each test drives `reset_condition` with injected ports against a fresh
temporary registry. No venue, no network, never the production registry.
"""

import sqlite3

import pytest

from core_brain.lifecycle_reset import (
    LATE_FILL,
    REFUSED,
    RESIDUAL,
    RESET,
    UNAVAILABLE,
    ResetOutcome,
    ResetPorts,
    reset_condition,
)
from core_brain.order_registry import LifecycleStateRecord, OrderRegistry
from core_brain.single_buy_saver import ExposureUnavailable, PairExitRefused

CID = "0xReset"
UP = "tok-up"
DOWN = "tok-down"


class FakePorts:
    """Ports over local variables. Tests mutate the fields mid-proof."""

    def __init__(self):
        self.inventory = (0.0, 0.0)
        self.balances = (0.0, 0.0)
        self.resting: list[str] = []
        self.watermark = (0, 0)
        self.cancelled: list[str] = []
        self.reconcile_calls = 0
        self.on_reconcile = None
        self.verify_readable = True

    def as_ports(self) -> ResetPorts:
        return ResetPorts(
            read_inventory=lambda: (float(self.inventory[0]), float(self.inventory[1])),
            fill_watermark=lambda: self.watermark,
            active_order_ids=lambda: list(self.resting),
            cancel=self._cancel,
            verify_cancelled=self._verify,
            reconcile_fills=self._reconcile,
            read_balances=lambda: (float(self.balances[0]), float(self.balances[1])),
        )

    def _cancel(self, order_ids: list[str]) -> None:
        self.cancelled.extend(order_ids)
        self.resting = [o for o in self.resting if o not in order_ids]

    def _verify(self) -> bool:
        if not self.verify_readable:
            raise ExposureUnavailable("injected unreadable book")
        return not self.resting

    def _reconcile(self) -> None:
        self.reconcile_calls += 1
        if self.on_reconcile is not None:
            self.on_reconcile()


@pytest.fixture
def registry(tmp_path):
    return OrderRegistry(db_path=tmp_path / "reset.db")


def _seed_row(registry: OrderRegistry, state: str = "HARD_STOP") -> None:
    registry.save_lifecycle_state(LifecycleStateRecord(
        pair_id="pair-1", condition_id=CID, state=state,
        reason="seed", updated_ts_ms=1_000, evidence_json="{}"))


def _reset(registry, fake: FakePorts, reason: str = "test") -> ResetOutcome:
    return reset_condition(
        registry, CID, UP, DOWN, fake.as_ports(), reason, now_ms=2_000)


def test_a_flat_market_clears_rows_and_leaves_an_awaiting_mark(registry):
    """#646: proof of flat deletes every lifecycle row for the condition."""
    _seed_row(registry)
    fake = FakePorts()
    fake.resting = ["venue-1"]

    outcome = _reset(registry, fake)

    assert outcome.status == RESET
    assert fake.cancelled == ["venue-1"]
    assert registry.has_lifecycle_for_condition(CID) is False
    mark = registry.requalification_marks()[CID.lower()]
    assert mark.status == "awaiting"


def test_a_residual_balance_resets_nothing(registry):
    """#646: leftover shares keep the market under its manager."""
    _seed_row(registry)
    fake = FakePorts()
    fake.balances = (3.0, 0.0)

    outcome = _reset(registry, fake)

    assert outcome.status == RESIDUAL
    assert outcome.up_residual == 3.0
    assert registry.has_lifecycle_for_condition(CID) is True
    assert registry.requalification_marks() == {}


def test_a_residual_ledger_resets_nothing(registry):
    """#646: the ledger disagreeing with the venue still blocks the reset."""
    _seed_row(registry)
    fake = FakePorts()
    fake.inventory = (0.0, 2.0)

    outcome = _reset(registry, fake)

    assert outcome.status == RESIDUAL
    assert outcome.down_residual == 2.0
    assert registry.has_lifecycle_for_condition(CID) is True


def test_a_late_fill_after_cancel_blocks_the_reset(registry):
    """#646: a fill recorded between cancel and finish is never reset away."""
    _seed_row(registry)
    fake = FakePorts()
    fake.resting = ["venue-1"]

    def _late_fill():
        fake.watermark = (1, 9_999)
    fake.on_reconcile = _late_fill

    outcome = _reset(registry, fake)

    assert outcome.status == LATE_FILL
    assert registry.has_lifecycle_for_condition(CID) is True
    assert registry.requalification_marks() == {}


def test_an_unreadable_balance_is_unavailable_not_flat(registry):
    """#646: a failed venue read never counts as zero."""
    _seed_row(registry)

    def _no_read():
        raise ExposureUnavailable("injected outage")
    ports = FakePorts().as_ports()
    object.__setattr__(ports, "read_balances", _no_read)

    outcome = reset_condition(
        registry, CID, UP, DOWN, ports, "test", now_ms=2_000)

    assert outcome.status == UNAVAILABLE
    assert registry.has_lifecycle_for_condition(CID) is True


def test_an_unverified_cancel_is_refused(registry):
    """#646: orders that survive cancel keep the market managed."""
    _seed_row(registry)
    fake = FakePorts()
    fake.resting = ["venue-1"]

    def _stubborn_cancel(order_ids: list[str]) -> None:
        pass
    ports = fake.as_ports()
    object.__setattr__(ports, "cancel", _stubborn_cancel)

    outcome = reset_condition(
        registry, CID, UP, DOWN, ports, "test", now_ms=2_000)

    assert outcome.status == REFUSED
    assert registry.has_lifecycle_for_condition(CID) is True
    assert registry.requalification_marks() == {}


def test_a_pair_locked_row_with_balances_does_not_reset(registry):
    """#646: lifecycle state alone is never proof of flat."""
    _seed_row(registry, state="PAIR_LOCKED")
    fake = FakePorts()
    fake.balances = (5.0, 5.0)

    outcome = _reset(registry, fake)

    assert outcome.status == RESIDUAL
    assert registry.get_lifecycle_state("pair-1").state == "PAIR_LOCKED"


def test_a_cancel_refusal_abandons_the_entry_block(registry):
    """#646: a refused cancel leaves no resetting block behind."""

    def _refuse(order_ids: list[str]) -> None:
        raise PairExitRefused("injected cancel failure")
    fake = FakePorts()
    fake.resting = ["venue-1"]
    ports = fake.as_ports()
    object.__setattr__(ports, "cancel", _refuse)

    outcome = reset_condition(
        registry, CID, UP, DOWN, ports, "test", now_ms=2_000)

    assert outcome.status == REFUSED
    assert registry.requalification_marks() == {}


def test_a_registry_error_is_unavailable(registry, monkeypatch):
    """#646: an unreadable ledger fails closed, never flat."""
    import contextlib

    @contextlib.contextmanager
    def _broken():
        raise sqlite3.OperationalError("injected")
        yield

    monkeypatch.setattr(registry, "_conn", _broken)
    outcome = _reset(registry, FakePorts())

    assert outcome.status == UNAVAILABLE
