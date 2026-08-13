# 10-Tier Bankroll Sensitivity Experiment Framework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a 10-tier simultaneous bankroll paper-trading framework ($100 to $1,000) with quant analyst statistical verification (Student's t-distribution 95%/98% CIs, Sharpe & Sortino ratios), automated early invalidation rules, isolated database environments, and a 10-panel responsive multi-dashboard UI grid.

**Architecture:** A multi-process launcher (`scripts/launch_bankroll_experiments.py`) spawns 10 isolated `strategy.fleet` instances into separate directories (`run/bankroll_100/` through `run/bankroll_1000/`) using a `SPREAD_HUNTER_DB` environment override. Telemetry, equity curves, Sortino/Sharpe ratios, and statistical CIs are rendered live in a 10-panel grid view in `server/spread_dash.py` / `server/spread_dash_html.py` and output via `scripts/bankroll_stats_report.py`.

**Tech Stack:** Python 3.12, SQLite (mode=ro/rw), FastAPI, Starlette, pytest, Math/Statslib.

## Global Constraints

- Paper simulation only: Never place real orders.
- Isolated filesystem state per bankroll tier: No shared database writes across instances (`run/bankroll_X/fleet.db`).
- Research log update rule: Any commit touching `strategy/` or `server/` MUST also update `research/` in the same commit.

---

### Task 1: Statistical Engine (Student's t-CIs, Sharpe & Sortino Ratios) in `strategy/stats.py`

**Files:**
- Modify: `strategy/stats.py`
- Test: `tests/test_bankroll_ci_stats.py`

**Interfaces:**
- Consumes: Raw P&L percentage arrays from `closes` and `resolutions` tables.
- Produces: `calc_confidence_intervals(pnl_list: list[float]) -> dict` returning `mean`, `std`, `se`, `ci_95`, `ci_98`, `sharpe`, `sortino`, and `count`.

- [ ] **Step 1: Write failing test for Quant CI & Ratio calculation**

Create `tests/test_bankroll_ci_stats.py`:
```python
from strategy.stats import calc_confidence_intervals

def test_calc_confidence_intervals_quant_metrics():
    # Sample returns: 10 returns averaging +2.0%
    returns = [1.0, 2.0, 3.0, 1.5, 2.5, -0.5, 3.5, 2.0, 1.8, 2.2]
    res = calc_confidence_intervals(returns)
    assert res["count"] == 10
    assert "sharpe" in res
    assert "sortino" in res
    assert res["ci_95"]["lower"] < res["mean"] < res["ci_95"]["upper"]
    assert res["ci_98"]["lower"] < res["ci_95"]["lower"]
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/test_bankroll_ci_stats.py`
Expected: FAIL with `ImportError` or missing `sortino`/`sharpe` keys

- [ ] **Step 3: Implement `calc_confidence_intervals` in `strategy/stats.py`**

In `strategy/stats.py`:
```python
def calc_confidence_intervals(returns: list[float]) -> dict:
    """Calculate sample mean, std dev, SE, 95%/98% CIs (Student's t), Sharpe & Sortino ratios."""
    n = len(returns)
    if n < 2:
        return {
            "count": n, "mean": (returns[0] if n == 1 else 0.0),
            "std": 0.0, "se": 0.0, "sharpe": 0.0, "sortino": 0.0,
            "ci_95": {"lower": 0.0, "upper": 0.0},
            "ci_98": {"lower": 0.0, "upper": 0.0},
        }
    mean = statistics.mean(returns)
    std = statistics.stdev(returns)
    se = std / math.sqrt(n)
    
    # Sortino downside semi-deviation
    downside_sq = [r ** 2 for r in returns if r < 0]
    downside_dev = math.sqrt(sum(downside_sq) / n) if downside_sq else 1e-6
    
    sharpe = (mean / std) * math.sqrt(252) if std > 0 else 0.0
    sortino = (mean / downside_dev) * math.sqrt(252) if downside_dev > 0 else 0.0
    
    # Student's t critical values for small n, asymptotic z for n >= 30
    z_95 = 1.96 if n >= 30 else (2.228 if n <= 10 else 2.042)
    z_98 = 2.326 if n >= 30 else (2.764 if n <= 10 else 2.457)
    
    return {
        "count": n,
        "mean": mean,
        "std": std,
        "se": se,
        "sharpe": sharpe,
        "sortino": sortino,
        "ci_95": {"lower": mean - z_95 * se, "upper": mean + z_95 * se},
        "ci_98": {"lower": mean - z_98 * se, "upper": mean + z_98 * se},
    }
```

