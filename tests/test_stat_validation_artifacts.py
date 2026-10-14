"""Tests for the statistical validation artifact bundle (Issue #54).

The bundle proves the gate INCLUSIVE of all losses: `reports/stat_*/report.json`
carries the full KPI (with `trade_analytics.ci90_lower_pct` and
`n_closes == count(distinct closes)` across every method), and `report.md`
renders the gate table and a GO / NO-GO / INCONCLUSIVE verdict whose primary
gate is `ci90_lower_pct > 1.0` inclusive — not a conditional merge-only win
rate, which is tautological.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core_brain.config import load as load_cfg
from core_brain.kpi import report as kpi_report
from core_brain.order_registry import (
    CloseRecord,
    FillRecord,
    OrderRecord,
    OrderRegistry,
    QuoteRecord,
)
from statistical_validation_run.artifacts import (
    build_gate_rows,
    render_report_md,
    rescue_stats,
    resolve_verdict,
    rows_to_csv,
    write_artifacts,
)

RUN_ID = "shadow-artifacts-test"


def _seed_closes(db: Path, closes: list[dict]):
    """Seed `closes` rows of mixed methods under one shadow run_id."""
    reg = OrderRegistry(db_path=db)
    for i, c in enumerate(closes):
        reg.log_close(CloseRecord(
            ts=float(i),
            condition_id="0xabc",
            market_slug="fake-market",
            method=c["method"],
            shares=float(c.get("shares", 1)),
            cost_basis=float(c.get("cost_basis", 5.0)),
            proceeds=float(c.get("proceeds", 5.0 + c["realized_pnl"])),
            realized_pnl=float(c["realized_pnl"]),
            run_id=RUN_ID,
        ))
    return db


def _kpi_for(db: Path) -> dict:
    return kpi_report(db, run_id=RUN_ID)


def _run_closes(db: Path) -> list[dict]:
    reg = OrderRegistry(db_path=db)
    return [c for c in reg.get_all_closes() if c.get("run_id") == RUN_ID]


def _write_bundle(tmp_path: Path, closes: list[dict], out_name: str = "stat_bundle",
                  *, target_closes: int | None = 3, min_markouts: int | None = 25,
                  matured_markouts: int | None = 30) -> tuple[Path, dict]:
    """Seed closes, run the full kpi path, write the bundle, return (dir, kpi)."""
    db = _seed_closes(tmp_path / f"{out_name}.db", closes)
    cfg = load_cfg()
    kpi = _kpi_for(db)
    rows = build_gate_rows(
        closes=_run_closes(db), kpi=kpi, cfg=cfg,
        target_closes=target_closes, matured_markouts=matured_markouts,
        min_markouts=min_markouts,
    )
    stat = resolve_verdict(
        gate_rows=rows, kpi=kpi,
        underpowered=False, underpowered_reasons=[],
        threshold_pct=cfg.stat_gate_threshold_pct,
    )
    out = tmp_path / out_name
    write_artifacts(
        out, db_path=db, run_id=RUN_ID, cfg=cfg, kpi=kpi,
        verdict=stat["verdict"], verdict_reason=stat["verdict_reason"],
        gate_rows=rows,
        run_result={"status": "POWERED", "reason": "sample satisfied"},
        target_closes=target_closes, min_markouts=min_markouts,
    )
    return out, kpi


class TestRescueStats:
    """Completion/exit rates fold every exit method into one denominator."""

    def test_completion_and_exit_rates(self):
        closes = [
            {"method": "shadow_merge", "realized_pnl": 0.04},
            {"method": "shadow_merge", "realized_pnl": 0.03},
            {"method": "single_buy_exit", "realized_pnl": -0.60},
            {"method": "naked_exit", "realized_pnl": -0.55},
        ]
        stats = rescue_stats(closes)
        assert stats["merges"] == 2
        assert stats["exits"] == 2
        assert stats["completion_rate"] == pytest.approx(0.5)
        assert stats["exit_rate"] == pytest.approx(0.5)

    def test_no_rescues_is_null_not_zero(self):
        stats = rescue_stats([{"method": "venue_sync", "realized_pnl": 0.0}])
        assert stats["merges"] == 0
        assert stats["exits"] == 0
        assert stats["completion_rate"] is None
        assert stats["exit_rate"] is None


class TestGateRowsAndVerdict:
    """The gate is ci90_lower_pct > 1.0 INCLUSIVE of every close method."""

    def test_strong_inclusive_sample_is_go(self, tmp_path):
        db = _seed_closes(tmp_path / "go.db", [
            {"method": "shadow_merge", "realized_pnl": 0.30},
            {"method": "single_buy_exit", "realized_pnl": 0.30},
            {"method": "naked_exit", "realized_pnl": 0.30},
        ])
        kpi = _kpi_for(db)
        cfg = load_cfg()
        rows = build_gate_rows(
            closes=_run_closes(db), kpi=kpi, cfg=cfg,
            target_closes=3, matured_markouts=30, min_markouts=25,
        )
        verdict = resolve_verdict(
            gate_rows=rows, kpi=kpi,
            underpowered=False, underpowered_reasons=[],
            threshold_pct=cfg.stat_gate_threshold_pct,
        )
        assert verdict["verdict"] == "GO"
        primary = next(r for r in rows if "Primary" in r["gate"])
        assert primary["passed"] is True

    def test_losing_inclusive_sample_is_no_go(self, tmp_path):
        db = _seed_closes(tmp_path / "nogo.db", [
            {"method": "shadow_merge", "realized_pnl": -0.30},
            {"method": "single_buy_exit", "realized_pnl": -0.30},
            {"method": "naked_exit", "realized_pnl": -0.30},
        ])
        kpi = _kpi_for(db)
        cfg = load_cfg()
        rows = build_gate_rows(
            closes=_run_closes(db), kpi=kpi, cfg=cfg,
            target_closes=3, matured_markouts=30, min_markouts=25,
        )
        verdict = resolve_verdict(
            gate_rows=rows, kpi=kpi,
            underpowered=False, underpowered_reasons=[],
            threshold_pct=cfg.stat_gate_threshold_pct,
        )
        assert verdict["verdict"] == "NO-GO"
        assert "primary gate failed" in verdict["verdict_reason"]

    def test_underpowered_is_inconclusive_even_when_profitable(self, tmp_path):
        db = _seed_closes(tmp_path / "under.db", [
            {"method": "shadow_merge", "realized_pnl": 0.30},
        ])
        kpi = _kpi_for(db)
        cfg = load_cfg()
        rows = build_gate_rows(
            closes=_run_closes(db), kpi=kpi, cfg=cfg,
            target_closes=50, matured_markouts=10, min_markouts=25,
        )
        verdict = resolve_verdict(
            gate_rows=rows, kpi=kpi,
            underpowered=True,
            underpowered_reasons=["closes 1 < 50", "markouts 10 < 25"],
            threshold_pct=cfg.stat_gate_threshold_pct,
        )
        assert verdict["verdict"] == "INCONCLUSIVE"
        assert "underpowered" in verdict["verdict_reason"]

    def test_primary_gate_is_strict_at_the_threshold_boundary(self):
        """Equality with the threshold is a FAIL, not a pass (90% CI (1%, inf))."""
        cfg = load_cfg()

        def rows_for(ci90: float) -> tuple[list[dict], dict]:
            kpi = {
                "trade_analytics": {"n_closes": 10, "ci90_lower_pct": ci90},
                "portfolio": {"starting_capital": 100.0},
            }
            return build_gate_rows(
                closes=[], kpi=kpi, cfg=cfg,
                target_closes=None, matured_markouts=None, min_markouts=None,
            ), kpi

        rows, kpi = rows_for(cfg.stat_gate_threshold_pct)  # exactly at the boundary
        primary = next(r for r in rows if "Primary" in r["gate"])
        assert primary["passed"] is False
        assert resolve_verdict(
            gate_rows=rows, kpi=kpi,
            underpowered=False, underpowered_reasons=[],
            threshold_pct=cfg.stat_gate_threshold_pct,
        )["verdict"] == "NO-GO"

        rows, kpi = rows_for(cfg.stat_gate_threshold_pct + 0.0001)  # just above
        assert resolve_verdict(
            gate_rows=rows, kpi=kpi,
            underpowered=False, underpowered_reasons=[],
            threshold_pct=cfg.stat_gate_threshold_pct,
        )["verdict"] == "GO"

    def test_single_close_is_inconclusive(self, tmp_path):
        db = _seed_closes(tmp_path / "single.db", [
            {"method": "shadow_merge", "realized_pnl": 0.30},
        ])
        kpi = _kpi_for(db)
        cfg = load_cfg()
        rows = build_gate_rows(
            closes=_run_closes(db), kpi=kpi, cfg=cfg,
            target_closes=None, matured_markouts=None, min_markouts=None,
        )
        verdict = resolve_verdict(
            gate_rows=rows, kpi=kpi,
            underpowered=False, underpowered_reasons=[],
            threshold_pct=cfg.stat_gate_threshold_pct,
        )
        assert verdict["verdict"] == "INCONCLUSIVE"
        assert "fewer than 2 closes" in verdict["verdict_reason"]


class TestArtifactBundle:
    """Issue #54 acceptance: report.json + report.md + CSVs after a run."""

    def test_report_json_includes_inclusive_trade_analytics(self, tmp_path):
        out, _ = _write_bundle(tmp_path, [
            {"method": "shadow_merge", "realized_pnl": 0.30},
            {"method": "single_buy_exit", "realized_pnl": 0.30},
            {"method": "naked_exit", "realized_pnl": 0.30},
        ])

        data = json.loads((out / "report.json").read_text(encoding="utf-8"))
        ta = data["trade_analytics"]
        # n_closes counts every close method — 3 rows, all three methods.
        assert ta["n_closes"] == 3
        assert ta["ci90_lower_pct"] is not None
        assert ta["ci90_lower_pct"] > 1.0
        assert set(data["stat_validation"]["methods_present"]) == {
            "shadow_merge", "single_buy_exit", "naked_exit",
        }
        assert data["stat_validation"]["verdict"] == "GO"
        assert data["stat_validation"]["primary_gate"].startswith(
            "ci90_lower_pct > 1.0 inclusive"
        )

        closes_csv = (out / "closes.csv").read_text(encoding="utf-8").splitlines()
        assert len(closes_csv) == 4  # header + 3 closes
        assert "condition_id" in closes_csv[0]
        assert "realized_pnl" in closes_csv[0]

    def test_report_md_has_gate_table_tautology_and_caps(self, tmp_path):
        out, _ = _write_bundle(tmp_path, [
            {"method": "shadow_merge", "realized_pnl": 0.30},
            {"method": "single_buy_exit", "realized_pnl": 0.30},
            {"method": "naked_exit", "realized_pnl": 0.30},
        ], out_name="stat_memo")
        md = (out / "report.md").read_text(encoding="utf-8")

        assert "## Verdict" in md
        assert "**GO**" in md
        assert "## Gate table" in md
        # The memo must state the tautology explicitly (acceptance criterion 2).
        assert "TAUTOLOGY DISCLAIMER" in md
        assert "E[PnL" in md
        assert "tautological" in md
        # Cost of being wrong + caps at risk. Assert the EFFECTIVE caps the
        # harness actually runs under (max_total_usd is 90% of bankroll, not
        # the $100 baseline itself) rather than a hard-coded pair.
        from core_brain.venue import MAX_ORDER_USD, MAX_TOTAL_USD
        assert "cost of being wrong" in md.lower()
        assert f"MAX_ORDER_USD={MAX_ORDER_USD:.2f}" in md
        assert f"MAX_TOTAL_USD={MAX_TOTAL_USD:.2f}" in md
        # Inclusive outcomes block.
        assert "## Inclusive outcomes" in md
        assert "total_realized_pnl" in md
        assert "single_buy_exit" in md

    def test_csvs_written_for_quotes_and_fills(self, tmp_path):
        db = tmp_path / "csvs.db"
        reg = OrderRegistry(db_path=db)
        reg.log_quote(QuoteRecord(
            ts=1.0, condition_id="0xabc", market_slug="fake-market",
            token_id="tok-up", side="UP", price=0.48, size=2, run_id=RUN_ID,
        ))
        reg.create_order(OrderRecord(
            id="o1", condition_id="0xabc", token_id="tok-up", side="BUY",
            price=0.48, original_size=2, status="filled", posted_ts=1000,
            last_polled_ts=2000, run_id=RUN_ID,
        ))
        reg.record_fill(FillRecord(
            trade_id="t1", order_uuid="o1", size=1, price=0.48,
            venue_ts=1001, recorded_ts=1002, run_id=RUN_ID,
        ))
        cfg = load_cfg()
        kpi = _kpi_for(db)
        rows = build_gate_rows(
            closes=[], kpi=kpi, cfg=cfg,
            target_closes=None, matured_markouts=None, min_markouts=None,
        )
        stat = resolve_verdict(
            gate_rows=rows, kpi=kpi,
            underpowered=False, underpowered_reasons=[],
            threshold_pct=cfg.stat_gate_threshold_pct,
        )
        out = tmp_path / "stat_csvs"
        write_artifacts(
            out, db_path=db, run_id=RUN_ID, cfg=cfg, kpi=kpi,
            verdict=stat["verdict"], verdict_reason=stat["verdict_reason"],
            gate_rows=rows,
            run_result={"status": "INCONCLUSIVE", "reason": "no closes"},
            target_closes=None, min_markouts=None,
        )
        fills_csv = (out / "fills.csv").read_text(encoding="utf-8").splitlines()
        assert len(fills_csv) == 2  # header + 1 fill
        assert "t1" in fills_csv[1]
        quotes_csv = (out / "quotes.csv").read_text(encoding="utf-8").splitlines()
        assert len(quotes_csv) == 2  # header + 1 quote
        assert "tok-up" in quotes_csv[1]

    def test_rows_to_csv_preserves_nulls_as_empty(self):
        text = rows_to_csv([{"a": 1, "b": None}, {"a": 2, "b": "x"}])
        lines = text.splitlines()
        assert lines[0] == "a,b"
        assert lines[1] == "1,"
        assert lines[2] == "2,x"

    def test_memo_renders_pairs_under_1_as_percent_not_double_scaled(self):
        """pairs_under_1 is already percent-scaled by kpi (100.0 == 100%);
        the memo must render it as a percent, never double-scale it to
        `10000.0%`. Regression for the off-by-100x bug found in a real shadow
        bundle (report.md rendered `10000.0%`)."""
        cfg = load_cfg()
        md = render_report_md(
            run_id=RUN_ID,
            db_path="data/shadow.db",
            artifact_dir="reports/stat_x",
            kpi={
                "pairs_under_1": 100.0,
                "median_pair_cost": 0.95,
                "trade_analytics": {"n_closes": 2},
                "portfolio": {"realized_pnl": 0.30},
            },
            cfg=cfg,
            gate_rows=[],
            verdict="INCONCLUSIVE",
            verdict_reason="underpowered",
            run_result={"status": "INCONCLUSIVE", "reason": "underpowered"},
            closes=[],
            target_closes=None,
            min_markouts=None,
        )
        line = next(l for l in md.splitlines() if "pairs_under_1" in l)
        assert "100.00%" in line
        assert "10000.0%" not in md

    def test_memo_renders_missing_pairs_under_1_as_clean_n_a(self):
        """When pairs_under_1 is None the memo renders a clean `n/a`, never the
        malformed `n/a%` (the '%' suffix must belong to a value, not a marker)."""
        cfg = load_cfg()
        md = render_report_md(
            run_id=RUN_ID,
            db_path="data/shadow.db",
            artifact_dir="reports/stat_x",
            kpi={
                "pairs_under_1": None,
                "median_pair_cost": None,
                "trade_analytics": {"n_closes": 0},
                "portfolio": {"realized_pnl": 0.0},
            },
            cfg=cfg,
            gate_rows=[],
            verdict="INCONCLUSIVE",
            verdict_reason="underpowered",
            run_result={"status": "INCONCLUSIVE", "reason": "underpowered"},
            closes=[],
            target_closes=None,
            min_markouts=None,
        )
        line = next(l for l in md.splitlines() if "pairs_under_1" in l)
        assert "pairs_under_1**: n/a " in line
        assert "%" not in line


