"""Statistical validation artifact bundle (Issue #54).

Emit the machine-readable report and human memo that prove the stat gate
inclusive of ALL losses:

    reports/stat_*/report.json            full KPI dict + stat_validation block
    reports/stat_*/report.md              gate table + GO/NO-GO/INCONCLUSIVE memo
    reports/stat_*/closes.csv             every close row of the run
    reports/stat_*/fills.csv              every fill row of the run
    reports/stat_*/quotes.csv             every quote row of the run
    reports/stat_*/config_snapshot.json   effective MakerConfig
    reports/stat_*/pipeline_snapshot.json screener funnel snapshot
    reports/stat_*/markets_snapshot.json  graduated market list

The primary gate is `ci90_lower_pct > 1.0` INCLUSIVE: the 90% one-sided CI lower
bound of mean return % across every close method (merge, single-buy exit, naked
exit). A merge-only gate would be a tautology -- merged pairs always pay $1.00
for less than $1.00, so E[PnL | successful pair] > 0 can never fail and proves
nothing. The inclusive gate is the one that can fail, and that is the one the
verdict is decided on.

Nothing here reaches the venue. Everything reads the shadow SQLite store and
the effective config, and writes text files.
"""
from __future__ import annotations

import csv
import datetime
import io
import json
import logging
from pathlib import Path
from typing import Any, Optional

from core_brain.config import MakerConfig
from core_brain.kpi import (
    build_sensitivity,
    evaluate_stat_gate,
    gate_definition,
)
from core_brain.order_registry import OrderRegistry
from core_brain.venue import MAX_ORDER_USD, MAX_TOTAL_USD

log = logging.getLogger("statistical_validation_run")

# Per-share directional-loss bound quoted in the memo (docs/agents/strategy.md):
# a stranded single-buy leg that resolves against the bet loses roughly the
# dollar it was worth if the other side had been bought.
COST_OF_BEING_WRONG = (
    "A single buy is a directional bet nobody decided to take: if one leg fills "
    "and the other never does, the stranded leg pays $1.00 if the market "
    "resolves against it, minus nothing. The cost of being wrong on GO is "
    "therefore a booked loss of up to ~$1.00 per stranded share, bounded by the "
    "venue caps below."
)

WIN_RATE_GATE = 0.50          # secondary: win_rate_ci95.lower must exceed 0.50
ADVERSE_SELECTION_GATE = -0.005  # per share; more adverse than this is a finding
MERGE_METHODS = ("merge", "shadow_merge")
EXIT_METHODS = ("single_buy_exit", "naked_exit", "ladder_exit")

GATE_ORDER = [
    "Primary — total PnL (inclusive)",
    "Total PnL $ (dollar twin)",
    "Win rate (secondary)",
    "Economic (expectancy)",
    "Adverse selection",
    "Rescue completion",
    "Sample size",
    "Markout maturity",
]


def rescue_stats(closes: list[dict]) -> dict[str, Any]:
    """Completion/exit counts for the rescue block of the memo.

    `shadow_merge` closes are completed pairs; `single_buy_exit` and
    `naked_exit` closes are the failure mode the whole gate exists to fold in.
    Their losses are already inside `total_realized_pnl` -- this block only
    labels them, it does not gate on them separately.

    Completion rate is NULL when no rescue path ever closed a position:
    "no rescues needed" and "rescues never resolved" are different facts.
    """
    merges = [c for c in closes if c.get("method") in MERGE_METHODS]
    exits = [c for c in closes if c.get("method") in EXIT_METHODS]
    resolved = merges + exits
    return {
        "merges": len(merges),
        "exits": len(exits),
        "other_methods": sorted(
            {c.get("method") or "unknown" for c in closes}
            - set(MERGE_METHODS) - set(EXIT_METHODS)
        ),
        "completion_rate": (len(merges) / len(resolved)) if resolved else None,
        "exit_rate": (len(exits) / len(resolved)) if resolved else None,
    }


