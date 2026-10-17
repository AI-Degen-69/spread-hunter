"""Regression tests for the Data & Markets Kanban two-lane redesign and sidebar sub-tabs."""
from pathlib import Path
import json
import re

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
    assert "function isLiveMarket(" in js
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


def test_is_live_market_classification():
    import shutil
    import subprocess
    import pytest

    if shutil.which("node") is None:
        pytest.skip("node is not installed on this host")

    js = (_STATIC / "app.js").read_text(encoding="utf-8")
    fn_code = "function isLiveMarket(m) {" + js.split("function isLiveMarket(m) {")[1].split("const KANBAN_LANE_STORAGE_KEY")[0]
    script = f"""
    {fn_code}

    const cases = [
        {{ m: {{ category: 'Sports', question: 'Arsenal vs Chelsea', live_event: true }}, expected: true }},
        {{ m: {{ category: 'Soccer', question: 'Real Madrid vs Barcelona', in_play: true }}, expected: true }},
        {{ m: {{ category: 'eSports', question: 'T1 vs Gen.G', volatility_exempt: true }}, expected: true }},
        {{ m: {{ category: 'Soccer', question: 'PSG vs Bayern', event_period: '2H' }}, expected: true }},
        {{ m: {{ category: 'Sports', question: 'Lakers vs Warriors (Scheduled Dec 2026)', live: false }}, expected: false }},
        {{ m: {{ category: 'Politics', question: 'US Presidential Election 2028' }}, expected: false }},
        {{ m: {{ category: 'Crypto', question: 'Bitcoin above 100k?' }}, expected: false }},
        {{ m: null, expected: false }}
    ];

    for (const c of cases) {{
        const actual = isLiveMarket(c.m);
        if (actual !== c.expected) {{
            console.error('Mismatch for', JSON.stringify(c.m), 'expected', c.expected, 'got', actual);
            process.exit(1);
        }}
    }}
    console.log('ALL_PASSED');
    """
    res = subprocess.run([shutil.which("node"), "-e", script], capture_output=True, text=True, check=True)
    assert "ALL_PASSED" in res.stdout


