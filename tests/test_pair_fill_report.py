"""The pair-cost trial knob, and the report that reads a rehearsal's fills."""
from __future__ import annotations

import importlib
import os
import sqlite3
from pathlib import Path

import pytest

from core_brain import config as core_config
from core_brain import rehearsal
from scripts import pair_fill_report as rpt


# --- the knob -----------------------------------------------------------------


def test_pair_cost_trial_is_refused_outside_a_rehearsal():
    with pytest.raises(ValueError) as e:
        core_config.resolve_pair_cost_trial("0.995")

    assert "declared itself unable to place an order" in str(e.value)


def test_pair_cost_trial_applies_inside_a_rehearsal():
    rehearsal.declare_rehearsal()

    assert core_config.resolve_pair_cost_trial("0.995") == 0.995


def test_pair_cost_trial_refuses_a_cap_above_the_payout():
    # The instrument pays exactly $1.00, so a cap above it is not a looser
    # risk setting -- it admits pairs that are a booked loss on assembly.
    rehearsal.declare_rehearsal()

    with pytest.raises(ValueError):
        core_config.resolve_pair_cost_trial("1.05")


def test_a_cap_of_exactly_one_dollar_is_allowed():
    # `hard_block` compares with `>=`, so a cap of 1.00 still refuses a
    # $1.0000 pair while permitting everything under it.
    rehearsal.declare_rehearsal()

    assert core_config.resolve_pair_cost_trial("1.0") == 1.0


def test_load_applies_the_trial_cap(monkeypatch):
    rehearsal.declare_rehearsal()
    monkeypatch.setenv("HUNTER_PAIR_COST_CAP", "0.995")
    monkeypatch.delenv("HUNTER_MARKET", raising=False)

    cfg = importlib.reload(core_config).load()

    assert cfg.max_pair_cost == 0.995


def test_load_leaves_the_shipped_cap_alone_when_unset(monkeypatch):
    monkeypatch.delenv("HUNTER_PAIR_COST_CAP", raising=False)
    monkeypatch.delenv("HUNTER_MARKET", raising=False)

    cfg = importlib.reload(core_config).load()

    assert cfg.max_pair_cost == 0.99


def test_the_touch_pair_is_refused_at_the_shipped_cap_and_allowed_at_the_trial():
    # This is the whole reason the knob exists. Run 145 measured the touch pair
    # at exactly $0.99 on every 1c-tick book, and `hard_block` compares `>=`.
    from core_brain import risk

    class _Inv:
        up_shares = 100.0
        down_shares = 0.0
        fills = 0
        cost = 0.0

        def avg(self, side):
            return 0.66 if side == "UP" else 0.0

    # Built through `load()` rather than by constructing MakerConfig directly:
    # a hand-built config would keep this test passing even if the
    # HUNTER_PAIR_COST_CAP wiring were deleted, which is the one thing it is
    # here to protect.
    shipped = core_config.MakerConfig()
    rehearsal.declare_rehearsal()
    os.environ["HUNTER_PAIR_COST_CAP"] = "0.995"
    try:
        trial = importlib.reload(core_config).load()
    finally:
        # Both of these are process-global. Left set, the rehearsal flag makes
        # every later test in the process read as a declared rehearsal, and the
        # reloaded module hands out a different `MakerConfig` than the one
        # already-imported modules closed over -- so the suite's result starts
        # depending on file order. Restored here, not at some later fixture.
        os.environ.pop("HUNTER_PAIR_COST_CAP", None)
        rehearsal.reset_for_test()
        importlib.reload(core_config)
    assert trial.max_pair_cost == 0.995
    assert shipped.max_pair_cost == 0.99
    book = {"best_bid": 0.32, "best_ask": 0.34, "bids": {0.32: 5000.0}}

    # A DOWN bid at 0.33 against an UP average of 0.66 assembles a $0.99 pair.
    assert "max_pair_cost" not in (
        risk.hard_block(trial, _Inv(), "DOWN", 0.33, book, book) or "")
    assert "0.990" in (
        risk.hard_block(shipped, _Inv(), "DOWN", 0.33, book, book) or "")


# --- the report ---------------------------------------------------------------


def _store(tmp_path: Path, quotes, fills=(), closes=()) -> Path:
    db = tmp_path / "shadow.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE quotes (market_slug TEXT, side TEXT, filled REAL)")
    conn.execute("CREATE TABLE fills (size REAL)")
    conn.execute("CREATE TABLE closes (market_slug TEXT, method TEXT,"
                 " shares REAL, cost_basis REAL, proceeds REAL,"
                 " realized_pnl REAL)")
    conn.executemany("INSERT INTO quotes VALUES (?,?,?)", quotes)
    conn.executemany("INSERT INTO fills VALUES (?)", [(f,) for f in fills])
    conn.executemany("INSERT INTO closes VALUES (?,?,?,?,?,?)", closes)
    conn.commit()
    conn.close()
    return db


