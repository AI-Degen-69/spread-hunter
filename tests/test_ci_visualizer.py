"""Interactive CI visualizer (docs/specs/ci-visualizer.md).

V1 upgrades the Tier 1 Average Close card in place with a KPI picker over core
means (Mean PnL $, Expectancy $, Mean Return %, Win Rate) driven by one shared
JS adapter, and lifts the ch1-ch5 chart grid with axes, native-SVG hover
values and consistent footers. One additive backend field (`win_rate_ci90`,
the 90% Wilson row from the backend's critical-value table per #535); every
other field the adapter reads already ships in the /api/kpi payload.

The adapter runs against the stub DOM in
tests/js/analytics_surface_harness.cjs; the charts run through
tests/js/analysis_v2_harness.cjs. Assertions are on rendered copy and adapter
output rather than on calls having been made.
"""
from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

SURFACE_HARNESS = Path(__file__).resolve().parent / "js" / "analytics_surface_harness.cjs"
AV2_HARNESS = Path(__file__).resolve().parent / "js" / "analysis_v2_harness.cjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not installed on this host")

_TAG_RE = None


def _visible(html: str) -> str:
    import re
    global _TAG_RE
    if _TAG_RE is None:
        _TAG_RE = re.compile(r"<[^>]+>")
    return _TAG_RE.sub(" ", html or "")


def _surface(kpi: dict) -> dict:
    out = subprocess.run([shutil.which("node"), str(SURFACE_HARNESS), json.dumps({"kpi": kpi})],
                         capture_output=True, text=True, check=True, encoding="utf-8")
    return json.loads(out.stdout)


def _av2(kpi: dict, tmp_path: Path) -> dict:
    target = tmp_path / "av2_ci_visualizer.json"
    target.write_text(json.dumps({"kpi": kpi}), encoding="utf-8")
    out = subprocess.run([shutil.which("node"), str(AV2_HARNESS), str(target), "render"],
                         capture_output=True, text=True, check=True, encoding="utf-8")
    return json.loads(out.stdout)


def _band(level: int, lower: float, upper: float) -> dict:
    return {
        "level": level, "lower": lower, "upper": upper,
        "includes_negative": lower < 0,
        "negative_depth_usd": abs(lower) if lower < 0 else None,
    }


