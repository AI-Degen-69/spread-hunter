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


def _slice(js: str, start_marker: str, end_markers: tuple[str, ...]) -> str:
    """Return the text from start_marker up to the first end marker, with no
    fixed length. A fragile char-count slice drifts as markup grows (#586)."""
    start = js.find(start_marker)
    assert start != -1, f"start marker {start_marker!r} not found in app.js"
    rest = js[start:]
    ends = [rest.find(m) for m in end_markers if rest.find(m) != -1]
    end = min(ends) if ends else len(rest)
    return rest[:end]


def test_dropdown_row_builder_emits_value_only():
    js = _js()
    # Status only (#602): the dropdown row's name column identifies the service,
    # so the row pill carries just the colored dot + state value, no label. It is
    # still built by the single renderPillHtml template, never hand-rolled.
    seg = _slice(js, "let rowTitle", ("}).join",))
    assert "renderPillHtml({ value: state.toUpperCase(), dotState: state })" in seg, \
        "dropdown row pill must be status-only via renderPillHtml (no label)"
    assert "label: pillTag" not in seg, "dropdown row pill must not carry a name label"


def test_dropdown_row_dot_has_separator():
    js = _js()
    # Scope to the dropdown row builder so the check is about that code.
    seg = _slice(js, "let rowTitle", ("}).join",))
    # No hand-rolled dot variant may survive alongside the canonical call.
    assert "${dot}<span" not in seg, "dropdown liveness dot emitted with no separator"
    assert "pulse-dot ${state" not in seg, "no hand-rolled dot markup: renderPillHtml owns the dot"


def test_dropdown_meta_never_echoes_state():
    js = _js()
    # #602: the meta column is genuine detail only; it must never restate the
    # pill's state word (stopped/running), which the pill already shows.
    seg = _slice(js, "let state, meta, pid", ("}).join",))
    assert "meta = 'stopped'" not in seg, "meta must not echo the STOPPED state"
    assert "meta = 'running'" not in seg, "meta must not echo the RUNNING state"
    assert "|| 'running'" not in seg, "meta fallback must not echo the RUNNING state"


def test_switcher_rows_use_pill_grammar():
    js = _js()
    # Each slice ends at the template literal's closing backtick, not a byte count.
    run_seg = _slice(js, "const runState", ("</div>`;",))
    assert 'class="pill state-' in run_seg, "switcher run row missing .pill shell"
    assert 'class="pill-label"' in run_seg, "switcher run row missing .pill-label"
    assert 'class="pill-value"' in run_seg, "switcher run row missing .pill-value"
    demo_seg = _slice(js, "const demoState", ("return `<div", "</div>`;"))
    demo_seg = _slice(js, "const demoState", ("</div>`;",))
    assert 'class="pill-value"' in demo_seg, "demo row missing value span"

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

def test_static_fallback_rows_are_status_only():
    # #602: dropdown rows are status-only. The pill carries just .pill-value (the
    # state); the name column identifies the service. No .pill-label in the pill.
    html = _html()
    ids = ("hud-guardrail-pill", "market-scan-pill", "scan-state-pill", "hud-shadow-pill")
    for pill_id in ids:
        start = html.find(f'id="{pill_id}"')
        assert start != -1, f"{pill_id} fallback pill not found"
        end = html.find("services-dropdown-name", start)
        assert end != -1, f"{pill_id} row has no name column"
        slice_ = html[start:end]
        assert 'class="pill-value"' in slice_, f"{pill_id} must carry .pill-value"
        assert 'class="pill-label"' not in slice_, \
            f"{pill_id} must NOT carry .pill-label: the name column identifies the service (#602)"

# -- Grid + ellipsis + tint rules --

def test_dropdown_row_is_grid_with_fixed_columns():
    css = _css()
    m = re.search(r"\.services-dropdown-row\s*\{([^}]*)\}", css, re.DOTALL)
    assert m, ".services-dropdown-row rule missing"
    assert "grid-template-columns: 100px 1fr 120px" in m.group(1), \
        "dropdown row must be a 100px / 1fr / 120px grid (#602: pill is status-only)"


def test_dropdown_pills_share_one_width():
    # #600: every pill box fills the fixed 140px column so WATCHDOG/SCAN/ENGINE/
    # DECIDE/SHADOW share one right edge and stack in a clean aligned column.
    css = _css()
    m = re.search(r"\.services-dropdown-row \.pill\s*\{([^}]*)\}", css, re.DOTALL)
    assert m, ".services-dropdown-row .pill rule missing"
    assert "width: 100%" in m.group(1), \
        "dropdown pill must fill its column so all pills are the same width"


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
    assert "max-width: 120px" in body, "meta slot must be width-bounded"

def test_mode_stopped_value_uses_state_gray():
    css = _css()
    shadow = re.search(r"\.pill\.mode-shadow\.state-stopped\s*\{([^}]*)\}", css, re.DOTALL)
    live = re.search(r"\.pill\.mode-live\.state-stopped\s*\{([^}]*)\}", css, re.DOTALL)
    assert shadow and live, "mode stopped rules missing"
    assert "#f59e0b" not in shadow.group(1), "shadow-stopped text must not be mode-amber"
    assert "#10b981" not in live.group(1), "live-stopped text must not be mode-emerald"
    assert "var(--text-secondary)" in shadow.group(1)
    assert "var(--text-secondary)" in live.group(1)
# -- Shadow freshness (stale-node swap must not freeze unrendered pills) --

def test_shadow_and_guardrail_pills_not_preserved_as_stale_nodes():
    js = _js()
    m = re.search(r"telemetryPillIds\s*=\s*\[(.*?)\]", js, re.DOTALL)
    assert m, "telemetryPillIds list missing"
    ids = m.group(1)
    assert "hud-shadow-pill" not in ids, "hud-shadow-pill has no dedicated renderer; preserving it freezes the static UNKNOWN fallback"
    assert "hud-guardrail-pill" not in ids, "hud-guardrail-pill has no dedicated renderer either"
    assert "market-scan-pill" in ids, "SCAN is live-rendered and must be preserved across rebuilds"
    assert "scan-state-pill" in ids, "ENGINE is live-rendered and must be preserved across rebuilds"