def build_gate_rows(
    *,
    closes: list[dict],
    kpi: dict[str, Any],
    cfg: MakerConfig,
    target_closes: Optional[int],
    matured_markouts: Optional[int],
    min_markouts: Optional[int],
) -> list[dict[str, Any]]:
    """One row per gate, in the order the memo renders them.

    Each row carries `gate`, `metric`, `value`, `threshold`, `passed` and a
    one-line `rationale`. `passed` is True/False where the gate is checkable
    and None where the metric could not be measured -- a NULL metric is never
    reported as a passing gate.
    """
    ta = kpi.get("trade_analytics") or {}
    portfolio = kpi.get("portfolio") or {}
    starting_capital = portfolio.get("starting_capital")

    gate = evaluate_stat_gate(
        closes,
        starting_capital=float(starting_capital) if starting_capital is not None else 0.0,
        threshold_pct=cfg.stat_gate_threshold_pct,
        bankroll_fraction=cfg.stat_gate_bankroll_fraction,
    )
    ci90 = ta.get("ci90_lower_pct")
    ci90_usd = gate.get("ci90_lower_usd")
    win_ci = (ta.get("win_rate_ci95") or {}).get("lower")
    expectancy = ta.get("expectancy_usd")
    adverse = kpi.get("adverse_selection")
    n_closes = ta.get("n_closes", 0)
    rescues = rescue_stats(closes)

    dollar_threshold = (
        float(starting_capital) * cfg.stat_gate_bankroll_fraction
        if starting_capital is not None else None
    )

    # Strict `>` on both twins, matching the artifact policy in the issue
    # (90% CI (1%, inf)) rather than kpi.evaluate_stat_gate's `>=`. Equality at
    # the boundary must read as FAIL, and the config threshold is env-
    # overridable (HUNTER_STAT_GATE_THRESHOLD_PCT), so nothing here hard-codes
    # 1.0.
    rows: list[dict[str, Any]] = [
        {
            "gate": "Primary — total PnL (inclusive)",
            "metric": "ci90_lower_pct",
            "value": ci90,
            "threshold": f"> {cfg.stat_gate_threshold_pct}",
            # A CI that could not be computed (no cost basis on any close) is
            # unmeasurable, not failed: the memo renders it n/a and the verdict
            # falls to INCONCLUSIVE.
            "passed": (
                None if ci90 is None else ci90 > cfg.stat_gate_threshold_pct
            ),
            "rationale": (
                "one-sided 90% CI lower bound on mean return %, every close "
                "method and every loss included"
            ),
        },
        {
            "gate": "Total PnL $ (dollar twin)",
            "metric": "ci90_lower_usd",
            "value": ci90_usd,
            "threshold": (
                f"> ${dollar_threshold:.2f}" if dollar_threshold is not None else "n/a"
            ),
            "passed": (
                None
                if ci90_usd is None or dollar_threshold is None
                else ci90_usd > dollar_threshold
            ),
            "rationale": "same gate in dollars: lower bound must exceed 1% of bankroll",
        },
        {
            "gate": "Win rate (secondary)",
            "metric": "win_rate_ci95.lower",
            "value": win_ci,
            "threshold": f"> {WIN_RATE_GATE}",
            "passed": None if win_ci is None else win_ci > WIN_RATE_GATE,
            "rationale": "wins are not a coin flip (Wilson interval) — secondary only",
        },
        {
            "gate": "Economic (expectancy)",
            "metric": "expectancy_usd",
            "value": expectancy,
            "threshold": "> $0.00",
            "passed": None if expectancy is None else expectancy > 0.0,
            "rationale": "edge per close is positive; folded into total PnL anyway",
        },
        {
            "gate": "Adverse selection",
            "metric": "adverse_selection/share",
            "value": adverse,
            "threshold": f"> {ADVERSE_SELECTION_GATE}",
            "passed": None if adverse is None else adverse > ADVERSE_SELECTION_GATE,
            "rationale": "size-weighted markout drift to longest matured horizon",
        },
        {
            "gate": "Rescue completion",
            "metric": "completion_rate",
            "value": rescues["completion_rate"],
            "threshold": "reported",
            "passed": None,
            "rationale": "not a gate: exit losses are already inside the primary gate",
        },
        {
            "gate": "Sample size",
            "metric": "n_closes",
            "value": n_closes,
            "threshold": (
                f">= {target_closes}" if target_closes is not None else "reported"
            ),
            "passed": (
                target_closes is not None and n_closes >= target_closes
            ),
            "rationale": "underpowered runs cannot be GO",
        },
        {
            "gate": "Markout maturity",
            "metric": "matured_markouts",
            "value": matured_markouts,
            "threshold": (
                f">= {min_markouts}" if min_markouts is not None else "reported"
            ),
            "passed": (
                min_markouts is not None
                and matured_markouts is not None
                and matured_markouts >= min_markouts
            ),
            "rationale": "immature markouts read as insufficient_sample, not zero drift",
        },
    ]

    # GATE_ORDER is a name-keyed filter, so a gate renamed here and not there
    # disappears from the memo without a word -- the report simply comes back
    # one gate shorter and still reads as complete. Renaming the economic gate
    # off `merge_gas_usd` did exactly that. Fail loudly instead.
    ordered = {r["gate"]: r for r in rows}
    unlisted = [name for name in ordered if name not in GATE_ORDER]
    if unlisted:
        raise ValueError(
            "gate(s) built but missing from GATE_ORDER, so they would be "
            f"dropped from the memo: {unlisted}"
        )
    return [ordered[name] for name in GATE_ORDER if name in ordered]


