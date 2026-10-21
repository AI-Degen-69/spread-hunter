"""The decision-gates panel reads as a decision (#94).

Three things were wrong with it. The Edge Viability hypothesis printed the raw
LaTeX string `$H_1: \\mu > 0$` on screen. The renderer emitted a fourth verdict
class, `stopped` (STANDBY), that had no CSS rule behind it, so that state came
out unstyled and could not be told from neutral text. And two gates reported GO
without ever checking their own threshold — the markout gate on a single sample,
and the drawdown guard on a run that had blown straight through the envelope.

Driven through node against the real `dashboard/static/app.js`, so the verdicts
asserted here are the ones the panel would render.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parent / "js" / "decision_gates_harness.cjs"
_STATIC = Path(__file__).resolve().parent.parent / "dashboard" / "static"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not installed on this host")


def _render(trade_analytics: dict, n: int, statistical: dict | None = None,
            kpi: dict | None = None) -> dict:
    payload = {"trade_analytics": trade_analytics,
               "statistical_analytics": statistical or {},
               "n": n,
               "kpi": kpi or {}}
    out = subprocess.run([shutil.which("node"), str(HARNESS), json.dumps(payload)],
                         capture_output=True, text=True, check=True, encoding="utf-8")
    return json.loads(out.stdout)


def _verdict(rendered: dict, name_fragment: str) -> dict:
    for row in rendered["verdicts"]:
        if name_fragment.lower() in row["name"].lower():
            return row
    raise AssertionError(f"no gate matching {name_fragment!r}")


def _healthy() -> dict:
    return {
        "n_closes": 40,
        "ci90_lower_pct": 0.80,
        "win_rate": 0.62,
        "required_observations": 120,
        "markout_samples": 30,
        "adverse_selection": 0.0022,
        "max_drawdown_pct": 1.2,
    }


def test_the_hypothesis_is_typeset_not_printed_as_latex():
    # Arrange / Act
    rendered = _render(_healthy(), 40)

    # Assert — the LaTeX travels in `data-math` for KaTeX, and the visible text
    # is the Unicode fallback for a dashboard that never loaded the library.
    assert 'data-math="H_1: \\mu &gt; 0"' in rendered["html"]
    assert "H₁: μ" in rendered["html"]
    assert "$H_1:" not in rendered["html"]
    assert "\\\\mu" not in rendered["html"]


def test_every_verdict_state_has_a_styled_class():
    # Arrange
    css = (_STATIC / "styles.css").read_text(encoding="utf-8")

    # Act / Assert — STANDBY had no rule at all before this.
    for state in ("go", "nogo", "accumulating", "standby"):
        assert f".analytics-gate-badge.{state}" in css


def test_a_run_with_no_closes_reads_standby_not_go():
    # Arrange / Act
    rendered = _render({}, 0)

    # Assert
    assert _verdict(rendered, "Edge Viability")["state"] == "standby"
    assert _verdict(rendered, "Directional Neutrality")["state"] == "standby"
    assert _verdict(rendered, "Max Drawdown")["state"] == "standby"
    assert "analytics-gate-badge standby" in rendered["html"]


def test_unmeasured_values_are_labelled_rather_than_printed_as_zero():
    # Arrange / Act
    rendered = _render({}, 0)

    # Assert
    assert "unmeasured" in rendered["html"]


def test_the_markout_gate_needs_its_matured_fills():
    # Arrange — one sample is not 25.
    thin = {**_healthy(), "markout_samples": 1}

    # Act
    rendered = _render(thin, 40)

    # Assert
    assert _verdict(rendered, "Adverse Selection")["state"] == "accumulating"


def test_negative_drift_is_a_no_go_not_a_shrug():
    # Arrange — adverse selection is running against us.
    adverse = {**_healthy(), "adverse_selection": -0.004}

    # Act
    rendered = _render(adverse, 40)

    # Assert
    assert _verdict(rendered, "Adverse Selection")["state"] == "nogo"
    assert "analytics-gate-badge nogo" in rendered["html"]


def test_a_breached_drawdown_envelope_is_a_no_go():
    # Arrange — 12% against a 5% envelope. This used to render GO.
    breached = {**_healthy(), "max_drawdown_pct": 12.0}

    # Act
    rendered = _render(breached, 40)

    # Assert
    assert _verdict(rendered, "Max Drawdown")["state"] == "nogo"


def test_a_healthy_run_confirms_the_edge_gate():
    # Arrange / Act
    rendered = _render(_healthy(), 40)

    # Assert
    assert _verdict(rendered, "Edge Viability")["state"] == "confirmed"
    assert _verdict(rendered, "Statistical Power")["state"] == "accumulating"
    assert "GO (CONFIRMED)" in rendered["html"]


def test_the_sweet_spot_figure_comes_from_the_payload():
    # Arrange — the panel used to print a hardcoded 82.5%.
    stats = {"probability_bell": {"sweet_spot_pct": 61.4}}

    # Act
    rendered = _render(_healthy(), 40, stats)

    # Assert
    assert "61.4% in band" in rendered["html"]
    assert "82.5%" not in rendered["html"]


def test_katex_is_loaded_and_the_gate_hypotheses_are_math():
    # Arrange / Act — #749 retired the distribution heading this once pinned;
    # the decision gates still typeset their hypotheses, and they are built in
    # app.js rather than carried in the static page.
    html = (_STATIC / "index.html").read_text(encoding="utf-8")
    app_js = (_STATIC / "app.js").read_text(encoding="utf-8")

    # Assert
    assert "katex.min.js" in html
    assert "katex.min.css" in html
    assert 'data-math="${esc(tex)}"' in app_js
    assert "mathSpan(" in app_js
    assert "typesetMath(gates)" in app_js


def test_the_markout_row_reads_the_top_level_payload_fields():
    # Arrange — `markout_samples` and `adverse_selection` live at the top level
    # of the KPI payload, not inside trade_analytics. Reading only `ta` made
    # this row report "0 samples" on runs that had measured plenty.
    # trade_analytics deliberately carries the WRONG values, so the test fails
    # if the renderer goes back to reading them from there.
    ta = {**_healthy(), "markout_samples": 0, "adverse_selection": None}
    rendered = _render(ta, 40, kpi={"markout_samples": 30,
                                    "adverse_selection": 0.0022,
                                    "adverse_selection_excess": 0.0009})

    # Act / Assert — samples, raw drift, and the baseline-corrected figure.
    assert "30 samples" in rendered["html"]
    assert "0.22¢/share" in rendered["html"]
    assert "excess 0.09¢" in rendered["html"]
