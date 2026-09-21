"""Dashboard input lag: polls repaint the visible tab only, switches paint now (#264).

The page used to re-render every section on every 2s poll — both hidden tabs
plus the Monte Carlo / KDE / markout charts — one long main-thread task that a
tab click waited behind for 2-4 seconds. Now each poll repaints the cheap
header pills plus only the visible tab, and switching tabs repaints the newly
shown tab synchronously from the cached poll snapshot.

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


def _run(scenario: str) -> dict:
    out = subprocess.run([shutil.which("node"), str(HARNESS), json.dumps({"scenario": scenario})],
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


def test_heavy_charts_paint_after_the_tiles():
    # Arrange — the analytics surface holds the Monte Carlo / KDE / markout
    # charts, the heaviest synchronous block in the poll render chain.
    source = APP_JS.read_text(encoding="utf-8")

    # Assert — tiles paint first; the chart surface is deferred one frame so a
    # click arriving mid-render is handled between the two paints.
    assert re.search(r"deferPaint\(\(\)\s*=>\s*renderAnalyticsSurface", source), \
        "renderKPIs must route renderAnalyticsSurface through deferPaint"
    assert "requestAnimationFrame" in source
