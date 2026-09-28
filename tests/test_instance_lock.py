"""Whole-loop ownership: `instance_lock` slots for the fleet and poll loops.

`reconcile_lock` guards a single reconcile call. These tests pin the slot that
owns the whole loop: a second holder of the SAME role is refused, while the
designed fleet+poll pair on one DB coexists.
"""
from __future__ import annotations

import time

import pytest

from core_brain.order_registry import (
    INSTANCE_LOCK_STALE_MS,
    InstanceInUse,
    OrderRegistry,
    ReconcileInProgress,
)
from core_brain.trader_loop import VenueSeam, run


def _now_ms() -> int:
    return int(time.time() * 1000)


def _reg(tmp_path, name="test_instance.db") -> OrderRegistry:
    return OrderRegistry(db_path=tmp_path / name)


class _FakeMarket:
    def __init__(self, cid="0xabc"):
        self.condition_id = cid
        self.up_token = "tok-up"
        self.down_token = "tok-dn"
        self.market_slug = "fake-market"
        self.tick_size = 0.01
        self.neg_risk = False

    def t_remaining(self, now=None):
        return 14400.0


def _seam(registry) -> VenueSeam:
    return VenueSeam(
        client=object(),
        registry=registry,
        fetch_market=lambda cid: _FakeMarket(cid),
        fetch_books=lambda h, t: {"token_id": t, "bids": {}, "asks": {}},
        decide=lambda *a: ([], "declined"),
        submit_fn=lambda *a, **k: 0,
        cancel_fn=lambda *a, **k: 0,
        reconcile_fn=lambda *a, **k: None,
        sweep_fn=lambda: None,
    )


def test_second_same_role_holder_refused_names_holder_and_age(tmp_path):
    reg = _reg(tmp_path)
    with reg.instance_lock("fleet", _now_ms()) as holder:
        other = _reg(tmp_path)
        with pytest.raises(InstanceInUse) as exc_info:
            with other.instance_lock("fleet", _now_ms()):
                pass
    assert holder in str(exc_info.value)
    assert "held for" in str(exc_info.value)


def test_fleet_and_poll_slots_coexist(tmp_path):
    reg = _reg(tmp_path)
    with reg.instance_lock("fleet", _now_ms()):
        with _reg(tmp_path).instance_lock("poll", _now_ms()):
            pass


def test_slot_released_on_clean_exit(tmp_path):
    reg = _reg(tmp_path)
    with reg.instance_lock("fleet", _now_ms()):
        pass
    with _reg(tmp_path).instance_lock("fleet", _now_ms()):
        pass


def test_slot_released_on_exception(tmp_path):
    reg = _reg(tmp_path)
    with pytest.raises(RuntimeError, match="boom"):
        with reg.instance_lock("fleet", _now_ms()):
            raise RuntimeError("boom")
    with _reg(tmp_path).instance_lock("fleet", _now_ms()):
        pass


def test_stale_holder_adopted_after_ttl(tmp_path):
    reg = _reg(tmp_path)
    reg._write_instance_lock("fleet", "dead-holder", _now_ms() - INSTANCE_LOCK_STALE_MS - 1)
    with _reg(tmp_path).instance_lock("fleet", _now_ms()):
        pass


def test_ttl_boundary_holder_is_adopted(tmp_path):
    reg = _reg(tmp_path)
    reg._write_instance_lock("fleet", "dead-holder", _now_ms() - INSTANCE_LOCK_STALE_MS)
    with _reg(tmp_path).instance_lock("fleet", _now_ms()):
        pass


def test_fresh_holder_is_refused(tmp_path):
    reg = _reg(tmp_path)
    reg._write_instance_lock("fleet", "live-holder", _now_ms())
    with pytest.raises(InstanceInUse):
        with _reg(tmp_path).instance_lock("fleet", _now_ms()):
            pass


def test_heartbeat_refreshes_and_reports_ownership(tmp_path):
    reg = _reg(tmp_path)
    with reg.instance_lock("fleet", _now_ms()) as holder:
        assert reg.refresh_instance_lock("fleet", holder, _now_ms()) is True


