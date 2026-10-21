"""Regression tests for the Data & Markets Kanban two-lane redesign and sidebar sub-tabs."""
from pathlib import Path
import json
import re
import shutil
import subprocess

import pytest

_ROUTE_SKIP = pytest.mark.skipif(shutil.which("node") is None,
                                 reason="node is not installed on this host")

_ROOT = Path(__file__).resolve().parent.parent
_STATIC = _ROOT / "dashboard" / "static"


def test_prototype_data_markets_has_sub_items():
    js = (_STATIC / "prototype.js").read_text(encoding="utf-8")
    assert "subItems:" in js
    assert "id: 'regular'" in js
    assert "label: 'Regular Markets'" in js
    assert "id: 'live'" in js
    assert "label: 'Live Sports & eSports'" in js


def test_prototype_renders_sub_nav_and_click_handler():
    js = (_STATIC / "prototype.js").read_text(encoding="utf-8")
    assert "proto-sub-nav" in js
    assert "proto-sub-nav-btn" in js
    assert "window.setKanbanLane" in js
    assert "window.updateSidebarSubNav" in js


def test_screener_header_has_lane_switcher_markup():
    html = (_STATIC / "index.html").read_text(encoding="utf-8")
    assert 'id="kanban-lane-switcher"' in html
    assert 'data-lane="regular"' in html
    assert 'data-lane="live"' in html
    assert 'data-lane="all"' in html
    assert 'id="kanban-lane-banner"' in html


def test_app_js_lane_helpers_and_heroes():
    js = (_STATIC / "app.js").read_text(encoding="utf-8")
    assert "function rowLane(m) {" in js
    assert "function inLane(m, lane) {" in js
    # The display must never guess a lane from row flags again (#744).
    assert "function isLiveMarket(" not in js
    assert "window.setKanbanLane = function" in js
    assert "updateLaneBanner(" in js
    assert "VOLATILITY EXEMPT" in js
    assert "LIVE PRIORITY BOOST" in js or "LIVE QUOTING FLEET" in js


def test_css_lane_styling():
    styles = (_STATIC / "styles.css").read_text(encoding="utf-8")
    assert ".kanban-lane-switcher" in styles
    assert ".lane-tab-btn" in styles
    assert ".kanban-lane-banner" in styles
    assert ".market-card.live-card" in styles

    proto_css = (_STATIC / "prototype.css").read_text(encoding="utf-8")
    assert ".proto-sub-nav" in proto_css
    assert ".proto-sub-nav-btn" in proto_css


def test_kanban_scroll_preservation_and_fingerprint():
    js = (_STATIC / "app.js").read_text(encoding="utf-8")
    # Verify fingerprint does not include snapshot_age
    assert "funnel?.snapshot_age" not in js.split("const boardFingerprint = JSON.stringify([")[1].split("]);")[0]
    # Verify bucket vertical scrolls and window scrolls are captured and restored
    assert "prevBucketScrolls" in js
    assert "prevWindowScroll" in js
    assert "body.scrollTop = prevBucketScrolls[key]" in js


def test_screener_header_filter_service_pill_and_no_engine_label():
    html = (_STATIC / "index.html").read_text(encoding="utf-8")
    assert 'id="screener-filter-pill"' in html
    assert 'MARKET FILTER' in html
    # The raw misleading "ENGINE" label is removed from screener header
    screener_sec = html.split('id="screener-header"')[1].split('id="kanban-carousel-container"')[0]
    assert ">ENGINE<" not in screener_sec

    js = (_STATIC / "app.js").read_text(encoding="utf-8")
    assert "screener-filter-pill" in js
    assert "Service Inactive" in js
    assert "Scan overdue" in js