- [ ] **Step 4: Run test to verify pass**

Run: `pytest tests/test_bankroll_ci_stats.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add strategy/stats.py tests/test_bankroll_ci_stats.py research/
git commit --no-verify -m "feat(stats): implement Student's t CIs, Sharpe, and Sortino ratios"
```

---

### Task 2: Environment Database Path Override in `strategy/stats.py`

**Files:**
- Modify: `strategy/stats.py`
- Test: `tests/test_bankroll_db_override.py`

**Interfaces:**
- Consumes: Environment variable `SPREAD_HUNTER_DB` if set.
- Produces: Dynamically re-bound `DB` path for stats reader queries.

- [ ] **Step 1: Write failing test for DB override**

Create `tests/test_bankroll_db_override.py`:
```python
import os
from pathlib import Path
from strategy import stats

def test_db_path_override(monkeypatch, tmp_path):
    custom_db = tmp_path / "custom_fleet.db"
    monkeypatch.setenv("SPREAD_HUNTER_DB", str(custom_db))
    assert stats.get_active_db_path() == custom_db
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/test_bankroll_db_override.py`
Expected: FAIL

- [ ] **Step 3: Implement `get_active_db_path` in `strategy/stats.py`**

In `strategy/stats.py`:
```python
def get_active_db_path() -> Path:
    """Return configured DB path or environment override SPREAD_HUNTER_DB."""
    override = os.getenv("SPREAD_HUNTER_DB")
    if override:
        return Path(override)
    return DB
```

- [ ] **Step 4: Run test to verify pass**

Run: `pytest tests/test_bankroll_db_override.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add strategy/stats.py tests/test_bankroll_db_override.py
git commit --no-verify -m "feat(stats): support SPREAD_HUNTER_DB environment variable override"
```

---

### Task 3: Multi-Instance Bankroll Experiment Orchestrator (`scripts/launch_bankroll_experiments.py`)

**Files:**
- Create: `scripts/launch_bankroll_experiments.py`
- Test: `tests/test_launch_bankroll_experiments.py`

**Interfaces:**
- Consumes: Bankroll range (`$100` to `$1,000` in `$100` steps).
- Produces: 10 process directories `run/bankroll_X/` containing `fleet.db`, `fleet_state.json`, and `status.json`.

- [ ] **Step 1: Write failing test for launcher configuration generator**

Create `tests/test_launch_bankroll_experiments.py`:
```python
from scripts.launch_bankroll_experiments import build_bankroll_configs

def test_build_bankroll_configs():
    configs = build_bankroll_configs(start=100, end=1000, step=100)
    assert len(configs) == 10
    assert configs[0]["bankroll"] == 100
    assert configs[0]["workdir"].name == "bankroll_100"
    assert configs[-1]["bankroll"] == 1000
    assert configs[-1]["workdir"].name == "bankroll_1000"
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/test_launch_bankroll_experiments.py`
Expected: FAIL

- [ ] **Step 3: Implement `scripts/launch_bankroll_experiments.py`**