def _wilson(successes: int, n: int, z: float):
    # Mirrors core_brain/kpi.py::_wilson_ci so the shipped 90% band is pinned
    # to the backend formula rather than to a copy of itself.
    p = successes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denom
    half = (z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def _full_ta() -> dict:
    lo90, hi90 = _wilson(34, 52, 1.645)
    return {
        "n_closes": 52,
        "wins": 34,
        "losses": 18,
        "win_rate": 34 / 52,
        "win_rate_ci95": {"lower": 0.51, "upper": 0.78},
        "win_rate_ci90": {"lower": lo90, "upper": hi90},
        "expectancy_usd": 0.33,
        "mean_return_pct": 2.19,
        "n_measured_returns": 47,
        "ci90_lower_pct": 0.42,
        "ci95_return_pct": {"lower": 0.11, "upper": 4.27},
        "mean_pnl_ci": {
            "mean_usd": 0.33,
            "n": 52,
            "levels": [_band(90, 0.22, 0.45), _band(95, 0.20, 0.47)],
            "verdict": "positive",
        },
    }


# ── CI adapter: one shared view per KPI ──────────────────────────────────


def test_mean_pnl_view_reads_the_dollar_band():
    views = _surface({"trade_analytics": _full_ta()})["ci_views"]
    v = views["mean_pnl"]
    assert v["mean"] == pytest.approx(0.33)
    assert v["n"] == 52
    assert v["units"] == "usd"
    assert v["l95"] == {"lower": pytest.approx(0.20), "upper": pytest.approx(0.47)}
    assert v["verdict"] == "positive"


def test_expectancy_view_shares_the_spread_with_its_own_mean():
    views = _surface({"trade_analytics": _full_ta()})["ci_views"]
    v = views["expectancy"]
    assert v["mean"] == pytest.approx(0.33)
    assert v["units"] == "usd"
    assert v["l90"] == {"lower": pytest.approx(0.22), "upper": pytest.approx(0.45)}


def test_mean_return_view_reads_the_percent_band():
    views = _surface({"trade_analytics": _full_ta()})["ci_views"]
    v = views["mean_return"]
    assert v["units"] == "pct"
    assert v["l95"] == {"lower": pytest.approx(0.11), "upper": pytest.approx(4.27)}
    # The payload carries only the 90% lower bound; the upper mirrors it
    # around the mean (symmetric normal interval).
    assert v["l90"]["lower"] == pytest.approx(0.42)
    assert v["l90"]["upper"] == pytest.approx(2.19 + (2.19 - 0.42))


def test_win_rate_90_band_matches_the_backend_wilson_formula():
    views = _surface({"trade_analytics": _full_ta()})["ci_views"]
    v = views["win_rate"]
    assert v["units"] == "winrate"
    assert v["l95"] == {"lower": pytest.approx(51.0), "upper": pytest.approx(78.0)}
    lo, hi = _wilson(34, 52, 1.645)
    assert v["l90"]["lower"] == pytest.approx(lo * 100, abs=1e-6)
    assert v["l90"]["upper"] == pytest.approx(hi * 100, abs=1e-6)
    assert 0.0 <= v["l90"]["lower"] <= v["l90"]["upper"] <= 100.0


def test_backend_ships_the_win_rate_90_band_from_one_table():
    from core_brain.kpi import compute_trade_analytics
    from core_brain.statistical_analytics import ci_z
    closes = [{"realized_pnl": 0.3, "cost_basis": 1.0, "ts": float(i)}
              for i in range(34)]
    closes += [{"realized_pnl": -0.2, "cost_basis": 1.0, "ts": float(100 + i)}
               for i in range(18)]
    ta = compute_trade_analytics(closes, 100.0, [], [])
    assert ta["win_rate_ci90"] is not None
    lo, hi = _wilson(34, 52, ci_z(90))
    assert ta["win_rate_ci90"]["lower"] == pytest.approx(lo)
    assert ta["win_rate_ci90"]["upper"] == pytest.approx(hi)


def test_unknown_kpi_key_falls_back_to_mean_pnl():
    views = _surface({"trade_analytics": _full_ta()})["ci_views"]
    assert views["bogus"]["metric"] == "mean_pnl"
    assert views["bogus"]["mean"] == pytest.approx(0.33)


def test_no_band_renders_standby_not_zero_per_kpi():
    ta = _full_ta()
    ta["mean_pnl_ci"] = {"mean_usd": 0.08, "n": 1, "levels": [], "verdict": None}
    ta["ci95_return_pct"] = None
    out = _surface({"trade_analytics": ta})
    assert out["ci_views"]["mean_pnl"]["noBand"] is True
    assert "NO BAND YET" in _visible(out["ci_html"]["mean_pnl"])


def test_unmeasured_fields_show_no_mean_for_every_kpi():
    # Number(null) is 0, not NaN: every KPI must render `--`, never a zero.
    ta = {"n_closes": 0, "win_rate": None, "win_rate_ci95": None,
          "win_rate_ci90": None, "expectancy_usd": None,
          "mean_return_pct": None, "ci95_return_pct": None,
          "ci90_lower_pct": None, "n_measured_returns": None,
          "mean_pnl_ci": {"mean_usd": None, "n": 0, "levels": [], "verdict": None}}
    views = _surface({"trade_analytics": ta})["ci_views"]
    for key in ("mean_pnl", "expectancy", "mean_return", "win_rate"):
        assert views[key]["noBand"] is True
        assert views[key]["mean"] is None, key


def test_win_rate_readout_judges_against_fifty_percent():
    out = _surface({"trade_analytics": _full_ta()})
    text = _visible(out["ci_html"]["win_rate"])
    assert "50%" in text


# ── Chart grid: hover values, axes, unmeasured notes ─────────────────────


def test_return_distribution_bars_carry_hover_values_and_axis_ends(tmp_path):
    kpi = {
        "trade_analytics": _full_ta(),
        "statistical_analytics": {
            "position_returns": {
                "positions": [{"pnl_pct": float(v)} for v in
                              (-8.0, -3.0, -1.0, 0.5, 1.0, 2.0, 2.5, 3.0, 5.0, 9.0)],
                "mean_pnl_pct": 2.19,
                "stdev_pnl_pct": 10.73,
                "sem_pnl_pct": 1.49,
            },
        },
    }
    charts = {c["id"]: c for c in _av2(kpi, tmp_path)["charts"]}
    svg = charts["ch1"]["svg"]
    # Every bar carries its own closing title (a lone curve/zero-line title
    # must not satisfy this), axis ends are labeled, and bin ranges read
    # as short "a to b" text rather than raw 16-decimal floats.
    assert svg.count("<rect ") >= 1
    assert svg.count("</title></rect>") == svg.count("<rect ")
    assert ">-8.00%</text>" in svg
    assert ">9.00%</text>" in svg
    assert " to " in svg
    assert charts["ch1"]["stats"] and "Unmeasured" not in charts["ch1"]["stats"]


def test_pair_cost_and_monte_carlo_shapes_carry_hover_values(tmp_path):
    kpi = {
        "trade_analytics": _full_ta(),
        "statistical_analytics": {
            "pair_costs": {
                "bins": [{"density": d, "min": 0.9 + i * 0.01, "max": 0.91 + i * 0.01}
                         for i, d in enumerate([1, 4, 9, 4, 1])],
                "mean": 0.94, "median": 0.94, "stdev": 0.01,
                "min_observed": 0.92, "samples_count": 37,
            },
            "monte_carlo": {
                "paths": 1000,
                "prob_positive_return": 100.0,
                "worst_case_drawdown_pct": -2.05,
                "samples_count": 52,
                "steps": [{"p01": -1.0 + i * 0.2, "p50": i * 0.5, "p99": 1.0 + i * 0.8}
                          for i in range(10)],
            },
        },
    }
    charts = {c["id"]: c for c in _av2(kpi, tmp_path)["charts"]}
    assert "<title>" in charts["ch2"]["svg"]
    assert "<title>" in charts["ch3"]["svg"]


def test_empty_charts_name_what_is_unmeasured(tmp_path):
    out = _av2({"trade_analytics": {"n_closes": 0}}, tmp_path)
    charts = {c["id"]: c for c in out["charts"]}
    assert "Unmeasured" in _visible(charts["ch1"]["stats"] + charts["ch1"]["foot"])


def test_repaint_to_empty_clears_stale_series(tmp_path):
    # A run switch from populated to empty must not leave the old series
    # drawn behind a fresh Unmeasured badge: the harness prefills every
    # chart area with a stale svg, then renders the empty payload.
    target = tmp_path / "av2_ci_stale.json"
    target.write_text(json.dumps({"kpi": {}}), encoding="utf-8")
    out = subprocess.run([shutil.which("node"), str(AV2_HARNESS), str(target), "stale"],
                         capture_output=True, text=True, check=True, encoding="utf-8")
    charts = {c["id"]: c for c in json.loads(out.stdout)["charts"]}
    assert set(charts) == {"ch1", "ch2", "ch3", "ch4", "ch5"}
    for cid, c in charts.items():
        assert c["svg"] == "", f"{cid} kept its stale series"
        assert c["badge"] == "Unmeasured", f"{cid} hides its empty state"
        assert c["badgeCls"] == "chart-badge", f"{cid} kept its stale badge color"
