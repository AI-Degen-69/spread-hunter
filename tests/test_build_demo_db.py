"""Smoke tests for the demo registry builder.

The builder is the operator's dashboard test fixture: if the registry API
drifts, the demo database silently loses its scenarios. These tests pin
that every scenario still lands, every close still reaches the right fill
path, and the builder's safety refusals hold.
"""
from __future__ import annotations

import sqlite3

import pytest

from core_brain import statistical_analytics as sa
from core_brain.order_registry import DEFAULT_DB_PATH, OrderRegistry
from core_brain.registry_state import summarize_state
from scripts.build_demo_db import DEMO_RUN_ID, DemoRefused, build


def test_the_demo_registry_carries_every_hedge_state(tmp_path):
    """The main page classifies each scenario pair."""
    counts = build(tmp_path / "demo.db")
    assert counts["orders"] >= 20
    state = summarize_state(tmp_path / "demo.db")
    states = {p["hedge_state"] for p in state["pairs"]}
    assert {"RESTING", "NAKED", "BALANCED", "REFUSED", "SETTLED"} <= states
    assert not state["empty"]


def test_every_row_is_tagged_with_the_demo_run_id(tmp_path):
    """A stray demo row is greppable by run id."""
    build(tmp_path / "demo.db")
    reg = OrderRegistry(tmp_path / "demo.db")
    for fill in reg.get_all_fills():
        assert fill["run_id"] == DEMO_RUN_ID


def test_the_closes_cover_every_fill_path(tmp_path):
    """Merges and rescues both reach the fill-path edge.

    Pins the rescue close methods: a rescue recorded as a generic sell
    lands in the `other` bucket and silently drops out of the metric.
    """
    build(tmp_path / "demo.db")
    reg = OrderRegistry(tmp_path / "demo.db")
    edge = sa.fill_path_edge(reg.get_all_closes())
    assert edge["merge_count"] >= 5
    assert edge["rescue_count"] >= 5
    friction = sa.rescue_friction_summary(reg.get_all_closes())
    assert friction["assumed_reference_count"] >= 1


def test_the_ghost_fill_has_no_order_behind_it(tmp_path):
    """The corrupted-store scenario the dashboard must survive.

    `get_all_fills` inner-joins orders, so the ghost is invisible to that
    getter by design; the dashboard's state summary reads fills directly
    and is the read path that must survive it.
    """
    build(tmp_path / "demo.db")
    reg = OrderRegistry(tmp_path / "demo.db")
    with sqlite3.connect(str(tmp_path / "demo.db")) as conn:
        ghost = conn.execute(
            "SELECT order_uuid FROM fills WHERE trade_id = 'trade-ghost'"
        ).fetchall()
    assert ghost
    assert ghost[0][0] not in {o["id"] for o in reg.get_all_orders()}
    # And the dashboard's read path renders it rather than crashing.
    state = summarize_state(tmp_path / "demo.db")
    assert any(f["order_uuid"] == "loc-never-existed" for f in state["fills"])


def test_the_builder_refuses_the_production_registry(tmp_path):
    """Never write the live registry, even with --force."""
    with pytest.raises(DemoRefused):
        build(DEFAULT_DB_PATH, force=True)


def test_the_builder_refuses_to_overwrite_without_force(tmp_path):
    """An existing output is only replaced on an explicit --force."""
    out = tmp_path / "demo.db"
    build(out)
    with pytest.raises(DemoRefused):
        build(out, force=False)