def resolve_verdict(
    *,
    gate_rows: list[dict[str, Any]],
    kpi: dict[str, Any],
    underpowered: bool,
    underpowered_reasons: list[str],
    threshold_pct: float,
) -> dict[str, str]:
    """Decide GO / NO-GO / INCONCLUSIVE from the gate rows.

    Order of precedence:
    1. Underpowered (closes below target or markouts below minimum) -> INCONCLUSIVE.
    2. Fewer than 2 closes -> INCONCLUSIVE (a CI needs a variance).
    3. Primary gate passed (strict `> threshold_pct`) -> GO.
    4. Otherwise -> NO-GO.

    Secondary gates are reported but never block: a 51% win rate with large
    exits is still a net loss, so the primary gate is the only veto.

    `threshold_pct` is the EFFECTIVE configured threshold
    (``cfg.stat_gate_threshold_pct``, env-overridable via
    ``HUNTER_STAT_GATE_THRESHOLD_PCT``) and is used in the verdict text so the
    memo never hard-codes 1.0.
    """
    ta = kpi.get("trade_analytics") or {}
    n_closes = ta.get("n_closes", 0)

    if underpowered:
        reason = f"underpowered: {', '.join(underpowered_reasons) or 'sample below target'}"
        return {"verdict": "INCONCLUSIVE", "verdict_reason": reason}
    if n_closes < 2:
        return {
            "verdict": "INCONCLUSIVE",
            "verdict_reason": (
                "insufficient sample: fewer than 2 closes, so no confidence "
                "interval can be computed"
            ),
        }

    primary = next(
        (r for r in gate_rows if r["gate"] == "Primary — total PnL (inclusive)"),
        None,
    )
    value = primary.get("value") if primary else None
    if primary is None or value is None:
        # A CI that cannot be computed is not a failed gate — it is a run
        # without enough evidence, which is the INCONCLUSIVE definition.
        return {
            "verdict": "INCONCLUSIVE",
            "verdict_reason": (
                "no confidence interval could be computed from the closes "
                "(no close carries a measurable cost basis) — extend the run"
            ),
        }
    if primary.get("passed") is True:
        return {
            "verdict": "GO",
            "verdict_reason": (
                f"primary gate passed: 90% CI lower bound {value:.4f}% on mean "
                f"return sits above {threshold_pct}%, inclusive of every close "
                "method and every loss"
            ),
        }
    return {
        "verdict": "NO-GO",
        "verdict_reason": (
            f"primary gate failed: 90% CI lower bound {value:.4f}% on mean "
            f"return does not sit above {threshold_pct}% with adequate sample "
            "— extend the run or investigate the exit path"
        ),
    }


