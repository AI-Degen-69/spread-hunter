"""The Data Analysis sidebar page paints live telemetry, not mock numbers.

Drives dashboard/static/analysis_v2.js through tests/js/analysis_v2_harness.cjs
against the real `core_brain.kpi.report()` payload shape, so every data-bind
hook in the sidebar markup is checked against a field that actually exists.

The page is read-only by construction: its only telemetry read is the shell's
existing GET /api/kpi poll (plus ?run_id=), which feeds renderAnalysisV2.
The module itself never fetches. (#568: analysis-v2 IS the Data Analysis
sidebar page; the standalone /analysis-v2 route is retired.)
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from core_brain.kpi import report as kpi_report

HARNESS = Path(__file__).resolve().parent / "js" / "analysis_v2_harness.cjs"
STATIC = Path(__file__).resolve().parent.parent / "dashboard" / "static"
INDEX = STATIC / "index.html"
PAGE_JS = STATIC / "analysis_v2.js"
PAGE_CSS = STATIC / "analysis_v2.css"
DEMO_DB = Path("data/demo_orders.db")

requires_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _run(payload: dict, action: str = "render", tmp_path=None) -> dict:
    src = tmp_path / "analysis_v2_payload.json" if tmp_path else None
    if src is None:
        raise AssertionError("harness reads its payload from a file; pass tmp_path")
    src.write_text(json.dumps({"kpi": payload}), encoding="utf-8")
    proc = subprocess.run(
        ["node", str(HARNESS), str(src), action],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert proc.returncode == 0, f"harness failed: {proc.stderr[:2000]}"
    return json.loads(proc.stdout)


@pytest.fixture(scope="module")
def live_payload(tmp_path_factory) -> dict:
    """The real /api/kpi shape from the committed demo registry.

    ``data/demo_orders.db`` is the one database in ``data/`` the repo
    tracks (the .gitignore negation exists so it can be served for
    dashboard review), so a fresh checkout -- CI included -- has it.

    The report is taken from a COPY: `kpi_report` opens the registry
    read-write (schema check plus WAL), so a full-suite run rewrote the tracked
    fixture's bytes and showed up as `M data/demo_orders.db` every time.
    """
    demo_copy = Path(shutil.copy(DEMO_DB, tmp_path_factory.mktemp("demo") / DEMO_DB.name))
    return kpi_report(db_path=str(demo_copy))


def test_page_and_assets_are_served_from_static():
    """The sidebar page owns the markup; the static mount serves the assets."""
    assert 'id="analysis-v2"' in INDEX.read_text(encoding="utf-8")
    assert PAGE_CSS.is_file()
    assert PAGE_JS.is_file()
    # The stylesheet link must be absolute: a relative "styles.css" would
    # resolve against whatever path serves the document. The ?v= bump keeps a
    # browser that cached the retired /analysis-v2 page (which served these
    # two unversioned, with unscoped :root/* rules) from reusing that copy.
    assert 'href="/static/analysis_v2.css?v=1"' in INDEX.read_text(encoding="utf-8")
    assert '<script src="/static/analysis_v2.js?v=1">' in INDEX.read_text(encoding="utf-8")


def test_standalone_page_removed():
    """No second copy of the page remains on disk or in the shell."""
    assert not (STATIC / "analysis_v2.html").exists()
    html = INDEX.read_text(encoding="utf-8")
    assert "da-surface" not in html
    assert "data_analysis.js" not in html
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "renderDataAnalysis" not in js


def test_standalone_route_removed():
    """/analysis-v2 is retired; the legacy routes are untouched."""
    from dashboard import server

    paths = {getattr(r, "path", None) for r in server.app.routes}
    assert "/analysis-v2" not in paths
    # Guardrails: nothing the operator relies on moved or vanished.
    for legacy in ("/", "/prototype", "/api/kpi", "/api/state", "/api/system/status"):
        assert legacy in paths, f"regression: {legacy} disappeared"


def test_analysis_v2_module_never_fetches():
    """One data owner: the shell poll fetches; the renderer only paints."""
    src = PAGE_JS.read_text(encoding="utf-8")
    assert "fetch(" not in src
    assert "XMLHttpRequest" not in src
    assert "EventSource(" not in src
    assert "renderAnalysisV2" in src


def _panel(html: str) -> str:
    """`#analysis-v2` and everything inside it, bounded by balanced <div> tags.

    Splitting on the first `</section>` stopped inside the first KPI strip: the
    panel nests <section> and <div> blocks, so ids such as `ciScale` and
    `ch1`..`ch5` were never reached by the duplicate-id check, and mock numbers
    further down the page were never seen.
    """
    at = html.index('id="analysis-v2"')
    open_at = html.rindex("<div", 0, at)
    depth = 0
    pos = open_at
    while True:
        next_open = html.find("<div", pos + 4)
        next_close = html.find("</div>", pos + 4)
        if next_close == -1:
            raise AssertionError("#analysis-v2 has no closing </div>")
        if next_open != -1 and next_open < next_close:
            depth += 1
            pos = next_open
        elif depth == 0:
            return html[open_at:next_close + len("</div>")]
        else:
            depth -= 1
            pos = next_close


def test_analysis_ids_unique():
    """Every id inside #analysis-v2 occurs exactly once in the document."""
    import re

    html = INDEX.read_text(encoding="utf-8")
    section = _panel(html)
    ids = re.findall(r'id="([^"]+)"', section)
    assert ids, "no ids found inside #analysis-v2"
    for ident in set(ids):
        assert html.count(f'id="{ident}"') == 1, f"id {ident} occurs more than once"


