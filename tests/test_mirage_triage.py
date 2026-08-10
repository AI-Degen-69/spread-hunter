"""Tests for `scripts/mirage_triage` (U33) -- the offline triage of the
depth-reject population. Pins the read, the aggregation, the bucket mirror
of the trap rule, and the volume-gate / sharpening-signal tables."""

import json

from scripts.mirage_triage import (
    _load_ranks, _per_market, _grade, triage_report,
)


def _green(cid, depth, cause="YES: top-3 bid depth", **kw):
    g = {"cid": cid, "title": cid, "slug": cid, "cause": cause,
         "reason": f"YES: top-3 bid depth ${depth} <= $1,000.00",
         "depth_measured": depth, "depth_bar": 1000.0, "pot_day": 1.0,
         "marg_pct_day": 2.0, "volume_24h": 500_000.0, "days": 3.0}
    g.update(kw)
    return g


def _write_nm(path, ranks):
    with open(path, "w", encoding="utf-8") as fh:
        for ts, greens in ranks:
            fh.write(json.dumps({"ts": ts, "greens": greens}) + "\n")
            fh.write("garbage line that must be skipped\n")


def test_load_ranks_skips_malformed_and_attaches_ts(tmp_path):
    p = tmp_path / "nm.jsonl"
    _write_nm(p, [(1.0, [_green("a", 700.0)]), (2.0, [_green("b", 300.0)])])
    ranks, n = _load_ranks(p)
    assert n == 2
    assert ranks[0]["ts"] == 1.0
    assert ranks[0]["greens"][0]["cid"] == "a"
    assert ranks[0]["greens"][0]["ts"] == 1.0
    # the garbage line is skipped, not fatal: exactly the two rank rows
    assert len(ranks) == 2


def test_per_market_keeps_only_depth_rejects_and_all_readings(tmp_path):
    p = tmp_path / "nm.jsonl"
    _write_nm(p, [
        (1.0, [_green("a", 700.0), _green("x", 500.0, cause="YES spread")]),
        (2.0, [_green("a", 800.0)]),
    ])
    markets = _per_market(_load_ranks(p)[0])
    assert set(markets) == {"a"}  # the spread-reject green is not a depth green
    m = markets["a"]
    assert m["n_readings"] == 2
    assert m["depth_last"] == 800.0
    assert m["depth_max"] == 800.0
    assert m["volume_last"] == 500_000.0


def test_grade_mirrors_trap_rule(tmp_path):
    # mirage: depth under half the bar
    assert _grade(_green("m1", 400.0), 1000.0) == "mirage"
    # mirage: estimate past 10%/day even with real depth
    assert _grade(_green("m2", 900.0, marg_pct_day=15.0), 1000.0) == "mirage"
    # near: 0.5*bar <= depth < bar
    assert _grade(_green("n1", 700.0), 1000.0) == "near"
    # graduate: depth >= bar and not a mirage
    assert _grade(_green("g1", 1200.0), 1000.0) == "graduate"
    # under half the loosest sweep bar is still a trap, not a near-miss
    assert _grade(_green("b1", 200.0), 500.0) == "mirage"
    # a green with no parseable depth grades 'below' -- absent, not near
    assert _grade(_green("b2", None), 1000.0) == "below"
    # the empty book parses depth 0.0 and MUST still be a trap (falsy check)
    assert _grade(_green("e1", 0.0), 1000.0) == "mirage"


def test_report_volume_cross_and_signals(tmp_path):
    p = tmp_path / "nm.jsonl"
    _write_nm(p, [
        (1.0, [
            _green("mirage_low_vol", 400.0, volume_24h=1_000.0),
            _green("near_high_vol", 700.0, volume_24h=500_000.0),
            _green("grad_high_vol", 1200.0, volume_24h=500_000.0),
            _green("below_empty", None, volume_24h=0.0),
        ]),
    ])
    rep = triage_report(p, bar=1000.0, volume_bar=250_000.0)
    assert rep["buckets"] == {"graduate": 1, "near": 1, "mirage": 1, "below": 1}
    vc = rep["volume_cross"]
    # the graduate and the near-miss both clear the volume gate; the mirage
    # and the below-market do not
    assert vc["graduate"]["volume_fail"] == 0
    assert vc["near"]["volume_fail"] == 0
    assert vc["mirage"]["volume_fail"] == 1
    assert vc["all_depth_rejects"]["volume_fail"] == 2
    # the live-volume-gate signal excludes the mirage and loses no n/g
    vol_sig = next(s for s in rep["signals"]
                   if s["signal"].startswith("volume_last >= $250k"))
    assert vol_sig["mirages_excluded"] == 1
    assert vol_sig["near_or_grad_lost"] == 0
    # reading-consistency signal: the mirage was seen once
    one_shot = next(s for s in rep["signals"]
                    if s["signal"].startswith("seen in >= 2 ranks"))
    assert one_shot["mirages_excluded"] == 1


def test_report_absent_log_is_absence_not_zero(tmp_path):
    rep = triage_report(tmp_path / "missing.jsonl")
    assert rep["exists"] is False
    assert rep["depth_rejects"] == 0
