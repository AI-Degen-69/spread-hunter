"""Visit-seam contract tests (#804, second slice).

The market-visit interface is seven ports: fetch_market, fetch_books,
decide, submit_fn, cancel_fn, reconcile_fn, sweep_fn. Everything else on the
seam is optional and inert when unset. `VenueSeam` owns that contract, so the
live rotation and the rehearsal seam are checked the same way — a seam
missing a required port raises, never silently.
"""
from __future__ import annotations

import pytest

from core_brain.trader_loop import VenueSeam


def _wired(**overrides):
    ports = dict(
        fetch_market=object(),
        fetch_books=object(),
        decide=object(),
        submit_fn=object(),
        cancel_fn=object(),
        reconcile_fn=object(),
        sweep_fn=object(),
    )
    ports.update(overrides)
    return VenueSeam(**ports)


def test_required_ports_are_the_visit_interface():
    assert VenueSeam.required_ports() == (
        "fetch_market",
        "fetch_books",
        "decide",
        "submit_fn",
        "cancel_fn",
        "reconcile_fn",
        "sweep_fn",
    )


def test_wired_seam_passes_ready_check():
    _wired().check_ready()


def test_missing_port_raises_naming_it():
    seam = _wired(decide=None)
    with pytest.raises(TypeError, match="decide"):
        seam.check_ready()


def test_each_missing_port_is_named():
    for port in VenueSeam.required_ports():
        with pytest.raises(TypeError, match=port):
            _wired(**{port: None}).check_ready()


def test_shadow_seam_satisfies_the_same_contract(tmp_path):
    from core_brain.shadow_run import build_shadow_seam

    seam = build_shadow_seam(db_path=str(tmp_path / "shadow.db"))
    assert isinstance(seam, VenueSeam)
    seam.check_ready()