class TestPessimisticSensitivity:
    """Issue #55: the artifact bundle embeds the base/pessimistic sensitivity
    column and the GO-survives-pessimism verdict."""

    def test_report_json_carries_base_and_pessimistic_variants(self, tmp_path):
        closes = [
            {"method": "shadow_merge", "realized_pnl": 0.30,
             "cost_basis": 9.8, "shares": 10},
            {"method": "shadow_merge", "realized_pnl": 0.30,
             "cost_basis": 9.8, "shares": 10},
            {"method": "shadow_merge", "realized_pnl": 0.30,
             "cost_basis": 9.8, "shares": 10},
        ]
        out, _kpi = _write_bundle(tmp_path, closes)

        data = json.loads((out / "report.json").read_text(encoding="utf-8"))
        sens = data["sensitivity"]
        assert set(sens) >= {"base", "pessimistic", "verdict",
                             "gas", "tick_per_share", "threshold_pct"}
        # The relayer pays for a merge, so the pessimistic column charges
        # the tick and nothing else.
        assert sens["gas"] == pytest.approx(0.0)
        assert sens["base"]["ci90_lower_pct"] >= 1.0
        assert sens["pessimistic"]["ci90_lower_pct"] >= 1.0
        assert sens["verdict"] == "GO"
        assert sens["base"]["rebate_est"] is None
        assert sens["pessimistic"]["rebate_est"] is None

        md = (out / "report.md").read_text(encoding="utf-8")
        assert "Pessimistic sensitivity" in md
        assert "no fill is ever credited without" in md

    def test_a_primary_go_is_downgraded_when_pessimism_fails(self, tmp_path):
        """A primary GO must not be headlined when the sensitivity gate says
        NO-GO -- the report would otherwise claim GO survived pessimism while
        presenting one that did not."""
        # Base passes (0.40/19.6 = 2.04%) but the pessimistic column charges
        # shares*tick (22 * $0.01 = $0.22), leaving 0.18/19.6 = 0.92%.
        closes = [
            {"method": "shadow_merge", "realized_pnl": 0.40,
             "cost_basis": 19.6, "shares": 22}
        ] * 5
        out, _kpi = _write_bundle(tmp_path, closes)

        data = json.loads((out / "report.json").read_text(encoding="utf-8"))
        sens = data["sensitivity"]
        assert sens["base"]["ci90_lower_pct"] >= 1.0
        assert sens["pessimistic"]["ci90_lower_pct"] < 1.0
        assert sens["verdict"] == "NO-GO"
        # The effective (headline) verdict is the downgraded sensitivity verdict.
        assert data["stat_validation"]["verdict"] == "NO-GO"

        md = (out / "report.md").read_text(encoding="utf-8")
        headline = next(l for l in md.splitlines()
                        if l.strip() == "**NO-GO**")
        assert headline == "**NO-GO**"

    def test_pnl_by_fill_path_in_artifacts(self, tmp_path):
        closes = [
            {"method": "shadow_merge", "realized_pnl": 0.50,
             "cost_basis": 9.5, "shares": 10},
            {"method": "single_buy_exit", "realized_pnl": -0.20,
             "cost_basis": 5.0, "shares": 5},
        ]
        out, _kpi = _write_bundle(tmp_path, closes)

        data = json.loads((out / "report.json").read_text(encoding="utf-8"))
        assert "pnl_by_fill_path" in data["stat_validation"]
        assert "pnl_by_fill_path" in data
        pnl_split = data["stat_validation"]["pnl_by_fill_path"]
        assert pnl_split["total"] == pytest.approx(0.30)
        assert pnl_split["by_path"]["maker_merged"] == pytest.approx(0.50)
        assert pnl_split["by_path"]["single_buy_exit"] == pytest.approx(-0.20)

        md = (out / "report.md").read_text(encoding="utf-8")
        assert "Realized PnL attribution by fill path" in md
        assert "Maker-merged" in md
        assert "Taker-completed" in md
        assert "Single-buy exit" in md
        assert "Shadow-only estimate" in md


class TestMethodGroupsSingleSourced541:
    """#541: artifact readers import close-method groups from kpi — one tuple per idea."""

    def test_method_tuples_are_the_kpi_objects(self):
        import core_brain.kpi as kpi_mod
        import statistical_validation_run.artifacts as artifacts_mod

        assert artifacts_mod.MERGE_METHODS is kpi_mod.MERGE_METHODS
        assert artifacts_mod.SINGLE_BUY_EXIT_METHODS is kpi_mod.SINGLE_BUY_EXIT_METHODS

