"""Targeted tests for Stitch Terminal Closed Trades table layout and YES/NO pills."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_APP_JS = _ROOT / "dashboard" / "static" / "app.js"

requires_node = pytest.mark.skipif(shutil.which("node") is None,
                                   reason="node is not installed on this host")


def _eval_js(code: str) -> str:
    runner = f"""
const fs = require('fs');
const source = fs.readFileSync({json.dumps(str(_APP_JS))}, 'utf8');
const mod = {{ exports: {{}} }};
const noop = () => {{}};
const stub = () => ({{
    textContent: '', innerHTML: '', style: {{}}, dataset: {{}},
    classList: {{ add: noop, remove: noop, toggle: noop, contains: () => true }},
    addEventListener: noop, querySelector: () => null, querySelectorAll: () => [],
    setAttribute: noop, getAttribute: () => null
}});
const doc = {{
    getElementById: stub, querySelector: () => null, querySelectorAll: () => [],
    addEventListener: noop, body: {{ classList: {{ add: noop, remove: noop, toggle: noop, contains: () => true }} }}
}};
const win = {{ addEventListener: noop, location: {{ href: '' }} }};
const storage = {{ getItem: () => null, setItem: noop, removeItem: noop }};
new Function('module', 'exports', 'document', 'window', 'localStorage', 'EventSource', source)(
    mod, mod.exports, doc, win, storage, function() {{ return {{ addEventListener: noop }}; }}
);
const app = mod.exports;
{code}
"""
    out = subprocess.run([shutil.which("node"), "-e", runner],
                         capture_output=True, text=True, check=True, encoding="utf-8")
    return out.stdout.strip()


@requires_node
def test_stitch_closed_trades_pair_renders_single_row_with_both_legs_and_single_pnl():
    code = """
    const settlements = [{
        method: 'merge',
        shares: 10,
        up_cost_removed: 4.70,
        dn_cost_removed: 4.60,
        cost_basis: 9.30,
        proceeds: 10.00,
        realized_pnl: 0.70,
        ts: 1700000000
    }];
    const html = app.renderStitchClosedLedgerRows('0x123', {}, settlements, [], Date.now());
    const kpi = {
        by_market: {
            '0x123': {
                condition_id: '0x123',
                title: 'Fed Rate Cut in June',
                category: 'Finance',
                settlements: settlements,
                realized_pnl: 0.70,
                total_cost: 9.30,
                total_sh: 10
            }
        }
    };
    const parentTableHtml = app.stitchClosedTradesRows(kpi, { orders: [] }, null);
    console.log(JSON.stringify({
        html,
        parentTableHtml,
        hasYes: html.includes('<span class="stitch-leg-pill yes">YES</span>'),
        hasNo: html.includes('<span class="stitch-leg-pill no">NO</span>'),
        rowCount: (html.match(/<tr /g) || []).length,
        pnlCount: (html.match(/class="stitch-pnl-cell"/g) || []).length
    }));
    """
    res = json.loads(_eval_js(code))
    assert res["hasYes"] is True
    assert res["hasNo"] is True
    assert res["rowCount"] == 1
    assert res["pnlCount"] == 1
    assert "+$0.70" in res["html"]
    assert "YES" in res["html"]
    assert "NO" in res["html"]
    # EXEC TYPE replaces the old STATUS badge: same outcome, own column, and it
    # is a pill now (#831). "Merge" alone also matches the old "Pair Merge", so
    # the old wording is asserted gone rather than merely shadowed.
    assert 'stitch-exec-type is-merge">Merge</span>' in res["html"]
    assert "Pair Merge" not in res["html"]
    assert "stitch-exec-type" in res["html"]
    assert "Fed Rate Cut in June" in res["parentTableHtml"]
    assert "+$0.70" in res["parentTableHtml"]
    # The outcome column counts money now, not the method that closed it (#831).
    assert "1 Win" in res["parentTableHtml"]
    assert "Merged" not in res["parentTableHtml"]


@requires_node
def test_stitch_closed_trades_solos_render_each_in_own_row_with_own_pnl():
    code = """
    const settlements = [
        {
            method: 'stop_loss_exit',
            reason: 'lifecycle_hard_stop',
            shares: 5,
            up_cost_removed: 2.35,
            dn_cost_removed: 0,
            cost_basis: 2.35,
            proceeds: 1.00,
            realized_pnl: -1.35,
            ts: 1700000000
        },
        {
            method: 'aged_out_exit',
            reason: 'grace_expired',
            shares: 5,
            up_cost_removed: 0,
            dn_cost_removed: 2.30,
            cost_basis: 2.30,
            proceeds: 0.50,
            realized_pnl: -1.80,
            ts: 1700000010
        }
    ];
    const html = app.renderStitchClosedLedgerRows('0x123', {}, settlements, [], Date.now());
    console.log(JSON.stringify({
        html,
        hasYes: html.includes('<span class="stitch-leg-pill yes">YES</span>'),
        hasNo: html.includes('<span class="stitch-leg-pill no">NO</span>'),
        rowCount: (html.match(/<tr /g) || []).length,
        pnlCount: (html.match(/class="stitch-pnl-cell"/g) || []).length
    }));
    """
    res = json.loads(_eval_js(code))
    assert res["hasYes"] is True
    assert res["hasNo"] is True
    # Two separate rows because each is a solo!
    assert res["rowCount"] == 2
    # Two separate PnL cells!
    assert res["pnlCount"] == 2
    # EXEC TYPE replaces the old STATUS badge: same outcomes, own column.
    assert "Stop Loss" in res["html"]
    assert "Grace Expired" in res["html"]


@requires_node
def test_stitch_closed_trades_total_cost_uses_settlement_basis_not_residual_dust():
    """#579: a closed market's Total Cost is the basis of what closed, never the
    residual open-inventory dust KPI publishes in the same field."""
    code = """
    const settlements = [
        {
            method: 'shadow_merge',
            shares: 7,
            up_cost_removed: 2.87,
            dn_cost_removed: 4.06,
            cost_basis: 6.93,
            proceeds: 7.00,
            realized_pnl: 0.07,
            ts: 1700000000
        },
        {
            method: 'shadow_merge',
            shares: 6,
            up_cost_removed: 2.10,
            dn_cost_removed: 3.42,
            cost_basis: 5.52,
            proceeds: 6.00,
            realized_pnl: 0.48,
            ts: 1700000010
        }
    ];
    // The real-world shape from the #569 store: both pairs fully closed, so the
    // only thing left in KPI's residual field is dust.
    const kpi = {
        by_market: {
            '0x123': {
                condition_id: '0x123',
                title: 'Feyenoord vs Como',
                category: 'Sports',
                settlements: settlements,
                realized_pnl: 0.55,
                total_cost: 0.35,
                total_sh: 0
            }
        }
    };
    const html = app.stitchClosedTradesRows(kpi, { orders: [] }, null);
    console.log(JSON.stringify({
        html,
        hasDustCost: html.includes('$0.35'),
        hasTrueCost: html.includes('$12.45'),
        hasInflatedPct: html.includes('157.14%'),
        hasTruePct: html.includes('4.42%')
    }));
    """
    res = json.loads(_eval_js(code))
    assert res["hasTrueCost"] is True, "closed row must show the settlement basis total"
    assert res["hasTruePct"] is True, "PnL pct must divide by the settlement basis total"
    assert res["hasDustCost"] is False, "residual dust must not render as Total Cost"
    assert res["hasInflatedPct"] is False, "dust denominator must not inflate the pct"


@requires_node
def test_stitch_closed_trades_total_cost_sort_uses_settlement_basis():
    """#579 follow-up: the Total Cost column sort ranks by the settlement basis,
    not the residual dust — a $12.45-basis market must outrank a $5.00 one even
    when it holds only $0.35 of residual."""
    code = """
    const big = {
        condition_id: '0xbig', title: 'Big Basis Market', category: 'Sports',
        settlements: [{ method: 'shadow_merge', shares: 7, cost_basis: 12.45,
            proceeds: 13.00, realized_pnl: 0.55, ts: 1700000000 }],
        realized_pnl: 0.55, total_cost: 0.35, total_sh: 0
    };
    const small = {
        condition_id: '0xsmall', title: 'Small Basis Market', category: 'Sports',
        settlements: [{ method: 'shadow_merge', shares: 5, cost_basis: 5.00,
            proceeds: 5.20, realized_pnl: 0.20, ts: 1700000000 }],
        realized_pnl: 0.20, total_cost: 0.50, total_sh: 0
    };
    const kpi = { by_market: { '0xbig': big, '0xsmall': small } };
    const desc = app.stitchClosedTradesRows(kpi, { orders: [] }, { col: 3, dir: 'desc' });
    const asc = app.stitchClosedTradesRows(kpi, { orders: [] }, { col: 3, dir: 'asc' });
    console.log(JSON.stringify({
        descFirst: desc.indexOf('0xbig') < desc.indexOf('0xsmall'),
        ascFirst: asc.indexOf('0xsmall') < asc.indexOf('0xbig')
    }));
    """
    res = json.loads(_eval_js(code))
    assert res["descFirst"] is True, "desc sort must put the larger basis first"
    assert res["ascFirst"] is True, "asc sort must put the smaller basis first"


@requires_node
def test_stitch_open_orders_and_positions_headers_and_pills():
    code = """
    const ordersHead = app.stitchOpenOrdersHeadHtml(null);
    const posHead = app.stitchPositionsHeadHtml(null);
    const mktsHead = app.stitchActiveMarketsHeadHtml(null);
    console.log(JSON.stringify({
        ordersHead,
        posHead,
        mktsHead
    }));
    """
    res = json.loads(_eval_js(code))
    assert "Dual-Leg Quotes (YES / NO)" in res["ordersHead"]
    # Positions uses the same shared dual-leg HELD INVENTORY cell as Orders
    # (single template); the split YES HELD / NO HELD columns are gone.
    assert "HELD INVENTORY" in res["posHead"]
    assert "LAST UPDATED" in res["posHead"]
    assert "YES PRICE" in res["mktsHead"]
    assert "NO PRICE" in res["mktsHead"]


@requires_node
def test_stitch_closed_trades_empty_and_ledger_row_span_all_seven_columns():
    code = """
    const kpi = { by_market: { '0x123': {
        condition_id: '0x123', title: 'M', category: 'C',
        settlements: [{ method: 'merge', shares: 5, cost_basis: 5.0,
            proceeds: 5.5, realized_pnl: 0.5, ts: 1700000000 }],
        realized_pnl: 0.5, total_cost: 0, total_sh: 0 } } };
    const empty = app.stitchClosedTradesRows({ by_market: {} }, { orders: [] }, null);
    app.expandedMarkets.add('0x123');
    const full = app.stitchClosedTradesRows(kpi, { orders: [] }, null);
    app.expandedMarkets.delete('0x123');
    console.log(JSON.stringify({ empty, full }));
    """
    res = json.loads(_eval_js(code))
    assert 'colspan="7"' in res["empty"]
    assert "No closed trades yet" in res["empty"]
    expand = res["full"].split("stitch-expand-row")[1].split("</td>")[0]
    assert 'colspan="7"' in expand, "the ledger detail row must span the new column count"
