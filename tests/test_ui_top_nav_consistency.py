"""Issue #586: Unify top-nav pill grammar across header, services dropdown, and DB switcher.

Follow-up to #504 (#575). Pins one label/value/detail grammar on all three
top-nav surfaces: header pills, SERVICES dropdown rows, DB run-switcher rows.
Reads index.html, styles.css, and the row builders in app.js.
"""
from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "dashboard" / "static"
INDEX_HTML = STATIC / "index.html"
STYLES_CSS = STATIC / "styles.css"
APP_JS = STATIC / "app.js"

CONTEXT_SENTENCES = (
    "live only (shadow uses in-process client)",
    "shadow only (live uses venue engine)",
)


def _css() -> str:
    return STYLES_CSS.read_text(encoding="utf-8")


def _js() -> str:
    return APP_JS.read_text(encoding="utf-8")


def _html() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


# ── Label / value / detail roles ──

def test_header_pills_carry_label_and_value_spans():
    html = _html()
    for pill_id in ("master-status-indicator", "db-mode-badge", "hud-services-pill"):
        m = re.search(
            rf'id="{pill_id}"[^>]*>.*?<span class="pill-label">.*?</span>'
            rf'\s*<span[^>]*class="[^"]*pill-value[^"]*"[^>]*>',
            html, re.DOTALL,
        )
        assert m, f"{pill_id} must pair a .pill-label with a .pill-value"


def test_dropdown_row_builder_emits_label_and_value():
    js = _js()
    seg = js[js.find("const pillTag"):]
    seg = seg[:seg.find("});join('')")] if "});join('')" in seg else seg[:1200]
    assert 'class="pill-label"' in seg, "dropdown row pill missing .pill-label"
    assert 'class="pill-value"' in seg, "dropdown row pill missing .pill-value"


def test_dropdown_row_dot_has_separator():
    js = _js()
    assert "${dot}<span" not in js, "pulse-dot emitted with no separator before a span"
    assert "${dot} <span" in js, "pulse-dot must be followed by a space before the label span"


def test_switcher_rows_use_pill_grammar():
    js = _js()
    switcher = js[js.find("const runState"):][:600]
    assert 'class="pill state-' in switcher, "switcher run row missing .pill shell"
    assert 'class="pill-label"' in switcher, "switcher run row missing .pill-label"
    assert 'class="pill-value"' in switcher, "switcher run row missing .pill-value"
    demo = js[js.find("const demoState"):][:400]
    assert 'class="pill-label">DEMO' in demo, "demo row missing DEMO label pill"
    assert 'class="pill-value"' in demo, "demo row missing value span"

# -- Meta vocabulary + tooltip relocation --

def test_context_sentences_not_in_visible_meta():
    js = _js()
    for sentence in CONTEXT_SENTENCES:
        assert "meta = '" + sentence + "'" not in js, \
            f"{sentence!r} must not be assigned to the meta slot"

def test_context_sentences_ride_row_title():
    js = _js()
    assert "rowTitle += " in js, "context sentences must ride the row title tooltip"
    for sentence in CONTEXT_SENTENCES:
        assert sentence in js, f"{sentence!r} must survive on the row title tooltip"

def test_guardrail_alert_meta_preserved():
    js = _js()
    assert "alert${alertsTotal === 1 ? '' : 's'}" in js, "guardrail alerts meta must stay"
    assert "'auto-watch'" in js, "guardrail auto-watch meta must stay"

def test_static_fallback_rows_use_label_value_spans():
    html = _html()
    ids = ("hud-guardrail-pill", "market-scan-pill", "scan-state-pill", "hud-shadow-pill")
    for pill_id in ids:
        start = html.find(f'id="{pill_id}"')
        assert start != -1, f"{pill_id} fallback pill not found"
        end = html.find("services-dropdown-name", start)
        assert end != -1, f"{pill_id} row has no name column"
        slice_ = html[start:end]
        assert 'class="pill-label"' in slice_, f"{pill_id} must carry .pill-label"
        assert 'class="pill-value"' in slice_, f"{pill_id} must carry .pill-value"

# -- Grid + ellipsis + tint rules --

def test_dropdown_row_is_grid_with_fixed_columns():
    css = _css()
    m = re.search(r"\.services-dropdown-row\s*\{([^}]*)\}", css, re.DOTALL)
    assert m, ".services-dropdown-row rule missing"
    assert "grid-template-columns: 150px 1fr 140px" in m.group(1), \
        "dropdown row must be a 150px / 1fr / 140px grid"

def test_switcher_row_is_grid():
    css = _css()
    m = re.search(r"\.db-run-row\s*\{([^}]*)\}", css, re.DOTALL)
    assert m, ".db-run-row rule missing"
    assert "grid-template-columns" in m.group(1), "switcher row must be a grid"

def test_meta_slot_ellipsized():
    css = _css()
    m = re.search(r"\.services-dropdown-meta\s*\{([^}]*)\}", css, re.DOTALL)
    assert m, ".services-dropdown-meta rule missing"
    body = m.group(1)
    assert "text-overflow: ellipsis" in body, "meta slot must ellipsize"
    assert "white-space: nowrap" in body, "meta slot must not wrap"
    assert "max-width: 140px" in body, "meta slot must be width-bounded"

def test_mode_stopped_value_uses_state_gray():
    css = _css()
    shadow = re.search(r"\.pill\.mode-shadow\.state-stopped\s*\{([^}]*)\}", css, re.DOTALL)
    live = re.search(r"\.pill\.mode-live\.state-stopped\s*\{([^}]*)\}", css, re.DOTALL)
    assert shadow and live, "mode stopped rules missing"
    assert "#f59e0b" not in shadow.group(1), "shadow-stopped text must not be mode-amber"
    assert "#10b981" not in live.group(1), "live-stopped text must not be mode-emerald"
    assert "var(--text-secondary)" in shadow.group(1)
    assert "var(--text-secondary)" in live.group(1)