@requires_node
def test_every_bind_hook_paints_a_real_value(tmp_path, live_payload):
    """No data-bind is left blank and no mock number survives."""
    out = _run(live_payload, "render", tmp_path)
    assert not out["errors"], out["errors"]
    assert out["hookCount"] >= 45, f"only {out['hookCount']} bind hooks found"

    # Fields this (real, sparse) demo cannot measure stay blank ("unmeasured")
    # rather than inventing a number. That is the honest answer, not a bug.
    expected_blank = {
        "statistical_analytics.capital_risk.drawdown_recovery.deepest_pct",
        "statistical_analytics.capital_risk.drawdown_recovery.deepest_usd",
        "statistical_analytics.capital_risk.sortino_ratio.sortino_ratio",
        "statistical_analytics.fill_path_edge.rescue_expectancy_usd",
        "statistical_analytics.rescue_friction.mean_execution_drag_cents",
        "statistical_analytics.rescue_friction.mean_latency_drag_cents",
        "statistical_analytics.rescue_friction.mean_overshoot_ratio",
        "statistical_analytics.rescue_friction.mean_sled_cents",
        "statistical_analytics.rescue_friction.mean_tcdr",
        "statistical_analytics.robustness.mae.median_cents",
        "trade_analytics.half_kelly",
        "trade_analytics.kelly_fraction",
        "trade_analytics.payoff_ratio",
        "trade_analytics.profit_factor",
    }
    blank = {k for k, v in out["painted"].items() if v in (None, "")}
    assert blank == expected_blank, f"unexpected blank hooks: {blank ^ expected_blank}"

    painted = out["painted"]
    # Spot-check the numbers an operator would actually read, against the
    # payload's own values (this demo is the real shadow-run registry).
    assert painted["trade_analytics.mean_return_pct"] == "6.76%"
    assert painted["statistical_analytics.fill_path_edge.net_expectancy_usd"] == "+$0.50"
    assert painted["trade_analytics.win_rate"] == "100.0%"
    assert painted["trade_analytics.n_closes"] == "3"
    assert painted["_required"] == "120"
    assert painted["_winloss"] == "3W / 0L"
    # No rescues on this run: the rate reads 0.0% and friction is unmeasured.
    assert painted["statistical_analytics.fill_path_edge.rescue_rate"] == "0.0%"
    assert painted["statistical_analytics.rescue_friction.mean_tcdr"] == ""
    # All-win run: no loss denominator, so Kelly is unmeasured.
    assert painted["trade_analytics.kelly_fraction"] == ""


def test_no_mock_value_survives_on_the_page():
    """The design's hardcoded numbers must not still be in the markup."""
    html = INDEX.read_text(encoding="utf-8")
    section = _panel(html)
    # Only the pure telemetry mocks: "9W / 6L" is a legitimate tooltip
    # value; default sufficiency-table cells repaint from the payload.
    for mock in ("-6.76%", "-$0.41", "60.0%", "-$1.30", "+$0.32"):
        assert mock not in section, f"mock value still hardcoded in markup: {mock}"


