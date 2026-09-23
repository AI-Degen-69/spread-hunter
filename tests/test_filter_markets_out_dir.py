"""A trial ranker run publishes to its own directory, never the shared feed.

`--out-dir` routes every RUN-anchored artifact (feed, pipeline and universe
snapshots, both near-miss logs, the write marker) to `runtime/trials/<run-id>/`
so the shadow-03 trial cannot leak into the shadow-01/02 baselines. Without the
flag the ranker writes exactly where it always did.
"""
from __future__ import annotations

import json

from scripts import filter_markets as fm


def test_out_dir_defaults_to_none():
    args = fm.parse_args([])
    assert args.out_dir is None


def test_out_dir_parses_a_directory():
    args = fm.parse_args(["--out-dir", "runtime/trials/shadow-03"])
    assert str(args.out_dir) == "runtime/trials/shadow-03"


def test_pipeline_snapshot_goes_to_the_output_directory(tmp_path):
    fm._write_pipeline_snapshot(
        cands=[], spread_cands=[], out=[], eligible=[], picked=[],
        causes={}, census={}, gates={}, attempted=0, rejected=0,
        out_dir=tmp_path)
    snap = json.loads((tmp_path / "pipeline.json").read_text(encoding="utf-8"))
    assert snap["counts"]["scored"] == 0


def test_universe_file_goes_to_the_output_directory(tmp_path):
    fm._write_universe_file([], {"pages_fetched": 0}, out_dir=tmp_path)
    snap = json.loads((tmp_path / "market_universe.json").read_text(encoding="utf-8"))
    assert snap["rows"] == []


def test_depth_near_misses_go_to_the_output_directory(tmp_path):
    fm._log_rank_near_misses([], 0, {}, out_dir=tmp_path)
    lines = (tmp_path / "near_misses.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["scored"] == 0


def test_volume_near_misses_go_to_the_output_directory(tmp_path):
    fm._log_rank_volume_near_misses([], 0, {}, out_dir=tmp_path)
    lines = (tmp_path / "volume_near_misses.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["scored"] == 0


def test_trial_run_leaves_the_shared_directories_untouched(tmp_path, monkeypatch):
    # Arrange -- the shared runtime/ is a fixture with a feed the trial must
    # not rewrite, and logs it must not append to.
    shared = tmp_path / "shared"
    shared.mkdir()
    feed = [{"cid": "baseline", "title": "baseline market"}]
    (shared / "markets.json").write_text(json.dumps(feed), encoding="utf-8")
    (shared / "near_misses.jsonl").write_text("", encoding="utf-8")
    (shared / "volume_near_misses.jsonl").write_text("", encoding="utf-8")
    monkeypatch.setattr(fm, "RUN", shared)
    trial = tmp_path / "trial"
    trial.mkdir()

    # Act
    fm._write_pipeline_snapshot(
        cands=[], spread_cands=[], out=[], eligible=[], picked=[],
        causes={}, census={}, gates={}, attempted=0, rejected=0,
        out_dir=trial)
    fm._write_universe_file([], {"pages_fetched": 0}, out_dir=trial)
    fm._log_rank_near_misses([], 0, {}, out_dir=trial)
    fm._log_rank_volume_near_misses([], 0, {}, out_dir=trial)

    # Assert -- the shared feed bytes are identical, the shared logs unappended.
    assert json.loads((shared / "markets.json").read_text(encoding="utf-8")) == feed
    assert (shared / "near_misses.jsonl").read_text(encoding="utf-8") == ""
    assert (shared / "volume_near_misses.jsonl").read_text(encoding="utf-8") == ""
    assert (shared / "pipeline.json").exists() is False
    assert (shared / "market_universe.json").exists() is False
    assert (trial / "pipeline.json").exists()
    assert (trial / "market_universe.json").exists()
    assert (trial / "near_misses.jsonl").exists()
    assert (trial / "volume_near_misses.jsonl").exists()


def test_main_with_out_dir_publishes_a_tagged_trial_feed(tmp_path, monkeypatch, capsys):
    # Arrange -- one scored winner, no network; the heavy writers are stubbed
    # so this proves the publish path: marker, tagged feed, trial directory.
    trial = tmp_path / "trials" / "shadow-03"
    winner = {"cid": "0xtrial", "title": "trial pick", "source": "spread",
              "eligible": True, "return_pct_day": 1.5,
              "est_income": 2.0, "est_capital": 100.0}
    seen = {}
    monkeypatch.setattr(fm, "RUN", tmp_path / "shared")
    monkeypatch.setattr(fm, "gamma_universe",
                        lambda s, min_volume_usd=None, full_scan=False: (
                            [], {"pages_fetched": 0, "rows_scanned": 0,
                                 "truncated": False, "cheap_rejects": {}}))
    monkeypatch.setattr(fm, "_score_universe", lambda *a, **k: ([winner], 1))
    monkeypatch.setattr(fm, "_if_adopted", lambda r: {})
    monkeypatch.setattr(fm, "_write_universe_file",
                        lambda *a, **k: seen.setdefault("universe", k.get("out_dir")))
    monkeypatch.setattr(fm, "_write_pipeline_snapshot",
                        lambda *a, **k: seen.setdefault("pipeline", k.get("out_dir")))
    monkeypatch.setattr(fm, "_log_rank_near_misses",
                        lambda *a, **k: seen.setdefault("depth", k.get("out_dir")) or 0)
    monkeypatch.setattr(fm, "_log_rank_volume_near_misses",
                        lambda *a, **k: seen.setdefault("volume", k.get("out_dir")) or 0)
    monkeypatch.setattr("sys.argv", ["filter_markets", "--out-dir", str(trial),
                                     "--trial-depth", "250"])

    # Act
    fm.main()
    capsys.readouterr()

    # Assert -- the trial feed carries the one winner tagged with the trial
    # bar; every writer was routed to the trial directory; the shared
    # directory was never touched.
    feed = json.loads((trial / "markets.json").read_text(encoding="utf-8"))
    assert len(feed) == 1
    assert feed[0]["cid"] == "0xtrial"
    assert feed[0]["trial_depth_usd"] == 250.0
    assert (trial / "ranking.marker").exists() is False
    assert {str(v) for v in seen.values()} == {str(trial)}
    assert not (tmp_path / "shared").exists()
