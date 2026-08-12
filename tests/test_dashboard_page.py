"""The fleet dashboard page must actually PARSE.

Written after shipping a blank dashboard. The page had been "verified" by
checking that /api/state returned the right JSON and that the expected strings
were present in the HTML -- both of which passed while the page rendered
nothing at all. The cause was a duplicate `const bar` in the same function
scope: a SyntaxError, which aborts the entire <script> tag before a single
line runs. Every element stays empty and the browser logs nothing useful.

Neither an API check nor a string grep can catch that class of bug. Only
parsing the script can. This test does exactly that for the live fleet
dashboard. The archived kanban / single-bot page validation lived alongside
this on the now-moved ``archive/legacy-bot-8788`` branch; the kanban page
itself is no longer importable on this branch.
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Only the live dashboard is rendered on this branch; the legacy "kanban"
# page lived in server/kanban.py and is preserved on the
# archive/legacy-bot-8788 branch (alongside the rest of the port-8788
# single-bot pipeline). Importing it here would point at the archive
# snapshot, which is not what this regression test is for.
from server.fleet_dash import PAGE as FLEET_PAGE  # noqa: E402
from server.spread_dash_html import DASHBOARD_HTML  # noqa: E402

NODE = shutil.which("node")

# One page, one flatten, one parse: SyntaxError in any <script> renders a
# fully blank dashboard, not a degraded one.
PAGES = {"fleet": FLEET_PAGE, "spread": DASHBOARD_HTML}


def _script_blocks(page: str | None = None) -> list[str]:
    return re.findall(r"<script>([\s\S]*?)</script>",
                      FLEET_PAGE if page is None else page)


def test_page_has_a_script_block():
    assert _script_blocks(), "the dashboard is inert without its <script>"


@pytest.mark.skipif(NODE is None, reason="node not installed")
@pytest.mark.parametrize("name", sorted(PAGES))
def test_dashboard_script_parses(tmp_path, name):
    """A parse error here is a fully blank page, not a degraded one."""
    for i, src in enumerate(_script_blocks(PAGES[name])):
        f = tmp_path / f"{name}{i}.js"
        # --check parses without executing, so no browser globals are needed.
        f.write_text(src, encoding="utf-8")
        r = subprocess.run([NODE, "--check", str(f)],
                           capture_output=True, text=True)
        assert r.returncode == 0, (
            f"dashboard script block {i} does not parse -- the page will render "
            f"BLANK:\n{r.stderr}")


@pytest.mark.parametrize("name", sorted(PAGES))
def test_no_duplicate_top_level_consts_all_pages(name):
    for src in _script_blocks(PAGES[name]):
        names = re.findall(r"^const\s+([A-Za-z_$][\w$]*)\s*=", src, re.M)
        dupes = {n for n in names if names.count(n) > 1}
        assert not dupes, f"{name}: duplicate const declarations: {sorted(dupes)}"


def test_settled_rows_harden_market_identifiers():
    """PR #22 review: a persisted market slug must never reach an inline
    handler, an unescaped attribute, or a raw link. The row carries
    data-market (attribute-escaped) and a delegated DOM listener, the link
    path is URL-encoded, and both formatted titles are HTML-escaped."""
    page = DASHBOARD_HTML
    assert 'data-market="${escAttr(g.market)}"' in page
    assert 'onclick="toggleMarketExpand(' not in page
    assert 'https://polymarket.com/event/${encodeURIComponent(g.market)}' in page
    assert '${esc(formatMarketTitle(g.market))}' in page
    assert '${esc(formatMarketTitle(x.market))}' in page
    assert 'function escAttr(' in page
    assert 'const row = e.target.closest("[data-market]");' in page


def test_grouped_return_uses_aggregate_pnl_over_cost_basis():
    """PR #22 review: the grouped return must be 100 * total_pnl /
    total_cost_basis, not a mean of per-exit percentages -- the mean only
    matches when every exit shares the same cost basis."""
    page = DASHBOARD_HTML
    assert '100 * g.total_pnl / g.total_cost_basis' in page
    assert 'count_pct' not in page


def test_dashboard_auto_refreshes_every_15s():
    """Auto-refresh polls the four endpoints in place instead of rebuilding
    the page: a 15s interval drives refresh(), renderSummary() is the single
    summary renderer shared by boot and refresh, a busy guard stops polls
    from overlapping, and the refresh reuses the stateful renderers so open
    settled groups, pagination, and the active tab survive."""
    page = DASHBOARD_HTML
    assert "setInterval(refresh, 15000)" in page
    assert "async function refresh()" in page
    assert "function renderSummary(s)" in page
    assert "if (REFRESH_BUSY) return;" in page
    assert "if (st) renderSettled(st.settled, st.total_closes);" in page
    # The settled list can shrink between polls; the page index is clamped
    # so a refresh never strands the table on an empty page.
    assert "settledState.page = page;" in page


def test_data_change_cues_and_market_drawer_are_wired():
    """Phase-1 interactions: financial figures flash on change (data-kpi /
    data-v), hero numbers roll up (data-rollup), status cells fade+scale in
    (data-state), the data-health dot pulses (health-pulse), and clicking a
    market row opens the right-edge drawer (data-drawer + openDrawer) whose
    content is rendered from data already in memory."""
    page = DASHBOARD_HTML
    assert "function animateChanges()" in page
    assert "function animateNumber(" in page
    assert "data-kpi=\"hero_realized\"" in page
    assert "data-rollup" in page
    assert "data-state=\"${escAttr(r.market)}\"" in page
    assert "health-pulse" in page
    assert "data-drawer=\"${escAttr(r.market)}\"" in page
    assert "function openDrawer(slug)" in page
    assert "function closeDrawer()" in page
    assert "function renderDrawer()" in page
    assert 'id="drawer"' in page
    assert "translate-x-full" in page
    assert "rgba(34,197,94,.2)" in page
    # Drawer wiring must not regress the PR #22 hardening: the settled
    # rows still expand inline, never via the drawer.
    assert "toggleMarketExpand(row.getAttribute(\"data-market\"))" in page


def test_order_depth_view_uses_live_orders_and_mid_axis():
    """The market table must show live resting depth, not a stale flat position."""
    assert '<th>Order depth / mid</th>' in FLEET_PAGE
    assert 'function orderDepth(m)' in FLEET_PAGE
    assert 'm.quotes||[]' in FLEET_PAGE
    assert 'm.mid_up' in FLEET_PAGE
    assert 'YES ${upSh.toFixed(0)} sh' in FLEET_PAGE
    assert 'NO ${dnSh.toFixed(0)} sh' in FLEET_PAGE
    assert "const v=(m.max_spread||0.045);" in FLEET_PAGE
    assert 'function posBar(m)' not in FLEET_PAGE
    assert 'position/risk' not in FLEET_PAGE.lower()
    assert '<th>Last action</th>' in FLEET_PAGE
    assert 'function orderDepth(m)' in FLEET_PAGE
    assert "color==='var(--gold)'?'3px':'2px'" in FLEET_PAGE
    assert 'action-pill' in FLEET_PAGE
    assert 'events.slice(1,3)' in FLEET_PAGE
    assert 'Fleet Naked Risk' in FLEET_PAGE
    assert 'Gate Refusals' in FLEET_PAGE
    assert 'Active Quoting Markets' in FLEET_PAGE


def test_no_duplicate_top_level_consts():
    """Top-level duplicate `const` in the same script block.

    The blank-dashboard bug class is a SyntaxError that aborts the entire
    ``<script>`` tag and leaves every element empty. Two `const NAME` lines
    at the top level of one script is the input the engine rejects. This
    regex only catches top-level duplicates (no leading whitespace);
    duplicates inside separate function bodies are legal in JS and are
    caught by ``test_dashboard_script_parses`` (node --check will reject
    any actual scope violation).
    """
    for src in _script_blocks(PAGES["fleet"]):
        names = re.findall(r"^const\s+([A-Za-z_$][\w$]*)\s*=", src, re.M)
        dupes = {n for n in names if names.count(n) > 1}
        assert not dupes, f"duplicate const declarations: {sorted(dupes)}"


def test_market_pipeline_view_is_wired_in():
    """The fleet page and the market-scan view must both exist, with the
    switcher, the four-lane board, and the JS that renders it from
    /api/pipeline -- the whole point of the view is seeing the funnel live,
    so a board skeleton without its renderer is the blank-page bug class."""
    assert 'id="view-fleet"' in FLEET_PAGE
    assert 'id="view-pipeline"' in FLEET_PAGE
    assert 'id="viewFleet"' in FLEET_PAGE
    assert 'id="viewScan"' in FLEET_PAGE
    assert 'class="view-btn active"' in FLEET_PAGE
    assert 'id="laneRaw"' in FLEET_PAGE
    assert 'id="laneFilter"' in FLEET_PAGE
    assert 'id="laneFinal"' in FLEET_PAGE
    assert 'id="laneGrad"' in FLEET_PAGE
    assert 'id="pipeStrip"' in FLEET_PAGE
    assert 'function pipeLane(' in FLEET_PAGE
    assert 'function pipeFilter(snap)' in FLEET_PAGE
    assert 'function pipeGrad(s)' in FLEET_PAGE
    assert 'async function tickPipeline()' in FLEET_PAGE
    assert "fetch('/api/pipeline'" in FLEET_PAGE
    assert 'setInterval(tickPipeline,10000)' in FLEET_PAGE
