"""The sidebar-pages layout (#95 frame, #140 content move).

The dashboard's three horizontal tabs stopped scaling: live ops, analytics,
screener and the strategy explainer are four concerns crammed into three
panels plus a separate URL. #120 shipped the frame -- the rail, five named
pages, empty containers. This is the content moving in.

It moves nodes rather than copying markup. `app.js` finds every panel it
renders into by id, and an id survives a change of parent, so `/prototype`
shows the same live data the tabbed page does with no second copy of the
markup to drift.

It is still served at `/prototype` rather than replacing `/`: this dashboard
is the control surface for a loop that places real orders, and the operator
keeps the surface they know until the layout is signed off.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_ROOT = Path(__file__).resolve().parent.parent
_STATIC = _ROOT / "dashboard" / "static"
HARNESS = Path(__file__).resolve().parent / "js" / "prototype_nav_harness.cjs"
LAYOUT_HARNESS = Path(__file__).resolve().parent / "js" / "prototype_layout_harness.cjs"

PAGES = ("home", "data-markets", "strategy", "trades", "reports")

requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node is not installed on this host")


def _nav(clicks: list[str] | None = None, stored: str | None = None,
         toggle_nav: bool = False, stored_pin: str | None = None,
         click_pin: int = 0) -> dict:
    payload = {"clicks": clicks or [], "stored": stored, "toggleNav": toggle_nav,
               "storedPin": stored_pin, "clickPin": click_pin}
    out = subprocess.run([shutil.which("node"), str(HARNESS), json.dumps(payload)],
                         capture_output=True, text=True, check=True, encoding="utf-8")
    return json.loads(out.stdout)


def _layout() -> dict:
    out = subprocess.run([shutil.which("node"), str(LAYOUT_HARNESS)],
                         capture_output=True, text=True, check=True, encoding="utf-8")
    return json.loads(out.stdout)


def _rule(css: str, selector: str) -> str:
    """The declarations of the first rule this selector is part of.

    A rule may list several selectors, so matching on the whole comma group
    would make the assertion depend on how the stylesheet wraps its lines.
    """
    body = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for head, block in re.findall(r"([^{}]+)\{([^{}]*)\}", body):
        if selector in [part.strip() for part in head.split(",")]:
            return block
    raise AssertionError(f"no rule for {selector}")


def _matches(html: str, selector: str) -> int:
    """How many elements in the served page a layout selector would find."""
    if selector.startswith("#"):
        return len(re.findall(rf'\bid="{re.escape(selector[1:])}"', html))
    if selector.startswith("."):
        name = re.escape(selector[1:])
        return len(re.findall(rf'\bclass="[^"]*\b{name}\b[^"]*"', html))
    raise AssertionError(f"unsupported selector in the layout map: {selector}")


# ── The layout map ──────────────────────────────────────────────────────────

@requires_node
def test_every_page_in_the_rail_has_panels_assigned():
    # Arrange — the frame shipped empty; the deliverable here is that no page
    # is still a placeholder.
    layout = _layout()

    # Act / Assert
    assert [entry["page"] for entry in layout["layout"]] == list(PAGES)
    for entry in layout["layout"]:
        assert entry["selectors"], f'{entry["page"]} has no panels assigned'


@requires_node
def test_every_assigned_panel_exists_on_the_served_page():
    # Arrange — a selector that matches nothing is a silently empty page, and
    # one that matches twice would move the wrong node.
    html = (_STATIC / "index.html").read_text(encoding="utf-8")

    # Act / Assert
    for entry in _layout()["layout"]:
        for selector in entry["selectors"]:
            assert _matches(html, selector) == 1, (
                f'{selector} ({entry["page"]}) matches '
                f"{_matches(html, selector)} elements in index.html")


@requires_node
def test_no_panel_is_claimed_by_two_pages():
    # Arrange — a node has one parent. Two pages claiming it means whichever
    # page is built last silently steals it from the other.
    seen: dict[str, str] = {}

    # Act / Assert
    for entry in _layout()["layout"]:
        for selector in entry["selectors"]:
            assert selector not in seen, (
                f'{selector} is on both {seen[selector]} and {entry["page"]}')
            seen[selector] = entry["page"]


@requires_node
def test_the_layout_is_the_dashboard_at_the_root():
    # Arrange — the layout was judged at /prototype against live data and
    # signed off. It is the control surface now, so `/` serves it.
    layout = _layout()

    # Act / Assert — /prototype still mounts it, so the path the layout was
    # reviewed through lands on the layout rather than on a dead tab row.
    assert layout["mounts_on_root"] is True
    assert layout["mounts_on_prototype"] is True
    assert layout["mounts_on_prototype_slash"] is True


# ── Moving, not copying ─────────────────────────────────────────────────────

def test_the_layout_moves_live_nodes_instead_of_rendering_its_own():
    # Arrange — a second render path would have to be kept in sync with
    # app.js for ever. The layout re-parents; it does not fetch or render.
    js = (_STATIC / "prototype.js").read_text(encoding="utf-8")

    # Act / Assert
    assert "fetch(" not in js
    assert "/api/" not in js
    assert "appendChild(panel)" in js


def test_the_pages_are_no_longer_placeholders():
    # Arrange / Act
    js = (_STATIC / "prototype.js").read_text(encoding="utf-8")
    css = (_STATIC / "prototype.css").read_text(encoding="utf-8")

    # Assert — the empty-frame scaffold is gone, page and file.
    assert "proto-placeholder" not in js
    assert "proto-placeholder" not in css
    assert not (_STATIC / "prototype.html").exists()


def test_the_emptied_tab_shells_stay_in_the_document():
    # Arrange — app.js reads `#tab-3.hidden` before it will scroll the kanban
    # and toggles `#tab-1..3` by id. Removing the shells would break both.
    js = (_STATIC / "prototype.js").read_text(encoding="utf-8")

    # Act / Assert
    assert "'tab-1', 'tab-2', 'tab-3'" in js
    assert "section.hidden = false" in js


def test_the_strategy_explainer_is_framed_rather_than_forked():
    # Arrange — the explainer is a whole document; copying its markup into the
    # dashboard would leave two copies to edit.
    js = (_STATIC / "prototype.js").read_text(encoding="utf-8")

    # Act / Assert
    assert "/static/strategy_explainer.html" in js
    assert (_STATIC / "strategy_explainer.html").exists()


def test_the_analytics_subnav_filters_only_its_own_page():
    # Arrange — those buttons filtered one tab panel by section. Two of the
    # panels they hide (`#analytics-gates`, `#market-inspection-card`) now
    # live on other pages, so an unscoped click blanks a page the operator is
    # not looking at. Hiding the whole sub-nav was the holding fix while the
    # layout sat at /prototype; scoping it to its own page is the real one.
    css = (_STATIC / "prototype.css").read_text(encoding="utf-8")
    app = (_STATIC / "app.js").read_text(encoding="utf-8")

    # Act / Assert
    assert ".proto-body .stats-subnav" not in css
    assert "function statsFilterScope()" in app
    assert "scope.contains(el)" in app
    assert "closest('.proto-page')" in app


def test_a_subnav_view_with_nothing_on_the_page_is_dropped():
    # Arrange — Market Inspection was retired with the Data & Markets table:
    # the kanban and the dashboard's orders table superseded it. Its button
    # and STATS_VIEW_TARGETS entry are gone, and the prune still runs.
    app = (_STATIC / "app.js").read_text(encoding="utf-8")
    prototype = (_STATIC / "prototype.js").read_text(encoding="utf-8")

    # Act
    targets = app.split("const STATS_VIEW_TARGETS = {")[1].split("};")[0]

    # Assert — the retired view has no target left, and the prune runs
    # again after the panels move.
    assert "#market-inspection-card" not in targets
    assert "function pruneStatsSubnav()" in app
    assert "pruneStatsSubnav()" in prototype


# ── Navigation ──────────────────────────────────────────────────────────────

@requires_node
def test_exactly_one_page_is_visible_at_a_time():
    # Arrange / Act
    rendered = _nav(clicks=["strategy"])

    # Assert
    assert rendered["visible"] == ["strategy"]
    assert rendered["current"] == ["strategy"]


@requires_node
def test_home_is_the_page_a_first_visit_lands_on():
    # Arrange / Act
    rendered = _nav()

    # Assert
    assert rendered["visible"] == ["home"]


@requires_node
def test_the_chosen_page_survives_a_reload():
    # Arrange — the previous visit ended on Reports.
    rendered = _nav(stored="reports")

    # Act / Assert
    assert rendered["visible"] == ["reports"]
    assert rendered["current"] == ["reports"]


@requires_node
def test_an_unknown_stored_page_falls_back_to_home():
    # Arrange — stored state is data, not a command: a page name that no
    # longer exists must not hide every panel.
    rendered = _nav(stored="page-that-was-renamed")

    # Act / Assert
    assert rendered["visible"] == ["home"]
    assert rendered["stored"] == "home"


@requires_node
def test_choosing_a_page_closes_the_narrow_screen_drawer():
    # Arrange — on a narrow window the rail sits over the page, so a chosen
    # page that leaves it open hides the thing just selected.
    opened = _nav(toggle_nav=True)
    assert opened["nav_open"] is True
    assert opened["toggle_expanded"] == "true"

    # Act
    rendered = _nav(clicks=["trades"], toggle_nav=False)

    # Assert
    assert rendered["nav_open"] is False


def test_the_nav_toggle_sits_with_the_wordmark():
    # Arrange — the header is a space-between flex. A third top-level child
    # pushed the wordmark into the middle of the bar at exactly the widths
    # where the toggle appears.
    js = (_STATIC / "prototype.js").read_text(encoding="utf-8")

    # Act / Assert
    assert "header .brand" in js
    assert "brand.insertBefore(buildNavToggle(scope), brand.firstChild)" in js


def test_the_rail_spans_its_column():
    # Arrange — five items sized to their content left a 237px card beside a
    # 1300px page, which reads as an unfinished panel rather than as the rail.
    css = (_STATIC / "prototype.css").read_text(encoding="utf-8")

    # Act
    rail = css.split(".proto-sidebar {")[1].split("}")[0]

    # Assert
    assert "height: calc(100vh - 24px)" in rail
    assert "max-height" not in rail


def test_the_page_notes_orient_rather_than_narrate():
    # Arrange — a tool used every day should not list its own panels back at
    # the operator; the panels are right there and carry their own headings.
    js = (_STATIC / "prototype.js").read_text(encoding="utf-8")

    # Act
    notes = re.findall(r"note: '([^']+)'", js)

    # Assert
    assert len(notes) == len(PAGES)
    for note in notes:
        assert len(note.split()) <= 12, f"note narrates instead of orienting: {note}"


def test_the_drawer_has_a_narrow_screen_rule():
    # Arrange / Act
    css = (_STATIC / "prototype.css").read_text(encoding="utf-8")

    # Assert — the rail becomes a drawer rather than squeezing the page.
    assert "@media (max-width: 900px)" in css
    assert ".proto-hamburger" in css
    assert ".proto-body.proto-nav-open .proto-sidebar" in css


# ── Wiring ──────────────────────────────────────────────────────────────────

def test_the_open_drawer_is_opaque():
    # Arrange — `--bg-card` is 65% translucent. That is right for a card on the
    # page and wrong for a drawer sitting over the header: the wordmark and the
    # EMERGENCY CANCEL ALL button read straight through the page names.
    css = (_STATIC / "prototype.css").read_text(encoding="utf-8")

    # Act
    drawer = css.split("@media (max-width: 900px)")[1]
    rail = drawer.split(".proto-sidebar {")[1].split("}")[0]

    # Assert
    assert "background: var(--bg-surface)" in rail


def test_the_layout_inherits_the_dashboard_theme():
    # Arrange — a layout with its own colours drifts from the live page the
    # moment either changes. The rail reads `--bg-surface` rather than the
    # translucent `--bg-card` because it expands over the page (#163).
    css = (_STATIC / "prototype.css").read_text(encoding="utf-8")

    # Act / Assert
    assert "var(--bg-surface)" in css
    assert "var(--border-subtle)" in css


def test_the_served_page_loads_the_layout_assets():
    # Arrange / Act
    index = (_STATIC / "index.html").read_text(encoding="utf-8")

    # Assert — after app.js, so the panels exist before they are moved.
    assert '/static/prototype.css' in index
    assert index.index('/static/app.js') < index.index('/static/prototype.js')


def test_the_layout_stylesheet_is_inert_on_the_live_page():
    # Arrange — index.html loads it on every request, including `/`.
    css = (_STATIC / "prototype.css").read_text(encoding="utf-8")

    # Act — every rule outside a comment must be scoped to a proto- class.
    body = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    selectors = [part.strip()
                 for block in re.findall(r"([^{}]+)\{", body)
                 for part in block.split(",")
                 if part.strip() and not part.strip().startswith("@")]

    # Assert
    unscoped = [s for s in selectors if "proto-" not in s]
    assert not unscoped, f"these rules would restyle the live page: {unscoped}"


def _client() -> TestClient:
    """A test client over the real app. Both pages are served from disk."""
    from dashboard.server import app

    return TestClient(app)


def test_the_live_dashboard_is_still_served_at_the_root():
    # Arrange — the layout under review must not displace the control surface.
    client = _client()

    # Act
    root = client.get("/")
    proto = client.get("/prototype")

    # Assert — both answer, and the root still carries the live panels.
    assert root.status_code == 200
    assert proto.status_code == 200
    assert 'id="orders-trades-table"' in root.text


def test_the_prototype_path_serves_the_live_document_with_its_token():
    # Arrange — the panels only show data if app.js can authenticate its
    # control calls, so the placeholder has to be filled here too.
    from dashboard.server import CONTROL_TOKEN, CONTROL_TOKEN_PLACEHOLDER

    client = _client()

    # Act
    proto = client.get("/prototype")

    # Assert — the live panels moved in, and the token was substituted.
    assert 'id="orders-trades-table"' in proto.text
    assert "/static/prototype.js" in proto.text
    assert CONTROL_TOKEN_PLACEHOLDER not in proto.text
    assert CONTROL_TOKEN in proto.text


def test_the_root_serves_the_layout_and_links_out_of_nothing():
    # Arrange — `/` is the layout now, so the `Layout prototype →` link has
    # nowhere left to point and the operator has nowhere to be stranded.
    from dashboard.server import CONTROL_TOKEN, CONTROL_TOKEN_PLACEHOLDER

    client = _client()
    index = (_STATIC / "index.html").read_text(encoding="utf-8")

    # Act
    root = client.get("/")

    # Assert
    assert "/static/prototype.js" in root.text
    assert CONTROL_TOKEN_PLACEHOLDER not in root.text
    assert CONTROL_TOKEN in root.text
    assert 'href="/prototype"' not in index
    assert "Layout prototype" not in index


# ── The collapsible icon rail (#163) ────────────────────────────────────────

def test_the_rail_sits_at_icon_width_until_it_is_asked_for():
    # Arrange — a fixed 236px column spends the widest panels' room on five
    # short labels that are only read when the operator is switching pages.
    css = (_STATIC / "prototype.css").read_text(encoding="utf-8")

    # Act
    shell = _rule(css, ".proto-shell")
    rail = _rule(css, ".proto-sidebar")

    # Assert — the grid track and the rail agree on the collapsed width.
    assert "grid-template-columns: 56px minmax(0, 1fr)" in shell
    assert "width: 56px" in rail


def test_hovering_the_rail_expands_it_over_the_page():
    # Arrange — an expansion that widens the grid track reflows every panel
    # on the page each time the pointer crosses the rail.
    css = (_STATIC / "prototype.css").read_text(encoding="utf-8")

    # Act
    rail = _rule(css, ".proto-sidebar")
    hovered = _rule(css, ".proto-body:not(.proto-rail-pinned) .proto-sidebar:hover")

    # Assert — the rail overlays: it widens itself, above the page, on its own
    # layer, while the track it sits in keeps the collapsed width.
    assert "transition: width 220ms cubic-bezier(0.16, 1, 0.3, 1)" in rail
    assert "z-index" in rail
    assert "background: var(--bg-surface)" in rail
    assert "width: 236px" in hovered
    assert "236px minmax" not in _rule(css, ".proto-shell")


def test_a_collapsed_label_is_still_announced():
    # Arrange — an icon-only rail that drops its labels from the accessibility
    # tree leaves a screen reader with five unnamed buttons.
    css = (_STATIC / "prototype.css").read_text(encoding="utf-8")
    js = (_STATIC / "prototype.js").read_text(encoding="utf-8")

    # Act
    label = _rule(css, ".proto-nav-label")

    # Assert — clipped, not removed.
    assert "display: none" not in label
    assert "visibility: hidden" not in label
    assert "white-space: nowrap" in label
    assert "proto-nav-label" in js


def test_the_labels_are_revealed_when_the_rail_is_open():
    # Arrange / Act — hover and pin are the two ways the rail opens, and a
    # label revealed by only one of them is a half-finished rail.
    css = (_STATIC / "prototype.css").read_text(encoding="utf-8")

    # Assert
    assert ".proto-sidebar:hover .proto-nav-label" in css
    assert ".proto-rail-pinned .proto-nav-label" in css


@requires_node
def test_pinning_the_rail_holds_it_open():
    # Arrange / Act
    pinned = _nav(click_pin=1)

    # Assert
    assert pinned["rail_pinned"] is True
    assert pinned["pin_pressed"] == "true"


@requires_node
def test_a_second_click_unpins_the_rail():
    # Arrange / Act
    unpinned = _nav(click_pin=2)

    # Assert
    assert unpinned["rail_pinned"] is False
    assert unpinned["pin_pressed"] == "false"


@requires_node
def test_the_pinned_rail_survives_a_reload():
    # Arrange — pinning is a preference, not a per-visit mode.
    pinned = _nav(click_pin=1)
    assert pinned["stored_pin"] == "1"

    # Act — the next visit reads what the last one wrote.
    reloaded = _nav(stored_pin="1")

    # Assert
    assert reloaded["rail_pinned"] is True


@requires_node
def test_an_unreadable_pinned_state_loads_the_rail_collapsed():
    # Arrange — stored state is data, not a command.
    rendered = _nav(stored_pin="yes-please")

    # Act / Assert
    assert rendered["rail_pinned"] is False


@requires_node
def test_pinning_does_not_disturb_the_chosen_page():
    # Arrange — the pin writes to storage, and one shared slot would make
    # pinning the rail forget which page the operator was on.
    rendered = _nav(clicks=["reports"], click_pin=1)

    # Act / Assert
    assert rendered["stored"] == "reports"
    assert rendered["visible"] == ["reports"]


@requires_node
def test_every_page_carries_an_svg_icon():
    # Arrange — a font glyph renders differently on every machine, and an
    # icon-only rail has nothing else to identify a page by.
    layout = _layout()
    js = (_STATIC / "prototype.js").read_text(encoding="utf-8")

    # Act
    icons = [entry["icon"] for entry in layout["layout"]]

    # Assert — path data, drawn as real SVG, hidden from the label beside it.
    assert len(icons) == len(PAGES)
    for icon in icons:
        assert icon.startswith("M"), f"not SVG path data: {icon}"
    assert "createElementNS" in js
    assert "aria-hidden" in js


def test_the_rail_ends_in_the_links_the_operator_leaves_for():
    # Arrange — the venue and the repo are the two places the operator opens
    # next, and both were a browser-history hunt from here.
    js = (_STATIC / "prototype.js").read_text(encoding="utf-8")

    # Act / Assert
    assert "proto-rail-divider" in js
    assert "https://polymarket.com" in js
    assert "https://github.com/AI-Degen-69/spread-hunter" in js
    assert 'rel' in js and 'noopener' in js


@requires_node
def test_the_pin_leaves_the_narrow_screen_drawer_alone():
    # Arrange — below 900px the rail is a drawer over the page; a pin that
    # also opened the drawer would cover the dashboard on a phone.
    rendered = _nav(click_pin=1)

    # Act / Assert
    assert rendered["rail_pinned"] is True
    assert rendered["nav_open"] is False


def test_the_drawer_ignores_the_collapsed_width():
    # Arrange — the drawer slides a full-width rail over the page; inheriting
    # the 56px desktop width would slide in an unreadable strip.
    css = (_STATIC / "prototype.css").read_text(encoding="utf-8")

    # Act
    drawer = css.split("@media (max-width: 900px)")[1]
    rail = drawer.split(".proto-sidebar {")[1].split("}")[0]

    # Assert
    assert "width: 240px" in rail


def test_the_drawer_drops_the_pin():
    # Arrange — below 900px there is no collapsed rail to hold open, so a pin
    # in the drawer is a control that does nothing.
    css = (_STATIC / "prototype.css").read_text(encoding="utf-8")

    # Act
    drawer = css.split("@media (max-width: 900px)")[1]

    # Assert
    pin = drawer.split(".proto-rail-pin {")[1].split("}")[0]
    assert "display: none" in pin


def test_the_pin_is_announced_by_the_label_the_operator_reads():
    # Arrange - an aria-label overrides the visible text, so a button reading
    # "Pin sidebar" announced as something else is the mismatch WCAG 2.5.3
    # exists to catch. The state it toggles rides on aria-pressed instead.
    js = (_STATIC / "prototype.js").read_text(encoding="utf-8")

    # Act
    pin = js.split("function buildRailPin(doc)")[1].split("function buildRailDivider")[0]

    # Assert
    assert "aria-label" not in pin
    assert "aria-pressed" in pin
    assert "buildNavLabel(doc, 'Pin sidebar')" in pin


def test_the_keyboard_opens_the_rail_without_the_mouse_holding_it_open():
    # Arrange - `:focus-within` fires for a mouse click too, so clicking the
    # pin to close the rail left it open under the pointer that had just
    # closed it. Keyboard focus still has to open it.
    css = (_STATIC / "prototype.css").read_text(encoding="utf-8")

    # Act - the comment above the rule names the pitfall, so read the
    # selectors rather than the raw file.
    body = re.sub(r"/\*.*?\*/", "", css, flags=re.S)

    # Assert
    assert ":focus-within" not in body
    assert ":has(:focus-visible)" in body