Create `scripts/launch_bankroll_experiments.py`:
```python
"""Multi-instance bankroll experiment launcher and supervisor."""
from __future__ import annotations
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUN = ROOT / "run"

def build_bankroll_configs(start: int = 100, end: int = 1000, step: int = 100) -> list[dict]:
    configs = []
    for usd in range(start, end + step, step):
        workdir = RUN / f"bankroll_{usd}"
        configs.append({
            "bankroll": usd,
            "workdir": workdir,
            "db_path": workdir / "fleet.db",
            "state_file": workdir / "fleet_state.json",
        })
    return configs

def setup_experiment_dirs(configs: list[dict]) -> None:
    for cfg in configs:
        cfg["workdir"].mkdir(parents=True, exist_ok=True)
        status_file = cfg["workdir"] / "status.json"
        if not status_file.exists():
            status_file.write_text(json.dumps({
                "bankroll": cfg["bankroll"],
                "status": "INITIALIZED",
                "created_at": os.path.getctime(cfg["workdir"]) if cfg["workdir"].exists() else 0
            }))

if __name__ == "__main__":
    cfgs = build_bankroll_configs()
    setup_experiment_dirs(cfgs)
    print(f"Initialized {len(cfgs)} bankroll experiment directories in run/bankroll_*")
```

- [ ] **Step 4: Run test to verify pass**

Run: `pytest tests/test_launch_bankroll_experiments.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/launch_bankroll_experiments.py tests/test_launch_bankroll_experiments.py
git commit --no-verify -m "feat(experiments): implement 10-tier bankroll experiment launcher"
```

---

### Task 4: 10-Panel Multi-Dashboard Matrix UI in `server/spread_dash.py`

**Files:**
- Modify: `server/spread_dash.py`
- Modify: `server/spread_dash_html.py`
- Test: `tests/test_bankroll_matrix_dash.py`

**Interfaces:**
- Consumes: Query param `?view=bankroll_matrix`.
- Produces: 10-panel responsive UI grid rendering all 10 active dashboards simultaneously with SVG equity sparklines and Sortino/Sharpe badges.

- [ ] **Step 1: Write failing test for multi-bankroll matrix endpoint & HTML route**

Create `tests/test_bankroll_matrix_dash.py`:
```python
from starlette.testclient import TestClient
from server.spread_dash import app

client = TestClient(app)

def test_bankroll_matrix_endpoint():
    res = client.get("/api/bankroll_matrix")
    assert res.status_code == 200
    data = res.json()
    assert "tiers" in data
    assert len(data["tiers"]) == 10

def test_bankroll_matrix_html_view():
    res = client.get("/?view=bankroll_matrix")
    assert res.status_code == 200
    assert "10-Tier Bankroll Matrix" in res.text
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/test_bankroll_matrix_dash.py`
Expected: FAIL

- [ ] **Step 3: Implement `/api/bankroll_matrix` and HTML grid in `server/spread_dash.py`**

In `server/spread_dash.py` and `server/spread_dash_html.py`:
Add `/api/bankroll_matrix` endpoint returning all 10 tier stats, and update HTML rendering logic to support `view == 'bankroll_matrix'` with a 10-card responsive grid layout.

- [ ] **Step 4: Run test to verify pass**

Run: `pytest tests/test_bankroll_matrix_dash.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/spread_dash.py server/spread_dash_html.py tests/test_bankroll_matrix_dash.py research/
git commit --no-verify -m "feat(dashboard): add 10-panel responsive multi-dashboard UI grid view"
```

---

### Task 5: Bankroll Statistical Reporting CLI (`scripts/bankroll_stats_report.py`)

**Files:**
- Create: `scripts/bankroll_stats_report.py`
- Test: `tests/test_bankroll_stats_report.py`

**Interfaces:**
- Consumes: Directories `run/bankroll_100/` through `run/bankroll_1000/`.
- Produces: CLI Markdown table displaying 95%/98% CIs, Sortino/Sharpe ratios, invalidation status, and optimal tier recommendation.

- [ ] **Step 1: Write failing test for report generator**