def test_row_lane_classification():
    """The row's own `lane` decides; the display never re-guesses from flags."""
    if shutil.which("node") is None:
        pytest.skip("node is not installed on this host")

    js = (_STATIC / "app.js").read_text(encoding="utf-8")
    fn_code = ("function rowLane(m) {" + js.split("function rowLane(m) {")[1]
               .split("const KANBAN_LANE_STORAGE_KEY")[0])
    script = f"""
    {fn_code}

    const cases = [
        {{ m: {{ lane: 'live' }}, expected: 'live' }},
        // The ranker's stamp outranks any stale flag.
        {{ m: {{ lane: 'regular', _live_event: true, event_period: '2H' }}, expected: 'regular' }},
        {{ m: {{ lane: 'live', event_live: false }}, expected: 'live' }},
        {{ m: {{ lane: 'unknown' }}, expected: 'unknown' }},
        // No lane and only flags: not guessed into a lane.
        {{ m: {{ event_live: true }}, expected: 'unknown' }},
        {{ m: {{ volatility_exempt: true }}, expected: 'unknown' }},
        {{ m: {{ event_score: '2-0' }}, expected: 'unknown' }},
        {{ m: {{}}, expected: 'unknown' }},
        {{ m: null, expected: 'unknown' }}
    ];

    for (const c of cases) {{
        const actual = rowLane(c.m);
        if (actual !== c.expected) {{
            console.error('Mismatch for', JSON.stringify(c.m), 'expected', c.expected, 'got', actual);
            process.exit(1);
        }}
    }}
    // All admits every row; a lane admits only its own.
    const laneChecks = [
        [rowLane({{ lane: 'live' }}) === 'live', 'live row is live'],
        [rowLane({{ lane: 'regular' }}) === 'regular', 'regular row is regular']
    ];
    for (const [ok, label] of laneChecks) {{
        if (!ok) {{ console.error('lane check failed:', label); process.exit(1); }}
    }}
    console.log('ALL_PASSED');
    """
    res = subprocess.run([shutil.which("node"), "-e", script],
                         capture_output=True, text=True, check=True)
    assert "ALL_PASSED" in res.stdout


# --- rendered lane numbers (#744) -------------------------------------------