def test_both_legs_filled_is_counted_only_when_two_sides_filled(tmp_path):
    db = _store(tmp_path, [
        ("both", "UP", 5.0), ("both", "DOWN", 5.0),
        ("one", "UP", 5.0), ("one", "DOWN", 0.0),
        ("none", "UP", 0.0), ("none", "DOWN", 0.0),
    ])

    text = rpt.report(db)

    assert "BOTH legs filled           : 1" in text
    assert "ONE leg filled             : 1" in text
    assert "no leg filled              : 1" in text


def test_a_one_sided_market_is_not_counted_as_two_sided(tmp_path):
    # Quoting one side is not an attempt at a pair, so it must not dilute the
    # denominator the double-maker rate is measured against.
    db = _store(tmp_path, [("solo", "UP", 5.0)])

    text = rpt.report(db)

    assert "markets quoted two-sidedly : 0" in text


def test_fill_sizes_report_what_a_median_fill_earns(tmp_path):
    # A pair earns one tick per share, so the fill size IS the trade size.
    # There is no gas floor under it: merges are gasless (#152).
    db = _store(tmp_path, [("m", "UP", 1.0), ("m", "DOWN", 1.0)],
                fills=[5.0, 6.0, 200.0])

    text = rpt.report(db)

    assert "median 6" in text
    assert "$ 0.0600" in text        # 6 shares x 1c on a 1c-tick book
    assert "$ 0.0060" in text        # 6 shares x 0.1c on a 0.1c-tick book


def test_the_report_charges_no_merge_gas(tmp_path):
    # An earlier version charged $0.01138 per merge -- the median burn of
    # twelve on-chain mergePositions transactions, which was the cost to
    # whoever sent THOSE, not to us. Polymarket's relayer submits our merges
    # from its own address and pays for them.
    db = _store(tmp_path, [("m", "UP", 5.0), ("m", "DOWN", 5.0)], fills=[5.0],
                closes=[("m", "shadow_merge", 5.0, 4.95, 5.0, 0.05)])

    text = rpt.report(db)

    assert "gas" not in text.lower()
    assert not hasattr(rpt, "MERGE_GAS_USD")


def test_report_survives_a_store_with_no_fills(tmp_path):
    db = _store(tmp_path, [("m", "UP", 0.0), ("m", "DOWN", 0.0)])

    text = rpt.report(db)

    assert "no fills recorded" in text


def test_merges_are_reported_at_their_full_value(tmp_path):
    # Gross IS net for a gasless merge, so the report states one number.
    db = _store(tmp_path, [("m", "UP", 5.0), ("m", "DOWN", 5.0)],
                fills=[5.0],
                closes=[("m", "shadow_merge", 5.0, 4.95, 5.0, 0.05)])

    text = rpt.report(db)

    assert "merges : 1" in text
    # The whole merge line, not just the gross figure. `"$ 0.0500" in text`
    # passed on the OLD output too: that version printed the gross value and
    # the gas-adjusted one side by side, so the assertion could not fail on the
    # behaviour it was meant to pin. Anchor the line, then state the absence.
    merge_line = next(l for l in text.splitlines() if "5sh" in l)
    assert merge_line.strip().endswith("$ 0.0500")
    # $0.05 gross minus the retired $0.01138 merge-gas figure.
    assert "0.0386" not in text
    assert "0.01138" not in text


@pytest.fixture(autouse=True)
def _clean_state():
    rehearsal.reset_for_test()
    yield
    rehearsal.reset_for_test()
    importlib.reload(core_config)


def test_report_opens_the_store_read_only(tmp_path):
    # A reporting tool holding a read-write handle on the production registry
    # is one typo away from being the thing that corrupts it.
    db = _store(tmp_path, [("m", "UP", 0.0), ("m", "DOWN", 0.0)])
    conn = rpt.open_read_only(db)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO quotes VALUES ('x','UP',1.0)")
    finally:
        conn.close()


def test_single_leg_exits_are_labelled_mark_to_exit_not_pnl(tmp_path):
    # A lone leg never prints: proceeds is the best-bid mark at the exit.
    # Labelling it `pnl` reads as a fill that never happened.
    db = _store(tmp_path, [("m", "UP", 5.0), ("m", "DOWN", 0.0)],
                fills=[5.0],
                closes=[("m", "single_buy_exit", 5.0, 2.4, 1.75, -0.65)])

    text = rpt.report(db)

    assert "mark-to-exit" in text
    exit_line = next(l for l in text.splitlines() if "5sh" in l)
    assert "mark-to-exit" in exit_line
    assert "pnl $" not in exit_line


def test_report_refuses_a_path_that_does_not_exist(tmp_path):
    # Read-write SQLite would silently CREATE an empty database here, and a
    # mistyped --db would read as a run that recorded nothing.
    with pytest.raises(sqlite3.OperationalError):
        rpt.report(tmp_path / "typo.db")
