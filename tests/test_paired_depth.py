from __future__ import annotations

import json

import pytest

from scripts.filter_markets import build_paired_depth_bundle


def _row(cid: str, yes: float, no: float, score: float) -> dict:
    return {
        "cid": cid,
        "slug": f"market-{cid}",
        "title": f"Market {cid}",
        "return_pct_day": score,
        "yes_depth_usd": yes,
        "no_depth_usd": no,
    }


def test_pair_uses_one_common_rank_and_only_varies_both_leg_depth_cutoffs():
    rows = [
        _row("shared-top", 900, 800, 9),
        _row("treatment-only", 400, 700, 8),
        _row("fails-one-leg", 900, 400, 7),
        _row("at-control-boundary", 500, 900, 6),
        _row("at-treatment-boundary", 300, 900, 5),
        _row("below-treatment", 249, 900, 4),
    ]

    bundle = build_paired_depth_bundle(
        rows, top=10, control_depth_usd=500, treatment_depth_usd=250,
        volume_gate_usd=125_000, snapshot_id="snap-a", ranked_at=123.0,
    )

    assert [r["cid"] for r in bundle["control"]] == ["shared-top"]
    assert [r["cid"] for r in bundle["treatment"]] == [
        "shared-top", "treatment-only", "fails-one-leg",
        "at-control-boundary", "at-treatment-boundary",
    ]
    assert bundle["counts"] == {
        "treatment_eligible": 5,
        "control_eligible": 1,
        "incremental_eligible": 4,
        "treatment_selected": 5,
        "control_selected": 1,
    }
    assert all(r["paired_depth_snapshot_id"] == "snap-a"
               for arm in (bundle["control"], bundle["treatment"])
               for r in arm)
    assert {r["paired_depth_arm"] for r in bundle["control"]} == {"control"}
    assert {r["paired_depth_arm"] for r in bundle["treatment"]} == {"treatment"}
    assert bundle["other_gate_trials"] is False
    assert bundle["volume_gate_usd"] == 125_000


def test_pair_applies_the_same_top_n_to_each_ranked_arm():
    rows = [_row(f"m{i}", 900 - i, 900 - i, 10 - i) for i in range(5)]

    bundle = build_paired_depth_bundle(
        rows, top=2, control_depth_usd=500, treatment_depth_usd=250,
        volume_gate_usd=125_000, snapshot_id="snap-b", ranked_at=456.0,
    )

    assert [r["cid"] for r in bundle["control"]] == ["m0", "m1"]
    assert [r["cid"] for r in bundle["treatment"]] == ["m0", "m1"]
    assert bundle["counts"]["control_eligible"] == 5
    assert bundle["counts"]["treatment_eligible"] == 5


def test_pair_requires_positive_ordered_thresholds_and_positive_top_n():
    with pytest.raises(ValueError, match="top"):
        build_paired_depth_bundle(
            [], top=0, control_depth_usd=500, treatment_depth_usd=250,
            volume_gate_usd=125_000, snapshot_id="snap", ranked_at=0,
        )
    with pytest.raises(ValueError, match="control depth"):
        build_paired_depth_bundle(
            [], top=1, control_depth_usd=250, treatment_depth_usd=250,
            volume_gate_usd=125_000, snapshot_id="snap", ranked_at=0,
        )
    with pytest.raises(ValueError, match="control depth"):
        build_paired_depth_bundle(
            [], top=1, control_depth_usd=500, treatment_depth_usd=0,
            volume_gate_usd=125_000, snapshot_id="snap", ranked_at=0,
        )


def test_paired_depth_cli_requires_isolated_explicit_depth_only_mode():
    from scripts.filter_markets import parse_args

    with pytest.raises(SystemExit):
        parse_args(["--paired-depth-control-usd", "500"])
    with pytest.raises(SystemExit):
        parse_args(["--out-dir", "runtime/trials/pair", "--trial-depth", "250",
                    "--paired-depth-control-usd", "500", "--dry-run"])
    with pytest.raises(SystemExit):
        parse_args(["--out-dir", "runtime/trials/pair", "--trial-depth", "250",
                    "--trial-volume", "60000", "--paired-depth-control-usd", "500"])


