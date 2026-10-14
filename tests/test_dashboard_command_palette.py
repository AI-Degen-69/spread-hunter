"""Command palette (#477): keyboard entry to pages, actions, and market search.

Cmd/Ctrl+K (or `/`) opens a palette over the dashboard: every sidebar page,
SYNC VENUE, RESET, and markets already loaded in the page. Driven through node
against the real dashboard/static/command-palette.js and a stub DOM — the
offline pattern of tests/js/prototype_nav_harness.cjs — so these are the keys
the page would actually see.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
HARNESS = Path(__file__).resolve().parent / "js" / "command_palette_harness.cjs"
REPO = Path(__file__).resolve().parent.parent
PALETTE_JS = REPO / "dashboard" / "static" / "command-palette.js"
INDEX_HTML = REPO / "dashboard" / "static" / "index.html"


def _run(fixture=None, actions=None) -> dict:
    """One harness scenario → its observation line."""
    out = subprocess.run(
        [NODE, str(HARNESS), json.dumps({"fixture": fixture or {}, "actions": actions or []})],
        capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout)


def _key(key: str, **kw) -> dict:
    return {"type": "key", "key": key, **kw}


def _open() -> list:
    return [_key("k", ctrl=True, target="opener-btn")]


# ── Markup, load order, serving, parse (static — no node needed) ──

def test_palette_markup_is_a_hidden_labelled_dialog():
    """The overlay copies the reset-modal dialog pattern and starts hidden."""
    html = INDEX_HTML.read_text(encoding="utf-8")
    block = html[html.index('id="command-palette"'):]
    assert 'role="dialog"' in block
    assert 'aria-modal="true"' in block
    assert 'aria-labelledby="palette-title"' in block
    assert "hidden" in block  # not focusable/tabbable, no DOM leak, when closed
    assert "Command palette" in block  # visible title
    assert 'id="palette-search"' in block
    assert 'role="combobox"' in block
    assert 'aria-controls="palette-list"' in block
    assert 'aria-expanded="false"' in block
    assert "aria-label" in block
    assert 'id="palette-list"' in block
    assert 'role="listbox"' in block
    assert "Press Escape to close." in block  # the hint line


def test_palette_script_loads_after_prototype():
    """app.js first, prototype.js second, palette last — the order the
    script-order assertion in tests/test_sidebar_prototype.py pins."""
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert html.index("/static/app.js") < html.index("/static/prototype.js")
    assert html.index("/static/prototype.js") < html.index("/static/command-palette.js")


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_palette_script_is_served_and_parses():
    """The static mount serves the new file and node accepts it."""
    from fastapi.testclient import TestClient

    from dashboard.server import app

    res = TestClient(app).get("/static/command-palette.js")
    assert res.status_code == 200
    assert "initPalette" in res.text
    res = subprocess.run([NODE, "--check", str(PALETTE_JS)], capture_output=True, text=True)
    assert res.returncode == 0, f"command-palette.js parse error: {res.stderr}"


# ── Shell: open, close, keyboard ownership (harness) ──

@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_palette_init_is_safe_without_markup():
    """A page without the palette markup mounts without an error."""
    res = _run({"paletteMarkup": False}, [])
    assert res["threw"] is None


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_ctrl_k_and_meta_k_open_and_focus_search():
    """Ctrl+K and Cmd+K open from anywhere and focus the search input."""
    for mod in ("ctrl", "meta"):
        res = _run({"opener": "opener-btn"}, [_key("k", ctrl=mod == "ctrl", meta=mod == "meta", target="opener-btn")])
        assert res["threw"] is None
        assert res["open"] is True
        assert res["show"] is True
        assert res["active"] == "palette-search"
        assert res["keyPrevented"] is True
    # uppercase K works too (case-insensitive shortcut)
    res = _run({"opener": "opener-btn"}, [_key("K", ctrl=True, target="opener-btn")])
    assert res["open"] is True
    assert res["active"] == "palette-search"


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_escape_closes_and_restores_focus():
    """Escape closes and focus returns to the opener; a detached opener falls
    back to the body rather than vanishing."""
    res = _run({"opener": "opener-btn"}, _open() + [_key("Escape")])
    assert res["open"] is False
    assert res["show"] is False
    assert res["active"] == "opener-btn"
    res = _run({"opener": "opener-btn", "openerRemoved": True}, _open() + [_key("Escape")])
    assert res["open"] is False
    assert res["active"] == "body"


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_slash_opens_only_outside_editable_fields():
    """`/` is an alias only where typing it is safe: no modifiers, focus not
    in an editable field, event not already consumed."""
    res = _run({}, [_key("/", target="body")])
    assert res["open"] is True
    for target in ("palette-search", "palette-textarea", "palette-editable"):
        res = _run({}, [_key("/", target=target)])
        assert res["open"] is False, target
    res = _run({}, [_key("/", ctrl=True, target="body")])
    assert res["open"] is False


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_shortcut_ignored_while_reset_modal_open():
    """The palette never stacks on top of the reset confirmation."""
    res = _run({"resetModalShown": True}, [_key("k", ctrl=True)])
    assert res["open"] is False


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_backdrop_click_closes_inner_click_does_not():
    """A click on the overlay itself closes; a click inside the modal does not."""
    res = _run({}, _open() + [{"type": "click", "target": "command-palette"}])
    assert res["open"] is False
    res = _run({}, _open() + [{"type": "click", "target": "palette-inner"}])
    assert res["open"] is True


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_tab_and_shift_tab_keep_focus_on_search():
    """The search input is the only tabbable control — Tab is consumed, not
    allowed to walk focus behind the dialog."""
    for shift in (False, True):
        res = _run({}, _open() + [_key("Tab", shift=shift)])
        assert res["active"] == "palette-search"
        assert res["keyPrevented"] is True


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_open_palette_hides_keys_from_background_handlers():
    """While open, the palette owns the keyboard: the kanban arrow listener and
    the reset Escape listener must not react. (Sanity: they DO fire when the
    palette is closed, or the spies prove nothing.)"""
    res = _run({}, [_key("ArrowLeft")])
    assert res["kanbanKeys"] == ["ArrowLeft"]
    res = _run({}, _open() + [_key("ArrowLeft"), _key("ArrowRight"), _key("Escape")])
    assert res["kanbanKeys"] == []
    assert res["resetEscapeKeys"] == []
    assert res["open"] is False  # Escape still closed the palette


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_closed_palette_is_hidden_and_has_no_options():
    """Closed, the list leaks no option rows."""
    res = _run({}, _open() + [_key("Escape")])
    assert res["open"] is False
    assert res["rows"] == []


# ── Entries: pages, actions, filtering, arrow navigation (harness) ──

@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_palette_lists_six_pages_and_two_actions():
    """Every rail page (six today — the list is built from the DOM, never
    hardcoded) plus the two actions; the external Polymarket link, which
    shares the .proto-nav-btn class without data-page, is excluded."""
    res = _run({"withPrototype": True, "externalLink": True}, _open())
    labels = [r["label"] for r in res["rows"]]
    assert labels == [
        "Dashboard", "Data & Markets", "Strategy", "Trades & Positions",
        "Reports & Analytics", "Data Analysis", "SYNC VENUE", "RESET",
    ]
    assert "Polymarket" not in labels


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_typing_filters_entries():
    """Trimmed, case-insensitive substring match on the label."""
    res = _run({"withPrototype": True}, _open() + [{"type": "input", "value": "rep"}])
    assert [r["label"] for r in res["rows"]] == ["Reports & Analytics"]
    res = _run({"withPrototype": True}, _open() + [{"type": "input", "value": "REP"}])
    assert [r["label"] for r in res["rows"]] == ["Reports & Analytics"]


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_arrows_move_highlight_and_stop_at_ends():
    """One row carries aria-selected; aria-activedescendant points at it; the
    highlight stops at the first and last rows."""
    res = _run({"withPrototype": True}, _open() + [_key("ArrowDown"), _key("ArrowDown")])
    selected = [i for i, r in enumerate(res["rows"]) if r["selected"]]
    assert selected == [2]
    assert res["activeDescendant"] == res["rows"][2]["id"]
    res = _run({"withPrototype": True}, _open() + [_key("ArrowUp")])
    assert [i for i, r in enumerate(res["rows"]) if r["selected"]] == [0]
    res = _run({"withPrototype": True}, _open() + [_key("ArrowDown")] * 9)
    assert [i for i, r in enumerate(res["rows"]) if r["selected"]] == [7]


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_no_matches_row_and_enter_does_nothing():
    """No match → one inert row; Enter activates nothing."""
    res = _run({"withPrototype": True}, _open() + [{"type": "input", "value": "zzzz"}, _key("Enter")])
    assert [r["label"] for r in res["rows"]] == ["No matches."]
    assert res["rows"][0]["disabled"] is True
    assert res["syncClicks"] == 0
    assert res["resetClicks"] == 0


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_page_entry_navigates_like_the_sidebar():
    """Choosing a page runs the real sidebar click: page visibility,
    aria-current, and the sh-proto-page persistence all move."""
    res = _run(
        {"withPrototype": True, "stored": "strategy"},
        _open() + [{"type": "input", "value": "reports"}, _key("Enter")],
    )
    assert res["visiblePages"] == ["page-reports"]
    assert res["currentPage"] == "reports"
    assert res["stored"] == "reports"


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_sync_entry_clicks_btn_sync_once():
    """SYNC VENUE triggers the existing #btn-sync click — once, and never
    while the button is disabled."""
    res = _run({"withPrototype": True}, _open() + [{"type": "input", "value": "sync"}, _key("Enter")])
    assert res["syncClicks"] == 1
    res = _run(
        {"withPrototype": True, "syncDisabled": True},
        _open() + [{"type": "input", "value": "sync"}, _key("Enter")],
    )
    assert res["syncClicks"] == 0
    rows = _run({"withPrototype": True, "syncDisabled": True}, _open())["rows"]
    sync_row = [r for r in rows if r["label"] == "SYNC VENUE"][0]
    assert sync_row["disabled"] is True


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_reset_entry_opens_confirm_without_resetting():
    """RESET opens the existing typed-confirm modal: one click on the reset
    button, zero fetch calls, no focus pulled back to the opener."""
    res = _run({"withPrototype": True, "opener": "opener-btn"},
               _open() + [{"type": "input", "value": "reset"}, _key("Enter")])
    assert res["resetClicks"] == 1
    assert res["fetchCalls"] == 0
    assert res["open"] is False
    assert res["active"] != "opener-btn"
    sub = [r for r in _run({"withPrototype": True}, _open())["rows"] if r["label"] == "RESET"][0]["sub"]
    assert sub == "Opens the typed confirmation."
