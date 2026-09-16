"""The tape view is the dashboard's read-only window onto the price tape.

Journeys under test:
1. As the Owner, the page tells me how much tape has been collected, because a
   finding computed on a thin store is not yet an answer.
2. As the Owner, a store that does not exist yet renders a page that says so
   rather than an error, because the page is opened WHILE collection runs.
3. As the Owner, the grid reaches the page already answered -- the view never
   recomputes it, because loading the tape takes tens of seconds.
4. As the Owner, each cell arrives with the verdict already decided against the
   pre-registered bar, so the page cannot pick a friendlier threshold.
5. As the Owner, the production registry is refused by name, at every layer
   including the environment.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from core_brain.price_tape import DriftCell, TapeMarket, TapeStore, analyse
from core_brain.tape_view import (
    RefusedStore,
    resolve_tape_db,
    tape_findings,
    tape_status,
)


def _market(token_id="tok-up", **over):
    fields = {
        "token_id": token_id,
        "condition_id": "0xabc",
        "question": "Minnesota Twins vs. Chicago White Sox: O/U 8.5",
        "slug": "twins-white-sox-ou",
        "volume_24h": 470_372.0,
    }
    fields.update(over)
    return TapeMarket(**fields)


def _seeded(tmp_path, ticks=400):
    store = TapeStore(tmp_path / "price_tape.db")
    store.record_market(_market())
    store.append_ticks("tok-up", [(i * 60, 0.20 + 0.004 * i) for i in range(ticks)])
    return store


# ------------------------------------------------------------------ status


def test_status_reports_how_much_tape_exists(tmp_path):
    store = _seeded(tmp_path)

    status = tape_status(store.path)

    assert status["state"] == "READY"
    assert status["markets"] == 1
    assert status["ticks"] == 400
    assert status["resolved"] == 0
    assert status["span_days"] == pytest.approx(399 * 60 / 86400.0, rel=1e-3)


def test_a_missing_store_is_a_page_that_says_so(tmp_path):
    status = tape_status(tmp_path / "nothing.db")

    assert status["state"] == "MISSING"
    assert status["ticks"] == 0
    assert status["exists"] is False


def test_an_empty_store_is_not_an_error(tmp_path):
    store = TapeStore(tmp_path / "price_tape.db")

    status = tape_status(store.path)

    assert status["state"] == "EMPTY"
    assert status["ticks"] == 0


def test_status_counts_resolved_markets_separately(tmp_path):
    store = _seeded(tmp_path)
    store.record_market(_market(token_id="other-up", condition_id="0xother"))
    store.mark_resolved("other-up", True)

    assert tape_status(store.path)["resolved"] == 1


# ------------------------------------------------------------------ findings


def test_findings_are_served_already_computed(tmp_path):
    store = _seeded(tmp_path)
    analyse(store, cells=(DriftCell(0.03, 60, 60), DriftCell(0.05, 60, 240)))

    report = tape_findings(store.path)

    assert report["state"] == "READY"
    assert len(report["cells"]) == 2
    assert report["computed_at"] > 0


def test_findings_before_any_analysis_say_so(tmp_path):
    store = _seeded(tmp_path)

    report = tape_findings(store.path)

    assert report["state"] == "NOT_ANALYSED"
    assert report["cells"] == []


def test_each_cell_carries_its_verdict(tmp_path):
    store = _seeded(tmp_path)
    analyse(store, cells=(DriftCell(0.03, 60, 60),))

    cell = tape_findings(store.path)["cells"][0]

    assert cell["verdict"] in {"CONTINUES", "COMES_BACK", "NO_SIGNAL"}
    assert set(cell) >= {"label", "trigger_c", "lookback_min", "horizon_min",
                         "n", "mean_c", "t", "verdict"}


def test_a_cell_under_the_bar_is_no_signal(tmp_path):
    store = _seeded(tmp_path)
    analyse(store, cells=(DriftCell(0.03, 60, 60),))
    # rewrite the stored answer to a t the bar must reject
    findings = store.findings()
    store.replace_findings([type(findings[0])(
        label=findings[0].label, trigger=findings[0].trigger,
        lookback_min=findings[0].lookback_min, horizon_min=findings[0].horizon_min,
        n=500, mean=0.01, t=2.9, computed_at=findings[0].computed_at)])

    assert tape_findings(store.path)["cells"][0]["verdict"] == "NO_SIGNAL"


def test_the_bar_is_reported_so_the_page_cannot_invent_one(tmp_path):
    store = _seeded(tmp_path)
    analyse(store, cells=(DriftCell(0.03, 60, 60),))

    assert tape_findings(store.path)["significance_t"] == 3.0


def test_findings_report_their_own_age(tmp_path):
    store = _seeded(tmp_path)
    analyse(store, cells=(DriftCell(0.03, 60, 60),))

    now = int(analyse_now(store) + 9 * 3600)  # 9h after the compute stamp
    report = tape_findings(store.path, now_fn=lambda: now)

    assert report["grid_age_hours"] == pytest.approx(9.0, rel=1e-3)
    assert report["computed_at_h"].startswith("20")


def analyse_now(store):
    return store.findings()[0].computed_at


def test_findings_are_stale_when_ticks_were_appended_after_compute(tmp_path):
    store = _seeded(tmp_path)
    analyse(store, cells=(DriftCell(0.03, 60, 60),))
    compute_ts = analyse_now(store)
    store.append_ticks("tok-up", [(compute_ts + 600, 0.9)])

    report = tape_findings(store.path, now_fn=lambda: compute_ts + 3600)

    assert report["stale"] is True
    assert report["stale_reason"] == "NEW_TICKS"
    assert report["ticks_since_computed"] >= 1


def test_findings_are_stale_when_the_grid_is_old_even_without_new_ticks(tmp_path):
    store = _seeded(tmp_path)
    analyse(store, cells=(DriftCell(0.03, 60, 60),))
    compute_ts = analyse_now(store)

    report = tape_findings(store.path, now_fn=lambda: compute_ts + 8 * 86400)

    assert report["stale"] is True
    assert report["stale_reason"] == "GRID_AGE"


def test_a_fresh_grid_with_no_new_ticks_is_not_stale(tmp_path):
    store = _seeded(tmp_path)
    analyse(store, cells=(DriftCell(0.03, 60, 60),))
    compute_ts = analyse_now(store)

    report = tape_findings(store.path, now_fn=lambda: compute_ts + 1800)

    assert report["stale"] is False
    assert report["stale_reason"] is None


def test_findings_report_coverage_at_compute_time(tmp_path):
    store = _seeded(tmp_path)
    analyse(store, cells=(DriftCell(0.03, 60, 60),))
    compute_ts = analyse_now(store)
    store.append_ticks("tok-up", [(compute_ts + 600, 0.9)])

    report = tape_findings(store.path, now_fn=lambda: compute_ts + 3600)

    assert report["computed_over_tape_ticks"] == 400
    assert report["ticks_since_computed"] == 1
    assert report["last_tick_ts_h"].startswith("20")


def test_mean_is_reported_in_cents(tmp_path):
    store = _seeded(tmp_path)
    analyse(store, cells=(DriftCell(0.03, 60, 60),))

    cell = tape_findings(store.path)["cells"][0]

    assert cell["mean_c"] == pytest.approx(cell["mean"] * 100)


def test_findings_of_a_missing_store_are_not_an_error(tmp_path):
    report = tape_findings(tmp_path / "nothing.db")

    assert report["state"] == "MISSING"
    assert report["cells"] == []


# ------------------------------------------------------------------ refusal


def test_the_production_registry_is_refused_by_name():
    with pytest.raises(RefusedStore):
        resolve_tape_db("data/orders.db")


def test_the_production_registry_is_refused_from_the_environment(monkeypatch):
    monkeypatch.setenv("SHL_TAPE_DB", "some/where/orders.db")

    with pytest.raises(RefusedStore):
        resolve_tape_db(None)


def test_the_default_store_is_the_recorders_own(monkeypatch):
    monkeypatch.delenv("SHL_TAPE_DB", raising=False)

    assert resolve_tape_db(None).name == "price_tape.db"


def test_an_explicit_store_wins_over_the_default(tmp_path):
    path = tmp_path / "other_tape.db"

    assert resolve_tape_db(path) == Path(path)


# ------------------------------------------------------------------ URI safety
# `file:{path}?mode=ro` is string concatenation into a URI. A path holding `#`
# or `?` re-parses: the fragment swallows `mode=ro`, SQLite drops read-only,
# and it will happily create the truncated file it thinks it was asked for.


def test_a_store_whose_name_holds_a_fragment_is_still_read_only(tmp_path):
    store = TapeStore(tmp_path / "tape#v2.db")
    store.record_market(_market())
    store.append_ticks("tok-up", [(1_700_000_060, 0.47)])

    status = tape_status(store.path)

    assert status["state"] == "READY"
    assert status["ticks"] == 1


def test_a_read_only_open_never_creates_a_store(tmp_path):
    missing = tmp_path / "tape#nope.db"

    assert tape_status(missing)["state"] == "MISSING"
    assert not missing.exists(), "a read must not bring the file into being"


def test_a_store_whose_name_holds_a_query_marker_still_reads(tmp_path):
    store = TapeStore(tmp_path / "tape?v3.db") if os.name != "nt" else None
    if store is None:
        pytest.skip("Windows forbids '?' in a file name")
    store.record_market(_market())
    store.append_ticks("tok-up", [(1_700_000_060, 0.47)])

    assert tape_status(store.path)["ticks"] == 1