def test_paired_bundle_is_a_single_json_document_with_both_arms():
    bundle = build_paired_depth_bundle(
        [_row("m1", 900, 900, 1)], top=2, control_depth_usd=500,
        treatment_depth_usd=250, volume_gate_usd=125_000,
        snapshot_id="snap-c", ranked_at=789.0,
    )

    decoded = json.loads(json.dumps(bundle))

    assert decoded["format"] == "spread_hunter.paired-depth.v1"
    assert decoded["snapshot_id"] == "snap-c"
    assert decoded["control"][0]["cid"] == decoded["treatment"][0]["cid"]


def test_ranker_writes_atomic_pair_with_shared_snapshot_and_pins_volume(
        tmp_path, monkeypatch, capsys):
    from scripts import filter_markets as fm

    scored = [
        {"cid": "0xdeep", "title": "deep", "slug": "deep", "source": "spread",
         "eligible": True, "return_pct_day": 4.0, "yes_depth_usd": 800,
         "no_depth_usd": 900, "est_income": 1.0, "est_capital": 100.0,
         "event_id": "event-deep", "event_slug": "event-deep"},
        {"cid": "0xmid", "title": "mid", "slug": "mid", "source": "spread",
         "eligible": True, "return_pct_day": 3.0, "yes_depth_usd": 400,
         "no_depth_usd": 600, "est_income": 0.8, "est_capital": 100.0,
         "event_id": "event-mid", "event_slug": "event-mid"},
    ]
    seen = {}

    def universe_spy(session, *a, **kw):
        seen["full_scan"] = kw.get("full_scan")
        return ([{"condition_id": "0xdeep"}, {"condition_id": "0xmid"}],
                {"pages_fetched": 1, "rows_scanned": 2, "truncated": False,
                 "cheap_rejects": {}})

    monkeypatch.setattr(fm, "gamma_universe", universe_spy)

    def score(universe, **kwargs):
        seen.update(kwargs)
        return scored, len(universe)

    monkeypatch.setattr(fm, "_score_universe", score)
    monkeypatch.setattr(fm, "_if_adopted", lambda _row: None)
    monkeypatch.setattr(fm, "_write_universe_file", lambda *a, **kw: None)
    monkeypatch.setattr(fm, "_write_pipeline_snapshot", lambda *a, **kw: None)
    monkeypatch.setattr(fm, "_log_rank_near_misses", lambda *a, **kw: 0)
    monkeypatch.setattr(fm, "_log_rank_volume_near_misses", lambda *a, **kw: 0)
    monkeypatch.setattr("sys.argv", [
        "filter_markets", "--top", "5", "--out-dir", str(tmp_path / "paired"),
        "--trial-depth", "250", "--paired-depth-control-usd", "500",
    ])

    fm.main()

    bundle = json.loads((tmp_path / "paired" / "paired_markets.json").read_text(
        encoding="utf-8"))
    audit = (tmp_path / "paired" / "paired_depth_audit.jsonl").read_text(
        encoding="utf-8").splitlines()
    capsys.readouterr()

    # Paired mode must exhaust the listing: the plain rank's boundary policy
    # sets `truncated` as a POLICY stop, and the paired completeness gate
    # refuses on that flag -- so without a full scan the gate fires on every
    # rank and no bundle could ever exist.
    assert seen["full_scan"] is True
    assert seen["depth_bar"] == 250.0
    assert seen["volume_bar"] == fm.MIN_VOLUME_24H
    assert bundle["control_depth_usd"] == 500.0
    assert bundle["treatment_depth_usd"] == 250.0
    assert bundle["control"][0]["cid"] == "0xdeep"
    assert [row["cid"] for row in bundle["treatment"]] == ["0xdeep", "0xmid"]
    assert bundle["control"][0]["paired_depth_snapshot_id"] == bundle["snapshot_id"]
    assert bundle["treatment"][1]["paired_depth_snapshot_id"] == bundle["snapshot_id"]
    assert len(audit) == 1
    assert json.loads(audit[0])["snapshot_id"] == bundle["snapshot_id"]
