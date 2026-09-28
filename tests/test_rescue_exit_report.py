"""Tests for the read-only rescue-exit forensic report (Issue #306).

Every store used here is synthetic, built in a tmp dir with the real
`order_registry` schema. Two invariants matter more than any single assertion:

  * the report NEVER writes -- both stores are byte-compared before/after;
  * the report answers each question or says "unanswerable" -- it never guesses.
"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "rescue_exit_report.py"

sys.path.insert(0, str(REPO))

from core_brain.order_registry import (  # noqa: E402
    CloseRecord,
    FillRecord,
    OrderRegistry,
    OrderRecord,
    QuoteRecord,
)
from core_brain.shadow_exec import ensure_shadow_tables  # noqa: E402

RUN = "run-306-test"


# --- store builders -----------------------------------------------------------


def build_registry(path: Path) -> OrderRegistry:
    return OrderRegistry(path)


def add_pair_orders(
    reg: OrderRegistry,
    *,
    pair_id: str,
    cid: str,
    up_token: str,
    dn_token: str,
    heavy_side: str = "UP",
    heavy_price: float = 0.55,
    heavy_size: float = 10.0,
    heavy_order_ts: int = 1_000,
    fill_ts: int = 1_500,
) -> dict:
    """One heavy leg (filled) + one light leg (empty), the rescue shape."""
    heavy_token = up_token if heavy_side == "UP" else dn_token
    light_token = dn_token if heavy_side == "UP" else up_token
    heavy = OrderRecord(
        id=f"{pair_id}-heavy", condition_id=cid, token_id=heavy_token,
        side="BUY", price=heavy_price, original_size=heavy_size,
        status="filled", posted_ts=heavy_order_ts, last_polled_ts=heavy_order_ts,
        pair_id=pair_id,
    )
    light = OrderRecord(
        id=f"{pair_id}-light", condition_id=cid, token_id=light_token,
        side="BUY", price=0.40, original_size=heavy_size,
        status="filled", posted_ts=heavy_order_ts, last_polled_ts=heavy_order_ts,
        pair_id=pair_id,
    )
    reg.create_order(heavy)
    reg.create_order(light)
    reg.record_fill(FillRecord(
        trade_id=f"{pair_id}-t1", order_uuid=heavy.id, size=heavy_size,
        price=heavy_price, venue_ts=fill_ts, run_id=RUN,
    ))
    reg.log_quote(QuoteRecord(
        ts=heavy_order_ts, condition_id=cid, token_id=heavy_token,
        side=heavy_side, price=heavy_price, size=heavy_size, run_id=RUN,
    ))
    reg.log_quote(QuoteRecord(
        ts=heavy_order_ts, condition_id=cid, token_id=light_token,
        side=("DOWN" if heavy_side == "UP" else "UP"), price=0.40,
        size=heavy_size, run_id=RUN,
    ))
    return {
        "heavy_token": heavy_token, "light_token": light_token,
        "heavy_size": heavy_size, "heavy_price": heavy_price, "fill_ts": fill_ts,
    }


def add_exit_close(
    reg: OrderRegistry,
    *,
    cid: str,
    shares: float,
    sell_price: float,
    heavy_avg: float,
    ts: float,
    method: str = "single_buy_exit",
    reason: str | None = None,
) -> None:
    cost_basis = shares * heavy_avg
    proceeds = shares * sell_price
    reg.log_close(CloseRecord(
        ts=ts, condition_id=cid, method=method, shares=shares,
        up_price=sell_price, dn_price=None,
        cost_basis=cost_basis, proceeds=proceeds, realized_pnl=proceeds - cost_basis,
        forgone_vs_settlement=None, up_cost_removed=cost_basis, dn_cost_removed=0.0,
        run_id=RUN, reason=reason,
    ))


def add_queue_marks(reg_path: Path, rows: list[dict]) -> None:
    ensure_shadow_tables(str(reg_path))
    conn = sqlite3.connect(reg_path)
    try:
        conn.executemany(
            """
            INSERT INTO queue_marks
                (ts, condition_id, market_slug, token_id, price, level_size,
                 traded, run_id, best_bid, best_bid_size)
            VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
            """,
            [
                (r["ts"], r["condition_id"], "mkt", r["token_id"], r.get("price", 0.0),
                 100.0, RUN, r.get("best_bid"), r.get("best_bid_size"))
                for r in rows
            ],
        )
        conn.commit()
    finally:
        conn.close()


def add_merged_pair(reg: OrderRegistry, *, cid: str, up_token: str, dn_token: str,
                    price: float = 0.48) -> None:
    reg.create_order(OrderRecord(
        id=f"{cid}-m-up", condition_id=cid, token_id=up_token, side="BUY",
        price=price, original_size=10.0, status="filled", posted_ts=500,
        last_polled_ts=500, pair_id=f"{cid}-merge",
    ))
    reg.create_order(OrderRecord(
        id=f"{cid}-m-dn", condition_id=cid, token_id=dn_token, side="BUY",
        price=1.0 - price - 0.01, original_size=10.0, status="filled",
        posted_ts=500, last_polled_ts=500, pair_id=f"{cid}-merge",
    ))
    reg.record_fill(FillRecord(
        trade_id=f"{cid}-mt1", order_uuid=f"{cid}-m-up", size=10.0,
        price=price, venue_ts=600, run_id=RUN,
    ))
    reg.record_fill(FillRecord(
        trade_id=f"{cid}-mt2", order_uuid=f"{cid}-m-dn", size=10.0,
        price=1.0 - price - 0.01, venue_ts=610, run_id=RUN,
    ))
    reg.log_close(CloseRecord(
        ts=700, condition_id=cid, method="merge", shares=10.0,
        cost_basis=10.0 * (price + 1.0 - price - 0.01), proceeds=10.0,
        realized_pnl=10.0 - 10.0 * (price + 1.0 - price - 0.01),
        forgone_vs_settlement=None, run_id=RUN,
    ))


def run_report(reg_path: Path, extra: list[str] | None = None) -> str:
    cmd = [sys.executable, str(SCRIPT), "--registry", str(reg_path)] + (extra or [])
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO)
    assert proc.returncode == 0, f"report failed:\n{proc.stdout}\n{proc.stderr}"
    return proc.stdout


def file_digest(path: Path) -> bytes:
    return path.read_bytes()


# --- tests --------------------------------------------------------------------


class TestLossRanking:
    def test_exits_ranked_worst_first_with_pnl_share(self, tmp_path):
        reg = build_registry(tmp_path / "reg.db")
        e1 = add_pair_orders(reg, pair_id="p1", cid="c1", up_token="u1", dn_token="d1",
                             heavy_price=0.60)
        add_exit_close(reg, cid="c1", shares=10.0, sell_price=0.30,
                       heavy_avg=e1["heavy_price"], ts=2000.0)
        e2 = add_pair_orders(reg, pair_id="p2", cid="c2", up_token="u2", dn_token="d2",
                             heavy_price=0.50)
        add_exit_close(reg, cid="c2", shares=10.0, sell_price=0.45,
                       heavy_avg=e2["heavy_price"], ts=3000.0)
        reg_path = tmp_path / "reg.db"

        out = run_report(reg_path)

        # p1 lost 10*(0.60-0.30)=$3.00, p2 lost 10*(0.50-0.45)=$0.50.
        lines = [ln for ln in out.splitlines() if "single_buy_exit" in ln]
        assert len(lines) == 2
        first, second = lines[0], lines[1]
        assert "p1" in first and "p2" in second  # worst first
        pnl_line = [ln for ln in out.splitlines() if "loss share=85.7%" in ln]
        assert pnl_line and "$3.00" in pnl_line[0]  # worst-first row carries its numbers

    def test_fill_to_exit_seconds_reported(self, tmp_path):
        reg = build_registry(tmp_path / "reg.db")
        # Fill at t=10000 s, close at t=10060 s -> 60 s fill-to-exit.
        e = add_pair_orders(reg, pair_id="p1", cid="c1", up_token="u1", dn_token="d1",
                            fill_ts=10_000_000)
        add_exit_close(reg, cid="c1", shares=10.0, sell_price=0.30,
                       heavy_avg=e["heavy_price"], ts=10_060.0)
        out = run_report(tmp_path / "reg.db")
        assert "fill_to_exit=60.0s" in out

    def test_shadow_settlement_leg_aged_out_flagged(self, tmp_path):
        reg = build_registry(tmp_path / "reg.db")
        # Fill at t=10,000,000 ms (10,000 s); close 2000 s later -> aged out.
        add_pair_orders(reg, pair_id="p1", cid="c1", up_token="u1", dn_token="d1",
                        fill_ts=10_000_000)
        add_exit_close(reg, cid="c1", shares=10.0, sell_price=1.0,
                       heavy_avg=0.55, ts=12_000.0, method="shadow_settlement")
        out = run_report(tmp_path / "reg.db")
        assert "shadow_settlement" in out
        assert "AGED OUT" in out

    def test_no_rescue_closes_clean_report(self, tmp_path):
        reg = build_registry(tmp_path / "reg.db")
        add_merged_pair(reg, cid="cm", up_token="um", dn_token="dm")
        out = run_report(tmp_path / "reg.db")
        assert "single_buy_exit" not in out
        assert "0" in out


class TestStoreUntouched:
    def test_report_never_writes_either_store(self, tmp_path):
        reg_path = tmp_path / "reg.db"
        reg = build_registry(reg_path)
        e = add_pair_orders(reg, pair_id="p1", cid="c1", up_token="u1", dn_token="d1")
        add_exit_close(reg, cid="c1", shares=10.0, sell_price=0.30,
                       heavy_avg=e["heavy_price"], ts=2000.0)
        tape_path = tmp_path / "tape.db"
        _build_tape(tape_path, [{"ts": 1900.0, "condition_id": "c1",
                                 "best_bid_down": 0.40}])
        before_reg, before_tape = file_digest(reg_path), file_digest(tape_path)

        run_report(reg_path, ["--booktape", str(tape_path)])

        assert file_digest(reg_path) == before_reg
        assert file_digest(tape_path) == before_tape


def _build_tape(path: Path, rows: list[dict]) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS book_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                run_id TEXT,
                condition_id TEXT NOT NULL,
                market_slug TEXT,
                tick REAL,
                best_bid_up REAL, best_ask_up REAL,
                best_bid_down REAL, best_ask_down REAL,
                mid_up REAL, mid_down REAL,
                mid_sum REAL,
                touch_pair_cost REAL,
                touch_size_up REAL, touch_size_down REAL
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO book_samples (ts, run_id, condition_id, best_bid_up,
                best_bid_down, best_ask_up, best_ask_down)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (r["ts"], RUN, r["condition_id"], r.get("best_bid_up"),
                 r.get("best_bid_down"), r.get("best_ask_up"),
                 r.get("best_ask_down"))
                for r in rows
            ],
        )
        conn.commit()
    finally:
        conn.close()


class TestQuestion2Classifier:
    def test_late_trigger_when_bids_stayed_above_sold_price(self, tmp_path):
        reg_path = tmp_path / "reg.db"
        reg = build_registry(reg_path)
        e = add_pair_orders(reg, pair_id="p1", cid="c1", up_token="u1", dn_token="d1",
                            heavy_price=0.55, fill_ts=10_000)
        # Sold at 0.30. Threshold = max(0.045, 10% of 0.55=0.055) -> 0.495.
        # Bids: 0.55 (t=1s) -> 0.50 (t=10s, first crossing, above sold price)
        # -> 0.45 (t=20s, below sold price 0.30? no, 0.45>0.30)
        # Simpler: bids stay above 0.30 well past the threshold crossing.
        add_queue_marks(reg_path, [
            {"ts": 10_001.0, "condition_id": "c1", "token_id": "u1", "best_bid": 0.55},
            {"ts": 10_010.0, "condition_id": "c1", "token_id": "u1", "best_bid": 0.52},
            {"ts": 10_020.0, "condition_id": "c1", "token_id": "u1", "best_bid": 0.50},
            {"ts": 10_030.0, "condition_id": "c1", "token_id": "u1", "best_bid": 0.48},
            {"ts": 10_040.0, "condition_id": "c1", "token_id": "u1", "best_bid": 0.46},
        ])
        add_exit_close(reg, cid="c1", shares=10.0, sell_price=0.30,
                       heavy_avg=e["heavy_price"], ts=10_050.0)

        out = run_report(reg_path)
        assert "late_trigger" in out

    def test_gapped_when_first_crossing_is_already_at_sold_price(self, tmp_path):
        reg_path = tmp_path / "reg.db"
        reg = build_registry(reg_path)
        e = add_pair_orders(reg, pair_id="p1", cid="c1", up_token="u1", dn_token="d1",
                            heavy_price=0.55, fill_ts=10_000_000)
        # First post-fill crossing sample already sits at the sold price:
        # the bid gapped through the exit level before the trigger fired.
        add_queue_marks(reg_path, [
            {"ts": 10_001.0, "condition_id": "c1", "token_id": "u1", "best_bid": 0.55},
            {"ts": 10_010.0, "condition_id": "c1", "token_id": "u1", "best_bid": 0.30},
        ])
        add_exit_close(reg, cid="c1", shares=10.0, sell_price=0.30,
                       heavy_avg=e["heavy_price"], ts=10_020.0)

        out = run_report(reg_path)
        assert "gapped" in out

    def test_unresolved_when_no_samples_between_fill_and_exit(self, tmp_path):
        reg_path = tmp_path / "reg.db"
        reg = build_registry(reg_path)
        e = add_pair_orders(reg, pair_id="p1", cid="c1", up_token="u1", dn_token="d1",
                            heavy_price=0.55, fill_ts=10_000)
        # All samples BEFORE the fill.
        add_queue_marks(reg_path, [
            {"ts": 9_000.0, "condition_id": "c1", "token_id": "u1", "best_bid": 0.55},
        ])
        add_exit_close(reg, cid="c1", shares=10.0, sell_price=0.30,
                       heavy_avg=e["heavy_price"], ts=10_020.0)

        out = run_report(reg_path)
        assert "unresolved" in out

    def test_unresolved_when_best_bid_is_null(self, tmp_path):
        reg_path = tmp_path / "reg.db"
        reg = build_registry(reg_path)
        e = add_pair_orders(reg, pair_id="p1", cid="c1", up_token="u1", dn_token="d1",
                            heavy_price=0.55, fill_ts=10_000)
        add_queue_marks(reg_path, [
            {"ts": 10_010.0, "condition_id": "c1", "token_id": "u1", "best_bid": None},
        ])
        add_exit_close(reg, cid="c1", shares=10.0, sell_price=0.30,
                       heavy_avg=e["heavy_price"], ts=10_020.0)

        out = run_report(reg_path)
        assert "unresolved" in out

    def test_recorded_reason_preferred_over_reconstruction(self, tmp_path):
        reg_path = tmp_path / "reg.db"
        reg = build_registry(reg_path)
        e = add_pair_orders(reg, pair_id="p1", cid="c1", up_token="u1", dn_token="d1",
                            heavy_price=0.55, fill_ts=10_000)
        add_queue_marks(reg_path, [
            # Samples that would classify as late_trigger...
            {"ts": 10_010.0, "condition_id": "c1", "token_id": "u1", "best_bid": 0.55},
        ])
        # ...but the close carries a recorded reason, which must win.
        add_exit_close(reg, cid="c1", shares=10.0, sell_price=0.30,
                       heavy_avg=e["heavy_price"], ts=10_020.0,
                       reason="grace_expired")

        out = run_report(reg_path)
        assert "grace_expired" in out
        assert "late_trigger" not in out


class TestQuestion1Grace:
    def test_unanswerable_without_book_tape(self, tmp_path):
        reg = build_registry(tmp_path / "reg.db")
        e = add_pair_orders(reg, pair_id="p1", cid="c1", up_token="u1", dn_token="d1")
        add_exit_close(reg, cid="c1", shares=10.0, sell_price=0.30,
                       heavy_avg=e["heavy_price"], ts=2000.0)
        out = run_report(tmp_path / "reg.db")
        assert "unanswerable" in out.lower()

    def test_upper_bound_when_opposite_bid_reaches_quote_price(self, tmp_path):
        reg_path = tmp_path / "reg.db"
        reg = build_registry(reg_path)
        e = add_pair_orders(reg, pair_id="p1", cid="c1", up_token="u1", dn_token="d1",
                            fill_ts=10_000)
        add_exit_close(reg, cid="c1", shares=10.0, sell_price=0.30,
                       heavy_avg=e["heavy_price"], ts=10_050.0)
        # Light leg was quoted at 0.40. After the exit (t=1050+), the DOWN bid
        # reaches 0.40 inside the exit window.
        tape_path = tmp_path / "tape.db"
        _build_tape(tape_path, [
            {"ts": 10_060.0, "condition_id": "c1", "best_bid_down": 0.40},
        ])
        out = run_report(reg_path, ["--booktape", str(tape_path)])
        assert "upper bound" in out.lower()
        assert "0.40" in out

    def test_unanswerable_with_tape_but_no_opposite_samples(self, tmp_path):
        reg_path = tmp_path / "reg.db"
        reg = build_registry(reg_path)
        e = add_pair_orders(reg, pair_id="p1", cid="c1", up_token="u1", dn_token="d1",
                            fill_ts=10_000)
        add_exit_close(reg, cid="c1", shares=10.0, sell_price=0.30,
                       heavy_avg=e["heavy_price"], ts=10_050.0)
        tape_path = tmp_path / "tape.db"
        _build_tape(tape_path, [
            {"ts": 10_060.0, "condition_id": "c1", "best_bid_down": None},
        ])
        out = run_report(reg_path, ["--booktape", str(tape_path)])
        assert "unanswerable" in out.lower()


class TestQuestion3Features:
    def test_quote_time_features_compared_with_merged_pairs(self, tmp_path):
        reg = build_registry(tmp_path / "reg.db")
        e = add_pair_orders(reg, pair_id="p1", cid="c1", up_token="u1", dn_token="d1")
        add_exit_close(reg, cid="c1", shares=10.0, sell_price=0.30,
                       heavy_avg=e["heavy_price"], ts=2000.0)
        add_merged_pair(reg, cid="cm", up_token="um", dn_token="dm")
        out = run_report(tmp_path / "reg.db")
        assert "merged" in out.lower()
        assert "n=4" in out or "n=1" in out  # sample-size caveat is stated


# --- CLI contract -------------------------------------------------------------


class TestCli:
    def test_missing_registry_file_fails_cleanly(self, tmp_path):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--registry", str(tmp_path / "nope.db")],
            capture_output=True, text=True, cwd=REPO,
        )
        assert proc.returncode != 0
        assert "not found" in (proc.stderr + proc.stdout).lower()

    def test_top_option_limits_exits(self, tmp_path):
        reg = build_registry(tmp_path / "reg.db")
        e1 = add_pair_orders(reg, pair_id="p1", cid="c1", up_token="u1", dn_token="d1",
                             heavy_price=0.60)
        add_exit_close(reg, cid="c1", shares=10.0, sell_price=0.30,
                       heavy_avg=e1["heavy_price"], ts=2000.0)
        e2 = add_pair_orders(reg, pair_id="p2", cid="c2", up_token="u2", dn_token="d2",
                             heavy_price=0.50)
        add_exit_close(reg, cid="c2", shares=10.0, sell_price=0.45,
                       heavy_avg=e2["heavy_price"], ts=3000.0)
        out = run_report(tmp_path / "reg.db", ["--top", "1"])
        assert "p1" in out and "p2" not in out