@requires_node
def test_units_and_signs_match_the_payload(tmp_path, live_payload):
    """$ and ¢ are signed; % and x are signed only when negative."""
    out = _run(live_payload, "render", tmp_path)
    p = out["painted"]
    # No rescues on this run: rescue expectancy is unmeasured (blank), merge positive.
    assert p["statistical_analytics.fill_path_edge.rescue_expectancy_usd"] == ""
    assert p["statistical_analytics.fill_path_edge.merge_expectancy_usd"].startswith("+")
    # No rescues: rescue friction is unmeasured.
    assert p["statistical_analytics.rescue_friction.mean_sled_cents"] == ""
    # This run is net positive: mean return reads positive (un-signed `pct`).
    assert p["trade_analytics.mean_return_pct"] == "6.76%"
    # All-win run: no loss denominator, so profit factor is unmeasured.
    assert p["trade_analytics.profit_factor"] == ""
    assert p["trade_analytics.max_drawdown_pct"] == "0.00%"


@requires_node
def test_unmeasured_sufficiency_rows_blank_rather_than_keep_design_numbers(tmp_path):
    """No status for a row must read '—', never the design's sample 82 / 15%.

    An empty registry still renders the four rows; if the payload carries no
    level for one, the markup's shipped numbers would otherwise survive as a
    measurement nobody made.
    """
    out = _run({}, "empty", tmp_path)
    assert not out["errors"], out["errors"]
    rows = out["sufficiency"]
    assert {r["key"] for r in rows} == {
        "pnl_expectancy", "stop_loss_rate", "merge_rate", "fill_rate",
    }
    for r in rows:
        assert r["req"] == "—", r
        assert r["rem"] == "—", r
        assert r["pct"] == "—", r
        assert r["pfill"] == "0%", r


@requires_node
def test_unmeasured_fields_blank_rather_than_invent(tmp_path, live_payload):
    """var_95/cvar_95 are null in the payload -- the page must say unmeasured."""
    out = _run(live_payload, "render", tmp_path)
    assert out["painted"]["_var"] == "unmeasured"
    assert out["painted"]["_cvar"] == "unmeasured"


@requires_node
def test_the_band_readout_owns_the_loss_verdict(tmp_path, live_payload):
    """#749: one owner for the confidence band, and it is the portfolio readout.

    The canonical page used to draw its own scale and verdict from these same
    levels. The loss-depth sentence now comes from the band readout only, so the
    page must not carry a second CI rendering of its own.
    """
    out = _run(live_payload, "render", tmp_path)
    assert not out["errors"], out["errors"]
    assert not [k for k in out["painted"] if k.startswith("_ci")]
    html = INDEX.read_text(encoding="utf-8")
    assert 'id="pnl-ci-readout"' in html
    assert 'id="ciScale"' not in html


@requires_node
def test_sufficiency_table_fills_from_the_payload(tmp_path, live_payload):
    """The 95% column the table defaults to comes from the real statuses."""
    out = _run(live_payload, "render", tmp_path)
    rows = {r["key"]: r for r in out["sufficiency"]}
    assert set(rows) == {"pnl_expectancy", "stop_loss_rate", "merge_rate", "fill_rate"}
    assert rows["pnl_expectancy"]["req"] == "97"
    assert rows["pnl_expectancy"]["cur"] == "3 closes"
    # fill_rate is counted in ORDERS, not closes -- the unit must follow.
    assert rows["fill_rate"]["cur"] == "44 orders"
    assert rows["stop_loss_rate"]["dataset"]["r98"] == "542"


@requires_node
def test_sufficiency_toggle_repaints_visible_cells(tmp_path, live_payload):
    """Clicking 98/99 repaints the visible .req/.rem/.pct cells, not just data.

    The toggle is the module's own init; the harness wires it and fires the
    pills the way a click does.
    """
    out = _run(live_payload, "toggle", tmp_path)
    assert not out["errors"], out["errors"]
    t = out["toggle"]
    rows98 = {r["key"]: r for r in t["r98"]}
    assert rows98["stop_loss_rate"]["req"] == "542"
    assert rows98["stop_loss_rate"]["rem"] == "539"
    assert rows98["pnl_expectancy"]["req"] == "136"
    rows99 = {r["key"]: r for r in t["r99"]}
    assert rows99["stop_loss_rate"]["req"] == "664"
    assert rows99["stop_loss_rate"]["rem"] == "661"
    assert rows99["pnl_expectancy"]["req"] == "166"
    rows95 = {r["key"]: r for r in t["r95"]}
    assert rows95["pnl_expectancy"]["req"] == "97"
    assert rows95["pnl_expectancy"]["rem"] == "94"
    # The progress bar tracks the same pct the cell shows.
    for rows in (rows95, rows98, rows99):
        for r in rows.values():
            assert r["pfill"] == r["pct"], r


