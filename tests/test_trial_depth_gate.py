"""Depth-gate trial replay (U32): pure logic over the recorded near-miss log.

The trial is licensed by the near-miss tracker (READY_TO_TRIAL) and staged so
markouts are watched before the permanent bar changes. This module pins the
replay's grading: which recorded depth-rejected markets a trial bar adopts,
the mirage rule re-derived against the trial bar, last-reading-per-market
dedup, and the bar sweep -- all against synthetic logs, no network, no writes.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.trial_depth_gate import _is_trap, trial_depth_report  # noqa: E402


def _green(cid, dm, pot=10.0, marg=3.0, cause="YES: top-3 bid depth",
           db=1000.0, days=3.0, ts=1.0):
    return {
        "cid": cid, "title": f"Market {cid}", "slug": f"slug-{cid}",
        "cause": cause, "reason": f"{cause} ${dm:,.2f} <= ${db:,.2f}",
        "source": "rewards", "marg_pct_day": marg, "pot_day": pot,
        "competition": 100.0, "trap": False, "threshold_pct": 2.0,
        "volume_24h": 1_000_000.0, "days": days,
        "depth_measured": dm, "depth_bar": db, "ts": ts,
    }


def _write_log(tmp_path, lines):
    p = tmp_path / "near_misses.jsonl"
    p.write_text("\n".join(json.dumps(l) for l in lines) + "\n",
                 encoding="utf-8")
    return p


def test_graduates_are_credible_depth_rejects_above_the_trial_bar(tmp_path):
    """A dm=$800 clears the $750 trial; dm=$600 is a near-miss at $750;
    dm=$300 is a mirage (under half the trial bar); dm=$2000 clears every
    bar. Only the depth-cause greens are graded at all."""
    log = _write_log(tmp_path, [
        {"ts": 100.0, "greens": [
            _green("a", 800.0, pot=20.0),
            _green("b", 600.0, pot=15.0),
            _green("c", 300.0, pot=50.0),
            _green("d", 2000.0, pot=5.0),
        ]},
    ])
    rep = trial_depth_report(log, 750.0)

    assert [g["cid"] for g in rep["graduates"]] == ["a", "d"]
    assert rep["n_graduates"] == 2
    assert rep["graduate_pot_day"] == 25.0
    assert [g["cid"] for g in rep["near_misses_at_bar"]] == ["b"]
    assert rep["n_near_misses_at_bar"] == 1
    assert rep["depth_rejects_unique"] == 4

    # The sweep shows the opportunity set growing as the bar drops: at $1,000
    # only d graduates, at $750 a and d, at $500 a, b and d.
    assert rep["sweep"]["1000"] == {"graduates": 1, "graduate_pot_day": 5.0,
                                    "near": 2, "near_pot_day": 35.0,
                                    "mirages": 1}
    assert rep["sweep"]["750"]["graduates"] == 2
    assert rep["sweep"]["500"]["graduates"] == 3
    assert rep["sweep"]["500"]["graduate_pot_day"] == 40.0


def test_the_mirage_rule_is_re_derived_against_the_trial_bar(tmp_path):
    """dm=$400 is a MIRAGE against the permanent $1,000 bar (400 < 500) but a
    genuine near-miss against a $750 trial (400 >= 375). The sweep must show
    the market appearing as the bar drops -- that is exactly the opportunity
    set the trial exists to measure."""
    log = _write_log(tmp_path, [
        {"ts": 1.0, "greens": [_green("m", 400.0, pot=30.0)]},
    ])
    rep = trial_depth_report(log, 750.0)

    assert rep["n_graduates"] == 0
    assert [g["cid"] for g in rep["near_misses_at_bar"]] == ["m"]
    assert rep["sweep"]["1000"]["mirages"] == 1
    assert rep["sweep"]["750"]["near"] == 1
    assert rep["sweep"]["750"]["mirages"] == 0
    assert rep["sweep"]["500"]["near"] == 1      # 250 <= 400 < 500
    assert rep["sweep"]["500"]["graduates"] == 0


def test_last_reading_per_market_wins(tmp_path):
    """One market appears across many ranks; its newest reading describes it
    now, and the older one must not linger as a second (cheaper) graduate."""
    log = _write_log(tmp_path, [
        {"ts": 10.0, "greens": [_green("a", 500.0, pot=5.0)]},
        {"ts": 20.0, "greens": [_green("a", 900.0, pot=30.0)]},
    ])
    rep = trial_depth_report(log, 750.0)

    assert rep["unique_markets"] == 1
    assert rep["n_graduates"] == 1
    assert rep["graduates"][0]["depth_measured"] == 900.0
    assert rep["graduates"][0]["pot_day"] == 30.0


def test_non_depth_greens_never_graduate(tmp_path):
    """A spread reject or volume reject is not a depth-gate candidate, however
    deep its book happened to read -- the trial loosens the depth gate, not
    the others."""
    log = _write_log(tmp_path, [
        {"ts": 1.0, "greens": [
            _green("s", 900.0, cause="YES spread"),
            _green("v", 900.0, cause="volume"),
        ]},
    ])
    rep = trial_depth_report(log, 750.0)

    assert rep["n_graduates"] == 0
    assert rep["depth_rejects_unique"] == 0


def test_absent_log_reads_as_zero_not_error(tmp_path):
    """A missing log is an absence of data, reported as such, not a crash and
    not a silent zero that reads as 'no opportunity'."""
    rep = trial_depth_report(tmp_path / "missing.jsonl", 750.0)

    assert not rep["exists"]
    assert rep["n_graduates"] == 0 and rep["unique_markets"] == 0
    assert any("no such file" in n for n in rep["notes"])


def test_the_trap_predicate_matches_the_dashboard_bar_rule():
    """Same rule as server/fleet_dash.near_miss_stats._is_trap, parametrized
    to the bar under test: under-half-the-bar or >10%/day is a mirage, and a
    zero/absent depth must still count (the empty-book shape, not a pass)."""
    assert _is_trap({"depth_measured": 300.0, "depth_bar": 1000.0}, 750.0)
    assert not _is_trap({"depth_measured": 400.0, "depth_bar": 1000.0}, 750.0)
    assert _is_trap({"depth_measured": 0.0, "depth_bar": 1000.0}, 750.0)
    assert _is_trap({"depth_measured": None, "depth_bar": 1000.0,
                     "marg_pct_day": 50.0}, 750.0)
    assert _is_trap({"depth_measured": 800.0, "depth_bar": 1000.0,
                     "marg_pct_day": 11.0}, 750.0)
