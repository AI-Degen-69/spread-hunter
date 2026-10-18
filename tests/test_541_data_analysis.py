"""#541/#568: the Data Analysis sidebar page renders once-placed stats with units.

Drives dashboard/static/analysis_v2.js through
tests/js/analysis_v2_harness.cjs against the T1 fixture payload. Every figure
sits in exactly one card under #analysis-v2; the shell poll feeds the page
from the same GET /api/kpi snapshot as every other surface.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from core_brain.kpi import report as kpi_report

from tests.stat_541_fixture import RUN_ID_541, build_fixed_run

HARNESS = Path(__file__).resolve().parent / "js" / "analysis_v2_harness.cjs"
STATIC = Path(__file__).resolve().parent.parent / "dashboard" / "static"
V2_JS = STATIC / "analysis_v2.js"
INDEX_HTML = STATIC / "index.html"

requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node is not installed on this host")


def _run(payload: dict, action: str = "render", tmp_path=None) -> dict:
    target = Path(str(tmp_path or ".")) / f"av2_harness_{action}.json"
    target.write_text(json.dumps({"kpi": payload}), encoding="utf-8")
    out = subprocess.run([shutil.which("node"), str(HARNESS),
                          str(target), action],
                         capture_output=True, text=True, check=True,
                         encoding="utf-8")
    return json.loads(out.stdout)


def _fixture_payload(tmp_path) -> dict:
    db = build_fixed_run(tmp_path / "da.db")
    return kpi_report(db, run_id=RUN_ID_541)


def test_old_surface_removed():
    """The #541 STAT_REGISTRY implementation is retired, not shadowed."""
    assert not (STATIC / "data_analysis.js").exists()
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert "da-surface" not in html
    assert "STAT_REGISTRY" not in (STATIC / "app.js").read_text(encoding="utf-8")


def test_single_analysis_surface_in_tab4():
    """Tab 4 holds exactly one analysis surface: #analysis-v2."""
    html = INDEX_HTML.read_text(encoding="utf-8")
    tab4 = html.split('id="tab-4"')[1].split("</section>")[0]
    assert tab4.count('id="analysis-v2"') == 1
    assert "da-surface" not in tab4


def test_shell_poll_feeds_analysis_v2():
    """Both poll call sites feed the cached snapshot to the v2 renderer."""
    src = (STATIC / "app.js").read_text(encoding="utf-8")
    assert src.count("renderAnalysisV2(") >= 2
    assert "renderDataAnalysis" not in src
    js = (STATIC / "prototype.js").read_text(encoding="utf-8")
    assert "'#analysis-v2'" in js


@requires_node
def test_da_empty_payload_mounts_without_throwing(tmp_path):
    rendered = _run({}, action="empty", tmp_path=tmp_path)
    assert rendered["errors"] == []


@requires_node
def test_da_fixture_close_count_and_units_in_headline_and_gates(tmp_path):
    """8 closes (5W/2L); headline, gates and capital carry units and values."""
    rendered = _run(_fixture_payload(tmp_path), tmp_path=tmp_path)
    assert rendered["errors"] == []
    p = rendered["painted"]
    assert p["trade_analytics.n_closes"] == "8"
    assert p["_winloss"] == "5W / 2L"
    # Units ride on the values: % / $ / x families all present.
    assert p["trade_analytics.mean_return_pct"].endswith("%")
    assert p["trade_analytics.expectancy_usd"].startswith(("$", "-", "+"))
    assert "x" in p["trade_analytics.profit_factor"]
    assert rendered["gates"]["power"]["observed"].startswith("8 / ")
    assert rendered["gates"]["drawdown_guard"]["observed"] != ""


@requires_node
def test_da_capital_keeps_account_curve_meaning(tmp_path):
    """Drawdown language stays dollar-signed with an honest recovery state."""
    rendered = _run(_fixture_payload(tmp_path), tmp_path=tmp_path)
    p = rendered["painted"]
    assert "$" in p["statistical_analytics.capital_risk.drawdown_recovery.deepest_usd"]
    assert p["_ddRecovered"] in ("regained", "still open")


@requires_node
def test_da_charts_each_get_their_own_series(tmp_path):
    """ch1-ch4 draw their own series; ch5 blanks honestly with no matured fills."""
    rendered = _run(_fixture_payload(tmp_path), tmp_path=tmp_path)
    assert rendered["errors"] == []
    charts = {c["id"]: c for c in rendered["charts"]}
    assert set(charts) == {"ch1", "ch2", "ch3", "ch4", "ch5"}
    for cid in ("ch1", "ch2", "ch3", "ch4"):
        c = charts[cid]
        assert c["svg"].startswith("<svg"), f"{cid} painted no svg"
        assert "<path" in c["svg"] or "<rect" in c["svg"], f"{cid} drew no series"
        assert c["badge"], f"{cid} has no badge"
        assert c["stats"], f"{cid} has no stats line"
    # ch5 (markout by horizon) needs matured fills the 8-close fixture has
    # none of: it must blank, never fabricate. The demo-DB binding suite
    # (test_all_five_charts_paint_real_series) proves ch5 draws with data.
    assert charts["ch5"]["svg"] == "" and charts["ch5"]["badge"] == ""
