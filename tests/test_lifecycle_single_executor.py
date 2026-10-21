"""Regression tests for the single-executor lifecycle guarantee.

The venue-mutating single-leg lifecycle (`manage_single_leg_positions`) is
run from two threads in production shadow mode: the rotation sweep thread and
the <=1 Hz risk-guardian thread. Before the fix both pointed at the same
mutation path with no mutual exclusion, so they decided / cancelled / sold the
same pair concurrently -- the double-execution corruption (double-booked
merges, exits priced off a book the other thread already moved).

The guarantee is now two-layered:

1. The rotation/guardian are scoped so only the defense-only lifecycle pass
   runs on the guardian thread (merges / markouts / resolution stay on the
   rotation thread alone) -- see `shadow_run._shadow_lifecycle_pass`.
2. `manage_single_leg_positions` wraps its whole mutation pass in a
   process-wide `lifecycle_mutation_guard`, so even a future mis-wiring that
   hands the full sweep to a second executor degrades to serialised
   one-at-a-time execution instead of overlapping writes.

These tests lock in BOTH: the guard mechanism (this file) and the wiring.
"""

import threading
import time

import pytest


def test_lifecycle_mutation_guard_serialises_concurrent_executors():
    """Two threads entering the guarded region never overlap.

    Fails without the lock: with no mutual exclusion both threads reach the
    instrumented region near-simultaneously (synced by the barrier) and the
    observed peak concurrency is 2. With the guard it is always 1.
    """
    from core_brain.single_buy_saver import lifecycle_mutation_guard

    barrier = threading.Barrier(2)
    inside = 0
    peak = 0
    peak_lock = threading.Lock()

    def worker():
        nonlocal inside, peak
        barrier.wait()  # both threads hit the guard at the same instant
        with lifecycle_mutation_guard():
            with peak_lock:
                inside += 1
                peak = max(peak, inside)
            time.sleep(0.05)  # hold the region long enough to overlap if unlocked
            with peak_lock:
                inside -= 1

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert peak == 1, (
        f"lifecycle mutation region overlapped: peak concurrency was {peak}, "
        "expected 1. Two executors are mutating the same registry at once."
    )


def test_nested_pass_under_guard_does_not_deadlock():
    """A nested acquire (same logical pass, two call depths) must not deadlock.

    This is the real reason the guard is an RLock, not a Lock: the guardian
    (outer) and the rotation sweep (inner) can form one logical pass that
    enters the guarded region twice. A plain Lock deadlocks the inner acquire
    against the outer holder (the SAME pass), so the fix is reentrancy. The
    thread runs the nested acquire with a watchdog: if it never finishes, the
    test fails with a deadlock, which is exactly the regression the RLock
    prevents. Fails without the RLock.
    """
    from core_brain.single_buy_saver import lifecycle_mutation_guard

    done = threading.Event()

    def nested():
        with lifecycle_mutation_guard():
            # Same logical pass enters the guard a second time (inner call).
            with lifecycle_mutation_guard():
                done.set()

    t = threading.Thread(target=nested, daemon=True)
    t.start()
    finished = done.wait(2.0)  # watchdog: 2s is far longer than a no-op acquire
    t.join(timeout=2.0)
    assert finished, (
        "nested lifecycle pass deadlocked -- the mutation guard must be a "
        "reentrant lock so a single logical pass can enter it twice."
    )


