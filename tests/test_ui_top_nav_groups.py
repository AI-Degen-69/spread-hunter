"""Issue #504: Reorganize the top nav bar with consistent grouping and clear hierarchy.

Tests the header structure, semantic groups, pill grammar, button hierarchy,
and panel placement. Uses html.parser to verify the live DOM tree in
dashboard/static/index.html and CSS assertions against dashboard/static/styles.css.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "dashboard" / "static"
INDEX_HTML = STATIC / "index.html"
STYLES_CSS = STATIC / "styles.css"
HARNESS = Path(__file__).resolve().parent / "js" / "live_state_harness.cjs"

requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node is not installed on this host")


def _harness(script: str) -> dict:
    out = subprocess.run([shutil.which("node"), str(HARNESS), script],
                         capture_output=True, text=True, check=True, encoding="utf-8")
    return json.loads(out.stdout)


class DOMNode:
    def __init__(self, tag: str, attrs: dict[str, str], parent: DOMNode | None = None):
        self.tag = tag
        self.attrs = attrs
        self.parent = parent
        self.children: list[DOMNode] = []
        self.text: str = ""

    def get_attr(self, name: str) -> str | None:
        return self.attrs.get(name)

    @property
    def classes(self) -> list[str]:
        return self.attrs.get("class", "").split()

    @property
    def id(self) -> str | None:
        return self.attrs.get("id")

    def find_all(self, tag: str | None = None, cls: str | None = None, id: str | None = None) -> list[DOMNode]:
        results = []
        match = True
        if tag and self.tag != tag:
            match = False
        if cls and cls not in self.classes:
            match = False
        if id and self.id != id:
            match = False
        if match and self.tag != "root":
            results.append(self)
        for child in self.children:
            results.extend(child.find_all(tag=tag, cls=cls, id=id))
        return results

    def find_one(self, tag: str | None = None, cls: str | None = None, id: str | None = None) -> DOMNode | None:
        nodes = self.find_all(tag=tag, cls=cls, id=id)
        return nodes[0] if nodes else None


class DOMTreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__()
        self.root = DOMNode("root", {})
        self.current = self.root

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        attr_dict = {k: v or "" for k, v in attrs}
        node = DOMNode(tag, attr_dict, self.current)
        self.current.children.append(node)
        # Void elements do not nest
        if tag not in ("area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"):
            self.current = node

    def handle_endtag(self, tag: str):
        curr = self.current
        while curr and curr.tag != tag and curr.parent:
            curr = curr.parent
        if curr and curr.parent:
            self.current = curr.parent

    def handle_data(self, data: str):
        if self.current:
            self.current.text += data


@pytest.fixture(scope="module")
def dom() -> DOMNode:
    html = INDEX_HTML.read_text(encoding="utf-8")
    parser = DOMTreeBuilder()
    parser.feed(html)
    return parser.root


@pytest.fixture(scope="module")
def css() -> str:
    return STYLES_CSS.read_text(encoding="utf-8")


# ── Task 1: Semantic Groups & Landmarks ──

def test_header_order_is_brand_then_strip(dom: DOMNode):
    header = dom.find_one("header")
    assert header is not None, "header element not found"
    # Ignore #db-run-switcher and #services-dropdown which are header-level panels
    in_flow_children = [
        c for c in header.children
        if c.id not in ("db-run-switcher", "services-dropdown") and not (c.tag == "div" and "header-panel" in c.classes)
    ]
    assert len(in_flow_children) >= 2
    assert "brand" in in_flow_children[0].classes, "First child must be .brand"
    assert "top-meta" in in_flow_children[1].classes, "Second child must be .top-meta"


def test_top_meta_has_three_named_groups(dom: DOMNode):
    top_meta = dom.find_one(cls="top-meta")
    assert top_meta is not None, ".top-meta element not found"
    child_groups = [c for c in top_meta.children if "top-group" in c.classes]
    assert len(child_groups) == 3, f"Expected 3 .top-group children, got {len(child_groups)}"

    labels = [g.get_attr("aria-label") for g in child_groups]
    roles = [g.get_attr("role") for g in child_groups]

    assert roles == ["group", "group", "group"], "All 3 groups must have role='group'"
    assert labels == ["Data source", "Live state", "Run controls"], (
        f"Expected groups 'Data source', 'Live state', 'Run controls', got {labels}"
    )


def test_top_meta_mixed_aria_removed(dom: DOMNode):
    top_meta = dom.find_one(cls="top-meta")
    assert top_meta is not None
    assert top_meta.get_attr("role") is None, ".top-meta should not have a mixed role='group'"
    assert top_meta.get_attr("aria-label") is None, ".top-meta should not have mixed aria-label"
    assert top_meta.get_attr("tabindex") == "0", ".top-meta must retain tabindex='0'"


def test_live_state_group_items(dom: DOMNode):
    top_meta = dom.find_one(cls="top-meta")
    state_group = [c for c in top_meta.children if "top-group-state" in c.classes][0]
    assert state_group.find_one(id="hud-services-pill") is not None, "Expected #hud-services-pill in Live state group"
    for removed_from_top in ["market-scan-pill", "scan-state-pill", "hud-guardrail-pill"]:
        assert state_group.find_one(id=removed_from_top) is None, f"#{removed_from_top} must not be a standalone pill in Live state group"


def test_source_group_items(dom: DOMNode):
    top_meta = dom.find_one(cls="top-meta")
    source_group = [c for c in top_meta.children if "top-group-source" in c.classes][0]
    expected_ids = ["master-status-indicator", "db-mode-badge", "shadow-run-clock", "scan-engine-elsewhere"]
    for eid in expected_ids:
        assert source_group.find_one(id=eid) is not None, f"Expected #{eid} in Data source group"
    assert source_group.find_one(id="exposure-bar") is None, "#exposure-bar must be removed from Data source group"


def test_controls_group_items(dom: DOMNode):
    top_meta = dom.find_one(cls="top-meta")
    ctrls_group = [c for c in top_meta.children if "top-group-controls" in c.classes][0]
    expected_ids = ["btn-master-toggle", "btn-reset", "btn-command-palette"]
    for eid in expected_ids:
        assert ctrls_group.find_one(id=eid) is not None, f"Expected #{eid} in Run controls group"
    assert ctrls_group.find_one(id="btn-sync") is None, "#btn-sync must be removed from Run controls group"


def test_top_group_styles(css: str):
    assert ".top-group" in css
    match = re.search(r"\.top-group\s*\{([^}]*)\}", css)
    assert match, ".top-group CSS rule not found"
    body = match.group(1)
    assert "inline-flex" in body
    assert "nowrap" in body
    assert "gap" in body


def test_tab_row_styling(css: str):
    match_switcher = re.search(r"\.tab-switcher\s*\{([^}]*)\}", css)
    assert match_switcher, ".tab-switcher rule not found"
    assert "border-radius" in match_switcher.group(1)

    match_btn = re.search(r"\.tab-btn\s*\{([^}]*)\}", css)
    assert match_btn, ".tab-btn rule not found"
    body = match_btn.group(1)
    assert "uppercase" in body
    assert "IBM Plex Sans" in body
    assert "letter-spacing" in body


def test_header_status_pills_share_markup(dom: DOMNode):
    """Header status pills (no name beside them) keep .pill-label + .pill-value.

    The Services-dropdown pills are excluded: they are status-only (#602) because
    the row's name column identifies the service."""
    pills = [
        "master-status-indicator",
        "hud-services-pill",
        "db-mode-badge",
    ]
    for pid in pills:
        el = dom.find_one(id=pid)
        assert el is not None, f"Expected #{pid} in DOM"
        assert "pill" in el.classes, f"#{pid} must have .pill class"
        assert "mono" in el.classes, f"#{pid} must have .mono class"
        label = el.find_one(cls="pill-label")
        assert label is not None, f"#{pid} must carry .pill-label span"
        value = el.find_one(cls="pill-value")
        assert value is not None, f"#{pid} must carry .pill-value span"


def test_dropdown_pills_are_status_only(dom: DOMNode):
    """#602: the Services-dropdown pills carry only .pill-value (the state).

    The row's name column identifies the service, so the pill must not also
    carry a .pill-label — that would duplicate the name."""
    pills = [
        "market-scan-pill",
        "scan-state-pill",
        "hud-guardrail-pill",
    ]
    for pid in pills:
        el = dom.find_one(id=pid)
        assert el is not None, f"Expected #{pid} in DOM"
        assert "pill" in el.classes, f"#{pid} must have .pill class"
        value = el.find_one(cls="pill-value")
        assert value is not None, f"#{pid} must carry .pill-value span"
        label = el.find_one(cls="pill-label")
        assert label is None, \
            f"#{pid} must NOT carry .pill-label: the name column identifies the service (#602)"


def test_implemented_run_pills_in_header(dom: DOMNode):
    for pid in ("live-implemented-pill", "shadow-implemented-pill"):
        el = dom.find_one(id=pid)
        assert el is not None, f"Expected #{pid} in DOM"
        assert "pill" in el.classes, f"#{pid} must have .pill class"
        assert "mono" in el.classes, f"#{pid} must have .mono class"
        label = el.find_one(cls="pill-label")
        assert label is not None, f"#{pid} must carry .pill-label span"
        value = el.find_one(cls="pill-value")
        assert value is not None, f"#{pid} must carry .pill-value span"


def test_pill_value_css(css: str):
    match = re.search(r"\.pill-value\s*\{([^}]*)\}", css)
    assert match, ".pill-value rule not found in styles.css"
    assert "font-weight" in match.group(1)


@requires_node
def test_scan_pill_renders_grammar():
    # #602: the SCAN pill lives in a Services-dropdown row whose name column
    # identifies the service, so it is status-only: dot + state, no label.
    data = _harness("topnav")["scan"]
    assert {"pill", "state-running", "mono"}.issubset(set(data["className"].split()))
    assert 'class="pill-label"' not in data["innerHTML"]
    assert 'class="pill-value"' in data["innerHTML"]
    assert data["textContent"] == "ON"


@requires_node
def test_engine_and_elsewhere_renders_grammar():
    top = _harness("topnav")
    eng = top["engineRunning"]
    assert {"pill", "state-running", "mono"}.issubset(set(eng["className"].split()))
    assert eng["elsewhereDisplay"] == "none"

    stalled = top["engineStalledElsewhere"]
    assert {"pill", "state-down", "mono"}.issubset(set(stalled["className"].split()))
    assert stalled["elsewhereDisplay"] == ""
    assert {"pill", "engine-elsewhere-pill", "mono"}.issubset(set(stalled["elsewhereClass"].split()))
    assert 'class="pill-label"' in stalled["elsewhereHtml"]
    assert 'class="pill-value"' in stalled["elsewhereHtml"]
    assert stalled["elsewhereText"] == "1 RUN LIVE ON ANOTHER STORE"


@requires_node
def test_engine_no_payload_is_status_only():
    # CodeRabbit on #605: the no-payload branch rendered ENGINE UNKNOWN,
    # duplicating the row's name. Status-only per #602: just UNKNOWN.
    eng = _harness("topnav")["engineNoPayload"]
    assert {"pill", "state-unknown", "mono"}.issubset(set(eng["className"].split()))
    assert 'class="pill-label"' not in eng["stateHtml"]
    assert 'class="pill-value"' in eng["stateHtml"]
    assert eng["stateText"] == "UNKNOWN"


@requires_node
def test_db_badge_renders_grammar():
    data = _harness("topnav")["dbProd"]
    assert {"pill", "mode-live", "mono"}.issubset(set(data["className"].split()))
    assert 'class="pill-label"' in data["innerHTML"]
    assert 'class="pill-value"' in data["innerHTML"]
    assert data["textContent"] == "DB LIVE"


@requires_node
def test_live_db_badge_shows_the_store_name():
    # #600: LIVE must show the db filename like SHADOW does — the store name is
    # significant info, and the two badges must be consistent.
    data = _harness("topnav")["dbLiveWithName"]
    assert {"pill", "mode-live", "mono"}.issubset(set(data["className"].split()))
    assert data["textContent"] == "DB LIVE · production_live.db"
    assert 'class="pill-detail"' in data["innerHTML"]


@requires_node
def test_master_and_services_render_grammar():
    data = _harness("topnav")["masterRunning"]
    assert {"pill", "state-running", "mono"}.issubset(set(data["className"].split()))
    assert 'pulse-dot active' in data["innerHTML"]
    assert 'class="pill-label"' in data["innerHTML"]
    assert 'class="pill-value"' in data["innerHTML"]
    assert data["textContent"] == "STACK RUNNING"
    assert "services-pill-trigger" in data["servicesClass"]


def test_header_buttons_carry_ctrl_btn_hierarchy(dom: DOMNode):
    master = dom.find_one(id="btn-master-toggle")
    assert master is not None
    assert "ctrl-btn" in master.classes
    assert "ctrl-btn-primary" in master.classes

    for bid in ["btn-reset", "btn-command-palette"]:
        btn = dom.find_one(id=bid)
        assert btn is not None, f"Expected #{bid} in DOM"
        assert "ctrl-btn" in btn.classes, f"#{bid} must have .ctrl-btn class"
        assert "ctrl-btn-secondary" in btn.classes, f"#{bid} must have .ctrl-btn-secondary class"
    assert dom.find_one(id="btn-sync") is None, "#btn-sync must not exist in DOM"


def test_ctrl_btn_css_rules(css: str):
    match_base = re.search(r"\.ctrl-btn\s*\{([^}]*)\}", css)
    assert match_base, ".ctrl-btn rule not found"
    base = match_base.group(1)
    assert "inline-flex" in base
    assert "min-height" in base and "--touch-min" in base
    assert "min-width" in base and "--touch-min" in base

    match_prim = re.search(r"\.ctrl-btn-primary\s*\{([^}]*)\}", css)
    assert match_prim, ".ctrl-btn-primary rule not found"
    assert "800" in match_prim.group(1)

    match_sec = re.search(r"\.ctrl-btn-secondary\s*\{([^}]*)\}", css)
    assert match_sec, ".ctrl-btn-secondary rule not found"
    assert "700" in match_sec.group(1)


@requires_node
def test_master_toggle_helper_classes():
    t = _harness("topnav")["toggleClasses"]
    assert t["startLive"] == "ctrl-btn ctrl-btn-primary btn-start-run"
    assert t["stopLive"] == "ctrl-btn ctrl-btn-primary btn-stop-run"
    assert t["startShadow"] == "ctrl-btn ctrl-btn-primary btn-start-run btn-start-shadow"
    assert t["stopShadow"] == "ctrl-btn ctrl-btn-primary btn-stop-run btn-stop-shadow"
    assert t["renderedLiveRunning"] == "ctrl-btn ctrl-btn-primary btn-stop-run"


# ── Task 4: Panel Anchoring, Unified Positioning & Dismissal ──

def test_header_panels_direct_children_of_header(dom: DOMNode):
    header = dom.find_one("header")
    assert header is not None

    services = dom.find_one(id="services-dropdown")
    assert services is not None, "#services-dropdown must exist in DOM"
    assert services.parent == header, "#services-dropdown must be a direct child of <header>"
    assert "header-panel" in services.classes, "#services-dropdown must carry .header-panel"
    assert "services-dropdown" in services.classes

    switcher = dom.find_one(id="db-run-switcher")
    assert switcher is not None, "#db-run-switcher must exist in DOM"
    assert switcher.parent == header, "#db-run-switcher must be a direct child of <header>"
    assert "header-panel" in switcher.classes, "#db-run-switcher must carry .header-panel"
    assert "db-run-switcher" in switcher.classes

    top_meta = dom.find_one(cls="top-meta")
    assert top_meta is not None
    assert top_meta.find_one(id="services-dropdown") is None, "#services-dropdown must not be inside .top-meta"
    assert top_meta.find_one(id="db-run-switcher") is None, "#db-run-switcher must not be inside .top-meta"

    assert dom.find_one(cls="services-pill-host") is None, ".services-pill-host must be removed from DOM"


def test_header_panel_css_rules(css: str):
    match_panel = re.search(r"\.header-panel\s*\{([^}]*)\}", css)
    assert match_panel, ".header-panel rule not found in styles.css"
    body = match_panel.group(1)
    assert "position: absolute" in body
    assert "z-index: 60" in body
    assert "border-radius" in body
    assert "box-shadow" in body

    assert ".services-pill-host" not in css, ".services-pill-host rule must be removed from styles.css"


@requires_node
def test_place_panel_clamping():
    placement = _harness("topnav")["panelPlacement"]
    assert placement["leftNormal"] == "200px"
    assert placement["leftClampMin"] == "4px"
    assert placement["leftClampMax"] == "656px"


# ── Task 5: Live State Aggregation & Neutral Kanban Hero Palette (Consolidated #479) ──

def test_kanban_bucket_hero_palette_neutralized(css: str):
    """Categorical colors in kanban bucket heroes are retired to neutral slate/ink chip styling."""
    match_hero = re.search(r"/\* Neutral slate/ink chip styling for kanban bucket heroes[^*]*\*/([^}]+})", css)
    assert match_hero, "Neutral kanban bucket hero rule comment not found in styles.css"
    block = match_hero.group(1)
    assert "color: var(--text-primary);" in block

    # Ensure none of the categorical hues are used for kanban heroes
    for hue in ("#a78bfa", "#fb923c", "#f472b6", "#38bdf8", "#fbbf24"):
        pattern = rf"\.kanban-bucket-hero[^{{]*\{{[^}}]*{re.escape(hue)}"
        assert not re.search(pattern, css), f"Categorical hue {hue} found in .kanban-bucket-hero rule"

    match_passed = re.search(r"\.kanban-bucket-hero\.passed\s*\{([^}]*)\}", css)
    assert match_passed, ".kanban-bucket-hero.passed rule not found"
    assert "var(--border-subtle)" in match_passed.group(1)


@requires_node
def test_aggregate_service_state_hierarchy():
    """Worst-state-wins: DOWN > DEGRADED > UNKNOWN > RUNNING > STOPPED."""
    agg = _harness("topnav")["aggregation"]
    assert agg["allRunning"] == "running"
    assert agg["degradedAlert"] == "degraded"
    assert agg["worstWinsDown"] == "down"
    assert agg["allStopped"] == "stopped"
    assert agg["unknownPresence"] == "unknown"
    assert agg["emptyState"] == "unknown"


@requires_node
def test_services_pill_reflects_worst_state_wins():
    """#hud-services-pill reflects worst-state-wins when individual services degrade or fault."""
    top = _harness("topnav")

    degraded = top["servicesDegradedByAlert"]
    assert "state-degraded" in degraded["className"]
    assert degraded["stateText"] == "DEGRADED"
    assert "degraded" in degraded["subText"]

    down = top["servicesDownByStaleHeartbeat"]
    assert "state-down" in down["className"]
    assert down["stateText"] == "DOWN"
    assert down["subText"] == "fault detected"


