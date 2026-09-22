"""Dashboard input lag: polls repaint the visible tab only, switches paint now (#264).

The page used to re-render every section on every 2s poll — both hidden tabs
plus the Monte Carlo / KDE / markout charts — one long main-thread task that a
tab click waited behind for 2-4 seconds. Now each poll repaints the cheap
header pills plus only the visible tab, and switching tabs repaints the newly
shown tab synchronously from the cached poll snapshot.

#266 follow-up: the sidebar-pages layout (#140) moved every live panel under
`#page-*` sections and shows one at a time, leaving the legacy `#tab-1..3`
shells un-hidden — which made every tab-visibility gate read "visible" and put
the whole render chain back on every poll (multi-second freezes, ~5k DOM
nodes/sec of churn). Poll gating must consult the active rail page.

Driven through node against the real `dashboard/static/app.js` and a stub DOM,
so these are the paints the page would actually perform.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parent / "js" / "input_lag_harness.cjs"
APP_JS = Path(__file__).resolve().parent.parent / "dashboard" / "static" / "app.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not installed on this host")


def _run(scenario: str, fetch_ok: bool = True) -> dict:
    out = subprocess.run(
        [shutil.which("node"), str(HARNESS),
         json.dumps({"scenario": scenario, "fetchOk": fetch_ok})],
        capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_poll_skips_hidden_tabs_but_keeps_header_live():
    # Arrange — operator sits on Tab 1 while a full poll snapshot arrives.
    # Act
    res = _run("hidden-tabs-skipped")

    # Assert — visible tab painted, header pill painted, hidden tabs untouched.
    assert res["tab1Head"] != ""
    assert res["scanPill"] != ""
    assert res["kpiGrid"] == ""
    assert res["marketBody"] == ""
    assert res["kanbanBoard"] == ""


def test_poll_paints_only_the_active_rail_page():
    # Arrange — the real post-#140 layout: the rail mounted and shows the
    # Trades page (service cards + ticker), while the legacy tab shells sit
    # un-hidden (prototype.js unhides them after moving every panel out).
    # Act
    res = _run("rail-pages-skipped")

    # Assert — the active rail page's sections painted; the pages the rail
    # keeps hidden (Reports tiles, Data & Markets tables, kanban) untouched.
    # The header stays live regardless of the page shown.
    assert res["serviceCards"] != ""
    assert res["masterIndicator"] != ""
    assert res["brokerHero"] == ""
    assert res["ordersHead"] == ""
    assert res["kpiGrid"] == ""
    assert res["marketBody"] == ""
    assert res["kanbanBoard"] == ""
    assert res["scanPill"] != ""


def test_poll_skips_offpage_renders_even_when_tab_gates_all_claim_visible():
    # Arrange — exactly the state prototype.js leaves the page in: the operator
    # on the rail's Dashboard while every legacy tab gate reads visible. This
    # is the regression that froze hover/click input for seconds per poll.
    # Act
    res = _run("rail-skips-offpage-renders")

    # Assert — Home's own Orders & Trades painted; the service cards (Trades),
    # the KPI tiles (Reports) and the market table + kanban (Data & Markets)
    # living on other rail pages were skipped even though the tab gates all
    # claimed to be visible. Header stays live — including the master-stack
    # pill renderServiceCards paints: it sits in the top nav on every page,
    # so a poll that skips the Trades grid must still refresh it. The
    # portfolio hero is Home's own section (#140 moved it there), so it must
    # paint with Home even though renderKPIs also owns the Reports tiles.
    assert res["serviceCards"] == ""
    assert res["masterIndicator"] != ""
    assert res["brokerHero"] != ""
    assert res["ordersHead"] != ""
    assert res["kpiGrid"] == ""
    assert res["marketBody"] == ""
    assert res["kanbanBoard"] == ""
    assert res["scanPill"] != ""


def test_tab_switch_paints_synchronously_from_cache():
    # Arrange — caches filled by an earlier poll, operator on Tab 1.
    # Act — one synchronous switchTab(2), no poll in between.
    res = _run("switch-paints-from-cache")

    # Assert — panels flipped immediately and Tab 2 repainted from cache,
    # while the still-hidden Tab 3 stayed untouched.
    assert res["tab1Hidden"] is True
    assert res["tab2Hidden"] is False
    assert res["tab3Hidden"] is True
    assert res["kpiGrid"] != ""
    assert res["marketBody"] != ""
    assert res["kanbanBoard"] == ""


def test_rapid_clicks_end_on_the_last_tab_with_no_queued_replay():
    # Arrange — caches filled by an earlier poll, operator on Tab 1.
    # Act — three synchronous clicks: Tab 2, back to Tab 1, then Tab 3.
    res = _run("switch-tab3-double-click")

    # Assert — toggles are synchronous, so nothing queues: only Tab 3 stands
    # visible and painted. Tab 2 keeps its last paint while hidden, so
    # switching back is instant instead of flashing blank.
    assert res["tab1Hidden"] is True
    assert res["tab2Hidden"] is True
    assert res["tab3Hidden"] is False
    assert res["kpiGrid"] != ""
    assert res["kanbanBoard"] != ""


def test_a_failed_poll_never_crashes_and_leaves_hidden_tabs_blank():
    # Arrange — dead backend, all tabs visible.
    # Act — one poll tick against failing endpoints.
    res = _run("failed-poll", fetch_ok=False)

    # Assert — no throw, nothing painted from nothing, header honest.
    assert res["crashed"] is False
    assert res["kpiGrid"] == ""
    assert res["marketBody"] == ""
    assert res["kanbanBoard"] == ""
    assert res["scanPill"] != ""


def test_a_failed_poll_after_a_good_poll_keeps_cache_and_hides_stale_ready():
    # Arrange — one good poll fills every tab, then the backend dies.
    # Act — the next poll fails.
    res = _run("failed-after-cache")

    # Assert — no throw, the cached tiles survive (repainted from cache, still
    # non-blank), the stale READY banner hides, and the header stays honest.
    assert res["crashed"] is False
    assert res["kpiBefore"] != ""
    assert res["kpiAfter"] != ""
    assert res["readinessDisplay"] == "none"
    assert res["scanPill"] != ""


def test_heavy_charts_wait_a_frame_while_tiles_paint_now():
    # Arrange/Act — the deferral mechanism itself through the real export.
    res = _run("charts-deferred")

    # Assert — with rAF present the callback waits exactly one frame; without
    # rAF (node fallback) it runs synchronously. Either way tiles are never
    # stuck behind the charts.
    assert res["queuedBeforeFlush"] == 1
    assert res["ranBeforeFlush"] == 0
    assert res["flushed"] == 1
    assert res["ranAfterFlush"] == 1
    assert res["ranSync"] == 1


def test_heavy_charts_paint_after_the_tiles():
    # Arrange — the analytics surface holds the Monte Carlo / KDE / markout
    # charts, the heaviest synchronous block in the poll render chain.
    source = APP_JS.read_text(encoding="utf-8")

    # Assert — tiles paint first; the chart surface is deferred one frame so a
    # click arriving mid-render is handled between the two paints.
    assert re.search(r"deferPaint\(\(\)\s*=>\s*\{[\s\S]*?renderAnalyticsSurface", source), \
        "renderKPIs must route renderAnalyticsSurface through deferPaint"
    assert "requestAnimationFrame" in source
