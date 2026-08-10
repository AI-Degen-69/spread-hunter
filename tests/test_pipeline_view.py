"""The market-pipeline telemetry: the ranker persists the whole selection
funnel, and the dashboard serves it alongside the fleet's current universe.

run/markets.json was the only artefact of a rank, so the dashboard could
answer "which markets is the fleet on" but not "how did the funnel treat the
other two hundred". These tests pin the snapshot writer (the funnel captured
at rank time) and the /api/pipeline endpoint that renders it live.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import rank_markets as rank  # noqa: E402
from server import fleet_dash as dash     # noqa: E402
from strategy import stats                # noqa: E402


def _out(title, eligible, reason="", vol=100_000, days=2.0, source="rewards",
         income=2.0, capital=120.0, ret=1.7,
         their_score=5000.0, daily=5.0, max_spread=3.5, spread=0.02,
         with_score=True):
    row = {
        "source": source, "eligible": eligible, "reject_reason": reason,
        "volume_24h": vol, "days_to_resolve": days,
        "cid": title, "title": title, "slug": title,
        "est_income": income, "est_capital": capital,
        "return_pct_day": ret,
    }
    # An identity rejection happens before the book is fetched, so it carries
    # no score reading -- the shape `evaluate` really produces.
    if with_score:
        row.update({"their_score": their_score, "daily": daily,
                    "max_spread": max_spread, "spread": spread})
    return row


def test_ranker_snapshot_persists_the_whole_funnel(tmp_path, monkeypatch):
    monkeypatch.setattr(rank, "RUN", tmp_path)
    cands = [(10.0, {"question": f"reward {i}", "end_date_iso": None})
             for i in range(30)]
    spread = [{"question": f"spread {i}", "_volume_24h": 500_000,
               "_spread": 0.07, "end_date_iso": None} for i in range(30)]
    out = [
        # A depth reject on a crowded book: if adopted, the allocator's
        # first-dollar marginal would sit far under the floor.
        _out("thin book", False,
             "YES: top-3 bid depth $400 <= $1,000", vol=900_000),
        # A depth reject on a near-empty book with a real pot: the ONLY thing
        # standing between it and a funded quote is this ranker gate.
        _out("thin but would fund", False,
             "YES: top-3 bid depth $950 <= $1,000", vol=900_000,
             their_score=150.0, daily=20.0),
        _out("wide spread", False,
             "YES: spread 0.0800 > 0.0600", vol=800_000),
        _out("low volume", False,
             "24h volume $40,000 < $250,000", vol=40_000),
        _out("long horizon", False,
             "horizon 40.0d > 30d", vol=900_000, days=40.0),
        _out("under floor", False,
             "income $0.50/day under payout floor", income=0.5),
        # Identity rejects carry no score reading -> no estimate.
        _out("blocked keyword", False, "blocked dynamic/submarket keyword",
             with_score=False),
        _out("winner one", True, income=3.0, ret=2.5),
        _out("winner two", True, income=2.0, ret=1.5),
    ]
    eligible = [r for r in out if r["eligible"]]

    rank._write_pipeline_snapshot(
        cands=cands, spread_cands=spread, out=out, eligible=eligible,
        picked=eligible,
        causes={"YES: top-3 bid depth": 2, "YES spread": 1,
                "volume": 1, "horizon": 1, "income": 1,
                "blocked dynamic/submarket keyword": 1},
        census="scored 9, rejected 7 (YES: top-3 bid depth=2 YES spread=1 "
               "volume=1 horizon=1 income=1 blocked dynamic/submarket "
               "keyword=1), wrote top 2 -> run/markets.json",
        gates="gates: primary/main-line only, ...",
        attempted=9, rejected=7)

    snap = json.loads((tmp_path / "pipeline.json").read_text(encoding="utf-8"))
    assert snap["counts"]["funded"] == 30
    assert snap["counts"]["spread_universe"] == 30
    assert snap["counts"]["attempted"] == 9
    assert snap["counts"]["scored"] == 9
    assert snap["counts"]["rejected"] == 7
    assert snap["counts"]["eligible"] == 2
    assert snap["counts"]["picked"] == 2
    assert snap["counts"]["dropped_no_verdict"] == 0
    # Raw samples are capped so the snapshot stays small.
    assert len(snap["raw"]["rewards"]) == 24
    assert len(snap["raw"]["spread"]) == 24
    # Rejections bucketed by gate (the same labels the census line uses, so
    # the snapshot and the printed census always agree), each with examples.
    causes = [r["cause"] for r in snap["rejections"]]
    assert set(causes) == {"YES: top-3 bid depth", "YES spread",
                           "volume", "horizon", "income",
                           "blocked dynamic/submarket keyword"}
    depth = next(r for r in snap["rejections"]
                 if r["cause"] == "YES: top-3 bid depth")
    assert depth["n"] == 2
    # Near-miss count: exactly one of the two depth rejects would have cleared
    # the allocator's 2%/day floor had the ranker admitted it.
    assert depth["would_fund"] == 1
    # The other depth reject ("thin book" at $400, under half the bar) is an
    # empty-book trap: labeled, counted, and never a green.
    assert depth["traps"] == 1
    exs = {e["title"]: e for e in depth["examples"]}
    assert exs["thin book"]["reason"].startswith("YES: top-3 bid depth")
    # 20/day pot on a book scoring 150 (T = 150/k, k = ((3.5-2)/3.5)^2)
    # -> first-dollar marginal ~2.45%/day, over the floor.
    fund = exs["thin but would fund"]["marg"]
    assert fund["would_fund"] is True
    assert abs(fund["marg_pct_day"] - 2.45) < 0.01
    assert fund["pot_day"] == 20.0
    # A 5/day pot on a book scoring 5,000 -> ~0.02%/day, kept out by the
    # floor exactly as it would be if adopted.
    assert exs["thin book"]["marg"]["would_fund"] is False
    assert exs["thin book"]["marg"]["marg_pct_day"] < 0.1
    # Identity rejects never fetched the book: no estimate, no near-miss.
    blocked = next(r for r in snap["rejections"]
                   if r["cause"] == "blocked dynamic/submarket keyword")
    assert blocked["would_fund"] == 0
    assert "marg" not in blocked["examples"][0]
    # The final lane is exactly the eligible set; picked mirrors it here.
    assert [r["title"] for r in snap["final"]] == ["winner one", "winner two"]
    assert snap["picked"][0]["source"] == "rewards"
    assert snap["picked"][0]["income"] == 3.0


def test_ranker_snapshot_reports_dropped_without_verdict(tmp_path, monkeypatch):
    """Candidates that fail inside scoring (book fetch, one-sided, band)
    never reach a verdict -- the snapshot must not lose that count."""
    monkeypatch.setattr(rank, "RUN", tmp_path)
    rank._write_pipeline_snapshot(
        cands=[], spread_cands=[], out=[], eligible=[], picked=[],
        causes={},
        census="scored 0, rejected 0 (none), wrote top 0 -> run/markets.json",
        gates="gates: ...", attempted=9, rejected=0)
    snap = json.loads((tmp_path / "pipeline.json").read_text(encoding="utf-8"))
    assert snap["counts"]["scored"] == 0
    assert snap["counts"]["dropped_no_verdict"] == 9


def test_dashboard_pipeline_endpoint_merges_live_fleet_state(tmp_path,
                                                             monkeypatch):
    monkeypatch.setenv("MAKER_DB", str(tmp_path / "dash.db"))
    monkeypatch.setattr(dash, "RUN", tmp_path)
    monkeypatch.setattr(stats, "DB", tmp_path / "dash.db")

    (tmp_path / "pipeline.json").write_text(json.dumps({
        "ts": time.time(), "census": "c", "gates": "g",
        "counts": {"funded": 3, "spread_universe": 0, "attempted": 3,
                   "scored": 3, "dropped_no_verdict": 0, "rejected": 1,
                   "eligible": 2, "picked": 2},
        "raw": {"rewards": [], "spread": []}, "rejections": [], "final": [],
        "picked": [],
    }), encoding="utf-8")
    (tmp_path / "markets.json").write_text(json.dumps([
        {"cid": "c1", "title": "adopted one", "slug": "adopted-one",
         "daily": 5.0, "source": "rewards"},
        {"cid": "c2", "title": "adopted two", "slug": "adopted-two",
         "daily": 0.0, "source": "spread"},
    ]), encoding="utf-8")
    (tmp_path / "fleet_state.json").write_text(json.dumps([
        {"cid": "c1", "title": "adopted one", "daily": 5.0,
         "source": "rewards",
         "_live": {"income": 1.2, "capital": 120.0, "share": 0.05,
                   "err": "", "ts": time.time(),
                   "alloc": {"funded": False, "shares": 0,
                             "marginal_pct": 1.16,
                             "first_marginal_pct": 1.16,
                             "competition_avg": 33397.0,
                             "threshold_pct": 2.0,
                             "reason": "unfunded: below 2.00%/day floor"}}},
    ]), encoding="utf-8")

    payload = dash.pipeline()
    assert payload["picked"] == 2
    assert payload["live"] == 1
    assert payload["snapshot"]["counts"]["eligible"] == 2
    assert payload["snapshot_age"] is not None
    by = {g["slug"]: g for g in payload["graduated"]}
    assert by["adopted-one"]["live"] is True
    assert by["adopted-one"]["income"] == 1.2
    assert by["adopted-one"]["capital"] == 120.0
    assert by["adopted-one"]["source"] == "rewards"
    # The allocator verdict rides through to the board unchanged.
    a = by["adopted-one"]["alloc"]
    assert a["funded"] is False
    assert a["marginal_pct"] == 1.16
    assert a["competition_avg"] == 33397.0
    assert a["reason"] == "unfunded: below 2.00%/day floor"
    # A market with no live state has no verdict either.
    assert by["adopted-two"]["alloc"] is None
    assert by["adopted-two"]["live"] is False
    assert by["adopted-two"]["source"] == "spread"
    assert by["adopted-two"]["income"] == 0.0


def test_dashboard_pipeline_endpoint_degrades_without_snapshot(tmp_path,
                                                               monkeypatch):
    """No pipeline.json yet (fresh install) must be a shape, not a crash."""
    monkeypatch.setenv("MAKER_DB", str(tmp_path / "dash.db"))
    monkeypatch.setattr(dash, "RUN", tmp_path)
    monkeypatch.setattr(stats, "DB", tmp_path / "dash.db")
    payload = dash.pipeline()
    assert payload["snapshot"] is None
    assert payload["snapshot_age"] is None
    assert payload["graduated"] == []
    assert payload["picked"] == 0
    assert payload["live"] == 0
