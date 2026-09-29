"""A merged pair reads as one finished row (#99).

After a successful merge the two legs no longer exist as separate positions --
the merge consumed them back into $1.00 of USDC. The registry already records
the merge as a `closes` row with method 'merge' (live) or 'shadow_merge'
(shadow); `summarize_state` now derives a per-order display status from it so
the dashboard can collapse the legs into one muted MERGED row instead of
showing two FILLED rows for positions that are gone.

'merged' is a display status only: `orders.status` is CHECK-constrained and
must never carry it.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from core_brain.order_registry import SCHEMA, CloseRecord, OrderRegistry
from core_brain.registry_state import summarize_state

NOW = 1_788_000_000.0
CID = "0xmerged_market"
OTHER_CID = "0xopen_market"


@pytest.fixture
def db(tmp_path) -> Path:
    db_file = tmp_path / "live.db"
    con = sqlite3.connect(str(db_file))
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    return db_file


def _order(db: Path, oid: str, *, cid: str = CID, pair: str = "pair-1",
           token: str = "tok-up", price: float = 0.47, size: float = 5.0,
           status: str = "filled", posted_ts: float = NOW - 300) -> None:
    con = sqlite3.connect(str(db))
    con.execute(
        "INSERT INTO orders (id, order_id, condition_id, token_id, side, price,"
        " original_size, status, posted_ts, last_polled_ts, pair_id)"
        " VALUES (?,?,?,?,'BUY',?,?,?,?,?,?)",
        (oid, oid, cid, token, price, size, status,
         int(posted_ts * 1000), int(NOW * 1000), pair),
    )
    con.commit()
    con.close()


def _merge_close(db: Path, *, cid: str = CID, method: str = "merge") -> None:
    OrderRegistry(db).log_close(CloseRecord(
        ts=NOW - 60, condition_id=cid, market_slug="merged-market",
        method=method, shares=5.0, cost_basis=4.70, proceeds=5.00,
        realized_pnl=0.30, tx_hash="0xmerge",
    ))


def _orders_by_id(state: dict) -> dict:
    return {o["id"]: o for o in state["orders"]}


def test_filled_legs_of_a_merged_pair_get_the_merged_display_status(db):
    # Arrange
    _order(db, "leg-up", token="tok-up", price=0.47, posted_ts=NOW - 300)
    _order(db, "leg-down", token="tok-down", price=0.51, posted_ts=NOW - 200)
    _merge_close(db)

    # Act
    orders = _orders_by_id(summarize_state(db, now=NOW))

    # Assert
    assert orders["leg-up"]["display_status"] == "merged"
    assert orders["leg-down"]["is_merged"] is True


def test_a_shadow_merge_close_marks_the_legs_too(db):
    # Arrange
    _order(db, "leg-up")
    _order(db, "leg-down", token="tok-down")
    _merge_close(db, method="shadow_merge")

    # Act
    orders = _orders_by_id(summarize_state(db, now=NOW))

    # Assert
    assert all(o["is_merged"] for o in orders.values())


def test_open_and_cancelled_legs_are_never_marked_merged(db):
    # Arrange — a resting order in the same market as a merged pair was never
    # part of the position that closed.
    _order(db, "leg-up")
    _order(db, "still-open", token="tok-down", status="open", pair="pair-2")
    _order(db, "gone", token="tok-down", status="cancelled", pair="pair-2")
    _merge_close(db)

    # Act
    orders = _orders_by_id(summarize_state(db, now=NOW))

    # Assert
    assert orders["leg-up"]["is_merged"] is True
    assert orders["still-open"]["is_merged"] is False
    assert orders["gone"]["is_merged"] is False


def test_a_market_with_no_merge_close_is_untouched(db):
    # Arrange
    _order(db, "leg-up", cid=OTHER_CID)
    _order(db, "leg-down", cid=OTHER_CID, token="tok-down")

    # Act
    orders = _orders_by_id(summarize_state(db, now=NOW))

    # Assert
    assert all(o["is_merged"] is False for o in orders.values())
    assert orders["leg-up"]["display_status"] == "filled"


def test_a_sell_close_is_not_a_merge(db):
    # Arrange — the pair was sold out, not merged; the legs are still legs.
    _order(db, "leg-up")
    _order(db, "leg-down", token="tok-down")
    _merge_close(db, method="sell")

    # Act
    orders = _orders_by_id(summarize_state(db, now=NOW))

    # Assert
    assert all(o["is_merged"] is False for o in orders.values())


def test_merged_status_is_never_written_to_the_orders_table(db):
    # Arrange
    _order(db, "leg-up")
    _order(db, "leg-down", token="tok-down")
    _merge_close(db)
    summarize_state(db, now=NOW)

    # Act
    con = sqlite3.connect(str(db))
    statuses = {row[0] for row in con.execute("SELECT status FROM orders")}
    con.close()

    # Assert — the CHECK constraint has no 'merged' member and must not need one.
    assert statuses == {"filled"}


# --- the rendered row (#99) ------------------------------------------------
# Driven through node against the real dashboard/static/app.js, so these are
# the rows the page would actually print.

import json
import shutil
import subprocess

HARNESS = Path(__file__).resolve().parent / "js" / "orders_table_harness.cjs"

requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node is not installed on this host")


def _render(orders: list[dict], show_cancelled: bool = False) -> dict:
    payload = {"orders": orders, "fills": [], "showCancelled": show_cancelled}
    out = subprocess.run([shutil.which("node"), str(HARNESS), json.dumps(payload)],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def _leg(oid: str, **overrides) -> dict:
    leg = {
        "id": oid,
        "pair_id": "pair-1",
        "condition_id": CID,
        "token_id": "tok-" + oid,
        "side": "BUY",
        "price": 0.47,
        "original_size": 5.0,
        "size_matched": 5.0,
        "status": "filled",
        "age_sec": 300.0,
        "display_status": "merged",
        "is_merged": True,
    }
    leg.update(overrides)
    return leg


@requires_node
def test_a_merged_pair_renders_as_one_row_with_summed_price_and_size():
    # Arrange
    legs = [_leg("up", price=0.47, original_size=5.0, size_matched=5.0, age_sec=300.0),
            _leg("down", price=0.51, original_size=4.0, size_matched=4.0, age_sec=120.0)]

    # Act
    rendered = _render(legs)

    # Assert — one row, price and size summed, age from the older leg.
    assert rendered["rows"] == 1
    assert rendered["merged_rows"] == 1
    assert "0.9800" in rendered["html"]
    assert ">9<" in rendered["html"]


@requires_node
def test_the_merged_row_uses_the_muted_pill():
    # Arrange / Act
    rendered = _render([_leg("up"), _leg("down", price=0.51)])

    # Assert — the same muted family as `.pill.finished`, not OPEN or FILLED.
    assert 'class="pill finished"' in rendered["html"]
    assert ">MERGED</span>" in rendered["html"]


@requires_node
def test_merged_legs_do_not_count_as_active_orders():
    # Arrange
    orders = [_leg("up"), _leg("down", price=0.51),
              _leg("resting", pair_id="pair-2", status="open",
                   display_status="open", is_merged=False)]

    # Act
    rendered = _render(orders)

    # Assert
    assert rendered["active_count"] == 1


@requires_node
def test_an_unmerged_pair_still_renders_one_row_per_leg():
    # Arrange
    orders = [_leg("up", display_status="filled", is_merged=False),
              _leg("down", price=0.51, display_status="filled", is_merged=False)]

    # Act
    rendered = _render(orders)

    # Assert
    assert rendered["rows"] == 2
    assert rendered["merged_rows"] == 0


@requires_node
def test_a_lone_merged_leg_is_not_collapsed():
    # Arrange — one merged leg and one still resting: there is no pair to sum.
    orders = [_leg("up"),
              _leg("down", price=0.51, status="open",
                   display_status="open", is_merged=False)]

    # Act
    rendered = _render(orders)

    # Assert
    assert rendered["rows"] == 2
    assert rendered["merged_rows"] == 1
