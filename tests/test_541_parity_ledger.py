"""#541 T1: page<->report parity (GROUP 1, green today) + ledger REDs (GROUP 2, red today).

GROUP 1 freezes today's page<->report agreement on the shared fixture
(tests/stat_541_fixture.py) so the Station III consolidation cannot silently
drift a number. Every expected string is derived from running code
(`kpi_report` + the report's own `_fmt`/`_pct`), never hand-written.

GROUP 2 is the ledger for T3 and MUST FAIL today:
  2a. `required_observations` is rendered at two sites (decisionGatesRows and
      renderAnalyticsSurface); T3 removes one, no test edit needed.
  2b. stat-card titles without a unit symbol; T3 adds units, no test edit needed.

Tests only — no production-code change in this file's commit.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from core_brain.config import load as load_cfg
from core_brain.kpi import (
    build_sensitivity,
    evaluate_stat_gate,
    report as kpi_report,
)
from core_brain.order_registry import OrderRegistry
from statistical_validation_run.artifacts import (
    _fmt,
    _pct,
    build_gate_rows,
    resolve_verdict,
    write_artifacts,
)

from tests.stat_541_fixture import RUN_ID_541, build_fixed_run

REPO = Path(__file__).resolve().parents[1]
APP_JS = REPO / "dashboard" / "static" / "app.js"
INDEX_HTML = REPO / "dashboard" / "static" / "index.html"


def _fixture_closes(db: Path) -> list[dict]:
    reg = OrderRegistry(db)
    return [c for c in reg.get_all_closes() if c.get("run_id") == RUN_ID_541]


class TestParity541:
    """GROUP 1 — page<->report parity. All green today."""

    def test_1a_statistics_markdown_matches_kpi_payload(self, tmp_path):
        from core_brain.statistics_report import (
            _VALID_MODES,
            write_statistics_report,
        )

        assert "shadow" in _VALID_MODES
        db = build_fixed_run(tmp_path / "parity.db")
        payload = kpi_report(db, run_id=RUN_ID_541)
        ta = payload["trade_analytics"]
        pf = payload["portfolio"]

        out = tmp_path / "stats_report"
        write_statistics_report(db, RUN_ID_541, "shadow", out_dir=out)
        # 1d: never predict the timestamped name — glob the output dir.
        mds = sorted(out.glob("*_statistics_report.md"))
        assert len(mds) == 1
        lines = mds[0].read_text(encoding="utf-8").splitlines()

        def bullet(label: str) -> str:
            return next(l for l in lines if label in l)

        assert f"{_fmt(ta['mean_return_pct'], 4)}%" in bullet("**mean_return_pct**")
        assert f"{_fmt(ta['ci90_lower_pct'], 4)}%" in bullet("**ci90_lower_pct**")
        assert f"{_fmt(ta['expectancy_usd'], 4)}" in bullet("**expectancy_usd**")
        assert _pct(ta["win_rate"]) in bullet("**win_rate**")
        assert f"${pf['realized_pnl']:.2f}" in bullet("**total_realized_pnl**")
        assert str(ta["n_closes"]) in bullet("**n_closes**")
        assert "n/a" in bullet("**pairs_under_1**")  # fixture has no pair costs
        dd = bullet("**max_drawdown**")
        assert _fmt(ta["max_drawdown_usd"], 2) in dd
        assert _fmt(ta["max_drawdown_pct"], 2) in dd

        # Gate table renders the same payload ci90 with the report's formatting.
        primary = next(l for l in lines if l.startswith("| Primary"))
        assert _fmt(ta["ci90_lower_pct"]) in primary

        # Sensitivity block matches build_sensitivity on the same closes.
        cfg = load_cfg()
        sens = build_sensitivity(
            _fixture_closes(db), threshold_pct=cfg.stat_gate_threshold_pct
        )
        assert f"{_fmt(sens['base']['ci90_lower_pct'], 4)}%" in bullet("**Base**")
        assert (
            f"{_fmt(sens['pessimistic']['ci90_lower_pct'], 4)}%"
            in bullet("**Pessimistic**")
        )

    def test_1b_report_json_matches_kpi_payload(self, tmp_path):
        # Bundle pattern from tests/test_stat_validation_artifacts.py lines 65-90.
        db = build_fixed_run(tmp_path / "bundle.db")
        cfg = load_cfg()
        payload = kpi_report(db, run_id=RUN_ID_541)
        rows = build_gate_rows(
            closes=_fixture_closes(db), kpi=payload, cfg=cfg,
            target_closes=3, matured_markouts=30, min_markouts=25,
        )
        stat = resolve_verdict(
            gate_rows=rows, kpi=payload,
            underpowered=False, underpowered_reasons=[],
            threshold_pct=cfg.stat_gate_threshold_pct,
        )
        out = tmp_path / "stat_bundle"
        write_artifacts(
            out, db_path=db, run_id=RUN_ID_541, cfg=cfg, kpi=payload,
            verdict=stat["verdict"], verdict_reason=stat["verdict_reason"],
            gate_rows=rows,
            run_result={"status": "POWERED", "reason": "sample satisfied"},
            target_closes=3, min_markouts=25,
        )
        data = json.loads((out / "report.json").read_text(encoding="utf-8"))
        for key in (
            "n_closes", "wins", "losses", "win_rate", "expectancy_usd",
            "mean_return_pct", "stdev_return_pct", "ci90_lower_pct",
            "dollar_weighted_return_pct",
        ):
            assert data["trade_analytics"][key] == payload["trade_analytics"][key]
        assert data["portfolio"]["realized_pnl"] == payload["portfolio"]["realized_pnl"]
        assert data["pairs_under_1"] == payload["pairs_under_1"]
        assert data["median_pair_cost"] == payload["median_pair_cost"]
        assert data["stat_validation"]["verdict"] == stat["verdict"]

    def test_1c_ci90_cross_check_recorded_finding(self, tmp_path):
        # evaluate_stat_gate and build_sensitivity both live in core_brain/kpi.py
        # (lines ~242/~1167); artifacts.py re-imports them from there.
        db = build_fixed_run(tmp_path / "ci90.db")
        cfg = load_cfg()
        ta = kpi_report(db, run_id=RUN_ID_541)["trade_analytics"]
        closes = _fixture_closes(db)
        gate = evaluate_stat_gate(
            closes, starting_capital=0.0,
            threshold_pct=cfg.stat_gate_threshold_pct,
        )
        sens = build_sensitivity(closes, threshold_pct=cfg.stat_gate_threshold_pct)
        # Same code path: sensitivity base IS the gate evaluation.
        assert sens["base"]["ci90_lower_pct"] == gate["ci90_lower_pct"]

        # RECORDED FINDING #541-1c (observed on the fixture, informational only
        # so a T3 unification never breaks this test): trade_analytics ci90
        # (-1.6525) differs from the gate/sensitivity-base ci90 (-1.7740) —
        # different populations (measured-returns analytics vs inclusive closes).
        finding = {
            "trade_analytics_ci90_lower_pct": ta["ci90_lower_pct"],
            "gate_ci90_lower_pct": gate["ci90_lower_pct"],
            "sensitivity_base_ci90_lower_pct": sens["base"]["ci90_lower_pct"],
        }
        assert isinstance(finding["trade_analytics_ci90_lower_pct"], float)
        assert isinstance(finding["gate_ci90_lower_pct"], float)
        assert finding["sensitivity_base_ci90_lower_pct"] == finding["gate_ci90_lower_pct"]


_UNIT_RE = re.compile(r"[$¢%×]|bps|\b(s|sec|secs|min|mins|h)\b", re.IGNORECASE)

# Titles that need no unit: unitless ratios, raw counts, or a word-unit
# already in the title. Everything else must name its unit ($, c, %, bps).
_BARE_541 = frozenset({
    "SQN", "Sortino", "PnL skewness",
    "Annualized Sharpe Ratio", "Sharpe", "Sharpe & Sortino Ratio",
    "Executed Sample Size", "Total Fills", "Resolved", "Drawdowns regained",
    "Recovery median (marks)", "Recovery p90 (marks)",
})


def _js_unescape(title: str) -> str:
    title = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), title)
    return title.replace("&amp;", "&").strip()


def _stat_card_titles() -> list[str]:
    """Hardcoded stat-card titles: tile('..') + kpi-label/quant-label literals.

    Dynamic `${esc(label)}` labels and tooltip/funnel labels are out of scope:
    the former are payload-driven, the latter are not stat cards.
    """
    src = APP_JS.read_text(encoding="utf-8")
    raw = re.findall(r"tile\('((?:[^'\\]|\\.)*)'", src)
    raw += re.findall(r'<div class="kpi-label">([^<]*)', src)
    raw += re.findall(r'<div class="quant-label">([^<]*)', src)
    titles = []
    for t in raw:
        t = _js_unescape(t)
        if not t or "$" in t or "{" in t or "}" in t:
            continue  # dynamic template label, not a hardcoded card
        if t not in titles:
            titles.append(t)
    return titles


class TestLedgerRed541:
    """GROUP 2 — ledger REDs. Both MUST FAIL today, green after T3 untouched."""

    def test_2a_required_observations_single_render_site(self):
        lines = [
            ln for ln in APP_JS.read_text(encoding="utf-8").splitlines()
            if "required_observations" in ln and "ta." in ln
        ]
        # Today: decisionGatesRows (~4136) + renderAnalyticsSurface (~5171).
        # T3 removes one site; this line-count then reads 1 with no test edit.
        assert len(lines) == 1, (
            f"required_observations rendered at {len(lines)} sites, want 1: {lines}"
        )

    def test_2b_every_stat_card_title_carries_a_unit(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        assert 'id="kpi-grid"' in html and 'id="analytics-gates"' in html
        bare = [
            t for t in _stat_card_titles()
            if t not in _BARE_541 and not _UNIT_RE.search(t)
        ]
        assert bare == [], (
            "stat-card titles without a unit "
            f"($, \u00a2, %, bps) and not on BARE: {bare}"
        )
