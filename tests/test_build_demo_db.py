"""Smoke tests for the demo registry builder.

The builder is the operator's dashboard test fixture: if the registry API
drifts, the demo database silently loses its scenarios. These tests pin
that every scenario still lands, every close still reaches the right fill
path, and the builder's safety refusals hold.
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from core_brain import kpi
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


def test_the_demo_store_measures_an_excursion_and_the_robustness_gates(tmp_path):
    """The robustness card (#516) needs store rows to be checkable by eye.

    A readout that can only ever say "unmeasured" cannot be verified on the screen, so the demo
    records one close on the markout market, timed to leave the earliest recorded marks inside
    the holding window the excursion is measured over.
    """
    build(tmp_path / "demo.db")
    reg = OrderRegistry(tmp_path / "demo.db")

    gates = sa.robustness(reg.get_all_closes(), reg.get_all_markouts())

    mae = gates["mae"]
    # Four trajectories fall inside the seeded window, and the eight later fills are still open
    # rather than being folded in as zero excursions.
    assert mae["measured"] >= 4
    assert mae["open"] >= 1
    assert mae["median_cents"] is not None
    assert mae["median_cents"] > 0.0      # a real adverse move, not a stored zero
    assert mae["no_marks_in_window"] == 0  # the seeded window is wide enough to hold a mark
    # The markout half has rows on this store too, with the ticket's bands on them.
    assert gates["markout"]["intervals"]
    assert all(i["band"] in {"good", "average", "bad"}
               for i in gates["markout"]["intervals"]), gates["markout"]["intervals"]
    # The two close-based gates have a real sample on this store, band and all.
    assert gates["mean_pnl_ci"]["n"] >= 2
    assert gates["mean_pnl_ci"]["band"] in {"good", "average", "bad"}
    assert gates["sqn"]["sqn"] is not None
    assert gates["sqn"]["band"] != "unmeasured"


def test_the_demo_store_measures_the_capital_risk_section(tmp_path):
    """The capital-risk card (#517) reads numbers on the demo store, not `unmeasured`.

    A readout that can only ever say "unmeasured" cannot be verified on the screen. The scenarios
    already carry what these metrics need: closes that are mostly small wins with a few large
    losses (the downside-tail shape the skewness band names), and an account-value curve with one
    deep drawdown that recovers plus two shallower ones.
    """
    build(tmp_path / "demo.db")
    payload = kpi.report(db_path=str(tmp_path / "demo.db"), run_id="all")

    capital = payload["statistical_analytics"]["capital_risk"]
    # #730 added three hard-stop closes to the demo store (15 → 18 closes), so the two
    # close-population statistics move with it. Bands and every drawdown figure below are
    # unchanged: the shape (few large losses) and the account curve are the same scenarios.
    assert capital["pnl_skewness"]["pnl_skewness"] == pytest.approx(-0.963916, abs=1e-6)
    assert capital["pnl_skewness"]["band"] == "bad"
    assert capital["sortino_ratio"]["sortino_ratio"] == pytest.approx(-0.351863, abs=1e-6)
    assert capital["sortino_ratio"]["band"] == "bad"

    recovery = capital["drawdown_recovery"]
    assert recovery["deepest_usd"] == pytest.approx(14.6)
    assert recovery["deepest_pct"] == pytest.approx(11.957412, abs=1e-6)
    assert recovery["deepest_recovered"] is True
    assert recovery["episodes"] == 3
    assert recovery["open"] == 0
    assert recovery["drawdown_recovery_trades"] == {"median": 4.0, "p90": 7.2, "n": 3}
    assert recovery["drawdown_recovery_sec"] == {"median": 4800.0, "p90": 8640.0, "n": 3}
    # The curve the section reads is the one the payload already shipped, untouched.
    assert len(payload["account_series"]) == 45
    assert payload["account_series"][-1]["v"] == pytest.approx(163.4)


def test_every_quote_carries_a_leg_side(tmp_path):
    """The Active Markets tab normalizes `quotes.side` into legs.

    A 'BUY' side is dropped by the renderer's `normalizeLeg`, leaving
    YES PRICE / NO PRICE / EDGE empty -- production writes the leg label,
    so the demo must too.
    """
    build(tmp_path / "demo.db")
    reg = OrderRegistry(tmp_path / "demo.db")
    quotes = reg.get_all_quotes()
    assert quotes
    assert {q["side"] for q in quotes} <= {"UP", "DOWN"}


def test_the_pair_completion_wait_state_exists(tmp_path):
    """One leg filled with the other leg still quoted."""
    build(tmp_path / "demo.db")
    reg = OrderRegistry(tmp_path / "demo.db")
    orders = {o["id"]: o for o in reg.get_all_orders()}
    assert orders["loc-w-up"]["status"] == "filled"
    assert orders["loc-w-dn"]["status"] == "open"
    assert orders["loc-w-up"]["pair_id"] == orders["loc-w-dn"]["pair_id"] == "pair-wait"


def test_the_single_leg_missing_partner_state_exists(tmp_path):
    """One resting leg with no order on the other side."""
    build(tmp_path / "demo.db")
    reg = OrderRegistry(tmp_path / "demo.db")
    resting = [o for o in reg.get_all_orders() if o["pair_id"] == "pair-half"]
    assert len(resting) == 1
    assert resting[0]["status"] == "open"


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


# --- outcome-probability arm on the demo store (#538) -----------------------

_HARNESS = Path(__file__).resolve().parent / "js" / "position_dist_harness.cjs"

requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node is not installed on this host")


@requires_node
def test_the_demo_store_probability_arm_draws_measured_fills(tmp_path):
    """The demo's fixed fill prices must be what the chart arm draws (#538).

    No test verified the expected demo probability arm, so the arm could be
    drawing anything on the fixture the operator checks by eye. Rendered bar
    counts must conserve the fill sample, and the occupied bins must match
    the demo's fixed prices around 43%–52%.
    """
    build(tmp_path / "demo.db")
    payload = kpi.report(db_path=str(tmp_path / "demo.db"), run_id="all")
    bell = payload["statistical_analytics"]["probability_bell"]
    assert bell["samples_count"] >= 1

    out = subprocess.run(
        [shutil.which("node"), str(_HARNESS),
         json.dumps({"statistical_analytics": payload["statistical_analytics"],
                     "metric": "outcome_prob"})],
        capture_output=True, text=True, check=True, encoding="utf-8")
    rendered = json.loads(out.stdout)

    bar_counts = [int(m) for m in re.findall(r": (\d+) fills", rendered["svgText"])]
    assert sum(bar_counts) == bell["samples_count"]
    occupied = [b for b in bell["bins"] if b["empirical_count"]]
    assert occupied
    # The demo's balanced-pair fills sit at fixed prices around 43%–52%;
    # rescue and single-leg scenarios fill elsewhere, so the band must be
    # occupied without being the whole occupied set.
    assert any(0.40 <= b["bin"] <= 0.55 for b in occupied), occupied
