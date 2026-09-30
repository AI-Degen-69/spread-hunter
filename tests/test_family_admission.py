"""D12 admission trial: ranker axis, atomic bundle, CLI guards (all TDD)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from scripts.family_probe import family_key
from scripts.filter_markets import (
    RUN,
    build_paired_admission_bundle,
    evaluate,
    parse_args,
)


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _GoodSession:
    """Live tape (enough movement) + tight deep books, no network."""

    def __init__(self):
        import time
        self._trades = [{"timestamp": time.time(), "price": 0.5,
                         "size": 4000.0}]

    def get(self, url, params=None, timeout=None):
        if "trades" in url:
            return _Resp(self._trades)
        return _Resp({
            "bids": [{"price": "0.48", "size": "5000"}],
            "asks": [{"price": "0.52", "size": "5000"}],
        })


class _DeadBookSession(_GoodSession):
    def get(self, url, params=None, timeout=None):
        if "trades" in url:
            return _Resp(self._trades)
        raise OSError("book unreadable")


def _candidate(cid: str, **over) -> dict:
    row = {
        "condition_id": cid,
        "question": "Will BTC close above 100k?",
        "market_slug": f"mkt-{cid}",
        "category": "Crypto",
        "market_type": "",
        "market_group": "",
        "series_title": "Bitcoin",
        "event_title": "Bitcoin price",
        "event_id": "ev-1",
        "event_slug": "ev-1",
        "tokens": [{"token_id": f"{cid}-yes"}, {"token_id": f"{cid}-no"}],
        "rewards": {"max_spread": 3.5, "min_size": 50},
        "minimum_tick_size": 0.01,
        "end_date_iso": (datetime.now(timezone.utc)
                         + timedelta(days=2)).isoformat(),
        "_order_min": 5,
        "_spread": 0.04,
        "_volume_24h": 250_000.0,
        "closed": False,
        "accepting_orders": True,
    }
    row.update(over)
    return row


def _spread_candidate(cid: str, event: str = "ev-1", **over) -> dict:
    row = _candidate(cid, market_group="Spread -3.5",
                     groupItemTitle="Spread -3.5",
                     event_id=event, event_slug=event)
    row.update(over)
    return row


def _eligible(cid: str, event: str, role: str, ret: float,
              shipped_reason: str = "") -> dict:
    return {
        "cid": cid,
        "title": f"Market {cid}",
        "slug": f"mkt-{cid}",
        "series_title": "S",
        "event_title": f"Event {event}",
        "event_id": event,
        "event_slug": event,
        "eligible": True,
        "return_pct_day": ret,
        "shipped_identity_reason": shipped_reason,
        "admission_role": role,
    }


def _refused(cid: str, event: str, reason: str, group: str = "") -> dict:
    return {
        "cid": cid,
        "title": f"Market {cid}",
        "slug": f"mkt-{cid}",
        "event_id": event,
        "event_slug": event,
        "market_group": group,
        "eligible": False,
        "reject_reason": reason,
    }


def test_default_path_bytes_unchanged():
    row = evaluate(_GoodSession(), 5.0,
                   _spread_candidate("0xgrp"), 250_000.0)
    assert row == {
        "source": "spread", "eligible": False,
        "reject_reason": 'carries a submarket group label (groupItemTitle "Spread -3.5")',
        "cid": "0xgrp",
        "title": "Will BTC close above 100k?"[:90],
        "slug": "mkt-0xgrp",
        "fetch_truncated": False,
    }
    assert "trial_arm" not in row
    assert "family" not in row
    assert "admission_role" not in row
    assert "shipped_identity_reason" not in row


def test_default_identity_decisions_unchanged():
    refused = evaluate(_GoodSession(), 5.0,
                       _spread_candidate("0xg2"), 250_000.0)
    assert refused["eligible"] is False
    assert refused["reject_reason"].startswith(
        "carries a submarket group label")
    ok = evaluate(_GoodSession(), 5.0, _candidate("0xok"), 250_000.0)
    assert ok["eligible"] is True


def test_trial_continues_past_relaxable_refusal():
    row = evaluate(_GoodSession(), 5.0,
                   _spread_candidate("0xrel"), 250_000.0,
                   admission_trial=True)
    assert row["eligible"] is True
    assert row["shipped_identity_reason"].startswith(
        "carries a submarket group label")
    assert row["admission_role"] == "submarket"


def test_trial_mainline_role_and_empty_shipped_reason():
    row = evaluate(_GoodSession(), 5.0, _candidate("0xmain"), 250_000.0,
                   admission_trial=True)
    assert row["eligible"] is True
    assert row["shipped_identity_reason"] == ""
    assert row["admission_role"] == "mainline"


def test_control_equals_shipped_selection():
    rows = [
        _eligible("0xa", "ev-1", "mainline", 3.0),
        _eligible("0xb", "ev-2", "submarket", 5.0,
                  shipped_reason="carries a submarket group label"),
        _eligible("0xc", "ev-3", "mainline", 1.0),
    ]
    bundle = build_paired_admission_bundle(rows, top=10,
                                           volume_gate_usd=125_000.0,
                                           snapshot_id="s1", ranked_at=1.0)
    assert [r["cid"] for r in bundle["control"]] == ["0xa", "0xc"]
    for r in bundle["control"]:
        assert r["trial_arm"] == "control"
        assert r["arm"] == "control"


def test_treatment_one_market_per_event():
    rows = [
        _eligible("0xmoney", "ev-1", "mainline", 2.0),
        _eligible("0xspread", "ev-1", "submarket", 9.0,
                  shipped_reason="carries a submarket group label"),
    ]
    bundle = build_paired_admission_bundle(rows, top=10,
                                           volume_gate_usd=125_000.0,
                                           snapshot_id="s1", ranked_at=1.0)
    picked = [r["cid"] for r in bundle["treatment"]]
    assert picked == ["0xmoney"]
    assert bundle["treatment"][0]["admission_role"] == "mainline"


def test_fallback_when_mainline_decided_mid():
    eligible = [_eligible("0xsub", "ev-1", "submarket", 4.0,
                          shipped_reason="carries a submarket group label")]
    refused = [_refused("0xmain", "ev-1", "YES: decided mid 0.95 outside [0.20, 0.80]")]
    bundle = build_paired_admission_bundle(eligible, refused=refused, top=10,
                                           volume_gate_usd=125_000.0,
                                           snapshot_id="s1", ranked_at=1.0)
    (row,) = bundle["treatment"]
    assert row["cid"] == "0xsub"
    assert row["admission_role"] == "fallback"
    assert "decided mid" in row["fallback_reason"]


def test_fallback_when_mainline_refused_by_depth():
    eligible = [_eligible("0xsub", "ev-1", "submarket", 4.0,
                          shipped_reason="carries a submarket group label")]
    refused = [_refused("0xmain", "ev-1",
                        "YES: top-3 bid depth $100.00 <= $500.00")]
    bundle = build_paired_admission_bundle(eligible, refused=refused, top=10,
                                           volume_gate_usd=125_000.0,
                                           snapshot_id="s1", ranked_at=1.0)
    (row,) = bundle["treatment"]
    assert row["admission_role"] == "fallback"
    assert "top-3 bid depth" in row["fallback_reason"]


def test_fallback_when_mainline_absent():
    eligible = [_eligible("0xsub", "ev-1", "submarket", 4.0,
                          shipped_reason="carries a submarket group label")]
    bundle = build_paired_admission_bundle(eligible, top=10,
                                           volume_gate_usd=125_000.0,
                                           snapshot_id="s1", ranked_at=1.0)
    (row,) = bundle["treatment"]
    assert row["admission_role"] == "fallback"
    assert row["fallback_reason"] == "no mainline member in universe"


def test_at_most_one_fallback_per_event():
    rows = [
        _eligible("0xs1", "ev-1", "submarket", 6.0,
                  shipped_reason="carries a submarket group label"),
        _eligible("0xs2", "ev-1", "submarket", 4.0,
                  shipped_reason="carries a submarket group label"),
    ]
    bundle = build_paired_admission_bundle(rows, top=10,
                                           volume_gate_usd=125_000.0,
                                           snapshot_id="s1", ranked_at=1.0)
    assert [r["cid"] for r in bundle["treatment"]] == ["0xs1"]


def test_blocked_keyword_stays_refused():
    m = _spread_candidate("0xblk", question="Game 1 winner: A vs B",
                          market_slug="game-1-a-vs-b")
    row = evaluate(_GoodSession(), 5.0, m, 250_000.0, admission_trial=True)
    assert row["eligible"] is False
    assert row["reject_reason"] == "blocked dynamic/submarket keyword"
    m2 = _spread_candidate("0xblk2", groupItemTitle="Game 1",
                           market_group="Game 1")
    row2 = evaluate(_GoodSession(), 5.0, m2, 250_000.0, admission_trial=True)
    assert row2["eligible"] is False
    assert row2["reject_reason"] == "blocked dynamic/submarket keyword"


def test_resolved_and_unreadable_stay_refused():
    m = _candidate("0xclosed", closed=True)
    row = evaluate(_GoodSession(), 5.0, m, 250_000.0, admission_trial=True)
    assert row["eligible"] is False
    assert "closed" in row["reject_reason"]
    m2 = _candidate("0xunread")
    row2 = evaluate(_DeadBookSession(), 5.0, m2, 250_000.0,
                    admission_trial=True)
    assert row2["eligible"] is False
    assert "book fetch failed" in row2["reject_reason"]


def test_treatment_rows_tagged():
    rows = [_eligible("0xa", "ev-9", "mainline", 3.0)]
    bundle = build_paired_admission_bundle(rows, top=10,
                                           volume_gate_usd=125_000.0,
                                           snapshot_id="snap-7", ranked_at=1.0)
    (row,) = bundle["treatment"]
    assert row["trial_arm"] == "treatment"
    assert row["arm"] == "treatment"
    assert row["trial_axis"] == "admission"
    assert row["snapshot_id"] == "snap-7"
    assert row["event_cluster_id"] == "gamma-event:ev-9"
    assert row["family"] == family_key("Market 0xa", "mkt-0xa",
                                       "S", "Event ev-9")
    assert row["admission_role"] == "mainline"


def test_bundle_single_snapshot():
    rows = [
        _eligible("0xa", "ev-1", "mainline", 3.0),
        _eligible("0xb", "ev-2", "submarket", 5.0,
                  shipped_reason="carries a submarket group label"),
    ]
    bundle = build_paired_admission_bundle(rows, top=10,
                                           volume_gate_usd=125_000.0,
                                           snapshot_id="snap-7", ranked_at=1.0)
    assert bundle["format"] == "spread_hunter.paired-admission.v1"
    assert bundle["snapshot_id"] == "snap-7"
    for row in bundle["control"] + bundle["treatment"]:
        assert row["snapshot_id"] == "snap-7"
    assert bundle["counts"]["control_selected"] == len(bundle["control"])
    assert bundle["counts"]["treatment_selected"] == len(bundle["treatment"])


def test_refused_swap_writes_no_audit_line(tmp_path, monkeypatch):
    import scripts.filter_markets as fm

    bundle = build_paired_admission_bundle(
        [_eligible("0xa", "ev-1", "mainline", 3.0)], top=10,
        volume_gate_usd=125_000.0, snapshot_id="s1", ranked_at=1.0)
    monkeypatch.setattr(fm, "_publish_json", lambda *a, **k: False)
    assert fm._publish_paired_bundle(tmp_path, bundle) is False
    assert not (tmp_path / "paired_admission_audit.jsonl").exists()

    monkeypatch.setattr(fm, "_publish_json", lambda *a, **k: True)
    assert fm._publish_paired_bundle(tmp_path, bundle) is True
    audit = (tmp_path / "paired_admission_audit.jsonl").read_text(
        encoding="utf-8")
    assert '"snapshot_id": "s1"' in audit


def test_incomplete_listing_and_missing_identity_refuse():
    import scripts.filter_markets as fm

    with pytest.raises(SystemExit):
        fm._require_complete_listing({"truncated": True}, "admission")
    fm._require_complete_listing({"truncated": False}, "admission")
    with pytest.raises(SystemExit):
        fm._require_event_identity([{"cid": "0x?"}], "admission")
    fm._require_event_identity(
        [_eligible("0xa", "ev-1", "mainline", 3.0)], "admission")


def test_cli_rejections(tmp_path):
    out = str(tmp_path / "t")
    with pytest.raises(SystemExit):
        parse_args(["--paired-admission"])
    with pytest.raises(SystemExit):
        parse_args(["--paired-admission", "--out-dir", str(RUN)])
    with pytest.raises(SystemExit):
        parse_args(["--paired-admission", "--out-dir", out, "--dry-run"])
    with pytest.raises(SystemExit):
        parse_args(["--paired-admission", "--out-dir", out,
                    "--paired-depth-control-usd", "1000",
                    "--trial-depth", "500"])
    with pytest.raises(SystemExit):
        parse_args(["--paired-admission", "--out-dir", out,
                    "--trial-volume", "50000"])
    with pytest.raises(SystemExit):
        parse_args(["--paired-admission", "--out-dir", out,
                    "--trial-spread", "0.10"])
    with pytest.raises(SystemExit):
        parse_args(["--paired-admission", "--out-dir", out,
                    "--legacy-rewards"])
    args = parse_args(["--paired-admission", "--out-dir", out])
    assert args.paired_admission is True


def _adm_spec(cid, arm="treatment", role="mainline", event="ev-1",
              snapshot="s1"):
    return {
        "cid": cid,
        "trial_arm": arm, "arm": arm,
        "trial_axis": "admission",
        "snapshot_id": snapshot,
        "event_cluster_id": f"gamma-event:{event}",
        "family": f"fam-{event}",
        "admission_role": role,
        "event_id": event, "event_slug": event,
        "event_title": f"Event {event}",
        "slug": f"mkt-{cid}",
    }


def _adm_db(path):
    import sqlite3

    from core_brain.paired_shadow import ensure_paired_shadow_tables

    ensure_paired_shadow_tables(path)
    return sqlite3.connect(path)


def test_family_label_persists_admission_to_order_to_completion(tmp_path):
    import sqlite3

    from core_brain.paired_shadow import (
        copy_paired_order_attribution,
        record_paired_market_selection,
        record_paired_order_attribution,
        record_paired_run_start,
    )

    db = tmp_path / "adm.db"
    _adm_db(db).close()
    record_paired_run_start(
        db, run_id="r1", arm="treatment", cutoff_usd=None,
        starting_bankroll_usd=100.0, started_at=1.0, planned_minutes=5.0,
        trial_axis="admission")
    record_paired_market_selection(
        db, run_id="r1", arm="treatment", cutoff_usd=None,
        spec=_adm_spec("0xadm"))
    record_paired_order_attribution(
        db, run_id="r1", local_id="loc-1", condition_id="0xadm",
        pair_id="p1")
    copy_paired_order_attribution(
        db, run_id="r1", pair_id="p1", local_id="loc-2",
        condition_id="0xadm")

    with sqlite3.connect(db) as conn:
        adm = conn.execute(
            "SELECT family_label, admission_role FROM shadow_paired_admissions"
        ).fetchone()
        orders = conn.execute(
            "SELECT local_id, family_label, admission_role "
            "FROM shadow_paired_orders ORDER BY local_id").fetchall()
    assert tuple(adm) == ("fam-ev-1", "mainline")
    assert [tuple(r) for r in orders] == [
        ("loc-1", "fam-ev-1", "mainline"),
        ("loc-2", "fam-ev-1", "mainline"),
    ]


def test_old_store_opens_after_additive_columns(tmp_path):
    import sqlite3

    from core_brain.paired_shadow import (
        ensure_paired_shadow_tables,
        record_paired_market_selection,
        record_paired_run_start,
    )

    db = tmp_path / "old.db"
    with sqlite3.connect(db) as conn:
        conn.executescript(
            """
            CREATE TABLE shadow_paired_runs (
                run_id TEXT PRIMARY KEY,
                arm TEXT NOT NULL,
                cutoff_usd REAL NOT NULL,
                starting_bankroll_usd REAL NOT NULL,
                started_at REAL NOT NULL,
                planned_minutes REAL NOT NULL,
                finished_at REAL,
                status TEXT NOT NULL DEFAULT 'running'
            );
            CREATE TABLE shadow_paired_admissions (
                run_id TEXT NOT NULL,
                condition_id TEXT NOT NULL,
                snapshot_id TEXT NOT NULL,
                arm TEXT NOT NULL,
                cutoff_usd REAL NOT NULL,
                event_cluster_id TEXT NOT NULL DEFAULT '',
                event_cluster_source TEXT NOT NULL DEFAULT 'missing',
                event_title TEXT NOT NULL DEFAULT '',
                market_slug TEXT NOT NULL DEFAULT '',
                selected_at REAL NOT NULL,
                PRIMARY KEY (run_id, condition_id, snapshot_id)
            );
            CREATE TABLE shadow_paired_orders (
                local_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                condition_id TEXT NOT NULL,
                pair_id TEXT,
                arm TEXT NOT NULL,
                cutoff_usd REAL NOT NULL,
                snapshot_id TEXT NOT NULL,
                event_cluster_id TEXT NOT NULL DEFAULT '',
                event_cluster_source TEXT NOT NULL DEFAULT 'missing',
                event_title TEXT NOT NULL DEFAULT '',
                market_slug TEXT NOT NULL DEFAULT '',
                admitted_at REAL NOT NULL
            );
            """)
        conn.commit()
    ensure_paired_shadow_tables(db)

    with sqlite3.connect(db) as conn:
        cols = lambda t: {r[1] for r in conn.execute(
            f"PRAGMA table_info({t})").fetchall()}
        assert "trial_axis" in cols("shadow_paired_runs")
        assert "family_label" in cols("shadow_paired_admissions")
        assert "admission_role" in cols("shadow_paired_orders")

    record_paired_run_start(
        db, run_id="r1", arm="control", cutoff_usd=500.0,
        starting_bankroll_usd=100.0, started_at=1.0, planned_minutes=5.0)
    record_paired_market_selection(
        db, run_id="r1", arm="control", cutoff_usd=500.0,
        spec={"cid": "0xdepth",
              "paired_depth_arm": "control",
              "paired_depth_cutoff_usd": 500.0,
              "paired_depth_snapshot_id": "s1",
              "event_id": "ev-1"})


def test_guard_skips_second_fallback_while_first_held(tmp_path):
    import sqlite3

    from core_brain.paired_shadow import (
        PairedShadowError,
        record_paired_market_selection,
        record_paired_order_attribution,
        record_paired_run_start,
    )

    db = tmp_path / "guard.db"
    _adm_db(db).close()
    record_paired_run_start(
        db, run_id="r1", arm="treatment", cutoff_usd=None,
        starting_bankroll_usd=100.0, started_at=1.0, planned_minutes=5.0,
        trial_axis="admission")
    record_paired_market_selection(
        db, run_id="r1", arm="treatment", cutoff_usd=None,
        spec=_adm_spec("0xfirst", role="fallback"))
    record_paired_order_attribution(
        db, run_id="r1", local_id="loc-a", condition_id="0xfirst",
        pair_id="pA")

    with pytest.raises(PairedShadowError, match="fallback guard"):
        record_paired_market_selection(
            db, run_id="r1", arm="treatment", cutoff_usd=None,
            spec=_adm_spec("0xsecond", role="fallback"))
    with sqlite3.connect(db) as conn:
        events = conn.execute(
            "SELECT kind, detail FROM shadow_paired_feed_events").fetchall()
    assert any(k == "fallback_guard_skip" and "0xfirst" in d
               for k, d in events)


def test_guard_ignores_control_arm(tmp_path):
    from core_brain.paired_shadow import (
        record_paired_market_selection,
        record_paired_order_attribution,
        record_paired_run_start,
    )

    db = tmp_path / "guard-ctl.db"
    _adm_db(db).close()
    record_paired_run_start(
        db, run_id="r1", arm="control", cutoff_usd=None,
        starting_bankroll_usd=100.0, started_at=1.0, planned_minutes=5.0,
        trial_axis="admission")
    record_paired_market_selection(
        db, run_id="r1", arm="control", cutoff_usd=None,
        spec=_adm_spec("0xfirst", arm="control"))
    record_paired_order_attribution(
        db, run_id="r1", local_id="loc-a", condition_id="0xfirst",
        pair_id="pA")
    record_paired_market_selection(
        db, run_id="r1", arm="control", cutoff_usd=None,
        spec=_adm_spec("0xsecond", arm="control"))
