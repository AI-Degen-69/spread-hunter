"""#541 T1: golden statistics payload on the fixed shadow run (pre-t-switch record).

The golden JSON freezes today's values BEFORE any refactor. Post-freeze changes must
keep every value exact — except the operator-ordered Student's-t switch (T2), which
regenerates this file exactly once with per-stat deltas logged in the findings doc.
"""
from __future__ import annotations

import json
from pathlib import Path

from core_brain.kpi import report as kpi_report

from tests.stat_541_fixture import RUN_ID_541, START_CAPITAL_541, build_fixed_run

GOLDEN = Path(__file__).with_name("test_541_golden_payload.json")

# Top-level scalar statistics pinned alongside the three sections. Pace fields
# (days, fills_per_day, notional_per_day) and bankroll-derived fields (equity,
# bankroll) are deliberately out: wall-clock / host-config dependent, not statistics.
TOP_SCALARS = [
    "markets_quoted", "active_quoting_markets", "markets_filled", "markets_settled",
    "fills", "quotes", "fill_rate", "posted_shares", "filled_shares", "crossed_shares",
    "taker_fees_paid", "median_seconds_to_fill", "median_queue_ahead",
    "spread_capture", "spread_capture_per_share", "avg_edge_cents", "cost",
    "realized_pnl", "wins", "losses", "win_rate", "avg_win", "avg_loss", "roi_on_cost",
]

SECTIONS = ("portfolio", "trade_analytics", "statistical_analytics")


def _norm(value):
    """JSON round-trip: tuples/sets become lists so live and golden compare exactly."""
    return json.loads(json.dumps(value, allow_nan=False))


def stats_slice(payload: dict) -> dict:
    out = {name: _norm(payload[name]) for name in SECTIONS}
    out["scalars"] = {k: _norm(payload[k]) for k in TOP_SCALARS}
    return out


def test_payload_matches_golden(tmp_path):
    db = build_fixed_run(tmp_path / "golden.db")
    payload = kpi_report(db, run_id=RUN_ID_541)
    live = stats_slice(payload)
    if not GOLDEN.exists():
        GOLDEN.write_text(json.dumps(live, indent=2, sort_keys=True), encoding="utf-8")
        return  # bootstrap run: golden written, next run compares
    assert live == json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_curves_dip_below_start_and_unmeasured_close_excluded(tmp_path):
    """Fixture contract: early losses pull both curves under starting capital,
    and the unmeasured close is a close but neither a win nor a loss."""
    db = build_fixed_run(tmp_path / "contract.db")
    payload = kpi_report(db, run_id=RUN_ID_541)
    assert min(p["v"] for p in payload["equity_series"]) < START_CAPITAL_541
    assert min(p["v"] for p in payload["account_series"]) < START_CAPITAL_541
    assert payload["trade_analytics"]["n_closes"] == 8
    assert payload["wins"] + payload["losses"] == 7  # unmeasured row excluded
