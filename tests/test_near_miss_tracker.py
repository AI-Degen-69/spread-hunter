"""The near-miss tracker (U21): the ranker logs every rejected market that
would clear the allocator's floor, and the dashboard accumulates whether that
is CONSISTENT enough to justify a controlled gate-loosening trial.

The FILTERS lane shows the estimate live; this is the durable side -- a log
that survives pipeline.json's every-rank overwrite, and a stats read that
turns it into a COLLECTING / READY_TO_TRIAL verdict against fixed bars.
"""
import json
import time
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import rank_markets as rank  # noqa: E402
from server import fleet_dash as dash      # noqa: E402


def _row(title, eligible, reason, cid=None, source="rewards", daily=5.0,
         their_score=5000.0, volume=900_000, days=2.0):
    r = {
        "source": source, "eligible": eligible, "reject_reason": reason,
        "cid": cid or title, "title": title, "slug": title,
        "volume_24h": volume, "days_to_resolve": days,
        "their_score": their_score, "daily": daily, "max_spread": 3.5,
        "spread": 0.02,
    }
    return r


def test_ranker_logs_only_green_near_misses(tmp_path, monkeypatch):
    monkeypatch.setattr(rank, "RUN", tmp_path)
    # Green: 20/day pot on a 150-score book -> ~2.45%/day, clears the floor.
    green = _row("green depth reject", False,
                 "YES: top-3 bid depth $612.00 <= $1,000.00",
                 cid="g1", their_score=150.0, daily=20.0)
    # Below floor: crowded book, low pot.
    red = _row("crowded reject", False,
               "YES: top-3 bid depth $950.00 <= $1,000.00",
               cid="r1", their_score=5000.0, daily=5.0)
    winner = _row("adopted winner", True, "", cid="w1")
    out = [green, red, winner]
    verdicts = {id(r): rank._if_adopted(r) for r in out}
    n = rank._log_rank_near_misses(out, 2, verdicts, ts=1_700_000_000.0)
    assert n == 1

    lines = (tmp_path / "near_misses.jsonl").read_text(
        encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["ts"] == 1_700_000_000.0
    assert row["scored"] == 3
    assert row["rejected"] == 2
    assert len(row["greens"]) == 1
    g = row["greens"][0]
    assert g["cid"] == "g1"
    assert g["cause"] == "YES: top-3 bid depth"
    assert g["marg_pct_day"] == 2.45
    assert g["pot_day"] == 20.0
    assert g["competition"] == 150.0
    # The depth measurement is parsed out of the reason so the stats can ask
    # "would a modest loosening have admitted it?".
    assert g["depth_measured"] == 612.0
    assert g["depth_bar"] == 1000.0


def test_ranker_counts_unparsed_depth_reasons(tmp_path, monkeypatch):
    """A depth-gate reason whose format the parser no longer recognises must
    be counted, not silently dropped -- otherwise the small-margin bar would
    undercount without any signal."""
    monkeypatch.setattr(rank, "RUN", tmp_path)
    green = _row("green depth reject", False,
                 "YES: top-3 bid depth $850.00 <= 1000",  # no "$" on the bar
                 cid="g1", their_score=150.0, daily=20.0)
    out = [green]
    verdicts = {id(r): rank._if_adopted(r) for r in out}
    rank._log_rank_near_misses(out, 1, verdicts, ts=1_700_000_000.0)
    row = json.loads((tmp_path / "near_misses.jsonl").read_text(
        encoding="utf-8").strip())
    assert row["depth_unparsed"] == 1
    assert row["greens"][0]["depth_measured"] is None


def test_ranker_logs_a_rank_even_with_zero_greens(tmp_path, monkeypatch):
    """A rank with no greens must still record itself -- the stability bar is
    a fraction of ranks, and zero-green ranks count against it."""
    monkeypatch.setattr(rank, "RUN", tmp_path)
    red = _row("crowded reject", False,
               "YES: top-3 bid depth $950.00 <= $1,000.00")
    out = [red]
    verdicts = {id(r): rank._if_adopted(r) for r in out}
    n = rank._log_rank_near_misses(out, 1, verdicts, ts=1_700_000_000.0)
    assert n == 0
    row = json.loads((tmp_path / "near_misses.jsonl").read_text(
        encoding="utf-8").strip())
    assert row["greens"] == []


def _rank(ts, greens):
    return {"ts": ts, "scored": 200, "rejected": 198, "greens": greens}


def _green(cid, cause="YES: top-3 bid depth", depth_measured=None,
           depth_bar=1000.0, pot=20.0, marg=2.4):
    g = {"cid": cid, "title": cid, "slug": cid, "cause": cause,
         "reason": "x", "source": "rewards", "marg_pct_day": marg,
         "pot_day": pot, "competition": 150.0, "threshold_pct": 2.0,
         "volume_24h": 900_000, "days": 2.0,
         "depth_measured": depth_measured, "depth_bar": depth_bar}
    return g


def test_near_miss_stats_stays_collecting_until_every_bar(tmp_path, monkeypatch):
    monkeypatch.setattr(dash, "NEAR_MISS_FILE", tmp_path / "near_misses.jsonl")
    day0 = 1_700_000_000.0
    lines = []
    # 72 ranks within one UTC day, each with the same 6 unique green markets,
    # only 2 of which are small-margin depth. Stability 100%, unique 6 < 25,
    # days 1 < 3, small-margin 2 < 5 -> COLLECTING.
    for i in range(72):
        greens = [_green(f"m{j}", depth_measured=850.0 if j < 2 else 400.0)
                  for j in range(6)]
        lines.append(_rank(day0 + i * 60, greens))
    (tmp_path / "near_misses.jsonl").write_text(
        "\n".join(json.dumps(l) for l in lines) + "\n", encoding="utf-8")
    s = dash.near_miss_stats()
    assert s["status"] == "COLLECTING"
    assert s["ranks"] == 72
    # Decision bars count the CREDIBLE set only: the two 850-depth markets
    # feed unique/days/stability, the four 400-depth traps never do.
    assert s["greens"] == 72 * 2
    assert s["traps"] == 72 * 4
    assert s["days"] == 1
    assert s["unique_markets"] == 2
    assert s["small_margin_depth"] == 2
    assert s["stability"] == 1.0
    # Pot is the CREDIBLE set only too: the two 850-depth markets count, the
    # four 400-depth traps (under half the bar) are excluded.
    assert s["uniq_pot_day"] == 2 * 20.0
    assert s["raw_uniq_pot_day"] == 6 * 20.0
    assert s["pot_traps"] == 4


def test_near_miss_stats_ready_to_trial_when_all_bars_met(tmp_path, monkeypatch):
    monkeypatch.setattr(dash, "NEAR_MISS_FILE", tmp_path / "near_misses.jsonl")
    # 25 unique credible depth greens across 3 days, all small-margin (only
    # credible markets count toward the bars -- a mirage can never be "enough
    # evidence"), present in every one of the last 72 ranks -> READY_TO_TRIAL.
    day = 1_700_000_000.0
    lines = []
    for i in range(72):
        ts = day + i * 600
        # 25 distinct cids, all at 85% of the depth bar, re-appearing each
        # rank.
        greens = [_green(f"m{j}", depth_measured=850.0) for j in range(25)]
        # Spread the ranks across 3 distinct UTC dates.
        lines.append(_rank(day + (i % 3) * 86400.0 + i * 600, greens))
    (tmp_path / "near_misses.jsonl").write_text(
        "\n".join(json.dumps(l) for l in lines) + "\n", encoding="utf-8")
    s = dash.near_miss_stats()
    assert s["status"] == "READY_TO_TRIAL"
    assert s["days"] >= 3
    assert s["unique_markets"] >= 25
    assert s["small_margin_depth"] >= 25
    assert s["stability"] >= 0.5
    assert s["traps"] == 0
    # All 25 credible x $20.
    assert s["uniq_pot_day"] == 25 * 20.0
    assert s["raw_uniq_pot_day"] == 25 * 20.0
    assert s["pot_traps"] == 0


def test_traps_do_not_feed_decision_bars(tmp_path, monkeypatch):
    """An empty-book mirage -- the 4,938%/day UK-inflation shape -- must not
    count toward days, unique markets, or stability: one wild hour of empty
    books is not evidence for loosening a gate."""
    monkeypatch.setattr(dash, "NEAR_MISS_FILE", tmp_path / "near_misses.jsonl")
    # 3 ranks, each holding ONLY mirages (trap by marg), on 3 different days.
    day = 1_700_000_000.0
    lines = []
    for i in range(3):
        mirage = _green(f"mirage{i}", cause="YES spread",
                        depth_measured=None, marg=4938.27, pot=16.0)
        lines.append(_rank(day + i * 86400.0, [mirage]))
    (tmp_path / "near_misses.jsonl").write_text(
        "\n".join(json.dumps(l) for l in lines) + "\n", encoding="utf-8")
    s = dash.near_miss_stats()
    assert s["days"] == 0
    assert s["unique_markets"] == 0
    assert s["stability"] == 0.0
    assert s["greens"] == 0
    assert s["traps"] == 3
    # Data exists -- COLLECTING, not NO_DATA -- it just has nothing credible.
    assert s["status"] == "COLLECTING"


def test_pot_excludes_marg_traps_even_at_full_depth(tmp_path, monkeypatch):
    """The trap rule has two arms: depth under half the bar OR an absurd
    marg estimate. A market with healthy depth but a >10%/day estimate (a
    near-empty reward window with a giant pot) is still excluded -- the
    Yankees $15,768/day-on-$22-depth shape."""
    monkeypatch.setattr(dash, "NEAR_MISS_FILE", tmp_path / "near_misses.jsonl")
    greens = [
        _green("deep-but-mirage", depth_measured=900.0, pot=15000.0, marg=1500.0),
        _green("real", depth_measured=900.0, pot=20.0, marg=3.0),
    ]
    (tmp_path / "near_misses.jsonl").write_text(
        json.dumps(_rank(1_700_000_000.0, greens)) + "\n", encoding="utf-8")
    s = dash.near_miss_stats()
    assert s["uniq_pot_day"] == 20.0      # the mirage pot is not counted
    assert s["raw_uniq_pot_day"] == 15020.0
    assert s["pot_traps"] == 1


def test_zero_depth_book_is_a_trap_not_credible(tmp_path, monkeypatch):
    """A book whose top-3 depth parses to exactly $0.00 -- a fully empty
    book -- is falsy in Python, so `_is_trap` must use `is not None` (not
    truthiness) or it would classify the emptiest possible book as credible
    whenever the estimate stayed under 10%/day."""
    monkeypatch.setattr(dash, "NEAR_MISS_FILE", tmp_path / "near_misses.jsonl")
    empty = _green("zero-depth", depth_measured=0.0, pot=20.0, marg=3.0)
    (tmp_path / "near_misses.jsonl").write_text(
        json.dumps(_rank(1_700_000_000.0, [empty])) + "\n", encoding="utf-8")
    s = dash.near_miss_stats()
    assert s["greens"] == 0
    assert s["traps"] == 1
    assert s["uniq_pot_day"] == 0.0


def test_pot_all_traps_renders_zero_credible(tmp_path, monkeypatch):
    """Every near-miss being a trap must not break the tile: credible pot
    $0, trap count == unique count, raw pot unchanged."""
    monkeypatch.setattr(dash, "NEAR_MISS_FILE", tmp_path / "near_misses.jsonl")
    greens = [_green(f"m{j}", depth_measured=100.0, pot=20.0) for j in range(4)]
    (tmp_path / "near_misses.jsonl").write_text(
        json.dumps(_rank(1_700_000_000.0, greens)) + "\n", encoding="utf-8")
    s = dash.near_miss_stats()
    assert s["uniq_pot_day"] == 0.0
    assert s["pot_traps"] == 4
    assert s["raw_uniq_pot_day"] == 80.0


def test_near_miss_stats_surfaces_unparsed_total(tmp_path, monkeypatch):
    monkeypatch.setattr(dash, "NEAR_MISS_FILE", tmp_path / "near_misses.jsonl")
    r = _rank(1_700_000_000.0, [])
    r["depth_unparsed"] = 3
    (tmp_path / "near_misses.jsonl").write_text(
        json.dumps(r) + "\n", encoding="utf-8")
    s = dash.near_miss_stats()
    assert s["depth_unparsed"] == 3


def test_near_miss_stats_no_data_on_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(dash, "NEAR_MISS_FILE", tmp_path / "nope.jsonl")
    s = dash.near_miss_stats()
    assert s["status"] == "NO_DATA"
    assert s["ranks"] == 0
    assert s["greens"] == 0


def test_near_miss_stats_ignores_corrupt_lines(tmp_path, monkeypatch):
    monkeypatch.setattr(dash, "NEAR_MISS_FILE", tmp_path / "near_misses.jsonl")
    (tmp_path / "near_misses.jsonl").write_text(
        "{not json}\n" + json.dumps(_rank(1_700_000_000.0, [])) + "\n",
        encoding="utf-8")
    s = dash.near_miss_stats()
    assert s["status"] == "NO_DATA"
    assert s["ranks"] == 1
    assert s["greens"] == 0
