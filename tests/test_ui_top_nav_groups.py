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
    assert labels == ["Live state", "Data source", "Run controls"], (
        f"Expected groups 'Live state', 'Data source', 'Run controls', got {labels}"
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
    expected_ids = ["market-scan-pill", "scan-state-pill", "scan-engine-elsewhere", "master-status-indicator", "hud-guardrail-pill"]
    for eid in expected_ids:
        assert state_group.find_one(id=eid) is not None, f"Expected #{eid} in Live state group"


def test_source_group_items(dom: DOMNode):
    top_meta = dom.find_one(cls="top-meta")
    source_group = [c for c in top_meta.children if "top-group-source" in c.classes][0]
    expected_ids = ["db-mode-badge", "shadow-run-clock", "exposure-bar"]
    for eid in expected_ids:
        assert source_group.find_one(id=eid) is not None, f"Expected #{eid} in Data source group"


def test_controls_group_items(dom: DOMNode):
    top_meta = dom.find_one(cls="top-meta")
    ctrls_group = [c for c in top_meta.children if "top-group-controls" in c.classes][0]
    expected_ids = ["btn-master-toggle", "btn-sync", "btn-reset", "btn-command-palette"]
    for eid in expected_ids:
        assert ctrls_group.find_one(id=eid) is not None, f"Expected #{eid} in Run controls group"


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


def test_status_pills_share_markup(dom: DOMNode):
    """All top-nav status pills must share the .pill mono base and carry .pill-label and .pill-value spans."""
    pills = [
        "market-scan-pill",
        "scan-state-pill",
        "master-status-indicator",
        "hud-services-pill",
        "hud-guardrail-pill",
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


def test_pill_value_css(css: str):
    match = re.search(r"\.pill-value\s*\{([^}]*)\}", css)
    assert match, ".pill-value rule not found in styles.css"
    assert "font-weight" in match.group(1)


@requires_node
def test_scan_pill_renders_grammar():
    data = _harness("topnav")["scan"]
    assert {"pill", "state-running", "mono"}.issubset(set(data["className"].split()))
    assert 'class="pill-label"' in data["innerHTML"]
    assert 'class="pill-value"' in data["innerHTML"]
    assert data["textContent"].startswith("SCAN RUNNING")


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
def test_db_badge_renders_grammar():
    data = _harness("topnav")["dbProd"]
    assert {"pill", "mode-live", "mono"}.issubset(set(data["className"].split()))
    assert 'class="pill-label"' in data["innerHTML"]
    assert 'class="pill-value"' in data["innerHTML"]
    assert data["textContent"] == "DB LIVE"


@requires_node
def test_master_and_services_render_grammar():
    data = _harness("topnav")["masterRunning"]
    assert {"pill", "state-running", "mono"}.issubset(set(data["className"].split()))
    assert 'pulse-dot active' in data["innerHTML"]
    assert 'class="pill-label"' in data["innerHTML"]
    assert 'class="pill-value"' in data["innerHTML"]
    assert data["textContent"] == "STACK RUNNING"
    assert "services-pill-trigger" in data["servicesClass"]