def _fmt(v: Any, digits: int = 4) -> str:
    """One stable way to print a metric value, NULL preserved."""
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.{digits}f}"
    return str(v)


def _pct(v: Any) -> str:
    if v is None:
        return "n/a"
    return f"{float(v) * 100:.1f}%"


def render_report_md(
    *,
    run_id: str,
    db_path: Path | str,
    artifact_dir: Path | str,
    kpi: dict[str, Any],
    cfg: MakerConfig,
    gate_rows: list[dict[str, Any]],
    verdict: str,
    verdict_reason: str,
    run_result: dict[str, Any],
    closes: list[dict[str, Any]],
    target_closes: Optional[int],
    min_markouts: Optional[int],
    sensitivity: Optional[dict[str, Any]] = None,
) -> str:
    """The human decision memo: verdict first, gate table, inclusive outcomes,
    resilience + pessimistic sensitivity, mechanics, cost of being wrong, caps,
    limitations."""
    ta = kpi.get("trade_analytics") or {}
    portfolio = kpi.get("portfolio") or {}
    if sensitivity is None:
        sensitivity = build_sensitivity(
            closes, threshold_pct=cfg.stat_gate_threshold_pct,
        )
    rescues = rescue_stats(closes)
    methods = sorted({c.get("method") or "unknown" for c in closes})
    ci95 = ta.get("ci95_return_pct") or {}
    win_ci = (ta.get("win_rate_ci95") or {})
    generated = datetime.datetime.now().isoformat(timespec="seconds")
    caps = f"`MAX_ORDER_USD={MAX_ORDER_USD:.2f}`, `MAX_TOTAL_USD={MAX_TOTAL_USD:.2f}`"
    disclaimer = gate_definition(cfg)["tautology_disclaimer"]

    lines: list[str] = [
        "# Statistical Validation Report",
        "",
        f"- **Run id**: `{run_id}`",
        f"- **Store**: `{db_path}`",
        f"- **Artifacts**: `{artifact_dir}`",
        f"- **Generated**: `{generated}`",
        f"- **Harness verdict (sample)**: `{run_result.get('status')}` — {run_result.get('reason')}",
        "",
        "> Rehearsal numbers, not results. The shadow store has no signer; no "
        "money was spent and no position was opened.",
        "",
        "## Verdict",
        "",
        f"**{verdict}**",
        "",
        verdict_reason,
        "",
        "## Pessimistic sensitivity (GO must survive pessimism)",
        "",
        "- **Verdict**: "
        + str(sensitivity.get("verdict"))
        + " — " + str(sensitivity.get("verdict_reason") or ""),
        "- **Base**: `ci90_lower_pct` "
        + _fmt(sensitivity["base"].get("ci90_lower_pct"), 4) + "%"
        + f" (mean {_fmt(sensitivity['base'].get('mean_return_pct'), 4)}%, n={sensitivity['base'].get('n_closes')})",
        "- **Pessimistic**: completion recosted at `ask + 1 tick` "
        f"(tick {sensitivity['tick_per_share']}) plus `gas` "
        f"({sensitivity['gas']:.2f}), ci90_lower_pct "
        + _fmt(sensitivity["pessimistic"].get("ci90_lower_pct"), 4) + "%"
        + f" (mean {_fmt(sensitivity['pessimistic'].get('mean_return_pct'), 4)}%, n={sensitivity['pessimistic'].get('n_closes')})",
        f"- **Threshold**: `ci90_lower_pct >= {sensitivity['threshold_pct']} (inclusive)` in BOTH variants for GO",
        "- **Rebate income**: `None` in both variants (graduated spread markets pay $0.00 maker rewards).",
        "",
        "## Gate table",
        "",
        "| Gate | Metric | Value | Threshold | Pass |",
        "|------|--------|-------|-----------|------|",
    ]
    for r in gate_rows:
        passed = r["passed"]
        cell = "PASS" if passed is True else ("FAIL" if passed is False else "n/a")
        lines.append(
            f"| {r['gate']} | `{r['metric']}` | {_fmt(r['value'])} "
            f"| {r['threshold']} | {cell} |"
        )
    lines += [
        "",
        f"> {disclaimer}",
        "",
        "## Inclusive outcomes (every close method, every loss)",
        "",
        "- **n_closes**: "
        f"{ta.get('n_closes')} (distinct close rows, all methods: "
        f"{', '.join(methods) or 'none'})",
        "- **total_realized_pnl**: "
        f"${portfolio.get('realized_pnl'):.2f} "
        f"({'losses folded in' if (portfolio.get('realized_pnl') or 0) < 0 else 'net of all losses'})",
        "- **mean_return_pct**: " + _fmt(ta.get("mean_return_pct"), 4) + "%",
        "- **ci90_lower_pct** (one-sided 90%, the gate): "
        + _fmt(ta.get("ci90_lower_pct"), 4) + "%",
        "- **ci95_return_pct**: "
        f"({_fmt(ci95.get('lower'), 4)}%, {_fmt(ci95.get('upper'), 4)}%)",
        "- **win_rate**: " + _pct(ta.get("win_rate"))
        + f" (95% CI lower {_pct(win_ci.get('lower'))})",
        "- **expectancy_usd**: " + _fmt(ta.get("expectancy_usd"), 4),
        # pairs_under_1 arrives already percent-scaled (kpi returns
        # `100.0 * count / len`, e.g. 100.0 == 100%), so render it with
        # _fmt + "%" like the other percent-scaled metrics -- _pct would
        # double the scale (100.0 -> 10000.0%).
        "- **pairs_under_1**: "
        + ("n/a" if kpi.get("pairs_under_1") is None
           else _fmt(kpi.get("pairs_under_1"), 2) + "%")
        + f" (median pair cost {_fmt(kpi.get('median_pair_cost'), 4)})",
        "- **adverse_selection/share**: " + _fmt(kpi.get("adverse_selection"), 6)
        + f" ({kpi.get('markout_samples')} markout samples)",
        "- **max_drawdown**: "
        + _fmt(ta.get("max_drawdown_usd"), 2)
        + " USD / "
        + _fmt(ta.get("max_drawdown_pct"), 2)
        + "%",
        "- **max_naked_exposure_usd**: " + _fmt(ta.get("max_naked_exposure_usd"), 2),
        "",
        "## Rescue & failure modes (inside the gate, not beside it)",
        "",
        f"- Merges (`shadow_merge`): {rescues['merges']}",
        f"- Single-buy / naked exits: {rescues['exits']}",
        f"- Completion rate: {_pct(rescues['completion_rate'])}"
        f" (exit rate {_pct(rescues['exit_rate'])})",
        f"- Config calibration: `pairs_complete_gain_cents={cfg.pairs_complete_gain_cents}` "
        f"vs `pairs_exit_cost_cents={cfg.pairs_exit_cost_cents}`",
        "- The losses above are already inside `total_realized_pnl`; a bad exit "
        "path fails the primary gate with the rest of the PnL.",
        "",
        "### Realized PnL attribution by fill path (shadow estimate)",
        "- **Maker-merged**: "
        + f"${((kpi.get('pnl_by_fill_path') or {}).get('by_path') or {}).get('maker_merged', 0.0):.2f}"
        + f" ({_pct(((kpi.get('pnl_by_fill_path') or {}).get('pct') or {}).get('maker_merged'))})",
        "- **Taker-completed**: "
        + f"${((kpi.get('pnl_by_fill_path') or {}).get('by_path') or {}).get('taker_completed', 0.0):.2f}"
        + f" ({_pct(((kpi.get('pnl_by_fill_path') or {}).get('pct') or {}).get('taker_completed'))})",
        "- **Single-buy exit**: "
        + f"${((kpi.get('pnl_by_fill_path') or {}).get('by_path') or {}).get('single_buy_exit', 0.0):.2f}"
        + f" ({_pct(((kpi.get('pnl_by_fill_path') or {}).get('pct') or {}).get('single_buy_exit'))})",
        "- *Shadow-only estimate: taker completions identified via multi-order pair IDs; live merges default to maker_merged.*",
        "",
        "## Mechanics",
        "",
        f"- **order_latency_ms**: median {_fmt((kpi.get('order_latency_ms') or {}).get('median'), 1)}, "
        f"max {_fmt((kpi.get('order_latency_ms') or {}).get('max'), 1)}",
        f"- **reconcile_lag_ms**: median {_fmt((kpi.get('reconcile_lag_ms') or {}).get('median'), 1)}, "
        f"max {_fmt((kpi.get('reconcile_lag_ms') or {}).get('max'), 1)}",
        f"- **venue_rejects**: {(kpi.get('venue_rejects') or {}).get('total', 0)} "
        f"(by code: {(kpi.get('venue_rejects') or {}).get('by_code', {})})",
        f"- **three_way_divergences**: {(kpi.get('three_way_divergences') or {}).get('total', 0)}",
        "- **skipped stages**: `reconcile` (shadow store has no venue positions "
        "to reconcile against)",
        "",
        "## Cost of being wrong",
        "",
        COST_OF_BEING_WRONG,
        "",
        f"Caps at risk if GO is acted on: {caps}.",
        "",
        "## Limitations",
        "",
        "- Fills are tape-confirmed models, not venue executions "
        "(`shadow_fills.credit_fills` only); no fill is ever credited without "
        "trade-tape volume at the order's own price.",
        "- Completion fills are priced at the ask — the optimistic end of taker "
        "(upper bound, noted in the design doc). The pessimistic sensitivity "
        "column re-prices that completion one tick worse; "
        "GO requires the base AND the pessimistic `ci90_lower_pct` to pass.",
        "- Rebate income is `None` on graduated spread markets (`rebate_est`); "
        "the report never invents rebate income.",
        (
            "- The pessimistic sensitivity charges no conversion gas: a merge is "
            "submitted through Polymarket's relayer, which sends it from its own "
            "address and pays for it. Receipt-measured gas on a close "
            "(`closes.gas`, via `core_brain.gas.close_gas_usd`) is a separate "
            "figure and may be non-zero on a live transaction our own funder "
            "sent. The economic gate is positive expectancy per close."
        ),
        "- Immature markouts read as `insufficient_sample` (INCONCLUSIVE), never "
        "as zero drift.",
        "- Rehearsal PnL must never be presented as live PnL.",
        "",
    ]
    if target_closes is not None:
        lines.append(
            f"- Target sample: `{target_closes}` closes"
            f" (met: {ta.get('n_closes')})"
        )
    if min_markouts is not None:
        lines.append(
            f"- Markout floor: `{min_markouts}` matured samples"
            f" (met: {run_result.get('matured_markouts')})"
        )
    lines.append("")
    return "\n".join(lines)


