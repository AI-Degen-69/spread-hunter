"""#541 T3: the Data Analysis tab renders once-claimed stats with units.

Drives dashboard/static/data_analysis.js (plus app.js shared renderers) through
tests/js/data_analysis_harness.cjs against the T1 fixture payload.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from core_brain.kpi import report as kpi_report

from tests.stat_541_fixture import RUN_ID_541, build_fixed_run

HARNESS = Path(__file__).resolve().parent / "js" / "data_analysis_harness.cjs"
STATIC = Path(__file__).resolve().parent.parent / "dashboard" / "static"
DA_JS = STATIC / "data_analysis.js"
INDEX_HTML = STATIC / "index.html"

requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node is not installed on this host")


def _run(payload: dict, action: str = "render", tmp_path=None) -> dict:
    target = Path(str(tmp_path or ".")) / f"da_harness_{action}.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    out = subprocess.run([shutil.which("node"), str(HARNESS),
                          str(target), action],
                         capture_output=True, text=True, check=True,
                         encoding="utf-8")
    return json.loads(out.stdout)


def _fixture_payload(tmp_path) -> dict:
    db = build_fixed_run(tmp_path / "da.db")
    return {"kpi": kpi_report(db, run_id=RUN_ID_541)}


@requires_node
def test_da_renders_every_block_exactly_once(tmp_path):
    rendered = _run(_fixture_payload(tmp_path), tmp_path=tmp_path)
    assert rendered["errors"] == []
    assert sorted(rendered["claimed"]) == sorted(rendered["registryBlocks"])
    assert len(rendered["claimed"]) == 16


@requires_node
def test_da_double_claim_of_a_block_throws(tmp_path):
    rendered = _run(_fixture_payload(tmp_path), action="double-claim", tmp_path=tmp_path)
    assert rendered["errors"] == []
    assert rendered["doubleClaimThrew"] is True
    assert "da-quant" in rendered["doubleClaimMessage"]


@requires_node
def test_da_empty_payload_mounts_without_throwing(tmp_path):
    rendered = _run({}, action="empty", tmp_path=tmp_path)
    assert rendered["errors"] == []


@requires_node
def test_da_headline_gates_and_quant_carry_units_and_values(tmp_path):
    rendered = _run(_fixture_payload(tmp_path), tmp_path=tmp_path)
    html = rendered["html"]
    assert "Mean Return Per Trade (%)" in html["headline"]
    assert "8 Closes" in rendered["samplePill"]
    assert "Max Drawdown Guard" in html["gates"]
    assert "Statistical Power Target" in html["gates"]
    assert "Mathematical Expectancy ($)" in html["quant"]
    assert "Win Rate &amp; Wilson CI (%)" in html["quant"]


@requires_node
def test_da_capital_names_the_account_curve_not_the_equity_curve(tmp_path):
    rendered = _run(_fixture_payload(tmp_path), tmp_path=tmp_path)
    capital = rendered["html"]["capital"]
    assert "account curve" in capital
    assert "on the equity curve" not in capital
    assert "has no points yet" not in capital  # fixture has points; guard text absent


@requires_node
def test_da_charts_paint_into_their_own_containers(tmp_path):
    rendered = _run(_fixture_payload(tmp_path), tmp_path=tmp_path)
    html = rendered["html"]
    for key in ("dist", "distFooter", "paircost", "mc", "bell", "markout"):
        assert html[key].strip(), f"chart block {key} rendered blank"


def test_da_registry_blocks_exist_in_the_tab_markup():
    html = INDEX_HTML.read_text(encoding="utf-8")
    for block in ("da-headline", "da-pnl-ci", "da-sufficiency", "da-funnel", "da-gates",
                  "da-fillpath", "da-rescue", "da-queue", "da-robustness", "da-capital",
                  "da-quant", "da-dist-container", "da-paircost-container",
                  "da-mc-container", "da-bell-container", "da-markout-container"):
        assert f'id="{block}"' in html, f"registry block {block} has no container"


def test_da_required_observations_has_one_read_site():
    lines = [ln for ln in DA_JS.read_text(encoding="utf-8").splitlines()
             if "ta.required_observations" in ln]
    assert len(lines) == 1, f"want 1 read site, got {len(lines)}: {lines}"


def test_da_registry_entries_all_carry_a_unit_and_valid_refs():
    src = DA_JS.read_text(encoding="utf-8")
    ids = re.findall(r"'([\w.]+)': \{ producer:", src)
    assert len(ids) >= 40, f"registry looks thin: {len(ids)} entries"
    for stat_id in ids:
        assert re.search(r"'%s': \{[^}]*unit: '[^']+'" % re.escape(stat_id), src), \
            f"{stat_id} has no unit"
    for ref in re.findall(r"refOf: '([\w.]+)'", src):
        assert ref in ids, f"refOf target {ref} is not a registry id"
