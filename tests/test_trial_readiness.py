"""Near-miss evidence becomes a trial decision (#87).

The ranker writes one line per rank to `runtime/near_misses.jsonl` (depth
rejects the allocator would have funded) and `runtime/volume_near_misses.jsonl`
(volume rejects carrying a measured volume). Nothing read them back, so the
live dashboard could show that markets were being refused but never that they
were being refused CONSISTENTLY — the precondition for loosening a bar on
purpose rather than on a hunch.

The four thresholds are the sim `?view=scan` page's, carried over unchanged so
both dashboards license a trial on the same evidence.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from core_brain.trial_readiness import (
    LOOKBACK_RANKS,
    MIN_SMALL_MARGIN,
    MIN_UNIQUE,
    depth_tracker,
    readiness,
    volume_tracker,
)

_HARNESS = Path(__file__).resolve().parent / "js" / "live_state_harness.cjs"

requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node is not installed on this host")


def _harness(script: str) -> dict:
    out = subprocess.run([shutil.which("node"), str(_HARNESS), script],
                         capture_output=True, text=True, check=True, encoding="utf-8")
    return json.loads(out.stdout)


DAY = 86400.0
T0 = 1_788_000_000.0


def _write(path: Path, rows: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def _green(cid: str, measured: float = 600.0, bar: float = 1000.0) -> dict:
    return {"cid": cid, "title": cid, "depth_measured": measured, "depth_bar": bar}


def _volume(cid: str, measured: float = 90_000.0, bar: float = 125_000.0) -> dict:
    return {"cid": cid, "title": cid, "volume_measured": measured, "volume_bar": bar}


def _ranks(days: float, markets: int, *, measured: float = 600.0,
           bar: float = 1000.0, populated_fraction: float = 1.0,
           ranks: int = 20) -> list[dict]:
    """`ranks` rank lines spread over `days`, carrying `markets` unique cids."""
    rows = []
    for i in range(ranks):
        ts = T0 + (days * DAY) * (i / max(1, ranks - 1))
        populated = i < int(round(ranks * populated_fraction))
        greens = []
        if populated:
            for j in range(markets):
                if (j % ranks) == (i % ranks) or ranks == 1 or markets <= ranks:
                    greens.append(_green(f"0x{j:04d}", measured, bar))
        rows.append({"ts": ts, "scored": 100, "rejected": 40,
                     "depth_unparsed": 0, "greens": greens})
    return rows


def test_a_healthy_population_reads_as_trial_ready(tmp_path):
    # Arrange — 30 markets, four days, every rank populated, all near the bar.
    log = _write(tmp_path / "near_misses.jsonl", _ranks(4.0, 30))

    # Act
    tracker = depth_tracker(log)

    # Assert
    assert tracker.ready is True
    assert tracker.blockers == ()
    assert tracker.unique_markets >= MIN_UNIQUE
    assert tracker.days == pytest.approx(4.0)


def test_one_afternoon_of_evidence_is_not_ready(tmp_path):
    # Arrange — the same population, all inside a few hours.
    log = _write(tmp_path / "near_misses.jsonl", _ranks(0.2, 30))

    # Act
    tracker = depth_tracker(log)

    # Assert
    assert tracker.ready is False
    assert any("needs 3d" in b for b in tracker.blockers)


def test_the_same_three_markets_rescanned_is_not_ready(tmp_path):
    # Arrange — plenty of days, almost no distinct markets.
    log = _write(tmp_path / "near_misses.jsonl", _ranks(5.0, 3))

    # Act
    tracker = depth_tracker(log)

    # Assert
    assert tracker.ready is False
    assert any("unique markets" in b for b in tracker.blockers)


def test_misses_by_an_order_of_magnitude_do_not_count_as_near(tmp_path):
    # Arrange — 30 markets over five days, every one at a hundredth of the bar.
    # That is evidence about the markets, not about where the bar belongs.
    log = _write(tmp_path / "near_misses.jsonl",
                 _ranks(5.0, 30, measured=10.0, bar=1000.0))

    # Act
    tracker = depth_tracker(log)

    # Assert
    assert tracker.small_margin == 0
    assert tracker.ready is False
    assert any("small-margin" in b for b in tracker.blockers)


def test_a_burst_in_a_few_ranks_is_not_stable(tmp_path):
    # Arrange — the population appears in a fifth of the ranks.
    log = _write(tmp_path / "near_misses.jsonl",
                 _ranks(5.0, 30, populated_fraction=0.2))

    # Act
    tracker = depth_tracker(log)

    # Assert
    assert tracker.ready is False
    assert any("of ranks" in b for b in tracker.blockers)


def test_a_missing_log_is_no_evidence_not_an_error(tmp_path):
    # Arrange / Act
    tracker = depth_tracker(tmp_path / "nothing-here.jsonl")

    # Assert
    assert tracker.ready is False
    assert tracker.ranks == 0
    assert tracker.blockers == ("no evidence recorded yet",)


def test_a_truncated_last_line_does_not_take_the_reader_down(tmp_path):
    # Arrange — the ranker is mid-append.
    log = tmp_path / "near_misses.jsonl"
    rows = _ranks(4.0, 30)
    log.write_text("\n".join(json.dumps(r) for r in rows) + "\n{\"ts\": 178800",
                   encoding="utf-8")

    # Act
    tracker = depth_tracker(log)

    # Assert — the complete lines still count.
    assert tracker.ranks == len(rows)
    assert tracker.ready is True


def test_only_the_lookback_window_is_read(tmp_path):
    # Arrange — more ranks than the window.
    log = _write(tmp_path / "near_misses.jsonl", _ranks(9.0, 30, ranks=LOOKBACK_RANKS + 30))

    # Act
    tracker = depth_tracker(log)

    # Assert
    assert tracker.ranks == LOOKBACK_RANKS


def test_the_volume_tracker_reads_its_own_measurements(tmp_path):
    # Arrange — the volume log has a different entry shape.
    rows = []
    for i in range(20):
        rows.append({
            "ts": T0 + 4 * DAY * (i / 19),
            "volume_unknown": 1,
            "volumes": [_volume(f"0x{j:04d}") for j in range(30)],
        })
    log = _write(tmp_path / "volume_near_misses.jsonl", rows)

    # Act
    tracker = volume_tracker(log)

    # Assert
    assert tracker.ready is True
    assert tracker.small_margin >= MIN_SMALL_MARGIN
    assert tracker.unparsed == 20


def test_the_banner_flags_whichever_gate_has_the_evidence(tmp_path):
    # Arrange — depth is ready, volume has nothing.
    depth_log = _write(tmp_path / "near_misses.jsonl", _ranks(4.0, 30))
    volume_log = tmp_path / "volume_near_misses.jsonl"

    # Act
    payload = readiness(depth_log, volume_log, now=T0)

    # Assert
    assert payload["trial_ready"] is True
    assert payload["ready_gates"] == ["depth"]
    assert payload["volume"]["ready"] is False
    assert payload["generated_ts"] == T0


def test_no_evidence_anywhere_is_not_trial_ready(tmp_path):
    # Arrange / Act
    payload = readiness(tmp_path / "a.jsonl", tmp_path / "b.jsonl", now=T0)

    # Assert
    assert payload["trial_ready"] is False
    assert payload["ready_gates"] == []


# --- the endpoint and the screener's gate fallbacks ------------------------

_STATIC = Path(__file__).resolve().parent.parent / "dashboard" / "static"


def test_the_endpoint_serves_both_trackers(tmp_path, monkeypatch):
    # Arrange
    from dashboard import server as srv

    depth_log = _write(tmp_path / "near_misses.jsonl", _ranks(4.0, 30))
    volume_log = tmp_path / "volume_near_misses.jsonl"
    monkeypatch.setattr(
        srv, "resolve_runtime_file",
        lambda name, **kw: depth_log if name == "near_misses.jsonl" else volume_log)

    # Act
    payload = srv.get_trial_readiness()

    # Assert
    assert payload["depth"]["ready"] is True
    assert payload["volume"]["ready"] is False
    assert payload["trial_ready"] is True


def test_the_screener_hero_falls_back_to_the_shipped_bars():
    # Arrange — the pre-2026-08-25 bars described a filter this repo no longer
    # runs, so a hero falling back to them reads as a gate change nobody made.
    app_js = (_STATIC / "app.js").read_text(encoding="utf-8")

    # Act / Assert
    assert "volume_gate_usd, 125000" in app_js
    assert "depth_gate_usd, 500" in app_js
    assert "volume_gate_usd || 250000" not in app_js
    assert "depth_gate_usd || 1000" not in app_js


def test_the_screener_has_the_ready_banner_slot():
    # The per-gate progress trackers were removed (Owner 2026-09-16): "DEPTH
    # gathering 3/8 markets" is progress nobody acts on. The banner survives
    # because TRIAL READY is the moment a decision exists, and the full detail
    # is still on /api/trial-readiness.
    # Arrange / Act
    html = (_STATIC / "index.html").read_text(encoding="utf-8")
    app_js = (_STATIC / "app.js").read_text(encoding="utf-8")

    # Assert
    assert 'id="trial-ready-banner"' in html
    assert "renderTrialReadiness" in app_js


def test_a_failed_readiness_fetch_hides_the_banner():
    # Arrange -- the poll must call the renderer even when the fetch returned
    # nothing, or the last reading stays on screen as if it were current.
    app_js = (_STATIC / "app.js").read_text(encoding="utf-8")

    # Act / Assert -- called unconditionally.
    assert "if (trialReadiness) {" + chr(92) + "n      renderTrialReadiness" not in app_js
    assert "renderTrialReadiness(trialReadiness);" in app_js


@requires_node
def test_the_banner_shows_only_on_a_real_ready_verdict():
    # Behaviour, not source text: a dead fetch, a not-ready verdict and a
    # "ready" with no gates named all leave the header empty.
    v = _harness("trialbanner")
    assert v["failedFetch"] == "none"
    assert v["notReady"] == "none"
    assert v["readyNoGates"] == "none"
    assert v["ready"] == ""
    assert v["readyText"] == "TRIAL READY: DEPTH"