@requires_node
def test_band_badges_come_from_the_payloads_own_verdicts(tmp_path, live_payload):
    """Payload bands drive the badges; computed gates drive their own."""
    out = _run(live_payload, "render", tmp_path)
    bands = out["bands"]
    assert bands["fp.merge_expectancy"]["cls"].endswith("badge--good")
    # No rescues on this run: taker-cost ratio is unmeasured.
    assert bands["fr.tcdr"]["cls"].endswith("badge--neu")
    assert bands["rb.sqn"]["text"] == "Good"
    # Every badge carries a label -- an empty badge is a bug.
    for ref, b in bands.items():
        assert b["text"], f"badge {ref} rendered with no label"
        assert "badge--" in b["cls"], f"badge {ref} has no badge-- class"


@requires_node
def test_gate_verdicts_follow_the_payload(tmp_path, live_payload):
    """15 closes against a 120 gate is Accumulating, never Go."""
    out = _run(live_payload, "render", tmp_path)
    g = out["gates"]
    assert g["power"]["observed"] == "3 / 120 (3%)"
    assert g["power"]["badge"] == "badge--accum"
    # ci90 lower bound is positive -> edge is Go on this run.
    assert g["edge_viability"]["observed"] == "+5.65%"
    assert g["edge_viability"]["badge"] == "badge--go"
    # 100% win rate on 3 closes is Go on the neutrality gate.
    assert g["neutrality"]["badge"] == "badge--go"
    # 0.00% drawdown is inside the 5% envelope -> Go.
    assert g["drawdown_guard"]["badge"] == "badge--go"
    # The badges themselves paint from the payload: data-band sits on the
    # badge span, so the verdict updates while the observed cell keeps its
    # own bound value (a band on the value cell would clobber the number).
    b = out["bands"]
    # #781: the mislabeled "Strategy Pricing Band / Active Rule" row is gone.
    # The sweet spot is a descriptive chart metric now, never a gate verdict.
    assert "band" not in g, f"gateVerdicts still carries a band verdict: {g['band']!r}"
    assert "gates.band" not in b, "stale gates.band badge ref still paints"
    assert b["gates.edge_viability"]["text"] == "Go"
    assert b["gates.power"]["text"] == "Accumulating"
    assert b["gates.markout"]["text"] == "Accumulating"
    assert b["gates.neutrality"]["text"] == "Go"
    assert b["gates.drawdown_guard"]["text"] == "Go"
    for ref in ("gates.edge_viability", "gates.power",
                "gates.markout", "gates.neutrality", "gates.drawdown_guard"):
        assert b[ref]["cls"].endswith("badge--go") or b[ref]["cls"].endswith("badge--nogo") \
            or b[ref]["cls"].endswith("badge--accum"), ref
    assert out["painted"]["trade_analytics.ci90_lower_pct"] == "5.65%"
    # The row's markup hooks are gone from the Data Analysis panel.
    section = _panel(INDEX.read_text(encoding="utf-8"))
    assert "Strategy Pricing Band" not in section
    assert 'data-band="gates.band"' not in section
    assert 'data-bind="_gateBand"' not in section