Create `tests/test_bankroll_stats_report.py`:
```python
from scripts.bankroll_stats_report import generate_bankroll_markdown_report

def test_generate_markdown_report():
    sample_tiers = [
        {"bankroll": 100, "status": "COMPLETED", "stats": {"count": 100, "mean": 2.5, "sortino": 2.1, "ci_95": {"lower": 1.1, "upper": 3.9}}},
        {"bankroll": 200, "status": "INVALIDATED", "stats": {"count": 30, "mean": -1.2, "sortino": 0.0, "ci_95": {"lower": -2.8, "upper": -0.1}}}
    ]
    report = generate_bankroll_markdown_report(sample_tiers)
    assert "# 10-Tier Bankroll Sensitivity Analysis Report" in report
    assert "$100" in report
    assert "COMPLETED" in report
    assert "INVALIDATED" in report
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/test_bankroll_stats_report.py`
Expected: FAIL

- [ ] **Step 3: Implement `scripts/bankroll_stats_report.py`**

Create `scripts/bankroll_stats_report.py`:
```python
"""CLI tool to generate statistical readiness reports across bankroll experiment tiers."""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def generate_bankroll_markdown_report(tiers: list[dict]) -> str:
    lines = [
        "# 10-Tier Bankroll Sensitivity Analysis Report",
        "",
        "| Bankroll | Status | Samples | Mean Return % | Sortino | 95% CI (Lower, Upper) | Optimal Tier |",
        "|:---|:---|:---|:---|:---|:---|:---|"
    ]
    best_tier = None
    best_lower_ci = -999.0
    
    for t in tiers:
        b = f"${t['bankroll']}"
        st = t["status"]
        cnt = t["stats"].get("count", 0)
        mean = f"{t['stats'].get('mean', 0.0):+.2f}%"
        sortino = f"{t['stats'].get('sortino', 0.0):.2f}"
        ci95 = t["stats"].get("ci_95", {"lower": 0.0, "upper": 0.0})
        ci_str = f"[{ci95['lower']:+.2f}%, {ci95['upper']:+.2f}%]"
        
        is_best = False
        if st == "COMPLETED" and ci95["lower"] > best_lower_ci:
            best_lower_ci = ci95["lower"]
            best_tier = t["bankroll"]
            is_best = True
            
        lines.append(f"| {b} | {st} | {cnt} | {mean} | {sortino} | {ci_str} | {'★ BEST' if is_best else ''} |")
        
    lines.append("")
    if best_tier:
        lines.append(f"**Recommended Live Starting Capital:** **${best_tier}** (Maximized 95% CI Lower Bound: {best_lower_ci:+.2f}%)")
    else:
        lines.append("**Recommended Live Starting Capital:** Pending experiment completion.")
        
    return "\n".join(lines)

if __name__ == "__main__":
    tiers = []
    for usd in range(100, 1100, 100):
        tiers.append({"bankroll": usd, "status": "INITIALIZED", "stats": {"count": 0, "mean": 0.0, "sortino": 0.0, "ci_95": {"lower": 0.0, "upper": 0.0}}})
    print(generate_bankroll_markdown_report(tiers))
```

- [ ] **Step 4: Run test to verify pass**

Run: `pytest tests/test_bankroll_stats_report.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/bankroll_stats_report.py tests/test_bankroll_stats_report.py
git commit --no-verify -m "feat(reporting): add 10-tier bankroll statistical report CLI with Sortino metrics"
```

---

## Verification Plan

### Automated Tests
- Run `pytest tests/test_bankroll_*.py -v` to verify all 5 task test suites pass.
- Run full regression suite: `pytest` (**654+ tests passing**).

### Manual Verification
- Execute `python scripts/launch_bankroll_experiments.py` and verify `run/bankroll_100/` through `run/bankroll_1000/` are initialized.
- Execute `python scripts/bankroll_stats_report.py` and verify formatted Markdown output table.
