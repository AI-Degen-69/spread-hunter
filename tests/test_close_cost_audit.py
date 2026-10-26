"""Regression tests for the #798 close-cost-basis audit.

The audit must be TRUE AND GREEN ON ITS OWN: the violations it reports in the
real run databases disappear only once #795 merges, so these tests never
depend on that fix. Each test seeds a synthetic registry through the real
writers (`create_order` / `record_fill` / `log_close`) with the moe shape --
fills spending $9.70 and rescue closes removing $16.40 -- and asserts the
audit flags it.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from core_brain.close_methods import RESCUE_METHODS
from core_brain.order_registry import CloseRecord, FillRecord, OrderRecord, OrderRegistry
from scripts.audit_close_cost_basis import audit_db, run

NOW_MS = 1_787_000_000_000


def _order(registry: OrderRegistry, oid: str, venue_id: str, cid: str,
           token: str, price: float, size: float, pair: str) -> None:
    registry.create_order(OrderRecord(
        id=oid, order_id=venue_id, condition_id=cid, token_id=token,
        side="BUY", price=price, original_size=size, status="filled",
        posted_ts=NOW_MS, last_polled_ts=NOW_MS, pair_id=pair,
    ))


def _close(registry: OrderRegistry, cid: str, method: str, cost: float,
           slug: str = "test-market") -> None:
    registry.log_close(CloseRecord(
        ts=NOW_MS / 1000.0, condition_id=cid, market_slug=slug, method=method,
        shares=1.0, cost_basis=cost, proceeds=0.0, realized_pnl=-cost,
    ))


def _seed_moe_shape(registry: OrderRegistry) -> None:
    """The moe condition (#798): fills spent 9.70, rescue closes removed 16.40."""
    cid = "0x7e6de9ad46moe"
    _order(registry, "uuid-up", "venue-up", cid, "tok-up", 0.62, 10.0, "p1")
    _order(registry, "uuid-dn", "venue-dn", cid, "tok-dn", 0.50, 7.0, "p1")
    registry.record_fill(FillRecord("tr1", "uuid-up", 10.0, 0.62, NOW_MS))
    registry.record_fill(FillRecord("tr2", "uuid-dn", 7.0, 0.50, NOW_MS))
    _close(registry, cid, "single_buy_exit", 8.20)
    _close(registry, cid, "single_buy_exit", 8.20)
    # Decoys that must stay out of BOTH sides: a merge close and a settlement
    # close on the same condition carry cost basis but resolve shares the
    # fills paid for, so they are not part of this invariant.
    _close(registry, cid, "shadow_merge", 3.00)
    _close(registry, cid, "shadow_settlement", 2.00)


def _seed_healthy_condition(registry: OrderRegistry) -> None:
    """A condition whose rescue cost exactly equals its fills' spend."""
    cid = "0xok"
    _order(registry, "uuid-ok", "venue-ok", cid, "tok-ok", 0.50, 8.7, "p2")
    registry.record_fill(FillRecord("tr3", "uuid-ok", 8.7, 0.50, NOW_MS))
    _close(registry, cid, "single_buy_exit", 4.35)
    # A merge close WITH cost basis on an otherwise exact-equality condition:
    # if the audit wrongly counted merge closes this would flip to a
    # violation, so the OK here proves the merge bucket stays out.
    _close(registry, cid, "shadow_merge", 2.00)


@pytest.fixture
def moe_db(tmp_path: Path) -> Path:
    db = tmp_path / "moe.db"
    registry = OrderRegistry(db)
    _seed_moe_shape(registry)
    _seed_healthy_condition(registry)
    # The registry connection stays open and in WAL mode, so hand the tests a
    # clean single-file snapshot: the audit under test then owns the only
    # reader, and the read-only test proves IT creates nothing.
    snap = tmp_path / "moe_audit.db"
    src = sqlite3.connect(db)
    src.execute("VACUUM INTO ?", (str(snap),))
    src.close()
    return snap


def test_moe_shape_is_flagged_with_exact_excess(moe_db: Path) -> None:
    result = audit_db(moe_db)

    flagged = {r["condition_id"]: r for r in result["violations"]}
    assert set(flagged) == {"0x7e6de9ad46moe"}
    moe = flagged["0x7e6de9ad46moe"]
    # Exact excess pins the join: a wrong key (orders.order_id) would drop
    # every fill, make spent 0 and inflate the excess to 16.40.
    assert round(moe["excess"], 2) == 6.70
    assert round(moe["spent"], 2) == 9.70
    assert round(moe["rescue_cost_basis"], 2) == 16.40
    # Decoys stayed out of the cost side and the healthy condition stayed OK.
    ok = {r["condition_id"]: r for r in result["rows"] if not r["violation"]}
    assert round(ok["0xok"]["spent"], 2) == 4.35
    assert round(ok["0xok"]["rescue_cost_basis"], 2) == 4.35
    assert not result["join_suspicious"]


def test_run_reports_violation_lines_and_exit_one(moe_db: Path, capsys) -> None:
    assert run(moe_db) == 1
    out = capsys.readouterr().out
    # The spent figure is on the line so a zero (the join trap) is visible.
    assert "spent=$9.70" in out
    assert "rescue_cost_basis=$16.40" in out
    assert "excess=+6.70" in out
    assert "VIOLATION" in out
    assert "1 violation(s)" in out


def test_clean_database_exits_zero(tmp_path: Path) -> None:
    db = tmp_path / "clean.db"
    registry = OrderRegistry(db)
    _seed_healthy_condition(registry)
    assert run(db) == 0


def _seed_join_trap(tmp_path: Path) -> Path:
    """A fill keyed on the venue id joins nothing; the audit must refuse clean."""
    db = tmp_path / "trap.db"
    registry = OrderRegistry(db)
    cid = "0xtrap"
    _order(registry, "uuid-1", "venue-1", cid, "tok-1", 0.50, 5.0, "p3")
    _close(registry, cid, "single_buy_exit", 3.00)
    # The trap, seeded raw: record_fill refuses a fill whose order_uuid does
    # not reference orders.id (the FK fires -- by design), so a mis-keyed
    # ledger can only be reproduced below the writers, with FKs off.
    con = sqlite3.connect(db)
    con.execute("PRAGMA foreign_keys=OFF")
    con.execute(
        "INSERT INTO fills (trade_id, order_uuid, size, price, recorded_ts)"
        " VALUES ('tr-x', 'venue-1', 5.0, 0.50, ?)",
        (NOW_MS,),
    )
    con.commit()
    con.close()

    assert run(db) == 2  # join suspicion outranks the violation
    result = audit_db(db)
    assert result["join_suspicious"] is True
    # The violation is still reported alongside the suspicion.
    assert [r["condition_id"] for r in result["violations"]] == [cid]
    assert round(result["violations"][0]["spent"], 2) == 0.00


def test_audit_is_read_only(moe_db: Path) -> None:
    # The seeding connection left WAL artifacts for the ORIGINAL db; the
    # audit target is a separate snapshot file, so the audit must leave ITS
    # directory untouched and its bytes unchanged.
    before = hashlib.sha256(moe_db.read_bytes()).hexdigest()
    before_files = sorted(p.name for p in moe_db.parent.iterdir())
    assert run(moe_db) == 1
    after = hashlib.sha256(moe_db.read_bytes()).hexdigest()
    assert before == after
    assert sorted(p.name for p in moe_db.parent.iterdir()) == before_files


def test_rescue_methods_match_the_code_taxonomy() -> None:
    """The audit's close-side filter is the code's own set, not a re-spelling."""
    assert RESCUE_METHODS == ("single_buy_exit", "orphan_profit_exit", "naked_exit", "ladder_exit")
