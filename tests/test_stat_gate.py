"""Tests for statistical validation decision math and the standalone stat_gate CLI.

Issue #51: Stat Validation #1: Total-PnL decision math — 90% CI (1%, inf) inclusive + dry-calc CLI.
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

from core_brain.config import MakerConfig, load as load_cfg
from core_brain.kpi import (
    _wilson_ci,
    evaluate_stat_gate,
    gate_definition,
    power_table,
    required_sample_size,
    Z_ALPHA_90_ONE_SIDED,
    Z_BETA_80_POWER,
)
import scripts.stat_gate as stat_gate_script


# --------------------------------------------------------------------------
# 1. Wilson Interval Edge Cases
# --------------------------------------------------------------------------

def test_wilson_ci_edge_cases():
    """Wilson CI handles n=0, n=1, all wins, and all losses without collapsing."""
    # n <= 0 returns None
    assert _wilson_ci(0, 0) is None
    assert _wilson_ci(1, -5) is None

    # n = 1, single win: interval is strictly bounded inside [0, 1] and width > 0
    ci_one_win = _wilson_ci(1, 1)
    assert ci_one_win is not None
    assert 0.0 < ci_one_win["lower"] < 1.0
    assert ci_one_win["upper"] == pytest.approx(1.0)
    assert ci_one_win["lower"] <= 1.0 <= ci_one_win["upper"]

    # n = 1, single loss
    ci_one_loss = _wilson_ci(0, 1)
    assert ci_one_loss is not None
    assert ci_one_loss["lower"] == pytest.approx(0.0)
    assert 0.0 < ci_one_loss["upper"] < 1.0

    # 100% wins at n=20
    ci_all_wins = _wilson_ci(20, 20)
    assert ci_all_wins is not None
    assert ci_all_wins["lower"] > 0.80
    assert ci_all_wins["upper"] == pytest.approx(1.0)

    # 0% wins at n=20
    ci_all_losses = _wilson_ci(0, 20)
    assert ci_all_losses is not None
    assert ci_all_losses["lower"] == pytest.approx(0.0)
    assert ci_all_losses["upper"] < 0.20


# --------------------------------------------------------------------------
# 2. Power Calculation Math & Edge Cases
# --------------------------------------------------------------------------

def test_required_sample_size_formula():
    """Formula n = ceil(((z_alpha + z_beta) * sigma / delta) ** 2)."""
    z_a = 1.645
    z_b = 0.8416
    sigma = 0.05
    delta = 0.01

    # Exact expected: ((1.645 + 0.8416) * 0.05 / 0.01) ** 2 = (2.4866 * 5)^2 = 12.433^2 = 154.58 -> ceil 155
    n = required_sample_size(sigma=sigma, delta=delta, z_alpha=z_a, z_beta=z_b)
    expected = math.ceil(((z_a + z_b) * sigma / delta) ** 2)
    assert n == expected
    assert n == 155


def test_required_sample_size_edge_cases():
    """Invalid or unmeasurable parameters safely return None."""
    assert required_sample_size(sigma=None, delta=0.01) is None
    assert required_sample_size(sigma=0.05, delta=None) is None
    assert required_sample_size(sigma=0.0, delta=0.01) is None
    assert required_sample_size(sigma=-0.05, delta=0.01) is None
    assert required_sample_size(sigma=0.05, delta=0.0) is None
    assert required_sample_size(sigma=0.05, delta=-0.01) is None
    assert required_sample_size(sigma=float("nan"), delta=0.01) is None
    assert required_sample_size(sigma=0.05, delta=float("inf")) is None


def test_power_table_generation():
    """power_table produces valid sample size and Wilson half-widths per delta."""
    table = power_table(sigma=0.05, deltas=(0.01, 0.02, 0.04))
    assert len(table) == 3

    d1 = table[0]
    assert d1["delta"] == pytest.approx(0.01)
    assert d1["n_required"] == 155
    assert d1["wilson_half_width"] is not None
    assert 0.0 < d1["wilson_half_width"] < 0.10

    d2 = table[1]
    assert d2["delta"] == pytest.approx(0.02)
    assert d2["n_required"] == 39
    assert d2["n_required"] < d1["n_required"]

    d4 = table[2]
    assert d4["delta"] == pytest.approx(0.04)
    assert d4["n_required"] == 10
    assert d4["n_required"] < d2["n_required"]


# --------------------------------------------------------------------------
# 3. Gate Definition & Tautology Disclaimer
# --------------------------------------------------------------------------

def test_gate_definition_content():
    """Gate definition contains inclusive 90% CI rules and tautology disclaimer."""
    gdef = gate_definition()
    assert "ci90_lower_pct" in gdef["primary_gate"]
    assert gdef["threshold_pct"] == 1.0
    assert gdef["bankroll_fraction"] == 0.01
    assert "tautology_disclaimer" in gdef
    disclaimer = gdef["tautology_disclaimer"]
    assert "tautology" in disclaimer.lower() or "tautological" in disclaimer.lower()
    assert "merge" in disclaimer.lower()
    assert "single_buy" in disclaimer.lower() or "naked" in disclaimer.lower()


# --------------------------------------------------------------------------
# 4. Statistical Gate Evaluation (Inclusive Multi-Method Closes)
# --------------------------------------------------------------------------

def test_evaluate_stat_gate_passes_on_strong_sample():
    """A consistently profitable run passes the inclusive gate."""
    # 50 trades with +3% average return, std dev 1%
    closes = []
    for i in range(50):
        # 45 merges at +$0.15 on $5.00 (+3.0%)
        # 5 exits at -$0.05 on $5.00 (-1.0%)
        if i < 45:
            closes.append(dict(realized_pnl=0.15, cost_basis=5.00, method="merge", ts=float(i)))
        else:
            closes.append(dict(realized_pnl=-0.05, cost_basis=5.00, method="single_buy_exit", ts=float(i)))

    eval_res = evaluate_stat_gate(closes, starting_capital=100.0, threshold_pct=1.0)
    assert eval_res["passed"] is True
    assert eval_res["ci90_lower_pct"] > 1.0
    assert eval_res["n_closes"] == 50
    assert "merge" in eval_res["methods_present"]
    assert "single_buy_exit" in eval_res["methods_present"]


def test_evaluate_stat_gate_fails_when_underpowered_or_loss():
    """A run with high drag or low sample fails the inclusive gate."""
    # 3 trades: 2 merges +$0.10, 1 single buy exit -$0.50 -> net negative
    closes = [
        dict(realized_pnl=0.10, cost_basis=5.00, method="merge", ts=1.0),
        dict(realized_pnl=0.10, cost_basis=5.00, method="merge", ts=2.0),
        dict(realized_pnl=-0.50, cost_basis=5.00, method="single_buy_exit", ts=3.0),
    ]
    eval_res = evaluate_stat_gate(closes, starting_capital=100.0, threshold_pct=1.0)
    assert eval_res["passed"] is False
    assert eval_res["ci90_lower_pct"] is not None and eval_res["ci90_lower_pct"] < 1.0


def test_inclusive_gate_fails_where_merge_only_passes():
    """The gate is inclusive of ALL exit methods, so a merge-only slice cannot
    manufacture a pass.

    Two profitable merges clear the bar in isolation; adding one large naked
    exit -- all loss, no hedge -- drags the inclusive CI below the threshold.
    Filtering to merges only would be a tautology. This is the guard that fails
    if the gate is ever reimplemented over merges alone.
    """
    merge_only = [
        dict(realized_pnl=0.10, cost_basis=5.00, method="merge", ts=1.0),
        dict(realized_pnl=0.10, cost_basis=5.00, method="merge", ts=2.0),
    ]
    inclusive = merge_only + [
        dict(realized_pnl=-0.50, cost_basis=5.00, method="naked_exit", ts=3.0),
    ]

    merge_eval = evaluate_stat_gate(merge_only, starting_capital=100.0, threshold_pct=1.0)
    inc_eval = evaluate_stat_gate(inclusive, starting_capital=100.0, threshold_pct=1.0)

    # Merge-only would pass -- the tautology the inclusive gate rejects.
    assert merge_eval["passed"] is True
    assert merge_eval["ci90_lower_pct"] is not None
    assert merge_eval["ci90_lower_pct"] > 1.0
    # Including the naked loss flips the inclusive gate to a fail.
    assert inc_eval["passed"] is False
    assert inc_eval["ci90_lower_pct"] is not None
    assert inc_eval["ci90_lower_pct"] < 1.0
    assert "merge" in inc_eval["methods_present"]
    assert "naked_exit" in inc_eval["methods_present"]


def test_evaluate_stat_gate_empty_or_single_close():
    """Empty or 1-close dataset cannot evaluate dispersion; fails cleanly."""
    assert evaluate_stat_gate([], starting_capital=100.0)["passed"] is False
    assert evaluate_stat_gate([dict(realized_pnl=0.10, cost_basis=5.00, method="merge", ts=1.0)],
                              starting_capital=100.0)["passed"] is False


# --------------------------------------------------------------------------
# 5. Standalone CLI (`scripts/stat_gate.py`)
# --------------------------------------------------------------------------

def test_cli_stat_gate_dry_calc_text(capsys):
    """Running main() with --dry-calc outputs ASCII table and gate text."""
    rc = stat_gate_script.main(["--dry-calc", "--sigma", "0.05", "--bankroll", "100"])
    assert rc == 0
    captured = capsys.readouterr().out
    assert "STATISTICAL POWER & SAMPLE SIZE" in captured
    assert "155" in captured
    assert "TAUTOLOGY DISCLAIMER" in captured
    assert "GATE DEFINITION" in captured


def test_cli_stat_gate_json(capsys):
    """Running main() with --json outputs machine-readable JSON schema."""
    rc = stat_gate_script.main(["--dry-calc", "--json", "--sigma", "0.05", "--bankroll", "100"])
    assert rc == 0
    captured = capsys.readouterr().out
    data = json.loads(captured)
    assert "power_table" in data
    assert len(data["power_table"]) == 3
    assert data["power_table"][0]["n_required"] == 155
    assert "gate_definition" in data
    assert data["gate_definition"]["threshold_pct"] == 1.0


def test_cli_subprocess_zero_network():
    """Executing scripts/stat_gate.py via subprocess runs with no network/db dependencies."""
    repo_root = Path(__file__).resolve().parent.parent
    cmd = [sys.executable, str(repo_root / "scripts" / "stat_gate.py"), "--dry-calc"]
    res = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True, check=True)
    assert res.returncode == 0
    assert "STATISTICAL POWER & SAMPLE SIZE" in res.stdout


# --------------------------------------------------------------------------
# 6. Environment variable loader & standardization (#632)
# --------------------------------------------------------------------------

def test_stat_gate_bankroll_pct_canonical_env(monkeypatch, caplog):
    monkeypatch.setenv("HUNTER_STAT_GATE_BANKROLL_PCT", "0.03")
    monkeypatch.delenv("HUNTER_STAT_GATE_BANKROLL_FRACTION", raising=False)
    with caplog.at_level("WARNING"):
        cfg = load_cfg()
    assert cfg.stat_gate_bankroll_pct == 0.03
    assert cfg.stat_gate_bankroll_fraction == 0.03
    assert "HUNTER_STAT_GATE_BANKROLL_FRACTION is deprecated" not in caplog.text


def test_stat_gate_bankroll_fraction_legacy_env(monkeypatch, caplog):
    monkeypatch.delenv("HUNTER_STAT_GATE_BANKROLL_PCT", raising=False)
    monkeypatch.setenv("HUNTER_STAT_GATE_BANKROLL_FRACTION", "0.025")
    with caplog.at_level("WARNING"):
        cfg = load_cfg()
    assert cfg.stat_gate_bankroll_pct == 0.025
    assert cfg.stat_gate_bankroll_fraction == 0.025
    assert "HUNTER_STAT_GATE_BANKROLL_FRACTION is deprecated (#632)" in caplog.text


def test_stat_gate_bankroll_pct_wins_over_legacy(monkeypatch, caplog):
    monkeypatch.setenv("HUNTER_STAT_GATE_BANKROLL_PCT", "0.04")
    monkeypatch.setenv("HUNTER_STAT_GATE_BANKROLL_FRACTION", "0.02")
    with caplog.at_level("WARNING"):
        cfg = load_cfg()
    assert cfg.stat_gate_bankroll_pct == 0.04
    assert cfg.stat_gate_bankroll_fraction == 0.04
    assert "HUNTER_STAT_GATE_BANKROLL_FRACTION is deprecated" not in caplog.text


def test_stat_gate_display_criteria_reads_canonical():
    cfg = MakerConfig(stat_gate_bankroll_pct=0.05, stat_gate_threshold_pct=2.0)
    criteria = stat_gate_script.gate_definition(cfg)
    assert criteria["threshold_pct"] == 2.0
    assert criteria["bankroll_fraction"] == 0.05