def test_heartbeat_after_adoption_returns_false(tmp_path):
    reg = _reg(tmp_path)
    with reg.instance_lock("fleet", _now_ms()) as holder:
        _reg(tmp_path)._write_instance_lock(
            "fleet", "adopter", _now_ms() - INSTANCE_LOCK_STALE_MS - 1
        )
        # Adopter takes the row the moment it reads stale...
        with _reg(tmp_path).instance_lock("fleet", _now_ms()):
            pass
        assert reg.refresh_instance_lock("fleet", holder, _now_ms()) is False


def test_instance_in_use_is_not_reconcile_in_progress():
    assert not issubclass(InstanceInUse, ReconcileInProgress)


def test_run_refuses_when_fleet_slot_held(tmp_path):
    reg = _reg(tmp_path)
    with _reg(tmp_path).instance_lock("fleet", _now_ms()):
        with pytest.raises(InstanceInUse):
            run(_seam(reg), interval=0.0, once=True, live=False,
                markets=[_FakeMarket()], sleep_fn=lambda s: None)


def test_run_adopts_stale_fleet_slot(tmp_path):
    reg = _reg(tmp_path)
    reg._write_instance_lock("fleet", "dead-holder", _now_ms() - INSTANCE_LOCK_STALE_MS - 1)
    results = run(_seam(reg), interval=0.0, once=True, live=False,
                  markets=[_FakeMarket()], sleep_fn=lambda s: None)
    assert results
    # Adopted means owned: the run held the slot and released it on exit.
    with _reg(tmp_path).instance_lock("fleet", _now_ms()):
        pass


def test_run_releases_fleet_slot_on_exit(tmp_path):
    reg = _reg(tmp_path)
    run(_seam(reg), interval=0.0, once=True, live=False,
        markets=[_FakeMarket()], sleep_fn=lambda s: None)
    with _reg(tmp_path).instance_lock("fleet", _now_ms()):
        pass


def test_run_without_registry_needs_no_slot():
    results = run(_seam(None), interval=0.0, once=True, live=False,
                  markets=[_FakeMarket()], sleep_fn=lambda s: None)
    assert results and results[0].status == "DECLINED"


def test_poll_refusal_exits_2(tmp_path, monkeypatch):
    import core_brain.order_manager as om
    from core_brain.order_manager import poll

    monkeypatch.setattr(om, "RUN", tmp_path / "runtime")
    db = tmp_path / "poll_refused.db"
    OrderRegistry(db_path=db)._write_instance_lock("poll", "live-holder", _now_ms())
    with pytest.raises(SystemExit) as exc_info:
        poll(once=True, db_path=db, client=object())
    assert exc_info.value.code == 2


def test_poll_releases_slot_when_cycle_fails(tmp_path, monkeypatch):
    import core_brain.order_manager as om
    from core_brain.order_manager import poll

    monkeypatch.setattr(om, "RUN", tmp_path / "runtime")
    db = tmp_path / "poll_failed.db"
    with pytest.raises(SystemExit):
        poll(once=True, db_path=db, client=object())
    with OrderRegistry(db_path=db).instance_lock("poll", _now_ms()):
        pass


def test_readers_work_while_slot_held(tmp_path):
    reg = _reg(tmp_path)
    with reg.instance_lock("fleet", _now_ms()):
        assert _reg(tmp_path).get_active_orders() == []


def test_main_maps_fleet_refusal_to_exit_2(tmp_path, monkeypatch):
    import core_brain.trader_loop as fleet_mod

    def _refuse(*a, **k):
        raise InstanceInUse("Another writer loop already owns X (holder=h, held for 1 ms).")

    monkeypatch.setattr(fleet_mod, "run", _refuse)
    monkeypatch.setattr(fleet_mod, "_market_specs", lambda *a, **k: [])
    monkeypatch.setattr("core_brain.account.fetch_live_balance", lambda *a, **k: None)
    monkeypatch.delenv("POLY_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("POLY_KEY", raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    rc = fleet_mod.main(["--once", "--no-live", "--db", str(tmp_path / "m.db")])
    assert rc == 2
