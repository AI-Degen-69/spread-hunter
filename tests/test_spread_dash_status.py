"""Spread dashboard market-status derivation (PR #22 review fix).

A market holding BOTH paired shares and a naked one-sided leg must be
reported as mixed -- a "Paired (holding)" read hides the naked inventory,
the only part of the position that can pay $0 at resolution.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.spread_dash import _market_status  # noqa: E402


def test_status_reports_error_first():
    assert _market_status(
        {"err": "unfunded: below 2.00%/day floor", "paired": 5, "naked_sh": 3}
    ) == "unfunded: below 2.00%/day floor"


def test_status_reports_mixed_paired_and_naked():
    assert _market_status({"paired": 5, "naked_sh": 3}) == \
        "Paired + one side filled (15m window)"


def test_status_reports_paired_only():
    assert _market_status({"paired": 5, "naked_sh": 0}) == "Paired (holding)"


def test_status_reports_naked_only():
    assert _market_status({"paired": 0, "naked_sh": 3}) == \
        "One side filled (15m window)"


def test_status_reports_resting_quotes():
    assert _market_status({"paired": 0, "naked_sh": 0, "quotes": [1]}) == \
        "Orders resting"


def test_status_falls_back_to_close_merge_then_inactive():
    assert _market_status({"close_why": "profit take"}) == "profit take"
    assert _market_status({"merge_why": "merge"}) == "merge"
    assert _market_status({}) == "Inactive"
