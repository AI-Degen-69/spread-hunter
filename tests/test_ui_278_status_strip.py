"""Issue #278: top status strip overflow is silent on desktop.

`.top-meta` is a single scrolling row with a hidden scrollbar, so cut-off
pills give no cue and keyboard users get no affordance. The strip must
show an edge cue exactly when it can scroll, scroll by keyboard, keep
pills intact on desktop, and keep the mobile wrap behavior unchanged.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "dashboard" / "static"


@pytest.fixture(scope="module")
def index_html() -> str:
    return (STATIC / "index.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def css() -> str:
    return (STATIC / "styles.css").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_js() -> str:
    return (STATIC / "app.js").read_text(encoding="utf-8")


def test_strip_is_keyboard_scrollable(index_html):
    match = re.search(r'<div class="top-meta"[^>]*>', index_html)
    assert match, ".top-meta div missing from index.html"
    assert 'tabindex="0"' in match.group(0), ".top-meta needs tabindex=0"


def test_desktop_pills_never_shrink_or_wrap(css):
    assert re.search(
        r"\.top-meta\s*>\s*\*\s*{[^}]*flex-shrink\s*:\s*0", css
    ), ".top-meta children must not shrink on desktop"


def test_scroll_cue_shows_only_when_scrollable(css):
    assert re.search(
        r"\.top-meta\.is-scrollable[^{]*\{[^}]*box-shadow", css
    ), "no .is-scrollable edge-cue rule with a shadow"


def test_strip_shows_keyboard_focus(css):
    assert re.search(
        r"\.top-meta:focus-visible\s*{[^}]*outline", css
    ), "no .top-meta:focus-visible outline rule"


def test_strip_toggle_wired_in_js(app_js):
    assert "initTopMetaScrollCue" in app_js, "no initTopMetaScrollCue in app.js"
    assert "is-scrollable" in app_js, "JS never toggles is-scrollable"
    assert re.search(
        r"module\.exports\s*=\s*\{[\s\S]*?initTopMetaScrollCue", app_js
    ), "initTopMetaScrollCue not exported for tests"


def test_mobile_wrap_behavior_unchanged(css):
    match = re.search(
        r"@media\s*\(max-width:\s*900px\)\s*\{[\s\S]*?\.top-meta\s*\{([\s\S]*?)\}",
        css,
    )
    assert match, "no max-width:900px rule for .top-meta"
    body = match.group(1)
    assert "flex-wrap" in body and "wrap" in body, "mobile must keep wrapping"
    assert "overflow-x" in body and "visible" in body, "mobile must keep no scroller"