def rows_to_csv(rows: list[dict[str, Any]]) -> str:
    """Serialize a list of row dicts to CSV text with a stable header order."""
    if not rows:
        return ""
    fieldnames: list[str] = []
    for r in rows:
        for key in r:
            if key not in fieldnames:
                fieldnames.append(key)
    buf = io.StringIO()
    # lineterminator="\n": csv's default "\r\n" would be translated AGAIN by
    # Path.write_text on Windows (text mode), producing "\r\r\n" in the file.
    writer = csv.DictWriter(
        buf, fieldnames=fieldnames, extrasaction="ignore",
        lineterminator="\n",
    )
    writer.writeheader()
    for r in rows:
        writer.writerow({k: ("" if v is None else v) for k, v in r.items()})
    return buf.getvalue()


# Public helper implementations remain here for compatibility. The promoted
# mode-aware reporter imports and delegates to these helpers.


def write_artifacts(
    artifact_dir: Path | str,
    *,
    db_path: Path | str,
    run_id: str,
    cfg: MakerConfig,
    kpi: dict[str, Any],
    verdict: str,
    verdict_reason: str,
    gate_rows: list[dict[str, Any]],
    run_result: dict[str, Any],
    target_closes: Optional[int],
    min_markouts: Optional[int],
) -> dict[str, Path]:
    """Write the full artifact bundle and return {name: path} for every file."""
    out = Path(artifact_dir)
    out.mkdir(parents=True, exist_ok=True)

    reg = OrderRegistry(db_path)
    closes = [c for c in reg.get_all_closes() if c.get("run_id") == run_id]
    fills = [f for f in reg.get_all_fills() if f.get("run_id") == run_id]
    quotes = [q for q in reg.get_all_quotes() if q.get("run_id") == run_id]

    def _write(name: str, text: str) -> Path:
        p = out / name
        p.write_text(text, encoding="utf-8")
        return p

    methods = sorted({c.get("method") or "unknown" for c in closes})

    # Issue #55: the side-by-side sensitivity column. GO must survive pessimism,
    # so the verdict here is GO only when BOTH the base and the pessimistic
    # `ci90_lower_pct` sit at or above the threshold. `rebate_est` stays None in
    # both variants -- the report never invents rebate income.
    sensitivity = build_sensitivity(
        closes, threshold_pct=cfg.stat_gate_threshold_pct,
    )

    # Issue #55: GO must survive pessimism, so the headline verdict is the thing
    # the sensitivity gate can veto. When the primary verdict is GO but the
    # sensitivity gate is not, a GO on the cover of the same report would say the
    # opposite of the ticket -- downgrade it to the sensitivity verdict. A
    # primary NO-GO or INCONCLUSIVE already fails, so it is left untouched (it
    # never upgrades on a weaker gate).
    if verdict == "GO" and sensitivity["verdict"] != "GO":
        effective_verdict = sensitivity["verdict"]
        effective_reason = sensitivity["verdict_reason"]
    else:
        effective_verdict = verdict
        effective_reason = verdict_reason

    report = dict(kpi)
    report["sensitivity"] = sensitivity
    report["stat_validation"] = {
        "run_id": run_id,
        "verdict": effective_verdict,
        "verdict_reason": effective_reason,
        "gates": gate_rows,
        "primary_gate": f"ci90_lower_pct > {cfg.stat_gate_threshold_pct} inclusive",
        "threshold_pct": cfg.stat_gate_threshold_pct,
        "bankroll_fraction": cfg.stat_gate_bankroll_fraction,
        "tautology_disclaimer": gate_definition(cfg)["tautology_disclaimer"],
        "caps_at_risk_usd": {
            "MAX_ORDER_USD": MAX_ORDER_USD,
            "MAX_TOTAL_USD": MAX_TOTAL_USD,
        },
        "cost_of_being_wrong": COST_OF_BEING_WRONG,
        "rescue": rescue_stats(closes),
        "pnl_by_fill_path": kpi.get("pnl_by_fill_path"),
        "methods_present": methods if closes else [],
        "target_closes": target_closes,
        "min_markouts": min_markouts,
        "run_result": run_result,
    }

    paths = {
        "report.json": _write("report.json", json.dumps(report, indent=2, default=str)),
        "report.md": _write(
            "report.md",
            render_report_md(
                run_id=run_id,
                db_path=db_path,
                artifact_dir=out,
                kpi=kpi,
                cfg=cfg,
                gate_rows=gate_rows,
                verdict=effective_verdict,
                verdict_reason=effective_reason,
                run_result=run_result,
                closes=closes,
                target_closes=target_closes,
                min_markouts=min_markouts,
                sensitivity=sensitivity,
            ),
        ),
        "closes.csv": _write("closes.csv", rows_to_csv(closes)),
        "fills.csv": _write("fills.csv", rows_to_csv(fills)),
        "quotes.csv": _write("quotes.csv", rows_to_csv(quotes)),
    }
    log.info(
        "ARTIFACTS written to %s: report.json report.md closes.csv fills.csv "
        "quotes.csv (%d closes, %d fills, %d quotes)",
        out, len(closes), len(fills), len(quotes),
    )
    return paths
