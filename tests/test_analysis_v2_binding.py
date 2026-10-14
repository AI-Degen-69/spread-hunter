"""The redesigned Data Analysis surface paints live telemetry, not mock numbers.

Drives dashboard/static/analysis_v2.js through tests/js/analysis_v2_harness.cjs
against the real `core_brain.kpi.report()` payload shape, so every data-bind
hook in the markup is checked against a field that actually exists.

The page is read-only by construction: its only telemetry read is
GET /api/kpi (plus ?run_id=), served on its own /analysis-v2 route. The
live-operations dashboard and the #541 Data Analysis tab are untouched.
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
PAGE = STATIC / "analysis_v2.html"
PAGE_JS = STATIC / "analysis_v2.js"
DEMO_DB = Path("data/demo_analytics_unique.db")

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
def live_payload() -> dict:
    """The real /api/kpi shape from the demo registry.

    ``data/*.db`` is gitignored, so a fresh checkout (CI) has no file:
    build the registry on demand rather than assert against an empty
    store. The builder is deterministic apart from wall-clock stamps,
    so every numeric assertion below holds for a fresh build.
    """
    if not DEMO_DB.is_file():
        from scripts.build_demo_db import build

        DEMO_DB.parent.mkdir(parents=True, exist_ok=True)
        build(DEMO_DB)
    return kpi_report(db_path=str(DEMO_DB))


def test_page_and_assets_are_served_from_static():
    """The route and the static mount must both find the files."""
    assert PAGE.is_file(), "analysis_v2.html missing from dashboard/static/"
    assert (STATIC / "analysis_v2.css").is_file()
    assert PAGE_JS.is_file()
    # The stylesheet must be absolute: a relative "styles.css" would resolve to
    # the live dashboard's own 97K stylesheet when served from /static/.
    assert 'href="/static/analysis_v2.css"' in PAGE.read_text(encoding="utf-8")


def test_server_exposes_the_analysis_v2_route():
    """/analysis-v2 exists and the legacy routes are untouched."""
    from dashboard import server

    paths = {getattr(r, "path", None) for r in server.app.routes}
    assert "/analysis-v2" in paths
    # Guardrails: nothing the operator relies on moved or vanished.
    for legacy in ("/", "/prototype", "/api/kpi", "/api/state", "/api/system/status"):
        assert legacy in paths, f"regression: {legacy} disappeared"


@requires_node
def test_every_bind_hook_paints_a_real_value(tmp_path, live_payload):
    """No data-bind is left blank and no mock number survives."""
    out = _run(live_payload, "render", tmp_path)
    assert not out["errors"], out["errors"]
    assert out["hookCount"] >= 50, f"only {out['hookCount']} bind hooks found"

    blank = [k for k, v in out["painted"].items() if v in (None, "")]
    assert not blank, f"hooks painted nothing: {blank}"

    painted = out["painted"]
    # Spot-check the numbers an operator would actually read, against the
    # payload's own values -- these are the mock numbers the design shipped.
    assert painted["trade_analytics.mean_return_pct"] == "-6.76%"
    assert painted["trade_analytics.expectancy_usd"] == "-$0.41"
    assert painted["trade_analytics.win_rate"] == "60.0%"          # 0.6 fraction
    assert painted["trade_analytics.n_closes"] == "15"
    assert painted["_required"] == "120"
    assert painted["_winloss"] == "9W / 6L"
    # Fraction vs percentage: rescue_rate 0.5 must read 50%, not 0.5%.
    assert painted["statistical_analytics.fill_path_edge.rescue_rate"] == "50.0%"
    assert painted["statistical_analytics.rescue_friction.mean_tcdr"] == "54.3%"
    assert painted["trade_analytics.kelly_fraction"] == "-94.3%"


def test_no_mock_value_survives_on_the_page():
    """The design's hardcoded numbers must not still be in the markup."""
    html = PAGE.read_text(encoding="utf-8")
    # Only the pure telemetry mocks: "9W / 6L" is a legitimate tooltip
    # value and the .db names are DB-switcher chrome, where run-switching
    # was deliberately not wired.
    for mock in ("-6.76%", "-$0.41", "60.0%", "-$1.30", "+$0.32"):
        assert mock not in html, f"mock value still hardcoded in markup: {mock}"


@requires_node
def test_units_and_signs_match_the_payload(tmp_path, live_payload):
    """$ and ¢ are signed; % and x are signed only when negative."""
    out = _run(live_payload, "render", tmp_path)
    p = out["painted"]
    assert p["trade_analytics.expectancy_usd"].startswith("-")       # negative usd
    assert p["statistical_analytics.fill_path_edge.merge_expectancy_usd"].startswith("+")
    assert p["statistical_analytics.rescue_friction.mean_sled_cents"] == "-6.21¢"
    assert p["trade_analytics.mean_return_pct"].startswith("-")      # negative pct
    assert p["trade_analytics.profit_factor"] == "0.39x"             # positive x, no '+'
    assert p["trade_analytics.max_drawdown_pct"] == "6.33%"


@requires_node
def test_unmeasured_fields_blank_rather_than_invent(tmp_path, live_payload):
    """var_95/cvar_95 are null in the payload -- the page must say unmeasured."""
    out = _run(live_payload, "render", tmp_path)
    assert out["painted"]["_var"] == "unmeasured"
    assert out["painted"]["_cvar"] == "unmeasured"


@requires_node
def test_ci_verdict_reads_a_loss_as_negative(tmp_path, live_payload):
    """negative_depth_usd is a loss depth; it must not render as +$."""
    out = _run(live_payload, "render", tmp_path)
    verdict = out["painted"]["_ciVerdict"]
    assert "loss down to -$1.30" in verdict, verdict
    # The scale's geometry is the payload's own: the track spans the 95%
    # band (lo95 -> 0%, hi95 -> 100%), so lo90/mean/be/hi90 map onto it.
    # These positions were the design's hardcoded mock values; they must
    # now be computed, or they lie the moment the payload changes.
    s = out["ciScale"]
    assert s["display"] != "none"
    assert s["ticks"]["lo95"] == ["0.0%"]
    assert s["ticks"]["hi95"] == ["100.0%"]
    assert abs(float(s["ticks"]["mean"][0][:-1]) - 50.0) < 0.5   # -$0.41 on [-1.30, +0.48]
    assert abs(float(s["ticks"]["be"][0][:-1]) - 73.0) < 0.5     # breakeven on the same span
    assert s["labels"]["lo95"][0]["text"] == "95% Low -$1.30"
    assert s["labels"]["mean"][0]["text"] == "Mean -$0.41"
    assert s["labels"]["hi90"][0]["text"] == "90% High +$0.32"


@requires_node
def test_sufficiency_table_fills_from_the_payload(tmp_path, live_payload):
    """The 95% column the table defaults to comes from the real statuses."""
    out = _run(live_payload, "render", tmp_path)
    rows = {r["key"]: r for r in out["sufficiency"]}
    assert set(rows) == {"pnl_expectancy", "stop_loss_rate", "merge_rate", "fill_rate"}
    assert rows["pnl_expectancy"]["req"] == "97"
    assert rows["pnl_expectancy"]["cur"] == "15 closes"
    # fill_rate is counted in ORDERS, not closes -- the unit must follow.
    assert rows["fill_rate"]["cur"] == "27 orders"
    assert rows["stop_loss_rate"]["dataset"]["r98"] == "542"


@requires_node
def test_sufficiency_toggle_repaints_visible_cells(tmp_path, live_payload):
    """Clicking 98/99 repaints the visible .req/.rem/.pct cells, not just data.

    The toggle is the design's own IIFE; the harness executes it against
    the stub and fires the pills the way a click does.
    """
    out = _run(live_payload, "toggle", tmp_path)
    assert not out["errors"], out["errors"]
    t = out["toggle"]
    rows98 = {r["key"]: r for r in t["r98"]}
    assert rows98["stop_loss_rate"]["req"] == "542"
    assert rows98["stop_loss_rate"]["rem"] == "527"
    assert rows98["pnl_expectancy"]["req"] == "136"
    rows99 = {r["key"]: r for r in t["r99"]}
    assert rows99["stop_loss_rate"]["req"] == "664"
    assert rows99["stop_loss_rate"]["rem"] == "649"
    assert rows99["pnl_expectancy"]["req"] == "166"
    rows95 = {r["key"]: r for r in t["r95"]}
    assert rows95["pnl_expectancy"]["req"] == "97"
    assert rows95["pnl_expectancy"]["rem"] == "82"
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
    assert bands["fr.tcdr"]["cls"].endswith("badge--bad")
    assert bands["rb.sqn"]["text"] == "Bad"
    # Every badge carries a label -- an empty badge is a bug.
    for ref, b in bands.items():
        assert b["text"], f"badge {ref} rendered with no label"
        assert "badge--" in b["cls"], f"badge {ref} has no badge-- class"


@requires_node
def test_gate_verdicts_follow_the_payload(tmp_path, live_payload):
    """15 closes against a 120 gate is Accumulating, never Go."""
    out = _run(live_payload, "render", tmp_path)
    g = out["gates"]
    assert g["power"]["observed"] == "15 / 120 (13%)"
    assert g["power"]["badge"] == "badge--accum"
    # ci90 lower bound is negative -> not yet a proven edge.
    assert g["edge_viability"]["observed"] == "-15.00%"
    assert g["edge_viability"]["badge"] == "badge--accum"
    # 60% win rate on 15 closes is Go on the neutrality gate.
    assert g["neutrality"]["badge"] == "badge--go"
    # 6.33% drawdown breaches the 5% envelope.
    assert g["drawdown_guard"]["badge"] == "badge--nogo"
    # The badges themselves paint from the payload: data-band sits on the
    # badge span, so the verdict updates while the observed cell keeps its
    # own bound value (a band on the value cell would clobber the number).
    b = out["bands"]
    assert b["gates.band"]["text"] == "Active Rule"
    assert b["gates.edge_viability"]["text"] == "Accumulating"
    assert b["gates.power"]["text"] == "Accumulating"
    assert b["gates.markout"]["text"] == "Accumulating"
    assert b["gates.neutrality"]["text"] == "Go"
    assert b["gates.drawdown_guard"]["text"] == "No-Go"
    for ref in ("gates.band", "gates.edge_viability", "gates.power",
                "gates.markout", "gates.neutrality", "gates.drawdown_guard"):
        assert b[ref]["cls"].endswith("badge--go") or b[ref]["cls"].endswith("badge--nogo") \
            or b[ref]["cls"].endswith("badge--accum") or b[ref]["cls"].endswith("badge--active"), ref
    assert out["painted"]["trade_analytics.ci90_lower_pct"] == "-15.00%"


@requires_node
def test_all_five_charts_paint_real_series(tmp_path, live_payload):
    """Each chart card gets an svg, a badge and a stats line from live data."""
    out = _run(live_payload, "render", tmp_path)
    charts = {c["id"]: c for c in out["charts"]}
    assert set(charts) == {"ch1", "ch2", "ch3", "ch4", "ch5"}
    for cid, c in charts.items():
        assert c["svg"].startswith("<svg"), f"{cid} painted no svg"
        assert "<path" in c["svg"] or "<rect" in c["svg"], f"{cid} drew no series"
        assert c["badge"], f"{cid} has no badge"
        assert c["stats"], f"{cid} has no stats line"

    # Chart 1's CI badge is the payload's own 95% band, not the mock's 98%.
    assert "95% CI: [-16.79%, +3.26%]" in charts["ch1"]["badge"]
    # Chart 3: prob_positive_return is already a PERCENT (0.2 == 0.2%).
    assert "P(Profit &gt; 0): 0.2%" in charts["ch3"]["badge"]
    # Chart 2 median is a pair cost in dollars; usd fmt is always signed.
    assert "Median: +$0.95" in charts["ch2"]["badge"]


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
    assert "unmeasured" in p["_ciVerdict"]         # never claims an edge on 0 closes
    # No samples: the markout honestly says "unmeasured", never a fabricated number.
    assert "unmeasured" in p["_gateMarkout"]
    # Nothing numeric from the live sample may appear.
    for key in ("trade_analytics.mean_return_pct", "trade_analytics.n_closes",
                "trade_analytics.expectancy_usd", "trade_analytics.win_rate"):
        assert p[key] == "", f"{key} painted {p[key]!r} on an empty payload"
    # No chart may draw a series with no data behind it.
    for c in out["charts"]:
        assert c["svg"] == "" and c["badge"] == "", f"{c['id']} drew on empty data"
    # Same rule for the CI scale: static geometry would keep drawing the
    # last payload's ticks on an empty registry.
    assert out["ciScale"]["display"] == "none"


@requires_node
def test_formatters_handle_the_fraction_and_percent_families():
    """pct takes a percentage; pctmul takes a 0..1 fraction."""
    import subprocess as sp

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
    proc = sp.run(["node", "-e", probe], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr[:1000]
    got = json.loads(proc.stdout.strip().splitlines()[-1])
    assert got == ["-6.76%", "60.0%", "0.2%", "100.0%",
                   "-$0.41", "+$0.46", "-6.21¢", "0.39x",
                   "15", "300.0s", "", "n/a"]