def test_hold_lifecycle_lock_reports_contention_only_while_held():
    """`hold_lifecycle_lock` is True only while another holder owns the lock.

    A nested call by the same logical pass re-acquires the RLock, so the pass
    runs end to end; only a SEPARATE owner contends. The probe releases
    immediately, so it never leaves the lock taken.
    """
    from core_brain.single_buy_saver import (
        hold_lifecycle_lock, lifecycle_mutation_guard,
    )

    # Nobody holds it -> probe reports free.
    assert hold_lifecycle_lock() is False

    observed = {}
    with lifecycle_mutation_guard():
        # We hold it -> a probe from a separate acquirer would contend. The
        # probe itself is a non-blocking try-acquire: it succeeds here because
        # the RLock is reentrant and we are the same thread, so it reports
        # free. The contention signal that matters is tested below from a
        # genuinely separate thread.
        observed["same_thread"] = hold_lifecycle_lock()

    def other_thread_probe(result):
        with lifecycle_mutation_guard():
            # A second, independent thread cannot enter while we hold it.
            # Block briefly so the main thread's probe below observes the hold.
            time.sleep(0.05)
        result["done"] = True

    # Main holds; a second thread must block until we release.
    held = threading.Event()
    released = threading.Event()

    def contender():
        with lifecycle_mutation_guard():
            held.set()
            released.wait(1.0)

    t = threading.Thread(target=contender)
    t.start()
    held.wait(1.0)
    try:
        # While the contender holds it, our probe (separate owner) must see
        # contention -> hold_lifecycle_lock() True.
        assert hold_lifecycle_lock() is True
    finally:
        released.set()
        t.join()


def test_manage_single_leg_positions_is_reentrant_under_guard():
    """A nested pass under the guard does not deadlock (RLock, not Lock)."""
    from core_brain.single_buy_saver import (
        lifecycle_mutation_guard, manage_single_leg_positions,
    )

    calls = {"n": 0}

    class _Cfg:
        bot_risk_gate_pairs_rule_on = False
        bot_risk_gate_aged_out_rescue_on = False

    # Both rules disabled -> the pass returns [] immediately, but it still runs
    # inside the guard, so calling it from within a guard we already hold
    # proves reentrancy rather than a deadlock.
    with lifecycle_mutation_guard():
        out = manage_single_leg_positions(None, None, _Cfg(), live=False)
    calls["n"] += 1
    assert out == []
    assert calls["n"] == 1


def test_wake_driven_guardian_and_rotation_pass_serialize_under_guard(monkeypatch):
    """A reactive-wake guardian pass and a rotation pass hitting lifecycle_mutation_guard never overlap (#699)."""
    from unittest.mock import MagicMock
    from core_brain.quotes import Inventory
    from core_brain.single_buy_saver import lifecycle_mutation_guard
    from core_brain.trader_loop import start_risk_guardian

    registry = MagicMock()
    registry.get_all_quotes.return_value = [
        {"condition_id": "0x1", "side": "UP", "token_id": "tok-u", "ts": 1.0},
        {"condition_id": "0x1", "side": "DOWN", "token_id": "tok-d", "ts": 1.0},
    ]
    monkeypatch.setattr(
        "core_brain.risk_guardian.inventory_from_registry",
        lambda **kw: Inventory(up_shares=10.0, down_shares=0.0, up_cost=5.0),
    )

    initial_done = threading.Event()
    barrier = threading.Barrier(2)
    inside = 0
    peak = 0
    peak_lock = threading.Lock()
    call_idx = 0

    def saver(client, reg, cfg, live=False):
        nonlocal call_idx, inside, peak
        call_idx += 1
        if call_idx == 1:
            initial_done.set()
            return []
        barrier.wait(timeout=2.0)
        with lifecycle_mutation_guard():
            with peak_lock:
                inside += 1
                peak = max(peak, inside)
            time.sleep(0.05)
            with peak_lock:
                inside -= 1
        return []

    stop_evt = threading.Event()
    wake_evt = threading.Event()
    guardian_thread = start_risk_guardian(
        client=MagicMock(),
        registry=registry,
        cfg=MagicMock(),
        interval=3600.0,
        live=False,
        single_buy_saver_fn=saver,
        stop_event=stop_evt,
        wake_event=wake_evt,
    )

    def rotation_pass():
        barrier.wait(timeout=2.0)
        with lifecycle_mutation_guard():
            with peak_lock:
                nonlocal inside, peak
                inside += 1
                peak = max(peak, inside)
            time.sleep(0.05)
            with peak_lock:
                inside -= 1

    try:
        assert initial_done.wait(2.0)
        rot_thread = threading.Thread(target=rotation_pass)
        rot_thread.start()
        wake_evt.set()
        rot_thread.join(timeout=2.0)
        assert peak == 1
    finally:
        stop_evt.set()
        wake_evt.set()
        guardian_thread.join(timeout=2.0)