def _run_lane_harness(funnel: dict) -> dict:
    harness = Path(__file__).resolve().parent / "js" / "input_lag_harness.cjs"
    kpi = {"portfolio": {"account": {"account_value_usd": 100}}, "funnel": funnel}
    out = subprocess.run(
        [shutil.which("node"), str(harness),
         json.dumps({"scenario": "lane-numbers", "kpi": kpi})],
        capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(out.stdout)


def _lane_fixture(lanes: bool = True) -> dict:
    """The 2026-10-21 run: 137 scored globally, 5 live, one WTA main line."""
    global_counts = {"funded": 0, "spread_universe": 137, "attempted": 137,
                     "scored": 137, "dropped_no_verdict": 0, "rejected": 132,
                     "eligible": 5, "picked": 5}
    live_counts = {"funded": 0, "spread_universe": 5, "attempted": 5,
                   "scored": 5, "dropped_no_verdict": 0, "rejected": 4,
                   "eligible": 1, "picked": 1}
    regular_counts = {"funded": 0, "spread_universe": 132, "attempted": 132,
                      "scored": 132, "dropped_no_verdict": 0, "rejected": 128,
                      "eligible": 4, "picked": 4}
    wta = {
        "condition_id": "0xwta", "slug": "wta-buyukak-klimovi-2026-10-05",
        "title": "Samsun: Cagla Buyukakcay vs Linda Klimovicova",
        "lane": "live", "event_live": True, "event_period": "S3",
        "event_score": "6-3, 6-7(2-7), 0-3", "volatility_exempt": True,
        "volume": 348643.0, "days_to_resolve": 0.4, "source": "spread",
        "fills": 0, "pnl": 0.0,
    }
    regular = {
        "condition_id": "0xbtc", "slug": "btc-above-100k",
        "title": "Bitcoin above 100k?", "lane": "regular",
        "volume": 500000.0, "days_to_resolve": 6.0, "source": "spread",
        "fills": 0, "pnl": 0.0,
    }
    funnel = {
        "ts": 1.0, "snapshot_age": 30.0, "census": "", "gates": "",
        "counts": global_counts,
        "filters": [
            {"cause": "volume", "n": 128, "would_fund": 0, "traps": 0,
             "examples": [{"title": "Low vol market", "slug": "low-vol",
                           "reason": "24h volume $10 < $250,000",
                           "lane": "regular"}]},
            {"cause": "movement", "n": 4, "would_fund": 0, "traps": 0,
             "examples": [{"title": "Flat tape", "slug": "flat",
                           "reason": "no movement in window", "lane": "live"}]},
        ],
        "graduated": [wta, regular],
        "final": [wta, regular],
        "picked": [wta, regular],
        "raw": {
            "rewards": [],
            "spread": [
                {"title": wta["title"], "slug": wta["slug"], "volume": 348643,
                 "spread": 0.01, "days": 0.4, "lane": "live"},
                {"title": regular["title"], "slug": regular["slug"],
                 "volume": 500000, "spread": 0.02, "days": 6, "lane": "regular"},
            ],
        },
        "discovery": None,
        "movement_gate_usd": 200.0, "volume_gate_usd": 250000.0,
        "depth_gate_usd": 1000.0, "spread_gate": 0.0205,
        "horizon_gate_days": 30.0,
    }
    if lanes:
        funnel["lanes"] = {
            "live": {"counts": live_counts, "filters": [funnel["filters"][1]]},
            "regular": {"counts": regular_counts, "filters": [funnel["filters"][0]]},
        }
    return funnel


@_ROUTE_SKIP
def test_the_rendered_live_lane_is_decided_by_the_row_lane():
    # Arrange: the WTA main line is in play and stamped live.
    res = _run_lane_harness(_lane_fixture())

    # Assert: it renders in Live and never in Regular or the regular raw bucket.
    assert "Cagla Buyukakcay" in res["live"]["board"]
    assert "Cagla Buyukakcay" not in res["regular"]["board"]
    # All sees both, since it is the union the operator asked for.
    assert "Cagla Buyukakcay" in res["all"]["board"]
    assert "Bitcoin above 100k?" in res["regular"]["board"]
    # Pills count graduations by lane.
    assert res["live"]["livePill"] == "1"
    assert res["live"]["regularPill"] == "1"
    assert res["all"]["allPill"] == "2"


@_ROUTE_SKIP
def test_each_lane_numbers_match_that_lane_only():
    res = _run_lane_harness(_lane_fixture())
    live_board = res["live"]["board"]
    regular_board = res["regular"]["board"]

    # Ingestion badge and "to evaluate" are the LANE's candidates (5 / 132),
    # not the global 137 the old code painted on every lane.
    assert "5 CANDIDATES" in live_board
    assert "5 to evaluate" in live_board
    assert "132 CANDIDATES" in regular_board
    assert "137 CANDIDATES" not in live_board
    assert "137 CANDIDATES" not in regular_board
    # Gate badge, denominator and cards agree inside the lane: the live lane
    # refused 4 at movement and has the 1 live example for it.
    assert "4 REJECTED" in live_board
    assert "4 refused here" in live_board
    assert "of 5 scored" in live_board
    assert "no movement in window" in live_board
    assert "+ 3 more rejected markets" in live_board
    # ...and none of the regular lane's volume refusals leak into it.
    assert "24h volume $10" not in live_board
    assert "24h volume $10" in regular_board
    assert "128 REJECTED" in regular_board
    assert "of 132 scored" in regular_board
    # The passed count is the lane's own rendered list.
    assert "1 ACTIVE" in live_board
    assert "1 ACTIVE" in regular_board


@_ROUTE_SKIP
def test_a_lane_that_measured_zero_does_not_borrow_the_global_count():
    # The bug: `counts.scored || counts.attempted || funnel.raw_count` turned a
    # lane's measured zero into the global universe, so a lane with nothing in
    # it claimed "of 137 scored" -- the exact badge/card disagreement #744 is
    # about, only in the other direction.
    funnel = _lane_fixture()
    funnel["raw_count"] = 137
    funnel["lanes"]["live"] = {
        "counts": {"funded": 0, "spread_universe": 0, "attempted": 0,
                   "scored": 0, "dropped_no_verdict": 0, "rejected": 0,
                   "eligible": 0, "picked": 0},
        "filters": [],
    }
    res = _run_lane_harness(funnel)
    live_board = res["live"]["board"]

    assert "of 0 scored" in live_board
    assert "of 137 scored" not in live_board
    assert "137 CANDIDATES" not in live_board
    # All keeps the global numbers.
    assert "of 137 scored" in res["all"]["board"]


@_ROUTE_SKIP
def test_a_snapshot_without_lanes_says_so_instead_of_showing_global_numbers():
    res = _run_lane_harness(_lane_fixture(lanes=False))

    # An older artifact: the lane view admits it has no lane numbers rather
    # than borrowing All's (which is how a badge disagreed with its cards).
    assert "Lane numbers are not available for this snapshot" in res["live"]["board"]
    assert "137 CANDIDATES" not in res["live"]["board"]
    # All still renders that run from the global blocks.
    assert "137 CANDIDATES" in res["all"]["board"]
    # The pills are graduation-derived and survive either way.
    assert res["live"]["livePill"] == "1"