@requires_node
def test_all_five_charts_paint_real_series(tmp_path, live_payload):
    """Each chart card gets an svg, a badge and a stats line from live data."""
    out = _run(live_payload, "render", tmp_path)
    charts = {c["id"]: c for c in out["charts"]}
    assert set(charts) == {"ch1", "ch2", "ch3", "ch4", "ch5"}
    # ch3 (Monte Carlo) is correctly blank: 3 closes is below the sample
    # floor, so it reports nothing rather than drawing a fake fan from one
    # observation. The other four charts paint real series.
    for cid in ("ch1", "ch2", "ch4", "ch5"):
        c = charts[cid]
        assert c["svg"].startswith("<svg"), f"{cid} painted no svg"
        assert "<path" in c["svg"] or "<rect" in c["svg"], f"{cid} drew no series"
        assert c["badge"], f"{cid} has no badge"
        assert c["stats"], f"{cid} has no stats line"
    # ch3 names its unmeasured state instead of an empty box
    # (docs/specs/ci-visualizer.md); the svg still draws no series.
    assert charts["ch3"]["svg"] == ""
    assert charts["ch3"]["badge"] == "Unmeasured"

    # Chart 1's CI badge is the payload's own 95% band, from the real run.
    assert "95% CI: [+5.12%, +8.41%] · EXCLUDES ZERO" in charts["ch1"]["badge"]
    # Chart 4's sweet-spot concentration is real; ch3's P(Profit) is unmeasured.
    assert "100.0% In Sweet Spot" in charts["ch4"]["badge"]
    # Chart 2 median is a pair cost in dollars; usd fmt is always signed.
    assert "Median: +$0.94" in charts["ch2"]["badge"]


@requires_node
def test_empty_payload_mounts_without_throwing(tmp_path):
    """A fresh registry (no closes yet) must blank the page, not crash it.

    Derived fields with a documented default (the 120-close gate) or an
    explicit 'unmeasured' answer are allowed to render; what must NOT happen
    is a fabricated number or a claimed win on zero data.
    """
    out = _run({}, "empty", tmp_path)
    assert not out["errors"], out["errors"]

    p = out["painted"]
    assert p["_required"] == "120"                 # documented default
    assert p["_var"] == "unmeasured" and p["_cvar"] == "unmeasured"
    # No samples: the markout honestly says "unmeasured", never a fabricated number.
    assert "unmeasured" in p["_gateMarkout"]
    # #781: the removed gate row leaves no cells behind. It used to paint
    # "0.0% in sweet spot" + "Active Rule" on zero data — a fabricated verdict.
    assert "_gateBand" not in p, f"stale band cell painted {p['_gateBand']!r}"
    assert "gates.band" not in out["bands"]
    assert "band" not in out["gates"]
    # Nothing numeric from the live sample may appear.
    for key in ("trade_analytics.mean_return_pct", "trade_analytics.n_closes",
                "statistical_analytics.fill_path_edge.net_expectancy_usd",
                "trade_analytics.win_rate"):
        assert p[key] == "", f"{key} painted {p[key]!r} on an empty payload"
    # No chart may draw a series with no data behind it. Cards with no sample
    # name the unmeasured state outright instead of an empty box
    # (docs/specs/ci-visualizer.md); ch5 has no unmeasured copy and stays blank.
    for c in out["charts"]:
        assert c["svg"] == "", f"{c['id']} drew on empty data"
        if c["id"] in ("ch1", "ch2", "ch3", "ch4"):
            assert c["badge"] == "Unmeasured", f"{c['id']} hides its empty state"
            assert "Unmeasured" in c["stats"], f"{c['id']} hides its empty state"
        else:
            assert c["badge"] == "", f"{c['id']} drew on empty data"


@requires_node
def test_formatters_handle_the_fraction_and_percent_families():
    """pct takes a percentage; pctmul takes a 0..1 fraction."""

    probe = (
        "const m={exports:{}};"
        "new Function('module','exports',require('fs').readFileSync("
        f"'{PAGE_JS.as_posix()}','utf8'))(m,m.exports);"
        "const f=m.exports.format;"
        "console.log(JSON.stringify(["
        "f(-6.76,'pct'), f(0.6,'pctmul'), f(0.002,'pctmul'), f(100,'pct1'),"
        "f(-0.414,'usd'), f(0.457,'usd'), f(-6.21,'cents'), f(0.39,'x'),"
        "f(15,'int'), f(300,'sec'), f(null,'pct'), f('n/a','raw')]))"
    )
    proc = subprocess.run(["node", "-e", probe], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr[:1000]
    got = json.loads(proc.stdout.strip().splitlines()[-1])
    assert got == ["-6.76%", "60.0%", "0.2%", "100.0%",
                   "-$0.41", "+$0.46", "-6.21¢", "0.39x",
                   "15", "300.0s", "", "n/a"]
